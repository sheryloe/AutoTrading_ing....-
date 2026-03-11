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

    def get_sol_balance_raw(self, wallet_address: str) -> int:
        result = self._rpc("getBalance", [wallet_address])
        return int((result or {}).get("value") or 0)

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

    def get_token_balance_raw(self, wallet_address: str, mint_address: str) -> dict[str, Any]:
        mint = str(mint_address or "").strip()
        if not wallet_address or not mint:
            return {"raw_amount": 0, "decimals": 0, "qty": 0.0}
        total_raw = 0
        decimals = 0
        for row in self.get_token_accounts(wallet_address):
            try:
                parsed = (((row or {}).get("account") or {}).get("data") or {}).get("parsed") or {}
                info = (parsed.get("info") or {}) if isinstance(parsed, dict) else {}
                token_mint = str(info.get("mint") or "").strip()
                if token_mint != mint:
                    continue
                token_amount = dict(info.get("tokenAmount") or {})
                raw = int(token_amount.get("amount") or 0)
                dec = int(token_amount.get("decimals") or 0)
                total_raw += max(0, raw)
                decimals = max(decimals, dec)
            except Exception:
                continue
        qty = float(total_raw) / float(10**max(0, decimals)) if total_raw > 0 else 0.0
        return {"raw_amount": int(total_raw), "decimals": int(decimals), "qty": float(qty)}

    def get_wallet_snapshot(self, wallet_address: str, mint_address: str = "") -> dict[str, Any]:
        snap = {
            "wallet_address": str(wallet_address or "").strip(),
            "token_address": str(mint_address or "").strip(),
            "sol_lamports": 0,
            "sol_qty": 0.0,
            "token_raw_amount": 0,
            "token_qty": 0.0,
            "token_decimals": 0,
        }
        if not wallet_address:
            return snap
        try:
            sol_lamports = self.get_sol_balance_raw(wallet_address)
            snap["sol_lamports"] = int(sol_lamports)
            snap["sol_qty"] = float(sol_lamports) / 1_000_000_000.0
        except Exception:
            pass
        mint = str(mint_address or "").strip()
        if mint:
            try:
                token_row = self.get_token_balance_raw(wallet_address, mint)
                snap["token_raw_amount"] = int(token_row.get("raw_amount") or 0)
                snap["token_qty"] = float(token_row.get("qty") or 0.0)
                snap["token_decimals"] = int(token_row.get("decimals") or 0)
            except Exception:
                pass
        return snap

    def get_signatures_for_address(
        self,
        address: str,
        limit: int = 20,
        before: str = "",
        until: str = "",
    ) -> list[dict[str, Any]]:
        wallet = str(address or "").strip()
        if not wallet:
            return []
        params: dict[str, Any] = {"limit": max(1, min(100, int(limit or 20)))}
        if str(before or "").strip():
            params["before"] = str(before).strip()
        if str(until or "").strip():
            params["until"] = str(until).strip()
        result = self._rpc("getSignaturesForAddress", [wallet, params])
        rows = result or []
        return list(rows) if isinstance(rows, list) else []

    def get_transaction(self, signature: str) -> dict[str, Any]:
        sig = str(signature or "").strip()
        if not sig:
            return {}
        result = self._rpc(
            "getTransaction",
            [
                sig,
                {
                    "encoding": "jsonParsed",
                    "commitment": "confirmed",
                    "maxSupportedTransactionVersion": 0,
                },
            ],
        )
        return dict(result or {}) if isinstance(result, dict) else {}

    @staticmethod
    def _token_amount_raw(token_amount: dict[str, Any]) -> tuple[int, int, float]:
        raw = int(token_amount.get("amount") or 0)
        decimals = int(token_amount.get("decimals") or 0)
        qty = float(raw) / float(10**max(0, decimals)) if raw > 0 else 0.0
        return raw, decimals, qty

    def get_wallet_transaction_deltas(
        self,
        signature: str,
        wallet_address: str,
        tracked_mints: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        wallet = str(wallet_address or "").strip()
        if not wallet:
            return {}
        tx = self.get_transaction(signature)
        if not tx:
            return {}
        meta = dict(tx.get("meta") or {})
        transaction = dict(tx.get("transaction") or {})
        message = dict(transaction.get("message") or {})
        account_keys = list(message.get("accountKeys") or [])
        keys: list[str] = []
        for row in account_keys:
            if isinstance(row, dict):
                keys.append(str(row.get("pubkey") or "").strip())
            else:
                keys.append(str(row or "").strip())
        wallet_index = -1
        for idx, key in enumerate(keys):
            if key == wallet:
                wallet_index = idx
                break
        pre_balances = list(meta.get("preBalances") or [])
        post_balances = list(meta.get("postBalances") or [])
        wallet_pre_lamports = 0
        wallet_post_lamports = 0
        if 0 <= wallet_index < len(pre_balances):
            wallet_pre_lamports = int(pre_balances[wallet_index] or 0)
        if 0 <= wallet_index < len(post_balances):
            wallet_post_lamports = int(post_balances[wallet_index] or 0)
        tracked = {str(m).strip() for m in list(tracked_mints or []) if str(m or "").strip()}
        token_map: dict[str, dict[str, Any]] = {}
        for phase, rows in (("pre", meta.get("preTokenBalances") or []), ("post", meta.get("postTokenBalances") or [])):
            for row in list(rows or []):
                if not isinstance(row, dict):
                    continue
                owner = str(row.get("owner") or "").strip()
                mint = str(row.get("mint") or "").strip()
                if owner != wallet or not mint:
                    continue
                if tracked and mint not in tracked:
                    continue
                amt_raw, decimals, qty = self._token_amount_raw(dict(row.get("uiTokenAmount") or {}))
                rec = token_map.setdefault(
                    mint,
                    {
                        "mint": mint,
                        "decimals": int(decimals),
                        "pre_raw": 0,
                        "post_raw": 0,
                        "pre_qty": 0.0,
                        "post_qty": 0.0,
                    },
                )
                rec["decimals"] = max(int(rec.get("decimals") or 0), int(decimals))
                rec[f"{phase}_raw"] = int(amt_raw)
                rec[f"{phase}_qty"] = float(qty)
        for rec in token_map.values():
            rec["delta_raw"] = int(rec.get("post_raw") or 0) - int(rec.get("pre_raw") or 0)
            rec["delta_qty"] = float(rec.get("post_qty") or 0.0) - float(rec.get("pre_qty") or 0.0)
        fee_lamports = int(meta.get("fee") or 0)
        return {
            "signature": str(signature or "").strip(),
            "slot": int(tx.get("slot") or 0),
            "block_time": int(tx.get("blockTime") or 0),
            "fee_lamports": int(fee_lamports),
            "wallet_pre_sol_lamports": int(wallet_pre_lamports),
            "wallet_post_sol_lamports": int(wallet_post_lamports),
            "net_sol_change_lamports": int(wallet_post_lamports - wallet_pre_lamports),
            "gross_sol_change_lamports": int(wallet_post_lamports - wallet_pre_lamports + fee_lamports),
            "token_deltas": token_map,
            "meta_err": meta.get("err"),
        }

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
        include_token_addresses: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        if not wallet_address:
            return []

        out: list[dict[str, Any]] = []
        min_usd = max(0.0, float(min_asset_usd))
        include_set: set[str] = set()
        for raw in list(include_token_addresses or []):
            token = str(raw or "").strip()
            if token:
                include_set.add(token)

        try:
            sol_result = self._rpc("getBalance", [wallet_address])
            sol_lamports = int((sol_result or {}).get("value") or 0)
            sol_qty = float(sol_lamports) / 1_000_000_000.0
            sol_price = self._get_sol_price_usd()
            sol_value = sol_qty * sol_price
            if sol_value >= min_usd:
                out.append(
                    {
                        "symbol": "SOL",
                        "name": "Solana",
                        "token_address": "So11111111111111111111111111111111111111112",
                        "qty": sol_qty,
                        "raw_amount": int(sol_lamports),
                        "decimals": 9,
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

        aggregated: dict[str, dict[str, Any]] = {}
        for row in token_accounts:
            try:
                parsed = (((row or {}).get("account") or {}).get("data") or {}).get("parsed") or {}
                info = (parsed.get("info") or {}) if isinstance(parsed, dict) else {}
                mint = str(info.get("mint") or "").strip()
                token_amount = dict(info.get("tokenAmount") or {})
                amount_raw = int(token_amount.get("amount") or 0)
                decimals = int(token_amount.get("decimals") or 0)
                amount_ui_val = token_amount.get("uiAmount")
                if amount_ui_val is None:
                    amount_ui = float(amount_raw) / float(10**max(0, decimals))
                else:
                    amount_ui = float(amount_ui_val or 0.0)
                if not mint or amount_ui <= 0:
                    continue
                rec = aggregated.setdefault(
                    mint,
                    {
                        "token_address": mint,
                        "qty": 0.0,
                        "raw_amount": 0,
                        "decimals": int(decimals),
                    },
                )
                rec["qty"] = float(rec.get("qty") or 0.0) + float(amount_ui)
                rec["raw_amount"] = int(rec.get("raw_amount") or 0) + int(amount_raw)
                rec["decimals"] = max(int(rec.get("decimals") or 0), int(decimals))
            except Exception:
                continue

        snapshot_cache: dict[str, dict[str, Any]] = {}
        for mint, rec in aggregated.items():
            try:
                snap = snapshot_cache.get(mint)
                if snap is None:
                    s = dex.fetch_snapshot_for_token("solana", mint)
                    snap = {
                        "symbol": s.symbol if s else mint[:6],
                        "name": s.name if s else mint[:10],
                        "price_usd": float(s.price_usd if s else 0.0),
                    }
                    snapshot_cache[mint] = snap
                qty = float(rec.get("qty") or 0.0)
                value_usd = qty * float(snap["price_usd"])
                force_include = mint in include_set
                if value_usd < min_usd and not force_include:
                    continue
                out.append(
                    {
                        "symbol": str(snap["symbol"]).upper(),
                        "name": str(snap["name"]),
                        "token_address": mint,
                        "qty": float(qty),
                        "raw_amount": int(rec.get("raw_amount") or 0),
                        "decimals": int(rec.get("decimals") or 0),
                        "price_usd": float(snap["price_usd"]),
                        "value_usd": float(value_usd),
                    }
                )
            except Exception:
                continue

        out.sort(key=lambda row: float(row.get("value_usd") or 0.0), reverse=True)
        return out
