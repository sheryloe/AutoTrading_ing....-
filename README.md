# AI_Auto

AI_Auto는 단일 자동매매 스크립트를 바로 실거래에 붙이는 프로젝트가 아니라, 운영자가 상태를 읽고 설정을 분리해서 관리할 수 있도록 재구성한 서비스형 선물 데모 트레이딩 콘솔입니다.

![AI_Auto 운영 화면](docs/assets/screenshots/auto-trading-cover.png)

## 프로젝트 소개

현재 기준 AI_Auto는 다음 전제를 중심으로 운영됩니다.

- 선물 기준 데모 운영
- Top 5 메이저 코인만 추적: `BTCUSDT`, `ETHUSDT`, `SOLUSDT`, `XRPUSDT`, `BNBUSDT`
- 밈 코인 제외
- 4개 planner 모델이 각각 `entry / stop loss / target price`를 생성
- 8분 배치 분석
- 최근 8분 구간은 1분 intrabar 체결 시뮬레이션으로 보강
- 일별 PnL 기록 및 Git 리포트
- 주간 autotune
- `Vercel + Supabase + Python 배치 + GitHub Actions` 구조

## 현재 핵심 기능

### 1. 운영자 콘솔

`Vercel`에 배포되는 콘솔은 역할별로 분리되어 있습니다.

- `개요`
  - heartbeat
  - 최근 PnL 합계
  - 최근 사이클 상태
  - 오픈 포지션 수
  - 모델 스냅샷
- `모델 성과`
  - 모델별 PnL
  - 승률
  - 종료 거래 수
  - autotune 상태
  - 모델 탭별 성과 비교
- `포지션`
  - 오픈 포지션
  - 최신 setup
  - entry / SL / TP
  - intrabar 체결/종료 로그
  - 모델 탭별 분리 조회
- `설정`
  - Service control
  - provider 자격증명 저장
  - execution target
  - live arm
  - runtime profile
  - 데모 하드 리셋

### 2. 모델 구조

현재 crypto 모델은 점수형 필터가 아니라 planner 구조입니다.

- `A. 레인지 리버전`
- `B. 리클레임`
- `C. 압축 돌파`
- `D. 리셋 바운스`

각 모델은 다음 값을 직접 제안합니다.

- 진입가
- 진입 구간
- 손절가
- 목표가 1/2/3
- 신뢰도
- 권장 레버리지 범위

### 3. 선물 데모 운영 기준

현재 기본 운영 기준은 아래와 같습니다.

- 모델별 데모 시드: `10000 USDT`
- 최대 동시 포지션 수: `3`
- 포지션 진입 비중: `10% ~ 30%`
- 모델별 레버리지 프로필: `5x ~ 25x`
- execution target 기본값: `paper`
- 하드 리셋 전까지 시드와 누적 성과는 유지

중요한 점은 `런타임 프로필 저장 = 시드 초기화`가 아니라는 점입니다.

- 런타임 프로필 저장: 현재 상태 유지, 다음 사이클부터 새 설정 반영
- 하드 리셋: 포지션, setup, 일별 PnL, runtime tune, 엔진 상태를 초기화하고 새 시드 기준으로 다시 시작

### 4. 8분 배치 + intrabar 체결

AI_Auto는 틱 기반 실시간 엔진이 아니라, 운영 가능한 배치 구조를 기준으로 설계되어 있습니다.

- GitHub Actions가 `8분`마다 배치 실행
- 각 배치에서 Top 5 메이저 코인을 분석
- planner 모델이 setup 생성
- 최근 구간의 `1분봉 high / low`를 다시 읽어 intrabar 체결 여부 판정
- 같은 캔들에서 TP와 SL이 모두 닿으면 충돌 규칙으로 판정
  - `conservative`: SL 우선
  - `neutral`: open 기준 더 가까운 쪽 우선
  - `aggressive`: TP 우선

즉 현재가가 배치 시점에는 원위치여도, 그 사이 구간에서 entry / TP / SL을 찍었다면 데모 체결과 PnL에 반영될 수 있습니다.

### 5. provider vault

거래소 키와 provider 키는 GitHub Secrets에 직접 두지 않고, 서비스 콘솔을 통해 Supabase vault에 저장합니다.

현재 지원 provider:

- `Bybit`
  - 선물 실행용 자격증명 저장
- `Binance`
  - 시장 데이터 소스
- `CoinGecko`
  - 시총 및 메타 데이터 보강

