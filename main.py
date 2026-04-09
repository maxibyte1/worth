"""
Worth AI Trading Bot - Main entry point.
Runs 24/7 with Telegram bot interface and automated trading.
"""

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone

from binance.client import Client

import config
from market_analyzer import MarketAnalyzer
from strategy import Strategy
from risk_manager import RiskManager
from trader import Trader
from journal import TradeJournal
from chart_generator import ChartGenerator
from telegram_bot import TelegramBot
from copy_trader import CopyTrader

# =============================================================================
# LOGGING SETUP
# =============================================================================
os.makedirs(config.LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(config.LOG_DIR, "bot.log")),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("main")


class TradingEngine:
    """Main engine that orchestrates scanning, trading, and position management."""

    def __init__(self):
        # Load persisted settings (overrides .env with saved Telegram changes)
        config.load_settings()
        logger.info("=" * 60)
        logger.info("  Worth AI Trading Bot Starting...")
        logger.info(f"  Mode: {config.TRADING_MODE.upper()}")
        logger.info(f"  Pairs: {len(config.TRADING_PAIRS)}")
        logger.info(f"  Leverage: {config.LEVERAGE}x")
        logger.info("=" * 60)

        # Initialize Binance client
        self.client = None
        try:
            if config.TRADING_MODE == "paper" and (not config.BINANCE_API_KEY):
                logger.warning("No API keys found. Using testnet for paper trading.")
                self.client = Client(
                    config.BINANCE_API_KEY,
                    config.BINANCE_API_SECRET,
                    testnet=True,
                )
            else:
                self.client = Client(
                    config.BINANCE_API_KEY,
                    config.BINANCE_API_SECRET,
                )
        except Exception as e:
            logger.error(f"Binance connection failed: {e}")
            logger.warning("Bot will start in Telegram-only mode. Trading disabled.")

        # Initialize components
        self.journal = TradeJournal()
        self.chart_gen = ChartGenerator()
        self.market_analyzer = MarketAnalyzer(self.client)
        self.strategy = Strategy(self.client, self.market_analyzer)
        self.risk_manager = RiskManager(self.client)
        self.trader = Trader(self.client, self.risk_manager, self.journal)
        self.copy_trader = CopyTrader(self.client, self.risk_manager, self.journal)
        self.telegram = TelegramBot(
            self.trader, self.risk_manager, self.journal, self.chart_gen
        )
        self.telegram.copy_trader = self.copy_trader

        self._last_report_date = None

    async def scan_and_trade(self):
        """Main scan loop - find signals and execute trades."""
        if not self.telegram.bot_running:
            return

        logger.info("Scanning for signals...")
        try:
            signals = self.strategy.scan_all_pairs()

            for signal in signals:
                # Execute the trade
                result = self.trader.execute_signal(signal)
                if result and result.get("success"):
                    logger.info(f"Trade executed: {signal.symbol} {signal.direction}")

                    # Generate chart
                    df = self.strategy.get_klines(
                        signal.symbol, config.TIMEFRAMES["entry"]
                    )
                    chart_path = ""
                    if df is not None:
                        chart_path = self.chart_gen.generate_signal_chart(
                            df, signal.symbol, signal.direction,
                            signal.entry_price, signal.stop_loss,
                            signal.take_profit,
                        )

                    # Send Telegram alert
                    await self.telegram.send_signal_alert(
                        signal, result, chart_path
                    )
                elif result:
                    logger.info(f"Trade blocked: {signal.symbol} - {result.get('reason')}")

        except Exception as e:
            logger.error(f"Error in scan loop: {e}", exc_info=True)

    async def manage_positions(self):
        """Position management loop - trailing stops, TP checks, liquidation."""
        try:
            if config.TRADING_MODE == "paper":
                events = self.trader.update_paper_positions()
            else:
                events = self.trader.update_live_positions()

            for event in events:
                logger.info(f"Position event: {event}")
                await self.telegram.send_event_alert(event)

        except Exception as e:
            logger.error(f"Error in position management: {e}", exc_info=True)

    async def check_daily_report(self):
        """Send daily report at configured hour."""
        now = datetime.now(timezone.utc)
        today = now.date()

        if (now.hour == config.REPORT_HOUR and
                self._last_report_date != today):
            self._last_report_date = today
            await self.telegram.send_daily_report()
            logger.info("Daily report sent")

    async def copy_trade_loop(self):
        """Check leader positions and mirror trades."""
        if not config.COPY_TRADING_ENABLED:
            return
        try:
            events = await self.copy_trader.check_leaders()
            results = await self.copy_trader.process_events(events)
            for result in results:
                if result.get("success"):
                    action = result["action"]
                    symbol = result["symbol"]
                    leader = result.get("leader", "?")
                    if action == "COPY_OPEN":
                        await self.telegram.send_copy_alert(result)
                    elif action == "COPY_CLOSE":
                        await self.telegram.send_copy_alert(result)
                    logger.info(f"Copy trade: {action} {symbol} (leader: {leader})")
        except Exception as e:
            logger.error(f"Error in copy trade loop: {e}", exc_info=True)

    async def trading_loop(self):
        """Main async trading loop."""
        scan_counter = 0
        position_counter = 0
        copy_counter = 0

        while True:
            try:
                scan_counter += 1
                position_counter += 1
                copy_counter += 1

                # Scan for new signals every SCAN_INTERVAL
                if scan_counter >= config.SCAN_INTERVAL:
                    await self.scan_and_trade()
                    scan_counter = 0

                # Check positions every POSITION_CHECK_INTERVAL
                if position_counter >= config.POSITION_CHECK_INTERVAL:
                    await self.manage_positions()
                    position_counter = 0

                # Copy trading check
                if copy_counter >= config.COPY_CHECK_INTERVAL:
                    await self.copy_trade_loop()
                    copy_counter = 0

                # Daily report check
                await self.check_daily_report()

                # Sleep 1 second per tick
                await asyncio.sleep(1)

            except KeyboardInterrupt:
                logger.info("Bot stopped by user")
                break
            except Exception as e:
                logger.error(f"Error in main loop: {e}", exc_info=True)
                await asyncio.sleep(5)

    async def run(self):
        """Start the bot - Telegram + trading loop."""
        # Setup Telegram
        app = self.telegram.setup()

        # Initialize the application
        await app.initialize()
        await app.start()

        # Start polling for Telegram updates
        await app.updater.start_polling(drop_pending_updates=True)

        logger.info("Telegram bot started. Send /start to begin.")
        logger.info("Send /startbot to begin trading.")

        # Send startup message
        try:
            await app.bot.send_message(
                chat_id=config.TELEGRAM_CHAT_ID,
                text=(
                    f"🤖 *Worth AI Bot Online*\n\n"
                    f"Mode: `{config.TRADING_MODE.upper()}`\n"
                    f"Pairs: `{len(config.TRADING_PAIRS)}`\n"
                    f"Leverage: `{config.LEVERAGE}x`\n\n"
                    f"Send /startbot to begin trading.\n"
                    f"Send /help for all commands."
                ),
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.warning(f"Could not send startup message: {e}")

        # Run trading loop
        try:
            await self.trading_loop()
        finally:
            logger.info("Shutting down...")
            await app.updater.stop()
            await app.stop()
            await app.shutdown()


def main():
    engine = TradingEngine()
    asyncio.run(engine.run())


if __name__ == "__main__":
    main()
