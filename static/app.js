const REFRESH_MS = Math.max(2000, (window.APP_CONFIG?.refreshSeconds || 4) * 1000);

const VIEW = {
  market: "meme",
  model: "A",
  cryptoModel: "A",
  liveMarket: "meme",
  workspace: "paper",
  data: null,
};

const MODEL_IDS = ["A", "B", "C"];
const LIVE_MODEL_DIRTY = { meme: false, crypto: false };
let LIVE_MARKET_DIRTY = false;

const SECRET_KEYS = [
  "BYBIT_API_KEY",
  "BYBIT_API_SECRET",
  "PHANTOM_WALLET_ADDRESS",
  "SOLANA_PRIVATE_KEY",
  "SOLANA_RPC_URL",
  "TELEGRAM_BOT_TOKEN",
  "TELEGRAM_CHAT_ID",
  "GOOGLE_API_KEY",
  "SOLSCAN_API_KEY",
  "BINANCE_API_KEY",
  "BINANCE_API_SECRET",
  "COINGECKO_API_KEY",
  "CMC_API_KEY",
];

let SECRET_CACHE = null;

function isMobileViewport() {
  return window.matchMedia("(max-width: 980px)").matches;
}

function closeMobileNav() {
  const shell = document.querySelector(".app-shell");
  if (!shell) return;
  shell.classList.remove("nav-open");
}

function fmtUsd(value) {
  const num = Number(value || 0);
  return `$${num.toLocaleString("en-US", { maximumFractionDigits: 2 })}`;
}

function fmtUsdPrice(value) {
  const num = Number(value || 0);
  if (!Number.isFinite(num) || num <= 0) return "$0";
  const abs = Math.abs(num);
  let digits = 2;
  if (abs < 1) digits = 4;
  if (abs < 0.01) digits = 6;
  if (abs < 0.0001) digits = 8;
  return `$${num.toLocaleString("en-US", { maximumFractionDigits: digits })}`;
}

function fmtPct(value) {
  const num = Number(value || 0);
  return `${num >= 0 ? "+" : ""}${num.toFixed(2)}%`;
}

function fmtTs(unixSec) {
  const n = Number(unixSec || 0);
  if (!n) return "-";
  return new Date(n * 1000).toLocaleString();
}

function clsPn(v) {
  return Number(v || 0) >= 0 ? "pos" : "neg";
}

function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}

function sumBy(rows, key) {
  return (rows || []).reduce((acc, row) => acc + Number((row || {})[key] || 0), 0);
}

function workspaceSnapshot(data, workspace) {
  const s = data.settings || {};
  const metrics = data.metrics || {};
  const modeActual = String(s.trade_mode || "paper").toUpperCase();
  const isRunning = Boolean(data.running);
  if (workspace === "live") {
    const walletUsd = sumBy(data.wallet_assets || [], "value_usd");
    const bybitUsd = sumBy(data.bybit_assets || [], "usd_value");
    const liveEq = Number(data.live_equity_usd ?? (walletUsd + bybitUsd) ?? 0);
    const liveAnchor = Number(data.live_perf_anchor_usd ?? data.live_seed_usd ?? liveEq ?? 0);
    const liveNetFlow = Number(data.live_net_flow_usd ?? 0);
    const liveAdjEq = Number(data.live_adjusted_equity_usd ?? (liveEq - liveNetFlow));
    const livePerfPnl = Number(data.live_perf_pnl_usd ?? (liveAdjEq - liveAnchor));
    const livePerfRoi = Number(data.live_perf_roi_pct ?? (liveAnchor > 0 ? ((livePerfPnl / liveAnchor) * 100) : 0));
    const liveMemeUpnl = Number(
      data.live_managed_meme_upnl_usd ??
      data.live_meme_upnl_usd ??
      sumBy(data.live_meme_positions || [], "pnl_usd")
    );
    const liveCryptoUpnl = (data.crypto_live_positions || data.bybit_live_positions || []).reduce(
      (acc, p) =>
        acc +
        Number(
          p.unrealisedPnl ??
            p.unrealised_pnl ??
            p.unrealizedPnl ??
            p.unrealized_pnl ??
            0
        ),
      0
    );
    const livePnl = Number(liveMemeUpnl + liveCryptoUpnl);
    const livePos = (data.crypto_live_positions || data.bybit_live_positions || []).length;
    const liveUpnl = livePnl;
    const liveMeme = (s.live_enable_meme !== false);
    const liveCrypto = (s.live_enable_crypto !== false);
    const runningText = isRunning && modeActual === "LIVE" && Boolean(s.enable_live_execution)
      ? "RUNNING(LIVE)"
      : "STANDBY(LIVE)";
    return {
      runningText: `${runningText} | Auto:${s.enable_autotrade ? "ON" : "OFF"} | Exec:${s.enable_live_execution ? "ON" : "OFF"} | M/C:${liveMeme ? "ON" : "OFF"}/${liveCrypto ? "ON" : "OFF"}`,
      modeText: "LIVE",
      seedText: `${fmtUsd(liveAnchor)} (성과 기준선)`,
      equityUsd: liveEq,
      pnlUsd: livePerfPnl,
      pnlText: `${fmtUsd(livePerfPnl)} (${fmtPct(livePerfRoi)})`,
      winrateText: `순입출금 보정 ${liveNetFlow >= 0 ? "+" : "-"}${fmtUsd(Math.abs(liveNetFlow)).replace("$", "")} USD`,
    };
  }
  if (workspace === "paper") {
    const memeRows = [...(data.meme_model_rankings || data.meme_model_runs || [])];
    const cryptoRows = [...(data.crypto_model_rankings || data.crypto_model_runs || [])];
    const memeTop = memeRows.length ? memeRows[0] : null;
    const cryptoTop = cryptoRows.length ? cryptoRows[0] : null;
    const modelCount = memeRows.length + cryptoRows.length;
    const seedPerModel = Number((memeRows[0] || cryptoRows[0] || {}).seed_usd || data.demo_seed_usdt || 1000);
    const roi = (row) => {
      const seed = Number((row || {}).seed_usd || 0);
      if (seed <= 0) return 0;
      return ((Number((row || {}).equity_usd || 0) - seed) / seed) * 100;
    };
    const memeTopText = memeTop
      ? `${memeTop.model_id || "-"} ${fmtPct(roi(memeTop))}`
      : "데이터 없음";
    const cryptoTopText = cryptoTop
      ? `${cryptoTop.model_id || "-"} ${fmtPct(roi(cryptoTop))}`
      : "데이터 없음";
    const runningText = isRunning && modeActual === "PAPER" ? "RUNNING(PAPER)" : "STANDBY(PAPER)";
    return {
      runningText: `${runningText} | Auto:${s.enable_autotrade ? "ON" : "OFF"} | ResetLock:${s.allow_demo_reset ? "OFF" : "ON"}`,
      modeText: "PAPER",
      seedText: `${fmtUsd(seedPerModel)} (모델당)`,
      equityUsd: 0,
      pnlUsd: 0,
      equityText: `밈 1위: ${memeTopText}`,
      pnlText: `크립토 1위: ${cryptoTopText}`,
      winrateText: `활성 모델 ${modelCount}개`,
    };
  }
  return {
    runningText: `${isRunning ? "RUNNING" : "STOPPED"} | Auto:${s.enable_autotrade ? "ON" : "OFF"} | ResetLock:${s.allow_demo_reset ? "OFF" : "ON"}`,
    modeText: modeActual,
    seedText: `${Number(data.demo_seed_usdt || 1000).toFixed(0)} USDT`,
    equityUsd: Number(metrics.total_equity_usd || 0),
    pnlUsd: Number(metrics.total_pnl_usd || 0),
    winrateText: `${Number(metrics.win_rate || 0).toFixed(1)}%`,
  };
}

function marketModelName(data, market, modelId) {
  const marketKey = market === "meme" ? "meme_model_labels" : "crypto_model_labels";
  const table = (data && data[marketKey]) || {};
  return table[modelId] || modelId;
}

function parseModelCsv(raw) {
  const tokens = String(raw || "")
    .replaceAll("|", ",")
    .replaceAll(" ", ",")
    .split(",")
    .map((v) => String(v || "").trim().toUpperCase())
    .filter((v) => MODEL_IDS.includes(v));
  return [...new Set(tokens)];
}

function renderTableBody(id, rowsHtml, colSpan = 8) {
  const el = document.getElementById(id);
  if (!el) return;
  el.innerHTML = rowsHtml || `<tr><td colspan="${colSpan}">데이터 없음</td></tr>`;
}

async function postJson(url, body = {}) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let msg = `${url} failed`;
    try {
      const data = await res.json();
      if (data && data.error) msg = data.error;
    } catch (e) {
      // no-op
    }
    throw new Error(msg);
  }
  return res.json();
}

function renderSecretSettings(secrets) {
  const data = secrets || {};
  SECRET_KEYS.forEach((key) => {
    const maskEl = document.getElementById(`mask_${key}`);
    const inpEl = document.getElementById(`inp_${key}`);
    const row = data[key] || {};
    if (maskEl) {
      maskEl.textContent = row.masked || "(not set)";
    }
    if (inpEl) {
      inpEl.value = "";
      inpEl.placeholder = row.configured ? "변경값 입력(미입력시 유지)" : "새 값 입력";
    }
  });
}

async function loadSecretSettings(force = false) {
  if (!force && SECRET_CACHE) {
    renderSecretSettings(SECRET_CACHE);
    return;
  }
  try {
    const res = await fetch("/api/settings/secrets");
    if (!res.ok) throw new Error("secret settings fetch failed");
    const data = await res.json();
    SECRET_CACHE = (data && data.secrets) || {};
    renderSecretSettings(SECRET_CACHE);
  } catch (err) {
    setText("secretSaveMsg", `불러오기 실패: ${String(err)}`);
  }
}

function bindControls() {
  document.getElementById("btnNavToggle")?.addEventListener("click", () => {
    const shell = document.querySelector(".app-shell");
    if (!shell) return;
    if (isMobileViewport()) {
      shell.classList.toggle("nav-open");
    } else {
      shell.classList.toggle("nav-collapsed");
      try {
        window.localStorage.setItem("ui_nav_collapsed", shell.classList.contains("nav-collapsed") ? "1" : "0");
      } catch (e) {
        // no-op
      }
    }
  });
  document.getElementById("navBackdrop")?.addEventListener("click", () => {
    closeMobileNav();
  });
  document.getElementById("btnStart")?.addEventListener("click", async () => {
    await postJson("/api/control/start");
    await refreshDashboard();
  });
  document.getElementById("btnStop")?.addEventListener("click", async () => {
    await postJson("/api/control/stop");
    await refreshDashboard();
  });
  document.getElementById("btnRestart")?.addEventListener("click", async () => {
    await postJson("/api/control/restart");
    await refreshDashboard();
  });
  document.getElementById("btnSync")?.addEventListener("click", async () => {
    await postJson("/api/control/force-sync");
    await refreshDashboard();
  });
  document.getElementById("btnAuto")?.addEventListener("click", async () => {
    const isOn = document.getElementById("btnAuto")?.dataset?.enabled === "true";
    await postJson("/api/control/autotrade", { enabled: !isOn });
    await refreshDashboard();
  });
  document.getElementById("btnResetDemo")?.addEventListener("click", async () => {
    const seedInput = window.prompt("초기화 시드(USDT)를 입력하세요.", "1000");
    if (seedInput === null) return;
    const parsed = Number(seedInput);
    const seed = Number.isFinite(parsed) && parsed > 0 ? parsed : 1000;
    const confirmText = window.prompt("정말 초기화하려면 RESET DEMO 를 정확히 입력하세요.", "");
    if (confirmText !== "RESET DEMO") {
      window.alert("초기화를 취소했습니다.");
      return;
    }
    try {
      await postJson("/api/control/reset-demo", { seed_usdt: seed, confirm_text: confirmText });
      await refreshDashboard();
    } catch (err) {
      window.alert(err?.message || String(err));
    }
  });
  document.getElementById("btnCloseMeme")?.addEventListener("click", async () => {
    await postJson("/api/control/close-meme");
    await refreshDashboard();
  });
  document.querySelectorAll(".mode-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const mode = btn.dataset.mode || "paper";
      await postJson("/api/control/mode", { mode });
      await refreshDashboard();
    });
  });
}

