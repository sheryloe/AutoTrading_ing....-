import { getSupabaseAdmin } from "./supabase-admin";
import { getServiceProviderDef, SERVICE_PROVIDER_ORDER } from "./service-provider";

export const SERVICE_RUNTIME_BLOB_KEY = "service_runtime_config";

const DEFAULT_SOURCE_ORDER = "binance,bybit,coingecko";
const DEFAULT_SYMBOLS = "BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,BNBUSDT";

const DEFAULT_RUNTIME_CONFIG = {
  EXECUTION_TARGET: "paper",
  TRADE_MODE: "paper",
  ENABLE_AUTOTRADE: true,
  ENABLE_LIVE_EXECUTION: false,
  LIVE_ENABLE_CRYPTO: false,
  LIVE_EXECUTION_ARMED: false,
  ENABLE_MEME_MARKET: false,
  LIVE_ENABLE_MEME: false,
  OPENAI_REVIEW_ENABLED: false,
  GOOGLE_TREND_ENABLED: false,
  DEMO_SEED_USDT: 10000,
  SCAN_INTERVAL_SECONDS: 480,
  SIGNAL_COOLDOWN_MINUTES: 10,
  MODEL_AUTOTUNE_INTERVAL_HOURS: 168,
  BYBIT_SYMBOLS: DEFAULT_SYMBOLS,
  CRYPTO_DYNAMIC_UNIVERSE_ENABLED: false,
  CRYPTO_PRIORITY_SYMBOLS: "",
  CRYPTO_TUNE_OVERRIDES: {},
  BYBIT_MAX_POSITIONS: 3,
  BYBIT_ORDER_PCT: 0.2,
  BYBIT_ORDER_PCT_MIN: 0.15,
  BYBIT_ORDER_PCT_MAX: 0.3,
  INTRABAR_CONFLICT_POLICY: "conservative",
  CRYPTO_DATA_SOURCE_ORDER: DEFAULT_SOURCE_ORDER,
  CRYPTO_USE_BINANCE_DATA: true,
  CRYPTO_USE_BYBIT_DATA: true,
  CRYPTO_USE_COINGECKO_DATA: true,
  MACRO_REALTIME_SOURCES: "binance,bybit",
  MACRO_UNIVERSE_SOURCE: "coingecko",
  DEMO_ENABLE_MACRO: true,
  MACRO_TREND_POOL_SIZE: 5,
  MACRO_TREND_RESELECT_SECONDS: 14400,
};

function normalizeSymbolList(raw) {
  return String(raw || "")
    .split(",")
    .map((item) => item.trim().toUpperCase())
    .filter(Boolean);
}

function buildEmptyEngineState(seedUsdt) {
  const seed = Number(seedUsdt || DEFAULT_RUNTIME_CONFIG.DEMO_SEED_USDT);
  return {
    cash_usd: seed,
    positions: {},
    trades: [],
    last_signal_ts: {},
    latest_signals: [],
    alerts: [],
    trend_events: [],
    wallet_assets: [],
    bybit_assets: [],
    bybit_positions: [],
    bybit_error: "",
    memecoin_error: "",
    last_cycle_ts: 0,
    last_wallet_sync_ts: 0,
    last_bybit_sync_ts: 0,
    telegram_offset: 0,
    demo_seed_usdt: seed,
    live_seed_usd: 0,
    live_seed_set_ts: 0,
    live_perf_anchor_usd: 0,
    live_perf_anchor_ts: 0,
    live_net_flow_usd: 0,
    model_runs: {},
    daily_pnl: [],
  };
}

function collectMutationErrors(results = []) {
  return results
    .map((result) => result?.error?.message)
    .filter(Boolean)
    .map((message) => String(message));
}

function toBool(value, fallback) {
  if (value === undefined || value === null) return fallback;
  if (typeof value === "boolean") return value;
  return ["1", "true", "yes", "on"].includes(String(value).trim().toLowerCase());
}

function toInt(value, fallback, minValue = 0, maxValue = Number.POSITIVE_INFINITY) {
  const parsed = Number.parseInt(String(value ?? ""), 10);
  if (Number.isNaN(parsed)) return fallback;
  return Math.max(minValue, Math.min(maxValue, parsed));
}

function toFloat(value, fallback, minValue = 0, maxValue = Number.POSITIVE_INFINITY) {
  const parsed = Number.parseFloat(String(value ?? ""));
  if (Number.isNaN(parsed)) return fallback;
  return Math.max(minValue, Math.min(maxValue, parsed));
}

