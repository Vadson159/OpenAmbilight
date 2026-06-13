"""
Ambilight for OpenRGB (GPU-accelerated, system-level optimized).

Optimizations:
    - DXcam DXGI capture (GPU, zero CPU render)
    - Raw BGRA buffer (skips cv2.cvtColor entirely)
    - Below-normal process priority (never steals game cycles)
    - Manual GC (disabled during active capture, runs in idle)
    - Aggressive dynamic FPS (1 FPS deep idle, instant wake-up)
    - Async color sending (non-blocking OpenRGB I/O)
    - Edge-only & letterbox detection (optional)

Usage:
    python ambilight.py
    python ambilight.py --fps 30 --edges --skip-bars
    python ambilight.py --list | --test | --restore
"""

import time
import sys
import gc
import argparse
import logging
import threading
import ctypes
import numpy as np
from openrgb import OpenRGBClient
from openrgb.utils import RGBColor


# ---------------------------------------------------------------------------
#  Constants
# ---------------------------------------------------------------------------

DOWNSAMPLE = 20
IDLE_STREAK_LIMIT = 10       # frames until "idle" mode
IDLE_MULTIPLIER = 3.0        # sleep multiplier while idle
DEEP_IDLE_LIMIT = 60         # frames until "deep idle" (≈4 sec at 15 FPS)
DEEP_IDLE_INTERVAL = 1.0     # 1 FPS in deep idle
EDGE_RATIO = 0.15
BLACK_THRESHOLD = 20
BLACK_ROW_RATIO = 0.90


# ---------------------------------------------------------------------------
#  System tuning
# ---------------------------------------------------------------------------

def lower_process_priority():
    """Set current process to BELOW_NORMAL priority via Win32 API."""
    try:
        kernel32 = ctypes.windll.kernel32
        BELOW_NORMAL = 0x00004000
        handle = kernel32.GetCurrentProcess()
        kernel32.SetPriorityClass(handle, BELOW_NORMAL)
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
    brightness = frame.max(axis=2)
    top, bot = 0, h
    for i in range(h):
        if np.count_nonzero(brightness[i] > threshold) / brightness.shape[1] > (1 - row_ratio):
            top = i
            break
    for i in range(h - 1, -1, -1):
        if np.count_nonzero(brightness[i] > threshold) / brightness.shape[1] > (1 - row_ratio):
            bot = i + 1
            break
    if bot - top < h * 0.3:
        return frame
    return frame[top:bot]


# ---------------------------------------------------------------------------
#  Color math
# ---------------------------------------------------------------------------

