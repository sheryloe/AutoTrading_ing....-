import { getSupabaseAdmin } from "./supabase-admin";

export const SERVICE_RUNTIME_BLOB_KEY = "service_runtime_config";

const DEFAULT_RUNTIME_CONFIG = {
  TRADE_MODE: "paper",
  ENABLE_AUTOTRADE: true,
  ENABLE_LIVE_EXECUTION: false,
  LIVE_ENABLE_CRYPTO: true,
  ENABLE_MEME_MARKET: false,
  LIVE_ENABLE_MEME: false,
  OPENAI_REVIEW_ENABLED: false,
  GOOGLE_TREND_ENABLED: false,
  SCAN_INTERVAL_SECONDS: 600,
  SIGNAL_COOLDOWN_MINUTES: 10,
  MODEL_AUTOTUNE_INTERVAL_HOURS: 168,
  BYBIT_SYMBOLS: "BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,BNBUSDT",
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

export function normalizeRuntimeConfig(raw = {}) {
  const symbols = String(raw.BYBIT_SYMBOLS || DEFAULT_RUNTIME_CONFIG.BYBIT_SYMBOLS)
    .split(",")
    .map((item) => item.trim().toUpperCase())
    .filter(Boolean);

  const mode = String(raw.TRADE_MODE || DEFAULT_RUNTIME_CONFIG.TRADE_MODE).toLowerCase();
  const autotuneHours = toInt(
    raw.MODEL_AUTOTUNE_INTERVAL_HOURS,
    DEFAULT_RUNTIME_CONFIG.MODEL_AUTOTUNE_INTERVAL_HOURS,
    6,
  );

  return {
    TRADE_MODE: mode === "live" ? "live" : "paper",
    ENABLE_AUTOTRADE: toBool(raw.ENABLE_AUTOTRADE, DEFAULT_RUNTIME_CONFIG.ENABLE_AUTOTRADE),
    ENABLE_LIVE_EXECUTION: toBool(raw.ENABLE_LIVE_EXECUTION, DEFAULT_RUNTIME_CONFIG.ENABLE_LIVE_EXECUTION),
    LIVE_ENABLE_CRYPTO: toBool(raw.LIVE_ENABLE_CRYPTO, DEFAULT_RUNTIME_CONFIG.LIVE_ENABLE_CRYPTO),
    ENABLE_MEME_MARKET: false,
    LIVE_ENABLE_MEME: false,
    OPENAI_REVIEW_ENABLED: false,
    GOOGLE_TREND_ENABLED: false,
    SCAN_INTERVAL_SECONDS: toInt(
      raw.SCAN_INTERVAL_SECONDS,
      DEFAULT_RUNTIME_CONFIG.SCAN_INTERVAL_SECONDS,
      300,
    ),
    SIGNAL_COOLDOWN_MINUTES: toInt(
      raw.SIGNAL_COOLDOWN_MINUTES,
      DEFAULT_RUNTIME_CONFIG.SIGNAL_COOLDOWN_MINUTES,
      1,
    ),
    MODEL_AUTOTUNE_INTERVAL_HOURS: [6, 12, 24, 168].includes(autotuneHours) ? autotuneHours : 168,
    BYBIT_SYMBOLS: (symbols.length ? symbols : DEFAULT_RUNTIME_CONFIG.BYBIT_SYMBOLS.split(",")).join(","),
  };
}

export async function loadServiceControlData() {
  const supabase = getSupabaseAdmin();
  const writeReady = Boolean(
    process.env.SERVICE_ADMIN_TOKEN &&
    process.env.SERVICE_MASTER_KEY &&
    (process.env.SUPABASE_SECRET_KEY || process.env.SUPABASE_SERVICE_ROLE_KEY),
  );

  if (!supabase) {
    return {
      writeReady: false,
      runtimeConfig: { ...DEFAULT_RUNTIME_CONFIG },
      runtimeUpdatedAt: null,
      bybitStatus: null,
    };
  }

  const [runtimeRes, secretsRes] = await Promise.all([
    supabase
      .from("engine_state_blobs")
      .select("payload_json,updated_at")
      .eq("blob_key", SERVICE_RUNTIME_BLOB_KEY)
      .maybeSingle(),
    supabase
      .from("service_secrets")
      .select("provider,updated_at,meta_json")
      .order("provider", { ascending: true }),
  ]);

  const runtimePayload = runtimeRes.data?.payload_json;
  const bybitStatus = (secretsRes.data || []).find((row) => row.provider === "bybit") || null;

  return {
    writeReady,
    runtimeConfig: normalizeRuntimeConfig(runtimePayload && typeof runtimePayload === "object" ? runtimePayload : {}),
    runtimeUpdatedAt: runtimeRes.data?.updated_at || null,
    bybitStatus,
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
    { onConflict: "blob_key" },
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
