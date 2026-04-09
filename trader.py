"""
Trader - Binance Futures order execution with partial TP, trailing stops,
breakeven management. Supports both paper and live trading.
"""

import logging
import time
from typing import Optional

from binance.client import Client
from binance.exceptions import BinanceAPIException

import config
from strategy import Signal
from risk_manager import RiskManager
from journal import TradeJournal

logger = logging.getLogger(__name__)


class PaperPosition:
    """Simulated position for paper trading."""
    def __init__(self, symbol, direction, size, entry_price, stop_loss,
                 tp_levels, leverage):
        self.symbol = symbol
        self.direction = direction
        self.size = size
        self.remaining_size = size
        self.entry_price = entry_price
        self.stop_loss = stop_loss
        self.current_stop = stop_loss
        self.tp_levels = tp_levels
        self.leverage = leverage
        self.tp1_hit = False
        self.tp2_hit = False
        self.pnl = 0.0
        self.open_time = time.time()

    def check_price(self, current_price: float) -> list:
        """Check if any TP or SL is hit. Returns list of events."""
        events = []

        if self.direction == "LONG":
            # Check stop loss
            if current_price <= self.current_stop:
                self.pnl = (current_price - self.entry_price) * self.remaining_size * self.leverage
                events.append(("STOPPED_OUT", self.remaining_size, self.pnl))
                self.remaining_size = 0
                return events

            # Check TP1
            if not self.tp1_hit and current_price >= self.tp_levels["tp1"]:
                close_size = self.size * self.tp_levels["tp1_close_pct"]
                pnl = (current_price - self.entry_price) * close_size * self.leverage
                self.pnl += pnl
                self.remaining_size -= close_size
                self.tp1_hit = True
                # Move stop to breakeven
                self.current_stop = self.tp_levels["breakeven_stop"]
                events.append(("TP1_HIT", close_size, pnl))

            # Check TP2
            if not self.tp2_hit and current_price >= self.tp_levels["tp2"]:
                close_size = self.size * self.tp_levels["tp2_close_pct"]
                pnl = (current_price - self.entry_price) * close_size * self.leverage
                self.pnl += pnl
                self.remaining_size -= close_size
                self.tp2_hit = True
                events.append(("TP2_HIT", close_size, pnl))

        else:  # SHORT
            if current_price >= self.current_stop:
                self.pnl = (self.entry_price - current_price) * self.remaining_size * self.leverage
                events.append(("STOPPED_OUT", self.remaining_size, self.pnl))
                self.remaining_size = 0
                return events

            if not self.tp1_hit and current_price <= self.tp_levels["tp1"]:
                close_size = self.size * self.tp_levels["tp1_close_pct"]
                pnl = (self.entry_price - current_price) * close_size * self.leverage
                self.pnl += pnl
                self.remaining_size -= close_size
                self.tp1_hit = True
                self.current_stop = self.tp_levels["breakeven_stop"]
                events.append(("TP1_HIT", close_size, pnl))

            if not self.tp2_hit and current_price <= self.tp_levels["tp2"]:
                close_size = self.size * self.tp_levels["tp2_close_pct"]
                pnl = (self.entry_price - current_price) * close_size * self.leverage
                self.pnl += pnl
                self.remaining_size -= close_size
                self.tp2_hit = True
                events.append(("TP2_HIT", close_size, pnl))

        return events


