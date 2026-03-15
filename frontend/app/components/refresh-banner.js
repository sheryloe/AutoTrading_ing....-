"use client";

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
        <span className="refresh-pill">AUTO REFRESH</span>
        <p>
          이 화면은 {UI_REFRESH_MINUTES}분마다 자동 갱신됩니다. 다음 갱신 전까지 포지션이 자동으로
          정리되거나 상태가 바뀔 수 있습니다.
        </p>
      </div>
      <div className="refresh-banner-actions">
        <strong>다음 갱신 {nextLabel}</strong>
        <button type="button" className="refresh-button" onClick={handleRefreshNow}>
          지금 새로고침
        </button>
      </div>
    </div>
  );
}
