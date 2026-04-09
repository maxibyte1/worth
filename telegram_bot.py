"""
Telegram Bot - Full control interface for the trading bot.
Commands, alerts, dashboards, and manual overrides.
"""

import logging
import os
from datetime import datetime, timezone

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes
)

import config
from trader import Trader
from risk_manager import RiskManager
from journal import TradeJournal
from chart_generator import ChartGenerator
from copy_trader import CopyTrader

logger = logging.getLogger(__name__)


class TelegramBot:
    def __init__(self, trader: Trader, risk_manager: RiskManager,
                 journal: TradeJournal, chart_gen: ChartGenerator):
        self.trader = trader
        self.risk_manager = risk_manager
        self.journal = journal
        self.chart_gen = chart_gen
        self.copy_trader: CopyTrader = None  # Set from main.py
        self.bot_running = False
        self.app = None

    def _authorized(self, update: Update) -> bool:
        """Check if user is authorized."""
        return str(update.effective_chat.id) == config.TELEGRAM_CHAT_ID

    # =========================================================================
    # COMMAND HANDLERS
    # =========================================================================
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update):
            await update.message.reply_text("Unauthorized.")
            return

        text = (
            "🤖 *Worth AI Trading Bot*\n\n"
            "📋 *Commands:*\n"
            "/status - Bot status & account info\n"
            "/positions - View open positions\n"
            "/pnl - Today's P&L\n"
            "/performance - Overall performance\n"
            "/weekly - Weekly report\n"
            "/trades - Recent trade history\n"
            "/chart - P&L chart\n"
            "/close [SYMBOL] - Close a position\n"
            "/closeall - Close all positions\n"
            "/startbot - Start trading\n"
            "/stopbot - Stop trading\n"
            "/settings - View current settings\n"
            "/mode - Switch paper/live mode\n"
            "/pairs - View trading pairs\n"
            "/risk - View risk status\n"
            "/copytraders - View copy trading status\n"
            "/copyadd - Add a leader UID\n"
            "/copyremove - Remove a leader\n"
            "/copyon - Enable copy trading\n"
            "/copyoff - Disable copy trading\n"
            "/copyscale - Set copy trade scale\n"
        )
        await update.message.reply_text(text, parse_mode="Markdown")

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update):
            return

        balance = self.risk_manager.get_account_balance()
        available = self.risk_manager.get_available_balance()
        positions = self.risk_manager.get_open_positions()
        daily = self.journal.get_daily_report()

        mode_emoji = "📝" if config.TRADING_MODE == "paper" else "🔴"

        text = (
            f"{mode_emoji} *Bot Status*\n"
            f"━━━━━━━━━━━━━━━\n"
            f"Mode: `{config.TRADING_MODE.upper()}`\n"
            f"Running: {'✅ Yes' if self.bot_running else '❌ No'}\n\n"
            f"💰 *Account*\n"
            f"Balance: `${balance:,.2f}`\n"
            f"Available: `${available:,.2f}`\n"
            f"Open Positions: `{len(positions)}/{config.MAX_OPEN_POSITIONS}`\n\n"
            f"📊 *Today*\n"
            f"Trades: `{daily['trades']}`\n"
            f"Win Rate: `{daily['win_rate']}%`\n"
            f"P&L: `${daily['pnl']:+.2f}`\n"
            f"Daily Drawdown: `${self.risk_manager.daily_pnl:+.2f}`\n"
        )
        await update.message.reply_text(text, parse_mode="Markdown")

    async def cmd_positions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update):
            return

        positions = self.trader.get_all_positions_info()
        if not positions:
            await update.message.reply_text("No open positions.")
            return

        text = "📊 *Open Positions*\n━━━━━━━━━━━━━━━\n\n"
        for pos in positions:
            pnl_emoji = "🟢" if pos["unrealized_pnl"] >= 0 else "🔴"
            direction_emoji = "📈" if pos["direction"] == "LONG" else "📉"

            text += (
                f"{direction_emoji} *{pos['symbol']}* ({pos['direction']})\n"
                f"  Size: `{pos['size']}`\n"
                f"  Entry: `${pos['entry_price']:,.4f}`\n"
                f"  Current: `${pos['current_price']:,.4f}`\n"
                f"  SL: `${pos.get('stop_loss', 0):,.4f}`\n"
                f"  {pnl_emoji} PnL: `${pos['unrealized_pnl']:+.2f}`\n"
            )
            if pos.get("tp1_hit"):
                text += "  ✅ TP1 Hit\n"
            if pos.get("tp2_hit"):
                text += "  ✅ TP2 Hit\n"
            text += "\n"

        # Add close buttons
        keyboard = []
        for pos in positions:
            keyboard.append([
                InlineKeyboardButton(
                    f"Close {pos['symbol']}",
                    callback_data=f"close_{pos['symbol']}"
                )
            ])
        keyboard.append([InlineKeyboardButton("Close All", callback_data="close_all")])
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(text, parse_mode="Markdown",
                                         reply_markup=reply_markup)

    async def cmd_pnl(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update):
            return

        daily = self.journal.get_daily_report()
        perf = self.journal.get_performance_summary()

        text = (
            f"💰 *P&L Report*\n"
            f"━━━━━━━━━━━━━━━\n\n"
            f"📅 *Today ({daily['date']})*\n"
            f"  Trades: `{daily['trades']}`\n"
            f"  Wins: `{daily['wins']}` | Losses: `{daily['losses']}`\n"
            f"  Win Rate: `{daily['win_rate']}%`\n"
            f"  P&L: `${daily['pnl']:+.2f}`\n\n"
            f"📊 *All Time*\n"
            f"  Total Trades: `{perf['total_trades']}`\n"
            f"  Win Rate: `{perf['win_rate']}%`\n"
            f"  Total P&L: `${perf['total_pnl']:+.2f}`\n"
            f"  Avg P&L/Trade: `${perf['avg_pnl']:+.2f}`\n"
            f"  Best: `${perf['best_trade']:+.2f}`\n"
            f"  Worst: `${perf['worst_trade']:+.2f}`\n"
        )
        await update.message.reply_text(text, parse_mode="Markdown")

    async def cmd_performance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update):
            return

        perf = self.journal.get_performance_summary()

        win_bar = "🟢" * min(int(perf["win_rate"] / 10), 10)
        loss_bar = "🔴" * (10 - min(int(perf["win_rate"] / 10), 10))

        text = (
            f"📊 *Performance Dashboard*\n"
            f"━━━━━━━━━━━━━━━━━━━\n\n"
            f"Win Rate: {win_bar}{loss_bar} `{perf['win_rate']}%`\n\n"
            f"📈 *Stats*\n"
            f"  Total Trades: `{perf['total_trades']}`\n"
            f"  Wins: `{perf['wins']}` | Losses: `{perf['losses']}`\n"
            f"  Total P&L: `${perf['total_pnl']:+.2f}`\n"
            f"  Avg Trade: `${perf['avg_pnl']:+.2f}`\n"
            f"  Best Trade: `${perf['best_trade']:+.2f}`\n"
            f"  Worst Trade: `${perf['worst_trade']:+.2f}`\n"
            f"  Current Streak: `{perf['current_streak']}`\n"
            f"  Max Streak: `{perf['max_streak']}`\n"
        )
        await update.message.reply_text(text, parse_mode="Markdown")

    async def cmd_weekly(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update):
            return

        weekly = self.journal.get_weekly_report()
        text = (
            f"📅 *Weekly Report*\n"
            f"━━━━━━━━━━━━━━━\n"
            f"Period: `{weekly['period']}`\n\n"
            f"Trades: `{weekly['trades']}`\n"
            f"Wins: `{weekly['wins']}` | Losses: `{weekly['losses']}`\n"
            f"Win Rate: `{weekly['win_rate']}%`\n"
            f"P&L: `${weekly['pnl']:+.2f}`\n"
        )
        await update.message.reply_text(text, parse_mode="Markdown")

    async def cmd_trades(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update):
            return

        trades = self.journal.get_recent_trades(10)
        if not trades:
            await update.message.reply_text("No trades recorded yet.")
            return

        text = "📜 *Recent Trades*\n━━━━━━━━━━━━━━━\n\n"
        for t in reversed(trades):
            status_emoji = "✅" if t.get("pnl", 0) and t["pnl"] > 0 else "❌" if t.get("pnl", 0) and t["pnl"] < 0 else "⏳"
            direction = t.get("direction", "?")
            pnl = t.get("pnl", 0) or 0

            exit_price = t.get('exit_price')
            exit_text = f"Exit: `${exit_price:,.4f}`" if exit_price else "Status: Open"

            text += (
                f"{status_emoji} *{t['symbol']}* {direction}\n"
                f"  Entry: `${t['entry_price']:,.4f}`\n"
                f"  {exit_text}\n"
                f"  P&L: `${pnl:+.2f}`\n"
                f"  Conf: `{t.get('confidence', 0):.0%}`\n\n"
            )
        await update.message.reply_text(text, parse_mode="Markdown")

    async def cmd_chart(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update):
            return

        perf = self.journal._load_performance()
        daily = perf.get("daily_stats", {})

        if not daily:
            await update.message.reply_text("Not enough data for chart yet.")
            return

        filepath = self.chart_gen.generate_pnl_chart(daily)
        if filepath and os.path.exists(filepath):
            with open(filepath, "rb") as f:
                await update.message.reply_photo(photo=f,
                                                  caption="📊 P&L Performance")
        else:
            await update.message.reply_text("Error generating chart.")

    async def cmd_close(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update):
            return

        if not context.args:
            await update.message.reply_text("Usage: /close BTCUSDT")
            return

        symbol = context.args[0].upper()
        result = self.trader.close_position(symbol)

        if result["success"]:
            pnl = result.get("pnl", 0)
            await update.message.reply_text(
                f"✅ Closed {symbol}\nP&L: `${pnl:+.2f}`",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(f"❌ {result['reason']}")

    async def cmd_closeall(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update):
            return

        keyboard = [[
            InlineKeyboardButton("✅ Yes, close all", callback_data="confirm_closeall"),
            InlineKeyboardButton("❌ Cancel", callback_data="cancel"),
        ]]
        await update.message.reply_text(
            "⚠️ Close ALL positions?",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    async def cmd_startbot(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update):
            return
        self.bot_running = True
        await update.message.reply_text(
            f"✅ Bot STARTED in `{config.TRADING_MODE.upper()}` mode.\n"
            f"Scanning {len(config.TRADING_PAIRS)} pairs every {config.SCAN_INTERVAL}s.",
            parse_mode="Markdown"
        )

    async def cmd_stopbot(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update):
            return
        self.bot_running = False
        await update.message.reply_text("🛑 Bot STOPPED. No new trades will be opened.")

    async def cmd_settings(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update):
            return

        text = (
            f"⚙️ *Settings*\n"
            f"━━━━━━━━━━━━━━━\n"
            f"Mode: `{config.TRADING_MODE}`\n"
            f"Leverage: `{config.LEVERAGE}x`\n"
            f"Risk/Trade: `{config.MAX_RISK_PER_TRADE*100}%`\n"
            f"Daily Loss Limit: `{config.MAX_ACCOUNT_RISK*100}%`\n"
            f"Max Positions: `{config.MAX_OPEN_POSITIONS}`\n"
            f"Min RR Ratio: `1:{config.MIN_RISK_REWARD}`\n"
            f"Scan Interval: `{config.SCAN_INTERVAL}s`\n\n"
            f"*Timeframes:*\n"
            f"  Entry: `{config.TIMEFRAMES['entry']}`\n"
            f"  Confirm: `{config.TIMEFRAMES['confirm']}`\n"
            f"  Trend: `{config.TIMEFRAMES['trend']}`\n\n"
            f"*TP Levels:*\n"
            f"  TP1: `{config.TP1_RATIO}R` (close {config.TP1_CLOSE_PCT*100}%)\n"
            f"  TP2: `{config.TP2_RATIO}R` (close {config.TP2_CLOSE_PCT*100}%)\n"
            f"  Trailing: `{'On' if config.TP3_TRAIL else 'Off'}`\n"
        )
        await update.message.reply_text(text, parse_mode="Markdown")

    async def cmd_mode(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update):
            return

        keyboard = [[
            InlineKeyboardButton("📝 Paper", callback_data="mode_paper"),
            InlineKeyboardButton("🔴 Live", callback_data="mode_live"),
        ]]
        await update.message.reply_text(
            f"Current mode: `{config.TRADING_MODE}`\nSelect mode:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    async def cmd_pairs(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update):
            return

        text = "📋 *Trading Pairs*\n━━━━━━━━━━━━━━━\n\n"
        for pair in config.TRADING_PAIRS:
            text += f"• `{pair}`\n"
        await update.message.reply_text(text, parse_mode="Markdown")

    async def cmd_risk(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update):
            return

        balance = self.risk_manager.get_account_balance()
        max_loss = balance * config.MAX_ACCOUNT_RISK
        daily_pnl = self.risk_manager.daily_pnl
        remaining = max_loss + daily_pnl  # daily_pnl is negative when losing

        positions = self.risk_manager.get_open_positions()
        text = (
            f"🛡️ *Risk Status*\n"
            f"━━━━━━━━━━━━━━━\n\n"
            f"Balance: `${balance:,.2f}`\n"
            f"Daily P&L: `${daily_pnl:+.2f}`\n"
            f"Daily Loss Limit: `${max_loss:.2f}`\n"
            f"Remaining Risk: `${remaining:.2f}`\n"
            f"Circuit Breaker: {'🔴 ACTIVE' if daily_pnl < -max_loss else '🟢 OK'}\n\n"
            f"Open Positions: `{len(positions)}/{config.MAX_OPEN_POSITIONS}`\n"
        )

        # Liquidation risk for each position
        for pos in positions:
            liq = self.risk_manager.check_liquidation_risk(pos["symbol"], pos)
            if liq.get("at_risk"):
                text += f"⚠️ {pos['symbol']}: `{liq['distance_pct']}%` from liquidation!\n"

        await update.message.reply_text(text, parse_mode="Markdown")

    async def cmd_copytraders(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update):
            return

        if not config.COPY_TRADING_ENABLED:
            await update.message.reply_text(
                "Copy trading is disabled.\n"
                "Use /copyon to enable it.\n"
                "Use /copyadd <UID> to add leaders."
            )
            return

        if not self.copy_trader:
            await update.message.reply_text("Copy trader not initialized.")
            return

        leaders = [u.strip() for u in config.COPY_TRADER_UIDS if u.strip()]
        positions = self.copy_trader.get_copied_positions()

        text = "👥 *Copy Trading*\n━━━━━━━━━━━━━━━\n\n"
        text += f"Status: {'✅ Enabled' if config.COPY_TRADING_ENABLED else '❌ Disabled'}\n"
        text += f"Scale: `{config.COPY_TRADE_SCALE:.0%}` of leader size\n"
        text += f"Leaders: `{len(leaders)}`\n\n"

        if leaders:
            text += "*Leaders:*\n"
            for uid in leaders:
                text += f"  • `{uid[:12]}...`\n"
            text += "\n"

        if not positions:
            text += "No copied positions.\n"
        else:
            text += "*Open Positions:*\n\n"
            for pos in positions:
                direction_emoji = "📈" if pos["direction"] == "LONG" else "📉"
                text += (
                    f"{direction_emoji} *{pos['symbol']}* ({pos['direction']})\n"
                    f"  Leader: `{pos['leader_uid'][:8]}...`\n"
                    f"  Size: `{pos['size']}`\n"
                    f"  Entry: `${pos['entry_price']:,.4f}`\n"
                    f"  Leverage: `{pos['leverage']}x`\n\n"
                )

        await update.message.reply_text(text, parse_mode="Markdown")

    async def cmd_copyadd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update):
            return

        if not context.args:
            await update.message.reply_text("Usage: /copyadd <UID>\nExample: /copyadd ABC123DEF456")
            return

        uid = context.args[0].strip()

        # Validate UID: only alphanumeric, 16-40 chars (Binance encrypted UIDs)
        if not uid.isalnum() or len(uid) < 16 or len(uid) > 40:
            await update.message.reply_text("❌ Invalid UID. Must be 16-40 alphanumeric characters.")
            return

        # Check if already added
        existing = [u.strip() for u in config.COPY_TRADER_UIDS if u.strip()]
        if uid in existing:
            await update.message.reply_text(f"❌ Leader `{uid[:12]}...` is already added.", parse_mode="Markdown")
            return

        config.COPY_TRADER_UIDS.append(uid)
        config.save_settings()
        await update.message.reply_text(
            f"✅ Added leader `{uid[:12]}...`\n"
            f"Total leaders: `{len([u for u in config.COPY_TRADER_UIDS if u.strip()])}`\n\n"
            f"{'⚠️ Copy trading is disabled. Use /copyon to enable.' if not config.COPY_TRADING_ENABLED else ''}",
            parse_mode="Markdown"
        )

    async def cmd_copyremove(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update):
            return

        if not context.args:
            leaders = [u.strip() for u in config.COPY_TRADER_UIDS if u.strip()]
            if not leaders:
                await update.message.reply_text("No leaders to remove.")
                return

            # Show inline buttons for each leader
            keyboard = []
            for uid in leaders:
                keyboard.append([
                    InlineKeyboardButton(
                        f"Remove {uid[:12]}...",
                        callback_data=f"copyremove_{uid}"
                    )
                ])
            keyboard.append([InlineKeyboardButton("Cancel", callback_data="cancel")])
            await update.message.reply_text(
                "Select a leader to remove:",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            return

        uid = context.args[0].strip()

        # Validate input
        if not uid.isalnum():
            await update.message.reply_text("❌ Invalid UID.")
            return

        # Find and remove (match by full UID or prefix)
        removed = False
        new_uids = []
        for existing_uid in config.COPY_TRADER_UIDS:
            existing_uid = existing_uid.strip()
            if not existing_uid:
                continue
            if existing_uid == uid or existing_uid.startswith(uid):
                removed = True
            else:
                new_uids.append(existing_uid)

        if removed:
            config.COPY_TRADER_UIDS = new_uids
            config.save_settings()
            await update.message.reply_text(
                f"✅ Removed leader `{uid[:12]}...`\n"
                f"Remaining leaders: `{len(new_uids)}`",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(f"❌ Leader not found.")

    async def cmd_copyon(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update):
            return

        leaders = [u.strip() for u in config.COPY_TRADER_UIDS if u.strip()]
        if not leaders:
            await update.message.reply_text(
                "❌ No leaders added yet.\n"
                "Use /copyadd <UID> to add a leader first."
            )
            return

        config.COPY_TRADING_ENABLED = True
        config.save_settings()
        await update.message.reply_text(
            f"✅ Copy trading ENABLED\n"
            f"Tracking `{len(leaders)}` leader(s)\n"
            f"Scale: `{config.COPY_TRADE_SCALE:.0%}`",
            parse_mode="Markdown"
        )

    async def cmd_copyoff(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update):
            return

        config.COPY_TRADING_ENABLED = False
        config.save_settings()
        await update.message.reply_text("🛑 Copy trading DISABLED. No new trades will be copied.")

    async def cmd_copyscale(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update):
            return

        if not context.args:
            await update.message.reply_text(
                f"Current scale: `{config.COPY_TRADE_SCALE:.0%}`\n\n"
                f"Usage: /copyscale <value>\n"
                f"Example: /copyscale 0.1 (10% of leader size)\n"
                f"Range: 0.01 to 1.0",
                parse_mode="Markdown"
            )
            return

        try:
            scale = float(context.args[0])
        except ValueError:
            await update.message.reply_text("❌ Invalid number. Example: /copyscale 0.1")
            return

        if scale < 0.01 or scale > 1.0:
            await update.message.reply_text("❌ Scale must be between 0.01 (1%) and 1.0 (100%).")
            return

        old_scale = config.COPY_TRADE_SCALE
        config.COPY_TRADE_SCALE = scale
        config.save_settings()
        await update.message.reply_text(
            f"✅ Copy trade scale updated\n"
            f"Old: `{old_scale:.0%}` → New: `{scale:.0%}`",
            parse_mode="Markdown"
        )

    # =========================================================================
    # CALLBACK HANDLERS
    # =========================================================================
    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()

        if not self._authorized(update):
            return

        data = query.data

        if data.startswith("close_"):
            symbol = data.replace("close_", "")
            result = self.trader.close_position(symbol)
            if result["success"]:
                await query.edit_message_text(
                    f"✅ Closed {symbol}\nP&L: `${result.get('pnl', 0):+.2f}`",
                    parse_mode="Markdown"
                )
            else:
                await query.edit_message_text(f"❌ {result['reason']}")

        elif data == "confirm_closeall":
            results = self.trader.close_all_positions()
            text = "✅ *All Positions Closed*\n\n"
            for r in results:
                text += f"  {r['symbol']}: `${r.get('pnl', 0):+.2f}`\n"
            await query.edit_message_text(text, parse_mode="Markdown")

        elif data == "cancel":
            await query.edit_message_text("Cancelled.")

        elif data == "mode_paper":
            config.TRADING_MODE = "paper"
            config.save_settings()
            await query.edit_message_text("✅ Switched to PAPER mode.")

        elif data == "mode_live":
            config.TRADING_MODE = "live"
            config.save_settings()
            await query.edit_message_text(
                "🔴 Switched to LIVE mode.\n⚠️ Real money is now at risk!"
            )

        elif data.startswith("copyremove_"):
            uid = data.replace("copyremove_", "")
            # Validate before using
            if not uid.isalnum():
                await query.edit_message_text("❌ Invalid UID.")
                return
            new_uids = [u.strip() for u in config.COPY_TRADER_UIDS
                        if u.strip() and u.strip() != uid]
            removed = len(new_uids) < len([u for u in config.COPY_TRADER_UIDS if u.strip()])
            config.COPY_TRADER_UIDS = new_uids
            if removed:
                config.save_settings()
                await query.edit_message_text(
                    f"✅ Removed leader `{uid[:12]}...`\n"
                    f"Remaining: `{len(new_uids)}`",
                    parse_mode="Markdown"
                )
            else:
                await query.edit_message_text("❌ Leader not found.")

    # =========================================================================
    # ALERT METHODS (called from main loop)
    # =========================================================================
    async def send_signal_alert(self, signal, trade_result: dict, chart_path: str = ""):
        """Send trade alert to Telegram."""
        if not self.app:
            return

        direction_emoji = "📈" if signal.direction == "LONG" else "📉"
        text = (
            f"{direction_emoji} *NEW TRADE: {signal.symbol}*\n"
            f"━━━━━━━━━━━━━━━━━━━\n\n"
            f"Direction: `{signal.direction}`\n"
            f"Entry: `${signal.entry_price:,.4f}`\n"
            f"Stop Loss: `${signal.stop_loss:,.4f}`\n"
            f"Take Profit: `${signal.take_profit:,.4f}`\n"
            f"Size: `{trade_result.get('size', 0)}`\n"
            f"Risk: `${trade_result.get('risk_amount', 0):.2f}`\n"
            f"Confidence: `{signal.confidence:.0%}`\n\n"
            f"*Reasons:*\n"
        )
        for reason in signal.reasons:
            text += f"  • {reason}\n"

        text += f"\nMode: `{config.TRADING_MODE.upper()}`"

        bot = self.app.bot
        if chart_path and os.path.exists(chart_path):
            with open(chart_path, "rb") as f:
                await bot.send_photo(
                    chat_id=config.TELEGRAM_CHAT_ID,
                    photo=f,
                    caption=text, parse_mode="Markdown"
                )
        else:
            await bot.send_message(
                chat_id=config.TELEGRAM_CHAT_ID,
                text=text, parse_mode="Markdown"
            )

    async def send_event_alert(self, event: dict):
        """Send position event (TP hit, stopped out, etc.)."""
        if not self.app:
            return

        event_type = event.get("event", "")
        symbol = event.get("symbol", "")
        pnl = event.get("pnl", 0)

        emoji_map = {
            "TP1_HIT": "🎯",
            "TP2_HIT": "🎯🎯",
            "STOPPED_OUT": "🛑",
            "ALL_TP_HIT": "🏆",
            "LIQUIDATION_WARNING": "⚠️🔥",
        }
        emoji = emoji_map.get(event_type, "📢")

        text = (
            f"{emoji} *{event_type}: {symbol}*\n"
            f"Price: `${event.get('price', 0):,.4f}`\n"
            f"P&L: `${pnl:+.2f}`\n"
        )

        if event_type == "LIQUIDATION_WARNING":
            text += f"Distance to liq: `{event.get('distance_pct', 0)}%`\n"
            text += "⚠️ Position auto-reduced!"

        await self.app.bot.send_message(
            chat_id=config.TELEGRAM_CHAT_ID,
            text=text, parse_mode="Markdown"
        )

    async def send_daily_report(self):
        """Send automated daily performance report."""
        if not self.app:
            return

        daily = self.journal.get_daily_report()
        perf = self.journal.get_performance_summary()

        text = (
            f"📊 *Daily Report*\n"
            f"━━━━━━━━━━━━━━━\n"
            f"Date: `{daily['date']}`\n\n"
            f"*Today:*\n"
            f"  Trades: `{daily['trades']}`\n"
            f"  Win Rate: `{daily['win_rate']}%`\n"
            f"  P&L: `${daily['pnl']:+.2f}`\n\n"
            f"*Overall:*\n"
            f"  Total Trades: `{perf['total_trades']}`\n"
            f"  Win Rate: `{perf['win_rate']}%`\n"
            f"  Total P&L: `${perf['total_pnl']:+.2f}`\n"
        )

        await self.app.bot.send_message(
            chat_id=config.TELEGRAM_CHAT_ID,
            text=text, parse_mode="Markdown"
        )

    async def send_copy_alert(self, result: dict):
        """Send copy trade alert to Telegram."""
        if not self.app:
            return

        action = result.get("action", "")
        symbol = result.get("symbol", "")
        leader = result.get("leader", "?")

        if action == "COPY_OPEN":
            text = (
                f"👥 *COPY TRADE OPENED*\n"
                f"━━━━━━━━━━━━━━━━━━━\n\n"
                f"Leader: `{leader}`\n"
                f"Symbol: `{symbol}`\n"
                f"Direction: `{result.get('direction', '?')}`\n"
                f"Size: `{result.get('size', 0)}`\n"
                f"Mode: `{result.get('mode', '?').upper()}`\n"
            )
        elif action == "COPY_CLOSE":
            pnl = result.get("pnl", 0)
            text = (
                f"👥 *COPY TRADE CLOSED*\n"
                f"━━━━━━━━━━━━━━━━━━━\n\n"
                f"Leader: `{leader}`\n"
                f"Symbol: `{symbol}`\n"
            )
            if pnl:
                text += f"P&L: `${pnl:+.2f}`\n"
        else:
            return

        await self.app.bot.send_message(
            chat_id=config.TELEGRAM_CHAT_ID,
            text=text, parse_mode="Markdown"
        )

    # =========================================================================
    # SETUP
    # =========================================================================
    def setup(self) -> Application:
        """Build and return the Telegram application."""
        self.app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

        # Register command handlers
        self.app.add_handler(CommandHandler("start", self.cmd_start))
        self.app.add_handler(CommandHandler("help", self.cmd_start))
        self.app.add_handler(CommandHandler("status", self.cmd_status))
        self.app.add_handler(CommandHandler("positions", self.cmd_positions))
        self.app.add_handler(CommandHandler("pnl", self.cmd_pnl))
        self.app.add_handler(CommandHandler("performance", self.cmd_performance))
        self.app.add_handler(CommandHandler("weekly", self.cmd_weekly))
        self.app.add_handler(CommandHandler("trades", self.cmd_trades))
        self.app.add_handler(CommandHandler("chart", self.cmd_chart))
        self.app.add_handler(CommandHandler("close", self.cmd_close))
        self.app.add_handler(CommandHandler("closeall", self.cmd_closeall))
        self.app.add_handler(CommandHandler("startbot", self.cmd_startbot))
        self.app.add_handler(CommandHandler("stopbot", self.cmd_stopbot))
        self.app.add_handler(CommandHandler("settings", self.cmd_settings))
        self.app.add_handler(CommandHandler("mode", self.cmd_mode))
        self.app.add_handler(CommandHandler("pairs", self.cmd_pairs))
        self.app.add_handler(CommandHandler("risk", self.cmd_risk))
        self.app.add_handler(CommandHandler("copytraders", self.cmd_copytraders))
        self.app.add_handler(CommandHandler("copyadd", self.cmd_copyadd))
        self.app.add_handler(CommandHandler("copyremove", self.cmd_copyremove))
        self.app.add_handler(CommandHandler("copyon", self.cmd_copyon))
        self.app.add_handler(CommandHandler("copyoff", self.cmd_copyoff))
        self.app.add_handler(CommandHandler("copyscale", self.cmd_copyscale))
        self.app.add_handler(CallbackQueryHandler(self.button_handler))

        return self.app
