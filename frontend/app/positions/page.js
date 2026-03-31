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
        eyebrow="포지션"
        title="포지션 상태 및 감시 추적"
        description="포지션 최근 체결, 최신 신호 감사 로그를 확인하고 진입/청산 사유를 빠르게 파악합니다."
        actions={[
          { href: "/settings", label: "설정 보기", tone: "primary" },
          { href: "/models", label: "모델 성과", tone: "ghost" },
        ]}
      />

      {!data.ready ? (
        <section className="warning-card">
          <strong>포지션 데이터를 불러오지 못했습니다.</strong>
          {data.errors.map((msg) => (
            <p key={msg}>{msg}</p>
          ))}
        </section>
      ) : null}

      <section className="kpi-row">
        <MetricCard
          label="엔진 하트비트"
          value={data.heartbeat ? formatTs(data.heartbeat.last_seen_at) : "-"}
          meta={data.heartbeat?.engine_name || "엔진 스냅샷"}
          tone="cyan"
          icon={Activity}
        />
        <MetricCard
          label="오픈 포지션"
          value={String(snapshot?.openPositionCount || 0)}
          meta="전체 운용 모델"
          tone="amber"
          icon={Wallet}
        />
        <MetricCard
          label="최근 신호 감사"
          value={snapshot?.latestSignalAuditCycleAt ? formatTs(snapshot.latestSignalAuditCycleAt) : "-"}
          meta={`총 ${snapshot?.latestSignalAuditCount || 0}건`}
          tone="green"
          icon={SearchCheck}
        />
        <MetricCard
          label="최근 체결"
          value={String(snapshot?.recentTradeCount || 0)}
          meta="거래 경로 체결 로그"
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
