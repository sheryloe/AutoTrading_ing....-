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

function buildTrendPoints(rows, valueKey, cumulative = false) {
  const ordered = [...rows].sort((a, b) => String(a.day).localeCompare(String(b.day)));
  let running = 0;
  return ordered.map((row) => {
    const nextValue = Number(row[valueKey] || 0);
    running = cumulative ? running + nextValue : nextValue;
    return {
      label: String(row.day || ""),
      value: running,
    };
  });
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

function TrendChart({ title, caption, points, money = false }) {
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
  const formatter = money ? formatMoney : (value) => formatNumber(value, 2);

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
          <strong>{formatter(first.value)}</strong>
        </div>
        <div>
          <span>최근</span>
          <strong>{formatter(last.value)}</strong>
        </div>
        <div>
          <span>변화</span>
          <strong className={`trend-delta ${deltaClass}`}>{formatter(delta)}</strong>
        </div>
      </div>
    </article>
  );
}

export default function ModelsPerformanceTabs({ modelSummaries, dailyRows, tunes }) {
  const [activeModel, setActiveModel] = useState(() => pickDefaultModel(modelSummaries, dailyRows, tunes));

  const activeSummary = useMemo(
    () => modelSummaries.find((item) => String(item.modelId || "").toUpperCase() === activeModel) || null,
    [activeModel, modelSummaries]
  );
  const activeRows = useMemo(
    () =>
      dailyRows
        .filter((item) => String(item.model_id || "").toUpperCase() === activeModel)
        .sort((a, b) => String(b.day || "").localeCompare(String(a.day || ""))),
    [activeModel, dailyRows]
  );
  const activeTune = useMemo(
    () => tunes.find((item) => String(item.model_id || "").toUpperCase() === activeModel) || null,
    [activeModel, tunes]
  );
  // realized_pnl_usd is already cumulative-by-day; do not cumulatively sum again.
  const realizedTrend = useMemo(() => buildTrendPoints(activeRows, "realized_pnl_usd"), [activeRows]);
  const equityTrend = useMemo(() => buildTrendPoints(activeRows, "equity_usd"), [activeRows]);
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
              <StatusBadge tone="info">최근 일자 {activeSummary?.latestDay || "-"}</StatusBadge>
              <StatusBadge tone="success">승률 {formatPercent(activeSummary?.latestWinRate || 0)}</StatusBadge>
              <StatusBadge tone="warning">종료 거래 {formatNumber(activeSummary?.closedTrades || 0)}</StatusBadge>
            </div>
          </div>

          <div className="focus-metric-grid">
            <article className="focus-metric-card">
              <span>누적 실현 pnl</span>
              <strong>{formatMoney(activeSummary?.realizedPnlUsd || 0)}</strong>
            </article>
            <article className="focus-metric-card">
              <span>최근 자산</span>
              <strong>{formatMoney(activeSummary?.latestEquityUsd || 0)}</strong>
            </article>
            <article className="focus-metric-card">
              <span>활성 버전</span>
              <strong>{activeTune?.active_variant_id || "기본값"}</strong>
            </article>
          </div>
        </div>
      </SectionCard>

      <section className="chart-grid">
        <TrendChart title="누적 실현 PnL" caption="일자별 누적 추이" points={realizedTrend} money />
        <TrendChart title="자산 곡선" caption="일자별 자산 추이" points={equityTrend} money />
      </section>

      <section className="content-grid content-grid-two">
        <TablePanel eyebrow="일자별 데이터" title={`${meta.name} PnL 테이블`} meta={`${activeRows.length}행`}>
          <table>
            <thead>
              <tr>
                <th>일자</th>
                <th>자산</th>
                <th>실현 PnL</th>
                <th>승률</th>
                <th>종료 거래</th>
              </tr>
            </thead>
            <tbody>
              {activeRows.length ? (
                activeRows.map((row) => (
                  <tr key={`${row.day}-${row.model_id}`}>
                    <td>{String(row.day)}</td>
                    <td>{formatMoney(row.equity_usd)}</td>
                    <td>{formatMoney(row.realized_pnl_usd)}</td>
                    <td>{formatPercent(row.win_rate)}</td>
                    <td>{row.closed_trades}</td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan="5">표시할 데이터가 없습니다.</td>
                </tr>
              )}
            </tbody>
          </table>
        </TablePanel>

        <SectionCard eyebrow="런타임 튠" title={`${meta.name} 튜닝 메모`} meta={activeTune?.last_eval_note_code || "-"}>
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
            <EmptyState title="런타임 튠 데이터 없음" description="이 모델의 런타임 튜닝 기록이 아직 없습니다." />
          )}
        </SectionCard>
      </section>
    </section>
  );
}
