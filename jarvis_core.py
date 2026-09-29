"""
J.A.R.V.I.S. v5 - ЯДРО асистента (голос, команди, ШІ). Вікно - hud.py, запуск - jarvis.py.

Що нового у v5
  * НОВИЙ РУШІЙ МІКРОФОНА (виправлення "реагує через раз"):
      - власний детектор фраз з адаптивним рівнем шуму замість фіксованого порогу:
        працює і в тихій кімнаті, і коли шумить вентилятор чи грає музика;
      - мікрофон більше НЕ глухне, поки Джарвіс генерує голос через інтернет
        (раніше - 1-3 секунди "глухоти" перед кожною відповіддю);
      - фраза, яку ви почали одразу після відповіді Джарвіса, більше не склеюється
        з його власним голосом і не викидається;
      - розпізнавання йде в окремому потоці, паралельно з виконанням команд;
      - "Джарвіс" ... (пауза) ... "відкрий стім" - команда більше не губиться;
      - слово "Джарвіс" впізнається фонетично (дарвіс, гарвіс, джавіс, jarvis...);
      - запис 16 кГц моно - менше даних для Google, швидша відповідь;
      - повторний запит, якщо сервіс розпізнавання на мить не відповів.
  * Кеш голосу: короткі фрази ("Відкриваю", "Запускаю") звучать миттєво.
  * Таймери можна переглядати й скасовувати ("скасуй таймер", "скільки залишилось").
  * Усі налаштування змінюються у вікні й зберігаються в %APPDATA%\\Jarvis\\config.json.

Приклади (починай зі слова "Джарвіс"):
  "Джарвіс, запусти стім"          "Джарвіс, закрий стім"          "Джарвіс, відкрий ютуб у хромі"
  "Джарвіс, ігровий режим"         "Джарвіс, робочий режим"        "Джарвіс, режим спокою"
  "Джарвіс, постав таймер на 10 хвилин"    "Джарвіс, скільки залишилось"   "Джарвіс, скасуй таймер"
  "Джарвіс, нагадай через 20 хвилин зателефонувати мамі"
  "Джарвіс, яка погода в Одесі"    "Джарвіс, підкинь монету"       "Джарвіс, кинь кубик на 20"
  "Джарвіс, увімкни Imagine Dragons Believer"   "Джарвіс, пауза / наступний трек / гучніше"
  "Джарвіс, зроби скріншот"        "Джарвіс, заблокуй комп'ютер"   "Джарвіс, вимкнись"
  "Джарвіс, а що ти думаєш про..." (ШІ, потрібен безкоштовний ключ Gemini)
"""

import array
import asyncio
import ctypes
import datetime
import glob
import hashlib
import itertools
import json
import math
import os
import queue
import random
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from collections import deque
from difflib import SequenceMatcher
from urllib.parse import quote_plus

FROZEN = getattr(sys, "frozen", False)          # True, коли запущено як .exe
BASE_DIR = os.path.dirname(sys.executable if FROZEN else os.path.abspath(__file__))
LOG_PATH = os.path.join(BASE_DIR, "jarvis.log")

# Без консолі (pythonw / exe --noconsole) пишемо у файл jarvis.log
if sys.stdout is None or sys.stderr is None:
    _log = open(LOG_PATH, "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = _log

# Захист від подвійного запуску
_lock = socket.socket()
try:
    _lock.bind(("127.0.0.1", 54721))
except OSError:
    print("Джарвіс уже запущений. Виходжу.")
    sys.exit(0)

import edge_tts
import pygame
import speech_recognition as sr
import yt_dlp

try:
    import audioop                      # швидкий підрахунок гучності (у Python 3.13+ - пакет audioop-lts)
except ImportError:
    audioop = None

try:
    import winsound
except ImportError:          # не Windows
    winsound = None

# ======================= НАЛАШТУВАННЯ =======================
# Більшість із них тепер змінюється у вікні (вкладки МІКРОФОН і НАЛАШТУВАННЯ)
# і зберігається у %APPDATA%\Jarvis\config.json. Тут - значення за замовчуванням.

CONFIG_DIR = os.path.join(os.environ.get("APPDATA") or BASE_DIR, "Jarvis")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
_cfg_lock = threading.Lock()


def load_config() -> dict:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(**values) -> None:
    with _cfg_lock:
        cfg = load_config()
        cfg.update(values)
        os.makedirs(CONFIG_DIR, exist_ok=True)
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)


_cfg = load_config()

import jarvis_db
from jarvis_db import COMMAND_KINDS, norm

DB_PATH = os.path.join(CONFIG_DIR, "jarvis.db")
try:
    DB = jarvis_db.JarvisDB(DB_PATH)
except Exception as _e:                     # немає прав на запис - база поруч із програмою або в памʼяті
    print(f"(база {DB_PATH} недоступна: {_e})")
    try:
        DB_PATH = os.path.join(BASE_DIR, "jarvis.db")
        DB = jarvis_db.JarvisDB(DB_PATH)
    except Exception:
        DB_PATH = ":memory:"
        DB = jarvis_db.JarvisDB(DB_PATH)


def _c(key, default):
    v = _cfg.get(key)
    return default if v is None else v


WAKE_WORDS = ["джарвіс", "джарвис", "jarvis", "жарвіс", "джарвіз", "джервіс", "чарвіс", "джарвес",
              "джарвіш", "джарбіс", "дарвіс", "гарвіс", "джавіс", "жервіс"]
WAKE_WORDS += ["джарвісе", "джарвиз", "жарвис", "джервис", "джарвін", "джарвіса", "джарвісу",
               "жарвіз", "джарвас", "jarves"]
for _w in _c("wake_learned", []):             # переносимо старі навчені варіанти з config.json у базу
    DB.add_wake(_w, "голос")
WAKE_LEARNED = [r["word"] for r in DB.wake_words()]   # як Google записує ВАШЕ "Джарвіс" (з бази)
WAKE_SIMILARITY = 0.78          # фонетична схожість 0..1 (менше = легше спрацьовує, але більше хибних)
REQUIRE_WAKE_WORD = bool(_c("require_wake_word", True))
SAY_LISTENING = bool(_c("say_listening", False))   # казати "Слухаю" після голого "Джарвіс" (інакше - лише сигнал)
VOICE = _c("voice", "uk-UA-OstapNeural")           # жіночий: "uk-UA-PolinaNeural"
VOICES = {"uk-UA-OstapNeural": "Остап (чоловічий)", "uk-UA-PolinaNeural": "Поліна (жіночий)"}
LANG = "uk-UA"

# Інтонації: (швидкість, висота) для edge-tts
MOODS = {
    "neutral":  ("-8%",  "-5Hz"),
    "happy":    ("+4%",  "+15Hz"),
    "excited":  ("+15%", "+25Hz"),
    "calm":     ("-18%", "-15Hz"),
    "serious":  ("-12%", "-10Hz"),
}

# --- Мікрофон ---
MIC_INDEX = _c("mic_index", None)                  # None = мікрофон Windows за замовчуванням
MIC_SENSITIVITY = int(_c("mic_sensitivity", 6))    # 1..10: більше = чує тихіше, але ловить більше шуму
PAUSE_SECONDS = float(_c("pause_seconds", 0.8))    # стільки тиші = кінець фрази
FOLLOWUP_SECONDS = float(_c("followup_seconds", 8))  # після команди стільки секунд можна без "Джарвіс"
WAKE_WAIT_SECONDS = 7                              # скільки чекати команду після голого "Джарвіс"

# --- ШІ (безкоштовний Google Gemini). Ключ: у вікні /key КЛЮЧ або вкладка НАЛАШТУВАННЯ ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or _c("gemini_api_key", "AQ.Ab8RN6Ix_yZO7ykANDAAv4AfCuKxoqsS9D8_ceE4kVrHA7A07w")
GEMINI_MODEL = _c("gemini_model", "") or "gemini-3.1-flash-lite"
GEMINI_FALLBACK_MODELS = ["gemini-3-flash-preview", "gemini-2.5-flash-lite", "gemini-2.5-flash"]
AI_PERSONA = (
    "Ти Джарвіс - голосовий асистент українською мовою, як з фільмів про Залізну людину: "
    "ввічливий, трохи дотепний, звертаєшся на 'ви'. Відповідай ДУЖЕ коротко (1-2 речення, "
    "максимум 40 слів), розмовно, без списків, без markdown і без emoji - твою відповідь "
    "буде озвучено вголос."
)
AI_HISTORY_LIMIT = 6

AI_TRIGGERS = [
    "що ти думаєш", "що думаєш", "як ти вважаєш", "яка твоя думка", "твоя думка",
    "як ти гадаєш", "порадь", "поясни", "розкажи", "що таке", "хто такий", "хто така",
    "навіщо", "чому", "як зробити так щоб", "твоя порада", "запитання до тебе",
]
ALWAYS_USE_CHROME = bool(_c("always_chrome", False))
APP_MATCH_MIN = 0.75

APPS = {
    "калькулятор": "calc.exe",
    "блокнот": "notepad.exe",
    "провідник": "explorer.exe",
    "термінал": "wt.exe",
    "командний рядок": "cmd.exe",
    "диспетчер завдань": "taskmgr.exe",
    "панель керування": "control.exe",
    "налаштування": "ms-settings:",
    "пейнт": "mspaint.exe",
    # "пайчарм": r"C:\Program Files\JetBrains\PyCharm 2025.2\bin\pycharm64.exe",
}

ALIASES = BASE_ALIASES = {
    "стім": "steam", "стим": "steam", "пар": "steam",
    "діскорд": "discord", "дискорд": "discord", "діс корд": "discord",
    "телеграм": "telegram", "телега": "telegram",
    "хром": "chrome", "гугл хром": "chrome", "браузер": "chrome",
    "файрфокс": "firefox", "мозілла": "firefox", "едж": "edge",
    "спотіфай": "spotify", "спотифай": "spotify",
    "вайбер": "viber", "ватсап": "whatsapp", "скайп": "skype", "зум": "zoom",
    "пайчарм": "pycharm", "пай чарм": "pycharm", "пайчам": "pycharm",
    "візуал студіо": "visual studio", "вс код": "visual studio code",
    "ворд": "word", "ексель": "excel", "ексел": "excel", "пауерпоінт": "powerpoint",
    "обс": "obs", "епік геймс": "epic games", "епік": "epic games",
    "майнкрафт": "minecraft", "дота": "dota", "контр страйк": "counter-strike",
    "фотошоп": "photoshop", "блендер": "blender", "опера": "opera",
}

SITES = {
    "ютуб": "https://www.youtube.com", "ютюб": "https://www.youtube.com",
    "youtube": "https://www.youtube.com",
    "гугл": "https://www.google.com", "google": "https://www.google.com",
    "пошта": "https://mail.google.com", "gmail": "https://mail.google.com",
    "гітхаб": "https://github.com", "github": "https://github.com",
    "клод": "https://claude.ai", "claude": "https://claude.ai",
    "інстаграм": "https://www.instagram.com", "фейсбук": "https://www.facebook.com",
    "твіч": "https://www.twitch.tv", "нетфлікс": "https://www.netflix.com",
    "переклад": "https://translate.google.com",
}

MUSIC_DIR = os.path.expanduser("~/Music")
DEFAULT_CITY = _c("city", "") or "Вінниця"

JOKES = [
    "Чому програмісти плутають Гелловін і Різдво? Бо Oct 31 дорівнює Dec 25.",
    "Я б розповів жарт про UDP, але не гарантую, що ти його отримаєш.",
    "У програміста питають: у тебе є дружина? Він відповідає: ні, але є жучок у коді, "
    "і він забирає весь мій час.",
    "Два байти зустрілись. Один питає: ти хворий? Ні, просто в мене біт зіпсований.",
]

# ============================================================================

CREATE_NO_WINDOW = 0x08000000

try:
    pygame.mixer.init()
except Exception as _e:
    print(f"(немає звукового виходу: {_e})")

recognizer = sr.Recognizer()
recognizer.operation_timeout = 10       # не чекати Google вічно

MONTHS = ["січня", "лютого", "березня", "квітня", "травня", "червня", "липня",
          "серпня", "вересня", "жовтня", "листопада", "грудня"]
WEEKDAYS = ["понеділок", "вівторок", "середа", "четвер", "п'ятниця", "субота", "неділя"]

