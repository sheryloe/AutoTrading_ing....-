from __future__ import annotations

import math
import json
import os
import re
import shutil
import hashlib
import tempfile
import threading
import time
import uuid
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from src.alerts import AlertManager
from src.config import Settings, load_settings, save_runtime_overrides, settings_to_public_dict
from src.data_sources import DexScreenerClient, MacroMarketClient, PumpFunClient, SolscanProClient, TrendCollector
from src.daily_reports import git_commit_report_files, write_daily_pnl_report
from src.models import TokenSnapshot, TrendEvent
from src.online_model import OnlineModel, load_online_model, save_online_model
from src.meme_discovery import MemeDiscoveryConfig, MemeDiscoveryService
from src.providers import BybitV5Client, JupiterSolanaTrader, OpenAICandidateAdvisor, PumpPortalLocalTrader, SolanaWalletTracker, TelegramBotClient
from src.runtime_feedback import RuntimeFeedbackStore
from src.supabase_sync import SupabaseSyncClient
from src.state import (
    STATE_DAILY_PNL_HISTORY_LIMIT,
    STATE_TREND_HISTORY_LIMIT,
    EngineState,
    Position,
    Trade,
    load_state,
    save_state,
    state_from_dict,
    state_to_dict,
)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def _sigmoid(value: float) -> float:
    x = _clamp(float(value), -30.0, 30.0)
    return 1.0 / (1.0 + math.exp(-x))


MODEL_SPECS: dict[str, dict[str, str]] = {
    "A": {"name": "안정 추세 예측모델", "description": "신뢰형: 고신뢰 지표 중심 스윙"},
    "B": {"name": "흐름 추종 예측모델", "description": "트렌드형: 최근 이슈/소셜 추론 중심"},
    "C": {"name": "공격 모멘텀 예측모델", "description": "공격형: 빠른 진입/고위험 추론"},
    "D": {"name": "스몰캡 단타 예측모델", "description": "A형 단타 로직 기반 800~1000위 소형주 스캘프"},
}
MEME_MODEL_SPECS: dict[str, dict[str, str]] = {
    "A": {"name": "도그리 밈 선별모델", "description": "고품질 밈코인 선별 진입"},
    "B": {"name": "밈 장기홀딩 예측모델", "description": "장기홀딩(기본 14일) 중심 전략"},
    "C": {"name": "밈 단타 모멘텀모델", "description": "단타(빠른 회전) 중심 전략"},
}
CRYPTO_MODEL_SPECS: dict[str, dict[str, str]] = {
    "A": {"name": "크립토 레인지 리버전 플래너", "description": "과열 추격 대신 레인지 하단 재진입 구간을 예측하는 계획형 모델"},
    "B": {"name": "크립토 리클레임 플래너", "description": "지지 회복과 재안착 구간에서 진입/손절/목표가를 산출하는 모델"},
    "C": {"name": "크립토 압축 돌파 플래너", "description": "변동성 수축 후 확장 구간의 돌파 진입 계획을 만드는 모델"},
    "D": {"name": "크립토 리셋 바운스 플래너", "description": "급락 후 안정화 구간의 되돌림 진입 계획을 계산하는 모델"},
}
MEME_ENGINE_SPEC: dict[str, str] = {
    "id": "MEME_ONE",
    "name": "Unified Meme Engine",
    "description": "THEME_SNIPER 메인 모델에서 신규 런치와 소셜 버스트를 함께 점수화하고 NARRATIVE는 재점화형 서브 시그널로만 쓰는 단일 밈 엔진",
}
MEME_STRATEGY_IDS = ("THEME", "SNIPER", "NARRATIVE")
MEME_STRATEGY_ALIASES: dict[str, str] = {
    "LAUNCH": "SNIPER",
}
MEME_STRATEGY_SPECS: dict[str, dict[str, Any]] = {
    "THEME": {
        "name": "Theme Basket",
        "description": "신규 밈 전체를 보는 메인 전략. 3k~5k launch-first 후보를 0.1 SOL로 빠르게 분산 진입",
        "bridge_model_id": "A",
        "execution_mode": "theme_basket",
        "entry_sol": "meme_theme_entry_sol",
    },
    "SNIPER": {
        "name": "Sniper",
        "description": "X/Reddit/4chan/뉴스 버스트가 붙은 1k~50k 밈을 0.2 SOL로 빠르게 공략하는 메인 전략",
        "bridge_model_id": "C",
        "execution_mode": "sniper_utility",
        "entry_sol": "meme_launch_entry_sol",
    },
    "NARRATIVE": {
        "name": "Narrative",
        "description": "죽은 코인 재점화/장기 내러티브 부활을 잡는 서브 전략",
        "bridge_model_id": "B",
        "execution_mode": "narrative_engine",
        "entry_sol": "meme_narrative_entry_sol",
    },
}
BRIDGE_MEME_MODEL_TO_STRATEGY_ID: dict[str, str] = {
    str(spec.get("bridge_model_id") or "").upper(): strategy_id
    for strategy_id, spec in MEME_STRATEGY_SPECS.items()
    if str(spec.get("bridge_model_id") or "").strip()
}
CRYPTO_STRATEGY_IDS = ("SCALP", "AGGRESSIVE", "SWING10", "CONVICTION1")
CRYPTO_STRATEGY_SPECS: dict[str, dict[str, str]] = {
    "SCALP": {"name": "Range Reversion", "description": "레인지 하단 재진입 계획"},
    "AGGRESSIVE": {"name": "Support Reclaim", "description": "지지 회복 재진입 계획"},
    "SWING10": {"name": "Compression Breakout", "description": "압축 후 돌파 재진입 계획"},
    "CONVICTION1": {"name": "Reset Bounce", "description": "급락 후 리셋 바운스 계획"},
}
MEME_MODEL_IDS = ("A", "B", "C")
CRYPTO_MODEL_IDS = ("A", "B", "C", "D")
ALL_MODEL_IDS = ("A", "B", "C", "D")
MODEL_IDS = MEME_MODEL_IDS
SECRET_UPDATE_KEYS: tuple[str, ...] = (
    "BYBIT_API_KEY",
    "BYBIT_API_SECRET",
    "PHANTOM_WALLET_ADDRESS",
    "SOLANA_PRIVATE_KEY",
    "SOLANA_RPC_URL",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "GOOGLE_API_KEY",
    "SOLSCAN_API_KEY",
    "HELIUS_API_KEY",
    "HELIUS_RPC_URL",
    "HELIUS_WS_URL",
    "HELIUS_SENDER_URL",
    "BIRDEYE_API_KEY",
    "OPENAI_API_KEY",
    "BINANCE_API_KEY",
    "BINANCE_API_SECRET",
    "COINGECKO_API_KEY",
    "CMC_API_KEY",
)
DEFAULT_MODEL_AUTOTUNE_INTERVAL_SECONDS = 86400
MODEL_AUTOTUNE_MIN_CLOSED_TRADES = 8
MODEL_AUTOTUNE_LOOKBACK_TRADES = 80
RUN_TRADE_HISTORY_LIMIT = 9_999_999
RUN_TRADE_HISTORY_MAX_AGE_SECONDS = 60 * 60 * 24 * 190
STATE_BACKUP_INTERVAL_SECONDS = 600
STATE_BACKUP_MAX_FILES = 1000
TELEGRAM_POLL_LOCK_STALE_SECONDS = 30
STATE_PERSIST_MIN_INTERVAL_SECONDS = 12
LOSS_GUARD_DRAWDOWN_RATIO = 0.50
LOSS_GUARD_RESTART_COOLDOWN_SECONDS = 6 * 60 * 60
LIVE_MEME_CLOSE_ALERT_STREAK = 3
LIVE_MEME_CLOSE_ALERT_COOLDOWN_SECONDS = 300
LIVE_ACCOUNTING_SCHEMA_VERSION = 5
LIVE_PENDING_SIGNATURE_TTL_SECONDS = 600
LIVE_EXTERNAL_FLOW_SCAN_LIMIT = 120
LIVE_EXTERNAL_FLOW_PROCESSED_LIMIT = 4000
MEME_C_FIXED_SL_PCT = 0.25
MEME_C_SL_CONFIRM_SECONDS = 90
MEME_C_SL_RECOVERY_RESET_FACTOR = 0.70
MEME_C_REENTRY_WAIT_SECONDS = 120
MODEL_RUNTIME_TUNE_DEFAULTS: dict[str, dict[str, float]] = {
    "A": {"threshold": 0.074, "tp_mul": 1.00, "sl_mul": 0.92},
    "B": {"threshold": 0.076, "tp_mul": 1.08, "sl_mul": 0.90},
    "C": {"threshold": 0.080, "tp_mul": 1.18, "sl_mul": 0.86},
    "D": {"threshold": 0.072, "tp_mul": 0.98, "sl_mul": 0.94},
}
CRYPTO_MODEL_GATE_DEFAULTS: dict[str, dict[str, Any]] = {
    "A": {"rank_min": 1, "rank_max": 20, "trend_stack_min": 0.0, "overheat_max": 0.52, "smallcap_trend_only": False},
    "B": {"rank_min": 1, "rank_max": 20, "trend_stack_min": 0.0, "overheat_max": 0.58, "smallcap_trend_only": False},
    "C": {"rank_min": 1, "rank_max": 20, "trend_stack_min": 0.0, "overheat_max": 0.46, "smallcap_trend_only": False},
    "D": {"rank_min": 1, "rank_max": 20, "trend_stack_min": 0.0, "overheat_max": 0.60, "smallcap_trend_only": False},
}
AUTOTUNE_NOTE_KO: dict[str, str] = {
    "hold": "유지",
    "hold_not_enough_samples": "표본 부족으로 유지",
    "hold_good_pnl": "성과 양호로 유지",
    "hold_clamp_limit": "클램프 한계로 유지",
    "range_reversion_defensive": "레인지 리버전 방어 튜닝",
    "reclaim_defensive": "리클레임 방어 튜닝",
    "breakout_risk_off": "돌파형 리스크오프 튜닝",
    "reset_bounce_defensive": "리셋 바운스 방어 튜닝",
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

MEME_TREND_EXCLUDED_SYMBOLS = {
    # User-requested major meme exclusions for trend brief focus.
    "DOGE",
    "SHIB",
    "PEPE",
    "FLOKI",
    "BONK",
    "WIF",
    # Generic/noisy symbols that are not useful for "new meme theme".
    "MEME",
    "PUMP",
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

MEME_SIMILARITY_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "token",
    "coin",
    "official",
    "of",
    "on",
    "in",
}
MEME_SIMILARITY_LOOKBACK_MINUTES = 180.0
MEME_SIMILARITY_SPLIT_SUFFIXES = ("house", "coin", "token", "meme", "dog", "cat", "ai")

MEME_SMALLCAP_MAX_USD = 1_000_000.0
MEME_TRACKED_MAX_CAP_USD = 5_000_000.0
MEME_TRACKED_ENTRY_MAX_CAP_USD = 3_000_000.0
MEME_THEME_LAUNCH_IDEAL_MIN_CAP_USD = 3_000.0
MEME_THEME_LAUNCH_IDEAL_MAX_CAP_USD = 5_000.0
MEME_THEME_LAUNCH_SOFT_MIN_CAP_USD = 1_800.0
MEME_THEME_LAUNCH_SOFT_MAX_CAP_USD = 8_000.0
MEME_THEME_LAUNCH_HARD_MAX_CAP_USD = 12_000.0
MEME_THEME_LAUNCH_MAX_AGE_MINUTES = 20.0
MEME_THEME_LAUNCH_ENTRY_MAX_AGE_MINUTES = 8.0
MEME_THEME_LAUNCH_MIN_LIQUIDITY_USD = 800.0
MEME_THEME_LAUNCH_MIN_VOLUME_5M_USD = 250.0
MEME_THEME_RAW_FEED_SLOTS = 48
MEME_SNIPER_MIN_CAP_USD = 1_000.0
MEME_SNIPER_MAX_CAP_USD = 50_000.0
MEME_SNIPER_MIN_LIQUIDITY_USD = 450.0
MEME_SNIPER_MIN_VOLUME_5M_USD = 250.0
MEME_SNIPER_MIN_SOCIAL_BURST = 0.55
MEME_SNIPER_MIN_SIGNAL_FIT = 0.58
MEME_PUMPPORTAL_PRIORITY_FEE_SOL = 0.001
MEME_PUMPPORTAL_SLIPPAGE_PCT = 15.0
# Newly discovered meme tokens stay on watch for at least 30 minutes.
MEME_WATCHLIST_TTL_SECONDS = 60 * 30
MEME_WATCHLIST_MAX_TOKENS = 800
MEME_WATCH_SNAPSHOT_CACHE_SECONDS = 60 * 30
MEME_WATCH_SCORE_REFRESH_SECONDS = 180
MEME_MAX_AGE_MINUTES = 60.0 * 24.0 * 365.0
MEME_TREND_THEME_MAX_AGE_MINUTES = 60.0 * 24.0 * 30.0
MEME_EXCLUDE_TOP_RANK_MAX = 500
CRYPTO_TREND_RANK_MIN = 11
CRYPTO_TREND_RANK_MAX = 1000
CRYPTO_HELD_PRICE_JUMP_GUARD_PCT = 0.35

MEME_THEME_KEYWORDS: dict[str, tuple[str, ...]] = {
    "개밈(DOG)": ("DOGE", "SHIB", "FLOKI", "BONK", "WIF", "DOG"),
    "개구리(FROG)": ("PEPE", "FROG"),
    "고양이(CAT)": ("CAT", "POPCAT", "MEW"),
    "AI/에이전트": ("AI", "AGENT", "GPT"),
    "펌프/디젠": ("PUMP", "FUN", "DEGEN", "RUG"),
}

CRYPTO_THEME_KEYWORDS: dict[str, tuple[str, ...]] = {
    "메이저(BTC/ETH)": ("BTC", "ETH"),
    "솔라나 생태계": ("SOL", "JUP", "WIF", "BONK"),
    "AI 섹터": ("TAO", "FET", "RENDER", "RNDR", "AI"),
    "RWA/인프라": ("ONDO", "LINK", "RWA", "ARB", "OP"),
}


class TradingEngine:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()
        self._enforce_paper_lock()
        self.supabase_sync = SupabaseSyncClient(
            url=self.settings.supabase_url,
            secret_key=self.settings.supabase_secret_key,
            enabled=bool(self.settings.supabase_sync_enabled),
            timeout_seconds=int(self.settings.supabase_sync_timeout_seconds),
        )
        self.state: EngineState = self._load_bootstrap_state()
        if self.state.cash_usd <= 0:
            self.state.cash_usd = float(self.settings.paper_start_cash_usd)
        if self.state.demo_seed_usdt <= 0:
            self.state.demo_seed_usdt = float(self.settings.demo_seed_usdt)

        self.model: OnlineModel = self._load_bootstrap_model()
        self.runtime_feedback = RuntimeFeedbackStore(self.settings.runtime_feedback_db_file)
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
            runtime_feedback_store=self.runtime_feedback,
        )
        helius_rpc_url = str(self.settings.helius_rpc_url or "").strip()
        helius_ws_url = str(self.settings.helius_ws_url or "").strip()
        helius_sender_url = str(self.settings.helius_sender_url or "").strip()
        if self.settings.helius_api_key:
            if not helius_rpc_url:
                helius_rpc_url = f"https://mainnet.helius-rpc.com/?api-key={self.settings.helius_api_key}"
            if not helius_ws_url:
                helius_ws_url = f"wss://mainnet.helius-rpc.com/?api-key={self.settings.helius_api_key}"
            if not helius_sender_url:
                helius_sender_url = "https://sender.helius-rpc.com/fast"
        self.meme_discovery = MemeDiscoveryService(
            MemeDiscoveryConfig(
                helius_api_key=self.settings.helius_api_key,
                helius_rpc_url=helius_rpc_url,
                helius_ws_url=helius_ws_url,
                helius_sender_url=helius_sender_url,
                birdeye_api_key=self.settings.birdeye_api_key,
                social_4chan_enabled=bool(self.settings.social_4chan_enabled),
                social_4chan_boards=self.settings.social_4chan_boards,
                social_4chan_max_threads_per_board=int(self.settings.social_4chan_max_threads_per_board),
                meme_sniper_poll_seconds=int(self.settings.meme_sniper_poll_seconds),
                meme_sniper_social_window_seconds=int(self.settings.meme_sniper_social_window_seconds),
                meme_theme_cluster_min_tokens=int(self.settings.meme_theme_cluster_min_tokens),
            )
        )
        self.openai_advisor = OpenAICandidateAdvisor(
            api_key=self.settings.openai_api_key,
            model=self.settings.openai_model,
            enabled=bool(self.settings.openai_review_enabled),
            monthly_budget_usd=float(self.settings.openai_monthly_budget_usd),
            daily_budget_usd=float(self.settings.openai_daily_budget_usd),
            candidate_review_interval_seconds=int(self.settings.openai_candidate_review_interval_seconds),
            candidate_top_n=int(self.settings.openai_candidate_top_n),
            candidate_min_score=float(self.settings.openai_candidate_min_score),
            narrative_max_calls_per_day=int(self.settings.openai_narrative_max_calls_per_day),
            input_token_estimate=int(self.settings.openai_input_token_estimate),
            output_token_estimate=int(self.settings.openai_output_token_estimate),
            state_path=self.settings.openai_budget_state_file,
        )
        self.wallet = SolanaWalletTracker(self.settings.solana_rpc_url)
        self.solana_trader = JupiterSolanaTrader(
            rpc_url=self.settings.solana_rpc_url,
            private_key=self.settings.solana_private_key,
            wallet_address=self.settings.phantom_wallet_address,
        )
        self.pumpportal_trader = PumpPortalLocalTrader(
            rpc_url=self.settings.solana_rpc_url,
            private_key=self.settings.solana_private_key,
            wallet_address=self.settings.phantom_wallet_address,
        )
        self.bybit = BybitV5Client(
            self.settings.bybit_api_key,
            self.settings.bybit_api_secret,
            self.settings.bybit_base_url,
            self.settings.bybit_recv_window,
        )
        self.openai_advisor = OpenAICandidateAdvisor(
            api_key=self.settings.openai_api_key,
            model=self.settings.openai_model,
            enabled=bool(self.settings.openai_review_enabled),
            monthly_budget_usd=float(self.settings.openai_monthly_budget_usd),
            daily_budget_usd=float(self.settings.openai_daily_budget_usd),
            candidate_review_interval_seconds=int(self.settings.openai_candidate_review_interval_seconds),
            candidate_top_n=int(self.settings.openai_candidate_top_n),
            candidate_min_score=float(self.settings.openai_candidate_min_score),
            narrative_max_calls_per_day=int(self.settings.openai_narrative_max_calls_per_day),
            input_token_estimate=int(self.settings.openai_input_token_estimate),
            output_token_estimate=int(self.settings.openai_output_token_estimate),
            state_path=self.settings.openai_budget_state_file,
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
        self._crypto_model_watch_pools: dict[str, list[str]] = {}
        self._crypto_model_watch_pool_next_refresh_ts: dict[str, int] = {}
        self._wallet_pattern_cache: dict[str, dict[str, Any]] = {}
        self._focus_wallet_analysis: dict[str, Any] = {}
        self._trend_source_status: dict[str, Any] = {}
        self._trend_cache_trending: set[str] = set()
        self._trend_cache_events: dict[str, list[TrendEvent]] = {}
        self._trend_next_fetch_ts: dict[str, int] = {}
        self._new_meme_feed: list[dict[str, Any]] = []
        self._meme_symbol_market_caps: dict[str, float] = {}
        self._meme_symbol_age_minutes: dict[str, float] = {}
        self._meme_score_log_guard: dict[str, dict[str, Any]] = {}
        self._meme_watch_tokens: dict[str, int] = {}
        self._meme_watch_snapshot_cache: dict[str, dict[str, Any]] = {}
        self._meme_watch_score_last_ts: dict[str, int] = {}
        self._meme_watch_latest: dict[str, dict[str, Any]] = {}
        self._last_wallet_sync = 0
        self._last_bybit_sync = 0
        self._pending_live_trade_signatures: dict[str, int] = {}
        self._last_telegram_poll = 0
        self._telegram_thread_start_ts = 0
        self._last_telegram_report = 0
        self._runtime_error_notice: dict[str, dict[str, Any]] = {}
        self._last_state_backup_ts = 0
        self._last_persist_ts = 0
        self._last_trend_brief_emit_ts = 0
        self._trend_prev_hits: dict[str, dict[str, int]] = {"meme": {}, "crypto": {}}
        self._run_epoch = 0
        self._telegram_inflight_lock = threading.Lock()
        self._telegram_poll_lock_path = self._telegram_lock_path_for_token(self.settings.telegram_bot_token)
        self._telegram_webhook_init_done = False
        self._restart_request_lock = threading.Lock()
        self._restart_requested = False
        self._dashboard_cache: dict[str, Any] = {}
        self._dashboard_cache_ts = 0.0
        self._dashboard_cache_ttl_seconds = _clamp(float(getattr(self.settings, "scan_interval_seconds", 20)), 3.0, 20.0)
        self._dashboard_cache_cycle_ts = 0
        self._dashboard_cache_wallet_ts = 0
        self._dashboard_cache_bybit_ts = 0
        self._feedback_cache: dict[str, Any] = {}
        self._feedback_cache_ts = 0.0
        self._feedback_cache_ttl_seconds = 60.0
        self._repo_root = Path(__file__).resolve().parents[1]
        self._git_daily_report_kv_key = "git_daily_pnl_report_state"

        self._ensure_model_runs()
        self._sync_primary_views_from_model_a()

    @property
    def running(self) -> bool:
        return self._running

    def _load_bootstrap_state(self) -> EngineState:
        local_path = Path(str(self.settings.state_file or "state.json"))
        if local_path.exists():
            return load_state(str(local_path), self.settings.paper_start_cash_usd)
        if self.supabase_sync.enabled:
            try:
                result = self.supabase_sync.fetch_blob("engine_state")
                payload = result.get("payload") if bool(result.get("ok")) else None
                if isinstance(payload, dict):
                    return state_from_dict(payload, self.settings.paper_start_cash_usd)
            except Exception:
                pass
        return EngineState(cash_usd=float(self.settings.paper_start_cash_usd))

    def _load_bootstrap_model(self) -> OnlineModel:
        local_path = Path(str(self.settings.model_file or "model_online.json"))
        if local_path.exists():
            return load_online_model(str(local_path))
        if self.supabase_sync.enabled:
            try:
                result = self.supabase_sync.fetch_blob("online_model")
                payload = result.get("payload") if bool(result.get("ok")) else None
                if isinstance(payload, dict):
                    return OnlineModel.from_dict(payload)
            except Exception:
                pass
        return OnlineModel()

    def _load_daily_git_report_state(self) -> dict[str, Any]:
        try:
            state = dict(self.runtime_feedback.load_kv(self._git_daily_report_kv_key) or {})
        except Exception:
            state = {}
        if state:
            return state
        if self.supabase_sync.enabled:
            try:
                result = self.supabase_sync.fetch_blob(self._git_daily_report_kv_key)
                payload = result.get("payload") if bool(result.get("ok")) else None
                if isinstance(payload, dict):
                    return payload
            except Exception:
                pass
        return {}

    def _save_daily_git_report_state(self, payload: dict[str, Any], now_ts: int) -> None:
        try:
            self.runtime_feedback.save_kv(self._git_daily_report_kv_key, payload, now_ts=int(now_ts))
        except Exception:
            pass
        if self.supabase_sync.enabled:
            try:
                self.supabase_sync.upsert_blob(self._git_daily_report_kv_key, payload)
            except Exception:
                pass

    def _persist_supabase_state(self) -> None:
        if not self.supabase_sync.enabled:
            return
        try:
            self.supabase_sync.upsert_blob("engine_state", state_to_dict(self.state))
            self.supabase_sync.upsert_blob("online_model", self.model.to_dict())
        except Exception:
            pass

    def _reload_settings(self) -> None:
        prev_token = str(getattr(getattr(self, "telegram", None), "bot_token", "") or "")
        prev_feedback_db = str(getattr(getattr(self, "runtime_feedback", None), "db_path", "") or "")
        latest = load_settings()
        self.settings = latest
        self._enforce_paper_lock()
        self.supabase_sync = SupabaseSyncClient(
            url=self.settings.supabase_url,
            secret_key=self.settings.supabase_secret_key,
            enabled=bool(self.settings.supabase_sync_enabled),
            timeout_seconds=int(self.settings.supabase_sync_timeout_seconds),
        )
        feedback_db_changed = str(self.settings.runtime_feedback_db_file or "").strip() != prev_feedback_db
        if feedback_db_changed:
            self.runtime_feedback = RuntimeFeedbackStore(self.settings.runtime_feedback_db_file)
        self.wallet = SolanaWalletTracker(self.settings.solana_rpc_url)
        self.solana_trader = JupiterSolanaTrader(
            rpc_url=self.settings.solana_rpc_url,
            private_key=self.settings.solana_private_key,
            wallet_address=self.settings.phantom_wallet_address,
        )
        self.pumpportal_trader = PumpPortalLocalTrader(
            rpc_url=self.settings.solana_rpc_url,
            private_key=self.settings.solana_private_key,
            wallet_address=self.settings.phantom_wallet_address,
        )
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
                runtime_feedback_store=self.runtime_feedback,
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
            self.trend.google_trend_interval_seconds = max(14000, int(self.settings.google_trend_interval_seconds))
            self.trend.google_trend_cooldown_seconds = max(14000, int(self.settings.google_trend_cooldown_seconds))
            self.trend.google_trend_max_symbols = max(5, min(40, int(self.settings.google_trend_max_symbols)))
            self.trend.runtime_feedback_store = self.runtime_feedback
            if feedback_db_changed and hasattr(self.trend, "_load_google_runtime_state"):
                try:
                    self.trend._load_google_runtime_state()
                except Exception:
                    pass
        self.bybit = BybitV5Client(
            self.settings.bybit_api_key,
            self.settings.bybit_api_secret,
            self.settings.bybit_base_url,
            self.settings.bybit_recv_window,
        )
        self.alert_manager = AlertManager(self.settings.telegram_bot_token, self.settings.telegram_chat_id)
        self.telegram = TelegramBotClient(self.settings.telegram_bot_token)
        self._dashboard_cache_ttl_seconds = _clamp(
            float(getattr(self.settings, "scan_interval_seconds", 20)),
            3.0,
            20.0,
        )
        if str(self.settings.telegram_bot_token or "") != prev_token:
            self._release_telegram_poll_lock(force=True)
            self._telegram_poll_lock_path = self._telegram_lock_path_for_token(self.settings.telegram_bot_token)
            self._telegram_webhook_init_done = False

    def _invalidate_dashboard_cache(self) -> None:
        with self._lock:
            self._dashboard_cache = {}
            self._dashboard_cache_ts = 0.0
            self._dashboard_cache_cycle_ts = 0
            self._dashboard_cache_wallet_ts = 0
            self._dashboard_cache_bybit_ts = 0

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

    @staticmethod
    def _wallet_equity_usd(wallet_assets: list[dict[str, Any]]) -> float:
        return float(sum(float(a.get("value_usd") or 0.0) for a in list(wallet_assets or [])))

    @staticmethod
    def _bybit_equity_usd(bybit_assets: list[dict[str, Any]]) -> float:
        return float(sum(float(a.get("usd_value") or 0.0) for a in list(bybit_assets or [])))

    def _live_equity_usd_from_assets(self, wallet_assets: list[dict[str, Any]], bybit_assets: list[dict[str, Any]]) -> float:
        total = 0.0
        if bool(self.settings.live_enable_meme):
            total += self._wallet_equity_usd(wallet_assets)
        if bool(self.settings.live_enable_crypto):
            total += self._bybit_equity_usd(bybit_assets)
        return float(total)

    def _has_live_open_positions(self, runs: dict[str, Any] | None = None) -> bool:
        table = dict(runs or {})
        if bool(self.settings.live_enable_meme):
            for model_id in MEME_MODEL_IDS:
                run = self._get_market_run(table, "meme", model_id)
                for pos in list((run.get("meme_positions") or {}).values()):
                    if str((pos or {}).get("mode") or "").strip().lower() == "live":
                        return True
        if bool(self.settings.live_enable_crypto):
            with self._lock:
                bybit_positions = list(self.state.bybit_positions or [])
            if bybit_positions:
                return True
        return False

    def _sync_live_seed_if_idle(self, now_ts: int, force: bool = False) -> None:
        if str(self.settings.trade_mode or "").lower() != "live":
            return
        with self._lock:
            live_equity = self._live_equity_usd_from_assets(self.state.wallet_assets, self.state.bybit_assets)
            current_seed = float(self.state.live_seed_usd or 0.0)
            should_sync = bool(force or current_seed <= 0.0)
            if not should_sync:
                return
            if current_seed > 0.0:
                gap = abs(live_equity - current_seed)
                if gap <= max(0.5, current_seed * 0.0025):
                    return
            anchor = float(getattr(self.state, "live_perf_anchor_usd", 0.0) or 0.0)
            seed_value = float(anchor if anchor > 0.0 else live_equity)
            self.state.live_seed_usd = float(seed_value)
            self.state.live_seed_set_ts = int(now_ts)
            if anchor <= 0.0:
                self.state.live_perf_anchor_usd = float(live_equity)
                self.state.live_perf_anchor_ts = int(now_ts)
                self.state.live_net_flow_usd = float(getattr(self.state, "live_net_flow_usd", 0.0) or 0.0)

    def _live_performance_view_locked(
        self,
        live_equity_usd: float | None = None,
        now_ts: int | None = None,
        ensure_anchor: bool = True,
    ) -> dict[str, Any]:
        now = int(now_ts or int(time.time()))
        live_equity = float(
            live_equity_usd
            if live_equity_usd is not None
            else self._live_equity_usd_from_assets(self.state.wallet_assets, self.state.bybit_assets)
        )
        anchor = float(getattr(self.state, "live_perf_anchor_usd", 0.0) or 0.0)
        anchor_ts = int(getattr(self.state, "live_perf_anchor_ts", 0) or 0)
        net_flow = float(getattr(self.state, "live_net_flow_usd", 0.0) or 0.0)
        if ensure_anchor and anchor <= 0.0:
            anchor = float(live_equity)
            anchor_ts = int(now)
            self.state.live_perf_anchor_usd = float(anchor)
            self.state.live_perf_anchor_ts = int(anchor_ts)
        adjusted_equity = float(live_equity - net_flow)
        perf_pnl = float(adjusted_equity - anchor)
        perf_roi = float((perf_pnl / max(anchor, 1e-9)) * 100.0) if anchor > 0.0 else 0.0
        return {
            "live_perf_anchor_usd": float(anchor),
            "live_perf_anchor_ts": int(anchor_ts),
            "live_net_flow_usd": float(net_flow),
            "live_adjusted_equity_usd": float(adjusted_equity),
            "live_perf_pnl_usd": float(perf_pnl),
            "live_perf_roi_pct": float(perf_roi),
        }

    def set_live_performance_anchor_now(self, reset_net_flow: bool = True) -> dict[str, Any]:
        now_ts = int(time.time())
        with self._lock:
            live_equity = self._live_equity_usd_from_assets(self.state.wallet_assets, self.state.bybit_assets)
            self.state.live_perf_anchor_usd = float(live_equity)
            self.state.live_perf_anchor_ts = int(now_ts)
            if bool(reset_net_flow):
                self.state.live_net_flow_usd = 0.0
            perf = self._live_performance_view_locked(live_equity_usd=live_equity, now_ts=now_ts, ensure_anchor=False)
        self._persist(force=True)
        self._invalidate_dashboard_cache()
        net_flow_value = float(perf.get("live_net_flow_usd") or 0.0)
        net_flow_text = "0.00" if reset_net_flow else f"{net_flow_value:+.2f}"
        self._push_alert(
            "info",
            "실전 성과 기준선 설정",
            f"기준자산={float(perf.get('live_perf_anchor_usd') or 0.0):.2f} USD | 순입출금 보정={net_flow_text} USD",
            send_telegram=False,
        )
        return perf

    def adjust_live_net_flow(self, delta_usd: float, note: str = "") -> dict[str, Any]:
        delta = float(delta_usd)
        if abs(delta) < 1e-9:
            raise ValueError("delta_usd must be non-zero")
        now_ts = int(time.time())
        with self._lock:
            live_equity = self._live_equity_usd_from_assets(self.state.wallet_assets, self.state.bybit_assets)
            _ = self._live_performance_view_locked(live_equity_usd=live_equity, now_ts=now_ts, ensure_anchor=True)
            current = float(getattr(self.state, "live_net_flow_usd", 0.0) or 0.0)
            self.state.live_net_flow_usd = float(current + delta)
            perf = self._live_performance_view_locked(live_equity_usd=live_equity, now_ts=now_ts, ensure_anchor=False)
        self._persist(force=True)
        self._invalidate_dashboard_cache()
        flow = float(perf.get("live_net_flow_usd") or 0.0)
        label = "입금" if delta > 0 else "출금"
        detail_note = f" | 메모={note.strip()}" if str(note or "").strip() else ""
        self._push_alert(
            "info",
            "실전 순입출금 보정",
            f"{label} 보정 {delta:+.2f} USD 반영 | 누적 보정 {flow:+.2f} USD{detail_note}",
            send_telegram=False,
        )
        return perf

    @staticmethod
    def _extract_live_tx_signature(reason: str) -> str:
        text = str(reason or "").strip()
        if not text:
            return ""
        m = re.search(r"(?:^|\|)live_tx=([1-9A-HJ-NP-Za-km-z]+)", text)
        return str(m.group(1)).strip() if m else ""

    @staticmethod
    def _trade_reason_family(reason: str) -> str:
        text = str(reason or "").strip().lower()
        if not text:
            return ""
        head = text.split("|", 1)[0].strip()
        family = head.split(" ", 1)[0].strip()
        return family

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        try:
            return float(value)
        except Exception:
            return None

    def _live_trade_is_realized(self, row: dict[str, Any]) -> bool:
        side = str((row or {}).get("side") or "").strip().lower()
        if side != "sell":
            return False
        if bool((row or {}).get("accounting_excluded")):
            return False
        reason = str((row or {}).get("reason") or "").strip().lower()
        if "wallet_miss_cleanup" in reason:
            return False
        if "realized" in dict(row or {}):
            return bool((row or {}).get("realized"))
        return True

    def _live_trade_realized_pnl_usd(self, row: dict[str, Any]) -> float | None:
        if not self._live_trade_is_realized(row):
            return None
        explicit = self._optional_float((row or {}).get("realized_pnl_usd"))
        if explicit is not None:
            return float(explicit)
        fallback = self._optional_float((row or {}).get("pnl_usd"))
        return float(fallback) if fallback is not None else None

    def _live_trade_realized_pnl_pct(self, row: dict[str, Any]) -> float | None:
        if not self._live_trade_is_realized(row):
            return None
        explicit = self._optional_float((row or {}).get("realized_pnl_pct"))
        if explicit is not None:
            return float(explicit)
        fallback = self._optional_float((row or {}).get("pnl_pct"))
        return float(fallback) if fallback is not None else None

    def _prune_pending_live_trade_signatures(self, now_ts: int | None = None) -> None:
        now = int(now_ts or int(time.time()))
        keep: dict[str, int] = {}
        for sig, ts in dict(self._pending_live_trade_signatures or {}).items():
            if not sig:
                continue
            if (now - int(ts or 0)) <= int(LIVE_PENDING_SIGNATURE_TTL_SECONDS):
                keep[str(sig)] = int(ts or now)
        self._pending_live_trade_signatures = keep

    def _mark_pending_live_trade_signature(self, signature: str, now_ts: int | None = None) -> None:
        sig = str(signature or "").strip()
        if not sig:
            return
        now = int(now_ts or int(time.time()))
        self._prune_pending_live_trade_signatures(now)
        self._pending_live_trade_signatures[sig] = int(now)

    def _known_live_trade_signatures(self) -> set[str]:
        self._prune_pending_live_trade_signatures()
        out: set[str] = {str(sig) for sig in dict(self._pending_live_trade_signatures or {}).keys() if str(sig)}
        with self._lock:
            runs = dict(self.state.model_runs or {})
        for model_id in self._all_model_ids():
            for market in ("meme", "crypto"):
                run = self._get_market_run(runs, market, model_id)
                for tr in list(run.get("trades") or []):
                    if not self._is_live_trade_row(tr):
                        continue
                    sig = self._extract_live_tx_signature(str((tr or {}).get("reason") or ""))
                    if sig:
                        out.add(sig)
        return out

    def _capture_live_wallet_snapshot(self, token_address: str = "") -> dict[str, Any]:
        if not self.settings.phantom_wallet_address or not self.wallet.enabled:
            return {}
        try:
            return self.wallet.get_wallet_snapshot(self.settings.phantom_wallet_address, token_address)
        except Exception:
            return {}

    def _live_sol_price_usd(self) -> float:
        with self._lock:
            wallet_assets = list(self.state.wallet_assets or [])
        for row in wallet_assets:
            if str((row or {}).get("symbol") or "").upper().strip() == "SOL":
                px = max(0.0, float((row or {}).get("price_usd") or 0.0))
                if px > 0.0:
                    return float(px)
        try:
            px = max(0.0, float(self.wallet._get_sol_price_usd() or 0.0))
            if px > 0.0:
                return float(px)
        except Exception:
            pass
        try:
            budget = self._solana_trade_budget()
            return max(0.0, float(budget.get("sol_price_usd") or 0.0))
        except Exception:
            return 0.0

    def _live_swap_accounting_from_signature(
        self,
        token_address: str,
        swap_signature: str,
        now_ts: int,
        side: str,
        fallback_sol_price_usd: float,
    ) -> dict[str, Any]:
        sig = str(swap_signature or "").strip()
        token = str(token_address or "").strip()
        if not sig or not token or not self.wallet.enabled or not self.settings.phantom_wallet_address:
            return {}
        try:
            tx_summary = self.wallet.get_wallet_transaction_deltas(
                sig,
                self.settings.phantom_wallet_address,
                tracked_mints={token},
            )
        except Exception:
            tx_summary = {}
        if not tx_summary:
            return {}
        sol_price_usd = max(0.0, float(fallback_sol_price_usd or 0.0), self._live_sol_price_usd())
        token_row = dict((tx_summary.get("token_deltas") or {}).get(token) or {})
        before_sol_lamports = int(tx_summary.get("wallet_pre_sol_lamports") or 0)
        after_sol_lamports = int(tx_summary.get("wallet_post_sol_lamports") or 0)
        before_token_raw = int(token_row.get("pre_raw") or 0)
        after_token_raw = int(token_row.get("post_raw") or 0)
        decimals = int(token_row.get("decimals") or 0)
        fee_lamports = int(tx_summary.get("fee_lamports") or 0)
        sol_delta_lamports = int(tx_summary.get("net_sol_change_lamports") or (after_sol_lamports - before_sol_lamports))
        token_delta_raw = int(token_row.get("delta_raw") or (after_token_raw - before_token_raw))
        qty_delta = float(token_row.get("delta_qty") or 0.0)
        if abs(qty_delta) <= 1e-12 and token_delta_raw != 0:
            qty_delta = float(token_delta_raw) / float(10**max(0, decimals))
        fee_usd = (float(fee_lamports) / 1_000_000_000.0) * sol_price_usd if fee_lamports > 0 else 0.0
        result = {
            "signature": sig,
            "ts": int(now_ts),
            "side": str(side or "").lower().strip(),
            "before_sol_lamports": int(before_sol_lamports),
            "after_sol_lamports": int(after_sol_lamports),
            "before_token_raw": int(before_token_raw),
            "after_token_raw": int(after_token_raw),
            "token_decimals": int(decimals),
            "fee_lamports": int(fee_lamports),
            "fee_usd": float(fee_usd),
            "net_sol_delta_lamports": int(sol_delta_lamports),
            "net_sol_delta_usd": float((float(sol_delta_lamports) / 1_000_000_000.0) * sol_price_usd),
            "token_delta_raw": int(token_delta_raw),
            "token_delta_qty": float(qty_delta),
            "sol_price_usd": float(sol_price_usd),
            "tx_summary": dict(tx_summary or {}),
        }
        if str(side or "").lower().strip() == "buy":
            spent_lamports = max(0, -int(sol_delta_lamports))
            received_qty = max(0.0, float(qty_delta))
            spent_usd = (float(spent_lamports) / 1_000_000_000.0) * sol_price_usd
            avg_price_usd = spent_usd / max(received_qty, 1e-12) if received_qty > 0.0 else 0.0
            result.update(
                {
                    "spent_lamports": int(spent_lamports),
                    "spent_usd": float(spent_usd),
                    "received_qty": float(received_qty),
                    "avg_price_usd": float(avg_price_usd),
                }
            )
        else:
            received_lamports = max(0, int(sol_delta_lamports))
            sold_qty = max(0.0, -float(qty_delta))
            proceeds_usd = (float(received_lamports) / 1_000_000_000.0) * sol_price_usd
            avg_exit_price_usd = proceeds_usd / max(sold_qty, 1e-12) if sold_qty > 0.0 else 0.0
            result.update(
                {
                    "received_lamports": int(received_lamports),
                    "proceeds_usd": float(proceeds_usd),
                    "sold_qty": float(sold_qty),
                    "avg_exit_price_usd": float(avg_exit_price_usd),
                }
            )
        return result

    def _apply_live_swap_accounting(
        self,
        token_address: str,
        swap_signature: str,
        before_snapshot: dict[str, Any],
        now_ts: int,
        side: str,
        fallback_sol_price_usd: float,
    ) -> dict[str, Any]:
        after_snapshot = self._capture_live_wallet_snapshot(token_address)
        tx_summary: dict[str, Any] = {}
        sig = str(swap_signature or "").strip()
        if sig and self.wallet.enabled and self.settings.phantom_wallet_address:
            try:
                tx_summary = self.wallet.get_wallet_transaction_deltas(
                    sig,
                    self.settings.phantom_wallet_address,
                    tracked_mints={str(token_address or "").strip()},
                )
            except Exception:
                tx_summary = {}
        sol_price_usd = max(0.0, float(fallback_sol_price_usd or 0.0), self._live_sol_price_usd())
        before_sol_lamports = int(before_snapshot.get("sol_lamports") or 0)
        after_sol_lamports = int(after_snapshot.get("sol_lamports") or 0)
        before_token_raw = int(before_snapshot.get("token_raw_amount") or 0)
        after_token_raw = int(after_snapshot.get("token_raw_amount") or 0)
        decimals = int(after_snapshot.get("token_decimals") or before_snapshot.get("token_decimals") or 0)
        if tx_summary:
            before_sol_lamports = int(tx_summary.get("wallet_pre_sol_lamports") or before_sol_lamports)
            after_sol_lamports = int(tx_summary.get("wallet_post_sol_lamports") or after_sol_lamports)
            fee_lamports = int(tx_summary.get("fee_lamports") or 0)
            token_row = dict((tx_summary.get("token_deltas") or {}).get(str(token_address or "").strip()) or {})
            before_token_raw = int(token_row.get("pre_raw") or before_token_raw)
            after_token_raw = int(token_row.get("post_raw") or after_token_raw)
            decimals = int(token_row.get("decimals") or decimals)
            sol_delta_lamports = int(tx_summary.get("net_sol_change_lamports") or (after_sol_lamports - before_sol_lamports))
        else:
            fee_lamports = 0
            sol_delta_lamports = int(after_sol_lamports - before_sol_lamports)
        token_delta_raw = int(after_token_raw - before_token_raw)
        qty_delta = float(token_delta_raw) / float(10**max(0, decimals)) if token_delta_raw != 0 else 0.0
        fee_usd = (float(fee_lamports) / 1_000_000_000.0) * sol_price_usd if fee_lamports > 0 else 0.0
        result = {
            "signature": sig,
            "ts": int(now_ts),
            "side": str(side or "").lower().strip(),
            "before_sol_lamports": int(before_sol_lamports),
            "after_sol_lamports": int(after_sol_lamports),
            "before_token_raw": int(before_token_raw),
            "after_token_raw": int(after_token_raw),
            "token_decimals": int(decimals),
            "fee_lamports": int(fee_lamports),
            "fee_usd": float(fee_usd),
            "net_sol_delta_lamports": int(sol_delta_lamports),
            "net_sol_delta_usd": float((float(sol_delta_lamports) / 1_000_000_000.0) * sol_price_usd),
            "token_delta_raw": int(token_delta_raw),
            "token_delta_qty": float(qty_delta),
            "sol_price_usd": float(sol_price_usd),
            "tx_summary": dict(tx_summary or {}),
        }
        if str(side or "").lower().strip() == "buy":
            spent_lamports = max(0, -int(sol_delta_lamports))
            received_qty = max(0.0, float(qty_delta))
            spent_usd = (float(spent_lamports) / 1_000_000_000.0) * sol_price_usd
            avg_price_usd = spent_usd / max(received_qty, 1e-12) if received_qty > 0.0 else 0.0
            result.update(
                {
                    "spent_lamports": int(spent_lamports),
                    "spent_usd": float(spent_usd),
                    "received_qty": float(received_qty),
                    "avg_price_usd": float(avg_price_usd),
                }
            )
        else:
            received_lamports = max(0, int(sol_delta_lamports))
            sold_qty = max(0.0, -float(qty_delta))
            proceeds_usd = (float(received_lamports) / 1_000_000_000.0) * sol_price_usd
            avg_exit_price_usd = proceeds_usd / max(sold_qty, 1e-12) if sold_qty > 0.0 else 0.0
            result.update(
                {
                    "received_lamports": int(received_lamports),
                    "proceeds_usd": float(proceeds_usd),
                    "sold_qty": float(sold_qty),
                    "avg_exit_price_usd": float(avg_exit_price_usd),
                }
            )
        return result

    def _estimate_wallet_tx_flow_usd(self, tx_summary: dict[str, Any]) -> float:
        if not tx_summary:
            return 0.0
        sol_price_usd = max(0.0, float(self._live_sol_price_usd() or 0.0))
        total = (float(tx_summary.get("net_sol_change_lamports") or 0.0) / 1_000_000_000.0) * sol_price_usd
        for mint, row in dict(tx_summary.get("token_deltas") or {}).items():
            qty = float((row or {}).get("delta_qty") or 0.0)
            if abs(qty) <= 1e-12:
                continue
            price = self._resolve_price(str(mint or "").strip())
            if price <= 0.0:
                try:
                    snap = self.dex.fetch_snapshot_for_token(self.settings.dex_chain, str(mint or "").strip())
                except Exception:
                    snap = None
                if snap and float(getattr(snap, "price_usd", 0.0) or 0.0) > 0.0:
                    price = float(getattr(snap, "price_usd", 0.0) or 0.0)
                    self._last_prices[str(mint or "").strip()] = float(price)
            if price <= 0.0:
                continue
            total += qty * price
        return float(total)

    def _detect_and_apply_external_live_flows(self, now_ts: int) -> None:
        if not self.settings.phantom_wallet_address or not self.wallet.enabled:
            return
        known_trade_sigs = self._known_live_trade_signatures()
        with self._lock:
            runs = self.state.model_runs if isinstance(self.state.model_runs, dict) else {}
            if not isinstance(runs, dict):
                runs = {}
                self.state.model_runs = runs
            flow_state = dict(runs.get("_live_external_flow_state") or {})
            if int(flow_state.get("schema_version") or 0) < int(LIVE_ACCOUNTING_SCHEMA_VERSION):
                flow_state = {}
            processed = {str(k): int(v or 0) for k, v in dict(flow_state.get("processed") or {}).items() if str(k)}
        try:
            sig_rows = self.wallet.get_signatures_for_address(
                self.settings.phantom_wallet_address,
                limit=LIVE_EXTERNAL_FLOW_SCAN_LIMIT,
            )
        except Exception:
            return
        if not sig_rows:
            return
        changed = False
        for row in reversed(sig_rows):
            sig = str((row or {}).get("signature") or "").strip()
            if not sig or sig in processed:
                continue
            processed[sig] = int(now_ts)
            if sig in known_trade_sigs:
                changed = True
                continue
            try:
                tx_summary = self.wallet.get_wallet_transaction_deltas(
                    sig,
                    self.settings.phantom_wallet_address,
                    tracked_mints=None,
                )
            except Exception:
                changed = True
                continue
            if not tx_summary or tx_summary.get("meta_err") is not None:
                changed = True
                continue
            token_deltas = dict(tx_summary.get("token_deltas") or {})
            tracked_touch = any(abs(float((item or {}).get("delta_qty") or 0.0)) > 1e-12 for item in token_deltas.values())
            non_sol_touch = sum(1 for item in token_deltas.values() if abs(float((item or {}).get("delta_qty") or 0.0)) > 1e-12)
            net_sol_delta_usd = (float(tx_summary.get("net_sol_change_lamports") or 0.0) / 1_000_000_000.0) * self._live_sol_price_usd()
            swap_like = bool(non_sol_touch >= 1 and abs(net_sol_delta_usd) >= 1.0)
            if not tracked_touch and swap_like:
                changed = True
                continue
            flow_delta_usd = self._estimate_wallet_tx_flow_usd(tx_summary)
            if abs(flow_delta_usd) < 0.25:
                changed = True
                continue
            with self._lock:
                current = float(getattr(self.state, "live_net_flow_usd", 0.0) or 0.0)
                self.state.live_net_flow_usd = float(current + flow_delta_usd)
            direction = "입금/유입" if flow_delta_usd > 0 else "출금/유출"
            try:
                self.runtime_feedback.append_event(
                    source="live:wallet_flow",
                    level="info",
                    status="external_flow_applied",
                    action="외부 지갑 활동을 실전 거래손익이 아닌 입출금 보정으로 분리했습니다.",
                    detail=f"{direction} {flow_delta_usd:+.2f} USD | sig={sig[:10]}...",
                    meta={
                        "signature": sig,
                        "delta_usd": float(flow_delta_usd),
                        "tracked_touch": bool(tracked_touch),
                        "swap_like": bool(swap_like),
                    },
                    now_ts=int(now_ts),
                )
            except Exception:
                pass
            changed = True
        if changed:
            rows = sorted(processed.items(), key=lambda kv: int(kv[1] or 0), reverse=True)[:LIVE_EXTERNAL_FLOW_PROCESSED_LIMIT]
            flow_state["processed"] = {str(k): int(v or 0) for k, v in rows}
            flow_state["schema_version"] = int(LIVE_ACCOUNTING_SCHEMA_VERSION)
            flow_state["last_scan_ts"] = int(now_ts)
            with self._lock:
                runs = self.state.model_runs if isinstance(self.state.model_runs, dict) else {}
                if not isinstance(runs, dict):
                    runs = {}
                runs["_live_external_flow_state"] = dict(flow_state)
                self.state.model_runs = runs

    def _reconcile_live_meme_trade_history(self, now_ts: int) -> bool:
        with self._lock:
            runs = self.state.model_runs if isinstance(self.state.model_runs, dict) else {}
            if not isinstance(runs, dict):
                runs = {}
                self.state.model_runs = runs
            accounting_state = dict(runs.get("_live_accounting_state") or {})
            saved_version = int(accounting_state.get("schema_version") or 0)
            if saved_version >= int(LIVE_ACCOUNTING_SCHEMA_VERSION):
                return False
        changed = False
        for model_id in MEME_MODEL_IDS:
            with self._lock:
                runs = self.state.model_runs if isinstance(self.state.model_runs, dict) else {}
                run = self._get_market_run(runs, "meme", model_id)
                trades = list(run.get("trades") or [])
            if not trades:
                continue
            ordered_for_dedupe = sorted(enumerate(trades), key=lambda item: int(((item[1] or {}).get("ts") or 0)))
            stale_sell_indices: set[int] = set()
            for pos, (idx, tr) in enumerate(ordered_for_dedupe):
                row = dict(tr or {})
                if not self._is_live_trade_row(row):
                    continue
                if str(row.get("source") or "").strip().lower() != "memecoin":
                    continue
                if str(row.get("side") or "").strip().lower() != "sell":
                    continue
                reason = str(row.get("reason") or "")
                reason_l = reason.lower()
                if "wallet_miss_cleanup" in reason_l:
                    continue
                if self._extract_live_tx_signature(reason):
                    continue
                token = str(row.get("token_address") or "").strip()
                qty = max(0.0, float(row.get("qty") or 0.0))
                ts = int(row.get("ts") or 0)
                if not token or qty <= 0.0 or ts <= 0:
                    continue
                reason_family = self._trade_reason_family(reason)
                for idx2, tr2 in ordered_for_dedupe[pos + 1 :]:
                    row2 = dict(tr2 or {})
                    if not self._is_live_trade_row(row2):
                        continue
                    if str(row2.get("source") or "").strip().lower() != "memecoin":
                        continue
                    if str(row2.get("side") or "").strip().lower() != "sell":
                        continue
                    reason2 = str(row2.get("reason") or "")
                    sig2 = self._extract_live_tx_signature(reason2)
                    if not sig2:
                        continue
                    if str(row2.get("token_address") or "").strip() != token:
                        continue
                    ts2 = int(row2.get("ts") or 0)
                    if ts2 < ts:
                        continue
                    if (ts2 - ts) > 172800:
                        break
                    qty2 = max(0.0, float(row2.get("qty") or 0.0))
                    if abs(qty2 - qty) > max(1e-6, qty * 0.02):
                        continue
                    reason2_family = self._trade_reason_family(reason2)
                    if reason_family and reason2_family and reason_family != reason2_family:
                        continue
                    stale_sell_indices.add(int(idx))
                    break
            basis_by_token: dict[str, dict[str, float]] = {}
            row_changed = False
            ordered = sorted(enumerate(trades), key=lambda item: int(((item[1] or {}).get("ts") or 0)))
            for idx, tr in ordered:
                row = dict(tr or {})
                if not self._is_live_trade_row(row):
                    continue
                if str(row.get("source") or "").strip().lower() != "memecoin":
                    continue
                side = str(row.get("side") or "").strip().lower()
                token = str(row.get("token_address") or "").strip()
                if not token:
                    continue
                signature = self._extract_live_tx_signature(str(row.get("reason") or ""))
                if side == "buy":
                    actual = {}
                    if signature:
                        actual = self._live_swap_accounting_from_signature(
                            token_address=token,
                            swap_signature=signature,
                            now_ts=int(row.get("ts") or now_ts),
                            side="buy",
                            fallback_sol_price_usd=float(row.get("price_usd") or 0.0),
                        )
                    qty = max(
                        0.0,
                        float(actual.get("received_qty") or row.get("qty") or 0.0),
                    )
                    notional = max(
                        0.0,
                        float(actual.get("spent_usd") or row.get("notional_usd") or 0.0),
                    )
                    avg_price = float(actual.get("avg_price_usd") or (notional / max(qty, 1e-12) if qty > 0.0 else 0.0))
                    fee_usd = float(actual.get("fee_usd") or row.get("network_fee_usd") or 0.0)
                    updated = dict(row)
                    updated["qty"] = float(qty)
                    updated["price_usd"] = float(avg_price)
                    updated["notional_usd"] = float(notional)
                    updated["pnl_usd"] = None
                    updated["pnl_pct"] = None
                    updated["realized_pnl_usd"] = None
                    updated["realized_pnl_pct"] = None
                    updated["network_fee_usd"] = float(fee_usd)
                    updated["realized"] = False
                    updated["accounting_version"] = int(LIVE_ACCOUNTING_SCHEMA_VERSION)
                    if actual:
                        updated["before_sol_lamports"] = int(actual.get("before_sol_lamports") or 0)
                        updated["after_sol_lamports"] = int(actual.get("after_sol_lamports") or 0)
                        updated["before_token_raw"] = int(actual.get("before_token_raw") or 0)
                        updated["after_token_raw"] = int(actual.get("after_token_raw") or 0)
                    if updated != row:
                        trades[idx] = updated
                        row = updated
                        row_changed = True
                    basis = basis_by_token.setdefault(token, {"qty": 0.0, "cost_usd": 0.0})
                    basis["qty"] = float(basis.get("qty") or 0.0) + float(qty)
                    basis["cost_usd"] = float(basis.get("cost_usd") or 0.0) + float(notional)
                    continue
                if side != "sell":
                    continue
                basis = basis_by_token.setdefault(token, {"qty": 0.0, "cost_usd": 0.0})
                cur_qty = max(0.0, float(basis.get("qty") or 0.0))
                cur_cost = max(0.0, float(basis.get("cost_usd") or 0.0))
                reason_l = str(row.get("reason") or "").lower()
                if int(idx) in stale_sell_indices:
                    updated = dict(row)
                    updated["pnl_usd"] = 0.0
                    updated["pnl_pct"] = 0.0
                    updated["realized_pnl_usd"] = None
                    updated["realized_pnl_pct"] = None
                    updated["realized"] = False
                    updated["accounting_excluded"] = True
                    updated["accounting_note"] = "stale_estimated_sell_replaced_by_live_tx"
                    updated["accounting_version"] = int(LIVE_ACCOUNTING_SCHEMA_VERSION)
                    if updated != row:
                        trades[idx] = updated
                        row_changed = True
                    continue
                if "wallet_miss_cleanup" in reason_l:
                    close_qty = min(cur_qty, max(0.0, float(row.get("qty") or 0.0)))
                    avg_cost = cur_cost / max(cur_qty, 1e-12) if cur_qty > 0.0 else 0.0
                    basis["qty"] = max(0.0, cur_qty - close_qty)
                    basis["cost_usd"] = max(0.0, cur_cost - (avg_cost * close_qty))
                    updated = dict(row)
                    updated["pnl_usd"] = 0.0
                    updated["pnl_pct"] = 0.0
                    updated["realized_pnl_usd"] = None
                    updated["realized_pnl_pct"] = None
                    updated["realized"] = False
                    updated["accounting_excluded"] = True
                    updated["accounting_version"] = int(LIVE_ACCOUNTING_SCHEMA_VERSION)
                    if updated != row:
                        trades[idx] = updated
                        row_changed = True
                    continue
                actual = {}
                if signature:
                    actual = self._live_swap_accounting_from_signature(
                        token_address=token,
                        swap_signature=signature,
                        now_ts=int(row.get("ts") or now_ts),
                        side="sell",
                        fallback_sol_price_usd=float(row.get("price_usd") or 0.0),
                    )
                sold_qty = max(0.0, float(actual.get("sold_qty") or row.get("qty") or 0.0))
                proceeds_usd = max(0.0, float(actual.get("proceeds_usd") or row.get("notional_usd") or 0.0))
                avg_exit_price = float(actual.get("avg_exit_price_usd") or (proceeds_usd / max(sold_qty, 1e-12) if sold_qty > 0.0 else 0.0))
                close_qty = min(cur_qty, sold_qty if sold_qty > 0.0 else cur_qty)
                avg_cost = cur_cost / max(cur_qty, 1e-12) if cur_qty > 0.0 else 0.0
                cost_basis = avg_cost * close_qty
                realized_pnl = proceeds_usd - cost_basis if close_qty > 0.0 else 0.0
                realized_pct = (realized_pnl / max(cost_basis, 1e-12)) if cost_basis > 0.0 else 0.0
                basis["qty"] = max(0.0, cur_qty - close_qty)
                basis["cost_usd"] = max(0.0, cur_cost - cost_basis)
                updated = dict(row)
                updated["qty"] = float(sold_qty if sold_qty > 0.0 else close_qty)
                updated["price_usd"] = float(avg_exit_price)
                updated["notional_usd"] = float(proceeds_usd)
                updated["pnl_usd"] = float(realized_pnl)
                updated["pnl_pct"] = float(realized_pct)
                updated["realized_pnl_usd"] = float(realized_pnl)
                updated["realized_pnl_pct"] = float(realized_pct)
                updated["network_fee_usd"] = float(actual.get("fee_usd") or row.get("network_fee_usd") or 0.0)
                updated["realized"] = True
                updated["accounting_excluded"] = False
                updated["accounting_version"] = int(LIVE_ACCOUNTING_SCHEMA_VERSION)
                if actual:
                    updated["before_sol_lamports"] = int(actual.get("before_sol_lamports") or 0)
                    updated["after_sol_lamports"] = int(actual.get("after_sol_lamports") or 0)
                    updated["before_token_raw"] = int(actual.get("before_token_raw") or 0)
                    updated["after_token_raw"] = int(actual.get("after_token_raw") or 0)
                if updated != row:
                    trades[idx] = updated
                    row_changed = True
            if row_changed:
                with self._lock:
                    runs = self.state.model_runs if isinstance(self.state.model_runs, dict) else {}
                    run = self._get_market_run(runs, "meme", model_id)
                    run["trades"] = trades
                changed = True
        with self._lock:
            runs = self.state.model_runs if isinstance(self.state.model_runs, dict) else {}
            accounting_state = dict(runs.get("_live_accounting_state") or {})
            accounting_state["schema_version"] = int(LIVE_ACCOUNTING_SCHEMA_VERSION)
            accounting_state["last_reconcile_ts"] = int(now_ts)
            runs["_live_accounting_state"] = accounting_state
            self.state.model_runs = runs
        return changed

    def _persist(self, force: bool = False) -> None:
        now = int(time.time())
        with self._lock:
            if not force and (now - int(self._last_persist_ts)) < STATE_PERSIST_MIN_INTERVAL_SECONDS:
                return
            save_state(self.settings.state_file, self.state)
            self._backup_state_file("auto", force=False)
            save_online_model(self.settings.model_file, self.model)
            self._persist_supabase_state()
            self._last_persist_ts = int(now)

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

    @staticmethod
    def _telegram_lock_path_for_token(bot_token: str) -> Path:
        token = str(bot_token or "").strip()
        digest = hashlib.sha1(token.encode("utf-8")).hexdigest()[:16] if token else "default"
        return Path(tempfile.gettempdir()) / "ai_auto" / f"telegram_poll_{digest}.lock"

    def _acquire_telegram_poll_lock(self, now_ts: int) -> bool:
        path = self._telegram_poll_lock_path
        path.parent.mkdir(parents=True, exist_ok=True)
        me = int(os.getpid())
        me_tid = int(threading.get_ident())
        payload = {"pid": me, "tid": me_tid, "ts": int(now_ts)}
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
        holder_tid = 0
        holder_ts = 0
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            holder_pid = int(raw.get("pid") or 0)
            holder_tid = int(raw.get("tid") or 0)
            holder_ts = int(raw.get("ts") or 0)
        except Exception:
            holder_pid = 0
            holder_tid = 0
            holder_ts = 0

        is_stale = (int(now_ts) - int(holder_ts)) > TELEGRAM_POLL_LOCK_STALE_SECONDS
        if holder_pid == me and holder_tid == me_tid:
            try:
                path.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
                return True
            except Exception:
                return False

        # Same process but different thread: prevent duplicate polling threads.
        if holder_pid == me and holder_tid != me_tid and not is_stale:
            return False

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

    def _release_telegram_poll_lock(self, force: bool = False) -> None:
        path = self._telegram_poll_lock_path
        me = int(os.getpid())
        me_tid = int(threading.get_ident())
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if int(raw.get("pid") or 0) != me:
                return
            if not force:
                holder_tid = int(raw.get("tid") or 0)
                if holder_tid > 0 and holder_tid != me_tid:
                    return
        except Exception:
            return
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass

    def _append_runtime_event_only(
        self,
        source: str,
        *,
        level: str = "info",
        status: str = "skip",
        error: str = "",
        detail: str = "",
        title: str = "",
    ) -> None:
        now = int(time.time())
        try:
            self.runtime_feedback.append_event(
                source=str(source or "runtime"),
                level=str(level or "info").lower(),
                status=str(status or "skip"),
                error=str(error or "").strip(),
                action="",
                detail=str(detail or "").strip(),
                meta={"title": str(title or "")},
                now_ts=now,
            )
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

    @staticmethod
    def _meme_strategy_spec(strategy_or_model_id: str) -> dict[str, Any]:
        raw = str(strategy_or_model_id or "").upper().strip()
        normalized = str(MEME_STRATEGY_ALIASES.get(raw) or raw)
        strategy_id = normalized if normalized in MEME_STRATEGY_SPECS else str(BRIDGE_MEME_MODEL_TO_STRATEGY_ID.get(raw) or "")
        if strategy_id in MEME_STRATEGY_SPECS:
            return dict(MEME_STRATEGY_SPECS.get(strategy_id) or {})
        return {}

    @classmethod
    def _meme_strategy_id_for_model(cls, model_id: str) -> str:
        raw = str(model_id or "").upper().strip()
        normalized = str(MEME_STRATEGY_ALIASES.get(raw) or raw)
        if normalized in MEME_STRATEGY_SPECS:
            return normalized
        strategy_id = str(BRIDGE_MEME_MODEL_TO_STRATEGY_ID.get(raw) or "")
        if strategy_id:
            return strategy_id
        return "THEME"

    @classmethod
    def _meme_strategy_name(cls, strategy_or_model_id: str) -> str:
        spec = cls._meme_strategy_spec(strategy_or_model_id)
        if spec:
            return str(spec.get("name") or strategy_or_model_id)
        return str(strategy_or_model_id or "")

    def _meme_strategy_id_from_signal_context(
        self,
        *,
        snap: TokenSnapshot | None = None,
        features: dict[str, Any] | None = None,
        reason: str = "",
        current_strategy_id: str = "",
    ) -> str:
        feats = dict(features or {})
        source = str(getattr(snap, "source", "") or "").strip().lower()
        reason_low = str(reason or "").strip().lower()
        current = str(current_strategy_id or "").strip().upper()
        if reason_low.startswith("theme|"):
            return "THEME"
        if reason_low.startswith("sniper|"):
            return "SNIPER"
        if reason_low.startswith("narrative|"):
            return "NARRATIVE"
        if "live_wallet_sync_seed" in reason_low:
            return "THEME"
        if current in MEME_STRATEGY_IDS and not feats and snap is None:
            return current

        trader_strength = float(feats.get("trader_strength") or 0.0)
        news_strength = float(feats.get("news_strength") or 0.0)
        community_strength = float(feats.get("community_strength") or 0.0)
        google_strength = float(feats.get("google_strength") or 0.0)
        trend_strength = float(feats.get("trend_strength") or 0.0)
        tx_flow = float(feats.get("tx_flow") or 0.0)
        buy_sell_ratio = float(feats.get("buy_sell_ratio") or 0.0)
        age_freshness = float(feats.get("age_freshness") or 0.0)
        age_stability = float(feats.get("age_stability") or 0.0)
        is_pump_fun = float(feats.get("is_pump_fun") or 0.0) > 0.0
        new_meme_instant = float(feats.get("new_meme_instant") or 0.0) > 0.0
        social_burst = float(feats.get("sniper_social_burst") or 0.0)
        sniper_signal_fit = float(feats.get("sniper_signal_fit") or 0.0)
        cap_usd = 0.0
        if snap is not None:
            cap_usd = float(self._meme_effective_cap_usd(snap))
        if cap_usd <= 0.0:
            cap_usd = float(feats.get("market_cap_usd") or 0.0)
        sniper_cap_ok = bool(MEME_SNIPER_MIN_CAP_USD <= cap_usd <= MEME_SNIPER_MAX_CAP_USD) if cap_usd > 0.0 else False

        social_trigger = bool(
            trader_strength >= 0.24
            or community_strength >= 0.24
            or news_strength >= 0.24
            or google_strength >= 0.28
            or (trader_strength + community_strength + news_strength + google_strength) >= 0.60
            or social_burst >= 0.55
            or any(tag in reason_low for tag in ("트레이더", "커뮤니티", "뉴스", "구글ai", "reddit", "4chan", "twitter", "x "))
        )
        fresh_new_coin = bool(
            new_meme_instant
            or (is_pump_fun and age_freshness >= 0.35)
            or source in {"pumpfun_raw", "pumpfun_dex"}
            or source.startswith("pumpfun")
            or "pumpfun" in reason_low
        )
        revival_flow = bool(
            age_stability >= 0.82
            and social_burst >= 0.62
            and social_trigger
            and not fresh_new_coin
            and (trader_strength >= 0.20 or community_strength >= 0.22 or news_strength >= 0.18)
        )
        sniper_trigger = bool(
            social_trigger
            and sniper_cap_ok
            and (
                social_burst >= 0.58
                or sniper_signal_fit >= 0.62
                or (
                    social_burst >= 0.50
                    and trend_strength >= 0.35
                    and (tx_flow >= 0.56 or buy_sell_ratio >= 0.56)
                )
            )
        )
        if revival_flow:
            return "NARRATIVE"
        if sniper_trigger:
            return "SNIPER"
        if fresh_new_coin:
            return "THEME"
        if social_trigger and sniper_cap_ok:
            return "SNIPER"
        if current in MEME_STRATEGY_IDS:
            return current
        return "THEME"

    @classmethod
    def _meme_strategy_registry(cls) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for strategy_id in MEME_STRATEGY_IDS:
            spec = dict(MEME_STRATEGY_SPECS.get(strategy_id) or {})
            rows.append(
                {
                    "id": strategy_id,
                    "name": str(spec.get("name") or strategy_id),
                    "description": str(spec.get("description") or ""),
                    "bridge_model_id": str(spec.get("bridge_model_id") or ""),
                    "execution_mode": str(spec.get("execution_mode") or ""),
                    "entry_sol_setting": str(spec.get("entry_sol") or ""),
                }
            )
        return rows

    def _openai_candidate_preview(self, model_views: dict[str, Any]) -> dict[str, Any]:
        seen: dict[str, dict[str, Any]] = {}
        min_score = float(self.settings.openai_candidate_min_score)
        for model_id in MEME_MODEL_IDS:
            for row in list((((model_views.get(model_id) or {}).get("meme") or {}).get("signals") or [])):
                item = dict(row or {})
                score = float(item.get("score") or 0.0)
                if score < min_score:
                    continue
                token_obj = item.get("token")
                token_address = str(
                    item.get("token_address")
                    or (getattr(token_obj, "token_address", "") if token_obj is not None else "")
                    or ""
                )
                symbol = str(item.get("symbol") or "-").upper()
                strategy_id = str(item.get("strategy_id") or "THEME").upper()
                key = f"{strategy_id}:{token_address or symbol}"
                prev = seen.get(key)
                if prev is None or score > float(prev.get("score") or 0.0):
                    feats = dict(item.get("features") or {})
                    seen[key] = {
                        "symbol": symbol,
                        "token_address": token_address,
                        "strategy_id": strategy_id,
                        "score": score,
                        "grade": str(item.get("grade") or "-"),
                        "probability": float(item.get("probability") or 0.0),
                        "market_cap_usd": float(item.get("market_cap_usd") or 0.0),
                        "liquidity_usd": float(item.get("liquidity_usd") or 0.0),
                        "volume_5m_usd": float(item.get("volume_5m_usd") or 0.0),
                        "buy_sell_ratio": float(item.get("buy_sell_ratio") or 0.0),
                        "sniper_social_burst": float(feats.get("sniper_social_burst") or 0.0),
                        "sniper_signal_fit": float(feats.get("sniper_signal_fit") or 0.0),
                        "theme_confirmation": float(feats.get("theme_confirmation") or 0.0),
                        "holder_overlap_risk": float(feats.get("holder_overlap_risk") or 0.0),
                        "reason": str(item.get("reason") or ""),
                        "score_low_reason": str(item.get("score_low_reason") or ""),
                    }
        ordered = sorted(seen.values(), key=lambda row: (float(row.get("score") or 0.0), float(row.get("probability") or 0.0)), reverse=True)
        candidate_rows = ordered[: int(self.settings.openai_candidate_top_n)]
        candidate_payload = self.openai_advisor.build_meme_candidate_payload(candidate_rows, "candidate_review")
        return {
            "candidate_ready": bool(candidate_rows),
            "candidate_count": int(len(candidate_rows)),
            "candidate_rows": candidate_rows,
            "candidate_payload": candidate_payload,
            "budget": self.openai_advisor.dashboard_payload(),
            "candidate_gate": {
                "min_score": float(self.settings.openai_candidate_min_score),
                "top_n": int(self.settings.openai_candidate_top_n),
                "interval_seconds": int(self.settings.openai_candidate_review_interval_seconds),
            },
        }

    @staticmethod
    def _configured_meme_strategy_ids(raw: Any, fallback_all: bool = True) -> tuple[str, ...]:
        text = str(raw or "").replace("|", ",").replace(" ", ",")
        out: list[str] = []
        for token in text.split(","):
            strategy_id = str(token or "").strip().upper()
            strategy_id = str(MEME_STRATEGY_ALIASES.get(strategy_id) or strategy_id)
            if strategy_id in MEME_STRATEGY_IDS and strategy_id not in out:
                out.append(strategy_id)
        if out:
            return tuple(out)
        return tuple(MEME_STRATEGY_IDS) if bool(fallback_all) else tuple()

    def _display_model_name(self, model_id: str, market: str | None = None) -> str:
        market_id = str(market or "").lower().strip()
        if market_id in {"meme", "crypto"}:
            return self._market_model_name(market_id, model_id)
        return str(MODEL_SPECS.get(model_id, {}).get("name") or model_id)

    @staticmethod
    def _market_model_ids(market: str) -> tuple[str, ...]:
        return tuple(CRYPTO_MODEL_IDS if str(market).lower() == "crypto" else MEME_MODEL_IDS)

    @staticmethod
    def _all_model_ids() -> tuple[str, ...]:
        return tuple(ALL_MODEL_IDS)

    def _configured_crypto_symbols(self) -> tuple[str, ...]:
        configured: list[str] = []
        for raw in str(getattr(self.settings, "bybit_symbols", "") or "").replace("|", ",").replace(" ", ",").split(","):
            symbol = str(raw or "").upper().strip()
            if not symbol:
                continue
            if not symbol.endswith("USDT"):
                symbol = f"{symbol}USDT"
            if symbol not in configured:
                configured.append(symbol)
        return tuple(configured)

    def _meme_market_enabled(self) -> bool:
        return bool(getattr(self.settings, "enable_meme_market", True))

    @staticmethod
    def _iso_datetime(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            ts = int(value)
            if ts <= 0:
                return None
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        text = str(value or "").strip()
        return text or None

    @staticmethod
    def _supabase_position_id(model_id: str, symbol: str, opened_at: Any) -> str:
        raw = f"crypto:{str(model_id or '').upper()}:{str(symbol or '').upper()}:{int(opened_at or 0)}"
        return str(uuid.uuid5(uuid.NAMESPACE_URL, raw))

    def _build_supabase_heartbeat_row(self, now_ts: int) -> dict[str, Any]:
        return {
            "engine_name": "ai_auto_core",
            "market": "crypto",
            "last_seen_at": self._iso_datetime(now_ts),
            "last_cycle_started_at": self._iso_datetime(now_ts),
            "last_cycle_finished_at": self._iso_datetime(now_ts),
            "last_error": "",
            "version_sha": "",
            "host_name": os.environ.get("HOSTNAME", "") or os.environ.get("COMPUTERNAME", ""),
            "meta_json": {
                "trade_mode": str(self.settings.trade_mode or ""),
                "demo_enable_macro": bool(self.settings.demo_enable_macro),
                "configured_symbols": list(self._configured_crypto_symbols()),
            },
        }

    def _build_supabase_runtime_tune_rows(self, now_ts: int) -> list[dict[str, Any]]:
        with self._lock:
            runs = dict(self.state.model_runs or {})
        rows: list[dict[str, Any]] = []
        for model_id in CRYPTO_MODEL_IDS:
            run = self._get_market_run(runs, "crypto", model_id)
            tune = self._read_model_runtime_tune_from_run(run, model_id, now_ts)
            rows.append(
                {
                    "model_id": model_id,
                    "market": "crypto",
                    "active_variant_id": str(tune.get("active_variant_id") or run.get("active_variant_id") or ""),
                    "threshold": float(tune.get("threshold") or 0.0),
                    "tp_mul": float(tune.get("tp_mul") or 0.0),
                    "sl_mul": float(tune.get("sl_mul") or 0.0),
                    "next_eval_at": self._iso_datetime(tune.get("next_eval_ts")),
                    "last_eval_at": self._iso_datetime(tune.get("last_eval_ts")),
                    "last_eval_note_code": str(tune.get("last_eval_note") or ""),
                    "last_eval_note_ko": str(tune.get("last_eval_note_ko") or ""),
                    "last_eval_closed": int(tune.get("last_eval_closed") or 0),
                    "last_eval_win_rate": float(tune.get("last_eval_win_rate") or 0.0),
                    "last_eval_pnl_usd": float(tune.get("last_eval_pnl_usd") or 0.0),
                    "last_eval_pf": float(tune.get("last_eval_pf") or 0.0),
                    "source_json": {
                        "variant_seq": int(tune.get("variant_seq") or 0),
                        "next_eval_ts": int(tune.get("next_eval_ts") or 0),
                    },
                }
            )
        return rows

    def _build_supabase_daily_pnl_rows(self) -> list[dict[str, Any]]:
        with self._lock:
            table = list(self.state.daily_pnl or [])
        rows: list[dict[str, Any]] = []
        for row in table[-180:]:
            model_id = str((row or {}).get("model_id") or "").upper()
            day_key = str((row or {}).get("date") or "").strip()
            if model_id not in CRYPTO_MODEL_IDS or not day_key:
                continue
            rows.append(
                {
                    "day": day_key,
                    "market": "crypto",
                    "model_id": model_id,
                    "equity_usd": float((row or {}).get("bybit_equity_usd") or 0.0),
                    "total_pnl_usd": float((row or {}).get("bybit_total_pnl_usd") or 0.0),
                    "realized_pnl_usd": float((row or {}).get("bybit_realized_pnl_usd") or 0.0),
                    "unrealized_pnl_usd": float((row or {}).get("bybit_unrealized_pnl_usd") or 0.0),
                    "win_rate": float((row or {}).get("bybit_win_rate") or 0.0),
                    "closed_trades": int((row or {}).get("bybit_closed_trades") or 0),
                    "source_json": {
                        "total_equity_usd": float((row or {}).get("total_equity_usd") or 0.0),
                        "total_pnl_usd_all": float((row or {}).get("total_pnl_usd") or 0.0),
                    },
                }
            )
        return rows

    def _build_supabase_setup_rows(self) -> list[dict[str, Any]]:
        with self._lock:
            runs = dict(self.state.model_runs or {})
        rows: list[dict[str, Any]] = []
        for model_id in CRYPTO_MODEL_IDS:
            run = self._get_market_run(runs, "crypto", model_id)
            for signal in list(run.get("latest_crypto_signals") or [])[:80]:
                symbol = str((signal or {}).get("symbol") or "").upper().strip()
                if not symbol:
                    continue
                rows.append(
                    {
                        "cycle_at": self._iso_datetime((signal or {}).get("scored_at_ts")),
                        "market": "crypto",
                        "symbol": symbol,
                        "model_id": model_id,
                        "timeframe": "10m",
                        "side": "long",
                        "score": float((signal or {}).get("score") or 0.0),
                        "threshold": float((signal or {}).get("entry_threshold") or 0.0),
                        "confidence": float((signal or {}).get("score") or 0.0),
                        "entry_price": float((signal or {}).get("entry_price") or 0.0),
                        "entry_zone_low": float((signal or {}).get("entry_zone_low") or 0.0),
                        "entry_zone_high": float((signal or {}).get("entry_zone_high") or 0.0),
                        "stop_loss_price": float((signal or {}).get("stop_loss_price") or 0.0),
                        "take_profit_price": float((signal or {}).get("take_profit_price") or 0.0),
                        "target_price_1": float((signal or {}).get("target_price_1") or 0.0),
                        "target_price_2": float((signal or {}).get("target_price_2") or 0.0),
                        "target_price_3": float((signal or {}).get("target_price_3") or 0.0),
                        "risk_reward": float((signal or {}).get("risk_reward") or 0.0),
                        "recommended_leverage": float((signal or {}).get("leverage") or 0.0),
                        "entry_ready": bool((signal or {}).get("entry_ready")),
                        "setup_state": str((signal or {}).get("setup_state") or "planned"),
                        "expires_at": self._iso_datetime((signal or {}).get("setup_expiry_ts")),
                        "reason_text": str((signal or {}).get("reason") or ""),
                        "indicators_json": dict((signal or {}).get("indicator_snapshot") or {}),
                    }
                )
        return rows

    def _build_supabase_open_position_rows(self) -> list[dict[str, Any]]:
        with self._lock:
            runs = dict(self.state.model_runs or {})
        rows: list[dict[str, Any]] = []
        for model_id in CRYPTO_MODEL_IDS:
            run = self._get_market_run(runs, "crypto", model_id)
            for pos in list((run.get("bybit_positions") or {}).values()):
                symbol = str((pos or {}).get("symbol") or "").upper().strip()
                if not symbol:
                    continue
                current = float(self._crypto_current_price(pos))
                marked = self._mark_crypto_position(pos, current)
                opened_at = int((pos or {}).get("opened_at") or 0)
                rows.append(
                    {
                        "id": self._supabase_position_id(model_id, symbol, opened_at),
                        "market": "crypto",
                        "symbol": symbol,
                        "model_id": model_id,
                        "side": str((pos or {}).get("side") or "long"),
                        "status": "open",
                        "opened_at": self._iso_datetime(opened_at),
                        "planned_entry_price": float((pos or {}).get("entry_plan_price") or 0.0),
                        "actual_entry_price": float((pos or {}).get("avg_price_usd") or 0.0),
                        "stop_loss_price": float((pos or {}).get("stop_loss_price") or 0.0),
                        "take_profit_price": float((pos or {}).get("take_profit_price") or 0.0),
                        "target_price_1": float((pos or {}).get("target_price_1") or 0.0),
                        "target_price_2": float((pos or {}).get("target_price_2") or 0.0),
                        "target_price_3": float((pos or {}).get("target_price_3") or 0.0),
                        "qty": float(marked.get("qty") or 0.0),
                        "notional_usd": float((pos or {}).get("notional_usd") or marked.get("exposure_usd") or 0.0),
                        "leverage": float(marked.get("leverage") or 0.0),
                        "fees_usd": 0.0,
                        "funding_usd": 0.0,
                        "realized_pnl_usd": 0.0,
                        "unrealized_pnl_usd": float(marked.get("pnl_usd") or 0.0),
                        "max_drawdown_usd": 0.0,
                        "position_meta": {
                            "entry_score": float((pos or {}).get("entry_score") or 0.0),
                            "risk_reward": float((pos or {}).get("risk_reward") or 0.0),
                            "fill_mode": str((pos or {}).get("fill_mode") or "spot"),
                            "reason": str((pos or {}).get("reason") or ""),
                            "setup_state": str((pos or {}).get("setup_state") or ""),
                        },
                    }
                )
        return rows

    def _build_recent_crypto_trade_rows(self, limit: int = 80) -> list[dict[str, Any]]:
        with self._lock:
            runs = dict(self.state.model_runs or {})
        rows: list[dict[str, Any]] = []
        for model_id in CRYPTO_MODEL_IDS:
            run = self._get_market_run(runs, "crypto", model_id)
            for tr in list(run.get("trades") or []):
                if str((tr or {}).get("source") or "").strip().lower() != "crypto_demo":
                    continue
                side = str((tr or {}).get("side") or "").strip().lower()
                fill_mode = str((tr or {}).get("fill_mode") or "spot").strip().lower()
                close_mode = str((tr or {}).get("close_mode") or "").strip().lower()
                ts = int((tr or {}).get("ts") or 0)
                rows.append(
                    {
                        "ts": self._iso_datetime(ts),
                        "ts_epoch": ts,
                        "model_id": model_id,
                        "model_name": self._market_model_name("crypto", model_id),
                        "side": side,
                        "symbol": str((tr or {}).get("symbol") or ""),
                        "price_usd": float((tr or {}).get("price_usd") or 0.0),
                        "notional_usd": float((tr or {}).get("notional_usd") or 0.0),
                        "pnl_usd": float((tr or {}).get("pnl_usd") or 0.0),
                        "pnl_pct": float((tr or {}).get("pnl_pct") or 0.0),
                        "reason": str((tr or {}).get("reason") or ""),
                        "fill_mode": fill_mode if side == "buy" else "",
                        "close_mode": close_mode if side == "sell" else "",
                        "event_mode": fill_mode if side == "buy" else close_mode,
                        "event_label": (
                            "intrabar 체결"
                            if side == "buy" and fill_mode == "intrabar"
                            else (
                                "spot 체결"
                                if side == "buy"
                                else ("intrabar 종료" if close_mode == "intrabar" else "spot 종료")
                            )
                        ),
                        "is_intrabar": bool((fill_mode if side == "buy" else close_mode) == "intrabar"),
                    }
                )
        rows.sort(key=lambda row: int(row.get("ts_epoch") or 0), reverse=True)
        return rows[: max(1, int(limit))]

    def _sync_supabase_snapshot(self, now_ts: int) -> None:
        if not bool(getattr(getattr(self, "supabase_sync", None), "enabled", False)):
            return
        results = {
            "engine_heartbeat": self.supabase_sync.upsert_rows(
                "engine_heartbeat",
                [self._build_supabase_heartbeat_row(now_ts)],
                on_conflict="engine_name",
            ),
            "model_runtime_tunes": self.supabase_sync.upsert_rows(
                "model_runtime_tunes",
                self._build_supabase_runtime_tune_rows(now_ts),
                on_conflict="model_id",
            ),
            "daily_model_pnl": self.supabase_sync.upsert_rows(
                "daily_model_pnl",
                self._build_supabase_daily_pnl_rows(),
                on_conflict="day,market,model_id",
            ),
            "model_setups": self.supabase_sync.upsert_rows(
                "model_setups",
                self._build_supabase_setup_rows(),
                on_conflict="cycle_at,symbol,model_id",
            ),
            "positions": self.supabase_sync.replace_open_positions(self._build_supabase_open_position_rows()),
            "recent_crypto_trades": self.supabase_sync.upsert_blob(
                "recent_crypto_trades",
                {"rows": self._build_recent_crypto_trade_rows(limit=120), "updated_at": self._iso_datetime(now_ts)},
            ),
        }
        errors = {key: value for key, value in results.items() if not bool((value or {}).get("ok"))}
        if errors:
            self.runtime_feedback.append_event(
                source="supabase_sync",
                level="warn",
                status="partial_failure",
                detail="Supabase snapshot sync failed",
                meta=errors,
                now_ts=now_ts,
            )

    @staticmethod
    def _parse_model_id_csv(raw: Any, fallback_all: bool = True, allowed_ids: tuple[str, ...] | None = None) -> tuple[str, ...]:
        valid_ids = tuple(allowed_ids or ALL_MODEL_IDS)
        text = str(raw or "").replace("|", ",").replace(" ", ",")
        out: list[str] = []
        for token in text.split(","):
            model_id = str(token or "").strip().upper()
            if model_id in valid_ids and model_id not in out:
                out.append(model_id)
        if out:
            return tuple(out)
        return tuple(valid_ids) if bool(fallback_all) else tuple()

    def _autotrade_model_ids(self, market: str) -> tuple[str, ...]:
        market_id = "meme" if str(market).lower() == "meme" else "crypto"
        raw = self.settings.meme_autotrade_models if market_id == "meme" else self.settings.crypto_autotrade_models
        return self._parse_model_id_csv(raw, fallback_all=True, allowed_ids=self._market_model_ids(market_id))

    def _is_autotrade_model_enabled(self, market: str, model_id: str) -> bool:
        return str(model_id).upper() in set(self._autotrade_model_ids(market))

    def _live_model_ids(self, market: str) -> tuple[str, ...]:
        market_id = "meme" if str(market).lower() == "meme" else "crypto"
        raw = self.settings.live_meme_models if market_id == "meme" else self.settings.live_crypto_models
        parsed = self._parse_model_id_csv(raw, fallback_all=False, allowed_ids=self._market_model_ids(market_id))
        if parsed:
            return parsed
        return self._autotrade_model_ids(market_id)

    def _is_live_model_enabled(self, market: str, model_id: str) -> bool:
        return str(model_id).upper() in set(self._live_model_ids(market))

    def _is_live_market_enabled(self, market: str) -> bool:
        market_id = "meme" if str(market).lower() == "meme" else "crypto"
        if market_id == "meme":
            return bool(self.settings.live_enable_meme)
        return bool(self.settings.live_enable_crypto)

    def _is_live_execution_market(self, market: str, model_id: str | None = None) -> bool:
        if not bool(self.settings.enable_live_execution):
            return False
        if not self._is_live_market_enabled(market):
            return False
        if model_id is None:
            return True
        return self._is_live_model_enabled(market, model_id)

    def _is_market_autotrade_enabled(self, market: str) -> bool:
        # Keep demo strategy evaluation running for both markets.
        # Live market toggles control only live-execution gating.
        return bool(self.settings.enable_autotrade)

    @staticmethod
    def _meme_strategy_mode_for_model(model_id: str) -> str:
        spec = TradingEngine._meme_strategy_spec(model_id)
        if spec:
            return str(spec.get("execution_mode") or "theme_basket")
        return "theme_basket"

    def _migrate_market_model_profile(self, run: dict[str, Any], model_id: str, now_ts: int) -> None:
        version = int(run.get("market_profile_ver") or 0)
        meme_positions = dict(run.get("meme_positions") or {})
        if version < 1:
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
            version = 1
        if version < 2 and model_id == "B":
            for token, pos in meme_positions.items():
                row = dict(pos or {})
                if str(row.get("strategy") or "").lower() == "swing":
                    row["sl_pct"] = float(_clamp(max(float(row.get("sl_pct") or 0.0), 0.30), 0.20, 0.65))
                    row["catastrophic_sl_pct"] = float(
                        row.get("catastrophic_sl_pct") or _clamp(max(float(row.get("sl_pct") or 0.0), 0.30), 0.20, 0.75)
                    )
                    row["trailing_stop_pct"] = float(max(0.46, float(row.get("trailing_stop_pct") or 0.0)))
                    row["entry_wallet_score"] = float(row.get("entry_wallet_score") or 0.0)
                    row["entry_holder_risk"] = float(row.get("entry_holder_risk") or 0.0)
                    row["hold_ext_count"] = int(row.get("hold_ext_count") or 0)
                    row["last_wallet_check_ts"] = int(row.get("last_wallet_check_ts") or now_ts)
                meme_positions[token] = row
            version = 2
        if version < 3 and model_id == "C":
            for token, pos in meme_positions.items():
                row = dict(pos or {})
                row["strategy"] = "scalp"
                row["hold_until_ts"] = 0
                row["trailing_stop_pct"] = 0.0
                row["sl_pct"] = float(MEME_C_FIXED_SL_PCT)
                row["catastrophic_sl_pct"] = 0.0
                meme_positions[token] = row
            version = 3
        if version < 4:
            for token, pos in meme_positions.items():
                row = dict(pos or {})
                strategy = str(row.get("strategy") or "").lower().strip()
                entry_score = max(0.50, float(row.get("entry_score") or 0.0))
                if strategy == "swing":
                    row["tp_pct"] = float(_clamp(float(row.get("tp_pct") or self._meme_score_target_tp_pct(entry_score)), 0.10, 0.20))
                else:
                    row["tp_pct"] = float(self._meme_score_target_tp_pct(entry_score))
                meme_positions[token] = row
            version = 4
        run["meme_positions"] = meme_positions
        run["market_profile_ver"] = max(int(version), 4)

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
        if market_id == "meme":
            run["strategy_id"] = self._meme_strategy_id_for_model(model_id)
            run["strategy_name"] = self._meme_strategy_name(model_id)
            run["strategy_engine"] = str(self.settings.meme_strategy_engine or "unified_strategy_bridge")
        else:
            run["strategy_id"] = ""
            run["strategy_name"] = ""
            run["strategy_engine"] = ""
        run.setdefault("trades", [])
        run.setdefault("market_profile_ver", 4)
        run.setdefault("started_at", int(time.time()))
        run.setdefault("last_entry_alloc", {})
        run.setdefault("variant_seq", 0)
        run.setdefault("active_variant_id", f"{model_id}-BASE")
        run.setdefault("variant_history", [])

        if market_id == "meme":
            run.setdefault("meme_seed_usd", seed)
            run.setdefault("meme_cash_usd", seed)
            run.setdefault("meme_positions", {})
            run.setdefault("latest_signals", [])
            run.setdefault("last_signal_ts", {})
            run.setdefault("meme_reentry_after_ts", {})
            run["crypto_reentry_cooldowns"] = {}
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
            run.setdefault("crypto_reentry_cooldowns", {})
            run["meme_seed_usd"] = 0.0
            run["meme_cash_usd"] = 0.0
            run["meme_positions"] = {}
            run["latest_signals"] = []
            run["last_signal_ts"] = {}
            run["meme_reentry_after_ts"] = {}
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
        row["strategy_id"] = self._meme_strategy_id_for_model(model_id)
        row["strategy_name"] = self._meme_strategy_name(model_id)
        row["strategy_engine"] = str(self.settings.meme_strategy_engine or "unified_strategy_bridge")
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
            return {"threshold": (0.066, 0.104), "tp_mul": (0.86, 1.20), "sl_mul": (0.82, 1.08)}
        if model_id == "B":
            return {"threshold": (0.068, 0.108), "tp_mul": (0.94, 1.28), "sl_mul": (0.80, 1.04)}
        if model_id == "C":
            return {"threshold": (0.072, 0.112), "tp_mul": (1.04, 1.42), "sl_mul": (0.76, 1.00)}
        if model_id == "D":
            return {"threshold": (0.064, 0.102), "tp_mul": (0.84, 1.16), "sl_mul": (0.84, 1.10)}
        return {"threshold": (0.058, 0.108), "tp_mul": (1.08, 1.52), "sl_mul": (0.80, 1.04)}

    def _autotune_interval_seconds(self) -> int:
        raw_hours = int(getattr(self.settings, "model_autotune_interval_hours", 24) or 24)
        if raw_hours not in {6, 12, 24, 168}:
            raw_hours = 168
        hours = int(raw_hours)
        return int(hours * 3600)

    def _autotune_interval_label(self) -> str:
        raw_hours = int(getattr(self.settings, "model_autotune_interval_hours", 24) or 24)
        if raw_hours not in {6, 12, 24, 168}:
            raw_hours = 168
        hours = int(raw_hours)
        return f"{hours}시간"

    @staticmethod
    def _autotune_note_ko(note_code: str) -> str:
        code = str(note_code or "").strip()
        if not code:
            return "-"
        if code in AUTOTUNE_NOTE_KO:
            return AUTOTUNE_NOTE_KO[code]
        compact = code.replace("_", " ").strip()
        return compact if compact else code

    @staticmethod
    def _autotune_should_tune(model_id: str, pnl: float, win_rate: float, pf: float) -> bool:
        if model_id == "A":
            return bool(pnl < -6.0 or win_rate < 48.0 or pf < 0.96)
        if model_id == "B":
            return bool(pnl < -7.0 or win_rate < 46.0 or pf < 0.94)
        if model_id == "C":
            return bool(pnl < -8.0 or win_rate < 44.0 or pf < 0.92)
        return bool(pnl < -6.0 or win_rate < 45.0 or pf < 0.95)

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
        # If B-crypto has no closed samples yet, keep default threshold without lowering further.
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
        if model_id == "B":
            threshold = max(float(threshold), 0.070)
            tp_mul = min(float(tp_mul), 1.28)
            sl_mul = min(float(sl_mul), 1.02)
        if model_id == "C":
            threshold = _clamp(float(threshold), 0.072, 0.110)
            tp_mul = _clamp(float(tp_mul), 1.08, 1.42)
            sl_mul = _clamp(float(sl_mul), 0.80, 1.00)
        last_eval_ts = int(raw.get("last_eval_ts") or 0)
        next_eval_ts = int(raw.get("next_eval_ts") or 0)
        interval_seconds = max(3600, int(self._autotune_interval_seconds() or DEFAULT_MODEL_AUTOTUNE_INTERVAL_SECONDS))
        min_next_eval = int((last_eval_ts if last_eval_ts > 0 else started) + interval_seconds)
        if next_eval_ts <= 0:
            next_eval_ts = int(min_next_eval)
        elif next_eval_ts < min_next_eval:
            next_eval_ts = int(min_next_eval)
        payload = {
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
            "last_eval_note_ko": self._autotune_note_ko(str(raw.get("last_eval_note") or "")),
            "active_variant_id": str(run.get("active_variant_id") or f"{model_id}-BASE"),
            "variant_seq": int(run.get("variant_seq") or 0),
        }
        return payload

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
            for model_id in self._all_model_ids():
                legacy = runs.get(model_id)
                if isinstance(legacy, dict):
                    if not isinstance(runs.get(f"legacy_{model_id}"), dict):
                        runs[f"legacy_{model_id}"] = deepcopy(legacy)
                    # Enforce 6-run split mode: remove legacy combined active key.
                    runs.pop(model_id, None)
                else:
                    legacy = runs.get(f"legacy_{model_id}")
                for market in ("meme", "crypto"):
                    if model_id not in set(self._market_model_ids(market)):
                        continue
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
        now_ts = int(time.time())
        block_until = int(getattr(self.settings, "demo_reset_block_until_ts", 0) or 0)
        if block_until > now_ts:
            until_local = datetime.fromtimestamp(block_until, tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
            raise PermissionError(f"데모 초기화 금지 기간입니다. 해제 시각={until_local}")
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
            for mid in MEME_MODEL_IDS:
                next_runs[self._market_run_key("meme", mid)] = self._blank_market_run("meme", mid, seed)
            for mid in CRYPTO_MODEL_IDS:
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
        # Hard guard: block further reset for 30 days unless user explicitly updates runtime file.
        save_runtime_overrides(
            self.settings,
            {
                "DEMO_SEED_USDT": seed,
                "DEMO_RESET_BLOCK_UNTIL_TS": int(now_ts + (30 * 24 * 60 * 60)),
            },
        )
        self._reload_settings()
        self._sync_primary_views_from_model_a()
        self._persist(force=True)
        suffix = (
            f"+ Macro Top {int(self.settings.macro_top_n)} ({self.settings.macro_universe_source})"
            if self.settings.demo_enable_macro
            else "(Macro 데모 OFF)"
        )
        self._push_alert(
            "info",
            "데모 초기화",
            f"밈 엔진 브리지 3개 + 크립토 4개 모델 각각 시드 {int(seed)} {suffix} | backup={backup_path or '-'}",
            send_telegram=True,
        )
        return {"seed_usdt": seed, "meme_models": list(MEME_MODEL_IDS), "crypto_models": list(CRYPTO_MODEL_IDS), "backup_path": backup_path}

    def reset_crypto_demo(self, seed_usdt: float | None = None, confirm_text: str = "", actor: str = "manual") -> dict[str, Any]:
        if str(confirm_text or "").strip().upper() != "RESET CRYPTO":
            raise ValueError("초기화 확인 문구가 필요합니다: RESET CRYPTO")
        seed = max(50.0, float(seed_usdt if seed_usdt is not None else (self.state.demo_seed_usdt or self.settings.demo_seed_usdt)))
        backup_path = ""
        now_ts = int(time.time())
        with self._lock:
            # Keep a recoverable snapshot before wiping crypto model runs.
            save_state(self.settings.state_file, self.state)
            backup_path = self._backup_state_file(f"pre_crypto_reset_{actor}", force=True)
            runs = dict(self.state.model_runs or {})
            meme_seed = max(50.0, float(self.state.demo_seed_usdt or self.settings.demo_seed_usdt))
            for mid in CRYPTO_MODEL_IDS:
                crypto_key = self._market_run_key("crypto", mid)
                runs[crypto_key] = self._blank_market_run("crypto", mid, seed)
            for mid in MEME_MODEL_IDS:
                meme_key = self._market_run_key("meme", mid)
                if not isinstance(runs.get(meme_key), dict):
                    runs[meme_key] = self._blank_market_run("meme", mid, meme_seed)
            self.state.model_runs = runs
            self.state.bybit_error = ""
            self.state.last_bybit_sync_ts = now_ts
            # Keep meme history, but wipe crypto daily PNL history because broken feeds can pollute long-term charts.
            cleaned_daily: list[dict[str, Any]] = []
            for row in list(self.state.daily_pnl or []):
                item = dict(row or {})
                item["bybit_equity_usd"] = 0.0
                item["bybit_total_pnl_usd"] = 0.0
                item["bybit_realized_pnl_usd"] = 0.0
                item["bybit_unrealized_pnl_usd"] = 0.0
                item["bybit_win_rate"] = 0.0
                item["bybit_closed_trades"] = 0
                cleaned_daily.append(item)
            self.state.daily_pnl = cleaned_daily[-STATE_DAILY_PNL_HISTORY_LIMIT:]
        self._sync_primary_views_from_model_a()
        self._persist(force=True)
        self._push_alert(
            "info",
            "크립토 데모 초기화",
            f"크립토 4개 모델 시드 {int(seed)} 초기화 완료 | backup={backup_path or '-'}",
            send_telegram=True,
        )
        return {"seed_usdt": seed, "models": list(CRYPTO_MODEL_IDS), "backup_path": backup_path}

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._run_epoch += 1
            epoch = int(self._run_epoch)
            self._running = True
            self._last_telegram_poll = 0
            self._telegram_thread_start_ts = int(time.time())
            self._thread = threading.Thread(target=self._loop, args=(epoch,), name="trade-engine", daemon=True)
            self._telegram_thread = threading.Thread(
                target=self._telegram_loop,
                args=(epoch,),
                name="telegram-poll",
                daemon=True,
            )
            loop_thread = self._thread
            tg_thread = self._telegram_thread
        tg_thread.start()
        loop_thread.start()

    def stop(self) -> None:
        with self._lock:
            self._running = False
            self._run_epoch += 1
            loop_thread = self._thread
            tg_thread = self._telegram_thread
        try:
            # Interrupt long-poll socket quickly so restart does not overlap getUpdates calls.
            self.telegram.session.close()
        except Exception:
            pass
        if loop_thread and loop_thread.is_alive():
            loop_thread.join(timeout=3)
        if tg_thread and tg_thread.is_alive():
            tg_thread.join(timeout=3)
        with self._lock:
            self._thread = None
            self._telegram_thread = None
            self._telegram_thread_start_ts = 0
        self._release_telegram_poll_lock(force=True)
        self._persist(force=True)

    def restart(self) -> None:
        self.stop()
        self.start()

    def _request_async_restart(self, reason: str) -> bool:
        with self._restart_request_lock:
            if self._restart_requested:
                return False
            self._restart_requested = True

        def _worker() -> None:
            try:
                time.sleep(1.0)
                self.restart()
                self._push_alert("warn", "엔진 재시작", f"자동 재시작 완료: {reason}", send_telegram=True)
            except Exception as exc:  # noqa: BLE001
                self._emit_runtime_error("core:auto_restart", "자동 재시작 실패", str(exc), cooldown_seconds=120)
            finally:
                with self._restart_request_lock:
                    self._restart_requested = False

        threading.Thread(target=_worker, name="engine-auto-restart", daemon=True).start()
        return True

    def set_trade_mode(self, mode: str) -> None:
        before_mode = str(self.settings.trade_mode or "paper").lower()
        normalized = "live" if str(mode).lower() == "live" else "paper"
        updates: dict[str, Any] = {
            "TRADE_MODE": normalized,
            # Demo loop always runs. This flag now controls only real execution ON/OFF.
            "ENABLE_LIVE_EXECUTION": bool(normalized == "live"),
        }
        # Explicit live-mode request from UI should also release paper-lock.
        if normalized == "live" and bool(self.settings.lock_paper_mode):
            updates["LOCK_PAPER_MODE"] = False
        save_runtime_overrides(self.settings, updates)
        self._reload_settings()
        if normalized == "live" and before_mode != "live":
            now = int(time.time())
            self._sync_wallet(now, force=True)
            self._sync_bybit(now, force=True)
            self._sync_live_seed_if_idle(now, force=True)
            self._sync_live_wallet_managed_positions(now)
            self._persist(force=True)
        self._invalidate_dashboard_cache()

    def set_autotrade(self, enabled: bool) -> None:
        save_runtime_overrides(self.settings, {"ENABLE_AUTOTRADE": bool(enabled)})
        self._reload_settings()
        self._invalidate_dashboard_cache()

    def set_live_markets(self, meme_enabled: Any = None, crypto_enabled: Any = None) -> dict[str, bool]:
        updates: dict[str, Any] = {}
        out = {
            "meme": bool(self.settings.live_enable_meme),
            "crypto": bool(self.settings.live_enable_crypto),
        }
        if meme_enabled is not None:
            updates["LIVE_ENABLE_MEME"] = bool(meme_enabled)
            out["meme"] = bool(meme_enabled)
        if crypto_enabled is not None:
            updates["LIVE_ENABLE_CRYPTO"] = bool(crypto_enabled)
            out["crypto"] = bool(crypto_enabled)
        if updates:
            save_runtime_overrides(self.settings, updates)
            self._reload_settings()
            self._sync_live_seed_if_idle(int(time.time()), force=True)
            out = {
                "meme": bool(self.settings.live_enable_meme),
                "crypto": bool(self.settings.live_enable_crypto),
            }
            self._push_alert(
                "info",
                "실전 시장 ON/OFF 변경",
                f"밈={ 'ON' if out['meme'] else 'OFF' } | 크립토={ 'ON' if out['crypto'] else 'OFF' }",
                send_telegram=False,
            )
            self._invalidate_dashboard_cache()
        return out

    def set_demo_reset_enabled(self, enabled: bool) -> None:
        save_runtime_overrides(self.settings, {"ALLOW_DEMO_RESET": bool(enabled)})
        self._reload_settings()

    def set_telegram_trade_alerts(self, enabled: bool) -> None:
        save_runtime_overrides(self.settings, {"TELEGRAM_TRADE_ALERTS_ENABLED": bool(enabled)})
        self._reload_settings()

    def set_telegram_report(self, enabled: bool) -> None:
        save_runtime_overrides(self.settings, {"TELEGRAM_REPORT_ENABLED": bool(enabled)})
        self._reload_settings()

    def set_autotrade_models_runtime(self, meme_models: Any = None, crypto_models: Any = None) -> dict[str, list[str]]:
        updates: dict[str, Any] = {}
        out: dict[str, list[str]] = {
            "meme": list(self._autotrade_model_ids("meme")),
            "crypto": list(self._autotrade_model_ids("crypto")),
        }

        def _parse(raw: Any, market: str) -> tuple[str, ...]:
            if raw is None:
                return tuple()
            if isinstance(raw, (list, tuple)):
                joined = ",".join(str(x or "").strip() for x in raw)
            else:
                joined = str(raw or "").strip()
            allowed_ids = self._market_model_ids("meme" if market == "밈" else "crypto")
            parsed = self._parse_model_id_csv(joined, fallback_all=False, allowed_ids=allowed_ids)
            if not parsed:
                # If that market is live-off, empty model selection is acceptable.
                if market == "밈" and not bool(self.settings.live_enable_meme):
                    return tuple()
                if market == "크립토" and not bool(self.settings.live_enable_crypto):
                    return tuple()
                allowed_text = "/".join(allowed_ids)
                raise ValueError(f"{market} 모델 선택이 비어있습니다. {allowed_text} 중 최소 1개를 선택하세요.")
            return parsed

        if meme_models is not None:
            parsed = _parse(meme_models, "밈")
            if parsed:
                updates["MEME_AUTOTRADE_MODELS"] = ",".join(parsed)
                out["meme"] = list(parsed)
        if crypto_models is not None:
            parsed = _parse(crypto_models, "크립토")
            if parsed:
                updates["CRYPTO_AUTOTRADE_MODELS"] = ",".join(parsed)
                out["crypto"] = list(parsed)
        if updates:
            save_runtime_overrides(self.settings, updates)
            self._reload_settings()
            self._push_alert(
                "info",
                "실전 모델 선택 변경",
                f"밈={','.join(out['meme'])} | 크립토={','.join(out['crypto'])}",
                send_telegram=False,
            )
        return out

    def set_live_models_runtime(self, meme_models: Any = None, crypto_models: Any = None) -> dict[str, list[str]]:
        updates: dict[str, Any] = {}
        out: dict[str, list[str]] = {
            "meme": list(self._live_model_ids("meme")),
            "crypto": list(self._live_model_ids("crypto")),
        }

        def _parse(raw: Any, market: str) -> tuple[str, ...]:
            if raw is None:
                return tuple()
            if isinstance(raw, (list, tuple)):
                joined = ",".join(str(x or "").strip() for x in raw)
            else:
                joined = str(raw or "").strip()
            allowed_ids = self._market_model_ids("meme" if market == "밈" else "crypto")
            parsed = self._parse_model_id_csv(joined, fallback_all=False, allowed_ids=allowed_ids)
            if not parsed:
                if market == "밈" and not bool(self.settings.live_enable_meme):
                    return tuple()
                if market == "크립토" and not bool(self.settings.live_enable_crypto):
                    return tuple()
                allowed_text = "/".join(allowed_ids)
                raise ValueError(f"{market} 실전 모델 선택이 비어있습니다. {allowed_text} 중 최소 1개를 선택하세요.")
            return parsed

        if meme_models is not None:
            parsed = _parse(meme_models, "밈")
            if parsed:
                updates["LIVE_MEME_MODELS"] = ",".join(parsed)
                out["meme"] = list(parsed)
        if crypto_models is not None:
            parsed = _parse(crypto_models, "크립토")
            if parsed:
                updates["LIVE_CRYPTO_MODELS"] = ",".join(parsed)
                out["crypto"] = list(parsed)
        if updates:
            save_runtime_overrides(self.settings, updates)
            self._reload_settings()
            self._push_alert(
                "info",
                "실전 모델 선택 변경",
                f"밈={','.join(out['meme'])} | 크립토={','.join(out['crypto'])}",
                send_telegram=False,
            )
        return out

    @staticmethod
    def _mask_secret_value(raw: Any) -> str:
        value = str(raw or "").strip()
        if not value:
            return "(not set)"
        n = len(value)
        if n <= 4:
            return "*" * n
        keep = max(1, int(math.floor(n * 0.1)))
        tail = max(1, int(math.floor(n * 0.1)))
        if keep + tail >= n:
            keep = 1
            tail = 1
        hidden = max(1, n - keep - tail)
        return f"{value[:keep]}{'*' * hidden}{value[-tail:]}"

    def secret_settings_payload(self) -> dict[str, Any]:
        with self._lock:
            settings = self.settings
            value_map = {
                "BYBIT_API_KEY": settings.bybit_api_key,
                "BYBIT_API_SECRET": settings.bybit_api_secret,
                "PHANTOM_WALLET_ADDRESS": settings.phantom_wallet_address,
                "SOLANA_PRIVATE_KEY": settings.solana_private_key,
                "SOLANA_RPC_URL": settings.solana_rpc_url,
                "TELEGRAM_BOT_TOKEN": settings.telegram_bot_token,
                "TELEGRAM_CHAT_ID": settings.telegram_chat_id,
                "GOOGLE_API_KEY": settings.google_api_key,
                "SOLSCAN_API_KEY": settings.solscan_api_key,
                "HELIUS_API_KEY": settings.helius_api_key,
                "HELIUS_RPC_URL": settings.helius_rpc_url,
                "HELIUS_WS_URL": settings.helius_ws_url,
                "HELIUS_SENDER_URL": settings.helius_sender_url,
                "BIRDEYE_API_KEY": settings.birdeye_api_key,
                "OPENAI_API_KEY": settings.openai_api_key,
                "BINANCE_API_KEY": settings.binance_api_key,
                "BINANCE_API_SECRET": settings.binance_api_secret,
                "COINGECKO_API_KEY": settings.coingecko_api_key,
                "CMC_API_KEY": settings.cmc_api_key,
            }
        out: dict[str, Any] = {}
        for key, value in value_map.items():
            plain = str(value or "").strip()
            out[key] = {
                "configured": bool(plain),
                "masked": self._mask_secret_value(plain),
            }
        return out

    def update_secret_settings(self, updates: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(updates, dict):
            return self.secret_settings_payload()
        clean: dict[str, Any] = {}
        for key in SECRET_UPDATE_KEYS:
            if key not in updates:
                continue
            raw = str(updates.get(key) or "").strip()
            if not raw:
                continue
            # Prevent accidentally writing masked placeholders (e.g. ********).
            if raw.count("*") >= max(4, int(len(raw) * 0.6)):
                continue
            clean[key] = raw
        if "SOLANA_PRIVATE_KEY" in clean:
            private_key = str(clean.get("SOLANA_PRIVATE_KEY") or "").strip()
            wallet_address = str(clean.get("PHANTOM_WALLET_ADDRESS") or self.settings.phantom_wallet_address or "").strip()
            if private_key and wallet_address and private_key == wallet_address:
                raise ValueError("SOLANA_PRIVATE_KEY에는 지갑 주소가 아니라 프라이빗 키를 입력해야 합니다.")
            probe = JupiterSolanaTrader(
                rpc_url=str(self.settings.solana_rpc_url or "http://localhost"),
                private_key=private_key,
                wallet_address=wallet_address,
                timeout_seconds=5,
            )
            if probe.init_error:
                raise ValueError(f"SOLANA_PRIVATE_KEY 검증 실패: {self._translate_error_to_korean(probe.init_error)}")
        if clean:
            save_runtime_overrides(self.settings, clean)
            self._reload_settings()
            self._push_alert(
                "info",
                "설정 업데이트",
                f"민감 설정 {len(clean)}개가 저장되었습니다.",
                send_telegram=False,
            )
        return self.secret_settings_payload()

    def force_sync(self) -> None:
        now = int(time.time())
        self._sync_wallet(now, force=True)
        self._sync_bybit(now, force=True)
        self._sync_live_wallet_managed_positions(now)
        self._persist(force=True)

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
        self._persist(force=True)
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
        action = TradingEngine._error_action_hint(low)
        return f"{reason} 조치={action} detail={compact}"

    @staticmethod
    def _error_action_hint(low_text: str) -> str:
        low = str(low_text or "").lower()
        if "429" in low or "too many requests" in low or "rate-limited" in low:
            return "호출 주기를 늘리고(>=300s) 폴백 소스를 사용하세요."
        if "409" in low or "conflict" in low:
            return "중복 프로세스를 종료하고 봇 인스턴스를 1개만 유지하세요."
        if "failed to resolve" in low or "name or service not known" in low or "nameresolutionerror" in low:
            return "도메인 주소를 교체하거나 DNS/네트워크 상태를 점검하세요."
        if "invalid_api_key" in low or "api key not valid" in low or "permission denied" in low:
            return "키/권한/허용 IP를 재확인하고 재발급 후 반영하세요."
        if "110007" in low or "not enough for new order" in low or "not enough hold money" in low:
            return "잔고 기준 주문비율을 낮추고 잔고 부족 시 진입을 건너뛰세요."
        if "custom(6024)" in low or "0x1788" in low:
            return "유동성 높은 라우트 우선, 슬리피지 상향, 최소 주문금액 상향 후 재시도하세요."
        if "timeout" in low or "timed out" in low:
            return "타임아웃을 늘리고 재시도 간격을 증가시키세요."
        return "에러 상세 로그를 확인해 소스별 재시도/비활성 정책을 적용하세요."

    @staticmethod
    def _error_signature(raw_error: str) -> str:
        low = str(raw_error or "").strip().lower()
        if not low:
            return "empty"
        if "custom(6024)" in low or "0x1788" in low:
            return "solana_swap_sim_0x1788"
        if "token_not_tradable" in low or "not tradable" in low:
            return "token_not_tradable"
        if "no_wallet_balance_for_live_position" in low or "no_wallet_balance" in low:
            return "no_wallet_balance"
        if "429" in low or "too many requests" in low or "rate-limited" in low:
            return "rate_limited_429"
        if "409" in low or "conflict" in low:
            return "telegram_poll_conflict_409"
        if "timeout" in low or "timed out" in low:
            return "request_timeout"
        if "failed to resolve" in low or "name or service not known" in low or "nameresolutionerror" in low:
            return "dns_resolution_failed"
        if "invalid_api_key" in low or "api key not valid" in low or "permission denied" in low:
            return "api_key_permission"
        if "110007" in low or "not enough for new order" in low or "not enough hold money" in low:
            return "insufficient_balance"
        compact = " ".join(low.replace("\r", " ").replace("\n", " ").split())
        return compact[:120]

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
        sig = self._error_signature(raw_error)
        prev = dict(self._runtime_error_notice.get(key) or {})
        prev_text = str(prev.get("text") or "")
        prev_sig = str(prev.get("sig") or "")
        prev_ts = int(prev.get("ts") or 0)
        if (prev_sig == sig or prev_text == text) and (now - prev_ts) < max(30, int(cooldown_seconds)):
            return
        self._runtime_error_notice[key] = {"text": text, "sig": sig, "ts": now}
        self._push_alert(level, title, text, send_telegram=True)
        try:
            low_raw = str(raw_error or "").strip().lower()
            action = self._error_action_hint(low_raw)
            self.runtime_feedback.append_event(
                source=str(key or "runtime_error"),
                level=str(level or "error").lower(),
                status="error_notice",
                error=str(raw_error or "").strip(),
                action=str(action or ""),
                detail=str(text or ""),
                meta={"title": str(title or "")},
                now_ts=now,
            )
        except Exception:
            pass

    def _scan_and_notify_runtime_errors(self) -> None:
        with self._lock:
            memecoin_error = str(self.state.memecoin_error or "")
            bybit_error = str(self.state.bybit_error or "")
            trend_status = dict(self._trend_source_status or {})
        if memecoin_error:
            low = str(memecoin_error or "").lower()
            duplicate_scoped = (
                low.startswith("live_skip:")
                or "open_failed:" in low
                or "close_failed:" in low
                or low.startswith("live_meme_open_failed:")
            )
            if not duplicate_scoped:
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

    def _loop(self, run_epoch: int) -> None:
        while self._running and int(run_epoch) == int(self._run_epoch):
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

    def _telegram_loop(self, run_epoch: int) -> None:
        while self._running and int(run_epoch) == int(self._run_epoch):
            try:
                if self.telegram.enabled and not bool(self._telegram_webhook_init_done):
                    self.telegram.delete_webhook(drop_pending_updates=False)
                    self._telegram_webhook_init_done = True
                self._poll_telegram(int(time.time()), run_epoch=run_epoch)
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
        self._sync_live_wallet_managed_positions(now)
        tg_thread = self._telegram_thread
        telegram_alive = bool(tg_thread and tg_thread.is_alive())
        telegram_grace = max(10, int(self.settings.telegram_poll_interval_seconds) * 3)
        telegram_thread_start_ts = int(self._telegram_thread_start_ts or 0)
        should_fallback_poll = (
            not telegram_alive
            and telegram_thread_start_ts > 0
            and (now - telegram_thread_start_ts) >= telegram_grace
        )
        if should_fallback_poll:
            self._poll_telegram(now)
        self._update_focus_wallet_analysis(now)

        trend_bundle: dict[str, Any] = {}
        snapshots: list[TokenSnapshot] = []
        if self._meme_market_enabled():
            trend_bundle = self._fetch_trends()
            snapshots = self._fetch_snapshots(trend_bundle)
            self._update_new_meme_feed(snapshots, trend_bundle)
            self._persist_trend_history(now, trend_bundle)
            self._refresh_meme_watch_scores(now)
        bybit_prices = self._fetch_macro_demo_prices(trend_bundle) if self.settings.demo_enable_macro else {}
        crypto_intrabar_candles = (
            self._fetch_crypto_intrabar_candles(list(bybit_prices.keys()))
            if self.settings.demo_enable_macro and bybit_prices
            else {}
        )

        if self._meme_market_enabled():
            for model_id in MEME_MODEL_IDS:
                with self._lock:
                    meme_key = self._market_run_key("meme", model_id)
                    run = self.state.model_runs.get(meme_key)
                    if not isinstance(run, dict):
                        run = self._blank_market_run("meme", model_id, self.state.demo_seed_usdt)
                        self.state.model_runs[meme_key] = run
                    self._normalize_market_run(run, "meme", model_id, self.state.demo_seed_usdt)

                signals, scored_rows = self._score_signals_variant(snapshots, trend_bundle, model_id)
                run["latest_signals"] = [
                    {
                        "symbol": s["token"].symbol,
                        "name": s["token"].name,
                        "strategy_id": str(s.get("strategy_id") or "THEME"),
                        "strategy_name": str(
                            s.get("strategy_name") or self._meme_strategy_name(str(s.get("strategy_id") or "THEME"))
                        ),
                        "grade": str(s.get("grade") or "G"),
                        "score": round(float(s["score"]), 4),
                        "probability": round(float(s["probability"]), 4),
                        "price_usd": float(s["token"].price_usd),
                        "liquidity_usd": float(s["token"].liquidity_usd),
                        "volume_5m_usd": float(s["token"].volume_5m_usd),
                        "market_cap_usd": float(self._meme_effective_cap_usd(s["token"])),
                        "age_minutes": float(s["token"].age_minutes),
                        "reason": str(s["reason"]),
                        "token_address": s["token"].token_address,
                    }
                    for s in signals[:80]
                ]
                self._record_meme_score_history(model_id, scored_rows, now)
                self._evaluate_model_memecoin_exits(model_id, run)
                if self._is_market_autotrade_enabled("meme") and self._is_autotrade_model_enabled("meme", model_id):
                    self._execute_model_memecoin_entries(model_id, run, signals, execution_mode="paper")
                    if self._is_live_execution_market("meme", model_id):
                        self._execute_model_memecoin_entries(model_id, run, signals, execution_mode="live")

        if self.settings.demo_enable_macro:
            for model_id in CRYPTO_MODEL_IDS:
                with self._lock:
                    crypto_key = self._market_run_key("crypto", model_id)
                    run = self.state.model_runs.get(crypto_key)
                    if not isinstance(run, dict):
                        run = self._blank_market_run("crypto", model_id, self.state.demo_seed_usdt)
                        self.state.model_runs[crypto_key] = run
                    self._normalize_market_run(run, "crypto", model_id, self.state.demo_seed_usdt)

                self._process_crypto_intrabar_window(model_id, run, bybit_prices, crypto_intrabar_candles, now)
                run["latest_crypto_signals"] = self._score_crypto_signals(model_id, run, bybit_prices, trend_bundle)[:80]
                self._evaluate_model_bybit_exits(model_id, run, bybit_prices)
                if self._is_market_autotrade_enabled("crypto") and self._is_autotrade_model_enabled("crypto", model_id):
                    self._execute_model_bybit_entries(
                        model_id,
                        run,
                        bybit_prices,
                        trend_bundle,
                        list(run.get("latest_crypto_signals") or []),
                    )

        self._record_daily_pnl(now)
        self._maybe_emit_daily_git_report(now)
        self._maybe_autotune_models(now)
        self._maybe_drawdown_guard_restart(now)
        self._sync_primary_views_from_model_a()
        self._sync_supabase_snapshot(now)
        self._scan_and_notify_runtime_errors()
        self._send_telegram_periodic_report(now)

    def _record_meme_score_history(self, model_id: str, signals: list[dict[str, Any]], now_ts: int) -> None:
        rows: list[dict[str, Any]] = []
        watch_ranked: dict[str, list[dict[str, Any]]] = {}
        watch_snapshots: dict[str, TokenSnapshot] = {}
        for row in list(signals or [])[:120]:
            token = row.get("token")
            if not isinstance(token, TokenSnapshot):
                continue
            token_address = str(token.token_address or "").strip()
            if not token_address:
                continue
            score_now = float(row.get("score") or 0.0)
            grade_now = str(row.get("grade") or "G").upper().strip() or "G"
            guard_key = f"{str(model_id or 'A').upper()}:{token_address}"
            prev = dict(self._meme_score_log_guard.get(guard_key) or {})
            prev_ts = int(prev.get("ts") or 0)
            prev_score = float(prev.get("score") or 0.0)
            prev_grade = str(prev.get("grade") or "").upper().strip()
            if (int(now_ts) - prev_ts) < 900 and abs(score_now - prev_score) < 0.015 and grade_now == prev_grade:
                continue
            rows.append(
                {
                    "token_address": token_address,
                    "symbol": str(token.symbol or "").upper().strip(),
                    "name": str(token.name or "").strip(),
                    "score": float(score_now),
                    "grade": grade_now,
                    "probability": float(row.get("probability") or 0.0),
                    "price_usd": float(token.price_usd or 0.0),
                    "liquidity_usd": float(token.liquidity_usd or 0.0),
                    "volume_5m_usd": float(token.volume_5m_usd or 0.0),
                    "market_cap_usd": float(self._meme_effective_cap_usd(token)),
                    "age_minutes": float(token.age_minutes or 0.0),
                    "reason": str(row.get("reason") or ""),
                }
            )
            if score_now >= 0.30:
                self._touch_meme_watch_token(token_address, now_ts=int(now_ts))
                watch_ranked.setdefault(token_address, []).append(dict(row or {}))
                watch_snapshots[token_address] = token
            self._meme_score_log_guard[guard_key] = {"ts": int(now_ts), "score": float(score_now), "grade": grade_now}
            if len(self._meme_score_log_guard) > 20000:
                sorted_rows = sorted(
                    list(self._meme_score_log_guard.items()),
                    key=lambda kv: int((kv[1] or {}).get("ts") or 0),
                    reverse=True,
                )[:12000]
                self._meme_score_log_guard = {str(k): dict(v or {}) for k, v in sorted_rows}
        if not rows:
            return
        try:
            self.runtime_feedback.append_meme_score_points(
                str(model_id or "A"),
                rows,
                now_ts=int(now_ts),
                source="meme_cycle",
            )
        except Exception:
            # score logging must not break engine loop
            return
        for token_address, ranked_rows in watch_ranked.items():
            snapshot = watch_snapshots.get(token_address)
            if not isinstance(snapshot, TokenSnapshot):
                continue
            try:
                ranked = sorted(
                    [dict(r or {}) for r in list(ranked_rows or [])],
                    key=lambda r: float(r.get("score") or 0.0),
                    reverse=True,
                )
                self._update_meme_watch_latest(
                    token_address,
                    snapshot,
                    ranked,
                    now_ts=int(now_ts),
                    source="meme_cycle",
                )
            except Exception:
                continue

    def _touch_meme_watch_token(self, token_address: str, *, now_ts: int | None = None, ttl_seconds: int | None = None) -> None:
        token = str(token_address or "").strip()
        if not token:
            return
        now = int(now_ts or int(time.time()))
        ttl = max(900, int(ttl_seconds or MEME_WATCHLIST_TTL_SECONDS))
        until_ts = int(now + ttl)
        with self._lock:
            rows = dict(self._meme_watch_tokens or {})
            prev_until = int(rows.get(token) or 0)
            # Fixed 30-minute watch window: do not extend while already active.
            rows[token] = int(until_ts if prev_until <= now else prev_until)
            if len(rows) > MEME_WATCHLIST_MAX_TOKENS:
                items = sorted(rows.items(), key=lambda kv: int(kv[1] or 0), reverse=True)[:MEME_WATCHLIST_MAX_TOKENS]
                rows = {str(k): int(v or 0) for k, v in items}
            self._meme_watch_tokens = rows

    def _meme_watch_tokens_snapshot(self, now_ts: int | None = None) -> dict[str, int]:
        now = int(now_ts or int(time.time()))
        with self._lock:
            rows = dict(self._meme_watch_tokens or {})
        out = {str(k): int(v or 0) for k, v in rows.items() if int(v or 0) > now}
        if len(out) != len(rows):
            with self._lock:
                self._meme_watch_tokens = dict(out)
                latest_rows = dict(self._meme_watch_latest or {})
                for token in list(latest_rows.keys()):
                    if str(token) not in out:
                        latest_rows.pop(token, None)
                self._meme_watch_latest = latest_rows
        return out

    def _refresh_meme_watch_scores(self, now_ts: int) -> None:
        watch_map = self._meme_watch_tokens_snapshot(now_ts=now_ts)
        if not watch_map:
            return
        with self._lock:
            last_map = dict(self._meme_watch_score_last_ts or {})
        rows = sorted(watch_map.items(), key=lambda kv: int(kv[1] or 0), reverse=True)
        refreshed = 0
        for token, _ in rows:
            if refreshed >= 12:
                break
            token_addr = str(token or "").strip()
            if not token_addr:
                continue
            prev_ts = int(last_map.get(token_addr) or 0)
            if prev_ts > 0 and (int(now_ts) - prev_ts) < int(MEME_WATCH_SCORE_REFRESH_SECONDS):
                continue
            try:
                result = self._score_meme_token_now(token_addr, now_ts=now_ts, source="watch_cycle")
            except Exception:
                result = {"found": False}
            if bool((result or {}).get("found")):
                last_map[token_addr] = int(now_ts)
                refreshed += 1
        for token in list(last_map.keys()):
            if str(token) not in watch_map:
                last_map.pop(token, None)
        if len(last_map) > MEME_WATCHLIST_MAX_TOKENS:
            items = sorted(last_map.items(), key=lambda kv: int(kv[1] or 0), reverse=True)[:MEME_WATCHLIST_MAX_TOKENS]
            last_map = {str(k): int(v or 0) for k, v in items}
        with self._lock:
            self._meme_watch_score_last_ts = dict(last_map)

    def _update_meme_watch_latest(
        self,
        token_address: str,
        snapshot: TokenSnapshot,
        ranked_rows: list[dict[str, Any]],
        *,
        now_ts: int,
        source: str = "",
    ) -> None:
        token = str(token_address or "").strip()
        if not token:
            return
        ranked = list(ranked_rows or [])
        best: dict[str, Any] = {}
        for row in ranked:
            if str((row or {}).get("model_id") or "").upper().strip() == "C":
                best = dict(row or {})
                break
        if not best and ranked:
            best = dict(ranked[0] or {})
        with self._lock:
            watch_until = int((self._meme_watch_tokens or {}).get(token) or (int(now_ts) + int(MEME_WATCHLIST_TTL_SECONDS)))
            rows = dict(self._meme_watch_latest or {})
            rows[token] = {
                "ts": int(now_ts),
                "source": str(source or ""),
                "token_address": token,
                "symbol": str(snapshot.symbol or "").upper().strip(),
                "name": str(snapshot.name or ""),
                "model_id": str(best.get("model_id") or ""),
                "model_name": self._market_model_name("meme", str(best.get("model_id") or "")),
                "score": float(best.get("score") or 0.0),
                "grade": str(best.get("grade") or "G"),
                "probability": float(best.get("probability") or 0.0),
                "reason": str(best.get("reason") or ""),
                "score_low_reason": str(best.get("score_low_reason") or ""),
                "score_hold_hint": str(best.get("score_hold_hint") or ""),
                "score_hold_target_grade": str(best.get("score_hold_target_grade") or ""),
                "score_hold_target_score": float(best.get("score_hold_target_score") or 0.0),
                "score_hold_gap": float(best.get("score_hold_gap") or 0.0),
                "price_usd": float(snapshot.price_usd or 0.0),
                "liquidity_usd": float(snapshot.liquidity_usd or 0.0),
                "volume_5m_usd": float(snapshot.volume_5m_usd or 0.0),
                "market_cap_usd": float(self._meme_effective_cap_usd(snapshot)),
                "age_minutes": float(snapshot.age_minutes or 0.0),
                "watch_until_ts": int(watch_until),
                "watch_remaining_seconds": max(0, int(watch_until - int(now_ts))),
            }
            if len(rows) > MEME_WATCHLIST_MAX_TOKENS:
                items = sorted(
                    rows.items(),
                    key=lambda kv: int(((kv[1] or {}).get("ts") if isinstance(kv[1], dict) else 0) or 0),
                    reverse=True,
                )[:MEME_WATCHLIST_MAX_TOKENS]
                rows = {str(k): dict(v or {}) for k, v in items}
            self._meme_watch_latest = rows

    def _build_live_meme_watch_rows(self, now_ts: int, limit: int = 120) -> list[dict[str, Any]]:
        n = max(10, min(500, int(limit)))
        watch_map = self._meme_watch_tokens_snapshot(now_ts=now_ts)
        if not watch_map:
            recent_rows = list(
                self.runtime_feedback.meme_score_watch_recent(
                    lookback_seconds=int(MEME_WATCHLIST_TTL_SECONDS),
                    limit=n,
                    model_id="C",
                )
            )
            out_recent: list[dict[str, Any]] = []
            for row in recent_rows:
                token_addr = str((row or {}).get("token_address") or "").strip()
                if not token_addr:
                    continue
                ts = int((row or {}).get("ts") or 0)
                if ts <= 0:
                    continue
                until_ts = int(ts + int(MEME_WATCHLIST_TTL_SECONDS))
                remain = max(0, int(until_ts - int(now_ts)))
                if remain <= 0:
                    continue
                out_row = dict(row or {})
                out_row["model_name"] = self._market_model_name("meme", str((row or {}).get("model_id") or ""))
                hold_grade = self._meme_hold_target_grade(str((row or {}).get("model_id") or "C"))
                hold_score = float(self._meme_grade_min_score(hold_grade))
                score_now = float((row or {}).get("score") or 0.0)
                if not str(out_row.get("score_low_reason") or "").strip():
                    out_row["score_low_reason"] = str((row or {}).get("reason") or "")
                if not str(out_row.get("score_hold_hint") or "").strip():
                    out_row["score_hold_hint"] = (
                        f"홀딩권장 {hold_grade}({hold_score:.2f})까지 +{max(0.0, hold_score - score_now):.2f}"
                    )
                out_row["watch_until_ts"] = int(until_ts)
                out_row["watch_remaining_seconds"] = int(remain)
                out_recent.append(out_row)
            if out_recent:
                return out_recent[:n]
            return []
        with self._lock:
            latest_map = dict(self._meme_watch_latest or {})
            snap_cache = dict(self._meme_watch_snapshot_cache or {})
        out: list[dict[str, Any]] = []
        for token, until_ts in watch_map.items():
            token_addr = str(token or "").strip()
            if not token_addr:
                continue
            latest = dict(latest_map.get(token_addr) or {})
            if not latest:
                cached = dict(snap_cache.get(token_addr) or {})
                snap = cached.get("snapshot")
                if isinstance(snap, TokenSnapshot):
                    latest = {
                        "ts": int(cached.get("ts") or 0),
                        "source": "snapshot_cache",
                        "token_address": token_addr,
                        "symbol": str(snap.symbol or "").upper().strip(),
                        "name": str(snap.name or ""),
                        "model_id": "",
                        "model_name": "-",
                        "score": 0.0,
                        "grade": "-",
                        "probability": 0.0,
                        "reason": "",
                        "price_usd": float(snap.price_usd or 0.0),
                        "liquidity_usd": float(snap.liquidity_usd or 0.0),
                        "volume_5m_usd": float(snap.volume_5m_usd or 0.0),
                        "market_cap_usd": float(self._meme_effective_cap_usd(snap)),
                        "age_minutes": float(snap.age_minutes or 0.0),
                    }
            if not latest:
                continue
            row = dict(latest)
            hold_grade = self._meme_hold_target_grade(str(row.get("model_id") or "C"))
            hold_score = float(self._meme_grade_min_score(hold_grade))
            score_now = float(row.get("score") or 0.0)
            if not str(row.get("score_low_reason") or "").strip():
                row["score_low_reason"] = str(row.get("reason") or "")
            if not str(row.get("score_hold_hint") or "").strip():
                row["score_hold_hint"] = f"홀딩권장 {hold_grade}({hold_score:.2f})까지 +{max(0.0, hold_score - score_now):.2f}"
            row["watch_until_ts"] = int(until_ts)
            row["watch_remaining_seconds"] = max(0, int(until_ts - int(now_ts)))
            out.append(row)
        out.sort(
            key=lambda r: (
                float((r or {}).get("score") or 0.0),
                float((r or {}).get("volume_5m_usd") or 0.0),
                float((r or {}).get("liquidity_usd") or 0.0),
                int((r or {}).get("watch_until_ts") or 0),
            ),
            reverse=True,
        )
        return out[:n]

    def _trend_bundle_from_cache(self) -> dict[str, Any]:
        trending: set[str] = set(self._trend_cache_trending or set())
        trader_events = list(self._trend_cache_events.get("trader_x") or [])
        wallet_events = list(self._trend_cache_events.get("wallet_tracker") or [])
        news_events = list(self._trend_cache_events.get("yahoo_news") or [])
        community_events = list(self._trend_cache_events.get("community_reddit") or [])
        community_events.extend(list(self._trend_cache_events.get("community_4chan") or []))
        google_events = list(self._trend_cache_events.get("google_gemini") or [])

        trader_counts: dict[str, int] = {}
        wallet_counts: dict[str, int] = {}
        news_counts: dict[str, int] = {}
        community_counts: dict[str, int] = {}
        google_counts: dict[str, int] = {}
        combined_counts: dict[str, int] = {}
        for ev in trader_events:
            sym = str(getattr(ev, "symbol", "") or "").upper().strip()
            if not sym:
                continue
            trader_counts[sym] = trader_counts.get(sym, 0) + 1
            combined_counts[sym] = combined_counts.get(sym, 0) + 1
        for ev in wallet_events:
            sym = str(getattr(ev, "symbol", "") or "").upper().strip()
            if not sym:
                continue
            wallet_counts[sym] = wallet_counts.get(sym, 0) + 1
            combined_counts[sym] = combined_counts.get(sym, 0) + 1
        for ev in news_events:
            sym = str(getattr(ev, "symbol", "") or "").upper().strip()
            if not sym:
                continue
            news_counts[sym] = news_counts.get(sym, 0) + 1
            combined_counts[sym] = combined_counts.get(sym, 0) + 1
        for ev in community_events:
            sym = str(getattr(ev, "symbol", "") or "").upper().strip()
            if not sym:
                continue
            community_counts[sym] = community_counts.get(sym, 0) + 1
            combined_counts[sym] = combined_counts.get(sym, 0) + 1
        for ev in google_events:
            sym = str(getattr(ev, "symbol", "") or "").upper().strip()
            if not sym:
                continue
            google_counts[sym] = google_counts.get(sym, 0) + 1
            combined_counts[sym] = combined_counts.get(sym, 0) + 1
        for sym, hits in combined_counts.items():
            if int(hits) >= 2:
                trending.add(sym)
        for sym, hits in google_counts.items():
            non_google_hits = (
                int(trader_counts.get(sym, 0))
                + int(wallet_counts.get(sym, 0))
                + int(news_counts.get(sym, 0))
                + int(community_counts.get(sym, 0))
            )
            if int(hits) >= 2 and non_google_hits >= 1:
                trending.add(sym)
        return {
            "trending": trending,
            "trader_counts": trader_counts,
            "wallet_counts": wallet_counts,
            "news_counts": news_counts,
            "community_counts": community_counts,
            "google_counts": google_counts,
            "combined_counts": combined_counts,
            "source_status": dict(self._trend_source_status or {}),
        }

    def _score_meme_token_now(
        self,
        token_address: str,
        now_ts: int | None = None,
        *,
        source: str = "lookup",
    ) -> dict[str, Any]:
        token = str(token_address or "").strip()
        if not token:
            return {"found": False, "error": "token_address_required"}
        now = int(now_ts or int(time.time()))
        snapshot: TokenSnapshot | None = None
        snapshot_error = ""
        try:
            snapshot = self.dex.fetch_snapshot_for_token(self.settings.dex_chain, token)
        except Exception as exc:  # noqa: BLE001
            snapshot_error = f"dex_lookup_failed:{exc}"
        if snapshot is None:
            try:
                if bool(self.settings.pumpfun_enabled) and str(self.settings.dex_chain).lower() == "solana":
                    pump_rows = self.pumpfun.fetch_latest_coins(
                        limit=max(120, int(self.settings.pumpfun_fetch_limit)),
                        include_nsfw=bool(self.settings.pumpfun_include_nsfw),
                        cache_seconds=int(self.settings.pumpfun_cache_seconds),
                    )
                    for row in pump_rows:
                        if str((row or {}).get("mint") or "").strip() != token:
                            continue
                        snapshot = self._snapshot_from_pump_coin(row)
                        break
            except Exception:
                snapshot = snapshot
        if snapshot is None:
            return {"found": False, "error": snapshot_error or "snapshot_not_found"}
        self._touch_meme_watch_token(token, now_ts=now)
        with self._lock:
            cache_rows = dict(self._meme_watch_snapshot_cache or {})
            cache_rows[token] = {"ts": int(now), "snapshot": snapshot}
            if len(cache_rows) > MEME_WATCHLIST_MAX_TOKENS:
                items = sorted(
                    cache_rows.items(),
                    key=lambda kv: int(((kv[1] or {}).get("ts") if isinstance(kv[1], dict) else 0) or 0),
                    reverse=True,
                )[:MEME_WATCHLIST_MAX_TOKENS]
                cache_rows = {str(k): dict(v or {}) for k, v in items}
            self._meme_watch_snapshot_cache = cache_rows

        trend_bundle = self._trend_bundle_from_cache()
        if not trend_bundle.get("combined_counts") and not trend_bundle.get("trending"):
            try:
                trend_bundle = self._fetch_trends()
            except Exception:
                trend_bundle = self._trend_bundle_from_cache()
        trending: set[str] = set(trend_bundle.get("trending") or set())
        trader_counts = dict(trend_bundle.get("trader_counts") or {})
        wallet_counts = dict(trend_bundle.get("wallet_counts") or {})
        news_counts = dict(trend_bundle.get("news_counts") or {})
        community_counts = dict(trend_bundle.get("community_counts") or {})
        google_counts = dict(trend_bundle.get("google_counts") or {})

        sym = str(snapshot.symbol or "").upper().strip()
        trend_hit = 1 if sym in trending else 0
        trader_hits = max(0, int(trader_counts.get(sym) or 0))
        wallet_hits = max(0, int(wallet_counts.get(sym) or 0))
        news_hits = max(0, int(news_counts.get(sym) or 0))
        community_hits = max(0, int(community_counts.get(sym) or 0))
        google_hits = max(0, int(google_counts.get(sym) or 0))
        cached_pattern = self._get_wallet_pattern_cached(token, now_ts=now)
        if (
            str(source or "").lower() == "lookup"
            and self.settings.solscan_enable_pattern
            and self.solscan.enabled
            and not bool(cached_pattern.get("available"))
        ):
            cached_pattern = self._get_wallet_pattern(token, now_ts=now)
        peer_info = self._meme_similarity_for_snapshot(snapshot)
        peer_info.update(
            self._holder_overlap_features(
                token,
                cached_pattern,
                peer_info,
                now_ts=now,
                fetch_missing_peers=bool(str(source or "").lower() == "lookup"),
                max_peer_fetches=2,
            )
        )

        scored_rows: list[dict[str, Any]] = []
        for model_id in MEME_MODEL_IDS:
            wallet_pattern = dict(cached_pattern or {})
            if (
                model_id in {"A", "B"}
                and self.settings.solscan_enable_pattern
                and self.solscan.enabled
                and not bool(wallet_pattern.get("available"))
            ):
                wallet_pattern = self._get_wallet_pattern(token, now_ts=now)
            scored = self._score_meme_snapshot_variant(
                snapshot,
                model_id,
                trend_hit=trend_hit,
                trader_hits=trader_hits,
                wallet_hits=wallet_hits,
                news_hits=news_hits,
                community_hits=community_hits,
                google_hits=google_hits,
                wallet_pattern=wallet_pattern,
                peer_info=peer_info,
            )
            scored_rows.append(scored)

        for row in scored_rows:
            try:
                self.runtime_feedback.append_meme_score_points(
                    str(row.get("model_id") or "A"),
                    [
                        {
                            "token_address": token,
                            "symbol": str(snapshot.symbol or "").upper().strip(),
                            "name": str(snapshot.name or "").strip(),
                            "score": float(row.get("score") or 0.0),
                            "grade": str(row.get("grade") or "G"),
                            "probability": float(row.get("probability") or 0.0),
                            "price_usd": float(snapshot.price_usd or 0.0),
                            "liquidity_usd": float(snapshot.liquidity_usd or 0.0),
                            "volume_5m_usd": float(snapshot.volume_5m_usd or 0.0),
                            "market_cap_usd": float(self._meme_effective_cap_usd(snapshot)),
                            "age_minutes": float(snapshot.age_minutes or 0.0),
                            "reason": str(row.get("reason") or ""),
                        }
                    ],
                    now_ts=now,
                    source=str(source or "lookup"),
                )
            except Exception:
                continue

        ranked = sorted(
            [
                {
                    "model_id": str(r.get("model_id") or ""),
                    "model_name": self._market_model_name("meme", str(r.get("model_id") or "")),
                    "score": float(r.get("score") or 0.0),
                    "grade": str(r.get("grade") or "G"),
                    "probability": float(r.get("probability") or 0.0),
                    "reason": str(r.get("reason") or ""),
                    "score_low_reason": str(r.get("score_low_reason") or ""),
                    "score_hold_hint": str(r.get("score_hold_hint") or ""),
                    "score_hold_target_grade": str(r.get("score_hold_target_grade") or ""),
                    "score_hold_target_score": float(r.get("score_hold_target_score") or 0.0),
                    "score_hold_gap": float(r.get("score_hold_gap") or 0.0),
                    "price_usd": float(snapshot.price_usd or 0.0),
                    "liquidity_usd": float(snapshot.liquidity_usd or 0.0),
                    "volume_5m_usd": float(snapshot.volume_5m_usd or 0.0),
                    "market_cap_usd": float(self._meme_effective_cap_usd(snapshot)),
                    "age_minutes": float(snapshot.age_minutes or 0.0),
                }
                for r in scored_rows
            ],
            key=lambda r: float(r.get("score") or 0.0),
            reverse=True,
        )
        self._update_meme_watch_latest(token, snapshot, ranked, now_ts=now, source=str(source or ""))
        return {
            "found": True,
            "token_address": token,
            "ts": now,
            "symbol": str(snapshot.symbol or ""),
            "name": str(snapshot.name or ""),
            "rows": ranked,
        }

    def get_meme_score_history(
        self,
        token_address: str,
        limit: int = 240,
        ensure_fresh: bool = True,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        token = str(token_address or "").strip()
        if not token:
            raise ValueError("token_address is required")
        n = max(10, min(2000, int(limit)))
        rows = list(self.runtime_feedback.meme_score_recent(token, limit=n))
        lookup: dict[str, Any] = {}
        latest_ts = int(rows[0].get("ts") or 0) if rows else 0
        stale_seconds = max(300, int(self.settings.scan_interval_seconds) * 3)
        needs_refresh = bool(
            bool(force_refresh) or not rows or (latest_ts > 0 and (int(time.time()) - latest_ts) > stale_seconds)
        )
        if ensure_fresh and needs_refresh:
            lookup = self._score_meme_token_now(token, source="lookup")
            rows = list(self.runtime_feedback.meme_score_recent(token, limit=n))
        lookup_rows = list((lookup or {}).get("rows") or [])
        if lookup_rows:
            latest_lookup_by_model = {
                str((row or {}).get("model_id") or "").upper().strip(): dict(row or {})
                for row in lookup_rows
                if str((row or {}).get("model_id") or "").strip()
            }
            enriched_rows: list[dict[str, Any]] = []
            seen_models: set[str] = set()
            for row in rows:
                item = dict(row or {})
                mid = str(item.get("model_id") or "").upper().strip()
                latest_lookup = dict(latest_lookup_by_model.get(mid) or {})
                if latest_lookup and mid not in seen_models:
                    item["score_low_reason"] = str(latest_lookup.get("score_low_reason") or "")
                    item["score_hold_hint"] = str(latest_lookup.get("score_hold_hint") or "")
                    item["score_hold_target_grade"] = str(latest_lookup.get("score_hold_target_grade") or "")
                    item["score_hold_target_score"] = float(latest_lookup.get("score_hold_target_score") or 0.0)
                    item["score_hold_gap"] = float(latest_lookup.get("score_hold_gap") or 0.0)
                    seen_models.add(mid)
                enriched_rows.append(item)
            rows = enriched_rows
        latest_by_model: dict[str, dict[str, Any]] = {}
        for row in rows:
            mid = str(row.get("model_id") or "").upper().strip()
            if not mid or mid in latest_by_model:
                continue
            latest_by_model[mid] = dict(row)
        ranked_latest = sorted(
            list(latest_by_model.values()),
            key=lambda r: str(r.get("model_id") or ""),
        )
        best_row = max(rows, key=lambda r: float(r.get("score") or 0.0)) if rows else {}
        return {
            "token_address": token,
            "count": int(len(rows)),
            "latest_by_model": ranked_latest,
            "best": dict(best_row or {}),
            "rows": rows,
            "lookup": lookup,
        }

    def _persist_trend_history(self, now_ts: int, trend_bundle: dict[str, Any]) -> None:
        combined = dict(trend_bundle.get("combined_counts") or {})
        source_status = dict(trend_bundle.get("source_status") or {})
        trader_counts = dict(trend_bundle.get("trader_counts") or {})
        wallet_counts = dict(trend_bundle.get("wallet_counts") or {})
        news_counts = dict(trend_bundle.get("news_counts") or {})
        community_counts = dict(trend_bundle.get("community_counts") or {})
        google_counts = dict(trend_bundle.get("google_counts") or {})
        trending = set(str(s or "").upper().strip() for s in set(trend_bundle.get("trending") or set()) if str(s or "").strip())
        if not combined and not source_status and not trending:
            return
        with self._lock:
            macro_meta = dict(self._macro_meta or {})
            meme_caps = dict(self._meme_symbol_market_caps or {})
            meme_ages = dict(self._meme_symbol_age_minutes or {})
        rows_meme: list[dict[str, Any]] = []
        rows_crypto: list[dict[str, Any]] = []
        symbols = set(str(k or "").upper().strip() for k in combined.keys() if str(k or "").strip())
        symbols.update(trending)
        for sym in symbols:
            trader_hits = max(0, int(trader_counts.get(sym) or 0))
            wallet_hits = max(0, int(wallet_counts.get(sym) or 0))
            news_hits = max(0, int(news_counts.get(sym) or 0))
            community_hits = max(0, int(community_counts.get(sym) or 0))
            google_hits = max(0, int(google_counts.get(sym) or 0))
            non_google_hits = trader_hits + wallet_hits + news_hits + community_hits
            hits = max(1, int(combined.get(sym) or (trader_hits + wallet_hits + news_hits + community_hits + google_hits)))
            source_count = int(sum(1 for v in (trader_hits, wallet_hits, news_hits, community_hits, google_hits) if v > 0))
            cap_usd = float(meme_caps.get(sym) or (macro_meta.get(sym) or {}).get("market_cap_usd") or 0.0)
            rank = int(self._meme_market_rank(sym, macro_meta))
            score = round(
                float(hits)
                + (0.55 * float(source_count))
                + (0.26 * float(trader_hits))
                + (0.20 * float(wallet_hits))
                + (0.15 * float(news_hits))
                + (0.12 * float(community_hits))
                + (0.06 * float(google_hits))
                + (0.90 if sym in trending else 0.0)
                + (0.35 if non_google_hits >= 2 else 0.0),
                6,
            )
            row = {
                "symbol": sym,
                "hits": int(hits),
                "source_count": int(source_count),
                "score": float(score),
                "market_cap_usd": float(cap_usd),
                "payload": {
                    "trending": bool(sym in trending),
                    "trader_hits": int(trader_hits),
                    "wallet_hits": int(wallet_hits),
                    "news_hits": int(news_hits),
                    "community_hits": int(community_hits),
                    "google_hits": int(google_hits),
                    "non_google_hits": int(non_google_hits),
                },
            }
            if self._is_memecoin_token(sym, sym, ""):
                age_minutes = float(meme_ages.get(sym) or 999999.0)
                if not self._meme_age_allowed(age_minutes):
                    continue
                if 0 < rank <= MEME_EXCLUDE_TOP_RANK_MAX:
                    continue
                row["age_minutes"] = float(age_minutes)
                row["market_cap_rank"] = int(rank)
                row["payload"]["age_minutes"] = float(age_minutes)
                row["payload"]["market_cap_rank"] = int(rank)
                rows_meme.append(row)
            else:
                if not (CRYPTO_TREND_RANK_MIN <= int(rank) <= CRYPTO_TREND_RANK_MAX):
                    continue
                row["market_cap_rank"] = int(rank)
                row["payload"]["market_cap_rank"] = int(rank)
                rows_crypto.append(row)
        rows_meme.sort(key=lambda r: (int(r["hits"]), float(r["score"]), int(r["source_count"])), reverse=True)
        rows_crypto.sort(key=lambda r: (int(r["hits"]), float(r["score"]), int(r["source_count"])), reverse=True)
        try:
            if rows_meme:
                self.runtime_feedback.append_trend_points("meme", rows_meme[:200], now_ts=now_ts)
            if rows_crypto:
                self.runtime_feedback.append_trend_points("crypto", rows_crypto[:200], now_ts=now_ts)
            if source_status:
                self.runtime_feedback.append_trend_source_status(source_status, now_ts=now_ts)
            self._emit_trend_brief_events(now_ts, rows_meme, rows_crypto)
        except Exception:
            pass

    @staticmethod
    def _infer_theme(symbols: list[str], table: dict[str, tuple[str, ...]], default_name: str) -> str:
        if not symbols:
            return default_name
        scores: dict[str, int] = {}
        upper = [str(s or "").upper().strip() for s in symbols if str(s or "").strip()]
        for theme, keywords in table.items():
            score = 0
            for sym in upper:
                for kw in keywords:
                    if str(kw or "").upper() in sym:
                        score += 1
            if score > 0:
                scores[theme] = score
        if not scores:
            return default_name
        return sorted(scores.items(), key=lambda it: it[1], reverse=True)[0][0]

    @staticmethod
    def _trend_brief_symbol(value: Any) -> str:
        return str(value or "").upper().strip()

    @classmethod
    def _meme_trend_brief_entries(cls, meta: dict[str, Any]) -> list[dict[str, Any]]:
        excluded = set(MEME_TREND_EXCLUDED_SYMBOLS)
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        top_symbol = cls._trend_brief_symbol(meta.get("top_symbol"))
        top_hits = max(1, int(meta.get("top_hits") or 1))
        if top_symbol and top_symbol not in excluded:
            out.append({"symbol": top_symbol, "hits": top_hits})
            seen.add(top_symbol)
        for raw in list(meta.get("top_symbols") or []):
            sym = cls._trend_brief_symbol(raw)
            if not sym or sym in excluded or sym in seen:
                continue
            out.append({"symbol": sym, "hits": 1})
            seen.add(sym)
        return out

    @classmethod
    def _meme_trend_brief_recent(
        cls,
        rows: list[dict[str, Any]],
        now_ts: int,
        lookback_seconds: int,
    ) -> list[dict[str, Any]]:
        cutoff = int(now_ts - max(600, int(lookback_seconds or 0)))
        out: list[dict[str, Any]] = []
        for row in list(rows or []):
            ts = int((row or {}).get("ts") or 0)
            if ts < cutoff:
                continue
            if not cls._meme_trend_brief_entries(dict((row or {}).get("meta") or {})):
                continue
            out.append(dict(row or {}))
        return out

    @staticmethod
    def _meme_trend_brief_bucket_label(ts: int, bucket_seconds: int) -> str:
        dt = datetime.fromtimestamp(int(ts), tz=timezone.utc).astimezone()
        if int(bucket_seconds) >= 86400 * 7:
            return dt.strftime("%Y-%m")
        if int(bucket_seconds) >= 86400:
            return dt.strftime("%m-%d")
        return dt.strftime("%m-%d %H:00")

    @classmethod
    def _meme_trend_brief_distribution(
        cls,
        rows: list[dict[str, Any]],
        now_ts: int,
        *,
        lookback_seconds: int = 60 * 60 * 24,
        top_n: int = 8,
    ) -> list[dict[str, Any]]:
        recent = cls._meme_trend_brief_recent(rows, now_ts, lookback_seconds)
        agg: dict[str, int] = {}
        for row in list(recent or []):
            meta = dict((row or {}).get("meta") or {})
            for item in cls._meme_trend_brief_entries(meta):
                sym = str(item.get("symbol") or "")
                agg[sym] = int(agg.get(sym, 0)) + max(1, int(item.get("hits") or 0))
        ordered = sorted(
            ({"symbol": sym, "hits": hits} for sym, hits in agg.items() if hits > 0),
            key=lambda row: int(row.get("hits") or 0),
            reverse=True,
        )
        if not ordered:
            return []
        total_hits = int(sum(int(row.get("hits") or 0) for row in ordered))
        keep_n = max(3, min(20, int(top_n or 8)))
        kept = ordered[:keep_n]
        etc_hits = int(sum(int(row.get("hits") or 0) for row in ordered[keep_n:]))
        out = [
            {
                "symbol": str(row.get("symbol") or "-"),
                "hits": int(row.get("hits") or 0),
                "share_pct": round((float(int(row.get("hits") or 0)) / float(max(1, total_hits))) * 100.0, 4),
                "total_hits": int(total_hits),
            }
            for row in kept
        ]
        if etc_hits > 0:
            out.append(
                {
                    "symbol": "ETC",
                    "hits": int(etc_hits),
                    "share_pct": round((float(etc_hits) / float(max(1, total_hits))) * 100.0, 4),
                    "total_hits": int(total_hits),
                }
            )
        return out

    @classmethod
    def _meme_trend_brief_bucket_series(
        cls,
        rows: list[dict[str, Any]],
        now_ts: int,
        *,
        lookback_seconds: int = 60 * 60 * 24,
        bucket_seconds: int = 1800,
    ) -> list[dict[str, Any]]:
        bucket = max(300, int(bucket_seconds or 1800))
        recent = cls._meme_trend_brief_recent(rows, now_ts, lookback_seconds)
        start_ts = int(now_ts - max(bucket, int(lookback_seconds or 0)))
        bucket_start = int(start_ts // bucket * bucket)
        bucket_end = int(now_ts // bucket * bucket)
        slots: dict[int, dict[str, Any]] = {}
        for ts in range(bucket_start, bucket_end + bucket, bucket):
            slots[int(ts)] = {"hits": 0, "symbol_hits": {}}
        for row in list(recent or []):
            ts = int((row or {}).get("ts") or 0)
            bts = int(ts // bucket * bucket)
            slot = dict(slots.get(bts) or {})
            if not slot:
                continue
            table = dict(slot.get("symbol_hits") or {})
            for item in cls._meme_trend_brief_entries(dict((row or {}).get("meta") or {})):
                sym = str(item.get("symbol") or "")
                hit = max(1, int(item.get("hits") or 0))
                slot["hits"] = int(slot.get("hits") or 0) + hit
                table[sym] = int(table.get(sym, 0)) + hit
            slot["symbol_hits"] = table
            slots[bts] = slot
        out: list[dict[str, Any]] = []
        for bts in sorted(slots.keys()):
            slot = dict(slots.get(bts) or {})
            table = dict(slot.get("symbol_hits") or {})
            top_symbol = ""
            top_hits = 0
            if table:
                top_symbol, top_hits = max(table.items(), key=lambda it: int(it[1]))
            out.append(
                {
                    "ts": int(bts),
                    "label": cls._meme_trend_brief_bucket_label(int(bts), bucket),
                    "hits": int(slot.get("hits") or 0),
                    "top_symbol": str(top_symbol or ""),
                    "top_hits": int(top_hits or 0),
                }
            )
        return out

    @classmethod
    def _meme_trend_brief_period_summary(
        cls,
        rows: list[dict[str, Any]],
        now_ts: int,
        *,
        bucket_seconds: int,
        lookback_seconds: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        series = cls._meme_trend_brief_bucket_series(
            rows,
            now_ts,
            lookback_seconds=lookback_seconds,
            bucket_seconds=bucket_seconds,
        )
        picked = [row for row in list(series or []) if int(row.get("hits") or 0) > 0][-max(1, int(limit or 1)) :]
        out: list[dict[str, Any]] = []
        for row in picked:
            top_symbol = str(row.get("top_symbol") or "-")
            top_hits = int(row.get("top_hits") or 0)
            out.append(
                {
                    "ts": int(row.get("ts") or 0),
                    "label": str(row.get("label") or "-"),
                    "total_hits": int(row.get("hits") or 0),
                    "top_symbol": top_symbol,
                    "top_hits": int(top_hits),
                    "breakdown_text": f"{top_symbol} {top_hits}" if top_symbol and top_symbol != "-" else "-",
                }
            )
        return out

    @classmethod
    def _meme_trend_brief_rank(
        cls,
        rows: list[dict[str, Any]],
        now_ts: int,
        *,
        lookback_seconds: int = 60 * 60 * 24,
        limit: int = 120,
        feed_rows: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        recent = cls._meme_trend_brief_recent(rows, now_ts, lookback_seconds)
        cap_map: dict[str, float] = {}
        for row in list(feed_rows or []):
            sym = cls._trend_brief_symbol((row or {}).get("symbol"))
            if not sym:
                continue
            cap_map[sym] = max(float(cap_map.get(sym) or 0.0), float((row or {}).get("market_cap_usd") or 0.0))
        agg: dict[str, dict[str, Any]] = {}
        for row in list(recent or []):
            ts = int((row or {}).get("ts") or 0)
            for item in cls._meme_trend_brief_entries(dict((row or {}).get("meta") or {})):
                sym = str(item.get("symbol") or "")
                hit = max(1, int(item.get("hits") or 0))
                slot = agg.get(sym)
                if slot is None:
                    slot = {
                        "symbol": sym,
                        "hits": 0,
                        "source_count": 0,
                        "score": 0.0,
                        "market_cap_usd": float(cap_map.get(sym) or 0.0),
                        "last_seen_ts": ts,
                    }
                    agg[sym] = slot
                slot["hits"] = int(slot.get("hits") or 0) + hit
                slot["score"] = float(slot.get("hits") or 0)
                slot["last_seen_ts"] = max(int(slot.get("last_seen_ts") or 0), ts)
                if float(cap_map.get(sym) or 0.0) > 0.0:
                    slot["market_cap_usd"] = float(cap_map.get(sym) or 0.0)
        ranked = sorted(
            list(agg.values()),
            key=lambda row: (int(row.get("hits") or 0), int(row.get("last_seen_ts") or 0)),
            reverse=True,
        )
        return ranked[: max(5, min(300, int(limit or 120)))]

    def _build_trend_brief(self, market: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        market_id = "meme" if str(market or "").lower().strip() == "meme" else "crypto"

        def _payload(row: dict[str, Any]) -> dict[str, Any]:
            payload = dict((row or {}).get("payload") or {})
            return payload if isinstance(payload, dict) else {}

        if not rows and market_id == "meme":
            with self._lock:
                feed_rows = list(self._new_meme_feed or [])
            feed_compact: list[dict[str, Any]] = []
            for feed in list(feed_rows or [])[:80]:
                sym = str((feed or {}).get("symbol") or "").upper().strip()
                if not sym or sym in MEME_TREND_EXCLUDED_SYMBOLS:
                    continue
                age_minutes = float((feed or {}).get("age_minutes") or 999999.0)
                if age_minutes > MEME_TREND_THEME_MAX_AGE_MINUTES:
                    continue
                feed_compact.append(
                    {
                        "symbol": sym,
                        "hits": max(1, int((feed or {}).get("trend_hits") or 0)),
                        "source_count": 1,
                        "score": float((feed or {}).get("trend_hits") or 0.0),
                        "market_cap_usd": float((feed or {}).get("market_cap_usd") or 0.0),
                        "age_minutes": float(age_minutes),
                        "payload": {
                            "trader_hits": 0,
                            "wallet_hits": 0,
                            "news_hits": 0,
                            "community_hits": 0,
                            "google_hits": 0,
                            "age_minutes": float(age_minutes),
                            "market_cap_rank": 0,
                        },
                    }
                )
            rows = feed_compact

        if not rows:
            return {}

        sorted_rows = sorted(
            list(rows or []),
            key=lambda r: (int(r.get("hits") or 0), float(r.get("score") or 0.0)),
            reverse=True,
        )
        if market_id == "meme":
            # Keep meme brief focused on newer/smaller meme flow instead of major memes.
            filtered_rows: list[dict[str, Any]] = []
            for row in list(sorted_rows or []):
                item = dict(row or {})
                payload = _payload(item)
                sym = str(item.get("symbol") or "").upper().strip()
                if not sym:
                    continue
                if sym in MEME_TREND_EXCLUDED_SYMBOLS:
                    continue
                age_minutes = float(item.get("age_minutes") or payload.get("age_minutes") or 999999.0)
                if age_minutes > MEME_TREND_THEME_MAX_AGE_MINUTES:
                    continue
                rank = int(item.get("market_cap_rank") or payload.get("market_cap_rank") or 0)
                if 0 < rank <= MEME_EXCLUDE_TOP_RANK_MAX:
                    continue
                filtered_rows.append(item)
            if filtered_rows:
                sorted_rows = filtered_rows
            else:
                # Fallback to new meme feed when trend rows are dominated by excluded majors.
                with self._lock:
                    feed_rows = list(self._new_meme_feed or [])
                feed_compact: list[dict[str, Any]] = []
                for feed in list(feed_rows or [])[:80]:
                    sym = str((feed or {}).get("symbol") or "").upper().strip()
                    if not sym or sym in MEME_TREND_EXCLUDED_SYMBOLS:
                        continue
                    age_minutes = float((feed or {}).get("age_minutes") or 999999.0)
                    if age_minutes > MEME_TREND_THEME_MAX_AGE_MINUTES:
                        continue
                    feed_compact.append(
                        {
                            "symbol": sym,
                            "hits": max(1, int((feed or {}).get("trend_hits") or 0)),
                            "source_count": 1,
                            "score": float((feed or {}).get("trend_hits") or 0.0),
                            "market_cap_usd": float((feed or {}).get("market_cap_usd") or 0.0),
                            "age_minutes": float(age_minutes),
                            "payload": {
                                "trader_hits": 0,
                                "wallet_hits": 0,
                                "news_hits": 0,
                                "community_hits": 0,
                                "google_hits": 0,
                                "age_minutes": float(age_minutes),
                                "market_cap_rank": 0,
                            },
                        }
                    )
                if feed_compact:
                    sorted_rows = sorted(
                        feed_compact,
                        key=lambda r: (int(r.get("hits") or 0), float(r.get("score") or 0.0)),
                        reverse=True,
                    )
                else:
                    return {}

        top = dict(sorted_rows[0] or {})
        top_symbol = str(top.get("symbol") or "-")
        top_hits = max(0, int(top.get("hits") or 0))
        symbols = [str((r or {}).get("symbol") or "").upper().strip() for r in sorted_rows[:10]]
        symbols = [s for s in symbols if s]
        prev_hits_table = dict(self._trend_prev_hits.get(market_id) or {})
        prev_hits = max(0, int(prev_hits_table.get(top_symbol) or 0))
        top_delta_hits = int(top_hits - prev_hits)
        if prev_hits > 0:
            growth_ratio = (float(top_hits) - float(prev_hits)) / float(max(1, prev_hits))
        else:
            growth_ratio = 1.0 if top_hits > 0 else 0.0
        growth_ratio = max(-1.0, min(5.0, float(growth_ratio)))
        total_hits = int(sum(max(0, int((r or {}).get("hits") or 0)) for r in sorted_rows[:20]))
        prev_total_hits = int(sum(max(0, int(prev_hits_table.get(str((r or {}).get("symbol") or "").upper().strip()) or 0)) for r in sorted_rows[:20]))
        total_delta_hits = int(total_hits - prev_total_hits)
        if prev_total_hits > 0:
            total_growth_ratio = (float(total_hits) - float(prev_total_hits)) / float(max(1, prev_total_hits))
        else:
            total_growth_ratio = 1.0 if total_hits > 0 else 0.0
        total_growth_ratio = max(-1.0, min(5.0, float(total_growth_ratio)))
        row_span = max(1, min(20, len(sorted_rows)))
        baseline_hits = float(max(8, int(total_hits / float(row_span))))
        delta_norm = float(total_delta_hits) / float(max(1.0, baseline_hits))
        if total_delta_hits >= 20 or (total_hits >= 120 and delta_norm >= 1.2):
            momentum = "급상승"
        elif total_delta_hits >= 6 or (total_hits >= 60 and delta_norm >= 0.6):
            momentum = "상승"
        elif total_delta_hits <= -20 or delta_norm <= -1.2:
            momentum = "둔화"
        else:
            momentum = "보합"
        source_avg = (
            float(sum(max(0, int((r or {}).get("source_count") or 0)) for r in sorted_rows[:10])) / float(max(1, min(10, len(sorted_rows))))
        )
        source_totals = {"trader": 0, "wallet": 0, "news": 0, "community": 0, "google": 0}
        source_wide_rows = 0
        for row in list(sorted_rows or [])[:20]:
            payload = _payload(dict(row or {}))
            trader_hits = max(0, int(payload.get("trader_hits") or 0))
            wallet_hits = max(0, int(payload.get("wallet_hits") or 0))
            news_hits = max(0, int(payload.get("news_hits") or 0))
            community_hits = max(0, int(payload.get("community_hits") or 0))
            google_hits = max(0, int(payload.get("google_hits") or 0))
            source_totals["trader"] += trader_hits
            source_totals["wallet"] += wallet_hits
            source_totals["news"] += news_hits
            source_totals["community"] += community_hits
            source_totals["google"] += google_hits
            src_cnt = int(sum(1 for v in (trader_hits, wallet_hits, news_hits, community_hits, google_hits) if v > 0))
            if src_cnt >= 2:
                source_wide_rows += 1
        source_total_hits = int(sum(int(v) for v in source_totals.values()))
        x_share_pct = float(100.0 * float(source_totals.get("trader", 0)) / float(max(1, source_total_hits)))
        source_spread_ratio = float(source_wide_rows) / float(max(1, min(20, len(sorted_rows))))

        burst_rows: list[tuple[str, int, int]] = []
        for row in list(sorted_rows or [])[:15]:
            sym = str((row or {}).get("symbol") or "").upper().strip()
            if not sym:
                continue
            cur_hits = max(0, int((row or {}).get("hits") or 0))
            prev = max(0, int(prev_hits_table.get(sym) or 0))
            delta = int(cur_hits - prev)
            if cur_hits >= 2 and (delta >= 2 or (prev == 0 and cur_hits >= 3)):
                burst_rows.append((sym, delta, cur_hits))
        burst_rows.sort(key=lambda it: (int(it[1]), int(it[2])), reverse=True)
        burst_symbols = [sym for sym, _, _ in burst_rows[:6]]

        top_source = "-"
        if source_totals:
            top_source = sorted(source_totals.items(), key=lambda it: int(it[1]), reverse=True)[0][0]
        top_symbols_text = ", ".join(symbols[:6]) if symbols else "-"
        extra_meta: dict[str, Any] = {}
        if market_id == "meme":
            theme = self._infer_theme(symbols, MEME_THEME_KEYWORDS, "혼합/신규 밈")
            market_name = "밈코인"
            newcomers_3h = int(
                sum(
                    1
                    for r in list(sorted_rows or [])[:30]
                    if float((r or {}).get("age_minutes") or _payload(dict(r or {})).get("age_minutes") or 999999.0) <= 180.0
                )
            )
            smallcap_hits = int(
                sum(1 for r in list(sorted_rows or [])[:30] if 0.0 < float((r or {}).get("market_cap_usd") or 0.0) <= MEME_SMALLCAP_MAX_USD)
            )
            hits_intensity = _clamp(math.log1p(float(total_hits)) / math.log1p(500.0), 0.0, 1.0)
            delta_intensity = _clamp((float(total_delta_hits) + 20.0) / 70.0, 0.0, 1.0)
            impact_score = _clamp(
                (0.30 * hits_intensity)
                + (0.24 * delta_intensity)
                + (0.24 * _clamp(float(len(burst_symbols)) / 5.0, 0.0, 1.0))
                + (0.12 * _clamp(float(source_spread_ratio), 0.0, 1.0))
                + (0.10 * _clamp(float(newcomers_3h) / 6.0, 0.0, 1.0)),
                0.0,
                1.0,
            )
            burst_text = ", ".join(burst_symbols[:3]) if burst_symbols else "-"
            signal = (
                f"신규/버스트 {burst_text} | 상위20 {total_hits}건(Δ{total_delta_hits:+d}) | 3h 신규 {newcomers_3h}개 | "
                f"X비중 {x_share_pct:.1f}% | 소스확산 {source_spread_ratio:.2f}"
            )
            if impact_score >= 0.70 and total_delta_hits >= 8:
                action_hint = "추세 강함: D등급 이상 재평가 우선, 과열 추격은 제한하세요."
            elif total_delta_hits <= -10:
                action_hint = "관심 둔화: 신규 진입 축소, 보유 포지션 리스크 먼저 점검하세요."
            else:
                action_hint = "중립: 버스트 심볼과 신규 유입 심볼의 체결/유동성 검증 후 선별 진입하세요."
            summary = (
                f"선두 {top_symbol}({top_hits}건, 직전 {prev_hits}건, 증감 {top_delta_hits:+d}건), "
                f"상위20 합계 {total_hits}건 (직전 {prev_total_hits}건, 증감 {total_delta_hits:+d}건), "
                f"3시간 신규 {newcomers_3h}개, 소형시총 후보 {smallcap_hits}개, "
                f"주요 소스 {top_source}, 상위심볼 {top_symbols_text}."
            )
            headline = f"[{market_name}] {theme} | {momentum} | 선두 {top_symbol} | 버스트 {burst_text}"
            extra_meta = {
                "newcomers_3h": int(newcomers_3h),
                "smallcap_count": int(smallcap_hits),
                "impact_score": float(round(impact_score, 4)),
            }
        else:
            theme = self._infer_theme(symbols, CRYPTO_THEME_KEYWORDS, "알트 혼합")
            market_name = "크립토"
            rank_rows = [int((r or {}).get("market_cap_rank") or _payload(dict(r or {})).get("market_cap_rank") or 0) for r in sorted_rows[:30]]
            rank_rows = [int(v) for v in rank_rows if int(v) > 0]
            rank_lo = int(min(rank_rows)) if rank_rows else 0
            rank_hi = int(max(rank_rows)) if rank_rows else 0
            rank_band = f"{rank_lo}~{rank_hi}" if rank_lo > 0 and rank_hi > 0 else "-"
            mid_alt = int(sum(1 for v in rank_rows if 50 <= int(v) <= 300))
            burst_text = ", ".join(burst_symbols[:3]) if burst_symbols else "-"
            signal = (
                f"이슈 알트 {burst_text} | 상위20 {total_hits}건(Δ{total_delta_hits:+d}) | 랭크대 {rank_band} | "
                f"X비중 {x_share_pct:.1f}% | 소스확산 {source_spread_ratio:.2f}"
            )
            if total_delta_hits >= 10 and source_spread_ratio >= 0.35:
                action_hint = "이슈 확산 구간: 점수 상위 알트 중심으로 진입 후보를 재정렬하세요."
            elif total_delta_hits <= -10:
                action_hint = "관심 축소 구간: 무리한 신규 진입보다 기존 포지션 관리 우선입니다."
            else:
                action_hint = "혼조 구간: 과열 추격을 줄이고 모델 임계값 이상 후보만 선별하세요."
            summary = (
                f"선두 {top_symbol}({top_hits}건, 직전 {prev_hits}건, 증감 {top_delta_hits:+d}건), "
                f"상위20 합계 {total_hits}건 (직전 {prev_total_hits}건, 증감 {total_delta_hits:+d}건), "
                f"랭크대 {rank_band}, 50~300위 후보 {mid_alt}개, 주요 소스 {top_source}, "
                f"상위심볼 {top_symbols_text}."
            )
            headline = f"[{market_name}] {theme} | {momentum} | 선두 {top_symbol} | 이슈 {burst_text}"
            extra_meta = {
                "rank_band": str(rank_band),
                "mid_alt_count": int(mid_alt),
            }
        return {
            "market": market_id,
            "theme": theme,
            "top_symbol": top_symbol,
            "top_hits": int(top_hits),
            "prev_top_hits": int(prev_hits),
            "top_hits_delta": int(top_delta_hits),
            "growth_ratio": float(growth_ratio),
            "total_growth_ratio": float(total_growth_ratio),
            "momentum": momentum,
            "signal": signal,
            "action_hint": action_hint,
            "summary": summary,
            "headline": headline,
            "top_symbols": symbols[:8],
            "burst_symbols": list(burst_symbols),
            "total_hits_top20": int(total_hits),
            "prev_total_hits_top20": int(prev_total_hits),
            "total_hits_delta_top20": int(total_delta_hits),
            "avg_source_count_top10": float(round(source_avg, 4)),
            "source_totals": dict(source_totals),
            "source_spread_ratio": float(round(source_spread_ratio, 4)),
            "x_share_pct": float(round(x_share_pct, 2)),
            **extra_meta,
        }

    def _emit_trend_brief_events(
        self,
        now_ts: int,
        rows_meme: list[dict[str, Any]],
        rows_crypto: list[dict[str, Any]],
    ) -> None:
        min_gap = max(600, int(self.settings.scan_interval_seconds) * 10)
        if (int(now_ts) - int(self._last_trend_brief_emit_ts)) < int(min_gap):
            return
        self._last_trend_brief_emit_ts = int(now_ts)
        briefs = {
            "meme": self._build_trend_brief("meme", rows_meme),
            "crypto": self._build_trend_brief("crypto", rows_crypto),
        }
        for market_id, brief in briefs.items():
            if not brief:
                continue
            try:
                self.runtime_feedback.append_event(
                    source=f"trend_brief_{market_id}",
                    level="info",
                    status="snapshot",
                    detail=str(brief.get("headline") or ""),
                    meta=dict(brief),
                    now_ts=now_ts,
                )
            except Exception:
                pass
        self._trend_prev_hits["meme"] = {
            str((r or {}).get("symbol") or "").upper().strip(): int((r or {}).get("hits") or 0) for r in list(rows_meme or [])[:80]
        }
        self._trend_prev_hits["crypto"] = {
            str((r or {}).get("symbol") or "").upper().strip(): int((r or {}).get("hits") or 0) for r in list(rows_crypto or [])[:80]
        }

    def _build_telegram_periodic_report_demo(self) -> str:
        with self._lock:
            runs = dict(self.state.model_runs or {})
            demo_seed = float(self.state.demo_seed_usdt or self.settings.demo_seed_usdt)
        now_ts = int(time.time())
        ts_text = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
        mode_text = str(self.settings.trade_mode or "paper").upper()
        auto_text = "ON" if bool(self.settings.enable_autotrade) else "OFF"

        def _sgn(v: float) -> str:
            return f"{float(v):+.2f}"

        meme_engine = self._aggregate_meme_engine_state(runs, mode_filter="paper", now_ts=now_ts)
        lines: list[str] = [
            f"[10분 리포트][DEMO] {ts_text}",
            f"상태: {'RUNNING' if self.running else 'STOPPED'} | 모드: {mode_text} | 자동매매: {auto_text}",
            f"데모 시드: {demo_seed:.0f} USDT",
            "",
            "[DEMO 밈 엔진]",
            f"- 구조: THEME_SNIPER 메인 / NARRATIVE 서브",
            f"- 오픈 {int(meme_engine.get('open_positions') or 0)} | 실현PNL {_sgn(float(meme_engine.get('realized_pnl_usd') or 0.0))} | "
            f"평가손익 {_sgn(float(meme_engine.get('unrealized_pnl_usd') or 0.0))} | 총손익 {_sgn(float(meme_engine.get('total_pnl_usd') or 0.0))}",
            f"- 청산 {int(meme_engine.get('closed_trades') or 0)} | 승률 {float(meme_engine.get('win_rate') or 0.0):.1f}%",
        ]
        top_signal = dict(meme_engine.get("top_signal") or {})
        if top_signal:
            lines.append(
                f"- 상위 신호: {str(top_signal.get('symbol') or '-')} | "
                f"{str(top_signal.get('strategy_id') or '-')} | "
                f"{str(top_signal.get('grade') or '-')} {float(top_signal.get('score') or 0.0):.4f}"
            )
        recent_alloc = dict(meme_engine.get("recent_allocations") or {})
        if recent_alloc:
            alloc_text = " | ".join(f"{k} {v}" for k, v in recent_alloc.items())
            lines.append(f"- 최근 진입: {alloc_text}")

        lines.append("")
        lines.append("[DEMO 크립토 4모델 순위]")
        ranked_crypto: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        for model_id in CRYPTO_MODEL_IDS:
            crypto_run = self._get_market_run(runs, "crypto", model_id)
            cm = self._model_metrics_market(model_id, crypto_run, "crypto")
            crypto_name = self._market_model_name("crypto", model_id)
            ranked_crypto.append((crypto_name, cm, crypto_run))
        ranked_crypto.sort(key=lambda row: (float((row[1] or {}).get("total_pnl_usd") or 0.0), float((row[1] or {}).get("win_rate") or 0.0)), reverse=True)
        for rank, (crypto_name, cm, crypto_run) in enumerate(ranked_crypto, start=1):
            lines.append(
                f"#{rank} {crypto_name}: PNL {_sgn(float(cm.get('total_pnl_usd') or 0.0))} | "
                f"OPEN {int(cm.get('open_positions') or 0)} | 최근진입 "
                f"{self._fmt_last_entry_alloc(dict((crypto_run.get('last_entry_alloc') or {}).get('crypto') or {}), now_ts)}"
            )

        lines.append("")
        lines.append(f"[자동튜닝 {self._autotune_interval_label()}]")
        for model_id in CRYPTO_MODEL_IDS:
            crypto_run = self._get_market_run(runs, "crypto", model_id)
            tune = self._read_model_runtime_tune_from_run(crypto_run or {}, model_id, now_ts)
            remain = max(0, int(tune.get("next_eval_ts") or 0) - now_ts)
            core_name = self._market_model_name("crypto", model_id)
            lines.append(
                f"- {core_name}: next {remain // 60}m | thr {float(tune['threshold']):.4f} | "
                f"tp {float(tune['tp_mul']):.2f} | sl {float(tune['sl_mul']):.2f}"
            )
            if int(tune.get("last_eval_ts") or 0) > 0:
                note_text = str(tune.get("last_eval_note_ko") or self._autotune_note_ko(str(tune.get("last_eval_note") or "")) or "-")
                lines.append(
                    f"  · 최근평가: closed {int(tune['last_eval_closed'])}, wr {float(tune['last_eval_win_rate']):.1f}%, "
                    f"pnl {_sgn(float(tune['last_eval_pnl_usd']))}, pf {float(tune['last_eval_pf']):.2f}, "
                    f"결과 {note_text} | variant {str(tune.get('active_variant_id') or '-')}"
                )
        return "\n".join(lines)

    def _aggregate_meme_engine_state(
        self,
        runs: dict[str, Any],
        mode_filter: str = "paper",
        now_ts: int | None = None,
    ) -> dict[str, Any]:
        now = int(now_ts or int(time.time()))
        positions: list[dict[str, Any]] = []
        trades: list[dict[str, Any]] = []
        recent_allocations: dict[str, str] = {}
        signal_map: dict[str, dict[str, Any]] = {}

        for model_id in MEME_MODEL_IDS:
            strategy_id = self._meme_strategy_id_for_model(model_id)
            strategy_name = self._meme_strategy_name(strategy_id)
            run = self._get_market_run(runs, "meme", model_id)
            alloc_row = dict((run.get("last_entry_alloc") or {}).get("meme") or {})
            if alloc_row:
                recent_allocations[strategy_id] = self._fmt_last_entry_alloc(alloc_row, now)

            for row in self._build_meme_positions_view(run, mode_filter=mode_filter):
                item = dict(row or {})
                item_strategy_id = str(item.get("engine_strategy_id") or strategy_id or "THEME").upper().strip() or "THEME"
                item["strategy_id"] = str(item_strategy_id)
                item["strategy_name"] = str(self._meme_strategy_name(item_strategy_id))
                positions.append(item)

            for raw in list(run.get("trades") or []):
                if str((raw or {}).get("source") or "").strip().lower() != "memecoin":
                    continue
                is_live = self._is_live_trade_row(raw)
                if mode_filter == "live":
                    if not is_live:
                        continue
                    if str((raw or {}).get("side") or "").strip().lower() == "sell" and not self._live_trade_is_realized(raw):
                        continue
                    pnl_usd = self._live_trade_realized_pnl_usd(raw) if str((raw or {}).get("side") or "").strip().lower() == "sell" else None
                else:
                    if is_live:
                        continue
                    pnl_usd = float((raw or {}).get("pnl_usd") or 0.0) if str((raw or {}).get("side") or "").strip().lower() == "sell" else None
                item = dict(raw or {})
                item_strategy_id = str(
                    item.get("strategy_id")
                    or item.get("engine_strategy_id")
                    or strategy_id
                    or "THEME"
                ).upper().strip() or "THEME"
                item["strategy_id"] = str(item_strategy_id)
                item["strategy_name"] = str(self._meme_strategy_name(item_strategy_id))
                item["pnl_usd"] = pnl_usd
                trades.append(item)

            for raw in list(run.get("latest_signals") or []):
                item = self._enrich_meme_score_row(dict(raw or {}), model_id)
                item_strategy_id = self._meme_strategy_id_from_signal_context(
                    features=dict(item.get("features") or {}),
                    reason=str(item.get("reason") or ""),
                    current_strategy_id=str(item.get("strategy_id") or strategy_id or "THEME"),
                )
                item["strategy_id"] = str(item_strategy_id)
                item["strategy_name"] = str(self._meme_strategy_name(item_strategy_id))
                key = str(item.get("token_address") or item.get("symbol") or "").upper().strip()
                if not key:
                    continue
                prev = signal_map.get(key)
                if prev is None or float(item.get("score") or 0.0) > float(prev.get("score") or 0.0):
                    signal_map[key] = item

        positions.sort(key=lambda row: float(row.get("value_usd") or 0.0), reverse=True)
        trades.sort(key=lambda row: int((row or {}).get("ts") or 0), reverse=True)
        signals = sorted(signal_map.values(), key=lambda row: float(row.get("score") or 0.0), reverse=True)
        sells = [row for row in trades if str((row or {}).get("side") or "").strip().lower() == "sell" and row.get("pnl_usd") is not None]
        realized = float(sum(float((row or {}).get("pnl_usd") or 0.0) for row in sells))
        unrealized = float(sum(float((row or {}).get("pnl_usd") or 0.0) for row in positions))
        closed = int(len(sells))
        wins = int(sum(1 for row in sells if float((row or {}).get("pnl_usd") or 0.0) > 0.0))
        win_rate = (float(wins) / float(closed) * 100.0) if closed > 0 else 0.0
        metric_rows = [
            self._model_metrics_market(model_id, self._get_market_run(runs, "meme", model_id), "meme", mode_filter=mode_filter)
            for model_id in MEME_MODEL_IDS
        ]
        seed_usd = float(sum(float((row or {}).get("seed_usd") or 0.0) for row in metric_rows))
        equity_usd = float(sum(float((row or {}).get("equity_usd") or 0.0) for row in metric_rows))
        aggregate_total_pnl_usd = float(sum(float((row or {}).get("total_pnl_usd") or 0.0) for row in metric_rows))
        aggregate_closed = float(sum(float((row or {}).get("closed_trades") or 0.0) for row in metric_rows))
        aggregate_win_weight = float(
            sum(float((row or {}).get("win_rate") or 0.0) * float((row or {}).get("closed_trades") or 0.0) for row in metric_rows)
        )
        aggregate_roi_pct = (aggregate_total_pnl_usd / seed_usd * 100.0) if seed_usd > 0.0 else 0.0
        return {
            "positions": positions,
            "trades": trades,
            "signals": signals,
            "open_positions": int(len(positions)),
            "closed_trades": int(closed),
            "win_rate": float(win_rate),
            "realized_pnl_usd": float(realized),
            "unrealized_pnl_usd": float(unrealized),
            "total_pnl_usd": float(realized + unrealized),
            "seed_usd": float(seed_usd),
            "equity_usd": float(equity_usd),
            "aggregate_total_pnl_usd": float(aggregate_total_pnl_usd),
            "aggregate_roi_pct": float(aggregate_roi_pct),
            "aggregate_win_rate": float((aggregate_win_weight / aggregate_closed) if aggregate_closed > 0.0 else 0.0),
            "top_signal": dict(signals[0]) if signals else {},
            "recent_allocations": dict(recent_allocations),
        }

    def _build_telegram_periodic_report_live(self) -> str:
        with self._lock:
            runs = dict(self.state.model_runs or {})
            wallet_assets = list(self.state.wallet_assets or [])
            bybit_assets = list(self.state.bybit_assets or [])
            live_seed_saved = float(self.state.live_seed_usd or 0.0)
            live_seed_set_ts = int(self.state.live_seed_set_ts or 0)
            live_perf_anchor_saved = float(getattr(self.state, "live_perf_anchor_usd", 0.0) or 0.0)
            live_net_flow_saved = float(getattr(self.state, "live_net_flow_usd", 0.0) or 0.0)
        now_ts = int(time.time())
        ts_text = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
        mode_text = str(self.settings.trade_mode or "paper").upper()
        live_exec = bool(self.settings.enable_live_execution)
        live_meme_on = bool(self.settings.live_enable_meme)
        live_crypto_on = bool(self.settings.live_enable_crypto)

        def _sgn(v: float) -> str:
            return f"{float(v):+.2f}"

        def _last_live_trade_ts(run: dict[str, Any], market: str) -> int:
            src = "memecoin" if str(market) == "meme" else "crypto_demo"
            rows = list(run.get("trades") or [])
            for row in reversed(rows):
                if not self._is_live_trade_row(row):
                    continue
                if str((row or {}).get("source") or "").strip().lower() != src:
                    continue
                return int((row or {}).get("ts") or 0)
            return 0

        def _is_live_meme_trade(row: dict[str, Any]) -> bool:
            if not self._is_live_trade_row(row):
                return False
            return str((row or {}).get("source") or "").strip().lower() == "memecoin"

        def _realized_trade_pnl(row: dict[str, Any]) -> float | None:
            return self._live_trade_realized_pnl_usd(row)

        def _fmt_ts(unix_ts: int) -> str:
            t = int(unix_ts or 0)
            if t <= 0:
                return "-"
            try:
                return datetime.fromtimestamp(t, tz=timezone.utc).astimezone().strftime("%m-%d %H:%M")
            except Exception:
                return "-"

        def _sgn_pct(v: float) -> str:
            return f"{float(v):+.2f}%"

        wallet_total = sum(float(r.get("value_usd") or 0.0) for r in wallet_assets)
        bybit_total = sum(float(r.get("usd_value") or 0.0) for r in bybit_assets)
        live_equity = self._live_equity_usd_from_assets(wallet_assets, bybit_assets)
        live_perf_anchor = float(live_perf_anchor_saved) if float(live_perf_anchor_saved) > 0.0 else float(live_equity)
        live_seed = float(live_seed_saved) if float(live_seed_saved) > 0.0 else float(live_perf_anchor)
        live_net_flow = float(live_net_flow_saved)
        live_adj_equity = float(live_equity - live_net_flow)
        live_perf_pnl = float(live_adj_equity - live_perf_anchor)
        live_perf_roi = (live_perf_pnl / max(1e-9, live_perf_anchor)) * 100.0 if live_perf_anchor > 0.0 else 0.0
        live_pnl = float(live_perf_pnl)
        live_roi = float(live_perf_roi)
        session_cut_ts = int(live_seed_set_ts) if int(live_seed_set_ts) > 0 else 0
        basis_map = dict((runs.get("_live_meme_basis") or {}))
        live_meme_wallet_rows: list[dict[str, Any]] = []
        for row in list(wallet_assets or []):
            symbol = str((row or {}).get("symbol") or "").upper().strip()
            name = str((row or {}).get("name") or "").strip()
            token = str((row or {}).get("token_address") or "").strip()
            qty = float((row or {}).get("qty") or 0.0)
            price = float((row or {}).get("price_usd") or 0.0)
            value = float((row or {}).get("value_usd") or 0.0)
            if qty <= 0.0 or value < float(self.settings.min_wallet_asset_usd or 1.0):
                continue
            if not self._is_memecoin_token(symbol, name, token):
                continue
            basis = dict(basis_map.get(token) or {})
            entry_price = float(basis.get("entry_price_usd") or 0.0)
            cost_basis = (entry_price * qty) if entry_price > 0.0 else 0.0
            pnl_usd = (value - cost_basis) if cost_basis > 0.0 else 0.0
            pnl_pct = ((pnl_usd / max(cost_basis, 1e-9)) * 100.0) if cost_basis > 0.0 else 0.0
            live_meme_wallet_rows.append(
                {
                    "symbol": symbol,
                    "value_usd": float(value),
                    "pnl_usd": float(pnl_usd),
                    "pnl_pct": float(pnl_pct),
                    "has_basis": bool(cost_basis > 0.0),
                }
            )
        live_meme_wallet_rows.sort(key=lambda r: float(r.get("value_usd") or 0.0), reverse=True)

        live_meme_engine = self._aggregate_meme_engine_state(runs, mode_filter="live", now_ts=now_ts)
        recent_live_trades: list[dict[str, Any]] = []
        for tr in list(live_meme_engine.get("trades") or []):
            ts = int((tr or {}).get("ts") or 0)
            if session_cut_ts > 0 and ts < session_cut_ts:
                continue
            realized_pnl_usd = _realized_trade_pnl(tr)
            recent_live_trades.append(
                {
                    "ts": int(ts),
                    "strategy_id": str((tr or {}).get("strategy_id") or "-"),
                    "strategy_name": str((tr or {}).get("strategy_name") or "-"),
                    "side": str((tr or {}).get("side") or "").lower(),
                    "symbol": str((tr or {}).get("symbol") or ""),
                    "notional_usd": float((tr or {}).get("notional_usd") or 0.0),
                    "pnl_usd": float(realized_pnl_usd) if realized_pnl_usd is not None else None,
                }
            )
        recent_live_trades.sort(key=lambda r: int(r.get("ts") or 0), reverse=True)

        crypto_live_models = list(self._live_model_ids("crypto"))
        meme_live_text = "단일 엔진" if live_meme_on else "OFF(시장비활성)"
        crypto_live_text = (
            ", ".join(f"{mid}:{self._market_model_name('crypto', mid)}" for mid in crypto_live_models)
            if (live_crypto_on and crypto_live_models)
            else ("ON(모델미설정)" if live_crypto_on else "OFF(시장비활성)")
        )

        lines: list[str] = [
            f"[10분 리포트][LIVE] {ts_text}",
            (
                f"상태: {'RUNNING' if self.running else 'STOPPED'} | "
                f"실전실행: {'ON' if live_exec else 'OFF'} | "
                f"실전시장(밈/크립토): {'ON' if live_meme_on else 'OFF'} / {'ON' if live_crypto_on else 'OFF'} | "
                f"모드: {mode_text}"
            ),
            f"실전 모델(밈): {meme_live_text}",
            f"실전 모델(크립토): {crypto_live_text}",
            (
                f"실전 평가금액 ${live_equity:.2f} | 성과기준 ${live_perf_anchor:.2f} | "
                f"보정 PNL {_sgn(live_perf_pnl)} ({_sgn_pct(live_perf_roi)})"
            ),
            f"순입출금 보정: {_sgn(live_net_flow)} USD | 보정 평가금액 ${live_adj_equity:.2f}",
            f"시드 동기화 시각: {_fmt_ts(live_seed_set_ts)}",
        ]

        if not live_exec:
            lines.append("주의: 실전실행이 OFF입니다. LIVE 모드 전환 후 체결됩니다.")

        lines.append("")
        lines.append("[LIVE 밈 엔진]")
        if not live_meme_on:
            lines.append("- OFF")
        else:
            last_ts = max([int(tr.get("ts") or 0) for tr in recent_live_trades], default=0)
            lines.append("- 구조: THEME_SNIPER 메인 / NARRATIVE 서브")
            lines.append(
                f"- 세션 실현PNL {_sgn(float(live_meme_engine.get('realized_pnl_usd') or 0.0))} | "
                f"평가손익 {_sgn(float(live_meme_engine.get('unrealized_pnl_usd') or 0.0))} | "
                f"총손익 {_sgn(float(live_meme_engine.get('total_pnl_usd') or 0.0))}"
            )
            lines.append(
                f"- OPEN {int(live_meme_engine.get('open_positions') or 0)} | "
                f"CLOSED {int(live_meme_engine.get('closed_trades') or 0)} | "
                f"WIN {float(live_meme_engine.get('win_rate') or 0.0):.1f}% | "
                f"최근체결 {_fmt_ts(last_ts)}"
            )
            top_signal = dict(live_meme_engine.get("top_signal") or {})
            if top_signal:
                lines.append(
                    f"- 상위 신호: {str(top_signal.get('symbol') or '-')} | "
                    f"{str(top_signal.get('strategy_id') or '-')} | "
                    f"{str(top_signal.get('grade') or '-')} {float(top_signal.get('score') or 0.0):.4f}"
                )

        lines.append("")
        lines.append("[LIVE 밈 실자산 TOP]")
        if not live_meme_wallet_rows:
            lines.append("- 표시 가능한 밈 실자산 없음")
        else:
            for row in live_meme_wallet_rows[:6]:
                if bool(row.get("has_basis")):
                    lines.append(
                        f"- {row['symbol']}: ${float(row['value_usd']):.2f} | "
                        f"PNL {_sgn(float(row['pnl_usd']))} ({_sgn_pct(float(row['pnl_pct']))})"
                    )
                else:
                    lines.append(f"- {row['symbol']}: ${float(row['value_usd']):.2f} | PNL 기준가 미확정")

        lines.append("")
        lines.append("[최근 LIVE 밈 체결]")
        if not recent_live_trades:
            lines.append("- 최근 체결 없음")
        else:
            for tr in recent_live_trades[:6]:
                side = "매수" if str(tr.get("side") or "").lower() == "buy" else "매도"
                pnl_text = _sgn(float(tr.get("pnl_usd") or 0.0)) if tr.get("pnl_usd") is not None else "-"
                lines.append(
                    f"- {_fmt_ts(int(tr.get('ts') or 0))} | {tr.get('strategy_id')} | "
                    f"{side} {tr.get('symbol')} ${float(tr.get('notional_usd') or 0.0):.2f} | PNL {pnl_text}"
                )

        lines.append("")
        lines.append("[LIVE 크립토 활성 모델]")
        if not live_crypto_on:
            lines.append("- OFF")
        else:
            crypto_ids = list(self._live_model_ids("crypto"))
            if not crypto_ids:
                lines.append("- 설정된 실전 모델 없음")
            for model_id in crypto_ids:
                run = self._get_market_run(runs, "crypto", model_id)
                cm = self._model_metrics_market(model_id, run, "crypto", mode_filter="live")
                last_ts = _last_live_trade_ts(run, "crypto")
                lines.append(
                    f"- {self._market_model_name('crypto', model_id)}: "
                    f"PNL {_sgn(float(cm.get('total_pnl_usd') or 0.0))} | "
                    f"OPEN {int(cm.get('open_positions') or 0)} | "
                    f"WIN {float(cm.get('win_rate') or 0.0):.1f}% | "
                    f"최근체결 {_fmt_ts(last_ts)}"
                )

        sol_budget = self._solana_trade_budget()
        lines.append("")
        lines.append(f"실전 팬텀 잔고(USD>=1): ${wallet_total:.2f}")
        lines.append(
            "SOL 최소유지/가용(제외 후): "
            f"{float(sol_budget.get('reserve_sol') or 0.0):.4f} / "
            f"{float(sol_budget.get('tradeable_sol') or 0.0):.6f} SOL "
            f"(~${float(sol_budget.get('tradeable_usd') or 0.0):.2f})"
        )
        lines.append(f"실전 거래소 잔고: ${bybit_total:.2f}")
        return "\n".join(lines)

    def _build_telegram_periodic_report(self) -> str:
        demo_text = self._build_telegram_periodic_report_demo()
        live_text = self._build_telegram_periodic_report_live()
        return f"{demo_text}\n\n{live_text}"

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
            for model_id in CRYPTO_MODEL_IDS:
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
                should_tune = False
                if closed < MODEL_AUTOTUNE_MIN_CLOSED_TRADES:
                    note = "hold_not_enough_samples"
                else:
                    if self._autotune_should_tune(model_id, pnl, win_rate, pf):
                        should_tune = True
                        if model_id == "A":
                            new_thr += 0.003
                            new_tp -= 0.03
                            new_sl += 0.04
                            note = "range_reversion_defensive"
                        elif model_id == "B":
                            new_thr += 0.004
                            new_tp -= 0.03
                            new_sl += 0.04
                            note = "reclaim_defensive"
                        elif model_id == "C":
                            new_thr += 0.005
                            new_tp -= 0.06
                            new_sl += 0.05
                            note = "breakout_risk_off"
                        else:
                            new_thr += 0.003
                            new_tp -= 0.03
                            new_sl += 0.03
                            note = "reset_bounce_defensive"
                    else:
                        note = "hold_good_pnl"

                clamps = self._model_tune_clamps(model_id)
                new_thr = _clamp(new_thr, float(clamps["threshold"][0]), float(clamps["threshold"][1]))
                new_tp = _clamp(new_tp, float(clamps["tp_mul"][0]), float(clamps["tp_mul"][1]))
                new_sl = _clamp(new_sl, float(clamps["sl_mul"][0]), float(clamps["sl_mul"][1]))
                tuned = bool(
                    should_tune
                    and (
                        abs(float(new_thr) - float(old_thr)) > 1e-12
                        or abs(float(new_tp) - float(old_tp)) > 1e-12
                        or abs(float(new_sl) - float(old_sl)) > 1e-12
                    )
                )
                if should_tune and not tuned:
                    note = "hold_clamp_limit"

                parent_variant_id = str(run.get("active_variant_id") or f"{model_id}-BASE")
                variant_seq = int(run.get("variant_seq") or 0)
                variant_id = parent_variant_id
                if tuned:
                    variant_seq += 1
                    variant_id = f"{model_id}-T{variant_seq:03d}"
                    run["variant_seq"] = int(variant_seq)
                    run["active_variant_id"] = str(variant_id)
                    vh = list(run.get("variant_history") or [])
                    vh.append(
                        {
                            "ts": int(now),
                            "variant_id": str(variant_id),
                            "parent_variant_id": str(parent_variant_id),
                            "note_code": str(note),
                            "note_ko": self._autotune_note_ko(note),
                            "threshold_before": float(old_thr),
                            "threshold_after": float(new_thr),
                            "tp_mul_before": float(old_tp),
                            "tp_mul_after": float(new_tp),
                            "sl_mul_before": float(old_sl),
                            "sl_mul_after": float(new_sl),
                            "closed_trades": int(closed),
                            "win_rate": float(win_rate),
                            "pnl_usd": float(pnl),
                            "profit_factor": float(pf),
                        }
                    )
                    run["variant_history"] = vh[-2000:]
                else:
                    run.setdefault("variant_seq", int(variant_seq))
                    run.setdefault("active_variant_id", str(parent_variant_id))
                tune.update(
                    {
                        "threshold": float(new_thr),
                        "tp_mul": float(new_tp),
                        "sl_mul": float(new_sl),
                        "last_eval_ts": int(now),
                        "next_eval_ts": int(now + self._autotune_interval_seconds()),
                        "last_eval_closed": int(closed),
                        "last_eval_win_rate": round(float(win_rate), 4),
                        "last_eval_pnl_usd": round(float(pnl), 6),
                        "last_eval_pf": round(float(pf), 6),
                        "last_eval_note": str(note),
                        "last_eval_note_ko": self._autotune_note_ko(note),
                        "active_variant_id": str(variant_id),
                        "variant_seq": int(run.get("variant_seq") or 0),
                    }
                )
                all_raw = dict(run.get("model_runtime_tune") or {})
                all_raw[model_id] = dict(tune)
                run["model_runtime_tune"] = dict(all_raw)
                if model_id == "B":
                    run["b_runtime_tune"] = dict(tune)
                core_name = self._display_model_name(model_id)
                try:
                    self.runtime_feedback.append_model_tune_event(
                        {
                            "market": "crypto",
                            "model_id": model_id,
                            "model_name": self._market_model_name("crypto", model_id),
                            "variant_id": str(variant_id),
                            "parent_variant_id": str(parent_variant_id),
                            "tuned": bool(tuned),
                            "note_code": str(note),
                            "note_ko": self._autotune_note_ko(note),
                            "closed_trades": int(closed),
                            "win_rate": float(win_rate),
                            "pnl_usd": float(pnl),
                            "profit_factor": float(pf),
                            "threshold_before": float(old_thr),
                            "threshold_after": float(new_thr),
                            "tp_mul_before": float(old_tp),
                            "tp_mul_after": float(new_tp),
                            "sl_mul_before": float(old_sl),
                            "sl_mul_after": float(new_sl),
                        },
                        now_ts=now,
                    )
                except Exception:
                    pass
                state_text = "튜닝적용" if tuned else "유지"
                alert_lines.append(
                    f"[{core_name}] {state_text}({self._autotune_note_ko(note)}) | "
                    f"closed={closed} wr={win_rate:.1f}% pnl={pnl:+.2f} pf={pf:.2f} | "
                    f"variant {parent_variant_id}->{variant_id} | "
                    f"thr {old_thr:.4f}->{new_thr:.4f} tp_mul {old_tp:.2f}->{new_tp:.2f} sl_mul {old_sl:.2f}->{new_sl:.2f}"
                )
        if alert_lines:
            self._push_alert(
                "info",
                f"모델 {self._autotune_interval_label()} 자동튜닝",
                "\n".join(alert_lines),
                send_telegram=True,
            )

    def _scan_drawdown_rows(self) -> list[dict[str, Any]]:
        with self._lock:
            runs = dict(self.state.model_runs or {})
        out: list[dict[str, Any]] = []
        for market in ("meme", "crypto"):
            for model_id in self._market_model_ids(market):
                run = self._get_market_run(runs, market, model_id)
                mm = self._model_metrics_market(model_id, run, market)
                seed = float(mm.get("seed_usd") or 0.0)
                equity = float(mm.get("equity_usd") or 0.0)
                if seed <= 0.0:
                    continue
                ratio = equity / seed
                drawdown = 1.0 - ratio
                out.append(
                    {
                        "market": market,
                        "model_id": model_id,
                        "model_name": self._market_model_name(market, model_id),
                        "seed_usd": seed,
                        "equity_usd": equity,
                        "equity_ratio": float(ratio),
                        "drawdown_ratio": float(drawdown),
                    }
                )
        return out

    def _record_rebuild_required_models(self, now_ts: int, triggers: list[dict[str, Any]]) -> list[dict[str, Any]]:
        now = int(now_ts)
        normalized: list[dict[str, Any]] = []
        for row in list(triggers or []):
            market = "meme" if str(row.get("market") or "").lower().strip() == "meme" else "crypto"
            model_id = str(row.get("model_id") or "").upper().strip()
            if model_id not in set(self._market_model_ids(market)):
                continue
            seed = float(row.get("seed_usd") or 0.0)
            equity = float(row.get("equity_usd") or 0.0)
            ratio = float(row.get("equity_ratio") or 0.0)
            dd = float(row.get("drawdown_ratio") or (1.0 - ratio))
            if seed <= 0.0 or dd < float(LOSS_GUARD_DRAWDOWN_RATIO):
                continue
            normalized.append(
                {
                    "market": market,
                    "model_id": model_id,
                    "model_name": str(row.get("model_name") or self._market_model_name(market, model_id)),
                    "seed_usd": float(seed),
                    "equity_usd": float(equity),
                    "equity_ratio": float(ratio),
                    "drawdown_ratio": float(dd),
                }
            )
        if not normalized:
            return []

        newly_flagged: list[dict[str, Any]] = []
        with self._lock:
            runs = dict(self.state.model_runs or {})
            watch_map = dict(runs.get("_model_rebuild_watch") or {})
            for row in normalized:
                key = f"{row['market']}_{row['model_id']}"
                prev = dict(watch_map.get(key) or {})
                first_ts = int(prev.get("first_breach_ts") or now)
                min_ratio_prev = float(prev.get("min_equity_ratio") or 1.0)
                min_ratio = min(min_ratio_prev, float(row["equity_ratio"]))
                item = {
                    "market": str(row["market"]),
                    "model_id": str(row["model_id"]),
                    "model_name": str(row["model_name"]),
                    "seed_usd": float(row["seed_usd"]),
                    "latest_equity_usd": float(row["equity_usd"]),
                    "latest_equity_ratio": float(row["equity_ratio"]),
                    "latest_drawdown_ratio": float(row["drawdown_ratio"]),
                    "min_equity_ratio": float(min_ratio),
                    "min_equity_usd": float(row["seed_usd"] * min_ratio),
                    "first_breach_ts": int(first_ts),
                    "last_breach_ts": int(now),
                    "rebuild_required": True,
                    "retune_scope": "full_direction_change",
                    "retune_status": str(prev.get("retune_status") or "pending"),
                }
                if not prev:
                    newly_flagged.append(dict(item))
                watch_map[key] = item
            runs["_model_rebuild_watch"] = watch_map
            self.state.model_runs = runs

        for row in newly_flagged:
            try:
                self.runtime_feedback.append_event(
                    source="model_rebuild_watch",
                    level="warn",
                    status="required",
                    error="",
                    action="full_direction_retune_required",
                    detail=(
                        f"{row['model_name']} 시드 50% 하회 기록 | "
                        f"seed={float(row['seed_usd']):.2f} equity={float(row['latest_equity_usd']):.2f} "
                        f"dd={float(row['latest_drawdown_ratio']) * 100:.1f}%"
                    ),
                    meta={
                        "market": row["market"],
                        "model_id": row["model_id"],
                        "model_name": row["model_name"],
                        "seed_usd": float(row["seed_usd"]),
                        "equity_usd": float(row["latest_equity_usd"]),
                        "drawdown_ratio": float(row["latest_drawdown_ratio"]),
                        "threshold": float(LOSS_GUARD_DRAWDOWN_RATIO),
                        "retune_scope": "full_direction_change",
                    },
                    now_ts=now,
                )
            except Exception:
                pass
        return newly_flagged

    def _apply_loss_guard_rewrite(self, now_ts: int, triggers: list[dict[str, Any]]) -> dict[str, Any]:
        current_min = float(getattr(self.settings, "crypto_min_entry_score", 0.30) or 0.30)
        current_order_min = float(self.settings.demo_order_pct_min or 0.15)
        current_order_max = float(self.settings.demo_order_pct_max or 0.30)
        current_bybit_pos = int(self.settings.bybit_max_positions or 4)
        current_meme_pos = int(self.settings.meme_max_positions or 4)
        current_rank_max = int(getattr(self.settings, "macro_rank_max", 300) or 300)
        updates: dict[str, Any] = {
            "CRYPTO_MIN_ENTRY_SCORE": round(_clamp(current_min + 0.05, 0.30, 0.70), 4),
            "DEMO_ORDER_PCT_MIN": round(_clamp(min(current_order_min, 0.12), 0.05, 0.50), 4),
            "DEMO_ORDER_PCT_MAX": round(_clamp(min(current_order_max, 0.20), 0.08, 0.60), 4),
            "BYBIT_MAX_POSITIONS": int(max(1, min(current_bybit_pos, 2))),
            "MEME_MAX_POSITIONS": int(max(1, min(current_meme_pos, 3))),
            "MACRO_RANK_MAX": int(max(300, min(current_rank_max, 300))),
        }
        if float(updates["DEMO_ORDER_PCT_MAX"]) < float(updates["DEMO_ORDER_PCT_MIN"]):
            updates["DEMO_ORDER_PCT_MAX"] = float(updates["DEMO_ORDER_PCT_MIN"])
        save_runtime_overrides(self.settings, updates)
        self._reload_settings()

        with self._lock:
            runs = self.state.model_runs or {}
            for market in ("meme", "crypto"):
                for model_id in self._market_model_ids(market):
                    key = self._market_run_key(market, model_id)
                    run = runs.get(key)
                    if not isinstance(run, dict):
                        continue
                    if market == "meme":
                        run["loss_guard"] = {
                            "active": True,
                            "threshold_boost": 0.020,
                            "order_mul": 0.50,
                            "reason": "drawdown_50pct",
                            "trigger_ts": int(now_ts),
                        }
                    else:
                        run["loss_guard"] = {
                            "active": True,
                            "threshold_boost": 0.012,
                            "order_mul": 0.55,
                            "reason": "drawdown_50pct",
                            "trigger_ts": int(now_ts),
                        }
                        tune = self._ensure_model_runtime_tune(run, model_id, int(now_ts))
                        clamps = self._model_tune_clamps(model_id)
                        tune["threshold"] = _clamp(
                            max(float(tune.get("threshold") or 0.0), float(clamps["threshold"][0]) + 0.004),
                            float(clamps["threshold"][0]),
                            float(clamps["threshold"][1]),
                        )
                        tune["tp_mul"] = _clamp(
                            float(tune.get("tp_mul") or 1.0) * 0.96,
                            float(clamps["tp_mul"][0]),
                            float(clamps["tp_mul"][1]),
                        )
                        tune["sl_mul"] = _clamp(
                            float(tune.get("sl_mul") or 1.0) * 0.88,
                            float(clamps["sl_mul"][0]),
                            float(clamps["sl_mul"][1]),
                        )
                        all_raw = dict(run.get("model_runtime_tune") or {})
                        all_raw[model_id] = dict(tune)
                        run["model_runtime_tune"] = all_raw
            runs["_system_guard_state"] = {
                "last_trigger_ts": int(now_ts),
                "cooldown_seconds": int(LOSS_GUARD_RESTART_COOLDOWN_SECONDS),
                "drawdown_ratio_threshold": float(LOSS_GUARD_DRAWDOWN_RATIO),
                "last_triggers": list(triggers),
                "last_updates": dict(updates),
            }
            self.state.model_runs = runs
        return updates

    def _maybe_drawdown_guard_restart(self, now_ts: int) -> None:
        rows = self._scan_drawdown_rows()
        triggers = [
            row
            for row in rows
            if float(row.get("drawdown_ratio") or 0.0) >= float(LOSS_GUARD_DRAWDOWN_RATIO)
        ]
        if not triggers:
            return
        newly_flagged = self._record_rebuild_required_models(int(now_ts), triggers)
        if newly_flagged:
            lines = [
                f"- {str(r['model_name'])}: seed={float(r['seed_usd']):.2f} "
                f"equity={float(r['latest_equity_usd']):.2f} dd={float(r['latest_drawdown_ratio']) * 100:.1f}%"
                for r in newly_flagged[:8]
            ]
            self._push_alert(
                "warn",
                "전면 재튜닝 필요 모델 기록",
                (
                    "시드 50% 하회 모델을 기록했습니다. (1주 운용 추적)\n"
                    f"{chr(10).join(lines)}"
                ),
                send_telegram=True,
            )

        with self._lock:
            guard_state = dict((self.state.model_runs or {}).get("_system_guard_state") or {})
        last_trigger = int(guard_state.get("last_trigger_ts") or 0)
        cooldown = int(guard_state.get("cooldown_seconds") or LOSS_GUARD_RESTART_COOLDOWN_SECONDS)
        if last_trigger > 0 and (int(now_ts) - last_trigger) < max(900, cooldown):
            return

        updates = self._apply_loss_guard_rewrite(int(now_ts), triggers)
        ranked = sorted(triggers, key=lambda r: float(r.get("drawdown_ratio") or 0.0), reverse=True)
        top = ranked[:6]
        lines = [
            f"- {str(r['model_name'])}: seed={float(r['seed_usd']):.2f} equity={float(r['equity_usd']):.2f} dd={float(r['drawdown_ratio']) * 100:.1f}%"
            for r in top
        ]
        self._push_alert(
            "warn",
            "손실 50% 가드 발동",
            (
                "모델 전면 방어 리라이트를 적용했습니다.\n"
                f"업데이트: {updates}\n"
                f"트리거:\n{chr(10).join(lines)}\n"
                "엔진을 자동 재시작합니다."
            ),
            send_telegram=True,
        )
        self._request_async_restart("loss_guard_drawdown_50pct")

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
        if bool(self.settings.enable_live_execution):
            live_text = self._build_telegram_periodic_report_live()
            self.alert_manager.send_telegram(live_text)
        else:
            demo_text = self._build_telegram_periodic_report_demo()
            self.alert_manager.send_telegram(demo_text)

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
            market_cap_usd=float(usd_market_cap),
            fdv_usd=float(usd_market_cap),
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
                raw_slots = max(12, min(int(MEME_THEME_RAW_FEED_SLOTS), max(target * 2, 24)))
                for row in pump_rows[:raw_slots]:
                    add_or_replace_snapshot(self._snapshot_from_pump_coin(row))
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
                        mints[: max(raw_slots, 80)],
                        max_tokens=max(target * 2, raw_slots),
                        source="pumpfun_dex",
                    )
                    for snap in hydrated:
                        add_or_replace_snapshot(snap)
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
            for model_id in MEME_MODEL_IDS:
                run = self._get_market_run(self.state.model_runs or {}, "meme", model_id)
                for token_address in dict(run.get("meme_positions") or {}).keys():
                    addr = str(token_address or "").strip()
                    if addr:
                        held_tokens.append(addr)
        unique_held: list[str] = []
        seen_held: set[str] = set()
        for addr in held_tokens:
            token_addr = str(addr or "").strip()
            if not token_addr or token_addr in seen_held:
                continue
            seen_held.add(token_addr)
            unique_held.append(token_addr)
        watch_map = self._meme_watch_tokens_snapshot(int(time.time()))
        for addr in watch_map.keys():
            token_addr = str(addr or "").strip()
            if not token_addr or token_addr in seen_held:
                continue
            seen_held.add(token_addr)
            unique_held.append(token_addr)
        for addr in unique_held[:120]:
            try:
                snap = self.dex.fetch_snapshot_for_token(self.settings.dex_chain, addr)
            except Exception:
                snap = None
            if snap is None and addr in watch_map:
                with self._lock:
                    cached_row = dict((self._meme_watch_snapshot_cache or {}).get(addr) or {})
                cached_ts = int(cached_row.get("ts") or 0)
                cached_snap = cached_row.get("snapshot")
                if (
                    isinstance(cached_snap, TokenSnapshot)
                    and cached_ts > 0
                    and (int(time.time()) - cached_ts) <= int(MEME_WATCH_SNAPSHOT_CACHE_SECONDS)
                ):
                    snap = cached_snap
            add_or_replace_snapshot(snap)
        with self._lock:
            if rows:
                self.state.memecoin_error = ""
            elif error_msg:
                self.state.memecoin_error = str(error_msg)
        watch_set = set(self._meme_watch_tokens_snapshot(int(time.time())).keys())
        if watch_set:
            rows.sort(
                key=lambda s: (
                    1 if str(getattr(s, "token_address", "") or "").strip() in watch_set else 0,
                    float(getattr(s, "volume_5m_usd", 0.0) or 0.0),
                    float(getattr(s, "liquidity_usd", 0.0) or 0.0),
                ),
                reverse=True,
            )
        for snap in rows:
            self._last_prices[snap.token_address] = float(snap.price_usd)
        extra_watch = min(20, len(watch_set))
        launch_slots = min(60, int(MEME_THEME_RAW_FEED_SLOTS))
        limit = min(120, target + max(extra_watch, launch_slots))
        return rows[:limit]

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
        community_4chan_events = _fetch_with_cache(
            "community_4chan",
            enabled=bool(self.settings.social_4chan_enabled) and bool(str(self.settings.social_4chan_boards or "").strip()),
            interval_seconds=max(120, int(self.settings.trend_community_interval_seconds)),
            fetcher=lambda: self.trend.fetch_4chan_events(
                self.settings.social_4chan_boards,
                self.settings.social_4chan_max_threads_per_board,
            ),
        )
        if community_4chan_events:
            community_events.extend(list(community_4chan_events))

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
        if google_status == "fallback_http":
            # HTTP fallback succeeded; expose as degraded-but-healthy to reduce noise.
            google_status = "ok_fallback"
            google_error = ""
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
            self.state.trend_events = self.state.trend_events[-STATE_TREND_HISTORY_LIMIT:]

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
            non_google_hits = (
                int(trader_counts.get(sym, 0))
                + int(wallet_counts.get(sym, 0))
                + int(news_counts.get(sym, 0))
                + int(community_counts.get(sym, 0))
            )
            # Google-only mentions are noisy for meme discovery.
            if int(hits) >= 2 and non_google_hits >= 1:
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
            "combined_counts": combined_counts,
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
        symbol_caps: dict[str, float] = {}
        symbol_ages: dict[str, float] = {}
        for snap in snapshots:
            if not self._is_memecoin_snapshot(snap):
                continue
            cap_usd = self._meme_effective_cap_usd(snap)
            sym = str(snap.symbol or "").upper()
            age_minutes = max(0.0, float(snap.age_minutes or 0.0))
            if sym and cap_usd > 0:
                prev_cap = float(symbol_caps.get(sym) or 0.0)
                if prev_cap <= 0.0 or cap_usd < prev_cap:
                    symbol_caps[sym] = float(cap_usd)
            if sym:
                prev_age = float(symbol_ages.get(sym) or 0.0)
                if prev_age <= 0.0 or age_minutes < prev_age:
                    symbol_ages[sym] = float(age_minutes)
            if not self._is_smallcap_memecoin_snapshot(snap):
                continue
            if age_minutes > max_age_minutes:
                continue
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
                    "age_minutes": float(age_minutes),
                    "price_usd": float(snap.price_usd),
                    "liquidity_usd": float(snap.liquidity_usd),
                    "volume_5m_usd": float(snap.volume_5m_usd),
                    "buys_5m": int(snap.buys_5m),
                    "sells_5m": int(snap.sells_5m),
                    "buy_sell_ratio": float(snap.buy_sell_ratio),
                    "trend_hits": int(hits),
                    "is_pump_fun": bool(str(snap.token_address or "").lower().endswith("pump")),
                    "market_cap_usd": float(cap_usd),
                    "market_cap_rank": int(self._meme_market_rank(sym)),
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
            self._meme_symbol_market_caps = symbol_caps
            self._meme_symbol_age_minutes = symbol_ages

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

    def _get_wallet_pattern_cached(self, token_address: str, now_ts: int | None = None) -> dict[str, Any]:
        token = str(token_address or "").strip()
        if not token:
            return {"available": False, "smart_wallet_score": 0.50, "holder_risk": 0.50}
        now = int(now_ts or int(time.time()))
        cached = self._wallet_pattern_cache.get(token) or {}
        ts = int(cached.get("cached_ts") or 0)
        if ts > 0 and (now - ts) < int(self.settings.solscan_cache_seconds):
            analysis = dict(cached.get("analysis") or {})
            if analysis:
                return analysis
        return {"available": False, "smart_wallet_score": 0.50, "holder_risk": 0.50}

    def _score_meme_snapshot_variant(
        self,
        snap: TokenSnapshot,
        model_id: str,
        *,
        trend_hit: int,
        trader_hits: int,
        wallet_hits: int,
        news_hits: int,
        community_hits: int,
        google_hits: int,
        wallet_pattern: dict[str, Any] | None = None,
        peer_info: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        wp = dict(wallet_pattern or {})
        if not wp:
            wp = {"available": False, "smart_wallet_score": 0.50, "holder_risk": 0.50}
        features = self._build_features(
            snap,
            trend_hit,
            trader_hits,
            wallet_hits,
            news_hits,
            community_hits,
            google_hits,
            wp,
            peer_info,
        )
        probability = self.model.predict_proba(features)
        heuristic = self._heuristic_score(features)
        score = self._variant_mix_score(model_id, probability, heuristic, features)
        grade = self._meme_grade(score)
        reason = self._build_reason(features, score, trend_hit, trader_hits, model_id, grade)
        strategy_id = self._meme_strategy_id_from_signal_context(
            snap=snap,
            features=features,
            reason=reason,
            current_strategy_id=self._meme_strategy_id_for_model(model_id),
        )
        strategy_name = self._meme_strategy_name(strategy_id)
        diagnostics = self._meme_score_diagnostics(model_id, features, score, grade)
        return {
            "model_id": str(model_id or "").upper().strip() or "A",
            "strategy_id": str(strategy_id),
            "strategy_name": str(strategy_name),
            "token": snap,
            "score": float(score),
            "grade": str(grade),
            "probability": float(probability),
            "reason": str(reason),
            "score_low_reason": str(diagnostics.get("low_reason") or ""),
            "score_hold_hint": str(diagnostics.get("hold_hint") or ""),
            "score_hold_target_grade": str(diagnostics.get("hold_target_grade") or ""),
            "score_hold_target_score": float(diagnostics.get("hold_target_score") or 0.0),
            "score_hold_gap": float(diagnostics.get("hold_gap") or 0.0),
            "features": features,
            "wallet_pattern": wp,
        }

    def _score_signals_variant(
        self,
        snapshots: list[TokenSnapshot],
        trend_bundle: dict[str, Any],
        model_id: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        trending: set[str] = set(trend_bundle.get("trending") or set())
        trader_count: dict[str, int] = dict(trend_bundle.get("trader_counts") or {})
        wallet_count: dict[str, int] = dict(trend_bundle.get("wallet_counts") or {})
        news_count: dict[str, int] = dict(trend_bundle.get("news_counts") or {})
        community_count: dict[str, int] = dict(trend_bundle.get("community_counts") or {})
        google_count: dict[str, int] = dict(trend_bundle.get("google_counts") or {})

        out: list[dict[str, Any]] = []
        relaxed: list[dict[str, Any]] = []
        scored_all: list[dict[str, Any]] = []
        now_ts = int(time.time())
        watch_map = self._meme_watch_tokens_snapshot(now_ts=now_ts)
        peer_map = self._meme_similarity_map(snapshots)
        wallet_budget = 6 if bool(self.settings.solscan_tracker_only) else 12
        wallet_pattern_map: dict[str, dict[str, Any]] = {}
        threshold = self._variant_threshold(model_id)
        guard = self._entry_guard_profile(model_id, "meme")
        threshold += float(guard.get("threshold_boost") or 0.0)
        for snap in snapshots:
            is_smallcap = self._is_smallcap_memecoin_snapshot(snap)
            tracked_wide_cap = False
            if (not is_smallcap) and str(model_id).upper() == "C" and self._is_memecoin_snapshot(snap):
                cap_usd = self._meme_effective_cap_usd(snap)
                token_addr = str(snap.token_address or "").strip()
                if token_addr:
                    watched = int(watch_map.get(token_addr) or 0) > now_ts
                    seen_before = any(f"{mid}:{token_addr}" in self._meme_score_log_guard for mid in MEME_MODEL_IDS)
                    tracked_wide_cap = bool(
                        (watched or seen_before)
                        and 0.0 < float(cap_usd) <= float(MEME_TRACKED_MAX_CAP_USD)
                        and self._meme_age_allowed(float(getattr(snap, "age_minutes", 0.0) or 0.0))
                    )
            if not is_smallcap and not tracked_wide_cap:
                continue
            symbol = snap.symbol.upper()
            trader_hits = int(trader_count.get(symbol, 0))
            wallet_hits = int(wallet_count.get(symbol, 0))
            news_hits = int(news_count.get(symbol, 0))
            community_hits = int(community_count.get(symbol, 0))
            google_hits = int(google_count.get(symbol, 0))
            trend_hit = 1 if (symbol in trending) else 0
            if is_smallcap:
                normal_candidate = self._is_candidate(snap, trend_hit, trader_hits)
                relaxed_candidate = self._is_relaxed_demo_candidate(
                    snap,
                    trend_hit,
                    trader_hits,
                    news_hits,
                    community_hits,
                    google_hits,
                )
            else:
                normal_candidate = False
                relaxed_candidate = False
            if tracked_wide_cap:
                cap_usd = self._meme_effective_cap_usd(snap)
                tracked_entry_ok = bool(
                    float(cap_usd) <= float(MEME_TRACKED_ENTRY_MAX_CAP_USD)
                    and float(snap.liquidity_usd) >= max(8000.0, float(self.settings.dex_min_liquidity_usd) * 1.20)
                    and float(snap.volume_5m_usd) >= max(4000.0, float(self.settings.dex_min_5m_volume_usd) * 1.00)
                    and float(snap.buy_sell_ratio) >= max(0.95, float(self.settings.dex_min_5m_buy_sell_ratio) * 0.85)
                )
                normal_candidate = bool(tracked_entry_ok)
                relaxed_candidate = False
            wallet_pattern: dict[str, Any] = {"available": False, "smart_wallet_score": 0.50, "holder_risk": 0.50}
            tracker_driven = bool(wallet_hits > 0 or trader_hits > 0)
            if (
                (normal_candidate or relaxed_candidate)
                and
                self.settings.solscan_enable_pattern
                and self.solscan.enabled
                and wallet_budget > 0
                and (tracker_driven or model_id in {"A", "B"})
            ):
                wallet_pattern = self._get_wallet_pattern(snap.token_address)
                wallet_budget -= 1
            elif self.settings.solscan_enable_pattern and self.solscan.enabled and model_id in {"A", "B"}:
                wallet_pattern = self._get_wallet_pattern_cached(snap.token_address)
            token_addr = str(snap.token_address or "").strip()
            if token_addr:
                wallet_pattern_map[token_addr] = dict(wallet_pattern or {})
            peer_info = dict(peer_map.get(token_addr) or {})
            peer_info.update(
                self._holder_overlap_features(
                    token_addr,
                    wallet_pattern,
                    peer_info,
                    peer_patterns=wallet_pattern_map,
                    now_ts=now_ts,
                    fetch_missing_peers=False,
                )
            )

            row = self._score_meme_snapshot_variant(
                snap,
                model_id,
                trend_hit=trend_hit,
                trader_hits=trader_hits,
                wallet_hits=wallet_hits,
                news_hits=news_hits,
                community_hits=community_hits,
                google_hits=google_hits,
                wallet_pattern=wallet_pattern,
                peer_info=peer_info,
            )
            scored_all.append(row)
            if not normal_candidate and not relaxed_candidate:
                continue
            score = float(row.get("score") or 0.0)
            if score < threshold:
                if normal_candidate or relaxed_candidate:
                    relaxed.append(row)
                continue
            out.append(row)
        if not out and relaxed:
            if not bool(guard.get("allow_demo_fallback", True)):
                scored_all.sort(key=lambda row: float(row.get("score") or 0.0), reverse=True)
                return out, scored_all
            floor = self._demo_meme_score_floor(model_id)
            floor += max(0.0, float(guard.get("threshold_boost") or 0.0))
            relaxed.sort(key=lambda row: float(row["score"]), reverse=True)
            limit = max(3, int(self.settings.max_signals_per_cycle) * 2)
            for row in relaxed:
                if float(row["score"]) < floor:
                    continue
                if model_id == "A":
                    # A fallback should still keep minimum liquidity/flow quality.
                    token: TokenSnapshot = row["token"]
                    feats = dict(row.get("features") or {})
                    if float(token.liquidity_usd) < 2500.0:
                        continue
                    if float(token.volume_5m_usd) < 1500.0:
                        continue
                    if float(feats.get("noise_penalty") or 0.0) > 0.72:
                        continue
                row["reason"] = f"{str(row.get('reason') or '')},데모폴백"
                out.append(row)
                if len(out) >= limit:
                    break
        out.sort(key=lambda row: float(row["score"]), reverse=True)
        scored_all.sort(key=lambda row: float(row.get("score") or 0.0), reverse=True)
        return out, scored_all

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
            {"grade": "E", "score_min": 0.50, "score_max": 0.5799, "meaning": "초기 후보. 실시간 추적/시험 진입 가능한 하한 구간"},
            {"grade": "F", "score_min": 0.42, "score_max": 0.4999, "meaning": "매우 약함. 진입 비권장"},
            {"grade": "G", "score_min": 0.00, "score_max": 0.4199, "meaning": "노이즈 구간"},
        ]

    @classmethod
    def _meme_grade_min_score(cls, grade: str) -> float:
        target = str(grade or "").upper().strip()
        for row in cls._meme_grade_criteria():
            if str(row.get("grade") or "").upper() == target:
                return float(row.get("score_min") or 0.0)
        return 0.0

    @staticmethod
    def _meme_hold_target_grade(model_id: str, strategy: str = "") -> str:
        mid = str(model_id or "").upper().strip()
        strat = str(strategy or "").lower().strip()
        if mid == "B" or strat == "swing":
            return "B"
        if mid == "A":
            return "B"
        return "A"

    def _meme_score_diagnostics(
        self,
        model_id: str,
        features: dict[str, Any],
        score: float,
        grade: str,
        *,
        strategy: str = "",
    ) -> dict[str, Any]:
        feats = dict(features or {})
        mid = str(model_id or "").upper().strip() or "C"
        hold_grade = self._meme_hold_target_grade(mid, strategy)
        hold_score = float(self._meme_grade_min_score(hold_grade))
        score_now = _clamp(float(score or 0.0), 0.0, 1.0)
        gap_to_hold = max(0.0, hold_score - score_now)

        issues: list[dict[str, Any]] = []
        seen_actions: set[str] = set()
        actions: list[str] = []

        def add_issue(label: str, severity: float, action: str) -> None:
            sev = float(max(0.0, severity))
            if sev <= 0.0:
                return
            issues.append({"label": label, "severity": sev, "action": action})
            if action and action not in seen_actions:
                seen_actions.add(action)
                actions.append(action)

        trend = float(feats.get("trend_strength") or 0.0)
        trader = float(feats.get("trader_strength") or 0.0)
        wallet = float(feats.get("wallet_strength") or 0.0)
        news = float(feats.get("news_strength") or 0.0)
        community = float(feats.get("community_strength") or 0.0)
        google = float(feats.get("google_strength") or 0.0)
        buy_ratio = float(feats.get("buy_sell_ratio") or 0.0)
        tx_flow = float(feats.get("tx_flow") or 0.0)
        liq_log = float(feats.get("liq_log") or 0.0)
        vol_log = float(feats.get("vol_log") or 0.0)
        age_freshness = float(feats.get("age_freshness") or 0.0)
        age_stability = float(feats.get("age_stability") or 0.0)
        noise = float(feats.get("noise_penalty") or 0.0)
        spread = float(feats.get("spread_proxy") or 0.0)
        smart = float(feats.get("smart_wallet_score") or 0.0)
        holder_risk = float(feats.get("holder_risk") or 0.0)
        transfer_diversity = float(feats.get("transfer_diversity") or 0.0)
        pattern_available = float(feats.get("wallet_pattern_available") or 0.0)
        suspicious = float(feats.get("wallet_suspicious") or 0.0)
        new_meme_instant = float(feats.get("new_meme_instant") or 0.0)
        similar_count = float(feats.get("similar_token_count") or 0.0)
        theme_confirmation = float(feats.get("theme_confirmation") or 0.0)
        theme_leader_score = float(feats.get("theme_leader_score") or 0.0)
        clone_pressure = float(feats.get("clone_pressure") or 0.0)
        late_clone_pressure = float(feats.get("late_clone_pressure") or 0.0)
        holder_overlap_risk = float(feats.get("holder_overlap_risk") or 0.0)

        social_mix = (news + community + google) / 3.0
        social_target = 0.16 if mid == "C" else 0.22
        flow_target = 0.58 if mid == "C" else 0.60
        liq_target = 0.56 if mid == "C" else 0.60
        vol_target = 0.60 if mid == "C" else 0.55

        if trend < 0.40:
            add_issue("트렌드 언급이 약함", 0.40 - trend, "X/뉴스/커뮤니티에서 동시 언급이 더 붙어야 합니다.")
        if trader < 0.22:
            add_issue("트레이더 언급이 부족함", 0.22 - trader, "유명 트레이더/탐지 계정에서 반복 언급되는 흐름이 필요합니다.")
        if social_mix < social_target:
            add_issue("소셜 확산이 약함", social_target - social_mix, "뉴스/커뮤니티 확산이 늘어야 홀딩 확률이 올라갑니다.")
        if 0.0 < similar_count < 1.5 and theme_confirmation < 0.34:
            add_issue("유사 코인 검증이 부족함", 0.34 - theme_confirmation, "같은 테마 확산이 1~2개 더 붙는지 확인이 필요합니다.")
        if clone_pressure >= 0.45:
            add_issue("유사 코인 과밀", clone_pressure - 0.35, "비슷한 코인이 너무 많아 선두 종목 여부를 더 엄격히 봐야 합니다.")
        if late_clone_pressure >= 0.45:
            add_issue("늦은 카피캣 가능성", late_clone_pressure - 0.35, "동일 테마 내 선두/원조인지 확인돼야 합니다.")
        if similar_count >= 1.0 and theme_leader_score < 0.5:
            add_issue("테마 선두가 아님", 0.55 - theme_leader_score, "동일 테마 중 거래대금/유동성 선두인지 확인돼야 합니다.")
        if holder_overlap_risk >= 0.30:
            add_issue("유사 코인과 상위 홀더 겹침 높음", holder_overlap_risk, "같은 지갑군이 여러 밈을 돌리는지 확인돼야 합니다.")
        if buy_ratio < 0.54:
            add_issue("매수 우위가 약함", 0.54 - buy_ratio, "매수/매도 비율이 더 개선돼야 합니다.")
        if tx_flow < flow_target:
            add_issue("체결 흐름이 약함", flow_target - tx_flow, "5분 체결 흐름이 순매수 쪽으로 더 기울어야 합니다.")
        if liq_log < liq_target:
            add_issue("유동성이 얕음", liq_target - liq_log, "유동성이 더 쌓여야 급락/청산 리스크가 줄어듭니다.")
        if vol_log < vol_target:
            add_issue("5분 거래량이 부족함", vol_target - vol_log, "5분 거래대금이 더 늘어야 추세 지속 가능성이 높아집니다.")
        if spread > 0.56:
            add_issue("스프레드/라우팅 부담이 큼", spread - 0.56, "더 두꺼운 유동성과 안정적인 라우트가 필요합니다.")
        if noise >= 0.5:
            add_issue("체결 표본이 너무 적음", noise, "틱 수가 더 쌓여 실제 수요인지 확인이 필요합니다.")
        if pattern_available < 0.5:
            add_issue("지갑 패턴 데이터가 없음", 0.22 if mid in {"A", "B"} else 0.12, "상위 홀더 분산과 스마트월렛 유입 확인이 필요합니다.")
        if smart < 0.60 and mid in {"A", "B"}:
            add_issue("스마트월렛 유입이 약함", 0.60 - smart, "상위 지갑의 순매수/분산 매집이 더 확인돼야 합니다.")
        if holder_risk > 0.60:
            add_issue("상위 지갑 집중 위험이 큼", holder_risk - 0.60, "홀더 분산이 개선돼야 홀딩 적합도가 올라갑니다.")
        if suspicious >= 0.5:
            add_issue("작업 지갑 패턴이 의심됨", suspicious, "의심 지갑 신호가 해소될 때까지 홀딩 비중을 낮춰야 합니다.")
        if mid == "B":
            if transfer_diversity < 0.20:
                add_issue("지갑 분산도가 낮음", 0.20 - transfer_diversity, "장기 홀딩용으로는 홀더 분산이 더 필요합니다.")
            if age_stability < 0.20:
                add_issue("홀딩 안정성이 아직 부족함", 0.20 - age_stability, "시간 경과 후에도 매수 흐름이 유지되는지 더 확인해야 합니다.")
        if mid == "C":
            if new_meme_instant < 0.5 and age_freshness < 0.78:
                add_issue("초기 버스트 강도가 약함", 0.78 - age_freshness, "초기 5~10분 버스트나 재버스트가 다시 붙어야 합니다.")
            if age_stability > 0.88:
                add_issue("신규성 메리트가 약함", age_stability - 0.88, "너무 성숙한 밈이면 재버스트 전까지 추적 우선이 맞습니다.")
        if mid == "A":
            quality_mix = (trend + trader + wallet) / 3.0
            if quality_mix < 0.28:
                add_issue("품질형 근거가 약함", 0.28 - quality_mix, "트렌드와 지갑 품질이 함께 보강돼야 합니다.")

        issues.sort(key=lambda row: float(row.get("severity") or 0.0), reverse=True)
        top_issues = issues[:3]
        low_reason = ", ".join(str(row.get("label") or "") for row in top_issues if str(row.get("label") or "")) or "주요 감점 요인 없음"

        if gap_to_hold > 0.0:
            hold_intro = f"홀딩권장 {hold_grade}({hold_score:.2f})까지 +{gap_to_hold:.2f}"
        else:
            hold_intro = f"현재 {grade}로 홀딩권장 {hold_grade} 구간 충족"

        top_actions = [str(text) for text in actions[:3] if str(text or "").strip()]
        if top_actions:
            hold_hint = hold_intro + " | " + " / ".join(top_actions)
        elif gap_to_hold > 0.0:
            hold_hint = hold_intro + " | 트렌드/거래량/지갑 분산이 함께 강화돼야 합니다."
        else:
            hold_hint = hold_intro + " | 추세 유지 여부만 계속 모니터링하면 됩니다."

        return {
            "low_reason": low_reason,
            "hold_hint": hold_hint,
            "hold_target_grade": hold_grade,
            "hold_target_score": float(round(hold_score, 4)),
            "hold_gap": float(round(gap_to_hold, 4)),
            "issues": [str(row.get("label") or "") for row in top_issues if str(row.get("label") or "")],
            "actions": top_actions,
        }

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
            {"key": "CONF", "meaning": "크립토 진입 신뢰도 점수(0~1), 최소 0.30 이상만 진입"},
            {"key": "OH", "meaning": "24h 급등 과열 패널티(높을수록 과열)"},
            {"key": "CHB", "meaning": "과열추격 차단 필터(Y면 추격 진입 차단)"},
            {"key": "rank_min", "meaning": "모델이 허용하는 시총 순위 하한"},
            {"key": "rank_max", "meaning": "모델이 허용하는 시총 순위 상한"},
            {"key": "trend_stack_min", "meaning": "모델별 최소 추세 스택 기준"},
            {"key": "overheat_max", "meaning": "모델별 과열 허용 상한"},
            {"key": "Hard-ROE", "meaning": "모델별 강제손절 기준(ROE%) 도달 시 즉시 청산"},
            {"key": "reentry_cooldown", "meaning": "Hard-ROE/SL 청산 후 같은 심볼 재진입 대기 시간"},
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

    def _meme_quality_gate_for_entry(self, model_id: str, features: dict[str, Any]) -> bool:
        smart = float(features.get("smart_wallet_score") or 0.0)
        holder_risk = float(features.get("holder_risk") or 1.0)
        transfer_diversity = float(features.get("transfer_diversity") or 0.0)
        trend = float(features.get("trend_strength") or 0.0)
        trader = float(features.get("trader_strength") or 0.0)
        wallet = float(features.get("wallet_strength") or 0.0)
        tx_flow = float(features.get("tx_flow") or 0.0)
        pattern_available = float(features.get("wallet_pattern_available") or 0.0)
        pattern_suspicious = float(features.get("wallet_suspicious") or 0.0)
        pattern_required = bool(self.settings.solscan_enable_pattern and self.solscan.enabled and model_id == "B")

        if pattern_required and pattern_available < 0.5:
            return False
        if model_id == "A":
            if pattern_suspicious >= 0.5:
                return False
            if smart < 0.58 or holder_risk > 0.65:
                return False
            if pattern_available < 0.5 and (trend + trader + wallet) < 0.70:
                return False
            if max(trend, trader, wallet) < 0.18:
                return False
            return True
        if model_id == "B":
            if pattern_suspicious >= 0.5:
                return False
            if smart < 0.62 or holder_risk > 0.58:
                return False
            if (trend + trader + wallet) < 0.55:
                return False
            if transfer_diversity < 0.18 and tx_flow < 0.58:
                return False
            return True
        return True

    def _variant_threshold(self, model_id: str) -> float:
        base = float(self.settings.min_signal_score)
        if model_id == "A":
            # Demo strategy runs continuously regardless of live execution toggle.
            return max(0.0, base - 0.060)
        if model_id == "B":
            # Trend model: medium gate.
            return max(0.0, base - 0.09)
        # Aggressive model: looser gate.
        return max(0.0, base - 0.18)

    @staticmethod
    def _variant_mix_score(model_id: str, probability: float, heuristic: float, features: dict[str, float]) -> float:
        if model_id == "A":
            score = (
                (0.82 * probability)
                + (0.18 * heuristic)
                + (0.14 * features.get("smart_wallet_score", 0.0))
                + (0.08 * features.get("trend_strength", 0.0))
                + (0.06 * features.get("trader_strength", 0.0))
                + (0.04 * features.get("liq_log", 0.0))
                + (0.03 * features.get("is_pump_fun", 0.0))
                + (0.10 * features.get("theme_launch_fit", 0.0))
                + (0.08 * features.get("theme_launch_ready", 0.0))
                + (0.03 * features.get("theme_confirmation", 0.0))
                + (0.04 * features.get("theme_leader_score", 0.0))
                + (0.03 * features.get("holder_overlap_clean", 0.0))
                - (0.08 * features.get("spread_proxy", 0.0))
                - (0.12 * features.get("holder_risk", 0.0))
                - (0.06 * features.get("noise_penalty", 0.0))
                - (0.12 * features.get("wallet_suspicious", 0.0))
                - (0.06 * (1.0 - float(features.get("wallet_pattern_available", 0.0))))
                - (0.03 * features.get("new_meme_instant", 0.0))
                - (0.10 * features.get("clone_pressure", 0.0))
                - (0.12 * features.get("late_clone_pressure", 0.0))
                - (0.14 * features.get("holder_overlap_risk", 0.0))
            )
            return _clamp(score, 0.0, 1.0)
        if model_id == "B":
            score = (
                (0.45 * probability)
                + (0.55 * heuristic)
                + (0.16 * features.get("trend_strength", 0.0))
                + (0.09 * features.get("news_strength", 0.0))
                + (0.10 * features.get("community_strength", 0.0))
                + (0.03 * features.get("google_strength", 0.0))
                + (0.18 * features.get("trader_strength", 0.0))
                + (0.10 * features.get("tx_flow", 0.0))
                + (0.08 * features.get("is_pump_fun", 0.0))
                + (0.10 * features.get("theme_launch_fit", 0.0))
                + (0.05 * features.get("theme_launch_ready", 0.0))
                + (0.18 * features.get("smart_wallet_score", 0.0))
                + (0.08 * features.get("transfer_diversity", 0.0))
                + (0.05 * features.get("theme_confirmation", 0.0))
                + (0.05 * features.get("theme_leader_score", 0.0))
                + (0.04 * features.get("holder_overlap_clean", 0.0))
                - (0.05 * features.get("noise_penalty", 0.0))
                - (0.14 * features.get("holder_risk", 0.0))
                - (0.12 * features.get("wallet_suspicious", 0.0))
                - (0.08 * (1.0 - float(features.get("wallet_pattern_available", 0.0))))
                - (0.10 * features.get("clone_pressure", 0.0))
                - (0.12 * features.get("late_clone_pressure", 0.0))
                - (0.16 * features.get("holder_overlap_risk", 0.0))
            )
            return _clamp(score, 0.0, 1.0)
        score = (
            (0.24 * probability)
            + (0.76 * heuristic)
            + (0.16 * features.get("sniper_social_burst", 0.0))
            + (0.18 * features.get("sniper_signal_fit", 0.0))
            + (0.12 * features.get("sniper_cap_fit", 0.0))
            + (0.16 * features.get("tx_flow", 0.0))
            + (0.12 * features.get("buy_sell_ratio", 0.0))
            + (0.10 * features.get("trader_strength", 0.0))
            + (0.08 * features.get("community_strength", 0.0))
            + (0.06 * features.get("news_strength", 0.0))
            + (0.12 * features.get("theme_launch_fit", 0.0))
            + (0.10 * features.get("theme_launch_ready", 0.0))
            + (0.08 * features.get("theme_confirmation", 0.0))
            + (0.08 * features.get("new_meme_instant", 0.0))
            + (0.08 * features.get("vol_log", 0.0))
            + (0.06 * features.get("liq_log", 0.0))
            + (0.04 * features.get("is_pump_fun", 0.0))
            + (0.04 * features.get("theme_leader_score", 0.0))
            + (0.02 * features.get("holder_overlap_clean", 0.0))
            - (0.04 * features.get("spread_proxy", 0.0))
            - (0.04 * features.get("holder_risk", 0.0))
            - (0.06 * features.get("noise_penalty", 0.0))
            - (0.10 * features.get("clone_pressure", 0.0))
            - (0.14 * features.get("late_clone_pressure", 0.0))
            - (0.18 * features.get("holder_overlap_risk", 0.0))
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

    def _meme_market_rank(self, symbol: str, macro_meta: dict[str, dict[str, Any]] | None = None) -> int:
        sym = str(symbol or "").upper().strip()
        if not sym:
            return 0
        table = dict(macro_meta or self._macro_meta or {})
        row = dict(table.get(sym) or table.get(f"{sym}USDT") or table.get(f"{sym}USD") or {})
        return int(row.get("market_cap_rank") or 0)

    @staticmethod
    def _meme_age_allowed(age_minutes: float) -> bool:
        age = float(age_minutes or 0.0)
        return 0.0 <= age <= MEME_MAX_AGE_MINUTES

    def _meme_symbol_allowed(self, symbol: str, macro_meta: dict[str, dict[str, Any]] | None = None) -> bool:
        rank = int(self._meme_market_rank(symbol, macro_meta))
        return not (0 < rank <= MEME_EXCLUDE_TOP_RANK_MAX)

    @staticmethod
    def _meme_effective_cap_usd(snap: TokenSnapshot) -> float:
        cap = float(getattr(snap, "market_cap_usd", 0.0) or 0.0)
        if cap <= 0.0:
            cap = float(getattr(snap, "fdv_usd", 0.0) or 0.0)
        return max(0.0, cap)

    @staticmethod
    def _meme_similarity_terms(symbol: str, name: str) -> set[str]:
        raw = re.findall(r"[a-z0-9]+", f"{str(symbol or '')} {str(name or '')}".lower())
        out: set[str] = set()
        for token in raw:
            tok = str(token or "").strip()
            if not tok or tok in MEME_SIMILARITY_STOPWORDS:
                continue
            if len(tok) >= 3:
                out.add(tok)
            elif tok in {"ai", "gm"}:
                out.add(tok)
            for suffix in MEME_SIMILARITY_SPLIT_SUFFIXES:
                if tok.endswith(suffix) and len(tok) > len(suffix) + 2:
                    stem = tok[: -len(suffix)]
                    if len(stem) >= 3:
                        out.add(stem)
                    if len(suffix) >= 2:
                        out.add(suffix)
        return out

    @classmethod
    def _meme_similarity_signature(cls, symbol: str, name: str) -> str:
        terms = sorted(cls._meme_similarity_terms(symbol, name))
        if terms:
            return " ".join(terms[:6])
        return re.sub(r"[^a-z0-9]+", " ", f"{str(symbol or '')} {str(name or '')}".lower()).strip()

    @classmethod
    def _meme_similarity_match(cls, left: dict[str, Any], right: dict[str, Any]) -> bool:
        left_terms = set(left.get("terms") or set())
        right_terms = set(right.get("terms") or set())
        shared = left_terms & right_terms
        left_sig = str(left.get("signature") or "")
        right_sig = str(right.get("signature") or "")
        ratio = SequenceMatcher(None, left_sig, right_sig).ratio() if left_sig and right_sig else 0.0
        if len(shared) >= 2:
            return True
        if len(shared) >= 1 and ratio >= 0.52:
            return True
        if ratio >= 0.84:
            return True
        return False

    @classmethod
    def _meme_similarity_features_from_rows(
        cls,
        target: dict[str, Any],
        peers: list[dict[str, Any]],
    ) -> dict[str, Any]:
        similar_rows = [row for row in list(peers or []) if cls._meme_similarity_match(target, row)]
        similar_count = len(similar_rows)
        if similar_count <= 0:
            return {
                "similar_token_count": 0.0,
                "similar_token_count_norm": 0.0,
                "theme_confirmation": 0.0,
                "theme_leader_score": 0.0,
                "clone_pressure": 0.0,
                "late_clone_pressure": 0.0,
                "similar_peer_tokens": [],
            }
        self_strength = float(target.get("strength") or 0.0)
        older_peers = sum(1 for row in similar_rows if float(row.get("age_minutes") or 0.0) + 1.0 < float(target.get("age_minutes") or 0.0))
        stronger_peers = sum(1 for row in similar_rows if float(row.get("strength") or 0.0) > (self_strength * 1.08))
        leader_score = 1.0 if stronger_peers <= 0 else 0.0
        theme_confirmation = _clamp(min(similar_count, 3) / 3.0, 0.0, 1.0)
        clone_pressure = _clamp(max(0, similar_count - 1) / 5.0, 0.0, 1.0)
        late_clone_pressure = _clamp(
            (0.55 if stronger_peers > 0 else 0.0)
            + (0.30 if older_peers > 0 else 0.0)
            + (0.12 * max(0, similar_count - 2)),
            0.0,
            1.0,
        )
        return {
            "similar_token_count": float(similar_count),
            "similar_token_count_norm": float(_clamp(similar_count / 6.0, 0.0, 1.0)),
            "theme_confirmation": float(theme_confirmation),
            "theme_leader_score": float(leader_score),
            "clone_pressure": float(clone_pressure),
            "late_clone_pressure": float(late_clone_pressure),
            "similar_peer_tokens": [
                str(row.get("token_address") or "")
                for row in similar_rows[:8]
                if str(row.get("token_address") or "")
            ],
        }

    @classmethod
    def _meme_similarity_row_from_snapshot(cls, snap: TokenSnapshot) -> dict[str, Any]:
        liq = float(snap.liquidity_usd or 0.0)
        vol = float(snap.volume_5m_usd or 0.0)
        return {
            "token_address": str(snap.token_address or "").strip(),
            "symbol": str(snap.symbol or "").upper().strip(),
            "name": str(snap.name or "").strip(),
            "age_minutes": float(snap.age_minutes or 0.0),
            "liquidity_usd": liq,
            "volume_5m_usd": vol,
            "market_cap_usd": float(cls._meme_effective_cap_usd(snap)),
            "strength": float(vol + (0.35 * liq)),
            "terms": cls._meme_similarity_terms(snap.symbol, snap.name),
            "signature": cls._meme_similarity_signature(snap.symbol, snap.name),
        }

    def _meme_similarity_map(self, snapshots: list[TokenSnapshot]) -> dict[str, dict[str, Any]]:
        rows = [
            self._meme_similarity_row_from_snapshot(snap)
            for snap in list(snapshots or [])
            if isinstance(snap, TokenSnapshot)
            and self._is_memecoin_snapshot(snap)
            and float(getattr(snap, "age_minutes", 0.0) or 0.0) <= float(MEME_SIMILARITY_LOOKBACK_MINUTES)
            and 0.0 < float(self._meme_effective_cap_usd(snap)) <= float(MEME_TRACKED_MAX_CAP_USD)
        ]
        out: dict[str, dict[str, float]] = {}
        for row in rows:
            token = str(row.get("token_address") or "").strip()
            if not token:
                continue
            peers = [peer for peer in rows if str(peer.get("token_address") or "") != token]
            out[token] = self._meme_similarity_features_from_rows(row, peers)
        return out

    def _meme_similarity_for_snapshot(self, snapshot: TokenSnapshot) -> dict[str, Any]:
        if not isinstance(snapshot, TokenSnapshot):
            return {
                "similar_token_count": 0.0,
                "similar_token_count_norm": 0.0,
                "theme_confirmation": 0.0,
                "theme_leader_score": 0.0,
                "clone_pressure": 0.0,
                "late_clone_pressure": 0.0,
            }
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        target_row = self._meme_similarity_row_from_snapshot(snapshot)
        target_token = str(target_row.get("token_address") or "").strip()
        if target_token:
            seen.add(target_token)
        rows.append(target_row)
        with self._lock:
            feed_rows = list(self._new_meme_feed or [])
            cache_rows = dict(self._meme_watch_snapshot_cache or {})
        for row in feed_rows:
            token = str((row or {}).get("token_address") or "").strip()
            if not token or token in seen:
                continue
            age_minutes = float((row or {}).get("age_minutes") or 0.0)
            mcap = float((row or {}).get("market_cap_usd") or 0.0)
            if age_minutes > float(MEME_SIMILARITY_LOOKBACK_MINUTES):
                continue
            if not (0.0 < mcap <= float(MEME_TRACKED_MAX_CAP_USD)):
                continue
            symbol = str((row or {}).get("symbol") or "").upper().strip()
            name = str((row or {}).get("name") or "").strip()
            liq = float((row or {}).get("liquidity_usd") or 0.0)
            vol = float((row or {}).get("volume_5m_usd") or 0.0)
            rows.append(
                {
                    "token_address": token,
                    "symbol": symbol,
                    "name": name,
                    "age_minutes": age_minutes,
                    "liquidity_usd": liq,
                    "volume_5m_usd": vol,
                    "market_cap_usd": mcap,
                    "strength": float(vol + (0.35 * liq)),
                    "terms": self._meme_similarity_terms(symbol, name),
                    "signature": self._meme_similarity_signature(symbol, name),
                }
            )
            seen.add(token)
        for cached in list(cache_rows.values()):
            snap = (cached or {}).get("snapshot")
            if not isinstance(snap, TokenSnapshot):
                continue
            token = str(snap.token_address or "").strip()
            if not token or token in seen:
                continue
            if not self._is_memecoin_snapshot(snap):
                continue
            if float(getattr(snap, "age_minutes", 0.0) or 0.0) > float(MEME_SIMILARITY_LOOKBACK_MINUTES):
                continue
            if not (0.0 < float(self._meme_effective_cap_usd(snap)) <= float(MEME_TRACKED_MAX_CAP_USD)):
                continue
            rows.append(self._meme_similarity_row_from_snapshot(snap))
            seen.add(token)
        peers = [row for row in rows if str(row.get("token_address") or "") != target_token]
        return self._meme_similarity_features_from_rows(target_row, peers)

    def _holder_overlap_features(
        self,
        token_address: str,
        wallet_pattern: dict[str, Any] | None,
        peer_info: dict[str, Any] | None,
        *,
        peer_patterns: dict[str, dict[str, Any]] | None = None,
        now_ts: int | None = None,
        fetch_missing_peers: bool = False,
        max_peer_fetches: int = 2,
    ) -> dict[str, float]:
        target_token = str(token_address or "").strip()
        pattern = dict(wallet_pattern or {})
        peer_ctx = dict(peer_info or {})
        target_wallets = [str(v or "").strip() for v in list(pattern.get("top_holder_wallets") or []) if str(v or "").strip()]
        target_weights_raw = dict(pattern.get("top_holder_weights") or {})
        target_weights = {str(k or "").strip(): float(v or 0.0) for k, v in target_weights_raw.items() if str(k or "").strip()}
        if not target_token or not target_wallets:
            return {
                "holder_overlap_max": 0.0,
                "holder_overlap_mean": 0.0,
                "holder_overlap_weighted_max": 0.0,
                "holder_overlap_peer_count": 0.0,
                "holder_overlap_risk": 0.0,
                "holder_overlap_clean": 0.0,
            }

        cached_peer_patterns = dict(peer_patterns or {})
        overlap_ratios: list[float] = []
        weighted_overlaps: list[float] = []
        peer_hits = 0
        fetched = 0
        target_set = set(target_wallets)
        peer_tokens = [
            str(v or "").strip()
            for v in list(peer_ctx.get("similar_peer_tokens") or [])
            if str(v or "").strip() and str(v or "").strip() != target_token
        ][:8]
        for peer_token in peer_tokens:
            pat = dict(cached_peer_patterns.get(peer_token) or {})
            if not pat:
                pat = self._get_wallet_pattern_cached(peer_token, now_ts=now_ts)
            if (not bool(pat.get("available"))) and fetch_missing_peers and fetched < max(0, int(max_peer_fetches)):
                try:
                    pat = self._get_wallet_pattern(peer_token, now_ts=now_ts)
                    fetched += 1
                except Exception:
                    pat = dict(pat or {})
            peer_wallets = [str(v or "").strip() for v in list(pat.get("top_holder_wallets") or []) if str(v or "").strip()]
            if not peer_wallets:
                continue
            peer_weights_raw = dict(pat.get("top_holder_weights") or {})
            peer_weights = {str(k or "").strip(): float(v or 0.0) for k, v in peer_weights_raw.items() if str(k or "").strip()}
            peer_set = set(peer_wallets)
            common = target_set & peer_set
            if not common:
                continue
            peer_hits += 1
            overlap_ratios.append(float(len(common)) / float(max(1, min(len(target_set), len(peer_set)))))
            weighted_overlaps.append(
                float(
                    sum(
                        min(float(target_weights.get(owner) or 0.0), float(peer_weights.get(owner) or 0.0))
                        for owner in common
                    )
                )
            )
        if not overlap_ratios:
            return {
                "holder_overlap_max": 0.0,
                "holder_overlap_mean": 0.0,
                "holder_overlap_weighted_max": 0.0,
                "holder_overlap_peer_count": 0.0,
                "holder_overlap_risk": 0.0,
                "holder_overlap_clean": float(
                    _clamp(float(peer_ctx.get("theme_confirmation") or 0.0), 0.0, 1.0) * 0.15
                ),
            }

        max_ratio = max(overlap_ratios)
        mean_ratio = float(sum(overlap_ratios)) / float(len(overlap_ratios))
        max_weighted = max(weighted_overlaps) if weighted_overlaps else 0.0
        peer_count_norm = _clamp(float(peer_hits) / 3.0, 0.0, 1.0)
        overlap_risk = _clamp(
            (0.45 * max_ratio)
            + (0.20 * mean_ratio)
            + (0.25 * _clamp(max_weighted / 0.18, 0.0, 1.0))
            + (0.10 * peer_count_norm),
            0.0,
            1.0,
        )
        clean_score = _clamp(
            float(peer_ctx.get("theme_confirmation") or 0.0) * (1.0 - overlap_risk),
            0.0,
            1.0,
        )
        return {
            "holder_overlap_max": float(max_ratio),
            "holder_overlap_mean": float(mean_ratio),
            "holder_overlap_weighted_max": float(max_weighted),
            "holder_overlap_peer_count": float(peer_hits),
            "holder_overlap_risk": float(overlap_risk),
            "holder_overlap_clean": float(clean_score),
        }

    def _is_smallcap_memecoin_snapshot(self, snap: TokenSnapshot) -> bool:
        if not self._is_memecoin_snapshot(snap):
            return False
        if not self._meme_age_allowed(float(getattr(snap, "age_minutes", 0.0) or 0.0)):
            return False
        if not self._meme_symbol_allowed(str(getattr(snap, "symbol", "") or "")):
            return False
        cap = self._meme_effective_cap_usd(snap)
        return 0.0 < cap <= MEME_SMALLCAP_MAX_USD

    def _is_candidate(self, snap: TokenSnapshot, trend_hit: int, trader_hits: int) -> bool:
        if not self._is_smallcap_memecoin_snapshot(snap):
            return False
        cap = self._meme_effective_cap_usd(snap)
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
        launch_lane = (
            str(getattr(snap, "source", "") or "").lower().startswith("pumpfun")
            and 0.0 < float(cap) <= float(MEME_THEME_LAUNCH_HARD_MAX_CAP_USD)
            and float(snap.age_minutes) <= float(MEME_THEME_LAUNCH_MAX_AGE_MINUTES)
            and (
                (
                    float(snap.liquidity_usd) >= float(MEME_THEME_LAUNCH_MIN_LIQUIDITY_USD)
                    and float(snap.volume_5m_usd) >= float(MEME_THEME_LAUNCH_MIN_VOLUME_5M_USD)
                    and int(snap.buys_5m) >= 2
                    and int(snap.buys_5m) >= int(snap.sells_5m)
                )
                or self._is_theme_launch_zone(snap)
            )
        )
        return base_ok or fast_lane or trend_lane or launch_lane

    def _theme_launch_entry_ok(self, snap: TokenSnapshot, features: dict[str, Any] | None = None) -> bool:
        feats = dict(features or {})
        cap = float(self._meme_effective_cap_usd(snap))
        age = float(getattr(snap, "age_minutes", 0.0) or 0.0)
        source = str(getattr(snap, "source", "") or "").strip().lower()
        vol_5m = float(getattr(snap, "volume_5m_usd", 0.0) or 0.0)
        buys_5m = int(getattr(snap, "buys_5m", 0) or 0)
        buy_sell_ratio = float(getattr(snap, "buy_sell_ratio", 0.0) or 0.0)
        tx_flow = float(feats.get("tx_flow") or 0.0)
        similar_count = int(float(feats.get("similar_token_count") or 0.0))
        theme_confirmation = float(feats.get("theme_confirmation") or 0.0)
        social_burst = float(feats.get("sniper_social_burst") or 0.0)
        clone_pressure = float(feats.get("clone_pressure") or 0.0)
        late_clone_pressure = float(feats.get("late_clone_pressure") or 0.0)
        holder_overlap_risk = float(feats.get("holder_overlap_risk") or 0.0)
        is_launch_source = bool(
            source.startswith("pumpfun")
            or source.startswith("bonk")
            or float(feats.get("new_meme_instant") or 0.0) > 0.0
            or float(feats.get("is_pump_fun") or 0.0) > 0.0
        )
        if cap <= 0.0 or cap > float(MEME_THEME_LAUNCH_HARD_MAX_CAP_USD):
            return False
        if age > float(MEME_THEME_LAUNCH_MAX_AGE_MINUTES):
            return False
        if not is_launch_source:
            return False
        if late_clone_pressure >= 0.72 or holder_overlap_risk >= 0.74:
            return False
        cluster_required = max(1, int(self.settings.meme_theme_cluster_min_tokens) - 1)
        cluster_ready = similar_count >= cluster_required or theme_confirmation >= 0.34
        flow_ready = buys_5m >= 2 and buy_sell_ratio >= 1.02 and tx_flow >= 0.52
        raw_launch_source = bool(source.startswith("pumpfun") or source.startswith("bonk"))
        if (
            raw_launch_source
            and float(MEME_THEME_LAUNCH_IDEAL_MIN_CAP_USD) <= cap <= float(MEME_THEME_LAUNCH_IDEAL_MAX_CAP_USD)
            and age <= float(MEME_THEME_LAUNCH_ENTRY_MAX_AGE_MINUTES)
            and flow_ready
            and (cluster_ready or social_burst >= 0.46)
        ):
            return True
        if (
            raw_launch_source
            and age <= float(MEME_THEME_LAUNCH_MAX_AGE_MINUTES)
            and vol_5m >= 250.0
            and buys_5m >= 20
            and buy_sell_ratio >= 0.80
            and (cluster_ready or social_burst >= 0.54)
        ):
            return True
        if (
            raw_launch_source
            and float(MEME_THEME_LAUNCH_SOFT_MIN_CAP_USD) <= cap <= float(MEME_THEME_LAUNCH_SOFT_MAX_CAP_USD)
            and age <= max(float(MEME_THEME_LAUNCH_ENTRY_MAX_AGE_MINUTES), 10.0)
            and vol_5m >= 250.0
            and buys_5m >= 20
            and buy_sell_ratio >= 0.80
            and (cluster_ready or social_burst >= 0.56)
        ):
            return True
        if float(getattr(snap, "liquidity_usd", 0.0) or 0.0) < float(MEME_THEME_LAUNCH_MIN_LIQUIDITY_USD):
            return False
        if vol_5m < float(MEME_THEME_LAUNCH_MIN_VOLUME_5M_USD):
            return False
        if buys_5m < 2:
            return False
        if buy_sell_ratio < 1.05:
            return False
        if tx_flow < 0.52:
            return False
        if not cluster_ready and social_burst < 0.60:
            return False
        if clone_pressure >= 0.78:
            return False
        return True

    def _sniper_entry_ok(self, snap: TokenSnapshot, features: dict[str, Any] | None = None) -> bool:
        feats = dict(features or {})
        cap = float(self._meme_effective_cap_usd(snap))
        liq = float(getattr(snap, "liquidity_usd", 0.0) or 0.0)
        vol_5m = float(getattr(snap, "volume_5m_usd", 0.0) or 0.0)
        buy_sell_ratio = float(getattr(snap, "buy_sell_ratio", 0.0) or 0.0)
        social_burst = float(feats.get("sniper_social_burst") or 0.0)
        signal_fit = float(feats.get("sniper_signal_fit") or 0.0)
        tx_flow = float(feats.get("tx_flow") or 0.0)
        trader_strength = float(feats.get("trader_strength") or 0.0)
        community_strength = float(feats.get("community_strength") or 0.0)
        news_strength = float(feats.get("news_strength") or 0.0)
        trend_strength = float(feats.get("trend_strength") or 0.0)
        clone_pressure = float(feats.get("clone_pressure") or 0.0)
        late_clone_pressure = float(feats.get("late_clone_pressure") or 0.0)
        holder_overlap_risk = float(feats.get("holder_overlap_risk") or 0.0)
        if cap < MEME_SNIPER_MIN_CAP_USD or cap > MEME_SNIPER_MAX_CAP_USD:
            return False
        if liq < MEME_SNIPER_MIN_LIQUIDITY_USD:
            return False
        if vol_5m < MEME_SNIPER_MIN_VOLUME_5M_USD:
            return False
        if social_burst < MEME_SNIPER_MIN_SOCIAL_BURST and signal_fit < MEME_SNIPER_MIN_SIGNAL_FIT:
            return False
        if buy_sell_ratio < 0.98 and tx_flow < 0.52:
            return False
        if max(trader_strength, community_strength, news_strength, trend_strength) < 0.18:
            return False
        if late_clone_pressure >= 0.82 or holder_overlap_risk >= 0.78:
            return False
        if clone_pressure >= 0.88 and social_burst < 0.70:
            return False
        return True

    def _theme_launch_priority_tuple(self, snap: TokenSnapshot, score: float) -> tuple[float, float, float, float]:
        cap = float(self._meme_effective_cap_usd(snap))
        age = float(getattr(snap, "age_minutes", 0.0) or 0.0)
        vol = float(getattr(snap, "volume_5m_usd", 0.0) or 0.0)
        if float(MEME_THEME_LAUNCH_IDEAL_MIN_CAP_USD) <= cap <= float(MEME_THEME_LAUNCH_IDEAL_MAX_CAP_USD):
            cap_penalty = 0.0
        else:
            cap_penalty = abs(cap - 4_000.0) / 4_000.0 if cap > 0.0 else 99.0
        return (
            float(cap_penalty),
            float(_clamp(age / max(MEME_THEME_LAUNCH_MAX_AGE_MINUTES, 1.0), 0.0, 10.0)),
            float(-score),
            float(-vol),
        )

    def _is_theme_launch_zone(self, snap: TokenSnapshot, features: dict[str, Any] | None = None) -> bool:
        feats = dict(features or {})
        cap = float(self._meme_effective_cap_usd(snap))
        age = float(getattr(snap, "age_minutes", 0.0) or 0.0)
        source = str(getattr(snap, "source", "") or "").strip().lower()
        return bool(
            (source.startswith("pumpfun") or float(feats.get("is_pump_fun") or 0.0) > 0.0)
            and float(MEME_THEME_LAUNCH_IDEAL_MIN_CAP_USD) <= cap <= float(MEME_THEME_LAUNCH_IDEAL_MAX_CAP_USD)
            and age <= float(MEME_THEME_LAUNCH_ENTRY_MAX_AGE_MINUTES)
        )

    @staticmethod
    def _pumpportal_sell_ratio_text(close_fraction: float) -> str:
        pct = _clamp(float(close_fraction or 1.0), 0.01, 1.0) * 100.0
        if pct >= 99.9:
            return "100%"
        if abs(pct - round(pct)) < 0.001:
            return f"{int(round(pct))}%"
        return f"{pct:.2f}%"

    def _live_meme_buy(
        self,
        *,
        token: TokenSnapshot,
        strategy_id: str,
        order_sol: float,
        model_id: str,
        features: dict[str, Any],
    ) -> tuple[dict[str, Any], str]:
        strategy_key = str(strategy_id or "").upper().strip()
        if strategy_key == "THEME" and self._is_theme_launch_zone(token, features) and self.pumpportal_trader.enabled:
            result = self.pumpportal_trader.buy_token_with_sol(
                token.token_address,
                amount_sol=float(order_sol),
                slippage_pct=MEME_PUMPPORTAL_SLIPPAGE_PCT,
                priority_fee_sol=MEME_PUMPPORTAL_PRIORITY_FEE_SOL,
                pool="auto",
            )
            return dict(result or {}), "pumpportal"
        slippage_try = 280 if model_id == "A" else (320 if model_id == "B" else 380)
        result = self.solana_trader.swap_sol_to_token(
            token.token_address,
            amount_sol=float(order_sol),
            slippage_bps=slippage_try,
        )
        return dict(result or {}), "jupiter"

    def _live_meme_sell(
        self,
        *,
        token_address: str,
        position: dict[str, Any],
        close_fraction: float,
        raw_amount: int,
        wallet_qty: float,
    ) -> tuple[dict[str, Any], str]:
        venue = str(position.get("execution_venue") or "").strip().lower()
        if venue == "pumpportal" and self.pumpportal_trader.enabled:
            result = self.pumpportal_trader.sell_token_to_sol(
                token_address,
                amount=self._pumpportal_sell_ratio_text(close_fraction),
                slippage_pct=MEME_PUMPPORTAL_SLIPPAGE_PCT,
                priority_fee_sol=MEME_PUMPPORTAL_PRIORITY_FEE_SOL,
                pool="auto",
            )
            return dict(result or {}), "pumpportal"
        swap_raw_amount = int(raw_amount)
        requested_fraction = _clamp(float(close_fraction or 1.0), 0.01, 1.0)
        if requested_fraction < 0.999 and wallet_qty > 0.0:
            qty_ratio = _clamp(requested_fraction, 0.01, 1.0)
            swap_raw_amount = int(max(1, math.floor(float(raw_amount) * float(qty_ratio))))
            if swap_raw_amount >= raw_amount:
                swap_raw_amount = max(1, raw_amount - 1)
        if swap_raw_amount <= 0:
            raise RuntimeError("close_amount_too_small")
        last_exc: Exception | None = None
        for slippage_try in (380, 520, 700):
            try:
                result = self.solana_trader.swap_token_to_sol(
                    token_address,
                    amount_raw=swap_raw_amount,
                    slippage_bps=slippage_try,
                )
                return dict(result or {}), "jupiter"
            except Exception as exc:
                err_low = str(exc).lower()
                last_exc = exc
                if "token_not_tradable" in err_low or "not tradable" in err_low:
                    raise
        raise last_exc if last_exc is not None else RuntimeError("live_close_swap_failed")

    def _is_relaxed_demo_candidate(
        self,
        snap: TokenSnapshot,
        trend_hit: int,
        trader_hits: int,
        news_hits: int,
        community_hits: int,
        google_hits: int,
    ) -> bool:
        if not self._is_smallcap_memecoin_snapshot(snap):
            return False
        cap = self._meme_effective_cap_usd(snap)
        signal_hits = int(trend_hit) + int(trader_hits) + int(news_hits) + int(community_hits) + int(google_hits)
        min_liq = max(350.0, float(self.settings.dex_min_liquidity_usd) * 0.08)
        min_vol = max(120.0, float(self.settings.dex_min_5m_volume_usd) * 0.10)
        flow_ok = snap.buys_5m >= max(2, snap.sells_5m)
        interest_ok = signal_hits > 0 or snap.age_minutes <= 90.0
        launch_relaxed = bool(
            str(getattr(snap, "source", "") or "").lower().startswith("pumpfun")
            and 0.0 < float(cap) <= float(MEME_THEME_LAUNCH_HARD_MAX_CAP_USD)
            and float(snap.age_minutes) <= float(MEME_THEME_LAUNCH_MAX_AGE_MINUTES)
            and (
                (
                    float(snap.liquidity_usd) >= max(300.0, float(MEME_THEME_LAUNCH_MIN_LIQUIDITY_USD) * 0.50)
                    and float(snap.volume_5m_usd) >= max(100.0, float(MEME_THEME_LAUNCH_MIN_VOLUME_5M_USD) * 0.50)
                    and int(snap.buys_5m) >= 1
                )
                or self._is_theme_launch_zone(snap)
            )
        )
        return bool((snap.liquidity_usd >= min_liq and snap.volume_5m_usd >= min_vol and flow_ok and interest_ok) or launch_relaxed)

    @staticmethod
    def _demo_meme_score_floor(model_id: str) -> float:
        if model_id == "A":
            return 0.52
        if model_id == "B":
            return 0.50
        return 0.50

    @staticmethod
    def _meme_score_target_tp_pct(score: float) -> float:
        score_now = _clamp(float(score or 0.0), 0.0, 1.0)
        score_norm = _clamp((score_now - 0.50) / 0.40, 0.0, 1.0)
        return _clamp(0.10 + (0.10 * score_norm), 0.10, 0.20)

    def _meme_strategy_entry_sol(self, strategy_or_model_id: str) -> float:
        strategy_id = self._meme_strategy_id_for_model(strategy_or_model_id)
        if strategy_id == "THEME":
            return float(max(0.001, self.settings.meme_theme_entry_sol))
        if strategy_id == "NARRATIVE":
            return float(max(0.001, self.settings.meme_narrative_entry_sol))
        return float(max(0.001, self.settings.meme_launch_entry_sol))

    def _meme_partial_take_profit_pct(self, pos: dict[str, Any] | None = None) -> float:
        if isinstance(pos, dict) and float(pos.get("partial_tp_pct") or 0.0) > 0.0:
            return float(pos.get("partial_tp_pct") or 0.0)
        return float(max(0.01, self.settings.meme_partial_take_profit_pct))

    def _meme_partial_take_profit_sell_ratio(self, pos: dict[str, Any] | None = None) -> float:
        if isinstance(pos, dict) and float(pos.get("partial_tp_sell_ratio") or 0.0) > 0.0:
            return float(pos.get("partial_tp_sell_ratio") or 0.0)
        return float(_clamp(self.settings.meme_partial_take_profit_sell_ratio, 0.01, 1.0))

    def _meme_exit_rule_text(self, pos: dict[str, Any] | None = None) -> str:
        row = dict(pos or {})
        partial_tp_pct = self._meme_partial_take_profit_pct(row) * 100.0
        partial_ratio = self._meme_partial_take_profit_sell_ratio(row) * 100.0
        sl_pct = float(row.get("sl_pct") or 0.0) * 100.0
        parts = [f"+{partial_tp_pct:.0f}%시 {partial_ratio:.0f}% 매도"]
        if sl_pct > 0.0:
            parts.append(f"SL {sl_pct:.0f}%")
        if bool(row.get("partial_tp_done")):
            parts.append("부분익절 완료")
        return " | ".join(parts)

    def _meme_min_entry_rank_for_model(self, model_id: str) -> int:
        base_grade = str(self.settings.meme_min_entry_grade or "E").upper()
        base_rank = self._grade_rank(base_grade)
        return max(base_rank, self._grade_rank("E"))

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
            and (not self._is_live_trade_row(t))
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

    @staticmethod
    def _run_loss_guard(run: dict[str, Any]) -> dict[str, Any]:
        row = dict(run.get("loss_guard") or {})
        return {
            "active": bool(row.get("active", False)),
            "threshold_boost": float(row.get("threshold_boost") or 0.0),
            "order_mul": _clamp(float(row.get("order_mul") or 1.0), 0.20, 1.0),
            "reason": str(row.get("reason") or ""),
            "trigger_ts": int(row.get("trigger_ts") or 0),
        }

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
        peer_info: dict[str, Any] | None = None,
    ) -> dict[str, float]:
        pattern = dict(wallet_pattern or {})
        peer = dict(peer_info or {})
        liq_log = min(1.0, math.log10(1.0 + snap.liquidity_usd) / 7.0)
        vol_log = min(1.0, math.log10(1.0 + snap.volume_5m_usd) / 6.0)
        age_freshness = _clamp(1.0 - (snap.age_minutes / 240.0), 0.0, 1.0)
        age_stability = _clamp(snap.age_minutes / 180.0, 0.0, 1.0)
        cap_usd = float(self._meme_effective_cap_usd(snap))
        buy_sell_ratio = _clamp(snap.buy_sell_ratio / 2.2, 0.0, 1.0)
        tx_total = max(1.0, float(snap.buys_5m + snap.sells_5m))
        tx_flow_raw = (float(snap.buys_5m) - float(snap.sells_5m)) / tx_total
        tx_flow = _clamp((tx_flow_raw + 1.0) / 2.0, 0.0, 1.0)
        trend_strength = _clamp(0.55 * trend_hit + min(0.45, trader_hits * 0.12), 0.0, 1.0)
        trader_strength = _clamp(trader_hits / 4.0, 0.0, 1.0)
        wallet_strength = _clamp(wallet_hits / 4.0, 0.0, 1.0)
        news_strength = _clamp(news_hits / 4.0, 0.0, 1.0)
        community_strength = _clamp(community_hits / 5.0, 0.0, 1.0)
        google_strength = _clamp(google_hits / 6.0, 0.0, 1.0)
        new_meme_quality = _clamp((liq_log * 0.45) + (vol_log * 0.55), 0.0, 1.0) * age_freshness
        new_meme_instant = 1.0 if (snap.age_minutes <= 8 and snap.buys_5m >= max(3, snap.sells_5m + 1)) else 0.0
        is_pump_fun = 1.0 if str(snap.token_address or "").strip().lower().endswith("pump") else 0.0
        launch_source = 1.0 if str(getattr(snap, "source", "") or "").strip().lower().startswith(("pumpfun", "bonk")) else 0.0
        if float(MEME_THEME_LAUNCH_IDEAL_MIN_CAP_USD) <= cap_usd <= float(MEME_THEME_LAUNCH_IDEAL_MAX_CAP_USD):
            launch_cap_fit = 1.0
        elif cap_usd > 0.0 and cap_usd <= float(MEME_THEME_LAUNCH_HARD_MAX_CAP_USD):
            launch_cap_fit = _clamp(1.0 - (abs(cap_usd - 4_000.0) / 8_000.0), 0.0, 1.0)
        else:
            launch_cap_fit = 0.0
        launch_age_fit = _clamp(1.0 - (float(snap.age_minutes or 0.0) / float(max(MEME_THEME_LAUNCH_MAX_AGE_MINUTES, 1.0))), 0.0, 1.0)
        theme_launch_fit = _clamp(launch_source * launch_cap_fit * launch_age_fit, 0.0, 1.0)
        theme_launch_ready = 1.0 if self._is_theme_launch_zone(snap) else 0.0
        if MEME_SNIPER_MIN_CAP_USD <= cap_usd <= MEME_SNIPER_MAX_CAP_USD:
            sniper_cap_fit = 1.0
        elif cap_usd > 0.0 and cap_usd < MEME_SNIPER_MIN_CAP_USD:
            sniper_cap_fit = _clamp(cap_usd / max(MEME_SNIPER_MIN_CAP_USD, 1.0), 0.0, 1.0)
        elif cap_usd > MEME_SNIPER_MAX_CAP_USD:
            sniper_cap_fit = _clamp(1.0 - ((cap_usd - MEME_SNIPER_MAX_CAP_USD) / max(MEME_SNIPER_MAX_CAP_USD, 1.0)), 0.0, 1.0)
        else:
            sniper_cap_fit = 0.0
        sniper_social_burst = _clamp(
            (0.34 * trader_strength)
            + (0.20 * community_strength)
            + (0.14 * news_strength)
            + (0.06 * google_strength)
            + (0.14 * trend_strength)
            + (0.06 * wallet_strength)
            + (0.06 * tx_flow)
            + (0.04 * buy_sell_ratio),
            0.0,
            1.0,
        )
        sniper_signal_fit = _clamp(
            (0.44 * sniper_social_burst)
            + (0.18 * sniper_cap_fit)
            + (0.14 * tx_flow)
            + (0.10 * buy_sell_ratio)
            + (0.08 * vol_log)
            + (0.06 * liq_log),
            0.0,
            1.0,
        )
        spread_proxy = _clamp(1.0 - (snap.liquidity_usd / (snap.liquidity_usd + 140_000.0)), 0.0, 1.0)
        noise_penalty = 1.0 if tx_total <= 3 else 0.0
        smart_wallet_score = _clamp(float(pattern.get("smart_wallet_score") or 0.50), 0.0, 1.0)
        holder_risk = _clamp(float(pattern.get("holder_risk") or 0.50), 0.0, 1.0)
        transfer_diversity = _clamp(float(pattern.get("transfer_diversity") or 0.0), 0.0, 1.0)
        wallet_pattern_available = 1.0 if bool(pattern.get("available")) else 0.0
        wallet_suspicious = 1.0 if bool(pattern.get("suspicious")) else 0.0
        whale_count_norm = _clamp(float(pattern.get("whale_count_ge_1pct") or 0.0) / 40.0, 0.0, 1.0)
        similar_token_count = max(0.0, float(peer.get("similar_token_count") or 0.0))
        similar_token_count_norm = _clamp(float(peer.get("similar_token_count_norm") or 0.0), 0.0, 1.0)
        theme_confirmation = _clamp(float(peer.get("theme_confirmation") or 0.0), 0.0, 1.0)
        theme_leader_score = _clamp(float(peer.get("theme_leader_score") or 0.0), 0.0, 1.0)
        clone_pressure = _clamp(float(peer.get("clone_pressure") or 0.0), 0.0, 1.0)
        late_clone_pressure = _clamp(float(peer.get("late_clone_pressure") or 0.0), 0.0, 1.0)
        holder_overlap_max = _clamp(float(peer.get("holder_overlap_max") or 0.0), 0.0, 1.0)
        holder_overlap_mean = _clamp(float(peer.get("holder_overlap_mean") or 0.0), 0.0, 1.0)
        holder_overlap_weighted_max = _clamp(float(peer.get("holder_overlap_weighted_max") or 0.0), 0.0, 1.0)
        holder_overlap_peer_count = max(0.0, float(peer.get("holder_overlap_peer_count") or 0.0))
        holder_overlap_risk = _clamp(float(peer.get("holder_overlap_risk") or 0.0), 0.0, 1.0)
        holder_overlap_clean = _clamp(float(peer.get("holder_overlap_clean") or 0.0), 0.0, 1.0)
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
            "theme_launch_fit": theme_launch_fit,
            "theme_launch_ready": theme_launch_ready,
            "sniper_cap_fit": sniper_cap_fit,
            "sniper_social_burst": sniper_social_burst,
            "sniper_signal_fit": sniper_signal_fit,
            "spread_proxy": spread_proxy,
            "noise_penalty": noise_penalty,
            "smart_wallet_score": smart_wallet_score,
            "holder_risk": holder_risk,
            "transfer_diversity": transfer_diversity,
            "wallet_pattern_available": wallet_pattern_available,
            "wallet_suspicious": wallet_suspicious,
            "whale_count_norm": whale_count_norm,
            "similar_token_count": similar_token_count,
            "similar_token_count_norm": similar_token_count_norm,
            "theme_confirmation": theme_confirmation,
            "theme_leader_score": theme_leader_score,
            "clone_pressure": clone_pressure,
            "late_clone_pressure": late_clone_pressure,
            "holder_overlap_max": holder_overlap_max,
            "holder_overlap_mean": holder_overlap_mean,
            "holder_overlap_weighted_max": holder_overlap_weighted_max,
            "holder_overlap_peer_count": holder_overlap_peer_count,
            "holder_overlap_risk": holder_overlap_risk,
            "holder_overlap_clean": holder_overlap_clean,
        }

    @staticmethod
    def _heuristic_score(features: dict[str, float]) -> float:
        base = 0.0
        base += 0.24 * features.get("trend_strength", 0.0)
        base += 0.19 * features.get("trader_strength", 0.0)
        base += 0.08 * features.get("wallet_strength", 0.0)
        base += 0.07 * features.get("news_strength", 0.0)
        base += 0.08 * features.get("community_strength", 0.0)
        base += 0.02 * features.get("google_strength", 0.0)
        base += 0.12 * features.get("buy_sell_ratio", 0.0)
        base += 0.12 * features.get("tx_flow", 0.0)
        base += 0.07 * features.get("liq_log", 0.0)
        base += 0.07 * features.get("vol_log", 0.0)
        base += 0.09 * features.get("new_meme_quality", 0.0)
        base += 0.09 * features.get("new_meme_instant", 0.0)
        base += 0.07 * features.get("is_pump_fun", 0.0)
        base += 0.14 * features.get("theme_launch_fit", 0.0)
        base += 0.10 * features.get("theme_launch_ready", 0.0)
        base += 0.12 * features.get("sniper_social_burst", 0.0)
        base += 0.10 * features.get("sniper_signal_fit", 0.0)
        base += 0.08 * features.get("sniper_cap_fit", 0.0)
        base += 0.09 * features.get("smart_wallet_score", 0.0)
        base += 0.04 * features.get("transfer_diversity", 0.0)
        base += 0.06 * features.get("theme_confirmation", 0.0)
        base += 0.05 * features.get("theme_leader_score", 0.0)
        base += 0.04 * features.get("holder_overlap_clean", 0.0)
        base -= 0.07 * features.get("spread_proxy", 0.0)
        base -= 0.09 * features.get("noise_penalty", 0.0)
        base -= 0.10 * features.get("holder_risk", 0.0)
        base -= 0.10 * features.get("clone_pressure", 0.0)
        base -= 0.12 * features.get("late_clone_pressure", 0.0)
        base -= 0.14 * features.get("holder_overlap_risk", 0.0)
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
        tags: list[str] = [self._display_model_name(model_id, "meme"), f"등급{grade}"]
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
        if features.get("sniper_social_burst", 0.0) >= 0.58:
            tags.append("소셜버스트")
        if features.get("sniper_cap_fit", 0.0) >= 0.95:
            tags.append("1k-50k")
        if features.get("theme_launch_ready", 0.0) > 0.0:
            tags.append("런치존3-5k")
        elif features.get("theme_launch_fit", 0.0) >= 0.60:
            tags.append("런치근접")
        if features.get("buy_sell_ratio", 0.0) >= 0.55:
            tags.append("매수우위")
        similar_count = int(float(features.get("similar_token_count") or 0.0))
        if similar_count > 0:
            tags.append(f"유사{similar_count}")
        if features.get("theme_leader_score", 0.0) >= 0.8:
            tags.append("테마선두")
        if features.get("late_clone_pressure", 0.0) >= 0.55:
            tags.append("복제혼잡")
        if features.get("holder_overlap_risk", 0.0) >= 0.35:
            tags.append("홀더겹침")
        if features.get("smart_wallet_score", 0.0) >= 0.65:
            tags.append("지갑패턴양호")
        if features.get("wallet_pattern_available", 0.0) < 0.5:
            tags.append("지갑패턴없음")
        if features.get("wallet_suspicious", 0.0) >= 0.5:
            tags.append("지갑패턴의심")
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
            if model_id == "C":
                sl = float(MEME_C_FIXED_SL_PCT)
            else:
                sl = base_sl * sl_mul * (0.80 + (0.95 * vol))
            return (_clamp(sl, 0.015, 0.30), _clamp(tp, 0.05, 0.80))

        with self._lock:
            run = (self.state.model_runs or {}).get(self._market_run_key("crypto", model_id)) or {}
        tune = self._read_model_runtime_tune_from_run(run if isinstance(run, dict) else {}, model_id, int(time.time()))
        if model_id in {"A", "D"}:  # strict quality trend / smallcap scalp
            tp_mul = float(tune.get("tp_mul") or 0.98)
            sl_mul = float(tune.get("sl_mul") or 0.78)
            tp = base_tp * tp_mul * (0.82 + (0.58 * vol))
            sl = base_sl * sl_mul * (0.68 + (0.62 * vol))
            return (_clamp(sl, 0.008, 0.10), _clamp(tp, 0.03, 0.24))
        if model_id == "B":  # trend-flow balance
            tp_mul = float(tune.get("tp_mul") or 1.08)
            sl_mul = float(tune.get("sl_mul") or 0.84)
            tp = base_tp * tp_mul * (0.82 + (0.56 * vol))
            sl = base_sl * sl_mul * (0.64 + (0.72 * vol))
            return (_clamp(sl, 0.008, 0.11), _clamp(tp, 0.03, 0.28))
        # C: aggressive momentum capture
        tp_mul = float(tune.get("tp_mul") or 1.32)
        sl_mul = float(tune.get("sl_mul") or 0.88)
        tp = base_tp * tp_mul * (0.92 + (0.86 * vol))
        sl = base_sl * sl_mul * (0.70 + (0.84 * vol))
        return (_clamp(sl, 0.010, 0.16), _clamp(tp, 0.05, 0.40))

    def _demo_order_pct_for_entry(self, market: str, score: float, threshold: float) -> float:
        if str(market).lower() == "crypto":
            min_pct = _clamp(float(getattr(self.settings, "bybit_order_pct_min", 0.15) or 0.15), 0.01, 0.95)
            max_pct = _clamp(float(getattr(self.settings, "bybit_order_pct_max", 0.40) or 0.40), min_pct, 0.95)
            gap = float(score) - float(threshold)
            confidence = _clamp(gap / 0.22, 0.0, 1.0)
            return float(min_pct + ((max_pct - min_pct) * confidence))
        min_pct = _clamp(float(self.settings.demo_order_pct_min), 0.01, 0.95)
        max_pct = _clamp(float(self.settings.demo_order_pct_max), min_pct, 0.95)
        gap = float(score) - float(threshold)
        # Both meme/crypto use 0~1 score in entry sizing.
        scale = 0.30 if str(market).lower() == "meme" else 0.35
        confidence = _clamp(gap / max(1e-6, scale), 0.0, 1.0)
        return float(min_pct + ((max_pct - min_pct) * confidence))

    def _crypto_target_order_usd(
        self,
        run: dict[str, Any],
        order_pct: float,
        prices: dict[str, float] | None = None,
    ) -> float:
        cash = float(run.get("bybit_cash_usd") or 0.0)
        positions = list((run.get("bybit_positions") or {}).values())
        position_equity = 0.0
        for pos in positions:
            symbol = str((pos or {}).get("symbol") or "")
            current = 0.0
            if isinstance(prices, dict) and symbol:
                current = float(prices.get(symbol) or 0.0)
            marked = self._mark_crypto_position(pos, current)
            position_equity += float(marked.get("position_equity_usd") or 0.0)
        total_equity = max(0.0, cash + position_equity)
        alloc_pct = _clamp(float(order_pct or self.settings.bybit_order_pct or 0.30), 0.01, 0.95)
        return float(total_equity * alloc_pct)

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
    def _crypto_reentry_cooldown_seconds(model_id: str, hard_stop: bool) -> int:
        if hard_stop:
            table = {"A": 20 * 60, "B": 45 * 60, "C": 30 * 60}
            return int(table.get(model_id, 20 * 60))
        table = {"A": 10 * 60, "B": 20 * 60, "C": 15 * 60}
        return int(table.get(model_id, 10 * 60))

    @staticmethod
    def _normalize_crypto_reentry_cooldowns(run: dict[str, Any], now_ts: int | None = None) -> dict[str, dict[str, Any]]:
        now = int(now_ts or int(time.time()))
        rows = dict(run.get("crypto_reentry_cooldowns") or {})
        out: dict[str, dict[str, Any]] = {}
        for symbol, row in rows.items():
            sym = str(symbol or "").upper().strip()
            if not sym:
                continue
            item = dict(row or {})
            until_ts = int(item.get("until_ts") or 0)
            if until_ts <= now:
                continue
            out[sym] = {
                "until_ts": int(until_ts),
                "set_ts": int(item.get("set_ts") or now),
                "reason": str(item.get("reason") or ""),
            }
        run["crypto_reentry_cooldowns"] = out
        return out

    def _set_crypto_reentry_cooldown(
        self,
        run: dict[str, Any],
        model_id: str,
        symbol: str,
        reason: str,
        now_ts: int | None = None,
    ) -> None:
        now = int(now_ts or int(time.time()))
        sym = str(symbol or "").upper().strip()
        if not sym:
            return
        reason_text = str(reason or "")
        reason_u = reason_text.upper()
        hard_stop = ("HARD-ROE" in reason_u) or ("LIQ" in reason_u)
        seconds = self._crypto_reentry_cooldown_seconds(model_id, hard_stop=hard_stop)
        state = self._normalize_crypto_reentry_cooldowns(run, now)
        state[sym] = {
            "until_ts": int(now + max(60, seconds)),
            "set_ts": int(now),
            "reason": reason_text,
        }
        run["crypto_reentry_cooldowns"] = state

    def _crypto_reentry_blocked(
        self,
        run: dict[str, Any],
        symbol: str,
        now_ts: int | None = None,
    ) -> tuple[bool, int, str]:
        now = int(now_ts or int(time.time()))
        sym = str(symbol or "").upper().strip()
        if not sym:
            return (False, 0, "")
        state = self._normalize_crypto_reentry_cooldowns(run, now)
        row = dict(state.get(sym) or {})
        until_ts = int(row.get("until_ts") or 0)
        if until_ts <= now:
            return (False, 0, "")
        remain = max(1, until_ts - now)
        return (True, remain, str(row.get("reason") or ""))

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

    def _resolve_price_cached(self, token_address: str, fallback: float = 0.0) -> float:
        price = float(self._last_prices.get(token_address) or 0.0)
        if price > 0.0:
            return price
        return max(0.0, float(fallback or 0.0))

    @staticmethod
    def _is_live_trade_row(row: dict[str, Any]) -> bool:
        mode = str((row or {}).get("mode") or "").strip().lower()
        if mode == "live":
            return True
        if mode == "paper":
            return False
        # Backward compatibility: older live rows may miss `mode` but include live tx marker.
        reason = str((row or {}).get("reason") or "").strip().lower()
        if "live_tx=" in reason:
            return True
        source = str((row or {}).get("source") or "").strip().lower()
        if source in {"memecoin_live", "crypto_live", "live_memecoin", "live_crypto"}:
            return True
        return False

    def _evaluate_model_memecoin_exits(self, model_id: str, run: dict[str, Any]) -> None:
        now = int(time.time())
        for pos in list((run.get("meme_positions") or {}).values()):
            retry_after = int((pos or {}).get("close_retry_after_ts") or 0)
            if retry_after > now:
                continue
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
            partial_tp_pct = float(pos.get("partial_tp_pct") or self.settings.meme_partial_take_profit_pct)
            partial_tp_sell_ratio = float(pos.get("partial_tp_sell_ratio") or self.settings.meme_partial_take_profit_sell_ratio)
            partial_tp_done = bool(pos.get("partial_tp_done"))
            if str(model_id).upper() == "C":
                sl_pct = float(MEME_C_FIXED_SL_PCT)
                pos["sl_pct"] = float(MEME_C_FIXED_SL_PCT)
            strategy = str(pos.get("strategy") or "scalp").lower()
            peak = float(pos.get("peak_price_usd") or entry)
            if current_price > peak:
                peak = current_price
                pos["peak_price_usd"] = peak

            if (not partial_tp_done) and partial_tp_pct > 0.0 and pnl_pct >= partial_tp_pct:
                did_partial = self._close_model_memecoin_position(
                    model_id,
                    run,
                    pos,
                    current_price,
                    f"PARTIAL +{pnl_pct * 100:.2f}% {partial_tp_sell_ratio * 100:.0f}%매도",
                    close_fraction=partial_tp_sell_ratio,
                )
                if did_partial:
                    continue

            if strategy == "swing":
                hold_until_ts = int(pos.get("hold_until_ts") or 0)
                trail_pct = float(pos.get("trailing_stop_pct") or self.settings.meme_swing_trailing_stop_pct)
                if model_id == "B":
                    hard_sl_pct = float(pos.get("catastrophic_sl_pct") or max(sl_pct, 0.30))
                    if self.settings.solscan_enable_pattern and self.solscan.enabled:
                        last_check = int(pos.get("last_wallet_check_ts") or 0)
                        if (now - last_check) >= 3600:
                            pat = self._get_wallet_pattern(token_address, now_ts=now)
                            pos["last_wallet_check_ts"] = now
                            if bool(pat.get("available")) and bool(pat.get("suspicious")) and pnl_pct <= 0.10:
                                self._close_model_memecoin_position(
                                    model_id,
                                    run,
                                    pos,
                                    current_price,
                                    f"Swing wallet-risk-spike {pnl_pct * 100:.2f}%",
                                )
                                continue
                    if pnl_pct <= -hard_sl_pct:
                        self._close_model_memecoin_position(
                            model_id, run, pos, current_price, f"Swing Hard-SL {pnl_pct * 100:.2f}%"
                        )
                    elif peak > entry and pnl_pct >= 0.40 and current_price <= (peak * (1.0 - trail_pct)):
                        self._close_model_memecoin_position(
                            model_id,
                            run,
                            pos,
                            current_price,
                            f"Swing trailing-stop {pnl_pct * 100:.2f}%",
                        )
                    elif hold_until_ts > 0 and now >= hold_until_ts:
                        if pnl_pct >= -0.12:
                            self._close_model_memecoin_position(
                                model_id,
                                run,
                                pos,
                                current_price,
                                f"Swing horizon-end {pnl_pct * 100:.2f}%",
                            )
                        else:
                            ext_n = int(pos.get("hold_ext_count") or 0)
                            if ext_n < 2:
                                pos["hold_ext_count"] = ext_n + 1
                                pos["hold_until_ts"] = now + (3 * 86400)
                    continue
                if pnl_pct <= -sl_pct:
                    self._close_model_memecoin_position(model_id, run, pos, current_price, f"Swing SL {pnl_pct * 100:.2f}%")
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
                if pnl_pct <= -sl_pct:
                    if str(model_id).upper() == "C":
                        sl_breach_ts = int(pos.get("sl_breach_ts") or 0)
                        if sl_breach_ts <= 0:
                            pos["sl_breach_ts"] = int(now)
                            continue
                        if (int(now) - int(sl_breach_ts)) < int(MEME_C_SL_CONFIRM_SECONDS):
                            continue
                    self._close_model_memecoin_position(model_id, run, pos, current_price, f"SL {pnl_pct * 100:.2f}%")
                elif str(model_id).upper() == "C":
                    recover_level = -float(sl_pct) * float(MEME_C_SL_RECOVERY_RESET_FACTOR)
                    if pnl_pct > recover_level:
                        pos.pop("sl_breach_ts", None)

    def _close_model_memecoin_position(
        self,
        model_id: str,
        run: dict[str, Any],
        pos: dict[str, Any],
        price_usd: float,
        reason: str,
        close_fraction: float = 1.0,
    ) -> bool:
        token_address = str(pos.get("token_address") or "")
        if not token_address:
            return False
        positions = run.get("meme_positions") or {}
        is_live_position = bool(str(pos.get("mode") or "").lower() == "live")
        position_key = ""
        for key, row in positions.items():
            if row is pos:
                position_key = str(key)
                break
        if not position_key:
            for key, row in positions.items():
                token = str((row or {}).get("token_address") or "")
                row_live = bool(str((row or {}).get("mode") or "").lower() == "live")
                if token == token_address and row_live == is_live_position:
                    position_key = str(key)
                    break
        if not position_key:
            return False

        now_ts = int(time.time())
        is_live_market = self._is_live_execution_market("meme")
        requested_fraction = _clamp(float(close_fraction or 1.0), 0.01, 1.0)
        original_qty = max(0.0, float(pos.get("qty") or 0.0))
        requested_close_qty = original_qty * requested_fraction
        requested_full_close = requested_fraction >= 0.999
        close_signature = ""
        live_accounting: dict[str, Any] = {}
        if is_live_market and is_live_position:
            wallet_row = self._wallet_asset_row(token_address)
            wallet_qty = float(wallet_row.get("qty") or 0.0)
            raw_amount = int(wallet_row.get("raw_amount") or 0)
            decimals = int(wallet_row.get("decimals") or 0)
            if raw_amount <= 0 and self.settings.phantom_wallet_address:
                try:
                    raw_row = self.wallet.get_token_balance_raw(self.settings.phantom_wallet_address, token_address)
                except Exception:
                    raw_row = {}
                raw_amount = max(raw_amount, int(raw_row.get("raw_amount") or 0))
                decimals = max(decimals, int(raw_row.get("decimals") or 0))
                wallet_qty = max(wallet_qty, float(raw_row.get("qty") or 0.0))
            if raw_amount <= 0 and wallet_qty > 0.0:
                raw_amount = int(wallet_qty * (10**max(0, decimals)))
            if raw_amount <= 0 and wallet_qty <= 0.0:
                # Wallet already no longer holds this token: close managed position as orphan cleanup
                # and do not fabricate realized PNL for a close the bot did not execute.
                close_fail_streak = int(pos.get("close_fail_streak") or 0)
                del positions[position_key]
                run["meme_positions"] = positions
                with self._lock:
                    # Cleanup succeeded, so stale close-failed state should not keep notifying.
                    if "close_failed:" in str(self.state.memecoin_error or ""):
                        self.state.memecoin_error = ""
                try:
                    self.runtime_feedback.append_event(
                        source="live:meme_close",
                        level="info",
                        status="reconciled_after_wallet_miss" if close_fail_streak > 0 else "wallet_miss_cleanup",
                        error="no_wallet_balance_for_live_position",
                        action="지갑 미보유 토큰 포지션을 정리했습니다. 지갑 동기화 상태를 확인하세요.",
                        detail=(
                            f"{pos.get('symbol')} 실전 포지션을 지갑 미보유 상태로 정리했습니다."
                            if close_fail_streak <= 0
                            else (
                                f"{pos.get('symbol')} 실전 포지션을 재시도 {int(close_fail_streak)}회 후 "
                                "지갑 미보유 상태로 정리했습니다."
                            )
                        ),
                        meta={
                            "symbol": str(pos.get("symbol") or ""),
                            "model_id": str(model_id),
                            "close_fail_streak": int(close_fail_streak),
                        },
                        now_ts=int(now_ts),
                    )
                except Exception:
                    pass
                return True
            if raw_amount > 0:
                close_exc: Exception | None = None
                before_snapshot = self._capture_live_wallet_snapshot(token_address)
                try:
                    swap_result, close_venue = self._live_meme_sell(
                        token_address=token_address,
                        position=pos,
                        close_fraction=requested_fraction,
                        raw_amount=raw_amount,
                        wallet_qty=wallet_qty,
                    )
                    close_signature = str(swap_result.get("signature") or "")
                    self._mark_pending_live_trade_signature(close_signature, now_ts)
                    pos.pop("close_fail_streak", None)
                    pos.pop("close_last_error", None)
                    pos.pop("close_last_error_ts", None)
                    pos.pop("close_retry_after_ts", None)
                    self._sync_wallet(int(time.time()), force=True)
                    live_accounting = self._apply_live_swap_accounting(
                        token_address=token_address,
                        swap_signature=close_signature,
                        before_snapshot=before_snapshot,
                        now_ts=int(now_ts),
                        side="sell",
                        fallback_sol_price_usd=float(self._live_sol_price_usd()),
                    )
                    live_accounting["execution_venue"] = str(close_venue or "")
                    with self._lock:
                        self.state.memecoin_error = ""
                except Exception as exc:
                    close_exc = exc
                if not close_signature:
                    err_obj = close_exc if close_exc is not None else RuntimeError("live_close_swap_failed")
                    retry_secs = 300
                    err_low = str(err_obj).lower()
                    if "token_not_tradable" in err_low or "not tradable" in err_low:
                        retry_secs = 1800
                    elif "429" in err_low or "rate" in err_low:
                        retry_secs = 600
                    close_fail_streak = int(pos.get("close_fail_streak") or 0) + 1
                    pos["close_fail_streak"] = int(close_fail_streak)
                    pos["close_last_error"] = str(err_obj)[:320]
                    pos["close_last_error_ts"] = int(now_ts)
                    pos["close_retry_after_ts"] = int(now_ts) + int(retry_secs)
                    with self._lock:
                        self.state.memecoin_error = f"{pos.get('symbol')}:close_failed:{err_obj}"
                    should_alert = ("token_not_tradable" in err_low or "not tradable" in err_low) or (
                        int(close_fail_streak) >= int(LIVE_MEME_CLOSE_ALERT_STREAK)
                    )
                    if should_alert:
                        self._emit_runtime_error(
                            f"live:meme_close:{token_address}",
                            f"{pos.get('symbol')} 실전 청산 실패",
                            f"{err_obj} | retry={int(retry_secs)}s | streak={int(close_fail_streak)}",
                            cooldown_seconds=LIVE_MEME_CLOSE_ALERT_COOLDOWN_SECONDS,
                        )
                    else:
                        try:
                            self.runtime_feedback.append_event(
                                source="live:meme_close",
                                level="warn",
                                status="close_retry_pending",
                                error=str(err_obj),
                                action="청산 실패 재시도 중입니다. 연속 실패 누적 시 경고를 승격 전송합니다.",
                                detail=(
                                    f"{pos.get('symbol')} 실전 청산 재시도 대기: "
                                    f"streak={int(close_fail_streak)}, retry={int(retry_secs)}s"
                                ),
                                meta={
                                    "symbol": str(pos.get("symbol") or ""),
                                    "model_id": str(model_id),
                                    "streak": int(close_fail_streak),
                                    "retry_after_ts": int(pos.get("close_retry_after_ts") or 0),
                                },
                                now_ts=int(now_ts),
                            )
                        except Exception:
                            pass
                    return False

        qty = float(pos.get("qty") or 0.0)
        avg = float(pos.get("avg_price_usd") or 0.0)
        actual_qty = max(0.0, float(live_accounting.get("sold_qty") or 0.0)) if is_live_position else 0.0
        actual_proceeds_usd = max(0.0, float(live_accounting.get("proceeds_usd") or 0.0)) if is_live_position else 0.0
        actual_exit_price_usd = max(0.0, float(live_accounting.get("avg_exit_price_usd") or 0.0)) if is_live_position else 0.0
        fee_usd = max(0.0, float(live_accounting.get("fee_usd") or 0.0)) if is_live_position else 0.0
        close_qty = float(actual_qty or requested_close_qty or qty)
        if close_qty <= 0.0:
            close_qty = float(requested_close_qty or qty)
        if is_live_position:
            notional = float(actual_proceeds_usd or 0.0)
            if notional <= 0.0:
                notional = close_qty * max(0.0, float(price_usd or avg))
            exit_price_usd = float(actual_exit_price_usd or (notional / max(close_qty, 1e-12) if close_qty > 0.0 else 0.0))
        else:
            notional = close_qty * price_usd
            exit_price_usd = float(price_usd)
        cost_basis = float(avg * close_qty)
        pnl_usd = float(notional - cost_basis)
        pnl_pct = float(pnl_usd / max(0.0001, cost_basis)) if cost_basis > 0.0 else 0.0
        remaining_qty = max(0.0, float(qty - close_qty))
        if is_live_position:
            after_token_raw = int(live_accounting.get("after_token_raw") or 0)
            token_decimals = int(live_accounting.get("token_decimals") or 0)
            if after_token_raw > 0 and token_decimals >= 0:
                remaining_qty = max(0.0, float(after_token_raw) / float(10**max(0, token_decimals)))
        full_close = bool(requested_full_close or remaining_qty <= max(1e-9, qty * 0.002))
        if full_close:
            del positions[position_key]
        else:
            row = dict(pos or {})
            row["qty"] = float(remaining_qty)
            row["peak_price_usd"] = float(max(float(row.get("peak_price_usd") or 0.0), float(price_usd or 0.0)))
            row["close_fail_streak"] = 0
            row["close_last_error"] = ""
            row["close_last_error_ts"] = 0
            row["close_retry_after_ts"] = 0
            if str(reason or "").upper().startswith("PARTIAL"):
                row["partial_tp_done"] = True
                row["partial_tp_done_ts"] = int(now_ts)
            positions[position_key] = row
        run["meme_positions"] = positions
        if not is_live_position:
            run["meme_cash_usd"] = float(run.get("meme_cash_usd") or 0.0) + notional
        run.setdefault("trades", []).append(
            {
                "ts": int(now_ts),
                "source": "memecoin",
                "side": "sell",
                "symbol": str(pos.get("symbol") or ""),
                "token_address": token_address,
                "qty": float(close_qty),
                "price_usd": float(exit_price_usd),
                "notional_usd": float(notional),
                "pnl_usd": float(pnl_usd),
                "pnl_pct": float(pnl_pct),
                "reason": reason + (f"|live_tx={close_signature}" if close_signature else ""),
                "model_id": model_id,
                "strategy_id": str(
                    self._meme_strategy_id_from_signal_context(
                        features=dict(pos.get("entry_features") or {}),
                        reason=str(pos.get("reason") or ""),
                        current_strategy_id=str(
                            pos.get("engine_strategy_id")
                            or self._meme_strategy_id_for_model(str(pos.get("model_id") or model_id))
                        ),
                    )
                ),
                "mode": "live" if is_live_position else "paper",
                "partial": not bool(full_close),
                "realized_pnl_usd": float(pnl_usd),
                "realized_pnl_pct": float(pnl_pct),
                "network_fee_usd": float(fee_usd),
                "before_sol_lamports": int(live_accounting.get("before_sol_lamports") or 0),
                "after_sol_lamports": int(live_accounting.get("after_sol_lamports") or 0),
                "before_token_raw": int(live_accounting.get("before_token_raw") or 0),
                "after_token_raw": int(live_accounting.get("after_token_raw") or 0),
                "realized": True,
                "accounting_version": int(LIVE_ACCOUNTING_SCHEMA_VERSION) if is_live_position else 0,
            }
        )
        reason_u = str(reason or "").upper().strip()
        if full_close and str(model_id).upper() == "C" and reason_u.startswith("SL"):
            sym = str(pos.get("symbol") or "").upper().strip()
            if sym:
                mode_prefix = "live" if is_live_position else "paper"
                sym_key = f"{mode_prefix}:{sym}"
                reentry_map = dict(run.get("meme_reentry_after_ts") or {})
                reentry_map[sym_key] = int(now_ts) + int(MEME_C_REENTRY_WAIT_SECONDS)
                if len(reentry_map) > 400:
                    items = sorted(reentry_map.items(), key=lambda kv: int(kv[1] or 0), reverse=True)[:240]
                    reentry_map = {str(k): int(v or 0) for k, v in items}
                run["meme_reentry_after_ts"] = reentry_map
        self._prune_run_trades(run, int(now_ts))

        if model_id == "A":
            entry_features = dict(pos.get("entry_features") or {})
            if entry_features and full_close:
                self.model.update(entry_features, pnl_pct)
            self._push_alert(
                "trade",
                f"[{self._display_model_name('A', 'meme')}] {pos.get('symbol')} {'청산' if full_close else '부분청산'}",
                f"{reason} | PNL {pnl_usd:+.2f} USD ({pnl_pct * 100:+.2f}%)",
                send_telegram=True,
            )
        return True

    def _solana_trade_budget(self) -> dict[str, float]:
        reserve_sol = max(0.0, float(getattr(self.settings, "solana_reserve_sol", 0.01) or 0.01))
        with self._lock:
            wallet_assets = list(self.state.wallet_assets or [])
        sol_row = None
        for row in wallet_assets:
            if str((row or {}).get("symbol") or "").upper().strip() == "SOL":
                sol_row = dict(row or {})
                break
        if not sol_row:
            return {
                "reserve_sol": reserve_sol,
                "sol_qty": 0.0,
                "sol_price_usd": 0.0,
                "tradeable_sol": 0.0,
                "tradeable_usd": 0.0,
            }
        sol_qty = max(0.0, float(sol_row.get("qty") or 0.0))
        price_usd = max(0.0, float(sol_row.get("price_usd") or 0.0))
        if price_usd <= 0.0 and sol_qty > 0.0:
            price_usd = max(0.0, float(sol_row.get("value_usd") or 0.0) / max(sol_qty, 1e-12))
        tradeable_sol = max(0.0, sol_qty - reserve_sol)
        tradeable_usd = tradeable_sol * price_usd
        return {
            "reserve_sol": reserve_sol,
            "sol_qty": sol_qty,
            "sol_price_usd": price_usd,
            "tradeable_sol": tradeable_sol,
            "tradeable_usd": tradeable_usd,
        }

    def _wallet_asset_row(self, token_address: str) -> dict[str, Any]:
        token = str(token_address or "").strip()
        if not token:
            return {}
        with self._lock:
            rows = list(self.state.wallet_assets or [])
        for row in rows:
            if str((row or {}).get("token_address") or "").strip() == token:
                return dict(row or {})
        return {}

    def _live_meme_watch_tokens(self) -> set[str]:
        tokens: set[str] = set()
        with self._lock:
            runs = self.state.model_runs if isinstance(self.state.model_runs, dict) else {}
        if not isinstance(runs, dict):
            return tokens

        basis_map = dict(runs.get("_live_meme_basis") or {})
        for token in basis_map.keys():
            addr = str(token or "").strip()
            if addr:
                tokens.add(addr)

        for model_id in MEME_MODEL_IDS:
            meme_run = self._get_market_run(runs, "meme", model_id)
            for pos in dict(meme_run.get("meme_positions") or {}).values():
                addr = str((pos or {}).get("token_address") or "").strip()
                if addr:
                    tokens.add(addr)
            for tr in list(meme_run.get("trades") or []):
                if str((tr or {}).get("source") or "").strip().lower() != "memecoin":
                    continue
                if not self._is_live_trade_row(tr):
                    continue
                addr = str((tr or {}).get("token_address") or "").strip()
                if addr:
                    tokens.add(addr)
        return tokens

    def _refresh_live_meme_basis(self, wallet_rows: list[dict[str, Any]], now_ts: int) -> None:
        with self._lock:
            runs = self.state.model_runs if isinstance(self.state.model_runs, dict) else {}
            if not isinstance(runs, dict):
                runs = {}
                self.state.model_runs = runs
            basis_map: dict[str, dict[str, Any]] = {}
            cost_by_token: dict[str, dict[str, float]] = {}
            for model_id in MEME_MODEL_IDS:
                run = self._get_market_run(runs, "meme", model_id)
                rows = sorted(list(run.get("trades") or []), key=lambda row: int((row or {}).get("ts") or 0))
                for tr in rows:
                    if str((tr or {}).get("source") or "").strip().lower() != "memecoin":
                        continue
                    if not self._is_live_trade_row(tr):
                        continue
                    token = str((tr or {}).get("token_address") or "").strip()
                    if not token:
                        continue
                    qty = max(0.0, float((tr or {}).get("qty") or 0.0))
                    notional = max(0.0, float((tr or {}).get("notional_usd") or 0.0))
                    if qty <= 0.0:
                        continue
                    basis = cost_by_token.setdefault(token, {"qty": 0.0, "cost_usd": 0.0})
                    side = str((tr or {}).get("side") or "").strip().lower()
                    if side == "buy":
                        basis["qty"] = float(basis.get("qty") or 0.0) + qty
                        basis["cost_usd"] = float(basis.get("cost_usd") or 0.0) + notional
                        continue
                    close_qty = min(float(basis.get("qty") or 0.0), qty)
                    avg_cost = float(basis.get("cost_usd") or 0.0) / max(float(basis.get("qty") or 0.0), 1e-12)
                    basis["qty"] = max(0.0, float(basis.get("qty") or 0.0) - close_qty)
                    basis["cost_usd"] = max(0.0, float(basis.get("cost_usd") or 0.0) - (avg_cost * close_qty))
            for token, basis in list(cost_by_token.items()):
                qty = max(0.0, float((basis or {}).get("qty") or 0.0))
                cost_usd = max(0.0, float((basis or {}).get("cost_usd") or 0.0))
                if qty <= 0.0 or cost_usd <= 0.0:
                    continue
                basis_map[token] = {
                    "token_address": token,
                    "entry_price_usd": float(cost_usd / max(qty, 1e-12)),
                    "remaining_qty": float(qty),
                    "remaining_cost_usd": float(cost_usd),
                    "updated_ts": int(now_ts),
                    "source": "live_trade_rebuild",
                }
            for model_id in MEME_MODEL_IDS:
                run = self._get_market_run(runs, "meme", model_id)
                for pos in list((run.get("meme_positions") or {}).values()):
                    if str((pos or {}).get("mode") or "").strip().lower() != "live":
                        continue
                    if str((pos or {}).get("reason") or "").strip().lower() == "live_wallet_sync_seed":
                        continue
                    token = str((pos or {}).get("token_address") or "").strip()
                    avg_price = max(0.0, float((pos or {}).get("avg_price_usd") or 0.0))
                    qty = max(0.0, float((pos or {}).get("qty") or 0.0))
                    if not token or avg_price <= 0.0 or qty <= 0.0:
                        continue
                    basis_map[token] = {
                        "token_address": token,
                        "symbol": str((pos or {}).get("symbol") or ""),
                        "entry_price_usd": float(avg_price),
                        "remaining_qty": float(qty),
                        "remaining_cost_usd": float(avg_price * qty),
                        "updated_ts": int(now_ts),
                        "source": "live_position",
                    }
            runs["_live_meme_basis"] = basis_map

    def _live_target_meme_model(self) -> str:
        ids = list(self._live_model_ids("meme"))
        if ids:
            return str(ids[0])
        return "C"

    def _sync_live_wallet_managed_positions(self, now_ts: int) -> dict[str, int]:
        if not self._is_live_execution_market("meme"):
            return {"added": 0, "updated_qty": 0}

        target_model = self._live_target_meme_model()
        min_usd = float(self.settings.min_wallet_asset_usd or 1.0)
        added = 0
        updated_qty = 0
        with self._lock:
            runs = self.state.model_runs if isinstance(self.state.model_runs, dict) else {}
            if not isinstance(runs, dict):
                runs = {}
                self.state.model_runs = runs
            target_key = self._market_run_key("meme", target_model)
            if not isinstance(runs.get(target_key), dict):
                runs[target_key] = self._blank_market_run("meme", target_model, float(self.state.demo_seed_usdt or self.settings.demo_seed_usdt))
            target_run = runs.get(target_key) if isinstance(runs.get(target_key), dict) else {}
            self._normalize_market_run(target_run, "meme", target_model, float(self.state.demo_seed_usdt or self.settings.demo_seed_usdt))
            basis_map = dict(runs.get("_live_meme_basis") or {})
            wallet_rows = list(self.state.wallet_assets or [])

            wallet_map: dict[str, dict[str, Any]] = {}
            for row in wallet_rows:
                symbol = str((row or {}).get("symbol") or "").upper().strip()
                name = str((row or {}).get("name") or "").strip()
                token = str((row or {}).get("token_address") or "").strip()
                qty = float((row or {}).get("qty") or 0.0)
                value = float((row or {}).get("value_usd") or 0.0)
                if not token or qty <= 0.0 or value < min_usd:
                    continue
                if not self._is_memecoin_token(symbol, name, token):
                    continue
                wallet_map[token] = dict(row or {})

            existing_live_tokens: set[str] = set()
            for model_id in MEME_MODEL_IDS:
                run = self._get_market_run(runs, "meme", model_id)
                pos_map = dict(run.get("meme_positions") or {})
                changed = False
                for pos_key, pos in list(pos_map.items()):
                    if str((pos or {}).get("mode") or "").strip().lower() != "live":
                        continue
                    token = str((pos or {}).get("token_address") or "").strip()
                    if not token:
                        continue
                    basis_row = dict(basis_map.get(token) or {})
                    if (
                        str((pos or {}).get("reason") or "").strip().lower() == "live_wallet_sync_seed"
                        and float(basis_row.get("entry_price_usd") or 0.0) <= 0.0
                    ):
                        pos_map.pop(pos_key, None)
                        changed = True
                        continue
                    existing_live_tokens.add(token)
                    w = wallet_map.get(token)
                    if not w:
                        miss = int((pos or {}).get("wallet_miss_count") or 0) + 1
                        if miss >= 3:
                            pos_map.pop(pos_key, None)
                        else:
                            row = dict(pos or {})
                            row["wallet_miss_count"] = int(miss)
                            pos_map[pos_key] = row
                        continue
                    new_qty = max(0.0, float((w or {}).get("qty") or 0.0))
                    old_qty = max(0.0, float((pos or {}).get("qty") or 0.0))
                    if new_qty <= 0.0:
                        continue
                    row = dict(pos or {})
                    row["wallet_miss_count"] = 0
                    if abs(new_qty - old_qty) > max(1e-9, new_qty * 0.002):
                        row["qty"] = float(new_qty)
                        row["symbol"] = str((w or {}).get("symbol") or row.get("symbol") or "")
                        price = float((w or {}).get("price_usd") or 0.0)
                        if price > float(row.get("peak_price_usd") or 0.0):
                            row["peak_price_usd"] = float(price)
                        updated_qty += 1
                    pos_map[pos_key] = row
                    changed = True
                if changed:
                    run["meme_positions"] = pos_map

            target_positions = dict(target_run.get("meme_positions") or {})
            for token, row in wallet_map.items():
                if token in existing_live_tokens:
                    continue
                qty = max(0.0, float((row or {}).get("qty") or 0.0))
                price = max(0.0, float((row or {}).get("price_usd") or 0.0))
                value = max(0.0, float((row or {}).get("value_usd") or 0.0))
                if qty <= 0.0 or price <= 0.0 or value < min_usd:
                    continue
                symbol = str((row or {}).get("symbol") or "").upper().strip()
                basis_row = dict(basis_map.get(token) or {})
                avg_price = max(0.0, float(basis_row.get("entry_price_usd") or 0.0))
                if avg_price <= 0.0:
                    continue
                sl_pct, tp_pct = self._compute_risk_profile(target_model, "meme", 0.50)
                strategy_id = "THEME"
                strategy = "swing" if str(target_model) == "B" else "scalp"
                if strategy != "swing":
                    tp_pct = float(self._meme_score_target_tp_pct(0.50))
                else:
                    tp_pct = _clamp(tp_pct, 0.10, 0.20)
                hold_until_ts = 0
                trailing_stop_pct = 0.0
                if strategy == "swing":
                    hold_until_ts = int(now_ts + (int(self.settings.meme_swing_hold_days) * 86400))
                    trailing_stop_pct = float(self.settings.meme_swing_trailing_stop_pct)
                partial_tp_pct = float(max(0.01, self.settings.meme_partial_take_profit_pct))
                partial_tp_sell_ratio = float(_clamp(self.settings.meme_partial_take_profit_sell_ratio, 0.01, 1.0))
                live_key = f"live:{token}"
                target_positions[live_key] = {
                    "token_address": token,
                    "symbol": symbol,
                    "qty": float(qty),
                    "avg_price_usd": float(avg_price),
                    "opened_at": int(now_ts),
                    "entry_score": 0.0,
                    "grade": "D",
                    "tp_pct": float(tp_pct),
                    "sl_pct": float(sl_pct),
                    "partial_tp_pct": float(partial_tp_pct),
                    "partial_tp_sell_ratio": float(partial_tp_sell_ratio),
                    "partial_tp_done": False,
                    "engine_strategy_id": str(strategy_id),
                    "strategy": strategy,
                    "hold_until_ts": int(hold_until_ts),
                    "trailing_stop_pct": float(trailing_stop_pct),
                    "peak_price_usd": float(price),
                    "reason": "live_wallet_sync_seed",
                    "order_pct": 0.0,
                    "entry_features": {},
                    "catastrophic_sl_pct": float(sl_pct if (strategy == "swing" and str(target_model) == "B") else 0.0),
                    "entry_wallet_score": 0.0,
                    "entry_holder_risk": 0.0,
                    "last_wallet_check_ts": int(now_ts),
                    "hold_ext_count": 0,
                    "wallet_miss_count": 0,
                    "mode": "live",
                    "live_signature": "",
                    "live_slippage_bps": 0,
                }
                added += 1
            target_run["meme_positions"] = target_positions
            if "close_failed:no_wallet_balance" in str(self.state.memecoin_error or ""):
                self.state.memecoin_error = ""
            self.state.model_runs = runs
        if added > 0:
            self._push_alert(
                "info",
                "실전 지갑 포지션 동기화",
                f"{self._market_model_name('meme', target_model)}에 {int(added)}개 보유 토큰을 실전 포지션으로 연동했습니다.",
                send_telegram=False,
            )
        return {"added": int(added), "updated_qty": int(updated_qty)}

    def _execute_model_memecoin_entries(
        self,
        model_id: str,
        run: dict[str, Any],
        signals: list[dict[str, Any]],
        execution_mode: str = "auto",
    ) -> None:
        now = int(time.time())
        opened = 0
        skip_reasons: dict[str, int] = {}
        skip_examples: dict[str, list[str]] = {}

        def _skip(reason: str, symbol: str = "") -> None:
            key = str(reason or "unknown").strip().lower() or "unknown"
            skip_reasons[key] = int(skip_reasons.get(key, 0)) + 1
            sym = str(symbol or "").strip().upper()
            if not sym:
                return
            rows = list(skip_examples.get(key) or [])
            if sym not in rows and len(rows) < 8:
                rows.append(sym)
            skip_examples[key] = rows

        cooldown = self.settings.signal_cooldown_minutes * 60
        min_entry_rank = self._meme_min_entry_rank_for_model(model_id)
        guard = self._entry_guard_profile(model_id, "meme")
        loss_guard = self._run_loss_guard(run)
        guard_boost = float(guard.get("threshold_boost") or 0.0) + float(loss_guard.get("threshold_boost") or 0.0)
        min_score = _clamp(self._variant_threshold(model_id) + guard_boost, 0.0, 0.99)
        max_open_cycle = self.settings.max_signals_per_cycle
        max_open_positions = max(1, int(self.settings.meme_max_positions))
        if bool(guard.get("active")):
            max_open_cycle = max(1, min(max_open_cycle, 1))
        mode_text = str(execution_mode or "auto").strip().lower()
        if mode_text not in {"auto", "paper", "live"}:
            mode_text = "auto"
        allow_live_for_model = self._is_live_execution_market("meme", model_id)
        is_live_market = bool(allow_live_for_model and mode_text in {"auto", "live"})
        if mode_text == "live" and not allow_live_for_model:
            return
        allowed_strategy_ids = set(
            self._configured_meme_strategy_ids(
                self.settings.live_meme_strategy_ids if is_live_market else self.settings.meme_strategy_ids,
                fallback_all=True,
            )
        )
        live_wallet_budget_usd: float | None = None
        live_sol_price_usd = 0.0
        live_open_block_map = dict(run.get("live_open_block_until") or {})
        if is_live_market:
            if not (self.solana_trader.enabled or self.pumpportal_trader.enabled):
                err = self.solana_trader.init_error or self.pumpportal_trader.init_error or "solana_trader_not_enabled"
                with self._lock:
                    self.state.memecoin_error = f"live_meme_setup_failed:{err}"
                self._emit_runtime_error(
                    "live:meme_setup",
                    "밈코인 실전 실행 비활성",
                    err,
                    cooldown_seconds=600,
                )
                return
            live_wallet_budget = self._solana_trade_budget()
            live_sol_price_usd = max(0.0, float(live_wallet_budget.get("sol_price_usd") or 0.0))
            live_wallet_budget_usd = max(0.0, float(live_wallet_budget.get("tradeable_usd") or 0.0))
            if live_wallet_budget_usd < 5.0:
                try:
                    self.runtime_feedback.append_event(
                        source="live:meme_skip",
                        level="info",
                        status="skip",
                        detail=f"model={model_id} reason=insufficient_sol_budget tradeable_usd={live_wallet_budget_usd:.2f}",
                        meta={
                            "model_id": str(model_id),
                            "reason": "insufficient_sol_budget",
                            "tradeable_usd": float(live_wallet_budget_usd),
                        },
                        now_ts=int(now),
                    )
                except Exception:
                    pass
                return
            if live_open_block_map:
                pruned_live_block: dict[str, Any] = {}
                for token_key, row in live_open_block_map.items():
                    until_ts = int(((row or {}).get("until_ts") if isinstance(row, dict) else row) or 0)
                    if until_ts > now:
                        pruned_live_block[str(token_key)] = row
                live_open_block_map = pruned_live_block
                run["live_open_block_until"] = pruned_live_block
        ordered_signals = list(signals or [])
        ordered_signals.sort(
            key=lambda s: (
                0
                if str(s.get("strategy_id") or "").upper() == "SNIPER"
                else (1 if str(s.get("strategy_id") or "").upper() == "THEME" else 2),
                float(-((dict(s.get("features") or {})).get("sniper_signal_fit") or 0.0))
                if str(s.get("strategy_id") or "").upper() == "SNIPER"
                else (
                    self._theme_launch_priority_tuple(
                        s["token"],
                        float(s.get("score") or 0.0),
                    )[0]
                    if str(s.get("strategy_id") or "").upper() == "THEME" and isinstance(s.get("token"), TokenSnapshot)
                    else 99.0
                ),
                float(-((dict(s.get("features") or {})).get("sniper_social_burst") or 0.0))
                if str(s.get("strategy_id") or "").upper() == "SNIPER"
                else (
                    self._theme_launch_priority_tuple(
                        s["token"],
                        float(s.get("score") or 0.0),
                    )[1]
                    if str(s.get("strategy_id") or "").upper() == "THEME" and isinstance(s.get("token"), TokenSnapshot)
                    else 99.0
                ),
                float(-(float(s.get("score") or 0.0))),
                float(-(float(getattr(s.get("token"), "volume_5m_usd", 0.0) or 0.0))),
            )
        )
        for signal in ordered_signals:
            token: TokenSnapshot = signal["token"]
            token_address = token.token_address
            if opened >= max_open_cycle:
                _skip("max_open_cycle_reached", token.symbol)
                break
            open_positions_mode = sum(
                1
                for p in list((run.get("meme_positions") or {}).values())
                if bool(str((p or {}).get("mode") or "").strip().lower() == "live") == bool(is_live_market)
            )
            if open_positions_mode >= max_open_positions:
                _skip("max_open_positions_reached", token.symbol)
                break
            same_mode_exists = False
            for pos_row in list((run.get("meme_positions") or {}).values()):
                row_token = str((pos_row or {}).get("token_address") or "")
                row_live = bool(str((pos_row or {}).get("mode") or "").strip().lower() == "live")
                if row_token == token_address and row_live == bool(is_live_market):
                    same_mode_exists = True
                    break
            if same_mode_exists:
                _skip("already_in_position", token.symbol)
                continue
            if is_live_market:
                block_row = dict(live_open_block_map.get(token_address) or {})
                block_until = int(block_row.get("until_ts") or 0)
                if block_until > now:
                    _skip("live_retry_cooldown", token.symbol)
                    continue
            grade = str(signal.get("grade") or "G").upper()
            reason_text = str(signal.get("reason") or "")
            is_demo_fallback = "데모폴백" in reason_text
            if is_live_market and is_demo_fallback:
                # Service-mode guard: live execution must never use demo fallback signals.
                _skip("live_demo_fallback_blocked", token.symbol)
                continue
            score_now = float(signal.get("score") or 0.0)
            if not is_demo_fallback and score_now < min_score:
                _skip("score_below_threshold", token.symbol)
                continue
            if is_demo_fallback:
                floor_score = _clamp(self._demo_meme_score_floor(model_id) + guard_boost, 0.0, 0.99)
                if score_now < floor_score:
                    _skip("fallback_score_low", token.symbol)
                    continue
            if is_demo_fallback and not bool(guard.get("allow_demo_fallback", True)):
                _skip("fallback_disabled", token.symbol)
                continue
            if self._grade_rank(grade) > min_entry_rank:
                _skip("grade_below_entry_floor", token.symbol)
                continue
            features = dict(signal.get("features") or {})
            if not self._meme_quality_gate_for_entry(model_id, features):
                _skip("quality_gate_failed", token.symbol)
                continue
            strategy_id = str(
                signal.get("strategy_id")
                or self._meme_strategy_id_from_signal_context(
                    snap=token,
                    features=features,
                    reason=reason_text,
                    current_strategy_id=self._meme_strategy_id_for_model(model_id),
                )
            ).upper().strip() or "THEME"
            if allowed_strategy_ids and strategy_id not in allowed_strategy_ids:
                _skip("strategy_disabled", token.symbol)
                continue
            if strategy_id == "THEME":
                theme_gate_ok = self._theme_launch_entry_ok(token, features)
                theme_live_override = bool(
                    is_live_market
                    and "pumpfun" in reason_text.lower()
                    and not is_demo_fallback
                    and float(features.get("theme_launch_ready") or 0.0) > 0.0
                    and float(score_now) >= max(0.70, min_score)
                )
                if not theme_gate_ok and not theme_live_override:
                    _skip("theme_launch_gate_failed", token.symbol)
                    continue
            elif strategy_id == "SNIPER":
                if not self._sniper_entry_ok(token, features):
                    _skip("sniper_gate_failed", token.symbol)
                    continue
            sym = token.symbol.upper()
            sym_key = f"{'live' if is_live_market else 'paper'}:{sym}"
            reentry_map = dict(run.get("meme_reentry_after_ts") or {})
            reentry_after_ts = int(reentry_map.get(sym_key) or 0)
            if reentry_after_ts > 0 and now < reentry_after_ts:
                _skip("sl_reentry_wait", token.symbol)
                continue
            last_ts = int((run.get("last_signal_ts") or {}).get(sym_key, 0))
            if (now - last_ts) < cooldown:
                allow_c_fast_reentry = bool(
                    str(model_id).upper() == "C" and reentry_after_ts > 0 and now >= reentry_after_ts
                )
                if not allow_c_fast_reentry:
                    _skip("signal_cooldown", token.symbol)
                    continue

            run_cash = float(run.get("meme_cash_usd") or 0.0)
            cash = float(live_wallet_budget_usd or 0.0) if is_live_market else run_cash
            reference_sol_price_usd = float(live_sol_price_usd or 0.0) if is_live_market else float(self._live_sol_price_usd() or 0.0)
            if not is_live_market and reference_sol_price_usd <= 0.0:
                reference_sol_price_usd = 100.0
            target_entry_sol = self._meme_strategy_entry_sol(strategy_id)
            order_usd_target = float(target_entry_sol * max(reference_sol_price_usd, 0.0))
            order_usd = min(cash, max(5.0, order_usd_target))
            if order_usd < 5.0:
                _skip("order_usd_too_small", token.symbol)
                continue
            order_pct = _clamp(order_usd / max(cash, 1e-9), 0.0, 1.0)
            live_signature = ""
            live_slippage_bps = 0
            live_accounting: dict[str, Any] = {}
            execution_venue = "paper"
            is_live_entry = bool(is_live_market)
            qty = order_usd / max(0.0000001, float(token.price_usd))
            if is_live_entry:
                if live_sol_price_usd <= 0.0:
                    with self._lock:
                        self.state.memecoin_error = "live_meme_open_failed:sol_price_unavailable"
                    _skip("sol_price_unavailable", token.symbol)
                    continue
                before_snapshot = self._capture_live_wallet_snapshot(token_address)
                order_sol = order_usd / max(1e-9, live_sol_price_usd)
                try:
                    swap_result, execution_venue = self._live_meme_buy(
                        token=token,
                        strategy_id=strategy_id,
                        order_sol=order_sol,
                        model_id=model_id,
                        features=features,
                    )
                    live_signature = str(swap_result.get("signature") or "")
                    self._mark_pending_live_trade_signature(live_signature, now)
                    live_slippage_bps = int(
                        swap_result.get("slippage_bps")
                        or round(float(swap_result.get("slippage_pct") or 0.0) * 100.0)
                        or 0
                    )
                    self._sync_wallet(int(time.time()), force=True)
                    live_accounting = self._apply_live_swap_accounting(
                        token_address=token_address,
                        swap_signature=live_signature,
                        before_snapshot=before_snapshot,
                        now_ts=int(now),
                        side="buy",
                        fallback_sol_price_usd=float(live_sol_price_usd),
                    )
                    actual_qty = max(0.0, float(live_accounting.get("received_qty") or 0.0))
                    actual_spent_usd = max(0.0, float(live_accounting.get("spent_usd") or 0.0))
                    actual_avg_usd = max(0.0, float(live_accounting.get("avg_price_usd") or 0.0))
                    if actual_spent_usd > 0.0:
                        order_usd = float(actual_spent_usd)
                    if actual_qty > 0.0:
                        qty = float(actual_qty)
                    elif float(token.price_usd) > 0.0:
                        qty = order_usd / float(token.price_usd)
                    if qty <= 0.0:
                        raise RuntimeError("live_fill_qty_zero")
                    with self._lock:
                        self.state.memecoin_error = ""
                    if token_address in live_open_block_map:
                        live_open_block_map.pop(token_address, None)
                        run["live_open_block_until"] = live_open_block_map
                except Exception as exc:
                    with self._lock:
                        self.state.memecoin_error = f"{token.symbol}:open_failed:{exc}"
                    _skip("live_swap_failed", token.symbol)
                    err_text = str(exc)
                    err_low = err_text.lower()
                    if "token_not_tradable" in err_low or "not tradable" in err_low:
                        block_seconds = 60 * 60
                    elif "429" in err_low or "rate" in err_low:
                        block_seconds = 20 * 60
                    else:
                        block_seconds = 5 * 60
                    live_open_block_map[token_address] = {
                        "until_ts": int(now + block_seconds),
                        "reason": err_text[:240],
                        "updated_ts": int(now),
                    }
                    if len(live_open_block_map) > 600:
                        rows = sorted(
                            list(live_open_block_map.items()),
                            key=lambda kv: int(
                                (
                                    (kv[1].get("updated_ts") if isinstance(kv[1], dict) else 0)
                                    or (kv[1].get("until_ts") if isinstance(kv[1], dict) else 0)
                                    or (kv[1] if isinstance(kv[1], (int, float)) else 0)
                                )
                            ),
                            reverse=True,
                        )[:600]
                        live_open_block_map = {
                            str(k): (
                                dict(v or {})
                                if isinstance(v, dict)
                                else {"until_ts": int(v or 0), "reason": "", "updated_ts": int(now)}
                            )
                            for k, v in rows
                        }
                    run["live_open_block_until"] = live_open_block_map
                    self._emit_runtime_error(
                        "live:meme_open",
                        f"{token.symbol} 실전 진입 실패",
                        str(exc),
                        cooldown_seconds=90,
                    )
                    continue
            vol = self._meme_volatility_proxy(features)
            sl_pct, tp_pct = self._compute_risk_profile(model_id, "meme", vol)
            strategy = self._meme_strategy_for_model(model_id, grade, features)
            if is_demo_fallback and model_id in {"A", "B"}:
                strategy = "swing"
            if str(strategy).lower() != "swing":
                tp_pct = float(self._meme_score_target_tp_pct(score_now))
            swing = strategy == "swing"
            hold_until_ts = 0
            trailing_stop_pct = 0.0
            if swing:
                if model_id == "B":
                    tp_pct = _clamp(tp_pct, 0.10, 0.20)
                else:
                    tp_pct = _clamp(tp_pct, 0.10, 0.20)
                hold_days = int(self.settings.meme_swing_hold_days)
                if model_id == "B":
                    hold_days = max(14, hold_days)
                hold_until_ts = now + hold_days * 86400
                trailing_stop_pct = float(self.settings.meme_swing_trailing_stop_pct)
                if model_id == "B":
                    smart = float(features.get("smart_wallet_score") or 0.0)
                    holder_risk = float(features.get("holder_risk") or 1.0)
                    if smart >= 0.72 and holder_risk <= 0.42:
                        sl_pct = _clamp(max(sl_pct, 0.45), 0.25, 0.75)
                        trailing_stop_pct = max(0.58, trailing_stop_pct)
                    elif smart >= 0.64 and holder_risk <= 0.52:
                        sl_pct = _clamp(max(sl_pct, 0.36), 0.22, 0.70)
                        trailing_stop_pct = max(0.52, trailing_stop_pct)
                    else:
                        sl_pct = _clamp(max(sl_pct, 0.30), 0.20, 0.65)
                        trailing_stop_pct = max(0.46, trailing_stop_pct)
                else:
                    sl_pct = _clamp(max(sl_pct, 0.16), 0.06, 0.60)

            position_key = f"live:{token_address}" if is_live_entry else token_address
            entry_avg_price_usd = float(live_accounting.get("avg_price_usd") or token.price_usd)
            partial_tp_pct = float(max(0.01, self.settings.meme_partial_take_profit_pct))
            partial_tp_sell_ratio = float(_clamp(self.settings.meme_partial_take_profit_sell_ratio, 0.01, 1.0))
            run.setdefault("meme_positions", {})[position_key] = {
                "token_address": token_address,
                "symbol": token.symbol,
                "qty": qty,
                "avg_price_usd": float(entry_avg_price_usd),
                "opened_at": now,
                "entry_score": float(signal["score"]),
                "grade": grade,
                "tp_pct": tp_pct,
                "sl_pct": sl_pct,
                "partial_tp_pct": float(partial_tp_pct),
                "partial_tp_sell_ratio": float(partial_tp_sell_ratio),
                "partial_tp_done": False,
                "engine_strategy_id": str(strategy_id),
                "strategy": strategy,
                "hold_until_ts": hold_until_ts,
                "trailing_stop_pct": trailing_stop_pct,
                "peak_price_usd": float(token.price_usd),
                "reason": str(signal["reason"]),
                "order_pct": float(order_pct),
                "entry_features": features,
                "catastrophic_sl_pct": float(sl_pct if (strategy == "swing" and model_id == "B") else 0.0),
                "entry_wallet_score": float(features.get("smart_wallet_score") or 0.0),
                "entry_holder_risk": float(features.get("holder_risk") or 0.0),
                "last_wallet_check_ts": int(now),
                "hold_ext_count": 0,
                "mode": "live" if is_live_entry else "paper",
                "execution_venue": str(execution_venue or ("live" if is_live_entry else "paper")),
                "live_signature": live_signature,
                "live_slippage_bps": int(live_slippage_bps),
                "entry_notional_usd": float(order_usd),
                "entry_fee_usd": float(live_accounting.get("fee_usd") or 0.0),
                "entry_before_sol_lamports": int(live_accounting.get("before_sol_lamports") or 0),
                "entry_after_sol_lamports": int(live_accounting.get("after_sol_lamports") or 0),
                "entry_before_token_raw": int(live_accounting.get("before_token_raw") or 0),
                "entry_after_token_raw": int(live_accounting.get("after_token_raw") or 0),
            }
            if not is_live_entry:
                run["meme_cash_usd"] = max(0.0, run_cash - order_usd)
            if live_wallet_budget_usd is not None:
                live_wallet_budget_usd = max(0.0, live_wallet_budget_usd - order_usd)
            run.setdefault("last_signal_ts", {})[sym_key] = now
            if sym_key in reentry_map:
                reentry_map.pop(sym_key, None)
                run["meme_reentry_after_ts"] = reentry_map
            run.setdefault("trades", []).append(
                {
                    "ts": now,
                    "source": "memecoin",
                    "side": "buy",
                    "symbol": token.symbol,
                    "token_address": token_address,
                    "qty": qty,
                    "price_usd": float(entry_avg_price_usd),
                    "notional_usd": order_usd,
                    "order_pct": float(order_pct),
                    "pnl_usd": None,
                    "pnl_pct": None,
                    "reason": (
                        f"{strategy_id}|{strategy}|{target_entry_sol:.2f}SOL|{order_usd:.2f}USD|{reason_text}"
                        + (f"|live_tx={live_signature}" if live_signature else "")
                    ),
                    "model_id": model_id,
                    "strategy_id": str(strategy_id),
                    "mode": "live" if is_live_entry else "paper",
                    "realized_pnl_usd": None,
                    "realized_pnl_pct": None,
                    "network_fee_usd": float(live_accounting.get("fee_usd") or 0.0),
                    "before_sol_lamports": int(live_accounting.get("before_sol_lamports") or 0),
                    "after_sol_lamports": int(live_accounting.get("after_sol_lamports") or 0),
                    "before_token_raw": int(live_accounting.get("before_token_raw") or 0),
                    "after_token_raw": int(live_accounting.get("after_token_raw") or 0),
                    "realized": False,
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
                        f"{order_usd:.2f} USD ({target_entry_sol:.2f} SOL 기준) | 배분 {order_pct*100:.1f}% | {strategy_id} | {strategy} | "
                        f"{signal.get('grade','G')} | score={float(signal['score']):.2f} | "
                        f"+{partial_tp_pct*100:.0f}%시 {partial_tp_sell_ratio*100:.0f}% 매도 / SL {sl_pct*100:.1f}% | {signal['reason']}"
                    ),
                    send_telegram=True,
                )

        if is_live_market and opened <= 0 and skip_reasons:
            top_reason, top_count = max(skip_reasons.items(), key=lambda kv: int(kv[1]))
            top_symbols = list(skip_examples.get(str(top_reason), []) or [])
            run["last_live_skip"] = {
                "ts": int(now),
                "model_id": str(model_id),
                "reason": str(top_reason),
                "count": int(top_count),
                "details": dict(skip_reasons),
                "examples": dict(skip_examples),
            }
            benign_skip_reasons = {
                "already_in_position",
                "live_retry_cooldown",
                "sl_reentry_wait",
                "signal_cooldown",
                "score_below_threshold",
                "fallback_score_low",
                "fallback_disabled",
                "live_demo_fallback_blocked",
                "grade_below_entry_floor",
                "quality_gate_failed",
                "order_usd_too_small",
            }
            if str(top_reason) in benign_skip_reasons:
                try:
                    detail_text = f"model={model_id} reason={top_reason} count={int(top_count)}"
                    if top_symbols:
                        detail_text += f" symbols={','.join(top_symbols)}"
                    self.runtime_feedback.append_event(
                        source="live:meme_skip",
                        level="info",
                        status="skip",
                        detail=detail_text,
                        meta={
                            "model_id": str(model_id),
                            "reason": str(top_reason),
                            "count": int(top_count),
                            "symbols": list(top_symbols),
                            "details": dict(skip_reasons),
                            "examples": dict(skip_examples),
                        },
                        now_ts=int(now),
                    )
                except Exception:
                    pass
                with self._lock:
                    if str(self.state.memecoin_error or "").startswith("live_skip:"):
                        self.state.memecoin_error = ""
            else:
                with self._lock:
                    self.state.memecoin_error = f"live_skip:{model_id}:{top_reason}:{int(top_count)}"

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
        trend_hits = trader + news + community + (google // 2) + (2 if sym in trending else 0)
        rank = int(row.get("market_cap_rank") or 0)
        if rank <= 0:
            rank = 10000
        rank_quality = _clamp((600.0 - float(rank)) / 600.0, 0.0, 1.0)
        vol = float(row.get("volume_24h") or 0.0)
        vol_quality = _clamp(math.log10(max(1.0, vol)) / 11.0, 0.0, 1.0)
        trend_score = (
            (2.0 if sym in trending else 0.0)
            + min(2.2, float(trader) * 0.6)
            + min(2.0, float(news) * 0.55)
            + min(1.6, float(community) * 0.35)
            + min(1.0, float(google) * 0.2)
        )
        score = float(trend_score + (1.6 * rank_quality) + (0.8 * vol_quality))
        return (score, int(trend_hits))

    def _refresh_macro_trend_pool(
        self,
        rows: list[dict[str, Any]],
        trend_bundle: dict[str, Any],
        now_ts: int,
    ) -> set[str]:
        rank_lo, rank_hi = self._macro_rank_window()
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
            if rank < rank_lo or rank > rank_hi:
                continue
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

    def _refresh_crypto_model_watch_pool(
        self,
        model_id: str,
        rows: list[dict[str, Any]],
        trend_bundle: dict[str, Any],
        now_ts: int,
    ) -> set[str]:
        model_key = str(model_id or "").upper().strip()
        if model_key != "D":
            return set()
        target_size = 50
        refresh_sec = max(4 * 60 * 60, int(self.settings.macro_trend_reselect_seconds))
        cached = list(self._crypto_model_watch_pools.get(model_key) or [])
        if cached and now_ts < int(self._crypto_model_watch_pool_next_refresh_ts.get(model_key) or 0):
            return set(cached)

        rank_lo, rank_hi = self._crypto_rank_band_for_model(model_key)
        scored: list[dict[str, Any]] = []
        for row in rows:
            base_symbol = str(row.get("symbol") or "").upper().strip()
            if not base_symbol:
                continue
            rank = int(row.get("market_cap_rank") or 0)
            if not self._rank_within_window(rank, rank_lo, rank_hi):
                continue
            score, hits = self._macro_trend_score(base_symbol, row, trend_bundle)
            scored.append(
                {
                    "symbol": f"{base_symbol}USDT",
                    "rank": int(rank),
                    "score": float(score),
                    "hits": int(hits),
                    "volume_24h": float(row.get("volume_24h") or 0.0),
                }
            )
        if not scored:
            return set(cached)

        scored.sort(
            key=lambda item: (
                -int(item["hits"]),
                -float(item["score"]),
                -float(item["volume_24h"]),
                int(item["rank"]),
            )
        )
        selected = [str(item["symbol"]) for item in scored[:target_size]]
        self._crypto_model_watch_pools[model_key] = list(selected)
        self._crypto_model_watch_pool_next_refresh_ts[model_key] = int(now_ts + refresh_sec)
        return set(selected)

    def _fetch_macro_demo_prices(self, trend_bundle: dict[str, Any] | None = None) -> dict[str, float]:
        trend_data = dict(trend_bundle or {})
        now_ts = int(time.time())
        prices: dict[str, float] = {}
        meta: dict[str, dict[str, Any]] = {}
        rt_prices: dict[str, float] = {}
        rt_meta: dict[str, dict[str, Any]] = {}
        scan_bands = self._crypto_scan_rank_bands()
        fetch_limit = max(
            int(self.settings.macro_top_n),
            max((int(rank_hi) for _, rank_hi in scan_bands), default=int(self.settings.macro_top_n)),
        )
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
                limit=fetch_limit,
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
        rows_all = list(rows or [])
        rank_lo, rank_hi = self._macro_rank_window()
        rows = [
            row
            for row in rows_all
            if self._rank_within_window(int(row.get("market_cap_rank") or 0), rank_lo, rank_hi)
        ]
        configured_symbols = set(self._configured_crypto_symbols())
        if configured_symbols:
            selected_symbols = set(configured_symbols)
            self._macro_trend_pool = sorted(selected_symbols)
            self._macro_trend_pool_next_refresh_ts = int(now_ts + int(self.settings.macro_trend_reselect_seconds))
        else:
            extra_model_symbols: set[str] = set()
            for model_id in self._active_crypto_model_ids_for_scan():
                band_lo, band_hi = self._crypto_rank_band_for_model(model_id)
                if band_lo >= rank_lo and band_hi <= rank_hi:
                    continue
                if str(model_id).upper().strip() == "D":
                    extra_model_symbols.update(
                        self._refresh_crypto_model_watch_pool(model_id, rows_all, trend_data, now_ts)
                    )
                    continue
                band_candidates: list[dict[str, Any]] = []
                for row in rows_all:
                    rank = int(row.get("market_cap_rank") or 0)
                    if not self._rank_within_window(rank, band_lo, band_hi):
                        continue
                    base_symbol = str(row.get("symbol") or "").upper().strip()
                    if not base_symbol:
                        continue
                    score, hits = self._macro_trend_score(base_symbol, row, trend_data)
                    band_candidates.append(
                        {
                            "symbol": f"{base_symbol}USDT",
                            "rank": int(rank),
                            "score": float(score),
                            "hits": int(hits),
                            "volume_24h": float(row.get("volume_24h") or 0.0),
                        }
                    )
                band_candidates.sort(
                    key=lambda item: (
                        -int(item["hits"]),
                        -float(item["score"]),
                        -float(item["volume_24h"]),
                        int(item["rank"]),
                    )
                )
                for item in band_candidates[:24]:
                    extra_model_symbols.add(str(item["symbol"]))

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
            if extra_model_symbols:
                selected_symbols = set(selected_symbols or set())
                selected_symbols.update(extra_model_symbols)

        # Always keep open crypto positions marked-to-market even when they fall out of trend pool.
        held_symbols: set[str] = set()
        held_anchor_prices: dict[str, float] = {}
        with self._lock:
            for model_id in CRYPTO_MODEL_IDS:
                run = self.state.model_runs.get(self._market_run_key("crypto", model_id))
                if not isinstance(run, dict):
                    continue
                for pos in list((run.get("bybit_positions") or {}).values()):
                    sym = str((pos or {}).get("symbol") or "").upper().strip()
                    if sym:
                        held_symbols.add(sym)
                        anchor = float((pos or {}).get("last_mark_price_usd") or 0.0)
                        if anchor <= 0.0:
                            anchor = float((pos or {}).get("avg_price_usd") or 0.0)
                        if anchor > 0.0:
                            prev = float(held_anchor_prices.get(sym) or 0.0)
                            held_anchor_prices[sym] = anchor if prev <= 0.0 else prev
        if held_symbols:
            selected_symbols = set(selected_symbols or set())
            selected_symbols.update(held_symbols)

        # Price map can include held symbols outside current rank window (mark-to-market stability).
        for row in rows_all:
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
        # Backfill any selected/held symbols from realtime feed even if they are out of current rank window.
        if rt_prices and selected_symbols:
            for symbol in sorted(set(selected_symbols)):
                if symbol in prices:
                    continue
                price = float(rt_prices.get(symbol) or 0.0)
                if price <= 0:
                    continue
                rt_row = dict(rt_meta.get(symbol) or {})
                prev_rank = int((self._macro_meta.get(symbol) or {}).get("market_cap_rank") or 0)
                prices[symbol] = float(price)
                meta[symbol] = {
                    "change_1h": 0.0,
                    "change_24h": float(rt_row.get("change_24h") or 0.0),
                    "volume_24h": float(rt_row.get("volume_24h") or 0.0),
                    "market_cap": float((self._macro_meta.get(symbol) or {}).get("market_cap") or 0.0),
                    "market_cap_rank": int(prev_rank),
                    "source": "realtime_backfill",
                    "realtime_source": str(rt_row.get("realtime_source") or ""),
                }
                self._bybit_last_prices[symbol] = float(price)
                hist = self._bybit_price_history.get(symbol) or []
                hist.append(float(price))
                if len(hist) > 240:
                    hist = hist[-240:]
                self._bybit_price_history[symbol] = hist

        # Guard held symbols from abrupt one-cycle quote jumps (source mismatch/stale feed).
        if held_anchor_prices:
            jump_guard = float(CRYPTO_HELD_PRICE_JUMP_GUARD_PCT)
            for symbol, anchor in held_anchor_prices.items():
                a = float(anchor or 0.0)
                if a <= 0.0:
                    continue
                px = float(prices.get(symbol) or 0.0)
                if px <= 0.0:
                    prices[symbol] = a
                    meta_row = dict(meta.get(symbol) or {})
                    meta_row["price_guard"] = "anchor_missing_backfill"
                    meta[symbol] = meta_row
                    self._bybit_last_prices[symbol] = float(a)
                    hist = self._bybit_price_history.get(symbol) or []
                    hist.append(float(a))
                    if len(hist) > 240:
                        hist = hist[-240:]
                    self._bybit_price_history[symbol] = hist
                    continue
                jump = abs((px / max(a, 1e-12)) - 1.0)
                if jump > jump_guard:
                    prices[symbol] = float(a)
                    meta_row = dict(meta.get(symbol) or {})
                    meta_row["price_guard"] = "anchor_jump_guard"
                    meta_row["raw_price_usd"] = float(px)
                    meta[symbol] = meta_row
                    self._bybit_last_prices[symbol] = float(a)
                    hist = self._bybit_price_history.get(symbol) or []
                    hist.append(float(a))
                    if len(hist) > 240:
                        hist = hist[-240:]
                    self._bybit_price_history[symbol] = hist
        self._macro_meta = meta
        if not self.bybit.enabled:
            with self._lock:
                self.state.bybit_error = ""
        return prices

    def _macro_rank_window(self) -> tuple[int, int]:
        rank_min_cfg = int(getattr(self.settings, "macro_rank_min", 50) or 50)
        rank_max_cfg = int(getattr(self.settings, "macro_rank_max", 300) or 300)
        rank_min = max(1, min(300, rank_min_cfg))
        rank_max = max(1, min(300, rank_max_cfg))
        if rank_max < rank_min:
            rank_min, rank_max = rank_max, rank_min
        return (rank_min, rank_max)

    @staticmethod
    def _rank_within_window(rank: int, rank_min: int, rank_max: int) -> bool:
        return bool(rank > 0 and rank_min <= rank <= rank_max)

    def _crypto_rank_band_for_model(self, model_id: str) -> tuple[int, int]:
        default_min, default_max = self._macro_rank_window()
        profile = dict(CRYPTO_MODEL_GATE_DEFAULTS.get(model_id) or CRYPTO_MODEL_GATE_DEFAULTS["B"])
        rank_min_cfg = int(profile.get("rank_min") or default_min)
        rank_max_cfg = int(profile.get("rank_max") or default_max)
        rank_min = max(1, min(5000, rank_min_cfg))
        rank_max = max(1, min(5000, rank_max_cfg))
        if rank_max < rank_min:
            rank_min, rank_max = rank_max, rank_min
        return (rank_min, rank_max)

    def _active_crypto_model_ids_for_scan(self) -> tuple[str, ...]:
        ordered: list[str] = []
        valid_ids = set(CRYPTO_MODEL_IDS)
        for group in (self._autotrade_model_ids("crypto"), self._live_model_ids("crypto"), CRYPTO_MODEL_IDS):
            for raw in group:
                model_id = str(raw or "").upper().strip()
                if model_id in valid_ids and model_id not in ordered:
                    ordered.append(model_id)
        return tuple(ordered or CRYPTO_MODEL_IDS)

    def _crypto_scan_rank_bands(self) -> tuple[tuple[int, int], ...]:
        seen: set[tuple[int, int]] = set()
        ordered: list[tuple[int, int]] = []
        bands = [self._macro_rank_window(), *[self._crypto_rank_band_for_model(mid) for mid in self._active_crypto_model_ids_for_scan()]]
        for band in bands:
            if band not in seen:
                seen.add(band)
                ordered.append(band)
        return tuple(ordered)

    def _crypto_symbol_allowed_for_model(self, model_id: str, symbol: str) -> bool:
        configured_symbols = set(self._configured_crypto_symbols())
        symbol_u = str(symbol or "").upper().strip()
        if configured_symbols:
            return symbol_u in configured_symbols
        meta = dict(self._macro_meta.get(symbol) or {})
        rank = int(meta.get("market_cap_rank") or 0)
        if rank <= 0:
            return False
        rank_min, rank_max = self._crypto_rank_band_for_model(model_id)
        if not self._rank_within_window(rank, rank_min, rank_max):
            return False
        profile = dict(CRYPTO_MODEL_GATE_DEFAULTS.get(model_id) or CRYPTO_MODEL_GATE_DEFAULTS["B"])
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
        _ = (base_sym, trend_bundle)
        return 0.0

    @staticmethod
    def _series_std(values: list[float]) -> float:
        if len(values) < 2:
            return 0.0
        mean_v = sum(float(v) for v in values) / float(len(values))
        var = sum((float(v) - mean_v) ** 2 for v in values) / float(max(1, len(values) - 1))
        return math.sqrt(max(0.0, var))

    def _window_stats(self, values: list[float], bars: int, current_price: float) -> dict[str, float]:
        tail = [float(v) for v in list(values[-max(2, int(bars)) :]) if float(v) > 0.0]
        current = float(current_price or 0.0)
        if not tail:
            return {
                "low": current,
                "high": current,
                "mid": current,
                "width": max(current * 0.001, 0.0),
                "position": 0.5,
                "mean": current,
                "std": 0.0,
            }
        low = min(tail)
        high = max(tail)
        width = max(high - low, max(current, high, low) * 0.001)
        pos = _clamp((current - low) / width, 0.0, 1.0) if current > 0.0 else 0.5
        mean_v = sum(tail) / float(len(tail))
        return {
            "low": float(low),
            "high": float(high),
            "mid": float(low + (width * 0.5)),
            "width": float(width),
            "position": float(pos),
            "mean": float(mean_v),
            "std": float(self._series_std(tail)),
        }

    @staticmethod
    def _finalize_crypto_trade_plan(
        *,
        current_price: float,
        entry_price: float,
        zone_low: float,
        zone_high: float,
        stop_price: float,
        targets: list[float],
        ttl_minutes: int,
    ) -> dict[str, float]:
        current = max(0.0, float(current_price or 0.0))
        entry = max(1e-9, float(entry_price or current or 0.0))
        z_low = min(float(zone_low or entry), float(zone_high or entry), entry)
        z_high = max(float(zone_low or entry), float(zone_high or entry), entry)
        stop = min(float(stop_price or 0.0), entry - max(entry * 0.001, 1e-9))
        risk = max(entry - stop, entry * 0.0015)
        clean_targets = [float(value) for value in list(targets or []) if float(value) > (entry + (risk * 0.25))]
        while len(clean_targets) < 3:
            step = 1.10 + (0.75 * len(clean_targets))
            clean_targets.append(entry + (risk * step))
        clean_targets = sorted(clean_targets[:3])
        take_profit = float(clean_targets[1])
        risk_reward = (take_profit - entry) / max(risk, 1e-9)
        setup_state = "entry-ready" if z_low <= current <= z_high else ("waiting-retrace" if current > z_high else "waiting-breakout")
        return {
            "entry_price": float(entry),
            "entry_zone_low": float(z_low),
            "entry_zone_high": float(z_high),
            "stop_loss_price": float(stop),
            "target_price_1": float(clean_targets[0]),
            "target_price_2": float(clean_targets[1]),
            "target_price_3": float(clean_targets[2]),
            "take_profit_price": float(take_profit),
            "sl_pct": float(_clamp((entry - stop) / max(entry, 1e-9), 0.004, 0.30)),
            "tp_pct": float(_clamp((take_profit - entry) / max(entry, 1e-9), 0.008, 0.80)),
            "risk_reward": float(max(0.0, risk_reward)),
            "entry_ready": bool(z_low <= current <= z_high),
            "setup_state": str(setup_state),
            "setup_expiry_ts": int(time.time()) + (max(10, int(ttl_minutes)) * 60),
        }

    def _crypto_feature_pack(self, symbol: str, trend_bundle: dict[str, Any], model_id: str = "") -> dict[str, float]:
        _ = trend_bundle
        hist_tick = [float(v) for v in list(self._bybit_price_history.get(symbol) or []) if float(v) > 0.0]
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
                cache_seconds=max(60, min(600, int(self.settings.scan_interval_seconds))),
                binance_api_key=self.settings.binance_api_key,
            )
        except Exception:
            tf_5m = []
        timeframe_source = "binance_5m"
        if len(tf_5m) < 20 and hist_tick:
            tf_5m = [float(v) for v in hist_tick[-240:]]
            timeframe_source = "bybit_cache"
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

        ret_5m = _clamp(self._series_return(tf_5m, 1), -0.15, 0.15)
        ret_15m = _clamp(self._series_return(tf_15m, 1), -0.22, 0.22)
        ret_1h = _clamp(self._series_return(tf_1h, 1) if len(tf_1h) >= 2 else chg1h, -0.45, 0.45)
        ret_4h = _clamp(self._series_return(tf_4h, 1) if len(tf_4h) >= 2 else (chg24h * 0.55), -0.80, 0.80)
        ret_1d = _clamp(self._series_return(tf_1d, 1) if len(tf_1d) >= 2 else chg24h, -1.20, 1.20)

        edge_5m = _clamp(ret_5m / 0.020, -1.0, 1.0)
        edge_15m = _clamp(ret_15m / 0.030, -1.0, 1.0)
        edge_1h = _clamp(ret_1h / 0.060, -1.0, 1.0)
        edge_4h = _clamp(ret_4h / 0.120, -1.0, 1.0)
        edge_1d = _clamp(ret_1d / 0.200, -1.0, 1.0)

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

        current_price = float(tf_5m[-1] if tf_5m else (self._bybit_last_prices.get(symbol) or 0.0))
        atr_abs = max(current_price * max(atr_pct, 0.0025), current_price * 0.0015)
        stats_12 = self._window_stats(tf_5m, 12, current_price)
        stats_36 = self._window_stats(tf_5m, 36, current_price)
        stats_72 = self._window_stats(tf_5m, 72, current_price)
        range_12_pct = float(stats_12["width"]) / max(current_price, 1e-9)
        range_36_pct = float(stats_36["width"]) / max(current_price, 1e-9)
        range_72_pct = float(stats_72["width"]) / max(current_price, 1e-9)
        lower_range_bias = _clamp((0.60 - float(stats_36["position"])) / 0.60, 0.0, 1.0)
        upper_range_bias = _clamp((float(stats_36["position"]) - 0.40) / 0.60, 0.0, 1.0)
        support_closeness = _clamp(1.0 - float(stats_12["position"]), 0.0, 1.0)
        breakout_ready = _clamp(float(stats_12["position"]), 0.0, 1.0)
        rebound_strength = _clamp((current_price - float(stats_12["low"])) / max(atr_abs * 2.0, current_price * 0.002), 0.0, 1.0)
        reclaim_strength = _clamp((current_price - float(stats_36["mid"])) / max(atr_abs * 2.5, current_price * 0.002), -1.0, 1.0)
        mean_reversion_gap = _clamp((float(stats_36["mid"]) - current_price) / max(atr_abs * 3.0, current_price * 0.002), -1.0, 1.0)
        compression_ratio = self._series_std(tf_5m[-12:]) / max(self._series_std(tf_5m[-48:]), current_price * 0.0005)
        compression_score = _clamp((1.05 - compression_ratio) / 0.55, 0.0, 1.0)
        ema_alignment = _clamp((ema_gap_pct / 0.012) + 0.5, 0.0, 1.0)
        oversold_score = _clamp((45.0 - rsi) / 18.0, 0.0, 1.0)
        reset_score = _clamp((52.0 - rsi) / 22.0, 0.0, 1.0)
        washout_score = _clamp((-ret_1h) / 0.05, 0.0, 1.0) * _clamp(rebound_strength, 0.0, 1.0)
        pullback_mom = _clamp((-ret_15m) / 0.04, 0.0, 1.0)
        rsi_rebound = _clamp((55.0 - rsi) / 25.0, 0.0, 1.0)
        cci_rebound = _clamp((-cci_raw) / 180.0, 0.0, 1.0)
        stability_score = _clamp((0.075 - atr_pct) / 0.055, 0.0, 1.0)
        volatility_penalty = _clamp((atr_pct - 0.022) / 0.090, 0.0, 1.0)
        liquidity_quality = _clamp(math.log10(max(1.0, volume_24h)) / 10.0, 0.0, 1.0)
        cap_quality = _clamp(math.log10(max(1.0, market_cap)) / 12.0, 0.0, 1.0)
        rank_quality = _clamp((1200.0 - rank) / 1200.0, 0.0, 1.0) if rank > 0 else 0.0
        quality_score = _clamp((0.45 * rank_quality) + (0.30 * liquidity_quality) + (0.25 * cap_quality), 0.0, 1.0)
        noise_penalty = _clamp(abs(ret_5m) / 0.07, 0.0, 1.0)
        overheat_penalty = _clamp(
            max(0.0, breakout_ready - 0.88) / 0.12
            + max(0.0, abs(chg24h) - 0.18) / 0.50,
            0.0,
            1.0,
        )
        ema_edge = _clamp((ema_signal - 0.5) * 2.0, -1.0, 1.0)
        cci_edge = _clamp((cci_signal - 0.5) * 2.0, -1.0, 1.0)
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
            "trend_stack": 0.0,
            "mom1": ret_5m,
            "mom4": ret_1h,
            "mom12": ret_1d,
            "chg1h": chg1h,
            "chg24h": chg24h,
            "market_cap_rank": float(rank),
            "liquidity_quality": liquidity_quality,
            "cap_quality": cap_quality,
            "rank_quality": rank_quality,
            "social_heat": 0.0,
            "current_price": current_price,
            "atr_abs": atr_abs,
            "window_low_12": float(stats_12["low"]),
            "window_high_12": float(stats_12["high"]),
            "window_mid_12": float(stats_12["mid"]),
            "window_low_36": float(stats_36["low"]),
            "window_high_36": float(stats_36["high"]),
            "window_mid_36": float(stats_36["mid"]),
            "window_low_72": float(stats_72["low"]),
            "window_high_72": float(stats_72["high"]),
            "window_mid_72": float(stats_72["mid"]),
            "range_12_pct": range_12_pct,
            "range_36_pct": range_36_pct,
            "range_72_pct": range_72_pct,
            "lower_range_bias": lower_range_bias,
            "upper_range_bias": upper_range_bias,
            "support_closeness": support_closeness,
            "breakout_ready": breakout_ready,
            "rebound_strength": rebound_strength,
            "reclaim_strength": reclaim_strength,
            "mean_reversion_gap": mean_reversion_gap,
            "compression_score": compression_score,
            "ema_alignment": ema_alignment,
            "oversold_score": oversold_score,
            "reset_score": reset_score,
            "washout_score": washout_score,
            "stability_score": stability_score,
            "volatility_penalty": volatility_penalty,
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

    @staticmethod
    def _crypto_overheat_chase_block(model_id: str, features: dict[str, float]) -> bool:
        ret_15m = float(features.get("ret_15m") or 0.0)
        overheat = float(features.get("overheat_penalty") or 0.0)
        breakout_ready = float(features.get("breakout_ready") or 0.0)
        atr_pct = float(features.get("atr_pct") or 0.0)
        if model_id == "C":
            return bool(overheat >= 0.72 or (ret_15m >= 0.030 and breakout_ready >= 0.94 and atr_pct >= 0.040))
        return bool(overheat >= 0.84 and breakout_ready >= 0.92)

    def _crypto_score_profile(self, model_id: str, symbol: str, trend_bundle: dict[str, Any]) -> dict[str, Any]:
        feats = self._crypto_feature_pack(symbol, trend_bundle, model_id=model_id)
        allowed = self._crypto_symbol_allowed_for_model(model_id, symbol)
        feature_rank = int(feats.get("market_cap_rank") or 0)
        rank_min, rank_max_model = self._crypto_rank_band_for_model(model_id)
        if feature_rank > 0 and not self._rank_within_window(feature_rank, rank_min, rank_max_model):
            allowed = False
        with self._lock:
            run = (self.state.model_runs or {}).get(self._market_run_key("crypto", model_id)) or {}
        tune = self._read_model_runtime_tune_from_run(run if isinstance(run, dict) else {}, model_id, int(time.time()))
        tp_mul = float(tune.get("tp_mul") or MODEL_RUNTIME_TUNE_DEFAULTS.get(model_id, {}).get("tp_mul") or 1.0)
        sl_mul = float(tune.get("sl_mul") or MODEL_RUNTIME_TUNE_DEFAULTS.get(model_id, {}).get("sl_mul") or 1.0)
        threshold_raw = self._bybit_entry_threshold(model_id)
        chase_block = self._crypto_overheat_chase_block(model_id, feats)
        feats["chase_block"] = 1.0 if chase_block else 0.0
        current_price = float(feats.get("current_price") or 0.0)
        atr_abs = float(feats.get("atr_abs") or (current_price * 0.003))
        low_12 = float(feats.get("window_low_12") or current_price)
        high_12 = float(feats.get("window_high_12") or current_price)
        mid_12 = float(feats.get("window_mid_12") or current_price)
        low_36 = float(feats.get("window_low_36") or current_price)
        high_36 = float(feats.get("window_high_36") or current_price)
        mid_36 = float(feats.get("window_mid_36") or current_price)
        high_72 = float(feats.get("window_high_72") or current_price)
        gate_penalty = 0.028
        chase_penalty = 0.018
        score_lo = -0.220
        score_hi = 0.220
        if model_id == "A":
            strategy = "A-RangeReversionPlanner"
            gate_ok = bool(
                allowed
                and float(feats.get("lower_range_bias") or 0.0) >= 0.12
                and float(feats.get("range_36_pct") or 0.0) >= 0.008
                and float(feats.get("atr_pct") or 0.0) <= 0.065
                and float(feats.get("rsi") or 0.0) <= 58.0
                and float(feats.get("ema_gap_pct") or 0.0) >= -0.018
                and not chase_block
            )
            score = (
                (0.060 * float(feats.get("oversold_score") or 0.0))
                + (0.050 * float(feats.get("support_closeness") or 0.0))
                + (0.034 * float(feats.get("stability_score") or 0.0))
                + (0.030 * float(feats.get("rebound_strength") or 0.0))
                + (0.022 * float(feats.get("quality_score") or 0.0))
                + (0.024 * max(0.0, float(feats.get("mean_reversion_gap") or 0.0)))
                - (0.030 * float(feats.get("volatility_penalty") or 0.0))
                - (0.014 * float(feats.get("upper_range_bias") or 0.0))
            )
            entry_price = min(current_price, low_12 + (0.75 * atr_abs))
            stop_price = low_36 - (0.90 * atr_abs * sl_mul)
            risk = max(entry_price - stop_price, current_price * 0.002)
            plan = self._finalize_crypto_trade_plan(
                current_price=current_price,
                entry_price=entry_price,
                zone_low=entry_price - (0.35 * atr_abs),
                zone_high=entry_price + (0.35 * atr_abs),
                stop_price=stop_price,
                targets=[
                    max(mid_12, entry_price + (risk * (1.10 * tp_mul))),
                    max(high_36 - (0.10 * atr_abs), entry_price + (risk * (1.75 * tp_mul))),
                    max(high_72, entry_price + (risk * (2.45 * tp_mul))),
                ],
                ttl_minutes=30,
            )
            gate_reason = "레인지 하단 재진입 + 저변동 + 지지 근접"
        elif model_id == "B":
            strategy = "B-SupportReclaimPlanner"
            gate_ok = bool(
                allowed
                and float(feats.get("reclaim_strength") or 0.0) >= -0.06
                and float(feats.get("rebound_strength") or 0.0) >= 0.18
                and float(feats.get("ema_alignment") or 0.0) >= 0.42
                and float(feats.get("atr_pct") or 0.0) <= 0.075
                and not chase_block
            )
            score = (
                (0.054 * float(feats.get("reclaim_strength") or 0.0))
                + (0.038 * float(feats.get("support_closeness") or 0.0))
                + (0.028 * float(feats.get("ema_alignment") or 0.0))
                + (0.024 * float(feats.get("rebound_strength") or 0.0))
                + (0.022 * float(feats.get("quality_score") or 0.0))
                + (0.020 * float(feats.get("stability_score") or 0.0))
                - (0.026 * float(feats.get("volatility_penalty") or 0.0))
                - (0.018 * float(feats.get("overheat_penalty") or 0.0))
            )
            entry_price = max(min(current_price, mid_12), low_12 + (0.85 * atr_abs))
            stop_price = low_12 - (1.05 * atr_abs * sl_mul)
            risk = max(entry_price - stop_price, current_price * 0.002)
            plan = self._finalize_crypto_trade_plan(
                current_price=current_price,
                entry_price=entry_price,
                zone_low=entry_price - (0.30 * atr_abs),
                zone_high=entry_price + (0.30 * atr_abs),
                stop_price=stop_price,
                targets=[
                    max(high_12 - (0.05 * atr_abs), entry_price + (risk * (1.15 * tp_mul))),
                    max(high_36 - (0.05 * atr_abs), entry_price + (risk * (1.95 * tp_mul))),
                    max(high_72, entry_price + (risk * (2.75 * tp_mul))),
                ],
                ttl_minutes=40,
            )
            gate_reason = "지지 회복 + EMA 재안착 + 재반등 강도"
        elif model_id == "C":
            strategy = "C-CompressionBreakoutPlanner"
            gate_ok = bool(
                allowed
                and float(feats.get("compression_score") or 0.0) >= 0.18
                and float(feats.get("breakout_ready") or 0.0) >= 0.62
                and float(feats.get("ema_alignment") or 0.0) >= 0.48
                and float(feats.get("atr_pct") or 0.0) <= 0.085
                and not chase_block
            )
            score = (
                (0.058 * float(feats.get("compression_score") or 0.0))
                + (0.048 * float(feats.get("breakout_ready") or 0.0))
                + (0.034 * float(feats.get("ema_alignment") or 0.0))
                + (0.024 * float(feats.get("quality_score") or 0.0))
                + (0.018 * float(feats.get("stability_score") or 0.0))
                - (0.028 * float(feats.get("volatility_penalty") or 0.0))
                - (0.024 * float(feats.get("overheat_penalty") or 0.0))
            )
            box_height = max(high_12 - low_12, atr_abs * 1.20)
            entry_price = max(current_price, high_12 * 1.001)
            stop_price = entry_price - max((box_height * 0.70 * sl_mul), (atr_abs * 1.25 * sl_mul))
            plan = self._finalize_crypto_trade_plan(
                current_price=current_price,
                entry_price=entry_price,
                zone_low=(high_12 - (0.20 * atr_abs)),
                zone_high=entry_price + (0.30 * atr_abs),
                stop_price=stop_price,
                targets=[
                    entry_price + (box_height * (0.95 * tp_mul)),
                    entry_price + (box_height * (1.40 * tp_mul)),
                    entry_price + (box_height * (1.95 * tp_mul)),
                ],
                ttl_minutes=30,
            )
            gate_reason = "변동성 압축 + 상단 근접 + 돌파 계획"
        else:
            strategy = "D-ResetBouncePlanner"
            gate_ok = bool(
                allowed
                and float(feats.get("washout_score") or 0.0) >= 0.14
                and float(feats.get("lower_range_bias") or 0.0) >= 0.10
                and float(feats.get("atr_pct") or 0.0) <= 0.095
                and not chase_block
            )
            score = (
                (0.060 * float(feats.get("washout_score") or 0.0))
                + (0.038 * float(feats.get("reset_score") or 0.0))
                + (0.028 * float(feats.get("lower_range_bias") or 0.0))
                + (0.024 * float(feats.get("rebound_strength") or 0.0))
                + (0.020 * float(feats.get("quality_score") or 0.0))
                - (0.032 * float(feats.get("volatility_penalty") or 0.0))
                - (0.016 * float(feats.get("upper_range_bias") or 0.0))
            )
            entry_price = min(current_price, low_12 + (1.05 * atr_abs))
            stop_price = low_12 - (1.15 * atr_abs * sl_mul)
            risk = max(entry_price - stop_price, current_price * 0.002)
            plan = self._finalize_crypto_trade_plan(
                current_price=current_price,
                entry_price=entry_price,
                zone_low=entry_price - (0.40 * atr_abs),
                zone_high=entry_price + (0.28 * atr_abs),
                stop_price=stop_price,
                targets=[
                    max(mid_12, entry_price + (risk * (1.05 * tp_mul))),
                    max(mid_36, entry_price + (risk * (1.70 * tp_mul))),
                    max(high_36, entry_price + (risk * (2.35 * tp_mul))),
                ],
                ttl_minutes=35,
            )
            gate_reason = "급락 후 안정화 + 리셋 바운스 구간"
        if chase_block:
            score -= float(chase_penalty)
        score = _clamp(score, score_lo, score_hi)
        if not gate_ok or float(plan.get("risk_reward") or 0.0) < 1.10:
            score -= gate_penalty
        raw_score = _clamp(score, score_lo, score_hi)
        bayes_cfg = {
            "A": {"prior_logit": -0.48, "evidence_scale": 5.8},
            "B": {"prior_logit": -0.50, "evidence_scale": 6.0},
            "C": {"prior_logit": -0.60, "evidence_scale": 6.4},
            "D": {"prior_logit": -0.54, "evidence_scale": 5.8},
        }.get(model_id, {"prior_logit": -0.52, "evidence_scale": 6.0})
        prior_logit = float(bayes_cfg["prior_logit"])
        evidence_scale = float(bayes_cfg["evidence_scale"])
        posterior_logit = prior_logit + (raw_score * evidence_scale)
        threshold_logit = prior_logit + (float(threshold_raw) * evidence_scale)
        score_norm = _sigmoid(posterior_logit)
        threshold_norm = _sigmoid(threshold_logit)
        return {
            "strategy": strategy,
            "threshold": float(threshold_norm),
            "threshold_raw": float(threshold_raw),
            "score": float(score_norm),
            "score_raw": float(raw_score),
            "score_logit": float(posterior_logit),
            "threshold_logit": float(threshold_logit),
            "gate_ok": bool(gate_ok),
            "symbol_allowed": bool(allowed),
            "gate_reason": gate_reason,
            "features": feats,
            **plan,
        }

    def _bybit_score(self, model_id: str, symbol: str, trend_bundle: dict[str, Any]) -> float:
        return float(self._crypto_score_profile(model_id, symbol, trend_bundle).get("score") or 0.0)

    @staticmethod
    def _crypto_reason_text(profile: dict[str, Any]) -> str:
        feats = dict(profile.get("features") or {})
        score = float(profile.get("score") or 0.0)
        threshold = float(profile.get("threshold") or 0.0)
        entry = float(profile.get("entry_price") or 0.0)
        stop = float(profile.get("stop_loss_price") or 0.0)
        tp = float(profile.get("take_profit_price") or 0.0)
        rr = float(profile.get("risk_reward") or 0.0)
        return (
            f"{profile.get('strategy')} | "
            f"5m={float(feats.get('ret_5m') or 0.0)*100:+.2f}% "
            f"15m={float(feats.get('ret_15m') or 0.0)*100:+.2f}% "
            f"1h={float(feats.get('ret_1h') or 0.0)*100:+.2f}% "
            f"4h={float(feats.get('ret_4h') or 0.0)*100:+.2f}% "
            f"1d={float(feats.get('ret_1d') or 0.0)*100:+.2f}% | "
            f"FINAL={score:.3f}/{threshold:.3f} | "
            f"ENTRY={entry:.4f} SL={stop:.4f} TP={tp:.4f} RR={rr:.2f} | "
            f"EMA={float(feats.get('ema_signal') or 0.0):.2f} "
            f"RSI={float(feats.get('rsi') or 0.0):.1f} "
            f"CCI={float(feats.get('cci_raw') or 0.0):+.1f} "
            f"ATR={float(feats.get('atr_pct') or 0.0)*100:.2f}% | "
            f"Q={float(feats.get('quality_score') or 0.0):.2f} "
            f"compression={float(feats.get('compression_score') or 0.0):.2f} "
            f"support={float(feats.get('support_closeness') or 0.0):.2f} "
            f"OH={float(feats.get('overheat_penalty') or 0.0):.2f} "
            f"CHB={'Y' if float(feats.get('chase_block') or 0.0) > 0 else 'N'} "
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
        return float(threshold)

    def _crypto_leverage_bounds(self) -> tuple[float, float]:
        lev_min = _clamp(float(self.settings.bybit_leverage_min), 1.0, 30.0)
        lev_max = _clamp(float(self.settings.bybit_leverage_max), lev_min, 30.0)
        return (lev_min, lev_max)

    @staticmethod
    def _crypto_model_risk_profile(model_id: str) -> dict[str, float]:
        if model_id == "A":
            return {
                "lev_min": 15.0,
                "lev_max": 18.0,
                "order_pct_mul": 0.22,
                "hard_roe_cut": -0.26,
            }
        if model_id == "B":
            return {
                "lev_min": 18.0,
                "lev_max": 22.0,
                "order_pct_mul": 0.24,
                "hard_roe_cut": -0.28,
            }
        if model_id == "C":
            return {
                "lev_min": 24.0,
                "lev_max": 30.0,
                "order_pct_mul": 0.18,
                "hard_roe_cut": -0.32,
            }
        return {
            "lev_min": 15.0,
            "lev_max": 20.0,
            "order_pct_mul": 0.20,
            "hard_roe_cut": -0.24,
        }

    def _compute_crypto_leverage(self, model_id: str, score: float, threshold: float, volatility: float) -> float:
        lev_min_cfg, lev_max_cfg = self._crypto_leverage_bounds()
        prof = self._crypto_model_risk_profile(model_id)
        lev_min = _clamp(float(prof.get("lev_min") or 1.0), 1.0, 30.0)
        lev_max = _clamp(float(prof.get("lev_max") or lev_max_cfg), lev_min, 30.0)
        lev_min = max(lev_min, lev_min_cfg)
        lev_max = min(lev_max, lev_max_cfg)
        if lev_max < lev_min:
            lev_max = lev_min
        score_gap = max(0.0, float(score) - float(threshold))
        score_span = max(0.05, 1.0 - float(threshold))
        score_norm = _clamp(score_gap / score_span, 0.0, 1.0)
        vol_norm = _clamp(float(volatility), 0.0, 1.0)
        model_bias = 0.0
        if model_id == "A":
            model_bias = -0.34
        elif model_id == "B":
            model_bias = -0.08
        elif model_id == "C":
            model_bias = 0.02
        elif model_id == "D":
            model_bias = -0.14
        confidence = _clamp((0.70 * score_norm) + (0.30 * (1.0 - vol_norm)) + model_bias, 0.0, 1.0)
        lev = lev_min + ((lev_max - lev_min) * confidence)
        return round(_clamp(lev, lev_min, lev_max), 2)

    def _crypto_current_price(self, pos: dict[str, Any], prices: dict[str, float] | None = None) -> float:
        symbol = str((pos or {}).get("symbol") or "").upper().strip()
        if prices and symbol:
            px = float((prices or {}).get(symbol) or 0.0)
            if px > 0.0:
                return px
        if symbol:
            cached = float(self._bybit_last_prices.get(symbol) or 0.0)
            if cached > 0.0:
                return cached
        last_mark = float((pos or {}).get("last_mark_price_usd") or 0.0)
        if last_mark > 0.0:
            return last_mark
        return float((pos or {}).get("avg_price_usd") or 0.0)

    def _fetch_crypto_intrabar_candles(self, symbols: list[str], window_minutes: int | None = None) -> dict[str, list[dict[str, Any]]]:
        unique_symbols = [str(symbol or "").upper().strip() for symbol in list(symbols or []) if str(symbol or "").strip()]
        unique_symbols = list(dict.fromkeys(unique_symbols))
        if not unique_symbols:
            return {}
        lookback = int(window_minutes or max(10, min(30, int(self.settings.scan_interval_seconds / 60) + 4)))
        cache_seconds = max(10, min(60, int(self.settings.scan_interval_seconds // 8) or 30))
        out: dict[str, list[dict[str, Any]]] = {}
        for symbol in unique_symbols:
            try:
                rows = self.macro.fetch_binance_1m_ohlc(
                    symbol,
                    limit=lookback,
                    cache_seconds=cache_seconds,
                    binance_api_key=self.settings.binance_api_key,
                )
            except Exception:
                rows = []
            if rows:
                out[symbol] = [dict(row) for row in rows]
        return out

    @staticmethod
    def _intrabar_rows_since(rows: list[dict[str, Any]], from_ts: int) -> list[dict[str, Any]]:
        start_ts = int(from_ts or 0)
        if start_ts <= 0:
            return [dict(row) for row in list(rows or [])]
        out: list[dict[str, Any]] = []
        for row in list(rows or []):
            close_ts = int((row or {}).get("close_ts") or 0)
            if close_ts >= start_ts:
                out.append(dict(row or {}))
        return out

    @staticmethod
    def _intrabar_entry_price_hit(plan: dict[str, Any], candle: dict[str, Any]) -> float:
        entry_price = float((plan or {}).get("entry_price") or 0.0)
        zone_low = float((plan or {}).get("entry_zone_low") or 0.0)
        zone_high = float((plan or {}).get("entry_zone_high") or 0.0)
        low = float((candle or {}).get("low") or 0.0)
        high = float((candle or {}).get("high") or 0.0)
        if low <= 0.0 or high <= 0.0:
            return 0.0
        if entry_price > 0.0 and low <= entry_price <= high:
            return float(entry_price)
        if zone_low > 0.0 and zone_high > 0.0 and max(low, min(zone_low, zone_high)) <= min(high, max(zone_low, zone_high)):
            if entry_price > 0.0:
                return float(_clamp(entry_price, min(zone_low, zone_high), max(zone_low, zone_high)))
            return float((min(zone_low, zone_high) + max(zone_low, zone_high)) / 2.0)
        return 0.0

    def _intrabar_long_exit_hit(self, pos: dict[str, Any], candle: dict[str, Any]) -> tuple[float, str]:
        low = float((candle or {}).get("low") or 0.0)
        high = float((candle or {}).get("high") or 0.0)
        open_price = float((candle or {}).get("open") or 0.0)
        stop_loss_price = float((pos or {}).get("stop_loss_price") or 0.0)
        take_profit_price = float((pos or {}).get("take_profit_price") or 0.0)
        if low <= 0.0 or high <= 0.0:
            return (0.0, "")
        if stop_loss_price > 0.0 and low <= stop_loss_price and take_profit_price > 0.0 and high >= take_profit_price:
            policy = str(getattr(self.settings, "intrabar_conflict_policy", "conservative") or "conservative").lower()
            if policy == "aggressive":
                return (float(take_profit_price), f"TP intrabar both-hit {stop_loss_price:.4f}/{take_profit_price:.4f}")
            if policy == "neutral":
                ref_price = open_price if open_price > 0.0 else float((candle or {}).get("close") or 0.0)
                if ref_price <= 0.0:
                    ref_price = float((stop_loss_price + take_profit_price) / 2.0)
                if abs(ref_price - take_profit_price) < abs(ref_price - stop_loss_price):
                    return (float(take_profit_price), f"TP intrabar both-hit {stop_loss_price:.4f}/{take_profit_price:.4f}")
            return (float(stop_loss_price), f"SL intrabar both-hit {stop_loss_price:.4f}/{take_profit_price:.4f}")
        if stop_loss_price > 0.0 and low <= stop_loss_price:
            return (float(stop_loss_price), f"SL intrabar {stop_loss_price:.4f}")
        if take_profit_price > 0.0 and high >= take_profit_price:
            return (float(take_profit_price), f"TP intrabar {take_profit_price:.4f}")
        return (0.0, "")

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
            mark = float(pos.get("last_mark_price_usd") or 0.0)
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
        scored_at_ts = int(time.time())
        min_score_floor = _clamp(float(getattr(self.settings, "crypto_min_entry_score", 0.30) or 0.30), 0.0, 1.0)
        open_positions = set((run.get("bybit_positions") or {}).keys())
        out: list[dict[str, Any]] = []
        for symbol, price in prices.items():
            if symbol not in open_positions and not self._crypto_symbol_allowed_for_model(model_id, symbol):
                continue
            p = float(price or 0.0)
            if p <= 0:
                continue
            profile = self._crypto_score_profile(model_id, symbol, trend_bundle)
            score = float(profile.get("score") or 0.0)
            score_raw = float(profile.get("score_raw") or 0.0)
            threshold = float(profile.get("threshold") or 0.0)
            threshold_raw = float(profile.get("threshold_raw") or self._bybit_entry_threshold(model_id))
            effective_threshold = max(threshold, min_score_floor)
            indicators = dict(profile.get("features") or {})
            vol = self._crypto_volatility_proxy(symbol)
            sl_pct = float(profile.get("sl_pct") or 0.0)
            tp_pct = float(profile.get("tp_pct") or 0.0)
            out.append(
                {
                    "symbol": symbol,
                    "strategy": str(profile.get("strategy") or ""),
                    "scored_at_ts": int(scored_at_ts),
                    "score": score,
                    "score_raw": score_raw,
                    "price_usd": p,
                    "market_cap_rank": int(indicators.get("market_cap_rank") or 0),
                    "entry_threshold": float(effective_threshold),
                    "entry_threshold_raw": float(threshold_raw),
                    "above_threshold": bool(score > effective_threshold),
                    "in_position": bool(symbol in open_positions),
                    "volatility": float(vol),
                    "entry_price": float(profile.get("entry_price") or p),
                    "entry_zone_low": float(profile.get("entry_zone_low") or p),
                    "entry_zone_high": float(profile.get("entry_zone_high") or p),
                    "stop_loss_price": float(profile.get("stop_loss_price") or 0.0),
                    "target_price_1": float(profile.get("target_price_1") or 0.0),
                    "target_price_2": float(profile.get("target_price_2") or 0.0),
                    "target_price_3": float(profile.get("target_price_3") or 0.0),
                    "take_profit_price": float(profile.get("take_profit_price") or 0.0),
                    "risk_reward": float(profile.get("risk_reward") or 0.0),
                    "entry_ready": bool(profile.get("entry_ready")),
                    "setup_state": str(profile.get("setup_state") or ""),
                    "setup_expiry_ts": int(profile.get("setup_expiry_ts") or 0),
                    "tp_pct": float(tp_pct),
                    "sl_pct": float(sl_pct),
                    "gate_ok": bool(profile.get("gate_ok")),
                    "symbol_allowed": bool(profile.get("symbol_allowed", True)),
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
                        "chase_block": float(indicators.get("chase_block") or 0.0),
                        "compression_score": float(indicators.get("compression_score") or 0.0),
                        "support_closeness": float(indicators.get("support_closeness") or 0.0),
                        "rebound_strength": float(indicators.get("rebound_strength") or 0.0),
                        "reclaim_strength": float(indicators.get("reclaim_strength") or 0.0),
                        "pullback_from_high": float(indicators.get("pullback_from_high") or 0.0),
                        "breakout_strength": float(indicators.get("breakout_strength") or 0.0),
                        "score_raw": float(score_raw),
                        "entry_threshold_raw": float(threshold_raw),
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
            model_scale = 0.72
        elif model_id == "B":
            model_scale = 1.06
        elif model_id == "C":
            model_scale = 0.88
        elif model_id == "D":
            model_scale = 0.76
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
            "A": "A-RangeReversionPlanner",
            "B": "B-SupportReclaimPlanner",
            "C": "C-CompressionBreakoutPlanner",
            "D": "D-ResetBouncePlanner",
        }.get(model_id, "")
        for pos in list((run.get("bybit_positions") or {}).values()):
            symbol = str(pos.get("symbol") or "")
            entry = float(pos.get("avg_price_usd") or 0.0)
            current = float(self._crypto_current_price(pos, prices))
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
            pos["last_mark_price_usd"] = float(marked["mark_price_usd"])
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
            stop_loss_price = float(pos.get("stop_loss_price") or 0.0)
            take_profit_price = float(pos.get("take_profit_price") or 0.0)
            if stop_loss_price > 0.0 and current <= stop_loss_price:
                self._close_model_bybit_position(
                    model_id,
                    run,
                    pos,
                    current,
                    f"SL {current:.4f}/{stop_loss_price:.4f}",
                )
                continue
            if take_profit_price > 0.0 and current >= take_profit_price:
                self._close_model_bybit_position(
                    model_id,
                    run,
                    pos,
                    current,
                    f"TP {current:.4f}/{take_profit_price:.4f}",
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
        close_mode: str = "",
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
        now_ts = int(time.time())

        del positions[symbol]
        run["bybit_positions"] = positions
        run["bybit_cash_usd"] = float(run.get("bybit_cash_usd") or 0.0) + cash_back
        run.setdefault("trades", []).append(
            {
                "ts": now_ts,
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
                "close_mode": str(close_mode or ("intrabar" if "intrabar" in str(reason or "").lower() else "spot")),
                "model_id": model_id,
            }
        )
        reason_u = str(reason or "").upper()
        if reason_u.startswith("HARD-ROE") or reason_u.startswith("SL ") or reason_u.startswith("LIQ"):
            self._set_crypto_reentry_cooldown(run, model_id, symbol, reason, now_ts)
        self._prune_run_trades(run, now_ts)
        return True

    def _open_crypto_demo_position(
        self,
        *,
        model_id: str,
        run: dict[str, Any],
        symbol: str,
        fill_price: float,
        order_usd: float,
        order_pct: float,
        leverage: float,
        score: float,
        tp_pct: float,
        sl_pct: float,
        plan: dict[str, Any],
        reason_text: str,
        atr_pct: float,
        opened_at: int,
        fill_mode: str = "spot",
    ) -> bool:
        price = max(0.0, float(fill_price or 0.0))
        margin_usd = max(0.0, float(order_usd or 0.0))
        if not symbol or price <= 0.0 or margin_usd <= 0.0:
            return False
        notional_usd = margin_usd * max(1.0, float(leverage or 1.0))
        qty = notional_usd / max(price, 1e-9)
        run.setdefault("bybit_positions", {})[symbol] = {
            "symbol": symbol,
            "side": "long",
            "qty": qty,
            "avg_price_usd": price,
            "last_mark_price_usd": price,
            "margin_usd": margin_usd,
            "order_pct": float(order_pct),
            "leverage": float(leverage),
            "notional_usd": notional_usd,
            "opened_at": int(opened_at),
            "entry_score": float(score),
            "tp_pct": float(tp_pct),
            "sl_pct": float(sl_pct),
            "entry_plan_price": float((plan or {}).get("entry_price") or price),
            "entry_zone_low": float((plan or {}).get("entry_zone_low") or 0.0),
            "entry_zone_high": float((plan or {}).get("entry_zone_high") or 0.0),
            "stop_loss_price": float((plan or {}).get("stop_loss_price") or 0.0),
            "take_profit_price": float((plan or {}).get("take_profit_price") or 0.0),
            "target_price_1": float((plan or {}).get("target_price_1") or 0.0),
            "target_price_2": float((plan or {}).get("target_price_2") or 0.0),
            "target_price_3": float((plan or {}).get("target_price_3") or 0.0),
            "risk_reward": float((plan or {}).get("risk_reward") or 0.0),
            "setup_state": str((plan or {}).get("setup_state") or ""),
            "fill_mode": str(fill_mode or "spot"),
            "reason": str(reason_text or ""),
        }
        run["bybit_cash_usd"] = float(run.get("bybit_cash_usd") or 0.0) - margin_usd
        run.setdefault("trades", []).append(
            {
                "ts": int(opened_at),
                "source": "crypto_demo",
                "side": "buy",
                "symbol": symbol,
                "token_address": symbol,
                "qty": qty,
                "price_usd": price,
                "notional_usd": notional_usd,
                "margin_usd": margin_usd,
                "order_pct": float(order_pct),
                "leverage": float(leverage),
                "fill_mode": str(fill_mode or "spot"),
                "pnl_usd": 0.0,
                "pnl_pct": 0.0,
                "reason": (
                    f"{reason_text} | alloc={order_pct*100:.1f}% | lev={float(leverage):.2f}x tp={tp_pct*100:.1f}% sl={sl_pct*100:.1f}% "
                    f"entry={float((plan or {}).get('entry_price') or price):.4f} rr={float((plan or {}).get('risk_reward') or 0.0):.2f} atr={atr_pct:.2f}%"
                ),
                "model_id": model_id,
            }
        )
        self._record_last_entry_alloc(run, "crypto", symbol, order_pct, score, int(opened_at))
        self._prune_run_trades(run, int(opened_at))
        return True

    def _process_crypto_intrabar_window(
        self,
        model_id: str,
        run: dict[str, Any],
        prices: dict[str, float],
        candles_by_symbol: dict[str, list[dict[str, Any]]],
        now_ts: int,
    ) -> None:
        if not candles_by_symbol:
            run["crypto_intrabar_eval_ts"] = int(now_ts)
            return
        eval_from_ts = int(run.get("crypto_intrabar_eval_ts") or max(0, int(now_ts) - max(300, int(self.settings.scan_interval_seconds))))

        # Existing open positions: if SL/TP was touched intrabar, reflect it even if current price recovered.
        for pos in list((run.get("bybit_positions") or {}).values()):
            symbol = str((pos or {}).get("symbol") or "").upper().strip()
            rows = self._intrabar_rows_since(candles_by_symbol.get(symbol) or [], max(eval_from_ts, int((pos or {}).get("opened_at") or 0)))
            if not rows:
                continue
            for candle in rows:
                exit_price, exit_reason = self._intrabar_long_exit_hit(pos, candle)
                if exit_price > 0.0 and exit_reason:
                    self._close_model_bybit_position(model_id, run, pos, exit_price, exit_reason, close_mode="intrabar")
                    break
            else:
                last_close = float((rows[-1] or {}).get("close") or 0.0)
                if last_close > 0.0:
                    pos["last_mark_price_usd"] = last_close

        if not (self._is_market_autotrade_enabled("crypto") and self._is_autotrade_model_enabled("crypto", model_id)):
            run["crypto_intrabar_eval_ts"] = int(now_ts)
            return

        positions = run.get("bybit_positions") or {}
        max_positions = max(1, int(self.settings.bybit_max_positions))
        min_score_floor = _clamp(float(getattr(self.settings, "crypto_min_entry_score", 0.30) or 0.30), 0.0, 1.0)
        self._normalize_crypto_reentry_cooldowns(run, int(now_ts))
        pending_rows = sorted(
            [dict(row or {}) for row in list(run.get("latest_crypto_signals") or []) if isinstance(row, dict)],
            key=lambda row: float(row.get("score") or 0.0),
            reverse=True,
        )

        for row in pending_rows:
            if len(positions) >= max_positions:
                break
            symbol = str(row.get("symbol") or "").upper().strip()
            if not symbol or symbol in positions:
                continue
            score = float(row.get("score") or 0.0)
            entry_threshold = max(min_score_floor, float(row.get("entry_threshold") or min_score_floor))
            if score <= entry_threshold:
                continue
            if float(row.get("risk_reward") or 0.0) < 1.10:
                continue
            if int(row.get("setup_expiry_ts") or 0) > 0 and int(now_ts) > int(row.get("setup_expiry_ts") or 0):
                continue
            blocked, _, _ = self._crypto_reentry_blocked(run, symbol, int(now_ts))
            if blocked:
                continue
            rows = self._intrabar_rows_since(
                candles_by_symbol.get(symbol) or [],
                max(eval_from_ts, int(row.get("scored_at_ts") or 0)),
            )
            if not rows:
                continue
            hit_index = -1
            fill_price = 0.0
            for idx, candle in enumerate(rows):
                fill_price = self._intrabar_entry_price_hit(row, candle)
                if fill_price > 0.0:
                    hit_index = idx
                    break
            if hit_index < 0 or fill_price <= 0.0:
                continue
            cash = float(run.get("bybit_cash_usd") or 0.0)
            min_order = float(self.settings.bybit_min_order_usd)
            if cash < min_order:
                break
            order_pct = self._demo_order_pct_for_entry("crypto", score, entry_threshold)
            order_usd = min(cash, max(min_order, self._crypto_target_order_usd(run, order_pct, prices)))
            if order_usd < min_order:
                continue
            leverage = float(row.get("leverage") or 0.0)
            if leverage <= 0.0:
                leverage = self._compute_crypto_leverage(model_id, float(score), float(entry_threshold), float(row.get("volatility") or 0.0))
            tp_pct = float(row.get("tp_pct") or 0.0)
            sl_pct = float(row.get("sl_pct") or 0.0)
            if sl_pct <= 0.0 or tp_pct <= 0.0:
                sl_pct, tp_pct = self._compute_risk_profile(model_id, "crypto", float(row.get("volatility") or 0.0))
            atr_pct = float(((row.get("indicator_snapshot") or {}) if isinstance(row.get("indicator_snapshot"), dict) else {}).get("atr_pct") or 0.0)
            reason_text = str(row.get("reason") or "").strip()
            if not reason_text:
                reason_text = f"{str(row.get('strategy') or ('MODEL-' + model_id))} | intrabar-fill conf={score:.4f} thr={entry_threshold:.4f}"
            opened_at = int((rows[hit_index] or {}).get("close_ts") or int(now_ts))
            opened = self._open_crypto_demo_position(
                model_id=model_id,
                run=run,
                symbol=symbol,
                fill_price=fill_price,
                order_usd=order_usd,
                order_pct=order_pct,
                leverage=leverage,
                score=score,
                tp_pct=tp_pct,
                sl_pct=sl_pct,
                plan=row,
                reason_text=reason_text,
                atr_pct=atr_pct,
                opened_at=opened_at,
                fill_mode="intrabar",
            )
            if not opened:
                continue
            pos = (run.get("bybit_positions") or {}).get(symbol)
            if not isinstance(pos, dict):
                continue
            for candle in rows[hit_index:]:
                exit_price, exit_reason = self._intrabar_long_exit_hit(pos, candle)
                if exit_price > 0.0 and exit_reason:
                    self._close_model_bybit_position(model_id, run, pos, exit_price, exit_reason, close_mode="intrabar")
                    break

        run["crypto_intrabar_eval_ts"] = int(now_ts)

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
        now = int(time.time())
        positions = run.get("bybit_positions") or {}
        max_positions = max(1, int(self.settings.bybit_max_positions))
        if len(positions) >= max_positions:
            return
        if model_id == "C":
            today_key = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%d")
            todays_buys = sum(
                1
                for tr in list(run.get("trades") or [])
                if str((tr or {}).get("source") or "").lower() == "crypto_demo"
                and str((tr or {}).get("side") or "").lower() == "buy"
                and datetime.fromtimestamp(int((tr or {}).get("ts") or 0), tz=timezone.utc).strftime("%Y-%m-%d") == today_key
            )
            if todays_buys >= 10:
                return

        ranked: list[tuple[str, float]] = []
        leverage_by_symbol: dict[str, float] = {}
        threshold_by_symbol: dict[str, float] = {}
        threshold_raw_by_symbol: dict[str, float] = {}
        score_raw_by_symbol: dict[str, float] = {}
        reason_by_symbol: dict[str, str] = {}
        strategy_by_symbol: dict[str, str] = {}
        allowed_by_symbol: dict[str, bool] = {}
        snapshot_by_symbol: dict[str, dict[str, Any]] = {}
        plan_by_symbol: dict[str, dict[str, Any]] = {}
        min_score_floor = _clamp(float(getattr(self.settings, "crypto_min_entry_score", 0.30) or 0.30), 0.0, 1.0)
        if scored_signals:
            for row in scored_signals:
                symbol = str(row.get("symbol") or "")
                if not symbol or symbol in positions:
                    continue
                ranked.append((symbol, float(row.get("score") or 0.0)))
                leverage_by_symbol[symbol] = float(row.get("leverage") or 0.0)
                threshold_by_symbol[symbol] = max(
                    min_score_floor,
                    float(row.get("entry_threshold") or min_score_floor),
                )
                threshold_raw_by_symbol[symbol] = float(row.get("entry_threshold_raw") or self._bybit_entry_threshold(model_id))
                score_raw_by_symbol[symbol] = float(row.get("score_raw") or 0.0)
                reason_by_symbol[symbol] = str(row.get("reason") or "")
                strategy_by_symbol[symbol] = str(row.get("strategy") or "")
                allowed_by_symbol[symbol] = bool(row.get("symbol_allowed", True))
                snapshot_by_symbol[symbol] = dict(row.get("indicator_snapshot") or {})
                plan_by_symbol[symbol] = {
                    "entry_price": float(row.get("entry_price") or 0.0),
                    "entry_zone_low": float(row.get("entry_zone_low") or 0.0),
                    "entry_zone_high": float(row.get("entry_zone_high") or 0.0),
                    "stop_loss_price": float(row.get("stop_loss_price") or 0.0),
                    "take_profit_price": float(row.get("take_profit_price") or 0.0),
                    "target_price_1": float(row.get("target_price_1") or 0.0),
                    "target_price_2": float(row.get("target_price_2") or 0.0),
                    "target_price_3": float(row.get("target_price_3") or 0.0),
                    "risk_reward": float(row.get("risk_reward") or 0.0),
                    "entry_ready": bool(row.get("entry_ready")),
                    "setup_state": str(row.get("setup_state") or ""),
                    "setup_expiry_ts": int(row.get("setup_expiry_ts") or 0),
                    "tp_pct": float(row.get("tp_pct") or 0.0),
                    "sl_pct": float(row.get("sl_pct") or 0.0),
                }
        else:
            for symbol, price in prices.items():
                if price <= 0 or symbol in positions:
                    continue
                profile = self._crypto_score_profile(model_id, symbol, trend_bundle)
                ranked.append((symbol, float(profile.get("score") or 0.0)))
                threshold_by_symbol[symbol] = max(
                    min_score_floor,
                    float(profile.get("threshold") or min_score_floor),
                )
                threshold_raw_by_symbol[symbol] = float(profile.get("threshold_raw") or self._bybit_entry_threshold(model_id))
                score_raw_by_symbol[symbol] = float(profile.get("score_raw") or 0.0)
                reason_by_symbol[symbol] = str(self._crypto_reason_text(profile))
                strategy_by_symbol[symbol] = str(profile.get("strategy") or "")
                allowed_by_symbol[symbol] = bool(profile.get("symbol_allowed", True))
                snapshot_by_symbol[symbol] = {
                    "trend_stack": float((profile.get("features") or {}).get("trend_stack") or 0.0),
                    "overheat_penalty": float((profile.get("features") or {}).get("overheat_penalty") or 0.0),
                    "atr_pct": float((profile.get("features") or {}).get("atr_pct") or 0.0) * 100.0,
                }
                plan_by_symbol[symbol] = {
                    "entry_price": float(profile.get("entry_price") or 0.0),
                    "entry_zone_low": float(profile.get("entry_zone_low") or 0.0),
                    "entry_zone_high": float(profile.get("entry_zone_high") or 0.0),
                    "stop_loss_price": float(profile.get("stop_loss_price") or 0.0),
                    "take_profit_price": float(profile.get("take_profit_price") or 0.0),
                    "target_price_1": float(profile.get("target_price_1") or 0.0),
                    "target_price_2": float(profile.get("target_price_2") or 0.0),
                    "target_price_3": float(profile.get("target_price_3") or 0.0),
                    "risk_reward": float(profile.get("risk_reward") or 0.0),
                    "entry_ready": bool(profile.get("entry_ready")),
                    "setup_state": str(profile.get("setup_state") or ""),
                    "setup_expiry_ts": int(profile.get("setup_expiry_ts") or 0),
                    "tp_pct": float(profile.get("tp_pct") or 0.0),
                    "sl_pct": float(profile.get("sl_pct") or 0.0),
                }
        ranked.sort(key=lambda row: row[1], reverse=True)

        threshold = min_score_floor
        opened = 0
        guard = self._entry_guard_profile(model_id, "crypto")
        loss_guard = self._run_loss_guard(run)
        guard_boost = float(guard.get("threshold_boost") or 0.0) + float(loss_guard.get("threshold_boost") or 0.0)
        self._normalize_crypto_reentry_cooldowns(run, now)
        for symbol, score in ranked:
            if len(positions) + opened >= max_positions:
                break
            if score < min_score_floor:
                continue
            blocked, _, _ = self._crypto_reentry_blocked(run, symbol, now)
            if blocked:
                continue
            if not bool(allowed_by_symbol.get(symbol, True)):
                continue
            entry_threshold = float(threshold_by_symbol.get(symbol) or threshold) + guard_boost
            if score <= entry_threshold:
                continue
            plan = dict(plan_by_symbol.get(symbol) or {})
            if int(plan.get("setup_expiry_ts") or 0) > 0 and now > int(plan.get("setup_expiry_ts") or 0):
                continue
            if float(plan.get("risk_reward") or 0.0) < 1.10:
                continue
            if not bool(plan.get("entry_ready")):
                continue
            cash = float(run.get("bybit_cash_usd") or 0.0)
            min_order = float(self.settings.bybit_min_order_usd)
            if cash < min_order:
                break
            order_pct = self._demo_order_pct_for_entry("crypto", score, entry_threshold)
            order_pct = _clamp(order_pct, 0.01, 0.95)
            target_order_usd = self._crypto_target_order_usd(run, order_pct, prices)
            order_usd = min(cash, max(min_order, target_order_usd))
            if order_usd < min_order:
                continue
            price = float(prices.get(symbol) or 0.0)
            if price <= 0:
                continue
            vol = self._crypto_volatility_proxy(symbol)
            sl_pct = float(plan.get("sl_pct") or 0.0)
            tp_pct = float(plan.get("tp_pct") or 0.0)
            if sl_pct <= 0.0 or tp_pct <= 0.0:
                sl_pct, tp_pct = self._compute_risk_profile(model_id, "crypto", vol)
            leverage = float(leverage_by_symbol.get(symbol) or 0.0)
            if leverage <= 0.0:
                leverage = self._compute_crypto_leverage(model_id, float(score), float(entry_threshold), vol)
            score_raw = float(score_raw_by_symbol.get(symbol) or 0.0)
            threshold_raw = float(threshold_raw_by_symbol.get(symbol) or self._bybit_entry_threshold(model_id))
            reason_text = str(reason_by_symbol.get(symbol) or "").strip()
            if not reason_text:
                reason_text = (
                    f"{strategy_by_symbol.get(symbol) or ('MODEL-' + model_id)} | "
                    f"conf={score:.4f} thr={entry_threshold:.4f} raw={score_raw:+.4f}/{threshold_raw:+.4f}"
                )
            indicator_snapshot = dict(snapshot_by_symbol.get(symbol) or {})
            atr_pct = float(indicator_snapshot.get("atr_pct") or 0.0)
            fill_price = float(price)
            zone_low = float(plan.get("entry_zone_low") or 0.0)
            zone_high = float(plan.get("entry_zone_high") or 0.0)
            if zone_low > 0.0 and zone_high > 0.0:
                fill_price = _clamp(float(price), min(zone_low, zone_high), max(zone_low, zone_high))
            if self._open_crypto_demo_position(
                model_id=model_id,
                run=run,
                symbol=symbol,
                fill_price=fill_price,
                order_usd=order_usd,
                order_pct=order_pct,
                leverage=leverage,
                score=score,
                tp_pct=tp_pct,
                sl_pct=sl_pct,
                plan=plan,
                reason_text=reason_text,
                atr_pct=atr_pct,
                opened_at=now,
                fill_mode="spot",
            ):
                opened += 1

    def _model_metrics(self, model_id: str, run: dict[str, Any]) -> dict[str, Any]:
        trades = [t for t in list(run.get("trades") or []) if not self._is_live_trade_row(t)]
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
            if str((pos or {}).get("mode") or "").strip().lower() == "live":
                continue
            token_address = str(pos.get("token_address") or "")
            avg = float(pos.get("avg_price_usd") or 0.0)
            current = self._resolve_price_cached(token_address, fallback=avg)
            qty = float(pos.get("qty") or 0.0)
            if current <= 0:
                continue
            meme_value += current * qty
            meme_unrealized += (current - avg) * qty

        bybit_unrealized = 0.0
        bybit_value = 0.0
        if bybit_enabled:
            for pos in (run.get("bybit_positions") or {}).values():
                current = float(self._crypto_current_price(pos))
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

    def _market_trade_stats(self, run: dict[str, Any], market: str, mode_filter: str = "paper") -> dict[str, float]:
        source_name = "memecoin" if market == "meme" else "crypto_demo"
        trades = list(run.get("trades") or [])
        sells: list[dict[str, Any]] = []
        for t in trades:
            if str(t.get("side") or "").lower() != "sell":
                continue
            if str(t.get("source") or "").lower() != source_name:
                continue
            if mode_filter == "live":
                if not self._is_live_trade_row(t):
                    continue
                if market == "meme" and not self._live_trade_is_realized(t):
                    continue
            else:
                if self._is_live_trade_row(t):
                    continue
            sells.append(t)
        if mode_filter == "live" and market == "meme":
            realized_values = [float(self._live_trade_realized_pnl_usd(t) or 0.0) for t in sells]
        else:
            realized_values = [float(t.get("pnl_usd") or 0.0) for t in sells]
        realized = sum(realized_values)
        wins = sum(1 for pnl in realized_values if float(pnl) > 0.0)
        closed = len(sells)
        win_rate = (wins / closed * 100.0) if closed > 0 else 0.0
        return {
            "realized_pnl_usd": float(realized),
            "wins": float(wins),
            "closed_trades": float(closed),
            "win_rate": float(win_rate),
        }

    def _model_metrics_market(
        self,
        model_id: str,
        run: dict[str, Any],
        market: str,
        mode_filter: str = "paper",
    ) -> dict[str, Any]:
        market_id = "meme" if market == "meme" else "crypto"
        if market_id == "meme":
            seed = float(run.get("meme_seed_usd") or self.state.demo_seed_usdt)
            cash = float(run.get("meme_cash_usd") or 0.0)
            value = 0.0
            unrealized = 0.0
            for pos in (run.get("meme_positions") or {}).values():
                is_live_pos = str((pos or {}).get("mode") or "").strip().lower() == "live"
                if mode_filter == "live" and not is_live_pos:
                    continue
                if mode_filter != "live" and is_live_pos:
                    continue
                token_address = str(pos.get("token_address") or "")
                avg = float(pos.get("avg_price_usd") or 0.0)
                current = self._resolve_price_cached(token_address, fallback=avg)
                qty = float(pos.get("qty") or 0.0)
                if current <= 0:
                    continue
                value += current * qty
                unrealized += (current - avg) * qty
            if mode_filter == "live":
                open_positions = sum(
                    1
                    for p in (run.get("meme_positions") or {}).values()
                    if str((p or {}).get("mode") or "").strip().lower() == "live"
                )
            else:
                open_positions = sum(
                    1
                    for p in (run.get("meme_positions") or {}).values()
                    if str((p or {}).get("mode") or "").strip().lower() != "live"
                )
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
                    current = float(self._crypto_current_price(pos))
                    marked = self._mark_crypto_position(pos, current)
                    value += float(marked["position_equity_usd"])
                    unrealized += float(marked["pnl_usd"])
                open_positions = len(run.get("bybit_positions") or {})

        equity = cash + value
        total_pnl = equity - seed
        t = self._market_trade_stats(run, market_id, mode_filter=mode_filter)
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
        for model_id in self._all_model_ids():
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

    def _maybe_emit_daily_git_report(self, now_ts: int) -> None:
        if not bool(getattr(self.settings, "git_daily_reports_enabled", False)):
            return
        previous_day = (datetime.fromtimestamp(int(now_ts), tz=timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        last_state = self._load_daily_git_report_state()
        if str(last_state.get("last_report_date") or "") == previous_day:
            return
        with self._lock:
            rows = [
                dict(row or {})
                for row in list(self.state.daily_pnl or [])
                if str((row or {}).get("date") or "") == previous_day
            ]
        if not rows:
            return
        output_dir = self._repo_root / str(self.settings.git_daily_reports_path or "reports/daily_pnl")
        written_files = write_daily_pnl_report(previous_day, rows, str(output_dir))
        state_payload: dict[str, Any] = {
            "last_report_date": previous_day,
            "last_report_files": [str(Path(path).resolve()) for path in written_files],
            "last_report_ts": int(now_ts),
        }
        if bool(getattr(self.settings, "git_daily_reports_autocommit", False)):
            commit_message = f"daily pnl report: {previous_day}"
            commit_result = git_commit_report_files(
                str(self._repo_root),
                written_files,
                commit_message,
                push=bool(getattr(self.settings, "git_daily_reports_autopush", False)),
                branch=str(getattr(self.settings, "git_daily_reports_branch", "") or ""),
                author_name=str(getattr(self.settings, "git_committer_name", "") or ""),
                author_email=str(getattr(self.settings, "git_committer_email", "") or ""),
            )
            state_payload["git"] = dict(commit_result or {})
            if not bool(commit_result.get("ok")):
                self._append_runtime_event_only(
                    "core:daily_git_report",
                    level="warn",
                    status="error",
                    error=str(commit_result.get("error") or "git_daily_report_failed"),
                    detail=f"daily pnl report for {previous_day}",
                    title="일일 Git 리포트 실패",
                )
            else:
                self._push_alert(
                    "info",
                    "일일 Git 리포트",
                    f"{previous_day} 리포트 저장 완료"
                    + (" | commit" if bool(commit_result.get("committed")) else "")
                    + (" | push" if bool(commit_result.get("pushed")) else ""),
                    send_telegram=False,
                )
        self._save_daily_git_report_state(state_payload, int(now_ts))

    def _sync_wallet(self, now: int, force: bool = False) -> None:
        if not self.settings.phantom_wallet_address:
            return
        if not force and (now - self._last_wallet_sync) < self.settings.wallet_update_seconds:
            return
        self._last_wallet_sync = now
        try:
            include_tokens = self._live_meme_watch_tokens()
            rows = self.wallet.fetch_wallet_assets(
                self.settings.phantom_wallet_address,
                self.dex,
                self.settings.min_wallet_asset_usd,
                include_token_addresses=include_tokens,
            )
            with self._lock:
                self.state.wallet_assets = rows
                self.state.last_wallet_sync_ts = now
            self._refresh_live_meme_basis(rows, now)
            self._detect_and_apply_external_live_flows(now)
            self._reconcile_live_meme_trade_history(now)
            self._sync_live_seed_if_idle(now)
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
            self._sync_live_seed_if_idle(now)
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self.state.bybit_error = str(exc)

    def _poll_telegram(self, now: int, run_epoch: int | None = None) -> None:
        if run_epoch is not None and int(run_epoch) != int(self._run_epoch):
            return
        if not self.settings.telegram_polling_enabled or not self.telegram.enabled:
            self._release_telegram_poll_lock(force=True)
            return
        if (now - self._last_telegram_poll) < self.settings.telegram_poll_interval_seconds:
            return
        if not self._acquire_telegram_poll_lock(now):
            self._last_telegram_poll = now
            self._append_runtime_event_only(
                "core:telegram_poll_lock",
                level="info",
                status="skip",
                error="telegram_poll_lock_wait",
                detail="poll lock held; silent backoff",
                title="텔레그램 폴링 잠금 대기",
            )
            return
        self._last_telegram_poll = now

        with self._lock:
            offset = int(self.state.telegram_offset) + 1
        if not self._telegram_inflight_lock.acquire(blocking=False):
            return
        try:
            long_poll_timeout = max(10, min(30, int(self.settings.telegram_poll_interval_seconds) * 5))
            updates = self.telegram.get_updates(offset=offset, timeout=long_poll_timeout)
        except Exception as exc:  # noqa: BLE001
            err_text = str(exc)
            low = err_text.lower()
            if "409" in low or "conflict" in low:
                self.telegram.delete_webhook(drop_pending_updates=False)
                self._append_runtime_event_only(
                    "core:telegram_poll",
                    level="warn",
                    status="skip",
                    error=err_text,
                    detail="telegram getUpdates conflict; webhook cleared and backoff applied",
                    title="텔레그램 폴링 충돌",
                )
                self._last_telegram_poll = int(now) + 5
                return
            self._emit_runtime_error("core:telegram_poll", "텔레그램 폴링 오류", err_text, level="warn", cooldown_seconds=300)
            return
        finally:
            try:
                self._telegram_inflight_lock.release()
            except Exception:
                pass

        if run_epoch is not None and int(run_epoch) != int(self._run_epoch):
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
        if cmd in {"/position", "/pos"}:
            cmd = "/positions"

        def _market_name(market: str, mid: str) -> str:
            return self._display_model_name(mid, market)

        def _read_runs() -> dict[str, Any]:
            with self._lock:
                return dict(self.state.model_runs or {})

        if cmd in {"/start", "/help"}:
            return (
                "명령어 상세 도움말\n"
                "[1) 모델]\n"
                "/models - 현재 구조 요약(밈 단일 엔진 / 크립토 4모델)\n"
                "/status_meme - 밈 단일 엔진 상태\n"
                "/status_crypto - 크립토 4모델 상태\n"
                "/pnl - 데모 손익 요약(밈 엔진 + 크립토 4모델)\n"
                "/pnl_meme - 밈 엔진 손익만\n"
                "/pnl_crypto - 크립토 4모델 손익만\n"
                "\n"
                "[2) 데모/실전 포지션]\n"
                "/positions - 실전 자산/관리포지션 요약\n"
                "/position - /positions 동일(실전 요약)\n"
                "/positions_meme - 밈 엔진 포지션/최근 체결\n"
                "/positions_crypto - 크립토 4모델 포지션\n"
                "/meme_balance - 팬텀 지갑 자산\n"
                "/bybit_balance - 거래소 자산\n"
                "\n"
                "[3) 설정/튜닝/소스]\n"
                "/set_models crypto A,B,C,D - 데모 크립토 모델 설정\n"
                "/set_live_models crypto A,B,C,D - 실전 크립토 모델 설정\n"
                "/live_markets - 실전 밈/크립토 ON/OFF 상태\n"
                "/set_live_market <meme|crypto> <on|off> - 실전 시장별 ON/OFF\n"
                f"/tune_status - 자동튜닝 상태({self._autotune_interval_label()} 주기, 크립토 모델)\n"
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
        if cmd == "/models":
            meme_text = "단일 엔진(THEME_SNIPER 메인 / NARRATIVE 서브)"
            crypto_ids = ",".join(f"{mid}:{self._market_model_name('crypto', mid)}" for mid in self._autotrade_model_ids("crypto"))
            live_meme_ids = "단일 엔진"
            live_crypto_ids = ",".join(f"{mid}:{self._market_model_name('crypto', mid)}" for mid in self._live_model_ids("crypto"))
            return (
                "모델 설정\n"
                f"- 데모 밈: {meme_text}\n"
                f"  · THEME 0.10 SOL | SNIPER 0.20 SOL | NARRATIVE 0.20 SOL | +100%시 50% 매도\n"
                f"- 데모 크립토: {crypto_ids}\n"
                f"- 실전 밈: {live_meme_ids}\n"
                f"- 실전 크립토: {live_crypto_ids}\n"
                "예시: /set_models crypto A,B,C,D | /set_live_models crypto A,D"
            )
        if cmd == "/live_markets":
            return (
                "실전 시장별 ON/OFF\n"
                f"- 밈: {'ON' if self.settings.live_enable_meme else 'OFF'}\n"
                f"- 크립토: {'ON' if self.settings.live_enable_crypto else 'OFF'}\n"
                f"- 최소 유지 SOL(거래 제외): {float(self.settings.solana_reserve_sol):.4f} SOL"
            )
        if cmd == "/set_live_market":
            if len(chunks) < 3:
                return "사용법: /set_live_market <meme|crypto> <on|off>"
            market = str(chunks[1] or "").strip().lower()
            if market not in {"meme", "crypto"}:
                return "market은 meme 또는 crypto만 허용됩니다."
            flag = str(chunks[2] or "").strip().lower()
            enabled = flag in {"1", "true", "on", "yes"}
            if flag not in {"1", "0", "true", "false", "on", "off", "yes", "no"}:
                return "값은 on/off (또는 true/false)만 허용됩니다."
            if market == "meme":
                applied = self.set_live_markets(meme_enabled=enabled)
            else:
                applied = self.set_live_markets(crypto_enabled=enabled)
            return f"실전 시장 설정 변경 완료: 밈={'ON' if applied['meme'] else 'OFF'} | 크립토={'ON' if applied['crypto'] else 'OFF'}"
        if cmd == "/set_models":
            if len(chunks) < 3:
                return "사용법: /set_models <meme|crypto> <ids>\n예: /set_models crypto A,B,C,D"
            market = str(chunks[1] or "").strip().lower()
            if market not in {"meme", "crypto"}:
                return "market은 meme 또는 crypto만 허용됩니다."
            if market == "meme":
                return "밈은 단일 엔진 고정입니다. THEME_SNIPER 메인 / NARRATIVE 서브 구조라 별도 ID 설정을 받지 않습니다."
            allowed_ids = self._market_model_ids(market)
            parsed = self._parse_model_id_csv(",".join(chunks[2:]), fallback_all=False, allowed_ids=allowed_ids)
            if not parsed:
                return f"모델은 {','.join(allowed_ids)} 중 하나 이상 입력하세요."
            key = "MEME_AUTOTRADE_MODELS" if market == "meme" else "CRYPTO_AUTOTRADE_MODELS"
            save_runtime_overrides(self.settings, {key: ",".join(parsed)})
            self._reload_settings()
            selected = ", ".join(f"{mid}:{self._market_model_name(market, mid)}" for mid in self._autotrade_model_ids(market))
            return f"{'밈' if market == 'meme' else '크립토'} 자동매매 모델이 {selected} 로 설정되었습니다."
        if cmd == "/set_live_models":
            if len(chunks) < 3:
                return "사용법: /set_live_models <meme|crypto> <ids>\n예: /set_live_models crypto A,B,C,D"
            market = str(chunks[1] or "").strip().lower()
            if market not in {"meme", "crypto"}:
                return "market은 meme 또는 crypto만 허용됩니다."
            if market == "meme":
                return "밈 실전은 단일 엔진 ON/OFF만 사용합니다. 세부 브리지는 내부 처리라 텔레그램에서 직접 설정하지 않습니다."
            allowed_ids = self._market_model_ids(market)
            parsed = self._parse_model_id_csv(",".join(chunks[2:]), fallback_all=False, allowed_ids=allowed_ids)
            if not parsed:
                return f"모델은 {','.join(allowed_ids)} 중 하나 이상 입력하세요."
            key = "LIVE_MEME_MODELS" if market == "meme" else "LIVE_CRYPTO_MODELS"
            save_runtime_overrides(self.settings, {key: ",".join(parsed)})
            self._reload_settings()
            selected = ", ".join(f"{mid}:{self._market_model_name(market, mid)}" for mid in self._live_model_ids(market))
            return f"{'밈' if market == 'meme' else '크립토'} 실전 모델이 {selected} 로 설정되었습니다."
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
            lines = [f"자동튜닝 상태 (크립토, {self._autotune_interval_label()} 주기)"]
            for model_id in CRYPTO_MODEL_IDS:
                run = self._get_market_run(runs, "crypto", model_id)
                tune = self._read_model_runtime_tune_from_run(run or {}, model_id, now_ts)
                remain = max(0, int(tune.get("next_eval_ts") or 0) - now_ts)
                lines.append(
                    f"- {_market_name('crypto', model_id)}: next={remain // 60}m, "
                    f"thr={float(tune['threshold']):.4f}, tp_mul={float(tune['tp_mul']):.2f}, sl_mul={float(tune['sl_mul']):.2f}"
                )
                if int(tune.get("last_eval_ts") or 0) > 0:
                    note_text = str(
                        tune.get("last_eval_note_ko")
                        or self._autotune_note_ko(str(tune.get("last_eval_note") or ""))
                        or "-"
                    )
                    lines.append(
                        f"  최근평가: closed={int(tune['last_eval_closed'])}, wr={float(tune['last_eval_win_rate']):.1f}%, "
                        f"pnl={float(tune['last_eval_pnl_usd']):+.2f}, pf={float(tune['last_eval_pf']):.2f}, "
                        f"결과={note_text}, variant={str(tune.get('active_variant_id') or '-')}"
                    )
            return "\n".join(lines)
        if cmd in {"/status_meme", "/status_crypto"}:
            runs = _read_runs()
            market = "meme" if cmd == "/status_meme" else "crypto"
            if market == "meme":
                engine = self._aggregate_meme_engine_state(runs, mode_filter="paper")
                top_signal = dict(engine.get("top_signal") or {})
                seed_usd = float(engine.get("seed_usd") or 0.0)
                total_pnl_usd = float(engine.get("aggregate_total_pnl_usd") or engine.get("total_pnl_usd") or 0.0)
                total_roi_pct = (total_pnl_usd / seed_usd * 100.0) if seed_usd > 0.0 else 0.0
                lines = [
                    "데모 밈 단일 엔진 상태",
                    "- 구조: THEME_SNIPER 메인 / NARRATIVE 서브",
                    "- 진입: THEME 0.10 SOL | SNIPER 0.20 SOL | NARRATIVE 0.20 SOL",
                    "- 청산: +100%시 50% 매도, 나머지 러너 유지",
                    f"- 시드 {seed_usd:.2f} | 총수익률 {total_roi_pct:+.2f}% ({total_pnl_usd:+.2f})",
                    f"- 오픈 {int(engine.get('open_positions') or 0)} | 청산 {int(engine.get('closed_trades') or 0)} | 승률 {float(engine.get('aggregate_win_rate') or engine.get('win_rate') or 0.0):.1f}%",
                ]
                if top_signal:
                    lines.append(
                        f"- 상위 신호: {str(top_signal.get('symbol') or '-')} | "
                        f"{str(top_signal.get('strategy_id') or '-')} | "
                        f"{str(top_signal.get('grade') or '-')} {float(top_signal.get('score') or 0.0):.4f}"
                    )
                alloc_map = dict(engine.get("recent_allocations") or {})
                if alloc_map:
                    lines.append("- 최근 진입: " + " | ".join(f"{k} {v}" for k, v in alloc_map.items()))
                return "\n".join(lines)
            lines = ["데모 크립토 4모델 상태"]
            ranked_rows: list[tuple[str, dict[str, Any]]] = []
            for model_id in self._market_model_ids(market):
                run = self._get_market_run(runs, market, model_id)
                mm = self._model_metrics_market(model_id, run, market)
                ranked_rows.append((model_id, mm))
            ranked_rows.sort(key=lambda row: (float((row[1] or {}).get("total_pnl_usd") or 0.0), float((row[1] or {}).get("win_rate") or 0.0)), reverse=True)
            for rank, (model_id, mm) in enumerate(ranked_rows, start=1):
                lines.append(
                    f"#{rank} {_market_name(market, model_id)}: equity={float(mm['equity_usd']):.2f}, "
                    f"pnl={float(mm['total_pnl_usd']):+.2f}, realized={float(mm['realized_pnl_usd']):+.2f}, "
                    f"open={int(mm['open_positions'])}, win={float(mm['win_rate']):.1f}%"
                )
            return "\n".join(lines)
        if cmd in {"/pnl_meme", "/pnl_crypto"}:
            runs = _read_runs()
            market = "meme" if cmd == "/pnl_meme" else "crypto"
            if market == "meme":
                engine = self._aggregate_meme_engine_state(runs, mode_filter="paper")
                seed_usd = float(engine.get("seed_usd") or 0.0)
                total_pnl_usd = float(engine.get("aggregate_total_pnl_usd") or engine.get("total_pnl_usd") or 0.0)
                total_roi_pct = (total_pnl_usd / seed_usd * 100.0) if seed_usd > 0.0 else 0.0
                return (
                    "데모 밈 엔진 손익\n"
                    f"- 시드: {seed_usd:.2f}\n"
                    f"- 실현손익: {float(engine.get('realized_pnl_usd') or 0.0):+.2f}\n"
                    f"- 평가손익: {float(engine.get('unrealized_pnl_usd') or 0.0):+.2f}\n"
                    f"- 총수익률: {total_roi_pct:+.2f}% ({total_pnl_usd:+.2f})\n"
                    f"- 오픈: {int(engine.get('open_positions') or 0)} | 청산: {int(engine.get('closed_trades') or 0)} | 승률: {float(engine.get('aggregate_win_rate') or engine.get('win_rate') or 0.0):.1f}%"
                )
            lines = ["데모 크립토 4모델 손익"]
            ranked_rows = []
            for model_id in self._market_model_ids(market):
                mm = self._model_metrics_market(model_id, self._get_market_run(runs, market, model_id), market)
                ranked_rows.append((model_id, mm))
            ranked_rows.sort(key=lambda row: (float((row[1] or {}).get("total_pnl_usd") or 0.0), float((row[1] or {}).get("win_rate") or 0.0)), reverse=True)
            for rank, (model_id, mm) in enumerate(ranked_rows, start=1):
                lines.append(
                    f"#{rank} {_market_name(market, model_id)}: total={float(mm['total_pnl_usd']):+.2f}, "
                    f"realized={float(mm['realized_pnl_usd']):+.2f}, unrealized={float(mm['unrealized_pnl_usd']):+.2f}"
                )
            return "\n".join(lines)
        if cmd in {"/positions_meme", "/positions_crypto"}:
            runs = _read_runs()
            market = "meme" if cmd == "/positions_meme" else "crypto"
            if market == "meme":
                engine = self._aggregate_meme_engine_state(runs, mode_filter="paper")
                lines = ["데모 밈 엔진 포지션 / 최근 체결", "- 구조: THEME_SNIPER 메인 / NARRATIVE 서브"]
                pos_rows = list(engine.get("positions") or [])
                if not pos_rows:
                    lines.append("[현재 포지션]")
                    lines.append("- 없음")
                else:
                    lines.append("[현재 포지션]")
                    for pos in pos_rows[:20]:
                        lines.append(
                            f"- {str(pos.get('strategy_id') or '-')} | {pos.get('symbol') or '-'} | "
                            f"평가 ${float(pos.get('value_usd') or 0.0):.2f} | "
                            f"수익률 {float(pos.get('pnl_pct') or 0.0):+.2f}% ({float(pos.get('pnl_usd') or 0.0):+.2f}) | "
                            f"{str(pos.get('exit_rule_text') or '-')}"
                        )
                trade_rows = list(engine.get("trades") or [])
                lines.append("")
                lines.append("[최근 체결]")
                if not trade_rows:
                    lines.append("- 없음")
                else:
                    for tr in trade_rows[:15]:
                        if tr.get("pnl_usd") is None:
                            pnl_text = "-"
                        else:
                            pnl_text = f"{float(tr.get('pnl_pct') or 0.0) * 100.0:+.2f}% ({float(tr.get('pnl_usd') or 0.0):+.2f})"
                        lines.append(
                            f"- {datetime.fromtimestamp(int(tr.get('ts') or 0), tz=timezone.utc).astimezone().strftime('%m-%d %H:%M')} | "
                            f"{str(tr.get('strategy_id') or '-')} | {str(tr.get('side') or '-')} {str(tr.get('symbol') or '-')}"
                            f" ${float(tr.get('notional_usd') or 0.0):.2f} | 수익률 {pnl_text}"
                        )
                return "\n".join(lines)
            lines = ["데모 크립토 4모델 포지션"]
            for model_id in self._market_model_ids(market):
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
                        current = float(self._crypto_current_price(pos))
                        avg = float(pos.get("avg_price_usd") or 0.0)
                        pnl_pct = 0.0 if avg <= 0 else ((current - avg) / avg) * 100.0
                        lines.append(
                            f"  - {sym}: {pnl_pct:+.4f}% | lev {float(pos.get('leverage') or 1.0):.2f}x "
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
            demo_text = self._build_telegram_periodic_report_demo()
            live_text = self._build_telegram_periodic_report_live()
            ok_demo, err_demo = self.alert_manager.send_telegram(demo_text)
            ok_live, err_live = self.alert_manager.send_telegram(live_text)
            if ok_demo and ok_live:
                return "데모/실전 요약 리포트를 각각 발송했습니다."
            errs: list[str] = []
            if not ok_demo:
                errs.append(f"demo={err_demo}")
            if not ok_live:
                errs.append(f"live={err_live}")
            return f"전송 실패: {' | '.join(errs)}"
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
            runs = _read_runs()
            def _sgn(v: float) -> str:
                return f"{float(v):+.2f}"

            def _sgn_pct(v: float) -> str:
                return f"{float(v):+.2f}%"

            def _fmt_ts(unix_ts: int) -> str:
                t = int(unix_ts or 0)
                if t <= 0:
                    return "-"
                try:
                    return datetime.fromtimestamp(t, tz=timezone.utc).astimezone().strftime("%m-%d %H:%M")
                except Exception:
                    return "-"

            with self._lock:
                wallet_assets = list(self.state.wallet_assets or [])
                bybit_assets = list(self.state.bybit_assets or [])
                bybit_positions = list(self.state.bybit_positions or [])
                live_equity = self._live_equity_usd_from_assets(wallet_assets, bybit_assets)
                perf = self._live_performance_view_locked(live_equity_usd=live_equity)
                min_wallet_asset_usd = float(self.settings.min_wallet_asset_usd or 1.0)
            live_meme_models = "단일 엔진(THEME_SNIPER 메인 / NARRATIVE 서브)"
            live_crypto_models = ", ".join(
                f"{mid}:{self._market_model_name('crypto', mid)}" for mid in self._live_model_ids("crypto")
            )
            live_meme_text = live_meme_models if bool(self.settings.live_enable_meme) else "OFF"
            live_crypto_text = (
                live_crypto_models if (bool(self.settings.live_enable_crypto) and live_crypto_models) else (
                    "ON(미설정)" if bool(self.settings.live_enable_crypto) else "OFF"
                )
            )
            wallet_asset_count = sum(1 for row in wallet_assets if float((row or {}).get("value_usd") or 0.0) >= min_wallet_asset_usd)
            managed_meme_count = 0
            for model_id in MEME_MODEL_IDS:
                managed_meme_count += len(self._build_meme_positions_view(self._get_market_run(runs, "meme", model_id), mode_filter="live"))
            lines = [
                "실전 상태",
                f"- 상태: {'실행중' if self.running else '정지'} | 모드: {str(self.settings.trade_mode or 'paper').upper()} | 실전실행: {'ON' if self.settings.enable_live_execution else 'OFF'}",
                f"- 실전 시장: 밈 {'ON' if self.settings.live_enable_meme else 'OFF'} | 크립토 {'ON' if self.settings.live_enable_crypto else 'OFF'}",
                f"- 실전 모델: 밈 {live_meme_text} | 크립토 {live_crypto_text}",
                f"- 현재 실전 평가금액: ${live_equity:.2f}",
                f"- 성과 기준자산: ${float(perf.get('live_perf_anchor_usd') or 0.0):.2f}",
                f"- 순입출금 보정: {_sgn(float(perf.get('live_net_flow_usd') or 0.0))} USD",
                f"- 보정 손익: {_sgn(float(perf.get('live_perf_pnl_usd') or 0.0))} ({_sgn_pct(float(perf.get('live_perf_roi_pct') or 0.0))})",
                f"- 기준시각: {_fmt_ts(int(perf.get('live_perf_anchor_ts') or 0))}",
            ]
            if bool(self.settings.live_enable_meme):
                lines.append(
                    f"- 팬텀 자산(USD {min_wallet_asset_usd:.0f}+): {wallet_asset_count}개 | 실전 MEME 관리포지션: {managed_meme_count}개"
                )
            if bool(self.settings.live_enable_crypto):
                lines.append(f"- 거래소 자산: {len(bybit_assets)}개 | 실전 CRYPTO 포지션: {len(bybit_positions)}개")
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
            def _sgn(v: float) -> str:
                return f"{float(v):+.2f}"

            def _sgn_pct(v: float) -> str:
                return f"{float(v):+.2f}%"

            def _fmt_qty(v: float) -> str:
                text = f"{float(v):,.6f}".rstrip("0").rstrip(".")
                return text or "0"

            with self._lock:
                wallet_assets = list(self.state.wallet_assets or [])
                bybit_assets = list(self.state.bybit_assets or [])
                live_crypto_rows = list(self.state.bybit_positions or [])
                live_equity = self._live_equity_usd_from_assets(wallet_assets, bybit_assets)
                perf = self._live_performance_view_locked(live_equity_usd=live_equity)
                min_wallet_asset_usd = float(self.settings.min_wallet_asset_usd or 1.0)
            live_meme_models = "단일 엔진(THEME_SNIPER 메인 / NARRATIVE 서브)" if bool(self.settings.live_enable_meme) else "OFF"
            live_crypto_models = (
                ", ".join(f"{mid}:{self._market_model_name('crypto', mid)}" for mid in self._live_model_ids("crypto"))
                if bool(self.settings.live_enable_crypto)
                else "OFF"
            )
            lines = [
                "실전 자산 / 포지션",
                f"- 현재 실전 평가금액: ${live_equity:.2f}",
                f"- 성과 기준자산: ${float(perf.get('live_perf_anchor_usd') or 0.0):.2f}",
                f"- 순입출금 보정: {_sgn(float(perf.get('live_net_flow_usd') or 0.0))} USD",
                f"- 보정 손익: {_sgn(float(perf.get('live_perf_pnl_usd') or 0.0))} ({_sgn_pct(float(perf.get('live_perf_roi_pct') or 0.0))})",
                f"- 실전 모델: 밈 {live_meme_models or 'ON(미설정)'} | 크립토 {live_crypto_models or 'ON(미설정)'}",
            ]

            if bool(self.settings.live_enable_meme):
                wallet_rows = [
                    row for row in wallet_assets
                    if float((row or {}).get("value_usd") or 0.0) >= min_wallet_asset_usd
                ]
                wallet_rows.sort(key=lambda row: float((row or {}).get("value_usd") or 0.0), reverse=True)
                lines.append("")
                lines.append(f"[LIVE 팬텀 자산 USD {min_wallet_asset_usd:.0f}+]")
                if wallet_rows:
                    for row in wallet_rows[:12]:
                        lines.append(
                            f"- {str(row.get('symbol') or '-').upper()}: ${float(row.get('value_usd') or 0.0):.2f} | "
                            f"수량 {_fmt_qty(float(row.get('qty') or 0.0))} | 현재가 ${float(row.get('price_usd') or 0.0):.8f}"
                        )
                else:
                    lines.append("- 없음")

                managed_meme_rows: list[dict[str, Any]] = []
                for model_id in MEME_MODEL_IDS:
                    meme_run = self._get_market_run(runs, "meme", model_id)
                    for row in self._build_meme_positions_view(meme_run, mode_filter="live"):
                        item = dict(row or {})
                        item["model_name"] = self._meme_strategy_id_for_model(model_id)
                        managed_meme_rows.append(item)
                managed_meme_rows.sort(key=lambda row: float((row or {}).get("value_usd") or 0.0), reverse=True)
                lines.append("")
                lines.append("[LIVE MEME 관리포지션]")
                if managed_meme_rows:
                    for row in managed_meme_rows[:15]:
                        lines.append(
                            f"- {row.get('model_name')}: {row.get('symbol') or '-'} | 평가 ${float(row.get('value_usd') or 0.0):.2f} | "
                            f"수익률 {_sgn_pct(float(row.get('pnl_pct') or 0.0))} ({_sgn(float(row.get('pnl_usd') or 0.0))}) | "
                            f"{str(row.get('exit_rule_text') or '-')}"
                        )
                else:
                    lines.append("- 없음")
            else:
                lines.append("")
                lines.append("[LIVE 밈]")
                lines.append("- OFF")

            if bool(self.settings.live_enable_crypto):
                lines.append("")
                lines.append("[LIVE CRYPTO 포지션]")
                if live_crypto_rows:
                    for row in live_crypto_rows[:15]:
                        symbol = str(row.get("symbol") or "-")
                        side = str(row.get("side") or "-")
                        upnl = float(
                            row.get("unrealisedPnl")
                            or row.get("unrealised_pnl")
                            or row.get("unrealizedPnl")
                            or row.get("unrealized_pnl")
                            or 0.0
                        )
                        lines.append(f"- {symbol} ({side}): UPNL {_sgn(upnl)}")
                else:
                    lines.append("- 없음")
            else:
                lines.append("")
                lines.append("[LIVE 크립토]")
                lines.append("- OFF")

            lines.append("")
            lines.append("데모 상세는 /positions_meme, /positions_crypto 를 사용하세요.")
            return "\n".join(lines)
        if cmd == "/pnl":
            runs = _read_runs()
            lines = ["데모 손익 요약"]
            meme_engine = self._aggregate_meme_engine_state(runs, mode_filter="paper")
            lines.append("[밈 엔진]")
            lines.append(
                f"- 실현 {float(meme_engine.get('realized_pnl_usd') or 0.0):+.2f} | "
                f"평가 {float(meme_engine.get('unrealized_pnl_usd') or 0.0):+.2f} | "
                f"총손익 {float(meme_engine.get('total_pnl_usd') or 0.0):+.2f}"
            )
            lines.append(
                f"- 오픈 {int(meme_engine.get('open_positions') or 0)} | "
                f"청산 {int(meme_engine.get('closed_trades') or 0)} | "
                f"승률 {float(meme_engine.get('win_rate') or 0.0):.1f}%"
            )
            lines.append("")
            lines.append("[크립토 4모델 순위]")
            ranked_crypto = []
            for model_id in CRYPTO_MODEL_IDS:
                cm = self._model_metrics_market(model_id, self._get_market_run(runs, "crypto", model_id), "crypto")
                ranked_crypto.append((model_id, cm))
            ranked_crypto.sort(key=lambda row: (float((row[1] or {}).get("total_pnl_usd") or 0.0), float((row[1] or {}).get("win_rate") or 0.0)), reverse=True)
            for rank, (model_id, cm) in enumerate(ranked_crypto, start=1):
                lines.append(
                    f"#{rank} {_market_name('crypto', model_id)}: total={float(cm['total_pnl_usd']):+.2f}, "
                    f"realized={float(cm['realized_pnl_usd']):+.2f}, unrealized={float(cm['unrealized_pnl_usd']):+.2f}"
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

    def _build_meme_positions_view(self, run: dict[str, Any], mode_filter: str = "paper") -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for pos in list((run.get("meme_positions") or {}).values()):
            is_live_pos = str((pos or {}).get("mode") or "").strip().lower() == "live"
            if mode_filter == "live" and not is_live_pos:
                continue
            if mode_filter != "live" and is_live_pos:
                continue
            token_address = str(pos.get("token_address") or "")
            avg = float(pos.get("avg_price_usd") or 0.0)
            current = self._resolve_price_cached(token_address, fallback=avg)
            qty = float(pos.get("qty") or 0.0)
            pnl_usd = (current - avg) * qty if current > 0 else 0.0
            pnl_pct = 0.0 if avg <= 0 else ((current - avg) / avg) * 100.0
            entry_features = dict(pos.get("entry_features") or {})
            derived_strategy_id = self._meme_strategy_id_from_signal_context(
                features=entry_features,
                reason=str(pos.get("reason") or ""),
                current_strategy_id=str(
                    pos.get("engine_strategy_id")
                    or self._meme_strategy_id_for_model(str(pos.get("model_id") or "A"))
                ),
            )
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
                    "partial_tp_pct": float(pos.get("partial_tp_pct") or self.settings.meme_partial_take_profit_pct),
                    "partial_tp_sell_ratio": float(pos.get("partial_tp_sell_ratio") or self.settings.meme_partial_take_profit_sell_ratio),
                    "partial_tp_done": bool(pos.get("partial_tp_done")),
                    "engine_strategy_id": str(derived_strategy_id),
                    "exit_rule_text": self._meme_exit_rule_text(pos),
                    "hold_until_ts": int(pos.get("hold_until_ts") or 0),
                    "opened_at": int(pos.get("opened_at") or 0),
                    "reason": str(pos.get("reason") or ""),
                }
            )
        out.sort(key=lambda r: float(r.get("value_usd") or 0.0), reverse=True)
        return out

    def _enrich_meme_score_row(self, row: dict[str, Any], model_id_hint: str = "") -> dict[str, Any]:
        item = dict(row or {})
        model_id = str(item.get("model_id") or model_id_hint or "C").upper().strip() or "C"
        if str(item.get("score_low_reason") or "").strip() and str(item.get("score_hold_hint") or "").strip():
            return item
        features = dict(item.get("features") or {})
        if features:
            diagnostics = self._meme_score_diagnostics(
                model_id,
                features,
                float(item.get("score") or 0.0),
                str(item.get("grade") or "G"),
                strategy=str(item.get("strategy") or ""),
            )
            item["score_low_reason"] = str(diagnostics.get("low_reason") or "")
            item["score_hold_hint"] = str(diagnostics.get("hold_hint") or "")
            item["score_hold_target_grade"] = str(diagnostics.get("hold_target_grade") or "")
            item["score_hold_target_score"] = float(diagnostics.get("hold_target_score") or 0.0)
            item["score_hold_gap"] = float(diagnostics.get("hold_gap") or 0.0)
            return item
        hold_grade = self._meme_hold_target_grade(model_id, str(item.get("strategy") or ""))
        hold_score = float(self._meme_grade_min_score(hold_grade))
        score_now = float(item.get("score") or 0.0)
        if not str(item.get("score_low_reason") or "").strip():
            item["score_low_reason"] = str(item.get("reason") or "점수 세부 원인 데이터 없음")
        if not str(item.get("score_hold_hint") or "").strip():
            gap = max(0.0, hold_score - score_now)
            item["score_hold_hint"] = f"홀딩권장 {hold_grade}({hold_score:.2f})까지 +{gap:.2f}"
        item["score_hold_target_grade"] = str(item.get("score_hold_target_grade") or hold_grade)
        item["score_hold_target_score"] = float(item.get("score_hold_target_score") or hold_score)
        item["score_hold_gap"] = float(item.get("score_hold_gap") or max(0.0, hold_score - score_now))
        return item

    def _build_crypto_positions_view(self, run: dict[str, Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if not self.settings.demo_enable_macro:
            return out
        for pos in list((run.get("bybit_positions") or {}).values()):
            symbol = str(pos.get("symbol") or "")
            current = float(self._crypto_current_price(pos))
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
        interval_label = self._autotune_interval_label()
        return {
            "A": {
                "name": CRYPTO_MODEL_SPECS["A"]["name"],
                "meme": "도그리 밈 선별모델: Solscan 지갑패턴(스마트월렛/분산/홀더리스크) 품질게이트를 통과한 밈만 진입하는 신뢰형 전략입니다.",
                "crypto": "크립토 레인지 리버전 플래너: 레인지 하단 재진입 가격과 손절/목표가를 함께 계산하는 계획형 모델입니다.",
                "strengths_meme": "강점: 지갑패턴이 불량한 종목을 초기에 걸러내 손절 연속을 줄이는 데 유리합니다.",
                "strengths_crypto": "강점: 무리한 추격을 줄이고 레인지 지지 근처에서만 계획적으로 진입합니다.",
                "autotune": f"자동튜닝({interval_label}): 성과 악화 시 진입 임계값을 높이고 손절 버퍼를 넓히는 방어 튜닝을 적용합니다.",
            },
            "B": {
                "name": CRYPTO_MODEL_SPECS["B"]["name"],
                "meme": "밈 장기홀딩 예측모델: Solscan 지갑패턴 검증 통과 + 장기 보유(기본 14일, 연장 가능) 중심 전략입니다.",
                "crypto": "크립토 리클레임 플래너: 지지 회복 후 재안착 구간의 entry/SL/TP를 예측하는 모델입니다.",
                "strengths_meme": "강점: 초기 손절 난사를 줄이고, 지갑 리스크 재점검 기반으로 홀딩 지속 여부를 판단합니다.",
                "strengths_crypto": "강점: 눌림 뒤 회복하는 구간을 비교적 안정적으로 포착할 수 있습니다.",
                "autotune": f"자동튜닝({interval_label}): 성과 악화 시 entry zone을 보수화하고 손절/목표가 배수를 재조정합니다.",
            },
            "C": {
                "name": CRYPTO_MODEL_SPECS["C"]["name"],
                "meme": "밈 단타 모멘텀모델: 단타 전용으로 빠른 체결흐름/모멘텀을 반영해 짧게 회전하는 전략입니다.",
                "crypto": "크립토 압축 돌파 플래너: 변동성 수축 후 확장 구간의 돌파 진입 가격과 목표가를 생성합니다.",
                "strengths_meme": "강점: 짧은 시간대의 회전 매매 대응이 빠릅니다.",
                "strengths_crypto": "강점: 손익비가 큰 돌파 setup만 선별해 고배율 구간을 명확하게 다룹니다.",
                "autotune": f"자동튜닝({interval_label}): 성과 악화 시 돌파 기준을 높이고 목표가 배수를 줄이는 리스크오프 튜닝을 적용합니다.",
            },
            "D": {
                "name": CRYPTO_MODEL_SPECS["D"]["name"],
                "meme": "밈 엔진 전용 모델이 아니므로 사용하지 않습니다.",
                "crypto": "크립토 리셋 바운스 플래너: 급락 뒤 안정화된 반등 구간에서 entry/SL/TP를 계획합니다.",
                "strengths_meme": "-",
                "strengths_crypto": "강점: 과매도 리셋 구간만 골라 반등 setup을 분리해 볼 수 있습니다.",
                "autotune": f"자동튜닝({interval_label}): 성과 악화 시 반등 조건과 손절 버퍼를 더 보수적으로 조정합니다.",
            },
        }

    def _model_profile_snapshot(self) -> dict[str, dict[str, Any]]:
        rank_to_grade = {0: "S", 1: "A", 2: "B", 3: "C", 4: "D", 5: "E", 6: "F", 7: "G"}
        out: dict[str, dict[str, Any]] = {}
        for model_id in self._all_model_ids():
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
                    "rank_min": int(gate_prof.get("rank_min") or self._crypto_rank_band_for_model(model_id)[0]),
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
        now_wall = float(time.time())
        now_ts = int(now_wall)
        if str(self.settings.trade_mode or "").lower() == "live":
            self._sync_wallet(now_ts, force=False)
            self._sync_bybit(now_ts, force=False)
        else:
            with self._lock:
                # bybit_error/memecoin_error are live sync artifacts. In paper mode,
                # showing an old live-sync failure is misleading, so clear them.
                self.state.bybit_error = ""
                self.state.memecoin_error = ""
        with self._lock:
            cache_ready = bool(self._dashboard_cache)
            if cache_ready:
                cache_age = now_wall - float(self._dashboard_cache_ts or 0.0)
                cache_cycle_ok = int(self.state.last_cycle_ts or 0) == int(self._dashboard_cache_cycle_ts or 0)
                cache_wallet_ok = int(self.state.last_wallet_sync_ts or 0) == int(self._dashboard_cache_wallet_ts or 0)
                cache_bybit_ok = int(self.state.last_bybit_sync_ts or 0) == int(self._dashboard_cache_bybit_ts or 0)
                if (
                    cache_age <= float(self._dashboard_cache_ttl_seconds)
                    and cache_cycle_ok
                    and cache_wallet_ok
                    and cache_bybit_ok
                ):
                    return self._dashboard_cache
        perf = self._performance()
        with self._lock:
            settings_public = settings_to_public_dict(self.settings)
            alerts = list(self.state.alerts[-80:])
            trend_events = list(self.state.trend_events[-1500:])
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
            live_seed_saved = float(self.state.live_seed_usd or 0.0)
            live_seed_set_ts = int(self.state.live_seed_set_ts or 0)
            if float(getattr(self.state, "live_perf_anchor_usd", 0.0) or 0.0) <= 0.0:
                init_equity = self._live_equity_usd_from_assets(self.state.wallet_assets, self.state.bybit_assets)
                self.state.live_perf_anchor_usd = float(init_equity)
                self.state.live_perf_anchor_ts = int(now_ts)
            live_perf_anchor_saved = float(getattr(self.state, "live_perf_anchor_usd", 0.0) or 0.0)
            live_perf_anchor_ts_saved = int(getattr(self.state, "live_perf_anchor_ts", 0) or 0)
            live_net_flow_saved = float(getattr(self.state, "live_net_flow_usd", 0.0) or 0.0)
            trend_source_status = dict(self._trend_source_status or {})
            new_meme_feed = list(self._new_meme_feed or [])
            meme_symbol_caps = dict(self._meme_symbol_market_caps or {})
            meme_symbol_ages = dict(self._meme_symbol_age_minutes or {})
            macro_meta = dict(self._macro_meta or {})

        run_a_meme = self._get_market_run(runs, "meme", "A")
        run_a_crypto = self._get_market_run(runs, "crypto", "A")
        run_a = self._compose_model_run_from_market(runs, "A")
        demo_trades = list(run_a.get("trades") or [])[-120:]
        meme_signals = list(run_a_meme.get("latest_signals") or [])[-40:]
        crypto_signals = list(run_a_crypto.get("latest_crypto_signals") or [])[-40:]
        model_a_metrics = self._model_metrics("A", run_a)

        meme_trades = list(run_a_meme.get("trades") or [])[-120:]
        crypto_trades = list(run_a_crypto.get("trades") or [])[-120:]

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

        meme_positions = self._build_meme_positions_view(run_a_meme, mode_filter="paper")
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
        trend_daily_hits: dict[str, int] = {}
        for offset in range(13, -1, -1):
            day_ts = now_ts - (offset * 86400)
            day = datetime.fromtimestamp(day_ts, tz=timezone.utc).date().isoformat()
            trend_daily_hits[day] = 0
        for ev in trend_events:
            ts = int(ev.get("ts") or 0)
            if ts <= 0:
                continue
            sym = str(ev.get("symbol") or "").upper()
            if not sym:
                continue
            if not self._is_memecoin_token(sym, sym, ""):
                continue
            age_minutes = float(meme_symbol_ages.get(sym) or 999999.0)
            if not self._meme_age_allowed(age_minutes):
                continue
            rank = int(self._meme_market_rank(sym, macro_meta))
            if 0 < rank <= MEME_EXCLUDE_TOP_RANK_MAX:
                continue
            cap_usd = float(meme_symbol_caps.get(sym) or 0.0)
            if cap_usd <= 0.0 or cap_usd > MEME_SMALLCAP_MAX_USD:
                continue
            age = now_ts - ts
            if age <= 21600:
                trend_rank[sym] = trend_rank.get(sym, 0) + 1
            if age <= (14 * 86400):
                day = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
                if day in trend_daily_hits:
                    trend_daily_hits[day] = trend_daily_hits.get(day, 0) + 1
        # Fallback: if strict event filter yields no rows, use new meme feed trend_hits.
        if not trend_rank:
            for row in list(new_meme_feed or [])[:120]:
                sym = str((row or {}).get("symbol") or "").upper().strip()
                if not sym:
                    continue
                age_minutes = float((row or {}).get("age_minutes") or 999999.0)
                if not self._meme_age_allowed(age_minutes):
                    continue
                rank = int((row or {}).get("market_cap_rank") or self._meme_market_rank(sym, macro_meta))
                if 0 < rank <= MEME_EXCLUDE_TOP_RANK_MAX:
                    continue
                cap_usd = float((row or {}).get("market_cap_usd") or 0.0)
                if cap_usd <= 0.0 or cap_usd > MEME_SMALLCAP_MAX_USD:
                    continue
                hits = int((row or {}).get("trend_hits") or 0)
                if hits <= 0:
                    continue
                trend_rank[sym] = max(int(trend_rank.get(sym) or 0), int(hits))
        trend_top = sorted(
            [
                {
                    "symbol": k,
                    "hits": v,
                    "market_cap_usd": float(meme_symbol_caps.get(k) or 0.0),
                    "market_cap_rank": int(self._meme_market_rank(k, macro_meta)),
                    "age_minutes": float(meme_symbol_ages.get(k) or 0.0),
                }
                for k, v in trend_rank.items()
            ],
            key=lambda row: int(row["hits"]),
            reverse=True,
        )[:30]
        trend_daily = [{"date": day, "hits": int(hits)} for day, hits in trend_daily_hits.items()]
        bucket_now = int(now_ts // 1800 * 1800)
        trend_30m_hits: dict[int, int] = {}
        trend_30m_symbol_hits: dict[int, dict[str, int]] = {}
        for offset in range(47, -1, -1):
            b_ts = int(bucket_now - (offset * 1800))
            trend_30m_hits[b_ts] = 0
            trend_30m_symbol_hits[b_ts] = {}
        for ev in trend_events:
            ts = int(ev.get("ts") or 0)
            if ts <= 0:
                continue
            sym = str(ev.get("symbol") or "").upper()
            if not sym:
                continue
            if not self._is_memecoin_token(sym, sym, ""):
                continue
            age_minutes = float(meme_symbol_ages.get(sym) or 999999.0)
            if not self._meme_age_allowed(age_minutes):
                continue
            rank = int(self._meme_market_rank(sym, macro_meta))
            if 0 < rank <= MEME_EXCLUDE_TOP_RANK_MAX:
                continue
            cap_usd = float(meme_symbol_caps.get(sym) or 0.0)
            if cap_usd <= 0.0 or cap_usd > MEME_SMALLCAP_MAX_USD:
                continue
            b_ts = int(ts // 1800 * 1800)
            if b_ts not in trend_30m_hits:
                continue
            trend_30m_hits[b_ts] = int(trend_30m_hits.get(b_ts, 0)) + 1
            sym_table = trend_30m_symbol_hits.get(b_ts) or {}
            sym_table[sym] = int(sym_table.get(sym, 0)) + 1
            trend_30m_symbol_hits[b_ts] = sym_table
        trend_30m: list[dict[str, Any]] = []
        for b_ts in sorted(trend_30m_hits.keys()):
            sym_table = dict(trend_30m_symbol_hits.get(b_ts) or {})
            top_symbol = ""
            top_hits = 0
            if sym_table:
                top_symbol, top_hits = max(sym_table.items(), key=lambda it: int(it[1]))
            trend_30m.append(
                {
                    "ts": int(b_ts),
                    "label": datetime.fromtimestamp(b_ts, tz=timezone.utc).strftime("%m-%d %H:%M"),
                    "hits": int(trend_30m_hits.get(b_ts, 0)),
                    "top_symbol": str(top_symbol),
                    "top_hits": int(top_hits),
                }
            )

        model_runs = [self._model_metrics(mid, self._compose_model_run_from_market(runs, mid)) for mid in self._all_model_ids()]
        meme_model_runs = [
            self._model_metrics_market(mid, self._get_market_run(runs, "meme", mid), "meme", mode_filter="paper")
            for mid in MEME_MODEL_IDS
        ]
        crypto_model_runs = [
            self._model_metrics_market(mid, self._get_market_run(runs, "crypto", mid), "crypto") for mid in CRYPTO_MODEL_IDS
        ]
        meme_model_rankings = sorted(
            [
                {
                    "model_id": str(r.get("model_id") or ""),
                    "model_name": str(r.get("model_name") or ""),
                    "seed_usd": float(r.get("seed_usd") or 0.0),
                    "total_pnl_usd": float(r.get("total_pnl_usd") or 0.0),
                    "realized_pnl_usd": float(r.get("realized_pnl_usd") or 0.0),
                    "win_rate": float(r.get("win_rate") or 0.0),
                    "open_positions": int(r.get("open_positions") or 0),
                    "equity_usd": float(r.get("equity_usd") or 0.0),
                }
                for r in meme_model_runs
            ],
            key=lambda r: (float(r.get("total_pnl_usd") or 0.0), float(r.get("win_rate") or 0.0)),
            reverse=True,
        )
        for idx, row in enumerate(meme_model_rankings, start=1):
            row["rank"] = int(idx)
        crypto_model_rankings = sorted(
            [
                {
                    "model_id": str(r.get("model_id") or ""),
                    "model_name": str(r.get("model_name") or ""),
                    "seed_usd": float(r.get("seed_usd") or 0.0),
                    "total_pnl_usd": float(r.get("total_pnl_usd") or 0.0),
                    "realized_pnl_usd": float(r.get("realized_pnl_usd") or 0.0),
                    "win_rate": float(r.get("win_rate") or 0.0),
                    "open_positions": int(r.get("open_positions") or 0),
                    "equity_usd": float(r.get("equity_usd") or 0.0),
                }
                for r in crypto_model_runs
            ],
            key=lambda r: (float(r.get("total_pnl_usd") or 0.0), float(r.get("win_rate") or 0.0)),
            reverse=True,
        )
        for idx, row in enumerate(crypto_model_rankings, start=1):
            row["rank"] = int(idx)
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
            for mid in self._all_model_ids()
        ]
        meme_model_recommendations = [
            {"id": mid, "name": self._market_model_name("meme", mid), "description": self._market_model_spec("meme", mid)["description"]}
            for mid in MEME_MODEL_IDS
        ]
        crypto_model_recommendations = [
            {
                "id": mid,
                "name": self._market_model_name("crypto", mid),
                "description": self._market_model_spec("crypto", mid)["description"],
            }
            for mid in CRYPTO_MODEL_IDS
        ]
        model_methods = self._model_method_explanations()
        model_profiles = self._model_profile_snapshot()
        meme_strategy_registry = self._meme_strategy_registry()
        for row in meme_strategy_registry:
            try:
                row["entry_sol"] = float(self._meme_strategy_entry_sol(str(row.get("id") or "")))
            except Exception:
                row["entry_sol"] = 0.0
        crypto_strategy_registry = [
            {"id": sid, "name": spec["name"], "description": spec["description"]}
            for sid, spec in CRYPTO_STRATEGY_SPECS.items()
        ]
        meme_discovery_state = self.meme_discovery.dashboard_payload()
        meme_strategy_bridge = {
            strategy_id: str((MEME_STRATEGY_SPECS.get(strategy_id) or {}).get("bridge_model_id") or "")
            for strategy_id in MEME_STRATEGY_IDS
        }
        model_views: dict[str, Any] = {}
        for model_id in self._all_model_ids():
            meme_run = self._get_market_run(runs, "meme", model_id)
            crypto_run = self._get_market_run(runs, "crypto", model_id)
            model_meme_trades = [
                dict(tr)
                for tr in list(meme_run.get("trades") or [])
                if str((tr or {}).get("source") or "").strip().lower() == "memecoin"
                and not self._is_live_trade_row(tr)
            ][-60:]
            model_crypto_trades = [
                dict(tr)
                for tr in list(crypto_run.get("trades") or [])
                if str((tr or {}).get("source") or "").strip().lower() == "crypto_demo"
                and not self._is_live_trade_row(tr)
            ][-60:]
            model_meme_positions = self._build_meme_positions_view(meme_run, mode_filter="paper")
            model_crypto_positions = self._build_crypto_positions_view(crypto_run)
            model_meme_daily = [row for row in meme_daily_pnl if str(row.get("model_id") or "") == model_id]
            model_crypto_daily = [row for row in crypto_daily_pnl if str(row.get("model_id") or "") == model_id]
            model_meme_signals: list[dict[str, Any]] = []
            for row in list(meme_run.get("latest_signals") or [])[-30:]:
                item = self._enrich_meme_score_row(dict(row or {}), model_id)
                strategy_id = self._meme_strategy_id_from_signal_context(
                    features=dict(item.get("features") or {}),
                    reason=str(item.get("reason") or ""),
                    current_strategy_id=str(item.get("strategy_id") or self._meme_strategy_id_for_model(model_id)),
                )
                item["strategy_id"] = str(strategy_id)
                item["strategy_name"] = str(self._meme_strategy_name(strategy_id))
                model_meme_signals.append(item)
            model_views[model_id] = {
                "model_id": model_id,
                "model_name": MODEL_SPECS.get(model_id, {}).get("name", model_id),
                "market_names": {
                    "meme": self._market_model_name("meme", model_id),
                    "crypto": self._market_model_name("crypto", model_id),
                },
                "meme": {
                    "model_name": self._market_model_name("meme", model_id),
                    "summary": self._model_metrics_market(model_id, meme_run, "meme", mode_filter="paper"),
                    "signals": model_meme_signals,
                    "positions": model_meme_positions,
                    "trades": model_meme_trades,
                    "daily_pnl": model_meme_daily,
                },
                "crypto": {
                    "model_name": self._market_model_name("crypto", model_id),
                    "summary": self._model_metrics_market(model_id, crypto_run, "crypto"),
                    "signals": list(crypto_run.get("latest_crypto_signals") or [])[-30:],
                    "positions": model_crypto_positions,
                    "trades": model_crypto_trades,
                    "daily_pnl": model_crypto_daily,
                },
            }
        meme_signal_map: dict[str, dict[str, Any]] = {}
        agg_meme_positions: list[dict[str, Any]] = []
        agg_meme_trades: list[dict[str, Any]] = []
        agg_meme_seed = 0.0
        agg_meme_equity = 0.0
        agg_meme_cash = 0.0
        agg_meme_position_value = 0.0
        agg_meme_realized = 0.0
        agg_meme_unrealized = 0.0
        agg_meme_closed = 0.0
        agg_meme_wins = 0.0
        for model_id in MEME_MODEL_IDS:
            detail = ((model_views.get(model_id) or {}).get("meme") or {})
            model_name = str(detail.get("model_name") or self._market_model_name("meme", model_id))
            summary = dict(detail.get("summary") or {})
            agg_meme_seed += float(summary.get("seed_usd") or 0.0)
            agg_meme_equity += float(summary.get("equity_usd") or 0.0)
            agg_meme_cash += float(summary.get("cash_usd") or 0.0)
            agg_meme_position_value += float(summary.get("position_value_usd") or 0.0)
            agg_meme_realized += float(summary.get("realized_pnl_usd") or 0.0)
            agg_meme_unrealized += float(summary.get("unrealized_pnl_usd") or 0.0)
            agg_meme_closed += float(summary.get("closed_trades") or 0.0)
            agg_meme_wins += float(summary.get("wins") or 0.0)
            for row in list(detail.get("positions") or []):
                item = dict(row or {})
                item["model_id"] = str(model_id)
                item["model_name"] = model_name
                agg_meme_positions.append(item)
            for row in list(detail.get("trades") or []):
                item = dict(row or {})
                item["model_id"] = str(model_id)
                item["model_name"] = model_name
                agg_meme_trades.append(item)
            for row in list(detail.get("signals") or []):
                item = dict(row or {})
                item["model_id"] = str(model_id)
                item["model_name"] = model_name
                key = str(item.get("token_address") or item.get("symbol") or "").upper().strip()
                if not key:
                    continue
                prev = dict(meme_signal_map.get(key) or {})
                if not prev or float(item.get("score") or 0.0) > float(prev.get("score") or 0.0):
                    meme_signal_map[key] = item
        agg_meme_positions.sort(key=lambda row: float(row.get("value_usd") or 0.0), reverse=True)
        agg_meme_trades.sort(key=lambda row: int(row.get("ts") or 0), reverse=True)
        meme_signals = sorted(meme_signal_map.values(), key=lambda row: float(row.get("score") or 0.0), reverse=True)[:120]
        meme_positions = agg_meme_positions[:240]
        meme_trades = agg_meme_trades[:240]
        agg_meme_total_pnl = float(agg_meme_realized + agg_meme_unrealized)
        agg_meme_win_rate = float((agg_meme_wins / max(agg_meme_closed, 1e-9)) * 100.0) if agg_meme_closed > 0 else 0.0
        meme_summary = {
            "seed_usd": float(agg_meme_seed),
            "cash_usd": float(agg_meme_cash),
            "position_value_usd": float(agg_meme_position_value),
            "equity_usd": float(agg_meme_equity),
            "total_pnl_usd": float(agg_meme_total_pnl),
            "total_roi_pct": float((agg_meme_total_pnl / max(agg_meme_seed, 1e-9)) * 100.0) if agg_meme_seed > 0.0 else 0.0,
            "unrealized_pnl_usd": float(agg_meme_unrealized),
            "realized_pnl_usd": float(agg_meme_realized),
            "closed_trades": int(agg_meme_closed),
            "wins": float(agg_meme_wins),
            "win_rate": float(agg_meme_win_rate),
            "open_positions": len(meme_positions),
        }
        model_autotune: dict[str, Any] = {}
        for model_id in CRYPTO_MODEL_IDS:
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
        google_runtime: dict[str, Any] = {}
        try:
            if hasattr(self.trend, "google_runtime_status"):
                snap = self.trend.google_runtime_status()
                if isinstance(snap, dict):
                    google_runtime = dict(snap)
        except Exception:
            google_runtime = {}
        runtime_feedback_recent: list[dict[str, Any]] = []
        trend_brief_meme: list[dict[str, Any]] = []
        trend_brief_crypto: list[dict[str, Any]] = []
        trend_db_stats: dict[str, Any] = {}
        meme_trend_30m_db: list[dict[str, Any]] = []
        crypto_trend_30m_db: list[dict[str, Any]] = []
        meme_trend_rank_db: list[dict[str, Any]] = []
        crypto_trend_rank_db: list[dict[str, Any]] = []
        meme_trend_share_24h: list[dict[str, Any]] = []
        crypto_trend_share_24h: list[dict[str, Any]] = []
        meme_trend_hourly_db: list[dict[str, Any]] = []
        meme_trend_daily_db: list[dict[str, Any]] = []
        meme_trend_weekly_db: list[dict[str, Any]] = []
        crypto_trend_hourly_db: list[dict[str, Any]] = []
        crypto_trend_daily_db: list[dict[str, Any]] = []
        crypto_trend_weekly_db: list[dict[str, Any]] = []
        model_tune_history: list[dict[str, Any]] = []
        model_tune_variant_rank: list[dict[str, Any]] = []

        def _filter_meme_brief(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            out: list[dict[str, Any]] = []
            for row in list(rows or []):
                meta = dict((row or {}).get("meta") or {})
                if not self._meme_trend_brief_entries(meta):
                    continue
                out.append(dict(row or {}))
            return out

        feedback_age = time.time() - float(self._feedback_cache_ts or 0.0)
        if self._feedback_cache and feedback_age <= float(self._feedback_cache_ttl_seconds):
            runtime_feedback_recent = list(self._feedback_cache.get("runtime_feedback_recent") or [])
            trend_brief_meme = _filter_meme_brief(list(self._feedback_cache.get("trend_brief_meme") or []))
            trend_brief_crypto = list(self._feedback_cache.get("trend_brief_crypto") or [])
            trend_db_stats = dict(self._feedback_cache.get("trend_db_stats") or {})
            meme_trend_30m_db = list(self._feedback_cache.get("meme_trend_30m_db") or [])
            crypto_trend_30m_db = list(self._feedback_cache.get("crypto_trend_30m_db") or [])
            meme_trend_rank_db = list(self._feedback_cache.get("meme_trend_rank_db") or [])
            crypto_trend_rank_db = list(self._feedback_cache.get("crypto_trend_rank_db") or [])
            meme_trend_share_24h = list(self._feedback_cache.get("meme_trend_share_24h") or [])
            crypto_trend_share_24h = list(self._feedback_cache.get("crypto_trend_share_24h") or [])
            meme_trend_hourly_db = list(self._feedback_cache.get("meme_trend_hourly_db") or [])
            meme_trend_daily_db = list(self._feedback_cache.get("meme_trend_daily_db") or [])
            meme_trend_weekly_db = list(self._feedback_cache.get("meme_trend_weekly_db") or [])
            crypto_trend_hourly_db = list(self._feedback_cache.get("crypto_trend_hourly_db") or [])
            crypto_trend_daily_db = list(self._feedback_cache.get("crypto_trend_daily_db") or [])
            crypto_trend_weekly_db = list(self._feedback_cache.get("crypto_trend_weekly_db") or [])
            model_tune_history = list(self._feedback_cache.get("model_tune_history") or [])
            model_tune_variant_rank = list(self._feedback_cache.get("model_tune_variant_rank") or [])
        else:
            try:
                runtime_feedback_recent = [
                    row
                    for row in list(self.runtime_feedback.recent_events(limit=120))
                    if str((row or {}).get("source") or "") not in {"core:telegram_poll_lock", "core:telegram_poll"}
                ]
                trend_brief_meme = _filter_meme_brief(
                    list(self.runtime_feedback.recent_events(limit=240, source="trend_brief_meme"))
                )
                trend_brief_crypto = list(self.runtime_feedback.recent_events(limit=240, source="trend_brief_crypto"))
                trend_db_stats = dict(self.runtime_feedback.trend_stats() or {})
                meme_trend_30m_db = list(
                    self.runtime_feedback.trend_bucket_series("meme", lookback_seconds=60 * 60 * 24, bucket_seconds=1800)
                )
                crypto_trend_30m_db = list(
                    self.runtime_feedback.trend_bucket_series("crypto", lookback_seconds=60 * 60 * 24, bucket_seconds=1800)
                )
                meme_trend_rank_db = list(
                    self.runtime_feedback.trend_rank("meme", lookback_seconds=60 * 60 * 24, limit=120)
                )
                crypto_trend_rank_db = list(
                    self.runtime_feedback.trend_rank("crypto", lookback_seconds=60 * 60 * 24, limit=120)
                )
                meme_trend_share_24h = list(
                    self.runtime_feedback.trend_share_distribution(
                        "meme",
                        lookback_seconds=60 * 60 * 24,
                        top_n=8,
                        min_share_pct=2.0,
                        exclude_symbols=list(MEME_TREND_EXCLUDED_SYMBOLS),
                    )
                )
                crypto_trend_share_24h = list(
                    self.runtime_feedback.trend_share_distribution(
                        "crypto",
                        lookback_seconds=60 * 60 * 24,
                        top_n=8,
                        min_share_pct=2.0,
                    )
                )
                meme_trend_hourly_db = list(
                    self.runtime_feedback.trend_period_summary(
                        "meme",
                        bucket_seconds=60 * 60,
                        lookback_seconds=60 * 60 * 24,
                        top_n=5,
                        min_share_pct=2.0,
                        exclude_symbols=list(MEME_TREND_EXCLUDED_SYMBOLS),
                    )
                )
                meme_trend_daily_db = list(
                    self.runtime_feedback.trend_period_summary(
                        "meme",
                        bucket_seconds=60 * 60 * 24,
                        lookback_seconds=60 * 60 * 24 * 14,
                        top_n=5,
                        min_share_pct=2.0,
                        exclude_symbols=list(MEME_TREND_EXCLUDED_SYMBOLS),
                    )
                )
                meme_trend_weekly_db = list(
                    self.runtime_feedback.trend_period_summary(
                        "meme",
                        bucket_seconds=60 * 60 * 24 * 7,
                        lookback_seconds=60 * 60 * 24 * 56,
                        top_n=5,
                        min_share_pct=2.0,
                        exclude_symbols=list(MEME_TREND_EXCLUDED_SYMBOLS),
                    )
                )
                crypto_trend_hourly_db = list(
                    self.runtime_feedback.trend_period_summary(
                        "crypto",
                        bucket_seconds=60 * 60,
                        lookback_seconds=60 * 60 * 24,
                        top_n=5,
                        min_share_pct=2.0,
                    )
                )
                crypto_trend_daily_db = list(
                    self.runtime_feedback.trend_period_summary(
                        "crypto",
                        bucket_seconds=60 * 60 * 24,
                        lookback_seconds=60 * 60 * 24 * 14,
                        top_n=5,
                        min_share_pct=2.0,
                    )
                )
                crypto_trend_weekly_db = list(
                    self.runtime_feedback.trend_period_summary(
                        "crypto",
                        bucket_seconds=60 * 60 * 24 * 7,
                        lookback_seconds=60 * 60 * 24 * 56,
                        top_n=5,
                        min_share_pct=2.0,
                    )
                )
                model_tune_history = list(self.runtime_feedback.model_tune_recent(market="crypto", limit=240))
                model_tune_variant_rank = list(
                    self.runtime_feedback.model_tune_variant_rank(
                        market="crypto",
                        lookback_seconds=60 * 60 * 24 * 180,
                        limit=120,
                    )
                )
            except Exception:
                runtime_feedback_recent = []
                trend_brief_meme = []
                trend_brief_crypto = []
                trend_db_stats = {}
                meme_trend_30m_db = []
                crypto_trend_30m_db = []
                meme_trend_rank_db = []
                crypto_trend_rank_db = []
                meme_trend_share_24h = []
                crypto_trend_share_24h = []
                meme_trend_hourly_db = []
                meme_trend_daily_db = []
                meme_trend_weekly_db = []
                crypto_trend_hourly_db = []
                crypto_trend_daily_db = []
                crypto_trend_weekly_db = []
                model_tune_history = []
                model_tune_variant_rank = []
            self._feedback_cache = {
                "runtime_feedback_recent": list(runtime_feedback_recent),
                "trend_brief_meme": list(trend_brief_meme),
                "trend_brief_crypto": list(trend_brief_crypto),
                "trend_db_stats": dict(trend_db_stats),
                "meme_trend_30m_db": list(meme_trend_30m_db),
                "crypto_trend_30m_db": list(crypto_trend_30m_db),
                "meme_trend_rank_db": list(meme_trend_rank_db),
                "crypto_trend_rank_db": list(crypto_trend_rank_db),
                "meme_trend_share_24h": list(meme_trend_share_24h),
                "crypto_trend_share_24h": list(crypto_trend_share_24h),
                "meme_trend_hourly_db": list(meme_trend_hourly_db),
                "meme_trend_daily_db": list(meme_trend_daily_db),
                "meme_trend_weekly_db": list(meme_trend_weekly_db),
                "crypto_trend_hourly_db": list(crypto_trend_hourly_db),
                "crypto_trend_daily_db": list(crypto_trend_daily_db),
                "crypto_trend_weekly_db": list(crypto_trend_weekly_db),
                "model_tune_history": list(model_tune_history),
                "model_tune_variant_rank": list(model_tune_variant_rank),
            }
            self._feedback_cache_ts = float(time.time())

        meme_trend_30m_db = self._meme_trend_brief_bucket_series(
            trend_brief_meme,
            int(now_ts),
            lookback_seconds=60 * 60 * 24,
            bucket_seconds=1800,
        )
        meme_trend_rank_db = self._meme_trend_brief_rank(
            trend_brief_meme,
            int(now_ts),
            lookback_seconds=60 * 60 * 24,
            limit=120,
            feed_rows=new_meme_feed,
        )
        meme_trend_share_24h = self._meme_trend_brief_distribution(
            trend_brief_meme,
            int(now_ts),
            lookback_seconds=60 * 60 * 24,
            top_n=8,
        )
        meme_trend_hourly_db = self._meme_trend_brief_period_summary(
            trend_brief_meme,
            int(now_ts),
            bucket_seconds=60 * 60,
            lookback_seconds=60 * 60 * 24,
            limit=24,
        )
        meme_trend_daily_db = self._meme_trend_brief_period_summary(
            trend_brief_meme,
            int(now_ts),
            bucket_seconds=60 * 60 * 24,
            lookback_seconds=60 * 60 * 24 * 14,
            limit=14,
        )
        meme_trend_weekly_db = self._meme_trend_brief_period_summary(
            trend_brief_meme,
            int(now_ts),
            bucket_seconds=60 * 60 * 24 * 7,
            lookback_seconds=60 * 60 * 24 * 84,
            limit=12,
        )
        min_wallet_asset_usd = float(self.settings.min_wallet_asset_usd or 1.0)
        live_meme_watch_tokens = self._live_meme_watch_tokens()
        live_basis_map = dict(runs.get("_live_meme_basis") or {})
        live_cost_basis: dict[str, dict[str, float]] = {}
        for model_id in MEME_MODEL_IDS:
            meme_run = self._get_market_run(runs, "meme", model_id)
            rows = list(meme_run.get("trades") or [])
            rows.sort(key=lambda r: int((r or {}).get("ts") or 0))
            for tr in rows:
                if str(tr.get("source") or "").lower() != "memecoin":
                    continue
                if not self._is_live_trade_row(tr):
                    continue
                side = str(tr.get("side") or "").lower()
                token = str(tr.get("token_address") or "").strip()
                if not token:
                    continue
                qty = max(0.0, float(tr.get("qty") or 0.0))
                notional = max(0.0, float(tr.get("notional_usd") or 0.0))
                if qty <= 0.0:
                    continue
                row = live_cost_basis.setdefault(token, {"qty": 0.0, "cost_usd": 0.0})
                if side == "buy":
                    row["qty"] = float(row.get("qty") or 0.0) + qty
                    row["cost_usd"] = float(row.get("cost_usd") or 0.0) + notional
                    continue
                if side == "sell":
                    cur_qty = max(0.0, float(row.get("qty") or 0.0))
                    cur_cost = max(0.0, float(row.get("cost_usd") or 0.0))
                    if cur_qty <= 0.0:
                        continue
                    close_qty = min(cur_qty, qty)
                    avg_cost = cur_cost / max(cur_qty, 1e-12)
                    row["qty"] = max(0.0, cur_qty - close_qty)
                    row["cost_usd"] = max(0.0, cur_cost - (avg_cost * close_qty))
        live_meme_positions: list[dict[str, Any]] = []
        for asset in list(wallet_assets or []):
            symbol = str(asset.get("symbol") or "").upper().strip()
            name = str(asset.get("name") or "").strip()
            token_address = str(asset.get("token_address") or "").strip()
            qty = float(asset.get("qty") or 0.0)
            price_usd = float(asset.get("price_usd") or 0.0)
            value_usd = float(asset.get("value_usd") or 0.0)
            if value_usd <= 0.0 and qty > 0.0 and price_usd > 0.0:
                value_usd = qty * price_usd
            if price_usd <= 0.0 and qty > 0.0 and value_usd > 0.0:
                price_usd = value_usd / max(qty, 1e-12)
            force_include = token_address in live_meme_watch_tokens
            if qty <= 0.0:
                continue
            if value_usd < min_wallet_asset_usd and not force_include:
                continue
            if not self._is_memecoin_token(symbol, name, token_address):
                continue
            live_row = dict(live_cost_basis.get(token_address) or {})
            tracked_qty = max(0.0, float(live_row.get("qty") or 0.0))
            tracked_cost = max(0.0, float(live_row.get("cost_usd") or 0.0))
            entry_price = 0.0
            cost_basis = 0.0
            pnl_usd = 0.0
            pnl_pct = 0.0
            if tracked_qty > 0.0 and tracked_cost > 0.0:
                entry_price = tracked_cost / max(tracked_qty, 1e-12)
                cost_basis = entry_price * qty
                pnl_usd = value_usd - cost_basis
                pnl_pct = (pnl_usd / max(cost_basis, 1e-12)) * 100.0 if cost_basis > 0.0 else 0.0
            elif token_address:
                basis_row = dict(live_basis_map.get(token_address) or {})
                basis_price = float(basis_row.get("entry_price_usd") or 0.0)
                if basis_price > 0.0:
                    entry_price = basis_price
                    cost_basis = entry_price * qty
                    pnl_usd = value_usd - cost_basis
                    pnl_pct = (pnl_usd / max(cost_basis, 1e-12)) * 100.0 if cost_basis > 0.0 else 0.0
            live_meme_positions.append(
                {
                    "symbol": symbol,
                    "name": name,
                    "token_address": token_address,
                    "qty": qty,
                    "price_usd": price_usd,
                    "value_usd": value_usd,
                    "entry_price_usd": float(entry_price),
                    "cost_basis_usd": float(cost_basis),
                    "pnl_usd": float(pnl_usd),
                    "pnl_pct": float(pnl_pct),
                }
            )
        live_meme_positions.sort(key=lambda row: float(row.get("value_usd") or 0.0), reverse=True)
        live_meme_watchlist = [
            self._enrich_meme_score_row(dict(row or {}), str((row or {}).get("model_id") or "C"))
            for row in self._build_live_meme_watch_rows(int(now_ts), limit=180)
        ]
        live_managed_meme_positions: list[dict[str, Any]] = []
        live_meme_trades: list[dict[str, Any]] = []
        live_trade_logs: list[dict[str, Any]] = []
        for model_id in MEME_MODEL_IDS:
            meme_run = self._get_market_run(runs, "meme", model_id)
            for row in self._build_meme_positions_view(meme_run, mode_filter="live"):
                item = dict(row or {})
                item["model_id"] = str(model_id)
                item["model_name"] = self._market_model_name("meme", model_id)
                live_managed_meme_positions.append(item)
            for tr in list(meme_run.get("trades") or []):
                if not self._is_live_trade_row(tr):
                    continue
                if str((tr or {}).get("source") or "").strip().lower() != "memecoin":
                    continue
                if str((tr or {}).get("side") or "").strip().lower() == "sell" and not self._live_trade_is_realized(tr):
                    continue
                realized = self._live_trade_is_realized(tr)
                realized_pnl_usd = self._live_trade_realized_pnl_usd(tr)
                realized_pnl_pct = self._live_trade_realized_pnl_pct(tr)
                trade_row = {
                    "market": "meme",
                    "source": "memecoin",
                    "ts": int((tr or {}).get("ts") or 0),
                    "model_id": str(model_id),
                    "model_name": self._market_model_name("meme", model_id),
                    "side": str((tr or {}).get("side") or ""),
                    "symbol": str((tr or {}).get("symbol") or ""),
                    "token_address": str((tr or {}).get("token_address") or ""),
                    "price_usd": float((tr or {}).get("price_usd") or 0.0),
                    "notional_usd": float((tr or {}).get("notional_usd") or 0.0),
                    "pnl_usd": float(realized_pnl_usd) if realized_pnl_usd is not None else None,
                    "pnl_pct": (float(realized_pnl_pct) * 100.0) if realized_pnl_pct is not None else None,
                    "realized": bool(realized),
                    "realized_pnl_usd": float(realized_pnl_usd) if realized_pnl_usd is not None else None,
                    "realized_pnl_pct": (float(realized_pnl_pct) * 100.0) if realized_pnl_pct is not None else None,
                    "network_fee_usd": self._optional_float((tr or {}).get("network_fee_usd")),
                    "reason": str((tr or {}).get("reason") or ""),
                }
                live_meme_trades.append(dict(trade_row))
                live_trade_logs.append(dict(trade_row))
        for model_id in CRYPTO_MODEL_IDS:
            crypto_run = self._get_market_run(runs, "crypto", model_id)
            for tr in list(crypto_run.get("trades") or []):
                if not self._is_live_trade_row(tr):
                    continue
                if str((tr or {}).get("source") or "").strip().lower() != "crypto_demo":
                    continue
                live_trade_logs.append(
                    {
                        "market": "crypto",
                        "source": "crypto_demo",
                        "ts": int((tr or {}).get("ts") or 0),
                        "model_id": str(model_id),
                        "model_name": self._market_model_name("crypto", model_id),
                        "side": str((tr or {}).get("side") or ""),
                        "symbol": str((tr or {}).get("symbol") or ""),
                        "token_address": str((tr or {}).get("token_address") or ""),
                        "price_usd": float((tr or {}).get("price_usd") or 0.0),
                        "notional_usd": float((tr or {}).get("notional_usd") or 0.0),
                        "pnl_usd": float((tr or {}).get("pnl_usd") or 0.0),
                        "pnl_pct": float((tr or {}).get("pnl_pct") or 0.0) * 100.0,
                        "reason": str((tr or {}).get("reason") or ""),
                    }
                )
        live_seen_tokens = {str((row or {}).get("token_address") or "").strip() for row in live_meme_positions}
        for row in list(live_managed_meme_positions or []):
            token_address = str((row or {}).get("token_address") or "").strip()
            if not token_address or token_address in live_seen_tokens:
                continue
            live_seen_tokens.add(token_address)
            live_meme_positions.append(
                {
                    "symbol": str((row or {}).get("symbol") or ""),
                    "name": str((row or {}).get("symbol") or ""),
                    "token_address": token_address,
                    "qty": float((row or {}).get("qty") or 0.0),
                    "price_usd": float((row or {}).get("current_price_usd") or (row or {}).get("avg_price_usd") or 0.0),
                    "value_usd": float((row or {}).get("value_usd") or 0.0),
                    "entry_price_usd": float((row or {}).get("avg_price_usd") or 0.0),
                    "cost_basis_usd": float((row or {}).get("avg_price_usd") or 0.0) * float((row or {}).get("qty") or 0.0),
                    "pnl_usd": float((row or {}).get("pnl_usd") or 0.0),
                    "pnl_pct": float((row or {}).get("pnl_pct") or 0.0),
                }
            )
        live_meme_positions.sort(key=lambda row: float(row.get("value_usd") or 0.0), reverse=True)
        live_managed_meme_positions.sort(key=lambda row: float(row.get("value_usd") or 0.0), reverse=True)
        live_meme_trades.sort(key=lambda row: int(row.get("ts") or 0), reverse=True)
        if len(live_meme_trades) > 600:
            live_meme_trades = live_meme_trades[:600]
        live_trade_logs.sort(key=lambda row: int(row.get("ts") or 0), reverse=True)
        if len(live_trade_logs) > 1200:
            live_trade_logs = live_trade_logs[:1200]
        wallet_total_usd = float(sum(float((a or {}).get("value_usd") or 0.0) for a in list(wallet_assets or [])))
        wallet_sol_usd = 0.0
        for a in list(wallet_assets or []):
            if str((a or {}).get("symbol") or "").upper().strip() == "SOL":
                wallet_sol_usd = float((a or {}).get("value_usd") or 0.0)
                break
        live_meme_value_usd = float(sum(float((row or {}).get("value_usd") or 0.0) for row in live_meme_positions))
        live_meme_upnl_usd = float(sum(float((row or {}).get("pnl_usd") or 0.0) for row in live_meme_positions))
        live_managed_meme_value_usd = float(
            sum(float((row or {}).get("value_usd") or 0.0) for row in live_managed_meme_positions)
        )
        live_managed_meme_upnl_usd = float(
            sum(float((row or {}).get("pnl_usd") or 0.0) for row in live_managed_meme_positions)
        )
        live_equity_usd = self._live_equity_usd_from_assets(wallet_assets, bybit_assets)
        live_perf_anchor_usd = float(live_perf_anchor_saved) if float(live_perf_anchor_saved) > 0.0 else float(live_equity_usd)
        live_perf_anchor_ts = int(live_perf_anchor_ts_saved) if int(live_perf_anchor_ts_saved) > 0 else int(now_ts)
        live_seed_usd = float(live_seed_saved) if float(live_seed_saved) > 0.0 else float(live_perf_anchor_usd)
        live_net_flow_usd = float(live_net_flow_saved)
        live_adjusted_equity_usd = float(live_equity_usd - live_net_flow_usd)
        live_perf_pnl_usd = float(live_adjusted_equity_usd - live_perf_anchor_usd)
        live_perf_roi_pct = float((live_perf_pnl_usd / max(live_perf_anchor_usd, 1e-9)) * 100.0) if live_perf_anchor_usd > 0.0 else 0.0
        live_pnl_usd = float(live_perf_pnl_usd)
        sol_budget = self._solana_trade_budget()
        rebuild_watch_map = dict(runs.get("_model_rebuild_watch") or {})
        rebuild_watch_rows: list[dict[str, Any]] = []
        for key, row in rebuild_watch_map.items():
            item = dict(row or {})
            if not item:
                continue
            item["key"] = str(key)
            rebuild_watch_rows.append(item)
        rebuild_watch_rows.sort(
            key=lambda r: (int(r.get("last_breach_ts") or 0), float(r.get("latest_drawdown_ratio") or 0.0)),
            reverse=True,
        )
        openai_review_state = self.openai_advisor.dashboard_payload(now_ts)
        openai_review_preview = self._openai_candidate_preview(model_views)

        return {
            "server_time": now_ts,
            "running": self.running,
            "mode": str(self.settings.trade_mode or "paper"),
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
            "google_runtime": google_runtime,
            "runtime_feedback_recent": runtime_feedback_recent,
            "trend_brief_meme": trend_brief_meme,
            "trend_brief_crypto": trend_brief_crypto,
            "trend_db_stats": trend_db_stats,
            "meme_trend_30m_db": meme_trend_30m_db,
            "crypto_trend_30m_db": crypto_trend_30m_db,
            "meme_trend_rank_db": meme_trend_rank_db,
            "crypto_trend_rank_db": crypto_trend_rank_db,
            "meme_trend_share_24h": meme_trend_share_24h,
            "crypto_trend_share_24h": crypto_trend_share_24h,
            "meme_trend_hourly_db": meme_trend_hourly_db,
            "meme_trend_daily_db": meme_trend_daily_db,
            "meme_trend_weekly_db": meme_trend_weekly_db,
            "crypto_trend_hourly_db": crypto_trend_hourly_db,
            "crypto_trend_daily_db": crypto_trend_daily_db,
            "crypto_trend_weekly_db": crypto_trend_weekly_db,
            "model_tune_history": model_tune_history,
            "model_tune_variant_rank": model_tune_variant_rank,
            "last_cycle_ts": last_cycle_ts,
            "last_wallet_sync_ts": last_wallet_sync_ts,
            "last_bybit_sync_ts": last_bybit_sync_ts,
            "demo_seed_usdt": demo_seed,
            "live_seed_usd": float(live_seed_usd),
            "live_seed_set_ts": int(live_seed_set_ts),
            "live_equity_usd": float(live_equity_usd),
            "live_pnl_usd": float(live_pnl_usd),
            "live_perf_anchor_usd": float(live_perf_anchor_usd),
            "live_perf_anchor_ts": int(live_perf_anchor_ts),
            "live_net_flow_usd": float(live_net_flow_usd),
            "live_adjusted_equity_usd": float(live_adjusted_equity_usd),
            "live_perf_pnl_usd": float(live_perf_pnl_usd),
            "live_perf_roi_pct": float(live_perf_roi_pct),
            "wallet_total_usd": float(wallet_total_usd),
            "wallet_sol_usd": float(wallet_sol_usd),
            "live_meme_value_usd": float(live_meme_value_usd),
            "live_meme_upnl_usd": float(live_meme_upnl_usd),
            "live_managed_meme_value_usd": float(live_managed_meme_value_usd),
            "live_managed_meme_upnl_usd": float(live_managed_meme_upnl_usd),
            "live_markets": {
                "meme": bool(self.settings.live_enable_meme),
                "crypto": bool(self.settings.live_enable_crypto),
            },
            "solana_fee_reserve_sol": float(sol_budget.get("reserve_sol") or 0.0),
            "solana_tradeable_sol": float(sol_budget.get("tradeable_sol") or 0.0),
            "solana_tradeable_usd": float(sol_budget.get("tradeable_usd") or 0.0),
            "metrics": perf,
            "model_runs": model_runs,
            "meme_model_runs": meme_model_runs,
            "crypto_model_runs": crypto_model_runs,
            "meme_model_rankings": meme_model_rankings,
            "crypto_model_rankings": crypto_model_rankings,
            "model_views": model_views,
            "model_methods": model_methods,
            "model_profiles": model_profiles,
            "model_recommendations": model_recommendations,
            "meme_model_recommendations": meme_model_recommendations,
            "crypto_model_recommendations": crypto_model_recommendations,
            "meme_engine_spec": dict(MEME_ENGINE_SPEC),
            "meme_strategy_registry": meme_strategy_registry,
            "crypto_strategy_registry": crypto_strategy_registry,
            "meme_strategy_bridge": meme_strategy_bridge,
            "meme_strategy_labels": {sid: self._meme_strategy_name(sid) for sid in MEME_STRATEGY_IDS},
            "meme_discovery": meme_discovery_state,
            "openai_review": openai_review_state,
            "openai_review_preview": openai_review_preview,
            "meme_model_labels": {mid: self._market_model_name("meme", mid) for mid in MEME_MODEL_IDS},
            "crypto_model_labels": {mid: self._market_model_name("crypto", mid) for mid in CRYPTO_MODEL_IDS},
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
            "live_meme_positions": live_meme_positions,
            "live_meme_watchlist": live_meme_watchlist,
            "live_managed_meme_positions": live_managed_meme_positions,
            "live_meme_trades": live_meme_trades,
            "live_trade_logs": live_trade_logs,
            "wallet_assets": wallet_assets,
            "bybit_assets": bybit_assets,
            "crypto_assets": bybit_assets,
            "meme_signals": meme_signals,
            "crypto_signals": crypto_signals,
            "signals": meme_signals,
            "trend_top": trend_top,
            "trend_daily": trend_daily,
            "trend_30m": trend_30m,
            "trend_source_status": trend_source_status,
            "meme_grade_criteria": self._meme_grade_criteria(),
            "crypto_param_legend": self._crypto_param_legend(),
            "new_meme_feed": new_meme_feed,
            "focus_wallet_analysis": dict(self._focus_wallet_analysis or {}),
            "model_autotune": model_autotune,
            "b_model_autotune": b_autotune,
            "loss_guard_state": dict(runs.get("_system_guard_state") or {}),
            "model_rebuild_watch": rebuild_watch_rows,
            "alerts": alerts,
            "meme_trades": meme_trades,
            "crypto_trades": crypto_trades,
            "trades": demo_trades,
        }
        with self._lock:
            self._dashboard_cache = payload
            self._dashboard_cache_ts = float(time.time())
            self._dashboard_cache_cycle_ts = int(last_cycle_ts)
            self._dashboard_cache_wallet_ts = int(last_wallet_sync_ts)
            self._dashboard_cache_bybit_ts = int(last_bybit_sync_ts)
        return payload

