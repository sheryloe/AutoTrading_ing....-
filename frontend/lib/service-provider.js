export const SERVICE_PROVIDER_ORDER = ["bybit", "binance", "coingecko"];

export const SERVICE_PROVIDER_DEFS = {
  bybit: {
    id: "bybit",
    label: "Bybit",
    role: "Execution account",
    requiresSecret: true,
    description: "Credential set used for exchange execution connectivity and future live routing readiness.",
  },
  binance: {
    id: "binance",
    label: "Binance",
    role: "Realtime quote source",
    requiresSecret: true,
    description: "Primary source for realtime market data and rank-lock universe updates.",
  },
  coingecko: {
    id: "coingecko",
    label: "CoinGecko",
    role: "Macro metadata source",
    requiresSecret: false,
    description: "Optional provider for market-cap and metadata enrichment signals.",
  },
};

export function normalizeProviderId(value) {
  const provider = String(value || "").trim().toLowerCase();
  return Object.prototype.hasOwnProperty.call(SERVICE_PROVIDER_DEFS, provider) ? provider : "";
}

export function getServiceProviderDef(value) {
  const provider = normalizeProviderId(value);
  return provider ? SERVICE_PROVIDER_DEFS[provider] : null;
}

export function maskKeyHint(value) {
  const compact = String(value || "").trim();
  if (!compact) return "";
  if (compact.length <= 8) return compact;
  return `${compact.slice(0, 4)}...${compact.slice(-4)}`;
}

