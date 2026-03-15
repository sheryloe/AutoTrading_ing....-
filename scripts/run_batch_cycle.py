from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config import load_settings
from src.engine import TradingEngine
from src.supabase_sync import SupabaseSyncClient


def _hydrate_runtime_from_supabase() -> None:
    url = str(os.environ.get("SUPABASE_URL") or "").strip()
    secret_key = str(
        os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or ""
    ).strip()
    client = SupabaseSyncClient(
        url=url,
        secret_key=secret_key,
        enabled=bool(url and secret_key),
        timeout_seconds=15,
    )
    if not client.enabled:
        return

    runtime_result = client.fetch_blob("service_runtime_config")
    runtime_payload = runtime_result.get("payload") if bool(runtime_result.get("ok")) else None
    if isinstance(runtime_payload, dict) and runtime_payload:
        runtime_path = Path(str(os.environ.get("RUNTIME_SETTINGS_FILE") or "runtime_settings.json"))
        runtime_path.write_text(json.dumps(runtime_payload, ensure_ascii=True, indent=2), encoding="utf-8")

    master_key = str(os.environ.get("SERVICE_MASTER_KEY") or "").strip()
    if master_key:
        bybit_result = client.fetch_service_secret("bybit", master_key)
        bybit_payload = bybit_result.get("payload") if bool(bybit_result.get("ok")) else None
        if isinstance(bybit_payload, dict):
            os.environ["BYBIT_API_KEY"] = str(bybit_payload.get("api_key") or os.environ.get("BYBIT_API_KEY") or "")
            os.environ["BYBIT_API_SECRET"] = str(
                bybit_payload.get("api_secret") or os.environ.get("BYBIT_API_SECRET") or ""
            )


def main() -> int:
    started = int(time.time())
    _hydrate_runtime_from_supabase()
    engine = TradingEngine(load_settings())
    try:
        engine.run_cycle()
        engine._persist(force=True)  # noqa: SLF001
        payload = engine.dashboard_payload()
        summary = {
            "ok": True,
            "started_at": started,
            "finished_at": int(time.time()),
            "heartbeat": payload.get("last_cycle_ts"),
            "crypto_signals": len(list(payload.get("bybit_signal_rows") or [])),
            "open_positions": len(list(payload.get("bybit_positions") or [])),
            "daily_rows": len(list(payload.get("daily_pnl") or [])),
        }
        print(json.dumps(summary, ensure_ascii=True))
        return 0
    except Exception as exc:  # noqa: BLE001
        error_payload = {
            "ok": False,
            "started_at": started,
            "finished_at": int(time.time()),
            "error": str(exc),
        }
        print(json.dumps(error_payload, ensure_ascii=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
