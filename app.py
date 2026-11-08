import feedparser
import requests
from bs4 import BeautifulSoup
from flask import Flask, render_template_string, redirect
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
    "Mathus": {"color": "#0f9d58", "letter": "M"},
    "МИФИ": {"color": "#4285f4", "letter": "М"},
    "Росатом": {"color": "#f4b400", "letter": "Р"}
}

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
    if title:
        title = title[0].upper() + title[1:]

    if not any(item['link'] == link for item in news_db):
        if not snippet: snippet = "Нажмите, чтобы прочитать статью полностью..."
        
        if not pub_time:
            pub_time = time.time() - (len(news_db) * 600)

        style = SOURCE_STYLES.get(source, {"color": "#8b8b90", "letter": source[0]})
        
        dt_obj = datetime.datetime.fromtimestamp(pub_time)
        today = datetime.date.today()
        if dt_obj.date() == today:
            date_label = "Сегодня"
        elif dt_obj.date() == today - datetime.timedelta(days=1):
            date_label = "Вчера"
        else:
            date_label = dt_obj.strftime("%d %B")
            
        news_db.append({
            'title': title,
            'link': link,
            'source': source,
            'snippet': snippet,
            'color': style["color"],
            'letter': style["letter"],
            'time_str': get_relative_time(pub_time),
            'raw_time': pub_time,
            'date_label': date_label,
            'tag_bg': hex_to_rgba(style["color"], 0.14),
            'tag_text': style["color"]
        })

def get_rss_feed(url, source_name, limit=6):
    try:
        feed = feedparser.parse(url)
        for entry in feed.entries[:limit]:
            summary = entry.get('summary', '')
            snippet = clean_snippet(summary)
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
        blacklist = ['олимпиадам', 'дви мгу', 'telegram', 'на главную', 'физика', 'математика']
        for link in links:
            href = link.get('href')
            text = link.text.strip()
            if not text or len(text) < 5 or text.lower() in blacklist: continue
            if count < 3:
                if href.startswith('/'): href = url + href
                save_to_memory(text, href, source_name, "Олимпиадная задача. Проверьте свои силы!", time.time() - 3600)
                count += 1
    except Exception as e:
        print(f"Ошибка парсинга {source_name}: {e}")

