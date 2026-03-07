from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


RUNTIME_FEEDBACK_MAX_ROWS = 1_000_000
RUNTIME_FEEDBACK_MAX_AGE_SECONDS = 60 * 60 * 24 * 365
TREND_HISTORY_MAX_ROWS = 3_000_000
TREND_HISTORY_MAX_AGE_SECONDS = 60 * 60 * 24 * 365 * 2
TREND_SOURCE_HISTORY_MAX_ROWS = 1_000_000
MODEL_TUNE_HISTORY_MAX_ROWS = 2_000_000
MODEL_TUNE_HISTORY_MAX_AGE_SECONDS = 60 * 60 * 24 * 365 * 2
RUNTIME_FEEDBACK_PRUNE_INTERVAL_SECONDS = 300
TREND_HISTORY_PRUNE_INTERVAL_SECONDS = 600
MODEL_TUNE_PRUNE_INTERVAL_SECONDS = 900


class RuntimeFeedbackStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = str(db_path or "").strip() or "reports/runtime_feedback.db"
        self._lock = threading.RLock()
        self._last_event_prune_ts = 0
        self._last_trend_prune_ts = 0
        self._last_tune_prune_ts = 0
        path = Path(self.db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA cache_size=-20000")
        return conn

    def _init_schema(self) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS runtime_feedback_kv (
                        key TEXT PRIMARY KEY,
                        value_json TEXT NOT NULL,
                        updated_ts INTEGER NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS runtime_feedback_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ts INTEGER NOT NULL,
                        source TEXT NOT NULL,
                        level TEXT NOT NULL,
                        status TEXT NOT NULL,
                        error TEXT,
                        action TEXT,
                        detail TEXT,
                        meta_json TEXT
                    )
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_runtime_feedback_events_ts "
                    "ON runtime_feedback_events(ts DESC)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_runtime_feedback_events_source "
                    "ON runtime_feedback_events(source)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_runtime_feedback_events_source_id "
                    "ON runtime_feedback_events(source, id DESC)"
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS trend_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ts INTEGER NOT NULL,
                        market TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        hits INTEGER NOT NULL,
                        source_count INTEGER NOT NULL,
                        score REAL NOT NULL,
                        market_cap_usd REAL NOT NULL,
                        payload_json TEXT
                    )
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_trend_history_ts_market "
                    "ON trend_history(ts DESC, market)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_trend_history_market_ts "
                    "ON trend_history(market, ts DESC)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_trend_history_symbol "
                    "ON trend_history(symbol)"
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS trend_source_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ts INTEGER NOT NULL,
                        source TEXT NOT NULL,
                        status TEXT NOT NULL,
                        count INTEGER NOT NULL,
                        next_retry_seconds INTEGER NOT NULL,
                        error TEXT
                    )
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_trend_source_history_ts "
                    "ON trend_source_history(ts DESC)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_trend_source_history_source "
                    "ON trend_source_history(source)"
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS model_tune_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ts INTEGER NOT NULL,
                        market TEXT NOT NULL,
                        model_id TEXT NOT NULL,
                        model_name TEXT NOT NULL,
                        variant_id TEXT NOT NULL,
                        parent_variant_id TEXT NOT NULL,
                        tuned INTEGER NOT NULL,
                        note_code TEXT NOT NULL,
                        note_ko TEXT NOT NULL,
                        closed_trades INTEGER NOT NULL,
                        win_rate REAL NOT NULL,
                        pnl_usd REAL NOT NULL,
                        profit_factor REAL NOT NULL,
                        threshold_before REAL NOT NULL,
                        threshold_after REAL NOT NULL,
                        tp_mul_before REAL NOT NULL,
                        tp_mul_after REAL NOT NULL,
                        sl_mul_before REAL NOT NULL,
                        sl_mul_after REAL NOT NULL
                    )
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_model_tune_history_ts_market "
                    "ON model_tune_history(ts DESC, market)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_model_tune_history_market_model_ts "
                    "ON model_tune_history(market, model_id, ts DESC)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_model_tune_history_variant "
                    "ON model_tune_history(variant_id)"
                )

    @staticmethod
    def _should_prune(last_ts: int, now_ts: int, interval_seconds: int) -> bool:
        return int(now_ts) - int(last_ts) >= max(30, int(interval_seconds))

    def save_kv(self, key: str, value: dict[str, Any], now_ts: int | None = None) -> None:
        k = str(key or "").strip()
        if not k:
            return
        ts = int(now_ts or int(time.time()))
        payload = json.dumps(value or {}, ensure_ascii=True, separators=(",", ":"))
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO runtime_feedback_kv (key, value_json, updated_ts)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                      value_json=excluded.value_json,
                      updated_ts=excluded.updated_ts
                    """,
                    (k, payload, ts),
                )

    def load_kv(self, key: str) -> dict[str, Any]:
        k = str(key or "").strip()
        if not k:
            return {}
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT value_json FROM runtime_feedback_kv WHERE key = ?",
                    (k,),
                ).fetchone()
        if row is None:
            return {}
        try:
            obj = json.loads(str(row["value_json"] or "{}"))
        except Exception:
            return {}
        return obj if isinstance(obj, dict) else {}

    def append_event(
        self,
        *,
        source: str,
        level: str,
        status: str,
        error: str = "",
        action: str = "",
        detail: str = "",
        meta: dict[str, Any] | None = None,
        now_ts: int | None = None,
    ) -> None:
        ts = int(now_ts or int(time.time()))
        src = str(source or "").strip() or "runtime"
        lvl = str(level or "").strip().lower() or "info"
        st = str(status or "").strip() or "event"
        err = str(error or "").strip()
        act = str(action or "").strip()
        det = str(detail or "").strip()
        meta_json = json.dumps(meta or {}, ensure_ascii=True, separators=(",", ":"))
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO runtime_feedback_events
                    (ts, source, level, status, error, action, detail, meta_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (ts, src, lvl, st, err, act, det, meta_json),
                )
        with self._lock:
            do_prune = self._should_prune(
                int(self._last_event_prune_ts),
                int(ts),
                RUNTIME_FEEDBACK_PRUNE_INTERVAL_SECONDS,
            )
            if do_prune:
                self._last_event_prune_ts = int(ts)
        if do_prune:
            self.prune(now_ts=ts)

    def prune(
        self,
        *,
        now_ts: int | None = None,
        max_rows: int = RUNTIME_FEEDBACK_MAX_ROWS,
        max_age_seconds: int = RUNTIME_FEEDBACK_MAX_AGE_SECONDS,
    ) -> None:
        ts = int(now_ts or int(time.time()))
        with self._lock:
            with self._connect() as conn:
                if int(max_age_seconds) > 0:
                    cutoff = int(ts) - int(max_age_seconds)
                    conn.execute("DELETE FROM runtime_feedback_events WHERE ts < ?", (cutoff,))
                if int(max_rows) > 0:
                    row = conn.execute("SELECT COUNT(1) AS n FROM runtime_feedback_events").fetchone()
                    n = int(row["n"]) if isinstance(row, sqlite3.Row) and row is not None else int(row[0] if row else 0)
                    if n > int(max_rows):
                        drop = n - int(max_rows)
                        conn.execute(
                            """
                            DELETE FROM runtime_feedback_events
                            WHERE id IN (
                              SELECT id FROM runtime_feedback_events
                              ORDER BY id ASC
                              LIMIT ?
                            )
                            """,
                            (drop,),
                        )

    def recent_events(self, limit: int = 100, source: str = "") -> list[dict[str, Any]]:
        n = max(1, min(5000, int(limit)))
        src = str(source or "").strip()
        query = (
            "SELECT id, ts, source, level, status, error, action, detail, meta_json "
            "FROM runtime_feedback_events "
        )
        params: list[Any] = []
        if src:
            query += "WHERE source = ? "
            params.append(src)
        query += "ORDER BY id DESC LIMIT ?"
        params.append(n)
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(query, tuple(params)).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            meta_obj: dict[str, Any] = {}
            try:
                meta_obj = json.loads(str(row["meta_json"] or "{}"))
                if not isinstance(meta_obj, dict):
                    meta_obj = {}
            except Exception:
                meta_obj = {}
            out.append(
                {
                    "id": int(row["id"]),
                    "ts": int(row["ts"]),
                    "source": str(row["source"] or ""),
                    "level": str(row["level"] or ""),
                    "status": str(row["status"] or ""),
                    "error": str(row["error"] or ""),
                    "action": str(row["action"] or ""),
                    "detail": str(row["detail"] or ""),
                    "meta": meta_obj,
                }
            )
        return out

    def append_trend_points(self, market: str, rows: list[dict[str, Any]], now_ts: int | None = None) -> None:
        market_id = "meme" if str(market or "").strip().lower() == "meme" else "crypto"
        ts = int(now_ts or int(time.time()))
        payloads: list[tuple[Any, ...]] = []
        for row in list(rows or []):
            symbol = str((row or {}).get("symbol") or "").upper().strip()
            if not symbol:
                continue
            payloads.append(
                (
                    ts,
                    market_id,
                    symbol,
                    max(0, int((row or {}).get("hits") or 0)),
                    max(0, int((row or {}).get("source_count") or 0)),
                    float((row or {}).get("score") or 0.0),
                    max(0.0, float((row or {}).get("market_cap_usd") or 0.0)),
                    json.dumps((row or {}).get("payload") or {}, ensure_ascii=True, separators=(",", ":")),
                )
            )
        if not payloads:
            return
        with self._lock:
            with self._connect() as conn:
                conn.executemany(
                    """
                    INSERT INTO trend_history
                    (ts, market, symbol, hits, source_count, score, market_cap_usd, payload_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    payloads,
                )
        with self._lock:
            do_prune = self._should_prune(
                int(self._last_trend_prune_ts),
                int(ts),
                TREND_HISTORY_PRUNE_INTERVAL_SECONDS,
            )
            if do_prune:
                self._last_trend_prune_ts = int(ts)
        if do_prune:
            self.prune_trend(now_ts=ts)

    def append_trend_source_status(self, status_map: dict[str, Any], now_ts: int | None = None) -> None:
        ts = int(now_ts or int(time.time()))
        payloads: list[tuple[Any, ...]] = []
        for source, row in dict(status_map or {}).items():
            payloads.append(
                (
                    ts,
                    str(source or "").strip(),
                    str((row or {}).get("status") or "-"),
                    max(0, int((row or {}).get("count") or 0)),
                    max(0, int((row or {}).get("next_retry_seconds") or 0)),
                    str((row or {}).get("error") or ""),
                )
            )
        if not payloads:
            return
        with self._lock:
            with self._connect() as conn:
                conn.executemany(
                    """
                    INSERT INTO trend_source_history
                    (ts, source, status, count, next_retry_seconds, error)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    payloads,
                )
        with self._lock:
            do_prune = self._should_prune(
                int(self._last_trend_prune_ts),
                int(ts),
                TREND_HISTORY_PRUNE_INTERVAL_SECONDS,
            )
            if do_prune:
                self._last_trend_prune_ts = int(ts)
        if do_prune:
            self.prune_trend(now_ts=ts)

    def append_model_tune_event(self, row: dict[str, Any], now_ts: int | None = None) -> None:
        ts = int(now_ts or int(time.time()))
        payload = dict(row or {})
        market = "meme" if str(payload.get("market") or "").strip().lower() == "meme" else "crypto"
        model_id = str(payload.get("model_id") or "").strip().upper() or "A"
        model_name = str(payload.get("model_name") or "").strip() or model_id
        variant_id = str(payload.get("variant_id") or "").strip() or f"{model_id}-BASE"
        parent_variant_id = str(payload.get("parent_variant_id") or "").strip() or variant_id
        note_code = str(payload.get("note_code") or "").strip() or "-"
        note_ko = str(payload.get("note_ko") or "").strip() or note_code
        tuned = 1 if bool(payload.get("tuned")) else 0
        values = (
            ts,
            market,
            model_id,
            model_name,
            variant_id,
            parent_variant_id,
            tuned,
            note_code,
            note_ko,
            max(0, int(payload.get("closed_trades") or 0)),
            float(payload.get("win_rate") or 0.0),
            float(payload.get("pnl_usd") or 0.0),
            float(payload.get("profit_factor") or 0.0),
            float(payload.get("threshold_before") or 0.0),
            float(payload.get("threshold_after") or 0.0),
            float(payload.get("tp_mul_before") or 0.0),
            float(payload.get("tp_mul_after") or 0.0),
            float(payload.get("sl_mul_before") or 0.0),
            float(payload.get("sl_mul_after") or 0.0),
        )
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO model_tune_history
                    (ts, market, model_id, model_name, variant_id, parent_variant_id, tuned, note_code, note_ko,
                     closed_trades, win_rate, pnl_usd, profit_factor,
                     threshold_before, threshold_after, tp_mul_before, tp_mul_after, sl_mul_before, sl_mul_after)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
        with self._lock:
            do_prune = self._should_prune(
                int(self._last_tune_prune_ts),
                int(ts),
                MODEL_TUNE_PRUNE_INTERVAL_SECONDS,
            )
            if do_prune:
                self._last_tune_prune_ts = int(ts)
        if do_prune:
            self.prune_model_tune(now_ts=ts)

    def prune_trend(
        self,
        *,
        now_ts: int | None = None,
        max_rows: int = TREND_HISTORY_MAX_ROWS,
        max_age_seconds: int = TREND_HISTORY_MAX_AGE_SECONDS,
        source_max_rows: int = TREND_SOURCE_HISTORY_MAX_ROWS,
    ) -> None:
        ts = int(now_ts or int(time.time()))
        with self._lock:
            with self._connect() as conn:
                if int(max_age_seconds) > 0:
                    cutoff = int(ts) - int(max_age_seconds)
                    conn.execute("DELETE FROM trend_history WHERE ts < ?", (cutoff,))
                    conn.execute("DELETE FROM trend_source_history WHERE ts < ?", (cutoff,))
                if int(max_rows) > 0:
                    row = conn.execute("SELECT COUNT(1) AS n FROM trend_history").fetchone()
                    n = int(row["n"]) if isinstance(row, sqlite3.Row) and row is not None else int(row[0] if row else 0)
                    if n > int(max_rows):
                        drop = n - int(max_rows)
                        conn.execute(
                            """
                            DELETE FROM trend_history
                            WHERE id IN (
                              SELECT id FROM trend_history
                              ORDER BY id ASC
                              LIMIT ?
                            )
                            """,
                            (drop,),
                        )
                if int(source_max_rows) > 0:
                    row = conn.execute("SELECT COUNT(1) AS n FROM trend_source_history").fetchone()
                    n = int(row["n"]) if isinstance(row, sqlite3.Row) and row is not None else int(row[0] if row else 0)
                    if n > int(source_max_rows):
                        drop = n - int(source_max_rows)
                        conn.execute(
                            """
                            DELETE FROM trend_source_history
                            WHERE id IN (
                              SELECT id FROM trend_source_history
                              ORDER BY id ASC
                              LIMIT ?
                            )
                            """,
                            (drop,),
                        )

    def prune_model_tune(
        self,
        *,
        now_ts: int | None = None,
        max_rows: int = MODEL_TUNE_HISTORY_MAX_ROWS,
        max_age_seconds: int = MODEL_TUNE_HISTORY_MAX_AGE_SECONDS,
    ) -> None:
        ts = int(now_ts or int(time.time()))
        with self._lock:
            with self._connect() as conn:
                if int(max_age_seconds) > 0:
                    cutoff = int(ts) - int(max_age_seconds)
                    conn.execute("DELETE FROM model_tune_history WHERE ts < ?", (cutoff,))
                if int(max_rows) > 0:
                    row = conn.execute("SELECT COUNT(1) AS n FROM model_tune_history").fetchone()
                    n = int(row["n"]) if isinstance(row, sqlite3.Row) and row is not None else int(row[0] if row else 0)
                    if n > int(max_rows):
                        drop = n - int(max_rows)
                        conn.execute(
                            """
                            DELETE FROM model_tune_history
                            WHERE id IN (
                              SELECT id FROM model_tune_history
                              ORDER BY id ASC
                              LIMIT ?
                            )
                            """,
                            (drop,),
                        )

    def delete_trend_before(self, cutoff_ts: int, market: str = "") -> dict[str, int]:
        cutoff = max(0, int(cutoff_ts))
        market_id = str(market or "").strip().lower()
        if market_id not in {"meme", "crypto"}:
            market_id = ""
        deleted_trend = 0
        deleted_source = 0
        with self._lock:
            with self._connect() as conn:
                if market_id:
                    cur = conn.execute(
                        "DELETE FROM trend_history WHERE ts < ? AND market = ?",
                        (cutoff, market_id),
                    )
                else:
                    cur = conn.execute("DELETE FROM trend_history WHERE ts < ?", (cutoff,))
                deleted_trend = int(cur.rowcount or 0)
                cur = conn.execute("DELETE FROM trend_source_history WHERE ts < ?", (cutoff,))
                deleted_source = int(cur.rowcount or 0)
        return {
            "cutoff_ts": int(cutoff),
            "deleted_trend_rows": int(max(0, deleted_trend)),
            "deleted_source_rows": int(max(0, deleted_source)),
        }

    def trend_stats(self) -> dict[str, Any]:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT COUNT(1) AS n, MIN(ts) AS min_ts, MAX(ts) AS max_ts FROM trend_history"
                ).fetchone()
        n = int(row["n"]) if isinstance(row, sqlite3.Row) and row is not None else 0
        min_ts = int(row["min_ts"] or 0) if isinstance(row, sqlite3.Row) and row is not None else 0
        max_ts = int(row["max_ts"] or 0) if isinstance(row, sqlite3.Row) and row is not None else 0
        return {"total_rows": n, "min_ts": min_ts, "max_ts": max_ts}

    @staticmethod
    def _local_offset_seconds() -> int:
        try:
            offset = datetime.now().astimezone().utcoffset()
            if offset is None:
                return 0
            return int(offset.total_seconds())
        except Exception:
            return 0

    @staticmethod
    def _bucket_floor(ts: int, bucket_seconds: int, tz_offset_seconds: int = 0) -> int:
        b = max(60, int(bucket_seconds))
        raw = int(ts) + int(tz_offset_seconds)
        floored = int(raw // b * b)
        return int(floored - int(tz_offset_seconds))

    def trend_share_distribution(
        self,
        market: str,
        *,
        lookback_seconds: int = 60 * 60 * 24,
        top_n: int = 8,
        min_share_pct: float = 2.0,
        exclude_symbols: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        market_id = "meme" if str(market or "").strip().lower() == "meme" else "crypto"
        now_ts = int(time.time())
        start_ts = int(now_ts - max(3600, int(lookback_seconds)))
        excluded = set(str(s or "").upper().strip() for s in list(exclude_symbols or []) if str(s or "").strip())
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT symbol, SUM(hits) AS hits
                    FROM trend_history
                    WHERE market = ? AND ts >= ?
                    GROUP BY symbol
                    ORDER BY hits DESC
                    LIMIT 400
                    """,
                    (market_id, start_ts),
                ).fetchall()
        agg: list[tuple[str, int]] = []
        for row in rows:
            sym = str(row["symbol"] or "").upper().strip()
            if not sym or sym in excluded:
                continue
            hits = max(0, int(row["hits"] or 0))
            if hits <= 0:
                continue
            agg.append((sym, hits))
        if not agg:
            return []
        total_hits = int(sum(v for _, v in agg))
        if total_hits <= 0:
            return []
        keep_cap = max(3, min(20, int(top_n)))
        min_pct = max(0.0, min(40.0, float(min_share_pct)))
        kept: list[tuple[str, int]] = []
        etc_hits = 0
        for idx, (sym, hits) in enumerate(agg):
            share = float(100.0 * float(hits) / float(total_hits))
            if idx < keep_cap and (idx == 0 or share >= min_pct):
                kept.append((sym, hits))
            else:
                etc_hits += int(hits)
        if etc_hits > 0:
            kept.append(("ETC", int(etc_hits)))
        out: list[dict[str, Any]] = []
        for sym, hits in kept:
            out.append(
                {
                    "symbol": str(sym),
                    "hits": int(hits),
                    "share_pct": round(float(100.0 * float(hits) / float(total_hits)), 4),
                    "total_hits": int(total_hits),
                }
            )
        return out

    def trend_period_summary(
        self,
        market: str,
        *,
        bucket_seconds: int,
        lookback_seconds: int,
        top_n: int = 5,
        min_share_pct: float = 2.0,
        exclude_symbols: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        market_id = "meme" if str(market or "").strip().lower() == "meme" else "crypto"
        bucket = max(60, int(bucket_seconds))
        now_ts = int(time.time())
        start_ts = int(now_ts - max(bucket, int(lookback_seconds)))
        excluded = set(str(s or "").upper().strip() for s in list(exclude_symbols or []) if str(s or "").strip())
        offset = self._local_offset_seconds()
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT ts, symbol, hits
                    FROM trend_history
                    WHERE market = ? AND ts >= ?
                    ORDER BY ts ASC
                    """,
                    (market_id, start_ts),
                ).fetchall()
        buckets: dict[int, dict[str, Any]] = {}
        global_hits: dict[str, int] = {}
        for row in rows:
            ts = int(row["ts"] or 0)
            sym = str(row["symbol"] or "").upper().strip()
            if ts <= 0 or not sym or sym in excluded:
                continue
            bts = self._bucket_floor(ts, bucket, offset)
            hit = max(1, int(row["hits"] or 0))
            slot = buckets.get(bts)
            if slot is None:
                slot = {"total_hits": 0, "symbol_hits": {}}
                buckets[bts] = slot
            slot["total_hits"] = int(slot["total_hits"]) + hit
            sym_hits = dict(slot["symbol_hits"] or {})
            sym_hits[sym] = int(sym_hits.get(sym, 0)) + hit
            slot["symbol_hits"] = sym_hits
            global_hits[sym] = int(global_hits.get(sym, 0)) + hit
        if not buckets:
            return []
        top_symbols = [
            sym
            for sym, _ in sorted(global_hits.items(), key=lambda it: int(it[1]), reverse=True)[: max(3, min(10, int(top_n)))]
        ]
        min_pct = max(0.0, min(40.0, float(min_share_pct)))
        out: list[dict[str, Any]] = []
        for bts in sorted(buckets.keys()):
            slot = dict(buckets.get(bts) or {})
            total_hits = int(slot.get("total_hits") or 0)
            symbol_hits = dict(slot.get("symbol_hits") or {})
            if total_hits <= 0:
                continue
            breakdown: list[dict[str, Any]] = []
            etc_hits = 0
            for sym in top_symbols:
                hit = int(symbol_hits.get(sym) or 0)
                if hit <= 0:
                    continue
                share = float(100.0 * float(hit) / float(total_hits))
                if share < min_pct and len(breakdown) >= 2:
                    etc_hits += hit
                    continue
                breakdown.append({"symbol": sym, "hits": hit, "share_pct": round(share, 4)})
            for sym, hit in symbol_hits.items():
                if sym in top_symbols:
                    continue
                etc_hits += int(hit)
            if etc_hits > 0:
                breakdown.append(
                    {
                        "symbol": "ETC",
                        "hits": int(etc_hits),
                        "share_pct": round(float(100.0 * float(etc_hits) / float(total_hits)), 4),
                    }
                )
            breakdown.sort(key=lambda r: int(r.get("hits") or 0), reverse=True)
            top_symbol = str(breakdown[0]["symbol"]) if breakdown else "-"
            top_hits = int(breakdown[0]["hits"]) if breakdown else 0
            top_share = float(breakdown[0]["share_pct"]) if breakdown else 0.0
            breakdown_text = " | ".join(
                f"{str(r.get('symbol') or '-')} {float(r.get('share_pct') or 0.0):.1f}%"
                for r in breakdown[:6]
            )
            out.append(
                {
                    "ts": int(bts),
                    "label": datetime.fromtimestamp(int(bts), tz=timezone.utc).astimezone().strftime("%m-%d %H:%M"),
                    "total_hits": int(total_hits),
                    "top_symbol": top_symbol,
                    "top_hits": int(top_hits),
                    "top_share_pct": round(float(top_share), 4),
                    "breakdown": breakdown,
                    "breakdown_text": breakdown_text,
                }
            )
        return out

    def trend_bucket_series(
        self,
        market: str,
        *,
        lookback_seconds: int = 60 * 60 * 24,
        bucket_seconds: int = 1800,
    ) -> list[dict[str, Any]]:
        market_id = "meme" if str(market or "").strip().lower() == "meme" else "crypto"
        now_ts = int(time.time())
        start_ts = int(now_ts - max(3600, int(lookback_seconds)))
        bucket = max(60, int(bucket_seconds))
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT ts, symbol, hits
                    FROM trend_history
                    WHERE market = ? AND ts >= ?
                    ORDER BY ts ASC
                    """,
                    (market_id, start_ts),
                ).fetchall()
        slots: dict[int, dict[str, Any]] = {}
        bucket_start = int(start_ts // bucket * bucket)
        bucket_end = int(now_ts // bucket * bucket)
        for ts in range(bucket_start, bucket_end + bucket, bucket):
            slots[int(ts)] = {"hits": 0, "symbol_hits": {}}
        for row in rows:
            ts = int(row["ts"] or 0)
            bts = int(ts // bucket * bucket)
            if bts not in slots:
                continue
            hit = max(1, int(row["hits"] or 0))
            sym = str(row["symbol"] or "").upper()
            slots[bts]["hits"] = int(slots[bts]["hits"]) + hit
            table = dict(slots[bts]["symbol_hits"] or {})
            table[sym] = int(table.get(sym, 0)) + hit
            slots[bts]["symbol_hits"] = table
        out: list[dict[str, Any]] = []
        for bts in sorted(slots.keys()):
            table = dict(slots[bts]["symbol_hits"] or {})
            top_symbol = ""
            top_hits = 0
            if table:
                top_symbol, top_hits = max(table.items(), key=lambda it: int(it[1]))
            out.append(
                {
                    "ts": int(bts),
                    "label": time.strftime("%m-%d %H:%M", time.gmtime(int(bts))),
                    "hits": int(slots[bts]["hits"]),
                    "top_symbol": str(top_symbol),
                    "top_hits": int(top_hits),
                }
            )
        return out

    def trend_rank(
        self,
        market: str,
        *,
        lookback_seconds: int = 60 * 60 * 24,
        limit: int = 40,
    ) -> list[dict[str, Any]]:
        market_id = "meme" if str(market or "").strip().lower() == "meme" else "crypto"
        now_ts = int(time.time())
        start_ts = int(now_ts - max(3600, int(lookback_seconds)))
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT ts, symbol, hits, source_count, score, market_cap_usd
                    FROM trend_history
                    WHERE market = ? AND ts >= ?
                    ORDER BY ts DESC
                    LIMIT 20000
                    """,
                    (market_id, start_ts),
                ).fetchall()
        agg: dict[str, dict[str, Any]] = {}
        for row in rows:
            sym = str(row["symbol"] or "").upper().strip()
            if not sym:
                continue
            slot = agg.get(sym)
            if slot is None:
                slot = {
                    "symbol": sym,
                    "hits": 0,
                    "source_count": 0,
                    "score": float(row["score"] or 0.0),
                    "market_cap_usd": float(row["market_cap_usd"] or 0.0),
                    "last_seen_ts": int(row["ts"] or 0),
                }
                agg[sym] = slot
            slot["hits"] = int(slot["hits"]) + max(1, int(row["hits"] or 0))
            slot["source_count"] = max(int(slot["source_count"]), int(row["source_count"] or 0))
            slot["score"] = max(float(slot["score"]), float(row["score"] or 0.0))
            if float(row["market_cap_usd"] or 0.0) > 0:
                slot["market_cap_usd"] = float(row["market_cap_usd"] or 0.0)
            slot["last_seen_ts"] = max(int(slot["last_seen_ts"]), int(row["ts"] or 0))
        ranked = sorted(
            list(agg.values()),
            key=lambda r: (int(r.get("hits") or 0), float(r.get("score") or 0.0), int(r.get("last_seen_ts") or 0)),
            reverse=True,
        )
        return ranked[: max(5, min(300, int(limit)))]

    def trend_source_recent(self, limit: int = 120) -> list[dict[str, Any]]:
        n = max(1, min(2000, int(limit)))
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT ts, source, status, count, next_retry_seconds, error
                    FROM trend_source_history
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (n,),
                ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    "ts": int(row["ts"] or 0),
                    "source": str(row["source"] or ""),
                    "status": str(row["status"] or ""),
                    "count": int(row["count"] or 0),
                    "next_retry_seconds": int(row["next_retry_seconds"] or 0),
                    "error": str(row["error"] or ""),
                }
            )
        return out

    def model_tune_recent(self, *, market: str = "crypto", limit: int = 240) -> list[dict[str, Any]]:
        market_id = "meme" if str(market or "").strip().lower() == "meme" else "crypto"
        n = max(1, min(5000, int(limit)))
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT ts, market, model_id, model_name, variant_id, parent_variant_id, tuned, note_code, note_ko,
                           closed_trades, win_rate, pnl_usd, profit_factor,
                           threshold_before, threshold_after, tp_mul_before, tp_mul_after, sl_mul_before, sl_mul_after
                    FROM model_tune_history
                    WHERE market = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (market_id, n),
                ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    "ts": int(row["ts"] or 0),
                    "market": str(row["market"] or ""),
                    "model_id": str(row["model_id"] or ""),
                    "model_name": str(row["model_name"] or ""),
                    "variant_id": str(row["variant_id"] or ""),
                    "parent_variant_id": str(row["parent_variant_id"] or ""),
                    "tuned": bool(int(row["tuned"] or 0)),
                    "note_code": str(row["note_code"] or ""),
                    "note_ko": str(row["note_ko"] or ""),
                    "closed_trades": int(row["closed_trades"] or 0),
                    "win_rate": float(row["win_rate"] or 0.0),
                    "pnl_usd": float(row["pnl_usd"] or 0.0),
                    "profit_factor": float(row["profit_factor"] or 0.0),
                    "threshold_before": float(row["threshold_before"] or 0.0),
                    "threshold_after": float(row["threshold_after"] or 0.0),
                    "tp_mul_before": float(row["tp_mul_before"] or 0.0),
                    "tp_mul_after": float(row["tp_mul_after"] or 0.0),
                    "sl_mul_before": float(row["sl_mul_before"] or 0.0),
                    "sl_mul_after": float(row["sl_mul_after"] or 0.0),
                }
            )
        return out

    def model_tune_variant_rank(
        self,
        *,
        market: str = "crypto",
        lookback_seconds: int = 60 * 60 * 24 * 180,
        limit: int = 120,
    ) -> list[dict[str, Any]]:
        market_id = "meme" if str(market or "").strip().lower() == "meme" else "crypto"
        now_ts = int(time.time())
        start_ts = int(now_ts - max(3600, int(lookback_seconds)))
        n = max(5, min(500, int(limit)))
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT ts, model_id, model_name, variant_id, tuned, note_ko, pnl_usd, win_rate, profit_factor
                    FROM model_tune_history
                    WHERE market = ? AND ts >= ?
                    ORDER BY ts ASC
                    """,
                    (market_id, start_ts),
                ).fetchall()
        agg: dict[str, dict[str, Any]] = {}
        for row in rows:
            vid = str(row["variant_id"] or "").strip()
            if not vid:
                continue
            slot = agg.get(vid)
            if slot is None:
                slot = {
                    "variant_id": vid,
                    "model_id": str(row["model_id"] or ""),
                    "model_name": str(row["model_name"] or ""),
                    "eval_count": 0,
                    "tuned_count": 0,
                    "sum_pnl_usd": 0.0,
                    "avg_pnl_usd": 0.0,
                    "last_pnl_usd": 0.0,
                    "last_win_rate": 0.0,
                    "last_profit_factor": 0.0,
                    "last_note_ko": "",
                    "last_ts": 0,
                }
                agg[vid] = slot
            slot["eval_count"] = int(slot["eval_count"]) + 1
            if bool(int(row["tuned"] or 0)):
                slot["tuned_count"] = int(slot["tuned_count"]) + 1
            pnl = float(row["pnl_usd"] or 0.0)
            slot["sum_pnl_usd"] = float(slot["sum_pnl_usd"]) + pnl
            ts = int(row["ts"] or 0)
            if ts >= int(slot["last_ts"]):
                slot["last_ts"] = ts
                slot["last_pnl_usd"] = pnl
                slot["last_win_rate"] = float(row["win_rate"] or 0.0)
                slot["last_profit_factor"] = float(row["profit_factor"] or 0.0)
                slot["last_note_ko"] = str(row["note_ko"] or "")
        out: list[dict[str, Any]] = []
        for slot in agg.values():
            cnt = max(1, int(slot.get("eval_count") or 0))
            slot["avg_pnl_usd"] = float(slot.get("sum_pnl_usd") or 0.0) / float(cnt)
            out.append(slot)
        out.sort(
            key=lambda r: (float(r.get("avg_pnl_usd") or 0.0), float(r.get("last_pnl_usd") or 0.0), int(r.get("last_ts") or 0)),
            reverse=True,
        )
        ranked = out[:n]
        for idx, row in enumerate(ranked, start=1):
            row["rank"] = int(idx)
        return ranked
