"use client";

import { useMemo, useState } from "react";
import EmptyState from "./empty-state";
import SectionCard from "./section-card";
import StatusBadge from "./status-badge";
import TablePanel from "./table-panel";
import { formatMoney, formatNumber, formatPct, formatPrice, formatTs } from "../../lib/formatters";
import { getModelMeta, MODEL_ORDER } from "../../lib/model-meta";
import { groupRowsByModel, pickDefaultModel, summarizeAuditRows } from "../../lib/positions-view";

function tradeTone(row) {
  const eventKind = String(row.event_kind || "").toLowerCase();
  const mode = String(row.event_mode || "").toLowerCase();
  if (eventKind ? eventKind === "open" : String(row.side || "").toLowerCase() === "buy") {
    return mode === "intrabar" ? "warning" : "info";
  }
  return mode === "intrabar" ? "success" : "muted";
}

function auditTone(status) {
  const normalized = String(status || "").toLowerCase();
  if (normalized === "entry_candidate") return "success";
  if (normalized === "in_position") return "info";
  if (normalized === "filtered_symbol" || normalized === "filtered_gate") return "warning";
  if (normalized === "reentry_blocked" || normalized === "expired") return "warning";
  return "muted";
}

function leverageLabel(value) {
  const leverage = Number(value || 0);
  return leverage > 0 ? `${formatNumber(leverage, 2)}x` : "-";
}

function pnlToneClass(value) {
  const pnl = Number(value || 0);
  if (pnl > 0) return "positive";
  if (pnl < 0) return "negative";
  return "flat";
}

function tradeKindLabel(row) {
  const eventKind = String(row.event_kind || "").toLowerCase();
  if (eventKind) {
    return eventKind === "close" ? "청산" : "진입";
  }
  return String(row.side || "").toLowerCase() === "buy" ? "진입" : "청산";
}

function realizedPnlLabel(row) {
  const isCloseEvent =
    String(row.event_kind || "").toLowerCase() === "close" ||
    (!row.event_kind && String(row.side || "").toLowerCase() === "sell");
  return isCloseEvent && row.pnl_usd !== null && row.pnl_usd !== undefined ? formatMoney(row.pnl_usd) : "-";
}

function realizedPctLabel(row) {
  const isCloseEvent =
    String(row.event_kind || "").toLowerCase() === "close" ||
    (!row.event_kind && String(row.side || "").toLowerCase() === "sell");
  return isCloseEvent && row.pnl_pct !== null && row.pnl_pct !== undefined ? formatPct(row.pnl_pct, 2) : "-";
}

function entryLabel(row) {
  const actual = Number(row.actual_entry_price || 0);
  if (actual > 0) return formatPrice(actual);
  const planned = Number(row.planned_entry_price || 0);
  return planned > 0 ? `${formatPrice(planned)} (계획)` : "-";
}

function tpLabel(row) {
  const value = Number(row.take_profit_price || row.target_price_2 || row.target_price_1 || 0);
  return value > 0 ? formatPrice(value) : "-";
}

function slLabel(row) {
  const value = Number(row.stop_loss_price || 0);
  return value > 0 ? formatPrice(value) : "-";
}

function currentPriceLabel(row) {
  const metaPrice = Number(row?.position_meta?.current_price || 0);
  if (metaPrice > 0) return formatPrice(metaPrice);
  const qty = Number(row.qty || 0);
  const entry = Number(row.actual_entry_price || row.planned_entry_price || 0);
  const pnl = Number(row.unrealized_pnl_usd || 0);
  const side = String(row.side || "long").toLowerCase();
  if (qty > 0 && entry > 0) {
    const derived = side === "short" ? entry - pnl / qty : entry + pnl / qty;
    if (Number.isFinite(derived) && derived > 0) return formatPrice(derived);
  }
  return "-";
}

function openedMeta(row) {
  return row.opened_at ? formatTs(row.opened_at) : "-";
}

function auditLabel(status) {
  const normalized = String(status || "").toLowerCase();
  if (normalized === "entry_candidate") return "entry_candidate";
  if (normalized === "filtered_symbol") return "filtered_symbol";
  if (normalized === "filtered_gate") return "filtered_gate";
  if (normalized === "below_threshold") return "below_threshold";
  if (normalized === "waiting_setup") return "waiting_setup";
  if (normalized === "low_risk_reward") return "low_risk_reward";
  if (normalized === "reentry_blocked") return "reentry_blocked";
  if (normalized === "expired") return "expired";
  if (normalized === "in_position") return "in_position";
  return normalized || "-";
}

