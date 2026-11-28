import os, re, json, time, sqlite3
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote, urlparse
import requests
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify, Response, send_from_directory

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
    "codeforces.com": 10, "inp.nsk.su": 12, "ru.wikipedia.org": 12,
}
SITE_FILTER = (" (site:elementy.ru OR site:nplus1.ru OR site:habr.com OR site:postnauka.ru "
               "OR site:trv-science.ru OR site:cyberleninka.ru OR site:mathus.ru OR site:olimpiada.ru "
               "OR site:indicator.ru OR site:scientificrussia.ru)")
FEEDS = [
    "https://nplus1.ru/rss",
    "https://elementy.ru/rss",
    "https://habr.com/ru/rss/hub/physics/all/",
    "https://habr.com/ru/rss/hub/rust/all/",
    "https://habr.com/ru/rss/hub/python/all/",
    "https://habr.com/ru/rss/hub/algorithms/all/",
    "https://habr.com/ru/rss/hub/electronics/all/",
    "https://habr.com/ru/rss/hub/3d_printing/all/",
    "https://trv-science.ru/feed/",
]
BLOCKED = ["книга", "скачать", "pdf", "epub", "учебник", "пособие", "методичка", "конспект лекций", "read book"]
SCI = ["исследование", "эксперимент", "доказательство", "теорема", "гипотеза",
       "уравнение", "закономерность", "модель", "результаты", "метод", "вывод"]

