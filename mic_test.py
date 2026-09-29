"""
Діагностика мікрофона для Джарвіса.
Запуск (у папці з jarvis.py, Джарвіса перед цим ЗАКРИЙТЕ):
    python mic_test.py          - мікрофон за замовчуванням
    python mic_test.py 3        - мікрофон з номером 3 (номери показує цей же скрипт)
Під час запису скажіть: "Джарвіс, котра година".
Результат збережеться у mic_test_result.txt - надішліть його мені.
"""
import math
import os
import sys
import time
import array
import wave

OUT = []


def log(*a):
    line = " ".join(str(x) for x in a)
    print(line)
    OUT.append(line)


def rms(data):
    a = array.array("h", data[: len(data) - len(data) % 2])
    return math.sqrt(sum(x * x for x in a) / len(a)) if a else 0.0


def main():
    log("=== ДІАГНОСТИКА МІКРОФОНА ===")
    log("Python:", sys.version.split()[0])
    try:
        import speech_recognition as sr
        import pyaudio
        log("SpeechRecognition:", sr.__version__, "| PyAudio:", pyaudio.__version__)
    except Exception as e:
        log("!!! НЕ ВСТАНОВЛЕНО:", e, "\n    Виконайте: pip install -r requirements.txt")
        return

    index = int(sys.argv[1]) if len(sys.argv) > 1 else None
    pa = pyaudio.PyAudio()
    try:
        default = pa.get_default_input_device_info()
        log(f"\nМікрофон Windows за замовчуванням: [{default['index']}] {default['name']}")
    except Exception as e:
        default = None
        log("\n!!! Windows не бачить ЖОДНОГО мікрофона за замовчуванням:", e)

    log("\nВсі пристрої вводу:")
    apis = {i: pa.get_host_api_info_by_index(i)["name"] for i in range(pa.get_host_api_count())}
    for i in range(pa.get_device_count()):
        d = pa.get_device_info_by_index(i)
        if d.get("maxInputChannels", 0) > 0:
            log(f"  [{i}] {d['name']}  ({apis.get(d['hostApi'])}, {int(d['defaultSampleRate'])} Гц)")

    dev = index if index is not None else (default["index"] if default else None)
    if dev is None:
        log("\nНема чого тестувати.")
        return
    native = int(pa.get_device_info_by_index(dev)["defaultSampleRate"])
    pa.terminate()

    results = {}
    for rate in (16000, native):
        if rate in results:
            continue
        log(f"\n--- Запис 4 с, пристрій [{dev}], {rate} Гц. ГОВОРІТЬ: «Джарвіс, котра година» ---")
        for n in (3, 2, 1):
            print(f"  {n}...")
            time.sleep(0.7)
        print("  >>> ГОВОРІТЬ ЗАРАЗ <<<")
        try:
            mic = sr.Microphone(device_index=dev, sample_rate=rate)
            frames, levels = [], []
            with mic as src:
                t_end = time.time() + 4
                while time.time() < t_end:
                    d = src.stream.read(src.CHUNK)
                    frames.append(d)
                    levels.append(rms(d))
            data = b"".join(frames)
            nonzero = sum(1 for v in levels if v > 0)
            lv = sorted(levels)
            log(f"  Рівень: тиша≈{lv[len(lv) // 10]:.0f}  середнє={sum(lv) / len(lv):.0f}  максимум={lv[-1]:.0f}"
                f"  (ненульових шматків {nonzero}/{len(levels)})")
            if nonzero == 0:
                log("  !!! СУЦІЛЬНІ НУЛІ - Windows блокує доступ або пристрій вимкнено.")
            elif lv[-1] < 300:
                log("  !!! Дуже тихо. Підніміть гучність мікрофона у Windows (Звук → Запис → Властивості → Рівні).")
            else:
                log("  Сигнал є.")
            fn = f"mic_test_{rate}.wav"
            with wave.open(fn, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(mic.SAMPLE_WIDTH)
                w.setframerate(mic.SAMPLE_RATE)
                w.writeframes(data)
            log(f"  Збережено {fn} - можете прослухати, чи чутно вас.")
            try:
                r = sr.Recognizer().recognize_google(sr.AudioData(data, mic.SAMPLE_RATE, mic.SAMPLE_WIDTH),
                                                     language="uk-UA", show_all=True)
                alts = [a.get("transcript") for a in (r or {}).get("alternative", [])] if isinstance(r, dict) else []
                log("  Google почув:", alts if alts else "НІЧОГО (шум або тиша)")
            except Exception as e:
                log("  !!! Google недоступний:", type(e).__name__, e)
            results[rate] = True
        except Exception as e:
            log(f"  !!! Не вдалося відкрити на {rate} Гц: {type(e).__name__}: {e}")
            results[rate] = False


if __name__ == "__main__":
    try:
        main()
    finally:
        with open("mic_test_result.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(OUT))
        print("\nРезультат збережено у mic_test_result.txt")
        input("Натисніть Enter, щоб закрити...")