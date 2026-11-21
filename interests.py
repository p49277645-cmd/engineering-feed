# interests.py — Полная версия с интеграцией чатов и исправленными кавычками
import os, re, json, time, sqlite3, logging, threading, statistics
from contextlib import contextmanager
from urllib.parse import quote_plus, urlparse, unquote
from concurrent.futures import ThreadPoolExecutor, as_completed
import feedparser, requests
from bs4 import BeautifulSoup
from flask import Blueprint, request, jsonify, session

bp = Blueprint("interests", __name__)
DB_PATH = os.environ.get("DB_PATH", "engfeed.db")
FEED_MAX_GUEST, FEED_MAX_USER, COURSE_MAX = 10, 13, 3
CACHE_TTL = 24 * 3600
PROBE = (2.0, 4.0)
REPROBE = (1.5, 2.0)
DISCOVER_DEADLINE = 16

FEED_PATHS = [
    "/feed", "/feed/", "/rss", "/rss/", "/rss.xml", "/atom.xml", "/index.xml",
    "/feed.xml", "/feeds/posts/default", "/blog/feed", "/news/feed", "/news/rss",
    "/articles/feed", "/?feed=rss2", "/feed/rss", "/feed/atom", "/rss/news",
    "/export/rss", "/api/rss", "/latest/rss", "/all/rss", "/index.rss",
    "/feed.rss", "/atom", "/rss/feed",
]

WHITELIST_BONUS = {"habr.com", "nplus1.ru", "elementy.ru", "cyberleninka.ru",
                   "opennet.ru", "arxiv.org", "stepik.org", "bing.com"}

HTTP = requests.Session()
HTTP.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8",
})

