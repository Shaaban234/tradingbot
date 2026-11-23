"""Ensemble signal engine: many classic spot strategies vote on each coin.

Each strategy returns +1 (bullish / buy), 0 (no view) or -1 (bearish / avoid or
sell). Votes are averaged inside four families (trend, momentum, mean reversion,
market structure), and the families are weighted by market regime (ADX):
trending markets lean on trend + momentum, ranging markets on mean reversion.
Final score is in [-1, +1].
"""
import math

# ----------------------------------------------------------------- helpers

def ema(xs, n):
    k = 2 / (n + 1); out = [xs[0]]
    for x in xs[1:]:
        out.append(x * k + out[-1] * (1 - k))
    return out


def sma(xs, n):
    out, s = [], 0.0
    for i, x in enumerate(xs):
        s += x
        if i >= n:
            s -= xs[i - n]
        out.append(s / min(i + 1, n))
    return out


def wma(xs, n):
    w = list(range(1, n + 1)); tot = sum(w); out = []
    for i in range(len(xs)):
        win = xs[max(0, i - n + 1):i + 1]
        ww = w[-len(win):]
        out.append(sum(a * b for a, b in zip(win, ww)) / sum(ww))
    return out


def std(xs):
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))


def rsi_series(c, n=14):
    out = [50.0] * len(c)
    if len(c) <= n:
        return out
    g = [max(c[i] - c[i - 1], 0) for i in range(1, len(c))]
    l = [max(c[i - 1] - c[i], 0) for i in range(1, len(c))]
    ag, al = sum(g[:n]) / n, sum(l[:n]) / n
    for i in range(n, len(g)):
        ag = (ag * (n - 1) + g[i]) / n; al = (al * (n - 1) + l[i]) / n
        out[i + 1] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def true_range(h, l, c):
    return [h[0] - l[0]] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
                            for i in range(1, len(c))]


def atr_series(h, l, c, n=14):
    tr = true_range(h, l, c); out = [tr[0]]
    for t in tr[1:]:
        out.append((out[-1] * (n - 1) + t) / n)
    return out


def adx(h, l, c, n=14):
    pdm = [0.0] + [max(h[i] - h[i - 1], 0) if h[i] - h[i - 1] > l[i - 1] - l[i] else 0 for i in range(1, len(c))]
    ndm = [0.0] + [max(l[i - 1] - l[i], 0) if l[i - 1] - l[i] > h[i] - h[i - 1] else 0 for i in range(1, len(c))]
    tr = true_range(h, l, c)

    def wilder(xs):
        out = [sum(xs[1:n + 1])]
        for x in xs[n + 1:]:
            out.append(out[-1] - out[-1] / n + x)
        return out
    trs, p, m = wilder(tr), wilder(pdm), wilder(ndm)
    pdi = [100 * a / b if b else 0 for a, b in zip(p, trs)]
    ndi = [100 * a / b if b else 0 for a, b in zip(m, trs)]
    dx = [100 * abs(a - b) / (a + b) if a + b else 0 for a, b in zip(pdi, ndi)]
    ax = sum(dx[:n]) / n
    for d in dx[n:]:
        ax = (ax * (n - 1) + d) / n
    return ax, pdi[-1], ndi[-1]


def supertrend(h, l, c, n=10, mult=3.0):
    a = atr_series(h, l, c, n)
    up_band, dn_band, trend, line = [0.0] * len(c), [0.0] * len(c), [1] * len(c), [0.0] * len(c)
    for i in range(len(c)):
        mid = (h[i] + l[i]) / 2
        bu, bd = mid + mult * a[i], mid - mult * a[i]
        if i == 0:
            up_band[i], dn_band[i] = bu, bd
            continue
        up_band[i] = bu if bu < up_band[i - 1] or c[i - 1] > up_band[i - 1] else up_band[i - 1]
        dn_band[i] = bd if bd > dn_band[i - 1] or c[i - 1] < dn_band[i - 1] else dn_band[i - 1]
        if trend[i - 1] == 1 and c[i] < dn_band[i]:
            trend[i] = -1
        elif trend[i - 1] == -1 and c[i] > up_band[i]:
            trend[i] = 1
        else:
            trend[i] = trend[i - 1]
        line[i] = dn_band[i] if trend[i] == 1 else up_band[i]
    return trend[-1], line[-1]


