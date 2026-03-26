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
  if (!symbols.length) return "설정된 심볼 없음";
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
  return report.pass ? "정상" : "초과";
}

function freeTierUsageValue(report) {
  if (!report || typeof report !== "object") return "-";
  return String(toNumber(report.cycles_per_day, 0));
}

function freeTierUsageMeta(report) {
  if (!report || typeof report !== "object") return "리포트 없음";
  return `스캔 ${toNumber(report.scan_interval_seconds, 0)}초`;
}

function freeTierHeadroomValue(report) {
  const ratio = report?.providers?.market_data?.headroom_ratio;
  return formatPct(ratio);
}

function freeTierHeadroomMeta(report) {
  const worstMetric = String(report?.providers?.market_data?.worst_metric || "-");
  return `시장 병목: ${worstMetric}`;
}

function freeTierBottleneckValue(report) {
  const bottlenecks = Array.isArray(report?.bottlenecks) ? report.bottlenecks : [];
  return bottlenecks.length ? bottlenecks.join(", ") : "없음";
}

function freeTierBottleneckMeta(report) {
  const generatedAt = String(report?.generated_at_iso || "");
  return generatedAt ? `생성시각 ${generatedAt}` : "리포트 대기";
}

export default async function SettingsPage() {
  const control = await loadServiceControlData();
  const diagnostics = control.diagnostics || {};
  const freeTierReport = control.freeTierReport || null;

  return (
    <>
      <PageHeader
        eyebrow="설정"
        title="서비스 콘솔"
        description="프로바이더 자격정보, 랭크락 런타임 프로필, 무료티어 용량 상태를 한곳에서 관리합니다."
        actions={[
          { href: "/", label: "개요", tone: "ghost" },
          { href: "/positions", label: "실행 추적", tone: "primary" },
        ]}
      />

      {control.errors?.length ? (
        <section className="warning-card">
          <strong>설정 데이터를 완전히 불러오지 못했습니다.</strong>
          {control.errors.map((msg) => (
            <p key={msg}>{msg}</p>
          ))}
        </section>
      ) : null}

      <section className="kpi-row">
        <MetricCard
          label="쓰기 상태"
          value={control.writeReady ? "준비됨" : "읽기 전용"}
          meta="Vercel + Supabase 관리자 환경"
          tone={control.writeReady ? "green" : "amber"}
        />
        <MetricCard
          label="실행 타깃"
          value={String(control.runtimeConfig?.EXECUTION_TARGET || "paper")}
          meta={`arm ${control.runtimeConfig?.LIVE_EXECUTION_ARMED ? "예" : "아니오"}`}
          tone="cyan"
        />
        <MetricCard
          label="심볼 모드"
          value={symbolModeValue(diagnostics)}
          meta={diagnostics?.symbolModeLabel || "-"}
          tone="amber"
        />
        <MetricCard
          label="설정 심볼 수"
          value={String(diagnostics?.configuredSymbolCount || 0)}
          meta={symbolMeta(diagnostics)}
        />
      </section>

      <section className="kpi-row">
        <MetricCard
          label="무료티어 상태"
          value={freeTierStatusValue(freeTierReport)}
          meta={freeTierBottleneckMeta(freeTierReport)}
          tone={freeTierReport?.pass ? "green" : "amber"}
        />
        <MetricCard
          label="사용량 (사이클/일)"
          value={freeTierUsageValue(freeTierReport)}
          meta={freeTierUsageMeta(freeTierReport)}
          tone="cyan"
        />
        <MetricCard
          label="헤드룸"
          value={freeTierHeadroomValue(freeTierReport)}
          meta={freeTierHeadroomMeta(freeTierReport)}
          tone="amber"
        />
        <MetricCard
          label="병목"
          value={freeTierBottleneckValue(freeTierReport)}
          meta={freeTierReport ? `유니버스 ${freeTierReport.universe_mode || "-"}` : "리포트 없음"}
          tone={Array.isArray(freeTierReport?.bottlenecks) && freeTierReport.bottlenecks.length ? "amber" : "green"}
        />
      </section>

      <section className="warning-card">
        <strong>{diagnostics?.liveOrderRoutingLabel || "데모 전용 크립토 실행 경로"}</strong>
        <p>{diagnostics?.liveOrderSummary}</p>
        <p>{diagnostics?.symbolSummary}</p>
        <p>
          런타임 설정 소스: <code>{diagnostics?.configSourceValue || "-"}</code>
        </p>
      </section>

      {freeTierReport ? (
        <section className="warning-card">
          <strong>무료티어 용량 리포트</strong>
          <p>
            전체 상태: <code>{freeTierReport.overall_status || "-"}</code>
          </p>
          <p>
            OpenAI: <code>{freeTierReport.providers?.openai?.status || "-"}</code> / Google:{" "}
            <code>{freeTierReport.providers?.google_gemini?.status || "-"}</code> / Solscan:{" "}
            <code>{freeTierReport.providers?.solscan?.status || "-"}</code> / Market data:{" "}
            <code>{freeTierReport.providers?.market_data?.status || "-"}</code>
          </p>
          <p>
            병목: <code>{freeTierBottleneckValue(freeTierReport)}</code>
          </p>
        </section>
      ) : null}

      {diagnostics?.sourceConfigRepaired ? (
        <section className="warning-card">
          <strong>데모 데이터 소스가 자동 복구되었습니다</strong>
          {(diagnostics?.sourceWarnings || []).map((msg) => (
            <p key={msg}>{msg}</p>
          ))}
          <p>
            최종 소스 우선순위: <code>{diagnostics?.sourceOrderValue || "-"}</code>
          </p>
          <p>
            최종 소스 플래그: <code>{diagnostics?.sourceFlagSummary || "-"}</code>
          </p>
          <p>
            최종 실시간 시세 소스: <code>{diagnostics?.realtimeSourceValue || "-"}</code>
          </p>
        </section>
      ) : null}

      <section className="warning-card">
        <strong>리셋 안내</strong>
        <p>런타임 프로필 저장만으로는 현재 데모 시드, 포지션, PnL이 초기화되지 않습니다.</p>
        <p>크립토 데모 상태를 의도적으로 재시작할 때만 하드 리셋을 사용하세요.</p>
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
