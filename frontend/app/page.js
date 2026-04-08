import { ShieldAlert } from "lucide-react";
import PageHeader from "./components/page-header";
import StatusBadge from "./components/status-badge";
import { loadOverviewPageData } from "../lib/dashboard-data";
import { getModelMeta, MODEL_ORDER } from "../lib/model-meta";
import { formatMoney, formatNumber, formatPrice, formatTs } from "../lib/formatters";

function normalizeModelId(value) {
  return String(value || "").trim().toUpperCase();
}

function entryLabel(row) {
  const actual = Number(row.actual_entry_price || 0);
  if (actual > 0) return formatPrice(actual);
  const planned = Number(row.planned_entry_price || 0);
  return planned > 0 ? `${formatPrice(planned)} 계획가` : "-";
}

function leverageLabel(value) {
  const leverage = Number(value || 0);
  return leverage > 0 ? `${formatNumber(leverage, 2)}x` : "-";
}

function pnlToneClass(value) {
  const pnl = Number(value || 0);
  if (pnl > 0) return "positive";
  if (pnl < 0) return "negative";
  return "flat";
}

function currentPriceLabel(row) {
  const metaPrice = Number(row?.position_meta?.current_price || 0);
  if (metaPrice > 0) return formatPrice(metaPrice);
  const qty = Number(row.qty || 0);
  const entry = Number(row.actual_entry_price || row.planned_entry_price || 0);
  const pnl = Number(row.unrealized_pnl_usd || 0);
  const side = String(row.side || "long").toLowerCase();
  if (qty > 0 && entry > 0) {
    const derived = side === "short" ? entry - pnl / qty : entry + pnl / qty;
    if (Number.isFinite(derived) && derived > 0) return formatPrice(derived);
  }
  return "-";
}

function modelTone(modelId) {
  if (modelId === "A") return "model-tone-a";
  if (modelId === "B") return "model-tone-b";
  if (modelId === "C") return "model-tone-c";
  return "model-tone-d";
}

function latestDailyByModel(dailyRows = []) {
  const map = new Map();
  for (const row of dailyRows || []) {
    const modelId = normalizeModelId(row?.model_id);
    if (!MODEL_ORDER.includes(modelId)) continue;
    const prev = map.get(modelId);
    const day = String(row?.day || "");
    const prevDay = String(prev?.day || "");
    const updated = String(row?.updated_at || "");
    const prevUpdated = String(prev?.updated_at || "");
    const newer = !prev || day > prevDay || (day === prevDay && updated > prevUpdated);
    if (newer) map.set(modelId, row);
  }
  return map;
}

function latestSignalByModel(setupRows = []) {
  const latestCycleMap = new Map();
  for (const row of setupRows || []) {
    const modelId = normalizeModelId(row?.model_id);
    if (!MODEL_ORDER.includes(modelId)) continue;
    const cycleAt = String(row?.cycle_at || "");
    const prev = String(latestCycleMap.get(modelId) || "");
    if (cycleAt && cycleAt > prev) latestCycleMap.set(modelId, cycleAt);
  }

  const countMap = new Map();
  for (const row of setupRows || []) {
    const modelId = normalizeModelId(row?.model_id);
    if (!MODEL_ORDER.includes(modelId)) continue;
    const cycleAt = String(row?.cycle_at || "");
    if (!cycleAt || cycleAt !== latestCycleMap.get(modelId)) continue;
    countMap.set(modelId, Number(countMap.get(modelId) || 0) + 1);
  }

  return { latestCycleMap, countMap };
}

function openPositionByModel(openPositions = []) {
  const map = new Map(MODEL_ORDER.map((modelId) => [modelId, []]));
  for (const row of openPositions || []) {
    const modelId = normalizeModelId(row?.model_id);
    if (!map.has(modelId)) continue;
    map.get(modelId).push(row);
  }
  for (const modelId of MODEL_ORDER) {
    map.get(modelId).sort((a, b) => String(b?.updated_at || "").localeCompare(String(a?.updated_at || "")));
  }
  return map;
}

