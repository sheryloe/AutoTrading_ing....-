import { Activity, Crosshair, ShieldAlert, TrendingUp, Wallet } from "lucide-react";
import MetricCard from "./components/metric-card";
import PageHeader from "./components/page-header";
import SectionCard from "./components/section-card";
import StatusBadge from "./components/status-badge";
import { loadOverviewPageData } from "../lib/dashboard-data";
import { getModelMeta, MODEL_ORDER } from "../lib/model-meta";
import { formatMoney, formatNumber, formatPercent, formatPrice, formatTs } from "../lib/formatters";

function entryLabel(row) {
  const actual = Number(row.actual_entry_price || 0);
  if (actual > 0) return formatPrice(actual);
  const planned = Number(row.planned_entry_price || 0);
  return planned > 0 ? `${formatPrice(planned)} plan` : "-";
}

function tpLabel(row) {
  const value = Number(row.take_profit_price || row.target_price_2 || row.target_price_1 || 0);
  return value > 0 ? formatPrice(value) : "-";
}

function slLabel(row) {
  const value = Number(row.stop_loss_price || 0);
  return value > 0 ? formatPrice(value) : "-";
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

function buildOverviewSnapshotByModel(dailyRows = [], openPositions = []) {
  return MODEL_ORDER.map((modelId) => {
    const latest = dailyRows.find((row) => String(row.model_id || "").toUpperCase() === modelId) || null;
    const positions = openPositions.filter((row) => String(row.model_id || "").toUpperCase() === modelId);
    return {
      modelId,
      meta: getModelMeta(modelId),
      latest,
      positions,
    };
  });
}

export const dynamic = "force-dynamic";

export default async function HomePage() {
  const data = await loadOverviewPageData();
  const snapshot = data.snapshot;
  const boards = buildOverviewSnapshotByModel(data.dailyRows, data.openPositions);

  return (
    <>
      <PageHeader
        eyebrow="운영 개요"
        title="동그리 크립토 트레이딩 에이전트"
        description="개요 화면도 이제 모델별 실행 보드로 분리해, 어떤 모델이 실제 포지션을 잡았는지와 현재가가 어디인지 바로 읽을 수 있게 구성했습니다."
        actions={[
          { href: "/models", label: "모델 성과 보기", tone: "primary" },
          { href: "/positions", label: "포지션 보기", tone: "ghost" },
        ]}
      />

      {!data.ready ? (
        <section className="warning-card">
          <strong>Supabase 연결 상태를 먼저 확인해 주세요.</strong>
          {data.errors.map((msg) => (
            <p key={msg}>{msg}</p>
          ))}
        </section>
      ) : null}

      <section className="kpi-row">
        <MetricCard
          label="엔진 하트비트"
          value={snapshot?.heartbeat ? formatTs(snapshot.heartbeat.last_seen_at) : "데이터 없음"}
          meta={snapshot?.heartbeat?.engine_name || "엔진 오프라인"}
          tone="cyan"
          icon={Activity}
        />
        <MetricCard
          label="최근 실현 PnL"
          value={formatMoney(snapshot?.totalRealizedUsd || 0)}
          meta={`최신 ${data.dailyRows.length}개 일자 기준`}
          tone="green"
          icon={TrendingUp}
        />
        <MetricCard
          label="집계된 거래 수"
          value={String(snapshot?.totalClosedTrades || 0)}
          meta={`기준 일자 ${snapshot?.latestPnlDay || "-"}`}
          tone="amber"
          icon={Crosshair}
        />
        <MetricCard
          label="오픈 포지션"
          value={String(snapshot?.openPositionCount || 0)}
          meta={`최근 신호 ${snapshot?.latestSignalCount || 0}건`}
          icon={Wallet}
        />
      </section>

      <SectionCard
        eyebrow="최근 사이클"
        title="현재 엔진 상태"
        meta={snapshot?.latestCycleAt ? formatTs(snapshot.latestCycleAt) : "대기 중"}
      >
        <div className="overview-hero-grid">
          <div className="overview-hero-copy">
            <div className="status-row">
              <StatusBadge tone={snapshot?.heartbeat ? "success" : "muted"}>
                {snapshot?.heartbeat ? "엔진 연결됨" : "엔진 미확인"}
              </StatusBadge>
              <StatusBadge tone={snapshot?.latestSignalCount ? "info" : "muted"}>
                최근 신호 {snapshot?.latestSignalCount || 0}건
              </StatusBadge>
              <StatusBadge tone={snapshot?.openPositionCount ? "warning" : "success"}>
                오픈 포지션 {snapshot?.openPositionCount || 0}
              </StatusBadge>
            </div>
            <p className="overview-support-copy">
              통합 카드 한 장으로 뭉개지지 않도록, 아래에서 각 모델의 오늘 성과와 오픈 포지션을
              따로 분리해 보여줍니다.
            </p>
          </div>

          <div className="overview-coverage-grid">
            {boards.map(({ modelId, meta, positions }) => (
              <article key={modelId} className="coverage-card">
                <span>{`MODEL ${modelId}`}</span>
                <strong>{positions.length}</strong>
                <p>{meta.name}</p>
              </article>
            ))}
          </div>
        </div>
      </SectionCard>

      <SectionCard eyebrow="모델별 실행 보드" title="MODEL A-D를 분리해서 보는 개요" meta="실행 중인 포지션, 현재가, 오늘 성과">
        <div className="overview-board-grid">
          {boards.map(({ modelId, meta, latest, positions }) => (
            <article key={modelId} className="overview-board-card">
              <div className="overview-board-head">
                <div>
                  <span className="tab-eyebrow">{`MODEL ${modelId}`}</span>
                  <strong>{meta.name}</strong>
                  <p>{meta.subtitle}</p>
                </div>
                <StatusBadge tone={positions.length ? "warning" : "muted"}>
                  {positions.length ? `${positions.length}개 오픈` : "대기"}
                </StatusBadge>
              </div>

              <div className="overview-board-metrics">
                <div>
                  <label>오늘 실현 PnL</label>
                  <strong>{formatMoney(latest?.realized_pnl_usd || 0)}</strong>
                </div>
                <div>
                  <label>승률</label>
                  <strong>{formatPercent(latest?.win_rate || 0)}</strong>
                </div>
                <div>
                  <label>종료 거래</label>
                  <strong>{latest?.closed_trades || 0}</strong>
                </div>
              </div>

              {positions.length ? (
                <div className="overview-board-position-list">
                  {positions.map((row) => (
                    <article key={row.id} className="overview-position-line">
                      <div className="overview-position-main">
                        <strong>{row.symbol}</strong>
                        <span>{String(row.side || "").toUpperCase()} / 오픈 {formatTs(row.opened_at)}</span>
                      </div>
                      <div className="overview-position-grid">
                        <span>진입 {entryLabel(row)}</span>
                        <span>현재 {currentPriceLabel(row)}</span>
                        <span>TP {tpLabel(row)}</span>
                        <span>SL {slLabel(row)}</span>
                        <span>레버리지 {leverageLabel(row.leverage)}</span>
                        <strong className={`position-pnl ${pnlToneClass(row.unrealized_pnl_usd)}`}>
                          {formatMoney(row.unrealized_pnl_usd)}
                        </strong>
                      </div>
                    </article>
                  ))}
                </div>
              ) : (
                <div className="overview-board-empty">
                  <ShieldAlert size={16} />
                  <span>현재 오픈 포지션이 없습니다.</span>
                </div>
              )}
            </article>
          ))}
        </div>
      </SectionCard>
    </>
  );
}