function initNavState() {
  const shell = document.querySelector(".app-shell");
  if (!shell) return;
  try {
    if (window.localStorage.getItem("ui_nav_collapsed") === "1") {
      shell.classList.add("nav-collapsed");
    }
  } catch (e) {
    // no-op
  }
}

function bindSecretControls() {
  document.getElementById("btnSaveSecrets")?.addEventListener("click", async () => {
    const updates = {};
    SECRET_KEYS.forEach((key) => {
      const value = String(document.getElementById(`inp_${key}`)?.value || "").trim();
      if (value) updates[key] = value;
    });
    if (!Object.keys(updates).length) {
      setText("secretSaveMsg", "변경된 값이 없습니다.");
      return;
    }
    setText("secretSaveMsg", "저장 중...");
    try {
      const payload = await postJson("/api/settings/secrets", { updates });
      SECRET_CACHE = (payload && payload.secrets) || {};
      renderSecretSettings(SECRET_CACHE);
      setText("secretSaveMsg", `저장 완료 (${Object.keys(updates).length}개)`);
      if (VIEW.data) {
        await refreshDashboard();
      }
    } catch (err) {
      setText("secretSaveMsg", `저장 실패: ${err?.message || String(err)}`);
    }
  });
}

function selectedLiveModels(market) {
  return Array.from(document.querySelectorAll(`input[type="checkbox"][data-live-market="${market}"]:checked`))
    .map((el) => String(el.value || "").toUpperCase())
    .filter((v) => MODEL_IDS.includes(v));
}

function liveModelMsgId(market) {
  return market === "crypto" ? "liveModelSaveMsgCrypto" : "liveModelSaveMsgMeme";
}

function bindLiveModelControls() {
  document.querySelectorAll('input[type="checkbox"][data-live-market]').forEach((el) => {
    el.addEventListener("change", () => {
      const market = String(el.dataset.liveMarket || "meme").toLowerCase() === "crypto" ? "crypto" : "meme";
      LIVE_MODEL_DIRTY[market] = true;
      setText(liveModelMsgId(market), "변경됨 (해당 시장 모델 적용 필요)");
    });
  });

  const applyByMarket = async (market) => {
    const modelIds = selectedLiveModels(market);
    const isLive = market === "meme"
      ? (document.getElementById("liveToggleMeme")?.checked ?? true)
      : (document.getElementById("liveToggleCrypto")?.checked ?? true);
    const msgId = liveModelMsgId(market);
    if (isLive && !modelIds.length) {
      setText(msgId, "실전 ON인 시장은 최소 1개 모델이 필요합니다.");
      return;
    }
    setText(msgId, "적용 중...");
    try {
      const payload = market === "meme"
        ? { meme_models: modelIds }
        : { crypto_models: modelIds };
      await postJson("/api/control/live-models", payload);
      LIVE_MODEL_DIRTY[market] = false;
      setText(msgId, "적용 완료");
      await refreshDashboard();
    } catch (err) {
      setText(msgId, `적용 실패: ${err?.message || String(err)}`);
    }
  };

  document.getElementById("btnApplyLiveModelsMeme")?.addEventListener("click", async () => applyByMarket("meme"));
  document.getElementById("btnApplyLiveModelsCrypto")?.addEventListener("click", async () => applyByMarket("crypto"));
}

function bindLiveMarketControls() {
  const markDirty = () => {
    LIVE_MARKET_DIRTY = true;
    setText("liveMarketSaveMsg", "변경됨 (실전 시장 적용 버튼 필요)");
  };
  document.getElementById("liveToggleMeme")?.addEventListener("change", markDirty);
  document.getElementById("liveToggleCrypto")?.addEventListener("change", markDirty);
  document.getElementById("btnApplyLiveMarkets")?.addEventListener("click", async () => {
    const memeEnabled = document.getElementById("liveToggleMeme")?.checked ?? true;
    const cryptoEnabled = document.getElementById("liveToggleCrypto")?.checked ?? true;
    setText("liveMarketSaveMsg", "적용 중...");
    try {
      await postJson("/api/control/live-markets", {
        meme_enabled: Boolean(memeEnabled),
        crypto_enabled: Boolean(cryptoEnabled),
      });
      LIVE_MARKET_DIRTY = false;
      setText("liveMarketSaveMsg", "적용 완료");
      await refreshDashboard();
    } catch (err) {
      setText("liveMarketSaveMsg", `적용 실패: ${err?.message || String(err)}`);
    }
  });
}

function bindLivePerformanceControls() {
  document.getElementById("btnLiveAnchorNow")?.addEventListener("click", async () => {
    setText("livePerfSaveMsg", "적용 중...");
    try {
      await postJson("/api/control/live-performance/anchor-now", { reset_net_flow: true });
      setText("livePerfSaveMsg", "기준 재설정 완료");
      await refreshDashboard();
    } catch (err) {
      setText("livePerfSaveMsg", `실패: ${err?.message || String(err)}`);
    }
  });
  document.getElementById("btnLiveFlowAdjust")?.addEventListener("click", async () => {
    const raw = String(document.getElementById("liveFlowDeltaUsd")?.value || "").trim();
    const delta = Number(raw);
    if (!Number.isFinite(delta) || Math.abs(delta) < 1e-9) {
      setText("livePerfSaveMsg", "0이 아닌 숫자를 입력하세요.");
      return;
    }
    setText("livePerfSaveMsg", "반영 중...");
    try {
      await postJson("/api/control/live-performance/flow", { delta_usd: delta });
      const flowInput = document.getElementById("liveFlowDeltaUsd");
      if (flowInput) flowInput.value = "";
      setText("livePerfSaveMsg", "입출금 보정 반영 완료");
      await refreshDashboard();
    } catch (err) {
      setText("livePerfSaveMsg", `실패: ${err?.message || String(err)}`);
    }
  });
}

function bindTabs() {
  document.querySelectorAll("#marketTabs [data-market]").forEach((btn) => {
    btn.addEventListener("click", () => {
      VIEW.market = btn.dataset.market || "meme";
      updateModelTabLabels(VIEW.data || {});
      renderDetailPane(VIEW.data || {});
      setTabState();
    });
  });
  document.querySelectorAll("#modelTabs [data-model]").forEach((btn) => {
    btn.addEventListener("click", () => {
      VIEW.model = btn.dataset.model || "A";
      updateModelTabLabels(VIEW.data || {});
      renderDetailPane(VIEW.data || {});
      setTabState();
    });
  });
}

function bindDetailSelectControls() {
  const marketSelect = document.getElementById("marketSelect");
  const modelSelect = document.getElementById("modelSelect");
  marketSelect?.addEventListener("change", () => {
    VIEW.market = marketSelect.value || "meme";
    updateModelTabLabels(VIEW.data || {});
    renderDetailPane(VIEW.data || {});
    setTabState();
  });
  modelSelect?.addEventListener("change", () => {
    VIEW.model = modelSelect.value || "A";
    updateModelTabLabels(VIEW.data || {});
    renderDetailPane(VIEW.data || {});
    setTabState();
  });
}

function bindCryptoTrendTabs() {
  document.querySelectorAll("#cryptoModelTabs [data-crypto-model]").forEach((btn) => {
    btn.addEventListener("click", () => {
      VIEW.cryptoModel = btn.dataset.cryptoModel || "A";
      updateCryptoModelTabLabels(VIEW.data || {});
      renderCryptoTrend(VIEW.data || {});
      setCryptoTabState();
    });
  });
}

function bindWorkspaceTabs() {
  document.querySelectorAll("[data-workspace]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      VIEW.workspace = btn.dataset.workspace || "paper";
      setWorkspaceState();
      closeMobileNav();
      if (VIEW.workspace === "paper" || VIEW.workspace === "live") {
        try {
          const resp = await postJson("/api/control/mode", { mode: VIEW.workspace });
          const appliedMode = String((resp || {}).mode || "").toLowerCase();
          if (appliedMode && appliedMode !== VIEW.workspace) {
            setText("errorBar", `모드 전환 제한: 요청=${VIEW.workspace.toUpperCase()} 적용=${appliedMode.toUpperCase()}`);
          }
          await refreshDashboard();
        } catch (err) {
          setText("errorBar", `모드 전환 실패: ${String(err?.message || err)}`);
        }
      }
    });
  });
}

function bindLiveMarketTabs() {
  document.querySelectorAll("#liveMarketTabs [data-live-market]").forEach((btn) => {
    btn.addEventListener("click", () => {
      VIEW.liveMarket = btn.dataset.liveMarket || "meme";
      setLiveMarketTabState();
    });
  });
}

function setWorkspaceState() {
  document.querySelectorAll("[data-workspace]").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.workspace === VIEW.workspace);
  });
  document.querySelectorAll("[data-workspace-panel]").forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.workspacePanel === VIEW.workspace);
  });
  if (VIEW.data) {
    renderOverallMetrics(VIEW.data);
  }
  if (VIEW.workspace === "settings") {
    loadSecretSettings(false);
  }
  if (VIEW.workspace === "live") {
    setLiveMarketTabState();
  }
}

function setTabState() {
  document.querySelectorAll("#marketTabs [data-market]").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.market === VIEW.market);
  });
  document.querySelectorAll("#modelTabs [data-model]").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.model === VIEW.model);
  });
  const marketSelect = document.getElementById("marketSelect");
  const modelSelect = document.getElementById("modelSelect");
  if (marketSelect) marketSelect.value = VIEW.market;
  if (modelSelect) modelSelect.value = VIEW.model;
}

function updateModelTabLabels(data) {
  document.querySelectorAll("#modelTabs [data-model]").forEach((btn) => {
    const id = btn.dataset.model || "A";
    const name = marketModelName(data || {}, VIEW.market, id);
    btn.textContent = id;
    btn.title = name;
  });
  const modelSelect = document.getElementById("modelSelect");
  if (modelSelect) {
    const options = MODEL_IDS.map((id) => {
      const label = marketModelName(data || {}, VIEW.market, id);
      return `<option value="${id}">${id} | ${label}</option>`;
    }).join("");
    modelSelect.innerHTML = options;
    modelSelect.value = VIEW.model;
  }
}

function setCryptoTabState() {
  document.querySelectorAll("#cryptoModelTabs [data-crypto-model]").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.cryptoModel === VIEW.cryptoModel);
  });
}

function setLiveMarketTabState() {
  document.querySelectorAll("#liveMarketTabs [data-live-market]").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.liveMarket === VIEW.liveMarket);
  });
  document.querySelectorAll("[data-live-panel]").forEach((panel) => {
    const scope = String(panel.dataset.livePanel || "");
    const active = scope === "common" || scope === VIEW.liveMarket;
    panel.classList.toggle("active", active);
  });
}

function updateCryptoModelTabLabels(data) {
  document.querySelectorAll("#cryptoModelTabs [data-crypto-model]").forEach((btn) => {
    const id = btn.dataset.cryptoModel || "A";
    const labels = (data && data.crypto_model_labels) || {};
    btn.textContent = labels[id] || id;
  });
}

function renderOverallMetrics(data) {
  const settings = data.settings || {};
  const snap = workspaceSnapshot(data, VIEW.workspace);
  const seedLabel = document.getElementById("mSeedLabel");
  const equityLabel = document.getElementById("mEquityLabel");
  const pnlLabel = document.getElementById("mPnlLabel");
  const winrateLabel = document.getElementById("mWinrateLabel");
  if (seedLabel) {
    seedLabel.textContent = VIEW.workspace === "live" ? "성과 기준자산" : "데모 시드(모델당)";
  }
  if (equityLabel) {
    equityLabel.textContent = VIEW.workspace === "paper" ? "밈 최고 모델(ROI)" : "총 평가금액";
  }
  if (pnlLabel) {
    pnlLabel.textContent = VIEW.workspace === "paper" ? "크립토 최고 모델(ROI)" : "보정 손익";
  }
  if (winrateLabel) {
    if (VIEW.workspace === "paper") {
      winrateLabel.textContent = "활성 모델 수";
    } else if (VIEW.workspace === "live") {
      winrateLabel.textContent = "순입출금 보정";
    } else {
      winrateLabel.textContent = "통합 승률";
    }
  }
  setText(
    "mRunning",
    snap.runningText
  );
  setText("mMode", snap.modeText);
  setText("mSeed", snap.seedText);
  setText("mTotalEquity", snap.equityText || fmtUsd(snap.equityUsd));
  setText("mTotalPnl", snap.pnlText || fmtUsd(snap.pnlUsd));
  const totalPnlEl = document.getElementById("mTotalPnl");
  if (totalPnlEl) totalPnlEl.className = clsPn(snap.pnlUsd);
  setText("mWinrate", snap.winrateText);

  const autoBtn = document.getElementById("btnAuto");
  if (autoBtn) {
    autoBtn.dataset.enabled = settings.enable_autotrade ? "true" : "false";
    autoBtn.textContent = settings.enable_autotrade ? "자동매매 OFF" : "자동매매 ON";
  }
  const resetBtn = document.getElementById("btnResetDemo");
  if (resetBtn) {
    const unlocked = settings.allow_demo_reset === true;
    resetBtn.disabled = !unlocked;
    resetBtn.title = unlocked ? "" : "초기화 잠금 상태(ALLOW_DEMO_RESET=false)";
  }
  document.querySelectorAll(".mode-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.mode === settings.trade_mode);
  });
  setText("liveMode", snap.modeText);
}

