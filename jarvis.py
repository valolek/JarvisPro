"""
J.A.R.V.I.S. - запуск програми з вікном.

    python jarvis.py              - звичайний запуск
    python jarvis.py --minimized  - запуск згорнутим (так стартує з Windows)

Файли:
    jarvis.py       цей файл: збирає вікно й ядро разом
    hud.py          вікно з вкладками (ядро, мікрофон, команди, програми, таймери, налаштування)
    jarvis_core.py  ядро: мікрофон, голос, команди, програми, таймери, погода, ШІ
"""

import ctypes
import os
import sys
import threading
import traceback


def _message(title: str, text: str, error: bool = False) -> None:
    """Показує вікно з повідомленням (для .exe без консолі це єдиний спосіб щось сказати)."""
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        (messagebox.showerror if error else messagebox.showinfo)(title, text)
        root.destroy()
    except Exception:
        print(f"{title}: {text}")


try:                                    # чіткий (не розмитий) текст на екранах з масштабуванням
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

try:
    import jarvis_core as core
except SystemExit:                      # ядро завершилось саме: Джарвіс уже запущений
    _message("J.A.R.V.I.S.", "Джарвіс уже запущений - подивіться на панель завдань "
                             "або біля годинника.")
    raise
except Exception:
    err = traceback.format_exc()
    print(err)
    _message("J.A.R.V.I.S. - помилка запуску",
             "Не вдалося завантажити ядро.\n\n" + err[-900:] +
             "\n\nПеревірте, що встановлено все з requirements.txt.", error=True)
    sys.exit(1)

from hud import HUD


def main() -> None:
    stop = threading.Event()

    hud = HUD(api=core, on_exit=stop.set)
    core.EVENT_HOOK = hud.post
    hud.post("log", "Ядро запускається...")
    if not core.GEMINI_API_KEY:
        hud.post("log", "Щоб я відповідав на питання, додайте ключ Gemini на вкладці НАЛАШТУВАННЯ "
                        "(безкоштовно: aistudio.google.com/app/apikey)")

    threading.Thread(target=core.run, args=(stop,), daemon=True, name="jarvis-core").start()

    if "--minimized" in sys.argv:
        hud.iconify()
    try:
        hud.mainloop()
    finally:
        stop.set()
        try:
            sys.stdout.flush()
        except Exception:
            pass
        os._exit(0)             # гарантовано прибирає всі фонові потоки (мікрофон, звук)


if __name__ == "__main__":
    main()