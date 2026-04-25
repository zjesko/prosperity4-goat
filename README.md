# Prosperity4 GOAT Branch

This branch contains an updated Round 3 trading setup for IMC Prosperity 4:

- `traders/trader2.py`
  Submission-safe Round 3 trader that trades:
  - `HYDROGEL_PACK`
  - `VELVETFRUIT_EXTRACT`
  - all 10 `VEV_*` vouchers

- `traders/trader_round3_alpha_mm_submission.py`
  Explicit copy of the uploadable submission trader.

- `dashboard.py`
  Enhanced Streamlit dashboard for replaying persisted backtests and inspecting:
  - full smile snapshots across strikes
  - fair fitted curve vs observed IV
  - current position by strike
  - signal strength by strike
  - ATM-cluster diagnostics
  - option delta / vega exposure
  - continuous total PnL
  - playback controls and focused tick inspection

- `run_dashboard.sh`
  Launch helper for the dashboard.

## Strategy Summary

The current Round 3 trader uses:

- passive and semi-aggressive market making for the delta-1 products
- order-book imbalance and microprice edge as the main short-horizon alpha
- cross-sectional smile fitting for the voucher chain
- inventory-aware fair-value skewing
- all-strike voucher quoting, with curve residuals used when IV is reliable and book-based fallback when it is not

The cleaned trader intentionally avoids forbidden imports like `os` so it can be uploaded to the Prosperity portal.

## Current Backtest Reference

Verified locally on `data/ROUND3` with the cleaned submission trader:

- Total PnL: `42,871.0`
- Trades: `3,420`

Main contributors in that verified run:

- `HYDROGEL_PACK`: `23,261.0`
- `VELVETFRUIT_EXTRACT`: `9,434.0`
- `VEV_4000`: `8,843.0`

## How To Run

### Run the dashboard

```bash
./run_dashboard.sh
```

### Run a backtest

```bash
rust_backtester \
  --trader traders/trader2.py \
  --dataset data/ROUND3 \
  --persist \
  --artifact-mode full \
  --output-root runs
```

### Upload target

Use either of these files for submission:

- `traders/trader2.py`
- `traders/trader_round3_alpha_mm_submission.py`

They are intended to contain the same uploadable strategy logic.

## Notes

- `dashboard_old.py` and `trader2_old.py` are local backups of the earlier files.
- `runs/` contains local backtest artifacts and should generally stay uncommitted.
