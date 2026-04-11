# 운영 가이드

> [Prev: Deployment and Secrets](https://github.com/sheryloe/Automethemoney/wiki/Deployment-and-Secrets) | [Wiki Home](https://github.com/sheryloe/Automethemoney/wiki) | [Next: Troubleshooting](https://github.com/sheryloe/Automethemoney/wiki/Troubleshooting)

---

운영 루틴은 아래 3개 PowerShell 스크립트를 기준으로 고정합니다.

## 기본 운영 명령

```powershell
# 1) cloud-cycle 수동 1회
.\ops\run-once-cloud-cycle.ps1 -Repo "sheryloe/Automethemoney" -Workflow "cloud-cycle.yml" -Ref "main"

# 2) 스택 검증
.\ops\verify-stack.ps1 -EnvFile ".\\.env" -LookbackHours 1

# 3) Bybit 동기화 필수 검증
.\ops\verify-stack.ps1 -EnvFile ".\\.env" -LookbackHours 1 -RequireBybitSync
```

## 정상 판정 기준

- `engine_heartbeat` 최근 갱신
- `model_setups.recent > 0`
- `model_signal_audit.recent > 0`
- `meta_json.trade_mode = paper`
- `meta_json.bybit_readonly_sync = true`
- 접근 가능 환경이면 `meta_json.last_bybit_sync_ts > 0`

## 하드 리셋 후 재시작 체크

1. 리셋 실행
2. `cloud-cycle` 수동 1회 실행
3. 2~3분 대기 후 `verify-stack.ps1` 실행
4. GitHub Pages `index.html`, `daily-pnl.html`에서 상태/손익 확인

## 자주 보는 운영 지표

- `positions(status=open)` 오픈 수
- 모델별 최신 cycle 신호 수
- `daily_model_pnl` 최신 일자 누적/실현 값
- Bybit preflight 상태코드 (`public/auth`)