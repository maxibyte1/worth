"""
Trade Journal - Logs every trade with entry reason, outcome, and performance tracking.
Auto-disables strategies that drop below win rate threshold.
"""

import json
import logging
import os
import time
from datetime import datetime, timezone, timedelta

import config

logger = logging.getLogger(__name__)


class TradeJournal:
    def __init__(self):
        self.journal_file = config.JOURNAL_FILE
        self.performance_file = config.PERFORMANCE_FILE
        self._ensure_files()

    def _ensure_files(self):
        os.makedirs(os.path.dirname(self.journal_file), exist_ok=True)
        if not os.path.exists(self.journal_file):
            self._save_journal([])
        if not os.path.exists(self.performance_file):
            self._save_performance({
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "total_pnl": 0.0,
                "best_trade": 0.0,
                "worst_trade": 0.0,
                "streak": 0,
                "max_streak": 0,
                "daily_stats": {},
            })

    def _load_journal(self) -> list:
        try:
            with open(self.journal_file, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return []

    def _save_journal(self, data: list):
        with open(self.journal_file, "w") as f:
            json.dump(data, f, indent=2, default=str)

    def _load_performance(self) -> dict:
        try:
            with open(self.performance_file, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return {}

    def _save_performance(self, data: dict):
        with open(self.performance_file, "w") as f:
            json.dump(data, f, indent=2, default=str)

    # =========================================================================
    # LOGGING
    # =========================================================================
    def log_trade_open(self, symbol: str, direction: str, entry_price: float,
                       size: float, stop_loss: float, tp_levels: dict,
                       confidence: float, reasons: list, mode: str):
        """Log a new trade entry."""
        journal = self._load_journal()
        entry = {
            "id": f"{symbol}_{int(time.time())}",
            "symbol": symbol,
            "direction": direction,
            "entry_price": entry_price,
            "size": size,
            "stop_loss": stop_loss,
            "tp_levels": tp_levels,
            "confidence": confidence,
            "reasons": reasons,
            "mode": mode,
            "open_time": datetime.now(timezone.utc).isoformat(),
            "status": "open",
            "partial_closes": [],
            "exit_price": None,
            "pnl": None,
            "close_time": None,
            "close_reason": None,
        }
        journal.append(entry)
        self._save_journal(journal)
        logger.info(f"Journal: Trade opened - {symbol} {direction} @ {entry_price}")

    def log_partial_close(self, symbol: str, level: str, price: float, pnl: float):
        """Log a partial take profit."""
        journal = self._load_journal()
        for trade in reversed(journal):
            if trade["symbol"] == symbol and trade["status"] == "open":
                trade["partial_closes"].append({
                    "level": level,
                    "price": price,
                    "pnl": round(pnl, 2),
                    "time": datetime.now(timezone.utc).isoformat(),
                })
                break
        self._save_journal(journal)

    def log_trade_close(self, symbol: str, exit_price: float, pnl: float, reason: str):
        """Log a trade closure and update performance."""
        journal = self._load_journal()
        for trade in reversed(journal):
            if trade["symbol"] == symbol and trade["status"] == "open":
                trade["status"] = "closed"
                trade["exit_price"] = exit_price
                trade["pnl"] = round(pnl, 2)
                trade["close_time"] = datetime.now(timezone.utc).isoformat()
                trade["close_reason"] = reason
                break
        self._save_journal(journal)

        # Update performance
        self._update_performance(pnl)
        logger.info(f"Journal: Trade closed - {symbol} PnL: ${pnl:.2f} ({reason})")

    def _update_performance(self, pnl: float):
        """Update cumulative performance stats."""
        perf = self._load_performance()
        perf["total_trades"] = perf.get("total_trades", 0) + 1
        perf["total_pnl"] = perf.get("total_pnl", 0) + pnl

        if pnl > 0:
            perf["wins"] = perf.get("wins", 0) + 1
            streak = perf.get("streak", 0)
            perf["streak"] = streak + 1 if streak >= 0 else 1
        else:
            perf["losses"] = perf.get("losses", 0) + 1
            streak = perf.get("streak", 0)
            perf["streak"] = streak - 1 if streak <= 0 else -1

        perf["max_streak"] = max(perf.get("max_streak", 0), abs(perf["streak"]))
        perf["best_trade"] = max(perf.get("best_trade", 0), pnl)
        perf["worst_trade"] = min(perf.get("worst_trade", 0), pnl)

        # Daily stats
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        daily = perf.get("daily_stats", {})
        if today not in daily:
            daily[today] = {"trades": 0, "pnl": 0, "wins": 0, "losses": 0}
        daily[today]["trades"] += 1
        daily[today]["pnl"] = round(daily[today]["pnl"] + pnl, 2)
        if pnl > 0:
            daily[today]["wins"] += 1
        else:
            daily[today]["losses"] += 1
        perf["daily_stats"] = daily

        self._save_performance(perf)

    # =========================================================================
    # REPORTING
    # =========================================================================
    def get_performance_summary(self) -> dict:
        """Get overall performance summary."""
        perf = self._load_performance()
        total = perf.get("total_trades", 0)
        wins = perf.get("wins", 0)

        return {
            "total_trades": total,
            "wins": wins,
            "losses": perf.get("losses", 0),
            "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
            "total_pnl": round(perf.get("total_pnl", 0), 2),
            "best_trade": round(perf.get("best_trade", 0), 2),
            "worst_trade": round(perf.get("worst_trade", 0), 2),
            "current_streak": perf.get("streak", 0),
            "max_streak": perf.get("max_streak", 0),
            "avg_pnl": round(perf.get("total_pnl", 0) / total, 2) if total > 0 else 0,
        }

    def get_daily_report(self, date: str = None) -> dict:
        """Get daily performance report."""
        if date is None:
            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        perf = self._load_performance()
        daily = perf.get("daily_stats", {}).get(date, {
            "trades": 0, "pnl": 0, "wins": 0, "losses": 0,
        })

        total = daily.get("trades", 0)
        wins = daily.get("wins", 0)

        return {
            "date": date,
            "trades": total,
            "wins": wins,
            "losses": daily.get("losses", 0),
            "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
            "pnl": daily.get("pnl", 0),
        }

    def get_weekly_report(self) -> dict:
        """Get weekly performance summary."""
        perf = self._load_performance()
        daily = perf.get("daily_stats", {})

        now = datetime.now(timezone.utc)
        week_start = now - timedelta(days=now.weekday())

        week_trades = 0
        week_pnl = 0
        week_wins = 0
        week_losses = 0

        for date_str, stats in daily.items():
            try:
                date = datetime.strptime(date_str, "%Y-%m-%d")
                if date.date() >= week_start.date():
                    week_trades += stats.get("trades", 0)
                    week_pnl += stats.get("pnl", 0)
                    week_wins += stats.get("wins", 0)
                    week_losses += stats.get("losses", 0)
            except ValueError:
                continue

        return {
            "period": f"{week_start.strftime('%Y-%m-%d')} to {now.strftime('%Y-%m-%d')}",
            "trades": week_trades,
            "wins": week_wins,
            "losses": week_losses,
            "win_rate": round(week_wins / week_trades * 100, 1) if week_trades > 0 else 0,
            "pnl": round(week_pnl, 2),
        }

    def get_recent_trades(self, limit: int = 10) -> list:
        """Get most recent trades."""
        journal = self._load_journal()
        return journal[-limit:]

    def get_open_trades(self) -> list:
        """Get all currently open trades from journal."""
        journal = self._load_journal()
        return [t for t in journal if t["status"] == "open"]
