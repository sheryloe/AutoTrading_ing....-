"use client";

import { useMemo, useState } from "react";
import EmptyState from "./empty-state";
import SectionCard from "./section-card";
import TablePanel from "./table-panel";
import StatusBadge from "./status-badge";
import { formatMoney, formatNumber, formatPct } from "../../lib/formatters";
import { getModelMeta, MODEL_ORDER } from "../../lib/model-meta";

function pickDefaultModel(modelSummaries, dailyRows, tunes) {
  const ids = new Set([
    ...modelSummaries.map((item) => String(item.modelId || "").toUpperCase()),
    ...dailyRows.map((item) => String(item.model_id || "").toUpperCase()),
    ...tunes.map((item) => String(item.model_id || "").toUpperCase()),
  ]);
  return MODEL_ORDER.find((id) => ids.has(id)) || "A";
}

export default function ModelsPerformanceTabs({ modelSummaries, dailyRows, tunes }) {
  const [activeModel, setActiveModel] = useState(() => pickDefaultModel(modelSummaries, dailyRows, tunes));

  const activeSummary = useMemo(
    () => modelSummaries.find((item) => String(item.modelId || "").toUpperCase() === activeModel) || null,
    [activeModel, modelSummaries]
  );
  const activeRows = useMemo(
    () => dailyRows.filter((item) => String(item.model_id || "").toUpperCase() === activeModel),
    [activeModel, dailyRows]
  );
  const activeTune = useMemo(
    () => tunes.find((item) => String(item.model_id || "").toUpperCase() === activeModel) || null,
    [activeModel, tunes]
  );
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
              <span className="tab-eyebrow">MODEL {modelId}</span>
              <strong>{item.name}</strong>
              <small>{item.subtitle}</small>
            </button>
          );
        })}
      </div>

      <SectionCard eyebrow={`MODEL ${activeModel}`} title={meta.name} meta={meta.subtitle} className="model-focus-card">
        <div className="model-focus-grid">
          <div className="model-focus-copy">
            <p>{meta.description}</p>
            <div className="status-row compact">
              <StatusBadge tone="info">최근 일자 {activeSummary?.latestDay || "-"}</StatusBadge>
              <StatusBadge tone="success">승률 {formatPct(activeSummary?.latestWinRate || 0)}</StatusBadge>
              <StatusBadge tone="warning">거래 {formatNumber(activeSummary?.closedTrades || 0)}</StatusBadge>
            </div>
          </div>

          <div className="focus-metric-grid">
            <article className="focus-metric-card">
              <span>누적 실현 PnL</span>
              <strong>{formatMoney(activeSummary?.realizedPnlUsd || 0)}</strong>
            </article>
            <article className="focus-metric-card">
              <span>최신 자산</span>
              <strong>{formatMoney(activeSummary?.latestEquityUsd || 0)}</strong>
            </article>
            <article className="focus-metric-card">
              <span>활성 Variant</span>
              <strong>{activeTune?.active_variant_id || "기본값"}</strong>
            </article>
          </div>
        </div>
      </SectionCard>

      <section className="content-grid content-grid-two">
        <TablePanel eyebrow="일별 성과" title={`${meta.name} PnL 테이블`} meta={`${activeRows.length}건`}>
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
                    <td>{formatPct(row.win_rate)}</td>
                    <td>{row.closed_trades}</td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan="5">데이터가 없습니다.</td>
                </tr>
              )}
            </tbody>
          </table>
        </TablePanel>

        <SectionCard eyebrow="운영 메모" title={`${meta.name} 상태`} meta={activeTune?.last_eval_note_code || "-"}>
          {activeTune ? (
            <div className="mini-list">
              <article className="mini-card">
                <div>
                  <strong>Variant</strong>
                  <p>{activeTune.active_variant_id || "기본값"}</p>
                </div>
                <div className="mini-metrics">
                  <span>마지막 평가 {activeTune.last_eval_at || "-"}</span>
                  <span>참조 거래 {formatNumber(activeTune.last_eval_closed || 0)}</span>
                </div>
              </article>
              <article className="mini-card">
                <div>
                  <strong>평가 메모</strong>
                  <p>{activeTune.last_eval_note_code || "메모 없음"}</p>
                </div>
                <div className="mini-metrics">
                  <span>실행 모드 {activeTune.trade_mode || "paper"}</span>
                </div>
              </article>
            </div>
          ) : (
            <EmptyState title="튜닝 상태가 없습니다" description="선택한 모델의 runtime tune 데이터가 아직 없습니다." />
          )}
        </SectionCard>
      </section>
    </section>
  );
}
