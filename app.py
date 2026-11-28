import os, re, json, time, sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import quote, urlparse
import requests
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify, Response

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "state.db")
app = Flask(__name__, template_folder="templates", static_folder=None)
MEM = {}
SEARCH_CACHE = {}
TTL = 6 * 3600

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36"}

TRUSTED = {
    "elementy.ru": 15, "nplus1.ru": 13, "mccme.ru": 15, "kvantik.org": 12,
    "trv-science.ru": 13, "olimpiada.ru": 12, "scientificrussia.ru": 12,
    "indicator.ru": 13, "postnauka.ru": 14, "cyberleninka.ru": 12, "mathnet.ru": 15,
    "phys.msu.ru": 14, "msu.ru": 10, "spbu.ru": 10, "habr.com": 12,
    "metanit.com": 12, "pythonru.com": 10, "stepik.org": 10, "cxem.net": 12,
    "easyelectronics.ru": 12, "arduino.ru": 10, "3dtoday.ru": 10, "radiokot.ru": 10,
    "rosatom.ru": 12, "strana-rosatom.ru": 12, "atomic-energy.ru": 10, "roscosmos.ru": 12,
    "nsu.ru": 10, "mipt.ru": 10, "mephi.ru": 10, "bmstu.ru": 10, "nstu.ru": 10,
    "foxford.ru": 10, "interneturok.ru": 10, "resh.edu.ru": 10, "gramota.ru": 10,
    "fipi.ru": 10, "mathus.ru": 10, "acmp.ru": 10, "informatics.msk.ru": 10,
    "codeforces.com": 10, "inp.nsk.su": 12,
}
SCI = ["исследование", "эксперимент", "доказательство", "теорема", "гипотеза",
       "уравнение", "закономерность", "модель", "результаты", "метод", "вывод"]
RSS_FEEDS = [
    "https://nplus1.ru/rss",
    "https://elementy.ru/rss",
    "https://habr.com/ru/rss/hub/physics/all/",
    "https://habr.com/ru/rss/hub/rust/all/",
    "https://habr.com/ru/rss/hub/python/all/",
    "https://habr.com/ru/rss/hub/algorithms/all/",
    "https://habr.com/ru/rss/hub/electronics/all/",
    "https://habr.com/ru/rss/hub/3d_printing/all/",
    "https://trv-science.ru/feed/",
    "https://postnauka.ru/rss",
]

