import os
import re
import time
import datetime
import html
import sqlite3
import logging
import sys
import hashlib
import threading
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

import feedparser
import requests
from bs4 import BeautifulSoup
from flask import Flask, render_template_string, jsonify, request

logger = logging.getLogger("engfeed")
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
logger.addHandler(handler)
logger.setLevel(logging.INFO)

DB_PATH = os.environ.get("DB_PATH", "engfeed.db")
PORT = int(os.environ.get("PORT", 5000))

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
})

# ==========================================
# ИСТОЧНИКИ (ВСЕ СОХРАНЕНЫ)
# ==========================================
SOURCES = [
    {"type": "scrape", "url": "https://mathus.ru/phys/", "source": "Mathus: Физика 9", "category": "physics", "limit": 12},
    {"type": "scrape", "url": "https://olimpiada.ru/activity/394", "source": "Физтех: Физика", "category": "olympiad", "limit": 10},
    {"type": "scrape", "url": "https://olimpiada.ru/activity/5878", "source": "Росатом", "category": "olympiad", "limit": 10},
    {"type": "scrape", "url": "https://olimpiada.ru/activity/7288", "source": "Росатом Юниор", "category": "olympiad", "limit": 8},
    {"type": "scrape", "url": "https://olimpiada.ru/activity/395", "source": "Физтех: Математика", "category": "olympiad", "limit": 8},
    {"type": "scrape", "url": "https://olimpiada.ru/archive/394", "source": "Физтех: Архив", "category": "olympiad_archive", "limit": 10},
    {"type": "scrape", "url": "https://olimpiada.ru/archive/5878", "source": "Росатом: Архив", "category": "olympiad_archive", "limit": 10},
    {"type": "rss", "url": "https://www.quantamagazine.org/feed/", "source": "Quanta: Физика", "category": "physics_news", "limit": 8},
    {"type": "rss", "url": "https://physicsworld.com/feed/", "source": "Physics World", "category": "physics_news", "limit": 6},
    {"type": "rss", "url": "http://export.arxiv.org/rss/physics", "source": "arXiv: Физика", "category": "physics_news", "limit": 8},
    {"type": "scrape", "url": "https://mathus.ru/math/", "source": "Mathus: Математика", "category": "math", "limit": 10},
    {"type": "rss", "url": "https://habr.com/ru/rss/hub/python/daily/?fl=ru", "source": "Хабр: Python", "category": "code", "limit": 8},
    {"type": "rss", "url": "https://habr.com/ru/rss/hub/rust/daily/?fl=ru", "source": "Хабр: Rust", "category": "code", "limit": 8},
    {"type": "rss", "url": "https://hnrss.org/frontpage", "source": "Hacker News", "category": "code_news", "limit": 10},
    {"type": "rss", "url": "https://dev.to/feed", "source": "Dev.to", "category": "code_news", "limit": 8},
    {"type": "rss", "url": "https://blog.arduino.cc/feed/", "source": "Arduino Blog", "category": "robotics", "limit": 8},
    {"type": "rss", "url": "https://hackaday.com/category/arduino/feed/", "source": "Hackaday: Arduino", "category": "robotics", "limit": 6},
    {"type": "rss", "url": "https://hackaday.com/category/esp8266/feed/", "source": "Hackaday: ESP", "category": "robotics", "limit": 6},
    {"type": "rss", "url": "https://robohub.org/feed/", "source": "Robohub", "category": "robotics", "limit": 6},
    {"type": "rss", "url": "https://habr.com/ru/rss/hub/robotics/daily/?fl=ru", "source": "Хабр: Робототехника", "category": "robotics", "limit": 8},
    {"type": "scrape", "url": "https://mipt.ru/news", "source": "МФТИ", "category": "university", "limit": 6},
    {"type": "scrape", "url": "https://mephi.ru/press-center/news/", "source": "МИФИ", "category": "university", "limit": 6},
    {"type": "rss", "url": "https://habr.com/ru/rss/hub/electronics/daily/?fl=ru", "source": "Хабр: Электроника", "category": "tech", "limit": 8},
    {"type": "rss", "url": "https://hackaday.com/feed/", "source": "Hackaday", "category": "tech", "limit": 6},
]

# Категории -> группы (как на макете)
CAT_GROUP = {
    "physics": "physics", "physics_news": "physics",
    "olympiad": "olympiads", "olympiad_archive": "olympiads",
    "math": "math", "math_news": "math",
    "code": "code", "code_news": "code",
    "robotics": "robotics", "university": "universities", "tech": "electronics",
}

GROUP_META = {
    "physics":     {"letter": "Ф", "color": "#10b981", "name": "Физика"},
    "olympiads":   {"letter": "О", "color": "#ef4444", "name": "Олимпиады"},
    "math":        {"letter": "М", "color": "#a855f7", "name": "Математика"},
    "code":        {"letter": "К", "color": "#f59e0b", "name": "Код"},
    "robotics":    {"letter": "Р", "color": "#14b8a6", "name": "Робототехника"},
    "universities":{"letter": "У", "color": "#3b82f6", "name": "Университеты"},
    "electronics": {"letter": "Э", "color": "#f97316", "name": "Электроника"},
}

