import threading
import os
import re
import time
import datetime
import html
import sqlite3
import logging
import sys
from contextlib import contextmanager

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

# ВСЕ ИСТОЧНИКИ
SOURCES = [
    # ФИЗИКА 9 КЛАСС (БОЛЬШЕ НОВОСТЕЙ)
    {"type": "scrape", "url": "https://mathus.ru/phys/", "source": "Mathus: Физика 9", "category": "physics", "limit": 12},
    {"type": "rss", "url": "https://elementy.ru/rss/news", "source": "Элементы: Физика", "category": "physics", "limit": 10},
    
    # ИНТЕРЕСНЫЕ НОВОСТИ ФИЗИКИ
    {"type": "rss", "url": "https://www.quantamagazine.org/feed/", "source": "Quanta: Физика", "category": "physics_news", "limit": 8},
    {"type": "rss", "url": "https://physicsworld.com/feed/", "source": "Physics World", "category": "physics_news", "limit": 6},
    {"type": "rss", "url": "http://export.arxiv.org/rss/physics", "source": "arXiv: Физика", "category": "physics_news", "limit": 8},
    
    # ОЛИМПИАДЫ: АКТУАЛЬНЫЕ
    {"type": "scrape", "url": "https://olimpiada.ru/activity/394", "source": "Физтех: Физика", "category": "olympiad", "limit": 8},
    {"type": "scrape", "url": "https://olimpiada.ru/activity/5878", "source": "Росатом", "category": "olympiad", "limit": 8},
    {"type": "scrape", "url": "https://olimpiada.ru/activity/7288", "source": "Росатом Юниор", "category": "olympiad", "limit": 6},
    {"type": "scrape", "url": "https://olimpiada.ru/activity/395", "source": "Физтех: Математика", "category": "olympiad", "limit": 6},
    
    # ОЛИМПИАДЫ: АРХИВ (ЧТО БЫЛО)
    {"type": "scrape", "url": "https://olimpiada.ru/archive/394", "source": "Физтех: Архив", "category": "olympiad_archive", "limit": 10},
    {"type": "scrape", "url": "https://olimpiada.ru/archive/5878", "source": "Росатом: Архив", "category": "olympiad_archive", "limit": 10},
    {"type": "scrape", "url": "https://xn--l1afu.xn--p1ai/материалы/видеоразборы-всош-физика/", "source": "ВсОШ: Архив", "category": "olympiad_archive", "limit": 8},
    
    # ИНТЕРЕСНЫЕ НОВОСТИ МАТЕМАТИКИ
    {"type": "scrape", "url": "https://mathus.ru/math/", "source": "Mathus: Математика", "category": "math", "limit": 10},
    {"type": "rss", "url": "https://www.quantamagazine.org/feed/", "source": "Quanta: Математика", "category": "math_news", "limit": 6},
    
    # ИНТЕРЕСНЫЕ НОВОСТИ ПРОГРАММИРОВАНИЯ
    {"type": "rss", "url": "https://habr.com/ru/rss/hub/python/daily/?fl=ru", "source": "Хабр: Python", "category": "code", "limit": 8},
    {"type": "rss", "url": "https://habr.com/ru/rss/hub/rust/daily/?fl=ru", "source": "Хабр: Rust", "category": "code", "limit": 8},
    {"type": "rss", "url": "https://hnrss.org/frontpage", "source": "Hacker News", "category": "code_news", "limit": 10},
    {"type": "rss", "url": "https://dev.to/feed", "source": "Dev.to", "category": "code_news", "limit": 8},
    
    # УНИВЕРСИТЕТЫ
    {"type": "scrape", "url": "https://mipt.ru/news", "source": "МФТИ", "category": "university", "limit": 8},
    {"type": "scrape", "url": "https://mephi.ru/press-center/news/", "source": "МИФИ", "category": "university", "limit": 8},
    
    # ЭЛЕКТРОНИКА
    {"type": "rss", "url": "https://habr.com/ru/rss/hub/electronics/daily/?fl=ru", "source": "Хабр: Электроника", "category": "tech", "limit": 8},
    {"type": "rss", "url": "https://hackaday.com/feed/", "source": "Hackaday", "category": "tech", "limit": 6},
]

