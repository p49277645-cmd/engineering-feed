# globalsearch.py — глобальный мульти-поисковый читатель + умное ранжирование
import os, re, time, math, logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote_plus, urlparse, urljoin
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("engfeed.global")
HTTP = requests.Session()
HTTP.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8",
})
T = (2.0, 4.0)

SYN = {
    "олимпиада": ["олимпиад", "задач", "тур", "вариант", "решени"],
    "физика": ["физик", "механик", "оптик", "электрич", "термодинам"],
    "математика": ["математ", "алгебр", "геометр", "интеграл", "производн"],
    "ии": ["нейросет", "машинн", "ml", "ai", "deep"],
    "rust": ["rust", "cargo", "borrow", "ownership"],
}

def _stem(w):
    for e in ["иями", "ами", "ями", "ость", "ов", "ев", "ой", "ей", "ий", "ый", "ая", "яя", "ое", "ее", "ые", "ие", "ом", "ем", "ам", "у", "ю", "ь", "я", "и", "ы", "а", "е"]:
        if w.endswith(e) and len(w) - len(e) >= 4:
            return w[:-len(e)]
    return w

def _tokens(q):
    return [t for t in re.split(r"[^a-zа-яё0-9]+", q.lower()) if len(t) > 2]

def _expand(toks):
    out = set(toks)
    for t in toks:
        for k, v in SYN.items():
            if t.startswith(_stem(k)) or k.startswith(t):
                out.update(v)
    return {_stem(x) for x in out if len(_stem(x)) >= 3}

def relevance(q, text):
    stems = _expand(_tokens(q))
    if not stems:
        return 0.5
    words = re.split(r"[^a-zа-яё0-9]+", (text or "").lower())
    hit = sum(1 for w in words if any(w.startswith(s) for s in stems))
    return min(1.0, hit / max(6, len(words) * 0.25))

def _get(url):
    try:
        r = HTTP.get(url, timeout=T)
        if r.status_code == 200:
            return r.text
    except Exception:
        pass
    return ""

def _ddg(q):
    html = _get("https://html.duckduckgo.com/html/?q=" + quote_plus(q))
    out = []
    if not html:
        return out
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.select("a.result__a[href]"):
        href = a["href"]
        if "uddg=" in href:
            from urllib.parse import unquote
            href = unquote(href.split("uddg=")[1].split("&")[0])
        if href.startswith("http"):
            out.append({"url": href, "title": a.get_text(" ", strip=True), "snippet": ""})
    return out

def _bing(q):
    html = _get("https://www.bing.com/search?q=" + quote_plus(q))
    out = []
    if not html:
        return out
    soup = BeautifulSoup(html, "html.parser")
    for li in soup.select("li.b_algo"):
        a = li.select_one("h2 a[href]")
        if not a:
            continue
        p = li.select_one("p")
        out.append({"url": a["href"], "title": a.get_text(" ", strip=True),
                    "snippet": p.get_text(" ", strip=True) if p else ""})
    return out

def global_urls(q, limit=14):
    seen, out = set(), []
    with ThreadPoolExecutor(max_workers=2) as ex:
        futs = [ex.submit(_ddg, q), ex.submit(_bing, q)]
        for f in as_completed(futs, timeout=8):
            try:
                for it in f.result():
                    u = it["url"].split("#")[0]
                    dom = urlparse(u).netloc
                    if not dom or any(x in dom for x in ["duckduckgo", "bing.", "microsoft", "youtube", "facebook", "vk.", "instagram"]):
                        continue
                    if u in seen:
                        continue
                    seen.add(u)
                    out.append(it)
            except Exception:
                pass
    return out[:limit]

def _parse_date(s):
    if not s:
        return None
    s = s.strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return time.mktime(time.strptime(s[:10], fmt))
        except Exception:
            pass
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return time.mktime(time.strptime(m.group(0), "%Y-%m-%d"))
        except Exception:
            pass
    return None

def find_feed_link(html, base):
    soup = BeautifulSoup(html, "html.parser")
    for l in soup.select("link[rel=alternate]"):
        t = (l.get("type") or "").lower()
        if "rss" in t or "atom" in t or "xml" in t:
            href = l.get("href")
            if href:
                return urljoin(base, href)
    return None

def extract_articles(html, base, limit=25):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
        tag.decompose()
    out, seen = [], set()
    for a in soup.select("a[href]"):
        title = a.get_text(" ", strip=True)
        if not title or len(title) < 18 or len(title) > 220:
            continue
        href = a["href"]
        if href.startswith(("#", "javascript", "mailto")):
            continue
        low = href.lower()
        if any(x in low for x in ["login", "register", "tag/", "category", "author", "page/", "comment"]):
            continue
        url = urljoin(base, href)
        if url in seen:
            continue
        seen.add(url)
        date = None
        t = a.find_parent().find("time") if a.find_parent() else None
        if t and t.get("datetime"):
            date = _parse_date(t["datetime"])
        if not date:
            m = re.search(r"\d{2}\.\d{2}\.\d{4}|\d{4}-\d{2}-\d{2}", a.find_parent().get_text(" ", strip=True) if a.find_parent() else "")
            if m:
                date = _parse_date(m.group(0))
        par = a.find_parent(["li", "article", "div"])
        snip = ""
        if par:
            p = par.find("p")
            if p:
                snip = p.get_text(" ", strip=True)[:220]
        out.append({"title": title, "link": url, "snippet": snip, "pub_time": date})
        if len(out) >= limit:
            break
    return out

def read_source(url):
    """Возвращает ('rss', feed_url) или ('html', url, articles)."""
    html = _get(url)
    if not html:
        return None
    feed = find_feed_link(html, url)
    if feed:
        return ("rss", feed)
    arts = extract_articles(html, url)
    if len(arts) >= 3:
        return ("html", url, arts)
    return None

def semantic_scores(q, texts):
    """Опциональный нейронный хук: внешний embeddings-API (OpenAI-совместимый).
    Включается только если заданы EMBED_URL и EMBED_KEY. Иначе None (лексика)."""
    url = os.environ.get("EMBED_URL")
    key = os.environ.get("EMBED_KEY")
    if not url or not key or not texts:
        return None
    try:
        r = requests.post(url, headers={"Authorization": "Bearer " + key},
                          json={"input": [q] + texts, "model": os.environ.get("EMBED_MODEL", "text-embedding-3-small")},
                          timeout=(3, 8))
        data = r.json()["data"]
        vecs = [d["embedding"] for d in data]
        qv, tv = vecs[0], vecs[1:]
        def cos(a, b):
            na = math.sqrt(sum(x * x for x in a)); nb = math.sqrt(sum(x * x for x in b))
            return sum(x * y for x, y in zip(a, b)) / (na * nb + 1e-9)
        return [cos(qv, v) for v in tv]
    except Exception as e:
        logger.warning(f"semantic hook failed: {e}")
        return None

def rank_items(q, items):
    texts = [(it.get("title", "") + " " + it.get("snippet", "")) for it in items]
    sem = semantic_scores(q, texts)
    for i, it in enumerate(items):
        lex = relevance(q, texts[i])
        it["score"] = round(0.6 * lex + 0.4 * sem[i], 3) if sem else round(lex, 3)
    items.sort(key=lambda x: -x["score"])
    return items
