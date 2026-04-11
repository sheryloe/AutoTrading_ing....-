# Quick Start

> [Prev: Home](https://github.com/sheryloe/Automethemoney/wiki) | [Wiki Home](https://github.com/sheryloe/Automethemoney/wiki) | [Next: System Architecture](https://github.com/sheryloe/Automethemoney/wiki/System-Architecture)

---

This page is the minimum setup order for the hosted runtime.

## 0) Prerequisites

- Supabase project and schema ready
- Vercel project connected
- GitHub repository admin access (Secrets + Actions)
- `gh` CLI available for manual workflow runs

## 1) Configure secrets

### GitHub Secrets

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

## 2) Run one hosted cycle

```powershell
.\ops\run-once-cloud-cycle.ps1 -Repo "sheryloe/Automethemoney" -Workflow "cloud-cycle.yml" -Ref "main"
```

## 3) Verify state updates

```powershell
.\ops\verify-stack.ps1 -EnvFile ".\.env" -LookbackHours 1 -ExpectedRunner "github-hosted"
```

Success criteria:

- heartbeat updated recently
- `model_setups.recent > 0`
- `model_signal_audit.recent > 0`
- `meta_json.runner = github-hosted`
- `meta_json.trade_mode = paper`

## 4) Check operator surfaces

- Vercel console routes: `/`, `/models`, `/positions`, `/settings`
- GitHub Pages hub: `https://sheryloe.github.io/Automethemoney/`