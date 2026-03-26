export const SERVICE_PROVIDER_ORDER = ["bybit", "binance", "coingecko"];

export const SERVICE_PROVIDER_DEFS = {
  bybit: {
    id: "bybit",
    label: "Bybit",
    role: "실행 계정",
    requiresSecret: true,
    description: "거래소 실행 연결성과 향후 라이브 라우팅 준비 상태를 위한 자격정보 세트입니다.",
  },
  binance: {
    id: "binance",
    label: "Binance",
    role: "실시간 시세 소스",
    requiresSecret: true,
    description: "실시간 마켓데이터 및 rank_lock 유니버스 업데이트의 기본 소스입니다.",
  },
  coingecko: {
    id: "coingecko",
    label: "CoinGecko",
    role: "매크로 메타데이터 소스",
    requiresSecret: false,
    description: "시가총액 및 메타데이터 보강 신호를 위한 선택형 프로바이더입니다.",
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

