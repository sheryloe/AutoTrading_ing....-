from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import (
    load_settings,
    normalize_runtime_data_sources,
    normalize_runtime_universe_mode,
    save_runtime_overrides,
)
from src.state import EngineState, state_to_dict
from src.supabase_sync import SupabaseSyncClient


SCOPE_TABLE_FILTERS: dict[str, list[tuple[str, str]]] = {
    "minimal": [
        ("positions", "id"),
    ],
    "trade_only": [
        ("positions", "id"),
        ("model_setups", "id"),
        ("daily_model_pnl", "day"),
        ("model_runtime_tunes", "model_id"),
    ],
    "full": [
        ("positions", "id"),
        ("model_setups", "id"),
        ("model_signal_audit", "cycle_at"),
        ("daily_model_pnl", "day"),
        ("model_runtime_tunes", "model_id"),
        ("engine_heartbeat", "engine_name"),
    ],
}

FULL_RESET_BLOB_KEYS = (
    "engine_state",
    "online_model",
    "recent_crypto_trades",
    "free_tier_capacity_report",
)

RUNTIME_FORCE_OVERRIDES: dict[str, Any] = {
    "DEMO_SEED_USDT": 10000.0,
    "DEMO_RESET_BLOCK_UNTIL_TS": 0,
    "TRADE_MODE": "paper",
    "EXECUTION_TARGET": "paper",
    "ENABLE_LIVE_EXECUTION": False,
    "LIVE_ENABLE_CRYPTO": False,
    "BYBIT_READONLY_SYNC": True,
    "CRYPTO_DATA_SOURCE_ORDER": "binance,bybit,coingecko",
    "CRYPTO_USE_BINANCE_DATA": True,
    "CRYPTO_USE_BYBIT_DATA": True,
    "CRYPTO_USE_COINGECKO_DATA": True,
    "MACRO_UNIVERSE_SOURCE": "exchange",
}


def _require_env(name: str) -> str:
    value = str(os.getenv(name) or "").strip()
    return value


def _resolve_supabase() -> tuple[str, str]:
    url = _require_env("SUPABASE_URL")
    key = _require_env("SUPABASE_SERVICE_ROLE_KEY") or _require_env("SUPABASE_SECRET_KEY")
    return url, key


def _load_env_files() -> None:
    candidates = [ROOT / "env" / ".env", ROOT / ".env"]
    for path in candidates:
        if not path.exists():
            continue
        payload = dotenv_values(str(path))
        for key, value in payload.items():
            if key and value is not None and not os.getenv(key):
                os.environ[str(key)] = str(value)


def _build_engine_state(seed: float) -> dict[str, Any]:
    state = EngineState(cash_usd=float(seed), demo_seed_usdt=float(seed))
    return state_to_dict(state)


def _delete_all(client: SupabaseSyncClient, table: str, filter_col: str) -> dict[str, Any]:
    return client.delete_rows(table, filters={filter_col: "not.is.null"})


def _format_in_values(values: list[Any]) -> str:
    parts: list[str] = []
    for raw in list(values or []):
        if isinstance(raw, bool):
            parts.append("true" if raw else "false")
            continue
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            parts.append(str(raw))
            continue
        text = str(raw or "").replace('"', '\\"')
        parts.append(f"\"{text}\"")
    return ",".join(parts)


def _delete_table_in_chunks(
    client: SupabaseSyncClient,
    table: str,
    *,
    id_col: str = "id",
    chunk_size: int = 500,
    dry_run: bool = False,
) -> dict[str, Any]:
    if dry_run:
        return {"ok": True, "dry_run": True}
    deleted = 0
    loops = 0
    while True:
        loops += 1
        fetched = client.fetch_rows(
            table,
            params={
                "select": id_col,
                "order": f"{id_col}.asc",
                "limit": str(max(50, min(1000, int(chunk_size)))),
            },
        )
        if not bool(fetched.get("ok")):
            return {"ok": False, "error": fetched.get("error") or "chunk_fetch_failed", "deleted": deleted}
        ids = []
        for row in list(fetched.get("rows") or []):
            item = dict(row or {})
            value = item.get(id_col)
            if value not in (None, ""):
                ids.append(value)
        if not ids:
            return {"ok": True, "deleted": deleted, "loops": loops}
        filter_value = _format_in_values(ids)
        result = client.delete_rows(table, filters={id_col: f"in.({filter_value})"})
        if not bool(result.get("ok")):
            return {"ok": False, "error": result.get("error") or "chunk_delete_failed", "deleted": deleted}
        deleted += len(ids)


