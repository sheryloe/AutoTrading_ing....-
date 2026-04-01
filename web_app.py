from __future__ import annotations

import atexit
import json
import os
import sys
import time
from functools import wraps
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, redirect, render_template, request

from src.config import load_settings
from src.engine import TradingEngine

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


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

    if holder_pid == me:
        # Container restarts may reuse pid=1 while lock file remains on mounted volume.
        path.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
        return

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


def _expected_admin_token() -> str:
    return str(os.getenv("SERVICE_ADMIN_TOKEN") or "").strip()


def _extract_admin_token() -> str:
    header_token = str(request.headers.get("X-Service-Admin-Token") or "").strip()
    if header_token:
        return header_token
    auth_header = str(request.headers.get("Authorization") or "").strip()
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    if request.method != "GET":
        data = request.get_json(silent=True) or {}
        if isinstance(data, dict):
            body_token = str(data.get("adminToken") or "").strip()
            if body_token:
                return body_token
    return str(request.args.get("adminToken") or "").strip()


def _require_admin_token() -> tuple[bool, Any | None]:
    expected = _expected_admin_token()
    if not expected:
        return False, (jsonify({"ok": False, "error": "service_admin_token_missing"}), 503)
    provided = _extract_admin_token()
    if not provided or provided != expected:
        return False, (jsonify({"ok": False, "error": "unauthorized"}), 401)
    return True, None


def require_admin_token(fn):
    @wraps(fn)
    def _wrapped(*args, **kwargs):
        ok, response = _require_admin_token()
        if not ok:
            return response
        return fn(*args, **kwargs)

    return _wrapped


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


def _render_workspace(workspace: str) -> Any:
    settings = load_settings()
    safe_workspace = str(workspace or "models").strip().lower()
    if safe_workspace not in {"models", "paper", "live", "settings"}:
        safe_workspace = "models"
    return render_template(
        "index.html",
        ui_refresh_seconds=max(2, settings.ui_refresh_seconds),
        app_port=settings.app_port,
        asset_version=int(time.time()),
        initial_workspace=safe_workspace,
    )


@app.get("/")
def home() -> Any:
    return redirect("/models", code=302)


@app.get("/models")
def models_page() -> Any:
    return _render_workspace("models")


@app.get("/paper")
def paper_page() -> Any:
    return _render_workspace("paper")


@app.get("/live")
def live_page() -> Any:
    return _render_workspace("live")


@app.get("/settings")
def settings_page() -> Any:
    return _render_workspace("settings")


@app.get("/health")
def health() -> Any:
    payload = {"ok": True, "running": engine.running}
    return jsonify(payload)


@app.get("/api/dashboard")
def api_dashboard() -> Any:
    return jsonify(engine.dashboard_payload())


@app.get("/api/meme-score-history")
def api_meme_score_history() -> Any:
    token_address = str(request.args.get("token_address") or request.args.get("token") or "").strip()
    if not token_address:
        return jsonify({"ok": False, "error": "token_address is required"}), 400
    raw_limit = request.args.get("limit")
    try:
        limit = int(raw_limit) if raw_limit is not None else 240
    except Exception:
        limit = 240
    raw_refresh = request.args.get("refresh")
    if raw_refresh is None:
        ensure_fresh = True
    else:
        ensure_fresh = str(raw_refresh).strip().lower() in {"1", "true", "yes", "on"}
    force_refresh = bool(raw_refresh is not None and ensure_fresh)
    try:
        result = engine.get_meme_score_history(
            token_address=token_address,
            limit=limit,
            ensure_fresh=ensure_fresh,
            force_refresh=force_refresh,
        )
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "result": result})


@app.post("/api/control/start")
@require_admin_token
def api_start() -> Any:
    engine.start()
    return jsonify({"ok": True, "running": engine.running})


@app.post("/api/control/stop")
@require_admin_token
def api_stop() -> Any:
    engine.stop()
    return jsonify({"ok": True, "running": engine.running})


@app.post("/api/control/restart")
@require_admin_token
def api_restart() -> Any:
    engine.restart()
    return jsonify({"ok": True, "running": engine.running})


@app.post("/api/control/mode")
@require_admin_token
def api_mode() -> Any:
    data = request.get_json(silent=True) or {}
    mode = str(data.get("mode") or "").lower()
    engine.set_trade_mode(mode)
    return jsonify({"ok": True, "mode": engine.settings.trade_mode})


