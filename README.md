# Axiom Flow Console

<div align="center">
  <h1>실시간 자동매매 콘솔 (Meme + Crypto 분리 6모델)</h1>
  <p>Flask 기반 대시보드 + 텔레그램 제어 + 6시간 자동 튜닝</p>
  <p>
    <code>meme_A</code> / <code>meme_B</code> / <code>meme_C</code> +
    <code>crypto_A</code> / <code>crypto_B</code> / <code>crypto_C</code>
  </p>
</div>

---

## 1. 핵심 구조

### 1) 시장 완전 분리
- 밈코인과 크립토(선물 데모)를 런타임/포지션/PNL/이력 단위로 완전 분리
- 활성 키: `meme_A,B,C` / `crypto_A,B,C`
- 백업 키: `legacy_A,B,C` (마이그레이션 보존용)

### 2) 모델 이름 (한글)
- 통합 모델
  - `A`: 안정 추세 예측모델
  - `B`: 흐름 추종 예측모델
  - `C`: 공격 모멘텀 예측모델
- 밈 모델
  - `A`: 도그리 밈 선별모델
  - `B`: 밈 장기홀딩 예측모델
  - `C`: 밈 단타 모멘텀모델
- 크립토 모델
  - `A`: 크립토 안정 추세모델
  - `B`: 크립토 흐름 추종모델
  - `C`: 동그리 크립토 모멘텀모델

### 3) 초기화 안전장치
- 기본: `ALLOW_DEMO_RESET=false`
- 데모 초기화는 잠금 해제 + 확인 문구가 모두 필요
- API/텔레그램/웹 UI 모두 동일 보호 적용

### 4) 이력/백업
- 모델별 거래 이력 장기 보존 (최대 190일, 상한 크게 확장)
- 상태 백업 자동 생성: `reports/state_backups/`

---

## 2. 폴더 구조

```text
d:\AI_Auto
├─ src/
│  ├─ engine.py
│  ├─ config.py
│  ├─ state.py
│  ├─ providers/
│  └─ data_sources/
├─ static/
│  ├─ app.js
│  └─ style.css
├─ templates/
│  └─ index.html
├─ reports/
├─ web_app.py
├─ .env.example
└─ requirements.txt
```

---

## 3. 빠른 시작 (Windows)

```powershell
cd d:\AI_Auto
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
py -3 web_app.py
```

- 기본 UI: `http://localhost:8099`

---

## 4. Docker 실행

```powershell
cd d:\AI_Auto
docker compose up -d --build
```

- 중지:

```powershell
docker compose down
```

---

## 5. 환경변수 가이드

### 필수/주요
- `TRADE_MODE=paper`
- `ENABLE_AUTOTRADE=true`
- `DEMO_SEED_USDT=1000`
- `ALLOW_DEMO_RESET=false`  (권장: 항상 false)
- `DEMO_ENABLE_MACRO=true`
- `PHANTOM_WALLET_ADDRESS=...`
- `TELEGRAM_BOT_TOKEN=...`
- `TELEGRAM_CHAT_ID=...` (최초에는 비워도 자동 학습 가능)

### 트렌드/외부 소스
- `GOOGLE_API_KEY=...`
- `COINGECKO_API_KEY=...`
- `CMC_API_KEY=...`
- `SOLSCAN_API_KEY=...`

### 무료 티어 보호 권장값
- `GOOGLE_TREND_INTERVAL_SECONDS=1800`
- `GOOGLE_TREND_COOLDOWN_SECONDS=21600`
- `TREND_ERROR_BACKOFF_SECONDS=900`
- `SOLSCAN_BUDGET_WINDOW_SECONDS=300`

---

## 6. 텔레그램 설정 (1:1 기준)

1. BotFather에서 봇 생성 후 토큰 발급
2. `.env`에 `TELEGRAM_BOT_TOKEN` 입력
3. 서버 실행 후 봇에게 `/start` 전송
4. 자동 등록 실패 시:
   - `https://api.telegram.org/bot<토큰>/getUpdates`
   - `message.chat.id` 값을 `TELEGRAM_CHAT_ID`로 설정

