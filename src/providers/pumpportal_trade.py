from __future__ import annotations

import base64
import json
import time
from typing import Any

import requests
from solders.keypair import Keypair
from solders.message import to_bytes_versioned
from solders.transaction import VersionedTransaction


class PumpPortalLocalTrader:
    def __init__(
        self,
        rpc_url: str,
        private_key: str,
        wallet_address: str = "",
        timeout_seconds: int = 20,
        base_url: str = "https://pumpportal.fun/api",
    ) -> None:
        self.rpc_url = str(rpc_url or "").strip()
        self.private_key = str(private_key or "").strip()
        self.wallet_address = str(wallet_address or "").strip()
        self.timeout_seconds = max(5, int(timeout_seconds))
        self.base_url = str(base_url or "https://pumpportal.fun/api").rstrip("/")
        self.session = requests.Session()
        self._keypair: Keypair | None = None
        self._init_error: str = ""
        self._load_keypair()

    @property
    def init_error(self) -> str:
        return str(self._init_error or "")

    @property
    def enabled(self) -> bool:
        return bool(self.rpc_url and self._keypair and self.wallet_address)

    def _load_keypair(self) -> None:
        self._keypair = None
        self._init_error = ""
        if not self.private_key:
            self._init_error = "SOLANA_PRIVATE_KEY not configured"
            return
        try:
            keypair = Keypair.from_base58_string(self.private_key)
        except Exception:
            try:
                raw_obj = json.loads(self.private_key)
                if not isinstance(raw_obj, list) or not raw_obj:
                    raise ValueError("invalid json key format")
                raw = bytes(int(x) & 0xFF for x in raw_obj)
                if len(raw) == 64:
                    keypair = Keypair.from_bytes(raw)
                elif len(raw) == 32:
                    keypair = Keypair.from_seed(raw)
                else:
                    raise ValueError(f"unsupported key length={len(raw)}")
            except Exception as exc:
                self._init_error = f"invalid_solana_private_key:{exc}"
                return
        derived = str(keypair.pubkey())
        if self.wallet_address and self.wallet_address != derived:
            self._init_error = "private_key_wallet_mismatch"
            return
        self._keypair = keypair
        self.wallet_address = derived

    def _rpc(self, method: str, params: list[Any]) -> Any:
        if not self.rpc_url:
            raise RuntimeError("solana_rpc_url_missing")
        payload = {"jsonrpc": "2.0", "id": 1, "method": str(method), "params": params}
        res = self.session.post(self.rpc_url, json=payload, timeout=self.timeout_seconds)
        res.raise_for_status()
        body = res.json()
        if isinstance(body, dict) and body.get("error"):
            raise RuntimeError(str(body.get("error")))
        return body.get("result") if isinstance(body, dict) else None

    def _send_signed_transaction(self, raw_tx_bytes: bytes) -> str:
        if not self._keypair:
            raise RuntimeError(self._init_error or "solana_signer_not_ready")
        try:
            raw_tx = VersionedTransaction.from_bytes(raw_tx_bytes)
            sig = self._keypair.sign_message(to_bytes_versioned(raw_tx.message))
            signed_tx = VersionedTransaction.populate(raw_tx.message, [sig])
            signed_tx_b64 = base64.b64encode(bytes(signed_tx)).decode("ascii")
        except Exception as exc:
            raise RuntimeError(f"sign_failed:{exc}") from exc
        result = self._rpc(
            "sendTransaction",
            [
                signed_tx_b64,
                {
                    "encoding": "base64",
                    "skipPreflight": False,
                    "preflightCommitment": "processed",
                    "maxRetries": 3,
                },
            ],
        )
        sig_text = str(result or "").strip()
        if not sig_text:
            raise RuntimeError("send_transaction_empty_signature")
        return sig_text

    def _wait_confirmation(self, signature: str, timeout_seconds: int = 45) -> None:
        until = time.time() + max(10, int(timeout_seconds))
        while time.time() < until:
            result = self._rpc(
                "getSignatureStatuses",
                [
                    [signature],
                    {"searchTransactionHistory": True},
                ],
            )
            rows = (result or {}).get("value") or []
            row = rows[0] if isinstance(rows, list) and rows else None
            if isinstance(row, dict):
                if row.get("err") is not None:
                    raise RuntimeError(f"signature_failed:{row.get('err')}")
                status = str(row.get("confirmationStatus") or "")
                if status in {"confirmed", "finalized"}:
                    return
            time.sleep(1.25)

    def trade_local(
        self,
        *,
        action: str,
        mint: str,
        amount: str | float,
        denominated_in_sol: bool,
        slippage_pct: float = 15.0,
        priority_fee_sol: float = 0.001,
        pool: str = "auto",
    ) -> dict[str, Any]:
        if not self.enabled:
            raise RuntimeError(self._init_error or "pumpportal_trader_not_enabled")
        payload = {
            "publicKey": self.wallet_address,
            "action": str(action or "").strip().lower(),
            "mint": str(mint or "").strip(),
            "amount": str(amount),
            "denominatedInSol": "true" if bool(denominated_in_sol) else "false",
            "slippage": str(max(1.0, float(slippage_pct))),
            "priorityFee": str(max(0.0, float(priority_fee_sol))),
            "pool": str(pool or "auto").strip().lower(),
        }
        res = self.session.post(
            f"{self.base_url}/trade-local",
            data=payload,
            timeout=max(self.timeout_seconds, 30),
        )
        if res.status_code >= 400:
            raise RuntimeError(f"pumpportal_http_{res.status_code}: {res.text[:220]}")
        raw_bytes = bytes(res.content or b"")
        if not raw_bytes:
            raise RuntimeError("pumpportal_empty_transaction")
        signature = self._send_signed_transaction(raw_bytes)
        self._wait_confirmation(signature)
        return {
            "signature": signature,
            "action": str(action or "").strip().lower(),
            "mint": str(mint or "").strip(),
            "amount": str(amount),
            "denominated_in_sol": bool(denominated_in_sol),
            "slippage_pct": float(slippage_pct),
            "priority_fee_sol": float(priority_fee_sol),
            "pool": str(pool or "auto").strip().lower(),
        }

    def buy_token_with_sol(
        self,
        mint: str,
        amount_sol: float,
        *,
        slippage_pct: float = 15.0,
        priority_fee_sol: float = 0.001,
        pool: str = "auto",
    ) -> dict[str, Any]:
        amount = max(0.0, float(amount_sol))
        if amount <= 0.0:
            raise RuntimeError("amount_sol_too_small")
        return self.trade_local(
            action="buy",
            mint=mint,
            amount=f"{amount:.9f}".rstrip("0").rstrip("."),
            denominated_in_sol=True,
            slippage_pct=slippage_pct,
            priority_fee_sol=priority_fee_sol,
            pool=pool,
        )

    def sell_token_to_sol(
        self,
        mint: str,
        amount: str,
        *,
        slippage_pct: float = 15.0,
        priority_fee_sol: float = 0.001,
        pool: str = "auto",
    ) -> dict[str, Any]:
        return self.trade_local(
            action="sell",
            mint=mint,
            amount=str(amount),
            denominated_in_sol=False,
            slippage_pct=slippage_pct,
            priority_fee_sol=priority_fee_sol,
            pool=pool,
        )
