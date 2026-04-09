"""
Market Analyzer - Funding rate, open interest, order book depth, sentiment analysis.
Provides edge unique to futures trading.
"""

import logging
import time
from typing import Optional
from binance.client import Client
from binance.exceptions import BinanceAPIException

import config

logger = logging.getLogger(__name__)


class MarketAnalyzer:
    def __init__(self, client: Client):
        self.client = client
        self._cache = {}
        self._cache_ttl = 60  # Cache for 60 seconds

    def _get_cached(self, key: str):
        if key in self._cache:
            data, ts = self._cache[key]
            if time.time() - ts < self._cache_ttl:
                return data
        return None

    def _set_cached(self, key: str, data):
        self._cache[key] = (data, time.time())

    # =========================================================================
    # FUNDING RATE
    # =========================================================================
    def get_funding_rate(self, symbol: str) -> Optional[float]:
        """Get current funding rate. Positive = longs pay shorts, negative = shorts pay longs."""
        cache_key = f"funding_{symbol}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        try:
            info = self.client.futures_funding_rate(symbol=symbol, limit=1)
            if info:
                rate = float(info[-1]["fundingRate"])
                self._set_cached(cache_key, rate)
                return rate
        except BinanceAPIException as e:
            logger.error(f"Error fetching funding rate for {symbol}: {e}")
        return None

    def is_funding_extreme(self, symbol: str) -> dict:
        """Check if funding rate suggests overcrowded positioning."""
        rate = self.get_funding_rate(symbol)
        if rate is None:
            return {"extreme": False, "direction": None, "rate": 0}

        if rate >= config.FUNDING_RATE_EXTREME_LONG:
            return {"extreme": True, "direction": "long_crowded", "rate": rate}
        elif rate <= config.FUNDING_RATE_EXTREME_SHORT:
            return {"extreme": True, "direction": "short_crowded", "rate": rate}
        return {"extreme": False, "direction": None, "rate": rate}

    # =========================================================================
    # OPEN INTEREST
    # =========================================================================
    def get_open_interest(self, symbol: str) -> Optional[float]:
        """Get current open interest in contracts."""
        cache_key = f"oi_{symbol}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        try:
            oi = self.client.futures_open_interest(symbol=symbol)
            value = float(oi["openInterest"])
            self._set_cached(cache_key, value)
            return value
        except BinanceAPIException as e:
            logger.error(f"Error fetching OI for {symbol}: {e}")
        return None

    def get_oi_change(self, symbol: str) -> Optional[float]:
        """Get open interest change over recent periods. Returns percentage change."""
        try:
            oi_hist = self.client.futures_open_interest_hist(
                symbol=symbol, period="5m", limit=12
            )
            if len(oi_hist) < 2:
                return None
            oldest = float(oi_hist[0]["sumOpenInterest"])
            newest = float(oi_hist[-1]["sumOpenInterest"])
            if oldest == 0:
                return None
            return (newest - oldest) / oldest
        except BinanceAPIException as e:
            logger.error(f"Error fetching OI history for {symbol}: {e}")
        return None

    def analyze_open_interest(self, symbol: str) -> dict:
        """Analyze open interest for signals."""
        oi = self.get_open_interest(symbol)
        oi_change = self.get_oi_change(symbol)

        result = {
            "open_interest": oi,
            "oi_change_pct": oi_change,
            "significant": False,
            "signal": "neutral",
        }

        if oi_change is not None:
            if abs(oi_change) >= config.OI_CHANGE_THRESHOLD:
                result["significant"] = True
                if oi_change > 0:
                    result["signal"] = "increasing"  # New money entering
                else:
                    result["signal"] = "decreasing"  # Money leaving

        return result

    # =========================================================================
    # ORDER BOOK DEPTH
    # =========================================================================
    def get_order_book_imbalance(self, symbol: str, depth: int = 20) -> dict:
        """Analyze order book for buy/sell wall imbalances."""
        cache_key = f"orderbook_{symbol}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        try:
            book = self.client.futures_order_book(symbol=symbol, limit=depth)

            bid_volume = sum(float(b[1]) for b in book["bids"])
            ask_volume = sum(float(a[1]) for a in book["asks"])

            total = bid_volume + ask_volume
            if total == 0:
                return {"imbalance": 0, "bias": "neutral", "bid_vol": 0, "ask_vol": 0}

            imbalance = (bid_volume - ask_volume) / total

            # Find large walls
            bid_prices = [(float(b[0]), float(b[1])) for b in book["bids"]]
            ask_prices = [(float(a[0]), float(a[1])) for a in book["asks"]]

            avg_bid_size = bid_volume / len(bid_prices) if bid_prices else 0
            avg_ask_size = ask_volume / len(ask_prices) if ask_prices else 0

            buy_walls = [p for p, v in bid_prices if v > avg_bid_size * 3]
            sell_walls = [p for p, v in ask_prices if v > avg_ask_size * 3]

            if imbalance > 0.2:
                bias = "bullish"
            elif imbalance < -0.2:
                bias = "bearish"
            else:
                bias = "neutral"

            result = {
                "imbalance": round(imbalance, 4),
                "bias": bias,
                "bid_vol": round(bid_volume, 2),
                "ask_vol": round(ask_volume, 2),
                "buy_walls": buy_walls[:3],
                "sell_walls": sell_walls[:3],
            }
            self._set_cached(cache_key, result)
            return result

        except BinanceAPIException as e:
            logger.error(f"Error fetching order book for {symbol}: {e}")
        return {"imbalance": 0, "bias": "neutral", "bid_vol": 0, "ask_vol": 0}

    # =========================================================================
    # LONG/SHORT RATIO
    # =========================================================================
    def get_long_short_ratio(self, symbol: str) -> Optional[dict]:
        """Get top trader long/short ratio."""
        cache_key = f"ls_ratio_{symbol}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        try:
            data = self.client.futures_top_longshort_account_ratio(
                symbol=symbol, period="5m", limit=1
            )
            if data:
                entry = data[-1]
                result = {
                    "long_account": float(entry["longAccount"]),
                    "short_account": float(entry["shortAccount"]),
                    "long_short_ratio": float(entry["longShortRatio"]),
                }
                self._set_cached(cache_key, result)
                return result
        except BinanceAPIException as e:
            logger.error(f"Error fetching L/S ratio for {symbol}: {e}")
        return None

    # =========================================================================
    # COMBINED MARKET SCORE
    # =========================================================================
    def get_market_score(self, symbol: str) -> dict:
        """
        Combined market analysis score.
        Returns a score from -100 (extremely bearish) to +100 (extremely bullish).
        """
        score = 0
        details = {}

        # Funding rate analysis
        funding = self.is_funding_extreme(symbol)
        details["funding"] = funding
        if funding["extreme"]:
            if funding["direction"] == "long_crowded":
                score -= 20  # Contrarian: too many longs = bearish signal
            elif funding["direction"] == "short_crowded":
                score += 20  # Contrarian: too many shorts = bullish signal

        # Open interest
        oi = self.analyze_open_interest(symbol)
        details["open_interest"] = oi
        if oi["significant"]:
            if oi["signal"] == "increasing":
                score += 10  # New money = trend continuation
            else:
                score -= 10  # Money leaving = potential reversal

        # Order book
        orderbook = self.get_order_book_imbalance(symbol)
        details["orderbook"] = orderbook
        if orderbook["bias"] == "bullish":
            score += 15
        elif orderbook["bias"] == "bearish":
            score -= 15

        # Long/short ratio (contrarian)
        ls_ratio = self.get_long_short_ratio(symbol)
        details["long_short_ratio"] = ls_ratio
        if ls_ratio:
            ratio = ls_ratio["long_short_ratio"]
            if ratio > 2.0:
                score -= 15  # Too many longs
            elif ratio < 0.5:
                score += 15  # Too many shorts

        # Clamp score
        score = max(-100, min(100, score))

        return {
            "symbol": symbol,
            "score": score,
            "bias": "bullish" if score > 20 else "bearish" if score < -20 else "neutral",
            "details": details,
        }
