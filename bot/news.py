"""News signals for the paper trader.

Sources (all public, no key): Binance announcements (listings, delistings,
monitoring tags, security notices), Cointelegraph RSS, CoinDesk RSS.
Headlines are matched to coins by ticker ("(ABC)" or a standalone ABC) or by
name for well-known coins, then tagged positive / negative by keywords.
"""
import json, re, time, urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

UA = {"User-Agent": "Mozilla/5.0 (compatible; claude-paper-trader/1.0)"}
FEEDS = ["https://cointelegraph.com/rss", "https://www.coindesk.com/arc/outboundfeeds/rss"]
BINANCE = "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query?type=1&pageNo=1&pageSize=20"

NAMES = {  # names used in headlines for well-known coins
    "BTC": ["bitcoin"], "ETH": ["ethereum", "ether"], "SOL": ["solana"], "BNB": ["bnb chain"],
    "XRP": ["xrp", "ripple"], "DOGE": ["dogecoin"], "ADA": ["cardano"], "AVAX": ["avalanche"],
    "DOT": ["polkadot"], "LINK": ["chainlink"], "TRX": ["tron"], "TON": ["toncoin"],
    "SUI": ["sui network"], "APT": ["aptos"], "NEAR": ["near protocol"], "ARB": ["arbitrum"],
    "OP": ["optimism"], "LTC": ["litecoin"], "PEPE": ["pepe"], "SHIB": ["shiba inu"],
    "UNI": ["uniswap"], "AAVE": ["aave"], "HYPE": ["hyperliquid"], "ENA": ["ethena"],
}
COMMON = {"THE", "AND", "FOR", "NEW", "ALL", "ONE", "NOT", "ARE", "CEO", "SEC", "ETF", "USD",
          "API", "NFT", "DAO", "AI", "IPO", "CFTC", "FED", "GDP", "CPI", "WILL", "NOW", "TOP",
          "BIG", "HOT", "OUT", "KEY", "LAB", "BANK", "PAY", "WIN", "ACT", "USA", "UAE"}

NEG = [r"\bhack(ed|s)?\b", r"\bexploit", r"\bdrain(ed)?\b", r"\bdelist", r"removal of spot",
       r"monitoring tag", r"\blawsuit", r"\bsued\b", r"\bcharges?\b", r"\bfraud", r"\brug ?pull",
       r"\boutage", r"\bhalt(s|ed)?\b", r"\bsuspend", r"\btoken unlock",
       r"\binvestigat", r"\bbankrupt", r"\binsolven", r"\bdepeg", r"\battack(ed|er)?\b",
       r"\bbreach", r"\bstolen\b", r"\bsecurity incident", r"\bwithdrawals? (paused|halted)"]
POS = [r"\bwill list\b", r"\blisting\b", r"\bpartnership", r"\bpartners with\b", r"\bapprov",
       r"\bmainnet\b", r"\bupgrade\b", r"\bintegrat", r"\badopt", r"\blaunch(es|ed)? on binance",
       r"\bhodler airdrop", r"\bbuyback", r"\bburn\b"]
NEG_RE = [re.compile(p, re.I) for p in NEG]
POS_RE = [re.compile(p, re.I) for p in POS]
# Market-direction words: used only for the overall mood, not for single coins.
BEAR = [re.compile(p, re.I) for p in [r"\bplunge", r"\bslide", r"\bslump", r"\bdips?\b", r"\bsell-?off",
        r"\boutflows?\b", r"\bliquidations?\b", r"\bbear", r"\bcrash", r"\b\d+-(week|month) low",
        r"\btumble", r"\bfalls?\b", r"\bdrops?\b", r"\bjitters", r"\bfear\b", r"\bweekly loss"]]
BULL = [re.compile(p, re.I) for p in [r"\brally", r"\bsurge", r"\bsoar", r"\bjumps?\b", r"\binflows?\b",
        r"\brecord high", r"\ball-time high", r"\bbull", r"\brebound", r"\bclimbs?\b", r"\bgains?\b"]]
