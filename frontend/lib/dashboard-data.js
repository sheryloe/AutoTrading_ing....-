import { getSupabaseAdmin } from "./supabase-admin";

function emptyState(errors = []) {
  return {
    ready: false,
    errors,
  };
}

function collectErrors(results) {
  return results.map((item) => item?.error?.message).filter(Boolean);
}

function buildModelSummaries(dailyRows = [], tunes = []) {
  const map = new Map();

  for (const row of dailyRows) {
    const modelId = String(row.model_id || "-");
    if (!map.has(modelId)) {
      map.set(modelId, {
        modelId,
        latestDay: row.day || null,
        latestEquityUsd: Number(row.equity_usd || 0),
        latestWinRate: Number(row.win_rate || 0),
        realizedPnlUsd: 0,
        closedTrades: 0,
      });
    }
    const summary = map.get(modelId);
    summary.realizedPnlUsd += Number(row.realized_pnl_usd || 0);
    summary.closedTrades += Number(row.closed_trades || 0);
    if (!summary.latestDay || String(row.day) > String(summary.latestDay)) {
      summary.latestDay = row.day || null;
      summary.latestEquityUsd = Number(row.equity_usd || 0);
      summary.latestWinRate = Number(row.win_rate || 0);
    }
  }

  return Array.from(map.values())
    .map((summary) => {
      const tune = tunes.find((item) => item.model_id === summary.modelId) || null;
      return {
        ...summary,
        tune,
      };
    })
    .sort((a, b) => String(a.modelId).localeCompare(String(b.modelId)));
}

function buildOverviewSnapshot({ heartbeat, dailyRows, setupRows, openPositions, openPositionCount }) {
  const latestCycleAt = setupRows[0]?.cycle_at || null;
  const latestSignalCount = latestCycleAt
    ? setupRows.filter((row) => String(row.cycle_at || "") === String(latestCycleAt)).length
    : 0;

  return {
    latestPnlDay: dailyRows[0]?.day || "-",
    totalRealizedUsd: dailyRows.reduce((sum, row) => sum + Number(row.realized_pnl_usd || 0), 0),
    totalClosedTrades: dailyRows.reduce((sum, row) => sum + Number(row.closed_trades || 0), 0),
    openPositionCount: Number(openPositionCount ?? openPositions.length ?? 0),
    latestCycleAt,
    latestSignalCount,
    heartbeat,
  };
}

function missingAdminMessage() {
  return "SUPABASE_URL 또는 서버 비밀키가 설정되지 않았습니다.";
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
    supabase.from("model_setups").select("*").order("cycle_at", { ascending: false }).limit(12),
    supabase
      .from("positions")
      .select("*", { count: "exact" })
      .eq("status", "open")
      .order("opened_at", { ascending: false })
      .limit(24),
  ]);

  const errors = collectErrors([heartbeatRes, dailyRes, setupsRes, positionsRes]);
  const heartbeat = heartbeatRes.data?.[0] || null;
  const dailyRows = dailyRes.data || [];
  const recentSetups = setupsRes.data || [];
  const openPositions = positionsRes.data || [];
  const openPositionCount = Number(positionsRes.count ?? openPositions.length ?? 0);

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
  const dailyRows = dailyRes.data || [];
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
      recentTradeRows: [],
      snapshot: null,
    };
  }

  const [heartbeatRes, positionsRes, setupsRes, recentTradesRes] = await Promise.all([
    supabase.from("engine_heartbeat").select("*").order("last_seen_at", { ascending: false }).limit(1),
    supabase
      .from("positions")
      .select("*", { count: "exact" })
      .eq("status", "open")
      .order("opened_at", { ascending: false })
      .limit(48),
    supabase.from("model_setups").select("*").order("cycle_at", { ascending: false }).limit(18),
    supabase
      .from("engine_state_blobs")
      .select("payload_json,updated_at")
      .eq("blob_key", "recent_crypto_trades")
      .limit(1)
      .maybeSingle(),
  ]);

  const errors = collectErrors([heartbeatRes, positionsRes, setupsRes, recentTradesRes]);
  const heartbeat = heartbeatRes.data?.[0] || null;
  const openPositions = positionsRes.data || [];
  const openPositionCount = Number(positionsRes.count ?? openPositions.length ?? 0);
  const setupRows = setupsRes.data || [];
  const recentTradeRows = Array.isArray(recentTradesRes.data?.payload_json?.rows) ? recentTradesRes.data.payload_json.rows : [];
  const latestCycleAt = setupRows[0]?.cycle_at || null;

  return {
    ready: errors.length === 0,
    errors,
    heartbeat,
    openPositions,
    setupRows,
    recentTradeRows,
    snapshot: {
      latestCycleAt,
      latestSignalCount: latestCycleAt
        ? setupRows.filter((row) => String(row.cycle_at || "") === String(latestCycleAt)).length
        : 0,
      openPositionCount,
      recentTradeCount: recentTradeRows.length,
    },
  };
}
