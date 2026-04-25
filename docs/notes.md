# Round 3 notes

## Problem

- **Underlying:** `VELVETFRUIT_EXTRACT` (~5250)
- **Independent delta-1:** `HYDROGEL_PACK` (~10000)
- **Vouchers (European calls):** 10 strikes — 4000, 4500, 5000, 5100, 5200, 5300, 5400, 5500, 6000, 6500
- **Position limits:** underlying 200, hydrogel 200, each voucher 300
- **r = 0** (no funding/lending/dividends in Prosperity; structural, not an approximation)
- **Currency:** XIRECS

## TTE schedule

| Day | Start TTE | End TTE |
|---|---|---|
| Historical day 0 | 8d | 7d |
| Historical day 1 | 7d | 6d |
| Historical day 2 | 6d | 5d |
| Live Round 3 | 5d | 4d |
| Live Round 4 | 4d | 3d |
| Live Round 5 | 3d | 2d |

**Formula (matches Frankfurt Hedgehogs):** `TTE_years = (8 - day - ts/1_000_000) / 365`
For the live trader: `TTE_years = (TTE_DAYS_AT_ROUND_START - ts/1_000_000) / 365` with `TTE_DAYS_AT_ROUND_START = 5` for R3.

## Mapping to Prosperity 3 Round 3

| P3 R3 | P4 R3 |
|---|---|
| VOLCANIC_ROCK (~10000) | VELVETFRUIT_EXTRACT (~5250) |
| 5 vouchers (9500-10500) | 10 vouchers (4000-6500) |
| — | HYDROGEL_PACK (independent delta-1) |
| Pos limit 200/voucher | Pos limit 300/voucher |

Schedule identical (7d at R1 → 5d at R3 live start). Frankfurt's playbook transfers cleanly. More strikes = cleaner smile fit.

## Reference strategy (Frankfurt Hedgehogs, P3 winners)

1. BS with r=0 → implied vol per voucher per timestamp
2. Moneyness `m = ln(K/S) / sqrt(TTE)`
3. Fit quadratic `iv_hat(m) = a·m² + b·m + c` globally across all (m, iv) points
4. Detrend: `Δσ = σ − iv_hat(m)`
5. Push `iv_hat` back through BS → theoretical price → `price_dev = mid − theo` (tradable in XIRECS)
6. Validate with 1-lag autocorrelation (negative → scalpable)
7. Scalp only strikes with enough vega and good signal
8. Optional mean-reversion overlay on underlying

Last year's hardcoded coeffs (do NOT reuse — different asset): `[0.27362531, 0.01007566, 0.14876677]`

## Research findings (research/round3.ipynb)

### Global smile fit
`iv_hat(m) = 0.03037029·m² + 0.00224891·m + 0.23941452`

Per-day fits:
- day 0: a=+0.04588, b=-0.00428, c=+0.23784
- day 1: a=+0.01129, b=+0.00440, c=+0.24089
- day 2: a=+0.03442, b=+0.00531, c=+0.23961

Vol level (c ~0.239) is very stable. Curvature (a) wobbles a bit. Much shallower smile than last year's Volcanic Rock (a~0.27).

### Strike viability

**Usable (6 strikes):** VEV_5000, 5100, 5200, 5300, 5400, 5500

**Unusable (skip entirely in the trader):**
- VEV_4000, VEV_4500 — deep ITM, mid is essentially pure intrinsic, zero extrinsic → IV uninvertible
- VEV_6000, VEV_6500 — dead books, only 1 unique mid across all 30k rows of day 0 (stuck at 0.5)

### Autocorrelation (price-space deviation)

Strongly negative `ac1_diff` across all 6 viable strikes → IV scalping is the primary alpha:

| Strike | n | ac1_diff | std_dev | mean_vega | mean_delta |
|---|---|---|---|---|---|
| VEV_5000 | 29886 | -0.494 | 0.358 | 88.4  | 0.935 |
| VEV_5100 | 30000 | -0.487 | 0.870 | 182.1 | 0.821 |
| VEV_5200 | 30000 | -0.493 | 0.875 | 264.0 | 0.625 |
| VEV_5300 | 30000 | -0.481 | 1.127 | 267.1 | 0.390 |
| VEV_5400 | 30000 | -0.453 | 0.764 | 192.5 | 0.195 |
| VEV_5500 | 30000 | -0.330 | 0.414 | 103.1 | 0.079 |

### Underlying

`VELVETFRUIT_EXTRACT` return ac1 = **-0.129** — modestly negative. Side-bet mean reversion is supported but weaker than the IV scalping edge.

### HYDROGEL_PACK

