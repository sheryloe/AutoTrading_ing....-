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
  const totalRealized = data.dailyRows.reduce((sum, row) => sum + Number(row.realized_pnl_usd || 0), 0);
  const totalClosed = data.dailyRows.reduce((sum, row) => sum + Number(row.closed_trades || 0), 0);
  const bestModelMeta = bestModel ? getModelMeta(bestModel.modelId) : null;

  return (
    <>
      <PageHeader
        eyebrow="모델 성과"
        title="모델별 결과를 탭으로 분리한 성과 화면"
        description="모델 성과는 한 테이블에서 A/B/C/D를 섞지 않고, 탭으로 나눠 한 모델씩 읽을 수 있게 정리했습니다."
        actions={[
          { href: "/positions", label: "포지션 보기", tone: "primary" },
          { href: "/settings", label: "설정으로 이동", tone: "ghost" },
        ]}
      />

      {!data.ready ? (
        <section className="warning-card">
          <strong>모델 데이터를 불러오지 못했습니다.</strong>
          {data.errors.map((msg) => (
            <p key={msg}>{msg}</p>
          ))}
        </section>
      ) : null}

      <section className="kpi-row">
        <MetricCard label="누적 실현 PnL" value={formatMoney(totalRealized)} meta="최신 적재 일자 기준" tone="green" />
        <MetricCard label="종료된 거래 수" value={formatNumber(totalClosed)} meta="모델 전체 합계" tone="amber" />
        <MetricCard
          label="상위 모델"
          value={bestModelMeta ? bestModelMeta.name : "데이터 없음"}
          meta={bestModel ? formatMoney(bestModel.realizedPnlUsd) : "비교 불가"}
          tone="cyan"
        />
        <MetricCard label="활성 모델 수" value={formatNumber(data.modelSummaries.length)} meta="성과 데이터 보유 기준" />
      </section>

      <ModelsPerformanceTabs modelSummaries={data.modelSummaries} dailyRows={data.dailyRows} tunes={data.tunes} />
    </>
  );
}
