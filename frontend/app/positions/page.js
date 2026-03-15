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
        title="모델별 실행 상태와 체결 흐름"
        description="오픈 포지션, 최신 진입 계획, 최근 체결 로그를 모델 단위로 분리해서 봅니다. intrabar 체결과 종료도 여기서 바로 확인할 수 있습니다."
        actions={[
          { href: "/settings", label: "설정 열기", tone: "primary" },
          { href: "/models", label: "모델 성과 보기", tone: "ghost" },
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
          value={data.heartbeat ? formatTs(data.heartbeat.last_seen_at) : "데이터 없음"}
          meta={data.heartbeat?.engine_name || "엔진 미확인"}
          tone="cyan"
        />
        <MetricCard label="오픈 포지션" value={String(snapshot?.openPositionCount || 0)} meta="전체 모델 합계" tone="amber" />
        <MetricCard
          label="최신 사이클"
          value={snapshot?.latestCycleAt ? formatTs(snapshot.latestCycleAt) : "대기 중"}
          meta={`최신 신호 ${snapshot?.latestSignalCount || 0}건`}
          tone="green"
        />
        <MetricCard
          label="최근 체결 로그"
          value={String(snapshot?.recentTradeCount || 0)}
          meta="intrabar / 배치 포함"
        />
      </section>

      <PositionsTabs
        openPositions={data.openPositions}
        setupRows={data.setupRows}
        recentTradeRows={data.recentTradeRows}
      />
    </>
  );
}
