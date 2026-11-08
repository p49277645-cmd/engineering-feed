import feedparser
import requests
from bs4 import BeautifulSoup
from flask import Flask, render_template_string, redirect, request
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

# ==========================================
# БАЗА ЗНАНИЙ: ФИЗИКА
# ==========================================
PHYSICS_DB = {
    "kinematics": {
        "title": "Кинематика: Язык движения",
        "content": """
        <p>Физика начинается там, где мы задаем вопрос: <i>«Где?»</i> и <i>«Когда?»</i>. Кинематика — это искусство описывать движение, не задумываясь о причинах, его вызвавших. Это как смотреть на танец, не зная хореографии.</p>
        <h4>Равноускоренное движение</h4>
        <p>Если скорость тела меняется одинаково за равные промежутки времени, движение называется равноускоренным. Ускорение (\(a\)) — это быстрота изменения скорости. В инженерии это критически важная величина.</p>
        <div class="formula">\[ v = v_0 + at \]</div>
        <div class="formula">\[ S = v_0t + \\frac{at^2}{2} \]</div>
        <p>Геометрический смысл: площадь под графиком скорости — это и есть пройденный путь.</p>
        """
    },
    "dynamics": {
        "title": "Динамика: Архитектура сил",
        "content": """
        <p>Почему тела движутся? Ответ дает динамика. В основе лежат три закона, сформулированные Исааком Ньютоном.</p>
        <h4>Второй закон Ньютона</h4>
        <p>Ускорение прямо пропорционально силе и обратно пропорционально массе. Это главное уравнение механики:</p>
        <div class="formula">\[ \\vec{F} = m\\vec{a} \]</div>
        <p>Чтобы придать массивной ракете такое же ускорение, как легкому велосипеду, нужна колоссальная сила тяги.</p>
        """
    },
    "quantum_atom": {
        "title": "Атом и квантовый мир",
        "content": """
        <p>В начале 20 века физики заглянули в микромир и обнаружили, что классические законы Ньютона там не работают. На смену пришла квантовая физика.</p>
        <h4>Строение атома</h4>
        <p>Модель Резерфорда-Бора: в центре находится тяжелое положительное ядро, а вокруг него движутся электроны.</p>
        <div class="formula">\[ E = mc^2 \]</div>
        <p>Формула Эйнштейна показывает, что масса и энергия — это разные формы одного и того же. Инженеры лазерных установок (твоя будущая профессия!) отлично знают эту физику.</p>
        """
    }
}

# ==========================================
# БАЗА ЗНАНИЙ: RUST (СО ССЫЛКОЙ НА STEPIK)
# ==========================================
RUST_DB = {
    "ownership": {
        "title": "Rust: Владение и заимствование",
        "content": """
        <div style="margin-bottom: 24px;">
            <a href="https://stepik.org/course/195449/promo" target="_blank" style="display: inline-flex; align-items: center; gap: 8px; background: rgba(187, 134, 252, 0.1); color: var(--accent); padding: 10px 16px; border-radius: 8px; text-decoration: none; font-weight: 600; font-size: 14px; border: 1px solid rgba(187, 134, 252, 0.2); transition: background 0.2s;">
                🦀 Пройти интерактивный курс на Stepik
            </a>
        </div>
        <p>Rust — это язык для тех, кто хочет скорости C++ и безопасности Python. Его главная фишка — <b>Ownership (Владение)</b>. В Rust нет сборщика мусора, память очищается детерминированно.</p>
        <h4>Правила владения:</h4>
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
        """
    }
}

# ==========================================
# БАЗА ЗНАНИЙ: PYTHON (СО ССЫЛКОЙ НА STEPIK)
# ==========================================
PYTHON_DB = {
    "generators": {
        "title": "Python: Генераторы и yield",
        "content": """
        <div style="margin-bottom: 24px;">
            <a href="https://stepik.org/course/214271/promo" target="_blank" style="display: inline-flex; align-items: center; gap: 8px; background: rgba(3, 218, 198, 0.1); color: #03dac6; padding: 10px 16px; border-radius: 8px; text-decoration: none; font-weight: 600; font-size: 14px; border: 1px solid rgba(3, 218, 198, 0.2); transition: background 0.2s;">
                🐍 Пройти интерактивный курс на Stepik
            </a>
        </div>
        <p>В Python есть мощный инструмент для работы с памятью — генераторы. Обычная функция возвращает результат и завершается. Генератор использует <code>yield</code> и «замораживает» свое состояние.</p>
        <pre><code class="language-python">def count_up_to(max):
    count = 1
    while count <= max:
        yield count
        count += 1

counter = count_up_to(3)
print(next(counter)) # 1</code></pre>
        """
    }
}

# ==========================================
# ПАРСИНГ И ЛЕНТА
# ==========================================
def clean_snippet(raw_html):
    if not raw_html: return ""
    text = re.sub(r'<.*?>', '', raw_html)
    return text[:140] + "..." if len(text) > 140 else text

def save_to_memory(title, link, source, snippet=""):
    if not any(item['link'] == link for item in news_db):
        style = SOURCE_STYLES.get(source, {"color": "#8b8b90", "letter": source[0]})
        news_db.append({
            'title': title, 'link': link, 'source': source, 'snippet': snippet,
            'color': style["color"], 'letter': style["letter"]
        })

def fetch_all_news():
    global news_db
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

