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

function buildOverviewSnapshotByModel(dailyRows = [], openPositions = []) {
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

function modelTone(modelId) {
  const key = String(modelId || "").toUpperCase();
  if (key === "A") return "model-tone-a";
  if (key === "B") return "model-tone-b";
  if (key === "C") return "model-tone-c";
  return "model-tone-d";
}

export const dynamic = "force-dynamic";

export default async function HomePage() {
  const data = await loadOverviewPageData();
  const snapshot = data.snapshot;
  const boards = buildOverviewSnapshotByModel(data.dailyRows, data.openPositions);
  const recentPositions = data.openPositions.slice(0, 3);

  return (
    <>
      <PageHeader
        eyebrow="Overview"
        title="AI_Auto Control Deck"
        description="변동성 대응형 크립토 데모를 한 화면에서 확인합니다. 핵심 지표와 모델 상태만 간결하게 표시합니다."
        actions={[
          { href: "/models", label: "모델 성과", tone: "primary" },
          { href: "/positions", label: "포지션", tone: "ghost" },
          { href: "/settings", label: "설정", tone: "ghost" },
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

      <section className="overview-hero">
        <div className="hero-panel">
          <div className="hero-head">
            <span className="hero-chip">VOLATILITY MODE</span>
            <StatusBadge tone={snapshot?.heartbeat ? "success" : "muted"}>
              {snapshot?.heartbeat ? "엔진 연결됨" : "엔진 미연결"}
            </StatusBadge>
          </div>
          <h2>변동성 집중형 데모 운용 중</h2>
          <p>시드 리셋 후 공격적 튜닝을 적용했습니다. 변동성 구간에서 체결 확률을 높이는 방향입니다.</p>
          <div className="hero-metric-grid">
            <div className="hero-metric">
              <span>최근 사이클</span>
              <strong>{snapshot?.latestCycleAt ? formatTs(snapshot.latestCycleAt) : "-"}</strong>
              <small>신호 {snapshot?.latestSignalCount || 0}건</small>
            </div>
            <div className="hero-metric">
              <span>누적 실현 PnL</span>
              <strong>{formatMoney(snapshot?.totalRealizedUsd || 0)}</strong>
              <small>최근 {data.dailyRows.length}일 기준</small>
            </div>
            <div className="hero-metric">
              <span>진행 중 포지션</span>
              <strong>{snapshot?.openPositionCount || 0}건</strong>
              <small>닫힌 거래 {snapshot?.totalClosedTrades || 0}건</small>
            </div>
          </div>
          <div className="hero-actions">
            <a className="hero-action" href="/models">
              모델 보드 열기
            </a>
            <a className="hero-action ghost" href="/positions">
              포지션 트레일
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
              <span>실행 목표</span>
              <strong>paper</strong>
              <small>live arm: off</small>
            </div>
            <div>
              <span>최근 신호</span>
              <strong>{snapshot?.latestSignalCount || 0}건</strong>
              <small>사이클 기준</small>
            </div>
          </div>
          <div className="hero-mini-list">
            {recentPositions.length ? (
              recentPositions.map((row) => (
                <article key={row.id} className="hero-mini-card">
                  <div>
                    <strong>{row.symbol}</strong>
                    <p>{String(row.side || "").toUpperCase()} · {entryLabel(row)}</p>
                  </div>
                  <div className="hero-mini-metrics">
                    <span>현재 {currentPriceLabel(row)}</span>
                    <strong className={`position-pnl ${pnlToneClass(row.unrealized_pnl_usd)}`}>
                      {formatMoney(row.unrealized_pnl_usd)}
                    </strong>
                    <small>레버리지 {leverageLabel(row.leverage)}</small>
                  </div>
                </article>
              ))
            ) : (
              <div className="hero-empty">
                <ShieldAlert size={16} />
                <span>진행 중 포지션이 없습니다.</span>
              </div>
            )}
          </div>
        </div>
      </section>

      <section className="kpi-row">
        <MetricCard
          label="마지막 하트비트"
          value={snapshot?.heartbeat ? formatTs(snapshot.heartbeat.last_seen_at) : "데이터 없음"}
          meta={snapshot?.heartbeat?.engine_name || "엔진"}
          tone="cyan"
          icon={Activity}
        />
        <MetricCard
          label="누적 실현 PnL"
          value={formatMoney(snapshot?.totalRealizedUsd || 0)}
          meta={`기준일 ${snapshot?.latestPnlDay || "-"}`}
          tone="green"
          icon={TrendingUp}
        />
        <MetricCard
          label="완료 거래"
          value={String(snapshot?.totalClosedTrades || 0)}
          meta={`신호 ${snapshot?.latestSignalCount || 0}건`}
          tone="amber"
          icon={Crosshair}
        />
        <MetricCard
          label="오픈 포지션"
          value={String(snapshot?.openPositionCount || 0)}
          meta="현재 보유 중"
          icon={Wallet}
        />
      </section>

      <section className="model-pulse">
        <div className="model-pulse-head">
          <div>
            <span className="section-eyebrow">Model Pulse</span>
            <h3 className="section-title">A/B/C/D 요약</h3>
          </div>
          <p className="section-meta">모델별 최근 성과와 오픈 포지션 수만 간단히 표시합니다.</p>
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
                  {positions.length ? `${positions.length}개 오픈` : "대기"}
                </StatusBadge>
              </div>
              <div className="model-pulse-metrics">
                <div>
                  <label>최근 실현 PnL</label>
                  <strong>{formatMoney(latest?.realized_pnl_usd || 0)}</strong>
                </div>
                <div>
                  <label>승률</label>
                  <strong>{formatPercent(latest?.win_rate || 0)}</strong>
                </div>
                <div>
                  <label>종료 거래</label>
                  <strong>{latest?.closed_trades || 0}</strong>
                </div>
              </div>
              <div className="model-pulse-meta">
                <span>포커스 심볼</span>
                <strong>{positions[0]?.symbol || "-"}</strong>
                <small>{meta.description}</small>
              </div>
              <a className="model-pulse-link" href="/models">
                상세 보기
              </a>
            </article>
          ))}
        </div>
      </section>
    </>
  );
}
