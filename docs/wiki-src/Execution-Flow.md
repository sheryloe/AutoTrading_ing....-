# 실행 흐름

현재 AI_Auto는 8분 배치 기반의 futures demo 운영 루프를 기준으로 돌아갑니다.

## 전체 흐름

1. GitHub Actions `cloud-cycle` 실행
2. Supabase에서 runtime profile과 provider vault 로드
3. Top 5 메이저 코인 선물 데이터 수집
4. 4개 planner 모델 분석 수행
5. setup 생성
6. 최근 8분 구간의 1분봉으로 intrabar 체결 여부 판정
7. 포지션과 일별 PnL 갱신
8. Supabase에 결과 저장
9. 주간 autotune 시점이면 파라미터 조정

## planner 모델이 만드는 값

각 모델은 최소한 아래 값을 만듭니다.

- entry price
- entry zone
- stop loss
- target price 1 / 2 / 3
- confidence
- leverage profile

## intrabar 체결이 필요한 이유

배치 시점 현재가만 보면, 배치 사이 구간에서 실제로 entry / TP / SL을 찍고 지나간 경우를 놓칠 수 있습니다. 그래서 최근 8분 구간은 1분봉 high / low를 다시 읽어 아래를 판정합니다.

- entry 터치 여부
- entry 이후 TP / SL 터치 여부
- 같은 캔들 충돌 시 어떤 규칙으로 처리할지

## 충돌 규칙

현재 지원 규칙:

- `conservative`: SL 우선
- `neutral`: open 기준 더 가까운 쪽 우선
- `aggressive`: TP 우선

이 규칙은 `/settings`에서 저장되며, 포지션 로그 해석에 직접 영향을 줍니다.
