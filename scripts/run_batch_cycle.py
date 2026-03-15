from __future__ import annotations

import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config import load_settings
from src.engine import TradingEngine


def main() -> int:
    started = int(time.time())
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
