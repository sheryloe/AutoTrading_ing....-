import { MODEL_ORDER } from "./model-meta";

function modelIdOf(row) {
  return String(row?.model_id || "").toUpperCase();
}

export function groupRowsByModel(rows = []) {
  const grouped = Object.fromEntries(MODEL_ORDER.map((modelId) => [modelId, []]));
  for (const row of rows) {
    const modelId = modelIdOf(row);
    if (!grouped[modelId]) {
      grouped[modelId] = [];
    }
    grouped[modelId].push(row);
  }
  return grouped;
}

export function pickDefaultModel(openPositions = [], setupRows = [], signalAuditRows = [], recentTradeRows = []) {
  const ids = new Set([
    ...openPositions.map(modelIdOf),
    ...setupRows.map(modelIdOf),
    ...signalAuditRows.map(modelIdOf),
    ...recentTradeRows.map(modelIdOf),
  ]);
  return MODEL_ORDER.find((id) => ids.has(id)) || "A";
}

export function summarizeAuditRows(rows = []) {
  const counts = new Map();
  for (const row of rows) {
    const status = String(row?.audit_status || "unknown");
    counts.set(status, Number(counts.get(status) || 0) + 1);
  }
  return Array.from(counts.entries())
    .map(([status, count]) => ({ status, count }))
    .sort((a, b) => b.count - a.count);
}
