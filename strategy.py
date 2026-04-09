"""
Strategy Engine - Multi-timeframe technical analysis with confluence-based signals.
Only enters trades when 3+ indicators align.
"""

import logging
from typing import Optional

import numpy as np
import pandas as pd
import ta as ta_lib
from binance.client import Client
from binance.exceptions import BinanceAPIException

import config
from market_analyzer import MarketAnalyzer

logger = logging.getLogger(__name__)


class Signal:
    """Represents a trade signal."""
    def __init__(self, symbol: str, direction: str, confidence: float,
                 entry_price: float, stop_loss: float, take_profit: float,
                 reasons: list, timeframe_alignment: dict, market_score: int):
        self.symbol = symbol
        self.direction = direction          # "LONG" or "SHORT"
        self.confidence = confidence        # 0.0 to 1.0
        self.entry_price = entry_price
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.reasons = reasons
        self.timeframe_alignment = timeframe_alignment
        self.market_score = market_score

    def __repr__(self):
        return (f"Signal({self.symbol} {self.direction} "
                f"conf={self.confidence:.0%} entry={self.entry_price})")


class Strategy:
    def __init__(self, client: Client, market_analyzer: MarketAnalyzer):
        self.client = client
        self.market_analyzer = market_analyzer

    # =========================================================================
    # DATA FETCHING
    # =========================================================================
    def get_klines(self, symbol: str, interval: str, limit: int = 200) -> Optional[pd.DataFrame]:
        """Fetch candlestick data and return as DataFrame."""
        try:
            klines = self.client.futures_klines(
                symbol=symbol, interval=interval, limit=limit
            )
            df = pd.DataFrame(klines, columns=[
                "open_time", "open", "high", "low", "close", "volume",
                "close_time", "quote_volume", "trades", "taker_buy_base",
                "taker_buy_quote", "ignore"
            ])
            for col in ["open", "high", "low", "close", "volume", "quote_volume"]:
                df[col] = df[col].astype(float)
            df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
            df["close_time"] = pd.to_datetime(df["close_time"], unit="ms")
            return df
        except BinanceAPIException as e:
            logger.error(f"Error fetching klines for {symbol} {interval}: {e}")
            return None

    # =========================================================================
    # INDICATOR CALCULATIONS
    # =========================================================================
    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add all technical indicators to the DataFrame."""
        # EMAs
        df["ema_fast"] = ta_lib.trend.ema_indicator(df["close"], window=config.EMA_FAST)
        df["ema_slow"] = ta_lib.trend.ema_indicator(df["close"], window=config.EMA_SLOW)
        df["ema_trend"] = ta_lib.trend.ema_indicator(df["close"], window=config.EMA_TREND)

        # RSI
        df["rsi"] = ta_lib.momentum.rsi(df["close"], window=config.RSI_PERIOD)

        # MACD
        macd = ta_lib.trend.MACD(
            df["close"],
            window_fast=config.MACD_FAST,
            window_slow=config.MACD_SLOW,
            window_sign=config.MACD_SIGNAL,
        )
        df["macd"] = macd.macd()
        df["macd_signal"] = macd.macd_signal()
        df["macd_hist"] = macd.macd_diff()

        # ATR
        df["atr"] = ta_lib.volatility.average_true_range(
            df["high"], df["low"], df["close"], window=config.ATR_PERIOD
        )

        # Bollinger Bands
        bb = ta_lib.volatility.BollingerBands(
            df["close"], window=config.BB_PERIOD, window_dev=config.BB_STD
        )
        df["bb_upper"] = bb.bollinger_hband()
        df["bb_lower"] = bb.bollinger_lband()
        df["bb_mid"] = bb.bollinger_mavg()

        # Volume MA
        df["volume_ma"] = df["volume"].rolling(window=config.VOLUME_MA_PERIOD).mean()
        df["volume_ratio"] = df["volume"] / df["volume_ma"]

        # Stochastic RSI
        stoch = ta_lib.momentum.StochRSIIndicator(df["close"], window=14)
        df["stoch_k"] = stoch.stochrsi_k()
        df["stoch_d"] = stoch.stochrsi_d()

        # ADX (trend strength)
        adx = ta_lib.trend.ADXIndicator(df["high"], df["low"], df["close"], window=14)
        df["adx"] = adx.adx()

        return df

    # =========================================================================
    # SIGNAL DETECTION PER TIMEFRAME
    # =========================================================================
    def analyze_timeframe(self, df: pd.DataFrame) -> dict:
        """Analyze a single timeframe for signals. Returns signal components."""
        if df is None or len(df) < 50:
            return {"trend": "neutral", "signals": [], "score": 0}

        df = self.calculate_indicators(df)
        last = df.iloc[-1]
        prev = df.iloc[-2]

        signals = []
        bullish_score = 0
        bearish_score = 0

        # --- EMA Crossover ---
        if last["ema_fast"] > last["ema_slow"] and prev["ema_fast"] <= prev["ema_slow"]:
            signals.append("ema_bullish_cross")
            bullish_score += 2
        elif last["ema_fast"] < last["ema_slow"] and prev["ema_fast"] >= prev["ema_slow"]:
            signals.append("ema_bearish_cross")
            bearish_score += 2

        # EMA trend position
        if last["close"] > last["ema_trend"]:
            signals.append("above_ema_trend")
            bullish_score += 1
        elif last["close"] < last["ema_trend"]:
            signals.append("below_ema_trend")
            bearish_score += 1

        # --- RSI ---
        if last["rsi"] < config.RSI_OVERSOLD:
            signals.append("rsi_oversold")
            bullish_score += 2
        elif last["rsi"] > config.RSI_OVERBOUGHT:
            signals.append("rsi_overbought")
            bearish_score += 2
        elif 40 < last["rsi"] < 60:
            signals.append("rsi_neutral")

        # RSI divergence (simplified)
        if last["rsi"] > prev["rsi"] and last["close"] < prev["close"]:
            signals.append("rsi_bullish_divergence")
            bullish_score += 1
        elif last["rsi"] < prev["rsi"] and last["close"] > prev["close"]:
            signals.append("rsi_bearish_divergence")
            bearish_score += 1

        # --- MACD ---
        if last["macd_hist"] > 0 and prev["macd_hist"] <= 0:
            signals.append("macd_bullish_cross")
            bullish_score += 2
        elif last["macd_hist"] < 0 and prev["macd_hist"] >= 0:
            signals.append("macd_bearish_cross")
            bearish_score += 2

        if last["macd_hist"] > 0:
            signals.append("macd_bullish")
            bullish_score += 1
        elif last["macd_hist"] < 0:
            signals.append("macd_bearish")
            bearish_score += 1

        # --- Volume ---
        if last["volume_ratio"] > config.VOLUME_THRESHOLD:
            signals.append("high_volume")
            # Volume confirms the direction
            if bullish_score > bearish_score:
                bullish_score += 1
            elif bearish_score > bullish_score:
                bearish_score += 1

        # --- Bollinger Bands ---
        if last["close"] <= last["bb_lower"]:
            signals.append("bb_oversold")
            bullish_score += 1
        elif last["close"] >= last["bb_upper"]:
            signals.append("bb_overbought")
            bearish_score += 1

        # --- Stochastic RSI ---
        if last["stoch_k"] < 0.2 and last["stoch_d"] < 0.2:
            signals.append("stoch_oversold")
            bullish_score += 1
        elif last["stoch_k"] > 0.8 and last["stoch_d"] > 0.8:
            signals.append("stoch_overbought")
            bearish_score += 1

        # --- ADX (trend strength) ---
        if last["adx"] > 25:
            signals.append("strong_trend")

        # Determine trend
        net_score = bullish_score - bearish_score
        if net_score >= 3:
            trend = "bullish"
        elif net_score <= -3:
            trend = "bearish"
        else:
            trend = "neutral"

        return {
            "trend": trend,
            "signals": signals,
            "score": net_score,
            "bullish_score": bullish_score,
            "bearish_score": bearish_score,
            "last_close": last["close"],
            "atr": last["atr"],
            "rsi": last["rsi"],
            "adx": last["adx"],
            "ema_fast": last["ema_fast"],
            "ema_slow": last["ema_slow"],
        }

    # =========================================================================
    # MULTI-TIMEFRAME ANALYSIS
    # =========================================================================
    def multi_timeframe_analysis(self, symbol: str) -> dict:
        """Analyze across entry, confirmation, and trend timeframes."""
        results = {}
        for tf_name, tf_interval in config.TIMEFRAMES.items():
            df = self.get_klines(symbol, tf_interval)
            results[tf_name] = self.analyze_timeframe(df)

        # Alignment check
        trends = [results[tf]["trend"] for tf in results]
        all_bullish = all(t == "bullish" for t in trends)
        all_bearish = all(t == "bearish" for t in trends)

        # At least entry + one other must agree
        entry_trend = results["entry"]["trend"]
        confirm_trend = results["confirm"]["trend"]
        trend_trend = results["trend"]["trend"]

        partial_bullish = (entry_trend == "bullish" and
                          (confirm_trend == "bullish" or trend_trend == "bullish"))
        partial_bearish = (entry_trend == "bearish" and
                          (confirm_trend == "bearish" or trend_trend == "bearish"))

        return {
            "symbol": symbol,
            "timeframes": results,
            "all_aligned": all_bullish or all_bearish,
            "partial_aligned": partial_bullish or partial_bearish,
            "direction": "bullish" if (all_bullish or partial_bullish) else
                        "bearish" if (all_bearish or partial_bearish) else "neutral",
        }

    # =========================================================================
    # GENERATE TRADE SIGNAL
    # =========================================================================
    def generate_signal(self, symbol: str) -> Optional[Signal]:
        """Generate a trade signal with full confluence check."""
        # Multi-timeframe analysis
        mtf = self.multi_timeframe_analysis(symbol)

        if mtf["direction"] == "neutral":
            return None

        if not mtf["partial_aligned"]:
            return None

        entry_data = mtf["timeframes"]["entry"]
        if entry_data["atr"] is None or np.isnan(entry_data["atr"]):
            return None

        # Market analysis (funding, OI, orderbook)
        market = self.market_analyzer.get_market_score(symbol)

        # Check if market score conflicts with technical direction
        if mtf["direction"] == "bullish" and market["score"] < -30:
            logger.info(f"{symbol}: Bullish technicals but bearish market score ({market['score']}), skipping")
            return None
        if mtf["direction"] == "bearish" and market["score"] > 30:
            logger.info(f"{symbol}: Bearish technicals but bullish market score ({market['score']}), skipping")
            return None

        # Calculate confidence
        confidence = self._calculate_confidence(mtf, market)
        if confidence < 0.55:  # Minimum 55% confidence to trade
            return None

        # Calculate entry, SL, TP
        price = entry_data["last_close"]
        atr = entry_data["atr"]

        if mtf["direction"] == "bullish":
            direction = "LONG"
            stop_loss = price - (atr * config.ATR_MULTIPLIER)
            take_profit = price + (atr * config.ATR_MULTIPLIER * config.MIN_RISK_REWARD)
        else:
            direction = "SHORT"
            stop_loss = price + (atr * config.ATR_MULTIPLIER)
            take_profit = price - (atr * config.ATR_MULTIPLIER * config.MIN_RISK_REWARD)

        # Collect reasons
        reasons = []
        if mtf["all_aligned"]:
            reasons.append("All timeframes aligned")
        else:
            reasons.append("Entry + confirmation aligned")
        reasons.extend(entry_data["signals"][:5])

        if market["score"] > 20:
            reasons.append(f"Bullish market score ({market['score']})")
        elif market["score"] < -20:
            reasons.append(f"Bearish market score ({market['score']})")

        return Signal(
            symbol=symbol,
            direction=direction,
            confidence=confidence,
            entry_price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            reasons=reasons,
            timeframe_alignment={tf: mtf["timeframes"][tf]["trend"] for tf in mtf["timeframes"]},
            market_score=market["score"],
        )

    def _calculate_confidence(self, mtf: dict, market: dict) -> float:
        """Calculate overall signal confidence (0.0 to 1.0)."""
        score = 0.0

        # Timeframe alignment
        if mtf["all_aligned"]:
            score += 0.30
        elif mtf["partial_aligned"]:
            score += 0.15

        # Entry signal strength
        entry = mtf["timeframes"]["entry"]
        signal_strength = abs(entry["score"]) / 10.0
        score += min(signal_strength, 0.25)

        # Strong trend (ADX)
        if entry.get("adx") and entry["adx"] > 25:
            score += 0.10
        if entry.get("adx") and entry["adx"] > 40:
            score += 0.05

        # Volume confirmation
        if "high_volume" in entry["signals"]:
            score += 0.10

        # Market score alignment
        direction = mtf["direction"]
        if direction == "bullish" and market["score"] > 20:
            score += 0.10
        elif direction == "bearish" and market["score"] < -20:
            score += 0.10

        # Confluence bonus (multiple signals agreeing)
        num_signals = len(entry["signals"])
        if num_signals >= 5:
            score += 0.10
        elif num_signals >= 3:
            score += 0.05

        return min(score, 1.0)

    # =========================================================================
    # SCAN ALL PAIRS
    # =========================================================================
    def scan_all_pairs(self) -> list[Signal]:
        """Scan all configured pairs for trade signals."""
        signals = []
        for symbol in config.TRADING_PAIRS:
            try:
                signal = self.generate_signal(symbol)
                if signal:
                    signals.append(signal)
                    logger.info(f"Signal found: {signal}")
            except Exception as e:
                logger.error(f"Error scanning {symbol}: {e}")
        # Sort by confidence descending
        signals.sort(key=lambda s: s.confidence, reverse=True)
        return signals
