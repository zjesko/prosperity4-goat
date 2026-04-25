import json
import math
from statistics import NormalDist
from typing import Dict, List, Optional, Sequence, Tuple

from datamodel import Order, OrderDepth, TradingState


_N = NormalDist()


class Trader:
    UNDERLYING = "VELVETFRUIT_EXTRACT"
    HYDROGEL = "HYDROGEL_PACK"
    ALL_STRIKES = (4000, 4500, 5000, 5100, 5200, 5300, 5400, 5500, 6000, 6500)
    FIT_STRIKES = (4000, 4500, 5000, 5100, 5200, 5300, 5400, 5500, 6000, 6500)

    TTE_DAYS_AT_ROUND_START = 5
    DAYS_PER_YEAR = 365.0

    DELTA1_IMB_COEF = 0.6
    DELTA1_MICRO_COEF = 0.9
    DELTA1_INV_COEF = 2.5
    DELTA1_TAKE_EDGE = 0.0
    DELTA1_BASE_SIZE = 25

    OPTION_IMB_COEF = 1.4
    OPTION_MICRO_COEF = 1.2
    OPTION_CURVE_COEF = 0.0
    OPTION_INV_COEF = 0.8
    OPTION_TAKE_EDGE = 1.2
    OPTION_BASE_SIZE = 20
    MIN_EXTRINSIC_VALUE = 1.5
    MIN_POINTS_TO_FIT = 4

    LIMITS: Dict[str, int] = {
        UNDERLYING: 200,
        HYDROGEL: 200,
        **{f"VEV_{strike}": 300 for strike in ALL_STRIKES},
    }

    @staticmethod
    def voucher(strike: int) -> str:
        return f"VEV_{strike}"

    def run(self, state: TradingState):
        orders: Dict[str, List[Order]] = {}
        trader_state = self._load_state(state.traderData)

        for product in (self.UNDERLYING, self.HYDROGEL):
            depth = state.order_depths.get(product)
            if depth is None:
                continue
            product_orders = self._quote_delta1(product, depth, state.position.get(product, 0))
            if product_orders:
                orders[product] = product_orders

        spot_depth = state.order_depths.get(self.UNDERLYING)
        spot = self._wall_mid(spot_depth) if spot_depth is not None else None
        tte = self._time_to_expiry_years(state.timestamp)
        if spot is not None and tte > 0:
            option_orders = self._quote_options(state, spot, tte, trader_state)
            orders.update(option_orders)

        return orders, 0, json.dumps(trader_state)

    def _quote_delta1(self, product: str, depth: OrderDepth, position: int) -> List[Order]:
        best_bid = max(depth.buy_orders) if depth.buy_orders else None
        best_ask = min(depth.sell_orders) if depth.sell_orders else None
        if best_bid is None or best_ask is None:
            return []

        mid = (best_bid + best_ask) / 2.0
        bid_vol = abs(depth.buy_orders.get(best_bid, 0))
        ask_vol = abs(depth.sell_orders.get(best_ask, 0))
        imbalance = self._imbalance(bid_vol, ask_vol)
        micro_edge = self._microprice(best_bid, bid_vol, best_ask, ask_vol) - mid
        limit = self.LIMITS[product]
        fair = (
            mid
            + self.DELTA1_IMB_COEF * imbalance
            + self.DELTA1_MICRO_COEF * micro_edge
            - self.DELTA1_INV_COEF * position / max(1, limit)
        )

        return self._build_mm_orders(
            product=product,
            best_bid=best_bid,
            best_ask=best_ask,
            fair=fair,
            position=position,
            limit=limit,
            take_edge=self.DELTA1_TAKE_EDGE,
            base_size=self.DELTA1_BASE_SIZE,
        )

    def _quote_options(
        self,
        state: TradingState,
        spot: float,
        tte: float,
        trader_state: Dict[str, object],
    ) -> Dict[str, List[Order]]:
        snapshots = self._collect_option_snapshots(state, spot, tte)
        if not snapshots:
            return {}

        fit_points = [
            (snap["moneyness"], snap["observed_iv"])
            for snap in snapshots
            if snap["observed_iv"] is not None
        ]
        smile = self._fit_smile(fit_points) if len(fit_points) >= self.MIN_POINTS_TO_FIT else None

        orders: Dict[str, List[Order]] = {}
        for snapshot in snapshots:
            symbol = snapshot["symbol"]
            best_bid = int(snapshot["best_bid"])
            best_ask = int(snapshot["best_ask"])
            mid = float(snapshot["mid"])
            position = state.position.get(symbol, 0)
            limit = self.LIMITS[symbol]
            spread = max(1.0, float(snapshot["spread"]))
            curve_resid = 0.0
            normalized_curve = 0.0

            if smile is not None and snapshot["moneyness"] is not None:
                fit_iv = self._fair_iv(smile, float(snapshot["moneyness"]))
                theo = self._bs_call_price(spot, float(snapshot["strike"]), tte, fit_iv)
                curve_resid = mid - theo
                normalized_curve = curve_resid / spread

            fair = (
                mid
                + self.OPTION_IMB_COEF * float(snapshot["imbalance"])
                + self.OPTION_MICRO_COEF * float(snapshot["micro_edge"])
                - self.OPTION_CURVE_COEF * normalized_curve
                - self.OPTION_INV_COEF * position / max(1, limit)
            )

            option_orders = self._build_mm_orders(
                product=symbol,
                best_bid=best_bid,
                best_ask=best_ask,
                fair=fair,
                position=position,
                limit=limit,
                take_edge=self.OPTION_TAKE_EDGE,
                base_size=self.OPTION_BASE_SIZE,
            )
            if option_orders:
                orders[symbol] = option_orders
            trader_state[f"{symbol}_curve_resid"] = curve_resid

        return orders

    def _build_mm_orders(
        self,
        product: str,
        best_bid: int,
        best_ask: int,
        fair: float,
        position: int,
        limit: int,
        take_edge: float,
        base_size: int,
    ) -> List[Order]:
        orders: List[Order] = []
        projected = position

        if best_ask <= fair - take_edge:
            qty = min(base_size, limit - projected)
            if qty > 0:
                orders.append(Order(product, best_ask, qty))
                projected += qty

        if best_bid >= fair + take_edge:
            qty = min(base_size, limit + projected)
            if qty > 0:
                orders.append(Order(product, best_bid, -qty))
                projected -= qty

        spread = best_ask - best_bid
        if spread >= 2:
            bid_px = min(best_bid + 1, int(math.floor(fair)))
            ask_px = max(best_ask - 1, int(math.ceil(fair)))
            if bid_px < ask_px:
                buy_cap = min(base_size, limit - projected)
                sell_cap = min(base_size, limit + projected)
                if buy_cap > 0 and bid_px < best_ask:
                    orders.append(Order(product, bid_px, buy_cap))
                if sell_cap > 0 and ask_px > best_bid:
                    orders.append(Order(product, ask_px, -sell_cap))

        return orders

    def _collect_option_snapshots(
        self,
        state: TradingState,
        spot: float,
        tte: float,
    ) -> List[Dict[str, float]]:
        snapshots: List[Dict[str, float]] = []
        for strike in self.ALL_STRIKES:
            symbol = self.voucher(strike)
            depth = state.order_depths.get(symbol)
            if depth is None or not depth.buy_orders or not depth.sell_orders:
                continue

            best_bid = max(depth.buy_orders)
            best_ask = min(depth.sell_orders)
            bid_vol = abs(depth.buy_orders.get(best_bid, 0))
            ask_vol = abs(depth.sell_orders.get(best_ask, 0))
            mid = (best_bid + best_ask) / 2.0
            micro = self._microprice(best_bid, bid_vol, best_ask, ask_vol)
            intrinsic = max(spot - strike, 0.0)
            extrinsic = mid - intrinsic
            observed_iv = None
            moneyness = None
            if extrinsic >= self.MIN_EXTRINSIC_VALUE:
                observed_iv = self._implied_volatility(spot, float(strike), tte, mid)
                if observed_iv is not None:
                    moneyness = math.log(strike / spot) / math.sqrt(tte)

            snapshots.append(
                {
                    "symbol": symbol,
                    "strike": float(strike),
                    "best_bid": float(best_bid),
                    "best_ask": float(best_ask),
                    "mid": mid,
                    "spread": float(best_ask - best_bid),
                    "observed_iv": observed_iv,
                    "moneyness": moneyness,
                    "imbalance": self._imbalance(bid_vol, ask_vol),
                    "micro_edge": micro - mid,
                }
            )
        return snapshots

    def _fit_smile(self, points: Sequence[Tuple[float, float]]) -> Optional[Tuple[float, float, float]]:
        if len(points) < self.MIN_POINTS_TO_FIT:
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
        coeffs = self._solve_linear_system(xtx, xty)
        if coeffs is None:
            return None
        return coeffs[0], coeffs[1], coeffs[2]

    def _fair_iv(self, smile: Tuple[float, float, float], moneyness: float) -> float:
        a, b, c = smile
        return max(0.05, min(1.5, a * moneyness * moneyness + b * moneyness + c))

    def _solve_linear_system(
        self,
        matrix: Sequence[Sequence[float]],
        vector: Sequence[float],
    ) -> Optional[Tuple[float, float, float]]:
        augmented = [list(row) + [value] for row, value in zip(matrix, vector)]
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
        return tuple(augmented[row][size] for row in range(size))

    def _implied_volatility(
        self,
        spot: float,
        strike: float,
        tte: float,
        option_price: float,
    ) -> Optional[float]:
        intrinsic = max(spot - strike, 0.0)
        if option_price <= intrinsic:
            return None
        low = 1e-4
        high = 3.0
        if option_price < self._bs_call_price(spot, strike, tte, low):
            return None
        if option_price > self._bs_call_price(spot, strike, tte, high):
            return None
        for _ in range(60):
            mid = (low + high) / 2.0
            mid_price = self._bs_call_price(spot, strike, tte, mid)
            if mid_price < option_price:
                low = mid
            else:
                high = mid
        return (low + high) / 2.0

    def _bs_call_price(self, spot: float, strike: float, tte: float, sigma: float) -> float:
        if tte <= 0 or sigma <= 0:
            return max(spot - strike, 0.0)
        root_t = math.sqrt(tte)
        d1 = (math.log(spot / strike) + 0.5 * sigma * sigma * tte) / (sigma * root_t)
        d2 = d1 - sigma * root_t
        return spot * _N.cdf(d1) - strike * _N.cdf(d2)

    def _time_to_expiry_years(self, timestamp: int) -> float:
        return max(0.0, (self.TTE_DAYS_AT_ROUND_START - timestamp / 1_000_000.0) / self.DAYS_PER_YEAR)

    def _wall_mid(self, depth: Optional[OrderDepth]) -> Optional[float]:
        if depth is None or not depth.buy_orders or not depth.sell_orders:
            return None
        best_bid = max(depth.buy_orders)
        best_ask = min(depth.sell_orders)
        return (best_bid + best_ask) / 2.0

    def _microprice(self, best_bid: int, bid_vol: int, best_ask: int, ask_vol: int) -> float:
        total = bid_vol + ask_vol
        if total <= 0:
            return (best_bid + best_ask) / 2.0
        return (best_bid * ask_vol + best_ask * bid_vol) / total

    def _imbalance(self, bid_vol: int, ask_vol: int) -> float:
        total = bid_vol + ask_vol
        if total <= 0:
            return 0.0
        return (bid_vol - ask_vol) / total

    def _load_state(self, raw: str) -> Dict[str, object]:
        if not raw:
            return {}
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
