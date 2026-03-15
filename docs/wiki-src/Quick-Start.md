# 빠른 시작

> [Prev: Home](https://github.com/sheryloe/AutoTrading_ing....-/wiki) | [Wiki Home](https://github.com/sheryloe/AutoTrading_ing....-/wiki) | [Next: System Architecture](https://github.com/sheryloe/AutoTrading_ing....-/wiki/System-Architecture)

---

이 페이지는 AI_Auto를 새 환경에서 다시 붙일 때 가장 먼저 확인해야 하는 순서만 짧게 정리한 문서입니다.

## 시작 전 체크리스트

- [ ] Supabase 프로젝트와 코어 스키마가 준비되어 있다
- [ ] Vercel 프로젝트가 생성되어 있다
- [ ] GitHub Actions가 켜져 있다
- [ ] `/settings`에서 관리자 토큰으로 저장할 준비가 되어 있다

## 빠른 시작 다이어그램

```mermaid
flowchart LR
  A[Supabase 준비] --> B[Vercel env 입력]
  B --> C[/settings 저장]
  C --> D[GitHub Actions secrets 입력]
  D --> E[cloud-cycle 수동 실행]
  E --> F[heartbeat와 화면 확인]
```

> 빠른 시작은 연결 순서를 맞추는 것이 핵심입니다. 환경 변수, 설정 저장, 배치 실행을 섞지 않고 순서대로 진행하면 대부분의 초기 오류를 줄일 수 있습니다.

## 1. Supabase 준비

1. Supabase 프로젝트 생성
2. SQL Editor에서 코어 스키마 실행
3. `SUPABASE_URL`, `SUPABASE_SECRET_KEY`, `NEXT_PUBLIC_SUPABASE_ANON_KEY` 확보

관련 파일:
- 저장소의 `docs/SUPABASE_CORE_SCHEMA_20260315.sql`

## 2. Vercel 준비

필수 환경 변수:

- `NEXT_PUBLIC_SUPABASE_URL`
- `NEXT_PUBLIC_SUPABASE_ANON_KEY`
- `SUPABASE_URL`
- `SUPABASE_SECRET_KEY`
- `SERVICE_MASTER_KEY`
- `SERVICE_ADMIN_TOKEN`

| 항목 | 용도 | 비고 |
| --- | --- | --- |
| `NEXT_PUBLIC_SUPABASE_URL` | 브라우저에서 Supabase 엔드포인트 접근 | 공개 가능 |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | 브라우저 읽기용 키 | 공개 가능 |
| `SUPABASE_URL` | 서버 측 Supabase 접근 | 비공개 |
| `SUPABASE_SECRET_KEY` | 서버 측 쓰기/관리 권한 | 비공개 |
| `SERVICE_MASTER_KEY` | provider vault 암복호화 | Vercel과 GitHub Actions에서 동일해야 함 |
| `SERVICE_ADMIN_TOKEN` | `/settings` 저장 인증 | 운영자만 사용 |

배포 후 `/settings`에서 다음을 저장합니다.

- runtime profile
- execution target
- intrabar 충돌 규칙
- provider 자격증명

## 3. GitHub Actions 준비

Actions secrets:

- `SUPABASE_URL`
- `SUPABASE_SECRET_KEY`
- `SERVICE_MASTER_KEY`

워크플로우 권한:

- `Read and write permissions`

## 4. 첫 연결 순서

- [ ] Vercel env 입력 후 재배포
- [ ] `/settings`에서 관리자 토큰 입력
- [ ] runtime profile 저장
- [ ] Bybit / Binance / CoinGecko provider 저장
- [ ] 필요 시 하드 리셋
- [ ] `cloud-cycle` 수동 실행
- [ ] Supabase `engine_heartbeat`와 `/models`, `/positions` 확인

## 5. Service control 저장

설정 화면 `/settings`에서 아래 순서로 저장합니다.

1. 관리자 토큰 입력
2. runtime profile 저장
3. Bybit / Binance / CoinGecko provider 저장
4. 필요 시 하드 리셋 실행

## 6. 첫 배치 확인

- `cloud-cycle` 수동 실행
- Supabase `engine_heartbeat` 갱신 확인
- `/models`, `/positions` 화면 데이터 확인

## 권장 기본값

- execution target: `paper`
- 분석 주기: `480초`
- autotune 주기: `168시간`
- 진입 비중: `0.10 ~ 0.30`
- 모델별 데모 시드: `10000`
