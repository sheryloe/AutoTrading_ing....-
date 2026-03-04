from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _sigmoid(x: float) -> float:
    if x >= 35:
        return 1.0
    if x <= -35:
        return 0.0
    return 1.0 / (1.0 + math.exp(-x))


@dataclass
class OnlineModel:
    bias: float = 0.0
    lr: float = 0.04
    l2: float = 0.0008
    n_updates: int = 0
    updated_at: int = field(default_factory=lambda: int(time.time()))
    weights: dict[str, float] = field(
        default_factory=lambda: {
            "trend_strength": 1.20,
            "trader_strength": 0.95,
            "wallet_strength": 0.65,
            "buy_sell_ratio": 0.70,
            "liq_log": 0.42,
            "vol_log": 0.45,
            "age_freshness": 0.55,
            "age_stability": 0.35,
            "tx_flow": 0.50,
            "new_meme_quality": 0.75,
            "new_meme_instant": 0.80,
            "spread_proxy": -0.25,
            "noise_penalty": -0.35,
        }
    )

    def linear_score(self, features: dict[str, float]) -> float:
        s = float(self.bias)
        for key, value in features.items():
            s += float(self.weights.get(key, 0.0)) * float(value)
        return s

    def predict_proba(self, features: dict[str, float]) -> float:
        return _sigmoid(self.linear_score(features))

    def update(self, features: dict[str, float], pnl_pct: float) -> None:
        target = 1.0 if float(pnl_pct) > 0 else 0.0
        pred = self.predict_proba(features)
        err = pred - target
        for key, value in features.items():
            w = float(self.weights.get(key, 0.0))
            grad = (err * float(value)) + (self.l2 * w)
            self.weights[key] = w - (self.lr * grad)
        self.bias -= self.lr * err
        self.n_updates += 1
        self.updated_at = int(time.time())

    def to_dict(self) -> dict[str, Any]:
        return {
            "bias": float(self.bias),
            "lr": float(self.lr),
            "l2": float(self.l2),
            "n_updates": int(self.n_updates),
            "updated_at": int(self.updated_at),
            "weights": {str(k): float(v) for k, v in self.weights.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OnlineModel":
        model = cls()
        model.bias = float(data.get("bias") or model.bias)
        model.lr = max(0.0001, float(data.get("lr") or model.lr))
        model.l2 = max(0.0, float(data.get("l2") or model.l2))
        model.n_updates = int(data.get("n_updates") or 0)
        model.updated_at = int(data.get("updated_at") or int(time.time()))
        weights = data.get("weights")
        if isinstance(weights, dict):
            for k, v in weights.items():
                try:
                    model.weights[str(k)] = float(v)
                except Exception:
                    continue
        return model


def load_online_model(path: str) -> OnlineModel:
    target = Path(path)
    if not target.exists():
        return OnlineModel()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return OnlineModel()
    if not isinstance(raw, dict):
        return OnlineModel()
    return OnlineModel.from_dict(raw)


def save_online_model(path: str, model: OnlineModel) -> None:
    target = Path(path)
    target.write_text(json.dumps(model.to_dict(), ensure_ascii=True, indent=2), encoding="utf-8")

