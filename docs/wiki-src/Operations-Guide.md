# 운영 가이드

이 문서는 실제 운영 중 자주 헷갈리는 기준을 정리한 페이지입니다.

## 운영 시작 전 체크리스트

- [ ] execution target이 `paper`인지 확인했다
- [ ] provider 키 저장이 끝났다
- [ ] 하드 리셋이 필요한지 먼저 판단했다
- [ ] `cloud-cycle`이 최근 8분 내에 한 번 이상 돌았다
- [ ] 모델별 데모 시드와 진입 비중이 현재 운영 의도와 맞는다

## 운영 화면 참고

![AI_Auto 운영 화면](https://sheryloe.github.io/AutoTrading_ing....-/assets/screenshots/auto-trading-cover.png)

> 운영 가이드는 화면 조작 방법보다 “지금 무엇을 건드려도 되는지”와 “무엇은 유지되는지”를 먼저 구분하는 데 초점을 둡니다.

## provider 키 관리

현재 provider 구조는 아래와 같습니다.

- `Bybit`: 선물 실행 자격증명
- `Binance`: 시장 데이터 소스
- `CoinGecko`: 시총 및 메타 데이터 보강

핵심 원칙:
- provider 키는 GitHub Secrets에 직접 두지 않음
- `/settings`의 Service control에서 저장
- Supabase vault에 암호화 저장

## futures demo 기준

현재 기본 운영 기준:

- 모델별 시드: `10000 USDT`
- 최대 포지션 수: `3`
- 진입 비중: `10% ~ 30%`
- 레버리지 프로필: `5x ~ 25x`

중요:
- 이 프로젝트는 현재 spot이 아니라 futures demo 기준
- 과거 spot 감각의 기록은 비교 기준으로 그대로 쓰지 않음

## runtime 저장과 리셋의 차이

| 작업 | 유지되는 것 | 초기화되는 것 |
| --- | --- | --- |
| runtime profile 저장 | 포지션, 누적 PnL, provider 자격증명 | 없음 |
| 하드 리셋 | provider 자격증명, runtime profile | 포지션, setup, 일별 PnL, runtime tune, 최근 체결 로그, 엔진 상태 |

### runtime profile 저장

- 현재 포지션 유지
- 누적 PnL 유지
- 다음 배치부터 새 규칙 반영

### 하드 리셋

초기화되는 것:
- 포지션
- setup
- 일별 PnL
- runtime tune
- 최근 체결 로그
- 엔진 상태

유지되는 것:
- provider 자격증명
- runtime profile

## live 전환 가드

Bybit 키를 저장했다고 바로 live가 켜지는 구조가 아닙니다.

필요한 조건:
- execution target
- live execution flag
- crypto live enable
- arm
- 유효한 provider 키

즉 현재는 futures demo 운영 루프를 안정화하는 단계로 보는 것이 맞습니다.
