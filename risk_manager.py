"""
Risk Manager - Position sizing, correlation filtering, drawdown circuit breaker,
volatility-adjusted sizing, session filtering.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from binance.client import Client
from binance.exceptions import BinanceAPIException

import config

logger = logging.getLogger(__name__)


class RiskManager:
    def __init__(self, client: Client):
        self.client = client
        self.daily_pnl = 0.0
        self.daily_reset_date = datetime.now(timezone.utc).date()
        self.trades_today = 0
        self._precision_cache: dict[str, int] = {}

    # =========================================================================
    # ACCOUNT INFO
    # =========================================================================
    def get_account_balance(self) -> float:
        """Get total USDT balance."""
        try:
            account = self.client.futures_account()
            return float(account["totalWalletBalance"])
        except BinanceAPIException as e:
            logger.error(f"Error fetching balance: {e}")
            return 0.0

    def get_available_balance(self) -> float:
        """Get available (unrealized) balance."""
        try:
            account = self.client.futures_account()
            return float(account["availableBalance"])
        except BinanceAPIException as e:
            logger.error(f"Error fetching available balance: {e}")
            return 0.0

    def get_open_positions(self) -> list:
        """Get all currently open positions."""
        try:
            positions = self.client.futures_position_information()
            return [
                p for p in positions
                if float(p["positionAmt"]) != 0
            ]
        except BinanceAPIException as e:
            logger.error(f"Error fetching positions: {e}")
            return []

    # =========================================================================
    # DAILY RESET
    # =========================================================================
    def check_daily_reset(self):
        """Reset daily counters if new day."""
        today = datetime.now(timezone.utc).date()
        if today != self.daily_reset_date:
            self.daily_pnl = 0.0
            self.trades_today = 0
            self.daily_reset_date = today
            logger.info("Daily risk counters reset")

    def update_daily_pnl(self, pnl: float):
        """Update running daily P&L."""
        self.check_daily_reset()
        self.daily_pnl += pnl

    # =========================================================================
    # RISK CHECKS
    # =========================================================================
    def can_open_trade(self, symbol: str, direction: str) -> dict:
        """
        Full risk check before opening a new position.
        Returns {"allowed": bool, "reason": str}
        """
        self.check_daily_reset()

        # Check daily loss limit (circuit breaker)
        balance = self.get_account_balance()
        if balance <= 0:
            return {"allowed": False, "reason": "Cannot fetch account balance"}

        max_daily_loss = balance * config.MAX_ACCOUNT_RISK
        if self.daily_pnl < -max_daily_loss:
            return {
                "allowed": False,
                "reason": f"Daily loss limit hit: ${self.daily_pnl:.2f} "
                         f"(limit: -${max_daily_loss:.2f}). Trading paused until tomorrow.",
            }

        # Check max open positions
        open_positions = self.get_open_positions()
        if len(open_positions) >= config.MAX_OPEN_POSITIONS:
            return {
                "allowed": False,
                "reason": f"Max open positions reached ({config.MAX_OPEN_POSITIONS})",
            }

        # Check if already in this symbol
        for pos in open_positions:
            if pos["symbol"] == symbol:
                return {
                    "allowed": False,
                    "reason": f"Already have an open position in {symbol}",
                }

        # Check correlation filter
        if not self._check_correlation(symbol, open_positions):
            return {
                "allowed": False,
                "reason": f"Too many correlated positions. {symbol} blocked by correlation filter.",
            }

        # Check session filter
        if not config.TRADE_ALL_SESSIONS and not self._is_active_session():
            return {
                "allowed": False,
                "reason": "Outside active trading sessions",
            }

        return {"allowed": True, "reason": "All risk checks passed"}

    # =========================================================================
    # POSITION SIZING
    # =========================================================================
    def calculate_position_size(self, entry_price: float, stop_loss: float,
                                symbol: str) -> dict:
        """
        Calculate position size based on risk per trade and ATR-based stop.
        Adjusts for volatility.
        """
        balance = self.get_account_balance()
        if balance <= 0:
            return {"size": 0, "notional": 0, "risk_amount": 0}

        risk_amount = balance * config.MAX_RISK_PER_TRADE
        sl_distance = abs(entry_price - stop_loss)

        if sl_distance == 0:
            return {"size": 0, "notional": 0, "risk_amount": 0}

        # Base position size (in contracts)
        position_size = risk_amount / sl_distance

        # Apply leverage
        notional_value = position_size * entry_price
        max_notional = balance * config.LEVERAGE
        if notional_value > max_notional:
            position_size = max_notional / entry_price

        # Round to symbol precision
        precision = self._get_quantity_precision(symbol)
        position_size = round(position_size, precision)

        notional = position_size * entry_price

        return {
            "size": position_size,
            "notional": round(notional, 2),
            "risk_amount": round(risk_amount, 2),
            "sl_distance": round(sl_distance, 4),
            "leverage": config.LEVERAGE,
        }

    def _get_quantity_precision(self, symbol: str) -> int:
        """Get the quantity precision for a symbol (cached)."""
        if symbol in self._precision_cache:
            return self._precision_cache[symbol]
        try:
            info = self.client.futures_exchange_info()
            for s in info["symbols"]:
                self._precision_cache[s["symbol"]] = s["quantityPrecision"]
            return self._precision_cache.get(symbol, 3)
        except Exception:
            pass
        return 3  # Default

    # =========================================================================
    # TAKE PROFIT LEVELS
    # =========================================================================
    def calculate_tp_levels(self, entry_price: float, stop_loss: float,
                            direction: str) -> dict:
        """Calculate partial take profit levels."""
        sl_distance = abs(entry_price - stop_loss)

        if direction == "LONG":
            tp1 = entry_price + (sl_distance * config.TP1_RATIO)
            tp2 = entry_price + (sl_distance * config.TP2_RATIO)
            breakeven = entry_price + (sl_distance * 0.1)  # Slightly above entry
        else:
            tp1 = entry_price - (sl_distance * config.TP1_RATIO)
            tp2 = entry_price - (sl_distance * config.TP2_RATIO)
            breakeven = entry_price - (sl_distance * 0.1)

        return {
            "tp1": round(tp1, 4),
            "tp1_close_pct": config.TP1_CLOSE_PCT,
            "tp2": round(tp2, 4),
            "tp2_close_pct": config.TP2_CLOSE_PCT,
            "breakeven_stop": round(breakeven, 4),
            "trail_remaining": config.TP3_TRAIL,
        }

    # =========================================================================
    # TRAILING STOP
    # =========================================================================
    def calculate_trailing_stop(self, current_price: float, atr: float,
                                direction: str, entry_price: float,
                                current_stop: float) -> float:
        """Calculate ATR-based trailing stop. Only moves in favorable direction."""
        trail_distance = atr * config.ATR_MULTIPLIER

        if direction == "LONG":
            new_stop = current_price - trail_distance
            # Only move stop up, never down
            return max(new_stop, current_stop)
        else:
            new_stop = current_price + trail_distance
            # Only move stop down, never up
            return min(new_stop, current_stop)

    # =========================================================================
    # CORRELATION FILTER
    # =========================================================================
    def _check_correlation(self, symbol: str, open_positions: list) -> bool:
        """Check if adding this symbol would exceed correlated position limits."""
        # Find which group this symbol belongs to
        symbol_group = None
        for group, symbols in config.CORRELATION_GROUPS.items():
            if symbol in symbols:
                symbol_group = group
                break

        if symbol_group is None:
            return True  # Unknown symbol, allow

        # Count open positions in the same group
        group_symbols = config.CORRELATION_GROUPS[symbol_group]
        correlated_count = sum(
            1 for pos in open_positions
            if pos["symbol"] in group_symbols
        )

        return correlated_count < config.MAX_CORRELATED_POSITIONS

    # =========================================================================
    # SESSION FILTER
    # =========================================================================
    def _is_active_session(self) -> bool:
        """Check if current time is within an active trading session."""
        now = datetime.now(timezone.utc)
        current_hour = now.hour

        for session_name, (start, end) in config.ACTIVE_SESSIONS.items():
            if start <= current_hour < end:
                return True
        return False

    # =========================================================================
    # ANTI-LIQUIDATION
    # =========================================================================
    def check_liquidation_risk(self, symbol: str, position: dict) -> dict:
        """Check if position is approaching liquidation price and suggest action."""
        try:
            liq_price = float(position.get("liquidationPrice", 0))
            entry_price = float(position["entryPrice"])
            mark_price = float(position["markPrice"])
            position_amt = float(position["positionAmt"])

            if liq_price <= 0 or entry_price <= 0:
                return {"at_risk": False}

            direction = "LONG" if position_amt > 0 else "SHORT"

            if direction == "LONG":
                distance_to_liq = (mark_price - liq_price) / mark_price
            else:
                distance_to_liq = (liq_price - mark_price) / mark_price

            # If within 5% of liquidation, flag as at risk
            at_risk = distance_to_liq < 0.05

            return {
                "at_risk": at_risk,
                "distance_pct": round(distance_to_liq * 100, 2),
                "liquidation_price": liq_price,
                "mark_price": mark_price,
                "direction": direction,
                "action": "REDUCE_POSITION" if at_risk else "HOLD",
            }
        except (KeyError, ValueError) as e:
            logger.error(f"Error checking liquidation risk: {e}")
            return {"at_risk": False}