@app.post("/api/control/autotrade")
@require_admin_token
def api_autotrade() -> Any:
    data = request.get_json(silent=True) or {}
    raw = data.get("enabled")
    enabled = raw if isinstance(raw, bool) else str(raw).strip().lower() in {"1", "true", "yes", "on"}
    engine.set_autotrade(enabled)
    return jsonify({"ok": True, "enabled": engine.settings.enable_autotrade})


@app.post("/api/control/models")
@require_admin_token
def api_set_models() -> Any:
    data = request.get_json(silent=True) or {}
    meme_models = data.get("meme_models")
    crypto_models = data.get("crypto_models")
    try:
        applied = engine.set_autotrade_models_runtime(meme_models=meme_models, crypto_models=crypto_models)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "applied": applied})


@app.post("/api/control/live-models")
@require_admin_token
def api_set_live_models() -> Any:
    data = request.get_json(silent=True) or {}
    meme_models = data.get("meme_models")
    crypto_models = data.get("crypto_models")
    try:
        applied = engine.set_live_models_runtime(meme_models=meme_models, crypto_models=crypto_models)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "applied": applied})


@app.post("/api/control/live-markets")
@require_admin_token
def api_set_live_markets() -> Any:
    data = request.get_json(silent=True) or {}
    meme_enabled = data.get("meme_enabled")
    crypto_enabled = data.get("crypto_enabled")
    def _to_bool_or_none(value: Any) -> bool | None:
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
        return None
    applied = engine.set_live_markets(
        meme_enabled=_to_bool_or_none(meme_enabled),
        crypto_enabled=_to_bool_or_none(crypto_enabled),
    )
    return jsonify({"ok": True, "applied": applied})


@app.post("/api/control/live-performance/anchor-now")
@require_admin_token
def api_set_live_performance_anchor_now() -> Any:
    data = request.get_json(silent=True) or {}
    raw = data.get("reset_net_flow", True)
    if isinstance(raw, bool):
        reset_net_flow = raw
    else:
        reset_net_flow = str(raw).strip().lower() in {"1", "true", "yes", "on"}
    result = engine.set_live_performance_anchor_now(reset_net_flow=reset_net_flow)
    return jsonify({"ok": True, "result": result})


@app.post("/api/control/live-performance/flow")
@require_admin_token
def api_adjust_live_performance_flow() -> Any:
    data = request.get_json(silent=True) or {}
    try:
        delta_usd = float(data.get("delta_usd"))
    except Exception:
        return jsonify({"ok": False, "error": "delta_usd must be a number"}), 400
    note = str(data.get("note") or "")
    try:
        result = engine.adjust_live_net_flow(delta_usd=delta_usd, note=note)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "result": result})


@app.post("/api/control/force-sync")
@require_admin_token
def api_force_sync() -> Any:
    result = engine.force_sync()
    return jsonify({"ok": True, "result": result})


@app.post("/api/control/close-meme")
@require_admin_token
def api_close_meme() -> Any:
    result = engine.close_all_memecoin_positions("manual_close_api")
    return jsonify({"ok": True, "result": result})


@app.post("/api/control/reset-demo")
@require_admin_token
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


@app.post("/api/control/reset-demo-crypto")
@require_admin_token
def api_reset_demo_crypto() -> Any:
    data = request.get_json(silent=True) or {}
    seed = data.get("seed_usdt")
    confirm_text = str(data.get("confirm_text") or "")
    try:
        seed_value = float(seed) if seed is not None else None
    except Exception:
        seed_value = None
    try:
        result = engine.reset_crypto_demo(seed_value, confirm_text=confirm_text, actor="api")
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "result": result})


@app.get("/api/settings/secrets")
@require_admin_token
def api_get_secret_settings() -> Any:
    return jsonify({"ok": True, "secrets": engine.secret_settings_payload()})


@app.post("/api/settings/secrets")
@require_admin_token
def api_update_secret_settings() -> Any:
    data = request.get_json(silent=True) or {}
    updates = data.get("updates") if isinstance(data, dict) else {}
    if not isinstance(updates, dict):
        return jsonify({"ok": False, "error": "updates must be object"}), 400
    try:
        secrets = engine.update_secret_settings(updates)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "secrets": secrets})


if __name__ == "__main__":
    settings = load_settings()
    app.run(host=settings.app_host, port=settings.app_port, debug=False)
