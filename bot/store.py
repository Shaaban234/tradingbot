"""Bot memory between runs: Upstash Redis over HTTPS (free plan). Falls back to a local file for tests."""
import json, os, urllib.request

URL = os.environ.get("UPSTASH_REDIS_REST_URL") or os.environ.get("KV_REST_API_URL") or ""
TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN") or os.environ.get("KV_REST_API_TOKEN") or ""
LOCAL = os.environ.get("BOT_LOCAL_STORE", "/tmp/tradingbot_store.json")
PREFIX = "tb:"


def configured():
    return bool(URL and TOKEN)


def _cmd(*args):
    if not configured():
        return _local(*args)
    req = urllib.request.Request(URL, data=json.dumps([str(a) for a in args]).encode(),
                                 headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        out = json.loads(r.read())
    if "error" in out:
        raise RuntimeError(f"redis: {out['error']}")
    return out.get("result")


def _local(*args):
    """Tiny stand-in for Redis (tests / local runs): GET, SET [NX] [EX n], DEL."""
    try:
        db = json.load(open(LOCAL))
    except Exception:
        db = {}
    args = [str(a) for a in args]
    op, key = args[0].upper(), args[1]
    if op == "GET":
        return db.get(key)
    if op == "DEL":
        existed = key in db
        db.pop(key, None)
        json.dump(db, open(LOCAL, "w"))
        return 1 if existed else 0
    if op == "SET":
        if "NX" in [a.upper() for a in args[3:]] and key in db:
            return None
        db[key] = args[2]
        json.dump(db, open(LOCAL, "w"))
        return "OK"
    raise ValueError(op)


def get_json(key, default=None):
    v = _cmd("GET", PREFIX + key)
    return json.loads(v) if v else default


def set_json(key, value):
    _cmd("SET", PREFIX + key, json.dumps(value, separators=(",", ":")))


def lock(key, seconds):
    """True if we got the lock (nobody else holds it)."""
    return _cmd("SET", PREFIX + "lock:" + key, "1", "NX", "EX", int(seconds)) == "OK"


def unlock(key):
    _cmd("DEL", PREFIX + "lock:" + key)
