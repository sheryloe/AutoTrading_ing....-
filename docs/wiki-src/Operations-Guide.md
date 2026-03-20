# 운영 가이드

> [Prev: Deployment and Secrets](https://github.com/sheryloe/Automethemoney/wiki/Deployment-and-Secrets) | [Wiki Home](https://github.com/sheryloe/Automethemoney/wiki) | [Next: Troubleshooting](https://github.com/sheryloe/Automethemoney/wiki/Troubleshooting)

---

이 문서는 실제 운영 중 자주 헷갈리는 기준을 정리한 페이지입니다.

## 운영 시작 전 체크리스트

- [ ] execution target이 `paper`인지 확인했다
- [ ] provider 키 저장이 끝났다
- [ ] 하드 리셋이 필요한지 먼저 판단했다
- [ ] `cloud-cycle`이 최근 1분 내에 한 번 이상 돌았다
- [ ] 모델별 데모 시드와 진입 비중이 현재 운영 의도와 맞다

## provider 키 관리

현재 provider 구조는 아래와 같습니다.

- `Bybit`: 선물 실행 자격증명
- `Binance`: 시장 데이터 소스
- `CoinGecko`: 시총 및 메타 데이터 보강

핵심 원칙:
- provider 키는 GitHub Secrets에 직접 두지 않음
- `/settings`의 Service control에서 저장
- Supabase vault에 암호화 저장

## futures demo 기준

현재 기본 운영 기준:

- 모델별 시드: `10000 USDT`
- 최대 포지션 수: `3`
- 진입 비중: `10% ~ 30%`
- 레버리지 프로필: `5x ~ 25x`

## runtime 저장과 리셋의 차이

| 작업 | 유지되는 것 | 초기화되는 것 |
| --- | --- | --- |
| runtime profile 저장 | 포지션, 누적 PnL, provider 자격증명 | 없음 |
| 하드 리셋 | provider 자격증명, runtime profile | 포지션, setup, 일별 PnL, runtime tune, 엔진 상태 |

## 하드 리셋 (SQL Editor)

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

## live 전환 가드

Bybit 키를 저장했다고 바로 live가 켜지는 구조가 아닙니다.

필요한 조건:
- execution target
- live execution flag
- crypto live enable
- arm
- 유효한 provider 키

즉 현재는 futures demo 운영 루프를 안정화하는 단계로 보는 것이 맞습니다.
