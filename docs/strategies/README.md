# Discretionary-Confirmation Strategy Specs

This folder holds the implementable specifications for the three setups the
system detects on **D1 / H4** charts. The detectors do **not** trade
autonomously: each fires an **alert with a chart overlay** to the account owner
(Jackson), who eyeballs the setup and approves or denies. The specs are
therefore tuned for **high-quality, explainable setups a human can judge**, not
for blind statistical edge.

- Account: **FTMO Swing, $25,000, 1% risk per trade** (= $250 risk/trade).
- Symbols: FTMO custom symbols with real Dukascopy tick history (`*.dk`),
  timestamps **UTC** (project policy).
- Everything here must be computable in **MQL5** on closed bars — no indicator
  or data source that MQL5 can't provide in the tester and live.

## The three strategies at a glance

| # | Strategy | File | Direction | Primary TF (context) | Style | Typical R:R | Expected WR | Expected frequency* |
|---|----------|------|-----------|----------------------|-------|-------------|-------------|---------------------|
| 1 | Liquidity Sweep & Market Structure Shift (SMC) | [liquidity-sweep-mss.md](liquidity-sweep-mss.md) | Long & short (reversal) | H4 trigger (D1 context) | Reversal at a stop-run | **2.5–4 R** | ~40–45% | ~2–5 / month |
| 2 | Deep Fibonacci Retracement In-Trend | [deep-fib-retracement.md](deep-fib-retracement.md) | Long & short (with trend) | H4 trigger (D1 bias) | Trend pullback | **2.5–5 R** | ~45–50% | ~3–6 / month |
| 3 | 20 EMA Mean Reversion | [ema20-mean-reversion.md](ema20-mean-reversion.md) | Long & short (counter-stretch) | H4 (or D1) | Mean reversion | **1.3–2 R** | ~55–62% | ~4–8 / month |

\* Per symbol, rough order-of-magnitude on H4 for a liquid FX major; wider on
volatile symbols. These are design expectations to be **measured** in the
Phase-2 tick backtest, not promises.

> **Blend note.** Strategies 1 and 2 carry the favourable R:R at a moderate win
> rate; strategy 3 is the classic high-win-rate / low-R:R leg. The portfolio,
> not any single strategy, targets the "40–50% WR with favourable R:R" profile.
> Strategy 3 must **not** be run with a 1 R target — see its spec.

---

## Shared conventions (single source of truth)

All three specs use these definitions. Where a spec needs to differ it says so
explicitly and points back here.

### Bars and repainting

- **Closed-bar only.** Every *detector state transition* (setup forming →
  armed → triggered → invalidated) is evaluated **only on the close of an
  H4/D1 bar**. No transition ever depends on the current, still-forming bar.
- **Resting orders may fill intrabar — that is not repainting.** When a setup
  is *armed*, a **pending order** (limit or stop) rests in the market at a
  precomputed price with SL/TP attached. It can fill at any time, including
  mid-bar. This is a real order sitting in the book, fully reproducible in the
  MQL5 tester, and is distinct from a detector "seeing" an intrabar move.
  The rule is: **decisions on closed bars; fills by resting orders.**
- **Fractal confirmation lag.** A swing point (below) is only *confirmed* after
  `fractal_n` bars have closed to its right. So swing-based levels are known
  `fractal_n` bars late — correct and non-repainting, but the state machine
  must treat a swing as usable only once confirmed. **Level pools**
  (PDH/PDL/PWH/PWL) have **no** lag: they are fixed the instant the day/week
  closes.

### Swing points (fractals)

- **Swing high** at bar *i*: `high[i]` is strictly greater than `high` of the
  `fractal_n` bars immediately left **and** the `fractal_n` bars immediately
  right. **Swing low**: symmetric with `low`.
- **Default `fractal_n = 2`** (a 5-bar fractal: 2 left, 2 right). Sane range
  1–3. Confirmation lag = `fractal_n` closed bars.
- Ties: require *strict* inequality; if a plateau of equal highs occurs, no
  fractal forms at those bars (they may instead qualify as an equal-highs pool,
  strategy 1).

### Market-structure labels

- **HH / HL / LH / LL** are defined on the sequence of *confirmed* fractal
  swings: e.g. a swing high is a **HH** if it exceeds the previous confirmed
  swing high, a **LH** if it does not. Same logic for lows.
- **Uptrend** = most recent confirmed swing high is a HH **and** most recent
  confirmed swing low is a HL. **Downtrend** = mirror (LH + LL). Otherwise
  **range/undefined**.

