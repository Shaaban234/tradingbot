"""/api/control?token=CONTROL_TOKEN&action=pause|resume|close&symbol=XYZ"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bot.web import Base
from bot import engine


class handler(Base):
    def do_GET(self):
        q = self.query()
        if not self.token_ok(q.get("token")):
            return self.reply(401, "Not allowed.")
        if q.get("action") not in ("pause", "resume", "close"):
            return self.reply(400, "Use action=pause, resume or close (with symbol=...).")
        code, msg = engine.control(q["action"], q.get("symbol"))
        self.reply(code, msg)
