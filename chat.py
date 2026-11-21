# chat.py — API для тематических чатов
import os, re, json, time, hashlib, logging
from contextlib import contextmanager
from urllib.parse import quote_plus
import sqlite3
from flask import Blueprint, request, jsonify, session
from werkzeug.utils import secure_filename

bp = Blueprint("chat", __name__)
DB_PATH = os.environ.get("DB_PATH", "engfeed.db")
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads", "chat")
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp", "pdf", "txt", "doc", "docx"}

# Чёрный список слов для модерации
BANNED_WORDS = {
    "хуй", "хуя", "хуе", "хуё", "пизд", "бляд", "ебат", "ёбат",
    "сука", "сучк", "мудак", "мудил", "говно", "дерьм", "жопа",
    "fuck", "shit", "bitch", "asshole", "dick", "pussy", "cunt",
    "porn", "xxx", "sex", "наркот", "кокаин", "героин"
}

logger = logging.getLogger("engfeed.chat")

@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def _uid():
    return session.get("uid")

def _username():
    return session.get("username", "Аноним")

def check_profanity(text):
    """Проверка на нецензурную лексику"""
    text_lower = text.lower()
    for word in BANNED_WORDS:
        if word in text_lower:
            return True
    return False

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def init_chat(app):
    """Инициализация чатов при старте"""
    os.makedirs(UPLOAD_DIR, exist_ok=True)

@bp.get("/api/chat/rooms")
def get_rooms():
    """Список комнат пользователя"""
    uid = _uid()
    if not uid:
        return jsonify({"error": "not_authenticated"}), 401
    
    with db() as c:
        rooms = c.execute("""
            SELECT r.id, r.slug, r.title, 
                   COUNT(m.id) as unread_count
            FROM chat_rooms r
            JOIN chat_members mem ON mem.room_id = r.id
            LEFT JOIN chat_messages m ON m.room_id = r.id 
                AND m.created_at > mem.last_read_at
                AND m.user_id != ?
            WHERE mem.user_id = ? AND r.is_active = 1
            GROUP BY r.id
            ORDER BY r.title
        """, (uid, uid)).fetchall()
    
    return jsonify({"rooms": [dict(r) for r in rooms]})

@bp.post("/api/chat/rooms/<slug>/join")
def join_room(slug):
    """Вступить в комнату"""
    uid = _uid()
    if not uid:
        return jsonify({"error": "not_authenticated"}), 401
    
    with db() as c:
        room = c.execute("SELECT id FROM chat_rooms WHERE slug=? AND is_active=1", (slug,)).fetchone()
        if not room:
            return jsonify({"error": "room_not_found"}), 404
        
        c.execute("INSERT OR IGNORE INTO chat_members(user_id, room_id, joined_at, last_read_at) VALUES (?, ?, ?, ?)",
                  (uid, room["id"], time.time(), time.time()))
    
    return jsonify({"ok": True})

@bp.post("/api/chat/rooms/<slug>/leave")
def leave_room(slug):
    """Выйти из комнаты"""
    uid = _uid()
    if not uid:
        return jsonify({"error": "not_authenticated"}), 401
    
    with db() as c:
        room = c.execute("SELECT id FROM chat_rooms WHERE slug=?", (slug,)).fetchone()
        if room:
            c.execute("DELETE FROM chat_members WHERE user_id=? AND room_id=?", (uid, room["id"]))
    
    return jsonify({"ok": True})

@bp.get("/api/chat/rooms/<slug>/messages")
def get_messages(slug):
    """Получить сообщения. Короткий ответ — не блокируем единственный sync-воркер."""
    uid = _uid()
    if not uid:
        return jsonify({"error": "not_authenticated"}), 401

    since = request.args.get("since", 0, type=float)

    with db() as c:
        room = c.execute("SELECT id FROM chat_rooms WHERE slug=?", (slug,)).fetchone()
        if not room:
            return jsonify({"error": "room_not_found"}), 404

        messages = c.execute("""
            SELECT m.id, m.user_id, m.username, m.content,
                   m.attachment_type, m.attachment_url, m.attachment_size,
                   m.created_at, m.edited_at
            FROM chat_messages m
            WHERE m.room_id = ? AND m.created_at > ? AND m.is_deleted = 0
            ORDER BY m.created_at ASC
            LIMIT 50
        """, (room["id"], since)).fetchall()

        if messages:
            c.execute("UPDATE chat_members SET last_read_at = ? WHERE user_id = ? AND room_id = ?",
                      (time.time(), uid, room["id"]))
            return jsonify({
                "messages": [dict(m) for m in messages],
                "last_timestamp": messages[-1]["created_at"]
            })

        return jsonify({"messages": [], "last_timestamp": since})

