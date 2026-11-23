"""/api/status?token=CONTROL_TOKEN : everything the bot knows (positions, trades, balance, errors). Read-only."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bot.web import Base
from bot import engine, config


class handler(Base):
    def do_GET(self):
        if not self.token_ok(self.query().get("token")):
            return self.reply(401, {"error": "not allowed"})
        S = engine.load()
        S = {k: v for k, v in S.items() if k != "approvals"} | {"pending_approvals": [a["symbol"] for a in S["approvals"].values()]}
        S["equity_now"] = round(engine.equity(S), 4)
        S["mode"] = config.MODE
        S["equity_hist"] = S["equity_hist"][-100:]
        self.reply(200, S)
