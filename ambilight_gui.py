"""
Ambilight GUI — visual interface for the ambilight script.
Uses tkinter (built into Python, no extra install needed).
"""

import tkinter as tk
from tkinter import ttk
import threading
import sys
import os
import gc
import json
import logging
import pystray
from PIL import Image, ImageDraw

_STARTUP_MODE = "--startup" in sys.argv

logging.getLogger("openrgb").setLevel(logging.CRITICAL)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ambilight import (Ambilight, get_monitors, get_dominant_color,
                       color_diff, extract_edges, trim_letterbox,
                       AsyncColorSender, lower_process_priority,
                       apply_gamma, apply_saturation,
                       DOWNSAMPLE, IDLE_STREAK_LIMIT, IDLE_MULTIPLIER,
                       DEEP_IDLE_LIMIT, DEEP_IDLE_INTERVAL)

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


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
        self.monitors = get_monitors()

        self.config = {
            "monitor": 0,
            "fps": "15",
            "brightness": 100,
            "smoothness": 35,
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
        
        self.live_fps = 15.0
        self.live_bright = 100
        self.live_smooth = 35
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

        self._load_config()

        self._build_ui()
        self._center_window()

        try:
            import keyboard
            saved_hk = self.config.get("hotkey", "None")
            if saved_hk and saved_hk != "None":
                keyboard.add_hotkey(saved_hk, self._toggle, suppress=False)
        except Exception:
            pass

    def _on_setting_change(self, *args):
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
        self.live_algo = self.algo_var.get()
        self.live_tray = self.tray_var.get()
        self.live_audio = self.audio_var.get()
        self._save_config()

    def _load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r") as f:
                    self.config.update(json.load(f))
            except Exception:
                pass

    def _save_config(self):
        try:
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
            self.config["algo"] = self.algo_var.get()
            self.config["tray"] = self.tray_var.get()
            self.config["audio"] = self.audio_var.get()
            self.config["display_sync"] = self.display_sync_var.get()
            self.config["autostart"] = self.auto_var.get()
            self.config["autorun"] = self.autorun_var.get()
            self.config["hotkey"] = self.hotkey_var.get()
            self.config["schedule_start_enabled"] = self.sched_start_var.get()
            self.config["schedule_start_h"] = self.sched_start_h_var.get()
            self.config["schedule_start_m"] = self.sched_start_m_var.get()
            self.config["schedule_stop_enabled"] = self.sched_stop_var.get()
            self.config["schedule_stop_h"] = self.sched_stop_h_var.get()
            self.config["schedule_stop_m"] = self.sched_stop_m_var.get()
            with open(CONFIG_FILE, "w") as f:
                json.dump(self.config, f)
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
        self.hotkey_var.set("Press a key combination...")
        self.hotkey_btn.config(bg="#e94560", fg="#ffffff")
        self.root.update()
        
        def listen():
            try:
                import keyboard
                hk = keyboard.read_hotkey(suppress=False)
                self.root.after(0, self._set_hotkey, hk)
            except Exception:
                self.root.after(0, self._set_hotkey, "None")
                
        threading.Thread(target=listen, daemon=True).start()

    def _set_hotkey(self, hk):
        import keyboard
        self.hotkey_btn.config(bg="#16213e", fg="#4ecca3")
        if hk and "unknown" not in hk.lower():
            self.hotkey_var.set(hk)
            self.config["hotkey"] = hk
            keyboard.unhook_all()
            if hk != "None":
                try:
                    keyboard.add_hotkey(hk, self._toggle, suppress=False)
                except Exception:
                    pass
            self._save_config()
        else:
            self.hotkey_var.set(self.config.get("hotkey", "None"))

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

        ttk.Label(main_container, text="⬤ Ambilight", style="Title.TLabel").pack(anchor="w")
        ttk.Label(main_container, text="OpenRGB GPU Screen Sync", style="Sub.TLabel").pack(anchor="w", pady=(0, 20))

        content_frame = tk.Frame(main_container, bg="#1a1a2e")
        content_frame.pack(fill="both", expand=True)

        left_col = tk.Frame(content_frame, bg="#1a1a2e")
        left_col.pack(side="left", fill="both", expand=True, padx=(0, 15))

        right_col = tk.Frame(content_frame, bg="#1a1a2e")
        right_col.pack(side="left", fill="both", expand=True, padx=(15, 0))

        # -- Monitor
        ttk.Label(left_col, text="Display", style="Field.TLabel").pack(anchor="w", pady=(0, 4))
        names = [f"Monitor {m['id']}  —  {m['width']}×{m['height']}" for m in self.monitors]
        self.monitor_var = tk.StringVar()
        self.monitor_combo = ttk.Combobox(left_col, textvariable=self.monitor_var, values=names, state="readonly", width=30, font=("Segoe UI", 10))
        self.monitor_combo.pack(anchor="w", pady=(0, 2), ipady=4)
        if names:
            idx = self.config.get("monitor", 0)
            if idx >= len(names) or idx < 0: idx = 0
            self.monitor_combo.current(idx)
        ttk.Label(left_col, text="Select the monitor to capture colors from.", style="SmallSub.TLabel").pack(anchor="w", pady=(0, 16))

        # -- FPS
        ttk.Label(left_col, text="Refresh Rate (FPS)", style="Field.TLabel").pack(anchor="w", pady=(0, 4))
        self.fps_var = tk.StringVar(value=str(self.config.get("fps", "15")))
        self.fps_entry = tk.Entry(left_col, textvariable=self.fps_var, bg="#16213e", fg="#ffffff", insertbackground="#ffffff", font=("Segoe UI", 10), width=10, relief="flat", bd=0, highlightthickness=1, highlightbackground="#333355", highlightcolor="#4ecca3")
        self.fps_entry.pack(anchor="w", pady=(0, 2), ipady=6, ipadx=6)
        ttk.Label(left_col, text="How many times per second the screen is analyzed. Default: 15.", style="SmallSub.TLabel").pack(anchor="w", pady=(0, 16))

        # -- Scanning Algorithm
        ttk.Label(left_col, text="Scanning Algorithm", style="Field.TLabel").pack(anchor="w", pady=(0, 4))
        self.algo_var = tk.StringVar(value=self.config.get("algo", "Dominant"))
        self.algo_combo = ttk.Combobox(left_col, textvariable=self.algo_var, values=["Dominant", "Average", "Center Focus"], state="readonly", width=30, font=("Segoe UI", 10))
        self.algo_combo.pack(anchor="w", pady=(0, 2), ipady=4)
        ttk.Label(left_col, text="How the script interprets screen colors.", style="SmallSub.TLabel").pack(anchor="w", pady=(0, 16))

        # -- Brightness
        ttk.Label(left_col, text="Max Brightness (1–100)", style="Field.TLabel").pack(anchor="w", pady=(0, 4))
        self.bright_var = tk.StringVar(value=str(self.config.get("brightness", 100)))
        self.bright_entry = tk.Entry(left_col, textvariable=self.bright_var, bg="#16213e", fg="#ffffff", insertbackground="#ffffff", font=("Segoe UI", 10), width=10, relief="flat", bd=0, highlightthickness=1, highlightbackground="#333355", highlightcolor="#4ecca3")
        self.bright_entry.pack(anchor="w", pady=(0, 2), ipady=6, ipadx=6)
        ttk.Label(left_col, text="Limits the maximum brightness sent to the LEDs. Default: 100.", style="SmallSub.TLabel").pack(anchor="w", pady=(0, 16))

        # -- Smoothness
        ttk.Label(left_col, text="Transition Smoothness (1–100)", style="Field.TLabel").pack(anchor="w", pady=(0, 4))
        self.smooth_var = tk.StringVar(value=str(self.config.get("smoothness", 35)))
        self.smooth_entry = tk.Entry(left_col, textvariable=self.smooth_var, bg="#16213e", fg="#ffffff", insertbackground="#ffffff", font=("Segoe UI", 10), width=10, relief="flat", bd=0, highlightthickness=1, highlightbackground="#333355", highlightcolor="#4ecca3")
        self.smooth_entry.pack(anchor="w", pady=(0, 2), ipady=6, ipadx=6)
        ttk.Label(left_col, text="Lower = smooth and slow. Higher = snappy and fast. Default: 35.", style="SmallSub.TLabel").pack(anchor="w", pady=(0, 16))

        # -- Min Brightness
        ttk.Label(left_col, text="Min Brightness (0–100)", style="Field.TLabel").pack(anchor="w", pady=(0, 4))
        self.min_bright_var = tk.StringVar(value=str(self.config.get("min_brightness", 0)))
        self.min_bright_entry = tk.Entry(left_col, textvariable=self.min_bright_var, bg="#16213e", fg="#ffffff", insertbackground="#ffffff", font=("Segoe UI", 10), width=10, relief="flat", bd=0, highlightthickness=1, highlightbackground="#333355", highlightcolor="#4ecca3")
        self.min_bright_entry.pack(anchor="w", pady=(0, 2), ipady=6, ipadx=6)
        ttk.Label(left_col, text="LEDs never go below this brightness. 0 = allow full black. Default: 0.", style="SmallSub.TLabel").pack(anchor="w", pady=(0, 16))

        # -- Black Threshold
        ttk.Label(left_col, text="Black Threshold (1–100)", style="Field.TLabel").pack(anchor="w", pady=(0, 4))
        self.black_thresh_var = tk.StringVar(value=str(self.config.get("black_threshold", 20)))
        self.black_thresh_entry = tk.Entry(left_col, textvariable=self.black_thresh_var, bg="#16213e", fg="#ffffff", insertbackground="#ffffff", font=("Segoe UI", 10), width=10, relief="flat", bd=0, highlightthickness=1, highlightbackground="#333355", highlightcolor="#4ecca3")
        self.black_thresh_entry.pack(anchor="w", pady=(0, 2), ipady=6, ipadx=6)
        ttk.Label(left_col, text="Pixels darker than this are treated as black. Default: 20.", style="SmallSub.TLabel").pack(anchor="w", pady=(0, 16))

        # -- Hotkey
        ttk.Label(left_col, text="Global Hotkey", style="Field.TLabel").pack(anchor="w", pady=(0, 4))
        self.hotkey_var = tk.StringVar(value=self.config.get("hotkey", "None"))
        self.hotkey_btn = tk.Button(left_col, textvariable=self.hotkey_var, command=self._record_hotkey, bg="#16213e", fg="#4ecca3", activebackground="#4ecca3", activeforeground="#1a1a2e", font=("Segoe UI", 10, "bold"), relief="flat", bd=0, highlightthickness=1, highlightbackground="#333355")
        self.hotkey_btn.pack(anchor="w", fill="x", pady=(0, 2), ipady=4)
        ttk.Label(left_col, text="Click to map a keyboard shortcut to toggle Ambilight ON/OFF.", style="SmallSub.TLabel").pack(anchor="w", pady=(0, 16))

        # -- Display Sync
        self.display_sync_var = tk.BooleanVar(value=self.config.get("display_sync", True))
        self.display_sync_cb = ttk.Checkbutton(left_col, text="Turn off with display (Windows)", variable=self.display_sync_var, style="Dark.TCheckbutton")
        self.display_sync_cb.pack(anchor="w", pady=(0, 2))
        ttk.Label(left_col, text="Disables Ambilight when the monitor goes to sleep or PC is locked.", style="SmallSub.TLabel").pack(anchor="w", pady=(0, 16))

        # -- Checkboxes (RIGHT COLUMN)
        self.edge_var = tk.BooleanVar(value=self.config.get("edges", False))
        self.edge_cb = ttk.Checkbutton(right_col, text="Edge Only", variable=self.edge_var, style="Dark.TCheckbutton")
        self.edge_cb.pack(anchor="w", pady=(0, 2))
        ttk.Label(right_col, text="Analyzes only the outer 15% of the screen. Gives accurate Ambilight effect at the edges.", style="SmallSub.TLabel").pack(anchor="w", pady=(0, 8))

        self.bars_var = tk.BooleanVar(value=self.config.get("bars", False))
        self.bars_cb = ttk.Checkbutton(right_col, text="Skip Black Bars", variable=self.bars_var, style="Dark.TCheckbutton")
        self.bars_cb.pack(anchor="w", pady=(0, 2))
        ttk.Label(right_col, text="Ignores cinematic letterbox bands so movies don't artificially darken the lighting.", style="SmallSub.TLabel").pack(anchor="w", pady=(0, 16))

        self.sat_var = tk.BooleanVar(value=self.config.get("saturation", False))
        self.sat_cb = ttk.Checkbutton(right_col, text="Saturation Boost (+50%)", variable=self.sat_var, style="Dark.TCheckbutton")
        self.sat_cb.pack(anchor="w", pady=(0, 2))
        ttk.Label(right_col, text="Makes colors punchy and vibrant. Great for muted games/movies.", style="SmallSub.TLabel").pack(anchor="w", pady=(0, 8))

        self.gamma_var = tk.BooleanVar(value=self.config.get("gamma", False))
        self.gamma_cb = ttk.Checkbutton(right_col, text="Gamma Correction", variable=self.gamma_var, style="Dark.TCheckbutton")
        self.gamma_cb.pack(anchor="w", pady=(0, 2))
        ttk.Label(right_col, text="Adjusts LED brightness curve to match human perception.", style="SmallSub.TLabel").pack(anchor="w", pady=(0, 8))

        self.hdr_var = tk.BooleanVar(value=self.config.get("hdr", False))
        self.hdr_cb = ttk.Checkbutton(right_col, text="Adaptive Brightness (HDR)", variable=self.hdr_var, style="Dark.TCheckbutton")
        self.hdr_cb.pack(anchor="w", pady=(0, 2))
        ttk.Label(right_col, text="Dims LEDs in dark scenes, flares in bright highlights.", style="SmallSub.TLabel").pack(anchor="w", pady=(0, 8))

        self.tray_var = tk.BooleanVar(value=self.config.get("tray", False))
        self.tray_cb = ttk.Checkbutton(right_col, text="Minimize to System Tray", variable=self.tray_var, style="Dark.TCheckbutton")
        self.tray_cb.pack(anchor="w", pady=(0, 2))
        ttk.Label(right_col, text="When you minimize the window, it hides in the taskbar tray.", style="SmallSub.TLabel").pack(anchor="w", pady=(0, 8))

        self.auto_var = tk.BooleanVar(value=self.config.get("autostart", False))
        self.auto_cb = ttk.Checkbutton(right_col, text="Start with Windows", variable=self.auto_var, style="Dark.TCheckbutton", command=self._toggle_autostart)
        self.auto_cb.pack(anchor="w", pady=(0, 2))
        ttk.Label(right_col, text="Automatically launches the program hidden in the tray.", style="SmallSub.TLabel").pack(anchor="w", pady=(0, 8))

        self.autorun_var = tk.BooleanVar(value=self.config.get("autorun", False))
        self.autorun_cb = ttk.Checkbutton(right_col, text="Auto-start Ambilight", variable=self.autorun_var, style="Dark.TCheckbutton")
        self.autorun_cb.pack(anchor="w", pady=(0, 2))
        ttk.Label(right_col, text="Automatically starts the Ambilight loop after an 8-second delay (waits for OpenRGB).", style="SmallSub.TLabel").pack(anchor="w", pady=(0, 8))

        self.audio_var = tk.BooleanVar(value=self.config.get("audio", False))
        self.audio_cb = ttk.Checkbutton(right_col, text="Audio Visualizer (Music Mode)", variable=self.audio_var, style="Dark.TCheckbutton")
        self.audio_cb.pack(anchor="w", pady=(0, 2))
        ttk.Label(right_col, text="Stops screen capture and pulses colors to the beat of computer audio.", style="SmallSub.TLabel").pack(anchor="w", pady=(0, 16))

        # -- Schedule: Auto-Start at time
        hours = [f"{h:02d}" for h in range(24)]
        minutes = [f"{m:02d}" for m in range(0, 60, 5)]

        sched_start_frame = tk.Frame(right_col, bg="#1a1a2e")
        sched_start_frame.pack(anchor="w", fill="x", pady=(0, 2))
        self.sched_start_var = tk.BooleanVar(value=self.config.get("schedule_start_enabled", False))
        ttk.Checkbutton(sched_start_frame, text="Schedule Start", variable=self.sched_start_var, style="Dark.TCheckbutton").pack(side="left")
        self.sched_start_m_var = tk.StringVar(value=self.config.get("schedule_start_m", "00"))
        ttk.Combobox(sched_start_frame, textvariable=self.sched_start_m_var, values=minutes, state="readonly", width=3, font=("Segoe UI", 9)).pack(side="right", padx=(2, 0))
        tk.Label(sched_start_frame, text=":", bg="#1a1a2e", fg="#c4c4d4", font=("Segoe UI", 10, "bold")).pack(side="right")
        self.sched_start_h_var = tk.StringVar(value=self.config.get("schedule_start_h", "18"))
        ttk.Combobox(sched_start_frame, textvariable=self.sched_start_h_var, values=hours, state="readonly", width=3, font=("Segoe UI", 9)).pack(side="right")
        ttk.Label(right_col, text="Automatically starts Ambilight at the chosen time.", style="SmallSub.TLabel").pack(anchor="w", pady=(0, 8))

        # -- Schedule: Auto-Stop at time
        sched_stop_frame = tk.Frame(right_col, bg="#1a1a2e")
        sched_stop_frame.pack(anchor="w", fill="x", pady=(0, 2))
        self.sched_stop_var = tk.BooleanVar(value=self.config.get("schedule_stop_enabled", False))
        ttk.Checkbutton(sched_stop_frame, text="Schedule Stop", variable=self.sched_stop_var, style="Dark.TCheckbutton").pack(side="left")
        self.sched_stop_m_var = tk.StringVar(value=self.config.get("schedule_stop_m", "00"))
        ttk.Combobox(sched_stop_frame, textvariable=self.sched_stop_m_var, values=minutes, state="readonly", width=3, font=("Segoe UI", 9)).pack(side="right", padx=(2, 0))
        tk.Label(sched_stop_frame, text=":", bg="#1a1a2e", fg="#c4c4d4", font=("Segoe UI", 10, "bold")).pack(side="right")
        self.sched_stop_h_var = tk.StringVar(value=self.config.get("schedule_stop_h", "02"))
        ttk.Combobox(sched_stop_frame, textvariable=self.sched_stop_h_var, values=hours, state="readonly", width=3, font=("Segoe UI", 9)).pack(side="right")
        ttk.Label(right_col, text="Automatically stops Ambilight at the chosen time.", style="SmallSub.TLabel").pack(anchor="w", pady=(0, 20))

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
        self.autorun_var.trace_add("write", self._on_setting_change)
        self.hdr_var.trace_add("write", self._on_setting_change)
        self.algo_var.trace_add("write", self._on_setting_change)
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
        self.btn = tk.Button(bottom_frame, text="▶  START", command=self._toggle, bg="#4ecca3", fg="#1a1a2e", activebackground="#3dbb92", activeforeground="#1a1a2e", font=("Segoe UI", 13, "bold"), relief="flat", cursor="hand2", width=20, height=1, bd=0)
        self.btn.pack(pady=(0, 16), ipady=6)

        # -- Status
        self.status_var = tk.StringVar(value="Stopped")
        ttk.Label(bottom_frame, textvariable=self.status_var, style="Status.TLabel").pack()

        # -- Color bar
        self.color_bar = tk.Canvas(bottom_frame, height=6, bg="#333355", highlightthickness=0, bd=0)
        self.color_bar.pack(fill="x", pady=(12, 0))

    def _center_window(self):
        self.root.update_idletasks()
        w, h = self.root.winfo_width(), self.root.winfo_height()
        x = (self.root.winfo_screenwidth() // 2) - (w // 2)
        y = (self.root.winfo_screenheight() // 2) - (h // 2)
        self.root.geometry(f"+{x}+{y}")

    def _toggle(self):
        if self.running:
            self._stop()
        else:
            self._start()

    def _start(self):
        sel = self.monitor_combo.current()
        if sel < 0:
            self.status_var.set("Error: select a monitor")
            return
        mon = self.monitors[sel]
        
        self._on_setting_change() # Force update live variables
        
        if self.live_fps <= 0:
            self.status_var.set("Error: FPS must be > 0")
            return

        self.status_var.set("Connecting to OpenRGB…")
        self.btn.config(state="disabled")
        self.root.update()

        def connect_and_run():
            try:
                self.ambilight = Ambilight(fps=self.live_fps, brightness=self.live_bright)
            except SystemExit:
                self.root.after(0, lambda: self._on_error("OpenRGB SDK Server not found."))
                return
            self.running = True
            self.root.after(0, self._on_started)
            self._run_loop(mon)

        self.thread = threading.Thread(target=connect_and_run, daemon=True)
        self.thread.start()

    def _on_started(self):
        self.btn.config(text="⏹  STOP", bg="#e94560", fg="#ffffff", activebackground="#d63450", state="normal")
        self.monitor_combo.config(state="disabled")
        devs = len(self.ambilight.devices) if self.ambilight else 0
        feats = []
        if self.live_edges: feats.append("edges")
        if self.live_bars: feats.append("skip-bars")
        if self.live_audio: feats.append("audio")
        extra = f"  •  {', '.join(feats)}" if feats else ""
        self.status_var.set(f"Running  •  {devs} device(s){extra}")

    def _on_error(self, msg):
        self.running = False
        self.btn.config(state="normal")
        self.status_var.set(msg)

    def _stop(self):
        self.running = False
        if self.ambilight:
            self.ambilight.stop()
        self.btn.config(text="▶  START", bg="#4ecca3", fg="#1a1a2e", activebackground="#3dbb92")
        self.monitor_combo.config(state="readonly")
        self.status_var.set("Stopped")
        self.color_bar.config(bg="#333355")

    def _run_loop(self, mon):
        import time as _time
        import mss
        import soundcard as sc
        import colorsys
        import numpy as np

        lower_process_priority()

        ambi = self.ambilight
        sender = AsyncColorSender(ambi.devices, ambi._br)
        prev_sent = None
        prev_raw = None
        current_f = None
        prev_br = ambi._br
        idle_streak = 0
        force_cycles = 15
        last_send_time = _time.perf_counter()

        # capture setup — inline, same as original
        import bettercam
        camera = None
        sct = None
        bbox = None
        none_frame_count = 0
        camera_retries = 0
        MAX_NONE_FRAMES = 60
        MAX_CAMERA_RETRIES = 3

        try:
            camera = bettercam.create(output_idx=mon["id"], output_color="RGB")
            if camera:
                camera.start(target_fps=60)
                # First frame warmup check
                for _ in range(15):
                    if camera.get_latest_frame() is not None:
                        break
                    _time.sleep(0.05)
                if camera.get_latest_frame() is None:
                    raise Exception("Camera failed to capture first frame")
        except Exception:
            if camera:
                try: camera.stop(); camera.release()
                except: pass
            try:
                import bettercam
                if mon["id"] in bettercam.core._singletons:
                    del bettercam.core._singletons[mon["id"]]
            except: pass
            camera = None

        if not camera:
            sct = mss.mss()
            bbox = {"top": mon["top"], "left": mon["left"],
                    "width": mon["width"], "height": mon["height"]}

        current_fps = max(1, self.live_fps)

        CAPPED_DEEP_IDLE_INTERVAL = 0.25
        FORCE_RESEND_INTERVAL = 1.0
        RAW_WAKE_THRESHOLD = 15

        try:
            default_spk = sc.default_speaker().name
            mics = sc.all_microphones(include_loopback=True)
            mic = next((m for m in mics if m.isloopback and default_spk in m.name), [m for m in mics if m.isloopback][0])
            recorder = mic.recorder(samplerate=48000)
            recorder.__enter__()
        except Exception:
            recorder = None

        gc.disable()

        try:
            while self.running:
                # Read live settings
                if self.live_fps != current_fps and self.live_fps > 0:
                    current_fps = self.live_fps

                interval = 1.0 / current_fps
                smooth_t = max(1, min(100, self.live_smooth)) / 100.0

                # Dynamically update brightness
                br = max(1, min(100, self.live_bright)) / 100.0
                ambi._br = br
                sender._br = br

                force_update = False
                if abs(br - prev_br) > 0.01:
                    force_update = True
                    prev_br = br
                    
                if force_cycles > 0:
                    force_update = True
                    force_cycles -= 1

                # --- Audio Visualizer Mode ---
                if self.live_audio and recorder:
                    try:
                        data = recorder.record(numframes=1024)
                        vol = np.sqrt(np.mean(data**2))
                        intensity = min(1.0, vol * 6.0)
                        h = (_time.perf_counter() % 10) / 10.0
                        r, g, b = colorsys.hsv_to_rgb(h, 1.0, intensity)
                        color = (int(r*255), int(g*255), int(b*255))
                        diff = 999 if prev_sent is None else color_diff(color, prev_sent)
                        if diff > 5 or force_update:
                            sender.send(*color)
                            prev_sent = color
                            current_f = [float(c) for c in color]
                    except Exception:
                        pass
                    continue

                # --- Screen Capture Mode ---
                t0 = _time.perf_counter()

                try:
                    if camera:
                        frame = camera.get_latest_frame()
                        if frame is None:
                            none_frame_count += 1
                            if none_frame_count >= MAX_NONE_FRAMES:
                                # DDA frame capture failed completely. 
                                # This generally means the display is OFF or computer is locked.
                                if hasattr(self, 'display_sync_var') and self.display_sync_var.get():
                                    if current_f and (current_f[0] + current_f[1] + current_f[2]) > 0:
                                        ambi._sender.send(0, 0, 0)
                                        current_f = [0.0, 0.0, 0.0]
                                        prev_sent = (0, 0, 0)

                                # Camera is dead — try to recover
                                try:
                                    camera.stop()
                                    if hasattr(camera, 'release'):
                                        camera.release()
                                except Exception:
                                    pass
                                camera = None
                                
                                # Only retry camera indefinitely if we want to turn it back on when display wakes.
                                if hasattr(self, 'display_sync_var') and self.display_sync_var.get():
                                    _time.sleep(1.0)
                                    try:
                                        import bettercam
                                        if mon["id"] in bettercam.core._singletons:
                                            del bettercam.core._singletons[mon["id"]]
                                    except: pass
                                    try:
                                        camera = bettercam.create(output_idx=mon["id"], output_color="RGB")
                                        if camera:
                                            camera.start(target_fps=60)
                                    except Exception: pass
                                    none_frame_count = 0
                                    continue
                                
                                camera_retries += 1
                                if camera_retries <= MAX_CAMERA_RETRIES:
                                    _time.sleep(0.5)
                                    try:
                                        # Force clear singleton before retry
                                        try:
                                            import bettercam
                                            if mon["id"] in bettercam.core._singletons:
                                                del bettercam.core._singletons[mon["id"]]
                                        except: pass
                                        camera = bettercam.create(output_idx=mon["id"], output_color="RGB")
                                        if camera:
                                            camera.start(target_fps=60)
                                    except Exception:
                                        camera = None
                                if not camera and not sct:
                                    sct = mss.mss()
                                    bbox = {"top": mon["top"], "left": mon["left"],
                                            "width": mon["width"], "height": mon["height"]}
                                none_frame_count = 0
                            else:
                                _time.sleep(0.005)
                            continue
                        none_frame_count = 0
                        pixels = frame[::DOWNSAMPLE, ::DOWNSAMPLE]
                    else:
                        screen = sct.grab(bbox)
                        h, w = screen.height, screen.width
                        raw_bgra = np.frombuffer(screen.bgra, dtype=np.uint8).reshape(h, w, 4)
                        pixels_bgra = raw_bgra[::DOWNSAMPLE, ::DOWNSAMPLE]
                        pixels = pixels_bgra[:, :, 2::-1]
                except Exception as e:
                    import traceback
                    with open("ambi_debug.log", "a") as f:
                        f.write(traceback.format_exc() + "\n")
                    _time.sleep(1)
                    continue

                if self.live_bars:
                    pixels = trim_letterbox(pixels, threshold=self.live_black_thresh)
                if self.live_edges:
                    pixels = extract_edges(pixels, ratio=0.15)

                if self.live_algo == "Average":
                    raw = [np.mean(pixels[:, :, 0]), np.mean(pixels[:, :, 1]), np.mean(pixels[:, :, 2])]
                elif self.live_algo == "Center Focus":
                    ch, cw = pixels.shape[0], pixels.shape[1]
                    center = pixels[ch//4:3*ch//4, cw//4:3*cw//4]
                    if center.size > 0: raw = get_dominant_color(center)
                    else: raw = get_dominant_color(pixels)
                else:
                    raw = get_dominant_color(pixels)

                if self.live_hdr:
                    L = 0.299 * raw[0] + 0.587 * raw[1] + 0.114 * raw[2]
                    hdr_mult = max(0.2, min(1.0, (L / 255.0) * 2.0))
                    new_br = max(1, min(100, self.live_bright)) / 100.0 * hdr_mult
                    if abs(new_br - ambi._br) > 0.05:
                        ambi._br = new_br
                        sender._br = new_br
                        force_update = True

                if self.live_sat:
                    raw = apply_saturation(raw, boost=1.5)
                if self.live_gamma:
                    raw = apply_gamma(raw, gamma=1.8)

                # Wake-up detection: compare raw screen color to spot real changes
                raw_tuple = (int(raw[0]), int(raw[1]), int(raw[2]))
                if prev_raw is not None and color_diff(raw_tuple, prev_raw) > RAW_WAKE_THRESHOLD:
                    idle_streak = 0
                prev_raw = raw_tuple

                if current_f is None:
                    current_f = [float(c) for c in raw]
                else:
                    current_f[0] += (raw[0] - current_f[0]) * smooth_t
                    current_f[1] += (raw[1] - current_f[1]) * smooth_t
                    current_f[2] += (raw[2] - current_f[2]) * smooth_t

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
                now = _time.perf_counter()
                if now - last_send_time >= FORCE_RESEND_INTERVAL:
                    force_update = True

                if diff > 3 or force_update:
                    sender.send(*color)
                    prev_sent = color
                    idle_streak = 0
                    last_send_time = now
                    hx = f"#{color[0]:02x}{color[1]:02x}{color[2]:02x}"
                    self.root.after(0, lambda c=hx: self.color_bar.config(bg=c))
                else:
                    idle_streak += 1

                elapsed = _time.perf_counter() - t0

                if idle_streak >= DEEP_IDLE_LIMIT:
                    gc.collect()
                    _time.sleep(CAPPED_DEEP_IDLE_INTERVAL)
                elif idle_streak >= IDLE_STREAK_LIMIT:
                    if idle_streak % 30 == 0:
                        gc.collect()
                    s = interval * IDLE_MULTIPLIER - elapsed
                    if s > 0: _time.sleep(s)
                else:
                    s = interval - elapsed
                    if s > 0: _time.sleep(s)
        finally:
            gc.enable()
            if sct:
                sct.close()
            if camera:
                try:
                    camera.stop()
                    if hasattr(camera, 'release'):
                        camera.release()
                except Exception:
                    pass
            sender.stop()
            if recorder:
                recorder.__exit__(None, None, None)

    def _autorun_countdown(self, remaining=8):
        """Countdown before auto-starting Ambilight (gives OpenRGB time to start)."""
        if not self.running and remaining > 0:
            self.status_var.set(f"Auto-start in {remaining}s (waiting for OpenRGB)…")
            self.root.after(1000, self._autorun_countdown, remaining - 1)
        elif not self.running:
            self.status_var.set("Auto-starting Ambilight…")
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

        # If launched with --startup flag, start hidden in tray
        if _STARTUP_MODE:
            self.root.after(100, self._hide_window)

        # User request: Auto-start script with a micro-delay automatically.
        # Implemented their exact manual fix (Start -> Stop -> Start) as an automated sequence 
        # to guarantee the OpenRGB hardware race condition gets bypassed.
        self.root.after(500, self._start)
        self.root.after(2000, self._stop)
        self.root.after(3000, self._start)

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
            pystray.MenuItem("Show Ambilight", self._show_window, default=True),
            pystray.MenuItem("Exit", self._quit_from_tray)
        )
        self.icon = pystray.Icon("Ambilight", image, "Ambilight Desktop", menu)
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

if __name__ == "__main__":
    app = AmbilightGUI()
    app.run()
