"""
Round 3 basic trader.

Strategy:
  - VEV vouchers (strikes 5000-5500): IV scalping via a hardcoded global vol
    smile parabola fitted in research/round3.ipynb. Go max short if the
    wall-mid is above the BS theoretical price by THR_OPEN, max long if below
    by THR_OPEN, flatten within THR_CLOSE. Non-viable strikes (deep ITM
    4000/4500, dead OTM 6000/6500) are not traded.

  - VELVETFRUIT_EXTRACT and HYDROGEL_PACK: take any orders that cross
    wall-mid, plus a one-tick-inside-the-walls passive quote when the book
    spread allows positive edge.
"""

from datamodel import OrderDepth, TradingState, Order
from typing import Dict, List
import math
from statistics import NormalDist

_N = NormalDist()


UNDERLYING = "VELVETFRUIT_EXTRACT"
HYDROGEL = "HYDROGEL_PACK"

ALL_STRIKES = [4000, 4500, 5000, 5100, 5200, 5300, 5400, 5500, 6000, 6500]
VIABLE_STRIKES = [5000, 5100, 5200, 5300, 5400, 5500]


def voucher(k: int) -> str:
    return f"VEV_{k}"


POS_LIMITS: Dict[str, int] = {
    UNDERLYING: 200,
    HYDROGEL: 200,
    **{voucher(k): 300 for k in ALL_STRIKES},
}

# Global vol-smile parabola iv_hat(m) = a*m^2 + b*m + c
# where m = ln(K/S) / sqrt(TTE). Fitted on 3 days of historical data across
# the 6 viable strikes in research/round3.ipynb.
SMILE_A, SMILE_B, SMILE_C = 0.03037029, 0.00224891, 0.23941452

# Round 3 live starts at TTE = 5 days. Update this for later rounds.
TTE_DAYS_AT_ROUND_START = 5
DAYS_PER_YEAR = 365

# IV-scalping thresholds in XIRECS (price space, after BS transform).
THR_OPEN = 0.5   # open position when |mid - theo| exceeds this
THR_CLOSE = 0.0  # close position when signal crosses back through this


def bs_call(S: float, K: float, T: float, sigma: float):
    if T <= 0 or sigma <= 0:
        return max(S - K, 0.0), (1.0 if S > K else 0.0)
    d1 = (math.log(S / K) + 0.5 * sigma * sigma * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return S * _N.cdf(d1) - K * _N.cdf(d2), _N.cdf(d1)


def wall_levels(od: OrderDepth):
    """Returns (bid_wall_price, ask_wall_price) — the price levels with the
    largest volume on each side. None if that side is empty."""
    bid = max(od.buy_orders, key=od.buy_orders.get) if od.buy_orders else None
    # sell volumes are stored as negative ints; min() picks the most-negative
    ask = min(od.sell_orders, key=od.sell_orders.get) if od.sell_orders else None
    return bid, ask


def wall_mid(od: OrderDepth):
    bid, ask = wall_levels(od)
    if bid is None or ask is None:
        return None, None, None
    return (bid + ask) / 2.0, bid, ask


class Trader:

    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {}

        # ---------- Vouchers: IV scalping ----------
        und_od = state.order_depths.get(UNDERLYING)
        S = wall_mid(und_od)[0] if und_od is not None else None
        tte = (TTE_DAYS_AT_ROUND_START - state.timestamp / 1_000_000) / DAYS_PER_YEAR

        if S is not None and tte > 0:
            for K in VIABLE_STRIKES:
                sym = voucher(K)
                od = state.order_depths.get(sym)
                if od is None or not od.buy_orders or not od.sell_orders:
                    continue

                opt_mid, _, _ = wall_mid(od)
                if opt_mid is None:
                    continue

                m = math.log(K / S) / math.sqrt(tte)
                iv_hat = SMILE_A * m * m + SMILE_B * m + SMILE_C
                if iv_hat <= 0:
                    continue

                theo, _ = bs_call(S, K, tte, iv_hat)
                dev = opt_mid - theo

                pos = state.position.get(sym, 0)
                limit = POS_LIMITS[sym]
                best_bid = max(od.buy_orders)
                best_ask = min(od.sell_orders)

                if dev > THR_OPEN:
                    target = -limit
                elif dev < -THR_OPEN:
                    target = limit
                elif dev > THR_CLOSE and pos > 0:
                    target = 0
                elif dev < -THR_CLOSE and pos < 0:
                    target = 0
                else:
                    target = pos

                delta_q = target - pos
                if delta_q > 0:
                    result[sym] = [Order(sym, best_ask, delta_q)]
                elif delta_q < 0:
                    result[sym] = [Order(sym, best_bid, delta_q)]

        # ---------- Delta-1 products: take + passive MM ----------
        for prod in (UNDERLYING, HYDROGEL):
            od = state.order_depths.get(prod)
            if od is None or not od.buy_orders or not od.sell_orders:
                continue

            mid, bid_wall, ask_wall = wall_mid(od)
            if mid is None:
                continue

            pos = state.position.get(prod, 0)
            limit = POS_LIMITS[prod]
            best_bid = max(od.buy_orders)
            best_ask = min(od.sell_orders)

            orders: List[Order] = []
            proj_pos = pos

            # take anything that crosses fair
            if best_ask < mid:
                avail = -od.sell_orders[best_ask]
                qty = min(avail, limit - proj_pos)
                if qty > 0:
                    orders.append(Order(prod, best_ask, qty))
                    proj_pos += qty

            if best_bid > mid:
                avail = od.buy_orders[best_bid]
                qty = min(avail, limit + proj_pos)
                if qty > 0:
                    orders.append(Order(prod, best_bid, -qty))
                    proj_pos -= qty

            # passive MM: one tick inside the walls, but only if spread >= 2
            # so our bid stays below mid and our ask stays above mid
            if ask_wall - bid_wall >= 2:
                buy_cap = limit - proj_pos
                sell_cap = limit + proj_pos
                if buy_cap > 0:
                    orders.append(Order(prod, bid_wall + 1, buy_cap))
                if sell_cap > 0:
                    orders.append(Order(prod, ask_wall - 1, -sell_cap))

            if orders:
                result[prod] = orders

        return result, 0, ""
