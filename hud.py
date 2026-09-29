"""
Вікно J.A.R.V.I.S. у стилі HUD із Залізної людини (tkinter, без зайвих залежностей).

Вкладки (Ctrl+1 ... Ctrl+7):
  01 ЯДРО          анімоване ядро (реагує на ваш голос), монітор системи, журнал, рядок команд
  02 МІКРОФОН      вибір пристрою, живий рівень сигналу, чутливість, пауза, діагностика
  03 КОМАНДИ       усі команди - клік виконує
  04 ПРОГРАМИ      знайдені програми й ігри: пошук і запуск
  05 ТАЙМЕРИ       швидкі таймери і список активних зі зворотним відліком
  06 НАЛАШТУВАННЯ  голос, слово "Джарвіс", Chrome, ключ Gemini, місто
  07 НАВЧАННЯ      база SQLite: мої команди, виправлення, назви програм, слово "Джарвіс", історія

Вікно нічого не знає про голос чи ШІ: воно отримує події через HUD.post(kind, text)
і звертається до ядра через об'єкт api (модуль jarvis_core).
"""

import datetime
import math
import os
import queue
import random
import time
import tkinter as tk
from collections import deque
from tkinter import font as tkfont
from tkinter import messagebox, ttk

try:
    import psutil
except ImportError:            # без psutil монітор просто покаже прочерки
    psutil = None

# ------------------------------- палітра -------------------------------
BG = "#02060d"
PANEL = "#040c17"
PANEL2 = "#06111f"
FIELD = "#030a13"
LINE = "#0d3760"
LINE2 = "#0a2a48"
HOVER = "#0a2236"
CYAN = "#3fd6ff"
BLUE = "#2a86ff"
AMBER = "#ffb14a"
WHITE = "#e3f4ff"
DIM = "#5e8fb8"
MUTED = "#3c5f80"
RED = "#ff5c66"
GREEN = "#36f0a4"

# кольори ядра для кожного стану: (краї, центр, кільце-1, кільце-2, підпис)
STATES = {
    "listening": ((0x08, 0x26, 0x66), (0x3f, 0x9d, 0xff), (0x3f, 0xd6, 0xff), (0x2a, 0x86, 0xff), "СЛУХАЮ"),
    "awaiting":  ((0x05, 0x36, 0x28), (0x36, 0xf0, 0xa4), (0x36, 0xf0, 0xa4), (0x22, 0xb8, 0x80), "ЧЕКАЮ КОМАНДУ"),
    "thinking":  ((0x4a, 0x2a, 0x06), (0xff, 0xb1, 0x4a), (0xff, 0xb1, 0x4a), (0xff, 0x8a, 0x2a), "ДУМАЮ"),
    "speaking":  ((0x06, 0x3a, 0x6e), (0x8a, 0xee, 0xff), (0x9f, 0xf0, 0xff), (0x3f, 0xd6, 0xff), "ГОВОРЮ"),
    "muted":     ((0x14, 0x1c, 0x28), (0x4a, 0x5a, 0x70), (0x3a, 0x4a, 0x60), (0x2a, 0x36, 0x48), "МІКРОФОН ВИМКНЕНО"),
    "nomic":     ((0x3a, 0x0c, 0x14), (0xc8, 0x40, 0x4c), (0xff, 0x5c, 0x66), (0xb0, 0x30, 0x3c), "НЕМАЄ МІКРОФОНА - ПИШІТЬ КОМАНДИ"),
}
SPIN = {"listening": 1.0, "awaiting": 1.6, "thinking": 3.4, "speaking": 1.9, "muted": 0.2, "nomic": 0.25}

TABS = [("main", "ЯДРО"), ("mic", "МІКРОФОН"), ("cmds", "КОМАНДИ"), ("apps", "ПРОГРАМИ"),
        ("timers", "ТАЙМЕРИ"), ("settings", "НАЛАШТУВАННЯ"), ("learn", "НАВЧАННЯ")]

COMMANDS = [
    ("ПРОГРАМИ Й САЙТИ", ["відкрий стім", "запусти діскорд", "закрий стім",
                          "відкрий ютуб у хромі", "оновити список програм"]),
    ("МУЗИКА І ЗВУК", ["увімкни Imagine Dragons Believer", "пауза", "наступний трек",
                       "гучніше", "тихіше", "без звуку"]),
    ("РЕЖИМИ", ["ігровий режим", "робочий режим", "режим спокою"]),
    ("ЧАС І ТАЙМЕРИ", ["котра година", "яке сьогодні число", "постав таймер на 5 хвилин",
                       "нагадай через 20 хвилин випити води", "скільки залишилось", "скасуй таймер"]),
    ("ІНФОРМАЦІЯ", ["яка погода", "яка погода в Одесі", "пошукай рецепт борщу", "розкажи жарт"]),
    ("СИСТЕМА", ["зроби скріншот", "покажи робочий стіл", "заблокуй комп'ютер",
                 "вимкни комп'ютер", "скасуй вимкнення"]),
    ("НАВЧАННЯ І ЗАБАВКИ", ["нова команда", "мої команди", "навчись", "підкинь монету",
                             "вибери за мене піцу чи суші"]),
    ("ШТУЧНИЙ ІНТЕЛЕКТ", ["що ти думаєш про космос?", "поясни, що таке чорна діра",
                          "порадь фільм на вечір", "придумай назву для кота"]),
]

MONTHS = ["січня", "лютого", "березня", "квітня", "травня", "червня", "липня",
          "серпня", "вересня", "жовтня", "листопада", "грудня"]
WEEKDAYS = ["понеділок", "вівторок", "середа", "четвер", "пʼятниця", "субота", "неділя"]


def hexc(rgb) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(v))) for v in rgb)