function toObject(value, fallback = {}) {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return { ...value };
  }
  const text = String(value || "").trim();
  if (!text) return { ...fallback };
  try {
    const parsed = JSON.parse(text);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return { ...parsed };
    }
  } catch {}
  return { ...fallback };
}

function normalizeExecutionTarget(raw) {
  const value = String(raw || "").trim().toLowerCase();
  if (value === "bybit-live" || value === "live") return "bybit-live";
  return "paper";
}

function normalizeSourceOrder(raw) {
  const requested = String(raw || DEFAULT_SOURCE_ORDER)
    .split(",")
    .map((item) => item.trim().toLowerCase())
    .filter(Boolean);
  const ordered = [];
  for (const provider of requested) {
    if (!SERVICE_PROVIDER_ORDER.includes(provider)) continue;
    if (!ordered.includes(provider)) ordered.push(provider);
  }
  for (const provider of SERVICE_PROVIDER_ORDER) {
    if (!ordered.includes(provider)) ordered.push(provider);
  }
  return ordered;
}

function deriveRealtimeSources(order, flags) {
  const enabled = order.filter((provider) => {
    if (provider === "binance") return flags.binance;
    if (provider === "bybit") return flags.bybit;
    return false;
  });
  return (enabled.length ? enabled : ["binance", "bybit"]).join(",");
}

function normalizeProviderStatus(provider, row) {
  const definition = getServiceProviderDef(provider);
  const meta = row?.meta_json && typeof row.meta_json === "object" ? row.meta_json : {};
  return {
    provider,
    label: definition?.label || provider,
    role: definition?.role || "unknown",
    description: definition?.description || "",
    configured: Boolean(row),
    updated_at: row?.updated_at || null,
    meta_json: meta,
    api_key_hint: String(meta.api_key_hint || "미설정"),
  };
}

function computeLiveStatus(runtimeConfig, providerStatuses) {
  const bybitConfigured = Boolean(providerStatuses?.bybit?.configured);
  const executionTarget = String(runtimeConfig.EXECUTION_TARGET || "paper");
  const liveFlagsReady = Boolean(runtimeConfig.ENABLE_LIVE_EXECUTION && runtimeConfig.LIVE_ENABLE_CRYPTO);
  const armed = Boolean(runtimeConfig.LIVE_EXECUTION_ARMED && bybitConfigured);
  const futureLiveEligible = Boolean(
    executionTarget === "bybit-live" &&
      runtimeConfig.TRADE_MODE === "live" &&
      liveFlagsReady &&
      armed &&
      bybitConfigured
  );

  return {
    executionTarget,
    bybitConfigured,
    safe: executionTarget !== "bybit-live",
    configured: bybitConfigured,
    armed,
    liveFlagsReady,
    futureLiveEligible,
  };
}

function buildRuntimeDiagnostics(runtimeConfig, providerStatuses) {
  const configuredSymbols = normalizeSymbolList(runtimeConfig.BYBIT_SYMBOLS);
  const dynamicUniverseEnabled = Boolean(runtimeConfig.CRYPTO_DYNAMIC_UNIVERSE_ENABLED);
  const liveStatus = computeLiveStatus(runtimeConfig, providerStatuses);

  return {
    configSourceLabel: "Supabase runtime profile",
    configSourceValue: `engine_state_blobs.${SERVICE_RUNTIME_BLOB_KEY}`,
    configuredSymbols,
    configuredSymbolCount: configuredSymbols.length,
    dynamicUniverseEnabled,
    symbolModeLabel: dynamicUniverseEnabled
      ? "Dynamic rotation mode: BYBIT_SYMBOLS is only a reference or fallback list."
      : "Fixed universe mode: BYBIT_SYMBOLS is the enforced crypto watchlist.",
    liveOrderRoutingLabel: "Demo-only crypto execution path",
    liveOrderSummary: liveStatus.futureLiveEligible
      ? "Even with bybit-live, live flags, and arm enabled, this build still keeps crypto entries on the demo execution path."
      : "The current build does not send real Bybit crypto orders yet; it only prepares future live routing.",
    symbolSummary: dynamicUniverseEnabled
      ? "A symbol can still end up as symbol_not_allowed when it falls outside the rotating universe."
      : "A symbol must be present in BYBIT_SYMBOLS to be eligible when dynamic rotation is off.",
  };
}

