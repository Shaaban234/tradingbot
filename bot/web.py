"""Small helpers shared by the api/ endpoints (Vercel's Python runtime uses BaseHTTPRequestHandler)."""
import json, secrets
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from . import config


class Base(BaseHTTPRequestHandler):
    def query(self):
        return {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}

    def reply(self, code, body):
        if isinstance(body, (dict, list)):
            data, ctype = json.dumps(body, indent=1, default=str).encode(), "application/json"
        else:
            data, ctype = (f"<!doctype html><meta name=viewport content='width=device-width'>"
                           f"<body style='font:18px system-ui;padding:24px;max-width:640px'><p>{body}</p>").encode(), "text/html; charset=utf-8"
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def token_ok(self, given):
        return bool(config.CONTROL_TOKEN) and secrets.compare_digest(given or "", config.CONTROL_TOKEN)

    def cron_ok(self):
        auth = self.headers.get("Authorization", "")
        return bool(config.CRON_SECRET) and secrets.compare_digest(auth, f"Bearer {config.CRON_SECRET}")

    def log_message(self, *a):
        pass
