from http.server import BaseHTTPRequestHandler
import json, os, urllib.request

URLS = {
    "binance_main_ping": "https://api.binance.com/api/v3/ping",
    "binance_main_time": "https://api.binance.com/api/v3/time",
    "binance_testnet_ping": "https://testnet.binance.vision/api/v3/ping",
    "my_server_ip": "https://api.ipify.org?format=json",
}

def check(url):
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return {"ok": True, "status": r.status, "body": r.read(300).decode()}
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code, "body": e.read(300).decode(errors="ignore")}
    except Exception as e:
        return {"ok": False, "error": str(e)}

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        out = {"region": os.environ.get("VERCEL_REGION", "?")}
        out.update({k: check(u) for k, u in URLS.items()})
        ok = out["binance_main_ping"].get("ok")
        out["VERDICT"] = "BINANCE WORKS FROM HERE" if ok else "BINANCE BLOCKED FROM HERE"
        body = json.dumps(out, indent=2).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)
