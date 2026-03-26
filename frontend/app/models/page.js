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
        eyebrow="Model Performance"
        title="A/B/C/D Performance Analytics"
        description="Compare each model with separated tabs for realized PnL, win rate, and equity trajectory."
        actions={[
          { href: "/positions", label: "Execution Trail", tone: "primary" },
          { href: "/settings", label: "Runtime Settings", tone: "ghost" },
        ]}
      />

      {!data.ready ? (
        <section className="warning-card">
          <strong>Could not load model performance data.</strong>
          {data.errors.map((msg) => (
            <p key={msg}>{msg}</p>
          ))}
        </section>
      ) : null}

      <section className="kpi-row">
        <MetricCard label="Cumulative Realized PnL" value={formatMoney(totalRealized)} meta="all latest rows" tone="green" />
        <MetricCard label="Closed Trades" value={formatNumber(totalClosed)} meta="sum of all models" tone="amber" />
        <MetricCard
          label="Top Model"
          value={bestModelMeta ? bestModelMeta.name : "no data"}
          meta={bestModel ? formatMoney(bestModel.realizedPnlUsd) : "pending"}
          tone="cyan"
        />
        <MetricCard label="Models With Data" value={formatNumber(data.modelSummaries.length)} meta="A/B/C/D coverage" />
      </section>

      <ModelsPerformanceTabs modelSummaries={data.modelSummaries} dailyRows={data.dailyRows} tunes={data.tunes} />
    </>
  );
}