SERVER_CATS = {
    "phys9": {"kw": ["механика", "кинематика", "динамика", "закон", "энергия", "импульс", "давление", "температура", "ток", "оптика"], "queries": ["физика механика законы сохранения объяснение", "физика термодинамика электричество оптика разбор"]},
    "olympphys": {"kw": ["олимпиад", "задач", "механика", "колебан", "волн", "интерференц", "дифракц", "резонанс", "момент", "энерг"], "queries": ["олимпиадная физика задачи разбор решение", "олимпиада физика механика колебания оптика"]},
    "prog": {"kw": ["rust", "python", "алгоритм", "структур", "сортировк", "граф", "дерев", "рекурс", "памят", "код"], "queries": ["rust программирование руководство разбор", "python алгоритмы структуры данных разбор"]},
    "olympprog": {"kw": ["олимпиад", "алгоритм", "задач", "граф", "динамическ", "дерев", "сортировк", "поиск", "сложност"], "queries": ["олимпиадная информатика алгоритмы разбор задач", "олимпиада информатика динамическое программирование графы"]},
    "qphys": {"kw": ["квант", "фотон", "запутан", "суперпозиц", "волнов", "шрёдингер", "туннел", "спин", "кубит", "измерен"], "queries": ["квантовая физика эксперимент запутанность объяснение", "квантовая механика фотон суперпозиция измерение"]},
    "qmech": {"kw": ["квант", "гамильтон", "оператор", "волнов", "шрёдингер", "спин", "туннел", "осциллятор", "собствен"], "queries": ["квантовая механика уравнение шрёдингера разбор", "квантовая механика операторы гамильтониан спин"]},
    "nucphys": {"kw": ["ядр", "радиоактив", "распад", "нейтрон", "протон", "изотоп", "делен", "синтез", "цепн", "реактор"], "queries": ["ядерная физика радиоактивность деление ядра обзор", "ядерная физика термоядерный синтез нейтроны"]},
    "nucenergy": {"kw": ["аэс", "реактор", "ввэр", "рбмк", "бн-800", "топлив", "безопасност", "энергетик", "росатом", "станц"], "queries": ["ядерная энергетика АЭС реактор ВВЭР обзор", "Росатом атомная энергетика реактор новости"]},
    "astro": {"kw": ["астроном", "планет", "звезд", "чёрн", "дыр", "галактик", "комет", "астероид", "экзопланет", "телескоп"], "queries": ["астрономия чёрные дыры нейтронные звёзды обзор", "астрономия экзопланеты галактики наблюдения"]},
    "rosatom": {"kw": ["росатом", "атомн", "ледокол", "реактор", "твэл", "станц", "проект", "энергетик", "топлив", "флот"], "queries": ["Росатом атомная промышленность ледокол новости", "Росатом реактор проект энергетика обзор"]},
    "roscosmos": {"kw": ["роскосмос", "ракет", "космодром", "спутник", "орбит", "станц", "пуск", "космонавт", "байконур", "восточн"], "queries": ["Роскосмос пуск ракета космодром новости", "Роскосмос спутник орбитальная станция миссия"]},
    "math9": {"kw": ["алгебр", "геометр", "уравнен", "теорем", "неравенств", "прогресс", "функци", "треугольн", "окружност", "тригонометр"], "queries": ["математика алгебра геометрия теоремы разбор", "олимпиадная математика неравенства теория чисел"]},
    "chem9": {"kw": ["хими", "кислот", "основан", "соль", "оксид", "реакц", "электролиз", "гидролиз", "равновес", "моль"], "queries": ["химия кислоты основания соли реакции разбор", "химия окислительно-восстановительные реакции электролиз"]},
    "rusoge": {"kw": ["русск", "орфограф", "пунктуац", "синтакс", "морфолог", "причаст", "деепричаст", "предложен", "изложен", "огэ"], "queries": ["ОГЭ русский язык пунктуация синтаксис разбор", "русский язык орфография причастия деепричастия правила"]},
    "mephi": {"kw": ["мифи", "ядерн", "университет", "обнинск", "реактор", "исследован", "лаборатор", "физик", "энергетик", "наука"], "queries": ["МИФИ университет наука исследования новости", "МИФИ ядерные технологии лаборатория"]},
    "mipt": {"kw": ["мфти", "физтех", "долгодпрудн", "квант", "сверхпровод", "графен", "лазер", "нанотехнолог", "исследован"], "queries": ["МФТИ физтех наука исследования новости", "МФТИ квантовые технологии графен сверхпроводимость"]},
    "msu": {"kw": ["мгу", "ломоносов", "университет", "факультет", "воробьёв", "наук", "исследован", "лаборатор", "профессор", "эксперимент"], "queries": ["МГУ наука исследования новости открытия", "МГУ физический факультет эксперимент лаборатория"]},
    "nsu": {"kw": ["нгу", "академгородок", "новосибирск", "ияф", "будкер", "синхротрон", "коллайдер", "детектор", "лазер", "сибирск"], "queries": ["НГУ Новосибирск Академгородок наука новости", "ИЯФ СО РАН ускоритель синхротрон новости"]},
    "nstu": {"kw": ["нгту", "новосибирск", "техническ", "инженер", "электроник", "микроконтроллер", "робототехник", "мехатроник", "автоматизац", "привод"], "queries": ["НГТУ университет инженерия разработки новости", "НГТУ электроника робототехника мехатроника"]},
    "bmstu": {"kw": ["мгту", "бауман", "инженер", "машиностроен", "робототехник", "ракетостроен", "королёв", "туполев", "сухой", "аддитивн"], "queries": ["МГТУ Баумана инженерия разработки новости", "МГТУ Баумана ракетостроение робототехника"]},
    "electronics": {"kw": ["электроник", "микроконтроллер", "arduino", "esp32", "stm32", "плат", "резистор", "транзистор", "усилител", "пайк"], "queries": ["электроника микроконтроллер ESP32 STM32 проект разбор", "электроника Arduino схема пайка руководство"]},
    "print3d": {"kw": ["3d", "печать", "fdm", "филамент", "слайсер", "адгез", "усадк", "аддитивн", "прототип", "тополог"], "queries": ["3D печать инженерная FDM материалы обзор", "аддитивные технологии инженерная печать прочность"]},
}


def host_of(u):
    try:
        return (urlparse(u).hostname or "").lower().replace("www.", "")
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
    return len(re.findall(r"[а-яё]", t.lower())) / len(letters)


def blocked(t):
    tl = t.lower()
    return any(w in tl for w in BLOCKED)


def extract(url):
    try:
        r = requests.get(url, headers=UA, timeout=9, allow_redirects=True)
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
        if len(text) < 2500 or ru_ratio(text) < 0.8:
            return None
        title = soup.title.get_text(strip=True) if soup.title else ""
        if blocked(title) or blocked(text[:2000]):
            return None
        date = None
        m = soup.find("meta", attrs={"property": "article:published_time"})
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
    for th in (2500, 6000, 12000, 25000):
        if L >= th:
            s += 5
    s += min(sum(1 for w in SCI if w in t) * 2, 20)
    s += min(rep(it["url"]), 15)
    s += min(sum(1 for p in it["text"].split("\n") if len(p) > 100), 10)
    return min(s, 100)


