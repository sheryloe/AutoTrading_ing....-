from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import dotenv_values


TABLE_TIME_COLUMNS: dict[str, str] = {
    "model_signal_audit": "cycle_at",
    "model_setups": "cycle_at",
    "positions": "updated_at",
    "daily_model_pnl": "updated_at",
    "engine_state_blobs": "updated_at",
    "model_runtime_tunes": "updated_at",
    "engine_runtime_config": "updated_at",
    "instruments": "updated_at",
    "engine_heartbeat": "updated_at",
}


def _iso_utc(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).isoformat()


def _parse_content_range_total(content_range: str) -> int:
    text = str(content_range or "").strip()
    match = re.search(r"/(\d+)$", text)
    if not match:
        return -1
    return int(match.group(1))


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


class SupabaseAudit:
    def __init__(self, url: str, key: str, timeout: int = 20) -> None:
        self.url = str(url or "").rstrip("/")
        self.key = str(key or "").strip()
        self.timeout = max(5, int(timeout))
        self.session = requests.Session()
        self.base_headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
        }

    def _table_url(self, table: str) -> str:
        return f"{self.url}/rest/v1/{str(table).strip()}"

    def _fetch_rows(
        self,
        table: str,
        *,
        params: dict[str, Any] | None = None,
        prefer_count: bool = False,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        headers = dict(self.base_headers)
        if prefer_count:
            headers["Prefer"] = "count=exact"
        response = self.session.get(
            self._table_url(table),
            params=dict(params or {}),
            headers=headers,
            timeout=self.timeout,
        )
        if not response.ok:
            return {
                "ok": False,
                "status": response.status_code,
                "error": response.text[:300],
                "content_range": response.headers.get("Content-Range"),
            }, []
        try:
            rows = response.json()
        except Exception:
            rows = []
        if not isinstance(rows, list):
            rows = []
        return {
            "ok": True,
            "status": response.status_code,
            "content_range": response.headers.get("Content-Range"),
        }, rows

    def count_rows(self, table: str, *, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        params = {"select": "*", "limit": "1"}
        if filters:
            params.update(filters)
        meta, _ = self._fetch_rows(table, params=params, prefer_count=True)
        if not meta.get("ok"):
            return meta
        return {
            "ok": True,
            "status": int(meta.get("status") or 200),
            "count": _parse_content_range_total(str(meta.get("content_range") or "")),
            "content_range": meta.get("content_range"),
        }


def _load_supabase_credentials(env_path: Path) -> tuple[str, str]:
    cfg = dotenv_values(str(env_path))
    url = str(cfg.get("SUPABASE_URL") or "").strip()
    key = str(cfg.get("SUPABASE_SERVICE_ROLE_KEY") or cfg.get("SUPABASE_SECRET_KEY") or "").strip()
    return url, key


def run(env_path: Path, lookback_hours: int, timeout: int) -> dict[str, Any]:
    url, key = _load_supabase_credentials(env_path)
    if not url or not key:
        raise RuntimeError("missing_supabase_credentials")

    audit = SupabaseAudit(url=url, key=key, timeout=timeout)
    now_utc = datetime.now(timezone.utc)
    cutoff_utc = now_utc - timedelta(hours=max(1, int(lookback_hours)))
    cutoff_iso = _iso_utc(cutoff_utc)

    table_stats: dict[str, Any] = {}
    for table, time_col in TABLE_TIME_COLUMNS.items():
        total = audit.count_rows(table)
        recent = audit.count_rows(table, filters={time_col: f"gte.{cutoff_iso}"})
        table_stats[table] = {
            "time_col": time_col,
            "total": total,
            "recent": recent,
        }

    heartbeat_meta, heartbeat_rows = audit._fetch_rows(
        "engine_heartbeat",
        params={
            "select": "engine_name,last_seen_at,updated_at,last_error,meta_json",
            "order": "updated_at.desc",
            "limit": "1",
        },
    )
    heartbeat = dict((heartbeat_rows or [{}])[0] or {}) if heartbeat_meta.get("ok") else {}
    heartbeat_meta_json = dict(heartbeat.get("meta_json") or {}) if isinstance(heartbeat.get("meta_json"), dict) else {}

    open_positions_meta, open_positions = audit._fetch_rows(
        "positions",
        params={
            "select": "model_id,symbol,status,updated_at,position_meta",
            "market": "eq.crypto",
            "status": "eq.open",
            "order": "model_id.asc,updated_at.desc",
            "limit": "400",
        },
    )

    by_model_positions: dict[str, Any] = defaultdict(
        lambda: {
            "open_positions": 0,
            "guarded_positions": 0,
            "stale_positions": 0,
            "symbols": [],
            "latest_updated_at": "",
        }
    )
    if open_positions_meta.get("ok"):
        for row in list(open_positions or []):
            model = str(row.get("model_id") or "").upper().strip()
            symbol = str(row.get("symbol") or "").upper().strip()
            meta = dict(row.get("position_meta") or {}) if isinstance(row.get("position_meta"), dict) else {}
            quote_status = str(meta.get("quote_status") or "").lower().strip()
            updated_at = str(row.get("updated_at") or "").strip()
            if not model:
                continue
            item = by_model_positions[model]
            item["open_positions"] = int(item["open_positions"]) + 1
            if quote_status == "guarded":
                item["guarded_positions"] = int(item["guarded_positions"]) + 1
            elif quote_status == "stale":
                item["stale_positions"] = int(item["stale_positions"]) + 1
            if symbol and symbol not in item["symbols"]:
                item["symbols"].append(symbol)
            if updated_at and (not item["latest_updated_at"] or updated_at > str(item["latest_updated_at"])):
                item["latest_updated_at"] = updated_at

    trades_blob_meta, trades_blob_rows = audit._fetch_rows(
        "engine_state_blobs",
        params={
            "select": "updated_at,payload_json",
            "blob_key": "eq.recent_crypto_trades",
            "limit": "1",
        },
    )
    close_events_by_model: dict[str, int] = defaultdict(int)
    close_events_total = 0
    recent_blob_updated_at = ""
    if trades_blob_meta.get("ok") and trades_blob_rows:
        blob_row = dict(trades_blob_rows[0] or {})
        recent_blob_updated_at = str(blob_row.get("updated_at") or "").strip()
        payload = dict(blob_row.get("payload_json") or {}) if isinstance(blob_row.get("payload_json"), dict) else {}
        trades = list(payload.get("rows") or [])
        for tr in trades:
            row = dict(tr or {})
            if str(row.get("event_kind") or "").strip().lower() != "close":
                continue
            model = str(row.get("model_id") or "").upper().strip() or "UNKNOWN"
            close_events_total += 1
            close_events_by_model[model] += 1

    heavy_tables: list[dict[str, Any]] = []
    for table, stat in table_stats.items():
        recent_count = _safe_int(((stat.get("recent") or {}).get("count")), 0)
        total_count = _safe_int(((stat.get("total") or {}).get("count")), 0)
        if recent_count >= 2000 or total_count >= 50000:
            heavy_tables.append(
                {
                    "table": table,
                    "recent_count": recent_count,
                    "total_count": total_count,
                    "time_col": str(stat.get("time_col") or ""),
                }
            )

    last_seen_at = str(heartbeat.get("last_seen_at") or "").strip()
    heartbeat_stale = False
    if last_seen_at:
        try:
            last_seen_dt = datetime.fromisoformat(last_seen_at.replace("Z", "+00:00")).astimezone(timezone.utc)
            heartbeat_stale = (now_utc - last_seen_dt) > timedelta(minutes=10)
        except Exception:
            heartbeat_stale = False

    result = {
        "now_utc": _iso_utc(now_utc),
        "lookback_hours": int(lookback_hours),
        "cutoff_utc": cutoff_iso,
        "table_stats": table_stats,
        "heavy_tables": heavy_tables,
        "heartbeat": {
            "row": heartbeat,
            "meta_json": heartbeat_meta_json,
            "stale": bool(heartbeat_stale),
        },
        "open_positions": {
            "ok": bool(open_positions_meta.get("ok")),
            "count": len(open_positions or []),
            "by_model": dict(sorted(by_model_positions.items(), key=lambda x: x[0])),
        },
        "recent_crypto_trades": {
            "blob_updated_at": recent_blob_updated_at,
            "close_events_total_in_blob": int(close_events_total),
            "close_events_by_model_in_blob": dict(sorted(close_events_by_model.items(), key=lambda x: x[0])),
        },
    }
    return result


def print_report(payload: dict[str, Any]) -> None:
    print(f"[audit_at_utc] {payload.get('now_utc')}")
    print(f"[window] last {payload.get('lookback_hours')}h (cutoff={payload.get('cutoff_utc')})")
    print("")
    print("[table_stats] total / recent_window")
    for table, stat in (payload.get("table_stats") or {}).items():
        total = stat.get("total") or {}
        recent = stat.get("recent") or {}
        total_count = _safe_int(total.get("count"), -1)
        recent_count = _safe_int(recent.get("count"), -1)
        marker = ""
        if recent_count >= 2000 or total_count >= 50000:
            marker = "  <-- heavy"
        print(
            f"  - {table}: total={total_count}, recent={recent_count}, "
            f"time_col={stat.get('time_col')}{marker}"
        )
    print("")

    heartbeat = (payload.get("heartbeat") or {}).get("row") or {}
    heartbeat_meta = (payload.get("heartbeat") or {}).get("meta_json") or {}
    print("[heartbeat]")
    print(f"  - last_seen_at: {heartbeat.get('last_seen_at')}")
    print(f"  - updated_at: {heartbeat.get('updated_at')}")
    print(f"  - stale: {bool((payload.get('heartbeat') or {}).get('stale'))}")
    print(f"  - held_quote_sync_reason: {heartbeat_meta.get('held_quote_sync_reason')}")
    print(f"  - held_quote_sync_missing: {heartbeat_meta.get('held_quote_sync_missing')}")
    position_watch = heartbeat_meta.get("position_watch")
    if isinstance(position_watch, dict):
        print("  - position_watch: available")
        print(f"    total_open_positions={position_watch.get('total_open_positions')}")
        print(f"    models_with_stale_quotes={position_watch.get('models_with_stale_quotes')}")
        print(f"    models_with_guarded_quotes={position_watch.get('models_with_guarded_quotes')}")
    else:
        print("  - position_watch: missing")
    print("")

    open_positions = payload.get("open_positions") or {}
    print("[open_positions_by_model]")
    print(f"  - open_count={open_positions.get('count')}")
    for model, row in (open_positions.get("by_model") or {}).items():
        print(
            f"  - {model}: open={row.get('open_positions')}, guarded={row.get('guarded_positions')}, "
            f"stale={row.get('stale_positions')}, symbols={row.get('symbols')}, updated_at={row.get('latest_updated_at')}"
        )
    print("")

    recent_blob = payload.get("recent_crypto_trades") or {}
    print("[recent_crypto_trades]")
    print(f"  - blob_updated_at: {recent_blob.get('blob_updated_at')}")
    print(f"  - close_events_total_in_blob: {recent_blob.get('close_events_total_in_blob')}")
    print(f"  - close_events_by_model_in_blob: {recent_blob.get('close_events_by_model_in_blob')}")
    print("")

    print("[heavy_tables]")
    heavy = payload.get("heavy_tables") or []
    if not heavy:
        print("  - none")
    else:
        for item in heavy:
            print(
                f"  - {item.get('table')}: recent={item.get('recent_count')}, "
                f"total={item.get('total_count')}, time_col={item.get('time_col')}"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit Supabase usage pressure and model position monitoring signals.")
    parser.add_argument("--env-file", default=".env", help="Path to env file containing SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY.")
    parser.add_argument("--lookback-hours", type=int, default=24, help="Window size for recent activity counts.")
    parser.add_argument("--timeout", type=int, default=20, help="HTTP timeout seconds.")
    parser.add_argument("--json", action="store_true", help="Print JSON only.")
    args = parser.parse_args()

    try:
        result = run(Path(args.env_file), lookback_hours=int(args.lookback_hours), timeout=int(args.timeout))
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=True))
        return 1

    if args.json:
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False, indent=2))
    else:
        print_report(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