function renderCycleStatus(data) {
  const cycleTs = Number(data.last_cycle_ts || 0);
  const walletTs = Number(data.last_wallet_sync_ts || 0);
  const bybitTs = Number(data.last_bybit_sync_ts || 0);
  setText("commonLastCycle", cycleTs ? fmtTs(cycleTs) : "-");
  setText("commonWalletSync", walletTs ? fmtTs(walletTs) : "-");
  setText("commonBybitSync", bybitTs ? fmtTs(bybitTs) : "-");
}

function renderModelCompare(rows, targetId) {
  const html = (rows || []).map((m) => `
      <tr>
        <td>${m.model_name || m.model_id}</td>
        <td>${fmtUsd(m.seed_usd)}</td>
        <td>${fmtUsd(m.equity_usd)}</td>
        <td class="${clsPn(m.total_pnl_usd)}">${fmtUsd(m.total_pnl_usd)}</td>
        <td class="${clsPn(m.realized_pnl_usd)}">${fmtUsd(m.realized_pnl_usd)}</td>
        <td>${Number(m.win_rate || 0).toFixed(1)}%</td>
        <td>${Number(m.open_positions || 0)}</td>
      </tr>
    `).join("");
  renderTableBody(targetId, html, 7);
}

function renderModelRanking(rows, targetId) {
  const sorted = [...(rows || [])].sort((a, b) => {
    const pnlDiff = Number(b.total_pnl_usd || 0) - Number(a.total_pnl_usd || 0);
    if (Math.abs(pnlDiff) > 1e-9) return pnlDiff;
    return Number(b.win_rate || 0) - Number(a.win_rate || 0);
  });
  const html = sorted.map((m, idx) => `
      <tr>
        <td>#${Number(m.rank || (idx + 1))}</td>
        <td>${m.model_name || m.model_id}</td>
        <td>${fmtUsd(m.seed_usd)}</td>
        <td>${fmtUsd(m.equity_usd)}</td>
        <td class="${clsPn(m.total_pnl_usd)}">${fmtUsd(m.total_pnl_usd)}</td>
        <td class="${clsPn(m.realized_pnl_usd)}">${fmtUsd(m.realized_pnl_usd)}</td>
        <td>${Number(m.win_rate || 0).toFixed(1)}%</td>
        <td>${Number(m.open_positions || 0)}</td>
      </tr>
    `).join("");
  renderTableBody(targetId, html, 8);
}

function renderDemoModelBoard(data) {
  const memeRows = data.meme_model_rankings || data.meme_model_runs || [];
  const cryptoRows = data.crypto_model_rankings || data.crypto_model_runs || [];
  const rowToHtml = (r) => {
    const seed = Number(r.seed_usd || 0);
    const eq = Number(r.equity_usd || 0);
    const pnl = Number(r.total_pnl_usd || 0);
    const roi = seed > 0 ? ((eq - seed) / seed) * 100 : 0;
    return `
      <tr>
        <td>#${Number(r.rank || 0) > 0 ? Number(r.rank) : "-"}</td>
        <td>${r.model_name || r.model_id}</td>
        <td>${fmtUsd(seed)}</td>
        <td>${fmtUsd(eq)}</td>
        <td class="${clsPn(pnl)}">${fmtUsd(pnl)}</td>
        <td class="${clsPn(roi)}">${fmtPct(roi)}</td>
        <td>${Number(r.win_rate || 0).toFixed(1)}%</td>
        <td>${Number(r.open_positions || 0)}</td>
      </tr>
    `;
  };
  const memeHtml = memeRows.map(rowToHtml).join("");
  const cryptoHtml = cryptoRows.map(rowToHtml).join("");
  renderTableBody("demoMemeRows", memeHtml, 8);
  renderTableBody("demoCryptoRows", cryptoHtml, 8);
}

function renderTrend(data) {
  const rows = (data.trend_top || [])
    .slice(0, 30)
    .map((row) => {
      const cap = Number(row.market_cap_usd || 0);
      return `<tr><td>${row.symbol}</td><td>${row.hits}</td><td>${cap > 0 ? fmtUsd(cap) : "-"}</td></tr>`;
    })
    .join("");
  renderTableBody("trendTop", rows, 3);
}

function renderTrendSources(data) {
  const rowsObj = data.trend_source_status || {};
  const rows = Object.entries(rowsObj).map(([name, row]) => {
    const status = row.enabled === false ? "disabled" : (row.status || "-");
    const nextRetry = Number(row.next_retry_seconds || 0);
    return `
      <tr>
        <td>${name}</td>
        <td>${status}${row.cached ? " (cached)" : ""}</td>
        <td>${Number(row.count || 0)}</td>
        <td>${nextRetry > 0 ? `${nextRetry}s` : "-"}</td>
        <td class="wrap">${row.error || "-"}</td>
      </tr>
    `;
  }).join("");
  renderTableBody("trendSourceRows", rows, 5);
  renderTableBody("trendSourceRowsCrypto", rows, 5);
}

function renderMemeGrades(data) {
  const rows = (data.meme_grade_criteria || []).map((r) => `
      <tr>
        <td>${r.grade || "-"}</td>
        <td>${Number(r.score_min || 0).toFixed(2)} ~ ${Number(r.score_max || 0).toFixed(2)}</td>
        <td>${r.meaning || "-"}</td>
      </tr>
    `).join("");
  renderTableBody("memeGradeRows", rows, 3);
}

function renderNewMemeFeed(data) {
  const rows = (data.new_meme_feed || []).slice(0, 80).map((r) => `
      <tr>
        <td>${r.symbol || "-"}</td>
        <td>${Number(r.market_cap_usd || 0) > 0 ? fmtUsd(r.market_cap_usd) : "-"}</td>
        <td>${Number(r.age_minutes || 0).toFixed(1)}</td>
        <td>${fmtUsd(r.volume_5m_usd)}</td>
        <td>${fmtUsd(r.liquidity_usd)}</td>
        <td>${Number(r.trend_hits || 0)}</td>
        <td>${r.is_pump_fun ? "Y" : "-"}</td>
      </tr>
    `).join("");
  renderTableBody("newMemeRows", rows, 7);
}

function drawTrendChart14d(data) {
  const svg = document.getElementById("trendChart14d");
  const info = document.getElementById("trendChartStats");
  if (!svg) return;
  const rows = (data.meme_trend_30m_db || data.trend_30m || []).slice(-48);
  if (!rows.length) {
    svg.innerHTML = "";
    if (info) info.textContent = "30분 트렌드 데이터가 아직 없습니다.";
    return;
  }
  const points = rows.map((r, i) => ({ idx: i, y: Number(r.hits || 0), date: String(r.label || r.date || "") }));
  const ys = points.map((p) => p.y);
  const yMin = 0;
  const yMax = Math.max(1, ...ys);
  const ySpan = yMax - yMin;
  const w = 900;
  const h = 220;
  const padX = 42;
  const padY = 24;
  const xMax = Math.max(1, points.length - 1);
  const xScale = (i) => padX + (i / xMax) * (w - padX * 2);
  const yScale = (y) => h - padY - ((y - yMin) / ySpan) * (h - padY * 2);
  const path = points.map((p, i) => `${i ? "L" : "M"}${xScale(i).toFixed(2)},${yScale(p.y).toFixed(2)}`).join(" ");
  const area = `${path} L ${xScale(points.length - 1).toFixed(2)},${h - padY} L ${xScale(0).toFixed(2)},${h - padY} Z`;
  svg.innerHTML = `
    <path d="${area}" fill="rgba(255, 122, 0, 0.16)" />
    <path d="${path}" fill="none" stroke="#ff9b3d" stroke-width="2.3" />
  `;
  const total = ys.reduce((acc, v) => acc + v, 0);
  const last = points[points.length - 1];
  const lastRow = rows[rows.length - 1] || {};
  const topSym = String(lastRow.top_symbol || "").trim();
  const topHits = Number(lastRow.top_hits || 0);
  const topText = topSym ? ` | TOP ${topSym} (${topHits})` : "";
  if (info) info.textContent = `최근 24시간(30분) 언급 합계 ${total} | 최신 버킷(${last.date || "-"}) ${last.y}${topText}`;
}

function renderTrendDatabase(data) {
  const stats = data.trend_db_stats || {};
  const totalRows = Number(stats.total_rows || 0);
  const minTs = Number(stats.min_ts || 0);
  const maxTs = Number(stats.max_ts || 0);
  setText("trendDbRows", totalRows > 0 ? totalRows.toLocaleString("en-US") : "-");
  setText(
    "trendDbRange",
    minTs > 0 && maxTs > 0 ? `${fmtTs(minTs)} ~ ${fmtTs(maxTs)}` : "-"
  );
  setText("cryptoTrendSync", Number(data.last_cycle_ts || 0) > 0 ? fmtTs(data.last_cycle_ts) : "-");

  const rankSource = (data.crypto_trend_rank_db && data.crypto_trend_rank_db.length)
    ? data.crypto_trend_rank_db
    : (data.trend_top || []).map((r) => ({
        symbol: r.symbol,
        hits: Number(r.hits || 0),
        score: Number(r.hits || 0),
        market_cap_usd: 0,
        last_seen_ts: Number(data.last_cycle_ts || 0),
      }));
  const rankRows = (rankSource || []).slice(0, 120).map((r) => `
      <tr>
        <td>${r.symbol || "-"}</td>
        <td>${Number(r.hits || 0)}</td>
        <td>${Number(r.score || 0).toFixed(3)}</td>
        <td>${Number(r.market_cap_usd || 0) > 0 ? fmtUsd(r.market_cap_usd) : "-"}</td>
        <td>${fmtTs(r.last_seen_ts || 0)}</td>
      </tr>
    `).join("");
  renderTableBody("cryptoTrendHistoryRows", rankRows, 5);

  const series = ((data.crypto_trend_30m_db && data.crypto_trend_30m_db.length)
    ? data.crypto_trend_30m_db
    : (data.trend_30m || [])).slice(-48);
  const points = series.map((r) => ({ date: String(r.label || ""), y: Number(r.hits || 0) }));
  drawLineChart("cryptoMentionsChart", points, "#f0b90b", "rgba(240, 185, 11, 0.18)");
  const totalHits = points.reduce((acc, p) => acc + Number(p.y || 0), 0);
  const last = series.length ? series[series.length - 1] : null;
  const topInfo = last && last.top_symbol ? ` | TOP ${last.top_symbol} (${Number(last.top_hits || 0)})` : "";
  setText(
    "cryptoMentionsStats",
    points.length
      ? `최근 24시간 언급량 ${totalHits} | 최신 ${Number(last.hits || 0)}${topInfo}`
      : "DB 트렌드 데이터가 아직 없습니다."
  );
}

function trendSourceHealth(data) {
  const src = data.trend_source_status || {};
  const rows = Object.values(src || {}).filter((v) => (v || {}).enabled !== false);
  if (!rows.length) return { ok: 0, total: 0, ratio: 0 };
  const ok = rows.filter((r) => String((r || {}).status || "").toLowerCase() === "ok" && !String((r || {}).error || "")).length;
  return { ok, total: rows.length, ratio: ok / rows.length };
}

