# engine.py v2 — один прогон, учёт новизны по источникам, ротация застоя
import os, time, sqlite3, threading, logging
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutTimeout
from contextlib import contextmanager
import interests

DB_PATH = os.environ.get("DB_PATH", "engfeed.db")
log = logging.getLogger("engfeed.engine")
_lock = threading.Lock()
_thread_alive = False
DEADLINE = 20
COOLDOWN = 5

@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def init_engine(base_urls):
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS source_health(
            url TEXT PRIMARY KEY, fails INTEGER DEFAULT 0, rf_ok INTEGER DEFAULT 1,
            checked_at REAL DEFAULT 0, latency REAL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS refresh_runs(
            id INTEGER PRIMARY KEY CHECK (id=1),
            running INTEGER DEFAULT 0, started_at REAL DEFAULT 0, finished_at REAL DEFAULT 0,
            total INTEGER DEFAULT 0, ok INTEGER DEFAULT 0, failed INTEGER DEFAULT 0,
            last_error TEXT DEFAULT '');
        INSERT OR IGNORE INTO refresh_runs(id) VALUES (1);
        """)
        for u in base_urls:
            c.execute("INSERT OR IGNORE INTO source_health(url) VALUES (?)", (u,))

def report(url, ok, latency=0.0):
    now = time.time()
    with db() as c:
        c.execute("INSERT OR IGNORE INTO source_health(url) VALUES (?)", (url,))
        if ok:
            c.execute("UPDATE source_health SET fails=0, rf_ok=1, checked_at=?, latency=? WHERE url=?", (now, latency, url))
        else:
            c.execute("UPDATE source_health SET fails=fails+1, checked_at=? WHERE url=?", (now, url))
            c.execute("UPDATE source_health SET rf_ok=0 WHERE url=? AND fails>=3", (url,))

def live(url):
    with db() as c:
        r = c.execute("SELECT rf_ok, fails FROM source_health WHERE url=?", (url,)).fetchone()
    return True if r is None else (r["rf_ok"] == 1 and r["fails"] < 3)

def status():
    with db() as c:
        r = c.execute("SELECT * FROM refresh_runs WHERE id=1").fetchone()
    return dict(r) if r else {"running": 0, "started_at": 0, "total": 0, "ok": 0, "failed": 0, "finished_at": 0, "last_error": ""}

def _set_run(**kw):
    sets = ", ".join(k + "=?" for k in kw)
    with db() as c:
        c.execute("UPDATE refresh_runs SET " + sets + " WHERE id=1", tuple(kw.values()))

def start(sources, parse_fn, scrape_fn, enrich_fn, save_fn, force=False):
    global _thread_alive
    now = time.time()
    with db() as c:
        if force:
            cur = c.execute(
                "UPDATE refresh_runs SET running=1, started_at=?, finished_at=0, total=?, ok=0, failed=0, last_error='' "
                "WHERE id=1 AND running=0", (now, len(sources)))
        else:
            cur = c.execute(
                "UPDATE refresh_runs SET running=1, started_at=?, finished_at=0, total=?, ok=0, failed=0, last_error='' "
                "WHERE id=1 AND running=0 AND finished_at < ?", (now, len(sources), now - COOLDOWN))
        won = cur.rowcount == 1
    if not won:
        return False
    with _lock:
        if _thread_alive:
            return False
        _thread_alive = True
    threading.Thread(target=_run, args=(sources, parse_fn, scrape_fn, enrich_fn, save_fn), daemon=True).start()
    return True

def _run(sources, parse_fn, scrape_fn, enrich_fn, save_fn):
    global _thread_alive
    ok = failed = 0
    items = []
    name_to_url = {s["source"]: s["url"] for s in sources}
    try:
        with db() as c:
            known = {r["link"] for r in c.execute("SELECT link FROM posts").fetchall()}
    except Exception:
        known = set()
    new_per_url = {}
    ex = ThreadPoolExecutor(max_workers=8)
    futs = {}
    try:
        for s in sources:
            fn = parse_fn if s["type"] == "rss" else scrape_fn
            futs[ex.submit(fn, s)] = s
        started = status()["started_at"] or time.time()
        remaining = max(1.0, DEADLINE - (time.time() - started))
        try:
            for fut in as_completed(futs, timeout=remaining):
                s = futs[fut]
                t0 = time.time()
                try:
                    r = fut.result(timeout=1)
                except Exception:
                    r = None
                if r:
                    fresh = [it for it in r if it.get("link") not in known]
                    for it in fresh:
                        known.add(it.get("link"))
                    u = name_to_url.get(s["source"])
                    if u:
                        new_per_url[u] = new_per_url.get(u, 0) + len(fresh)
                    items.extend(r)
                    ok += 1
                    report(s["url"], True, round(time.time() - t0, 2))
                else:
                    failed += 1
                    report(s["url"], False)
                _set_run(ok=ok, failed=failed)
        except FutTimeout:
            for fut, s in futs.items():
                if not fut.done():
                    failed += 1
                    report(s["url"], False)
            _set_run(failed=failed, last_error="deadline 20s: часть источников пропущена")
        for u, n in new_per_url.items():
            try:
                interests.report_novelty(u, n)
            except Exception as e:
                log.warning(f"novelty report failed: {e}")
        for s in sources:
            u = s["url"]
            if u not in new_per_url and s.get("interest"):
                try:
                    interests.report_novelty(u, 0)
                except Exception:
                    pass
        enriched = enrich_fn(items)
        save_fn(enriched)
    except Exception as e:
        log.warning(f"refresh run error: {e}")
        _set_run(last_error=str(e)[:200])
    finally:
        try:
            ex.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            ex.shutdown(wait=False)
        _set_run(running=0, finished_at=time.time(), ok=ok, failed=failed)
        _thread_alive = False

def warm_if_needed(sources, parse_fn, scrape_fn, enrich_fn, save_fn, max_age=6*3600):
    try:
        with db() as c:
            r = c.execute("SELECT COUNT(*) n, MAX(fetched_at) m FROM posts").fetchone()
        if r["n"] and (time.time() - (r["m"] or 0)) < max_age:
            return
    except Exception:
        pass
    start(sources, parse_fn, scrape_fn, enrich_fn, save_fn)
