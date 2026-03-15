export const SERVICE_PROVIDER_ORDER = ["bybit", "binance", "coingecko"];

export const SERVICE_PROVIDER_DEFS = {
  bybit: {
    id: "bybit",
    label: "Bybit",
    role: "실행 계정",
    requiresSecret: true,
    description: "실제 주문 실행을 위한 거래소 자격증명입니다. 키를 저장해도 바로 실거래가 켜지지는 않습니다.",
  },
  binance: {
    id: "binance",
    label: "Binance",
    role: "실시간 시세 소스",
    requiresSecret: true,
    description: "대형 코인 시세와 호가 흐름을 보강하는 실시간 데이터 소스입니다.",
  },
  coingecko: {
    id: "coingecko",
    label: "CoinGecko",
    role: "유니버스 소스",
    requiresSecret: false,
    description: "시가총액과 코인 메타데이터를 가져오는 보조 데이터 소스입니다.",
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