function renderTrendInsights(data) {
  const srcHealth = trendSourceHealth(data);

  const memeSeries = (data.meme_trend_30m_db || data.trend_30m || []).slice(-48);
  const memeLast = memeSeries.length ? Number(memeSeries[memeSeries.length - 1].hits || 0) : 0;
  const memeAvg = memeSeries.length ? memeSeries.reduce((acc, r) => acc + Number(r.hits || 0), 0) / memeSeries.length : 0;
  const memeRatio = memeAvg > 0 ? memeLast / memeAvg : 0;
  const memeBrief = (data.trend_brief_meme || [])[0] || {};
  const memeMeta = memeBrief.meta || {};
  const memeSignal = String(memeMeta.signal || "밈 트렌드 요약 생성 중입니다.");
  const memeSummary = String(memeMeta.summary || "데이터 축적 중입니다.");
  const memeAction = String(memeMeta.action_hint || "신호 기반으로 후보군을 선별하세요.");
  const memeTopSymbol = String(memeMeta.top_symbol || "-");
  const memeGrowth = Number(memeMeta.total_growth_ratio ?? memeMeta.growth_ratio ?? 0);
  const memeBurst = Array.isArray(memeMeta.burst_symbols) && memeMeta.burst_symbols.length
    ? memeMeta.burst_symbols.slice(0, 5).join(", ")
    : "-";
  const memeNewcomer3h = Number(memeMeta.newcomers_3h || 0);
  const memeSmallcap = Number(memeMeta.smallcap_count || 0);
  const memeSource = memeMeta.source_totals || {};
  const memeSourceText = `X ${Number(memeSource.trader || 0)} | Wallet ${Number(memeSource.wallet || 0)} | News ${Number(memeSource.news || 0)} | Comm ${Number(memeSource.community || 0)} | Google ${Number(memeSource.google || 0)}`;
  const memeRows = [
    [
      "핵심 시그널",
      memeSignal,
      `${memeSummary} / ${memeAction}`,
    ],
    [
      "언급량 모멘텀",
      `최신 ${memeLast} / 평균 ${memeAvg.toFixed(1)} (${memeRatio.toFixed(2)}x)`,
      memeRatio >= 1.4
        ? "단기 급증 구간입니다. 상위 심볼 재평가 우선."
        : memeRatio >= 1.0
          ? "중립 이상입니다. 기존 후보 유지 + 신규 점검."
          : "관망 구간입니다. 진입 기준을 강화하세요.",
    ],
    [
      "버스트 심볼",
      memeBurst,
      `선두 ${memeTopSymbol} | 상위20 변화율 ${fmtPct(memeGrowth * 100)}`,
    ],
    [
      "신규/소형 밈 유입",
      `최근 3시간 신규 ${memeNewcomer3h}개 | 소형시총 후보 ${memeSmallcap}개`,
      memeNewcomer3h >= 5 ? "신규 유입이 강합니다. 슬리피지/유동성 확인 후 선별 진입." : "신규 유입이 약합니다. 기존 추세 추종 비중을 높이세요.",
    ],
    [
      "소스 분포",
      memeSourceText,
      `X 점유율 ${Number(memeMeta.x_share_pct || 0).toFixed(1)}% | 소스확산 ${Number(memeMeta.source_spread_ratio || 0).toFixed(2)}`,
    ],
    [
      "소스 안정성",
      `${srcHealth.ok}/${srcHealth.total} 정상`,
      srcHealth.ratio >= 0.7 ? "소스 신뢰도 양호." : "소스 오류가 많습니다. 자동진입 강도 축소 권장.",
    ],
  ];
  const memeHtml = memeRows.map((r) => `
      <tr>
        <td>${r[0]}</td>
        <td>${r[1]}</td>
        <td class="wrap">${r[2]}</td>
      </tr>
    `).join("");
  renderTableBody("memeTrendInsightRows", memeHtml, 3);

  const cryptoSeries = (data.crypto_trend_30m_db || []).slice(-48);
  const cryptoLast = cryptoSeries.length ? Number(cryptoSeries[cryptoSeries.length - 1].hits || 0) : 0;
  const cryptoAvg = cryptoSeries.length
    ? cryptoSeries.reduce((acc, r) => acc + Number(r.hits || 0), 0) / cryptoSeries.length
    : 0;
  const cryptoRatio = cryptoAvg > 0 ? cryptoLast / cryptoAvg : 0;
  const activeModel = String(VIEW.cryptoModel || "A");
  const signals = data.model_views?.[activeModel]?.crypto?.signals || [];
  const entryCandidates = signals.filter((s) => Number(s.score || 0) >= Number(s.entry_threshold || 0)).length;
  const cryptoBrief = (data.trend_brief_crypto || [])[0] || {};
  const cryptoMeta = cryptoBrief.meta || {};
  const cryptoSignal = String(cryptoMeta.signal || "크립토 트렌드 요약 생성 중입니다.");
  const cryptoSummary = String(cryptoMeta.summary || "데이터 축적 중입니다.");
  const cryptoAction = String(cryptoMeta.action_hint || "모델 임계값 이상 후보만 선별하세요.");
  const cryptoTopSymbol = String(cryptoMeta.top_symbol || "-");
  const cryptoGrowth = Number(cryptoMeta.total_growth_ratio ?? cryptoMeta.growth_ratio ?? 0);
  const cryptoBurst = Array.isArray(cryptoMeta.burst_symbols) && cryptoMeta.burst_symbols.length
    ? cryptoMeta.burst_symbols.slice(0, 5).join(", ")
    : "-";
  const cryptoRankBand = String(cryptoMeta.rank_band || "-");
  const cryptoSource = cryptoMeta.source_totals || {};
  const cryptoSourceText = `X ${Number(cryptoSource.trader || 0)} | Wallet ${Number(cryptoSource.wallet || 0)} | News ${Number(cryptoSource.news || 0)} | Comm ${Number(cryptoSource.community || 0)} | Google ${Number(cryptoSource.google || 0)}`;
  const cryptoRows = [
    [
      "핵심 시그널",
      cryptoSignal,
      `${cryptoSummary} / ${cryptoAction}`,
    ],
    [
      "이슈 버스트 알트",
      cryptoBurst,
      `선두 ${cryptoTopSymbol} | 상위20 변화율 ${fmtPct(cryptoGrowth * 100)}`,
    ],
    [
      "시총 랭크대",
      cryptoRankBand,
      `11~300위 핵심 후보 ${Number(cryptoMeta.mid_alt_count || 0)}개`,
    ],
    [
      "언급량 모멘텀",
      `최신 ${cryptoLast} / 평균 ${cryptoAvg.toFixed(1)} (${cryptoRatio.toFixed(2)}x)`,
      cryptoRatio >= 1.3
        ? "시장 관심 증가 구간입니다. 시그널 상위 집중."
        : cryptoRatio >= 1.0
          ? "중립 구간입니다. 선별 진입 유지."
          : "관심 약화 구간입니다. 포지션 보수 운용 권장.",
    ],
    [
      "선택 모델 진입후보",
      `${marketModelName(data, "crypto", activeModel)} 기준 ${entryCandidates}개`,
      entryCandidates > 0 ? "후보가 존재합니다. 포지션 한도/리스크 확인 후 진입." : "현재 조건 충족 후보가 없습니다.",
    ],
    [
      "소스 분포",
      cryptoSourceText,
      `X 점유율 ${Number(cryptoMeta.x_share_pct || 0).toFixed(1)}% | 소스확산 ${Number(cryptoMeta.source_spread_ratio || 0).toFixed(2)}`,
    ],
    [
      "소스 안정성",
      `${srcHealth.ok}/${srcHealth.total} 정상`,
      srcHealth.ratio >= 0.7 ? "소스 신뢰도 양호." : "소스 오류가 많습니다. 시그널 신뢰도 하향 고려.",
    ],
  ];
  const cryptoHtml = cryptoRows.map((r) => `
      <tr>
        <td>${r[0]}</td>
        <td>${r[1]}</td>
        <td class="wrap">${r[2]}</td>
      </tr>
    `).join("");
  renderTableBody("cryptoTrendInsightRows", cryptoHtml, 3);
}

function renderTrendBriefLogs(data) {
  const renderRows = (rows) => (rows || []).slice(0, 120).map((row) => {
    const meta = row.meta || {};
    const signal = String(meta.signal || meta.theme || "-");
    const top = String(meta.top_symbol || "-");
    const ratio = Number(meta.total_growth_ratio ?? meta.growth_ratio ?? 0);
    const ratioText = Number.isFinite(ratio) ? fmtPct(ratio * 100) : "-";
    const summary = String(meta.summary || row.detail || "-");
    const action = String(meta.action_hint || "");
    const summaryWithAction = action ? `${summary} / 액션: ${action}` : summary;
    return `
      <tr>
        <td>${fmtTs(row.ts)}</td>
        <td>${signal}</td>
        <td>${top}</td>
        <td class="${clsPn(ratio)}">${ratioText}</td>
        <td class="wrap">${summaryWithAction}</td>
      </tr>
    `;
  }).join("");
  renderTableBody("memeTrendLogRows", renderRows(data.trend_brief_meme || []), 5);
  renderTableBody("cryptoTrendLogRows", renderRows(data.trend_brief_crypto || []), 5);
}

const DONUT_COLORS = ["#3d8bff", "#00c2ff", "#f0b90b", "#28c76f", "#ff8a3d", "#ff6b6b", "#9f7aea", "#7dd3fc"];

function renderTrendDonut(chartId, legendId, statsId, rows, marketLabel) {
  const chartEl = document.getElementById(chartId);
  const legendEl = document.getElementById(legendId);
  const statsEl = document.getElementById(statsId);
  const series = Array.isArray(rows) ? rows.filter((r) => Number(r.hits || 0) > 0) : [];
  if (!chartEl || !legendEl || !statsEl) return;
  if (!series.length) {
    chartEl.style.background = "conic-gradient(#223347 0 360deg)";
    legendEl.innerHTML = `<div class="donut-legend-row"><span class="donut-legend-color" style="background:#3a4f68"></span><span>데이터 없음</span><span>-</span></div>`;
    statsEl.textContent = `${marketLabel} 점유율 데이터가 아직 없습니다.`;
    return;
  }
  const total = series.reduce((acc, row) => acc + Number(row.hits || 0), 0);
  const slices = [];
  let cursor = 0;
  series.forEach((row, idx) => {
    const pct = Math.max(0, Number(row.share_pct ?? (total > 0 ? (Number(row.hits || 0) / total) * 100 : 0)));
    const next = Math.min(100, cursor + pct);
    const color = DONUT_COLORS[idx % DONUT_COLORS.length];
    slices.push(`${color} ${cursor.toFixed(3)}% ${next.toFixed(3)}%`);
    cursor = next;
  });
  if (cursor < 100) slices.push(`#1f3247 ${cursor.toFixed(3)}% 100%`);
  chartEl.style.background = `conic-gradient(${slices.join(", ")})`;
  const top = series[0] || {};
  statsEl.textContent = `총 ${Number(total).toLocaleString("en-US")} hits | 1위 ${top.symbol || "-"} ${Number(top.share_pct || 0).toFixed(1)}%`;
  legendEl.innerHTML = series.slice(0, 8).map((row, idx) => `
      <div class="donut-legend-row">
        <span class="donut-legend-color" style="background:${DONUT_COLORS[idx % DONUT_COLORS.length]}"></span>
        <span>${row.symbol || "-"}</span>
        <span>${Number(row.share_pct || 0).toFixed(1)}%</span>
      </div>
    `).join("");
}

function renderTrendPeriodTable(rowId, rows, limit) {
  const picked = (rows || []).slice(-Math.max(1, Number(limit || 20))).reverse();
  const html = picked.map((row) => `
      <tr>
        <td>${row.label || fmtTs(row.ts || 0)}</td>
        <td>${Number(row.total_hits || 0)}</td>
        <td>${row.top_symbol || "-"}${Number(row.top_hits || 0) > 0 ? ` (${Number(row.top_hits || 0)})` : ""}</td>
        <td class="wrap">${row.breakdown_text || "-"}</td>
      </tr>
    `).join("");
  renderTableBody(rowId, html, 4);
}

function renderTrendDistribution(data) {
  renderTrendDonut("memeTrendDonut", "memeTrendDonutLegend", "memeTrendDonutStats", data.meme_trend_share_24h || [], "밈");
  renderTrendDonut("cryptoTrendDonut", "cryptoTrendDonutLegend", "cryptoTrendDonutStats", data.crypto_trend_share_24h || [], "크립토");

  renderTrendPeriodTable("memeTrendHourlyRows", data.meme_trend_hourly_db || [], 24);
  renderTrendPeriodTable("memeTrendDailyRows", data.meme_trend_daily_db || [], 14);
  renderTrendPeriodTable("memeTrendWeeklyRows", data.meme_trend_weekly_db || [], 12);
  renderTrendPeriodTable("cryptoTrendHourlyRows", data.crypto_trend_hourly_db || [], 24);
  renderTrendPeriodTable("cryptoTrendDailyRows", data.crypto_trend_daily_db || [], 14);
  renderTrendPeriodTable("cryptoTrendWeeklyRows", data.crypto_trend_weekly_db || [], 12);
}

