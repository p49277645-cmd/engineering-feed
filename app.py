import feedparser
import requests
from bs4 import BeautifulSoup
from flask import Flask, render_template_string
import os
import time

app = Flask(__name__)

# Вместо базы данных используем обычный список в памяти
news_db = []
last_update_time = 0

def save_to_memory(title, link, source):
    # Проверяем, нет ли уже такой ссылки (чтобы не было дублей)
    if not any(item['link'] == link for item in news_db):
        news_db.append({'title': title, 'link': link, 'source': source})

def get_rss_feed(url, source_name):
    try:
        feed = feedparser.parse(url)
        for entry in feed.entries[:4]: # Берем по 4 статьи с каждого источника
            save_to_memory(entry.title, entry.link, source_name)
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
                save_to_memory(text, href, source_name)
                count += 1
    except Exception as e:
        print(f"Ошибка парсинга {source_name}: {e}")

# Функция, которая собирает всё в кучу
def fetch_all_news():
    global news_db, last_update_time
    print("Начинаем обновление ленты...")
    news_db.clear() # Очищаем старые новости перед новой загрузкой
    
    get_rss_feed("https://habr.com/ru/rss/hubs/physics/articles/all/?fl=ru", "Хабр: Физика")
    get_rss_feed("https://habr.com/ru/rss/hubs/easyelectronics/articles/all/?fl=ru", "Хабр: DIY Электроника")
    get_rss_feed("https://habr.com/ru/rss/hubs/python/articles/all/?fl=ru", "Хабр: Python")
    get_rss_feed("https://habr.com/ru/rss/hubs/cpp/articles/all/?fl=ru", "Хабр: C++")
    get_rss_feed("https://hackaday.com/feed/", "Hackaday")
    scrape_mathus("https://mathus.ru/phys/", "Mathus (Физика)")
    
    last_update_time = time.time()
    print("Лента успешно обновлена!")

# --- HTML ШАБЛОН (пока оставляем старый дизайн) ---
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
            <div class="source">{{ item.source }}</div>
            <a href="{{ item.link }}" target="_blank">{{ item.title }}</a>
        </div>
        {% endfor %}
    </div>
</body>
</html>
"""

@app.route('/')
def index():
    # Если лента пуста ИЛИ прошло больше 1 часа (3600 секунд) с последнего обновления
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
