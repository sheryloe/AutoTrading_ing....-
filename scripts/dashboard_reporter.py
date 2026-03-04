from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(v: Any) -> float:
    try:
        return float(v)
    except Exception:
        return 0.0


def _build_summary(payload: dict[str, Any]) -> dict[str, Any]:
    models = list(payload.get("model_runs") or [])
    out_models: list[dict[str, Any]] = []
    for row in models:
        out_models.append(
            {
                "model_id": str(row.get("model_id") or ""),
                "total_equity_usd": _safe_float(row.get("total_equity_usd")),
                "total_pnl_usd": _safe_float(row.get("total_pnl_usd")),
                "realized_pnl_usd": _safe_float(row.get("realized_pnl_usd")),
                "unrealized_pnl_usd": _safe_float(row.get("unrealized_pnl_usd")),
                "win_rate": _safe_float(row.get("win_rate")),
                "open_meme_positions": int(row.get("open_meme_positions") or 0),
                "open_bybit_positions": int(row.get("open_bybit_positions") or 0),
            }
        )
    return {
        "captured_at_utc": _utc_now_iso(),
        "server_time": int(payload.get("server_time") or 0),
        "running": bool(payload.get("running")),
        "demo_seed_usdt": _safe_float(payload.get("demo_seed_usdt")),
        "models": out_models,
        "daily_pnl_rows": len(list(payload.get("daily_pnl") or [])),
        "signals_count": len(list(payload.get("signals") or [])),
    }


def _max_drawdown_pct(equities: list[float]) -> float:
    if not equities:
        return 0.0
    peak = float(equities[0])
    worst = 0.0
    for e in equities:
        v = float(e)
        if v > peak:
            peak = v
        if peak > 0:
            dd = (v - peak) / peak
            if dd < worst:
                worst = dd
    return worst * 100.0


def _load_recent_snapshots(path: Path, lookback_seconds: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not path.exists():
        return out
    cutoff = int(time.time()) - max(60, int(lookback_seconds))
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            body = line.strip()
            if not body:
                continue
            try:
                row = json.loads(body)
            except Exception:
                continue
            if not isinstance(row, dict):
                continue
            ts = int(row.get("server_time") or 0)
            if ts <= 0 or ts < cutoff:
                continue
            out.append(row)
    out.sort(key=lambda x: int(x.get("server_time") or 0))
    return out


def _build_rolling_validation(rows: list[dict[str, Any]], lookback_days: int) -> dict[str, Any]:
    model_series: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        for m in list(row.get("models") or []):
            model_id = str(m.get("model_id") or "").strip()
            if not model_id:
                continue
            model_series.setdefault(model_id, []).append(m)

    models_out: list[dict[str, Any]] = []
    for model_id in sorted(model_series.keys()):
        series = model_series[model_id]
        equities = [float(x.get("total_equity_usd") or 0.0) for x in series]
        if not equities:
            continue
        start_equity = float(equities[0])
        end_equity = float(equities[-1])
        pnl = end_equity - start_equity
        ret_pct = (pnl / start_equity * 100.0) if start_equity > 0 else 0.0
        mdd_pct = _max_drawdown_pct(equities)
        latest = series[-1]
        models_out.append(
            {
                "model_id": model_id,
                "samples": len(series),
                "start_equity_usd": round(start_equity, 6),
                "end_equity_usd": round(end_equity, 6),
                "pnl_usd": round(pnl, 6),
                "return_pct": round(ret_pct, 4),
                "max_drawdown_pct": round(mdd_pct, 4),
                "latest_win_rate": round(float(latest.get("win_rate") or 0.0), 4),
                "latest_realized_pnl_usd": round(float(latest.get("realized_pnl_usd") or 0.0), 6),
                "latest_unrealized_pnl_usd": round(float(latest.get("unrealized_pnl_usd") or 0.0), 6),
                "pass_gate": bool(ret_pct >= 0.0 and mdd_pct >= -25.0 and len(series) >= 100),
            }
        )

    overall_pass = bool(models_out) and all(bool(x.get("pass_gate")) for x in models_out)
    return {
        "generated_at_utc": _utc_now_iso(),
        "lookback_days": int(lookback_days),
        "rows": len(rows),
        "overall_pass": overall_pass,
        "gate_rule": "return_pct>=0 AND max_drawdown_pct>=-25 AND samples>=100",
        "models": models_out,
    }


def main() -> None:
    dashboard_url = os.getenv("DASHBOARD_URL", "http://app:8099/api/dashboard").strip()
    snapshot_seconds = max(10, int(os.getenv("SNAPSHOT_SECONDS", "300")))
    mid_report_seconds = max(60, int(os.getenv("MID_REPORT_SECONDS", "7200")))
    rolling_days = max(1, int(os.getenv("ROLLING_VALIDATION_DAYS", "30")))
    report_dir = Path(os.getenv("REPORT_DIR", "/app/reports"))
    report_dir.mkdir(parents=True, exist_ok=True)

    snapshots_path = report_dir / "dashboard_snapshots.jsonl"
    mid_path = report_dir / "mid_report_2h.json"
    rolling_path = report_dir / "rolling_30d_validation.json"
    started = time.time()
    mid_written = False

    while True:
        row: dict[str, Any]
        try:
            res = requests.get(dashboard_url, timeout=12)
            res.raise_for_status()
            payload = res.json() if isinstance(res.json(), dict) else {}
            row = _build_summary(payload)
        except Exception as exc:  # noqa: BLE001
            row = {
                "captured_at_utc": _utc_now_iso(),
                "error": str(exc),
            }

        with snapshots_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

        if (not mid_written) and (time.time() - started >= mid_report_seconds):
            with mid_path.open("w", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False, indent=2))
            mid_written = True

        try:
            recent_rows = _load_recent_snapshots(snapshots_path, rolling_days * 86400)
            rolling = _build_rolling_validation(recent_rows, rolling_days)
            with rolling_path.open("w", encoding="utf-8") as f:
                f.write(json.dumps(rolling, ensure_ascii=False, indent=2))
        except Exception:
            pass

        time.sleep(snapshot_seconds)


if __name__ == "__main__":
    main()