BANNED_TOPICS = {
    "порно", "porno", "porn", "xxx", "sex", "секс", "хентай", "hentai", "эротика",
    "erotica", "nude", "onlyfans", "camgirl", "порнуха",
    "наркотики", "drugs", "weed", "марихуана", "кокаин", "cocaine", "героин", "мефедрон",
    "насилие", "violence", "терроризм", "terrorism", "нацизм", "nazism", "экстремизм",
    "казино", "casino", "ставки", "betting", "скам", "scam", "фишинг", "phishing",
    "оружие", "weapons", "взрывчатка", "суицид", "suicide", "расчлен", "gore",
}
_CONF = str.maketrans({"а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "i", "ј": "j", "ѕ": "s"})
_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s", "!": "i"})
_RU_LAT = {"ж": "zh", "ч": "ch", "ш": "sh", "щ": "sch", "ц": "ts", "ю": "yu", "я": "ya",
           "ё": "yo", "й": "i", "ы": "y", "э": "e", "б": "b", "в": "v", "г": "g", "д": "d",
           "з": "z", "к": "k", "л": "l", "м": "m", "н": "n", "п": "p", "р": "r", "с": "s",
           "т": "t", "у": "u", "ф": "f", "х": "h", "и": "i", "е": "e", "а": "a", "о": "o"}

def _compact(s):
    s = (s or "").lower().translate(_CONF).translate(_LEET)
    return re.sub(r"[^a-zа-яё0-9]+", "", s)

def _translit(s):
    return "".join(_RU_LAT.get(ch, ch) for ch in s)

def topic_is_clean(query):
    a = _compact(query)
    b = _translit(a)
    for w in BANNED_TOPICS:
        t = _compact(w)
        if t and (t in a or t in b):
            return False
    return True

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
            slug TEXT PRIMARY KEY, title TEXT, top_sites TEXT, created_at REAL, refreshed_at REAL);
        CREATE TABLE IF NOT EXISTS interest_sources(
            id INTEGER PRIMARY KEY, slug TEXT, url TEXT UNIQUE, name TEXT, kind TEXT,
            domain TEXT DEFAULT '', score REAL DEFAULT 0, rf_ok INTEGER DEFAULT 1,
            fails INTEGER DEFAULT 0, latency REAL DEFAULT 0, checked_at REAL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS user_interests(
            user_id INTEGER, slug TEXT, pinned INTEGER DEFAULT 0, pos INTEGER,
            PRIMARY KEY(user_id, slug));
        CREATE TABLE IF NOT EXISTS courses(
            course_id INTEGER PRIMARY KEY, title TEXT, url TEXT, source TEXT, created_at REAL);
        CREATE TABLE IF NOT EXISTS user_courses(
            user_id INTEGER, course_id INTEGER, pos INTEGER, PRIMARY KEY(user_id, course_id));
        CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
        
        CREATE TABLE IF NOT EXISTS chat_rooms(
            id INTEGER PRIMARY KEY, slug TEXT UNIQUE NOT NULL, title TEXT NOT NULL,
            created_at REAL DEFAULT 0, is_active INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS chat_members(
            user_id INTEGER, room_id INTEGER, joined_at REAL DEFAULT 0,
            last_read_at REAL DEFAULT 0, is_muted INTEGER DEFAULT 0,
            PRIMARY KEY(user_id, room_id));
        CREATE TABLE IF NOT EXISTS chat_messages(
            id INTEGER PRIMARY KEY, room_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
            username TEXT NOT NULL, content TEXT NOT NULL, attachment_type TEXT,
            attachment_url TEXT, attachment_size INTEGER, created_at REAL NOT NULL,
            is_deleted INTEGER DEFAULT 0, edited_at REAL);
        CREATE TABLE IF NOT EXISTS chat_reports(
            id INTEGER PRIMARY KEY, message_id INTEGER NOT NULL, reporter_id INTEGER NOT NULL,
            reason TEXT NOT NULL, created_at REAL NOT NULL, resolved INTEGER DEFAULT 0);
        CREATE INDEX IF NOT EXISTS idx_messages_room ON chat_messages(room_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_members_user ON chat_members(user_id);
        """)
        try:
            c.execute("ALTER TABLE posts ADD COLUMN interest TEXT DEFAULT ''")
            c.execute("ALTER TABLE posts ADD COLUMN pub_time REAL")
        except Exception:
            pass
        for col, typ in [("domain", "TEXT DEFAULT ''"), ("score", "REAL DEFAULT 0"),
                         ("rf_ok", "INTEGER DEFAULT 1"), ("fails", "INTEGER DEFAULT 0"),
                         ("latency", "REAL DEFAULT 0"), ("checked_at", "REAL DEFAULT 0"),
                         ("entries", "INTEGER DEFAULT 0"), ("fresh", "REAL DEFAULT 0"),
                         ("cadence", "REAL DEFAULT 0"), ("rel", "REAL DEFAULT 0"),
                         ("lang", "INTEGER DEFAULT 1"), ("new_last", "INTEGER DEFAULT 0"),
                         ("stale_rounds", "INTEGER DEFAULT 0"), ("stale", "INTEGER DEFAULT 0")]:
            try:
                c.execute("ALTER TABLE interest_sources ADD COLUMN " + col + " " + typ)
            except Exception:
                pass
        done = c.execute("SELECT v FROM meta WHERE k='v4_wipe'").fetchone()
        if not done:
            c.execute("DELETE FROM posts WHERE interest IS NULL OR interest=''")
            c.execute("DELETE FROM user_interests")
            c.execute("DELETE FROM interest_sources")
            c.execute("DELETE FROM interests")
            c.execute("INSERT OR REPLACE INTO meta(k,v) VALUES ('v4_wipe','1')")
        done8 = c.execute("SELECT v FROM meta WHERE k='v8_pubtime'").fetchone()
        if not done8:
            c.execute("DELETE FROM posts WHERE pub_time IS NULL")
            c.execute("INSERT OR REPLACE INTO meta(k,v) VALUES ('v8_pubtime','1')")

def slugify(text):
    text = re.sub(r"[^a-zа-яё0-9]+", "-", (text or "").lower().strip())
    return text.strip("-")[:40] or "topic"

def probe(url):
    t0 = time.time()
    try:
        r = HTTP.get(url, timeout=PROBE)
        if r.status_code == 200:
            return True, round(time.time() - t0, 2), r.text
    except Exception:
        pass
    return False, round(time.time() - t0, 2), ""

def reprobe(url):
    try:
        r = HTTP.head(url, timeout=REPROBE, allow_redirects=True)
        return r.status_code < 400
    except Exception:
        return False

_ENDINGS = sorted(["иями", "остями", "остью", "ами", "ями", "ость", "ией", "ах", "ях",
                   "ов", "ев", "ой", "ей", "ий", "ый", "ая", "яя", "ое", "ее", "ые", "ие",
                   "ом", "ем", "ам", "у", "ю", "ь", "я", "и", "ы", "а", "е"], key=len, reverse=True)

def _stem(w):
    for e in _ENDINGS:
        if w.endswith(e) and len(w) - len(e) >= 5:
            return w[:-len(e)]
    return w

def evaluate(q, text, domain, bonus=0.0):
    f = feedparser.parse(text)
    entries = f.entries[:20]
    n = len(entries)
    if n < 5:
        return None
    toks = [t for t in re.split(r"[^a-zа-яё0-9]+", q.lower()) if len(t) > 2]
    stems = {s for s in (_stem(t) for t in toks) if len(s) >= 5}
    rel_hits = 0
    titles = []
    stamps = []
    cyr = 0
    total_chars = 0
    now = time.time()
    fresh = 0
    for e in entries:
        title = e.get("title", "")
        summ = e.get("summary", "")
        titles.append(title.strip().lower())
        if stems:
            t_words = re.split(r"[^a-zа-яё0-9]+", title.lower())
            b_words = re.split(r"[^a-zа-яё0-9]+", (title + " " + summ).lower())
            hit_title = any(w.startswith(st) for st in stems for w in t_words)
            hit_any = any(w.startswith(st) for st in stems for w in b_words)
            if hit_any:
                rel_hits += 1 if hit_title else 0.6
        pp = getattr(e, "published_parsed", None)
        if pp:
            ts = time.mktime(pp)
            stamps.append(ts)
            if (now - ts) < 7 * 86400:
                fresh += 1
        body = title + " " + summ
        total_chars += len(body)
        cyr += sum(1 for ch in body if "а" <= ch <= "я" or ch == "ё")
    rel = min(1.0, rel_hits / n) if stems else 0.5
    fresh_ratio = fresh / n
    if fresh_ratio == 0:
        return None
    stamps.sort(reverse=True)
    gaps = [(stamps[i] - stamps[i + 1]) / 3600 for i in range(len(stamps) - 1)]
    cadence_h = statistics.median(gaps) if gaps else 999
    cadence_score = max(0.0, min(1.0, 1 - cadence_h / 168))
    volume = min(1.0, n / 20)
    want_ru = any("а" <= ch <= "я" for ch in q.lower())
    lang_ratio = cyr / max(1, total_chars)
    lang_ok = 1 if (not want_ru or lang_ratio > 0.15) else 0
    uniq = len(set(titles)) / n
    trust = 0.05 if domain in WHITELIST_BONUS else 0
    score = (0.35 * rel + 0.25 * fresh_ratio + 0.15 * cadence_score +
             0.10 * volume + 0.10 * lang_ok + trust + bonus)
    score *= (0.5 + 0.5 * uniq)
    if score < 0.28:
        if not (fresh_ratio >= 0.3 and uniq >= 0.7):
            return None
        score = max(score, 0.2)
    return {"score": round(score, 3), "entries": n, "fresh": round(fresh_ratio, 2),
            "cadence": round(cadence_h, 1), "rel": round(rel, 2), "lang": lang_ok}

def discover(query, force=False):
    q = (query or "").strip()
    slug = slugify(q)
    now = time.time()
    with db() as c:
        row = c.execute("SELECT * FROM interests WHERE slug=?", (slug,)).fetchone()
        if row and not force and (now - row["refreshed_at"]) < CACHE_TTL:
            srcs = c.execute("SELECT url,name,kind,domain,score FROM interest_sources WHERE slug=? AND rf_ok=1 AND stale=0 ORDER BY score DESC", (slug,)).fetchall()
            if srcs:
                return {"slug": slug, "title": row["title"],
                        "top_sites": json.loads(row["top_sites"] or "[]"),
                        "sources": [dict(s) for s in srcs], "cached": True}

    top_sites, cands = [], []
    try:
        r = HTTP.get(f"https://html.duckduckgo.com/html/?q={quote_plus(q)}", timeout=(2.0, 3.0))
        soup = BeautifulSoup(r.text, "html.parser")
        links = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "uddg=" in href:
                href = unquote(href.split("uddg=")[1].split("&")[0])
            if not href.startswith("http"):
                continue
            dom = re.sub(r"^www\.", "", urlparse(href).netloc)
            if not dom or "duckduckgo" in dom:
                continue
            if dom not in top_sites:
                top_sites.append(dom)
            links.append((dom, href))
        for dom, href in links:
            if re.search(r"(rss|atom|feed|\.xml)", href, re.IGNORECASE):
                cands.append((dom, href))
        seen = set()
        for dom, page in links:
            if dom in seen:
                continue
            seen.add(dom)
            for path in FEED_PATHS:
                cands.append((dom, "https://" + dom + path))
            if len(seen) >= 4:
                break
    except Exception as e:
        logging.warning(f"DDG failed: {e}")

    q_cands = [("habr.com", f"https://habr.com/ru/rss/search/?q={quote_plus(q)}&fl=ru"),
               ("bing.com", f"https://www.bing.com/news/search?q={quote_plus(q)}&format=rss")]
    if q.isascii():
        q_cands.append(("arxiv.org", f"http://export.arxiv.org/api/query?search_query=all:{quote_plus(q)}&max_results=10"))
    cands = q_cands + cands

    rank_bonus = {}
    for i, d in enumerate(top_sites[:5]):
        rank_bonus[d] = max(0.0, 0.20 - 0.04 * i)

    accepted = {}
    def try_one(dom, url):
        ok, lat, text = probe(url)
        if not ok:
            return None
        m = evaluate(q, text, dom, rank_bonus.get(dom, 0.0))
        if not m:
            return None
        if not reprobe(url):
            return None
        m.update({"url": url, "domain": dom, "latency": lat})
        return m

    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = [ex.submit(try_one, d, u) for d, u in cands[:14]]
        try:
            for fut in as_completed(futs, timeout=DISCOVER_DEADLINE):
                m = fut.result()
                if m and m["url"] not in accepted:
                    accepted[m["url"]] = m
        except Exception:
            pass

    ranked = sorted(accepted.values(), key=lambda m: -m["score"])[:6]
    logging.info(f"discover '{q}': cands={len(cands)} accepted={len(ranked)}")
    
    if not ranked:
        fallback = [
            ("habr.com", f"https://habr.com/ru/rss/search/?q={quote_plus(q)}&fl=ru"),
            ("nplus1.ru", "https://nplus1.ru/rss"),
            ("elementy.ru", "https://elementy.ru/rss/news"),
            ("opennet.ru", "https://www.opennet.ru/opennews/opennews_all.rss"),
            ("cyberleninka.ru", "https://cyberleninka.ru/article/rss"),
        ]
        with ThreadPoolExecutor(max_workers=5) as ex_fb:
            futs_fb = [ex_fb.submit(probe, u) for _, u in fallback]
            for (dom, u), fut in zip(fallback, futs_fb):
                try:
                    ok, lat, text = fut.result(timeout=6)
                except Exception:
                    continue
                if not ok:
                    continue
                ent = feedparser.parse(text).entries
                if len(ent) < 3:
                    continue
                if not any((time.time() - time.mktime(e.published_parsed)) < 30*86400
                           for e in ent if getattr(e, "published_parsed", None)):
                    continue
                accepted[u] = {"url": u, "domain": dom, "score": 0.5,
                               "latency": lat, "entries": 3, "fresh": 0.5,
                               "cadence": 24, "rel": 0.5, "lang": 1}
        ranked = sorted(accepted.values(), key=lambda m: -m["score"])[:6]
        logging.info(f"discover '{q}' FALLBACK: accepted={len(ranked)}")

    for m in ranked:
        if m["domain"] not in top_sites:
            top_sites.append(m["domain"])
    top_sites = top_sites[:5]

    with db() as c:
        c.execute("INSERT OR REPLACE INTO interests(slug,title,top_sites,created_at,refreshed_at) VALUES (?,?,?,?,?)",
                  (slug, q, json.dumps(top_sites, ensure_ascii=False), now, now))
        if force:
            c.execute("DELETE FROM interest_sources WHERE slug=?", (slug,))
        for m in ranked:
            c.execute("""INSERT INTO interest_sources(slug,url,name,kind,domain,score,rf_ok,fails,latency,checked_at,entries,fresh,cadence,rel,lang,stale)
                         VALUES (?,?,?,?,?,?,1,0,?,?,?,?,?,?,?,0)
                         ON CONFLICT(url) DO UPDATE SET rf_ok=1, fails=0, stale=0, stale_rounds=0,
                         score=excluded.score, latency=excluded.latency, checked_at=excluded.checked_at,
                         entries=excluded.entries, fresh=excluded.fresh, cadence=excluded.cadence,
                         rel=excluded.rel, lang=excluded.lang""",
                      (slug, m["url"], f"{m['domain']}: {q}", "site", m["domain"], m["score"],
                       m["latency"], now, m["entries"], m["fresh"], m["cadence"], m["rel"], m["lang"]))
    return {"slug": slug, "title": q, "top_sites": top_sites,
            "sources": [{"url": m["url"], "name": f"{m['domain']}: {q}", "kind": "site",
                         "domain": m["domain"], "score": m["score"]} for m in ranked],
            "cached": False}

def rotate(slug):
    with db() as c:
        row = c.execute("SELECT title FROM interests WHERE slug=?", (slug,)).fetchone()
    if row:
        try:
            discover(row["title"], force=True)
        except Exception as e:
            logging.warning(f"rotate {slug} failed: {e}")

def report_source(url, ok):
    with db() as c:
        if ok:
            c.execute("UPDATE interest_sources SET fails=0, rf_ok=1, checked_at=? WHERE url=?", (time.time(), url))
        else:
            c.execute("UPDATE interest_sources SET fails=fails+1, checked_at=? WHERE url=?", (time.time(), url))
            c.execute("UPDATE interest_sources SET rf_ok=0 WHERE url=? AND fails>=3", (url,))

def report_novelty(url, new_count):
    with db() as c:
        row = c.execute("SELECT slug, stale_rounds FROM interest_sources WHERE url=?", (url,)).fetchone()
        if not row:
            return
        sr = 0 if new_count > 0 else row["stale_rounds"] + 1
        stale = 1 if sr >= 3 else 0
        c.execute("UPDATE interest_sources SET new_last=?, stale_rounds=?, stale=? WHERE url=?",
                  (new_count, sr, stale, url))
        slug = row["slug"]
        left = c.execute("SELECT COUNT(*) n FROM interest_sources WHERE slug=? AND rf_ok=1 AND stale=0", (slug,)).fetchone()["n"]
    if left == 0:
        threading.Thread(target=rotate, args=(slug,), daemon=True).start()

def interest_source_items():
    with db() as c:
        rows = c.execute("SELECT slug,url,name,score FROM interest_sources WHERE rf_ok=1 AND stale=0 ORDER BY score DESC").fetchall()
    per = {}
    out = []
    for r in rows:
        k = per.get(r["slug"], 0)
        if k >= 2:
            continue
        per[r["slug"]] = k + 1
        out.append({"type": "rss", "url": r["url"], "source": r["name"],
                    "category": "interest", "limit": 12, "interest": r["slug"]})
    return out

def current_slugs():
    st = get_state()["interests"]
    if not st:
        return []
    with db() as c:
        rows = c.execute("SELECT DISTINCT slug FROM interest_sources WHERE rf_ok=1 AND stale=0").fetchall()
    live = {r["slug"] for r in rows}
    return [i["slug"] for i in st if i["slug"] in live]

def search_courses(q, limit=6):
    out = []
    try:
        r = HTTP.get(f"https://stepik.org/api/courses?search={quote_plus(q)}&page=1", timeout=(2.0, 3.0))
        r.raise_for_status()
        for c in r.json().get("courses", []):
            price = c.get("price")
            if price not in (0, "0", None, ""):
                continue
            out.append({"id": c["id"], "title": c.get("title", "Курс"),
                        "url": f"https://stepik.org/course/{c['id']}",
                        "learners": c.get("learners_count", 0)})
            if len(out) >= limit:
                break
    except Exception as e:
        logging.warning(f"Stepik failed: {e}")
    return out

def _uid():
    return session.get("uid")

def _feed_max():
    return FEED_MAX_USER if _uid() else FEED_MAX_GUEST

def load_fragment():
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates", "interests.html"), encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        logging.error(f"interests fragment load failed: {e}")
        return ""

_refresher = None
def set_refresher(fn):
    global _refresher
    _refresher = fn

INTERESTS_FRAGMENT = load_fragment()

def get_state():
    uid = _uid()
    if uid:
        with db() as c:
            rows = c.execute("""SELECT i.slug,i.title,u.pos FROM user_interests u
                                JOIN interests i ON i.slug=u.slug WHERE u.user_id=? ORDER BY u.pos""", (uid,)).fetchall()
            items = [{"slug": r["slug"], "title": r["title"]} for r in rows]
            crs = c.execute("""SELECT c.course_id,c.title,c.url FROM user_courses uc
                               JOIN courses c ON c.course_id=uc.course_id WHERE uc.user_id=? ORDER BY uc.pos""", (uid,)).fetchall()
            courses = [{"id": r["course_id"], "title": r["title"], "url": r["url"]} for r in crs]
    else:
        feed = session.get("interests", [])
        with db() as c:
            items = []
            for s in feed:
                r = c.execute("SELECT title FROM interests WHERE slug=?", (s,)).fetchone()
                if r:
                    items.append({"slug": s, "title": r["title"]})
        courses = session.get("courses", [])
    with db() as c:
        lr = c.execute("SELECT DISTINCT slug FROM interest_sources WHERE rf_ok=1 AND stale=0").fetchall()
    live = {r["slug"] for r in lr}
    stale = bool(items) and not any(i["slug"] in live for i in items)
    return {"interests": items, "courses": courses, "is_user": bool(uid),
            "onboarded": bool(session.get("onboarded")), "stale": stale,
            "limits": {"feed_max": _feed_max(), "course_max": COURSE_MAX}}

def save_state(feed_slugs, courses):
    feed_slugs = list(dict.fromkeys(feed_slugs))[:_feed_max()]
    courses = [c for c in courses if c.get("url") and c.get("title")][:COURSE_MAX]
    uid = _uid()
    now = time.time()
    with db() as c:
        for cr in courses:
            c.execute("INSERT OR IGNORE INTO courses(course_id,title,url,source,created_at) VALUES (?,?,?,?,?)",
                      (cr.get("id", 0), cr["title"], cr["url"], "stepik", now))
        if uid:
            c.execute("DELETE FROM user_interests WHERE user_id=?", (uid,))
            for i, s in enumerate(feed_slugs):
                c.execute("INSERT OR IGNORE INTO user_interests(user_id,slug,pos) VALUES (?,?,?)", (uid, s, i))
            c.execute("DELETE FROM user_courses WHERE user_id=?", (uid,))
            for i, cr in enumerate(courses):
                c.execute("INSERT OR IGNORE INTO user_courses(user_id,course_id,pos) VALUES (?,?,?)", (uid, cr.get("id", 0), i))
        else:
            session["interests"] = feed_slugs
            session["courses"] = courses
    session["onboarded"] = 1
    
    # Создание комнат чатов для новых интересов
    try:
        from chat import create_room_for_interest
        for slug in feed_slugs:
            row = c.execute("SELECT title FROM interests WHERE slug=?", (slug,)).fetchone()
            if row:
                create_room_for_interest(slug, row["title"])
    except Exception as e:
        logging.warning(f"Chat room creation failed: {e}")
        
    return get_state()

def adopt_guest_interests(uid):
    feed = session.get("interests", [])
    courses = session.get("courses", [])
    now = time.time()
    with db() as c:
        for i, s in enumerate(feed[:FEED_MAX_USER]):
            c.execute("INSERT OR IGNORE INTO user_interests(user_id,slug,pos) VALUES (?,?,?)", (uid, s, i))
        for cr in courses[:COURSE_MAX]:
            c.execute("INSERT OR IGNORE INTO courses(course_id,title,url,source,created_at) VALUES (?,?,?,?,?)",
                      (cr.get("id", 0), cr["title"], cr["url"], "stepik", now))
            c.execute("INSERT OR IGNORE INTO user_courses(user_id,course_id,pos) VALUES (?,?,?)", (uid, cr.get("id", 0), 0))
    session.pop("interests", None)
    session.pop("courses", None)
    session["onboarded"] = 1

@bp.get("/api/interests")
def api_get():
    return jsonify(get_state())

@bp.post("/api/interests/add")
def api_add():
    d = request.get_json(silent=True) or {}
    q = d.get("q", "")
    if not q or len(q) > 60 or not topic_is_clean(q):
        return jsonify({"error": "banned_topic"}), 400
    r = discover(q)
    if not r["sources"]:
        return jsonify({"error": "no_sources"}), 404
    return jsonify({"slug": r["slug"], "title": r["title"], "top_sites": r["top_sites"]})

@bp.get("/api/interests/courses")
def api_courses():
    q = request.args.get("q", "").strip()
    if not q or len(q) > 60 or not topic_is_clean(q):
        return jsonify({"error": "banned_topic"}), 400
    return jsonify({"courses": search_courses(q)})

@bp.post("/api/interests/save")
def api_save():
    d = request.get_json(silent=True) or {}
    st = save_state(d.get("interests", []), d.get("courses", []))
    if _refresher:
        try:
            _refresher()
        except Exception as e:
            logging.warning(f"auto-refresh after save failed: {e}")
    return jsonify(st)

@bp.post("/api/interests/skip")
def api_skip():
    session["onboarded"] = 1
    return jsonify({"ok": True})
