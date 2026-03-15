import EmptyState from "../components/empty-state";
import MetricCard from "../components/metric-card";
import PageHeader from "../components/page-header";
import SectionCard from "../components/section-card";
import StatusBadge from "../components/status-badge";
import TablePanel from "../components/table-panel";
import { loadPositionsPageData } from "../../lib/dashboard-data";
import { formatMoney, formatTs } from "../../lib/formatters";

export const dynamic = "force-dynamic";

export default async function PositionsPage() {
  const data = await loadPositionsPageData();
  const snapshot = data.snapshot;

  return (
    <>
      <PageHeader
        eyebrow="포지션"
        title="실행 상태와 최신 진입 계획"
        description="이 화면은 실제 운영에 필요한 실행 데이터만 모읍니다. 오픈 포지션, 최신 setup, entry / stop loss / target price 흐름을 한곳에서 확인할 수 있습니다."
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
        <MetricCard label="오픈 포지션" value={String(snapshot?.openPositionCount || 0)} meta="현재 status=open 기준" tone="amber" />
        <MetricCard
          label="최근 사이클"
          value={snapshot?.latestCycleAt ? formatTs(snapshot.latestCycleAt) : "대기 중"}
          meta={`최근 신호 ${snapshot?.latestSignalCount || 0}건`}
          tone="green"
        />
        <MetricCard label="최근 setup 수" value={String(data.setupRows.length)} meta="최신 계획 데이터 기준" />
      </section>

      <section className="content-grid content-grid-two">
        <SectionCard eyebrow="오픈 포지션" title="현재 노출 상태" meta={`${data.openPositions.length}개`}>
          {data.openPositions.length ? (
            <div className="mini-list">
              {data.openPositions.map((row) => (
                <article key={row.id} className="mini-card">
                  <div>
                    <strong>{row.symbol}</strong>
                    <p>
                      {row.model_id} / {row.side} / {row.status}
                    </p>
                  </div>
                  <div className="mini-metrics">
                    <span>{formatMoney(row.actual_entry_price || row.planned_entry_price)}</span>
                    <span>{formatMoney(row.realized_pnl_usd)}</span>
                  </div>
                </article>
              ))}
            </div>
          ) : (
            <EmptyState title="오픈 포지션이 없습니다" description="현재 열려 있는 포지션이 없거나 아직 데이터가 쌓이지 않았습니다." />
          )}
        </SectionCard>

        <SectionCard
          eyebrow="최근 setup"
          title="진입 계획 요약"
          meta={snapshot?.latestCycleAt ? formatTs(snapshot.latestCycleAt) : "대기 중"}
        >
          {data.setupRows.length ? (
            <div className="mini-list">
              {data.setupRows.slice(0, 5).map((row) => (
                <article key={row.id} className="mini-card">
                  <div>
                    <strong>{row.symbol}</strong>
                    <p>
                      {row.model_id} / RR {Number(row.risk_reward || 0).toFixed(2)}
                    </p>
                  </div>
                  <div className="mini-metrics">
                    <span>{formatMoney(row.entry_price)}</span>
                    <StatusBadge tone={row.entry_ready ? "success" : "muted"}>
                      {row.entry_ready ? "진입 가능" : "대기"}
                    </StatusBadge>
                  </div>
                </article>
              ))}
            </div>
          ) : (
            <EmptyState title="최근 setup이 없습니다" description="model_setups가 아직 비어 있습니다." />
          )}
        </SectionCard>
      </section>

      <TablePanel eyebrow="최신 진입 계획" title="Entry / SL / TP 테이블" meta={`${data.setupRows.length}건`}>
        <table>
          <thead>
            <tr>
              <th>사이클</th>
              <th>심볼</th>
              <th>모델</th>
              <th>엔트리</th>
              <th>손절</th>
              <th>1차 목표</th>
              <th>RR</th>
              <th>상태</th>
            </tr>
          </thead>
          <tbody>
            {data.setupRows.length ? (
              data.setupRows.map((row) => (
                <tr key={row.id}>
                  <td>{formatTs(row.cycle_at)}</td>
                  <td>{row.symbol}</td>
                  <td>{row.model_id}</td>
                  <td>{formatMoney(row.entry_price)}</td>
                  <td>{formatMoney(row.stop_loss_price)}</td>
                  <td>{formatMoney(row.target_price_1)}</td>
                  <td>{Number(row.risk_reward || 0).toFixed(2)}</td>
                  <td>{row.entry_ready ? "진입 가능" : "대기"}</td>
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan="8">데이터가 없습니다.</td>
              </tr>
            )}
          </tbody>
        </table>
      </TablePanel>
    </>
  );
}
