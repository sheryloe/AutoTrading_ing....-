from __future__ import annotations

import atexit
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request

from src.config import load_settings
from src.engine import TradingEngine


def _pid_alive(pid: int) -> bool:
    if int(pid) <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return False
    try:
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


def _acquire_singleton_lock(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    me = int(os.getpid())
    payload = {"pid": me, "ts": int(time.time())}
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=True))
        return
    except FileExistsError:
        pass
    except Exception:
        return

    holder_pid = 0
    holder_ts = 0
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        holder_pid = int(raw.get("pid") or 0)
        holder_ts = int(raw.get("ts") or 0)
    except Exception:
        holder_pid = 0
        holder_ts = 0

    stale = (int(time.time()) - int(holder_ts)) > 120
    if holder_pid > 0 and _pid_alive(holder_pid) and not stale:
        print(f"[web_app] already running (pid={holder_pid})", flush=True)
        sys.exit(2)

    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass
    fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=True))


def _release_singleton_lock(path: Path) -> None:
    me = int(os.getpid())
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if int(raw.get("pid") or 0) != me:
            return
    except Exception:
        return
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass


_APP_LOCK = Path("reports") / "web_app.lock"
_acquire_singleton_lock(_APP_LOCK)

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["JSON_AS_ASCII"] = False
try:
    app.json.ensure_ascii = False
except Exception:
    pass
engine = TradingEngine(load_settings())
engine.start()


@atexit.register
def _shutdown() -> None:
    engine.stop()
    _release_singleton_lock(_APP_LOCK)


@app.after_request
def _force_utf8(resp: Any) -> Any:
    ctype = str(resp.headers.get("Content-Type") or "")
    if "charset=" not in ctype.lower():
        if ctype.startswith("text/") or ctype.startswith("application/json") or ctype.startswith(
            "application/javascript"
        ):
            resp.headers["Content-Type"] = f"{ctype}; charset=utf-8" if ctype else "text/plain; charset=utf-8"
    return resp


@app.get("/")
def home() -> Any:
    settings = load_settings()
    return render_template(
        "index.html",
        ui_refresh_seconds=max(2, settings.ui_refresh_seconds),
        app_port=settings.app_port,
    )


@app.get("/health")
def health() -> Any:
    payload = {"ok": True, "running": engine.running}
    return jsonify(payload)


@app.get("/api/dashboard")
def api_dashboard() -> Any:
    return jsonify(engine.dashboard_payload())


@app.post("/api/control/start")
def api_start() -> Any:
    engine.start()
    return jsonify({"ok": True, "running": engine.running})


@app.post("/api/control/stop")
def api_stop() -> Any:
    engine.stop()
    return jsonify({"ok": True, "running": engine.running})


@app.post("/api/control/restart")
def api_restart() -> Any:
    engine.restart()
    return jsonify({"ok": True, "running": engine.running})


@app.post("/api/control/mode")
def api_mode() -> Any:
    data = request.get_json(silent=True) or {}
    mode = str(data.get("mode") or "").lower()
    engine.set_trade_mode(mode)
    return jsonify({"ok": True, "mode": engine.settings.trade_mode})


@app.post("/api/control/autotrade")
def api_autotrade() -> Any:
    data = request.get_json(silent=True) or {}
    raw = data.get("enabled")
    enabled = raw if isinstance(raw, bool) else str(raw).strip().lower() in {"1", "true", "yes", "on"}
    engine.set_autotrade(enabled)
    return jsonify({"ok": True, "enabled": engine.settings.enable_autotrade})


@app.post("/api/control/force-sync")
def api_force_sync() -> Any:
    engine.force_sync()
    return jsonify({"ok": True})


@app.post("/api/control/close-meme")
def api_close_meme() -> Any:
    result = engine.close_all_memecoin_positions("manual_close_api")
    return jsonify({"ok": True, "result": result})


@app.post("/api/control/reset-demo")
def api_reset_demo() -> Any:
    data = request.get_json(silent=True) or {}
    seed = data.get("seed_usdt")
    confirm_text = str(data.get("confirm_text") or "")
    try:
        seed_value = float(seed) if seed is not None else None
    except Exception:
        seed_value = None
    try:
        result = engine.reset_demo(seed_value, confirm_text=confirm_text, actor="api")
    except PermissionError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 403
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "result": result})


if __name__ == "__main__":
    settings = load_settings()
    app.run(host=settings.app_host, port=settings.app_port, debug=False)