def psar(h, l, step=0.02, mx=0.2):
    up, af, ep, sar = True, step, h[0], l[0]
    for i in range(1, len(h)):
        sar = sar + af * (ep - sar)
        if up:
            sar = min(sar, l[i - 1], l[i - 2] if i > 1 else l[i - 1])
            if l[i] < sar:
                up, sar, ep, af = False, ep, l[i], step
            elif h[i] > ep:
                ep, af = h[i], min(af + step, mx)
        else:
            sar = max(sar, h[i - 1], h[i - 2] if i > 1 else h[i - 1])
            if h[i] > sar:
                up, sar, ep, af = True, ep, h[i], step
            elif l[i] < ep:
                ep, af = l[i], min(af + step, mx)
    return 1 if up else -1


def kama(c, n=10, fast=2, slow=30):
    fs, ss = 2 / (fast + 1), 2 / (slow + 1); out = [c[0]] * len(c)
    for i in range(n, len(c)):
        change = abs(c[i] - c[i - n]); vol = sum(abs(c[j] - c[j - 1]) for j in range(i - n + 1, i + 1))
        er = change / vol if vol else 0
        sc = (er * (fs - ss) + ss) ** 2
        out[i] = out[i - 1] + sc * (c[i] - out[i - 1])
    return out


def sign(x, dead=0.0):
    return 1 if x > dead else (-1 if x < -dead else 0)


# ----------------------------------------------------------------- strategies

