from __future__ import annotations

import csv
import json
import os
import subprocess
from pathlib import Path
from typing import Any


def _report_sort_key(row: dict[str, Any]) -> tuple[str, str]:
    return (str(row.get("date") or ""), str(row.get("model_id") or ""))


def write_daily_pnl_report(day_key: str, rows: list[dict[str, Any]], output_dir: str) -> list[str]:
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    ordered = sorted([dict(row or {}) for row in rows], key=lambda row: str(row.get("model_id") or ""))
    summary = {
        "date": str(day_key or ""),
        "generated_from": "engine.daily_pnl",
        "model_count": int(len(ordered)),
        "totals": {
            "crypto_equity_usd": round(
                float(sum(float((row or {}).get("bybit_equity_usd") or 0.0) for row in ordered)),
                6,
            ),
            "crypto_total_pnl_usd": round(
                float(sum(float((row or {}).get("bybit_total_pnl_usd") or 0.0) for row in ordered)),
                6,
            ),
            "crypto_realized_pnl_usd": round(
                float(sum(float((row or {}).get("bybit_realized_pnl_usd") or 0.0) for row in ordered)),
                6,
            ),
            "crypto_unrealized_pnl_usd": round(
                float(sum(float((row or {}).get("bybit_unrealized_pnl_usd") or 0.0) for row in ordered)),
                6,
            ),
        },
        "models": ordered,
    }

    json_path = target_dir / f"{day_key}.json"
    csv_path = target_dir / f"{day_key}.csv"
    summary_path = target_dir / "summary.csv"
    summary_json_path = target_dir / "summary.json"

    json_path.write_text(json.dumps(summary, ensure_ascii=True, indent=2), encoding="utf-8")

    fieldnames = [
        "date",
        "model_id",
        "bybit_equity_usd",
        "bybit_total_pnl_usd",
        "bybit_realized_pnl_usd",
        "bybit_unrealized_pnl_usd",
        "bybit_win_rate",
        "bybit_closed_trades",
        "total_equity_usd",
        "total_pnl_usd",
        "realized_pnl_usd",
        "unrealized_pnl_usd",
        "win_rate",
        "closed_trades",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in ordered:
            writer.writerow({key: row.get(key, "") for key in fieldnames})

    summary_rows: list[dict[str, Any]] = []
    if summary_path.exists():
        with summary_path.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                if not isinstance(row, dict):
                    continue
                summary_rows.append(dict(row))
    filtered = [
        row
        for row in summary_rows
        if not (
            str(row.get("date") or "") == str(day_key or "")
            and str(row.get("model_id") or "") in {str(item.get("model_id") or "") for item in ordered}
        )
    ]
    filtered.extend({key: row.get(key, "") for key in fieldnames} for row in ordered)
    filtered.sort(key=_report_sort_key)
    with summary_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(filtered)

    summary_json_path.write_text(json.dumps(filtered, ensure_ascii=True, indent=2), encoding="utf-8")

    return [str(json_path), str(csv_path), str(summary_path), str(summary_json_path)]


def git_commit_report_files(
    repo_root: str,
    file_paths: list[str],
    commit_message: str,
    *,
    push: bool = False,
    branch: str = "",
    author_name: str = "",
    author_email: str = "",
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    rel_files: list[str] = []
    for raw in list(file_paths or []):
        path = Path(raw).resolve()
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        rel_files.append(str(rel).replace("\\", "/"))
    rel_files = [item for item in rel_files if item]
    if not rel_files:
        return {"ok": False, "error": "no_report_files"}

    env = dict(os.environ)
    if str(author_name or "").strip():
        env["GIT_AUTHOR_NAME"] = str(author_name).strip()
        env["GIT_COMMITTER_NAME"] = str(author_name).strip()
    if str(author_email or "").strip():
        env["GIT_AUTHOR_EMAIL"] = str(author_email).strip()
        env["GIT_COMMITTER_EMAIL"] = str(author_email).strip()

    add_cmd = ["git", "-C", str(root), "add", "--", *rel_files]
    add_res = subprocess.run(add_cmd, capture_output=True, text=True, env=env, timeout=60, check=False)
    if add_res.returncode != 0:
        return {"ok": False, "error": add_res.stderr.strip() or add_res.stdout.strip() or "git_add_failed"}

    diff_cmd = ["git", "-C", str(root), "diff", "--cached", "--quiet", "--", *rel_files]
    diff_res = subprocess.run(diff_cmd, capture_output=True, text=True, env=env, timeout=60, check=False)
    if diff_res.returncode == 0:
        return {"ok": True, "committed": False, "pushed": False, "message": "no_changes"}
    if diff_res.returncode not in {0, 1}:
        return {"ok": False, "error": diff_res.stderr.strip() or diff_res.stdout.strip() or "git_diff_failed"}

    commit_cmd = ["git", "-C", str(root), "commit", "-m", str(commit_message or "daily pnl report")]
    commit_res = subprocess.run(commit_cmd, capture_output=True, text=True, env=env, timeout=120, check=False)
    if commit_res.returncode != 0:
        return {"ok": False, "error": commit_res.stderr.strip() or commit_res.stdout.strip() or "git_commit_failed"}

    pushed = False
    if push:
        push_cmd = ["git", "-C", str(root), "push"]
        if str(branch or "").strip():
            push_cmd = ["git", "-C", str(root), "push", "origin", f"HEAD:{str(branch).strip()}"]
        push_res = subprocess.run(push_cmd, capture_output=True, text=True, env=env, timeout=180, check=False)
        if push_res.returncode != 0:
            return {
                "ok": False,
                "committed": True,
                "pushed": False,
                "error": push_res.stderr.strip() or push_res.stdout.strip() or "git_push_failed",
            }
        pushed = True

    return {
        "ok": True,
        "committed": True,
        "pushed": bool(pushed),
        "files": rel_files,
    }