이 구조의 목적은 운영자가 웹 콘솔에서 키를 관리하고, 배치 러너는 Supabase vault에서 읽도록 책임을 분리하는 것입니다.

### 6. 상태 저장과 리포트

`Supabase`에는 다음 상태가 저장됩니다.

- engine heartbeat
- 최신 model setup
- 포지션 상태
- 일별 PnL
- runtime tune 상태
- provider vault
- runtime profile
- service state blob

추가로:

- 일별 PnL 리포트 생성
- Git 자동 커밋/푸시
- 주간 autotune 기록

## 실행 흐름

현재 운영 흐름은 아래와 같습니다.

1. GitHub Actions `cloud-cycle`이 8분마다 실행
2. 배치 러너가 Supabase에서 runtime profile과 provider vault를 읽음
3. Top 5 메이저 코인 선물 기준으로 4개 모델 분석 수행
4. 모델별 setup 생성
5. 최근 구간 intrabar 체결 여부 판정
6. 포지션과 일별 PnL 갱신
7. 결과를 Supabase와 리포트 파일에 저장
8. 주간 autotune 시점이면 튜닝 수행

## 배포 구조

### Vercel

- 운영자 콘솔 배포
- `/`, `/models`, `/positions`, `/settings` 화면 제공
- Service control API 라우트 제공

### Supabase

- 상태 저장 원장
- provider vault 암호화 저장
- runtime profile 저장
- 대시보드 조회 데이터 제공

### Python 배치

- planner 모델 분석
- intrabar 체결 시뮬레이션
- PnL 계산
- 일별 리포트와 autotune 처리

### GitHub Actions

- 8분 주기 배치 실행
- daily report commit/push
- 주간 운영 루프 실행

## 빠른 시작

### 1. Supabase 스키마 적용

아래 SQL을 Supabase SQL Editor에서 실행합니다.

- [Supabase 코어 스키마](docs/SUPABASE_CORE_SCHEMA_20260315.sql)

### 2. Vercel 환경 변수 설정

필수 값:

- `NEXT_PUBLIC_SUPABASE_URL`
- `NEXT_PUBLIC_SUPABASE_ANON_KEY`
- `SUPABASE_URL`
- `SUPABASE_SECRET_KEY`
- `SERVICE_MASTER_KEY`
- `SERVICE_ADMIN_TOKEN`

### 3. GitHub Actions Secrets 설정

서비스형 구조 기준 필수 값:

- `SUPABASE_URL`
- `SUPABASE_SECRET_KEY`
- `SERVICE_MASTER_KEY`

### 4. Service control 설정

`/settings`에서 아래를 저장합니다.

- runtime profile
- execution target
- provider 자격증명
- intrabar 충돌 규칙

### 5. cloud-cycle 실행

GitHub Actions에서 `cloud-cycle`을 수동 실행해 첫 heartbeat와 setup이 정상 반영되는지 확인합니다.

## 운영 시 주의사항

- `Bybit 키 저장`만으로 실거래가 바로 시작되지는 않습니다.
- live execution은 `execution target`, `live flag`, `crypto live`, `arm`을 별도로 통과해야 합니다.
- 현재 구조는 `futures demo` 중심이며, 실거래 전환은 체크리스트 기반으로 따로 검증해야 합니다.
- `SERVICE_MASTER_KEY`는 Vercel과 GitHub Actions에 동일한 값으로 설정해야 합니다.
- provider 키는 가급적 출금 권한 없는 전용 키를 사용해야 합니다.

## 문서와 링크

- [GitHub Pages 랜딩](https://sheryloe.github.io/AutoTrading_ing....-/)
- [10단계 블로그 시리즈](https://sheryloe.github.io/AutoTrading_ing....-/series/index.html)
- [GitHub Wiki](https://github.com/sheryloe/AutoTrading_ing....-/wiki)
- [운영 가이드](docs/VERCEL_SUPABASE_SETUP_20260315.md)
- [Supabase 코어 스키마](docs/SUPABASE_CORE_SCHEMA_20260315.sql)
- [전략 리팩터링 기록](docs/strategy_refactor_20260308.md)

## 현재 프로젝트 성격

AI_Auto는 지금 시점에서 아래 둘을 동시에 만족시키는 방향으로 정리돼 있습니다.

- 운영자가 상태를 빠르게 읽을 수 있는 콘솔
- 전략 실험과 결과 기록이 이어지는 선물 데모 운영 루프

즉 “신호만 보여주는 자동매매 UI”가 아니라, 설정과 결과와 리셋 정책까지 분리해 다루는 운영형 프로젝트입니다.
