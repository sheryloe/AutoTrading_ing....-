# AutoTrading_ing....-

도커 기반 앱과 리포터를 함께 띄워 자동매매 상태, 리포트, 런타임 설정을 운영하는 프로젝트입니다.

- Repository: https://github.com/sheryloe/AutoTrading_ing....-
- Live page: https://sheryloe.github.io/AutoTrading_ing....-/
- Audience: 크립토 자동매매 실험, 대시보드 관찰, 리포트 자동 생성에 관심 있는 개발자

## Overview
크립토 자동매매 실험을 위한 운영 콘솔과 리포팅 스택

## Why This Exists
자동매매 프로젝트는 전략 실험, 상태 점검, 리포트 축적, 런타임 설정을 별도로 관리하면 운영 피로가 크게 늘어납니다.

## What You Can Do
- 앱 컨테이너와 리포터 컨테이너를 분리한 Docker Compose 구성
- 리포트 디렉터리와 런타임 설정 파일을 볼륨으로 연결
- 웹 대시보드와 운영 상태 API를 전제로 한 구조
- 전략 실험 중간 보고를 자동화하는 보조 스크립트 포함

## Typical Flow
- `.env`와 상태 파일 준비
- Docker Compose로 앱과 리포터 기동
- 대시보드와 생성된 리포트로 전략 상태 확인

## Tech Stack
- Python
- Docker Compose
- HTML templates
- Static assets

## Quick Start
- `.env.example`을 참고해 `.env`와 상태 파일을 준비합니다.
- `docker compose up -d --build`로 앱과 리포터를 함께 실행합니다.
- 필요 시 `docker compose logs -f`로 상태를 점검합니다.

## Repository Structure
- `scripts/`: 리포트 및 운영 보조 스크립트
- `templates/`, `static/`: 대시보드 UI 자산
- `reports/`: 리포트 산출물 저장 위치

## Search Keywords
`crypto auto trading dashboard`, `algorithmic trading console`, `docker trading app`, `자동매매 대시보드`, `크립토 리포트 자동화`

## FAQ
### 이 저장소는 실전 자동매매용인가요?
현재는 운영 콘솔과 상태 리포트 중심의 실험/개선용 프로젝트로 보는 편이 정확합니다.

### 어떻게 실행하나요?
Docker Compose로 앱과 리포터 컨테이너를 함께 띄우는 방식입니다.

### 무엇을 먼저 확인하면 되나요?
런타임 설정 파일과 보고서 디렉터리, 대시보드 API 연결 상태를 먼저 확인하는 것이 좋습니다.

