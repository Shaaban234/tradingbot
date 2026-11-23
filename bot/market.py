"""Public, read-only market data from the real Binance market (used for signals, whale and news checks)."""
import json, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
from . import config


def get(path, base=config.DATA_API, tries=3):
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(base + path, timeout=15) as r:
                return json.loads(r.read())
        except Exception:
            if attempt == tries - 1:
                raise
            time.sleep(1.0)


def klines(sym, interval, limit, start_ms=None, closed_only=True, base=config.DATA_API):
    q = f"/klines?symbol={sym}&interval={interval}&limit={limit}"
    if start_ms:
        q += f"&startTime={int(start_ms)}"
    now = int(time.time() * 1000)
    rows = [(r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]), r[6]) for r in get(q, base)]
    return [r for r in rows if r[6] < now] if closed_only else rows


def universe(top_n=config.TOP_N):
    """Most-traded spot USDT coins, excluding stablecoins, leveraged tokens and tokenized stocks."""
    info = get("/exchangeInfo?permissions=SPOT&symbolStatus=TRADING")
    trading = {x["symbol"] for x in info["symbols"] if x.get("status") == "TRADING"}
    rows = []
    for t in get("/ticker/24hr"):
        s = t["symbol"]
        if not s.endswith("USDT") or s not in trading:
            continue
        lp, hi, lo = float(t["lastPrice"]), float(t["highPrice"]), float(t["lowPrice"])
        if lp <= 0 or float(t.get("askPrice") or 0) <= 0 or float(t.get("bidPrice") or 0) <= 0:
            continue
        if 0.9 < lp < 1.1 and (hi - lo) / lp < 0.01:
            continue
        base = s[:-4]
        if not base or base in config.STABLES or base.endswith(config.LEVERAGED):
            continue
        if base.endswith("B") and base not in config.CRYPTO_ENDING_B:
            continue
        qv = float(t["quoteVolume"])
        if qv < config.MIN_QUOTE_VOL or int(t.get("count", 0)) < 100:
            continue
        rows.append((qv, s))
    rows.sort(reverse=True)
    return [s for _, s in rows[:top_n]]


def load_candles(syms, workers=10):
    """{sym: (1h x300, 4h x250)} for every symbol; failures are skipped."""
    def one(s):
        try:
            return s, (klines(s, "1h", 300), klines(s, "4h", 250))
        except Exception:
            return s, None
    out = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for s, d in ex.map(one, syms):
            if d:
                out[s] = d
    return out
