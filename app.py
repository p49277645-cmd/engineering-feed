import feedparser
import requests
from bs4 import BeautifulSoup
from flask import Flask, render_template_string
import os
import time
import re
import datetime

app = Flask(__name__)

news_db = []
last_update_time = 0

# Цвета и иконки для источников (чтобы не было однообразия)
SOURCE_STYLES = {
    "Хабр: Физика": {"color": "#bb86fc", "letter": "Ф"},
    "Хабр: Электроника": {"color": "#03dac6", "letter": "Э"},
    "Хабр: Python": {"color": "#f4b400", "letter": "P"},
    "Хабр: C++": {"color": "#4285f4", "letter": "C"},
    "Hackaday": {"color": "#cf6679", "letter": "H"},
    "Mathus": {"color": "#0f9d58", "letter": "M"}
}

def clean_snippet(raw_html):
    if not raw_html: return ""
    # Удаляем HTML теги
    text = re.sub(r'<.*?>', '', raw_html)
    # ИСПРАВЛЕНИЕ: Добавляем пробел после точки, если его нет (встречать.Представьте -> встречать. Представьте)
    text = re.sub(r'([.!?])([А-ЯA-Z])', r'\1 \2', text)
    text = text.strip()
    return text[:130] + "..." if len(text) > 130 else text

def get_relative_time(ts):
    if not ts: return "Только что"
    now = time.time()
    diff = now - ts
    if diff < 60: return "Только что"
    elif diff < 3600: return f"{int(diff // 60)} мин назад"
    elif diff < 86400: return f"{int(diff // 3600)} ч назад"
    else: 
        dt = datetime.datetime.fromtimestamp(ts)
        return dt.strftime("%d %b, %H:%M")

def save_to_memory(title, link, source, snippet="", pub_time=None):
    if not any(item['link'] == link for item in news_db):
        if not snippet: snippet = "Нажмите, чтобы прочитать статью полностью..."
        
        # Если время публикации не пришло, ставим случайное в пределах 5 часов (для красоты)
        if not pub_time:
            pub_time = time.time() - (len(news_db) * 600)

        style = SOURCE_STYLES.get(source, {"color": "#8b8b90", "letter": source[0]})
        
        news_db.append({
            'title': title, 
            'link': link, 
            'source': source, 
            'snippet': snippet,
            'color': style["color"],
            'letter': style["letter"],
            'time_str': get_relative_time(pub_time)
        })

def get_rss_feed(url, source_name):
    try:
        feed = feedparser.parse(url)
        for entry in feed.entries[:3]:
            summary = entry.get('summary', '')
            snippet = clean_snippet(summary)
            
            # Пытаемся вытащить реальное время публикации
            pub_time = None
            if hasattr(entry, 'published_parsed') and entry.published_parsed:
                pub_time = time.mktime(entry.published_parsed)
                
            save_to_memory(entry.title, entry.link, source_name, snippet, pub_time)
    except Exception as e:
        print(f"Ошибка RSS {source_name}: {e}")

