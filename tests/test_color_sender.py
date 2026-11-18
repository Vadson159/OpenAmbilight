"""Deterministic scheduling regressions; no OpenRGB server or LEDs needed."""
import importlib.machinery
import importlib.util
from pathlib import Path
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import patch

loader = importlib.machinery.SourceFileLoader(
    "ambilight", str(Path(__file__).resolve().parents[1] / "Ambilight.pyw"))
spec = importlib.util.spec_from_loader(loader.name, loader)
engine = importlib.util.module_from_spec(spec)
loader.exec_module(engine)


class ColorSenderTests(unittest.TestCase):
    def sender(self, sync=True):
        devices = [SimpleNamespace(id=i, name=name, leds=[None])
                   for i, name in enumerate(("slow", "medium", "fast"))]
        with patch.object(engine.threading, "Thread"), patch.object(
                engine, "DEVICE_LATENCY_MS", {"slow": 200, "medium": 80, "fast": 10}):
            sender = engine.AsyncColorSender(None, devices, 1, sync=sync)
        self.sent = []
        self.now = 10.0

        def dispatch(writes):
            self.sent.extend((self.now, e["spec"][0], rgb) for e, rgb in writes)
            return [e for e, _ in writes]

        sender._dispatch = dispatch
        return sender

    def tick(self, sender, at, rgb=None):
        self.now = at
        if rgb is not None:
            sender._latest = (at, rgb)
        return sender._tick(at)

    def test_first_frame_and_inflight_frame_use_one_color(self):
        sender = self.sender()
        self.tick(sender, 10, (10, 20, 30))
        self.tick(sender, 10.12, (200, 150, 100))
        self.tick(sender, 10.19)
        self.assertEqual([r[1] for r in self.sent], [0, 1, 2])
        self.assertEqual({r[2] for r in self.sent}, {(10, 20, 30)})
        for stamp, dev, _ in self.sent:
            self.assertAlmostEqual(stamp + sender._plan[dev]["lat"], 10.2)

    def test_stream_has_identical_sequences_and_bounded_cadence(self):
        sender = self.sender()
        for i in range(2001):
            self.tick(sender, 10 + i / 1000, (i // 17, 50, 70))
        # Drain the frozen cohort without allowing another one to start.
        sender._sync = False
        while sender._frame:
            self.tick(sender, self.now + .001)
        per_device = [[r for r in self.sent if r[1] == dev] for dev in range(3)]
        self.assertEqual([r[2] for r in per_device[0]], [r[2] for r in per_device[1]])
        self.assertEqual([r[2] for r in per_device[0]], [r[2] for r in per_device[2]])
        for records in per_device:
            self.assertTrue(all(b[0] - a[0] >= .299 for a, b in zip(records, records[1:])))

    def test_skipped_first_write_is_retried_with_offsets_preserved(self):
        sender = self.sender()
        real_dispatch = sender._dispatch
        sender._dispatch = lambda writes: []
        self.tick(sender, 10, (20, 40, 60))
        self.assertIsNone(sender._plan[0]["last_rgb"])
        sender._dispatch = real_dispatch
        self.tick(sender, 10.04, (90, 90, 90))
        self.tick(sender, 10.161)
        self.tick(sender, 10.231)
        self.assertEqual([r[1] for r in self.sent], [0, 1, 2])
        self.assertEqual({r[2] for r in self.sent}, {(20, 40, 60)})
        self.assertAlmostEqual(self.sent[2][0] - self.sent[0][0], .191)

    def test_idle_waits_for_keepalive_and_wakes_for_final_color(self):
        sender = self.sender()
        self.tick(sender, 10, (1, 2, 3))
        self.tick(sender, 10.12)
        self.tick(sender, 10.19)
        self.assertEqual(self.tick(sender, 10.3), 11)
        self.assertEqual(len(self.sent), 3)
        self.tick(sender, 10.4, (4, 5, 6))
        self.tick(sender, 10.521)
        self.tick(sender, 10.591)
        self.assertEqual([r[2] for r in self.sent[-3:]], [(4, 5, 6)] * 3)

    def test_enabling_sync_drains_independent_writes(self):
        sender = self.sender(sync=False)
        self.tick(sender, 10, (10, 20, 30))
        sender.set_sync(True)
        self.assertAlmostEqual(self.tick(sender, 10.05, (20, 30, 40)), 10.3)
        self.assertEqual(len(self.sent), 3)
        self.tick(sender, 10.3)
        self.assertEqual(self.sent[-1][1:], (0, (20, 30, 40)))

    def test_disabling_sync_finishes_current_cohort(self):
        sender = self.sender()
        self.tick(sender, 10, (10, 20, 30))
        sender.set_sync(False)
        self.tick(sender, 10.12, (200, 150, 100))
        self.tick(sender, 10.19)
        self.assertEqual([r[2] for r in self.sent], [(10, 20, 30)] * 3)

    def test_failed_independent_write_does_not_suppress_retry(self):
        sender = self.sender(sync=False)
        real_dispatch = sender._dispatch
        sender._dispatch = lambda writes: []
        self.tick(sender, 10, (10, 20, 30))
        self.assertTrue(all(e["last_rgb"] is None for e in sender._plan))
        sender._dispatch = real_dispatch
        self.tick(sender, 10.005)
        self.assertEqual(len(self.sent), 3)

    def test_packet_contains_all_leds_and_correct_lengths(self):
        blob = engine.build_update_blob([((7, 2), (11, 22, 33))])
        self.assertEqual(struct.unpack("<4sIII", blob[:16]), (b"ORGB", 7, 1050, 14))
        self.assertEqual(struct.unpack("<IH", blob[16:22]), (14, 2))
        self.assertEqual(blob[22:], bytes((11, 22, 33, 0)) * 2)

    def test_sequential_msi_zones_align_midpoint_but_keep_full_busy_time(self):
        dev = SimpleNamespace(name="MSI MYSTIC LIGHT",
                              metadata=SimpleNamespace(description="MSI Mystic Light Device (761-byte)"),
                              zones=[SimpleNamespace(leds=[None] * n) for n in (0, 0, 60, 60)])
        latency, busy = engine.device_timing_ms(dev)
        self.assertEqual((latency, busy), (138.75, 185.0))
        entry = engine.AsyncColorSender._entry(dev, (2, 120), latency, busy)
        self.assertAlmostEqual(entry["period"], .2775)

    def test_raw_socket_lock_contention_is_retried(self):
        sender = self.sender()
        sender._dispatch = engine.AsyncColorSender._dispatch.__get__(sender)
        lock = engine.threading.Lock()
        calls = []
        sender._client = SimpleNamespace(comms=SimpleNamespace(
            lock=lock, sock=SimpleNamespace(sendall=calls.append)))
        with lock:
            self.tick(sender, 10, (1, 2, 3))
        self.assertEqual(calls, [])
        self.assertIsNone(sender._plan[0]["last_rgb"])
        self.tick(sender, 10.005)
        self.assertEqual(len(calls), 1)
        self.assertEqual(sender._plan[0]["last_rgb"], (1, 2, 3))

    def test_disconnected_hid_keyboard_does_not_stall_other_devices(self):
        sender = self.sender()
        entry = engine.AsyncColorSender._entry(None, None, 30)
        sender._plan.append(entry)
        sender._keyboard = SimpleNamespace(available=True)
        self.tick(sender, 10, (1, 2, 3))
        sender._keyboard.available = False
        self.tick(sender, 10.12)
        self.tick(sender, 10.19)
        self.assertIsNone(sender._frame)
        self.assertEqual(len(sender._plan), 3)


if __name__ == "__main__":
    unittest.main()
