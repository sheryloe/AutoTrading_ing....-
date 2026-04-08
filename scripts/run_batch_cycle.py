from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config import load_settings, normalize_runtime_data_sources, normalize_runtime_universe_mode
from src.engine import TradingEngine
from src.providers.bybit_api import BybitV5Client
from src.supabase_sync import SupabaseSyncClient


SERVICE_RUNTIME_BLOB_KEY = "service_runtime_config"
PROVIDER_ENV_MAP = {
    "bybit": {
        "api_key": "BYBIT_API_KEY",
        "api_secret": "BYBIT_API_SECRET",
    },
    "binance": {
        "api_key": "BINANCE_API_KEY",
        "api_secret": "BINANCE_API_SECRET",
    },
    "coingecko": {
        "api_key": "COINGECKO_API_KEY",
    },
}


def _normalize_bybit_secret_source(raw: str) -> str:
    value = str(raw or "").strip().lower()
    if value in {"github", "supabase"}:
        return value
    return "supabase"


def _hydrate_provider_secrets(client: SupabaseSyncClient, master_key: str, *, bybit_secret_source: str) -> None:
    bybit_source = _normalize_bybit_secret_source(bybit_secret_source)
    for provider, env_map in PROVIDER_ENV_MAP.items():
        if str(provider).strip().lower() == "bybit" and bybit_source == "github":
            continue
        result = client.fetch_service_secret(provider, master_key)
        payload = result.get("payload") if bool(result.get("ok")) else None
        if not isinstance(payload, dict):
            continue
        for payload_key, env_name in env_map.items():
            current = os.environ.get(env_name) or ""
            value = str(payload.get(payload_key) or current)
            if value:
                os.environ[env_name] = value


def _hydrate_runtime_from_supabase() -> None:
    bybit_secret_source = _normalize_bybit_secret_source(str(os.environ.get("BYBIT_SECRET_SOURCE") or "supabase"))
    os.environ["BYBIT_SECRET_SOURCE"] = bybit_secret_source
    url = str(os.environ.get("SUPABASE_URL") or "").strip()
    secret_key = str(
        os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or ""
    ).strip()
    client = SupabaseSyncClient(
        url=url,
        secret_key=secret_key,
        enabled=bool(url and secret_key),
        timeout_seconds=15,
    )
    if not client.enabled:
        return

    runtime_result = client.fetch_blob(SERVICE_RUNTIME_BLOB_KEY)
    runtime_payload = runtime_result.get("payload") if bool(runtime_result.get("ok")) else None
    if isinstance(runtime_payload, dict) and runtime_payload:
        normalized_payload = normalize_runtime_data_sources(runtime_payload)
        normalized_payload = normalize_runtime_universe_mode(normalized_payload)
        if json.dumps(normalized_payload, ensure_ascii=True, sort_keys=True) != json.dumps(
            runtime_payload, ensure_ascii=True, sort_keys=True
        ):
            client.upsert_blob(SERVICE_RUNTIME_BLOB_KEY, normalized_payload)
        runtime_path = Path(str(os.environ.get("RUNTIME_SETTINGS_FILE") or "runtime_settings.json"))
        runtime_path.write_text(json.dumps(normalized_payload, ensure_ascii=True, indent=2), encoding="utf-8")

    master_key = str(os.environ.get("SERVICE_MASTER_KEY") or "").strip()
    if master_key:
        _hydrate_provider_secrets(client, master_key, bybit_secret_source=bybit_secret_source)


def _env_flag(name: str, default: bool = False) -> bool:
    raw = str(os.environ.get(name) or "").strip().lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "on"}


def _parse_http_status(text: str) -> int:
    match = re.search(r"\b([1-5]\d\d)\b Client Error", str(text or ""))
    if match:
        try:
            return int(match.group(1))
        except Exception:
            return 0
    return 0


