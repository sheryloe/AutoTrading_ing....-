import { getSupabaseAdmin } from "../lib/supabase-admin";
import ControlConsole from "./components/control-console";
import { loadServiceControlData } from "../lib/service-control";

export const dynamic = "force-dynamic";

function fmtMoney(value) {
  const num = Number(value || 0);
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  }).format(num);
}

function fmtPct(value) {
  return `${(Number(value || 0) * 100).toFixed(2)}%`;
}

function fmtTs(value) {
  if (!value) return "-";
  const dt = new Date(value);
  if (Number.isNaN(dt.getTime())) return String(value);
  return new Intl.DateTimeFormat("ko-KR", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(dt);
}

async function loadDashboardData() {
  const supabase = getSupabaseAdmin();
  if (!supabase) {
    return {
      ready: false,
      errors: ["Missing SUPABASE_URL and server-side secret key env vars."],
      heartbeat: null,
      daily: [],
      setups: [],
      positions: [],
      tunes: [],
    };
  }

  const [heartbeatRes, dailyRes, setupsRes, positionsRes, tunesRes] = await Promise.all([
    supabase.from("engine_heartbeat").select("*").order("last_seen_at", { ascending: false }).limit(1),
    supabase.from("daily_model_pnl").select("*").order("day", { ascending: false }).limit(8),
    supabase.from("model_setups").select("*").order("cycle_at", { ascending: false }).limit(10),
    supabase.from("positions").select("*").eq("status", "open").order("opened_at", { ascending: false }).limit(8),
    supabase.from("model_runtime_tunes").select("*").order("model_id", { ascending: true }),
  ]);

  const errors = [heartbeatRes, dailyRes, setupsRes, positionsRes, tunesRes]
    .map((res) => res.error?.message)
    .filter(Boolean);

  return {
    ready: errors.length === 0,
    errors,
    heartbeat: heartbeatRes.data?.[0] || null,
    daily: dailyRes.data || [],
    setups: setupsRes.data || [],
    positions: positionsRes.data || [],
    tunes: tunesRes.data || [],
  };
}

function StatCard({ label, value, meta, tone = "default" }) {
  return (
    <article className={`stat-card ${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      <p>{meta}</p>
    </article>
  );
}

export default async function HomePage() {
  const [data, control] = await Promise.all([loadDashboardData(), loadServiceControlData()]);
  const latestDay = data.daily[0]?.day || "-";
  const totalRealized = data.daily.reduce((sum, row) => sum + Number(row.realized_pnl_usd || 0), 0);
  const totalClosedTrades = data.daily.reduce((sum, row) => sum + Number(row.closed_trades || 0), 0);

  return (
    <main className="dashboard-shell">
      <div className="grid-backdrop" />
      <header className="hero-bar">
        <div>
          <p className="eyebrow">AI_AUTO / VERCEL FRONTEND / SUPABASE READ MODEL</p>
          <h1>Execution dashboard for planner-based crypto ops</h1>
          <p className="hero-copy">
            Top-5 majors, 10-minute setups, daily model PnL, and weekly autotune status.
            This frontend is meant for Vercel. The Python engine stays outside Vercel.
          </p>
        </div>
        <div className="hero-actions">
          <a href="#service-control">Service Control</a>
          <a href="#setup-stream">Latest Setups</a>
          <a href="#autotune-state">Autotune State</a>
        </div>
      </header>

      {!data.ready ? (
        <section className="warning-panel">
          <h2>Supabase connection not ready</h2>
          <p>Set these server-side env vars in Vercel or local `.env.local`:</p>
          <code>SUPABASE_URL</code>
          <code>SUPABASE_SECRET_KEY or SUPABASE_SERVICE_ROLE_KEY</code>
          {data.errors.map((msg) => (
            <p key={msg} className="error-line">{msg}</p>
          ))}
        </section>
      ) : null}

      {control.errors?.length ? (
        <section className="warning-panel">
          <h2>Service control partially unavailable</h2>
          {control.errors.map((msg) => (
            <p key={msg} className="error-line">{msg}</p>
          ))}
        </section>
      ) : null}

      <ControlConsole
        initialConfig={control.runtimeConfig}
        runtimeUpdatedAt={control.runtimeUpdatedAt}
        bybitStatus={control.bybitStatus}
        writeReady={control.writeReady}
      />

      <section className="stats-grid">
        <StatCard
          label="Heartbeat"
          value={data.heartbeat ? fmtTs(data.heartbeat.last_seen_at) : "No data"}
          meta={data.heartbeat?.engine_name || "engine offline"}
          tone="cyan"
        />
        <StatCard
          label="Latest PnL Day"
          value={String(latestDay)}
          meta={`${data.daily.length} row snapshots loaded`}
          tone="green"
        />
        <StatCard
          label="Realized PnL"
          value={fmtMoney(totalRealized)}
          meta={`Across ${data.daily.length} daily rows`}
          tone="amber"
        />
        <StatCard
          label="Closed Trades"
          value={String(totalClosedTrades)}
          meta="Model-level daily aggregate"
        />
      </section>

      <section className="panel-grid two-up">
        <section className="panel wide" id="setup-stream">
          <div className="panel-head">
            <div>
              <p className="eyebrow">Latest setups</p>
              <h2>Planner output stream</h2>
            </div>
            <span>{data.setups.length} rows</span>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Cycle</th>
                  <th>Symbol</th>
                  <th>Model</th>
                  <th>Entry</th>
                  <th>SL</th>
                  <th>TP1</th>
                  <th>RR</th>
                  <th>Ready</th>
                </tr>
              </thead>
              <tbody>
                {data.setups.length ? data.setups.map((row) => (
                  <tr key={row.id}>
                    <td>{fmtTs(row.cycle_at)}</td>
                    <td>{row.symbol}</td>
                    <td>{row.model_id}</td>
                    <td>{fmtMoney(row.entry_price)}</td>
                    <td>{fmtMoney(row.stop_loss_price)}</td>
                    <td>{fmtMoney(row.target_price_1)}</td>
                    <td>{Number(row.risk_reward || 0).toFixed(2)}</td>
                    <td>{row.entry_ready ? "YES" : "NO"}</td>
                  </tr>
                )) : (
                  <tr><td colSpan="8">No setup rows yet.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </section>

        <section className="panel" id="autotune-state">
          <div className="panel-head">
            <div>
              <p className="eyebrow">Open positions</p>
              <h2>Live exposure</h2>
            </div>
            <span>{data.positions.length} open</span>
          </div>
          <div className="mini-list">
            {data.positions.length ? data.positions.map((row) => (
              <article key={row.id} className="mini-card">
                <div>
                  <strong>{row.symbol}</strong>
                  <p>{row.model_id} / {row.side} / {row.status}</p>
                </div>
                <div className="mini-metrics">
                  <span>{fmtMoney(row.actual_entry_price || row.planned_entry_price)}</span>
                  <span>{fmtMoney(row.realized_pnl_usd)}</span>
                </div>
              </article>
            )) : <p className="empty-line">No open positions.</p>}
          </div>
        </section>
      </section>

      <section className="panel-grid two-up">
        <section className="panel">
          <div className="panel-head">
            <div>
              <p className="eyebrow">Daily model PnL</p>
              <h2>Snapshot table</h2>
            </div>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Day</th>
                  <th>Model</th>
                  <th>Equity</th>
                  <th>Realized</th>
                  <th>Win Rate</th>
                  <th>Closed</th>
                </tr>
              </thead>
              <tbody>
                {data.daily.length ? data.daily.map((row) => (
                  <tr key={`${row.day}-${row.model_id}`}>
                    <td>{String(row.day)}</td>
                    <td>{row.model_id}</td>
                    <td>{fmtMoney(row.equity_usd)}</td>
                    <td>{fmtMoney(row.realized_pnl_usd)}</td>
                    <td>{fmtPct(row.win_rate)}</td>
                    <td>{row.closed_trades}</td>
                  </tr>
                )) : <tr><td colSpan="6">No daily PnL rows yet.</td></tr>}
              </tbody>
            </table>
          </div>
        </section>

        <section className="panel">
          <div className="panel-head">
            <div>
              <p className="eyebrow">Autotune state</p>
              <h2>Current runtime parameters</h2>
            </div>
          </div>
          <div className="mini-list tune-list">
            {data.tunes.length ? data.tunes.map((row) => (
              <article key={row.model_id} className="mini-card tune-card">
                <div>
                  <strong>Model {row.model_id}</strong>
                  <p>{row.active_variant_id || "base variant"}</p>
                </div>
                <div className="tune-metric-grid">
                  <span>thr {Number(row.threshold || 0).toFixed(4)}</span>
                  <span>tp {Number(row.tp_mul || 0).toFixed(2)}</span>
                  <span>sl {Number(row.sl_mul || 0).toFixed(2)}</span>
                  <span>note {row.last_eval_note_code || "-"}</span>
                </div>
              </article>
            )) : <p className="empty-line">No autotune rows yet.</p>}
          </div>
        </section>
      </section>
    </main>
  );
}