function renderTuneHistory(data) {
  const history = (data.model_tune_history || []).slice(0, 300);
  const historyHtml = history.map((row) => {
    const tuned = Boolean(row.tuned);
    const note = row.note_ko || row.note_code || "-";
    const diff = `TH ${Number(row.threshold_before || 0).toFixed(4)}->${Number(row.threshold_after || 0).toFixed(4)} | TPx ${Number(row.tp_mul_before || 0).toFixed(2)}->${Number(row.tp_mul_after || 0).toFixed(2)} | SLx ${Number(row.sl_mul_before || 0).toFixed(2)}->${Number(row.sl_mul_after || 0).toFixed(2)}`;
    const metrics = `closed ${Number(row.closed_trades || 0)} | WR ${Number(row.win_rate || 0).toFixed(1)}% | PNL ${fmtUsd(row.pnl_usd || 0)} | PF ${Number(row.profit_factor || 0).toFixed(2)}`;
    const variant = tuned && row.parent_variant_id && row.parent_variant_id !== row.variant_id
      ? `${row.parent_variant_id} -> ${row.variant_id}`
      : (row.variant_id || "-");
    return `
      <tr>
        <td>${fmtTs(row.ts || 0)}</td>
        <td>${row.model_name || row.model_id || "-"}</td>
        <td>${variant}</td>
        <td>${tuned ? "튜닝적용" : "유지"} / ${note}</td>
        <td>${metrics}</td>
        <td class="wrap">${diff}</td>
      </tr>
    `;
  }).join("");
  renderTableBody("modelTuneHistoryRows", historyHtml, 6);

  const ranks = (data.model_tune_variant_rank || []).slice(0, 120);
  const rankHtml = ranks.map((row) => `
      <tr>
        <td>#${Number(row.rank || 0)}</td>
        <td>${row.model_name || row.model_id || "-"}</td>
        <td>${row.variant_id || "-"}</td>
        <td class="${clsPn(row.avg_pnl_usd)}">${fmtUsd(row.avg_pnl_usd || 0)}</td>
        <td class="${clsPn(row.last_pnl_usd)}">${fmtUsd(row.last_pnl_usd || 0)}</td>
        <td>${Number(row.eval_count || 0)}</td>
        <td class="wrap">${row.last_note_ko || "-"}</td>
      </tr>
    `).join("");
  renderTableBody("modelTuneVariantRows", rankHtml, 7);
}

function renderWallet(data) {
  const rows = (data.wallet_assets || []).slice(0, 40).map((a) => `
      <tr>
        <td>${a.symbol || "-"}</td>
        <td>${Number(a.qty || 0).toLocaleString("en-US", { maximumFractionDigits: 6 })}</td>
        <td>${Number(a.price_usd || 0) > 0 ? fmtUsd(a.price_usd) : "-"}</td>
        <td>${fmtUsd(a.value_usd)}</td>
      </tr>
    `).join("");
  renderTableBody("walletRows", rows, 4);
}

function renderBybitAssets(data) {
  const rows = (data.bybit_assets || []).slice(0, 30).map((a) => `
      <tr>
        <td>${a.coin || "-"}</td>
        <td>${fmtUsd(a.usd_value)}</td>
        <td>${Number(a.equity || 0).toFixed(6)}</td>
      </tr>
    `).join("");
  renderTableBody("bybitAssetRows", rows, 3);
}

function detectLiveTradeMarket(row) {
  const market = String((row || {}).market || "").toLowerCase();
  if (market === "meme" || market === "crypto") return market;
  const source = String((row || {}).source || "").toLowerCase();
  if (source.includes("meme")) return "meme";
  if (source.includes("crypto") || source.includes("bybit")) return "crypto";
  return "meme";
}

function buildLiveDailyCumulativeRows(rows) {
  const dailyMap = new Map();
  const ordered = [...(rows || [])].sort((a, b) => Number(a.ts || 0) - Number(b.ts || 0));
  ordered.forEach((row) => {
    if (String((row || {}).side || "").toLowerCase() !== "sell") return;
    const ts = Number((row || {}).ts || 0);
    const pnl = Number((row || {}).pnl_usd || 0);
    const dt = new Date(ts * 1000);
    if (!Number.isFinite(dt.getTime())) return;
    const dateKey = `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, "0")}-${String(dt.getDate()).padStart(2, "0")}`;
    const rec = dailyMap.get(dateKey) || { date: dateKey, realized_pnl_usd: 0, sell_count: 0 };
    rec.realized_pnl_usd += pnl;
    rec.sell_count += 1;
    dailyMap.set(dateKey, rec);
  });
  const out = Array.from(dailyMap.values()).sort((a, b) => String(a.date).localeCompare(String(b.date)));
  let cumulative = 0;
  out.forEach((row) => {
    cumulative += Number(row.realized_pnl_usd || 0);
    row.cumulative_pnl_usd = Number(cumulative);
  });
  return out;
}

function renderLiveSection(data) {
  const totalAssetUsd = Number(data.live_equity_usd || 0);
  const walletTotalUsd = Number(data.wallet_total_usd ?? sumBy(data.wallet_assets || [], "value_usd"));
  const liveMemeValueUsd = Number(
    data.live_managed_meme_value_usd ??
    data.live_meme_value_usd ??
    sumBy(data.live_meme_positions || [], "value_usd")
  );
  const livePerfAnchorUsd = Number(data.live_perf_anchor_usd ?? data.live_seed_usd ?? totalAssetUsd);
  const liveNetFlowUsd = Number(data.live_net_flow_usd ?? 0);
  const liveAdjustedEquityUsd = Number(data.live_adjusted_equity_usd ?? (totalAssetUsd - liveNetFlowUsd));
  const livePerfPnlUsd = Number(data.live_perf_pnl_usd ?? (liveAdjustedEquityUsd - livePerfAnchorUsd));
  const livePerfRoiPct = Number(
    data.live_perf_roi_pct ?? (livePerfAnchorUsd > 0 ? ((livePerfPnlUsd / livePerfAnchorUsd) * 100) : 0)
  );
  const livePerfAnchorTs = Number(data.live_perf_anchor_ts || 0);
  setText("liveAssetTotal", fmtUsd(totalAssetUsd));
  setText("liveAssetTotal2", fmtUsd(totalAssetUsd));
  setText("livePerfAnchor", fmtUsd(livePerfAnchorUsd));
  setText("liveNetFlow", `${liveNetFlowUsd >= 0 ? "+" : "-"}${fmtUsd(Math.abs(liveNetFlowUsd)).replace("$", "")} USD`);
  setText("liveAdjEquity", fmtUsd(liveAdjustedEquityUsd));
  setText("livePerfPnl", `${fmtUsd(livePerfPnlUsd)} (${fmtPct(livePerfRoiPct)})`);
  setText("livePerfAnchorInfo", livePerfAnchorTs > 0 ? fmtTs(livePerfAnchorTs) : "-");
  setText("liveWalletTotal", fmtUsd(walletTotalUsd));
  setText("liveMemeValue", fmtUsd(liveMemeValueUsd));
  const livePerfPnlEl = document.getElementById("livePerfPnl");
  if (livePerfPnlEl) livePerfPnlEl.className = clsPn(livePerfPnlUsd);
  const liveNetFlowEl = document.getElementById("liveNetFlow");
  if (liveNetFlowEl) liveNetFlowEl.className = clsPn(liveNetFlowUsd);

  const managedRows = (data.live_managed_meme_positions || []).slice(0, 120);
  const managedHtml = managedRows.map((p) => {
    const avg = Number(p.avg_price_usd || 0);
    const cur = Number(p.current_price_usd || 0);
    const value = Number(p.value_usd || 0);
    const pnl = Number(p.pnl_usd || 0);
    const pnlPct = Number(p.pnl_pct || 0);
    const tp = Number(p.tp_pct || 0) * 100;
    const sl = Number(p.sl_pct || 0) * 100;
    return `
      <tr>
        <td>${p.model_name || p.model_id || "-"}</td>
        <td>${p.symbol || "-"}</td>
        <td>${p.strategy || "-"}</td>
        <td>${avg > 0 ? fmtUsdPrice(avg) : "-"}</td>
        <td>${cur > 0 ? fmtUsdPrice(cur) : "-"}</td>
        <td>${fmtUsd(value)}</td>
        <td class="${clsPn(pnl)}">${fmtUsd(pnl)} (${fmtPct(pnlPct)})</td>
        <td>TP ${tp.toFixed(1)}% / SL ${sl.toFixed(1)}%</td>
        <td class="wrap">${p.reason || "-"}</td>
      </tr>
    `;
  }).join("");
  renderTableBody("liveManagedMemeRows", managedHtml, 9);

  const memeRows = (data.live_meme_positions || []).slice(0, 120);
  const memeTotalUsd = memeRows.reduce((acc, p) => acc + Number(p.value_usd || 0), 0);
  const memeHtml = memeRows.map((p) => {
    const qty = Number(p.qty || 0);
    const price = Number(p.price_usd || 0);
    const entry = Number(p.entry_price_usd || 0);
    const valueRaw = Number(p.value_usd || 0);
    const value = valueRaw > 0 ? valueRaw : (qty > 0 && price > 0 ? qty * price : 0);
    const basis = Number(p.cost_basis_usd || 0);
    const pnlRaw = Number(p.pnl_usd || 0);
    const pnl = Math.abs(pnlRaw) > 0 ? pnlRaw : (basis > 0 ? value - basis : 0);
    const pnlPctRaw = Number(p.pnl_pct || 0);
    const pnlPct = Math.abs(pnlPctRaw) > 0 ? pnlPctRaw : (basis > 0 ? (pnl / basis) * 100 : 0);
    const w = memeTotalUsd > 0 ? (value / memeTotalUsd) * 100 : 0;
    return `
      <tr>
        <td>${p.symbol || "-"}</td>
        <td>${qty.toLocaleString("en-US", { maximumFractionDigits: 6 })}</td>
        <td>${entry > 0 ? fmtUsdPrice(entry) : "-"}</td>
        <td>${price > 0 ? fmtUsdPrice(price) : "-"}</td>
        <td class="${clsPn(pnl)}">${fmtUsd(pnl)} (${fmtPct(pnlPct)})</td>
        <td>${fmtUsd(value)}</td>
        <td>${w.toFixed(2)}%</td>
        <td class="wrap">${p.token_address || "-"}</td>
      </tr>
    `;
  }).join("");
  renderTableBody("liveMemePosRows", memeHtml, 8);

  const cryptoRows = (data.crypto_live_positions || data.bybit_live_positions || []).slice(0, 120);
  const cryptoUpnl = cryptoRows.reduce(
    (acc, p) =>
      acc +
      Number(
        p.unrealisedPnl ??
          p.unrealised_pnl ??
          p.unrealizedPnl ??
          p.unrealized_pnl ??
          0
      ),
    0
  );
  const managedMemeUpnl = Number(data.live_managed_meme_upnl_usd ?? managedRows.reduce((acc, p) => acc + Number(p.pnl_usd || 0), 0));
  const upnl = Number(cryptoUpnl + managedMemeUpnl);
  setText("liveMemePosCount", String(memeRows.length));
  setText("liveManagedMemePosCount", String(managedRows.length));
  setText("liveCryptoPosCount", String(cryptoRows.length));
  setText("livePosCount", String(managedRows.length + cryptoRows.length));
  setText("liveUpnl", fmtUsd(upnl));
  const liveUpnlEl = document.getElementById("liveUpnl");
  if (liveUpnlEl) liveUpnlEl.className = clsPn(upnl);

  const allLiveTrades = (data.live_trade_logs || data.live_meme_trades || []).slice(0, 1200);
  const liveMemeTrades = allLiveTrades.filter((t) => detectLiveTradeMarket(t) === "meme");
  const liveCryptoTrades = allLiveTrades.filter((t) => detectLiveTradeMarket(t) === "crypto");

  const tradeRow = (t) => `
      <tr>
        <td>${fmtTs(t.ts)}</td>
        <td>${String(detectLiveTradeMarket(t) || "-").toUpperCase()}</td>
        <td>${t.model_name || t.model_id || "-"}</td>
        <td>${t.side || "-"}</td>
        <td>${t.symbol || "-"}</td>
        <td>${fmtUsdPrice(t.price_usd)}</td>
        <td>${fmtUsd(t.notional_usd)}</td>
        <td class="${clsPn(t.pnl_usd)}">${fmtUsd(t.pnl_usd)} (${fmtPct(t.pnl_pct)})</td>
        <td class="wrap">${t.reason || "-"}</td>
      </tr>
    `;
  renderTableBody("liveMemeTradeRows", liveMemeTrades.slice(0, 500).map(tradeRow).join(""), 9);
  renderTableBody("liveCryptoTradeRows", liveCryptoTrades.slice(0, 500).map(tradeRow).join(""), 9);

  const runtimeRows = (data.runtime_feedback_recent || [])
    .filter((row) => {
      const src = String(row.source || "").toLowerCase();
      const lvl = String(row.level || "").toLowerCase();
      if (!["error", "warn", "warning", "info"].includes(lvl)) return false;
      return src.startsWith("live:") || src === "core:memecoin" || src === "core:crypto";
    })
    .slice(0, 300);
  const runtimeRowHtml = (row) => {
    const meta = row.meta || {};
    const title = String(meta.title || row.status || "-");
    const detail = String(row.detail || row.error || "-");
    return `
      <tr>
        <td>${fmtTs(row.ts)}</td>
        <td>${String(row.level || "-").toUpperCase()}</td>
        <td>${row.source || "-"}</td>
        <td>${title}</td>
        <td class="wrap">${detail}</td>
      </tr>
    `;
  };
  const memeRuntimeRows = runtimeRows.filter((row) => {
    const src = String(row.source || "").toLowerCase();
    return src.includes("meme") || src.includes("memecoin") || src === "core:memecoin";
  });
  const cryptoRuntimeRows = runtimeRows.filter((row) => {
    const src = String(row.source || "").toLowerCase();
    return src.includes("crypto") || src.includes("bybit") || src === "core:crypto";
  });
  renderTableBody("liveMemeRuntimeRows", memeRuntimeRows.map(runtimeRowHtml).join(""), 5);
  renderTableBody("liveCryptoRuntimeRows", cryptoRuntimeRows.map(runtimeRowHtml).join(""), 5);

  const memeDaily = buildLiveDailyCumulativeRows(liveMemeTrades);
  const cryptoDaily = buildLiveDailyCumulativeRows(liveCryptoTrades);
  const renderDailyRows = (rows) => rows.slice(-120).reverse().map((row) => `
      <tr>
        <td>${row.date}</td>
        <td class="${clsPn(row.realized_pnl_usd)}">${fmtUsd(row.realized_pnl_usd)}</td>
        <td class="${clsPn(row.cumulative_pnl_usd)}">${fmtUsd(row.cumulative_pnl_usd)}</td>
        <td>${Number(row.sell_count || 0)}</td>
      </tr>
    `).join("");
  renderTableBody("liveMemeDailyPnlRows", renderDailyRows(memeDaily), 4);
  renderTableBody("liveCryptoDailyPnlRows", renderDailyRows(cryptoDaily), 4);
  drawLineChart(
    "liveMemePnlChart",
    memeDaily.slice(-120).map((row) => ({ date: row.date, y: Number(row.cumulative_pnl_usd || 0) })),
    "#3d8bff",
    "rgba(61, 139, 255, 0.16)"
  );
  drawLineChart(
    "liveCryptoPnlChart",
    cryptoDaily.slice(-120).map((row) => ({ date: row.date, y: Number(row.cumulative_pnl_usd || 0) })),
    "#28c76f",
    "rgba(40, 199, 111, 0.16)"
  );
  const memeRealized = memeDaily.reduce((acc, row) => acc + Number(row.realized_pnl_usd || 0), 0);
  const cryptoRealized = cryptoDaily.reduce((acc, row) => acc + Number(row.realized_pnl_usd || 0), 0);
  const memeCum = memeDaily.length ? Number(memeDaily[memeDaily.length - 1].cumulative_pnl_usd || 0) : 0;
  const cryptoCum = cryptoDaily.length ? Number(cryptoDaily[cryptoDaily.length - 1].cumulative_pnl_usd || 0) : 0;
  setText(
    "liveMemePnlStats",
    memeDaily.length
      ? `실현 합계 ${fmtUsd(memeRealized)} | 누적 ${fmtUsd(memeCum)} | 일수 ${memeDaily.length}`
      : "실전 MEME 청산 이력이 아직 없습니다."
  );
  setText(
    "liveCryptoPnlStats",
    cryptoDaily.length
      ? `실현 합계 ${fmtUsd(cryptoRealized)} | 누적 ${fmtUsd(cryptoCum)} | 일수 ${cryptoDaily.length}`
      : "실전 CRYPTO 청산 이력이 아직 없습니다."
  );

  const html = cryptoRows.map((p) => {
    const size = Number(p.size ?? p.qty ?? 0);
    const avg = Number(p.avgPrice ?? p.avg_price ?? p.entry_price ?? 0);
    const mark = Number(p.markPrice ?? p.mark_price ?? p.price ?? 0);
    const posValueRaw = Number(p.position_value ?? p.positionValue ?? 0);
    const posValue = posValueRaw > 0 ? posValueRaw : Math.abs(size * mark);
    const lev = Number(p.leverage ?? 1);
    const posUpnl = Number(
      p.unrealisedPnl ?? p.unrealised_pnl ?? p.unrealizedPnl ?? p.unrealized_pnl ?? 0
    );
    const roe = Number(p.roe ?? p.roe_pct ?? 0);
    return `
      <tr>
        <td>${p.side || "-"}</td>
        <td>${p.symbol || "-"}</td>
        <td>${size.toLocaleString("en-US", { maximumFractionDigits: 4 })}</td>
        <td>${avg > 0 ? fmtUsd(avg) : "-"}</td>
        <td>${mark > 0 ? fmtUsd(mark) : "-"}</td>
        <td>${fmtUsd(posValue)}</td>
        <td class="${clsPn(posUpnl)}">${fmtUsd(posUpnl)}</td>
        <td class="${clsPn(roe)}">${fmtPct(roe)}</td>
        <td>${lev > 0 ? `${lev.toFixed(2)}x` : "-"}</td>
      </tr>
    `;
  }).join("");
  renderTableBody("livePosRows", html, 9);
}

