# nickguard.py — фильтрация ников: RU/EN, обходы, homoglyphs, leet, транслит
import re
import unicodedata
import os

# Кириллические двойники латиницы (а->a, е->e, о->o, р->p, с->c, у->y, х->x...)
CONFUSABLES = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x",
    "і": "i", "ї": "i", "ј": "j", "ѕ": "s", "ԁ": "d", "һ": "h", "ѡ": "w",
    "ё": "е", "ъ": "", "ь": "",
})
LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s",
                      "7": "t", "@": "a", "$": "s", "!": "i", "|": "i"})
RU_LAT = {"ж": "zh", "ч": "ch", "ш": "sh", "щ": "sch", "ц": "ts", "ю": "yu",
          "я": "ya", "ё": "yo", "й": "i", "ы": "y", "э": "e", "б": "b",
          "в": "v", "г": "g", "д": "d", "з": "z", "к": "k", "л": "l",
          "м": "m", "н": "n", "п": "p", "р": "r", "с": "s", "т": "t",
          "у": "u", "ф": "f", "х": "h", "и": "i", "е": "e", "а": "a",
          "о": "o", "ю": "yu"}

def ru_to_lat(s):
    return "".join(RU_LAT.get(ch, ch) for ch in s)

# Зарезервированные / impersonation (RU+EN)
RESERVED = {
    "admin", "administrator", "root", "support", "moderator", "staff",
    "official", "system", "security", "help", "abuse", "bot", "api",
    "null", "undefined", "owner", "operator", "webmaster", "sysop",
    "админ", "администратор", "модератор", "поддержка", "официальный",
    "система", "безопасность", "помощь", "владелец", "оператор", "бот",
}
# Стартовый словарь мата/оскорблений (расширяется файлами wordlists/)
PROFANITY = {
    "хуй", "пизд", "ебат", "ебал", "ебуч", "бля", "мудак", "сука",
    "пидор", "гандон", "залуп", "манда", "дроч", "fuck", "shit", "bitch",
    "asshole", "dick", "cunt", "bastard", "whore", "slut", "pussy",
}
# Ложные срабатывания (Scunthorpe-проблема)
WHITELIST = {"class", "assistant", "dickens", "scunthorpe", "херувим",
             "колебаться", "страховка", "требуется", "оскорбление"}

# Тяжёлые категории грузим из файлов (не печатаю тут словари слюров/экстремизма —
# подключи готовые: LDNOOBW (ru/en), Hatebase). Формат: одно слово/корень на строку, # — коммент.
HARD_CATEGORIES = ("hate", "extremism", "sexual", "drugs", "violence")

def _load_wordlists():
    terms = set(PROFANITY)
    base = os.path.join(os.path.dirname(__file__), "wordlists")
    for cat in HARD_CATEGORIES:
        path = os.path.join(base, cat + ".txt")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip().lower()
                    if line and not line.startswith("#"):
                        terms.add(line)
    return terms

BANNED = _load_wordlists()
ALLOWED_RE = re.compile(r"^[A-Za-zА-Яа-яЁё0-9_\-.]{3,24}$")

def _variants(nick):
    s = unicodedata.normalize("NFKC", nick).casefold().translate(CONFUSABLES)
    leet = s.translate(LEET)
    compact = re.sub(r"[\s_\-.]+", "", leet)
    collapsed = re.sub(r"(.)\1+", r"\1", compact)
    return [s, leet, compact, collapsed, ru_to_lat(compact), ru_to_lat(collapsed)]

def nick_key(nick):
    """Канонический ключ для UNIQUE: Admin == аdmin == @dmin == a_d_m_i_n."""
    return _variants(nick)[-1]

def _hit(v, term, compact=False):
    if any(w in v for w in WHITELIST):
        return False
    if compact and len(term) >= 4:
        return term in v
    if len(term) >= 6:
        return term in v
    return re.search(r"(?<![a-zа-яё0-9])" + re.escape(term) + r"(?![a-zа-яё0-9])", v) is not None

def validate_nick(nick):
    """Возвращает (ok, code). code: length|chars|reserved|banned"""
    nick = (nick or "").strip()
    if not 3 <= len(nick) <= 24:
        return False, "length"
    if not ALLOWED_RE.match(nick):
        return False, "chars"
    # zero-width / bidi-символы (спуфинг отображения) — сразу отказ
    if any(unicodedata.category(ch) in ("Cf", "Cc") for ch in nick):
        return False, "chars"

    vs = _variants(nick)
    compact = vs[2]
    # impersonation: токен или compact-форма + суффиксы official/team/цифры
    tokens = set(re.split(r"[^a-zа-яё0-9]+", vs[0]))
    for r in RESERVED:
        if r in tokens or compact == r or re.fullmatch(re.escape(r) + r"(official|team|real|ru|\d{1,4})", compact):
            return False, "reserved"
    for v in vs:
        for term in BANNED:
            if _hit(v, term, compact=(v in (vs[2], vs[3], vs[4], vs[5]))):
                return False, "banned"
    return True, ""
