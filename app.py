import sqlite3
from flask import Flask, render_template_string

app = Flask(__name__)

# HTML-шаблон с CSS (Темная тема, как у хороших инженерных приложений)
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Engineering Feed</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background-color: #121212;
            color: #e0e0e0;
            margin: 0;
            padding: 20px;
        }
        h1 { text-align: center; color: #bb86fc; }
        .container {
            max-width: 600px;
            margin: 0 auto;
        }
        .card {
            background-color: #1e1e1e;
            border-radius: 10px;
            padding: 15px;
            margin-bottom: 15px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.3);
            border-left: 4px solid #bb86fc;
        }
        .source {
            font-size: 0.8em;
            color: #03dac6;
            text-transform: uppercase;
            font-weight: bold;
            margin-bottom: 5px;
        }
        a {
            color: #e0e0e0;
            text-decoration: none;
            font-size: 1.1em;
            font-weight: 500;
        }
        a:hover { color: #bb86fc; }
    </style>
</head>
<body>
    <div class="container">
        <h1>⚡ Engineering Feed</h1>
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

@app.route('/')
def index():
    # Подключаемся к базе
    conn = sqlite3.connect('engineer_feed.db')
    cursor = conn.cursor()
    # Берем последние 20 записей (сортируем по id по убыванию)
    cursor.execute("SELECT title, link, source FROM news ORDER BY id DESC LIMIT 20")
    news_items = cursor.fetchall()
    conn.close()

    # Передаем данные в HTML-шаблон
    return render_template_string(HTML_TEMPLATE, news=news_items)

# ... (весь код выше остаётся без изменений)

if __name__ == "__main__":
    # Render сам назначит порт и передаст его в переменную окружения PORT
    import os
    port = int(os.environ.get("PORT", 5000))
    # host='0.0.0.0' разрешает подключения извне, а не только с твоего компа
    app.run(host='0.0.0.0', port=port, debug=False) # debug=False обязателен для продакшена