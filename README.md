# AI_Auto

AI_Auto는 Top 5 메이저 코인을 대상으로 8분 배치 futures demo를 운영하는 서비스형 자동매매 콘솔입니다.  
한 화면에 모든 것을 몰아넣는 대신, 운영자가 상태를 읽고 설정을 분리해 관리할 수 있도록 `개요 / 모델 성과 / 포지션 / 설정` 구조로 재구성했습니다.

![AI_Auto 운영 화면](docs/assets/screenshots/auto-trading-cover.png)

## 개요

- Top 5 메이저 코인만 추적: `BTCUSDT`, `ETHUSDT`, `SOLUSDT`, `XRPUSDT`, `BNBUSDT`
- 밈 코인 제외
- 4개 planner 모델이 `entry / stop loss / target price`를 생성
- 8분 배치 분석 + 1분 intrabar 체결 시뮬레이션
- 모델별 futures demo 시드 `10000 USDT`
- 진입 비중 `10% ~ 30%`, 최대 동시 포지션 `3`
- 일별 PnL 기록과 주간 autotune
- `Vercel + Supabase + Python 배치 + GitHub Actions` 구조

## 화면 구성

- `개요`
  - heartbeat
  - 최근 손익
  - 최근 사이클 상태
  - 오픈 포지션 수
- `모델 성과`
  - 모델별 PnL
  - 승률
  - 종료 거래 수
  - autotune 상태
- `포지션`
  - 오픈 포지션
  - 최신 setup
  - `entry / SL / TP`
  - intrabar 체결 및 종료 로그
- `설정`
  - provider 자격증명
  - execution target
  - runtime profile
  - 데모 하드 리셋

## 운영 기준

- 기본 execution target: `paper`
- 배치 주기: `8분`
- autotune 주기: `168시간`
- 기본 레버리지 프로필: `5x ~ 25x`
- runtime profile 저장은 현재 시드와 누적 성과를 초기화하지 않음
- 하드 리셋은 futures demo 상태를 명시적으로 비우고 새 시드 기준으로 다시 시작함

## 아키텍처

- `Vercel`
  - 운영 콘솔과 Service control API
- `Supabase`
  - 상태 원장
  - provider vault
  - runtime profile
- `Python 배치`
  - 모델 분석
  - intrabar 체결 판정
  - PnL 계산
  - autotune
- `GitHub Actions`
  - `cloud-cycle` 8분 주기 실행
  - 일일 리포트 자동화

## 빠른 시작

1. Supabase에 [코어 스키마](docs/SUPABASE_CORE_SCHEMA_20260315.sql)를 적용합니다.
2. Vercel 환경 변수를 설정합니다.
3. GitHub Actions secrets를 설정합니다.
4. `/settings`에서 runtime profile과 provider 자격증명을 저장합니다.
5. `cloud-cycle`을 실행해 heartbeat와 setup 반영을 확인합니다.

## 문서

상세 문서는 GitHub Wiki를 기준으로 관리합니다.

- [GitHub Wiki](https://github.com/sheryloe/AutoTrading_ing....-/wiki)
- [GitHub Pages 랜딩](https://sheryloe.github.io/AutoTrading_ing....-/)
- [운영 가이드](docs/VERCEL_SUPABASE_SETUP_20260315.md)
- [Supabase 코어 스키마](docs/SUPABASE_CORE_SCHEMA_20260315.sql)

## 주의사항

- Bybit 키를 저장했다고 바로 실거래가 시작되지는 않습니다.
- 현재 구조는 futures demo 운영이 기준이며, 실거래 전환은 별도 검증과 가드가 필요합니다.
- provider 키는 가능한 한 출금 권한 없는 전용 키를 사용하는 것을 권장합니다.
