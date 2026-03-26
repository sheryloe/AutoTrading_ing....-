"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";

const ADMIN_TOKEN_STORAGE_KEY = "ai_auto_service_admin_token";
const RESET_CONFIRM_TEXT = "RESET FUTURES DEMO";

const PROVIDERS = [
  {
    id: "bybit",
    label: "Bybit",
    role: "실행 계정",
    description:
      "실행 준비 상태와 계정 연결성을 점검하는 용도입니다. 현재 빌드는 크립토 체결을 데모 경로로 처리합니다.",
    requiresSecret: true,
    keyLabel: "Bybit API 키",
    secretLabel: "Bybit API 시크릿",
  },
  {
    id: "binance",
    label: "Binance",
    role: "실시간 시세 피드",
    description:
      "랭크락 및 인트라바 시뮬레이션의 기본 소스입니다. 안정적인 페이퍼 실행을 위해 활성화 상태를 유지하세요.",
    requiresSecret: true,
    keyLabel: "Binance API 키",
    secretLabel: "Binance API 시크릿",
  },
  {
    id: "coingecko",
    label: "CoinGecko",
    role: "유니버스 메타데이터 소스",
    description: "랭크/매크로 컨텍스트 보강용 선택 메타데이터 공급자입니다. 거래소 전용 모드에서는 비활성화해도 동작합니다.",
    requiresSecret: false,
    keyLabel: "CoinGecko API 키",
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
    return "관리자 토큰이 없거나 올바르지 않습니다. SERVICE_ADMIN_TOKEN을 다시 입력하세요.";
  }
  if (message === "provider_required") {
    return "프로바이더를 선택하세요.";
  }
  if (message === "service_master_key_missing") {
    return "서버 환경 변수에 SERVICE_MASTER_KEY가 없습니다.";
  }
  if (message === "reset_confirmation_required") {
    return `확인 입력란에 ${RESET_CONFIRM_TEXT} 를 정확히 입력하세요.`;
  }
  if (message === "crypto_tune_overrides_invalid_json") {
    return "Crypto tune overrides는 유효한 JSON 객체여야 합니다.";
  }
  return message;
}

