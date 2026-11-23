"""Discord messages through the webhook saved in Vercel's DISCORD_WEBHOOK setting."""
import json, urllib.request
from . import config


def send(text):
    if not config.DISCORD_WEBHOOK:
        print("[discord off]", text)
        return False
    uid = config.DISCORD_USER_ID
    if uid:
        text = f"<@{uid}> " + text
    tag = {"testnet": "🧪 TESTNET", "approval": "✋ REAL (approval)", "live": "💵 REAL"}[config.MODE]
    ok = True
    chunks = [text[i:i + 1900] for i in range(0, len(text), 1900)] or [""]
    for i, chunk in enumerate(chunks):
        body = {"username": f"Cloud Bot · {tag}", "content": chunk,
                "allowed_mentions": {"users": [uid] if uid and i == 0 else []}}
        req = urllib.request.Request(config.DISCORD_WEBHOOK + "?wait=true", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "User-Agent": "cloud-trading-bot/1.0"})
        try:
            urllib.request.urlopen(req, timeout=15).read()
        except Exception as e:
            print("discord error", e)
            ok = False
    return ok
