import feedparser
import requests
from bs4 import BeautifulSoup
from flask import Flask, render_template_string, jsonify, request
import os
import re
import time
import datetime
import html
import sqlite3
import threading
import logging
import sys
import hashlib
import json
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from cachetools import TTLCache
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import signal

# ==========================================
# ЛОГИРОВАНИЕ
# ==========================================
logger = logging.getLogger("engfeed")
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S"
))
logger.addHandler(handler)
logger.setLevel(logging.INFO)

# ==========================================
# КОНФИГУРАЦИЯ
# ==========================================
DB_PATH = os.environ.get("DB_PATH", "engfeed.db")
CACHE_TTL = int(os.environ.get("CACHE_TTL", 15 * 60))  # 15 минут
PORT = int(os.environ.get("PORT", 5000))
DEBUG = os.environ.get("FLASK_DEBUG", "0") == "1"

# Кэш в памяти
feed_cache = TTLCache(maxsize=1, ttl=CACHE_TTL)
cache_lock = threading.Lock()

# HTTP сессия с retry
def make_session():
    s = requests.Session()
    retries = Retry(
        total=3,
        backoff_factor=1.0,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retries)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    s.headers.update({
        "User-Agent": "EngFeed/1.0 (Engineering Feed Aggregator)",
        "Accept": "application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.8"
    })
    return s

session = make_session()

# ==========================================
# ИСТОЧНИКИ (RSS ФИДЫ)
# ==========================================
FEEDS = [
    # Программирование
    {"url": "https://habr.com/ru/rss/best/daily/?fl=ru", "source": "Хабр", "limit": 10, "category": "programming"},
    {"url": "https://blog.rust-lang.org/feed.xml", "source": "Rust Blog", "limit": 3, "category": "programming"},
    {"url": "https://blog.python.org/feeds/posts/default?alt=rss", "source": "Python Insider", "limit": 3, "category": "programming"},
    {"url": "https://hnrss.org/frontpage", "source": "Hacker News", "limit": 8, "category": "programming"},
    
    # Инженерия
    {"url": "https://hackaday.com/feed/", "source": "Hackaday", "limit": 5, "category": "engineering"},
    {"url": "https://spectrum.ieee.org/feeds/feed.rss", "source": "IEEE Spectrum", "limit": 5, "category": "engineering"},
    
    # Физика (англ)
    {"url": "https://www.quantamagazine.org/feed/", "source": "Quanta Magazine", "limit": 8, "category": "physics"},
    {"url": "http://export.arxiv.org/rss/quant-ph", "source": "arXiv:quant-ph", "limit": 5, "category": "physics"},
    {"url": "http://feeds.nature.com/nphys/rss/current", "source": "Nature Physics", "limit": 5, "category": "physics"},
    {"url": "https://physicsworld.com/feed/", "source": "Physics World", "limit": 5, "category": "physics"},
    
    # Наука (рус)
    {"url": "https://elementy.ru/rss/news", "source": "Элементы", "limit": 8, "category": "science"},
    {"url": "https://nplus1.ru/rss", "source": "N+1", "limit": 6, "category": "science"},
]

# Кастомные стили для известных источников
SOURCE_STYLES = {
    "Хабр": {"color": "#5f9df9", "letter": "Х"},
    "Hackaday": {"color": "#ef4444", "letter": "H"},
    "Quanta Magazine": {"color": "#a855f7", "letter": "Q"},
    "Элементы": {"color": "#22c55e", "letter": "Э"},
    "N+1": {"color": "#3b82f6", "letter": "N"},
    "arXiv:quant-ph": {"color": "#f59e0b", "letter": "A"},
    "Nature Physics": {"color": "#ec4899", "letter": "NP"},
    "Physics World": {"color": "#14b8a6", "letter": "PW"},
    "IEEE Spectrum": {"color": "#f97316", "letter": "IEEE"},
    "Hacker News": {"color": "#f97316", "letter": "Y"},
    "Rust Blog": {"color": "#ff9060", "letter": "R"},
    "Python Insider": {"color": "#7cb8ea", "letter": "P"},
}

LANG_STYLES = {
    "Rust": {"color": "#ff9060"},
    "Python": {"color": "#7cb8ea"}
}

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
                link TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                source TEXT NOT NULL,
                snippet TEXT,
                color TEXT,
                letter TEXT,
                pub_time REAL,
                day TEXT,
                fetched_at REAL DEFAULT (strftime('%s', 'now'))
            );
            CREATE INDEX IF NOT EXISTS idx_posts_source ON posts(source);
            CREATE INDEX IF NOT EXISTS idx_posts_pub_time ON posts(pub_time DESC);
        """)
    logger.info("Database initialized")

def save_posts_to_db(posts):
    with get_db() as conn:
        for post in posts:
            try:
                conn.execute("""
                    INSERT OR REPLACE INTO posts 
                    (link, title, source, snippet, color, letter, pub_time, day, fetched_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    post['link'], post['title'], post['source'], post['snippet'],
                    post['color'], post['letter'], post['pub_time'], post['day'],
                    time.time()
                ))
            except Exception as e:
                logger.error(f"Failed to save post {post.get('link')}: {e}")