def _delete_table_in_chunks_by_key(
    client: SupabaseSyncClient,
    table: str,
    *,
    key_col: str,
    chunk_size: int = 500,
    dry_run: bool = False,
) -> dict[str, Any]:
    if dry_run:
        return {"ok": True, "dry_run": True}
    deleted_floor = 0
    loops = 0
    last_upper = ""
    while True:
        loops += 1
        fetched = client.fetch_rows(
            table,
            params={
                "select": key_col,
                "order": f"{key_col}.asc",
                "limit": str(max(50, min(1000, int(chunk_size)))),
            },
        )
        if not bool(fetched.get("ok")):
            return {"ok": False, "error": fetched.get("error") or "chunk_fetch_failed", "deleted": deleted_floor}
        keys: list[str] = []
        for row in list(fetched.get("rows") or []):
            item = dict(row or {})
            value = str(item.get(key_col) or "").strip()
            if value:
                keys.append(value)
        if not keys:
            return {"ok": True, "deleted": deleted_floor, "loops": loops}
        upper = str(keys[-1])
        if upper == last_upper:
            return {"ok": False, "error": "chunk_delete_stalled", "deleted": deleted_floor}
        last_upper = upper
        result = client.delete_rows(table, filters={key_col: f"lte.{upper}"})
        if not bool(result.get("ok")):
            return {"ok": False, "error": result.get("error") or "chunk_delete_failed", "deleted": deleted_floor}
        deleted_floor += len(keys)


def _delete_blob_keys(client: SupabaseSyncClient, blob_keys: list[str], *, dry_run: bool) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for blob_key in list(blob_keys or []):
        if dry_run:
            result = {"ok": True, "dry_run": True}
        else:
            result = client.delete_rows("engine_state_blobs", filters={"blob_key": f"eq.{blob_key}"})
        rows.append({"blob_key": blob_key, "result": result})
    return rows


def _parse_anchor_day(anchor_day: str) -> str:
    text = str(anchor_day or "").strip()
    if not text:
        raise ValueError("anchor_day_required")
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d")
    except Exception as exc:  # noqa: BLE001
        raise ValueError("invalid_anchor_day_format") from exc
    return parsed.strftime("%Y-%m-%d")


def _expected_confirm(anchor_day: str) -> str:
    return f"HARD_RESET_{anchor_day.replace('-', '_')}"


def _apply_runtime_overrides(*, seed: float, dry_run: bool) -> dict[str, Any]:
    settings = load_settings()
    payload = dict(RUNTIME_FORCE_OVERRIDES)
    payload["DEMO_SEED_USDT"] = float(seed)
    payload = normalize_runtime_data_sources(payload)
    payload = normalize_runtime_universe_mode(payload)
    if dry_run:
        return {"ok": True, "dry_run": True, "updates": payload}
    save_runtime_overrides(settings, payload)
    return {"ok": True, "dry_run": False, "updates": payload}


def _upsert_service_runtime_config(
    client: SupabaseSyncClient,
    *,
    seed: float,
    dry_run: bool,
) -> dict[str, Any]:
    current: dict[str, Any] = {}
    fetched = client.fetch_blob("service_runtime_config")
    fetched_payload = fetched.get("payload") if bool(fetched.get("ok")) else None
    if isinstance(fetched_payload, dict):
        current.update(fetched_payload)
    updates = dict(RUNTIME_FORCE_OVERRIDES)
    updates["DEMO_SEED_USDT"] = float(seed)
    current.update(updates)
    current = normalize_runtime_data_sources(current)
    current = normalize_runtime_universe_mode(current)
    if dry_run:
        return {"ok": True, "dry_run": True, "updates": current}
    return client.upsert_blob("service_runtime_config", current)


