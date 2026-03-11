from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any

import requests


class OpenAICandidateAdvisor:
    CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"
    MODEL_PRICING_USD_PER_MTOKEN: dict[str, dict[str, float]] = {
        "gpt-5-mini": {"input": 0.25, "output": 2.00, "cached_input": 0.025},
    }

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        enabled: bool,
        monthly_budget_usd: float,
        daily_budget_usd: float,
        candidate_review_interval_seconds: int,
        candidate_top_n: int,
        candidate_min_score: float,
        narrative_max_calls_per_day: int,
        input_token_estimate: int,
        output_token_estimate: int,
        state_path: str,
        timeout_seconds: int = 20,
    ) -> None:
        self.api_key = str(api_key or "").strip()
        self.model = str(model or "gpt-5-mini").strip()
        self.enabled = bool(enabled and self.api_key)
        self.monthly_budget_usd = float(max(1.0, monthly_budget_usd))
        self.daily_budget_usd = float(max(0.1, daily_budget_usd))
        self.candidate_review_interval_seconds = int(max(300, candidate_review_interval_seconds))
        self.candidate_top_n = int(max(3, candidate_top_n))
        self.candidate_min_score = float(max(0.0, min(1.0, candidate_min_score)))
        self.narrative_max_calls_per_day = int(max(1, narrative_max_calls_per_day))
        self.input_token_estimate = int(max(256, input_token_estimate))
        self.output_token_estimate = int(max(64, output_token_estimate))
        self.state_path = Path(state_path)
        self.timeout_seconds = int(max(5, timeout_seconds))
        self.session = requests.Session()

    @staticmethod
    def _month_key(now_ts: int) -> str:
        return time.strftime("%Y-%m", time.localtime(int(now_ts)))

    @staticmethod
    def _day_key(now_ts: int) -> str:
        return time.strftime("%Y-%m-%d", time.localtime(int(now_ts)))

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return max(1, int(math.ceil(len(str(text or "")) / 4.0)))

    def _pricing(self) -> dict[str, float]:
        return dict(self.MODEL_PRICING_USD_PER_MTOKEN.get(self.model, self.MODEL_PRICING_USD_PER_MTOKEN["gpt-5-mini"]))

    def estimate_cost_usd(self, input_tokens: int, output_tokens: int) -> float:
        pricing = self._pricing()
        return (
            (float(max(0, input_tokens)) / 1_000_000.0) * float(pricing["input"])
            + (float(max(0, output_tokens)) / 1_000_000.0) * float(pricing["output"])
        )

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {
                "month_key": "",
                "month_spent_usd": 0.0,
                "daily": {},
                "last_candidate_review_ts": 0,
                "daily_narrative_calls": {},
            }
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                raw.setdefault("daily", {})
                raw.setdefault("daily_narrative_calls", {})
                return raw
        except Exception:
            pass
        return {
            "month_key": "",
            "month_spent_usd": 0.0,
            "daily": {},
            "last_candidate_review_ts": 0,
            "daily_narrative_calls": {},
        }

    def _save_state(self, payload: dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _normalize_state(self, now_ts: int) -> dict[str, Any]:
        state = self._load_state()
        month_key = self._month_key(now_ts)
        day_key = self._day_key(now_ts)
        if str(state.get("month_key") or "") != month_key:
            state["month_key"] = month_key
            state["month_spent_usd"] = 0.0
        daily = dict(state.get("daily") or {})
        state["daily"] = {day_key: float(daily.get(day_key) or 0.0)}
        daily_narrative = dict(state.get("daily_narrative_calls") or {})
        state["daily_narrative_calls"] = {day_key: int(daily_narrative.get(day_key) or 0)}
        return state

    def budget_snapshot(self, now_ts: int | None = None) -> dict[str, Any]:
        now = int(now_ts or time.time())
        state = self._normalize_state(now)
        day_key = self._day_key(now)
        month_spent = float(state.get("month_spent_usd") or 0.0)
        daily_spent = float((state.get("daily") or {}).get(day_key) or 0.0)
        narrative_calls = int((state.get("daily_narrative_calls") or {}).get(day_key) or 0)
        est_review_cost = self.estimate_cost_usd(self.input_token_estimate, self.output_token_estimate)
        monthly_remaining = max(0.0, self.monthly_budget_usd - month_spent)
        daily_remaining = max(0.0, self.daily_budget_usd - daily_spent)
        max_candidate_reviews_today = int(daily_remaining // max(est_review_cost, 1e-9))
        return {
            "enabled": bool(self.enabled),
            "model": self.model,
            "monthly_budget_usd": float(self.monthly_budget_usd),
            "daily_budget_usd": float(self.daily_budget_usd),
            "month_spent_usd": float(month_spent),
            "day_spent_usd": float(daily_spent),
            "month_remaining_usd": float(monthly_remaining),
            "day_remaining_usd": float(daily_remaining),
            "estimated_review_cost_usd": float(est_review_cost),
            "candidate_interval_seconds": int(self.candidate_review_interval_seconds),
            "candidate_top_n": int(self.candidate_top_n),
            "candidate_min_score": float(self.candidate_min_score),
            "narrative_max_calls_per_day": int(self.narrative_max_calls_per_day),
            "narrative_calls_today": int(narrative_calls),
            "last_candidate_review_ts": int(state.get("last_candidate_review_ts") or 0),
            "max_candidate_reviews_today_est": int(max_candidate_reviews_today),
        }

    def should_run_candidate_review(self, now_ts: int | None = None) -> tuple[bool, str]:
        now = int(now_ts or time.time())
        state = self._normalize_state(now)
        if not self.enabled:
            return False, "disabled"
        last_ts = int(state.get("last_candidate_review_ts") or 0)
        if last_ts > 0 and (now - last_ts) < int(self.candidate_review_interval_seconds):
            return False, "cooldown"
        snap = self.budget_snapshot(now)
        if float(snap["day_remaining_usd"]) < float(snap["estimated_review_cost_usd"]):
            return False, "daily_budget_exhausted"
        if float(snap["month_remaining_usd"]) < float(snap["estimated_review_cost_usd"]):
            return False, "monthly_budget_exhausted"
        return True, "ok"

    def should_run_narrative_review(self, now_ts: int | None = None) -> tuple[bool, str]:
        now = int(now_ts or time.time())
        state = self._normalize_state(now)
        if not self.enabled:
            return False, "disabled"
        day_key = self._day_key(now)
        calls_today = int((state.get("daily_narrative_calls") or {}).get(day_key) or 0)
        if calls_today >= int(self.narrative_max_calls_per_day):
            return False, "daily_narrative_cap"
        snap = self.budget_snapshot(now)
        if float(snap["day_remaining_usd"]) < float(snap["estimated_review_cost_usd"]):
            return False, "daily_budget_exhausted"
        if float(snap["month_remaining_usd"]) < float(snap["estimated_review_cost_usd"]):
            return False, "monthly_budget_exhausted"
        return True, "ok"

    def _record_usage(
        self,
        *,
        now_ts: int,
        cost_usd: float,
        candidate_review: bool = False,
        narrative_review: bool = False,
    ) -> dict[str, Any]:
        state = self._normalize_state(now_ts)
        day_key = self._day_key(now_ts)
        state["month_spent_usd"] = float(state.get("month_spent_usd") or 0.0) + float(max(0.0, cost_usd))
        daily = dict(state.get("daily") or {})
        daily[day_key] = float(daily.get(day_key) or 0.0) + float(max(0.0, cost_usd))
        state["daily"] = daily
        if candidate_review:
            state["last_candidate_review_ts"] = int(now_ts)
        if narrative_review:
            calls = dict(state.get("daily_narrative_calls") or {})
            calls[day_key] = int(calls.get(day_key) or 0) + 1
            state["daily_narrative_calls"] = calls
        self._save_state(state)
        return state

    def build_meme_candidate_payload(self, candidates: list[dict[str, Any]], purpose: str) -> dict[str, Any]:
        trimmed: list[dict[str, Any]] = []
        for row in list(candidates or [])[: int(self.candidate_top_n)]:
            trimmed.append(
                {
                    "symbol": str(row.get("symbol") or "-"),
                    "token_address": str(row.get("token_address") or ""),
                    "strategy_id": str(row.get("strategy_id") or ""),
                    "score": round(float(row.get("score") or 0.0), 4),
                    "grade": str(row.get("grade") or "-"),
                    "probability": round(float(row.get("probability") or 0.0), 4),
                    "market_cap_usd": round(float(row.get("market_cap_usd") or 0.0), 2),
                    "liquidity_usd": round(float(row.get("liquidity_usd") or 0.0), 2),
                    "volume_5m_usd": round(float(row.get("volume_5m_usd") or 0.0), 2),
                    "buy_sell_ratio": round(float(row.get("buy_sell_ratio") or 0.0), 4),
                    "sniper_social_burst": round(float(row.get("sniper_social_burst") or 0.0), 4),
                    "sniper_signal_fit": round(float(row.get("sniper_signal_fit") or 0.0), 4),
                    "theme_confirmation": round(float(row.get("theme_confirmation") or 0.0), 4),
                    "holder_overlap_risk": round(float(row.get("holder_overlap_risk") or 0.0), 4),
                    "reason": str(row.get("reason") or "")[:240],
                    "low_reason": str(row.get("score_low_reason") or "")[:180],
                }
            )
        return {
            "purpose": str(purpose or "candidate_review"),
            "policy": {
                "budget_monthly_usd": float(self.monthly_budget_usd),
                "budget_daily_usd": float(self.daily_budget_usd),
                "top_n": int(self.candidate_top_n),
                "min_score": float(self.candidate_min_score),
            },
            "candidates": trimmed,
        }

    def review_json(
        self,
        *,
        purpose: str,
        system_prompt: str,
        payload: dict[str, Any],
        candidate_review: bool = False,
        narrative_review: bool = False,
        max_output_tokens: int | None = None,
    ) -> dict[str, Any]:
        now = int(time.time())
        if candidate_review:
            ok, reason = self.should_run_candidate_review(now)
        elif narrative_review:
            ok, reason = self.should_run_narrative_review(now)
        else:
            ok, reason = (bool(self.enabled), "ok" if self.enabled else "disabled")
        if not ok:
            return {"ok": False, "skipped": True, "reason": reason, "budget": self.budget_snapshot(now)}
        user_text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        input_tokens_est = self._estimate_tokens(system_prompt) + self._estimate_tokens(user_text)
        output_tokens_est = int(max_output_tokens or self.output_token_estimate)
        estimated_cost = self.estimate_cost_usd(input_tokens_est, output_tokens_est)
        budget = self.budget_snapshot(now)
        if estimated_cost > float(budget["day_remaining_usd"]) or estimated_cost > float(budget["month_remaining_usd"]):
            return {"ok": False, "skipped": True, "reason": "budget_guard", "budget": budget}
        body = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "max_completion_tokens": int(output_tokens_est),
            "messages": [
                {"role": "system", "content": str(system_prompt or "").strip()},
                {"role": "user", "content": user_text},
            ],
        }
        res = self.session.post(
            self.CHAT_COMPLETIONS_URL,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=self.timeout_seconds,
        )
        res.raise_for_status()
        data = res.json()
        usage = dict(data.get("usage") or {})
        prompt_tokens = int(usage.get("prompt_tokens") or input_tokens_est)
        completion_tokens = int(usage.get("completion_tokens") or output_tokens_est)
        actual_cost = self.estimate_cost_usd(prompt_tokens, completion_tokens)
        self._record_usage(
            now_ts=now,
            cost_usd=actual_cost,
            candidate_review=bool(candidate_review),
            narrative_review=bool(narrative_review),
        )
        choices = list(data.get("choices") or [])
        content = ""
        if choices:
            msg = dict((choices[0] or {}).get("message") or {})
            content = str(msg.get("content") or "")
        parsed: dict[str, Any] | list[Any] | None = None
        if content:
            try:
                parsed = json.loads(content)
            except Exception:
                parsed = None
        return {
            "ok": True,
            "purpose": purpose,
            "model": self.model,
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "estimated_cost_usd": actual_cost,
            },
            "content": content,
            "parsed": parsed,
            "budget": self.budget_snapshot(now),
        }

    def dashboard_payload(self, now_ts: int | None = None) -> dict[str, Any]:
        snap = self.budget_snapshot(now_ts)
        return {
            "enabled": bool(self.enabled),
            "model": self.model,
            "review_enabled": bool(self.enabled),
            "budget": snap,
            "policy": {
                "candidate_review_interval_seconds": int(self.candidate_review_interval_seconds),
                "candidate_top_n": int(self.candidate_top_n),
                "candidate_min_score": float(self.candidate_min_score),
                "narrative_max_calls_per_day": int(self.narrative_max_calls_per_day),
                "input_token_estimate": int(self.input_token_estimate),
                "output_token_estimate": int(self.output_token_estimate),
            },
        }
