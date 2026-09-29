"""
База знань Джарвіса (SQLite). Файл: %APPDATA%\\Jarvis\\jarvis.db

Таблиці:
    commands     - "коли почую ФРАЗУ -> зроби ДІЮ" (ваші власні команди)
    corrections  - виправлення розпізнавання: Google чує "стін" -> насправді "стім"
    aliases      - як ви називаєте програму -> її справжня назва ("качалка" -> "qbittorrent")
    wake_words   - як Google записує ВАШЕ "Джарвіс"
    history      - що мікрофон почув останнім часом (з неї зручно навчати)

Модуль нічого не знає про голос чи вікно - лише зберігає й віддає дані. Потокобезпечний.
"""

import json
import os
import re
import sqlite3
import threading
import time

SCHEMA_VERSION = 1

COMMAND_KINDS = {
    "command": "Команда Джарвіса",
    "reply":   "Відповісти голосом",
    "open":    "Відкрити програму / сайт",
    "url":     "Відкрити посилання",
    "run":     "Запустити файл / команду",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS commands (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    phrase   TEXT NOT NULL UNIQUE,          -- нормалізована фраза
    kind     TEXT NOT NULL,                 -- command / reply / open / url / run
    action   TEXT NOT NULL,
    uses     INTEGER NOT NULL DEFAULT 0,
    created  REAL NOT NULL,
    last_used REAL
);
CREATE TABLE IF NOT EXISTS corrections (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    wrong    TEXT NOT NULL UNIQUE,
    right    TEXT NOT NULL,
    hits     INTEGER NOT NULL DEFAULT 0,
    created  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS aliases (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    spoken   TEXT NOT NULL UNIQUE,
    target   TEXT NOT NULL,
    created  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS wake_words (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    word     TEXT NOT NULL UNIQUE,
    source   TEXT NOT NULL DEFAULT 'вручну',
    hits     INTEGER NOT NULL DEFAULT 0,
    created  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS history (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       REAL NOT NULL,
    raw      TEXT NOT NULL,                 -- що записав Google
    text     TEXT NOT NULL,                 -- після виправлень
    alts     TEXT,                          -- інші варіанти (JSON)
    status   TEXT NOT NULL                  -- команда / без «Джарвіс» / відповідь
);
CREATE INDEX IF NOT EXISTS history_ts ON history(ts);
"""

HISTORY_KEEP = 500


def norm(text: str) -> str:
    """Нормалізація для порівняння: малі літери, без розділових знаків, один пробіл."""
    t = (text or "").lower().replace("’", "'").replace("ʼ", "'").replace("ё", "е")
    t = re.sub(r"[^\w'\- ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


class JarvisDB:
    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._lock = threading.RLock()
        self.con = sqlite3.connect(path, check_same_thread=False, timeout=10)
        self.con.row_factory = sqlite3.Row
        with self._lock, self.con:
            self.con.execute("PRAGMA journal_mode=WAL")
            self.con.executescript(SCHEMA)
            self.con.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    # ------------------------------ службове ------------------------------
    def _all(self, sql, args=()):
        with self._lock:
            return [dict(r) for r in self.con.execute(sql, args).fetchall()]

    def _exec(self, sql, args=()):
        with self._lock, self.con:
            return self.con.execute(sql, args)

    def close(self):
        with self._lock:
            self.con.close()

    # ------------------------------ команди ------------------------------
    def add_command(self, phrase: str, kind: str, action: str) -> int:
        phrase, action = norm(phrase), (action or "").strip()
        if not phrase or not action:
            raise ValueError("Потрібні і фраза, і дія")
        if kind not in COMMAND_KINDS:
            raise ValueError(f"Невідомий тип дії: {kind}")
        cur = self._exec("INSERT INTO commands(phrase, kind, action, created) VALUES(?,?,?,?) "
                         "ON CONFLICT(phrase) DO UPDATE SET kind=excluded.kind, action=excluded.action",
                         (phrase, kind, action, time.time()))
        return cur.lastrowid

    def commands(self):
        return self._all("SELECT * FROM commands ORDER BY phrase")

    def touch_command(self, cid: int):
        self._exec("UPDATE commands SET uses=uses+1, last_used=? WHERE id=?", (time.time(), cid))

    # ------------------------------ виправлення ------------------------------
    def add_correction(self, wrong: str, right: str) -> int:
        wrong, right = norm(wrong), norm(right)
        if not wrong or not right:
            raise ValueError("Потрібно і що чує Google, і як правильно")
        if wrong == right:
            raise ValueError("Слова однакові - нічого виправляти")
        cur = self._exec("INSERT INTO corrections(wrong, right, created) VALUES(?,?,?) "
                         "ON CONFLICT(wrong) DO UPDATE SET right=excluded.right", (wrong, right, time.time()))
        return cur.lastrowid

    def corrections(self):
        return self._all("SELECT * FROM corrections ORDER BY length(wrong) DESC")

    def hit_correction(self, cid: int):
        self._exec("UPDATE corrections SET hits=hits+1 WHERE id=?", (cid,))

    # ------------------------------ назви програм ------------------------------
    def add_alias(self, spoken: str, target: str) -> int:
        spoken, target = norm(spoken), (target or "").strip().lower()
        if not spoken or not target:
            raise ValueError("Потрібні обидві назви")
        cur = self._exec("INSERT INTO aliases(spoken, target, created) VALUES(?,?,?) "
                         "ON CONFLICT(spoken) DO UPDATE SET target=excluded.target", (spoken, target, time.time()))
        return cur.lastrowid

    def aliases(self):
        return self._all("SELECT * FROM aliases ORDER BY spoken")

    # ------------------------------ слово "Джарвіс" ------------------------------
    def add_wake(self, word: str, source: str = "вручну") -> bool:
        word = norm(word)
        if len(word) < 3:
            return False
        cur = self._exec("INSERT OR IGNORE INTO wake_words(word, source, created) VALUES(?,?,?)",
                         (word, source, time.time()))
        return cur.rowcount > 0

    def wake_words(self):
        return self._all("SELECT * FROM wake_words ORDER BY created")

    def hit_wake(self, word: str):
        self._exec("UPDATE wake_words SET hits=hits+1 WHERE word=?", (word,))

    # ------------------------------ історія ------------------------------
    def add_history(self, raw: str, text: str, alts, status: str):
        with self._lock, self.con:
            self.con.execute("INSERT INTO history(ts, raw, text, alts, status) VALUES(?,?,?,?,?)",
                             (time.time(), raw, text, json.dumps(list(alts or [])[:5], ensure_ascii=False), status))
            self.con.execute("DELETE FROM history WHERE id <= (SELECT max(id) FROM history) - ?", (HISTORY_KEEP,))

    def history(self, limit: int = 200):
        rows = self._all("SELECT * FROM history ORDER BY id DESC LIMIT ?", (limit,))
        for r in rows:
            try:
                r["alts"] = json.loads(r["alts"] or "[]")
            except Exception:
                r["alts"] = []
        return rows

    # ------------------------------ загальне ------------------------------
    TABLES = ("commands", "corrections", "aliases", "wake_words", "history")

    def delete(self, table: str, row_id: int):
        if table not in self.TABLES:
            raise ValueError(table)
        self._exec(f"DELETE FROM {table} WHERE id=?", (row_id,))

    def clear(self, table: str):
        if table not in self.TABLES:
            raise ValueError(table)
        self._exec(f"DELETE FROM {table}")

    def stats(self) -> dict:
        with self._lock:
            return {t: self.con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in self.TABLES}