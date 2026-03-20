begin;

create index if not exists idx_model_setups_cycle_at_desc_top20
  on public.model_setups (cycle_at desc);

create index if not exists idx_model_signal_audit_market_cycle_desc_top20
  on public.model_signal_audit (market, cycle_at desc);

create index if not exists idx_model_signal_audit_cycle_desc_top20
  on public.model_signal_audit (cycle_at desc);

commit;
