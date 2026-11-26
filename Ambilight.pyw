"""
Ambilight for OpenRGB — single-file build (engine + GUI + keyboard driver).

Double-click this file to open the interface. Everything the app needs lives
here: the DXGI capture engine, the tkinter GUI, and the XPG Summoner HID
fallback driver. There are no sibling modules to keep next to it — only
config.json, which is written beside this file.

Engine
    - DXGI capture on demand, downsampled on the GPU (no full-frame readback)
    - Colors pushed as one hand-packed write for all devices, never read back
    - Frame-rate independent smoothing (a setting means a time, not a frame count)
    - Below-normal priority + EcoQoS (never steals game cycles)
    - Manual GC (disabled during capture, collected on a wall-clock interval)
    - Dynamic FPS; an idle loop wakes the moment a still screen changes
    - Shared color frames with measured device latency compensation
    - Edge-only & letterbox detection (optional)

Usage
    Ambilight.pyw                       open the GUI (this is what a double-click does)
    pythonw Ambilight.pyw --startup     open the GUI minimised to the tray
    python  Ambilight.pyw --cli         run the headless capture loop in a console
    python  Ambilight.pyw --list | --test | --restore
    python  Ambilight.pyw --cli --fps 30 --edges --skip-bars

Any engine flag implies --cli, so `python Ambilight.pyw --test` works on its own.
"""

import sys
import os
import gc
import json
import math
import time
import struct
import ctypes
import logging
import locale
import argparse
import colorsys
import threading
from ctypes import wintypes

# numpy's OpenBLAS starts a worker thread per core at import and commits
# ~225 MB of buffers for them. BLAS only sits behind matmul / dot / linalg,
# none of which this file uses, so a single thread gives nothing up. Has to
# be set before numpy is imported.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

import numpy as np
import pystray
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageDraw
from openrgb import OpenRGBClient
from openrgb.utils import RGBColor

logging.getLogger("openrgb").setLevel(logging.CRITICAL)

# Set before the GUI is built: --startup means "come up hidden in the tray".
_STARTUP_MODE = "--startup" in sys.argv

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


# ===========================================================================
#  Localization (English / Russian)
# ===========================================================================

ALGO_KEYS = ("Dominant", "Average", "Center Focus")
LANG_CODES = ("auto", "en", "ru")

_UI_STRINGS = {
    "en": {
        "subtitle": "OpenRGB GPU Screen Sync",
        "language_label": "Language",
        "lang_auto": "Auto",
        "lang_en": "English",
        "lang_ru": "Русский",
        "display_label": "Display",
        "monitor_item": "Monitor {id}  —  {width}×{height}",
        "display_hint": "Select the monitor to capture colors from.",
        "fps_label": "Refresh Rate (FPS)",
        "fps_hint": "How many times per second the screen is analyzed. Default: 15.",
        "algo_label": "Scanning Algorithm",
        "algo_Dominant": "Dominant",
        "algo_Average": "Average",
        "algo_Center Focus": "Center Focus",
        "algo_hint": "How the script interprets screen colors.",
        "bright_label": "Max Brightness (1–100)",
        "bright_hint": "Limits the maximum brightness sent to the LEDs. Default: 100.",
        "smooth_label": "Transition Smoothness (1–100)",
        "smooth_hint": "Higher = snappier. {val} ≈ {ms} ms per colour change. Default: 55.",
        "min_bright_label": "Min Brightness (0–100)",
        "min_bright_hint": "LEDs never go below this brightness. 0 = allow full black. Default: 0.",
        "black_thresh_label": "Black Threshold (1–100)",
        "black_thresh_hint": "Pixels darker than this are treated as black. Default: 20.",
        "hotkey_label": "Global Hotkey",
        "hotkey_none": "None",
        "hotkey_recording": "Press a key combination...",
        "hotkey_hint": "Click to map a keyboard shortcut to toggle Ambilight ON/OFF.",
        "sync_cb": "Sync all devices",
        "sync_hint": "Shows the same frames on all devices and compensates their delay. Update rate follows the slowest device (~3.6 FPS here). Separate motherboard headers may still differ. Default: on.",
        "display_sync_cb": "Turn off with display (Windows)",
        "display_sync_hint": "Disables Ambilight when the monitor goes to sleep or PC is locked.",
        "edges_cb": "Edge Only",
        "edges_hint": "Analyzes only the outer 15% of the screen. Gives accurate Ambilight effect at the edges.",
        "bars_cb": "Skip Black Bars",
        "bars_hint": "Ignores cinematic letterbox bands so movies don't artificially darken the lighting.",
        "sat_cb": "Saturation Boost (+50%)",
        "sat_hint": "Makes colors punchy and vibrant. Great for muted games/movies.",
        "gamma_cb": "Gamma Correction",
        "gamma_hint": "Adjusts LED brightness curve to match human perception.",
        "hdr_cb": "Adaptive Brightness (HDR)",
        "hdr_hint": "Dims LEDs in dark scenes, flares in bright highlights.",
        "tray_cb": "Minimize to System Tray",
        "tray_hint": "When you minimize the window, it hides in the taskbar tray.",
        "autostart_cb": "Start with Windows",
        "autostart_hint": "Automatically launches the program hidden in the tray.",
        "autorun_cb": "Auto-start Ambilight",
        "autorun_hint": "Automatically starts the Ambilight loop after an 8-second delay (waits for OpenRGB).",
        "audio_cb": "Audio Visualizer (Music Mode)",
        "audio_hint": "Stops screen capture and pulses colors to the beat of computer audio.",
        "sched_start_cb": "Schedule Start",
        "sched_start_hint": "Automatically starts Ambilight at the chosen time.",
        "sched_stop_cb": "Schedule Stop",
        "sched_stop_hint": "Automatically stops Ambilight at the chosen time.",
        "btn_start": "▶  START",
        "btn_stop": "⏹  STOP",
        "status_stopped": "Stopped",
        "status_connecting": "Connecting to OpenRGB…",
        "status_waiting_server": "Waiting for OpenRGB SDK server... {sec}s left",
        "status_waiting_devices": "Waiting for OpenRGB devices... ({count} found)",
        "status_running": "Running  •  {devs}{extra}",
        "feat_edges": "edges",
        "feat_bars": "skip-bars",
        "feat_audio": "audio",
        "err_select_monitor": "Error: select a monitor",
        "err_fps": "Error: FPS must be > 0",
        "err_sdk_not_found": "OpenRGB SDK Server not found.",
        "err_sdk_unreachable": "OpenRGB SDK server not reachable on {host}:{port} - start OpenRGB and enable its SDK Server.",
        "err_startup": "Startup failed: {error}",
        "err_stopped": "Stopped: {error}",
        "status_autorun_wait": "Auto-start in {sec}s (waiting for OpenRGB)…",
        "status_autorun_go": "Auto-starting Ambilight…",
        "tray_show": "Show Ambilight",
        "tray_exit": "Exit",
        "handover_msg": (
            "Ambilight now runs inside OpenRGB: the \"Ambilight\" tab in the OpenRGB "
            "window, the Ambilight entry in OpenRGB's tray menu, and Ctrl+Alt+A. It "
            "starts with OpenRGB at logon.\n\nOpen OpenRGB now?"
        ),
        "cli_plugin_note": (
            "Note: the OpenRGB Ambilight plugin is installed. If it is running "
            "(OpenRGB tray > Ambilight), both will drive the LEDs."
        ),
        "cli_connecting": "Connecting to OpenRGB…",
        "cli_error": "ERROR: {error}",
        "cli_stopping": "\nStopping…",
        "cli_restored": "Devices restored to default mode.",
        "cli_no_monitors": "ERROR: No monitors detected.",
        "cli_list_header": "OpenRGB SDK — {devs}:\n",
        "cli_list_zone": "       zone: {name}  ({leds} LEDs)",
        "cli_list_hid_header": "\nDirect HID (bypassing OpenRGB):",
        "cli_list_hid_xpg": "  XPG Summoner Gaming Keyboard  (feature-report control)",
        "cli_list_monitors": "\nMonitors (mss):",
        "cli_started": "Ambilight started  |  {width}x{height}  |  capture: {backend}",
        "cli_started_meta": "  Devices: {count}  |  FPS: {fps}  |  Brightness: {brightness}%",
        "cli_started_features": "  Features: {features}",
        "cli_started_tuning": "  Priority: below-normal + EcoQoS  |  GC: manual  |  Smooth: {smooth}%",
        "cli_ctrl_c": "  Ctrl+C to stop\n",
        "cli_test_done": "Done!",
    },
    "ru": {
        "subtitle": "Синхронизация подсветки OpenRGB с экраном",
        "language_label": "Язык",
        "lang_auto": "Авто",
        "lang_en": "English",
        "lang_ru": "Русский",
        "display_label": "Монитор",
        "monitor_item": "Монитор {id}  —  {width}×{height}",
        "display_hint": "Выберите монитор для захвата цветов.",
        "fps_label": "Частота обновления (FPS)",
        "fps_hint": "Сколько раз в секунду анализируется экран. По умолчанию: 15.",
        "algo_label": "Алгоритм анализа",
        "algo_Dominant": "Доминирующий цвет",
        "algo_Average": "Средний цвет",
        "algo_Center Focus": "Центр экрана",
        "algo_hint": "Как программа вычисляет цвет экрана.",
        "bright_label": "Макс. яркость (1–100)",
        "bright_hint": "Ограничивает максимальную яркость подсветки. По умолчанию: 100.",
        "smooth_label": "Плавность переходов (1–100)",
        "smooth_hint": "Больше — быстрее. {val} ≈ {ms} мс на смену цвета. По умолчанию: 55.",
        "min_bright_label": "Мин. яркость (0–100)",
        "min_bright_hint": "Подсветка не становится темнее. 0 — можно погасить полностью. По умолчанию: 0.",
        "black_thresh_label": "Порог чёрного (1–100)",
        "black_thresh_hint": "Пиксели темнее этого считаются чёрными. По умолчанию: 20.",
        "hotkey_label": "Горячая клавиша",
        "hotkey_none": "Нет",
        "hotkey_recording": "Нажмите сочетание клавиш...",
        "hotkey_hint": "Нажмите, чтобы задать клавиши для включения/выключения Ambilight.",
        "sync_cb": "Синхронизировать устройства",
        "sync_hint": "Все устройства показывают одни и те же кадры с поправкой на задержку каждого. Частота обновления — по самому медленному (~3,6 FPS здесь). Разные разъёмы материнской платы могут немного расходиться. По умолчанию: вкл.",
        "display_sync_cb": "Выключать вместе с дисплеем (Windows)",
        "display_sync_hint": "Выключает подсветку, когда монитор спит или ПК заблокирован.",
        "edges_cb": "Только края",
        "edges_hint": "Анализирует только внешние 15% экрана. Точный эффект Ambilight по краям.",
        "bars_cb": "Пропускать чёрные полосы",
        "bars_hint": "Не учитывает чёрные полосы фильмов, чтобы они не затемняли подсветку.",
        "sat_cb": "Усилить насыщенность (+50%)",
        "sat_hint": "Делает цвета ярче и сочнее. Подходит для приглушённых игр и фильмов.",
        "gamma_cb": "Гамма-коррекция",
        "gamma_hint": "Корректирует кривую яркости диодов под восприятие глаза.",
        "hdr_cb": "Адаптивная яркость (HDR)",
        "hdr_hint": "Приглушает подсветку в тёмных сценах и усиливает в ярких.",
        "tray_cb": "Сворачивать в трей",
        "tray_hint": "При сворачивании окно прячется в область уведомлений.",
        "autostart_cb": "Запускать с Windows",
        "autostart_hint": "Автоматически запускает программу свёрнутой в трей.",
        "autorun_cb": "Автозапуск Ambilight",
        "autorun_hint": "Автоматически включает Ambilight через 8 секунд (ждёт OpenRGB).",
        "audio_cb": "Музыкальный режим (визуализатор звука)",
        "audio_hint": "Вместо захвата экрана подсветка пульсирует в такт звуку компьютера.",
        "sched_start_cb": "Включать по расписанию",
        "sched_start_hint": "Автоматически включает Ambilight в указанное время.",
        "sched_stop_cb": "Выключать по расписанию",
        "sched_stop_hint": "Автоматически выключает Ambilight в указанное время.",
        "btn_start": "▶  СТАРТ",
        "btn_stop": "⏹  СТОП",
        "status_stopped": "Остановлено",
        "status_connecting": "Подключение к OpenRGB…",
        "status_waiting_server": "Ожидание SDK-сервера OpenRGB… осталось {sec} с",
        "status_waiting_devices": "Ожидание устройств OpenRGB… (найдено: {count})",
        "status_running": "Работает  •  {devs}{extra}",
        "feat_edges": "края",
        "feat_bars": "без полос",
        "feat_audio": "музыка",
        "err_select_monitor": "Ошибка: выберите монитор",
        "err_fps": "Ошибка: FPS должен быть > 0",
        "err_sdk_not_found": "SDK-сервер OpenRGB не найден.",
        "err_sdk_unreachable": "SDK-сервер OpenRGB недоступен на {host}:{port} — запустите OpenRGB и включите SDK Server.",
        "err_startup": "Ошибка запуска: {error}",
        "err_stopped": "Остановлено: {error}",
        "status_autorun_wait": "Автозапуск через {sec} с (ожидание OpenRGB)…",
        "status_autorun_go": "Автозапуск Ambilight…",
        "tray_show": "Показать Ambilight",
        "tray_exit": "Выход",
        "handover_msg": (
            "Ambilight теперь работает внутри OpenRGB: вкладка «Ambilight» в окне "
            "OpenRGB, пункт Ambilight в меню трея OpenRGB и сочетание Ctrl+Alt+A. Он "
            "запускается вместе с OpenRGB при входе в систему.\n\nОткрыть OpenRGB сейчас?"
        ),
        "cli_plugin_note": (
            "Примечание: установлен плагин OpenRGB Ambilight. Если он запущен "
            "(трей OpenRGB > Ambilight), подсветкой будут управлять оба экземпляра."
        ),
        "cli_connecting": "Подключение к OpenRGB…",
        "cli_error": "ОШИБКА: {error}",
        "cli_stopping": "\nОстановка…",
        "cli_restored": "Устройства возвращены в режим по умолчанию.",
        "cli_no_monitors": "ОШИБКА: мониторы не обнаружены.",
        "cli_list_header": "OpenRGB SDK — {devs}:\n",
        "cli_list_zone": "       зона: {name}  ({leds} светодиодов)",
        "cli_list_hid_header": "\nПрямой HID (в обход OpenRGB):",
        "cli_list_hid_xpg": "  XPG Summoner Gaming Keyboard  (управление через feature-report)",
        "cli_list_monitors": "\nМониторы (mss):",
        "cli_started": "Ambilight запущен  |  {width}x{height}  |  захват: {backend}",
        "cli_started_meta": "  Устройства: {count}  |  FPS: {fps}  |  Яркость: {brightness}%",
        "cli_started_features": "  Функции: {features}",
        "cli_started_tuning": "  Приоритет: ниже обычного + EcoQoS  |  GC: ручной  |  Плавность: {smooth}%",
        "cli_ctrl_c": "  Нажмите Ctrl+C для остановки\n",
        "cli_test_done": "Готово!",
    },
}


def normalize_language(value):
    """Normalize a language preference to 'auto', 'en', or 'ru'."""
    if not isinstance(value, str):
        return "auto"
    raw = value.strip()
    low = raw.lower()
    if low in ("en", "en_us", "en-us", "english"):
        return "en"
    if low in ("ru", "ru_ru", "ru-ru", "russian") or raw.startswith("Рус") or low.startswith("рус"):
        return "ru"
    return "auto"


def detect_system_language():
    """Return 'ru' on Russian UI locales, 'en' otherwise."""
    try:
        lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        if (lang_id & 0x3FF) == 0x19:   # LANG_RUSSIAN
            return "ru"
        return "en"
    except Exception:
        pass
    for candidate in (locale.getlocale()[0], os.environ.get("LC_ALL"), os.environ.get("LANG")):
        if candidate and str(candidate).lower().startswith("ru"):
            return "ru"
    return "en"


def resolve_language(setting="auto"):
    """Resolve 'auto'/'en'/'ru' to the concrete UI language 'en' or 'ru'."""
    norm = normalize_language(setting)
    if norm == "auto":
        return detect_system_language()
    return norm


def format_device_count(count, lang="en"):
    """Format a device count with English or Russian pluralization."""
    resolved = resolve_language(lang)
    if resolved != "ru":
        return f"{count} device" if count == 1 else f"{count} devices"
    mod100 = count % 100
    mod10 = count % 10
    if 11 <= mod100 <= 14:
        word = "устройств"
    elif mod10 == 1:
        word = "устройство"
    elif 2 <= mod10 <= 4:
        word = "устройства"
    else:
        word = "устройств"
    return f"{count} {word}"


def tr(lang, key, **kwargs):
    """Look up a localized string for `lang` ('auto', 'en', or 'ru')."""
    resolved = resolve_language(lang)
    text = _UI_STRINGS.get(resolved, _UI_STRINGS["en"]).get(key, _UI_STRINGS["en"].get(key, key))
    return text.format(**kwargs) if kwargs else text


def _read_config_language(path=CONFIG_FILE):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "language" in data:
            return normalize_language(data["language"])
    except Exception:
        pass
    return "auto"


# ===========================================================================
#  XPG Summoner keyboard — direct USB-HID driver
# ===========================================================================
#
# Why this section exists
# -----------------------
# OpenRGB *detects* this keyboard but cannot drive its lighting. Its lighting
# interface (USB interface MI_02, HID usage page 0xFF01) exposes only a HID
# **Feature** report (report id 0x07, 264 bytes total) — there is no Output
# report. OpenRGB's driver sends the color payload with ``hid_write()`` (an
# *Output* report), which this device silently ignores. Sending the exact same
# payload as a **Feature** report (SetFeature) works.
#
# Protocol (taken from OpenRGB's XPGSummonerController, corrected to SetFeature):
#     * Feature report, report id 0x07, total length 264 bytes (1 id + 263 data).
#     * Init / terminate onboard effects:  [0x07, 0xEA, 0x00, 0x00, 0x00 ...]
#     * Color frame: 126 LEDs x 4 bytes [brightness, R, G, B], split into 2 packets:
#         packet 0: [0x07, 0xA3, 0x08, 0x00, 0x00, 0x00] + frame[0:256]
#         packet 1: [0x07, 0xA3, 0x08, 0x00, 0x01, 0x00] + frame[256:504]
#
# This is independent of OpenRGB; it talks to the keyboard directly via hidapi.