function modelListText(data, market, ids) {
  const labels = ids.map((id) => marketModelName(data, market, id));
  return labels.length ? labels.join(", ") : "-";
}

function modelTuneText(modelTune = {}) {
  const threshold = Number(modelTune.threshold ?? NaN);
  const tpMul = Number(modelTune.tp_mul ?? NaN);
  const slMul = Number(modelTune.sl_mul ?? NaN);
  const parts = [];
  if (Number.isFinite(threshold)) parts.push(`TH ${threshold.toFixed(3)}`);
  if (Number.isFinite(tpMul)) parts.push(`TPx ${tpMul.toFixed(2)}`);
  if (Number.isFinite(slMul)) parts.push(`SLx ${slMul.toFixed(2)}`);
  return parts.join(" | ");
}

function liveModelConfigText(data, market, modelId) {
  const profiles = (data && data.model_profiles) || {};
  const tuneMap = (data && data.model_autotune) || {};
  const profile = profiles[modelId] || {};
  const tune = tuneMap[modelId] || {};
  if (market === "meme") {
    const meme = profile.meme || {};
    const parts = [];
    if (meme.strategy_mode) parts.push(`전략 ${meme.strategy_mode}`);
    if (Number.isFinite(Number(meme.threshold_floor))) parts.push(`기본하한 ${Number(meme.threshold_floor).toFixed(3)}`);
    if (Number.isFinite(Number(meme.demo_score_floor))) parts.push(`데모하한 ${Number(meme.demo_score_floor).toFixed(2)}`);
    if (meme.paper_min_grade) parts.push(`최소등급 ${meme.paper_min_grade}`);
    if (Number.isFinite(Number(meme.swing_hold_days))) parts.push(`홀딩 ${Number(meme.swing_hold_days)}d`);
    const tuneText = modelTuneText(tune);
    if (tuneText) parts.push(`튜닝 ${tuneText}`);
    return parts.join(" | ") || "-";
  }
  const crypto = profile.crypto || {};
  const parts = [];
  if (Number.isFinite(Number(crypto.rank_max))) parts.push(`시총순위<=${Number(crypto.rank_max)}`);
  if (Number.isFinite(Number(crypto.trend_stack_min))) parts.push(`Trend>=${Number(crypto.trend_stack_min).toFixed(2)}`);
  if (Number.isFinite(Number(crypto.overheat_max))) parts.push(`Overheat<=${Number(crypto.overheat_max).toFixed(2)}`);
  if (Array.isArray(crypto.leverage_range) && crypto.leverage_range.length >= 2) {
    parts.push(`Lev ${Number(crypto.leverage_range[0]).toFixed(1)}-${Number(crypto.leverage_range[1]).toFixed(1)}x`);
  }
  if (Number.isFinite(Number(crypto.hard_roe_cut))) parts.push(`HardROE ${Number(crypto.hard_roe_cut).toFixed(2)}`);
  const tuneText = modelTuneText(tune);
  if (tuneText) parts.push(`튜닝 ${tuneText}`);
  return parts.join(" | ") || "-";
}

function renderLiveMarketToggles(data) {
  const settings = data.settings || {};
  const memeEnabled = settings.live_enable_meme !== false;
  const cryptoEnabled = settings.live_enable_crypto !== false;
  if (!LIVE_MARKET_DIRTY) {
    const memeEl = document.getElementById("liveToggleMeme");
    const cryptoEl = document.getElementById("liveToggleCrypto");
    if (memeEl) memeEl.checked = memeEnabled;
    if (cryptoEl) cryptoEl.checked = cryptoEnabled;
    setText("liveMarketSaveMsg", "현재 설정 반영됨");
  }
}

function renderLiveModelSelectors(data) {
  const settings = data.settings || {};
  const memeSelected = parseModelCsv(settings.live_meme_models || settings.meme_autotrade_models || "A,B,C");
  const cryptoSelected = parseModelCsv(settings.live_crypto_models || settings.crypto_autotrade_models || "A,B,C");
  MODEL_IDS.forEach((id) => {
    setText(`liveLabelMeme${id}`, marketModelName(data, "meme", id));
    setText(`liveLabelCrypto${id}`, marketModelName(data, "crypto", id));
  });
  if (!LIVE_MODEL_DIRTY.meme) {
    MODEL_IDS.forEach((id) => {
      const m = document.querySelector(`input[type="checkbox"][data-live-market="meme"][value="${id}"]`);
      if (m) m.checked = memeSelected.includes(id);
    });
    setText("liveModelSaveMsgMeme", "현재 설정 반영됨");
  }
  if (!LIVE_MODEL_DIRTY.crypto) {
    MODEL_IDS.forEach((id) => {
      const c = document.querySelector(`input[type="checkbox"][data-live-market="crypto"][value="${id}"]`);
      if (c) c.checked = cryptoSelected.includes(id);
    });
    setText("liveModelSaveMsgCrypto", "현재 설정 반영됨");
  }

  const memeLive = settings.live_enable_meme !== false;
  const cryptoLive = settings.live_enable_crypto !== false;
  const rows = [];
  const pushRows = (market, selectedIds, liveEnabled) => {
    const marketLabel = market === "meme" ? "밈" : "크립토";
    if (!liveEnabled) {
      rows.push({
        market: marketLabel,
        model: "-",
        config: "시장 OFF",
      });
      return;
    }
    if (!selectedIds.length) {
      rows.push({
        market: marketLabel,
        model: "-",
        config: "ON 상태인데 선택된 모델이 없습니다.",
      });
      return;
    }
    selectedIds.forEach((id) => {
      rows.push({
        market: marketLabel,
        model: marketModelName(data, market, id),
        config: `ON | ${liveModelConfigText(data, market, id)}`,
      });
    });
  };
  pushRows("meme", memeSelected, memeLive);
  pushRows("crypto", cryptoSelected, cryptoLive);

  const html = rows.map((r) => `
      <tr>
        <td>${r.market}</td>
        <td>${r.model}</td>
        <td class="wrap">${r.config}</td>
      </tr>
    `).join("");
  renderTableBody("liveModelConfigRows", html, 3);
}

