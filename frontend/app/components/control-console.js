"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";

const ADMIN_TOKEN_STORAGE_KEY = "ai_auto_service_admin_token";
const RESET_CONFIRM_TEXT = "RESET FUTURES DEMO";

const PROVIDERS = [
  {
    id: "bybit",
    label: "Bybit",
    role: "선물 실행 계정",
    description:
      "선물 실행에 사용할 거래소 계정입니다. 키를 저장해도 바로 라이브 주문이 켜지지는 않고, execution target과 arm 단계를 따로 통과해야 합니다.",
    requiresSecret: true,
    keyLabel: "Bybit API Key",
    secretLabel: "Bybit API Secret",
  },
  {
    id: "binance",
    label: "Binance",
    role: "실시간 시세 소스",
    description:
      "Top 5 메이저 코인의 가격과 1분봉 intrabar 판정에 우선으로 사용하는 데이터 소스입니다.",
    requiresSecret: true,
    keyLabel: "Binance API Key",
    secretLabel: "Binance API Secret",
  },
  {
    id: "coingecko",
    label: "CoinGecko",
    role: "유니버스 / 메타 소스",
    description: "시총, 코인 메타 정보, 보조 참조 데이터에 사용하는 보완 소스입니다.",
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
    return "관리자 토큰이 비어 있거나 일치하지 않습니다. 토큰을 다시 확인해 주세요.";
  }
  if (message === "provider_required") {
    return "저장할 provider 정보가 비어 있습니다.";
  }
  if (message === "service_master_key_missing") {
    return "SERVICE_MASTER_KEY가 서버에 설정되지 않았습니다.";
  }
  if (message === "reset_confirmation_required") {
    return `확인 문구 ${RESET_CONFIRM_TEXT} 를 정확히 입력해 주세요.`;
  }
  return message;
}