class Trader:
    def __init__(self, client: Client, risk_manager: RiskManager, journal: TradeJournal):
        self.client = client
        self.risk_manager = risk_manager
        self.journal = journal
        self.paper_positions: dict[str, PaperPosition] = {}
        self.active_orders: dict[str, list] = {}  # symbol -> [order_ids]
        self.trailing_data: dict[str, dict] = {}   # symbol -> trailing stop info

    # =========================================================================
    # ORDER EXECUTION
    # =========================================================================
    def execute_signal(self, signal: Signal) -> Optional[dict]:
        """Execute a trade signal after all risk checks pass."""
        # Risk check
        risk_check = self.risk_manager.can_open_trade(signal.symbol, signal.direction)
        if not risk_check["allowed"]:
            logger.info(f"Trade blocked: {risk_check['reason']}")
            return {"success": False, "reason": risk_check["reason"]}

        # Calculate position size
        sizing = self.risk_manager.calculate_position_size(
            signal.entry_price, signal.stop_loss, signal.symbol
        )
        if sizing["size"] <= 0:
            return {"success": False, "reason": "Position size too small"}

        # Calculate TP levels
        tp_levels = self.risk_manager.calculate_tp_levels(
            signal.entry_price, signal.stop_loss, signal.direction
        )

        if config.TRADING_MODE == "paper":
            return self._paper_trade(signal, sizing, tp_levels)
        else:
            return self._live_trade(signal, sizing, tp_levels)

    # =========================================================================
    # PAPER TRADING
    # =========================================================================
    def _paper_trade(self, signal: Signal, sizing: dict, tp_levels: dict) -> dict:
        """Execute paper trade."""
        position = PaperPosition(
            symbol=signal.symbol,
            direction=signal.direction,
            size=sizing["size"],
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            tp_levels=tp_levels,
            leverage=config.LEVERAGE,
        )
        self.paper_positions[signal.symbol] = position

        # Log to journal
        self.journal.log_trade_open(
            symbol=signal.symbol,
            direction=signal.direction,
            entry_price=signal.entry_price,
            size=sizing["size"],
            stop_loss=signal.stop_loss,
            tp_levels=tp_levels,
            confidence=signal.confidence,
            reasons=signal.reasons,
            mode="paper",
        )

        result = {
            "success": True,
            "mode": "paper",
            "symbol": signal.symbol,
            "direction": signal.direction,
            "size": sizing["size"],
            "entry_price": signal.entry_price,
            "stop_loss": signal.stop_loss,
            "tp1": tp_levels["tp1"],
            "tp2": tp_levels["tp2"],
            "risk_amount": sizing["risk_amount"],
            "confidence": signal.confidence,
        }
        logger.info(f"Paper trade opened: {result}")
        return result

    # =========================================================================
    # LIVE TRADING
    # =========================================================================
    def _live_trade(self, signal: Signal, sizing: dict, tp_levels: dict) -> dict:
        """Execute live trade on Binance Futures."""
        try:
            # Set leverage
            self.client.futures_change_leverage(
                symbol=signal.symbol, leverage=config.LEVERAGE
            )

            # Market order entry
            side = "BUY" if signal.direction == "LONG" else "SELL"
            order = self.client.futures_create_order(
                symbol=signal.symbol,
                side=side,
                type="MARKET",
                quantity=sizing["size"],
            )

            # Place stop loss
            sl_side = "SELL" if signal.direction == "LONG" else "BUY"
            sl_order = self.client.futures_create_order(
                symbol=signal.symbol,
                side=sl_side,
                type="STOP_MARKET",
                stopPrice=round(signal.stop_loss, 2),
                closePosition=True,
            )

            # Place TP1 (partial close)
            tp1_qty = round(sizing["size"] * config.TP1_CLOSE_PCT, self.risk_manager._get_quantity_precision(signal.symbol))
            tp1_order = self.client.futures_create_order(
                symbol=signal.symbol,
                side=sl_side,
                type="TAKE_PROFIT_MARKET",
                stopPrice=round(tp_levels["tp1"], 2),
                quantity=tp1_qty,
                reduceOnly=True,
            )

            # Track orders
            self.active_orders[signal.symbol] = [
                order["orderId"], sl_order["orderId"], tp1_order["orderId"]
            ]

            # Store trailing stop data
            self.trailing_data[signal.symbol] = {
                "direction": signal.direction,
                "entry_price": signal.entry_price,
                "current_stop": signal.stop_loss,
                "tp1_hit": False,
                "tp2_hit": False,
                "tp_levels": tp_levels,
                "size": sizing["size"],
                "remaining_size": sizing["size"],
            }

            # Log to journal
            self.journal.log_trade_open(
                symbol=signal.symbol,
                direction=signal.direction,
                entry_price=float(order.get("avgPrice", signal.entry_price)),
                size=sizing["size"],
                stop_loss=signal.stop_loss,
                tp_levels=tp_levels,
                confidence=signal.confidence,
                reasons=signal.reasons,
                mode="live",
            )

            return {
                "success": True,
                "mode": "live",
                "symbol": signal.symbol,
                "direction": signal.direction,
                "order_id": order["orderId"],
                "size": sizing["size"],
                "entry_price": signal.entry_price,
                "stop_loss": signal.stop_loss,
                "tp1": tp_levels["tp1"],
                "tp2": tp_levels["tp2"],
                "risk_amount": sizing["risk_amount"],
                "confidence": signal.confidence,
            }

        except BinanceAPIException as e:
            logger.error(f"Live trade error for {signal.symbol}: {e}")
            return {"success": False, "reason": str(e)}

    # =========================================================================
    # POSITION MANAGEMENT
    # =========================================================================
    def update_paper_positions(self) -> list:
        """Update all paper positions with current prices. Returns events."""
        events = []
        closed_symbols = []

        for symbol, pos in self.paper_positions.items():
            try:
                ticker = self.client.futures_symbol_ticker(symbol=symbol)
                current_price = float(ticker["price"])

                # Check TP/SL hits
                pos_events = pos.check_price(current_price)
                for event_type, size, pnl in pos_events:
                    events.append({
                        "symbol": symbol,
                        "event": event_type,
                        "size": size,
                        "pnl": round(pnl, 2),
                        "price": current_price,
                    })

                    if event_type == "STOPPED_OUT":
                        self.risk_manager.update_daily_pnl(pos.pnl)
                        self.journal.log_trade_close(
                            symbol=symbol, exit_price=current_price,
                            pnl=pos.pnl, reason=event_type,
                        )
                        closed_symbols.append(symbol)

                    elif event_type == "TP1_HIT":
                        self.journal.log_partial_close(
                            symbol=symbol, level="TP1",
                            price=current_price, pnl=pnl,
                        )

                    elif event_type == "TP2_HIT":
                        self.journal.log_partial_close(
                            symbol=symbol, level="TP2",
                            price=current_price, pnl=pnl,
                        )

                # Update trailing stop if TP1 hit
                if pos.tp1_hit and pos.remaining_size > 0:
                    # Simple trailing: move stop based on price movement
                    if pos.direction == "LONG":
                        new_stop = current_price * 0.985  # 1.5% trail
                        pos.current_stop = max(pos.current_stop, new_stop)
                    else:
                        new_stop = current_price * 1.015
                        pos.current_stop = min(pos.current_stop, new_stop)

                # Check if fully closed
                if pos.remaining_size <= 0 and symbol not in closed_symbols:
                    self.risk_manager.update_daily_pnl(pos.pnl)
                    self.journal.log_trade_close(
                        symbol=symbol, exit_price=current_price,
                        pnl=pos.pnl, reason="ALL_TP_HIT",
                    )
                    closed_symbols.append(symbol)

            except Exception as e:
                logger.error(f"Error updating paper position {symbol}: {e}")

        for symbol in closed_symbols:
            del self.paper_positions[symbol]

        return events

    def update_live_positions(self) -> list:
        """Monitor and manage live positions - trailing stops, TP management."""
        events = []
        positions = self.risk_manager.get_open_positions()

        for pos in positions:
            symbol = pos["symbol"]
            if symbol not in self.trailing_data:
                continue

            trail = self.trailing_data[symbol]
            mark_price = float(pos["markPrice"])
            unrealized_pnl = float(pos["unRealizedProfit"])

            # Detect TP1 fill: if position size shrank, TP1 order was filled by Binance
            current_size = abs(float(pos["positionAmt"]))
            if not trail["tp1_hit"] and current_size < trail["size"] * 0.95:
                trail["tp1_hit"] = True
                trail["remaining_size"] = current_size
                # Move stop to breakeven
                self._update_stop_order(symbol, trail["tp_levels"]["breakeven_stop"])
                trail["current_stop"] = trail["tp_levels"]["breakeven_stop"]
                events.append({
                    "symbol": symbol, "event": "TP1_HIT",
                    "price": mark_price, "pnl": unrealized_pnl,
                })

            # Check liquidation risk
            liq_check = self.risk_manager.check_liquidation_risk(symbol, pos)
            if liq_check["at_risk"]:
                events.append({
                    "symbol": symbol,
                    "event": "LIQUIDATION_WARNING",
                    "distance_pct": liq_check["distance_pct"],
                    "action": "REDUCE_POSITION",
                })
                # Auto-reduce 50% of position
                self._reduce_position(symbol, trail, 0.5)

            # TP management for live trades
            tp_levels = trail["tp_levels"]

            if trail["direction"] == "LONG":
                # Check TP2 (if TP1 already hit)
                if trail["tp1_hit"] and not trail["tp2_hit"]:
                    if mark_price >= tp_levels["tp2"]:
                        tp2_qty = round(
                            trail["size"] * config.TP2_CLOSE_PCT,
                            self.risk_manager._get_quantity_precision(symbol)
                        )
                        self._close_partial(symbol, "SELL", tp2_qty)
                        trail["tp2_hit"] = True
                        trail["remaining_size"] -= tp2_qty
                        events.append({
                            "symbol": symbol, "event": "TP2_HIT",
                            "price": mark_price, "pnl": unrealized_pnl,
                        })

                # Trailing stop update
                if trail["tp1_hit"]:
                    new_stop = mark_price * 0.985
                    if new_stop > trail["current_stop"]:
                        trail["current_stop"] = new_stop
                        self._update_stop_order(symbol, new_stop)

            else:  # SHORT
                if trail["tp1_hit"] and not trail["tp2_hit"]:
                    if mark_price <= tp_levels["tp2"]:
                        tp2_qty = round(
                            trail["size"] * config.TP2_CLOSE_PCT,
                            self.risk_manager._get_quantity_precision(symbol)
                        )
                        self._close_partial(symbol, "BUY", tp2_qty)
                        trail["tp2_hit"] = True
                        trail["remaining_size"] -= tp2_qty
                        events.append({
                            "symbol": symbol, "event": "TP2_HIT",
                            "price": mark_price, "pnl": unrealized_pnl,
                        })

                if trail["tp1_hit"]:
                    new_stop = mark_price * 1.015
                    if new_stop < trail["current_stop"]:
                        trail["current_stop"] = new_stop
                        self._update_stop_order(symbol, new_stop)

        return events

    def _close_partial(self, symbol: str, side: str, quantity: float):
        """Close a partial position."""
        try:
            self.client.futures_create_order(
                symbol=symbol, side=side, type="MARKET",
                quantity=quantity, reduceOnly=True,
            )
        except BinanceAPIException as e:
            logger.error(f"Error closing partial {symbol}: {e}")

    def _reduce_position(self, symbol: str, trail: dict, reduce_pct: float):
        """Reduce position size for anti-liquidation."""
        try:
            reduce_qty = round(
                trail["remaining_size"] * reduce_pct,
                self.risk_manager._get_quantity_precision(symbol)
            )
            side = "SELL" if trail["direction"] == "LONG" else "BUY"
            self._close_partial(symbol, side, reduce_qty)
            trail["remaining_size"] -= reduce_qty
            logger.warning(f"Anti-liquidation: Reduced {symbol} by {reduce_pct:.0%}")
        except Exception as e:
            logger.error(f"Error reducing position {symbol}: {e}")

    def _update_stop_order(self, symbol: str, new_stop: float):
        """Cancel existing stop and place new one."""
        try:
            # Cancel all open orders for this symbol
            self.client.futures_cancel_all_open_orders(symbol=symbol)
            # Place new stop
            trail = self.trailing_data.get(symbol, {})
            side = "SELL" if trail.get("direction") == "LONG" else "BUY"
            self.client.futures_create_order(
                symbol=symbol, side=side, type="STOP_MARKET",
                stopPrice=round(new_stop, 2), closePosition=True,
            )
        except BinanceAPIException as e:
            logger.error(f"Error updating stop for {symbol}: {e}")

    # =========================================================================
    # MANUAL CONTROLS
    # =========================================================================
    def close_position(self, symbol: str) -> dict:
        """Manually close an entire position."""
        if config.TRADING_MODE == "paper":
            if symbol in self.paper_positions:
                pos = self.paper_positions[symbol]
                try:
                    ticker = self.client.futures_symbol_ticker(symbol=symbol)
                    current_price = float(ticker["price"])
                    if pos.direction == "LONG":
                        pos.pnl = (current_price - pos.entry_price) * pos.remaining_size * pos.leverage
                    else:
                        pos.pnl = (pos.entry_price - current_price) * pos.remaining_size * pos.leverage
                except Exception:
                    current_price = pos.entry_price

                self.risk_manager.update_daily_pnl(pos.pnl)
                self.journal.log_trade_close(
                    symbol=symbol, exit_price=current_price,
                    pnl=pos.pnl, reason="MANUAL_CLOSE",
                )
                del self.paper_positions[symbol]
                return {"success": True, "pnl": round(pos.pnl, 2)}
            return {"success": False, "reason": "No paper position found"}

        # Live close
        try:
            positions = self.risk_manager.get_open_positions()
            for pos in positions:
                if pos["symbol"] == symbol:
                    amt = float(pos["positionAmt"])
                    side = "SELL" if amt > 0 else "BUY"
                    self.client.futures_create_order(
                        symbol=symbol, side=side, type="MARKET",
                        quantity=abs(amt), reduceOnly=True,
                    )
                    # Cancel open orders
                    self.client.futures_cancel_all_open_orders(symbol=symbol)
                    if symbol in self.trailing_data:
                        del self.trailing_data[symbol]
                    return {"success": True, "closed_size": abs(amt)}
            return {"success": False, "reason": "No position found"}
        except BinanceAPIException as e:
            return {"success": False, "reason": str(e)}

    def close_all_positions(self) -> list:
        """Close all open positions."""
        results = []
        if config.TRADING_MODE == "paper":
            symbols = list(self.paper_positions.keys())
            for symbol in symbols:
                result = self.close_position(symbol)
                results.append({"symbol": symbol, **result})
        else:
            positions = self.risk_manager.get_open_positions()
            for pos in positions:
                result = self.close_position(pos["symbol"])
                results.append({"symbol": pos["symbol"], **result})
        return results

    def get_all_positions_info(self) -> list:
        """Get info for all open positions."""
        positions_info = []

        if config.TRADING_MODE == "paper":
            for symbol, pos in self.paper_positions.items():
                try:
                    ticker = self.client.futures_symbol_ticker(symbol=symbol)
                    current_price = float(ticker["price"])
                    if pos.direction == "LONG":
                        unrealized = (current_price - pos.entry_price) * pos.remaining_size * pos.leverage
                    else:
                        unrealized = (pos.entry_price - current_price) * pos.remaining_size * pos.leverage
                except Exception:
                    current_price = pos.entry_price
                    unrealized = 0

                positions_info.append({
                    "symbol": symbol,
                    "direction": pos.direction,
                    "size": pos.remaining_size,
                    "entry_price": pos.entry_price,
                    "current_price": current_price,
                    "stop_loss": pos.current_stop,
                    "unrealized_pnl": round(unrealized, 2),
                    "realized_pnl": round(pos.pnl, 2),
                    "tp1_hit": pos.tp1_hit,
                    "tp2_hit": pos.tp2_hit,
                    "mode": "paper",
                })
        else:
            positions = self.risk_manager.get_open_positions()
            for pos in positions:
                positions_info.append({
                    "symbol": pos["symbol"],
                    "direction": "LONG" if float(pos["positionAmt"]) > 0 else "SHORT",
                    "size": abs(float(pos["positionAmt"])),
                    "entry_price": float(pos["entryPrice"]),
                    "current_price": float(pos["markPrice"]),
                    "unrealized_pnl": round(float(pos["unRealizedProfit"]), 2),
                    "leverage": int(pos["leverage"]),
                    "mode": "live",
                })

        return positions_info
