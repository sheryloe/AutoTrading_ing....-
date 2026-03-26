import { Activity, Crosshair, ShieldAlert, TrendingUp, Wallet } from "lucide-react";
import MetricCard from "./components/metric-card";
import PageHeader from "./components/page-header";
import StatusBadge from "./components/status-badge";
import { loadOverviewPageData } from "../lib/dashboard-data";
import { getModelMeta, MODEL_ORDER } from "../lib/model-meta";
import { formatMoney, formatNumber, formatPercent, formatPrice, formatTs } from "../lib/formatters";

function entryLabel(row) {
  const actual = Number(row.actual_entry_price || 0);
  if (actual > 0) return formatPrice(actual);
  const planned = Number(row.planned_entry_price || 0);
  return planned > 0 ? `${formatPrice(planned)} plan` : "-";
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

function modelTone(modelId) {
  const key = String(modelId || "").toUpperCase();
  if (key === "A") return "model-tone-a";
  if (key === "B") return "model-tone-b";
  if (key === "C") return "model-tone-c";
  return "model-tone-d";
}

function buildOverviewBoards(dailyRows = [], openPositions = []) {
  return MODEL_ORDER.map((modelId) => {
    const latest = dailyRows.find((row) => String(row.model_id || "").toUpperCase() === modelId) || null;
    const positions = openPositions.filter((row) => String(row.model_id || "").toUpperCase() === modelId);
    return {
      modelId,
      meta: getModelMeta(modelId),
      latest,
      positions,
    };
  });
}

export const dynamic = "force-dynamic";

export default async function HomePage() {
  const data = await loadOverviewPageData();
  const snapshot = data.snapshot;
  const boards = buildOverviewBoards(data.dailyRows, data.openPositions);
  const recentPositions = data.openPositions.slice(0, 3);

  return (
    <>
      <PageHeader
        eyebrow="Overview"
        title="AI_Auto Control Deck"
        description="Overview is focused on core metrics only. Detailed analytics are split into Models and Positions pages."
        actions={[
          { href: "/models", label: "Model Performance", tone: "primary" },
          { href: "/positions", label: "Execution Trail", tone: "ghost" },
          { href: "/settings", label: "Runtime Settings", tone: "ghost" },
        ]}
      />

      {!data.ready ? (
        <section className="warning-card">
          <strong>Could not load overview data from Supabase.</strong>
          {data.errors.map((msg) => (
            <p key={msg}>{msg}</p>
          ))}
        </section>
      ) : null}

      <section className="overview-hero">
        <div className="hero-panel">
          <div className="hero-head">
            <span className="hero-chip">VOLATILITY PROFILE</span>
            <StatusBadge tone={snapshot?.heartbeat ? "success" : "muted"}>
              {snapshot?.heartbeat ? "engine online" : "engine offline"}
            </StatusBadge>
          </div>

          <h2>High-Volatility Scalping Profile Active</h2>
          <p>
            The engine tracks A/B/C/D strategies in parallel with demo seed capital. This page is intentionally compact and status-first.
          </p>

          <div className="hero-metric-grid">
            <div className="hero-metric">
              <span>Latest Cycle</span>
              <strong>{snapshot?.latestCycleAt ? formatTs(snapshot.latestCycleAt) : "-"}</strong>
              <small>signals {snapshot?.latestSignalCount || 0}</small>
            </div>
            <div className="hero-metric">
              <span>Cumulative Realized PnL</span>
              <strong>{formatMoney(snapshot?.totalRealizedUsd || 0)}</strong>
              <small>latest day {snapshot?.latestPnlDay || "-"}</small>
            </div>
            <div className="hero-metric">
              <span>Open Positions</span>
              <strong>{snapshot?.openPositionCount || 0}</strong>
              <small>closed trades {snapshot?.totalClosedTrades || 0}</small>
            </div>
          </div>

          <div className="hero-actions">
            <a className="hero-action" href="/models">
              open model board
            </a>
            <a className="hero-action ghost" href="/positions">
              open execution trail
            </a>
          </div>
        </div>

        <div className="hero-panel hero-panel-alt">
          <div className="hero-tape">
            <div>
              <span>Heartbeat</span>
              <strong>{snapshot?.heartbeat ? formatTs(snapshot.heartbeat.last_seen_at) : "-"}</strong>
              <small>{snapshot?.heartbeat?.engine_name || "engine"}</small>
            </div>
            <div>
              <span>Target</span>
              <strong>paper</strong>
              <small>live arm off</small>
            </div>
            <div>
              <span>Latest Signals</span>
              <strong>{snapshot?.latestSignalCount || 0}</strong>
              <small>same cycle rows</small>
            </div>
          </div>

          <div className="hero-mini-list">
            {recentPositions.length ? (
              recentPositions.map((row) => (
                <article key={row.id} className="hero-mini-card">
                  <div>
                    <strong>{row.symbol}</strong>
                    <p>
                      {String(row.side || "").toUpperCase()} at {entryLabel(row)}
                    </p>
                  </div>
                  <div className="hero-mini-metrics">
                    <span>mark {currentPriceLabel(row)}</span>
                    <strong className={`position-pnl ${pnlToneClass(row.unrealized_pnl_usd)}`}>
                      {formatMoney(row.unrealized_pnl_usd)}
                    </strong>
                    <small>lev {leverageLabel(row.leverage)}</small>
                  </div>
                </article>
              ))
            ) : (
              <div className="hero-empty">
                <ShieldAlert size={16} />
                <span>No open positions in this cycle.</span>
              </div>
            )}
          </div>
        </div>
      </section>

      <section className="kpi-row">
        <MetricCard
          label="Engine Heartbeat"
          value={snapshot?.heartbeat ? formatTs(snapshot.heartbeat.last_seen_at) : "no heartbeat"}
          meta={snapshot?.heartbeat?.engine_name || "engine"}
          tone="cyan"
          icon={Activity}
        />
        <MetricCard
          label="Realized PnL"
          value={formatMoney(snapshot?.totalRealizedUsd || 0)}
          meta={`latest day ${snapshot?.latestPnlDay || "-"}`}
          tone="green"
          icon={TrendingUp}
        />
        <MetricCard
          label="Closed Trades"
          value={String(snapshot?.totalClosedTrades || 0)}
          meta={`signals ${snapshot?.latestSignalCount || 0}`}
          tone="amber"
          icon={Crosshair}
        />
        <MetricCard
          label="Open Positions"
          value={String(snapshot?.openPositionCount || 0)}
          meta="current holdings"
          icon={Wallet}
        />
      </section>

      <section className="model-pulse">
        <div className="model-pulse-head">
          <div>
            <span className="section-eyebrow">Model Pulse</span>
            <h3 className="section-title">A/B/C/D Snapshot</h3>
          </div>
          <p className="section-meta">Shows recent performance and current open positions for each model.</p>
        </div>

        <div className="model-pulse-grid">
          {boards.map(({ modelId, meta, latest, positions }) => (
            <article key={modelId} className={`model-pulse-card ${modelTone(modelId)}`}>
              <div className="model-pulse-title">
                <div>
                  <span>{`MODEL ${modelId}`}</span>
                  <strong>{meta.name}</strong>
                  <p>{meta.subtitle}</p>
                </div>
                <StatusBadge tone={positions.length ? "warning" : "muted"}>
                  {positions.length ? `open ${positions.length}` : "idle"}
                </StatusBadge>
              </div>

              <div className="model-pulse-metrics">
                <div>
                  <label>realized pnl</label>
                  <strong>{formatMoney(latest?.realized_pnl_usd || 0)}</strong>
                </div>
                <div>
                  <label>win rate</label>
                  <strong>{formatPercent(latest?.win_rate || 0)}</strong>
                </div>
                <div>
                  <label>closed trades</label>
                  <strong>{latest?.closed_trades || 0}</strong>
                </div>
              </div>

              <div className="model-pulse-meta">
                <span>active symbol</span>
                <strong>{positions[0]?.symbol || "-"}</strong>
                <small>{meta.description}</small>
              </div>

              <a className="model-pulse-link" href="/models">
                view details
              </a>
            </article>
          ))}
        </div>
      </section>
    </>
  );
}
