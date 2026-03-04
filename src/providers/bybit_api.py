from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any
from urllib.parse import urlencode

import requests


class BybitV5Client:
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        base_url: str = "https://api.bybit.com",
        recv_window: int = 5000,
        timeout_seconds: int = 10,
    ) -> None:
        self.api_key = str(api_key or "").strip()
        self.api_secret = str(api_secret or "").strip()
        self.base_url = str(base_url or "").rstrip("/")
        self.recv_window = int(recv_window)
        self.timeout_seconds = timeout_seconds
        self.session = requests.Session()

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.api_secret)

    def _headers(self, timestamp: str, sign: str) -> dict[str, str]:
        return {
            "X-BAPI-API-KEY": self.api_key,
            "X-BAPI-SIGN": sign,
            "X-BAPI-SIGN-TYPE": "2",
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-RECV-WINDOW": str(self.recv_window),
            "Content-Type": "application/json",
        }

    def _sign(self, timestamp: str, payload: str) -> str:
        body = f"{timestamp}{self.api_key}{self.recv_window}{payload}"
        return hmac.new(
            self.api_secret.encode("utf-8"),
            body.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _request(self, method: str, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.enabled:
            raise RuntimeError("bybit_disabled")
        params = dict(params or {})
        timestamp = str(int(time.time() * 1000))

        method_upper = method.upper()
        if method_upper == "GET":
            payload = urlencode(sorted(params.items()), doseq=True)
            sign = self._sign(timestamp, payload)
            headers = self._headers(timestamp, sign)
            url = f"{self.base_url}{path}"
            res = self.session.get(url, params=params, headers=headers, timeout=self.timeout_seconds)
        else:
            payload = json.dumps(params, separators=(",", ":"), ensure_ascii=True)
            sign = self._sign(timestamp, payload)
            headers = self._headers(timestamp, sign)
            url = f"{self.base_url}{path}"
            res = self.session.post(url, data=payload, headers=headers, timeout=self.timeout_seconds)

        res.raise_for_status()
        body = res.json()
        ret_code_raw = body.get("retCode")
        ret_code = -1 if ret_code_raw is None else int(ret_code_raw)
        if ret_code != 0:
            raise RuntimeError(
                f'Bybit error {body.get("retCode")}: {body.get("retMsg")} '
                f"(path={path}, params={params})"
            )
        result = body.get("result")
        return result if isinstance(result, dict) else {}

    def get_wallet_assets(self, account_type: str = "UNIFIED") -> list[dict[str, Any]]:
        result = self._request(
            "GET",
            "/v5/account/wallet-balance",
            {"accountType": account_type},
        )
        rows = result.get("list") or []
        out: list[dict[str, Any]] = []
        for item in rows:
            coins = item.get("coin") or []
            for coin in coins:
                coin_name = str(coin.get("coin") or "").upper().strip()
                equity = float(coin.get("equity") or 0.0)
                usd_value = float(coin.get("usdValue") or 0.0)
                if equity <= 0 and usd_value <= 0:
                    continue
                out.append(
                    {
                        "coin": coin_name,
                        "equity": equity,
                        "usd_value": usd_value,
                        "wallet_balance": float(coin.get("walletBalance") or 0.0),
                        "available_to_withdraw": float(coin.get("availableToWithdraw") or 0.0),
                    }
                )
        out.sort(key=lambda r: float(r.get("usd_value") or 0.0), reverse=True)
        return out

    def get_positions(
        self,
        category: str = "linear",
        settle_coin: str = "USDT",
    ) -> list[dict[str, Any]]:
        result = self._request(
            "GET",
            "/v5/position/list",
            {"category": category, "settleCoin": settle_coin},
        )
        rows = result.get("list") or []
        out: list[dict[str, Any]] = []
        for row in rows:
            size = float(row.get("size") or 0.0)
            if size <= 0:
                continue
            out.append(
                {
                    "symbol": str(row.get("symbol") or ""),
                    "side": str(row.get("side") or ""),
                    "size": size,
                    "avg_price": float(row.get("avgPrice") or 0.0),
                    "mark_price": float(row.get("markPrice") or 0.0),
                    "position_value": float(row.get("positionValue") or 0.0),
                    "unrealised_pnl": float(row.get("unrealisedPnl") or 0.0),
                    "leverage": str(row.get("leverage") or ""),
                }
            )
        out.sort(key=lambda r: float(r.get("position_value") or 0.0), reverse=True)
        return out

    def place_market_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        category: str = "linear",
        reduce_only: bool = False,
    ) -> dict[str, Any]:
        payload = {
            "category": category,
            "symbol": symbol,
            "side": side,
            "orderType": "Market",
            "qty": str(qty),
            "timeInForce": "IOC",
            "reduceOnly": bool(reduce_only),
        }
        return self._request("POST", "/v5/order/create", payload)

    def get_last_price(self, symbol: str, category: str = "linear") -> float:
        url = f"{self.base_url}/v5/market/tickers"
        res = self.session.get(
            url,
            params={"category": category, "symbol": symbol},
            timeout=self.timeout_seconds,
        )
        res.raise_for_status()
        body = res.json()
        if int(body.get("retCode") or -1) != 0:
            raise RuntimeError(f'Bybit ticker error {body.get("retCode")}: {body.get("retMsg")}')
        rows = ((body.get("result") or {}).get("list") or [])
        if not rows:
            return 0.0
        return float(rows[0].get("lastPrice") or 0.0)
