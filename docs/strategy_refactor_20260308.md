# Strategy Refactor 2026-03-08

## 1. Current problem

The current engine treats meme trading as a scored chart/trend model with three model IDs (`A/B/C`).
That structure is wrong for the strategy now requested:

- Meme trading should be split by `execution style`, not by generic score model.
- The current meme engine is optimized for filtered entry/exit scoring, not for launch detection and immediate execution.
- Real sniping needs lower-latency discovery and launch-aware execution, not only DexScreener + delayed social aggregation.
- The current UI is organized around `model comparison`, but the new system needs `strategy workflow`.

## 2. New target structure

### Meme engine

Meme trading is no longer treated as three separate portfolio models.

- There is one `Unified Meme Engine`
- `THEME` is the main always-on flow
- `NARRATIVE` and `SNIPER` are not equal parallel engines
- `NARRATIVE` and `SNIPER` fire only when their own trigger conditions are satisfied
- Demo/live capital stays separate, but meme signal generation and strategy state belong to one engine

### Meme engine functions

#### M1. Theme Basket

Purpose:
- Continuously scan newly launched Pump.fun / Bonk tokens.
- Build a theme bucket from social sources and launch metadata.
- If the same theme/similarity cluster appears in `2+` fresh launches, buy the basket automatically.
- This is the main default flow of the meme engine.

Execution:
- Fixed per-entry live size: `0.1 SOL`
- Split across matched tokens in the same theme cluster
- When position reaches `+75%`, sell `50%`
- Leave remainder as runner
- Manual operator review for stale positions
- Optional stale-exit rule later: sell if no fresh activity for `48h`

Decision basis:
- launch freshness
- theme similarity count
- repeated source mentions
- initial liquidity / volume / buy flow
- holder overlap / holder concentration / suspicious wallet pattern

#### M2. Sniper

Purpose:
- Detect token immediately after launch / pool creation / first burst mention.
- Buy before chart structure matters.
- This is an event-triggered utility, not the main loop.

Execution:
- Fixed per-entry live size: `0.2 SOL`
- `+75%` => sell `50%`
- Hold runner until manual review or later strategy rule

Decision basis:
- launch event first
- social corroboration second
- tradability / route / liquidity sanity check required before send

#### M3. Narrative

Purpose:
- Detect revival of previously dead or dormant meme coins.
- Enter when theme, source burst, and liquidity reactivation return together.
- This is an event-triggered utility, not the main loop.

Execution:
- Fixed per-entry live size: `0.2 SOL`
- `+75%` => sell `50%`
- Keep the remainder as runner until manual review or stale-exit policy

Decision basis:
- repeated mentions across `X / Reddit / 4chan`
- theme reactivation
- returning volume / liquidity
- whale or suspicious-cluster re-entry
- tradability / route / price impact validation

### Crypto strategies

Replace current 3-model demo structure with 4 strategy families:

#### C1. Short-Term Scalp
- TF: `5m / 15m / 1h`
- universe: rank `1~50`
- frequent but filtered entries
- strict stop and shorter holding time

#### C2. Aggressive Intraday
- TF: `5m / 15m / 1h / 4h`
- universe: rank `1~50`
- higher leverage, higher velocity
- more tolerant of pullback noise

#### C3. 10-Trades-Per-Day Swing
- TF: `15m / 1h / 4h / 1D`
- hard daily trade budget = `10`
- rotation allowed only by explicit strategy rule

#### C4. Single-Position Conviction
- TF: `15m / 1h / 4h / 1D`
- one position only
- `100%` model seed usage
- medium-short swing holding

## 3. Required engine refactor

### Separate `model` from `strategy`

Current:
- `MODEL_IDS = (A, B, C)` drives meme + crypto + UI + demo + live

Target:
- meme uses one engine where `THEME` is always-on and `NARRATIVE / SNIPER` are trigger-based functions
- crypto uses four models/strategies `SCALP / AGGRESSIVE / SWING10 / CONVICTION1`
- demo/live should reference the same strategy definition, but use separate capital state

### Separate `signal engine` from `execution engine`

Current:
- scoring, entry, position update, live execution are tightly coupled

Target:
- `discovery layer`
- `signal builder`
- `execution allocator`
- `position manager`
- `reporting/UI layer`

### Separate `portfolio state` from `strategy state`

Need:
- `meme engine signal state`
- `demo portfolio state`
- `live portfolio state`
- `wallet/account reconciliation state`

The same meme engine should emit signals once, then demo/live consume independently with their own capital.

## 4. Meme sniping architecture

### Discovery priority

1. On-chain launch detection
2. Launchpad feed detection
3. Social corroboration
4. Route / liquidity / price impact validation
5. Execution

### Recommended source stack