def candidates(meta):
    out = []
    kws = [k.lower() for k in meta["kw"]]
    for feed in FEEDS:
        try:
            r = requests.get(feed, headers=UA, timeout=8)
            soup = BeautifulSoup(r.content, "xml")
            for item in soup.find_all("item")[:25]:
                title = item.title.get_text(strip=True) if item.title else ""
                link = item.link.get_text(strip=True) if item.link else ""
                desc = item.description.get_text(strip=True) if item.description else ""
                blob = (title + " " + desc).lower()
                if link and rep(link) > 0 and any(k in blob for k in kws):
                    out.append({"url": link, "title": title})
        except Exception:
            pass
    for q in meta["queries"]:
        try:
            r = requests.get("https://news.google.com/rss/search?q=" + quote(q + SITE_FILTER) + "&hl=ru&gl=RU&ceid=RU:ru", headers=UA, timeout=8)
            soup = BeautifulSoup(r.content, "xml")
            for item in soup.find_all("item")[:12]:
                link = item.link.get_text(strip=True) if item.link else ""
                title = item.title.get_text(strip=True) if item.title else ""
                if link:
                    out.append({"url": link, "title": title})
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
    with ThreadPoolExecutor(max_workers=8) as ex:
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
            if sc < 40:
                continue
            L = len(art["text"])
            final.append({"title": art["title"], "url": art["url"], "source": art["host"],
                          "score": sc, "length": L, "minutes": max(8, L // 1200),
                          "excerpt": art["text"][:300], "date": art.get("date"), "provider": "ru-search"})
    best = {}
    for it in final:
        u = it["url"]
        if u not in best or it["score"] > best[u]["score"]:
            best[u] = it
    return sorted(best.values(), key=lambda x: (x["score"], x["length"]), reverse=True)[:20]


MANIFEST = {
    "name": "ENGFEED 2.0 · научная лента", "short_name": "ENGFEED", "start_url": "/", "scope": "/",
    "display": "standalone", "background_color": "#080D16", "theme_color": "#080D16",
    "orientation": "portrait-primary",
    "icons": [{"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"},
              {"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "maskable"}],
}
ICON_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"><rect width="512" height="512" rx="112" fill="#080D16"/><circle cx="256" cy="256" r="150" fill="none" stroke="#1E3A5F" stroke-width="26"/><circle cx="256" cy="256" r="150" fill="none" stroke="#398BFF" stroke-width="26" stroke-dasharray="220 722" stroke-linecap="round" transform="rotate(-90 256 256)"/><circle cx="256" cy="256" r="86" fill="none" stroke="#12203A" stroke-width="18"/><circle cx="256" cy="256" r="86" fill="none" stroke="#4CC3FF" stroke-width="18" stroke-dasharray="120 420" stroke-linecap="round" transform="rotate(40 256 256)"/><circle cx="256" cy="256" r="26" fill="#398BFF"/><circle cx="382" cy="180" r="14" fill="#FF941F"/></svg>'
SW_JS = """
var CACHE='engfeed-v3';
self.addEventListener('install',function(e){e.waitUntil(caches.open(CACHE).then(function(c){return c.addAll(['/','/manifest.webmanifest','/icon.svg']);}).then(function(){return self.skipWaiting();}));});
self.addEventListener('activate',function(e){e.waitUntil(caches.keys().then(function(ks){return Promise.all(ks.filter(function(k){return k!==CACHE;}).map(function(k){return caches.delete(k);}));}).then(function(){return self.clients.claim();}));});
self.addEventListener('fetch',function(e){
  if(e.request.method!=='GET') return;
  var url=new URL(e.request.url);
  if(e.request.mode==='navigate'){e.respondWith(fetch(e.request).then(function(r){var c=r.clone();caches.open(CACHE).then(function(cc){cc.put('/',c);});return r;}).catch(function(){return caches.match('/');}));return;}
  if(url.pathname==='/api/state'||url.pathname==='/api/search'){e.respondWith(fetch(e.request).then(function(r){var c=r.clone();caches.open(CACHE).then(function(cc){cc.put(e.request,c);});return r;}).catch(function(){return caches.match(e.request);}));return;}
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


@app.route("/assets/<path:fn>")
def assets(fn):
    resp = send_from_directory(os.path.join(BASE, "assets"), fn)
    resp.headers["Cache-Control"] = "public, max-age=604800"
    return resp


@app.route("/")
def index():
    try:
        with open(os.path.join(BASE, "templates", "index.html"), "rb") as f:
            return Response(f.read(), mimetype="text/html", headers={"Cache-Control": "no-store"})
    except Exception:
        return Response("<!DOCTYPE html><meta charset='utf-8'><title>ENGFEED</title><body style='background:#080D16;color:#F1F5FC;font-family:sans-serif;padding:40px'><h1>ENGFEED</h1><p>templates/index.html не найден.</p>", mimetype="text/html")


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
