# AI_Auto Wiki

AI_Auto는 Top 5 메이저 코인을 대상으로 8분 배치 futures demo를 운영하는 서비스형 자동매매 콘솔입니다. 이 위키는 README보다 더 운영자 시선에 맞춰, 실제로 어디부터 보고 무엇을 설정해야 하는지 빠르게 찾을 수 있도록 재구성한 문서 공간입니다.

## 먼저 읽으면 좋은 순서

- [ ] [[Quick-Start|빠른 시작]]으로 기본 연결 순서 확인
- [ ] [[Runtime-Configuration|런타임 설정 레퍼런스]]에서 `/settings` 저장 항목 확인
- [ ] [[Operations-Guide|운영 가이드]]에서 리셋 정책과 futures demo 기준 확인
- [ ] [[Troubleshooting|트러블슈팅]]으로 자주 막히는 지점 빠르게 확인

## 화면 미리보기

![AI_Auto 운영 화면](https://sheryloe.github.io/AutoTrading_ing....-/assets/screenshots/auto-trading-cover.png)

> 개요, 모델 성과, 포지션, 설정을 따로 두고 운영 흐름에 맞게 읽는 구조를 기본 전제로 삼습니다.

## 문서별 용도 한눈에 보기

| 문서 | 언제 보는지 | 핵심 내용 |
| --- | --- | --- |
| [[Quick-Start|빠른 시작]] | 첫 연결 직전 | Supabase, Vercel, GitHub Actions 준비 순서 |
| [[System-Architecture|시스템 아키텍처]] | 구조를 설명해야 할 때 | Vercel / Supabase / Python 배치 / Actions 역할 분리 |
| [[Console-Screens|운영 콘솔 화면 안내]] | 화면 배치를 이해할 때 | 개요 / 모델 성과 / 포지션 / 설정의 책임 구분 |
| [[Runtime-Configuration|런타임 설정 레퍼런스]] | `/settings` 저장 전에 | 실행 타깃, 주기, 충돌 규칙, 심볼, 데이터 소스 기본값 |
| [[Models-and-Risk|모델과 리스크 기준]] | 모델별 성격을 설명할 때 | A/B/C/D 모델 성격, 시드, 비중, 레버리지 기준 |
| [[Data-State-Reference|데이터 저장 구조]] | 테이블을 찾아야 할 때 | Supabase 테이블과 UI 연결 관계 |
| [[Deployment-and-Secrets|배포와 시크릿 기준]] | 시크릿 위치가 헷갈릴 때 | Vercel env, GitHub Actions secrets, provider vault 구분 |
| [[Operations-Guide|운영 가이드]] | 실제 운영 중일 때 | provider 관리, runtime 저장, 하드 리셋 정책 |
| [[Troubleshooting|트러블슈팅]] | 오류가 났을 때 | 저장 실패, unauthorized, heartbeat, 중복 실행 점검 |

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
- [저장소 README](https://github.com/sheryloe/AutoTrading_ing....-/blob/main/README.md)
- [GitHub Wiki 홈](https://github.com/sheryloe/AutoTrading_ing....-/wiki)
