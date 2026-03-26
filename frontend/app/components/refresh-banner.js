"use client";

import { RefreshCw, TimerReset } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { UI_REFRESH_MINUTES, UI_REFRESH_SECONDS } from "../../lib/ui-config";

export default function RefreshBanner() {
  const router = useRouter();
  const [secondsLeft, setSecondsLeft] = useState(UI_REFRESH_SECONDS);

  useEffect(() => {
    const tick = window.setInterval(() => {
      setSecondsLeft((prev) => {
        if (prev <= 1) {
          router.refresh();
          return UI_REFRESH_SECONDS;
        }
        return prev - 1;
      });
    }, 1000);

    return () => window.clearInterval(tick);
  }, [router]);

  const minutes = Math.floor(secondsLeft / 60);
  const seconds = secondsLeft % 60;
  const nextLabel = `${minutes}:${String(seconds).padStart(2, "0")}`;

  function handleRefreshNow() {
    router.refresh();
    setSecondsLeft(UI_REFRESH_SECONDS);
  }

  return (
    <div className="refresh-banner" role="status" aria-live="polite">
      <div className="refresh-banner-copy">
        <span className="refresh-pill">자동 갱신</span>
        <p>
          대시보드는 {UI_REFRESH_MINUTES}분마다 자동 갱신됩니다. 수동 갱신을 누르면 Supabase 최신 상태를 즉시 반영합니다.
        </p>
      </div>
      <div className="refresh-banner-actions">
        <strong>
          <TimerReset size={15} strokeWidth={2.1} aria-hidden="true" /> 다음 갱신 {nextLabel}
        </strong>
        <button type="button" className="refresh-button" onClick={handleRefreshNow}>
          <RefreshCw size={14} strokeWidth={2.1} aria-hidden="true" />
          지금 갱신
        </button>
      </div>
    </div>
  );
}
