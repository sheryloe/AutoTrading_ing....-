from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import load_settings
from src.daily_reports import write_daily_pnl_report
from src.supabase_sync import SupabaseSyncClient


MODEL_IDS = ("A", "B", "C", "D")
UTC = timezone.utc
KST = timezone(timedelta(hours=9))
MODEL_FIXED_SEED_USD = 10_000.0


def _parse_day(value: str) -> date:
    return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()


def _try_parse_day(value: Any) -> date | None:
    try:
        return _parse_day(str(value or "").strip())
    except Exception:
        return None


def _day_key(day_value: date) -> str:
    return day_value.strftime("%Y-%m-%d")


def _day_start_ts(day_value: date) -> int:
    return int(datetime(day_value.year, day_value.month, day_value.day, tzinfo=KST).timestamp())


def _day_end_ts(day_value: date) -> int:
    return int((datetime(day_value.year, day_value.month, day_value.day, tzinfo=KST) + timedelta(days=1)).timestamp()) - 1


def _iter_days(start_day: date, end_day: date) -> list[date]:
    out: list[date] = []
    current = start_day
    while current <= end_day:
        out.append(current)
        current += timedelta(days=1)
    return out


def _normalize_symbol(raw: Any) -> str:
    symbol = str(raw or "").upper().strip()
    if not symbol:
        return ""
    if not symbol.endswith("USDT"):
        symbol = f"{symbol}USDT"
    return symbol


def _load_env_files() -> None:
    candidates = [ROOT / "env" / ".env", ROOT / ".env"]
    for path in candidates:
        if not path.exists():
            continue
        payload = dotenv_values(str(path))
        for key, value in payload.items():
            if key and value is not None and not os.getenv(key):
                os.environ[str(key)] = str(value)


