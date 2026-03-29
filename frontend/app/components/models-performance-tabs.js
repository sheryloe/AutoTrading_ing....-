"use client";

import { useMemo, useState } from "react";
import EmptyState from "./empty-state";
import SectionCard from "./section-card";
import TablePanel from "./table-panel";
import StatusBadge from "./status-badge";
import { formatMoney, formatNumber, formatPercent } from "../../lib/formatters";
import { getModelMeta, MODEL_ORDER } from "../../lib/model-meta";

function pickDefaultModel(modelSummaries, dailyRows, tunes) {
  const ids = new Set([
    ...modelSummaries.map((item) => String(item.modelId || "").toUpperCase()),
    ...dailyRows.map((item) => String(item.model_id || "").toUpperCase()),
    ...tunes.map((item) => String(item.model_id || "").toUpperCase()),
  ]);
  return MODEL_ORDER.find((id) => ids.has(id)) || "A";
}

function buildTrendPoints(rows, valueKey) {
  const ordered = [...rows].sort((a, b) => String(a.day || "").localeCompare(String(b.day || "")));
  return ordered.map((row) => ({
    label: String(row.day || ""),
    value: Number(row[valueKey] || 0),
  }));
}

function buildPolyline(points) {
  if (!points.length) return "";
  const values = points.map((point) => point.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  return points
    .map((point, index) => {
      const x = points.length === 1 ? 50 : (index / (points.length - 1)) * 100;
      const y = 44 - ((point.value - min) / span) * 36;
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");
}

function TrendChart({ title, caption, points }) {
  if (!points.length) {
    return (
      <article className="chart-card">
        <div className="chart-head">
          <strong>{title}</strong>
          <span>{caption}</span>
        </div>
        <div className="chart-empty">데이터가 아직 없습니다.</div>
      </article>
    );
  }

  const first = points[0];
  const last = points[points.length - 1];
  const delta = last.value - first.value;
  const deltaClass = delta > 0 ? "positive" : delta < 0 ? "negative" : "flat";

  return (
    <article className="chart-card">
      <div className="chart-head">
        <strong>{title}</strong>
        <span>{caption}</span>
      </div>
      <svg className="trend-chart" viewBox="0 0 100 48" preserveAspectRatio="none" aria-hidden="true">
        <polyline className="trend-grid" points="0,44 100,44" />
        <polyline className="trend-grid" points="0,26 100,26" />
        <polyline className={`trend-line ${deltaClass}`} points={buildPolyline(points)} />
      </svg>
      <div className="trend-stats">
        <div>
          <span>시작</span>
          <strong>{formatMoney(first.value)}</strong>
        </div>
        <div>
          <span>최신</span>
          <strong>{formatMoney(last.value)}</strong>
        </div>
        <div>
          <span>변화</span>
          <strong className={`trend-delta ${deltaClass}`}>{formatMoney(delta)}</strong>
        </div>
      </div>
    </article>
  );
}

function sortDailyRowsDesc(rows = []) {
  return [...rows].sort((a, b) => {
    const dayCmp = String(b.day || "").localeCompare(String(a.day || ""));
    if (dayCmp !== 0) return dayCmp;
    return String(b.updated_at || "").localeCompare(String(a.updated_at || ""));
  });
}

export default function ModelsPerformanceTabs({ modelSummaries, dailyRows, tunes }) {
  const [activeModel, setActiveModel] = useState(() => pickDefaultModel(modelSummaries, dailyRows, tunes));

  const activeSummary = useMemo(
    () => modelSummaries.find((item) => String(item.modelId || "").toUpperCase() === activeModel) || null,
    [activeModel, modelSummaries]
  );
  const activeRows = useMemo(
    () =>
      sortDailyRowsDesc(dailyRows.filter((item) => String(item.model_id || "").toUpperCase() === activeModel)),
    [activeModel, dailyRows]
  );
  const activeTune = useMemo(
    () => tunes.find((item) => String(item.model_id || "").toUpperCase() === activeModel) || null,
    [activeModel, tunes]
  );

  const realizedTrend = useMemo(() => buildTrendPoints(activeRows, "realized_pnl_usd"), [activeRows]);
  const totalTrend = useMemo(() => buildTrendPoints(activeRows, "total_pnl_usd"), [activeRows]);
  const meta = getModelMeta(activeModel);

  return (
    <section className="tab-shell">
      <div className="tab-strip" role="tablist" aria-label="모델 선택">
        {MODEL_ORDER.map((modelId) => {
          const item = getModelMeta(modelId);
          const active = activeModel === modelId;
          return (
            <button
              key={modelId}
              type="button"
              className={`tab-button ${active ? "active" : ""}`}
              onClick={() => setActiveModel(modelId)}
            >
              <span className="tab-eyebrow">모델 {modelId}</span>
              <strong>{item.name}</strong>
              <small>{item.subtitle}</small>
            </button>
          );
        })}
      </div>

      <SectionCard eyebrow={`모델 ${activeModel}`} title={meta.name} meta={meta.subtitle} className="model-focus-card">
        <div className="model-focus-grid">
          <div className="model-focus-copy">
            <p>{meta.description}</p>
            <div className="status-row compact">
              <StatusBadge tone="info">기준일 {activeSummary?.latestDay || "-"}</StatusBadge>
              <StatusBadge tone="success">승률 {formatPercent(activeSummary?.latestWinRate || 0)}</StatusBadge>
              <StatusBadge tone="warning">종료 거래 {formatNumber(activeSummary?.closedTrades || 0)}</StatusBadge>
            </div>
          </div>

          <div className="focus-metric-grid">
            <article className="focus-metric-card">
              <span>누적 실현 PnL</span>
              <strong>{formatMoney(activeSummary?.realizedPnlUsd || 0)}</strong>
            </article>
            <article className="focus-metric-card">
              <span>미실현 PnL</span>
              <strong>{formatMoney(activeSummary?.unrealizedPnlUsd || 0)}</strong>
            </article>
            <article className="focus-metric-card">
              <span>총손익</span>
              <strong>{formatMoney(activeSummary?.totalPnlUsd || 0)}</strong>
            </article>
            <article className="focus-metric-card">
              <span>총자산(시드+총손익)</span>
              <strong>{formatMoney(activeSummary?.latestEquityUsd || 0)}</strong>
            </article>
          </div>
        </div>
      </SectionCard>

      <section className="chart-grid">
        <TrendChart title="누적 실현 PnL 추이" caption="일자별 누적 실현값" points={realizedTrend} />
        <TrendChart title="총손익 추이" caption="일자별 총손익" points={totalTrend} />
      </section>

      <section className="content-grid content-grid-two">
        <TablePanel eyebrow="일자별 성과" title={`${meta.name} PnL 상세`} meta={`${activeRows.length}일`}>
          <table>
            <thead>
              <tr>
                <th>일자</th>
                <th>총자산</th>
                <th>누적 실현</th>
                <th>당일 실현Δ</th>
                <th>미실현</th>
                <th>총손익</th>
                <th>종료 거래</th>
              </tr>
            </thead>
            <tbody>
              {activeRows.length ? (
                activeRows.map((row) => (
                  <tr key={`${row.day}-${row.model_id}`}>
                    <td>{String(row.day || "-")}</td>
                    <td>{formatMoney(row.equity_usd)}</td>
                    <td>{formatMoney(row.realized_pnl_usd)}</td>
                    <td>{formatMoney(row.daily_realized_delta)}</td>
                    <td>{formatMoney(row.unrealized_pnl_usd)}</td>
                    <td>{formatMoney(row.total_pnl_usd)}</td>
                    <td>{formatNumber(row.closed_trades || 0)}</td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan="7">표시할 일자 데이터가 없습니다.</td>
                </tr>
              )}
            </tbody>
          </table>
        </TablePanel>

        <SectionCard eyebrow="튜닝 상태" title={`${meta.name} 튜닝 메모`} meta={activeTune?.last_eval_note_code || "-"}>
          {activeTune ? (
            <div className="mini-list">
              <article className="mini-card">
                <div>
                  <strong>버전</strong>
                  <p>{activeTune.active_variant_id || "기본값"}</p>
                </div>
                <div className="mini-metrics">
                  <span>최근 평가 {activeTune.last_eval_at || "-"}</span>
                  <span>종료 거래 {formatNumber(activeTune.last_eval_closed || 0)}</span>
                </div>
              </article>
              <article className="mini-card">
                <div>
                  <strong>평가 메모</strong>
                  <p>{activeTune.last_eval_note_code || "없음"}</p>
                </div>
                <div className="mini-metrics">
                  <span>모드 {activeTune.trade_mode || "paper"}</span>
                </div>
              </article>
            </div>
          ) : (
            <EmptyState title="튜닝 데이터 없음" description="이 모델의 튜닝 이력이 아직 없습니다." />
          )}
        </SectionCard>
      </section>
    </section>
  );
}