def _run_bybit_preflight(engine: TradingEngine) -> dict[str, object]:
    info: dict[str, object] = {
        "bybit_preflight_ok": False,
        "bybit_preflight_public_status": 0,
        "bybit_preflight_auth_status": 0,
        "bybit_preflight_error": "",
    }
    api_key = str(getattr(engine.settings, "bybit_api_key", "") or "").strip()
    api_secret = str(getattr(engine.settings, "bybit_api_secret", "") or "").strip()
    base_url = str(getattr(engine.settings, "bybit_base_url", "") or "https://api.bybit.com").rstrip("/")
    timeout_seconds = max(3, int(getattr(engine.settings, "supabase_sync_timeout_seconds", 15)))
    errors: list[str] = []

    if not api_key or not api_secret:
        info["bybit_preflight_error"] = "bybit_credentials_missing"
        return info

    try:
        resp = requests.get(f"{base_url}/v5/market/time", timeout=timeout_seconds)
        info["bybit_preflight_public_status"] = int(resp.status_code)
        if not resp.ok:
            errors.append(f"public:{resp.status_code}")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"public:{exc}")

    try:
        client = BybitV5Client(
            api_key=api_key,
            api_secret=api_secret,
            base_url=base_url,
            recv_window=int(getattr(engine.settings, "bybit_recv_window", 5000)),
            timeout_seconds=timeout_seconds,
        )
        client.get_positions()
        info["bybit_preflight_auth_status"] = 200
    except Exception as exc:  # noqa: BLE001
        status = _parse_http_status(str(exc))
        if status > 0:
            info["bybit_preflight_auth_status"] = int(status)
        errors.append(f"auth:{exc}")

    auth_ok = int(info.get("bybit_preflight_auth_status") or 0) == 200
    info["bybit_preflight_ok"] = bool(auth_ok)
    if errors:
        info["bybit_preflight_error"] = "; ".join(str(e) for e in errors)[:400]
    return info


def _record_bybit_preflight(engine: TradingEngine, preflight: dict[str, object], now_ts: int) -> None:
    if bool(preflight.get("bybit_preflight_ok")):
        return
    error_text = str(preflight.get("bybit_preflight_error") or "bybit_preflight_failed")
    try:
        engine.runtime_feedback.append_event(
            source="bybit_preflight",
            level="warn",
            status="error",
            error=error_text,
            detail="Bybit preflight failed; batch cycle continues in warning mode",
            meta={
                "public_status": int(preflight.get("bybit_preflight_public_status") or 0),
                "auth_status": int(preflight.get("bybit_preflight_auth_status") or 0),
                "secret_source": str(os.environ.get("BYBIT_SECRET_SOURCE") or "supabase"),
            },
            now_ts=int(now_ts),
        )
    except Exception:
        pass


def _should_run_bybit_preflight(engine: TradingEngine) -> bool:
    trade_mode = str(getattr(engine.settings, "trade_mode", "paper") or "paper").lower()
    if trade_mode == "live":
        return bool(getattr(engine.settings, "enable_live_execution", False) and getattr(engine.settings, "live_enable_crypto", False))
    if trade_mode == "paper":
        return bool(getattr(engine.settings, "bybit_readonly_sync", False))
    return False


def _build_heartbeat_row(
    engine: TradingEngine,
    *,
    started_at: int,
    finished_at: int | None = None,
    error: str = "",
    preflight: dict[str, object] | None = None,
) -> dict:
    row = dict(engine._build_supabase_heartbeat_row(int(finished_at or started_at)))  # noqa: SLF001
    row["last_cycle_started_at"] = engine._iso_datetime(int(started_at))  # noqa: SLF001
    row["last_cycle_finished_at"] = engine._iso_datetime(int(finished_at or started_at))  # noqa: SLF001
    row["last_error"] = str(error or "")[:400]
    row["version_sha"] = str(os.environ.get("GITHUB_SHA") or row.get("version_sha") or "")[:64]
    row["host_name"] = str(
        os.environ.get("HOSTNAME") or os.environ.get("COMPUTERNAME") or row.get("host_name") or ""
    )[:120]
    meta = dict(row.get("meta_json") or {})
    meta["execution_target"] = str(getattr(engine.settings, "trade_mode", "") or "")
    meta["runner"] = "github-actions"
    preflight_info = dict(preflight or {})
    meta["bybit_preflight_ok"] = bool(preflight_info.get("bybit_preflight_ok", False))
    meta["bybit_preflight_public_status"] = int(preflight_info.get("bybit_preflight_public_status", 0) or 0)
    meta["bybit_preflight_auth_status"] = int(preflight_info.get("bybit_preflight_auth_status", 0) or 0)
    meta["bybit_preflight_error"] = str(preflight_info.get("bybit_preflight_error") or "")
    row["meta_json"] = meta
    return row


