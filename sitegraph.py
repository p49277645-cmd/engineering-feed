# sitegraph.py — сайт-первый глобальный поиск: probing + scoring + чтение любых потоков
import os, re, time, math, logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote_plus, urlparse, urljoin, unquote
import requests, feedparser
from bs4 import BeautifulSoup

logger = logging.getLogger("engfeed.sitegraph")
HTTP = requests.Session()
HTTP.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                     "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8"})
T = (2.0, 4.0)
SYN = {"олимпиада": ["олимпиад", "задач", "тур", "вариант", "решени"],
       "физика": ["физик", "механик", "оптик", "электрич", "термодинам"],
       "математика": ["математ", "алгебр", "геометр", "интеграл"],
       "ии": ["нейросет", "машинн", "ml", "ai"], "rust": ["rust", "cargo", "ownership"]}
SEEDS = {"olympiad": ["problems.ru", "olimpiada.ru", "vos.olimpiada.ru", "kvant.mccme.ru", "mathus.ru", "alleng.org", "siriusolymp.ru", "foxford.ru"],
         "physics": ["elementy.ru", "nplus1.ru", "kvant.mccme.ru", "physics.ru"],
         "math": ["problems.ru", "mathus.ru", "kvant.mccme.ru", "cyberleninka.ru"],
         "code": ["habr.com", "opennet.ru", "rust-lang.org", "python.org"],
         "university": ["nsu.ru", "msu.ru", "mipt.ru", "mephi.ru", "bmstu.ru", "spbu.ru"],
         "science": ["nplus1.ru", "elementy.ru", "cyberleninka.ru", "nauka.club"]}
FAMKEY = {"olympiad": ["олимпиад", "задач", "тур", "вариант"], "physics": ["физик", "механик", "оптик"],
          "math": ["математ", "алгебр", "геометр"], "code": ["rust", "python", "код", "программ", "ии", "нейро", "электрон", "arduino", "stm32"],
          "university": ["нгу", "мгу", "мфти", "мифи", "мгту", "спбгу", "вуз", "университет", "егэ", "огэ"],
          "science": ["наук", "журнал", "квант", "исследов"]}

def _stem(w):
    for e in ["иями", "ами", "ями", "ость", "ов", "ев", "ой", "ей", "ий", "ый", "ая", "яя", "ое", "ее", "ые", "ие", "ом", "ем", "ам", "у", "ю", "ь", "я", "и", "ы", "а", "е"]:
        if w.endswith(e) and len(w) - len(e) >= 4: return w[:-len(e)]
    return w
def _tokens(q): return [t for t in re.split(r"[^a-zа-яё0-9]+", q.lower()) if len(t) > 2]
def _expand(toks):
    out = set(toks)
    for t in toks:
        for k, v in SYN.items():
            if t.startswith(_stem(k)) or k.startswith(t): out.update(v)
    return {_stem(x) for x in out if len(_stem(x)) >= 3}
def relevance(q, text):
    st = _expand(_tokens(q))
    if not st: return 0.5
    words = re.split(r"[^a-zа-яё0-9]+", (text or "").lower())
    hit = sum(1 for w in words if any(w.startswith(s) for s in st))
    return min(1.0, hit / max(6, len(words) * 0.25))
def _fetch(url):
    t0 = time.time()
    try:
        r = HTTP.get(url, timeout=T)
        if r.status_code == 200: return r.text, round(time.time() - t0, 2)
    except Exception: pass
    return "", round(time.time() - t0, 2)
def _parse_date(s):
    if not s: return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try: return time.mktime(time.strptime(s.strip()[:10], fmt))
        except Exception: pass
    m = re.search(r"\d{4}-\d{2}-\d{2}", s)
    if m:
        try: return time.mktime(time.strptime(m.group(0), "%Y-%m-%d"))
        except Exception: pass
    return None
def _families(q):
    toks = _tokens(q); fams = set()
    for f, keys in FAMKEY.items():
        if any(any(t.startswith(_stem(k)) or k.startswith(t) for k in keys) for t in toks): fams.add(f)
    doms = []
    for f in fams:
        for d in SEEDS.get(f, []):
            if d not in doms: doms.append(d)
    return doms
def _ddg(q):
    h, _ = _fetch("https://html.duckduckgo.com/html/?q=" + quote_plus(q)); out = []
    if not h: return out
    for a in BeautifulSoup(h, "html.parser").select("a.result__a[href]"):
        u = a["href"]
        if "uddg=" in u: u = unquote(u.split("uddg=")[1].split("&")[0])
        if u.startswith("http"): out.append(u)
    return out
