"use client";

import { BriefcaseBusiness, LayoutDashboard, LineChart, SlidersHorizontal } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import RefreshBanner from "./refresh-banner";

const NAV_ITEMS = [
  {
    href: "/",
    label: "개요",
    desc: "핵심 KPI와 런타임 상태",
    icon: LayoutDashboard,
  },
  {
    href: "/models",
    label: "모델",
    desc: "A/B/C/D 성과와 추이",
    icon: LineChart,
  },
  {
    href: "/positions",
    label: "포지션",
    desc: "오픈 포지션과 체결 로그",
    icon: BriefcaseBusiness,
  },
  {
    href: "/settings",
    label: "설정",
    desc: "런타임·심볼·시드 제어",
    icon: SlidersHorizontal,
  },
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
            <span>자동매매 운영 콘솔</span>
          </span>
        </Link>

        <div className="sidebar-caption">
          <span className="caption-chip">퀀트 워크스페이스</span>
          <p>
            화면을 개요, 모델, 포지션, 설정으로 분리해 한눈에 보고 빠르게 의사결정할 수 있게 구성했습니다.
          </p>
        </div>

        <nav className="shell-nav" aria-label="콘솔 내비게이션">
          {NAV_ITEMS.map((item) => {
            const active = pathname === item.href;
            const Icon = item.icon;
            return (
              <Link key={item.href} href={item.href} className={`nav-link ${active ? "active" : ""}`}>
                <div className="nav-link-copy">
                  <span className="nav-icon-wrap">{Icon ? <Icon size={16} strokeWidth={2.05} aria-hidden="true" /> : null}</span>
                  <strong>{item.label}</strong>
                  <p>{item.desc}</p>
                </div>
                <span>{active ? "활성" : "이동"}</span>
              </Link>
            );
          })}
        </nav>

        <div className="sidebar-footer">
          <p>Vercel + Supabase + Python 배치 런타임</p>
        </div>
      </aside>

      <div className="shell-main-area">
        <header className="mobile-nav">
          <div className="mobile-brand">
            <span className="brand-mark small" />
            <div>
              <strong>AI_Auto</strong>
              <p>자동매매 콘솔</p>
            </div>
          </div>
          <nav className="mobile-nav-links" aria-label="모바일 내비게이션">
            {NAV_ITEMS.map((item) => {
              const active = pathname === item.href;
              const Icon = item.icon;
              return (
                <Link key={item.href} href={item.href} className={`mobile-link ${active ? "active" : ""}`}>
                  {Icon ? <Icon size={14} strokeWidth={2.1} aria-hidden="true" /> : null}
                  {item.label}
                </Link>
              );
            })}
          </nav>
        </header>

        <main className="shell-main">
          <div className="page-stack">
            <RefreshBanner />
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}
