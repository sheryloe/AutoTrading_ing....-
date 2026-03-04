const REFRESH_MS = Math.max(2000, (window.APP_CONFIG?.refreshSeconds || 4) * 1000);

const VIEW = {
  market: "meme",
  model: "A",
  data: null,
};

function fmtUsd(value) {
  const num = Number(value || 0);
  return `$${num.toLocaleString("en-US", { maximumFractionDigits: 2 })}`;
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

function marketModelName(data, market, modelId) {
  const marketKey = market === "meme" ? "meme_model_labels" : "crypto_model_labels";
  const table = (data && data[marketKey]) || {};
  return table[modelId] || modelId;
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

function bindControls() {
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

function bindTabs() {
  document.querySelectorAll("[data-market]").forEach((btn) => {
    btn.addEventListener("click", () => {
      VIEW.market = btn.dataset.market || "meme";
      updateModelTabLabels(VIEW.data || {});
      renderDetailPane(VIEW.data || {});
      setTabState();
    });
  });
  document.querySelectorAll("[data-model]").forEach((btn) => {
    btn.addEventListener("click", () => {
      VIEW.model = btn.dataset.model || "A";
      updateModelTabLabels(VIEW.data || {});
      renderDetailPane(VIEW.data || {});
      setTabState();
    });
  });
}

function setTabState() {
  document.querySelectorAll("[data-market]").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.market === VIEW.market);
  });
  document.querySelectorAll("[data-model]").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.model === VIEW.model);
  });
}

function updateModelTabLabels(data) {
  document.querySelectorAll("[data-model]").forEach((btn) => {
    const id = btn.dataset.model || "A";
    const name = marketModelName(data || {}, VIEW.market, id);
    btn.textContent = `${name}`;
  });
}

function renderOverallMetrics(data) {
  const m = data.metrics || {};
  const settings = data.settings || {};
  document.getElementById("mRunning").textContent = `${data.running ? "RUNNING" : "STOPPED"} | 자동매매: ${settings.enable_autotrade ? "ON" : "OFF"} | 초기화잠금: ${settings.allow_demo_reset ? "해제" : "설정"}`;
  document.getElementById("mMode").textContent = String(settings.trade_mode || "-").toUpperCase();
  document.getElementById("mSeed").textContent = `${Number(data.demo_seed_usdt || 1000).toFixed(0)} USDT`;
  document.getElementById("mTotalEquity").textContent = fmtUsd(m.total_equity_usd);
  document.getElementById("mTotalPnl").textContent = fmtUsd(m.total_pnl_usd);
  document.getElementById("mTotalPnl").className = clsPn(m.total_pnl_usd);
  document.getElementById("mWinrate").textContent = `${Number(m.win_rate || 0).toFixed(1)}%`;

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
}

function renderModelCompare(rows, targetId) {
  const html = (rows || []).map((m) => `
      <tr>
        <td>${m.model_name || m.model_id}</td>
        <td>${fmtUsd(m.equity_usd)}</td>
        <td class="${clsPn(m.total_pnl_usd)}">${fmtUsd(m.total_pnl_usd)}</td>
        <td class="${clsPn(m.realized_pnl_usd)}">${fmtUsd(m.realized_pnl_usd)}</td>
        <td>${Number(m.win_rate || 0).toFixed(1)}%</td>
        <td>${Number(m.open_positions || 0)}</td>
      </tr>
    `).join("");
  renderTableBody(targetId, html, 6);
}

function renderTrend(data) {
  const rows = (data.trend_top || []).slice(0, 30).map((row) => `<tr><td>${row.symbol}</td><td>${row.hits}</td></tr>`).join("");
  renderTableBody("trendTop", rows, 2);
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
        <td>${row.error || "-"}</td>
      </tr>
    `;
  }).join("");
  renderTableBody("trendSourceRows", rows, 5);
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
        <td>${Number(r.age_minutes || 0).toFixed(1)}</td>
        <td>${fmtUsd(r.volume_5m_usd)}</td>
        <td>${fmtUsd(r.liquidity_usd)}</td>
        <td>${Number(r.trend_hits || 0)}</td>
        <td>${r.is_pump_fun ? "Y" : "-"}</td>
      </tr>
    `).join("");
  renderTableBody("newMemeRows", rows, 6);
}

