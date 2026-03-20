# 운영 콘솔 화면 안내

> [Prev: System Architecture](https://github.com/sheryloe/Automethemoney/wiki/System-Architecture) | [Wiki Home](https://github.com/sheryloe/Automethemoney/wiki) | [Next: Execution Flow](https://github.com/sheryloe/Automethemoney/wiki/Execution-Flow)

---

운영 콘솔은 한 화면에 모든 것을 몰아넣지 않고, 역할별로 분리된 4개 화면 구조를 기준으로 합니다.

## 화면 구성 요약

| 화면 | 무엇을 보는지 | 무엇을 하지 않는지 |
| --- | --- | --- |
| 개요 | heartbeat, 최근 KPI, 최근 사이클 | 긴 설정 폼, 세부 로그 표 |
| 모델 성과 | 모델별 PnL, 승률, autotune 상태 | provider 저장, 운영 입력 |
| 포지션 | 오픈 포지션, latest setup, intrabar 로그 | 환경 변수나 실행 타깃 수정 |
| 설정 | Service control, provider vault, 리셋 | 실시간 성과 비교 |

## 콘솔 스크린샷

![AI_Auto 콘솔 화면](https://sheryloe.github.io/Automethemoney/assets/screenshots/auto-trading-dashboard.png)

## 1. 개요

포함되는 정보:
- heartbeat
- 최근 손익 합계
- 최근 사이클 상태
- 오픈 포지션 수
- 모델 스냅샷

운영자가 개요에서 확인할 것:
- [ ] heartbeat가 최근 1분 내에 갱신되었는가
- [ ] 최근 손익이 급격히 깨지지 않았는가
- [ ] 오픈 포지션 수가 리스크 기준을 넘지 않는가

## 2. 모델 성과

포함되는 정보:
- 모델별 PnL
- 승률
- 종료 거래 수
- autotune 상태
- 모델 탭별 성과 테이블

운영자가 모델 성과에서 확인할 것:
- [ ] 어떤 모델이 최근 손익을 만들었는가
- [ ] 승률과 종료 거래 수가 극단적으로 흔들리지 않는가
- [ ] autotune 상태가 비어 있지 않은가

## 3. 포지션

포함되는 정보:
- 오픈 포지션
- 최신 setup
- entry / SL / TP
- intrabar 체결/종료 로그
- 모델별 포지션 탭

운영자가 포지션 화면에서 확인할 것:
- [ ] 오픈 포지션 수가 최대 기준을 넘지 않는가
- [ ] latest setup과 실제 체결 로그가 맞는가
- [ ] intrabar 종료가 예상과 다른 방식으로 처리되지 않았는가

## 4. 설정

포함되는 기능:
- Service control
- provider 자격증명 저장
- execution target
- live arm
- runtime profile
- 하드 리셋

설정 화면에서 주로 하는 작업:
- [ ] runtime profile 저장
- [ ] provider 자격증명 저장
- [ ] execution target과 intrabar 규칙 조정
- [ ] 하드 리셋 실행