SERVER_CATS = {
    "phys9": {"q": "физика механика термодинамика электричество", "kw": ["механика", "кинематика", "динамика", "закон", "энергия", "импульс", "давление", "температура", "ток", "оптика"], "queries": ["физика 9 класс механика законы", "физика термодинамика электричество объяснение"]},
    "olympphys": {"q": "олимпиада физика задачи механика колебания", "kw": ["олимпиад", "задач", "механика", "колебан", "волн", "интерференц", "дифракц", "резонанс", "момент", "энерг"], "queries": ["олимпиадная физика задачи разбор", "олимпиада физика механика колебания решение"]},
    "prog": {"q": "программирование rust python алгоритмы", "kw": ["rust", "python", "алгоритм", "структур", "сортировк", "граф", "дерев", "рекурс", "памят", "код"], "queries": ["rust программирование руководство", "python алгоритмы структуры данных"]},
    "olympprog": {"q": "олимпиада информатика алгоритмы задачи", "kw": ["олимпиад", "алгоритм", "задач", "граф", "динамическ", "дерев", "сортировк", "поиск", "сложност"], "queries": ["олимпиадная информатика алгоритмы разбор", "олимпиада информатика задачи решение"]},
    "qphys": {"q": "квантовая физика фотон запутанность", "kw": ["квант", "фотон", "запутан", "суперпозиц", "волнов", "шрёдингер", "туннел", "спин", "кубит", "измерен"], "queries": ["квантовая физика эксперимент объяснение", "квантовая запутанность фотон эксперимент"]},
    "qmech": {"q": "квантовая механика оператор гамильтониан", "kw": ["квант", "гамильтон", "оператор", "волнов", "шрёдингер", "дирac", "спин", "туннел", "осциллятор", "собствен"], "queries": ["квантовая механика уравнение шрёдингера разбор", "квантовая механика операторы гамильтониан"]},
    "nucphys": {"q": "ядерная физика радиоактивность деление ядра", "kw": ["ядр", "радиоактив", "распад", "нейтрон", "протон", "изотоп", "делен", "синтез", "цепн", "реактор"], "queries": ["ядерная физика радиоактивность деление ядра", "ядерная физика термоядерный синтез обзор"]},
    "nucenergy": {"q": "ядерная энергетика АЭС реактор ВВЭР", "kw": ["аэс", "реактор", "ввэр", "рбмк", "бн-800", "топлив", "безопасност", "энергетик", "росатом", "станц"], "queries": ["ядерная энергетика АЭС реактор ВВЭР", "Росатом атомная энергетика новости"]},
    "astro": {"q": "астрономия планеты чёрная дыра галактика", "kw": ["астроном", "планет", "звезд", "чёрн", "дыр", "галактик", "комет", "астероид", "экзопланет", "телескоп"], "queries": ["астрономия чёрные дыры нейтронные звёзды", "астрономия экзопланеты наблюдения"]},
    "rosatom": {"q": "Росатом атомная промышленность ледокол", "kw": ["росатом", "атомн", "ледокол", "реактор", "твэл", "станц", "проект", "энергетик", "топлив", "флот"], "queries": ["Росатом атомная промышленность новости", "Росатом ледокол реактор проект"]},
    "roscosmos": {"q": "Роскосмос космонавтика ракета космодром", "kw": ["роскосмос", "ракет", "космодром", "спутник", "орбит", "станц", "пуск", "космонавт", "байконур", "восточн"], "queries": ["Роскосмос пуск ракета новости", "Роскосмос космодром спутник миссия"]},
    "math9": {"q": "математика алгебра геометрия квадратное уравнение", "kw": ["алгебр", "геометр", "уравнен", "теорем", "неравенств", "прогресс", "функци", "треугольн", "окружност", "тригонометр"], "queries": ["математика 9 класс алгебра геометрия профиль", "олимпиадная математика неравенства теория чисел"]},
    "chem9": {"q": "химия кислота основание соль реакция", "kw": ["хими", "кислот", "основан", "соль", "оксид", "реакц", "электролиз", "гидролиз", "равновес", "моль"], "queries": ["химия 9 класс кислоты основания соли", "химия окислительно-восстановительные реакции разбор"]},
    "rusoge": {"q": "русский язык орфография пунктуация синтаксис", "kw": ["русск", "орфограф", "пунктуац", "синтакс", "морфолог", "причаст", "деепричаст", "предложен", "изложен", "огэ"], "queries": ["ОГЭ русский язык пунктуация синтаксис разбор", "русский язык орфография правила разбор"]},
    "mephi": {"q": "МИФИ ядерный университет наука", "kw": ["мифи", "ядерн", "университет", "обнинск", "реактор", "исследован", "лаборатор", "физик", "энергетик", "наука"], "queries": ["МИФИ университет наука исследования", "МИФИ ядерные исследования новости"]},
    "mipt": {"q": "МФТИ физтех наука", "kw": ["мфти", "физтех", "долгодпрудн", "ландaу", "капиц", "квант", "сверхпровод", "графен", "лазер", "нанотехнолог"], "queries": ["МФТИ физтех наука исследования", "МФТИ физтех открытия новости"]},
    "msu": {"q": "МГУ московский университет наука", "kw": ["мгу", "ломоносов", "университет", "факультет", "воробьёв", "наук", "исследован", "лаборатор", "профессор", "эксперимент"], "queries": ["МГУ наука исследования новости", "МГУ физический факультет открытия"]},
    "nsu": {"q": "НГУ Новосибирский университет Академгородок", "kw": ["нгу", "академгородок", "новосибирск", "ияф", "будкер", "синхротрон", "коллайдер", "детектор", "лазер", "сибирск"], "queries": ["НГУ Новосибирск наука исследования", "ИЯФ СО РАН ускоритель новости"]},
    "nstu": {"q": "НГТУ технический университет инженерия", "kw": ["нгту", "новосибирск", "техническ", "инженер", "электроник", "микроконтроллер", "робототехник", "мехатроник", "автоматизац", "привод"], "queries": ["НГТУ университет инженерия исследования", "НГТУ новосибирск разработки новости"]},
    "bmstu": {"q": "МГТУ Баумана инженерия ракетостроение", "kw": ["мгту", "бауман", "инженер", "машиностроен", "робототехник", "ракетостроен", "королёв", "туполев", "сухой", "аддитивн"], "queries": ["МГТУ Баумана инженерия разработки", "МГТУ Баумана ракетостроение новости"]},
    "electronics": {"q": "электроника микроконтроллер arduino esp32 stm32", "kw": ["электроник", "микроконтроллер", "arduino", "esp32", "stm32", "плат", "резистор", "транзистор", "усилител", "пайк"], "queries": ["электроника микроконтроллер ESP32 проект", "электроника STM32 схема разбор"]},
    "print3d": {"q": "3D печать инженерная FDM аддитивные", "kw": ["3d", "печать", "fdm", "филамент", "слайсер", "адгез", "усадк", "аддитивн", "прототип", "тополог"], "queries": ["3D печать инженерная FDM материалы", "аддитивные технологии инженерная печать обзор"]},
}


