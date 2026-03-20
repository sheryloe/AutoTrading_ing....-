import ControlConsole from "../components/control-console";
import MetricCard from "../components/metric-card";
import PageHeader from "../components/page-header";
import { loadServiceControlData } from "../../lib/service-control";

export const dynamic = "force-dynamic";

function symbolModeValue(diagnostics) {
  if (diagnostics?.universeMode) return String(diagnostics.universeMode);
  return diagnostics?.dynamicUniverseEnabled ? "dynamic" : "fixed_symbols";
}

function symbolMeta(diagnostics) {
  const symbols = diagnostics?.configuredSymbols || [];
  if (!symbols.length) return "no configured symbols";
  const preview = symbols.slice(0, 3).join(", ");
  return symbols.length > 3 ? `${preview} +${symbols.length - 3}` : preview;
}

function toNumber(value, fallback = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function formatPct(value) {
  const numeric = toNumber(value, Number.NaN);
  if (!Number.isFinite(numeric)) return "-";
  return `${(numeric * 100).toFixed(1)}%`;
}

function freeTierStatusValue(report) {
  if (!report || typeof report !== "object") return "n/a";
  return report.pass ? "pass" : "fail";
}

function freeTierUsageValue(report) {
  if (!report || typeof report !== "object") return "-";
  return String(toNumber(report.cycles_per_day, 0));
}

function freeTierUsageMeta(report) {
  if (!report || typeof report !== "object") return "no report yet";
  return `scan ${toNumber(report.scan_interval_seconds, 0)}s`;
}

function freeTierHeadroomValue(report) {
  const ratio = report?.providers?.market_data?.headroom_ratio;
  return formatPct(ratio);
}

function freeTierHeadroomMeta(report) {
  const worstMetric = String(report?.providers?.market_data?.worst_metric || "-");
  return `market worst: ${worstMetric}`;
}

function freeTierBottleneckValue(report) {
  const bottlenecks = Array.isArray(report?.bottlenecks) ? report.bottlenecks : [];
  return bottlenecks.length ? bottlenecks.join(", ") : "none";
}

function freeTierBottleneckMeta(report) {
  const generatedAt = String(report?.generated_at_iso || "");
  return generatedAt ? `generated ${generatedAt}` : "report pending";
}

export default async function SettingsPage() {
  const control = await loadServiceControlData();
  const diagnostics = control.diagnostics || {};
  const freeTierReport = control.freeTierReport || null;

  return (
    <>
      <PageHeader
        eyebrow="Settings"
        title="Service Console"
        description="Manage provider vault credentials, rank-lock runtime profile, and free-tier capacity checks in one place."
        actions={[
          { href: "/", label: "Overview", tone: "ghost" },
          { href: "/positions", label: "Execution Trail", tone: "primary" },
        ]}
      />

      {control.errors?.length ? (
        <section className="warning-card">
          <strong>Could not fully load settings data.</strong>
          {control.errors.map((msg) => (
            <p key={msg}>{msg}</p>
          ))}
        </section>
      ) : null}

      <section className="kpi-row">
        <MetricCard
          label="Write status"
          value={control.writeReady ? "ready" : "read only"}
          meta="Vercel + Supabase admin env"
          tone={control.writeReady ? "green" : "amber"}
        />
        <MetricCard
          label="Execution target"
          value={String(control.runtimeConfig?.EXECUTION_TARGET || "paper")}
          meta={`armed ${control.runtimeConfig?.LIVE_EXECUTION_ARMED ? "yes" : "no"}`}
          tone="cyan"
        />
        <MetricCard
          label="Symbol mode"
          value={symbolModeValue(diagnostics)}
          meta={diagnostics?.symbolModeLabel || "-"}
          tone="amber"
        />
        <MetricCard
          label="Configured symbols"
          value={String(diagnostics?.configuredSymbolCount || 0)}
          meta={symbolMeta(diagnostics)}
        />
      </section>

      <section className="kpi-row">
        <MetricCard
          label="Free-tier status"
          value={freeTierStatusValue(freeTierReport)}
          meta={freeTierBottleneckMeta(freeTierReport)}
          tone={freeTierReport?.pass ? "green" : "amber"}
        />
        <MetricCard
          label="Usage (cycles/day)"
          value={freeTierUsageValue(freeTierReport)}
          meta={freeTierUsageMeta(freeTierReport)}
          tone="cyan"
        />
        <MetricCard
          label="Headroom"
          value={freeTierHeadroomValue(freeTierReport)}
          meta={freeTierHeadroomMeta(freeTierReport)}
          tone="amber"
        />
        <MetricCard
          label="Bottlenecks"
          value={freeTierBottleneckValue(freeTierReport)}
          meta={freeTierReport ? `universe ${freeTierReport.universe_mode || "-"}` : "no report yet"}
          tone={Array.isArray(freeTierReport?.bottlenecks) && freeTierReport.bottlenecks.length ? "amber" : "green"}
        />
      </section>

      <section className="warning-card">
        <strong>{diagnostics?.liveOrderRoutingLabel || "Demo-only crypto execution path"}</strong>
        <p>{diagnostics?.liveOrderSummary}</p>
        <p>{diagnostics?.symbolSummary}</p>
        <p>
          Runtime config source: <code>{diagnostics?.configSourceValue || "-"}</code>
        </p>
      </section>

      {freeTierReport ? (
        <section className="warning-card">
          <strong>Free-tier capacity report</strong>
          <p>
            Overall: <code>{freeTierReport.overall_status || "-"}</code>
          </p>
          <p>
            OpenAI: <code>{freeTierReport.providers?.openai?.status || "-"}</code> / Google:{" "}
            <code>{freeTierReport.providers?.google_gemini?.status || "-"}</code> / Solscan:{" "}
            <code>{freeTierReport.providers?.solscan?.status || "-"}</code> / Market data:{" "}
            <code>{freeTierReport.providers?.market_data?.status || "-"}</code>
          </p>
          <p>
            Bottlenecks: <code>{freeTierBottleneckValue(freeTierReport)}</code>
          </p>
        </section>
      ) : null}

      {diagnostics?.sourceConfigRepaired ? (
        <section className="warning-card">
          <strong>Demo data sources were auto-repaired</strong>
          {(diagnostics?.sourceWarnings || []).map((msg) => (
            <p key={msg}>{msg}</p>
          ))}
          <p>
            Effective source order: <code>{diagnostics?.sourceOrderValue || "-"}</code>
          </p>
          <p>
            Effective source flags: <code>{diagnostics?.sourceFlagSummary || "-"}</code>
          </p>
          <p>
            Effective realtime quotes: <code>{diagnostics?.realtimeSourceValue || "-"}</code>
          </p>
        </section>
      ) : null}

      <section className="warning-card">
        <strong>Reset reminder</strong>
        <p>Saving the runtime profile does not wipe the current demo seed, positions, or PnL.</p>
        <p>Use the hard reset section only when you intentionally want to restart the crypto demo state.</p>
      </section>

      <ControlConsole
        initialConfig={control.runtimeConfig}
        runtimeUpdatedAt={control.runtimeUpdatedAt}
        providerStatuses={control.providerStatuses}
        writeReady={control.writeReady}
      />
    </>
  );
}
