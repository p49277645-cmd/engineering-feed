import feedparser
import requests
from bs4 import BeautifulSoup
from flask import Flask, render_template_string
import os
import time
import re

app = Flask(__name__)

news_db = []
last_update_time = 0

# Генератор цвета аватарки по названию источника
def get_color(source_name):
    colors = ['#bb86fc', '#03dac6', '#cf6679', '#f4b400', '#0f9d58', '#4285f4']
    return colors[sum(ord(c) for c in source_name) % len(colors)]

# Очистка текста от HTML-тегов
def clean_html(raw_html):
    if not raw_html: return ""
    cleanr = re.compile('<.*?>')
    cleantext = re.sub(cleanr, '', raw_html)
    return cleantext[:120] + "..." if len(cleantext) > 120 else cleantext

def save_to_memory(title, link, source, snippet=""):
    if not any(item['link'] == link for item in news_db):
        # Если нет сниппета, ставим заглушку
        if not snippet:
            snippet = "Нажмите, чтобы прочитать статью полностью..."
        news_db.append({
            'title': title, 
            'link': link, 
            'source': source, 
            'snippet': snippet,
            'color': get_color(source),
            'time': 'Только что'
        })

def get_rss_feed(url, source_name):
    try:
        feed = feedparser.parse(url)
        for entry in feed.entries[:3]: # Берем по 3 статьи с источника
            summary = entry.get('summary', '')
            snippet = clean_html(summary)
            save_to_memory(entry.title, entry.link, source_name, snippet)
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
                save_to_memory(text, href, source_name, "Задача по физике. Проверьте свои силы!")
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
    
    # Сортируем новости (хотя бы просто перемешаем для теста, в будущем можно по дате)
    last_update_time = time.time()

# --- НОВЫЙ ДИЗАЙН (HTML + CSS) ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Engineering Feed</title>
    <style>
        :root {
            --bg-base: #0b0b0d;
            --bg-card: #17171a;
            --bg-hover: #1f1f23;
            --border: rgba(255, 255, 255, 0.06);
            --text-main: #f2f2f5;
            --text-muted: #8b8b90;
            --accent: #bb86fc;
        }

        * { box-sizing: border-box; margin: 0; padding: 0; }
        
        body { 
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background-color: var(--bg-base); 
            color: var(--text-main);
            -webkit-font-smoothing: antialiased;
            padding-top: 64px; /* Отступ под фиксированный хедер */
        }

        /* Матовый Хедер */
        header {
            position: fixed;
            top: 0; left: 0; right: 0;
            height: 64px;
            background: rgba(11, 11, 13, 0.8);
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
            border-bottom: 1px solid var(--border);
            display: flex;
            align-items: center;
            justify-content: center;
            z-index: 100;
        }
        
        .header-content {
            width: 100%;
            max-width: 600px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 0 16px;
        }
        
        .logo { font-weight: 700; font-size: 1.2rem; display: flex; align-items: center; gap: 8px;}
        
        /* Кнопка обновления */
        .btn-refresh {
            background: var(--accent);
            color: #000;
            border: none;
            padding: 8px 16px;
            border-radius: 8px;
            font-weight: 600;
            font-size: 0.9rem;
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 6px;
            transition: opacity 0.2s;
            text-decoration: none;
        }
        .btn-refresh:hover { opacity: 0.8; }

        /* Контейнер ленты */
        .container {
            max-width: 600px;
            margin: 0 auto;
            padding: 20px 16px;
        }

        /* Карточка */
        .card {
            background-color: var(--bg-card);
            border-radius: 16px;
            padding: 16px;
            margin-bottom: 16px;
            border: 1px solid var(--border);
            transition: background-color 0.2s ease, transform 0.2s ease;
            animation: slideUp 0.4s ease forwards;
            opacity: 0;
            transform: translateY(15px);
        }
        
        .card:hover {
            background-color: var(--bg-hover);
        }

        /* Анимация появления */
        @keyframes slideUp {
            to { opacity: 1; transform: translateY(0); }
        }

        /* Шапка карточки */
        .card-header {
            display: flex;
            align-items: center;
            gap: 12px;
            margin-bottom: 12px;
        }

        .avatar {
            width: 40px;
            height: 40px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: bold;
            color: #000;
            font-size: 1.2rem;
        }

        .meta-info {
            display: flex;
            flex-direction: column;
        }

        .source-name {
            font-weight: 600;
            font-size: 0.95rem;
            color: var(--text-main);
        }

        .time {
            font-size: 0.8rem;
            color: var(--text-muted);
        }

        /* Контент */
        .card-title {
            font-size: 1.05rem;
            font-weight: 600;
            line-height: 1.4;
            margin-bottom: 8px;
            display: block;
            color: var(--text-main);
            text-decoration: none;
        }
        .card-title:hover { color: var(--accent); }

        .card-snippet {
            font-size: 0.9rem;
            color: var(--text-muted);
            line-height: 1.5;
            margin-bottom: 16px;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }

        /* Панель действий (лайки, комменты) */
        .card-actions {
            display: flex;
            gap: 20px;
            border-top: 1px solid var(--border);
            padding-top: 12px;
        }

        .action-btn {
            display: flex;
            align-items: center;
            gap: 6px;
            background: none;
            border: none;
            color: var(--text-muted);
            font-size: 0.85rem;
            cursor: pointer;
            transition: color 0.15s;
        }
        .action-btn:hover { color: var(--accent); }
        .action-btn svg { width: 18px; height: 18px; fill: currentColor; }
    </style>
