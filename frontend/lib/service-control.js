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
  SCAN_INTERVAL_SECONDS: 600,
  SIGNAL_COOLDOWN_MINUTES: 10,
  MODEL_AUTOTUNE_INTERVAL_HOURS: 168,
  BYBIT_SYMBOLS: DEFAULT_SYMBOLS,
  CRYPTO_DATA_SOURCE_ORDER: DEFAULT_SOURCE_ORDER,
  CRYPTO_USE_BINANCE_DATA: true,
  CRYPTO_USE_BYBIT_DATA: true,
  CRYPTO_USE_COINGECKO_DATA: true,
  MACRO_REALTIME_SOURCES: "binance,bybit",
  MACRO_UNIVERSE_SOURCE: "coingecko",
};

function toBool(value, fallback) {
  if (value === undefined || value === null) return fallback;
  if (typeof value === "boolean") return value;
  return ["1", "true", "yes", "on"].includes(String(value).trim().toLowerCase());
}

function toInt(value, fallback, minValue = 0) {
  const parsed = Number.parseInt(String(value ?? ""), 10);
  if (Number.isNaN(parsed)) return fallback;
  return Math.max(minValue, parsed);
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
    if (!ordered.includes(provider)) {
      ordered.push(provider);
    }
  }
  for (const provider of SERVICE_PROVIDER_ORDER) {
    if (!ordered.includes(provider)) {
      ordered.push(provider);
    }
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
    api_key_hint: String(meta.api_key_hint || "설정 안 됨"),
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
    SCAN_INTERVAL_SECONDS: toInt(raw.SCAN_INTERVAL_SECONDS, DEFAULT_RUNTIME_CONFIG.SCAN_INTERVAL_SECONDS, 300),
    SIGNAL_COOLDOWN_MINUTES: toInt(
      raw.SIGNAL_COOLDOWN_MINUTES,
      DEFAULT_RUNTIME_CONFIG.SIGNAL_COOLDOWN_MINUTES,
      1
    ),
    MODEL_AUTOTUNE_INTERVAL_HOURS: [6, 12, 24, 168].includes(autotuneHours) ? autotuneHours : 168,
    BYBIT_SYMBOLS: (symbols.length ? symbols : DEFAULT_SYMBOLS.split(",")).join(","),
    CRYPTO_DATA_SOURCE_ORDER: sourceOrder.join(","),
    CRYPTO_USE_BINANCE_DATA: flags.binance,
    CRYPTO_USE_BYBIT_DATA: flags.bybit,
    CRYPTO_USE_COINGECKO_DATA: flags.coingecko,
    MACRO_REALTIME_SOURCES: deriveRealtimeSources(sourceOrder, flags),
    MACRO_UNIVERSE_SOURCE: "coingecko",
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