VK_MUTE, VK_VOL_DOWN, VK_VOL_UP = 0xAD, 0xAE, 0xAF
VK_MEDIA_NEXT, VK_MEDIA_PREV, VK_MEDIA_PLAY_PAUSE = 0xB0, 0xB1, 0xB3
VK_ALT, VK_F4, VK_LWIN, VK_D, VK_SNAPSHOT = 0x12, 0x73, 0x5B, 0x44, 0x2C

UNKNOWN = object()          # "команду не зрозуміла"

audio_q: "queue.Queue" = queue.Queue()     # записані фрази (start, end, AudioData)
heard_q: "queue.Queue" = queue.Queue()     # розпізнаний текст (start, [варіанти])
text_q: "queue.Queue" = queue.Queue()      # команди, набрані вручну у вікні
speaking = threading.Event()               # зараз РЕАЛЬНО грає голос Джарвіса
muted = threading.Event()                  # мікрофон вимкнено кнопкою
_transcribing = threading.Event()
MIC_OK = False
_echo_until = 0.0                          # до цього моменту звук у мікрофоні - відлуння Джарвіса

EVENT_HOOK = None      # вікно ставить сюди свою функцію: hook(kind, text)
_state = "listening"


def emit(kind: str, text: str = "") -> None:
    """Подія для вікна: user / heard / jarvis / log / error / state / mute / ready / timers / apps / quit."""
    hook = EVENT_HOOK
    if hook:
        try:
            hook(kind, text)
        except Exception:
            pass


def set_state(s: str) -> None:
    global _state
    _state = s
    emit("state", s)


def idle_state() -> str:
    if muted.is_set():
        return "muted"
    return "listening" if MIC_OK else "nomic"


def _refresh_idle() -> None:
    if _state in ("listening", "muted", "nomic"):
        set_state(idle_state())


def _drain(q) -> None:
    while True:
        try:
            q.get_nowait()
        except queue.Empty:
            return


def set_muted(value: bool) -> None:
    if value:
        muted.set()
        _drain(audio_q)
        _drain(heard_q)
    else:
        muted.clear()
    emit("mute", "1" if value else "0")
    _refresh_idle()


def submit_text(text: str) -> None:
    """Викликається вікном, коли ти набрав команду або питання вручну."""
    text_q.put(text)


# ================================ МІКРОФОН ================================
#
# Як це працює:
#   потік "jarvis-mic"  читає мікрофон шматками по 32 мс -> PhraseDetector вирізає фрази
#   потік "jarvis-stt"  відправляє фрази в Google і кладе текст у heard_q
#   головний цикл       бере текст, шукає "Джарвіс" і виконує команди
# Мікрофон не зупиняється ніколи - навіть поки йде розпізнавання чи виконується команда.

SAMPLE_RATE = 16000        # Google добре розпізнає 16 кГц, а даних утричі менше, ніж 48 кГц
CHUNK_SECONDS = 0.032
PREROLL_SECONDS = 0.45     # звук ДО початку мови, що додається до фрази (щоб не зрізати "Дж...")
ONSET_CHUNKS = 2           # стільки шматків поспіль мають бути гучними, щоб почалась фраза
MIN_VOICED_SECONDS = 0.22  # коротше - клацання/стук, а не слово
MAX_PHRASE_SECONDS = 10
STEADY_SECONDS = 2.5       # рівний гул стільки секунд (вентилятор, музика) = це новий фон, а не мова
ECHO_TAIL = 0.25           # після того, як Джарвіс замовк, ще стільки секунд вважаємо звук відлунням
TAIL_KEEP_SECONDS = 0.3    # тиші в кінці фрази, яку лишаємо для Google


def _thresholds():
    """(у скільки разів мова має бути голосніша за фоновий шум, мінімальний поріг)."""
    s = max(1, min(10, int(MIC_SENSITIVITY)))
    return 4.6 - 0.32 * s, 40 + (10 - s) * 28


def _rms(data: bytes, width: int) -> float:
    if audioop is not None:
        try:
            return float(audioop.rms(data, width))
        except Exception:
            pass
    if width != 2 or not data:
        return 0.0
    a = array.array("h", data[: len(data) - len(data) % 2])
    return math.sqrt(sum(x * x for x in a) / len(a)) if a else 0.0