export function normalizeRuntimeConfig(raw = {}) {
  const executionTarget = normalizeExecutionTarget(raw.EXECUTION_TARGET || raw.TRADE_MODE);
  const symbols = String(raw.BYBIT_SYMBOLS || DEFAULT_SYMBOLS)
    .split(",")
    .map((item) => item.trim().toUpperCase())
    .filter(Boolean);
  const autotuneHours = toInt(
    raw.MODEL_AUTOTUNE_INTERVAL_HOURS,
    DEFAULT_RUNTIME_CONFIG.MODEL_AUTOTUNE_INTERVAL_HOURS,
    6
  );
  const sourceOrder = normalizeSourceOrder(raw.CRYPTO_DATA_SOURCE_ORDER || raw.MARKET_DATA_SOURCE_ORDER);
  const flags = {
    binance: toBool(raw.CRYPTO_USE_BINANCE_DATA, DEFAULT_RUNTIME_CONFIG.CRYPTO_USE_BINANCE_DATA),
    bybit: toBool(raw.CRYPTO_USE_BYBIT_DATA, DEFAULT_RUNTIME_CONFIG.CRYPTO_USE_BYBIT_DATA),
    coingecko: toBool(raw.CRYPTO_USE_COINGECKO_DATA, DEFAULT_RUNTIME_CONFIG.CRYPTO_USE_COINGECKO_DATA),
  };
  const demoSeedUsdt = toFloat(raw.DEMO_SEED_USDT, DEFAULT_RUNTIME_CONFIG.DEMO_SEED_USDT, 50, 1_000_000);
  const maxPositions = toInt(raw.BYBIT_MAX_POSITIONS, DEFAULT_RUNTIME_CONFIG.BYBIT_MAX_POSITIONS, 1, 10);
  const orderPctMin = toFloat(raw.BYBIT_ORDER_PCT_MIN, DEFAULT_RUNTIME_CONFIG.BYBIT_ORDER_PCT_MIN, 0.15, 0.3);
  const orderPctMax = toFloat(raw.BYBIT_ORDER_PCT_MAX, DEFAULT_RUNTIME_CONFIG.BYBIT_ORDER_PCT_MAX, orderPctMin, 0.3);
  const orderPctMid = Number((((orderPctMin + orderPctMax) * 0.5).toFixed(4)));
  const tuneOverrides = toObject(raw.CRYPTO_TUNE_OVERRIDES, DEFAULT_RUNTIME_CONFIG.CRYPTO_TUNE_OVERRIDES);
  const prioritySymbols = String(raw.CRYPTO_PRIORITY_SYMBOLS || "")
    .split(",")
    .map((item) => item.trim().toUpperCase())
    .filter(Boolean);

  return {
    EXECUTION_TARGET: executionTarget,
    TRADE_MODE: executionTarget === "bybit-live" ? "live" : "paper",
    ENABLE_AUTOTRADE: toBool(raw.ENABLE_AUTOTRADE, DEFAULT_RUNTIME_CONFIG.ENABLE_AUTOTRADE),
    ENABLE_LIVE_EXECUTION: toBool(raw.ENABLE_LIVE_EXECUTION, DEFAULT_RUNTIME_CONFIG.ENABLE_LIVE_EXECUTION),
    LIVE_ENABLE_CRYPTO: toBool(raw.LIVE_ENABLE_CRYPTO, DEFAULT_RUNTIME_CONFIG.LIVE_ENABLE_CRYPTO),
    LIVE_EXECUTION_ARMED: toBool(raw.LIVE_EXECUTION_ARMED, DEFAULT_RUNTIME_CONFIG.LIVE_EXECUTION_ARMED),
    ENABLE_MEME_MARKET: false,
    LIVE_ENABLE_MEME: false,
    OPENAI_REVIEW_ENABLED: false,
    GOOGLE_TREND_ENABLED: false,
    DEMO_ENABLE_MACRO: true,
    DEMO_SEED_USDT: demoSeedUsdt,
    SCAN_INTERVAL_SECONDS: toInt(raw.SCAN_INTERVAL_SECONDS, DEFAULT_RUNTIME_CONFIG.SCAN_INTERVAL_SECONDS, 300),
    SIGNAL_COOLDOWN_MINUTES: toInt(
      raw.SIGNAL_COOLDOWN_MINUTES,
      DEFAULT_RUNTIME_CONFIG.SIGNAL_COOLDOWN_MINUTES,
      1,
      240
    ),
    MODEL_AUTOTUNE_INTERVAL_HOURS: [6, 12, 24, 168].includes(autotuneHours) ? autotuneHours : 168,
    BYBIT_SYMBOLS: (symbols.length ? symbols : DEFAULT_SYMBOLS.split(",")).join(","),
    CRYPTO_DYNAMIC_UNIVERSE_ENABLED: toBool(
      raw.CRYPTO_DYNAMIC_UNIVERSE_ENABLED,
      DEFAULT_RUNTIME_CONFIG.CRYPTO_DYNAMIC_UNIVERSE_ENABLED
    ),
    CRYPTO_PRIORITY_SYMBOLS: prioritySymbols.join(","),
    CRYPTO_TUNE_OVERRIDES: tuneOverrides,
    BYBIT_MAX_POSITIONS: maxPositions,
    BYBIT_ORDER_PCT: orderPctMid,
    BYBIT_ORDER_PCT_MIN: orderPctMin,
    BYBIT_ORDER_PCT_MAX: orderPctMax,
    INTRABAR_CONFLICT_POLICY: ["conservative", "neutral", "aggressive"].includes(
      String(raw.INTRABAR_CONFLICT_POLICY || "").toLowerCase()
    )
      ? String(raw.INTRABAR_CONFLICT_POLICY).toLowerCase()
      : DEFAULT_RUNTIME_CONFIG.INTRABAR_CONFLICT_POLICY,
    CRYPTO_DATA_SOURCE_ORDER: sourceOrder.join(","),
    CRYPTO_USE_BINANCE_DATA: flags.binance,
    CRYPTO_USE_BYBIT_DATA: flags.bybit,
    CRYPTO_USE_COINGECKO_DATA: flags.coingecko,
    MACRO_REALTIME_SOURCES: deriveRealtimeSources(sourceOrder, flags),
    MACRO_UNIVERSE_SOURCE: "coingecko",
    MACRO_TREND_POOL_SIZE: toInt(
      raw.MACRO_TREND_POOL_SIZE,
      DEFAULT_RUNTIME_CONFIG.MACRO_TREND_POOL_SIZE,
      5,
      200
    ),
    MACRO_TREND_RESELECT_SECONDS: toInt(
      raw.MACRO_TREND_RESELECT_SECONDS,
      DEFAULT_RUNTIME_CONFIG.MACRO_TREND_RESELECT_SECONDS,
      900,
      86400
    ),
  };
}

