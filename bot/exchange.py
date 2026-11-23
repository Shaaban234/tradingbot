"""Binance spot trading (testnet or real). Signed requests use the key in Vercel's settings.

The bot only ever: buys with a market order, protects the position with a stop-loss/take-profit
order that lives on Binance, moves that protection up, and sells. It never withdraws or transfers.
"""
import hashlib, hmac, json, time, urllib.error, urllib.parse, urllib.request
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from . import config


class BinanceError(Exception):
    def __init__(self, status, code, msg):
        super().__init__(f"Binance {status} {code}: {msg}")
        self.status, self.code, self.msg = status, code, msg


def _fmt(x, unit, rounding=ROUND_DOWN):
    """Round x to a multiple of unit (tick or step size) and print it without exponent."""
    u = Decimal(str(unit))
    q = (Decimal(str(x)) / u).to_integral_value(rounding=rounding) * u
    return format(q.normalize() if q != q.to_integral() else q.quantize(Decimal(1)), "f")


class Exchange:
    def __init__(self, base=config.TRADE_API, key=config.BINANCE_KEY, secret=config.BINANCE_SECRET):
        self.base, self.key, self.secret = base, key, secret
        self._offset = None
        self._rules = {}

    # ---------- plumbing
    def _send(self, method, path, params=None, signed=False):
        params = dict(params or {})
        headers = {"User-Agent": "cloud-trading-bot/1.0"}
        if signed:
            if not (self.key and self.secret):
                raise BinanceError(0, 0, "BINANCE_KEY / BINANCE_SECRET are not set in Vercel")
            if self._offset is None:
                self._offset = self._send("GET", "/time")["serverTime"] - int(time.time() * 1000)
            params["timestamp"] = int(time.time() * 1000) + self._offset
            params["recvWindow"] = 10000
            qs = urllib.parse.urlencode(params)
            qs += "&signature=" + hmac.new(self.secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
            headers["X-MBX-APIKEY"] = self.key
        else:
            qs = urllib.parse.urlencode(params)
        url = self.base + path + ("?" + qs if qs else "")
        req = urllib.request.Request(url, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read())
            except Exception:
                body = {}
            raise BinanceError(e.code, body.get("code"), body.get("msg", str(e)))

    # ---------- public data on the trading venue (testnet prices differ from the real market)
    def rules(self, sym):
        if sym not in self._rules:
            info = self._send("GET", "/exchangeInfo", {"symbol": sym})["symbols"][0]
            f = {x["filterType"]: x for x in info["filters"]}
            mn = f.get("NOTIONAL", {}).get("minNotional") or f.get("MIN_NOTIONAL", {}).get("minNotional") or "5"
            self._rules[sym] = {"status": info["status"], "tick": float(f["PRICE_FILTER"]["tickSize"]),
                                "step": float(f["LOT_SIZE"]["stepSize"]), "min_qty": float(f["LOT_SIZE"]["minQty"]),
                                "min_notional": float(mn), "oco": info.get("ocoAllowed", True)}
        return self._rules[sym]

    def has_symbol(self, sym):
        try:
            return self.rules(sym)["status"] == "TRADING"
        except Exception:
            return False

    def bbo(self, sym):
        b = self._send("GET", "/ticker/bookTicker", {"symbol": sym})
        return float(b["bidPrice"]), float(b["askPrice"])

    def highs_since(self, sym, start_ms):
        """Highest price on the venue since start_ms (5-minute candles)."""
        rows = self._send("GET", "/klines", {"symbol": sym, "interval": "5m", "limit": 1000, "startTime": int(start_ms)})
        return max([float(r[2]) for r in rows], default=None)

    # ---------- account
    def free(self, asset):
        for b in self._send("GET", "/account", {"omitZeroBalances": "true"}, signed=True)["balances"]:
            if b["asset"] == asset:
                return float(b["free"])
        return 0.0

    def floor_qty(self, sym, qty):
        return float(_fmt(qty, self.rules(sym)["step"]))

    # ---------- orders
    def market_buy(self, sym, quote_usdt):
        r = self._send("POST", "/order", {"symbol": sym, "side": "BUY", "type": "MARKET",
                                           "quoteOrderQty": _fmt(quote_usdt, 0.01), "newOrderRespType": "FULL"}, signed=True)
        base = sym[:-4]
        qty = float(r["executedQty"])
        fee_base = sum(float(f["commission"]) for f in r.get("fills", []) if f.get("commissionAsset") == base)
        spent = float(r["cummulativeQuoteQty"])
        return {"order_id": r["orderId"], "qty_gross": qty, "qty": qty - fee_base, "spent": spent,
                "avg": spent / qty if qty else 0.0}

    def market_sell(self, sym, qty):
        q = _fmt(qty, self.rules(sym)["step"])
        r = self._send("POST", "/order", {"symbol": sym, "side": "SELL", "type": "MARKET", "quantity": q,
                                           "newOrderRespType": "FULL"}, signed=True)
        sold = float(r["executedQty"]); got = float(r["cummulativeQuoteQty"])
        fee_q = sum(float(f["commission"]) for f in r.get("fills", []) if f.get("commissionAsset") == "USDT")
        return {"qty": sold, "proceeds": got - fee_q, "avg": got / sold if sold else 0.0}

    def protect(self, sym, qty, stop, target=None):
        """Place the exit orders on Binance: stop-loss + take-profit (OCO), or just a stop when there's no target."""
        R = self.rules(sym)
        q = _fmt(qty, R["step"])
        sp = _fmt(stop, R["tick"])
        lp = _fmt(stop * (1 - config.STOP_LIMIT_GAP), R["tick"])
        if target and R["oco"]:
            tp = _fmt(target, R["tick"], ROUND_UP)
            r = self._send("POST", "/orderList/oco", {
                "symbol": sym, "side": "SELL", "quantity": q,
                "aboveType": "LIMIT_MAKER", "abovePrice": tp,
                "belowType": "STOP_LOSS_LIMIT", "belowStopPrice": sp, "belowPrice": lp, "belowTimeInForce": "GTC"},
                signed=True)
            return {"kind": "oco", "id": r["orderListId"], "stop": float(sp), "target": float(tp), "qty": float(q)}
        r = self._send("POST", "/order", {"symbol": sym, "side": "SELL", "type": "STOP_LOSS_LIMIT", "timeInForce": "GTC",
                                           "quantity": q, "stopPrice": sp, "price": lp}, signed=True)
        return {"kind": "stop", "id": r["orderId"], "stop": float(sp), "target": None, "qty": float(q)}

    def unprotect(self, sym, prot):
        try:
            if prot["kind"] == "oco":
                self._send("DELETE", "/orderList", {"symbol": sym, "orderListId": prot["id"]}, signed=True)
            else:
                self._send("DELETE", "/order", {"symbol": sym, "orderId": prot["id"]}, signed=True)
        except BinanceError as e:
            if e.code not in (-2011,):          # -2011 = already filled / cancelled: fine
                raise

    def protection_result(self, sym, prot):
        """None while the exit orders are still waiting; otherwise what was sold: {qty, proceeds, avg, how}."""
        ids = []
        if prot["kind"] == "oco":
            lst = self._send("GET", "/orderList", {"orderListId": prot["id"]}, signed=True)
            if lst.get("listOrderStatus") not in ("ALL_DONE", "REJECT"):
                return None
            ids = [o["orderId"] for o in lst["orders"]]
        else:
            ids = [prot["id"]]
        qty = proceeds = 0.0; how = None; still_open = False
        for oid in ids:
            o = self._send("GET", "/order", {"symbol": sym, "orderId": oid}, signed=True)
            if o["status"] in ("NEW", "PARTIALLY_FILLED"):
                still_open = True
            ex = float(o["executedQty"])
            if ex > 0:
                qty += ex; proceeds += float(o["cummulativeQuoteQty"])
                how = "take-profit" if o["type"] == "LIMIT_MAKER" else "stop"
        if still_open:
            return None
        if qty == 0:
            return {"qty": 0.0, "proceeds": 0.0, "avg": 0.0, "how": "cancelled"}
        return {"qty": qty, "proceeds": proceeds * (1 - 0.001), "avg": proceeds / qty, "how": how}
