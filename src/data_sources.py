from __future__ import annotations

import json
import re
import time
import calendar
import threading
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any
from xml.etree import ElementTree as ET

import requests

from src.models import TokenSnapshot, TrendEvent
from src.runtime_feedback import RuntimeFeedbackStore


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


RSS_INSTANCES = (
    "https://rss.xcancel.com",
)

X_FALLBACK_INSTANCES = (
    "https://r.jina.ai/http://x.com",
    "https://r.jina.ai/http://twitter.com",
)

SYMBOL_STOPWORDS = {
    "THE",
    "AND",
    "FOR",
    "WITH",
    "THIS",
    "FROM",
    "COIN",
    "TOKEN",
    "MEME",
    "BUY",
    "SELL",
    "LONG",
    "SHORT",
    "RT",
    "USD",
    "USDT",
    "HTTP",
    "HTTPS",
    "RSS",
    "XML",
    "ATOM",
    "URL",
    "LANG",
    "EMPTY",
    "FEED",
    "RETRY",
    "ERROR",
    "FAILED",
    "READER",
    "WHITELISTED",
    "SEARCH",
    "TWEET",
    "TWEETS",
    "POST",
    "POSTS",
    "NEWS",
    "ALERT",
    "BREAKING",
    "LOOKONCHAIN",
    "XCANCEL",
    "NITTER",
    "DOT",
    "AT",
    "ID",
    "ORIGINAL",
    "SOURCE",
    "PUMPFUN",
    "MEMECOIN",
    "CRYPTO",
}

SOLANA_WALLET_RE = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")
URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
DOMAIN_RE = re.compile(r"\b[a-z0-9\-]+\.(?:com|net|org|io|xyz|info|ai|gg)\b", re.IGNORECASE)
EVM_CONTRACT_RE = re.compile(r"\b0x[a-fA-F0-9]{40}\b")
X_FALLBACK_BLOCK_MARKERS = (
    "rss reader not yet whitelisted",
    "log in to x",
    "sign in to x",
    "create account",
    "join x today",
    "something went wrong",
    "rate limit exceeded",
)
X_FALLBACK_STOPWORDS = SYMBOL_STOPWORDS | {
    "HAVE",
    "MORE",
    "WAIT",
    "UNDER",
    "ASSETS",
    "CEO",
    "COINS",
    "MANAGEMENT",
    "INSANE",
    "LMAO",
    "POWER",
    "BUILD",
    "BALANCED",
    "PERMISSION",
    "SATURDAYVIBE",
    "NFA",
    "ATTRACT",
    "MY",
    "YOUR",
    "OUR",
    "THEY",
    "THEM",
    "WILL",
    "WOULD",
    "COULD",
    "SHOULD",
    "JUST",
    "VERY",
    "MUCH",
    "ONLY",
    "ALPHA",
    "ENTRY",
    "EXIT",
    "THESIS",
    "THREAD",
    "QUOTE",
    "ACCOUNT",
    "PROFILE",
    "FOLLOW",
    "LIKES",
    "LIKE",
    "VIEW",
    "VIEWS",
    "IMAGE",
    "VIDEO",
    "SPACE",
    "SPACES",
}


def extract_symbols(text: str) -> set[str]:
    body = str(text or "")
    body = URL_RE.sub(" ", body)
    body = DOMAIN_RE.sub(" ", body)
    out: set[str] = set()
    candidates: list[str] = []
    for m in re.findall(r"[$#]([A-Za-z][A-Za-z0-9]{1,11})", body):
        candidates.append(str(m or ""))
    for m in re.findall(r"\b[A-Z]{2,12}\b", body):
        candidates.append(str(m or ""))
    for m in candidates:
        sym = str(m or "").strip().upper()
        if not sym:
            continue
        if sym in SYMBOL_STOPWORDS:
            continue
        if sym.isdigit():
            continue
        if len(set(sym)) == 1 and len(sym) >= 3:
            continue
        out.add(sym)
    return out