function renderWallet(data) {
  const rows = (data.wallet_assets || []).slice(0, 40).map((a) => `
      <tr>
        <td>${a.symbol || "-"}</td>
        <td>${Number(a.qty || 0).toLocaleString("en-US", { maximumFractionDigits: 6 })}</td>
        <td>${fmtUsd(a.value_usd)}</td>
      </tr>
    `).join("");
  renderTableBody("walletRows", rows, 3);
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
        <td>${r.reason}</td>
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
        <td>${a.text || "-"}</td>
      </tr>
    `).join("");
  renderTableBody("alertRows", rows, 4);
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
  const tuneDetail = tune.threshold !== undefined
    ? ` | next=${nextMin}m thr=${Number(tune.threshold || 0).toFixed(4)} tp_mul=${Number(tune.tp_mul || 0).toFixed(2)} sl_mul=${Number(tune.sl_mul || 0).toFixed(2)}`
    : "";
  document.getElementById("methodTuneText").textContent = tuneLine ? `Tune: ${tuneLine}${tuneDetail}` : "";

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
          <td>${fmtUsd(s.price_usd)}</td>
          <td>${s.reason || "-"}</td>
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
          <td>${p.reason || "-"}</td>
        </tr>
      `).join("");
    renderTableBody("detailPositionRows", pRows, 6);
  } else {
    document.getElementById("signalTitle").textContent = `${modelName} | crypto signals`;
    document.getElementById("positionTitle").textContent = `${modelName} | crypto positions`;
    document.getElementById("tradeTitle").textContent = `${modelName} | crypto trades`;
    document.getElementById("pnlTitle").textContent = `${modelName} | crypto daily PNL`;
    document.getElementById("detailSignalHead").innerHTML = "<tr><th>Symbol</th><th>Strategy</th><th>Score</th><th>Threshold</th><th>Lev</th><th>Price</th><th>Status</th><th>Reason</th></tr>";
    document.getElementById("detailPositionHead").innerHTML = "<tr><th>Symbol</th><th>Side</th><th>Lev</th><th>Exposure/Margin</th><th>UPNL(ROE)</th><th>TP/SL</th><th>Reason</th></tr>";
    const sRows = signals.slice(0, 80).map((s) => {
      const score = Number(s.score || 0);
      const threshold = Number(s.entry_threshold || 0);
      const status = s.in_position ? "in-position" : (s.above_threshold ? "entry-candidate" : "watch");
      return `
        <tr>
          <td>${s.symbol || "-"}</td>
          <td>${s.strategy || "-"}</td>
          <td>${score.toFixed(4)}</td>
          <td>${threshold.toFixed(4)}</td>
          <td>${Number(s.leverage || 1).toFixed(2)}x</td>
          <td>${fmtUsd(s.price_usd)}</td>
          <td>${status}</td>
          <td>${s.reason || "-"}</td>
        </tr>
      `;
    }).join("");
    renderTableBody("detailSignalRows", sRows, 8);
    const pRows = positions.slice(0, 80).map((p) => `
        <tr>
          <td>${p.symbol || "-"}</td>
          <td>${p.side || "-"}</td>
          <td>${Number(p.leverage || 1).toFixed(2)}x</td>
          <td>${fmtUsd(p.position_value)} / ${fmtUsd(p.margin_usd)}</td>
          <td class="${clsPn(p.unrealised_pnl)}">${fmtUsd(p.unrealised_pnl)} (${fmtPct(p.roe_pct)})</td>
          <td>${(Number(p.tp_pct || 0) * 100).toFixed(1)}% / ${(Number(p.sl_pct || 0) * 100).toFixed(1)}%</td>
          <td>${p.reason || "-"}</td>
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
        <td>${t.reason || "-"}</td>
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
    renderModelCompare(data.meme_model_runs || [], "memeModelRows");
    renderModelCompare(data.crypto_model_runs || [], "cryptoModelRows");
    renderTrend(data);
    renderTrendSources(data);
    renderMemeGrades(data);
    renderNewMemeFeed(data);
    renderWallet(data);
    renderBybitAssets(data);
    renderAllModelPositions(data);
    renderAlerts(data);
    updateModelTabLabels(data);
    renderDetailPane(data);
    setTabState();
  } catch (err) {
    document.getElementById("errorBar").textContent = String(err);
  } finally {
    busy = false;
  }
}

bindControls();
bindTabs();
setTabState();
refreshDashboard();
setInterval(refreshDashboard, REFRESH_MS);

