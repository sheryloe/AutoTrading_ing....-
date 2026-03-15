from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any

STATE_TRADE_HISTORY_LIMIT = 50_000
STATE_SIGNAL_HISTORY_LIMIT = 2_000
STATE_ALERT_HISTORY_LIMIT = 5_000
STATE_TREND_HISTORY_LIMIT = 5_000
STATE_WALLET_HISTORY_LIMIT = 5_000
STATE_ASSET_HISTORY_LIMIT = 2_000
STATE_DAILY_PNL_HISTORY_LIMIT = 5_000


@dataclass
class Position:
    token_address: str
    symbol: str
    qty: float
    avg_price_usd: float
    opened_at: int
    mode: str = "paper"
    source: str = "memecoin"
    side: str = "long"
    score: float = 0.0
    reason: str = ""
    entry_features: dict[str, float] = field(default_factory=dict)


@dataclass
class Trade:
    ts: int
    side: str
    symbol: str
    token_address: str
    qty: float
    price_usd: float
    notional_usd: float
    pnl_usd: float = 0.0
    pnl_pct: float = 0.0
    reason: str = ""
    mode: str = "paper"
    source: str = "memecoin"


@dataclass
class EngineState:
    cash_usd: float
    positions: dict[str, Position] = field(default_factory=dict)
    trades: list[Trade] = field(default_factory=list)
    last_signal_ts: dict[str, float] = field(default_factory=dict)
    latest_signals: list[dict[str, Any]] = field(default_factory=list)
    alerts: list[dict[str, Any]] = field(default_factory=list)
    trend_events: list[dict[str, Any]] = field(default_factory=list)
    wallet_assets: list[dict[str, Any]] = field(default_factory=list)
    bybit_assets: list[dict[str, Any]] = field(default_factory=list)
    bybit_positions: list[dict[str, Any]] = field(default_factory=list)
    bybit_error: str = ""
    memecoin_error: str = ""
    last_cycle_ts: int = 0
    last_wallet_sync_ts: int = 0
    last_bybit_sync_ts: int = 0
    telegram_offset: int = 0
    demo_seed_usdt: float = 1000.0
    live_seed_usd: float = 0.0
    live_seed_set_ts: int = 0
    live_perf_anchor_usd: float = 0.0
    live_perf_anchor_ts: int = 0
    live_net_flow_usd: float = 0.0
    model_runs: dict[str, Any] = field(default_factory=dict)
    daily_pnl: list[dict[str, Any]] = field(default_factory=list)


def _position_from_dict(data: dict[str, Any]) -> Position:
    return Position(
        token_address=str(data.get("token_address") or ""),
        symbol=str(data.get("symbol") or ""),
        qty=float(data.get("qty") or 0.0),
        avg_price_usd=float(data.get("avg_price_usd") or 0.0),
        opened_at=int(data.get("opened_at") or int(time.time())),
        mode=str(data.get("mode") or "paper"),
        source=str(data.get("source") or "memecoin"),
        side=str(data.get("side") or "long"),
        score=float(data.get("score") or 0.0),
        reason=str(data.get("reason") or ""),
        entry_features=dict(data.get("entry_features") or {}),
    )


def _trade_from_dict(data: dict[str, Any]) -> Trade:
    return Trade(
        ts=int(data.get("ts") or int(time.time())),
        side=str(data.get("side") or ""),
        symbol=str(data.get("symbol") or ""),
        token_address=str(data.get("token_address") or ""),
        qty=float(data.get("qty") or 0.0),
        price_usd=float(data.get("price_usd") or 0.0),
        notional_usd=float(data.get("notional_usd") or 0.0),
        pnl_usd=float(data.get("pnl_usd") or 0.0),
        pnl_pct=float(data.get("pnl_pct") or 0.0),
        reason=str(data.get("reason") or ""),
        mode=str(data.get("mode") or "paper"),
        source=str(data.get("source") or "memecoin"),
    )