def _ddglite(q):
    h, _ = _fetch("https://lite.duckduckgo.com/lite/?q=" + quote_plus(q)); out = []
    if not h: return out
    for a in BeautifulSoup(h, "html.parser").select("a.result-link[href]"):
        if a["href"].startswith("http"): out.append(a["href"])
    return out
def _mojeek(q):
    h, _ = _fetch("https://www.mojeek.com/search?q=" + quote_plus(q)); out = []
    if not h: return out
    s = BeautifulSoup(h, "html.parser")
    for a in s.select("a.title[href]") or s.select("h2 a[href]"):
        if a["href"].startswith("http"): out.append(a["href"])
    return out
def _bing(q):
    h, _ = _fetch("https://www.bing.com/search?q=" + quote_plus(q)); out = []
    if not h: return out
    for a in BeautifulSoup(h, "html.parser").select("li.b_algo h2 a[href]"):
        if a["href"].startswith("http"): out.append(a["href"])
    return out
def backend_urls(q, limit=12):
    seen, out = set(), []
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = [ex.submit(f, q) for f in (_mojeek, _ddglite, _ddg, _bing)]
        for fu in as_completed(futs, timeout=9):
            try:
                for u in fu.result():
                    u = u.split("#")[0]; dom = urlparse(u).netloc
                    if not dom or any(x in dom for x in ["duckduckgo", "bing.", "mojeek", "microsoft", "youtube", "facebook", "vk.", "instagram"]): continue
                    if u in seen: continue
                    seen.add(u); out.append(u)
            except Exception: pass
    return out[:limit]
def find_feed(html, base):
    for l in BeautifulSoup(html, "html.parser").select("link[rel=alternate]"):
        t = (l.get("type") or "").lower()
        if "rss" in t or "atom" in t or "xml" in t:
            h = l.get("href")
            if h: return urljoin(base, h)
    return None
def extract_articles(html, base, limit=25):
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "nav", "header", "footer", "aside"]): t.decompose()
    out, seen = [], set()
    for a in soup.select("a[href]"):
        title = a.get_text(" ", strip=True)
        if not title or len(title) < 12 or len(title) > 220: continue
        h = a["href"]
        if h.startswith(("#", "javascript", "mailto")): continue
        if any(x in h.lower() for x in ["login", "register", "tag/", "author", "comment"]): continue
        u = urljoin(base, h)
        if u in seen: continue
        seen.add(u)
        date = None; par = a.find_parent(["li", "article", "div"])
        if par:
            tm = par.find("time")
            if tm and tm.get("datetime"): date = _parse_date(tm["datetime"])
            if not date:
                m = re.search(r"\d{2}\.\d{2}\.\d{4}|\d{4}-\d{2}-\d{2}", par.get_text(" ", strip=True))
                if m: date = _parse_date(m.group(0))
        snip = ""
        if par:
            p = par.find("p")
            if p: snip = p.get_text(" ", strip=True)[:220]
        out.append({"title": title, "link": u, "snippet": snip, "pub_time": date})
        if len(out) >= limit: break
    return out
def probe_site(url, hint=None):
    text, lat = _fetch(url)
    if not text: return None
    o = urlparse(url); origin = o.scheme + "://" + o.netloc
    feed = url if hint == "rss" else find_feed(text, url)
    sm_locs = []
    sm, _ = _fetch(origin + "/sitemap.xml")
    if sm: sm_locs = re.findall(r"<loc>(.*?)</loc>", sm)[:40]
    arts = extract_articles(text, url); page = url
    if len(arts) < 3 and not feed:
        for path in ["/news", "/articles", "/tasks", "/problems", "/blog", "/olympiad"]:
            t2, _ = _fetch(origin + path)
            if t2:
                a2 = extract_articles(t2, origin + path)
                if len(a2) > len(arts): arts, page, text = a2, origin + path, t2
                if len(arts) >= 3: break
    volume = len(arts) + len(sm_locs)
    sample = text[:4000] + " " + " ".join(a["title"] for a in arts)
    cyr = sum(1 for ch in sample if "а" <= ch <= "я" or ch == "ё")
    lang = cyr / max(1, len(sample))
    dates = [a["pub_time"] for a in arts if a["pub_time"]]
    now = time.time()
    fresh = (sum(1 for d in dates if now - d < 45 * 86400) / len(dates)) if dates else (0.6 if sm_locs else 0.4)
    kind = "rss" if feed else ("html" if len(arts) >= 2 else ("sitemap" if len(sm_locs) >= 5 else None))
    if not kind: return None
    return {"url": feed or page, "kind": kind, "domain": re.sub(r"^www\.", "", o.netloc),
            "latency": lat, "volume": volume, "fresh": fresh, "lang": lang, "sample": sample,
            "sitemap": sm_locs[:12]}
