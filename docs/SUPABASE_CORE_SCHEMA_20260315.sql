begin;

create extension if not exists pgcrypto;

create table if not exists public.instruments (
  symbol text primary key,
  exchange text not null default 'bybit',
  is_active boolean not null default true,
  is_major boolean not null default true,
  sort_order integer not null default 100,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

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

create table if not exists public.engine_heartbeat (
  engine_name text primary key,
  market text not null default 'crypto',
  last_seen_at timestamptz,
  last_cycle_started_at timestamptz,
  last_cycle_finished_at timestamptz,
  last_error text,
  version_sha text,
  host_name text,
  meta_json jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.engine_state_blobs (
  blob_key text primary key,
  payload_json jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.service_secrets (
  provider text primary key,
  secret_ciphertext text not null,
  meta_json jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default timezone('utc', now())
);

create or replace function public.upsert_service_secret(
  p_provider text,
  p_payload jsonb,
  p_passphrase text,
  p_meta jsonb default '{}'::jsonb
)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  if coalesce(trim(p_provider), '') = '' then
    raise exception 'provider_required';
  end if;
  if coalesce(trim(p_passphrase), '') = '' then
    raise exception 'passphrase_required';
  end if;
  if p_payload is null then
    raise exception 'payload_required';
  end if;

  insert into public.service_secrets (
    provider,
    secret_ciphertext,
    meta_json,
    updated_at
  )
  values (
    trim(p_provider),
    encode(pgp_sym_encrypt(p_payload::text, p_passphrase, 'cipher-algo=aes256'), 'base64'),
    coalesce(p_meta, '{}'::jsonb),
    timezone('utc', now())
  )
  on conflict (provider) do update
  set
    secret_ciphertext = excluded.secret_ciphertext,
    meta_json = excluded.meta_json,
    updated_at = excluded.updated_at;
end;
$$;

create or replace function public.get_service_secret(
  p_provider text,
  p_passphrase text
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_cipher text;
  v_plain text;
begin
  if coalesce(trim(p_provider), '') = '' then
    raise exception 'provider_required';
  end if;
  if coalesce(trim(p_passphrase), '') = '' then
    raise exception 'passphrase_required';
  end if;

  select secret_ciphertext
  into v_cipher
  from public.service_secrets
  where provider = trim(p_provider);

  if v_cipher is null then
    return '{}'::jsonb;
  end if;

  v_plain := pgp_sym_decrypt(decode(v_cipher, 'base64'), p_passphrase);
  return coalesce(v_plain::jsonb, '{}'::jsonb);
end;
$$;

create or replace function public.delete_service_secret(
  p_provider text
)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  delete from public.service_secrets where provider = trim(coalesce(p_provider, ''));
end;
$$;

create table if not exists public.model_runtime_tunes (
  model_id text primary key,
  market text not null default 'crypto',
  active_variant_id text not null default '',
  threshold numeric(10, 6) not null default 0,
  tp_mul numeric(10, 6) not null default 0,
  sl_mul numeric(10, 6) not null default 0,
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

create table if not exists public.model_setups (
  id uuid primary key default gen_random_uuid(),
  cycle_at timestamptz not null,
  market text not null default 'crypto',
  symbol text not null,
  model_id text not null,
  timeframe text not null default '10m',
  side text not null default 'long',
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

create table if not exists public.positions (
  id uuid primary key,
  market text not null default 'crypto',
  symbol text not null,
  model_id text not null,
  side text not null default 'long',
  status text not null default 'open',
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
  position_meta jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.daily_model_pnl (
  day date not null,
  market text not null default 'crypto',
  model_id text not null,
  equity_usd numeric(20, 8) not null default 0,
  total_pnl_usd numeric(20, 8) not null default 0,
  realized_pnl_usd numeric(20, 8) not null default 0,
  unrealized_pnl_usd numeric(20, 8) not null default 0,
  win_rate numeric(10, 6) not null default 0,
  closed_trades integer not null default 0,
  source_json jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default timezone('utc', now()),
  primary key (day, market, model_id)
);

commit;
