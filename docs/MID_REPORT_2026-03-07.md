# AutoTrading Mid Report (2026-03-07)

## 1) Goal
- Summarize current status for 6 models: Meme A/B/C and Crypto A/B/C.
- Define a practical 10-step improvement plan for the next 30 days.
- Keep all recommendations feasible with free-tier data/API sources.

## 2) Model Definitions
### Meme models
- `A (Dogri Meme Selector)`: quality-first filter with wallet pattern and holder risk checks.
- `B (Meme Swing Predictor)`: longer holding profile (default 14 days), lower turnover.
- `C (Meme Scalp Momentum)`: fast momentum strategy for high-turnover short-term entries.

### Crypto models
- `A (Stable Trend)`: defensive trend alignment (quality + 1h/4h/1d alignment).
- `B (Flow Follower)`: pullback + trend continuation balance model.
- `C (Dongri Momentum)`: aggressive momentum/breakout model.

## 3) Current PNL Snapshot (2026-03-07 12:14:55 KST)
### Meme
| Model | Seed | Equity | Total PNL | Realized PNL | Win rate | Open |
|---|---:|---:|---:|---:|---:|---:|
| C Meme Scalp Momentum | $10,000 | $125,197.29 | $115,197.29 | $115,197.29 | 43.8% | 0 |
| B Meme Swing Predictor | $10,000 | $12,376.36 | $2,376.36 | $2,376.36 | 27.3% | 0 |
| A Dogri Meme Selector | $10,000 | $5,771.03 | $-4,228.97 | $-4,228.97 | 0.0% | 0 |

### Crypto
| Model | Seed | Equity | Total PNL | Realized PNL | Win rate | Open |
|---|---:|---:|---:|---:|---:|---:|
| A Stable Trend | $10,000 | $11,392.52 | $1,392.52 | $0.00 | 0.0% | 2 |
| C Dongri Momentum | $10,000 | $10,376.29 | $376.29 | $0.00 | 0.0% | 4 |
| B Flow Follower | $10,000 | $9,775.28 | $-224.72 | $-316.23 | 0.0% | 1 |

## 4) Mid Findings
- Model dispersion is high on Meme side: C strongly outperforms, A underperforms.
- Crypto A/C are currently unrealized-profit driven; B already has realized loss.
- Frequent operational failure classes in logs:
  - `TOKEN_NOT_TRADABLE` (Jupiter quote 400)
  - `custom program error 0x1788 / 6024` during close simulation
  - Live setup failure when private key format is invalid

## 5) 10-Step Improvement Plan (free-tier first)
1. Add 12h blacklist for `TOKEN_NOT_TRADABLE` mints to stop repeated waste.
2. Add two-stage close retry (higher slippage + route fallback) for 0x1788 class errors.
3. Tighten liquidity gate for meme entries (minimum route depth + recent fills).
4. Keep UI and execution on one unified Bayesian final score only.
5. Increase A/B/C feature separation (not just threshold differences).
6. Keep position sizing in 15-30% band, score-weighted allocation.
7. Tune only every 24h and only under underperformance conditions.
8. Keep variant-based tuning (`A1`, `B1`, etc.) without overwriting parent models.
9. Split Demo/Live telemetry and Telegram reporting by market and by selected live models.
10. Monthly model selection with objective metrics: PNL, MDD, trade count, hit ratio.

## 6) Model Improvement Method (operational)
- Keep parent model immutable; create child variants for every tuning cycle.
- Use rolling 24h + cumulative history together for parameter changes.
- Ranking priorities:
  - 1st: Max Drawdown
  - 2nd: Net PNL (realized + unrealized)
  - 3rd: Win rate / profit factor
- Promotion rule to live:
  - only models with enough samples and acceptable drawdown in demo.

## 7) Free Data Stack
- Market/meta: CoinGecko (+ CMC optional)
- Realtime pricing: Binance public + Bybit public market data
- On-chain wallet patterns: Solscan free CU budget scheduler
- Text trend: X/community/news scraping + low-frequency Gemini summarization