class PhraseDetector:
    """Вирізає фрази з потоку звуку. Поріг = фоновий шум x коефіцієнт, і фон
    постійно відстежується (швидко вниз, повільно вгору). Без мікрофона - легко тестувати."""

    def __init__(self, rate: int, width: int, chunk: int):
        self.rate, self.width = rate, width
        self.chunk_sec = chunk / float(rate)
        self.preroll = deque(maxlen=max(1, int(round(PREROLL_SECONDS / self.chunk_sec))))
        self.noise = None
        self.level = 0.0
        self.threshold = 0.0
        self._boot = []
        self._clear()

    def _clear(self):
        self.active = False
        self.frames = []
        self.levels = []
        self.voiced = 0
        self.silence = 0
        self.onset = 0
        self.start = 0.0

    def recalibrate(self):
        self.noise = None
        self._boot = []
        self._clear()

    def _finish(self, now: float):
        keep = int(round(TAIL_KEEP_SECONDS / self.chunk_sec))
        cut = max(0, self.silence - keep)
        frames = self.frames[: len(self.frames) - cut] if cut else self.frames
        voiced = self.voiced * self.chunk_sec
        start = self.start
        self._clear()
        if voiced < MIN_VOICED_SECONDS:
            return None
        return start, now, b"".join(frames)

    def feed(self, data: bytes, now: float, echo: bool = False):
        """Повертає (початок, кінець, байти) щойно фраза закінчилась, інакше None."""
        rms = _rms(data, self.width)
        self.level = rms
        ratio, floor = _thresholds()
        if self.noise is None:                           # перші 0.6 с міряємо тишу
            self._boot.append(rms)
            if len(self._boot) * self.chunk_sec >= 0.6:
                b = sorted(self._boot)
                self.noise = max(1.0, b[len(b) // 2])
            self.threshold = floor
            return None
        start_thr = max(floor, self.noise * ratio)
        self.threshold = start_thr

        if echo:
            # Грає голос Джарвіса - його не слухаємо. Фразу, яку ви почали ДО цього, віддаємо як є.
            result = self._finish(now) if self.active else None
            self.preroll.clear()
            self.onset = 0
            return result

        if not self.active:
            self.preroll.append(data)
            if rms >= start_thr:
                self.onset += 1
                if self.onset >= ONSET_CHUNKS:
                    self.active = True
                    self.frames = list(self.preroll)
                    self.preroll.clear()
                    self.levels = [rms]
                    self.start = now - len(self.frames) * self.chunk_sec
                    self.voiced = self.onset
                    self.silence = 0
            else:
                self.onset = 0
                k = 0.2 if rms < self.noise else 0.02     # фон падає швидко, росте повільно
                self.noise = max(1.0, self.noise + (rms - self.noise) * k)
            return None

        self.frames.append(data)
        self.levels.append(rms)
        if rms >= start_thr * 0.6:                        # гістерезис: продовжувати легше, ніж почати
            self.voiced += 1
            self.silence = 0
        else:
            self.silence += 1
        if self.silence * self.chunk_sec >= PAUSE_SECONDS:
            return self._finish(now)
        win = int(STEADY_SECONDS / self.chunk_sec)
        if len(self.levels) >= win and len(self.levels) % 8 == 0:
            # Мова "рвана" (склади, паузи між словами), а шум рівний. Якщо сигнал підозріло рівний -
            # це фон змінився (увімкнули музику/вентилятор): переймаємо його як новий рівень шуму.
            tail = self.levels[-win:]
            m = sum(tail) / len(tail)
            sd = math.sqrt(sum((x - m) ** 2 for x in tail) / len(tail))
            if m > 0 and sd / m < 0.25:
                self.noise = max(self.noise, sorted(tail)[len(tail) // 2])
                self._clear()
                self.preroll.clear()
                return None
        if len(self.frames) * self.chunk_sec >= MAX_PHRASE_SECONDS:
            lv = sorted(self.levels)
            self.noise = max(self.noise, lv[len(lv) // 2] * 0.7)   # схоже, фон виріс (музика) - підлаштовуємось
            return self._finish(now)
        return None


def _fix_name(name: str) -> str:
    """PyAudio на Windows іноді псує кирилицю в назвах пристроїв - пробуємо полагодити."""
    if re.search("[а-яіїєґА-ЯІЇЄҐ]", name):
        return name
    for a, b in (("cp1252", "utf-8"), ("latin-1", "utf-8"), ("latin-1", "cp1251")):
        try:
            fixed = name.encode(a).decode(b)
            if re.search("[а-яіїєґА-ЯІЇЄҐ]", fixed):
                return fixed
        except Exception:
            continue
    return name


def list_microphones() -> list:
    """[(індекс, назва, чи_за_замовчуванням)] - лише пристрої вводу, без дублікатів."""
    out = []
    try:
        pa = sr.Microphone.get_pyaudio().PyAudio()
        try:
            host = pa.get_default_host_api_info()["index"]
            try:
                default = pa.get_default_input_device_info()["index"]
            except Exception:
                default = None
            for i in range(pa.get_device_count()):
                info = pa.get_device_info_by_index(i)
                if info.get("maxInputChannels", 0) > 0 and info.get("hostApi") == host:
                    out.append((i, _fix_name(info.get("name", "?")), i == default))
        finally:
            pa.terminate()
    except Exception as e:
        print(f"(не вдалось отримати список мікрофонів: {e})")
    return out


class MicEngine:
    def __init__(self):
        self.detector = None
        self.device_name = ""
        self.rate = 0
        self.opened = threading.Event()
        self._restart = threading.Event()
        self._recal = threading.Event()
        self.stats = {"phrases": 0, "recognized": 0, "empty": 0, "ignored": 0}
        self.last_heard = ""
        self.force_native = bool(_c("mic_native_rate", False))

    def start(self, stop: threading.Event) -> None:
        threading.Thread(target=self._run, args=(stop,), daemon=True, name="jarvis-mic").start()

    def restart(self) -> None:
        self._restart.set()

    def recalibrate(self) -> None:
        self._recal.set()

    def _open(self):
        err = None
        rates = (None,) if self.force_native else (SAMPLE_RATE, None)
        for rate in rates:          # 16 кГц, а якщо пристрій не вміє - його рідна частота
            try:
                mic = sr.Microphone(device_index=MIC_INDEX, sample_rate=rate,
                                    chunk_size=int((rate or 48000) * CHUNK_SECONDS))
                mic.__enter__()
                return mic
            except Exception as e:
                err = e
        raise err

    @staticmethod
    def _name(mic) -> str:
        try:
            pa = mic.audio
            idx = MIC_INDEX if MIC_INDEX is not None else pa.get_default_input_device_info()["index"]
            return _fix_name(pa.get_device_info_by_index(idx)["name"])
        except Exception:
            return "мікрофон"

    def _run(self, stop: threading.Event) -> None:
        global MIC_OK
        warned = False
        while not stop.is_set():
            self._restart.clear()
            try:
                mic = self._open()
            except Exception as e:
                MIC_OK = False
                self.detector = None
                self.opened.set()
                if not warned:
                    print(f"(мікрофон недоступний: {e})")
                    emit("error", f"Мікрофон недоступний ({e}). Команди можна писати в рядок. "
                                  "Пробую підключитись знову...")
                    warned = True
                    _refresh_idle()
                stop.wait(3)
                continue

            warned = False
            self.device_name = self._name(mic)
            self.rate = mic.SAMPLE_RATE
            det = PhraseDetector(mic.SAMPLE_RATE, mic.SAMPLE_WIDTH, mic.CHUNK)
            self.detector = det
            MIC_OK = True
            self.opened.set()
            print(f"Мікрофон: {self.device_name} ({mic.SAMPLE_RATE} Гц)")
            emit("log", f"Мікрофон: {self.device_name} ({mic.SAMPLE_RATE} Гц)")
            _refresh_idle()
            opened_at, loud_seen, zero_warned = time.time(), False, False
            try:
                while not stop.is_set() and not self._restart.is_set():
                    data = mic.stream.read(mic.CHUNK)
                    now = time.time()
                    # Деякі драйвери на 16 кГц віддають "мертву" тишу (суцільні нулі).
                    # Якщо за 2 секунди не було жодного ненульового звуку - перевідкриваємо на рідній частоті.
                    if not loud_seen:
                        if _rms(data, mic.SAMPLE_WIDTH) > 3:      # реальний звук, а не цифрові нулі
                            loud_seen = True
                        elif now - opened_at > 2.0 and not zero_warned:
                            if not self.force_native:
                                print("(мікрофон віддає нулі на 16 кГц - перемикаюсь на рідну частоту)")
                                self.force_native = True
                                save_config(mic_native_rate=True)
                                break
                            zero_warned = True
                            emit("error", "Мікрофон відкрився, але звуку НЕМАЄ (суцільна тиша). Перевірте: чи не вимкнено мікрофон клавішею на ноутбуці, "
                                          "рівень запису у Windows (Звук → Запис → Властивості → Рівні), дозвіл доступу: Параметри Windows → Конфіденційність → Мікрофон → "
                                          "увімкніть «Дозволити класичним програмам доступ до мікрофона». "
                                          "Або оберіть інший пристрій на вкладці МІКРОФОН.")
                    if self._recal.is_set():
                        self._recal.clear()
                        det.recalibrate()
                        emit("log", "Перекалібровую мікрофон - помовчіть секунду...")
                    if muted.is_set():
                        det.level = 0.0
                        if det.active:
                            det._clear()
                        continue
                    res = det.feed(data, now, echo=speaking.is_set() or now < _echo_until)
                    if res:
                        start, end, frames = res
                        self.stats["phrases"] += 1
                        audio_q.put((start, end, sr.AudioData(frames, mic.SAMPLE_RATE, mic.SAMPLE_WIDTH)))
            except Exception as e:
                MIC_OK = False
                print(f"(мікрофон відключився: {e})")
                emit("error", f"Мікрофон відключився ({e}). Перепідключаю...")
                _refresh_idle()
                stop.wait(1)
            finally:
                try:
                    mic.__exit__(None, None, None)
                except Exception:
                    pass
                self.detector = None


MIC = MicEngine()


def recalibrate_mic() -> None:
    MIC.recalibrate()


def mic_busy() -> bool:
    """Чи ви зараз говорите, або фраза ще розпізнається."""
    d = MIC.detector
    return bool((d and d.active) or not audio_q.empty() or _transcribing.is_set())


def mic_stats() -> dict:
    d = MIC.detector
    return {
        "ok": MIC_OK, "device": MIC.device_name, "rate": MIC.rate, "muted": muted.is_set(),
        "speaking": speaking.is_set(),
        "level": d.level if d else 0.0, "threshold": d.threshold if d else 0.0,
        "noise": (d.noise or 0.0) if d else 0.0, "in_speech": bool(d and d.active),
        "calibrating": bool(d and d.noise is None),
        "last_heard": MIC.last_heard, **MIC.stats,
    }


def transcribe(audio) -> list:
    """Повертає ВСІ варіанти розпізнавання від Google (найімовірніший - перший)."""
    res = None
    for attempt in range(2):
        try:
            res = recognizer.recognize_google(audio, language=LANG, show_all=True)
            break
        except sr.UnknownValueError:
            return []
        except sr.RequestError as e:
            if attempt == 0:
                time.sleep(0.4)
                continue
            print(f"(немає зв'язку з сервісом розпізнавання: {e})")
            emit("error", "Немає зв'язку з сервісом розпізнавання мови - перевірте інтернет")
            return []
        except Exception as e:
            print(f"(помилка розпізнавання: {e})")
            return []
    alts = []
    if isinstance(res, dict):
        for a in res.get("alternative", []):
            t = a.get("transcript", "").lower().strip()
            if t and t not in alts:
                alts.append(t)
    if alts:
        extra = f"   [інші варіанти: {alts[1:4]}]" if len(alts) > 1 else ""
        print(f"Почув: {alts[0]}{extra}")
    return alts


def _transcriber(stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            start, end, audio = audio_q.get(timeout=0.3)
        except queue.Empty:
            continue
        if time.time() - end > 15 or muted.is_set():      # застаріле
            continue
        _transcribing.set()
        try:
            alts = transcribe(audio)
        finally:
            _transcribing.clear()
        raw = alts[0] if alts else ""
        if alts:
            MIC.stats["recognized"] += 1
            MIC.last_heard = alts[0]
            alts = apply_corrections(alts)
        else:
            MIC.stats["empty"] += 1
        heard_q.put((start, alts, raw))


def hear(timeout=None, typed=True, since: float = 0.0):
    """Чекає наступну фразу (голосом або набрану у вікні). Список варіантів або None.
    Якщо час вийшов, а ви саме говорите чи фраза ще розпізнається - почекає ще трохи."""
    end = None if timeout is None else time.time() + timeout
    hard_end = None if timeout is None else end + 8
    while True:
        if typed:
            try:
                t = text_q.get_nowait().strip()
            except queue.Empty:
                t = ""
            if t:
                emit("user", t)
                return [t.lower()]
        try:
            ts, alts, raw = heard_q.get(timeout=0.15)
        except queue.Empty:
            now = time.time()
            if end is not None and now >= end and (not mic_busy() or now >= hard_end):
                return None
            continue
        if ts < since or not alts:
            continue
        _history(raw, alts, "відповідь")
        emit("user", alts[0])
        return alts


# ------------------------------ ГОЛОС ------------------------------

TTS_DIR = os.path.join(tempfile.gettempdir(), "jarvis_tts")
_speak_lock = threading.Lock()


def beep(freq: int = 1000, ms: int = 90) -> None:
    if winsound:
        def _b():
            try:
                winsound.Beep(freq, ms)
            except Exception:
                pass
        threading.Thread(target=_b, daemon=True).start()


def _tts_file(text: str, rate: str, pitch: str):
    """Синтезує мову в mp3. Короткі фрази кешуються на диску. Повертає (шлях, чи_тимчасовий)."""
    os.makedirs(TTS_DIR, exist_ok=True)
    key = hashlib.md5(f"{VOICE}|{rate}|{pitch}|{text}".encode("utf-8")).hexdigest()
    cacheable = len(text) <= 60
    name = key if cacheable else f"tmp_{os.getpid()}_{threading.get_ident()}_{key[:8]}"
    path = os.path.join(TTS_DIR, name + ".mp3")
    if cacheable and os.path.exists(path) and os.path.getsize(path) > 0:
        return path, False
    part = f"{path}.{threading.get_ident()}.part"
    asyncio.run(edge_tts.Communicate(text, VOICE, rate=rate, pitch=pitch).save(part))
    os.replace(part, path)
    return path, not cacheable


def _prewarm_tts() -> None:
    """Прибирає старий кеш і наперед готує найчастіші фрази - вони звучатимуть миттєво."""
    try:
        files = sorted(glob.glob(os.path.join(TTS_DIR, "*.mp3")), key=os.path.getmtime)
        for f in files[:-300]:
            os.remove(f)
        for f in glob.glob(os.path.join(TTS_DIR, "tmp_*")) + glob.glob(os.path.join(TTS_DIR, "*.part")):
            os.remove(f)
    except Exception:
        pass
    rate, pitch = MOODS["neutral"]
    for phrase in ("Відкриваю", "Запускаю", "Закриваю", "Слухаю", "Зробив гучніше", "Зробив тихіше",
                   "Не зрозумів команду", "Скасував", "Готово"):
        try:
            _tts_file(phrase, rate, pitch)
        except Exception:
            return          # немає інтернету - не страшно


def speak(text: str, mood: str = "neutral") -> None:
    """Озвучує текст. Мікрофон ігнорує ЛИШЕ час, коли звук реально грає
    (а не весь час генерації голосу, як раніше)."""
    global _echo_until
    rate, pitch = MOODS.get(mood, MOODS["neutral"])
    with _speak_lock:
        print(f"Джарвіс [{mood}]: {text}")
        emit("jarvis", text)
        set_state("speaking")
        try:
            path, temp = _tts_file(text, rate, pitch)
        except Exception as e:
            print(f"(помилка озвучення: {e})")
            emit("error", "Не вдалося озвучити відповідь (немає інтернету?)")
            set_state(idle_state())
            return
        speaking.set()
        try:
            pygame.mixer.music.load(path)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy():
                pygame.time.wait(30)
            pygame.mixer.music.unload()
        except Exception as e:
            print(f"(помилка відтворення: {e})")
        finally:
            _echo_until = time.time() + ECHO_TAIL
            speaking.clear()
            if temp:
                try:
                    os.remove(path)
                except Exception:
                    pass
            set_state(idle_state())


# ------------------- НЕЧІТКЕ ПОРІВНЯННЯ НАЗВ (стім -> Steam) -------------------

_TR = {"а": "a", "б": "b", "в": "v", "г": "h", "ґ": "g", "д": "d", "е": "e", "є": "ye",
       "ж": "zh", "з": "z", "и": "y", "і": "i", "ї": "yi", "й": "i", "к": "k", "л": "l",
       "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
       "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch", "ь": "",
       "ю": "yu", "я": "ya", "ы": "y", "э": "e", "ъ": "", "'": "", "’": ""}


def phon(s: str) -> str:
    """Приблизне 'звучання' рядка латиницею, щоб порівнювати укр. і англ. назви."""
    s = "".join(_TR.get(ch, ch) for ch in s.lower())
    for a, b in (("ph", "f"), ("c", "k"), ("q", "k"), ("w", "v"), ("y", "i"), ("x", "ks"),
                 ("ea", "i"), ("ee", "i"), ("oo", "u"), ("ou", "u")):
        s = s.replace(a, b)
    s = re.sub(r"[^a-z0-9]", "", s)
    return re.sub(r"(.)\1+", r"\1", s)


def skeleton(p: str) -> str:
    return re.sub(r"[aeiou]", "", p)


def score(query: str, name: str) -> float:
    """Наскільки query схожий на name (0..1)."""
    q, n = phon(query), phon(name)
    if len(q) < 3 or not n:
        return 0.0
    if q == n:
        return 1.0
    best = SequenceMatcher(None, q, n).ratio()
    if q in n:
        best = max(best, 0.8 + 0.15 * len(q) / len(n))
    if len(n) >= 4 and n in q:
        best = max(best, 0.8)
    qs = skeleton(q)
    if len(qs) >= 3 and qs == skeleton(n):
        best = max(best, 0.88)
    for w in re.split(r"[\s\-_.]+", name):     # порівняння з окремими словами назви
        pw = phon(w)
        if len(pw) >= 3:
            if pw == q:
                best = max(best, 0.92)
            elif len(qs) >= 3 and qs == skeleton(pw):
                best = max(best, 0.85)
    return best


# ------------------------ ПОШУК ПРОГРАМ НА КОМП'ЮТЕРІ ------------------------

APP_INDEX: dict = {}          # назва -> (тип, значення, підказка_процесу)
APP_READY = threading.Event()
_BAD_NAMES = ("uninstall", "видали", "удали", "documentation", "readme", "справка",
              "довідка", "release notes", "steamworks", "redistributable",
              "system configuration", "конфігурація системи", "msconfig",
              "system information", "відомості про систему")


def _scan_powershell() -> dict:
    """Усі програми з меню Пуск + магазинні (UWP)."""
    found = {}
    try:
        cmd = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
               "Get-StartApps | ConvertTo-Json -Compress")
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                           capture_output=True, timeout=40, creationflags=CREATE_NO_WINDOW)
        data = json.loads(r.stdout.decode("utf-8", "ignore") or "[]")
        if isinstance(data, dict):
            data = [data]
        for item in data:
            name, appid = item.get("Name"), item.get("AppID")
            if name and appid:
                found[name] = ("appid", appid, "")
    except Exception as e:
        print(f"(Get-StartApps не спрацював: {e})")
    return found


def _scan_shortcuts() -> dict:
    """Запасний варіант: ярлики .lnk з меню Пуск."""
    found = {}
    roots = [os.path.join(os.environ.get("ProgramData", ""), r"Microsoft\Windows\Start Menu\Programs"),
             os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs")]
    for root in roots:
        for lnk in glob.glob(os.path.join(root, "**", "*.lnk"), recursive=True):
            found[os.path.splitext(os.path.basename(lnk))[0]] = ("lnk", lnk, "")
    return found


def _scan_steam() -> dict:
    """Встановлені ігри Steam (запуск через steam://rungameid/ID)."""
    found = {}
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            steam_path = winreg.QueryValueEx(k, "SteamPath")[0].replace("/", "\\")
    except Exception:
        return found
    steam_exe = os.path.join(steam_path, "steam.exe")
    if os.path.exists(steam_exe):                 # сам Steam - напряму, без меню Пуск
        found["Steam"] = ("cmd", steam_exe, "steam")
    libs = {steam_path}
    try:
        with open(os.path.join(steam_path, "steamapps", "libraryfolders.vdf"),
                  encoding="utf-8", errors="ignore") as f:
            for m in re.finditer(r'"path"\s+"([^"]+)"', f.read()):
                libs.add(m.group(1).replace("\\\\", "\\"))
    except Exception:
        pass
    for lib in libs:
        for acf in glob.glob(os.path.join(lib, "steamapps", "appmanifest_*.acf")):
            try:
                with open(acf, encoding="utf-8", errors="ignore") as f:
                    t = f.read()
                app_id = re.search(r'"appid"\s+"(\d+)"', t).group(1)
                name = re.search(r'"name"\s+"([^"]+)"', t).group(1)
                found[name] = ("steam", app_id, "")
            except Exception:
                continue
    return found


def build_app_index() -> None:
    APP_READY.clear()
    index = _scan_powershell()
    if not index:
        index = _scan_shortcuts()
    index.update(_scan_steam())
    index = {n: v for n, v in index.items() if not any(b in n.lower() for b in _BAD_NAMES)}
    for key, cmd in APPS.items():                     # ручні мають найвищий пріоритет
        hint = os.path.splitext(os.path.basename(cmd))[0] if not cmd.endswith(":") else ""
        index[key] = ("cmd", cmd, hint)
    APP_INDEX.clear()
    APP_INDEX.update(index)
    print(f"(знайдено програм і ігор: {len(APP_INDEX)})")
    APP_READY.set()


def best_app(target: str):
    """Повертає (назва, запис, оцінка) найсхожішої програми."""
    APP_READY.wait(25)
    best = (None, None, 0.0)
    for name, entry in APP_INDEX.items():
        s = score(target, name)
        if s > best[2]:
            best = (name, entry, s)
    return best


def best_site(target: str):
    best = (None, 0.0)
    for key, url in SITES.items():
        if re.search(r"(?<!\w)" + re.escape(key) + r"(?!\w)", target):
            return url, 1.0
        s = score(target, key)
        if s > best[1]:
            best = (url, s)
    return best


def launch(entry) -> None:
    kind, value, _ = entry
    if kind == "appid":
        subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{value}"])
    elif kind == "steam":
        os.startfile(f"steam://rungameid/{value}")
    elif kind == "lnk":
        os.startfile(value)
    else:
        if os.path.exists(value):
            os.startfile(value)
        else:
            subprocess.Popen(f'start "" "{value}"', shell=True, creationflags=CREATE_NO_WINDOW)


SAFE_PROCS = {"explorer", "svchost", "csrss", "winlogon", "dwm", "system", "services", "lsass",
              "wininit", "smss", "taskmgr", "powershell", "cmd", "conhost", "jarvis"}


def _list_processes():
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                            "Get-Process | Select-Object Id,ProcessName,MainWindowHandle | ConvertTo-Json -Compress"],
                           capture_output=True, timeout=25, creationflags=CREATE_NO_WINDOW)
        procs = json.loads(r.stdout.decode("utf-8", "ignore") or "[]")
    except Exception as e:
        print(f"(не вдалось отримати список процесів: {e})")
        return []
    return [procs] if isinstance(procs, dict) else procs


def _maybe_shutdown_steam(procs: list) -> None:
    """Steam краще закривати його ж командою, інакше він одразу перезапускається."""
    if not any((p.get("ProcessName") or "").lower().startswith("steam") for p in procs):
        return
    entry = APP_INDEX.get("Steam")
    if entry and entry[0] == "cmd" and os.path.exists(entry[1]):
        subprocess.Popen([entry[1], "-shutdown"], creationflags=CREATE_NO_WINDOW)
        time.sleep(4)


def _close_ids(ids: list) -> None:
    """Спершу просить програми закритись самі, а через 2 секунди прибирає те, що лишилось."""
    if not ids:
        return
    idstr = ",".join(str(i) for i in ids)
    ps = (f"Get-Process -Id {idstr} -ErrorAction SilentlyContinue | "
          f"ForEach-Object {{ [void]$_.CloseMainWindow() }}; Start-Sleep -Seconds 2; "
          f"Get-Process -Id {idstr} -ErrorAction SilentlyContinue | "
          f"Stop-Process -Force -ErrorAction SilentlyContinue")
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                       capture_output=True, timeout=40, creationflags=CREATE_NO_WINDOW)
    except Exception as e:
        print(f"(помилка закриття: {e})")


def close_app(names: list) -> int:
    """Закриває програму(и) за назвою ПРОЦЕСУ, включно з тими, що сидять у треї
    (Steam, Discord, Telegram). Повертає кількість знайдених процесів."""
    procs = _list_processes()
    me = {os.getpid(), os.getppid()}
    hits = []
    for p in procs:
        pname = (p.get("ProcessName") or "")
        if p.get("Id") in me or pname.lower() in SAFE_PROCS:
            continue
        if any(score(n, pname) >= 0.8 for n in names):
            hits.append((p["Id"], pname))
    print(f"(закриваю процеси: {hits})")
    if not hits:
        return 0
    _maybe_shutdown_steam([{"ProcessName": pn} for _, pn in hits])
    _close_ids([i for i, _ in hits])
    return len(hits)


def close_all_apps() -> int:
    """Для режиму спокою: закриває всі видимі вікна, крім самого Джарвіса й системних."""
    procs = [p for p in _list_processes() if p.get("MainWindowHandle", 0) != 0]
    me = {os.getpid(), os.getppid()}
    ids = [p["Id"] for p in procs
           if p.get("Id") not in me and (p.get("ProcessName") or "").lower() not in SAFE_PROCS]
    print(f"(режим спокою: закриваю вікон - {len(ids)})")
    _maybe_shutdown_steam(procs)
    _close_ids(ids)
    return len(ids)


# --------------------------- ВІДКРИТТЯ В CHROME ---------------------------

CHROME_RE = re.compile(r"(?<!\w)(?:у|в|на|через)\s+(?:(?:гугл|google)\s+)?(?:хромі|хром|chrome)(?!\w)")
_use_chrome = False


def chrome_path():
    try:
        import winreg
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(root, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe") as k:
                    path = winreg.QueryValueEx(k, "")[0]
                    if os.path.exists(path):
                        return path
            except OSError:
                continue
    except Exception:
        pass
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"),
                 os.environ.get("LOCALAPPDATA")):
        if base:
            path = os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")
            if os.path.exists(path):
                return path
    return None


def open_url(url: str) -> None:
    """Відкриває адресу в Chrome (якщо просили) або в браузері за замовчуванням."""
    if _use_chrome:
        exe = chrome_path()
        if exe:
            subprocess.Popen([exe, url])
            return
        print("(Chrome не знайдено, відкриваю у браузері за замовчуванням)")
    webbrowser.open(url)


def open_named(target: str) -> bool:
    """Відкриває сайт або програму за назвою (використовують режими). True, якщо знайшов."""
    url, s_site = best_site(target)
    name, entry, s_app = best_app(target)
    if s_site >= 0.85 and s_site >= s_app:
        open_url(url)
        return True
    if s_app >= APP_MATCH_MIN:
        launch(entry)
        return True
    return False


# ------------------------------- РЕЖИМИ -------------------------------
# Тут можна редагувати/додавати свої режими.
# triggers   - фрази, за якими режим впізнається (кажи "Джарвіс, <одна з фраз>")
# open       - що відкрити: назви програм чи сайтів, як ти б сказав Джарвісу
# close      - що закрити перед тим, як щось відкривати (за бажанням)
# close_all  - True, щоб закрити взагалі всі відкриті вікна (для режиму спокою)
# music      - що увімкнути на YouTube (за бажанням, можна прибрати рядок)
# message    - що Джарвіс скаже, вмикаючи режим

MODES = {
    "game": {
        "triggers": ["ігровий режим", "режим гри", "геймерський режим", "режим геймера",
                     "ігровий мод", "режим для ігор"],
        "message": "Вмикаю ігровий режим",
        "open": ["steam"],
        "close": [],
    },
    "work": {
        "triggers": ["робочий режим", "режим роботи", "режим для роботи", "діловий режим"],
        "message": "Вмикаю робочий режим",
        "open": ["chrome"],
        "music": "lofi hip hop radio",
        "close": [],
    },
    "calm": {
        "triggers": ["режим спокою", "спокійний режим", "тихий режим",
                     "закрий все", "закрий всі програми", "вимкни все", "вимкни всі програми"],
        "message": "Закриваю все, гарного відпочинку",
        "mood": "calm",
        "close_all": True,
    },
}


def find_mode(cmd: str):
    for key, cfg in MODES.items():
        if has(cmd, *cfg["triggers"]):
            return key
    return None


def run_mode(key: str) -> None:
    cfg = MODES[key]
    speak(cfg.get("message", "Вмикаю режим"), mood=cfg.get("mood", "happy"))
    if cfg.get("close"):
        close_app(cfg["close"])
    if cfg.get("close_all"):
        close_all_apps()
    for target in cfg.get("open", []):
        if not open_named(target):
            print(f"(режим {key}: не знайшов {target!r})")
    if cfg.get("music"):
        music_command(cfg["music"])


# ------------------------------ ДІЇ ------------------------------

def press_key(vk: int, times: int = 1) -> None:
    for _ in range(times):
        ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
        ctypes.windll.user32.keybd_event(vk, 0, 2, 0)


def hotkey(*vks: int) -> None:
    for vk in vks:
        ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
    for vk in reversed(vks):
        ctypes.windll.user32.keybd_event(vk, 0, 2, 0)


_JUNK = [
    "увімкни", "ввімкни", "включи", "постав", "поставь", "запусти", "відкрий", "відкрити",
    "зіграй", "заграй", "пісню", "пісня", "пісні", "трек", "композицію", "музику", "мені",
    "будь ласка", "будь-ласка", "на ютубі", "на ютюбі", "в ютубі", "у ютубі",
    "на youtube", "на ютуб", "ютуб", "youtube", "з ютуба", "з youtube", "будь добрий",
]
_JUNK.sort(key=len, reverse=True)
_JUNK_RE = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(j) for j in _JUNK) + r")(?!\w)")

_TARGET_JUNK = ["відкрий", "відкрити", "запусти", "запустити", "увімкни", "ввімкни", "включи",
                "стартуй", "запали", "закрий", "закрити", "вимкни", "програму", "програма",
                "додаток", "гру", "гра", "мені", "будь ласка", "будь-ласка", "пожалуйста"]
_TARGET_JUNK.sort(key=len, reverse=True)
_TARGET_RE = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(j) for j in _TARGET_JUNK) + r")(?!\w)")


