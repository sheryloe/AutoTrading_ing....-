import MetricCard from "../components/metric-card";
import ModelsPerformanceTabs from "../components/models-performance-tabs";
import PageHeader from "../components/page-header";
import { loadModelsPageData } from "../../lib/dashboard-data";
import { formatMoney, formatNumber } from "../../lib/formatters";

export const dynamic = "force-dynamic";

const MODEL_TONE = {
  A: "cyan",
  B: "green",
  C: "amber",
  D: "cyan",
};

function latestDayOf(summaries = []) {
  const days = summaries.map((row) => String(row?.latestDay || "")).filter(Boolean).sort();
  return days.at(-1) || "-";
}

export default async function ModelsPage() {
  const data = await loadModelsPageData();

  const totalSeed = data.modelSummaries.reduce((sum, row) => sum + Number(row.seedUsd || 0), 0);
  const totalEquity = data.modelSummaries.reduce((sum, row) => sum + Number(row.latestEquityUsd || 0), 0);
  const totalRealized = data.modelSummaries.reduce((sum, row) => sum + Number(row.realizedPnlUsd || 0), 0);
  const totalUnrealized = data.modelSummaries.reduce((sum, row) => sum + Number(row.unrealizedPnlUsd || 0), 0);
  const totalPnl = data.modelSummaries.reduce((sum, row) => sum + Number(row.totalPnlUsd || 0), 0);
  const totalClosed = data.modelSummaries.reduce((sum, row) => sum + Number(row.closedTrades || 0), 0);

  return (
    <>
      <PageHeader
        eyebrow="모델 성과"
        title="A/B/C/D 모델별 손익 대시보드"
        description="누적 실현, 미실현, 총손익, 총자산을 모델별로 고정 분리해 표시합니다."
        actions={[
          { href: "/positions", label: "포지션 상세", tone: "primary" },
          { href: "/settings", label: "운영 설정", tone: "ghost" },
        ]}
      />

      {!data.ready ? (
        <section className="warning-card">
          <strong>모델 성과 데이터를 불러오지 못했습니다.</strong>
          {data.errors.map((msg) => (
            <p key={msg}>{msg}</p>
          ))}
        </section>
      ) : null}

      <section className="kpi-row model-summary-grid">
        {data.modelSummaries.map((summary) => (
          <MetricCard
            key={summary.modelId}
            label={`모델 ${summary.modelId} 누적 실현 PnL`}
            value={formatMoney(summary.realizedPnlUsd)}
            meta={`미실현 ${formatMoney(summary.unrealizedPnlUsd)} · 총손익 ${formatMoney(summary.totalPnlUsd)} · 총자산 ${formatMoney(summary.latestEquityUsd)}`}
            tone={MODEL_TONE[summary.modelId] || "default"}
          />
        ))}
      </section>

      <section className="warning-card">
        <strong>전체 요약 (기준일 {latestDayOf(data.modelSummaries)})</strong>
        <p>
          총 시드 {formatMoney(totalSeed)} · 총자산 {formatMoney(totalEquity)} · 누적 실현 {formatMoney(totalRealized)} ·
          미실현 {formatMoney(totalUnrealized)} · 총손익 {formatMoney(totalPnl)} · 종료 거래 {formatNumber(totalClosed)}
        </p>
      </section>

      <ModelsPerformanceTabs modelSummaries={data.modelSummaries} dailyRows={data.dailyRows} tunes={data.tunes} />
    </>
  );
}
