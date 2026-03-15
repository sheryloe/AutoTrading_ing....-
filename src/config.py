from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from dotenv import dotenv_values


def _to_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _to_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _to_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _to_grade(value: Any, default: str = "C") -> str:
    grade = _to_str(value, default).upper()
    if grade in {"S", "A", "B", "C", "D", "E", "F", "G"}:
        return grade
    return default


@dataclass
class Settings:
    trade_mode: str
    lock_paper_mode: bool
    enable_autotrade: bool
    enable_live_execution: bool
    enable_meme_market: bool
    live_enable_meme: bool
    live_enable_crypto: bool
    scan_interval_seconds: int
    max_signals_per_cycle: int
    signal_cooldown_minutes: int
    take_profit_pct: float
    stop_loss_pct: float
    paper_start_cash_usd: float
    paper_trade_usd: float
    hold_positions_until_gone: bool
    bybit_enable_rotation: bool
    bybit_enable_flip: bool
    trend_query: str
    google_api_key: str
    google_model: str
    google_trend_enabled: bool
    google_trend_interval_seconds: int
    google_trend_cooldown_seconds: int
    google_trend_max_symbols: int
    trend_cg_interval_seconds: int
    trend_trader_interval_seconds: int
    trend_wallet_interval_seconds: int
    trend_news_interval_seconds: int
    trend_community_interval_seconds: int
    trend_error_backoff_seconds: int
    community_subreddits: str
    community_max_items_per_subreddit: int
    dex_chain: str
    pumpfun_enabled: bool
    pumpfun_fetch_limit: int
    pumpfun_cache_seconds: int
    pumpfun_include_nsfw: bool
    max_boost_tokens_per_cycle: int
    new_meme_feed_max_age_minutes: int
    dex_min_liquidity_usd: float
    dex_min_5m_volume_usd: float
    dex_min_5m_buy_sell_ratio: float
    min_token_age_minutes: int
    min_signal_score: float
    meme_order_pct: float
    meme_max_positions: int
    meme_min_entry_grade: str
    meme_strategy_engine: str
    meme_strategy_ids: str
    live_meme_strategy_ids: str
    meme_theme_entry_sol: float
    meme_theme_cluster_min_tokens: int
    meme_launch_entry_sol: float
    meme_narrative_entry_sol: float
    meme_partial_take_profit_pct: float
    meme_partial_take_profit_sell_ratio: float
    meme_stale_exit_hours: int
    meme_sniper_social_window_seconds: int
    meme_sniper_poll_seconds: int
    meme_sniper_max_slippage_bps: int
    meme_sniper_max_price_impact_pct: float
    demo_order_pct_min: float
    demo_order_pct_max: float
    meme_swing_enabled: bool
    meme_swing_hold_days: int
    meme_swing_min_grade: str
    meme_swing_target_multiple: float
    meme_swing_trailing_stop_pct: float
    wallet_update_seconds: int
    watch_trader_accounts: str
    watch_wallets: str
    social_4chan_enabled: bool
    social_4chan_boards: str
    social_4chan_max_threads_per_board: int
    solana_rpc_url: str
    helius_api_key: str
    helius_rpc_url: str
    helius_ws_url: str
    helius_sender_url: str
    birdeye_api_key: str
    openai_api_key: str
    openai_model: str
    openai_review_enabled: bool
    openai_monthly_budget_usd: float
    openai_daily_budget_usd: float
    openai_candidate_review_interval_seconds: int
    openai_candidate_top_n: int
    openai_candidate_min_score: float
    openai_narrative_max_calls_per_day: int
    openai_input_token_estimate: int
    openai_output_token_estimate: int
    phantom_wallet_address: str
    solana_private_key: str
    solana_reserve_sol: float
    min_wallet_asset_usd: float
    binance_api_key: str
    binance_api_secret: str
    binance_inference_only: bool
    bybit_api_key: str
    bybit_api_secret: str
    bybit_base_url: str
    bybit_recv_window: int
    bybit_order_pct: float
    bybit_order_pct_min: float
    bybit_order_pct_max: float
    intrabar_conflict_policy: str
    bybit_leverage_min: float
    bybit_leverage_max: float
    bybit_max_positions: int
    bybit_min_order_usd: float
    crypto_min_entry_score: float
    meme_autotrade_models: str
    crypto_autotrade_models: str
    live_meme_models: str
    live_crypto_models: str
    bybit_symbols: str
    telegram_polling_enabled: bool
    telegram_poll_interval_seconds: int
    telegram_language: str
    telegram_bot_token: str
    telegram_chat_id: str
    telegram_trade_alerts_enabled: bool
    telegram_report_enabled: bool
    telegram_report_interval_seconds: int
    ui_refresh_seconds: int
    app_host: str
    app_port: int
    demo_seed_usdt: float
    allow_demo_reset: bool
    demo_reset_block_until_ts: int
    model_autotune_interval_hours: int
    demo_enable_bybit: bool
    demo_enable_macro: bool
    macro_universe_source: str
    macro_top_n: int
    macro_rank_min: int
    macro_rank_max: int
    macro_trend_pool_size: int
    macro_trend_reselect_seconds: int
    macro_realtime_sources: str
    macro_realtime_cache_seconds: int
    cmc_api_key: str
    coingecko_api_key: str
    crypto_news_symbols: str
    solscan_enable_pattern: bool
    solscan_api_key: str
    solscan_tracker_only: bool
    solscan_focus_token: str
    solscan_cache_seconds: int
    solscan_monthly_cu_limit: int
    solscan_cu_per_request: int
    solscan_budget_window_seconds: int
    solscan_permission_backoff_seconds: int
    git_daily_reports_enabled: bool
    git_daily_reports_autocommit: bool
    git_daily_reports_autopush: bool
    git_daily_reports_path: str
    git_daily_reports_branch: str
    git_committer_name: str
    git_committer_email: str
    supabase_sync_enabled: bool
    supabase_url: str
    supabase_secret_key: str
    supabase_sync_timeout_seconds: int
    openai_budget_state_file: str
    model_file: str
    state_file: str
    runtime_settings_file: str
    runtime_feedback_db_file: str

    @property
    def is_live_mode(self) -> bool:
        return self.trade_mode == "live"

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "Settings":
        trade_mode = _to_str(data.get("TRADE_MODE"), "paper").lower()
        if trade_mode not in {"paper", "live"}:
            trade_mode = "paper"
        settings = cls(
            trade_mode=trade_mode,
            lock_paper_mode=_to_bool(data.get("LOCK_PAPER_MODE"), False),
            enable_autotrade=_to_bool(data.get("ENABLE_AUTOTRADE"), True),
            enable_live_execution=_to_bool(data.get("ENABLE_LIVE_EXECUTION"), False),
            enable_meme_market=_to_bool(data.get("ENABLE_MEME_MARKET"), False),
            live_enable_meme=_to_bool(data.get("LIVE_ENABLE_MEME"), True),
            live_enable_crypto=_to_bool(data.get("LIVE_ENABLE_CRYPTO"), True),
            scan_interval_seconds=max(5, _to_int(data.get("SCAN_INTERVAL_SECONDS"), 480)),
            max_signals_per_cycle=max(1, min(10, _to_int(data.get("MAX_SIGNALS_PER_CYCLE"), 3))),
            signal_cooldown_minutes=max(1, _to_int(data.get("SIGNAL_COOLDOWN_MINUTES"), 10)),
            take_profit_pct=max(0.01, _to_float(data.get("TAKE_PROFIT_PCT"), 0.18)),
            stop_loss_pct=max(0.01, _to_float(data.get("STOP_LOSS_PCT"), 0.08)),
            paper_start_cash_usd=max(10.0, _to_float(data.get("PAPER_START_CASH_USD"), 500.0)),
            paper_trade_usd=max(1.0, _to_float(data.get("PAPER_TRADE_USD"), 25.0)),
            hold_positions_until_gone=_to_bool(data.get("HOLD_POSITIONS_UNTIL_GONE"), True),
            bybit_enable_rotation=_to_bool(data.get("BYBIT_ENABLE_ROTATION"), False),
            bybit_enable_flip=_to_bool(data.get("BYBIT_ENABLE_FLIP"), False),
            trend_query=_to_str(
                data.get("TREND_QUERY"),
                '(memecoin OR "meme coin" OR $BONK OR $WIF OR $PEPE OR $FLOKI OR $POPCAT) lang:en',
            ),
            google_api_key=_to_str(data.get("GOOGLE_API_KEY"), ""),
            google_model=_to_str(data.get("GOOGLE_MODEL"), "gemini-2.5-flash"),
            google_trend_enabled=_to_bool(data.get("GOOGLE_TREND_ENABLED"), True),
            # Keep Gemini usage within free-tier constraints by default.
            google_trend_interval_seconds=max(14000, _to_int(data.get("GOOGLE_TREND_INTERVAL_SECONDS"), 14000)),
            google_trend_cooldown_seconds=max(14000, _to_int(data.get("GOOGLE_TREND_COOLDOWN_SECONDS"), 21600)),
            google_trend_max_symbols=max(5, min(40, _to_int(data.get("GOOGLE_TREND_MAX_SYMBOLS"), 15))),
            trend_cg_interval_seconds=max(60, _to_int(data.get("TREND_CG_INTERVAL_SECONDS"), 300)),
            trend_trader_interval_seconds=max(120, _to_int(data.get("TREND_TRADER_INTERVAL_SECONDS"), 600)),
            trend_wallet_interval_seconds=max(60, _to_int(data.get("TREND_WALLET_INTERVAL_SECONDS"), 300)),
            trend_news_interval_seconds=max(120, _to_int(data.get("TREND_NEWS_INTERVAL_SECONDS"), 600)),
            trend_community_interval_seconds=max(120, _to_int(data.get("TREND_COMMUNITY_INTERVAL_SECONDS"), 600)),
            trend_error_backoff_seconds=max(120, _to_int(data.get("TREND_ERROR_BACKOFF_SECONDS"), 900)),
            community_subreddits=_to_str(
                data.get("COMMUNITY_SUBREDDITS"),
                "memecoins,solana,solanamemecoins,CryptoMoonShots",
            ),
            community_max_items_per_subreddit=max(
                1,
                min(25, _to_int(data.get("COMMUNITY_MAX_ITEMS_PER_SUBREDDIT"), 8)),
            ),
            dex_chain=_to_str(data.get("DEX_CHAIN"), "solana").lower(),
            pumpfun_enabled=_to_bool(data.get("PUMPFUN_ENABLED"), True),
            pumpfun_fetch_limit=max(20, min(300, _to_int(data.get("PUMPFUN_FETCH_LIMIT"), 120))),
            pumpfun_cache_seconds=max(5, min(300, _to_int(data.get("PUMPFUN_CACHE_SECONDS"), 45))),
            pumpfun_include_nsfw=_to_bool(data.get("PUMPFUN_INCLUDE_NSFW"), False),
            max_boost_tokens_per_cycle=max(10, _to_int(data.get("MAX_BOOST_TOKENS_PER_CYCLE"), 120)),
            new_meme_feed_max_age_minutes=max(5, min(1440, _to_int(data.get("NEW_MEME_FEED_MAX_AGE_MINUTES"), 45))),
            dex_min_liquidity_usd=max(0.0, _to_float(data.get("DEX_MIN_LIQUIDITY_USD"), 3000.0)),
            dex_min_5m_volume_usd=max(0.0, _to_float(data.get("DEX_MIN_5M_VOLUME_USD"), 250.0)),
            dex_min_5m_buy_sell_ratio=max(0.1, _to_float(data.get("DEX_MIN_5M_BUY_SELL_RATIO"), 0.85)),
            min_token_age_minutes=max(0, _to_int(data.get("MIN_TOKEN_AGE_MINUTES"), 1)),
            min_signal_score=max(0.0, _to_float(data.get("MIN_SIGNAL_SCORE"), 0.56)),
            meme_order_pct=min(1.0, max(0.01, _to_float(data.get("MEME_ORDER_PCT"), 0.18))),
            meme_max_positions=max(1, _to_int(data.get("MEME_MAX_POSITIONS"), 5)),
            meme_min_entry_grade=_to_grade(data.get("MEME_MIN_ENTRY_GRADE"), "E"),
            meme_strategy_engine=_to_str(data.get("MEME_STRATEGY_ENGINE"), "unified_strategy_bridge").lower(),
            meme_strategy_ids=_to_str(data.get("MEME_STRATEGY_IDS"), "THEME,SNIPER,NARRATIVE"),
            live_meme_strategy_ids=_to_str(
                data.get("LIVE_MEME_STRATEGY_IDS"),
                _to_str(data.get("MEME_STRATEGY_IDS"), "THEME,SNIPER,NARRATIVE"),
            ),
            meme_theme_entry_sol=max(0.001, _to_float(data.get("MEME_THEME_ENTRY_SOL"), 0.10)),
            meme_theme_cluster_min_tokens=max(2, min(10, _to_int(data.get("MEME_THEME_CLUSTER_MIN_TOKENS"), 2))),
            meme_launch_entry_sol=max(0.001, _to_float(data.get("MEME_LAUNCH_ENTRY_SOL"), 0.20)),
            meme_narrative_entry_sol=max(0.001, _to_float(data.get("MEME_NARRATIVE_ENTRY_SOL"), 0.20)),
            meme_partial_take_profit_pct=min(10.0, max(0.01, _to_float(data.get("MEME_PARTIAL_TAKE_PROFIT_PCT"), 1.00))),
            meme_partial_take_profit_sell_ratio=min(1.0, max(0.01, _to_float(data.get("MEME_PARTIAL_TAKE_PROFIT_SELL_RATIO"), 0.50))),
            meme_stale_exit_hours=max(1, min(24 * 30, _to_int(data.get("MEME_STALE_EXIT_HOURS"), 48))),
            meme_sniper_social_window_seconds=max(5, min(3600, _to_int(data.get("MEME_SNIPER_SOCIAL_WINDOW_SECONDS"), 120))),
            meme_sniper_poll_seconds=max(1, min(60, _to_int(data.get("MEME_SNIPER_POLL_SECONDS"), 3))),
            meme_sniper_max_slippage_bps=max(10, min(5000, _to_int(data.get("MEME_SNIPER_MAX_SLIPPAGE_BPS"), 400))),
            meme_sniper_max_price_impact_pct=min(1.0, max(0.0, _to_float(data.get("MEME_SNIPER_MAX_PRICE_IMPACT_PCT"), 0.15))),
            demo_order_pct_min=min(0.95, max(0.01, _to_float(data.get("DEMO_ORDER_PCT_MIN"), 0.15))),
            demo_order_pct_max=min(0.95, max(0.01, _to_float(data.get("DEMO_ORDER_PCT_MAX"), 0.30))),
            meme_swing_enabled=_to_bool(data.get("MEME_SWING_ENABLED"), True),
            meme_swing_hold_days=max(1, min(60, _to_int(data.get("MEME_SWING_HOLD_DAYS"), 14))),
            meme_swing_min_grade=_to_grade(data.get("MEME_SWING_MIN_GRADE"), "A"),
            meme_swing_target_multiple=max(1.2, min(200.0, _to_float(data.get("MEME_SWING_TARGET_MULTIPLE"), 5.0))),
            meme_swing_trailing_stop_pct=min(0.95, max(0.02, _to_float(data.get("MEME_SWING_TRAILING_STOP_PCT"), 0.30))),
            wallet_update_seconds=max(10, _to_int(data.get("WALLET_UPDATE_SECONDS"), 45)),
            watch_trader_accounts=_to_str(
                data.get("WATCH_TRADER_ACCOUNTS"),
                "lookonchain,HsakaTrades,blknoiz06,RookieXBT,pentosh1,CryptoKaleo,tier10k,zachxbt,murad,cobie,Ansem,0xMert_,TheFlowHorse,AltcoinSherpa,DegenSpartan,DefiIgnas,KookCapitalLLC,LedgerStatus,CryptoCred,CryptoHayes,zhusu,jfizzy,AP_Abacus,rektdiomedes,TheMoonCarl,scottmelker,cz_binance,Arthur_0x,TheCryptoDog,CanteringClark,MandoCT,KoroushAK,DonAlt,CryptoMichNL,IncomeSharks,CredibleCrypto,CryptoTony__,CryptoCapo_,MikybullCrypto,MMCrypto,CRYPTOBIRB,AviFelman,Qwatio,ByzGeneral,SalsaTekila,rektfencer,CryptoRover,AltcoinPsycho,MoonOverlord,CryptoGodJohn,TheCryptoLark,coinbureau,MessariCrypto,WuBlockchain,watcherguru,WhaleChart,CryptoSlate,CoinDesk,TheBlock__,db_news247,deitaone,CryptoBriefing,Cointelegraph,CoinMarketCap,coingecko,binance,Bybit_Official,okx,krakenfx,gate_io,kucoincom,MEXC_Official,solana,solanafloor,SolanaLegend,pumpdotfun,bonk_inu,dogwifcoin,RealFlokiInu,popcatsol,bome_meme,Slerfsol,jup_ag,raydiumprotocol,tensor_hq,MagicEden,birdeye_so,Dexscreener,geckoterminal,CryptoRank_io,tokenterminal,DefiLlama,aeyakovenko,GCRClassic,ilCapoOfCrypto,milesdeutscher,nansen_ai,ArkhamIntel,santimentfeed,glassnode,intotheblock",
            ),
            watch_wallets=_to_str(data.get("WATCH_WALLETS"), ""),
            social_4chan_enabled=_to_bool(data.get("SOCIAL_4CHAN_ENABLED"), True),
            social_4chan_boards=_to_str(data.get("SOCIAL_4CHAN_BOARDS"), "biz,pol"),
            social_4chan_max_threads_per_board=max(1, min(50, _to_int(data.get("SOCIAL_4CHAN_MAX_THREADS_PER_BOARD"), 12))),
            solana_rpc_url=_to_str(data.get("SOLANA_RPC_URL"), "https://api.mainnet-beta.solana.com"),
            helius_api_key=_to_str(data.get("HELIUS_API_KEY"), ""),
            helius_rpc_url=_to_str(data.get("HELIUS_RPC_URL"), ""),
            helius_ws_url=_to_str(data.get("HELIUS_WS_URL"), ""),
            helius_sender_url=_to_str(data.get("HELIUS_SENDER_URL"), ""),
            birdeye_api_key=_to_str(data.get("BIRDEYE_API_KEY"), ""),
            openai_api_key=_to_str(data.get("OPENAI_API_KEY"), ""),
            openai_model=_to_str(data.get("OPENAI_MODEL"), "gpt-5-mini"),
            openai_review_enabled=_to_bool(data.get("OPENAI_REVIEW_ENABLED"), False),
            openai_monthly_budget_usd=max(1.0, _to_float(data.get("OPENAI_MONTHLY_BUDGET_USD"), 30.0)),
            openai_daily_budget_usd=max(0.1, _to_float(data.get("OPENAI_DAILY_BUDGET_USD"), 0.85)),
            openai_candidate_review_interval_seconds=max(300, _to_int(data.get("OPENAI_CANDIDATE_REVIEW_INTERVAL_SECONDS"), 600)),
            openai_candidate_top_n=max(3, min(20, _to_int(data.get("OPENAI_CANDIDATE_TOP_N"), 8))),
            openai_candidate_min_score=max(0.0, min(1.0, _to_float(data.get("OPENAI_CANDIDATE_MIN_SCORE"), 0.62))),
            openai_narrative_max_calls_per_day=max(1, min(100, _to_int(data.get("OPENAI_NARRATIVE_MAX_CALLS_PER_DAY"), 12))),
            openai_input_token_estimate=max(256, min(20000, _to_int(data.get("OPENAI_INPUT_TOKEN_ESTIMATE"), 2600))),
            openai_output_token_estimate=max(64, min(4000, _to_int(data.get("OPENAI_OUTPUT_TOKEN_ESTIMATE"), 220))),
            phantom_wallet_address=_to_str(data.get("PHANTOM_WALLET_ADDRESS"), ""),
            solana_private_key=_to_str(data.get("SOLANA_PRIVATE_KEY"), ""),
            solana_reserve_sol=max(0.0, _to_float(data.get("SOLANA_RESERVE_SOL"), 0.01)),
            min_wallet_asset_usd=max(0.0, _to_float(data.get("MIN_WALLET_ASSET_USD"), 1.0)),
            binance_api_key=_to_str(data.get("BINANCE_API_KEY"), ""),
            binance_api_secret=_to_str(data.get("BINANCE_API_SECRET"), ""),
            binance_inference_only=_to_bool(data.get("BINANCE_INFERENCE_ONLY"), True),
            bybit_api_key=_to_str(data.get("BYBIT_API_KEY"), ""),
            bybit_api_secret=_to_str(data.get("BYBIT_API_SECRET"), ""),
            bybit_base_url=_to_str(data.get("BYBIT_BASE_URL"), "https://api.bybit.com"),
            bybit_recv_window=max(1000, _to_int(data.get("BYBIT_RECV_WINDOW"), 5000)),
            bybit_order_pct=min(0.30, max(0.10, _to_float(data.get("BYBIT_ORDER_PCT"), 0.20))),
            bybit_order_pct_min=min(0.30, max(0.10, _to_float(data.get("BYBIT_ORDER_PCT_MIN"), 0.10))),
            bybit_order_pct_max=min(0.30, max(0.10, _to_float(data.get("BYBIT_ORDER_PCT_MAX"), 0.30))),
            intrabar_conflict_policy=(
                _to_str(data.get("INTRABAR_CONFLICT_POLICY"), "conservative").lower()
                if _to_str(data.get("INTRABAR_CONFLICT_POLICY"), "conservative").lower() in {"conservative", "neutral", "aggressive"}
                else "conservative"
            ),
            bybit_leverage_min=min(25.0, max(5.0, _to_float(data.get("BYBIT_LEVERAGE_MIN"), 5.0))),
            bybit_leverage_max=min(25.0, max(5.0, _to_float(data.get("BYBIT_LEVERAGE_MAX"), 25.0))),
            bybit_max_positions=max(1, _to_int(data.get("BYBIT_MAX_POSITIONS"), 3)),
            bybit_min_order_usd=max(5.0, _to_float(data.get("BYBIT_MIN_ORDER_USD"), 10.0)),
            crypto_min_entry_score=min(1.0, max(0.0, _to_float(data.get("CRYPTO_MIN_ENTRY_SCORE"), 0.30))),
            meme_autotrade_models=_to_str(data.get("MEME_AUTOTRADE_MODELS"), "A,B,C"),
            crypto_autotrade_models=_to_str(data.get("CRYPTO_AUTOTRADE_MODELS"), "A,B,C"),
            live_meme_models=_to_str(
                data.get("LIVE_MEME_MODELS"),
                _to_str(data.get("MEME_AUTOTRADE_MODELS"), "A,B,C"),
            ),
            live_crypto_models=_to_str(
                data.get("LIVE_CRYPTO_MODELS"),
                _to_str(data.get("CRYPTO_AUTOTRADE_MODELS"), "A,B,C"),
            ),
            bybit_symbols=_to_str(data.get("BYBIT_SYMBOLS"), "BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,BNBUSDT"),
            telegram_polling_enabled=_to_bool(data.get("TELEGRAM_POLLING_ENABLED"), True),
            telegram_poll_interval_seconds=max(2, _to_int(data.get("TELEGRAM_POLL_INTERVAL_SECONDS"), 5)),
            telegram_language=_to_str(data.get("TELEGRAM_LANGUAGE"), "ko"),
            telegram_bot_token=_to_str(data.get("TELEGRAM_BOT_TOKEN"), ""),
            telegram_chat_id=_to_str(data.get("TELEGRAM_CHAT_ID"), ""),
            telegram_trade_alerts_enabled=_to_bool(data.get("TELEGRAM_TRADE_ALERTS_ENABLED"), False),
            telegram_report_enabled=_to_bool(data.get("TELEGRAM_REPORT_ENABLED"), True),
            telegram_report_interval_seconds=max(60, _to_int(data.get("TELEGRAM_REPORT_INTERVAL_SECONDS"), 600)),
            ui_refresh_seconds=max(2, _to_int(data.get("UI_REFRESH_SECONDS"), 4)),
            app_host=_to_str(data.get("APP_HOST"), "0.0.0.0"),
            app_port=max(1, _to_int(data.get("APP_PORT"), 5050)),
            demo_seed_usdt=max(50.0, _to_float(data.get("DEMO_SEED_USDT"), 10000.0)),
            allow_demo_reset=_to_bool(data.get("ALLOW_DEMO_RESET"), False),
            demo_reset_block_until_ts=max(0, _to_int(data.get("DEMO_RESET_BLOCK_UNTIL_TS"), 0)),
            model_autotune_interval_hours=(
                6
                if _to_int(data.get("MODEL_AUTOTUNE_INTERVAL_HOURS"), 168) == 6
                else (
                    12
                    if _to_int(data.get("MODEL_AUTOTUNE_INTERVAL_HOURS"), 168) == 12
                    else (24 if _to_int(data.get("MODEL_AUTOTUNE_INTERVAL_HOURS"), 168) == 24 else 168)
                )
            ),
            demo_enable_bybit=_to_bool(data.get("DEMO_ENABLE_BYBIT"), False),
            demo_enable_macro=_to_bool(data.get("DEMO_ENABLE_MACRO"), True),
            macro_universe_source=_to_str(data.get("MACRO_UNIVERSE_SOURCE"), "coingecko").lower(),
            macro_top_n=max(5, min(2000, _to_int(data.get("MACRO_TOP_N"), 50))),
            macro_rank_min=max(1, min(5000, _to_int(data.get("MACRO_RANK_MIN"), 1))),
            macro_rank_max=max(1, min(5000, _to_int(data.get("MACRO_RANK_MAX"), 20))),
            macro_trend_pool_size=max(5, min(200, _to_int(data.get("MACRO_TREND_POOL_SIZE"), 30))),
            macro_trend_reselect_seconds=max(900, min(86400, _to_int(data.get("MACRO_TREND_RESELECT_SECONDS"), 14400))),
            macro_realtime_sources=_to_str(data.get("MACRO_REALTIME_SOURCES"), "binance,bybit"),
            macro_realtime_cache_seconds=max(3, min(60, _to_int(data.get("MACRO_REALTIME_CACHE_SECONDS"), 12))),
            cmc_api_key=_to_str(data.get("CMC_API_KEY"), ""),
            coingecko_api_key=_to_str(data.get("COINGECKO_API_KEY"), ""),
            crypto_news_symbols=_to_str(
                data.get("CRYPTO_NEWS_SYMBOLS"),
                "BTC-USD,ETH-USD,SOL-USD,DOGE-USD,XRP-USD",
            ),
            solscan_enable_pattern=_to_bool(data.get("SOLSCAN_ENABLE_PATTERN"), True),
            solscan_api_key=_to_str(data.get("SOLSCAN_API_KEY"), ""),
            solscan_tracker_only=_to_bool(data.get("SOLSCAN_TRACKER_ONLY"), True),
            solscan_focus_token=_to_str(data.get("SOLSCAN_FOCUS_TOKEN"), ""),
            solscan_cache_seconds=max(60, min(86400, _to_int(data.get("SOLSCAN_CACHE_SECONDS"), 1800))),
            solscan_monthly_cu_limit=max(1000, _to_int(data.get("SOLSCAN_MONTHLY_CU_LIMIT"), 10_000_000)),
            solscan_cu_per_request=max(1, _to_int(data.get("SOLSCAN_CU_PER_REQUEST"), 100)),
            solscan_budget_window_seconds=max(60, _to_int(data.get("SOLSCAN_BUDGET_WINDOW_SECONDS"), 300)),
            solscan_permission_backoff_seconds=max(300, _to_int(data.get("SOLSCAN_PERMISSION_BACKOFF_SECONDS"), 21600)),
            git_daily_reports_enabled=_to_bool(data.get("GIT_DAILY_REPORTS_ENABLED"), False),
            git_daily_reports_autocommit=_to_bool(data.get("GIT_DAILY_REPORTS_AUTOCOMMIT"), False),
            git_daily_reports_autopush=_to_bool(data.get("GIT_DAILY_REPORTS_AUTOPUSH"), False),
            git_daily_reports_path=_to_str(data.get("GIT_DAILY_REPORTS_PATH"), "reports/daily_pnl"),
            git_daily_reports_branch=_to_str(data.get("GIT_DAILY_REPORTS_BRANCH"), ""),
            git_committer_name=_to_str(data.get("GIT_COMMITTER_NAME"), ""),
            git_committer_email=_to_str(data.get("GIT_COMMITTER_EMAIL"), ""),
            supabase_sync_enabled=_to_bool(data.get("SUPABASE_SYNC_ENABLED"), False),
            supabase_url=_to_str(data.get("SUPABASE_URL"), ""),
            supabase_secret_key=_to_str(
                data.get("SUPABASE_SECRET_KEY") or data.get("SUPABASE_SERVICE_ROLE_KEY"),
                "",
            ),
            supabase_sync_timeout_seconds=max(5, _to_int(data.get("SUPABASE_SYNC_TIMEOUT_SECONDS"), 15)),
            openai_budget_state_file=_to_str(data.get("OPENAI_BUDGET_STATE_FILE"), "reports/openai_budget_state.json"),
            model_file=_to_str(data.get("MODEL_FILE"), "model_online.json"),
            state_file=_to_str(data.get("STATE_FILE"), "state.json"),
            runtime_settings_file=_to_str(data.get("RUNTIME_SETTINGS_FILE"), "runtime_settings.json"),
            runtime_feedback_db_file=_to_str(data.get("RUNTIME_FEEDBACK_DB_FILE"), "reports/runtime_feedback.db"),
        )
        if settings.bybit_leverage_max < settings.bybit_leverage_min:
            settings.bybit_leverage_min, settings.bybit_leverage_max = (
                settings.bybit_leverage_max,
                settings.bybit_leverage_min,
            )
        if settings.demo_order_pct_max < settings.demo_order_pct_min:
            settings.demo_order_pct_min, settings.demo_order_pct_max = (
                settings.demo_order_pct_max,
                settings.demo_order_pct_min,
            )
        if settings.macro_rank_max < settings.macro_rank_min:
            settings.macro_rank_min, settings.macro_rank_max = settings.macro_rank_max, settings.macro_rank_min
        return settings


