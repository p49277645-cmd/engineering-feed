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
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from cachetools import TTLCache

import feedparser
import requests
from bs4 import BeautifulSoup
from flask import Flask, render_template_string, jsonify, request

# ==========================================
# 1. ЛОГИРОВАНИЕ И КОНФИГУРАЦИЯ
# ==========================================
logger = logging.getLogger("engfeed")
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S"))
logger.addHandler(handler)
logger.setLevel(logging.INFO)

DB_PATH = os.environ.get("DB_PATH", "engfeed.db")
CACHE_TTL = int(os.environ.get("CACHE_TTL", 20 * 60))  # 20 минут
PORT = int(os.environ.get("PORT", 5000))

feed_cache = TTLCache(maxsize=1, ttl=CACHE_TTL)
cache_lock = threading.Lock()

# Браузерный User-Agent, чтобы сайты не блокировали парсинг
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
})

# ==========================================
# 2. ИСТОЧНИКИ (Гибрид: RSS + Скрейпинг)
# ==========================================
SOURCES = [
    # --- РАБОТАЮЩИЕ RSS ---
    {"type": "rss", "url": "https://elementy.ru/rss/news", "source": "Элементы", "category": "physics", "limit": 8},
    {"type": "rss", "url": "https://nplus1.ru/rss", "source": "N+1", "category": "science", "limit": 6},
    {"type": "rss", "url": "https://habr.com/ru/rss/hub/electronics/daily/?fl=ru", "source": "Хабр: Электроника", "category": "tech", "limit": 6},
    {"type": "rss", "url": "https://habr.com/ru/rss/hub/python/daily/?fl=ru", "source": "Хабр: Python", "category": "code", "limit": 5},
    
    # --- СКРЕЙПИНГ (Ручной парсинг HTML) ---
    {"type": "scrape", "url": "https://mipt.ru/news", "source": "МФТИ", "category": "university", "limit": 6},
    {"type": "scrape", "url": "https://mephi.ru/press-center/news/", "source": "МИФИ", "category": "university", "limit": 6},
    {"type": "scrape", "url": "https://olimpiada.ru/activity/394", "source": "Олимпиада Физтех", "category": "olympiad", "limit": 5},
    {"type": "scrape", "url": "https://olimpiada.ru/activity/5878", "source": "Росатом", "category": "olympiad", "limit": 5},
    {"type": "scrape", "url": "https://mathus.ru/phys/", "source": "Mathus: Физика", "category": "physics", "limit": 5},
]

# Премиальная цветовая палитра
STYLES = {
    "physics": {"color": "#10b981", "letter": "Ф"},    # Emerald
    "science": {"color": "#8b5cf6", "letter": "Н"},    # Violet
    "tech": {"color": "#06b6d4", "letter": "Э"},       # Cyan
    "code": {"color": "#f59e0b", "letter": "К"},       # Amber
    "university": {"color": "#3b82f6", "letter": "В"}, # Blue
    "olympiad": {"color": "#ef4444", "letter": "О"},   # Red
}

