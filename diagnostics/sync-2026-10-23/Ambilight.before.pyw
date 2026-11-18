"""
Ambilight for OpenRGB — single-file build (engine + GUI + keyboard driver).

Double-click this file to open the interface. Everything the app needs lives
here: the DXGI capture engine, the tkinter GUI, and the XPG Summoner HID
fallback driver. There are no sibling modules to keep next to it — only
config.json, which is written beside this file.

Engine
    - DXGI capture on demand, reading only the sampled pixels (no full-frame copy)
    - Colors pushed as one hand-packed write for all devices, never read back
    - Frame-rate independent smoothing (a setting means a time, not a frame count)
    - Below-normal priority + EcoQoS (never steals game cycles)
    - Manual GC (disabled during capture, collected on a wall-clock interval)
    - Dynamic FPS with a 50 ms deep-idle poll
    - Async color sending (non-blocking OpenRGB I/O, wakes only when a device is due)
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

# bettercam pulls in comtypes, which initialises COM on the importing thread as
# STA; soundcard initialises the same thread as MTA. Whichever loads second gets
# RPC_E_CHANGED_MODE -- soundcard shrugs that off, comtypes turns it into a hard
# OSError. Pin comtypes to MTA so the two agree. Must run before any import can
# reach comtypes.
sys.coinit_flags = 0        # COINIT_MULTITHREADED

import os
import gc
import json
import math
import time
import struct
import ctypes
import logging
import argparse
import colorsys
import threading
import collections

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
# desktop refuses duplication for as long as it is up; bettercam's own
# recovery retries that in a loop with no sleep, i.e. a whole core spinning
# for as long as the PC stays locked.
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

class ScreenGrabber:
    """Downsampled RGB snapshots of one monitor, taken when the loop asks.

    This replaces bettercam's threaded ring buffer, which at 2560x1440 cost
    ~45% of a core at 60 FPS to keep one pixel in every 400: each frame was
    converted BGRA->RGB by cv2 into a fresh 11 MB array, copied into the ring,
    and copied out again by get_latest_frame() -- three full-frame passes and
    ~320k page faults a second. Here the GPU still copies the desktop into a
    CPU-readable staging texture, but the CPU reads only the sampled pixels,
    straight from the mapped texture, and copies them out *before* unmapping
    it. That order is what makes reading BGRA safe: bettercam's own BGRA mode
    hands back a view of memory it has already unmapped.

    Pulling frames from the caller's loop instead of a free-running thread
    also means nothing is captured while the loop idles or plays audio, and
    lets grab() tell "no change" apart from "desktop gone" -- the threaded
    get_latest_frame() just blocked, forever if the PC was locked.

    grab() returns
        ndarray  a new frame, (H/step, W/step, 3) uint8 RGB, C-contiguous
        None     the desktop has not changed since the previous call
        LOST     nothing can be captured right now (lock screen, UAC prompt,
                 display mode change); grab() keeps retrying on its own

    Falls back to GDI capture through mss when DXGI cannot be opened.
    """

    LOST = "lost"

    def __init__(self, mon, step=DOWNSAMPLE):
        self.mon = mon
        self.step = step
        self._output = None       # (bettercam Device, Output) showing `mon`
        self._dup = None
        self._stage = None
        self._surface = None
        self._size = (0, 0)       # staging texture width, height
        self._rotation = 0
        self._retry_at = 0.0
        self._sct = None
        try:
            # Imported here, on the capture thread: comtypes initialises COM on
            # whichever thread imports it first.
            import comtypes
            from bettercam._libs import d3d11, dxgi
            self._com_error = comtypes.COMError
            self._d3d11, self._dxgi = d3d11, dxgi
            self._wait_timeout = ctypes.c_int32(dxgi.DXGI_ERROR_WAIT_TIMEOUT).value
            self._output = self._find_output()
            self._open()
        except Exception:
            self._close_dxgi()
            self._output = None
            import mss
            self._sct = mss.mss()
            self._bbox = {"top": mon["top"], "left": mon["left"],
                          "width": mon["width"], "height": mon["height"]}

    @property
    def backend(self):
        return "DXGI" if self._output is not None else "GDI"

    def _find_output(self):
        """The bettercam (device, output) pair that shows this mss monitor.

        mss numbers monitors across every GPU, bettercam numbers outputs per
        GPU, so the index alone points a monitor on the second GPU at whatever
        shares its number on the first. The desktop origin is unambiguous.
        """
        import bettercam
        factory = getattr(bettercam, "__factory")
        for device, outputs in zip(factory.devices, factory.outputs):
            for output in outputs:
                output.update_desc()
                rc = output.desc.DesktopCoordinates
                if (rc.left, rc.top) == (self.mon["left"], self.mon["top"]):
                    return device, output
        return factory.devices[0], factory.outputs[0][self.mon["id"]]

    def _open(self):
        d3d11, dxgi = self._d3d11, self._dxgi
        device, output = self._output
        output.update_desc()
        width, height = output.surface_size

        desc = d3d11.D3D11_TEXTURE2D_DESC()
        desc.Width, desc.Height = width, height
        desc.Format = d3d11.DXGI_FORMAT_B8G8R8A8_UNORM
        desc.MipLevels = desc.ArraySize = desc.SampleDesc.Count = 1
        desc.Usage = d3d11.D3D11_USAGE_STAGING
        desc.CPUAccessFlags = d3d11.D3D11_CPU_ACCESS_READ
        stage = ctypes.POINTER(d3d11.ID3D11Texture2D)()
        device.device.CreateTexture2D(ctypes.byref(desc), None, ctypes.byref(stage))

        dup = ctypes.POINTER(dxgi.IDXGIOutputDuplication)()
        output.output.DuplicateOutput(device.device, ctypes.byref(dup))

        self._stage, self._dup = stage, dup
        self._surface = stage.QueryInterface(dxgi.IDXGISurface)
        self._size = (width, height)
        self._rotation = output.rotation_angle

    def _close_dxgi(self):
        # Dropping the pointers is the release: comtypes calls Release() once
        # as each is freed. (bettercam calls Release() itself and then drops
        # them as well, which releases everything twice.)
        self._surface = self._stage = self._dup = None

    def grab(self):
        if self._sct is not None:
            shot = self._sct.grab(self._bbox)
            bgra = np.frombuffer(shot.bgra, dtype=np.uint8).reshape(shot.height, shot.width, 4)
            return np.ascontiguousarray(bgra[::self.step, ::self.step, 2::-1])

        if self._dup is None:
            if time.perf_counter() < self._retry_at:
                return self.LOST
            try:
                self._open()
            except Exception:
                self._close_dxgi()
                self._retry_at = time.perf_counter() + CAPTURE_RETRY_INTERVAL
                return self.LOST
        try:
            return self._grab_dxgi()
        except Exception:
            # Almost always DXGI_ERROR_ACCESS_LOST: a mode change, a game going
            # exclusive full screen, or the secure desktop. Re-open on a
            # schedule; the first attempt waits out the mode change.
            self._close_dxgi()
            self._retry_at = time.perf_counter() + 0.1
            return self.LOST

    def _grab_dxgi(self):
        dxgi = self._dxgi
        info = dxgi.DXGI_OUTDUPL_FRAME_INFO()
        res = ctypes.POINTER(dxgi.IDXGIResource)()
        try:
            self._dup.AcquireNextFrame(0, ctypes.byref(info), ctypes.byref(res))
        except self._com_error as ce:
            if ce.args[0] == self._wait_timeout:
                return None
            raise
        try:
            # No desktop update, only the pointer. That includes the very
            # first frame of a new duplication, whose texture is still empty
            # -- copying it would turn the LEDs black until the screen moves.
            if info.LastPresentTime == 0:
                return None
            device = self._output[0]
            device.im_context.CopyResource(
                self._stage, res.QueryInterface(self._d3d11.ID3D11Texture2D))
        finally:
            self._dup.ReleaseFrame()

        rect = dxgi.DXGI_MAPPED_RECT()
        self._surface.Map(ctypes.byref(rect), 1)       # DXGI_MAP_READ; waits for the copy
        try:
            width, height = self._size
            pitch = rect.Pitch
            mapped = (ctypes.c_ubyte * (pitch * height)).from_address(
                ctypes.cast(rect.pBits, ctypes.c_void_p).value)
            bgra = np.frombuffer(mapped, dtype=np.uint8).reshape(height, pitch // 4, 4)
            small = np.ascontiguousarray(bgra[::self.step, :width:self.step, 2::-1])
        finally:
            self._surface.Unmap()

        if self._rotation == 90:
            small = np.ascontiguousarray(np.rot90(small, axes=(1, 0)))
        elif self._rotation == 180:
            small = np.ascontiguousarray(np.rot90(small, k=2, axes=(0, 1)))
        elif self._rotation == 270:
            small = np.ascontiguousarray(np.rot90(small, axes=(0, 1)))
        return small

    def close(self):
        self._close_dxgi()
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
    flat = pixels.reshape(-1, 3)
    r, g, b = flat[:, 0], flat[:, 1], flat[:, 2]
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    score = (mx - mn).astype(np.uint16) * mx

    k = max(1, len(score) // 10)
    threshold = np.partition(score, -k)[-k]
    mask = score >= threshold

    n = np.count_nonzero(mask)
    if n > 10:
        sr, sg, sb = flat[mask].sum(axis=0, dtype=np.int64).tolist()
    else:
        n = len(flat)
        sr, sg, sb = flat.sum(axis=0, dtype=np.int64).tolist()
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


# How long a device needs to actually put a colour on its LEDs, in
# milliseconds. Measured on this machine with an OpenRGB build that times
# every RGBController::DeviceUpdateLEDs() call -- the SDK cannot show this,
# because an UPDATELEDS packet only raises a flag and returns in ~0.1 ms while
# the device's own thread does the work:
#
#     Roccat Burst Pro           2 ms   one small HID feature report
#     XPG Summoner keyboard     33 ms   two 264-byte feature reports
#     Kingston Fury DDR5 DRAM   95 ms   72 SMBus byte writes at ~0.9 ms each
#     MSI MYSTIC LIGHT         185 ms   one 727-byte feature report per used
#                                       header, ~93 ms each, to a low-speed
#                                       USB MCU
#
# Writing the packets for all four costs 0.16 ms together, so none of this is
# transport -- it is what the hardware takes, and it spans two orders of
# magnitude. Keys match case-insensitively as substrings of the device name.
DEVICE_LATENCY_MS = {
    "mystic light": 185.0,
    "fury":          95.0,
    "summoner":      33.0,
    "burst pro":      2.0,
}
DEFAULT_LATENCY_MS = 25.0

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


def build_update_blob(items):
    """Pack one solid-colour UPDATELEDS packet per device into a single buffer.

    `items` is [((device_id, led_count), (r, g, b)), ...] -- one entry per
    device, each with its own colour, because the synchronised path hands
    different devices differently aged colours in the same write. Colours go
    out as BBBx to match RGBColor.pack(), and the body repeats its own length
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

    * Synchronisation. Because the latencies differ by two orders of
      magnitude, the same colour reaches the mouse ~183 ms before it reaches
      the motherboard. With `sync` on, each device is handed a colour aged by
      (slowest latency - its own) out of a short history buffer, so the change
      lands everywhere at the same moment. The price is that the whole system
      then trails the screen by the slowest device's latency, which is why
      this is a setting and not the default.
    """

    def __init__(self, client, devices, brightness, keyboard=None, sync=False):
        self._client = client
        self._devices = list(devices)
        self._br = brightness
        self._keyboard = keyboard
        self._sync = bool(sync)

        self._plan = []
        for dev in self._devices:
            self._plan.append(self._entry(dev, (dev.id, len(dev.leds)),
                                          device_latency_ms(getattr(dev, "name", ""))))
        if keyboard is not None:
            # The direct-HID fallback path: same keyboard, same cost.
            self._plan.append(self._entry(None, None, device_latency_ms("summoner")))

        self._slowest = max([e["lat"] for e in self._plan], default=0.0)
        self._latest = None
        self._new_color_event = threading.Event()
        self._stop_ev = threading.Event()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    @staticmethod
    def _entry(dev, spec, latency_ms):
        lat = latency_ms / 1000.0
        return {"dev": dev, "spec": spec, "lat": lat,
                "period": lat * DEVICE_PACE, "next": 0.0,
                "last_rgb": None, "last_t": 0.0}

    def set_sync(self, enabled):
        self._sync = bool(enabled)

    def send(self, r, g, b):
        self._latest = (time.perf_counter(), (r, g, b))
        self._new_color_event.set()

    @staticmethod
    def _pick(history, cutoff):
        """Index of the newest colour in `history` stamped at or before `cutoff`."""
        chosen = 0
        for i in range(1, len(history)):
            if history[i][0] > cutoff:
                break
            chosen = i
        return chosen

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
        # else holds it, drop this frame rather than corrupt the stream -- the
        # next one is milliseconds away, and falling back to the SDK path would
        # only queue up behind the same lock.
        if not comms.lock.acquire(timeout=0.25):
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
        for dev, rgb in writes:
            try:
                dev.set_color(RGBColor(*rgb), fast=True)
            except Exception:
                pass

    def _worker(self):
        history = collections.deque()       # (timestamp, (r, g, b)), oldest first
        poll = DEVICE_KEEPALIVE
        while not self._stop_ev.is_set():
            self._new_color_event.wait(timeout=poll)
            self._new_color_event.clear()

            latest = self._latest
            if latest is not None and (not history or history[-1][0] != latest[0]):
                history.append(latest)
            if not history:
                poll = DEVICE_KEEPALIVE
                continue

            now = time.perf_counter()
            slowest = self._slowest if self._sync else 0.0

            # Nothing will ever ask for a colour older than the longest delay
            # applied, so keep just one entry behind that line.
            while len(history) > 1 and history[1][0] <= now - slowest:
                history.popleft()

            blob = []
            fallback = []
            keyboard_color = None
            wake = now + DEVICE_KEEPALIVE
            for entry in self._plan:
                delay = slowest - entry["lat"]
                i = self._pick(history, now - delay)
                rgb = self._scale(history[i][1])

                if (now >= entry["next"]
                        and (rgb != entry["last_rgb"]
                             or now - entry["last_t"] >= DEVICE_KEEPALIVE)):
                    entry["last_rgb"] = rgb
                    entry["last_t"] = now
                    entry["next"] = now + entry["period"]
                    if entry["spec"] is None:
                        keyboard_color = rgb
                    else:
                        blob.append((entry["spec"], rgb))
                        fallback.append((entry["dev"], rgb))

                # When this device next needs looking at. A device that is up
                # to date only needs its keep-alive -- or, with sync on, the
                # moment the next colour in the history ages into its window.
                # Waking for anything else is what used to rouse this thread
                # ~65 times a second on a still screen, on behalf of the
                # mouse's 3 ms pacing slot.
                if rgb != entry["last_rgb"]:
                    due = entry["next"]
                else:
                    due = entry["last_t"] + DEVICE_KEEPALIVE
                    if i + 1 < len(history):
                        due = min(due, history[i + 1][0] + delay)
                    due = max(due, entry["next"])
                wake = min(wake, due)

            if blob and self._send_raw(blob) == "fail":
                self._send_via_sdk(fallback)

            if keyboard_color is not None and self._keyboard is not None:
                try:
                    self._keyboard.set_color(*keyboard_color)
                except Exception:
                    pass

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
                 sync_devices=False):
        import time as _time
        self.client = self._connect(timeout=connect_timeout, status_cb=status_cb)
        self.devices = self._wait_for_devices(status_cb=status_cb)

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

        # Hold every device's colour change to the slowest device's latency so
        # they all change together. See AsyncColorSender for what that costs.
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
    def _connect(timeout=OPENRGB_CONNECT_TIMEOUT, status_cb=None):
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
                    "OpenRGB SDK server not reachable on "
                    "%s:%d - start OpenRGB and enable its SDK Server."
                    % (OPENRGB_HOST, OPENRGB_PORT)) from last_err
            now = time.time()
            if now - last_notice >= 5.0:
                last_notice = now
                _notify(status_cb,
                        "Waiting for OpenRGB SDK server... %ds left" % int(remaining))
            time.sleep(min(1.0, remaining))

    def _wait_for_devices(self, timeout=OPENRGB_DEVICE_TIMEOUT, status_cb=None):
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
                        "Waiting for OpenRGB devices... (%d found)" % len(devices))
            time.sleep(POLL)
            try:
                self.client.update()
                fresh = list(self.client.devices)
            except Exception:
                # Server went away mid-detection (OpenRGB restarting) -- reconnect.
                self.client = self._connect(timeout=max(1.0, deadline - time.time()),
                                            status_cb=status_cb)
                fresh = list(self.client.devices)
            stable = stable + 1 if len(fresh) == len(devices) else 0
            devices = fresh
        return devices

    def list_devices(self, monitors=None):
        print(f"OpenRGB SDK — {len(self.devices)} device(s):\n")
        for dev in self.devices:
            print(f"  [{dev.id}] {dev.name}  ({dev.type.name})")
            for z in dev.zones:
                print(f"       zone: {z.name}  ({len(z.leds)} LEDs)")
        if self.keyboard is not None and self.keyboard.available:
            print("\nDirect HID (bypassing OpenRGB):")
            print("  XPG Summoner Gaming Keyboard  (feature-report control)")
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
        print("Devices restored to default mode.")

    def run(self, monitor_index=None, preferred_width=2560,
            edge_only=False, skip_bars=False, smoothness=35, display_sync=True):
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

        grabber = ScreenGrabber(mon)
        self._sender = AsyncColorSender(self.client, self.devices, self._br,
                                        self.keyboard, sync=self.sync_devices)

        FORCE_RESEND_INTERVAL = 1.0
        RAW_WAKE_THRESHOLD = 15

        flags = []
        if edge_only: flags.append("edges")
        if skip_bars: flags.append("skip-bars")
        print(f"Ambilight started  |  {mon['width']}x{mon['height']}  |  capture: {grabber.backend}", flush=True)
        print(f"  Devices: {len(self.devices)}  |  FPS: {self.fps}  |  Brightness: {int(self.brightness*100)}%", flush=True)
        if flags:
            print(f"  Features: {', '.join(flags)}", flush=True)
        print(f"  Priority: below-normal + EcoQoS  |  GC: manual  |  Smooth: {smoothness}%", flush=True)
        print(f"  Ctrl+C to stop\n", flush=True)

        prev = None
        prev_raw = None
        raw = None
        current_f = None
        lost_since = None
        idle_streak = 0
        force_cycles = 15
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
                    frame = grabber.grab()
                except Exception:
                    import traceback
                    traceback.print_exc()
                    time.sleep(1)
                    continue

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

                if frame is not None:
                    pixels = frame
                    if skip_bars:
                        pixels = trim_letterbox(pixels)
                    if edge_only:
                        pixels = extract_edges(pixels)
                    raw = get_dominant_color(pixels)
                elif raw is None:
                    time.sleep(self.interval)
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

                if idle_streak >= DEEP_IDLE_LIMIT:
                    time.sleep(DEEP_IDLE_INTERVAL)
                elif idle_streak >= IDLE_STREAK_LIMIT:
                    base_sleep = self.interval * IDLE_MULTIPLIER - elapsed
                    if base_sleep > 0:
                        time.sleep(base_sleep)
                else:
                    base_sleep = self.interval - elapsed
                    if base_sleep > 0:
                        time.sleep(base_sleep)
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
    parser.add_argument("--fps",        type=float, default=60)
    parser.add_argument("--monitor",    type=int,   default=None)
    parser.add_argument("--width",      type=int,   default=2560)
    parser.add_argument("--brightness", type=int,   default=100)
    parser.add_argument("--smoothness", type=int,   default=35)
    parser.add_argument("--edges",      action="store_true")
    parser.add_argument("--skip-bars",  action="store_true")
    parser.add_argument("--sync",       action="store_true",
                        help="delay fast devices so every device changes colour "
                             "at the same moment (costs the slowest device's latency)")
    parser.add_argument("--wait",       type=float, default=OPENRGB_CONNECT_TIMEOUT,
                        help="seconds to wait for the OpenRGB SDK server")
    args = parser.parse_args()

    logging.getLogger("openrgb").setLevel(logging.CRITICAL)

    print("Connecting to OpenRGB…", flush=True)
    try:
        ambi = Ambilight(fps=args.fps, brightness=args.brightness,
                         sync_devices=args.sync,
                         connect_timeout=args.wait)
    except OpenRGBUnavailable as exc:
        print("ERROR: %s" % exc)
        sys.exit(1)

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
        self.live_sync = False
        self.live_display_sync = True

        # The capture thread drops the last colour sent here; the Tk thread
        # paints it (see _refresh_color_bar).
        self._bar_color = None
        self._bar_shown = None
        self._bar_job = None
        self._hotkey = None

        self._load_config()

        self._build_ui()
        self._center_window()

        self._kb_hooked = False     # the keyboard-hook fallback is in use
        self._bind_hotkey(self.config.get("hotkey", "None"))

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
                "Higher = snappier. %d ≈ %d ms per colour change. Default: 55."
                % (self.live_smooth, round(smooth_settle_ms(self.live_smooth))))
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
        self.live_algo = self.algo_var.get()
        self.live_tray = self.tray_var.get()
        self.live_audio = self.audio_var.get()
        self.live_sync = self.sync_var.get()
        self.live_display_sync = self.display_sync_var.get()
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
            self.config["sync_devices"] = self.sync_var.get()
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
        self.hotkey_btn.config(bg="#16213e", fg="#4ecca3")
        if hk and "unknown" not in hk.lower():
            self.hotkey_var.set(hk)
            self.config["hotkey"] = hk
            self._bind_hotkey(hk)
            self._save_config()
        else:
            self.hotkey_var.set(self.config.get("hotkey", "None"))
            self._bind_hotkey(self.config.get("hotkey", "None"))

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
        self.fps_var = tk.StringVar(value=str(self.config.get("fps", "60")))
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
        self.smooth_var = tk.StringVar(value=str(self.config.get("smoothness", 55)))
        self.smooth_entry = tk.Entry(left_col, textvariable=self.smooth_var, bg="#16213e", fg="#ffffff", insertbackground="#ffffff", font=("Segoe UI", 10), width=10, relief="flat", bd=0, highlightthickness=1, highlightbackground="#333355", highlightcolor="#4ecca3")
        self.smooth_entry.pack(anchor="w", pady=(0, 2), ipady=6, ipadx=6)
        self.smooth_hint_var = tk.StringVar()
        ttk.Label(left_col, textvariable=self.smooth_hint_var, style="SmallSub.TLabel").pack(anchor="w", pady=(0, 16))
        self._update_smooth_hint()

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

        # -- Device Sync
        self.sync_var = tk.BooleanVar(value=self.config.get("sync_devices", False))
        self.sync_cb = ttk.Checkbutton(left_col, text="Sync all devices", variable=self.sync_var, style="Dark.TCheckbutton")
        self.sync_cb.pack(anchor="w", pady=(0, 2))
        ttk.Label(left_col, text="Holds the fast devices back so every device changes colour at the same instant. Costs the slowest device's lag (~185 ms here) on all of them. Default: off.", style="SmallSub.TLabel", wraplength=340, justify="left").pack(anchor="w", pady=(0, 16))

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
        self.sync_var.trace_add("write", self._on_setting_change)
        self.display_sync_var.trace_add("write", self._on_setting_change)
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
        if self.running or self._connecting:
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
        self._connecting = True
        self._start_token += 1
        token = self._start_token
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
                                 status_cb=status)
            except (OpenRGBUnavailable, SystemExit) as exc:
                msg = str(exc) or "OpenRGB SDK Server not found."
                self.root.after(0, lambda: self._on_error(token, msg))
                return
            except Exception as exc:
                self.root.after(0, lambda e=exc: self._on_error(token, "Startup failed: %s" % e))
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
                self.root.after(0, lambda e=exc: self._on_error(token, "Stopped: %s" % e))

        self.thread = threading.Thread(target=connect_and_run, daemon=True)
        self.thread.start()

    def _connect_status(self, token, msg):
        if token == self._start_token and not self.running:
            self.status_var.set(msg)

    def _on_started(self):
        self._autostart_done = True
        self._connecting = False
        self.btn.config(text="⏹  STOP", bg="#e94560", fg="#ffffff", activebackground="#d63450", state="normal")
        self.monitor_combo.config(state="disabled")
        devs = len(self.ambilight.devices) if self.ambilight else 0
        feats = []
        if self.live_edges: feats.append("edges")
        if self.live_bars: feats.append("skip-bars")
        if self.live_audio: feats.append("audio")
        extra = f"  •  {', '.join(feats)}" if feats else ""
        self.status_var.set(f"Running  •  {devs} device(s){extra}")
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
        hidden in the tray. This polls at a rate the eye cannot beat and skips
        the repaint entirely while the window is not on screen.
        """
        self._bar_job = None
        if not self.running:
            return
        color = self._bar_color
        if color is not None and color != self._bar_shown and self.root.winfo_viewable():
            self._bar_shown = color
            self.color_bar.config(bg="#%02x%02x%02x" % color)
        self._bar_job = self.root.after(COLOR_BAR_REFRESH_MS, self._refresh_color_bar)

    def _on_error(self, token, msg):
        if token != self._start_token:
            return          # superseded by a stop or a newer start
        self.running = False
        self._connecting = False
        self.ambilight = None
        self.btn.config(text="▶  START", bg="#4ecca3", fg="#1a1a2e",
                        activebackground="#3dbb92", state="normal")
        self.monitor_combo.config(state="readonly")
        self.status_var.set(msg)

    def _stop(self):
        self._start_token += 1      # discard a connect thread still waiting
        self._connecting = False
        self._autostart_done = True # a deliberate stop cancels auto-start
        self.running = False
        if self.ambilight:
            self.ambilight.stop()
        self.btn.config(text="▶  START", bg="#4ecca3", fg="#1a1a2e", activebackground="#3dbb92")
        self.monitor_combo.config(state="readonly")
        self.status_var.set("Stopped")
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
                    frame = grabber.grab()
                except Exception:
                    import traceback
                    try:
                        with open(os.path.join(os.path.dirname(CONFIG_FILE), "ambi_debug.log"), "a") as f:
                            f.write(traceback.format_exc() + "\n")
                    except OSError:
                        pass
                    time.sleep(1)
                    continue

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

                if frame is not None:
                    pixels = frame
                elif pixels is None:
                    time.sleep(interval)
                    continue

                # A still screen (frame is None) keeps easing towards the
                # colour it already has; it is only analysed again when a
                # setting that shapes the analysis changes.
                settings = (self.live_bars, self.live_black_thresh, self.live_edges,
                            self.live_algo, self.live_sat, self.live_gamma)
                if frame is not None or settings != analysed_with:
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

                if idle_streak >= DEEP_IDLE_LIMIT:
                    time.sleep(DEEP_IDLE_INTERVAL)
                elif idle_streak >= IDLE_STREAK_LIMIT:
                    s = interval * IDLE_MULTIPLIER - elapsed
                    if s > 0: time.sleep(s)
                else:
                    s = interval - elapsed
                    if s > 0: time.sleep(s)
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



# ===========================================================================
#  Entry point
# ===========================================================================

# Any engine flag implies the headless path, so `python Ambilight.pyw --test`
# needs no --cli of its own. Everything else -- no arguments at all, or the
# tray-start flag -- opens the window, which is what a double-click hits.
_CLI_FLAGS = frozenset((
    "--cli", "--list", "--test", "--restore", "--fps", "--monitor", "--width",
    "--brightness", "--smoothness", "--edges", "--skip-bars", "--wait",
    "--sync",
))


def _wants_cli(argv):
    return any(a.split("=", 1)[0] in _CLI_FLAGS for a in argv)


if __name__ == "__main__":
    if _wants_cli(sys.argv[1:]):
        sys.argv = [a for a in sys.argv if a != "--cli"]
        main()
    else:
        AmbilightGUI().run()
