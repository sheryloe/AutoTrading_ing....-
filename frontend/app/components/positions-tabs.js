"use client";

import { useMemo, useState } from "react";
import EmptyState from "./empty-state";
import SectionCard from "./section-card";
import StatusBadge from "./status-badge";
import TablePanel from "./table-panel";
import { formatMoney, formatNumber, formatPct, formatPrice, formatTs } from "../../lib/formatters";
import { getModelMeta, MODEL_ORDER } from "../../lib/model-meta";

function pickDefaultModel(openPositions, setupRows, signalAuditRows, recentTradeRows) {
  const ids = new Set([
    ...openPositions.map((item) => String(item.model_id || "").toUpperCase()),
    ...setupRows.map((item) => String(item.model_id || "").toUpperCase()),
    ...signalAuditRows.map((item) => String(item.model_id || "").toUpperCase()),
    ...recentTradeRows.map((item) => String(item.model_id || "").toUpperCase()),
  ]);
  return MODEL_ORDER.find((id) => ids.has(id)) || "A";
}

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
    return eventKind === "close" ? "Close" : "Open";
  }
  return String(row.side || "").toLowerCase() === "buy" ? "Open" : "Close";
}

function realizedPnlLabel(row) {
  const isCloseEvent =
    String(row.event_kind || "").toLowerCase() === "close" ||
    (!row.event_kind && String(row.side || "").toLowerCase() === "sell");
  return isCloseEvent && row.pnl_usd !== null && row.pnl_usd !== undefined
    ? formatMoney(row.pnl_usd)
    : "-";
}

function realizedPctLabel(row) {
  const isCloseEvent =
    String(row.event_kind || "").toLowerCase() === "close" ||
    (!row.event_kind && String(row.side || "").toLowerCase() === "sell");
  return isCloseEvent && row.pnl_pct !== null && row.pnl_pct !== undefined
    ? formatPct(row.pnl_pct, 2)
    : "-";
}

