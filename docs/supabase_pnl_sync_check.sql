-- PnL 동기화 / 실시간 청산 즉시 진단
-- 목적: 엔진 heartbeat, 일간 PnL 갱신, open/close 동기화, close 이벤트 반영 상태를 즉시 확인
-- 실행 위치: Supabase SQL Editor (service role 권한)

-- 1) heartbeat 최신 상태(엔진 생존)
select
  engine_name,
  last_seen_at,
  to_char(last_seen_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') as last_seen_utc
from public.engine_heartbeat
order by last_seen_at desc
limit 20;

-- 2) 일별 PnL 최근 80행(모델별 누적값)
select
  day,
  model_id,
  equity_usd,
  total_pnl_usd,
  realized_pnl_usd,
  unrealized_pnl_usd,
  win_rate,
  closed_trades,
  source_json
from public.daily_model_pnl
where market = 'crypto'
order by day desc, model_id asc
limit 80;

-- 3) 오픈 포지션 목록(실시간 종료 동작 반영 확인)
select
  model_id,
  symbol,
  status,
  side,
  updated_at,
  opened_at,
  jsonb_pretty(position_meta) as position_meta
from public.positions
where status = 'open' and market = 'crypto'
order by updated_at desc
limit 200;

-- 4) 최근 체결 이벤트 10개(특히 close 포함 여부)
select
  updated_at,
  jsonb_pretty(payload_json->'rows') as rows
from public.engine_state_blobs
where blob_key = 'recent_crypto_trades'
order by updated_at desc
limit 10;

-- 5) 24시간 close 이벤트 집계
with src as (
  select
    jsonb_array_elements(payload_json->'rows') as r,
    updated_at
  from public.engine_state_blobs
  where blob_key = 'recent_crypto_trades'
    and updated_at >= now() - interval '24 hours'
)
select
  count(*) as events,
  count(*) filter (where r->>'event_kind' = 'close') as closed_events,
  max(updated_at) as latest_blob
from src;

-- 6) 모델별 최근 2개 라인 누적값 차이(실시간 갱신 Δ 계산용)
select
  model_id,
  day,
  realized_pnl_usd,
  realized_pnl_usd - lag(realized_pnl_usd) over(partition by model_id order by day desc) as realized_delta_prev_day,
  total_pnl_usd,
  total_pnl_usd - lag(total_pnl_usd) over(partition by model_id order by day desc) as total_delta_prev_day
from public.daily_model_pnl
where market='crypto'
  and model_id in ('A','B','C','D')
order by model_id, day desc
limit 80;

-- 7) 엔진 상태 blob 요약(가격 동기화/ stale 메타 추적)
select
  blob_key,
  updated_at,
  payload_json->>'market' as market,
  to_char(updated_at at time zone 'UTC', 'YYYY-MM-DD HH24:MI:SS') as updated_utc,
  jsonb_pretty(payload_json) as payload_json
from public.engine_state_blobs
where blob_key in ('engine_state', 'bybit_state', 'recent_crypto_trades')
order by updated_at desc;
