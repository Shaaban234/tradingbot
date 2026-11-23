"""Reddit buzz for the paper trader, from public subreddit RSS (no login).

Reddit rate-limits hard, so feeds are fetched with gaps between requests and
cached for 50 minutes. Signal per coin over the last 72h: mention count,
bullish vs bearish wording, and scam/rug/hack talk (used as a veto).
"""
import json, os, re, time, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from html import unescape

from . import news, store  # ticker/name matching; cache lives in the bot's database

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "reddit_cache.json")
SUBS = ["CryptoCurrency", "CryptoMarkets", "altcoin", "CryptoMoonShots", "SatoshiStreetBets"]
UA = {"User-Agent": "python:cloud-trading-bot:v1.0 (personal trading research)"}
NS = {"a": "http://www.w3.org/2005/Atom"}
TTL_S = 50 * 60

BULL = re.compile(r"\b(moon|mooning|bullish|undervalued|gem|breakout|accumulat\w*|send it|pump(ing)?|"
                  r"buying|long|rally|ath|partnership|listing)\b", re.I)
BEAR = re.compile(r"\b(bearish|dump(ing|ed)?|sell(ing)?|dead|overvalued|exit liquidity|avoid|bagholder|"
                  r"crash(ing)?|bleeding|unlock)\b", re.I)
DANGER = re.compile(r"\b(scam|rug(ged| ?pull)?|hack(ed)?|exploit(ed)?|ponzi|drained|honeypot|fraud)\b", re.I)


def _fetch_sub(sub):
    url = f"https://www.reddit.com/r/{sub}/new/.rss?limit=100"
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=8) as r:
        root = ET.fromstring(r.read())
    out = []
    for e in root.findall("a:entry", NS):
        title = (e.findtext("a:title", "", NS) or "").strip()
        body = re.sub(r"<[^>]+>", " ", unescape(e.findtext("a:content", "", NS) or ""))
        ts = e.findtext("a:updated", "", NS) or e.findtext("a:published", "", NS)
        try:
            ms = int(datetime.fromisoformat(ts).timestamp() * 1000)
        except Exception:
            continue
        out.append({"sub": sub, "title": title, "text": re.sub(r"\s+", " ", body)[:400], "ts_ms": ms})
    return out


def fetch():
    """Posts from all subs; uses the cache when fresh, keeps old data for subs that fail."""
    try:
        cache = store.get_json("reddit_cache", {}) or {}
    except Exception:
        cache = {}
    now = time.time()
    errors = []
    for i, sub in enumerate(SUBS):
        ent = cache.get(sub)
        if ent and now - ent["at"] < TTL_S:
            continue
        if ent and now - ent.get("fail_at", 0) < 2 * 3600:       # failed recently: retry every 2h only
            errors.append(f"{sub}: waiting to retry")
            continue
        try:
            if i:
                time.sleep(3)
            cache[sub] = {"at": now, "posts": _fetch_sub(sub)}
        except Exception as e:
            errors.append(f"{sub}: {getattr(e, 'code', e.__class__.__name__)}")
            cache.setdefault(sub, {"at": 0, "posts": []})["fail_at"] = now
    try:
        store.set_json("reddit_cache", cache)
    except Exception:
        pass
    # never act on stale data: only subs refreshed in the last 6 hours count
    posts = [p for ent in cache.values() if now - ent.get("at", 0) < 6 * 3600 for p in ent.get("posts", [])]
    return posts, errors


def for_coin(posts, base, now_ms, hours=72):
    hits = []
    for p in posts:
        age_h = (now_ms - p["ts_ms"]) / 3.6e6
        if not 0 <= age_h <= hours:
            continue
        blob = p["title"] + " " + p["text"]
        if news.mentions(blob, base):
            hits.append(p)
    bull = sum(1 for p in hits if BULL.search(p["title"] + " " + p["text"]))
    bear = sum(1 for p in hits if BEAR.search(p["title"] + " " + p["text"]))
    # v5: a danger post only counts if the coin is named in the TITLE and the danger word is in the title too
    danger = [p["title"][:90] for p in hits
              if news.mentions(p["title"], base) and DANGER.search(p["title"])]
    return {"mentions": len(hits), "bullish": bull, "bearish": bear, "danger": danger,
            "titles": [p["title"][:90] for p in hits[:3]]}


if __name__ == "__main__":
    import sys
    posts, errs = fetch()
    now = int(time.time() * 1000)
    print(len(posts), "posts", errs)
    for b in sys.argv[1:] or ["BTC", "ETH", "SOL", "NIGHT"]:
        print(b, for_coin(posts, b, now))
