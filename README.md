# Automethemoney

Automethemoney는 Python 트레이딩 엔진, Supabase 상태 원장, Next.js 운영 콘솔, GitHub Pages 리포트를 묶어 운용하는 자동매매 프로젝트입니다.

- Repository: `https://github.com/sheryloe/Automethemoney`
- GitHub Pages: `https://sheryloe.github.io/Automethemoney/`

## 현재 운영 기준 (2026-03-21)

- 유니버스: `CRYPTO_UNIVERSE_MODE=rank_lock` (시총 상위 1~20 고정 운용)
- 모델: `A/B/C/D` 동시 운용, `long/short` 시그널 생성
- 사이클: 1분 주기 (`SCAN_INTERVAL_SECONDS=60`)
- 데모 시드: 모델별 `10000 USDT`
- GitHub Actions: `cloud-cycle` 주기 실행

## 아키텍처

1. `scripts/run_batch_cycle.py` 또는 GitHub Actions가 1회 사이클 실행
2. 엔진이 시그널/포지션/손익 상태 계산
3. 결과를 Supabase(`engine_heartbeat`, `model_setups`, `positions`, `daily_model_pnl`)에 동기화
4. Next.js 콘솔과 GitHub Pages가 Supabase를 조회해 대시보드 반영

## 런타임 설정 우선순위

1. ENV
2. runtime profile (`runtime_settings.json`)
3. 코드 기본값

## 웹 UI 라우트

- Next.js 콘솔: `/`, `/models`, `/positions`, `/settings`
- Flask 운영 패널: `/models`, `/paper`, `/live`, `/settings`

## 데모 초기화 (권장: Supabase SQL Editor)

```sql
begin;

insert into public.engine_state_blobs (blob_key, payload_json)
values ('engine_state', '{}'::jsonb)
on conflict (blob_key) do nothing;

delete from public.positions;
delete from public.model_setups;
delete from public.daily_model_pnl;
delete from public.model_runtime_tunes;

update public.engine_state_blobs
set payload_json =
  jsonb_set(
    jsonb_set(
      jsonb_set(
        jsonb_set(
          jsonb_set(
            jsonb_set(
              jsonb_set(
                jsonb_set(coalesce(payload_json, '{}'::jsonb), '{cash_usd}', '10000'::jsonb, true),
                '{demo_seed_usdt}', '10000'::jsonb, true
              ),
              '{positions}', '{}'::jsonb, true
            ),
            '{trades}', '[]'::jsonb, true
          ),
          '{daily_pnl}', '[]'::jsonb, true
        ),
        '{last_cycle_ts}', '0'::jsonb, true
      ),
      '{last_wallet_sync_ts}', '0'::jsonb, true
    ),
    '{last_bybit_sync_ts}', '0'::jsonb, true
  )
where blob_key = 'engine_state';

commit;
```

## 저장소 구조

- `src/`: 트레이딩 엔진 및 데이터/동기화 로직
- `scripts/`: 배치 실행, 리포트, 유지보수 스크립트
- `frontend/`: Next.js 운영 콘솔
- `docs/`: GitHub Pages 및 공개 데이터
- `wiki/`: 운영 문서

## 빠른 시작 (로컬)

```bash
pip install -r requirements.txt
copy .env.example .env
copy runtime_settings.example.json runtime_settings.json
python scripts/run_batch_cycle.py
python web_app.py
```

## Daily PnL Pages

- `docs/data/daily_pnl/summary_4col.json`이 기본 소스
- `docs/daily-pnl.html`에서 일자 + A/B/C/D 누적 손익 표시

## Supabase 관련 문서

- `docs/SUPABASE_CORE_SCHEMA_20260315.sql`
- `docs/SUPABASE_SCHEMA_20260315.sql`
- `docs/SUPABASE_PATCH_TOP20_20260320.sql`
- `docs/VERCEL_SUPABASE_SETUP_20260315.md`

## Security Notes

- Flask control endpoints와 `/api/settings/secrets`는 `SERVICE_ADMIN_TOKEN` 필요
- `SUPABASE_SERVICE_ROLE_KEY`, `SERVICE_MASTER_KEY`, `SERVICE_ADMIN_TOKEN`는 클라이언트 코드에 노출 금지