def host_of(u):
    try:
        h = urlparse(u).hostname or ""
        return h.lower().replace("www.", "")
    except Exception:
        return ""


def rep(u):
    h = host_of(u)
    if not h:
        return 0
    for d, s in TRUSTED.items():
        if h == d or h.endswith("." + d):
            return s
    return 0


def ru_ratio(t):
    letters = re.findall(r"[a-zа-яё]", t.lower())
    if not letters:
        return 0.0
    ru = re.findall(r"[а-яё]", t.lower())
    return len(ru) / len(letters)


def parse_date(v):
    if not v:
        return None
    try:
        from email.utils import parsedate_to_datetime
        d = parsedate_to_datetime(v)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        try:
            d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except Exception:
            return None


def extract(url):
    try:
        r = requests.get(url, headers=UA, timeout=8, allow_redirects=True)
        if r.status_code != 200:
            return None
        final = r.url
        if rep(final) == 0:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form"]):
            tag.decompose()
        art = soup.find("article") or soup
        ps = [p.get_text(" ", strip=True) for p in art.find_all("p")]
        text = "\n".join(p for p in ps if len(p) > 40)
        if len(text) < 5000 or ru_ratio(text) < 0.65:
            return None
        title = soup.title.get_text(strip=True) if soup.title else ""
        date = None
        m = soup.find("meta", attrs={"property": "article:published_time"}) or soup.find("meta", attrs={"name": "pubdate"})
        if m and m.get("content"):
            date = m["content"]
        else:
            t = soup.find("time")
            if t and t.get("datetime"):
                date = t["datetime"]
        return {"url": final, "title": title, "text": text, "date": date, "host": host_of(final)}
    except Exception:
        return None


def score_item(it, kws):
    t = (it["title"] + " " + it["text"]).lower()
    s = min(sum(1 for k in kws if k in t) * 5, 30)
    L = len(it["text"])
    for th in (5000, 10000, 20000, 30000):
        if L >= th:
            s += 5
    s += min(sum(1 for w in SCI if w in t) * 2, 20)
    s += min(rep(it["url"]), 15)
    s += min(sum(1 for p in it["text"].split("\n") if len(p) > 100), 10)
    d = parse_date(it.get("date"))
    if d:
        age = (datetime.now(timezone.utc) - d).days
        s += 5 if age <= 30 else 3 if age <= 180 else 1 if age <= 365 else 0
    return min(s, 100)


