# AI_Auto

AI_Auto is an operator-first crypto futures demo console built around four planner models (`A/B/C/D`), a Supabase-backed runtime, and a scheduled GitHub Actions batch cycle.

The system separates:

- overview and monitoring
- model performance
- open positions and entry plans
- runtime control and secrets

## Stack

- `frontend/`: Next.js operator console
- `src/`: Python trading engine and batch logic
- `Supabase`: runtime blobs, signal/setup snapshots, positions, model PnL
- `GitHub Actions`: scheduled `cloud-cycle` execution
- `docs/`: GitHub Pages landing and static docs

## Main Pages

- Console: `/`
- Models: `/models`
- Positions: `/positions`
- Settings: `/settings`

## Supabase Data

Key tables:

- `engine_heartbeat`
- `engine_runtime_config`
- `model_setups`
- `model_signal_audit`
- `positions`
- `daily_model_pnl`

Schema entry points:

- [docs/SUPABASE_CORE_SCHEMA_20260315.sql](docs/SUPABASE_CORE_SCHEMA_20260315.sql)
- [docs/SUPABASE_SCHEMA_20260315.sql](docs/SUPABASE_SCHEMA_20260315.sql)
- [docs/VERCEL_SUPABASE_SETUP_20260315.md](docs/VERCEL_SUPABASE_SETUP_20260315.md)

## Runtime Notes

- Default execution target is `paper`
- `cloud-cycle` loads runtime overrides from Supabase before running
- `A/B/D` now support shallower pullback tuning via `CRYPTO_TUNE_OVERRIDES`
- Dynamic Top 5 rotation can be enabled with:
  - `CRYPTO_DYNAMIC_UNIVERSE_ENABLED=true`
  - `MACRO_TREND_POOL_SIZE=5`
  - `MACRO_TREND_RESELECT_SECONDS=14400`

## Scheduled Execution

The main batch workflow is:

- [`.github/workflows/cloud-cycle.yml`](.github/workflows/cloud-cycle.yml)

It currently supports:

- native GitHub Actions schedule
- manual dispatch
- external cron calling `workflow_dispatch`

### Native schedule

`cloud-cycle.yml` currently includes:

```yaml
on:
  workflow_dispatch:
  schedule:
    - cron: "*/1 * * * *"
```

If you want GitHub itself to run the batch, this is enough.

### External cron -> GitHub Actions dispatch

If you want a third-party cron service to trigger the workflow, call:

```text
POST https://api.github.com/repos/sheryloe/Automethemoney/actions/workflows/cloud-cycle.yml/dispatches
```

Headers:

```text
Authorization: Bearer <GITHUB_TOKEN>
Accept: application/vnd.github+json
X-GitHub-Api-Version: 2022-11-28
Content-Type: application/json
```

Body:

```json
{
  "ref": "main"
}
```

## Daily Model PnL Archive

Daily model PnL archival is enabled through the engine's Git daily report flow.

The workflow now writes reports to:

```text
docs/data/daily_pnl
```

Generated files include:

- `docs/data/daily_pnl/YYYY-MM-DD.json`
- `docs/data/daily_pnl/YYYY-MM-DD.csv`
- `docs/data/daily_pnl/summary.csv`
- `docs/data/daily_pnl/summary.json`

At UTC day rollover, the engine writes the previous day's `daily_model_pnl` rows and auto-commits them when these flags are enabled:

- `GIT_DAILY_REPORTS_ENABLED=true`
- `GIT_DAILY_REPORTS_AUTOCOMMIT=true`
- `GIT_DAILY_REPORTS_AUTOPUSH=true`

## GitHub Pages

GitHub Pages source files:

- Landing: [docs/index.html](docs/index.html)
- Daily PnL archive: [docs/daily-pnl.html](docs/daily-pnl.html)

The archive page reads `docs/data/daily_pnl/summary.json` directly, so daily reports become visible on Pages after the next committed day-end snapshot.

## Quick Start

1. Apply [docs/SUPABASE_CORE_SCHEMA_20260315.sql](docs/SUPABASE_CORE_SCHEMA_20260315.sql) in Supabase SQL Editor.
2. Set Vercel environment variables and GitHub Actions secrets.
3. Save runtime profile and provider secrets from `/settings`.
4. Run `cloud-cycle` once.
5. Verify data in `engine_heartbeat`, `engine_runtime_config`, `model_signal_audit`, and `daily_model_pnl`.

## Safety

- Never expose `SUPABASE_SECRET_KEY` as `NEXT_PUBLIC_*`
- Keep service/provider keys only in Vercel, GitHub secrets, or local ignored `.env.local`
- Use restricted exchange API keys whenever possible