def _push_heartbeat(
    engine: TradingEngine,
    *,
    started_at: int,
    finished_at: int | None = None,
    error: str = "",
    preflight: dict[str, object] | None = None,
) -> dict:
    client = getattr(engine, "supabase_sync", None)
    if not bool(getattr(client, "enabled", False)):
        return {"ok": False, "error": "supabase_sync_disabled"}
    row = _build_heartbeat_row(
        engine,
        started_at=started_at,
        finished_at=finished_at,
        error=error,
        preflight=preflight,
    )
    result = client.upsert_rows("engine_heartbeat", [row], on_conflict="engine_name")
    if not bool((result or {}).get("ok")):
        print(
            json.dumps(
                {
                    "ok": False,
                    "heartbeat_sync_failed": True,
                    "error": result.get("error") if isinstance(result, dict) else "heartbeat_sync_failed",
                },
                ensure_ascii=True,
            ),
            file=sys.stderr,
        )
    return dict(result or {})


def main() -> int:
    started = int(time.time())
    strict_sync = _env_flag("SUPABASE_SYNC_STRICT", default=False)
    _hydrate_runtime_from_supabase()
    engine = TradingEngine(load_settings())
    if _should_run_bybit_preflight(engine):
        preflight = _run_bybit_preflight(engine)
        _record_bybit_preflight(engine, preflight, now_ts=started)
    else:
        preflight = {
            "bybit_preflight_ok": True,
            "bybit_preflight_public_status": 0,
            "bybit_preflight_auth_status": 0,
            "bybit_preflight_error": "skipped_not_required",
        }
    if strict_sync and not bool(getattr(getattr(engine, "supabase_sync", None), "enabled", False)):
        print(json.dumps({"ok": False, "error": "supabase_sync_disabled"}, ensure_ascii=True), file=sys.stderr)
        return 1
    initial_hb = _push_heartbeat(
        engine,
        started_at=started,
        finished_at=started,
        error="",
        preflight=preflight,
    )
    if strict_sync and not bool((initial_hb or {}).get("ok")):
        return 1
    try:
        engine.run_cycle()
        engine._persist(force=True)  # noqa: SLF001
        finished = int(time.time())
        final_hb = _push_heartbeat(
            engine,
            started_at=started,
            finished_at=finished,
            error="",
            preflight=preflight,
        )
        if strict_sync and not bool((final_hb or {}).get("ok")):
            return 1
        payload = engine.dashboard_payload()
        summary = {
            "ok": True,
            "started_at": started,
            "finished_at": finished,
            "heartbeat": payload.get("last_cycle_ts"),
            "crypto_signals": len(list(payload.get("bybit_signal_rows") or [])),
            "open_positions": len(list(payload.get("bybit_positions") or [])),
            "daily_rows": len(list(payload.get("daily_pnl") or [])),
        }
        print(json.dumps(summary, ensure_ascii=True))
        return 0
    except Exception as exc:  # noqa: BLE001
        finished = int(time.time())
        _push_heartbeat(
            engine,
            started_at=started,
            finished_at=finished,
            error=str(exc),
            preflight=preflight,
        )
        error_payload = {
            "ok": False,
            "started_at": started,
            "finished_at": finished,
            "error": str(exc),
        }
        print(json.dumps(error_payload, ensure_ascii=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