XPG_VID             = 0x125F
XPG_SUMMONER_PID    = 0x9418
LIGHTING_USAGE_PAGE = 0xFF01      # the interface that carries the Feature report
FEATURE_LEN         = 264         # report id (1) + 263 data bytes, per HID descriptor
LED_COUNT           = 126         # protocol frame size (hardware LED slots)
DEFAULT_BRIGHTNESS  = 0x64        # 100, per-LED brightness byte


class XPGSummonerKeyboard:
    """Solid-color controller for the XPG Summoner keyboard via HID feature reports."""

    def __init__(self):
        self._dev = None
        try:
            import hid
            self._hid = hid
        except Exception:
            self._hid = None
            return

        path = self._find_path(hid)
        if not path:
            return
        try:
            dev = hid.device()
            dev.open_path(path)
            self._dev = dev
            self.begin()
        except Exception:
            self._dev = None

    @staticmethod
    def _find_path(hid):
        try:
            for d in hid.enumerate():
                if (d["vendor_id"] == XPG_VID
                        and d["product_id"] == XPG_SUMMONER_PID
                        and d["usage_page"] == LIGHTING_USAGE_PAGE):
                    return d["path"]
        except Exception:
            pass
        return None

    @property
    def available(self):
        return self._dev is not None

    def _feature(self, payload):
        buf = bytearray(FEATURE_LEN)
        buf[:len(payload)] = payload
        self._dev.send_feature_report(bytes(buf))

    def begin(self):
        """Terminate onboard effects and put the keyboard into a direct/static state."""
        if self._dev:
            self._feature(bytes([0x07, 0xEA, 0x00, 0x00]))

    def set_color(self, r, g, b, brightness=DEFAULT_BRIGHTNESS):
        """Set every key to a single RGB color. Silently no-ops if unavailable."""
        if not self._dev:
            return
        frame = bytes((brightness, r & 0xFF, g & 0xFF, b & 0xFF)) * LED_COUNT
        try:
            self._feature(bytes([0x07, 0xA3, 0x08, 0x00, 0x00, 0x00]) + frame[0:256])
            self._feature(bytes([0x07, 0xA3, 0x08, 0x00, 0x01, 0x00]) + frame[256:504])
        except Exception:
            # Device went away (unplug / handle lost) — stop trying.
            self.close()

    def close(self):
        if self._dev:
            try:
                self._dev.close()
            except Exception:
                pass
            self._dev = None



# =========================================================================
#  Ambilight engine
# =========================================================================

# ---------------------------------------------------------------------------
#  Constants
# ---------------------------------------------------------------------------

DOWNSAMPLE = 20
IDLE_STREAK_LIMIT = 10       # frames until "idle" mode
IDLE_MULTIPLIER = 3.0        # sleep multiplier while idle
DEEP_IDLE_LIMIT = 60         # frames until "deep idle"
DEEP_IDLE_INTERVAL = 0.05    # deep-idle poll period
EDGE_RATIO = 0.15
BLACK_THRESHOLD = 20
BLACK_ROW_RATIO = 0.90

# How long ScreenGrabber waits before trying to get the desktop back after
# Windows took it away. Behind the lock screen or a UAC prompt the secure
# desktop refuses duplication for as long as it is up; retrying that in a
# loop with no sleep is a whole core spinning for as long as the PC stays
# locked.
CAPTURE_RETRY_INTERVAL = 0.5

# With the desktop gone this long, "Turn off with display" blanks the LEDs.
# Shorter losses are mode changes (a game going full screen), not a lock.
CAPTURE_LOST_BLANK_AFTER = 1.0

# Smoothing is an exponential moving average applied once per captured frame,
# so a raw slider value ties the transition time to the frame rate -- the same
# setting settles four times faster at 60 FPS than at 15. smooth_alpha()
# re-expresses it against a fixed reference so what the setting pins down is
# the wall-clock time constant, not a frame count.
SMOOTH_REF_FPS = 15.0

# gc.collect() costs 2-12 ms on this object graph. It used to run on every
# deep-idle iteration, which put that pause directly in front of every poll --
# i.e. in the wake-up path. A wall-clock interval keeps the collection without
# the latency.
GC_INTERVAL = 10.0

# OpenRGB SDK server. Connecting is a *wait*, not a one-shot: OpenRGB is
# normally started by the same logon that starts us (its launcher runs on a
# delay of its own) and its SDK server only starts listening once the process
# is up, so the first attempt almost always lands too early.
OPENRGB_HOST = "127.0.0.1"
OPENRGB_PORT = 6742
OPENRGB_CONNECT_TIMEOUT = 90.0   # wait this long for the SDK server to listen
OPENRGB_DEVICE_TIMEOUT = 30.0    # then this long for device detection to finish


class OpenRGBUnavailable(RuntimeError):
    """The OpenRGB SDK server could not be reached within the timeout."""


def _notify(status_cb, message):
    """Report connection progress to the GUI callback, or to stdout for the CLI."""
    if status_cb is not None:
        try:
            status_cb(message)
            return
        except Exception:
            pass
    print(message, flush=True)


# ---------------------------------------------------------------------------
#  System tuning
# ---------------------------------------------------------------------------

class _PowerThrottlingState(ctypes.Structure):
    _fields_ = [("Version", ctypes.c_ulong),
                ("ControlMask", ctypes.c_ulong),
                ("StateMask", ctypes.c_ulong)]


def lower_process_priority():
    """BELOW_NORMAL priority plus EcoQoS for the current process.

    EcoQoS is what Task Manager calls "efficiency mode": Windows may run the
    process on its most power-efficient cores and clocks. The per-frame work
    is a fraction of a millisecond, so nothing here needs a boosted core --
    only timer accuracy matters, and EcoQoS leaves timer resolution alone
    unless asked to ignore it.
    """
    kernel32 = ctypes.WinDLL("kernel32")
    handle = ctypes.c_void_p(-1)        # GetCurrentProcess() pseudo-handle
    try:
        BELOW_NORMAL = 0x00004000
        kernel32.SetPriorityClass(handle, BELOW_NORMAL)
    except Exception:
        pass
    try:
        PROCESS_POWER_THROTTLING = 4    # PROCESS_INFORMATION_CLASS
        EXECUTION_SPEED = 0x1
        state = _PowerThrottlingState(1, EXECUTION_SPEED, EXECUTION_SPEED)
        kernel32.SetProcessInformation(handle, PROCESS_POWER_THROTTLING,
                                       ctypes.byref(state), ctypes.sizeof(state))
    except Exception:
        pass


# ---------------------------------------------------------------------------
#  Monitor discovery
# ---------------------------------------------------------------------------

def get_monitors():
    import mss
    monitors = []
    try:
        with mss.mss() as sct:
            for i, m in enumerate(sct.monitors[1:]):
                monitors.append({
                    "id": i,
                    "top": m["top"],
                    "left": m["left"],
                    "width": m["width"],
                    "height": m["height"]
                })
    except Exception:
        pass
    return monitors


def find_monitor(monitors, preferred_width=2560):
    if not monitors:
        return None
    best, best_px = monitors[0], 0
    for m in monitors:
        if m["width"] == preferred_width:
            return m
        px = m["width"] * m["height"]
        if px > best_px:
            best_px = px
            best = m
    return best


# ---------------------------------------------------------------------------
#  Screen capture
# ---------------------------------------------------------------------------

# Desktop Duplication is driven through ctypes straight off the COM vtables,
# not through a capture library. bettercam, the obvious one, creates a
# Direct3D device on every graphics adapter in the machine the moment it is
# imported -- here the integrated GPU as well as the card the monitor hangs
# off -- and each device costs ~25 MB and eight driver threads for as long as
# the process lives. Only the handful of methods below are needed, on the one
# adapter that drives the captured monitor.

_VP = ctypes.c_void_p
_HR = ctypes.c_long          # an HRESULT, checked by hand so a timeout is not an exception

_E_ACCESSDENIED = 0x80070005             # the secure desktop (lock screen, UAC) is up
_DXGI_ERROR_WAIT_TIMEOUT = 0x887A0027

_DXGI_FORMAT_B8G8R8A8_UNORM = 87
_D3D11_USAGE_STAGING = 3
_D3D11_BIND_RENDER_TARGET = 0x20
_D3D11_CPU_ACCESS_READ = 0x20000
_D3D11_MAP_READ = 1
_D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST = 4
# The driver's own worker threads speed up draw-call-heavy rendering; there
# are half a dozen calls a frame here.
_D3D11_CREATE_DEVICE_PREVENT_INTERNAL_THREADING_OPTIMIZATIONS = 0x8


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16),
                ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]

    def __init__(self, text):
        p = text.split("-")
        super().__init__(int(p[0], 16), int(p[1], 16), int(p[2], 16),
                         (ctypes.c_ubyte * 8)(*bytes.fromhex(p[3] + p[4])))


_IID_IDXGIFactory1 = _GUID("770aae78-f26f-4dba-a829-253c83d1b387")
_IID_IDXGIOutput1 = _GUID("00cddea8-939b-4b83-a340-a685226666cc")
_IID_ID3D11Texture2D = _GUID("6f15aaf2-d208-4e89-9ab4-489535d34f9c")


class _DXGI_OUTPUT_DESC(ctypes.Structure):
    _fields_ = [("DeviceName", wintypes.WCHAR * 32),
                ("DesktopCoordinates", wintypes.RECT),
                ("AttachedToDesktop", wintypes.BOOL),
                ("Rotation", wintypes.UINT),
                ("Monitor", wintypes.HMONITOR)]


class _DXGI_OUTDUPL_FRAME_INFO(ctypes.Structure):
    _fields_ = [("LastPresentTime", ctypes.c_int64),
                ("LastMouseUpdateTime", ctypes.c_int64),
                ("AccumulatedFrames", wintypes.UINT),
                ("RectsCoalesced", wintypes.BOOL),
                ("ProtectedContentMaskedOut", wintypes.BOOL),
                ("PointerPosition", wintypes.POINT),
                ("PointerVisible", wintypes.BOOL),
                ("TotalMetadataBufferSize", wintypes.UINT),
                ("PointerShapeBufferSize", wintypes.UINT)]


class _D3D11_TEXTURE2D_DESC(ctypes.Structure):
    _fields_ = [("Width", wintypes.UINT), ("Height", wintypes.UINT),
                ("MipLevels", wintypes.UINT), ("ArraySize", wintypes.UINT),
                ("Format", wintypes.UINT),
                ("SampleCount", wintypes.UINT), ("SampleQuality", wintypes.UINT),
                ("Usage", wintypes.UINT), ("BindFlags", wintypes.UINT),
                ("CPUAccessFlags", wintypes.UINT), ("MiscFlags", wintypes.UINT)]


class _D3D11_MAPPED_SUBRESOURCE(ctypes.Structure):
    _fields_ = [("pData", _VP), ("RowPitch", wintypes.UINT), ("DepthPitch", wintypes.UINT)]


class _D3D11_VIEWPORT(ctypes.Structure):
    _fields_ = [(name, ctypes.c_float) for name in
                ("TopLeftX", "TopLeftY", "Width", "Height", "MinDepth", "MaxDepth")]


class CaptureError(OSError):
    """A DXGI / Direct3D call failed; `hresult` says how."""

    def __init__(self, hresult, what):
        self.hresult = hresult & 0xFFFFFFFF
        super().__init__("%s failed: 0x%08X" % (what, self.hresult))


_com_methods = {}


def _com(obj, index, restype, *argtypes):
    """Method number `index` in the vtable of the COM object `obj`.

    The result is called with `obj` as its first argument. Objects of one
    class share a vtable, so the lookup is cached per vtable, not per object;
    the vtables live in dxgi.dll and d3d11.dll, which ctypes never unloads.
    """
    vtable = _VP.from_address(obj.value).value
    key = (vtable, index)
    fn = _com_methods.get(key)
    if fn is None:
        address = _VP.from_address(vtable + index * ctypes.sizeof(_VP)).value
        fn = _com_methods[key] = ctypes.WINFUNCTYPE(restype, _VP, *argtypes)(address)
    return fn


def _check(hresult, what):
    if hresult < 0:
        raise CaptureError(hresult, what)


def _com_release(obj):
    if obj:
        _com(obj, 2, ctypes.c_ulong)(obj)                       # IUnknown::Release


def _com_query(obj, iid, what):
    out = _VP()
    _check(_com(obj, 0, _HR, ctypes.POINTER(_GUID), ctypes.POINTER(_VP))(
        obj, ctypes.byref(iid), ctypes.byref(out)), what)       # IUnknown::QueryInterface
    return out


# Point-samples every `step`-th pixel of the desktop into a render target
# 1/step the size on each axis: one triangle covering the target, and a pixel
# shader that fetches the texel its own position maps to.
_DOWNSAMPLE_HLSL = b"""
Texture2D<float4> desktop : register(t0);

float4 vs(uint id : SV_VertexID) : SV_Position
{
    return float4((id & 1) * 4.0 - 1.0, 1.0 - (id >> 1) * 4.0, 0.0, 1.0);
}

float4 ps(float4 pos : SV_Position) : SV_Target
{
    return desktop.Load(int3(int2(pos.xy) * STEP, 0));
}
"""


def _compile_shader(source, entry, target):
    """HLSL source -> bytecode, with the compiler that ships in Windows."""
    compiler = ctypes.WinDLL("d3dcompiler_47")
    compiler.D3DCompile.restype = _HR
    compiler.D3DCompile.argtypes = [
        ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p, _VP, _VP,
        ctypes.c_char_p, ctypes.c_char_p, wintypes.UINT, wintypes.UINT,
        ctypes.POINTER(_VP), ctypes.POINTER(_VP)]
    blob, errors = _VP(), _VP()
    hr = compiler.D3DCompile(source, len(source), None, None, None, entry, target,
                             0, 0, ctypes.byref(blob), ctypes.byref(errors))
    _com_release(errors)
    _check(hr, "D3DCompile")
    try:
        data = _com(blob, 3, _VP)(blob)                         # ID3DBlob::GetBufferPointer
        size = _com(blob, 4, ctypes.c_size_t)(blob)             # ID3DBlob::GetBufferSize
        return ctypes.string_at(data, size)
    finally:
        _com_release(blob)


