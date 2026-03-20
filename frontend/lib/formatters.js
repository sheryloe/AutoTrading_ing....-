const moneyFormatter = new Intl.NumberFormat("ko-KR", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 2,
});

const numberFormatterCache = new Map();
const priceFormatterCache = new Map();
const tsFormatter = new Intl.DateTimeFormat("ko-KR", {
  dateStyle: "medium",
  timeStyle: "short",
  timeZone: "Asia/Seoul",
});

function getNumberFormatter(digits) {
  const key = Number(digits || 0);
  if (!numberFormatterCache.has(key)) {
    numberFormatterCache.set(
      key,
      new Intl.NumberFormat("ko-KR", {
        maximumFractionDigits: key,
        minimumFractionDigits: key,
      })
    );
  }
  return numberFormatterCache.get(key);
}

function getPriceFormatter(digits) {
  const key = Number(digits || 0);
  if (!priceFormatterCache.has(key)) {
    priceFormatterCache.set(
      key,
      new Intl.NumberFormat("en-US", {
        minimumFractionDigits: key,
        maximumFractionDigits: key,
      })
    );
  }
  return priceFormatterCache.get(key);
}

export function formatMoney(value) {
  const num = Number(value || 0);
  return moneyFormatter.format(num);
}

export function formatPrice(value, digits = 4) {
  const num = Number(value || 0);
  if (!Number.isFinite(num) || num === 0) return "-";
  return `$${getPriceFormatter(digits).format(num)}`;
}

export function formatPct(value, digits = 2) {
  return `${(Number(value || 0) * 100).toFixed(digits)}%`;
}

export function formatPercent(value, digits = 2) {
  return `${Number(value || 0).toFixed(digits)}%`;
}

export function formatNumber(value, digits = 0) {
  return getNumberFormatter(digits).format(Number(value || 0));
}

export function formatTs(value) {
  if (!value) return "-";
  const dt = new Date(value);
  if (Number.isNaN(dt.getTime())) return String(value);
  return tsFormatter.format(dt);
}
