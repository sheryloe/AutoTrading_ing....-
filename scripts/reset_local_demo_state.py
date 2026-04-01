from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import load_settings
from src.state import EngineState, save_state


def main() -> int:
    parser = argparse.ArgumentParser(description="Reset local demo engine state file (no Supabase).")
    parser.add_argument("--seed", type=float, default=0.0, help="Seed USDT (0 = use runtime DEMO_SEED_USDT)")
    parser.add_argument("--state-file", default="", help="Override state file path (default: settings.state_file)")
    args = parser.parse_args()

    settings = load_settings()
    seed = float(args.seed or settings.demo_seed_usdt)
    if seed <= 0:
        print(json.dumps({"ok": False, "error": "seed_must_be_positive"}, ensure_ascii=False, indent=2))
        return 2

    state_path = Path(args.state_file).expanduser() if str(args.state_file or "").strip() else Path(settings.state_file)
    state = EngineState(cash_usd=seed, demo_seed_usdt=seed)
    save_state(str(state_path), state)

    print(
        json.dumps(
            {"ok": True, "seed_usdt": seed, "state_file": str(state_path)},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