def mix(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


def pick_font(root, candidates, fallback):
    have = set(tkfont.families(root))
    for c in candidates:
        if c in have:
            return c
    return fallback


def human_rate(bps: float) -> str:
    if bps >= 1024 * 1024:
        return f"{bps / 1024 / 1024:.1f} МБ/с"
    return f"{bps / 1024:.0f} КБ/с"


def level_frac(v: float) -> float:
    """Гучність 1..10000 -> 0..1 у логарифмічній шкалі (як чує вухо)."""
    return max(0.0, min(1.0, math.log10(max(1.0, v)) / 4.0))


def mmss(sec: float) -> str:
    sec = int(round(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


class HUD(tk.Tk):
    def __init__(self, api, on_exit):
        super().__init__()
        self.api, self.on_exit = api, on_exit
        self.q: "queue.Queue" = queue.Queue()

        self.title("J.A.R.V.I.S.")
        self.configure(bg=BG)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = min(1380, sw - 60), min(840, sh - 80)
        self.geometry(f"{w}x{h}+{(sw - w) // 2}+{max(0, (sh - h) // 3)}")
        self.minsize(1100, 680)

        self.f_mono = pick_font(self, ["Consolas", "Cascadia Mono", "DejaVu Sans Mono", "Courier New"], "Courier")
        self.f_title = pick_font(self, ["Bahnschrift SemiBold", "Bahnschrift", "Segoe UI Semibold", "DejaVu Sans"], "Helvetica")

        # стан анімації
        self.jstate = "listening"
        self.muted = False
        self.t0 = self.last = time.time()
        self.spin = 0.0
        self.amp = 0.0
        self.voice = 0.0
        self.cur = [tuple(float(v) for v in STATES["listening"][i]) for i in range(4)]
        self.bars = [0.0] * 72
        self._bar_col = None
        self.user_line = ""
        self.jarvis_line = ""
        self.cpu_hist = [0.0] * 60
        self.net_prev = None
        self.net_down = self.net_up = 0.0
        self.net_peak = 200 * 1024.0
        self.fullscreen = False
        self.page = "main"
        self.mic = {}
        self.mic_hist = deque([(0.0, 0.0, False)] * 170, maxlen=170)
        self._debounce_ids = {}
        self._apps_cache = []
        self._slow_n = 0

        self._build_layout()
        self._bind_keys()
        self.protocol("WM_DELETE_WINDOW", self._exit)
        self.after(50, self._pump)
        self.after(40, self._frame)
        self.after(60, self._mic_tick)
        self.after(200, self._slow_tick)

    # ============================ загальні елементи ============================
    def _title(self, parent, text, bg=PANEL, color=CYAN):
        return tk.Label(parent, text=text, bg=bg, fg=color, font=(self.f_mono, 9, "bold"), anchor="w")

    def _hint(self, parent, text, bg=PANEL, wrap=300):
        return tk.Label(parent, text=text, bg=bg, fg=MUTED, font=(self.f_mono, 8), anchor="w",
                        justify="left", wraplength=wrap)

    def _button(self, parent, text, color, cmd, bg="#04101c", size=10, pady=8):
        b = tk.Label(parent, text=text, bg=bg, fg=color, font=(self.f_mono, size, "bold"),
                     highlightthickness=1, highlightbackground=color, cursor="hand2", pady=pady, padx=10)
        b.bind("<Button-1>", lambda e: cmd())
        b.bind("<Enter>", lambda e: b.configure(bg=HOVER))
        b.bind("<Leave>", lambda e: b.configure(bg=bg))
        return b

    def _entry(self, parent, show=None, width=None):
        e = tk.Entry(parent, bg=FIELD, fg=WHITE, insertbackground=CYAN, relief="flat", show=show or "",
                     font=(self.f_mono, 11), highlightthickness=1, highlightbackground=LINE2,
                     highlightcolor=CYAN, bd=0)
        if width:
            e.configure(width=width)
        return e

    def _scale(self, parent, lo, hi, res, value, on_change):
        s = tk.Scale(parent, from_=lo, to=hi, resolution=res, orient="horizontal", showvalue=True,
                     bg=PANEL, fg=WHITE, troughcolor="#0a1b2e", activebackground=CYAN,
                     highlightthickness=0, bd=0, sliderrelief="flat", sliderlength=18, width=10,
                     font=(self.f_mono, 9))
        s.set(value)
        s.configure(command=lambda v: on_change(float(v)))
        return s

    def _toggle(self, parent, text, value, on_change, bg=PANEL):
        """Перемикач-«тумблер», намальований на canvas."""
        row = tk.Frame(parent, bg=bg, cursor="hand2")
        cv = tk.Canvas(row, width=40, height=20, bg=bg, highlightthickness=0, cursor="hand2")
        cv.pack(side="left")
        lab = tk.Label(row, text=text, bg=bg, fg=WHITE, font=(self.f_mono, 10), anchor="w",
                       justify="left", wraplength=420, cursor="hand2")
        lab.pack(side="left", padx=(8, 0), fill="x")
        row.value = bool(value)

        def draw():
            cv.delete("all")
            on = row.value
            col = GREEN if on else "#1a3550"
            cv.create_oval(2, 3, 16, 17, fill=col, outline="")
            cv.create_oval(24, 3, 38, 17, fill=col, outline="")
            cv.create_rectangle(9, 3, 31, 17, fill=col, outline="")
            x = 30 if on else 10
            cv.create_oval(x - 6, 4, x + 6, 16, fill=WHITE if on else DIM, outline="")

        def click(_e=None):
            row.value = not row.value
            draw()
            on_change(row.value)

        def set_value(v):
            row.value = bool(v)
            draw()

        for wdg in (row, cv, lab):
            wdg.bind("<Button-1>", click)
        row.set_value = set_value
        draw()
        return row

    def _card(self, parent, title):
        f = tk.Frame(parent, bg=PANEL, highlightthickness=1, highlightbackground=LINE)
        f.columnconfigure(0, weight=1)
        self._title(f, title).grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 6))
        return f

    def _debounce(self, key, fn, ms=400):
        old = self._debounce_ids.pop(key, None)
        if old:
            self.after_cancel(old)
        self._debounce_ids[key] = self.after(ms, fn)

    def _apply(self, **kw):
        try:
            self.api.apply_settings(**kw)
        except Exception as e:
            self._log("!", f"Налаштування: {e}", "err")

    # ============================ каркас вікна ============================
    def _build_layout(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        self.top = tk.Canvas(self, height=54, bg=BG, highlightthickness=0)
        self.top.grid(row=0, column=0, sticky="ew")

        self._build_tabbar()

        self.pages_wrap = tk.Frame(self, bg=BG)
        self.pages_wrap.grid(row=2, column=0, sticky="nsew")
        self.pages_wrap.rowconfigure(0, weight=1)
        self.pages_wrap.columnconfigure(0, weight=1)
        self.pages = {}
        builders = {"main": self._build_main, "mic": self._build_mic, "cmds": self._build_cmds,
                    "apps": self._build_apps, "timers": self._build_timers, "settings": self._build_settings,
                    "learn": self._build_learn}
        for key, _ in TABS:
            p = tk.Frame(self.pages_wrap, bg=BG)
            p.grid(row=0, column=0, sticky="nsew")
            self.pages[key] = p
            builders[key](p)

        self.bottom = tk.Canvas(self, height=26, bg=BG, highlightthickness=0)
        self.bottom.grid(row=3, column=0, sticky="ew")
        self.show_page("main")

    def _build_tabbar(self):
        bar = tk.Frame(self, bg=BG)
        bar.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 6))
        self.tab_widgets = {}
        for n, (key, name) in enumerate(TABS, 1):
            cell = tk.Frame(bar, bg=BG, cursor="hand2")
            cell.pack(side="left", padx=(0, 4))
            lab = tk.Label(cell, text=f"{n:02d}  {name}", bg=BG, fg=DIM, font=(self.f_title, 10, "bold"),
                           padx=10, pady=6, cursor="hand2")
            lab.pack()
            under = tk.Frame(cell, bg=LINE, height=2)
            under.pack(fill="x")
            for w in (cell, lab):
                w.bind("<Button-1>", lambda e, k=key: self.show_page(k))
                w.bind("<Enter>", lambda e, k=key: self._tab_hover(k, True))
                w.bind("<Leave>", lambda e, k=key: self._tab_hover(k, False))
            self.tab_widgets[key] = (lab, under)
        # міні-індикатор мікрофона, видно на будь-якій вкладці
        self.tab_meter = tk.Canvas(bar, width=230, height=26, bg=BG, highlightthickness=0, cursor="hand2")
        self.tab_meter.pack(side="right")
        self.tab_meter.bind("<Button-1>", lambda e: self.show_page("mic"))

    def _tab_hover(self, key, on):
        if key == self.page:
            return
        lab, _ = self.tab_widgets[key]
        lab.configure(fg=WHITE if on else DIM, bg=HOVER if on else BG)

    def show_page(self, key):
        self.page = key
        self.pages[key].tkraise()
        for k, (lab, under) in self.tab_widgets.items():
            active = k == key
            lab.configure(fg=CYAN if active else DIM, bg=PANEL2 if active else BG)
            under.configure(bg=CYAN if active else LINE)
        if key == "apps":
            self._refresh_apps(force=True)
            self.app_search.focus_set()
        elif key == "timers":
            self._draw_timers()
        elif key == "mic":
            self._refresh_mic_list()
        elif key == "settings":
            self._load_settings()
        elif key == "learn":
            self._refresh_learn()

    def _bind_keys(self):
        self.bind("<F11>", lambda e: self._toggle_fullscreen())
        self.bind("<Escape>", lambda e: self._toggle_fullscreen(False))
        self.bind("<Control-m>", lambda e: self._toggle_mute())
        self.bind("<Control-l>", lambda e: (self.show_page("main"), self.entry.focus_set(), self._clear_placeholder()))
        for n, (key, _) in enumerate(TABS, 1):
            self.bind(f"<Control-Key-{n}>", lambda e, k=key: self.show_page(k))

    def _toggle_fullscreen(self, value=None):
        self.fullscreen = (not self.fullscreen) if value is None else value
        self.attributes("-fullscreen", self.fullscreen)

    # ============================ 01 ЯДРО ============================
    def _build_main(self, p):
        p.columnconfigure(1, weight=1)
        p.rowconfigure(0, weight=1)

        self.left = tk.Canvas(p, width=250, bg=PANEL, highlightthickness=1, highlightbackground=LINE)
        self.left.grid(row=0, column=0, sticky="ns", padx=(10, 6), pady=(0, 6))

        self.core = tk.Canvas(p, bg=BG, highlightthickness=0)
        self.core.grid(row=0, column=1, sticky="nsew", pady=(0, 6))
        self.core.bind("<Configure>", self._build_core)

        right = tk.Frame(p, width=370, bg=PANEL, highlightthickness=1, highlightbackground=LINE)
        right.grid(row=0, column=2, sticky="ns", padx=(6, 10), pady=(0, 6))
        right.grid_propagate(False)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)
        self._build_right(right)

    def _build_right(self, right):
        head = tk.Frame(right, bg=PANEL)
        head.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 4))
        head.columnconfigure(0, weight=1)
        self._title(head, "ЖУРНАЛ ДІЙ").grid(row=0, column=0, sticky="w")
        clr = tk.Label(head, text="очистити", bg=PANEL, fg=MUTED, font=(self.f_mono, 8), cursor="hand2")
        clr.grid(row=0, column=1)
        clr.bind("<Button-1>", lambda e: self._clear_log())

        wrap = tk.Frame(right, bg=PANEL)
        wrap.grid(row=1, column=0, sticky="nsew", padx=12)
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)
        self.log = tk.Text(wrap, bg=FIELD, fg=AMBER, insertbackground=CYAN, relief="flat", wrap="word",
                           font=(self.f_mono, 10), padx=8, pady=6, highlightthickness=1,
                           highlightbackground=LINE2, state="disabled", cursor="arrow", spacing3=3)
        self.log.grid(row=0, column=0, sticky="nsew")
        self.log.tag_configure("ts", foreground=MUTED)
        self.log.tag_configure("user", foreground=WHITE)
        self.log.tag_configure("jarvis", foreground=CYAN)
        self.log.tag_configure("sys", foreground=AMBER)
        self.log.tag_configure("err", foreground=RED)
        self.log.tag_configure("heard", foreground="#35506b")

        self._title(right, "КОМАНДА ЧИ ПИТАННЯ").grid(row=2, column=0, sticky="ew", padx=12, pady=(10, 4))
        box = tk.Frame(right, bg=FIELD, highlightthickness=1, highlightbackground=LINE2)
        box.grid(row=3, column=0, sticky="ew", padx=12)
        box.columnconfigure(0, weight=1)
        self.entry = tk.Entry(box, bg=FIELD, fg=WHITE, insertbackground=CYAN, relief="flat",
                              font=(self.f_mono, 11), highlightthickness=0, bd=0)
        self.entry.grid(row=0, column=0, sticky="ew", ipady=7, padx=(8, 0))
        send = tk.Label(box, text="  ➤  ", bg=FIELD, fg=CYAN, font=(self.f_mono, 12, "bold"), cursor="hand2")
        send.grid(row=0, column=1)
        send.bind("<Button-1>", lambda e: self._submit())
        self.placeholder = "Команда або питання"
        self._set_placeholder()
        self.entry.bind("<FocusIn>", self._clear_placeholder)
        self.entry.bind("<FocusOut>", lambda e: self._set_placeholder())
        self.entry.bind("<Return>", lambda e: self._submit())
        self.entry.bind("<Up>", lambda e: self._history(-1))
        self.entry.bind("<Down>", lambda e: self._history(1))
        self.cmd_history, self.hist_pos = [], 0

        self._hint(right, "Enter - виконати  ·  ↑↓ - історія  ·  /mics  /key  /city").grid(
            row=4, column=0, sticky="ew", padx=12, pady=(4, 8))

        self.btn_mic = self._button(right, "МІКРОФОН: УВІМКНЕНО", GREEN, self._toggle_mute)
        self.btn_mic.grid(row=5, column=0, sticky="ew", padx=12, pady=(0, 6))
        self._button(right, "ВИМКНУТИ ДЖАРВІСА", RED, self._exit).grid(
            row=6, column=0, sticky="ew", padx=12, pady=(0, 12))

    # ---- рядок команд ----
    def _set_placeholder(self):
        if not self.entry.get():
            self.entry.insert(0, self.placeholder)
            self.entry.configure(fg=MUTED)

    def _clear_placeholder(self, _e=None):
        if self.entry.get() == self.placeholder:
            self.entry.delete(0, "end")
        self.entry.configure(fg=WHITE)

    def _submit(self):
        text = self.entry.get().strip()
        if not text or text == self.placeholder:
            return
        self.entry.delete(0, "end")
        if not self.cmd_history or self.cmd_history[-1] != text:
            self.cmd_history.append(text)
        self.hist_pos = len(self.cmd_history)
        self.api.submit_text(text)

    def _history(self, step):
        if not self.cmd_history:
            return "break"
        self.hist_pos = max(0, min(len(self.cmd_history), self.hist_pos + step))
        self._clear_placeholder()
        self.entry.delete(0, "end")
        if self.hist_pos < len(self.cmd_history):
            self.entry.insert(0, self.cmd_history[self.hist_pos])
        return "break"

    def _run_command(self, text):
        self.api.submit_text(text)
        self.show_page("main")

    def _toggle_mute(self):
        self.api.set_muted(not self.muted)

    def _exit(self):
        self.on_exit()
        self.after(1500, self.destroy)      # якщо ядро не встигло завершитись - закриваємо примусово

    # ---- події від ядра ----
    def post(self, kind: str, text: str = "") -> None:
        """Безпечно з будь-якого потоку."""
        self.q.put((kind, text))

    def _pump(self):
        try:
            for _ in range(200):
                kind, text = self.q.get_nowait()
                self._handle_event(kind, text)
        except queue.Empty:
            pass
        self.after(50, self._pump)

    def _handle_event(self, kind, text):
        if kind == "state":
            self.jstate = text
        elif kind == "mute":
            self.muted = (text == "1")
            if self.muted:
                self.btn_mic.configure(text="МІКРОФОН: ВИМКНЕНО", fg=RED, highlightbackground=RED)
            else:
                self.btn_mic.configure(text="МІКРОФОН: УВІМКНЕНО", fg=GREEN, highlightbackground=GREEN)
        elif kind == "user":
            self.user_line = text
            self._log("Ви", text, "user")
        elif kind == "heard":
            self._log("·", f"(без «Джарвіс», пропускаю) {text}", "heard")
        elif kind == "jarvis":
            self.jarvis_line = text
            self._log("Джарвіс", text, "jarvis")
        elif kind == "log":
            self._log("·", text, "sys")
        elif kind == "error":
            self._log("!", text, "err")
        elif kind == "timers":
            if self.page == "timers":
                self._draw_timers()
        elif kind == "learning":
            if self.page == "learn":
                self._refresh_learn()
        elif kind == "settings":
            if self.page == "settings":
                self._load_settings()
        elif kind == "apps":
            self._refresh_apps(force=True)
        elif kind == "quit":
            self.after(300, self.destroy)

    def _log(self, who, text, tag):
        self.log.configure(state="normal")
        self.log.insert("end", datetime.datetime.now().strftime("%H:%M:%S "), "ts")
        self.log.insert("end", f"{who} › {text}\n" if who not in ("·", "!") else f"{text}\n", tag)
        lines = int(self.log.index("end-1c").split(".")[0])
        if lines > 400:
            self.log.delete("1.0", f"{lines - 400}.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    # ---- анімоване ядро ----
    def _build_core(self, _e=None):
        c = self.core
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if w < 50 or h < 50:
            return
        self.cx, self.cy = w / 2, h * 0.45
        R = self.R = min(w * 0.5, h * 0.40) * 0.96
        cx, cy = self.cx, self.cy
        self._bar_col = None

        def circ(r, **kw):
            return c.create_oval(cx - r, cy - r, cx + r, cy + r, **kw)

        def arc(r, **kw):
            return c.create_arc(cx - r, cy - r, cx + r, cy + r, style="arc", **kw)

        c.create_line(0, cy, w, cy, fill="#061524")
        c.create_line(cx, 0, cx, h, fill="#061524")
        for (x, y, dx, dy) in ((10, 10, 1, 1), (w - 10, 10, -1, 1), (10, h - 10, 1, -1), (w - 10, h - 10, -1, -1)):
            c.create_line(x, y, x + 26 * dx, y, fill=LINE, width=2)
            c.create_line(x, y, x, y + 26 * dy, fill=LINE, width=2)

        circ(R, outline="#0b2d50")
        self.ring_dots = circ(R * 0.94, outline=CYAN, width=2, dash=(2, 7))
        self.arcsA = [arc(R * 0.87, outline=CYAN, width=4, start=i * 120, extent=64) for i in range(3)]
        circ(R * 0.80, outline="#0e3a68")
        self.arcsB = [arc(R * 0.74, outline=BLUE, width=3, start=i * 180, extent=110) for i in range(2)]
        self.ring_ticks = circ(R * 0.665, outline="#1f6bb5", width=7, dash=(1, 4))
        self.spinner = arc(R * 0.585, outline=WHITE, width=3, start=0, extent=70, state="hidden")

        self.bar_lines = [c.create_line(cx, cy, cx, cy, fill=CYAN, width=2, capstyle="round")
                          for _ in range(len(self.bars))]

        self.orb_layers = [circ(R * 0.5, outline="", fill=BG) for _ in range(10)]
        self.orb_ring = circ(R * 0.185, outline="#9fdcff", width=1)
        self.orb_ring2 = circ(R * 0.20, outline="#2f7fc4", width=1, dash=(3, 5))
        fs = max(8, int(R * 0.052))
        self.orb_text = c.create_text(cx, cy, text="J.A.R.V.I.S", fill=WHITE, font=(self.f_title, fs, "bold"))

        self.status_dot = c.create_oval(0, 0, 0, 0, fill=GREEN, outline="")
        self.status_text = c.create_text(cx, cy + R + 26, text="", fill=CYAN, font=(self.f_title, 13, "bold"))
        self.caption_user = c.create_text(cx, cy + R + 54, text="", fill=DIM, font=(self.f_mono, 10),
                                          width=min(w - 60, 720), justify="center", anchor="n")
        self.caption_jarvis = c.create_text(cx, cy + R + 74, text="", fill=WHITE, font=(self.f_mono, 11),
                                            width=min(w - 60, 720), justify="center", anchor="n")

    def _voice_level(self) -> float:
        """0..1 - наскільки голосно ви зараз говорите відносно порогу мікрофона."""
        m = self.mic
        thr, lvl, noise = m.get("threshold") or 0, m.get("level") or 0, m.get("noise") or 0
        if thr <= 0 or m.get("speaking") or m.get("muted"):
            return 0.0
        return max(0.0, min(1.0, (lvl - noise) / max(1.0, thr * 2.5 - noise)))

    def _frame(self):
        now = time.time()
        dt = min(0.1, now - self.last)
        self.last = now
        # Не малюємо, коли ядро не видно: інша вкладка або вікно згорнуте - економія процесора
        if self.page != "main" or self.wm_state() == "iconic" or not hasattr(self, "orb_layers"):
            self.after(150, self._frame)
            return
        t = now - self.t0
        c, R, cx, cy = self.core, self.R, self.cx, self.cy
        st = self.jstate if self.jstate in STATES else "listening"
        edge, mid, r1, r2, label = STATES[st]
        hearing = st in ("listening", "awaiting") and self.mic.get("in_speech")
        if hearing:
            label = "ЧУЮ ВАС..."

        for i, target in enumerate((edge, mid, r1, r2)):
            self.cur[i] = mix(self.cur[i], target, min(1.0, dt * 6))
        edge_c, mid_c, r1_c, r2_c = self.cur

        self.voice += (self._voice_level() - self.voice) * min(1.0, dt * 12)
        v = self.voice if st in ("listening", "awaiting") else 0.0
        self.spin += dt * (SPIN.get(st, 1.0) + v * 2.5)
        s = self.spin

        if st == "speaking":
            target_amp = 0.55 + 0.45 * abs(math.sin(t * 9.0) * math.sin(t * 3.7 + 1.0))
        elif st == "thinking":
            target_amp = 0.35 + 0.15 * math.sin(t * 6)
        elif st in ("muted", "nomic"):
            target_amp = 0.0
        else:
            target_amp = 0.25 + 0.2 * math.sin(t * 1.8) + v * 1.2
        self.amp += (target_amp - self.amp) * min(1.0, dt * 8)
        pulse = 1.0 + 0.055 * self.amp

        col1, col2 = hexc(r1_c), hexc(r2_c)
        c.itemconfigure(self.ring_dots, outline=col1, dashoffset=int(s * 14) % 9)
        for i, a in enumerate(self.arcsA):
            c.itemconfigure(a, start=(-s * 38 + i * 120) % 360, outline=col1)
        for i, a in enumerate(self.arcsB):
            c.itemconfigure(a, start=(s * 55 + i * 180) % 360, outline=col2)
        c.itemconfigure(self.ring_ticks, dashoffset=int(-s * 10) % 5, outline=hexc(mix(r2_c, (2, 6, 13), 0.45)))
        if st == "thinking":
            c.itemconfigure(self.spinner, state="normal", start=(-s * 210) % 360, outline=col1)
        else:
            c.itemconfigure(self.spinner, state="hidden")

        # спектр: коли говорить Джарвіс - "голос", коли слухаю - ВАШ реальний рівень мікрофона
        n = len(self.bars)
        r_in = R * 0.53 * pulse
        recolor = col1 != self._bar_col
        self._bar_col = col1
        for i in range(n):
            if st == "speaking":
                goal = (0.15 + 0.85 * abs(math.sin(t * 7.3 + i * 0.63) * math.sin(t * 3.1 + i * 0.29))) * (0.55 + 0.45 * random.random())
            elif st == "thinking":
                goal = 0.10 + 0.10 * (math.sin(t * 5 + i * 0.5) + 1) / 2
            elif v > 0.02:
                goal = 0.05 + v * (0.35 + 0.65 * abs(math.sin(t * 8.1 + i * 0.71))) * (0.6 + 0.4 * random.random())
            else:
                goal = 0.05
            self.bars[i] += (goal - self.bars[i]) * min(1.0, dt * 14)
            ang = 2 * math.pi * i / n - math.pi / 2
            ca, sa = math.cos(ang), math.sin(ang)
            r_out = r_in + 4 + self.bars[i] * R * 0.11
            c.coords(self.bar_lines[i], cx + ca * r_in, cy + sa * r_in, cx + ca * r_out, cy + sa * r_out)
            if recolor:
                c.itemconfigure(self.bar_lines[i], fill=col1)

        k = len(self.orb_layers)
        for j, item in enumerate(self.orb_layers):
            frac = j / (k - 1)
            r = R * 0.50 * pulse * (1 - frac * 0.62)
            c.coords(item, cx - r, cy - r, cx + r, cy + r)
            c.itemconfigure(item, fill=hexc(mix(edge_c, mid_c, frac ** 1.4)))
        bright = sum(mid_c) / 3
        c.itemconfigure(self.orb_text, fill="#03203a" if bright > 190 else WHITE)
        rr = R * 0.185 * pulse
        c.coords(self.orb_ring, cx - rr, cy - rr, cx + rr, cy + rr)
        c.itemconfigure(self.orb_ring2, dashoffset=int(s * 8) % 8)

        c.itemconfigure(self.status_text, text=label, fill=col1)
        bb = c.bbox(self.status_text)
        dx = (bb[0] - 14) if bb else cx - 60
        c.coords(self.status_dot, dx - 6, cy + R + 20, dx + 6, cy + R + 32)
        blink = st in ("thinking", "speaking") or hearing or int(t * 2) % 2 == 0
        c.itemconfigure(self.status_dot, fill=col1 if blink else BG)
        c.itemconfigure(self.caption_user, text=("Ви: " + self.user_line[:140]) if self.user_line else "")
        c.itemconfigure(self.caption_jarvis, text=self.jarvis_line[:220])
        bbox = c.bbox(self.caption_user)
        y2 = (bbox[3] + 8) if bbox and self.user_line else cy + R + 54
        c.coords(self.caption_jarvis, cx, y2)

        self.after(40, self._frame)

    # ============================ 02 МІКРОФОН ============================
    def _build_mic(self, p):
        p.columnconfigure(1, weight=1)
        p.rowconfigure(0, weight=1)

        left = tk.Frame(p, bg=PANEL, width=380, highlightthickness=1, highlightbackground=LINE)
        left.grid(row=0, column=0, sticky="ns", padx=(10, 6), pady=(0, 6))
        left.grid_propagate(False)
        left.columnconfigure(0, weight=1)

        self._title(left, "ПРИСТРІЙ ВВОДУ").grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 4))
        self.mic_list = tk.Listbox(left, bg=FIELD, fg=WHITE, selectbackground=LINE2, selectforeground=CYAN,
                                   font=(self.f_mono, 10), relief="flat", highlightthickness=1,
                                   highlightbackground=LINE2, activestyle="none", height=7, exportselection=False)
        self.mic_list.grid(row=1, column=0, sticky="ew", padx=12)
        self.mic_list.bind("<Double-Button-1>", lambda e: self._apply_mic())
        self._mic_ids = []
        btns = tk.Frame(left, bg=PANEL)
        btns.grid(row=2, column=0, sticky="ew", padx=12, pady=(6, 0))
        btns.columnconfigure((0, 1), weight=1)
        self._button(btns, "ЗАСТОСУВАТИ", GREEN, self._apply_mic, size=9, pady=5).grid(row=0, column=0, sticky="ew", padx=(0, 3))
        self._button(btns, "ОНОВИТИ СПИСОК", CYAN, self._refresh_mic_list, size=9, pady=5).grid(row=0, column=1, sticky="ew", padx=(3, 0))

        s = self._safe_settings()
        self._title(left, "ЧУТЛИВІСТЬ (1 - 10)").grid(row=3, column=0, sticky="ew", padx=12, pady=(16, 0))
        self.sc_sens = self._scale(left, 1, 10, 1, s.get("mic_sensitivity", 6),
                                   lambda v: self._debounce("sens", lambda: self._apply(mic_sensitivity=int(v))))
        self.sc_sens.grid(row=4, column=0, sticky="ew", padx=12)
        self._hint(left, "Більше - чує тихий голос, але частіше реагує на шум. "
                         "Жовта лінія праворуч - поріг.", wrap=340).grid(row=5, column=0, sticky="ew", padx=12)

        self._title(left, "ПАУЗА В КІНЦІ ФРАЗИ (секунд)").grid(row=6, column=0, sticky="ew", padx=12, pady=(14, 0))
        self.sc_pause = self._scale(left, 0.5, 1.6, 0.1, s.get("pause_seconds", 0.8),
                                    lambda v: self._debounce("pause", lambda: self._apply(pause_seconds=round(v, 1))))
        self.sc_pause.grid(row=7, column=0, sticky="ew", padx=12)
        self._hint(left, "Більше - можна робити паузи посеред команди, але відповідь трохи пізніше.",
                   wrap=340).grid(row=8, column=0, sticky="ew", padx=12)

        self._button(left, "ПЕРЕКАЛІБРУВАТИ ШУМ", AMBER, self._recalibrate, size=9, pady=6).grid(
            row=9, column=0, sticky="ew", padx=12, pady=(16, 4))
        self._hint(left, "Натисніть і помовчіть секунду - Джарвіс заново виміряє фоновий шум. "
                         "Фон він відстежує й сам, але після зміни обстановки це пришвидшить.",
                   wrap=340).grid(row=10, column=0, sticky="ew", padx=12)

        self.mic_canvas = tk.Canvas(p, bg=PANEL, highlightthickness=1, highlightbackground=LINE)
        self.mic_canvas.grid(row=0, column=1, sticky="nsew", padx=(6, 10), pady=(0, 6))

    def _safe_settings(self) -> dict:
        try:
            return self.api.get_settings()
        except Exception:
            return {}

    def _refresh_mic_list(self):
        try:
            mics = self.api.list_microphones()
        except Exception:
            mics = []
        cur = self._safe_settings().get("mic_index")
        self.mic_list.delete(0, "end")
        self._mic_ids = [None] + [i for i, _, _ in mics]
        self.mic_list.insert("end", "  Як у Windows (за замовчуванням)")
        for i, name, default in mics:
            self.mic_list.insert("end", f"  {name}" + ("   ★" if default else ""))
        sel = self._mic_ids.index(cur) if cur in self._mic_ids else 0
        self.mic_list.selection_clear(0, "end")
        self.mic_list.selection_set(sel)
        self.mic_list.see(sel)

    def _apply_mic(self):
        sel = self.mic_list.curselection()
        if not sel:
            return
        self._apply(mic_index=self._mic_ids[sel[0]])

    def _recalibrate(self):
        try:
            self.api.recalibrate_mic()
        except Exception:
            pass

    def _mic_tick(self):
        """~16 разів на секунду: беремо рівень мікрофона з ядра (дешево - просто читання змінних)."""
        try:
            self.mic = self.api.mic_stats() or {}
        except Exception:
            self.mic = {}
        m = self.mic
        self.mic_hist.append((m.get("level", 0.0), m.get("threshold", 0.0), bool(m.get("in_speech"))))
        if self.wm_state() != "iconic":
            self._draw_tab_meter()
            if self.page == "mic":
                self._draw_mic()
        self.after(60, self._mic_tick)

    def _mic_status(self):
        m = self.mic
        if not m.get("ok"):
            return "НЕМАЄ МІКРОФОНА", RED
        if m.get("muted"):
            return "ВИМКНЕНО КНОПКОЮ", MUTED
        if m.get("calibrating"):
            return "ВИМІРЮЮ ТИШУ...", AMBER
        if m.get("speaking"):
            return "ГОВОРИТЬ ДЖАРВІС", BLUE
        if m.get("in_speech"):
            return "ЧУЮ МОВУ", GREEN
        return "ТИША", DIM

    def _draw_tab_meter(self):
        c = self.tab_meter
        c.delete("all")
        m = self.mic
        text, col = self._mic_status()
        c.create_text(0, 13, text="MIC", anchor="w", fill=MUTED, font=(self.f_mono, 8, "bold"))
        n, x0, seg = 16, 30, 6
        on = int(level_frac(m.get("level", 0)) * n)
        thr = int(level_frac(m.get("threshold", 0)) * n)
        for i in range(n):
            x = x0 + i * (seg + 2)
            fill = (GREEN if i >= thr else CYAN) if i < on else "#0a1b2e"
            c.create_rectangle(x, 7, x + seg, 19, fill=fill, outline="")
            if i == thr:
                c.create_line(x - 1, 4, x - 1, 22, fill=AMBER)
        c.create_text(230, 13, text=text, anchor="e", fill=col, font=(self.f_mono, 8, "bold"))

    def _draw_mic(self):
        c = self.mic_canvas
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if w < 100:
            return
        m = self.mic
        pad = 22
        c.create_text(pad, 20, text="ЖИВИЙ СИГНАЛ", anchor="w", fill=CYAN, font=(self.f_mono, 9, "bold"))
        dev = m.get("device") or "—"
        rate = f"  ·  {m.get('rate')} Гц" if m.get("rate") else ""
        c.create_text(w - pad, 20, text=dev[:60] + rate, anchor="e", fill=DIM, font=(self.f_mono, 9))
        c.create_line(pad, 34, w - pad, 34, fill=LINE)

        text, col = self._mic_status()
        c.create_text(pad, 62, text=text, anchor="w", fill=col, font=(self.f_title, 20, "bold"))

        # великий індикатор
        y, bw = 92, w - 2 * pad
        n = 60
        sw = (bw - 2 * (n - 1)) / n
        lf, tf, nf = level_frac(m.get("level", 0)), level_frac(m.get("threshold", 0)), level_frac(m.get("noise", 0))
        for i in range(n):
            x = pad + i * (sw + 2)
            fr = (i + 0.5) / n
            if fr <= lf:
                fill = GREEN if fr >= tf else (CYAN if fr >= nf else "#1d5d8a")
            else:
                fill = "#0a1b2e"
            c.create_rectangle(x, y, x + sw, y + 26, fill=fill, outline="")
        tx, nx = pad + bw * tf, pad + bw * nf
        c.create_line(tx, y - 6, tx, y + 32, fill=AMBER, width=2)
        c.create_text(tx, y + 44, text=f"поріг {m.get('threshold', 0):.0f}", fill=AMBER, font=(self.f_mono, 8))
        c.create_line(nx, y - 2, nx, y + 28, fill=MUTED, width=1, dash=(2, 2))
        c.create_text(nx, y - 12, text=f"фон {m.get('noise', 0):.0f}", fill=MUTED, font=(self.f_mono, 8))
        c.create_text(w - pad, y + 44, text=f"зараз {m.get('level', 0):.0f}", anchor="e", fill=WHITE,
                      font=(self.f_mono, 9, "bold"))

        # графік за останні ~10 секунд
        gy, gh = y + 70, max(80, min(200, h - y - 290))
        c.create_text(pad, gy - 10, text="ОСТАННІ 10 СЕКУНД  (зелене тло - записана фраза)", anchor="w",
                      fill=DIM, font=(self.f_mono, 8))
        c.create_rectangle(pad, gy, pad + bw, gy + gh, outline=LINE2)
        hist = list(self.mic_hist)
        k = len(hist)
        step = bw / (k - 1)
        run_start = None
        for i, (_, _, sp) in enumerate(hist + [(0, 0, False)]):
            if sp and run_start is None:
                run_start = i
            elif not sp and run_start is not None:
                c.create_rectangle(pad + run_start * step, gy + 1, pad + (i - 1) * step, gy + gh - 1,
                                   fill="#08261c", outline="")
                run_start = None
        lv, th = [], []
        for i, (lvl, thr, _) in enumerate(hist):
            x = pad + i * step
            lv += [x, gy + gh - gh * level_frac(lvl)]
            th += [x, gy + gh - gh * level_frac(thr)]
        c.create_line(*th, fill=AMBER, width=1, dash=(4, 3))
        c.create_line(*lv, fill=CYAN, width=2)

        # лічильники
        sy = gy + gh + 30
        stats = [("ФРАЗ ЗАПИСАНО", m.get("phrases", 0), WHITE), ("РОЗПІЗНАНО", m.get("recognized", 0), GREEN),
                 ("НЕ РОЗІБРАНО (ШУМ)", m.get("empty", 0), AMBER), ("БЕЗ «ДЖАРВІС»", m.get("ignored", 0), DIM)]
        cw = bw / len(stats)
        for i, (name, val, colr) in enumerate(stats):
            x = pad + i * cw
            c.create_text(x, sy, text=str(val), anchor="w", fill=colr, font=(self.f_title, 18, "bold"))
            c.create_text(x, sy + 22, text=name, anchor="w", fill=MUTED, font=(self.f_mono, 8))
        last = m.get("last_heard") or "—"
        c.create_text(pad, sy + 50, text=f"Останнє почуте:  «{last[:90]}»", anchor="w", fill=WHITE,
                      font=(self.f_mono, 10))

        tips = ("ЯК НАЛАШТУВАТИ\n"
                "•  Коли говорите - смужка має заходити ЗА жовту лінію. Не заходить → збільште чутливість "
                "або гучність мікрофона в налаштуваннях звуку Windows.\n"
                "•  У тиші смужка має бути далеко ліворуч від жовтої лінії. Тиша її перетинає → зменште чутливість.\n"
                "•  Джарвіс обрізає кінець команди → збільште паузу.  Багато «не розібрано» → говоріть ближче до мікрофона.")
        c.create_text(pad, sy + 78, text=tips, anchor="nw", fill=DIM, font=(self.f_mono, 9), width=bw)

    # ============================ 03 КОМАНДИ ============================
    def _build_cmds(self, p):
        p.columnconfigure(0, weight=1)
        head = tk.Frame(p, bg=BG)
        head.grid(row=0, column=0, sticky="ew", padx=12, pady=(0, 6))
        tk.Label(head, text="Натисніть на команду, щоб виконати її. Голосом - те саме, але спочатку «Джарвіс, ...»",
                 bg=BG, fg=DIM, font=(self.f_mono, 9)).pack(side="left")
        grid = tk.Frame(p, bg=BG)
        grid.grid(row=1, column=0, sticky="nsew", padx=6)
        p.rowconfigure(1, weight=1)
        cols = 4
        for i in range(cols):
            grid.columnconfigure(i, weight=1, uniform="c")
        for i in range(2):
            grid.rowconfigure(i, weight=1)
        for n, (title, cmds) in enumerate(COMMANDS):
            card = self._card(grid, title)
            card.grid(row=n // cols, column=n % cols, sticky="nsew", padx=4, pady=4)
            for j, text in enumerate(cmds, 1):
                lab = tk.Label(card, text="›  " + text, bg=PANEL, fg=WHITE, font=(self.f_mono, 10),
                               anchor="w", cursor="hand2", padx=12, pady=3, wraplength=260, justify="left")
                lab.grid(row=j, column=0, sticky="ew")
                lab.bind("<Button-1>", lambda e, t=text: self._run_command(t))
                lab.bind("<Enter>", lambda e, l=lab: l.configure(bg=HOVER, fg=CYAN))
                lab.bind("<Leave>", lambda e, l=lab: l.configure(bg=PANEL, fg=WHITE))
            tk.Frame(card, bg=PANEL, height=8).grid(row=len(cmds) + 1, column=0)

    # ============================ 04 ПРОГРАМИ ============================
    def _build_apps(self, p):
        p.columnconfigure(0, weight=1)
        p.rowconfigure(0, weight=1)
        box = tk.Frame(p, bg=PANEL, highlightthickness=1, highlightbackground=LINE)
        box.grid(row=0, column=0, sticky="nsew", padx=(10, 6), pady=(0, 6))
        box.columnconfigure(0, weight=1)
        box.rowconfigure(2, weight=1)
        self._title(box, "ПОШУК").grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 4))
        self.app_search = self._entry(box)
        self.app_search.grid(row=1, column=0, sticky="ew", padx=12, ipady=6)
        self.app_search.bind("<KeyRelease>", lambda e: self._filter_apps())
        self.app_search.bind("<Return>", lambda e: self._launch_selected())
        self.app_search.bind("<Down>", lambda e: (self.app_list.focus_set(), self.app_list.selection_set(0)))
        wrap = tk.Frame(box, bg=PANEL)
        wrap.grid(row=2, column=0, sticky="nsew", padx=12, pady=10)
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)
        self.app_list = tk.Listbox(wrap, bg=FIELD, fg=WHITE, selectbackground=LINE2, selectforeground=CYAN,
                                   font=(self.f_mono, 11), relief="flat", highlightthickness=1,
                                   highlightbackground=LINE2, activestyle="none")
        self.app_list.grid(row=0, column=0, sticky="nsew")
        sb = tk.Scrollbar(wrap, command=self.app_list.yview, bg=PANEL, troughcolor=FIELD, relief="flat", width=12)
        sb.grid(row=0, column=1, sticky="ns")
        self.app_list.configure(yscrollcommand=sb.set)
        self.app_list.bind("<Double-Button-1>", lambda e: self._launch_selected())
        self.app_list.bind("<Return>", lambda e: self._launch_selected())

        side = tk.Frame(p, bg=PANEL, width=340, highlightthickness=1, highlightbackground=LINE)
        side.grid(row=0, column=1, sticky="ns", padx=(6, 10), pady=(0, 6))
        side.grid_propagate(False)
        side.columnconfigure(0, weight=1)
        self._title(side, "БАЗА ПРОГРАМ").grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 4))
        self.apps_count = tk.Label(side, text="—", bg=PANEL, fg=WHITE, font=(self.f_title, 26, "bold"), anchor="w")
        self.apps_count.grid(row=1, column=0, sticky="ew", padx=12)
        self._hint(side, "програм і ігор знайдено (меню Пуск + Steam)").grid(row=2, column=0, sticky="ew", padx=12)
        self._button(side, "ЗАПУСТИТИ ВИБРАНЕ", GREEN, self._launch_selected).grid(
            row=3, column=0, sticky="ew", padx=12, pady=(18, 6))
        self._button(side, "ОНОВИТИ БАЗУ", CYAN, lambda: self.api.rescan_apps()).grid(
            row=4, column=0, sticky="ew", padx=12)
        self._hint(side, "Подвійний клік або Enter - запуск.\n\nГолосом: «Джарвіс, запусти <назва>». "
                         "Джарвіс розуміє і українське звучання: «стім», «діскорд», «пайчарм».\n\n"
                         "Нову програму встановили? Натисніть «Оновити базу» або скажіть "
                         "«Джарвіс, оновити список програм».", wrap=310).grid(row=5, column=0, sticky="ew", padx=12, pady=16)

    def _refresh_apps(self, force=False):
        try:
            names = self.api.app_names()
        except Exception:
            names = []
        if force or len(names) != len(self._apps_cache):
            self._apps_cache = names
            self.apps_count.configure(text=str(len(names)) if names else "сканую...")
            self._filter_apps()

    def _filter_apps(self):
        q = self.app_search.get().strip().lower()
        self.app_list.delete(0, "end")
        for n in self._apps_cache:
            if not q or q in n.lower():
                self.app_list.insert("end", "  " + n)

    def _launch_selected(self):
        sel = self.app_list.curselection()
        if not sel and self.app_list.size():
            sel = (0,)
        if sel:
            self.api.launch_app_by_name(self.app_list.get(sel[0]).strip())

    # ============================ 05 ТАЙМЕРИ ============================
    def _build_timers(self, p):
        p.columnconfigure(1, weight=1)
        p.rowconfigure(0, weight=1)
        left = tk.Frame(p, bg=PANEL, width=360, highlightthickness=1, highlightbackground=LINE)
        left.grid(row=0, column=0, sticky="ns", padx=(10, 6), pady=(0, 6))
        left.grid_propagate(False)
        left.columnconfigure((0, 1), weight=1)
        self._title(left, "ШВИДКИЙ ТАЙМЕР").grid(row=0, column=0, columnspan=2, sticky="ew", padx=12, pady=(10, 6))
        for n, mins in enumerate((1, 3, 5, 10, 15, 25, 30, 60)):
            b = self._button(left, f"{mins} ХВ", CYAN, lambda m=mins: self._quick_timer(m * 60), size=10, pady=6)
            b.grid(row=1 + n // 2, column=n % 2, sticky="ew", padx=(12 if n % 2 == 0 else 3, 3 if n % 2 == 0 else 12), pady=3)

        self._title(left, "СВІЙ ТАЙМЕР").grid(row=6, column=0, columnspan=2, sticky="ew", padx=12, pady=(18, 4))
        self._hint(left, "Хвилин:").grid(row=7, column=0, sticky="w", padx=12)
        self.t_min = self._entry(left, width=6)
        self.t_min.grid(row=8, column=0, sticky="ew", padx=(12, 3), ipady=4)
        self._hint(left, "Нагадування (необовʼязково):").grid(row=9, column=0, columnspan=2, sticky="w", padx=12, pady=(8, 0))
        self.t_label = self._entry(left)
        self.t_label.grid(row=10, column=0, columnspan=2, sticky="ew", padx=12, ipady=4)
        self.t_label.bind("<Return>", lambda e: self._custom_timer())
        self.t_min.bind("<Return>", lambda e: self._custom_timer())
        self._button(left, "ЗАПУСТИТИ", GREEN, self._custom_timer).grid(
            row=11, column=0, columnspan=2, sticky="ew", padx=12, pady=(10, 4))
        self._button(left, "СКАСУВАТИ ВСІ", RED, lambda: self.api.cancel_timer(None), size=9, pady=5).grid(
            row=12, column=0, columnspan=2, sticky="ew", padx=12, pady=(14, 4))
        self._hint(left, "Голосом: «Джарвіс, постав таймер на 10 хвилин», «нагадай через 20 хвилин ...», "
                         "«скільки залишилось», «скасуй таймер».", wrap=330).grid(
            row=13, column=0, columnspan=2, sticky="ew", padx=12, pady=8)

        self.timer_canvas = tk.Canvas(p, bg=PANEL, highlightthickness=1, highlightbackground=LINE)
        self.timer_canvas.grid(row=0, column=1, sticky="nsew", padx=(6, 10), pady=(0, 6))

    def _quick_timer(self, seconds, label=""):
        self.api.start_timer(seconds, label)
        self._log("·", f"Таймер запущено: {mmss(seconds)}" + (f" - {label}" if label else ""), "sys")

    def _custom_timer(self):
        try:
            mins = float(self.t_min.get().replace(",", ".").strip())
        except ValueError:
            self.t_min.configure(highlightbackground=RED)
            return
        if not 0 < mins <= 720:
            self.t_min.configure(highlightbackground=RED)
            return
        self.t_min.configure(highlightbackground=LINE2)
        self._quick_timer(mins * 60, self.t_label.get().strip())
        self.t_min.delete(0, "end")
        self.t_label.delete(0, "end")

    def _draw_timers(self):
        c = self.timer_canvas
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        pad = 22
        c.create_text(pad, 20, text="АКТИВНІ ТАЙМЕРИ", anchor="w", fill=CYAN, font=(self.f_mono, 9, "bold"))
        c.create_line(pad, 34, w - pad, 34, fill=LINE)
        try:
            items = self.api.list_timers()
        except Exception:
            items = []
        if not items:
            c.create_text(w / 2, h / 2 - 10, text="Немає активних таймерів", fill=MUTED, font=(self.f_title, 16, "bold"))
            c.create_text(w / 2, h / 2 + 18, text="Оберіть час ліворуч або скажіть «Джарвіс, постав таймер на 5 хвилин»",
                          fill=MUTED, font=(self.f_mono, 9))
            return
        y = 56
        for t in items[:8]:
            frac = 1 - t["left"] / max(1.0, t["total"])
            col = AMBER if t["left"] < 60 else CYAN
            c.create_rectangle(pad, y, w - pad, y + 70, outline=LINE2, fill=FIELD)
            c.create_text(pad + 16, y + 26, text=mmss(t["left"]), anchor="w", fill=col, font=(self.f_title, 24, "bold"))
            c.create_text(pad + 150, y + 20, text=t["label"] or "Таймер", anchor="w", fill=WHITE, font=(self.f_mono, 11, "bold"))
            c.create_text(pad + 150, y + 38, text=f"з {mmss(t['total'])}", anchor="w", fill=MUTED, font=(self.f_mono, 9))
            bx0, bx1 = pad + 16, w - pad - 60
            c.create_rectangle(bx0, y + 54, bx1, y + 60, fill="#0a1b2e", outline="")
            c.create_rectangle(bx0, y + 54, bx0 + (bx1 - bx0) * frac, y + 60, fill=col, outline="")
            tag = f"cancel{t['id']}"
            c.create_text(w - pad - 24, y + 30, text="✕", fill=RED, font=(self.f_title, 16, "bold"), tags=tag)
            c.tag_bind(tag, "<Button-1>", lambda e, i=t["id"]: self.api.cancel_timer(i))
            c.tag_bind(tag, "<Enter>", lambda e: c.configure(cursor="hand2"))
            c.tag_bind(tag, "<Leave>", lambda e: c.configure(cursor=""))
            y += 82

    # ============================ 06 НАЛАШТУВАННЯ ============================
    def _build_settings(self, p):
        p.columnconfigure((0, 1), weight=1, uniform="s")
        p.rowconfigure(0, weight=1)
        colL = tk.Frame(p, bg=BG)
        colL.grid(row=0, column=0, sticky="nsew", padx=(10, 5))
        colR = tk.Frame(p, bg=BG)
        colR.grid(row=0, column=1, sticky="nsew", padx=(5, 10))
        colL.columnconfigure(0, weight=1)
        colR.columnconfigure(0, weight=1)
        s = self._safe_settings()

        # --- голос ---
        card = self._card(colL, "ГОЛОС ДЖАРВІСА")
        card.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        vrow = tk.Frame(card, bg=PANEL)
        vrow.grid(row=1, column=0, sticky="ew", padx=12)
        self.voice_btns = {}
        voices = getattr(self.api, "VOICES", {"uk-UA-OstapNeural": "Остап", "uk-UA-PolinaNeural": "Поліна"})
        for vid, name in voices.items():
            b = tk.Label(vrow, text=name, bg="#04101c", fg=DIM, font=(self.f_mono, 10, "bold"), padx=12, pady=6,
                         highlightthickness=1, highlightbackground=LINE2, cursor="hand2")
            b.pack(side="left", padx=(0, 8))
            b.bind("<Button-1>", lambda e, v=vid: self._set_voice(v))
            self.voice_btns[vid] = b
        self.tg_say = self._toggle(card, "Казати «Слухаю», коли звертаюсь лише «Джарвіс» (інакше - короткий сигнал, "
                                         "так швидше)", s.get("say_listening"), lambda v: self._apply(say_listening=v))
        self.tg_say.grid(row=2, column=0, sticky="ew", padx=12, pady=(12, 12))

        # --- розпізнавання ---
        card = self._card(colL, "РОЗПІЗНАВАННЯ")
        card.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        self.tg_wake = self._toggle(card, "Реагувати лише на звертання «Джарвіс, ...»", s.get("require_wake_word", True),
                                    lambda v: self._apply(require_wake_word=v))
        self.tg_wake.grid(row=1, column=0, sticky="ew", padx=12)
        self._title(card, "ПІСЛЯ КОМАНДИ МОЖНА БЕЗ «ДЖАРВІС» (секунд, 0 - вимкнено)", color=DIM).grid(
            row=2, column=0, sticky="ew", padx=12, pady=(12, 0))
        self.sc_follow = self._scale(card, 0, 20, 1, s.get("followup_seconds", 8),
                                     lambda v: self._debounce("follow", lambda: self._apply(followup_seconds=int(v))))
        self.sc_follow.grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 10))
        self._title(card, "СЛОВО «ДЖАРВІС» ПІД ВАШУ ВИМОВУ", color=DIM).grid(row=4, column=0, sticky="ew", padx=12, pady=(4, 2))
        self._hint(card, "Не реагує на «Джарвіс»? Натисніть «Навчити» і тричі скажіть слово після сигналу - "
                         "я запамʼятаю, як Google записує саме ваш голос.", wrap=520).grid(
            row=5, column=0, sticky="ew", padx=12)
        lrow = tk.Frame(card, bg=PANEL)
        lrow.grid(row=6, column=0, sticky="ew", padx=12, pady=(6, 4))
        lrow.columnconfigure(0, weight=1)
        self._button(lrow, "НАВЧИТИ СЛОВО «ДЖАРВІС»", GREEN, lambda: self._run_command("/learn"),
                     size=9, pady=5).grid(row=0, column=0, sticky="ew")
        self._button(lrow, "СКИНУТИ", RED, lambda: self.api.submit_text("/forget"), size=9, pady=5).grid(
            row=0, column=1, padx=(6, 0))
        self.learned_lbl = self._hint(card, "", wrap=520)
        self.learned_lbl.grid(row=7, column=0, sticky="ew", padx=12, pady=(0, 6))
        self._hint(card, "Чутливість мікрофона і паузу - на вкладці МІКРОФОН.", wrap=500).grid(
            row=8, column=0, sticky="ew", padx=12, pady=(0, 10))

        # --- браузер ---
        card = self._card(colL, "БРАУЗЕР")
        card.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        self.tg_chrome = self._toggle(card, "Завжди відкривати сайти й YouTube у Chrome", s.get("always_chrome"),
                                      lambda v: self._apply(always_chrome=v))
        self.tg_chrome.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 12))

        # --- ШІ ---
        card = self._card(colR, "ШТУЧНИЙ ІНТЕЛЕКТ (GOOGLE GEMINI)")
        card.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.ai_status = tk.Label(card, text="", bg=PANEL, fg=GREEN, font=(self.f_mono, 10, "bold"), anchor="w")
        self.ai_status.grid(row=1, column=0, sticky="ew", padx=12)
        self._hint(card, "Ключ безкоштовний: aistudio.google.com/app/apikey", wrap=500).grid(
            row=2, column=0, sticky="ew", padx=12, pady=(2, 6))
        krow = tk.Frame(card, bg=PANEL)
        krow.grid(row=3, column=0, sticky="ew", padx=12)
        krow.columnconfigure(0, weight=1)
        self.key_entry = self._entry(krow, show="•")
        self.key_entry.grid(row=0, column=0, sticky="ew", ipady=5)
        self.key_entry.bind("<Return>", lambda e: self._save_key())
        self._button(krow, "ЗБЕРЕГТИ", GREEN, self._save_key, size=9, pady=4).grid(row=0, column=1, padx=(6, 0))
        self._title(card, "МОДЕЛЬ", color=DIM).grid(row=4, column=0, sticky="ew", padx=12, pady=(10, 2))
        mrow = tk.Frame(card, bg=PANEL)
        mrow.grid(row=5, column=0, sticky="ew", padx=12, pady=(0, 12))
        mrow.columnconfigure(0, weight=1)
        self.model_entry = self._entry(mrow)
        self.model_entry.grid(row=0, column=0, sticky="ew", ipady=5)
        self._button(mrow, "ЗБЕРЕГТИ", CYAN, lambda: self._save_entry(self.model_entry, "gemini_model"),
                     size=9, pady=4).grid(row=0, column=1, padx=(6, 0))

        # --- погода ---
        card = self._card(colR, "ПОГОДА")
        card.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        self._hint(card, "Місто за замовчуванням для «Джарвіс, яка погода»", wrap=500).grid(
            row=1, column=0, sticky="ew", padx=12)
        crow = tk.Frame(card, bg=PANEL)
        crow.grid(row=2, column=0, sticky="ew", padx=12, pady=(4, 12))
        crow.columnconfigure(0, weight=1)
        self.city_entry = self._entry(crow)
        self.city_entry.grid(row=0, column=0, sticky="ew", ipady=5)
        self._button(crow, "ЗБЕРЕГТИ", CYAN, lambda: self._save_entry(self.city_entry, "city"),
                     size=9, pady=4).grid(row=0, column=1, padx=(6, 0))

        # --- службове ---
        card = self._card(colR, "СЛУЖБОВЕ")
        card.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        srow = tk.Frame(card, bg=PANEL)
        srow.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 12))
        srow.columnconfigure((0, 1), weight=1)
        self._button(srow, "ВІДКРИТИ ЖУРНАЛ", DIM, self._open_log, size=9, pady=5).grid(row=0, column=0, sticky="ew", padx=(0, 3))
        self._button(srow, "ПАПКА НАЛАШТУВАНЬ", DIM, self._open_config, size=9, pady=5).grid(row=0, column=1, sticky="ew", padx=(3, 0))

    def _load_settings(self):
        s = self._safe_settings()
        if not s:
            return
        for vid, b in self.voice_btns.items():
            on = vid == s.get("voice")
            b.configure(fg=CYAN if on else DIM, highlightbackground=CYAN if on else LINE2)
        self.tg_say.set_value(s.get("say_listening"))
        self.tg_wake.set_value(s.get("require_wake_word"))
        self.tg_chrome.set_value(s.get("always_chrome"))
        self.sc_follow.set(s.get("followup_seconds", 8))
        self.sc_sens.set(s.get("mic_sensitivity", 6))
        self.sc_pause.set(s.get("pause_seconds", 0.8))
        learned = s.get("wake_learned") or []
        self.learned_lbl.configure(text=("Навчені варіанти: " + ", ".join(learned)) if learned
                                   else "Навчених варіантів поки немає.", fg=GREEN if learned else MUTED)
        self.ai_status.configure(text="● КЛЮЧ ВСТАНОВЛЕНО" if s.get("gemini_key_set") else "● КЛЮЧА НЕМАЄ - ШІ НЕ ПРАЦЮЄ",
                                 fg=GREEN if s.get("gemini_key_set") else AMBER)
        for entry, key in ((self.model_entry, "gemini_model"), (self.city_entry, "city")):
            if self.focus_get() is not entry:
                entry.delete(0, "end")
                entry.insert(0, s.get(key) or "")

    def _set_voice(self, vid):
        self._apply(voice=vid)
        self._load_settings()
        self.api.submit_text("хто ти")         # одразу чути новий голос

    def _save_entry(self, entry, key):
        val = entry.get().strip()
        if val:
            self._apply(**{key: val})
            self._log("·", "Збережено", "sys")

    def _save_key(self):
        key = self.key_entry.get().strip()
        if not key:
            return
        self.key_entry.delete(0, "end")
        self.api.submit_text("/key " + key)      # ядро збереже ключ і перевірить його
        self.after(4000, self._load_settings)
        self.show_page("main")

    def _open_log(self):
        path = getattr(self.api, "LOG_PATH", "")
        if path and os.path.exists(path):
            os.startfile(path)
        else:
            self._log("!", "Журнал ще не створено (він пишеться лише у зібраному .exe)", "err")

    def _open_config(self):
        d = getattr(self.api, "CONFIG_DIR", "")
        if d:
            os.makedirs(d, exist_ok=True)
            os.startfile(d)


    # ============================ 07 НАВЧАННЯ ============================
    LEARN_SECTIONS = [
        {"key": "commands", "title": "МОЇ КОМАНДИ",
         "hint": "Коли Джарвіс почує фразу - виконає дію. Голосом: «Джарвіс, нова команда» або "
                 "«Джарвіс, коли я кажу кіно відкрий нетфлікс». Кілька дій - через крапку з комою: "
                 "«гучніше; відкрий ютуб».",
         "cols": [("phrase", "Фраза", 220), ("kind", "Тип", 190), ("action", "Що робити", 320), ("uses", "Разів", 60)],
         "fields": [("phrase", "Коли почую фразу"), ("kind", "Тип дії"), ("action", "Що робити")]},
        {"key": "corrections", "title": "ВИПРАВЛЕННЯ",
         "hint": "Google постійно чує одне слово замість іншого? Вкажіть, що він чує і як правильно - "
                 "виправлення застосується до всього почутого. Зручно додавати з ІСТОРІЇ.",
         "cols": [("wrong", "Google чує", 280), ("right", "Насправді", 280), ("hits", "Спрацювало", 100)],
         "fields": [("wrong", "Google чує"), ("right", "Насправді має бути")]},
        {"key": "aliases", "title": "НАЗВИ ПРОГРАМ",
         "hint": "Як ви називаєте програму → як вона називається насправді (як у меню Пуск). "
                 "Приклад: «качалка» → «qbittorrent». Відмінки («качалку») розуміє сам.",
         "cols": [("spoken", "Я кажу", 280), ("target", "Програма", 280)],
         "fields": [("spoken", "Я кажу"), ("target", "Справжня назва програми")]},
        {"key": "wake", "title": "СЛОВО «ДЖАРВІС»",
         "hint": "Як Google записує саме ваше «Джарвіс». Найпростіше - «Навчити голосом»: "
                 "тричі скажіть слово після сигналу.",
         "cols": [("word", "Варіант", 280), ("source", "Звідки", 120), ("hits", "Спрацювало", 100), ("created", "Додано", 140)],
         "fields": [("word", "Варіант слова")]},
        {"key": "history", "title": "ІСТОРІЯ ПОЧУТОГО",
         "hint": "Усе, що почув мікрофон (останні 500 фраз). Виберіть рядок і одним кліком зробіть з нього "
                 "виправлення, свою команду або варіант слова «Джарвіс».",
         "cols": [("ts", "Час", 120), ("raw", "Google записав", 320), ("text", "Після виправлень", 260), ("status", "Статус", 120)],
         "fields": []},
    ]

    def _build_learn(self, p):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("J.Treeview", background=FIELD, fieldbackground=FIELD, foreground=WHITE, rowheight=26,
                        borderwidth=0, font=(self.f_mono, 10))
        style.configure("J.Treeview.Heading", background=PANEL2, foreground=CYAN, relief="flat",
                        font=(self.f_mono, 9, "bold"), borderwidth=0)
        style.map("J.Treeview", background=[("selected", LINE2)], foreground=[("selected", CYAN)])
        style.map("J.Treeview.Heading", background=[("active", HOVER)])
        style.configure("J.Vertical.TScrollbar", background=PANEL2, troughcolor=FIELD, arrowcolor=DIM,
                        bordercolor=FIELD, lightcolor=PANEL2, darkcolor=PANEL2)

        p.columnconfigure(1, weight=1)
        p.rowconfigure(0, weight=1)
        nav = tk.Frame(p, bg=PANEL, width=250, highlightthickness=1, highlightbackground=LINE)
        nav.grid(row=0, column=0, sticky="ns", padx=(10, 6), pady=(0, 6))
        nav.grid_propagate(False)
        nav.columnconfigure(0, weight=1)
        self._title(nav, "БАЗА ЗНАНЬ").grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 8))
        self.learn_nav = {}
        for i, sec in enumerate(self.LEARN_SECTIONS, 1):
            b = tk.Label(nav, text=sec["title"], bg=PANEL, fg=DIM, font=(self.f_mono, 10, "bold"), anchor="w",
                         padx=12, pady=8, cursor="hand2")
            b.grid(row=i, column=0, sticky="ew")
            b.bind("<Button-1>", lambda e, k=sec["key"]: self._show_learn(k))
            b.bind("<Enter>", lambda e, w=b: w.configure(bg=HOVER))
            b.bind("<Leave>", lambda e, w=b, k=sec["key"]: w.configure(bg=PANEL2 if k == self.learn_page else PANEL))
            self.learn_nav[sec["key"]] = b
        tk.Frame(nav, bg=PANEL).grid(row=10, column=0, sticky="nsew")
        nav.rowconfigure(10, weight=1)
        self.learn_db_lbl = self._hint(nav, "", wrap=225)
        self.learn_db_lbl.grid(row=11, column=0, sticky="ew", padx=12, pady=(0, 6))
        self._button(nav, "ПАПКА З БАЗОЮ", DIM, self._open_db_folder, size=9, pady=5).grid(
            row=12, column=0, sticky="ew", padx=12, pady=(0, 12))

        wrap = tk.Frame(p, bg=BG)
        wrap.grid(row=0, column=1, sticky="nsew", padx=(6, 10), pady=(0, 6))
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)
        self.learn_frames, self.learn_trees, self.learn_forms, self.learn_rows, self.learn_msg = {}, {}, {}, {}, {}
        for sec in self.LEARN_SECTIONS:
            f = self._build_learn_section(wrap, sec)
            f.grid(row=0, column=0, sticky="nsew")
            self.learn_frames[sec["key"]] = f
        self.learn_page = "commands"
        self._show_learn("commands", refresh=False)

    def _build_learn_section(self, parent, sec):
        key = sec["key"]
        f = tk.Frame(parent, bg=PANEL, highlightthickness=1, highlightbackground=LINE)
        f.columnconfigure(0, weight=1)
        f.rowconfigure(2, weight=1)
        tk.Label(f, text=sec["title"], bg=PANEL, fg=WHITE, font=(self.f_title, 15, "bold"), anchor="w").grid(
            row=0, column=0, sticky="ew", padx=14, pady=(10, 0))
        self._hint(f, sec["hint"], wrap=900).grid(row=1, column=0, sticky="ew", padx=14, pady=(2, 8))

        tw = tk.Frame(f, bg=PANEL)
        tw.grid(row=2, column=0, sticky="nsew", padx=14)
        tw.rowconfigure(0, weight=1)
        tw.columnconfigure(0, weight=1)
        tree = ttk.Treeview(tw, columns=[c[0] for c in sec["cols"]], show="headings", style="J.Treeview",
                            selectmode="browse")
        for cid, name, width in sec["cols"]:
            tree.heading(cid, text=name, anchor="w")
            tree.column(cid, width=width, anchor="w", stretch=cid not in ("uses", "hits", "source", "ts", "created"))
        tree.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(tw, orient="vertical", command=tree.yview, style="J.Vertical.TScrollbar")
        sb.grid(row=0, column=1, sticky="ns")
        tree.configure(yscrollcommand=sb.set)
        tree.bind("<<TreeviewSelect>>", lambda e, k=key: self._learn_select(k))
        tree.bind("<Delete>", lambda e, k=key: self._learn_delete(k))
        self.learn_trees[key] = tree

        form = {}
        if sec["fields"]:
            ff = tk.Frame(f, bg=PANEL)
            ff.grid(row=3, column=0, sticky="ew", padx=14, pady=(10, 0))
            col = 0
            for fid, label in sec["fields"]:
                box = tk.Frame(ff, bg=PANEL)
                box.grid(row=0, column=col, sticky="ew", padx=(0, 10))
                ff.columnconfigure(col, weight=0 if fid == "kind" else 1)
                self._title(box, label.upper(), color=DIM).pack(anchor="w")
                if fid == "kind":
                    form[fid] = self._kind_picker(box)
                else:
                    e = self._entry(box)
                    e.pack(fill="x", ipady=5)
                    e.bind("<Return>", lambda ev, k=key: self._learn_save(k))
                    form[fid] = e
                col += 1
        self.learn_forms[key] = form

        br = tk.Frame(f, bg=PANEL)
        br.grid(row=4, column=0, sticky="ew", padx=14, pady=(10, 12))
        if sec["fields"]:
            self._button(br, "ДОДАТИ / ОНОВИТИ", GREEN, lambda k=key: self._learn_save(k), size=9, pady=5).pack(side="left")
            self._button(br, "ОЧИСТИТИ ПОЛЯ", DIM, lambda k=key: self._learn_clear_form(k), size=9, pady=5).pack(side="left", padx=6)
        if key == "commands":
            self._button(br, "ПЕРЕВІРИТИ", CYAN, self._learn_test, size=9, pady=5).pack(side="left")
        if key == "wake":
            self._button(br, "НАВЧИТИ ГОЛОСОМ", AMBER, lambda: self._run_command("/learn"), size=9, pady=5).pack(side="left")
        if key == "history":
            self._button(br, "→ ВИПРАВЛЕННЯ", GREEN, lambda: self._history_to("corrections"), size=9, pady=5).pack(side="left")
            self._button(br, "→ МОЯ КОМАНДА", GREEN, lambda: self._history_to("commands"), size=9, pady=5).pack(side="left", padx=6)
            self._button(br, "→ СЛОВО «ДЖАРВІС»", AMBER, lambda: self._history_to("wake"), size=9, pady=5).pack(side="left")
            self._button(br, "ОНОВИТИ", DIM, self._refresh_learn, size=9, pady=5).pack(side="left", padx=6)
        self._button(br, "ОЧИСТИТИ ВСЕ", RED, lambda k=key: self._learn_clear_all(k), size=9, pady=5).pack(side="right")
        if key != "history":
            self._button(br, "ВИДАЛИТИ", RED, lambda k=key: self._learn_delete(k), size=9, pady=5).pack(side="right", padx=6)
        msg = tk.Label(br, text="", bg=PANEL, fg=GREEN, font=(self.f_mono, 9, "bold"))
        msg.pack(side="left", padx=12)
        self.learn_msg[key] = msg
        return f

    def _kind_picker(self, parent):
        """Вибір типу дії кнопками."""
        kinds = getattr(self.api, "COMMAND_KINDS", {"command": "Команда Джарвіса"})
        holder = tk.Frame(parent, bg=PANEL)
        holder.pack(anchor="w")
        holder.value = "command"
        holder.btns = {}

        def choose(k):
            holder.value = k
            for kk, b in holder.btns.items():
                b.configure(fg=CYAN if kk == k else DIM, highlightbackground=CYAN if kk == k else LINE2)

        for k, name in kinds.items():
            b = tk.Label(holder, text=name, bg="#04101c", fg=DIM, font=(self.f_mono, 8, "bold"), padx=6, pady=5,
                         highlightthickness=1, highlightbackground=LINE2, cursor="hand2")
            b.pack(side="left", padx=(0, 4))
            b.bind("<Button-1>", lambda e, kk=k: choose(kk))
            holder.btns[k] = b
        holder.choose = choose
        choose("command")
        return holder

    def _show_learn(self, key, refresh=True):
        self.learn_page = key
        self.learn_frames[key].tkraise()
        for k, b in self.learn_nav.items():
            b.configure(bg=PANEL2 if k == key else PANEL, fg=CYAN if k == key else DIM)
        if refresh:
            self._refresh_learn()

    def _learn_flash(self, key, text, ok=True):
        lbl = self.learn_msg[key]
        lbl.configure(text=text, fg=GREEN if ok else RED)
        self.after(4000, lambda: lbl.configure(text=""))

    @staticmethod
    def _fmt_time(ts, with_date=False):
        try:
            return datetime.datetime.fromtimestamp(float(ts)).strftime("%d.%m %H:%M" if with_date else "%d.%m %H:%M:%S")
        except Exception:
            return ""

    def _refresh_learn(self):
        key = self.learn_page
        kinds = getattr(self.api, "COMMAND_KINDS", {})
        try:
            rows = self.api.learning_list(key)
        except Exception as e:
            self._learn_flash(key, f"Помилка бази: {e}", ok=False)
            rows = []
        tree = self.learn_trees[key]
        sel = tree.selection()
        tree.delete(*tree.get_children())
        self.learn_rows[key] = {}
        cols = [c[0] for c in next(s for s in self.LEARN_SECTIONS if s["key"] == key)["cols"]]
        for r in rows:
            vals = []
            for c in cols:
                v = r.get(c, "")
                if c == "kind":
                    v = kinds.get(v, v)
                elif c == "ts":
                    v = self._fmt_time(v)
                elif c == "created":
                    v = self._fmt_time(v, True)
                vals.append(v)
            iid = str(r["id"])
            tree.insert("", "end", iid=iid, values=vals)
            self.learn_rows[key][iid] = r
        if sel and sel[0] in self.learn_rows[key]:
            tree.selection_set(sel[0])
        try:
            st = self.api.learning_stats()
        except Exception:
            st = {}
        names = {"commands": "commands", "corrections": "corrections", "aliases": "aliases",
                 "wake": "wake_words", "history": "history"}
        for sec in self.LEARN_SECTIONS:
            n = st.get(names[sec["key"]])
            self.learn_nav[sec["key"]].configure(text=sec["title"] + (f"   {n}" if n else ""))
        self.learn_db_lbl.configure(text="Файл бази:\n" + str(getattr(self.api, "DB_PATH", "")))

    def _learn_selected(self, key):
        sel = self.learn_trees[key].selection()
        return self.learn_rows.get(key, {}).get(sel[0]) if sel else None

    def _learn_select(self, key):
        row = self._learn_selected(key)
        if not row:
            return
        for fid, w in self.learn_forms[key].items():
            if fid == "kind":
                w.choose(row.get("kind", "command"))
            else:
                w.delete(0, "end")
                w.insert(0, str(row.get(fid, "")))

    def _learn_clear_form(self, key):
        for fid, w in self.learn_forms[key].items():
            if fid == "kind":
                w.choose("command")
            else:
                w.delete(0, "end")
        self.learn_trees[key].selection_remove(*self.learn_trees[key].selection())

    def _learn_save(self, key):
        data = {fid: (w.value if fid == "kind" else w.get().strip()) for fid, w in self.learn_forms[key].items()}
        ok, msg = self.api.learning_save(key, data)
        self._learn_flash(key, ("✔ " if ok else "✖ ") + msg, ok)
        if ok:
            self._learn_clear_form(key)
            self._refresh_learn()

    def _learn_delete(self, key):
        row = self._learn_selected(key)
        if not row:
            self._learn_flash(key, "Спершу виберіть рядок", ok=False)
            return
        self.api.learning_delete(key, row["id"])
        self._learn_clear_form(key)
        self._refresh_learn()
        self._learn_flash(key, "Видалено")

    def _learn_clear_all(self, key):
        title = next(s["title"] for s in self.LEARN_SECTIONS if s["key"] == key)
        if messagebox.askyesno("J.A.R.V.I.S.", f"Видалити ВСЕ з розділу «{title}»?", parent=self):
            self.api.learning_clear(key)
            self._refresh_learn()

    def _learn_test(self):
        row = self._learn_selected("commands")
        text = row["phrase"] if row else self.learn_forms["commands"]["phrase"].get().strip()
        if text:
            self._run_command(text)

    def _history_to(self, target):
        row = self._learn_selected("history")
        if not row:
            self._learn_flash("history", "Спершу виберіть рядок в історії", ok=False)
            return
        raw = row.get("raw") or row.get("text") or ""
        if target == "wake":
            word = raw.split()[0] if raw.split() else ""
            ok, msg = self.api.learning_save("wake", {"word": word})
            self._learn_flash("history", f"«{word}»: {msg}", ok)
            return
        self._show_learn(target)
        self._learn_clear_form(target)
        form = self.learn_forms[target]
        if target == "corrections":
            form["wrong"].insert(0, raw)
            form["right"].focus_set()
            self._learn_flash(target, "Виправте текст: залиште лише помилкове слово і напишіть правильне")
        else:
            fw = getattr(self.api, "find_wake_word", None)
            rest = fw(raw) if fw else None
            form["phrase"].insert(0, rest if rest else raw)
            form["action"].focus_set()

    def _open_db_folder(self):
        path = getattr(self.api, "DB_PATH", "")
        if path and path != ":memory:":
            os.startfile(os.path.dirname(path))

    # ============================ повільні оновлення ============================
    def _slow_tick(self):
        self._slow_n += 1
        try:
            self._draw_top()
            s = self._sample_stats()             # статистику збираємо завжди (для графіка CPU)
            if self.wm_state() != "iconic":
                if self.page == "main":
                    self._draw_left(s)
                elif self.page == "timers":
                    self._draw_timers()
                elif self.page == "apps" and self._slow_n % 3 == 0:
                    self._refresh_apps()
                if self._slow_n % 2 == 0:
                    self._draw_bottom()
        except Exception as e:
            print(f"(hud: {e})")
        self.after(1000, self._slow_tick)

    def _draw_top(self):
        c = self.top
        c.delete("all")
        w = c.winfo_width()
        now = datetime.datetime.now()
        c.create_text(w / 2, 22, text="J . A . R . V . I . S", fill=WHITE, font=(self.f_title, 21, "bold"))
        c.create_text(w / 2, 43, text="Just A Rather Very Intelligent System", fill=DIM, font=(self.f_mono, 8))
        c.create_line(10, 53, w - 10, 53, fill=LINE)
        ok = self.mic.get("ok", True)
        c.create_oval(18, 16, 28, 26, fill=GREEN if ok else RED, outline="")
        c.create_text(36, 21, text="СИСТЕМА В МЕРЕЖІ" if ok else "МІКРОФОН НЕДОСТУПНИЙ", anchor="w",
                      fill=GREEN if ok else RED, font=(self.f_mono, 9, "bold"))
        c.create_text(w - 16, 20, text=now.strftime("%H:%M:%S"), anchor="e", fill=CYAN, font=(self.f_title, 19, "bold"))
        c.create_text(w - 16, 41, text=f"{WEEKDAYS[now.weekday()]}, {now.day} {MONTHS[now.month - 1]} {now.year}",
                      anchor="e", fill=DIM, font=(self.f_mono, 8))

    def _sample_stats(self):
        s = {"cpu": None, "ram": None, "disk": None, "batt": None, "procs": None, "up": None}
        if not psutil:
            return s
        s["cpu"] = psutil.cpu_percent(interval=None)
        self.cpu_hist = self.cpu_hist[1:] + [s["cpu"]]
        if self.page != "main":                   # решту рахуємо лише коли панель видно
            return s
        s["ram"] = psutil.virtual_memory().percent
        try:
            s["disk"] = psutil.disk_usage("C:\\" if os.name == "nt" else "/").percent
        except Exception:
            pass
        try:
            b = psutil.sensors_battery()
            if b:
                s["batt"] = (b.percent, b.power_plugged)
        except Exception:
            pass
        try:
            s["procs"] = len(psutil.pids())
            s["up"] = time.time() - psutil.boot_time()
        except Exception:
            pass
        try:
            io = psutil.net_io_counters()
            now = time.time()
            if self.net_prev:
                pt, pr, ps_ = self.net_prev
                dt = max(0.2, now - pt)
                self.net_down = (io.bytes_recv - pr) / dt
                self.net_up = (io.bytes_sent - ps_) / dt
                self.net_peak = max(self.net_peak * 0.98, self.net_down, self.net_up, 100 * 1024)
            self.net_prev = (now, io.bytes_recv, io.bytes_sent)
        except Exception:
            pass
        return s

    def _seg_bar(self, c, x, y, w, frac, color, n=24):
        gap = 2
        sw = (w - gap * (n - 1)) / n
        on = int(round(max(0.0, min(1.0, frac)) * n))
        for i in range(n):
            x0 = x + i * (sw + gap)
            c.create_rectangle(x0, y, x0 + sw, y + 8, fill=color if i < on else "#0a1b2e", outline="")

    def _draw_left(self, s):
        c = self.left
        c.delete("all")
        w = c.winfo_width() - 2

        c.create_text(14, 18, text="МОНІТОР СИСТЕМИ", anchor="w", fill=CYAN, font=(self.f_mono, 9, "bold"))
        c.create_line(12, 32, w - 12, 32, fill=LINE)

        y = 48
        for label, val in (("ПРОЦЕСОР", s["cpu"]), ("ПАМʼЯТЬ", s["ram"]), ("ДИСК C:", s["disk"])):
            col = CYAN if (val or 0) < 75 else (AMBER if (val or 0) < 90 else RED)
            c.create_text(14, y, text=label, anchor="w", fill=DIM, font=(self.f_mono, 9))
            c.create_text(w - 14, y, text="—" if val is None else f"{val:.0f}%", anchor="e", fill=WHITE,
                          font=(self.f_mono, 11, "bold"))
            self._seg_bar(c, 14, y + 12, w - 28, (val or 0) / 100, col)
            y += 46

        c.create_text(14, y, text="МЕРЕЖА", anchor="w", fill=DIM, font=(self.f_mono, 9))
        y += 18
        c.create_text(14, y, text="↓ " + human_rate(self.net_down), anchor="w", fill=GREEN, font=(self.f_mono, 10, "bold"))
        c.create_text(w - 14, y, text="↑ " + human_rate(self.net_up), anchor="e", fill=AMBER, font=(self.f_mono, 10, "bold"))
        self._seg_bar(c, 14, y + 12, w - 28, self.net_down / self.net_peak, GREEN)
        y += 40

        if s["batt"]:
            pct, plugged = s["batt"]
            c.create_text(14, y, text="БАТАРЕЯ" + (" (заряджається)" if plugged else ""), anchor="w",
                          fill=DIM, font=(self.f_mono, 9))
            c.create_text(w - 14, y, text=f"{pct:.0f}%", anchor="e", fill=WHITE, font=(self.f_mono, 11, "bold"))
            self._seg_bar(c, 14, y + 12, w - 28, pct / 100, GREEN if pct > 25 else RED)
            y += 46

        c.create_text(14, y, text="НАВАНТАЖЕННЯ CPU · 60 с", anchor="w", fill=DIM, font=(self.f_mono, 8))
        y += 14
        gh, gw = 70, w - 28
        c.create_rectangle(14, y, 14 + gw, y + gh, outline=LINE2)
        for q in (0.25, 0.5, 0.75):
            c.create_line(14, y + gh * q, 14 + gw, y + gh * q, fill="#071b30")
        pts = []
        for i, v in enumerate(self.cpu_hist):
            pts += [14 + gw * i / (len(self.cpu_hist) - 1), y + gh - gh * min(100, v) / 100]
        c.create_line(*pts, fill=CYAN, width=2)
        y += gh + 18

        if s["procs"] is not None:
            up = int(s["up"] or 0)
            c.create_text(14, y, text=f"ПРОЦЕСІВ  {s['procs']}", anchor="w", fill=DIM, font=(self.f_mono, 9))
            y += 18
            c.create_text(14, y, text=f"АПТАЙМ  {up // 3600} год {up % 3600 // 60} хв", anchor="w", fill=DIM,
                          font=(self.f_mono, 9))
            y += 18

        # таймери прямо на головній
        try:
            timers = self.api.list_timers()
        except Exception:
            timers = []
        if timers:
            y += 8
            c.create_text(14, y, text="ТАЙМЕРИ", anchor="w", fill=CYAN, font=(self.f_mono, 9, "bold"))
            for t in timers[:3]:
                y += 20
                c.create_text(14, y, text=(t["label"] or "таймер")[:18], anchor="w", fill=DIM, font=(self.f_mono, 9))
                c.create_text(w - 14, y, text=mmss(t["left"]), anchor="e", fill=AMBER if t["left"] < 60 else WHITE,
                              font=(self.f_mono, 11, "bold"))
        if not psutil:
            c.create_text(14, y + 24, text="встановіть psutil для монітора", anchor="w", fill=AMBER, font=(self.f_mono, 8))

    def _draw_bottom(self):
        c = self.bottom
        c.delete("all")
        w = c.winfo_width()
        try:
            st = self.api.status() or {}
        except Exception:
            st = {}
        ai = st.get("ai")
        c.create_text(16, 13, anchor="w", font=(self.f_mono, 9),
                      text="ШІ: " + ("ПІДКЛЮЧЕНО" if ai else "ПОТРІБЕН КЛЮЧ (вкладка 06)"),
                      fill=GREEN if ai else AMBER)
        apps = st.get("apps")
        c.create_text(w * 0.36, 13, font=(self.f_mono, 9), fill=DIM,
                      text=f"ПРОГРАМ У БАЗІ: {apps}" if apps else "СКАНУЮ ПРОГРАМИ...")
        c.create_text(w - 16, 13, anchor="e", font=(self.f_mono, 9), fill=DIM,
                      text="«Джарвіс, ...»   ·   Ctrl+1..7 вкладки   ·   Ctrl+M мікрофон   ·   F11")