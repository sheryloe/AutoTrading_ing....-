# Vercel + Supabase Service Console Operator Guide

This repo now follows a service-console pattern instead of a `store all exchange keys in GitHub Secrets` pattern.

## 1. What changed

The operator flow is now:

1. `Vercel` hosts the dashboard and service console.
2. You enter provider keys in the service console.
3. Vercel encrypts those provider keys into `Supabase`.
4. `GitHub Actions` reads the encrypted provider keys from Supabase at runtime.
5. The Python batch runner uses the hydrated credentials for data access and future execution readiness.

This means:

- `Bybit`, `Binance`, and `CoinGecko` keys belong in the service console.
- `GitHub Secrets` only keep shared infrastructure secrets.
- Saving `Bybit` keys is not the same as enabling live trading.

## 2. Provider split

### Bybit

Use for:

- the single execution account in service mode v1
- future live crypto execution routing
- account-specific exchange operations

Important:

- `Bybit` credentials being stored only means the execution provider is `configured`
- it does **not** mean the engine is armed for live trading

### Binance

Use for:

- realtime quote and market-data reads for planner models
- preferred price source in the default source order

### CoinGecko

Use for:

- market-cap and universe data
- top-market source in service mode v1

## 3. Execution target model

Service mode v1 treats "wallet selection" as **execution target selection**.

Available targets:

- `paper`
- `bybit-live`

### `paper`

- safest default
- provider keys may still be stored
- crypto cycles remain non-live

### `bybit-live`

- means you are selecting the `Bybit` execution account as the future live target
- still does **not** enable live trading by itself

## 4. 2-step live safety model

Live eligibility only becomes true when **all** of these are true:

- `EXECUTION_TARGET == bybit-live`
- `TRADE_MODE == live` (derived from execution target)
- `ENABLE_LIVE_EXECUTION == true`
- `LIVE_ENABLE_CRYPTO == true`
- `LIVE_EXECUTION_ARMED == true`
- valid `Bybit` credentials exist in the provider vault

And even then, this phase still keeps the current crypto execution path in demo mode. The UI will say the system is `configured for future live execution`, not that orders are already active.

## 5. Where each secret goes

### 5-1. Vercel Environment Variables

Path:

1. `Vercel`
2. project
3. `Settings`
4. `Environment Variables`

Add these values:

```env
NEXT_PUBLIC_SUPABASE_URL=https://your-project.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=sb_publishable_xxxxxxxxx
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SECRET_KEY=sb_secret_xxxxxxxxx
SERVICE_MASTER_KEY=replace-with-long-random-string
SERVICE_ADMIN_TOKEN=replace-with-long-random-string
```

### What each Vercel variable does

- `NEXT_PUBLIC_SUPABASE_URL`
  - browser-safe
  - frontend Supabase URL
- `NEXT_PUBLIC_SUPABASE_ANON_KEY`
  - browser-safe
  - frontend publishable key
- `SUPABASE_URL`
  - server-side read/write access target for Vercel routes
- `SUPABASE_SECRET_KEY`
  - server-side Supabase secret key
- `SERVICE_MASTER_KEY`
  - encrypts and decrypts provider vault payloads
- `SERVICE_ADMIN_TOKEN`
  - operator token required to save or clear service-console values

### Important

- anything starting with `NEXT_PUBLIC_` is exposed to the browser
- do **not** put `SUPABASE_SECRET_KEY`, `SERVICE_MASTER_KEY`, or `SERVICE_ADMIN_TOKEN` under `NEXT_PUBLIC_*`

## 5-2. GitHub Actions Secrets

Path:

1. GitHub repo
2. `Settings`
3. `Secrets and variables`
4. `Actions`
5. `Secrets`

Only these are required for service mode:

```env
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SECRET_KEY=sb_secret_xxxxxxxxx
SERVICE_MASTER_KEY=replace-with-the-same-master-key-used-in-vercel
```

Optional:

```env
TELEGRAM_BOT_TOKEN=optional
TELEGRAM_CHAT_ID=optional
GOOGLE_API_KEY=optional
```

### Important

Do **not** store these in GitHub Secrets for the service path:

