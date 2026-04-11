# 빠른 시작

> [Prev: Home](https://github.com/sheryloe/Automethemoney/wiki) | [Wiki Home](https://github.com/sheryloe/Automethemoney/wiki) | [Next: System Architecture](https://github.com/sheryloe/Automethemoney/wiki/System-Architecture)

---

이 페이지는 self-hosted runner 기준 최소 연결 순서만 제공합니다.

## 0) 사전 준비

- Supabase 프로젝트 생성 및 스키마 적용
- Vercel 프로젝트 생성
- GitHub 저장소 권한(Secrets/Actions) 확보
- Windows x64 호스트 1대(24/7) 준비

## 1) 시크릿 설정

### GitHub Secrets

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `SERVICE_MASTER_KEY`
- `BYBIT_API_KEY`
- `BYBIT_API_SECRET`

### Vercel Env

- `NEXT_PUBLIC_SUPABASE_URL`
- `NEXT_PUBLIC_SUPABASE_ANON_KEY`
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `SERVICE_MASTER_KEY`
- `SERVICE_ADMIN_TOKEN`

## 2) self-hosted runner 설치

```powershell
.\ops\setup-self-hosted-runner.ps1 -Repo "sheryloe/Automethemoney" -ReplaceExisting
```

## 3) 배치 1회 실행

```powershell
.\ops\run-once-cloud-cycle.ps1 -Repo "sheryloe/Automethemoney" -Workflow "cloud-cycle.yml" -Ref "main"
```

## 4) 상태 검증

```powershell
.\ops\verify-stack.ps1 -EnvFile ".\\.env" -LookbackHours 1
```

성공 기준:

- heartbeat 최신 갱신
- setup/audit recent count 증가
- `trade_mode=paper`
- `bybit_readonly_sync=true`

## 5) 운영 대시보드 확인

- Vercel 콘솔: `/`, `/models`, `/positions`, `/settings`
- GitHub Pages: `/index.html`, `/daily-pnl.html`