def fetch_all_news():
    global news_db, last_update_time
    news_db.clear()
    get_rss_feed("https://habr.com/ru/rss/best/daily/?fl=ru", "Хабр", limit=12)
    get_rss_feed("https://hackaday.com/feed/", "Hackaday", limit=8)
    scrape_mathus("https://mathus.ru/phys/", "Mathus")
    
    news_db.sort(key=lambda x: x['raw_time'], reverse=True)
    last_update_time = time.time()

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ru" data-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="theme-color" content="#0b0b0d">
    <title>Engineering Feed</title>
    <style>
        :root[data-theme="dark"] {
            --bg-base: #0b0b0d;
            --bg-card: #161619;
            --bg-card-hover: #1a1a1f;
            --bg-card-unread: #18181c;
            --bg-header: rgba(11, 11, 13, 0.75);
            --border: rgba(255, 255, 255, 0.05);
            --border-hover: rgba(255, 255, 255, 0.09);
            --text-main: #f4f4f6;
            --text-muted: #b8b8c0;
            --text-snippet: #9a9aa2;
            --text-time: #7c7c84;
            --accent: #bb86fc;
        }
        :root[data-theme="light"] {
            --bg-base: #f0f2f5;
            --bg-card: #ffffff;
            --bg-card-hover: #f8f9fa;
            --bg-card-unread: #ffffff;
            --bg-header: rgba(255, 255, 255, 0.85);
            --border: rgba(0, 0, 0, 0.06);
            --border-hover: rgba(0, 0, 0, 0.1);
            --text-main: #1c1c1e;
            --text-muted: #6b7280;
            --text-snippet: #4b5563;
            --text-time: #9ca3af;
            --accent: #7c3aed;
        }

        /* Скроллбар */
        ::-webkit-scrollbar { width: 10px !important; }
        ::-webkit-scrollbar-track { background: transparent !important; }
        ::-webkit-scrollbar-thumb { background: #2a2a30 !important; border-radius: 8px !important; }
        ::-webkit-scrollbar-thumb:hover { background: #3a3a40 !important; }

        * { box-sizing: border-box; margin: 0; padding: 0; }
        
        body { 
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; 
            background-color: var(--bg-base); 
            background-image: radial-gradient(900px 420px at 50% -120px, rgba(124,92,246,.07), transparent);
            color: var(--text-main); 
            -webkit-font-smoothing: antialiased; 
            padding-top: 64px; 
        }

        ::selection { background: var(--accent); color: #000; }

        /* Шапка 64px */
        header { 
            position: fixed; top: 0; left: 0; right: 0; height: 64px !important; 
            background: var(--bg-header); backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px); 
            border-bottom: 1px solid var(--border); 
            display: flex; align-items: center; justify-content: center; z-index: 100; 
        }
        .header-content { 
            width: 100%; max-width: 740px !important; display: flex; justify-content: space-between; align-items: center; padding: 0 16px; 
        }
        .logo-wrap { display: flex; align-items: center; gap: 12px; }
        .logo { font-weight: 700; font-size: 16px !important; display: flex; align-items: center; gap: 8px;}
        .unread-badge { background: rgba(187, 134, 252, 0.16); color: var(--accent); font-size: 11px !important; padding: 2px 6px; border-radius: 999px; font-weight: 600; }
        
        .header-actions { display: flex; gap: 8px; align-items: center; }
        .updated-time { font-size: 12px !important; color: var(--text-time); margin-right: 8px; }
        
        .btn-icon { width: 36px !important; height: 36px !important; background: transparent; border: none; color: var(--text-time); cursor: pointer; display: flex; align-items: center; justify-content: center; border-radius: 10px; transition: all 0.2s; }
        .btn-icon:hover { background: rgba(255,255,255,.07); color: var(--text-main); }
        .btn-icon:active { background: rgba(255,255,255,.12); }
        .btn-icon svg { width: 20px !important; height: 20px !important; fill: currentColor; }
        @keyframes spin { 100% { transform: rotate(360deg); } }
        .spin-anim svg { animation: spin 1s linear infinite; }

        /* Поиск и Чипы */
        .controls { max-width: 740px !important; margin: 20px auto 8px; padding: 0 16px; }
        .search-wrapper { position: relative; margin-bottom: 12px; }
        .search-icon { position: absolute; left: 12px; top: 50%; transform: translateY(-50%); width: 18px !important; height: 18px !important; stroke: var(--text-time); fill: none; stroke-width: 2; pointer-events: none; }
        .search-input { width: 100%; height: 44px !important; padding: 0 40px; border-radius: 12px; border: 1px solid var(--border); background: var(--bg-card); color: var(--text-main); font-size: 14px !important; outline: none; transition: all 0.2s; }
        .search-input:focus { border-color: var(--accent); box-shadow: 0 0 0 3px rgba(187, 134, 252, 0.22); }
        
        .kbd-hint { position: absolute; right: 12px; top: 50%; transform: translateY(-50%); font-size: 11px !important; color: var(--text-time); background: rgba(255,255,255,0.06); padding: 2px 6px; border-radius: 4px; border: 1px solid rgba(255,255,255,0.1); pointer-events: none; }
        .clear-btn { position: absolute; right: 12px; top: 50%; transform: translateY(-50%); background: none; border: none; color: var(--text-time); cursor: pointer; display: none; align-items: center; justify-content: center; }
        
        .chips-container { position: relative; }
        .chips-container::after { content: ''; position: absolute; right: 0; top: 0; bottom: 8px; width: 40px; background: linear-gradient(to right, transparent, var(--bg-base)); pointer-events: none; }
        .chips-row { display: flex; gap: 8px; overflow-x: auto; padding-bottom: 8px; scrollbar-width: none; }
        .chips-row::-webkit-scrollbar { display: none; }
        .chip { height: 32px !important; background: transparent; border: 1px solid rgba(255,255,255,.1); color: var(--text-muted); padding: 0 12px; border-radius: 999px; font-size: 13px !important; cursor: pointer; white-space: nowrap; transition: all 0.2s; display: flex; align-items: center; gap: 6px; }
        .chip:hover { background: rgba(255,255,255,.05); }
        .chip.active { background: var(--accent); color: #000; border-color: var(--accent); font-weight: 600; box-shadow: 0 2px 8px rgba(187, 134, 252, 0.35); }
        .chip-count { background: rgba(255,255,255,0.1); padding: 2px 6px; border-radius: 10px; font-size: 11px !important; font-weight: 600; }
        
        /* Лента */
        .container { width: 100%; max-width: 740px !important; margin: 0 auto; padding: 0 16px 40px; }
        
        /* Липкие заголовки */
        .day-header { 
            position: sticky; top: 64px !important; z-index: 10; 
            background: var(--bg-header); backdrop-filter: blur(8px); -webkit-backdrop-filter: blur(8px);
            font-size: 12px !important; font-weight: 600; color: var(--text-muted); 
            padding: 12px 0 8px; margin-top: 8px; border-bottom: 1px solid var(--border);
            text-transform: uppercase; letter-spacing: 0.5px;
        }

        /* Карточка (Строго 20-22px, 16px, 12px gap) */
        .card { 
            background-color: var(--bg-card-unread); 
            border-radius: 16px !important; 
            padding: 20px 22px !important; 
            margin-top: 12px !important; 
            border: 1px solid var(--border); 
            transition: all 160ms ease; 
            animation: slideUp 0.18s ease forwards; opacity: 0; transform: translateY(6px); 
            display: flex; gap: 12px !important; position: relative; cursor: pointer; 
        }
        .card:hover { 
            background-color: var(--bg-card-hover); 
            border-color: var(--border-hover); 
            box-shadow: 0 8px 24px rgba(0,0,0,.35); 
            transform: translateY(-1px); 
        }
        .card:active { transform: scale(0.995); }
        
        /* Прочитанное */
        .card.read { opacity: 0.6; background-color: var(--bg-card); }
        .card.read .card-title { color: #8a8a92 !important; }
        .card.read .avatar { opacity: 0.85; }
        .card.read .unread-dot { display: none !important; }

        @keyframes slideUp { to { opacity: 1; transform: translateY(0); } }
        
        .avatar-wrap { position: relative; flex-shrink: 0; }
        .avatar { width: 42px !important; height: 42px !important; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-weight: 700; color: #000; font-size: 16px !important; }
        .unread-dot { position: absolute; top: -2px; right: -2px; width: 9px !important; height: 9px !important; background: var(--accent); border-radius: 50%; box-shadow: 0 0 0 2px var(--bg-card-unread); }
        
        .card-body { flex-grow: 1; min-width: 0; }
        .meta-info { display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; }
        .source-name { font-weight: 600; font-size: 13px !important; color: var(--text-muted); }
        .time { font-size: 12px !important; color: var(--text-time); font-variant-numeric: tabular-nums; }
        
        .card-title { font-size: 17px !important; font-weight: 600; line-height: 1.35 !important; margin-bottom: 6px; display: block; color: var(--text-main); text-decoration: none; transition: color 0.15s; }
        .card-title:hover { color: var(--accent); }
        
        .card-snippet { font-size: 14px !important; color: var(--text-snippet); line-height: 1.55 !important; margin-bottom: 12px; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
        
        .card-footer { display: flex; justify-content: space-between; align-items: center; margin-top: 8px; }
        .tag { font-size: 11px !important; font-weight: 600; padding: 3px 8px; border-radius: 6px; cursor: pointer; transition: opacity 0.2s; }
        .tag:hover { opacity: 0.8; }
        
        .actions-group { display: flex; gap: 4px; }
        .action-btn { background: none; border: none; color: var(--text-time); cursor: pointer; display: flex; align-items: center; gap: 4px; font-size: 12px !important; transition: color 0.2s; padding: 4px 8px; border-radius: 6px; }
        .action-btn:hover { background: rgba(255,255,255,.06); color: var(--text-main); }
        .action-btn svg { width: 14px !important; height: 14px !important; fill: none; stroke: currentColor; stroke-width: 2; }
        
        /* Активная закладка */
        .action-btn.active { color: var(--accent) !important; }
        .action-btn.active svg { fill: var(--accent) !important; stroke: var(--accent) !important; }
        .action-btn.active span { color: var(--accent) !important; }

        /* Тост и Скелетоны */
        .toast { position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%) translateY(100px); background: var(--bg-card); border: 1px solid var(--border); padding: 12px 24px; border-radius: 12px; font-size: 14px; font-weight: 500; box-shadow: 0 8px 32px rgba(0,0,0,0.5); transition: transform 0.3s ease; z-index: 1000; display: flex; align-items: center; gap: 12px; }
        .toast.show { transform: translateX(-50%) translateY(0); }
        
        .skeleton { background: linear-gradient(90deg, #161619 25%, #1f1f23 50%, #161619 75%); background-size: 200% 100%; animation: shimmer 1.5s infinite; border-radius: 16px; height: 140px; margin-top: 12px; border: 1px solid var(--border); }
        @keyframes shimmer { 0% { background-position: 200% 0; } 100% { background-position: -200% 0; } }
    </style>
</head>
<body>
    <header>
        <div class="header-content">
            <div class="logo-wrap">
                <div class="logo">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="var(--accent)" stroke="none"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>
                    <span>EngFeed</span>
                </div>
                <span class="unread-badge" id="unread-counter">0</span>
            </div>
            <div class="header-actions">
                <span class="updated-time" id="updated-time">обновлено в {{ update_time }}</span>
                <button class="btn-icon" id="theme-toggle" title="Сменить тему">
                    <svg viewBox="0 0 24 24"><path d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z" stroke="currentColor" fill="none" stroke-width="2"/></svg>
                </button>
                <button class="btn-icon" id="refresh-btn" onclick="refreshFeed()" title="Обновить ленту">
                    <svg viewBox="0 0 24 24"><path d="M21 2v6h-6M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" stroke="currentColor" fill="none" stroke-width="2"/></svg>
                </button>
            </div>
        </div>
    </header>

    <div class="controls">
        <div class="search-wrapper">
            <svg class="search-icon" viewBox="0 0 24 24"><circle cx="11" cy="11" r="8"></circle><path d="m21 21-4.35-4.35"></path></svg>
            <input type="text" id="search-input" class="search-input" placeholder="Поиск по заголовкам...">
            <span class="kbd-hint" id="kbd-hint">/</span>
            <button class="clear-btn" id="clear-btn">
                <svg width="16" height="16" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" fill="none"><path d="M18 6L6 18M6 6l12 12"/></svg>
            </button>
        </div>
        
        <div class="chips-container">
            <div class="chips-row" id="filter-chips">
                <div class="chip active" data-filter="all">Все</div>
                <div class="chip" data-filter="unread">Непрочитанные</div>
                <div class="chip" data-filter="bookmarks">Закладки</div>
            </div>
        </div>
    </div>

    <main class="container" id="feed-container">
        <div id="skeletons" style="display: none;">
            <div class="skeleton"></div>
            <div class="skeleton"></div>
            <div class="skeleton"></div>
        </div>

        <div id="actual-feed">
            {% set ns = namespace(current_date="") %}
            {% for item in news %}
                {% if item.date_label != ns.current_date %}
                    {% set ns.current_date = item.date_label %}
                    <div class="day-header">{{ ns.current_date }}</div>
                {% endif %}
                
                <div class="card" data-link="{{ item.link }}" data-source="{{ item.source }}" style="animation-delay: {{ loop.index0 * 0.03 }}s;">
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
                            <span class="tag" style="background: {{ item.tag_bg }}; color: {{ item.tag_text }};">{{ item.source }}</span>
                            <div class="actions-group">
                                <button class="action-btn copy-btn" data-link="{{ item.link }}" title="Скопировать ссылку">
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
            
            <div id="empty-state" style="display: none; text-align: center; color: var(--text-time); margin-top: 40px;">
                <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="margin-bottom: 16px;"><circle cx="11" cy="11" r="8"></circle><path d="m21 21-4.35-4.35"></path></svg>
                <p>Ничего не нашлось. Попробуйте изменить запрос.</p>
            </div>
        </div>
    </main>

    <div class="toast" id="toast">
        <span id="toast-text">+3 новых поста</span>
        <button style="background: none; border: none; color: var(--accent); cursor: pointer; font-weight: 600;" onclick="document.getElementById('toast').classList.remove('show')">ОК</button>
    </div>

    <script>
        const sourceCounts = {
            {% for source, count in source_counts.items() %}
                "{{ source }}": {{ count }},
            {% endfor %}
        };

        const chipsRow = document.getElementById('filter-chips');
        for (const [source, count] of Object.entries(sourceCounts)) {
            if (count > 0) {
                const chip = document.createElement('div');
                chip.className = 'chip';
                chip.dataset.filter = source;
                chip.innerHTML = `${source} <span class="chip-count">${count}</span>`;
                chipsRow.appendChild(chip);
            }
        }

        function refreshFeed() {
            const btn = document.getElementById('refresh-btn');
            btn.classList.add('spin-anim');
            document.getElementById('actual-feed').style.display = 'none';
            document.getElementById('skeletons').style.display = 'block';
            setTimeout(() => {
                window.location.href = '/refresh?t=' + Date.now();
            }, 800);
        }

        const urlParams = new URLSearchParams(window.location.search);
        if (urlParams.get('new')) {
            document.getElementById('toast-text').innerText = `+${urlParams.get('new')} новых постов`;
            setTimeout(() => document.getElementById('toast').classList.add('show'), 500);
            setTimeout(() => document.getElementById('toast').classList.remove('show'), 3500);
        }

        document.addEventListener('keydown', (e) => {
            if (e.key === '/' && document.activeElement.tagName !== 'INPUT') {
                e.preventDefault();
                document.getElementById('search-input').focus();
            }
        });

        const themeToggle = document.getElementById('theme-toggle');
        const currentTheme = localStorage.getItem('theme') || 'dark';
        document.documentElement.setAttribute('data-theme', currentTheme);
        themeToggle.addEventListener('click', () => {
            const newTheme = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
            document.documentElement.setAttribute('data-theme', newTheme);
            localStorage.setItem('theme', newTheme);
        });

        let readLinks = JSON.parse(localStorage.getItem('readLinks')) || [];
        let bookmarks = JSON.parse(localStorage.getItem('bookmarks')) || [];
        const cards = document.querySelectorAll('.card');
        const unreadCounter = document.getElementById('unread-counter');
        const emptyState = document.getElementById('empty-state');

        function updateUnreadCount() {
            let count = 0;
            cards.forEach(card => { if (!readLinks.includes(card.dataset.link)) count++; });
            unreadCounter.innerText = count;
        }

        function applyState() {
            cards.forEach(card => {
                const link = card.dataset.link;
                if (readLinks.includes(link)) card.classList.add('read');
                
                const btn = card.querySelector('.bookmark-btn');
                if(btn) {
                    const textSpan = btn.querySelector('span');
                    if (bookmarks.includes(link)) {
                        btn.classList.add('active');
                        textSpan.innerText = 'В закладках';
                    } else {
                        btn.classList.remove('active');
                        textSpan.innerText = 'В закладки';
                    }
                }

                card.onclick = (e) => {
                    if (e.target.closest('.bookmark-btn') || e.target.closest('.copy-btn') || e.target.closest('.tag')) return;
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

        document.querySelectorAll('.bookmark-btn').forEach(btn => {
            btn.onclick = (e) => {
                e.stopPropagation();
                const link = btn.dataset.link;
                if (bookmarks.includes(link)) bookmarks = bookmarks.filter(l => l !== link);
                else bookmarks.push(link);
                localStorage.setItem('bookmarks', JSON.stringify(bookmarks));
                applyState();
            };
        });

        document.querySelectorAll('.copy-btn').forEach(btn => {
            btn.onclick = (e) => {
                e.stopPropagation();
                const link = btn.dataset.link;
                navigator.clipboard.writeText(link).then(() => {
                    const originalSvg = btn.innerHTML;
                    btn.innerHTML = '<svg viewBox="0 0 24 24" stroke="var(--accent)"><path d="M20 6L9 17l-5-5"/></svg>';
                    setTimeout(() => { btn.innerHTML = originalSvg; }, 1500);
                });
            };
        });

        let activeFilter = 'all';
        
        function filterCards() {
            const query = document.getElementById('search-input').value.toLowerCase();
            let visibleCount = 0;

            cards.forEach(card => {
                const title = card.querySelector('.card-title').innerText.toLowerCase();
                const source = card.dataset.source;
                const link = card.dataset.link;
                
                let matchesFilter = false;
                if (activeFilter === 'all') matchesFilter = true;
                else if (activeFilter === 'unread') matchesFilter = !readLinks.includes(link);
                else if (activeFilter === 'bookmarks') matchesFilter = bookmarks.includes(link);
                else matchesFilter = source === activeFilter;

                const matchesSearch = title.includes(query);
                const isVisible = (matchesFilter && matchesSearch);
                
                card.style.display = isVisible ? 'flex' : 'none';
                if (isVisible) visibleCount++;
            });

            emptyState.style.display = visibleCount === 0 ? 'block' : 'none';
        }

        document.querySelectorAll('.chip').forEach(chip => {
            chip.onclick = () => {
                document.querySelectorAll('.chip').forEach(c => c.classList.remove('active'));
                chip.classList.add('active');
                activeFilter = chip.dataset.filter;
                filterCards();
            };
        });
        
        document.getElementById('search-input').oninput = filterCards;
        applyState();
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    global news_db, last_update_time
    new_count = 0
    
    if not news_db:
        fetch_all_news()
    elif time.time() - last_update_time > 3600:
        old_links = set(item['link'] for item in news_db)
        fetch_all_news()
        new_count = len([item for item in news_db if item['link'] not in old_links])

    source_counts = Counter(item['source'] for item in news_db)
    update_time = datetime.datetime.now().strftime("%H:%M")
    
    return render_template_string(HTML_TEMPLATE, news=news_db, update_time=update_time, source_counts=source_counts)

@app.route('/refresh')
def refresh():
    global news_db
    old_links = set(item['link'] for item in news_db)
    fetch_all_news()
    new_count = len([item for item in news_db if item['link'] not in old_links])
    return redirect(f'/?new={new_count}')

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