export async function loadServiceControlData() {
  const supabase = getSupabaseAdmin();
  const writeReady = Boolean(
    process.env.SERVICE_ADMIN_TOKEN &&
      process.env.SERVICE_MASTER_KEY &&
      (process.env.SUPABASE_SECRET_KEY || process.env.SUPABASE_SERVICE_ROLE_KEY)
  );

  if (!supabase) {
    const providerStatuses = Object.fromEntries(
      SERVICE_PROVIDER_ORDER.map((provider) => [provider, normalizeProviderStatus(provider, null)])
    );
    return {
      writeReady: false,
      runtimeConfig: { ...DEFAULT_RUNTIME_CONFIG },
      runtimeUpdatedAt: null,
      providerStatuses,
      liveStatus: computeLiveStatus(DEFAULT_RUNTIME_CONFIG, providerStatuses),
      diagnostics: buildRuntimeDiagnostics(DEFAULT_RUNTIME_CONFIG, providerStatuses),
      errors: ["Supabase 서버 연결이 준비되지 않았습니다."],
    };
  }

  const [runtimeRes, secretsRes] = await Promise.all([
    supabase
      .from("engine_state_blobs")
      .select("payload_json,updated_at")
      .eq("blob_key", SERVICE_RUNTIME_BLOB_KEY)
      .maybeSingle(),
    supabase.from("service_secrets").select("provider,updated_at,meta_json").order("provider", { ascending: true }),
  ]);

  const runtimePayload = runtimeRes.data?.payload_json;
  const rows = Array.isArray(secretsRes.data) ? secretsRes.data : [];
  const providerStatuses = Object.fromEntries(
    SERVICE_PROVIDER_ORDER.map((provider) => {
      const row = rows.find((item) => item.provider === provider) || null;
      return [provider, normalizeProviderStatus(provider, row)];
    })
  );
  const runtimeConfig = normalizeRuntimeConfig(
    runtimePayload && typeof runtimePayload === "object" ? runtimePayload : {}
  );

  return {
    writeReady,
    runtimeConfig,
    runtimeUpdatedAt: runtimeRes.data?.updated_at || null,
    providerStatuses,
    liveStatus: computeLiveStatus(runtimeConfig, providerStatuses),
    diagnostics: buildRuntimeDiagnostics(runtimeConfig, providerStatuses),
    errors: [runtimeRes.error?.message, secretsRes.error?.message].filter(Boolean),
  };
}

