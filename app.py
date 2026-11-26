import os, json, sqlite3
from flask import Flask, request, jsonify, send_from_directory

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "state.db")
app = Flask(__name__, template_folder="templates", static_folder=None)


def db():
    c = sqlite3.connect(DB, timeout=10)
    c.execute("CREATE TABLE IF NOT EXISTS state(k TEXT PRIMARY KEY, v TEXT)")
    return c


@app.route("/")
def index():
    resp = send_from_directory(os.path.join(BASE, "templates"), "index.html")
    resp.headers["Cache-Control"] = "no-store"
    return resp


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
