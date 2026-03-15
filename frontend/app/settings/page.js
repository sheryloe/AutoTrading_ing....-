import ControlConsole from "../components/control-console";
import MetricCard from "../components/metric-card";
import PageHeader from "../components/page-header";
import { loadServiceControlData } from "../../lib/service-control";

export const dynamic = "force-dynamic";

function conflictPolicyLabel(value) {
  const normalized = String(value || "conservative").toLowerCase();
  if (normalized === "aggressive") return "TP 우선";
  if (normalized === "neutral") return "open 기준 근접 우선";
  return "SL 우선";
}

export default async function SettingsPage() {
  const control = await loadServiceControlData();
  const configuredProviderCount = Object.values(control.providerStatuses || {}).filter((item) => item?.configured).length;

  return (
    <>
      <PageHeader
        eyebrow="설정"
        title="운영 입력과 서비스 콘솔"
        description="실행 키, 데이터 provider vault, runtime profile을 이 화면에서만 관리합니다. 개요나 포지션 화면에는 입력 폼을 두지 않고 운영 콘솔로 분리했습니다."
        actions={[
          { href: "/", label: "개요로 이동", tone: "ghost" },
          { href: "/positions", label: "포지션 보기", tone: "primary" },
        ]}
      />

      {control.errors?.length ? (
        <section className="warning-card">
          <strong>설정 데이터를 일부 불러오지 못했습니다.</strong>
          {control.errors.map((msg) => (
            <p key={msg}>{msg}</p>
          ))}
        </section>
      ) : null}

      <section className="kpi-row">
        <MetricCard
          label="저장 준비 상태"
          value={control.writeReady ? "쓰기 가능" : "읽기 전용"}
          meta="Vercel 서버 환경변수 기준"
          tone={control.writeReady ? "green" : "amber"}
        />
        <MetricCard
          label="Execution target"
          value={String(control.runtimeConfig?.EXECUTION_TARGET || "paper")}
          meta={`arm ${control.runtimeConfig?.LIVE_EXECUTION_ARMED ? "on" : "off"}`}
          tone="cyan"
        />
        <MetricCard
          label="캔들 충돌 규칙"
          value={conflictPolicyLabel(control.runtimeConfig?.INTRABAR_CONFLICT_POLICY)}
          meta={String(control.runtimeConfig?.INTRABAR_CONFLICT_POLICY || "conservative")}
          tone="amber"
        />
        <MetricCard
          label="설정된 provider"
          value={String(configuredProviderCount)}
          meta="Bybit / Binance / CoinGecko 기준"
        />
      </section>

      <section className="warning-card">
        <strong>리셋 정책</strong>
        <p>런타임 프로필을 다시 저장해도 현재 데모 시드, 누적 손익, 오픈 포지션은 유지됩니다.</p>
        <p>시드를 새로 적용하려면 아래의 하드 리셋 섹션에서 명시적으로 다시 시작해야 합니다.</p>
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