def candidates(meta):
    out = []
    for q in meta["queries"]:
        try:
            r = requests.get("https://news.google.com/rss/search?q=" + quote(q) + "&hl=ru&gl=RU&ceid=RU:ru", headers=UA, timeout=8)
            soup = BeautifulSoup(r.content, "xml")
            for item in soup.find_all("item")[:12]:
                link = item.link.get_text(strip=True) if item.link else ""
                title = item.title.get_text(strip=True) if item.title else ""
                if link:
                    out.append({"url": link, "title": title, "provider": "Google News"})
        except Exception:
            pass
    for url in RSS_FEEDS:
        try:
            r = requests.get(url, headers=UA, timeout=8)
            soup = BeautifulSoup(r.content, "xml")
            for item in soup.find_all("item")[:10]:
                link = item.link.get_text(strip=True) if item.link else ""
                title = item.title.get_text(strip=True) if item.title else ""
                if link and rep(link) > 0:
                    out.append({"url": link, "title": title, "provider": "RSS"})
        except Exception:
            pass
    seen, uniq = set(), []
    for c in out:
        if c["url"] not in seen:
            seen.add(c["url"])
            uniq.append(c)
    return uniq[:40]


def search_cat(meta):
    cands = candidates(meta)
    kws = [k.lower() for k in meta["kw"]]
    final = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(extract, c["url"]): c for c in cands}
        for f in futs:
            try:
                art = f.result()
            except Exception:
                art = None
            if not art:
                continue
            c = futs[f]
            if c.get("title") and len(c["title"]) > len(art["title"]):
                art["title"] = c["title"]
            sc = score_item(art, kws)
            if sc < 35:
                continue
            L = len(art["text"])
            final.append({"title": art["title"], "url": art["url"], "source": art["host"],
                          "score": sc, "length": L, "minutes": max(8, L // 1200),
                          "excerpt": art["text"][:280], "date": art.get("date"), "provider": c["provider"]})
    best = {}
    for it in final:
        u = it["url"]
        if u not in best or it["score"] > best[u]["score"]:
            best[u] = it
    return sorted(best.values(), key=lambda x: (x["score"], x["length"]), reverse=True)[:30]


MANIFEST = {
    "name": "EngFeed · личная лента", "short_name": "EngFeed", "start_url": "/", "scope": "/",
    "display": "standalone", "background_color": "#0D1117", "theme_color": "#0D1117",
    "orientation": "portrait-primary",
    "icons": [{"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"},
              {"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "maskable"}],
}
ICON_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"><rect width="512" height="512" rx="96" fill="#0D1117"/><rect x="48" y="48" width="416" height="416" rx="72" fill="#23282E"/><rect x="48" y="48" width="416" height="40" rx="20" fill="#3A4149"/><rect x="96" y="236" width="320" height="40" rx="20" fill="#8A939E"/><rect x="96" y="238" width="320" height="12" rx="6" fill="#DCE3EA"/><ellipse cx="256" cy="256" rx="150" ry="40" fill="#4CC3FF" opacity=".35"/><rect x="236" y="120" width="40" height="120" rx="12" fill="#F5C400"/><rect x="96" y="330" width="320" height="16" rx="8" fill="#FF6A00"/><g fill="#B87333"><circle cx="96" cy="96" r="14"/><circle cx="416" cy="96" r="14"/><circle cx="96" cy="416" r="14"/><circle cx="416" cy="416" r="14"/></g></svg>'
SW_JS = """
var CACHE='engfeed-v1';
self.addEventListener('install',function(e){e.waitUntil(caches.open(CACHE).then(function(c){return c.addAll(['/','/manifest.webmanifest','/icon.svg']);}).then(function(){return self.skipWaiting();}));});
self.addEventListener('activate',function(e){e.waitUntil(caches.keys().then(function(ks){return Promise.all(ks.filter(function(k){return k!==CACHE;}).map(function(k){return caches.delete(k);}));}).then(function(){return self.clients.claim();}));});
self.addEventListener('fetch',function(e){
  if(e.request.method!=='GET') return;
  var url=new URL(e.request.url);
  if(e.request.mode==='navigate'){e.respondWith(fetch(e.request).then(function(r){var c=r.clone();caches.open(CACHE).then(function(cc){cc.put('/',c);});return r;}).catch(function(){return caches.match('/');}));return;}
  if(url.pathname==='/api/state'){e.respondWith(fetch(e.request).then(function(r){var c=r.clone();caches.open(CACHE).then(function(cc){cc.put(e.request,c);});return r;}).catch(function(){return caches.match(e.request);}));return;}
  if(url.origin===location.origin){e.respondWith(caches.match(e.request).then(function(h){return h||fetch(e.request).then(function(r){var c=r.clone();caches.open(CACHE).then(function(cc){cc.put(e.request,c);});return r;});}));}
});
"""


def db():
    try:
        c = sqlite3.connect(DB, timeout=5)
        c.execute("CREATE TABLE IF NOT EXISTS state(k TEXT PRIMARY KEY, v TEXT)")
        return c
    except Exception:
        return None


def state_get():
    c = db()
    if c is None:
        return MEM.get("me")
    try:
        row = c.execute("SELECT v FROM state WHERE k='me'").fetchone()
        return json.loads(row[0]) if row else None
    except Exception:
        return MEM.get("me")
    finally:
        c.close()


def state_put(data):
    MEM["me"] = data
    c = db()
    if c is None:
        return
    try:
        c.execute("INSERT INTO state(k,v) VALUES('me',?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (json.dumps(data, ensure_ascii=False),))
        c.commit()
    except Exception:
        pass
    finally:
        c.close()


@app.route("/healthz")
def healthz():
    return jsonify({"ok": True})


@app.route("/")
def index():
    try:
        with open(os.path.join(BASE, "templates", "index.html"), "rb") as f:
            return Response(f.read(), mimetype="text/html", headers={"Cache-Control": "no-store"})
    except Exception:
        return Response("<!DOCTYPE html><meta charset='utf-8'><title>EngFeed</title><body style='background:#0D1117;color:#E8EDF2;font-family:sans-serif;padding:40px'><h1>EngFeed</h1><p>templates/index.html не найден.</p>", mimetype="text/html")


@app.route("/manifest.webmanifest")
def manifest():
    return Response(json.dumps(MANIFEST, ensure_ascii=False), mimetype="application/manifest+json")


@app.route("/icon.svg")
def icon():
    return Response(ICON_SVG, mimetype="image/svg+xml")


@app.route("/sw.js")
def sw():
    return Response(SW_JS, mimetype="application/javascript", headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-store"})


@app.route("/api/state", methods=["GET", "PUT"])
def state():
    if request.method == "GET":
        return jsonify(state_get() or {})
    state_put(request.get_json(force=True, silent=True) or {})
    return jsonify({"ok": True})


@app.route("/api/search")
def api_search():
    cid = request.args.get("cat", "")
    meta = SERVER_CATS.get(cid)
    if not meta:
        return jsonify({"items": []})
    now = time.time()
    hit = SEARCH_CACHE.get(cid)
    if hit and now - hit["ts"] < TTL and not request.args.get("force"):
        return jsonify({"items": hit["items"], "cached": True})
    items = search_cat(meta)
    SEARCH_CACHE[cid] = {"ts": now, "items": items}
    return jsonify({"items": items, "cached": False})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
