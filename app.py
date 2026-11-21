import os
import re
import time
import html
import sqlite3
import logging
import sys
import hashlib
from contextlib import contextmanager

import feedparser
import requests
from bs4 import BeautifulSoup
from flask import Flask, render_template_string, jsonify, request, session
from auth import auth_bp, init_auth, AUTH_FRAGMENT
import interests as interests_mod
from interests import bp as interests_bp, init_interests, INTERESTS_FRAGMENT, interest_source_items, current_slugs
from chat import bp as chat_bp, init_chat
import engine

logger = logging.getLogger("engfeed")
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
logger.addHandler(handler)
logger.setLevel(logging.INFO)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DB_PATH", "engfeed.db")
PORT = int(os.environ.get("PORT", 5000))
TPL_PATH = os.path.join(BASE_DIR, "templates", "index.html")
CHAT_TPL_PATH = os.path.join(BASE_DIR, "templates", "chat.html")

PALETTE = ['#10b981', '#ef4444', '#a855f7', '#f59e0b', '#14b8a6', '#3b82f6', '#f97316']

def phash(s):
    x = 0
    for ch in s:
        x = (x * 31 + ord(ch)) & 0xFFFFFFFF
    return x

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
})

RUST_DB = {
    "ownership": {
        "title": "Владение и заимствование",
        "lang": "Rust",
        "content": '<p>Rust — скорость C++ и безопасность Python. Главная фишка — <b>Ownership</b>.</p><h2>Правила владения:</h2><ul><li>У каждого значения один владелец</li><li>Когда владелец выходит из области видимости — значение удаляется</li></ul><pre><code class="language-rust">fn main() {\n    let s1 = String::from("hello");\n    let s2 = s1;\n    println!("{}", s2);\n}</code></pre><a href="https://stepik.org/course/195449/promo" target="_blank" class="stepik-btn" style="--c:#ff9060">Пройти курс на Stepik →</a>'
    }
}

PYTHON_DB = {
    "generators": {
        "title": "Генераторы и yield",
        "lang": "Python",
        "content": '<p>Генераторы используют <code>yield</code> и «замораживают» состояние.</p><pre><code class="language-python">def count_up_to(max):\n    count = 1\n    while count <= max:\n        yield count\n        count += 1\n\ncounter = count_up_to(3)\nprint(next(counter)) # 1</code></pre><a href="https://stepik.org/course/214271/promo" target="_blank" class="stepik-btn" style="--c:#7cb8ea">Пройти курс на Stepik →</a>'
    }
}

FALLBACK_HTML = '<!DOCTYPE html><html><head><meta charset="utf-8"><title>EngFeed</title></head><body style="background:#0d0e0f;color:#e8e6e3;font-family:monospace;padding:40px"><h1>EngFeed</h1><p>Шаблон недоступен.</p></body></html>'