def clean_query(text: str) -> str:
    q = _JUNK_RE.sub(" ", text)
    return re.sub(r"\s+", " ", q).strip()


def clean_target(text: str) -> str:
    """'запусти будь ласка стім' -> 'steam' (прибирає службові слова, застосовує ALIASES)."""
    t = re.sub(r"\s+", " ", _TARGET_RE.sub(" ", text)).strip()
    for key in sorted(ALIASES, key=len, reverse=True):
        if re.search(r"(?<!\w)" + re.escape(key) + r"(?!\w)", t):
            return re.sub(r"(?<!\w)" + re.escape(key) + r"(?!\w)", ALIASES[key], t, count=1)
    # відмінки: "качалку" -> "качалка", "телеграмі" -> "телеграм"
    words = t.split()
    for key in sorted(ALIASES, key=len, reverse=True):
        n = len(key.split())
        if len(key) < 4:
            continue
        pk = phon(key)
        for i in range(len(words) - n + 1):
            chunk = " ".join(words[i:i + n])
            if abs(len(chunk) - len(key)) <= 2 and SequenceMatcher(None, phon(chunk), pk).ratio() >= 0.85:
                return " ".join(words[:i] + [ALIASES[key]] + words[i + n:])
    return t


def play_youtube(query: str) -> str:
    opts = {"quiet": True, "extract_flat": True, "skip_download": True, "no_warnings": True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"ytsearch1:{query}", download=False)
        entries = info.get("entries") or []
        if entries:
            open_url(f"https://www.youtube.com/watch?v={entries[0]['id']}")
            return entries[0].get("title") or query
    except Exception as e:
        print(f"(yt-dlp помилка: {e})")
    open_url("https://www.youtube.com/results?search_query=" + quote_plus(query))
    return query


def play_local_music() -> bool:
    exts = (".mp3", ".wav", ".ogg", ".flac")
    if not os.path.isdir(MUSIC_DIR):
        return False
    for f in os.listdir(MUSIC_DIR):
        if f.lower().endswith(exts):
            os.startfile(os.path.join(MUSIC_DIR, f))
            return True
    return False


