from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MemeDiscoveryConfig:
    helius_api_key: str
    helius_rpc_url: str
    helius_ws_url: str
    helius_sender_url: str
    birdeye_api_key: str
    social_4chan_enabled: bool
    social_4chan_boards: str
    social_4chan_max_threads_per_board: int
    meme_sniper_poll_seconds: int
    meme_sniper_social_window_seconds: int
    meme_theme_cluster_min_tokens: int


class MemeDiscoveryService:
    """Strategy-facing discovery budget and capability registry.

    This is intentionally a thin orchestration layer in phase 1. It does not
    replace the legacy meme engine yet; it exposes capability/budget metadata so
    theme/sniper/narrative services can be implemented on top without changing
    the current state schema in one shot.
    """

    HELIUS_FREE_MONTHLY_CREDITS = 1_000_000
    HELIUS_FREE_RPS = 10
    BIRDEYE_WALLET_ENDPOINT_RPM = 30

    def __init__(self, config: MemeDiscoveryConfig) -> None:
        self.config = config

    def capabilities(self) -> dict[str, Any]:
        helius_ready = bool(self.config.helius_api_key and (self.config.helius_rpc_url or self.config.helius_ws_url))
        return {
            "helius_enabled": bool(self.config.helius_api_key),
            "helius_rpc_ready": bool(self.config.helius_api_key and self.config.helius_rpc_url),
            "helius_ws_ready": bool(self.config.helius_api_key and self.config.helius_ws_url),
            "helius_sender_ready": bool(self.config.helius_api_key and self.config.helius_sender_url),
            "birdeye_enabled": bool(self.config.birdeye_api_key),
            "fourchan_enabled": bool(self.config.social_4chan_enabled),
            "launch_discovery_ready": bool(helius_ready),
            "narrative_discovery_ready": bool(self.config.social_4chan_enabled),
        }

    def budget_snapshot(self) -> dict[str, Any]:
        poll_seconds = max(1, int(self.config.meme_sniper_poll_seconds))
        launches_per_day = int(86400 // poll_seconds)
        conservative_launches = min(launches_per_day, 28_800)
        fourchan_threads = max(1, int(self.config.social_4chan_max_threads_per_board))
        boards = [b.strip() for b in str(self.config.social_4chan_boards or "").split(",") if b.strip()]
        fourchan_polls_per_day = 0
        if self.config.social_4chan_enabled:
            fourchan_polls_per_day = len(boards) * max(1, int(86400 // max(60, poll_seconds * 20)))
        return {
            "helius": {
                "plan": "free",
                "monthly_credits_limit": int(self.HELIUS_FREE_MONTHLY_CREDITS),
                "recommended_rps_cap": int(self.HELIUS_FREE_RPS),
                "sender_available": bool(self.config.helius_api_key),
                "launch_poll_seconds": int(poll_seconds),
                "estimated_launch_checks_per_day": int(conservative_launches),
            },
            "birdeye": {
                "plan": "free_or_trial",
                "wallet_endpoint_rpm_limit": int(self.BIRDEYE_WALLET_ENDPOINT_RPM),
                "recommended_mode": "hydrate only top candidates, avoid wallet-heavy polling",
                "enabled": bool(self.config.birdeye_api_key),
            },
            "fourchan": {
                "enabled": bool(self.config.social_4chan_enabled),
                "boards": boards,
                "max_threads_per_board": int(fourchan_threads),
                "estimated_catalog_polls_per_day": int(fourchan_polls_per_day),
            },
        }

    def runtime_policy(self) -> dict[str, Any]:
        caps = self.capabilities()
        budgets = self.budget_snapshot()
        return {
            "engine_mode": "unified_strategy_bridge",
            "meme_engine_id": "MEME_ONE",
            "primary_mode": "THEME_SNIPER",
            "secondary_triggers": ["NARRATIVE"],
            "primary_signal_sources": ["THEME", "SNIPER"],
            "theme_cluster_min_tokens": int(self.config.meme_theme_cluster_min_tokens),
            "social_window_seconds": int(self.config.meme_sniper_social_window_seconds),
            "launch": {
                "enabled": bool(caps["launch_discovery_ready"]),
                "primary_source": "helius" if bool(caps["launch_discovery_ready"]) else "pumpfun_poll_only",
                "fallback_source": "pumpfun+bonk polling",
                "recommended_poll_seconds": int(budgets["helius"]["launch_poll_seconds"]),
                "role": "THEME_SNIPER 메인 발견기",
            },
            "narrative": {
                "enabled": bool(caps["narrative_discovery_ready"]),
                "sources": ["x", "reddit"] + (["4chan"] if bool(caps["fourchan_enabled"]) else []),
                "recommended_refresh_seconds": max(60, int(self.config.meme_sniper_social_window_seconds)),
                "role": "재점화형 서브 트리거",
            },
            "hydration": {
                "preferred": "birdeye" if bool(caps["birdeye_enabled"]) else "dexscreener",
                "fallback": "dexscreener",
            },
        }

    def dashboard_payload(self) -> dict[str, Any]:
        return {
            "capabilities": self.capabilities(),
            "budget": self.budget_snapshot(),
            "policy": self.runtime_policy(),
        }
