import feedparser
import requests
from bs4 import BeautifulSoup
from flask import Flask, render_template_string, redirect, request
import os
import time
import re
import datetime
from collections import Counter

app = Flask(__name__)

news_db = []
last_update_time = 0

SOURCE_STYLES = {
    "Хабр": {"color": "#bb86fc", "letter": "Х"},
    "Hackaday": {"color": "#cf6679", "letter": "H"},
    "Mathus": {"color": "#0f9d58", "letter": "M"}
}

# ==========================================
# ФИЗИКА 9 КЛАСС (СО ССЫЛКОЙ НА ПОЛНЫЙ УЧЕБНИК)
# ==========================================
PHYSICS_DB = {
    "intro": {
        "title": "Глава 1. Законы взаимодействия и движения тел",
        "content": """
        <div style="margin-bottom: 24px;">
            <a href="https://kniga-figa.ru/fizika-9-klass-uchebnik-peryshkin-a-v-god-2023/" target="_blank" style="display: inline-flex; align-items: center; gap: 8px; background: rgba(187, 134, 252, 0.1); color: var(--accent); padding: 10px 16px; border-radius: 8px; text-decoration: none; font-weight: 600; font-size: 14px; border: 1px solid rgba(187, 134, 252, 0.2); transition: background 0.2s;">
                📖 Читать полный учебник (Перышкин) онлайн
            </a>
        </div>
        <p>Механическим движением называют изменение с течением времени положения тела относительно других тел. Чтобы описать движение, нам нужно выбрать систему отсчёта — тело отсчёта, связанную с ним систему координат и часы.</p>
        <p>Часто мы можем пренебречь размерами тела по сравнению с расстоянием, которое оно проходит. Такое тело называют <b>материальной точкой</b>. Например, при расчёте движения поезда между городами его длина несущественна, а при посадке в вагон — нет.</p>
        <h4>Основные понятия</h4>
        <ul>
            <li><b>Материальная точка</b> — тело, размерами которого можно пренебречь в данных условиях.</li>
            <li><b>Поступательное движение</b> — движение, при котором все точки тела движутся одинаково.</li>
            <li><b>Система отсчёта</b> (СО) — совокупность тела отсчёта, системы координат и прибора для измерения времени (часов).</li>
        </ul>
        """
    },
    "displacement": {
        "title": "§2-4. Перемещение. Равномерное движение",
        "content": """
        <div style="margin-bottom: 24px;">
            <a href="https://kniga-figa.ru/fizika-9-klass-uchebnik-peryshkin-a-v-god-2023/" target="_blank" style="display: inline-flex; align-items: center; gap: 8px; background: rgba(187, 134, 252, 0.1); color: var(--accent); padding: 10px 16px; border-radius: 8px; text-decoration: none; font-weight: 600; font-size: 14px; border: 1px solid rgba(187, 134, 252, 0.2); transition: background 0.2s;">
                📖 Читать полный учебник (Перышкин) онлайн
            </a>
        </div>
        <p>Путь — это длина траектории. Перемещение — это вектор, соединяющий начальное и конечное положение тела. Модуль перемещения может быть меньше пути (при криволинейном движении) или равен ему (при прямолинейном движении в одну сторону).</p>
        <h4>Прямолинейное равномерное движение</h4>
        <p>Это движение, при котором тело за любые равные промежутки времени совершает одинаковые перемещения. Скорость при таком движении постоянна.</p>
        <div class="formula">\[ v = \\frac{s}{t} = \\text{const} \]</div>
        <p>Координата тела в любой момент времени определяется по формуле:</p>
        <div class="formula">\[ x = x_0 + v_x t \]</div>
        """
    },
    "acceleration": {
        "title": "§5-8. Равноускоренное движение",
        "content": """
        <div style="margin-bottom: 24px;">
            <a href="https://kniga-figa.ru/fizika-9-klass-uchebnik-peryshkin-a-v-god-2023/" target="_blank" style="display: inline-flex; align-items: center; gap: 8px; background: rgba(187, 134, 252, 0.1); color: var(--accent); padding: 10px 16px; border-radius: 8px; text-decoration: none; font-weight: 600; font-size: 14px; border: 1px solid rgba(187, 134, 252, 0.2); transition: background 0.2s;">
                📖 Читать полный учебник (Перышкин) онлайн
            </a>
        </div>
        <p>Если скорость тела за равные промежутки времени изменяется на одну и ту же величину, движение называют <b>равноускоренным</b>. Величина, показывающая, как быстро меняется скорость, называется ускорением.</p>
        <div class="formula">\[ a = \\frac{v - v_0}{t} \]</div>
        <p>При равноускоренном движении скорость тела в любой момент времени:</p>
        <div class="formula">\[ v = v_0 + at \]</div>
        <p>Перемещение при этом можно найти по формуле (площадь под графиком скорости):</p>
        <div class="formula">\[ S = v_0 t + \\frac{at^2}{2} \]</div>
        """
    },
    "newton_laws": {
        "title": "§9-12. Законы Ньютона",
        "content": """
        <div style="margin-bottom: 24px;">
            <a href="https://kniga-figa.ru/fizika-9-klass-uchebnik-peryshkin-a-v-god-2023/" target="_blank" style="display: inline-flex; align-items: center; gap: 8px; background: rgba(187, 134, 252, 0.1); color: var(--accent); padding: 10px 16px; border-radius: 8px; text-decoration: none; font-weight: 600; font-size: 14px; border: 1px solid rgba(187, 134, 252, 0.2); transition: background 0.2s;">
                📖 Читать полный учебник (Перышкин) онлайн
            </a>
        </div>
        <p>Динамика изучает причины изменения движения. Основу составляют три закона Ньютона.</p>
        <h4>Первый закон (Закон инерции)</h4>
        <p>Существуют такие системы отсчёта, относительно которых тело сохраняет состояние покоя или равномерного прямолинейного движения, если на него не действуют другие тела (или их действие скомпенсировано). Такие системы называют <b>инерциальными</b> (ИСО).</p>
        <h4>Второй закон</h4>
        <p>Ускорение тела прямо пропорционально равнодействующей приложенных к нему сил и обратно пропорционально массе тела.</p>
        <div class="formula">\[ \\vec{a} = \\frac{\\vec{F}}{m} \\quad \\text{или} \\quad \\vec{F} = m\\vec{a} \]</div>
        <h4>Третий закон</h4>
        <p>Силы, с которыми два тела действуют друг на друга, равны по модулю и противоположны по направлению. Они приложены к разным телам и не компенсируют друг друга.</p>
        <div class="formula">\[ \\vec{F}_{12} = -\\vec{F}_{21} \]</div>
        """
    }
}

