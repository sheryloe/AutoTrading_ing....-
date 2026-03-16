# Automethemoney

`Automethemoney`는 크립토 선물 데모 자동매매를 운영자 관점에서 관리하는 콘솔형 서비스 프로젝트입니다.
모델 성능, 포지션, 런타임 설정, Supabase 상태, GitHub Actions 배치를 하나의 운영 흐름으로 묶습니다.

- 저장소: `https://github.com/sheryloe/Automethemoney`
- GitHub Pages: `https://sheryloe.github.io/Automethemoney/`

## 서비스 개요

- 모델 A/B/C/D 기반 진입 로직과 성과를 분리해 보여줍니다.
- Supabase를 런타임 설정과 진단용 백엔드로 사용합니다.
- GitHub Actions `cloud-cycle`로 주기 실행되는 서버리스 운영 구조를 지향합니다.

## 구성

- `frontend/`: Next.js 운영 콘솔
- `src/`: Python 트레이딩 엔진과 배치 로직
- `docs/`: GitHub Pages와 운영 문서
- `scripts/`: 실행/보조 스크립트

## 주요 데이터

- `engine_heartbeat`
- `engine_runtime_config`
- `model_setups`
- `model_signal_audit`
- `positions`
- `daily_model_pnl`

관련 스키마와 설정 문서는 아래 파일에서 확인할 수 있습니다.

- `docs/SUPABASE_CORE_SCHEMA_20260315.sql`
- `docs/SUPABASE_SCHEMA_20260315.sql`
- `docs/VERCEL_SUPABASE_SETUP_20260315.md`

## 실행 흐름

- 기본 실행 대상은 `paper`입니다.
- `cloud-cycle`이 Supabase 런타임 설정을 읽고 배치를 수행합니다.
- 일별 모델 성과는 `docs/data/daily_pnl/` 아래에 보관됩니다.

## 실행 방법

프런트와 파이썬 의존성을 각각 준비합니다.

```bash
cd frontend
npm install
```

```bash
pip install -r requirements.txt
```

## 다음 단계

- `paper`와 `live` 환경을 완전히 분리
- 수동 킬 스위치와 손실 제한 가드 추가
- 모델별 백테스트 대비 실운영 편차 리포트 강화