function renderSettings(data) {
  const s = data.settings || {};
  const memeModelText = modelListText(data, "meme", parseModelCsv(s.meme_autotrade_models || "A,B,C"));
  const cryptoModelText = modelListText(data, "crypto", parseModelCsv(s.crypto_autotrade_models || "A,B,C"));
  const liveMemeModelText = s.live_enable_meme === false
    ? "OFF (시장 비활성)"
    : modelListText(data, "meme", parseModelCsv(s.live_meme_models || s.meme_autotrade_models || "A,B,C"));
  const liveCryptoModelText = s.live_enable_crypto === false
    ? "OFF (시장 비활성)"
    : modelListText(data, "crypto", parseModelCsv(s.live_crypto_models || s.crypto_autotrade_models || "A,B,C"));
  const rows = [
    ["TRADE_MODE", String(s.trade_mode || "-").toUpperCase(), "현재 거래 모드"],
    ["ENABLE_AUTOTRADE", s.enable_autotrade ? "ON" : "OFF", "자동매매 동작 여부"],
    ["ENABLE_LIVE_EXECUTION", s.enable_live_execution ? "ON" : "OFF", "실거래 주문 허용"],
    ["LIVE_ENABLE_MEME", s.live_enable_meme ? "ON" : "OFF", "실전 밈 엔진 ON/OFF"],
    ["LIVE_ENABLE_CRYPTO", s.live_enable_crypto ? "ON" : "OFF", "실전 크립토 엔진 ON/OFF"],
    ["MIN_WALLET_ASSET_USD", Number(s.min_wallet_asset_usd || 0).toFixed(2), "지갑 표시 최소 USD 필터"],
    ["MEME_AUTOTRADE_MODELS", memeModelText, "데모 밈 자동매매 활성 모델"],
    ["CRYPTO_AUTOTRADE_MODELS", cryptoModelText, "데모 크립토 자동매매 활성 모델"],
    ["LIVE_MEME_MODELS", liveMemeModelText, "실전 밈 체결 활성 모델"],
    ["LIVE_CRYPTO_MODELS", liveCryptoModelText, "실전 크립토 체결 활성 모델"],
    ["BYBIT_MAX_POSITIONS", Number(s.bybit_max_positions || 0), "실전 최대 동시 포지션"],
    ["BYBIT_ORDER_PCT", Number(s.bybit_order_pct || 0).toFixed(2), "실전 주문 비율(잔고 대비)"],
    ["BYBIT_LEVERAGE_MIN/MAX", `${Number(s.bybit_leverage_min || 0).toFixed(2)} / ${Number(s.bybit_leverage_max || 0).toFixed(2)}`, "실전 레버리지 범위"],
    ["MEME_MAX_POSITIONS", Number(s.meme_max_positions || 0), "밈 최대 동시 포지션"],
    ["MEME_MIN_ENTRY_GRADE", String(s.meme_min_entry_grade || "-"), "밈 진입 최소 등급"],
    ["MODEL_AUTOTUNE_INTERVAL_HOURS", `${Number(s.model_autotune_interval_hours || 0)}h`, "모델 자동 튜닝 주기"],
    ["SCAN_INTERVAL_SECONDS", `${Number(s.scan_interval_seconds || 0)}s`, "엔진 스캔 주기"],
  ];

  const liveHtml = rows.map((r) => `
      <tr>
        <td>${r[0]}</td>
        <td>${r[1]}</td>
      </tr>
    `).join("");
  renderTableBody("settingsRows", liveHtml, 2);

  const settingsHtml = rows.map((r) => `
      <tr>
        <td>${r[0]}</td>
        <td>${r[1]}</td>
        <td class="wrap">${r[2]}</td>
      </tr>
    `).join("");
  renderTableBody("settingsRowsClone", settingsHtml, 3);
}

function renderCryptoTrend(data) {
  const pool = (data.macro_trend_pool || []).slice(0, 80);
  const poolRows = pool.map((sym, idx) => `
      <tr>
        <td>${idx + 1}</td>
        <td>${sym || "-"}</td>
      </tr>
    `).join("");
  renderTableBody("macroPoolRows", poolRows, 2);

  const activeModel = String(VIEW.cryptoModel || "A");
  const modelViews = data.model_views || {};
  const labels = data.crypto_model_labels || {};
  const modelLabel = labels[activeModel] || activeModel;
  const rows = [];
  const signals = modelViews[activeModel]?.crypto?.signals || [];
  signals.forEach((s) => {
    const score = Number(s.score || 0);
    rows.push({
      ts: Number(s.scored_at_ts || data.server_time || 0),
      model: modelLabel,
      symbol: s.symbol || "-",
      strategy: s.strategy || "-",
      score: score,
      threshold: Number(s.entry_threshold || 0),
      lev: Number(s.leverage || 1),
      price: Number(s.price_usd || 0),
      reason: `score=${score.toFixed(4)}`,
    });
  });
  rows.sort((a, b) => Math.abs(b.score) - Math.abs(a.score));
  const topRows = rows.slice(0, 120).map((r) => `
      <tr>
        <td>${fmtTs(r.ts)}</td>
        <td>${r.model}</td>
        <td>${r.symbol}</td>
        <td>${r.strategy}</td>
        <td>${r.score.toFixed(4)}</td>
        <td>${r.threshold.toFixed(4)}</td>
        <td>${r.lev.toFixed(2)}x</td>
        <td>${fmtUsd(r.price)}</td>
        <td>${r.reason}</td>
      </tr>
  `).join("");
  renderTableBody("cryptoTrendRows", topRows, 9);

  const daily = modelViews[activeModel]?.crypto?.daily_pnl || [];
  const points = daily
    .map((r) => ({ date: String(r.date || ""), y: Number(r.total_pnl_usd || 0) }))
    .filter((r) => !!r.date)
    .sort((a, b) => a.date.localeCompare(b.date))
    .slice(-30);
  drawLineChart("cryptoTrendChart", points, "#4dd4ff", "rgba(77, 212, 255, 0.16)");
  const total = points.reduce((acc, p) => acc + p.y, 0);
  const last = points.length ? points[points.length - 1] : null;
  setText(
    "cryptoTrendStats",
    points.length
      ? `${modelLabel} | 최근 30일 누적 PNL ${fmtUsd(total)} | 최근일(${last.date}) ${fmtUsd(last.y)}`
      : `${modelLabel} 일일 PNL 차트 데이터가 아직 없습니다.`
  );
}

function renderAllModelPositions(data) {
  const modelViews = data.model_views || {};
  const memeLabels = data.meme_model_labels || {};
  const cryptoLabels = data.crypto_model_labels || {};
  const memeRows = [];
  const cryptoRows = [];
  ["A", "B", "C"].forEach((mid) => {
    const view = modelViews[mid] || {};
    const mPos = (view.meme?.positions || []);
    const cPos = (view.crypto?.positions || []);
    mPos.forEach((p) => {
      memeRows.push({
        model: `${memeLabels[mid] || mid}`,
        symbol: p.symbol || "-",
        strategy: p.strategy || "-",
        value: Number(p.value_usd || 0),
        pnl: Number(p.pnl_usd || 0),
        pnlPct: Number(p.pnl_pct || 0),
        grade: p.grade || "-",
      });
    });
    cPos.forEach((p) => {
      cryptoRows.push({
        model: `${cryptoLabels[mid] || mid}`,
        symbol: p.symbol || "-",
        lev: Number(p.leverage || 1),
        exposure: Number(p.position_value || 0),
        margin: Number(p.margin_usd || 0),
        upnl: Number(p.unrealised_pnl || 0),
        roe: Number(p.roe_pct || 0),
        reason: p.reason || "-",
      });
    });
  });
  memeRows.sort((a, b) => b.value - a.value);
  cryptoRows.sort((a, b) => b.exposure - a.exposure);
  const memeHtml = memeRows.slice(0, 60).map((r) => `
      <tr>
        <td>${r.model}</td>
        <td>${r.symbol}</td>
        <td>${r.strategy}</td>
        <td>${fmtUsd(r.value)}</td>
        <td class="${clsPn(r.pnl)}">${fmtUsd(r.pnl)} (${fmtPct(r.pnlPct)})</td>
        <td>${r.grade}</td>
      </tr>
    `).join("");
  const cryptoHtml = cryptoRows.slice(0, 80).map((r) => `
      <tr>
        <td>${r.model}</td>
        <td>${r.symbol}</td>
        <td>${r.lev.toFixed(2)}x</td>
        <td>${fmtUsd(r.exposure)} / ${fmtUsd(r.margin)}</td>
        <td class="${clsPn(r.upnl)}">${fmtUsd(r.upnl)} (${fmtPct(r.roe)})</td>
        <td class="wrap">${r.reason}</td>
      </tr>
    `).join("");
  renderTableBody("allMemePosRows", memeHtml, 6);
  renderTableBody("allCryptoPosRows", cryptoHtml, 6);
}

function renderAlerts(data) {
  const e = data.errors || {};
  const errorText = [
    e.memecoin ? `memecoin: ${e.memecoin}` : "",
    e.bybit ? `crypto: ${e.bybit}` : "",
  ].filter(Boolean).join(" | ");
  document.getElementById("errorBar").textContent = errorText || "오류 없음";

  const rows = (data.alerts || []).slice(-120).reverse().map((a) => `
      <tr>
        <td>${fmtTs(a.ts)}</td>
        <td>${a.level || "-"}</td>
        <td>${a.title || "-"}</td>
        <td class="wrap">${a.text || "-"}</td>
      </tr>
    `).join("");
  renderTableBody("alertRows", rows, 4);
}

function drawLineChart(svgId, pointsRaw, strokeColor, fillColor) {
  const svg = document.getElementById(svgId);
  if (!svg) return;
  const points = (pointsRaw || []).map((p) => ({ x: p.date || "", y: Number(p.y || 0) }));
  if (!points.length) {
    svg.innerHTML = "";
    return;
  }
  const w = 900;
  const h = 220;
  const padX = 42;
  const padY = 24;
  const ys = points.map((p) => p.y);
  const yMin = Math.min(0, ...ys);
  const yMax = Math.max(1, ...ys);
  const ySpan = Math.max(1, yMax - yMin);
  const xMax = Math.max(1, points.length - 1);
  const xScale = (i) => padX + (i / xMax) * (w - padX * 2);
  const yScale = (y) => h - padY - ((y - yMin) / ySpan) * (h - padY * 2);
  const path = points.map((p, i) => `${i ? "L" : "M"}${xScale(i).toFixed(2)},${yScale(p.y).toFixed(2)}`).join(" ");
  const area = `${path} L ${xScale(points.length - 1).toFixed(2)},${h - padY} L ${xScale(0).toFixed(2)},${h - padY} Z`;
  svg.innerHTML = `
    <path d="${area}" fill="${fillColor}" />
    <path d="${path}" fill="none" stroke="${strokeColor}" stroke-width="2.2" />
  `;
}

function drawSinglePnlChart(rows) {
  const svg = document.getElementById("detailPnlChart");
  if (!svg) return;
  const points = (rows || []).map((r) => ({ x: String(r.date || ""), y: Number(r.total_pnl_usd || 0) }));
  if (!points.length) {
    svg.innerHTML = "";
    return;
  }
  const w = 900;
  const h = 220;
  const padX = 42;
  const padY = 24;
  const ys = points.map((p) => p.y);
  const yMin = Math.min(...ys, 0);
  const yMax = Math.max(...ys, 0);
  const ySpan = Math.max(1, yMax - yMin);
  const xMax = Math.max(1, points.length - 1);
  const xScale = (i) => padX + (i / xMax) * (w - padX * 2);
  const yScale = (y) => h - padY - ((y - yMin) / ySpan) * (h - padY * 2);
  const path = points.map((p, i) => `${i ? "L" : "M"}${xScale(i).toFixed(2)},${yScale(p.y).toFixed(2)}`).join(" ");
  const zeroY = yScale(0);
  svg.innerHTML = `
    <line x1="${padX}" y1="${zeroY}" x2="${w - padX}" y2="${zeroY}" stroke="rgba(173,210,255,0.35)" stroke-width="1" />
    <path d="${path}" fill="none" stroke="#73b7ff" stroke-width="2.2" />
  `;
}

