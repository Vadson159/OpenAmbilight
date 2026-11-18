"""Measure SDK dispatch and instrumented OpenRGB hardware write completion.

Stop the regular Ambilight instance first. OpenRGB must use loglevel 4 and
the FrameTiming instrumentation in OpenRGB-patched/patches/frame-timing.patch.
This changes the lights for the requested duration; it does not capture LEDs.
"""
import argparse
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import re
import time


def load_engine(path):
    loader = importlib.machinery.SourceFileLoader("ambilight_probe", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=root / "Ambilight.pyw")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=15)
    parser.add_argument("--sync", action="store_true")
    parser.add_argument("--pattern", choices=["steps", "stream"], default="steps")
    args = parser.parse_args()
    engine = load_engine(args.source)
    client = engine.OpenRGBClient(name="Ambilight timing measurement")
    import socket
    client.comms.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    log = max((root / "OpenRGB-patched/config/logs").glob("*.log"),
              key=lambda p: p.stat().st_mtime)
    offset = log.stat().st_size
    writes = []

    class MeasuredSender(engine.AsyncColorSender):
        def _send_raw(self, items):
            stamp = time.perf_counter()
            result = super()._send_raw(items)
            writes.extend({"time": stamp, "device": spec[0], "rgb": list(rgb),
                           "result": result} for spec, rgb in items)
            return result

    sender = MeasuredSender(client, client.devices, 1.0, sync=args.sync)
    start = time.perf_counter()
    inputs = []
    try:
        interval = 0.65 if args.pattern == "steps" else 1 / 60
        i = 0
        while time.perf_counter() - start < args.seconds:
            # All channels change; no repeated colors during a measurement.
            rgb = (24 + (i * 37) % 173, 24 + (i * 53) % 179,
                   24 + (i * 71) % 181)
            inputs.append({"time": time.perf_counter(), "rgb": rgb})
            sender.send(*rgb)
            i += 1
            time.sleep(max(0, start + i * interval - time.perf_counter()))
        time.sleep(0.7)  # allow the last cohort and in-flight hardware to finish
    finally:
        sender.stop()
        client.disconnect()
    with log.open("rb") as f:
        f.seek(offset)
        raw = f.read().decode("utf-8", errors="replace")
    regex = re.compile(r"\[FrameTiming\] (.*?) start=([\d.]+) duration=([\d.]+) "
                       r"rgb=([0-9A-F]+) end_rgb=([0-9A-F]+)")
    frames = []
    for name, stamp, duration, color, end_color in regex.findall(raw):
        packed = int(color, 16)
        frames.append({"device": name, "start": float(stamp) / 1000,
                       "duration_ms": float(duration),
                       "rgb": [(packed >> shift) & 255 for shift in (0, 8, 16)],
                       "changed_during_write": color != end_color})
    zones = []
    zone_regex = re.compile(r"\[ZoneTiming\] (.*?) start=([\d.]+) duration=([\d.]+) "
                            r"rgb=([0-9A-F]+) result=(-?\d+)")
    for name, stamp, duration, color, status in zone_regex.findall(raw):
        packed = int(color, 16)
        zones.append({"zone": name, "start": float(stamp) / 1000,
                      "duration_ms": float(duration), "result": int(status),
                      "rgb": [(packed >> shift) & 255 for shift in (0, 8, 16)]})
    result = {"source": str(args.source), "sync": args.sync,
              "pattern": args.pattern, "seconds": args.seconds,
              "devices": {d.id: d.name for d in client.devices},
              "inputs": inputs, "writes": writes, "frames": frames, "zones": zones}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"Saved {len(inputs)} inputs, {len(writes)} dispatches, "
          f"{len(frames)} hardware timings to {args.output}")
    for name in result["devices"].values():
        durations = sorted(f["duration_ms"] for f in frames if f["device"] == name)
        if durations:
            print(f"{name}: n={len(durations)}, mean={sum(durations)/len(durations):.2f} ms, "
                  f"p95={durations[int((len(durations)-1)*0.95)]:.2f} ms")


if __name__ == "__main__":
    main()