function booleanLabel(value) {
  return value ? "Y" : "-";
}

export default function PositionsTabs({ openPositions, setupRows, signalAuditRows, recentTradeRows }) {
  const [activeModel, setActiveModel] = useState(() =>
    pickDefaultModel(openPositions, setupRows, signalAuditRows, recentTradeRows)
  );

  const modelSnapshots = useMemo(
    () => {
      const openByModel = groupRowsByModel(openPositions);
      const setupByModel = groupRowsByModel(setupRows);
      const auditByModel = groupRowsByModel(signalAuditRows);
      const tradesByModel = groupRowsByModel(recentTradeRows);
      return MODEL_ORDER.map((modelId) => ({
        modelId,
        openCount: openByModel[modelId]?.length || 0,
        setupCount: setupByModel[modelId]?.length || 0,
        auditCount: auditByModel[modelId]?.length || 0,
        tradeCount: tradesByModel[modelId]?.length || 0,
      }));
    },
    [openPositions, recentTradeRows, setupRows, signalAuditRows]
  );

  const groupedRows = useMemo(
    () => ({
      openByModel: groupRowsByModel(openPositions),
      setupByModel: groupRowsByModel(setupRows),
      auditByModel: groupRowsByModel(signalAuditRows),
      tradeByModel: groupRowsByModel(recentTradeRows),
    }),
    [openPositions, recentTradeRows, setupRows, signalAuditRows]
  );

  const activePositions = useMemo(() => groupedRows.openByModel[activeModel] || [], [groupedRows.openByModel, activeModel]);
  const activeSetups = useMemo(() => groupedRows.setupByModel[activeModel] || [], [groupedRows.setupByModel, activeModel]);
  const activeAudits = useMemo(() => groupedRows.auditByModel[activeModel] || [], [groupedRows.auditByModel, activeModel]);
  const activeTrades = useMemo(() => groupedRows.tradeByModel[activeModel] || [], [groupedRows.tradeByModel, activeModel]);

  const latestAuditCycleAt = activeAudits[0]?.cycle_at || null;
  const latestAuditRows = useMemo(
    () => activeAudits.filter((item) => String(item.cycle_at || "") === String(latestAuditCycleAt || "")),
    [activeAudits, latestAuditCycleAt]
  );
  const auditSummary = useMemo(() => summarizeAuditRows(latestAuditRows), [latestAuditRows]);
  const meta = getModelMeta(activeModel);

  return (
    <section className="tab-shell">
      <div className="tab-strip" role="tablist" aria-label="모델 선택">
        {MODEL_ORDER.map((modelId) => {
          const item = getModelMeta(modelId);
          const active = activeModel === modelId;
          const snapshot = modelSnapshots.find((row) => row.modelId === modelId);
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
              <div className="tab-button-stats" aria-hidden="true">
                <span className="tab-stat">오픈 {snapshot?.openCount || 0}</span>
                <span className="tab-stat">감사 {snapshot?.auditCount || 0}</span>
                <span className="tab-stat">체결 {snapshot?.tradeCount || 0}</span>
              </div>
            </button>
          );
        })}
      </div>

      <SectionCard eyebrow={`모델 ${activeModel}`} title={meta.name} meta={meta.subtitle} className="model-focus-card">
        <div className="model-focus-grid">
          <div className="model-focus-copy">
            <p>{meta.description}</p>
            <div className="status-row compact">
              <StatusBadge tone="info">오픈 {activePositions.length}</StatusBadge>
              <StatusBadge tone="warning">최근 감사 {latestAuditRows.length}</StatusBadge>
              <StatusBadge tone="success">최근 체결 {activeTrades.length}</StatusBadge>
            </div>
          </div>

          <div className="focus-metric-grid">
            <article className="focus-metric-card">
              <span>오픈 포지션</span>
              <strong>{activePositions.length}</strong>
            </article>
            <article className="focus-metric-card">
              <span>최근 감사 사이클</span>
              <strong>{latestAuditCycleAt ? formatTs(latestAuditCycleAt) : "-"}</strong>
            </article>
            <article className="focus-metric-card">
              <span>최근 체결</span>
              <strong>{activeTrades.length}</strong>
            </article>
          </div>
        </div>
      </SectionCard>

      <SectionCard
        eyebrow="신호 감사"
        title={`${meta.name} 최근 감사 상태`}
        meta={latestAuditCycleAt ? formatTs(latestAuditCycleAt) : "감사 데이터 없음"}
      >
        <p className="panel-note">
          `filtered_symbol`은 현재 사용 중인 유니버스 밖의 종목을 뜻합니다. 동적 유니버스가 꺼져 있으면
          `BYBIT_SYMBOLS` 목록만 사용합니다.
        </p>
        <div className="status-row compact">
          {auditSummary.length ? (
            auditSummary.slice(0, 5).map((item) => (
              <StatusBadge key={item.status} tone={auditTone(item.status)}>
                {auditLabel(item.status)} {item.count}
              </StatusBadge>
            ))
          ) : (
            <StatusBadge tone="muted">감사 데이터 없음</StatusBadge>
          )}
        </div>
      </SectionCard>

      <section className="content-grid content-grid-two">
        <SectionCard eyebrow="오픈 포지션" title={`${meta.name} 현재 포지션`} meta={`${activePositions.length}건`}>
          {activePositions.length ? (
            <div className="mini-list">
              {activePositions.map((row) => (
                <article key={row.id} className="mini-card position-card">
                  <div>
                    <strong>{row.symbol}</strong>
                    <p>
                      {String(row.side || "").toUpperCase()} / {row.status}
                    </p>
                    <p className="position-secondary">진입시간 {openedMeta(row)}</p>
                  </div>
                  <div className="mini-metrics position-metrics">
                    <span className="position-secondary">진입가 {entryLabel(row)}</span>
                    <span className="position-secondary">현재가 {currentPriceLabel(row)}</span>
                    <span className="position-secondary">TP {tpLabel(row)}</span>
                    <span className="position-secondary">SL {slLabel(row)}</span>
                    <span className="position-secondary">레버리지 {leverageLabel(row.leverage)}</span>
                    <strong className={`position-pnl ${pnlToneClass(row.unrealized_pnl_usd)}`}>
                      {formatMoney(row.unrealized_pnl_usd)}
                    </strong>
                    <span className="position-secondary">미실현 PnL</span>
                  </div>
                </article>
              ))}
            </div>
          ) : (
            <EmptyState title="오픈 포지션 없음" description="현재 이 모델의 오픈 포지션이 없습니다." />
          )}
        </SectionCard>

        <SectionCard eyebrow="최근 체결" title={`${meta.name} 최근 포지션 체결`} meta={`${activeTrades.length}건`}>
          {activeTrades.length ? (
            <div className="mini-list">
              {activeTrades.slice(0, 6).map((row, idx) => (
                <article key={`${row.ts}-${row.symbol}-${idx}`} className="mini-card">
                  <div>
                    <strong>{row.symbol}</strong>
                    <p>{formatTs(row.ts)}</p>
                    <div className="status-row compact">
                      <StatusBadge tone={tradeTone(row)}>{row.event_label}</StatusBadge>
                      <StatusBadge tone="muted">{String(row.source || "crypto_demo")}</StatusBadge>
                    </div>
                  </div>
                  <div className="mini-metrics">
                    <span>레버리지 {leverageLabel(row.leverage)}</span>
                    <span>{realizedPnlLabel(row)}</span>
                    <span className="position-secondary">실현 PnL</span>
                  </div>
                </article>
              ))}
            </div>
          ) : (
            <EmptyState title="최근 체결 없음" description="이 모델의 최근 포지션 체결 기록이 없습니다." />
          )}
        </SectionCard>
      </section>

      <TablePanel eyebrow="신호 감사 상세" title={`${meta.name} 감사 로그`} meta={`최근 ${latestAuditRows.length}건`}>
        <table>
          <thead>
            <tr>
              <th>시간</th>
              <th>종목</th>
              <th>상태</th>
              <th>사유</th>
              <th>점수 / 임계값</th>
              <th>허용</th>
              <th>게이트</th>
              <th>진입준비</th>
            </tr>
          </thead>
          <tbody>
            {latestAuditRows.length ? (
              latestAuditRows.map((row) => (
                <tr key={`${row.cycle_at}-${row.symbol}`}>
                  <td>{formatTs(row.cycle_at)}</td>
                  <td>{row.symbol}</td>
                  <td>{auditLabel(row.audit_status)}</td>
                  <td>{row.audit_reason || "-"}</td>
                  <td>
                    {Number(row.score || 0).toFixed(4)} / {Number(row.threshold || 0).toFixed(4)}
                  </td>
                  <td>{booleanLabel(row.symbol_allowed)}</td>
                  <td>{booleanLabel(row.gate_ok)}</td>
                  <td>{booleanLabel(row.entry_ready)}</td>
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan="8">감사 로그가 없습니다.</td>
              </tr>
            )}
          </tbody>
        </table>
      </TablePanel>

      <TablePanel
        eyebrow="포지션 상세"
        title={`${meta.name} 진입 / TP / SL / PnL`}
        meta={`${activePositions.length}건`}
      >
        <table>
          <thead>
            <tr>
              <th>종목</th>
              <th>진입시간</th>
              <th>진입가</th>
              <th>현재가</th>
              <th>TP</th>
              <th>SL</th>
              <th>레버리지</th>
              <th>미실현</th>
              <th>실현</th>
            </tr>
          </thead>
          <tbody>
            {activePositions.length ? (
              activePositions.map((row) => (
                <tr key={row.id}>
                  <td>{row.symbol}</td>
                  <td>{openedMeta(row)}</td>
                  <td>{entryLabel(row)}</td>
                  <td>{currentPriceLabel(row)}</td>
                  <td>{tpLabel(row)}</td>
                  <td>{slLabel(row)}</td>
                  <td>{leverageLabel(row.leverage)}</td>
                  <td className={`position-pnl ${pnlToneClass(row.unrealized_pnl_usd)}`}>
                    {formatMoney(row.unrealized_pnl_usd)}
                  </td>
                  <td className={`position-pnl ${pnlToneClass(row.realized_pnl_usd)}`}>
                    {formatMoney(row.realized_pnl_usd)}
                  </td>
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan="9">포지션 데이터가 없습니다.</td>
              </tr>
            )}
          </tbody>
        </table>
      </TablePanel>

      <TablePanel eyebrow="진입 준비" title={`${meta.name} 셋업 로그`} meta={`${activeSetups.length}건`}>
        <table>
          <thead>
            <tr>
              <th>시간</th>
              <th>방향</th>
              <th>종목</th>
              <th>진입가</th>
              <th>손절가</th>
              <th>목표가1</th>
              <th>RR</th>
              <th>진입준비</th>
            </tr>
          </thead>
          <tbody>
            {activeSetups.length ? (
              activeSetups.map((row) => (
                <tr key={row.id}>
                  <td>{formatTs(row.cycle_at)}</td>
                  <td>{String(row.side || "long").toUpperCase()}</td>
                  <td>{row.symbol}</td>
                  <td>{formatPrice(row.entry_price)}</td>
                  <td>{formatPrice(row.stop_loss_price)}</td>
                  <td>{formatPrice(row.target_price_1)}</td>
                  <td>{Number(row.risk_reward || 0).toFixed(2)}</td>
                  <td>{row.entry_ready ? "Y" : "-"}</td>
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan="8">셋업 데이터가 없습니다.</td>
              </tr>
            )}
          </tbody>
        </table>
      </TablePanel>

      <TablePanel eyebrow="체결 상세" title={`${meta.name} 최근 체결`} meta={`${activeTrades.length}건`}>
        <p className="panel-note">현재 빌드에서 포지션 체결은 거래 경로(`crypto_demo`)로 기록됩니다.</p>
        <table>
          <thead>
            <tr>
              <th>시간</th>
              <th>소스</th>
              <th>종목</th>
              <th>구분</th>
              <th>모드</th>
              <th>레버리지</th>
              <th>가격</th>
              <th>실현 PnL</th>
              <th>실현 %</th>
            </tr>
          </thead>
          <tbody>
            {activeTrades.length ? (
              activeTrades.map((row, idx) => (
                <tr key={`${row.ts}-${row.symbol}-${idx}`}>
                  <td>{formatTs(row.ts)}</td>
                  <td>{String(row.source || "crypto_demo")}</td>
                  <td>{row.symbol}</td>
                  <td>{tradeKindLabel(row)}</td>
                  <td>{row.event_label}</td>
                  <td>{leverageLabel(row.leverage)}</td>
                  <td>{formatPrice(row.price_usd)}</td>
                  <td>{realizedPnlLabel(row)}</td>
                  <td>{realizedPctLabel(row)}</td>
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan="9">체결 데이터가 없습니다.</td>
              </tr>
            )}
          </tbody>
        </table>
      </TablePanel>
    </section>
  );
}