function entryLabel(row) {
  const actual = Number(row.actual_entry_price || 0);
  if (actual > 0) return formatPrice(actual);
  const planned = Number(row.planned_entry_price || 0);
  return planned > 0 ? `${formatPrice(planned)} (plan)` : "-";
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

function summarizeAuditRows(rows) {
  const counts = new Map();
  for (const row of rows) {
    const status = String(row.audit_status || "unknown");
    counts.set(status, Number(counts.get(status) || 0) + 1);
  }
  return Array.from(counts.entries())
    .map(([status, count]) => ({ status, count }))
    .sort((a, b) => b.count - a.count);
}

function booleanLabel(value) {
  return value ? "Y" : "-";
}

export default function PositionsTabs({ openPositions, setupRows, signalAuditRows, recentTradeRows }) {
  const [activeModel, setActiveModel] = useState(() =>
    pickDefaultModel(openPositions, setupRows, signalAuditRows, recentTradeRows)
  );

  const modelSnapshots = useMemo(
    () =>
      MODEL_ORDER.map((modelId) => ({
        modelId,
        openCount: openPositions.filter((item) => String(item.model_id || "").toUpperCase() === modelId).length,
        setupCount: setupRows.filter((item) => String(item.model_id || "").toUpperCase() === modelId).length,
        auditCount: signalAuditRows.filter((item) => String(item.model_id || "").toUpperCase() === modelId).length,
        tradeCount: recentTradeRows.filter((item) => String(item.model_id || "").toUpperCase() === modelId).length,
      })),
    [openPositions, recentTradeRows, setupRows, signalAuditRows]
  );

  const activePositions = useMemo(
    () => openPositions.filter((item) => String(item.model_id || "").toUpperCase() === activeModel),
    [activeModel, openPositions]
  );
  const activeSetups = useMemo(
    () => setupRows.filter((item) => String(item.model_id || "").toUpperCase() === activeModel),
    [activeModel, setupRows]
  );
  const activeAudits = useMemo(
    () => signalAuditRows.filter((item) => String(item.model_id || "").toUpperCase() === activeModel),
    [activeModel, signalAuditRows]
  );
  const activeTrades = useMemo(
    () => recentTradeRows.filter((item) => String(item.model_id || "").toUpperCase() === activeModel),
    [activeModel, recentTradeRows]
  );

  const latestAuditCycleAt = activeAudits[0]?.cycle_at || null;
  const latestAuditRows = useMemo(
    () => activeAudits.filter((item) => String(item.cycle_at || "") === String(latestAuditCycleAt || "")),
    [activeAudits, latestAuditCycleAt]
  );
  const auditSummary = useMemo(() => summarizeAuditRows(latestAuditRows), [latestAuditRows]);
  const meta = getModelMeta(activeModel);

  return (
    <section className="tab-shell">
      <div className="tab-strip" role="tablist" aria-label="Model selector">
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
              <span className="tab-eyebrow">MODEL {modelId}</span>
              <strong>{item.name}</strong>
              <small>{item.subtitle}</small>
              <div className="tab-button-stats" aria-hidden="true">
                <span className="tab-stat">open {snapshot?.openCount || 0}</span>
                <span className="tab-stat">audit {snapshot?.auditCount || 0}</span>
                <span className="tab-stat">fills {snapshot?.tradeCount || 0}</span>
              </div>
            </button>
          );
        })}
      </div>

      <SectionCard eyebrow={`MODEL ${activeModel}`} title={meta.name} meta={meta.subtitle} className="model-focus-card">
        <div className="model-focus-grid">
          <div className="model-focus-copy">
            <p>{meta.description}</p>
            <div className="status-row compact">
              <StatusBadge tone="info">open {activePositions.length}</StatusBadge>
              <StatusBadge tone="warning">latest audit {latestAuditRows.length}</StatusBadge>
              <StatusBadge tone="success">recent fills {activeTrades.length}</StatusBadge>
            </div>
          </div>

          <div className="focus-metric-grid">
            <article className="focus-metric-card">
              <span>Open positions</span>
              <strong>{activePositions.length}</strong>
            </article>
            <article className="focus-metric-card">
              <span>Latest audit cycle</span>
              <strong>{latestAuditCycleAt ? formatTs(latestAuditCycleAt) : "-"}</strong>
            </article>
            <article className="focus-metric-card">
              <span>Recent fills</span>
              <strong>{activeTrades.length}</strong>
            </article>
          </div>
        </div>
      </SectionCard>

      <SectionCard
        eyebrow="Signal audit"
        title={`${meta.name} latest audit status`}
        meta={latestAuditCycleAt ? formatTs(latestAuditCycleAt) : "no audit rows"}
      >
        <p className="panel-note">
          `filtered_symbol` means the symbol was outside the currently allowed universe. When dynamic universe is on,
          `BYBIT_SYMBOLS` is only a reference list.
        </p>
        <div className="status-row compact">
          {auditSummary.length ? (
            auditSummary.slice(0, 5).map((item) => (
              <StatusBadge key={item.status} tone={auditTone(item.status)}>
                {auditLabel(item.status)} {item.count}
              </StatusBadge>
            ))
          ) : (
            <StatusBadge tone="muted">no audit rows</StatusBadge>
          )}
        </div>
      </SectionCard>

      <section className="content-grid content-grid-two">
        <SectionCard eyebrow="Open positions" title={`${meta.name} current positions`} meta={`${activePositions.length} rows`}>
          {activePositions.length ? (
            <div className="mini-list">
              {activePositions.map((row) => (
                <article key={row.id} className="mini-card position-card">
                  <div>
                    <strong>{row.symbol}</strong>
                    <p>
                      {String(row.side || "").toUpperCase()} / {row.status}
                    </p>
                    <p className="position-secondary">opened {openedMeta(row)}</p>
                  </div>
                  <div className="mini-metrics position-metrics">
                    <span className="position-secondary">entry {entryLabel(row)}</span>
                    <span className="position-secondary">mark {currentPriceLabel(row)}</span>
                    <span className="position-secondary">TP {tpLabel(row)}</span>
                    <span className="position-secondary">SL {slLabel(row)}</span>
                    <span className="position-secondary">lev {leverageLabel(row.leverage)}</span>
                    <strong className={`position-pnl ${pnlToneClass(row.unrealized_pnl_usd)}`}>
                      {formatMoney(row.unrealized_pnl_usd)}
                    </strong>
                    <span className="position-secondary">unrealized pnl</span>
                  </div>
                </article>
              ))}
            </div>
          ) : (
            <EmptyState title="No open positions" description="This model does not currently have an open crypto position." />
          )}
        </SectionCard>

        <SectionCard eyebrow="Recent fills" title={`${meta.name} recent crypto fills`} meta={`${activeTrades.length} rows`}>
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
                    <span>lev {leverageLabel(row.leverage)}</span>
                    <span>{realizedPnlLabel(row)}</span>
                    <span className="position-secondary">realized pnl</span>
                  </div>
                </article>
              ))}
            </div>
          ) : (
            <EmptyState title="No recent fills" description="This model has not recorded a recent crypto demo fill yet." />
          )}
        </SectionCard>
      </section>

      <TablePanel eyebrow="Signal audit detail" title={`${meta.name} audit rows`} meta={`${latestAuditRows.length} latest rows`}>
        <table>
          <thead>
            <tr>
              <th>Cycle</th>
              <th>Symbol</th>
              <th>Status</th>
              <th>Reason</th>
              <th>Score / Thr</th>
              <th>Allowed</th>
              <th>Gate</th>
              <th>Ready</th>
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
                <td colSpan="8">No audit rows available.</td>
              </tr>
            )}
          </tbody>
        </table>
      </TablePanel>

      <TablePanel eyebrow="Position detail" title={`${meta.name} entry / TP / SL / PnL`} meta={`${activePositions.length} rows`}>
        <table>
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Opened</th>
              <th>Entry</th>
              <th>Current</th>
              <th>TP</th>
              <th>SL</th>
              <th>Lev</th>
              <th>Unrealized</th>
              <th>Realized</th>
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
                <td colSpan="9">No position rows available.</td>
              </tr>
            )}
          </tbody>
        </table>
      </TablePanel>

      <TablePanel eyebrow="Entry plan" title={`${meta.name} setup rows`} meta={`${activeSetups.length} rows`}>
        <table>
          <thead>
            <tr>
              <th>Cycle</th>
              <th>Side</th>
              <th>Symbol</th>
              <th>Entry</th>
              <th>Stop</th>
              <th>Target 1</th>
              <th>RR</th>
              <th>Ready</th>
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
                <td colSpan="8">No setup rows available.</td>
              </tr>
            )}
          </tbody>
        </table>
      </TablePanel>

      <TablePanel eyebrow="Trade detail" title={`${meta.name} recent fills`} meta={`${activeTrades.length} rows`}>
        <p className="panel-note">
          Crypto fills in this build are recorded on the demo path. The `source` column should currently read
          `crypto_demo`.
        </p>
        <table>
          <thead>
            <tr>
              <th>Time</th>
              <th>Source</th>
              <th>Symbol</th>
              <th>Kind</th>
              <th>Mode</th>
              <th>Lev</th>
              <th>Price</th>
              <th>Realized PnL</th>
              <th>Realized %</th>
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
                <td colSpan="9">No trade rows available.</td>
              </tr>
            )}
          </tbody>
        </table>
      </TablePanel>
    </section>
  );
}
