# AutoTrading Console

밈 + 크립토 자동매매 콘솔입니다. 현재 기본 프리셋은 `데모 전용`, `시드 10,000 USDT`, `밈 3엔진 + 크립토 4모델` 기준입니다.

## 1. 현재 기본 프리셋 (2026-03-11)
- 모드: `paper`
- 실전 실행: `OFF`
- 밈 데모: `A/B/C` 엔진 활성
- 크립토 데모: `A/B/C/D` 4모델 활성
- 데모 시드: 각 밈 엔진 / 각 크립토 모델별 `10,000 USDT`
- 크립토 배율 범위
  - `A`: `8x ~ 12x`
  - `B`: `11x ~ 18x`
  - `C`: `7x ~ 11x`
  - `D`: `8x ~ 13x`

## 2. 화면 구성
- 워크스페이스: `데모`, `실전`, `밈 트렌드`, `크립토 트렌드`, `설정`
- 실전 화면: `실전 밈` / `실전 크립토` 분리
- 데모 화면: 밈 엔진 3개와 크립토 모델 4개를 각각 독립 성과로 비교
- 리포트: 총자산, 실현/미실현 손익, 모델별 랭킹, 트렌드 요약 제공

## 3. 모델 구성
### 밈
- `A 도그리 밈 선별모델`: 품질형 필터 진입
- `B 밈 장기홀딩 예측모델`: 장기홀딩/재점화형
- `C 밈 단타 모멘텀모델`: 빠른 회전형

### 크립토
- `A 크립토 단타모델`: 안정형 단타
- `B 크립토 공격형 단타모델`: 고배율 공격형
- `C 크립토 스윙10 모델`: 일일 최대 10회 스윙
- `D 크립토 단일포지션 모델`: 단일 포지션 집중형

## 4. 빠른 시작
### 로컬 실행
```powershell
cd d:\AI_Auto
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
Copy-Item runtime_settings.example.json runtime_settings.local.json
py -3 web_app.py
```

### Docker 실행
```powershell
cd d:\AI_Auto
Copy-Item .env.example .env
Copy-Item runtime_settings.example.json runtime_settings.local.json
docker compose up -d --build
```

접속: `http://localhost:8099`

## 5. Docker 시작용 EXE
- 빌드 스크립트: `scripts/build_docker_launcher.ps1`
- 생성 파일: `dist/AI_Auto_Docker_Start.exe`
- 동작:
  - Docker/Rancher Desktop 데몬 확인
  - 필요 시 Desktop 앱 실행
  - `docker compose up -d --build` 실행
  - `/health` 확인 후 브라우저 오픈

직접 빌드:
```powershell
cd d:\AI_Auto
powershell -ExecutionPolicy Bypass -File .\scripts\build_docker_launcher.ps1
```

## 6. 데모 초기화
전체 데모(밈 + 크립토)를 `10,000`으로 다시 맞추려면 서버 실행 후 아래 API를 호출하면 됩니다.

```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:8099/api/control/reset-demo `
  -ContentType "application/json" `
  -Body '{"seed_usdt":10000,"confirm_text":"RESET DEMO"}'
```

이 호출은 밈 3엔진과 크립토 4모델을 함께 초기화합니다.

## 7. 설정 파일
- `.env`: 배포/포트/기본 환경 변수
- `runtime_settings.local.json`: 로컬 런타임 오버라이드와 비밀키 저장용
- `runtime_settings.example.json`: Git 커밋 가능한 샘플

도커 컴포즈는 `.env`에 지정된 `STATE_FILE`, `MODEL_FILE`, `RUNTIME_SETTINGS_FILE` 경로를 그대로 마운트합니다.

## 8. 보안 원칙
- `.env`는 Git 추적 제외
- `runtime_settings.local.json`도 Git 추적 제외
- 샘플 파일에는 비밀키를 넣지 않음
- 실전 전환 전 `ENABLE_LIVE_EXECUTION=false` 상태에서 검증

## 9. 추가 문서
- 중간 보고서: `docs/MID_REPORT_2026-03-07.md`
- 보안 점검: `docs/SECURITY_REVIEW_2026-03-07.md`
- 전략 리팩터링 노트: `docs/strategy_refactor_20260308.md`
