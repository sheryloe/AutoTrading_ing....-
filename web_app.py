from __future__ import annotations

import atexit
from typing import Any

from flask import Flask, jsonify, render_template, request

from src.config import load_settings
from src.engine import TradingEngine


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
