export const SERVICE_PROVIDER_ORDER = ["bybit", "binance", "coingecko"];

export const SERVICE_PROVIDER_DEFS = {
  bybit: {
    id: "bybit",
    label: "Bybit",
    role: "execution",
    requiresSecret: true,
    description: "Execution account for future live crypto routing. Saving keys alone never arms live trading.",
  },
  binance: {
    id: "binance",
    label: "Binance",
    role: "market-data",
    requiresSecret: true,
    description: "Primary realtime market-data feed candidate for the crypto planner models.",
  },
  coingecko: {
    id: "coingecko",
    label: "CoinGecko",
    role: "universe-data",
    requiresSecret: false,
    description: "Universe and top-market source in service mode v1.",
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
