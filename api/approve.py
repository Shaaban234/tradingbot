"""/api/approve?id=..&code=.. : the one-time link sent in Discord in approval mode."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bot.web import Base
from bot import engine


class handler(Base):
    def do_GET(self):
        q = self.query()
        code, msg = engine.approve(q.get("id", ""), q.get("code", ""))
        self.reply(code, msg)