#### Must-have
- Helius RPC / WebSocket
- Jupiter swap/quote
- Pump.fun launch feed
- Bonk launch feed
- DexScreener/Birdeye snapshot hydration

#### Should-have
- Helius Sender / staked connection path for lower latency send
- priority fee policy
- token route/tradable precheck

### Why Jupiter alone is not enough

Jupiter is execution and routing.
It is not a reliable discovery layer for token creation events.

Need additional detection:
- program logs / account events
- launchpad feed polling
- social burst detection

### Recommended real-time flow

#### Theme Basket
- poll Pump.fun / Bonk launch feeds every `2~5s`
- enrich each new mint with Dex/Birdeye snapshot
- push to theme clustering queue
- if cluster size >= 2 and tradable => enter basket

#### Sniper
- subscribe to launch / liquidity / migration signals
- attach social corroboration window (`0~120s`)
- if route exists and liquidity floor passes => fire entry immediately
- only activate when trigger conditions are met

#### Narrative
- watch previously discovered tokens and recent loser pools
- detect repeated source burst across `X / Reddit / 4chan`
- require volume/liquidity reactivation before entry
- fire only if route exists, price impact is bounded, and the token is not in rug/suspicion deny-list
- only activate when trigger conditions are met

## 5. Social source design for meme trading

Current social design is too generic.

Target social objects:
- source kind: `x / reddit / 4chan / news / launchpad / onchain`
- account/board/subreddit/thread
- first_seen_ts
- last_seen_ts
- mention_count
- related theme key
- related mint(s)

The key output is not just `score`.
The key output is:

- `what theme`
- `which sources`
- `how many mentions`
- `when first detected`
- `which tokens are inside the cluster`

## 6. UI redesign target

### Replace current `model widget` centered layout

Target main navigation:

- `Meme Engine`
- `Crypto Scalp`
- `Crypto Aggressive`
- `Crypto Swing10`
- `Crypto Conviction1`
- `Demo Portfolios`
- `Live Portfolios`
- `Source Monitor`
- `Execution Monitor`

### Meme UI should show

- fresh launches
- theme clusters
- source timeline
- current sniper candidates
- route/liquidity/tradable status
- partial take-profit history
- runner inventory

### Demo / Live UI should show

- strategy-level PNL
- separate capital base
- open positions
- realized exits
- runner inventory
- execution failures

## 7. What must be added to config

### New infra
- `HELIUS_API_KEY`
- `HELIUS_RPC_URL`
- `HELIUS_WS_URL`
- `HELIUS_SENDER_URL` or equivalent low-latency send path if used
- `BIRDEYE_API_KEY` (recommended for richer token trade/liquidity snapshots)

### Meme strategy settings
- `MEME_THEME_ENTRY_SOL`
- `MEME_LAUNCH_ENTRY_SOL`
- `MEME_NARRATIVE_ENTRY_SOL`
- `MEME_PARTIAL_TAKE_PROFIT_PCT` default `0.75`
- `MEME_PARTIAL_TAKE_PROFIT_SELL_RATIO` default `0.50`
- `MEME_THEME_CLUSTER_MIN_TOKENS`
- `MEME_STALE_EXIT_HOURS`
- `MEME_SNIPER_SOCIAL_WINDOW_SECONDS`
- `MEME_SNIPER_MAX_PRICE_IMPACT_PCT`
- `MEME_SNIPER_MAX_SLIPPAGE_BPS`
- `MEME_SNIPER_POLL_SECONDS`
- `SOCIAL_4CHAN_ENABLED`
- `SOCIAL_4CHAN_BOARDS`

### Crypto strategy settings
- leverage bands per strategy
- max daily trades for swing strategy
- fixed one-position mode settings
- market cap / rank universe range

## 8. Migration sequence

### Phase 1
- freeze current meme A/B/C logic
- introduce one `meme engine registry` where THEME is primary and NARRATIVE/SNIPER are trigger functions
- keep old UI alive temporarily

### Phase 2
- build `meme_discovery_service`
- separate Pump.fun / Bonk launch ingestion
- add Helius real-time listeners

### Phase 3
- implement `THEME` strategy
- implement `SNIPER` strategy
- implement `NARRATIVE` strategy
- keep meme capital state unified per engine, while demo/live remain separate

### Phase 4
- replace crypto model stack with 4 strategy families
- rebuild UI completely around strategies and portfolios

### Phase 5
- remove legacy meme A/B/C specific UI
- remove legacy model-centric wording

## 9. Immediate next implementation steps

1. Add strategy registry layer
2. Split meme engine into:
   - launch discovery
   - theme clustering
   - launch sniper queue
   - narrative sniper queue
3. Add Helius config and transport abstraction
4. Create new DB tables/state buckets for:
   - launch events
   - theme clusters
   - sniper candidates
   - strategy portfolios
5. Rebuild UI navigation around strategies instead of `A/B/C`