def load_settings(env_path: str = ".env") -> Settings:
    env_file = dict(dotenv_values(env_path))
    merged = dict(env_file)
    merged.update(dict(os.environ))
    settings = Settings.from_mapping(merged)
    runtime_path = Path(settings.runtime_settings_file)
    if runtime_path.exists():
        try:
            runtime = json.loads(runtime_path.read_text(encoding="utf-8-sig"))
        except Exception:
            runtime = {}
        if isinstance(runtime, dict) and runtime:
            with_runtime = dict(merged)
            with_runtime.update(runtime)
            settings = Settings.from_mapping(with_runtime)
    return settings


def save_runtime_overrides(settings: Settings, updates: dict[str, Any]) -> None:
    runtime_path = Path(settings.runtime_settings_file)
    payload: dict[str, Any] = {}
    if runtime_path.exists():
        try:
            current = json.loads(runtime_path.read_text(encoding="utf-8-sig"))
            if isinstance(current, dict):
                payload.update(current)
        except Exception:
            pass
    payload.update(updates)
    runtime_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


def settings_to_public_dict(settings: Settings) -> dict[str, Any]:
    data = asdict(settings)
    if data.get("telegram_bot_token"):
        data["telegram_bot_token"] = "***"
    if data.get("bybit_api_secret"):
        data["bybit_api_secret"] = "***"
    if data.get("bybit_api_key"):
        data["bybit_api_key"] = "***"
    if data.get("binance_api_secret"):
        data["binance_api_secret"] = "***"
    if data.get("binance_api_key"):
        data["binance_api_key"] = "***"
    if data.get("cmc_api_key"):
        data["cmc_api_key"] = "***"
    if data.get("coingecko_api_key"):
        data["coingecko_api_key"] = "***"
    if data.get("google_api_key"):
        data["google_api_key"] = "***"
    if data.get("solana_private_key"):
        data["solana_private_key"] = "***"
    if data.get("solscan_api_key"):
        data["solscan_api_key"] = "***"
    if data.get("helius_api_key"):
        data["helius_api_key"] = "***"
    if data.get("helius_rpc_url"):
        data["helius_rpc_url"] = "***"
    if data.get("helius_ws_url"):
        data["helius_ws_url"] = "***"
    if data.get("helius_sender_url"):
        data["helius_sender_url"] = "***"
    if data.get("birdeye_api_key"):
        data["birdeye_api_key"] = "***"
    if data.get("openai_api_key"):
        data["openai_api_key"] = "***"
    if data.get("supabase_secret_key"):
        data["supabase_secret_key"] = "***"
    return data
