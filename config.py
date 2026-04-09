import json
import os
from dotenv import load_dotenv

load_dotenv()

# =============================================================================
# API KEYS
# =============================================================================
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# =============================================================================
# TRADING MODE
# =============================================================================
TRADING_MODE = os.getenv("TRADING_MODE", "paper")  # "paper" or "live"

# =============================================================================
# TRADING PAIRS
# =============================================================================
TRADING_PAIRS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "MATICUSDT"
]

# =============================================================================
# TIMEFRAMES FOR MULTI-TIMEFRAME ANALYSIS
# =============================================================================
TIMEFRAMES = {
    "entry": "15m",       # Entry signal timeframe
    "confirm": "1h",      # Confirmation timeframe
    "trend": "4h",        # Trend direction timeframe
}

# =============================================================================
# STRATEGY PARAMETERS
# =============================================================================
# EMA
EMA_FAST = 9
EMA_SLOW = 21
EMA_TREND = 50

# RSI
RSI_PERIOD = 14
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30

# MACD
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

# ATR for trailing stop
ATR_PERIOD = 14
ATR_MULTIPLIER = 2.0

# Volume
VOLUME_MA_PERIOD = 20
VOLUME_THRESHOLD = 1.5  # 1.5x average volume

# Bollinger Bands
BB_PERIOD = 20
BB_STD = 2.0

# =============================================================================
# RISK MANAGEMENT
# =============================================================================
MAX_RISK_PER_TRADE = 0.02       # 2% of account per trade
MAX_ACCOUNT_RISK = 0.06         # 6% daily loss limit - circuit breaker
MIN_RISK_REWARD = 2.0           # Minimum 1:2 risk-reward ratio
MAX_OPEN_POSITIONS = 3          # Max concurrent positions
MAX_CORRELATED_POSITIONS = 2    # Max positions in correlated assets
LEVERAGE = 5                    # Default leverage

# =============================================================================
# TAKE PROFIT LEVELS (partial exits)
# =============================================================================
TP1_RATIO = 1.5    # Take 50% profit at 1.5R
TP2_RATIO = 2.5    # Take 25% profit at 2.5R
TP3_TRAIL = True   # Trail remaining 25% with ATR stop

TP1_CLOSE_PCT = 0.50   # Close 50% at TP1
TP2_CLOSE_PCT = 0.25   # Close 25% at TP2

# =============================================================================
# SESSION FILTERING (UTC hours)
# =============================================================================
ACTIVE_SESSIONS = {
    "london_open": (7, 8),
    "ny_open": (13, 14),
    "overlap": (13, 16),   # London/NY overlap - highest liquidity
    "asian": (0, 3),
}
TRADE_ALL_SESSIONS = True  # Set False to only trade during active sessions

# =============================================================================
# FUNDING RATE THRESHOLDS
# =============================================================================
FUNDING_RATE_EXTREME_LONG = 0.01    # >1% funding = overcrowded long
FUNDING_RATE_EXTREME_SHORT = -0.01  # <-1% funding = overcrowded short

# =============================================================================
# OPEN INTEREST THRESHOLDS
# =============================================================================
OI_CHANGE_THRESHOLD = 0.05  # 5% change in OI is significant

# =============================================================================
# CORRELATION GROUPS (assets that move together)
# =============================================================================
CORRELATION_GROUPS = {
    "btc_ecosystem": ["BTCUSDT"],
    "eth_ecosystem": ["ETHUSDT"],
    "large_alts": ["BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "AVAXUSDT"],
    "meme": ["DOGEUSDT"],
    "defi": ["DOTUSDT", "MATICUSDT"],
}

# =============================================================================
# COPY TRADING
# =============================================================================
COPY_TRADING_ENABLED = os.getenv("COPY_TRADING_ENABLED", "false").lower() == "true"
COPY_TRADER_UIDS = os.getenv("COPY_TRADER_UIDS", "").split(",")  # Binance leaderboard UIDs
COPY_TRADE_SCALE = float(os.getenv("COPY_TRADE_SCALE", "0.1"))   # 10% of their size
COPY_TRADE_MAX_RISK = 0.03   # Max 3% risk per copied trade
COPY_CHECK_INTERVAL = 15     # Check leader positions every 15 seconds

# =============================================================================
# LOGGING & DATA
# =============================================================================
LOG_DIR = "logs"
DATA_DIR = "data"
CHART_DIR = "charts"
JOURNAL_FILE = "data/trade_journal.json"
PERFORMANCE_FILE = "data/performance.json"

# =============================================================================
# BOT TIMING
# =============================================================================
SCAN_INTERVAL = 60          # Scan for signals every 60 seconds
POSITION_CHECK_INTERVAL = 30  # Check positions every 30 seconds
REPORT_HOUR = 8             # Daily report at 8:00 UTC

# =============================================================================
# SETTINGS PERSISTENCE
# =============================================================================
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")


def save_settings():
    """Save runtime-changeable settings to disk so they survive restarts."""
    os.makedirs(DATA_DIR, exist_ok=True)
    data = {
        "trading_mode": TRADING_MODE,
        "copy_trading_enabled": COPY_TRADING_ENABLED,
        "copy_trader_uids": [u.strip() for u in COPY_TRADER_UIDS if u.strip()],
        "copy_trade_scale": COPY_TRADE_SCALE,
    }
    tmp_path = SETTINGS_FILE + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, SETTINGS_FILE)


def load_settings():
    """Load saved settings from disk. Call once at startup."""
    global TRADING_MODE, COPY_TRADING_ENABLED, COPY_TRADER_UIDS, COPY_TRADE_SCALE
    if not os.path.exists(SETTINGS_FILE):
        return
    try:
        with open(SETTINGS_FILE, "r") as f:
            data = json.load(f)
        TRADING_MODE = data.get("trading_mode", TRADING_MODE)
        COPY_TRADING_ENABLED = data.get("copy_trading_enabled", COPY_TRADING_ENABLED)
        saved_uids = data.get("copy_trader_uids", None)
        if saved_uids is not None:
            COPY_TRADER_UIDS = saved_uids
        saved_scale = data.get("copy_trade_scale", None)
        if saved_scale is not None:
            COPY_TRADE_SCALE = float(saved_scale)
    except (json.JSONDecodeError, IOError):
        pass  # Corrupted file — fall back to .env defaults
