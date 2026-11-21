# auth.py — регистрация/вход/выход/настройки, защита от перебора
import os, time, secrets, sqlite3, logging
from contextlib import contextmanager
from flask import Blueprint, request, jsonify, session
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError
from nickguard import validate_nick, nick_key

try:
    from interests import adopt_guest_interests
except Exception:
    adopt_guest_interests = None

auth_bp = Blueprint("auth", __name__)
DB_PATH = os.environ.get("DB_PATH", "engfeed.db")
ph = PasswordHasher(time_cost=2, memory_cost=19 * 1024, parallelism=1)

_DUMMY_HASH = None
def _dummy_hash():
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = ph.hash("timing-equalizer-dummy")
    return _DUMMY_HASH

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

def _get_secret():
    key = os.environ.get("SECRET_KEY")
    if key:
        return key
    p = os.path.join(os.path.dirname(__file__), ".secret_key")
    try:
        if os.path.exists(p):
            return open(p).read().strip()
        key = secrets.token_hex(32)
        with open(p, "w") as f:
            f.write(key)
        return key
    except OSError:
        return secrets.token_hex(32)

def init_auth(app):
    app.config.update(
        SECRET_KEY=_get_secret(),
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
    data = {"nick": session.get("nick"), "csrf": session["csrf"]}
    if session.get("nick"):
        with db() as c:
            u = c.execute("SELECT id, created_at FROM users WHERE nick=?", (session["nick"],)).fetchone()
            if u:
                data["id"] = u["id"]
                data["created_at"] = u["created_at"]
    return jsonify(data)

@auth_bp.post("/api/register")
def register():
    if not csrf_ok():
        return jsonify({"error": "csrf"}), 403
    if not rate_ok("reg:" + _client_ip(), 10, 3600):
        return jsonify({"error": "rate"}), 429
    d = request.get_json(silent=True) or {}
    nick, pw = (d.get("nick") or "").strip(), d.get("password") or ""
    ok, code = validate_nick(nick)
    owner = os.environ.get("OWNER_NICK", "")
    is_owner = bool(owner) and nick.lower() == owner.lower()
    if not ok and not is_owner:
        return jsonify({"error": "nick_" + code}), 400
    if not 10 <= len(pw) <= 128:
        return jsonify({"error": "pw_length"}), 400
    if pw.lower() in COMMON_PW:
        return jsonify({"error": "pw_common"}), 400
    try:
        with db() as c:
            cur = c.execute("INSERT INTO users(nick, nick_key, pass_hash, created_at) VALUES (?,?,?,?)",
                            (nick, nick_key(nick), ph.hash(pw), time.time()))
            uid = cur.lastrowid
    except sqlite3.IntegrityError:
        return jsonify({"error": "nick_taken"}), 409
    
    # === ПЕРЕНОСИМ ИНТЕРЕСЫ ГOSTЯ В АККАУНТ ПЕРЕД ОЧИСТКОЙ ===
    if adopt_guest_interests:
        try:
            adopt_guest_interests(uid)
        except Exception as e:
            logging.warning(f"adopt_guest_interests on register failed: {e}")

    session.clear()
    session["uid"] = uid
    session["nick"] = nick
    session["csrf"] = secrets.token_urlsafe(24)
    session.permanent = True
    if is_owner:
        with db() as c:
            c.execute("UPDATE users SET role='admin' WHERE id=?", (uid,))
            
    return jsonify({"nick": nick, "id": uid, "created_at": time.time()})

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
    if u is None or u["locked_until"] > time.time():
        try:
            ph.verify(_dummy_hash(), pw)
        except VerificationError:
            pass
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
    
    # === ПЕРЕНОСИМ ИНТЕРЕСЫ ГOSTЯ В АККАУНТ ПЕРЕД ОЧИСТКОЙ (ЭТО БЫЛО ПРОПУЩЕНО!) ===
    if adopt_guest_interests:
        try:
            adopt_guest_interests(u["id"])
        except Exception as e:
            logging.warning(f"adopt_guest_interests on login failed: {e}")

    session.clear()
    session["uid"] = u["id"]
    session["nick"] = u["nick"]
    session["csrf"] = secrets.token_urlsafe(24)
    session.permanent = True
    return jsonify({"nick": u["nick"], "id": u["id"], "created_at": u["created_at"]})

@auth_bp.post("/api/logout")
def logout():
    session.clear()
    return jsonify({"ok": True})

@auth_bp.post("/api/settings")
def settings():
    if not csrf_ok() or "nick" not in session:
        return jsonify({"error": "unauthorized"}), 401
    d = request.get_json(silent=True) or {}
    action = d.get("action")
    with db() as c:
        u = c.execute("SELECT * FROM users WHERE nick=?", (session["nick"],)).fetchone()
        if not u:
            return jsonify({"error": "not_found"}), 404
        if action == "change_pass":
            pw = d.get("password", "")
            if len(pw) < 10:
                return jsonify({"error": "pw_length"}), 400
            c.execute("UPDATE users SET pass_hash=? WHERE id=?", (ph.hash(pw), u["id"]))
            return jsonify({"ok": True})
        if action == "delete":
            c.execute("DELETE FROM users WHERE id=?", (u["id"],))
            session.clear()
            return jsonify({"ok": True})
    return jsonify({"error": "unknown_action"}), 400

AUTH_FRAGMENT = """
<style>
.auth-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.85);backdrop-filter:blur(8px);z-index:200;align-items:center;justify-content:center;}
.auth-overlay.open{display:flex;}
.auth-box{width:100%;max-width:440px;background:var(--panel);border:1px solid var(--border);padding:32px;clip-path:polygon(0 0,100% 0,100% calc(100% - 24px),calc(100% - 24px) 100%,0 100%);position:relative;}
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
.settings-view{display:none;}
.settings-view.active{display:block;}
.info-row{display:flex;justify-content:space-between;padding:12px 0;border-bottom:1px solid var(--border);font-size:13px;color:var(--t2);}
.info-row span:last-child{color:var(--t1);font-family:'JetBrains Mono',monospace;}
.danger-zone{margin-top:24px;padding-top:16px;border-top:1px dashed var(--accent-coral);}
.btn-danger{width:100%;height:36px;background:transparent;border:1px solid var(--accent-coral);color:var(--accent-coral);font-family:'JetBrains Mono',monospace;font-size:12px;cursor:pointer;margin-top:8px;}
.btn-danger:hover{background:var(--accent-coral);color:var(--bg);}
</style>
<div class="auth-overlay" id="authOverlay">
  <div class="auth-box">
    <button class="auth-close" id="authClose">✕</button>
    <div id="viewAuth">
        <h2 id="authTitle">Вход</h2>
        <div class="auth-tabs">
          <button class="auth-tab active" id="tabLogin">Вход</button>
          <button class="auth-tab" id="tabReg">Регистрация</button>
        </div>
        <div class="auth-err" id="authErr"></div>
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
    <div id="viewSettings" class="settings-view">
        <h2>Настройки</h2>
        <div class="info-row"><span>Никнейм</span><span id="settNick">...</span></div>
        <div class="info-row"><span>ID пользователя</span><span id="settId">...</span></div>
        <div class="info-row"><span>Дата регистрации</span><span id="settDate">...</span></div>
        <div class="auth-field" style="margin-top:20px;"><label>Новый пароль</label>
            <input id="newPw" type="password" placeholder="Оставь пустым, если не меняешь">
        </div>
        <button class="auth-submit" id="editInterestsBtn" style="height:36px;font-size:12px;margin-bottom:8px;background:var(--card2);color:var(--accent-emerald);">Изменить интересы</button>
        <button class="auth-submit" id="saveSettingsBtn" style="height:36px;font-size:12px;">Сохранить</button>
        <div class="danger-zone">
            <div class="auth-hint" style="color:var(--accent-coral);margin-bottom:8px;">Опасная зона</div>
            <button class="btn-danger" id="deleteAccountBtn">Удалить аккаунт</button>
        </div>
        <button class="auth-submit" id="logoutBtn" style="margin-top:16px;background:var(--card2);color:var(--t2);">Выйти</button>
    </div>
  </div>
</div>
<script>
(function(){
  var csrf=null, me=null, mode='login';
  var ov=document.getElementById('authOverlay');
  var viewAuth=document.getElementById('viewAuth');
  var viewSettings=document.getElementById('viewSettings');
  var ERR={nick_length:'Ник: 3–24 символа',nick_chars:'Только буквы RU/EN, цифры, _ - .',
    nick_reserved:'Ник зарезервирован',nick_banned:'Запрещённая лексика',
    nick_taken:'Ник занят',pw_length:'Пароль: 10–128 символов',pw_common:'Слишком популярный пароль',
    bad_credentials:'Неверный логин или пароль',rate:'Слишком много попыток',
    csrf:'Сессия устарела, обнови страницу'};
  function showErr(c){var e=document.getElementById('authErr');e.innerText=ERR[c]||c;e.style.display='block';}
  function setMode(m){mode=m;
    document.getElementById('tabLogin').classList.toggle('active',m==='login');
    document.getElementById('tabReg').classList.toggle('active',m==='reg');
    document.getElementById('authPw2Wrap').style.display=m==='reg'?'block':'none';
    document.getElementById('authSubmit').innerText=m==='reg'?'Создать аккаунт':'Войти';
    document.getElementById('authTitle').innerText=m==='reg'?'Регистрация':'Вход';
    document.getElementById('authErr').style.display='none';}
  function render(data){
    me=data&&data.nick?data.nick:null;
    document.getElementById('avatarNick').innerText=me||'';
    var ob=document.getElementById('onboardingBanner');
    if(ob){ob.style.display=me?'none':'block';}
    if(me){
      viewAuth.style.display='none';
      viewSettings.classList.add('active');
      document.getElementById('settNick').innerText=data.nick;
      document.getElementById('settId').innerText='#'+(data.id||0);
      document.getElementById('settDate').innerText=new Date((data.created_at||0)*1000).toLocaleDateString();
    }else{
      viewSettings.classList.remove('active');
      viewAuth.style.display='block';
    }
  }
  function load(cb){fetch('/api/me').then(function(r){return r.json();}).then(function(d){csrf=d.csrf;cb(d);});}
  document.getElementById('avatarBtn').addEventListener('click',function(){load(function(d){render(d.nick?d:null);ov.classList.add('open');});});
  document.getElementById('authClose').addEventListener('click',function(){ov.classList.remove('open');});
  ov.addEventListener('click',function(e){if(e.target===ov)ov.classList.remove('open');});
  document.getElementById('tabLogin').addEventListener('click',function(){setMode('login');});
  document.getElementById('tabReg').addEventListener('click',function(){setMode('reg');});
  document.getElementById('authPw').addEventListener('input',function(){
    var n=this.value.length,h=document.getElementById('pwHint');
    h.innerText=n<10?'минимум 10 символов':n<14?'нормально, но длиннее = лучше':'отличная длина';
    h.style.color=n<10?'var(--t3)':n<14?'var(--accent-sand)':'var(--accent-emerald)';});
  document.getElementById('authForm').addEventListener('submit',function(e){
    e.preventDefault();
    var nick=document.getElementById('authNickInput').value.trim();
    var pw=document.getElementById('authPw').value;
    if(mode==='reg'&&pw!==document.getElementById('authPw2').value){showErr('Пароли не совпадают');return;}
    var btn=document.getElementById('authSubmit');btn.disabled=true;
    fetch(mode==='reg'?'/api/register':'/api/login',{method:'POST',
      headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},
      body:JSON.stringify({nick:nick,password:pw})})
    .then(function(r){return r.json().then(function(d){return {s:r.status,d:d};});})
    .then(function(x){btn.disabled=false;
      if(x.s===200){render(x.d);}else{showErr(x.d.error||'unknown');}})
    .catch(function(){btn.disabled=false;showErr('Ошибка сети');});});

  document.getElementById('editInterestsBtn').addEventListener('click',function(){
    ov.classList.remove('open');
    if(window.openInterests){window.openInterests();}
  });
  document.getElementById('saveSettingsBtn').addEventListener('click',function(){
    var pw=document.getElementById('newPw').value;
    if(!pw)return;
    fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},
      body:JSON.stringify({action:'change_pass',password:pw})})
    .then(function(r){return r.json();})
    .then(function(d){if(d.ok){alert('Пароль изменён');document.getElementById('newPw').value='';}});});
  document.getElementById('logoutBtn').addEventListener('click',function(){
    fetch('/api/logout',{method:'POST',headers:{'X-CSRF-Token':csrf}}).then(function(){render(null);});});
  document.getElementById('deleteAccountBtn').addEventListener('click',function(){
    if(confirm('Точно удалить аккаунт? Это необратимо.')){
      fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},
        body:JSON.stringify({action:'delete'})})
      .then(function(){location.reload();});}});
  load(function(d){render(d.nick?d:null);});
  setTimeout(function(){
    var ob=document.getElementById('onboardingBanner');
    if(ob && ob.style.display==='none' && !me){ob.style.display='block';}
  },2500);
})();
</script>
"""