---

## 7. 텔레그램 명령어 (상세)

### 상태/손익
- `/status` 전체 상태 요약
- `/status_meme` 밈 모델 상태만
- `/status_crypto` 크립토 모델 상태만
- `/pnl` 통합 손익
- `/pnl_meme` 밈 손익만
- `/pnl_crypto` 크립토 손익만

### 포지션/자산
- `/positions` 전체 포지션
- `/positions_meme` 밈 포지션만
- `/positions_crypto` 크립토 포지션만
- `/meme_balance` 팬텀 지갑 자산
- `/bybit_balance` 거래소 자산

### 튜닝/소스/오류
- `/tune_status` 자동튜닝 상태(6시간 주기)
- `/sources` 트렌드 소스 상태
- `/errors` 최근 오류 요약
- `/wallet_pattern <token_address>` Solscan 패턴 점검

### 제어
- `/auto_on`, `/auto_off`
- `/trade_alert_on`, `/trade_alert_off`
- `/report_on`, `/report_off`, `/report_now`
- `/chatid`

### 초기화 보호
- `/reset_unlock`
- `/reset_demo [seed] RESET DEMO`
- `/reset_lock`

---

## 8. 자동 튜닝 설명

- 주기: `6시간`
- 대상: `crypto_A`, `crypto_B`, `crypto_C`
- 튜닝 파라미터:
  - `threshold`
  - `tp_mul`
  - `sl_mul`
- 평가 지표:
  - 최근 닫힌 거래 수
  - 승률(`win_rate`)
  - 손익(`pnl`)
  - PF(`profit_factor`)

`/tune_status`로 현재 값/다음 평가 시간/최근 평가 노트를 확인할 수 있습니다.

---

## 9. 운영 안전 원칙

1. 초기화는 잠금 기본 유지 (`ALLOW_DEMO_RESET=false`)
2. 실거래 전 최소 2~4주 데모 검증
3. 텔레그램 409 충돌 시 단일 인스턴스만 polling
4. 429(rate-limit) 발생 시 주기 늘리고 cooldown 유지
5. 포지션/이력은 초기화 명령 없으면 삭제 금지

---

## 10. 트러블슈팅

### Telegram 409 Conflict
- 원인: 동일 봇 토큰을 여러 프로세스가 동시에 `getUpdates`
- 조치: 중복 프로세스 종료, 단일 서버만 polling

### Google 429 Too Many Requests
- 원인: 무료 티어 초과
- 조치: `GOOGLE_TREND_INTERVAL_SECONDS` 상향, cooldown 유지

### X/RSS 400 또는 empty_feed
- 원인: RSS 접근 제한/도메인 정책
- 조치: 소스 fallback 유지, 오류는 `/sources`에서 확인

### Phantom 자산이 포지션에 안 보임
- 지갑 자산 리스트와 봇 진입 포지션은 목적이 다름
- 봇 포지션은 모델 런(`meme_*`) 기준으로 관리됨

---

## 11. API 엔드포인트 (요약)

- `GET /health`
- `GET /api/dashboard`
- `POST /api/control/start`
- `POST /api/control/stop`
- `POST /api/control/restart`
- `POST /api/control/mode`
- `POST /api/control/autotrade`
- `POST /api/control/force-sync`
- `POST /api/control/close-meme`
- `POST /api/control/reset-demo` (잠금 + 확인문구 필요)

---

## 12. 커밋/배포 체크리스트

- [ ] `py -3 -m py_compile src/engine.py src/config.py web_app.py`
- [ ] `node --check static/app.js`
- [ ] `/health` 정상
- [ ] `/help`, `/status`, `/tune_status` 응답 확인
- [ ] 초기화 잠금 상태 확인 (`ALLOW_DEMO_RESET=false`)

---

## 13. 라이선스/주의

- 본 프로젝트는 고위험 자산 자동매매 연구/데모 목적입니다.
- 투자 손실 책임은 사용자에게 있습니다.
- 실거래 전 반드시 리스크/주문 정책을 별도로 검증하세요.