# ==========================================
# 3. ДВИЖОК ПАРСИНГА
# ==========================================
def clean_text(text):
    if not text: return ""
    text = html.unescape(text)
    text = re.sub(r'<.*?>', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def parse_rss(item):
    try:
        resp = SESSION.get(item["url"], timeout=10)
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
        logger.info(f"[RSS] {item['source']}: {len(results)} записей")
        return results
    except Exception as e:
        logger.warning(f"[RSS] Ошибка {item['source']}: {e}")
        return []

def scrape_html(item):
    try:
        resp = SESSION.get(item["url"], timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        results = []
        
        # Умные селекторы для разных сайтов
        if "mipt.ru" in item["url"]:
            nodes = soup.select(".news-list__item, .news-item, h3 a")
        elif "mephi.ru" in item["url"]:
            nodes = soup.select(".news-item__title a, .news-list a")
        elif "olimpiada.ru" in item["url"]:
            nodes = soup.select(".news-item__title a, .activity-news a, h3 a")
        elif "mathus.ru" in item["url"]:
            nodes = soup.select("h2 a, h3 a")
        else:
            nodes = soup.find_all("a", href=True)
            
        seen_links = set()
        for node in nodes[:item["limit"] * 3]: # Берем с запасом, фильтруем потом
            href = node.get("href")
            title = clean_text(node.get_text())
            
            # Фильтр мусорных ссылок
            if not title or len(title) < 15: continue
            if any(x in href.lower() for x in ["login", "register", "tag", "category", "javascript"]): continue
            
            # Делаем абсолютный URL
            if href.startswith("/"):
                domain = item["url"].split("/")[2]
                href = f"https://{domain}{href}"
            elif not href.startswith("http"):
                href = item["url"].rstrip("/") + "/" + href.lstrip("/")
                
            if href in seen_links: continue
            seen_links.add(href)
            
            results.append({
                "title": title[:120],
                "link": href,
                "source": item["source"],
                "snippet": "Нажмите, чтобы открыть страницу с материалами, заданиями или новостями.",
                "pub_time": time.time(), # Для скрейпинга время часто недоступно, ставим текущее
                "category": item["category"]
            })
            
            if len(results) >= item["limit"]:
                break
                
        logger.info(f"[Scrape] {item['source']}: {len(results)} записей")
        return results
    except Exception as e:
        logger.warning(f"[Scrape] Ошибка {item['source']}: {e}")
        return []

def fetch_all_data():
    all_items = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(parse_rss if item["type"] == "rss" else scrape_html, item) for item in SOURCES]
        for future in futures:
            all_items.extend(future.result())
    
    # Сортировка по времени (новые сверху)
    all_items.sort(key=lambda x: x["pub_time"], reverse=True)
    
    # Добавляем стили и форматирование времени
    enriched = []
    for item in all_items:
        style = STYLES.get(item["category"], {"color": "#6b7280", "letter": "?"})
        dt = datetime.datetime.fromtimestamp(item["pub_time"])
        day_label = "Сегодня" if dt.date() == datetime.date.today() else "Ранее"
        
        enriched.append({
            **item,
            "color": style["color"],
            "letter": style["letter"],
            "day": day_label,
            "time_str": dt.strftime("%H:%M")
        })
    return enriched

# ==========================================
# 4. БАЗА ДАННЫХ (SQLite)
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

def load_from_db(limit=100):
    with get_db() as conn:
        cursor = conn.execute("SELECT * FROM posts ORDER BY fetched_at DESC LIMIT ?", (limit,))
        return [dict(row) for row in cursor.fetchall()]

# ==========================================
# 5. ФОНОВОЕ ОБНОВЛЕНИЕ
# ==========================================
def background_update():
    def worker():
        try:
            logger.info("Запуск обновления ленты...")
            items = fetch_all_data()
            save_to_db(items)
            with cache_lock:
                feed_cache["data"] = items
                feed_cache["last_update"] = time.time()
            logger.info(f"Обновление завершено. Всего записей: {len(items)}")
        except Exception as e:
            logger.error(f"Критическая ошибка обновления: {e}")
            
    threading.Thread(target=worker, daemon=True).start()

def start_scheduler():
    def loop():
        time.sleep(5) # Даем серверу время на старт
        background_update()
        while True:
            time.sleep(CACHE_TTL)
            background_update()
    threading.Thread(target=loop, daemon=True).start()

# ==========================================
# 6. FLASK ПРИЛОЖЕНИЕ
# ==========================================
app = Flask(__name__)

@app.route('/')
def index():
    # Если кэш пуст, берем из БД. Если и там пусто - показываем заглушку, но запускаем фон.
    with cache_lock:
        data = feed_cache.get("data")
    
    if not data:
        data = load_from_db()
        if not data:
            background_update() # Первый запуск
            
    return render_template_string(HTML_TEMPLATE, news=data or [])

@app.route('/api/refresh')
def api_refresh():
    background_update()
    return jsonify({"status": "ok"})

@app.route('/health')
def health():
    return jsonify({"status": "ok", "sources": len(SOURCES)})

# ==========================================
# 7. ПРЕМИАЛЬНЫЙ HTML/CSS ШАБЛОН
# ==========================================
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ru" data-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="theme-color" content="#09090b">
    <title>EngFeed | Premium</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg: #09090b; --surface: #18181b; --surface-hover: #27272a;
            --border: #27272a; --border-strong: #3f3f46;
            --text-1: #fafafa; --text-2: #a1a1aa; --text-3: #71717a;
            --accent: #8b5cf6; --accent-glow: rgba(139, 92, 246, 0.15);
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: 'Inter', sans-serif; background: var(--bg); color: var(--text-1);
            padding-top: 80px; -webkit-font-smoothing: antialiased;
            background-image: radial-gradient(circle at 50% 0%, rgba(139, 92, 246, 0.08) 0%, transparent 50%);
            background-attachment: fixed;
        }
        
        /* HEADER */
        header {
            position: fixed; top: 0; left: 0; right: 0; height: 70px;
            background: rgba(9, 9, 11, 0.7); backdrop-filter: blur(16px) saturate(1.5);
            border-bottom: 1px solid var(--border); z-index: 100;
            display: flex; align-items: center; justify-content: center;
        }
        .header-inner { width: 100%; max-width: 900px; padding: 0 24px; display: flex; justify-content: space-between; align-items: center; }
        .logo { font-weight: 700; font-size: 18px; letter-spacing: -0.02em; display: flex; align-items: center; gap: 10px; }
        .logo-icon { width: 28px; height: 28px; background: linear-gradient(135deg, #8b5cf6, #06b6d4); border-radius: 8px; display: grid; place-items: center; color: white; font-weight: 800; font-size: 14px; }
        
        .refresh-btn {
            background: var(--surface); border: 1px solid var(--border); color: var(--text-2);
            width: 40px; height: 40px; border-radius: 10px; cursor: pointer; display: grid; place-items: center;
            transition: all 0.2s ease;
        }
        .refresh-btn:hover { background: var(--surface-hover); color: var(--text-1); border-color: var(--border-strong); }
        .refresh-btn svg { width: 18px; height: 18px; stroke: currentColor; fill: none; stroke-width: 2; }
        .spin { animation: spin 1s linear infinite; }
        @keyframes spin { 100% { transform: rotate(360deg); } }

        /* MAIN */
        .container { max-width: 900px; margin: 0 auto; padding: 24px; }
        
        .day-header {
            font-size: 12px; font-weight: 600; color: var(--text-3); text-transform: uppercase; letter-spacing: 0.05em;
            margin: 32px 0 16px; padding-left: 4px; display: flex; align-items: center; gap: 12px;
        }
        .day-header::after { content: ''; flex: 1; height: 1px; background: var(--border); }

        /* CARDS */
        .card {
            background: var(--surface); border: 1px solid var(--border); border-radius: 16px;
            padding: 20px; margin-bottom: 12px; display: flex; gap: 16px;
            transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1); cursor: pointer; text-decoration: none; color: inherit;
            animation: slideUp 0.4s ease forwards; opacity: 0; transform: translateY(10px);
        }
        .card:hover {
            background: var(--surface-hover); border-color: var(--border-strong);
            transform: translateY(-2px); box-shadow: 0 12px 24px -8px rgba(0,0,0,0.4);
        }
        @keyframes slideUp { to { opacity: 1; transform: translateY(0); } }

        .avatar {
            width: 44px; height: 44px; border-radius: 12px; flex-shrink: 0;
            display: grid; place-items: center; font-weight: 700; font-size: 16px; color: #000;
            box-shadow: 0 4px 12px -2px var(--shadow-color, rgba(0,0,0,0.3));
        }
        
        .card-body { flex: 1; min-width: 0; }
        .meta { display: flex; justify-content: space-between; margin-bottom: 6px; font-size: 12px; font-weight: 500; }
        .source-name { color: var(--text-2); }
        .time { color: var(--text-3); font-family: 'JetBrains Mono', monospace; }
        
        .title { font-size: 16px; font-weight: 600; line-height: 1.4; margin-bottom: 8px; color: var(--text-1); }
        .card:hover .title { color: var(--accent); }
        .snippet { font-size: 14px; line-height: 1.6; color: var(--text-2); display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }

        /* EMPTY STATE */
        .empty-state { text-align: center; padding: 60px 20px; color: var(--text-3); }
        .empty-state svg { width: 48px; height: 48px; stroke: var(--text-3); margin-bottom: 16px; opacity: 0.5; }

        /* SCROLLBAR */
        ::-webkit-scrollbar { width: 8px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: #3f3f46; border-radius: 4px; }
        ::-webkit-scrollbar-thumb:hover { background: #52525b; }
    </style>
    <script>
        function refresh() {
            const btn = document.querySelector('.refresh-btn svg');
            btn.classList.add('spin');
            fetch('/api/refresh').then(() => {
                setTimeout(() => location.reload(), 1500);
            });
        }
    </script>
</head>
<body>
    <header>
        <div class="header-inner">
            <div class="logo">
                <div class="logo-icon">EF</div>
                <span>EngFeed</span>
            </div>
            <button class="refresh-btn" onclick="refresh()" title="Обновить ленту">
                <svg viewBox="0 0 24 24"><path d="M21 2v6h-6M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/></svg>
            </button>
        </div>
    </header>

    <main class="container">
        {% if not news %}
        <div class="empty-state">
            <svg viewBox="0 0 24 24" fill="none" stroke-width="1.5"><path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/></svg>
            <p>Загрузка первых данных...<br><small>Это займет около 10-15 секунд. Нажмите кнопку обновления, если долго.</small></p>
        </div>
        {% endif %}

        {% set current_day = "" %}
        {% for item in news %}
            {% if item.day != current_day %}
                {% set current_day = item.day %}
                <div class="day-header">{{ current_day }}</div>
            {% endif %}
            
            <a href="{{ item.link }}" target="_blank" rel="noopener" class="card" style="animation-delay: {{ loop.index0 * 0.05 }}s; --shadow-color: {{ item.color }}40;">
                <div class="avatar" style="background: {{ item.color }};">{{ item.letter }}</div>
                <div class="card-body">
                    <div class="meta">
                        <span class="source-name">{{ item.source }}</span>
                        <span class="time">{{ item.time_str }}</span>
                    </div>
                    <div class="title">{{ item.title }}</div>
                    <div class="snippet">{{ item.snippet }}</div>
                </div>
            </a>
        {% endfor %}
    </main>
</body>
</html>
"""

# ==========================================
# 8. ЗАПУСК
# ==========================================
# Инициализация при импорте (для gunicorn)
init_db()
start_scheduler()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
