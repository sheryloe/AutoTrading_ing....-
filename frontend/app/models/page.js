import EmptyState from "../components/empty-state";
import MetricCard from "../components/metric-card";
import PageHeader from "../components/page-header";
import SectionCard from "../components/section-card";
import StatusBadge from "../components/status-badge";
import TablePanel from "../components/table-panel";
import { loadModelsPageData } from "../../lib/dashboard-data";
import { formatMoney, formatNumber, formatPct } from "../../lib/formatters";

export const dynamic = "force-dynamic";

export default async function ModelsPage() {
  const data = await loadModelsPageData();
  const bestModel = [...data.modelSummaries].sort((a, b) => b.realizedPnlUsd - a.realizedPnlUsd)[0] || null;
  const totalRealized = data.dailyRows.reduce((sum, row) => sum + Number(row.realized_pnl_usd || 0), 0);
  const totalClosed = data.dailyRows.reduce((sum, row) => sum + Number(row.closed_trades || 0), 0);

  return (
    <>
      <PageHeader
        eyebrow="모델 성과"
        title="모델별 결과와 튜닝 상태"
        description="이 화면은 A/B/C/D 모델의 성과 비교에만 집중합니다. 운영 의사결정에 필요한 숫자와 현재 튜닝 파라미터만 빠르게 읽을 수 있게 구성했습니다."
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
        <MetricCard label="누적 실현 PnL" value={formatMoney(totalRealized)} meta="최신 적재 일자 기준 모델 합계" tone="green" />
        <MetricCard label="종료된 거래 수" value={formatNumber(totalClosed)} meta="모델별 집계 합" tone="amber" />
        <MetricCard
          label="최상위 모델"
          value={bestModel ? `모델 ${bestModel.modelId}` : "데이터 없음"}
          meta={bestModel ? formatMoney(bestModel.realizedPnlUsd) : "비교 불가"}
          tone="cyan"
        />
        <MetricCard label="튜닝 상태" value={formatNumber(data.tunes.length)} meta="현재 runtime tune 항목 수" />
      </section>

      <section className="content-grid model-summary-grid">
        {data.modelSummaries.length ? (
          data.modelSummaries.map((summary) => (
            <SectionCard key={summary.modelId} eyebrow={`모델 ${summary.modelId}`} title="성과 카드" meta={summary.latestDay || "-"}>
              <div className="summary-stack">
                <div className="summary-line">
                  <span>실현 PnL</span>
                  <strong>{formatMoney(summary.realizedPnlUsd)}</strong>
                </div>
                <div className="summary-line">
                  <span>최신 승률</span>
                  <strong>{formatPct(summary.latestWinRate)}</strong>
                </div>
                <div className="summary-line">
                  <span>종료 거래</span>
                  <strong>{formatNumber(summary.closedTrades)}</strong>
                </div>
                <div className="status-row compact">
                  <StatusBadge tone="info">thr {Number(summary.tune?.threshold || 0).toFixed(4)}</StatusBadge>
                  <StatusBadge tone="success">tp {Number(summary.tune?.tp_mul || 0).toFixed(2)}</StatusBadge>
                  <StatusBadge tone="warning">sl {Number(summary.tune?.sl_mul || 0).toFixed(2)}</StatusBadge>
                </div>
              </div>
            </SectionCard>
          ))
        ) : (
          <EmptyState title="모델 성과 데이터가 없습니다" description="daily_model_pnl 또는 model_runtime_tunes가 아직 비어 있습니다." />
        )}
      </section>

      <section className="content-grid content-grid-two">
        <TablePanel eyebrow="일별 누적표" title="모델별 PnL 테이블" meta={`${data.dailyRows.length}건`}>
          <table>
            <thead>
              <tr>
                <th>일자</th>
                <th>모델</th>
                <th>자산</th>
                <th>실현 PnL</th>
                <th>승률</th>
                <th>종료 거래</th>
              </tr>
            </thead>
            <tbody>
              {data.dailyRows.length ? (
                data.dailyRows.map((row) => (
                  <tr key={`${row.day}-${row.model_id}`}>
                    <td>{String(row.day)}</td>
                    <td>{row.model_id}</td>
                    <td>{formatMoney(row.equity_usd)}</td>
                    <td>{formatMoney(row.realized_pnl_usd)}</td>
                    <td>{formatPct(row.win_rate)}</td>
                    <td>{row.closed_trades}</td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan="6">데이터가 없습니다.</td>
                </tr>
              )}
            </tbody>
          </table>
        </TablePanel>

        <SectionCard eyebrow="튜닝 상태" title="현재 runtime 파라미터" meta={`${data.tunes.length}개 모델`}>
          {data.tunes.length ? (
            <div className="mini-list">
              {data.tunes.map((row) => (
                <article key={row.model_id} className="mini-card">
                  <div>
                    <strong>모델 {row.model_id}</strong>
                    <p>{row.active_variant_id || "기본 variant"}</p>
                  </div>
                  <div className="mini-metrics">
                    <span>thr {Number(row.threshold || 0).toFixed(4)}</span>
                    <span>note {row.last_eval_note_code || "-"}</span>
                  </div>
                </article>
              ))}
            </div>
          ) : (
            <EmptyState title="튜닝 상태가 없습니다" description="model_runtime_tunes가 아직 비어 있습니다." />
          )}
        </SectionCard>
      </section>
    </>
  );
}