def main() -> int:
    parser = argparse.ArgumentParser(description="Reset demo seed and clear demo tables in Supabase.")
    parser.add_argument("--seed", type=float, default=10000.0, help="Seed USDT value (default: 10000)")
    parser.add_argument(
        "--scope",
        choices=("full", "trade_only", "minimal"),
        default="trade_only",
        help="Reset scope (default: trade_only)",
    )
    parser.add_argument("--anchor-day", type=str, default="", help="Anchor day in YYYY-MM-DD format")
    parser.add_argument("--confirm", type=str, default="", help="Confirmation text")
    parser.add_argument("--dry-run", action="store_true", help="Print actions without making changes")
    args = parser.parse_args()

    _load_env_files()

    if args.seed <= 0:
        print(json.dumps({"ok": False, "error": "seed_must_be_positive"}, ensure_ascii=False, indent=2))
        return 2

    url, key = _resolve_supabase()
    if not url or not key:
        if args.dry_run:
            print(
                json.dumps(
                    {
                        "ok": True,
                        "dry_run": True,
                        "seed": float(args.seed),
                        "note": "missing_supabase_env",
                        "required": ["SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY|SUPABASE_SECRET_KEY"],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "missing_supabase_env",
                    "required": ["SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY|SUPABASE_SECRET_KEY"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    anchor_day = ""
    expected_confirm = ""
    if str(args.scope).strip().lower() == "full":
        anchor_day = _parse_anchor_day(str(args.anchor_day or ""))
        expected_confirm = _expected_confirm(anchor_day)
        if str(args.confirm or "").strip() != expected_confirm:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": "invalid_confirmation_text",
                        "expected": expected_confirm,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 2

    client = SupabaseSyncClient(url=url, secret_key=key, enabled=True, timeout_seconds=20)

    actions = []
    selected_scope = str(args.scope or "trade_only").strip().lower()
    for table, filter_col in list(SCOPE_TABLE_FILTERS.get(selected_scope) or []):
        if str(filter_col).strip().lower() == "id":
            result = _delete_table_in_chunks(
                client,
                table,
                id_col="id",
                chunk_size=500,
                dry_run=bool(args.dry_run),
            )
        elif table == "model_signal_audit" and str(filter_col).strip().lower() == "cycle_at":
            result = _delete_table_in_chunks_by_key(
                client,
                table,
                key_col="cycle_at",
                chunk_size=1000,
                dry_run=bool(args.dry_run),
            )
        else:
            if args.dry_run:
                result = {"ok": True, "dry_run": True}
            else:
                result = _delete_all(client, table, filter_col)
        actions.append({"table": table, "filter": f"{filter_col}=not.is.null", "result": result})

    blob_actions: list[dict[str, Any]] = []
    if selected_scope == "full":
        blob_actions = _delete_blob_keys(client, list(FULL_RESET_BLOB_KEYS), dry_run=bool(args.dry_run))

    engine_state = _build_engine_state(args.seed)
    if args.dry_run:
        blob_result = {"ok": True, "dry_run": True}
    else:
        blob_result = client.upsert_blob("engine_state", engine_state)

    runtime_result = _apply_runtime_overrides(seed=float(args.seed), dry_run=bool(args.dry_run))
    service_runtime_result = _upsert_service_runtime_config(
        client,
        seed=float(args.seed),
        dry_run=bool(args.dry_run),
    )

    ok = (
        all(item.get("result", {}).get("ok") for item in actions)
        and all(item.get("result", {}).get("ok") for item in blob_actions)
        and bool(blob_result.get("ok"))
        and bool(runtime_result.get("ok"))
        and bool(service_runtime_result.get("ok"))
    )
    summary = {
        "ok": ok,
        "seed": float(args.seed),
        "scope": selected_scope,
        "anchor_day": anchor_day,
        "expected_confirm": expected_confirm,
        "dry_run": bool(args.dry_run),
        "table_actions": actions,
        "blob_deletes": blob_actions,
        "engine_state_blob": blob_result,
        "runtime_overrides": runtime_result,
        "service_runtime_config": service_runtime_result,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
