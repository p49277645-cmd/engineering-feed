# interests.py v3 — белые списки, двойной RF-probe, скоринг, бесплатные курсы, память онбординга
import os, re, json, time, sqlite3, logging
from contextlib import contextmanager
from urllib.parse import quote_plus, urlparse, unquote
import feedparser, requests
from bs4 import BeautifulSoup
from flask import Blueprint, request, jsonify, session

bp = Blueprint("interests", __name__)
DB_PATH = os.environ.get("DB_PATH", "engfeed.db")
FEED_MAX_GUEST, FEED_MAX_USER, COURSE_MAX = 10, 13, 3
CACHE_TTL = 7 * 86400
PROBE = (1.2, 1.8)

HTTP = requests.Session()
HTTP.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8",
})

# Домены, заведомо доступные из РФ (бонус к скорингу)
WHITELIST_RU = {"habr.com", "stepik.org", "nplus1.ru", "elementy.ru", "cyberleninka.ru",
                "opennet.ru", "linux.org.ru", "arxiv.org", "4pda.to", "ixbt.com",
                "openedu.ru", "universarium.org", "lektorium.tv"}

# ---------- усиленный фильтр запрещёнки ----------
BANNED_TOPICS = {
    "порно", "porno", "porn", "xxx", "sex", "секс", "хентай", "hentai", "эротика",
    "erotica", "nude", "onlyfans", "camgirl", "порнуха", "ебля", "трах",
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
        """)
        for col, typ in [("interest", "TEXT DEFAULT ''")]:
            try:
                c.execute("ALTER TABLE posts ADD COLUMN " + col + " " + typ)
            except Exception:
                pass
        for col, typ in [("domain", "TEXT DEFAULT ''"), ("score", "REAL DEFAULT 0"),
                         ("rf_ok", "INTEGER DEFAULT 1"), ("fails", "INTEGER DEFAULT 0"),
                         ("latency", "REAL DEFAULT 0"), ("checked_at", "REAL DEFAULT 0")]:
            try:
                c.execute("ALTER TABLE interest_sources ADD COLUMN " + col + " " + typ)
            except Exception:
                pass

def slugify(text):
    text = re.sub(r"[^a-zа-яё0-9]+", "-", (text or "").lower().strip())
    return text.strip("-")[:40] or "topic"

# ---------- стадии проверки ----------
def probe(url):
    """Стадия A/C: реальный запрос С РОССИЙСКОГО СЕРВЕРА = точный тест доступности в РФ."""
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
        r = HTTP.head(url, timeout=(1.0, 1.5), allow_redirects=True)
        return r.status_code < 400
    except Exception:
        return False

def score_feed(q, text, domain):
    """Быстрый линейный скоринг: overlap + свежесть + объём + бонус белого списка."""
    f = feedparser.parse(text)
    entries = f.entries[:12]
    if len(entries) < 3:
        return 0.0
    toks = [t for t in re.split(r"[^a-zа-яё0-9]+", q.lower()) if len(t) > 2]
    blob = " ".join((e.get("title", "") + " " + e.get("summary", "")) for e in entries).lower()
    overlap = sum(1 for t in toks if t in blob) / max(1, len(toks))
    now = time.time()
    fresh = 0
    for e in entries:
        pp = getattr(e, "published_parsed", None)
        if pp and (now - time.mktime(pp)) < 30 * 86400:
            fresh += 1
    fresh_ratio = fresh / len(entries)
    wl = 1.0 if domain in WHITELIST_RU else 0.0
    return round(0.5 * overlap + 0.25 * fresh_ratio + 0.15 * min(1.0, len(entries) / 8) + 0.10 * wl, 3)

def discover(query):
    q = (query or "").strip()
    slug = slugify(q)
    now = time.time()
    with db() as c:
        row = c.execute("SELECT * FROM interests WHERE slug=?", (slug,)).fetchone()
        if row and (now - row["refreshed_at"]) < CACHE_TTL:
            srcs = c.execute("SELECT url,name,kind,domain,score FROM interest_sources WHERE slug=? AND rf_ok=1", (slug,)).fetchall()
            if srcs:
                return {"slug": slug, "title": row["title"],
                        "top_sites": json.loads(row["top_sites"] or "[]"),
                        "sources": [dict(s) for s in srcs], "cached": True}

    top_sites, accepted = [], []
    # кандидаты из белого списка (заведомо живые в РФ шаблоны)
    cands = [("habr.com", f"https://habr.com/ru/rss/search/?q={quote_plus(q)}&fl=ru", "habr"),
             ("arxiv.org", f"http://export.arxiv.org/api/query?search_query=all:{quote_plus(q)}&max_results=8", "arxiv")]
    # кандидаты из поисковой выдачи DuckDuckGo (работает в РФ, чистый HTML)
    try:
        r = HTTP.get(f"https://html.duckduckgo.com/html/?q={quote_plus(q)}", timeout=PROBE)
        soup = BeautifulSoup(r.text, "html.parser")
        pages = []
        for a in soup.find_all("a", class_="result__a", href=True):
            href = a["href"]
            if "uddg=" in href:
                href = unquote(href.split("uddg=")[1].split("&")[0])
            if href.startswith("http"):
                dom = re.sub(r"^www\.", "", urlparse(href).netloc)
                if dom and dom not in top_sites:
                    top_sites.append(dom)
                pages.append((dom, href))
        for dom, page in pages[:2]:
            ok, lat, html_text = probe(page)          # стадия A для страницы
            if not ok:
                continue
            ps = BeautifulSoup(html_text, "html.parser")
            link = ps.find("link", rel="alternate", type=re.compile("rss|atom"))
            if link and link.get("href"):
                feed_url = link["href"]
                if feed_url.startswith("/"):
                    feed_url = f"https://{dom}{feed_url}"
                cands.append((dom, feed_url, "site"))
    except Exception as e:
        logging.warning(f"DDG failed: {e}")

    for dom, url, kind in cands[:5]:
        ok, lat, text = probe(url)                    # стадия A: RF-проверка
        if not ok:
            continue
        sc = score_feed(q, text, dom)                 # стадия B: анализ по интересу
        if sc < 0.15:
            continue
        if not reprobe(url):                          # стадия C: повторная RF-проверка
            continue
        accepted.append({"url": url, "name": f"{dom}: {q}", "kind": kind,
                         "domain": dom, "score": sc, "latency": lat})
        if dom not in top_sites:
            top_sites.append(dom)

    accepted.sort(key=lambda s: -s["score"])
    accepted = accepted[:3]
    top_sites = top_sites[:5]
    with db() as c:
        c.execute("INSERT OR REPLACE INTO interests(slug,title,top_sites,created_at,refreshed_at) VALUES (?,?,?,?,?)",
                  (slug, q, json.dumps(top_sites, ensure_ascii=False), now, now))
        for s in accepted:
            c.execute("""INSERT INTO interest_sources(slug,url,name,kind,domain,score,rf_ok,fails,latency,checked_at)
                         VALUES (?,?,?,?,?,?,1,0,?,?)
                         ON CONFLICT(url) DO UPDATE SET rf_ok=1, fails=0, score=excluded.score,
                         latency=excluded.latency, checked_at=excluded.checked_at""",
                      (slug, s["url"], s["name"], s["kind"], s["domain"], s["score"], s["latency"], now))
    return {"slug": slug, "title": q, "top_sites": top_sites, "sources": accepted, "cached": False}

def report_source(url, ok):
    """Учёт здоровья: 3 падения подряд → источник отключается до повторного поиска."""
    with db() as c:
        if ok:
            c.execute("UPDATE interest_sources SET fails=0, rf_ok=1, checked_at=? WHERE url=?", (time.time(), url))
        else:
            c.execute("UPDATE interest_sources SET fails=fails+1, checked_at=? WHERE url=?", (time.time(), url))
            c.execute("UPDATE interest_sources SET rf_ok=0 WHERE url=? AND fails>=3", (url,))

def interest_source_items():
    with db() as c:
        rows = c.execute("SELECT slug,url,name FROM interest_sources WHERE rf_ok=1 AND fails<3").fetchall()
    return [{"type": "rss", "url": r["url"], "source": r["name"],
             "category": "interest", "limit": 8, "interest": r["slug"]} for r in rows]

def current_slugs():
    return [i["slug"] for i in get_state()["interests"]]

# ---------- бесплатные курсы (Stepik API, работает в РФ) ----------
def search_courses(q, limit=6):
    out = []
    try:
        r = HTTP.get(f"https://stepik.org/api/courses?search={quote_plus(q)}&page=1", timeout=PROBE)
        r.raise_for_status()
        for c in r.json().get("courses", []):
            if c.get("is_paid"):
                continue
            if c.get("price") not in (None, 0, "0", ""):
                continue
            out.append({"id": c["id"], "title": c.get("title", "Курс"),
                        "url": f"https://stepik.org/course/{c['id']}",
                        "learners": c.get("learners_count", 0)})
            if len(out) >= limit:
                break
    except Exception as e:
        logging.warning(f"Stepik failed: {e}")
    return out

# ---------- состояние ----------
def _uid():
    return session.get("uid")

def _feed_max():
    return FEED_MAX_USER if _uid() else FEED_MAX_GUEST

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
                items.append({"slug": s, "title": r["title"] if r else s})
        courses = session.get("courses", [])
    return {"interests": items, "courses": courses, "is_user": bool(uid),
            "onboarded": bool(session.get("onboarded")),
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

# ---------- API ----------
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
    return jsonify(save_state(d.get("interests", []), d.get("courses", [])))

@bp.post("/api/interests/skip")
def api_skip():
    session["onboarded"] = 1
    return jsonify({"ok": True})

# ---------- UI ----------
INTERESTS_FRAGMENT = """
<style>
.int-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.85);backdrop-filter:blur(8px);z-index:210;align-items:center;justify-content:center;}
.int-overlay.open{display:flex;}
.int-box{width:100%;max-width:600px;max-height:88vh;overflow-y:auto;background:var(--panel);border:1px solid var(--border);border-top:3px solid var(--accent-emerald);padding:28px;position:relative;}
.int-box h2{font-family:'JetBrains Mono',monospace;font-size:20px;color:var(--accent-emerald);margin-bottom:6px;}
.int-box h2::before{content:'// ';color:var(--accent-coral);}
.int-sub{font-size:12px;color:var(--t3);margin-bottom:16px;font-family:'JetBrains Mono',monospace;}
.int-tabs{display:flex;gap:8px;margin-bottom:16px;}
.int-tab{flex:1;height:36px;border:1px solid var(--border);background:var(--card);color:var(--t2);font-family:'JetBrains Mono',monospace;font-size:12px;font-weight:700;cursor:pointer;}
.int-tab.active{background:var(--accent-emerald);color:var(--bg);border-color:var(--accent-emerald);}
.int-search{display:flex;gap:8px;margin-bottom:12px;}
.int-search input{flex:1;height:42px;background:var(--card);border:1px solid var(--border);color:var(--t1);padding:0 12px;font-family:'JetBrains Mono',monospace;outline:none;}
.int-search button{height:42px;padding:0 16px;background:var(--accent-emerald);border:none;color:var(--bg);font-weight:700;font-family:'JetBrains Mono',monospace;cursor:pointer;}
.int-tops{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px;}
.int-top{font-size:11px;color:var(--t3);border:1px dashed var(--border);padding:4px 8px;font-family:'JetBrains Mono',monospace;}
.int-chips{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px;}
.int-chip{display:flex;align-items:center;gap:8px;padding:8px 12px;background:var(--card);border:1px solid var(--accent-emerald);color:var(--t1);font-family:'JetBrains Mono',monospace;font-size:12px;}
.int-chip .rm{color:var(--accent-coral);font-weight:700;cursor:pointer;}
.int-count{font-family:'JetBrains Mono',monospace;font-size:12px;color:var(--t2);margin-bottom:12px;}
.int-count b{color:var(--accent-emerald);}
.int-save{width:100%;height:44px;background:var(--accent-sand);border:none;color:var(--bg);font-weight:800;font-family:'JetBrains Mono',monospace;cursor:pointer;}
.int-ghost{width:100%;height:36px;margin-top:8px;background:var(--card2);border:none;color:var(--t3);font-family:'JetBrains Mono',monospace;font-size:12px;cursor:pointer;}
.int-close{position:absolute;top:12px;right:12px;width:30px;height:30px;border:1px solid var(--border);background:var(--card);color:var(--t2);cursor:pointer;}
.int-err{color:var(--accent-coral);font-size:12px;font-family:'JetBrains Mono',monospace;margin-bottom:10px;display:none;}
.crs-row{display:flex;align-items:center;gap:10px;padding:10px;border:1px solid var(--border);background:var(--card);margin-bottom:8px;}
.crs-row .t{flex:1;font-size:12px;color:var(--t1);font-family:'JetBrains Mono',monospace;}
.crs-row .n{font-size:10px;color:var(--t3);}
.crs-row button{height:30px;padding:0 12px;background:var(--accent-emerald);border:none;color:var(--bg);font-weight:700;font-family:'JetBrains Mono',monospace;font-size:11px;cursor:pointer;}
.course-card{display:flex;align-items:center;gap:10px;width:100%;padding:12px;margin-bottom:8px;border:1px solid var(--border);border-left:3px solid var(--accent-sand);background:var(--card);color:var(--t1);font-size:12px;font-family:'JetBrains Mono',monospace;text-decoration:none;transition:.2s;}
.course-card:hover{border-color:var(--accent-sand);background:var(--card2);transform:translateX(4px);}
.course-card .star{color:var(--accent-sand);}
</style>
<div class="int-overlay" id="intOverlay">
  <div class="int-box">
    <button class="int-close" id="intClose">✕</button>
    <h2>Настрой свою ленту</h2>
    <div class="int-sub">до <span id="intMax">10</span> интересов · до 3 бесплатных курсов ★</div>
    <div class="int-tabs">
      <button class="int-tab active" id="tabInt">Интересы</button>
      <button class="int-tab" id="tabCrs">Курсы ★</button>
    </div>
    <div class="int-err" id="intErr"></div>
    <div id="panInt">
      <div class="int-search"><input id="intQ" placeholder="квантовая физика / rust / нейросети"><button id="intFind">Найти</button></div>
      <div class="int-tops" id="intTops"></div>
      <div class="int-chips" id="intChips"></div>
    </div>
    <div id="panCrs" style="display:none">
      <div class="int-search"><input id="crsQ" placeholder="python / физика / электроника"><button id="crsFind">Найти бесплатные</button></div>
      <div id="crsList"></div>
      <div class="int-chips" id="crsChips"></div>
    </div>
    <div class="int-count" id="intCount"></div>
    <button class="int-save" id="intSave">Сохранить и собрать ленту</button>
    <button class="int-ghost" id="intClear">Очистить всё</button>
    <button class="int-ghost" id="intSkip">Пропустить (выберу позже)</button>
  </div>
