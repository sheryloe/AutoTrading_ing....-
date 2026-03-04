from __future__ import annotations

import math
import json
import os
import shutil
import threading
import time
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.alerts import AlertManager
from src.config import Settings, load_settings, save_runtime_overrides, settings_to_public_dict
from src.data_sources import DexScreenerClient, MacroMarketClient, PumpFunClient, SolscanProClient, TrendCollector
from src.models import TokenSnapshot, TrendEvent
from src.online_model import OnlineModel, load_online_model, save_online_model
from src.providers import BybitV5Client, SolanaWalletTracker, TelegramBotClient
from src.state import EngineState, Position, Trade, load_state, save_state


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


MODEL_SPECS: dict[str, dict[str, str]] = {
    "A": {"name": "안정 추세 예측모델", "description": "신뢰형: 고신뢰 지표 중심 스윙"},
    "B": {"name": "흐름 추종 예측모델", "description": "트렌드형: 최근 이슈/소셜 추론 중심"},
    "C": {"name": "공격 모멘텀 예측모델", "description": "공격형: 빠른 진입/고위험 추론"},
}
MEME_MODEL_SPECS: dict[str, dict[str, str]] = {
    "A": {"name": "도그리 밈 선별모델", "description": "고품질 밈코인 선별 진입"},
    "B": {"name": "밈 장기홀딩 예측모델", "description": "장기홀딩(기본 14일) 중심 전략"},
    "C": {"name": "밈 단타 모멘텀모델", "description": "단타(빠른 회전) 중심 전략"},
}
CRYPTO_MODEL_SPECS: dict[str, dict[str, str]] = {
    "A": {"name": "크립토 안정 추세모델", "description": "신뢰형: 고신뢰 지표 중심 스윙"},
    "B": {"name": "크립토 흐름 추종모델", "description": "트렌드형: 최근 이슈/소셜 추론 중심"},
    "C": {"name": "동그리 크립토 모멘텀모델", "description": "공격형 모멘텀 전략"},
}
MODEL_IDS = ("A", "B", "C")
MODEL_AUTOTUNE_INTERVAL_SECONDS = 21600
MODEL_AUTOTUNE_MIN_CLOSED_TRADES = 8
MODEL_AUTOTUNE_LOOKBACK_TRADES = 80
RUN_TRADE_HISTORY_LIMIT = 9_999_999
RUN_TRADE_HISTORY_MAX_AGE_SECONDS = 60 * 60 * 24 * 190
STATE_BACKUP_INTERVAL_SECONDS = 600
STATE_BACKUP_MAX_FILES = 1000
TELEGRAM_POLL_LOCK_STALE_SECONDS = 120
MODEL_RUNTIME_TUNE_DEFAULTS: dict[str, dict[str, float]] = {
    # A: strict quality-first profile
    "A": {"threshold": 0.078, "tp_mul": 1.00, "sl_mul": 0.82},
    # B: balanced trend-flow profile
    "B": {"threshold": 0.052, "tp_mul": 1.24, "sl_mul": 1.02},
    # C: aggressive momentum profile
    "C": {"threshold": 0.058, "tp_mul": 1.44, "sl_mul": 1.18},
}
CRYPTO_MODEL_GATE_DEFAULTS: dict[str, dict[str, Any]] = {
    "A": {"rank_max": 150, "trend_stack_min": 0.14, "overheat_max": 0.55, "smallcap_trend_only": False},
    "B": {"rank_max": 450, "trend_stack_min": -0.10, "overheat_max": 0.80, "smallcap_trend_only": False},
    "C": {"rank_max": 500, "trend_stack_min": -0.08, "overheat_max": 0.90, "smallcap_trend_only": True},
}

NON_MEME_SYMBOLS = {
    "USDC",
    "USDT",
    "DAI",
    "FDUSD",
    "TUSD",
    "PYUSD",
    "USDE",
    "SOL",
    "BTC",
    "ETH",
    "BNB",
    "XRP",
    "ADA",
    "AVAX",
    "DOT",
    "LINK",
    "MATIC",
    "OP",
    "ARB",
    "UNI",
    "LDO",
    "RAY",
    "JUP",
    "AAVE",
    "SUI",
    "SEI",
    "TRX",
}

KNOWN_MEME_SYMBOLS = {
    "BONK",
    "WIF",
    "PEPE",
    "FLOKI",
    "BOME",
    "POPCAT",
    "DOGE",
    "SHIB",
    "MEW",
    "MYRO",
    "PONKE",
    "GIGA",
    "MOTHER",
    "TOSHI",
    "MOG",
    "WOJAK",
    "BRETT",
    "TURBO",
    "PNUT",
    "ACT",
}

MEME_HINT_WORDS = (
    "meme",
    "doge",
    "inu",
    "cat",
    "frog",
    "bonk",
    "wif",
    "pepe",
    "floki",
    "bome",
    "popcat",
    "shib",
    "pump",
    "degen",
    "moon",
    "ape",
)

NON_MEME_NAME_WORDS = (
    "usd coin",
    "tether",
    "wrapped bitcoin",
    "wrapped ether",
    "ethereum",
    "bitcoin",
    "solana",
    "liquid staking",
)


