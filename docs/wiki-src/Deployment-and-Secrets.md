# Deployment and Secrets

> [Prev: Data State Reference](https://github.com/sheryloe/Automethemoney/wiki/Data-State-Reference) | [Wiki Home](https://github.com/sheryloe/Automethemoney/wiki) | [Next: Operations Guide](https://github.com/sheryloe/Automethemoney/wiki/Operations-Guide)

---

This page defines where each secret must live for the hosted runtime.

## Secret placement

| Location | Required values | Purpose |
| --- | --- | --- |
| GitHub Secrets | `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `SERVICE_MASTER_KEY`, `BYBIT_API_KEY`, `BYBIT_API_SECRET` | Hosted `cloud-cycle` execution |
| Vercel Env | `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `SERVICE_MASTER_KEY`, `SERVICE_ADMIN_TOKEN` | Console + API |
| Local `.env` | `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | Local verification scripts |

## Hosted workflow contract

- `runs-on: ubuntu-latest`
- `TRADE_MODE=paper`
- `ENABLE_LIVE_EXECUTION=false`
- `LIVE_ENABLE_CRYPTO=false`
- `BYBIT_READONLY_SYNC=true`
- `BYBIT_SECRET_SOURCE=github`
- `RUNNER_ROLE=github-hosted`

## Deploy order

1. Update GitHub secrets
2. Update Vercel environment variables and redeploy
3. Dispatch one `cloud-cycle` run manually
4. Validate with `ops/verify-stack.ps1`