import feedparser
import requests
from bs4 import BeautifulSoup
from flask import Flask, render_template_string, redirect, request, send_file
import os
import time
import re
import datetime

app = Flask(__name__)

news_db = []
last_update_time = 0

SOURCE_STYLES = {
    "Хабр": {"color": "#bb86fc", "letter": "Х"},
    "Hackaday": {"color": "#cf6679", "letter": "H"},
    "Mathus": {"color": "#0f9d58", "letter": "M"}
}

def clean_snippet(raw_html):
    if not raw_html: return ""
    text = re.sub(r'<.*?>', '', raw_html)
    text = re.sub(r'([.!?])([А-ЯA-Z])', r'\1 \2', text)
    text = text.strip()
    return text[:140] + "..." if len(text) > 140 else text

def save_to_memory(title, link, source, snippet=""):
    if not any(item['link'] == link for item in news_db):
        style = SOURCE_STYLES.get(source, {"color": "#8b8b90", "letter": source[0]})
        news_db.append({
            'title': title, 'link': link, 'source': source, 'snippet': snippet,
            'color': style["color"], 'letter': style["letter"],
            'time_str': "Только что"
        })

def fetch_all_news():
    global news_db, last_update_time
    news_db.clear()
    try:
        feed = feedparser.parse("https://habr.com/ru/rss/best/daily/?fl=ru")
        for entry in feed.entries[:10]:
            save_to_memory(entry.title, entry.link, "Хабр", clean_snippet(entry.get('summary', '')))
    except Exception as e:
        print(f"RSS Error: {e}")
        
    try:
        feed = feedparser.parse("https://hackaday.com/feed/")
        for entry in feed.entries[:5]:
            save_to_memory(entry.title, entry.link, "Hackaday", clean_snippet(entry.get('summary', '')))
    except Exception as e:
        print(f"RSS Error: {e}")
        
    last_update_time = time.time()

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ru" data-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Engineering Feed</title>
    <script id="MathJax-script" async src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>
    <style>
        :root[data-theme="dark"] {
            --bg-base: #0b0b0d; --bg-card: #161619; --border: rgba(255, 255, 255, 0.05);
            --text-main: #f4f4f6; --text-muted: #b8b8c0; --accent: #bb86fc;
        }
        :root[data-theme="light"] {
            --bg-base: #f0f2f5; --bg-card: #ffffff; --border: rgba(0, 0, 0, 0.06);
            --text-main: #1c1c1e; --text-muted: #6b7280; --accent: #7c3aed;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background-color: var(--bg-base); background-image: radial-gradient(900px 420px at 50% -120px, rgba(124,92,246,.07), transparent); color: var(--text-main); padding-top: 64px; }
        
        header { position: fixed; top: 0; left: 0; right: 0; height: 64px; background: rgba(11, 11, 13, 0.75); backdrop-filter: blur(16px); border-bottom: 1px solid var(--border); display: flex; align-items: center; justify-content: center; z-index: 100; }
        .header-content { width: 100%; max-width: 900px; display: flex; justify-content: space-between; align-items: center; padding: 0 16px; }
        
        .nav-tabs { display: flex; gap: 4px; background: rgba(255,255,255,0.03); padding: 4px; border-radius: 10px; }
        .nav-tab { color: var(--text-muted); padding: 6px 16px; border-radius: 8px; text-decoration: none; font-size: 14px; font-weight: 500; transition: 0.2s; }
        .nav-tab:hover { color: var(--text-main); }
        .nav-tab.active { background: var(--bg-card); color: var(--accent); box-shadow: 0 2px 8px rgba(0,0,0,0.2); }

        .container { width: 100%; max-width: 800px; margin: 20px auto; padding: 0 16px; }
        .card { background-color: var(--bg-card); border-radius: 16px; padding: 20px 22px; margin-top: 12px; border: 1px solid var(--border); transition: all 160ms; display: flex; gap: 12px; cursor: pointer; text-decoration: none; color: inherit; }
        .card:hover { background-color: rgba(255,255,255,0.03); border-color: rgba(255,255,255,0.09); box-shadow: 0 8px 24px rgba(0,0,0,.35); transform: translateY(-1px); }
        
        .avatar { width: 42px; height: 42px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-weight: 700; color: #000; font-size: 16px; flex-shrink: 0; }
        .card-body { flex-grow: 1; min-width: 0; }
        .meta-info { display: flex; justify-content: space-between; margin-bottom: 4px; }
        .source-name { font-weight: 600; font-size: 13px; color: var(--text-muted); }
        .card-title { font-size: 17px; font-weight: 600; line-height: 1.35; margin-bottom: 6px; display: block; color: var(--text-main); }
        .card-snippet { font-size: 14px; color: var(--text-muted); line-height: 1.55; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }

        .book-container { background: var(--bg-card); border-radius: 16px; padding: 40px; border: 1px solid var(--border); margin-top: 20px; }
    </style>
</head>
<body>
    <header>
        <div class="header-content">
            <div class="logo" style="font-weight: 700; font-size: 18px; display: flex; align-items: center; gap: 8px;">
                <svg width="22" height="22" viewBox="0 0 24 24" fill="var(--accent)" stroke="none"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>
                EngFeed
            </div>
            <div class="nav-tabs">
                <a href="/" class="nav-tab {{ 'active' if active_tab == 'feed' else '' }}">Лента</a>
                <a href="/book" class="nav-tab {{ 'active' if active_tab == 'book' else '' }}">Учебник</a>
            </div>
            <div style="display: flex; gap: 8px;">
                <button class="nav-tab" id="theme-toggle" style="background:none; border:none; cursor:pointer; padding: 6px;">🌓</button>
                {% if active_tab == 'feed' %}
                <button class="nav-tab" onclick="window.location.href='/refresh'" style="background:none; border:none; cursor:pointer; padding: 6px;">🔄</button>
                {% endif %}
            </div>
        </div>
    </header>

    {% if active_tab == 'feed' %}
    <main class="container">
        {% for item in news %}
            <a href="{{ item.link }}" target="_blank" class="card">
                <div class="avatar" style="background-color: {{ item.color }};">{{ item.letter }}</div>
                <div class="card-body">
                    <div class="meta-info">
                        <span class="source-name">{{ item.source }}</span>
                    </div>
                    <div class="card-title">{{ item.title }}</div>
                    <div class="card-snippet">{{ item.snippet }}</div>
                </div>
            </a>
        {% endfor %}
    </main>
    {% endif %}

    {% if active_tab == 'book' %}
    <main class="container">
        <div class="book-container">
            {{ book_content | safe }}
        </div>
    </main>
    {% endif %}

    <script>
        const themeToggle = document.getElementById('theme-toggle');
        const currentTheme = localStorage.getItem('theme') || 'dark';
        document.documentElement.setAttribute('data-theme', currentTheme);
        themeToggle.addEventListener('click', () => {
            const newTheme = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
            document.documentElement.setAttribute('data-theme', newTheme);
            localStorage.setItem('theme', newTheme);
        });
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    global news_db
    if not news_db:
        fetch_all_news()
    return render_template_string(HTML_TEMPLATE, news=news_db, active_tab='feed')

@app.route('/refresh')
def refresh():
    fetch_all_news()
    return redirect('/')

@app.route('/book')
def book():
    try:
        # Открываем твой PDF файл напрямую
        return send_file('physics.pdf', mimetype='application/pdf')
    except Exception as e:
        return f"Ошибка: файл physics.pdf не найден в репозитории. {e}"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
