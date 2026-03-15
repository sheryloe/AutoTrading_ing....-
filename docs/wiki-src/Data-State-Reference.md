# 데이터 저장 구조

AI_Auto는 Supabase를 상태 원장으로 사용합니다. 각 테이블은 운영자가 화면에서 보는 데이터와 배치가 기록하는 데이터를 연결하는 역할을 맡습니다.

## 핵심 테이블

| 테이블 | 용도 | 주로 보는 화면 |
| --- | --- | --- |
| `public.instruments` | 추적 심볼 목록 | 공통 |
| `public.engine_heartbeat` | 배치 생존 상태와 마지막 오류 | 개요 |
| `public.engine_state_blobs` | 엔진 상태 blob 저장 | 운영/리셋 참고 |
| `public.service_secrets` | provider vault 암호화 저장 | 설정 |
| `public.model_runtime_tunes` | 모델별 현재 튜닝 상태 | 모델 성과 |
| `public.model_setups` | 최근 setup 기록 | 포지션 |
| `public.positions` | 오픈·종료 포지션 상태 | 포지션 |
| `public.daily_model_pnl` | 모델별 일별 손익 집계 | 모델 성과 |

## 테이블별 핵심 컬럼

| 테이블 | 중요 컬럼 예시 | 설명 |
| --- | --- | --- |
| `engine_heartbeat` | `last_seen_at`, `last_error`, `updated_at` | 배치가 최근에 돌았는지 확인 |
| `service_secrets` | `provider`, `secret_ciphertext`, `meta_json` | provider 자격증명 저장 |
| `model_runtime_tunes` | `threshold`, `tp_mul`, `sl_mul`, `updated_at` | autotune 이후 현재 값 |
| `model_setups` | `symbol`, `model_id`, `entry_price`, `stop_loss_price`, `target_price_1` | 모델이 제안한 계획 |
| `positions` | `status`, `actual_entry_price`, `realized_pnl_usd`, `updated_at` | 실제 데모 체결 상태 |
| `daily_model_pnl` | `day`, `model_id`, `equity_usd`, `realized_pnl_usd` | 일별 모델 성과 |

## 저장 원칙

- runtime profile은 별도 상태 blob으로 관리합니다
- provider 자격증명은 `service_secrets`에 암호화해 저장합니다
- heartbeat, setup, 포지션, PnL, runtime tune은 화면에서 바로 읽을 수 있게 테이블로 분리합니다

## 화면과 데이터 연결

| 화면 | 주로 읽는 데이터 |
| --- | --- |
| `/` | `engine_heartbeat`, 최근 PnL 요약 |
| `/models` | `daily_model_pnl`, `model_runtime_tunes` |
| `/positions` | `positions`, `model_setups` |
| `/settings` | runtime profile, provider vault 메타 정보 |

## 확인 체크리스트

- [ ] heartbeat가 갱신되는지 먼저 본다
- [ ] 모델 성과가 비면 `daily_model_pnl` 적재 여부를 확인한다
- [ ] 포지션 로그가 비면 `model_setups`와 `positions`를 함께 본다
- [ ] provider 저장 문제는 `service_secrets`와 runtime 저장 문제를 분리해서 본다
