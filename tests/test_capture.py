"""Capture loop pacing, and the screen grabber against the real desktop.

The grabber tests need a monitor and skip themselves without one; they open
no window and send nothing to OpenRGB.
"""
import ctypes
import importlib.machinery
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np

loader = importlib.machinery.SourceFileLoader(
    "ambilight", str(Path(__file__).resolve().parents[1] / "Ambilight.pyw"))
spec = importlib.util.spec_from_loader(loader.name, loader)
engine = importlib.util.module_from_spec(spec)
loader.exec_module(engine)


class LoopRestTests(unittest.TestCase):
    INTERVAL = 1 / 60

    def rest(self, streak, still, elapsed=0.002):
        return engine.loop_rest(streak, self.INTERVAL, elapsed, still)

    def test_active_loop_sleeps_out_the_frame_interval(self):
        for still in (False, True):
            sleep, wait = self.rest(0, still)
            self.assertAlmostEqual(sleep, self.INTERVAL - 0.002)
            self.assertEqual(wait, 0.0)

    def test_idle_on_a_changing_desktop_sleeps_and_never_waits(self):
        # Waiting for a frame here would return at once, every time.
        sleep, wait = self.rest(engine.IDLE_STREAK_LIMIT, still=False)
        self.assertAlmostEqual(sleep, self.INTERVAL * engine.IDLE_MULTIPLIER - 0.002)
        self.assertEqual(wait, 0.0)
        sleep, wait = self.rest(engine.DEEP_IDLE_LIMIT, still=False)
        self.assertEqual((sleep, wait), (engine.DEEP_IDLE_INTERVAL, 0.0))

    def test_idle_on_a_still_desktop_waits_for_the_next_frame(self):
        sleep, wait = self.rest(engine.IDLE_STREAK_LIMIT, still=True)
        self.assertEqual(sleep, 0.0)
        self.assertAlmostEqual(wait, self.INTERVAL * engine.IDLE_MULTIPLIER - 0.002)
        sleep, wait = self.rest(engine.DEEP_IDLE_LIMIT, still=True)
        self.assertEqual((sleep, wait), (0.0, engine.DEEP_IDLE_INTERVAL))

    def test_a_wait_is_never_longer_than_the_sleep_it_replaces(self):
        # The filter steps by elapsed time; a longer gap would be a bigger jump.
        for streak in (engine.IDLE_STREAK_LIMIT, engine.DEEP_IDLE_LIMIT, 10 ** 6):
            self.assertEqual(self.rest(streak, still=True)[1], self.rest(streak, still=False)[0])

    def test_a_slow_frame_never_asks_for_a_negative_rest(self):
        self.assertEqual(self.rest(0, False, elapsed=1.0), (0.0, 0.0))
        self.assertEqual(self.rest(engine.IDLE_STREAK_LIMIT, True, elapsed=1.0), (0.0, 0.0))


class ScreenGrabberTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.monitors = engine.get_monitors()
        if not cls.monitors:
            raise unittest.SkipTest("no monitor to capture")

    def grabbers(self):
        for mon in self.monitors:
            grabber = engine.ScreenGrabber(mon)
            self.addCleanup(grabber.close)
            if grabber.backend != "DXGI":
                self.skipTest("Desktop Duplication is not available here")
            yield mon, grabber

    @staticmethod
    def expected_shape(mon, step):
        return (len(range(0, mon["height"], step)), len(range(0, mon["width"], step)), 3)

    def test_gpu_downsample_returns_the_pixels_of_a_full_readback(self):
        byref, VP = ctypes.byref, ctypes.c_void_p
        for mon, g in self.grabbers():
            if g._shaders is None:
                self.skipTest("this driver does not run the downsampling shader")
            width, height = g._size
            full = g._texture(width, height, engine._D3D11_USAGE_STAGING, 0,
                              engine._D3D11_CPU_ACCESS_READ)
            self.addCleanup(engine._com_release, full)

            # One desktop frame, taken both ways.
            resource = VP()
            engine._check(g._acquire(g._dup, 2000, byref(g._info), byref(resource)), "acquire")
            texture = engine._com_query(resource, engine._IID_ID3D11Texture2D, "texture")
            view = VP()
            engine._check(g._create_view(g._device, texture, None, byref(view)), "view")
            g._set_view(g._context, 0, 1, byref(view))
            g._draw(g._context, 3, 0)
            g._set_view(g._context, 0, 1, byref(VP()))
            g._copy(g._context, g._stage, g._target[0])
            g._copy(g._context, full, texture)
            for obj in (view, texture, resource):
                engine._com_release(obj)
            g._release_frame(g._dup)

            def read(stage, rows, stride):
                mapped = engine._D3D11_MAPPED_SUBRESOURCE()
                engine._check(g._map(g._context, stage, 0, 1, 0, byref(mapped)), "map")
                try:
                    raw = (ctypes.c_ubyte * (mapped.RowPitch * rows)).from_address(mapped.pData)
                    bgra = np.frombuffer(raw, dtype=np.uint8).reshape(rows, mapped.RowPitch // 4, 4)
                    return bgra[::stride, :width:stride, 2::-1].copy()
                finally:
                    g._unmap(g._context, stage, 0)

            sampled = read(full, height, g.step)
            shaded = read(g._stage, g._stage_rows, 1)[:, :sampled.shape[1]]
            self.assertEqual(shaded.shape, sampled.shape)
            self.assertTrue(np.array_equal(shaded, sampled),
                            "monitor %d: the shader and the full readback disagree" % mon["id"])

    def test_capture_comes_back_after_the_desktop_was_lost(self):
        for mon, g in self.grabbers():
            for with_device in (False, True):
                g._close_dxgi(device=with_device)
                g._retry_at = 0.0
                self.assertIsNot(g.grab(), g.LOST)
                self.assertIsNotNone(g._dup)

    def test_a_frame_has_one_pixel_per_step(self):
        for mon, g in self.grabbers():
            frame = None
            for _ in range(20):
                frame = g.grab(0.1)
                if isinstance(frame, np.ndarray):
                    break
            if frame is None:
                continue        # nothing on this monitor changed; nothing to check
            self.assertEqual(frame.shape, self.expected_shape(mon, g.step))
            self.assertTrue(frame.flags.c_contiguous)

    def test_gdi_fallback_when_duplication_cannot_be_opened(self):
        mon = self.monitors[0]
        with patch.object(engine.ScreenGrabber, "_open", side_effect=OSError("no DXGI")):
            g = engine.ScreenGrabber(mon)
        self.addCleanup(g.close)
        self.assertEqual(g.backend, "GDI")
        frame = g.grab()
        self.assertEqual(frame.shape, self.expected_shape(mon, g.step))
        self.assertTrue(frame.flags.c_contiguous)


if __name__ == "__main__":
    unittest.main()