class TradingEngine:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()
        self._enforce_paper_lock()
        self.state: EngineState = load_state(self.settings.state_file, self.settings.paper_start_cash_usd)
        if self.state.cash_usd <= 0:
            self.state.cash_usd = float(self.settings.paper_start_cash_usd)
        if self.state.demo_seed_usdt <= 0:
            self.state.demo_seed_usdt = float(self.settings.demo_seed_usdt)

        self.model: OnlineModel = load_online_model(self.settings.model_file)
        self.dex = DexScreenerClient()
        self.pumpfun = PumpFunClient()
        self.macro = MacroMarketClient()
        self.solscan = SolscanProClient(
            api_key=self.settings.solscan_api_key,
            monthly_cu_limit=self.settings.solscan_monthly_cu_limit,
            cu_per_request=self.settings.solscan_cu_per_request,
            budget_window_seconds=self.settings.solscan_budget_window_seconds,
            permission_backoff_seconds=self.settings.solscan_permission_backoff_seconds,
        )
        self.trend = TrendCollector(
            coingecko_api_key=self.settings.coingecko_api_key,
            solscan_api_key=self.settings.solscan_api_key,
            solana_rpc_url=self.settings.solana_rpc_url,
            solscan_monthly_cu_limit=self.settings.solscan_monthly_cu_limit,
            solscan_cu_per_request=self.settings.solscan_cu_per_request,
            solscan_budget_window_seconds=self.settings.solscan_budget_window_seconds,
            solscan_permission_backoff_seconds=self.settings.solscan_permission_backoff_seconds,
            google_api_key=self.settings.google_api_key,
            google_model=self.settings.google_model,
            google_trend_enabled=self.settings.google_trend_enabled,
            google_trend_interval_seconds=self.settings.google_trend_interval_seconds,
            google_trend_cooldown_seconds=self.settings.google_trend_cooldown_seconds,
            google_trend_max_symbols=self.settings.google_trend_max_symbols,
        )
        self.wallet = SolanaWalletTracker(self.settings.solana_rpc_url)
        self.bybit = BybitV5Client(
            self.settings.bybit_api_key,
            self.settings.bybit_api_secret,
            self.settings.bybit_base_url,
            self.settings.bybit_recv_window,
        )
        self.alert_manager = AlertManager(self.settings.telegram_bot_token, self.settings.telegram_chat_id)
        self.telegram = TelegramBotClient(self.settings.telegram_bot_token)

        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._telegram_thread: threading.Thread | None = None
        self._running = False
        self._last_prices: dict[str, float] = {}
        self._bybit_last_prices: dict[str, float] = {}
        self._bybit_price_history: dict[str, list[float]] = {}
        self._macro_meta: dict[str, dict[str, Any]] = {}
        self._macro_trend_pool: list[str] = []
        self._macro_trend_pool_next_refresh_ts = 0
        self._wallet_pattern_cache: dict[str, dict[str, Any]] = {}
        self._focus_wallet_analysis: dict[str, Any] = {}
        self._trend_source_status: dict[str, Any] = {}
        self._trend_cache_trending: set[str] = set()
        self._trend_cache_events: dict[str, list[TrendEvent]] = {}
        self._trend_next_fetch_ts: dict[str, int] = {}
        self._new_meme_feed: list[dict[str, Any]] = []
        self._last_wallet_sync = 0
        self._last_bybit_sync = 0
        self._last_telegram_poll = 0
        self._last_telegram_report = 0
        self._runtime_error_notice: dict[str, dict[str, Any]] = {}
        self._last_state_backup_ts = 0
        self._telegram_poll_lock_path = Path("reports") / "telegram_poll.lock"

        self._ensure_model_runs()
        self._sync_primary_views_from_model_a()

    @property
    def running(self) -> bool:
        return self._running

    def _reload_settings(self) -> None:
        latest = load_settings()
        self.settings = latest
        self._enforce_paper_lock()
        self.wallet = SolanaWalletTracker(self.settings.solana_rpc_url)
        self.pumpfun = PumpFunClient()
        self.solscan = SolscanProClient(
            api_key=self.settings.solscan_api_key,
            monthly_cu_limit=self.settings.solscan_monthly_cu_limit,
            cu_per_request=self.settings.solscan_cu_per_request,
            budget_window_seconds=self.settings.solscan_budget_window_seconds,
            permission_backoff_seconds=self.settings.solscan_permission_backoff_seconds,
        )
        if not isinstance(getattr(self, "trend", None), TrendCollector):
            self.trend = TrendCollector(
                coingecko_api_key=self.settings.coingecko_api_key,
                solscan_api_key=self.settings.solscan_api_key,
                solana_rpc_url=self.settings.solana_rpc_url,
                solscan_monthly_cu_limit=self.settings.solscan_monthly_cu_limit,
                solscan_cu_per_request=self.settings.solscan_cu_per_request,
                solscan_budget_window_seconds=self.settings.solscan_budget_window_seconds,
                solscan_permission_backoff_seconds=self.settings.solscan_permission_backoff_seconds,
                google_api_key=self.settings.google_api_key,
                google_model=self.settings.google_model,
                google_trend_enabled=self.settings.google_trend_enabled,
                google_trend_interval_seconds=self.settings.google_trend_interval_seconds,
                google_trend_cooldown_seconds=self.settings.google_trend_cooldown_seconds,
                google_trend_max_symbols=self.settings.google_trend_max_symbols,
            )
        else:
            self.trend.coingecko_api_key = str(self.settings.coingecko_api_key or "").strip()
            self.trend.solscan_api_key = str(self.settings.solscan_api_key or "").strip()
            self.trend.solana_rpc_url = str(self.settings.solana_rpc_url or "").strip()
            self.trend.solscan = SolscanProClient(
                api_key=self.settings.solscan_api_key,
                monthly_cu_limit=self.settings.solscan_monthly_cu_limit,
                cu_per_request=self.settings.solscan_cu_per_request,
                budget_window_seconds=self.settings.solscan_budget_window_seconds,
                permission_backoff_seconds=self.settings.solscan_permission_backoff_seconds,
            )
            self.trend.google_api_key = str(self.settings.google_api_key or "").strip()
            self.trend.google_model = str(self.settings.google_model or "gemini-2.5-flash").strip()
            self.trend.google_trend_enabled = bool(self.settings.google_trend_enabled)
            self.trend.google_trend_interval_seconds = max(60, int(self.settings.google_trend_interval_seconds))
            self.trend.google_trend_cooldown_seconds = max(60, int(self.settings.google_trend_cooldown_seconds))
            self.trend.google_trend_max_symbols = max(5, min(40, int(self.settings.google_trend_max_symbols)))
        self.bybit = BybitV5Client(
            self.settings.bybit_api_key,
            self.settings.bybit_api_secret,
            self.settings.bybit_base_url,
            self.settings.bybit_recv_window,
        )
        self.alert_manager = AlertManager(self.settings.telegram_bot_token, self.settings.telegram_chat_id)
        self.telegram = TelegramBotClient(self.settings.telegram_bot_token)

    def _enforce_paper_lock(self) -> None:
        if not getattr(self.settings, "lock_paper_mode", False):
            return
        updates: dict[str, Any] = {}
        if str(self.settings.trade_mode).lower() != "paper":
            updates["TRADE_MODE"] = "paper"
        if bool(self.settings.enable_live_execution):
            updates["ENABLE_LIVE_EXECUTION"] = False
        if updates:
            save_runtime_overrides(self.settings, updates)
            self.settings = load_settings()

    def _persist(self) -> None:
        with self._lock:
            save_state(self.settings.state_file, self.state)
            self._backup_state_file("auto", force=False)
            save_online_model(self.settings.model_file, self.model)

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        if os.name == "nt":
            try:
                import ctypes

                PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
                if handle:
                    ctypes.windll.kernel32.CloseHandle(handle)
                    return True
                return False
            except Exception:
                return False
        try:
            os.kill(int(pid), 0)
            return True
        except OSError:
            return False
        except Exception:
            return False

    def _acquire_telegram_poll_lock(self, now_ts: int) -> bool:
        path = self._telegram_poll_lock_path
        path.parent.mkdir(parents=True, exist_ok=True)
        me = int(os.getpid())
        payload = {"pid": me, "ts": int(now_ts)}
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=True))
            return True
        except FileExistsError:
            pass
        except Exception:
            return False

        holder_pid = 0
        holder_ts = 0
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            holder_pid = int(raw.get("pid") or 0)
            holder_ts = int(raw.get("ts") or 0)
        except Exception:
            holder_pid = 0
            holder_ts = 0

        if holder_pid == me:
            try:
                path.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
                return True
            except Exception:
                return False

        is_stale = (int(now_ts) - int(holder_ts)) > TELEGRAM_POLL_LOCK_STALE_SECONDS
        if holder_pid > 0 and self._pid_alive(holder_pid) and not is_stale:
            return False

        try:
            path.unlink(missing_ok=True)
        except Exception:
            return False
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=True))
            return True
        except Exception:
            return False

    def _release_telegram_poll_lock(self) -> None:
        path = self._telegram_poll_lock_path
        me = int(os.getpid())
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if int(raw.get("pid") or 0) != me:
                return
        except Exception:
            return
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass

    def _backup_state_file(self, reason: str, force: bool = False) -> str:
        now = int(time.time())
        if not force and (now - int(self._last_state_backup_ts)) < STATE_BACKUP_INTERVAL_SECONDS:
            return ""
        src = Path(self.settings.state_file)
        if not src.exists():
            return ""
        backup_dir = Path("reports") / "state_backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        safe_reason = "".join(ch if ch.isalnum() else "_" for ch in str(reason or "auto")).strip("_") or "auto"
        safe_reason = safe_reason[:24]
        dst = backup_dir / f"state_{stamp}_{safe_reason}.json"
        try:
            shutil.copy2(src, dst)
            self._last_state_backup_ts = now
            files = sorted(backup_dir.glob("state_*.json"), key=lambda p: p.stat().st_mtime)
            if len(files) > STATE_BACKUP_MAX_FILES:
                for old in files[: len(files) - STATE_BACKUP_MAX_FILES]:
                    try:
                        old.unlink()
                    except Exception:
                        pass
            return str(dst)
        except Exception:
            return ""

    def _blank_model_run(self, model_id: str, seed_usdt: float) -> dict[str, Any]:
        spec = MODEL_SPECS.get(model_id, {"name": f"{model_id}-Model", "description": ""})
        seed = max(50.0, float(seed_usdt))
        bybit_seed = seed if self.settings.demo_enable_macro else 0.0
        row = {
            "model_id": model_id,
            "model_name": spec["name"],
            "model_description": spec["description"],
            "meme_seed_usd": seed,
            "bybit_seed_usd": bybit_seed,
            "meme_cash_usd": seed,
            "bybit_cash_usd": bybit_seed,
            "meme_positions": {},
            "bybit_positions": {},
            "trades": [],
            "latest_signals": [],
            "latest_crypto_signals": [],
            "last_signal_ts": {},
            "market_profile_ver": 1,
            "started_at": int(time.time()),
        }
        self._ensure_model_runtime_tune(row, model_id, int(time.time()))
        return row

    @staticmethod
    def _market_model_spec(market: str, model_id: str) -> dict[str, str]:
        market_id = "meme" if str(market).lower() == "meme" else "crypto"
        table = MEME_MODEL_SPECS if market_id == "meme" else CRYPTO_MODEL_SPECS
        if model_id in table:
            return dict(table[model_id])
        return dict(MODEL_SPECS.get(model_id) or {"name": model_id, "description": ""})

    @classmethod
    def _market_model_name(cls, market: str, model_id: str) -> str:
        return str(cls._market_model_spec(market, model_id).get("name") or model_id)

    def _display_model_name(self, model_id: str, market: str | None = None) -> str:
        market_id = str(market or "").lower().strip()
        if market_id in {"meme", "crypto"}:
            return self._market_model_name(market_id, model_id)
        return str(MODEL_SPECS.get(model_id, {}).get("name") or model_id)

    @staticmethod
    def _meme_strategy_mode_for_model(model_id: str) -> str:
        if model_id == "B":
            return "long_hold"
        if model_id == "C":
            return "scalp"
        return "quality_hybrid"

    def _migrate_market_model_profile(self, run: dict[str, Any], model_id: str, now_ts: int) -> None:
        version = int(run.get("market_profile_ver") or 0)
        if version >= 1:
            return
        meme_positions = dict(run.get("meme_positions") or {})
        if model_id == "B":
            hold_days = max(14, int(self.settings.meme_swing_hold_days))
            hold_window = int(hold_days) * 86400
            for token, pos in meme_positions.items():
                row = dict(pos or {})
                row["strategy"] = "swing"
                row["trailing_stop_pct"] = float(
                    row.get("trailing_stop_pct") or self.settings.meme_swing_trailing_stop_pct
                )
                if int(row.get("hold_until_ts") or 0) <= 0:
                    opened = int(row.get("opened_at") or now_ts)
                    row["hold_until_ts"] = opened + hold_window
                meme_positions[token] = row
        elif model_id == "C":
            for token, pos in meme_positions.items():
                row = dict(pos or {})
                row["strategy"] = "scalp"
                row["hold_until_ts"] = 0
                row["trailing_stop_pct"] = 0.0
                meme_positions[token] = row
        run["meme_positions"] = meme_positions
        run["market_profile_ver"] = 1

    @staticmethod
    def _market_run_key(market: str, model_id: str) -> str:
        market_id = "meme" if str(market).lower() == "meme" else "crypto"
        return f"{market_id}_{model_id}"

    def _blank_market_run(self, market: str, model_id: str, seed_usdt: float) -> dict[str, Any]:
        row = self._blank_model_run(model_id, seed_usdt)
        self._normalize_market_run(row, market, model_id, seed_usdt)
        return row

    def _normalize_market_run(
        self,
        run: dict[str, Any],
        market: str,
        model_id: str,
        seed_usdt: float,
    ) -> None:
        market_id = "meme" if str(market).lower() == "meme" else "crypto"
        seed = max(50.0, float(seed_usdt))
        run["model_id"] = model_id
        run["market_id"] = market_id
        run["model_name"] = self._market_model_name(market_id, model_id)
        run["model_description"] = self._market_model_spec(market_id, model_id).get("description", "")
        run.setdefault("trades", [])
        run.setdefault("market_profile_ver", 1)
        run.setdefault("started_at", int(time.time()))
        run.setdefault("last_entry_alloc", {})

        if market_id == "meme":
            run.setdefault("meme_seed_usd", seed)
            run.setdefault("meme_cash_usd", seed)
            run.setdefault("meme_positions", {})
            run.setdefault("latest_signals", [])
            run.setdefault("last_signal_ts", {})
            run["bybit_seed_usd"] = 0.0
            run["bybit_cash_usd"] = 0.0
            run["bybit_positions"] = {}
            run["latest_crypto_signals"] = []
            self._migrate_market_model_profile(run, model_id, int(time.time()))
        else:
            bybit_seed = seed if self.settings.demo_enable_macro else 0.0
            run.setdefault("bybit_seed_usd", bybit_seed)
            run.setdefault("bybit_cash_usd", bybit_seed)
            run.setdefault("bybit_positions", {})
            run.setdefault("latest_crypto_signals", [])
            run["meme_seed_usd"] = 0.0
            run["meme_cash_usd"] = 0.0
            run["meme_positions"] = {}
            run["latest_signals"] = []
            run["last_signal_ts"] = {}
            self._ensure_model_runtime_tune(run, model_id, int(time.time()))
            if not self.settings.demo_enable_macro:
                run["bybit_seed_usd"] = 0.0
                run["bybit_cash_usd"] = 0.0
                run["bybit_positions"] = {}

    @staticmethod
    def _filter_market_trades(rows: list[dict[str, Any]], market: str) -> list[dict[str, Any]]:
        market_id = "meme" if str(market).lower() == "meme" else "crypto"
        out: list[dict[str, Any]] = []
        for row in list(rows or []):
            source = str((row or {}).get("source") or "").lower()
            if market_id == "meme":
                if source == "memecoin":
                    out.append(dict(row))
            else:
                if source != "memecoin":
                    out.append(dict(row))
        out.sort(key=lambda r: int((r or {}).get("ts") or 0))
        return out

    def _split_legacy_run_to_market_run(
        self,
        legacy: dict[str, Any],
        market: str,
        model_id: str,
        seed_usdt: float,
    ) -> dict[str, Any]:
        market_id = "meme" if str(market).lower() == "meme" else "crypto"
        row = self._blank_market_run(market_id, model_id, seed_usdt)
        if market_id == "meme":
            row["meme_seed_usd"] = float(legacy.get("meme_seed_usd") or seed_usdt)
            row["meme_cash_usd"] = float(legacy.get("meme_cash_usd") or row["meme_seed_usd"])
            row["meme_positions"] = dict(legacy.get("meme_positions") or {})
            row["latest_signals"] = list(legacy.get("latest_signals") or [])
            row["last_signal_ts"] = dict(legacy.get("last_signal_ts") or {})
            last_entry = dict(legacy.get("last_entry_alloc") or {})
            row["last_entry_alloc"] = {"meme": dict(last_entry.get("meme") or {})}
            row["trades"] = self._filter_market_trades(list(legacy.get("trades") or []), "meme")
            row["started_at"] = int(legacy.get("started_at") or row.get("started_at") or int(time.time()))
            row["market_profile_ver"] = int(legacy.get("market_profile_ver") or row.get("market_profile_ver") or 1)
            self._migrate_market_model_profile(row, model_id, int(time.time()))
        else:
            row["bybit_seed_usd"] = float(legacy.get("bybit_seed_usd") or (seed_usdt if self.settings.demo_enable_macro else 0.0))
            row["bybit_cash_usd"] = float(legacy.get("bybit_cash_usd") or row["bybit_seed_usd"])
            row["bybit_positions"] = dict(legacy.get("bybit_positions") or {})
            row["latest_crypto_signals"] = list(legacy.get("latest_crypto_signals") or [])
            last_entry = dict(legacy.get("last_entry_alloc") or {})
            row["last_entry_alloc"] = {"crypto": dict(last_entry.get("crypto") or {})}
            row["trades"] = self._filter_market_trades(list(legacy.get("trades") or []), "crypto")
            row["started_at"] = int(legacy.get("started_at") or row.get("started_at") or int(time.time()))
            raw_tune = dict(legacy.get("model_runtime_tune") or {})
            if raw_tune:
                row["model_runtime_tune"] = dict(raw_tune)
            if model_id == "B" and isinstance(legacy.get("b_runtime_tune"), dict):
                row["b_runtime_tune"] = dict(legacy.get("b_runtime_tune") or {})
            self._ensure_model_runtime_tune(row, model_id, int(time.time()))
        return row

    def _compose_model_run_from_market(self, runs: dict[str, Any], model_id: str) -> dict[str, Any]:
        seed = max(50.0, float(self.state.demo_seed_usdt or self.settings.demo_seed_usdt))
        meme_key = self._market_run_key("meme", model_id)
        crypto_key = self._market_run_key("crypto", model_id)
        meme_run = runs.get(meme_key) if isinstance(runs.get(meme_key), dict) else {}
        crypto_run = runs.get(crypto_key) if isinstance(runs.get(crypto_key), dict) else {}
        row = self._blank_model_run(model_id, seed)
        row["model_name"] = MODEL_SPECS.get(model_id, {}).get("name", model_id)
        row["model_description"] = MODEL_SPECS.get(model_id, {}).get("description", "")
        row["meme_seed_usd"] = float(meme_run.get("meme_seed_usd") or seed)
        row["meme_cash_usd"] = float(meme_run.get("meme_cash_usd") or 0.0)
        row["meme_positions"] = dict(meme_run.get("meme_positions") or {})
        row["latest_signals"] = list(meme_run.get("latest_signals") or [])
        row["last_signal_ts"] = dict(meme_run.get("last_signal_ts") or {})
        row["bybit_seed_usd"] = float(crypto_run.get("bybit_seed_usd") or (seed if self.settings.demo_enable_macro else 0.0))
        row["bybit_cash_usd"] = float(crypto_run.get("bybit_cash_usd") or 0.0)
        row["bybit_positions"] = dict(crypto_run.get("bybit_positions") or {})
        row["latest_crypto_signals"] = list(crypto_run.get("latest_crypto_signals") or [])
        combined_trades = list(meme_run.get("trades") or []) + list(crypto_run.get("trades") or [])
        combined_trades.sort(key=lambda r: int((r or {}).get("ts") or 0))
        row["trades"] = combined_trades[-RUN_TRADE_HISTORY_LIMIT:]
        last_entry_alloc: dict[str, Any] = {}
        meme_entry = dict((meme_run.get("last_entry_alloc") or {}).get("meme") or {})
        crypto_entry = dict((crypto_run.get("last_entry_alloc") or {}).get("crypto") or {})
        if meme_entry:
            last_entry_alloc["meme"] = meme_entry
        if crypto_entry:
            last_entry_alloc["crypto"] = crypto_entry
        row["last_entry_alloc"] = last_entry_alloc
        tune_raw = dict(crypto_run.get("model_runtime_tune") or {})
        if tune_raw:
            row["model_runtime_tune"] = tune_raw
        if model_id == "B":
            row["b_runtime_tune"] = dict(crypto_run.get("b_runtime_tune") or {})
        return row

    def _get_market_run(self, runs: dict[str, Any], market: str, model_id: str) -> dict[str, Any]:
        key = self._market_run_key(market, model_id)
        row = runs.get(key)
        return row if isinstance(row, dict) else {}

    @staticmethod
    def _model_tune_clamps(model_id: str) -> dict[str, tuple[float, float]]:
        if model_id == "A":
            return {"threshold": (0.072, 0.098), "tp_mul": (0.90, 1.20), "sl_mul": (0.68, 0.98)}
        if model_id == "B":
            return {"threshold": (0.044, 0.090), "tp_mul": (1.05, 1.50), "sl_mul": (0.82, 1.22)}
        return {"threshold": (0.048, 0.080), "tp_mul": (1.12, 1.90), "sl_mul": (0.95, 1.55)}

    def _read_model_runtime_tune_from_run(self, run: dict[str, Any], model_id: str, now_ts: int) -> dict[str, Any]:
        started = int(run.get("started_at") or now_ts)
        defaults = dict(MODEL_RUNTIME_TUNE_DEFAULTS.get(model_id) or MODEL_RUNTIME_TUNE_DEFAULTS["B"])
        all_raw = dict(run.get("model_runtime_tune") or {})
        raw = dict(all_raw.get(model_id) or {})
        # Backward compatibility: migrate legacy B runtime tune.
        if model_id == "B" and not raw:
            raw = dict(run.get("b_runtime_tune") or {})
        clamps = self._model_tune_clamps(model_id)
        threshold = _clamp(
            float(raw.get("threshold") or defaults.get("threshold") or 0.070),
            float(clamps["threshold"][0]),
            float(clamps["threshold"][1]),
        )
        # If B-crypto has no closed samples yet, keep entry threshold relaxed to avoid zero-trade deadlock.
        if model_id == "B" and int(raw.get("last_eval_closed") or 0) <= 0:
            threshold = min(float(threshold), float(defaults.get("threshold") or threshold))
        tp_mul = _clamp(
            float(raw.get("tp_mul") or defaults.get("tp_mul") or 1.20),
            float(clamps["tp_mul"][0]),
            float(clamps["tp_mul"][1]),
        )
        sl_mul = _clamp(
            float(raw.get("sl_mul") or defaults.get("sl_mul") or 1.00),
            float(clamps["sl_mul"][0]),
            float(clamps["sl_mul"][1]),
        )
        last_eval_ts = int(raw.get("last_eval_ts") or 0)
        next_eval_ts = int(raw.get("next_eval_ts") or 0)
        min_next_eval = int((last_eval_ts if last_eval_ts > 0 else started) + MODEL_AUTOTUNE_INTERVAL_SECONDS)
        if next_eval_ts <= 0:
            next_eval_ts = int(min_next_eval)
        elif next_eval_ts < min_next_eval:
            next_eval_ts = int(min_next_eval)
        return {
            "model_id": str(model_id),
            "threshold": float(threshold),
            "tp_mul": float(tp_mul),
            "sl_mul": float(sl_mul),
            "last_eval_ts": int(last_eval_ts),
            "next_eval_ts": int(next_eval_ts),
            "last_eval_closed": int(raw.get("last_eval_closed") or 0),
            "last_eval_win_rate": float(raw.get("last_eval_win_rate") or 0.0),
            "last_eval_pnl_usd": float(raw.get("last_eval_pnl_usd") or 0.0),
            "last_eval_pf": float(raw.get("last_eval_pf") or 0.0),
            "last_eval_note": str(raw.get("last_eval_note") or ""),
        }

    def _ensure_model_runtime_tune(self, run: dict[str, Any], model_id: str, now_ts: int | None = None) -> dict[str, Any]:
        now = int(now_ts or int(time.time()))
        tune = self._read_model_runtime_tune_from_run(run, model_id, now)
        all_raw = dict(run.get("model_runtime_tune") or {})
        all_raw[model_id] = dict(tune)
        run["model_runtime_tune"] = dict(all_raw)
        if model_id == "B":
            run["b_runtime_tune"] = dict(tune)
        return tune

    # Backward-compatible wrappers for B references.
    def _read_b_runtime_tune_from_run(self, run: dict[str, Any], now_ts: int) -> dict[str, Any]:
        return self._read_model_runtime_tune_from_run(run, "B", now_ts)

    def _ensure_b_runtime_tune(self, run: dict[str, Any], now_ts: int | None = None) -> dict[str, Any]:
        return self._ensure_model_runtime_tune(run, "B", now_ts)

    def _ensure_model_runs(self) -> None:
        with self._lock:
            if not isinstance(self.state.model_runs, dict):
                self.state.model_runs = {}
            seed = max(50.0, float(self.state.demo_seed_usdt or self.settings.demo_seed_usdt))
            runs = self.state.model_runs
            # One-time migration from legacy combined A/B/C run layout to market-split runs.
            for model_id in MODEL_IDS:
                legacy = runs.get(model_id)
                if isinstance(legacy, dict):
                    if not isinstance(runs.get(f"legacy_{model_id}"), dict):
                        runs[f"legacy_{model_id}"] = deepcopy(legacy)
                    # Enforce 6-run split mode: remove legacy combined active key.
                    runs.pop(model_id, None)
                else:
                    legacy = runs.get(f"legacy_{model_id}")
                for market in ("meme", "crypto"):
                    key = self._market_run_key(market, model_id)
                    row = runs.get(key)
                    if not isinstance(row, dict):
                        if isinstance(legacy, dict):
                            row = self._split_legacy_run_to_market_run(legacy, market, model_id, seed)
                        else:
                            row = self._blank_market_run(market, model_id, seed)
                        runs[key] = row
                    self._normalize_market_run(row, market, model_id, seed)
            self.state.model_runs = runs

    def reset_demo(self, seed_usdt: float | None = None, confirm_text: str = "", actor: str = "manual") -> dict[str, Any]:
        if not bool(self.settings.allow_demo_reset):
            raise PermissionError("데모 초기화 잠금 상태입니다. ALLOW_DEMO_RESET=true 후 다시 시도하세요.")
        if str(confirm_text or "").strip().upper() != "RESET DEMO":
            raise ValueError("초기화 확인 문구가 필요합니다: RESET DEMO")
        seed = max(50.0, float(seed_usdt if seed_usdt is not None else self.settings.demo_seed_usdt))
        backup_path = ""
        with self._lock:
            # Keep a recoverable snapshot before wiping positions/trades.
            save_state(self.settings.state_file, self.state)
            backup_path = self._backup_state_file(f"pre_reset_{actor}", force=True)
            self.state.demo_seed_usdt = seed
            next_runs: dict[str, Any] = {}
            for mid in MODEL_IDS:
                next_runs[self._market_run_key("meme", mid)] = self._blank_market_run("meme", mid, seed)
                next_runs[self._market_run_key("crypto", mid)] = self._blank_market_run("crypto", mid, seed)
            self.state.model_runs = next_runs
            self.state.daily_pnl = []
            self.state.latest_signals = []
            self.state.trend_events = []
            self.state.positions = {}
            self.state.trades = []
            self.state.cash_usd = seed
            self.state.last_signal_ts = {}
            self._focus_wallet_analysis = {}
        save_runtime_overrides(self.settings, {"DEMO_SEED_USDT": seed})
        self._reload_settings()
        self._sync_primary_views_from_model_a()
        self._persist()
        suffix = (
            f"+ Macro Top {int(self.settings.macro_top_n)} ({self.settings.macro_universe_source})"
            if self.settings.demo_enable_macro
            else "(Macro 데모 OFF)"
        )
        self._push_alert(
            "info",
            "데모 초기화",
            f"밈 3개 + 크립토 3개 예측모델 각각 시드 {int(seed)} {suffix} | backup={backup_path or '-'}",
            send_telegram=True,
        )
        return {"seed_usdt": seed, "models": list(MODEL_IDS), "backup_path": backup_path}

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="trade-engine", daemon=True)
        self._thread.start()
        self._telegram_thread = threading.Thread(target=self._telegram_loop, name="telegram-poll", daemon=True)
        self._telegram_thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        if self._telegram_thread and self._telegram_thread.is_alive():
            self._telegram_thread.join(timeout=3)
        self._thread = None
        self._telegram_thread = None
        self._release_telegram_poll_lock()
        self._persist()

    def restart(self) -> None:
        self.stop()
        self.start()

    def set_trade_mode(self, mode: str) -> None:
        normalized = "live" if str(mode).lower() == "live" else "paper"
        if self.settings.lock_paper_mode:
            normalized = "paper"
        save_runtime_overrides(self.settings, {"TRADE_MODE": normalized})
        self._reload_settings()

    def set_autotrade(self, enabled: bool) -> None:
        save_runtime_overrides(self.settings, {"ENABLE_AUTOTRADE": bool(enabled)})
        self._reload_settings()

    def set_demo_reset_enabled(self, enabled: bool) -> None:
        save_runtime_overrides(self.settings, {"ALLOW_DEMO_RESET": bool(enabled)})
        self._reload_settings()

    def set_telegram_trade_alerts(self, enabled: bool) -> None:
        save_runtime_overrides(self.settings, {"TELEGRAM_TRADE_ALERTS_ENABLED": bool(enabled)})
        self._reload_settings()

    def set_telegram_report(self, enabled: bool) -> None:
        save_runtime_overrides(self.settings, {"TELEGRAM_REPORT_ENABLED": bool(enabled)})
        self._reload_settings()

    def force_sync(self) -> None:
        now = int(time.time())
        self._sync_wallet(now, force=True)
        self._sync_bybit(now, force=True)
        self._persist()

    def close_all_memecoin_positions(self, reason: str = "manual_close_all") -> dict[str, Any]:
        summary: dict[str, dict[str, int]] = {}
        with self._lock:
            runs = {mid: self._get_market_run(self.state.model_runs or {}, "meme", mid) for mid in MODEL_IDS}
        for model_id, run in runs.items():
            closed = 0
            failed = 0
            for pos in list((run.get("meme_positions") or {}).values()):
                token_address = str(pos.get("token_address") or "")
                price = self._resolve_price(token_address)
                if price <= 0:
                    failed += 1
                    continue
                if self._close_model_memecoin_position(model_id, run, pos, price, reason):
                    closed += 1
            summary[model_id] = {"closed": closed, "failed": failed}
        self._sync_primary_views_from_model_a()
        self._persist()
        return {"models": summary}

    def _push_alert(self, level: str, title: str, text: str, send_telegram: bool = False) -> None:
        row = self.alert_manager.make_alert_row(level, title, text)
        with self._lock:
            self.state.alerts.append(row)
            self.state.alerts = self.state.alerts[-500:]
        allow_telegram = bool(send_telegram)
        if str(level or "").lower() == "trade" and not bool(self.settings.telegram_trade_alerts_enabled):
            allow_telegram = False
        if allow_telegram and self.alert_manager.enabled:
            self.alert_manager.send_telegram(f"[{title}] {text}")

    @staticmethod
    def _translate_error_to_korean(raw_error: str) -> str:
        raw = str(raw_error or "").strip()
        if not raw:
            return ""
        compact = " ".join(raw.replace("\r", " ").replace("\n", " ").split())
        compact = compact[:320] + ("..." if len(compact) > 320 else "")
        low = compact.lower()
        if "solscan_window_cu_exceeded" in low:
            reason = "Solscan 5분 CU 예산을 모두 사용했습니다. 다음 윈도우에서 자동 재시도합니다."
        elif "solscan_monthly_cu_exceeded" in low:
            reason = "Solscan 월간 CU 한도를 모두 사용했습니다. 다음 달까지 Solscan 호출을 중단합니다."
        elif "solscan_permission_level_insufficient" in low:
            reason = "Solscan 키 권한이 현재 엔드포인트 등급보다 낮습니다. 무료 플랜 허용 엔드포인트로 제한됩니다."
        elif "solscan_permission_backoff" in low:
            reason = "Solscan 권한 백오프 구간입니다. 잠시 후 자동 재시도합니다."
        elif "solscan_rate_limit_backoff" in low or "solscan_rate_limited" in low:
            reason = "Solscan API 속도 제한입니다. 잠시 후 자동 재시도합니다."
        elif "429" in low or "too many requests" in low or "rate-limited" in low:
            reason = "요청 한도 초과(429)입니다. 자동으로 대기 후 재시도합니다."
        elif "409" in low or "conflict" in low:
            reason = "텔레그램 폴링 충돌(409)입니다. 동일 봇을 다른 프로세스가 동시에 polling 중입니다."
        elif "failed to resolve" in low or "name or service not known" in low or "nameresolutionerror" in low:
            reason = "도메인 DNS 해석에 실패했습니다. 소스 주소 또는 네트워크를 확인해야 합니다."
        elif "empty_feed" in low:
            reason = "RSS 피드가 비어 있거나 접근 제한되었습니다."
        elif "max retries exceeded" in low or "connection" in low and "failed" in low:
            reason = "원격 API 연결에 실패했습니다."
        elif "timeout" in low or "timed out" in low:
            reason = "API 요청 시간이 초과되었습니다."
        elif "invalid_api_key" in low or "api key not valid" in low or "permission denied" in low:
            reason = "API 키가 유효하지 않거나 권한이 부족합니다."
        elif "110007" in low or "not enough for new order" in low or "not enough hold money" in low:
            reason = "잔고 부족으로 신규 주문이 거절되었습니다."
        elif "custom(6024)" in low or "0x1788" in low:
            reason = "스왑 시뮬레이션이 실패했습니다. 유동성/슬리피지/라우팅 상태를 확인해야 합니다."
        else:
            reason = "실행 중 오류가 발생했습니다."
        return f"{reason} detail={compact}"

    def _emit_runtime_error(
        self,
        key: str,
        title: str,
        raw_error: str,
        *,
        level: str = "error",
        cooldown_seconds: int = 300,
    ) -> None:
        text = self._translate_error_to_korean(raw_error)
        if not text:
            return
        now = int(time.time())
        prev = dict(self._runtime_error_notice.get(key) or {})
        prev_text = str(prev.get("text") or "")
        prev_ts = int(prev.get("ts") or 0)
        if prev_text == text and (now - prev_ts) < max(30, int(cooldown_seconds)):
            return
        self._runtime_error_notice[key] = {"text": text, "ts": now}
        self._push_alert(level, title, text, send_telegram=True)

    def _scan_and_notify_runtime_errors(self) -> None:
        with self._lock:
            memecoin_error = str(self.state.memecoin_error or "")
            bybit_error = str(self.state.bybit_error or "")
            trend_status = dict(self._trend_source_status or {})
        if memecoin_error:
            self._emit_runtime_error("core:memecoin", "밈코인 엔진 오류", memecoin_error, cooldown_seconds=240)
        if bybit_error:
            self._emit_runtime_error("core:crypto", "크립토 동기화 오류", bybit_error, cooldown_seconds=240)
        for source, row in trend_status.items():
            status = str((row or {}).get("status") or "")
            err = str((row or {}).get("error") or "")
            if status not in {"error", "cooldown"} or not err:
                continue
            if source == "google_gemini" and "rate_limited_429" in err:
                continue
            cooldown = 900 if status == "cooldown" else 300
            self._emit_runtime_error(
                f"trend:{source}",
                f"트렌드 소스 오류({source})",
                f"{status}: {err}",
                cooldown_seconds=cooldown,
            )

    def _loop(self) -> None:
        while self._running:
            started = time.time()
            try:
                self._reload_settings()
                self._ensure_model_runs()
                self.run_cycle()
            except Exception as exc:  # noqa: BLE001
                with self._lock:
                    self.state.memecoin_error = f"cycle_failed: {exc}"
                self._emit_runtime_error("core:loop", "엔진 루프 오류", str(exc), cooldown_seconds=120)
            finally:
                self._persist()
            elapsed = time.time() - started
            sleep_s = max(0.5, float(self.settings.scan_interval_seconds) - elapsed)
            time.sleep(sleep_s)

    def _telegram_loop(self) -> None:
        while self._running:
            try:
                self._poll_telegram(int(time.time()))
            except Exception as exc:  # noqa: BLE001
                self._emit_runtime_error(
                    "core:telegram_loop",
                    "텔레그램 루프 오류",
                    str(exc),
                    level="warn",
                    cooldown_seconds=120,
                )
            interval = max(0.5, min(2.0, float(self.settings.telegram_poll_interval_seconds) / 2.0))
            time.sleep(interval)

    def run_cycle(self) -> None:
        now = int(time.time())
        with self._lock:
            self.state.last_cycle_ts = now

        self._sync_wallet(now)
        self._sync_bybit(now)
        if not (self._telegram_thread and self._telegram_thread.is_alive()):
            self._poll_telegram(now)
        self._update_focus_wallet_analysis(now)

        trend_bundle = self._fetch_trends()
        snapshots = self._fetch_snapshots(trend_bundle)
        self._update_new_meme_feed(snapshots, trend_bundle)
        bybit_prices = self._fetch_macro_demo_prices(trend_bundle) if self.settings.demo_enable_macro else {}

        for model_id in MODEL_IDS:
            with self._lock:
                meme_key = self._market_run_key("meme", model_id)
                run = self.state.model_runs.get(meme_key)
                if not isinstance(run, dict):
                    run = self._blank_market_run("meme", model_id, self.state.demo_seed_usdt)
                    self.state.model_runs[meme_key] = run
                self._normalize_market_run(run, "meme", model_id, self.state.demo_seed_usdt)

            signals = self._score_signals_variant(snapshots, trend_bundle, model_id)
            run["latest_signals"] = [
                {
                    "symbol": s["token"].symbol,
                    "name": s["token"].name,
                    "grade": str(s.get("grade") or "G"),
                    "score": round(float(s["score"]), 4),
                    "probability": round(float(s["probability"]), 4),
                    "price_usd": float(s["token"].price_usd),
                    "liquidity_usd": float(s["token"].liquidity_usd),
                    "volume_5m_usd": float(s["token"].volume_5m_usd),
                    "age_minutes": float(s["token"].age_minutes),
                    "reason": str(s["reason"]),
                    "token_address": s["token"].token_address,
                }
                for s in signals[:80]
            ]
            self._evaluate_model_memecoin_exits(model_id, run)
            if self.settings.enable_autotrade:
                self._execute_model_memecoin_entries(model_id, run, signals)

        if self.settings.demo_enable_macro:
            for model_id in MODEL_IDS:
                with self._lock:
                    crypto_key = self._market_run_key("crypto", model_id)
                    run = self.state.model_runs.get(crypto_key)
                    if not isinstance(run, dict):
                        run = self._blank_market_run("crypto", model_id, self.state.demo_seed_usdt)
                        self.state.model_runs[crypto_key] = run
                    self._normalize_market_run(run, "crypto", model_id, self.state.demo_seed_usdt)

                run["latest_crypto_signals"] = self._score_crypto_signals(model_id, run, bybit_prices, trend_bundle)[:80]
                self._evaluate_model_bybit_exits(model_id, run, bybit_prices)
                if self.settings.enable_autotrade:
                    self._execute_model_bybit_entries(
                        model_id,
                        run,
                        bybit_prices,
                        trend_bundle,
                        list(run.get("latest_crypto_signals") or []),
                    )

        self._record_daily_pnl(now)
        self._maybe_autotune_models(now)
        self._sync_primary_views_from_model_a()
        self._scan_and_notify_runtime_errors()
        self._send_telegram_periodic_report(now)

    def _build_telegram_periodic_report(self) -> str:
        with self._lock:
            runs = dict(self.state.model_runs or {})
            wallet_assets = list(self.state.wallet_assets or [])
            bybit_assets = list(self.state.bybit_assets or [])
        now_ts = int(time.time())
        ts_text = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
        mode_text = str(self.settings.trade_mode or "paper").upper()
        auto_text = "ON" if bool(self.settings.enable_autotrade) else "OFF"
        report_text = "ON" if bool(self.settings.telegram_report_enabled) else "OFF"

        def _sgn(v: float) -> str:
            return f"{float(v):+.2f}"

        lines: list[str] = [
            f"[10분 리포트] {ts_text}",
            f"상태: {'RUNNING' if self.running else 'STOPPED'} | 모드: {mode_text} | 자동매매: {auto_text} | 주기리포트: {report_text}",
            "",
            "[자산 요약]",
        ]
        for model_id in MODEL_IDS:
            meme_run = self._get_market_run(runs, "meme", model_id)
            crypto_run = self._get_market_run(runs, "crypto", model_id)
            mm = self._model_metrics_market(model_id, meme_run, "meme")
            cm = self._model_metrics_market(model_id, crypto_run, "crypto")
            core_name = self._display_model_name(model_id)
            meme_name = self._market_model_name("meme", model_id)
            crypto_name = self._market_model_name("crypto", model_id)
            lines.append(
                f"- {core_name}"
            )
            lines.append(
                f"  · 밈({meme_name}): PNL {_sgn(float(mm.get('total_pnl_usd') or 0.0))} | OPEN {int(mm.get('open_positions') or 0)}"
            )
            lines.append(
                f"  · 크립토({crypto_name}): PNL {_sgn(float(cm.get('total_pnl_usd') or 0.0))} | OPEN {int(cm.get('open_positions') or 0)}"
            )
            meme_alloc = self._fmt_last_entry_alloc(
                dict((meme_run.get("last_entry_alloc") or {}).get("meme") or {}),
                now_ts,
            )
            crypto_alloc = self._fmt_last_entry_alloc(
                dict((crypto_run.get("last_entry_alloc") or {}).get("crypto") or {}),
                now_ts,
            )
            lines.append(f"  · 최근진입: 밈 {meme_alloc} | 크립토 {crypto_alloc}")
        wallet_total = sum(float(r.get("value_usd") or 0.0) for r in wallet_assets)
        bybit_total = sum(float(r.get("usd_value") or 0.0) for r in bybit_assets)
        lines.append("")
        lines.append(f"팬텀 잔고(USD>=1): ${wallet_total:.2f}")
        lines.append(f"거래소 잔고: ${bybit_total:.2f}")
        lines.append("")
        lines.append("[자동튜닝 6시간]")
        for model_id in MODEL_IDS:
            crypto_run = self._get_market_run(runs, "crypto", model_id)
            tune = self._read_model_runtime_tune_from_run(crypto_run or {}, model_id, now_ts)
            remain = max(0, int(tune.get("next_eval_ts") or 0) - now_ts)
            core_name = self._display_model_name(model_id)
            lines.append(
                f"- {core_name}: next {remain // 60}m | thr {float(tune['threshold']):.4f} | "
                f"tp {float(tune['tp_mul']):.2f} | sl {float(tune['sl_mul']):.2f}"
            )
            if int(tune.get("last_eval_ts") or 0) > 0:
                lines.append(
                    f"  · 최근평가: closed {int(tune['last_eval_closed'])}, wr {float(tune['last_eval_win_rate']):.1f}%, "
                    f"pnl {_sgn(float(tune['last_eval_pnl_usd']))}, pf {float(tune['last_eval_pf']):.2f}, "
                    f"note {str(tune.get('last_eval_note') or '-')}"
                )
        return "\n".join(lines)

    @staticmethod
    def _crypto_recent_stats(run: dict[str, Any], lookback: int = MODEL_AUTOTUNE_LOOKBACK_TRADES) -> dict[str, float]:
        trades = list(run.get("trades") or [])
        sells = [
            t
            for t in trades
            if str(t.get("side") or "").lower() == "sell"
            and str(t.get("source") or "").lower() == "crypto_demo"
        ]
        if lookback > 0:
            sells = sells[-int(lookback) :]
        pnl_rows = [float(t.get("pnl_usd") or 0.0) for t in sells]
        wins = [v for v in pnl_rows if v > 0.0]
        losses = [v for v in pnl_rows if v < 0.0]
        gross_win = float(sum(wins))
        gross_loss = float(abs(sum(losses)))
        closed = len(pnl_rows)
        win_rate = (len(wins) / closed * 100.0) if closed > 0 else 0.0
        avg_win = (gross_win / len(wins)) if wins else 0.0
        avg_loss = (gross_loss / len(losses)) if losses else 0.0
        if gross_loss <= 1e-9:
            profit_factor = 9.99 if gross_win > 0 else 0.0
        else:
            profit_factor = gross_win / gross_loss
        return {
            "closed": float(closed),
            "wins": float(len(wins)),
            "win_rate": float(win_rate),
            "pnl_usd": float(sum(pnl_rows)),
            "gross_win": float(gross_win),
            "gross_loss": float(gross_loss),
            "avg_win": float(avg_win),
            "avg_loss": float(avg_loss),
            "profit_factor": float(profit_factor),
        }

    def _maybe_autotune_models(self, now_ts: int) -> None:
        now = int(now_ts)
        alert_lines: list[str] = []
        with self._lock:
            runs = self.state.model_runs or {}
            for model_id in MODEL_IDS:
                run = runs.get(self._market_run_key("crypto", model_id))
                if not isinstance(run, dict):
                    continue
                tune = self._ensure_model_runtime_tune(run, model_id, now)
                if now < int(tune.get("next_eval_ts") or 0):
                    continue

                stats = self._crypto_recent_stats(run, MODEL_AUTOTUNE_LOOKBACK_TRADES)
                closed = int(stats.get("closed") or 0)
                win_rate = float(stats.get("win_rate") or 0.0)
                pnl = float(stats.get("pnl_usd") or 0.0)
                pf = float(stats.get("profit_factor") or 0.0)
                old_thr = float(tune.get("threshold") or MODEL_RUNTIME_TUNE_DEFAULTS[model_id]["threshold"])
                old_tp = float(tune.get("tp_mul") or MODEL_RUNTIME_TUNE_DEFAULTS[model_id]["tp_mul"])
                old_sl = float(tune.get("sl_mul") or MODEL_RUNTIME_TUNE_DEFAULTS[model_id]["sl_mul"])
                new_thr = old_thr
                new_tp = old_tp
                new_sl = old_sl

                note = "hold"
                if closed < MODEL_AUTOTUNE_MIN_CLOSED_TRADES:
                    note = "hold_not_enough_samples"
                else:
                    if model_id == "A":
                        if pnl <= -10.0 or win_rate < 50.0 or pf < 0.98:
                            new_thr += 0.002
                            new_tp -= 0.02
                            new_sl -= 0.04
                            note = "quality_defensive"
                        elif pnl >= 10.0 and win_rate >= 61.0 and pf >= 1.20:
                            new_thr -= 0.001
                            new_tp += 0.01
                            new_sl += 0.02
                            note = "quality_expansion"
                        else:
                            note = "quality_hold"
                    elif model_id == "B":
                        if pnl <= -12.0 or win_rate < 44.0 or pf < 0.90:
                            new_thr += 0.003
                            new_tp -= 0.04
                            new_sl -= 0.08
                            note = "trend_defensive"
                        elif pnl >= 12.0 and win_rate >= 58.0 and pf >= 1.15:
                            new_thr -= 0.002
                            new_tp += 0.03
                            new_sl += 0.02
                            note = "trend_expansion"
                        else:
                            if pf < 1.0:
                                new_thr += 0.001
                                new_sl -= 0.03
                                note = "trend_stability_down"
                            elif pf > 1.05 and win_rate >= 50.0:
                                new_thr -= 0.001
                                new_tp += 0.01
                                note = "trend_stability_up"
                    else:  # C
                        if pnl <= -8.0 or win_rate < 42.0 or pf < 0.92:
                            new_thr += 0.004
                            new_tp -= 0.05
                            new_sl -= 0.06
                            note = "aggr_risk_off"
                        elif pnl >= 10.0 and win_rate >= 55.0 and pf >= 1.10:
                            new_thr -= 0.002
                            new_tp += 0.03
                            new_sl += 0.01
                            note = "aggr_risk_on"
                        else:
                            note = "aggr_hold"

                clamps = self._model_tune_clamps(model_id)
                new_thr = _clamp(new_thr, float(clamps["threshold"][0]), float(clamps["threshold"][1]))
                new_tp = _clamp(new_tp, float(clamps["tp_mul"][0]), float(clamps["tp_mul"][1]))
                new_sl = _clamp(new_sl, float(clamps["sl_mul"][0]), float(clamps["sl_mul"][1]))
                tune.update(
                    {
                        "threshold": float(new_thr),
                        "tp_mul": float(new_tp),
                        "sl_mul": float(new_sl),
                        "last_eval_ts": int(now),
                        "next_eval_ts": int(now + MODEL_AUTOTUNE_INTERVAL_SECONDS),
                        "last_eval_closed": int(closed),
                        "last_eval_win_rate": round(float(win_rate), 4),
                        "last_eval_pnl_usd": round(float(pnl), 6),
                        "last_eval_pf": round(float(pf), 6),
                        "last_eval_note": str(note),
                    }
                )
                all_raw = dict(run.get("model_runtime_tune") or {})
                all_raw[model_id] = dict(tune)
                run["model_runtime_tune"] = dict(all_raw)
                if model_id == "B":
                    run["b_runtime_tune"] = dict(tune)
                core_name = self._display_model_name(model_id)
                alert_lines.append(
                    f"[{core_name}] {note} | closed={closed} wr={win_rate:.1f}% pnl={pnl:+.2f} pf={pf:.2f} | "
                    f"thr {old_thr:.4f}->{new_thr:.4f} tp_mul {old_tp:.2f}->{new_tp:.2f} sl_mul {old_sl:.2f}->{new_sl:.2f}"
                )
        if alert_lines:
            self._push_alert("info", "모델 6시간 자동튜닝", "\n".join(alert_lines), send_telegram=True)

    def _send_telegram_periodic_report(self, now: int) -> None:
        if not self.alert_manager.enabled:
            return
        if not bool(self.settings.telegram_report_enabled):
            return
        interval = max(60, int(self.settings.telegram_report_interval_seconds))
        if self._last_telegram_report <= 0:
            self._last_telegram_report = int(now)
            return
        if (int(now) - int(self._last_telegram_report)) < interval:
            return
        self._last_telegram_report = int(now)
        text = self._build_telegram_periodic_report()
        self.alert_manager.send_telegram(text)

    def _sync_primary_views_from_model_a(self) -> None:
        with self._lock:
            runs = dict(self.state.model_runs or {})
            meme_run = self._get_market_run(runs, "meme", "A")
            crypto_run = self._get_market_run(runs, "crypto", "A")
            if not isinstance(meme_run, dict):
                return
            self.state.cash_usd = float(meme_run.get("meme_cash_usd") or 0.0)
            self.state.latest_signals = list(meme_run.get("latest_signals") or [])

            positions: dict[str, Position] = {}
            for token_address, row in dict(meme_run.get("meme_positions") or {}).items():
                pos = Position(
                    token_address=str(token_address),
                    symbol=str(row.get("symbol") or ""),
                    qty=float(row.get("qty") or 0.0),
                    avg_price_usd=float(row.get("avg_price_usd") or 0.0),
                    opened_at=int(row.get("opened_at") or int(time.time())),
                    mode=self.settings.trade_mode,
                    source="memecoin",
                    side="long",
                    score=float(row.get("entry_score") or 0.0),
                    reason=str(row.get("reason") or ""),
                    entry_features=dict(row.get("entry_features") or {}),
                )
                if pos.token_address:
                    positions[pos.token_address] = pos
            self.state.positions = positions

            trades: list[Trade] = []
            merged = list(meme_run.get("trades") or []) + list(crypto_run.get("trades") or [])
            merged.sort(key=lambda r: int((r or {}).get("ts") or 0))
            for row in merged[-RUN_TRADE_HISTORY_LIMIT:]:
                trades.append(
                    Trade(
                        ts=int(row.get("ts") or int(time.time())),
                        side=str(row.get("side") or ""),
                        symbol=str(row.get("symbol") or ""),
                        token_address=str(row.get("token_address") or row.get("symbol") or ""),
                        qty=float(row.get("qty") or 0.0),
                        price_usd=float(row.get("price_usd") or 0.0),
                        notional_usd=float(row.get("notional_usd") or 0.0),
                        pnl_usd=float(row.get("pnl_usd") or 0.0),
                        pnl_pct=float(row.get("pnl_pct") or 0.0),
                        reason=str(row.get("reason") or ""),
                        mode=self.settings.trade_mode,
                        source=str(row.get("source") or "memecoin"),
                    )
                )
            self.state.trades = trades

    @staticmethod
    def _snapshot_from_pump_coin(coin: dict[str, Any]) -> TokenSnapshot | None:
        if not isinstance(coin, dict):
            return None
        mint = str(coin.get("mint") or "").strip()
        if not mint:
            return None
        symbol = str(coin.get("symbol") or "").upper().strip() or "PUMP"
        name = str(coin.get("name") or "").strip() or symbol
        created_ms = coin.get("created_timestamp")
        age_minutes = 999999.0
        try:
            if created_ms:
                age_minutes = max(0.0, ((time.time() * 1000.0) - float(created_ms)) / 60000.0)
        except Exception:
            age_minutes = 999999.0
        usd_market_cap = 0.0
        total_supply = 0.0
        try:
            usd_market_cap = float(coin.get("usd_market_cap") or coin.get("market_cap") or 0.0)
        except Exception:
            usd_market_cap = 0.0
        try:
            total_supply = float(coin.get("total_supply") or 0.0)
        except Exception:
            total_supply = 0.0
        price_usd = 0.0
        if usd_market_cap > 0 and total_supply > 0:
            price_usd = usd_market_cap / total_supply
        if price_usd <= 0.0:
            price_usd = 0.000000001
        return TokenSnapshot(
            token_address=mint,
            symbol=symbol,
            name=name,
            pair_url=f"https://pump.fun/{mint}",
            price_usd=float(price_usd),
            liquidity_usd=0.0,
            volume_5m_usd=0.0,
            buys_5m=0,
            sells_5m=0,
            age_minutes=float(age_minutes),
            source="pumpfun_raw",
        )

    def _fetch_snapshots(self, trend_bundle: dict[str, Any] | None = None) -> list[TokenSnapshot]:
        target = max(20, int(self.settings.max_boost_tokens_per_cycle))
        rows: list[TokenSnapshot] = []
        index_by_token: dict[str, int] = {}
        error_msg = ""

        def add_or_replace_snapshot(snap: TokenSnapshot | None) -> None:
            if snap is None:
                return
            addr = str(snap.token_address or "").strip()
            if not addr:
                return
            idx = index_by_token.get(addr)
            if idx is None:
                index_by_token[addr] = len(rows)
                rows.append(snap)
                return
            cur = rows[idx]
            cur_raw = str(cur.source or "").lower() == "pumpfun_raw"
            new_raw = str(snap.source or "").lower() == "pumpfun_raw"
            if cur_raw and not new_raw:
                rows[idx] = snap
                return
            if (not cur_raw) and (not new_raw):
                if float(snap.liquidity_usd) > float(cur.liquidity_usd):
                    rows[idx] = snap

        # 1) pump.fun latest feed as primary source for meme discovery.
        if bool(self.settings.pumpfun_enabled) and str(self.settings.dex_chain).lower() == "solana":
            try:
                pump_rows = self.pumpfun.fetch_latest_coins(
                    limit=max(target, int(self.settings.pumpfun_fetch_limit)),
                    include_nsfw=bool(self.settings.pumpfun_include_nsfw),
                    cache_seconds=int(self.settings.pumpfun_cache_seconds),
                )
                mints: list[str] = []
                seen_mints: set[str] = set()
                for row in pump_rows:
                    mint = str((row or {}).get("mint") or "").strip()
                    if not mint or mint in seen_mints:
                        continue
                    seen_mints.add(mint)
                    mints.append(mint)
                if mints:
                    hydrated = self.dex.fetch_snapshots_for_addresses(
                        self.settings.dex_chain,
                        mints[: max(target * 2, 80)],
                        max_tokens=target,
                        source="pumpfun_dex",
                    )
                    for snap in hydrated:
                        add_or_replace_snapshot(snap)
                if len(rows) < target:
                    for row in pump_rows:
                        add_or_replace_snapshot(self._snapshot_from_pump_coin(row))
                        if len(rows) >= target:
                            break
            except Exception as exc:  # noqa: BLE001
                error_msg = f"pumpfun_fetch_failed: {exc}"

        # 2) Dex boosted fallback.
        if len(rows) < target:
            try:
                boosted = self.dex.fetch_snapshots(self.settings.dex_chain, self.settings.max_boost_tokens_per_cycle)
            except Exception as exc:  # noqa: BLE001
                if not error_msg:
                    error_msg = f"dex_fetch_failed: {exc}"
                boosted = []
            for snap in boosted:
                add_or_replace_snapshot(snap)
                if len(rows) >= target:
                    break

        if len(rows) < target and isinstance(trend_bundle, dict):
            trending = [str(s).upper() for s in list(trend_bundle.get("trending") or set()) if str(s).strip()]
            trader_counts = dict(trend_bundle.get("trader_counts") or {})
            news_counts = dict(trend_bundle.get("news_counts") or {})
            community_counts = dict(trend_bundle.get("community_counts") or {})
            google_counts = dict(trend_bundle.get("google_counts") or {})
            trader_ranked = sorted(
                [(str(k).upper(), int(v)) for k, v in trader_counts.items() if str(k).strip()],
                key=lambda x: x[1],
                reverse=True,
            )
            news_ranked = sorted(
                [(str(k).upper(), int(v)) for k, v in news_counts.items() if str(k).strip()],
                key=lambda x: x[1],
                reverse=True,
            )
            community_ranked = sorted(
                [(str(k).upper(), int(v)) for k, v in community_counts.items() if str(k).strip()],
                key=lambda x: x[1],
                reverse=True,
            )
            google_ranked = sorted(
                [(str(k).upper(), int(v)) for k, v in google_counts.items() if str(k).strip()],
                key=lambda x: x[1],
                reverse=True,
            )
            symbols = (
                trending
                + [s for s, _ in trader_ranked[:80]]
                + [s for s, _ in news_ranked[:60]]
                + [s for s, _ in community_ranked[:60]]
                + [s for s, _ in google_ranked[:80]]
            )
            merged_symbols: list[str] = []
            seen_symbols: set[str] = set()
            for sym in symbols:
                if sym in seen_symbols:
                    continue
                seen_symbols.add(sym)
                merged_symbols.append(sym)
            remain = max(0, target - len(rows))
            if remain > 0 and merged_symbols:
                try:
                    extra = self.dex.fetch_symbol_snapshots(self.settings.dex_chain, merged_symbols[:120], remain)
                except Exception:
                    extra = []
                for snap in extra:
                    add_or_replace_snapshot(snap)
                    if len(rows) >= target:
                        break
        # Keep mark prices fresh for currently held meme positions even when they
        # fall out of boosted/trending lists. This prevents unrealized PNL from
        # appearing frozen.
        held_tokens: list[str] = []
        with self._lock:
            for model_id in MODEL_IDS:
                run = self._get_market_run(self.state.model_runs or {}, "meme", model_id)
                for token_address in dict(run.get("meme_positions") or {}).keys():
                    addr = str(token_address or "").strip()
                    if addr:
                        held_tokens.append(addr)
        if held_tokens:
            unique_held: list[str] = []
            seen_held: set[str] = set()
            for addr in held_tokens:
                if addr in seen_held:
                    continue
                seen_held.add(addr)
                unique_held.append(addr)
            for addr in unique_held[:40]:
                try:
                    snap = self.dex.fetch_snapshot_for_token(self.settings.dex_chain, addr)
                except Exception:
                    snap = None
                add_or_replace_snapshot(snap)
        with self._lock:
            if rows:
                self.state.memecoin_error = ""
            elif error_msg:
                self.state.memecoin_error = str(error_msg)
        for snap in rows:
            self._last_prices[snap.token_address] = float(snap.price_usd)
        return rows[:target]

    def _fetch_trends(self) -> dict[str, Any]:
        now = int(time.time())
        trending: set[str] = set(self._trend_cache_trending or set())
        trader_events: list[TrendEvent] = []
        wallet_events: list[TrendEvent] = []
        news_events: list[TrendEvent] = []
        community_events: list[TrendEvent] = []
        google_events: list[TrendEvent] = []

        source_status: dict[str, dict[str, Any]] = {}
        error_backoff = max(120, int(self.settings.trend_error_backoff_seconds))

        def _set_source_status(
            source: str,
            *,
            enabled: bool = True,
            status: str = "ok",
            count: int = 0,
            error: str = "",
            cached: bool = False,
            next_retry_seconds: int = 0,
        ) -> None:
            source_status[source] = {
                "enabled": bool(enabled),
                "status": str(status),
                "count": int(count),
                "error": str(error or ""),
                "cached": bool(cached),
                "next_retry_seconds": int(max(0, next_retry_seconds)),
            }

        def _fetch_with_cache(
            source: str,
            *,
            enabled: bool,
            interval_seconds: int,
            fetcher: Any,
        ) -> list[TrendEvent]:
            if not enabled:
                _set_source_status(source, enabled=False, status="disabled")
                return []
            wait_until = int(self._trend_next_fetch_ts.get(source) or 0)
            cached_rows = list(self._trend_cache_events.get(source) or [])
            if now < wait_until:
                _set_source_status(
                    source,
                    status="cached",
                    count=len(cached_rows),
                    cached=True,
                    next_retry_seconds=(wait_until - now),
                )
                return cached_rows
            try:
                rows = fetcher()
                rows = list(rows or [])
                self._trend_cache_events[source] = list(rows)
                self._trend_next_fetch_ts[source] = int(now + max(60, int(interval_seconds)))
                _set_source_status(source, status="ok", count=len(rows))
                return rows
            except Exception as exc:
                self._trend_next_fetch_ts[source] = int(now + error_backoff)
                if cached_rows:
                    _set_source_status(
                        source,
                        status="error_cached",
                        count=len(cached_rows),
                        error=str(exc),
                        cached=True,
                        next_retry_seconds=error_backoff,
                    )
                    return cached_rows
                _set_source_status(source, status="error", error=str(exc), next_retry_seconds=error_backoff)
                return []

        cg_interval = max(60, int(self.settings.trend_cg_interval_seconds))
        cg_wait_until = int(self._trend_next_fetch_ts.get("coingecko") or 0)
        if now < cg_wait_until:
            trending = set(self._trend_cache_trending)
            _set_source_status(
                "coingecko",
                status="cached",
                count=len(trending),
                cached=True,
                next_retry_seconds=(cg_wait_until - now),
            )
        else:
            try:
                trending = set(self.trend.fetch_coingecko_symbols() or set())
                self._trend_cache_trending = set(trending)
                self._trend_next_fetch_ts["coingecko"] = int(now + cg_interval)
                _set_source_status("coingecko", status="ok", count=len(trending))
            except Exception as exc:
                self._trend_next_fetch_ts["coingecko"] = int(now + error_backoff)
                if self._trend_cache_trending:
                    trending = set(self._trend_cache_trending)
                    _set_source_status(
                        "coingecko",
                        status="error_cached",
                        count=len(trending),
                        error=str(exc),
                        cached=True,
                        next_retry_seconds=error_backoff,
                    )
                else:
                    trending = set()
                    _set_source_status("coingecko", status="error", error=str(exc), next_retry_seconds=error_backoff)

        trader_events = _fetch_with_cache(
            "trader_x",
            enabled=bool(str(self.settings.watch_trader_accounts or "").strip()),
            interval_seconds=self.settings.trend_trader_interval_seconds,
            fetcher=lambda: self.trend.fetch_trader_rss_events(self.settings.watch_trader_accounts),
        )
        wallet_watch_csv = str(self.settings.watch_wallets or "").strip()
        phantom_wallet = str(self.settings.phantom_wallet_address or "").strip()
        if phantom_wallet and phantom_wallet not in wallet_watch_csv:
            wallet_watch_csv = f"{wallet_watch_csv},{phantom_wallet}".strip(",")
        wallet_events = _fetch_with_cache(
            "wallet_tracker",
            enabled=bool(str(wallet_watch_csv or "").strip()) or bool(str(self.settings.watch_trader_accounts or "").strip()),
            interval_seconds=self.settings.trend_wallet_interval_seconds,
            fetcher=lambda: self.trend.fetch_wallet_events(wallet_watch_csv),
        )
        news_events = _fetch_with_cache(
            "yahoo_news",
            enabled=bool(str(self.settings.crypto_news_symbols or "").strip()),
            interval_seconds=self.settings.trend_news_interval_seconds,
            fetcher=lambda: self.trend.fetch_yahoo_crypto_news_events(self.settings.crypto_news_symbols),
        )
        community_events = _fetch_with_cache(
            "community_reddit",
            enabled=bool(str(self.settings.community_subreddits or "").strip()),
            interval_seconds=self.settings.trend_community_interval_seconds,
            fetcher=lambda: self.trend.fetch_reddit_events(
                self.settings.community_subreddits,
                self.settings.community_max_items_per_subreddit,
            ),
        )

        context_lines = [e.text for e in (trader_events + news_events + community_events)[:100]]
        google_cached = list(self._trend_cache_events.get("google_gemini") or [])
        try:
            google_events, google_meta = self.trend.fetch_google_gemini_events(
                self.settings.trend_query,
                context_lines,
                now_ts=now,
            )
            google_events = list(google_events or [])
            self._trend_cache_events["google_gemini"] = list(google_events)
        except Exception as exc:
            google_events = list(google_cached)
            google_meta = {
                "enabled": bool(self.settings.google_trend_enabled and self.settings.google_api_key),
                "status": "error_cached" if google_events else "error",
                "count": len(google_events),
                "cached": bool(google_events),
                "next_retry_seconds": error_backoff,
                "error": str(exc),
            }
        google_status = str(google_meta.get("status") or "ok")
        google_error = str(google_meta.get("error") or "")
        if google_status in {"rate_limited", "cooldown"}:
            # Quota cooldown is expected on free tier; keep status visible without noisy error text.
            google_status = "cooldown"
            google_error = ""
        _set_source_status(
            "google_gemini",
            enabled=bool(google_meta.get("enabled", True)),
            status=google_status,
            count=int(google_meta.get("count") or len(google_events)),
            error=google_error,
            cached=bool(google_meta.get("cached")),
            next_retry_seconds=int(google_meta.get("next_retry_seconds") or 0),
        )

        cg_events = [TrendEvent(source="coingecko", symbol=s, text=f"{s} trending", ts=now) for s in list(trending)[:80]]
        all_events = cg_events + trader_events + wallet_events + news_events + community_events + google_events
        with self._lock:
            self._trend_source_status = dict(source_status)
            self.state.trend_events.extend(asdict(e) for e in all_events)
            self.state.trend_events = self.state.trend_events[-1200:]

        trader_counts: dict[str, int] = {}
        wallet_counts: dict[str, int] = {}
        news_counts: dict[str, int] = {}
        community_counts: dict[str, int] = {}
        google_counts: dict[str, int] = {}
        combined_counts: dict[str, int] = {}
        for ev in trader_events:
            trader_counts[ev.symbol] = trader_counts.get(ev.symbol, 0) + 1
            combined_counts[ev.symbol] = combined_counts.get(ev.symbol, 0) + 1
        for ev in wallet_events:
            wallet_counts[ev.symbol] = wallet_counts.get(ev.symbol, 0) + 1
            combined_counts[ev.symbol] = combined_counts.get(ev.symbol, 0) + 1
        for ev in news_events:
            news_counts[ev.symbol] = news_counts.get(ev.symbol, 0) + 1
            combined_counts[ev.symbol] = combined_counts.get(ev.symbol, 0) + 1
        for ev in community_events:
            community_counts[ev.symbol] = community_counts.get(ev.symbol, 0) + 1
            combined_counts[ev.symbol] = combined_counts.get(ev.symbol, 0) + 1
        for ev in google_events:
            google_counts[ev.symbol] = google_counts.get(ev.symbol, 0) + 1
            combined_counts[ev.symbol] = combined_counts.get(ev.symbol, 0) + 1

        for sym, hits in combined_counts.items():
            if int(hits) >= 2:
                trending.add(sym)
        for sym, hits in google_counts.items():
            if int(hits) >= 1:
                trending.add(sym)
        self._trend_cache_trending = set(trending)
        return {
            "trending": trending,
            "trader_events": trader_events,
            "wallet_events": wallet_events,
            "news_events": news_events,
            "community_events": community_events,
            "google_events": google_events,
            "trader_counts": trader_counts,
            "wallet_counts": wallet_counts,
            "news_counts": news_counts,
            "community_counts": community_counts,
            "google_counts": google_counts,
            "source_status": source_status,
        }

    def _update_focus_wallet_analysis(self, now_ts: int) -> None:
        token = str(self.settings.solscan_focus_token or "").strip()
        if not token or not self.settings.solscan_enable_pattern or bool(self.settings.solscan_tracker_only):
            return
        cached = self._wallet_pattern_cache.get(token) or {}
        ts = int(cached.get("cached_ts") or 0)
        if ts > 0 and (now_ts - ts) < int(self.settings.solscan_cache_seconds):
            self._focus_wallet_analysis = dict(cached.get("analysis") or {})
            return
        analysis = self._get_wallet_pattern(token, now_ts)
        self._focus_wallet_analysis = dict(analysis)

    def _update_new_meme_feed(self, snapshots: list[TokenSnapshot], trend_bundle: dict[str, Any]) -> None:
        trader_counts = dict(trend_bundle.get("trader_counts") or {})
        news_counts = dict(trend_bundle.get("news_counts") or {})
        community_counts = dict(trend_bundle.get("community_counts") or {})
        google_counts = dict(trend_bundle.get("google_counts") or {})
        max_age_minutes = max(5.0, float(self.settings.new_meme_feed_max_age_minutes))
        rows: list[dict[str, Any]] = []
        for snap in snapshots:
            if not self._is_memecoin_snapshot(snap):
                continue
            if float(snap.age_minutes) > max_age_minutes:
                continue
            sym = str(snap.symbol or "").upper()
            hits = (
                int(trader_counts.get(sym, 0))
                + int(news_counts.get(sym, 0))
                + int(community_counts.get(sym, 0))
                + int(google_counts.get(sym, 0))
            )
            rows.append(
                {
                    "symbol": sym,
                    "name": str(snap.name or ""),
                    "token_address": str(snap.token_address or ""),
                    "age_minutes": float(snap.age_minutes),
                    "price_usd": float(snap.price_usd),
                    "liquidity_usd": float(snap.liquidity_usd),
                    "volume_5m_usd": float(snap.volume_5m_usd),
                    "buys_5m": int(snap.buys_5m),
                    "sells_5m": int(snap.sells_5m),
                    "buy_sell_ratio": float(snap.buy_sell_ratio),
                    "trend_hits": int(hits),
                    "is_pump_fun": bool(str(snap.token_address or "").lower().endswith("pump")),
                }
            )
        rows.sort(
            key=lambda r: (
                -int(bool(r.get("is_pump_fun"))),
                float(r.get("age_minutes") or 999999.0),
                -float(r.get("trend_hits") or 0),
                -float(r.get("volume_5m_usd") or 0.0),
            )
        )
        with self._lock:
            self._new_meme_feed = rows[:80]

    def _get_wallet_pattern(self, token_address: str, now_ts: int | None = None, *, force: bool = False) -> dict[str, Any]:
        token = str(token_address or "").strip()
        if not token:
            return {"available": False, "smart_wallet_score": 0.50, "holder_risk": 0.50}
        if not self.settings.solscan_enable_pattern:
            return {"available": False, "smart_wallet_score": 0.50, "holder_risk": 0.50, "error": "pattern_disabled"}
        now = int(now_ts or int(time.time()))
        cached = self._wallet_pattern_cache.get(token) or {}
        ts = int(cached.get("cached_ts") or 0)
        if ts > 0 and (now - ts) < int(self.settings.solscan_cache_seconds):
            return dict(cached.get("analysis") or {})
        try:
            analysis = self.solscan.analyze_wallet_pattern(token)
        except Exception as exc:  # noqa: BLE001
            analysis = {
                "token_address": token,
                "available": False,
                "error": f"solscan_failed:{exc}",
                "smart_wallet_score": 0.50,
                "holder_risk": 0.50,
            }
        self._wallet_pattern_cache[token] = {"cached_ts": now, "analysis": dict(analysis)}
        return dict(analysis)

    def _score_signals_variant(
        self,
        snapshots: list[TokenSnapshot],
        trend_bundle: dict[str, Any],
        model_id: str,
    ) -> list[dict[str, Any]]:
        trending: set[str] = set(trend_bundle.get("trending") or set())
        trader_count: dict[str, int] = dict(trend_bundle.get("trader_counts") or {})
        wallet_count: dict[str, int] = dict(trend_bundle.get("wallet_counts") or {})
        news_count: dict[str, int] = dict(trend_bundle.get("news_counts") or {})
        community_count: dict[str, int] = dict(trend_bundle.get("community_counts") or {})
        google_count: dict[str, int] = dict(trend_bundle.get("google_counts") or {})

        out: list[dict[str, Any]] = []
        relaxed: list[dict[str, Any]] = []
        wallet_budget = 6 if bool(self.settings.solscan_tracker_only) else 12
        threshold = self._variant_threshold(model_id)
        guard = self._entry_guard_profile(model_id, "meme")
        threshold += float(guard.get("threshold_boost") or 0.0)
        for snap in snapshots:
            symbol = snap.symbol.upper()
            trader_hits = int(trader_count.get(symbol, 0))
            wallet_hits = int(wallet_count.get(symbol, 0))
            news_hits = int(news_count.get(symbol, 0))
            community_hits = int(community_count.get(symbol, 0))
            google_hits = int(google_count.get(symbol, 0))
            trend_hit = 1 if (symbol in trending or google_hits > 0) else 0
            normal_candidate = self._is_candidate(snap, trend_hit, trader_hits)
            relaxed_candidate = self._is_relaxed_demo_candidate(
                snap,
                trend_hit,
                trader_hits,
                news_hits,
                community_hits,
                google_hits,
            )
            if not normal_candidate and not relaxed_candidate:
                continue

            wallet_pattern: dict[str, Any] = {"available": False, "smart_wallet_score": 0.50, "holder_risk": 0.50}
            tracker_driven = bool(wallet_hits > 0 or trader_hits > 0)
            if (
                self.settings.solscan_enable_pattern
                and self.solscan.enabled
                and wallet_budget > 0
                and tracker_driven
            ):
                wallet_pattern = self._get_wallet_pattern(snap.token_address)
                wallet_budget -= 1

            features = self._build_features(
                snap,
                trend_hit,
                trader_hits,
                wallet_hits,
                news_hits,
                community_hits,
                google_hits,
                wallet_pattern,
            )
            probability = self.model.predict_proba(features)
            heuristic = self._heuristic_score(features)
            score = self._variant_mix_score(model_id, probability, heuristic, features)
            grade = self._meme_grade(score)
            reason = self._build_reason(features, score, trend_hit, trader_hits, model_id, grade)
            row = {
                "token": snap,
                "score": score,
                "grade": grade,
                "probability": probability,
                "reason": reason,
                "features": features,
            }
            if score < threshold:
                if normal_candidate or relaxed_candidate:
                    relaxed.append(row)
                continue
            out.append(row)
        if not out and self.settings.trade_mode == "paper" and relaxed:
            if not bool(guard.get("allow_demo_fallback", True)):
                return out
            floor = self._demo_meme_score_floor(model_id)
            floor += max(0.0, float(guard.get("threshold_boost") or 0.0))
            relaxed.sort(key=lambda row: float(row["score"]), reverse=True)
            limit = max(3, int(self.settings.max_signals_per_cycle) * 2)
            for row in relaxed:
                if float(row["score"]) < floor:
                    continue
                row["reason"] = f"{str(row.get('reason') or '')},데모폴백"
                out.append(row)
                if len(out) >= limit:
                    break
        out.sort(key=lambda row: float(row["score"]), reverse=True)
        return out

    @staticmethod
    def _meme_grade(score: float) -> str:
        s = float(score)
        if s >= 0.90:
            return "S"
        if s >= 0.82:
            return "A"
        if s >= 0.74:
            return "B"
        if s >= 0.66:
            return "C"
        if s >= 0.58:
            return "D"
        if s >= 0.50:
            return "E"
        if s >= 0.42:
            return "F"
        return "G"

    @staticmethod
    def _meme_grade_criteria() -> list[dict[str, Any]]:
        return [
            {"grade": "S", "score_min": 0.90, "score_max": 1.00, "meaning": "초강세. 트렌드/체결/지갑패턴 모두 매우 강함"},
            {"grade": "A", "score_min": 0.82, "score_max": 0.8999, "meaning": "강세. 단기 추세 지속 가능성 높음"},
            {"grade": "B", "score_min": 0.74, "score_max": 0.8199, "meaning": "우세. 진입 고려 구간"},
            {"grade": "C", "score_min": 0.66, "score_max": 0.7399, "meaning": "보통 이상. 기본 진입 최소 등급"},
            {"grade": "D", "score_min": 0.58, "score_max": 0.6599, "meaning": "애매. 변동성 대비 신뢰도 낮음"},
            {"grade": "E", "score_min": 0.50, "score_max": 0.5799, "meaning": "약함. 관망 권장"},
            {"grade": "F", "score_min": 0.42, "score_max": 0.4999, "meaning": "매우 약함. 진입 비권장"},
            {"grade": "G", "score_min": 0.00, "score_max": 0.4199, "meaning": "노이즈 구간"},
        ]

    @staticmethod
    def _crypto_param_legend() -> list[dict[str, str]]:
        return [
            {"key": "5m/15m/1h/4h/1d", "meaning": "5분/15분/1시간/4시간/일봉 수익률(%)"},
            {"key": "EMA", "meaning": "EMA(9/21) 기반 추세 강도(0~1)"},
            {"key": "RSI", "meaning": "과매수/과매도 강도(0~100)"},
            {"key": "CCI", "meaning": "평균 대비 이격 강도(음수=눌림, 양수=과열)"},
            {"key": "ATR", "meaning": "단기 변동성(%)"},
            {"key": "Q", "meaning": "유동성/시총/시총순위 기반 품질 점수(0~1)"},
            {"key": "S", "meaning": "트렌드+뉴스+커뮤니티+Google 소셜 히트 점수(0~1)"},
            {"key": "T", "meaning": "멀티타임프레임 추세 스택 점수(-1~+1)"},
            {"key": "OH", "meaning": "24h 급등 과열 패널티(높을수록 과열)"},
            {"key": "rank_max", "meaning": "모델이 허용하는 시총 순위 상한"},
            {"key": "trend_stack_min", "meaning": "모델별 최소 추세 스택 기준"},
            {"key": "overheat_max", "meaning": "모델별 과열 허용 상한"},
            {"key": "Hard-ROE", "meaning": "모델별 강제손절 기준(ROE%) 도달 시 즉시 청산"},
            {"key": "gate", "meaning": "모델별 진입 필터 통과 여부(Y/N)"},
        ]

    @staticmethod
    def _grade_rank(grade: str) -> int:
        table = {"S": 0, "A": 1, "B": 2, "C": 3, "D": 4, "E": 5, "F": 6, "G": 7}
        return int(table.get(str(grade or "").upper(), 7))

    def _is_swing_candidate(self, grade: str, features: dict[str, Any]) -> bool:
        if not self.settings.meme_swing_enabled:
            return False
        min_grade = str(self.settings.meme_swing_min_grade or "A").upper()
        if self._grade_rank(grade) > self._grade_rank(min_grade):
            return False
        trend_strength = float(features.get("trend_strength") or 0.0)
        trader_strength = float(features.get("trader_strength") or 0.0)
        smart_wallet = float(features.get("smart_wallet_score") or 0.5)
        return bool((trend_strength >= 0.40 and trader_strength >= 0.20) or smart_wallet >= 0.64)

    def _meme_strategy_for_model(self, model_id: str, grade: str, features: dict[str, Any]) -> str:
        if not self.settings.meme_swing_enabled:
            return "scalp"
        if model_id == "B":
            # B is now dedicated to long-hold meme mode.
            return "swing"
        if model_id == "C":
            # C is now dedicated to short-term scalp mode.
            return "scalp"
        g_rank = self._grade_rank(grade)
        trend = float(features.get("trend_strength") or 0.0)
        trader = float(features.get("trader_strength") or 0.0)
        news = float(features.get("news_strength") or 0.0)
        community = float(features.get("community_strength") or 0.0)
        google = float(features.get("google_strength") or 0.0)
        social = (news + community + google) / 3.0
        smart = float(features.get("smart_wallet_score") or 0.5)
        holder_risk = float(features.get("holder_risk") or 0.5)
        instant = float(features.get("new_meme_instant") or 0.0)
        tx_flow = float(features.get("tx_flow") or 0.0)

        if model_id == "A":
            if (
                g_rank <= self._grade_rank("B")
                and smart >= 0.62
                and holder_risk <= 0.58
                and (trend >= 0.28 or trader >= 0.22)
            ):
                return "swing"
            return "scalp"
        if g_rank <= self._grade_rank("A") and instant > 0.0 and (trend >= 0.45 or tx_flow >= 0.62):
            return "swing"
        return "scalp"

    def _variant_threshold(self, model_id: str) -> float:
        base = float(self.settings.min_signal_score)
        if model_id == "A":
            # Reliable model: stricter gate.
            return max(0.0, base - 0.005)
        if model_id == "B":
            # Trend model: medium gate.
            return max(0.0, base - 0.09)
        # Aggressive model: looser gate.
        return max(0.0, base - 0.18)

    @staticmethod
    def _variant_mix_score(model_id: str, probability: float, heuristic: float, features: dict[str, float]) -> float:
        if model_id == "A":
            score = (
                (0.84 * probability)
                + (0.16 * heuristic)
                + (0.12 * features.get("smart_wallet_score", 0.0))
                + (0.06 * features.get("trend_strength", 0.0))
                + (0.04 * features.get("trader_strength", 0.0))
                + (0.03 * features.get("liq_log", 0.0))
                + (0.02 * features.get("is_pump_fun", 0.0))
                - (0.10 * features.get("spread_proxy", 0.0))
                - (0.13 * features.get("holder_risk", 0.0))
                - (0.08 * features.get("noise_penalty", 0.0))
                - (0.05 * features.get("new_meme_instant", 0.0))
            )
            return _clamp(score, 0.0, 1.0)
        if model_id == "B":
            score = (
                (0.48 * probability)
                + (0.52 * heuristic)
                + (0.13 * features.get("trend_strength", 0.0))
                + (0.12 * features.get("news_strength", 0.0))
                + (0.12 * features.get("community_strength", 0.0))
                + (0.14 * features.get("google_strength", 0.0))
                + (0.10 * features.get("trader_strength", 0.0))
                + (0.08 * features.get("tx_flow", 0.0))
                + (0.04 * features.get("is_pump_fun", 0.0))
                - (0.05 * features.get("noise_penalty", 0.0))
                - (0.05 * features.get("holder_risk", 0.0))
            )
            return _clamp(score, 0.0, 1.0)
        score = (
            (0.28 * probability)
            + (0.72 * heuristic)
            + (0.22 * features.get("new_meme_instant", 0.0))
            + (0.16 * features.get("tx_flow", 0.0))
            + (0.12 * features.get("trend_strength", 0.0))
            + (0.08 * features.get("is_pump_fun", 0.0))
            + (0.08 * features.get("google_strength", 0.0))
            + (0.06 * features.get("news_strength", 0.0))
            - (0.02 * features.get("spread_proxy", 0.0))
            - (0.02 * features.get("holder_risk", 0.0))
        )
        return _clamp(score, 0.0, 1.0)

    @staticmethod
    def _is_memecoin_token(symbol: str, name: str, token_address: str) -> bool:
        sym = str(symbol or "").upper().strip()
        nm = str(name or "").lower().strip()
        addr = str(token_address or "").strip().lower()
        if not sym:
            return False
        if sym in NON_MEME_SYMBOLS:
            return False
        if sym.endswith(("USD", "USDT", "USDC")):
            return False
        if any(word in nm for word in NON_MEME_NAME_WORDS):
            return False
        if addr.endswith("pump"):
            return True
        if sym in KNOWN_MEME_SYMBOLS:
            return True
        text = f"{sym.lower()} {nm}"
        return any(word in text for word in MEME_HINT_WORDS)

    def _is_memecoin_snapshot(self, snap: TokenSnapshot) -> bool:
        return self._is_memecoin_token(snap.symbol, snap.name, snap.token_address)

    def _is_candidate(self, snap: TokenSnapshot, trend_hit: int, trader_hits: int) -> bool:
        if not self._is_memecoin_snapshot(snap):
            return False
        base_ok = (
            snap.liquidity_usd >= self.settings.dex_min_liquidity_usd
            and snap.volume_5m_usd >= self.settings.dex_min_5m_volume_usd
            and snap.buy_sell_ratio >= self.settings.dex_min_5m_buy_sell_ratio
            and snap.age_minutes >= self.settings.min_token_age_minutes
        )
        if base_ok:
            return True
        fast_lane = (
            snap.age_minutes <= 10.0
            and snap.buys_5m >= 4
            and snap.buys_5m > snap.sells_5m
            and snap.liquidity_usd >= (self.settings.dex_min_liquidity_usd * 0.45)
            and snap.volume_5m_usd >= (self.settings.dex_min_5m_volume_usd * 0.35)
            and (trend_hit > 0 or trader_hits > 0)
        )
        trend_lane = (
            (trend_hit > 0 or trader_hits >= 2)
            and snap.buy_sell_ratio >= (self.settings.dex_min_5m_buy_sell_ratio * 0.90)
            and snap.liquidity_usd >= (self.settings.dex_min_liquidity_usd * 0.25)
            and snap.volume_5m_usd >= (self.settings.dex_min_5m_volume_usd * 0.20)
            and snap.age_minutes >= 0.5
        )
        return fast_lane or trend_lane

    def _is_relaxed_demo_candidate(
        self,
        snap: TokenSnapshot,
        trend_hit: int,
        trader_hits: int,
        news_hits: int,
        community_hits: int,
        google_hits: int,
    ) -> bool:
        if self.settings.trade_mode != "paper":
            return False
        if not self._is_memecoin_snapshot(snap):
            return False
        signal_hits = int(trend_hit) + int(trader_hits) + int(news_hits) + int(community_hits) + int(google_hits)
        min_liq = max(350.0, float(self.settings.dex_min_liquidity_usd) * 0.08)
        min_vol = max(120.0, float(self.settings.dex_min_5m_volume_usd) * 0.10)
        flow_ok = snap.buys_5m >= max(2, snap.sells_5m)
        interest_ok = signal_hits > 0 or snap.age_minutes <= 90.0
        return bool(snap.liquidity_usd >= min_liq and snap.volume_5m_usd >= min_vol and flow_ok and interest_ok)

    @staticmethod
    def _demo_meme_score_floor(model_id: str) -> float:
        if model_id == "A":
            return 0.38
        if model_id == "B":
            return 0.31
        return 0.27

    def _meme_min_entry_rank_for_model(self, model_id: str) -> int:
        base_grade = str(self.settings.meme_min_entry_grade or "C").upper()
        base_rank = self._grade_rank(base_grade)
        if self.settings.trade_mode != "paper":
            return base_rank
        if model_id == "A":
            return max(base_rank, self._grade_rank("C"))
        if model_id == "B":
            return max(base_rank, self._grade_rank("B"))
        return max(base_rank, self._grade_rank("D"))

    def _recent_market_trade_stats(self, model_id: str, market: str, lookback: int = 40) -> dict[str, float]:
        source_name = "memecoin" if market == "meme" else "crypto_demo"
        with self._lock:
            key = self._market_run_key("meme" if market == "meme" else "crypto", model_id)
            run = (self.state.model_runs or {}).get(key) or {}
            trades = list((run or {}).get("trades") or [])
        sells = [
            t
            for t in trades
            if str(t.get("side") or "").lower() == "sell"
            and str(t.get("source") or "").lower() == source_name
        ]
        if lookback > 0:
            sells = sells[-int(lookback) :]
        pnl_rows = [float(t.get("pnl_usd") or 0.0) for t in sells]
        closed = len(pnl_rows)
        wins = sum(1 for v in pnl_rows if v > 0.0)
        win_rate = (wins / closed * 100.0) if closed > 0 else 0.0
        total_pnl = float(sum(pnl_rows))
        return {
            "closed": float(closed),
            "wins": float(wins),
            "win_rate": float(win_rate),
            "pnl_usd": float(total_pnl),
        }

    def _entry_guard_profile(self, model_id: str, market: str) -> dict[str, Any]:
        stats = self._recent_market_trade_stats(model_id, market, lookback=40)
        closed = int(stats.get("closed") or 0)
        win_rate = float(stats.get("win_rate") or 0.0)
        pnl_usd = float(stats.get("pnl_usd") or 0.0)
        guard = {
            "active": False,
            "threshold_boost": 0.0,
            "order_mul": 1.0,
            "allow_demo_fallback": True,
            "state": "normal",
            "closed": closed,
            "win_rate": win_rate,
            "pnl_usd": pnl_usd,
        }
        if closed < 8:
            return guard
        if win_rate < 40.0 or pnl_usd <= -18.0:
            guard.update(
                {
                    "active": True,
                    "threshold_boost": 0.020 if market == "crypto" else 0.025,
                    "order_mul": 0.65 if market == "crypto" else 0.55,
                    "allow_demo_fallback": False,
                    "state": "defensive_hard",
                }
            )
            return guard
        if win_rate < 48.0 or pnl_usd < 0.0:
            guard.update(
                {
                    "active": True,
                    "threshold_boost": 0.012 if market == "crypto" else 0.015,
                    "order_mul": 0.82 if market == "crypto" else 0.72,
                    "allow_demo_fallback": False,
                    "state": "defensive_soft",
                }
            )
            return guard
        if win_rate >= 58.0 and pnl_usd > 8.0:
            guard.update(
                {
                    "threshold_boost": -0.003,
                    "order_mul": 1.00,
                    "allow_demo_fallback": True,
                    "state": "normal_plus",
                }
            )
        return guard

    def _build_features(
        self,
        snap: TokenSnapshot,
        trend_hit: int,
        trader_hits: int,
        wallet_hits: int,
        news_hits: int,
        community_hits: int,
        google_hits: int,
        wallet_pattern: dict[str, Any] | None = None,
    ) -> dict[str, float]:
        pattern = dict(wallet_pattern or {})
        liq_log = min(1.0, math.log10(1.0 + snap.liquidity_usd) / 7.0)
        vol_log = min(1.0, math.log10(1.0 + snap.volume_5m_usd) / 6.0)
        age_freshness = _clamp(1.0 - (snap.age_minutes / 240.0), 0.0, 1.0)
        age_stability = _clamp(snap.age_minutes / 180.0, 0.0, 1.0)
        buy_sell_ratio = _clamp(snap.buy_sell_ratio / 2.2, 0.0, 1.0)
        tx_total = max(1.0, float(snap.buys_5m + snap.sells_5m))
        tx_flow_raw = (float(snap.buys_5m) - float(snap.sells_5m)) / tx_total
        tx_flow = _clamp((tx_flow_raw + 1.0) / 2.0, 0.0, 1.0)
        trend_strength = _clamp(0.55 * trend_hit + min(0.45, trader_hits * 0.12), 0.0, 1.0)
        trader_strength = _clamp(trader_hits / 4.0, 0.0, 1.0)
        wallet_strength = _clamp(wallet_hits / 4.0, 0.0, 1.0)
        news_strength = _clamp(news_hits / 4.0, 0.0, 1.0)
        community_strength = _clamp(community_hits / 5.0, 0.0, 1.0)
        google_strength = _clamp(google_hits / 3.0, 0.0, 1.0)
        new_meme_quality = _clamp((liq_log * 0.45) + (vol_log * 0.55), 0.0, 1.0) * age_freshness
        new_meme_instant = 1.0 if (snap.age_minutes <= 8 and snap.buys_5m >= max(3, snap.sells_5m + 1)) else 0.0
        is_pump_fun = 1.0 if str(snap.token_address or "").strip().lower().endswith("pump") else 0.0
        spread_proxy = _clamp(1.0 - (snap.liquidity_usd / (snap.liquidity_usd + 140_000.0)), 0.0, 1.0)
        noise_penalty = 1.0 if tx_total <= 3 else 0.0
        smart_wallet_score = _clamp(float(pattern.get("smart_wallet_score") or 0.50), 0.0, 1.0)
        holder_risk = _clamp(float(pattern.get("holder_risk") or 0.50), 0.0, 1.0)
        transfer_diversity = _clamp(float(pattern.get("transfer_diversity") or 0.0), 0.0, 1.0)
        return {
            "trend_strength": trend_strength,
            "trader_strength": trader_strength,
            "wallet_strength": wallet_strength,
            "news_strength": news_strength,
            "community_strength": community_strength,
            "google_strength": google_strength,
            "buy_sell_ratio": buy_sell_ratio,
            "liq_log": liq_log,
            "vol_log": vol_log,
            "age_freshness": age_freshness,
            "age_stability": age_stability,
            "tx_flow": tx_flow,
            "new_meme_quality": new_meme_quality,
            "new_meme_instant": new_meme_instant,
            "is_pump_fun": is_pump_fun,
            "spread_proxy": spread_proxy,
            "noise_penalty": noise_penalty,
            "smart_wallet_score": smart_wallet_score,
            "holder_risk": holder_risk,
            "transfer_diversity": transfer_diversity,
        }

    @staticmethod
    def _heuristic_score(features: dict[str, float]) -> float:
        base = 0.0
        base += 0.22 * features.get("trend_strength", 0.0)
        base += 0.16 * features.get("trader_strength", 0.0)
        base += 0.06 * features.get("wallet_strength", 0.0)
        base += 0.08 * features.get("news_strength", 0.0)
        base += 0.08 * features.get("community_strength", 0.0)
        base += 0.10 * features.get("google_strength", 0.0)
        base += 0.12 * features.get("buy_sell_ratio", 0.0)
        base += 0.10 * features.get("tx_flow", 0.0)
        base += 0.08 * features.get("liq_log", 0.0)
        base += 0.08 * features.get("vol_log", 0.0)
        base += 0.08 * features.get("new_meme_quality", 0.0)
        base += 0.08 * features.get("new_meme_instant", 0.0)
        base += 0.06 * features.get("is_pump_fun", 0.0)
        base += 0.08 * features.get("smart_wallet_score", 0.0)
        base += 0.03 * features.get("transfer_diversity", 0.0)
        base -= 0.07 * features.get("spread_proxy", 0.0)
        base -= 0.09 * features.get("noise_penalty", 0.0)
        base -= 0.10 * features.get("holder_risk", 0.0)
        return _clamp(base, 0.0, 1.0)

    def _build_reason(
        self,
        features: dict[str, float],
        score: float,
        trend_hit: int,
        trader_hits: int,
        model_id: str,
        grade: str,
    ) -> str:
        tags: list[str] = [self._display_model_name(model_id), f"등급{grade}"]
        if trend_hit:
            tags.append("트렌드")
        if trader_hits > 0:
            tags.append(f"트레이더{trader_hits}")
        if features.get("news_strength", 0.0) >= 0.25:
            tags.append("뉴스")
        if features.get("community_strength", 0.0) >= 0.20:
            tags.append("커뮤니티")
        if features.get("google_strength", 0.0) >= 0.30:
            tags.append("구글AI")
        if features.get("new_meme_instant", 0.0) > 0:
            tags.append("신규코인")
        if features.get("is_pump_fun", 0.0) > 0.0:
            tags.append("pumpfun")
        if features.get("buy_sell_ratio", 0.0) >= 0.55:
            tags.append("매수우위")
        if features.get("smart_wallet_score", 0.0) >= 0.65:
            tags.append("지갑패턴양호")
        if features.get("holder_risk", 0.0) >= 0.65:
            tags.append("지갑집중주의")
        return f"score={score:.2f} " + ",".join(tags[:5])

    @staticmethod
    def _meme_volatility_proxy(features: dict[str, float]) -> float:
        spread = float(features.get("spread_proxy") or 0.0)
        noise = float(features.get("noise_penalty") or 0.0)
        freshness = float(features.get("age_freshness") or 0.0)
        return _clamp((0.45 * spread) + (0.25 * noise) + (0.30 * freshness), 0.0, 1.0)

    def _crypto_volatility_proxy(self, symbol: str) -> float:
        series_5m: list[float] = []
        try:
            series_5m = self.macro.fetch_binance_5m_closes(
                symbol,
                limit=240,
                cache_seconds=max(60, min(240, int(self.settings.scan_interval_seconds * 3))),
                binance_api_key=self.settings.binance_api_key,
            )
        except Exception:
            series_5m = []
        base_series = self._compress_close_series(series_5m, 3) if len(series_5m) >= 24 else series_5m
        if not base_series:
            meta = dict(self._macro_meta.get(symbol) or {})
            chg1h = abs(float(meta.get("change_1h") or 0.0) / 100.0)
            chg24h = abs(float(meta.get("change_24h") or 0.0) / 100.0)
            atr_proxy = _clamp((chg1h * 0.55) + (chg24h * 0.18), 0.0, 0.18)
            return _clamp(atr_proxy / 0.08, 0.0, 1.0)
        ind = self._crypto_indicators(symbol, series=base_series)
        atr_pct = float(ind.get("atr_pct") or 0.0)
        return _clamp(atr_pct / 0.08, 0.0, 1.0)

    def _compute_risk_profile(self, model_id: str, market: str, volatility: float) -> tuple[float, float]:
        base_tp = float(self.settings.take_profit_pct)
        base_sl = float(self.settings.stop_loss_pct)
        vol = _clamp(volatility, 0.0, 1.0)

        if market == "meme":
            if model_id == "A":  # topdogri quality hybrid
                tp_mul, sl_mul = 1.10, 0.80
            elif model_id == "B":  # long-hold bias
                tp_mul, sl_mul = 2.20, 1.40
            else:  # short-term scalp
                tp_mul, sl_mul = 1.05, 0.86
            tp = base_tp * tp_mul * (0.85 + (1.15 * vol))
            sl = base_sl * sl_mul * (0.80 + (0.95 * vol))
            return (_clamp(sl, 0.015, 0.30), _clamp(tp, 0.05, 0.80))

        with self._lock:
            run = (self.state.model_runs or {}).get(self._market_run_key("crypto", model_id)) or {}
        tune = self._read_model_runtime_tune_from_run(run if isinstance(run, dict) else {}, model_id, int(time.time()))
        if model_id == "A":  # strict quality trend
            tp_mul = float(tune.get("tp_mul") or 1.00)
            sl_mul = float(tune.get("sl_mul") or 0.82)
            tp = base_tp * tp_mul * (0.84 + (0.64 * vol))
            sl = base_sl * sl_mul * (0.72 + (0.66 * vol))
            return (_clamp(sl, 0.008, 0.12), _clamp(tp, 0.03, 0.28))
        if model_id == "B":  # trend-flow balance
            tp_mul = float(tune.get("tp_mul") or 1.24)
            sl_mul = float(tune.get("sl_mul") or 1.02)
            tp = base_tp * tp_mul * (0.92 + (0.85 * vol))
            sl = base_sl * sl_mul * (0.86 + (0.98 * vol))
            return (_clamp(sl, 0.010, 0.22), _clamp(tp, 0.04, 0.46))
        # C: aggressive momentum capture
        tp_mul = float(tune.get("tp_mul") or 1.44)
        sl_mul = float(tune.get("sl_mul") or 1.18)
        tp = base_tp * tp_mul * (1.00 + (1.35 * vol))
        sl = base_sl * sl_mul * (0.95 + (1.25 * vol))
        return (_clamp(sl, 0.012, 0.32), _clamp(tp, 0.05, 0.78))

    def _demo_order_pct_for_entry(self, market: str, score: float, threshold: float) -> float:
        min_pct = _clamp(float(self.settings.demo_order_pct_min), 0.01, 0.95)
        max_pct = _clamp(float(self.settings.demo_order_pct_max), min_pct, 0.95)
        gap = float(score) - float(threshold)
        # Meme score is 0~1, crypto score is around -0.26~0.26.
        scale = 0.30 if str(market).lower() == "meme" else 0.10
        confidence = _clamp(gap / max(1e-6, scale), 0.0, 1.0)
        return float(min_pct + ((max_pct - min_pct) * confidence))

    @staticmethod
    def _record_last_entry_alloc(
        run: dict[str, Any],
        market: str,
        symbol: str,
        order_pct: float,
        score: float,
        ts: int,
    ) -> None:
        key = "meme" if str(market).lower() == "meme" else "crypto"
        row = {
            "ts": int(ts),
            "symbol": str(symbol or ""),
            "order_pct": float(order_pct),
            "score": float(score),
        }
        obj = dict(run.get("last_entry_alloc") or {})
        obj[key] = row
        run["last_entry_alloc"] = obj

    @staticmethod
    def _fmt_last_entry_alloc(row: dict[str, Any], now_ts: int) -> str:
        if not isinstance(row, dict):
            return "-"
        ts = int(row.get("ts") or 0)
        if ts <= 0:
            return "-"
        symbol = str(row.get("symbol") or "-")
        pct = float(row.get("order_pct") or 0.0) * 100.0
        score = float(row.get("score") or 0.0)
        age_min = max(0, int((int(now_ts) - ts) // 60))
        return f"{pct:.1f}%({symbol}, {age_min}m전, score={score:.3f})"

    @staticmethod
    def _prune_run_trades(run: dict[str, Any], now_ts: int | None = None) -> None:
        rows = list(run.get("trades") or [])
        if not rows:
            run["trades"] = []
            return
        now = int(now_ts or int(time.time()))
        cutoff = int(now - RUN_TRADE_HISTORY_MAX_AGE_SECONDS)
        kept = [r for r in rows if int((r or {}).get("ts") or now) >= cutoff]
        if len(kept) > RUN_TRADE_HISTORY_LIMIT:
            kept = kept[-RUN_TRADE_HISTORY_LIMIT:]
        run["trades"] = kept

    def _resolve_price(self, token_address: str) -> float:
        price = float(self._last_prices.get(token_address) or 0.0)
        if price > 0:
            return price
        try:
            snap = self.dex.fetch_snapshot_for_token(self.settings.dex_chain, token_address)
        except Exception:
            snap = None
        if snap and snap.price_usd > 0:
            self._last_prices[token_address] = float(snap.price_usd)
            return float(snap.price_usd)
        return 0.0

    def _evaluate_model_memecoin_exits(self, model_id: str, run: dict[str, Any]) -> None:
        now = int(time.time())
        for pos in list((run.get("meme_positions") or {}).values()):
            token_address = str(pos.get("token_address") or "")
            symbol = str(pos.get("symbol") or "")
            if not self._is_memecoin_token(symbol, symbol, token_address):
                forced_price = self._resolve_price(token_address)
                if forced_price > 0:
                    self._close_model_memecoin_position(model_id, run, pos, forced_price, "non_meme_universe_filter")
                continue

            current_price = self._resolve_price(token_address)
            entry = float(pos.get("avg_price_usd") or 0.0)
            if current_price <= 0 or entry <= 0:
                continue
            pnl_pct = (current_price - entry) / entry
            tp_pct = float(pos.get("tp_pct") or self.settings.take_profit_pct)
            sl_pct = float(pos.get("sl_pct") or self.settings.stop_loss_pct)
            strategy = str(pos.get("strategy") or "scalp").lower()
            peak = float(pos.get("peak_price_usd") or entry)
            if current_price > peak:
                peak = current_price
                pos["peak_price_usd"] = peak

            if strategy == "swing":
                hold_until_ts = int(pos.get("hold_until_ts") or 0)
                trail_pct = float(pos.get("trailing_stop_pct") or self.settings.meme_swing_trailing_stop_pct)
                if pnl_pct <= -sl_pct:
                    self._close_model_memecoin_position(model_id, run, pos, current_price, f"Swing SL {pnl_pct * 100:.2f}%")
                elif pnl_pct >= tp_pct:
                    self._close_model_memecoin_position(model_id, run, pos, current_price, f"Swing TP {pnl_pct * 100:.2f}%")
                elif peak > entry and current_price <= (peak * (1.0 - trail_pct)) and pnl_pct >= 0.10:
                    self._close_model_memecoin_position(
                        model_id,
                        run,
                        pos,
                        current_price,
                        f"Swing trailing-stop {pnl_pct * 100:.2f}%",
                    )
                elif hold_until_ts > 0 and now >= hold_until_ts and pnl_pct >= -0.02:
                    self._close_model_memecoin_position(
                        model_id,
                        run,
                        pos,
                        current_price,
                        f"Swing horizon-end {pnl_pct * 100:.2f}%",
                    )
            else:
                if pnl_pct >= tp_pct:
                    self._close_model_memecoin_position(model_id, run, pos, current_price, f"TP {pnl_pct * 100:.2f}%")
                elif pnl_pct <= -sl_pct:
                    self._close_model_memecoin_position(model_id, run, pos, current_price, f"SL {pnl_pct * 100:.2f}%")

    def _close_model_memecoin_position(
        self,
        model_id: str,
        run: dict[str, Any],
        pos: dict[str, Any],
        price_usd: float,
        reason: str,
    ) -> bool:
        token_address = str(pos.get("token_address") or "")
        if not token_address:
            return False
        positions = run.get("meme_positions") or {}
        if token_address not in positions:
            return False

        qty = float(pos.get("qty") or 0.0)
        avg = float(pos.get("avg_price_usd") or 0.0)
        notional = qty * price_usd
        pnl_usd = (price_usd - avg) * qty
        pnl_pct = pnl_usd / max(0.0001, avg * qty)

        del positions[token_address]
        run["meme_positions"] = positions
        run["meme_cash_usd"] = float(run.get("meme_cash_usd") or 0.0) + notional
        run.setdefault("trades", []).append(
            {
                "ts": int(time.time()),
                "source": "memecoin",
                "side": "sell",
                "symbol": str(pos.get("symbol") or ""),
                "token_address": token_address,
                "qty": qty,
                "price_usd": price_usd,
                "notional_usd": notional,
                "pnl_usd": pnl_usd,
                "pnl_pct": pnl_pct,
                "reason": reason,
                "model_id": model_id,
            }
        )
        self._prune_run_trades(run, int(time.time()))

        if model_id == "A":
            entry_features = dict(pos.get("entry_features") or {})
            if entry_features:
                self.model.update(entry_features, pnl_pct)
            self._push_alert(
                "trade",
                f"[{self._display_model_name('A', 'meme')}] {pos.get('symbol')} 청산",
                f"{reason} | PNL {pnl_usd:+.2f} USD ({pnl_pct * 100:+.2f}%)",
                send_telegram=True,
            )
        return True

    def _execute_model_memecoin_entries(
        self,
        model_id: str,
        run: dict[str, Any],
        signals: list[dict[str, Any]],
    ) -> None:
        now = int(time.time())
        opened = 0
        cooldown = self.settings.signal_cooldown_minutes * 60
        min_entry_rank = self._meme_min_entry_rank_for_model(model_id)
        guard = self._entry_guard_profile(model_id, "meme")
        guard_boost = float(guard.get("threshold_boost") or 0.0)
        min_score = _clamp(self._variant_threshold(model_id) + guard_boost, 0.0, 0.99)
        max_open_cycle = self.settings.max_signals_per_cycle
        max_open_positions = max(1, int(self.settings.meme_max_positions))
        if bool(guard.get("active")):
            max_open_cycle = max(1, min(max_open_cycle, 1))
        for signal in signals:
            if opened >= max_open_cycle:
                break
            if len(run.get("meme_positions") or {}) >= max_open_positions:
                break
            token: TokenSnapshot = signal["token"]
            token_address = token.token_address
            if token_address in (run.get("meme_positions") or {}):
                continue
            grade = str(signal.get("grade") or "G").upper()
            reason_text = str(signal.get("reason") or "")
            is_demo_fallback = self.settings.trade_mode == "paper" and ("데모폴백" in reason_text)
            score_now = float(signal.get("score") or 0.0)
            if score_now < min_score:
                continue
            if is_demo_fallback and not bool(guard.get("allow_demo_fallback", True)):
                continue
            if self._grade_rank(grade) > min_entry_rank and not is_demo_fallback:
                continue
            sym = token.symbol.upper()
            last_ts = int((run.get("last_signal_ts") or {}).get(sym, 0))
            if (now - last_ts) < cooldown:
                continue

            cash = float(run.get("meme_cash_usd") or 0.0)
            order_pct = self._demo_order_pct_for_entry("meme", score_now, min_score)
            order_usd = min(cash, max(5.0, cash * order_pct))
            if order_usd < 5.0:
                continue
            qty = order_usd / max(0.0000001, float(token.price_usd))
            features = dict(signal.get("features") or {})
            vol = self._meme_volatility_proxy(features)
            sl_pct, tp_pct = self._compute_risk_profile(model_id, "meme", vol)
            strategy = self._meme_strategy_for_model(model_id, grade, features)
            if is_demo_fallback and model_id in {"A", "B"}:
                strategy = "swing"
            swing = strategy == "swing"
            hold_until_ts = 0
            trailing_stop_pct = 0.0
            if swing:
                target_pct = max(tp_pct, float(self.settings.meme_swing_target_multiple) - 1.0)
                if model_id == "B":
                    tp_pct = _clamp(target_pct, 0.40, 2999.0)
                else:
                    tp_pct = _clamp(target_pct, 0.25, 99.0)
                sl_pct = _clamp(max(sl_pct, 0.16), 0.06, 0.60)
                hold_days = int(self.settings.meme_swing_hold_days)
                if model_id == "B":
                    hold_days = max(14, hold_days)
                hold_until_ts = now + hold_days * 86400
                trailing_stop_pct = float(self.settings.meme_swing_trailing_stop_pct)
                if model_id == "B":
                    trailing_stop_pct = max(0.40, trailing_stop_pct)

            run.setdefault("meme_positions", {})[token_address] = {
                "token_address": token_address,
                "symbol": token.symbol,
                "qty": qty,
                "avg_price_usd": float(token.price_usd),
                "opened_at": now,
                "entry_score": float(signal["score"]),
                "grade": grade,
                "tp_pct": tp_pct,
                "sl_pct": sl_pct,
                "strategy": strategy,
                "hold_until_ts": hold_until_ts,
                "trailing_stop_pct": trailing_stop_pct,
                "peak_price_usd": float(token.price_usd),
                "reason": str(signal["reason"]),
                "order_pct": float(order_pct),
                "entry_features": features,
            }
            run["meme_cash_usd"] = cash - order_usd
            run.setdefault("last_signal_ts", {})[sym] = now
            run.setdefault("trades", []).append(
                {
                    "ts": now,
                    "source": "memecoin",
                    "side": "buy",
                    "symbol": token.symbol,
                    "token_address": token_address,
                    "qty": qty,
                    "price_usd": float(token.price_usd),
                    "notional_usd": order_usd,
                    "order_pct": float(order_pct),
                    "pnl_usd": 0.0,
                    "pnl_pct": 0.0,
                    "reason": f"{strategy}|alloc={order_pct*100:.1f}%|{reason_text}",
                    "model_id": model_id,
                }
            )
            self._record_last_entry_alloc(run, "meme", token.symbol, order_pct, score_now, now)
            self._prune_run_trades(run, now)
            opened += 1

            if model_id == "A":
                self._push_alert(
                    "trade",
                    f"[{self._display_model_name('A', 'meme')}] {token.symbol} 진입",
                    (
                        f"{order_usd:.2f} USD | 배분 {order_pct*100:.1f}% | {strategy} | {signal.get('grade','G')} | score={float(signal['score']):.2f} | "
                        f"TP {tp_pct*100:.1f}% / SL {sl_pct*100:.1f}% | {signal['reason']}"
                    ),
                    send_telegram=True,
                )

    def _macro_trend_score(
        self,
        base_symbol: str,
        row: dict[str, Any],
        trend_bundle: dict[str, Any],
    ) -> tuple[float, int]:
        sym = str(base_symbol or "").upper()
        trending = set(str(s).upper() for s in list(trend_bundle.get("trending") or set()) if str(s).strip())
        trader = int((trend_bundle.get("trader_counts") or {}).get(sym, 0))
        news = int((trend_bundle.get("news_counts") or {}).get(sym, 0))
        community = int((trend_bundle.get("community_counts") or {}).get(sym, 0))
        google = int((trend_bundle.get("google_counts") or {}).get(sym, 0))
        trend_hits = trader + news + community + google + (2 if sym in trending else 0)
        rank = int(row.get("market_cap_rank") or 0)
        if rank <= 0:
            rank = 10000
        rank_quality = _clamp((600.0 - float(rank)) / 600.0, 0.0, 1.0)
        vol = float(row.get("volume_24h") or 0.0)
        vol_quality = _clamp(math.log10(max(1.0, vol)) / 11.0, 0.0, 1.0)
        trend_score = (
            (2.0 if sym in trending else 0.0)
            + min(2.0, float(trader) * 0.5)
            + min(2.0, float(news) * 0.5)
            + min(1.5, float(community) * 0.3)
            + min(2.5, float(google) * 0.8)
        )
        score = float(trend_score + (1.6 * rank_quality) + (0.8 * vol_quality))
        return (score, int(trend_hits))

    def _refresh_macro_trend_pool(
        self,
        rows: list[dict[str, Any]],
        trend_bundle: dict[str, Any],
        now_ts: int,
    ) -> set[str]:
        pool_size = max(5, int(self.settings.macro_trend_pool_size))
        refresh_sec = max(900, int(self.settings.macro_trend_reselect_seconds))
        if self._macro_trend_pool and now_ts < int(self._macro_trend_pool_next_refresh_ts):
            return set(self._macro_trend_pool)
        if not rows:
            return set(self._macro_trend_pool or [])

        scored: list[dict[str, Any]] = []
        for row in rows:
            base_symbol = str(row.get("symbol") or "").upper().strip()
            if not base_symbol:
                continue
            symbol = f"{base_symbol}USDT"
            rank = int(row.get("market_cap_rank") or 0)
            if rank <= 0:
                rank = 10000
            score, hits = self._macro_trend_score(base_symbol, row, trend_bundle)
            scored.append(
                {
                    "symbol": symbol,
                    "rank": rank,
                    "score": float(score),
                    "hits": int(hits),
                }
            )
        if not scored:
            return set(self._macro_trend_pool or [])

        hot = [r for r in scored if int(r["hits"]) > 0]
        cold = [r for r in scored if int(r["hits"]) <= 0]
        hot.sort(key=lambda r: (-int(r["hits"]), -float(r["score"]), int(r["rank"])))
        cold.sort(key=lambda r: (-float(r["score"]), int(r["rank"])))
        selected: list[str] = []
        for row in hot:
            selected.append(str(row["symbol"]))
            if len(selected) >= pool_size:
                break
        if len(selected) < pool_size:
            for row in cold:
                sym = str(row["symbol"])
                if sym in selected:
                    continue
                selected.append(sym)
                if len(selected) >= pool_size:
                    break
        if not selected:
            selected = [str(r["symbol"]) for r in scored[:pool_size]]

        self._macro_trend_pool = selected[:pool_size]
        self._macro_trend_pool_next_refresh_ts = int(now_ts + refresh_sec)
        return set(self._macro_trend_pool)

    def _fetch_macro_demo_prices(self, trend_bundle: dict[str, Any] | None = None) -> dict[str, float]:
        trend_data = dict(trend_bundle or {})
        now_ts = int(time.time())
        prices: dict[str, float] = {}
        meta: dict[str, dict[str, Any]] = {}
        rt_prices: dict[str, float] = {}
        rt_meta: dict[str, dict[str, Any]] = {}
        try:
            rt_prices, rt_meta = self.macro.fetch_realtime_quotes(
                sources_csv=self.settings.macro_realtime_sources,
                cache_seconds=self.settings.macro_realtime_cache_seconds,
                binance_api_key=self.settings.binance_api_key,
                binance_api_secret=self.settings.binance_api_secret,
            )
        except Exception:
            rt_prices, rt_meta = {}, {}
        try:
            rows = self.macro.fetch_top_markets(
                limit=self.settings.macro_top_n,
                source=self.settings.macro_universe_source,
                cmc_api_key=self.settings.cmc_api_key,
                coingecko_api_key=self.settings.coingecko_api_key,
            )
        except Exception as exc:  # noqa: BLE001
            if rt_prices:
                rows = []
            else:
                if not self.bybit.enabled:
                    with self._lock:
                        self.state.bybit_error = f"macro_fetch_failed: {exc}"
                return {}

        selected_symbols = self._refresh_macro_trend_pool(rows, trend_data, now_ts)
        if not selected_symbols and rows:
            default_limit = max(5, int(self.settings.macro_trend_pool_size))
            fallback_symbols: list[str] = []
            for row in rows[:default_limit]:
                base_symbol = str(row.get("symbol") or "").upper().strip()
                if not base_symbol:
                    continue
                fallback_symbols.append(f"{base_symbol}USDT")
            self._macro_trend_pool = fallback_symbols[:default_limit]
            self._macro_trend_pool_next_refresh_ts = int(now_ts + int(self.settings.macro_trend_reselect_seconds))
            selected_symbols = set(self._macro_trend_pool)

        for row in rows:
            base_symbol = str(row.get("symbol") or "").upper().strip()
            if not base_symbol:
                continue
            symbol = f"{base_symbol}USDT"
            if selected_symbols and symbol not in selected_symbols:
                continue
            if symbol in prices:
                continue
            price = float(rt_prices.get(symbol) or row.get("price_usd") or 0.0)
            if price <= 0:
                continue
            rt_row = dict(rt_meta.get(symbol) or {})
            row_volume = float(row.get("volume_24h") or 0.0)
            rt_volume = float(rt_row.get("volume_24h") or 0.0)
            prices[symbol] = price
            meta[symbol] = {
                "change_1h": float(row.get("change_1h") or 0.0),
                "change_24h": float(rt_row.get("change_24h") or row.get("change_24h") or 0.0),
                "volume_24h": max(row_volume, rt_volume),
                "market_cap": float(row.get("market_cap") or 0.0),
                "market_cap_rank": int(row.get("market_cap_rank") or 0),
                "source": str(row.get("source") or ""),
                "realtime_source": str(rt_row.get("realtime_source") or ""),
            }
            self._bybit_last_prices[symbol] = price
            hist = self._bybit_price_history.get(symbol) or []
            hist.append(price)
            if len(hist) > 240:
                hist = hist[-240:]
                self._bybit_price_history[symbol] = hist
        if not prices and rt_prices:
            rt_rows = list(rt_prices.items())
            if selected_symbols:
                rt_rows = [(sym, px) for sym, px in rt_rows if sym in selected_symbols]
            for idx, (symbol, price) in enumerate(rt_rows[: max(10, int(self.settings.macro_trend_pool_size))]):
                if price <= 0:
                    continue
                prices[symbol] = float(price)
                rt_row = dict(rt_meta.get(symbol) or {})
                prev_rank = int((self._macro_meta.get(symbol) or {}).get("market_cap_rank") or 0)
                if prev_rank <= 0:
                    prev_rank = 400 + idx
                meta[symbol] = {
                    "change_1h": 0.0,
                    "change_24h": float(rt_row.get("change_24h") or 0.0),
                    "volume_24h": float(rt_row.get("volume_24h") or 0.0),
                    "market_cap": 0.0,
                    "market_cap_rank": int(prev_rank),
                    "source": "realtime_only",
                    "realtime_source": str(rt_row.get("realtime_source") or ""),
                }
                self._bybit_last_prices[symbol] = float(price)
                hist = self._bybit_price_history.get(symbol) or []
                hist.append(float(price))
                if len(hist) > 240:
                    hist = hist[-240:]
                self._bybit_price_history[symbol] = hist
        self._macro_meta = meta
        if not self.bybit.enabled:
            with self._lock:
                self.state.bybit_error = ""
        return prices

    def _crypto_symbol_allowed_for_model(self, model_id: str, symbol: str) -> bool:
        meta = dict(self._macro_meta.get(symbol) or {})
        rank = int(meta.get("market_cap_rank") or 0)
        if rank <= 0:
            return False
        profile = dict(CRYPTO_MODEL_GATE_DEFAULTS.get(model_id) or CRYPTO_MODEL_GATE_DEFAULTS["B"])
        rank_max = int(profile.get("rank_max") or 500)
        if rank > rank_max:
            return False
        if bool(profile.get("smallcap_trend_only")) and rank > 220:
            trend_pool = {str(v).upper() for v in list(self._macro_trend_pool or [])}
            if str(symbol).upper() not in trend_pool:
                return False
        return True

    @staticmethod
    def _safe_return(now_price: float, prev_price: float) -> float:
        p_now = float(now_price or 0.0)
        p_prev = float(prev_price or 0.0)
        if p_now <= 0 or p_prev <= 0:
            return 0.0
        return (p_now / p_prev) - 1.0

    @staticmethod
    def _compress_close_series(values: list[float], step: int) -> list[float]:
        if not values:
            return []
        s = max(1, int(step))
        if s <= 1:
            return [float(v) for v in values]
        out_rev: list[float] = []
        idx = len(values) - 1
        while idx >= 0:
            out_rev.append(float(values[idx]))
            idx -= s
        out_rev.reverse()
        return out_rev

    def _series_return(self, values: list[float], bars_back: int = 1) -> float:
        n = max(1, int(bars_back))
        if len(values) <= n:
            return 0.0
        return self._safe_return(float(values[-1]), float(values[-1 - n]))

    @staticmethod
    def _social_heat(base_sym: str, trend_bundle: dict[str, Any]) -> float:
        sym = str(base_sym or "").upper()
        trend_hit = 1.0 if sym in set(trend_bundle.get("trending") or set()) else 0.0
        news_hit = _clamp(float((trend_bundle.get("news_counts") or {}).get(sym, 0)) / 5.0, 0.0, 1.0)
        community_hit = _clamp(float((trend_bundle.get("community_counts") or {}).get(sym, 0)) / 8.0, 0.0, 1.0)
        google_hit = _clamp(float((trend_bundle.get("google_counts") or {}).get(sym, 0)) / 4.0, 0.0, 1.0)
        return _clamp(
            (0.30 * trend_hit) + (0.20 * news_hit) + (0.20 * community_hit) + (0.30 * google_hit),
            0.0,
            1.0,
        )

    def _crypto_feature_pack(self, symbol: str, trend_bundle: dict[str, Any]) -> dict[str, float]:
        hist_tick = [float(v) for v in list(self._bybit_price_history.get(symbol) or []) if float(v) > 0]
        meta = dict(self._macro_meta.get(symbol) or {})
        chg1h = _clamp(float(meta.get("change_1h") or 0.0) / 100.0, -0.40, 0.40)
        chg24h = _clamp(float(meta.get("change_24h") or 0.0) / 100.0, -0.90, 0.90)
        volume_24h = float(meta.get("volume_24h") or 0.0)
        market_cap = float(meta.get("market_cap") or 0.0)
        rank = float(meta.get("market_cap_rank") or 0.0)

        tf_5m: list[float] = []
        try:
            tf_5m = self.macro.fetch_binance_5m_closes(
                symbol,
                limit=360,
                cache_seconds=max(60, min(240, int(self.settings.scan_interval_seconds * 3))),
                binance_api_key=self.settings.binance_api_key,
            )
        except Exception:
            tf_5m = []
        timeframe_source = "binance_5m"
        if len(tf_5m) < 20:
            timeframe_source = "macro_fallback"
            p_now = float(self._bybit_last_prices.get(symbol) or (hist_tick[-1] if hist_tick else 0.0))
            if p_now > 0.0:
                base_24h = p_now / max(0.10, (1.0 + chg24h))
                synthetic: list[float] = []
                for i in range(24):
                    alpha = float(i) / 23.0
                    synthetic.append((base_24h * (1.0 - alpha)) + (p_now * alpha))
                tf_5m = synthetic

        tf_15m = self._compress_close_series(tf_5m, 3)
        tf_1h = self._compress_close_series(tf_5m, 12)
        tf_4h = self._compress_close_series(tf_5m, 48)
        tf_1d = self._compress_close_series(tf_5m, 288)

        ret_5m = self._series_return(tf_5m, 1)
        ret_15m = self._series_return(tf_15m, 1)
        ret_1h = self._series_return(tf_1h, 1)
        ret_4h = self._series_return(tf_4h, 1)
        ret_1d = self._series_return(tf_1d, 1)

        if len(tf_1h) < 2:
            ret_1h = chg1h
        if len(tf_4h) < 2:
            ret_4h = chg24h * 0.55
        if len(tf_1d) < 2:
            ret_1d = chg24h

        ret_5m = _clamp(ret_5m, -0.15, 0.15)
        ret_15m = _clamp(ret_15m, -0.22, 0.22)
        ret_1h = _clamp(ret_1h, -0.45, 0.45)
        ret_4h = _clamp(ret_4h, -0.80, 0.80)
        ret_1d = _clamp(ret_1d, -1.20, 1.20)

        edge_5m = _clamp(ret_5m / 0.020, -1.0, 1.0)
        edge_15m = _clamp(ret_15m / 0.030, -1.0, 1.0)
        edge_1h = _clamp(ret_1h / 0.060, -1.0, 1.0)
        edge_4h = _clamp(ret_4h / 0.120, -1.0, 1.0)
        edge_1d = _clamp(ret_1d / 0.200, -1.0, 1.0)
        trend_stack = _clamp(
            (0.10 * edge_5m) + (0.18 * edge_15m) + (0.27 * edge_1h) + (0.25 * edge_4h) + (0.20 * edge_1d),
            -1.0,
            1.0,
        )

        liquidity_quality = _clamp(math.log10(max(1.0, volume_24h)) / 10.0, 0.0, 1.0)
        cap_quality = _clamp(math.log10(max(1.0, market_cap)) / 12.0, 0.0, 1.0)
        rank_quality = _clamp((1200.0 - rank) / 1200.0, 0.0, 1.0) if rank > 0 else 0.0
        base_sym = symbol.replace("USDT", "").replace("USD", "")
        social_heat = self._social_heat(base_sym, trend_bundle)
        ind_base = tf_15m if len(tf_15m) >= 8 else tf_5m
        ind = self._crypto_indicators(symbol, series=ind_base)

        ema_signal = float(ind.get("ema_signal") or 0.0)
        cci_signal = float(ind.get("cci_signal") or 0.0)
        cci_raw = float(ind.get("cci_raw") or 0.0)
        atr_pct = float(ind.get("atr_pct") or 0.0)
        atr_penalty = float(ind.get("atr_penalty") or 0.0)
        rsi = float(ind.get("rsi") or 50.0)
        breakout_strength = float(ind.get("breakout_strength") or 0.0)
        pullback_from_high = float(ind.get("pullback_from_high") or 0.0)
        ema_gap_pct = float(ind.get("ema_gap_pct") or 0.0)
        if len(ind_base) < 8:
            ema_signal = _clamp(0.5 + (ret_15m * 4.5) + (ret_1h * 2.0), 0.0, 1.0)
            ema_gap_pct = (ema_signal - 0.5) / 18.0
            cci_raw = _clamp((ret_15m * 900.0) + (ret_1h * 260.0), -220.0, 220.0)
            cci_signal = _clamp((cci_raw + 200.0) / 400.0, 0.0, 1.0)
            rsi = _clamp(50.0 + (ret_15m * 450.0) + (ret_1h * 220.0), 8.0, 92.0)
            atr_pct = _clamp((abs(ret_15m) * 0.55) + (abs(ret_1h) * 0.25), 0.004, 0.18)
            atr_penalty = _clamp((atr_pct - 0.008) / 0.06, 0.0, 1.0)
            breakout_strength = _clamp((max(0.0, ret_15m) / 0.03) + (max(0.0, ret_1h) / 0.08), 0.0, 1.0)
            pullback_from_high = _clamp(max(0.0, -ret_15m) / 0.04, 0.0, 1.0)

        ema_edge = _clamp((ema_signal - 0.5) * 2.0, -1.0, 1.0)
        cci_edge = _clamp((cci_signal - 0.5) * 2.0, -1.0, 1.0)
        pullback_mom = _clamp((-ret_15m) / 0.04, 0.0, 1.0)
        rsi_rebound = _clamp((55.0 - rsi) / 25.0, 0.0, 1.0)
        cci_rebound = _clamp((-cci_raw) / 180.0, 0.0, 1.0)
        quality_score = _clamp((0.45 * rank_quality) + (0.30 * liquidity_quality) + (0.25 * cap_quality), 0.0, 1.0)
        noise_penalty = _clamp(abs(ret_5m) / 0.07, 0.0, 1.0)
        overheat_penalty = _clamp(max(0.0, abs(chg24h) - 0.25) / 0.40, 0.0, 1.0)
        return {
            "history_points": float(len(tf_5m)),
            "timeframe_source": timeframe_source,
            "timeframe_points_5m": float(len(tf_5m)),
            "timeframe_points_15m": float(len(tf_15m)),
            "timeframe_points_1h": float(len(tf_1h)),
            "timeframe_points_4h": float(len(tf_4h)),
            "timeframe_points_1d": float(len(tf_1d)),
            "ret_5m": ret_5m,
            "ret_15m": ret_15m,
            "ret_1h": ret_1h,
            "ret_4h": ret_4h,
            "ret_1d": ret_1d,
            "edge_5m": edge_5m,
            "edge_15m": edge_15m,
            "edge_1h": edge_1h,
            "edge_4h": edge_4h,
            "edge_1d": edge_1d,
            "trend_stack": trend_stack,
            # Backward compatibility aliases
            "mom1": ret_5m,
            "mom4": ret_1h,
            "mom12": ret_1d,
            "chg1h": chg1h,
            "chg24h": chg24h,
            "market_cap_rank": float(rank),
            "liquidity_quality": liquidity_quality,
            "cap_quality": cap_quality,
            "rank_quality": rank_quality,
            "social_heat": social_heat,
            "ema_signal": ema_signal,
            "ema_edge": ema_edge,
            "ema_gap_pct": ema_gap_pct,
            "cci_signal": cci_signal,
            "cci_edge": cci_edge,
            "cci_raw": cci_raw,
            "rsi": rsi,
            "pullback_mom": pullback_mom,
            "rsi_rebound": rsi_rebound,
            "cci_rebound": cci_rebound,
            "breakout_strength": breakout_strength,
            "pullback_from_high": pullback_from_high,
            "atr_pct": atr_pct,
            "atr_penalty": atr_penalty,
            "quality_score": quality_score,
            "noise_penalty": noise_penalty,
            "overheat_penalty": overheat_penalty,
        }

    def _crypto_score_profile(self, model_id: str, symbol: str, trend_bundle: dict[str, Any]) -> dict[str, Any]:
        feats = self._crypto_feature_pack(symbol, trend_bundle)
        feats["edge_5m"] = _clamp(float(feats.get("edge_5m") or 0.0), -1.0, 1.0)
        feats["edge_15m"] = _clamp(float(feats.get("edge_15m") or 0.0), -1.0, 1.0)
        feats["edge_1h"] = _clamp(float(feats.get("edge_1h") or 0.0), -1.0, 1.0)
        feats["edge_4h"] = _clamp(float(feats.get("edge_4h") or 0.0), -1.0, 1.0)
        feats["edge_1d"] = _clamp(float(feats.get("edge_1d") or 0.0), -1.0, 1.0)
        feats["trend_stack"] = _clamp(float(feats.get("trend_stack") or 0.0), -1.0, 1.0)
        hist_points = int(feats.get("history_points") or 0)
        feats["atr_pct"] = _clamp(float(feats.get("atr_pct") or 0.0), 0.0, 0.80)
        feats["atr_penalty"] = _clamp(float(feats.get("atr_penalty") or 0.0), 0.0, 1.0)
        feats["quality_score"] = _clamp(float(feats.get("quality_score") or 0.0), 0.0, 1.0)
        feats["social_heat"] = _clamp(float(feats.get("social_heat") or 0.0), 0.0, 1.0)
        feats["rsi"] = _clamp(float(feats.get("rsi") or 50.0), 0.0, 100.0)
        feats["ema_edge"] = _clamp(float(feats.get("ema_edge") or 0.0), -1.0, 1.0)
        feats["pullback_from_high"] = _clamp(float(feats.get("pullback_from_high") or 0.0), 0.0, 1.0)
        feats["breakout_strength"] = _clamp(float(feats.get("breakout_strength") or 0.0), 0.0, 1.0)
        feats["pullback_mom"] = _clamp(float(feats.get("pullback_mom") or 0.0), 0.0, 1.0)
        feats["rsi_rebound"] = _clamp(float(feats.get("rsi_rebound") or 0.0), 0.0, 1.0)
        feats["cci_rebound"] = _clamp(float(feats.get("cci_rebound") or 0.0), 0.0, 1.0)
        feats["overheat_penalty"] = _clamp(float(feats.get("overheat_penalty") or 0.0), 0.0, 1.0)
        allowed = self._crypto_symbol_allowed_for_model(model_id, symbol)
        threshold = self._bybit_entry_threshold(model_id)
        abs_chg24 = abs(float(feats.get("chg24h") or 0.0))
        if model_id == "A":
            strategy = "A-ReliabilityTrend"
            gate_ok = bool(
                allowed
                and feats["quality_score"] >= 0.66
                and feats["edge_1h"] >= -0.25
                and feats["edge_4h"] >= -0.20
                and feats["edge_1d"] >= -0.15
                and feats["atr_pct"] <= 0.070
                and abs_chg24 <= 0.40
                and (hist_points < 8 or feats["ema_edge"] >= -0.05)
            )
            score = (
                (0.014 * feats["edge_5m"])
                + (0.028 * feats["edge_15m"])
                + (0.048 * feats["edge_1h"])
                + (0.048 * feats["edge_4h"])
                + (0.030 * feats["edge_1d"])
                + (0.024 * feats["ema_edge"])
                + (0.030 * feats["quality_score"])
                + (0.010 * feats["trend_stack"])
                + (0.006 * feats["social_heat"])
                + (0.010 * feats["breakout_strength"])
                - (0.028 * feats["atr_penalty"])
                - (0.016 * feats["noise_penalty"])
                - (0.036 * feats["overheat_penalty"])
            )
            score_lo, score_hi, gate_penalty = -0.180, 0.180, 0.030
            gate_reason = "품질/저변동 + 1h/4h/1d 추세 정합 조건"
        elif model_id == "B":
            strategy = "B-PullbackFlow"
            pullback_or_breakout = bool(
                feats["pullback_from_high"] >= 0.02 or feats["breakout_strength"] >= 0.32
            )
            gate_ok = bool(
                allowed
                and feats["edge_4h"] > -0.75
                and feats["edge_1d"] > -0.80
                and pullback_or_breakout
                and feats["social_heat"] >= 0.03
                and (hist_points < 8 or feats["rsi"] < 82.0)
                and abs_chg24 <= 0.90
            )
            score = (
                (0.030 * feats["edge_15m"])
                + (0.038 * feats["edge_1h"])
                + (0.030 * feats["edge_4h"])
                + (0.018 * feats["edge_1d"])
                - (0.016 * max(0.0, feats["edge_5m"]))
                + (0.030 * feats["pullback_from_high"])
                + (0.026 * feats["pullback_mom"])
                + (0.018 * feats["rsi_rebound"])
                + (0.012 * feats["cci_rebound"])
                + (0.016 * feats["social_heat"])
                + (0.010 * feats["quality_score"])
                + (0.012 * feats["trend_stack"])
                - (0.018 * feats["atr_penalty"])
                - (0.012 * feats["noise_penalty"])
                - (0.022 * feats["overheat_penalty"])
            )
            score_lo, score_hi, gate_penalty = -0.220, 0.220, 0.012
            gate_reason = "눌림/돌파 + 소셜흐름 + 4h/1d 추세 유지 조건"
        else:
            strategy = "C-AggressiveMomentum"
            gate_ok = bool(
                allowed
                and feats["edge_5m"] >= -0.20
                and feats["edge_15m"] >= -0.25
                and feats["breakout_strength"] >= 0.18
                and feats["social_heat"] >= 0.18
                and feats["atr_pct"] <= 0.14
                and abs_chg24 <= 0.90
            )
            score = (
                (0.058 * feats["edge_5m"])
                + (0.052 * feats["edge_15m"])
                + (0.030 * feats["edge_1h"])
                + (0.010 * feats["edge_4h"])
                + (0.014 * feats["edge_1d"])
                + (0.046 * feats["breakout_strength"])
                + (0.016 * feats["ema_edge"])
                + (0.028 * feats["social_heat"])
                + (0.012 * feats["trend_stack"])
                + (0.010 * feats["quality_score"])
                - (0.014 * feats["atr_penalty"])
                - (0.008 * feats["noise_penalty"])
                - (0.012 * feats["overheat_penalty"])
            )
            score_lo, score_hi, gate_penalty = -0.260, 0.260, 0.012
            gate_reason = "5m/15m 가속 + 브레이크아웃 + 소셜히트 조건"
        score = _clamp(score, score_lo, score_hi)
        if not gate_ok:
            score -= gate_penalty
        score = _clamp(score, score_lo, score_hi)
        return {
            "strategy": strategy,
            "threshold": float(threshold),
            "score": float(score),
            "gate_ok": bool(gate_ok),
            "gate_reason": gate_reason,
            "features": feats,
        }

    def _bybit_score(self, model_id: str, symbol: str, trend_bundle: dict[str, Any]) -> float:
        return float(self._crypto_score_profile(model_id, symbol, trend_bundle).get("score") or -1.0)

    @staticmethod
    def _crypto_reason_text(profile: dict[str, Any]) -> str:
        feats = dict(profile.get("features") or {})
        return (
            f"{profile.get('strategy')} | "
            f"5m={float(feats.get('ret_5m') or 0.0)*100:+.2f}% "
            f"15m={float(feats.get('ret_15m') or 0.0)*100:+.2f}% "
            f"1h={float(feats.get('ret_1h') or 0.0)*100:+.2f}% "
            f"4h={float(feats.get('ret_4h') or 0.0)*100:+.2f}% "
            f"1d={float(feats.get('ret_1d') or 0.0)*100:+.2f}% | "
            f"EMA={float(feats.get('ema_signal') or 0.0):.2f} "
            f"RSI={float(feats.get('rsi') or 0.0):.1f} "
            f"CCI={float(feats.get('cci_raw') or 0.0):+.1f} "
            f"ATR={float(feats.get('atr_pct') or 0.0)*100:.2f}% | "
            f"Q={float(feats.get('quality_score') or 0.0):.2f} "
            f"S={float(feats.get('social_heat') or 0.0):.2f} "
            f"T={float(feats.get('trend_stack') or 0.0):+.2f} "
            f"OH={float(feats.get('overheat_penalty') or 0.0):.2f} "
            f"rank={int(feats.get('market_cap_rank') or 0)} "
            f"gate={'Y' if profile.get('gate_ok') else 'N'}"
        )

    @staticmethod
    def _ema(values: list[float], period: int) -> float:
        if not values:
            return 0.0
        n = max(1, int(period))
        alpha = 2.0 / (n + 1.0)
        ema_v = float(values[0])
        for v in values[1:]:
            ema_v = (alpha * float(v)) + ((1.0 - alpha) * ema_v)
        return float(ema_v)

    @staticmethod
    def _atr_pct(values: list[float], period: int = 14) -> float:
        if len(values) < 3:
            return 0.0
        p = max(2, int(period))
        tail = list(values[-(p + 1) :])
        trs: list[float] = []
        for i in range(1, len(tail)):
            prev = float(tail[i - 1])
            cur = float(tail[i])
            if prev <= 0:
                continue
            trs.append(abs(cur - prev) / prev)
        if not trs:
            return 0.0
        return float(sum(trs) / len(trs))

    @staticmethod
    def _cci(values: list[float], period: int = 20) -> float:
        if len(values) < max(5, period):
            return 0.0
        tail = [float(v) for v in values[-period:]]
        sma = sum(tail) / float(len(tail))
        mean_dev = sum(abs(v - sma) for v in tail) / float(len(tail))
        if mean_dev <= 0:
            return 0.0
        return (tail[-1] - sma) / (0.015 * mean_dev)

    @staticmethod
    def _rsi(values: list[float], period: int = 14) -> float:
        if len(values) < max(3, period + 1):
            return 50.0
        p = max(2, int(period))
        tail = [float(v) for v in values[-(p + 1) :]]
        gains = 0.0
        losses = 0.0
        for i in range(1, len(tail)):
            diff = float(tail[i] - tail[i - 1])
            if diff > 0:
                gains += diff
            elif diff < 0:
                losses += abs(diff)
        if losses <= 0:
            return 100.0 if gains > 0 else 50.0
        rs = gains / losses
        return 100.0 - (100.0 / (1.0 + rs))

    def _crypto_indicators(self, symbol: str, series: list[float] | None = None) -> dict[str, float]:
        base = list(series or [])
        if base:
            hist = [float(v) for v in base if float(v) > 0]
        else:
            hist = [float(v) for v in list(self._bybit_price_history.get(symbol) or []) if float(v) > 0]
        if len(hist) < 8:
            return {
                "ema_signal": 0.0,
                "ema_gap_pct": 0.0,
                "cci_signal": 0.0,
                "cci_raw": 0.0,
                "rsi": 50.0,
                "atr_pct": 0.0,
                "atr_penalty": 0.0,
                "breakout_strength": 0.0,
                "pullback_from_high": 0.0,
            }
        ema_fast = self._ema(hist[-55:], 9)
        ema_slow = self._ema(hist[-89:], 21)
        ema_signal = 0.0
        ema_gap_pct = 0.0
        if ema_slow > 0:
            ema_gap_pct = (ema_fast / ema_slow) - 1.0
            ema_signal = _clamp((ema_gap_pct * 18.0) + 0.5, 0.0, 1.0)
        cci = self._cci(hist, 20)
        cci_signal = _clamp((cci + 200.0) / 400.0, 0.0, 1.0)
        rsi = _clamp(self._rsi(hist, 14), 0.0, 100.0)
        atr_pct = self._atr_pct(hist, 14)
        atr_penalty = _clamp((atr_pct - 0.008) / 0.06, 0.0, 1.0)
        current = float(hist[-1])
        prev_window = [float(v) for v in hist[-21:-1]] if len(hist) >= 22 else [float(v) for v in hist[:-1]]
        high_prev = max(prev_window) if prev_window else current
        breakout_strength = 0.0
        if high_prev > 0:
            breakout_strength = _clamp(((current / high_prev) - 1.0) / 0.05, 0.0, 1.0)
        high_recent = max([float(v) for v in hist[-20:]]) if len(hist) >= 2 else current
        pullback_from_high = 0.0
        if high_recent > 0:
            pullback_from_high = _clamp((high_recent - current) / high_recent / 0.12, 0.0, 1.0)
        return {
            "ema_signal": ema_signal,
            "ema_gap_pct": ema_gap_pct,
            "cci_signal": cci_signal,
            "cci_raw": cci,
            "rsi": rsi,
            "atr_pct": atr_pct,
            "atr_penalty": atr_penalty,
            "breakout_strength": breakout_strength,
            "pullback_from_high": pullback_from_high,
        }

    def _bybit_entry_threshold(self, model_id: str) -> float:
        with self._lock:
            run = (self.state.model_runs or {}).get(self._market_run_key("crypto", model_id)) or {}
        tune = self._read_model_runtime_tune_from_run(
            run if isinstance(run, dict) else {},
            model_id,
            int(time.time()),
        )
        threshold = float(tune.get("threshold") or MODEL_RUNTIME_TUNE_DEFAULTS.get(model_id, {}).get("threshold") or 0.070)
        if model_id == "B":
            trades = list((run or {}).get("trades") or [])
            closed_crypto = sum(
                1
                for row in trades
                if str(row.get("side") or "").lower() == "sell"
                and str(row.get("source") or "").lower() == "crypto_demo"
            )
            # Bootstrap: when B has no crypto close history, relax threshold to avoid no-trade deadlock.
            if closed_crypto <= 0:
                threshold = min(threshold, 0.030)
        return float(threshold)

    def _crypto_leverage_bounds(self) -> tuple[float, float]:
        lev_min = _clamp(float(self.settings.bybit_leverage_min), 1.0, 20.0)
        lev_max = _clamp(float(self.settings.bybit_leverage_max), lev_min, 20.0)
        return (lev_min, lev_max)

    @staticmethod
    def _crypto_model_risk_profile(model_id: str) -> dict[str, float]:
        if model_id == "A":
            return {
                "lev_min": 1.0,
                "lev_max": 5.0,
                "order_pct_mul": 0.45,
                "hard_roe_cut": -0.12,
            }
        if model_id == "B":
            return {
                "lev_min": 2.0,
                "lev_max": 10.0,
                "order_pct_mul": 0.80,
                "hard_roe_cut": -0.20,
            }
        return {
            "lev_min": 3.0,
            "lev_max": 20.0,
            "order_pct_mul": 1.00,
            "hard_roe_cut": -0.32,
        }

    def _compute_crypto_leverage(self, model_id: str, score: float, threshold: float, volatility: float) -> float:
        lev_min_cfg, lev_max_cfg = self._crypto_leverage_bounds()
        prof = self._crypto_model_risk_profile(model_id)
        lev_min = _clamp(float(prof.get("lev_min") or 1.0), 1.0, 20.0)
        lev_max = _clamp(float(prof.get("lev_max") or lev_max_cfg), lev_min, 20.0)
        lev_min = max(lev_min, lev_min_cfg)
        lev_max = min(lev_max, lev_max_cfg)
        if lev_max < lev_min:
            lev_max = lev_min
        score_gap = float(score) - float(threshold)
        score_norm = _clamp(0.5 + (0.5 * math.tanh(score_gap * 120.0)), 0.0, 1.0)
        vol_norm = _clamp(float(volatility), 0.0, 1.0)
        model_bias = 0.0
        if model_id == "A":
            model_bias = -0.24
        elif model_id == "B":
            model_bias = -0.08
        elif model_id == "C":
            model_bias = 0.10
        confidence = _clamp((0.70 * score_norm) + (0.30 * (1.0 - vol_norm)) + model_bias, 0.0, 1.0)
        lev = lev_min + ((lev_max - lev_min) * confidence)
        return round(_clamp(lev, lev_min, lev_max), 2)

    @staticmethod
    def _mark_crypto_position(pos: dict[str, Any], current_price: float) -> dict[str, float]:
        qty = float(pos.get("qty") or 0.0)
        avg = float(pos.get("avg_price_usd") or 0.0)
        leverage = max(1.0, float(pos.get("leverage") or 1.0))
        margin = float(pos.get("margin_usd") or 0.0)
        if margin <= 0.0 and avg > 0.0 and qty > 0.0:
            margin = (avg * qty) / leverage
        mark = float(current_price or 0.0)
        if mark <= 0.0:
            mark = avg
        exposure = max(0.0, mark * qty)
        pnl_raw = (mark - avg) * qty if avg > 0.0 and qty > 0.0 else 0.0
        pnl_floor = -max(0.0, margin)
        pnl = max(pnl_floor, pnl_raw)
        position_equity = max(0.0, margin + pnl)
        price_pnl_pct = 0.0 if avg <= 0.0 else ((mark - avg) / avg)
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

    def _score_crypto_signals(
        self,
        model_id: str,
        run: dict[str, Any],
        prices: dict[str, float],
        trend_bundle: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if not prices:
            return []
        open_positions = set((run.get("bybit_positions") or {}).keys())
        out: list[dict[str, Any]] = []
        for symbol, price in prices.items():
            p = float(price or 0.0)
            if p <= 0:
                continue
            profile = self._crypto_score_profile(model_id, symbol, trend_bundle)
            score = float(profile.get("score") or -1.0)
            threshold = float(profile.get("threshold") or self._bybit_entry_threshold(model_id))
            indicators = dict(profile.get("features") or {})
            vol = self._crypto_volatility_proxy(symbol)
            sl_pct, tp_pct = self._compute_risk_profile(model_id, "crypto", vol)
            out.append(
                {
                    "symbol": symbol,
                    "strategy": str(profile.get("strategy") or ""),
                    "score": score,
                    "price_usd": p,
                    "entry_threshold": threshold,
                    "above_threshold": bool(score > threshold and bool(profile.get("gate_ok"))),
                    "in_position": bool(symbol in open_positions),
                    "volatility": float(vol),
                    "tp_pct": float(tp_pct),
                    "sl_pct": float(sl_pct),
                    "gate_ok": bool(profile.get("gate_ok")),
                    "gate_reason": str(profile.get("gate_reason") or ""),
                    "indicator_snapshot": {
                        "ret_5m_pct": float(indicators.get("ret_5m") or 0.0) * 100.0,
                        "ret_15m_pct": float(indicators.get("ret_15m") or 0.0) * 100.0,
                        "ret_1h_pct": float(indicators.get("ret_1h") or 0.0) * 100.0,
                        "ret_4h_pct": float(indicators.get("ret_4h") or 0.0) * 100.0,
                        "ret_1d_pct": float(indicators.get("ret_1d") or 0.0) * 100.0,
                        # Backward compatibility fields
                        "mom1_pct": float(indicators.get("ret_5m") or 0.0) * 100.0,
                        "mom4_pct": float(indicators.get("ret_1h") or 0.0) * 100.0,
                        "mom12_pct": float(indicators.get("ret_1d") or 0.0) * 100.0,
                        "rank": int(indicators.get("market_cap_rank") or 0),
                        "ema_signal": float(indicators.get("ema_signal") or 0.0),
                        "rsi": float(indicators.get("rsi") or 0.0),
                        "cci_raw": float(indicators.get("cci_raw") or 0.0),
                        "atr_pct": float(indicators.get("atr_pct") or 0.0) * 100.0,
                        "quality_score": float(indicators.get("quality_score") or 0.0),
                        "social_heat": float(indicators.get("social_heat") or 0.0),
                        "trend_stack": float(indicators.get("trend_stack") or 0.0),
                        "overheat_penalty": float(indicators.get("overheat_penalty") or 0.0),
                        "pullback_from_high": float(indicators.get("pullback_from_high") or 0.0),
                        "breakout_strength": float(indicators.get("breakout_strength") or 0.0),
                    },
                    "reason": self._crypto_reason_text(profile),
                }
            )
        out.sort(key=lambda row: float(row.get("score") or 0.0), reverse=True)
        lev_cfg_min, lev_cfg_max = self._crypto_leverage_bounds()
        prof = self._crypto_model_risk_profile(model_id)
        lev_min = max(float(prof.get("lev_min") or 1.0), lev_cfg_min)
        lev_max = min(float(prof.get("lev_max") or lev_cfg_max), lev_cfg_max)
        if lev_max < lev_min:
            lev_max = lev_min
        model_scale = 1.0
        if model_id == "A":
            model_scale = 0.78
        elif model_id == "B":
            model_scale = 0.94
        elif model_id == "C":
            model_scale = 1.08
        denom = max(1, len(out) - 1)
        for idx, row in enumerate(out):
            vol = _clamp(float(row.get("volatility") or 0.0), 0.0, 1.0)
            rank_norm = _clamp(1.0 - (idx / denom), 0.0, 1.0)
            base = lev_min + ((lev_max - lev_min) * (rank_norm**1.15))
            vol_scale = 1.0 - (0.45 * vol)
            lev = lev_min + ((base - lev_min) * vol_scale * model_scale)
            row["leverage"] = round(_clamp(lev, lev_min, lev_max), 2)
            row.pop("volatility", None)
        return out

    def _evaluate_model_bybit_exits(self, model_id: str, run: dict[str, Any], prices: dict[str, float]) -> None:
        strategy_prefix = {
            "A": "A-ReliabilityTrend",
            "B": "B-PullbackFlow",
            "C": "C-AggressiveMomentum",
        }.get(model_id, "")
        for pos in list((run.get("bybit_positions") or {}).values()):
            symbol = str(pos.get("symbol") or "")
            entry = float(pos.get("avg_price_usd") or 0.0)
            current = float(prices.get(symbol) or self._bybit_last_prices.get(symbol) or entry)
            pos_reason = str(pos.get("reason") or "")
            entry_score = float(pos.get("entry_score") or 0.0)
            risk_prof = self._crypto_model_risk_profile(model_id)
            model_lev_max = float(risk_prof.get("lev_max") or 20.0)
            pos_lev = max(1.0, float(pos.get("leverage") or 1.0))
            if strategy_prefix and ((entry_score > 1.0) or (strategy_prefix not in pos_reason)):
                migration_price = current if current > 0 else entry
                if migration_price <= 0:
                    continue
                self._close_model_bybit_position(
                    model_id,
                    run,
                    pos,
                    migration_price,
                    "model_upgrade_migration_close",
                )
                continue
            if pos_lev > model_lev_max:
                migration_price = current if current > 0 else entry
                if migration_price <= 0:
                    continue
                self._close_model_bybit_position(
                    model_id,
                    run,
                    pos,
                    migration_price,
                    "model_risk_cap_migration_close",
                )
                continue
            if current <= 0 or entry <= 0:
                continue
            marked = self._mark_crypto_position(pos, current)
            pnl_pct = float(marked["price_pnl_pct"])
            roe_pct = float(marked["roe_pct"])
            hard_roe_cut = float(risk_prof.get("hard_roe_cut") or -0.30)
            if float(marked["position_equity_usd"]) <= 0.0 and float(marked["margin_usd"]) > 0.0:
                self._close_model_bybit_position(model_id, run, pos, current, "LIQ -100% margin")
                continue
            if roe_pct <= hard_roe_cut:
                self._close_model_bybit_position(
                    model_id,
                    run,
                    pos,
                    current,
                    f"Hard-ROE {roe_pct * 100:.2f}%",
                )
                continue
            tp_pct = float(pos.get("tp_pct") or self.settings.take_profit_pct)
            sl_pct = float(pos.get("sl_pct") or self.settings.stop_loss_pct)
            if pnl_pct >= tp_pct:
                self._close_model_bybit_position(model_id, run, pos, current, f"TP {pnl_pct * 100:.2f}%")
            elif pnl_pct <= -sl_pct:
                self._close_model_bybit_position(model_id, run, pos, current, f"SL {pnl_pct * 100:.2f}%")

    def _close_model_bybit_position(
        self,
        model_id: str,
        run: dict[str, Any],
        pos: dict[str, Any],
        price_usd: float,
        reason: str,
    ) -> bool:
        symbol = str(pos.get("symbol") or "")
        if not symbol:
            return False
        positions = run.get("bybit_positions") or {}
        if symbol not in positions:
            return False
        marked = self._mark_crypto_position(pos, price_usd)
        qty = float(marked["qty"])
        notional = float(marked["exposure_usd"])
        pnl_usd = float(marked["pnl_usd"])
        margin_usd = float(marked["margin_usd"])
        leverage = float(marked["leverage"])
        pnl_pct = pnl_usd / max(0.0001, margin_usd)
        cash_back = max(0.0, margin_usd + pnl_usd)

        del positions[symbol]
        run["bybit_positions"] = positions
        run["bybit_cash_usd"] = float(run.get("bybit_cash_usd") or 0.0) + cash_back
        run.setdefault("trades", []).append(
            {
                "ts": int(time.time()),
                "source": "crypto_demo",
                "side": "sell",
                "symbol": symbol,
                "token_address": symbol,
                "qty": qty,
                "price_usd": price_usd,
                "notional_usd": notional,
                "margin_usd": margin_usd,
                "leverage": leverage,
                "pnl_usd": pnl_usd,
                "pnl_pct": pnl_pct,
                "reason": reason,
                "model_id": model_id,
            }
        )
        self._prune_run_trades(run, int(time.time()))
        return True

    def _execute_model_bybit_entries(
        self,
        model_id: str,
        run: dict[str, Any],
        prices: dict[str, float],
        trend_bundle: dict[str, Any],
        scored_signals: list[dict[str, Any]] | None = None,
    ) -> None:
        if not prices:
            return
        positions = run.get("bybit_positions") or {}
        max_positions = max(1, int(self.settings.bybit_max_positions))
        if len(positions) >= max_positions:
            return

        ranked: list[tuple[str, float]] = []
        leverage_by_symbol: dict[str, float] = {}
        threshold_by_symbol: dict[str, float] = {}
        reason_by_symbol: dict[str, str] = {}
        strategy_by_symbol: dict[str, str] = {}
        gate_by_symbol: dict[str, bool] = {}
        snapshot_by_symbol: dict[str, dict[str, Any]] = {}
        if scored_signals:
            for row in scored_signals:
                symbol = str(row.get("symbol") or "")
                if not symbol or symbol in positions:
                    continue
                ranked.append((symbol, float(row.get("score") or 0.0)))
                leverage_by_symbol[symbol] = float(row.get("leverage") or 0.0)
                threshold_by_symbol[symbol] = float(row.get("entry_threshold") or self._bybit_entry_threshold(model_id))
                reason_by_symbol[symbol] = str(row.get("reason") or "")
                strategy_by_symbol[symbol] = str(row.get("strategy") or "")
                gate_by_symbol[symbol] = bool(row.get("gate_ok", True))
                snapshot_by_symbol[symbol] = dict(row.get("indicator_snapshot") or {})
        else:
            for symbol, price in prices.items():
                if price <= 0 or symbol in positions:
                    continue
                ranked.append((symbol, self._bybit_score(model_id, symbol, trend_bundle)))
        ranked.sort(key=lambda row: row[1], reverse=True)

        threshold = self._bybit_entry_threshold(model_id)
        opened = 0
        risk_prof = self._crypto_model_risk_profile(model_id)
        guard = self._entry_guard_profile(model_id, "crypto")
        guard_boost = float(guard.get("threshold_boost") or 0.0)
        gate_prof = dict(CRYPTO_MODEL_GATE_DEFAULTS.get(model_id) or CRYPTO_MODEL_GATE_DEFAULTS["B"])
        min_trend_stack = float(gate_prof.get("trend_stack_min") or 0.0)
        max_overheat = float(gate_prof.get("overheat_max") or 0.72)
        for symbol, score in ranked:
            if len(positions) + opened >= max_positions:
                break
            entry_threshold = float(threshold_by_symbol.get(symbol) or threshold) + guard_boost
            if not bool(gate_by_symbol.get(symbol, True)):
                continue
            if score <= entry_threshold:
                continue
            snap = dict(snapshot_by_symbol.get(symbol) or {})
            trend_stack = float(snap.get("trend_stack") or 0.0)
            if trend_stack < min_trend_stack:
                continue
            overheat = float(snap.get("overheat_penalty") or 0.0)
            if overheat >= max_overheat:
                continue
            cash = float(run.get("bybit_cash_usd") or 0.0)
            min_order = float(self.settings.bybit_min_order_usd)
            if cash < min_order:
                break
            order_pct = self._demo_order_pct_for_entry("crypto", score, entry_threshold)
            order_usd = min(cash, max(min_order, cash * order_pct))
            if order_usd < min_order:
                continue
            price = float(prices.get(symbol) or 0.0)
            if price <= 0:
                continue
            vol = self._crypto_volatility_proxy(symbol)
            sl_pct, tp_pct = self._compute_risk_profile(model_id, "crypto", vol)
            leverage = float(leverage_by_symbol.get(symbol) or 0.0)
            if leverage <= 0.0:
                leverage = self._compute_crypto_leverage(model_id, float(score), float(entry_threshold), vol)
            reason_text = str(reason_by_symbol.get(symbol) or "").strip()
            if not reason_text:
                reason_text = (
                    f"{strategy_by_symbol.get(symbol) or ('MODEL-' + model_id)} | "
                    f"score={score:.4f} thr={entry_threshold:.4f}"
                )
            notional_usd = order_usd * leverage
            qty = notional_usd / price
            run.setdefault("bybit_positions", {})[symbol] = {
                "symbol": symbol,
                "side": "long",
                "qty": qty,
                "avg_price_usd": price,
                "margin_usd": order_usd,
                "order_pct": float(order_pct),
                "leverage": leverage,
                "notional_usd": notional_usd,
                "opened_at": int(time.time()),
                "entry_score": score,
                "tp_pct": tp_pct,
                "sl_pct": sl_pct,
                "reason": reason_text,
            }
            run["bybit_cash_usd"] = cash - order_usd
            run.setdefault("trades", []).append(
                {
                    "ts": int(time.time()),
                    "source": "crypto_demo",
                    "side": "buy",
                    "symbol": symbol,
                    "token_address": symbol,
                    "qty": qty,
                    "price_usd": price,
                    "notional_usd": notional_usd,
                    "margin_usd": order_usd,
                    "order_pct": float(order_pct),
                    "leverage": leverage,
                    "pnl_usd": 0.0,
                    "pnl_pct": 0.0,
                    "reason": (
                        f"{reason_text} | alloc={order_pct*100:.1f}% | lev={leverage:.2f}x tp={tp_pct*100:.1f}% sl={sl_pct*100:.1f}% "
                        f"atr={float(snap.get('atr_pct') or 0.0):.2f}%"
                    ),
                    "model_id": model_id,
                }
            )
            self._record_last_entry_alloc(run, "crypto", symbol, order_pct, score, int(time.time()))
            self._prune_run_trades(run, int(time.time()))
            opened += 1

    def _model_metrics(self, model_id: str, run: dict[str, Any]) -> dict[str, Any]:
        trades = list(run.get("trades") or [])
        sells = [t for t in trades if str(t.get("side") or "").lower() == "sell"]
        realized = sum(float(t.get("pnl_usd") or 0.0) for t in sells)
        wins = sum(1 for t in sells if float(t.get("pnl_usd") or 0.0) > 0)
        closed = len(sells)
        win_rate = (wins / closed * 100.0) if closed > 0 else 0.0

        bybit_enabled = bool(self.settings.demo_enable_macro)
        meme_cash = float(run.get("meme_cash_usd") or 0.0)
        bybit_cash = float(run.get("bybit_cash_usd") or 0.0) if bybit_enabled else 0.0
        meme_seed = float(run.get("meme_seed_usd") or self.state.demo_seed_usdt)
        bybit_seed = float(run.get("bybit_seed_usd") or self.state.demo_seed_usdt) if bybit_enabled else 0.0

        meme_unrealized = 0.0
        meme_value = 0.0
        for pos in (run.get("meme_positions") or {}).values():
            token_address = str(pos.get("token_address") or "")
            current = self._resolve_price(token_address)
            qty = float(pos.get("qty") or 0.0)
            avg = float(pos.get("avg_price_usd") or 0.0)
            if current <= 0:
                continue
            meme_value += current * qty
            meme_unrealized += (current - avg) * qty

        bybit_unrealized = 0.0
        bybit_value = 0.0
        if bybit_enabled:
            for pos in (run.get("bybit_positions") or {}).values():
                symbol = str(pos.get("symbol") or "")
                current = float(self._bybit_last_prices.get(symbol) or pos.get("avg_price_usd") or 0.0)
                marked = self._mark_crypto_position(pos, current)
                bybit_value += float(marked["position_equity_usd"])
                bybit_unrealized += float(marked["pnl_usd"])

        meme_equity = meme_cash + meme_value
        bybit_equity = bybit_cash + bybit_value
        total_equity = meme_equity + bybit_equity
        total_seed = meme_seed + bybit_seed
        total_pnl = total_equity - total_seed
        return {
            "model_id": model_id,
            "model_name": run.get("model_name") or MODEL_SPECS.get(model_id, {}).get("name", model_id),
            "meme_seed_usd": meme_seed,
            "bybit_seed_usd": bybit_seed,
            "meme_cash_usd": meme_cash,
            "bybit_cash_usd": bybit_cash,
            "meme_equity_usd": meme_equity,
            "bybit_equity_usd": bybit_equity,
            "total_equity_usd": total_equity,
            "total_pnl_usd": total_pnl,
            "realized_pnl_usd": realized,
            "unrealized_pnl_usd": meme_unrealized + bybit_unrealized,
            "wins": wins,
            "closed_trades": closed,
            "win_rate": win_rate,
            "open_meme_positions": len(run.get("meme_positions") or {}),
            "open_bybit_positions": len(run.get("bybit_positions") or {}) if bybit_enabled else 0,
        }

    def _market_trade_stats(self, run: dict[str, Any], market: str) -> dict[str, float]:
        source_name = "memecoin" if market == "meme" else "crypto_demo"
        trades = list(run.get("trades") or [])
        sells = [
            t
            for t in trades
            if str(t.get("side") or "").lower() == "sell"
            and str(t.get("source") or "").lower() == source_name
        ]
        realized = sum(float(t.get("pnl_usd") or 0.0) for t in sells)
        wins = sum(1 for t in sells if float(t.get("pnl_usd") or 0.0) > 0.0)
        closed = len(sells)
        win_rate = (wins / closed * 100.0) if closed > 0 else 0.0
        return {
            "realized_pnl_usd": float(realized),
            "wins": float(wins),
            "closed_trades": float(closed),
            "win_rate": float(win_rate),
        }

    def _model_metrics_market(self, model_id: str, run: dict[str, Any], market: str) -> dict[str, Any]:
        market_id = "meme" if market == "meme" else "crypto"
        if market_id == "meme":
            seed = float(run.get("meme_seed_usd") or self.state.demo_seed_usdt)
            cash = float(run.get("meme_cash_usd") or 0.0)
            value = 0.0
            unrealized = 0.0
            for pos in (run.get("meme_positions") or {}).values():
                token_address = str(pos.get("token_address") or "")
                current = self._resolve_price(token_address)
                qty = float(pos.get("qty") or 0.0)
                avg = float(pos.get("avg_price_usd") or 0.0)
                if current <= 0:
                    continue
                value += current * qty
                unrealized += (current - avg) * qty
            open_positions = len(run.get("meme_positions") or {})
        else:
            if not self.settings.demo_enable_macro:
                seed = 0.0
                cash = 0.0
                value = 0.0
                unrealized = 0.0
                open_positions = 0
            else:
                seed = float(run.get("bybit_seed_usd") or self.state.demo_seed_usdt)
                cash = float(run.get("bybit_cash_usd") or 0.0)
                value = 0.0
                unrealized = 0.0
                for pos in (run.get("bybit_positions") or {}).values():
                    symbol = str(pos.get("symbol") or "")
                    current = float(self._bybit_last_prices.get(symbol) or pos.get("avg_price_usd") or 0.0)
                    marked = self._mark_crypto_position(pos, current)
                    value += float(marked["position_equity_usd"])
                    unrealized += float(marked["pnl_usd"])
                open_positions = len(run.get("bybit_positions") or {})

        equity = cash + value
        total_pnl = equity - seed
        t = self._market_trade_stats(run, market_id)
        return {
            "model_id": model_id,
            "model_name": self._market_model_name(market_id, model_id),
            "market": market_id,
            "seed_usd": float(seed),
            "cash_usd": float(cash),
            "position_value_usd": float(value),
            "equity_usd": float(equity),
            "total_pnl_usd": float(total_pnl),
            "realized_pnl_usd": float(t["realized_pnl_usd"]),
            "unrealized_pnl_usd": float(unrealized),
            "wins": float(t["wins"]),
            "closed_trades": float(t["closed_trades"]),
            "win_rate": float(t["win_rate"]),
            "open_positions": int(open_positions),
        }

    def _record_daily_pnl(self, now_ts: int) -> None:
        day_key = datetime.fromtimestamp(now_ts, tz=timezone.utc).strftime("%Y-%m-%d")
        with self._lock:
            table = list(self.state.daily_pnl or [])
            runs = dict(self.state.model_runs or {})
        for model_id in MODEL_IDS:
            meme_run = self._get_market_run(runs, "meme", model_id)
            crypto_run = self._get_market_run(runs, "crypto", model_id)
            run = self._compose_model_run_from_market(runs, model_id)
            m = self._model_metrics(model_id, run)
            meme_m = self._model_metrics_market(model_id, meme_run, "meme")
            crypto_m = self._model_metrics_market(model_id, crypto_run, "crypto")
            row = {
                "date": day_key,
                "model_id": model_id,
                "meme_equity_usd": round(float(m["meme_equity_usd"]), 6),
                "bybit_equity_usd": round(float(m["bybit_equity_usd"]), 6),
                "meme_total_pnl_usd": round(float(meme_m["total_pnl_usd"]), 6),
                "bybit_total_pnl_usd": round(float(crypto_m["total_pnl_usd"]), 6),
                "meme_realized_pnl_usd": round(float(meme_m["realized_pnl_usd"]), 6),
                "bybit_realized_pnl_usd": round(float(crypto_m["realized_pnl_usd"]), 6),
                "meme_unrealized_pnl_usd": round(float(meme_m["unrealized_pnl_usd"]), 6),
                "bybit_unrealized_pnl_usd": round(float(crypto_m["unrealized_pnl_usd"]), 6),
                "meme_win_rate": round(float(meme_m["win_rate"]), 4),
                "bybit_win_rate": round(float(crypto_m["win_rate"]), 4),
                "meme_closed_trades": int(meme_m["closed_trades"]),
                "bybit_closed_trades": int(crypto_m["closed_trades"]),
                "total_equity_usd": round(float(m["total_equity_usd"]), 6),
                "total_pnl_usd": round(float(m["total_pnl_usd"]), 6),
                "realized_pnl_usd": round(float(m["realized_pnl_usd"]), 6),
                "unrealized_pnl_usd": round(float(m["unrealized_pnl_usd"]), 6),
                "win_rate": round(float(m["win_rate"]), 4),
                "closed_trades": int(m["closed_trades"]),
            }
            idx = None
            for i in range(len(table) - 1, -1, -1):
                old = table[i]
                if str(old.get("date")) == day_key and str(old.get("model_id")) == model_id:
                    idx = i
                    break
            if idx is None:
                table.append(row)
            else:
                table[idx] = row
        with self._lock:
            self.state.daily_pnl = table[-1200:]

    def _sync_wallet(self, now: int, force: bool = False) -> None:
        if not self.settings.phantom_wallet_address:
            return
        if not force and (now - self._last_wallet_sync) < self.settings.wallet_update_seconds:
            return
        self._last_wallet_sync = now
        try:
            rows = self.wallet.fetch_wallet_assets(
                self.settings.phantom_wallet_address,
                self.dex,
                self.settings.min_wallet_asset_usd,
            )
            with self._lock:
                self.state.wallet_assets = rows
                self.state.last_wallet_sync_ts = now
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self.state.memecoin_error = f"wallet_sync_failed: {exc}"

    def _sync_bybit(self, now: int, force: bool = False) -> None:
        if not self.bybit.enabled:
            with self._lock:
                self.state.bybit_error = ""
                self.state.bybit_assets = []
                self.state.bybit_positions = []
            return
        if not force and (now - self._last_bybit_sync) < 15:
            return
        self._last_bybit_sync = now
        try:
            assets = self.bybit.get_wallet_assets()
            positions = self.bybit.get_positions()
            with self._lock:
                self.state.bybit_assets = assets
                self.state.bybit_positions = positions
                self.state.bybit_error = ""
                self.state.last_bybit_sync_ts = now
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self.state.bybit_error = str(exc)

    def _poll_telegram(self, now: int) -> None:
        if not self.settings.telegram_polling_enabled or not self.telegram.enabled:
            self._release_telegram_poll_lock()
            return
        if (now - self._last_telegram_poll) < self.settings.telegram_poll_interval_seconds:
            return
        if not self._acquire_telegram_poll_lock(now):
            self._last_telegram_poll = now
            self._emit_runtime_error(
                "core:telegram_poll_lock",
                "텔레그램 폴링 잠금 대기",
                "다른 프로세스가 동일 봇 토큰으로 polling 중입니다. 단일 인스턴스만 실행하세요.",
                level="warn",
                cooldown_seconds=300,
            )
            return
        self._last_telegram_poll = now

        with self._lock:
            offset = int(self.state.telegram_offset) + 1
        try:
            updates = self.telegram.get_updates(offset=offset, timeout=0)
        except Exception as exc:  # noqa: BLE001
            err_text = str(exc)
            low = err_text.lower()
            if "409" in low or "conflict" in low:
                self.telegram.delete_webhook(drop_pending_updates=False)
                self._emit_runtime_error(
                    "core:telegram_poll",
                    "텔레그램 폴링 충돌",
                    f"{err_text} | 다른 인스턴스의 getUpdates 중복 실행 여부를 확인하세요.",
                    level="warn",
                    cooldown_seconds=600,
                )
                self._last_telegram_poll = int(now) + 5
                return
            self._emit_runtime_error("core:telegram_poll", "텔레그램 폴링 오류", err_text, level="warn", cooldown_seconds=300)
            return

        max_update = offset - 1
        for upd in updates:
            update_id = int(upd.get("update_id") or 0)
            max_update = max(max_update, update_id)
            msg = upd.get("message") or upd.get("channel_post") or {}
            chat = msg.get("chat") or {}
            chat_id = str(chat.get("id") or "")
            text = str(msg.get("text") or "").strip()
            if not chat_id or not text:
                continue

            if not self.settings.telegram_chat_id:
                save_runtime_overrides(self.settings, {"TELEGRAM_CHAT_ID": chat_id})
                self._reload_settings()
                self._push_alert("info", "텔레그램 연결", f"chat_id 자동 설정: {chat_id}", send_telegram=False)

            if self.settings.telegram_chat_id and chat_id != self.settings.telegram_chat_id:
                continue

            if text.startswith("/"):
                response = self._handle_telegram_command(text, chat_id)
                if response:
                    self.telegram.send_message(chat_id, response)

        if max_update >= offset:
            with self._lock:
                self.state.telegram_offset = max_update

    def _handle_telegram_command(self, text: str, chat_id: str) -> str:
        chunks = text.split()
        cmd = chunks[0].lower().strip()

        def _core_name(mid: str) -> str:
            return self._display_model_name(mid)

        def _market_name(market: str, mid: str) -> str:
            return self._display_model_name(mid, market)

        def _read_runs() -> dict[str, Any]:
            with self._lock:
                return dict(self.state.model_runs or {})

        if cmd in {"/start", "/help"}:
            return (
                "명령어 상세 도움말\n"
                "[1) 상태/손익]\n"
                "/status - 전체 상태(모드, 자동매매, 모델 요약)\n"
                "/status_meme - 밈 모델 상태만 요약\n"
                "/status_crypto - 크립토 모델 상태만 요약\n"
                "/pnl - 모델별 통합 손익(TOTAL/MEME/CRYPTO)\n"
                "/pnl_meme - 밈 모델 손익만\n"
                "/pnl_crypto - 크립토 모델 손익만\n"
                "\n"
                "[2) 포지션/자산]\n"
                "/positions - 전체 포지션 요약\n"
                "/positions_meme - 밈 포지션만\n"
                "/positions_crypto - 크립토 포지션만\n"
                "/meme_balance - 팬텀 지갑 자산\n"
                "/bybit_balance - 거래소 자산\n"
                "\n"
                "[3) 튜닝/소스]\n"
                "/tune_status - 자동튜닝 상태(6시간 주기, 크립토 모델)\n"
                "/sources - 트렌드 소스 상태/재시도 시간\n"
                "/errors - 최근 엔진 오류 요약\n"
                "/wallet_pattern <token_address> - Solscan 지갑 패턴 점검\n"
                "\n"
                "[4) 제어]\n"
                "/auto_on, /auto_off - 자동매매 ON/OFF\n"
                "/trade_alert_on, /trade_alert_off - 체결 알림 ON/OFF\n"
                "/report_on, /report_off, /report_now - 주기 리포트 제어/즉시 발송\n"
                "/chatid - 현재 chat id 확인\n"
                "\n"
                "[5) 초기화 보호]\n"
                "/reset_unlock - 초기화 잠금 해제\n"
                "/reset_demo [seed] RESET DEMO - 데모 초기화(확인문구 필수)\n"
                "/reset_lock - 초기화 잠금 설정"
            )
        if cmd == "/chatid":
            return f"현재 chat_id: {chat_id}"
        if cmd == "/errors":
            with self._lock:
                memecoin_error = str(self.state.memecoin_error or "")
                bybit_error = str(self.state.bybit_error or "")
                trend_status = dict(self._trend_source_status or {})
            lines = ["오류 요약"]
            lines.append(f"- 밈코인 엔진: {memecoin_error or '-'}")
            lines.append(f"- 크립토 엔진: {bybit_error or '-'}")
            trend_err = []
            for source, row in trend_status.items():
                status = str((row or {}).get("status") or "")
                err = str((row or {}).get("error") or "")
                if status in {"error", "cooldown"} and err:
                    trend_err.append(f"{source}({status}): {err}")
            lines.append(f"- 트렌드 소스: {' | '.join(trend_err[:6]) if trend_err else '-'}")
            return "\n".join(lines)
        if cmd == "/sources":
            with self._lock:
                trend_status = dict(self._trend_source_status or {})
            if not trend_status:
                return "트렌드 소스 상태 정보가 아직 없습니다."
            lines = ["트렌드 소스 상태"]
            for source, row in sorted(trend_status.items(), key=lambda x: str(x[0])):
                status = str((row or {}).get("status") or "-")
                count = int((row or {}).get("count") or 0)
                retry = int((row or {}).get("next_retry_seconds") or 0)
                err = str((row or {}).get("error") or "")
                lines.append(f"- {source}: status={status}, count={count}, retry={retry}s, err={err or '-'}")
            return "\n".join(lines)
        if cmd == "/tune_status":
            runs = _read_runs()
            now_ts = int(time.time())
            lines = ["자동튜닝 상태 (크립토, 6시간 주기)"]
            for model_id in MODEL_IDS:
                run = self._get_market_run(runs, "crypto", model_id)
                tune = self._read_model_runtime_tune_from_run(run or {}, model_id, now_ts)
                remain = max(0, int(tune.get("next_eval_ts") or 0) - now_ts)
                lines.append(
                    f"- {_market_name('crypto', model_id)}: next={remain // 60}m, "
                    f"thr={float(tune['threshold']):.4f}, tp_mul={float(tune['tp_mul']):.2f}, sl_mul={float(tune['sl_mul']):.2f}"
                )
                if int(tune.get("last_eval_ts") or 0) > 0:
                    lines.append(
                        f"  최근평가: closed={int(tune['last_eval_closed'])}, wr={float(tune['last_eval_win_rate']):.1f}%, "
                        f"pnl={float(tune['last_eval_pnl_usd']):+.2f}, pf={float(tune['last_eval_pf']):.2f}, note={str(tune.get('last_eval_note') or '-')}"
                    )
            return "\n".join(lines)
        if cmd in {"/status_meme", "/status_crypto"}:
            runs = _read_runs()
            market = "meme" if cmd == "/status_meme" else "crypto"
            lines = [f"{'밈' if market == 'meme' else '크립토'} 모델 상태"]
            for model_id in MODEL_IDS:
                run = self._get_market_run(runs, market, model_id)
                mm = self._model_metrics_market(model_id, run, market)
                lines.append(
                    f"- {_market_name(market, model_id)}: equity={float(mm['equity_usd']):.2f}, "
                    f"pnl={float(mm['total_pnl_usd']):+.2f}, realized={float(mm['realized_pnl_usd']):+.2f}, "
                    f"open={int(mm['open_positions'])}, win={float(mm['win_rate']):.1f}%"
                )
            return "\n".join(lines)
        if cmd in {"/pnl_meme", "/pnl_crypto"}:
            runs = _read_runs()
            market = "meme" if cmd == "/pnl_meme" else "crypto"
            lines = [f"{'밈' if market == 'meme' else '크립토'} 모델 손익"]
            for model_id in MODEL_IDS:
                mm = self._model_metrics_market(model_id, self._get_market_run(runs, market, model_id), market)
                lines.append(
                    f"- {_market_name(market, model_id)}: total={float(mm['total_pnl_usd']):+.2f}, "
                    f"realized={float(mm['realized_pnl_usd']):+.2f}, unrealized={float(mm['unrealized_pnl_usd']):+.2f}"
                )
            return "\n".join(lines)
        if cmd in {"/positions_meme", "/positions_crypto"}:
            runs = _read_runs()
            market = "meme" if cmd == "/positions_meme" else "crypto"
            lines = [f"{'밈' if market == 'meme' else '크립토'} 포지션"]
            for model_id in MODEL_IDS:
                run = self._get_market_run(runs, market, model_id)
                lines.append(f"[{_market_name(market, model_id)}]")
                if market == "meme":
                    pos_rows = list((run.get("meme_positions") or {}).values())
                    if not pos_rows:
                        lines.append("  - 없음")
                        continue
                    for pos in pos_rows[:20]:
                        current = self._resolve_price(str(pos.get("token_address") or ""))
                        avg = float(pos.get("avg_price_usd") or 0.0)
                        pnl_pct = 0.0 if avg <= 0 else ((current - avg) / avg) * 100.0
                        lines.append(
                            f"  - {pos.get('symbol')} ({str(pos.get('strategy') or 'scalp')}): "
                            f"{pnl_pct:+.2f}% | TP {float(pos.get('tp_pct') or self.settings.take_profit_pct) * 100:.1f}% "
                            f"| SL {float(pos.get('sl_pct') or self.settings.stop_loss_pct) * 100:.1f}%"
                        )
                else:
                    pos_rows = list((run.get("bybit_positions") or {}).values())
                    if not pos_rows:
                        lines.append("  - 없음")
                        continue
                    for pos in pos_rows[:20]:
                        sym = str(pos.get("symbol") or "")
                        current = float(self._bybit_last_prices.get(sym) or pos.get("avg_price_usd") or 0.0)
                        avg = float(pos.get("avg_price_usd") or 0.0)
                        pnl_pct = 0.0 if avg <= 0 else ((current - avg) / avg) * 100.0
                        lines.append(
                            f"  - {sym}: {pnl_pct:+.2f}% | lev {float(pos.get('leverage') or 1.0):.2f}x "
                            f"| TP {float(pos.get('tp_pct') or self.settings.take_profit_pct) * 100:.1f}% "
                            f"| SL {float(pos.get('sl_pct') or self.settings.stop_loss_pct) * 100:.1f}%"
                        )
            return "\n".join(lines)
        if cmd == "/wallet_pattern":
            token = str(chunks[1] if len(chunks) > 1 else self.settings.solscan_focus_token or "").strip()
            if not token:
                return "토큰 주소를 입력하세요. 예: /wallet_pattern <token_address>"
            analysis = self._get_wallet_pattern(token, force=True)
            if not analysis.get("available"):
                return f"지갑패턴 분석 실패: {analysis.get('error') or 'no_data'}"
            return (
                f"토큰 {token}\n"
                f"- smart_wallet_score: {float(analysis.get('smart_wallet_score') or 0.0):.2f}\n"
                f"- holder_risk: {float(analysis.get('holder_risk') or 0.0):.2f}\n"
                f"- top10_pct: {float(analysis.get('top10_pct') or 0.0):.2f}%\n"
                f"- whale_count>=1%: {int(analysis.get('whale_count_ge_1pct') or 0)}\n"
                f"- suspicious: {'YES' if analysis.get('suspicious') else 'NO'}"
            )
        if cmd == "/auto_on":
            self.set_autotrade(True)
            return "자동매매를 켰습니다."
        if cmd == "/auto_off":
            self.set_autotrade(False)
            return "자동매매를 껐습니다."
        if cmd == "/trade_alert_on":
            self.set_telegram_trade_alerts(True)
            return "체결 텔레그램 알림을 켰습니다."
        if cmd == "/trade_alert_off":
            self.set_telegram_trade_alerts(False)
            return "체결 텔레그램 알림을 껐습니다."
        if cmd == "/report_on":
            self.set_telegram_report(True)
            return "10분 요약 리포트를 켰습니다."
        if cmd == "/report_off":
            self.set_telegram_report(False)
            return "10분 요약 리포트를 껐습니다."
        if cmd == "/report_now":
            if not self.alert_manager.enabled:
                return "텔레그램 전송이 비활성입니다. BOT_TOKEN/CHAT_ID를 확인하세요."
            text_out = self._build_telegram_periodic_report()
            ok, err = self.alert_manager.send_telegram(text_out)
            return "요약 리포트를 발송했습니다." if ok else f"전송 실패: {err}"
        if cmd == "/reset_unlock":
            self.set_demo_reset_enabled(True)
            return "데모 초기화 잠금을 해제했습니다. 초기화 후 /reset_lock 으로 다시 잠그세요."
        if cmd == "/reset_lock":
            self.set_demo_reset_enabled(False)
            return "데모 초기화 잠금을 설정했습니다."
        if cmd == "/reset_demo":
            seed = None
            idx = 1
            if len(chunks) > idx:
                try:
                    seed = float(chunks[idx])
                    idx += 1
                except Exception:
                    seed = None
            confirm_text = " ".join(chunks[idx:]).strip()
            try:
                result = self.reset_demo(seed, confirm_text=confirm_text, actor=f"telegram_{chat_id}")
            except PermissionError as exc:
                return str(exc)
            except ValueError as exc:
                return f"{exc} 예: /reset_demo 1000 RESET DEMO"
            return (
                f"데모 초기화 완료: seed={result['seed_usdt']:.2f} USDT\n"
                f"backup={result.get('backup_path') or '-'}"
            )
        if cmd == "/status":
            lines = [
                f"상태: {'실행중' if self.running else '정지'}",
                f"모드: {self.settings.trade_mode}",
                f"자동매매: {'ON' if self.settings.enable_autotrade else 'OFF'}",
                f"체결알림: {'ON' if self.settings.telegram_trade_alerts_enabled else 'OFF'}",
                f"주기리포트: {'ON' if self.settings.telegram_report_enabled else 'OFF'} ({int(self.settings.telegram_report_interval_seconds)}s)",
                f"초기화잠금: {'해제' if self.settings.allow_demo_reset else '설정'}",
            ]
            runs = _read_runs()
            for model_id in MODEL_IDS:
                merged = self._compose_model_run_from_market(runs, model_id)
                meme_run = self._get_market_run(runs, "meme", model_id)
                crypto_run = self._get_market_run(runs, "crypto", model_id)
                m = self._model_metrics(model_id, merged)
                mm = self._model_metrics_market(model_id, meme_run, "meme")
                cm = self._model_metrics_market(model_id, crypto_run, "crypto")
                core_name = _core_name(model_id)
                lines.append(
                    f"[{core_name}] TOTAL equity={m['total_equity_usd']:.2f} pnl={m['total_pnl_usd']:+.2f} win={m['win_rate']:.1f}%"
                )
                lines.append(
                    f"  - {_market_name('meme', model_id)}: pnl={mm['total_pnl_usd']:+.2f} "
                    f"realized={mm['realized_pnl_usd']:+.2f} open={mm['open_positions']}"
                )
                lines.append(
                    f"  - {_market_name('crypto', model_id)}: pnl={cm['total_pnl_usd']:+.2f} "
                    f"realized={cm['realized_pnl_usd']:+.2f} open={cm['open_positions']}"
                )
            return "\n".join(lines)
        if cmd == "/meme_balance":
            with self._lock:
                rows = list(self.state.wallet_assets)
            if not rows:
                return "팬텀 지갑 자산을 아직 동기화하지 못했습니다."
            lines = ["팬텀 지갑 자산 (USD 1 이상):"]
            for row in rows[:25]:
                lines.append(f"- {row.get('symbol')}: ${float(row.get('value_usd') or 0):.2f}")
            return "\n".join(lines)
        if cmd == "/bybit_balance":
            with self._lock:
                rows = list(self.state.bybit_assets)
                err = str(self.state.bybit_error or "")
            if err:
                return f"Crypto 동기화 오류: {err}"
            if not rows:
                return "Crypto 자산이 없거나 API 연결이 비활성입니다."
            lines = ["Crypto 자산:"]
            for row in rows[:25]:
                lines.append(f"- {row.get('coin')}: ${float(row.get('usd_value') or 0):.2f}")
            return "\n".join(lines)
        if cmd == "/positions":
            runs = _read_runs()
            lines = ["모델 포지션 요약"]
            for model_id in MODEL_IDS:
                meme_run = self._get_market_run(runs, "meme", model_id)
                crypto_run = self._get_market_run(runs, "crypto", model_id)
                lines.append(f"[{_core_name(model_id)}]")
                meme_positions = list((meme_run.get("meme_positions") or {}).values())
                bybit_positions = list((crypto_run.get("bybit_positions") or {}).values())
                if meme_positions:
                    lines.append(f"  [{_market_name('meme', model_id)}]")
                    for pos in meme_positions[:15]:
                        current = self._resolve_price(str(pos.get("token_address") or ""))
                        avg = float(pos.get("avg_price_usd") or 0.0)
                        pnl_pct = 0.0 if avg <= 0 else ((current - avg) / avg) * 100.0
                        lines.append(
                            f"  - {pos.get('symbol')} ({str(pos.get('strategy') or 'scalp')}): {pnl_pct:+.2f}%"
                        )
                else:
                    lines.append(f"  [{_market_name('meme', model_id)}] 없음")
                if bybit_positions:
                    lines.append(f"  [{_market_name('crypto', model_id)}]")
                    for pos in bybit_positions[:15]:
                        sym = str(pos.get("symbol") or "")
                        current = float(self._bybit_last_prices.get(sym) or pos.get("avg_price_usd") or 0.0)
                        avg = float(pos.get("avg_price_usd") or 0.0)
                        pnl_pct = 0.0 if avg <= 0 else ((current - avg) / avg) * 100.0
                        lines.append(f"  - {sym}: {pnl_pct:+.2f}%")
                else:
                    lines.append(f"  [{_market_name('crypto', model_id)}] 없음")
            return "\n".join(lines)
        if cmd == "/pnl":
            runs = _read_runs()
            lines = ["모델별 손익 요약"]
            for model_id in MODEL_IDS:
                merged = self._compose_model_run_from_market(runs, model_id)
                mm = self._model_metrics_market(model_id, self._get_market_run(runs, "meme", model_id), "meme")
                cm = self._model_metrics_market(model_id, self._get_market_run(runs, "crypto", model_id), "crypto")
                m = self._model_metrics(model_id, merged)
                lines.append(
                    f"- {_core_name(model_id)}: TOTAL {m['total_pnl_usd']:+.2f} | "
                    f"{_market_name('meme', model_id)} {mm['total_pnl_usd']:+.2f} | "
                    f"{_market_name('crypto', model_id)} {cm['total_pnl_usd']:+.2f}"
                )
            return "\n".join(lines)
        return "알 수 없는 명령어입니다. /help 를 확인하세요."

    def _performance(self) -> dict[str, float]:
        with self._lock:
            runs = dict(self.state.model_runs or {})
            run = self._compose_model_run_from_market(runs, "A")
        m = self._model_metrics("A", run)
        return {
            "realized_pnl_usd": float(m["realized_pnl_usd"]),
            "unrealized_pnl_usd": float(m["unrealized_pnl_usd"]),
            "bybit_unrealized_pnl_usd": 0.0,
            "wins": float(m["wins"]),
            "closed_trades": float(m["closed_trades"]),
            "win_rate": float(m["win_rate"]),
            "cash_usd": float(m["meme_cash_usd"]),
            "meme_value_usd": float(m["meme_equity_usd"]) - float(m["meme_cash_usd"]),
            "total_equity_usd": float(m["total_equity_usd"]),
            "total_pnl_usd": float(m["total_pnl_usd"]),
        }

    def _build_meme_positions_view(self, run: dict[str, Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for pos in list((run.get("meme_positions") or {}).values()):
            token_address = str(pos.get("token_address") or "")
            current = self._resolve_price(token_address)
            qty = float(pos.get("qty") or 0.0)
            avg = float(pos.get("avg_price_usd") or 0.0)
            pnl_usd = (current - avg) * qty if current > 0 else 0.0
            pnl_pct = 0.0 if avg <= 0 else ((current - avg) / avg) * 100.0
            out.append(
                {
                    "symbol": str(pos.get("symbol") or ""),
                    "token_address": token_address,
                    "qty": qty,
                    "grade": str(pos.get("grade") or ""),
                    "strategy": str(pos.get("strategy") or "scalp"),
                    "avg_price_usd": avg,
                    "current_price_usd": current,
                    "value_usd": current * qty if current > 0 else 0.0,
                    "pnl_usd": pnl_usd,
                    "pnl_pct": pnl_pct,
                    "tp_pct": float(pos.get("tp_pct") or self.settings.take_profit_pct),
                    "sl_pct": float(pos.get("sl_pct") or self.settings.stop_loss_pct),
                    "hold_until_ts": int(pos.get("hold_until_ts") or 0),
                    "opened_at": int(pos.get("opened_at") or 0),
                    "reason": str(pos.get("reason") or ""),
                }
            )
        out.sort(key=lambda r: float(r.get("value_usd") or 0.0), reverse=True)
        return out

    def _build_crypto_positions_view(self, run: dict[str, Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if not self.settings.demo_enable_macro:
            return out
        for pos in list((run.get("bybit_positions") or {}).values()):
            symbol = str(pos.get("symbol") or "")
            current = float(self._bybit_last_prices.get(symbol) or pos.get("avg_price_usd") or 0.0)
            avg = float(pos.get("avg_price_usd") or 0.0)
            marked = self._mark_crypto_position(pos, current)
            out.append(
                {
                    "symbol": symbol,
                    "side": str(pos.get("side") or "long"),
                    "size": float(marked["qty"]),
                    "leverage": float(marked["leverage"]),
                    "margin_usd": float(marked["margin_usd"]),
                    "avg_price": avg,
                    "mark_price": float(marked["mark_price_usd"]),
                    "position_value": float(marked["exposure_usd"]),
                    "position_equity_usd": float(marked["position_equity_usd"]),
                    "unrealised_pnl": float(marked["pnl_usd"]),
                    "roe_pct": float(marked["roe_pct"]) * 100.0,
                    "entry_score": float(pos.get("entry_score") or 0.0),
                    "tp_pct": float(pos.get("tp_pct") or self.settings.take_profit_pct),
                    "sl_pct": float(pos.get("sl_pct") or self.settings.stop_loss_pct),
                    "reason": str(pos.get("reason") or ""),
                }
            )
        out.sort(key=lambda r: float(r.get("position_value") or 0.0), reverse=True)
        return out

    def _model_method_explanations(self) -> dict[str, dict[str, str]]:
        return {
            "A": {
                "name": MODEL_SPECS["A"]["name"],
                "meme": "도그리 밈 선별모델: 품질/스마트월렛/홀더리스크를 엄격히 보는 밈코인 신뢰형 전략입니다.",
                "crypto": "크립토 안정 추세모델: 품질/저변동 + 1h/4h/1d 추세 정합을 통과한 종목만 진입하는 방어형 전략입니다.",
                "strengths_meme": "강점: 저품질 신규 펌프 추격을 줄입니다. 진입 횟수는 적지만 생존성에 유리합니다.",
                "strengths_crypto": "강점: 과열/고변동 회피가 강하고 승률 안정화에 유리합니다.",
                "autotune": "자동튜닝(6시간): 품질형 규칙으로 threshold/TP/SL을 미세 보정합니다. 목표는 승률 안정화입니다.",
            },
            "B": {
                "name": MODEL_SPECS["B"]["name"],
                "meme": "밈 장기홀딩 예측모델: 장기홀딩 전용(기본 14일)으로 고품질 밈코인을 느리게 포착하는 전략입니다.",
                "crypto": "크립토 흐름 추종모델: 눌림목 + 소셜 흐름 + 중기 추세 유지를 결합한 밸런스 전략입니다.",
                "strengths_meme": "강점: 과도한 잦은 매매를 줄이고, 상위 등급 밈코인을 길게 보유하는 데 유리합니다.",
                "strengths_crypto": "강점: 상승 추세의 눌림 재진입 구간 포착에 강합니다.",
                "autotune": "자동튜닝(6시간): 트렌드형 규칙으로 threshold/TP/SL을 조정합니다. 과손실 시 방어, 성과 구간은 확장합니다.",
            },
            "C": {
                "name": MODEL_SPECS["C"]["name"],
                "meme": "밈 단타 모멘텀모델: 단타 전용으로 빠른 체결흐름/모멘텀을 반영해 짧게 회전하는 전략입니다.",
                "crypto": "동그리 크립토 모멘텀모델: 5m/15m 가속 + 브레이크아웃 + 소셜히트를 크게 반영하는 공격형 전략입니다.",
                "strengths_meme": "강점: 짧은 시간대의 회전 매매 대응이 빠릅니다.",
                "strengths_crypto": "강점: 빠른 모멘텀 구간 진입에 유리합니다. 대신 변동성 리스크가 큽니다.",
                "autotune": "자동튜닝(6시간): 공격형 규칙으로 threshold/TP/SL을 조절합니다. 손실 구간에서는 빠르게 리스크 오프합니다.",
            },
        }

    def _model_profile_snapshot(self) -> dict[str, dict[str, Any]]:
        rank_to_grade = {0: "S", 1: "A", 2: "B", 3: "C", 4: "D", 5: "E", 6: "F", 7: "G"}
        out: dict[str, dict[str, Any]] = {}
        for model_id in MODEL_IDS:
            tune_defaults = dict(MODEL_RUNTIME_TUNE_DEFAULTS.get(model_id) or MODEL_RUNTIME_TUNE_DEFAULTS["B"])
            tune_clamps = self._model_tune_clamps(model_id)
            gate_prof = dict(CRYPTO_MODEL_GATE_DEFAULTS.get(model_id) or CRYPTO_MODEL_GATE_DEFAULTS["B"])
            risk_prof = self._crypto_model_risk_profile(model_id)
            meme_min_rank = int(self._meme_min_entry_rank_for_model(model_id))
            out[model_id] = {
                "meme": {
                    "threshold_floor": round(float(self._variant_threshold(model_id)), 4),
                    "paper_min_grade": rank_to_grade.get(meme_min_rank, "G"),
                    "demo_score_floor": round(float(self._demo_meme_score_floor(model_id)), 4),
                    "strategy_mode": self._meme_strategy_mode_for_model(model_id),
                    "swing_hold_days": max(14, int(self.settings.meme_swing_hold_days))
                    if model_id == "B"
                    else int(self.settings.meme_swing_hold_days),
                },
                "crypto": {
                    "rank_max": int(gate_prof.get("rank_max") or 0),
                    "trend_stack_min": round(float(gate_prof.get("trend_stack_min") or 0.0), 4),
                    "overheat_max": round(float(gate_prof.get("overheat_max") or 0.0), 4),
                    "smallcap_trend_only": bool(gate_prof.get("smallcap_trend_only")),
                    "leverage_range": [
                        round(float(risk_prof.get("lev_min") or 1.0), 2),
                        round(float(risk_prof.get("lev_max") or 1.0), 2),
                    ],
                    "order_pct_mul": round(float(risk_prof.get("order_pct_mul") or 1.0), 4),
                    "hard_roe_cut": round(float(risk_prof.get("hard_roe_cut") or 0.0), 4),
                    "runtime_defaults": {
                        "threshold": round(float(tune_defaults.get("threshold") or 0.0), 4),
                        "tp_mul": round(float(tune_defaults.get("tp_mul") or 0.0), 4),
                        "sl_mul": round(float(tune_defaults.get("sl_mul") or 0.0), 4),
                    },
                    "runtime_clamps": {
                        "threshold": [
                            round(float(tune_clamps["threshold"][0]), 4),
                            round(float(tune_clamps["threshold"][1]), 4),
                        ],
                        "tp_mul": [
                            round(float(tune_clamps["tp_mul"][0]), 4),
                            round(float(tune_clamps["tp_mul"][1]), 4),
                        ],
                        "sl_mul": [
                            round(float(tune_clamps["sl_mul"][0]), 4),
                            round(float(tune_clamps["sl_mul"][1]), 4),
                        ],
                    },
                },
            }
        return out

    def dashboard_payload(self) -> dict[str, Any]:
        perf = self._performance()
        with self._lock:
            settings_public = settings_to_public_dict(self.settings)
            alerts = list(self.state.alerts[-80:])
            trend_events = list(self.state.trend_events[-200:])
            wallet_assets = list(self.state.wallet_assets)
            bybit_assets = list(self.state.bybit_assets)
            bybit_live_positions = list(self.state.bybit_positions)
            bybit_error = str(self.state.bybit_error or "")
            memecoin_error = str(self.state.memecoin_error or "")
            last_cycle_ts = int(self.state.last_cycle_ts)
            last_wallet_sync_ts = int(self.state.last_wallet_sync_ts)
            last_bybit_sync_ts = int(self.state.last_bybit_sync_ts)
            daily_pnl = list(self.state.daily_pnl[-1200:])
            runs = dict(self.state.model_runs or {})
            demo_seed = float(self.state.demo_seed_usdt or self.settings.demo_seed_usdt)
            trend_source_status = dict(self._trend_source_status or {})
            new_meme_feed = list(self._new_meme_feed or [])

        run_a_meme = self._get_market_run(runs, "meme", "A")
        run_a_crypto = self._get_market_run(runs, "crypto", "A")
        run_a = self._compose_model_run_from_market(runs, "A")
        demo_trades = list(run_a.get("trades") or [])[-300:]
        meme_signals = list(run_a_meme.get("latest_signals") or [])[-80:]
        crypto_signals = list(run_a_crypto.get("latest_crypto_signals") or [])[-80:]
        model_a_metrics = self._model_metrics("A", run_a)

        meme_trades = list(run_a_meme.get("trades") or [])[-300:]
        crypto_trades = list(run_a_crypto.get("trades") or [])[-300:]

        def _trade_stats(rows: list[dict[str, Any]]) -> dict[str, float]:
            sells = [t for t in rows if str(t.get("side") or "").lower() == "sell"]
            realized = sum(float(t.get("pnl_usd") or 0.0) for t in sells)
            closed = len(sells)
            wins = sum(1 for t in sells if float(t.get("pnl_usd") or 0.0) > 0.0)
            win_rate = (wins / closed * 100.0) if closed > 0 else 0.0
            return {
                "realized_pnl_usd": float(realized),
                "closed_trades": float(closed),
                "wins": float(wins),
                "win_rate": float(win_rate),
            }

        meme_positions = self._build_meme_positions_view(run_a_meme)
        bybit_demo_positions = self._build_crypto_positions_view(run_a_crypto)

        meme_unrealized = sum(float(p.get("pnl_usd") or 0.0) for p in meme_positions)
        crypto_unrealized = sum(float(p.get("unrealised_pnl") or 0.0) for p in bybit_demo_positions)
        crypto_exposure = sum(float(p.get("position_value") or 0.0) for p in bybit_demo_positions)
        crypto_margin = sum(float(p.get("margin_usd") or 0.0) for p in bybit_demo_positions)
        crypto_position_equity = sum(float(p.get("position_equity_usd") or 0.0) for p in bybit_demo_positions)
        meme_stats = _trade_stats(meme_trades)
        crypto_stats = _trade_stats(crypto_trades)
        meme_equity = float(model_a_metrics.get("meme_equity_usd") or 0.0)
        meme_cash = float(model_a_metrics.get("meme_cash_usd") or 0.0)
        crypto_equity = float(model_a_metrics.get("bybit_equity_usd") or 0.0)
        crypto_cash = float(model_a_metrics.get("bybit_cash_usd") or 0.0)
        meme_summary = {
            "cash_usd": meme_cash,
            "position_value_usd": max(0.0, meme_equity - meme_cash),
            "equity_usd": meme_equity,
            "unrealized_pnl_usd": float(meme_unrealized),
            "realized_pnl_usd": float(meme_stats["realized_pnl_usd"]),
            "closed_trades": int(meme_stats["closed_trades"]),
            "win_rate": float(meme_stats["win_rate"]),
            "open_positions": len(meme_positions),
        }
        crypto_summary = {
            "cash_usd": crypto_cash,
            "position_value_usd": float(crypto_position_equity),
            "exposure_usd": float(crypto_exposure),
            "margin_usd": float(crypto_margin),
            "equity_usd": crypto_equity,
            "unrealized_pnl_usd": float(crypto_unrealized),
            "realized_pnl_usd": float(crypto_stats["realized_pnl_usd"]),
            "closed_trades": int(crypto_stats["closed_trades"]),
            "win_rate": float(crypto_stats["win_rate"]),
            "open_positions": len(bybit_demo_positions),
        }

        now_ts = int(time.time())
        trend_rank: dict[str, int] = {}
        for ev in trend_events:
            ts = int(ev.get("ts") or 0)
            if ts <= 0 or (now_ts - ts) > 21600:
                continue
            sym = str(ev.get("symbol") or "").upper()
            if not sym:
                continue
            if not self._is_memecoin_token(sym, sym, ""):
                continue
            trend_rank[sym] = trend_rank.get(sym, 0) + 1
        trend_top = sorted(
            [{"symbol": k, "hits": v} for k, v in trend_rank.items()],
            key=lambda row: int(row["hits"]),
            reverse=True,
        )[:30]

        model_runs = [self._model_metrics(mid, self._compose_model_run_from_market(runs, mid)) for mid in MODEL_IDS]
        meme_model_runs = [self._model_metrics_market(mid, self._get_market_run(runs, "meme", mid), "meme") for mid in MODEL_IDS]
        crypto_model_runs = [
            self._model_metrics_market(mid, self._get_market_run(runs, "crypto", mid), "crypto") for mid in MODEL_IDS
        ]
        meme_daily_pnl = [
            {
                "date": str(row.get("date") or ""),
                "model_id": str(row.get("model_id") or ""),
                "equity_usd": float(row.get("meme_equity_usd") or 0.0),
                "total_pnl_usd": float(row.get("meme_total_pnl_usd") or 0.0),
                "realized_pnl_usd": float(row.get("meme_realized_pnl_usd") or 0.0),
                "unrealized_pnl_usd": float(row.get("meme_unrealized_pnl_usd") or 0.0),
                "win_rate": float(row.get("meme_win_rate") or 0.0),
                "closed_trades": int(row.get("meme_closed_trades") or 0),
            }
            for row in daily_pnl
        ]
        crypto_daily_pnl = [
            {
                "date": str(row.get("date") or ""),
                "model_id": str(row.get("model_id") or ""),
                "equity_usd": float(row.get("bybit_equity_usd") or 0.0),
                "total_pnl_usd": float(row.get("bybit_total_pnl_usd") or 0.0),
                "realized_pnl_usd": float(row.get("bybit_realized_pnl_usd") or 0.0),
                "unrealized_pnl_usd": float(row.get("bybit_unrealized_pnl_usd") or 0.0),
                "win_rate": float(row.get("bybit_win_rate") or 0.0),
                "closed_trades": int(row.get("bybit_closed_trades") or 0),
            }
            for row in daily_pnl
        ]
        model_recommendations = [
            {"id": mid, "name": MODEL_SPECS[mid]["name"], "description": MODEL_SPECS[mid]["description"]}
            for mid in MODEL_IDS
        ]
        meme_model_recommendations = [
            {"id": mid, "name": self._market_model_name("meme", mid), "description": self._market_model_spec("meme", mid)["description"]}
            for mid in MODEL_IDS
        ]
        crypto_model_recommendations = [
            {
                "id": mid,
                "name": self._market_model_name("crypto", mid),
                "description": self._market_model_spec("crypto", mid)["description"],
            }
            for mid in MODEL_IDS
        ]
        model_methods = self._model_method_explanations()
        model_profiles = self._model_profile_snapshot()
        model_views: dict[str, Any] = {}
        for model_id in MODEL_IDS:
            meme_run = self._get_market_run(runs, "meme", model_id)
            crypto_run = self._get_market_run(runs, "crypto", model_id)
            model_meme_trades = list(meme_run.get("trades") or [])[-400:]
            model_crypto_trades = list(crypto_run.get("trades") or [])[-400:]
            model_meme_positions = self._build_meme_positions_view(meme_run)
            model_crypto_positions = self._build_crypto_positions_view(crypto_run)
            model_meme_daily = [row for row in meme_daily_pnl if str(row.get("model_id") or "") == model_id]
            model_crypto_daily = [row for row in crypto_daily_pnl if str(row.get("model_id") or "") == model_id]
            model_views[model_id] = {
                "model_id": model_id,
                "model_name": MODEL_SPECS.get(model_id, {}).get("name", model_id),
                "market_names": {
                    "meme": self._market_model_name("meme", model_id),
                    "crypto": self._market_model_name("crypto", model_id),
                },
                "meme": {
                    "model_name": self._market_model_name("meme", model_id),
                    "summary": self._model_metrics_market(model_id, meme_run, "meme"),
                    "signals": list(meme_run.get("latest_signals") or [])[-120:],
                    "positions": model_meme_positions,
                    "trades": model_meme_trades,
                    "daily_pnl": model_meme_daily,
                },
                "crypto": {
                    "model_name": self._market_model_name("crypto", model_id),
                    "summary": self._model_metrics_market(model_id, crypto_run, "crypto"),
                    "signals": list(crypto_run.get("latest_crypto_signals") or [])[-120:],
                    "positions": model_crypto_positions,
                    "trades": model_crypto_trades,
                    "daily_pnl": model_crypto_daily,
                },
            }
        model_autotune: dict[str, Any] = {}
        for model_id in MODEL_IDS:
            run = self._get_market_run(runs, "crypto", model_id)
            model_autotune[model_id] = self._read_model_runtime_tune_from_run(run or {}, model_id, now_ts)
        b_autotune = dict(model_autotune.get("B") or {})
        solscan_usage: dict[str, Any] = {}
        try:
            trend_solscan = getattr(self.trend, "solscan", None)
            if trend_solscan is not None and hasattr(trend_solscan, "usage_snapshot"):
                snap = trend_solscan.usage_snapshot()
                if isinstance(snap, dict):
                    solscan_usage = dict(snap)
        except Exception:
            solscan_usage = {}

        return {
            "server_time": now_ts,
            "running": self.running,
            "settings": settings_public,
            "errors": {"memecoin": memecoin_error, "bybit": bybit_error},
            "demo_enable_bybit": self.settings.demo_enable_bybit,
            "demo_enable_macro": self.settings.demo_enable_macro,
            "macro_universe_source": self.settings.macro_universe_source,
            "macro_top_n": self.settings.macro_top_n,
            "binance_key_configured": bool(
                str(self.settings.binance_api_key or "").strip() and str(self.settings.binance_api_secret or "").strip()
            ),
            "execution_policy": {
                "binance": "inference_only" if bool(self.settings.binance_inference_only) else "mixed",
                "bybit": "execution_and_sync",
            },
            "macro_trend_pool_size": self.settings.macro_trend_pool_size,
            "macro_trend_reselect_seconds": self.settings.macro_trend_reselect_seconds,
            "macro_trend_pool": list(self._macro_trend_pool or []),
            "macro_trend_pool_next_refresh_ts": int(self._macro_trend_pool_next_refresh_ts or 0),
            "macro_realtime_sources": self.settings.macro_realtime_sources,
            "macro_realtime_cache_seconds": self.settings.macro_realtime_cache_seconds,
            "solscan_pattern_enabled": self.settings.solscan_enable_pattern,
            "solscan_focus_token": self.settings.solscan_focus_token,
            "solscan_usage": solscan_usage,
            "last_cycle_ts": last_cycle_ts,
            "last_wallet_sync_ts": last_wallet_sync_ts,
            "last_bybit_sync_ts": last_bybit_sync_ts,
            "demo_seed_usdt": demo_seed,
            "metrics": perf,
            "model_runs": model_runs,
            "meme_model_runs": meme_model_runs,
            "crypto_model_runs": crypto_model_runs,
            "model_views": model_views,
            "model_methods": model_methods,
            "model_profiles": model_profiles,
            "model_recommendations": model_recommendations,
            "meme_model_recommendations": meme_model_recommendations,
            "crypto_model_recommendations": crypto_model_recommendations,
            "meme_model_labels": {mid: self._market_model_name("meme", mid) for mid in MODEL_IDS},
            "crypto_model_labels": {mid: self._market_model_name("crypto", mid) for mid in MODEL_IDS},
            "daily_pnl": daily_pnl,
            "meme_daily_pnl": meme_daily_pnl,
            "crypto_daily_pnl": crypto_daily_pnl,
            "meme_positions": meme_positions,
            "meme_summary": meme_summary,
            "bybit_positions": bybit_demo_positions,
            "crypto_summary": crypto_summary,
            "crypto_positions": bybit_demo_positions,
            "bybit_live_positions": bybit_live_positions,
            "crypto_live_positions": bybit_live_positions,
            "wallet_assets": wallet_assets,
            "bybit_assets": bybit_assets,
            "crypto_assets": bybit_assets,
            "meme_signals": meme_signals,
            "crypto_signals": crypto_signals,
            "signals": meme_signals,
            "trend_top": trend_top,
            "trend_source_status": trend_source_status,
            "meme_grade_criteria": self._meme_grade_criteria(),
            "crypto_param_legend": self._crypto_param_legend(),
            "new_meme_feed": new_meme_feed,
            "focus_wallet_analysis": dict(self._focus_wallet_analysis or {}),
            "model_autotune": model_autotune,
            "b_model_autotune": b_autotune,
            "alerts": alerts,
            "meme_trades": meme_trades,
            "crypto_trades": crypto_trades,
            "trades": demo_trades,
        }

