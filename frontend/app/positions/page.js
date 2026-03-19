import { Activity, Crosshair, SearchCheck, Wallet } from "lucide-react";
import MetricCard from "../components/metric-card";
import PageHeader from "../components/page-header";
import PositionsTabs from "../components/positions-tabs";
import { loadPositionsPageData } from "../../lib/dashboard-data";
import { formatTs } from "../../lib/formatters";

export const dynamic = "force-dynamic";

export default async function PositionsPage() {
  const data = await loadPositionsPageData();
  const snapshot = data.snapshot;

  return (
    <>
      <PageHeader
        eyebrow="Positions"
        title="Execution State And Audit Trail"
        description="Review open positions, recent crypto fills, and the latest signal-audit rows that explain why a symbol was accepted or filtered."
        actions={[
          { href: "/settings", label: "Open Settings", tone: "primary" },
          { href: "/models", label: "Model Performance", tone: "ghost" },
        ]}
      />

      {!data.ready ? (
        <section className="warning-card">
          <strong>Could not load position data.</strong>
          {data.errors.map((msg) => (
            <p key={msg}>{msg}</p>
          ))}
        </section>
      ) : null}

      <section className="kpi-row">
        <MetricCard
          label="Engine heartbeat"
          value={data.heartbeat ? formatTs(data.heartbeat.last_seen_at) : "-"}
          meta={data.heartbeat?.engine_name || "engine offline"}
          tone="cyan"
          icon={Activity}
        />
        <MetricCard
          label="Open positions"
          value={String(snapshot?.openPositionCount || 0)}
          meta="all crypto models"
          tone="amber"
          icon={Wallet}
        />
        <MetricCard
          label="Latest signal audit"
          value={snapshot?.latestSignalAuditCycleAt ? formatTs(snapshot.latestSignalAuditCycleAt) : "-"}
          meta={`rows ${snapshot?.latestSignalAuditCount || 0}`}
          tone="green"
          icon={SearchCheck}
        />
        <MetricCard
          label="Recent crypto fills"
          value={String(snapshot?.recentTradeCount || 0)}
          meta="demo path trade log"
          icon={Crosshair}
        />
      </section>

      <PositionsTabs
        openPositions={data.openPositions}
        setupRows={data.setupRows}
        signalAuditRows={data.signalAuditRows}
        recentTradeRows={data.recentTradeRows}
      />
    </>
  );
}