- Range: 9891 - 10079, mean ~9991, std 31.94
- Spread: mean 15.72, median 16
- Return std: 2.17, ac1 = **-0.129**
- Treatment: standard market making with edge; wide spread + modest mean reversion.

## trader1.py design

### Vouchers
- Hardcoded smile coeffs from research (will drift in live — monitor)
- Stateless per-tick: compute `m` → `iv_hat` → BS theo → `dev = mid − theo`
- Trade only on 6 viable strikes
- Execute aggressively at best_bid (sell) / best_ask (buy):
  - `dev > THR_OPEN` (0.5) → target pos = -limit
  - `dev < -THR_OPEN` → target pos = +limit
  - `dev > THR_CLOSE` (0.0) and `pos > 0` → close to 0
  - `dev < -THR_CLOSE` and `pos < 0` → close to 0
  - else: hold

### Delta-1 products (VELVETFRUIT_EXTRACT, HYDROGEL_PACK)
- Take any crosses of wall_mid (ask below mid → buy, bid above mid → sell)
- Passive MM: buy at `bid_wall + 1`, sell at `ask_wall - 1`, only when `ask_wall - bid_wall >= 2`
- Size: fill up to position limit

### Wall mid
Midpoint of the price levels with the **largest volume** on each side of the book. Much less noisy than best-bid/best-ask mid; used everywhere as the fair-price anchor.

## Known gaps / deliberate simplifications

1. **Flat THR_OPEN=0.5** across all strikes — fires rarely on VEV_5000/5500 (std 0.36/0.41), chronically on VEV_5300 (std 1.13). Should be per-strike (scaled by std).
2. **No signal smoothing** — Frankfurt used an EMA on `dev` to suppress single-tick flicker.
3. **No underlying mean-reversion overlay** despite ac1 = -0.129.
4. **No online smile refit** — hardcoded coeffs. Fine for R3 if R3 live looks like historical, risky otherwise. Per-day a-coefficient drift (0.011 → 0.046) suggests the smile isn't perfectly stationary.
5. **No gamma/delta hedging** — ignored because bid-ask spreads make explicit hedging expensive (Frankfurt's finding).
6. **No traderData state** — everything stateless.
7. **Deep-ITM strikes (4000/4500) left on the table** — there might be simple arbitrage here (mid vs intrinsic) but untested.
8. **THR_CLOSE = 0** means closes happen as soon as dev crosses back through zero — matches Frankfurt but aggressive.

## Caveats flagged before building

- TTE convention matches Frankfurt: `DAY` constant indexes the live trading day continuously through history (day 0 = historical day 0, day 5 would be R5 live last year).
- Wall mid beats top-of-book mid for IV inversion — top-of-book flicker creates fake IV oscillation.
- `EXTRINSIC_FLOOR = 1.5` filter before fitting IV: drops rows where `mid - intrinsic < 1.5` (numerically unstable).
- IV solver is `scipy.optimize.brentq` on [1e-4, 5.0], catches sign-change failure → NaN.
- BS validated: `bs_call(100, 100, 1.0, 0.2) = 7.9656` exact; IV roundtrip exact.
- Day-boundary plotting: global `t = day*1_000_000 + timestamp` for continuous x-axis.
- IV at round start for live R3: TTE=5d, i.e., `tte = (5 - ts/1e6)/365`.

## Parameters to update per round

In `traders/trader1.py`:

```python
TTE_DAYS_AT_ROUND_START = 5  # R3=5, R4=4, R5=3
```

If the smile changes materially between rounds, refit coefficients from fresh data:

```python
SMILE_A, SMILE_B, SMILE_C = 0.03037029, 0.00224891, 0.23941452
```

## File map

| Path | Purpose |
|---|---|
| `data/ROUND3/` | Price/trade CSVs for historical days 0-2 |
| `docs/hints_round3.md` | Competition hints (IV, moneyness, volume sizing) |
| `docs/writing_an_algorithm.md` | Prosperity `Trader` / `TradingState` API reference |
| `references/imc-prosperity-3/README.md` | Frankfurt Hedgehogs writeup (P3 winners) |
| `references/imc-prosperity-3/FrankfurtHedgehogs_polished.py` | Their final P3 submission |
| `research/round3.ipynb` | Replicated research notebook (executed, 2.25 MB) |
| `traders/trader1.py` | Basic R3 trader |

## Env

- `conda activate quant`
- Python 3.10, pandas 2.2.3, numpy 2.2.2, scipy 1.15.1, matplotlib, jupyter available
- Prosperity submissions may only use: pandas, numpy, statistics, math, typing, jsonpickle. `scipy` is research-only — the live trader uses `statistics.NormalDist` from stdlib.