# ==========================================
# RUST (СО ССЫЛКОЙ НА STEPIK)
# ==========================================
RUST_DB = {
    "ownership": {
        "title": "Rust: Владение и заимствование",
        "content": """
        <div style="margin-bottom: 24px;">
            <a href="https://stepik.org/course/195449/promo" target="_blank" style="display: inline-flex; align-items: center; gap: 8px; background: rgba(207, 102, 121, 0.1); color: #cf6679; padding: 10px 16px; border-radius: 8px; text-decoration: none; font-weight: 600; font-size: 14px; border: 1px solid rgba(207, 102, 121, 0.2); transition: background 0.2s;">
                🦀 Пройти интерактивный курс на Stepik
            </a>
        </div>
        <p>Rust — это язык для тех, кто хочет скорости C++ и безопасности Python. Его главная фишка — <b>Ownership (Владение)</b>. В Rust нет сборщика мусора (Garbage Collector), память очищается детерминированно, когда переменная выходит из области видимости.</p>
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
# PYTHON (СО ССЫЛКОЙ НА STEPIK)
# ==========================================
PYTHON_DB = {
    "generators": {
        "title": "Python: Генераторы и yield",
        "content": """
        <div style="margin-bottom: 24px;">
            <a href="https://stepik.org/course/214271/promo" target="_blank" style="display: inline-flex; align-items: center; gap: 8px; background: rgba(244, 180, 0, 0.1); color: #f4b400; padding: 10px 16px; border-radius: 8px; text-decoration: none; font-weight: 600; font-size: 14px; border: 1px solid rgba(244, 180, 0, 0.2); transition: background 0.2s;">
                🐍 Пройти интерактивный курс на Stepik
            </a>
        </div>
        <p>В Python есть мощный инструмент для работы с памятью — генераторы. Обычная функция возвращает результат и завершается. Генератор использует <code>yield</code> и «замораживает» свое состояние, возвращаясь к нему при следующем вызове.</p>
        <pre><code class="language-python">def count_up_to(max):
    count = 1
    while count <= max:
        yield count
        count += 1</code></pre>
        """
    }
}

# ==========================================
# ПАРСИНГ И БАЗА ДАННЫХ
# ==========================================
def hex_to_rgba(hex_color, alpha):
    h = hex_color.lstrip('#')
    return f"rgba({int(h[0:2], 16)}, {int(h[2:4], 16)}, {int(h[4:6], 16)}, {alpha})"

def clean_snippet(raw_html):
    if not raw_html: return ""
    text = re.sub(r'<.*?>', '', raw_html)
    text = re.sub(r'([.!?])([А-ЯA-Z])', r'\1 \2', text)
    text = text.strip()
    return text[:140] + "..." if len(text) > 140 else text

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
    title = title.strip()
    if title: title = title[0].upper() + title[1:]

    if not any(item['link'] == link for item in news_db):
        if not snippet: snippet = "Нажмите, чтобы прочитать статью полностью..."
        if not pub_time: pub_time = time.time() - (len(news_db) * 600)

        style = SOURCE_STYLES.get(source, {"color": "#8b8b90", "letter": source[0]})
        dt_obj = datetime.datetime.fromtimestamp(pub_time)
        today = datetime.date.today()
        if dt_obj.date() == today: date_label = "Сегодня"
        elif dt_obj.date() == today - datetime.timedelta(days=1): date_label = "Вчера"
        else: date_label = dt_obj.strftime("%d %B")
            
        news_db.append({
            'title': title, 'link': link, 'source': source, 'snippet': snippet,
            'color': style["color"], 'letter': style["letter"], 'time_str': get_relative_time(pub_time),
            'raw_time': pub_time, 'date_label': date_label,
            'tag_bg': hex_to_rgba(style["color"], 0.14), 'tag_text': style["color"]
        })

def get_rss_feed(url, source_name, limit=6):
    try:
        feed = feedparser.parse(url)
        for entry in feed.entries[:limit]:
            save_to_memory(entry.title, entry.link, source_name, clean_snippet(entry.get('summary', '')), time.mktime(entry.published_parsed) if hasattr(entry, 'published_parsed') else None)
    except Exception as e: print(f"Ошибка RSS {source_name}: {e}")

def scrape_mathus(url, source_name):
    try:
        response = requests.get(url, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        links = soup.find_all('a')
        count = 0
        for link in links:
            href, text = link.get('href'), link.text.strip()
            if not text or len(text) < 5 or text.lower() in ['олимпиадам', 'дви мгу', 'telegram', 'на главную', 'физика']: continue
            if count < 3:
                if href.startswith('/'): href = url + href
                save_to_memory(text, href, source_name, "Олимпиадная задача.", time.time() - 3600)
                count += 1
    except Exception as e: print(f"Ошибка парсинга {source_name}: {e}")

def fetch_all_news():
    global news_db, last_update_time
    news_db.clear()
    get_rss_feed("https://habr.com/ru/rss/best/daily/?fl=ru", "Хабр", limit=12)
    get_rss_feed("https://hackaday.com/feed/", "Hackaday", limit=8)
    scrape_mathus("https://mathus.ru/phys/", "Mathus")
    news_db.sort(key=lambda x: x['raw_time'], reverse=True)
    last_update_time = time.time()

# ==========================================
# ШАБЛОН (ДИЗАЙН И ФУНКЦИОНАЛ)
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
            --bg-base: #0b0b0d; --bg-card: #161619; --bg-hover: #1a1a1f;
            --bg-header: rgba(11, 11, 13, 0.75); --border: rgba(255, 255, 255, 0.05);
            --text-main: #f4f4f6; --text-muted: #b8b8c0; --text-snippet: #9a9aa2;
            --accent: #bb86fc;
        }
        :root[data-theme="light"] {
            --bg-base: #f0f2f5; --bg-card: #ffffff; --bg-hover: #f8f9fa;
            --bg-header: rgba(255, 255, 255, 0.85); --border: rgba(0, 0, 0, 0.06);
            --text-main: #1c1c1e; --text-muted: #6b7280; --text-snippet: #4b5563;
            --accent: #7c3aed;
        }
        ::-webkit-scrollbar { width: 10px !important; }
        ::-webkit-scrollbar-track { background: transparent !important; }
        ::-webkit-scrollbar-thumb { background: #2a2a30 !important; border-radius: 8px !important; }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background-color: var(--bg-base); background-image: radial-gradient(900px 420px at 50% -120px, rgba(124,92,246,.07), transparent); color: var(--text-main); padding-top: 64px; }
        
        header { position: fixed; top: 0; left: 0; right: 0; height: 64px; background: var(--bg-header); backdrop-filter: blur(16px); border-bottom: 1px solid var(--border); display: flex; align-items: center; justify-content: center; z-index: 100; }
        .header-content { width: 100%; max-width: 900px; display: flex; justify-content: space-between; align-items: center; padding: 0 16px; }
        
        .nav-tabs { display: flex; gap: 4px; background: rgba(255,255,255,0.03); padding: 4px; border-radius: 10px; }
        .nav-tab { color: var(--text-muted); padding: 6px 16px; border-radius: 8px; text-decoration: none; font-size: 14px; font-weight: 500; transition: 0.2s; }
        .nav-tab:hover { color: var(--text-main); }
        .nav-tab.active { background: var(--bg-card); color: var(--accent); box-shadow: 0 2px 8px rgba(0,0,0,0.2); }

        .container { width: 100%; max-width: 740px; margin: 20px auto; padding: 0 16px; }
        .card { background-color: var(--bg-card); border-radius: 16px; padding: 20px 22px; margin-top: 12px; border: 1px solid var(--border); transition: all 160ms; display: flex; gap: 12px; cursor: pointer; }
        .card:hover { background-color: var(--bg-hover); border-color: rgba(255,255,255,0.09); box-shadow: 0 8px 24px rgba(0,0,0,.35); transform: translateY(-1px); }
        
        .avatar { width: 42px; height: 42px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-weight: 700; color: #000; font-size: 16px; flex-shrink: 0; }
        .card-body { flex-grow: 1; min-width: 0; }
        .meta-info { display: flex; justify-content: space-between; margin-bottom: 4px; }
        .source-name { font-weight: 600; font-size: 13px; color: var(--text-muted); }
        .time { font-size: 12px; color: var(--text-muted); }
        .card-title { font-size: 17px; font-weight: 600; line-height: 1.35; margin-bottom: 6px; display: block; color: var(--text-main); text-decoration: none; }
        .card-title:hover { color: var(--accent); }
        .card-snippet { font-size: 14px; color: var(--text-snippet); line-height: 1.55; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }

        .wiki-layout { display: flex; gap: 20px; max-width: 900px; margin: 20px auto; padding: 0 16px; }
        .wiki-sidebar { width: 280px; flex-shrink: 0; position: sticky; top: 84px; height: max-content; }
        .wiki-menu { list-style: none; }
        .wiki-menu li a { display: block; padding: 8px 12px; color: var(--text-muted); text-decoration: none; border-radius: 8px; font-size: 13px; margin-bottom: 2px; transition: 0.2s; line-height: 1.4; }
        .wiki-menu li a:hover { background: rgba(255,255,255,0.05); color: var(--text-main); }
        .wiki-menu li a.active { background: rgba(187, 134, 252, 0.1); color: var(--accent); font-weight: 600; }
        
        .wiki-content { flex-grow: 1; background: var(--bg-card); border-radius: 16px; border: 1px solid var(--border); padding: 32px; }
        .wiki-content h1 { font-size: 24px; margin-bottom: 24px; color: var(--text-main); }
        .wiki-content h4 { font-size: 18px; margin: 24px 0 12px; color: var(--text-main); }
        .wiki-content p { font-size: 15px; color: var(--text-snippet); line-height: 1.6; margin-bottom: 16px; }
        .wiki-content ul { margin-left: 20px; margin-bottom: 16px; color: var(--text-snippet); line-height: 1.6; }
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
        {% set ns = namespace(current_date="") %}
        {% for item in news %}
            {% if item.date_label != ns.current_date %}
                {% set ns.current_date = item.date_label %}
                <div style="margin: 20px 0 10px; font-size: 12px; font-weight: 600; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px;">{{ ns.current_date }}</div>
            {% endif %}
            <div class="card" onclick="window.open('{{ item.link }}', '_blank')">
                <div class="avatar" style="background-color: {{ item.color }};">{{ item.letter }}</div>
                <div class="card-body">
                    <div class="meta-info">
                        <span class="source-name">{{ item.source }}</span>
                        <span class="time">{{ item.time_str }}</span>
                    </div>
                    <a href="{{ item.link }}" target="_blank" class="card-title">{{ item.title }}</a>
                    <div class="card-snippet">{{ item.snippet }}</div>
                </div>
            </div>
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

# ==========================================
# РОУТИНГ
# ==========================================
@app.route('/')
def index():
    global news_db, last_update_time
    if not news_db:
        fetch_all_news()
    return render_template_string(HTML_TEMPLATE, news=news_db, active_tab='feed', update_time="", source_counts={})

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