</head>
<body>
    <header>
        <div class="header-content">
            <div class="logo">⚡ EngFeed</div>
            <a href="/refresh" class="btn-refresh">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 2v6h-6"></path><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"></path></svg>
                Обновить
            </a>
        </div>
    </header>

    <main class="container">
        {% for item in news %}
        <!-- Задержка анимации зависит от индекса карточки -->
        <div class="card" style="animation-delay: {{ loop.index0 * 0.05 }}s;">
            <div class="card-header">
                <div class="avatar" style="background-color: {{ item.color }};">
                    {{ item.source[0] }}
                </div>
                <div class="meta-info">
                    <span class="source-name">{{ item.source }}</span>
                    <span class="time">{{ item.time }}</span>
                </div>
            </div>
            
            <a href="{{ item.link }}" target="_blank" class="card-title">{{ item.title }}</a>
            <div class="card-snippet">{{ item.snippet }}</div>
            
            <div class="card-actions">
                <!-- Иконки нарисованы через SVG, чтобы не подключать тяжелые библиотеки -->
                <button class="action-btn">
                    <svg viewBox="0 0 24 24"><path d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"/></svg>
                    <span>{{ loop.index * 12 + 5 }}</span>
                </button>
                <button class="action-btn">
                    <svg viewBox="0 0 24 24"><path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm0 14H5.17L4 17.17V4h16v12z"/></svg>
                    <span>{{ loop.index * 3 }}</span>
                </button>
                <button class="action-btn">
                    <svg viewBox="0 0 24 24"><path d="M18 16.08c-.76 0-1.44.3-1.96.77L8.91 12.7c.05-.23.09-.46.09-.7s-.04-.47-.09-.7l7.05-4.11c.54.5 1.25.81 2.04.81 1.66 0 3-1.34 3-3s-1.34-3-3-3-3 1.34-3 3c0 .24.04.47.09.7L8.04 9.81C7.5 9.31 6.79 9 6 9c-1.66 0-3 1.34-3 3s1.34 3 3 3c.79 0 1.5-.31 2.04-.81l7.12 4.16c-.05.21-.08.43-.08.65 0 1.61 1.31 2.92 2.92 2.92 1.61 0 2.92-1.31 2.92-2.92s-1.31-2.92-2.92-2.92z"/></svg>
                </button>
            </div>
        </div>
        {% endfor %}
    </main>
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