# ==========================================
# WIKI (STEPik)
# ==========================================
RUST_DB = {
    "ownership": {
        "title": "Владение и заимствование",
        "lang": "Rust",
        "content": """
        <p>Rust — скорость C++ и безопасность Python. Главная фишка — <b>Ownership</b>.</p>
        <h2>Правила владения:</h2>
        <ul>
            <li>У каждого значения один владелец</li>
            <li>Когда владелец выходит из области видимости — значение удаляется</li>
        </ul>
        <pre><code class="language-rust">fn main() {
    let s1 = String::from("hello");
    let s2 = s1;
    println!("{}", s2);
}</code></pre>
        <a href="https://stepik.org/course/195449/promo" target="_blank" class="stepik-btn" style="--c:#ff9060">Пройти курс на Stepik →</a>
        """
    }
}

PYTHON_DB = {
    "generators": {
        "title": "Генераторы и yield",
        "lang": "Python",
        "content": """
        <p>Генераторы используют <code>yield</code> и «замораживают» состояние.</p>
        <pre><code class="language-python">def count_up_to(max):
    count = 1
    while count <= max:
        yield count
        count += 1

counter = count_up_to(3)
print(next(counter)) # 1</code></pre>
        <a href="https://stepik.org/course/214271/promo" target="_blank" class="stepik-btn" style="--c:#7cb8ea">Пройти курс на Stepik →</a>
        """
    }
}

# ==========================================
# ПАРСИНГ + РЕАЛЬНЫЕ КАРТИНКИ
# ==========================================
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
        resp = SESSION.get(item["url"], timeout=6)
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
                "category": item["category"]
            })
        logger.info(f"[RSS] {item['source']}: {len(results)}")
        return results
    except Exception as e:
        logger.warning(f"[RSS] {item['source']}: {e}")
        return []

def scrape_html(item):
    try:
        resp = SESSION.get(item["url"], timeout=8) # Чуть больше времени на ответ сайта
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        base_url = "/".join(item["url"].split("/")[:3])
        results = []
        # ДОБАВЛЕНЫ li a и table a для захвата ссылок внутри списков и таблиц mathus.ru
        nodes = soup.select("h2 a, h3 a, h4 a, .news-item a, .news-list a, .activity-news a, .archive-item a, article a, li a, table a")[:50]
        seen = set()
        for node in nodes:
            href = node.get("href", "")
            title = clean_text(node.get_text())
            if not title or len(title) < 10: continue
            if any(x in href.lower() for x in ["login", "register", "tag", "category", "javascript"]): continue
            
            # Корректное формирование абсолютных ссылок
            if href.startswith("/"): href = base_url + href
            elif not href.startswith("http"): href = base_url + "/" + href.lstrip("/")
            
            if href in seen: continue
            seen.add(href)

            img = ""
            parent = node.find_parent(["li", "div", "article", "section", "td", "tr"])
            if parent:
                im = parent.find("img")
                if im:
                    img = im.get("src") or im.get("data-src") or ""
                    if img.startswith("/"): img = base_url + img

            results.append({
                "title": title[:120],
                "link": href,
                "source": item["source"],
                "snippet": "Материалы, задания и разборы",
                "image": img,
                "pub_time": time.time(),
                "category": item["category"]
            })
            if len(results) >= item["limit"]: break
        logger.info(f"[Scrape] {item['source']}: {len(results)}")
        return results
    except Exception as e:
        logger.warning(f"[Scrape] {item['source']}: {e}")
        return []

def fetch_all_data():
    all_items = []
    pairs = []
    # Запускаем все источники параллельно
    with ThreadPoolExecutor(max_workers=24) as executor:
        for s in SOURCES:
            fn = parse_rss if s["type"] == "rss" else scrape_html
            pairs.append((executor.submit(fn, s), s["source"]))
        
        # Ждём результаты, но не дольше 15 секунд на каждый источник
        for fut, name in pairs:
            try:
                r = fut.result(timeout=15) 
                if r:
                    all_items.extend(r)
                else:
                    logger.warning(f"[EMPTY] {name}: 0 записей")
            except FuturesTimeoutError:
                logger.warning(f"[SKIP] {name}: таймаут потока >15с")
            except Exception as e:
                logger.warning(f"[SKIP] {name}: {e}")

    enriched = []
    for it in all_items:
        g = CAT_GROUP.get(it["category"], "physics")
        meta = GROUP_META.get(g)
        if not meta: continue
        
        ts = it.get("pub_time") or time.time()
        enriched.append({
            "title": it["title"], "link": it["link"], "source": it["source"],
            "snippet": it["snippet"], "image": it.get("image", ""),
            "group": g, "group_name": meta["name"], "color": meta["color"], "letter": meta["letter"],
            "time_str": rel_time(ts), "read_min": read_minutes(it["title"]),
            "category": it["category"], "pub_time": ts, "day": "",
        })
    enriched.sort(key=lambda x: x["pub_time"], reverse=True)
    logger.info(f"Итого: {len(enriched)} записей")
    return enriched

