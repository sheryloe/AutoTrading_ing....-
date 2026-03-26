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
        eyebrow="개요"
        title="AI_Auto 운영 대시보드"
        description="핵심 지표를 우선 보여주는 개요 화면입니다. 상세 분석은 모델/포지션 페이지로 분리했습니다."
        actions={[
          { href: "/models", label: "모델 성과", tone: "primary" },
          { href: "/positions", label: "실행 추적", tone: "ghost" },
          { href: "/settings", label: "런타임 설정", tone: "ghost" },
        ]}
      />

      {!data.ready ? (
        <section className="warning-card">
          <strong>Supabase에서 개요 데이터를 불러오지 못했습니다.</strong>
          {data.errors.map((msg) => (
            <p key={msg}>{msg}</p>
          ))}
        </section>
      ) : null}

      <section className="overview-hero">
        <div className="hero-panel">
          <div className="hero-head">
            <span className="hero-chip">변동성 프로필</span>
            <StatusBadge tone={snapshot?.heartbeat ? "success" : "muted"}>
              {snapshot?.heartbeat ? "엔진 연결됨" : "엔진 오프라인"}
            </StatusBadge>
          </div>

          <h2>고변동성 단타 프로필 운영 중</h2>
          <p>
            엔진이 데모 시드 기준으로 A/B/C/D 전략을 병렬 추적합니다. 이 화면은 상태 확인 중심으로 간결하게 유지했습니다.
          </p>

          <div className="hero-metric-grid">
            <div className="hero-metric">
              <span>최근 사이클</span>
              <strong>{snapshot?.latestCycleAt ? formatTs(snapshot.latestCycleAt) : "-"}</strong>
              <small>신호 {snapshot?.latestSignalCount || 0}건</small>
            </div>
            <div className="hero-metric">
              <span>누적 실현 PnL</span>
              <strong>{formatMoney(snapshot?.totalRealizedUsd || 0)}</strong>
              <small>최근 기준일 {snapshot?.latestPnlDay || "-"}</small>
            </div>
            <div className="hero-metric">
              <span>오픈 포지션</span>
              <strong>{snapshot?.openPositionCount || 0}</strong>
              <small>종료 거래 {snapshot?.totalClosedTrades || 0}건</small>
            </div>
          </div>

          <div className="hero-actions">
            <a className="hero-action" href="/models">
              모델 보드 열기
            </a>
            <a className="hero-action ghost" href="/positions">
              실행 추적 열기
            </a>
          </div>
        </div>

        <div className="hero-panel hero-panel-alt">
          <div className="hero-tape">
            <div>
              <span>하트비트</span>
              <strong>{snapshot?.heartbeat ? formatTs(snapshot.heartbeat.last_seen_at) : "-"}</strong>
              <small>{snapshot?.heartbeat?.engine_name || "엔진"}</small>
            </div>
            <div>
              <span>실행 타깃</span>
              <strong>paper</strong>
              <small>live arm 꺼짐</small>
            </div>
            <div>
              <span>최근 신호</span>
              <strong>{snapshot?.latestSignalCount || 0}</strong>
              <small>동일 사이클 행 수</small>
            </div>
          </div>

          <div className="hero-mini-list">
            {recentPositions.length ? (
              recentPositions.map((row) => (
                <article key={row.id} className="hero-mini-card">
                  <div>
                    <strong>{row.symbol}</strong>
                    <p>
                      {String(row.side || "").toUpperCase()} 진입가 {entryLabel(row)}
                    </p>
                  </div>
                  <div className="hero-mini-metrics">
                    <span>현재가 {currentPriceLabel(row)}</span>
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
                <span>현재 사이클에 오픈 포지션이 없습니다.</span>
              </div>
            )}
          </div>
        </div>
      </section>

      <section className="kpi-row">
        <MetricCard
          label="엔진 하트비트"
          value={snapshot?.heartbeat ? formatTs(snapshot.heartbeat.last_seen_at) : "데이터 없음"}
          meta={snapshot?.heartbeat?.engine_name || "엔진"}
          tone="cyan"
          icon={Activity}
        />
        <MetricCard
          label="실현 PnL"
          value={formatMoney(snapshot?.totalRealizedUsd || 0)}
          meta={`최근 기준일 ${snapshot?.latestPnlDay || "-"}`}
          tone="green"
          icon={TrendingUp}
        />
        <MetricCard
          label="종료 거래"
          value={String(snapshot?.totalClosedTrades || 0)}
          meta={`신호 ${snapshot?.latestSignalCount || 0}건`}
          tone="amber"
          icon={Crosshair}
        />
        <MetricCard
          label="오픈 포지션"
          value={String(snapshot?.openPositionCount || 0)}
          meta="현재 보유 상태"
          icon={Wallet}
        />
      </section>

      <section className="model-pulse">
        <div className="model-pulse-head">
          <div>
            <span className="section-eyebrow">모델 펄스</span>
            <h3 className="section-title">A/B/C/D 스냅샷</h3>
          </div>
          <p className="section-meta">모델별 최근 성과와 현재 오픈 포지션만 빠르게 확인합니다.</p>
        </div>

        <div className="model-pulse-grid">
          {boards.map(({ modelId, meta, latest, positions }) => (
            <article key={modelId} className={`model-pulse-card ${modelTone(modelId)}`}>
              <div className="model-pulse-title">
                <div>
                  <span>{`모델 ${modelId}`}</span>
                  <strong>{meta.name}</strong>
                  <p>{meta.subtitle}</p>
                </div>
                <StatusBadge tone={positions.length ? "warning" : "muted"}>
                  {positions.length ? `오픈 ${positions.length}` : "대기"}
                </StatusBadge>
              </div>

              <div className="model-pulse-metrics">
                <div>
                  <label>실현 pnl</label>
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
                <span>활성 심볼</span>
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
