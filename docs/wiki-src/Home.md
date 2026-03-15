# AI_Auto Wiki

AI_Auto는 Top 5 메이저 코인을 대상으로 8분 배치 futures demo를 운영하는 서비스형 자동매매 콘솔입니다. 이 위키는 README보다 더 운영자 시선에 맞춰, 실제로 어디부터 보고 무엇을 설정해야 하는지 빠르게 찾을 수 있도록 재구성한 문서 공간입니다.

## 이 위키에서 먼저 볼 페이지

- [[Quick-Start|빠른 시작]]
  - 새 환경에서 무엇부터 설정해야 하는지 바로 확인할 때
- [[System-Architecture|시스템 아키텍처]]
  - Vercel, Supabase, GitHub Actions, Python 배치의 역할을 한눈에 파악할 때
- [[Console-Screens|운영 콘솔 화면 안내]]
  - 개요, 모델 성과, 포지션, 설정 화면이 각각 무엇을 담당하는지 볼 때
- [[Execution-Flow|실행 흐름]]
  - 8분 배치, intrabar 체결, 일별 PnL, autotune이 어떻게 이어지는지 볼 때
- [[Operations-Guide|운영 가이드]]
  - provider 키 관리, 하드 리셋, futures demo 운영 기준을 확인할 때
- [[Blog-Series-Index|10단계 시리즈 인덱스]]
  - 프로젝트 리빌드 과정을 긴 글로 읽고 싶을 때

## 현재 핵심 기능

- Vercel 운영 콘솔: 개요 / 모델 성과 / 포지션 / 설정
- Supabase 상태 원장: heartbeat, setup, 포지션, 일별 PnL, 튜닝 상태, provider vault
- GitHub Actions 8분 배치
- 4개 planner 모델의 entry / TP / SL 제안
- 1분봉 intrabar 체결 시뮬레이션
- 모델별 데모 시드 10000 USDT 기준 선물 데모 운영
- 하드 리셋과 runtime 저장 정책 분리

## 현재 운영 기준 요약

- 추적 대상: `BTCUSDT`, `ETHUSDT`, `SOLUSDT`, `XRPUSDT`, `BNBUSDT`
- 밈 코인 제외
- 최대 동시 포지션 수: `3`
- 진입 비중: `10% ~ 30%`
- 모델별 레버리지 프로필: `5x ~ 25x`
- execution target 기본값: `paper`
- 배치 주기: `8분`

## 외부 링크

- [GitHub Pages 랜딩](https://sheryloe.github.io/AutoTrading_ing....-/)
- [10단계 HTML 시리즈](https://sheryloe.github.io/AutoTrading_ing....-/series/index.html)
- [저장소 README](https://github.com/sheryloe/AutoTrading_ing....-/blob/main/README.md)
