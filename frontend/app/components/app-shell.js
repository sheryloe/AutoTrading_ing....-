"use client";

import { BriefcaseBusiness, LayoutDashboard, LineChart, SlidersHorizontal } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import RefreshBanner from "./refresh-banner";

const NAV_ITEMS = [
  {
    href: "/",
    label: "Overview",
    desc: "Core KPI and runtime state",
    icon: LayoutDashboard,
  },
  {
    href: "/models",
    label: "Models",
    desc: "A/B/C/D performance and trends",
    icon: LineChart,
  },
  {
    href: "/positions",
    label: "Positions",
    desc: "Open positions and fill logs",
    icon: BriefcaseBusiness,
  },
  {
    href: "/settings",
    label: "Settings",
    desc: "Runtime, symbols, and seed controls",
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
            <span>Quant Trading Console</span>
          </span>
        </Link>

        <div className="sidebar-caption">
          <span className="caption-chip">QUANT ATELIER</span>
          <p>
            Information is split into Overview, Models, Positions, and Settings so decisions stay fast and readable.
          </p>
        </div>

        <nav className="shell-nav" aria-label="Console navigation">
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
                <span>{active ? "ON" : "GO"}</span>
              </Link>
            );
          })}
        </nav>

        <div className="sidebar-footer">
          <p>Vercel + Supabase + Python batch runtime</p>
        </div>
      </aside>

      <div className="shell-main-area">
        <header className="mobile-nav">
          <div className="mobile-brand">
            <span className="brand-mark small" />
            <div>
              <strong>AI_Auto</strong>
              <p>Trading Console</p>
            </div>
          </div>
          <nav className="mobile-nav-links" aria-label="Mobile navigation">
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
