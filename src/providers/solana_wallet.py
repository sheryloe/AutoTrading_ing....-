from __future__ import annotations

import time
from typing import Any

import requests

from src.data_sources import DexScreenerClient


TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM_ID = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"


class SolanaWalletTracker:
    def __init__(self, rpc_url: str, timeout_seconds: int = 10) -> None:
        self.rpc_url = str(rpc_url or "").strip()
        self.timeout_seconds = timeout_seconds
        self.session = requests.Session()
        self._sol_price_cache: tuple[float, float] = (0.0, 0.0)

    @property
    def enabled(self) -> bool:
        return bool(self.rpc_url)

    def _rpc(self, method: str, params: list[Any]) -> Any:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": str(method),
            "params": params,
        }
        res = self.session.post(self.rpc_url, json=payload, timeout=self.timeout_seconds)
        res.raise_for_status()
        body = res.json()
        if isinstance(body, dict) and body.get("error"):
            raise RuntimeError(str(body.get("error")))
        return body.get("result") if isinstance(body, dict) else None

    def get_sol_balance(self, wallet_address: str) -> float:
        result = self._rpc("getBalance", [wallet_address])
        lamports = float((result or {}).get("value") or 0.0)
        return lamports / 1_000_000_000.0

    def get_token_accounts(self, wallet_address: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for program_id in (TOKEN_PROGRAM_ID, TOKEN_2022_PROGRAM_ID):
            try:
                result = self._rpc(
                    "getTokenAccountsByOwner",
                    [
                        wallet_address,
                        {"programId": program_id},
                        {"encoding": "jsonParsed"},
                    ],
                )
                rows = (result or {}).get("value") or []
                if isinstance(rows, list):
                    out.extend(rows)
            except Exception:
                continue
        return out

    def _get_sol_price_usd(self) -> float:
        now = time.time()
        cached_price, cached_ts = self._sol_price_cache
        if cached_price > 0 and (now - cached_ts) < 90:
            return cached_price
        try:
            res = self.session.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": "solana", "vs_currencies": "usd"},
                timeout=self.timeout_seconds,
            )
            res.raise_for_status()
            price = float((res.json() or {}).get("solana", {}).get("usd") or 0.0)
        except Exception:
            price = cached_price
        if price > 0:
            self._sol_price_cache = (price, now)
        return price

    def fetch_wallet_assets(
        self,
        wallet_address: str,
        dex: DexScreenerClient,
        min_asset_usd: float = 1.0,
    ) -> list[dict[str, Any]]:
        if not wallet_address:
            return []

        out: list[dict[str, Any]] = []
        min_usd = max(0.0, float(min_asset_usd))

        try:
            sol_qty = self.get_sol_balance(wallet_address)
            sol_price = self._get_sol_price_usd()
            sol_value = sol_qty * sol_price
            if sol_value >= min_usd:
                out.append(
                    {
                        "symbol": "SOL",
                        "name": "Solana",
                        "token_address": "So11111111111111111111111111111111111111112",
                        "qty": sol_qty,
                        "price_usd": sol_price,
                        "value_usd": sol_value,
                    }
                )
        except Exception:
            pass

        try:
            token_accounts = self.get_token_accounts(wallet_address)
        except Exception:
            token_accounts = []

        snapshot_cache: dict[str, dict[str, Any]] = {}
        for row in token_accounts:
            try:
                parsed = (((row or {}).get("account") or {}).get("data") or {}).get("parsed") or {}
                info = (parsed.get("info") or {}) if isinstance(parsed, dict) else {}
                mint = str(info.get("mint") or "").strip()
                amount_ui = float((((info.get("tokenAmount") or {}).get("uiAmount")) or 0.0))
                if not mint or amount_ui <= 0:
                    continue

                snap = snapshot_cache.get(mint)
                if snap is None:
                    s = dex.fetch_snapshot_for_token("solana", mint)
                    snap = {
                        "symbol": s.symbol if s else mint[:6],
                        "name": s.name if s else mint[:10],
                        "price_usd": float(s.price_usd if s else 0.0),
                    }
                    snapshot_cache[mint] = snap
                value_usd = amount_ui * float(snap["price_usd"])
                if value_usd < min_usd:
                    continue
                out.append(
                    {
                        "symbol": str(snap["symbol"]).upper(),
                        "name": str(snap["name"]),
                        "token_address": mint,
                        "qty": amount_ui,
                        "price_usd": float(snap["price_usd"]),
                        "value_usd": value_usd,
                    }
                )
            except Exception:
                continue

        out.sort(key=lambda row: float(row.get("value_usd") or 0.0), reverse=True)
        return out