def _build_supabase_client() -> SupabaseSyncClient:
    url = str(os.getenv("SUPABASE_URL") or "").strip()
    key = str(os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_SECRET_KEY") or "").strip()
    return SupabaseSyncClient(url=url, secret_key=key, enabled=bool(url and key), timeout_seconds=20)


def _load_state_payload(state_path: Path | None, client: SupabaseSyncClient) -> dict[str, Any]:
    if state_path and state_path.exists():
        return json.loads(state_path.read_text(encoding="utf-8"))
    if client.enabled:
        result = client.fetch_blob("engine_state")
        payload = result.get("payload") if bool(result.get("ok")) else None
        if isinstance(payload, dict):
            return payload
    raise RuntimeError("engine_state_not_found")


def _persist_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    serialized = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    tmp_path.write_text(serialized, encoding="utf-8")
    tmp_path.replace(path)


def _daily_row_key(day_value: Any, model_id: Any) -> tuple[str, str]:
    return (str(day_value or "").strip(), str(model_id or "").strip().upper())


def _daily_total_pnl(row: dict[str, Any]) -> float:
    return _safe_float(
        row.get("bybit_total_pnl_usd")
        if row.get("bybit_total_pnl_usd") not in {None, ""}
        else row.get("total_pnl_usd"),
        0.0,
    )


def _is_flat_zero_row(row: dict[str, Any]) -> bool:
    total = _safe_float(row.get("bybit_total_pnl_usd"), _safe_float(row.get("total_pnl_usd"), 0.0))
    realized = _safe_float(row.get("bybit_realized_pnl_usd"), _safe_float(row.get("realized_pnl_usd"), 0.0))
    unrealized = _safe_float(row.get("bybit_unrealized_pnl_usd"), _safe_float(row.get("unrealized_pnl_usd"), 0.0))
    open_positions = _safe_int(row.get("bybit_open_positions"), 0)
    closed_trades = _safe_int(row.get("bybit_closed_trades"), _safe_int(row.get("closed_trades"), 0))
    return bool(
        abs(total) <= 1e-9
        and abs(realized) <= 1e-9
        and abs(unrealized) <= 1e-9
        and open_positions == 0
        and closed_trades == 0
    )


def _build_state_daily_index(state_payload: dict[str, Any], *, start_day: date, end_day: date) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for row in list(state_payload.get("daily_pnl") or []):
        if not isinstance(row, dict):
            continue
        day_text = str(row.get("date") or row.get("day") or "").strip()
        model_id = str(row.get("model_id") or "").strip().upper()
        day_parsed = _try_parse_day(day_text)
        if day_parsed is None or not (start_day <= day_parsed <= end_day):
            continue
        if model_id not in MODEL_IDS:
            continue
        key = _daily_row_key(day_text, model_id)
        out[key] = dict(row)
    return out


def _build_docs_daily_index(docs_dir: Path, *, start_day: date, end_day: date) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    if not docs_dir.exists():
        return out
    for day_value in _iter_days(start_day, end_day):
        day_key = _day_key(day_value)
        path = docs_dir / f"{day_key}.json"
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for row in list(payload.get("models") or []):
            if not isinstance(row, dict):
                continue
            model_id = str(row.get("model_id") or "").strip().upper()
            if model_id not in MODEL_IDS:
                continue
            normalized = dict(row)
            normalized["date"] = day_key
            normalized["model_id"] = model_id
            out[_daily_row_key(day_key, model_id)] = normalized
    return out


def _build_supabase_daily_index(
    client: SupabaseSyncClient,
    *,
    start_day: date,
    end_day: date,
) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    if not client.enabled:
        return out
    result = client.fetch_rows(
        "daily_model_pnl",
        params={
            "select": "day,model_id,equity_usd,total_pnl_usd,realized_pnl_usd,unrealized_pnl_usd,win_rate,closed_trades,source_json,bybit_rebuild_restart_variant_id,bybit_rebuild_restart_note_ko,bybit_rebuild_restart_seed_usd,bybit_rebuild_restart_ts",
            "market": "eq.crypto",
            "day": f"gte.{_day_key(start_day)}",
            "order": "day.asc,model_id.asc",
            "limit": "800",
        },
    )
    if not bool(result.get("ok")):
        result = client.fetch_rows(
            "daily_model_pnl",
            params={
                "select": "day,model_id,equity_usd,total_pnl_usd,realized_pnl_usd,unrealized_pnl_usd,win_rate,closed_trades,source_json",
                "market": "eq.crypto",
                "day": f"gte.{_day_key(start_day)}",
                "order": "day.asc,model_id.asc",
                "limit": "800",
            },
        )
        if not bool(result.get("ok")):
            return out
    for row in list(result.get("rows") or []):
        if not isinstance(row, dict):
            continue
        source_json = row.get("source_json")
        if not isinstance(source_json, dict):
            source_json = {}
        day_text = str(row.get("day") or "").strip()
        day_parsed = _try_parse_day(day_text)
        if day_parsed is None or day_parsed > end_day:
            continue
        model_id = str(row.get("model_id") or "").strip().upper()
        if model_id not in MODEL_IDS:
            continue
        cycle_total = _safe_float(
            (row.get("source_json") or {}).get("cycle_total_pnl_usd")
            if isinstance(row.get("source_json"), dict)
            else None,
            _safe_float(row.get("total_pnl_usd"), 0.0),
        )
        anchor_total = _safe_float(
            (row.get("source_json") or {}).get("total_pnl_anchor_usd")
            if isinstance(row.get("source_json"), dict)
            else None,
            0.0,
        )
        display_total = _safe_float(row.get("total_pnl_usd"), cycle_total + anchor_total)
        out[_daily_row_key(day_text, model_id)] = {
            "date": day_text,
            "model_id": model_id,
            "meme_equity_usd": 0.0,
            "bybit_equity_usd": _safe_float(row.get("equity_usd"), MODEL_FIXED_SEED_USD + display_total),
            "meme_total_pnl_usd": 0.0,
            "bybit_total_pnl_usd": display_total,
            "bybit_cycle_total_pnl_usd": cycle_total,
            "bybit_total_pnl_anchor_usd": anchor_total,
            "meme_realized_pnl_usd": 0.0,
            "bybit_realized_pnl_usd": _safe_float(row.get("realized_pnl_usd"), 0.0),
            "meme_unrealized_pnl_usd": 0.0,
            "bybit_unrealized_pnl_usd": _safe_float(row.get("unrealized_pnl_usd"), 0.0),
            "meme_win_rate": 0.0,
            "bybit_win_rate": _safe_float(row.get("win_rate"), 0.0),
            "meme_closed_trades": 0,
            "bybit_closed_trades": _safe_int(row.get("closed_trades"), 0),
            "meme_open_positions": 0,
            "bybit_open_positions": 0,
            "total_equity_usd": _safe_float(row.get("equity_usd"), MODEL_FIXED_SEED_USD + display_total),
            "total_pnl_usd": display_total,
            "realized_pnl_usd": _safe_float(row.get("realized_pnl_usd"), 0.0),
            "unrealized_pnl_usd": _safe_float(row.get("unrealized_pnl_usd"), 0.0),
            "win_rate": _safe_float(row.get("win_rate"), 0.0),
            "closed_trades": _safe_int(row.get("closed_trades"), 0),
            "bybit_quote_status": str(source_json.get("quote_status") or "fresh"),
            "bybit_quote_stale": False,
            "bybit_quote_as_of_ts": 0,
            "bybit_quote_age_seconds": 0,
            "bybit_quote_stale_after_seconds": 0,
            "bybit_quote_symbols": [],
            "bybit_quote_stale_symbols": [],
            "bybit_quote_guard_symbols": [],
            "bybit_quote_realtime_sources": [],
            "bybit_quote_fallback_sources": [],
            "bybit_quote_sync_attempted_symbols": [],
            "bybit_quote_sync_synced_symbols": [],
            "bybit_quote_sync_missing_symbols": [],
            "bybit_quote_sync_provider_summary": {},
            "bybit_quote_sync_reason": "supabase_snapshot",
            "bybit_quote_sync_at_ts": 0,
            "rebuild_source": str(((row.get("source_json") or {}) if isinstance(row.get("source_json"), dict) else {}).get("rebuild_source") or ""),
            "bybit_rebuild_restart_note_ko": str(
                row.get("bybit_rebuild_restart_note_ko")
                or source_json.get("rebuild_restart_note_ko")
                or ""
            ),
            "bybit_rebuild_restart_variant_id": str(
                row.get("bybit_rebuild_restart_variant_id")
                or source_json.get("rebuild_restart_variant_id")
                or ""
            ),
            "bybit_rebuild_restart_seed_usd": _safe_float(
                row.get("bybit_rebuild_restart_seed_usd") or source_json.get("rebuild_restart_seed_usd"),
                0.0,
            ),
            "bybit_rebuild_restart_ts": _safe_int(
                row.get("bybit_rebuild_restart_ts") or source_json.get("rebuild_restart_ts"),
                0,
            ),
            "eod_price_sources": {},
            "replay_window": {"from": _day_key(start_day), "to": _day_key(end_day)},
        }
    return out


def _normalize_existing_row(row: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(row or {})
    normalized["date"] = str(normalized.get("date") or normalized.get("day") or "").strip()
    normalized["model_id"] = str(normalized.get("model_id") or "").strip().upper()
    cycle = _safe_float(
        normalized.get("bybit_cycle_total_pnl_usd"),
        _safe_float(normalized.get("bybit_total_pnl_usd"), _safe_float(normalized.get("total_pnl_usd"), 0.0)),
    )
    anchor = _safe_float(normalized.get("bybit_total_pnl_anchor_usd"), 0.0)
    if "bybit_cycle_total_pnl_usd" not in normalized:
        normalized["bybit_cycle_total_pnl_usd"] = float(cycle)
    if "bybit_total_pnl_anchor_usd" not in normalized:
        normalized["bybit_total_pnl_anchor_usd"] = float(anchor)
    return normalized


def _merge_rebuilt_with_existing(
    rebuilt_rows: list[dict[str, Any]],
    *,
    existing_index: dict[tuple[str, str], dict[str, Any]],
    start_day: date,
    end_day: date,
) -> tuple[list[dict[str, Any]], int, int]:
    merged_map: dict[tuple[str, str], dict[str, Any]] = {}
    for key, row in dict(existing_index or {}).items():
        current = _normalize_existing_row(dict(row or {}))
        day_text = str(current.get("date") or "").strip()
        model_id = str(current.get("model_id") or "").strip().upper()
        parsed_day = _try_parse_day(day_text)
        if parsed_day is None or not (start_day <= parsed_day <= end_day):
            continue
        if model_id not in MODEL_IDS:
            continue
        merged_map[_daily_row_key(day_text, model_id)] = current
    existing_rows_in_window = len(merged_map)
    preserved_nonzero_rows = 0

    for row in list(rebuilt_rows or []):
        current = _normalize_existing_row(dict(row or {}))
        day_text = str(current.get("date") or "").strip()
        model_id = str(current.get("model_id") or "").strip().upper()
        parsed_day = _try_parse_day(day_text)
        if parsed_day is None or not (start_day <= parsed_day <= end_day):
            continue
        if model_id not in MODEL_IDS:
            continue
        key = _daily_row_key(day_text, model_id)
        existing = dict(merged_map.get(key) or {})
        if existing and _is_flat_zero_row(current) and abs(_daily_total_pnl(existing)) > 1e-9:
            if model_id in {"A", "B", "C"} and _row_has_restart_marker(existing):
                for marker_key in (
                    "rebuild_source",
                    "bybit_rebuild_restart_variant_id",
                    "bybit_rebuild_restart_note_ko",
                    "bybit_rebuild_restart_seed_usd",
                    "bybit_rebuild_restart_ts",
                ):
                    marker_value = existing.get(marker_key)
                    if marker_value is not None and marker_value != "" and marker_value != [] and marker_value != {}:
                        current[marker_key] = marker_value
            else:
                preserved_nonzero_rows += 1
                continue
        merged_map[key] = current

    out = sorted(merged_map.values(), key=lambda r: (str(r.get("date") or ""), str(r.get("model_id") or "")))
    return out, int(existing_rows_in_window), int(preserved_nonzero_rows)


def _row_has_restart_marker(row: dict[str, Any]) -> bool:
    source_json = dict(row.get("source_json") or {})
    if not isinstance(source_json, dict):
        source_json = {}
    variant_id = str(
        row.get("bybit_rebuild_restart_variant_id")
        or row.get("rebuild_restart_variant_id")
        or source_json.get("rebuild_restart_variant_id")
        or ""
    ).strip()
    if variant_id:
        return True
    rebuild_source = str(row.get("rebuild_source") or source_json.get("rebuild_source") or "").strip().lower()
    return rebuild_source == "drawdown_50pct_rebuild_restart"


def _row_cycle_total_pnl(row: dict[str, Any]) -> float:
    return _safe_float(
        row.get("bybit_cycle_total_pnl_usd"),
        _safe_float(row.get("bybit_total_pnl_usd"), _safe_float(row.get("total_pnl_usd"), 0.0)),
    )


def _apply_model_anchor_policy(rows: list[dict[str, Any]], *, seed_usd: float) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {model_id: [] for model_id in MODEL_IDS}
    for row in list(rows or []):
        model_id = str((row or {}).get("model_id") or "").strip().upper()
        if model_id not in grouped:
            continue
        grouped[model_id].append(dict(row or {}))

    out: list[dict[str, Any]] = []
    for model_id in MODEL_IDS:
        model_rows = sorted(
            grouped.get(model_id) or [],
            key=lambda r: (str(r.get("date") or ""), str(r.get("model_id") or "")),
        )
        anchor = 0.0
        prev_display_total: float | None = None
        for row in model_rows:
            cycle_total = float(_row_cycle_total_pnl(row))
            has_restart = _row_has_restart_marker(row)
            if model_id == "D":
                if has_restart:
                    anchor = 0.0
            else:
                if has_restart and prev_display_total is not None:
                    anchor = float(prev_display_total - cycle_total)
            display_total = float(cycle_total + anchor)
            meme_total = _safe_float(row.get("meme_total_pnl_usd"), 0.0)
            meme_equity = _safe_float(row.get("meme_equity_usd"), 0.0)
            bybit_equity = float(seed_usd + display_total)

            row["bybit_cycle_total_pnl_usd"] = round(float(cycle_total), 6)
            row["bybit_total_pnl_anchor_usd"] = round(float(anchor), 6)
            row["bybit_total_pnl_usd"] = round(float(display_total), 6)
            row["total_pnl_usd"] = round(float(meme_total + display_total), 6)
            row["bybit_equity_usd"] = round(float(bybit_equity), 6)
            row["total_equity_usd"] = round(float(meme_equity + bybit_equity), 6)

            prev_display_total = float(display_total)
            out.append(row)

    out.sort(key=lambda r: (str(r.get("date") or ""), str(r.get("model_id") or "")))
    return out


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _mark_crypto_position(pos: dict[str, Any], current_price: float) -> dict[str, float]:
    qty = float(pos.get("qty") or 0.0)
    avg = float(pos.get("avg_price_usd") or 0.0)
    side = "short" if str(pos.get("side") or "").strip().lower() == "short" else "long"
    leverage = max(1.0, float(pos.get("leverage") or 1.0))
    margin = float(pos.get("margin_usd") or 0.0)
    if margin <= 0.0 and avg > 0.0 and qty > 0.0:
        margin = (avg * qty) / leverage
    mark = float(current_price or 0.0)
    if mark <= 0.0:
        mark = float(pos.get("last_mark_price_usd") or 0.0)
    if mark <= 0.0:
        mark = avg
    exposure = max(0.0, mark * qty)
    if avg > 0.0 and qty > 0.0:
        pnl_raw = (avg - mark) * qty if side == "short" else (mark - avg) * qty
        price_pnl_pct = ((avg - mark) / avg) if side == "short" else ((mark - avg) / avg)
    else:
        pnl_raw = 0.0
        price_pnl_pct = 0.0
    pnl_floor = -max(0.0, margin)
    pnl = max(pnl_floor, pnl_raw)
    position_equity = max(0.0, margin + pnl)
    roe_pct = 0.0 if margin <= 0.0 else (pnl / margin)
    return {
        "qty": qty,
        "avg_price_usd": avg,
        "mark_price_usd": mark,
        "leverage": leverage,
        "margin_usd": max(0.0, margin),
        "exposure_usd": exposure,
        "pnl_usd": pnl,
        "position_equity_usd": position_equity,
        "price_pnl_pct": price_pnl_pct,
        "roe_pct": roe_pct,
    }


class DailyPriceClient:
    def __init__(self, timeout_seconds: int = 15) -> None:
        self.session = requests.Session()
        self.timeout_seconds = max(5, int(timeout_seconds))
        self.errors: dict[str, list[dict[str, str]]] = {}

    def _record_error(self, symbol: str, source: str, exc: Exception) -> None:
        sym = _normalize_symbol(symbol)
        if not sym:
            return
        rows = self.errors.setdefault(sym, [])
        rows.append(
            {
                "source": str(source or "").strip() or "unknown",
                "error": str(exc or "").strip() or exc.__class__.__name__,
            }
        )

    def _fetch_binance_daily(self, symbol: str, start_day: date, end_day: date) -> dict[str, dict[str, Any]]:
        sym = _normalize_symbol(symbol)
        if not sym:
            return {}
        out: dict[str, dict[str, Any]] = {}
        current_start = _day_start_ts(start_day) * 1000
        final_end = (_day_end_ts(end_day) * 1000)
        while current_start <= final_end:
            try:
                res = self.session.get(
                    "https://api.binance.com/api/v3/klines",
                    params={
                        "symbol": sym,
                        "interval": "1d",
                        "startTime": current_start,
                        "endTime": final_end,
                        "limit": 1000,
                    },
                    timeout=self.timeout_seconds,
                )
                res.raise_for_status()
                rows = res.json()
            except Exception as exc:
                self._record_error(sym, "binance_1d", exc)
                break
            if not isinstance(rows, list) or not rows:
                break
            last_open_ms = None
            for row in rows:
                if not isinstance(row, list) or len(row) < 5:
                    continue
                try:
                    open_ms = int(row[0])
                    close_price = float(row[4] or 0.0)
                except Exception:
                    continue
                if close_price <= 0.0:
                    continue
                day_key = datetime.fromtimestamp(open_ms / 1000.0, tz=KST).strftime("%Y-%m-%d")
                out[day_key] = {
                    "price": close_price,
                    "source": "binance_1d",
                    "as_of_ts": _day_end_ts(_parse_day(day_key)),
                }
                last_open_ms = open_ms
            if last_open_ms is None or len(rows) < 1000:
                break
            current_start = int(last_open_ms + 86400000)
        return out

    def _fetch_bybit_daily(self, symbol: str, start_day: date, end_day: date) -> dict[str, dict[str, Any]]:
        sym = _normalize_symbol(symbol)
        if not sym:
            return {}
        out: dict[str, dict[str, Any]] = {}
        current_start = _day_start_ts(start_day) * 1000
        final_end = _day_end_ts(end_day) * 1000
        while current_start <= final_end:
            try:
                res = self.session.get(
                    "https://api.bybit.com/v5/market/kline",
                    params={
                        "category": "linear",
                        "symbol": sym,
                        "interval": "D",
                        "start": current_start,
                        "end": final_end,
                        "limit": 1000,
                    },
                    timeout=self.timeout_seconds,
                )
                res.raise_for_status()
                body = res.json()
            except Exception as exc:
                self._record_error(sym, "bybit_1d", exc)
                break
            rows = (((body or {}).get("result") or {}).get("list") or []) if isinstance(body, dict) else []
            if not isinstance(rows, list) or not rows:
                break
            parsed: list[tuple[int, float]] = []
            for row in rows:
                if not isinstance(row, list) or len(row) < 5:
                    continue
                try:
                    open_ms = int(row[0])
                    close_price = float(row[4] or 0.0)
                except Exception:
                    continue
                if close_price <= 0.0:
                    continue
                parsed.append((open_ms, close_price))
            parsed.sort(key=lambda item: item[0])
            if not parsed:
                break
            for open_ms, close_price in parsed:
                day_key = datetime.fromtimestamp(open_ms / 1000.0, tz=KST).strftime("%Y-%m-%d")
                out[day_key] = {
                    "price": close_price,
                    "source": "bybit_1d",
                    "as_of_ts": _day_end_ts(_parse_day(day_key)),
                }
            if len(parsed) < 1000:
                break
            current_start = int(parsed[-1][0] + 86400000)
        return out

    def fetch_symbol_daily_series(self, symbol: str, start_day: date, end_day: date) -> dict[str, dict[str, Any]]:
        result = self._fetch_binance_daily(symbol, start_day, end_day)
        wanted_days = {_day_key(day_value) for day_value in _iter_days(start_day, end_day)}
        missing = sorted(day_key for day_key in wanted_days if day_key not in result)
        if missing:
            fallback = self._fetch_bybit_daily(symbol, start_day, end_day)
            for day_key in missing:
                if day_key in fallback:
                    result[day_key] = dict(fallback[day_key])
        return result

    def _fetch_binance_latest(self, symbol: str) -> dict[str, Any]:
        sym = _normalize_symbol(symbol)
        if not sym:
            return {}
        try:
            res = self.session.get(
                "https://api.binance.com/api/v3/ticker/price",
                params={"symbol": sym},
                timeout=self.timeout_seconds,
            )
            res.raise_for_status()
            body = res.json()
            price = _safe_float((body or {}).get("price"), 0.0)
            if price <= 0.0:
                return {}
            return {"price": float(price), "source": "binance_latest", "as_of_ts": int(time.time())}
        except Exception as exc:
            self._record_error(sym, "binance_latest", exc)
            return {}

    def _fetch_bybit_latest(self, symbol: str) -> dict[str, Any]:
        sym = _normalize_symbol(symbol)
        if not sym:
            return {}
        try:
            res = self.session.get(
                "https://api.bybit.com/v5/market/tickers",
                params={"category": "linear", "symbol": sym},
                timeout=self.timeout_seconds,
            )
            res.raise_for_status()
            body = res.json()
            rows = (((body or {}).get("result") or {}).get("list") or []) if isinstance(body, dict) else []
            row = rows[0] if isinstance(rows, list) and rows else {}
            price = _safe_float((row or {}).get("lastPrice"), 0.0)
            if price <= 0.0:
                return {}
            return {"price": float(price), "source": "bybit_latest", "as_of_ts": int(time.time())}
        except Exception as exc:
            self._record_error(sym, "bybit_latest", exc)
            return {}

    def fetch_symbol_latest_price(self, symbol: str) -> dict[str, Any]:
        latest = self._fetch_binance_latest(symbol)
        if _safe_float(latest.get("price"), 0.0) > 0.0:
            return latest
        return self._fetch_bybit_latest(symbol)


def _trade_sort_key(row: dict[str, Any], index: int) -> tuple[int, int]:
    return (_safe_int(row.get("ts"), 0), int(index))


def _is_crypto_trade(row: dict[str, Any]) -> bool:
    return str((row or {}).get("source") or "").strip().lower() == "crypto_demo"


def _is_close_trade(row: dict[str, Any]) -> bool:
    if not _is_crypto_trade(row):
        return False
    if str((row or {}).get("event_kind") or "").strip().lower() == "close":
        return True
    return bool(str((row or {}).get("close_mode") or "").strip())


def _is_open_trade(row: dict[str, Any]) -> bool:
    if not _is_crypto_trade(row):
        return False
    if str((row or {}).get("event_kind") or "").strip().lower() == "open":
        return True
    return not _is_close_trade(row)


def _normalize_position_side(row: dict[str, Any]) -> str:
    raw = str((row or {}).get("position_side") or (row or {}).get("side") or "").strip().lower()
    if raw in {"short", "sell"}:
        return "short"
    return "long"


def _build_open_position_from_trade(row: dict[str, Any]) -> dict[str, Any]:
    position_side = _normalize_position_side(row)
    price = _safe_float(row.get("price_usd"), 0.0)
    qty = _safe_float(row.get("qty"), 0.0)
    leverage = max(1.0, _safe_float(row.get("leverage"), 1.0))
    notional = _safe_float(row.get("notional_usd"), 0.0)
    margin = _safe_float(row.get("margin_usd"), 0.0)
    if margin <= 0.0 and notional > 0.0:
        margin = notional / leverage
    if margin <= 0.0 and price > 0.0 and qty > 0.0:
        margin = (price * qty) / leverage
    symbol = _normalize_symbol(row.get("symbol"))
    return {
        "symbol": symbol,
        "side": position_side,
        "qty": qty,
        "avg_price_usd": price,
        "last_mark_price_usd": price,
        "margin_usd": margin,
        "leverage": leverage,
        "notional_usd": notional if notional > 0.0 else price * qty,
        "opened_at": _safe_int(row.get("ts"), 0),
        "entry_score": _safe_float(row.get("entry_score"), 0.0),
        "reason": str(row.get("reason") or ""),
    }


def _build_synthetic_open_trades(run: dict[str, Any], trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    open_positions = dict(run.get("bybit_positions") or {})
    if not open_positions:
        return []
    existing_pairs = {
        (str((row or {}).get("symbol") or "").upper().strip(), _safe_int((row or {}).get("ts"), 0))
        for row in trades
        if _is_open_trade(row)
    }
    synthetic: list[dict[str, Any]] = []
    for pos in list(open_positions.values()):
        symbol = _normalize_symbol((pos or {}).get("symbol"))
        opened_at = _safe_int((pos or {}).get("opened_at"), 0)
        if not symbol or opened_at <= 0:
            continue
        if any(existing_symbol == symbol and abs(existing_ts - opened_at) <= 300 for existing_symbol, existing_ts in existing_pairs):
            continue
        position_side = "short" if str((pos or {}).get("side") or "").strip().lower() == "short" else "long"
        synthetic.append(
            {
                "ts": opened_at,
                "source": "crypto_demo",
                "event_kind": "open",
                "symbol": symbol,
                "token_address": symbol,
                "side": "sell" if position_side == "short" else "buy",
                "position_side": position_side,
                "qty": _safe_float((pos or {}).get("qty"), 0.0),
                "price_usd": _safe_float((pos or {}).get("avg_price_usd"), 0.0),
                "notional_usd": _safe_float((pos or {}).get("notional_usd"), 0.0),
                "margin_usd": _safe_float((pos or {}).get("margin_usd"), 0.0),
                "leverage": _safe_float((pos or {}).get("leverage"), 1.0),
                "reason": "rebuild_synthetic_open",
                "fill_mode": "rebuild",
            }
        )
    return synthetic


def _provider_summary_from_sources(sources: dict[str, str]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for source in dict(sources or {}).values():
        name = str(source or "").strip() or "unknown"
        summary[name] = int(summary.get(name) or 0) + 1
    return summary


def _rebuild_model_rows(
    model_id: str,
    run: dict[str, Any],
    *,
    start_day: date,
    end_day: date,
    seed_usdt: float,
    price_map: dict[str, dict[str, dict[str, Any]]],
    latest_price_map: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    trades = [dict(row or {}) for row in list(run.get("trades") or []) if isinstance(row, dict) and _is_crypto_trade(row)]
    trades.extend(_build_synthetic_open_trades(run, trades))
    events = sorted(enumerate(trades), key=lambda item: _trade_sort_key(item[1], item[0]))
    cash = float(seed_usdt)
    realized = 0.0
    closed = 0
    wins = 0
    open_positions: dict[str, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    skipped_days: list[dict[str, Any]] = []
    event_index = 0
    days = _iter_days(start_day, end_day)

    for day_value in days:
        day_end = _day_end_ts(day_value)
        while event_index < len(events) and _safe_int(events[event_index][1].get("ts"), 0) <= day_end:
            event = dict(events[event_index][1] or {})
            symbol = _normalize_symbol(event.get("symbol"))
            if _is_open_trade(event):
                pos = _build_open_position_from_trade(event)
                if symbol:
                    open_positions[symbol] = pos
                    cash -= float(pos.get("margin_usd") or 0.0)
            elif _is_close_trade(event):
                pnl = _safe_float(event.get("pnl_usd"), 0.0)
                margin = _safe_float(event.get("margin_usd"), 0.0)
                pos = open_positions.pop(symbol, None) if symbol else None
                if margin <= 0.0 and isinstance(pos, dict):
                    margin = _safe_float(pos.get("margin_usd"), 0.0)
                cash += max(0.0, margin + pnl)
                realized += pnl
                closed += 1
                wins += 1 if pnl > 0.0 else 0
            event_index += 1

        missing_symbols: list[str] = []
        eod_price_sources: dict[str, str] = {}
        position_value = 0.0
        unrealized = 0.0
        quote_as_of_ts = 0
        for symbol, pos in open_positions.items():
            day_series = dict(price_map.get(symbol) or {})
            mark_row = dict(day_series.get(_day_key(day_value)) or {})
            price = _safe_float(mark_row.get("price"), 0.0)
            if day_value == end_day:
                latest_row = dict((latest_price_map or {}).get(symbol) or {})
                latest_price = _safe_float(latest_row.get("price"), 0.0)
                if latest_price > 0.0:
                    price = latest_price
                    mark_row = {
                        "price": float(latest_price),
                        "source": str(latest_row.get("source") or "latest"),
                        "as_of_ts": _safe_int(latest_row.get("as_of_ts"), int(time.time())),
                    }
            if price <= 0.0:
                missing_symbols.append(symbol)
                continue
            eod_price_sources[symbol] = str(mark_row.get("source") or "")
            quote_as_of_ts = max(quote_as_of_ts, _safe_int(mark_row.get("as_of_ts"), day_end))
            marked = _mark_crypto_position(pos, price)
            position_value += float(marked.get("position_equity_usd") or 0.0)
            unrealized += float(marked.get("pnl_usd") or 0.0)

        if missing_symbols:
            skipped_days.append(
                {
                    "day": _day_key(day_value),
                    "model_id": model_id,
                    "missing_symbols": sorted(missing_symbols),
                }
            )
            continue

        equity = float(cash + position_value)
        total_pnl = float(equity - seed_usdt)
        win_rate = (wins / closed * 100.0) if closed > 0 else 0.0
        provider_summary = _provider_summary_from_sources(eod_price_sources)
        rows.append(
            {
                "date": _day_key(day_value),
                "model_id": model_id,
                "meme_equity_usd": 0.0,
                "bybit_equity_usd": round(equity, 6),
                "meme_total_pnl_usd": 0.0,
                "bybit_total_pnl_usd": round(total_pnl, 6),
                "bybit_cycle_total_pnl_usd": round(total_pnl, 6),
                "bybit_total_pnl_anchor_usd": 0.0,
                "meme_realized_pnl_usd": 0.0,
                "bybit_realized_pnl_usd": round(realized, 6),
                "meme_unrealized_pnl_usd": 0.0,
                "bybit_unrealized_pnl_usd": round(unrealized, 6),
                "meme_win_rate": 0.0,
                "bybit_win_rate": round(win_rate, 4),
                "meme_closed_trades": 0,
                "bybit_closed_trades": int(closed),
                "meme_open_positions": 0,
                "bybit_open_positions": int(len(open_positions)),
                "total_equity_usd": round(equity, 6),
                "total_pnl_usd": round(total_pnl, 6),
                "realized_pnl_usd": round(realized, 6),
                "unrealized_pnl_usd": round(unrealized, 6),
                "win_rate": round(win_rate, 4),
                "closed_trades": int(closed),
                "bybit_quote_status": "fresh",
                "bybit_quote_stale": False,
                "bybit_quote_as_of_ts": int(quote_as_of_ts or day_end),
                "bybit_quote_age_seconds": 0,
                "bybit_quote_stale_after_seconds": 0,
                "bybit_quote_symbols": sorted(open_positions.keys()),
                "bybit_quote_stale_symbols": [],
                "bybit_quote_guard_symbols": [],
                "bybit_quote_realtime_sources": sorted({source for source in eod_price_sources.values() if source}),
                "bybit_quote_fallback_sources": [],
                "bybit_quote_sync_attempted_symbols": sorted(open_positions.keys()),
                "bybit_quote_sync_synced_symbols": sorted(open_positions.keys()),
                "bybit_quote_sync_missing_symbols": [],
                "bybit_quote_sync_provider_summary": dict(provider_summary),
                "bybit_quote_sync_reason": "daily_close_replay",
                "bybit_quote_sync_at_ts": int(quote_as_of_ts or day_end),
                "rebuild_source": "daily_close_replay",
                "eod_price_sources": dict(eod_price_sources),
                "replay_window": {"from": _day_key(start_day), "to": _day_key(end_day)},
            }
        )
    return rows, skipped_days


def _build_supabase_daily_rows(rows: list[dict[str, Any]], *, updated_ts: int) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    snapshot_iso = datetime.fromtimestamp(updated_ts, tz=UTC).isoformat()
    for row in rows:
        source_json = dict(row.get("source_json") or {})
        if not isinstance(source_json, dict):
            source_json = {}
        payload.append(
            {
                "day": str(row.get("date") or ""),
                "market": "crypto",
                "model_id": str(row.get("model_id") or ""),
                "bybit_rebuild_restart_variant_id": str(
                    row.get("bybit_rebuild_restart_variant_id")
                    or source_json.get("rebuild_restart_variant_id")
                    or ""
                ),
                "bybit_rebuild_restart_note_ko": str(
                    row.get("bybit_rebuild_restart_note_ko")
                    or source_json.get("rebuild_restart_note_ko")
                    or ""
                ),
                "bybit_rebuild_restart_seed_usd": float(
                    row.get("bybit_rebuild_restart_seed_usd")
                    or source_json.get("rebuild_restart_seed_usd")
                    or 0.0
                ),
                "bybit_rebuild_restart_ts": int(
                    row.get("bybit_rebuild_restart_ts")
                    or source_json.get("rebuild_restart_ts")
                    or 0
                ),
                "equity_usd": float(row.get("bybit_equity_usd") or 0.0),
                "total_pnl_usd": float(row.get("bybit_total_pnl_usd") or 0.0),
                "realized_pnl_usd": float(row.get("bybit_realized_pnl_usd") or 0.0),
                "unrealized_pnl_usd": float(row.get("bybit_unrealized_pnl_usd") or 0.0),
                "win_rate": float(row.get("bybit_win_rate") or 0.0),
                "closed_trades": int(row.get("bybit_closed_trades") or 0),
                "updated_at": snapshot_iso,
                "source_json": {
                    "snapshot_at": snapshot_iso,
                    "rebuild_source": str(row.get("rebuild_source") or "daily_close_replay"),
                    "rebuild_restart_variant_id": str(
                        row.get("bybit_rebuild_restart_variant_id")
                        or source_json.get("rebuild_restart_variant_id")
                        or ""
                    ),
                    "rebuild_restart_note_ko": str(
                        row.get("bybit_rebuild_restart_note_ko")
                        or source_json.get("rebuild_restart_note_ko")
                        or ""
                    ),
                    "rebuild_restart_seed_usd": float(
                        row.get("bybit_rebuild_restart_seed_usd")
                        or source_json.get("rebuild_restart_seed_usd")
                        or 0.0
                    ),
                    "rebuild_restart_ts": int(
                        row.get("bybit_rebuild_restart_ts")
                        or source_json.get("rebuild_restart_ts")
                        or 0
                    ),
                    "cycle_total_pnl_usd": float(
                        _safe_float(
                            row.get("bybit_cycle_total_pnl_usd")
                            if row.get("bybit_cycle_total_pnl_usd") not in {None, ""}
                            else source_json.get("cycle_total_pnl_usd"),
                            0.0,
                        )
                    ),
                    "total_pnl_anchor_usd": float(
                        _safe_float(
                            row.get("bybit_total_pnl_anchor_usd")
                            if row.get("bybit_total_pnl_anchor_usd") not in {None, ""}
                            else source_json.get("total_pnl_anchor_usd"),
                            0.0,
                        )
                    ),
                    "eod_price_sources": dict(row.get("eod_price_sources") or {}),
                    "quote_sync_missing_symbols": list(row.get("bybit_quote_sync_missing_symbols") or []),
                    "quote_sync_provider_summary": dict(row.get("bybit_quote_sync_provider_summary") or {}),
                    "replay_window": dict(row.get("replay_window") or {}),
                    "quote_status": str(row.get("bybit_quote_status") or "fresh"),
                    "quote_as_of": datetime.fromtimestamp(
                        int(row.get("bybit_quote_as_of_ts") or updated_ts), tz=UTC
                    ).isoformat(),
                },
            }
        )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild crypto daily PnL rows from trade replay and daily closes.")
    parser.add_argument("--from", dest="from_day", default="2026-03-28", help="KST start day (YYYY-MM-DD)")
    parser.add_argument(
        "--to",
        dest="to_day",
        default=datetime.now(tz=KST).strftime("%Y-%m-%d"),
        help="KST end day (YYYY-MM-DD)",
    )
    parser.add_argument("--state-file", default="", help="Optional engine state JSON path")
    parser.add_argument("--output-dir", default="", help="Optional daily report output directory")
    parser.add_argument("--write-state", action="store_true", help="Write rebuilt daily_pnl into local state and engine_state blob")
    parser.add_argument("--write-supabase", action="store_true", help="Upsert rebuilt rows into daily_model_pnl")
    parser.add_argument("--write-docs", action="store_true", help="Rewrite docs/data/daily_pnl artifacts")
    parser.add_argument("--dry-run", action="store_true", help="Preview only")
    args = parser.parse_args()

    _load_env_files()
    settings = load_settings()
    start_day = _parse_day(args.from_day)
    end_day = _parse_day(args.to_day)
    if end_day < start_day:
        raise SystemExit("end_day_before_start_day")

    client = _build_supabase_client()
    state_path = Path(args.state_file).resolve() if str(args.state_file or "").strip() else Path(str(settings.state_file or "state.json")).resolve()
    state_payload = _load_state_payload(state_path if state_path.exists() else None, client)
    model_runs = dict(state_payload.get("model_runs") or {})
    price_client = DailyPriceClient()

    symbols: list[str] = []
    for model_id in MODEL_IDS:
        run = dict(model_runs.get(f"crypto_{model_id}") or {})
        for tr in list(run.get("trades") or []):
            symbol = _normalize_symbol((tr or {}).get("symbol"))
            if symbol and symbol not in symbols and _is_crypto_trade(tr):
                symbols.append(symbol)
        for pos in list((run.get("bybit_positions") or {}).values()):
            symbol = _normalize_symbol((pos or {}).get("symbol"))
            if symbol and symbol not in symbols:
                symbols.append(symbol)

    price_map: dict[str, dict[str, dict[str, Any]]] = {}
    for symbol in symbols:
        price_map[symbol] = price_client.fetch_symbol_daily_series(symbol, start_day, end_day)
    latest_price_map: dict[str, dict[str, Any]] = {}
    for symbol in symbols:
        latest_price_map[symbol] = price_client.fetch_symbol_latest_price(symbol)

    rebuilt_rows: list[dict[str, Any]] = []
    skipped_days: list[dict[str, Any]] = []
    seed_usdt = float(MODEL_FIXED_SEED_USD)
    for model_id in MODEL_IDS:
        run = dict(model_runs.get(f"crypto_{model_id}") or {})
        model_rows, model_skipped = _rebuild_model_rows(
            model_id,
            run,
            start_day=start_day,
            end_day=end_day,
            seed_usdt=seed_usdt,
            price_map=price_map,
            latest_price_map=latest_price_map,
        )
        rebuilt_rows.extend(model_rows)
        skipped_days.extend(model_skipped)

    docs_dir = (
        Path(args.output_dir).resolve()
        if str(args.output_dir or "").strip()
        else (ROOT / "docs/data/daily_pnl").resolve()
    )
    existing_index: dict[tuple[str, str], dict[str, Any]] = {}
    existing_index.update(_build_supabase_daily_index(client, start_day=start_day, end_day=end_day))
    existing_index.update(_build_state_daily_index(state_payload, start_day=start_day, end_day=end_day))
    existing_index.update(_build_docs_daily_index(docs_dir, start_day=start_day, end_day=end_day))
    rebuilt_rows, existing_rows_in_window, preserved_nonzero_rows = _merge_rebuilt_with_existing(
        rebuilt_rows,
        existing_index=existing_index,
        start_day=start_day,
        end_day=end_day,
    )
    rebuilt_rows = _apply_model_anchor_policy(rebuilt_rows, seed_usd=seed_usdt)

    updated_ts = int(time.time())

    state_result: dict[str, Any] = {"ok": False, "updated_rows": 0}
    if args.write_state and not args.dry_run:
        existing_daily = [dict(row or {}) for row in list(state_payload.get("daily_pnl") or [])]
        filtered_daily: list[dict[str, Any]] = []
        for row in existing_daily:
            parsed_day = _try_parse_day((row or {}).get("date"))
            model_id = str((row or {}).get("model_id") or "").upper()
            if model_id in MODEL_IDS and parsed_day is not None and parsed_day < start_day:
                continue
            if parsed_day is not None and start_day <= parsed_day <= end_day and model_id in MODEL_IDS:
                continue
            filtered_daily.append(row)
        filtered_daily.extend(rebuilt_rows)
        filtered_daily.sort(key=lambda row: (str(row.get("date") or ""), str(row.get("model_id") or "")))
        state_payload["daily_pnl"] = filtered_daily[-1200:]
        _persist_json(state_path, state_payload)
        state_result = {"ok": True, "updated_rows": len(rebuilt_rows), "state_file": str(state_path)}
        if client.enabled:
            blob_result = client.upsert_blob("engine_state", state_payload)
            state_result["engine_state_blob"] = blob_result
            state_result["ok"] = bool(state_result["ok"] and bool(blob_result.get("ok")))

    supabase_result: dict[str, Any] = {"ok": False, "count": 0}
    if args.write_supabase and not args.dry_run:
        if not client.enabled:
            raise RuntimeError("supabase_client_disabled")
        supabase_rows = _build_supabase_daily_rows(rebuilt_rows, updated_ts=updated_ts)
        supabase_result = client.upsert_rows("daily_model_pnl", supabase_rows, on_conflict="day,market,model_id")

    docs_result: dict[str, Any] = {"ok": False, "files": 0}
    if args.write_docs and not args.dry_run:
        output_dir = docs_dir
        output_dir.mkdir(parents=True, exist_ok=True)

        for path in (
            output_dir / "summary.csv",
            output_dir / "summary.json",
            output_dir / "summary_4col.csv",
            output_dir / "summary_4col.json",
        ):
            if path.exists():
                path.unlink()
        for path in output_dir.glob("20??-??-??.json"):
            parsed_day = _try_parse_day(path.stem)
            if parsed_day is not None and parsed_day < start_day:
                path.unlink()
        for path in output_dir.glob("20??-??-??.csv"):
            parsed_day = _try_parse_day(path.stem)
            if parsed_day is not None and parsed_day < start_day:
                path.unlink()

        # Preserve existing history outside the requested range.
        # Only overwrite artifacts for days we successfully rebuilt.
        written_files: set[str] = set()
        for day_value in _iter_days(start_day, end_day):
            day_key = _day_key(day_value)
            day_rows = [dict(row) for row in rebuilt_rows if str(row.get("date") or "") == day_key]
            if not day_rows:
                continue
            for path in write_daily_pnl_report(day_key, day_rows, str(output_dir)):
                written_files.add(str(path))

        docs_result = {"ok": True, "files": len(written_files), "output_dir": str(output_dir)}

    summary = {
        "ok": True,
        "from": _day_key(start_day),
        "to": _day_key(end_day),
        "rows": len(rebuilt_rows),
        "existing_rows_in_window": int(existing_rows_in_window),
        "preserved_nonzero_rows": int(preserved_nonzero_rows),
        "symbols": len(symbols),
        "dry_run": bool(args.dry_run),
        "skipped_days": skipped_days,
        "price_fetch_errors": dict(price_client.errors),
        "write_state": state_result,
        "write_supabase": supabase_result,
        "write_docs": docs_result,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": str(exc or exc.__class__.__name__),
                    "type": exc.__class__.__name__,
                    "traceback": traceback.format_exc().splitlines()[-30:],
                },
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        raise
