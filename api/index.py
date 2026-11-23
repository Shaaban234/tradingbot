"""Home page: just says the bot is alive (no private data)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bot.web import Base
from bot import config


class handler(Base):
    def do_GET(self):
        self.reply(200, f"Trading bot is deployed. Mode: <b>{config.MODE}</b>. Status and controls need your token.")