def scrape_mathus(url, source_name):
    try:
        response = requests.get(url, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        links = soup.find_all('a')
        count = 0
        for link in links:
            href = link.get('href')
            text = link.text.strip()
            if text and len(text) > 5 and count < 2: 
                if href.startswith('/'):
                    href = url + href
                save_to_memory(text, href, source_name, "Олимпиадная задача. Проверьте свои силы!", time.time() - 3600)
                count += 1
    except Exception as e:
        print(f"Ошибка парсинга {source_name}: {e}")

def fetch_all_news():
    global news_db, last_update_time
    news_db.clear() 
    get_rss_feed("https://habr.com/ru/rss/hubs/physics/articles/all/?fl=ru", "Хабр: Физика")
    get_rss_feed("https://habr.com/ru/rss/hubs/easyelectronics/articles/all/?fl=ru", "Хабр: Электроника")
    get_rss_feed("https://habr.com/ru/rss/hubs/python/articles/all/?fl=ru", "Хабр: Python")
    get_rss_feed("https://habr.com/ru/rss/hubs/cpp/articles/all/?fl=ru", "Хабр: C++")
    get_rss_feed("https://hackaday.com/feed/", "Hackaday")
    scrape_mathus("https://mathus.ru/phys/", "Mathus")
    
    # Сортируем по времени (чтобы новые были сверху)
    news_db.sort(key=lambda x: x['time_str'])
    last_update_time = time.time()

# --- НОВЫЙ ДИЗАЙН С JS ЛОГИКОЙ ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ru" data-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Engineering Feed</title>
    <style>
        :root[data-theme="dark"] {
            --bg-base: #0b0b0d; --bg-card: #17171a; --bg-hover: #1f1f23;
            --bg-header: rgba(11, 11, 13, 0.8); --border: rgba(255, 255, 255, 0.06);
            --text-main: #f2f2f5; --text-muted: #8b8b90; --accent: #bb86fc;
        }
        :root[data-theme="light"] {
            --bg-base: #f0f2f5; --bg-card: #ffffff; --bg-hover: #f8f9fa;
            --bg-header: rgba(255, 255, 255, 0.85); --border: rgba(0, 0, 0, 0.08);
            --text-main: #1c1c1e; --text-muted: #6b7280; --accent: #7c3aed;
        }

        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { 
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background-color: var(--bg-base); color: var(--text-main);
            -webkit-font-smoothing: antialiased; padding-top: 64px; transition: background 0.3s;
        }

        /* Хедер */
        header {
            position: fixed; top: 0; left: 0; right: 0; height: 64px;
            background: var(--bg-header); backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px);
            border-bottom: 1px solid var(--border); display: flex; align-items: center; justify-content: center;
            z-index: 100; transition: background 0.3s;
        }
        .header-content { width: 100%; max-width: 640px; display: flex; justify-content: space-between; align-items: center; padding: 0 16px; }
        .logo { font-weight: 700; font-size: 1.2rem; display: flex; align-items: center; gap: 8px;}
        .unread-count { background: var(--accent); color: #000; font-size: 0.75rem; padding: 2px 8px; border-radius: 12px; font-weight: bold; }
        
        /* Кнопки в хедере */
        .header-actions { display: flex; gap: 12px; align-items: center; }
        .btn-icon { background: none; border: none; color: var(--text-muted); cursor: pointer; display: flex; align-items: center; justify-content: center; padding: 8px; border-radius: 8px; transition: background 0.2s; }
        .btn-icon:hover { background: var(--border); color: var(--text-main); }
        .btn-icon svg { width: 20px; height: 20px; fill: currentColor; }

        /* Панель поиска и чипы */
        .controls { max-width: 640px; margin: 20px auto 10px; padding: 0 16px; }
        .search-input { width: 100%; padding: 12px 16px; border-radius: 12px; border: 1px solid var(--border); background: var(--bg-card); color: var(--text-main); font-size: 0.95rem; margin-bottom: 12px; outline: none; }
        .search-input:focus { border-color: var(--accent); }
        
        .chips { display: flex; gap: 8px; overflow-x: auto; padding-bottom: 8px; scrollbar-width: none; }
        .chips::-webkit-scrollbar { display: none; }
        .chip { background: var(--bg-card); border: 1px solid var(--border); color: var(--text-muted); padding: 6px 14px; border-radius: 20px; font-size: 0.85rem; cursor: pointer; white-space: nowrap; transition: all 0.2s; }
        .chip:hover, .chip.active { background: var(--accent); color: #000; border-color: var(--accent); font-weight: 600; }

        /* Лента */
        .container { max-width: 640px; margin: 0 auto; padding: 0 16px 40px; }
        .day-group { font-size: 0.85rem; color: var(--text-muted); font-weight: 600; margin: 24px 0 12px; text-transform: uppercase; letter-spacing: 1px; }

        /* Карточка */
        .card {
            background-color: var(--bg-card); border-radius: 16px; padding: 16px; margin-bottom: 12px;
            border: 1px solid var(--border); transition: background-color 0.2s, opacity 0.2s, transform 0.1s;
            animation: slideUp 0.4s ease forwards; opacity: 0; transform: translateY(15px);
            display: flex; gap: 12px; position: relative; cursor: pointer;
        }
        .card:hover { background-color: var(--bg-hover); }
        .card.read { opacity: 0.5; }
        .card.read .card-title { color: var(--text-muted); }

        @keyframes slideUp { to { opacity: 1; transform: translateY(0); } }

        /* Аватарка с индикатором */
        .avatar-wrap { position: relative; flex-shrink: 0; }
        .avatar {
            width: 40px; height: 40px; border-radius: 50%; display: flex; align-items: center; justify-content: center;
            font-weight: 700; color: #000; font-size: 1.1rem;
        }
        .unread-dot { position: absolute; top: -2px; right: -2px; width: 10px; height: 10px; background: var(--accent); border-radius: 50%; border: 2px solid var(--bg-card); display: none; }
        .card:not(.read) .unread-dot { display: block; }

        /* Контент */
        .card-body { flex-grow: 1; }
        .meta-info { display: flex; justify-content: space-between; margin-bottom: 4px; }
        .source-name { font-weight: 600; font-size: 0.9rem; color: var(--text-main); }
        .time { font-size: 0.8rem; color: var(--text-muted); }
        
        .card-title { font-size: 1.05rem; font-weight: 600; line-height: 1.4; margin-bottom: 6px; display: block; }
        .card-snippet { font-size: 0.9rem; color: var(--text-muted); line-height: 1.4; margin-bottom: 12px; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }

        /* Подвал карточки */
        .card-footer { display: flex; justify-content: space-between; align-items: center; border-top: 1px solid var(--border); padding-top: 10px; }
        .tag { font-size: 0.75rem; color: var(--accent); background: rgba(187, 134, 252, 0.1); padding: 2px 8px; border-radius: 6px; }
        
        .action-btn { background: none; border: none; color: var(--text-muted); cursor: pointer; display: flex; align-items: center; gap: 4px; font-size: 0.85rem; transition: color 0.2s; }
        .action-btn:hover { color: var(--text-main); }
        .action-btn svg { width: 16px; height: 16px; fill: none; stroke: currentColor; stroke-width: 2; }
        .action-btn.active { color: var(--accent); }
        .action-btn.active svg { fill: var(--accent); stroke: var(--accent); }

    </style>
</head>
<body>
    <header>
        <div class="header-content">
            <div class="logo">
                <span>⚡ EngFeed</span>
                <span class="unread-count" id="unread-counter">0</span>
            </div>
            <div class="header-actions">
                <button class="btn-icon" id="theme-toggle" title="Сменить тему">
                    <svg viewBox="0 0 24 24"><path d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z"/></svg>
                </button>
                <button class="btn-icon" onclick="window.location.reload()" title="Обновить ленту">
                    <svg viewBox="0 0 24 24"><path d="M21 2v6h-6M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/></svg>
                </button>
            </div>
        </div>
    </header>

    <div class="controls">
        <input type="text" id="search-input" class="search-input" placeholder="Поиск по заголовкам...">
        <div class="chips" id="filter-chips">
            <div class="chip active" data-filter="all">Все</div>
            <div class="chip" data-filter="unread">Непрочитанные</div>
            <div class="chip" data-filter="bookmarks">Закладки</div>
            <div class="chip" data-filter="Хабр">Хабр</div>
            <div class="chip" data-filter="Hackaday">Hackaday</div>
            <div class="chip" data-filter="Mathus">Mathus</div>
        </div>
    </div>

    <main class="container" id="feed-container">
        {% for item in news %}
        <div class="card" data-link="{{ item.link }}" data-source="{{ item.source }}" style="animation-delay: {{ loop.index0 * 0.05 }}s;">
            <div class="avatar-wrap">
                <div class="avatar" style="background-color: {{ item.color }};">{{ item.letter }}</div>
                <div class="unread-dot"></div>
            </div>
            <div class="card-body">
                <div class="meta-info">
                    <span class="source-name">{{ item.source }}</span>
                    <span class="time">{{ item.time_str }}</span>
                </div>
                <a href="{{ item.link }}" target="_blank" class="card-title">{{ item.title }}</a>
                <div class="card-snippet">{{ item.snippet }}</div>
                
                <div class="card-footer">
                    <span class="tag">#{{ item.source.split(': ')[-1] }}</span>
                    <button class="action-btn bookmark-btn" data-link="{{ item.link }}">
                        <svg viewBox="0 0 24 24"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"></path></svg>
                        В закладки
                    </button>
                </div>
            </div>
        </div>
        {% endfor %}
    </main>

    <script>
        // --- 1. ЛОГИКА ТЕМЫ ---
        const themeToggle = document.getElementById('theme-toggle');
        const currentTheme = localStorage.getItem('theme') || 'dark';
        document.documentElement.setAttribute('data-theme', currentTheme);
        
        themeToggle.addEventListener('click', () => {
            const newTheme = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
            document.documentElement.setAttribute('data-theme', newTheme);
            localStorage.setItem('theme', newTheme);
        });

        // --- 2. ЛОГИКА READ/UNREAD И ЗАКЛАДОК ---
        let readLinks = JSON.parse(localStorage.getItem('readLinks')) || [];
        let bookmarks = JSON.parse(localStorage.getItem('bookmarks')) || [];
        
        const cards = document.querySelectorAll('.card');
        const unreadCounter = document.getElementById('unread-counter');

        function updateUnreadCount() {
            let count = 0;
            cards.forEach(card => {
                if (!readLinks.includes(card.dataset.link)) count++;
            });
            unreadCounter.innerText = count;
        }

        function applyState() {
            cards.forEach(card => {
                const link = card.dataset.link;
                
                // Помечаем прочитанным
                if (readLinks.includes(link)) {
                    card.classList.add('read');
                }

                // Закладки
                const btn = card.querySelector('.bookmark-btn');
                if (bookmarks.includes(link)) {
                    btn.classList.add('active');
                    btn.innerHTML = '<svg viewBox="0 0 24 24"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"></path></svg> В закладках';
                } else {
                    btn.classList.remove('active');
                    btn.innerHTML = '<svg viewBox="0 0 24 24"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"></path></svg> В закладки';
                }

                // Клик по карточке (пометить прочитанным и открыть)
                card.onclick = (e) => {
                    if (e.target.closest('.bookmark-btn')) return; // Если кликнули на закладку, не открываем ссылку
                    if (!readLinks.includes(link)) {
                        readLinks.push(link);
                        localStorage.setItem('readLinks', JSON.stringify(readLinks));
                        applyState();
                        updateUnreadCount();
                    }
                    window.open(link, '_blank');
                };
            });
            updateUnreadCount();
        }

        // Обработка клика по закладке
        document.querySelectorAll('.bookmark-btn').forEach(btn => {
            btn.onclick = (e) => {
                e.stopPropagation();
                const link = btn.dataset.link;
                if (bookmarks.includes(link)) {
                    bookmarks = bookmarks.filter(l => l !== link);
                } else {
                    bookmarks.push(link);
                }
                localStorage.setItem('bookmarks', JSON.stringify(bookmarks));
                applyState();
            };
        });

        // --- 3. ФИЛЬТРЫ И ПОИСК ---
        let activeFilter = 'all';
        const searchInput = document.getElementById('search-input');
        const chips = document.querySelectorAll('.chip');

        function filterCards() {
            const query = searchInput.value.toLowerCase();
            
            cards.forEach(card => {
                const title = card.querySelector('.card-title').innerText.toLowerCase();
                const source = card.dataset.source;
                const link = card.dataset.link;
                
                let matchesFilter = false;
                if (activeFilter === 'all') matchesFilter = true;
                else if (activeFilter === 'unread') matchesFilter = !readLinks.includes(link);
                else if (activeFilter === 'bookmarks') matchesFilter = bookmarks.includes(link);
                else matchesFilter = source.includes(activeFilter);

                const matchesSearch = title.includes(query);

                if (matchesFilter && matchesSearch) {
                    card.style.display = 'flex';
                } else {
                    card.style.display = 'none';
                }
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

        searchInput.oninput = filterCards;

        // Инициализация
        applyState();
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    if not news_db or (time.time() - last_update_time > 3600):
        fetch_all_news()
    return render_template_string(HTML_TEMPLATE, news=news_db)

@app.route('/refresh')
def refresh():
    fetch_all_news()
    return index()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
