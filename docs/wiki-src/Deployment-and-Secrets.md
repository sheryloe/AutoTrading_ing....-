# 배포와 시크릿 기준

> [Prev: Data State Reference](https://github.com/sheryloe/Automethemoney/wiki/Data-State-Reference) | [Wiki Home](https://github.com/sheryloe/Automethemoney/wiki) | [Next: Operations Guide](https://github.com/sheryloe/Automethemoney/wiki/Operations-Guide)

---

AI_Auto는 시크릿을 한 곳에 몰아넣지 않습니다. 어떤 값이 어디에 있어야 하는지 구분해야 저장 버튼, provider vault, GitHub Actions가 서로 충돌하지 않습니다.

## 저장 위치 요약

| 위치 | 넣는 값 | 용도 |
| --- | --- | --- |
| Vercel Environment Variables | `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `SERVICE_MASTER_KEY`, `SERVICE_ADMIN_TOKEN` | 콘솔 화면 + 서버 API |
| GitHub Actions Secrets | `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `SERVICE_MASTER_KEY` | `cloud-cycle` 배치 실행 |
| `/settings` Service control | Bybit / Binance / CoinGecko provider 자격증명 | provider vault 저장 |
| Supabase | runtime profile, provider vault, heartbeat, setup, PnL | 상태 원장 |

## 시크릿 흐름 다이어그램

```mermaid
flowchart LR
  V[Vercel env] --> API[Service control API]
  API --> SB[(Supabase)]
  G[GitHub Actions secrets] --> C[cloud-cycle]
  C --> SB
  UI[/settings 입력] --> API
```

## Vercel 환경 변수

필수 항목:

- `NEXT_PUBLIC_SUPABASE_URL`
- `NEXT_PUBLIC_SUPABASE_ANON_KEY`
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `SERVICE_MASTER_KEY`
- `SERVICE_ADMIN_TOKEN`

주의:

- `NEXT_PUBLIC_*`는 브라우저에서 읽을 수 있는 값입니다
- `SUPABASE_SERVICE_ROLE_KEY`, `SERVICE_MASTER_KEY`, `SERVICE_ADMIN_TOKEN`은 비공개여야 합니다

## GitHub Actions secrets

필수 항목:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `SERVICE_MASTER_KEY`

provider 키는 GitHub Secrets가 아니라 `/settings`에서 저장합니다.

## cloud-cycle 기준

| 항목 | 값 |
| --- | --- |
| 주기 | 1분 |
| timeout | 7분 |
| concurrency | 켜짐 |
| 기본 실행 타깃 | `paper` |
| `SCAN_INTERVAL_SECONDS` | `60` |
| `MODEL_AUTOTUNE_INTERVAL_HOURS` | `168` |

## 배포 전 체크리스트

- [ ] `SERVICE_MASTER_KEY`가 Vercel과 GitHub Actions에 같은 값으로 들어갔다
- [ ] `SERVICE_ADMIN_TOKEN`은 Vercel에만 넣었다
- [ ] provider 키를 GitHub Secrets가 아니라 `/settings`에서 저장했다
- [ ] env 수정 후 Vercel 재배포를 했다
