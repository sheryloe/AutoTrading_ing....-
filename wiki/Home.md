# Automethemoney Wiki

## 2026-03-17 기준 현재 상태

Automethemoney는 전략 실행, 손익 확인, 시그널 진단, Supabase 상태를 함께 보는 자동매매 운영 콘솔입니다.

- GitHub Actions `cloud-cycle`은 1분마다 기동합니다.
- 엔진 heartbeat와 가격/PnL 동기화는 현재 정상 상태입니다.
- 크립토 모델 `A/B/C/D`는 이제 `long/short`를 모두 다룰 수 있습니다.
- GitHub Pages는 모델 카드와 그래프 중심으로 정리되어 있습니다.

## 2026-03-17 기준 완료 메모

- Pages GUIDE와 실제 화면 구성을 맞췄습니다.
- 하단에 `Repository`, `Wiki` 바로가기 버튼을 추가했습니다.
- heartbeat / positions / daily PnL 시각 동기화 문제를 정리했습니다.
- 크립토 `signal -> setup -> position -> pnl` 전 구간에 숏 지원을 붙였습니다.

## 2026-03-17 기준 TODO

- [ ] A/D 모델 숏 신호 빈도와 실제 체결 품질을 며칠 단위로 확인
- [ ] Pages 문구와 Supabase 컬럼 의미가 다시 어긋나지 않도록 점검
- [ ] `paper` / `live` 권한, 시드, 리포트를 완전히 분리
- [ ] 운영 Kill Switch, 손실 제한, 재진입 쿨다운 정책을 문서와 화면에 같이 반영
- [ ] 최근 체결 로그에서 long/short 성과 비교 카드가 더 필요한지 검토

## 보류 또는 확인 필요

- [ ] Notion 연동: 저장소 기준으로는 토큰/스크립트/자동화 경로가 아직 확인되지 않음
- [ ] GitHub Wiki 본문과 `docs/wiki-src/`를 어떤 쪽을 기준 원본으로 둘지 결정 필요

## 지금 바로 볼 문서

- [README.md](../README.md)
- [Service-Roadmap.md](./Service-Roadmap.md)
- [docs/wiki-src/Home.md](../docs/wiki-src/Home.md)

## 운영 메모

- 자동매매 기능 자체보다 `동기화 신뢰도`, `paper/live 분리`, `운영 안전장치`가 서비스 신뢰도를 결정합니다.
