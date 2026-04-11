# Troubleshooting

> [Prev: Operations Guide](https://github.com/sheryloe/Automethemoney/wiki/Operations-Guide) | [Wiki Home](https://github.com/sheryloe/Automethemoney/wiki)

---

## 1) No fresh data in dashboard

Check in order:

1. Latest `cloud-cycle` run status in GitHub Actions
2. Workflow logs (`gh run view --log`)
3. `engine_heartbeat.last_seen_at` in Supabase

## 2) `last_bybit_sync_ts` remains 0

Inspect heartbeat meta:

- `bybit_preflight_public_status`
- `bybit_preflight_auth_status`
- `bybit_preflight_error`

Interpretation:

- `auth=401`: invalid API key/secret
- `public=403` and `auth=200`: public endpoint blocked while signed endpoint works
- `public=403` and `auth=403`: hosted runner network path blocked for both

## 3) Setup/audit counts do not increase

Run:

```powershell
.\ops\verify-stack.ps1 -EnvFile ".\.env" -LookbackHours 1 -ExpectedRunner "github-hosted"
```

If counts are 0:

- inspect workflow logs for Python exceptions
- verify Supabase URL/service role key in GitHub secrets
- confirm workflow branch and ref are correct

## 4) Pages and README mismatch

Sync priority:

1. `README.md`
2. `docs/wiki-src/*`
3. `docs/index.html`

Update all three before push.