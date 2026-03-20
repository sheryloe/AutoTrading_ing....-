# Automethemoney

Automethemoney는 Python 트레이딩 엔진, Supabase 상태 저장, Next.js 운영 콘솔, GitHub Pages 리포트, GitHub Actions 배치를 묶어 운용하는 자동매매 프로젝트입니다.

- Repository: `https://github.com/sheryloe/Automethemoney`
- GitHub Pages: `https://sheryloe.github.io/Automethemoney/`

## 현재 운영 기준 (2026-03-20)

- 유니버스: `CRYPTO_UNIVERSE_MODE=rank_lock` (시총 상위 1~20 고정 운용)
- 모델: `A/B/C/D` 동시 운용, `long/short` 시그널 생성
- 사이클: 1분 주기 (`SCAN_INTERVAL_SECONDS=60`)
- 스케줄: GitHub Actions `cloud-cycle` + 외부 cron 연계 구조 유지
- Supabase 보존 정책: 최근 7일 (`SUPABASE_HISTORY_RETENTION_DAYS=7`)
- Prune 주기: 6시간 (`SUPABASE_PRUNE_INTERVAL_SECONDS=21600`)

## 아키텍처

1. `scripts/run_batch_cycle.py`가 1회 사이클 실행
2. 엔진이 시그널/포지션/손익 상태 계산
3. 결과를 Supabase(`engine_heartbeat`, `model_setups`, `model_signal_audit`, `positions`, `daily_model_pnl`)에 동기화
4. Next.js 콘솔과 GitHub Pages가 Supabase를 조회해 대시보드 반영

## 런타임 설정 우선순위

설정 해석 우선순위는 아래와 같습니다.

1. ENV
2. runtime profile (`runtime_settings.json`)
3. 코드 기본값

즉, runtime profile이 있어도 ENV가 항상 우선합니다.

## 저장소 구조

- `src/`: 트레이딩 엔진 및 데이터/동기화 로직
- `scripts/`: 배치 실행, 리포트, 유지보수 스크립트
- `frontend/`: Next.js 운영 콘솔
- `docs/`: GitHub Pages 및 공개 데이터
- `wiki/`: 운영 문서

## 빠른 시작

### 1) Python 의존성 설치

```bash
pip install -r requirements.txt
```

### 2) 런타임 설정 준비

```bash
copy .env.example .env
copy runtime_settings.example.json runtime_settings.json
```

### 3) 단일 배치 사이클 실행

```bash
python scripts/run_batch_cycle.py
```

### 4) 웹 서버 실행 (로컬)

```bash
python web_app.py
```

### 5) Next.js 콘솔 실행 (선택)

```bash
cd frontend
npm install
npm run dev
```

## 운영 리셋

서비스 초기화는 기존 API 경로를 사용합니다.

- Endpoint: `POST /api/service/reset`
- Body:
  - `adminToken`
  - `seedUsdt` (예: `10000`)
  - `confirmText` (`"RESET FUTURES DEMO"`)

## Supabase 관련 문서

- `docs/SUPABASE_CORE_SCHEMA_20260315.sql`
- `docs/SUPABASE_SCHEMA_20260315.sql`
- `docs/SUPABASE_PATCH_TOP20_20260320.sql`
- `docs/VERCEL_SUPABASE_SETUP_20260315.md`

## 최근 반영 포인트

- Top20 확장 시 FK 안정화: `instruments` 선행 upsert
- 7일 보존 정책 + prune interval 도입
- 최신 사이클 count 집계 정확도 보정
- GitHub Pages 기준 문구 `3월 20일부터 계속 동작중` 반영

## 점검 체크리스트

- `cloud-cycle` 스케줄이 `*/1 * * * *`인지 확인
- heartbeat가 분 단위로 연속 갱신되는지 확인
- 최신 사이클에서 모델 A/B/C/D 신호가 정상 집계되는지 확인
- Supabase prune 후 최근 데이터가 유지되는지 확인