import colorsys

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
    if pixels.ndim == 3:
        flat = pixels.reshape(-1, 3).astype(np.float32)
    else:
        flat = pixels.astype(np.float32)

    r, g, b = flat[:, 0], flat[:, 1], flat[:, 2]
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)

    with np.errstate(divide="ignore", invalid="ignore"):
        score = np.where(mx > 0, (mx - mn) * mx, 0)

    k = max(1, len(score) // 10)
    threshold = np.partition(score, -k)[-k]
    mask = score >= threshold

    if np.count_nonzero(mask) > 10:
        dr, dg, db = int(np.mean(r[mask])), int(np.mean(g[mask])), int(np.mean(b[mask]))
    else:
        dr, dg, db = int(np.mean(r)), int(np.mean(g)), int(np.mean(b))

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


# ---------------------------------------------------------------------------
#  Async color sender
# ---------------------------------------------------------------------------

class AsyncColorSender:
    def __init__(self, devices, brightness):
        self._devices = devices
        self._br = brightness
        self._latest_color = None
        self._new_color_event = threading.Event()
        self._stop_ev = threading.Event()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def send(self, r, g, b):
        self._latest_color = (r, g, b)
        self._new_color_event.set()

    def _worker(self):
        while not self._stop_ev.is_set():
            if self._new_color_event.wait(timeout=0.5):
                self._new_color_event.clear()
                color_tuple = self._latest_color
                if color_tuple:
                    r, g, b = color_tuple
                    color = RGBColor(int(r * self._br), int(g * self._br), int(b * self._br))
                    for dev in self._devices:
                        try:
                            dev.set_color(color)
                        except Exception:
                            pass

    def stop(self):
        self._stop_ev.set()
        self._thread.join(timeout=2)


# ---------------------------------------------------------------------------
#  Ambilight controller
# ---------------------------------------------------------------------------

class Ambilight:
    def __init__(self, fps=15, brightness=100):
        self.client = self._connect()
        import time as _time
        for _ in range(5):
            self.devices = self.client.devices
            if len(self.devices) > 0:
                break
            _time.sleep(0.4)
            try: self.client = self._connect()
            except: pass
            
        # Set Direct mode for real-time color control
        for dev in self.devices:
            try:
                for i, mode in enumerate(dev.modes):
                    if 'direct' in mode.name.lower():
                        dev.set_mode(i)
                        break
            except Exception:
                pass
        
        # KEY FIX: Hardware controllers (especially motherboards/fans) ignore all color 
        # packets for ~500-1000ms while they switch into Direct/Custom mode on the first run.
        # This delay prevents the capture loop from dropping its first initial colors.
        _time.sleep(1.2)
        
        self.fps = fps
        self.interval = 1.0 / fps
        self.brightness = max(1, min(100, brightness)) / 100.0
        self.running = False
        self._br = self.brightness
        self._sender = None

    @staticmethod
    def _connect():
        try:
            import socket
            client = OpenRGBClient()
            try:
                client.comms.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except Exception:
                pass
            return client
        except Exception:
            print("ERROR: Could not connect to OpenRGB SDK Server.")
            sys.exit(1)

    def list_devices(self, monitors=None):
        print(f"OpenRGB SDK — {len(self.devices)} device(s):\n")
        for dev in self.devices:
            print(f"  [{dev.id}] {dev.name}  ({dev.type.name})")
            for z in dev.zones:
                print(f"       zone: {z.name}  ({len(z.leds)} LEDs)")
        if monitors:
            print("\nMonitors (mss):")
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
                    dev.set_color(color)
                except Exception:
                    pass

    def restore(self):
        for dev in self.devices:
            try:
                if len(dev.modes) > 0:
                    dev.set_mode(0)
            except Exception:
                pass
        print("Devices restored to default mode.")

    def run(self, monitor_index=None, preferred_width=2560,
            edge_only=False, skip_bars=False, smoothness=35, display_sync=True):
        import mss
        
        self.running = True
        lower_process_priority()

        monitors = get_monitors()
        if not monitors:
            print("ERROR: No monitors detected.")
            sys.exit(1)

        if monitor_index is not None:
            mon = next((m for m in monitors if m["id"] == monitor_index), monitors[0])
        else:
            mon = find_monitor(monitors, preferred_width)

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
                    time.sleep(0.05)
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

        self._sender = AsyncColorSender(self.devices, self._br)
        smooth_t = max(1, min(100, smoothness)) / 100.0

        CAPPED_DEEP_IDLE_INTERVAL = 0.25
        FORCE_RESEND_INTERVAL = 1.0
        RAW_WAKE_THRESHOLD = 15

        flags = []
        if edge_only: flags.append("edges")
        if skip_bars: flags.append("skip-bars")
        print(f"Ambilight started  |  {mon['width']}x{mon['height']}", flush=True)
        print(f"  Devices: {len(self.devices)}  |  FPS: {self.fps}  |  Brightness: {int(self.brightness*100)}%", flush=True)
        if flags:
            print(f"  Features: {', '.join(flags)}", flush=True)
        print(f"  Priority: below-normal  |  GC: manual  |  Smooth: {smoothness}%", flush=True)
        print(f"  Ctrl+C to stop\n", flush=True)

        prev = None
        prev_raw = None
        current_f = None
        idle_streak = 0
        force_cycles = 15
        last_send_time = time.perf_counter()

        gc.disable()

        try:
            while self.running:
                t0 = time.perf_counter()

                try:
                    if camera:
                        frame = camera.get_latest_frame()
                        if frame is None:
                            none_frame_count += 1
                            if none_frame_count >= MAX_NONE_FRAMES:
                                # DDA frame capture failed completely. 
                                # This generally means the display is OFF or computer is locked.
                                if display_sync:
                                    if current_f and (current_f[0] + current_f[1] + current_f[2]) > 0:
                                        self._sender.send(0, 0, 0)
                                        current_f = [0.0, 0.0, 0.0]
                                        prev_sent = (0, 0, 0)

                                try:
                                    camera.stop()
                                    if hasattr(camera, 'release'):
                                        camera.release()
                                except Exception:
                                    pass
                                camera = None

                                # Only retry camera indefinitely if we want to turn it back on when display wakes.
                                if display_sync:
                                    time.sleep(1.0)
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
                                    time.sleep(0.5)
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
                                time.sleep(0.005)
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
                    traceback.print_exc()
                    time.sleep(1)
                    continue

                if skip_bars:
                    pixels = trim_letterbox(pixels)
                if edge_only:
                    pixels = extract_edges(pixels)

                raw = get_dominant_color(pixels)

                # Wake-up detection via raw color drift
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
                    idle_streak = 0
                    last_send_time = now
                else:
                    idle_streak += 1

                # Adaptive sleep with capped deep idle
                elapsed = time.perf_counter() - t0

                if idle_streak >= DEEP_IDLE_LIMIT:
                    gc.collect()
                    time.sleep(CAPPED_DEEP_IDLE_INTERVAL)
                elif idle_streak >= IDLE_STREAK_LIMIT:
                    if idle_streak % 30 == 0:
                        gc.collect()
                    base_sleep = self.interval * IDLE_MULTIPLIER - elapsed
                    if base_sleep > 0:
                        time.sleep(base_sleep)
                else:
                    base_sleep = self.interval - elapsed
                    if base_sleep > 0:
                        time.sleep(base_sleep)
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
            if self._sender:
                self._sender.stop()
                self._sender = None

    def stop(self):
        self.running = False

    def test(self):
        colors = [
            (255,0,0,"RED"),(0,255,0,"GREEN"),(0,0,255,"BLUE"),
            (255,255,0,"YELLOW"),(255,0,255,"MAGENTA"),(0,255,255,"CYAN"),
            (255,255,255,"WHITE"),
        ]
        time.sleep(0.5)
        for r, g, b, name in colors:
            self.set_color(r, g, b)
            print(f"  {name}", flush=True)
            time.sleep(1)
        self.set_color(0, 0, 0)
        print("Done!")


# ---------------------------------------------------------------------------
#  CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Ambilight via OpenRGB")
    parser.add_argument("--list",       action="store_true")
    parser.add_argument("--test",       action="store_true")
    parser.add_argument("--restore",    action="store_true")
    parser.add_argument("--fps",        type=float, default=15)
    parser.add_argument("--monitor",    type=int,   default=None)
    parser.add_argument("--width",      type=int,   default=2560)
    parser.add_argument("--brightness", type=int,   default=100)
    parser.add_argument("--smoothness", type=int,   default=35)
    parser.add_argument("--edges",      action="store_true")
    parser.add_argument("--skip-bars",  action="store_true")
    args = parser.parse_args()

    logging.getLogger("openrgb").setLevel(logging.CRITICAL)

    print("Connecting to OpenRGB…", flush=True)
    ambi = Ambilight(fps=args.fps, brightness=args.brightness)

    if args.list:
        ambi.list_devices(monitors=get_monitors()); return
    if args.test:
        ambi.test(); return
    if args.restore:
        ambi.restore(); return

    import signal
    def _quit(sig=None, frame=None):
        print("\nStopping…", flush=True)
        ambi.stop()
    signal.signal(signal.SIGINT, _quit)
    signal.signal(signal.SIGTERM, _quit)

    ambi.run(monitor_index=args.monitor, preferred_width=args.width,
             edge_only=args.edges, skip_bars=args.skip_bars, smoothness=args.smoothness)

if __name__ == "__main__":
    main()