### ATR

- **ATR(14), Wilder smoothing**, on the **detection timeframe** (the TF the
  setup triggers on), computed on closed bars. This is exactly MQL5
  `iATR(sym, tf, 14)`. All "in ATR" tolerances and buffers use this value read
  at the **signal bar's close** (frozen for that setup, so the level doesn't
  wander).

### Level pools (day/week/session)

- **PDH / PDL** = prior completed **D1** bar's high / low.
- **PWH / PWL** = prior completed **calendar-week** high / low.
- **Session extreme** (used only by strategy 1, optional): the high/low of the
  H4 bars whose **open time (UTC)** falls inside a named session window:
  - Asia `00:00–07:00`, London `07:00–13:00`, New York `13:00–20:00` (UTC).
  - Granularity caveat: H4 bars break at 00/04/08/12/16/20 UTC, so a "session"
    is approximated by whole H4 bars, not the exact clock open. PDH/PDL/PWH/PWL
    are the cleaner, preferred pools; session extremes are a secondary flag.

### Buffer (SL padding)

Wherever a spec places a stop "beyond X", the buffer is:

```
buffer = max(sl_buffer_atr * ATR(14), spread_now, min_stop_points * point)
```

- `sl_buffer_atr` default **0.10** (range 0.05–0.25).
- `spread_now` = current spread (guards against being wicked out by the spread).
- `min_stop_points` respects the broker's `SYMBOL_TRADE_STOPS_LEVEL`.

### Position sizing (1% rule, MQL5-exact)

```
risk_usd        = capital_usd * risk_per_trade          # 25000 * 0.01 = 250
sl_distance     = |entry_price - sl_price|              # in price
ticks_at_risk   = sl_distance / SYMBOL_TRADE_TICK_SIZE
money_per_lot   = ticks_at_risk * SYMBOL_TRADE_TICK_VALUE
lots_raw        = risk_usd / money_per_lot
lots            = floor(lots_raw / SYMBOL_VOLUME_STEP) * SYMBOL_VOLUME_STEP
lots            = clamp(lots, SYMBOL_VOLUME_MIN, SYMBOL_VOLUME_MAX)
```

- If `lots < SYMBOL_VOLUME_MIN`, the SL is too wide for 1% at min lot →
  **skip the setup** (do not round up; that would exceed 1%).
- Entry and SL are always known before sizing, so risk is exact regardless of
  how TP is managed.

### FTMO drawdown awareness

- FTMO Swing limits: **max daily loss 5% ($1,250)**, **max total loss 10%
  ($2,500)** (trailing on this account type — treat 10% from peak as the hard
  floor).
- At 1% risk, **5 full losers in one day** breaches the daily limit. Guardrails
  baked into the specs / orchestrator:
  - **Concurrent open risk cap:** total risk of all *open + armed* positions ≤
    **3%** ($750) at any time. New alerts beyond that are suppressed.
  - **Daily new-risk cap:** do not *arm* new setups once realised + open loss
    for the UTC day reaches **3%**; hard stop new setups at 4%.
  - These are portfolio rules enforced by the orchestrator; each spec assumes
    them and does not re-derive them.

### One-setup-per-symbol-at-a-time

- **At most one live setup per symbol across all three strategies.** While a
  symbol has an *armed* or *open* setup, its detectors suppress new alerts on
  that symbol. Rationale: keeps the human's decision load clean and prevents
  stacking correlated risk on one instrument.
- If two strategies arm on the same symbol on the same bar, priority order is
  **1 (Sweep+MSS) > 2 (Deep Fib) > 3 (EMA reversion)** (most selective first).

### Timeframe interplay (default)

- **D1 = context/bias, H4 = trigger.** Each spec defines a D1 gate/flag and an
  H4 trigger. Each also documents a **single-TF variant** (run entirely on D1)
  for slower, higher-conviction alerts.

### Directionality

- Every spec is written for the **long** case with the **short** case being the
  exact mirror (swap high↔low, above↔below, bullish↔bearish). Specs give the
  long rules and state "short = mirror" rather than repeating.

### Alert lifecycle (shared)

`SETUP FORMING` → `ARMED` (pending order placed, overlay drawn, human alerted)
→ `TRIGGERED` (order filled) → closed by SL/TP, **or** `INVALIDATED` (armed
order cancelled on a qualifying close before fill, or setup conditions break).
The human is alerted at **ARMED**; the alert carries the full overlay so the
decision is made on the chart.