def evaluate(k1, k4, kd, btc_d, xs_rank, book=None):
    """k1/k4/kd: closed candles [(t,o,h,l,c,v)] for 1h, 4h, 1d. btc_d: BTC daily closes.
    xs_rank: this coin's 7d-return percentile in the universe (0..1).
    book: optional (bid_qty_1pct, ask_qty_1pct) for order-book imbalance."""
    o = [k[1] for k in k1]; h = [k[2] for k in k1]; l = [k[3] for k in k1]
    c = [k[4] for k in k1]; v = [k[5] for k in k1]
    c4 = [k[4] for k in k4]; cd = [k[4] for k in kd]
    price = c[-1]
    a = atr_series(h, l, c)[-1]; atr_pct = a / price
    e = {n: ema(c, n) for n in (10, 20, 50, 200)}
    e4_50, e4_200 = ema(c4, 50)[-1], ema(c4, 200)[-1] if len(c4) >= 200 else ema(c4, len(c4) // 2)[-1]
    votes = {}

    # ---- trend family
    votes["ema_50_200_4h"] = 1 if e4_50 > e4_200 else -1
    t3 = (e[10][-1], e[20][-1], e[50][-1])
    votes["triple_ma"] = 1 if t3[0] > t3[1] > t3[2] else (-1 if t3[0] < t3[1] < t3[2] else 0)
    hi20, lo20 = max(h[-21:-1]), min(l[-21:-1])
    votes["donchian_20"] = 1 if price > hi20 else (-1 if price < lo20 else 0)
    st_dir, st_line = supertrend(h, l, c)
    votes["supertrend"] = st_dir
    tenkan = (max(h[-9:]) + min(l[-9:])) / 2; kijun = (max(h[-26:]) + min(l[-26:])) / 2
    span_a = ((max(h[-35:-26]) + min(l[-35:-26])) / 2 + (max(h[-52:-26]) + min(l[-52:-26])) / 2) / 2
    span_b = (max(h[-78:-26]) + min(l[-78:-26])) / 2
    top, bot = max(span_a, span_b), min(span_a, span_b)
    votes["ichimoku"] = 1 if price > top and tenkan > kijun else (-1 if price < bot and tenkan < kijun else 0)
    adx_v, pdi, ndi = adx(h, l, c)
    votes["adx_di"] = (1 if pdi > ndi else -1) if adx_v > 25 else 0
    votes["parabolic_sar"] = psar(h, l)
    hull = wma([2 * x - y for x, y in zip(wma(c, 27), wma(c, 55))], 7)
    votes["hull_ma"] = sign(hull[-1] - hull[-3])
    km = kama(c)
    votes["kama"] = 1 if price > km[-1] and km[-1] > km[-3] else (-1 if price < km[-1] and km[-1] < km[-3] else 0)
    r30 = cd[-1] / cd[-31] - 1 if len(cd) > 31 else 0
    votes["ts_momentum_30d"] = sign(r30)
    ha_c = [(o[i] + h[i] + l[i] + c[i]) / 4 for i in range(len(c))]
    ha_o = [o[0]]
    for i in range(1, len(c)):
        ha_o.append((ha_o[-1] + ha_c[i - 1]) / 2)
    last3 = [(ha_o[i], ha_c[i], h[i], l[i]) for i in range(-3, 0)]
    if all(cc > oo and min(oo, cc) - ll <= 0.1 * (hh - ll) for oo, cc, hh, ll in last3):
        votes["heikin_ashi"] = 1
    elif all(cc < oo and hh - max(oo, cc) <= 0.1 * (hh - ll) for oo, cc, hh, ll in last3):
        votes["heikin_ashi"] = -1
    else:
        votes["heikin_ashi"] = 0

    # 20/50 EMA crossover on 1h: a *fresh* cross in the last 6 hours is the signal
    diff = [a - b for a, b in zip(e[20][-8:], e[50][-8:])]
    up_x = any(diff[i - 1] <= 0 < diff[i] for i in range(1, len(diff))) and diff[-1] > 0
    dn_x = any(diff[i - 1] >= 0 > diff[i] for i in range(1, len(diff))) and diff[-1] < 0
    votes["ma_cross_20_50"] = 1 if up_x else (-1 if dn_x else 0)

    if len(cd) >= 200:
        s50, s200 = sum(cd[-50:]) / 50, sum(cd[-200:]) / 200
        votes["golden_cross_1d"] = 1 if s50 > s200 else -1
    else:
        votes["golden_cross_1d"] = 0          # coin too young for a 200-day average

    # ---- momentum family
    votes["cross_sectional_7d"] = 1 if xs_rank >= 0.8 else (-1 if xs_rank <= 0.2 else 0)
    if len(cd) > 8 and len(btc_d) > 8:
        rel = (cd[-1] / cd[-8]) / (btc_d[-1] / btc_d[-8]) - 1
        votes["rel_strength_vs_btc"] = sign(rel, 0.01)
    else:
        votes["rel_strength_vs_btc"] = 0
    votes["dual_momentum"] = 1 if r30 > 0 and xs_rank >= 0.5 else (-1 if r30 < 0 else 0)
    m = [x - y for x, y in zip(ema(c, 12), ema(c, 26))]
    hist = [x - y for x, y in zip(m, ema(m, 9))]
    votes["macd"] = 1 if hist[-1] > 0 and hist[-1] > hist[-2] else (-1 if hist[-1] < 0 and hist[-1] < hist[-2] else 0)
    roc = c[-1] / c[-13] - 1
    votes["roc_12h"] = sign(roc, 2 * atr_pct)
    vavg = sum(v[-21:-1]) / 20 or 1
    votes["volume_breakout"] = 1 if price > hi20 and v[-1] > 1.5 * vavg else (-1 if price < lo20 and v[-1] > 1.5 * vavg else 0)

    # ---- mean-reversion family
    r14 = rsi_series(c)[-1]
    votes["rsi_30_70"] = 1 if r14 < 30 else (-1 if r14 > 70 else 0)
    mid20 = sum(c[-20:]) / 20; sd20 = std(c[-20:])
    votes["bollinger"] = 1 if price < mid20 - 2 * sd20 else (-1 if price > mid20 + 2 * sd20 else 0)
    z = (price - sum(c[-50:]) / 50) / (std(c[-50:]) or 1)
    votes["zscore_50"] = 1 if z < -2 else (-1 if z > 2 else 0)
    tp = [(h[i] + l[i] + c[i]) / 3 for i in range(len(c))]
    vwap = sum(tp[i] * v[i] for i in range(-24, 0)) / (sum(v[-24:]) or 1)
    votes["vwap_24h"] = 1 if price < vwap - 1.5 * a else (-1 if price > vwap + 1.5 * a else 0)
    rs = rsi_series(c)
    win = rs[-14:]; k_now = (rs[-1] - min(win)) / ((max(win) - min(win)) or 1)
    win2 = rs[-15:-1]; k_prev = (rs[-2] - min(win2)) / ((max(win2) - min(win2)) or 1)
    votes["stoch_rsi"] = 1 if k_prev < 0.2 and k_now > k_prev else (-1 if k_prev > 0.8 and k_now < k_prev else 0)
    kmid = e[20][-1]
    votes["keltner"] = 1 if price < kmid - 2 * a else (-1 if price > kmid + 2 * a else 0)
    r2 = rsi_series(c, 2)[-1]
    votes["connors_rsi2"] = 1 if r2 < 10 and price > e[200][-1] else (-1 if r2 > 90 else 0)
    hi7d = max(h[-168:]) if len(h) >= 168 else max(h)
    dd = price / hi7d - 1
    votes["dip_ladder"] = 1 if dd <= -0.10 and c4[-1] > e4_200 else 0

    # ---- market-structure family
    piv_lo = [l[i] for i in range(len(l) - 60, len(l) - 3) if l[i] == min(l[i - 3:i + 4])]
    piv_hi = [h[i] for i in range(len(h) - 60, len(h) - 3) if h[i] == max(h[i - 3:i + 4])]
    sup = max([p for p in piv_lo if p < price], default=None)
    res = min([p for p in piv_hi if p > price], default=None)
    bounced = c[-1] > o[-1] and sup is not None and l[-1] <= sup * 1.005
    votes["support_resistance"] = 1 if bounced else (-1 if res is not None and res / price - 1 < 0.005 else 0)
    if book:
        b, s = book
        imb = b / (b + s) if b + s else 0.5
        votes["orderbook_imbalance"] = 1 if imb > 0.6 else (-1 if imb < 0.4 else 0)
    else:
        votes["orderbook_imbalance"] = 0

    families = {
        "trend": ["ema_50_200_4h", "triple_ma", "donchian_20", "supertrend", "ichimoku", "adx_di",
                  "parabolic_sar", "hull_ma", "kama", "ts_momentum_30d", "heikin_ashi", "golden_cross_1d", "ma_cross_20_50"],
        "momentum": ["cross_sectional_7d", "rel_strength_vs_btc", "dual_momentum", "macd", "roc_12h",
                     "volume_breakout"],
        "mean_reversion": ["rsi_30_70", "bollinger", "zscore_50", "vwap_24h", "stoch_rsi", "keltner",
                           "connors_rsi2", "dip_ladder"],
        "structure": ["support_resistance", "orderbook_imbalance"],
    }
    fam = {f: sum(votes[n] for n in names) / len(names) for f, names in families.items()}
    if adx_v > 25:
        regime, w = "trending", {"trend": 0.4, "momentum": 0.35, "mean_reversion": 0.1, "structure": 0.15}
    elif adx_v < 20:
        regime, w = "ranging", {"trend": 0.15, "momentum": 0.15, "mean_reversion": 0.5, "structure": 0.2}
    else:
        regime, w = "mixed", {"trend": 0.3, "momentum": 0.3, "mean_reversion": 0.25, "structure": 0.15}
    score = sum(w[f] * fam[f] for f in fam) / sum(w.values())
    buys = sorted(n for n, x in votes.items() if x > 0)
    sells = sorted(n for n, x in votes.items() if x < 0)
    return {"score": round(score, 3), "regime": regime, "adx": round(adx_v, 1),
            "families": {f: round(x, 2) for f, x in fam.items()}, "votes": votes,
            "buy_votes": buys, "sell_votes": sells, "atr_pct": atr_pct,
            "supertrend_dir": st_dir, "ema20": e[20][-1], "stretch": price / e[20][-1] - 1, "supertrend_line": st_line, "rsi": round(r14, 1),
            "price": price}