def music_command(cmd: str) -> None:
    query = clean_query(cmd)
    if not query:
        speak("Яку пісню увімкнути?")
        alts = hear(timeout=7)
        query = clean_query(alts[0]) if alts else ""
        if not query:
            speak("Я не почув назву")
            return
    speak(f"Шукаю {query}")
    play_youtube(query)
    speak("Вмикаю")


def plural(n: int, forms: tuple) -> str:
    n = abs(n) % 100
    if 11 <= n <= 14:
        return forms[2]
    n %= 10
    if n == 1:
        return forms[0]
    if 2 <= n <= 4:
        return forms[1]
    return forms[2]


def confirm(question: str) -> bool:
    speak(question, mood="serious")
    alts = hear(timeout=7, since=time.time() - 0.6) or []     # лише відповідь ПІСЛЯ питання
    return any(has(a, "так", "підтверджую", "давай", "звісно", "авжеж") and not has(a, "ні", "не ")
               for a in alts)



# ------------------------------ ТАЙМЕРИ ------------------------------

_DURATION_RE = re.compile(
    r"(\d+)\s*(секунд\w*|сек\b|хвилин\w*|хв\b|годин\w*|год\b)")
_DURATION_WORDS = {"секунд": 1, "сек": 1, "хвилин": 60, "хв": 60, "годин": 3600, "год": 3600}

TIMERS: dict = {}               # id -> {"label", "end", "total", "timer"}
_timer_seq = itertools.count(1)
_timers_lock = threading.Lock()


def parse_duration(text: str):
    """'через 10 хвилин' -> (600, span). None, якщо числа не знайдено."""
    m = _DURATION_RE.search(text)
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2)
    for key, mult in _DURATION_WORDS.items():
        if unit.startswith(key):
            return n * mult, m.span()
    return None


def human_duration(seconds: int) -> str:
    seconds = int(seconds)
    if seconds % 3600 == 0 and seconds >= 3600:
        h = seconds // 3600
        return f"{h} {plural(h, ('годину', 'години', 'годин'))}"
    if seconds % 60 == 0 and seconds >= 60:
        m = seconds // 60
        return f"{m} {plural(m, ('хвилину', 'хвилини', 'хвилин'))}"
    if seconds > 60:
        m, s = divmod(seconds, 60)
        return (f"{m} {plural(m, ('хвилину', 'хвилини', 'хвилин'))} "
                f"{s} {plural(s, ('секунду', 'секунди', 'секунд'))}")
    return f"{seconds} {plural(seconds, ('секунду', 'секунди', 'секунд'))}"


def start_timer(seconds: float, label: str = "") -> int:
    """Запускає таймер (голосом або з вікна). Повертає його номер."""
    tid = next(_timer_seq)

    def fire():
        with _timers_lock:
            TIMERS.pop(tid, None)
        emit("timers")
        beep(1400, 150)
        speak(f"Час вийшов! {label}" if label else "Час вийшов!", mood="excited")

    t = threading.Timer(seconds, fire)
    t.daemon = True
    with _timers_lock:
        TIMERS[tid] = {"label": label, "end": time.time() + seconds, "total": float(seconds), "timer": t}
    t.start()
    emit("timers")
    return tid


def cancel_timer(tid=None) -> int:
    """Скасовує таймер за номером (None = всі). Повертає кількість скасованих."""
    with _timers_lock:
        ids = list(TIMERS) if tid is None else [tid]
        n = 0
        for i in ids:
            item = TIMERS.pop(i, None)
            if item:
                item["timer"].cancel()
                n += 1
    emit("timers")
    return n


def list_timers() -> list:
    now = time.time()
    with _timers_lock:
        items = [{"id": i, "label": v["label"], "left": max(0.0, v["end"] - now), "total": v["total"]}
                 for i, v in TIMERS.items()]
    return sorted(items, key=lambda x: x["left"])


def timer_command(cmd: str) -> None:
    parsed = parse_duration(cmd)
    if not parsed:
        speak("На скільки поставити? Скажи, наприклад: постав таймер на 5 хвилин")
        return
    seconds, (start, end) = parsed
    if seconds <= 0 or seconds > 12 * 3600:
        speak("Це якийсь дивний час, спробуй ще раз")
        return
    label = re.sub(r"(?<!\w)(постав|поставити|таймер|нагадай|нагадати|через|на|мені|"
                   r"будь ласка|будь-ласка)(?!\w)", " ", cmd[:start] + " " + cmd[end:])
    label = re.sub(r"\s+", " ", label).strip()
    start_timer(seconds, label)
    if label:
        speak(f"Добре, за {human_duration(seconds)} нагадаю: {label}", mood="happy")
    else:
        speak(f"Ставлю таймер на {human_duration(seconds)}", mood="happy")


def timers_left_command() -> None:
    items = list_timers()
    if not items:
        speak("Активних таймерів немає")
        return
    first = items[0]
    left = human_duration(max(1, round(first["left"])))
    extra = f". Всього таймерів: {len(items)}" if len(items) > 1 else ""
    if first["label"]:
        speak(f"Нагадаю через {left}: {first['label']}{extra}")
    else:
        speak(f"Таймер спрацює через {left}{extra}")


# ------------------------------ ПОГОДА ------------------------------
# Безкоштовно, без API-ключа (open-meteo.com).

WEATHER_CODES = {
    0: "ясно", 1: "переважно ясно", 2: "мінлива хмарність", 3: "хмарно",
    45: "туман", 48: "туман з інеєм",
    51: "легка мряка", 53: "мряка", 55: "сильна мряка",
    61: "невеликий дощ", 63: "дощ", 65: "сильний дощ",
    71: "невеликий сніг", 73: "сніг", 75: "сильний снігопад", 77: "сніжна крупа",
    80: "короткочасний дощ", 81: "зливи", 82: "сильні зливи",
    85: "снігові заряди", 86: "сильні снігові заряди",
    95: "гроза", 96: "гроза з градом", 99: "сильна гроза з градом",
}


# "у Львові", "в Одесі" тощо - називний відмінок API не завжди впізнає сам, тож для
# найпоширеніших міст тримаємо словник, а для решти пробуємо кілька типових закінчень.
CITY_FORMS = {
    "києві": "Київ", "києва": "Київ",
    "одесі": "Одеса", "одеси": "Одеса", "одесу": "Одеса",
    "харкові": "Харків", "харкова": "Харків",
    "дніпрі": "Дніпро", "дніпра": "Дніпро",
    "львові": "Львів", "львова": "Львів",
    "запоріжжі": "Запоріжжя",
    "вінниці": "Вінниця", "вінницю": "Вінниця",
    "полтаві": "Полтава", "полтаву": "Полтава",
    "чернігові": "Чернігів", "чернігова": "Чернігів",
    "сумах": "Суми",
    "житомирі": "Житомир", "житомира": "Житомир",
    "тернополі": "Тернопіль", "тернополя": "Тернопіль",
    "рівному": "Рівне", "рівного": "Рівне",
    "луцьку": "Луцьк", "луцька": "Луцьк",
    "ужгороді": "Ужгород", "ужгорода": "Ужгород",
    "чернівцях": "Чернівці",
    "херсоні": "Херсон", "херсона": "Херсон",
    "миколаєві": "Миколаїв", "миколаєва": "Миколаїв",
    "кропивницькому": "Кропивницький",
    "івано-франківську": "Івано-Франківськ",
}


def _city_candidates(raw: str) -> list:
    low = raw.lower()
    cands = []
    if low in CITY_FORMS:
        cands.append(CITY_FORMS[low])
    cands.append(raw)
    if low.endswith("ові") or low.endswith("еві"):
        cands.append(raw[:-3])
    if low.endswith("і"):
        cands.append(raw[:-1] + "а")
    if low.endswith("ому"):
        cands.append(raw[:-3] + "ий")
    if low.endswith("у") or low.endswith("ю"):
        cands.append(raw[:-1])
    seen, out = set(), []
    for c in cands:
        if c.lower() not in seen:
            seen.add(c.lower())
            out.append(c)
    return out


def get_weather(city: str):
    """Повертає (назва_міста, температура, опис, вітер) або None, якщо не вдалось."""
    results, name = None, None
    try:
        for candidate in _city_candidates(city):
            geo_url = ("https://geocoding-api.open-meteo.com/v1/search?name="
                       f"{quote_plus(candidate)}&count=1&language=uk")
            with urllib.request.urlopen(geo_url, timeout=10) as r:
                geo = json.loads(r.read().decode("utf-8"))
            results = geo.get("results") or []
            if results:
                break
        if not results:
            return None
        lat, lon, name = results[0]["latitude"], results[0]["longitude"], results[0]["name"]
        w_url = ("https://api.open-meteo.com/v1/forecast?latitude="
                 f"{lat}&longitude={lon}&current=temperature_2m,weather_code,wind_speed_10m"
                 "&timezone=auto")
        with urllib.request.urlopen(w_url, timeout=10) as r:
            fc = json.loads(r.read().decode("utf-8"))
        cur = fc["current"]
        temp = round(cur["temperature_2m"])
        wind = round(cur["wind_speed_10m"])
        desc = WEATHER_CODES.get(cur["weather_code"], "погода без особливостей")
        return name, temp, desc, wind
    except Exception as e:
        print(f"(погода: помилка {e})")
        return None


def weather_command(cmd: str) -> None:
    m = re.search(r"(?:в|у|для)\s+([а-щьюяіїєґ'’\- ]{3,30})", cmd)
    city = m.group(1).strip() if m else DEFAULT_CITY
    result = get_weather(city)
    if not result:
        speak(f"Не вдалось дізнатись погоду в {city}. Перевір інтернет або назву міста")
        return
    name, temp, desc, wind = result
    speak(f"У місті {name} зараз {temp} {plural(temp, ('градус', 'градуси', 'градусів'))}, "
          f"{desc}, вітер {wind} {plural(wind, ('метр', 'метри', 'метрів'))} за секунду")


# ------------------------------ ЖАРТІВЛИВІ КОМАНДИ ------------------------------

def pick_command(cmd: str) -> None:
    """'вибери за мене піцу чи суші' -> випадково обирає один із варіантів."""
    text = re.sub(r"(?<!\w)(вибери|обери|за мене|мені|будь ласка|будь-ласка|"
                  r"що вибрати|порадь|що краще)(?!\w)", " ", cmd)
    parts = [p.strip(" ?.,!") for p in re.split(r"\bчи\b|\bабо\b", text) if p.strip(" ?.,!")]
    if len(parts) < 2:
        speak("Скажи варіанти через 'чи', наприклад: вибери за мене піцу чи суші")
        return
    speak(f"Я обираю: {random.choice(parts)}", mood="happy")


# --------------------------- ОБРОБКА КОМАНД ---------------------------

MUSIC_WORDS = ("трек", "пісню", "пісня", "пісні", "музику", "композицію", "зіграй", "заграй")
OPEN_VERBS = ("відкрий", "відкрити", "запусти", "запустити", "увімкни", "ввімкни", "включи",
              "стартуй", "запали", "постав")
PLAY_VERBS = ("увімкни", "ввімкни", "включи", "постав")


def has(cmd: str, *words: str) -> bool:
    """Чи є в команді будь-яке зі слів (збіг з початку слова: 'наступн' -> 'наступний')."""
    return any(re.search(r"(?<!\w)" + re.escape(w), cmd) for w in words)


def open_command(cmd: str, cmds: list) -> bool:
    play_like = has(cmd, *PLAY_VERBS)
    need = 0.85 if play_like else APP_MATCH_MIN
    targets = [t for t in (clean_target(c) for c in cmds) if t]
    if not targets:
        speak("Що саме відкрити?")
        return True

    best_kind, best_val, best_score, best_name = None, None, 0.0, ""
    for t in targets:
        url, s_site = best_site(t)
        if s_site > best_score:
            best_kind, best_val, best_score, best_name = "site", url, s_site, t
        name, entry, s_app = best_app(t)
        if s_app > best_score:
            best_kind, best_val, best_score, best_name = "app", entry, s_app, name

    print(f"(ціль: {targets[0]!r} -> {best_name!r}, збіг {best_score:.2f})")
    if best_score >= need:
        if best_kind == "site":
            open_url(best_val)
            speak("Відкриваю")
        else:
            launch(best_val)
            speak("Запускаю")
        return True
    if play_like:
        return UNKNOWN          # схоже, це назва пісні - нехай спробує музика
    speak(f"Не знайшов програму {targets[0]}")
    return True


