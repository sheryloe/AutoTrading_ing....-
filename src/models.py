from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TokenSnapshot:
    token_address: str
    symbol: str
    name: str
    pair_url: str
    price_usd: float
    liquidity_usd: float
    volume_5m_usd: float
    buys_5m: int
    sells_5m: int
    age_minutes: float
    source: str

    @property
    def buy_sell_ratio(self) -> float:
        return float(self.buys_5m) / float(max(1, self.sells_5m))


@dataclass(frozen=True)
class BuySignal:
    token: TokenSnapshot
    score: float
    probability: float
    reason: str


@dataclass(frozen=True)
class TrendEvent:
    source: str
    symbol: str
    text: str
    ts: int

