# AI_Auto Wiki

> [Wiki Home](https://github.com/sheryloe/Automethemoney/wiki) | [Next: Quick Start](https://github.com/sheryloe/Automethemoney/wiki/Quick-Start)

---

AI_Auto는 crypto futures demo를 기준으로 운영되는 자동매매 콘솔입니다. 이 위키는 README보다 운영자 관점에 맞춰, 설정 순서와 점검 기준만 빠르게 찾을 수 있도록 정리했습니다.

## 먼저 읽으면 좋은 순서

- [ ] [빠른 시작](https://github.com/sheryloe/Automethemoney/wiki/Quick-Start)
- [ ] [런타임 설정 레퍼런스](https://github.com/sheryloe/Automethemoney/wiki/Runtime-Configuration)
- [ ] [운영 가이드](https://github.com/sheryloe/Automethemoney/wiki/Operations-Guide)
- [ ] [트러블슈팅](https://github.com/sheryloe/Automethemoney/wiki/Troubleshooting)

## 화면 미리보기

![AI_Auto 운영 화면](https://sheryloe.github.io/Automethemoney/assets/screenshots/auto-trading-cover.png)

> 개요, 모델 성과, 포지션, 설정을 분리해 읽는 구조를 기본 전제로 삼습니다.

## 문서별 용도 한눈에 보기

| 문서 | 언제 보는지 | 핵심 내용 |
| --- | --- | --- |
| [빠른 시작](https://github.com/sheryloe/Automethemoney/wiki/Quick-Start) | 첫 연결 직전 | Supabase, Vercel, GitHub Actions 준비 순서 |
| [시스템 아키텍처](https://github.com/sheryloe/Automethemoney/wiki/System-Architecture) | 구조 설명이 필요할 때 | Vercel / Supabase / Python 배치 / Actions 역할 분리 |
| [운영 콘솔 화면 안내](https://github.com/sheryloe/Automethemoney/wiki/Console-Screens) | 화면 배치를 이해할 때 | 개요 / 모델 성과 / 포지션 / 설정의 책임 구분 |
| [런타임 설정 레퍼런스](https://github.com/sheryloe/Automethemoney/wiki/Runtime-Configuration) | `/settings` 저장 전 | 실행 타깃, 주기, 충돌 규칙, 심볼 기준 |
| [모델과 리스크 기준](https://github.com/sheryloe/Automethemoney/wiki/Models-and-Risk) | 모델 성격 설명 | A/B/C/D 모델 성격, 시드, 비중, 레버리지 |
| [데이터 저장 구조](https://github.com/sheryloe/Automethemoney/wiki/Data-State-Reference) | 테이블이 필요할 때 | Supabase 테이블과 UI 연결 |
| [배포와 시크릿 기준](https://github.com/sheryloe/Automethemoney/wiki/Deployment-and-Secrets) | 시크릿 위치 확인 | Vercel env, GitHub Actions secrets, provider vault 구분 |
| [운영 가이드](https://github.com/sheryloe/Automethemoney/wiki/Operations-Guide) | 실제 운영 중 | provider 관리, runtime 저장, 하드 리셋 |
| [트러블슈팅](https://github.com/sheryloe/Automethemoney/wiki/Troubleshooting) | 오류 발생 시 | 저장 실패, unauthorized, heartbeat 점검 |

## 현재 핵심 기능

- Vercel 운영 콘솔: 개요 / 모델 성과 / 포지션 / 설정
- Supabase 상태 원장: heartbeat, setup, 포지션, 일별 PnL, 튜닝 상태, provider vault
- GitHub Actions 배치 실행
- 4개 planner 모델의 entry / TP / SL 제안
- 모델별 데모 시드 10000 USDT 기준 운영
- 하드 리셋과 runtime 저장 정책 분리

## 현재 운영 기준 요약

- 유니버스: `CRYPTO_UNIVERSE_MODE=rank_lock` (Top 1~20)
- 사이클: `SCAN_INTERVAL_SECONDS=60`
- execution target 기본값: `paper`
- 데모 시드: `10000 USDT`
- 최대 동시 포지션 수: `3`
- 진입 비중: `10% ~ 30%`

## 외부 링크

- [GitHub Pages 랜딩](https://sheryloe.github.io/Automethemoney/)
- [저장소 README](https://github.com/sheryloe/Automethemoney/blob/main/README.md)
- [GitHub Wiki 홈](https://github.com/sheryloe/Automethemoney/wiki)
