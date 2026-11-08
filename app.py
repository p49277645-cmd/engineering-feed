import feedparser
import requests
from bs4 import BeautifulSoup
from flask import Flask, render_template_string, redirect
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
    "Mathus": {"color": "#0f9d58", "letter": "M"},
    "МИФИ": {"color": "#4285f4", "letter": "М"},
    "Росатом": {"color": "#f4b400", "letter": "Р"}
}

def clean_snippet(raw_html):
    if not raw_html: return ""
    text = re.sub(r'<.*?>', '', raw_html)
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
    # Делаем первую букву заголовка заглавной
    title = title.strip()
    if title:
        title = title[0].upper() + title[1:]

    if not any(item['link'] == link for item in news_db):
        if not snippet: snippet = "Нажмите, чтобы прочитать статью полностью..."
        
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
            'time_str': get_relative_time(pub_time),
            'raw_time': pub_time
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
        # Черный список мусорных ссылок с сайта
        blacklist = ['олимпиадам', 'дви мгу', 'telegram', 'на главную', 'физика', 'математика']
        
        for link in links:
            href = link.get('href')
            text = link.text.strip()
            
            # Пропускаем, если текст короткий или в черном списке
            if not text or len(text) < 5 or text.lower() in blacklist:
                continue
                
            if count < 3:
                if href.startswith('/'):
                    href = url + href
                save_to_memory(text, href, source_name, "Олимпиадная задача. Проверьте свои силы!", time.time() - 3600)
                count += 1
    except Exception as e:
        print(f"Ошибка парсинга {source_name}: {e}")