# ==========================================
# ШАБЛОН (ДИЗАЙН)
# ==========================================
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ru" data-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Engineering Feed</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/themes/prism-tomorrow.min.css">
    <script src="https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/prism.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/components/prism-rust.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/components/prism-python.min.js"></script>
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

        .container { width: 100%; max-width: 740px; margin: 20px auto; padding: 0 16px; }
        .card { background-color: var(--bg-card); border-radius: 16px; padding: 20px 22px; margin-top: 12px; border: 1px solid var(--border); transition: all 160ms; display: flex; gap: 12px; cursor: pointer; text-decoration: none; color: inherit; }
        .card:hover { background-color: rgba(255,255,255,0.03); border-color: rgba(255,255,255,0.09); box-shadow: 0 8px 24px rgba(0,0,0,.35); transform: translateY(-1px); }
        
        .avatar { width: 42px; height: 42px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-weight: 700; color: #000; font-size: 16px; flex-shrink: 0; }
        .card-body { flex-grow: 1; min-width: 0; }
        .meta-info { display: flex; justify-content: space-between; margin-bottom: 4px; }
        .source-name { font-weight: 600; font-size: 13px; color: var(--text-muted); }
        .card-title { font-size: 17px; font-weight: 600; line-height: 1.35; margin-bottom: 6px; display: block; color: var(--text-main); }
        .card-snippet { font-size: 14px; color: var(--text-muted); line-height: 1.55; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }

        /* Стили для Wiki */
        .wiki-layout { display: flex; gap: 20px; max-width: 900px; margin: 20px auto; padding: 0 16px; }
        .wiki-sidebar { width: 260px; flex-shrink: 0; position: sticky; top: 84px; height: max-content; }
        .wiki-menu { list-style: none; }
        .wiki-menu li a { display: block; padding: 8px 12px; color: var(--text-muted); text-decoration: none; border-radius: 8px; font-size: 14px; margin-bottom: 2px; transition: 0.2s; }
        .wiki-menu li a:hover { background: rgba(255,255,255,0.05); color: var(--text-main); }
        .wiki-menu li a.active { background: rgba(187, 134, 252, 0.1); color: var(--accent); font-weight: 600; }
        
        .wiki-content { flex-grow: 1; background: var(--bg-card); border-radius: 16px; border: 1px solid var(--border); padding: 32px; }
        .wiki-content h1 { font-size: 28px; margin-bottom: 24px; color: var(--text-main); }
        .wiki-content h4 { font-size: 18px; margin: 24px 0 12px; color: var(--text-main); }
        .wiki-content p { font-size: 15px; color: #a2a2a8; line-height: 1.6; margin-bottom: 16px; }
        .wiki-content ul { margin-left: 20px; margin-bottom: 16px; color: #a2a2a8; line-height: 1.6; }
        .wiki-content pre { border-radius: 12px !important; margin: 16px 0 !important; }
        .formula { background: rgba(255,255,255,0.03); padding: 16px; border-radius: 12px; text-align: center; font-size: 20px; margin: 16px 0; color: var(--accent); border: 1px solid var(--border); overflow-x: auto; }

        @media (max-width: 768px) {
            .wiki-layout { flex-direction: column; }
            .wiki-sidebar { width: 100%; position: static; }
            .header-content { flex-direction: column; height: auto; padding: 12px; gap: 12px;}
            body { padding-top: 110px; }
            header { height: auto; }
        }
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
                <a href="/wiki/physics" class="nav-tab {{ 'active' if active_tab == 'physics' else '' }}">Физика</a>
                <a href="/wiki/rust" class="nav-tab {{ 'active' if active_tab == 'rust' else '' }}">Rust</a>
                <a href="/wiki/python" class="nav-tab {{ 'active' if active_tab == 'python' else '' }}">Python</a>
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

    {% if active_tab in ['physics', 'rust', 'python'] %}
    <div class="wiki-layout">
        <aside class="wiki-sidebar">
            <ul class="wiki-menu">
                {% for key, item in db.items() %}
                <li><a href="?topic={{ key }}" class="{{ 'active' if current_topic == key else '' }}">{{ item.title }}</a></li>
                {% endfor %}
            </ul>
        </aside>
        <main class="wiki-content">
            {% if db[current_topic] %}
                <h1>{{ db[current_topic].title }}</h1>
                {{ db[current_topic].content | safe }}
            {% else %}
                <h1>Выберите тему</h1>
            {% endif %}
        </main>
    </div>
    {% endif %}

    <script>
        const themeToggle = document.getElementById('theme-toggle');
        const currentTheme = localStorage.getItem('theme') || 'dark';
        document.documentElement.setAttribute('data-theme', currentTheme);
        themeToggle.addEventListener('click', () => {
            const newTheme = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
            document.documentElement.setAttribute('data-theme', newTheme);
            localStorage.setItem('theme', newTheme);
            if(window.MathJax) MathJax.typeset();
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

@app.route('/wiki/physics')
def wiki_physics():
    topic = request.args.get('topic', list(PHYSICS_DB.keys())[0])
    return render_template_string(HTML_TEMPLATE, active_tab='physics', db=PHYSICS_DB, current_topic=topic, news=[])

@app.route('/wiki/rust')
def wiki_rust():
    topic = request.args.get('topic', list(RUST_DB.keys())[0])
    return render_template_string(HTML_TEMPLATE, active_tab='rust', db=RUST_DB, current_topic=topic, news=[])

@app.route('/wiki/python')
def wiki_python():
    topic = request.args.get('topic', list(PYTHON_DB.keys())[0])
    return render_template_string(HTML_TEMPLATE, active_tab='python', db=PYTHON_DB, current_topic=topic, news=[])

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
