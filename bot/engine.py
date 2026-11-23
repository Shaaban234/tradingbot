"""One trading cycle (called every 15 minutes) plus the approve / control actions.

Safety design:
- Every open position has its stop-loss (and take-profit) as a real order on Binance, so it is
  protected even if a run is late or Vercel is down. If protection can't be placed, the bot sells.
- The bot only uses its own budget (BUDGET_USDT), tracked separately from the rest of the account.
- A run lock stops two runs (or a run and an approval) from trading at the same time.
"""
import secrets, time, traceback
from datetime import datetime, timezone, timedelta
from . import config as C, market, news, guards, signals, signals_full, store, notify, reddit
from .exchange import Exchange, BinanceError

PKT = timezone(timedelta(hours=5))
FEE = 0.001
VENUE = "testnet" if C.MODE == "testnet" else "live"
STATE_KEY = f"state:{VENUE}"


# ---------------------------------------------------------------- helpers
def iso(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def pkt(ms, f="%a %-I:%M %p"):
    return datetime.fromtimestamp(ms / 1000, PKT).strftime(f)


def fp(p):
    return f"{p:,.2f}" if p >= 100 else (f"{p:.4f}" if p >= 1 else f"{p:.6g}")


def new_state():
    return {"venue": VENUE, "budget": C.BUDGET_USDT, "cash": C.BUDGET_USDT, "positions": [], "trades": [],
            "equity_hist": [], "approvals": {}, "halted": False, "pause_until_ms": 0,
            "started_utc": iso(int(time.time() * 1000)), "runs": 0, "errors": [],
            "paused_by_user": C.REAL_MONEY}       # real money starts paused until the key/balance are verified


def load():
    S = store.get_json(STATE_KEY) or new_state()
    if abs(S.get("budget", C.BUDGET_USDT) - C.BUDGET_USDT) > 1e-9:      # budget changed in Vercel settings
        S["cash"] += C.BUDGET_USDT - S["budget"]
        S["budget"] = C.BUDGET_USDT
    return S


def save(S):
    S["trades"] = S["trades"][-500:]
    S["equity_hist"] = S["equity_hist"][-800:]
    S["errors"] = S["errors"][-30:]
    store.set_json(STATE_KEY, S)


def dyn_stop(pos):
    """Stop implied by the best price so far: breakeven at 1.5R, then trail. Never lowers the stop."""
    e, R, peak = pos["entry_price"], pos["R"], pos.get("peak", pos["entry_price"])
    gain = peak / e - 1
    st = pos["init_stop"]
    if gain >= C.BE_AT_R * R:
        st = max(st, e * 1.003)
    if gain >= C.TRAIL_AT_R * R:
        st = max(st, peak * (1 - max(C.TRAIL_GAP_R * R, 0.015)))
    return max(st, pos["stop"])


def equity(S):
    return S["cash"] + sum(p["qty"] * p.get("mark_bid", p["entry_price"]) * (1 - FEE) for p in S["positions"])


def record_close(S, pos, proceeds, avg, reason, ts, events):
    pnl = proceeds - pos["cost"]
    S["cash"] = round(S["cash"] + proceeds, 6)
    S["trades"].append({k: v for k, v in pos.items() if k not in ("prot",)} |
                       {"exit_price": avg, "exit_utc": iso(ts), "exit_reason": reason, "proceeds": round(proceeds, 6),
                        "pnl": round(pnl, 6), "pnl_pct": round(100 * pnl / pos["cost"], 2)})
    S["positions"].remove(pos)
    events.append(f"{'🟢' if pnl >= 0 else '🔴'} **Sold {pos['symbol'][:-4]}** at ${fp(avg)} ({reason}): "
                  f"{'+' if pnl >= 0 else ''}{pnl:.3f} USDT ({100 * pnl / pos['cost']:+.2f}%)")


def exit_now(S, ex, pos, reason, ts, events):
    """Cancel the exit orders and sell everything at market."""
    sym = pos["symbol"]
    if pos.get("prot"):
        ex.unprotect(sym, pos["prot"])
        pos["prot"] = None
    have = min(pos["qty"], ex.free(sym[:-4]))
    qty = ex.floor_qty(sym, have)
    R = ex.rules(sym)
    bid, _ = ex.bbo(sym)
    if qty < R["min_qty"] or qty * bid < R["min_notional"]:
        events.append(f"⚠️ {sym[:-4]}: only {have:g} left, worth ${have * bid:.2f}, below Binance's minimum order. "
                      f"It stays in the account as dust; counted at its current value.")
        record_close(S, pos, have * bid * (1 - FEE), bid, reason + " (dust)", ts, events)
        return
    r = ex.market_sell(sym, qty)
    record_close(S, pos, r["proceeds"], r["avg"], reason, ts, events)


def protect_or_exit(S, ex, pos, stop, ts, events):
    """(Re)place exit orders on Binance. If that fails, never leave the coin unprotected: sell it."""
    sym = pos["symbol"]
    try:
        qty = ex.floor_qty(sym, min(pos["qty"], ex.free(sym[:-4])))
        pos["prot"] = ex.protect(sym, qty, stop, pos.get("target"))
        pos["stop"] = pos["prot"]["stop"]
        return True
    except BinanceError as e:
        S["errors"].append(f"{iso(ts)} protect {sym}: {e}")
        events.append(f"⚠️ {sym[:-4]}: couldn't place the stop-loss on Binance ({e.msg}). Selling to stay safe.")
        exit_now(S, ex, pos, "safety exit (stop order failed)", ts, events)
        return False


# ---------------------------------------------------------------- buying
def execute_buy(S, ex, sym, plan, ts, events):
    """Market-buy `sym`, then immediately place stop-loss + take-profit on Binance."""
    if not ex.has_symbol(sym):
        events.append(f"↪️ {sym[:-4]} isn't tradable on the {VENUE} exchange, skipped.")
        return None
    R = ex.rules(sym)
    free_slots = max(C.MAX_POS - len(S["positions"]) - len(S["approvals"]), 1)
    floor = max(C.MIN_TRADE_USDT, R["min_notional"] * 1.15)
    size = min(max(equity(S) * C.RISK_PCT / plan["stop_pct"], floor), S["cash"] / free_slots, C.MAX_TRADE_USDT)
    if size < floor:
        size = S["cash"] if S["cash"] >= floor else 0
    if size < floor:
        events.append(f"💤 Not enough bot cash for a safe-size buy (${S['cash']:.2f}).")
        return None
    if ex.free("USDT") < size * 0.995:
        events.append(f"⚠️ Binance account has less free USDT than the bot's budget expects; skipped {sym[:-4]}.")
        return None
    fill = ex.market_buy(sym, min(size, ex.free("USDT")))
    avg, qty, spent = fill["avg"], fill["qty"], fill["spent"]
    stop_pct, tgt_pct = plan["stop_pct"], plan["tgt_pct"]
    pos = {"symbol": sym, "entry_price": avg, "entry_ms": ts, "entry_utc": iso(ts), "qty": qty, "cost": round(spent, 6),
           "stop": avg * (1 - stop_pct), "init_stop": avg * (1 - stop_pct), "target": avg * (1 + tgt_pct),
           "R": stop_pct, "peak": avg, "why": plan["why"], "research": "PENDING", "last_checked_ms": ts,
           "mark_bid": avg, "order_id": fill["order_id"], "prot": None}
    S["positions"].append(pos)
    S["cash"] = round(S["cash"] - spent, 6)
    events.append(f"🛒 **Bought ${spent:.2f} of {sym[:-4]}** at ${fp(avg)} ({plan['why']}). "
                  f"Stop ${fp(pos['stop'])} (-{100 * stop_pct:.1f}%), target ${fp(pos['target'])} (+{100 * tgt_pct:.1f}%)")
    if protect_or_exit(S, ex, pos, pos["stop"], ts, events):
        events.append(f"🛡️ {sym[:-4]}: stop-loss and take-profit are live on Binance.")
    return pos


# ---------------------------------------------------------------- the 15-minute cycle
def run_cycle(host="", ex=None):
    if not store.lock("run", 280):
        return {"skipped": "another run is in progress"}
    ex = ex or Exchange()
    ts = int(time.time() * 1000)
    S = load()
    events, report = [], []
    try:
        _cycle(S, ex, ts, events, report, host)
    except Exception as e:
        S["errors"].append(f"{iso(ts)} {e.__class__.__name__}: {e}")
        events.append(f"❗ Run error: {e.__class__.__name__}: {str(e)[:200]} (open positions stay protected on Binance)")
        print(traceback.format_exc())
    finally:
        S["runs"] = S.get("runs", 0) + 1
        S["last_run"] = {"utc": iso(ts), "secs": round(time.time() - ts / 1000, 1), "events": events}
        save(S)
        store.unlock("run")
    msg = "\n".join(report[:1] + events + report[1:])
    notify.send(msg)
    _daily(S, ts)
    return {"ok": True, "events": events}


def _cycle(S, ex, ts, events, report, host):
    try:
        items, _ = news.fetch()
    except Exception:
        items = []
    mood = news.market_mood(items, ts)
    try:
        rposts, _ = reddit.fetch()
    except Exception:
        rposts = []

    syms = market.universe()
    for p in S["positions"]:
        if p["symbol"] not in syms:
            syms.append(p["symbol"])
    if "BTCUSDT" not in syms:
        syms.append("BTCUSDT")
    data = market.load_candles(syms)
    btc_down = signals.btc_down(data["BTCUSDT"][1]) if "BTCUSDT" in data else True
    # 7-day return rank across all coins (feeds the 29-strategy score), from 4h candles
    r7 = {s: d[1][-1][4] / d[1][-43][4] - 1 for s, d in data.items() if len(d[1]) > 43}
    ordered = sorted(r7, key=r7.get)
    rank = {s: i / max(len(ordered) - 1, 1) for i, s in enumerate(ordered)}
    sigs = {}
    for s, (k1, k4) in data.items():
        if len(k1) < 200 or len(k4) < 60 or ts - k1[-1][6] > 3 * 3.6e6:
            continue
        try:
            sigs[s] = signals.evaluate(k1, k4)
        except Exception:
            pass

    # ---- 1. positions whose exit orders filled on Binance since last run
    for pos in list(S["positions"]):
        if not pos.get("prot"):
            continue
        res = ex.protection_result(pos["symbol"], pos["prot"])
        if res is None:
            continue
        if res["qty"] <= 0:
            pos["prot"] = None
            events.append(f"⚠️ {pos['symbol'][:-4]}: exit orders were cancelled outside the bot. Re-placing them.")
            continue
        how = res["how"]
        if how == "stop":
            how = "stop-loss" if res["avg"] < pos["entry_price"] else (
                "breakeven stop" if res["avg"] < pos["entry_price"] * 1.01 else "trailing stop (profit locked)")
        left = pos["qty"] - res["qty"]
        extra = 0.0
        if left > 0:          # rounding leftover: sell if big enough, else count its value as dust
            try:
                bid, _ = ex.bbo(pos["symbol"])
                q = ex.floor_qty(pos["symbol"], min(left, ex.free(pos["symbol"][:-4])))
                if q * bid >= ex.rules(pos["symbol"])["min_notional"]:
                    extra = ex.market_sell(pos["symbol"], q)["proceeds"]
                else:
                    extra = left * bid * (1 - FEE)
            except Exception:
                pass
        record_close(S, pos, res["proceeds"] + extra, res["avg"], how, ts, events)

    # ---- 2. manage what's still open
    for pos in list(S["positions"]):
        sym = pos["symbol"]
        bid, _ = ex.bbo(sym)
        pos["mark_bid"] = bid
        hi = ex.highs_since(sym, pos.get("last_checked_ms", pos["entry_ms"]))
        if hi:
            pos["peak"] = max(pos.get("peak", pos["entry_price"]), hi)
        pos["last_checked_ms"] = ts
        held_h = (ts - pos["entry_ms"]) / 3.6e6
        bad = [n for n in news.for_coin(items, sym[:-4], ts) if n["tone"] < 0 and n["ts_ms"] >= pos["entry_ms"]]
        if pos.get("close_requested"):
            exit_now(S, ex, pos, pos["close_requested"], ts, events); continue
        if bad:
            exit_now(S, ex, pos, f"bad news: {bad[0]['title'][:70]}", ts, events); continue
        full = full_signal(sym, data, rank)
        if full:
            pos["last_score"] = full["score"]
        keep = bid >= pos["entry_price"] * (1 + C.EXTEND_MIN_GAIN) and full and full["score"] >= C.EXTEND_MIN_SCORE
        if held_h >= C.EXT_HOLD_H or (held_h >= C.MAX_HOLD_H and not keep):
            exit_now(S, ex, pos, f"max hold {int(held_h)}h", ts, events); continue
        if full:
            strong = full["score"] >= C.RUNNER_SCORE and full["supertrend_dir"] > 0
            near_tp = pos.get("target") and bid >= pos["target"] * 0.985
            if strong and near_tp and not pos.get("runner"):
                pos["runner"], pos["target"] = True, None
                pos["stop"] = max(pos["stop"], bid * (1 - max(C.TRAIL_GAP_R * pos["R"], 0.015)))
                if pos.get("prot"):
                    ex.unprotect(sym, pos["prot"]); pos["prot"] = None
                if protect_or_exit(S, ex, pos, pos["stop"], ts, events):
                    events.append(f"🚀 {sym[:-4]}: near target but signals still strong ({full['score']:+.2f}), "
                                  f"letting it run with a trailing stop at ${fp(pos['stop'])}")
                continue
            if pos.get("runner") and not strong and bid > pos["entry_price"]:
                pos["runner"] = False
                pos["target"] = max(bid * 1.01, pos["stop"] * 1.02)
                if pos.get("prot"):
                    ex.unprotect(sym, pos["prot"]); pos["prot"] = None
                if protect_or_exit(S, ex, pos, dyn_stop(pos), ts, events):
                    events.append(f"🎯 {sym[:-4]}: momentum fading ({full['score']:+.2f}), new target ${fp(pos['target'])}")
                continue
        new_stop = dyn_stop(pos)
        if bid <= new_stop:
            exit_now(S, ex, pos, "price below stop (gap)", ts, events); continue
        if not pos.get("prot"):
            protect_or_exit(S, ex, pos, new_stop, ts, events); continue
        if new_stop > pos["prot"]["stop"] * 1.003:
            old = pos["prot"]["stop"]
            ex.unprotect(sym, pos["prot"]); pos["prot"] = None
            if protect_or_exit(S, ex, pos, new_stop, ts, events):
                locked = 100 * (pos["stop"] / pos["entry_price"] - 1)
                events.append(f"🔒 {sym[:-4]}: stop raised ${fp(old)} → ${fp(pos['stop'])} "
                              f"({'locks ' if locked >= 0 else ''}{locked:+.1f}%; peak ${fp(pos['peak'])})")

    # ---- 3. guards
    U = guards.load_unlocks()
    for p in S["positions"]:
        c = p["symbol"][:-4]
        ur = guards.unlock_risk(U, c, ts, before_d=2, after_d=0)
        if ur and not p.get("unlock_warned"):
            p["unlock_warned"] = True
            events.append(f"🔓 Heads-up on {c}: {ur}. Keeping the stop in place.")
        w = guards.whale_flow(market.get, p["symbol"], ts)
        if w and w["dumping"] and ts - p.get("whale_warn_ms", 0) > 2 * 3.6e6:
            p["whale_warn_ms"] = ts
            events.append(f"🐋 Whales selling {c}: {guards.whale_text(w)}. Stop stays at ${fp(p['stop'])}.")
    eq = equity(S)
    paused, gmsg = guards.loss_guard(S, eq, ts)
    if gmsg:
        events.append(gmsg)
    if S.get("paused_by_user"):
        paused = True
        events.append("⏸️ Paused by you: no new buys (open trades still managed). Open the resume link to restart.")

    # ---- 4. candidates (same rules as the paper bot v6.2)
    held = {p["symbol"] for p in S["positions"]} | {a["symbol"] for a in S["approvals"].values()}
    losers = {t["symbol"] for t in S["trades"] if t["pnl"] < 0 and
              ts - datetime.fromisoformat(t["exit_utc"].replace("Z", "+00:00")).timestamp() * 1000 < C.RECENT_LOSER_H * 3.6e6}
    cands, skipped = [], []
    for s, g in sigs.items():
        if s in held or s in losers or s == "BTCUSDT":
            continue
        mr = sum(1 for v in C.MR_VOTES if g["votes"].get(v, 0) > 0)
        if mr < C.MR_MIN or (btc_down and g["votes"]["ema_50_200_4h"] < 0):
            continue
        rd = reddit.for_coin(rposts, s[:-4], ts)
        if (len(rd["danger"]) >= 2 and len(rd["danger"]) >= 0.25 * rd["mentions"]) or (rd["danger"] and rd["mentions"] <= 3):
            skipped.append(f"🚫 Skipped {s[:-4]}: Reddit: {rd['danger'][0][:70]}"); continue
        if not (C.MIN_ATR_PCT <= g["atr_pct"] <= C.MAX_ATR_PCT):
            continue
        neg = [n for n in news.for_coin(items, s[:-4], ts) if n["tone"] < 0]
        if neg:
            skipped.append(f"🚫 Skipped {s[:-4]}: bad news, \"{neg[0]['title'][:80]}\""); continue
        ur = guards.unlock_risk(U, s[:-4], ts)
        if ur:
            skipped.append(f"🔓 Skipped {s[:-4]}: {ur}"); continue
        cands.append((g["stretch"], s, g, mr))
    cands.sort()

    # ---- 5. buy (or ask for approval)
    for stretch, s, g, mr in ([] if paused else cands):
        free_slots = C.MAX_POS - len(S["positions"]) - len(S["approvals"])
        if free_slots <= 0 or S["cash"] < 5.5:
            break
        try:
            bid, ask = market_bbo(s)          # spread on the real market (testnet books are thin and fake)
        except Exception:
            continue
        if ask <= 0 or (ask - bid) / ask > C.MAX_SPREAD:
            continue
        hc = next(((p["symbol"][:-4], cr) for p in S["positions"] if p["symbol"] in data and s in data
                   for cr in [guards.corr_1h(data[s][0], data[p["symbol"]][0])] if cr is not None and cr > guards.MAX_CORR), None)
        if hc:
            skipped.append(f"🔗 Skipped {s[:-4]}: moves too much like {hc[0]} (correlation {hc[1]:.2f})"); continue
        wf = guards.whale_flow(market.get, s, ts)
        if wf and wf["dumping"]:
            skipped.append(f"🐋 Skipped {s[:-4]}: whales still dumping ({guards.whale_text(wf)})"); continue
        stop_pct = min(max(C.STOP_MULT * g["atr_pct"], C.STOP_MIN), C.STOP_MAX)
        tgt_pct = min(max(C.TGT_R * stop_pct, 0.02), C.TGT_MAX)
        why = (f"dip buy: {', '.join(v.split('_')[0] for v in C.MR_VOTES if g['votes'].get(v, 0) > 0)} say oversold, "
               f"{100 * stretch:+.1f}% vs 1h average; {guards.whale_text(wf)}")
        plan = {"stop_pct": stop_pct, "tgt_pct": tgt_pct, "why": why, "signal_price": ask}
        if C.MODE == "approval":
            aid, code = secrets.token_hex(4), secrets.token_urlsafe(12)
            S["approvals"][aid] = {"symbol": s, "plan": plan, "code": code, "created_ms": ts}
            link = f"https://{host}/api/approve?id={aid}&code={code}" if host else "(set up the domain)"
            events.append(f"✋ **Buy signal: {s[:-4]}** at ~${fp(ask)} ({why}). "
                          f"Stop -{100 * stop_pct:.1f}%, target +{100 * tgt_pct:.1f}%.\n👉 Approve within "
                          f"{C.APPROVAL_TTL_MIN} min: {link}")
        else:
            try:
                execute_buy(S, ex, s, plan, ts, events)
            except BinanceError as e:
                S["errors"].append(f"{iso(ts)} buy {s}: {e}")
                events.append(f"⚠️ Buy of {s[:-4]} failed on Binance: {e.msg}")
    for aid, a in list(S["approvals"].items()):
        if ts - a["created_ms"] > C.APPROVAL_TTL_MIN * 6e4:
            del S["approvals"][aid]
            events.append(f"⌛ Buy signal for {a['symbol'][:-4]} expired (not approved in {C.APPROVAL_TTL_MIN} min).")
    events += skipped[:4]

    # ---- 6. report
    eq = equity(S)
    S["equity_hist"].append([ts, round(eq, 4)])
    pnl = eq - S["budget"]
    n = len(S["trades"]); wins = sum(1 for t in S["trades"] if t["pnl"] > 0)
    report.append(f"⏱️ **Check {pkt(ts)}** · {len(sigs)} coins · news {mood['label']} · BTC trend {'down 📉' if btc_down else 'ok'}"
                  + (" · ⏸️ buys paused" if paused else ""))
    if not any(e.startswith(("🛒", "🟢", "🔴", "✋")) for e in events):
        dips = sorted(sigs.items(), key=lambda x: (-sum(1 for v in C.MR_VOTES if x[1]["votes"].get(v, 0) > 0), x[1]["stretch"]))
        report.append("Closest dips: " + ", ".join(
            f"{s[:-4]} {sum(1 for v in C.MR_VOTES if g['votes'].get(v, 0) > 0)}/4 ({100 * g['stretch']:+.1f}%)" for s, g in dips[:3]))
    for p in S["positions"]:
        mb = p.get("mark_bid", p["entry_price"])
        report.append(f"📌 {p['symbol'][:-4]}: entry ${fp(p['entry_price'])}, now ${fp(mb)} ({100 * (mb / p['entry_price'] - 1):+.2f}%)"
                      f" · stop ${fp(p['stop'])} · " + (f"target ${fp(p['target'])}" if p.get("target") else "🚀 runner, no target")
                      + ("" if p.get("prot") else " · ⚠️ no exit orders"))
    report.append(f"💰 **Bot balance ${eq:.3f}** of ${S['budget']:.2f} budget ({'+' if pnl >= 0 else ''}{pnl:.3f}, "
                  f"{100 * pnl / S['budget']:+.2f}%) · closed {n}, wins {wins}")
    in_trades = eq - S["cash"]
    try:
        acct = f" · Binance USDT free ${ex.free('USDT'):.2f}"
    except Exception:
        acct = ""
    report.append(f"💵 Cash left **${S['cash']:.2f}** · in trades ${in_trades:.2f}{acct}")


def full_signal(sym, data, rank):
    """The paper bot's full 29-strategy evaluation (score, Supertrend), for held coins only (keeps CPU low)."""
    try:
        k1, k4 = data[sym]
        kd = market.klines(sym, "1d", 210)
        btc_d = [k[4] for k in market.klines("BTCUSDT", "1d", 210)]
        d = market.get(f"/depth?symbol={sym}&limit=100")
        price = k1[-1][4]
        book = (sum(float(q) for p, q in d["bids"] if float(p) >= price * 0.99),
                sum(float(q) for p, q in d["asks"] if float(p) <= price * 1.01))
        return signals_full.evaluate(k1, k4, kd, btc_d, rank.get(sym, 0.5), book)
    except Exception as e:
        print("full_signal", sym, e)
        return None


def market_bbo(sym):
    b = market.get(f"/ticker/bookTicker?symbol={sym}")
    return float(b["bidPrice"]), float(b["askPrice"])


def _daily(S, ts):
    loc = datetime.fromtimestamp(ts / 1000, PKT)
    today = loc.strftime("%Y-%m-%d")
    if loc.hour < 21 or S.get("daily_summary_date") == today:
        return
    day = [t for t in S["trades"] if datetime.fromisoformat(t["exit_utc"].replace("Z", "+00:00")).astimezone(PKT).strftime("%Y-%m-%d") == today]
    pnl = sum(t["pnl"] for t in day)
    eq = equity(S)
    lines = [f"📅 **Daily summary {loc.strftime('%a %b %d')}** (Pakistan time)",
             f"💰 Bot balance **${eq:.2f}** ({100 * (eq / S['budget'] - 1):+.2f}% since start)",
             f"🔁 {len(day)} trades closed today, {sum(1 for t in day if t['pnl'] > 0)} wins, P&L {'+' if pnl >= 0 else '-'}${abs(pnl):.2f}"]
    lines += [f"• {t['symbol'][:-4]} {t['pnl_pct']:+.2f}% ({t['exit_reason']})" for t in day[:8]]
    lines += [f"📌 Open: {p['symbol'][:-4]} {100 * (p.get('mark_bid', p['entry_price']) / p['entry_price'] - 1):+.2f}%" for p in S["positions"]]
    if notify.send("\n".join(lines)):
        S["daily_summary_date"] = today
        store.set_json(STATE_KEY, S)


# ---------------------------------------------------------------- approve / control
def approve(aid, code, ex=None):
    if not store.lock("run", 120):
        return 409, "The bot is busy with a check. Try the link again in a minute."
    ex = ex or Exchange()
    ts = int(time.time() * 1000)
    S = load()
    events = []
    try:
        a = S["approvals"].get(aid)
        if not a or not secrets.compare_digest(a["code"], code or ""):
            return 404, "This approval link is invalid or was already used."
        del S["approvals"][aid]
        if ts - a["created_ms"] > C.APPROVAL_TTL_MIN * 6e4:
            save(S); return 410, f"Too late: buy signals expire after {C.APPROVAL_TTL_MIN} minutes."
        if len(S["positions"]) >= C.MAX_POS:
            save(S); return 409, "Already holding the maximum number of coins."
        bid, ask = market_bbo(a["symbol"])
        if abs(ask / a["plan"]["signal_price"] - 1) > C.APPROVAL_MAX_DRIFT:
            save(S); return 409, f"Price moved more than {100 * C.APPROVAL_MAX_DRIFT:.1f}% since the signal; not buying."
        pos = execute_buy(S, ex, a["symbol"], a["plan"], ts, events)
        save(S)
        if events:
            notify.send("✅ Approved by you.\n" + "\n".join(events))
        return 200, ("Bought. " if pos else "Not bought. ") + " ".join(events)
    finally:
        store.unlock("run")


def self_test(ex, sym):
    """Testnet only: full order path (buy -> OCO -> check -> cancel -> stop-only -> cancel -> sell). Not counted in the bot's books."""
    out = []
    step = "start"
    try:
        step = "account"; usdt = ex.free("USDT"); out.append(f"account ok, test USDT {usdt:,.0f}")
        step = "rules"; R = ex.rules(sym); out.append(f"rules {sym}: tick {R['tick']}, step {R['step']}, min ${R['min_notional']}")
        step = "buy"; f = ex.market_buy(sym, 6.0); out.append(f"buy ok: {f['qty']:g} at {f['avg']:.4f} (${f['spent']:.2f})")
        bid, ask = ex.bbo(sym)
        step = "oco"; p = ex.protect(sym, ex.floor_qty(sym, f["qty"]), bid * 0.96, ask * 1.08); out.append(f"OCO ok: stop {p['stop']}, target {p['target']}")
        step = "oco status"; out.append(f"OCO status: {'waiting' if ex.protection_result(sym, p) is None else 'done?!'}")
        step = "cancel oco"; ex.unprotect(sym, p); out.append("OCO cancelled")
        step = "stop only"; p2 = ex.protect(sym, ex.floor_qty(sym, f["qty"]), bid * 0.96, None); out.append(f"stop-only ok: {p2['stop']}")
        step = "cancel stop"; ex.unprotect(sym, p2); out.append("stop cancelled")
        step = "sell"; s = ex.market_sell(sym, ex.floor_qty(sym, min(f["qty"], ex.free(sym[:-4])))); out.append(f"sell ok: {s['qty']:g} for ${s['proceeds']:.2f}")
        return ["🧪 Self-test PASSED · " + " · ".join(out)]
    except Exception as e:
        return ["🧪 Self-test FAILED at " + step + f": {e} · so far: " + " · ".join(out)]


def control(action, symbol=None, ex=None, note=None):
    """pause | resume | close (symbol) | status. Protected by CONTROL_TOKEN in the API layer."""
    S = load()
    if action == "status":
        return 200, S
    if action == "balance":                 # read-only: proves the API key works
        e = ex or Exchange()
        try:
            return 200, {"mode": C.MODE, "usdt_free": e.free("USDT"), "bnb_free": e.free("BNB"),
                         "bot_budget": S["budget"], "bot_cash": S["cash"], "paused": S.get("paused_by_user")}
        except BinanceError as err:
            return 502, {"error": str(err)}
    if not store.lock("run", 120):
        return 409, "The bot is busy with a check. Try again in a minute."
    ts = int(time.time() * 1000)
    events = []
    try:
        if action == "pause":
            S["paused_by_user"] = True
            events.append("⏸️ Paused: no new buys. Open trades keep their stop-loss and take-profit on Binance.")
        elif action == "resume":
            S["paused_by_user"] = False; S["halted"] = False; S["pause_until_ms"] = 0
            events.append("▶️ Resumed: the bot may buy again on the next check.")
        elif action == "selftest":
            if C.MODE != "testnet":
                return 400, "Self-test only runs in testnet mode."
            events += self_test(ex or Exchange(), symbol or "BTCUSDT")
        elif action == "research":
            sym = (symbol or "").upper(); sym = sym if sym.endswith("USDT") else sym + "USDT"
            pos = next((p for p in S["positions"] if p["symbol"] == sym), None)
            if not pos:
                return 404, f"No open position in {sym}."
            pos["research"] = (note or "checked")[:300]
            save(S)
            return 200, f"Research noted for {sym}."
        elif action == "close":
            sym = (symbol or "").upper()
            sym = sym if sym.endswith("USDT") else sym + "USDT"
            pos = next((p for p in S["positions"] if p["symbol"] == sym), None)
            if not pos:
                return 404, f"No open position in {sym}."
            exit_now(S, ex or Exchange(), pos, "closed on request", ts, events)
        else:
            return 400, "Unknown action."
        save(S)
    finally:
        store.unlock("run")
    notify.send("\n".join(events))
    return 200, " ".join(events)
