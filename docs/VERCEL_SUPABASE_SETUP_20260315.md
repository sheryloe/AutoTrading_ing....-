# Vercel + Supabase Service Console Setup

This repo now supports a service-style flow:

- Vercel hosts the dashboard and the operator console
- the operator console stores exchange keys encrypted in Supabase
- GitHub Actions runs the batch engine every 10 minutes
- the batch runner reads encrypted provider keys from Supabase at runtime
- provider keys do not need to live in GitHub Secrets anymore

## 1. Core architecture

### Vercel

- serves the dashboard
- serves the operator console UI
- accepts the admin token and provider keys through a server route
- encrypts provider keys into Supabase using `SERVICE_MASTER_KEY`

### Supabase

- stores runtime tables like setups, positions, pnl, heartbeat
- stores encrypted provider credentials in `service_secrets`
- stores runtime profile overrides in `engine_state_blobs` with key `service_runtime_config`

### GitHub Actions

- runs `python scripts/run_batch_cycle.py`
- loads runtime profile from Supabase
- decrypts Bybit credentials from Supabase using `SERVICE_MASTER_KEY`
- writes snapshots back to Supabase

## 2. What values are required

### Supabase project values

- `Project URL`
- `Publishable key`
- `Secret key`

Example:

```txt
Project URL
https://abcxyz123456.supabase.co

Publishable key
sb_publishable_xxxxxxxxxxxxxxxxx

Secret key
sb_secret_xxxxxxxxxxxxxxxxx
```

### Service console values

- `SERVICE_MASTER_KEY`
- `SERVICE_ADMIN_TOKEN`

Generate them as random long strings.

Example:

```txt
SERVICE_MASTER_KEY
replace-with-random-32-plus-char-string

SERVICE_ADMIN_TOKEN
replace-with-random-operator-password
```

## 3. What goes where

### Vercel Environment Variables

These are required for the service console and dashboard:

```env
NEXT_PUBLIC_SUPABASE_URL=https://abcxyz123456.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=sb_publishable_xxxxxxxxxxxxxxxxx
SUPABASE_URL=https://abcxyz123456.supabase.co
SUPABASE_SECRET_KEY=sb_secret_xxxxxxxxxxxxxxxxx
SERVICE_MASTER_KEY=replace-with-random-32-plus-char-string
SERVICE_ADMIN_TOKEN=replace-with-random-operator-password
```

Notes:

- `NEXT_PUBLIC_*` values are safe for the browser
- `SUPABASE_SECRET_KEY`, `SERVICE_MASTER_KEY`, and `SERVICE_ADMIN_TOKEN` must stay server-side only
- after changing Vercel env vars, redeploy the project

### GitHub Actions Secrets

These are required for the batch runner:

```env
SUPABASE_URL=https://abcxyz123456.supabase.co
SUPABASE_SECRET_KEY=sb_secret_xxxxxxxxxxxxxxxxx
SERVICE_MASTER_KEY=replace-with-random-32-plus-char-string
```

Optional:

```env
TELEGRAM_BOT_TOKEN=optional
TELEGRAM_CHAT_ID=optional
GOOGLE_API_KEY=optional
```

Important:

- `BYBIT_API_KEY` and `BYBIT_API_SECRET` no longer need to be stored in GitHub Secrets for the service flow
- the batch runner will fetch Bybit credentials from Supabase if they were saved through the Vercel console

### GitHub Variables

Nothing is required there right now.

## 4. What the operator does in the service UI

After Vercel env vars are set and deployed:

1. Open the Vercel dashboard URL
2. Go to the `Service control` panel
3. Enter `SERVICE_ADMIN_TOKEN`
4. Save the runtime profile
5. Save Bybit API key and secret

The Bybit credentials are stored encrypted in Supabase, not in the browser and not in GitHub Secrets.

## 5. Supabase SQL to run

Run:

- [SUPABASE_CORE_SCHEMA_20260315.sql](D:\AI_Auto\docs\SUPABASE_CORE_SCHEMA_20260315.sql)

This schema now includes:

- runtime tables
- `engine_state_blobs`
- `service_secrets`
- encryption/decryption helper functions:
  - `upsert_service_secret`
  - `get_service_secret`
  - `delete_service_secret`

## 6. GitHub workflow

The workflow file is:

- [.github/workflows/cloud-cycle.yml](D:\AI_Auto\.github\workflows\cloud-cycle.yml)

It runs:

- every 10 minutes
- on manual dispatch

And executes:

- [run_batch_cycle.py](D:\AI_Auto\scripts\run_batch_cycle.py)

That script now:

- loads runtime config from Supabase
- loads Bybit credentials from Supabase
- runs one batch cycle
- persists state/model snapshots back to Supabase

## 7. Repository permission setting

GitHub repo:

- `Settings`
- `Actions`
- `General`
- `Workflow permissions`
- `Read and write permissions`

This is needed for the daily report commit/push flow.

## 8. Minimum secure setup

If you want the safest initial rollout:

1. Keep runtime profile on `paper`
2. Set `ENABLE_LIVE_EXECUTION=false`
3. Save Bybit credentials only after the service path works end-to-end
4. Use a dedicated Bybit API key with no withdrawal permission

## 9. Current UI and code entrypoints

- Dashboard page: [page.js](D:\AI_Auto\frontend\app\page.js)
- Service console UI: [control-console.js](D:\AI_Auto\frontend\app\components\control-console.js)
- Runtime config helper: [service-control.js](D:\AI_Auto\frontend\lib\service-control.js)
- Runtime config API: [route.js](D:\AI_Auto\frontend\app\api\service\runtime\route.js)
- Bybit vault API: [route.js](D:\AI_Auto\frontend\app\api\service\credentials\bybit\route.js)
