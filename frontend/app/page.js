import EmptyState from "./components/empty-state";
import MetricCard from "./components/metric-card";
import PageHeader from "./components/page-header";
import SectionCard from "./components/section-card";
import StatusBadge from "./components/status-badge";
import { loadOverviewPageData } from "../lib/dashboard-data";
import { getModelMeta, MODEL_ORDER } from "../lib/model-meta";
import { formatMoney, formatNumber, formatPercent, formatTs } from "../lib/formatters";

function buildOverviewModelCards(dailyRows = []) {
  return MODEL_ORDER.map((modelId) => {
    const latest = dailyRows.find((row) => String(row.model_id || "").toUpperCase() === modelId) || null;
    return {
      modelId,
      meta: getModelMeta(modelId),
      latest,
    };
  });
}

function buildOverviewPositionSummary(openPositions = []) {
  const counts = new Map(MODEL_ORDER.map((modelId) => [modelId, 0]));
  for (const row of openPositions) {
    const modelId = String(row.model_id || "").toUpperCase();
    counts.set(modelId, Number(counts.get(modelId) || 0) + 1);
  }
  return MODEL_ORDER.map((modelId) => {
    const meta = getModelMeta(modelId);
    return {
      modelId,
      meta,
      count: Number(counts.get(modelId) || 0),
    };
  });
}

function entryLabel(row) {
  const actual = Number(row.actual_entry_price || 0);
  if (actual > 0) return formatMoney(actual);
  const planned = Number(row.planned_entry_price || 0);
  return planned > 0 ? `${formatMoney(planned)} (plan)` : "-";
}

function tpLabel(row) {
  const value = Number(row.take_profit_price || row.target_price_2 || row.target_price_1 || 0);
  return value > 0 ? formatMoney(value) : "-";
}

function slLabel(row) {
  const value = Number(row.stop_loss_price || 0);
  return value > 0 ? formatMoney(value) : "-";
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

export const dynamic = "force-dynamic";

export default async function HomePage() {
  const data = await loadOverviewPageData();
  const snapshot = data.snapshot;
  const modelCards = buildOverviewModelCards(data.dailyRows);
  const positionSummary = buildOverviewPositionSummary(data.openPositions);

  return (
    <>
      <PageHeader
        eyebrow="운영 개요"
        title="동그리 크립토 트레이딩 에이전트"
        description="개요 화면에서는 핵심 지표와 최근 사이클, 모델 스냅샷만 먼저 보여줍니다. 성과 분석과 설정 입력은 각 전용 화면에서 따로 관리합니다."
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
        />
        <MetricCard
          label="최근 실현 PnL"
          value={formatMoney(snapshot?.totalRealizedUsd || 0)}
          meta={`최신 ${data.dailyRows.length}개 일자 기준`}
          tone="green"
        />
        <MetricCard
          label="집계된 거래 수"
          value={String(snapshot?.totalClosedTrades || 0)}
          meta={`기준 일자 ${snapshot?.latestPnlDay || "-"}`}
          tone="amber"
        />
        <MetricCard
          label="오픈 포지션"
          value={String(snapshot?.openPositionCount || 0)}
          meta={`최근 신호 ${snapshot?.latestSignalCount || 0}건`}
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
              현재 개요 화면은 숫자만 보는 곳이 아니라, 어떤 모델이 실제 포지션을 들고 있는지까지
              한 번에 파악하는 운영 보드로 동작합니다.
            </p>
          </div>

          <div className="overview-coverage-grid">
            {positionSummary.map(({ modelId, meta, count }) => (
              <article key={modelId} className="coverage-card">
                <span>{`MODEL ${modelId}`}</span>
                <strong>{count}</strong>
                <p>{meta.name}</p>
              </article>
            ))}
          </div>
        </div>
      </SectionCard>

      <section className="content-grid content-grid-two">
        <SectionCard
          eyebrow="실시간 포지션"
          title="지금 열려 있는 포지션"
          meta={`총 ${snapshot?.openPositionCount || 0}건`}
        >
          {data.openPositions.length ? (
            <div className="overview-open-grid">
              {data.openPositions.map((row) => {
                const model = getModelMeta(row.model_id);
                return (
                  <article key={row.id} className="overview-open-card">
                    <div className="overview-open-top">
                      <div>
                        <span className="tab-eyebrow">{`MODEL ${String(row.model_id || "-").toUpperCase()}`}</span>
                        <strong>{row.symbol}</strong>
                        <p>{model.name}</p>
                      </div>
                      <strong className={`position-pnl ${pnlToneClass(row.unrealized_pnl_usd)}`}>
                        {formatMoney(row.unrealized_pnl_usd)}
                      </strong>
                    </div>
                    <div className="overview-open-metrics">
                      <span>오픈 {formatTs(row.opened_at)}</span>
                      <span>진입 {entryLabel(row)}</span>
                      <span>TP {tpLabel(row)}</span>
                      <span>SL {slLabel(row)}</span>
                      <span>레버리지 {leverageLabel(row.leverage)}</span>
                    </div>
                  </article>
                );
              })}
            </div>
          ) : (
            <EmptyState
              title="오픈 포지션이 없습니다"
              description="새 체결이 생기면 이 영역에서 모델과 심볼, 진입가, TP/SL, 미실현 PnL이 바로 보입니다."
            />
          )}
        </SectionCard>

        <SectionCard eyebrow="운영 포인트" title="모델별 포지션 커버리지" meta="대시보드 즉시 확인">
          <div className="summary-stack">
            {positionSummary.map(({ modelId, meta, count }) => (
              <div key={modelId} className="summary-line">
                <div className="summary-copy">
                  <strong>{`MODEL ${modelId}`}</strong>
                  <span>{meta.subtitle}</span>
                </div>
                <div className="summary-value-stack">
                  <strong>{count}건</strong>
                  <span>{count ? "포지션 활성" : "대기 중"}</span>
                </div>
              </div>
            ))}
          </div>
        </SectionCard>
      </section>

      <SectionCard eyebrow="모델 스냅샷" title="모델별 오늘의 흐름" meta="개요 화면 전용 요약">
        <div className="overview-model-grid">
          {modelCards.map(({ modelId, meta, latest }) => (
            <article key={modelId} className="overview-model-card">
              <div className="overview-model-head">
                <span>{`MODEL ${modelId}`}</span>
                <strong>{meta.name}</strong>
              </div>
              <p className="overview-model-copy">{meta.subtitle}</p>
              <div className="overview-model-metrics">
                <div>
                  <label>실현 PnL</label>
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
            </article>
          ))}
        </div>
      </SectionCard>
    </>
  );
}
