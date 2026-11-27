"""All settings in one place. Anything secret or account-specific comes from Vercel environment variables."""
import os


def _f(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


# ---- mode: testnet (fake money), approval (real money, you approve each buy), live (real money, automatic)
MODE = os.environ.get("BOT_MODE", "testnet").strip().lower()
if MODE not in ("testnet", "approval", "live"):
    MODE = "testnet"
REAL_MONEY = MODE in ("approval", "live")

# ---- money limits (hard caps: the bot never spends more than BUDGET_USDT in total)
BUDGET_USDT = _f("BUDGET_USDT", 12)          # the slice of your account the bot may use
RISK_PCT = 0.015                             # paper bot v6.2 sizing: a stop-out loses ~1.5% of the balance...
MIN_TRADE_USDT = 6.0                         # ...but never below $6 (Binance's $5 minimum must still hold after a stop)
MAX_TRADE_USDT = _f("MAX_TRADE_USDT", 1000)  # optional hard cap per buy
MAX_POS = int(_f("MAX_POS", 1))                # 1 open trade at a time: at ~$20 with a $6 min order, 2 slots backtest -26% vs +70% (Nov 27 test)

# ---- strategy v6.2 (same as the paper bot): buy dips when >= 2 of 4 oversold signals agree
MR_VOTES = ("rsi_30_70", "keltner", "bollinger", "zscore_50")
MR_MIN = 2
STOP_MULT, STOP_MIN, STOP_MAX = 2.5, 0.025, 0.06     # stop = 2.5x hourly ATR, between 2.5% and 6%
TGT_R, TGT_MAX = 2.0, 0.12                           # target = 2x stop distance, max 12%
BE_AT_R = 1.5                                        # move stop to breakeven once up 1.5x the stop distance
TRAIL_AT_R, TRAIL_GAP_R = 2.0, 0.75                  # then trail 0.75x the stop distance below the peak
STOP_LIMIT_GAP = 0.015                               # stop-limit order sells down to 1.5% below the stop price
MAX_HOLD_H, EXT_HOLD_H, EXTEND_MIN_GAIN = 24, 48, 0.003
EXTEND_MIN_SCORE = 0.20                              # keep past 24h only if up >0.3% AND 29-strategy score >= 0.20
RUNNER_SCORE = 0.40                                  # near target with score >= 0.40 and Supertrend up: drop target, trail
MAX_ATR_PCT, MIN_ATR_PCT = 0.04, 0.002
MAX_SPREAD = 0.003
RECENT_LOSER_H = 6
TOP_N = int(_f("TOP_N", 150))                        # scan the most-traded coins only (fits Vercel's free limits)
MIN_QUOTE_VOL = 20_000
APPROVAL_TTL_MIN = 30
APPROVAL_MAX_DRIFT = 0.015                           # approved buy is cancelled if price moved >1.5% since the alert

STABLES = {"USDC", "FDUSD", "TUSD", "USDP", "DAI", "BUSD", "EUR", "USD1", "XUSD", "AEUR", "EURI",
           "PAXG", "USDE", "BFUSD", "RLUSD", "USDS", "PYUSD", "U", "XAUT", "RWUSD", "KGST", "BRL", "TRY", "IDR"}
CRYPTO_ENDING_B = {"ARB", "BB", "BNB", "CKB", "DGB", "SHIB", "TRB", "YB"}
LEVERAGED = ("UP", "DOWN", "BULL", "BEAR")

# ---- endpoints
DATA_API = "https://data-api.binance.vision/api/v3"        # real market prices (public, read-only)
TRADE_API = "https://testnet.binance.vision/api/v3" if MODE == "testnet" else "https://api.binance.com/api/v3"

# ---- secrets (Vercel environment variables, never in the code)
BINANCE_KEY = os.environ.get("BINANCE_KEY", "")
BINANCE_SECRET = os.environ.get("BINANCE_SECRET", "")
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "")
DISCORD_USER_ID = os.environ.get("DISCORD_USER_ID", "")
CONTROL_TOKEN = os.environ.get("CONTROL_TOKEN", "")
CRON_SECRET = os.environ.get("CRON_SECRET", "")
