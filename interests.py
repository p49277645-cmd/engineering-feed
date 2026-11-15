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
def current_slugs():
    return [i["slug"] for i in get_state()["interests"]]

def interest_source_items():
    with db() as c:
        rows = c.execute("SELECT slug,url,name FROM interest_sources").fetchall()
    return [{"type": "rss", "url": r["url"], "source": r["name"],
             "category": "interest", "limit": 8, "interest": r["slug"]} for r in rows]

INTERESTS_FRAGMENT = """
<style>
.int-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.85);backdrop-filter:blur(8px);z-index:210;align-items:center;justify-content:center;}
.int-overlay.open{display:flex;}
.int-box{width:100%;max-width:560px;max-height:86vh;overflow-y:auto;background:var(--panel);border:1px solid var(--border);border-top:3px solid var(--accent-emerald);padding:28px;position:relative;}
.int-box h2{font-family:'JetBrains Mono',monospace;font-size:20px;color:var(--accent-emerald);margin-bottom:6px;}
.int-box h2::before{content:'// ';color:var(--accent-coral);}
.int-sub{font-size:12px;color:var(--t3);margin-bottom:18px;font-family:'JetBrains Mono',monospace;}
.int-search{display:flex;gap:8px;margin-bottom:14px;}
.int-search input{flex:1;height:42px;background:var(--card);border:1px solid var(--border);color:var(--t1);padding:0 12px;font-family:'JetBrains Mono',monospace;outline:none;}
.int-search button{height:42px;padding:0 18px;background:var(--accent-emerald);border:none;color:var(--bg);font-weight:700;font-family:'JetBrains Mono',monospace;cursor:pointer;}
.int-tops{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:14px;}
.int-top{font-size:11px;color:var(--t3);border:1px dashed var(--border);padding:4px 8px;font-family:'JetBrains Mono',monospace;}
.int-chips{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:18px;}
.int-chip{display:flex;align-items:center;gap:8px;padding:8px 12px;background:var(--card);border:1px solid var(--accent-emerald);color:var(--t1);font-family:'JetBrains Mono',monospace;font-size:12px;cursor:pointer;}
.int-chip .pin{width:20px;height:20px;border:1px solid var(--border);display:grid;place-items:center;font-size:10px;color:var(--t3);}
.int-chip.pinned .pin{background:var(--accent-coral);border-color:var(--accent-coral);color:var(--bg);}
.int-chip .rm{color:var(--accent-coral);font-weight:700;}
.int-count{font-family:'JetBrains Mono',monospace;font-size:12px;color:var(--t2);margin-bottom:14px;}
.int-count b{color:var(--accent-emerald);}
.int-save{width:100%;height:44px;background:var(--accent-sand);border:none;color:var(--bg);font-weight:800;font-family:'JetBrains Mono',monospace;cursor:pointer;}
.int-close{position:absolute;top:12px;right:12px;width:30px;height:30px;border:1px solid var(--border);background:var(--card);color:var(--t2);cursor:pointer;}
.int-err{color:var(--accent-coral);font-size:12px;font-family:'JetBrains Mono',monospace;margin-bottom:10px;display:none;}
.side-pin{display:flex;align-items:center;gap:10px;width:100%;padding:10px 12px;margin-bottom:6px;border:1px solid var(--border);background:var(--card);color:var(--t2);font-size:12px;font-family:'JetBrains Mono',monospace;cursor:pointer;text-align:left;}
.side-pin:hover{border-color:var(--accent-emerald);color:var(--t1);}
.side-pin .p-dot{width:8px;height:8px;border-radius:50%;background:var(--accent-emerald);flex-shrink:0;}
</style>
<div class="int-overlay" id="intOverlay">
  <div class="int-box">
    <button class="int-close" id="intClose">✕</button>
    <h2>Твои интересы</h2>
    <div class="int-sub">до <span id="intMax">10</span> интересов в ленту · до 3 закрепи слева (клик по ★)</div>
    <div class="int-search">
      <input id="intQ" placeholder="например: квантовая физика / rust / нейросети">
      <button id="intFind">Найти источники</button>
    </div>
    <div class="int-err" id="intErr"></div>
    <div class="int-tops" id="intTops"></div>
    <div class="int-chips" id="intChips"></div>
    <div class="int-count" id="intCount"></div>
    <button class="int-save" id="intSave">Сохранить интересы</button>
  </div>
</div>
<script>
(function(){
  var state={interests:[],limits:{feed_max:10,pin_max:3},is_user:false};
  var ov=document.getElementById('intOverlay');
  function err(t){var e=document.getElementById('intErr');e.innerText=t||'';e.style.display=t?'block':'none';}
  function load(cb){fetch('/api/interests').then(function(r){return r.json();}).then(function(d){state=d;if(cb)cb();});}
  function renderChips(){
    var box=document.getElementById('intChips');box.innerHTML='';
    state.interests.forEach(function(it){
      var c=document.createElement('button');
      c.type='button';
      c.className='int-chip'+(it.pinned?' pinned':'');
      c.innerHTML='<span class="pin">★</span> '+it.title+' <span class="rm">✕</span>';
      c.querySelector('.pin').addEventListener('click',function(ev){ev.stopPropagation();it.pinned=!it.pinned;renderChips();});
      c.querySelector('.rm').addEventListener('click',function(ev){ev.stopPropagation();state.interests=state.interests.filter(function(x){return x.slug!==it.slug;});renderChips();});
      box.appendChild(c);
    });
    var pins=state.interests.filter(function(i){return i.pinned;}).length;
    document.getElementById('intCount').innerHTML='в ленте: <b>'+state.interests.length+'/'+state.limits.feed_max+'</b> · слева: <b>'+pins+'/'+state.limits.pin_max+'</b>';
    document.getElementById('intMax').innerText=state.limits.feed_max;
  }
  function renderPins(){
    var host=document.getElementById('pinsBox');if(!host)return;host.innerHTML='';
    state.interests.filter(function(i){return i.pinned;}).forEach(function(i){
      var b=document.createElement('button');b.type='button';b.className='side-pin';
      b.innerHTML='<span class="p-dot"></span>'+i.title;
      b.addEventListener('click',function(){location.href='/?int='+i.slug;});
      host.appendChild(b);
    });
  }
  window.openInterests=function(){load(function(){err('');renderChips();renderPins();ov.classList.add('open');});};
  document.getElementById('intClose').addEventListener('click',function(){ov.classList.remove('open');});
  ov.addEventListener('click',function(e){if(e.target===ov)ov.classList.remove('open');});
  document.getElementById('intFind').addEventListener('click',function(){
    var q=document.getElementById('intQ').value.trim();if(!q)return;
    err('');document.getElementById('intTops').innerText='ищу топовые сайты…';
    fetch('/api/interests/add',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({q:q})})
    .then(function(r){return r.json().then(function(d){return {s:r.status,d:d};});})
    .then(function(x){
      document.getElementById('intTops').innerHTML='';
      if(x.s!==200){err('Не нашёл живых источников по запросу');return;}
      if(state.interests.length>=state.limits.feed_max){err('Лимит: '+state.limits.feed_max+' интересов в ленте');return;}
      if(state.interests.some(function(i){return i.slug===x.d.slug;})){err('Уже добавлено');return;}
      state.interests.push({slug:x.d.slug,title:x.d.title,pinned:false});
      var t=document.getElementById('intTops');
      (x.d.top_sites||[]).forEach(function(s){var sp=document.createElement('span');sp.className='int-top';sp.innerText=s;t.appendChild(sp);});
      renderChips();
    })
    .catch(function(){document.getElementById('intTops').innerHTML='';err('Ошибка сети');});
  });
  document.getElementById('intSave').addEventListener('click',function(){
    var pins=state.interests.filter(function(i){return i.pinned;});
    if(pins.length>state.limits.pin_max){err('Закрепить можно максимум 3');return;}
    fetch('/api/interests/save',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({interests:state.interests.map(function(i){return i.slug;}),
        pins:pins.map(function(i){return i.slug;})})})
    .then(function(r){return r.json();})
    .then(function(d){state=d;ov.classList.remove('open');renderPins();location.reload();});
  });
  load(function(){
    renderPins();
    if(!state.is_user&&state.interests.length===0&&!localStorage.getItem('intSeen')){
      localStorage.setItem('intSeen','1');
      setTimeout(function(){renderChips();ov.classList.add('open');},1200);
    }
  });
})();
</script>
"""

@bp.post("/api/interests/save")
def api_save():
    d = request.get_json(silent=True) or {}
    return jsonify(save_state(d.get("interests", []), d.get("pins", [])))
