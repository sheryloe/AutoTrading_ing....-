# Automethemoney

Automethemoney runs on **GitHub Actions (hosted) + Supabase + Vercel**.

- Repository: https://github.com/sheryloe/Automethemoney
- GitHub Pages Hub: https://sheryloe.github.io/Automethemoney/

## Architecture (fixed)

| Layer | Role | Runtime |
| --- | --- | --- |
| GitHub Actions (hosted) | Scheduled batch execution (`cloud-cycle`) | `ubuntu-latest` |
| Supabase | State ledger | `engine_heartbeat`, `model_setups`, `positions`, `daily_model_pnl` |
| Vercel | Console and API | `/`, `/models`, `/positions`, `/settings` |
| GitHub Pages | Public hub and service guide | `docs/index.html` |

## Required secrets

### GitHub Secrets (workflow)

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `SERVICE_MASTER_KEY`
- `BYBIT_API_KEY`
- `BYBIT_API_SECRET`

### Vercel env

- `NEXT_PUBLIC_SUPABASE_URL`
- `NEXT_PUBLIC_SUPABASE_ANON_KEY`
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `SERVICE_MASTER_KEY`
- `SERVICE_ADMIN_TOKEN`

### Local `.env` (verification scripts)

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`

## Runtime policy

- `TRADE_MODE=paper`
- `ENABLE_LIVE_EXECUTION=false`
- `LIVE_ENABLE_CRYPTO=false`
- `BYBIT_READONLY_SYNC=true`
- `BYBIT_SECRET_SOURCE=github`
- `RUNNER_ROLE=github-hosted`

## Build and operation (PowerShell)

```powershell
# install dependencies
python -m pip install --upgrade pip
pip install -r .\requirements.txt

# dispatch one hosted cycle
.\ops\run-once-cloud-cycle.ps1 -Repo "sheryloe/Automethemoney" -Workflow "cloud-cycle.yml" -Ref "main"

# verify Supabase updates
.\ops\verify-stack.ps1 -EnvFile ".\.env" -LookbackHours 1 -ExpectedRunner "github-hosted"
```

## Health checks

- heartbeat updated in the last cycle
- `model_setups.recent > 0`
- `model_signal_audit.recent > 0`
- `meta_json.runner = github-hosted`
- `meta_json.trade_mode = paper`

## Troubleshooting quick map

1. No cycle updates: check Actions run logs first.
2. `last_bybit_sync_ts = 0`: inspect `bybit_preflight_public_status` and `bybit_preflight_auth_status`.
3. Console shows stale data: verify Supabase keys and last heartbeat timestamp.

## Main paths

- Workflow: `.github/workflows/cloud-cycle.yml`
- Batch entry: `scripts/run_batch_cycle.py`
- Ops scripts: `ops/run-once-cloud-cycle.ps1`, `ops/verify-stack.ps1`
- Pages hub: `docs/index.html`
- Wiki source: `docs/wiki-src/`