"""Called by Vercel's schedule every 15 minutes. Also runnable by hand: /api/run?token=CONTROL_TOKEN"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bot.web import Base
from bot import engine


class handler(Base):
    def do_GET(self):
        if not (self.cron_ok() or self.token_ok(self.query().get("token"))):
            return self.reply(401, {"error": "not allowed"})
        host = self.headers.get("x-forwarded-host") or self.headers.get("Host") or ""
        self.reply(200, engine.run_cycle(host=host))