function renderDetailPane(data) {
  const modelViews = data.model_views || {};
  const modelData = modelViews[VIEW.model] || {};
  const detail = modelData[VIEW.market] || {};
  const summary = detail.summary || {};
  const signals = detail.signals || [];
  const positions = detail.positions || [];
  const trades = detail.trades || [];
  const daily = detail.daily_pnl || [];

  const methods = data.model_methods || {};
  const note = methods[VIEW.model] || {};
  const modelName = detail.model_name || marketModelName(data || {}, VIEW.market, VIEW.model);
  document.getElementById("methodTitle").textContent = `${modelName}`;
  document.getElementById("methodText").textContent = note[VIEW.market] || "-";
  const strengthKey = VIEW.market === "meme" ? "strengths_meme" : "strengths_crypto";
  document.getElementById("methodStrengthText").textContent = note[strengthKey] ? `Strength: ${note[strengthKey]}` : "";
  const tuneMap = data.model_autotune || {};
  const tune = tuneMap[VIEW.model] || {};
  const nextMin = Math.max(0, Math.floor(Number(tune.next_eval_ts || 0) - Number(data.server_time || 0)) / 60);
  const tuneLine = note.autotune || "";
  const tuneNote = String(tune.last_eval_note_ko || tune.last_eval_note || "").trim();
  const tuneDetail = tune.threshold !== undefined
    ? ` | next=${nextMin}m thr=${Number(tune.threshold || 0).toFixed(4)} tp_mul=${Number(tune.tp_mul || 0).toFixed(2)} sl_mul=${Number(tune.sl_mul || 0).toFixed(2)}`
    : "";
  const tuneEvalText = tuneNote ? ` | 최근평가=${tuneNote}` : "";
  document.getElementById("methodTuneText").textContent = tuneLine ? `Tune: ${tuneLine}${tuneDetail}${tuneEvalText}` : "";

  const profiles = data.model_profiles || {};
  const profile = profiles[VIEW.model] || {};
  if (VIEW.market === "meme") {
    const p = profile.meme || {};
    document.getElementById("methodParamText").textContent = [
      `mode=${p.strategy_mode || "-"}`,
      `entry_floor=${Number(p.threshold_floor || 0).toFixed(4)}`,
      `paper_min_grade=${p.paper_min_grade || "-"}`,
      `demo_floor=${Number(p.demo_score_floor || 0).toFixed(3)}`,
      `swing_hold_days=${Number(p.swing_hold_days || 0)}`,
    ].join(" | ");
  } else {
    const p = profile.crypto || {};
    const levRange = Array.isArray(p.leverage_range)
      ? `${Number(p.leverage_range[0] || 0).toFixed(2)}~${Number(p.leverage_range[1] || 0).toFixed(2)}x`
      : "-";
    const runtime = p.runtime_defaults || {};
    document.getElementById("methodParamText").textContent = [
      `rank_max=${p.rank_max ?? "-"}`,
      `trend_stack_min=${Number(p.trend_stack_min || 0).toFixed(2)}`,
      `overheat_max=${Number(p.overheat_max || 0).toFixed(2)}`,
      `smallcap_trend_only=${p.smallcap_trend_only ? "Y" : "N"}`,
      `lev=${levRange}`,
      `order_mul=${Number(p.order_pct_mul || 0).toFixed(2)}`,
      `hard_roe=${Number(p.hard_roe_cut || 0).toFixed(2)}`,
      `base(thr/tp/sl)=${Number(runtime.threshold || 0).toFixed(4)}/${Number(runtime.tp_mul || 0).toFixed(2)}/${Number(runtime.sl_mul || 0).toFixed(2)}`,
    ].join(" | ");
  }

  document.getElementById("dEquity").textContent = fmtUsd(summary.equity_usd);
  document.getElementById("dTotalPnl").textContent = fmtUsd(summary.total_pnl_usd);
  document.getElementById("dTotalPnl").className = clsPn(summary.total_pnl_usd);
  document.getElementById("dRealized").textContent = fmtUsd(summary.realized_pnl_usd);
  document.getElementById("dRealized").className = clsPn(summary.realized_pnl_usd);
  document.getElementById("dUnrealized").textContent = fmtUsd(summary.unrealized_pnl_usd);
  document.getElementById("dUnrealized").className = clsPn(summary.unrealized_pnl_usd);
  document.getElementById("dWinrate").textContent = `${Number(summary.win_rate || 0).toFixed(1)}%`;
  document.getElementById("dOpen").textContent = String(Number(summary.open_positions || 0));

  if (VIEW.market === "meme") {
    document.getElementById("signalTitle").textContent = `${modelName} | meme signals`;
    document.getElementById("positionTitle").textContent = `${modelName} | meme positions`;
    document.getElementById("tradeTitle").textContent = `${modelName} | meme trades`;
    document.getElementById("pnlTitle").textContent = `${modelName} | meme daily PNL`;
    document.getElementById("detailSignalHead").innerHTML = "<tr><th>Symbol</th><th>Grade</th><th>Score</th><th>Prob</th><th>Price</th><th>Reason</th></tr>";
    document.getElementById("detailPositionHead").innerHTML = "<tr><th>Symbol</th><th>Value</th><th>PNL</th><th>Strategy</th><th>TP/SL</th><th>Reason</th></tr>";
    const sRows = signals.slice(0, 60).map((s) => `
        <tr>
          <td>${s.symbol || "-"}</td>
          <td>${s.grade || "-"}</td>
          <td>${Number(s.score || 0).toFixed(3)}</td>
          <td>${Number(s.probability || 0).toFixed(3)}</td>
          <td>${fmtUsdPrice(s.price_usd)}</td>
          <td class="wrap">${s.reason || "-"}</td>
        </tr>
      `).join("");
    renderTableBody("detailSignalRows", sRows, 6);
    const pRows = positions.slice(0, 60).map((p) => `
        <tr>
          <td>${p.symbol || "-"} ${p.grade ? `(${p.grade})` : ""}</td>
          <td>${fmtUsd(p.value_usd)}</td>
          <td class="${clsPn(p.pnl_usd)}">${fmtUsd(p.pnl_usd)} (${fmtPct(p.pnl_pct)})</td>
          <td>${p.strategy || "-"}</td>
          <td>${(Number(p.tp_pct || 0) * 100).toFixed(1)}% / ${(Number(p.sl_pct || 0) * 100).toFixed(1)}%</td>
          <td class="wrap">${p.reason || "-"}</td>
        </tr>
      `).join("");
    renderTableBody("detailPositionRows", pRows, 6);
  } else {
    document.getElementById("signalTitle").textContent = `${modelName} | crypto signals`;
    document.getElementById("positionTitle").textContent = `${modelName} | crypto positions`;
    document.getElementById("tradeTitle").textContent = `${modelName} | crypto trades`;
    document.getElementById("pnlTitle").textContent = `${modelName} | crypto daily PNL`;
    document.getElementById("detailSignalHead").innerHTML = "<tr><th>시각</th><th>Symbol</th><th>Strategy</th><th>Score</th><th>Threshold</th><th>Lev</th><th>Price</th><th>Status</th><th>Reason</th></tr>";
    document.getElementById("detailPositionHead").innerHTML = "<tr><th>Symbol</th><th>Side</th><th>Lev</th><th>Exposure/Margin</th><th>UPNL(ROE)</th><th>TP/SL</th><th>Reason</th></tr>";
    const sRows = signals.slice(0, 80).map((s) => {
      const ts = Number(s.scored_at_ts || data.server_time || 0);
      const score = Number(s.score || 0);
      const threshold = Number(s.entry_threshold || 0);
      const status = s.in_position ? "in-position" : (s.above_threshold ? "entry-candidate" : "watch");
      return `
        <tr>
          <td>${fmtTs(ts)}</td>
          <td>${s.symbol || "-"}</td>
          <td>${s.strategy || "-"}</td>
          <td>${score.toFixed(4)}</td>
          <td>${threshold.toFixed(4)}</td>
          <td>${Number(s.leverage || 1).toFixed(2)}x</td>
          <td>${fmtUsdPrice(s.price_usd)}</td>
          <td>${status}</td>
          <td>score=${score.toFixed(4)}</td>
        </tr>
      `;
    }).join("");
    renderTableBody("detailSignalRows", sRows, 9);
    const pRows = positions.slice(0, 80).map((p) => `
        <tr>
          <td>${p.symbol || "-"}</td>
          <td>${p.side || "-"}</td>
          <td>${Number(p.leverage || 1).toFixed(2)}x</td>
          <td>${fmtUsd(p.position_value)} / ${fmtUsd(p.margin_usd)}</td>
          <td class="${clsPn(p.unrealised_pnl)}">${fmtUsd(p.unrealised_pnl)} (${fmtPct(p.roe_pct)})</td>
          <td>${(Number(p.tp_pct || 0) * 100).toFixed(1)}% / ${(Number(p.sl_pct || 0) * 100).toFixed(1)}%</td>
          <td class="wrap">${p.reason || "-"}</td>
        </tr>
      `).join("");
    renderTableBody("detailPositionRows", pRows, 7);
  }

  const tRows = trades.slice(-200).reverse().map((t) => `
      <tr>
        <td>${fmtTs(t.ts)}</td>
        <td>${t.source || "-"}</td>
        <td>${t.side || "-"}</td>
        <td>${t.symbol || "-"}</td>
        <td>${fmtUsd(t.notional_usd)}</td>
        <td class="${clsPn(t.pnl_usd)}">${fmtUsd(t.pnl_usd)}</td>
        <td class="wrap">${t.reason || "-"}</td>
      </tr>
    `).join("");
  renderTableBody("detailTradeRows", tRows, 7);

  const dRows = daily.slice(-120).reverse().map((r) => `
      <tr>
        <td>${r.date || "-"}</td>
        <td>${marketModelName(VIEW.data || {}, VIEW.market, r.model_id || VIEW.model)}</td>
        <td>${fmtUsd(r.equity_usd)}</td>
        <td class="${clsPn(r.total_pnl_usd)}">${fmtUsd(r.total_pnl_usd)}</td>
        <td class="${clsPn(r.realized_pnl_usd)}">${fmtUsd(r.realized_pnl_usd)}</td>
        <td>${Number(r.win_rate || 0).toFixed(1)}%</td>
      </tr>
    `).join("");
  renderTableBody("detailDailyRows", dRows, 6);
  drawSinglePnlChart(daily);
}

let busy = false;
async function refreshDashboard() {
  if (busy) return;
  busy = true;
  try {
    const res = await fetch("/api/dashboard");
    if (!res.ok) throw new Error("dashboard fetch failed");
    const data = await res.json();
    VIEW.data = data;
    renderOverallMetrics(data);
    renderCycleStatus(data);
    renderModelRanking(data.meme_model_rankings || data.meme_model_runs || [], "memeModelRows");
    renderModelRanking(data.crypto_model_rankings || data.crypto_model_runs || [], "cryptoModelRows");
    renderDemoModelBoard(data);
    renderTrend(data);
    renderTrendSources(data);
    renderTrendDatabase(data);
    renderTrendInsights(data);
    renderTrendBriefLogs(data);
    renderTrendDistribution(data);
    renderTuneHistory(data);
    drawTrendChart14d(data);
    renderCryptoTrend(data);
    renderMemeGrades(data);
    renderNewMemeFeed(data);
    renderWallet(data);
    renderBybitAssets(data);
    renderLiveSection(data);
    renderSettings(data);
    renderLiveMarketToggles(data);
    renderLiveModelSelectors(data);
    renderAllModelPositions(data);
    renderAlerts(data);
    updateModelTabLabels(data);
    updateCryptoModelTabLabels(data);
    renderDetailPane(data);
    setTabState();
    setCryptoTabState();
    setWorkspaceState();
  } catch (err) {
    document.getElementById("errorBar").textContent = String(err);
  } finally {
    busy = false;
  }
}

bindControls();
bindSecretControls();
bindLiveModelControls();
bindLiveMarketControls();
bindLivePerformanceControls();
bindTabs();
bindDetailSelectControls();
bindCryptoTrendTabs();
bindLiveMarketTabs();
bindWorkspaceTabs();
initNavState();
setTabState();
setCryptoTabState();
setLiveMarketTabState();
setWorkspaceState();
refreshDashboard();
loadSecretSettings(false);
setInterval(refreshDashboard, REFRESH_MS);

