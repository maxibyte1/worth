"""
Copy Trader - Monitor profitable traders on Binance Futures leaderboard
and automatically mirror their trades with risk management.
"""

import logging
import time
from typing import Optional

import aiohttp

from binance.client import Client
from binance.exceptions import BinanceAPIException

import config
from risk_manager import RiskManager
from journal import TradeJournal

logger = logging.getLogger(__name__)

# Binance leaderboard API (public, no auth needed)
LEADERBOARD_URL = "https://www.binance.com/bapi/futures/v2/public/future/leaderboard"


class CopyTrader:
    def __init__(self, client: Client, risk_manager: RiskManager, journal: TradeJournal):
        self.client = client
        self.risk_manager = risk_manager
        self.journal = journal
        # Track what positions each leader has so we detect new ones
        self.leader_positions: dict[str, dict[str, dict]] = {}
        # Track our copied positions: leader_uid:symbol -> our position info
        self.copied_positions: dict[str, dict] = {}

    # =========================================================================
    # FETCH LEADER POSITIONS
    # =========================================================================
    async def fetch_leader_positions(self, uid: str) -> list[dict]:
        """
        Fetch a trader's current positions from Binance leaderboard.
        They must have position sharing enabled on their profile.
        """
        url = f"{LEADERBOARD_URL}/getOtherPosition"
        payload = {
            "encryptedUid": uid,
            "tradeType": "PERPETUAL",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        logger.error(f"Leaderboard API error: {resp.status}")
                        return []
                    data = await resp.json()

                    if not data.get("success") or not data.get("data"):
                        return []

                    positions = []
                    for pos in data["data"]["otherPositionRetList"]:
                        positions.append({
                            "symbol": pos["symbol"],
                            "direction": "LONG" if float(pos["amount"]) > 0 else "SHORT",
                            "size": abs(float(pos["amount"])),
                            "entry_price": float(pos["entryPrice"]),
                            "mark_price": float(pos["markPrice"]),
                            "pnl": float(pos.get("pnl", 0)),
                            "roe": float(pos.get("roe", 0)),
                            "leverage": int(pos.get("leverage", 1)),
                            "update_time": pos.get("updateTimeStamp", 0),
                        })
                    return positions

        except Exception as e:
            logger.error(f"Error fetching leader {uid} positions: {e}")
            return []

    # =========================================================================
    # DETECT CHANGES
    # =========================================================================
    async def check_leaders(self) -> list[dict]:
        """
        Check all leaders for new/closed positions.
        Returns list of events to act on.
        """
        events = []

        for uid in config.COPY_TRADER_UIDS:
            uid = uid.strip()
            if not uid:
                continue

            current_positions = await self.fetch_leader_positions(uid)
            if not current_positions and uid not in self.leader_positions:
                continue

            previous = self.leader_positions.get(uid, {})
            current_map = {p["symbol"]: p for p in current_positions}

            # Detect NEW positions (leader opened a trade)
            for symbol, pos in current_map.items():
                if symbol not in previous:
                    events.append({
                        "type": "LEADER_OPENED",
                        "uid": uid,
                        "symbol": symbol,
                        "direction": pos["direction"],
                        "entry_price": pos["entry_price"],
                        "leverage": pos["leverage"],
                        "size": pos["size"],
                    })
                    logger.info(
                        f"Leader {uid[:8]} opened {pos['direction']} on {symbol}"
                    )

            # Detect CLOSED positions (leader closed a trade)
            for symbol, prev_pos in previous.items():
                if symbol not in current_map:
                    events.append({
                        "type": "LEADER_CLOSED",
                        "uid": uid,
                        "symbol": symbol,
                        "direction": prev_pos["direction"],
                    })
                    logger.info(
                        f"Leader {uid[:8]} closed {symbol}"
                    )

            # Detect direction FLIPS (leader reversed position)
            for symbol in current_map:
                if symbol in previous:
                    if current_map[symbol]["direction"] != previous[symbol]["direction"]:
                        events.append({
                            "type": "LEADER_CLOSED",
                            "uid": uid,
                            "symbol": symbol,
                            "direction": previous[symbol]["direction"],
                        })
                        events.append({
                            "type": "LEADER_OPENED",
                            "uid": uid,
                            "symbol": symbol,
                            "direction": current_map[symbol]["direction"],
                            "entry_price": current_map[symbol]["entry_price"],
                            "leverage": current_map[symbol]["leverage"],
                            "size": current_map[symbol]["size"],
                        })

            self.leader_positions[uid] = current_map

        return events

    # =========================================================================
    # EXECUTE COPY TRADES
    # =========================================================================
    async def process_events(self, events: list[dict]) -> list[dict]:
        """Process leader events and execute copy trades."""
        results = []

        for event in events:
            if event["type"] == "LEADER_OPENED":
                result = self._copy_open(event)
                if result:
                    results.append(result)

            elif event["type"] == "LEADER_CLOSED":
                result = self._copy_close(event)
                if result:
                    results.append(result)

        return results

    def _copy_open(self, event: dict) -> Optional[dict]:
        """Mirror a leader's new position."""
        symbol = event["symbol"]
        uid = event["uid"]
        key = f"{uid}:{symbol}"

        # Risk check
        risk_check = self.risk_manager.can_open_trade(symbol, event["direction"])
        if not risk_check["allowed"]:
            logger.info(f"Copy trade blocked: {risk_check['reason']}")
            return {"success": False, "action": "COPY_OPEN", "symbol": symbol,
                    "reason": risk_check["reason"]}

        # Calculate our position size based on scale factor
        balance = self.risk_manager.get_account_balance()
        max_risk = balance * config.COPY_TRADE_MAX_RISK
        entry_price = event["entry_price"]

        # Use the leader's leverage (capped at our config)
        leverage = min(event["leverage"], config.LEVERAGE)

        # Scale the position: use a fraction of what the leader has
        our_notional = balance * config.COPY_TRADE_SCALE * leverage
        our_size = our_notional / entry_price

        # Get precision and round
        precision = self.risk_manager._get_quantity_precision(symbol)
        our_size = round(our_size, precision)

        if our_size <= 0:
            return None

        if config.TRADING_MODE == "paper":
            self.copied_positions[key] = {
                "symbol": symbol,
                "direction": event["direction"],
                "size": our_size,
                "entry_price": entry_price,
                "leverage": leverage,
                "leader_uid": uid,
                "open_time": time.time(),
            }
            self.journal.log_trade_open(
                symbol=symbol, direction=event["direction"],
                entry_price=entry_price, size=our_size,
                stop_loss=0, tp_levels={},
                confidence=0, reasons=[f"Copy trade from leader {uid[:8]}"],
                mode="paper_copy",
            )
            return {
                "success": True, "action": "COPY_OPEN", "symbol": symbol,
                "direction": event["direction"], "size": our_size,
                "leader": uid[:8], "mode": "paper",
            }

        # Live copy trade
        try:
            self.client.futures_change_leverage(symbol=symbol, leverage=leverage)

            side = "BUY" if event["direction"] == "LONG" else "SELL"
            order = self.client.futures_create_order(
                symbol=symbol, side=side, type="MARKET", quantity=our_size,
            )

            self.copied_positions[key] = {
                "symbol": symbol,
                "direction": event["direction"],
                "size": our_size,
                "entry_price": float(order.get("avgPrice", entry_price)),
                "leverage": leverage,
                "leader_uid": uid,
                "order_id": order["orderId"],
                "open_time": time.time(),
            }

            self.journal.log_trade_open(
                symbol=symbol, direction=event["direction"],
                entry_price=float(order.get("avgPrice", entry_price)),
                size=our_size, stop_loss=0, tp_levels={},
                confidence=0, reasons=[f"Copy trade from leader {uid[:8]}"],
                mode="live_copy",
            )

            return {
                "success": True, "action": "COPY_OPEN", "symbol": symbol,
                "direction": event["direction"], "size": our_size,
                "leader": uid[:8], "mode": "live",
            }

        except BinanceAPIException as e:
            logger.error(f"Copy trade error: {e}")
            return {"success": False, "action": "COPY_OPEN", "symbol": symbol,
                    "reason": str(e)}

    def _copy_close(self, event: dict) -> Optional[dict]:
        """Close our copied position when leader closes theirs."""
        symbol = event["symbol"]
        uid = event["uid"]
        key = f"{uid}:{symbol}"

        if key not in self.copied_positions:
            return None

        copied = self.copied_positions[key]

        if config.TRADING_MODE == "paper":
            # Calculate paper P&L
            try:
                ticker = self.client.futures_symbol_ticker(symbol=symbol)
                current_price = float(ticker["price"])
                if copied["direction"] == "LONG":
                    pnl = (current_price - copied["entry_price"]) * copied["size"] * copied["leverage"]
                else:
                    pnl = (copied["entry_price"] - current_price) * copied["size"] * copied["leverage"]
            except Exception:
                current_price = copied["entry_price"]
                pnl = 0

            self.risk_manager.update_daily_pnl(pnl)
            self.journal.log_trade_close(
                symbol=symbol, exit_price=current_price,
                pnl=pnl, reason="LEADER_CLOSED",
            )
            del self.copied_positions[key]
            return {
                "success": True, "action": "COPY_CLOSE", "symbol": symbol,
                "pnl": round(pnl, 2), "leader": uid[:8], "mode": "paper",
            }

        # Live close
        try:
            side = "SELL" if copied["direction"] == "LONG" else "BUY"
            self.client.futures_create_order(
                symbol=symbol, side=side, type="MARKET",
                quantity=copied["size"], reduceOnly=True,
            )
            del self.copied_positions[key]
            return {
                "success": True, "action": "COPY_CLOSE", "symbol": symbol,
                "leader": uid[:8], "mode": "live",
            }
        except BinanceAPIException as e:
            logger.error(f"Copy close error: {e}")
            return {"success": False, "action": "COPY_CLOSE",
                    "symbol": symbol, "reason": str(e)}

    # =========================================================================
    # STATUS
    # =========================================================================
    def get_copied_positions(self) -> list[dict]:
        """Get all currently copied positions."""
        return list(self.copied_positions.values())
