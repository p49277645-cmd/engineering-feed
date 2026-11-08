import sqlite3
import feedparser
import requests
from bs4 import BeautifulSoup
from flask import Flask, render_template_string
import os

app = Flask(__name__)

# --- БАЗА ДАННЫХ ---
def get_db_connection():
    conn = sqlite3.connect('engineer_feed.db')
    return conn

def setup_database():
    conn = get_db_connection()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT UNIQUE,
            link TEXT UNIQUE,
            source TEXT
        )
    ''')
    conn.commit()
    conn.close()

def save_to_db(title, link, source):
    conn = get_db_connection()
    try:
        conn.execute('INSERT OR IGNORE INTO news (title, link, source) VALUES (?, ?, ?)', (title, link, source))
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()

# --- ПАРСИНГ ---
def get_rss_feed(url, source_name):
    try:
        feed = feedparser.parse(url)
        for entry in feed.entries[:5]: # Берем 5 статей
            save_to_db(entry.title, entry.link, source_name)
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
            if text and len(text) > 5 and count < 3: 
                if href.startswith('/'):
                    href = url + href
                save_to_db(text, href, source_name)
                count += 1
    except Exception as e:
        print(f"Ошибка парсинга {source_name}: {e}")

# --- HTML ШАБЛОН ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Engineering Feed</title>
    <style>
        body { font-family: -apple-system, sans-serif; background-color: #121212; color: #e0e0e0; margin: 0; padding: 20px; }
        h1 { text-align: center; color: #bb86fc; }
        .container { max-width: 600px; margin: 0 auto; }
        .card { background-color: #1e1e1e; border-radius: 10px; padding: 15px; margin-bottom: 15px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); border-left: 4px solid #bb86fc; }
        .source { font-size: 0.8em; color: #03dac6; text-transform: uppercase; font-weight: bold; margin-bottom: 5px; }
        a { color: #e0e0e0; text-decoration: none; font-size: 1.1em; font-weight: 500; }
        a:hover { color: #bb86fc; }
        .btn { display: block; text-align: center; background: #bb86fc; color: #121212; padding: 10px; border-radius: 5px; text-decoration: none; font-weight: bold; margin-bottom: 20px; }
    </style>
</head>
<body>
    <div class="container">
        <h1>⚡ Engineering Feed</h1>
        <a href="/refresh" class="btn">🔄 Обновить ленту</a>
        {% for item in news %}
        <div class="card">
            <div class="source">{{ item[2] }}</div>
            <a href="{{ item[1] }}" target="_blank">{{ item[0] }}</a>
        </div>
        {% endfor %}
    </div>
</body>
</html>
"""

# --- ВЕБ-СЕРВЕР ---
@app.route('/')
def index():
    conn = get_db_connection()
    news_items = conn.execute("SELECT title, link, source FROM news ORDER BY id DESC LIMIT 20").fetchall()
    conn.close()
    
    # Если база пустая (первый запуск в облаке), скачиваем данные
    if not news_items:
        return refresh()
        
    return render_template_string(HTML_TEMPLATE, news=news_items)

@app.route('/refresh')
def refresh():
    setup_database()
    get_rss_feed("https://habr.com/ru/rss/hubs/physics/articles/all/?fl=ru", "Хабр: Физика")
    get_rss_feed("https://hackaday.com/feed/", "Hackaday")
    scrape_mathus("https://mathus.ru/phys/", "Mathus")
    
    conn = get_db_connection()
    news_items = conn.execute("SELECT title, link, source FROM news ORDER BY id DESC LIMIT 20").fetchall()
    conn.close()
    return render_template_string(HTML_TEMPLATE, news=news_items)

# Инициализация базы при старте
setup_database()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