def close_command(cmd: str, cmds: list) -> bool:
    targets = [t for t in (clean_target(c) for c in cmds) if t]
    if not targets:
        speak("Що закрити?")
        return True
    names = set()
    for t in targets:
        names.add(t)
        name, entry, sc = best_app(t)
        if sc >= APP_MATCH_MIN:
            names.add(name)
            if entry[2]:
                names.add(entry[2])
    print(f"(закрити: {names})")
    speak("Закриваю" if close_app(list(names)) else "Не знайшов запущеної програми з такою назвою")
    return True


def dispatch(cmd: str, cmds: list):
    """Виконує команду. True = працюємо далі, False = вимкнутись, UNKNOWN = не зрозуміла."""
    if has(cmd, "вимкнись", "до побачення", "вийди", "завершити роботу"):
        speak("До зустрічі!", mood="happy")
        return False

    if learning_voice_command(cmd):
        return True
    if re.fullmatch(r"навчись( ім['ʼ]?я| імені| слова джарвіс| своєму імені)?", cmd.strip()) or \
            has(cmd, "вивчи моє", "запамʼятай мій голос", "запам'ятай мій голос"):
        learn_wake_word()
        return True

    mode = find_mode(cmd)
    if mode:
        run_mode(mode)
        return True

    # --- живлення ПК ---
    if has(cmd, "скасуй вимкнення", "скасуй перезавантаження"):
        subprocess.Popen(["shutdown", "/a"], creationflags=CREATE_NO_WINDOW)
        speak("Скасував")
        return True
    if has(cmd, "вимкни комп", "вимкни пк", "вимкнути комп", "вимкни ноутбук"):
        if confirm("Точно вимкнути комп'ютер?"):
            subprocess.Popen(["shutdown", "/s", "/t", "15"], creationflags=CREATE_NO_WINDOW)
            speak("Вимикаю через п'ятнадцять секунд. Скажи скасуй вимкнення, щоб зупинити", mood="calm")
        else:
            speak("Добре, не вимикаю")
        return True
    if has(cmd, "перезавантаж"):
        if confirm("Точно перезавантажити комп'ютер?"):
            subprocess.Popen(["shutdown", "/r", "/t", "15"], creationflags=CREATE_NO_WINDOW)
            speak("Перезавантажую через п'ятнадцять секунд", mood="calm")
        else:
            speak("Добре, не перезавантажую")
        return True

    # --- таймери, погода, забавки ---
    if has(cmd, "скасуй таймер", "скасуй всі таймери", "скасуй нагадування", "вимкни таймер",
           "зупини таймер", "видали таймер"):
        speak("Скасував" if cancel_timer() else "Активних таймерів немає")
        return True
    if has(cmd, "скільки залишилось", "скільки лишилось", "скільки залишилося", "скільки ще"):
        timers_left_command()
        return True
    if has(cmd, "таймер", "нагадай", "нагадати"):
        timer_command(cmd)
        return True
    if has(cmd, "погода"):
        weather_command(cmd)
        return True
    if has(cmd, "монетку", "підкинь монету", "кинь монету", "орел чи решка"):
        speak(random.choice(["Орел", "Решка"]), mood="happy")
        return True
    if has(cmd, "кинь кубик", "кубик", "кинь кістку"):
        m = re.search(r"(\d+)", cmd)
        sides = int(m.group(1)) if m else 6
        speak(f"Випало {random.randint(1, sides)}", mood="happy")
        return True
    if has(cmd, "вибери за мене", "що мені вибрати", "порадь що обрати"):
        pick_command(cmd)
        return True

    # --- інформація ---
    if has(cmd, "котра година", "який час", "скільки часу"):
        n = datetime.datetime.now()
        speak(f"Зараз {n.hour} {plural(n.hour, ('година', 'години', 'годин'))} "
              f"{n.minute} {plural(n.minute, ('хвилина', 'хвилини', 'хвилин'))}")
        return True
    if has(cmd, "яке число", "яка дата", "який день", "яке сьогодні"):
        d = datetime.date.today()
        speak(f"Сьогодні {d.day} {MONTHS[d.month - 1]}, {WEEKDAYS[d.weekday()]}")
        return True
    if has(cmd, "як справи") or re.fullmatch(r"(а )?як ти( там)?", cmd.strip()):
        speak("Все чудово, працюю на повну потужність. А в тебе як?", mood="happy")
        return True
    if has(cmd, "хто ти", "як тебе звати"):
        speak("Я Джарвіс, твій голосовий асистент.")
        return True
    if has(cmd, "дякую", "дякуй"):
        speak("Завжди радий допомогти", mood="happy")
        return True
    if has(cmd, "жарт", "анекдот"):
        speak(random.choice(JOKES), mood="happy")
        return True

    # --- медіа (до пошуку музики) ---
    if has(cmd, "пауза", "паузу", "зупини", "продовжи", "грай далі", "відновити"):
        press_key(VK_MEDIA_PLAY_PAUSE)
        return True
    if has(cmd, "наступн"):
        press_key(VK_MEDIA_NEXT)
        return True
    if has(cmd, "попередн"):
        press_key(VK_MEDIA_PREV)
        return True

    # --- звук ---
    if has(cmd, "гучніше"):
        press_key(VK_VOL_UP, 5)
        speak("Зробив гучніше")
        return True
    if has(cmd, "тихіше"):
        press_key(VK_VOL_DOWN, 5)
        speak("Зробив тихіше")
        return True
    if has(cmd, "без звуку", "вимкни звук", "увімкни звук", "включи звук"):
        press_key(VK_MUTE)
        return True

    # --- система ---
    if has(cmd, "заблокуй"):
        speak("Блокую комп'ютер")
        ctypes.windll.user32.LockWorkStation()
        return True
    if has(cmd, "скріншот", "знімок екрана", "зроби знімок"):
        hotkey(VK_LWIN, VK_SNAPSHOT)          # зберігається в Зображення\Знімки екрана
        speak("Готово, знімок збережено в папці Знімки екрана")
        return True
    if has(cmd, "робочий стіл", "згорни все", "згорни всі"):
        hotkey(VK_LWIN, VK_D)
        return True
    if has(cmd, "закрий вікно", "закрити вікно"):
        hotkey(VK_ALT, VK_F4)
        return True
    if has(cmd, "оновити список програм", "оновити програми", "онови програми", "онови список"):
        speak("Оновлюю список програм")
        build_app_index()
        speak(f"Знайшов {len(APP_INDEX)} програм та ігор")
        return True
    if has(cmd, "закрий", "закрити"):
        return close_command(cmd, cmds)

    # --- музика ---
    if has(cmd, "мою музику"):
        speak("Вмикаю вашу музику" if play_local_music() else "Не знайшов музику у вашій папці")
        return True
    if has(cmd, *MUSIC_WORDS):
        music_command(cmd)
        return True

    # --- відкрити сайт / програму / гру ---
    if has(cmd, *OPEN_VERBS):
        r = open_command(cmd, cmds)
        if r is UNKNOWN:              # "увімкни <щось невідоме>" = назва пісні
            music_command(cmd)
            return True
        return r

    # --- пошук в інтернеті ---
    if has(cmd, "пошукай", "знайди", "загугли"):
        q = re.sub(r"(?<!\w)(пошукай|знайди|загугли|в інтернеті|в гуглі|мені)(?!\w)", " ", cmd)
        q = re.sub(r"\s+", " ", q).strip()
        speak(f"Шукаю {q}")
        open_url("https://www.google.com/search?q=" + quote_plus(q))
        return True

    return UNKNOWN



_ai_history: list = []          # [{"role": "user"/"model", "parts":[{"text": "..."}]}]
LAST_AI_ERROR = ""              # nokey / badkey / quota / network / empty / other

AI_ERROR_SPEECH = {
    "nokey": "Щоб відповідати на питання, мені потрібен ключ Gemini. Підказка в журналі справа",
    "badkey": "Ключ Gemini не підходить. Перевірте його або створіть новий",
    "quota": "Ліміт запитань до штучного інтелекту вичерпано, спробуйте трохи пізніше",
    "network": "Немає зв'язку зі штучним інтелектом. Перевірте інтернет",
    "empty": "Штучний інтелект повернув порожню відповідь. Спробуйте перефразувати питання",
}


def _ai_fail(code: str, detail: str = "") -> None:
    global LAST_AI_ERROR
    LAST_AI_ERROR = code
    if detail:
        print(f"(ШІ: {detail})")
        emit("error", f"ШІ: {detail}")


def ask_ai(question: str):
    """Питає Gemini. Повертає текст відповіді або None (причина - у LAST_AI_ERROR)."""
    global GEMINI_MODEL, LAST_AI_ERROR
    LAST_AI_ERROR = ""
    if not GEMINI_API_KEY:
        LAST_AI_ERROR = "nokey"
        emit("log", "Немає ключа Gemini. Отримай безкоштовно на aistudio.google.com/app/apikey "
                    "і введи тут: /key ТВІЙ_КЛЮЧ")
        return None

    _ai_history.append({"role": "user", "parts": [{"text": question}]})
    del _ai_history[:-AI_HISTORY_LIMIT * 2 or None]        # лишаємо тільки останні репліки
    while _ai_history and _ai_history[0]["role"] != "user":
        _ai_history.pop(0)

    body = json.dumps({
        "system_instruction": {"parts": [{"text": AI_PERSONA}]},
        "contents": _ai_history,
        # Запас токенів: моделі Gemini "думають" перед відповіддю, і ці токени теж
        # рахуються в ліміті. З 200 відповідь могла вийти ПОРОЖНЬОЮ - ось чому Джарвіс мовчав.
        "generationConfig": {"maxOutputTokens": 2048, "temperature": 0.8},
    }).encode("utf-8")

    models = [GEMINI_MODEL] + [m for m in GEMINI_FALLBACK_MODELS if m != GEMINI_MODEL]
    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        req = urllib.request.Request(url, data=body, method="POST", headers={
            "Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY,
        })
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "ignore")[:300]
            low = text.lower()
            print(f"(Gemini {model}: HTTP {e.code}: {text})")
            if e.code == 404 or (e.code == 400 and "not found" in low):
                _ai_fail("other", f"модель {model} недоступна, пробую іншу")
                continue
            if e.code in (500, 502, 503, 504):
                _ai_fail("other", f"{model} перевантажена, пробую іншу")
                continue
            if e.code == 429:
                _ai_fail("quota", f"ліміт вичерпано ({model})")
            elif e.code in (400, 401, 403):
                _ai_fail("badkey", f"HTTP {e.code} - {text[:120]}")
            else:
                _ai_fail("other", f"HTTP {e.code}")
            break
        except Exception as e:
            _ai_fail("network", f"{type(e).__name__}: {e}")
            break

        try:
            cand = (data.get("candidates") or [{}])[0]
            parts = (cand.get("content") or {}).get("parts") or []
            answer = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
        except Exception:
            answer = ""
        answer = re.sub(r"[*#_`>]+", "", answer).strip()       # markdown не треба озвучувати
        if answer:
            GEMINI_MODEL = model                              # ця модель працює - лишаємо її
            _ai_history.append({"role": "model", "parts": [{"text": answer}]})
            return answer
        reason = (data.get("promptFeedback") or {}).get("blockReason") or cand.get("finishReason", "?")
        _ai_fail("empty", f"порожня відповідь від {model} ({reason})")

    if _ai_history and _ai_history[-1]["role"] == "user":
        _ai_history.pop()           # питання не вдалось - не засмічуємо пам'ять
    return None


def speak_ai_failure() -> None:
    speak(AI_ERROR_SPEECH.get(LAST_AI_ERROR, "Не вдалося отримати відповідь від штучного інтелекту"))


QUESTION_STARTS = ("що ", "хто ", "чому", "навіщо", "як ", "де ", "коли ", "скільки ", "який ", "яка ",
                   "яке ", "які ", "чи ", "куди ", "звідки ", "чим ", "чого ", "кому ", "поясни",
                   "розкажи", "скажи", "порадь", "допоможи", "придумай", "напиши", "переклади", "порахуй",
                   "а що ", "а як ", "а чому", "а хто ")
# питання, на які Джарвіс відповідає сам, без ШІ
BUILTIN_INFO = ("котра година", "який час", "скільки часу", "погода", "яке число", "яка дата",
                "який день", "яке сьогодні", "як справи", "хто ти", "як тебе звати", "жарт", "анекдот",
                "залишилось", "лишилось", "залишилося")