def score_site(q, p):
    rel = relevance(q, p["sample"])
    struct = min(1.0, p["volume"] / 25)
    want_ru = any("а" <= ch <= "я" for ch in q.lower())
    lang_ok = 1 if (not want_ru or p["lang"] > 0.1) else 0
    reli = 1 if p["latency"] < 3 else 0.5
    return round(0.4 * rel + 0.2 * p["fresh"] + 0.2 * struct + 0.1 * lang_ok + 0.1 * reli, 3)
def discover_sources(q, limit=6):
    cands = [(u, None) for u in backend_urls(q)]
    cands += [("https://" + d + "/", None) for d in _families(q)]
    cands += [("https://habr.com/ru/rss/search/?q=" + quote_plus(q) + "&fl=ru", "rss"),
              ("https://www.bing.com/news/search?q=" + quote_plus(q) + "&format=rss", "rss")]
    seen, uniq = set(), []
    for u, h in cands:
        if u not in seen: seen.add(u); uniq.append((u, h))
    acc = {}
    def one(u, h):
        p = probe_site(u, h)
        if not p: return None
        s = score_site(q, p)
        if s < 0.22: return None
        p["score"] = s; p["rel"] = round(relevance(q, p["sample"]), 2)
        p["entries"] = p["volume"]; p["cadence"] = 24
        return p
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(one, u, h) for u, h in uniq[:18]]
        try:
            for fu in as_completed(futs, timeout=15):
                p = fu.result()
                if p and p["url"] not in acc: acc[p["url"]] = p
        except Exception: pass
    return sorted(acc.values(), key=lambda x: -x["score"])[:limit]
def _post(title, link, source, snippet, ts, interest, archival):
    return {"title": title[:160], "link": link, "source": source, "snippet": (snippet or "Материалы по интересу")[:220],
            "image": "", "pub_time": ts or time.time(), "interest": interest, "archival": archival}
def read_stream(src):
    kind = src.get("kind", "rss"); url = src["url"]; limit = src.get("limit", 15)
    interest = src.get("interest", ""); source = src.get("source", url); out = []
    if kind == "rss":
        text, _ = _fetch(url)
        if text:
            for e in feedparser.parse(text).entries[:limit]:
                ts = time.time()
                if getattr(e, "published_parsed", None):
                    try: ts = time.mktime(e.published_parsed)
                    except Exception: pass
                out.append(_post(e.get("title", "Без заголовка"), e.get("link", "#"), source,
                                 re.sub(r"<.*?>", "", e.get("summary", "")), ts, interest, False))
        if out: return out
    if kind == "sitemap":
        o = urlparse(url); sm, _ = _fetch(o.scheme + "://" + o.netloc + "/sitemap.xml")
        locs = re.findall(r"<loc>(.*?)</loc>", sm)[:limit] if sm else []
        def one(u):
            t, _ = _fetch(u)
            if not t: return None
            s = BeautifulSoup(t, "html.parser")
            ti = s.find("title"); desc = s.find("meta", attrs={"name": "description"})
            lm = s.find("lastmod")
            return _post(ti.get_text(strip=True) if ti else u, u, source,
                         desc.get("content", "") if desc else "", _parse_date(lm.get_text()) if lm else None, interest, True)
        with ThreadPoolExecutor(max_workers=6) as ex:
            for r in ex.map(one, locs):
                if r: out.append(r)
        return out
    text, _ = _fetch(url)
    if not text: return out
    arts = extract_articles(text, url, limit=limit)
    for a in arts:
        a["score"] = relevance(interest or source, a["title"] + " " + a["snippet"])
    arts.sort(key=lambda x: -x["score"])
    for a in arts:
        if a["score"] < 0.08: continue
        out.append(_post(a["title"], a["link"], source, a["snippet"], a["pub_time"], interest, True))
    return out
