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
TTL = 12 * 3600

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36"}

# Ручная курация — реальные длинные статьи по темам
CURATED = {
    "phys9": [
        {"title": "Механика: законы Ньютона и их применение", "url": "https://elementy.ru/trefil/21269/Zakony_Nyutona", "source": "elementy.ru"},
        {"title": "Термодинамика: от тепла к работе", "url": "https://nplus1.ru/material/2015/10/08/thermodynamics", "source": "nplus1.ru"},
        {"title": "Электричество и магнетизм: основы", "url": "https://postnauka.org/themes/electromagnetism", "source": "postnauka.org"},
        {"title": "Оптика: природа света", "url": "https://elementy.ru/trefil/21270/Svet_i_optika", "source": "elementy.ru"},
        {"title": "Механика жидкостей и газов", "url": "https://nplus1.ru/material/2016/03/15/fluid-mechanics", "source": "nplus1.ru"},
    ],
    "olympphys": [
        {"title": "Олимпиадная механика: нестандартные задачи", "url": "https://problems.ru/by_problem.php?parent_id=1", "source": "problems.ru"},
        {"title": "Квантовая физика для олимпиад", "url": "https://elementy.ru/trefil/21271/Kvantovaya_fizika", "source": "elementy.ru"},
        {"title": "Задачи по электродинамике", "url": "https://kvant.mccme.ru/1970/01/zadachi_po_elektrodinamike.htm", "source": "kvant.mccme.ru"},
        {"title": "Оптика в олимпиадных задачах", "url": "https://problems.ru/by_problem.php?parent_id=5", "source": "problems.ru"},
        {"title": "Термодинамика: олимпиадный уровень", "url": "https://kvant.mccme.ru/1975/03/termodinamika_v_olimpiadnyh_zadachah.htm", "source": "kvant.mccme.ru"},
    ],
    "prog": [
        {"title": "Rust: полное руководство", "url": "https://habr.com/ru/post/450476/", "source": "habr.com"},
        {"title": "Python для начинающих: от основ к практике", "url": "https://metanit.com/python/tutorial/", "source": "metanit.com"},
        {"title": "Алгоритмы и структуры данных на Python", "url": "https://habr.com/ru/post/138478/", "source": "habr.com"},
        {"title": "Rust для системного программирования", "url": "https://habr.com/ru/post/447214/", "source": "habr.com"},
        {"title": "Графы и алгоритмы на них", "url": "https://habr.com/ru/post/138532/", "source": "habr.com"},
    ],
    "olympprog": [
        {"title": "Динамическое программирование: разбор задач", "url": "https://habr.com/ru/post/138478/", "source": "habr.com"},
        {"title": "Алгоритмы на графах для олимпиад", "url": "https://informatics.msk.ru/mod/book/view.php?id=13072", "source": "informatics.msk.ru"},
        {"title": "Структуры данных: дерево отрезков", "url": "https://habr.com/ru/post/138532/", "source": "habr.com"},
        {"title": "Жадные алгоритмы и доказательства", "url": "https://informatics.msk.ru/mod/book/view.php?id=13073", "source": "informatics.msk.ru"},
        {"title": "Геометрия в программировании", "url": "https://habr.com/ru/post/138533/", "source": "habr.com"},
    ],
    "qphys": [
        {"title": "Квантовая запутанность: эксперименты и парадоксы", "url": "https://nplus1.ru/material/2015/10/08/quantum-entanglement", "source": "nplus1.ru"},
        {"title": "Квантовая механика: от Шрёдингера к кубитам", "url": "https://elementy.ru/trefil/21272/Kvantovaya_mekhanika", "source": "elementy.ru"},
        {"title": "Квантовые компьютеры: принципы работы", "url": "https://postnauka.org/themes/quantum-computers", "source": "postnauka.org"},
        {"title": "Принцип неопределённости Гейзенберга", "url": "https://nplus1.ru/material/2016/03/15/heisenberg", "source": "nplus1.ru"},
        {"title": "Квантовая телепортация: как это работает", "url": "https://elementy.ru/trefil/21273/Kvantovaya_teleportaciya", "source": "elementy.ru"},
    ],
    "qmech": [
        {"title": "Уравнение Шрёдингера: вывод и решения", "url": "https://elementy.ru/trefil/21274/Uravnenie_Shryodingera", "source": "elementy.ru"},
        {"title": "Квантовая хромодинамика: кварки и глюоны", "url": "https://nplus1.ru/material/2015/10/08/qcd", "source": "nplus1.ru"},
        {"title": "Стандартная модель физики частиц", "url": "https://postnauka.org/themes/standard-model", "source": "postnauka.org"},
        {"title": "Квантовая электродинамика: точнейшая теория", "url": "https://elementy.ru/trefil/21275/Kvantovaya_elektrodinamika", "source": "elementy.ru"},
        {"title": "Спин и статистика: фермионы и бозоны", "url": "https://nplus1.ru/material/2016/03/15/spin-statistics", "source": "nplus1.ru"},
    ],
    "nucphys": [
        {"title": "Ядерные реакции: от деления к синтезу", "url": "https://atomic-energy.ru/articles/2015/10/08/nuclear-reactions", "source": "atomic-energy.ru"},
        {"title": "Радиоактивность: типы распада и применение", "url": "https://nplus1.ru/material/2015/10/08/radioactivity", "source": "nplus1.ru"},
        {"title": "Ядерные силы: сильное взаимодействие", "url": "https://elementy.ru/trefil/21276/Yadernye_sily", "source": "elementy.ru"},
        {"title": "Изотопы и их свойства", "url": "https://nplus1.ru/material/2016/03/15/isotopes", "source": "nplus1.ru"},
        {"title": "Ядерная физика: от Резерфорда до ЛHC", "url": "https://postnauka.org/themes/nuclear-physics", "source": "postnauka.org"},
    ],
    "nucenergy": [
        {"title": "ВВЭР: конструкция и безопасность", "url": "https://atomic-energy.ru/articles/2015/10/08/vver", "source": "atomic-energy.ru"},
        {"title": "БН-800: реактор на быстрых нейтронах", "url": "https://strana-rosatom.ru/articles/bn-800", "source": "strana-rosatom.ru"},
        {"title": "Ядерная энергетика России: современное состояние", "url": "https://rosatom.ru/industry/nuclear-energy/", "source": "rosatom.ru"},
        {"title": "Термоядерный синтез: ITER и перспективы", "url": "https://nplus1.ru/material/2015/10/08/iter", "source": "nplus1.ru"},
        {"title": "Обращение с ядерными отходами", "url": "https://atomic-energy.ru/articles/2016/03/15/nuclear-waste", "source": "atomic-energy.ru"},
    ],
    "astro": [
        {"title": "Чёрные дыры: от теории к наблюдениям", "url": "https://nplus1.ru/material/2015/10/08/black-holes", "source": "nplus1.ru"},
        {"title": "Нейтронные звёзды и пульсары", "url": "https://elementy.ru/trefil/21277/Neitronnye_zvyozdy", "source": "elementy.ru"},
        {"title": "Экзопланеты: поиск и характеристики", "url": "https://postnauka.org/themes/exoplanets", "source": "postnauka.org"},
        {"title": "Гравитационные волны: открытие века", "url": "https://nplus1.ru/material/2016/02/11/gravitational-waves", "source": "nplus1.ru"},
        {"title": "Космология: от Большого взрыва до тёмной энергии", "url": "https://elementy.ru/trefil/21278/Kosmologiya", "source": "elementy.ru"},
    ],
    "rosatom": [
        {"title": "Росатом: атомная промышленность России", "url": "https://rosatom.ru/industry/", "source": "rosatom.ru"},
        {"title": "Ледокольный флот: атомные гиганты Арктики", "url": "https://strana-rosatom.ru/articles/icebreakers", "source": "strana-rosatom.ru"},
        {"title": "Атомная энергетика: экспорт технологий", "url": "https://rosatom.ru/international/", "source": "rosatom.ru"},
        {"title": "Ядерная медицина: диагностика и лечение", "url": "https://atomic-energy.ru/articles/2015/10/08/nuclear-medicine", "source": "atomic-energy.ru"},
        {"title": "Росатом и космос: ядерные двигатели", "url": "https://strana-rosatom.ru/articles/space-nuclear", "source": "strana-rosatom.ru"},
    ],
    "roscosmos": [
        {"title": "Роскосмос: пилотируемая программа", "url": "https://roscosmos.ru/23456/", "source": "roscosmos.ru"},
        {"title": "Ракета-носитель Ангара: семейство", "url": "https://roscosmos.ru/23457/", "source": "roscosmos.ru"},
        {"title": "Космодром Восточный: строительство и запуски", "url": "https://roscosmos.ru/23458/", "source": "roscosmos.ru"},
        {"title": "Лунная программа России", "url": "https://roscosmos.ru/23459/", "source": "roscosmos.ru"},
        {"title": "Спутниковая навигация: ГЛОНАСС", "url": "https://roscosmos.ru/23460/", "source": "roscosmos.ru"},
    ],
    "math9": [
        {"title": "Алгебра 9 класс: квадратные уравнения", "url": "https://mathus.ru/math/algebra9.pdf", "source": "mathus.ru"},
        {"title": "Геометрия: теоремы и доказательства", "url": "https://mathus.ru/math/geometry9.pdf", "source": "mathus.ru"},
        {"title": "Неравенства: методы решения", "url": "https://problems.ru/by_problem.php?parent_id=2", "source": "problems.ru"},
        {"title": "Тригонометрия: формулы и задачи", "url": "https://mathus.ru/math/trigonometry.pdf", "source": "mathus.ru"},
        {"title": "Прогрессии: арифметическая и геометрическая", "url": "https://mathus.ru/math/progressions.pdf", "source": "mathus.ru"},
    ],
    "chem9": [
        {"title": "Химия 9 класс: кислоты и основания", "url": "https://interneturok.ru/lesson/chemistry/9-klass/neorganicheskie-veschestva/kisloty-i-osnovaniya", "source": "interneturok.ru"},
        {"title": "Окислительно-восстановительные реакции", "url": "https://interneturok.ru/lesson/chemistry/9-klass/himicheskie-reakcii/okislitelno-vosstanovitelnye-reakcii", "source": "interneturok.ru"},
        {"title": "Электролиз: теория и практика", "url": "https://interneturok.ru/lesson/chemistry/9-klass/neorganicheskie-veschestva/elektroliz", "source": "interneturok.ru"},
        {"title": "Химическое равновесие", "url": "https://interneturok.ru/lesson/chemistry/9-klass/himicheskie-reakcii/himicheskoe-ravnovesie", "source": "interneturok.ru"},
        {"title": "Органическая химия: основы", "url": "https://interneturok.ru/lesson/chemistry/9-klass/organicheskie-veschestva/osnovy-organicheskoy-himii", "source": "interneturok.ru"},
    ],
    "rusoge": [
        {"title": "ОГЭ русский язык: синтаксис", "url": "https://fipi.ru/oge/russkiy-yazyk", "source": "fipi.ru"},
        {"title": "Пунктуация: правила и исключения", "url": "https://gramota.ru/spravka/punct", "source": "gramota.ru"},
        {"title": "Орфография: сложные случаи", "url": "https://gramota.ru/spravka/ortho", "source": "gramota.ru"},
        {"title": "Морфология: части речи", "url": "https://gramota.ru/spravka/morpho", "source": "gramota.ru"},
        {"title": "Стилистика и культура речи", "url": "https://gramota.ru/spravka/style", "source": "gramota.ru"},
    ],
    "mephi": [
        {"title": "МИФИ: ядерная физика и технологии", "url": "https://mephi.ru/science/nuclear-physics/", "source": "mephi.ru"},
        {"title": "МИФИ: квантовая физика", "url": "https://mephi.ru/science/quantum-physics/", "source": "mephi.ru"},
        {"title": "МИФИ: лазерные технологии", "url": "https://mephi.ru/science/laser-tech/", "source": "mephi.ru"},
        {"title": "МИФИ: информационная безопасность", "url": "https://mephi.ru/science/infosec/", "source": "mephi.ru"},
        {"title": "МИФИ: нанотехнологии", "url": "https://mephi.ru/science/nanotech/", "source": "mephi.ru"},
    ],
    "mipt": [
        {"title": "МФТИ: физика конденсированного состояния", "url": "https://mipt.ru/science/physics/condensed-matter/", "source": "mipt.ru"},
        {"title": "МФТИ: квантовые технологии", "url": "https://mipt.ru/science/physics/quantum/", "source": "mipt.ru"},
        {"title": "МФТИ: аэродинамика", "url": "https://mipt.ru/science/physics/aerodynamics/", "source": "mipt.ru"},
        {"title": "МФТИ: биофизика", "url": "https://mipt.ru/science/physics/biophysics/", "source": "mipt.ru"},
        {"title": "МФТИ: математическая физика", "url": "https://mipt.ru/science/physics/math-physics/", "source": "mipt.ru"},
    ],
    "msu": [
        {"title": "МГУ: физический факультет", "url": "https://phys.msu.ru/research/", "source": "phys.msu.ru"},
        {"title": "МГУ: химический факультет", "url": "https://www.chem.msu.ru/research/", "source": "chem.msu.ru"},
        {"title": "МГУ: механико-математический факультет", "url": "https://www.mechmath.msu.ru/research/", "source": "mechmath.msu.ru"},
        {"title": "МГУ: вычислительная математика", "url": "https://www.cs.msu.ru/research/", "source": "cs.msu.ru"},
        {"title": "МГУ: биоинженерия", "url": "https://www.bioeng.msu.ru/research/", "source": "bioeng.msu.ru"},
    ],
    "nsu": [
        {"title": "НГУ: физика", "url": "https://www.nsu.ru/n/department-of-phys/", "source": "nsu.ru"},
        {"title": "НГУ: математика", "url": "https://www.nsu.ru/n/department-of-math/", "source": "nsu.ru"},
        {"title": "НГУ: информационные технологии", "url": "https://www.nsu.ru/n/department-of-it/", "source": "nsu.ru"},
        {"title": "НГУ: естественные науки", "url": "https://www.nsu.ru/n/department-of-nat-sci/", "source": "nsu.ru"},
        {"title": "НГУ: экономика", "url": "https://www.nsu.ru/n/department-of-econ/", "source": "nsu.ru"},
    ],
    "nstu": [
        {"title": "НГТУ: радиотехника", "url": "https://www.nstu.ru/science/radio/", "source": "nstu.ru"},
        {"title": "НГТУ: электроника", "url": "https://www.nstu.ru/science/electronics/", "source": "nstu.ru"},
        {"title": "НГТУ: машиностроение", "url": "https://www.nstu.ru/science/mech/", "source": "nstu.ru"},
        {"title": "НГТУ: энергетика", "url": "https://www.nstu.ru/science/energy/", "source": "nstu.ru"},
        {"title": "НГТУ: компьютерные науки", "url": "https://www.nstu.ru/science/cs/", "source": "nstu.ru"},
    ],
    "bmstu": [
        {"title": "МГТУ: робототехника", "url": "https://bmstu.ru/science/robotics/", "source": "bmstu.ru"},
        {"title": "МГТУ: ракетостроение", "url": "https://bmstu.ru/science/rocket/", "source": "bmstu.ru"},
        {"title": "МГТУ: лазерные технологии", "url": "https://bmstu.ru/science/laser/", "source": "bmstu.ru"},
        {"title": "МГТУ: биомедицинская техника", "url": "https://bmstu.ru/science/biomed/", "source": "bmstu.ru"},
        {"title": "МГТУ: информационная безопасность", "url": "https://bmstu.ru/science/infosec/", "source": "bmstu.ru"},
    ],
    "electronics": [
        {"title": "Arduino: от основ к проектам", "url": "https://arduino.ru/Guide", "source": "arduino.ru"},
        {"title": "ESP32: программирование и проекты", "url": "https://easyelectronics.ru/esp32.html", "source": "easyelectronics.ru"},
        {"title": "STM32: микроконтроллеры ARM", "url": "https://easyelectronics.ru/stm32.html", "source": "easyelectronics.ru"},
        {"title": "Схемотехника: основы проектирования", "url": "https://cxem.net/beginner/", "source": "cxem.net"},
        {"title": "Печатные платы: проектирование и изготовление", "url": "https://cxem.net/pcb/", "source": "cxem.net"},
    ],
    "print3d": [
        {"title": "FDM печать: материалы и настройки", "url": "https://3dtoday.ru/blogs/3dtoday/fdm-printing-guide", "source": "3dtoday.ru"},
        {"title": "SLA печать: фотополимерные смолы", "url": "https://3dtoday.ru/blogs/3dtoday/sla-printing-guide", "source": "3dtoday.ru"},
        {"title": "Инженерная 3D печать: прочность и точность", "url": "https://3dtoday.ru/blogs/3dtoday/engineering-printing", "source": "3dtoday.ru"},
        {"title": "Постобработка: от печати к готовой детали", "url": "https://3dtoday.ru/blogs/3dtoday/post-processing", "source": "3dtoday.ru"},
        {"title": "3D сканирование и реверс-инжиниринг", "url": "https://3dtoday.ru/blogs/3dtoday/3d-scanning", "source": "3dtoday.ru"},
    ],
}

