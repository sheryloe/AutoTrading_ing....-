# AutoTrading 1단계: 자동매매를 기능보다 운영 리스크로 다시 설계한 이유

- 권장 슬러그: `auto-trading-step-1-ops-risk-design`
- SEO 설명: `자동매매 프로젝트를 기능 확장보다 운영 리스크 관점에서 다시 설계한 이유를 정리한 1단계 글입니다.`
- 핵심 키워드: `자동매매 대시보드`, `운영 리스크`, `crypto auto trading`, `runtime config`, `Flask trading app`
- 대표 이미지 ALT: `자동매매 운영 리스크와 런타임 설계 메모`

## 들어가며

자동매매 프로젝트를 만지다 보면 전략보다 먼저 무서운 것이 있습니다. 바로 상태 파일이 꼬이거나, 리셋이 잘못 눌리거나, 어떤 모델이 어떤 기준으로 돌고 있는지 모르게 되는 순간입니다. 그래서 이 프로젝트는 기능 추가보다 운영 리스크를 줄이는 구조를 먼저 잡는 쪽에 더 집중했습니다.

## 이번 단계에서 집중한 문제

- 밈과 크립토 상태를 같은 키에 섞지 않고 분리해야 했습니다.
- 리셋, 상태 파일, 모델 파일 경로를 런타임 설정으로 분리할 필요가 있었습니다.
- 실험 코드가 아니라 운영 콘솔이라는 관점에서 설정 파일과 README를 다시 봐야 했습니다.

## 이렇게 코드를 반영했다

### 1. 런타임 설정을 환경 변수 중심으로 정리
- 파일: `.env.example`
- 왜 넣었는가: 운영 키를 한 곳에서 보이게 해야 어떤 조합으로 돌고 있는지 빠르게 점검할 수 있기 때문입니다.

```bash
TRADE_MODE=paper
APP_PORT=8099
STATE_FILE=state.json
RUNTIME_SETTINGS_FILE=runtime_settings.local.json
ALLOW_DEMO_RESET=false
```

### 2. 상태/모델/런타임 파일 경로를 Settings로 묶은 부분
- 파일: `src/config.py`
- 왜 넣었는가: 실행 중 어떤 파일이 진짜 상태인지 코드에서 분명히 보여 줘야 장애 시 복구 포인트를 설명할 수 있습니다.

```python
state_file=_to_str(data.get("STATE_FILE"), "state.json")
runtime_settings_file=_to_str(data.get("RUNTIME_SETTINGS_FILE"), "runtime_settings.json")
model_file=_to_str(data.get("MODEL_FILE"), "model_online.json")
```

## 적용 결과

- 자동매매 실험을 `운영 가능한 콘솔`로 설명할 기준이 생겼습니다.
- 런타임 파일 구조와 리셋 정책이 문서화 가능한 형태로 정리됐습니다.
- Step 2에서 대시보드 제어 레이어를 붙일 준비가 완료됐습니다.

## 티스토리 SEO 정리 포인트

- 자동매매 글은 수익보다 안전장치를 먼저 설명해야 신뢰도가 높습니다.
- 환경 변수 캡처나 설정 테이블 이미지를 초반에 넣으면 이해가 빠릅니다.
- `운영 리스크`라는 표현이 글 차별화 포인트로 잘 작동합니다.

## 마무리

이 단계에서 가장 중요했던 것은 멋진 전략이 아니라 망가졌을 때 복구 가능한 구조를 만드는 일이었습니다.