BINANCE_RISK_CATS = ("Binance: Delisting", "Binance: Latest Binance News")


def _get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=8) as r:
        return r.read()


def fetch():
    """Return list of {title, ts_ms, source} from all sources; failures are skipped."""
    items, errors = [], []
    for url in FEEDS:
        try:
            root = ET.fromstring(_get(url))
            src = "Cointelegraph" if "cointelegraph" in url else "CoinDesk"
            for it in root.iter("item"):
                t = (it.findtext("title") or "").strip()
                d = it.findtext("pubDate")
                if t and d:
                    items.append({"title": t, "ts_ms": int(parsedate_to_datetime(d).timestamp() * 1000),
                                  "source": src})
        except Exception as e:
            errors.append(f"{url}: {e.__class__.__name__}")
    try:
        data = json.loads(_get(BINANCE))
        for cat in data["data"]["catalogs"]:
            for a in cat.get("articles", []):
                items.append({"title": a["title"], "ts_ms": int(a["releaseDate"]),
                              "source": "Binance: " + cat["catalogName"]})
    except Exception as e:
        errors.append(f"binance: {e.__class__.__name__}")
    return items, errors


def tone(title):
    neg = any(r.search(title) for r in NEG_RE)
    pos = any(r.search(title) for r in POS_RE)
    if neg:
        return -1          # bad news wins if both
    return 1 if pos else 0


def mentions(title, base):
    if base in COMMON:
        return False
    if re.search(r"\(" + re.escape(base) + r"\)", title):
        return True
    if len(base) >= 3 and re.search(r"(?<![A-Za-z0-9])" + re.escape(base) + r"(?![A-Za-z0-9])", title):
        return True
    low = title.lower()
    return any(re.search(r"\b" + re.escape(n) + r"\b", low) for n in NAMES.get(base, []))


def for_coin(items, base, now_ms, hours=24, binance_neg_days=14):
    """News about one coin. Binance delist/monitoring notices count for 14 days."""
    out = []
    for it in items:
        age_h = (now_ms - it["ts_ms"]) / 3.6e6
        if age_h < 0 or not mentions(it["title"], base):
            continue
        t = tone(it["title"])
        if it["source"].startswith("Binance") and not it["source"].startswith(BINANCE_RISK_CATS) and t < 0:
            t = 0          # promo/activity posts are not risk events
        long_lived = it["source"].startswith(BINANCE_RISK_CATS) and t < 0
        if age_h <= hours or (long_lived and age_h <= binance_neg_days * 24):
            out.append({**it, "tone": t, "age_h": round(age_h, 1)})
    out.sort(key=lambda x: x["age_h"])
    return out


def market_mood(items, now_ms, hours=12):
    """Rough count of good vs bad headlines across the whole market in the last few hours."""
    recent = [it for it in items if 0 <= (now_ms - it["ts_ms"]) / 3.6e6 <= hours]
    bear = sum(1 for it in recent if any(r.search(it["title"]) for r in BEAR))
    bull = sum(1 for it in recent if any(r.search(it["title"]) for r in BULL))
    if bear >= 3 and bear >= 2 * max(bull, 1):
        label = "risk-off"
    elif bull >= 3 and bull >= 2 * max(bear, 1):
        label = "risk-on"
    else:
        label = "neutral"
    return {"headlines": len(recent), "bearish": bear, "bullish": bull, "label": label}


if __name__ == "__main__":
    import sys
    items, errs = fetch()
    now = int(time.time() * 1000)
    print(len(items), "items", errs, market_mood(items, now))
    for base in sys.argv[1:] or ["BTC", "ETH", "SOL"]:
        for n in for_coin(items, base, now):
            print(base, n["tone"], n["age_h"], "h", n["source"], "|", n["title"][:100])
