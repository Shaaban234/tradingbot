"""The dip-buying signals from the paper bot (v6.2), same formulas, only the parts the rules use:
4 oversold votes (RSI, Keltner, Bollinger, z-score), the 4h EMA 50/200 trend vote, ATR and stretch."""
import math


def ema(xs, n):
    k = 2 / (n + 1); out = [xs[0]]
    for x in xs[1:]:
        out.append(x * k + out[-1] * (1 - k))
    return out


def std(xs):
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))


def rsi_last(c, n=14):
    if len(c) <= n:
        return 50.0
    g = [max(c[i] - c[i - 1], 0) for i in range(1, len(c))]
    l = [max(c[i - 1] - c[i], 0) for i in range(1, len(c))]
    ag, al = sum(g[:n]) / n, sum(l[:n]) / n
    out = 50.0
    for i in range(n, len(g)):
        ag = (ag * (n - 1) + g[i]) / n; al = (al * (n - 1) + l[i]) / n
        out = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def atr_last(h, l, c, n=14):
    tr = [h[0] - l[0]] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, len(c))]
    a = tr[0]
    for t in tr[1:]:
        a = (a * (n - 1) + t) / n
    return a


def evaluate(k1, k4):
    """k1: 1h candles, k4: 4h candles, as (t, o, h, l, c, v, close_t)."""
    h = [k[2] for k in k1]; l = [k[3] for k in k1]; c = [k[4] for k in k1]
    c4 = [k[4] for k in k4]
    price = c[-1]
    a = atr_last(h, l, c)
    e20 = ema(c, 20)[-1]
    e4_50 = ema(c4, 50)[-1]
    e4_200 = ema(c4, 200)[-1] if len(c4) >= 200 else ema(c4, len(c4) // 2)[-1]
    votes = {"ema_50_200_4h": 1 if e4_50 > e4_200 else -1}
    r14 = rsi_last(c)
    votes["rsi_30_70"] = 1 if r14 < 30 else (-1 if r14 > 70 else 0)
    mid20 = sum(c[-20:]) / 20; sd20 = std(c[-20:])
    votes["bollinger"] = 1 if price < mid20 - 2 * sd20 else (-1 if price > mid20 + 2 * sd20 else 0)
    z = (price - sum(c[-50:]) / 50) / (std(c[-50:]) or 1)
    votes["zscore_50"] = 1 if z < -2 else (-1 if z > 2 else 0)
    votes["keltner"] = 1 if price < e20 - 2 * a else (-1 if price > e20 + 2 * a else 0)
    return {"votes": votes, "price": price, "atr_pct": a / price, "ema20": e20,
            "stretch": price / e20 - 1, "rsi": round(r14, 1)}


def btc_down(k4_btc):
    c4 = [k[4] for k in k4_btc]
    e20, e50 = ema(c4, 20)[-1], ema(c4, 50)[-1]
    return c4[-1] < e50 and e20 < e50
