"""
Enhanced Prosperity Round 3 dashboard.

This app is designed to inspect persisted rust_backtester runs, especially
Round 3 option strategies. It adds:
  - reconstructed per-product position path
  - fair-value and curve overlays for vouchers
  - alpha-strength diagnostics from book pressure and curve mispricing
  - smile snapshot at a selected timestamp

By default it points at the sibling Prosperity4 repo if present.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from statistics import NormalDist
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots


_N = NormalDist()

APP_ROOT = Path(__file__).resolve().parent
DEFAULT_SOURCE_ROOT = APP_ROOT.parent / "Prosperity4"
LOCAL_FALLBACK_ROOT = APP_ROOT

UNDERLYING = "VELVETFRUIT_EXTRACT"
HYDROGEL = "HYDROGEL_PACK"
ALL_STRIKES = (4000, 4500, 5000, 5100, 5200, 5300, 5400, 5500, 6000, 6500)
ROUND3_TTE_BY_DAY = {0: 8.0, 1: 7.0, 2: 6.0}

PRODUCT_COLORS = {
    UNDERLYING: "#00bfff",
    HYDROGEL: "#ffa500",
    "VEV_5000": "#ff6b6b",
    "VEV_5100": "#ff9f43",
    "VEV_5200": "#ffd32a",
    "VEV_5300": "#0be881",
    "VEV_5400": "#0fbcf9",
    "VEV_5500": "#f8b739",
    "VEV_4000": "#808e9b",
    "VEV_4500": "#808e9b",
    "VEV_6000": "#808e9b",
    "VEV_6500": "#808e9b",
}


@dataclass
class DayRun:
    day: int
    run_dir: Path
    metrics: Dict[str, Any]
    bundle: Dict[str, Any]


def repo_root() -> Path:
    return DEFAULT_SOURCE_ROOT if DEFAULT_SOURCE_ROOT.exists() else LOCAL_FALLBACK_ROOT


def runs_dir(root: Path) -> Path:
    return root / "runs"


def traders_dir(root: Path) -> Path:
    return root / "traders"


def data_dir(root: Path) -> Path:
    return root / "data"


def list_traders(root: Path) -> List[str]:
    directory = traders_dir(root)
    if not directory.exists():
        return []
    return sorted(path.name for path in directory.glob("*.py") if not path.name.startswith("_"))


def list_datasets(root: Path) -> List[str]:
    directory = data_dir(root)
    if not directory.exists():
        return []
    return sorted(path.name for path in directory.iterdir() if path.is_dir())


def run_backtest(root: Path, trader_file: str, dataset_name: str, carry: bool) -> str:
    cmd = [
        "rust_backtester",
        "--trader",
        str(traders_dir(root) / trader_file),
        "--dataset",
        str(data_dir(root) / dataset_name),
        "--persist",
        "--artifact-mode",
        "full",
        "--output-root",
        str(runs_dir(root)),
    ]
    if carry:
        cmd.append("--carry")
    env = os.environ.copy()
    env["PATH"] = env.get("PATH", "") + ":" + str(Path.home() / ".cargo" / "bin")
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(root), env=env)
    return result.stdout + result.stderr


def discover_runs(root: Path) -> List[Dict[str, Any]]:
    all_runs = []
    run_root = runs_dir(root)
    if not run_root.exists():
        return []

    for manifest in sorted(run_root.glob("backtest-*/manifest.json"), reverse=True):
        try:
            payload = json.loads(manifest.read_text())
        except Exception:
            payload = {}
        all_runs.append({"id": manifest.parent.name, "path": manifest.parent, "manifest": payload})
    return all_runs


def load_day_runs(bundle_dir: Path) -> List[DayRun]:
    day_runs: List[DayRun] = []
    parent = bundle_dir.parent
    prefix = bundle_dir.name
    for child in sorted(parent.glob(f"{prefix}-round3-day*")):
        metrics_path = child / "metrics.json"
        bundle_path = child / "bundle.json"
        if not metrics_path.exists() or not bundle_path.exists():
            continue
        metrics = json.loads(metrics_path.read_text())
        bundle = json.loads(bundle_path.read_text())
        day_runs.append(
            DayRun(
                day=int(metrics.get("day", 0)),
                run_dir=child,
                metrics=metrics,
                bundle=bundle,
            )
        )
    return sorted(day_runs, key=lambda run: run.day)


def voucher_symbol(strike: int) -> str:
    return f"VEV_{strike}"


def strike_from_symbol(symbol: str) -> Optional[int]:
    if not symbol.startswith("VEV_"):
        return None
    try:
        return int(symbol.split("_", 1)[1])
    except ValueError:
        return None


def time_to_expiry_years(day: int, timestamp: int) -> float:
    days_left = ROUND3_TTE_BY_DAY.get(day, 0.0) - timestamp / 1_000_000.0
    return max(days_left / 365.0, 1e-9)


def best_bid_ask(product_state: Dict[str, Any]) -> Tuple[Optional[int], int, Optional[int], int]:
    bids = product_state.get("bids", [])
    asks = product_state.get("asks", [])
    best_bid = bids[0]["price"] if bids else None
    bid_vol = abs(int(bids[0]["volume"])) if bids else 0
    best_ask = asks[0]["price"] if asks else None
    ask_vol = abs(int(asks[0]["volume"])) if asks else 0
    return best_bid, bid_vol, best_ask, ask_vol


def microprice(best_bid: Optional[int], bid_vol: int, best_ask: Optional[int], ask_vol: int) -> Optional[float]:
    if best_bid is None or best_ask is None:
        return None
    total = bid_vol + ask_vol
    if total <= 0:
        return (best_bid + best_ask) / 2.0
    return (best_bid * ask_vol + best_ask * bid_vol) / total


def imbalance(bid_vol: int, ask_vol: int) -> float:
    total = bid_vol + ask_vol
    if total <= 0:
        return 0.0
    return (bid_vol - ask_vol) / total


def bs_call_price(spot: float, strike: float, tte: float, sigma: float) -> float:
    if tte <= 0 or sigma <= 0:
        return max(spot - strike, 0.0)
    root_t = math.sqrt(tte)
    d1 = (math.log(spot / strike) + 0.5 * sigma * sigma * tte) / (sigma * root_t)
    d2 = d1 - sigma * root_t
    return spot * _N.cdf(d1) - strike * _N.cdf(d2)


def implied_volatility(spot: float, strike: float, tte: float, option_price: float) -> Optional[float]:
    intrinsic = max(spot - strike, 0.0)
    if option_price <= intrinsic or spot <= 0:
        return None

    low = 1e-4
    high = 3.0
    low_price = bs_call_price(spot, strike, tte, low)
    high_price = bs_call_price(spot, strike, tte, high)
    if option_price < low_price or option_price > high_price:
        return None

    for _ in range(60):
        mid = (low + high) / 2.0
        mid_price = bs_call_price(spot, strike, tte, mid)
        if mid_price < option_price:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


def solve_linear_system(matrix: List[List[float]], vector: List[float]) -> Optional[List[float]]:
    augmented = [row[:] + [value] for row, value in zip(matrix, vector)]
    size = len(augmented)
    for pivot in range(size):
        best = max(range(pivot, size), key=lambda row: abs(augmented[row][pivot]))
        if abs(augmented[best][pivot]) < 1e-12:
            return None
        augmented[pivot], augmented[best] = augmented[best], augmented[pivot]
        scale = augmented[pivot][pivot]
        for col in range(pivot, size + 1):
            augmented[pivot][col] /= scale
        for row in range(size):
            if row == pivot:
                continue
            factor = augmented[row][pivot]
            for col in range(pivot, size + 1):
                augmented[row][col] -= factor * augmented[pivot][col]
    return [augmented[row][size] for row in range(size)]


def fit_smile(points: Iterable[Tuple[float, float]]) -> Optional[Tuple[float, float, float]]:
    points = list(points)
    if len(points) < 4:
        return None
    xtx = [[0.0, 0.0, 0.0] for _ in range(3)]
    xty = [0.0, 0.0, 0.0]
    for x, y in points:
        basis = [x * x, x, 1.0]
        for i in range(3):
            xty[i] += basis[i] * y
            for j in range(3):
                xtx[i][j] += basis[i] * basis[j]
    for i in range(3):
        xtx[i][i] += 1e-6
    coeffs = solve_linear_system(xtx, xty)
    if coeffs is None:
        return None
    return coeffs[0], coeffs[1], coeffs[2]


def fair_iv(smile: Tuple[float, float, float], moneyness: float) -> float:
    a, b, c = smile
    return max(0.05, min(1.5, a * moneyness * moneyness + b * moneyness + c))


def bs_delta_vega(spot: float, strike: float, tte: float, sigma: float) -> Tuple[float, float]:
    if tte <= 0 or sigma <= 0 or spot <= 0 or strike <= 0:
        return (1.0 if spot > strike else 0.0, 0.0)
    root_t = math.sqrt(tte)
    d1 = (math.log(spot / strike) + 0.5 * sigma * sigma * tte) / (sigma * root_t)
    pdf = math.exp(-0.5 * d1 * d1) / math.sqrt(2.0 * math.pi)
    delta = _N.cdf(d1)
    vega = spot * pdf * root_t
    return delta, vega


def scale_series(series: pd.Series) -> pd.Series:
    std = float(series.std()) if len(series) > 1 else 0.0
    if std <= 1e-9:
        return series * 0.0
    return series / std


@st.cache_data(show_spinner=False)
def build_product_frame(bundle_dir_str: str, product: str) -> pd.DataFrame:
    bundle_dir = Path(bundle_dir_str)
    day_runs = load_day_runs(bundle_dir)
    rows: List[Dict[str, Any]] = []
    offset = 0

    for day_run in day_runs:
        for event in day_run.bundle.get("timeline", []):
            timestamp = int(event.get("timestamp", 0))
            products = event.get("products", {})
            prod_state = products.get(product)
            if prod_state is None:
                continue

            best_bid, bid_vol, best_ask, ask_vol = best_bid_ask(prod_state)
            mid = float(prod_state.get("mid_price", 0.0))
            spread = (best_ask - best_bid) if best_bid is not None and best_ask is not None else None
            micro = microprice(best_bid, bid_vol, best_ask, ask_vol)
            pos = int(event.get("position", {}).get(product, 0))
            pnl_product = float(event.get("pnl_by_product", {}).get(product, 0.0))
            own_trades = event.get("own_trades", [])

            buy_qty = 0
            sell_qty = 0
            for trade in own_trades:
                if trade.get("symbol") != product:
                    continue
                qty = int(trade.get("quantity", 0))
                if trade.get("buyer") == "SUBMISSION":
                    buy_qty += qty
                if trade.get("seller") == "SUBMISSION":
                    sell_qty += qty

            order_qty = 0
            for order in event.get("orders", []):
                if order.get("symbol") == product:
                    order_qty += int(order.get("quantity", 0))

            row = {
                "day": day_run.day,
                "timestamp": timestamp,
                "ts_cont": timestamp + offset,
                "mid_price": mid,
                "best_bid": best_bid,
                "best_ask": best_ask,
                "bid_vol": bid_vol,
                "ask_vol": ask_vol,
                "spread": spread,
                "microprice": micro,
                "imbalance": imbalance(bid_vol, ask_vol),
                "position": pos,
                "pnl_product": pnl_product,
                "buy_qty": buy_qty,
                "sell_qty": sell_qty,
                "net_trade_qty": buy_qty - sell_qty,
                "net_order_qty": order_qty,
                "pnl_total": float(event.get("pnl_total", 0.0)),
                "trader_data_raw": event.get("trader_data", ""),
            }

            if product in (UNDERLYING, HYDROGEL):
                row["micro_edge"] = 0.0 if micro is None else micro - mid
                row["alpha_book_strength"] = row["imbalance"] + 0.5 * (row["micro_edge"] / max(1.0, float(spread or 1.0)))

            strike = strike_from_symbol(product)
            if strike is not None:
                spot_state = products.get(UNDERLYING)
                if spot_state is not None:
                    spot = float(spot_state.get("mid_price", 0.0))
                    tte = time_to_expiry_years(day_run.day, timestamp)
                    intrinsic = max(spot - strike, 0.0)
                    extrinsic = mid - intrinsic
                    option_micro = 0.0 if micro is None else micro - mid
                    row.update(
                        {
                            "spot_mid": spot,
                            "tte_years": tte,
                            "intrinsic": intrinsic,
                            "extrinsic": extrinsic,
                            "micro_edge": option_micro,
                        }
                    )
            rows.append(row)
        if rows:
            offset = int(rows[-1]["ts_cont"]) + 100

    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame

    if product not in (UNDERLYING, HYDROGEL):
        voucher_meta = build_voucher_meta(bundle_dir_str)
        meta = voucher_meta[voucher_meta["symbol"] == product].copy()
        if not meta.empty:
            frame = frame.merge(
                meta[
                    [
                        "day",
                        "timestamp",
                        "observed_iv",
                        "fair_iv",
                        "curve_price",
                        "curve_resid",
                        "curve_resid_norm",
                        "neighbor_iv_resid",
                        "alpha_strength",
                        "smile_a",
                        "smile_b",
                        "smile_c",
                    ]
                ],
                on=["day", "timestamp"],
                how="left",
            )
    else:
        frame["alpha_strength"] = scale_series(frame["alpha_book_strength"].fillna(0.0))

    return frame.sort_values(["day", "timestamp"]).reset_index(drop=True)


@st.cache_data(show_spinner=False)
def build_voucher_meta(bundle_dir_str: str) -> pd.DataFrame:
    bundle_dir = Path(bundle_dir_str)
    day_runs = load_day_runs(bundle_dir)
    rows: List[Dict[str, Any]] = []

    for day_run in day_runs:
        for event in day_run.bundle.get("timeline", []):
            timestamp = int(event.get("timestamp", 0))
            products = event.get("products", {})
            spot_state = products.get(UNDERLYING)
            if spot_state is None:
                continue
            spot = float(spot_state.get("mid_price", 0.0))
            tte = time_to_expiry_years(day_run.day, timestamp)

            points: List[Tuple[float, float]] = []
            option_snapshots: List[Dict[str, Any]] = []
            iv_map: Dict[int, float] = {}

            for strike in ALL_STRIKES:
                symbol = voucher_symbol(strike)
                state = products.get(symbol)
                if state is None:
                    continue
                best_bid, bid_vol, best_ask, ask_vol = best_bid_ask(state)
                if best_bid is None or best_ask is None:
                    continue
                mid = float(state.get("mid_price", 0.0))
                intrinsic = max(spot - strike, 0.0)
                extrinsic = mid - intrinsic
                if extrinsic < 1.5:
                    continue
                iv = implied_volatility(spot, float(strike), tte, mid)
                if iv is None:
                    continue
                moneyness = math.log(strike / spot) / math.sqrt(tte)
                option_snapshots.append(
                    {
                        "symbol": symbol,
                        "strike": strike,
                        "mid": mid,
                        "best_bid": best_bid,
                        "best_ask": best_ask,
                        "bid_vol": bid_vol,
                        "ask_vol": ask_vol,
                        "observed_iv": iv,
                        "moneyness": moneyness,
                    }
                )
                iv_map[strike] = iv
                points.append((moneyness, iv))

            smile = fit_smile(points)
            if smile is None:
                continue

            for snap in option_snapshots:
                strike = int(snap["strike"])
                fit_iv = fair_iv(smile, float(snap["moneyness"]))
                curve_price = bs_call_price(spot, float(strike), tte, fit_iv)
                curve_resid = float(snap["mid"]) - curve_price
                spread = max(1.0, float(snap["best_ask"] - snap["best_bid"]))

                neighbor_values = []
                if strike in ALL_STRIKES:
                    idx = ALL_STRIKES.index(strike)
                    if idx > 0 and ALL_STRIKES[idx - 1] in iv_map:
                        neighbor_values.append(iv_map[ALL_STRIKES[idx - 1]])
                    if idx + 1 < len(ALL_STRIKES) and ALL_STRIKES[idx + 1] in iv_map:
                        neighbor_values.append(iv_map[ALL_STRIKES[idx + 1]])
                neighbor_iv_resid = snap["observed_iv"] - (sum(neighbor_values) / len(neighbor_values)) if neighbor_values else 0.0
                book_component = imbalance(int(snap["bid_vol"]), int(snap["ask_vol"]))
                micro_component = (microprice(snap["best_bid"], int(snap["bid_vol"]), snap["best_ask"], int(snap["ask_vol"])) or snap["mid"]) - snap["mid"]

                rows.append(
                    {
                        "day": day_run.day,
                        "timestamp": timestamp,
                        "symbol": snap["symbol"],
                        "strike": strike,
                        "spot_mid": spot,
                        "tte_years": tte,
                        "observed_iv": snap["observed_iv"],
                        "fair_iv": fit_iv,
                        "curve_price": curve_price,
                        "curve_resid": curve_resid,
                        "curve_resid_norm": curve_resid / spread,
                        "neighbor_iv_resid": neighbor_iv_resid,
                        "book_component": book_component,
                        "micro_component": micro_component,
                        "smile_a": smile[0],
                        "smile_b": smile[1],
                        "smile_c": smile[2],
                    }
                )

    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame["alpha_strength"] = scale_series(
        frame["book_component"].fillna(0.0)
        + 0.8 * scale_series(frame["micro_component"].fillna(0.0))
        - 0.4 * scale_series(frame["curve_resid_norm"].fillna(0.0))
    )
    return frame.sort_values(["day", "timestamp", "symbol"]).reset_index(drop=True)


@st.cache_data(show_spinner=False)
def build_total_pnl_frame(bundle_dir_str: str) -> pd.DataFrame:
    bundle_dir = Path(bundle_dir_str)
    day_runs = load_day_runs(bundle_dir)
    rows: List[Dict[str, Any]] = []
    offset = 0
    for day_run in day_runs:
        for event in day_run.bundle.get("timeline", []):
            ts = int(event.get("timestamp", 0))
            rows.append(
                {
                    "day": day_run.day,
                    "timestamp": ts,
                    "ts_cont": ts + offset,
                    "pnl_total": float(event.get("pnl_total", 0.0)),
                }
            )
        if rows:
            offset = int(rows[-1]["ts_cont"]) + 100
    return pd.DataFrame(rows)


@st.cache_data(show_spinner=False)
def build_chain_snapshot(bundle_dir_str: str, day: int, timestamp: int) -> pd.DataFrame:
    meta = build_voucher_meta(bundle_dir_str)
    sub = meta[(meta["day"] == day) & (meta["timestamp"] == timestamp)].copy()
    if sub.empty:
        return sub

    bundle_dir = Path(bundle_dir_str)
    day_runs = load_day_runs(bundle_dir)
    target_event = None
    for day_run in day_runs:
        if day_run.day != day:
            continue
        for event in day_run.bundle.get("timeline", []):
            if int(event.get("timestamp", 0)) == timestamp:
                target_event = event
                break
        if target_event is not None:
            break

    positions = target_event.get("position", {}) if target_event is not None else {}
    spot = float(sub["spot_mid"].iloc[0])
    tte = float(sub["tte_years"].iloc[0])
    sub["position"] = sub["symbol"].map(lambda symbol: int(positions.get(symbol, 0)))
    greeks = sub.apply(
        lambda row: bs_delta_vega(spot, float(row["strike"]), tte, float(row["fair_iv"])),
        axis=1,
    )
    sub["delta"] = [item[0] for item in greeks]
    sub["vega"] = [item[1] for item in greeks]
    sub["delta_exposure"] = sub["position"] * sub["delta"]
    sub["vega_exposure"] = sub["position"] * sub["vega"]
    return sub.sort_values("strike").reset_index(drop=True)


def total_pnl_chart(frame: pd.DataFrame) -> go.Figure:
    fig = go.Figure(
        go.Scatter(
            x=frame["ts_cont"],
            y=frame["pnl_total"],
            line=dict(color="#00bfff", width=2),
            fill="tozeroy",
            fillcolor="rgba(0,191,255,0.12)",
            name="Total PnL",
        )
    )
    fig.update_layout(
        title="Continuous Total PnL",
        template="plotly_dark",
        height=280,
        margin=dict(l=20, r=20, t=40, b=20),
        showlegend=False,
    )
    fig.update_xaxes(title_text="Continuous Timestamp")
    fig.update_yaxes(title_text="PnL")
    return fig


def current_position_bar(chain: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=chain["strike"],
            y=chain["position"],
            marker_color="#ff66cc",
            name="Position",
        )
    )
    fig.update_layout(
        title="Current Position By Strike",
        template="plotly_dark",
        height=280,
        margin=dict(l=20, r=20, t=40, b=20),
        showlegend=False,
    )
    fig.update_xaxes(title_text="Strike")
    fig.update_yaxes(title_text="Contracts")
    return fig


def current_signal_bar(chain: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Bar(x=chain["strike"], y=chain["alpha_strength"], name="Alpha Strength", marker_color="#ffd32a"))
    fig.add_trace(go.Bar(x=chain["strike"], y=chain["curve_resid_norm"], name="Curve Resid Norm", marker_color="#0fbcf9"))
    fig.add_trace(go.Bar(x=chain["strike"], y=chain["neighbor_iv_resid"], name="Neighbor IV Resid", marker_color="#2ed573"))
    fig.update_layout(
        title="Current Signal Components By Strike",
        template="plotly_dark",
        height=320,
        margin=dict(l=20, r=20, t=40, b=20),
        barmode="group",
    )
    fig.update_xaxes(title_text="Strike")
    fig.update_yaxes(title_text="Signal")
    return fig


def atm_cluster_table(chain: pd.DataFrame) -> pd.DataFrame:
    if chain.empty:
        return chain
    spot = float(chain["spot_mid"].iloc[0])
    ordered = chain.assign(dist=(chain["strike"] - spot).abs()).sort_values(["dist", "strike"]).head(4)
    return ordered[
        [
            "strike",
            "observed_iv",
            "fair_iv",
            "curve_resid",
            "alpha_strength",
            "position",
            "delta_exposure",
            "vega_exposure",
        ]
    ].reset_index(drop=True)


def exposure_summary(chain: pd.DataFrame, current_product_frame: pd.DataFrame) -> Dict[str, float]:
    option_delta = float(chain["delta_exposure"].sum()) if not chain.empty else 0.0
    option_vega = float(chain["vega_exposure"].sum()) if not chain.empty else 0.0
    underlying_position = 0.0
    if current_product_frame is not None and UNDERLYING in str(current_product_frame.iloc[0:1].to_dict()):
        pass
    return {
        "net_option_delta": option_delta,
        "net_option_vega": option_vega,
    }


def price_position_chart(frame: pd.DataFrame, product: str) -> go.Figure:
    fig = make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.04,
        row_heights=[0.48, 0.24, 0.28],
        specs=[[{"secondary_y": True}], [{"secondary_y": False}], [{"secondary_y": False}]],
    )

    color = PRODUCT_COLORS.get(product, "#cccccc")
    fig.add_trace(
        go.Scatter(x=frame["ts_cont"], y=frame["mid_price"], name="Mid", line=dict(color=color, width=1.5)),
        row=1,
        col=1,
        secondary_y=False,
    )

    if "curve_price" in frame.columns and frame["curve_price"].notna().any():
        fig.add_trace(
            go.Scatter(
                x=frame["ts_cont"],
                y=frame["curve_price"],
                name="Fair Curve",
                line=dict(color="#ffffff", width=1.2, dash="dot"),
            ),
            row=1,
            col=1,
            secondary_y=False,
        )

    buys = frame[frame["buy_qty"] > 0]
    sells = frame[frame["sell_qty"] > 0]
    if not buys.empty:
        fig.add_trace(
            go.Scatter(
                x=buys["ts_cont"],
                y=buys["mid_price"],
                mode="markers",
                name="Buy Fill",
                marker=dict(color="lime", size=7, symbol="triangle-up"),
            ),
            row=1,
            col=1,
            secondary_y=False,
        )
    if not sells.empty:
        fig.add_trace(
            go.Scatter(
                x=sells["ts_cont"],
                y=sells["mid_price"],
                mode="markers",
                name="Sell Fill",
                marker=dict(color="red", size=7, symbol="triangle-down"),
            ),
            row=1,
            col=1,
            secondary_y=False,
        )

    fig.add_trace(
        go.Scatter(
            x=frame["ts_cont"],
            y=frame["position"],
            name="Position",
            line=dict(color="#ff66cc", width=1.2),
        ),
        row=1,
        col=1,
        secondary_y=True,
    )

    fig.add_trace(
        go.Bar(
            x=frame["ts_cont"],
            y=frame["net_order_qty"],
            name="Net Order Qty",
            marker_color="#7289da",
        ),
        row=2,
        col=1,
    )
    fig.add_trace(
        go.Bar(
            x=frame["ts_cont"],
            y=frame["net_trade_qty"],
            name="Net Fill Qty",
            marker_color="#2ed573",
        ),
        row=2,
        col=1,
    )

    fig.add_trace(
        go.Scatter(
            x=frame["ts_cont"],
            y=frame["alpha_strength"],
            name="Alpha Strength",
            line=dict(color="#ffd32a", width=1.5),
        ),
        row=3,
        col=1,
    )

    if "curve_resid" in frame.columns and frame["curve_resid"].notna().any():
        fig.add_trace(
            go.Scatter(
                x=frame["ts_cont"],
                y=frame["curve_resid"],
                name="Curve Resid",
                line=dict(color="#0fbcf9", width=1.0),
            ),
            row=3,
            col=1,
        )

    fig.update_layout(
        template="plotly_dark",
        height=760,
        margin=dict(l=20, r=20, t=50, b=20),
        barmode="relative",
        legend=dict(orientation="h", y=-0.08),
        title=f"Price / Position / Alpha Diagnostic - {product}",
    )
    fig.update_yaxes(title_text="Price", row=1, col=1, secondary_y=False)
    fig.update_yaxes(title_text="Position", row=1, col=1, secondary_y=True)
    fig.update_yaxes(title_text="Orders / Fills", row=2, col=1)
    fig.update_yaxes(title_text="Strength", row=3, col=1)
    fig.update_xaxes(title_text="Continuous Timestamp", row=3, col=1)
    return fig


def alpha_breakdown_chart(frame: pd.DataFrame, product: str) -> Optional[go.Figure]:
    columns = []
    if "imbalance" in frame.columns:
        columns.append(("Imbalance", frame["imbalance"]))
    if "micro_edge" in frame.columns:
        columns.append(("Micro Edge", frame["micro_edge"].fillna(0.0)))
    if "curve_resid_norm" in frame.columns and frame["curve_resid_norm"].notna().any():
        columns.append(("Curve Resid Norm", frame["curve_resid_norm"].fillna(0.0)))
    if "neighbor_iv_resid" in frame.columns and frame["neighbor_iv_resid"].notna().any():
        columns.append(("Neighbor IV Resid", frame["neighbor_iv_resid"].fillna(0.0)))
    if not columns:
        return None

    fig = go.Figure()
    for name, values in columns:
        fig.add_trace(go.Scatter(x=frame["ts_cont"], y=scale_series(values), name=name, line=dict(width=1.2)))

    fig.update_layout(
        title=f"Standardized Alpha Components - {product}",
        template="plotly_dark",
        height=320,
        margin=dict(l=20, r=20, t=40, b=20),
        legend=dict(orientation="h", y=-0.2),
    )
    fig.update_xaxes(title_text="Continuous Timestamp")
    fig.update_yaxes(title_text="Z-Score")
    return fig


def smile_snapshot_chart(bundle_dir_str: str, day: int, timestamp: int) -> Optional[go.Figure]:
    meta = build_voucher_meta(bundle_dir_str)
    sub = meta[(meta["day"] == day) & (meta["timestamp"] == timestamp)].copy()
    if sub.empty:
        return None

    sub = sub.sort_values("strike")
    spot = float(sub["spot_mid"].iloc[0])
    tte = float(sub["tte_years"].iloc[0])
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=sub["strike"],
            y=sub["observed_iv"],
            mode="markers+lines",
            name="Observed IV",
            line=dict(color="#00bfff"),
        )
    )
    fig.add_trace(
        go.Scatter(
            x=sub["strike"],
            y=sub["fair_iv"],
            mode="lines",
            name="Fitted IV Curve",
            line=dict(color="#ffffff", dash="dot"),
        )
    )
    fig.update_layout(
        title=f"Smile Snapshot - day {day}, ts {timestamp}, spot {spot:.1f}, TTE {tte * 365:.2f}d",
        template="plotly_dark",
        height=340,
        margin=dict(l=20, r=20, t=45, b=20),
    )
    fig.update_xaxes(title_text="Strike")
    fig.update_yaxes(title_text="Implied Vol")
    return fig


def product_table(frame: pd.DataFrame) -> pd.DataFrame:
    cols = [
        "day",
        "timestamp",
        "mid_price",
        "position",
        "net_order_qty",
        "net_trade_qty",
        "alpha_strength",
        "pnl_product",
    ]
    extras = ["curve_price", "curve_resid", "neighbor_iv_resid"]
    for extra in extras:
        if extra in frame.columns and frame[extra].notna().any():
            cols.append(extra)
    out = frame[cols].copy()
    return out.tail(250).reset_index(drop=True)


def summary_cards(day_runs: List[DayRun]) -> None:
    total_pnl = sum(float(run.metrics.get("final_pnl_total", 0.0)) for run in day_runs)
    total_trades = sum(int(run.metrics.get("own_trade_count", 0)) for run in day_runs)
    total_ticks = sum(int(run.metrics.get("tick_count", 0)) for run in day_runs)
    worst_day = min(float(run.metrics.get("final_pnl_total", 0.0)) for run in day_runs)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total PnL", f"{total_pnl:+,.1f}")
    c2.metric("Own Trades", f"{total_trades:,}")
    c3.metric("Ticks", f"{total_ticks:,}")
    c4.metric("Worst Day", f"{worst_day:+,.1f}")


@st.cache_data(show_spinner=False)
def load_event(bundle_dir_str: str, day: int, timestamp: int) -> Dict[str, Any]:
    bundle_dir = Path(bundle_dir_str)
    for day_run in load_day_runs(bundle_dir):
        if day_run.day != day:
            continue
        for event in day_run.bundle.get("timeline", []):
            if int(event.get("timestamp", 0)) == timestamp:
                return event
    return {}


st.set_page_config(page_title="Enhanced Round 3 Dashboard", layout="wide")
st.title("Enhanced Prosperity Round 3 Dashboard")

root = repo_root()
st.sidebar.header("Data Source")
source_root = Path(
    st.sidebar.text_input("Repo root", value=str(root))
).expanduser()

st.sidebar.header("Run New Backtest")
available_traders = list_traders(source_root)
available_datasets = list_datasets(source_root)
selected_trader_file = st.sidebar.selectbox("Python trader file", available_traders, index=0 if available_traders else None)
selected_dataset_name = st.sidebar.selectbox("Dataset", available_datasets, index=0 if available_datasets else None)
carry_positions = st.sidebar.checkbox("Carry positions across days", value=False)
if st.sidebar.button("Run Backtest", type="primary", disabled=not (available_traders and available_datasets)):
    with st.spinner(f"Running {selected_trader_file} on {selected_dataset_name}..."):
        output = run_backtest(source_root, selected_trader_file, selected_dataset_name, carry_positions)
    st.sidebar.code(output, language="text")
    time.sleep(0.5)
    st.rerun()

runs = discover_runs(source_root)
if not runs:
    st.error(f"No persisted manifest runs found under {runs_dir(source_root)}")
    st.stop()

st.sidebar.header("Inspect Existing Run")
run_ids = [run["id"] for run in runs]
selected_run_id = st.sidebar.selectbox("Run", run_ids)
selected_run = next(run for run in runs if run["id"] == selected_run_id)
day_runs = load_day_runs(selected_run["path"])

if not day_runs:
    st.error("Selected run has no child day folders with bundle.json")
    st.stop()

summary_cards(day_runs)

with st.expander("Per-day summary", expanded=False):
    day_summary = []
    for run in day_runs:
        row = {
            "day": run.day,
            "pnl": run.metrics.get("final_pnl_total", 0.0),
            "trades": run.metrics.get("own_trade_count", 0),
        }
        row.update(run.metrics.get("final_pnl_by_product", {}))
        day_summary.append(row)
    st.dataframe(pd.DataFrame(day_summary), use_container_width=True)

total_frame = build_total_pnl_frame(str(selected_run["path"]))
if not total_frame.empty:
    st.plotly_chart(total_pnl_chart(total_frame), use_container_width=True)

product_options = [UNDERLYING, HYDROGEL] + [voucher_symbol(strike) for strike in ALL_STRIKES]
default_index = product_options.index("VEV_5200") if "VEV_5200" in product_options else 0
selected_product = st.sidebar.selectbox("Product", product_options, index=default_index)
frame = build_product_frame(str(selected_run["path"]), selected_product)

if frame.empty:
    st.warning("No data found for the selected product in this run.")
    st.stop()

if "focus_idx" not in st.session_state or st.session_state.get("focus_product") != selected_product:
    st.session_state["focus_idx"] = min(200, len(frame) - 1)
    st.session_state["playing"] = False
    st.session_state["focus_product"] = selected_product

play_col, pause_col = st.sidebar.columns(2)
if play_col.button("Play"):
    st.session_state["playing"] = True
if pause_col.button("Pause"):
    st.session_state["playing"] = False

selected_idx = st.sidebar.slider(
    "Playback index",
    min_value=0,
    max_value=len(frame) - 1,
    value=min(int(st.session_state.get("focus_idx", 0)), len(frame) - 1),
)
st.session_state["focus_idx"] = selected_idx

step_size = st.sidebar.selectbox("Playback step", [1, 2, 5, 10], index=2)
focus_row = frame.iloc[selected_idx]
current_event = load_event(str(selected_run["path"]), int(focus_row["day"]), int(focus_row["timestamp"]))
chain = build_chain_snapshot(str(selected_run["path"]), int(focus_row["day"]), int(focus_row["timestamp"]))

underlying_pos = int(current_event.get("position", {}).get(UNDERLYING, 0))
hydrogel_pos = int(current_event.get("position", {}).get(HYDROGEL, 0))
net_option_delta = float(chain["delta_exposure"].sum()) if not chain.empty else 0.0
net_option_vega = float(chain["vega_exposure"].sum()) if not chain.empty else 0.0
total_delta_with_underlying = net_option_delta + underlying_pos

exp1, exp2, exp3, exp4 = st.columns(4)
exp1.metric("Current Option Delta", f"{net_option_delta:+,.2f}")
exp2.metric("Current Total Delta", f"{total_delta_with_underlying:+,.2f}")
exp3.metric("Current Option Vega", f"{net_option_vega:+,.2f}")
exp4.metric("Underlying / Hydrogel Pos", f"{underlying_pos:+d} / {hydrogel_pos:+d}")

st.subheader(f"Product Diagnostics - {selected_product}")
st.plotly_chart(price_position_chart(frame, selected_product), use_container_width=True)

alpha_fig = alpha_breakdown_chart(frame, selected_product)
if alpha_fig is not None:
    st.plotly_chart(alpha_fig, use_container_width=True)

left, right = st.columns([1.2, 1])
with left:
    st.markdown("**Focused tick**")
    focus_payload = {
        "day": int(focus_row["day"]),
        "timestamp": int(focus_row["timestamp"]),
        "mid_price": float(focus_row["mid_price"]),
        "position": int(focus_row["position"]),
        "alpha_strength": float(focus_row["alpha_strength"]),
        "net_order_qty": int(focus_row["net_order_qty"]),
        "net_trade_qty": int(focus_row["net_trade_qty"]),
        "pnl_product": float(focus_row["pnl_product"]),
    }
    if "curve_price" in frame.columns and pd.notna(focus_row.get("curve_price")):
        focus_payload["curve_price"] = float(focus_row["curve_price"])
        focus_payload["curve_resid"] = float(focus_row["curve_resid"])
    st.json(focus_payload)

    st.markdown("**Recent rows**")
    st.dataframe(product_table(frame), use_container_width=True, height=320)

with right:
    smile_fig = smile_snapshot_chart(str(selected_run["path"]), int(focus_row["day"]), int(focus_row["timestamp"]))
    if smile_fig is not None:
        st.plotly_chart(smile_fig, use_container_width=True)
    else:
        st.info("Smile snapshot is only available for voucher products and timestamps with a valid fit.")
    st.caption("Round 3 has voucher calls only. There is no put chain in this dataset, so the curve panel is call-only.")

row1, row2 = st.columns(2)
with row1:
    if not chain.empty:
        st.plotly_chart(current_position_bar(chain), use_container_width=True)
with row2:
    if not chain.empty:
        st.plotly_chart(current_signal_bar(chain), use_container_width=True)

st.subheader("ATM Cluster")
if not chain.empty:
    st.dataframe(atm_cluster_table(chain), use_container_width=True)
else:
    st.info("ATM cluster is available when a valid option-chain snapshot exists.")

st.subheader("Whole Chain Snapshot")
if not chain.empty:
    chain_display = chain[
        [
            "strike",
            "position",
            "observed_iv",
            "fair_iv",
            "curve_resid",
            "curve_resid_norm",
            "neighbor_iv_resid",
            "alpha_strength",
            "delta_exposure",
            "vega_exposure",
        ]
    ].copy()
    st.dataframe(chain_display, use_container_width=True, height=320)
else:
    st.info("No valid chain snapshot for this focused tick.")

st.markdown("---")
st.subheader("How To Read It")
st.markdown(
    """
    - Use the sidebar to pick any trader `.py` file in `Prosperity4/traders` and launch a backtest directly.
    - `Play` / `Pause` moves the focused tick so the whole dashboard updates like a replay.
    - The top PnL chart tracks total strategy performance continuously across the run.
    - `Fair Curve` is recomputed from the live cross-sectional smile at each tick, so you can see whether the trader is leaning into or against the local option surface.
    - `Position` lets you spot when we keep adding into a bad move instead of flattening.
    - `Net Order Qty` vs `Net Fill Qty` helps separate intent from executions.
    - `Alpha Strength` is a compact diagnostic score. For delta-1 products it is driven by book imbalance and microprice edge; for vouchers it blends those with normalized curve mispricing.
    - `ATM Cluster` shows the four strikes closest to spot at the current tick, which is usually the most important local region of the smile.
    """
)

if st.session_state.get("playing"):
    next_idx = min(len(frame) - 1, selected_idx + int(step_size))
    if next_idx == selected_idx:
        st.session_state["playing"] = False
    else:
        st.session_state["focus_idx"] = next_idx
        time.sleep(0.35)
        st.rerun()
