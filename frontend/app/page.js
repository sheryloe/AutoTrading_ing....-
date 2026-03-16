import MetricCard from "./components/metric-card";
import PageHeader from "./components/page-header";
import SectionCard from "./components/section-card";
import StatusBadge from "./components/status-badge";
import { loadOverviewPageData } from "../lib/dashboard-data";
import { getModelMeta, MODEL_ORDER } from "../lib/model-meta";
import { formatMoney, formatPercent, formatTs } from "../lib/formatters";

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

export const dynamic = "force-dynamic";

export default async function HomePage() {
  const data = await loadOverviewPageData();
  const snapshot = data.snapshot;
  const modelCards = buildOverviewModelCards(data.dailyRows);

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
      </SectionCard>

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
