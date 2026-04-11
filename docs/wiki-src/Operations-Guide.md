# Operations Guide

> [Prev: Deployment and Secrets](https://github.com/sheryloe/Automethemoney/wiki/Deployment-and-Secrets) | [Wiki Home](https://github.com/sheryloe/Automethemoney/wiki) | [Next: Troubleshooting](https://github.com/sheryloe/Automethemoney/wiki/Troubleshooting)

---

Use the hosted operation loop below.

## Core commands (PowerShell)

```powershell
# 1) dispatch one hosted cycle
.\ops\run-once-cloud-cycle.ps1 -Repo "sheryloe/Automethemoney" -Workflow "cloud-cycle.yml" -Ref "main"

# 2) verify state writes
.\ops\verify-stack.ps1 -EnvFile ".\.env" -LookbackHours 1 -ExpectedRunner "github-hosted"

# 3) strict check (requires bybit sync timestamp > 0)
.\ops\verify-stack.ps1 -EnvFile ".\.env" -LookbackHours 1 -ExpectedRunner "github-hosted" -RequireBybitSync
```

## Expected healthy state

- heartbeat updated in the current window
- setup/audit rows increasing by cycle
- runner reported as `github-hosted`
- mode reported as `paper`

## Daily operator checks

1. Last `cloud-cycle` run status in GitHub Actions
2. `engine_heartbeat.last_seen_at`
3. `model_setups` and `model_signal_audit` recent counts
4. Bybit preflight statuses (`public`, `auth`, `error`)