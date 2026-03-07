from __future__ import annotations

import base64
import json
import time
from typing import Any

import requests
from solders.keypair import Keypair
from solders.message import to_bytes_versioned
from solders.transaction import VersionedTransaction


SOL_MINT = "So11111111111111111111111111111111111111112"


class JupiterSolanaTrader:
    def __init__(
        self,
        rpc_url: str,
        private_key: str,
        wallet_address: str = "",
        timeout_seconds: int = 20,
        jupiter_base_url: str = "https://lite-api.jup.ag/swap/v1",
    ) -> None:
        self.rpc_url = str(rpc_url or "").strip()
        self.private_key = str(private_key or "").strip()
        self.wallet_address = str(wallet_address or "").strip()
        self.timeout_seconds = max(5, int(timeout_seconds))
        self.jupiter_base_url = str(jupiter_base_url or "https://lite-api.jup.ag/swap/v1").rstrip("/")
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

    def _http_get_json(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.jupiter_base_url}/{path.lstrip('/')}"
        res = self.session.get(url, params=params, timeout=self.timeout_seconds)
        if res.status_code >= 400:
            raise RuntimeError(f"jupiter_quote_http_{res.status_code}: {res.text[:220]}")
        body = res.json()
        if not isinstance(body, dict):
            raise RuntimeError("jupiter_quote_invalid_response")
        if body.get("error"):
            raise RuntimeError(str(body.get("error")))
        return body

    def _http_post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.jupiter_base_url}/{path.lstrip('/')}"
        res = self.session.post(url, json=payload, timeout=max(self.timeout_seconds, 30))
        if res.status_code >= 400:
            raise RuntimeError(f"jupiter_swap_http_{res.status_code}: {res.text[:220]}")
        body = res.json()
        if not isinstance(body, dict):
            raise RuntimeError("jupiter_swap_invalid_response")
        if body.get("error"):
            raise RuntimeError(str(body.get("error")))
        return body

    def _send_signed_transaction(self, swap_tx_b64: str) -> str:
        if not self._keypair:
            raise RuntimeError(self._init_error or "solana_signer_not_ready")
        try:
            raw_tx = VersionedTransaction.from_bytes(base64.b64decode(str(swap_tx_b64)))
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
            time.sleep(1.5)

    def _build_quote(
        self,
        input_mint: str,
        output_mint: str,
        amount_raw: int,
        slippage_bps: int,
    ) -> dict[str, Any]:
        if amount_raw <= 0:
            raise RuntimeError("amount_raw_must_be_positive")
        params = {
            "inputMint": str(input_mint),
            "outputMint": str(output_mint),
            "amount": str(int(amount_raw)),
            "slippageBps": str(max(10, int(slippage_bps))),
            "swapMode": "ExactIn",
        }
        return self._http_get_json("quote", params)

    def swap_exact_in(
        self,
        input_mint: str,
        output_mint: str,
        amount_raw: int,
        slippage_bps: int = 300,
    ) -> dict[str, Any]:
        if not self.enabled:
            raise RuntimeError(self._init_error or "solana_trader_not_enabled")
        slippage_candidates = [max(10, int(slippage_bps))]
        if slippage_candidates[0] < 600:
            slippage_candidates.append(min(900, slippage_candidates[0] + 200))
        if slippage_candidates[0] < 800:
            slippage_candidates.append(min(1200, slippage_candidates[0] + 400))
        last_error = ""
        for bps in slippage_candidates:
            try:
                quote = self._build_quote(input_mint, output_mint, amount_raw, bps)
                payload = {
                    "userPublicKey": self.wallet_address,
                    "quoteResponse": quote,
                    "dynamicComputeUnitLimit": True,
                    "prioritizationFeeLamports": "auto",
                    "wrapAndUnwrapSol": True,
                }
                body = self._http_post_json("swap", payload)
                swap_tx_b64 = str(body.get("swapTransaction") or "")
                if not swap_tx_b64:
                    raise RuntimeError("swap_transaction_empty")
                signature = self._send_signed_transaction(swap_tx_b64)
                self._wait_confirmation(signature)
                return {
                    "signature": signature,
                    "slippage_bps": int(bps),
                    "input_mint": str(input_mint),
                    "output_mint": str(output_mint),
                    "in_amount_raw": int(quote.get("inAmount") or amount_raw),
                    "out_amount_raw": int(quote.get("outAmount") or 0),
                    "price_impact_pct": float(quote.get("priceImpactPct") or 0.0),
                }
            except Exception as exc:
                last_error = str(exc)
        raise RuntimeError(last_error or "swap_failed")

    def swap_sol_to_token(self, output_mint: str, amount_sol: float, slippage_bps: int = 300) -> dict[str, Any]:
        lamports = int(max(0.0, float(amount_sol)) * 1_000_000_000.0)
        if lamports <= 0:
            raise RuntimeError("amount_sol_too_small")
        return self.swap_exact_in(SOL_MINT, str(output_mint), lamports, slippage_bps=slippage_bps)

    def swap_token_to_sol(
        self,
        input_mint: str,
        amount_raw: int,
        slippage_bps: int = 350,
    ) -> dict[str, Any]:
        return self.swap_exact_in(str(input_mint), SOL_MINT, int(amount_raw), slippage_bps=slippage_bps)
