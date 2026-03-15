import EmptyState from "./components/empty-state";
import MetricCard from "./components/metric-card";
import PageHeader from "./components/page-header";
import SectionCard from "./components/section-card";
import StatusBadge from "./components/status-badge";
import { loadOverviewPageData } from "../lib/dashboard-data";
import { formatMoney, formatTs } from "../lib/formatters";

export const dynamic = "force-dynamic";

export default async function HomePage() {
  const data = await loadOverviewPageData();
  const snapshot = data.snapshot;

  return (
    <>
      <PageHeader
        eyebrow="운영 개요"
        title="한눈에 보는 오늘의 운영 상태"
        description="메인 화면에서는 핵심 지표와 최근 사이클만 확인합니다. 상세한 성과, 포지션, 설정 입력은 각 전용 화면으로 분리했습니다."
        actions={[
          { href: "/models", label: "모델 성과 보기", tone: "primary" },
          { href: "/positions", label: "포지션 보기", tone: "ghost" },
        ]}
      />

      {!data.ready ? (
        <section className="warning-card">
          <strong>Supabase 연결 상태를 먼저 확인해 주세요.</strong>
          {data.errors.map((msg) => (
            <p key={msg}>{msg}</p>
          ))}
        </section>
      ) : null}

      <section className="kpi-row">
        <MetricCard
          label="엔진 하트비트"
          value={snapshot?.heartbeat ? formatTs(snapshot.heartbeat.last_seen_at) : "데이터 없음"}
          meta={snapshot?.heartbeat?.engine_name || "엔진 오프라인"}
          tone="cyan"
        />
        <MetricCard
          label="최근 실현 PnL"
          value={formatMoney(snapshot?.totalRealizedUsd || 0)}
          meta={`최신 ${data.dailyRows.length}개 일자 기준`}
          tone="green"
        />
        <MetricCard
          label="집계된 거래 수"
          value={String(snapshot?.totalClosedTrades || 0)}
          meta={`기준 일자 ${snapshot?.latestPnlDay || "-"}`}
          tone="amber"
        />
        <MetricCard
          label="오픈 포지션"
          value={String(snapshot?.openPositionCount || 0)}
          meta={`최근 신호 ${snapshot?.latestSignalCount || 0}건`}
        />
      </section>

      <section className="content-grid content-grid-two">
        <SectionCard
          eyebrow="사이클 상태"
          title="최근 사이클 요약"
          meta={snapshot?.latestCycleAt ? formatTs(snapshot.latestCycleAt) : "대기 중"}
        >
          <div className="status-row">
            <StatusBadge tone={snapshot?.heartbeat ? "success" : "muted"}>
              {snapshot?.heartbeat ? "엔진 연결됨" : "엔진 미확인"}
            </StatusBadge>
            <StatusBadge tone={snapshot?.latestSignalCount ? "info" : "muted"}>
              최근 신호 {snapshot?.latestSignalCount || 0}건
            </StatusBadge>
            <StatusBadge tone={snapshot?.openPositionCount ? "warning" : "success"}>
              오픈 포지션 {snapshot?.openPositionCount || 0}
            </StatusBadge>
          </div>

          {data.recentSetups.length ? (
            <div className="mini-list">
              {data.recentSetups.slice(0, 5).map((row) => (
                <article key={row.id} className="mini-card">
                  <div>
                    <strong>{row.symbol}</strong>
                    <p>
                      {row.model_id} / {formatTs(row.cycle_at)}
                    </p>
                  </div>
                  <div className="mini-metrics">
                    <span>{formatMoney(row.entry_price)}</span>
                    <span>{row.entry_ready ? "진입 준비" : "대기"}</span>
                  </div>
                </article>
              ))}
            </div>
          ) : (
            <EmptyState
              title="최근 신호가 없습니다"
              description="아직 Supabase에 setup 데이터가 들어오지 않았습니다."
            />
          )}
        </SectionCard>

        <SectionCard eyebrow="빠른 이동" title="역할별 화면 바로가기" meta="한 화면 한 역할">
          <div className="quick-link-grid">
            <a href="/models" className="quick-link-card">
              <strong>모델 성과</strong>
              <p>모델별 PnL, 승률, autotune 상태를 따로 확인합니다.</p>
            </a>
            <a href="/positions" className="quick-link-card">
              <strong>포지션</strong>
              <p>오픈 포지션과 최신 setup, entry / SL / TP를 점검합니다.</p>
            </a>
            <a href="/settings" className="quick-link-card">
              <strong>설정</strong>
              <p>Provider vault, execution target, runtime profile은 여기서만 다룹니다.</p>
            </a>
          </div>
        </SectionCard>
      </section>
    </>
  );
}