STYLES = {
    "physics": {"color": "#10b981", "letter": "Ф"},
    "physics_news": {"color": "#059669", "letter": "ФН"},
    "olympiad": {"color": "#ef4444", "letter": "О"},
    "olympiad_archive": {"color": "#dc2626", "letter": "А"},
    "university": {"color": "#3b82f6", "letter": "В"},
    "math": {"color": "#a855f7", "letter": "М"},
    "math_news": {"color": "#9333ea", "letter": "МН"},
    "code": {"color": "#f59e0b", "letter": "К"},
    "code_news": {"color": "#d97706", "letter": "КН"},
    "tech": {"color": "#06b6d4", "letter": "Э"},
}

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
        <a href="https://stepik.org/course/195449/promo" target="_blank" style="display:inline-block;margin-top:16px;padding:12px 24px;background:#ff9060;color:#000;text-decoration:none;border-radius:8px;font-weight:600;">Пройти курс на Stepik →</a>
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
        <a href="https://stepik.org/course/214271/promo" target="_blank" style="display:inline-block;margin-top:16px;padding:12px 24px;background:#7cb8ea;color:#000;text-decoration:none;border-radius:8px;font-weight:600;">Пройти курс на Stepik →</a>
        """
    }
}

def clean_text(text):
    if not text: return ""
    text = html.unescape(text)
    text = re.sub(r'<.*?>', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def parse_rss(item):
    try:
        resp = SESSION.get(item["url"], timeout=8)
        feed = feedparser.parse(resp.content)
        results = []
        for entry in feed.entries[:item["limit"]]:
            pub_time = time.mktime(entry.published_parsed) if hasattr(entry, 'published_parsed') else time.time()
            results.append({
                "title": clean_text(entry.get("title", "Без заголовка")),
                "link": entry.get("link", "#"),
                "source": item["source"],
                "snippet": clean_text(entry.get("summary", ""))[:200] + "...",
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
        resp = SESSION.get(item["url"], timeout=8)
        soup = BeautifulSoup(resp.text, "html.parser")
        results = []
        
        nodes = soup.select("h2 a, h3 a, h4 a, .news-item a, .news-list a, .activity-news a, .archive-item a")[:50]
        
        seen = set()
        for node in nodes:
            href = node.get("href", "")
            title = clean_text(node.get_text())
            
            if not title or len(title) < 10: continue
            if "login" in href or "tag" in href or "category" in href: continue
            
            if href.startswith("/"):
                base = "/".join(item["url"].split("/")[:3])
                href = base + href
            elif not href.startswith("http"):
                href = item["url"].rstrip("/") + "/" + href.lstrip("/")
            
            if href in seen: continue
            seen.add(href)
            
            results.append({
                "title": title[:100],
                "link": href,
                "source": item["source"],
                "snippet": "Материалы и задания олимпиады",
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
    for item in SOURCES:
        if item["type"] == "rss":
            all_items.extend(parse_rss(item))
        else:
            all_items.extend(scrape_html(item))
    
    all_items.sort(key=lambda x: x["pub_time"], reverse=True)
    
    enriched = []
    for item in all_items:
        style = STYLES.get(item["category"], {"color": "#6b7280", "letter": "?"})
        dt = datetime.datetime.fromtimestamp(item["pub_time"])
        day = "Сегодня" if dt.date() == datetime.date.today() else "Ранее"
        
        enriched.append({
            **item,
            "color": style["color"],
            "letter": style["letter"],
            "day": day,
            "time_str": dt.strftime("%H:%M")
        })
    return enriched

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
                link TEXT PRIMARY KEY,
                title TEXT, source TEXT, snippet TEXT,
                color TEXT, letter TEXT, day TEXT, time_str TEXT,
                category TEXT, fetched_at REAL
            );
        """)

