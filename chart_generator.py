"""
Chart Generator - Creates chart images for Telegram alerts.
"""

import logging
import os

import matplotlib
matplotlib.use("Agg")  # Non-interactive backend
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import mplfinance as mpf
import pandas as pd

import config

logger = logging.getLogger(__name__)


class ChartGenerator:
    def __init__(self):
        os.makedirs(config.CHART_DIR, exist_ok=True)
        self.style = mpf.make_mpf_style(
            base_mpf_style="nightclouds",
            rc={"font.size": 8},
        )

    def generate_signal_chart(self, df: pd.DataFrame, symbol: str,
                               signal_type: str, entry: float,
                               stop_loss: float, take_profit: float) -> str:
        """Generate a candlestick chart with signal markers."""
        try:
            chart_df = df.tail(60).copy()
            chart_df.index = pd.DatetimeIndex(chart_df["open_time"])
            chart_df = chart_df[["open", "high", "low", "close", "volume"]]

            # Horizontal lines for entry, SL, TP
            hlines = dict(
                hlines=[entry, stop_loss, take_profit],
                colors=["white", "red", "green"],
                linestyle=["--", "-", "-"],
                linewidths=[1, 1.5, 1.5],
            )

            filepath = os.path.join(config.CHART_DIR, f"{symbol}_{signal_type}.png")

            fig, axes = mpf.plot(
                chart_df,
                type="candle",
                style=self.style,
                volume=True,
                title=f"\n{symbol} - {signal_type}",
                hlines=hlines,
                figsize=(10, 6),
                returnfig=True,
            )

            # Add labels
            ax = axes[0]
            ax.text(0.02, entry, f"Entry: {entry}", transform=ax.get_yaxis_transform(),
                    color="white", fontsize=7, va="bottom")
            ax.text(0.02, stop_loss, f"SL: {stop_loss}", transform=ax.get_yaxis_transform(),
                    color="red", fontsize=7, va="bottom")
            ax.text(0.02, take_profit, f"TP: {take_profit}", transform=ax.get_yaxis_transform(),
                    color="green", fontsize=7, va="bottom")

            fig.savefig(filepath, dpi=150, bbox_inches="tight",
                       facecolor="#0e1117", edgecolor="none")
            plt.close(fig)

            logger.info(f"Chart generated: {filepath}")
            return filepath

        except Exception as e:
            logger.error(f"Error generating chart for {symbol}: {e}")
            return ""

    def generate_pnl_chart(self, daily_stats: dict) -> str:
        """Generate a P&L curve chart."""
        try:
            if not daily_stats:
                return ""

            dates = sorted(daily_stats.keys())
            pnls = [daily_stats[d].get("pnl", 0) for d in dates]

            # Cumulative P&L
            cumulative = []
            total = 0
            for p in pnls:
                total += p
                cumulative.append(total)

            fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 6),
                                            facecolor="#0e1117")

            # Cumulative P&L
            ax1.set_facecolor("#0e1117")
            ax1.plot(dates, cumulative, color="#00ff88", linewidth=2)
            ax1.fill_between(dates, cumulative, alpha=0.3,
                            where=[c >= 0 for c in cumulative], color="#00ff88")
            ax1.fill_between(dates, cumulative, alpha=0.3,
                            where=[c < 0 for c in cumulative], color="#ff4444")
            ax1.set_title("Cumulative P&L", color="white", fontsize=12)
            ax1.tick_params(colors="white")
            ax1.grid(alpha=0.2)

            # Daily P&L bars
            ax2.set_facecolor("#0e1117")
            colors = ["#00ff88" if p >= 0 else "#ff4444" for p in pnls]
            ax2.bar(dates, pnls, color=colors, alpha=0.8)
            ax2.set_title("Daily P&L", color="white", fontsize=12)
            ax2.tick_params(colors="white")
            ax2.grid(alpha=0.2)

            plt.xticks(rotation=45)
            plt.tight_layout()

            filepath = os.path.join(config.CHART_DIR, "pnl_chart.png")
            fig.savefig(filepath, dpi=150, bbox_inches="tight",
                       facecolor="#0e1117")
            plt.close(fig)

            return filepath
        except Exception as e:
            logger.error(f"Error generating P&L chart: {e}")
            return ""