# ==========================================
# БАЗА ДАННЫХ
# ==========================================
@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
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
        for col, typ in [("image", "TEXT"), ("read_min", "INTEGER"), ('"group"', "TEXT")]:
            try:
                conn.execute("ALTER TABLE posts ADD COLUMN " + col + " " + typ)
            except Exception:
                pass

def save_to_db(items):
    with get_db() as conn:
        for it in items:
            conn.execute("""
                INSERT OR REPLACE INTO posts
                (link, title, source, snippet, color, letter, day, time_str, category, fetched_at, image, read_min, "group")
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (it["link"], it["title"], it["source"], it["snippet"], it["color"], it["letter"],
                  it["day"], it["time_str"], it["category"], time.time(),
                  it.get("image", ""), it.get("read_min", 5), it.get("group", "physics")))
def load_from_db(limit=200):
    with get_db() as conn:
        cur = conn.execute("SELECT * FROM posts ORDER BY fetched_at DESC LIMIT ?", (limit,))
        return [dict(r) for r in cur.fetchall()]
def normalize(rows):
    for r in rows:
        g = r.get("group") or CAT_GROUP.get(r.get("category"), "physics")
        # Безопасное получение метаданных группы
        meta = GROUP_META.get(g) 
        if not meta:
            continue  # Пропускаем битые записи
        
        r["group"] = g
        r["group_name"] = meta["name"]
        if not r.get("color"): r["color"] = meta["color"]
        if not r.get("letter"): r["letter"] = meta["letter"]
        if r.get("read_min") is None: r["read_min"] = 5
        if r.get("image") is None: r["image"] = ""
        if not r.get("time_str"): r["time_str"] = rel_time(r.get("fetched_at") or time.time())
    return rows

# ==========================================
# FLASK
# ==========================================
app = Flask(__name__)

def _background_fetch():
    try:
        items = fetch_all_data()
        if items:
            save_to_db(items)
            logger.info(f"Фон завершён: {len(items)}")
    except Exception as e:
        logger.error(f"Ошибка фона: {e}")

@app.route('/')
def index():
    news = normalize(load_from_db())
    return render_template_string(HTML_TEMPLATE, news=news, groups=GROUP_META, active_tab='feed')

@app.route('/wiki/rust')
def wiki_rust():
    topic = request.args.get('topic', list(RUST_DB.keys())[0])
    return render_template_string(HTML_TEMPLATE, active_tab='rust', db=RUST_DB, current_topic=topic, news=[], groups=GROUP_META)

@app.route('/wiki/python')
def wiki_python():
    topic = request.args.get('topic', list(PYTHON_DB.keys())[0])
    return render_template_string(HTML_TEMPLATE, active_tab='python', db=PYTHON_DB, current_topic=topic, news=[], groups=GROUP_META)

@app.route('/api/refresh')
def api_refresh():
    try:
        items = fetch_all_data()
        save_to_db(items)
        logger.info(f"REFRESH OK: {len(items)} записей сохранено")
        return jsonify({"status": "ok", "count": len(items)})
    except Exception as e:
        logger.error(f"Refresh error: {e}")
        return jsonify({"status": "error", "count": 0, "error": str(e)}), 500

@app.route('/health')
def health():
    return jsonify({"status": "ok", "sources": len(SOURCES)})

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="EngFeed">
<title>EngFeed</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/themes/prism-tomorrow.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/prism.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/components/prism-rust.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/components/prism-python.min.js"></script>
<style>
:root{
    /* Instagram-цвета */
    --bg:#000000;
    --panel:#121212;
    --card:#1a1a1a;
    --card2:#262626;
    --border:#333333;
    --t1:#ffffff;
    --t2:#a8a8a8;
    --t3:#737373;
    /* Instagram-градиент */
    --insta-gradient: linear-gradient(45deg, #f09433 0%, #e6683c 25%, #dc2743 50%, #cc2366 75%, #bc1888 100%);
    --accent:#e1306c;
    /* Изюминка — неоновое свечение */
    --glow: 0 0 30px rgba(225, 48, 108, 0.4);
    --glow-soft: 0 0 60px rgba(225, 48, 108, 0.2);
}

*{box-sizing:border-box;margin:0;padding:0;}

body{
    font-family:'Inter',sans-serif;
    background:#000;
    color:var(--t1);
    min-height:100vh;
    overflow-x:hidden;
    /* ИЗЮМИНКА — живой анимированный фон */
    background: linear-gradient(-45deg, #0a0a0a, #1a0a0f, #0f0a1a, #0a0a0a);
    background-size: 400% 400%;
    animation: gradientShift 15s ease infinite;
}

@keyframes gradientShift {
    0% { background-position: 0% 50%; }
    50% { background-position: 100% 50%; }
    100% { background-position: 0% 50%; }
}

/* Плавающие "пузыри" на фоне — изюминка */
body::before, body::after{
    content:'';
    position:fixed;
    border-radius:50%;
    filter:blur(80px);
    opacity:0.4;
    z-index:0;
    animation: float 20s ease-in-out infinite;
}

body::before{
    width:600px;
    height:600px;
    background: radial-gradient(circle, #f09433 0%, #dc2743 50%, transparent 70%);
    top:-200px;
    right:-100px;
    animation-delay:0s;
}

body::after{
    width:500px;
    height:500px;
    background: radial-gradient(circle, #bc1888 0%, #833ab4 50%, transparent 70%);
    bottom:-150px;
    left:-100px;
    animation-delay:-10s;
}

@keyframes float {
    0%, 100% { transform: translate(0, 0) scale(1); }
    33% { transform: translate(30px, -50px) scale(1.1); }
    66% { transform: translate(-20px, 30px) scale(0.9); }
}

::-webkit-scrollbar{width:8px;}
::-webkit-scrollbar-thumb{background:var(--insta-gradient);border-radius:4px;}

/* ШАПКА с Instagram-градиентом */
.topbar{
    position:sticky;
    top:0;
    z-index:50;
    height:64px;
    display:flex;
    align-items:center;
    gap:24px;
    padding:0 28px;
    background:rgba(0,0,0,0.85);
    backdrop-filter:blur(20px);
    border-bottom:1px solid var(--border);
}

.logo{
    font-size:26px;
    font-weight:800;
    letter-spacing:-1px;
    /* Instagram-градиент на тексте */
    background: var(--insta-gradient);
    -webkit-background-clip:text;
    -webkit-text-fill-color:transparent;
    background-clip:text;
    white-space:nowrap;
}

.searchwrap{flex:1;max-width:640px;margin:0 auto;position:relative;}
.searchwrap svg{position:absolute;left:14px;top:50%;transform:translateY(-50%);width:17px;height:17px;stroke:var(--t3);fill:none;stroke-width:2;}
.searchwrap input{
    width:100%;
    height:42px;
    background:var(--card);
    border:1px solid var(--border);
    border-radius:12px;
    color:var(--t1);
    font-size:14px;
    padding:0 64px 0 42px;
    outline:none;
    transition:.2s;
}
.searchwrap input:focus{
    border-color:var(--accent);
    box-shadow:0 0 0 3px rgba(225,48,108,0.2);
}
.kbd{position:absolute;right:12px;top:50%;transform:translateY(-50%);font-size:11px;color:var(--t3);background:var(--card2);border:1px solid var(--border);border-radius:6px;padding:2px 7px;}

.top-actions{display:flex;align-items:center;gap:14px;}
.iconbtn{
    position:relative;
    width:38px;
    height:38px;
    border-radius:50%;
    border:none;
    background:none;
    color:var(--t1);
    cursor:pointer;
    display:grid;
    place-items:center;
    transition:.2s;
}
.iconbtn:hover{
    background:var(--card2);
    transform:scale(1.1);
}
.iconbtn svg{width:20px;height:20px;stroke:currentColor;fill:none;stroke-width:2;}

.badge{
    position:absolute;
    top:0;
    right:0;
    min-width:18px;
    height:18px;
    background:var(--insta-gradient);
    color:#fff;
    font-size:10px;
    font-weight:700;
    border-radius:999px;
    display:none;
    align-items:center;
    justify-content:center;
    padding:0 5px;
    border:2px solid #000;
}

.avatar{
    position:relative;
    width:36px;
    height:36px;
    border-radius:50%;
    background:var(--insta-gradient);
    padding:2px;
}
.avatar-inner{
    width:100%;
    height:100%;
    border-radius:50%;
    background:var(--card);
    display:grid;
    place-items:center;
}
.avatar svg{width:18px;height:18px;stroke:var(--t2);fill:none;stroke-width:2;}
.online{position:absolute;right:2px;bottom:2px;width:10px;height:10px;background:#00ff88;border-radius:50%;border:2px solid #000;}
.spin{animation:spin 1s linear infinite;}
@keyframes spin{100%{transform:rotate(360deg);}}

/* КАРКАС */
.shell{display:flex;gap:28px;max-width:1440px;margin:0 auto;padding:28px;position:relative;z-index:1;}
.sidebar{
    width:236px;
    flex-shrink:0;
    position:sticky;
    top:92px;
    height:max-content;
    max-height:calc(100vh - 120px);
    overflow-y:auto;
    padding-bottom:12px;
    /* ИЗЮМИНКА — стеклянная панель */
    background:rgba(26,26,26,0.6);
    backdrop-filter:blur(10px);
    border-radius:20px;
    padding:20px;
    border:1px solid rgba(255,255,255,0.05);
}

.side-label{font-size:10px;font-weight:700;letter-spacing:.12em;color:var(--t3);margin:18px 0 10px 4px;}
.side-label:first-child{margin-top:0;}

.side-item{
    display:flex;
    align-items:center;
    gap:10px;
    width:100%;
    padding:12px 14px;
    border:none;
    border-radius:12px;
    background:none;
    color:var(--t2);
    font-size:14px;
    font-weight:500;
    cursor:pointer;
    text-decoration:none;
    transition:.2s;
    text-align:left;
}
.side-item:hover{
    background:rgba(255,255,255,0.05);
    color:var(--t1);
}
.side-item.active{
    /* Instagram-градиент фон */
    background:var(--insta-gradient);
    color:#fff;
    box-shadow:var(--glow-soft);
}
.side-item svg{width:20px;height:20px;stroke:currentColor;fill:none;stroke-width:2;flex-shrink:0;}

/* Категории с Instagram-стилем */
.cat-card{
    display:flex;
    align-items:center;
    gap:12px;
    width:100%;
    padding:14px;
    margin-bottom:10px;
    border:none;
    border-radius:16px;
    /* ИЗЮМИНКА — градиентная рамка */
    background: linear-gradient(var(--card), var(--card)) border-box,
                var(--insta-gradient) border-box;
    border:2px solid transparent;
    color:var(--t1);
    cursor:pointer;
    text-align:left;
    transition:.3s;
    position:relative;
    overflow:hidden;
}
.cat-card::before{
    content:'';
    position:absolute;
    top:0;
    left:-100%;
    width:100%;
    height:100%;
    background:var(--insta-gradient);
    opacity:0;
    transition:.3s;
}
.cat-card:hover::before{left:0;opacity:0.1;}
.cat-card:hover{
    transform:translateX(4px);
    box-shadow:var(--glow-soft);
}
.cat-card.active{
    background:var(--insta-gradient);
    box-shadow:var(--glow);
}
.cat-letter{
    width:36px;
    height:36px;
    display:grid;
    place-items:center;
    border-radius:10px;
    background:rgba(255,255,255,0.1);
    font-size:18px;
    font-weight:800;
    color:#fff;
    position:relative;
    z-index:1;
}
.cat-name{
    font-size:13px;
    font-weight:600;
    color:var(--t1);
    position:relative;
    z-index:1;
}

.manage{
    display:flex;
    justify-content:space-between;
    align-items:center;
    font-size:12px;
    color:var(--t3);
    margin-top:14px;
    padding:10px;
    cursor:pointer;
    border-radius:10px;
    transition:.2s;
}
.manage:hover{
    background:rgba(255,255,255,0.05);
    color:var(--t1);
}

/* ОСНОВНАЯ ЧАСТЬ */
.main{flex:1;min-width:0;}
.main-head{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-bottom:22px;flex-wrap:wrap;}
.main-head h1{font-size:26px;font-weight:800;letter-spacing:-.02em;}

.pills{display:flex;gap:8px;flex-wrap:wrap;}
.pill{
    height:36px;
    padding:0 18px;
    border-radius:20px;
    border:1px solid var(--border);
    background:var(--card);
    color:var(--t2);
    font-size:13px;
    font-weight:600;
    cursor:pointer;
    transition:.2s;
}
.pill:hover{
    background:var(--card2);
    color:var(--t1);
}
.pill.active{
    background:var(--insta-gradient);
    border-color:transparent;
    color:#fff;
    box-shadow:var(--glow-soft);
}

/* СЕТКА КАРТОЧЕК */
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(430px,1fr));gap:20px;}

.card{
    display:flex;
    gap:18px;
    background:var(--card);
    border:1px solid var(--border);
    border-radius:20px;
    padding:20px;
    cursor:pointer;
    transition:.3s;
    animation:fadeUp .35s ease forwards;
    opacity:0;
    position:relative;
    overflow:hidden;
}
/* ИЗЮМИНКА — градиентная подсветка при наведении */
.card::before{
    content:'';
    position:absolute;
    top:0;
    left:0;
    right:0;
    bottom:0;
    background:var(--insta-gradient);
    opacity:0;
    transition:.3s;
    z-index:0;
}
.card:hover::before{
    opacity:0.05;
}
.card:hover{
    border-color:var(--accent);
    transform:translateY(-4px);
    box-shadow:var(--glow);
}
.card.read{opacity:.5;}
.card.read:hover{opacity:.65;}

.card-left{flex:1;min-width:0;display:flex;flex-direction:column;position:relative;z-index:1;}
.card-meta{display:flex;align-items:center;gap:8px;font-size:12px;margin-bottom:10px;}

.dot{
    width:8px;
    height:8px;
    border-radius:50%;
    background:var(--c);
    box-shadow:0 0 10px var(--c);
}
.cat{color:var(--t2);font-weight:600;}
.time{margin-left:auto;color:var(--t3);}

.card-title{font-size:16.5px;font-weight:700;line-height:1.35;margin-bottom:6px;}
.card-title a{color:var(--t1);text-decoration:none;}
.card:hover .card-title a{color:#e1306c;}

.src{font-size:12.5px;color:var(--t2);margin-bottom:8px;}
.snippet{font-size:13px;line-height:1.55;color:var(--t2);display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;}

.card-foot{display:flex;align-items:center;gap:6px;margin-top:auto;padding-top:14px;}
.act{
    width:34px;
    height:34px;
    border:none;
    border-radius:10px;
    background:none;
    color:var(--t3);
    cursor:pointer;
    display:grid;
    place-items:center;
    transition:.2s;
}
.act:hover{
    background:var(--card2);
    color:var(--t1);
}
.act.on{
    background:var(--insta-gradient);
    color:#fff;
}
.act svg{width:17px;height:17px;stroke:currentColor;fill:none;stroke-width:2;}

.readtime{
    margin-left:auto;
    font-size:11.5px;
    color:var(--t2);
    background:var(--card2);
    border:1px solid var(--border);
    border-radius:10px;
    padding:4px 10px;
}

.thumb{
    width:190px;
    height:128px;
    flex-shrink:0;
    border-radius:14px;
    overflow:hidden;
    position:relative;
    background:linear-gradient(135deg,var(--ctint),#1a1a1a);
}
.thumb img{width:100%;height:100%;object-fit:cover;position:absolute;inset:0;}
.thumb-letter{
    position:absolute;
    inset:0;
    display:grid;
    place-items:center;
    font-size:40px;
    font-weight:800;
    background:var(--insta-gradient);
    -webkit-background-clip:text;
    -webkit-text-fill-color:transparent;
    background-clip:text;
    opacity:.6;
}

/* WIKI */
.wiki-wrap{max-width:860px;}
.wiki-top{display:flex;gap:8px;margin-bottom:20px;flex-wrap:wrap;}
.wiki-content{
    background:var(--card);
    border:1px solid var(--border);
    border-radius:20px;
    padding:44px;
}
.wiki-content h1{font-size:30px;margin-bottom:20px;letter-spacing:-.02em;}
.wiki-content h2{font-size:21px;margin:30px 0 14px;}
.wiki-content p{font-size:15px;line-height:1.7;color:var(--t2);margin-bottom:14px;}
.wiki-content ul{margin:0 0 18px 22px;color:var(--t2);line-height:1.7;font-size:15px;}
.wiki-content pre{background:#0a0a0a !important;border:1px solid var(--border);border-radius:12px;padding:16px;margin:16px 0;}
.wiki-content code{font-family:'JetBrains Mono',monospace;font-size:13.5px;}

.stepik-btn{
    display:inline-block;
    margin-top:14px;
    padding:12px 22px;
    background:var(--insta-gradient);
    border:none;
    color:#fff;
    text-decoration:none;
    border-radius:12px;
    font-weight:700;
    font-size:14px;
    box-shadow:var(--glow-soft);
    transition:.2s;
}
.stepik-btn:hover{
    transform:translateY(-2px);
    box-shadow:var(--glow);
}

.empty-state{text-align:center;padding:70px 20px;color:var(--t3);}
.empty-state button{
    margin-top:18px;
    padding:12px 26px;
    background:var(--insta-gradient);
    color:#fff;
    border:none;
    border-radius:12px;
    font-weight:700;
    cursor:pointer;
    box-shadow:var(--glow-soft);
    transition:.2s;
}
.empty-state button:hover{
    transform:translateY(-2px);
    box-shadow:var(--glow);
}

@keyframes fadeUp{to{opacity:1;}}

@media(max-width:1000px){.sidebar{display:none;}.grid{grid-template-columns:1fr;}}
@media(max-width:560px){.thumb{display:none;}.topbar{padding:0 14px;}.shell{padding:16px;}}
</style>
</head>
<body>
<header class="topbar">
    <div class="logo">EngFeed</div>
    <div class="searchwrap">
        <svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
        <input id="search" placeholder="Поиск статей, источников, тегов...">
        <span class="kbd">K</span>
    </div>
    <div class="top-actions">
        <button class="iconbtn" id="bellBtn" title="Непрочитанные">
            <svg viewBox="0 0 24 24"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9M13.73 21a2 2 0 0 1-3.46 0"/></svg>
            <span class="badge" id="unreadBadge">0</span>
        </button>
        <button class="iconbtn" id="refreshBtn" title="Обновить">
            <svg viewBox="0 0 24 24"><path d="M21 2v6h-6M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/></svg>
        </button>
        <div class="avatar">
            <div class="avatar-inner">
                <svg viewBox="0 0 24 24"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
            </div>
            <i class="online"></i>
        </div>
    </div>
</header>

<div class="shell">
    <aside class="sidebar">
        <div class="side-label">БИБЛИОТЕКА</div>
        <button class="side-item active" data-mode="all"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><path d="M2 12h20M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg> Вся лента</button>
        <button class="side-item" data-mode="fav"><svg viewBox="0 0 24 24"><path d="m12 2 3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01z"/></svg> Избранное</button>
        <button class="side-item" data-mode="archived"><svg viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M16 13H8M16 17H8"/></svg> Прочитанное</button>
        <div class="side-label">WIKI</div>
        <a class="side-item" href="/wiki/rust"><svg viewBox="0 0 24 24"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20M4 19.5A2.5 2.5 0 0 0 6.5 22H20V2H6.5A2.5 2.5 0 0 0 4 4.5z"/></svg> Rust: конспект</a>
        <a class="side-item" href="/wiki/python"><svg viewBox="0 0 24 24"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20M4 19.5A2.5 2.5 0 0 0 6.5 22H20V2H6.5A2.5 2.5 0 0 0 4 4.5z"/></svg> Python: конспект</a>
        <div class="side-label">КАТЕГОРИИ</div>
        {% for gid, g in groups.items() %}
        <button class="cat-card" data-group="{{ gid }}" style="--c:{{ g.color }};--ctint:{{ g.color }}26">
            <span class="cat-letter">{{ g.letter }}</span>
            <span class="cat-name">{{ g.name }}</span>
        </button>
        {% endfor %}
        <div class="manage">Управление категориями <span>+</span></div>
    </aside>

    <main class="main">
    {% if active_tab in ['rust','python'] %}
        <div class="wiki-wrap">
            <div class="wiki-top">
                {% for key, item in db.items() %}
                <button class="pill {{ 'active' if current_topic == key else '' }}" onclick="location.href='?topic={{ key }}'">{{ item.title }}</button>
                {% endfor %}
                <button class="pill" onclick="location.href='/'">← Лента</button>
            </div>
            <div class="wiki-content">
                <h1>{{ db[current_topic].title }}</h1>
                {{ db[current_topic].content | safe }}
            </div>
        </div>
    {% else %}
        <div class="main-head">
            <h1>Лента на сегодня</h1>
            <div class="pills">
                <button class="pill active" data-group="all">Все</button>
                <button class="pill" data-mode="unread">Непрочитанные</button>
                <button class="pill" data-group="physics">Физика</button>
                <button class="pill" data-group="olympiads">Олимпиады</button>
                <button class="pill" data-group="code">Код</button>
            </div>
        </div>

        {% if not news %}
        <div class="empty-state">
            <p style="font-size:18px;margin-bottom:6px;">Лента пуста</p>
            <p style="font-size:13px;">Идёт первая загрузка источников (10–20 секунд)</p>
            <button onclick="doRefresh()">Загрузить новости</button>
        </div>
        {% endif %}

        <div class="grid" id="grid">
        {% for item in news %}
            <article class="card" data-group="{{ item.group }}" data-link="{{ item.link }}" style="--c:{{ item.color }};--ctint:{{ item.color }}1f">
                <div class="card-left">
                    <div class="card-meta"><span class="dot"></span><span class="cat">{{ item.group_name }}</span><span class="time">{{ item.time_str }}</span></div>
                    <h2 class="card-title"><a href="{{ item.link }}" target="_blank" rel="noopener">{{ item.title }}</a></h2>
                    <div class="src">{{ item.source }}</div>
                    <p class="snippet">{{ item.snippet }}</p>
                    <div class="card-foot">
                        <button class="act bm" title="В закладки"><svg viewBox="0 0 24 24"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/></svg></button>
                        <button class="act sh" title="Копировать ссылку"><svg viewBox="0 0 24 24"><circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><path d="m8.59 13.51 6.83 3.98M15.41 6.51l-6.82 3.98"/></svg></button>
                        <span class="readtime">{{ item.read_min }} мин чтения</span>
                    </div>
                </div>
                <div class="thumb">
                    <span class="thumb-letter">{{ item.letter }}</span>
                    {% if item.image %}<img src="{{ item.image }}" loading="lazy" onerror='this.remove()'>{% endif %}
                </div>
            </article>
        {% endfor %}
        </div>
    {% endif %}
    </main>
</div>

<script>
// ==========================================
// JAVASCRIPT ФУНКЦИОНАЛ (без изменений)
// ==========================================
var refreshAttempts = 0;
var maxRefreshAttempts = 10;

function resetRefreshState() {
    var svg = document.querySelector('#refreshBtn svg');
    var btn = document.querySelector('#refreshBtn');
    var ebtn = document.querySelector('.empty-state button');
    if (svg) svg.classList.remove('spin');
    if (btn) btn.disabled = false;
    if (ebtn) ebtn.disabled = false;
    refreshAttempts = 0;
}

function doRefresh() {
    var svg = document.querySelector('#refreshBtn svg');
    var btn = document.querySelector('#refreshBtn');
    var ebtn = document.querySelector('.empty-state button');
    if (svg) svg.classList.add('spin');
    if (btn) btn.disabled = true;
    if (ebtn) {
        ebtn.innerText = 'Загружаем источники... (подождите, это может занять до 30 секунд)';
        ebtn.disabled = true;
    }
    fetch('/api/refresh')
        .then(function(response) {
            if (!response.ok) throw new Error('Network response was not ok');
            return response.json();
        })
        .then(function(data) {
            if (data.count > 0) {
                location.reload();
            } else {
                refreshAttempts++;
                if (refreshAttempts < maxRefreshAttempts) {
                    if (ebtn) ebtn.innerText = 'Повторная попытка... (' + refreshAttempts + '/' + maxRefreshAttempts + ')';
                    setTimeout(doRefresh, 4000);
                } else {
                    resetRefreshState();
                    if (ebtn) ebtn.innerText = 'Не удалось загрузить. Нажмите, чтобы попробовать снова';
                }
            }
        })
        .catch(function(error) {
            console.error('Refresh error:', error);
            refreshAttempts++;
            if (refreshAttempts < maxRefreshAttempts) {
                if (ebtn) ebtn.innerText = 'Ошибка сети. Повторная попытка...';
                setTimeout(doRefresh, 4000);
            } else {
                resetRefreshState();
                if (ebtn) ebtn.innerText = 'Ошибка соединения. Нажмите, чтобы попробовать снова';
            }
        });
}

document.getElementById('refreshBtn').addEventListener('click', function() {
    refreshAttempts = 0;
    doRefresh();
});

if (document.querySelector('.empty-state')) {
    doRefresh();
}

var readLinks = JSON.parse(localStorage.getItem('readLinks') || '[]');
var bookmarks = JSON.parse(localStorage.getItem('bookmarks') || '[]');
var subs = JSON.parse(localStorage.getItem('subs') || '[]');
var mode = 'all', group = 'all', query = '';

function apply() {
    document.querySelectorAll('.card').forEach(function (card) {
        var link = card.getAttribute('data-link');
        var g = card.getAttribute('data-group');
        var text = card.innerText.toLowerCase();
        var okGroup = (group === 'all') || (g === group);
        var okMode = true;
        if (mode === 'unread') okMode = readLinks.indexOf(link) === -1;
        if (mode === 'fav') okMode = bookmarks.indexOf(link) !== -1;
        if (mode === 'archived') okMode = readLinks.indexOf(link) !== -1;
        var okQ = !query || text.indexOf(query) !== -1;
        card.style.display = (okGroup && okMode && okQ) ? 'flex' : 'none';
    });
}

function updateBadge() {
    var n = 0;
    document.querySelectorAll('.card').forEach(function (c) {
        if (readLinks.indexOf(c.getAttribute('data-link')) === -1) n++;
    });
    var b = document.getElementById('unreadBadge');
    if (b) { b.innerText = n; b.style.display = n > 0 ? 'flex' : 'none'; }
}

document.querySelectorAll('.card').forEach(function (card) {
    var link = card.getAttribute('data-link');
    if (readLinks.indexOf(link) !== -1) card.classList.add('read');
    var bm = card.querySelector('.bm');
    if (bookmarks.indexOf(link) !== -1) bm.classList.add('on');

    card.addEventListener('click', function (e) {
        if (e.target.closest('.act')) return;
        if (readLinks.indexOf(link) === -1) {
            readLinks.push(link);
            localStorage.setItem('readLinks', JSON.stringify(readLinks));
            card.classList.add('read');
            updateBadge();
            if (mode === 'unread') apply();
        }
        if (!e.target.closest('a')) window.open(link, '_blank');
    });

    bm.addEventListener('click', function (e) {
        e.stopPropagation();
        if (bookmarks.indexOf(link) === -1) { bookmarks.push(link); bm.classList.add('on'); }
        else { bookmarks = bookmarks.filter(function (l) { return l !== link; }); bm.classList.remove('on'); }
        localStorage.setItem('bookmarks', JSON.stringify(bookmarks));
        if (mode === 'fav') apply();
    });

    card.querySelector('.sh').addEventListener('click', function (e) {
        e.stopPropagation();
        if (navigator.clipboard) navigator.clipboard.writeText(link);
        var sh = card.querySelector('.sh');
        sh.classList.add('on');
        setTimeout(function () { sh.classList.remove('on'); }, 1200);
    });
});

function syncUI() {
    document.querySelectorAll('.pills .pill').forEach(function (p) {
        var pg = p.getAttribute('data-group');
        var pm = p.getAttribute('data-mode');
        var on = false;
        if (pm === 'unread') on = (mode === 'unread');
        else if (pg) on = (group === pg && mode === 'all');
        p.classList.toggle('active', on);
    });
    document.querySelectorAll('.side-item[data-mode]').forEach(function (b) {
        b.classList.toggle('active', b.getAttribute('data-mode') === mode);
    });
    document.querySelectorAll('.cat-card').forEach(function (c) {
        c.classList.toggle('active', c.getAttribute('data-group') === group);
    });
}

function handleFilter(btn) {
    var pg = btn.getAttribute('data-group');
    var pm = btn.getAttribute('data-mode');
    if (btn.classList.contains('pill')) {
        if (pm === 'unread') { mode = 'unread'; group = 'all'; }
        else if (pg) { group = pg; mode = 'all'; }
    } else if (btn.classList.contains('cat-card')) {
        group = pg; mode = 'all';
    } else if (btn.classList.contains('side-item')) {
        mode = pm; group = 'all';
    }
    syncUI();
    apply();
}

document.querySelectorAll('.pills .pill, .cat-card, .side-item[data-mode]').forEach(function (btn) {
    btn.addEventListener('click', function () { handleFilter(btn); });
});

document.getElementById('bellBtn').addEventListener('click', function () {
    mode = (mode === 'unread') ? 'all' : 'unread';
    if (mode === 'all') group = 'all';
    syncUI();
    apply();
});

syncUI();

var searchInput = document.getElementById('search');
searchInput.addEventListener('input', function () { query = searchInput.value.toLowerCase(); apply(); });
document.addEventListener('keydown', function (e) {
    if (e.key === '/' || (e.metaKey && e.key.toLowerCase() === 'k')) { e.preventDefault(); searchInput.focus(); }
});

document.querySelectorAll('.subbtn').forEach(function (btn) {
    var name = btn.getAttribute('data-name');
    if (subs.indexOf(name) !== -1) { btn.classList.add('on'); btn.innerText = 'Вы подписаны'; }
    btn.addEventListener('click', function () {
        if (subs.indexOf(name) === -1) { subs.push(name); btn.classList.add('on'); btn.innerText = 'Вы подписаны'; }
        else { subs = subs.filter(function (s) { return s !== name; }); btn.classList.remove('on'); btn.innerText = 'Подписаться'; }
        localStorage.setItem('subs', JSON.stringify(subs));
    });
});

updateBadge();
</script>
</body>
</html>
"""



init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False) 
