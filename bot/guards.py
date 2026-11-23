"""Extra safety checks for trader.py: token-unlock calendar, whale flow (Binance big trades), loss limit."""
import json, os, statistics
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
UNLOCK_BEFORE_D, UNLOCK_AFTER_D, UNLOCK_MIN_PCT, UNLOCK_MIN_USD = 7, 1, 1.0, 5e6
WHALE_MIN_USD, WHALE_MULT, WHALE_SELL_RATIO, WHALE_SHARE, WHALE_MIN_SELL = 2_000, 20, 3.0, 0.15, 5_000
LOSS_DAY, LOSS_DAY_PAUSE_H, KILL_DD = 0.03, 6, 0.25


# ---------- token unlocks
def load_unlocks():
    try:
        return json.load(open(os.path.join(HERE, "unlocks.json")))
    except Exception:
        return {"events": [], "updated_utc": None}


def unlock_stale_days(U, now_ms):
    if not U.get("updated_utc"):
        return 99
    t = datetime.fromisoformat(U["updated_utc"].replace("Z", "+00:00")).timestamp() * 1000
    return (now_ms - t) / 86.4e6


def unlock_risk(U, coin, now_ms, before_d=UNLOCK_BEFORE_D, after_d=UNLOCK_AFTER_D):
    """Return the first significant unlock for `coin` inside the window, else None."""
    now = datetime.fromtimestamp(now_ms / 1000, timezone.utc)
    for e in U.get("events", []):
        if e["sym"].upper() != coin.upper():
            continue
        d = datetime.fromisoformat(e["date"]).replace(tzinfo=timezone.utc) + timedelta(hours=12)
        if not (now - timedelta(days=after_d) <= d <= now + timedelta(days=before_d)):
            continue
        big = (e.get("pct") is not None and e["pct"] >= UNLOCK_MIN_PCT) or \
              (e.get("pct") is None and (e.get("usd") or 0) >= UNLOCK_MIN_USD)
        if big:
            days = (d - now).total_seconds() / 86400
            when = f"in {days:.0f}d" if days >= 1 else ("today" if days > -1 else f"{-days:.0f}d ago")
            size = f"{e['pct']:.1f}% of supply" if e.get("pct") is not None else f"${e['usd'] / 1e6:.0f}M"
            return f"token unlock {e['date']} ({when}, {size})"
    return None


# ---------- whale flow from Binance big trades (last 60 min)
def whale_flow(get, sym, now_ms):
    try:
        tr = get(f"/aggTrades?symbol={sym}&limit=1000")
    except Exception:
        return None
    tr = [t for t in tr if t["T"] >= now_ms - 3.6e6]
    if len(tr) < 20:
        return None
    nots = [float(t["p"]) * float(t["q"]) for t in tr]
    total = sum(nots)
    thr = max(WHALE_MIN_USD, WHALE_MULT * statistics.median(nots))
    buy = sum(n for n, t in zip(nots, tr) if n >= thr and not t["m"])   # m=True -> buyer is maker -> taker SELL
    sell = sum(n for n, t in zip(nots, tr) if n >= thr and t["m"])
    span_min = (tr[-1]["T"] - tr[0]["T"]) / 6e4
    return {"buy": buy, "sell": sell, "total": total, "thr": thr, "span_min": span_min,
            "dumping": sell >= WHALE_SELL_RATIO * max(buy, 1) and sell >= WHALE_SHARE * total and sell >= WHALE_MIN_SELL}


def whale_text(w):
    if not w:
        return "whales: n/a"
    return f"whales (trades ≥${w['thr'] / 1e3:.0f}k, last {w['span_min']:.0f}m): bought ${w['buy'] / 1e3:.0f}k, sold ${w['sell'] / 1e3:.0f}k"


# ---------- loss limit / kill switch
def loss_guard(L, eq_now, now_ms):
    """Returns (paused: bool, message or None). Updates L['pause_until_ms'] / L['halted']."""
    if L.get("halted"):
        return True, "🛑 **Emergency stop is ON** (equity fell 25% from its peak). No new buys until you open the resume link."
    hist = [(t, e) for t, e in L.get("equity_hist", [])]
    peak_all = max([e for _, e in hist] + [eq_now, L["budget"]])
    if eq_now < peak_all * (1 - KILL_DD):
        L["halted"] = True
        return True, f"🛑 **Emergency stop:** equity ${eq_now:.2f} is {100 * (1 - eq_now / peak_all):.0f}% below its peak ${peak_all:.2f}. No new buys until you open the resume link."
    if L.get("pause_until_ms", 0) > now_ms:
        t = datetime.fromtimestamp(L["pause_until_ms"] / 1000, timezone.utc) + timedelta(hours=5)
        return True, f"⏸️ Loss limit pause: no new buys until {t.strftime('%a %I:%M %p').replace(' 0', ' ')} (open trades still managed)."
    peak24 = max([e for t, e in hist if t >= now_ms - 24 * 3.6e6] + [eq_now])
    if eq_now < peak24 * (1 - LOSS_DAY):
        L["pause_until_ms"] = int(now_ms + LOSS_DAY_PAUSE_H * 3.6e6)
        t = datetime.fromtimestamp(L["pause_until_ms"] / 1000, timezone.utc) + timedelta(hours=5)
        return True, (f"🛑 **Loss limit hit:** equity ${eq_now:.2f} is {100 * (1 - eq_now / peak24):.1f}% below the 24h high "
                      f"${peak24:.2f}. Pausing new buys for {LOSS_DAY_PAUSE_H}h (until {t.strftime('%a %I:%M %p').replace(' 0', ' ')}).")
    return False, None


# ---------- correlation limit between holdings (backtest: <=0.55 helped; results noisy)
MAX_CORR = 0.55


def corr_1h(k_a, k_b, n=168):
    """Pearson correlation of hourly returns over the last n hours (klines lists, close at [4])."""
    a = {k[0]: k[4] for k in k_a}; b = {k[0]: k[4] for k in k_b}
    ts = sorted(set(a) & set(b))[-(n + 1):]
    if len(ts) < 50:
        return None
    ra = [a[y] / a[x] - 1 for x, y in zip(ts, ts[1:])]
    rb = [b[y] / b[x] - 1 for x, y in zip(ts, ts[1:])]
    m = len(ra); ma, mb = sum(ra) / m, sum(rb) / m
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    va = sum((x - ma) ** 2 for x in ra); vb = sum((y - mb) ** 2 for y in rb)
    return cov / (va * vb) ** 0.5 if va > 0 and vb > 0 else None