def save_to_db(items):
    with get_db() as conn:
        for item in items:
            conn.execute("""
                INSERT OR REPLACE INTO posts 
                (link, title, source, snippet, color, letter, day, time_str, category, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (item["link"], item["title"], item["source"], item["snippet"], 
                  item["color"], item["letter"], item["day"], item["time_str"], 
                  item["category"], time.time()))

def load_from_db(limit=200):
    with get_db() as conn:
        cursor = conn.execute("SELECT * FROM posts ORDER BY fetched_at DESC LIMIT ?", (limit,))
        return [dict(row) for row in cursor.fetchall()]

app = Flask(__name__)

def _background_fetch():
    """Фоновая загрузка — не блокирует ответ."""
    try:
        items = fetch_all_data()
        save_to_db(items)
        logger.info(f"Background fetch done: {len(items)}")
    except Exception as e:
        logger.error(f"Background fetch error: {e}")

@app.route('/')
def index():
    news = load_from_db()
    if not news:
        # Запускаем загрузку в фоне, НЕ ждём
        threading.Thread(target=_background_fetch, daemon=True).start()
    return render_template_string(HTML_TEMPLATE, news=news)

@app.route('/wiki/rust')
def wiki_rust():
    topic = request.args.get('topic', list(RUST_DB.keys())[0])
    return render_template_string(HTML_TEMPLATE, active_tab='rust', db=RUST_DB, current_topic=topic)

@app.route('/wiki/python')
def wiki_python():
    topic = request.args.get('topic', list(PYTHON_DB.keys())[0])
    return render_template_string(HTML_TEMPLATE, active_tab='python', db=PYTHON_DB, current_topic=topic)

@app.route('/api/refresh')
def api_refresh():
    try:
        items = fetch_all_data()
        save_to_db(items)
        return jsonify({"status": "ok", "count": len(items)})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/health')
def health():
    return jsonify({"status": "ok", "sources": len(SOURCES)})

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>EngFeed</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/themes/prism-tomorrow.min.css">
    <script src="https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/prism.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/components/prism-rust.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/components/prism-python.min.js"></script>
    <style>
        :root { --bg:#09090b; --surface:#18181b; --surface-hover:#27272a; --border:#27272a; --text-1:#fafafa; --text-2:#a1a1aa; --text-3:#71717a; }
        * { box-sizing:border-box; margin:0; padding:0; }
        body { font-family:'Inter',sans-serif; background:var(--bg); color:var(--text-1); padding-top:70px; }
        header { position:fixed; top:0; left:0; right:0; height:70px; background:rgba(9,9,11,0.8); backdrop-filter:blur(16px); border-bottom:1px solid var(--border); display:flex; align-items:center; justify-content:center; z-index:100; }
        .header-inner { width:100%; max-width:900px; padding:0 24px; display:flex; justify-content:space-between; align-items:center; }
        .logo { font-weight:700; font-size:18px; display:flex; align-items:center; gap:10px; }
        .logo-icon { width:28px; height:28px; background:linear-gradient(135deg,#8b5cf6,#06b6d4); border-radius:8px; display:grid; place-items:center; color:#fff; font-weight:800; font-size:14px; }
        .nav-tabs { display:flex; gap:4px; background:var(--surface); padding:4px; border-radius:10px; }
        .nav-tab { color:var(--text-3); padding:8px 14px; border-radius:8px; text-decoration:none; font-size:13px; font-weight:500; border:none; background:none; cursor:pointer; }
        .nav-tab:hover { background:rgba(255,255,255,0.06); color:var(--text-1); }
        .nav-tab.active { background:rgba(139,92,246,0.16); color:#c4b5fd; }
        .refresh-btn { width:40px; height:40px; border-radius:10px; border:1px solid var(--border); background:var(--surface); color:var(--text-2); cursor:pointer; display:grid; place-items:center; }
        .refresh-btn:hover { background:var(--surface-hover); color:var(--text-1); }
        .refresh-btn svg { width:18px; height:18px; stroke:currentColor; fill:none; stroke-width:2; }
        .spin { animation:spin 1s linear infinite; }
        @keyframes spin { 100%{transform:rotate(360deg);} }
        .container { max-width:900px; margin:0 auto; padding:24px; }
        .day-header { font-size:12px; font-weight:600; color:var(--text-3); text-transform:uppercase; letter-spacing:0.05em; margin:32px 0 16px; }
        .card { background:var(--surface); border:1px solid var(--border); border-radius:16px; padding:20px; margin-bottom:12px; display:flex; gap:16px; cursor:pointer; text-decoration:none; color:inherit; transition:all 0.2s; }
        .card:hover { background:var(--surface-hover); border-color:#3f3f46; transform:translateY(-2px); box-shadow:0 12px 24px -8px rgba(0,0,0,0.4); }
        .avatar { width:44px; height:44px; border-radius:12px; flex-shrink:0; display:grid; place-items:center; font-weight:700; font-size:16px; color:#000; }
        .card-body { flex:1; min-width:0; }
        .meta { display:flex; justify-content:space-between; margin-bottom:6px; font-size:12px; }
        .source-name { color:var(--text-2); font-weight:500; }
        .time { color:var(--text-3); font-family:'JetBrains Mono',monospace; }
        .title { font-size:16px; font-weight:600; line-height:1.4; margin-bottom:8px; }
        .card:hover .title { color:#8b5cf6; }
        .snippet { font-size:14px; line-height:1.6; color:var(--text-2); display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
        .wiki-layout { display:flex; gap:32px; max-width:1200px; margin:40px auto; padding:0 24px; }
        .wiki-sidebar { width:260px; flex-shrink:0; position:sticky; top:84px; }
        .sidebar-label { font-size:11px; font-weight:600; color:var(--text-3); text-transform:uppercase; margin-bottom:12px; }
        .wiki-menu { list-style:none; }
        .wiki-menu a { display:flex; align-items:center; gap:8px; padding:8px 10px; border-radius:8px; font-size:13px; color:var(--text-2); text-decoration:none; }
        .wiki-menu a:hover { background:rgba(255,255,255,0.06); color:var(--text-1); }
        .wiki-menu a.active { background:rgba(139,92,246,0.16); color:#c4b5fd; }
        .wiki-content { flex:1; max-width:860px; background:var(--surface); border:1px solid var(--border); border-radius:20px; padding:48px; }
        .wiki-content h1 { font-size:32px; margin-bottom:24px; }
        .wiki-content h2 { font-size:22px; margin:36px 0 16px; }
        .wiki-content p { font-size:15px; line-height:1.7; color:var(--text-2); margin-bottom:16px; }
        .wiki-content pre { background:#101013; border-radius:12px; padding:16px; margin:16px 0; }
        .wiki-content code { font-family:'JetBrains Mono',monospace; font-size:13.5px; }
        .empty-state { text-align:center; padding:60px 20px; color:var(--text-3); }
        .empty-state button { margin-top:20px; padding:12px 24px; background:#8b5cf6; color:#fff; border:none; border-radius:8px; cursor:pointer; font-weight:600; }
        .empty-state button:hover { background:#7c3aed; }
        @media(max-width:768px) { .wiki-layout{flex-direction:column;} .wiki-sidebar{width:100%;position:static;} }
    </style>
</head>
<body>
    <header>
        <div class="header-inner">
            <div class="logo"><div class="logo-icon">EF</div><span>EngFeed</span></div>
            <div class="nav-tabs">
                <button onclick="location.href='/'" class="nav-tab {{ 'active' if active_tab=='feed' or not active_tab else '' }}">Лента</button>
                <button onclick="location.href='/wiki/rust'" class="nav-tab {{ 'active' if active_tab=='rust' else '' }}">Rust</button>
                <button onclick="location.href='/wiki/python'" class="nav-tab {{ 'active' if active_tab=='python' else '' }}">Python</button>
            </div>
            {% if not active_tab or active_tab=='feed' %}
            <button class="refresh-btn" onclick="refresh()"><svg viewBox="0 0 24 24"><path d="M21 2v6h-6M3 12a9 9 0 1 0 9-9"/></svg></button>
            {% endif %}
        </div>
    </header>

    {% if active_tab in ['rust','python'] %}
    <div class="wiki-layout">
        <aside class="wiki-sidebar">
            <div class="sidebar-label">{{ 'Rust' if active_tab=='rust' else 'Python' }}</div>
            <ul class="wiki-menu">
                {% for key,item in db.items() %}
                <li><a href="?topic={{key}}" class="{{ 'active' if current_topic==key else '' }}">{{item.title}}</a></li>
                {% endfor %}
            </ul>
            <div style="margin-top:24px;"><a href="/" style="font-size:13px;color:var(--text-3);text-decoration:none;">← К ленте</a></div>
        </aside>
        <main class="wiki-content">
            <h1>{{db[current_topic].title}}</h1>
            {{db[current_topic].content|safe}}
        </main>
    </div>
    {% else %}
    <main class="container">
        {% if not news %}
        <div class="empty-state">
            <p style="font-size:18px;margin-bottom:8px;">Лента пуста</p>
            <p style="font-size:14px;">Нажми кнопку ниже, чтобы загрузить новости</p>
            <button onclick="refresh()">Загрузить новости</button>
        </div>
        {% endif %}
        {% set current_day="" %}
        {% for item in news %}
            {% if item.day!=current_day %}{% set current_day=item.day %}<div class="day-header">{{current_day}}</div>{% endif %}
            <a href="{{item.link}}" target="_blank" rel="noopener" class="card">
                <div class="avatar" style="background:{{item.color}}">{{item.letter}}</div>
                <div class="card-body">
                    <div class="meta"><span class="source-name">{{item.source}}</span><span class="time">{{item.time_str}}</span></div>
                    <div class="title">{{item.title}}</div>
                    <div class="snippet">{{item.snippet}}</div>
                </div>
            </a>
        {% endfor %}
    </main>
    {% endif %}
    <script>
        function refresh() {
            const btn = document.querySelector('.refresh-btn svg');
            if(btn) btn.classList.add('spin');
            fetch('/api/refresh').then(() => setTimeout(() => location.reload(), 2000));
        }
    </script>
</body>
</html>
"""

init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
