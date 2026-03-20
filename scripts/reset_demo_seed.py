from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.state import EngineState, state_to_dict
from src.supabase_sync import SupabaseSyncClient


TABLE_FILTERS = {
    "positions": "id",
    "model_setups": "id",
    "daily_model_pnl": "day",
    "model_runtime_tunes": "model_id",
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Reset demo seed and clear demo tables in Supabase.")
    parser.add_argument("--seed", type=float, default=10000.0, help="Seed USDT value (default: 10000)")
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

    client = SupabaseSyncClient(url=url, secret_key=key, enabled=True, timeout_seconds=20)

    actions = []
    for table, filter_col in TABLE_FILTERS.items():
        if args.dry_run:
            result = {"ok": True, "dry_run": True}
        else:
            result = _delete_all(client, table, filter_col)
        actions.append({"table": table, "filter": f"{filter_col}=not.is.null", "result": result})

    engine_state = _build_engine_state(args.seed)
    if args.dry_run:
        blob_result = {"ok": True, "dry_run": True}
    else:
        blob_result = client.upsert_blob("engine_state", engine_state)

    ok = all(item.get("result", {}).get("ok") for item in actions) and bool(blob_result.get("ok"))
    summary = {
        "ok": ok,
        "seed": float(args.seed),
        "dry_run": bool(args.dry_run),
        "table_actions": actions,
        "engine_state_blob": blob_result,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