function conflictPolicyHelp(value) {
  const normalized = String(value || "conservative").toLowerCase();
  if (normalized === "aggressive") {
    return "한 캔들에서 TP/SL이 동시에 터지면 TP를 먼저 적용합니다.";
  }
  if (normalized === "neutral") {
    return "한 캔들에서 TP/SL이 동시에 터지면 시가에 더 가까운 레벨을 먼저 적용합니다.";
  }
  return "한 캔들에서 TP/SL이 동시에 터지면 SL을 먼저 적용합니다.";
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
      message: "모든 크립토 데이터 소스가 꺼져 있습니다. 저장 시 Binance + Bybit를 복구해 페이퍼 거래를 계속 실행합니다.",
    };
  }
  if (realtimeDisabled) {
    return {
      autoRepair: true,
      message: "실시간 시세 소스가 꺼져 있습니다. 저장 시 Binance + Bybit를 복구해 페이퍼 거래를 계속 실행합니다.",
    };
  }
  if (orderBlank) {
    return {
      autoRepair: true,
      message: "소스 순서가 비어 있습니다. 저장 시 기본 데모 순서를 복구합니다.",
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
      return "현재 모드는 paper이며 실제 주문은 전송되지 않습니다.";
    }
    if (!bybitConfigured) {
      return "bybit-live가 선택되어 있지만 Bybit 자격정보가 아직 설정되지 않았습니다.";
    }
    if (!liveFlagsReady) {
      return "bybit-live가 선택되어 있지만 라이브 플래그가 비활성화 상태입니다.";
    }
    if (!armed) {
      return "라이브 사전 조건은 대부분 충족됐지만 arm 플래그가 꺼져 있습니다.";
    }
    return "라이브 사전 조건은 모두 충족됐지만 현재 빌드는 크립토 체결을 데모 경로로 처리합니다.";
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
      setRuntimeMessage("런타임 프로필을 저장했습니다. 다음 사이클부터 변경값이 적용됩니다.");
      if (sourceGuard.autoRepair) {
        setRuntimeMessage("런타임 프로필을 저장했습니다. 기본 데모 소스 설정이 자동 복구되었습니다.");
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
        [provider]: `${providerLabel} 자격정보를 Supabase vault에 저장했습니다.`,
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
        [provider]: `${providerLabel} 자격정보를 vault에서 삭제했습니다.`,
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
      setResetMessage(`하드 리셋이 완료되었습니다. 다음 사이클은 시드 ${nextSeed} USDT로 시작합니다.`);
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
          <p className="section-eyebrow">서비스 콘솔</p>
          <h2 className="section-title">Vault 자격정보 및 런타임 제어</h2>
        </div>
        <span className="section-meta">{writeReady ? "쓰기 가능" : "읽기 전용"}</span>
      </div>

      <div className="service-grid service-grid-top">
        <section className="control-card">
          <h3>관리자 토큰</h3>
          <p className="control-copy">
            토큰은 브라우저 local storage에만 저장됩니다. 프로바이더 수정, 런타임 저장, 하드 리셋에 필요합니다.
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
            placeholder="SERVICE_ADMIN_TOKEN 입력"
          />
          <p className="status-line">
            상태: <strong>{hasAdminToken ? "로드됨" : "필수"}</strong>
          </p>
          {!writeReady ? (
            <p className="error-line">
              Vercel에 `SERVICE_ADMIN_TOKEN`, `SERVICE_MASTER_KEY`, `SUPABASE_SERVICE_ROLE_KEY`(또는 `SUPABASE_SECRET_KEY`)를 설정하세요.
            </p>
          ) : null}
          {writeReady && !hasAdminToken ? <p className="error-line">쓰기 작업 전에 관리자 토큰을 입력하세요.</p> : null}
        </section>

        <section className="control-card execution-card">
          <div className="control-head">
            <h3>실행 타깃</h3>
            <span>{config.executionTarget}</span>
          </div>
          <p className="control-copy">이 패널은 런타임 프로필만 업데이트합니다. 현재 빌드는 크립토 체결을 데모 경로로 유지합니다.</p>
          <div className="badge-row">
            <span className={`status-badge ${statusBadges.safe ? "active safe" : "inactive"}`}>안전</span>
            <span className={`status-badge ${statusBadges.configured ? "active configured" : "inactive"}`}>설정됨</span>
            <span className={`status-badge ${statusBadges.armed ? "active armed" : "inactive"}`}>활성</span>
          </div>
          <p className="status-line">{liveSummary}</p>
          <div className="status-stack compact">
            <p className="status-line">
              키 힌트 <strong>{providerStatuses?.bybit?.api_key_hint || "미설정"}</strong>
            </p>
            <p className="status-line">
              최근 업데이트 <strong>{providerStatuses?.bybit?.updated_at || "-"}</strong>
            </p>
            <p className="status-line">
              인트라바 정책 <strong>{config.intrabarConflictPolicy}</strong>
            </p>
          </div>
        </section>
      </div>

      <section className="control-card provider-section">
        <div className="control-head">
          <h3>프로바이더 Vault 자격정보</h3>
          <span>실행 + 마켓데이터 분리</span>
        </div>
        <p className="control-copy">
          자격정보는 암호화된 RPC를 통해 Supabase vault에 저장됩니다. GitHub 시크릿과 런타임 vault 자격정보를 분리하세요.
        </p>
        <div className="provider-grid">
          {PROVIDERS.map((provider) => {
            const status = providerStatuses?.[provider.id];
            const form = providerForms[provider.id] || { apiKey: "", apiSecret: "" };
            return (
              <section key={provider.id} className="control-card provider-card">
                <div className="control-head">
                  <h3>{provider.label}</h3>
                  <span>{status?.configured ? "설정됨" : "비어있음"}</span>
                </div>
                <p className="control-copy">{provider.description}</p>
                <div className="status-stack compact">
                  <p className="status-line">
                    역할 <strong>{provider.role}</strong>
                  </p>
                  <p className="status-line">
                    키 힌트 <strong>{status?.api_key_hint || "미설정"}</strong>
                  </p>
                  <p className="status-line">
                    최근 업데이트 <strong>{status?.updated_at || "-"}</strong>
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
                    placeholder={`${provider.label} API 키 입력`}
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
                        placeholder={`${provider.label} API 시크릿 입력`}
                      />
                    </>
                  ) : null}
                  <div className="button-row">
                    <button className="action-button" type="submit" disabled={providerSaving[provider.id] || !writeReady || !hasAdminToken}>
                      {providerSaving[provider.id] ? "저장 중..." : `${provider.label} 저장`}
                    </button>
                    <button
                      className="action-button ghost"
                      type="button"
                      disabled={providerSaving[provider.id] || !writeReady || !hasAdminToken}
                      onClick={() => clearProvider(provider.id)}
                    >
                      삭제
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
          <h3>런타임 프로필</h3>
          <span>{runtimeUpdatedAt ? "Supabase에서 로드됨" : "기본 프로필"}</span>
        </div>
        <p className="control-copy">
          스캔 주기, 랭크 윈도우, 리스크 사이징, 인트라바 충돌 규칙, 소스 우선순위를 제어합니다. 변경값은 다음 사이클에 적용됩니다.
        </p>
        <form className="control-form runtime-form" onSubmit={saveRuntime}>
          <label className="field-label" htmlFor="execution-target">
            실행 타깃
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
            라이브 arm
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
            DEMO_SEED_USDT (다음 리셋 적용)
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
            런타임 프로필 저장은 현재 시드, 오픈 포지션, PnL을 지우지 않습니다. 데모 상태 재초기화는 아래 하드 리셋을 사용하세요.
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
            <option value="conservative">conservative / SL 우선</option>
            <option value="neutral">neutral / 시가 근접 레벨 우선</option>
            <option value="aggressive">aggressive / TP 우선</option>
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
            유니버스 모드
          </label>
          <select
            id="crypto-universe-mode"
            className="control-input"
            value={config.cryptoUniverseMode}
            onChange={(event) => setConfig((prev) => ({ ...prev, cryptoUniverseMode: event.target.value }))}
          >
            <option value="rank_lock">rank_lock / 시총 상위 1~20</option>
            <option value="fixed_symbols">fixed_symbols / BYBIT_SYMBOLS만 사용</option>
            <option value="dynamic">dynamic / 순환 유니버스</option>
          </select>

          <label className="field-label" htmlFor="macro-rank-min">
            랭크 최소
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
            랭크 최대
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
            유니버스 풀 크기
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
            로테이션 주기(초)
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
            우선 심볼(dynamic 모드)
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
            <strong>rank_lock</strong>는 설정된 랭크 구간에서 거래 가능한 심볼을 유지하며, CoinGecko가 꺼져 있으면 Binance + Bybit 거래대금 랭킹으로 대체합니다.
            <strong> fixed_symbols</strong>는 <strong>BYBIT_SYMBOLS</strong>만 강제하고, <strong>dynamic</strong>은 매 사이클 유니버스를 회전합니다.
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
            지원 키: <strong>threshold</strong>, <strong>threshold_bias</strong>, <strong>entry_atr_mul</strong>,
            <strong>floor_atr_mul</strong>, <strong>mid_atr_boost</strong>, <strong>zone_half_atr</strong>,
            <strong>zone_low_atr</strong>, <strong>zone_high_atr</strong>.
          </p>

          <div className="button-row full-span">
            <button className="action-button" type="submit" disabled={runtimeSaving || !writeReady || !hasAdminToken}>
              {runtimeSaving ? "저장 중..." : "런타임 프로필 저장"}
            </button>
          </div>
        </form>
        {runtimeMessage ? <p className="success-line">{runtimeMessage}</p> : null}
        {runtimeError ? <p className="error-line">{runtimeError}</p> : null}
      </section>

      <section className="control-card reset-card">
        <div className="control-head">
          <h3>데모 상태 하드 리셋</h3>
          <span>positions + pnl + 런타임 상태 초기화</span>
        </div>
        <p className="control-copy">
          데모 포지션, 최근 체결, 일별 pnl, 모델 튜닝 상태를 초기화합니다. 프로바이더 자격정보와 런타임 프로필은 유지됩니다.
        </p>
        <form className="control-form runtime-form" onSubmit={runHardReset}>
          <label className="field-label" htmlFor="reset-seed-usdt">
            시드 (USDT)
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
            확인 문구
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
            <strong>{RESET_CONFIRM_TEXT}</strong>를 입력하세요. 리셋 즉시 Supabase 데모 상태를 정리하고 다음 사이클은 시드 {resetSeedUsdt || "10000"} USDT로 시작합니다.
          </p>

          <div className="button-row full-span">
            <button
              className="action-button danger"
              type="submit"
              disabled={resetting || !writeReady || !hasAdminToken || resetConfirmText.trim().toUpperCase() !== RESET_CONFIRM_TEXT}
            >
              {resetting ? "리셋 중..." : "하드 리셋 실행"}
            </button>
          </div>
        </form>
        {resetMessage ? <p className="success-line">{resetMessage}</p> : null}
        {resetError ? <p className="error-line">{resetError}</p> : null}
      </section>
    </section>
  );
}