function conflictPolicyHelp(value) {
  const normalized = String(value || "conservative").toLowerCase();
  if (normalized === "aggressive") {
    return "같은 캔들에서 TP와 SL이 모두 닿으면 TP를 우선 반영합니다.";
  }
  if (normalized === "neutral") {
    return "같은 캔들의 시가를 기준으로 더 가까운 가격을 먼저 반영합니다.";
  }
  return "같은 캔들에서 TP와 SL이 모두 닿으면 SL을 우선 반영합니다.";
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
    scanIntervalSeconds: String(initialConfig?.SCAN_INTERVAL_SECONDS || 480),
    signalCooldownMinutes: String(initialConfig?.SIGNAL_COOLDOWN_MINUTES || 10),
    autotuneHours: String(initialConfig?.MODEL_AUTOTUNE_INTERVAL_HOURS || 168),
    bybitMaxPositions: String(initialConfig?.BYBIT_MAX_POSITIONS || 3),
    bybitOrderPctMin: String(initialConfig?.BYBIT_ORDER_PCT_MIN || 0.1),
    bybitOrderPctMax: String(initialConfig?.BYBIT_ORDER_PCT_MAX || 0.3),
    intrabarConflictPolicy: String(initialConfig?.INTRABAR_CONFLICT_POLICY || "conservative"),
    bybitSymbols: String(initialConfig?.BYBIT_SYMBOLS || "BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,BNBUSDT"),
    cryptoDataSourceOrder: String(initialConfig?.CRYPTO_DATA_SOURCE_ORDER || "binance,bybit,coingecko"),
    useBinanceData: boolToString(Boolean(initialConfig?.CRYPTO_USE_BINANCE_DATA ?? true)),
    useBybitData: boolToString(Boolean(initialConfig?.CRYPTO_USE_BYBIT_DATA ?? true)),
    useCoingeckoData: boolToString(Boolean(initialConfig?.CRYPTO_USE_COINGECKO_DATA ?? true)),
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
      return "현재는 paper 모드입니다. 선물 데모 포지션과 PnL만 관리하고 실주문은 열지 않습니다.";
    }
    if (!bybitConfigured) {
      return "bybit-live가 선택되어 있지만 Bybit 실행 키가 아직 비어 있습니다.";
    }
    if (!liveFlagsReady) {
      return "bybit-live가 선택되어 있지만 live 실행 플래그가 아직 꺼져 있습니다.";
    }
    if (!armed) {
      return "실행 조건은 거의 준비됐지만 arm 단계가 꺼져 있어서 future live 준비 상태로만 유지됩니다.";
    }
    return "future live execution 준비 상태입니다. 현재 빌드는 설정과 가드를 먼저 고정하는 단계이며, 실주문 라우팅은 별도 가드 아래에서 열게 됩니다.";
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
            SCAN_INTERVAL_SECONDS: Number(config.scanIntervalSeconds || 480),
            SIGNAL_COOLDOWN_MINUTES: Number(config.signalCooldownMinutes || 10),
            MODEL_AUTOTUNE_INTERVAL_HOURS: Number(config.autotuneHours || 168),
            BYBIT_MAX_POSITIONS: Number(config.bybitMaxPositions || 3),
            BYBIT_ORDER_PCT_MIN: Number(config.bybitOrderPctMin || 0.1),
            BYBIT_ORDER_PCT_MAX: Number(config.bybitOrderPctMax || 0.3),
            INTRABAR_CONFLICT_POLICY: config.intrabarConflictPolicy,
            BYBIT_SYMBOLS: config.bybitSymbols,
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
      setRuntimeMessage("런타임 프로필을 저장했습니다. 다음 8분 배치부터 새 규칙이 반영됩니다.");
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
        [provider]: `${providerLabel} 자격증명을 Supabase 암호화 vault에 저장했습니다.`,
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
        [provider]: `${providerLabel} 자격증명을 vault에서 비웠습니다.`,
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
      setResetMessage(`하드 리셋을 완료했습니다. 시드 ${nextSeed} USDT 기준으로 다음 배치부터 다시 시작합니다.`);
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
          <h2 className="section-title">Provider vault와 실행 프로필 관리</h2>
        </div>
        <span className="section-meta">{writeReady ? "쓰기 가능" : "읽기 전용"}</span>
      </div>

      <div className="service-grid service-grid-top">
        <section className="control-card">
          <h3>운영자 토큰</h3>
          <p className="control-copy">
            이 토큰은 저장 시점에만 검증합니다. 이 브라우저 안에서만 로컬로 유지해서, provider 저장 뒤 새로고침이 일어나도 다시 입력하지 않도록 했습니다.
          </p>
          <label className="field-label" htmlFor="admin-token">
            관리자 토큰
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
            현재 상태: <strong>{hasAdminToken ? "토큰 로드됨" : "토큰 필요"}</strong>
          </p>
          {!writeReady ? (
            <p className="error-line">
              먼저 Vercel에 `SERVICE_ADMIN_TOKEN`, `SERVICE_MASTER_KEY`, `SUPABASE_SECRET_KEY`를 모두 넣어야 저장 기능이 활성화됩니다.
            </p>
          ) : null}
          {writeReady && !hasAdminToken ? <p className="error-line">저장 전에 관리자 토큰을 한 번 입력해 주세요.</p> : null}
        </section>

        <section className="control-card execution-card">
          <div className="control-head">
            <h3>Execution target</h3>
            <span>{config.executionTarget}</span>
          </div>
          <p className="control-copy">
            v1에서는 지갑 선택 대신 execution target으로 해석합니다. Bybit 키를 저장해도 선물 라이브 주문은 자동으로 켜지지 않습니다.
          </p>
          <div className="badge-row">
            <span className={`status-badge ${statusBadges.safe ? "active safe" : "inactive"}`}>safe</span>
            <span className={`status-badge ${statusBadges.configured ? "active configured" : "inactive"}`}>configured</span>
            <span className={`status-badge ${statusBadges.armed ? "active armed" : "inactive"}`}>armed</span>
          </div>
          <p className="status-line">{liveSummary}</p>
          <div className="status-stack compact">
            <p className="status-line">
              등록된 실행 키 <strong>{providerStatuses?.bybit?.api_key_hint || "미설정"}</strong>
            </p>
            <p className="status-line">
              마지막 업데이트 <strong>{providerStatuses?.bybit?.updated_at || "-"}</strong>
            </p>
            <p className="status-line">
              현재 캔들 충돌 규칙 <strong>{config.intrabarConflictPolicy}</strong>
            </p>
          </div>
        </section>
      </div>

      <section className="control-card provider-section">
        <div className="control-head">
          <h3>Provider 자격증명</h3>
          <span>실행용 / 데이터용 분리</span>
        </div>
        <p className="control-copy">
          거래소 키는 GitHub secrets에 직접 넣지 않고 여기서 저장합니다. 저장된 값은 Supabase 암호화 vault에 들어가고, 배치 실행 직전에만 복호화됩니다.
        </p>
        <div className="provider-grid">
          {PROVIDERS.map((provider) => {
            const status = providerStatuses?.[provider.id];
            const form = providerForms[provider.id] || { apiKey: "", apiSecret: "" };
            return (
              <section key={provider.id} className="control-card provider-card">
                <div className="control-head">
                  <h3>{provider.label}</h3>
                  <span>{status?.configured ? "설정됨" : "비어 있음"}</span>
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
                    마지막 업데이트 <strong>{status?.updated_at || "-"}</strong>
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
                    placeholder={`${provider.label} API Key 입력`}
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
                        placeholder={`${provider.label} API Secret 입력`}
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
                      비우기
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
          <span>{runtimeUpdatedAt ? "Supabase 반영됨" : "기본 프로필"}</span>
        </div>
        <p className="control-copy">
          배치 러너는 사이클 시작 전에 이 프로필을 읽습니다. Top 5 심볼, 최대 포지션 수, 진입 비중, intrabar 충돌 규칙, 데이터 소스 우선순위를 여기서 관리합니다.
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
            자동 실행 사용
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
            Live execution 플래그
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
            Crypto live 활성화
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
            다음 리셋 기준 시드(USDT)
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
            런타임 프로필 저장은 <strong>현재 데모 시드, 누적 PnL, 오픈 포지션을 초기화하지 않습니다.</strong> 이 값은 아래 하드 리셋을 실행할 때 다음 시작 시드로만 사용됩니다.
          </p>

          <label className="field-label" htmlFor="max-positions">
            최대 포지션 수
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
            진입 비중 최소값
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
            진입 비중 최대값
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
            같은 캔들 충돌 규칙
          </label>
          <select
            id="intrabar-conflict-policy"
            className="control-input"
            value={config.intrabarConflictPolicy}
            onChange={(event) => setConfig((prev) => ({ ...prev, intrabarConflictPolicy: event.target.value }))}
          >
            <option value="conservative">conservative / SL 우선</option>
            <option value="neutral">neutral / 시가 기준 가까운 쪽 우선</option>
            <option value="aggressive">aggressive / TP 우선</option>
          </select>
          <p className="status-line full-span">{conflictPolicyHelp(config.intrabarConflictPolicy)}</p>

          <label className="field-label" htmlFor="autotune-hours">
            튜닝 주기(시간)
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
            분석 주기(초)
          </label>
          <input
            id="scan-interval"
            className="control-input"
            type="number"
            min="300"
            step="60"
            value={config.scanIntervalSeconds}
            onChange={(event) => setConfig((prev) => ({ ...prev, scanIntervalSeconds: event.target.value }))}
          />

          <label className="field-label" htmlFor="signal-cooldown">
            신호 쿨다운(분)
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
            Binance 데이터 사용
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
            Bybit 데이터 사용
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
            CoinGecko 데이터 사용
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
            데이터 소스 우선순위
          </label>
          <input
            id="source-order"
            className="control-input"
            type="text"
            value={config.cryptoDataSourceOrder}
            onChange={(event) => setConfig((prev) => ({ ...prev, cryptoDataSourceOrder: event.target.value }))}
            placeholder="binance,bybit,coingecko"
          />

          <label className="field-label full-span" htmlFor="bybit-symbols">
            추적 심볼
          </label>
          <input
            id="bybit-symbols"
            className="control-input full-span"
            type="text"
            value={config.bybitSymbols}
            onChange={(event) => setConfig((prev) => ({ ...prev, bybitSymbols: event.target.value }))}
            placeholder="BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,BNBUSDT"
          />

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
          <h3>데모 하드 리셋</h3>
          <span>포지션 / PnL / 튜닝 상태 초기화</span>
        </div>
        <p className="control-copy">
          현재 데모 포지션, 최근 체결 로그, 일별 PnL, 모델 튜닝 상태를 모두 비우고 다시 시작합니다. provider 자격증명과 runtime profile은 유지됩니다.
        </p>
        <form className="control-form runtime-form" onSubmit={runHardReset}>
          <label className="field-label" htmlFor="reset-seed-usdt">
            새 시작 시드(USDT)
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
            실행 문구는 <strong>{RESET_CONFIRM_TEXT}</strong> 입니다. 이 리셋은 현재 Supabase에 쌓인 선물 데모 상태를 즉시 비우고, 다음 8분 배치가 시드 {resetSeedUsdt || "10000"} USDT에서 다시 시작하게 만듭니다.
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
