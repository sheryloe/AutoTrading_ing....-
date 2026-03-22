-- Supabase storage control: pruning + scheduling

-- 1) Check top tables by size
select
  relname as table,
  pg_size_pretty(pg_total_relation_size(relid)) as total_size,
  pg_total_relation_size(relid) as bytes
from pg_catalog.pg_statio_user_tables
order by pg_total_relation_size(relid) desc
limit 20;

-- 2) Create prune function
create or replace function public.prune_automethemoney_history()
returns void
language plpgsql
as $$
begin
  delete from public.model_signal_audit
  where market = 'crypto'
    and cycle_at < (now() - interval '7 days');

  delete from public.model_setups
  where market = 'crypto'
    and cycle_at < (now() - interval '7 days');

  delete from public.runtime_events
  where created_at < (now() - interval '7 days');

  delete from public.model_tune_history
  where market = 'crypto'
    and created_at < (now() - interval '30 days');

  delete from public.positions
  where market = 'crypto'
    and status = 'closed'
    and closed_at < (now() - interval '30 days');
end;
$$;

-- 3) Schedule pruning every 6 hours
create extension if not exists pg_cron;

select
  cron.schedule(
    'automethemoney_prune',
    '0 */6 * * *',
    $$select public.prune_automethemoney_history();$$
  );

-- 4) Manual run (optional)
select public.prune_automethemoney_history();

-- 5) Verify cron job
select * from cron.job where jobname = 'automethemoney_prune';

-- 6) Emergency truncate (use only if storage is blocked)
-- truncate table public.model_signal_audit;
-- truncate table public.model_setups;