# слова-дії: якщо вони є, це команда, а не питання
ACTION_WORDS = ("відкр", "закр", "запуск", "увімкн", "ввімкн", "включи", "вимкн", "постав", "пошукай",
                "знайди", "загугли", "кинь", "підкинь", "вибери", "скріншот", "заблокуй", "перезавантаж",
                "таймер", "нагадай", "зіграй", "заграй", "скасуй",
                "режим", "оновити список", "оновити програми", "монет", "кубик",
                "навчи", "запамʼят", "запам'ят", "забудь", "коли я кажу", "коли я скажу", "нова команда",
                "мої команди")
MEDIA_WORDS = ("гучніш", "тихіш", "наступн", "попередн", "пауз")     # команда лише якщо фраза коротка


def is_question(cmd: str) -> bool:
    c = cmd.strip()
    if not c or has(c, *ACTION_WORDS) or has(c, *BUILTIN_INFO):
        return False
    if has(c, *MEDIA_WORDS) and len(c.split()) <= 3 and not c.startswith(QUESTION_STARTS):
        return False
    if re.fullmatch(r"(а )?як ти( там)?", c):
        return False
    return c.endswith("?") or c.startswith(QUESTION_STARTS) or len(c.split()) >= 6 and not has(c, *OPEN_VERBS)


def handle_slash(text: str) -> None:
    """Службові команди, які вводяться в рядок: /key, /model, /city, /help."""
    global GEMINI_API_KEY, GEMINI_MODEL, DEFAULT_CITY
    parts = text.strip().split(None, 1)
    name, arg = parts[0].lower(), (parts[1].strip() if len(parts) > 1 else "")
    if name == "/key":
        if not arg:
            emit("log", "Використання: /key ТВІЙ_КЛЮЧ  (ключ з aistudio.google.com/app/apikey)")
            return
        GEMINI_API_KEY = arg
        save_config(gemini_api_key=arg)
        emit("log", "Ключ Gemini збережено. Перевіряю...")
        answer = ask_ai("Скажи одним коротким реченням, що ти готовий допомагати.")
        if answer:
            speak(answer, mood="happy")
        else:
            speak_ai_failure()
    elif name == "/model":
        if arg:
            GEMINI_MODEL = arg
            save_config(gemini_model=arg)
            emit("log", f"Модель ШІ: {arg}")
        else:
            emit("log", f"Поточна модель ШІ: {GEMINI_MODEL}")
    elif name == "/city":
        if arg:
            DEFAULT_CITY = arg
            save_config(city=arg)
            emit("log", f"Місто за замовчуванням: {arg}")
        else:
            emit("log", f"Місто за замовчуванням: {DEFAULT_CITY}")
    elif name == "/learn":
        learn_wake_word()
    elif name == "/forget":
        forget_wake_words()
    elif name == "/mics":
        for i, n, d in list_microphones():
            emit("log", f"[{i}] {n}" + ("  (за замовчуванням)" if d else ""))
    else:
        emit("log", "Команди: /mics - мікрофони,  /key КЛЮЧ - ключ Gemini,  /model НАЗВА - модель ШІ,  /city МІСТО - для погоди")


def handle(cmds: list, quiet: bool = False, _depth: int = 0) -> bool:
    """Пробує кожен варіант розпізнавання, доки якийсь не зрозуміє."""
    global _use_chrome
    _use_chrome = ALWAYS_USE_CHROME or any(CHROME_RE.search(c) for c in cmds)
    cmds = [re.sub(r"\s+", " ", CHROME_RE.sub(" ", c)).strip() for c in cmds]

    # Ваші власні (навчені) команди мають найвищий пріоритет
    item = find_custom(cmds)
    if item:
        return run_custom(item, _depth)

    # Питання й розмова - одразу до ШІ, а не в пошуки "команди"
    if any(has(c, *AI_TRIGGERS) for c in cmds) or is_question(cmds[0]):
        question = next((c for c in cmds if has(c, *AI_TRIGGERS)), cmds[0])
        answer = ask_ai(question)
        if answer:
            speak(answer)
        else:
            speak_ai_failure()
        return True

    for c in cmds:
        r = dispatch(c, cmds)
        if r is not UNKNOWN:
            return r
    if not quiet:
        answer = ask_ai(cmds[0])
        if answer:
            speak(answer)
        elif LAST_AI_ERROR and LAST_AI_ERROR != "nokey":
            speak_ai_failure()
        else:
            speak("Не зрозумів команду")
    return True



# ------------------------- СЛОВО "ДЖАРВІС" -------------------------

_WAKE_KEYS = None


def _wake_keys() -> set:
    global _WAKE_KEYS
    if _WAKE_KEYS is None:
        _WAKE_KEYS = {p for p in (phon(w) for w in WAKE_WORDS + WAKE_LEARNED) if len(p) >= 4}
    return _WAKE_KEYS


def is_wake_word(word: str) -> bool:
    if word in WAKE_WORDS or word in WAKE_LEARNED:
        return True
    p = phon(word)
    if not 4 <= len(p) <= 11:
        return False
    return any(p == k or (abs(len(p) - len(k)) <= 1 and SequenceMatcher(None, p, k).ratio() >= WAKE_SIMILARITY)
               for k in _wake_keys())


def find_wake_word(text: str):
    """Шукає 'Джарвіс' на початку фрази (порівнюючи ЗВУЧАННЯ, а не букви).
    Повертає текст ПІСЛЯ нього або None."""
    words = re.findall(r"[\w'’-]+", text.lower())
    for i, w in enumerate(words[:5]):
        if len(w) <= 4 and i + 1 < len(words) and is_wake_word(w + words[i + 1]):   # "джар віс"
            return " ".join(words[i + 2:])
        if is_wake_word(w):
            return " ".join(words[i + 1:])
    return None


_LEARN_STOP = {"так", "ні", "що", "як", "а", "і", "та", "це", "ну", "добре", "давай", "привіт", "алло",
               "слухай", "окей", "ок", "ой", "ага", "угу", "то", "той", "вже", "ще", "тут", "там"}


def learn_wake_word(tries: int = 3) -> None:
    """Навчання: ви кажете "Джарвіс" кілька разів, ми запамʼятовуємо, як Google це записує."""
    stop_words = _LEARN_STOP | set(OPEN_VERBS) | set(_TARGET_JUNK)
    speak(f"Навчання. Після кожного сигналу скажіть лише одне слово: Джарвіс. Всього {tries} рази.")
    found = []
    for i in range(tries):
        time.sleep(0.3)
        beep(1200, 120)
        set_state("awaiting")
        alts = hear(timeout=6, typed=False, since=time.time() - 0.3)
        if not alts:
            emit("log", f"Спроба {i + 1}: нічого не почув")
            continue
        emit("log", f"Спроба {i + 1}: Google записав {alts[:4]}")
        for a in alts[:4]:
            words = re.findall(r"[\w'’-]+", a.lower())
            if not words:
                continue
            if len(words[0]) <= 4 and len(words) > 1:      # "жар віт" - слово розірване навпіл
                cands = [words[0] + words[1]]
            else:
                cands = [words[0]]
            for w in cands:
                if w not in stop_words and len(phon(w)) >= 4 and w not in found:
                    found.append(w)
    set_state(idle_state())
    new = [w for w in found if w not in WAKE_WORDS and w not in WAKE_LEARNED]
    if not found:
        speak("Я нічого не почув. Перевірте мікрофон на вкладці мікрофон і спробуйте ще раз.")
        return
    for w in new:
        DB.add_wake(w, "голос")
    reload_learning()
    if new:
        emit("log", "Запамʼятав нові варіанти: " + ", ".join(new))
    speak("Готово, запамʼятав. Спробуйте: Джарвіс, котра година.", mood="happy")


def forget_wake_words() -> None:
    DB.clear("wake_words")
    reload_learning()
    emit("log", "Навчені варіанти слова «Джарвіс» видалено")


def extract_command(alts: list):
    """З усіх варіантів Google вибирає ті, де є слово 'Джарвіс'."""
    rests = [r for r in (find_wake_word(a) for a in alts) if r is not None]
    if rests:
        non_empty = [r for r in rests if r]
        return True, (non_empty or [""])
    return False, alts


# =========================== НАВЧАННЯ (база SQLite) ===========================
#   Власні команди:  "Джарвіс, нова команда" -> діалог
#                    "Джарвіс, коли я кажу кіно відкрий нетфлікс"
#                    "Джарвіс, запамʼятай що доброго ранку це увімкни lofi"
#                    "Джарвіс, забудь команду кіно"      "Джарвіс, мої команди"
#   Виправлення, назви програм, слово "Джарвіс", історія - вкладка 07 НАВЧАННЯ.

_CUSTOM: list = []          # кеш команд з бази
_FILLER = {"будь", "ласка", "будь-ласка", "мені", "а", "ну", "давай", "зараз", "ось", "там", "пожалуйста", "і"}
_CORR: list = []            # [(regex, right, id)]
_hist_lock = threading.Lock()


def reload_learning() -> None:
    """Перечитує базу у швидкі кеші. Викликається після кожної зміни."""
    global _CUSTOM, _CORR, ALIASES, WAKE_LEARNED, _WAKE_KEYS
    try:
        _CUSTOM = [dict(r, norm=r["phrase"]) for r in DB.commands()]
        _CORR = [(re.compile(r"(?<!\w)" + re.escape(r["wrong"]) + r"(?!\w)"), r["right"], r["id"])
                 for r in DB.corrections()]
        al = dict(BASE_ALIASES)
        al.update({r["spoken"]: r["target"] for r in DB.aliases()})
        ALIASES = al
        WAKE_LEARNED = [r["word"] for r in DB.wake_words()]
        _WAKE_KEYS = None
    except Exception as e:
        print(f"(база: помилка читання {e})")
    emit("learning")
    emit("settings")


def apply_corrections(alts: list) -> list:
    """'відкрий стін' -> 'відкрий стім' згідно з таблицею виправлень."""
    if not _CORR:
        return alts
    out = []
    for a in alts:
        t = norm(a)
        for rx, right, cid in _CORR:
            t2 = rx.sub(right, t)
            if t2 != t:
                t = t2
                try:
                    DB.hit_correction(cid)
                except Exception:
                    pass
        if t not in out:
            out.append(t)
    if out and out[0] != norm(alts[0]):
        print(f"(виправлено: {alts[0]!r} -> {out[0]!r})")
    return out


def _history(raw: str, alts: list, status: str) -> None:
    def work():
        try:
            with _hist_lock:
                DB.add_history(raw or (alts[0] if alts else ""), alts[0] if alts else "", alts[1:], status)
        except Exception as e:
            print(f"(історія: {e})")
    threading.Thread(target=work, daemon=True).start()


def find_custom(cmds: list):
    """Шукає серед ваших команд. Точний збіг, фраза всередині (плюс кілька зайвих слів
    на кшталт 'будь ласка') або дуже схожа фраза."""
    if not _CUSTOM:
        return None
    best, best_score = None, 0.0
    for c in cmds:
        n = norm(c)
        if not n:
            continue
        for item in _CUSTOM:
            p = item["norm"]
            if n == p:
                return item
            sc = SequenceMatcher(None, n, p).ratio()
            if len(p) >= 3 and re.search(r"(?<!\w)" + re.escape(p) + r"(?!\w)", n):
                extra = set(n.split()) - set(p.split())
                if extra <= _FILLER:                         # "будь ласка кіно" = "кіно"
                    sc = max(sc, 0.9)
            if sc > best_score:
                best, best_score = item, sc
    return best if best_score >= 0.86 else None


def run_custom(item: dict, depth: int = 0) -> bool:
    kind, action = item["kind"], item["action"]
    print(f"(моя команда: {item['phrase']!r} -> {kind}: {action!r})")
    try:
        DB.touch_command(item["id"])
    except Exception:
        pass
    if kind == "reply":
        speak(action, mood="happy")
    elif kind == "command":
        if depth >= 3:
            speak("Команди посилаються одна на одну по колу, зупиняюсь")
            return True
        parts = [p.strip() for p in re.split(r"\s*(?:;|\n)\s*", action) if p.strip()]   # кілька дій через ;
        for part in parts:
            if not handle([part.lower()], _depth=depth + 1):
                return False
    elif kind == "open":
        if open_named(clean_target(action.lower())):
            speak("Відкриваю")
        else:
            speak(f"Не знайшов {action}")
    elif kind == "url":
        open_url(action if re.match(r"^[a-z]+://", action, re.I) else "https://" + action)
        speak("Відкриваю")
    elif kind == "run":
        try:
            path = os.path.expandvars(os.path.expanduser(action.strip('"')))
            if os.path.exists(path):
                os.startfile(path)
            else:
                subprocess.Popen(action, shell=True, creationflags=CREATE_NO_WINDOW)
            speak("Виконую")
        except Exception as e:
            speak("Не вдалося виконати")
            emit("error", f"Команда «{item['phrase']}»: {e}")
    return True


