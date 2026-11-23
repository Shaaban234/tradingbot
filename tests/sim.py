"""Offline test: fake market data + fake Binance. Run: python3 tests/sim.py"""
import os, sys, json, math, random, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["BOT_LOCAL_STORE"] = "/tmp/tb_sim_store.json"
if os.path.exists("/tmp/tb_sim_store.json"):
    os.remove("/tmp/tb_sim_store.json")
os.environ.pop("UPSTASH_REDIS_REST_URL", None); os.environ.pop("KV_REST_API_URL", None)

from bot import engine, market, news, guards, notify, store
from bot.exchange import BinanceError, _fmt

random.seed(7)
NOW = int(time.time() * 1000)
H = 3_600_000
PRICES = {}
SENT = []
notify.send = lambda t: SENT.append(t) or True
news.fetch = lambda: ([], [])


def candles(sym, interval, limit, dip):
    step = H if interval == "1h" else 4 * H
    p = {"BTCUSDT": 90000, "AAAUSDT": 2.0, "BBBUSDT": 0.5, "CCCUSDT": 13.0}[sym]
    end = (NOW // step) * step
    rows = []
    rng = random.Random(sym + interval)
    for i in range(limit):
        t = end - (limit - i) * step
        drift = 0.0004 if interval == "4h" else 0
        o = p; p = p * (1 + drift + rng.gauss(0, 0.006))
        if dip and interval == "1h" and i >= limit - 4:
            p *= 0.975                       # sharp dip at the end
        rows.append((t, o, max(o, p) * 1.002, min(o, p) * 0.998, p, 1000, t + step - 1))
    PRICES.setdefault(sym, p)
    return rows


market.universe = lambda top_n=150: ["BTCUSDT", "AAAUSDT", "BBBUSDT", "CCCUSDT"]
market.load_candles = lambda syms, workers=10: {s: (candles(s, "1h", 300, s in ("AAAUSDT", "BBBUSDT")),
                                                    candles(s, "4h", 250, False)) for s in syms}
engine.market_bbo = lambda s: (PRICES[s] * 0.9998, PRICES[s] * 1.0002)
market.get = lambda path, base=None, tries=3: []          # whale data: none


class FakeEx:
    def __init__(self):
        self.bal = {"USDT": 10000.0}; self.orders = {}; self.lists = {}; self.n = 100; self.fail_protect = False
    def rules(self, s): return {"status": "TRADING", "tick": 0.0001, "step": 0.01, "min_qty": 0.1, "min_notional": 5, "oco": True}
    def has_symbol(self, s): return True
    def bbo(self, s): return PRICES[s] * 0.9998, PRICES[s] * 1.0002
    def highs_since(self, s, ms): return PRICES[s] * 1.001
    def free(self, a): return self.bal.get(a, 0.0)
    def floor_qty(self, s, q): return float(_fmt(q, 0.01))
    def market_buy(self, s, quote):
        ask = PRICES[s] * 1.0002; q = float(_fmt(quote / ask, 0.01)); spent = q * ask
        self.bal["USDT"] -= spent; self.bal[s[:-4]] = self.bal.get(s[:-4], 0) + q * 0.999
        self.n += 1; return {"order_id": self.n, "qty_gross": q, "qty": q * 0.999, "spent": spent, "avg": ask}
    def market_sell(self, s, q):
        assert self.bal.get(s[:-4], 0) + 1e-9 >= q, "selling more than we own"
        self.bal[s[:-4]] -= q; got = q * PRICES[s] * 0.9998
        self.bal["USDT"] += got * 0.999; return {"qty": q, "proceeds": got * 0.999, "avg": PRICES[s] * 0.9998}
    def protect(self, s, q, stop, target=None):
        if self.fail_protect: raise BinanceError(400, -2010, "test failure")
        assert q <= self.bal.get(s[:-4], 0) + 1e-9, "protecting more than we own"
        self.n += 1; self.lists[self.n] = {"s": s, "q": q, "stop": stop, "tgt": target, "done": None}
        return {"kind": "oco" if target else "stop", "id": self.n, "stop": float(_fmt(stop, 0.0001)), "target": target, "qty": q}
    def unprotect(self, s, p): self.lists.pop(p["id"], None)
    def protection_result(self, s, p):
        L = self.lists.get(p["id"])
        if not L or not L["done"]: return None
        px = L["stop"] * 0.999 if L["done"] == "stop" else L["tgt"]
        self.bal[s[:-4]] -= L["q"]; self.bal["USDT"] += L["q"] * px
        return {"qty": L["q"], "proceeds": L["q"] * px * 0.999, "avg": px, "how": "stop" if L["done"] == "stop" else "take-profit"}


ex = FakeEx()
t0 = time.time()
r = engine.run_cycle(host="example.vercel.app", ex=ex)
print("run 1:", r, f"{time.time() - t0:.2f}s")
S = store.get_json(engine.STATE_KEY)
print("positions:", [(p["symbol"], round(p["cost"], 2), p["prot"]["kind"]) for p in S["positions"]], "cash", S["cash"])
assert S["positions"], "expected a dip buy"
assert all(p["prot"] for p in S["positions"]), "every position must be protected"
assert S["cash"] >= 0

# stop hits on first position
pid = S["positions"][0]["prot"]["id"]; ex.lists[pid]["done"] = "stop"
r = engine.run_cycle(host="x", ex=ex)
S = store.get_json(engine.STATE_KEY)
print("run 2 trades:", [(t["symbol"], t["exit_reason"], t["pnl_pct"]) for t in S["trades"]])
assert S["trades"] and S["trades"][0]["exit_reason"] == "stop-loss"
loser = S["trades"][0]["symbol"]
assert loser not in [p["symbol"] for p in S["positions"]], "no rebuy of a fresh loser"

# price jumps: stop should trail up
for p in S["positions"]:
    PRICES[p["symbol"]] *= 1.10
r = engine.run_cycle(host="x", ex=ex)
S = store.get_json(engine.STATE_KEY)
for p in S["positions"]:
    print("after +10%:", p["symbol"], "entry", round(p["entry_price"], 4), "stop", round(p["stop"], 4))
    assert p["stop"] > p["entry_price"], "stop should have moved above entry"

# protection failure -> must sell, not hold unprotected
ex.fail_protect = True
for p in S["positions"]:
    PRICES[p["symbol"]] *= 1.05
engine.run_cycle(host="x", ex=ex)
S = store.get_json(engine.STATE_KEY)
assert all(p.get("prot") for p in S["positions"]), "unprotected position left open"
ex.fail_protect = False

# control + approval paths
print(engine.control("pause"))
engine.run_cycle(host="x", ex=ex)
S = store.get_json(engine.STATE_KEY)
assert S["paused_by_user"]
print(engine.control("resume"))
print(engine.approve("nope", "bad", ex=ex))
assert ex.bal["USDT"] > 0 and all(v >= -1e-9 for v in ex.bal.values())
print("equity", round(engine.equity(S), 3), "budget", S["budget"], "trades", len(S["trades"]))
print("\n--- last check report ---\n" + [m for m in SENT if m.startswith("⏱️")][-1])
print("\nALL TESTS PASSED")

# ---- runner mode + 24h extend rule (score from the 29-strategy evaluation)
print("\n--- runner / extend ---")
S = store.get_json(engine.STATE_KEY)
for p in list(S["positions"]):
    S["positions"].remove(p)
S["cash"] = 12.0; store.set_json(engine.STATE_KEY, S)
PRICES["CCCUSDT"] = 13.0
plan = {"stop_pct": 0.03, "tgt_pct": 0.06, "why": "test", "signal_price": 13.0}
ev = []; engine.execute_buy(S, ex, "CCCUSDT", plan, NOW, ev); store.set_json(engine.STATE_KEY, S)
print("size bought:", round(S["positions"][0]["cost"], 2), "(paper rule: max(12*1.5%/3%, 6) capped at cash/2 = 6)")
market.universe = lambda top_n=150: ["BTCUSDT", "CCCUSDT"]
FULL = {"score": 0.5, "supertrend_dir": 1}
engine.full_signal = lambda sym, data, rank: dict(FULL)
PRICES["CCCUSDT"] = S["positions"][0]["target"] * 0.99          # near target
engine.run_cycle(host="x", ex=ex); S = store.get_json(engine.STATE_KEY); p = S["positions"][0]
print("runner:", p.get("runner"), "target", p["target"], "prot", p["prot"]["kind"], "stop", round(p["stop"], 4))
assert p["runner"] and p["target"] is None and p["prot"]["kind"] == "stop"
FULL = {"score": 0.1, "supertrend_dir": -1}
engine.run_cycle(host="x", ex=ex); S = store.get_json(engine.STATE_KEY); p = S["positions"][0]
print("fading:", p.get("runner"), "new target", round(p["target"], 4), "prot", p["prot"]["kind"])
assert not p["runner"] and p["target"] and p["prot"]["kind"] == "oco"
# 25h old, up 1%, but score 0.1 -> must close; with score 0.3 -> keep
p["entry_ms"] = NOW - 25 * H; store.set_json(engine.STATE_KEY, S)
FULL = {"score": 0.3, "supertrend_dir": 1}
engine.run_cycle(host="x", ex=ex); S = store.get_json(engine.STATE_KEY)
assert S["positions"], "should extend (up, score>=0.2)"
FULL = {"score": 0.1, "supertrend_dir": 1}
engine.run_cycle(host="x", ex=ex); S = store.get_json(engine.STATE_KEY)
assert not S["positions"] and "max hold" in S["trades"][-1]["exit_reason"]
print("extend rule ok; RUNNER/EXTEND TESTS PASSED")
