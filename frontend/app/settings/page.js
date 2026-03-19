import ControlConsole from "../components/control-console";
import MetricCard from "../components/metric-card";
import PageHeader from "../components/page-header";
import { loadServiceControlData } from "../../lib/service-control";

export const dynamic = "force-dynamic";

function symbolModeValue(diagnostics) {
  return diagnostics?.dynamicUniverseEnabled ? "dynamic" : "fixed";
}

function symbolMeta(diagnostics) {
  const symbols = diagnostics?.configuredSymbols || [];
  if (!symbols.length) return "no configured symbols";
  const preview = symbols.slice(0, 3).join(", ");
  return symbols.length > 3 ? `${preview} +${symbols.length - 3}` : preview;
}

export default async function SettingsPage() {
  const control = await loadServiceControlData();
  const diagnostics = control.diagnostics || {};

  return (
    <>
      <PageHeader
        eyebrow="Settings"
        title="Service Console"
        description="Manage provider vault credentials and the runtime profile in one place. This page now also explains why new symbols may still not produce real Bybit fills."
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

      <section className="warning-card">
        <strong>{diagnostics?.liveOrderRoutingLabel || "Demo-only crypto execution path"}</strong>
        <p>{diagnostics?.liveOrderSummary}</p>
        <p>{diagnostics?.symbolSummary}</p>
        <p>
          Runtime config source: <code>{diagnostics?.configSourceValue || "-"}</code>
        </p>
      </section>

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