# ---- голосові діалоги навчання ----

_ACTION_START = ("відкрий", "запусти", "увімкни", "ввімкни", "включи", "закрий", "скажи", "відповідай",
                 "постав", "пошукай", "знайди", "зроби", "покажи", "вимкни", "кинь", "підкинь", "заблокуй",
                 "гучніше", "тихіше", "пауза", "наступний", "яка погода", "котра година", "ігровий", "робочий",
                 "режим", "нагадай", "розкажи")


def _action_from_text(text: str):
    """'скажи привіт' -> ('reply', 'привіт'); 'відкрий ютуб' -> ('command', 'відкрий ютуб')."""
    t = text.strip()
    m = re.match(r"^(?:скажи|відповідай|відповідь|відповісти)\s+(.+)$", t)
    if m:
        return "reply", m.group(1)
    return "command", t


def _split_phrase_action(text: str):
    """'кіно відкрий нетфлікс' -> ('кіно', 'відкрий нетфлікс'); 'X це Y' / 'X означає Y'."""
    m = re.match(r"^(?:що\s+)?(.+?)\s+(?:це|означає|значить|то)\s+(.+)$", text)
    if m:
        return m.group(1).strip(" ,"), m.group(2).strip(" ,")
    words = text.split()
    for i in range(1, len(words)):
        rest = " ".join(words[i:])
        if any(rest.startswith(a) for a in _ACTION_START):
            return " ".join(words[:i]).strip(" ,"), rest
    return None


def _save_command(phrase: str, action_text: str) -> None:
    rest = find_wake_word(phrase)
    phrase = norm(rest if rest else phrase)
    kind, action = _action_from_text(norm(action_text))
    if len(phrase) < 2:
        speak("Фраза надто коротка")
        return
    try:
        DB.add_command(phrase, kind, action)
    except Exception as e:
        speak("Не вдалося зберегти")
        emit("error", f"База: {e}")
        return
    reload_learning()
    emit("log", f"Нова команда: «{phrase}» → {COMMAND_KINDS[kind]}: {action}")
    speak(f"Запамʼятав. Коли скажете {phrase}, я {'відповім' if kind == 'reply' else 'виконаю'}: {action}",
          mood="happy")


def learn_command_dialog() -> None:
    speak("Яку фразу мені запамʼятати? Скажіть її після сигналу.")
    beep(1200, 120)
    set_state("awaiting")
    a = hear(timeout=8, since=time.time() - 0.3)
    if not a:
        speak("Не почув фразу. Спробуйте ще раз.")
        return
    phrase = a[0]
    speak(f"Що робити, коли почую: {phrase}? Скажіть команду, наприклад відкрий ютуб. "
          f"Або скажіть: скажи, і текст відповіді.")
    beep(1200, 120)
    set_state("awaiting")
    b = hear(timeout=10, since=time.time() - 0.3)
    if not b:
        speak("Не почув, що робити. Нічого не зберіг.")
        return
    _save_command(phrase, b[0])


def learning_voice_command(cmd: str) -> bool:
    """Голосові команди навчання. True, якщо команда була про навчання."""
    c = cmd.strip()
    if has(c, "нова команда", "навчись команди", "навчись нової команди", "вивчи команду", "додай команду"):
        learn_command_dialog()
        return True
    m = re.match(r"^(?:коли я (?:кажу|скажу|говорю))\s+(.+)$", c)
    if m:
        pa = _split_phrase_action(m.group(1))
        if not pa:
            speak("Не зрозумів, де фраза, а де дія. Скажіть: нова команда - і я запитаю по черзі.")
        else:
            _save_command(*pa)
        return True
    m = re.match(r"^запам['ʼ’]?ятай,?\s+(?!мій голос)(.+)$", c)
    if m:
        pa = _split_phrase_action(m.group(1))
        if pa:
            _save_command(*pa)
            return True
    m = re.match(r"^(?:забудь|видали) (?:команду\s+)?(.+)$", c)
    if m and _CUSTOM:
        item = find_custom([m.group(1)])
        if item:
            DB.delete("commands", item["id"])
            reload_learning()
            speak(f"Забув команду {item['phrase']}")
        else:
            speak("Не знайшов такої команди")
        return True
    if has(c, "мої команди", "які команди ти вивчив", "що ти вивчив", "список моїх команд"):
        if not _CUSTOM:
            speak("Ви ще не навчили мене жодної команди. Скажіть: нова команда.")
        else:
            names = ", ".join(i["phrase"] for i in _CUSTOM[:6])
            more = f" і ще {len(_CUSTOM) - 6}" if len(_CUSTOM) > 6 else ""
            k = len(_CUSTOM)
            speak(f"Я знаю {k} {plural(k, ('вашу команду', 'ваші команди', 'ваших команд'))}: {names}{more}")
        return True
    return False


# ---- API для вкладки НАВЧАННЯ ----

LEARN_TABLES = {"commands": "commands", "corrections": "corrections", "aliases": "aliases",
                "wake": "wake_words", "history": "history"}


def learning_list(section: str) -> list:
    if section == "commands":
        return DB.commands()
    if section == "corrections":
        return DB.corrections()
    if section == "aliases":
        return DB.aliases()
    if section == "wake":
        return DB.wake_words()
    if section == "history":
        return DB.history(300)
    return []


def learning_save(section: str, data: dict):
    """Додає/оновлює запис. Повертає (успіх, повідомлення)."""
    try:
        if section == "commands":
            DB.add_command(data.get("phrase", ""), data.get("kind", "command"), data.get("action", ""))
        elif section == "corrections":
            DB.add_correction(data.get("wrong", ""), data.get("right", ""))
        elif section == "aliases":
            DB.add_alias(data.get("spoken", ""), data.get("target", ""))
        elif section == "wake":
            if not DB.add_wake(data.get("word", ""), "вручну"):
                return False, "Слово надто коротке або вже є"
        else:
            return False, "Сюди додавати не можна"
    except Exception as e:
        return False, str(e)
    reload_learning()
    return True, "Збережено"


def learning_delete(section: str, row_id: int) -> None:
    DB.delete(LEARN_TABLES[section], row_id)
    reload_learning()


def learning_clear(section: str) -> None:
    DB.clear(LEARN_TABLES[section])
    reload_learning()


def learning_stats() -> dict:
    try:
        return DB.stats()
    except Exception:
        return {}


# --------------------------- API ДЛЯ ВІКНА ---------------------------

_SETTINGS = {
    "mic_index": "MIC_INDEX", "mic_sensitivity": "MIC_SENSITIVITY", "pause_seconds": "PAUSE_SECONDS",
    "followup_seconds": "FOLLOWUP_SECONDS", "require_wake_word": "REQUIRE_WAKE_WORD",
    "say_listening": "SAY_LISTENING", "always_chrome": "ALWAYS_USE_CHROME", "voice": "VOICE",
    "city": "DEFAULT_CITY", "gemini_model": "GEMINI_MODEL",
}


def get_settings() -> dict:
    g = globals()
    s = {k: g[v] for k, v in _SETTINGS.items()}
    s["gemini_key_set"] = bool(GEMINI_API_KEY)
    s["wake_learned"] = list(WAKE_LEARNED)
    return s


def apply_settings(**values) -> None:
    """Змінює налаштування на льоту і зберігає їх у config.json."""
    g = globals()
    restart_mic = "mic_index" in values and values["mic_index"] != MIC_INDEX
    for k, v in values.items():
        if k in _SETTINGS:
            g[_SETTINGS[k]] = v
    try:
        save_config(**{k: v for k, v in values.items() if k in _SETTINGS})
    except Exception as e:
        emit("error", f"Не вдалось зберегти налаштування: {e}")
    if restart_mic:
        emit("log", "Перемикаю мікрофон...")
        MIC.restart()


def status() -> dict:
    return {"ai": bool(GEMINI_API_KEY), "apps": len(APP_INDEX), "mic": MIC.device_name if MIC_OK else ""}


def app_names() -> list:
    return sorted(APP_INDEX, key=str.lower)


def launch_app_by_name(name: str) -> bool:
    entry = APP_INDEX.get(name)
    if not entry:
        return False
    try:
        launch(entry)
        emit("log", f"Запускаю: {name}")
        return True
    except Exception as e:
        emit("error", f"Не вдалось запустити {name}: {e}")
        return False


def rescan_apps() -> None:
    def work():
        build_app_index()
        emit("apps")
        emit("log", f"Знайдено програм та ігор: {len(APP_INDEX)}")
    threading.Thread(target=work, daemon=True).start()


# --------------------------- ГОЛОВНИЙ ЦИКЛ ---------------------------

def greeting() -> str:
    h = datetime.datetime.now().hour
    if 5 <= h < 12:
        return "Доброго ранку! Джарвіс на зв'язку"
    if 12 <= h < 18:
        return "Добрий день! Джарвіс на зв'язку"
    if 18 <= h < 23:
        return "Добрий вечір! Джарвіс на зв'язку"
    return "Доброї ночі! Джарвіс на зв'язку"


def run(stop=None) -> None:
    """Головний цикл асистента. stop - threading.Event, щоб зупинити ззовні (вікно закрили)."""
    stop = stop or threading.Event()
    threading.Thread(target=build_app_index, daemon=True, name="jarvis-apps").start()
    threading.Thread(target=_prewarm_tts, daemon=True, name="jarvis-tts").start()
    try:
        print("Мікрофони:", list_microphones())
    except Exception:
        pass
    MIC.start(stop)
    threading.Thread(target=_transcriber, args=(stop,), daemon=True, name="jarvis-stt").start()
    MIC.opened.wait(4)
    set_state(idle_state())
    emit("ready")
    speak(greeting(), mood="happy")

    state = {"active_until": 0.0}

    def step() -> bool:
        """Одна ітерація. Повертає False, коли треба вимкнутись."""
        try:
            typed = text_q.get_nowait().strip()
        except queue.Empty:
            typed = None

        if typed is not None:
            if not typed:
                return True
            if typed.startswith("/"):
                handle_slash(typed)
                return True
            emit("user", typed)
            alts = [typed.lower()]
            woke, cmds = extract_command(alts)
            if not woke:                       # набране вручну не потребує слова "Джарвіс"
                woke, cmds = True, alts
            phrase_ts = time.time()
        else:
            try:
                phrase_ts, alts, raw = heard_q.get(timeout=0.2)
            except queue.Empty:
                return True
            if not alts:
                return True
            woke, cmds = extract_command(alts)
            if not woke:
                if REQUIRE_WAKE_WORD and time.time() >= state["active_until"]:
                    MIC.stats["ignored"] += 1
                    _history(raw, alts, "без «Джарвіс»")
                    emit("heard", alts[0])     # почув, але це не до Джарвіса
                    return True
                cmds = alts
            _history(raw, alts, "команда")
            emit("user", alts[0])
            beep()

        if woke and cmds == [""]:              # сказав лише "Джарвіс"
            set_state("awaiting")
            if SAY_LISTENING and not mic_busy():   # не перебиваємо, якщо ви вже кажете команду
                speak("Слухаю")
                set_state("awaiting")
            more = hear(timeout=WAKE_WAIT_SECONDS, since=phrase_ts)
            if not more:
                set_state(idle_state())
                beep(500, 120)
                emit("log", "Команду не почув. Скажіть ще раз: «Джарвіс, ...»")
                return True
            w2, c2 = extract_command(more)
            cmds = c2 if (w2 and c2 != [""]) else more

        set_state("thinking")
        running = handle(cmds, quiet=(not woke))
        if woke:                     # вікно без "Джарвіс" відкривається лише після справжнього звертання
            state["active_until"] = time.time() + FOLLOWUP_SECONDS
        return running

    running = True
    try:
        while running and not stop.is_set():
            try:
                running = step()
            except Exception as e:        # одна помилка не вимикає асистента
                print(f"(помилка: {e})")
                emit("error", f"Помилка: {e}")
            if _state in ("thinking", "awaiting"):
                set_state(idle_state())
    finally:
        stop.set()
        cancel_timer()
        emit("quit")


reload_learning()          # завантажуємо навчене з бази одразу при старті


def main() -> None:
    run()


if __name__ == "__main__":
    main()