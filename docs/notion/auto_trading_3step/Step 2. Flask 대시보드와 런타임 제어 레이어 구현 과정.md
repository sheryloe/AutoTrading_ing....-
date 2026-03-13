# AutoTrading 2단계: Flask 대시보드와 런타임 제어 레이어 구현 과정

- 권장 슬러그: `auto-trading-step-2-flask-dashboard-runtime`
- SEO 설명: `AutoTrading 프로젝트에서 Flask 웹앱과 런타임 제어 UI를 어떻게 구현했는지 정리한 2단계 글입니다.`
- 핵심 키워드: `Flask trading dashboard`, `runtime control UI`, `auto trading web app`, `strategy studio`, `자동매매 콘솔`
- 대표 이미지 ALT: `AutoTrading Strategy Studio 메인 대시보드`

## 들어가며

운영 리스크를 정리했다면 이제는 실제로 보고 조작할 화면이 필요했습니다. Strategy Studio는 단순 현황판이 아니라 시작, 중지, 동기화, 모델 상태 확인까지 한 번에 처리하는 운영 패널 역할을 맡습니다.

## 이번 단계에서 집중한 문제

- 홈 화면에서 health와 대시보드 API를 동시에 제공해야 했습니다.
- 모드 전환, 동기화, 리셋, 자동매매 토글을 같은 화면에서 다뤄야 했습니다.
- JS 쪽 상태 뷰 모델을 market/model/workspace 기준으로 나눠 정보 밀도를 낮춰야 했습니다.

## 이렇게 코드를 반영했다

### 1. 웹앱과 API를 한 파일에서 묶는 Flask 엔트리
- 파일: `web_app.py`
- 왜 넣었는가: 실험 단계에서는 배포 복잡도보다 운영 집중도가 더 중요했기 때문입니다.

```python
@app.get("/")
def home():
    return render_template("index.html", ui_refresh_seconds=settings.ui_refresh_seconds)

@app.get("/api/dashboard")
def api_dashboard():
    return jsonify(engine.dashboard_payload())
```

### 2. 화면 상태를 모델/시장 단위로 나눈 프론트 코드
- 파일: `static/app.js`
- 왜 넣었는가: 자동매매 화면은 숫자가 많기 때문에 어떤 탭이 무엇을 보여 주는지 먼저 구조화할 필요가 있었습니다.

```javascript
const VIEW = {
  market: "meme",
  model: "A",
  demoMarket: "meme",
  liveMarket: "meme",
  workspace: "models",
};
```

## 적용 결과

- 상태 확인과 제어를 한 화면에서 처리할 수 있는 운영 콘솔이 완성됐습니다.
- 전략, 모델, 데모/라이브 구분을 UI에서도 자연스럽게 읽을 수 있게 됐습니다.
- Step 3에서 안전장치와 리포트 계층을 설명할 기반 화면이 생겼습니다.

## 티스토리 SEO 정리 포인트

- 대시보드 글에서는 버튼보다 `왜 이 제어가 필요한가`를 먼저 설명하는 편이 좋습니다.
- 캡처 이미지는 데스크톱 1장, 모바일 1장 조합이 읽기 흐름이 좋습니다.
- 모델/시장 탭 구조를 소제목으로 분리하면 긴 글도 덜 답답해 보입니다.

## 마무리

Step 2까지 오면 이 프로젝트는 전략 코드보다 운영 패널의 성격이 더 강해집니다.
