# 트러블슈팅

> [Prev: Operations Guide](https://github.com/sheryloe/Automethemoney/wiki/Operations-Guide) | [Wiki Home](https://github.com/sheryloe/Automethemoney/wiki)

---

self-hosted 운영에서 자주 발생하는 장애를 원인별로 정리합니다.

## 1) cloud-cycle가 실행되지 않음

점검 순서:

1. GitHub > Actions > Runners에서 runner가 online인지 확인
2. 라벨이 `self-hosted`, `windows`, `x64`, `automethemoney`인지 확인
3. Windows 서비스에서 Actions Runner 서비스 상태 확인

## 2) heartbeat는 갱신되는데 Bybit 동기화가 0

확인 키 (`engine_heartbeat.meta_json`):

- `bybit_preflight_public_status`
- `bybit_preflight_auth_status`
- `bybit_preflight_error`
- `last_bybit_sync_ts`

판정:

- `auth=401`: 키 자체 문제
- `public=403 + auth=200`: 공개 endpoint 경로 차단 가능
- `public=403 + auth=403`: 러너 네트워크/지역 차단 가능성 높음

## 3) 시그널/포지션이 증가하지 않음

```powershell
.\ops\verify-stack.ps1 -EnvFile ".\\.env" -LookbackHours 1
```

- `model_setups.recent=0` 또는 `model_signal_audit.recent=0`이면 배치 실행 경로부터 점검
- `gh run view --log`에서 Python 예외 확인

## 4) Pages와 README 설명이 다를 때

기준 문서 우선순위:

1. `README.md`
2. `docs/wiki-src/*.md`
3. `docs/index.html`

배포 전 3개를 동일 기준으로 맞춘 뒤 push합니다.