- `BYBIT_API_KEY`
- `BYBIT_API_SECRET`
- `BINANCE_API_KEY`
- `BINANCE_API_SECRET`
- `COINGECKO_API_KEY`

Those now belong in the Vercel service console.

## 6. Supabase schema to run

Run this file in `Supabase > SQL Editor`:

- [SUPABASE_CORE_SCHEMA_20260315.sql](./SUPABASE_CORE_SCHEMA_20260315.sql)

It creates:

- `instruments`
- `engine_heartbeat`
- `engine_runtime_config`
- `engine_state_blobs`
- `service_secrets`
- `model_runtime_tunes`
- `model_setups`
- `model_signal_audit`
- `positions`
- `daily_model_pnl`

It also creates encrypted provider-vault RPC helpers:

- `upsert_service_secret`
- `get_service_secret`
- `delete_service_secret`

Supported provider ids in the service console are:

- `bybit`
- `binance`
- `coingecko`

## 7. Service console workflow

After Vercel env vars are saved and the project is redeployed:

1. open the Vercel app URL
2. go to `Service control`
3. enter `SERVICE_ADMIN_TOKEN`
4. choose an `Execution target`
5. save the `Runtime profile`
6. save `Bybit` credentials if you want future live execution readiness
7. save `Binance` credentials if you want the preferred realtime data source configured
8. save `CoinGecko` API key for universe data

### Recommended first rollout

1. `Execution target = paper`
2. `ENABLE_AUTOTRADE = true`
3. `ENABLE_LIVE_EXECUTION = false`
4. `LIVE_ENABLE_CRYPTO = false`
5. `LIVE_EXECUTION_ARMED = false`
6. save all provider keys only after the dashboard is confirmed healthy

## 8. Deployment and redeploy order

### First deployment

1. push repo changes to GitHub
2. run the Supabase SQL schema
3. create or update the Vercel project
4. set Vercel env vars
5. deploy or redeploy Vercel
6. add the GitHub Actions secrets
7. set `GitHub > Settings > Actions > General > Workflow permissions > Read and write permissions`
8. run `cloud-cycle` manually once

### When env vars change

1. update Vercel env vars
2. redeploy Vercel
3. if `SERVICE_MASTER_KEY` changes, update the same value in GitHub Secrets too

### When provider keys change

1. open the Vercel service console
2. enter the admin token
3. save or clear the provider card
4. no GitHub secret change is needed

## 9. Batch runner behavior

Workflow file:

- [.github/workflows/cloud-cycle.yml](../.github/workflows/cloud-cycle.yml)

Batch entrypoint:

- [scripts/run_batch_cycle.py](../scripts/run_batch_cycle.py)

What the runner does:

1. loads runtime config from `engine_state_blobs` key `service_runtime_config`
2. decrypts provider vault entries from `service_secrets`
3. hydrates `Bybit`, `Binance`, and `CoinGecko` credentials into runtime env
4. runs one Python engine cycle
5. syncs heartbeat, setups, positions, and PnL back to Supabase

It also syncs:

- active runtime config snapshot into `engine_runtime_config`
- per-model signal audit rows into `model_signal_audit`

### Runtime keys worth knowing

- `CRYPTO_DYNAMIC_UNIVERSE_ENABLED`
  - `false`: `BYBIT_SYMBOLS` is a hard fixed universe
  - `true`: the engine rotates the universe from trend data
- `CRYPTO_PRIORITY_SYMBOLS`
  - only used when dynamic universe is enabled
  - these symbols are merged in first as priority candidates
- `MACRO_TREND_POOL_SIZE`
  - how many symbols the dynamic universe keeps
- `MACRO_TREND_RESELECT_SECONDS`
  - how often the dynamic universe rotates
  - recommended starting point: `14400` for 4 hours
- `CRYPTO_TUNE_OVERRIDES`
  - JSON object stored in the runtime profile
  - lets you bias `A/B/D` toward shallower pullbacks without code changes
  - example:

```json
{
  "A": { "threshold_bias": -0.006, "entry_atr_mul": 1.45, "shallow_pullback_atr": 0.20, "zone_half_atr": 0.60, "risk_reward_min": 1.02 },
  "B": { "threshold_bias": -0.006, "floor_atr_mul": 1.20, "mid_atr_boost": 0.35, "shallow_pullback_atr": 0.18, "zone_half_atr": 0.56, "risk_reward_min": 1.03 },
  "D": { "threshold_bias": -0.006, "entry_atr_mul": 1.75, "shallow_pullback_atr": 0.22, "zone_low_atr": 0.62, "zone_high_atr": 0.64, "risk_reward_min": 1.00 }
}
```

## 9-1. Runtime diagnostics validation

After running `cloud-cycle` once, validate these tables in `Supabase > SQL Editor`.

### Confirm runtime config snapshot

```sql
select
  engine_name,
  captured_at,
  trade_mode,
  autotrade_enabled,
  live_execution_enabled,
  live_enable_crypto,
  autotrade_models,
  live_models,
  configured_symbols
from public.engine_runtime_config
order by captured_at desc nulls last
limit 1;
```

Expected:

- one row for `ai_auto_core`
- `autotrade_models` and `live_models` match the runtime profile you saved
- `configured_symbols` matches the service/runtime config

### Confirm model signal audit rows exist for A/B/C/D

```sql
select
  model_id,
  count(*) as rows,
  max(cycle_at) as latest_cycle_at
from public.model_signal_audit
group by model_id
order by model_id;
```

Expected:

- rows exist for active scan cycles
- latest cycle timestamps are recent after the batch finishes

### Read why models were filtered

```sql
select
  cycle_at,
  model_id,
  symbol,
  audit_status,
  audit_reason,
  score,
  threshold,
  risk_reward,
  gate_ok,
  symbol_allowed,
  reentry_blocked
from public.model_signal_audit
order by cycle_at desc, model_id, symbol
limit 60;
```

Use this to answer questions like:

- why only `C` entered
- whether `A/B/D` were blocked by gate, threshold, or cooldown
- whether a model is inactive vs. simply filtered out

### Check latest status distribution

```sql
select
  model_id,
  audit_status,
  count(*) as cnt
from public.model_signal_audit
where cycle_at >= now() - interval '1 hour'
group by model_id, audit_status
order by model_id, audit_status;
```

Typical statuses:

- `entry_candidate`
- `in_position`
- `filtered_symbol`
- `filtered_gate`
- `below_threshold`
- `low_risk_reward`
- `expired`
- `reentry_blocked`
- `waiting_setup`

### Compare planned signals with actual positions

```sql
select
  s.cycle_at,
  s.model_id,
  s.symbol,
  s.audit_status,
  p.status as position_status,
  p.opened_at
from public.model_signal_audit s
left join public.positions p
  on p.market = s.market
 and p.model_id = s.model_id
 and p.symbol = s.symbol
 and p.status = 'open'
where s.cycle_at >= now() - interval '1 hour'
order by s.cycle_at desc, s.model_id, s.symbol;
```

Use this to confirm:

- `entry_candidate` rows that became open positions
- filtered rows that never opened
- cases where one model opened while others were filtered

## 10. Current limitation in this phase

This refactor prepares the service model, vault, safety semantics, and deployment flow.

It does **not** yet switch the crypto engine into real Bybit market-order execution.

So even if the service console shows:

- `configured`
- `armed`
- `bybit-live`

that still means:

- `future live execution target is prepared`
- not `real crypto orders are already firing`

## 11. Main repo entrypoints

- Dashboard page: [frontend/app/page.js](../frontend/app/page.js)
- Service console UI: [frontend/app/components/control-console.js](../frontend/app/components/control-console.js)
- Provider/runtime helpers: [frontend/lib/service-control.js](../frontend/lib/service-control.js)
- Provider definitions: [frontend/lib/service-provider.js](../frontend/lib/service-provider.js)
- Generic provider API route: [frontend/app/api/service/credentials/[provider]/route.js](../frontend/app/api/service/credentials/%5Bprovider%5D/route.js)
- Runtime API route: [frontend/app/api/service/runtime/route.js](../frontend/app/api/service/runtime/route.js)
