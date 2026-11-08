# ⚡ EngFeed — персональная инженерная лента с упором на физику

![Python](https://img.shields.io/badge/python-3.12-blue.svg)
![Flask](https://img.shields.io/badge/Flask-3.0-000?logo=flask&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-green)

Агрегатор RSS: Хабр, Quanta Magazine, arXiv, Nature Physics, Physics World,
Элементы, N+1, IEEE Spectrum, Hackaday и др. + wiki-разделы Rust/Python.

![Feed](assets/feed-dark.png)

## Фичи
- 12 источников, параллельный парсинг, обновление каждые 15 минут
- Read/unread, закладки, фильтры по источникам, живой поиск
- Группировка по дням, тёмная/светлая тема, горячие клавиши
- SQLite: лента переживает перезапуск
- Wiki: конспекты Rust/Python с подсветкой кода и MathJax

## Быстрый старт
pip install -r requirements.txt
python app.py
→ http://localhost:5000

## Деплой
Procfile внутри (gunicorn). Render/Railway: start command
`gunicorn app:app --timeout 30`, health check `/health`.

## API
GET /api/feed · GET /api/refresh · GET /api/stats · GET /health

## Хоткеи
`/` — поиск · `r` — обновить