def fetch_all_news():
    global news_db, last_update_time
    news_db.clear()
    
    get_rss_feed("https://habr.com/ru/rss/best/daily/?fl=ru", "Хабр", limit=10)
    get_rss_feed("https://hackaday.com/feed/", "Hackaday", limit=5)
    
    scrape_mathus("https://mathus.ru/phys/", "Mathus")
    
    news_db.sort(key=lambda x: x['raw_time'], reverse=True)
    last_update_time = time.time()

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
            --bg-header: rgba(11, 11, 13, 0.75); --border: rgba(255, 255, 255, 0.06);
            --text-main: #f2f2f5; --text-muted: #8b8b90; --accent: #bb86fc;
        }
        :root[data-theme="light"] {
            --bg-base: #f0f2f5; --bg-card: #ffffff; --bg-hover: #f8f9fa;
            --bg-header: rgba(255, 255, 255, 0.85); --border: rgba(0, 0, 0, 0.08);
            --text-main: #1c1c1e; --text-muted: #6b7280; --accent: #7c3aed;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background-color: var(--bg-base); color: var(--text-main); -webkit-font-smoothing: antialiased; padding-top: 64px; transition: background 0.3s; }
        
        /* Шапка */
        header { position: fixed; top: 0; left: 0; right: 0; height: 64px; background: var(--bg-header); backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px); border-bottom: 1px solid var(--border); display: flex; align-items: center; justify-content: center; z-index: 100; }
        .header-content { width: 100%; max-width: 720px; display: flex; justify-content: space-between; align-items: center; padding: 0 16px; }
        .logo-wrap { display: flex; flex-direction: column; }
        .logo { font-weight: 700; font-size: 1.1rem; display: flex; align-items: center; gap: 8px;}
        .last-updated { font-size: 0.7rem; color: var(--text-muted); margin-top: 2px; }
        .unread-count { background: rgba(187, 134, 252, 0.16); color: var(--accent); font-size: 0.75rem; padding: 2px 8px; border-radius: 999px; font-weight: 600; }
        
        /* Идеальные кнопки 36x36 */
        .header-actions { display: flex; gap: 8px; align-items: center; }
        .btn-icon { width: 36px; height: 36px; background: transparent; border: none; color: var(--text-muted); cursor: pointer; display: flex; align-items: center; justify-content: center; border-radius: 10px; transition: background 0.2s; }
        .btn-icon:hover { background: rgba(255,255,255,.07); color: var(--text-main); }
        .btn-icon:active { background: rgba(255,255,255,.12); }
        .btn-icon svg { width: 20px; height: 20px; fill: currentColor; }
        @keyframes spin { 100% { transform: rotate(360deg); } }
        .spin-anim svg { animation: spin 1s linear infinite; }

        /* Поиск и Фильтры */
        .controls { max-width: 720px; margin: 16px auto 8px; padding: 0 16px; }
        .search-wrapper { position: relative; margin-bottom: 12px; }
        .search-icon { position: absolute; left: 12px; top: 50%; transform: translateY(-50%); width: 18px; height: 18px; stroke: var(--text-muted); fill: none; stroke-width: 2; pointer-events: none; }
        .search-input { width: 100%; height: 44px; padding: 0 40px; border-radius: 12px; border: 1px solid var(--border); background: var(--bg-card); color: var(--text-main); font-size: 0.95rem; outline: none; transition: border-color 0.2s, box-shadow 0.2s; }
        .search-input:focus { border-color: var(--accent); box-shadow: 0 0 0 3px rgba(187, 134, 252, 0.22); }
        
        /* Kbd-подсказка и крестик */
        .kbd-hint { position: absolute; right: 12px; top: 50%; transform: translateY(-50%); font-size: 0.75rem; color: var(--text-muted); background: rgba(255,255,255,0.06); padding: 2px 6px; border-radius: 4px; border: 1px solid rgba(255,255,255,0.1); pointer-events: none; }
        .clear-btn { position: absolute; right: 12px; top: 50%; transform: translateY(-50%); background: none; border: none; color: var(--text-muted); cursor: pointer; display: none; align-items: center; justify-content: center; }
        .clear-btn:hover { color: var(--text-main); }
        
        /* Чипы с горизонтальным скроллом и градиентом */
        .chips-container { position: relative; }
        .chips-container::after { content: ''; position: absolute; right: 0; top: 0; bottom: 8px; width: 40px; background: linear-gradient(to right, transparent, var(--bg-base)); pointer-events: none; }
        .chips-row { display: flex; gap: 8px; overflow-x: auto; padding-bottom: 8px; scrollbar-width: none; }
        .chips-row::-webkit-scrollbar { display: none; }
        .chip { height: 32px; background: transparent; border: 1px solid rgba(255,255,255,.1); color: #b4b4ba; padding: 0 12px; border-radius: 999px; font-size: 0.8rem; cursor: pointer; white-space: nowrap; transition: all 0.2s; display: flex; align-items: center; }
        .chip:hover { background: rgba(255,255,255,.05); }
        .chip.active { background: var(--accent); color: #000; border-color: var(--accent); font-weight: 600; box-shadow: 0 2px 8px rgba(187, 134, 252, 0.35); }
        
        /* Лента */
        .container { width: 100%; max-width: 720px; margin: 0 auto; padding: 0 16px 40px; }
        .card { background-color: var(--bg-card); border-radius: 16px; padding: 16px; margin-bottom: 12px; border: 1px solid var(--border); transition: background-color 0.2s, opacity 0.2s, transform 0.1s; animation: slideUp 0.18s ease forwards; opacity: 0; transform: translateY(6px); display: flex; gap: 12px; position: relative; cursor: pointer; }
        .card:hover { background-color: var(--bg-hover); box-shadow: 0 6px 20px rgba(0,0,0,.35); }
        .card:active { transform: scale(0.995); }
        
        /* Состояние прочитанного (гасим) */
        .card.read { opacity: 0.5; }
        .card.read .card-title { color: #8f8f96; }
        .card.read .avatar { opacity: 0.85; }
        .card.read .unread-dot { display: none; }
        
        @keyframes slideUp { to { opacity: 1; transform: translateY(0); } }
        
        .avatar-wrap { position: relative; flex-shrink: 0; }
        .avatar { width: 40px; height: 40px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-weight: 700; color: #000; font-size: 1rem; }
        /* Точка с кольцом */
        .unread-dot { position: absolute; top: -2px; right: -2px; width: 9px; height: 9px; background: var(--accent); border-radius: 50%; box-shadow: 0 0 0 2px var(--bg-card); }
        
        .card-body { flex-grow: 1; min-width: 0; }
        .meta-info { display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; }
        .source-name { font-weight: 600; font-size: 0.8rem; color: #e6e6ea; }
        .time { font-size: 0.75rem; color: #8b8b90; font-variant-numeric: tabular-nums; }
        
        .card-title { font-size: 0.95rem; font-weight: 600; line-height: 1.4; margin-bottom: 6px; display: block; color: inherit; text-decoration: none; transition: color 0.15s; }
        .card-title:hover { color: var(--accent); }
        
        /* Сниппет */
        .card-snippet { font-size: 0.85rem; color: #a2a2a8; line-height: 1.5; margin-bottom: 12px; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
        
        /* Футер без разделителя, с отступом */
        .card-footer { display: flex; justify-content: space-between; align-items: center; }
        .tag { font-size: 0.7rem; color: var(--accent); background: rgba(187, 134, 252, 0.1); padding: 3px 8px; border-radius: 6px; cursor: pointer; transition: background 0.2s; }
        .tag:hover { background: rgba(187, 134, 252, 0.2); }
        
        .actions-group { display: flex; gap: 4px; }
        .action-btn { background: none; border: none; color: var(--text-muted); cursor: pointer; display: flex; align-items: center; gap: 4px; font-size: 0.75rem; transition: color 0.2s; padding: 4px 8px; border-radius: 6px; }
        .action-btn:hover { background: rgba(255,255,255,.06); color: var(--text-main); }
        .action-btn svg { width: 14px; height: 14px; fill: none; stroke: currentColor; stroke-width: 2; }
        .action-btn.active { color: var(--accent); }
        .action-btn.active svg { fill: var(--accent); stroke: var(--accent); }

        /* Скрываем стандартные состояния браузера */
        ::selection { background: var(--accent); color: #000; }
    </style>
</head>
<body>
    <header>
        <div class="header-content">
            <div class="logo-wrap">
                <div class="logo">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="var(--accent)" stroke="none"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>
                    <span>EngFeed</span>
                    <span class="unread-count" id="unread-counter">0</span>
                </div>
                <div class="last-updated">обновлено в {{ update_time }}</div>
            </div>
            <div class="header-actions">
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
                <div class="chip" data-filter="Хабр">Хабр</div>
                <div class="chip" data-filter="Hackaday">Hackaday</div>
                <div class="chip" data-filter="Mathus">Mathus</div>
                <div class="chip" data-filter="МИФИ">МИФИ</div>
                <div class="chip" data-filter="Росатом">Росатом</div>
            </div>
        </div>
    </div>

    <main class="container" id="feed-container">
        {% for item in news %}
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
                    <span class="tag">#{{ item.source.split(': ')[-1] }}</span>
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
    </main>

    <script>
        function refreshFeed() {
            const btn = document.getElementById('refresh-btn');
            btn.classList.add('spin-anim');
            setTimeout(() => {
                window.location.href = '/refresh?t=' + Date.now();
            }, 500);
        }

        // Хоткей "/" для фокуса на поиске
        document.addEventListener('keydown', (e) => {
            if (e.key === '/' && document.activeElement.tagName !== 'INPUT') {
                e.preventDefault();
                document.getElementById('search-input').focus();
            }
        });

        // Логика кнопки очистки поиска
        const searchInput = document.getElementById('search-input');
        const clearBtn = document.getElementById('clear-btn');
        const kbdHint = document.getElementById('kbd-hint');

        searchInput.addEventListener('input', () => {
            if (searchInput.value.length > 0) {
                clearBtn.style.display = 'flex';
                kbdHint.style.display = 'none';
            } else {
                clearBtn.style.display = 'none';
                kbdHint.style.display = 'block';
            }
        });

        clearBtn.addEventListener('click', () => {
            searchInput.value = '';
            clearBtn.style.display = 'none';
            kbdHint.style.display = 'block';
            searchInput.focus();
            filterCards(); // Вызываем фильтрацию после очистки
        });

        // Тема
        const themeToggle = document.getElementById('theme-toggle');
        const currentTheme = localStorage.getItem('theme') || 'dark';
        document.documentElement.setAttribute('data-theme', currentTheme);
        themeToggle.addEventListener('click', () => {
            const newTheme = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
            document.documentElement.setAttribute('data-theme', newTheme);
            localStorage.setItem('theme', newTheme);
        });

        // Read/Unread и Закладки
        let readLinks = JSON.parse(localStorage.getItem('readLinks')) || [];
        let bookmarks = JSON.parse(localStorage.getItem('bookmarks')) || [];
        const cards = document.querySelectorAll('.card');
        const unreadCounter = document.getElementById('unread-counter');

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
                const textSpan = btn.querySelector('span');
                if (bookmarks.includes(link)) {
                    btn.classList.add('active');
                    textSpan.innerText = 'В закладках';
                } else {
                    btn.classList.remove('active');
                    textSpan.innerText = 'В закладки';
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

        // Кнопки копирования и закладок
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

        // Теги
        document.querySelectorAll('.tag').forEach(tag => {
            tag.onclick = (e) => {
                e.stopPropagation();
                const sourceName = tag.innerText.replace('#', '');
                const chip = document.querySelector(`.chip[data-filter="${sourceName}"]`);
                if (chip) chip.click();
            };
        });

        // Фильтрация
        let activeFilter = 'all';
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
                card.style.display = (matchesFilter && matchesSearch) ? 'flex' : 'none';
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
    
    update_time = datetime.datetime.now().strftime("%H:%M")
    return render_template_string(HTML_TEMPLATE, news=news_db, update_time=update_time)

@app.route('/refresh')
def refresh():
    fetch_all_news()
    return redirect('/')

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
