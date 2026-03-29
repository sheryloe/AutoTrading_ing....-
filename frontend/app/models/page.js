import MetricCard from "../components/metric-card";
import ModelsPerformanceTabs from "../components/models-performance-tabs";
import PageHeader from "../components/page-header";
import { loadModelsPageData } from "../../lib/dashboard-data";
import { getModelMeta } from "../../lib/model-meta";
import { formatMoney, formatNumber } from "../../lib/formatters";

export const dynamic = "force-dynamic";

export default async function ModelsPage() {
  const data = await loadModelsPageData();
  const bestModel = [...data.modelSummaries].sort((a, b) => b.realizedPnlUsd - a.realizedPnlUsd)[0] || null;
  const totalRealized = data.modelSummaries.reduce((sum, row) => sum + Number(row.realizedPnlUsd || 0), 0);
  const totalClosed = data.modelSummaries.reduce((sum, row) => sum + Number(row.closedTrades || 0), 0);
  const bestModelMeta = bestModel ? getModelMeta(bestModel.modelId) : null;

  return (
    <>
      <PageHeader
        eyebrow="모델 성과"
        title="A/B/C/D 성과 분석"
        description="모델별 실현 PnL, 승률, 자산 추이를 탭으로 분리해 비교합니다."
        actions={[
          { href: "/positions", label: "실행 추적", tone: "primary" },
          { href: "/settings", label: "런타임 설정", tone: "ghost" },
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

      <section className="kpi-row">
        <MetricCard label="누적 실현 PnL" value={formatMoney(totalRealized)} meta="최근 기준 전체 합계" tone="green" />
        <MetricCard label="종료 거래 수" value={formatNumber(totalClosed)} meta="전 모델 합산" tone="amber" />
        <MetricCard
          label="최상위 모델"
          value={bestModelMeta ? bestModelMeta.name : "데이터 없음"}
          meta={bestModel ? formatMoney(bestModel.realizedPnlUsd) : "집계 대기"}
          tone="cyan"
        />
        <MetricCard label="집계 모델 수" value={formatNumber(data.modelSummaries.length)} meta="A/B/C/D 커버리지" />
      </section>

      <ModelsPerformanceTabs modelSummaries={data.modelSummaries} dailyRows={data.dailyRows} tunes={data.tunes} />
    </>
  );
}