</div>
<script>
(function(){
  var state={interests:[],courses:[],limits:{feed_max:10,course_max:3},is_user:false,onboarded:false};
  var ov=document.getElementById('intOverlay');
  function err(t){var e=document.getElementById('intErr');e.innerText=t||'';e.style.display=t?'block':'none';}
  function load(cb){fetch('/api/interests').then(function(r){return r.json();}).then(function(d){state=d;if(cb)cb();});}
  function counts(){
    document.getElementById('intCount').innerHTML='интересов: <b>'+state.interests.length+'/'+state.limits.feed_max+'</b> · курсов: <b>'+state.courses.length+'/'+state.limits.course_max+'</b>';
    document.getElementById('intMax').innerText=state.limits.feed_max;
  }
  function renderInt(){
    var box=document.getElementById('intChips');box.innerHTML='';
    state.interests.forEach(function(it){
      var c=document.createElement('span');c.className='int-chip';
      c.innerHTML=it.title+' <span class="rm">✕</span>';
      c.querySelector('.rm').addEventListener('click',function(){state.interests=state.interests.filter(function(x){return x.slug!==it.slug;});renderInt();counts();});
      box.appendChild(c);
    });
  }
  function renderCrs(){
    var box=document.getElementById('crsChips');box.innerHTML='';
    state.courses.forEach(function(cr){
      var c=document.createElement('span');c.className='int-chip';
      c.innerHTML='★ '+cr.title+' <span class="rm">✕</span>';
      c.querySelector('.rm').addEventListener('click',function(){state.courses=state.courses.filter(function(x){return x.url!==cr.url;});renderCrs();counts();});
      box.appendChild(c);
    });
  }
  function renderSidebar(){
    var COLORS=['#10b981','#ef4444','#a855f7','#f59e0b','#14b8a6','#3b82f6','#f97316'];
    function hash(s){var x=0;for(var i=0;i<s.length;i++){x=(x*31+s.charCodeAt(i))>>>0;}return x;}
    var ib=document.getElementById('interestsBox');
    if(ib){ib.innerHTML='';
      state.interests.forEach(function(i){
        var b=document.createElement('button');b.type='button';b.className='cat-card';
        b.style.setProperty('--c',COLORS[hash(i.slug)%COLORS.length]);
        b.innerHTML='<span class="cat-letter">'+i.title.charAt(0).toUpperCase()+'</span><span class="cat-name">'+i.title+'</span>';
        b.addEventListener('click',function(){location.href='/?int='+encodeURIComponent(i.slug);});
        ib.appendChild(b);
      });
    }
    var cb=document.getElementById('coursesBox');
    if(cb){cb.innerHTML='';
      state.courses.forEach(function(cr){
        var a=document.createElement('a');a.className='course-card';a.href=cr.url;a.target='_blank';a.rel='noopener';
        a.innerHTML='<span class="star">★</span><span>'+cr.title+'</span>';
        cb.appendChild(a);
      });
    }
  }
  window.openInterests=function(){load(function(){err('');renderInt();renderCrs();renderSidebar();counts();ov.classList.add('open');});};
  document.getElementById('intClose').addEventListener('click',function(){ov.classList.remove('open');});
  ov.addEventListener('click',function(e){if(e.target===ov)ov.classList.remove('open');});
  document.getElementById('tabInt').addEventListener('click',function(){
    this.classList.add('active');document.getElementById('tabCrs').classList.remove('active');
    document.getElementById('panInt').style.display='block';document.getElementById('panCrs').style.display='none';});
  document.getElementById('tabCrs').addEventListener('click',function(){
    this.classList.add('active');document.getElementById('tabInt').classList.remove('active');
    document.getElementById('panCrs').style.display='block';document.getElementById('panInt').style.display='none';});
  document.getElementById('intFind').addEventListener('click',function(){
    var q=document.getElementById('intQ').value.trim();if(!q)return;
    err('');document.getElementById('intTops').innerText='проверяю доступность из РФ и релевантность…';
    fetch('/api/interests/add',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({q:q})})
    .then(function(r){return r.json().then(function(d){return {s:r.status,d:d};});})
    .then(function(x){
      document.getElementById('intTops').innerHTML='';
      if(x.s!==200){err(x.d.error==='banned_topic'?'Такая тема запрещена':'Не нашёл подтверждённых источников');return;}
      if(state.interests.length>=state.limits.feed_max){err('Лимит интересов');return;}
      if(state.interests.some(function(i){return i.slug===x.d.slug;})){err('Уже добавлено');return;}
      state.interests.push({slug:x.d.slug,title:x.d.title});
      (x.d.top_sites||[]).forEach(function(s){var sp=document.createElement('span');sp.className='int-top';sp.innerText=s;document.getElementById('intTops').appendChild(sp);});
      renderInt();counts();
    }).catch(function(){document.getElementById('intTops').innerHTML='';err('Ошибка сети');});
  });
  document.getElementById('crsFind').addEventListener('click',function(){
    var q=document.getElementById('crsQ').value.trim();if(!q)return;
    err('');document.getElementById('crsList').innerHTML='ищу бесплатные курсы…';
    fetch('/api/interests/courses?q='+encodeURIComponent(q))
    .then(function(r){return r.json();})
    .then(function(d){
      var box=document.getElementById('crsList');box.innerHTML='';
      if(!d.courses||!d.courses.length){box.innerHTML='';err('Бесплатных курсов не найдено');return;}
      d.courses.forEach(function(cr){
        var row=document.createElement('div');row.className='crs-row';
        row.innerHTML='<span class="t">'+cr.title+'</span><span class="n">'+(cr.learners||0)+' уч.</span>';
        var b=document.createElement('button');b.innerText='Добавить';
        b.addEventListener('click',function(){
          if(state.courses.length>=state.limits.course_max){err('Максимум 3 курса');return;}
          if(state.courses.some(function(x){return x.url===cr.url;})){err('Уже добавлен');return;}
          state.courses.push(cr);renderCrs();counts();
        });
        row.appendChild(b);box.appendChild(row);
      });
    }).catch(function(){document.getElementById('crsList').innerHTML='';err('Ошибка сети');});
  });
  document.getElementById('intClear').addEventListener('click',function(){state.interests=[];state.courses=[];renderInt();renderCrs();counts();err('');});
  document.getElementById('intSkip').addEventListener('click',function(){
    fetch('/api/interests/skip',{method:'POST'}).then(function(){localStorage.setItem('ef_onboarded','1');ov.classList.remove('open');});
  });
  document.getElementById('intSave').addEventListener('click',function(){
    fetch('/api/interests/save',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({interests:state.interests.map(function(i){return i.slug;}),courses:state.courses})})
    .then(function(r){return r.json();})
    .then(function(d){state=d;localStorage.setItem('ef_onboarded','1');ov.classList.remove('open');renderSidebar();location.reload();});
  });
  load(function(){
    renderSidebar();
    if(!state.is_user&&!state.onboarded&&!localStorage.getItem('ef_onboarded')){
      setTimeout(function(){renderInt();renderCrs();counts();ov.classList.add('open');},1200);
    }
  });
})();
</script>
"""
