"use client";

import { useMemo, useState } from "react";
import EmptyState from "./empty-state";
import SectionCard from "./section-card";
import StatusBadge from "./status-badge";
import TablePanel from "./table-panel";
import { formatMoney, formatPct, formatTs } from "../../lib/formatters";
import { getModelMeta, MODEL_ORDER } from "../../lib/model-meta";

function pickDefaultModel(openPositions, setupRows, recentTradeRows) {
  const ids = new Set([
    ...openPositions.map((item) => String(item.model_id || "").toUpperCase()),
    ...setupRows.map((item) => String(item.model_id || "").toUpperCase()),
    ...recentTradeRows.map((item) => String(item.model_id || "").toUpperCase()),
  ]);
  return MODEL_ORDER.find((id) => ids.has(id)) || "A";
}

function tradeTone(row) {
  const side = String(row.side || "").toLowerCase();
  const mode = String(row.event_mode || "").toLowerCase();
  if (side === "buy") {
    return mode === "intrabar" ? "warning" : "info";
  }
  return mode === "intrabar" ? "success" : "muted";
}

function positionFillLabel(row) {
  const fillMode = String(row?.position_meta?.fill_mode || "").toLowerCase();
  return fillMode === "intrabar" ? "intrabar 체결" : "spot 체결";
}

export default function PositionsTabs({ openPositions, setupRows, recentTradeRows }) {
  const [activeModel, setActiveModel] = useState(() => pickDefaultModel(openPositions, setupRows, recentTradeRows));

  const activePositions = useMemo(
    () => openPositions.filter((item) => String(item.model_id || "").toUpperCase() === activeModel),
    [activeModel, openPositions]
  );
  const activeSetups = useMemo(
    () => setupRows.filter((item) => String(item.model_id || "").toUpperCase() === activeModel),
    [activeModel, setupRows]
  );
  const activeTrades = useMemo(
    () => recentTradeRows.filter((item) => String(item.model_id || "").toUpperCase() === activeModel),
    [activeModel, recentTradeRows]
  );
  const latestCycleAt = activeSetups[0]?.cycle_at || null;
  const meta = getModelMeta(activeModel);

  return (
    <section className="tab-shell">
      <div className="tab-strip" role="tablist" aria-label="포지션 모델 선택">
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
              <StatusBadge tone="info">오픈 포지션 {activePositions.length}</StatusBadge>
              <StatusBadge tone="warning">최신 setup {activeSetups.length}</StatusBadge>
              <StatusBadge tone="success">최근 체결 {activeTrades.length}</StatusBadge>
            </div>
          </div>

          <div className="focus-metric-grid">
            <article className="focus-metric-card">
              <span>오픈 포지션</span>
              <strong>{activePositions.length}</strong>
            </article>
            <article className="focus-metric-card">
              <span>최근 체결</span>
              <strong>{activeTrades.length}</strong>
            </article>
            <article className="focus-metric-card">
              <span>최신 사이클</span>
              <strong>{latestCycleAt ? formatTs(latestCycleAt) : "사이클 대기"}</strong>
            </article>
          </div>
        </div>
      </SectionCard>

      <section className="content-grid content-grid-two">
        <SectionCard eyebrow="오픈 포지션" title={`${meta.name} 현재 포지션`} meta={`${activePositions.length}개`}>
          {activePositions.length ? (
            <div className="mini-list">
              {activePositions.map((row) => (
                <article key={row.id} className="mini-card">
                  <div>
                    <strong>{row.symbol}</strong>
                    <p>
                      {row.side} / {row.status}
                    </p>
                    <div className="status-row compact">
                      <StatusBadge tone="info">{positionFillLabel(row)}</StatusBadge>
                    </div>
                  </div>
                  <div className="mini-metrics">
                    <span>진입 {formatMoney(row.actual_entry_price || row.planned_entry_price)}</span>
                    <span>미실현 {formatMoney(row.unrealized_pnl_usd)}</span>
                  </div>
                </article>
              ))}
            </div>
          ) : (
            <EmptyState title="오픈 포지션이 없습니다" description="선택한 모델에 현재 열려 있는 포지션이 없습니다." />
          )}
        </SectionCard>

        <SectionCard eyebrow="최근 체결" title={`${meta.name} 체결 로그`} meta={`${activeTrades.length}건`}>
          {activeTrades.length ? (
            <div className="mini-list">
              {activeTrades.slice(0, 6).map((row, idx) => (
                <article key={`${row.ts}-${row.symbol}-${idx}`} className="mini-card">
                  <div>
                    <strong>{row.symbol}</strong>
                    <p>{formatTs(row.ts)}</p>
                    <div className="status-row compact">
                      <StatusBadge tone={tradeTone(row)}>{row.event_label}</StatusBadge>
                    </div>
                  </div>
                  <div className="mini-metrics">
                    <span>{formatMoney(row.price_usd)}</span>
                    <span>{String(row.side || "").toLowerCase() === "sell" ? formatMoney(row.pnl_usd) : "-"}</span>
                  </div>
                </article>
              ))}
            </div>
          ) : (
            <EmptyState title="최근 체결 로그가 없습니다" description="선택한 모델에 아직 체결 이벤트가 기록되지 않았습니다." />
          )}
        </SectionCard>
      </section>

      <TablePanel eyebrow="진입 계획" title={`${meta.name} Entry / SL / TP`} meta={`${activeSetups.length}건`}>
        <table>
          <thead>
            <tr>
              <th>사이클</th>
              <th>심볼</th>
              <th>엔트리</th>
              <th>손절</th>
              <th>1차 목표</th>
              <th>RR</th>
              <th>상태</th>
            </tr>
          </thead>
          <tbody>
            {activeSetups.length ? (
              activeSetups.map((row) => (
                <tr key={row.id}>
                  <td>{formatTs(row.cycle_at)}</td>
                  <td>{row.symbol}</td>
                  <td>{formatMoney(row.entry_price)}</td>
                  <td>{formatMoney(row.stop_loss_price)}</td>
                  <td>{formatMoney(row.target_price_1)}</td>
                  <td>{Number(row.risk_reward || 0).toFixed(2)}</td>
                  <td>{row.entry_ready ? "진입 가능" : "대기"}</td>
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan="7">데이터가 없습니다.</td>
              </tr>
            )}
          </tbody>
        </table>
      </TablePanel>

      <TablePanel eyebrow="체결 상세" title={`${meta.name} 최근 체결 이벤트`} meta={`${activeTrades.length}건`}>
        <table>
          <thead>
            <tr>
              <th>시각</th>
              <th>심볼</th>
              <th>구분</th>
              <th>방식</th>
              <th>가격</th>
              <th>PnL</th>
              <th>수익률</th>
            </tr>
          </thead>
          <tbody>
            {activeTrades.length ? (
              activeTrades.map((row, idx) => (
                <tr key={`${row.ts}-${row.symbol}-${idx}`}>
                  <td>{formatTs(row.ts)}</td>
                  <td>{row.symbol}</td>
                  <td>{String(row.side || "").toLowerCase() === "buy" ? "체결" : "종료"}</td>
                  <td>{row.event_label}</td>
                  <td>{formatMoney(row.price_usd)}</td>
                  <td>{String(row.side || "").toLowerCase() === "sell" ? formatMoney(row.pnl_usd) : "-"}</td>
                  <td>{String(row.side || "").toLowerCase() === "sell" ? formatPct(row.pnl_pct || 0) : "-"}</td>
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan="7">데이터가 없습니다.</td>
              </tr>
            )}
          </tbody>
        </table>
      </TablePanel>
    </section>
  );
}