class ScreenGrabber:
    """Downsampled RGB snapshots of one monitor, taken when the loop asks.

    Windows hands over each new desktop frame as a texture on the GPU. A
    pixel shader keeps one pixel in every `step` on each axis, and only that
    thumbnail -- 128x72 for a 2560x1440 monitor -- is copied to memory the CPU
    can read. Reading the full frame back instead moves 14.7 MB a frame from
    the graphics card for the sake of 37 kB of it; that path is still here,
    as the fallback for a driver that will not run the shader, and returns
    exactly the same pixels.

    Frames are pulled from the caller's loop, not pushed by a capture thread,
    so nothing is captured while the loop idles or plays audio, and grab()
    can tell "no change" apart from "desktop gone".

    grab() returns
        ndarray  a new frame, (H/step, W/step, 3) uint8 RGB, C-contiguous
        None     the desktop has not changed since the previous call
        LOST     nothing can be captured right now (lock screen, UAC prompt,
                 display mode change); grab() keeps retrying on its own

    grab(wait) blocks for up to `wait` seconds until there is a new frame,
    which lets an idle loop sleep inside Windows and still react at once.

    Falls back to GDI capture through mss when DXGI cannot be opened.
    """

    LOST = "lost"

    def __init__(self, mon, step=DOWNSAMPLE):
        self.mon = mon
        self.step = step
        self._device = None       # ID3D11Device on the adapter driving `mon`
        self._context = None      # its immediate context
        self._output = None       # IDXGIOutput1 showing `mon`
        self._display = None      # its name, e.g. \\.\DISPLAY1, once found
        self._shaders = None      # (vertex, pixel) shader, None: read full frames
        self._use_shader = True
        self._dup = None          # IDXGIOutputDuplication
        self._target = None       # (texture, view) the shader renders into
        self._stage = None        # CPU-readable texture
        self._stage_rows = 0
        self._stride = 1          # sampling still to do on the CPU
        self._size = (0, 0)       # desktop texture width, height
        self._rotation = 0
        self._retry_at = 0.0
        self._rebuild_at = 0.0
        self._info = _DXGI_OUTDUPL_FRAME_INFO()
        self._mapped = _D3D11_MAPPED_SUBRESOURCE()
        self._no_view = _VP()
        self._sct = None
        try:
            self._open()
        except Exception:
            self._close_dxgi(device=True)
            import mss
            self._sct = mss.mss()
            self._bbox = {"top": mon["top"], "left": mon["left"],
                          "width": mon["width"], "height": mon["height"]}

    @property
    def backend(self):
        return "DXGI" if self._sct is None else "GDI"

    # -- setup ------------------------------------------------------------

    def _find_output(self):
        """(adapter, output) showing this mss monitor, as owned COM pointers.

        mss numbers monitors across every GPU and DXGI numbers outputs per
        GPU, so an index alone can point a monitor on the second GPU at
        whatever shares its number on the first. The desktop origin is
        unambiguous; the index is only the fallback.

        Once found, the monitor is known by its display name, and a later
        search (after the driver was reset, say) takes that display or
        nothing: a monitor that is switched off must not hand the LEDs to
        whichever screen has taken over its place on the desktop.
        """
        dxgi = ctypes.WinDLL("dxgi")
        dxgi.CreateDXGIFactory1.restype = _HR
        dxgi.CreateDXGIFactory1.argtypes = [ctypes.POINTER(_GUID), ctypes.POINTER(_VP)]
        factory = _VP()
        _check(dxgi.CreateDXGIFactory1(ctypes.byref(_IID_IDXGIFactory1),
                                       ctypes.byref(factory)), "CreateDXGIFactory1")
        found = []                # [(adapter, output, display name, (left, top))]
        try:
            a = 0
            while True:
                adapter = _VP()
                if _com(factory, 12, _HR, wintypes.UINT, ctypes.POINTER(_VP))(
                        factory, a, ctypes.byref(adapter)) < 0:     # IDXGIFactory1::EnumAdapters1
                    break
                a += 1
                o = 0
                while True:
                    output = _VP()
                    if _com(adapter, 7, _HR, wintypes.UINT, ctypes.POINTER(_VP))(
                            adapter, o, ctypes.byref(output)) < 0:  # IDXGIAdapter::EnumOutputs
                        break
                    o += 1
                    desc = _DXGI_OUTPUT_DESC()
                    _com(output, 7, _HR, ctypes.POINTER(_DXGI_OUTPUT_DESC))(
                        output, ctypes.byref(desc))                 # IDXGIOutput::GetDesc
                    _com(adapter, 1, ctypes.c_ulong)(adapter)       # AddRef: one per output
                    found.append((adapter, output, desc.DeviceName,
                                  (desc.DesktopCoordinates.left, desc.DesktopCoordinates.top)))
                _com_release(adapter)
        finally:
            _com_release(factory)

        if self._display is not None:
            pick = next((i for i, f in enumerate(found) if f[2] == self._display), None)
        else:
            origin = (self.mon["left"], self.mon["top"])
            pick = next((i for i, f in enumerate(found) if f[3] == origin), None)
            if pick is None and found:
                pick = self.mon["id"] if 0 <= self.mon["id"] < len(found) else 0
        chosen = None
        for i, (adapter, output, name, _) in enumerate(found):
            if i == pick:
                chosen = (adapter, output)
                self._display = name
            else:
                _com_release(output)
                _com_release(adapter)
        if chosen is None:
            raise CaptureError(0x887A0002, "finding the monitor")   # DXGI_ERROR_NOT_FOUND
        return chosen

    def _open_device(self):
        adapter, output = self._find_output()
        output1, device, context = _VP(), _VP(), _VP()
        try:
            output1 = _com_query(output, _IID_IDXGIOutput1, "IDXGIOutput1")
            d3d11 = ctypes.WinDLL("d3d11")
            d3d11.D3D11CreateDevice.restype = _HR
            d3d11.D3D11CreateDevice.argtypes = [
                _VP, wintypes.UINT, _VP, wintypes.UINT, ctypes.POINTER(wintypes.UINT),
                wintypes.UINT, wintypes.UINT, ctypes.POINTER(_VP), _VP, ctypes.POINTER(_VP)]
            levels = (wintypes.UINT * 3)(0xB000, 0xA100, 0xA000)    # 11.0, 10.1, 10.0
            _check(d3d11.D3D11CreateDevice(
                adapter, 0, None, _D3D11_CREATE_DEVICE_PREVENT_INTERNAL_THREADING_OPTIMIZATIONS,
                levels, len(levels), 7, ctypes.byref(device), None, ctypes.byref(context)),
                "D3D11CreateDevice")
        except Exception:
            _com_release(output1)
            raise
        finally:
            _com_release(output)
            _com_release(adapter)
        self._output, self._device, self._context = output1, device, context

        if self._use_shader:
            try:
                self._shaders = self._create_shaders()
            except Exception:
                self._use_shader = False

    def _create_shaders(self):
        source = _DOWNSAMPLE_HLSL.replace(b"STEP", b"%d" % self.step)
        device = self._device
        made = []
        try:
            # ID3D11Device::CreateVertexShader is method 12, CreatePixelShader 15.
            for index, entry, target in ((12, b"vs", b"vs_4_0"), (15, b"ps", b"ps_4_0")):
                code = _compile_shader(source, entry, target)
                shader = _VP()
                _check(_com(device, index, _HR, ctypes.c_char_p, ctypes.c_size_t, _VP,
                            ctypes.POINTER(_VP))(device, code, len(code), None,
                                                 ctypes.byref(shader)), "creating a shader")
                made.append(shader)
        except Exception:
            for shader in made:
                _com_release(shader)
            raise
        return tuple(made)

    def _texture(self, width, height, usage, bind, cpu_access):
        desc = _D3D11_TEXTURE2D_DESC(width, height, 1, 1, _DXGI_FORMAT_B8G8R8A8_UNORM,
                                     1, 0, usage, bind, cpu_access, 0)
        texture = _VP()
        _check(_com(self._device, 5, _HR, ctypes.POINTER(_D3D11_TEXTURE2D_DESC), _VP,
                    ctypes.POINTER(_VP))(self._device, ctypes.byref(desc), None,
                                         ctypes.byref(texture)),
               "ID3D11Device::CreateTexture2D")
        return texture

    def _open(self):
        if self._device is None:
            self._open_device()
        device, context, output = self._device, self._context, self._output

        desc = _DXGI_OUTPUT_DESC()
        _check(_com(output, 7, _HR, ctypes.POINTER(_DXGI_OUTPUT_DESC))(
            output, ctypes.byref(desc)), "IDXGIOutput::GetDesc")
        rc = desc.DesktopCoordinates
        width, height = rc.right - rc.left, rc.bottom - rc.top
        rotation = (0, 0, 90, 180, 270)[desc.Rotation]
        if rotation in (90, 270):
            # The duplicated texture is the panel's own orientation.
            width, height = height, width

        dup = _VP()
        _check(_com(output, 22, _HR, _VP, ctypes.POINTER(_VP))(
            output, device, ctypes.byref(dup)), "IDXGIOutput1::DuplicateOutput")
        self._dup = dup

        if self._shaders is not None:
            cols, rows = len(range(0, width, self.step)), len(range(0, height, self.step))
            texture = self._texture(cols, rows, 0, _D3D11_BIND_RENDER_TARGET, 0)
            view = _VP()
            hr = _com(device, 9, _HR, _VP, _VP, ctypes.POINTER(_VP))(
                device, texture, None, ctypes.byref(view))      # CreateRenderTargetView
            self._target = (texture, view)
            _check(hr, "ID3D11Device::CreateRenderTargetView")
            self._stage = self._texture(cols, rows, _D3D11_USAGE_STAGING, 0,
                                        _D3D11_CPU_ACCESS_READ)
            self._stage_rows, self._stride = rows, 1

            # Nothing else draws on this context, so the pipeline is set once.
            vertex, pixel = self._shaders
            viewport = _D3D11_VIEWPORT(0.0, 0.0, cols, rows, 0.0, 1.0)
            _com(context, 17, None, _VP)(context, None)                         # IASetInputLayout
            _com(context, 24, None, wintypes.UINT)(
                context, _D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST)                # IASetPrimitiveTopology
            _com(context, 11, None, _VP, _VP, wintypes.UINT)(context, vertex, None, 0)  # VSSetShader
            _com(context, 9, None, _VP, _VP, wintypes.UINT)(context, pixel, None, 0)    # PSSetShader
            _com(context, 44, None, wintypes.UINT, ctypes.POINTER(_D3D11_VIEWPORT))(
                context, 1, ctypes.byref(viewport))                             # RSSetViewports
            _com(context, 33, None, wintypes.UINT, ctypes.POINTER(_VP), _VP)(
                context, 1, ctypes.byref(view), None)                           # OMSetRenderTargets
        else:
            self._stage = self._texture(width, height, _D3D11_USAGE_STAGING, 0,
                                        _D3D11_CPU_ACCESS_READ)
            self._stage_rows, self._stride = height, self.step

        self._size = (width, height)
        self._rotation = rotation

        # The calls made for every frame, looked up once.
        self._acquire = _com(dup, 8, _HR, wintypes.UINT,
                             ctypes.POINTER(_DXGI_OUTDUPL_FRAME_INFO), ctypes.POINTER(_VP))
        self._release_frame = _com(dup, 14, _HR)
        self._create_view = _com(device, 7, _HR, _VP, _VP, ctypes.POINTER(_VP))
        self._set_view = _com(context, 8, None, wintypes.UINT, wintypes.UINT, ctypes.POINTER(_VP))
        self._draw = _com(context, 13, None, wintypes.UINT, wintypes.UINT)
        self._copy = _com(context, 47, None, _VP, _VP)
        self._map = _com(context, 14, _HR, _VP, wintypes.UINT, wintypes.UINT, wintypes.UINT,
                         ctypes.POINTER(_D3D11_MAPPED_SUBRESOURCE))
        self._unmap = _com(context, 15, None, _VP, wintypes.UINT)

    def _close_dxgi(self, device=False):
        if self._context:
            _com(self._context, 110, None)(self._context)       # ClearState: unbind the target
        if self._target is not None:
            for obj in self._target:
                _com_release(obj)
        _com_release(self._stage)
        _com_release(self._dup)
        self._target = self._stage = self._dup = None
        if device:
            for obj in (self._shaders or ()) + (self._output, self._context, self._device):
                _com_release(obj)
            self._shaders = self._output = self._context = self._device = None

    # -- capture ----------------------------------------------------------

    def grab(self, wait=0.0):
        if self._sct is not None:
            if wait > 0:
                time.sleep(wait)
            shot = self._sct.grab(self._bbox)
            bgra = np.frombuffer(shot.bgra, dtype=np.uint8).reshape(shot.height, shot.width, 4)
            return np.ascontiguousarray(bgra[::self.step, ::self.step, 2::-1])

        if self._dup is None:
            now = time.perf_counter()
            if now < self._retry_at:
                return self.LOST
            try:
                self._open()
            except Exception as exc:
                # The secure desktop refuses for as long as it is up, and the
                # device is fine. Anything else -- the driver was reset, the
                # monitor was switched off and on again -- needs the device
                # and output found again, which is worth trying only now and
                # then: a new device costs ~35 ms of CPU.
                rebuild = (getattr(exc, "hresult", None) != _E_ACCESSDENIED
                           and now >= self._rebuild_at)
                if rebuild:
                    self._rebuild_at = now + 10.0
                self._close_dxgi(device=rebuild)
                self._retry_at = now + CAPTURE_RETRY_INTERVAL
                return self.LOST
        try:
            return self._grab_dxgi(wait)
        except Exception:
            # Almost always DXGI_ERROR_ACCESS_LOST: a mode change, a game going
            # exclusive full screen, or the secure desktop. Re-open on a
            # schedule; the first attempt waits out the mode change.
            self._close_dxgi()
            self._retry_at = time.perf_counter() + 0.1
            return self.LOST

    def _grab_dxgi(self, wait):
        info = self._info
        resource = _VP()
        deadline = time.perf_counter() + wait
        while True:
            left = deadline - time.perf_counter()
            hr = self._acquire(self._dup, int(left * 1000) if left > 0 else 0,
                               ctypes.byref(info), ctypes.byref(resource))
            if hr & 0xFFFFFFFF == _DXGI_ERROR_WAIT_TIMEOUT:
                return None
            _check(hr, "IDXGIOutputDuplication::AcquireNextFrame")
            if info.LastPresentTime != 0:
                break
            # No desktop update, only the pointer. That includes the very
            # first frame of a new duplication, whose texture is still empty
            # -- copying it would turn the LEDs black until the screen moves.
            _com_release(resource)
            self._release_frame(self._dup)
            left = deadline - time.perf_counter()
            if left <= 0:
                return None
            # A moving pointer delivers these at the refresh rate; wait them
            # out in one sleep, not one wake-up each.
            time.sleep(left)
            deadline = 0.0

        context, stage = self._context, self._stage
        try:
            texture = _com_query(resource, _IID_ID3D11Texture2D, "ID3D11Texture2D")
            try:
                if self._shaders is None:
                    self._copy(context, stage, texture)
                else:
                    view = _VP()
                    try:
                        _check(self._create_view(self._device, texture, None,
                                                 ctypes.byref(view)),
                               "ID3D11Device::CreateShaderResourceView")
                        self._set_view(context, 0, 1, ctypes.byref(view))
                        self._draw(context, 3, 0)
                        self._set_view(context, 0, 1, ctypes.byref(self._no_view))
                    except CaptureError:
                        # This driver will not sample the desktop texture:
                        # reopen reading full frames, and stay that way.
                        self._use_shader = False
                        self._rebuild_at = 0.0
                        self._close_dxgi(device=True)
                        return self.LOST
                    finally:
                        _com_release(view)
                    self._copy(context, stage, self._target[0])
            finally:
                _com_release(texture)
        finally:
            _com_release(resource)
            if self._dup:
                self._release_frame(self._dup)

        mapped = self._mapped
        _check(self._map(context, stage, 0, _D3D11_MAP_READ, 0, ctypes.byref(mapped)),
               "ID3D11DeviceContext::Map")                      # waits for the copy
        try:
            # The copy has to be taken before the texture is unmapped: after
            # that the memory behind `mapped` is gone.
            rows, pitch, stride = self._stage_rows, mapped.RowPitch, self._stride
            raw = (ctypes.c_ubyte * (pitch * rows)).from_address(mapped.pData)
            bgra = np.frombuffer(raw, dtype=np.uint8).reshape(rows, pitch // 4, 4)
            columns = self._size[0] if stride > 1 else len(range(0, self._size[0], self.step))
            small = np.ascontiguousarray(bgra[::stride, :columns:stride, 2::-1])
        finally:
            self._unmap(context, stage, 0)

        if self._rotation == 90:
            small = np.ascontiguousarray(np.rot90(small, axes=(1, 0)))
        elif self._rotation == 180:
            small = np.ascontiguousarray(np.rot90(small, k=2, axes=(0, 1)))
        elif self._rotation == 270:
            small = np.ascontiguousarray(np.rot90(small, axes=(0, 1)))
        return small

    def close(self):
        self._close_dxgi(device=True)
        if self._sct is not None:
            self._sct.close()
            self._sct = None


# ---------------------------------------------------------------------------
#  Frame preprocessing
# ---------------------------------------------------------------------------




def extract_edges(frame, ratio=EDGE_RATIO):
    h, w = frame.shape[:2]
    bh, bw = max(1, int(h * ratio)), max(1, int(w * ratio))
    return np.concatenate([
        frame[:bh, :].reshape(-1, 3),
        frame[-bh:, :].reshape(-1, 3),
        frame[bh:-bh, :bw].reshape(-1, 3),
        frame[bh:-bh, -bw:].reshape(-1, 3),
    ], axis=0)


def trim_letterbox(frame, threshold=BLACK_THRESHOLD, row_ratio=BLACK_ROW_RATIO):
    h = frame.shape[0]
    # One pass over all rows instead of a numpy call per row from each end;
    # elementwise maximum beats a max() reduction over a 3-wide axis.
    brightness = np.maximum(np.maximum(frame[:, :, 0], frame[:, :, 1]), frame[:, :, 2])
    lit = np.count_nonzero(brightness > threshold, axis=1) / frame.shape[1] > (1 - row_ratio)
    rows = np.flatnonzero(lit)
    if rows.size == 0:
        return frame
    top, bot = int(rows[0]), int(rows[-1]) + 1
    if bot - top < h * 0.3:
        return frame
    return frame[top:bot]


# ---------------------------------------------------------------------------
#  Color math
# ---------------------------------------------------------------------------


def apply_gamma(color, gamma=1.8):
    # Apply gamma to Value (brightness) preserving precise Hue and Saturation
    r, g, b = color
    h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
    v = v ** gamma
    rn, gn, bn = colorsys.hsv_to_rgb(h, s, v)
    return (int(rn * 255), int(gn * 255), int(bn * 255))

def apply_saturation(color, boost=1.5):
    r, g, b = color
    h, s, v = colorsys.rgb_to_hsv(r/255.0, g/255.0, b/255.0)
    s = min(1.0, s * boost)
    rn, gn, bn = colorsys.hsv_to_rgb(h, s, v)
    return (int(rn * 255), int(gn * 255), int(bn * 255))

def get_dominant_color(pixels):
    # Integer maths throughout: chroma x value tops out at 255 * 255, which
    # fits uint16, and the sums are exact -- the same answer the float32
    # version gave, without converting the whole frame first.
    #
    # The channels are split into three contiguous rows first. numpy
    # vectorises a max or a sum over adjacent bytes, not over every third
    # byte of an interleaved frame, and the copy costs less than that gains.
    planes = np.ascontiguousarray(pixels.reshape(-1, 3).T)
    r, g, b = planes
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    score = np.multiply(mx - mn, mx, dtype=np.uint16)

    k = max(1, score.size // 10)
    threshold = np.partition(score, -k)[-k]
    top = np.flatnonzero(score >= threshold)

    n = top.size
    if 10 < n < score.size:
        sr, sg, sb = planes.take(top, axis=1).sum(axis=1, dtype=np.int64).tolist()
    else:
        # Too few vivid pixels to trust, or every pixel tied: average them all.
        n = score.size
        sr, sg, sb = planes.sum(axis=1, dtype=np.int64).tolist()
    dr, dg, db = int(sr / n), int(sg / n), int(sb / n)

    avg = (dr + dg + db) / 3
    dr = min(255, max(0, int(avg + (dr - avg) * 2.5)))
    dg = min(255, max(0, int(avg + (dg - avg) * 2.5)))
    db = min(255, max(0, int(avg + (db - avg) * 2.5)))

    mx_val = max(dr, dg, db)
    if 0 < mx_val < 100:
        s = 100 / mx_val
        dr, dg, db = min(255, int(dr*s)), min(255, int(dg*s)), min(255, int(db*s))

    return dr, dg, db





def color_diff(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2])


def loop_rest(idle_streak, interval, elapsed, still):
    """How the capture loop spends the time until its next frame.

    Returns (sleep, wait) in seconds: `sleep` is slept through, `wait` is
    handed to ScreenGrabber.grab(), which returns the moment Windows has a new
    frame. An idle loop that only sleeps reacts to a picture that starts
    moving up to a whole idle period late; one that waits in grab() reacts at
    once. That is only safe while the desktop is `still` -- no frame came this
    pass -- because a desktop that keeps producing frames of one steady colour
    would end every wait immediately and run the loop flat out.

    The wait is never longer than the sleep it replaces. The smoothing filter
    steps by elapsed time, so the colour is where the sleeping loop would
    have put it at its next poll -- it just starts moving sooner.
    """
    if idle_streak >= DEEP_IDLE_LIMIT:
        rest = DEEP_IDLE_INTERVAL
    elif idle_streak >= IDLE_STREAK_LIMIT:
        rest = interval * IDLE_MULTIPLIER - elapsed
    else:
        return max(0.0, interval - elapsed), 0.0
    return (0.0, max(0.0, rest)) if still else (max(0.0, rest), 0.0)


def smooth_alpha(smoothness, dt):
    """Per-frame EMA factor for a `smoothness` of 1-100 over a `dt`-second step.

    At SMOOTH_REF_FPS this returns the slider value unchanged, so a setting
    keeps the feel it has always had; at any other rate the per-frame step is
    rescaled so raising the capture rate makes the picture fresher without
    silently making the fade snappier too.
    """
    s = max(1, min(100, int(smoothness))) / 100.0
    if s >= 1.0 or dt <= 0.0:
        return 1.0
    return 1.0 - (1.0 - s) ** (dt * SMOOTH_REF_FPS)


def smooth_settle_ms(smoothness):
    """Wall-clock time a `smoothness` setting takes to cover 90% of a step."""
    s = max(1, min(100, int(smoothness))) / 100.0
    if s >= 1.0:
        return 0.0
    return 1000.0 * math.log(0.1) / (SMOOTH_REF_FPS * math.log(1.0 - s))


# ---------------------------------------------------------------------------
#  Async color sender
# ---------------------------------------------------------------------------

ORGB_UPDATE_LEDS = 1050                     # PacketType.RGBCONTROLLER_UPDATELEDS
_ORGB_HEADER = struct.Struct("<4sIII")      # magic, device id, packet type, size
_ORGB_BODY_HEAD = struct.Struct("<IH")      # body size (repeated), led count


# Hardware write durations in milliseconds. Measured on this machine with
# an OpenRGB build that times
# every RGBController::DeviceUpdateLEDs() call -- the SDK cannot show this,
# because an UPDATELEDS packet only raises a flag and returns in ~0.1 ms while
# the device's own thread does the work:
#
#     Roccat Burst Pro           2 ms   one small HID feature report
#     XPG Summoner keyboard     31 ms   two 264-byte feature reports
#     Kingston Fury DDR5 DRAM  140 ms   SMBus writes and the apply handshake
#     MSI MYSTIC LIGHT         185 ms   one 727-byte feature report per used
#                                       header, ~93 ms each, to a low-speed
#                                       USB MCU
#
# Writing the packets for all four costs 0.16 ms together, so none of this is
# transport -- it is what the hardware takes, and it spans two orders of
# magnitude. Keys match case-insensitively as substrings of the device name.
DEVICE_LATENCY_MS = {
    "mystic light": 185.0,
    "fury":         140.0,
    "summoner":      31.0,
    "burst pro":      2.0,
}
DEFAULT_LATENCY_MS = 25.0
MSI_761_ZONE_MS = 92.5

# A device is never sent frames faster than one per (latency x DEVICE_PACE).
# At exactly 1/latency a device transmits 100% of the time, and the
# motherboard, keyboard and mouse share one USB root hub here: with the
# motherboard streaming back to back, the keyboard's own update went from
# 33 ms to 470 ms. Pacing the slow devices just under their ceiling leaves the
# bus idle enough for the fast ones. 1.0 disables the pacing.
DEVICE_PACE = 1.5

# A device that already has the colour is left alone until this much time has
# passed, so a still picture does not keep the bus busy with identical frames.
# It still has to be re-sent eventually: direct mode is a keep-alive protocol
# on some of this hardware -- the XPG Summoner drops its LEDs to black if it
# gets no frame for ~2 s.
DEVICE_KEEPALIVE = 1.0


def device_latency_ms(name, table=None):
    """Latency for an OpenRGB device name, or DEFAULT_LATENCY_MS if unknown."""
    lowered = (name or "").lower()
    for key, ms in (table or DEVICE_LATENCY_MS).items():
        if key in lowered:
            return float(ms)
    return DEFAULT_LATENCY_MS


def device_timing_ms(dev):
    """Return (alignment latency, busy time), based on measured writes.

    The MSI 761 driver sends one report per active header, sequentially. Aim
    the other devices between the first and last header, but pace frames by
    the FULL busy time. A single latency for both purposes either overloads
    the board or aligns everything to its last header only.
    """
    latency = device_latency_ms(getattr(dev, "name", ""))
    description = getattr(getattr(dev, "metadata", None), "description", "")
    if ("mystic light" in getattr(dev, "name", "").lower()
            and "761-byte" in description.lower()):
        active = sum(bool(zone.leds) for zone in dev.zones)
        if active:
            return MSI_761_ZONE_MS * (active + 1) / 2, MSI_761_ZONE_MS * active
    return latency, latency


def build_update_blob(items):
    """Pack one solid-colour UPDATELEDS packet per device into a single buffer.

    `items` is [((device_id, led_count), (r, g, b)), ...] -- one entry per
    device, each with its own colour. Colours go
    out as RGBx to match RGBColor.pack(), and the body repeats its own length
    because the SDK reads that field before the LED array.
    """
    out = []
    for (dev_id, n), (r, g, b) in items:
        size = _ORGB_BODY_HEAD.size + 4 * n
        out.append(_ORGB_HEADER.pack(b"ORGB", dev_id, ORGB_UPDATE_LEDS, size))
        out.append(_ORGB_BODY_HEAD.pack(size, n))
        out.append(bytes((r, g, b, 0)) * n)
    return b"".join(out)


class AsyncColorSender:
    """Pushes the newest colour to every device from a background thread.

    Colours arrive far faster than the hardware needs them, so a device is
    only ever given the most recent one -- intermediate values are dropped,
    never queued.

    The wire format is built here and written with a single sendall() instead
    of looping over Device.set_color(). That call defaults to fast=False,
    which follows every update with a blocking REQUEST_CONTROLLER_DATA round
    trip and a full re-parse of the device descriptor -- then throws the
    result away. Measured across these four devices it costs 2.58 ms per
    colour (p90 8.5 ms, max 17.7 ms) against 0.01 ms for the single write.

    On top of that the worker paces the devices and can align them:

    * Pacing. OpenRGB drives every device on its own thread, so a slow device
      never holds up the others -- but the bus it shares with them can. Each
      device is sent at most one frame per (its latency x DEVICE_PACE).

    * Synchronisation. Freeze ONE colour per hardware frame, then dispatch it
      to each device at (deadline - device latency). All devices share the
      slowest device's cadence. Sampling history independently per device
      cannot align a stream: the mouse otherwise shows colours that the
      motherboard never receives. An in-flight frame is never replaced.
      Hardware timing jitter and sequential zones still limit alignment.
    """

    def __init__(self, client, devices, brightness, keyboard=None, sync=True):
        self._client = client
        self._devices = list(devices)
        self._br = brightness
        self._keyboard = keyboard
        self._sync = bool(sync)

        self._plan = []
        for dev in self._devices:
            self._plan.append(self._entry(dev, (dev.id, len(dev.leds)),
                                          *device_timing_ms(dev)))
        if keyboard is not None and keyboard.available:
            # The direct-HID fallback path: same keyboard, same cost.
            self._plan.append(self._entry(None, None, device_latency_ms("summoner")))

        self._slowest = max([e["lat"] for e in self._plan], default=0.0)
        self._frame_period = max([e["period"] for e in self._plan], default=0.0)
        self._frame = None
        self._next_frame_at = 0.0
        self._was_sync = self._sync
        self._latest = None
        self._new_color_event = threading.Event()
        self._stop_ev = threading.Event()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    @staticmethod
    def _entry(dev, spec, latency_ms, busy_ms=None):
        lat = latency_ms / 1000.0
        busy = (latency_ms if busy_ms is None else busy_ms) / 1000.0
        return {"dev": dev, "spec": spec, "lat": lat,
                "period": busy * DEVICE_PACE, "next": 0.0,
                "last_rgb": None, "last_t": 0.0}

    def set_sync(self, enabled):
        enabled = bool(enabled)
        if enabled != self._sync:
            self._sync = enabled
            self._new_color_event.set()

    def send(self, r, g, b):
        self._latest = (time.perf_counter(), (r, g, b))
        self._new_color_event.set()

    def _scale(self, rgb):
        br = self._br
        return (max(0, min(255, int(rgb[0] * br))),
                max(0, min(255, int(rgb[1] * br))),
                max(0, min(255, int(rgb[2] * br))))

    def _send_raw(self, items):
        """Write every due device in one go. Returns 'sent', 'skip' or 'fail'."""
        comms = getattr(self._client, "comms", None)
        sock = getattr(comms, "sock", None)
        if sock is None:
            return "fail"
        # The SDK client multiplexes a single socket and its lock is what stops
        # a concurrent update() from interleaving with our bytes. If someone
        # else holds it, retry this dispatch on a later scheduling step.
        # Falling back to the SDK would block behind the same lock.
        if not comms.lock.acquire(blocking=False):
            return "skip"
        try:
            sock.sendall(build_update_blob(items))
            return "sent"
        except Exception:
            return "fail"
        finally:
            try:
                comms.lock.release()
            except RuntimeError:
                pass

    def _send_via_sdk(self, writes):
        """Fallback for when the raw write fails: still fast=True, no read-back."""
        sent = []
        for dev, rgb in writes:
            try:
                dev.set_color(RGBColor(*rgb), fast=True)
                sent.append(dev)
            except Exception:
                pass
        return sent

    def _dispatch(self, writes):
        """Return successfully dispatched entries so skipped writes get retried."""
        sent = []
        sdk = [(entry, rgb) for entry, rgb in writes if entry["spec"] is not None]
        if sdk:
            result = self._send_raw([(entry["spec"], rgb) for entry, rgb in sdk])
            if result == "sent":
                sent.extend(entry for entry, _ in sdk)
            elif result == "fail":
                devices = self._send_via_sdk([(entry["dev"], rgb) for entry, rgb in sdk])
                sent.extend(entry for entry, _ in sdk
                            if any(entry["dev"] is dev for dev in devices))
        for entry, rgb in writes:
            if entry["spec"] is None and self._keyboard is not None:
                try:
                    self._keyboard.set_color(*rgb)
                    if self._keyboard.available:
                        sent.append(entry)
                except Exception:
                    pass
        return sent

    def _tick(self, now):
        """Run one scheduling step and return the next absolute wake time."""
        # Losing the optional HID keyboard must not hold every other device
        # behind a frame that can never be delivered to it.
        if self._keyboard is not None and not self._keyboard.available:
            self._plan = [e for e in self._plan if e["spec"] is not None]
            if self._frame is not None:
                self._frame["pending"] = [(e, at) for e, at in self._frame["pending"]
                                          if e["spec"] is not None]
                if not self._frame["pending"]:
                    self._frame = None
            self._keyboard = None
        latest = self._latest
        if latest is None or not self._plan:
            return now + DEVICE_KEEPALIVE

        # Finish a cohort even if sync is switched off midway through it.
        # On entry to sync, first drain writes from the independent schedule.
        if self._frame is None and self._sync != self._was_sync:
            self._was_sync = self._sync
            self._next_frame_at = max(now, *(e["next"] for e in self._plan))

        if self._sync or self._frame is not None:
            if self._frame is None:
                if now < self._next_frame_at:
                    return self._next_frame_at
                rgb = self._scale(latest[1])
                refresh_at = min(e["last_t"] + DEVICE_KEEPALIVE for e in self._plan)
                if all(e["last_rgb"] == rgb for e in self._plan) and now < refresh_at:
                    return refresh_at
                self._frame = {"rgb": rgb, "started": False,
                               "pending": [(e, now + self._slowest - e["lat"])
                                           for e in self._plan]}

            frame = self._frame
            if not frame["started"]:
                # If the socket was busy, move the whole unsent cohort forward
                # together, preserving the compensation even on the first frame.
                shift = max(0.0, now - min(at for _, at in frame["pending"]))
                frame["pending"] = [(e, at + shift) for e, at in frame["pending"]]
            writes = [(e, frame["rgb"]) for e, at in frame["pending"] if at <= now]
            sent = self._dispatch(writes)
            if sent and not frame["started"]:
                frame["started"] = True
                self._next_frame_at = now + self._frame_period
            for entry in sent:
                entry["last_rgb"] = frame["rgb"]
                entry["last_t"] = now
                entry["next"] = now + entry["period"]
            sent_ids = {id(e) for e in sent}
            frame["pending"] = [(e, at) for e, at in frame["pending"]
                                if id(e) not in sent_ids]
            if not frame["pending"]:
                self._frame = None
                return self._next_frame_at if self._sync else now
            return min(at if at > now else now + 0.005 for _, at in frame["pending"])

        rgb = self._scale(latest[1])
        writes = [(e, rgb) for e in self._plan if now >= e["next"]
                  and (rgb != e["last_rgb"] or now - e["last_t"] >= DEVICE_KEEPALIVE)]
        for entry in self._dispatch(writes):
            entry["last_rgb"] = rgb
            entry["last_t"] = now
            entry["next"] = now + entry["period"]
        return min(max(now + 0.005, e["next"] if rgb != e["last_rgb"]
                       else e["last_t"] + DEVICE_KEEPALIVE) for e in self._plan)

    def _worker(self):
        poll = DEVICE_KEEPALIVE
        while not self._stop_ev.is_set():
            self._new_color_event.wait(timeout=poll)
            self._new_color_event.clear()

            wake = self._tick(time.perf_counter())
            poll = max(0.001, wake - time.perf_counter())

    def stop(self):
        self._stop_ev.set()
        self._new_color_event.set()         # the worker may be in a long wait
        self._thread.join(timeout=2)


# ---------------------------------------------------------------------------
#  Ambilight controller
# ---------------------------------------------------------------------------

class Ambilight:
    def __init__(self, fps=15, brightness=100,
                 connect_timeout=OPENRGB_CONNECT_TIMEOUT, status_cb=None,
                 sync_devices=True, lang="auto"):
        import time as _time
        self.lang = resolve_language(lang)
        self.client = self._connect(timeout=connect_timeout, status_cb=status_cb, lang=self.lang)
        self.devices = self._wait_for_devices(status_cb=status_cb, lang=self.lang)

        # Direct mode is what makes per-frame colour control possible. set_mode()
        # costs a blocking round trip and, on motherboards and fan hubs, a
        # controller-side switch that swallows colour packets for ~500-1000 ms --
        # so only touch a device that is not already there.
        switched = False
        for dev in self.devices:
            try:
                if 'direct' in dev.modes[dev.active_mode].name.lower():
                    continue
                for i, mode in enumerate(dev.modes):
                    if 'direct' in mode.name.lower():
                        dev.set_mode(i)
                        switched = True
                        break
            except Exception:
                pass

        # Wait out that switch so the capture loop's first colours are not
        # dropped. After the first run every device is already in Direct, and
        # this used to be a flat second of dead time on every single start.
        if switched:
            _time.sleep(1.2)

        self.fps = fps
        self.interval = 1.0 / fps
        self.brightness = max(1, min(100, brightness)) / 100.0
        self.running = False
        self._br = self.brightness
        self._sender = None

        # Share hardware frames and compensate measured device latencies.
        self.sync_devices = bool(sync_devices)

        # XPG Summoner keyboard: stock OpenRGB detects it but cannot drive its
        # lighting (its lighting interface only accepts HID *feature* reports,
        # while OpenRGB sends output reports). The patched OpenRGB in
        # OpenRGB-patched\ (next to this file) does it natively, so we only fall back to driving
        # the keyboard over raw HID when OpenRGB is not already exposing it --
        # otherwise both writers hammer the same feature report. See the
        # XPG Summoner section at the top of this file.
        self.keyboard = None
        if not self._openrgb_has_keyboard():
            try:
                self.keyboard = XPGSummonerKeyboard()
            except Exception:
                self.keyboard = None

    def _openrgb_has_keyboard(self):
        for dev in getattr(self, "devices", []):
            if "summoner" in dev.name.lower():
                return True
        return False

    @staticmethod
    def _connect(timeout=OPENRGB_CONNECT_TIMEOUT, status_cb=None, lang="auto"):
        """Connect to the OpenRGB SDK server, waiting for it to come up.

        A single attempt fails after ~1 s if nothing is listening yet, which is
        the normal case right after logon -- so retry until `timeout` instead of
        giving up on the first refused connection. Raises OpenRGBUnavailable
        (never exits the process) so the GUI can report it and try again.
        """
        import socket
        deadline = time.time() + max(0.0, timeout)
        last_err = None
        last_notice = 0.0
        while True:
            try:
                client = OpenRGBClient(OPENRGB_HOST, OPENRGB_PORT, name="Ambilight")
                try:
                    client.comms.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                except Exception:
                    pass
                return client
            except Exception as exc:
                last_err = exc
            remaining = deadline - time.time()
            if remaining <= 0:
                raise OpenRGBUnavailable(
                    tr(lang, "err_sdk_unreachable", host=OPENRGB_HOST, port=OPENRGB_PORT)
                ) from last_err
            now = time.time()
            if now - last_notice >= 5.0:
                last_notice = now
                _notify(status_cb,
                        tr(lang, "status_waiting_server", sec=int(remaining)))
            time.sleep(min(1.0, remaining))

    def _wait_for_devices(self, timeout=OPENRGB_DEVICE_TIMEOUT, status_cb=None, lang=None):
        """Wait until OpenRGB has finished enumerating its devices.

        The SDK server accepts connections *before* detection is done (SMBus /
        DRAM probing takes seconds), so an early client sees an empty or partial
        device list. Poll until the count holds steady at a non-zero value.

        Two samples 150 ms apart is a stricter steadiness test than the single
        500 ms one it replaces, and it cuts the floor on a start where detection
        had already finished from 500 ms to 300 ms.
        """
        POLL = 0.15
        STABLE_SAMPLES = 2
        ui_lang = getattr(self, "lang", "en") if lang is None else lang
        deadline = time.time() + max(0.0, timeout)
        devices = list(self.client.devices)
        stable = 0
        notified = None
        while time.time() < deadline:
            if devices and stable >= STABLE_SAMPLES:
                break
            if notified != len(devices):
                notified = len(devices)
                _notify(status_cb,
                        tr(ui_lang, "status_waiting_devices", count=len(devices)))
            time.sleep(POLL)
            try:
                self.client.update()
                fresh = list(self.client.devices)
            except Exception:
                # Server went away mid-detection (OpenRGB restarting) -- reconnect.
                self.client = self._connect(timeout=max(1.0, deadline - time.time()),
                                            status_cb=status_cb, lang=ui_lang)
                fresh = list(self.client.devices)
            stable = stable + 1 if len(fresh) == len(devices) else 0
            devices = fresh
        return devices

    def list_devices(self, monitors=None):
        print(tr(self.lang, "cli_list_header", devs=format_device_count(len(self.devices), self.lang)))
        for dev in self.devices:
            print(f"  [{dev.id}] {dev.name}  ({dev.type.name})")
            for z in dev.zones:
                print(tr(self.lang, "cli_list_zone", name=z.name, leds=len(z.leds)))
        if self.keyboard is not None and self.keyboard.available:
            print(tr(self.lang, "cli_list_hid_header"))
            print(tr(self.lang, "cli_list_hid_xpg"))
        if monitors:
            print(tr(self.lang, "cli_list_monitors"))
            for m in monitors:
                print(f"  [{m['id']}] {m['width']}x{m['height']}")
        print()

    def set_color(self, r, g, b):
        if self._sender:
            self._sender.send(r, g, b)
        else:
            color = RGBColor(int(r*self._br), int(g*self._br), int(b*self._br))
            for dev in self.devices:
                try:
                    dev.set_color(color, fast=True)
                except Exception:
                    pass
            if self.keyboard is not None:
                try:
                    self.keyboard.set_color(int(r*self._br), int(g*self._br), int(b*self._br))
                except Exception:
                    pass

    def restore(self):
        for dev in self.devices:
            try:
                if len(dev.modes) > 0:
                    dev.set_mode(0)
            except Exception:
                pass
        print(tr(self.lang, "cli_restored"))

    def run(self, monitor_index=None, preferred_width=2560,
            edge_only=False, skip_bars=False, smoothness=35, display_sync=True):
        self.running = True
        lower_process_priority()

        monitors = get_monitors()
        if not monitors:
            print(tr(self.lang, "cli_no_monitors"))
            sys.exit(1)

        if monitor_index is not None:
            mon = next((m for m in monitors if m["id"] == monitor_index), monitors[0])
        else:
            mon = find_monitor(monitors, preferred_width)

        grabber = ScreenGrabber(mon)
        self._sender = AsyncColorSender(self.client, self.devices, self._br,
                                        self.keyboard, sync=self.sync_devices)

        FORCE_RESEND_INTERVAL = 1.0
        RAW_WAKE_THRESHOLD = 15

        flags = []
        if edge_only: flags.append(tr(self.lang, "feat_edges"))
        if skip_bars: flags.append(tr(self.lang, "feat_bars"))
        print(tr(self.lang, "cli_started", width=mon["width"], height=mon["height"], backend=grabber.backend), flush=True)
        print(tr(self.lang, "cli_started_meta", count=len(self.devices), fps=self.fps, brightness=int(self.brightness * 100)), flush=True)
        if flags:
            print(tr(self.lang, "cli_started_features", features=", ".join(flags)), flush=True)
        print(tr(self.lang, "cli_started_tuning", smooth=smoothness), flush=True)
        print(tr(self.lang, "cli_ctrl_c"), flush=True)

        prev = None
        prev_raw = None
        raw = None
        sampled = None              # the frame `raw` was computed from
        current_f = None
        lost_since = None
        idle_streak = 0
        force_cycles = 15
        wait = 0.0                  # see loop_rest()
        last_send_time = time.perf_counter()
        last_frame_time = None
        last_gc = time.perf_counter()

        # Startup garbage is permanent; freezing it keeps the periodic
        # collection below from walking the whole object graph every time.
        gc.collect()
        gc.freeze()
        gc.disable()

        try:
            while self.running:
                t0 = time.perf_counter()

                try:
                    frame = grabber.grab(wait)
                except Exception:
                    import traceback
                    traceback.print_exc()
                    time.sleep(1)
                    continue
                if wait:
                    # The rest was taken inside grab(); pace from its return.
                    wait = 0.0
                    t0 = time.perf_counter()

                if frame is ScreenGrabber.LOST:
                    # Lock screen, UAC prompt or a mode change. Give a mode
                    # change time to pass before calling it a lock.
                    if lost_since is None:
                        lost_since = t0
                    elif (display_sync and current_f is not None and sum(current_f) > 0
                            and t0 - lost_since >= CAPTURE_LOST_BLANK_AFTER):
                        self._sender.send(0, 0, 0)
                        current_f = [0.0, 0.0, 0.0]
                        prev = (0, 0, 0)
                    time.sleep(CAPTURE_RETRY_INTERVAL / 2)
                    continue
                lost_since = None

                still = frame is None
                # Most desktop updates -- a blinking caret, a spinner -- miss
                # every sampled pixel, and the same pixels give the same colour.
                if frame is not None and not (sampled is not None
                                              and np.array_equal(frame, sampled)):
                    sampled = pixels = frame
                    if skip_bars:
                        pixels = trim_letterbox(pixels)
                    if edge_only:
                        pixels = extract_edges(pixels)
                    raw = get_dominant_color(pixels)
                elif raw is None:
                    wait = DEEP_IDLE_INTERVAL       # no first frame yet
                    continue
                # else: the screen has not changed -- keep easing towards the
                # colour it already has instead of re-analysing it.

                # Wake-up detection via raw color drift
                raw_tuple = (int(raw[0]), int(raw[1]), int(raw[2]))
                if prev_raw is not None and color_diff(raw_tuple, prev_raw) > RAW_WAKE_THRESHOLD:
                    idle_streak = 0
                prev_raw = raw_tuple

                # Step the filter by real elapsed time, not the nominal interval:
                # idle throttling and slow frames both stretch it, and a filter
                # that ignores that stalls exactly when the screen starts moving.
                now_f = time.perf_counter()
                dt = self.interval if last_frame_time is None else (now_f - last_frame_time)
                last_frame_time = now_f

                if current_f is None:
                    current_f = [float(c) for c in raw]
                else:
                    a = smooth_alpha(smoothness, dt)
                    current_f[0] += (raw[0] - current_f[0]) * a
                    current_f[1] += (raw[1] - current_f[1]) * a
                    current_f[2] += (raw[2] - current_f[2]) * a

                color = (int(current_f[0]), int(current_f[1]), int(current_f[2]))
                diff = 999 if prev is None else color_diff(color, prev)

                # Forced periodic re-send
                now = time.perf_counter()
                force_update = now - last_send_time >= FORCE_RESEND_INTERVAL

                if force_cycles > 0:
                    force_update = True
                    force_cycles -= 1

                if diff > 10 or force_update:
                    self._sender.send(*color)
                    prev = color
                    last_send_time = now
                # A forced re-send is not activity: counting it as such reset
                # the idle streak every second, so a still screen never got to
                # deep idle.
                if diff > 10:
                    idle_streak = 0
                else:
                    idle_streak += 1

                # Adaptive sleep with capped deep idle
                elapsed = time.perf_counter() - t0

                # Collect on a wall clock rather than per idle iteration: the
                # pause used to land immediately before each deep-idle poll,
                # which put it squarely in the wake-up path.
                if now - last_gc >= GC_INTERVAL:
                    last_gc = now
                    gc.collect()

                sleep, wait = loop_rest(idle_streak, self.interval, elapsed, still)
                if sleep > 0:
                    time.sleep(sleep)
        finally:
            gc.unfreeze()
            gc.enable()
            grabber.close()
            if self._sender:
                self._sender.stop()
                self._sender = None
            if self.keyboard is not None:
                self.keyboard.close()

    def stop(self):
        self.running = False

    def test(self):
        colors = [
            (255,0,0,"RED"),(0,255,0,"GREEN"),(0,0,255,"BLUE"),
            (255,255,0,"YELLOW"),(255,0,255,"MAGENTA"),(0,255,255,"CYAN"),
            (255,255,255,"WHITE"),
        ]
        sender = AsyncColorSender(self.client, self.devices, self._br,
                                  self.keyboard, sync=self.sync_devices)
        try:
            time.sleep(0.5)
            for r, g, b, name in colors:
                sender.send(r, g, b)
                print(f"  {name}", flush=True)
                time.sleep(1)
            sender.send(0, 0, 0)
            time.sleep(max(1.0, sender._frame_period + sender._slowest))
        finally:
            sender.stop()
        print(tr(self.lang, "cli_test_done"))


# ---------------------------------------------------------------------------
#  CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Ambilight via OpenRGB")
    parser.add_argument("--list",       action="store_true")
    parser.add_argument("--test",       action="store_true")
    parser.add_argument("--restore",    action="store_true")
    parser.add_argument("--fps",        type=float, default=60)
    parser.add_argument("--monitor",    type=int,   default=None)
    parser.add_argument("--width",      type=int,   default=2560)
    parser.add_argument("--brightness", type=int,   default=100)
    parser.add_argument("--smoothness", type=int,   default=35)
    parser.add_argument("--edges",      action="store_true")
    parser.add_argument("--skip-bars",  action="store_true")
    parser.add_argument("--sync", action=argparse.BooleanOptionalAction, default=True,
                        help="align common frames to measured device latencies "
                             "(default: on; --no-sync uses independent updates)")
    parser.add_argument("--wait",       type=float, default=OPENRGB_CONNECT_TIMEOUT,
                        help="seconds to wait for the OpenRGB SDK server")
    parser.add_argument("--lang",       choices=["auto", "en", "ru"], default=None,
                        help="interface language: auto, en, or ru")
    args = parser.parse_args()

    lang = resolve_language(args.lang if args.lang is not None else _read_config_language())

    logging.getLogger("openrgb").setLevel(logging.CRITICAL)

    print(tr(lang, "cli_connecting"), flush=True)
    try:
        ambi = Ambilight(fps=args.fps, brightness=args.brightness,
                         sync_devices=args.sync,
                         connect_timeout=args.wait,
                         lang=lang)
    except OpenRGBUnavailable as exc:
        print(tr(lang, "cli_error", error=exc))
        sys.exit(1)

    if args.list:
        ambi.list_devices(monitors=get_monitors()); return
    if args.test:
        ambi.test(); return
    if args.restore:
        ambi.restore(); return

    import signal
    def _quit(sig=None, frame=None):
        print(tr(lang, "cli_stopping"), flush=True)
        ambi.stop()
    signal.signal(signal.SIGINT, _quit)
    signal.signal(signal.SIGTERM, _quit)

    ambi.run(monitor_index=args.monitor, preferred_width=args.width,
             edge_only=args.edges, skip_bars=args.skip_bars, smoothness=args.smoothness)


# =========================================================================
#  GUI
# =========================================================================


AUTOSTART_DELAY_MS = 500     # first auto-start attempt after the window is up
AUTOSTART_RETRY_MS = 15000   # re-check while OpenRGB still is not running
COLOR_BAR_REFRESH_MS = 66    # colour bar repaint rate, ~15 Hz


class GlobalHotkey:
    """A system-wide hotkey through RegisterHotKey, with no keyboard hook.

    keyboard.add_hotkey() installs a low-level keyboard hook, so every key
    press on the system detours through this process's Python code before the
    foreground app sees it -- and with the process at below-normal priority
    and EcoQoS, that detour sits in front of every keystroke in a game.
    RegisterHotKey lets Windows match the combination itself and post one
    message when it is pressed. Unlike the hook, Windows then keeps that
    combination from the focused app.

    Raises ValueError for a combination it cannot express and OSError when
    another program already owns it; the caller falls back to the hook.
    """

    _MODIFIERS = {"ctrl": 0x2, "control": 0x2, "alt": 0x1, "shift": 0x4,
                  "win": 0x8, "windows": 0x8, "cmd": 0x8}
    _KEYS = {"space": 0x20, "enter": 0x0D, "tab": 0x09, "esc": 0x1B,
             "escape": 0x1B, "backspace": 0x08, "insert": 0x2D, "delete": 0x2E,
             "home": 0x24, "end": 0x23, "page up": 0x21, "page down": 0x22,
             "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
             "pause": 0x13, "print screen": 0x2C, "scroll lock": 0x91}
    MOD_NOREPEAT = 0x4000
    WM_HOTKEY = 0x0312
    WM_QUIT = 0x0012

    @classmethod
    def parse(cls, combo):
        """keyboard-style "ctrl+alt+a" -> (modifier flags, virtual key)."""
        mods, vk = 0, None
        for part in combo.lower().split("+"):
            name = part.strip()
            for side in ("left ", "right "):
                if name.startswith(side) and name[len(side):] in cls._MODIFIERS:
                    name = name[len(side):]
            if name in cls._MODIFIERS:
                mods |= cls._MODIFIERS[name]
            elif vk is not None:
                raise ValueError("more than one non-modifier key in %r" % combo)
            elif len(name) == 1 and name.isascii() and name.isalnum():
                vk = ord(name.upper())
            elif name[:1] == "f" and name[1:].isdigit() and 1 <= int(name[1:]) <= 24:
                vk = 0x6F + int(name[1:])
            elif name in cls._KEYS:
                vk = cls._KEYS[name]
            else:
                raise ValueError("no virtual key for %r" % name)
        if vk is None:
            raise ValueError("no key in %r" % combo)
        return mods, vk

    def __init__(self, combo, callback):
        self._mods, self._vk = self.parse(combo)
        self._callback = callback
        self._tid = None
        self._registered = False
        ready = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(ready,), daemon=True)
        self._thread.start()
        ready.wait(2.0)
        if not self._registered:
            raise OSError("RegisterHotKey refused %r" % combo)

    def _run(self, ready):
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32")
        self._tid = ctypes.WinDLL("kernel32").GetCurrentThreadId()
        # Registering with no window ties the hotkey to this thread's queue.
        self._registered = bool(user32.RegisterHotKey(
            None, 1, self._mods | self.MOD_NOREPEAT, self._vk))
        ready.set()
        if not self._registered:
            return
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == self.WM_HOTKEY:
                    try:
                        self._callback()
                    except Exception:
                        pass
        finally:
            user32.UnregisterHotKey(None, 1)

    def stop(self):
        if self._tid is not None:
            ctypes.WinDLL("user32").PostThreadMessageW(self._tid, self.WM_QUIT, 0, 0)
            self._thread.join(timeout=1.0)


class AmbilightGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Ambilight")
        self.root.resizable(False, False)
        self.root.configure(bg="#1a1a2e")

        try:
            from PIL import Image, ImageDraw, ImageTk
            import ctypes
            
            # Detach script icon from python.exe default feather icon
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('ambilight.openrgb.desktop.app')

            icon_img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
            d = ImageDraw.Draw(icon_img)
            d.ellipse((3, 3, 61, 61), fill=(26, 26, 46, 255))
            d.ellipse((14, 14, 50, 50), fill=(233, 69, 96, 255))
            self.tk_icon = ImageTk.PhotoImage(icon_img)
            self.root.iconphoto(True, self.tk_icon)
        except Exception:
            pass

        self.ambilight = None
        self.thread = None
        self.running = False
        self._connecting = False    # a connect thread is in flight
        self._start_token = 0       # bumped on stop, invalidates that thread
        self._autostart_done = False
        self.monitors = get_monitors()

        self.config = {
            "language": "auto",
            "monitor": 0,
            "fps": "60",
            "brightness": 100,
            "smoothness": 55,
            "min_brightness": 0,
            "black_threshold": 20,
            "edges": False,
            "bars": False,
            "saturation": False,
            "gamma": False,
            "tray": False,
            "audio": False,
            "autostart": False,
            "autorun": False,
            "algo": "Dominant",
            "hdr": False,
            "hotkey": "None",
            "schedule_start_enabled": False,
            "schedule_start_h": "18",
            "schedule_start_m": "00",
            "schedule_stop_enabled": False,
            "schedule_stop_h": "02",
            "schedule_stop_m": "00"
        }
        
        self.live_fps = 60.0
        self.live_bright = 100
        self.live_smooth = 55
        self.live_min_bright = 0
        self.live_black_thresh = 20
        self.live_edges = False
        self.live_bars = False
        self.live_sat = False
        self.live_gamma = False
        self.live_hdr = False
        self.live_algo = "Dominant"
        self.live_tray = False
        self.live_audio = False
        self.live_sync = True
        self.live_display_sync = True

        # The capture thread drops the last colour sent here; the Tk thread
        # paints it (see _refresh_color_bar).
        self._bar_color = None
        self._bar_shown = None
        self._bar_job = None
        self._hotkey = None
        self._loading_ui = False
        self._recording_hotkey = False
        self._status_state = ("status_stopped", {})
        self._raw_status_text = None

        self._load_config()
        self.lang_setting = normalize_language(self.config.get("language", "auto"))
        cli_lang = _parse_lang_arg(sys.argv[1:])
        if cli_lang is not None:
            self.lang_setting = normalize_language(cli_lang)
        self.lang = resolve_language(self.lang_setting)
        self._hotkey_value = self.config.get("hotkey", "None") or "None"
        if self.config.get("algo") not in ALGO_KEYS:
            self.config["algo"] = "Dominant"
        self.live_algo = self.config["algo"]

        self._build_ui()
        self._center_window()

        self._kb_hooked = False     # the keyboard-hook fallback is in use
        self._bind_hotkey(self._hotkey_value)

    def _t(self, key, **kwargs):
        return tr(self.lang, key, **kwargs)

    def _set_status(self, key, **kwargs):
        self._status_state = (key, kwargs)
        self._raw_status_text = None
        if hasattr(self, "status_var"):
            self.status_var.set(self._t(key, **kwargs))

    def _refresh_status_text(self):
        if not hasattr(self, "status_var"):
            return
        if self.running:
            devs = len(self.ambilight.devices) if self.ambilight else 0
            feats = []
            if self.live_edges: feats.append(self._t("feat_edges"))
            if self.live_bars: feats.append(self._t("feat_bars"))
            if self.live_audio: feats.append(self._t("feat_audio"))
            extra = f"  •  {', '.join(feats)}" if feats else ""
            self.status_var.set(self._t("status_running", devs=format_device_count(devs, self.lang), extra=extra))
        elif self._raw_status_text is not None:
            self.status_var.set(self._raw_status_text)
        else:
            key, kwargs = self._status_state
            self.status_var.set(self._t(key, **kwargs))

    def _format_hotkey_label(self):
        if self._recording_hotkey:
            return self._t("hotkey_recording")
        if not self._hotkey_value or self._hotkey_value == "None":
            return self._t("hotkey_none")
        return self._hotkey_value

    def _bind_hotkey(self, hk):
        """Make `hk` toggle Start/Stop, replacing whatever was bound before."""
        if self._hotkey is not None:
            self._hotkey.stop()
            self._hotkey = None
        if self._kb_hooked:
            # unhook_all() starts keyboard's hook if it is not running yet, so
            # only call it when the fallback really was used.
            try:
                import keyboard
                keyboard.unhook_all()
            except Exception:
                pass
            self._kb_hooked = False
        if not hk or hk == "None":
            return
        try:
            self._hotkey = GlobalHotkey(hk, lambda: self.root.after(0, self._toggle))
            return
        except Exception:
            pass
        try:
            import keyboard
            keyboard.add_hotkey(hk, self._toggle, suppress=False)
            self._kb_hooked = True
        except Exception:
            pass

    def _update_smooth_hint(self):
        """Spell the smoothing setting out in milliseconds.

        The number itself is an EMA weight and means nothing on its own; what a
        user is choosing is how long a colour takes to arrive. Since the filter
        is frame-rate independent now, that time is a property of the setting
        alone and can be shown honestly.
        """
        if not hasattr(self, "smooth_hint_var"):
            return
        try:
            self.smooth_hint_var.set(
                self._t("smooth_hint", val=self.live_smooth,
                        ms=round(smooth_settle_ms(self.live_smooth))))
        except Exception:
            pass

    def _on_language_change(self, *args):
        if self._loading_ui:
            return
        idx = self.lang_combo.current()
        code = LANG_CODES[idx] if 0 <= idx < len(LANG_CODES) else "auto"
        self.lang_setting = code
        self.lang = resolve_language(code)
        self.config["language"] = code
        self._apply_language()
        self._save_config()

    def _on_setting_change(self, *args):
        if self._loading_ui:
            return
        try:
            val = float(self.fps_var.get())
            if 0 < val <= 240:
                self.live_fps = val
        except ValueError:
            pass
        try:
            self.live_bright = max(1, min(100, int(self.bright_var.get())))
        except (ValueError, tk.TclError):
            pass
        try:
            self.live_smooth = max(1, min(100, int(self.smooth_var.get())))
            self._update_smooth_hint()
        except (ValueError, tk.TclError):
            pass
        try:
            self.live_min_bright = max(0, min(100, int(self.min_bright_var.get())))
        except (ValueError, tk.TclError):
            pass
        try:
            self.live_black_thresh = max(1, min(100, int(self.black_thresh_var.get())))
        except (ValueError, tk.TclError):
            pass
        self.live_edges = self.edge_var.get()
        self.live_bars = self.bars_var.get()
        self.live_sat = self.sat_var.get()
        self.live_gamma = self.gamma_var.get()
        self.live_hdr = self.hdr_var.get()
        algo_idx = self.algo_combo.current()
        if 0 <= algo_idx < len(ALGO_KEYS):
            self.live_algo = ALGO_KEYS[algo_idx]
        self.live_tray = self.tray_var.get()
        self.live_audio = self.audio_var.get()
        self.live_sync = self.sync_var.get()
        self.live_display_sync = self.display_sync_var.get()
        if self.running:
            self._refresh_status_text()
        self._save_config()

    def _load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    self.config.update(json.load(f))
            except Exception:
                pass

    def _save_config(self):
        try:
            self.config["language"] = self.lang_setting
            self.config["monitor"] = self.monitor_combo.current()
            self.config["fps"] = self.fps_var.get()
            self.config["brightness"] = self.live_bright
            self.config["smoothness"] = self.live_smooth
            self.config["min_brightness"] = self.live_min_bright
            self.config["black_threshold"] = self.live_black_thresh
            self.config["edges"] = self.edge_var.get()
            self.config["bars"] = self.bars_var.get()
            self.config["saturation"] = self.sat_var.get()
            self.config["gamma"] = self.gamma_var.get()
            self.config["hdr"] = self.hdr_var.get()
            self.config["algo"] = self.live_algo
            self.config["tray"] = self.tray_var.get()
            self.config["audio"] = self.audio_var.get()
            self.config["display_sync"] = self.display_sync_var.get()
            self.config["sync_devices"] = self.sync_var.get()
            self.config["autostart"] = self.auto_var.get()
            self.config["autorun"] = self.autorun_var.get()
            self.config["hotkey"] = self._hotkey_value
            self.config["schedule_start_enabled"] = self.sched_start_var.get()
            self.config["schedule_start_h"] = self.sched_start_h_var.get()
            self.config["schedule_start_m"] = self.sched_start_m_var.get()
            self.config["schedule_stop_enabled"] = self.sched_stop_var.get()
            self.config["schedule_stop_h"] = self.sched_stop_h_var.get()
            self.config["schedule_stop_m"] = self.sched_stop_m_var.get()
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.config, f, ensure_ascii=False)
        except Exception:
            pass

    def _toggle_autostart(self, *args):
        import os, sys, winreg
        key = winreg.HKEY_CURRENT_USER
        path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        app_name = "MSI_Ambilight"
        try:
            reg = winreg.ConnectRegistry(None, key)
            k = winreg.OpenKey(reg, path, 0, winreg.KEY_ALL_ACCESS)
            if self.auto_var.get():
                exe = sys.executable.replace("python.exe", "pythonw.exe")
                cmd = f'"{exe}" "{os.path.abspath(__file__)}" --startup'
                winreg.SetValueEx(k, app_name, 0, winreg.REG_SZ, cmd)
            else:
                try: winreg.DeleteValue(k, app_name)
                except FileNotFoundError: pass
            winreg.CloseKey(k)
        except Exception:
            pass
        self._save_config()

    def _record_hotkey(self):
        self._recording_hotkey = True
        self.hotkey_var.set(self._format_hotkey_label())
        self.hotkey_btn.config(bg="#e94560", fg="#ffffff")
        self.root.update()
        # A registered hotkey is swallowed by Windows; let the recorder see it.
        if self._hotkey is not None:
            self._hotkey.stop()
            self._hotkey = None

        def listen():
            try:
                import keyboard
                hk = keyboard.read_hotkey(suppress=False)
                self.root.after(0, self._set_hotkey, hk)
            except Exception:
                self.root.after(0, self._set_hotkey, "None")
                
        threading.Thread(target=listen, daemon=True).start()

    def _set_hotkey(self, hk):
        self._recording_hotkey = False
        self.hotkey_btn.config(bg="#16213e", fg="#4ecca3")
        if hk and "unknown" not in hk.lower():
            self._hotkey_value = hk
            self.config["hotkey"] = hk
            self.hotkey_var.set(self._format_hotkey_label())
            self._bind_hotkey(hk)
            self._save_config()
        else:
            self._hotkey_value = self.config.get("hotkey", "None") or "None"
            self.hotkey_var.set(self._format_hotkey_label())
            self._bind_hotkey(self._hotkey_value)

    def _apply_language(self):
        """Update all visible UI texts to the currently selected language."""
        self._loading_ui = True
        try:
            self.subtitle_lbl.config(text=self._t("subtitle"))
            self.lang_lbl.config(text=self._t("language_label"))
            lang_labels = [self._t("lang_auto"), self._t("lang_en"), self._t("lang_ru")]
            self.lang_combo.config(values=lang_labels)
            self.lang_combo.current(LANG_CODES.index(self.lang_setting))

            self.display_lbl.config(text=self._t("display_label"))
            mon_idx = self.monitor_combo.current()
            mon_names = [self._t("monitor_item", id=m["id"], width=m["width"], height=m["height"])
                         for m in self.monitors]
            self.monitor_combo.config(values=mon_names)
            if mon_names:
                if mon_idx < 0 or mon_idx >= len(mon_names):
                    mon_idx = self.config.get("monitor", 0)
                    if mon_idx < 0 or mon_idx >= len(mon_names):
                        mon_idx = 0
                self.monitor_combo.current(mon_idx)
            self.display_hint_lbl.config(text=self._t("display_hint"))

            self.fps_lbl.config(text=self._t("fps_label"))
            self.fps_hint_lbl.config(text=self._t("fps_hint"))

            self.algo_lbl.config(text=self._t("algo_label"))
            algo_labels = [self._t(f"algo_{k}") for k in ALGO_KEYS]
            self.algo_combo.config(values=algo_labels)
            algo_idx = ALGO_KEYS.index(self.live_algo) if self.live_algo in ALGO_KEYS else 0
            self.algo_combo.current(algo_idx)
            self.algo_hint_lbl.config(text=self._t("algo_hint"))

            self.bright_lbl.config(text=self._t("bright_label"))
            self.bright_hint_lbl.config(text=self._t("bright_hint"))

            self.smooth_lbl.config(text=self._t("smooth_label"))
            self._update_smooth_hint()

            self.min_bright_lbl.config(text=self._t("min_bright_label"))
            self.min_bright_hint_lbl.config(text=self._t("min_bright_hint"))

            self.black_thresh_lbl.config(text=self._t("black_thresh_label"))
            self.black_thresh_hint_lbl.config(text=self._t("black_thresh_hint"))

            self.hotkey_lbl.config(text=self._t("hotkey_label"))
            self.hotkey_var.set(self._format_hotkey_label())
            self.hotkey_hint_lbl.config(text=self._t("hotkey_hint"))

            self.sync_cb.config(text=self._t("sync_cb"))
            self.sync_hint_lbl.config(text=self._t("sync_hint"))

            self.display_sync_cb.config(text=self._t("display_sync_cb"))
            self.display_sync_hint_lbl.config(text=self._t("display_sync_hint"))

            self.edge_cb.config(text=self._t("edges_cb"))
            self.edge_hint_lbl.config(text=self._t("edges_hint"))

            self.bars_cb.config(text=self._t("bars_cb"))
            self.bars_hint_lbl.config(text=self._t("bars_hint"))

            self.sat_cb.config(text=self._t("sat_cb"))
            self.sat_hint_lbl.config(text=self._t("sat_hint"))

            self.gamma_cb.config(text=self._t("gamma_cb"))
            self.gamma_hint_lbl.config(text=self._t("gamma_hint"))

            self.hdr_cb.config(text=self._t("hdr_cb"))
            self.hdr_hint_lbl.config(text=self._t("hdr_hint"))

            self.tray_cb.config(text=self._t("tray_cb"))
            self.tray_hint_lbl.config(text=self._t("tray_hint"))

            self.auto_cb.config(text=self._t("autostart_cb"))
            self.auto_hint_lbl.config(text=self._t("autostart_hint"))

            self.autorun_cb.config(text=self._t("autorun_cb"))
            self.autorun_hint_lbl.config(text=self._t("autorun_hint"))

            self.audio_cb.config(text=self._t("audio_cb"))
            self.audio_hint_lbl.config(text=self._t("audio_hint"))

            self.sched_start_cb.config(text=self._t("sched_start_cb"))
            self.sched_start_hint_lbl.config(text=self._t("sched_start_hint"))

            self.sched_stop_cb.config(text=self._t("sched_stop_cb"))
            self.sched_stop_hint_lbl.config(text=self._t("sched_stop_hint"))

            self.btn.config(text=self._t("btn_stop") if self.running else self._t("btn_start"))
            self._refresh_status_text()
        finally:
            self._loading_ui = False
        self.root.update_idletasks()

    def _build_ui(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Title.TLabel", background="#1a1a2e", foreground="#e94560", font=("Segoe UI", 18, "bold"))
        style.configure("Sub.TLabel", background="#1a1a2e", foreground="#8a8a9a", font=("Segoe UI", 9))
        style.configure("SmallSub.TLabel", background="#1a1a2e", foreground="#666688", font=("Segoe UI", 8, "italic"))
        style.configure("Field.TLabel", background="#1a1a2e", foreground="#c4c4d4", font=("Segoe UI", 10))
        style.configure("Status.TLabel", background="#1a1a2e", foreground="#4ecca3", font=("Segoe UI", 10, "bold"))
        style.configure("Dark.TCheckbutton", background="#1a1a2e", foreground="#c4c4d4", font=("Segoe UI", 10))
        style.map("Dark.TCheckbutton", background=[("active", "#1a1a2e")])

        main_container = tk.Frame(self.root, bg="#1a1a2e", padx=30, pady=20)
        main_container.pack(fill="both", expand=True)

        header_frame = tk.Frame(main_container, bg="#1a1a2e")
        header_frame.pack(fill="x")

        title_frame = tk.Frame(header_frame, bg="#1a1a2e")
        title_frame.pack(side="left", anchor="w")
        ttk.Label(title_frame, text="⬤ Ambilight", style="Title.TLabel").pack(anchor="w")
        self.subtitle_lbl = ttk.Label(title_frame, text="", style="Sub.TLabel")
        self.subtitle_lbl.pack(anchor="w", pady=(0, 20))

        lang_frame = tk.Frame(header_frame, bg="#1a1a2e")
        lang_frame.pack(side="right", anchor="ne", pady=(6, 0))
        self.lang_lbl = ttk.Label(lang_frame, text="", style="Field.TLabel")
        self.lang_lbl.pack(side="left", padx=(0, 8))
        self.lang_var = tk.StringVar()
        self.lang_combo = ttk.Combobox(lang_frame, textvariable=self.lang_var, state="readonly", width=12, font=("Segoe UI", 9))
        self.lang_combo.pack(side="left")
        self.lang_combo.bind("<<ComboboxSelected>>", self._on_language_change)

        content_frame = tk.Frame(main_container, bg="#1a1a2e")
        content_frame.pack(fill="both", expand=True)

        left_col = tk.Frame(content_frame, bg="#1a1a2e")
        left_col.pack(side="left", fill="both", expand=True, padx=(0, 15))

        right_col = tk.Frame(content_frame, bg="#1a1a2e")
        right_col.pack(side="left", fill="both", expand=True, padx=(15, 0))

        # -- Monitor
        self.display_lbl = ttk.Label(left_col, text="", style="Field.TLabel")
        self.display_lbl.pack(anchor="w", pady=(0, 4))
        self.monitor_var = tk.StringVar()
        self.monitor_combo = ttk.Combobox(left_col, textvariable=self.monitor_var, state="readonly", width=30, font=("Segoe UI", 10))
        self.monitor_combo.pack(anchor="w", pady=(0, 2), ipady=4)
        self.monitor_combo.bind("<<ComboboxSelected>>", self._on_setting_change)
        self.display_hint_lbl = ttk.Label(left_col, text="", style="SmallSub.TLabel", wraplength=350, justify="left")
        self.display_hint_lbl.pack(anchor="w", pady=(0, 16))

        # -- FPS
        self.fps_lbl = ttk.Label(left_col, text="", style="Field.TLabel")
        self.fps_lbl.pack(anchor="w", pady=(0, 4))
        self.fps_var = tk.StringVar(value=str(self.config.get("fps", "60")))
        self.fps_entry = tk.Entry(left_col, textvariable=self.fps_var, bg="#16213e", fg="#ffffff", insertbackground="#ffffff", font=("Segoe UI", 10), width=10, relief="flat", bd=0, highlightthickness=1, highlightbackground="#333355", highlightcolor="#4ecca3")
        self.fps_entry.pack(anchor="w", pady=(0, 2), ipady=6, ipadx=6)
        self.fps_hint_lbl = ttk.Label(left_col, text="", style="SmallSub.TLabel", wraplength=350, justify="left")
        self.fps_hint_lbl.pack(anchor="w", pady=(0, 16))

        # -- Scanning Algorithm
        self.algo_lbl = ttk.Label(left_col, text="", style="Field.TLabel")
        self.algo_lbl.pack(anchor="w", pady=(0, 4))
        self.algo_var = tk.StringVar()
        self.algo_combo = ttk.Combobox(left_col, textvariable=self.algo_var, state="readonly", width=30, font=("Segoe UI", 10))
        self.algo_combo.pack(anchor="w", pady=(0, 2), ipady=4)
        self.algo_combo.bind("<<ComboboxSelected>>", self._on_setting_change)
        self.algo_hint_lbl = ttk.Label(left_col, text="", style="SmallSub.TLabel", wraplength=350, justify="left")
        self.algo_hint_lbl.pack(anchor="w", pady=(0, 16))

        # -- Brightness
        self.bright_lbl = ttk.Label(left_col, text="", style="Field.TLabel")
        self.bright_lbl.pack(anchor="w", pady=(0, 4))
        self.bright_var = tk.StringVar(value=str(self.config.get("brightness", 100)))
        self.bright_entry = tk.Entry(left_col, textvariable=self.bright_var, bg="#16213e", fg="#ffffff", insertbackground="#ffffff", font=("Segoe UI", 10), width=10, relief="flat", bd=0, highlightthickness=1, highlightbackground="#333355", highlightcolor="#4ecca3")
        self.bright_entry.pack(anchor="w", pady=(0, 2), ipady=6, ipadx=6)
        self.bright_hint_lbl = ttk.Label(left_col, text="", style="SmallSub.TLabel", wraplength=350, justify="left")
        self.bright_hint_lbl.pack(anchor="w", pady=(0, 16))

        # -- Smoothness
        self.smooth_lbl = ttk.Label(left_col, text="", style="Field.TLabel")
        self.smooth_lbl.pack(anchor="w", pady=(0, 4))
        self.smooth_var = tk.StringVar(value=str(self.config.get("smoothness", 55)))
        self.smooth_entry = tk.Entry(left_col, textvariable=self.smooth_var, bg="#16213e", fg="#ffffff", insertbackground="#ffffff", font=("Segoe UI", 10), width=10, relief="flat", bd=0, highlightthickness=1, highlightbackground="#333355", highlightcolor="#4ecca3")
        self.smooth_entry.pack(anchor="w", pady=(0, 2), ipady=6, ipadx=6)
        self.smooth_hint_var = tk.StringVar()
        ttk.Label(left_col, textvariable=self.smooth_hint_var, style="SmallSub.TLabel", wraplength=350, justify="left").pack(anchor="w", pady=(0, 16))

        # -- Min Brightness
        self.min_bright_lbl = ttk.Label(left_col, text="", style="Field.TLabel")
        self.min_bright_lbl.pack(anchor="w", pady=(0, 4))
        self.min_bright_var = tk.StringVar(value=str(self.config.get("min_brightness", 0)))
        self.min_bright_entry = tk.Entry(left_col, textvariable=self.min_bright_var, bg="#16213e", fg="#ffffff", insertbackground="#ffffff", font=("Segoe UI", 10), width=10, relief="flat", bd=0, highlightthickness=1, highlightbackground="#333355", highlightcolor="#4ecca3")
        self.min_bright_entry.pack(anchor="w", pady=(0, 2), ipady=6, ipadx=6)
        self.min_bright_hint_lbl = ttk.Label(left_col, text="", style="SmallSub.TLabel", wraplength=350, justify="left")
        self.min_bright_hint_lbl.pack(anchor="w", pady=(0, 16))

        # -- Black Threshold
        self.black_thresh_lbl = ttk.Label(left_col, text="", style="Field.TLabel")
        self.black_thresh_lbl.pack(anchor="w", pady=(0, 4))
        self.black_thresh_var = tk.StringVar(value=str(self.config.get("black_threshold", 20)))
        self.black_thresh_entry = tk.Entry(left_col, textvariable=self.black_thresh_var, bg="#16213e", fg="#ffffff", insertbackground="#ffffff", font=("Segoe UI", 10), width=10, relief="flat", bd=0, highlightthickness=1, highlightbackground="#333355", highlightcolor="#4ecca3")
        self.black_thresh_entry.pack(anchor="w", pady=(0, 2), ipady=6, ipadx=6)
        self.black_thresh_hint_lbl = ttk.Label(left_col, text="", style="SmallSub.TLabel", wraplength=350, justify="left")
        self.black_thresh_hint_lbl.pack(anchor="w", pady=(0, 16))

        # -- Hotkey
        self.hotkey_lbl = ttk.Label(left_col, text="", style="Field.TLabel")
        self.hotkey_lbl.pack(anchor="w", pady=(0, 4))
        self.hotkey_var = tk.StringVar()
        self.hotkey_btn = tk.Button(left_col, textvariable=self.hotkey_var, command=self._record_hotkey, bg="#16213e", fg="#4ecca3", activebackground="#4ecca3", activeforeground="#1a1a2e", font=("Segoe UI", 10, "bold"), relief="flat", bd=0, highlightthickness=1, highlightbackground="#333355")
        self.hotkey_btn.pack(anchor="w", fill="x", pady=(0, 2), ipady=4)
        self.hotkey_hint_lbl = ttk.Label(left_col, text="", style="SmallSub.TLabel", wraplength=350, justify="left")
        self.hotkey_hint_lbl.pack(anchor="w", pady=(0, 16))

        # -- Device Sync
        self.sync_var = tk.BooleanVar(value=self.config.get("sync_devices", True))
        self.sync_cb = ttk.Checkbutton(left_col, text="", variable=self.sync_var, style="Dark.TCheckbutton")
        self.sync_cb.pack(anchor="w", pady=(0, 2))
        self.sync_hint_lbl = ttk.Label(left_col, text="", style="SmallSub.TLabel", wraplength=350, justify="left")
        self.sync_hint_lbl.pack(anchor="w", pady=(0, 16))

        # -- Display Sync
        self.display_sync_var = tk.BooleanVar(value=self.config.get("display_sync", True))
        self.display_sync_cb = ttk.Checkbutton(left_col, text="", variable=self.display_sync_var, style="Dark.TCheckbutton")
        self.display_sync_cb.pack(anchor="w", pady=(0, 2))
        self.display_sync_hint_lbl = ttk.Label(left_col, text="", style="SmallSub.TLabel", wraplength=350, justify="left")
        self.display_sync_hint_lbl.pack(anchor="w", pady=(0, 16))

        # -- Checkboxes (RIGHT COLUMN)
        self.edge_var = tk.BooleanVar(value=self.config.get("edges", False))
        self.edge_cb = ttk.Checkbutton(right_col, text="", variable=self.edge_var, style="Dark.TCheckbutton")
        self.edge_cb.pack(anchor="w", pady=(0, 2))
        self.edge_hint_lbl = ttk.Label(right_col, text="", style="SmallSub.TLabel", wraplength=360, justify="left")
        self.edge_hint_lbl.pack(anchor="w", pady=(0, 8))

        self.bars_var = tk.BooleanVar(value=self.config.get("bars", False))
        self.bars_cb = ttk.Checkbutton(right_col, text="", variable=self.bars_var, style="Dark.TCheckbutton")
        self.bars_cb.pack(anchor="w", pady=(0, 2))
        self.bars_hint_lbl = ttk.Label(right_col, text="", style="SmallSub.TLabel", wraplength=360, justify="left")
        self.bars_hint_lbl.pack(anchor="w", pady=(0, 16))

        self.sat_var = tk.BooleanVar(value=self.config.get("saturation", False))
        self.sat_cb = ttk.Checkbutton(right_col, text="", variable=self.sat_var, style="Dark.TCheckbutton")
        self.sat_cb.pack(anchor="w", pady=(0, 2))
        self.sat_hint_lbl = ttk.Label(right_col, text="", style="SmallSub.TLabel", wraplength=360, justify="left")
        self.sat_hint_lbl.pack(anchor="w", pady=(0, 8))

        self.gamma_var = tk.BooleanVar(value=self.config.get("gamma", False))
        self.gamma_cb = ttk.Checkbutton(right_col, text="", variable=self.gamma_var, style="Dark.TCheckbutton")
        self.gamma_cb.pack(anchor="w", pady=(0, 2))
        self.gamma_hint_lbl = ttk.Label(right_col, text="", style="SmallSub.TLabel", wraplength=360, justify="left")
        self.gamma_hint_lbl.pack(anchor="w", pady=(0, 8))

        self.hdr_var = tk.BooleanVar(value=self.config.get("hdr", False))
        self.hdr_cb = ttk.Checkbutton(right_col, text="", variable=self.hdr_var, style="Dark.TCheckbutton")
        self.hdr_cb.pack(anchor="w", pady=(0, 2))
        self.hdr_hint_lbl = ttk.Label(right_col, text="", style="SmallSub.TLabel", wraplength=360, justify="left")
        self.hdr_hint_lbl.pack(anchor="w", pady=(0, 8))

        self.tray_var = tk.BooleanVar(value=self.config.get("tray", False))
        self.tray_cb = ttk.Checkbutton(right_col, text="", variable=self.tray_var, style="Dark.TCheckbutton")
        self.tray_cb.pack(anchor="w", pady=(0, 2))
        self.tray_hint_lbl = ttk.Label(right_col, text="", style="SmallSub.TLabel", wraplength=360, justify="left")
        self.tray_hint_lbl.pack(anchor="w", pady=(0, 8))

        self.auto_var = tk.BooleanVar(value=self.config.get("autostart", False))
        self.auto_cb = ttk.Checkbutton(right_col, text="", variable=self.auto_var, style="Dark.TCheckbutton", command=self._toggle_autostart)
        self.auto_cb.pack(anchor="w", pady=(0, 2))
        self.auto_hint_lbl = ttk.Label(right_col, text="", style="SmallSub.TLabel", wraplength=360, justify="left")
        self.auto_hint_lbl.pack(anchor="w", pady=(0, 8))

        self.autorun_var = tk.BooleanVar(value=self.config.get("autorun", False))
        self.autorun_cb = ttk.Checkbutton(right_col, text="", variable=self.autorun_var, style="Dark.TCheckbutton")
        self.autorun_cb.pack(anchor="w", pady=(0, 2))
        self.autorun_hint_lbl = ttk.Label(right_col, text="", style="SmallSub.TLabel", wraplength=360, justify="left")
        self.autorun_hint_lbl.pack(anchor="w", pady=(0, 8))

        self.audio_var = tk.BooleanVar(value=self.config.get("audio", False))
        self.audio_cb = ttk.Checkbutton(right_col, text="", variable=self.audio_var, style="Dark.TCheckbutton")
        self.audio_cb.pack(anchor="w", pady=(0, 2))
        self.audio_hint_lbl = ttk.Label(right_col, text="", style="SmallSub.TLabel", wraplength=360, justify="left")
        self.audio_hint_lbl.pack(anchor="w", pady=(0, 16))

        # -- Schedule: Auto-Start at time
        hours = [f"{h:02d}" for h in range(24)]
        minutes = [f"{m:02d}" for m in range(0, 60, 5)]

        sched_start_frame = tk.Frame(right_col, bg="#1a1a2e")
        sched_start_frame.pack(anchor="w", fill="x", pady=(0, 2))
        self.sched_start_var = tk.BooleanVar(value=self.config.get("schedule_start_enabled", False))
        self.sched_start_cb = ttk.Checkbutton(sched_start_frame, text="", variable=self.sched_start_var, style="Dark.TCheckbutton")
        self.sched_start_cb.pack(side="left")
        self.sched_start_m_var = tk.StringVar(value=self.config.get("schedule_start_m", "00"))
        ttk.Combobox(sched_start_frame, textvariable=self.sched_start_m_var, values=minutes, state="readonly", width=3, font=("Segoe UI", 9)).pack(side="right", padx=(2, 0))
        tk.Label(sched_start_frame, text=":", bg="#1a1a2e", fg="#c4c4d4", font=("Segoe UI", 10, "bold")).pack(side="right")
        self.sched_start_h_var = tk.StringVar(value=self.config.get("schedule_start_h", "18"))
        ttk.Combobox(sched_start_frame, textvariable=self.sched_start_h_var, values=hours, state="readonly", width=3, font=("Segoe UI", 9)).pack(side="right")
        self.sched_start_hint_lbl = ttk.Label(right_col, text="", style="SmallSub.TLabel", wraplength=360, justify="left")
        self.sched_start_hint_lbl.pack(anchor="w", pady=(0, 8))

        # -- Schedule: Auto-Stop at time
        sched_stop_frame = tk.Frame(right_col, bg="#1a1a2e")
        sched_stop_frame.pack(anchor="w", fill="x", pady=(0, 2))
        self.sched_stop_var = tk.BooleanVar(value=self.config.get("schedule_stop_enabled", False))
        self.sched_stop_cb = ttk.Checkbutton(sched_stop_frame, text="", variable=self.sched_stop_var, style="Dark.TCheckbutton")
        self.sched_stop_cb.pack(side="left")
        self.sched_stop_m_var = tk.StringVar(value=self.config.get("schedule_stop_m", "00"))
        ttk.Combobox(sched_stop_frame, textvariable=self.sched_stop_m_var, values=minutes, state="readonly", width=3, font=("Segoe UI", 9)).pack(side="right", padx=(2, 0))
        tk.Label(sched_stop_frame, text=":", bg="#1a1a2e", fg="#c4c4d4", font=("Segoe UI", 10, "bold")).pack(side="right")
        self.sched_stop_h_var = tk.StringVar(value=self.config.get("schedule_stop_h", "02"))
        ttk.Combobox(sched_stop_frame, textvariable=self.sched_stop_h_var, values=hours, state="readonly", width=3, font=("Segoe UI", 9)).pack(side="right")
        self.sched_stop_hint_lbl = ttk.Label(right_col, text="", style="SmallSub.TLabel", wraplength=360, justify="left")
        self.sched_stop_hint_lbl.pack(anchor="w", pady=(0, 20))

        # -- Traces for Live Sync --
        self.fps_var.trace_add("write", self._on_setting_change)
        self.bright_var.trace_add("write", self._on_setting_change)
        self.smooth_var.trace_add("write", self._on_setting_change)
        self.min_bright_var.trace_add("write", self._on_setting_change)
        self.black_thresh_var.trace_add("write", self._on_setting_change)
        self.edge_var.trace_add("write", self._on_setting_change)
        self.bars_var.trace_add("write", self._on_setting_change)
        self.sat_var.trace_add("write", self._on_setting_change)
        self.gamma_var.trace_add("write", self._on_setting_change)
        self.tray_var.trace_add("write", self._on_setting_change)
        self.audio_var.trace_add("write", self._on_setting_change)
        self.sync_var.trace_add("write", self._on_setting_change)
        self.display_sync_var.trace_add("write", self._on_setting_change)
        self.autorun_var.trace_add("write", self._on_setting_change)
        self.hdr_var.trace_add("write", self._on_setting_change)
        self.sched_start_var.trace_add("write", self._on_setting_change)
        self.sched_start_h_var.trace_add("write", self._on_setting_change)
        self.sched_start_m_var.trace_add("write", self._on_setting_change)
        self.sched_stop_var.trace_add("write", self._on_setting_change)
        self.sched_stop_h_var.trace_add("write", self._on_setting_change)
        self.sched_stop_m_var.trace_add("write", self._on_setting_change)

        # -- BOTTOM SECTION
        bottom_frame = tk.Frame(main_container, bg="#1a1a2e")
        bottom_frame.pack(fill="x", pady=(20, 0))

        # -- Button
        self.btn = tk.Button(bottom_frame, text="", command=self._toggle, bg="#4ecca3", fg="#1a1a2e", activebackground="#3dbb92", activeforeground="#1a1a2e", font=("Segoe UI", 13, "bold"), relief="flat", cursor="hand2", width=20, height=1, bd=0)
        self.btn.pack(pady=(0, 16), ipady=6)

        # -- Status
        self.status_var = tk.StringVar()
        ttk.Label(bottom_frame, textvariable=self.status_var, style="Status.TLabel").pack()

        # -- Color bar
        self.color_bar = tk.Canvas(bottom_frame, height=6, bg="#333355", highlightthickness=0, bd=0)
        self.color_bar.pack(fill="x", pady=(12, 0))

        self._apply_language()

    def _center_window(self):
        self.root.update_idletasks()
        w, h = self.root.winfo_width(), self.root.winfo_height()
        x = (self.root.winfo_screenwidth() // 2) - (w // 2)
        y = (self.root.winfo_screenheight() // 2) - (h // 2)
        self.root.geometry(f"+{x}+{y}")

    def _toggle(self):
        if self.running or self._connecting:
            self._stop()
        else:
            self._start()

    def _start(self):
        sel = self.monitor_combo.current()
        if sel < 0:
            self._set_status("err_select_monitor")
            return
        mon = self.monitors[sel]
        
        self._on_setting_change() # Force update live variables
        
        if self.live_fps <= 0:
            self._set_status("err_fps")
            return

        self._set_status("status_connecting")
        self.btn.config(state="disabled")
        self._connecting = True
        self._start_token += 1
        token = self._start_token
        ui_lang = self.lang
        self.root.update()

        def status(msg):
            # Ambilight() blocks while OpenRGB comes up; keep the label alive.
            try:
                self.root.after(0, lambda m=msg: self._connect_status(token, m))
            except Exception:
                pass

        def connect_and_run():
            try:
                ambi = Ambilight(fps=self.live_fps, brightness=self.live_bright,
                                 status_cb=status, lang=ui_lang)
            except (OpenRGBUnavailable, SystemExit) as exc:
                msg = str(exc) or tr(ui_lang, "err_sdk_not_found")
                self.root.after(0, lambda: self._on_error(token, msg))
                return
            except Exception as exc:
                self.root.after(0, lambda e=exc: self._on_error(token, tr(ui_lang, "err_startup", error=e)))
                return

            if token != self._start_token:
                # A stop landed while we were still waiting for OpenRGB.
                try: ambi.client.disconnect()
                except Exception: pass
                return

            self.ambilight = ambi
            self.running = True
            self._connecting = False
            self.root.after(0, self._on_started)
            try:
                self._run_loop(mon)
            except Exception as exc:
                self.root.after(0, lambda e=exc: self._on_error(token, tr(self.lang, "err_stopped", error=e)))

        self.thread = threading.Thread(target=connect_and_run, daemon=True)
        self.thread.start()

    def _connect_status(self, token, msg):
        if token == self._start_token and not self.running:
            self._raw_status_text = msg
            self.status_var.set(msg)

    def _on_started(self):
        self._autostart_done = True
        self._connecting = False
        self._raw_status_text = None
        self.btn.config(text=self._t("btn_stop"), bg="#e94560", fg="#ffffff", activebackground="#d63450", state="normal")
        self.monitor_combo.config(state="disabled")
        self._refresh_status_text()
        self._bar_color = self._bar_shown = None
        if self._bar_job is not None:
            self.root.after_cancel(self._bar_job)
        self._refresh_color_bar()

    def _refresh_color_bar(self):
        """Paint the last colour sent onto the colour bar, from the Tk thread.

        The capture thread only stores the colour. It used to call
        root.after() itself on every colour change, and a Tk call from another
        thread blocks that thread until the Tk main loop has serviced it --
        twice per call, at up to 60 a second, for a window that mostly sits
        hidden in the tray. This polls at a rate the eye cannot beat, and the
        timer stops altogether while the window is not on screen, so nothing
        is left ticking in the tray; _on_map starts it again.
        """
        self._bar_job = None
        if not self.running or not self.root.winfo_viewable():
            return
        color = self._bar_color
        if color is not None and color != self._bar_shown:
            self._bar_shown = color
            self.color_bar.config(bg="#%02x%02x%02x" % color)
        self._bar_job = self.root.after(COLOR_BAR_REFRESH_MS, self._refresh_color_bar)

    def _on_map(self, event):
        if event.widget is self.root and self._bar_job is None:
            self._refresh_color_bar()

    def _on_error(self, token, msg):
        if token != self._start_token:
            return          # superseded by a stop or a newer start
        self.running = False
        self._connecting = False
        self.ambilight = None
        self.btn.config(text=self._t("btn_start"), bg="#4ecca3", fg="#1a1a2e",
                        activebackground="#3dbb92", state="normal")
        self.monitor_combo.config(state="readonly")
        self._raw_status_text = msg
        self.status_var.set(msg)

    def _stop(self):
        self._start_token += 1      # discard a connect thread still waiting
        self._connecting = False
        self._autostart_done = True # a deliberate stop cancels auto-start
        self.running = False
        if self.ambilight:
            self.ambilight.stop()
        self.btn.config(text=self._t("btn_start"), bg="#4ecca3", fg="#1a1a2e", activebackground="#3dbb92")
        self.monitor_combo.config(state="readonly")
        self._set_status("status_stopped")
        self.color_bar.config(bg="#333355")

    @staticmethod
    def _open_recorder():
        """Loopback capture of the default speaker for audio mode, or False.

        Opened only while audio mode is on. It used to be opened on every
        start whether or not audio mode was ever used, which kept a WASAPI
        loopback stream running in the audio engine for nothing.
        """
        try:
            import soundcard as sc
            default_spk = sc.default_speaker().name
            mics = sc.all_microphones(include_loopback=True)
            mic = next((m for m in mics if m.isloopback and default_spk in m.name), [m for m in mics if m.isloopback][0])
            recorder = mic.recorder(samplerate=48000)
            recorder.__enter__()
            return recorder
        except Exception:
            return False

    def _run_loop(self, mon):
        lower_process_priority()

        ambi = self.ambilight
        grabber = ScreenGrabber(mon)
        sender = AsyncColorSender(ambi.client, ambi.devices, ambi._br,
                                  getattr(ambi, "keyboard", None),
                                  sync=self.live_sync)
        recorder = None             # None: closed, False: failed to open
        prev_sent = None
        prev_raw = None
        raw = None
        lum = 0.0
        pixels = None
        analysed_with = None        # the settings `raw` was computed under
        current_f = None
        applied_br = ambi._br
        lost_since = None
        idle_streak = 0
        force_cycles = 15
        wait = 0.0                  # see loop_rest()
        last_send_time = time.perf_counter()
        last_frame_time = None
        last_gc = time.perf_counter()

        current_fps = max(1, self.live_fps)

        FORCE_RESEND_INTERVAL = 1.0
        RAW_WAKE_THRESHOLD = 15

        # Startup garbage is permanent; freezing it keeps the periodic
        # collection below from walking the whole object graph every time.
        gc.collect()
        gc.freeze()
        gc.disable()

        try:
            while self.running:
                # Read live settings
                if self.live_fps != current_fps and self.live_fps > 0:
                    current_fps = self.live_fps

                interval = 1.0 / current_fps
                base_br = max(1, min(100, self.live_bright)) / 100.0
                sender.set_sync(self.live_sync)

                force_update = False
                if force_cycles > 0:
                    force_update = True
                    force_cycles -= 1

                if self.live_audio:
                    if recorder is None:
                        recorder = self._open_recorder()
                elif recorder is not None:
                    if recorder:
                        recorder.__exit__(None, None, None)
                    recorder = None

                # --- Audio Visualizer Mode ---
                if self.live_audio and recorder:
                    if abs(base_br - applied_br) > 0.005:
                        applied_br = ambi._br = sender._br = base_br
                        force_update = True
                    try:
                        data = recorder.record(numframes=1024)
                        vol = np.sqrt(np.mean(data**2))
                        intensity = min(1.0, vol * 6.0)
                        h = (time.perf_counter() % 10) / 10.0
                        r, g, b = colorsys.hsv_to_rgb(h, 1.0, intensity)
                        color = (int(r*255), int(g*255), int(b*255))
                        diff = 999 if prev_sent is None else color_diff(color, prev_sent)
                        if diff > 5 or force_update:
                            sender.send(*color)
                            prev_sent = color
                            current_f = [float(c) for c in color]
                    except Exception:
                        # record() is what paces this branch; if it fails
                        # outright (device unplugged) this would spin.
                        time.sleep(0.05)
                    continue

                # --- Screen Capture Mode ---
                t0 = time.perf_counter()

                try:
                    frame = grabber.grab(wait)
                except Exception:
                    import traceback
                    try:
                        with open(os.path.join(os.path.dirname(CONFIG_FILE), "ambi_debug.log"), "a") as f:
                            f.write(traceback.format_exc() + "\n")
                    except OSError:
                        pass
                    time.sleep(1)
                    continue
                if wait:
                    # The rest was taken inside grab(); pace from its return.
                    wait = 0.0
                    t0 = time.perf_counter()

                if frame is ScreenGrabber.LOST:
                    # Lock screen, UAC prompt or a mode change. Give a mode
                    # change time to pass before calling it a lock.
                    if lost_since is None:
                        lost_since = t0
                    elif (self.live_display_sync and current_f is not None and sum(current_f) > 0
                            and t0 - lost_since >= CAPTURE_LOST_BLANK_AFTER):
                        sender.send(0, 0, 0)
                        current_f = [0.0, 0.0, 0.0]
                        prev_sent = (0, 0, 0)
                        self._bar_color = prev_sent
                    time.sleep(CAPTURE_RETRY_INTERVAL / 2)
                    continue
                lost_since = None

                still = frame is None
                if frame is not None:
                    # Most desktop updates -- a blinking caret, a spinner --
                    # miss every sampled pixel, and the same pixels give the
                    # same colour.
                    changed = pixels is None or not np.array_equal(frame, pixels)
                    pixels = frame
                elif pixels is None:
                    wait = DEEP_IDLE_INTERVAL       # no first frame yet
                    continue
                else:
                    changed = False

                # A still screen keeps easing towards the colour it already
                # has; it is only analysed again when a setting that shapes
                # the analysis changes.
                settings = (self.live_bars, self.live_black_thresh, self.live_edges,
                            self.live_algo, self.live_sat, self.live_gamma)
                if changed or settings != analysed_with:
                    analysed_with = settings
                    px = pixels
                    if self.live_bars:
                        px = trim_letterbox(px, threshold=self.live_black_thresh)
                    if self.live_edges:
                        px = extract_edges(px, ratio=0.15)

                    if self.live_algo == "Average":
                        # reshape: edge mode hands over an (N, 3) array
                        raw = tuple(px.reshape(-1, 3).mean(axis=0).tolist())
                    elif self.live_algo == "Center Focus" and px.ndim == 3:
                        ch, cw = px.shape[0], px.shape[1]
                        center = px[ch//4:3*ch//4, cw//4:3*cw//4]
                        raw = get_dominant_color(center if center.size > 0 else px)
                    else:
                        raw = get_dominant_color(px)

                    lum = 0.299 * raw[0] + 0.587 * raw[1] + 0.114 * raw[2]
                    if self.live_sat:
                        raw = apply_saturation(raw, boost=1.5)
                    if self.live_gamma:
                        raw = apply_gamma(raw, gamma=1.8)

                # Brightness is pushed to the sender only when it moves. It
                # used to be reset to the slider value at the top of every
                # pass and re-dimmed here, which in HDR mode forced a re-send
                # on every frame -- a dark scene never went idle -- and left
                # the sender a window to scale a colour at full brightness.
                br = base_br
                if self.live_hdr:
                    br *= max(0.2, min(1.0, (lum / 255.0) * 2.0))
                if abs(br - applied_br) > (0.05 if self.live_hdr else 0.005):
                    applied_br = ambi._br = sender._br = br
                    force_update = True

                # Wake-up detection: compare raw screen color to spot real changes
                raw_tuple = (int(raw[0]), int(raw[1]), int(raw[2]))
                if prev_raw is not None and color_diff(raw_tuple, prev_raw) > RAW_WAKE_THRESHOLD:
                    idle_streak = 0
                prev_raw = raw_tuple

                # Step the filter by real elapsed time, not the nominal interval:
                # idle throttling and slow frames both stretch it, and a filter
                # that ignores that stalls exactly when the screen starts moving.
                now_f = time.perf_counter()
                dt = interval if last_frame_time is None else (now_f - last_frame_time)
                last_frame_time = now_f

                if current_f is None:
                    current_f = [float(c) for c in raw]
                else:
                    a = smooth_alpha(self.live_smooth, dt)
                    current_f[0] += (raw[0] - current_f[0]) * a
                    current_f[1] += (raw[1] - current_f[1]) * a
                    current_f[2] += (raw[2] - current_f[2]) * a

                color = (int(current_f[0]), int(current_f[1]), int(current_f[2]))

                # Enforce min brightness floor
                min_br = self.live_min_bright
                if min_br > 0:
                    mx = max(color[0], color[1], color[2])
                    floor = int(255 * min_br / 100.0)
                    if mx < floor and floor > 0:
                        if mx > 0:
                            scale = floor / mx
                            color = (min(255, int(color[0] * scale)),
                                     min(255, int(color[1] * scale)),
                                     min(255, int(color[2] * scale)))
                        else:
                            color = (floor, floor, floor)

                diff = 999 if prev_sent is None else color_diff(color, prev_sent)

                # Forced periodic re-send to prevent long freezes
                now = time.perf_counter()
                if now - last_send_time >= FORCE_RESEND_INTERVAL:
                    force_update = True

                if diff > 3 or force_update:
                    sender.send(*color)
                    prev_sent = color
                    last_send_time = now
                    self._bar_color = color     # picked up by _refresh_color_bar
                # A forced re-send is not activity: counting it as such reset
                # the idle streak every second, so a still screen never got to
                # deep idle.
                if diff > 3:
                    idle_streak = 0
                else:
                    idle_streak += 1

                elapsed = time.perf_counter() - t0

                # Collect on a wall clock rather than per idle iteration: the
                # pause used to land immediately before each deep-idle poll,
                # which put it squarely in the wake-up path.
                if now - last_gc >= GC_INTERVAL:
                    last_gc = now
                    gc.collect()

                sleep, wait = loop_rest(idle_streak, interval, elapsed, still)
                if sleep > 0:
                    time.sleep(sleep)
        finally:
            gc.unfreeze()
            gc.enable()
            grabber.close()
            sender.stop()
            if getattr(ambi, "keyboard", None) is not None:
                ambi.keyboard.close()
            if recorder:
                recorder.__exit__(None, None, None)

    def _autostart_retry(self):
        """Start the loop as soon as OpenRGB is reachable.

        Ambilight() waits for the SDK server on its own, so this normally fires
        once. It only re-fires if a whole wait window expired with OpenRGB still
        down (e.g. it was never launched). Stops after the first successful start
        or any deliberate stop -- see _on_started / _stop.
        """
        if self._autostart_done:
            return
        if not self.running and not self._connecting:
            self._start()
        self.root.after(AUTOSTART_RETRY_MS, self._autostart_retry)

    def _autorun_countdown(self, remaining=8):
        """Countdown before auto-starting Ambilight (gives OpenRGB time to start)."""
        if self._connecting or self.running:
            return
        if not self.running and remaining > 0:
            self._set_status("status_autorun_wait", sec=remaining)
            self.root.after(1000, self._autorun_countdown, remaining - 1)
        elif not self.running:
            self._set_status("status_autorun_go")
            self._start()

    def _check_schedule(self):
        """Check if it's time to auto-start or auto-stop. Runs every 30 seconds."""
        import datetime
        now = datetime.datetime.now()
        now_hm = (now.hour, now.minute)

        if self.sched_start_var.get() and not self.running:
            try:
                target = (int(self.sched_start_h_var.get()), int(self.sched_start_m_var.get()))
                # Match if within 2-minute window of the target
                diff = (now_hm[0] - target[0]) * 60 + (now_hm[1] - target[1])
                if 0 <= diff < 2:
                    self._start()
            except (ValueError, tk.TclError):
                pass

        if self.sched_stop_var.get() and self.running:
            try:
                target = (int(self.sched_stop_h_var.get()), int(self.sched_stop_m_var.get()))
                diff = (now_hm[0] - target[0]) * 60 + (now_hm[1] - target[1])
                if 0 <= diff < 2:
                    self._stop()
            except (ValueError, tk.TclError):
                pass

        self.root.after(30000, self._check_schedule)  # check every 30s

    def run(self):
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.bind("<Unmap>", self._on_unmap)
        self.root.bind("<Map>", self._on_map)

        # If launched with --startup flag, start hidden in tray
        if _STARTUP_MODE:
            self.root.after(100, self._hide_window)

        # Auto-start the loop. This used to be a fixed Start -> Stop -> Start
        # dance on 0.5 s / 2 s / 3 s timers, which simply lost the race: at logon
        # OpenRGB is not listening for another ~10-20 s, so all three attempts
        # died on "OpenRGB SDK Server not found" and the app then sat idle.
        # Ambilight() now waits for the server itself, and _autostart_retry
        # covers the case where OpenRGB is not running at all yet.
        self.root.after(AUTOSTART_DELAY_MS, self._autostart_retry)

        # If autorun is enabled, schedule the ambilight loop after cooldown
        if self.config.get("autorun", False):
            self.root.after(5000, self._autorun_countdown, 8)

        # Start schedule checker
        self.root.after(5000, self._check_schedule)

        self.root.mainloop()

    def _on_unmap(self, event):
        if event.widget is self.root and self.root.state() == 'iconic':
            if self.live_tray:
                self._hide_window()

    def _hide_window(self):
        self.root.withdraw()
        image = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(image)
        d.ellipse((3, 3, 61, 61), fill=(26, 26, 46, 255))
        d.ellipse((14, 14, 50, 50), fill=(233, 69, 96, 255))
        
        menu = pystray.Menu(
            pystray.MenuItem(self._t("tray_show"), self._show_window, default=True),
            pystray.MenuItem(self._t("tray_exit"), self._quit_from_tray)
        )
        self.icon = pystray.Icon("Ambilight", image, "Ambilight", menu)
        threading.Thread(target=self.icon.run, daemon=True).start()

    def _show_window(self, icon, item):
        self.icon.stop()
        self.root.after(0, self._restore_gui)
        
    def _restore_gui(self):
        self.root.deiconify()
        self.root.state('normal')

    def _quit_from_tray(self, icon, item):
        self.icon.stop()
        self.root.after(0, self._on_close)

    def _on_close(self):
        self._stop()
        self.root.destroy()



# ===========================================================================
#  Entry point
# ===========================================================================

# Any engine flag implies the headless path, so `python Ambilight.pyw --test`
# needs no --cli of its own. Everything else -- no arguments at all, or the
# tray-start flag -- opens the window, which is what a double-click hits.
_CLI_FLAGS = frozenset((
    "--cli", "--list", "--test", "--restore", "--fps", "--monitor", "--width",
    "--brightness", "--smoothness", "--edges", "--skip-bars", "--wait",
    "--sync", "--no-sync",
))


def _parse_lang_arg(argv):
    for i, arg in enumerate(argv):
        if arg == "--lang" and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith("--lang="):
            return arg.split("=", 1)[1]
    return None


def _wants_cli(argv):
    return any(a.split("=", 1)[0] in _CLI_FLAGS for a in argv)


# Since 2026-11-22 Ambilight runs inside OpenRGB as a plugin (plugin/ in this
# repository, installed to OpenRGB-patched\plugins\). This window would drive
# the same LEDs a second time, so a plain launch hands over to OpenRGB
# instead; --legacy still opens the old window.
_OPENRGB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "OpenRGB-patched")
_PLUGIN_DLL = os.path.join(_OPENRGB_DIR, "plugins", "OpenRGBAmbilightPlugin.dll")


def _detect_handover_language():
    cli_lang = _parse_lang_arg(sys.argv[1:])
    if cli_lang is not None:
        return resolve_language(cli_lang)
    plugin_cfg = os.path.join(_OPENRGB_DIR, "config", "plugins", "settings", "Ambilight.json")
    lang = _read_config_language(plugin_cfg)
    if lang != "auto":
        return lang
    openrgb_cfg = os.path.join(_OPENRGB_DIR, "config", "OpenRGB.json")
    try:
        with open(openrgb_cfg, "r", encoding="utf-8") as f:
            ui = json.load(f).get("UserInterface", {})
        orgb_lang = normalize_language(ui.get("language", ""))
        if orgb_lang != "auto":
            return orgb_lang
    except Exception:
        pass
    return resolve_language(_read_config_language())


def _hand_over_to_openrgb():
    from tkinter import messagebox
    lang = _detect_handover_language()
    root = tk.Tk()
    root.withdraw()
    if messagebox.askyesno("Ambilight", tr(lang, "handover_msg")):
        import subprocess
        subprocess.Popen([os.path.join(_OPENRGB_DIR, "OpenRGB.exe")], cwd=_OPENRGB_DIR)
    root.destroy()


if __name__ == "__main__":
    if _wants_cli(sys.argv[1:]):
        sys.argv = [a for a in sys.argv if a != "--cli"]
        if os.path.exists(_PLUGIN_DLL):
            note_lang = _parse_lang_arg(sys.argv[1:]) or _read_config_language()
            print(tr(note_lang, "cli_plugin_note"), flush=True)
        main()
    elif os.path.exists(_PLUGIN_DLL) and "--legacy" not in sys.argv:
        _hand_over_to_openrgb()
    else:
        AmbilightGUI().run()
