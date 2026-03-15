"use client";

import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

const PROVIDERS = [
  {
    id: "bybit",
    label: "Bybit",
    role: "실행 계정",
    description:
      "실제 거래소 연결용 자격증명입니다. 키를 저장하는 것만으로는 live가 켜지지 않고 execution target과 arm 단계가 따로 필요합니다.",
    requiresSecret: true,
    keyLabel: "Bybit API Key",
    secretLabel: "Bybit API Secret",
  },
  {
    id: "binance",
    label: "Binance",
    role: "실시간 시세 소스",
    description: "상위 코인 가격 흐름과 단기 캔들 판단에 쓰는 실시간 데이터 소스입니다.",
    requiresSecret: true,
    keyLabel: "Binance API Key",
    secretLabel: "Binance API Secret",
  },
  {
    id: "coingecko",
    label: "CoinGecko",
    role: "유니버스/메타 소스",
    description: "시총, 코인 메타 정보, 보조 참조 데이터를 보강하는 소스입니다.",
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
    bybitOrderPctMin: String(initialConfig?.BYBIT_ORDER_PCT_MIN || 0.15),
    bybitOrderPctMax: String(initialConfig?.BYBIT_ORDER_PCT_MAX || 0.4),
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

  const liveSummary = useMemo(() => {
    const bybitConfigured = Boolean(providerStatuses?.bybit?.configured);
    const target = config.executionTarget;
    const liveFlagsReady = config.enableLiveExecution === "true" && config.liveEnableCrypto === "true";
    const armed = config.liveExecutionArmed === "true" && bybitConfigured;

    if (target === "paper") {
      return "현재는 paper 실행 모드입니다. Bybit 키를 저장해도 데모 포지션/PnL만 관리하고 실주문은 열리지 않습니다.";
    }
    if (!bybitConfigured) {
      return "bybit-live가 선택되어 있지만 Bybit 실행 키가 아직 비어 있습니다.";
    }
    if (!liveFlagsReady) {
      return "bybit-live가 선택되어 있지만 live execution 관련 플래그가 아직 꺼져 있습니다.";
    }
    if (!armed) {
      return "live 실행 조건은 거의 준비됐지만 arm 단계가 꺼져 있어서 future live 준비 상태로만 유지됩니다.";
    }
    return "future live execution 준비 상태입니다. 현재 빌드는 crypto 실주문을 바로 켜지 않고, 먼저 설정/가드 흐름을 고정합니다.";
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
      futureLiveEligible,
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
            BYBIT_ORDER_PCT_MIN: Number(config.bybitOrderPctMin || 0.15),
            BYBIT_ORDER_PCT_MAX: Number(config.bybitOrderPctMax || 0.4),
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
      setRuntimeError(error instanceof Error ? error.message : "runtime_save_failed");
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
        [provider]: error instanceof Error ? error.message : "provider_save_failed",
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
        [provider]: `${providerLabel} 자격증명을 vault에서 제거했습니다.`,
      }));
      router.refresh();
    } catch (error) {
      setProviderErrors((prev) => ({
        ...prev,
        [provider]: error instanceof Error ? error.message : "provider_delete_failed",
      }));
    } finally {
      setProviderSaving((prev) => ({ ...prev, [provider]: false }));
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
            이 토큰은 설정 저장 시점에만 검증합니다. 브라우저에 별도로 저장하지 않고, 서비스 운영자만 설정을 바꿀 수 있게 막는 용도입니다.
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
          {!writeReady ? (
            <p className="error-line">
              먼저 Vercel에 SERVICE_ADMIN_TOKEN, SERVICE_MASTER_KEY, SUPABASE_SECRET_KEY를 모두 넣어야 저장이 활성화됩니다.
            </p>
          ) : null}
        </section>

        <section className="control-card execution-card">
          <div className="control-head">
            <h3>Execution target</h3>
            <span>{config.executionTarget}</span>
          </div>
          <p className="control-copy">
            v1에서는 지갑 선택을 execution target으로 해석합니다. Bybit 키를 저장해도 live가 바로 켜지지 않도록 arm 단계를 별도로 분리해뒀습니다.
          </p>
          <div className="badge-row">
            <span className={`status-badge ${statusBadges.safe ? "active safe" : "inactive"}`}>safe</span>
            <span className={`status-badge ${statusBadges.configured ? "active configured" : "inactive"}`}>configured</span>
            <span className={`status-badge ${statusBadges.armed ? "active armed" : "inactive"}`}>armed</span>
          </div>
          <p className="status-line">{liveSummary}</p>
          <div className="status-stack compact">
            <p className="status-line">
              등록된 실행 키: <strong>{providerStatuses?.bybit?.api_key_hint || "미설정"}</strong>
            </p>
            <p className="status-line">
              마지막 업데이트: <strong>{providerStatuses?.bybit?.updated_at || "-"}</strong>
            </p>
            <p className="status-line">
              future live 준비: <strong>{statusBadges.futureLiveEligible ? "yes" : "no"}</strong>
            </p>
          </div>
        </section>
      </div>

      <section className="control-card provider-section">
        <div className="control-head">
          <h3>Provider 자격증명</h3>
          <span>거래/데이터 키 분리</span>
        </div>
        <p className="control-copy">
          거래소와 데이터 API 키는 GitHub Secrets가 아니라 여기서 저장합니다. 저장된 키는 Supabase 암호화 vault에 들어가고, 배치 러너는 실행 직전에 읽어서 씁니다.
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
                    역할: <strong>{provider.role}</strong>
                  </p>
                  <p className="status-line">
                    키 힌트: <strong>{status?.api_key_hint || "미설정"}</strong>
                  </p>
                  <p className="status-line">
                    업데이트 시각: <strong>{status?.updated_at || "-"}</strong>
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
                    <button className="action-button" type="submit" disabled={providerSaving[provider.id] || !writeReady}>
                      {providerSaving[provider.id] ? "저장 중..." : `${provider.label} 저장`}
                    </button>
                    <button
                      className="action-button ghost"
                      type="button"
                      disabled={providerSaving[provider.id] || !writeReady}
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
          <span>{runtimeUpdatedAt ? "Supabase에 반영됨" : "기본 프로필"}</span>
        </div>
        <p className="control-copy">
          배치 러너는 사이클 시작 전에 이 프로필을 읽습니다. Top 5 기준 데모 시드, 포지션 수, 점수별 진입 비중, 분석 주기를 여기서 관리합니다.
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
            자동 매매 사용
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
            모델별 데모 시드(USDT)
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
            min="0.05"
            max="0.95"
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
            min="0.05"
            max="0.95"
            step="0.01"
            value={config.bybitOrderPctMax}
            onChange={(event) => setConfig((prev) => ({ ...prev, bybitOrderPctMax: event.target.value }))}
          />

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
            <button className="action-button" type="submit" disabled={runtimeSaving || !writeReady}>
              {runtimeSaving ? "저장 중..." : "런타임 프로필 저장"}
            </button>
          </div>
        </form>
        {runtimeMessage ? <p className="success-line">{runtimeMessage}</p> : null}
        {runtimeError ? <p className="error-line">{runtimeError}</p> : null}
      </section>
    </section>
  );
}