def load_posts_from_db(source_filter=None, limit=100):
    with get_db() as conn:
        if source_filter and source_filter != 'all':
            cursor = conn.execute(
                "SELECT * FROM posts WHERE source = ? ORDER BY pub_time DESC LIMIT ?",
                (source_filter, limit)
            )
        else:
            cursor = conn.execute(
                "SELECT * FROM posts ORDER BY pub_time DESC LIMIT ?",
                (limit,)
            )
        return [dict(row) for row in cursor.fetchall()]

# ==========================================
# УТИЛИТЫ
# ==========================================
def color_from_name(name: str) -> str:
    """Генерирует детерминированный цвет из имени источника."""
    h = int(hashlib.md5(name.encode()).hexdigest()[:8], 16) % 360
    return f"hsl({h}, 65%, 60%)"

def clean_text(raw_html: str) -> str:
    """Очистка HTML и нормализация текста."""
    if not raw_html:
        return ""
    # Декодируем HTML-сущности
    text = html.unescape(raw_html)
    # Парсим и извлекаем текст
    soup = BeautifulSoup(text, "html.parser")
    text = soup.get_text(" ", strip=True)
    # Пробел после точки перед заглавной
    text = re.sub(r"([.!?])([А-ЯA-Z])", r"\1 \2", text)
    # Склейки с тире
    text = re.sub(r"([А-Яа-яA-Za-z]):—", r"\1: —", text)
    # Множественные пробелы
    text = re.sub(r"\s+", " ", text).strip()
    return text

def smart_snippet(html_text: str, max_chars: int = 240) -> str:
    """Умное извлечение сниппета из HTML."""
    if not html_text:
        return ""
    soup = BeautifulSoup(html_text, "html.parser")
    # Убираем картинки, видео, код
    for tag in soup.find_all(["img", "iframe", "video", "code", "pre", "figure"]):
        tag.decompose()
    paragraphs = soup.find_all("p")
    text = " ".join(p.get_text(" ", strip=True) for p in paragraphs) or soup.get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(" ", 1)[0] + "…"

def pluralize_time(minutes: int, hours: int, days: int) -> str:
    """Правильное склонение временных единиц."""
    if days > 0:
        if days % 10 == 1 and days % 100 != 11:
            return f"{days} день назад"
        elif days % 10 in [2, 3, 4] and days % 100 not in [12, 13, 14]:
            return f"{days} дня назад"
        else:
            return f"{days} дней назад"
    elif hours > 0:
        if hours % 10 == 1 and hours % 100 != 11:
            return f"{hours} час назад"
        elif hours % 10 in [2, 3, 4] and hours % 100 not in [12, 13, 14]:
            return f"{hours} часа назад"
        else:
            return f"{hours} часов назад"
    else:
        if minutes % 10 == 1 and minutes % 100 != 11:
            return f"{minutes} минуту назад"
        elif minutes % 10 in [2, 3, 4] and minutes % 100 not in [12, 13, 14]:
            return f"{minutes} минуты назад"
        else:
            return f"{minutes} минут назад"