def state_from_dict(raw: dict[str, Any], start_cash_usd: float) -> EngineState:
    if not isinstance(raw, dict):
        return EngineState(cash_usd=float(start_cash_usd))

    positions_raw = raw.get("positions") or {}
    positions: dict[str, Position] = {}
    if isinstance(positions_raw, dict):
        for token_address, row in positions_raw.items():
            if not isinstance(row, dict):
                continue
            pos = _position_from_dict({"token_address": token_address, **row})
            if pos.token_address:
                positions[pos.token_address] = pos

    trades_raw = raw.get("trades") or []
    trades: list[Trade] = []
    if isinstance(trades_raw, list):
        for row in trades_raw:
            if isinstance(row, dict):
                trades.append(_trade_from_dict(row))

    return EngineState(
        cash_usd=float(raw.get("cash_usd") or start_cash_usd),
        positions=positions,
        trades=trades,
        last_signal_ts={str(k): float(v) for k, v in dict(raw.get("last_signal_ts") or {}).items()},
        latest_signals=list(raw.get("latest_signals") or []),
        alerts=list(raw.get("alerts") or []),
        trend_events=list(raw.get("trend_events") or []),
        wallet_assets=list(raw.get("wallet_assets") or []),
        bybit_assets=list(raw.get("bybit_assets") or []),
        bybit_positions=list(raw.get("bybit_positions") or []),
        bybit_error=str(raw.get("bybit_error") or ""),
        memecoin_error=str(raw.get("memecoin_error") or ""),
        last_cycle_ts=int(raw.get("last_cycle_ts") or 0),
        last_wallet_sync_ts=int(raw.get("last_wallet_sync_ts") or 0),
        last_bybit_sync_ts=int(raw.get("last_bybit_sync_ts") or 0),
        telegram_offset=int(raw.get("telegram_offset") or 0),
        demo_seed_usdt=float(raw.get("demo_seed_usdt") or 1000.0),
        live_seed_usd=float(raw.get("live_seed_usd") or 0.0),
        live_seed_set_ts=int(raw.get("live_seed_set_ts") or 0),
        live_perf_anchor_usd=float(raw.get("live_perf_anchor_usd") or 0.0),
        live_perf_anchor_ts=int(raw.get("live_perf_anchor_ts") or 0),
        live_net_flow_usd=float(raw.get("live_net_flow_usd") or 0.0),
        model_runs=dict(raw.get("model_runs") or {}),
        daily_pnl=list(raw.get("daily_pnl") or []),
    )


def state_to_dict(state: EngineState) -> dict[str, Any]:
    return {
        "cash_usd": float(state.cash_usd),
        "positions": {token: asdict(pos) for token, pos in state.positions.items()},
        "trades": [asdict(trade) for trade in state.trades[-STATE_TRADE_HISTORY_LIMIT:]],
        "last_signal_ts": dict(state.last_signal_ts),
        "latest_signals": list(state.latest_signals[-STATE_SIGNAL_HISTORY_LIMIT:]),
        "alerts": list(state.alerts[-STATE_ALERT_HISTORY_LIMIT:]),
        "trend_events": list(state.trend_events[-STATE_TREND_HISTORY_LIMIT:]),
        "wallet_assets": list(state.wallet_assets[-STATE_WALLET_HISTORY_LIMIT:]),
        "bybit_assets": list(state.bybit_assets[-STATE_ASSET_HISTORY_LIMIT:]),
        "bybit_positions": list(state.bybit_positions[-STATE_ASSET_HISTORY_LIMIT:]),
        "bybit_error": str(state.bybit_error or ""),
        "memecoin_error": str(state.memecoin_error or ""),
        "last_cycle_ts": int(state.last_cycle_ts),
        "last_wallet_sync_ts": int(state.last_wallet_sync_ts),
        "last_bybit_sync_ts": int(state.last_bybit_sync_ts),
        "telegram_offset": int(state.telegram_offset),
        "demo_seed_usdt": float(state.demo_seed_usdt),
        "live_seed_usd": float(state.live_seed_usd),
        "live_seed_set_ts": int(state.live_seed_set_ts),
        "live_perf_anchor_usd": float(state.live_perf_anchor_usd),
        "live_perf_anchor_ts": int(state.live_perf_anchor_ts),
        "live_net_flow_usd": float(state.live_net_flow_usd),
        "model_runs": dict(state.model_runs or {}),
        "daily_pnl": list(state.daily_pnl[-STATE_DAILY_PNL_HISTORY_LIMIT:]),
    }


def load_state(path: str, start_cash_usd: float) -> EngineState:
    target = Path(path)
    if not target.exists():
        return EngineState(cash_usd=float(start_cash_usd))
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return EngineState(cash_usd=float(start_cash_usd))
    return state_from_dict(raw, start_cash_usd)


def save_state(path: str, state: EngineState) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = state_to_dict(state)
    serialized = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    tmp = target.with_suffix(f"{target.suffix}.tmp")
    try:
        tmp.write_text(serialized, encoding="utf-8")
        tmp.replace(target)
    except Exception:
        target.write_text(serialized, encoding="utf-8")
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
