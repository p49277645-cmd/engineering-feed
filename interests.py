# interests.py — интересы + авто-подбор источников («мощный поиск» без API-ключей)
import os, re, json, time, sqlite3
from collections import Counter
from contextlib import contextmanager
from urllib.parse import quote_plus
import feedparser, requests
from flask import Blueprint, request, jsonify, session

bp = Blueprint("interests", __name__)
DB_PATH = os.environ.get("DB_PATH", "engfeed.db")

FEED_MAX_GUEST, FEED_MAX_USER, PIN_MAX = 10, 13, 3
CACHE_TTL = 7 * 86400

HTTP = requests.Session()
HTTP.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})

@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def init_interests(app):
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS interests(
            slug TEXT PRIMARY KEY, title TEXT, top_sites TEXT,
            created_at REAL, refreshed_at REAL);
        CREATE TABLE IF NOT EXISTS interest_sources(
            id INTEGER PRIMARY KEY, slug TEXT, url TEXT UNIQUE, name TEXT, kind TEXT);
        CREATE TABLE IF NOT EXISTS user_interests(
            user_id INTEGER, slug TEXT, pinned INTEGER DEFAULT 0, pos INTEGER,
            PRIMARY KEY(user_id, slug));
        """)
        try:
            c.execute("ALTER TABLE posts ADD COLUMN interest TEXT DEFAULT ''")
        except Exception:
            pass

def slugify(text):
    text = re.sub(r"[^a-zа-яё0-9]+", "-", (text or "").lower().strip())
    return text.strip("-")[:40] or "topic"

def _fetch(url, limit=15):
    try:
        r = HTTP.get(url, timeout=8)
        r.raise_for_status()
        f = feedparser.parse(r.content)
        out = []
        for e in f.entries[:limit]:
            src = getattr(e, "source", None)
            st = src.get("title", "") if isinstance(src, dict) else ""
            out.append({"title": e.get("title", ""), "link": e.get("link", ""), "source_title": st})
        return out
    except Exception:
        return []

def discover(query):
    """Ищет источники и топ-сайты по интересу. Кешируется в БД."""
    q = (query or "").strip()
    slug = slugify(q)
    now = time.time()
    with db() as c:
        row = c.execute("SELECT * FROM interests WHERE slug=?", (slug,)).fetchone()
        if row and (now - row["refreshed_at"]) < CACHE_TTL:
            srcs = c.execute("SELECT url,name,kind FROM interest_sources WHERE slug=?", (slug,)).fetchall()
            if srcs:
                return {"slug": slug, "title": row["title"],
                        "top_sites": json.loads(row["top_sites"] or "[]"),
                        "sources": [dict(s) for s in srcs], "cached": True}

    sources, top_sites = [], []
    gurl = f"https://news.google.com/rss/search?q={quote_plus(q)}&hl=ru&gl=RU&ceid=RU:ru"
    entries = _fetch(gurl)
    if len(entries) >= 3:
        sources.append({"url": gurl, "name": f"Google News: {q}", "kind": "news"})
        cnt = Counter(e["source_title"] for e in entries if e["source_title"])
        top_sites = [n for n, _ in cnt.most_common(5)]
    hurl = f"https://habr.com/ru/rss/search/?q={quote_plus(q)}&fl=ru"
    if len(_fetch(hurl, 5)) >= 3:
        sources.append({"url": hurl, "name": f"Хабр: {q}", "kind": "habr"})
    if re.fullmatch(r"[a-z0-9_]{2,25}", slug.replace("-", "_")):
        rurl = f"https://www.reddit.com/r/{slug.replace('-', '_')}/.rss"
        if len(_fetch(rurl, 5)) >= 3:
            sources.append({"url": rurl, "name": f"Reddit r/{slug.replace('-', '_')}", "kind": "reddit"})

    with db() as c:
        c.execute("INSERT OR REPLACE INTO interests(slug,title,top_sites,created_at,refreshed_at) VALUES (?,?,?,?,?)",
                  (slug, q, json.dumps(top_sites, ensure_ascii=False), now, now))
        for s in sources:
            try:
                c.execute("INSERT OR IGNORE INTO interest_sources(slug,url,name,kind) VALUES (?,?,?,?)",
                          (slug, s["url"], s["name"], s["kind"]))
            except Exception:
                pass
    return {"slug": slug, "title": q, "top_sites": top_sites, "sources": sources, "cached": False}

# ---------- хранение интересов (гость = session, юзер = БД) ----------
def _uid():
    return session.get("uid")

def _feed_max():
    return FEED_MAX_USER if _uid() else FEED_MAX_GUEST

def get_state():
    uid = _uid()
    if uid:
        with db() as c:
            rows = c.execute("""SELECT i.slug,i.title,u.pinned,u.pos FROM user_interests u
                                JOIN interests i ON i.slug=u.slug WHERE u.user_id=? ORDER BY u.pos""", (uid,)).fetchall()
        items = [{"slug": r["slug"], "title": r["title"], "pinned": bool(r["pinned"])} for r in rows]
    else:
        feed = session.get("interests", [])
        pins = set(session.get("pins", []))
        with db() as c:
            items = []
            for i, s in enumerate(feed):
                r = c.execute("SELECT title FROM interests WHERE slug=?", (s,)).fetchone()
                items.append({"slug": s, "title": r["title"] if r else s, "pinned": s in pins})
    return {"interests": items, "is_user": bool(uid),
            "limits": {"feed_max": _feed_max(), "pin_max": PIN_MAX}}

def save_state(feed_slugs, pin_slugs):
    feed_slugs = list(dict.fromkeys(feed_slugs))[:_feed_max()]
    pin_slugs = [s for s in dict.fromkeys(pin_slugs) if s in feed_slugs][:PIN_MAX]
    uid = _uid()
    if uid:
        with db() as c:
            c.execute("DELETE FROM user_interests WHERE user_id=?", (uid,))
            for i, s in enumerate(feed_slugs):
                c.execute("INSERT OR IGNORE INTO user_interests(user_id,slug,pinned,pos) VALUES (?,?,?,?)",
                          (uid, s, 1 if s in pin_slugs else 0, i))
    else:
        session["interests"] = feed_slugs
        session["pins"] = pin_slugs
    return get_state()

def adopt_guest_interests(uid):
    """После регистрации переносим интересы гостя в аккаунт."""
    feed = session.get("interests", [])
    pins = set(session.get("pins", []))
    with db() as c:
        for i, s in enumerate(feed[:FEED_MAX_USER]):
            c.execute("INSERT OR IGNORE INTO user_interests(user_id,slug,pinned,pos) VALUES (?,?,?,?)",
                      (uid, s, 1 if s in pins else 0, i))
    session.pop("interests", None)
    session.pop("pins", None)

# ---------- API ----------
@bp.get("/api/interests")
def api_get():
    return jsonify(get_state())

@bp.get("/api/interests/discover")
def api_discover():
    q = request.args.get("q", "").strip()
    if not q or len(q) > 60:
        return jsonify({"error": "bad_query"}), 400
    return jsonify(discover(q))

@bp.post("/api/interests/add")
def api_add():
    d = request.get_json(silent=True) or {}
    r = discover(d.get("q", ""))
    if not r["sources"]:
        return jsonify({"error": "no_sources"}), 404
    return jsonify({"slug": r["slug"], "title": r["title"], "top_sites": r["top_sites"]})

@bp.post("/api/interests/save")
def api_save():
    d = request.get_json(silent=True) or {}
    return jsonify(save_state(d.get("interests", []), d.get("pins", [])))
