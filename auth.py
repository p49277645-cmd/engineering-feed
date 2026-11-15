# auth.py — регистрация/вход/выход, защита от перебора, серверные лимиты в SQLite
import os, time, secrets, sqlite3, hashlib
from contextlib import contextmanager
from flask import Blueprint, request, jsonify, session
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError
from nickguard import validate_nick, nick_key

auth_bp = Blueprint("auth", __name__)
DB_PATH = os.environ.get("DB_PATH", "engfeed.db")
ph = PasswordHasher(time_cost=3, memory_cost=32 * 1024, parallelism=2)  # Argon2id, ~0.1с
DUMMY_HASH = ph.hash("timing-equalizer-dummy")  # чтобы отсутствие юзера не палиться по времени

COMMON_PW = {"password", "password1", "password123", "qwerty", "qwerty123",
             "12345678", "123456789", "1234567890", "iloveyou", "admin123",
             "letmein", "welcome", "monkey", "dragon", "1q2w3e4r", "1qaz2wsx",
             "пароль", "пароль123", "йцукен", "qwertyuiop"}

@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def init_auth(app):
    # SECRET_KEY: env или локальный файл (одинаковый для всех воркеров gunicorn!)
    key = os.environ.get("SECRET_KEY")
    if not key:
        p = os.path.join(os.path.dirname(__file__), ".secret_key")
        if not os.path.exists(p):
            with open(p, "w") as f:
                f.write(secrets.token_hex(32))
        key = open(p).read().strip()
    app.config.update(
        SECRET_KEY=key,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "0") == "1",
        PERMANENT_SESSION_LIFETIME=7 * 86400,
    )
    with db() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY, nick TEXT, nick_key TEXT UNIQUE,
            pass_hash TEXT, created_at REAL, failed INTEGER DEFAULT 0,
            locked_until REAL DEFAULT 0, role TEXT DEFAULT 'user');
        CREATE TABLE IF NOT EXISTS attempts(key TEXT, ts REAL);
        CREATE INDEX IF NOT EXISTS idx_attempts ON attempts(key, ts);
        """)

def _client_ip():
    return request.headers.get("X-Forwarded-For", request.remote_addr or "?").split(",")[0].strip()

def rate_ok(key, limit, window):
    now = time.time()
    with db() as c:
        c.execute("DELETE FROM attempts WHERE ts < ?", (now - window,))
        n = c.execute("SELECT COUNT(*) FROM attempts WHERE key=?", (key,)).fetchone()[0]
        if n >= limit:
            return False
        c.execute("INSERT INTO attempts VALUES (?,?)", (key, now))
        return True

def csrf_ok():
    tok = session.get("csrf")
    return bool(tok) and request.headers.get("X-CSRF-Token") == tok

@auth_bp.get("/api/me")
def me():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(24)
    session.permanent = True
    return jsonify({"nick": session.get("nick"), "csrf": session["csrf"]})

@auth_bp.post("/api/register")
def register():
    if not csrf_ok():
        return jsonify({"error": "csrf"}), 403
    if not rate_ok("reg:" + _client_ip(), 10, 3600):
        return jsonify({"error": "rate"}), 429
    d = request.get_json(silent=True) or {}
    nick, pw = (d.get("nick") or "").strip(), d.get("password") or ""
    ok, code = validate_nick(nick)
    if not ok:
        return jsonify({"error": "nick_" + code}), 400
    if not 10 <= len(pw) <= 128:
        return jsonify({"error": "pw_length"}), 400
    if pw.lower() in COMMON_PW:
        return jsonify({"error": "pw_common"}), 400
    try:
        with db() as c:
            c.execute("INSERT INTO users(nick, nick_key, pass_hash, created_at) VALUES (?,?,?,?)",
                      (nick, nick_key(nick), ph.hash(pw), time.time()))
    except sqlite3.IntegrityError:
        return jsonify({"error": "nick_taken"}), 409
    session.clear()
    session["nick"] = nick
    session["csrf"] = secrets.token_urlsafe(24)
    session.permanent = True
    return jsonify({"nick": nick})

@auth_bp.post("/api/login")
def login():
    if not csrf_ok():
        return jsonify({"error": "csrf"}), 403
    ip = _client_ip()
    if not rate_ok("ip:" + ip, 30, 900):
        return jsonify({"error": "rate"}), 429
    d = request.get_json(silent=True) or {}
    nick, pw = (d.get("nick") or "").strip(), d.get("password") or ""
    if not rate_ok("nick:" + nick_key(nick), 10, 900):
        return jsonify({"error": "rate"}), 429
    with db() as c:
        u = c.execute("SELECT * FROM users WHERE nick_key=?", (nick_key(nick),)).fetchone()
    # одинаковая ошибка и время ответа — не раскрываем, существует ли ник
    if u is None:
        try: ph.verify(DUMMY_HASH, pw)
        except VerificationError: pass
        return jsonify({"error": "bad_credentials"}), 401
    if u["locked_until"] > time.time():
        try: ph.verify(DUMMY_HASH, pw)
        except VerificationError: pass
        return jsonify({"error": "bad_credentials"}), 401
    try:
        ph.verify(u["pass_hash"], pw)
    except VerifyMismatchError:
        with db() as c:
            failed = u["failed"] + 1
            lock = time.time() + min(2 ** failed, 900) if failed >= 5 else 0
            c.execute("UPDATE users SET failed=?, locked_until=? WHERE id=?", (failed, lock, u["id"]))
        return jsonify({"error": "bad_credentials"}), 401
    with db() as c:
        c.execute("UPDATE users SET failed=0, locked_until=0 WHERE id=?", (u["id"],))
        if ph.check_needs_rehash(u["pass_hash"]):
            c.execute("UPDATE users SET pass_hash=? WHERE id=?", (ph.hash(pw), u["id"]))
    session.clear()
    session["nick"] = u["nick"]
    session["csrf"] = secrets.token_urlsafe(24)
    session.permanent = True
    return jsonify({"nick": u["nick"]})

@auth_bp.post("/api/logout")
def logout():
    session.clear()
    return jsonify({"ok": True})
AUTH_FRAGMENT = """
<style>
.auth-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.75);backdrop-filter:blur(6px);z-index:200;align-items:center;justify-content:center;}
.auth-overlay.open{display:flex;}
.auth-box{width:100%;max-width:420px;background:var(--panel);border:1px solid var(--border);border-top:3px solid var(--accent-sand);padding:32px;clip-path:polygon(0 0,100% 0,100% calc(100% - 24px),calc(100% - 24px) 100%,0 100%);}
.auth-box h2{font-family:'JetBrains Mono',monospace;font-size:20px;margin-bottom:20px;color:var(--accent-sand);}
.auth-box h2::before{content:'// ';color:var(--accent-coral);}
.auth-tabs{display:flex;gap:8px;margin-bottom:20px;}
.auth-tab{flex:1;height:36px;border:1px solid var(--border);background:var(--card);color:var(--t2);font-family:'JetBrains Mono',monospace;font-size:12px;font-weight:700;cursor:pointer;}
.auth-tab.active{background:var(--accent-sand);color:var(--bg);border-color:var(--accent-sand);}
.auth-field{margin-bottom:14px;}
.auth-field label{display:block;font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:var(--t3);font-family:'JetBrains Mono',monospace;margin-bottom:6px;}
.auth-field input{width:100%;height:42px;background:var(--card);border:1px solid var(--border);color:var(--t1);padding:0 12px;font-family:'JetBrains Mono',monospace;font-size:14px;outline:none;}
.auth-field input:focus{border-color:var(--accent-sand);}
.auth-hint{font-size:11px;color:var(--t3);margin-top:4px;font-family:'JetBrains Mono',monospace;}
.auth-err{display:none;background:rgba(224,122,95,.12);border:1px solid var(--accent-coral);color:var(--accent-coral);padding:10px;font-size:12px;font-family:'JetBrains Mono',monospace;margin-bottom:14px;}
.auth-submit{width:100%;height:44px;background:var(--accent-sand);border:none;color:var(--bg);font-weight:800;font-family:'JetBrains Mono',monospace;font-size:13px;cursor:pointer;}
.auth-submit:hover{background:var(--accent-coral);}
.auth-submit:disabled{opacity:.5;cursor:wait;}
.auth-close{position:absolute;top:12px;right:12px;width:30px;height:30px;border:1px solid var(--border);background:var(--card);color:var(--t2);cursor:pointer;}
.avatar-nick{font-family:'JetBrains Mono',monospace;font-size:12px;color:var(--accent-sand);max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
</style>
<div class="auth-overlay" id="authOverlay">
  <div class="auth-box" style="position:relative">
    <button class="auth-close" id="authClose">✕</button>
    <h2 id="authTitle">Вход</h2>
    <div class="auth-tabs">
      <button class="auth-tab active" id="tabLogin">Вход</button>
      <button class="auth-tab" id="tabReg">Регистрация</button>
    </div>
    <div class="auth-err" id="authErr"></div>
    <div id="authLogged" style="display:none">
      <p style="color:var(--t2);font-size:14px;margin-bottom:16px">Привет, <b id="authNick" style="color:var(--accent-sand)"></b></p>
      <button class="auth-submit" id="authLogout">Выйти</button>
    </div>
    <form id="authForm">
      <div class="auth-field"><label>Никнейм</label>
        <input id="authNickInput" maxlength="24" autocomplete="username" placeholder="fox_лис">
        <div class="auth-hint">3–24 символа, RU/EN, цифры, _ - .</div></div>
      <div class="auth-field"><label>Пароль</label>
        <input id="authPw" type="password" maxlength="128" autocomplete="current-password">
        <div class="auth-hint" id="pwHint">минимум 10 символов</div></div>
      <div class="auth-field" id="authPw2Wrap" style="display:none"><label>Повтори пароль</label>
        <input id="authPw2" type="password" maxlength="128" autocomplete="new-password"></div>
      <button class="auth-submit" id="authSubmit" type="submit">Войти</button>
    </form>
  </div>
</div>
<script>
(function(){
  var csrf=null, me=null, mode='login';
  var ov=document.getElementById('authOverlay');
  var ERR={nick_length:'Ник: 3–24 символа',nick_chars:'Ник: только буквы RU/EN, цифры, _ - .',
    nick_reserved:'Ник зарезервирован (admin/support/...)',nick_banned:'Ник содержит запрещённую лексику',
    nick_taken:'Ник уже занят',pw_length:'Пароль: 10–128 символов',pw_common:'Слишком популярный пароль',
    bad_credentials:'Неверный логин или пароль',rate:'Слишком много попыток, подожди',csrf:'Сессия устарела, обнови страницу'};
  function showErr(c){var e=document.getElementById('authErr');e.innerText=ERR[c]||('Ошибка: '+c);e.style.display='block';}
  function setMode(m){mode=m;
    document.getElementById('tabLogin').classList.toggle('active',m==='login');
    document.getElementById('tabReg').classList.toggle('active',m==='reg');
    document.getElementById('authPw2Wrap').style.display=m==='reg'?'block':'none';
    document.getElementById('authSubmit').innerText=m==='reg'?'Создать аккаунт':'Войти';
    document.getElementById('authTitle').innerText=m==='reg'?'Регистрация':'Вход';
    document.getElementById('authErr').style.display='none';}
  function render(){
    document.getElementById('authForm').style.display=me?'none':'block';
    document.getElementById('authLogged').style.display=me?'block':'none';
    document.querySelector('.auth-tabs').style.display=me?'none':'flex';
    if(me){document.getElementById('authNick').innerText=me;}
    document.getElementById('avatarNick').innerText=me||'';}
  fetch('/api/me').then(r=>r.json()).then(d=>{csrf=d.csrf;me=d.nick;render();});
  document.getElementById('avatarBtn').addEventListener('click',()=>{render();ov.classList.add('open');});
  document.getElementById('authClose').addEventListener('click',()=>ov.classList.remove('open'));
  ov.addEventListener('click',e=>{if(e.target===ov)ov.classList.remove('open');});
  document.getElementById('tabLogin').addEventListener('click',()=>setMode('login'));
  document.getElementById('tabReg').addEventListener('click',()=>setMode('reg'));
  document.getElementById('authPw').addEventListener('input',function(){
    var n=this.value.length,h=document.getElementById('pwHint');
    h.innerText=n<10?'минимум 10 символов':n<14?'нормально, но длиннее = лучше':'отличная длина';
    h.style.color=n<10?'var(--t3)':n<14?'var(--accent-sand)':'var(--accent-emerald)';});
  document.getElementById('authLogout').addEventListener('click',function(){
    fetch('/api/logout',{method:'POST',headers:{'X-CSRF-Token':csrf}}).then(()=>{me=null;csrf=null;
      fetch('/api/me').then(r=>r.json()).then(d=>{csrf=d.csrf;render();});});});
  document.getElementById('authForm').addEventListener('submit',function(e){
    e.preventDefault();
    var nick=document.getElementById('authNickInput').value.trim();
    var pw=document.getElementById('authPw').value;
    if(mode==='reg'&&pw!==document.getElementById('authPw2').value){showErr('pw_mismatch');ERR.pw_mismatch='Пароли не совпадают';return;}
    var btn=document.getElementById('authSubmit');btn.disabled=true;
    fetch(mode==='reg'?'/api/register':'/api/login',{method:'POST',
      headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},
      body:JSON.stringify({nick:nick,password:pw})})
    .then(r=>r.json().then(d=>({s:r.status,d:d})))
    .then(x=>{btn.disabled=false;
      if(x.s===200){me=x.d.nick;ov.classList.remove('open');render();}
      else showErr(x.d.error||'unknown');})
    .catch(()=>{btn.disabled=false;showErr('network');ERR.network='Ошибка сети';});});
})();
</script>
"""