function buildBoards(dailyRows = [], setupRows = [], openPositions = []) {
  const dailyMap = latestDailyByModel(dailyRows);
  const openMap = openPositionByModel(openPositions);
  const { latestCycleMap, countMap } = latestSignalByModel(setupRows);

  return MODEL_ORDER.map((modelId) => {
    const latest = dailyMap.get(modelId) || null;
    const positions = openMap.get(modelId) || [];
    return {
      modelId,
      meta: getModelMeta(modelId),
      latestDay: latest?.day || "-",
      latestCycleAt: latestCycleMap.get(modelId) || null,
      latestSignalCount: Number(countMap.get(modelId) || 0),
      realizedPnlUsd: Number(latest?.realized_pnl_usd || 0),
      equityUsd: Number(latest?.equity_usd || 0),
      closedTrades: Number(latest?.closed_trades || 0),
      winRate: Number(latest?.win_rate || 0),
      openPositions: positions,
    };
  });
}

export const dynamic = "force-dynamic";

export default async function HomePage() {
  const data = await loadOverviewPageData();
  const snapshot = data.snapshot;
  const boards = buildBoards(data.dailyRows, data.recentSetups, data.openPositions);
  const recentPositions = data.openPositions.slice(0, 3);
  const heartbeatMeta = snapshot?.heartbeat?.meta_json || {};
  const tradeMode = String(heartbeatMeta.trade_mode || "").toLowerCase();
  const bybitReadonlySync = Boolean(heartbeatMeta.bybit_readonly_sync);
  const bybitSyncAt = heartbeatMeta.last_bybit_sync_at || null;
  const bybitSyncTs = Number(heartbeatMeta.last_bybit_sync_ts || 0);
  const bybitSyncModeLabel = !snapshot?.heartbeat
    ? "-"
    : tradeMode === "live"
      ? "실거래"
      : bybitReadonlySync
        ? "읽기 전용"
        : "OFF";

  return (
    <>
      <PageHeader
        eyebrow="개요"
        title="AI Auto 운영 대시보드"
        description="상태 확인 중심으로 핵심만 표시합니다. 상세 분석은 모델/포지션 페이지에서 확인하세요."
        actions={[
          { href: "/models", label: "모델 성과", tone: "primary" },
          { href: "/positions", label: "포지션 추적", tone: "ghost" },
          { href: "/settings", label: "운영 설정", tone: "ghost" },
        ]}
      />

      {!data.ready ? (
        <section className="warning-card">
          <strong>Supabase에서 개요 데이터를 불러오지 못했습니다.</strong>
          {data.errors.map((msg) => (
            <p key={msg}>{msg}</p>
          ))}
        </section>
      ) : null}

      <section className="overview-hero">
        <div className="hero-panel">
          <div className="hero-head">
            <span className="hero-chip">고변동성 단타 프로필</span>
            <StatusBadge tone={snapshot?.heartbeat ? "success" : "muted"}>
              {snapshot?.heartbeat ? "엔진 연결됨" : "엔진 오프라인"}
            </StatusBadge>
          </div>

          <h2>고변동성 단타 프로필 운영 중</h2>
          <p>엔진이 데모 시드 기준으로 A/B/C/D 전략을 병렬 추적합니다. 개요는 모델별 상태 확인에 집중합니다.</p>

          <div className="hero-metric-grid">
            <div className="hero-metric">
              <span>최근 하트비트</span>
              <strong>{snapshot?.heartbeat ? formatTs(snapshot.heartbeat.last_seen_at) : "-"}</strong>
              <small>{snapshot?.heartbeat?.engine_name || "엔진"}</small>
            </div>
            <div className="hero-metric">
              <span>전체 오픈 포지션</span>
              <strong>{formatNumber(snapshot?.openPositionCount || 0)}</strong>
              <small>종료 거래 {formatNumber(snapshot?.totalClosedTrades || 0)}건</small>
            </div>
            <div className="hero-metric">
              <span>최신 신호 수</span>
              <strong>{formatNumber(snapshot?.latestSignalCount || 0)}</strong>
              <small>{snapshot?.latestCycleAt ? formatTs(snapshot.latestCycleAt) : "사이클 없음"}</small>
            </div>
            <div className="hero-metric">
              <span>Bybit 동기화 상태(읽기 전용/실거래)</span>
              <strong>{bybitSyncModeLabel}</strong>
              <small>
                {bybitSyncAt ? formatTs(bybitSyncAt) : "-"}
                {bybitSyncTs ? ` (ts ${bybitSyncTs})` : ""}
              </small>
            </div>
          </div>

          <div className="hero-actions">
            <a className="hero-action" href="/models">
              모델별 성과 보기
            </a>
            <a className="hero-action ghost" href="/positions">
              포지션 상세 보기
            </a>
          </div>
        </div>

        <div className="hero-panel hero-panel-alt">
          <div className="hero-tape">
            <div>
              <span>시드 기준</span>
              <strong>모델별 10,000 USDT</strong>
              <small>모델 A/B/C/D 병렬 운용</small>
            </div>
            <div>
              <span>실행 대상</span>
              <strong>paper</strong>
              <small>live arm 꺼짐</small>
            </div>
            <div>
              <span>최신 기준일</span>
              <strong>{snapshot?.latestPnlDay || "-"}</strong>
              <small>daily_model_pnl 스냅샷</small>
            </div>
          </div>

          <div className="hero-mini-list">
            {recentPositions.length ? (
              recentPositions.map((row) => (
                <article key={row.id} className="hero-mini-card">
                  <div>
                    <strong>{row.symbol}</strong>
                    <p>
                      {String(row.side || "").toUpperCase()} 진입가 {entryLabel(row)}
                    </p>
                  </div>
                  <div className="hero-mini-metrics">
                    <span>현재가 {currentPriceLabel(row)}</span>
                    <strong className={`position-pnl ${pnlToneClass(row.unrealized_pnl_usd)}`}>
                      {formatMoney(row.unrealized_pnl_usd)}
                    </strong>
                    <small>레버리지 {leverageLabel(row.leverage)}</small>
                  </div>
                </article>
              ))
            ) : (
              <div className="hero-empty">
                <ShieldAlert size={16} />
                <span>현재 오픈 포지션이 없습니다.</span>
              </div>
            )}
          </div>
        </div>
      </section>

      <section className="model-pulse">
        <div className="model-pulse-head">
          <div>
            <span className="section-eyebrow">모델별 상태</span>
            <h3 className="section-title">A/B/C/D 핵심 지표</h3>
          </div>
          <p className="section-meta">최근 사이클, 신호, 데모 누적 실현 PnL, 오픈 포지션, 종료 거래를 모델별로 분리 표시합니다.</p>
        </div>

        <div className="model-pulse-grid">
          {boards.map((board) => (
            <article key={board.modelId} className={`model-pulse-card ${modelTone(board.modelId)}`}>
              <div className="model-pulse-title">
                <div>
                  <span>{`모델 ${board.modelId}`}</span>
                  <strong>{board.meta.name}</strong>
                  <p>{board.meta.subtitle}</p>
                </div>
                <StatusBadge tone={board.openPositions.length ? "warning" : "muted"}>
                  {board.openPositions.length ? `오픈 ${board.openPositions.length}` : "대기"}
                </StatusBadge>
              </div>

              <div className="model-pulse-metrics">
                <div>
                  <label>최근 사이클</label>
                  <strong>{board.latestCycleAt ? formatTs(board.latestCycleAt) : "-"}</strong>
                </div>
                <div>
                  <label>신호</label>
                  <strong>{formatNumber(board.latestSignalCount)}</strong>
                </div>
                <div>
                  <label>데모 누적 실현 PnL</label>
                  <strong>{formatMoney(board.realizedPnlUsd)}</strong>
                </div>
                <div>
                  <label>오픈 포지션</label>
                  <strong>{formatNumber(board.openPositions.length)}</strong>
                </div>
                <div>
                  <label>종료 거래</label>
                  <strong>{formatNumber(board.closedTrades)}</strong>
                </div>
                <div>
                  <label>총자산</label>
                  <strong>{formatMoney(board.equityUsd)}</strong>
                </div>
              </div>

              <div className="model-pulse-meta">
                <span>기준일</span>
                <strong>{board.latestDay}</strong>
                <small>승률 {formatNumber(board.winRate, 2)}%</small>
              </div>

              <a className="model-pulse-link" href="/models">
                모델 상세 보기
              </a>
            </article>
          ))}
        </div>
      </section>
    </>
  );
}
