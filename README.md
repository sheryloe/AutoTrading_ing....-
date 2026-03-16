# Automethemoney

`Automethemoney`는 크립토 선물 데모 자동매매를 운영자 시점에서 관리하는 콘솔형 프로젝트입니다.
Next.js 운영 화면, Python 엔진, Supabase 상태 저장, GitHub Actions 배치를 하나의 흐름으로 묶어 모델 성과와 포지션을 계속 추적합니다.

- 저장소: `https://github.com/sheryloe/Automethemoney`
- GitHub Pages: `https://sheryloe.github.io/Automethemoney/`

## 2026-03-17 기준 현재 상태

- 모델 `A/B/C/D` 크립토 플래너 운영
- `signal -> setup -> position -> pnl` 전 구간에서 `long/short` 지원
- GitHub Actions `cloud-cycle`은 `1분`마다 기동
- 기본 스캔 간격은 `SCAN_INTERVAL_SECONDS=480` 기준
- 실행 타깃 기본값은 `paper`
- GitHub Pages는 모델 카드와 그래프 중심으로 정리
- Supabase 동기화는 heartbeat, setup, positions, daily PnL까지 연결

## 핵심 기능

- 모델별 setup, 오픈 포지션, 최근 체결, 일별 손익 추적
- intrabar 체결 시뮬레이션과 TP/SL 반영
- Supabase 기반 런타임 설정 저장과 상태 조회
- GitHub Actions 기반 서버리스 배치 운영
- GitHub Pages 요약 화면과 Next.js 운영 콘솔 분리

## 저장소 구조

- `frontend/`: Next.js 운영 콘솔
- `src/`: Python 트레이딩 엔진
- `scripts/`: 배치 실행 및 보조 스크립트
- `docs/`: GitHub Pages와 운영 문서
- `wiki/`: 운영 메모와 로드맵

## 주요 데이터 테이블

- `engine_heartbeat`
- `engine_runtime_config`
- `model_setups`
- `model_signal_audit`
- `positions`
- `daily_model_pnl`
- `engine_state_blobs`

스키마와 설정 문서:

- `docs/SUPABASE_CORE_SCHEMA_20260315.sql`
- `docs/SUPABASE_SCHEMA_20260315.sql`
- `docs/VERCEL_SUPABASE_SETUP_20260315.md`

## 운영 흐름

1. GitHub Actions가 1분마다 `scripts/run_batch_cycle.py`를 실행합니다.
2. 엔진은 런타임 설정과 시장 데이터를 읽어 setup과 포지션을 갱신합니다.
3. 결과는 Supabase와 `docs/data/daily_pnl/`에 반영됩니다.
4. Next.js 콘솔과 GitHub Pages가 이 상태를 읽어 화면에 보여줍니다.

## 로컬 실행

프런트 설치:

```bash
cd frontend
npm install
```

파이썬 의존성 설치:

```bash
pip install -r requirements.txt
```

문법 점검:

```bash
python -m py_compile src/engine.py scripts/run_batch_cycle.py
```

## 문서 바로가기

- `wiki/Home.md`
- `wiki/Service-Roadmap.md`
- `docs/wiki-src/Home.md`

## 다음 확인 항목

- A/D 모델 숏 빈도와 체결 품질 추가 검증
- `paper`와 `live` 권한/자금/문서 완전 분리
- 운영 Kill Switch와 손실 제한 가드 명확화
- Notion 연동 경로가 준비되면 운영 로그 동기화 추가