def get_relative_time(ts: float) -> str:
    """Человекочитаемое время."""
    diff = time.time() - ts
    if diff < 60:
        return "Только что"
    return pluralize_time(
        int(diff // 60) % 60,
        int(diff // 3600) % 24,
        int(diff // 86400)
    )

def enrich_post(item: dict) -> dict:
    """Обогащение поста метаданными."""
    style = SOURCE_STYLES.get(item['source'])
    if not style:
        color = color_from_name(item['source'])
        letter = item['source'][0].upper()
    else:
        color, letter = style["color"], style["letter"]
    
    pub_time = item.get('pub_time') or time.time()
    dt = datetime.datetime.fromtimestamp(pub_time)
    today = datetime.date.today()
    
    if dt.date() == today:
        day_label = "Сегодня"
    elif dt.date() == today - datetime.timedelta(days=1):
        day_label = "Вчера"
    elif (today - dt.date()).days < 7:
        day_label = dt.strftime("%A")
    else:
        day_label = dt.strftime("%d %B")
    
    return {
        'title': clean_text(item.get('title', '')),
        'link': item['link'],
        'source': item['source'],
        'snippet': smart_snippet(item.get('snippet', ''), 240),
        'color': color,
        'letter': letter,
        'time': get_relative_time(pub_time),
        'day': day_label,
        'pub_time': pub_time,
    }

# ==========================================
# ПАРСИНГ
# ==========================================
def parse_feed_safe(url: str, source_name: str, limit: int = 10) -> list:
    """Безопасный парсинг одного фида."""
    try:
        resp = session.get(url, timeout=10)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
        items = []
        for entry in feed.entries[:limit]:
            pub_time = None
            if hasattr(entry, 'published_parsed') and entry.published_parsed:
                pub_time = time.mktime(entry.published_parsed)
            items.append({
                'title': entry.get('title', 'Без заголовка'),
                'link': entry.get('link'),
                'source': source_name,
                'snippet': entry.get('summary', ''),
                'pub_time': pub_time,
            })
        logger.info(f"[{source_name}] Parsed {len(items)} items")
        return items
    except Exception as e:
        logger.warning(f"[{source_name}] Failed to parse {url}: {e}")
        return []

def fetch_all_news() -> list:
    """Параллельный сбор всех лент."""
    all_items = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [
            pool.submit(parse_feed_safe, f["url"], f["source"], f["limit"])
            for f in FEEDS
        ]
        for future in futures:
            all_items.extend(future.result())
    
    # Дедупликация и сортировка
    seen = set()
    unique = []
    for item in sorted(all_items, key=lambda x: x.get('pub_time') or 0, reverse=True):
        if item['link'] and item['link'] not in seen:
            seen.add(item['link'])
            unique.append(enrich_post(item))
    
    logger.info(f"Fetched {len(unique)} unique posts from {len(FEEDS)} sources")
    return unique

def refresh_feeds_background():
    """Фоновое обновление лент."""
    def worker():
        try:
            items = fetch_all_news()
            with cache_lock:
                feed_cache["latest"] = items
                feed_cache["last_update"] = time.time()
            save_posts_to_db(items)
            logger.info("Feed refresh completed")
        except Exception as e:
            logger.error(f"Feed refresh failed: {e}")
    
    threading.Thread(target=worker, daemon=True).start()

def start_scheduler():
    """Периодическое обновление каждые CACHE_TTL секунд."""
    def loop():
        while True:
            time.sleep(CACHE_TTL)
            logger.info("Scheduled feed refresh")
            refresh_feeds_background()
    
    threading.Thread(target=loop, daemon=True).start()

# ==========================================
# WIKI БАЗА ЗНАНИЙ
# ==========================================
RUST_DB = {
    "ownership": {
        "title": "Владение и заимствование",
        "lang": "Rust",
        "content": """
        <p>Rust — это язык для тех, кто хочет скорости C++ и безопасности Python. Его главная фишка — <b>Ownership (Владение)</b>. В Rust нет сборщика мусора, память очищается детерминированно.</p>
        <h2>Правила владения:</h2>
        <ul>
            <li>У каждого значения есть ровно один владелец.</li>
            <li>В один момент времени может быть только один владелец.</li>
            <li>Когда владелец выходит из области видимости, значение удаляется.</li>
        </ul>
        <pre><code class="language-rust">fn main() {
    let s1 = String::from("hello");
    let s2 = s1; // владение перемещается
    // println!("{}", s1); // ОШИБКА
    println!("{}", s2);
}</code></pre>
        <h2>Заимствование</h2>
        <p>Вместо передачи владения можно <b>заимствовать</b> значение через ссылки:</p>
        <pre><code class="language-rust">fn calculate_length(s: &String) -> usize {
    s.len() // s заимствуется, не владеет
}

fn main() {
    let s1 = String::from("hello");
    let len = calculate_length(&s1);
    println!("Длина '{}' равна {}.", s1, len);
}</code></pre>
        """
    }
}

PYTHON_DB = {
    "generators": {
        "title": "Генераторы и yield",
        "lang": "Python",
        "content": """
        <p>В Python есть мощный инструмент для работы с памятью — генераторы. Обычная функция возвращает результат и завершается. Генератор использует <code>yield</code> и «замораживает» свое состояние.</p>
        <h2>Пример бесконечной последовательности</h2>
        <pre><code class="language-python">def count_up_to(max):
    count = 1
    while count <= max:
        yield count
        count += 1

counter = count_up_to(3)
print(next(counter)) # 1
print(next(counter)) # 2
print(next(counter)) # 3</code></pre>
        <h2>Генераторные выражения</h2>
        <p>Можно создавать генераторы в одну строку:</p>
        <pre><code class="language-python">squares = (x**2 for x in range(10))
print(list(squares)) # [0, 1, 4, 9, 16, 25, 36, 49, 64, 81]

# Память не тратится на весь список сразу
huge_gen = (x for x in range(10**7))
print(next(huge_gen)) # 0</code></pre>
        """
    }
}

# ==========================================
# FLASK ПРИЛОЖЕНИЕ
# ==========================================
app = Flask(__name__)
START_TIME = time.time()

@app.route('/')
def index():
    source_filter = request.args.get('source', 'all')
    news = load_posts_from_db(source_filter=source_filter, limit=100)
    return render_template_string(
        HTML_TEMPLATE,
        news=news,
        active_tab='feed',
        LANG_STYLES=LANG_STYLES,
        source_filter=source_filter
    )

@app.route('/wiki/rust')
def wiki_rust():
    topic = request.args.get('topic', list(RUST_DB.keys())[0])
    return render_template_string(
        HTML_TEMPLATE,
        active_tab='rust',
        db=RUST_DB,
        current_topic=topic,
        news=[],
        LANG_STYLES=LANG_STYLES
    )

@app.route('/wiki/python')
def wiki_python():
    topic = request.args.get('topic', list(PYTHON_DB.keys())[0])
    return render_template_string(
        HTML_TEMPLATE,
        active_tab='python',
        db=PYTHON_DB,
        current_topic=topic,
        news=[],
        LANG_STYLES=LANG_STYLES
    )

@app.route('/api/feed')
def api_feed():
    """JSON-API для ленты."""
    source_filter = request.args.get('source', 'all')
    items = load_posts_from_db(source_filter=source_filter, limit=100)
    return jsonify({
        "items": items,
        "last_update": feed_cache.get("last_update", time.time()),
        "total": len(items),
    })

@app.route('/api/refresh')
def api_refresh():
    """Запуск фонового обновления."""
    refresh_feeds_background()
    return jsonify({"status": "ok", "started_at": time.time()})

@app.route('/api/stats')
def api_stats():
    """Статистика для бейджей."""
    items = load_posts_from_db(limit=500)
    by_source = {}
    for item in items:
        by_source[item['source']] = by_source.get(item['source'], 0) + 1
    return jsonify({"by_source": by_source, "total": len(items)})

@app.route('/health')
def health():
    """Health check для мониторинга."""
    return jsonify({
        "status": "ok",
        "uptime": time.time() - START_TIME,
        "feeds_count": len(FEEDS),
    })

# ==========================================
# HTML ШАБЛОН
# ==========================================
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ru" data-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="theme-color" content="#0b0b0d">
    <title>Engineering Feed</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/themes/prism-tomorrow.min.css">
    <script src="https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/prism.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/components/prism-rust.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/components/prism-python.min.js"></script>
    <script id="MathJax-script" async src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>
    
    <style>
        :root[data-theme="dark"] {
            --bg: #0b0b0d; --surface: #161619; --surface-hover: #1a1a1f; --raised: #1c1c21;
            --border: rgba(255,255,255,.06); --border-strong: rgba(255,255,255,.10);
            --text-1: #f4f4f6; --text-2: #b8b8c0; --text-3: #8b8b93; --text-4: #6c6c74;
            --accent: #8b5cf6; --accent-soft: rgba(139,92,246,.16); --accent-ring: rgba(139,92,246,.25);
            --code-bg: #101013;
        }
        :root[data-theme="light"] {
            --bg: #f4f4f6; --surface: #ffffff; --surface-hover: #f8f8fa; --raised: #ffffff;
            --border: rgba(0,0,0,.08); --border-strong: rgba(0,0,0,.12);
            --text-1: #16161a; --text-2: #4b4b52; --text-3: #7a7a82; --text-4: #9a9aa2;
            --accent: #7c3aed; --accent-soft: rgba(124,58,237,.1); --accent-ring: rgba(124,58,237,.2);
            --code-bg: #f6f6f8;
        }

        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { 
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; 
            background: var(--bg); color: var(--text-1); padding-top: 64px; 
            -webkit-font-smoothing: antialiased;
        }
        ::selection { background: var(--accent-ring); color: var(--text-1); }
        ::-webkit-scrollbar { width: 10px; height: 10px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: #2a2a30; border-radius: 8px; }
        ::-webkit-scrollbar-thumb:hover { background: #3a3a42; }
        :focus-visible { outline: none; box-shadow: 0 0 0 3px var(--accent-ring); }
        
        code, pre { font-family: 'JetBrains Mono', monospace; font-size: 13.5px; line-height: 1.65; }
        pre { background: var(--code-bg) !important; border: 1px solid var(--border); border-radius: 12px; padding: 16px 20px; overflow-x: auto; margin: 16px 0; position: relative; }
        code:not([class*="language-"]) { background: rgba(255,255,255,.08); padding: 2px 6px; border-radius: 6px; font-size: 13px; }

        header { 
            position: fixed; top: 0; left: 0; right: 0; height: 64px; 
            background: rgba(11,11,13,.75); backdrop-filter: blur(16px) saturate(1.4); -webkit-backdrop-filter: blur(16px) saturate(1.4);
            border-bottom: 1px solid var(--border); display: flex; align-items: center; justify-content: center; z-index: 100; 
        }
        [data-theme="light"] header { background: rgba(255,255,255,.75); }
        .header-content { width: 100%; max-width: 1200px; display: flex; justify-content: space-between; align-items: center; padding: 0 24px; }
        
        .logo { font-weight: 700; font-size: 17px; display: flex; align-items: center; gap: 8px; }
        .logo svg { width: 20px; height: 20px; fill: var(--accent); }
        .badge { background: var(--accent-soft); color: #c4b5fd; font-size: 12px; font-weight: 600; padding: 2px 8px; border-radius: 999px; }
        
        .nav-tabs { display: flex; gap: 4px; background: var(--surface); padding: 4px; border-radius: 10px; }
        .nav-tab { color: var(--text-3); padding: 8px 14px; border-radius: 8px; text-decoration: none; font-size: 13px; font-weight: 500; transition: 120ms ease; border: none; background: none; cursor: pointer; }
        .nav-tab:hover { background: rgba(255,255,255,.06); color: var(--text-1); }
        .nav-tab.active { background: var(--accent-soft); color: #c4b5fd; font-weight: 600; }
        
        .actions { display: flex; align-items: center; gap: 12px; }
        .updated-time { font-size: 12px; color: var(--text-3); }
        .icon-btn { width: 36px; height: 36px; border-radius: 10px; border: none; background: none; color: var(--text-3); display: flex; align-items: center; justify-content: center; cursor: pointer; transition: 120ms ease; }
        .icon-btn:hover { background: rgba(255,255,255,.07); color: var(--text-1); }
        .icon-btn:active { background: rgba(255,255,255,.12); }
        .icon-btn svg { width: 20px; height: 20px; stroke: currentColor; fill: none; stroke-width: 2; }
        
        @keyframes spin { 100% { transform: rotate(360deg); } }
        .spin { animation: spin 800ms linear infinite; }

        .container { width: 100%; max-width: 740px; margin: 24px auto; padding: 0 24px; }
        
        .search-wrapper { position: relative; margin-bottom: 16px; }
        .search-wrapper svg { position: absolute; left: 14px; top: 50%; transform: translateY(-50%); width: 18px; height: 18px; stroke: var(--text-4); fill: none; stroke-width: 2; }
        .search-input { width: 100%; height: 44px; padding: 0 40px; border-radius: 12px; border: 1px solid var(--border); background: var(--surface); color: var(--text-1); font-size: 14px; transition: 160ms ease; }
        .search-input:focus { border-color: var(--accent); outline: none; box-shadow: 0 0 0 3px var(--accent-ring); }
        .kbd-hint { position: absolute; right: 14px; top: 50%; transform: translateY(-50%); font-size: 11px; color: var(--text-4); background: rgba(255,255,255,.08); padding: 2px 6px; border-radius: 4px; }
        
        .chips-row { display: flex; gap: 8px; overflow-x: auto; padding-bottom: 16px; scrollbar-width: none; position: relative; mask-image: linear-gradient(to right, black 90%, transparent); }
        .chips-row::-webkit-scrollbar { display: none; }
        .chip { height: 32px; background: transparent; border: 1px solid var(--border); color: var(--text-2); padding: 0 12px; border-radius: 999px; font-size: 13px; font-weight: 500; cursor: pointer; white-space: nowrap; transition: 120ms ease; display: flex; align-items: center; gap: 6px; }
        .chip:hover { background: rgba(255,255,255,.06); }
        .chip.active { background: var(--accent-soft); border-color: var(--accent); color: #c4b5fd; }
        .chip-count { background: rgba(255,255,255,.08); padding: 2px 6px; border-radius: 999px; font-size: 11px; }

        .day-header { font-size: 12px; font-weight: 600; color: var(--text-3); letter-spacing: 0.4px; text-transform: uppercase; margin: 24px 0 12px; position: sticky; top: 64px; background: var(--bg); padding: 8px 0; z-index: 10; }
        
        .card { 
            background: var(--surface); border: 1px solid var(--border); border-radius: 16px; padding: 20px 22px; margin-bottom: 12px; 
            display: flex; gap: 16px; cursor: pointer; transition: 160ms ease; animation: slideUp 240ms ease forwards; opacity: 0; transform: translateY(6px);
        }
        .card:hover { background: var(--surface-hover); border-color: var(--border-strong); box-shadow: 0 8px 24px rgba(0,0,0,.35); transform: translateY(-1px); }
        .card:active { transform: scale(0.995); }
        .card.read { opacity: 0.6; }
        .card.read .card-title { color: var(--text-3); }
        .card.focused { border-color: var(--accent); box-shadow: 0 0 0 2px var(--accent-ring); }
        
        @keyframes slideUp { to { opacity: 1; transform: translateY(0); } }
        
        .avatar-wrap { position: relative; flex-shrink: 0; }
        .avatar { width: 40px; height: 40px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-weight: 700; font-size: 15px; color: #000; }
        .unread-dot { position: absolute; top: -2px; right: -2px; width: 9px; height: 9px; background: var(--accent); border-radius: 50%; border: 2px solid var(--surface); display: none; }
        .card:not(.read) .unread-dot { display: block; }
        
        .card-body { flex-grow: 1; min-width: 0; }
        .meta { display: flex; justify-content: space-between; margin-bottom: 4px; }
        .source { font-weight: 600; font-size: 13px; color: var(--text-2); }
        .time { font-size: 12px; color: var(--text-3); font-variant-numeric: tabular-nums; }
        
        .card-title { font-size: 17px; font-weight: 600; color: var(--text-1); text-decoration: none; line-height: 1.35; display: block; margin-bottom: 6px; transition: 120ms; }
        .card-title:hover { color: var(--accent); }
        .card-snippet { font-size: 14px; line-height: 1.55; color: var(--text-3); display: -webkit-box; -webkit-line-clamp: 3; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
        
        .card-footer { display: flex; justify-content: space-between; align-items: center; margin-top: 12px; }
        .tag { font-size: 12px; font-weight: 500; padding: 2px 8px; border-radius: 6px; }
        .action-group { display: flex; gap: 8px; }
        .action-btn { background: none; border: none; color: var(--text-3); cursor: pointer; display: flex; align-items: center; gap: 4px; font-size: 12px; padding: 4px 8px; border-radius: 6px; transition: 120ms; }
        .action-btn:hover { background: rgba(255,255,255,.06); color: var(--text-1); }
        .action-btn svg { width: 16px; height: 16px; fill: none; stroke: currentColor; stroke-width: 2; }
        .action-btn.active { color: var(--accent); }
        .action-btn.active svg { fill: var(--accent); stroke: var(--accent); }

        .skeleton { background: linear-gradient(90deg, var(--surface) 25%, var(--surface-hover) 50%, var(--surface) 75%); background-size: 200% 100%; animation: shimmer 1.4s infinite; border-radius: 12px; }
        @keyframes shimmer { 0% { background-position: 200% 0; } 100% { background-position: -200% 0; } }
        .skeleton-card { height: 140px; margin-bottom: 12px; }

        .wiki-layout { display: flex; gap: 32px; max-width: 1200px; margin: 40px auto; padding: 0 24px; }
        .wiki-sidebar { width: 260px; flex-shrink: 0; position: sticky; top: 84px; height: max-content; }
        .sidebar-label { font-size: 11px; font-weight: 600; color: var(--text-4); text-transform: uppercase; letter-spacing: 0.8px; margin-bottom: 12px; }
        .wiki-menu { list-style: none; display: flex; flex-direction: column; gap: 2px; }
        .wiki-menu a { display: flex; align-items: center; gap: 8px; padding: 8px 10px; border-radius: 8px; font-size: 13px; font-weight: 500; color: var(--text-2); text-decoration: none; transition: 120ms; }
        .wiki-menu a:hover { background: rgba(255,255,255,.06); color: var(--text-1); }
        .wiki-menu a.active { background: var(--accent-soft); color: #c4b5fd; }
        .lang-dot { width: 8px; height: 8px; border-radius: 50%; }
        
        .wiki-content { flex-grow: 1; max-width: 860px; background: var(--surface); border: 1px solid var(--border); border-radius: 20px; padding: 48px; }
        .breadcrumbs { font-size: 12px; color: var(--text-3); margin-bottom: 24px; display: flex; gap: 8px; align-items: center; }
        .breadcrumbs svg { width: 12px; height: 12px; stroke: var(--text-4); fill: none; stroke-width: 2; }
        
        .wiki-content h1 { font-size: 32px; font-weight: 700; letter-spacing: -0.02em; margin-bottom: 24px; }
        .wiki-content h2 { font-size: 22px; font-weight: 650; margin: 36px 0 16px; }
        .wiki-content p { font-size: 15px; line-height: 1.7; color: var(--text-2); margin-bottom: 16px; }
        .wiki-content strong { color: var(--text-1); }
        .wiki-content ul { margin-left: 24px; margin-bottom: 24px; color: var(--text-2); line-height: 1.7; font-size: 15px; }
        .wiki-content li { margin-bottom: 6px; }
        .wiki-content li::marker { color: var(--accent); }
        
        .cta-btn { display: inline-flex; align-items: center; gap: 10px; height: 44px; padding: 0 20px; border-radius: 12px; text-decoration: none; font-size: 14px; font-weight: 600; margin: 24px 0; border: 1px solid; transition: 160ms; }
        .cta-btn svg { width: 18px; height: 18px; fill: currentColor; }
        .cta-rust { background: rgba(255,144,96,.14); border-color: rgba(255,144,96,.3); color: #ff9060; }
        .cta-rust:hover { background: rgba(255,144,96,.2); }
        .cta-python { background: rgba(124,184,234,.14); border-color: rgba(124,184,234,.3); color: #7cb8ea; }
        .cta-python:hover { background: rgba(124,184,234,.2); }

        pre::-webkit-scrollbar { height: 8px; }
        pre::-webkit-scrollbar-thumb { background: #3a3a42; }

        @media (max-width: 768px) {
            .wiki-layout { flex-direction: column; }
            .wiki-sidebar { width: 100%; position: static; }
            .header-content { flex-direction: column; height: auto; padding: 12px; gap: 12px;}
            body { padding-top: 110px; }
            header { height: auto; }
            .wiki-content { padding: 24px; }
        }
    </style>
</head>
<body>
    <header>
        <div class="header-content">
            <div class="logo">
                <svg viewBox="0 0 24 24"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>
                EngFeed
                <span class="badge" id="unread-badge" style="display:none;">0</span>
            </div>
            
            <div class="nav-tabs">
                <button onclick="window.location.href='/'" class="nav-tab {{ 'active' if active_tab == 'feed' else '' }}">Лента</button>
                <button onclick="window.location.href='/wiki/rust'" class="nav-tab {{ 'active' if active_tab == 'rust' else '' }}">Rust</button>
                <button onclick="window.location.href='/wiki/python'" class="nav-tab {{ 'active' if active_tab == 'python' else '' }}">Python</button>
            </div>
            
            <div class="actions">
                <span class="updated-time" id="update-time">обновлено сейчас</span>
                <button class="icon-btn" id="theme-btn" aria-label="Сменить тему">
                    <svg viewBox="0 0 24 24"><path d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z"/></svg>
                </button>
                {% if active_tab == 'feed' %}
                <button class="icon-btn" id="refresh-btn" onclick="refreshFeed()" aria-label="Обновить">
                    <svg viewBox="0 0 24 24"><path d="M21 2v6h-6M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/></svg>
                </button>
                {% endif %}
            </div>
        </div>
    </header>

    {% if active_tab == 'feed' %}
    <main class="container">
        <div class="search-wrapper">
            <svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="8"></circle><path d="m21 21-4.35-4.35"></path></svg>
            <input type="text" id="search" class="search-input" placeholder="Поиск по заголовкам…">
            <span class="kbd-hint">/</span>
        </div>
        
        <div class="chips-row" id="filters">
            <div class="chip active" data-filter="all">Все</div>
            <div class="chip" data-filter="unread">Непрочитанные</div>
            <div class="chip" data-filter="bookmarks">Закладки</div>
            {% set sources = {} %}
            {% for item in news %}
                {% set _ = sources.update({item.source: (sources.get(item.source, 0) + 1)}) %}
            {% endfor %}
            {% for source, count in sources.items() %}
            <div class="chip" data-filter="{{ source }}">{{ source }} <span class="chip-count">{{ count }}</span></div>
            {% endfor %}
        </div>

        <div id="feed-container">
            {% set ns = namespace(current_day="") %}
            {% for item in news %}
                {% if item.day != ns.current_day %}
                    {% set ns.current_day = item.day %}
                    <div class="day-header">{{ ns.current_day }}</div>
                {% endif %}
                
                <div class="card" data-link="{{ item.link }}" data-source="{{ item.source }}">
                    <div class="avatar-wrap">
                        <div class="avatar" style="background: {{ item.color }}; box-shadow: 0 0 0 1px {{ item.color }}40;">{{ item.letter }}</div>
                        <div class="unread-dot"></div>
                    </div>
                    <div class="card-body">
                        <div class="meta">
                            <span class="source">{{ item.source }}</span>
                            <span class="time">{{ item.time }}</span>
                        </div>
                        <a href="{{ item.link }}" target="_blank" rel="noopener" class="card-title">{{ item.title }}</a>
                        <div class="card-snippet">{{ item.snippet }}</div>
                        
                        <div class="card-footer">
                            <span class="tag" style="color: {{ item.color }}; background: {{ item.color }}20;" onclick="filterTag('{{ item.source }}')">#{{ item.source }}</span>
                            <div class="action-group">
                                <button class="action-btn copy-btn" data-link="{{ item.link }}" onclick="copyLink(this, '{{ item.link }}')">
                                    <svg viewBox="0 0 24 24"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
                                </button>
                                <button class="action-btn bookmark-btn" data-link="{{ item.link }}">
                                    <svg viewBox="0 0 24 24"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"></path></svg>
                                    <span>В закладки</span>
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            {% endfor %}
        </div>
    </main>
    {% endif %}

    {% if active_tab in ['rust', 'python'] %}
    <div class="wiki-layout">
        <aside class="wiki-sidebar">
            <div class="sidebar-label">{{ 'Rust' if active_tab == 'rust' else 'Python' }}</div>
            <ul class="wiki-menu">
                {% for key, item in db.items() %}
                <li>
                    <a href="?topic={{ key }}" class="{{ 'active' if current_topic == key else '' }}">
                        <div class="lang-dot" style="background: {{ LANG_STYLES[item.lang].color }}"></div>
                        {{ item.title }}
                    </a>
                </li>
                {% endfor %}
            </ul>
            <div style="margin-top: 24px;">
                <a href="/" style="font-size: 13px; color: var(--text-3); text-decoration: none; display: flex; align-items: center; gap: 6px;">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 12H5M12 19l-7-7 7-7"/></svg>
                    К ленте
                </a>
            </div>
        </aside>
        
        <main class="wiki-content">
            <div class="breadcrumbs">
                Wiki <svg viewBox="0 0 24 24"><path d="m9 18 6-6-6-6"/></svg> {{ db[current_topic].lang }}
            </div>
            <h1>{{ db[current_topic].title }}</h1>
            
            {% if active_tab == 'rust' %}
            <a href="https://stepik.org/course/195449/promo" target="_blank" class="cta-btn cta-rust">
                <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><path d="M12 8v8M8 12h8"/></svg>
                Пройти интерактивный курс на Stepik
            </a>
            {% else %}
            <a href="https://stepik.org/course/214271/promo" target="_blank" class="cta-btn cta-python">
                <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><path d="M12 8v8M8 12h8"/></svg>
                Пройти интерактивный курс на Stepik
            </a>
            {% endif %}
            
            {{ db[current_topic].content | safe }}
        </main>
    </div>
    {% endif %}

    <script>
        const themeBtn = document.getElementById('theme-btn');
        let currentTheme = localStorage.getItem('theme') || 'dark';
        document.documentElement.setAttribute('data-theme', currentTheme);
        
        themeBtn.addEventListener('click', () => {
            currentTheme = currentTheme === 'dark' ? 'light' : 'dark';
            document.documentElement.setAttribute('data-theme', currentTheme);
            localStorage.setItem('theme', currentTheme);
        });

        let readLinks = JSON.parse(localStorage.getItem('readLinks') || '[]');
        let bookmarks = JSON.parse(localStorage.getItem('bookmarks') || '[]');
        
        function updateBadge() {
            const cards = document.querySelectorAll('.card');
            let unread = 0;
            cards.forEach(c => { if (!readLinks.includes(c.dataset.link)) unread++; });
            const badge = document.getElementById('unread-badge');
            if (badge) {
                badge.innerText = unread;
                badge.style.display = unread > 0 ? 'inline-block' : 'none';
            }
        }

        function applyState() {
            document.querySelectorAll('.card').forEach(card => {
                const link = card.dataset.link;
                if (readLinks.includes(link)) card.classList.add('read');
                
                const btn = card.querySelector('.bookmark-btn');
                if (btn) {
                    const span = btn.querySelector('span');
                    if (bookmarks.includes(link)) {
                        btn.classList.add('active');
                        span.innerText = "В закладках";
                    } else {
                        btn.classList.remove('active');
                        span.innerText = "В закладки";
                    }
                }

                card.onclick = (e) => {
                    if (e.target.closest('.bookmark-btn') || e.target.closest('.copy-btn') || e.target.closest('.tag')) return;
                    if (!readLinks.includes(link)) {
                        readLinks.push(link);
                        localStorage.setItem('readLinks', JSON.stringify(readLinks));
                        applyState();
                        updateBadge();
                    }
                    window.open(link, '_blank');
                };
            });
            updateBadge();
        }

        document.querySelectorAll('.bookmark-btn').forEach(btn => {
            btn.onclick = (e) => {
                e.stopPropagation();
                const link = btn.closest('.card').dataset.link;
                if (bookmarks.includes(link)) bookmarks = bookmarks.filter(l => l !== link);
                else bookmarks.push(link);
                localStorage.setItem('bookmarks', JSON.stringify(bookmarks));
                applyState();
            };
        });

        window.copyLink = function(btn, link) {
            navigator.clipboard.writeText(link).then(() => {
                const original = btn.innerHTML;
                btn.innerHTML = '<svg viewBox="0 0 24 24"><path d="M20 6L9 17l-5-5" stroke="var(--accent)"/></svg>';
                setTimeout(() => btn.innerHTML = original, 1200);
            });
        };

        let activeFilter = 'all';
        const chips = document.querySelectorAll('.chip');
        const searchInput = document.getElementById('search');

        function filterCards() {
            const query = searchInput.value.toLowerCase();
            document.querySelectorAll('.card').forEach(card => {
                const title = card.querySelector('.card-title').innerText.toLowerCase();
                const source = card.dataset.source;
                const link = card.dataset.link;
                
                let matchFilter = (activeFilter === 'all') || 
                                  (activeFilter === 'unread' && !readLinks.includes(link)) ||
                                  (activeFilter === 'bookmarks' && bookmarks.includes(link)) ||
                                  (source === activeFilter);
                                  
                card.style.display = (matchFilter && title.includes(query)) ? 'flex' : 'none';
            });
        }

        chips.forEach(chip => {
            chip.onclick = () => {
                chips.forEach(c => c.classList.remove('active'));
                chip.classList.add('active');
                activeFilter = chip.dataset.filter;
                filterCards();
            };
        });
        if(searchInput) searchInput.oninput = filterCards;

        window.filterTag = function(source) {
            chips.forEach(c => c.classList.remove('active'));
            const chip = document.querySelector(`.chip[data-filter="${source}"]`);
            if (chip) {
                chip.classList.add('active');
                activeFilter = source;
                filterCards();
            }
        };

        window.refreshFeed = async function() {
            const btn = document.getElementById('refresh-btn');
            const svg = btn.querySelector('svg');
            svg.classList.add('spin');
            
            const container = document.getElementById('feed-container');
            container.innerHTML = '<div class="skeleton skeleton-card"></div>'.repeat(3);
            
            try {
                await fetch('/api/refresh');
                setTimeout(async () => {
                    const resp = await fetch('/api/feed');
                    const data = await resp.json();
                    location.reload();
                }, 2000);
            } catch (e) {
                console.error('Refresh failed:', e);
                svg.classList.remove('spin');
            }
        };

        document.querySelectorAll('pre').forEach(pre => {
            const btn = document.createElement('button');
            btn.className = 'icon-btn';
            btn.style.cssText = 'position:absolute; right:8px; top:8px; background:rgba(255,255,255,0.1); width:28px; height:28px; border-radius:6px; color:var(--text-2);';
            btn.innerHTML = '<svg viewBox="0 0 24 24" style="width:14px;height:14px;stroke:currentColor;fill:none;stroke-width:2;"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>';
            btn.onclick = () => {
                navigator.clipboard.writeText(pre.innerText);
                btn.innerHTML = '<svg viewBox="0 0 24 24" style="width:14px;height:14px;stroke:var(--accent);fill:none;stroke-width:2;"><path d="M20 6L9 17l-5-5"/></svg>';
                setTimeout(() => btn.innerHTML = '<svg viewBox="0 0 24 24" style="width:14px;height:14px;stroke:currentColor;fill:none;stroke-width:2;"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>', 1500);
            };
            pre.appendChild(btn);
        });

        document.addEventListener('keydown', (e) => {
            if (e.target.tagName === 'INPUT') return;
            const cards = [...document.querySelectorAll('.card')];
            if (!cards.length) return;
            
            if (e.key === '/') { e.preventDefault(); searchInput.focus(); }
            else if (e.key === 'r') refreshFeed();
        });

        if (document.querySelector('.card')) applyState();
    </script>
</body>
</html>
"""

# ==========================================
# ЗАПУСК
# ==========================================
def graceful_shutdown(signum, frame):
    logger.info("Shutting down gracefully...")
    sys.exit(0)

signal.signal(signal.SIGTERM, graceful_shutdown)
signal.signal(signal.SIGINT, graceful_shutdown)

# Инициализация при загрузке модуля (для gunicorn)
init_db()
logger.info("Database initialized on module load")

# Первое обновление СИНХРОННО (чтобы лента не была пустой при первом запросе)
logger.info("Initial synchronous feed fetch...")
try:
    items = fetch_all_news()
    save_posts_to_db(items)
    with cache_lock:
        feed_cache["latest"] = items
        feed_cache["last_update"] = time.time()
    logger.info(f"Initial fetch done: {len(items)} posts")
except Exception as e:
    logger.error(f"Initial fetch failed: {e}")

# Дальше — фоновый планировщик для периодических обновлений
start_scheduler()

if __name__ == "__main__":
    logger.info(f"Server starting on port {PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=DEBUG)
