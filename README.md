# AI_Auto

AI_Auto는 상시 자동매매 엔진을 무작정 한 화면에 붙이는 프로젝트가 아니라, 운영자가 상태를 읽고 설정을 분리해 관리할 수 있도록 재구성한 서비스형 콘솔입니다.

![AI_Auto 운영 화면](docs/assets/screenshots/auto-trading-cover.png)

## 프로젝트 소개

현재 구조는 다음 전제를 기준으로 정리되어 있습니다.

- 메이저 5개 코인만 추적: `BTCUSDT`, `ETHUSDT`, `SOLUSDT`, `XRPUSDT`, `BNBUSDT`
- 밈 코인 제외
- 4개 모델이 각각 `entry / stop loss / target price`를 생성
- 10분 주기 분석
- 일별 PnL 기록
- 주간 autotune
- `Vercel + Supabase + Python 배치` 구조

## 현재 운영 구조

### 프론트

- `Vercel`에 운영자 콘솔 배포
- 콘솔 화면은 4개로 분리
  - `개요`
  - `모델 성과`
  - `포지션`
  - `설정`

### 저장소

- `Supabase`에 운영 상태 저장
- 저장 대상
  - 엔진 heartbeat
  - 최신 setup
  - 포지션 상태
  - 일별 PnL
  - runtime tune 상태
  - provider vault

### 실행

- `Python` 배치 러너가 주기적으로 실행
- `GitHub Actions` 또는 별도 워커가 실행 주체
- 실행 전 설정과 provider 키는 Supabase에서 읽음

## 화면 구조 안내

### 1. 개요

개요 화면은 지금 무슨 일이 벌어지고 있는지 빠르게 확인하는 용도입니다.

- 엔진 heartbeat
- 최근 PnL 합계
- 최근 사이클 상태
- 오픈 포지션 수
- 최근 signal 수

### 2. 모델 성과

모델 화면은 성과 비교에만 집중합니다.

- 모델별 PnL
- 승률
- 종료 거래 수
- autotune 상태
- 현재 튜닝 파라미터

### 3. 포지션

포지션 화면은 실행 데이터를 모읍니다.

- 오픈 포지션
- 최신 setup
- entry / SL / TP
- 최근 cycle 상태

### 4. 설정

설정 화면은 운영 입력 전용입니다.

- `Service control`
- provider vault
- execution target
- live arm
- runtime profile

## 실행 흐름

현재 실행 흐름은 아래와 같습니다.

1. 10분마다 배치가 메이저 5개 코인을 분석
2. 모델 A/B/C/D가 각각 진입 계획을 생성
3. 결과를 Supabase에 기록
4. 일별 PnL을 별도 리포트로 저장
5. 누적 성과를 기준으로 주간 autotune 수행

즉 `실시간 차트만 보는 UI`가 아니라 `신호 생성 -> 상태 기록 -> 결과 분석` 흐름을 운영하는 구조입니다.

## 배포 구조

### Vercel

- 운영자 콘솔 배포
- Service control 화면 제공
- Supabase 읽기/쓰기 API 라우트 제공

### Supabase

- 상태 저장
- provider vault 암호화 저장
- runtime profile 저장
- 대시보드 조회 원장 역할

### Python 배치

- 10분 주기 분석
- signal / setup 생성
- position 상태 갱신
- Supabase 동기화

## 빠른 시작

### 1. 저장소 준비

```bash
git clone <repo-url>
cd AI_Auto
```

### 2. Supabase 스키마 적용

- [Supabase 코어 스키마](docs/SUPABASE_CORE_SCHEMA_20260315.sql) 내용을 SQL Editor에 실행

### 3. Vercel 환경 변수 설정

필수 값:

- `NEXT_PUBLIC_SUPABASE_URL`
- `NEXT_PUBLIC_SUPABASE_ANON_KEY`
- `SUPABASE_URL`
- `SUPABASE_SECRET_KEY`
- `SERVICE_MASTER_KEY`
- `SERVICE_ADMIN_TOKEN`

### 4. GitHub Actions Secrets 설정

서비스형 구조 기준으로 필요한 값:

- `SUPABASE_URL`
- `SUPABASE_SECRET_KEY`
- `SERVICE_MASTER_KEY`

거래소 키는 GitHub Secrets에 직접 넣지 않고, 서비스 콘솔에서 저장합니다.

## 운영 시 주의사항

- `Service control`에서 Bybit 키를 저장해도 바로 실거래가 시작되지는 않습니다.
- `execution target`, `live execution flag`, `live crypto`, `arm`은 별도 단계입니다.
- 현재 단계는 `future crypto live order routing` 전 준비 구조이며, UI와 저장 흐름을 먼저 고정한 상태입니다.
- `SERVICE_MASTER_KEY`는 Vercel과 GitHub Actions에 같은 값으로 넣어야 합니다.

## 관련 문서

- [Supabase 코어 스키마](docs/SUPABASE_CORE_SCHEMA_20260315.sql)
- [Vercel + Supabase 운영 가이드](docs/VERCEL_SUPABASE_SETUP_20260315.md)
- [전략 리팩토링 기록](docs/strategy_refactor_20260308.md)

## 다음 단계 후보

- 서비스 콘솔 추가 개선
- 실제 라이브 라우팅 가드 강화
- 모델/포지션 차트 보강
- 운영 로그 상세화
- Supabase Realtime 연결 고도화