export async function upsertRuntimeConfig(config) {
  const supabase = getSupabaseAdmin();
  if (!supabase) {
    throw new Error("supabase_admin_not_ready");
  }

  const payload = normalizeRuntimeConfig(config);
  const { error } = await supabase.from("engine_state_blobs").upsert(
    [
      {
        blob_key: SERVICE_RUNTIME_BLOB_KEY,
        payload_json: payload,
      },
    ],
    { onConflict: "blob_key" }
  );

  if (error) {
    throw new Error(error.message || "runtime_config_upsert_failed");
  }

  return payload;
}

export async function upsertProviderSecret(provider, payload, meta = {}) {
  const supabase = getSupabaseAdmin();
  const masterKey = String(process.env.SERVICE_MASTER_KEY || "").trim();

  if (!supabase) {
    throw new Error("supabase_admin_not_ready");
  }
  if (!masterKey) {
    throw new Error("service_master_key_missing");
  }

  const { error } = await supabase.rpc("upsert_service_secret", {
    p_provider: provider,
    p_payload: payload,
    p_passphrase: masterKey,
    p_meta: meta,
  });

  if (error) {
    throw new Error(error.message || "service_secret_upsert_failed");
  }
}

export async function deleteProviderSecret(provider) {
  const supabase = getSupabaseAdmin();
  if (!supabase) {
    throw new Error("supabase_admin_not_ready");
  }

  const { error } = await supabase.rpc("delete_service_secret", {
    p_provider: provider,
  });

  if (error) {
    throw new Error(error.message || "service_secret_delete_failed");
  }
}

export async function hardResetServiceDemo({ seedUsdt = DEFAULT_RUNTIME_CONFIG.DEMO_SEED_USDT } = {}) {
  const supabase = getSupabaseAdmin();
  if (!supabase) {
    throw new Error("supabase_admin_not_ready");
  }

  const seed = toFloat(seedUsdt, DEFAULT_RUNTIME_CONFIG.DEMO_SEED_USDT, 50, 1_000_000);
  const resetAt = new Date().toISOString();

  const runtimeRes = await supabase
    .from("engine_state_blobs")
    .select("payload_json")
    .eq("blob_key", SERVICE_RUNTIME_BLOB_KEY)
    .maybeSingle();

  if (runtimeRes.error) {
    throw new Error(runtimeRes.error.message || "runtime_config_load_failed");
  }

  const runtimeConfig = normalizeRuntimeConfig(
    runtimeRes.data?.payload_json && typeof runtimeRes.data.payload_json === "object" ? runtimeRes.data.payload_json : {}
  );
  runtimeConfig.DEMO_SEED_USDT = seed;

  const blobRes = await supabase.from("engine_state_blobs").upsert(
    [
      {
        blob_key: SERVICE_RUNTIME_BLOB_KEY,
        payload_json: runtimeConfig,
      },
      {
        blob_key: "engine_state",
        payload_json: buildEmptyEngineState(seed),
      },
      {
        blob_key: "online_model",
        payload_json: {},
      },
      {
        blob_key: "recent_crypto_trades",
        payload_json: {
          rows: [],
          reset_at: resetAt,
          seed_usdt: seed,
        },
      },
    ],
    { onConflict: "blob_key" }
  );

  if (blobRes.error) {
    throw new Error(blobRes.error.message || "engine_state_reset_failed");
  }

  const minDate = "1970-01-01T00:00:00+00:00";
  const deleteResults = await Promise.all([
    supabase.from("positions").delete().gte("updated_at", minDate),
    supabase.from("model_setups").delete().gte("updated_at", minDate),
    supabase.from("daily_model_pnl").delete().gte("updated_at", minDate),
    supabase.from("model_runtime_tunes").delete().gte("updated_at", minDate),
  ]);

  const deleteErrors = collectMutationErrors(deleteResults);
  if (deleteErrors.length) {
    throw new Error(deleteErrors[0] || "service_demo_reset_failed");
  }

  return {
    ok: true,
    seedUsdt: seed,
    resetAt,
    runtimeConfig,
    clearedTables: ["positions", "model_setups", "daily_model_pnl", "model_runtime_tunes"],
  };
}
