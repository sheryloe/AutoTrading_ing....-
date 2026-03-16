begin;

create extension if not exists pgcrypto;

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = timezone('utc', now());
  return new;
end;
$$;

create table if not exists public.instruments (
  symbol text primary key,
  exchange text not null default 'bybit',
  is_active boolean not null default true,
  is_major boolean not null default true,
  sort_order integer not null default 100,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

drop trigger if exists trg_instruments_updated_at on public.instruments;
create trigger trg_instruments_updated_at
before update on public.instruments
for each row execute function public.set_updated_at();

insert into public.instruments (symbol, exchange, is_active, is_major, sort_order)
values
  ('BTCUSDT', 'bybit', true, true, 1),
  ('ETHUSDT', 'bybit', true, true, 2),
  ('SOLUSDT', 'bybit', true, true, 3),
  ('XRPUSDT', 'bybit', true, true, 4),
  ('BNBUSDT', 'bybit', true, true, 5)
on conflict (symbol) do update
set
  exchange = excluded.exchange,
  is_active = excluded.is_active,
  is_major = excluded.is_major,
  sort_order = excluded.sort_order,
  updated_at = timezone('utc', now());

create table if not exists public.engine_state_kv (
  key text primary key,
  value_json jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.runtime_events (
  id bigint generated always as identity primary key,
  created_at timestamptz not null default timezone('utc', now()),
  source text not null,
  level text not null,
  status text not null,
  error_text text,
  action text,
  detail text,
  meta_json jsonb not null default '{}'::jsonb
);

create index if not exists idx_runtime_events_created_at
  on public.runtime_events (created_at desc);

create index if not exists idx_runtime_events_source
  on public.runtime_events (source, created_at desc);

create table if not exists public.engine_heartbeat (
  engine_name text primary key,
  market text not null default 'crypto',
  last_seen_at timestamptz not null default timezone('utc', now()),
  last_cycle_started_at timestamptz,
  last_cycle_finished_at timestamptz,
  last_error text,
  version_sha text,
  host_name text,
  meta_json jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default timezone('utc', now())
);

drop trigger if exists trg_engine_heartbeat_updated_at on public.engine_heartbeat;
create trigger trg_engine_heartbeat_updated_at
before update on public.engine_heartbeat
for each row execute function public.set_updated_at();

create table if not exists public.engine_runtime_config (
  engine_name text primary key,
  market text not null default 'crypto' check (market = 'crypto'),
  captured_at timestamptz,
  trade_mode text not null default 'paper',
  autotrade_enabled boolean not null default false,
  live_execution_enabled boolean not null default false,
  demo_enable_macro boolean not null default false,
  live_enable_crypto boolean not null default false,
  autotrade_models jsonb not null default '[]'::jsonb,
  live_models jsonb not null default '[]'::jsonb,
  configured_symbols jsonb not null default '[]'::jsonb,
  scan_interval_seconds integer not null default 0,
  bybit_max_positions integer not null default 0,
  bybit_min_order_usd numeric(20, 8) not null default 0,
  bybit_order_pct_min numeric(10, 6) not null default 0,
  bybit_order_pct_max numeric(10, 6) not null default 0,
  bybit_leverage_min numeric(10, 4) not null default 0,
  bybit_leverage_max numeric(10, 4) not null default 0,
  crypto_min_entry_score numeric(10, 6) not null default 0,
  macro_rank_min integer not null default 0,
  macro_rank_max integer not null default 0,
  macro_trend_pool_size integer not null default 0,
  source_json jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default timezone('utc', now())
);

drop trigger if exists trg_engine_runtime_config_updated_at on public.engine_runtime_config;
create trigger trg_engine_runtime_config_updated_at
before update on public.engine_runtime_config
for each row execute function public.set_updated_at();

create table if not exists public.model_runtime_tunes (
  model_id text primary key check (model_id in ('A', 'B', 'C', 'D')),
  market text not null default 'crypto' check (market = 'crypto'),
  active_variant_id text not null default '',
  threshold numeric(10, 6) not null,
  tp_mul numeric(10, 6) not null,
  sl_mul numeric(10, 6) not null,
  next_eval_at timestamptz,
  last_eval_at timestamptz,
  last_eval_note_code text,
  last_eval_note_ko text,
  last_eval_closed integer not null default 0,
  last_eval_win_rate numeric(10, 6) not null default 0,
  last_eval_pnl_usd numeric(20, 8) not null default 0,
  last_eval_pf numeric(20, 8) not null default 0,
  source_json jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default timezone('utc', now())
);

drop trigger if exists trg_model_runtime_tunes_updated_at on public.model_runtime_tunes;
create trigger trg_model_runtime_tunes_updated_at
before update on public.model_runtime_tunes
for each row execute function public.set_updated_at();

create table if not exists public.model_tune_history (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default timezone('utc', now()),
  market text not null default 'crypto' check (market = 'crypto'),
  model_id text not null check (model_id in ('A', 'B', 'C', 'D')),
  model_name text not null,
  variant_id text not null,
  parent_variant_id text not null default '',
  tuned boolean not null default false,
  note_code text not null,
  note_ko text not null default '',
  closed_trades integer not null default 0,
  win_rate numeric(10, 6) not null default 0,
  pnl_usd numeric(20, 8) not null default 0,
  profit_factor numeric(20, 8) not null default 0,
  threshold_before numeric(10, 6) not null default 0,
  threshold_after numeric(10, 6) not null default 0,
  tp_mul_before numeric(10, 6) not null default 0,
  tp_mul_after numeric(10, 6) not null default 0,
  sl_mul_before numeric(10, 6) not null default 0,
  sl_mul_after numeric(10, 6) not null default 0,
  meta_json jsonb not null default '{}'::jsonb
);

create index if not exists idx_model_tune_history_model_created_at
  on public.model_tune_history (model_id, created_at desc);

create index if not exists idx_model_tune_history_variant
  on public.model_tune_history (variant_id, created_at desc);

create table if not exists public.model_setups (
  id uuid primary key default gen_random_uuid(),
  cycle_at timestamptz not null,
  market text not null default 'crypto' check (market = 'crypto'),
  symbol text not null references public.instruments(symbol),
  model_id text not null check (model_id in ('A', 'B', 'C', 'D')),
  timeframe text not null default '10m',
  side text not null default 'long' check (side in ('long', 'short')),
  score numeric(10, 6) not null default 0,
  threshold numeric(10, 6) not null default 0,
  confidence numeric(10, 6) not null default 0,
  entry_price numeric(20, 8),
  entry_zone_low numeric(20, 8),
  entry_zone_high numeric(20, 8),
  stop_loss_price numeric(20, 8),
  take_profit_price numeric(20, 8),
  target_price_1 numeric(20, 8),
  target_price_2 numeric(20, 8),
  target_price_3 numeric(20, 8),
  risk_reward numeric(10, 6),
  recommended_leverage numeric(10, 4),
  entry_ready boolean not null default false,
  setup_state text not null default 'planned',
  expires_at timestamptz,
  reason_text text,
  indicators_json jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  unique (cycle_at, symbol, model_id)
);

create index if not exists idx_model_setups_cycle_at
  on public.model_setups (cycle_at desc);

create index if not exists idx_model_setups_symbol_model
  on public.model_setups (symbol, model_id, cycle_at desc);

drop trigger if exists trg_model_setups_updated_at on public.model_setups;
create trigger trg_model_setups_updated_at
before update on public.model_setups
for each row execute function public.set_updated_at();

create table if not exists public.model_signal_audit (
  cycle_at timestamptz not null,
  market text not null default 'crypto' check (market = 'crypto'),
  model_id text not null check (model_id in ('A', 'B', 'C', 'D')),
  symbol text not null references public.instruments(symbol),
  strategy text not null default '',
  score numeric(10, 6) not null default 0,
  threshold numeric(10, 6) not null default 0,
  risk_reward numeric(10, 6) not null default 0,
  price_usd numeric(20, 8) not null default 0,
  entry_price numeric(20, 8) not null default 0,
  recommended_leverage numeric(10, 4) not null default 0,
  entry_ready boolean not null default false,
  above_threshold boolean not null default false,
  gate_ok boolean not null default false,
  symbol_allowed boolean not null default false,
  in_position boolean not null default false,
  reentry_blocked boolean not null default false,
  audit_status text not null default '',
  audit_reason text not null default '',
  setup_state text not null default '',
  expires_at timestamptz,
  reason_text text,
  indicators_json jsonb not null default '{}'::jsonb,
  source_json jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  primary key (cycle_at, market, model_id, symbol)
);

create index if not exists idx_model_signal_audit_model_cycle
  on public.model_signal_audit (model_id, cycle_at desc);

create index if not exists idx_model_signal_audit_status
  on public.model_signal_audit (audit_status, cycle_at desc);

drop trigger if exists trg_model_signal_audit_updated_at on public.model_signal_audit;
create trigger trg_model_signal_audit_updated_at
before update on public.model_signal_audit
for each row execute function public.set_updated_at();

create table if not exists public.positions (
  id uuid primary key default gen_random_uuid(),
  setup_id uuid references public.model_setups(id) on delete set null,
  market text not null default 'crypto' check (market = 'crypto'),
  symbol text not null references public.instruments(symbol),
  model_id text not null check (model_id in ('A', 'B', 'C', 'D')),
  side text not null default 'long' check (side in ('long', 'short')),
  status text not null default 'planned' check (status in ('planned', 'open', 'closed', 'canceled')),
  opened_at timestamptz,
  closed_at timestamptz,
  planned_entry_price numeric(20, 8),
  actual_entry_price numeric(20, 8),
  stop_loss_price numeric(20, 8),
  take_profit_price numeric(20, 8),
  target_price_1 numeric(20, 8),
  target_price_2 numeric(20, 8),
  target_price_3 numeric(20, 8),
  qty numeric(24, 8),
  notional_usd numeric(20, 8),
  leverage numeric(10, 4),
  fees_usd numeric(20, 8) not null default 0,
  funding_usd numeric(20, 8) not null default 0,
  realized_pnl_usd numeric(20, 8) not null default 0,
  unrealized_pnl_usd numeric(20, 8) not null default 0,
  max_drawdown_usd numeric(20, 8) not null default 0,
  close_reason text,
  exchange_order_id text,
  position_meta jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create index if not exists idx_positions_status
  on public.positions (status, opened_at desc nulls last);

create index if not exists idx_positions_symbol_model
  on public.positions (symbol, model_id, created_at desc);

drop trigger if exists trg_positions_updated_at on public.positions;
create trigger trg_positions_updated_at
before update on public.positions
for each row execute function public.set_updated_at();

create table if not exists public.daily_model_pnl (
  day date not null,
  market text not null default 'crypto' check (market = 'crypto'),
  model_id text not null check (model_id in ('A', 'B', 'C', 'D')),
  equity_usd numeric(20, 8) not null default 0,
  total_pnl_usd numeric(20, 8) not null default 0,
  realized_pnl_usd numeric(20, 8) not null default 0,
  unrealized_pnl_usd numeric(20, 8) not null default 0,
  win_rate numeric(10, 6) not null default 0,
  closed_trades integer not null default 0,
  report_commit_sha text,
  report_path text,
  source_json jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  primary key (day, market, model_id)
);

create index if not exists idx_daily_model_pnl_model_day
  on public.daily_model_pnl (model_id, day desc);

drop trigger if exists trg_daily_model_pnl_updated_at on public.daily_model_pnl;
create trigger trg_daily_model_pnl_updated_at
before update on public.daily_model_pnl
for each row execute function public.set_updated_at();

create table if not exists public.report_commits (
  id uuid primary key default gen_random_uuid(),
  report_day date not null unique,
  report_files jsonb not null default '[]'::jsonb,
  commit_sha text,
  pushed boolean not null default false,
  error_text text,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

drop trigger if exists trg_report_commits_updated_at on public.report_commits;
create trigger trg_report_commits_updated_at
before update on public.report_commits
for each row execute function public.set_updated_at();

alter table public.instruments enable row level security;
alter table public.engine_state_kv enable row level security;
alter table public.runtime_events enable row level security;
alter table public.engine_heartbeat enable row level security;
alter table public.engine_runtime_config enable row level security;
alter table public.model_runtime_tunes enable row level security;
alter table public.model_tune_history enable row level security;
alter table public.model_setups enable row level security;
alter table public.model_signal_audit enable row level security;
alter table public.positions enable row level security;
alter table public.daily_model_pnl enable row level security;
alter table public.report_commits enable row level security;

drop policy if exists instruments_auth_read on public.instruments;
create policy instruments_auth_read on public.instruments
for select to authenticated
using (true);

drop policy if exists engine_state_kv_auth_read on public.engine_state_kv;
create policy engine_state_kv_auth_read on public.engine_state_kv
for select to authenticated
using (true);

drop policy if exists runtime_events_auth_read on public.runtime_events;
create policy runtime_events_auth_read on public.runtime_events
for select to authenticated
using (true);

drop policy if exists engine_heartbeat_auth_read on public.engine_heartbeat;
create policy engine_heartbeat_auth_read on public.engine_heartbeat
for select to authenticated
using (true);

drop policy if exists engine_runtime_config_auth_read on public.engine_runtime_config;
create policy engine_runtime_config_auth_read on public.engine_runtime_config
for select to authenticated
using (true);

drop policy if exists model_runtime_tunes_auth_read on public.model_runtime_tunes;
create policy model_runtime_tunes_auth_read on public.model_runtime_tunes
for select to authenticated
using (true);

drop policy if exists model_tune_history_auth_read on public.model_tune_history;
create policy model_tune_history_auth_read on public.model_tune_history
for select to authenticated
using (true);

drop policy if exists model_setups_auth_read on public.model_setups;
create policy model_setups_auth_read on public.model_setups
for select to authenticated
using (true);

drop policy if exists model_signal_audit_auth_read on public.model_signal_audit;
create policy model_signal_audit_auth_read on public.model_signal_audit
for select to authenticated
using (true);

drop policy if exists positions_auth_read on public.positions;
create policy positions_auth_read on public.positions
for select to authenticated
using (true);

drop policy if exists daily_model_pnl_auth_read on public.daily_model_pnl;
create policy daily_model_pnl_auth_read on public.daily_model_pnl
for select to authenticated
using (true);

drop policy if exists report_commits_auth_read on public.report_commits;
create policy report_commits_auth_read on public.report_commits
for select to authenticated
using (true);

do $$
begin
  alter publication supabase_realtime add table public.engine_heartbeat;
exception
  when duplicate_object then null;
end $$;

do $$
begin
  alter publication supabase_realtime add table public.engine_runtime_config;
exception
  when duplicate_object then null;
end $$;

do $$
begin
  alter publication supabase_realtime add table public.model_runtime_tunes;
exception
  when duplicate_object then null;
end $$;

do $$
begin
  alter publication supabase_realtime add table public.model_setups;
exception
  when duplicate_object then null;
end $$;

do $$
begin
  alter publication supabase_realtime add table public.model_signal_audit;
exception
  when duplicate_object then null;
end $$;

do $$
begin
  alter publication supabase_realtime add table public.positions;
exception
  when duplicate_object then null;
end $$;

do $$
begin
  alter publication supabase_realtime add table public.daily_model_pnl;
exception
  when duplicate_object then null;
end $$;

do $$
begin
  alter publication supabase_realtime add table public.model_tune_history;
exception
  when duplicate_object then null;
end $$;

commit;
