"use client";

import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

function boolToString(value) {
  return value ? "true" : "false";
}

export default function ControlConsole({ initialConfig, runtimeUpdatedAt, bybitStatus, writeReady }) {
  const router = useRouter();
  const [adminToken, setAdminToken] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [config, setConfig] = useState({
    tradeMode: String(initialConfig?.TRADE_MODE || "paper"),
    enableAutotrade: boolToString(Boolean(initialConfig?.ENABLE_AUTOTRADE)),
    enableLiveExecution: boolToString(Boolean(initialConfig?.ENABLE_LIVE_EXECUTION)),
    liveEnableCrypto: boolToString(Boolean(initialConfig?.LIVE_ENABLE_CRYPTO)),
    scanIntervalSeconds: String(initialConfig?.SCAN_INTERVAL_SECONDS || 600),
    signalCooldownMinutes: String(initialConfig?.SIGNAL_COOLDOWN_MINUTES || 10),
    autotuneHours: String(initialConfig?.MODEL_AUTOTUNE_INTERVAL_HOURS || 168),
    bybitSymbols: String(initialConfig?.BYBIT_SYMBOLS || "BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,BNBUSDT"),
  });
  const [runtimeMessage, setRuntimeMessage] = useState("");
  const [runtimeError, setRuntimeError] = useState("");
  const [credentialMessage, setCredentialMessage] = useState("");
  const [credentialError, setCredentialError] = useState("");
  const [runtimeSaving, setRuntimeSaving] = useState(false);
  const [credentialSaving, setCredentialSaving] = useState(false);

  const bybitHint = useMemo(() => {
    if (!bybitStatus?.meta_json?.api_key_hint) return "not configured";
    return String(bybitStatus.meta_json.api_key_hint);
  }, [bybitStatus]);

  async function saveRuntime(event) {
    event.preventDefault();
    setRuntimeSaving(true);
    setRuntimeError("");
    setRuntimeMessage("");
    try {
      const response = await fetch("/api/service/runtime", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          adminToken,
          config: {
            TRADE_MODE: config.tradeMode,
            ENABLE_AUTOTRADE: config.enableAutotrade === "true",
            ENABLE_LIVE_EXECUTION: config.enableLiveExecution === "true",
            LIVE_ENABLE_CRYPTO: config.liveEnableCrypto === "true",
            SCAN_INTERVAL_SECONDS: Number(config.scanIntervalSeconds || 600),
            SIGNAL_COOLDOWN_MINUTES: Number(config.signalCooldownMinutes || 10),
            MODEL_AUTOTUNE_INTERVAL_HOURS: Number(config.autotuneHours || 168),
            BYBIT_SYMBOLS: config.bybitSymbols,
          },
        }),
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) {
        throw new Error(payload.error || "runtime_save_failed");
      }
      setRuntimeMessage("Runtime config saved. The next batch cycle will use it.");
      router.refresh();
    } catch (error) {
      setRuntimeError(error instanceof Error ? error.message : "runtime_save_failed");
    } finally {
      setRuntimeSaving(false);
    }
  }

  async function saveBybit(event) {
    event.preventDefault();
    setCredentialSaving(true);
    setCredentialError("");
    setCredentialMessage("");
    try {
      const response = await fetch("/api/service/credentials/bybit", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          adminToken,
          apiKey,
          apiSecret,
        }),
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) {
        throw new Error(payload.error || "bybit_save_failed");
      }
      setApiKey("");
      setApiSecret("");
      setCredentialMessage("Bybit credentials stored in Supabase vault.");
      router.refresh();
    } catch (error) {
      setCredentialError(error instanceof Error ? error.message : "bybit_save_failed");
    } finally {
      setCredentialSaving(false);
    }
  }

  async function clearBybit() {
    setCredentialSaving(true);
    setCredentialError("");
    setCredentialMessage("");
    try {
      const response = await fetch("/api/service/credentials/bybit", {
        method: "DELETE",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ adminToken }),
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) {
        throw new Error(payload.error || "bybit_delete_failed");
      }
      setCredentialMessage("Bybit credentials removed from Supabase vault.");
      router.refresh();
    } catch (error) {
      setCredentialError(error instanceof Error ? error.message : "bybit_delete_failed");
    } finally {
      setCredentialSaving(false);
    }
  }

  return (
    <section className="panel service-panel" id="service-control">
      <div className="panel-head">
        <div>
          <p className="eyebrow">Service control</p>
          <h2>Encrypted key vault and runtime config</h2>
        </div>
        <span>{writeReady ? "write enabled" : "read only"}</span>
      </div>

      <div className="service-grid">
        <section className="control-card">
          <h3>Operator token</h3>
          <p className="control-copy">
            The admin token never leaves the server as a stored value. It is only checked on save.
          </p>
          <label className="field-label" htmlFor="admin-token">Admin token</label>
          <input
            id="admin-token"
            className="control-input"
            type="password"
            value={adminToken}
            onChange={(event) => setAdminToken(event.target.value)}
            placeholder="Enter SERVICE_ADMIN_TOKEN"
          />
          {!writeReady ? (
            <p className="error-line">Set SERVICE_ADMIN_TOKEN and SERVICE_MASTER_KEY in Vercel first.</p>
          ) : null}
        </section>

        <section className="control-card">
          <div className="control-head">
            <h3>Bybit credential vault</h3>
            <span>{bybitStatus ? "configured" : "empty"}</span>
          </div>
          <p className="control-copy">
            Stored encrypted in Supabase. GitHub Actions reads the vault at runtime.
          </p>
          <p className="status-line">
            Current key hint: <strong>{bybitHint}</strong>
          </p>
          <p className="status-line">
            Updated at: <strong>{bybitStatus?.updated_at || "-"}</strong>
          </p>
          <form className="control-form" onSubmit={saveBybit}>
            <label className="field-label" htmlFor="bybit-key">Bybit API key</label>
            <input
              id="bybit-key"
              className="control-input"
              type="password"
              value={apiKey}
              onChange={(event) => setApiKey(event.target.value)}
              placeholder="Paste Bybit API key"
            />
            <label className="field-label" htmlFor="bybit-secret">Bybit API secret</label>
            <input
              id="bybit-secret"
              className="control-input"
              type="password"
              value={apiSecret}
              onChange={(event) => setApiSecret(event.target.value)}
              placeholder="Paste Bybit API secret"
            />
            <div className="button-row">
              <button className="action-button" type="submit" disabled={credentialSaving || !writeReady}>
                {credentialSaving ? "Saving..." : "Save Bybit keys"}
              </button>
              <button
                className="action-button ghost"
                type="button"
                disabled={credentialSaving || !writeReady}
                onClick={clearBybit}
              >
                Clear vault
              </button>
            </div>
          </form>
          {credentialMessage ? <p className="success-line">{credentialMessage}</p> : null}
          {credentialError ? <p className="error-line">{credentialError}</p> : null}
        </section>
      </div>

      <section className="control-card runtime-card">
        <div className="control-head">
          <h3>Runtime profile</h3>
          <span>{runtimeUpdatedAt ? "stored in Supabase" : "default profile"}</span>
        </div>
        <p className="control-copy">
          These values are written to a Supabase blob and fetched by the batch runner before each cycle.
        </p>
        <form className="control-form runtime-form" onSubmit={saveRuntime}>
          <label className="field-label" htmlFor="trade-mode">Trade mode</label>
          <select
            id="trade-mode"
            className="control-input"
            value={config.tradeMode}
            onChange={(event) => setConfig((prev) => ({ ...prev, tradeMode: event.target.value }))}
          >
            <option value="paper">paper</option>
            <option value="live">live</option>
          </select>

          <label className="field-label" htmlFor="enable-autotrade">Enable autotrade</label>
          <select
            id="enable-autotrade"
            className="control-input"
            value={config.enableAutotrade}
            onChange={(event) => setConfig((prev) => ({ ...prev, enableAutotrade: event.target.value }))}
          >
            <option value="true">true</option>
            <option value="false">false</option>
          </select>

          <label className="field-label" htmlFor="enable-live-execution">Enable live execution</label>
          <select
            id="enable-live-execution"
            className="control-input"
            value={config.enableLiveExecution}
            onChange={(event) => setConfig((prev) => ({ ...prev, enableLiveExecution: event.target.value }))}
          >
            <option value="false">false</option>
            <option value="true">true</option>
          </select>

          <label className="field-label" htmlFor="enable-crypto">Live crypto enabled</label>
          <select
            id="enable-crypto"
            className="control-input"
            value={config.liveEnableCrypto}
            onChange={(event) => setConfig((prev) => ({ ...prev, liveEnableCrypto: event.target.value }))}
          >
            <option value="true">true</option>
            <option value="false">false</option>
          </select>

          <label className="field-label" htmlFor="scan-interval">Scan interval seconds</label>
          <input
            id="scan-interval"
            className="control-input"
            type="number"
            min="300"
            step="60"
            value={config.scanIntervalSeconds}
            onChange={(event) => setConfig((prev) => ({ ...prev, scanIntervalSeconds: event.target.value }))}
          />

          <label className="field-label" htmlFor="signal-cooldown">Signal cooldown minutes</label>
          <input
            id="signal-cooldown"
            className="control-input"
            type="number"
            min="1"
            step="1"
            value={config.signalCooldownMinutes}
            onChange={(event) => setConfig((prev) => ({ ...prev, signalCooldownMinutes: event.target.value }))}
          />

          <label className="field-label" htmlFor="autotune-hours">Autotune interval hours</label>
          <select
            id="autotune-hours"
            className="control-input"
            value={config.autotuneHours}
            onChange={(event) => setConfig((prev) => ({ ...prev, autotuneHours: event.target.value }))}
          >
            <option value="6">6</option>
            <option value="12">12</option>
            <option value="24">24</option>
            <option value="168">168</option>
          </select>

          <label className="field-label full-span" htmlFor="bybit-symbols">Tracked symbols</label>
          <input
            id="bybit-symbols"
            className="control-input full-span"
            type="text"
            value={config.bybitSymbols}
            onChange={(event) => setConfig((prev) => ({ ...prev, bybitSymbols: event.target.value }))}
            placeholder="BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,BNBUSDT"
          />

          <div className="button-row full-span">
            <button className="action-button" type="submit" disabled={runtimeSaving || !writeReady}>
              {runtimeSaving ? "Saving..." : "Save runtime profile"}
            </button>
          </div>
        </form>
        {runtimeMessage ? <p className="success-line">{runtimeMessage}</p> : null}
        {runtimeError ? <p className="error-line">{runtimeError}</p> : null}
      </section>
    </section>
  );
}