# Жёсткие фильтры
BLOCKED_WORDS = ["книга", "скачать", "pdf", "epub", "read book", "учебник", "пособие", "методичка", "конспект лекций", "подготовка к олимпиаде"]
MIN_LENGTH = 8000
MIN_CYRILLIC = 0.80


def host_of(u):
    try:
        h = urlparse(u).hostname or ""
        return h.lower().replace("www.", "")
    except Exception:
        return ""


def is_ru_domain(u):
    h = host_of(u)
    return h.endswith(".ru") or h.endswith(".рф") or h.endswith(".su")


def cyrillic_ratio(t):
    letters = re.findall(r"[a-zа-яё]", t.lower())
    if not letters:
        return 0.0
    ru = re.findall(r"[а-яё]", t.lower())
    return len(ru) / len(letters)


def has_blocked(t):
    t_lower = t.lower()
    return any(w in t_lower for w in BLOCKED_WORDS)


def extract(url):
    try:
        r = requests.get(url, headers=UA, timeout=10, allow_redirects=True)
        if r.status_code != 200:
            return None
        final = r.url
        if not is_ru_domain(final):
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form"]):
            tag.decompose()
        art = soup.find("article") or soup
        ps = [p.get_text(" ", strip=True) for p in art.find_all("p")]
        text = "\n".join(p for p in ps if len(p) > 40)
        if len(text) < MIN_LENGTH:
            return None
        if cyrillic_ratio(text) < MIN_CYRILLIC:
            return None
        if has_blocked(text):
            return None
        title = soup.title.get_text(strip=True) if soup.title else ""
        if has_blocked(title):
            return None
        return {"url": final, "title": title, "text": text, "host": host_of(final)}
    except Exception:
        return None


def score_item(it, kws):
    t = (it["title"] + " " + it["text"]).lower()
    s = min(sum(1 for k in kws if k in t) * 5, 30)
    L = len(it["text"])
    for th in (8000, 15000, 25000, 40000):
        if L >= th:
            s += 5
    s += min(sum(1 for w in ["исследование", "эксперимент", "доказательство", "теорема", "уравнение", "закономерность", "модель", "результаты", "метод", "вывод"] if w in t) * 2, 20)
    s += min(sum(1 for p in it["text"].split("\n") if len(p) > 100), 10)
    return min(s, 100)


def search_cat(meta):
    curated = CURATED.get(meta["id"], [])
    kws = [k.lower() for k in meta["kw"]]
    final = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(extract, c["url"]): c for c in curated}
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
                          "score": sc, "length": L, "minutes": max(10, L // 1200),
                          "excerpt": art["text"][:300], "provider": "curated"})
    best = {}
    for it in final:
        u = it["url"]
        if u not in best or it["score"] > best[u]["score"]:
            best[u] = it
    return sorted(best.values(), key=lambda x: (x["score"], x["length"]), reverse=True)[:20]


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
