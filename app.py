import os, json, sqlite3
from flask import Flask, request, jsonify, send_from_directory, Response

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "state.db")
app = Flask(__name__, template_folder="templates", static_folder=None)

MANIFEST = {
    "name": "EngFeed · личная лента",
    "short_name": "EngFeed",
    "start_url": "/",
    "scope": "/",
    "display": "standalone",
    "background_color": "#0D1117",
    "theme_color": "#0D1117",
    "orientation": "portrait-primary",
    "icons": [
        {"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"},
        {"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "maskable"},
    ],
}

ICON_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"><rect width="512" height="512" rx="96" fill="#0D1117"/><rect x="48" y="48" width="416" height="416" rx="72" fill="#23282E"/><rect x="48" y="48" width="416" height="40" rx="20" fill="#3A4149"/><rect x="96" y="236" width="320" height="40" rx="20" fill="#8A939E"/><rect x="96" y="238" width="320" height="12" rx="6" fill="#DCE3EA"/><ellipse cx="256" cy="256" rx="150" ry="40" fill="#4CC3FF" opacity=".35"/><rect x="236" y="120" width="40" height="120" rx="12" fill="#F5C400"/><rect x="96" y="330" width="320" height="16" rx="8" fill="#FF6A00"/><g fill="#B87333"><circle cx="96" cy="96" r="14"/><circle cx="416" cy="96" r="14"/><circle cx="96" cy="416" r="14"/><circle cx="416" cy="416" r="14"/></g></svg>'

SW_JS = """
var CACHE='engfeed-v1';
self.addEventListener('install',function(e){
  e.waitUntil(caches.open(CACHE).then(function(c){return c.addAll(['/','/manifest.webmanifest','/icon.svg']);}).then(function(){return self.skipWaiting();}));
});
self.addEventListener('activate',function(e){
  e.waitUntil(caches.keys().then(function(ks){return Promise.all(ks.filter(function(k){return k!==CACHE;}).map(function(k){return caches.delete(k);}));}).then(function(){return self.clients.claim();}));
});
self.addEventListener('fetch',function(e){
  if(e.request.method!=='GET') return;
  var url=new URL(e.request.url);
  if(e.request.mode==='navigate'){
    e.respondWith(fetch(e.request).then(function(r){var copy=r.clone();caches.open(CACHE).then(function(c){c.put('/',copy);});return r;}).catch(function(){return caches.match('/');}));
    return;
  }
  if(url.pathname==='/api/state'){
    e.respondWith(fetch(e.request).then(function(r){var copy=r.clone();caches.open(CACHE).then(function(c){c.put(e.request,copy);});return r;}).catch(function(){return caches.match(e.request);}));
    return;
  }
  if(url.origin===location.origin){
    e.respondWith(caches.match(e.request).then(function(hit){return hit||fetch(e.request).then(function(r){var copy=r.clone();caches.open(CACHE).then(function(c){c.put(e.request,copy);});return r;});}));
  }
});
"""


def db():
    c = sqlite3.connect(DB, timeout=10)
    c.execute("CREATE TABLE IF NOT EXISTS state(k TEXT PRIMARY KEY, v TEXT)")
    return c


@app.route("/")
def index():
    resp = send_from_directory(os.path.join(BASE, "templates"), "index.html")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/manifest.webmanifest")
def manifest():
    return Response(json.dumps(MANIFEST, ensure_ascii=False), mimetype="application/manifest+json")


@app.route("/icon.svg")
def icon():
    return Response(ICON_SVG, mimetype="image/svg+xml")


@app.route("/sw.js")
def sw():
    return Response(SW_JS, mimetype="application/javascript",
                    headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-store"})


@app.route("/api/state", methods=["GET", "PUT"])
def state():
    if request.method == "GET":
        c = db()
        try:
            row = c.execute("SELECT v FROM state WHERE k='me'").fetchone()
        finally:
            c.close()
        return jsonify(json.loads(row[0]) if row else {})
    data = request.get_json(force=True, silent=True) or {}
    c = db()
    try:
        c.execute("INSERT INTO state(k,v) VALUES('me',?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                  (json.dumps(data, ensure_ascii=False),))
        c.commit()
    finally:
        c.close()
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