def clean_text(text):
    if not text: return ""
    text = html.unescape(text)
    text = re.sub(r'<.*?>', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def get_entry_image(entry):
    try:
        for m in getattr(entry, "media_content", []) or []:
            if m.get("url"): return m["url"]
    except Exception: pass
    try:
        for m in getattr(entry, "media_thumbnail", []) or []:
            if m.get("url"): return m["url"]
    except Exception: pass
    try:
        for enc in entry.get("enclosures", []) or []:
            if "image" in (enc.get("type") or ""): return enc.get("href")
    except Exception: pass
    try:
        m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', entry.get("summary", ""))
        if m: return m.group(1)
    except Exception: pass
    return ""

def rel_time(ts):
    d = max(0, time.time() - ts)
    if d < 3600: return str(max(1, int(d // 60))) + " мин назад"
    if d < 86400: return str(int(d // 3600)) + " ч назад"
    return str(int(d // 86400)) + " дн назад"

def read_minutes(title):
    try: h = int(hashlib.md5(title.encode()).hexdigest()[:6], 16)
    except Exception: h = 0
    return 3 + h % 10

def parse_rss(item):
    try:
        resp = SESSION.get(item["url"], timeout=(3, 6))
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
        results = []
        for entry in feed.entries[:item["limit"]]:
            pub_time = time.time()
            if hasattr(entry, 'published_parsed') and entry.published_parsed:
                try: pub_time = time.mktime(entry.published_parsed)
                except Exception: pass
            results.append({
                "title": clean_text(entry.get("title", "Без заголовка")),
                "link": entry.get("link", "#"),
                "source": item["source"],
                "snippet": clean_text(entry.get("summary", ""))[:220] + "...",
                "image": get_entry_image(entry),
                "pub_time": pub_time,
                "interest": item.get("interest", "")
            })
        logger.info(f"[RSS] {item['source']}: {len(results)}")
        return results
    except Exception as e:
        logger.warning(f"[RSS] {item['source']}: {e}")
        return []

def scrape_html(item):
    try:
        resp = SESSION.get(item["url"], timeout=(3, 6))
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        base_url = "/".join(item["url"].split("/")[:3])
        results = []
        nodes = soup.select("h2 a, h3 a, h4 a, .news-item a, .news-list a, article a, li a")[:50]
        seen = set()
        for node in nodes:
            href = node.get("href", "")
            title = clean_text(node.get_text())
            if not title or len(title) < 10: continue
            if any(x in href.lower() for x in ["login", "register", "tag", "category", "javascript"]): continue
            if href.startswith("/"): href = base_url + href
            elif not href.startswith("http"): href = base_url + "/" + href.lstrip("/")
            if href in seen: continue
            seen.add(href)
            results.append({
                "title": title[:120],
                "link": href,
                "source": item["source"],
                "snippet": "Материалы по твоему интересу",
                "image": "",
                "pub_time": time.time(),
                "interest": item.get("interest", "")
            })
            if len(results) >= item["limit"]: break
        logger.info(f"[Scrape] {item['source']}: {len(results)}")
        return results
    except Exception as e:
        logger.warning(f"[Scrape] {item['source']}: {e}")
        return []

def interest_titles():
    try:
        with engine.db() as c:
            rows = c.execute("SELECT slug, title FROM interests").fetchall()
        return {r["slug"]: r["title"] for r in rows}
    except Exception:
        return {}

def enrich_items(all_items):
    titles = interest_titles()
    enriched = []
    now = time.time()
    for it in all_items:
        ts0 = it.get("pub_time") or now
        if ts0 < now - 45 * 86400:
            continue
        slug = it.get("interest", "") or "topic"
        title = titles.get(slug, slug)
        ts = it.get("pub_time") or time.time()
        enriched.append({
            "title": it["title"], "link": it["link"], "source": it["source"],
            "snippet": it["snippet"], "image": it.get("image", ""),
            "group": slug, "group_name": title,
            "color": PALETTE[phash(slug) % len(PALETTE)],
            "letter": (title[:1] or "•").upper(),
            "time_str": rel_time(ts), "read_min": read_minutes(it["title"]),
            "category": "interest", "pub_time": ts, "day": "",
            "interest": slug,
        })
    enriched.sort(key=lambda x: x["pub_time"], reverse=True)
    logger.info(f"Итого: {len(enriched)} записей")
    return enriched

def current_sources():
    return interest_source_items()

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def init_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS posts (
                link TEXT PRIMARY KEY, title TEXT, source TEXT, snippet TEXT,
                color TEXT, letter TEXT, day TEXT, time_str TEXT,
                category TEXT, fetched_at REAL, image TEXT, read_min INTEGER, "group" TEXT
            );
        """)
        for col, typ in [("image", "TEXT"), ("read_min", "INTEGER"), ('"group"', "TEXT"),
                         ("interest", "TEXT"), ("pub_time", "REAL")]:
            try:
                conn.execute("ALTER TABLE posts ADD COLUMN " + col + " " + typ)
            except Exception:
                pass

def save_to_db(items):
    with get_db() as conn:
        for it in items:
            conn.execute("""
                INSERT OR REPLACE INTO posts
                (link, title, source, snippet, color, letter, day, time_str, category, fetched_at, image, read_min, "group", interest, pub_time)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (it["link"], it["title"], it["source"], it["snippet"], it["color"], it["letter"],
                  it["day"], it["time_str"], it["category"], time.time(),
                  it.get("image", ""), it.get("read_min", 5), it.get("group", "topic"), it.get("interest", ""),
                  it.get("pub_time", time.time())))

def load_from_db(limit=200):
    with get_db() as conn:
        cur = conn.execute("SELECT * FROM posts ORDER BY fetched_at DESC LIMIT ?", (limit,))
        return [dict(r) for r in cur.fetchall()]

def normalize(rows):
    for r in rows:
        if not r.get("color"): r["color"] = PALETTE[phash(r.get("group") or "x") % len(PALETTE)]
        if not r.get("letter"): r["letter"] = ((r.get("group_name") or "•")[:1]).upper()
        if not r.get("group_name"): r["group_name"] = r.get("group") or "Интерес"
        if r.get("read_min") is None: r["read_min"] = 5
        if r.get("image") is None: r["image"] = ""
        if not r.get("time_str"): r["time_str"] = rel_time(r.get("fetched_at") or time.time())
    return rows

def load_template():
    try:
        with open(TPL_PATH, encoding="utf-8") as f:
            t = f.read()
        t = t.replace("<!--AUTH-->", AUTH_FRAGMENT)
        t = t.replace("<!--INTERESTS-->", INTERESTS_FRAGMENT)
        return t
    except Exception as e:
        logger.error(f"template load failed: {e}")
        return FALLBACK_HTML

def load_chat_template():
    try:
        with open(CHAT_TPL_PATH, encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        logger.error(f"chat template load failed: {e}")
        return FALLBACK_HTML

app = Flask(__name__)
try:
    init_db()
except Exception as e:
    logger.error(f"init_db failed: {e}")
try:
    init_auth(app)
except Exception as e:
    logger.error(f"init_auth failed: {e}")
try:
    init_interests(app)
except Exception as e:
    logger.error(f"init_interests failed: {e}")
try:
    init_chat(app)
except Exception as e:
    logger.error(f"init_chat failed: {e}")
try:
    engine.init_engine([])
except Exception as e:
    logger.error(f"init_engine failed: {e}")
app.register_blueprint(auth_bp)
app.register_blueprint(interests_bp)
app.register_blueprint(chat_bp)
interests_mod.set_refresher(lambda: engine.start(current_sources(), parse_rss, scrape_html, enrich_items, save_to_db))

HTML_TEMPLATE = load_template()
CHAT_TEMPLATE = load_chat_template()

def render_page(**ctx):
    try:
        return render_template_string(HTML_TEMPLATE, **ctx)
    except Exception as e:
        logger.error(f"render failed: {e}")
        return FALLBACK_HTML

def feed_news():
    news = normalize(load_from_db())
    only = request.args.get("int", "")
    slugs = current_slugs()
    if only:
        news = [n for n in news if n.get("interest") == only]
    elif slugs:
        news = [n for n in news if n.get("interest") in slugs]
    else:
        news = []
    return news, (not slugs and not only)

@app.route('/')
def index():
    news, no_interests = feed_news()
    return render_page(news=news, groups={}, active_tab='feed', no_interests=no_interests)

@app.route('/wiki/rust')
def wiki_rust():
    topic = request.args.get('topic', list(RUST_DB.keys())[0])
    return render_page(active_tab='rust', db=RUST_DB, current_topic=topic, news=[], groups={}, no_interests=False)

@app.route('/wiki/python')
def wiki_python():
    topic = request.args.get('topic', list(PYTHON_DB.keys())[0])
    return render_page(active_tab='python', db=PYTHON_DB, current_topic=topic, news=[], groups={}, no_interests=False)

@app.route('/chat')
def chat_page():
    if not session.get("uid"):
        return jsonify({"error": "not_authenticated"}), 401
    try:
        return render_template_string(CHAT_TEMPLATE, session=session)
    except Exception as e:
        logger.error(f"chat render failed: {e}")
        return FALLBACK_HTML

@app.route('/api/feed')
def api_feed():
    news, no_interests = feed_news()
    return jsonify({"news": news, "no_interests": no_interests})

@app.route('/api/refresh', methods=['GET', 'POST'])
def api_refresh():
    started = engine.start(current_sources(), parse_rss, scrape_html, enrich_items, save_to_db)
    st = engine.status()
    return jsonify({"status": "started" if started else "already_running",
                    "running": st["running"], "total": st["total"],
                    "ok": st["ok"], "failed": st["failed"]})

@app.route('/api/refresh/status')
def api_refresh_status():
    return jsonify(engine.status())

@app.route('/health')
def health():
    return jsonify({"status": "ok", "sources": len(current_sources())})

try:
    engine.warm_if_needed(current_sources(), parse_rss, scrape_html, enrich_items, save_to_db)
except Exception as e:
    logger.error(f"warm failed: {e}")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
