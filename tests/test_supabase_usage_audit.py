from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.supabase_usage_audit import _build_held_quote_sync_diagnosis


def test_held_quote_sync_diagnosis_prefers_fixed_universe_mismatch() -> None:
    meta_json = {
        "held_quote_sync_attempted": True,
        "held_quote_sync_requested": ["ENAUSDT", "SOLUSDT"],
        "configured_symbols": ["BTCUSDT", "SOLUSDT"],
        "crypto_universe_mode": "fixed_symbols",
        "held_quote_provider_summary": {},
        "held_quote_sync_stage_status": {
            "requested_outside_configured": ["ENAUSDT"],
            "realtime_missing": ["ENAUSDT", "SOLUSDT"],
            "coingecko_missing": ["ENAUSDT", "SOLUSDT"],
            "candle_fallback_missing": ["ENAUSDT", "SOLUSDT"],
        },
    }

    result = _build_held_quote_sync_diagnosis(meta_json, ["ENAUSDT", "SOLUSDT"])

    assert result["inferred_cause"] == "missing_symbols_outside_fixed_universe"
    assert result["requested_outside_configured"] == ["ENAUSDT"]
    assert result["stage_status"]["candle_fallback_missing"] == ["ENAUSDT", "SOLUSDT"]


def test_held_quote_sync_diagnosis_marks_provider_chain_exhausted() -> None:
    meta_json = {
        "held_quote_sync_attempted": True,
        "held_quote_sync_requested": ["SOLUSDT"],
        "configured_symbols": ["SOLUSDT"],
        "crypto_universe_mode": "fixed_symbols",
        "held_quote_provider_summary": {},
        "held_quote_sync_stage_status": {
            "requested_outside_configured": [],
            "realtime_missing": ["SOLUSDT"],
            "coingecko_missing": ["SOLUSDT"],
            "candle_fallback_missing": ["SOLUSDT"],
            "coingecko_enabled": True,
        },
    }

    result = _build_held_quote_sync_diagnosis(meta_json, ["SOLUSDT"])

    assert result["inferred_cause"] == "provider_chain_exhausted"
    assert result["missing_absent_in_provider"] == ["SOLUSDT"]


def test_held_quote_sync_diagnosis_tracks_symbol_level_causes_and_streaks() -> None:
    meta_json = {
        "held_quote_sync_attempted": True,
        "held_quote_sync_requested": ["ENAUSDT", "SOLUSDT"],
        "configured_symbols": ["BTCUSDT", "SOLUSDT"],
        "crypto_universe_mode": "fixed_symbols",
        "held_quote_provider_summary": {},
        "held_quote_sync_missing_streaks": {"ENAUSDT": 3, "SOLUSDT": 5},
        "held_quote_sync_stage_status": {
            "requested_outside_configured": ["ENAUSDT"],
            "realtime_missing": ["ENAUSDT", "SOLUSDT"],
            "coingecko_missing": ["ENAUSDT", "SOLUSDT"],
            "candle_fallback_missing": ["ENAUSDT", "SOLUSDT"],
            "coingecko_enabled": False,
        },
    }

    result = _build_held_quote_sync_diagnosis(meta_json, ["ENAUSDT", "SOLUSDT"])

    assert result["provider_summary_empty"] is True
    assert result["requested_inside_configured"] == ["SOLUSDT"]
    assert result["missing_streaks"] == {"ENAUSDT": 3, "SOLUSDT": 5}
    assert result["max_missing_streak"] == 5
    assert result["symbol_causes"] == {
        "ENAUSDT": "fixed_universe_mismatch",
        "SOLUSDT": "provider_chain_exhausted",
    }


def test_held_quote_sync_diagnosis_falls_back_to_requested_vs_configured_diff() -> None:
    meta_json = {
        "held_quote_sync_attempted": True,
        "held_quote_sync_requested": ["ENAUSDT", "SOLUSDT"],
        "configured_symbols": ["BTCUSDT", "SOLUSDT"],
        "crypto_universe_mode": "fixed_symbols",
        "held_quote_provider_summary": {},
    }

    result = _build_held_quote_sync_diagnosis(meta_json, ["ENAUSDT", "SOLUSDT"])

    assert result["requested_outside_configured"] == ["ENAUSDT"]
    assert result["requested_inside_configured"] == ["SOLUSDT"]
    assert result["inferred_cause"] == "missing_symbols_outside_fixed_universe"
    assert result["symbol_causes"]["ENAUSDT"] == "fixed_universe_mismatch"
