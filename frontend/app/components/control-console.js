"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";

const ADMIN_TOKEN_STORAGE_KEY = "ai_auto_service_admin_token";
const RESET_CONFIRM_TEXT = "RESET FUTURES DEMO";

const PROVIDERS = [
  {
    id: "bybit",
    label: "Bybit",
    role: "Execution account",
    description:
      "Used for execution readiness and account connectivity checks. This build still routes crypto fills through demo path.",
    requiresSecret: true,
    keyLabel: "Bybit API Key",
    secretLabel: "Bybit API Secret",
  },
  {
    id: "binance",
    label: "Binance",
    role: "Realtime quote feed",
    description:
      "Primary source for rank-lock and intrabar simulation. Keep enabled for stable paper execution.",
    requiresSecret: true,
    keyLabel: "Binance API Key",
    secretLabel: "Binance API Secret",
  },
  {
    id: "coingecko",
    label: "CoinGecko",
    role: "Universe metadata source",
    description: "Optional metadata provider for rank and macro context. Exchange-only mode can run with this disabled.",
    requiresSecret: false,
    keyLabel: "CoinGecko API Key",
    secretLabel: "",
  },
];

function boolToString(value) {
  return value ? "true" : "false";
}

function providerInitialState() {
  return {
    bybit: { apiKey: "", apiSecret: "" },
    binance: { apiKey: "", apiSecret: "" },
    coingecko: { apiKey: "", apiSecret: "" },
  };
}

function friendlyError(error) {
  const message = error instanceof Error ? error.message : String(error || "unknown_error");
  if (message === "unauthorized") {
    return "Admin token is missing or invalid. Re-enter SERVICE_ADMIN_TOKEN and try again.";
  }
  if (message === "provider_required") {
    return "Provider is required.";
  }
  if (message === "service_master_key_missing") {
    return "SERVICE_MASTER_KEY is missing on server environment.";
  }
  if (message === "reset_confirmation_required") {
    return `Type ${RESET_CONFIRM_TEXT} exactly in the confirmation field.`;
  }
  if (message === "crypto_tune_overrides_invalid_json") {
    return "Crypto tune overrides must be a valid JSON object.";
  }
  return message;
}

function conflictPolicyHelp(value) {
  const normalized = String(value || "conservative").toLowerCase();
  if (normalized === "aggressive") {
    return "If TP and SL are both touched in one candle, TP is applied first.";
  }
  if (normalized === "neutral") {
    return "If TP and SL are both touched in one candle, the level closer to candle open is applied.";
  }
  return "If TP and SL are both touched in one candle, SL is applied first.";
}

function sourceGuardForConfig(config) {
  const orderBlank = !String(config?.cryptoDataSourceOrder || "").trim();
  const useBinance = String(config?.useBinanceData || "true") === "true";
  const useBybit = String(config?.useBybitData || "true") === "true";
  const useCoingecko = String(config?.useCoingeckoData || "true") === "true";
  const allDisabled = !useBinance && !useBybit && !useCoingecko;
  const realtimeDisabled = !useBinance && !useBybit;
  if (allDisabled) {
    return {
      autoRepair: true,
      message: "All crypto data sources are off. Saving will restore Binance + Bybit so paper trading can keep running.",
    };
  }
  if (realtimeDisabled) {
    return {
      autoRepair: true,
      message: "Realtime quote sources are off. Saving will restore Binance + Bybit so paper trading can keep running.",
    };
  }
  if (orderBlank) {
    return {
      autoRepair: true,
      message: "The source order is blank. Saving will restore the default demo order.",
    };
  }
  return { autoRepair: false, message: "" };
}