@bp.post("/api/chat/rooms/<slug>/messages")
def send_message(slug):
    """Отправить сообщение"""
    uid = _uid()
    if not uid:
        return jsonify({"error": "not_authenticated"}), 401
    
    data = request.get_json(silent=True) or {}
    content = data.get("content", "").strip()
    
    if not content and not data.get("attachment"):
        return jsonify({"error": "empty_message"}), 400
    
    # Проверка на мат
    if content and check_profanity(content):
        return jsonify({"error": "profanity_detected"}), 400
    
    with db() as c:
        room = c.execute("SELECT id FROM chat_rooms WHERE slug=? AND is_active=1", (slug,)).fetchone()
        if not room:
            return jsonify({"error": "room_not_found"}), 404
        
        # Проверка, что пользователь в комнате
        member = c.execute("SELECT 1 FROM chat_members WHERE user_id=? AND room_id=?", 
                          (uid, room["id"])).fetchone()
        if not member:
            return jsonify({"error": "not_member"}), 403
        
        # Rate limiting: не больше 5 сообщений в минуту
        recent = c.execute("""
            SELECT COUNT(*) as cnt FROM chat_messages 
            WHERE user_id = ? AND room_id = ? AND created_at > ?
        """, (uid, room["id"], time.time() - 60)).fetchone()
        if recent["cnt"] >= 5:
            return jsonify({"error": "rate_limit"}), 429
        
        now = time.time()
        attachment_type = data.get("attachment_type")
        attachment_url = data.get("attachment_url")
        attachment_size = data.get("attachment_size")
        
        c.execute("""
            INSERT INTO chat_messages(room_id, user_id, username, content, 
                                     attachment_type, attachment_url, attachment_size, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (room["id"], uid, _username(), content, attachment_type, attachment_url, attachment_size, now))
    
    return jsonify({"ok": True, "timestamp": now})

@bp.post("/api/chat/upload")
def upload_file():
    """Загрузить файл или фото"""
    uid = _uid()
    if not uid:
        return jsonify({"error": "not_authenticated"}), 401
    
    if "file" not in request.files:
        return jsonify({"error": "no_file"}), 400
    
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "empty_filename"}), 400
    
    if not allowed_file(file.filename):
        return jsonify({"error": "file_type_not_allowed"}), 400
    
    # Проверка размера
    file.seek(0, os.SEEK_END)
    size = file.tell()
    file.seek(0)
    if size > MAX_FILE_SIZE:
        return jsonify({"error": "file_too_large"}), 400
    
    # Сохранение файла
    filename = secure_filename(file.filename)
    ext = filename.rsplit(".", 1)[1].lower()
    file_hash = hashlib.md5(f"{uid}{time.time()}{filename}".encode()).hexdigest()[:8]
    saved_name = f"{file_hash}_{filename}"
    
    filepath = os.path.join(UPLOAD_DIR, saved_name)
    file.save(filepath)
    
    # Определяем тип
    if ext in {"jpg", "jpeg", "png", "gif", "webp"}:
        attachment_type = "image"
    else:
        attachment_type = "file"
    
    return jsonify({
        "ok": True,
        "url": f"/uploads/chat/{saved_name}",
        "type": attachment_type,
        "size": size,
        "filename": filename
    })

@bp.post("/api/chat/messages/<int:msg_id>/report")
def report_message(msg_id):
    """Пожаловаться на сообщение"""
    uid = _uid()
    if not uid:
        return jsonify({"error": "not_authenticated"}), 401
    
    data = request.get_json(silent=True) or {}
    reason = data.get("reason", "other")
    
    with db() as c:
        c.execute("INSERT INTO chat_reports(message_id, reporter_id, reason, created_at) VALUES (?, ?, ?, ?)",
                  (msg_id, uid, reason, time.time()))
    
    return jsonify({"ok": True})

@bp.delete("/api/chat/messages/<int:msg_id>")
def delete_message(msg_id):
    """Удалить своё сообщение"""
    uid = _uid()
    if not uid:
        return jsonify({"error": "not_authenticated"}), 401
    
    with db() as c:
        msg = c.execute("SELECT user_id FROM chat_messages WHERE id=?", (msg_id,)).fetchone()
        if not msg:
            return jsonify({"error": "message_not_found"}), 404
        if msg["user_id"] != uid:
            return jsonify({"error": "not_your_message"}), 403
        
        c.execute("UPDATE chat_messages SET is_deleted = 1 WHERE id=?", (msg_id,))
    
    return jsonify({"ok": True})

def create_room_for_interest(slug, title):
    """Создать комнату чата для интереса (вызывается из interests.py)"""
    with db() as c:
        existing = c.execute("SELECT id FROM chat_rooms WHERE slug=?", (slug,)).fetchone()
        if not existing:
            c.execute("INSERT INTO chat_rooms(slug, title, created_at) VALUES (?, ?, ?)",
                      (slug, title, time.time()))
            logger.info(f"Created chat room for interest: {slug}")
