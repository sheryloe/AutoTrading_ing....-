from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.runtime_feedback import RuntimeFeedbackStore


TABLES: dict[str, dict[str, object]] = {
    "runtime_feedback_kv": {
        "columns": ["key", "value_json", "updated_ts"],
        "order_by": "key ASC",
    },
    "runtime_feedback_events": {
        "columns": ["ts", "source", "level", "status", "error", "action", "detail", "meta_json"],
        "order_by": "id ASC",
    },
    "trend_history": {
        "columns": ["ts", "market", "symbol", "hits", "source_count", "score", "market_cap_usd", "payload_json"],
        "order_by": "id ASC",
    },
    "trend_source_history": {
        "columns": ["ts", "source", "status", "count", "next_retry_seconds", "error"],
        "order_by": "id ASC",
    },
    "model_tune_history": {
        "columns": [
            "ts",
            "market",
            "model_id",
            "model_name",
            "variant_id",
            "parent_variant_id",
            "tuned",
            "note_code",
            "note_ko",
            "closed_trades",
            "win_rate",
            "pnl_usd",
            "profit_factor",
            "threshold_before",
            "threshold_after",
            "tp_mul_before",
            "tp_mul_after",
            "sl_mul_before",
            "sl_mul_after",
        ],
        "order_by": "id ASC",
    },
    "meme_score_history": {
        "columns": [
            "ts",
            "model_id",
            "symbol",
            "name",
            "token_address",
            "score",
            "grade",
            "probability",
            "price_usd",
            "liquidity_usd",
            "volume_5m_usd",
            "market_cap_usd",
            "age_minutes",
            "reason",
            "source",
        ],
        "order_by": "id ASC",
    },
}


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _copy_table(src_conn: sqlite3.Connection, dst_conn: sqlite3.Connection, table: str, columns: list[str], order_by: str) -> int:
    col_sql = ", ".join(columns)
    placeholders = ", ".join("?" for _ in columns)
    select_sql = f"SELECT {col_sql} FROM {table} ORDER BY {order_by}"
    insert_sql = f"INSERT INTO {table} ({col_sql}) VALUES ({placeholders})"
    copied = 0
    cur = src_conn.execute(select_sql)
    while True:
        rows = cur.fetchmany(2000)
        if not rows:
            break
        payload = [tuple(row[col] for col in columns) for row in rows]
        dst_conn.executemany(insert_sql, payload)
        copied += len(payload)
    return copied


def repair_db(src_path: Path, repaired_path: Path) -> None:
    if repaired_path.exists():
        repaired_path.unlink()
    RuntimeFeedbackStore(str(repaired_path))
    with _connect(src_path) as src_conn, _connect(repaired_path) as dst_conn:
        for table, meta in TABLES.items():
            copied = _copy_table(
                src_conn,
                dst_conn,
                table,
                list(meta["columns"]),
                str(meta["order_by"]),
            )
            print(f"{table}: copied={copied}")
        dst_conn.commit()
        dst_conn.execute("VACUUM")
        dst_conn.commit()
        check = dst_conn.execute("PRAGMA integrity_check").fetchall()
        print("integrity_check:", check[:1])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="reports/runtime_feedback.db")
    parser.add_argument("--output", default="reports/runtime_feedback.repaired.db")
    parser.add_argument("--swap", action="store_true")
    args = parser.parse_args()

    src_path = Path(args.source)
    repaired_path = Path(args.output)
    if not src_path.exists():
        raise SystemExit(f"source db not found: {src_path}")

    repair_db(src_path, repaired_path)

    if args.swap:
        stamp = int(time.time())
        backup_path = src_path.with_name(f"{src_path.name}.corrupt_{stamp}.bak")
        shutil.copy2(src_path, backup_path)
        shutil.move(str(repaired_path), str(src_path))
        print(f"swapped: backup={backup_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