class DexScreenerClient:
    BASE = "https://api.dexscreener.com"

    def __init__(self, timeout_seconds: int = 8) -> None:
        self.session = requests.Session()
        self.timeout_seconds = timeout_seconds

    def _get_json(self, path: str) -> Any:
        res = self.session.get(f"{self.BASE}{path}", timeout=self.timeout_seconds)
        res.raise_for_status()
        return res.json()

    def get_boosted_tokens(self) -> list[dict[str, Any]]:
        data = self._get_json("/token-boosts/latest/v1")
        return data if isinstance(data, list) else []

    def get_pairs_for_token(self, chain_id: str, token_address: str) -> list[dict[str, Any]]:
        data = self._get_json(f"/token-pairs/v1/{chain_id}/{token_address}")
        return data if isinstance(data, list) else []

    def fetch_snapshot_for_token(self, chain_id: str, token_address: str) -> TokenSnapshot | None:
        pairs = self.get_pairs_for_token(chain_id, token_address)
        best = self._pick_best_pair(pairs)
        if best is None:
            return None
        return self._parse_pair(best, source="dex_token")

    def fetch_snapshots(self, chain_id: str, max_tokens: int) -> list[TokenSnapshot]:
        rows = self.get_boosted_tokens()
        snapshots: list[TokenSnapshot] = []
        seen: set[str] = set()
        for row in rows:
            if str(row.get("chainId") or "").lower() != str(chain_id).lower():
                continue
            token_address = str(row.get("tokenAddress") or "").strip()
            if not token_address or token_address in seen:
                continue
            seen.add(token_address)
            if len(snapshots) >= max(1, int(max_tokens)):
                break
            try:
                pairs = self.get_pairs_for_token(chain_id, token_address)
            except Exception:
                continue
            best = self._pick_best_pair(pairs)
            if best is None:
                continue
            parsed = self._parse_pair(best, source="dex_boosted")
            if parsed is None:
                continue
            snapshots.append(parsed)
        return snapshots

    def search_pairs(self, query: str) -> list[dict[str, Any]]:
        q = str(query or "").strip()
        if not q:
            return []
        res = self.session.get(
            f"{self.BASE}/latest/dex/search",
            params={"q": q},
            timeout=self.timeout_seconds,
        )
        res.raise_for_status()
        body = res.json()
        rows = (body.get("pairs") or []) if isinstance(body, dict) else []
        return rows if isinstance(rows, list) else []

    def fetch_pairs_for_addresses(self, chain_id: str, token_addresses: list[str]) -> list[dict[str, Any]]:
        chain = str(chain_id or "").strip().lower()
        if not chain:
            return []
        addrs: list[str] = []
        seen: set[str] = set()
        for raw in token_addresses:
            addr = str(raw or "").strip()
            if not addr or addr in seen:
                continue
            seen.add(addr)
            addrs.append(addr)
        if not addrs:
            return []
        out: list[dict[str, Any]] = []
        for i in range(0, len(addrs), 25):
            chunk = addrs[i : i + 25]
            joined = ",".join(chunk)
            try:
                rows = self._get_json(f"/tokens/v1/{chain}/{joined}")
            except Exception:
                continue
            if isinstance(rows, list):
                out.extend([r for r in rows if isinstance(r, dict)])
        return out

    def fetch_snapshots_for_addresses(
        self,
        chain_id: str,
        token_addresses: list[str],
        max_tokens: int,
        source: str = "dex_token_batch",
    ) -> list[TokenSnapshot]:
        pairs = self.fetch_pairs_for_addresses(chain_id, token_addresses)
        if not pairs:
            return []
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in pairs:
            base = row.get("baseToken") if isinstance(row.get("baseToken"), dict) else {}
            addr = str((base or {}).get("address") or "").strip()
            if not addr:
                continue
            grouped.setdefault(addr, []).append(row)
        out: list[TokenSnapshot] = []
        limit = max(1, int(max_tokens))
        for addr in token_addresses:
            if len(out) >= limit:
                break
            rows = grouped.get(str(addr).strip()) or []
            best = self._pick_best_pair(rows)
            if best is None:
                continue
            parsed = self._parse_pair(best, source=source)
            if parsed is None:
                continue
            out.append(parsed)
        return out

    def fetch_symbol_snapshots(self, chain_id: str, symbols: list[str], max_tokens: int) -> list[TokenSnapshot]:
        out: list[TokenSnapshot] = []
        seen: set[str] = set()
        limit = max(1, int(max_tokens))
        for symbol in symbols:
            if len(out) >= limit:
                break
            sym = str(symbol or "").strip().upper()
            if not sym:
                continue
            try:
                pairs = self.search_pairs(sym)
            except Exception:
                continue
            selected: list[dict[str, Any]] = []
            for row in pairs:
                if str(row.get("chainId") or "").lower() != str(chain_id).lower():
                    continue
                base = row.get("baseToken") or {}
                base_symbol = str(base.get("symbol") or "").upper().strip()
                if base_symbol != sym:
                    continue
                selected.append(row)
            best = self._pick_best_pair(selected)
            if best is None:
                continue
            parsed = self._parse_pair(best, source="dex_search")
            if parsed is None or not parsed.token_address or parsed.token_address in seen:
                continue
            seen.add(parsed.token_address)
            out.append(parsed)
        return out

    @staticmethod
    def _pick_best_pair(pairs: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not pairs:
            return None
        return max(
            pairs,
            key=lambda row: float(((row.get("liquidity") or {}).get("usd")) or 0.0),
        )

    @staticmethod
    def _parse_pair(pair: dict[str, Any], source: str) -> TokenSnapshot | None:
        base = pair.get("baseToken") or {}
        tx_m5 = (pair.get("txns") or {}).get("m5") or {}
        liq = float(((pair.get("liquidity") or {}).get("usd")) or 0.0)
        vol = float(((pair.get("volume") or {}).get("m5")) or 0.0)
        price = float(pair.get("priceUsd") or 0.0)
        try:
            market_cap_usd = float(pair.get("marketCap") or pair.get("marketcap") or 0.0)
        except Exception:
            market_cap_usd = 0.0
        try:
            fdv_usd = float(pair.get("fdv") or 0.0)
        except Exception:
            fdv_usd = 0.0
        if price <= 0:
            return None
        created_ms = pair.get("pairCreatedAt")
        age_minutes = 999999.0
        if created_ms:
            try:
                age_minutes = max(0.0, ((time.time() * 1000.0) - float(created_ms)) / 60000.0)
            except Exception:
                age_minutes = 999999.0
        return TokenSnapshot(
            token_address=str(base.get("address") or ""),
            symbol=str(base.get("symbol") or "").upper().strip(),
            name=str(base.get("name") or "").strip(),
            pair_url=str(pair.get("url") or "").strip(),
            price_usd=price,
            liquidity_usd=liq,
            volume_5m_usd=vol,
            buys_5m=int(tx_m5.get("buys") or 0),
            sells_5m=int(tx_m5.get("sells") or 0),
            age_minutes=age_minutes,
            source=source,
            market_cap_usd=market_cap_usd,
            fdv_usd=fdv_usd,
        )


class PumpFunClient:
    BASE = "https://frontend-api-v3.pump.fun"

    def __init__(self, timeout_seconds: int = 8) -> None:
        self.session = requests.Session()
        self.timeout_seconds = timeout_seconds
        self._cache_rows: list[dict[str, Any]] = []
        self._cache_ts: float = 0.0
        self._cache_key: tuple[int, bool] | None = None

    def fetch_latest_coins(
        self,
        limit: int = 120,
        include_nsfw: bool = False,
        cache_seconds: int = 45,
    ) -> list[dict[str, Any]]:
        n = max(20, min(300, int(limit)))
        use_nsfw = bool(include_nsfw)
        ttl = max(5, int(cache_seconds))
        now = time.time()
        key = (n, use_nsfw)
        if self._cache_rows and self._cache_key == key and (now - self._cache_ts) < ttl:
            return list(self._cache_rows)
        headers = {"User-Agent": "Mozilla/5.0 (compatible; AxiomFlowBot/1.0)"}
        res = self.session.get(
            f"{self.BASE}/coins",
            params={
                "offset": 0,
                "limit": n,
                "sort": "created_timestamp",
                "order": "desc",
                "includeNsfw": "true" if use_nsfw else "false",
            },
            headers=headers,
            timeout=self.timeout_seconds,
        )
        res.raise_for_status()
        body = res.json()
        rows = body if isinstance(body, list) else []
        out = [r for r in rows if isinstance(r, dict)]
        self._cache_rows = list(out)
        self._cache_ts = now
        self._cache_key = key
        return out


class TrendCollector:
    def __init__(
        self,
        timeout_seconds: int = 8,
        coingecko_api_key: str = "",
        solscan_api_key: str = "",
        solana_rpc_url: str = "https://api.mainnet-beta.solana.com",
        solscan_monthly_cu_limit: int = 10_000_000,
        solscan_cu_per_request: int = 100,
        solscan_budget_window_seconds: int = 300,
        solscan_permission_backoff_seconds: int = 21600,
        google_api_key: str = "",
        google_model: str = "gemini-2.5-flash",
        google_trend_enabled: bool = True,
        google_trend_interval_seconds: int = 14000,
        google_trend_cooldown_seconds: int = 21600,
        google_trend_max_symbols: int = 15,
        runtime_feedback_store: RuntimeFeedbackStore | None = None,
    ) -> None:
        self.session = requests.Session()
        self.timeout_seconds = timeout_seconds
        self.dex = DexScreenerClient(timeout_seconds=timeout_seconds)
        self.coingecko_api_key = str(coingecko_api_key or "").strip()
        self.solscan_api_key = str(solscan_api_key or "").strip()
        self.solana_rpc_url = str(solana_rpc_url or "").strip()
        self.solscan = SolscanProClient(
            api_key=self.solscan_api_key,
            timeout_seconds=self.timeout_seconds,
            monthly_cu_limit=solscan_monthly_cu_limit,
            cu_per_request=solscan_cu_per_request,
            budget_window_seconds=solscan_budget_window_seconds,
            permission_backoff_seconds=solscan_permission_backoff_seconds,
        )
        self.google_api_key = str(google_api_key or "").strip()
        self.google_model = str(google_model or "gemini-2.5-flash").strip()
        self.google_trend_enabled = bool(google_trend_enabled)
        self.google_trend_interval_seconds = max(14000, int(google_trend_interval_seconds))
        self.google_trend_cooldown_seconds = max(14000, int(google_trend_cooldown_seconds))
        self.google_trend_max_symbols = max(5, min(40, int(google_trend_max_symbols)))
        self.runtime_feedback_store = runtime_feedback_store
        self._google_last_fetch_ts = 0
        self._google_backoff_until_ts = 0
        self._google_cache_events: list[TrendEvent] = []
        self._google_last_error = ""
        self._google_rate_limit_hits = 0
        self._google_daily_calls: dict[str, int] = {}
        # Keep a soft daily ceiling well below free-tier maximum.
        self._google_daily_max_calls = 300
        self._google_state_saved_ts = 0
        self._google_last_feedback_sig = ""
        self._google_last_feedback_ts = 0
        self._trader_round_robin_idx = 0
        self._wallet_round_robin_idx = 0
        self._wallet_last_cursor: dict[str, str] = {}
        self._dynamic_wallet_watch: dict[str, int] = {}
        self._load_google_runtime_state()

    def _trim_google_daily_calls(self, now_ts: int) -> None:
        if not isinstance(self._google_daily_calls, dict):
            self._google_daily_calls = {}
            return
        keep_keys: set[str] = set()
        for i in range(0, 14):
            day = datetime.fromtimestamp(int(now_ts) - (i * 86400), tz=timezone.utc).strftime("%Y-%m-%d")
            keep_keys.add(day)
        cleaned: dict[str, int] = {}
        for k, v in self._google_daily_calls.items():
            key = str(k or "").strip()
            if key not in keep_keys:
                continue
            try:
                n = max(0, int(v))
            except Exception:
                n = 0
            cleaned[key] = n
        self._google_daily_calls = cleaned

    def _serialize_google_events(self, events: list[TrendEvent]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for ev in list(events or [])[:120]:
            if isinstance(ev, TrendEvent):
                out.append(asdict(ev))
                continue
            if isinstance(ev, dict):
                out.append(
                    {
                        "source": str(ev.get("source") or "google_gemini"),
                        "symbol": str(ev.get("symbol") or ""),
                        "text": str(ev.get("text") or ""),
                        "ts": int(ev.get("ts") or 0),
                    }
                )
        return out

    def _deserialize_google_events(self, rows: list[dict[str, Any]]) -> list[TrendEvent]:
        out: list[TrendEvent] = []
        for row in list(rows or [])[:120]:
            if not isinstance(row, dict):
                continue
            out.append(
                TrendEvent(
                    source=str(row.get("source") or "google_gemini"),
                    symbol=str(row.get("symbol") or ""),
                    text=str(row.get("text") or ""),
                    ts=int(row.get("ts") or 0),
                )
            )
        return out

    def _load_google_runtime_state(self) -> None:
        store = self.runtime_feedback_store
        if store is None:
            return
        try:
            payload = store.load_kv("google_runtime_state")
        except Exception:
            return
        if not isinstance(payload, dict) or not payload:
            return
        try:
            self._google_last_fetch_ts = int(payload.get("last_fetch_ts") or 0)
        except Exception:
            self._google_last_fetch_ts = 0
        try:
            self._google_backoff_until_ts = int(payload.get("backoff_until_ts") or 0)
        except Exception:
            self._google_backoff_until_ts = 0
        self._google_last_error = str(payload.get("last_error") or "")
        try:
            self._google_rate_limit_hits = max(0, int(payload.get("rate_limit_hits") or 0))
        except Exception:
            self._google_rate_limit_hits = 0
        daily_calls = payload.get("daily_calls")
        self._google_daily_calls = dict(daily_calls) if isinstance(daily_calls, dict) else {}
        self._trim_google_daily_calls(int(time.time()))
        events_raw = payload.get("cache_events")
        if isinstance(events_raw, list):
            self._google_cache_events = self._deserialize_google_events(events_raw)
        try:
            self._google_state_saved_ts = int(payload.get("saved_ts") or 0)
        except Exception:
            self._google_state_saved_ts = 0

    def _save_google_runtime_state(self, now_ts: int, force: bool = False) -> None:
        store = self.runtime_feedback_store
        if store is None:
            return
        if not force and (int(now_ts) - int(self._google_state_saved_ts)) < 30:
            return
        self._trim_google_daily_calls(now_ts)
        payload = {
            "last_fetch_ts": int(self._google_last_fetch_ts),
            "backoff_until_ts": int(self._google_backoff_until_ts),
            "last_error": str(self._google_last_error or ""),
            "rate_limit_hits": int(self._google_rate_limit_hits),
            "daily_calls": dict(self._google_daily_calls or {}),
            "cache_events": self._serialize_google_events(self._google_cache_events),
            "saved_ts": int(now_ts),
        }
        try:
            store.save_kv("google_runtime_state", payload, now_ts=now_ts)
            self._google_state_saved_ts = int(now_ts)
        except Exception:
            return

    def _record_google_feedback(
        self,
        *,
        now_ts: int,
        status: str,
        error: str = "",
        action: str = "",
        detail: str = "",
        level: str = "info",
        meta: dict[str, Any] | None = None,
    ) -> None:
        store = self.runtime_feedback_store
        if store is None:
            return
        st = str(status or "").strip() or "event"
        err = str(error or "").strip()
        sig = f"{st}|{err}|{str(action or '').strip()}"
        if sig == self._google_last_feedback_sig and (int(now_ts) - int(self._google_last_feedback_ts)) < 60:
            return
        try:
            store.append_event(
                source="google_gemini",
                level=str(level or "info").strip().lower(),
                status=st,
                error=err,
                action=str(action or "").strip(),
                detail=str(detail or "").strip(),
                meta=dict(meta or {}),
                now_ts=int(now_ts),
            )
            self._google_last_feedback_sig = sig
            self._google_last_feedback_ts = int(now_ts)
        except Exception:
            return

    def google_runtime_status(self) -> dict[str, Any]:
        now = int(time.time())
        day_key = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%d")
        self._trim_google_daily_calls(now)
        return {
            "enabled": bool(self.google_trend_enabled and self.google_api_key),
            "model": str(self.google_model or "gemini-2.5-flash"),
            "interval_seconds": int(self.google_trend_interval_seconds),
            "cooldown_seconds": int(self.google_trend_cooldown_seconds),
            "last_fetch_ts": int(self._google_last_fetch_ts),
            "backoff_until_ts": int(self._google_backoff_until_ts),
            "backoff_remaining_seconds": max(0, int(self._google_backoff_until_ts) - now),
            "last_error": str(self._google_last_error or ""),
            "rate_limit_hits": int(self._google_rate_limit_hits),
            "daily_used": int(self._google_daily_calls.get(day_key) or 0),
            "daily_cap": int(self._google_daily_max_calls),
            "cache_count": len(self._google_cache_events),
            "state_saved_ts": int(self._google_state_saved_ts),
        }

    def _get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        res = self.session.get(url, params=params, timeout=self.timeout_seconds)
        res.raise_for_status()
        return res.json()

    @staticmethod
    def _find_feed_text(node: ET.Element, tags: tuple[str, ...]) -> str:
        for tag in tags:
            text = node.findtext(tag)
            if text and str(text).strip():
                return str(text).strip()
            text = node.findtext(f".//{{*}}{tag}")
            if text and str(text).strip():
                return str(text).strip()
        return ""

    @staticmethod
    def _extract_solana_wallets(text: str) -> list[str]:
        body = str(text or "")
        out: list[str] = []
        seen: set[str] = set()
        for raw in SOLANA_WALLET_RE.findall(body):
            addr = str(raw or "").strip()
            if not addr or addr in seen:
                continue
            seen.add(addr)
            out.append(addr)
        return out

    @staticmethod
    def _parse_wallet_entries(wallets_csv: str) -> list[tuple[str, str]]:
        rows: list[tuple[str, str]] = []
        seen: set[str] = set()
        for raw in [w.strip() for w in str(wallets_csv or "").split(",") if w.strip()]:
            label = ""
            addr = str(raw or "").strip()
            if "|" in addr:
                left, right = addr.split("|", 1)
                if right.strip():
                    label = left.strip()
                    addr = right.strip()
            elif ":" in addr:
                left, right = addr.split(":", 1)
                right_clean = right.strip()
                # Only treat as label:address when right side looks like Solana wallet.
                if 32 <= len(right_clean) <= 44 and SOLANA_WALLET_RE.fullmatch(right_clean):
                    label = left.strip()
                    addr = right_clean
            if not SOLANA_WALLET_RE.fullmatch(addr):
                continue
            if addr in seen:
                continue
            seen.add(addr)
            rows.append((label, addr))
        return rows

    def _wallet_fetch_batch(self, entries: list[tuple[str, str]], batch_size: int = 24) -> list[tuple[str, str]]:
        if not entries:
            return []
        n = len(entries)
        take = max(1, min(int(batch_size), n))
        if n <= take:
            return list(entries)
        start = int(self._wallet_round_robin_idx) % n
        ordered = list(entries[start:]) + list(entries[:start])
        self._wallet_round_robin_idx = int((start + take) % n)
        return ordered[:take]

    def _wallet_events_from_solscan(self, label: str, wallet: str, now_ts: int) -> list[TrendEvent]:
        if not bool(self.solscan and self.solscan.enabled):
            return []
        events: list[TrendEvent] = []
        try:
            rows = self.solscan.fetch_account_transfers(wallet, page_size=16)
        except Exception:
            return []
        if not rows:
            return events
        newest = rows[0] if isinstance(rows[0], dict) else {}
        newest_cursor = (
            str(newest.get("tx_hash") or newest.get("txHash") or newest.get("signature") or newest.get("trans_id") or "")
            .strip()
        )
        if not newest_cursor:
            newest_cursor = str(int(now_ts))
        prev_cursor = str(self._wallet_last_cursor.get(wallet) or "")
        if prev_cursor and newest_cursor == prev_cursor:
            return events
        title = f"{label}({wallet[:6]}...)" if label else f"{wallet[:6]}..."
        for row in rows:
            if not isinstance(row, dict):
                continue
            cur = str(row.get("tx_hash") or row.get("txHash") or row.get("signature") or row.get("trans_id") or "").strip()
            if prev_cursor and cur and cur == prev_cursor:
                break
            symbol_raw = str(
                row.get("token_symbol")
                or row.get("symbol")
                or row.get("token")
                or row.get("token_name")
                or ""
            ).strip()
            symbol = self._normalize_symbol(symbol_raw)
            if not symbol:
                continue
            action = str(row.get("activity_type") or row.get("flow") or row.get("type") or "transfer").strip()
            events.append(
                TrendEvent(
                    source="wallet_tracker",
                    symbol=symbol,
                    text=f"{title}: {action} {symbol}",
                    ts=int(now_ts),
                )
            )
            if len(events) >= 8:
                break
        self._wallet_last_cursor[wallet] = newest_cursor
        return events

    def _wallet_events_from_rpc(self, label: str, wallet: str, now_ts: int) -> list[TrendEvent]:
        rpc_url = str(self.solana_rpc_url or "").strip()
        if not rpc_url:
            return []
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignaturesForAddress",
            "params": [wallet, {"limit": 5}],
        }
        try:
            res = self.session.post(rpc_url, json=payload, timeout=self.timeout_seconds)
            res.raise_for_status()
            body = res.json()
            rows = (body or {}).get("result") or []
            if not isinstance(rows, list) or not rows:
                return []
        except Exception:
            return []
        newest = rows[0] if isinstance(rows[0], dict) else {}
        newest_sig = str(newest.get("signature") or "").strip()
        if not newest_sig:
            return []
        prev_sig = str(self._wallet_last_cursor.get(wallet) or "")
        if prev_sig and newest_sig == prev_sig:
            return []
        self._wallet_last_cursor[wallet] = newest_sig
        title = f"{label}({wallet[:6]}...)" if label else f"{wallet[:6]}..."
        return [
            TrendEvent(
                source="wallet_tracker",
                symbol="SOL",
                text=f"{title}: new tx detected",
                ts=int(now_ts),
            )
        ]

    def _parse_feed_entries(self, xml_text: str, max_items: int) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        try:
            root = ET.fromstring(str(xml_text or "").lstrip())
        except Exception:
            return out
        nodes = list(root.findall(".//item"))
        if not nodes:
            nodes = list(root.findall(".//{*}entry"))
        for node in nodes[: max(1, int(max_items))]:
            title = self._find_feed_text(node, ("title",))
            desc = self._find_feed_text(node, ("description", "summary", "content"))
            if not title and not desc:
                continue
            out.append((title, desc))
        return out

    def fetch_coingecko_symbols(self) -> set[str]:
        out: set[str] = set()
        try:
            params: dict[str, Any] = {}
            if self.coingecko_api_key:
                params["x_cg_demo_api_key"] = self.coingecko_api_key
            data = self._get_json("https://api.coingecko.com/api/v3/search/trending", params=params or None)
        except Exception:
            return out
        coins = data.get("coins") if isinstance(data, dict) else []
        if not isinstance(coins, list):
            return out
        for row in coins[:50]:
            item = row.get("item") if isinstance(row, dict) else {}
            symbol = str((item or {}).get("symbol") or "").upper().strip()
            if symbol:
                out.add(symbol)
        return out

    def fetch_trader_rss_events(self, accounts_csv: str, max_items_per_account: int = 4) -> list[TrendEvent]:
        events: list[TrendEvent] = []
        accounts = [a.strip().lstrip("@") for a in str(accounts_csv or "").split(",") if a.strip()]
        if not accounts:
            return events
        uniq_accounts: list[str] = []
        seen_acc: set[str] = set()
        for a in accounts:
            key = str(a or "").strip()
            if not key:
                continue
            low = key.lower()
            if low in seen_acc:
                continue
            seen_acc.add(low)
            uniq_accounts.append(key)
        accounts = uniq_accounts
        # Keep each cycle responsive by scanning a rotating subset.
        # Keep cycle latency bounded; combine with 5-minute source interval for near 30-minute full rotation at ~100 accounts.
        batch_n = max(1, min(20, len(accounts)))
        if len(accounts) > batch_n:
            start = int(self._trader_round_robin_idx) % len(accounts)
            ordered = list(accounts[start:]) + list(accounts[:start])
            self._trader_round_robin_idx = int((start + batch_n) % len(accounts))
            target_accounts = ordered[:batch_n]
        else:
            target_accounts = list(accounts)
        now_ts = int(time.time())
        headers = {"User-Agent": "Mozilla/5.0 (compatible; AxiomFlowBot/1.0)"}
        any_feed_success = False
        last_error = ""
        fallback_budget = max(2, min(8, (len(target_accounts) // 2) + 1))
        fallback_used = 0
        for account in target_accounts:
            fetched = False
            for instance in RSS_INSTANCES:
                account_path = account
                if "rss.xcancel.com" in instance:
                    account_path = str(account or "").lower()
                    urls = [
                        f"{instance}/search/rss?f=tweets&q=from%3A{account_path}",
                    ]
                else:
                    urls = [f"{instance}/{account_path}/rss"]
                for url in urls:
                    try:
                        res = self.session.get(url, headers=headers, timeout=min(2.5, float(self.timeout_seconds)))
                    except Exception as exc:
                        last_error = str(exc)
                        continue
                    body_text = str(res.text or "")
                    lowered_body = body_text.lower()
                    if int(res.status_code) >= 400:
                        if "rss reader not yet whitelisted" in lowered_body:
                            last_error = "rss_not_whitelisted"
                        else:
                            last_error = f"http_{int(res.status_code)}"
                        continue
                    if "rss reader not yet whitelisted" in lowered_body:
                        last_error = "rss_not_whitelisted"
                        continue
                    entries = self._parse_feed_entries(res.text, max_items=max_items_per_account)
                    if not entries:
                        last_error = "feed_entries_empty"
                        continue
                    fetched = True
                    any_feed_success = True
                    for title, desc in entries:
                        merged = f"{title} {desc}".strip()
                        lowered = merged.lower()
                        if "rss reader not yet whitelisted" in lowered:
                            continue
                        for addr in self._extract_solana_wallets(merged):
                            self._dynamic_wallet_watch[str(addr)] = int(now_ts + 86400)
                        symbols = extract_symbols(merged)
                        if not symbols:
                            continue
                        for sym in symbols:
                            events.append(
                                TrendEvent(
                                    source="trader_x",
                                    symbol=sym,
                                    text=f"@{account}: {title[:120]}",
                                    ts=now_ts,
                                )
                            )
                    if fetched:
                        break
                if fetched:
                    break
            if not fetched:
                # Fallback path: parse public X page via r.jina.ai mirror when RSS endpoints are blocked.
                if fallback_used < fallback_budget:
                    fallback_symbols = self._fetch_trader_x_fallback_symbols(account, now_ts=now_ts)
                    if fallback_symbols:
                        fallback_used += 1
                        any_feed_success = True
                        for sym in sorted(fallback_symbols)[:10]:
                            events.append(
                                TrendEvent(
                                    source="trader_x",
                                    symbol=sym,
                                    text=f"@{account}: x_fallback",
                                    ts=now_ts,
                                )
                            )
                continue
        if not any_feed_success and target_accounts:
            reason = str(last_error or "rss_unreachable")
            if (
                reason.startswith("rss_not_whitelisted")
                or reason.startswith("feed_entries_empty")
                or reason.startswith("http_400")
                or reason.startswith("http_403")
                or reason.startswith("http_429")
            ):
                return events
            raise RuntimeError(f"trader_rss_unavailable:{reason[:180]}")
        return events

    def _extract_x_post_snippets(
        self,
        markdown_text: str,
        *,
        max_posts: int = 8,
    ) -> list[str]:
        text = str(markdown_text or "")
        if not text:
            return []
        lines = [str(line or "").strip() for line in text.splitlines()]
        lines = [line for line in lines if line]
        if not lines:
            return []
        snippets: list[str] = []
        buffer: list[str] = []
        in_posts = False
        for line in lines[:1400]:
            lower = line.lower()
            if not in_posts and ("posts" in lower):
                in_posts = True
                buffer.clear()
                continue
            if not in_posts:
                continue
            if line.startswith("http://") or line.startswith("https://"):
                continue
            buffer.append(line)
            buffer = buffer[-6:]
            if "https://x.com/" in line and "/status/" in line:
                snippet = " ".join(buffer[:-1]).strip()
                if snippet:
                    snippets.append(snippet)
                buffer = []
                if len(snippets) >= max(1, int(max_posts)):
                    break
        if not snippets:
            snippets = [" ".join(lines[:220])]
        return snippets[: max(1, int(max_posts))]

    def _resolve_symbols_from_contract_addresses(
        self,
        addresses: list[str],
        *,
        max_symbols: int = 6,
    ) -> list[str]:
        ranked: list[str] = []
        seen: set[str] = set()
        for addr in addresses:
            token = str(addr or "").strip()
            if not token or token in seen:
                continue
            seen.add(token)
            try:
                if token.lower().startswith("0x"):
                    pairs = self.dex.search_pairs(token)
                    best = None
                    for row in pairs:
                        if not isinstance(row, dict):
                            continue
                        base = row.get("baseToken") if isinstance(row.get("baseToken"), dict) else {}
                        if str((base or {}).get("address") or "").strip().lower() != token.lower():
                            continue
                        liq = float(((row.get("liquidity") or {}).get("usd")) or 0.0)
                        if best is None or liq > float(((best.get("liquidity") or {}).get("usd")) or 0.0):
                            best = row
                    parsed = self.dex._parse_pair(best, source="dex_search") if best else None
                else:
                    parsed = self.dex.fetch_snapshot_for_token("solana", token)
                sym = str(getattr(parsed, "symbol", "") or "").upper().strip()
                if sym and sym not in X_FALLBACK_STOPWORDS:
                    ranked.append(sym)
            except Exception:
                continue
            if len(ranked) >= max(1, int(max_symbols)):
                break
        return ranked

    def _extract_symbols_from_x_markdown(
        self,
        markdown_text: str,
        *,
        max_posts: int = 8,
        max_symbols: int = 12,
    ) -> set[str]:
        text = str(markdown_text or "")
        if not text:
            return set()
        lowered_all = text.lower()
        if any(marker in lowered_all for marker in X_FALLBACK_BLOCK_MARKERS):
            return set()
        snippets = self._extract_x_post_snippets(text, max_posts=max_posts)
        if not snippets:
            return set()

        explicit_counts: dict[str, int] = {}
        repeated_counts: dict[str, int] = {}
        repeated_snippet_hits: dict[str, int] = {}
        contract_addresses: list[str] = []
        for snippet in snippets[: max(1, int(max_posts))]:
            clean = DOMAIN_RE.sub(" ", URL_RE.sub(" ", snippet))
            for m in re.findall(r"\$([A-Za-z][A-Za-z0-9]{1,11})", clean):
                sym = str(m or "").upper().strip()
                if not sym or sym in X_FALLBACK_STOPWORDS:
                    continue
                explicit_counts[sym] = int(explicit_counts.get(sym, 0)) + 1

            for addr in SOLANA_WALLET_RE.findall(clean):
                token = str(addr or "").strip()
                if len(token) >= 32:
                    contract_addresses.append(token)
            for addr in EVM_CONTRACT_RE.findall(clean):
                token = str(addr or "").strip()
                if token:
                    contract_addresses.append(token)

            local_seen: set[str] = set()
            for sym in extract_symbols(clean):
                key = str(sym or "").upper().strip()
                if not key or key in X_FALLBACK_STOPWORDS:
                    continue
                repeated_counts[key] = int(repeated_counts.get(key, 0)) + 1
                if key not in local_seen:
                    repeated_snippet_hits[key] = int(repeated_snippet_hits.get(key, 0)) + 1
                    local_seen.add(key)

        ranked: list[str] = []
        seen: set[str] = set()
        for sym, _ in sorted(explicit_counts.items(), key=lambda item: (-int(item[1]), item[0])):
            if sym in seen:
                continue
            seen.add(sym)
            ranked.append(sym)
            if len(ranked) >= max(1, int(max_symbols)):
                break
        if len(ranked) < max(1, int(max_symbols)):
            for sym in self._resolve_symbols_from_contract_addresses(
                contract_addresses,
                max_symbols=max(1, int(max_symbols) - len(ranked)),
            ):
                if sym in seen or sym in X_FALLBACK_STOPWORDS:
                    continue
                seen.add(sym)
                ranked.append(sym)
                if len(ranked) >= max(1, int(max_symbols)):
                    break
        if len(ranked) < max(1, int(max_symbols)):
            repeated_ranked = sorted(
                repeated_counts.items(),
                key=lambda item: (-int(repeated_snippet_hits.get(item[0], 0)), -int(item[1]), item[0]),
            )
            for sym, cnt in repeated_ranked:
                if sym in seen or sym in X_FALLBACK_STOPWORDS:
                    continue
                distinct_posts = int(repeated_snippet_hits.get(sym, 0))
                if distinct_posts < 2 and int(cnt) < 3:
                    continue
                seen.add(sym)
                ranked.append(sym)
                if len(ranked) >= max(1, int(max_symbols)):
                    break
        return set(ranked)

    def _fetch_trader_x_fallback_symbols(self, account: str, *, now_ts: int | None = None) -> set[str]:
        handle = str(account or "").strip().lstrip("@")
        if not handle:
            return set()
        if not re.fullmatch(r"[A-Za-z0-9_]{1,32}", handle):
            return set()

        headers = {"User-Agent": "Mozilla/5.0 (compatible; AxiomFlowBot/1.0)"}
        timeout = min(6.0, max(2.0, float(self.timeout_seconds)))
        for base in X_FALLBACK_INSTANCES:
            url = f"{base}/{handle}"
            try:
                res = self.session.get(url, headers=headers, timeout=timeout)
            except Exception:
                continue
            if int(getattr(res, "status_code", 0)) >= 400:
                continue
            body = str(getattr(res, "text", "") or "")
            lowered = body.lower()
            if not body:
                continue
            if "forbidden" in lowered and "403" in lowered:
                continue
            if "too many requests" in lowered and "429" in lowered:
                continue
            symbols = self._extract_symbols_from_x_markdown(body)
            if symbols:
                return symbols
        return set()

    def fetch_reddit_events(
        self,
        subreddits_csv: str,
        max_items_per_subreddit: int = 8,
    ) -> list[TrendEvent]:
        events: list[TrendEvent] = []
        subs = [s.strip().lstrip("r/") for s in str(subreddits_csv or "").split(",") if s.strip()]
        if not subs:
            return events
        now_ts = int(time.time())
        headers = {"User-Agent": "Mozilla/5.0 (compatible; AxiomFlowBot/1.0)"}
        for sub in subs[:40]:
            url = f"https://www.reddit.com/r/{sub}/new/.rss"
            try:
                res = self.session.get(url, headers=headers, timeout=self.timeout_seconds)
                res.raise_for_status()
            except Exception:
                continue
            entries = self._parse_feed_entries(res.text, max_items=max_items_per_subreddit)
            for title, desc in entries:
                merged = f"{title} {desc}".strip()
                symbols = extract_symbols(merged)
                if not symbols:
                    continue
                for sym in symbols:
                    events.append(
                        TrendEvent(
                            source="community_reddit",
                            symbol=sym,
                            text=f"r/{sub}: {title[:120]}",
                            ts=now_ts,
                        )
                    )
        return events

    def fetch_4chan_events(
        self,
        boards_csv: str,
        max_threads_per_board: int = 12,
    ) -> list[TrendEvent]:
        events: list[TrendEvent] = []
        boards = [str(b or "").strip().lower() for b in str(boards_csv or "").split(",") if str(b or "").strip()]
        if not boards:
            return events
        now_ts = int(time.time())
        headers = {"User-Agent": "Mozilla/5.0 (compatible; AxiomFlowBot/1.0)"}
        thread_cap = max(1, min(50, int(max_threads_per_board)))
        for board in boards[:6]:
            try:
                res = self.session.get(
                    f"https://a.4cdn.org/{board}/catalog.json",
                    headers=headers,
                    timeout=self.timeout_seconds,
                )
                res.raise_for_status()
                pages = res.json()
            except Exception:
                continue
            if not isinstance(pages, list):
                continue
            seen_threads = 0
            for page in pages[:2]:
                threads = list((page or {}).get("threads") or [])
                for row in threads:
                    if seen_threads >= thread_cap:
                        break
                    if not isinstance(row, dict):
                        continue
                    title = str(row.get("sub") or "").strip()
                    comment = str(row.get("com") or "").strip()
                    merged = f"{title} {comment}".strip()
                    symbols = extract_symbols(merged)
                    if not symbols:
                        continue
                    snippet = re.sub(r"<[^>]+>", " ", merged)
                    snippet = re.sub(r"\s+", " ", snippet).strip()[:180]
                    for sym in symbols:
                        events.append(
                            TrendEvent(
                                source="community_4chan",
                                symbol=sym,
                                text=f"/{board}/: {snippet}",
                                ts=now_ts,
                            )
                        )
                    seen_threads += 1
                if seen_threads >= thread_cap:
                    break
        return events

    def fetch_wallet_events(self, wallets_csv: str) -> list[TrendEvent]:
        events: list[TrendEvent] = []
        now_ts = int(time.time())
        entries = self._parse_wallet_entries(wallets_csv)
        # Include wallets discovered from trader feeds for the next 24 hours.
        for addr, exp in list(self._dynamic_wallet_watch.items()):
            if int(exp) <= int(now_ts):
                self._dynamic_wallet_watch.pop(addr, None)
                continue
            if not any(w == addr for _, w in entries):
                entries.append(("trader", addr))
        if not entries:
            return events
        for label, wallet in self._wallet_fetch_batch(entries, batch_size=24):
            rows = self._wallet_events_from_solscan(label, wallet, now_ts)
            if not rows:
                rows = self._wallet_events_from_rpc(label, wallet, now_ts)
            if rows:
                events.extend(rows)
        return events

    def fetch_yahoo_crypto_news_events(
        self,
        symbols_csv: str,
        max_items_per_symbol: int = 4,
    ) -> list[TrendEvent]:
        events: list[TrendEvent] = []
        pairs = [s.strip().upper() for s in str(symbols_csv or "").split(",") if s.strip()]
        if not pairs:
            return events
        allowed = {p.split("-")[0].strip().upper() for p in pairs if p.strip()}
        now_ts = int(time.time())
        headers = {"User-Agent": "Mozilla/5.0 (compatible; AxiomFlowBot/1.0)"}
        for pair in pairs[:25]:
            base_symbol = pair.split("-")[0].strip().upper()
            if not base_symbol:
                continue
            url = "https://feeds.finance.yahoo.com/rss/2.0/headline"
            try:
                res = self.session.get(
                    url,
                    params={"s": pair, "region": "US", "lang": "en-US"},
                    headers=headers,
                    timeout=self.timeout_seconds,
                )
                res.raise_for_status()
            except Exception:
                continue

            entries = self._parse_feed_entries(res.text, max_items=max_items_per_symbol)
            for title, desc in entries:
                merged = f"{title} {desc}".strip()
                explicit = {m.upper() for m in re.findall(r"\$([A-Za-z][A-Za-z0-9]{1,11})", merged)}
                symbols = {base_symbol}
                for sym in explicit:
                    if sym in allowed:
                        symbols.add(sym)
                for sym in symbols:
                    events.append(
                        TrendEvent(
                            source="yahoo_news",
                            symbol=str(sym).upper(),
                            text=f"{pair}: {title[:120]}",
                            ts=now_ts,
                        )
                    )
        return events

    @staticmethod
    def _normalize_symbol(raw: Any) -> str:
        value = str(raw or "").strip().upper().lstrip("$")
        value = re.sub(r"[^A-Z0-9]", "", value)
        if len(value) < 2 or len(value) > 12:
            return ""
        if value in SYMBOL_STOPWORDS:
            return ""
        return value

    @staticmethod
    def _extract_first_json_object(text: str) -> dict[str, Any]:
        raw = str(text or "").strip()
        if not raw:
            return {}
        if raw.startswith("```"):
            lines = [ln for ln in raw.splitlines() if not ln.strip().startswith("```")]
            raw = "\n".join(lines).strip()
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            pass
        match = re.search(r"\{[\s\S]+\}", raw)
        if not match:
            return {}
        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    def fetch_google_gemini_events(
        self,
        trend_query: str,
        context_lines: list[str],
        now_ts: int | None = None,
    ) -> tuple[list[TrendEvent], dict[str, Any]]:
        now = int(now_ts or int(time.time()))
        self._trim_google_daily_calls(now)
        enabled = bool(self.google_trend_enabled)
        gemini_enabled = bool(self.google_api_key)
        meta: dict[str, Any] = {
            "enabled": enabled,
            "status": "disabled",
            "count": 0,
            "cached": False,
            "next_retry_seconds": 0,
            "error": "",
        }
        if not enabled:
            self._record_google_feedback(
                now_ts=now,
                status="disabled",
                action="GOOGLE_TREND_ENABLED=false, Google 수집 비활성화 상태 유지",
                level="info",
                meta={"enabled": False},
            )
            return [], meta
        if self._google_cache_events and (now - int(self._google_last_fetch_ts)) < self.google_trend_interval_seconds:
            remain = max(0, self.google_trend_interval_seconds - (now - int(self._google_last_fetch_ts)))
            meta.update(
                {
                    "status": "cached",
                    "count": len(self._google_cache_events),
                    "cached": True,
                    "next_retry_seconds": int(remain),
                }
            )
            self._save_google_runtime_state(now, force=False)
            return list(self._google_cache_events), meta
        if not gemini_enabled:
            events = self._fetch_google_http_search_events(trend_query, now_ts=now)
            self._google_last_fetch_ts = now
            self._google_last_error = "google_api_key_missing"
            self._google_cache_events = list(events[:120])
            meta.update(
                {
                    "status": "fallback_http",
                    "count": len(self._google_cache_events),
                    "cached": False,
                    "next_retry_seconds": int(self.google_trend_interval_seconds),
                    "error": "",
                }
            )
            self._save_google_runtime_state(now, force=True)
            self._record_google_feedback(
                now_ts=now,
                status="fallback_http",
                error="google_api_key_missing",
                action="Gemini 키 없음 -> Google RSS 폴백 사용",
                detail="Google API 키가 비어 있어 HTTP 검색으로 대체했습니다.",
                level="warn",
                meta=meta,
            )
            return list(self._google_cache_events), meta
        if now < int(self._google_backoff_until_ts):
            events = self._fetch_google_http_search_events(trend_query, now_ts=now)
            if events:
                self._google_last_fetch_ts = now
                self._google_cache_events = list(events[:120])
                meta.update(
                    {
                        "status": "fallback_http",
                        "count": len(self._google_cache_events),
                        "cached": False,
                        "next_retry_seconds": int(self.google_trend_interval_seconds),
                        "error": self._google_last_error or "gemini_cooldown",
                    }
                )
                self._save_google_runtime_state(now, force=True)
                self._record_google_feedback(
                    now_ts=now,
                    status="fallback_http",
                    error=str(meta.get("error") or "gemini_cooldown"),
                    action="쿨다운 동안 HTTP 폴백 유지",
                    detail="429 백오프 구간에서 Gemini 대신 HTTP 검색 결과를 사용합니다.",
                    level="warn",
                    meta=meta,
                )
                return list(self._google_cache_events), meta
            remain = int(self._google_backoff_until_ts - now)
            meta.update(
                {
                    "status": "cooldown",
                    "count": len(self._google_cache_events),
                    "cached": bool(self._google_cache_events),
                    "next_retry_seconds": remain,
                    "error": self._google_last_error or "google_rate_limited",
                }
            )
            self._save_google_runtime_state(now, force=True)
            self._record_google_feedback(
                now_ts=now,
                status="cooldown",
                error=str(meta.get("error") or ""),
                action="백오프 종료 시점까지 Gemini 호출 건너뜀",
                detail="쿨다운 시간 동안 캐시 결과만 반환합니다.",
                level="warn",
                meta=meta,
            )
            return list(self._google_cache_events), meta

        day_key = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%d")
        used_today = int(self._google_daily_calls.get(day_key) or 0)
        if used_today >= int(self._google_daily_max_calls):
            events = self._fetch_google_http_search_events(trend_query, now_ts=now)
            self._google_last_fetch_ts = now
            if events:
                self._google_cache_events = list(events[:120])
            meta.update(
                {
                    "status": "fallback_http",
                    "count": len(self._google_cache_events),
                    "cached": not bool(events),
                    "next_retry_seconds": int(self.google_trend_interval_seconds),
                    "error": "gemini_daily_cap_reached",
                }
            )
            self._save_google_runtime_state(now, force=True)
            self._record_google_feedback(
                now_ts=now,
                status="fallback_http",
                error="gemini_daily_cap_reached",
                action="일일 한도 도달 -> HTTP 폴백으로 전환",
                detail="오늘 Gemini 호출 한도에 도달하여 HTTP 검색을 사용합니다.",
                level="warn",
                meta=meta,
            )
            return list(self._google_cache_events), meta

        context_block = "\n".join([str(x).strip() for x in context_lines if str(x).strip()][:80])
        prompt = (
            "You are a memecoin trend extractor. Return strict JSON only.\n"
            "Schema:\n"
            "{\n"
            '  "symbols": ["TICKER", "..."],\n'
            '  "highlights": ["short summary lines"]\n'
            "}\n\n"
            "Rules:\n"
            "- symbols: uppercase ticker list, max "
            f"{int(self.google_trend_max_symbols)}\n"
            "- focus on memecoin momentum signals from the context\n"
            "- no markdown, no prose outside JSON\n\n"
            f"Query: {str(trend_query or '').strip()}\n"
            f"Context:\n{context_block}"
        )

        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": 600},
        }
        requested_model = str(self.google_model or "gemini-2.5-flash").strip()
        try_models: list[str] = [requested_model] if requested_model else ["gemini-2.5-flash"]

        body: dict[str, Any] = {}
        last_error = ""
        used_model = ""
        for model in try_models:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            try:
                res = self.session.post(
                    url,
                    params={"key": self.google_api_key},
                    json=payload,
                    timeout=self.timeout_seconds,
                )
                res.raise_for_status()
                parsed = res.json()
                body = parsed if isinstance(parsed, dict) else {}
                used_model = model
                self._google_daily_calls[day_key] = int(used_today + 1)
                self._trim_google_daily_calls(now)
                break
            except requests.HTTPError as exc:
                status = int(exc.response.status_code) if exc.response is not None else 0
                detail = ""
                if exc.response is not None:
                    try:
                        err_body = exc.response.json()
                    except Exception:
                        err_body = {}
                    if isinstance(err_body, dict):
                        err_obj = err_body.get("error") if isinstance(err_body.get("error"), dict) else {}
                        detail = str((err_obj or {}).get("message") or "").strip()
                    if not detail:
                        detail = str(exc.response.text or "").strip()[:180]
                last_error = (f"http_{status}:{detail}" if detail else f"http_{status}") if status else "http_error"
                if status == 429:
                    self._google_rate_limit_hits = min(8, int(self._google_rate_limit_hits) + 1)
                    retry_after = 0
                    if exc.response is not None:
                        try:
                            retry_after = int(exc.response.headers.get("Retry-After") or 0)
                        except Exception:
                            retry_after = 0
                    cooldown = max(
                        self.google_trend_cooldown_seconds,
                        retry_after,
                        int(self.google_trend_interval_seconds * (2 ** max(0, self._google_rate_limit_hits - 1))),
                    )
                    self._google_backoff_until_ts = now + int(cooldown)
                    err = f"rate_limited_429_cooldown_{int(cooldown)}s"
                    self._google_last_error = err
                    fallback_events = self._fetch_google_http_search_events(trend_query, now_ts=now)
                    if fallback_events:
                        self._google_last_fetch_ts = now
                        self._google_cache_events = list(fallback_events[:120])
                        meta.update(
                            {
                                "status": "fallback_http",
                                "count": len(self._google_cache_events),
                                "cached": False,
                                "next_retry_seconds": int(self.google_trend_interval_seconds),
                                "error": err,
                            }
                        )
                        self._save_google_runtime_state(now, force=True)
                        self._record_google_feedback(
                            now_ts=now,
                            status="fallback_http",
                            error=err,
                            action="429 감지 -> 쿨다운 저장 후 HTTP 폴백",
                            detail=f"Gemini 429로 {int(cooldown)}초 백오프를 적용했습니다.",
                            level="warn",
                            meta=meta,
                        )
                        return list(self._google_cache_events), meta
                    meta.update(
                        {
                            "status": "rate_limited",
                            "count": len(self._google_cache_events),
                            "cached": bool(self._google_cache_events),
                            "next_retry_seconds": int(cooldown),
                            "error": err,
                        }
                    )
                    self._save_google_runtime_state(now, force=True)
                    self._record_google_feedback(
                        now_ts=now,
                        status="rate_limited",
                        error=err,
                        action="쿨다운 유지, 다음 재시도까지 캐시 사용",
                        detail=f"Gemini 429로 {int(cooldown)}초 백오프 상태입니다.",
                        level="warn",
                        meta=meta,
                    )
                    return list(self._google_cache_events), meta
                if status == 400 and "api key not valid" in detail.lower():
                    self._google_last_error = "invalid_api_key"
                    meta.update(
                        {
                            "enabled": False,
                            "status": "disabled",
                            "count": len(self._google_cache_events),
                            "cached": bool(self._google_cache_events),
                            "next_retry_seconds": 0,
                            "error": "invalid_api_key",
                        }
                    )
                    self._save_google_runtime_state(now, force=True)
                    self._record_google_feedback(
                        now_ts=now,
                        status="disabled",
                        error="invalid_api_key",
                        action="Google API 키 검증 필요",
                        detail="API 키가 유효하지 않아 Gemini 호출을 중단했습니다.",
                        level="error",
                        meta=meta,
                    )
                    return list(self._google_cache_events), meta
                if status in {400, 404}:
                    continue
                self._google_last_error = last_error
                meta.update({"status": "error", "error": last_error})
                self._save_google_runtime_state(now, force=True)
                self._record_google_feedback(
                    now_ts=now,
                    status="error",
                    error=last_error,
                    action="요청 파라미터/모델명/네트워크 점검 후 재시도",
                    detail="Google API 호출 오류가 발생했습니다.",
                    level="error",
                    meta=meta,
                )
                return list(self._google_cache_events), meta
            except Exception as exc:
                last_error = str(exc)
                self._google_last_error = last_error
                meta.update({"status": "error", "error": last_error})
                self._save_google_runtime_state(now, force=True)
                self._record_google_feedback(
                    now_ts=now,
                    status="error",
                    error=last_error,
                    action="네트워크/타임아웃 상태 확인 후 재시도",
                    detail="Google API 일반 예외가 발생했습니다.",
                    level="error",
                    meta=meta,
                )
                return list(self._google_cache_events), meta
        if not body:
            self._google_last_error = last_error or "google_empty_response"
            meta.update({"status": "error", "error": self._google_last_error})
            self._save_google_runtime_state(now, force=True)
            self._record_google_feedback(
                now_ts=now,
                status="error",
                error=self._google_last_error,
                action="응답 포맷 확인 후 재시도",
                detail="Google API 응답 본문이 비어 있습니다.",
                level="error",
                meta=meta,
            )
            return list(self._google_cache_events), meta

        text_out = ""
        candidates = body.get("candidates") if isinstance(body, dict) else []
        if isinstance(candidates, list) and candidates:
            content = (candidates[0] or {}).get("content") if isinstance(candidates[0], dict) else {}
            parts = (content or {}).get("parts") if isinstance(content, dict) else []
            if isinstance(parts, list):
                for part in parts:
                    if not isinstance(part, dict):
                        continue
                    txt = str(part.get("text") or "").strip()
                    if txt:
                        text_out = txt
                        break

        parsed = self._extract_first_json_object(text_out)
        symbols_raw = parsed.get("symbols") if isinstance(parsed, dict) else []
        highlights_raw = parsed.get("highlights") if isinstance(parsed, dict) else []

        symbols: list[str] = []
        if isinstance(symbols_raw, list):
            for row in symbols_raw:
                sym = self._normalize_symbol(row)
                if not sym or sym in symbols:
                    continue
                symbols.append(sym)
                if len(symbols) >= self.google_trend_max_symbols:
                    break
        if not symbols:
            for sym in sorted(extract_symbols(text_out)):
                ns = self._normalize_symbol(sym)
                if not ns or ns in symbols:
                    continue
                symbols.append(ns)
                if len(symbols) >= self.google_trend_max_symbols:
                    break

        highlights: list[str] = []
        if isinstance(highlights_raw, list):
            for row in highlights_raw:
                txt = str(row or "").strip()
                if not txt:
                    continue
                highlights.append(txt[:160])
                if len(highlights) >= 20:
                    break

        events: list[TrendEvent] = []
        for sym in symbols:
            events.append(TrendEvent(source="google_gemini", symbol=sym, text=f"gemini: {sym}", ts=now))
        for line in highlights:
            line_symbols = extract_symbols(line)
            if not line_symbols and symbols:
                line_symbols = {symbols[0]}
            for sym in line_symbols:
                ns = self._normalize_symbol(sym)
                if not ns:
                    continue
                events.append(TrendEvent(source="google_gemini", symbol=ns, text=f"gemini: {line[:120]}", ts=now))

        self._google_last_fetch_ts = now
        self._google_backoff_until_ts = 0
        self._google_last_error = ""
        self._google_rate_limit_hits = 0
        self._google_cache_events = list(events[:120])
        meta.update(
            {
                "status": "ok",
                "count": len(self._google_cache_events),
                "cached": False,
                "next_retry_seconds": self.google_trend_interval_seconds,
                "error": "",
                "model": used_model or self.google_model,
            }
        )
        self._save_google_runtime_state(now, force=True)
        self._record_google_feedback(
            now_ts=now,
            status="ok",
            action="Gemini 정상 응답, 캐시/호출카운트 갱신",
            detail=f"model={used_model or self.google_model}",
            level="info",
            meta=meta,
        )
        return list(self._google_cache_events), meta

    def _fetch_google_http_search_events(self, trend_query: str, now_ts: int | None = None) -> list[TrendEvent]:
        now = int(now_ts or int(time.time()))
        query = str(trend_query or "").strip()
        if not query:
            query = '(memecoin OR "meme coin" OR $BONK OR $WIF OR $PEPE OR $FLOKI)'
        headers = {"User-Agent": "Mozilla/5.0 (compatible; AxiomFlowBot/1.0)"}
        try:
            res = self.session.get(
                "https://news.google.com/rss/search",
                params={"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"},
                headers=headers,
                timeout=self.timeout_seconds,
            )
            res.raise_for_status()
        except Exception:
            return []

        entries = self._parse_feed_entries(str(res.text or ""), max_items=20)
        out: list[TrendEvent] = []
        seen: set[tuple[str, str]] = set()
        for title, desc in entries:
            merged = f"{title} {desc}".strip()
            symbols = extract_symbols(merged)
            if not symbols:
                continue
            for sym in symbols:
                ns = self._normalize_symbol(sym)
                if not ns:
                    continue
                key = (ns, title[:80])
                if key in seen:
                    continue
                seen.add(key)
                out.append(
                    TrendEvent(
                        source="google_http",
                        symbol=ns,
                        text=f"google_http: {title[:120]}",
                        ts=now,
                    )
                )
                if len(out) >= 120:
                    return out
        return out


class SolscanProClient:
    BASE = "https://pro-api.solscan.io"

    def __init__(
        self,
        api_key: str = "",
        timeout_seconds: int = 10,
        monthly_cu_limit: int = 10_000_000,
        cu_per_request: int = 100,
        budget_window_seconds: int = 300,
        permission_backoff_seconds: int = 21600,
    ) -> None:
        self.api_key = str(api_key or "").strip()
        self.session = requests.Session()
        self.timeout_seconds = timeout_seconds
        self.monthly_cu_limit = max(1000, int(monthly_cu_limit))
        self.cu_per_request = max(1, int(cu_per_request))
        self.budget_window_seconds = max(60, int(budget_window_seconds))
        self.permission_backoff_seconds = max(300, int(permission_backoff_seconds))
        self._usage_lock = threading.Lock()
        self._usage_month_key = ""
        self._usage_month_used = 0
        self._usage_window_start_ts = 0
        self._usage_window_used = 0
        self._permission_backoff_until_ts = 0
        self._rate_limit_backoff_until_ts = 0
        self._last_error = ""

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    @staticmethod
    def _month_key_and_seconds(now_ts: int) -> tuple[str, int]:
        dt = datetime.fromtimestamp(int(now_ts), tz=timezone.utc)
        days = int(calendar.monthrange(dt.year, dt.month)[1])
        key = f"{dt.year:04d}-{dt.month:02d}"
        return key, int(days * 86400)

    def _window_cu_limit(self, month_seconds: int) -> int:
        windows = max(1, int((int(month_seconds) + int(self.budget_window_seconds) - 1) // int(self.budget_window_seconds)))
        return max(self.cu_per_request, int((int(self.monthly_cu_limit) + windows - 1) // windows))

    def _refresh_usage_state_locked(self, now_ts: int) -> int:
        month_key, month_seconds = self._month_key_and_seconds(now_ts)
        if month_key != self._usage_month_key:
            self._usage_month_key = month_key
            self._usage_month_used = 0
            self._usage_window_start_ts = int((int(now_ts) // int(self.budget_window_seconds)) * int(self.budget_window_seconds))
            self._usage_window_used = 0
        window_start = int((int(now_ts) // int(self.budget_window_seconds)) * int(self.budget_window_seconds))
        if int(self._usage_window_start_ts) != window_start:
            self._usage_window_start_ts = window_start
            self._usage_window_used = 0
        return int(month_seconds)

    def usage_snapshot(self) -> dict[str, Any]:
        now = int(time.time())
        with self._usage_lock:
            month_seconds = self._refresh_usage_state_locked(now)
            window_limit = self._window_cu_limit(month_seconds)
            next_window_seconds = max(1, int(self.budget_window_seconds) - max(0, now - int(self._usage_window_start_ts)))
            month_remaining = max(0, int(self.monthly_cu_limit) - int(self._usage_month_used))
            window_remaining = max(0, int(window_limit) - int(self._usage_window_used))
            backoff_until = max(int(self._permission_backoff_until_ts), int(self._rate_limit_backoff_until_ts))
            backoff_seconds = max(0, backoff_until - now)
            backoff_reason = ""
            if now < int(self._permission_backoff_until_ts):
                backoff_reason = "permission_level"
            elif now < int(self._rate_limit_backoff_until_ts):
                backoff_reason = "rate_limited"
            return {
                "enabled": bool(self.enabled),
                "month_key_utc": str(self._usage_month_key or ""),
                "monthly_limit_cu": int(self.monthly_cu_limit),
                "monthly_used_cu": int(self._usage_month_used),
                "monthly_remaining_cu": int(month_remaining),
                "window_seconds": int(self.budget_window_seconds),
                "window_limit_cu": int(window_limit),
                "window_used_cu": int(self._usage_window_used),
                "window_remaining_cu": int(window_remaining),
                "next_window_seconds": int(next_window_seconds),
                "cu_per_request": int(self.cu_per_request),
                "backoff_seconds": int(backoff_seconds),
                "backoff_reason": str(backoff_reason),
                "last_error": str(self._last_error or ""),
            }

    def _acquire_budget_or_raise(self) -> None:
        if not self.enabled:
            return
        now = int(time.time())
        with self._usage_lock:
            month_seconds = self._refresh_usage_state_locked(now)
            if now < int(self._permission_backoff_until_ts):
                remain = int(self._permission_backoff_until_ts) - now
                raise RuntimeError(f"solscan_permission_backoff_{max(1, remain)}s")
            if now < int(self._rate_limit_backoff_until_ts):
                remain = int(self._rate_limit_backoff_until_ts) - now
                raise RuntimeError(f"solscan_rate_limit_backoff_{max(1, remain)}s")
            window_limit = self._window_cu_limit(month_seconds)
            if int(self._usage_month_used) + int(self.cu_per_request) > int(self.monthly_cu_limit):
                raise RuntimeError("solscan_monthly_cu_exceeded")
            if int(self._usage_window_used) + int(self.cu_per_request) > int(window_limit):
                wait = max(1, int(self.budget_window_seconds) - max(0, now - int(self._usage_window_start_ts)))
                raise RuntimeError(f"solscan_window_cu_exceeded_{wait}s")
            self._usage_month_used += int(self.cu_per_request)
            self._usage_window_used += int(self.cu_per_request)

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            return {}
        return {
            "token": self.api_key,
            "Authorization": self.api_key,
            "Accept": "application/json",
        }

    def _get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        self._acquire_budget_or_raise()
        res = self.session.get(
            f"{self.BASE}{path}",
            params=params or {},
            headers=self._headers(),
            timeout=self.timeout_seconds,
        )
        if int(res.status_code) >= 400:
            code = int(res.status_code)
            raw = str(res.text or "")
            low = raw.lower()
            if code == 401 and ("upgrade your api key level" in low or "unauthorized" in low):
                with self._usage_lock:
                    self._permission_backoff_until_ts = max(
                        int(self._permission_backoff_until_ts),
                        int(time.time()) + int(self.permission_backoff_seconds),
                    )
                    self._last_error = "solscan_permission_level_insufficient"
                raise RuntimeError("solscan_permission_level_insufficient")
            if code == 429:
                retry_after = 0
                try:
                    retry_after = int(res.headers.get("Retry-After") or 0)
                except Exception:
                    retry_after = 0
                backoff = max(60, retry_after, int(self.budget_window_seconds))
                with self._usage_lock:
                    self._rate_limit_backoff_until_ts = max(
                        int(self._rate_limit_backoff_until_ts),
                        int(time.time()) + int(backoff),
                    )
                    self._last_error = f"solscan_rate_limited_{int(backoff)}s"
                raise RuntimeError(f"solscan_rate_limited_{int(backoff)}s")
            with self._usage_lock:
                self._last_error = f"http_{code}"
            res.raise_for_status()
        with self._usage_lock:
            self._last_error = ""
            self._rate_limit_backoff_until_ts = 0
        body = res.json()
        return body if isinstance(body, dict) else {}

    @staticmethod
    def _extract_list(body: dict[str, Any]) -> list[dict[str, Any]]:
        data = body.get("data")
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        if isinstance(data, dict):
            for key in ("items", "list", "result"):
                rows = data.get(key)
                if isinstance(rows, list):
                    return [x for x in rows if isinstance(x, dict)]
        for key in ("items", "list", "result"):
            rows = body.get(key)
            if isinstance(rows, list):
                return [x for x in rows if isinstance(x, dict)]
        return []

    @staticmethod
    def _as_float(row: dict[str, Any], keys: tuple[str, ...]) -> float:
        for k in keys:
            try:
                v = float(row.get(k) or 0.0)
            except Exception:
                v = 0.0
            if v > 0:
                return v
        token_amount = row.get("token_amount")
        if isinstance(token_amount, dict):
            for k in ("ui_amount", "amount", "value"):
                try:
                    v = float(token_amount.get(k) or 0.0)
                except Exception:
                    v = 0.0
                if v > 0:
                    return v
        return 0.0

    @staticmethod
    def _as_str(row: dict[str, Any], keys: tuple[str, ...]) -> str:
        for k in keys:
            v = str(row.get(k) or "").strip()
            if v:
                return v
        return ""

    def fetch_token_holders(self, token_address: str, page_size: int = 40) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        token = str(token_address or "").strip()
        if not token:
            return []
        body = self._get_json(
            "/v2.0/token/holders",
            params={"address": token, "page": 1, "page_size": max(10, min(100, int(page_size)))},
        )
        return self._extract_list(body)

    def fetch_token_transfers(self, token_address: str, page_size: int = 60) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        token = str(token_address or "").strip()
        if not token:
            return []
        body = self._get_json(
            "/v2.0/token/transfer",
            params={"address": token, "page": 1, "page_size": max(10, min(100, int(page_size)))},
        )
        return self._extract_list(body)

    def fetch_account_transfers(self, wallet_address: str, page_size: int = 20) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        wallet = str(wallet_address or "").strip()
        if not wallet:
            return []
        body = self._get_json(
            "/v2.0/account/transfer",
            params={"address": wallet, "page": 1, "page_size": max(5, min(100, int(page_size)))},
        )
        return self._extract_list(body)

    def analyze_wallet_pattern(self, token_address: str) -> dict[str, Any]:
        token = str(token_address or "").strip()
        if not token or not self.enabled:
            return {
                "token_address": token,
                "available": False,
                "error": "solscan_api_key_missing",
                "smart_wallet_score": 0.50,
                "holder_risk": 0.50,
            }

        holders = self.fetch_token_holders(token, page_size=60)
        transfers = self.fetch_token_transfers(token, page_size=80)

        amounts: list[float] = []
        owners: list[str] = []
        for row in holders:
            amt = self._as_float(
                row,
                ("amount", "holding_amount", "owner_amount", "ui_amount", "balance"),
            )
            if amt <= 0:
                continue
            owner = self._as_str(row, ("owner", "owner_address", "address", "wallet_address"))
            if not owner:
                continue
            amounts.append(amt)
            owners.append(owner)

        if not amounts:
            return {
                "token_address": token,
                "available": False,
                "error": "holders_empty",
                "smart_wallet_score": 0.45,
                "holder_risk": 0.55,
            }

        total = max(0.000001, float(sum(amounts)))
        owner_rows = [
            {"owner": str(owner), "amount": float(amount), "share": float(amount) / total}
            for owner, amount in zip(owners, amounts)
            if str(owner)
        ]
        owner_rows.sort(key=lambda row: float(row.get("share") or 0.0), reverse=True)
        shares = sorted([a / total for a in amounts], reverse=True)
        top1 = float(shares[0]) if shares else 1.0
        top5 = float(sum(shares[:5])) if shares else 1.0
        top10 = float(sum(shares[:10])) if shares else 1.0
        whale_count = sum(1 for s in shares if s >= 0.01)

        transfer_pairs: dict[str, int] = {}
        unique_wallets: set[str] = set()
        for row in transfers:
            src = self._as_str(row, ("src", "source", "from_address", "from", "owner"))
            dst = self._as_str(row, ("dst", "destination", "to_address", "to"))
            if src:
                unique_wallets.add(src)
            if dst:
                unique_wallets.add(dst)
            if src and dst:
                key = f"{src}->{dst}"
                transfer_pairs[key] = transfer_pairs.get(key, 0) + 1

        transfer_n = max(1, int(len(transfers)))
        max_pair_repeat = max(transfer_pairs.values()) if transfer_pairs else 0
        repeat_ratio = float(max_pair_repeat) / float(transfer_n)
        diversity = _clamp(float(len(unique_wallets)) / float(transfer_n * 1.4), 0.0, 1.0)

        concentration_risk = _clamp((top10 - 0.35) / 0.55, 0.0, 1.0)
        top1_risk = _clamp((top1 - 0.12) / 0.38, 0.0, 1.0)
        pair_risk = _clamp((repeat_ratio - 0.25) / 0.60, 0.0, 1.0)
        whale_risk = _clamp((float(whale_count) - 10.0) / 20.0, 0.0, 1.0)
        holder_risk = _clamp((0.45 * concentration_risk) + (0.25 * top1_risk) + (0.20 * pair_risk) + (0.10 * whale_risk), 0.0, 1.0)
        smart_wallet_score = _clamp((1.0 - holder_risk) * 0.75 + diversity * 0.25, 0.0, 1.0)

        return {
            "token_address": token,
            "available": True,
            "holders_count": int(len(amounts)),
            "transfers_count": int(len(transfers)),
            "top1_pct": top1 * 100.0,
            "top5_pct": top5 * 100.0,
            "top10_pct": top10 * 100.0,
            "whale_count_ge_1pct": int(whale_count),
            "transfer_repeat_ratio": repeat_ratio,
            "transfer_diversity": diversity,
            "holder_risk": holder_risk,
            "smart_wallet_score": smart_wallet_score,
            "suspicious": bool(holder_risk >= 0.68),
            "top_holder_wallets": [str(row.get("owner") or "") for row in owner_rows[:20] if str(row.get("owner") or "")],
            "top_holder_weights": {
                str(row.get("owner") or ""): float(row.get("share") or 0.0)
                for row in owner_rows[:20]
                if str(row.get("owner") or "")
            },
        }


class MacroMarketClient:
    def __init__(self, timeout_seconds: int = 10) -> None:
        self.session = requests.Session()
        self.timeout_seconds = timeout_seconds
        self._cache_rows: list[dict[str, Any]] = []
        self._cache_ts: float = 0.0
        self._cache_key: tuple[Any, ...] | None = None
        self._rt_cache_quotes: dict[str, float] = {}
        self._rt_cache_meta: dict[str, dict[str, Any]] = {}
        self._rt_cache_ts: float = 0.0
        self._rt_cache_key: tuple[str, ...] = ()
        self._kline_cache: dict[str, list[float]] = {}
        self._kline_cache_ts: dict[str, float] = {}
        self._kline_ohlc_cache: dict[str, list[dict[str, Any]]] = {}
        self._kline_ohlc_cache_ts: dict[str, float] = {}

    def fetch_top_markets(
        self,
        limit: int = 1000,
        source: str = "coingecko",
        cmc_api_key: str = "",
        coingecko_api_key: str = "",
    ) -> list[dict[str, Any]]:
        n = max(50, min(2000, int(limit)))
        src = str(source or "coingecko").lower()
        cache_key = (src, n, bool(str(cmc_api_key).strip()), bool(str(coingecko_api_key).strip()))
        now = time.time()
        # Free API limits are strict. Cache results for 5 minutes.
        if self._cache_rows and self._cache_key == cache_key and (now - self._cache_ts) < 300:
            return list(self._cache_rows)

        if src in {"coinmarketcap", "cmc"} and cmc_api_key:
            try:
                rows = self._fetch_cmc(n, cmc_api_key)
                if rows:
                    self._cache_rows = list(rows)
                    self._cache_ts = now
                    self._cache_key = cache_key
                    return rows
            except Exception:
                if self._cache_rows:
                    return list(self._cache_rows)

        try:
            rows = self._fetch_coingecko(n, coingecko_api_key)
        except Exception:
            if self._cache_rows:
                return list(self._cache_rows)
            raise
        if rows:
            self._cache_rows = list(rows)
            self._cache_ts = now
            self._cache_key = cache_key
        return rows

    def fetch_realtime_quotes(
        self,
        sources_csv: str = "binance,bybit",
        cache_seconds: int = 12,
        binance_api_key: str = "",
        binance_api_secret: str = "",
    ) -> tuple[dict[str, float], dict[str, dict[str, Any]]]:
        sources = [
            s.strip().lower()
            for s in str(sources_csv or "binance,bybit").split(",")
            if s.strip()
        ]
        if not sources:
            sources = ["binance", "bybit"]
        key = tuple(sorted(set(sources)))
        ttl = max(1, min(60, int(cache_seconds)))
        now = time.time()
        cache_key = key + (f"bk:{1 if str(binance_api_key or '').strip() else 0}",)
        if self._rt_cache_quotes and self._rt_cache_key == cache_key and (now - self._rt_cache_ts) < ttl:
            return dict(self._rt_cache_quotes), dict(self._rt_cache_meta)

        quotes: dict[str, float] = {}
        meta: dict[str, dict[str, Any]] = {}
        if "binance" in key:
            try:
                bq, bm = self._fetch_binance_quotes(
                    binance_api_key=binance_api_key,
                    binance_api_secret=binance_api_secret,
                )
                quotes.update(bq)
                meta.update(bm)
            except Exception:
                pass
        if "bybit" in key:
            try:
                yq, ym = self._fetch_bybit_public_quotes()
                for symbol, price in yq.items():
                    px = float(price or 0.0)
                    if px <= 0.0:
                        continue
                    prev = dict(meta.get(symbol) or {})
                    yrow = dict(ym.get(symbol) or {})
                    # For futures-style demo positions, prefer Bybit public ticker when available.
                    quotes[symbol] = px
                    meta[symbol] = {
                        "change_24h": float(yrow.get("change_24h") or prev.get("change_24h") or 0.0),
                        "volume_24h": max(float(yrow.get("volume_24h") or 0.0), float(prev.get("volume_24h") or 0.0)),
                        "realtime_source": "bybit_public",
                        "fallback_source": str(prev.get("realtime_source") or ""),
                    }
            except Exception:
                pass

        if quotes:
            self._rt_cache_quotes = dict(quotes)
            self._rt_cache_meta = dict(meta)
            self._rt_cache_ts = now
            self._rt_cache_key = cache_key
            return quotes, meta
        if self._rt_cache_quotes:
            return dict(self._rt_cache_quotes), dict(self._rt_cache_meta)
        return {}, {}

    def fetch_realtime_quotes_for_symbols(
        self,
        symbols: list[str] | tuple[str, ...] | set[str],
        sources_csv: str = "binance,bybit",
        binance_api_key: str = "",
        binance_api_secret: str = "",
    ) -> tuple[dict[str, float], dict[str, dict[str, Any]]]:
        requested: list[str] = []
        for raw in list(symbols or []):
            sym = str(raw or "").upper().strip()
            if not sym:
                continue
            if not sym.endswith("USDT"):
                sym = f"{sym}USDT"
            if sym not in requested:
                requested.append(sym)
        if not requested:
            return {}, {}

        sources = [
            s.strip().lower()
            for s in str(sources_csv or "binance,bybit").split(",")
            if s.strip()
        ]
        if not sources:
            sources = ["binance", "bybit"]
        key = tuple(sorted(set(sources)))

        quotes: dict[str, float] = {}
        meta: dict[str, dict[str, Any]] = {}
        if "binance" in key:
            for symbol in requested:
                try:
                    row = self._fetch_binance_quote(symbol, binance_api_key=binance_api_key)
                except Exception:
                    row = None
                if not isinstance(row, dict):
                    continue
                price = float(row.get("price") or 0.0)
                if price <= 0.0:
                    continue
                quotes[symbol] = price
                meta[symbol] = {
                    "change_24h": float(row.get("change_24h") or 0.0),
                    "volume_24h": float(row.get("volume_24h") or 0.0),
                    "realtime_source": "binance_symbol",
                    "api_key_auth": bool(str(binance_api_key or "").strip()),
                    "api_credentials_configured": bool(
                        str(binance_api_key or "").strip() and str(binance_api_secret or "").strip()
                    ),
                }
        if "bybit" in key:
            for symbol in requested:
                try:
                    row = self._fetch_bybit_public_quote(symbol)
                except Exception:
                    row = None
                if not isinstance(row, dict):
                    continue
                price = float(row.get("price") or 0.0)
                if price <= 0.0:
                    continue
                prev = dict(meta.get(symbol) or {})
                quotes[symbol] = price
                meta[symbol] = {
                    "change_24h": float(row.get("change_24h") or prev.get("change_24h") or 0.0),
                    "volume_24h": max(float(row.get("volume_24h") or 0.0), float(prev.get("volume_24h") or 0.0)),
                    "realtime_source": "bybit_public_symbol",
                    "fallback_source": str(prev.get("realtime_source") or ""),
                }
        return quotes, meta

    def _fetch_binance_quotes(
        self,
        binance_api_key: str = "",
        binance_api_secret: str = "",
    ) -> tuple[dict[str, float], dict[str, dict[str, Any]]]:
        quotes: dict[str, float] = {}
        meta: dict[str, dict[str, Any]] = {}
        api_key = str(binance_api_key or "").strip()
        api_secret = str(binance_api_secret or "").strip()
        auth_configured = bool(api_key and api_secret)
        headers: dict[str, str] = {}
        if api_key:
            headers["X-MBX-APIKEY"] = api_key
        res = self.session.get(
            "https://api.binance.com/api/v3/ticker/24hr",
            headers=headers or None,
            timeout=self.timeout_seconds,
        )
        res.raise_for_status()
        rows = res.json()
        if not isinstance(rows, list):
            return quotes, meta
        for row in rows:
            symbol = str(row.get("symbol") or "").upper().strip()
            if not symbol.endswith("USDT"):
                continue
            price = float(row.get("lastPrice") or 0.0)
            if price <= 0:
                continue
            quotes[symbol] = price
            meta[symbol] = {
                "change_24h": float(row.get("priceChangePercent") or 0.0),
                "volume_24h": float(row.get("quoteVolume") or 0.0),
                "realtime_source": "binance",
                "api_key_auth": bool(api_key),
                "api_credentials_configured": auth_configured,
            }
        return quotes, meta

    def _fetch_binance_quote(
        self,
        symbol: str,
        binance_api_key: str = "",
    ) -> dict[str, Any] | None:
        sym = str(symbol or "").upper().strip()
        if not sym:
            return None
        headers: dict[str, str] = {}
        api_key = str(binance_api_key or "").strip()
        if api_key:
            headers["X-MBX-APIKEY"] = api_key
        res = self.session.get(
            "https://api.binance.com/api/v3/ticker/24hr",
            params={"symbol": sym},
            headers=headers or None,
            timeout=self.timeout_seconds,
        )
        if res.status_code == 400:
            return None
        res.raise_for_status()
        row = res.json()
        if not isinstance(row, dict):
            return None
        price = float(row.get("lastPrice") or 0.0)
        if price <= 0.0:
            return None
        return {
            "symbol": sym,
            "price": float(price),
            "change_24h": float(row.get("priceChangePercent") or 0.0),
            "volume_24h": float(row.get("quoteVolume") or 0.0),
        }

    def _fetch_bybit_public_quotes(self) -> tuple[dict[str, float], dict[str, dict[str, Any]]]:
        quotes: dict[str, float] = {}
        meta: dict[str, dict[str, Any]] = {}
        res = self.session.get(
            "https://api.bybit.com/v5/market/tickers",
            params={"category": "linear"},
            timeout=self.timeout_seconds,
        )
        res.raise_for_status()
        body = res.json()
        rows = (((body or {}).get("result") or {}).get("list") or []) if isinstance(body, dict) else []
        if not isinstance(rows, list):
            return quotes, meta
        for row in rows:
            symbol = str(row.get("symbol") or "").upper().strip()
            if not symbol.endswith("USDT"):
                continue
            price = float(row.get("lastPrice") or 0.0)
            if price <= 0:
                continue
            quotes[symbol] = price
            meta[symbol] = {
                "change_24h": float(row.get("price24hPcnt") or 0.0) * 100.0,
                "volume_24h": float(row.get("turnover24h") or 0.0),
                "realtime_source": "bybit_public",
            }
        return quotes, meta

    def _fetch_bybit_public_quote(self, symbol: str) -> dict[str, Any] | None:
        sym = str(symbol or "").upper().strip()
        if not sym:
            return None
        res = self.session.get(
            "https://api.bybit.com/v5/market/tickers",
            params={"category": "linear", "symbol": sym},
            timeout=self.timeout_seconds,
        )
        res.raise_for_status()
        body = res.json()
        rows = (((body or {}).get("result") or {}).get("list") or []) if isinstance(body, dict) else []
        if not isinstance(rows, list):
            return None
        for row in rows:
            row_symbol = str(row.get("symbol") or "").upper().strip()
            if row_symbol != sym:
                continue
            price = float(row.get("lastPrice") or 0.0)
            if price <= 0.0:
                return None
            return {
                "symbol": sym,
                "price": float(price),
                "change_24h": float(row.get("price24hPcnt") or 0.0) * 100.0,
                "volume_24h": float(row.get("turnover24h") or 0.0),
            }
        return None

    def fetch_binance_5m_closes(
        self,
        symbol: str,
        limit: int = 360,
        cache_seconds: int = 90,
        binance_api_key: str = "",
    ) -> list[float]:
        sym = str(symbol or "").upper().strip()
        if not sym:
            return []
        n = max(60, min(1000, int(limit)))
        ttl = max(15, min(300, int(cache_seconds)))
        key = f"{sym}:5m:{n}:{1 if str(binance_api_key or '').strip() else 0}"
        now = time.time()
        cached = self._kline_cache.get(key) or []
        ts = float(self._kline_cache_ts.get(key) or 0.0)
        if cached and (now - ts) < ttl:
            return list(cached)

        headers: dict[str, str] = {}
        api_key = str(binance_api_key or "").strip()
        if api_key:
            headers["X-MBX-APIKEY"] = api_key
        res = self.session.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": sym, "interval": "5m", "limit": n},
            headers=headers or None,
            timeout=self.timeout_seconds,
        )
        res.raise_for_status()
        rows = res.json()
        if not isinstance(rows, list):
            return list(cached)
        closes: list[float] = []
        for row in rows:
            if not isinstance(row, list) or len(row) < 5:
                continue
            try:
                close_v = float(row[4] or 0.0)
            except Exception:
                close_v = 0.0
            if close_v > 0:
                closes.append(close_v)
        if len(closes) >= 20:
            self._kline_cache[key] = list(closes)
            self._kline_cache_ts[key] = now
            return closes
        return list(cached)

    def fetch_binance_1m_ohlc(
        self,
        symbol: str,
        limit: int = 20,
        cache_seconds: int = 30,
        binance_api_key: str = "",
    ) -> list[dict[str, Any]]:
        sym = str(symbol or "").upper().strip()
        if not sym:
            return []
        n = max(5, min(240, int(limit)))
        ttl = max(10, min(120, int(cache_seconds)))
        key = f"{sym}:1m:{n}:{1 if str(binance_api_key or '').strip() else 0}"
        now = time.time()
        cached = self._kline_ohlc_cache.get(key) or []
        ts = float(self._kline_ohlc_cache_ts.get(key) or 0.0)
        if cached and (now - ts) < ttl:
            return [dict(row) for row in cached]

        headers: dict[str, str] = {}
        api_key = str(binance_api_key or "").strip()
        if api_key:
            headers["X-MBX-APIKEY"] = api_key
        res = self.session.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": sym, "interval": "1m", "limit": n},
            headers=headers or None,
            timeout=self.timeout_seconds,
        )
        res.raise_for_status()
        rows = res.json()
        if not isinstance(rows, list):
            return [dict(row) for row in cached]

        candles: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, list) or len(row) < 7:
                continue
            try:
                open_ts = int(float(row[0]) / 1000.0)
                open_v = float(row[1] or 0.0)
                high_v = float(row[2] or 0.0)
                low_v = float(row[3] or 0.0)
                close_v = float(row[4] or 0.0)
                close_ts = int(float(row[6]) / 1000.0)
            except Exception:
                continue
            if min(open_v, high_v, low_v, close_v) <= 0.0:
                continue
            candles.append(
                {
                    "open_ts": open_ts,
                    "close_ts": close_ts,
                    "open": open_v,
                    "high": high_v,
                    "low": low_v,
                    "close": close_v,
                }
            )

        if candles:
            self._kline_ohlc_cache[key] = [dict(row) for row in candles]
            self._kline_ohlc_cache_ts[key] = now
            return candles
        return [dict(row) for row in cached]

    def _fetch_cmc(self, limit: int, api_key: str) -> list[dict[str, Any]]:
        url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"
        params = {
            "start": 1,
            "limit": max(1, min(5000, int(limit))),
            "convert": "USD",
        }
        headers = {"X-CMC_PRO_API_KEY": str(api_key).strip()}
        res = self.session.get(url, params=params, headers=headers, timeout=self.timeout_seconds)
        res.raise_for_status()
        body = res.json()
        rows = (body.get("data") or []) if isinstance(body, dict) else []
        out: list[dict[str, Any]] = []
        for row in rows:
            quote = (row.get("quote") or {}).get("USD") or {}
            out.append(
                {
                    "symbol": str(row.get("symbol") or "").upper(),
                    "name": str(row.get("name") or ""),
                    "price_usd": float(quote.get("price") or 0.0),
                    "market_cap": float(quote.get("market_cap") or 0.0),
                    "volume_24h": float(quote.get("volume_24h") or 0.0),
                    "change_1h": float(quote.get("percent_change_1h") or 0.0),
                    "change_24h": float(quote.get("percent_change_24h") or 0.0),
                    "market_cap_rank": int(row.get("cmc_rank") or 0),
                    "source": "coinmarketcap",
                }
            )
        return [r for r in out if r["symbol"] and r["price_usd"] > 0]

    def _fetch_coingecko(self, limit: int, api_key: str = "") -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        per_page = 250
        pages = (int(limit) + per_page - 1) // per_page
        for page in range(1, pages + 1):
            try:
                params = {
                    "vs_currency": "usd",
                    "order": "market_cap_desc",
                    "per_page": per_page,
                    "page": page,
                    "sparkline": "false",
                    "price_change_percentage": "1h,24h",
                }
                if api_key:
                    params["x_cg_demo_api_key"] = str(api_key).strip()
                res = self.session.get(
                    "https://api.coingecko.com/api/v3/coins/markets",
                    params=params,
                    timeout=self.timeout_seconds,
                )
                res.raise_for_status()
                rows = res.json()
            except Exception:
                # Keep partial data instead of failing the whole cycle.
                if out:
                    break
                raise
            if not isinstance(rows, list):
                break
            for idx, row in enumerate(rows):
                out.append(
                    {
                        "symbol": str(row.get("symbol") or "").upper(),
                        "name": str(row.get("name") or ""),
                        "price_usd": float(row.get("current_price") or 0.0),
                        "market_cap": float(row.get("market_cap") or 0.0),
                        "volume_24h": float(row.get("total_volume") or 0.0),
                        "change_1h": float(row.get("price_change_percentage_1h_in_currency") or 0.0),
                        "change_24h": float(row.get("price_change_percentage_24h_in_currency") or 0.0),
                        "market_cap_rank": int(row.get("market_cap_rank") or ((page - 1) * per_page + idx + 1)),
                        "source": "coingecko",
                    }
                )
            if len(out) >= int(limit):
                break
        return [r for r in out[:limit] if r["symbol"] and r["price_usd"] > 0]
