# 배포와 시크릿 기준

> [Prev: Data State Reference](https://github.com/sheryloe/Automethemoney/wiki/Data-State-Reference) | [Wiki Home](https://github.com/sheryloe/Automethemoney/wiki) | [Next: Operations Guide](https://github.com/sheryloe/Automethemoney/wiki/Operations-Guide)

---

이 문서는 self-hosted runner 기준으로 시크릿 저장 위치와 배포 순서를 고정합니다.

## 시크릿 저장 위치

| 위치 | 필수 값 | 용도 |
| --- | --- | --- |
| GitHub Secrets | `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `SERVICE_MASTER_KEY`, `BYBIT_API_KEY`, `BYBIT_API_SECRET` | `cloud-cycle` 실행 |
| Vercel Env | `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `SERVICE_MASTER_KEY`, `SERVICE_ADMIN_TOKEN` | 콘솔 UI/API |
| 로컬 `.env` | `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | 운영 점검 스크립트 |

## cloud-cycle 고정값

- `runs-on: [self-hosted, windows, x64, automethemoney]`
- `TRADE_MODE=paper`
- `ENABLE_LIVE_EXECUTION=false`
- `LIVE_ENABLE_CRYPTO=false`
- `BYBIT_READONLY_SYNC=true`
- `BYBIT_SECRET_SOURCE=github`

## self-hosted runner 설치

관리자 PowerShell에서 실행:

```powershell
.\ops\setup-self-hosted-runner.ps1 -Repo "sheryloe/Automethemoney" -ReplaceExisting
```

설치 후 GitHub 저장소에서 라벨을 확인합니다.

- `self-hosted`
- `windows`
- `x64`
- `automethemoney`

## 배포 순서

1. GitHub Secrets 입력
2. Vercel Env 입력 후 재배포
3. self-hosted runner 서비스 시작
4. `cloud-cycle` 수동 1회 실행
5. `ops/verify-stack.ps1`로 DB 반영 확인