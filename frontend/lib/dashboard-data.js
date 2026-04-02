import { getSupabaseAdmin } from "./supabase-admin";
import { MODEL_ORDER } from "./model-meta";

function emptyState(errors = []) {
  return {
    ready: false,
    errors,
  };
}

function collectErrors(results) {
  return results.map((item) => item?.error?.message).filter(Boolean);
}

function normalizeModelId(value) {
  return String(value || "").trim().toUpperCase();
}

function toNumber(value) {
  const parsed = Number(value || 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

function compareDateLike(a, b) {
  return String(a || "").localeCompare(String(b || ""));
}

function isRowNewer(nextRow, prevRow) {
  const dayCmp = compareDateLike(nextRow?.day, prevRow?.day);
  if (dayCmp !== 0) return dayCmp > 0;
  const updatedCmp = compareDateLike(nextRow?.updated_at, prevRow?.updated_at);
  if (updatedCmp !== 0) return updatedCmp > 0;
  return false;
}

function pickLatestDailyRowsByModel(rows = []) {
  const latestByModel = new Map();
  for (const row of rows || []) {
    const modelId = normalizeModelId(row?.model_id);
    if (!MODEL_ORDER.includes(modelId)) {
      continue;
    }
    const previous = latestByModel.get(modelId);
    if (!previous || isRowNewer(row, previous)) {
      latestByModel.set(modelId, row);
    }
  }
  return latestByModel;
}

function buildDailyRowsWithDelta(rows = []) {
  const grouped = new Map();
  for (const modelId of MODEL_ORDER) {
    grouped.set(modelId, []);
  }

  for (const row of rows || []) {
    const modelId = normalizeModelId(row?.model_id);
    if (!grouped.has(modelId)) continue;
    grouped.get(modelId).push({
      ...row,
      model_id: modelId,
      equity_usd: toNumber(row?.equity_usd),
      total_pnl_usd: toNumber(row?.total_pnl_usd),
      realized_pnl_usd: toNumber(row?.realized_pnl_usd),
      unrealized_pnl_usd: toNumber(row?.unrealized_pnl_usd),
      closed_trades: toNumber(row?.closed_trades),
      win_rate: toNumber(row?.win_rate),
    });
  }

  const withDelta = [];
  for (const modelId of MODEL_ORDER) {
    const orderedAsc = [...(grouped.get(modelId) || [])].sort((a, b) => {
      const dayCmp = compareDateLike(a?.day, b?.day);
      if (dayCmp !== 0) return dayCmp;
      return compareDateLike(a?.updated_at, b?.updated_at);
    });

    let prevRealized = null;
    let prevTotal = null;
    for (const row of orderedAsc) {
      const realized = toNumber(row.realized_pnl_usd);
      const total = toNumber(row.total_pnl_usd);
      const sourceJson = row?.source_json && typeof row.source_json === "object" ? row.source_json : {};
      const rebuildSource = String(row?.rebuild_source || sourceJson?.rebuild_source || "").trim();
      const restartVariant = String(
        row?.bybit_rebuild_restart_variant_id ||
          sourceJson?.bybit_rebuild_restart_variant_id ||
          sourceJson?.rebuild_restart_variant_id ||
          ""
      ).trim();
      const hasRestartMarker = rebuildSource === "drawdown_50pct_rebuild_restart" || Boolean(restartVariant);
      const isDResetBoundary = modelId === "D" && hasRestartMarker;
      const dailyRealizedDelta = prevRealized === null || isDResetBoundary ? realized : realized - prevRealized;
      const dailyTotalDelta = prevTotal === null || isDResetBoundary ? total : total - prevTotal;
      withDelta.push({
        ...row,
        daily_realized_delta: dailyRealizedDelta,
        daily_total_pnl_delta: dailyTotalDelta,
      });
      prevRealized = realized;
      prevTotal = total;
    }
  }

  return withDelta.sort((a, b) => {
    const dayCmp = compareDateLike(b?.day, a?.day);
    if (dayCmp !== 0) return dayCmp;
    const modelCmp = MODEL_ORDER.indexOf(normalizeModelId(a?.model_id)) - MODEL_ORDER.indexOf(normalizeModelId(b?.model_id));
    if (modelCmp !== 0) return modelCmp;
    return compareDateLike(b?.updated_at, a?.updated_at);
  });
}

function countRowsForLatestCycle(rows = [], key = "cycle_at") {
  const latestCycleAt = rows[0]?.[key] || null;
  if (!latestCycleAt) {
    return { latestCycleAt: null, count: 0 };
  }
  let count = 0;
  for (const row of rows) {
    if (String(row?.[key] || "") !== String(latestCycleAt)) break;
    count += 1;
  }
  return { latestCycleAt, count };
}

function buildModelSummaries(dailyRows = [], tunes = []) {
  const latestByModel = pickLatestDailyRowsByModel(dailyRows);
  const tuneMap = new Map((tunes || []).map((item) => [normalizeModelId(item?.model_id), item]));

  return MODEL_ORDER.map((modelId) => {
    const row = latestByModel.get(modelId) || null;
    const equityUsd = toNumber(row?.equity_usd);
    const totalPnlUsd = toNumber(row?.total_pnl_usd);
    return {
      modelId,
      latestDay: row?.day || null,
      latestEquityUsd: equityUsd,
      latestWinRate: toNumber(row?.win_rate),
      realizedPnlUsd: toNumber(row?.realized_pnl_usd),
      unrealizedPnlUsd: toNumber(row?.unrealized_pnl_usd),
      totalPnlUsd,
      seedUsd: row ? equityUsd - totalPnlUsd : 10000,
      closedTrades: toNumber(row?.closed_trades),
    };
  })
    .map((summary) => {
      const tune = tuneMap.get(summary.modelId) || null;
      return {
        ...summary,
        tune,
      };
    });
}

function buildOverviewSnapshot({ heartbeat, dailyRows, setupRows, openPositions, openPositionCount, latestSignalCount }) {
  const latestSetup = countRowsForLatestCycle(setupRows, "cycle_at");
  const latestByModel = pickLatestDailyRowsByModel(dailyRows);
  const latestRows = Array.from(latestByModel.values());

  return {
    latestPnlDay: dailyRows[0]?.day || "-",
    totalRealizedUsd: latestRows.reduce((sum, row) => sum + Number(row.realized_pnl_usd || 0), 0),
    totalClosedTrades: latestRows.reduce((sum, row) => sum + Number(row.closed_trades || 0), 0),
    openPositionCount: Number(openPositionCount ?? openPositions.length ?? 0),
    latestCycleAt: latestSetup.latestCycleAt,
    latestSignalCount: Number(latestSignalCount ?? latestSetup.count ?? 0),
    heartbeat,
  };
}

function missingAdminMessage() {
  return "Supabase admin environment variables are missing. Set SUPABASE_URL and a service-role key.";
}

export async function loadOverviewPageData() {
  const supabase = getSupabaseAdmin();
  if (!supabase) {
    return {
      ...emptyState([missingAdminMessage()]),
      heartbeat: null,
      dailyRows: [],
      recentSetups: [],
      openPositions: [],
      snapshot: null,
    };
  }

  const [heartbeatRes, dailyRes, setupsRes, positionsRes] = await Promise.all([
    supabase.from("engine_heartbeat").select("*").order("last_seen_at", { ascending: false }).limit(1),
    supabase.from("daily_model_pnl").select("*").order("day", { ascending: false }).limit(12),
    supabase.from("model_setups").select("*").order("cycle_at", { ascending: false }).limit(240),
    supabase
      .from("positions")
      .select("*", { count: "exact" })
      .eq("status", "open")
      .order("opened_at", { ascending: false })
      .limit(24),
  ]);

  const latestSetupCycleAt = setupsRes.data?.[0]?.cycle_at || null;
  const latestSetupCountRes = latestSetupCycleAt
    ? await supabase
        .from("model_setups")
        .select("*", { count: "exact", head: true })
        .eq("cycle_at", latestSetupCycleAt)
    : { count: 0, error: null };

  const errors = collectErrors([heartbeatRes, dailyRes, setupsRes, positionsRes, latestSetupCountRes]);
  const heartbeat = heartbeatRes.data?.[0] || null;
  const dailyRows = dailyRes.data || [];
  const recentSetups = setupsRes.data || [];
  const openPositions = positionsRes.data || [];
  const openPositionCount = Number(positionsRes.count ?? openPositions.length ?? 0);
  const latestSetupCount = Number(latestSetupCountRes.count ?? countRowsForLatestCycle(recentSetups, "cycle_at").count ?? 0);

  return {
    ready: errors.length === 0,
    errors,
    heartbeat,
    dailyRows,
    recentSetups,
    openPositions,
    snapshot: buildOverviewSnapshot({
      heartbeat,
      dailyRows,
      setupRows: recentSetups,
      openPositions,
      openPositionCount,
      latestSignalCount: latestSetupCount,
    }),
  };
}

export async function loadModelsPageData() {
  const supabase = getSupabaseAdmin();
  if (!supabase) {
    return {
      ...emptyState([missingAdminMessage()]),
      dailyRows: [],
      tunes: [],
      modelSummaries: [],
    };
  }

  const [dailyRes, tunesRes] = await Promise.all([
    supabase.from("daily_model_pnl").select("*").order("day", { ascending: false }).limit(24),
    supabase.from("model_runtime_tunes").select("*").order("model_id", { ascending: true }),
  ]);

  const errors = collectErrors([dailyRes, tunesRes]);
  const dailyRows = buildDailyRowsWithDelta(dailyRes.data || []);
  const tunes = tunesRes.data || [];

  return {
    ready: errors.length === 0,
    errors,
    dailyRows,
    tunes,
    modelSummaries: buildModelSummaries(dailyRows, tunes),
  };
}

export async function loadPositionsPageData() {
  const supabase = getSupabaseAdmin();
  if (!supabase) {
    return {
      ...emptyState([missingAdminMessage()]),
      heartbeat: null,
      openPositions: [],
      setupRows: [],
      signalAuditRows: [],
      recentTradeRows: [],
      snapshot: {
        latestCycleAt: null,
        latestSignalCount: 0,
        openPositionCount: 0,
        latestSignalAuditCycleAt: null,
        latestSignalAuditCount: 0,
        recentTradeCount: 0,
      },
    };
  }

  const [heartbeatRes, positionsRes, setupsRes, signalAuditRes, recentTradesRes] = await Promise.all([
    supabase.from("engine_heartbeat").select("*").order("last_seen_at", { ascending: false }).limit(1),
    supabase
      .from("positions")
      .select("*", { count: "exact" })
      .eq("status", "open")
      .order("opened_at", { ascending: false })
      .limit(48),
    supabase.from("model_setups").select("*").order("cycle_at", { ascending: false }).limit(240),
    supabase
      .from("model_signal_audit")
      .select("*")
      .eq("market", "crypto")
      .order("cycle_at", { ascending: false })
      .limit(480),
    supabase
      .from("engine_state_blobs")
      .select("payload_json,updated_at")
      .eq("blob_key", "recent_crypto_trades")
      .limit(1)
      .maybeSingle(),
  ]);

  const latestSetupCycleAt = setupsRes.data?.[0]?.cycle_at || null;
  const latestSetupCountRes = latestSetupCycleAt
    ? await supabase
        .from("model_setups")
        .select("*", { count: "exact", head: true })
        .eq("cycle_at", latestSetupCycleAt)
    : { count: 0, error: null };
  const latestAuditCycleAt = signalAuditRes.data?.[0]?.cycle_at || null;
  const latestAuditCountRes = latestAuditCycleAt
    ? await supabase
        .from("model_signal_audit")
        .select("*", { count: "exact", head: true })
        .eq("market", "crypto")
        .eq("cycle_at", latestAuditCycleAt)
    : { count: 0, error: null };

  const errors = collectErrors([
    heartbeatRes,
    positionsRes,
    setupsRes,
    signalAuditRes,
    recentTradesRes,
    latestSetupCountRes,
    latestAuditCountRes,
  ]);
  const heartbeat = heartbeatRes.data?.[0] || null;
  const openPositions = positionsRes.data || [];
  const openPositionCount = Number(positionsRes.count ?? openPositions.length ?? 0);
  const setupRows = setupsRes.data || [];
  const signalAuditRows = signalAuditRes.data || [];
  const recentTradeRows = Array.isArray(recentTradesRes.data?.payload_json?.rows) ? recentTradesRes.data.payload_json.rows : [];
  const latestSetup = countRowsForLatestCycle(setupRows, "cycle_at");
  const latestAudit = countRowsForLatestCycle(signalAuditRows, "cycle_at");
  const latestSetupCount = Number(latestSetupCountRes.count ?? latestSetup.count ?? 0);
  const latestAuditCount = Number(latestAuditCountRes.count ?? latestAudit.count ?? 0);

  return {
    ready: errors.length === 0,
    errors,
    heartbeat,
    openPositions,
    setupRows,
    signalAuditRows,
    recentTradeRows,
    snapshot: {
      latestCycleAt: latestSetup.latestCycleAt,
      latestSignalCount: latestSetupCount,
      openPositionCount,
      latestSignalAuditCycleAt: latestAudit.latestCycleAt,
      latestSignalAuditCount: latestAuditCount,
      recentTradeCount: recentTradeRows.length,
    },
  };
}
