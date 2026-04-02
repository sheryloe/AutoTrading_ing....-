from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import requests


class SupabaseSyncClient:
    def __init__(
        self,
        *,
        url: str,
        secret_key: str,
        enabled: bool,
        timeout_seconds: int = 15,
    ) -> None:
        self.url = str(url or "").rstrip("/")
        self.secret_key = str(secret_key or "").strip()
        self.enabled = bool(enabled and self.url and self.secret_key)
        self.timeout_seconds = max(5, int(timeout_seconds))
        self.session = requests.Session()

    def _headers(self, *, upsert: bool = False) -> dict[str, str]:
        headers = {
            "apikey": self.secret_key,
            "Authorization": f"Bearer {self.secret_key}",
            "Content-Type": "application/json",
        }
        if upsert:
            headers["Prefer"] = "resolution=merge-duplicates,return=minimal"
        else:
            headers["Prefer"] = "return=minimal"
        return headers

    def _table_url(self, table: str) -> str:
        return f"{self.url}/rest/v1/{str(table).strip()}"

    def _rpc_url(self, fn_name: str) -> str:
        return f"{self.url}/rest/v1/rpc/{str(fn_name).strip()}"

    def upsert_rows(self, table: str, rows: list[dict[str, Any]], *, on_conflict: str) -> dict[str, Any]:
        if not self.enabled:
            return {"ok": False, "error": "disabled"}
        payload = [dict(row or {}) for row in list(rows or []) if isinstance(row, dict) and row]
        if not payload:
            return {"ok": True, "count": 0}
        resp = self.session.post(
            self._table_url(table),
            params={"on_conflict": str(on_conflict or "")},
            headers=self._headers(upsert=True),
            data=json.dumps(payload, ensure_ascii=True, separators=(",", ":")),
            timeout=self.timeout_seconds,
        )
        if resp.ok:
            return {"ok": True, "count": len(payload)}
        return {"ok": False, "status": resp.status_code, "error": resp.text[:400]}

    def fetch_rows(self, table: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.enabled:
            return {"ok": False, "error": "disabled"}
        resp = self.session.get(
            self._table_url(table),
            params=dict(params or {}),
            headers=self._headers(),
            timeout=self.timeout_seconds,
        )
        if resp.ok:
            try:
                data = resp.json()
            except Exception:
                return {"ok": False, "status": resp.status_code, "error": "invalid_json"}
            return {"ok": True, "rows": data if isinstance(data, list) else []}
        return {"ok": False, "status": resp.status_code, "error": resp.text[:400]}

    def delete_rows(self, table: str, *, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.enabled:
            return {"ok": False, "error": "disabled"}
        resp = self.session.delete(
            self._table_url(table),
            params=dict(filters or {}),
            headers=self._headers(),
            timeout=self.timeout_seconds,
        )
        if resp.ok:
            return {"ok": True}
        return {"ok": False, "status": resp.status_code, "error": resp.text[:400]}

    def upsert_blob(self, blob_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        row = {
            "blob_key": str(blob_key or "").strip(),
            "payload_json": dict(payload or {}),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if not row["blob_key"]:
            return {"ok": False, "error": "blob_key_required"}
        return self.upsert_rows("engine_state_blobs", [row], on_conflict="blob_key")

    def fetch_blob(self, blob_key: str) -> dict[str, Any]:
        key = str(blob_key or "").strip()
        if not key:
            return {"ok": False, "error": "blob_key_required"}
        result = self.fetch_rows(
            "engine_state_blobs",
            params={
                "blob_key": f"eq.{key}",
                "select": "payload_json,updated_at",
                "limit": "1",
            },
        )
        if not result.get("ok"):
            return result
        rows = list(result.get("rows") or [])
        if not rows:
            return {"ok": False, "error": "not_found"}
        row = dict(rows[0] or {})
        payload = row.get("payload_json")
        if not isinstance(payload, dict):
            payload = {}
        return {"ok": True, "payload": payload, "updated_at": row.get("updated_at")}

    def call_rpc(self, fn_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled:
            return {"ok": False, "error": "disabled"}
        resp = self.session.post(
            self._rpc_url(fn_name),
            headers=self._headers(),
            data=json.dumps(dict(payload or {}), ensure_ascii=True, separators=(",", ":")),
            timeout=self.timeout_seconds,
        )
        if not resp.ok:
            return {"ok": False, "status": resp.status_code, "error": resp.text[:400]}
        if not resp.text.strip():
            return {"ok": True, "data": None}
        try:
            return {"ok": True, "data": resp.json()}
        except Exception:
            return {"ok": False, "status": resp.status_code, "error": "invalid_json"}

    def fetch_service_secret(self, provider: str, passphrase: str) -> dict[str, Any]:
        result = self.call_rpc(
            "get_service_secret",
            {"p_provider": str(provider or "").strip(), "p_passphrase": str(passphrase or "")},
        )
        if not result.get("ok"):
            return result
        payload = result.get("data")
        if not isinstance(payload, dict):
            payload = {}
        return {"ok": True, "payload": payload}

    def replace_open_positions(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        if not self.enabled:
            return {"ok": False, "error": "disabled"}
        delete_resp = self.session.delete(
            self._table_url("positions"),
            params={"market": "eq.crypto", "status": "eq.open"},
            headers=self._headers(),
            timeout=self.timeout_seconds,
        )
        if not delete_resp.ok:
            return {"ok": False, "status": delete_resp.status_code, "error": delete_resp.text[:400]}
        if not rows:
            return {"ok": True, "count": 0}
        return self.upsert_rows("positions", rows, on_conflict="id")
