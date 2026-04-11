# Automethemoney

Automethemoney는 **Self-hosted Runner + Supabase + Vercel** 3계층으로 운영하는 paper 자동매매 프로젝트입니다.

- Repository: https://github.com/sheryloe/Automethemoney
- GitHub Pages: https://sheryloe.github.io/Automethemoney/

## 1) 운영 아키텍처 (고정 기준)

| 계층 | 역할 | 배포 위치 |
| --- | --- | --- |
| Self-hosted Runner | `cloud-cycle` 배치 실행, 시그널/포지션 계산 | Windows x64 (24/7) |
| Supabase | 상태 원장 (`engine_heartbeat`, `model_setups`, `positions`, `daily_model_pnl`) | Supabase |
| Vercel | 운영 콘솔 UI/API (`/`, `/models`, `/positions`, `/settings`) | Vercel |
| GitHub Pages | 운영문서 + 상태허브 + Daily PnL 공개 뷰 | `docs/` |

## 2) 필수 시크릿/환경 변수

### GitHub Secrets (`cloud-cycle`)

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `SERVICE_MASTER_KEY`
- `BYBIT_API_KEY`
- `BYBIT_API_SECRET`

### Vercel Environment Variables

- `NEXT_PUBLIC_SUPABASE_URL`
- `NEXT_PUBLIC_SUPABASE_ANON_KEY`
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `SERVICE_MASTER_KEY`
- `SERVICE_ADMIN_TOKEN`

### 로컬 `.env` (운영 스크립트 실행용)

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- 필요 시 `SERVICE_MASTER_KEY`

## 3) Self-hosted Runner 구축 (PowerShell)

```powershell
# 0) gh auth 확인
gh auth status

# 1) 러너 설치/등록/서비스 시작 (관리자 PowerShell)
.\ops\setup-self-hosted-runner.ps1 -Repo "sheryloe/Automethemoney" -ReplaceExisting
```

러너 라벨 기준:

- `self-hosted`
- `windows`
- `x64`
- `automethemoney`

`cloud-cycle.yml`은 위 라벨이 없으면 실행되지 않습니다.

## 4) 배포/실행 순서

```powershell
# 1) 의존성
python -m pip install --upgrade pip
pip install -r .\requirements.txt

# 2) 수동 1회 실행 (workflow_dispatch)
.\ops\run-once-cloud-cycle.ps1 -Repo "sheryloe/Automethemoney" -Workflow "cloud-cycle.yml" -Ref "main"

# 3) 스택 검증
.\ops\verify-stack.ps1 -EnvFile ".\\.env" -LookbackHours 1
```

## 5) 운영 검증 기준

정상 상태:

- `engine_heartbeat` 최신 갱신
- `model_setups.recent > 0`
- `model_signal_audit.recent > 0`
- `trade_mode=paper`
- `bybit_readonly_sync=true`
- 가능 환경에서는 `last_bybit_sync_ts > 0`

Bybit가 막힌 경우에도 배치는 계속 돌아야 하며, 원인은 heartbeat `meta_json.bybit_preflight_*`로 판단합니다.

## 6) 장애 대응 요약

1. 러너 오프라인: GitHub > Actions > Runners에서 상태 확인 후 서비스 재시작.
2. `last_bybit_sync_ts=0`: `bybit_preflight_public_status/auth_status`로 401/403/경로 문제 분리.
3. 데이터 미갱신: `ops/verify-stack.ps1` 실행 후 `model_setups`, `model_signal_audit`, `engine_heartbeat` 순서로 점검.

## 7) 주요 경로

- 배치 엔트리: `scripts/run_batch_cycle.py`
- 워크플로우: `.github/workflows/cloud-cycle.yml`
- 운영 스크립트: `ops/*.ps1`
- GitHub Pages: `docs/index.html`, `docs/daily-pnl.html`
- 위키 소스: `docs/wiki-src/*.md`