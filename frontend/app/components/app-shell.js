"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV_ITEMS = [
  { href: "/", label: "개요", desc: "오늘의 상태와 핵심 KPI" },
  { href: "/models", label: "모델 성과", desc: "모델별 PnL과 튜닝 상태" },
  { href: "/positions", label: "포지션", desc: "오픈 포지션과 최신 진입 계획" },
  { href: "/settings", label: "설정", desc: "서비스 콘솔과 운영 입력" },
];

export default function AppShell({ children }) {
  const pathname = usePathname();

  return (
    <div className="app-shell">
      <aside className="shell-sidebar">
        <Link href="/" className="shell-brand">
          <span className="brand-mark" />
          <span className="brand-copy">
            <strong>AI_Auto</strong>
            <span>운영자용 트레이딩 콘솔</span>
          </span>
        </Link>

        <div className="sidebar-caption">
          <span className="caption-chip">HYPER OPS</span>
          <p>
            상태 확인, 모델 성과, 포지션 점검, 운영 설정을 한 화면에 몰아넣지 않고
            역할별로 분리한 운영형 대시보드입니다.
          </p>
        </div>

        <nav className="shell-nav" aria-label="운영 메뉴">
          {NAV_ITEMS.map((item) => {
            const active = pathname === item.href;
            return (
              <Link key={item.href} href={item.href} className={`nav-link ${active ? "active" : ""}`}>
                <div>
                  <strong>{item.label}</strong>
                  <p>{item.desc}</p>
                </div>
                <span>{active ? "ON" : "GO"}</span>
              </Link>
            );
          })}
        </nav>

        <div className="sidebar-footer">
          <p>Vercel 프론트 / Supabase 상태 저장 / Python 배치 실행</p>
        </div>
      </aside>

      <div className="shell-main-area">
        <header className="mobile-nav">
          <div className="mobile-brand">
            <span className="brand-mark small" />
            <div>
              <strong>AI_Auto</strong>
              <p>운영자 콘솔</p>
            </div>
          </div>
          <nav className="mobile-nav-links" aria-label="모바일 메뉴">
            {NAV_ITEMS.map((item) => {
              const active = pathname === item.href;
              return (
                <Link key={item.href} href={item.href} className={`mobile-link ${active ? "active" : ""}`}>
                  {item.label}
                </Link>
              );
            })}
          </nav>
        </header>

        <main className="shell-main">
          <div className="page-stack">{children}</div>
        </main>
      </div>
    </div>
  );
}
