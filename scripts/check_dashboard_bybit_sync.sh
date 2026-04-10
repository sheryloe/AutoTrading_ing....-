#!/usr/bin/env bash
set -euo pipefail

if ! command -v npx >/dev/null 2>&1; then
  echo "npx not found. Install Node.js/npm first." >&2
  exit 2
fi

: "${DASHBOARD_URL:?Set DASHBOARD_URL to the dashboard URL.}"

MAX_AGE_SECONDS="${MAX_AGE_SECONDS:-120}"
SESSION="${PLAYWRIGHT_CLI_SESSION:-bybit-sync}"
export CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
export PWCLI="$CODEX_HOME/skills/playwright/scripts/playwright_cli.sh"

if [[ ! -x "$PWCLI" ]]; then
  echo "Playwright CLI wrapper not found: $PWCLI" >&2
  exit 2
fi

mkdir -p output/playwright

OPEN_ARGS=()
if [[ "${PW_HEADED:-0}" == "1" ]]; then
  OPEN_ARGS+=(--headed)
fi

cleanup() {
  "$PWCLI" --session "$SESSION" close >/dev/null 2>&1 || true
}
trap cleanup EXIT

"$PWCLI" --session "$SESSION" open "$DASHBOARD_URL" "${OPEN_ARGS[@]}"
"$PWCLI" --session "$SESSION" snapshot > output/playwright/bybit_snapshot.txt

payload=$(
  "$PWCLI" --session "$SESSION" eval "(() => {
    const spans = Array.from(document.querySelectorAll('.hero-metric span'));
    const label = spans.find(el => (el.textContent || '').includes('Bybit'));
    if (!label) return JSON.stringify({ error: 'label_not_found' });
    const card = label.closest('.hero-metric');
    const mode = card?.querySelector('strong')?.textContent?.trim() || '';
    const detail = card?.querySelector('small')?.textContent?.trim() || '';
    return JSON.stringify({ mode, detail });
  })()"
)

"$PWCLI" --session "$SESSION" run-code "await page.screenshot({ path: 'output/playwright/bybit_sync.png', fullPage: true })"

export BYBIT_PAYLOAD="$payload"
export MAX_AGE_SECONDS
python3 - <<'PY'
import json, os, re, time, sys

raw = os.environ.get("BYBIT_PAYLOAD", "")
try:
    data = json.loads(raw) if raw else {}
except Exception as exc:
    print("ERR: payload parse failed:", exc)
    print("RAW:", raw)
    sys.exit(2)

if data.get("error"):
    print("ERR:", data["error"])
    sys.exit(2)

mode = (data.get("mode") or "").strip()
detail = (data.get("detail") or "").strip()
m = re.search(r"\bts\s*(\d+)\b", detail)
ts = int(m.group(1)) if m else 0
now = int(time.time())
age = (now - ts) if ts else None
max_age = int(os.environ.get("MAX_AGE_SECONDS") or "120")

print("mode:", mode or "-")
print("detail:", detail or "-")
print("ts:", ts or "-")
if ts and age is not None:
    print("age_seconds:", age)
    if age <= max_age:
        print("status: OK")
        sys.exit(0)
    print("status: STALE")
    sys.exit(1)

print("status: NO_TS")
sys.exit(1)
PY
