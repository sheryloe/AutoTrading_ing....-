# AutoTrading Security Review (2026-03-07)

## Scope
- API exposure and authentication
- Secret handling and runtime persistence
- Deployment defaults (`docker-compose`)
- Git secret hygiene (`.gitignore`, `.env.example`)

## Executive Summary
- No hardcoded live API key was detected in tracked files.
- `.env.example` is correctly kept with blank placeholders.
- There are still important operational security gaps that should be closed before internet exposure.

## Findings
### High
1. **No authentication on control APIs**
   - Impact: Any network peer reaching the service can change mode/settings and control trading.
   - Evidence: control and secret update endpoints in `web_app.py` (`/api/control/*`, `/api/settings/secrets`).

2. **Direct host port exposure**
   - Impact: If host firewall is weak, unauthenticated API becomes externally reachable.
   - Evidence: `docker-compose.yml` publishes `${APP_PORT}` directly.

### Medium
3. **Runtime secrets stored in plaintext local file**
   - Impact: local file compromise can leak API keys.
   - Evidence: `save_runtime_overrides(...)` writes updates into `runtime_settings.json` in `src/config.py`.
   - Note: file is gitignored, so Git leakage risk is reduced.

4. **No API rate-limit / abuse controls**
   - Impact: flood, repeated bad calls, and brute-force behavior are not constrained.

### Low
5. **No built-in TLS termination**
   - Impact: plaintext transport risk unless reverse proxy handles HTTPS.

## Immediate Actions (priority order)
1. Add `ADMIN_API_TOKEN` header guard middleware for all `/api/*`.
2. Default to local bind (`APP_HOST=127.0.0.1`) and expose only through reverse proxy.
3. Use Nginx/Caddy for HTTPS + BasicAuth + IP allowlist.
4. Restrict `runtime_settings.json` file permissions (owner read/write only).
5. Add rate limit on control endpoints.

## Git and Secret Hygiene Check
- `.env` ignored: pass
- `runtime_settings.json` ignored: pass
- `.env.example` contains placeholder blanks: pass
- Pattern scan on tracked files: no live key match found

## Free Security Upgrades
- Cloudflare Tunnel / Zero Trust for private admin access
- Fail2ban (or proxy-level blocklist)
- Scheduled key-pattern scan (`rg`) in CI or local cron
- Telegram command allowlist by admin `chat_id`
