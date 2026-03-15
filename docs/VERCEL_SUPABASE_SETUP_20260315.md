# Vercel + Supabase Setup Checklist

This project no longer needs any LLM review key for the core crypto flow.
The current target flow is:

- 10-minute crypto analysis
- daily PnL persistence
- weekly parameter autotune
- Vercel dashboard
- Supabase storage + realtime
- GitHub Actions batch execution

## 1. What values are required

### Required for Supabase

Use the current key naming if available:

- `Project URL`
- `Publishable key`
- `Secret key`

Legacy equivalents still commonly appear in existing projects:

- `anon key` == frontend public key
- `service_role key` == backend server key

Examples:

```txt
Project URL
https://abcxyz123456.supabase.co

Publishable key
sb_publishable_xxxxxxxxxxxxxxxxx

Secret key
sb_secret_xxxxxxxxxxxxxxxxx
```

If the dashboard or docs show legacy keys instead:

```txt
anon key
eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...

service_role key
eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

### Required for Vercel

- GitHub repo
- branch
- Vercel project name

Current repo example:

```txt
GitHub repo: origin of this repository
Branch: main
Vercel project name: ai-auto-dashboard
```

### Required only if Auth is enabled

- `Site URL`
- `Redirect URLs`

Examples:

```txt
Site URL
https://ai-auto-dashboard.vercel.app

Redirect URLs
https://ai-auto-dashboard.vercel.app/auth/callback
http://localhost:3000/auth/callback
```

## 2. Which values go where

### Vercel frontend env vars

Use only the public frontend key here.

```env
NEXT_PUBLIC_SUPABASE_URL=https://abcxyz123456.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=sb_publishable_xxxxxxxxxxxxxxxxx
NEXT_PUBLIC_APP_NAME=AI_Auto
```

If you use newer naming in code later, the same value can be stored as:

```env
NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=sb_publishable_xxxxxxxxxxxxxxxxx
```

### Vercel project import choices

For this repository, do not point Vercel at the Flask root.
Use the dedicated frontend app:

```txt
Import Git Repository
Root Directory: frontend
Framework Preset: Next.js
Build Command: next build
Install Command: npm install
Output setting: default
```

Server-side env vars may also be added to the same Vercel project if you want
the Next.js server layer to read Supabase directly:

```env
SUPABASE_URL=https://abcxyz123456.supabase.co
SUPABASE_SECRET_KEY=sb_secret_xxxxxxxxxxxxxxxxx
```

These must not be prefixed with `NEXT_PUBLIC_`.

### GitHub Actions or any backend worker

Use the server-side key here. Never expose it in the browser.

```env
SUPABASE_URL=https://abcxyz123456.supabase.co
SUPABASE_SECRET_KEY=sb_secret_xxxxxxxxxxxxxxxxx
```

If you keep older variable naming, map the same values like this:

```env
SUPABASE_URL=https://abcxyz123456.supabase.co
SUPABASE_SERVICE_ROLE_KEY=eyJ...
```

### GitHub Actions secrets for no-local operation

These are the secrets the batch runner needs when there is no local machine involved:

```env
SUPABASE_URL=https://abcxyz123456.supabase.co
SUPABASE_SECRET_KEY=sb_secret_xxxxxxxxxxxxxxxxx
BYBIT_API_KEY=xxxxxxxxxxxxxxxxx
BYBIT_API_SECRET=xxxxxxxxxxxxxxxxx
TELEGRAM_BOT_TOKEN=xxxxxxxxxxxxxxxxx
TELEGRAM_CHAT_ID=xxxxxxxxxxxxxxxxx
GOOGLE_API_KEY=optional
```

Notes:

- `BYBIT_API_KEY` / `BYBIT_API_SECRET` are needed if the engine should pull account and execution data.
- `TELEGRAM_*` are optional.
- `GOOGLE_API_KEY` is optional in the current crypto-only path.
- Repository `Settings > Actions > General > Workflow permissions` should allow `Read and write permissions`
  because the daily report can commit and push to `main`.

## 3. What is optional

These are only needed if you want CLI-driven migrations or admin automation:

- `SUPABASE_ACCESS_TOKEN`
- `SUPABASE_PROJECT_REF`
- direct Postgres connection string
- database password

Examples:

```txt
SUPABASE_PROJECT_REF=abcxyz123456
SUPABASE_ACCESS_TOKEN=sbp_xxxxxxxxxxxxxxxxx
POSTGRES_URL=postgresql://postgres:[password]@db.abcxyz123456.supabase.co:5432/postgres
```

## 4. What is not needed now

Not needed for the current crypto-only flow:

- OpenAI API key
- Google AI Studio key

Reason:

- no LLM review
- tuning is based on accumulated trade results and daily/weekly performance

## 5. Recommended ownership split

### Vercel

- hosts the dashboard frontend
- reads from Supabase using the public key

### Supabase

- stores setups, positions, daily pnl, autotune history, heartbeat
- provides realtime subscriptions

### GitHub Actions

- runs every 10 minutes for analysis
- uses the same batch cycle to trigger the daily report and weekly autotune when due
- writes to Supabase using the server-side key
- persists engine state/model snapshots into Supabase so the runner does not depend on local files

## 6. Fastest path

If you want the quickest deployment path:

1. Create Supabase project
2. Run [SUPABASE_CORE_SCHEMA_20260315.sql](D:\AI_Auto\docs\SUPABASE_CORE_SCHEMA_20260315.sql)
3. Send:
   - `Project URL`
   - `Publishable key` or `anon key`
   - `Secret key` or `service_role key`
   - `repo`
   - `branch`
   - `Vercel project name`
   - `Auth on/off`
4. Then the frontend wiring can start

## 7. Current cloud runner shape

The repo now includes a one-shot batch entrypoint:

- [run_batch_cycle.py](D:\AI_Auto\scripts\run_batch_cycle.py)

And the batch cycle is intended to run on GitHub Actions with:

- checkout
- Python install
- `pip install -r requirements.txt`
- `python scripts/run_batch_cycle.py`

The runner does not start Flask. It runs one cycle, persists state/model into Supabase,
and exits.

The workflow file is:

- [.github/workflows/cloud-cycle.yml](D:\AI_Auto\.github\workflows\cloud-cycle.yml)

## 8. Source notes

Official docs to check while setting values:

- Supabase API keys
- Supabase Realtime / Postgres Changes
- Supabase Auth redirect URLs / Site URL
- Vercel Git deploy
- Vercel environment variables