export default function ControlConsole({ initialConfig, runtimeUpdatedAt, providerStatuses, writeReady }) {
  const router = useRouter();
  const [adminToken, setAdminToken] = useState("");
  const [providerForms, setProviderForms] = useState(providerInitialState);
  const [config, setConfig] = useState({
    executionTarget: String(initialConfig?.EXECUTION_TARGET || "paper"),
    enableAutotrade: boolToString(Boolean(initialConfig?.ENABLE_AUTOTRADE)),
    enableLiveExecution: boolToString(Boolean(initialConfig?.ENABLE_LIVE_EXECUTION)),
    liveEnableCrypto: boolToString(Boolean(initialConfig?.LIVE_ENABLE_CRYPTO)),
    liveExecutionArmed: boolToString(Boolean(initialConfig?.LIVE_EXECUTION_ARMED)),
    demoSeedUsdt: String(initialConfig?.DEMO_SEED_USDT || 10000),
    scanIntervalSeconds: String(initialConfig?.SCAN_INTERVAL_SECONDS || 60),
    signalCooldownMinutes: String(initialConfig?.SIGNAL_COOLDOWN_MINUTES || 10),
    autotuneHours: String(initialConfig?.MODEL_AUTOTUNE_INTERVAL_HOURS || 168),
    bybitMaxPositions: String(initialConfig?.BYBIT_MAX_POSITIONS || 3),
    bybitOrderPctMin: String(initialConfig?.BYBIT_ORDER_PCT_MIN || 0.15),
    bybitOrderPctMax: String(initialConfig?.BYBIT_ORDER_PCT_MAX || 0.3),
    intrabarConflictPolicy: String(initialConfig?.INTRABAR_CONFLICT_POLICY || "conservative"),
    bybitSymbols: String(initialConfig?.BYBIT_SYMBOLS || "BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,BNBUSDT"),
    cryptoUniverseMode: String(initialConfig?.CRYPTO_UNIVERSE_MODE || "rank_lock"),
    cryptoPrioritySymbols: String(initialConfig?.CRYPTO_PRIORITY_SYMBOLS || ""),
    macroTrendPoolSize: String(initialConfig?.MACRO_TREND_POOL_SIZE || 20),
    macroTrendReselectSeconds: String(initialConfig?.MACRO_TREND_RESELECT_SECONDS || 14400),
    macroRankMin: String(initialConfig?.MACRO_RANK_MIN || 1),
    macroRankMax: String(initialConfig?.MACRO_RANK_MAX || 20),
    cryptoTuneOverrides: JSON.stringify(initialConfig?.CRYPTO_TUNE_OVERRIDES || {}, null, 2),
    cryptoDataSourceOrder: String(initialConfig?.CRYPTO_DATA_SOURCE_ORDER || "binance,bybit"),
    useBinanceData: boolToString(Boolean(initialConfig?.CRYPTO_USE_BINANCE_DATA ?? true)),
    useBybitData: boolToString(Boolean(initialConfig?.CRYPTO_USE_BYBIT_DATA ?? true)),
    useCoingeckoData: boolToString(Boolean(initialConfig?.CRYPTO_USE_COINGECKO_DATA ?? false)),
  });
  const [runtimeMessage, setRuntimeMessage] = useState("");
  const [runtimeError, setRuntimeError] = useState("");
  const [providerMessages, setProviderMessages] = useState({});
  const [providerErrors, setProviderErrors] = useState({});
  const [runtimeSaving, setRuntimeSaving] = useState(false);
  const [providerSaving, setProviderSaving] = useState({});
  const [resetSeedUsdt, setResetSeedUsdt] = useState(String(initialConfig?.DEMO_SEED_USDT || 10000));
  const [resetConfirmText, setResetConfirmText] = useState("");
  const [resetMessage, setResetMessage] = useState("");
  const [resetError, setResetError] = useState("");
  const [resetting, setResetting] = useState(false);

  const hasAdminToken = adminToken.trim().length > 0;

  useEffect(() => {
    try {
      const saved = window.localStorage.getItem(ADMIN_TOKEN_STORAGE_KEY) || "";
      if (saved) {
        setAdminToken(saved);
      }
    } catch {}
  }, []);

  useEffect(() => {
    try {
      if (adminToken.trim()) {
        window.localStorage.setItem(ADMIN_TOKEN_STORAGE_KEY, adminToken.trim());
      } else {
        window.localStorage.removeItem(ADMIN_TOKEN_STORAGE_KEY);
      }
    } catch {}
  }, [adminToken]);

  const liveSummary = useMemo(() => {
    const bybitConfigured = Boolean(providerStatuses?.bybit?.configured);
    const target = config.executionTarget;
    const liveFlagsReady = config.enableLiveExecution === "true" && config.liveEnableCrypto === "true";
    const armed = config.liveExecutionArmed === "true" && bybitConfigured;

    if (target === "paper") {
      return "Current mode is paper. No real order is sent.";
    }
    if (!bybitConfigured) {
      return "bybit-live is selected, but Bybit credentials are not configured yet.";
    }
    if (!liveFlagsReady) {
      return "bybit-live is selected, but live flags are still disabled.";
    }
    if (!armed) {
      return "Live prerequisites are mostly ready, but arm flag is still off.";
    }
    return "Live prerequisites are all set, but this build still routes crypto fills to demo path.";
  }, [config, providerStatuses]);

  const statusBadges = useMemo(() => {
    const configured = Boolean(providerStatuses?.bybit?.configured);
    const liveFlagsReady = config.enableLiveExecution === "true" && config.liveEnableCrypto === "true";
    const armed = config.liveExecutionArmed === "true" && configured;
    const futureLiveEligible = config.executionTarget === "bybit-live" && liveFlagsReady && armed;

    return {
      safe: !futureLiveEligible,
      configured,
      armed,
    };
  }, [config.enableLiveExecution, config.executionTarget, config.liveEnableCrypto, config.liveExecutionArmed, providerStatuses]);

  const sourceGuard = useMemo(() => sourceGuardForConfig(config), [config]);

  function updateProviderForm(provider, field, value) {
    setProviderForms((prev) => ({
      ...prev,
      [provider]: {
        ...prev[provider],
        [field]: value,
      },
    }));
  }

  async function saveRuntime(event) {
    event.preventDefault();
    setRuntimeSaving(true);
    setRuntimeError("");
    setRuntimeMessage("");

    try {
      let parsedTuneOverrides = {};
      try {
        parsedTuneOverrides = JSON.parse(config.cryptoTuneOverrides || "{}");
      } catch {
        throw new Error("crypto_tune_overrides_invalid_json");
      }
      if (!parsedTuneOverrides || typeof parsedTuneOverrides !== "object" || Array.isArray(parsedTuneOverrides)) {
        throw new Error("crypto_tune_overrides_invalid_json");
      }
      const response = await fetch("/api/service/runtime", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          adminToken,
          config: {
            EXECUTION_TARGET: config.executionTarget,
            ENABLE_AUTOTRADE: config.enableAutotrade === "true",
            ENABLE_LIVE_EXECUTION: config.enableLiveExecution === "true",
            LIVE_ENABLE_CRYPTO: config.liveEnableCrypto === "true",
            LIVE_EXECUTION_ARMED: config.liveExecutionArmed === "true",
            DEMO_SEED_USDT: Number(config.demoSeedUsdt || 10000),
            SCAN_INTERVAL_SECONDS: Number(config.scanIntervalSeconds || 60),
            SIGNAL_COOLDOWN_MINUTES: Number(config.signalCooldownMinutes || 10),
            MODEL_AUTOTUNE_INTERVAL_HOURS: Number(config.autotuneHours || 168),
            BYBIT_MAX_POSITIONS: Number(config.bybitMaxPositions || 3),
            BYBIT_ORDER_PCT_MIN: Number(config.bybitOrderPctMin || 0.15),
            BYBIT_ORDER_PCT_MAX: Number(config.bybitOrderPctMax || 0.3),
            INTRABAR_CONFLICT_POLICY: config.intrabarConflictPolicy,
            BYBIT_SYMBOLS: config.bybitSymbols,
            CRYPTO_UNIVERSE_MODE: config.cryptoUniverseMode,
            CRYPTO_DYNAMIC_UNIVERSE_ENABLED: config.cryptoUniverseMode === "dynamic",
            CRYPTO_PRIORITY_SYMBOLS: config.cryptoPrioritySymbols,
            CRYPTO_TUNE_OVERRIDES: parsedTuneOverrides,
            MACRO_TREND_POOL_SIZE: Number(config.macroTrendPoolSize || 20),
            MACRO_TREND_RESELECT_SECONDS: Number(config.macroTrendReselectSeconds || 14400),
            MACRO_RANK_MIN: Number(config.macroRankMin || 1),
            MACRO_RANK_MAX: Number(config.macroRankMax || 20),
            CRYPTO_DATA_SOURCE_ORDER: config.cryptoDataSourceOrder,
            CRYPTO_USE_BINANCE_DATA: config.useBinanceData === "true",
            CRYPTO_USE_BYBIT_DATA: config.useBybitData === "true",
            CRYPTO_USE_COINGECKO_DATA: config.useCoingeckoData === "true",
          },
        }),
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) {
        throw new Error(payload.error || "runtime_save_failed");
      }
      setRuntimeMessage("Runtime profile saved. The next cycle will apply updated values.");
      if (sourceGuard.autoRepair) {
        setRuntimeMessage("Runtime profile saved. Default demo source settings were auto-restored.");
      }
      router.refresh();
    } catch (error) {
      setRuntimeError(friendlyError(error));
    } finally {
      setRuntimeSaving(false);
    }
  }

  async function saveProvider(event, provider) {
    event.preventDefault();
    const providerLabel = PROVIDERS.find((item) => item.id === provider)?.label || provider;
    setProviderSaving((prev) => ({ ...prev, [provider]: true }));
    setProviderErrors((prev) => ({ ...prev, [provider]: "" }));
    setProviderMessages((prev) => ({ ...prev, [provider]: "" }));

    try {
      const response = await fetch(`/api/service/credentials/${provider}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          adminToken,
          apiKey: providerForms[provider]?.apiKey || "",
          apiSecret: providerForms[provider]?.apiSecret || "",
        }),
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) {
        throw new Error(payload.error || "provider_save_failed");
      }
      setProviderForms((prev) => ({
        ...prev,
        [provider]: { apiKey: "", apiSecret: "" },
      }));
      setProviderMessages((prev) => ({
        ...prev,
        [provider]: `${providerLabel} credentials saved in Supabase vault.`,
      }));
      router.refresh();
    } catch (error) {
      setProviderErrors((prev) => ({
        ...prev,
        [provider]: friendlyError(error),
      }));
    } finally {
      setProviderSaving((prev) => ({ ...prev, [provider]: false }));
    }
  }

  async function clearProvider(provider) {
    const providerLabel = PROVIDERS.find((item) => item.id === provider)?.label || provider;
    setProviderSaving((prev) => ({ ...prev, [provider]: true }));
    setProviderErrors((prev) => ({ ...prev, [provider]: "" }));
    setProviderMessages((prev) => ({ ...prev, [provider]: "" }));

    try {
      const response = await fetch(`/api/service/credentials/${provider}`, {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ adminToken }),
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) {
        throw new Error(payload.error || "provider_delete_failed");
      }
      setProviderMessages((prev) => ({
        ...prev,
        [provider]: `${providerLabel} credentials removed from vault.`,
      }));
      router.refresh();
    } catch (error) {
      setProviderErrors((prev) => ({
        ...prev,
        [provider]: friendlyError(error),
      }));
    } finally {
      setProviderSaving((prev) => ({ ...prev, [provider]: false }));
    }
  }

  async function runHardReset(event) {
    event.preventDefault();
    setResetting(true);
    setResetError("");
    setResetMessage("");

    try {
      const response = await fetch("/api/service/reset", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          adminToken,
          seedUsdt: Number(resetSeedUsdt || 10000),
          confirmText: resetConfirmText,
        }),
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) {
        throw new Error(payload.error || "service_demo_reset_failed");
      }
      const nextSeed = String(payload.seedUsdt || resetSeedUsdt || 10000);
      setConfig((prev) => ({ ...prev, demoSeedUsdt: nextSeed }));
      setResetSeedUsdt(nextSeed);
      setResetConfirmText("");
      setResetMessage(`Hard reset completed. Next cycle starts with seed ${nextSeed} USDT.`);
      router.refresh();
    } catch (error) {
      setResetError(friendlyError(error));
    } finally {
      setResetting(false);
    }
  }

  return (
    <section className="section-card service-panel" id="service-control">
      <div className="section-head">
        <div>
          <p className="section-eyebrow">Service Console</p>
          <h2 className="section-title">Vault Credentials And Runtime Controls</h2>
        </div>
        <span className="section-meta">{writeReady ? "write ready" : "read only"}</span>
      </div>

      <div className="service-grid service-grid-top">
        <section className="control-card">
          <h3>Admin Token</h3>
          <p className="control-copy">
            Token is stored in browser local storage only. It is required for provider updates, runtime profile saves, and hard reset.
          </p>
          <label className="field-label" htmlFor="admin-token">
            SERVICE_ADMIN_TOKEN
          </label>
          <input
            id="admin-token"
            className="control-input"
            type="password"
            value={adminToken}
            onChange={(event) => setAdminToken(event.target.value)}
            placeholder="Enter SERVICE_ADMIN_TOKEN"
          />
          <p className="status-line">
            status: <strong>{hasAdminToken ? "loaded" : "required"}</strong>
          </p>
          {!writeReady ? (
            <p className="error-line">
              Set `SERVICE_ADMIN_TOKEN`, `SERVICE_MASTER_KEY`, and `SUPABASE_SERVICE_ROLE_KEY` (or `SUPABASE_SECRET_KEY`) in Vercel.
            </p>
          ) : null}
          {writeReady && !hasAdminToken ? <p className="error-line">Enter admin token before running write operations.</p> : null}
        </section>

        <section className="control-card execution-card">
          <div className="control-head">
            <h3>Execution target</h3>
            <span>{config.executionTarget}</span>
          </div>
          <p className="control-copy">This panel updates runtime profile only. Current build keeps crypto fills on demo path.</p>
          <div className="badge-row">
            <span className={`status-badge ${statusBadges.safe ? "active safe" : "inactive"}`}>safe</span>
            <span className={`status-badge ${statusBadges.configured ? "active configured" : "inactive"}`}>configured</span>
            <span className={`status-badge ${statusBadges.armed ? "active armed" : "inactive"}`}>armed</span>
          </div>
          <p className="status-line">{liveSummary}</p>
          <div className="status-stack compact">
            <p className="status-line">
              key hint <strong>{providerStatuses?.bybit?.api_key_hint || "not_set"}</strong>
            </p>
            <p className="status-line">
              last update <strong>{providerStatuses?.bybit?.updated_at || "-"}</strong>
            </p>
            <p className="status-line">
              intrabar policy <strong>{config.intrabarConflictPolicy}</strong>
            </p>
          </div>
        </section>
      </div>

      <section className="control-card provider-section">
        <div className="control-head">
          <h3>Provider Vault Credentials</h3>
          <span>execution + market data split</span>
        </div>
        <p className="control-copy">
          Credentials are written to Supabase vault via encrypted RPC. Keep GitHub secrets and runtime vault credentials separate.
        </p>
        <div className="provider-grid">
          {PROVIDERS.map((provider) => {
            const status = providerStatuses?.[provider.id];
            const form = providerForms[provider.id] || { apiKey: "", apiSecret: "" };
            return (
              <section key={provider.id} className="control-card provider-card">
                <div className="control-head">
                  <h3>{provider.label}</h3>
                  <span>{status?.configured ? "configured" : "empty"}</span>
                </div>
                <p className="control-copy">{provider.description}</p>
                <div className="status-stack compact">
                  <p className="status-line">
                    role <strong>{provider.role}</strong>
                  </p>
                  <p className="status-line">
                    key hint <strong>{status?.api_key_hint || "not_set"}</strong>
                  </p>
                  <p className="status-line">
                    last update <strong>{status?.updated_at || "-"}</strong>
                  </p>
                </div>
                <form className="control-form" onSubmit={(event) => saveProvider(event, provider.id)}>
                  <label className="field-label" htmlFor={`${provider.id}-key`}>
                    {provider.keyLabel}
                  </label>
                  <input
                    id={`${provider.id}-key`}
                    className="control-input"
                    type="password"
                    value={form.apiKey}
                    onChange={(event) => updateProviderForm(provider.id, "apiKey", event.target.value)}
                    placeholder={`Enter ${provider.label} API Key`}
                  />
                  {provider.requiresSecret ? (
                    <>
                      <label className="field-label" htmlFor={`${provider.id}-secret`}>
                        {provider.secretLabel}
                      </label>
                      <input
                        id={`${provider.id}-secret`}
                        className="control-input"
                        type="password"
                        value={form.apiSecret}
                        onChange={(event) => updateProviderForm(provider.id, "apiSecret", event.target.value)}
                        placeholder={`Enter ${provider.label} API Secret`}
                      />
                    </>
                  ) : null}
                  <div className="button-row">
                    <button className="action-button" type="submit" disabled={providerSaving[provider.id] || !writeReady || !hasAdminToken}>
                      {providerSaving[provider.id] ? "saving..." : `save ${provider.label}`}
                    </button>
                    <button
                      className="action-button ghost"
                      type="button"
                      disabled={providerSaving[provider.id] || !writeReady || !hasAdminToken}
                      onClick={() => clearProvider(provider.id)}
                    >
                      clear
                    </button>
                  </div>
                </form>
                {providerMessages[provider.id] ? <p className="success-line">{providerMessages[provider.id]}</p> : null}
                {providerErrors[provider.id] ? <p className="error-line">{providerErrors[provider.id]}</p> : null}
              </section>
            );
          })}
        </div>
      </section>

      <section className="control-card runtime-card">
        <div className="control-head">
          <h3>Runtime profile</h3>
          <span>{runtimeUpdatedAt ? "loaded from Supabase" : "default profile"}</span>
        </div>
        <p className="control-copy">
          Controls scan cadence, rank window, risk sizing, intrabar conflict rule, and source priority. Updated values apply in next cycle.
        </p>
        <form className="control-form runtime-form" onSubmit={saveRuntime}>
          <label className="field-label" htmlFor="execution-target">
            Execution target
          </label>
          <select
            id="execution-target"
            className="control-input"
            value={config.executionTarget}
            onChange={(event) => setConfig((prev) => ({ ...prev, executionTarget: event.target.value }))}
          >
            <option value="paper">paper</option>
            <option value="bybit-live">bybit-live</option>
          </select>

          <label className="field-label" htmlFor="enable-autotrade">
            ENABLE_AUTOTRADE
          </label>
          <select
            id="enable-autotrade"
            className="control-input"
            value={config.enableAutotrade}
            onChange={(event) => setConfig((prev) => ({ ...prev, enableAutotrade: event.target.value }))}
          >
            <option value="true">true</option>
            <option value="false">false</option>
          </select>

          <label className="field-label" htmlFor="enable-live-execution">
            ENABLE_LIVE_EXECUTION
          </label>
          <select
            id="enable-live-execution"
            className="control-input"
            value={config.enableLiveExecution}
            onChange={(event) => setConfig((prev) => ({ ...prev, enableLiveExecution: event.target.value }))}
          >
            <option value="false">false</option>
            <option value="true">true</option>
          </select>

          <label className="field-label" htmlFor="enable-crypto">
            LIVE_ENABLE_CRYPTO
          </label>
          <select
            id="enable-crypto"
            className="control-input"
            value={config.liveEnableCrypto}
            onChange={(event) => setConfig((prev) => ({ ...prev, liveEnableCrypto: event.target.value }))}
          >
            <option value="false">false</option>
            <option value="true">true</option>
          </select>

          <label className="field-label" htmlFor="live-arm">
            Live arm
          </label>
          <select
            id="live-arm"
            className="control-input"
            value={config.liveExecutionArmed}
            disabled={!providerStatuses?.bybit?.configured}
            onChange={(event) => setConfig((prev) => ({ ...prev, liveExecutionArmed: event.target.value }))}
          >
            <option value="false">false</option>
            <option value="true">true</option>
          </select>

          <label className="field-label" htmlFor="demo-seed">
            DEMO_SEED_USDT (next reset)
          </label>
          <input
            id="demo-seed"
            className="control-input"
            type="number"
            min="1000"
            step="500"
            value={config.demoSeedUsdt}
            onChange={(event) => setConfig((prev) => ({ ...prev, demoSeedUsdt: event.target.value }))}
          />
          <p className="status-line full-span">
            Saving runtime profile does not wipe current seed, open positions, or PnL. Use hard reset below to reinitialize demo state.
          </p>

          <label className="field-label" htmlFor="max-positions">
            BYBIT_MAX_POSITIONS
          </label>
          <input
            id="max-positions"
            className="control-input"
            type="number"
            min="1"
            max="10"
            step="1"
            value={config.bybitMaxPositions}
            onChange={(event) => setConfig((prev) => ({ ...prev, bybitMaxPositions: event.target.value }))}
          />

          <label className="field-label" htmlFor="order-pct-min">
            BYBIT_ORDER_PCT_MIN
          </label>
          <input
            id="order-pct-min"
            className="control-input"
            type="number"
            min="0.10"
            max="0.30"
            step="0.01"
            value={config.bybitOrderPctMin}
            onChange={(event) => setConfig((prev) => ({ ...prev, bybitOrderPctMin: event.target.value }))}
          />

          <label className="field-label" htmlFor="order-pct-max">
            BYBIT_ORDER_PCT_MAX
          </label>
          <input
            id="order-pct-max"
            className="control-input"
            type="number"
            min="0.10"
            max="0.30"
            step="0.01"
            value={config.bybitOrderPctMax}
            onChange={(event) => setConfig((prev) => ({ ...prev, bybitOrderPctMax: event.target.value }))}
          />

          <label className="field-label" htmlFor="intrabar-conflict-policy">
            INTRABAR_CONFLICT_POLICY
          </label>
          <select
            id="intrabar-conflict-policy"
            className="control-input"
            value={config.intrabarConflictPolicy}
            onChange={(event) => setConfig((prev) => ({ ...prev, intrabarConflictPolicy: event.target.value }))}
          >
            <option value="conservative">conservative / SL first</option>
            <option value="neutral">neutral / nearest to open first</option>
            <option value="aggressive">aggressive / TP first</option>
          </select>
          <p className="status-line full-span">{conflictPolicyHelp(config.intrabarConflictPolicy)}</p>

          <label className="field-label" htmlFor="autotune-hours">
            MODEL_AUTOTUNE_INTERVAL_HOURS
          </label>
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

          <label className="field-label" htmlFor="scan-interval">
            SCAN_INTERVAL_SECONDS
          </label>
          <input
            id="scan-interval"
            className="control-input"
            type="number"
            min="60"
            step="60"
            value={config.scanIntervalSeconds}
            onChange={(event) => setConfig((prev) => ({ ...prev, scanIntervalSeconds: event.target.value }))}
          />

          <label className="field-label" htmlFor="signal-cooldown">
            SIGNAL_COOLDOWN_MINUTES
          </label>
          <input
            id="signal-cooldown"
            className="control-input"
            type="number"
            min="1"
            step="1"
            value={config.signalCooldownMinutes}
            onChange={(event) => setConfig((prev) => ({ ...prev, signalCooldownMinutes: event.target.value }))}
          />

          <label className="field-label" htmlFor="use-binance">
            CRYPTO_USE_BINANCE_DATA
          </label>
          <select
            id="use-binance"
            className="control-input"
            value={config.useBinanceData}
            onChange={(event) => setConfig((prev) => ({ ...prev, useBinanceData: event.target.value }))}
          >
            <option value="true">true</option>
            <option value="false">false</option>
          </select>

          <label className="field-label" htmlFor="use-bybit">
            CRYPTO_USE_BYBIT_DATA
          </label>
          <select
            id="use-bybit"
            className="control-input"
            value={config.useBybitData}
            onChange={(event) => setConfig((prev) => ({ ...prev, useBybitData: event.target.value }))}
          >
            <option value="true">true</option>
            <option value="false">false</option>
          </select>

          <label className="field-label" htmlFor="use-coingecko">
            CRYPTO_USE_COINGECKO_DATA
          </label>
          <select
            id="use-coingecko"
            className="control-input"
            value={config.useCoingeckoData}
            onChange={(event) => setConfig((prev) => ({ ...prev, useCoingeckoData: event.target.value }))}
          >
            <option value="true">true</option>
            <option value="false">false</option>
          </select>

          <label className="field-label" htmlFor="source-order">
            CRYPTO_DATA_SOURCE_ORDER
          </label>
          <input
            id="source-order"
            className="control-input"
            type="text"
            value={config.cryptoDataSourceOrder}
            onChange={(event) => setConfig((prev) => ({ ...prev, cryptoDataSourceOrder: event.target.value }))}
            placeholder="binance,bybit"
          />
          {sourceGuard.autoRepair ? <p className="error-line full-span">{sourceGuard.message}</p> : null}

          <label className="field-label full-span" htmlFor="bybit-symbols">
            BYBIT_SYMBOLS
          </label>
          <input
            id="bybit-symbols"
            className="control-input full-span"
            type="text"
            value={config.bybitSymbols}
            onChange={(event) => setConfig((prev) => ({ ...prev, bybitSymbols: event.target.value }))}
            placeholder="BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,BNBUSDT"
          />

          <label className="field-label" htmlFor="crypto-universe-mode">
            Universe mode
          </label>
          <select
            id="crypto-universe-mode"
            className="control-input"
            value={config.cryptoUniverseMode}
            onChange={(event) => setConfig((prev) => ({ ...prev, cryptoUniverseMode: event.target.value }))}
          >
            <option value="rank_lock">rank_lock / market-cap top 1-20</option>
            <option value="fixed_symbols">fixed_symbols / BYBIT_SYMBOLS only</option>
            <option value="dynamic">dynamic / rotating universe</option>
          </select>

          <label className="field-label" htmlFor="macro-rank-min">
            Rank min
          </label>
          <input
            id="macro-rank-min"
            className="control-input"
            type="number"
            min="1"
            max="5000"
            step="1"
            value={config.macroRankMin}
            onChange={(event) => setConfig((prev) => ({ ...prev, macroRankMin: event.target.value }))}
          />

          <label className="field-label" htmlFor="macro-rank-max">
            Rank max
          </label>
          <input
            id="macro-rank-max"
            className="control-input"
            type="number"
            min="1"
            max="5000"
            step="1"
            value={config.macroRankMax}
            onChange={(event) => setConfig((prev) => ({ ...prev, macroRankMax: event.target.value }))}
          />

          <label className="field-label" htmlFor="macro-trend-pool-size">
            Universe pool size
          </label>
          <input
            id="macro-trend-pool-size"
            className="control-input"
            type="number"
            min="20"
            max="200"
            step="1"
            value={config.macroTrendPoolSize}
            onChange={(event) => setConfig((prev) => ({ ...prev, macroTrendPoolSize: event.target.value }))}
          />

          <label className="field-label" htmlFor="macro-trend-reselect-seconds">
            Rotation interval(sec)
          </label>
          <input
            id="macro-trend-reselect-seconds"
            className="control-input"
            type="number"
            min="900"
            max="86400"
            step="900"
            value={config.macroTrendReselectSeconds}
            onChange={(event) => setConfig((prev) => ({ ...prev, macroTrendReselectSeconds: event.target.value }))}
          />

          <label className="field-label full-span" htmlFor="crypto-priority-symbols">
            Priority symbols (dynamic mode)
          </label>
          <input
            id="crypto-priority-symbols"
            className="control-input full-span"
            type="text"
            value={config.cryptoPrioritySymbols}
            onChange={(event) => setConfig((prev) => ({ ...prev, cryptoPrioritySymbols: event.target.value }))}
            placeholder="BTCUSDT,ETHUSDT"
          />
          <p className="status-line full-span">
            <strong>rank_lock</strong> keeps tradable symbols in the configured rank window and, when CoinGecko is off, falls back to exchange-turnover ranking from Binance + Bybit.
            <strong> fixed_symbols</strong> enforces <strong>BYBIT_SYMBOLS</strong> only, while <strong>dynamic</strong> rotates the universe each cycle.
          </p>
          <label className="field-label full-span" htmlFor="crypto-tune-overrides">
            Crypto tune overrides(JSON)
          </label>
          <textarea
            id="crypto-tune-overrides"
            className="control-input full-span"
            rows="12"
            value={config.cryptoTuneOverrides}
            onChange={(event) => setConfig((prev) => ({ ...prev, cryptoTuneOverrides: event.target.value }))}
            placeholder={'{\n  "A": { "threshold_bias": -0.003, "entry_atr_mul": 1.05 },\n  "B": { "floor_atr_mul": 1.10, "mid_atr_boost": 0.18 },\n  "D": { "entry_atr_mul": 1.35, "zone_high_atr": 0.38 }\n}'}
          />
          <p className="status-line full-span">
            Supported keys: <strong>threshold</strong>, <strong>threshold_bias</strong>, <strong>entry_atr_mul</strong>,
            <strong>floor_atr_mul</strong>, <strong>mid_atr_boost</strong>, <strong>zone_half_atr</strong>,
            <strong>zone_low_atr</strong>, <strong>zone_high_atr</strong>.
          </p>

          <div className="button-row full-span">
            <button className="action-button" type="submit" disabled={runtimeSaving || !writeReady || !hasAdminToken}>
              {runtimeSaving ? "saving..." : "save runtime profile"}
            </button>
          </div>
        </form>
        {runtimeMessage ? <p className="success-line">{runtimeMessage}</p> : null}
        {runtimeError ? <p className="error-line">{runtimeError}</p> : null}
      </section>

      <section className="control-card reset-card">
        <div className="control-head">
          <h3>Hard Reset Demo State</h3>
          <span>positions + pnl + runtime state reset</span>
        </div>
        <p className="control-copy">
          This clears demo positions, recent fills, daily pnl, and model tune state. Provider credentials and runtime profile remain intact.
        </p>
        <form className="control-form runtime-form" onSubmit={runHardReset}>
          <label className="field-label" htmlFor="reset-seed-usdt">
            Seed (USDT)
          </label>
          <input
            id="reset-seed-usdt"
            className="control-input"
            type="number"
            min="1000"
            step="500"
            value={resetSeedUsdt}
            onChange={(event) => setResetSeedUsdt(event.target.value)}
          />

          <label className="field-label" htmlFor="reset-confirm-text">
            Confirmation text
          </label>
          <input
            id="reset-confirm-text"
            className="control-input"
            type="text"
            value={resetConfirmText}
            onChange={(event) => setResetConfirmText(event.target.value)}
            placeholder={RESET_CONFIRM_TEXT}
          />

          <p className="status-line full-span">
            Type <strong>{RESET_CONFIRM_TEXT}</strong>. Reset immediately clears Supabase demo state and next cycle starts with seed {resetSeedUsdt || "10000"} USDT.
          </p>

          <div className="button-row full-span">
            <button
              className="action-button danger"
              type="submit"
              disabled={resetting || !writeReady || !hasAdminToken || resetConfirmText.trim().toUpperCase() !== RESET_CONFIRM_TEXT}
            >
              {resetting ? "resetting..." : "run hard reset"}
            </button>
          </div>
        </form>
        {resetMessage ? <p className="success-line">{resetMessage}</p> : null}
        {resetError ? <p className="error-line">{resetError}</p> : null}
      </section>
    </section>
  );
}






