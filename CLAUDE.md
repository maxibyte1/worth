# Worth AI Trading Bot

## Project Overview
Automated crypto futures trading bot on Binance with Telegram interface, copy trading, and risk management.

## Rules
- **No agents or superpowers skills** — work directly, don't waste credits
- **Security is critical** — this handles real money and API keys
  - NEVER log or expose API keys, secrets, or tokens
  - ALWAYS validate and sanitize all user input from Telegram commands
  - ALWAYS check authorization (`_authorized`) on every command handler
  - NEVER trust raw user input — validate UIDs, symbols, numeric values
  - Prevent command injection, path traversal, and arbitrary code execution
  - Keep `.env` out of git (already in `.gitignore`)
- **No bugs** — test edge cases, handle errors gracefully, don't break existing functionality
- **Don't over-engineer** — keep changes minimal and focused
- **Follow existing patterns** — match the code style already in the project

## Tech Stack
- Python 3.11+
- python-binance (Binance Futures API)
- python-telegram-bot (Telegram interface)
- pandas, numpy, ta (technical analysis)
- matplotlib (charts)

## Key Files
- `main.py` — entry point, orchestrates all components
- `telegram_bot.py` — Telegram command handlers and alerts
- `trader.py` — order execution, paper + live trading
- `strategy.py` — multi-timeframe signal generation
- `risk_manager.py` — position sizing, circuit breaker
- `copy_trader.py` — copy trading from Binance leaderboard
- `config.py` — all configuration, loaded from `.env`
- `journal.py` — trade journaling and performance tracking
- `chart_generator.py` — matplotlib chart generation

## Repository
- Remote: https://github.com/maxibyte1/worth.git
- Push all changes here

## Deployment
- Hosted on Railway
- Attach a **persistent volume** mounted at `/app/data` so `data/settings.json` survives deploys
- Runtime settings changed via Telegram are saved to `data/settings.json` and loaded on startup

## Configuration
All secrets in `.env` (see `.env.example`). Runtime config in `config.py`.
