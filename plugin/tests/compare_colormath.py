"""Hold the plugin's C++ frame analysis against Ambilight.pyw, bit for bit.

Generates a few thousand frames (random, dark, letterboxed, flat, nearly
grey with a few vivid pixels, palettes full of ties), runs the analysis
step of Ambilight.pyw's capture loop on each in Python and through
colormath_cli.exe, and compares every number.

    python compare_colormath.py --cli <colormath_cli.exe> --work <dir>

Exit status 0 when everything that must match does. pow() lives in two
different C runtimes here (MSVC's ucrt for Python, MinGW's for the plugin),
so gamma and the smoothing factor are allowed a 1-ulp-class difference and
are reported separately.
"""
import argparse
import importlib.machinery
import importlib.util
import math
import os
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
loader = importlib.machinery.SourceFileLoader("ambilight", str(ROOT / "Ambilight.pyw"))
spec = importlib.util.spec_from_loader(loader.name, loader)
engine = importlib.util.module_from_spec(spec)
loader.exec_module(engine)

ALGOS = ["Dominant", "Average", "Center Focus"]


def python_analysis(frame, bars, threshold, edges, algo):
    """The analysis block of AmbilightGUI._run_loop, verbatim."""
    px = frame
    top, bot = 0, frame.shape[0]
    if bars:
        px = engine.trim_letterbox(px, threshold=threshold)
        offset = px.__array_interface__["data"][0] - frame.__array_interface__["data"][0]
        top = offset // (frame.shape[1] * 3)
        bot = top + px.shape[0]
    if edges:
        px = engine.extract_edges(px, ratio=0.15)

    if ALGOS[algo] == "Average":
        raw = tuple(px.reshape(-1, 3).mean(axis=0).tolist())
    elif ALGOS[algo] == "Center Focus" and px.ndim == 3:
        ch, cw = px.shape[0], px.shape[1]
        center = px[ch // 4:3 * ch // 4, cw // 4:3 * cw // 4]
        raw = engine.get_dominant_color(center if center.size > 0 else px)
    else:
        raw = engine.get_dominant_color(px)

    lum = 0.299 * raw[0] + 0.587 * raw[1] + 0.114 * raw[2]
    sat = engine.apply_saturation(raw, boost=1.5)
    gamma = engine.apply_gamma(raw, gamma=1.8)
    return raw, (top, bot), sat, gamma, lum


def make_frames(rng):
    """Yield (frame, kind) pairs covering the paths through the analysis."""
    sizes = [(128, 72)] * 6 + [(96, 54), (64, 36), (37, 23), (7, 5), (10, 10),
                               (2, 3), (1, 1), (128, 1), (1, 72), (13, 4)]
    kinds = ["random", "dark", "letterbox", "flat", "few_vivid", "palette",
             "gradient", "lowcontrast", "letterbox_thin", "black"]
    for i in range(1700):
        w, h = sizes[rng.integers(len(sizes))]
        kind = kinds[i % len(kinds)]
        if kind == "random":
            f = rng.integers(0, 256, (h, w, 3), dtype=np.uint8)
        elif kind == "dark":
            f = rng.integers(0, 31, (h, w, 3), dtype=np.uint8)
        elif kind in ("letterbox", "letterbox_thin"):
            f = rng.integers(0, 256, (h, w, 3), dtype=np.uint8)
            band = rng.integers(0, max(1, h // 2) + 1)
            if kind == "letterbox_thin":
                band = rng.integers(max(0, h // 2 - 3), max(1, h // 2) + 1)
            noise = rng.integers(0, 25, (band, w, 3), dtype=np.uint8)
            f[:band] = noise
            f[h - band:] = rng.integers(0, 25, (band, w, 3), dtype=np.uint8)
        elif kind == "flat":
            f = np.empty((h, w, 3), dtype=np.uint8)
            f[:] = rng.integers(0, 256, 3, dtype=np.uint8)
        elif kind == "few_vivid":
            f = np.full((h, w, 3), rng.integers(40, 200), dtype=np.uint8)
            f += rng.integers(0, 3, (h, w, 3), dtype=np.uint8)
            n = int(rng.integers(1, 16))
            ys = rng.integers(0, h, n)
            xs = rng.integers(0, w, n)
            f[ys, xs] = rng.integers(0, 256, (n, 3), dtype=np.uint8)
        elif kind == "palette":
            pal = rng.integers(0, 256, (int(rng.integers(2, 6)), 3), dtype=np.uint8)
            f = pal[rng.integers(0, len(pal), (h, w))]
        elif kind == "gradient":
            x = np.linspace(0, 255, w, dtype=np.float64)
            y = np.linspace(0, 255, h, dtype=np.float64)
            f = np.empty((h, w, 3), dtype=np.uint8)
            f[..., 0] = x[None, :].astype(np.uint8)
            f[..., 1] = y[:, None].astype(np.uint8)
            f[..., 2] = rng.integers(0, 256)
        elif kind == "lowcontrast":
            base = rng.integers(0, 256, 3)
            f = np.clip(base + rng.integers(-4, 5, (h, w, 3)), 0, 255).astype(np.uint8)
        else:
            f = np.zeros((h, w, 3), dtype=np.uint8)
        yield np.ascontiguousarray(f), kind


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cli", required=True)
    ap.add_argument("--work", required=True)
    args = ap.parse_args()

    rng = np.random.default_rng(20261122)
    cases = []
    for frame, kind in make_frames(rng):
        bars = int(rng.integers(0, 2))
        threshold = int(rng.choice([1, 20, 20, 50, 100]))
        edges = int(rng.integers(0, 2))
        algo = int(rng.integers(0, 3))
        cases.append((frame, kind, bars, threshold, edges, algo))
    # A systematic sweep of every option on a few full-size frames.
    for frame, kind in list(make_frames(np.random.default_rng(7)))[:40]:
        for bars in (0, 1):
            for threshold in (20, 60):
                for edges in (0, 1):
                    for algo in (0, 1, 2):
                        cases.append((frame, kind, bars, threshold, edges, algo))

    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    blob = work / "colormath_cases.bin"
    with open(blob, "wb") as out:
        out.write(b"AMBI" + struct.pack("<I", len(cases)))
        for frame, _, bars, threshold, edges, algo in cases:
            h, w = frame.shape[:2]
            out.write(struct.pack("<iiBiBB", w, h, bars, threshold, edges, algo))
            out.write(frame.tobytes())

    result = subprocess.run([args.cli, str(blob)], capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stderr)
        sys.exit("colormath_cli failed")
    lines = result.stdout.splitlines()

    hard = soft = 0
    worst_gamma = 0
    for i, (frame, kind, bars, threshold, edges, algo) in enumerate(cases):
        raw, rows, sat, gamma, lum = python_analysis(frame, bars, threshold, edges, algo)
        c_raw, c_rows, c_sat, c_gamma, c_lum = [p.split() for p in lines[i].split("|")]
        c_raw = tuple(float(v) for v in c_raw)
        c_rows = tuple(int(v) for v in c_rows)
        c_sat = tuple(int(v) for v in c_sat)
        c_gamma = tuple(int(v) for v in c_gamma)
        problems = []
        if tuple(float(v) for v in raw) != c_raw:
            problems.append("raw %r != %r" % (raw, c_raw))
        if bars and tuple(rows) != c_rows:
            problems.append("rows %r != %r" % (rows, c_rows))
        if tuple(sat) != c_sat:
            problems.append("sat %r != %r" % (sat, c_sat))
        if float(c_lum[0]) != lum:
            problems.append("lum %r != %r" % (lum, c_lum[0]))
        if problems:
            hard += 1
            if hard <= 10:
                print("MISMATCH case %d (%s, %dx%d, bars=%d thr=%d edges=%d algo=%s): %s"
                      % (i, kind, frame.shape[1], frame.shape[0], bars, threshold, edges,
                         ALGOS[algo], "; ".join(problems)))
        if tuple(gamma) != c_gamma:
            diff = max(abs(a - b) for a, b in zip(gamma, c_gamma))
            worst_gamma = max(worst_gamma, diff)
            soft += 1

    # Smoothing and pacing probes.
    probe = lines[len(cases):]
    alpha_worst = settle_worst = 0.0
    rest_bad = 0
    for line in probe:
        parts = line.split()
        if parts[0] == "settle":
            s = int(parts[1])
            want = engine.smooth_settle_ms(s)
            got = float(parts[2])
            settle_worst = max(settle_worst, abs(want - got) / max(1e-300, abs(want)) if want else abs(got))
        elif parts[0] == "alpha":
            s, dt, got = int(parts[1]), float(parts[2]), float(parts[3])
            want = engine.smooth_alpha(s, dt)
            alpha_worst = max(alpha_worst, abs(want - got))
        elif parts[0] == "rest":
            streak, interval, elapsed, still = int(parts[1]), float(parts[2]), float(parts[3]), bool(int(parts[4]))
            want = engine.loop_rest(streak, interval, elapsed, still)
            got = (float(parts[5]), float(parts[6]))
            if tuple(want) != got:
                rest_bad += 1
                print("loop_rest mismatch", parts, want)

    print("cases: %d   exact mismatches: %d" % (len(cases), hard))
    print("gamma differs in %d case(s), by at most %d" % (soft, worst_gamma))
    print("smooth_alpha worst abs diff: %.3g   smooth_settle_ms worst rel diff: %.3g"
          % (alpha_worst, settle_worst))
    print("loop_rest mismatches: %d" % rest_bad)
    ok = hard == 0 and rest_bad == 0 and worst_gamma <= 1 and alpha_worst < 1e-12 and settle_worst < 1e-12
    print("RESULT:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
