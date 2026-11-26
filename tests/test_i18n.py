"""Localization tests for Ambilight.pyw (English & Russian)."""
import importlib.machinery
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

loader = importlib.machinery.SourceFileLoader(
    "ambilight_i18n", str(Path(__file__).resolve().parents[1] / "Ambilight.pyw"))
spec = importlib.util.spec_from_loader(loader.name, loader)
app = importlib.util.module_from_spec(spec)
loader.exec_module(app)


class LocalizationTests(unittest.TestCase):
    def test_en_and_ru_have_identical_key_sets(self):
        self.assertEqual(set(app._UI_STRINGS["en"].keys()), set(app._UI_STRINGS["ru"].keys()))
        for lang in ("en", "ru"):
            for key, val in app._UI_STRINGS[lang].items():
                self.assertTrue(isinstance(val, str) and val, f"{lang}.{key} must be non-empty")

    def test_normalize_language(self):
        for raw in ("en", "EN", "en_US", "en-US", "English", "  english "):
            self.assertEqual(app.normalize_language(raw), "en")
        for raw in ("ru", "RU", "ru_RU", "ru-RU", "Russian", "Русский", "Русский - RU"):
            self.assertEqual(app.normalize_language(raw), "ru")
        for raw in ("auto", "", "System Default", "de", None, 123):
            self.assertEqual(app.normalize_language(raw), "auto")

    def test_resolve_language_respects_override_and_auto(self):
        with patch.object(app, "detect_system_language", return_value="ru"):
            self.assertEqual(app.resolve_language("auto"), "ru")
            self.assertEqual(app.resolve_language("en"), "en")
            self.assertEqual(app.resolve_language("ru"), "ru")
        with patch.object(app, "detect_system_language", return_value="en"):
            self.assertEqual(app.resolve_language("auto"), "en")
            self.assertEqual(app.resolve_language("ru"), "ru")

    def test_device_count_pluralization(self):
        self.assertEqual(app.format_device_count(0, "en"), "0 devices")
        self.assertEqual(app.format_device_count(1, "en"), "1 device")
        self.assertEqual(app.format_device_count(4, "en"), "4 devices")

        self.assertEqual(app.format_device_count(0, "ru"), "0 устройств")
        self.assertEqual(app.format_device_count(1, "ru"), "1 устройство")
        self.assertEqual(app.format_device_count(2, "ru"), "2 устройства")
        self.assertEqual(app.format_device_count(4, "ru"), "4 устройства")
        self.assertEqual(app.format_device_count(5, "ru"), "5 устройств")
        self.assertEqual(app.format_device_count(11, "ru"), "11 устройств")
        self.assertEqual(app.format_device_count(12, "ru"), "12 устройств")
        self.assertEqual(app.format_device_count(21, "ru"), "21 устройство")
        self.assertEqual(app.format_device_count(22, "ru"), "22 устройства")
        self.assertEqual(app.format_device_count(25, "ru"), "25 устройств")

    def test_gui_live_language_switching(self):
        with patch.object(app, "get_monitors", return_value=[{"id": 0, "left": 0, "top": 0, "width": 2560, "height": 1440}]), \
                patch.object(app.AmbilightGUI, "_load_config", lambda self: None), \
                patch.object(app.AmbilightGUI, "_save_config", lambda self: None), \
                patch.object(app.AmbilightGUI, "_bind_hotkey", lambda self, hk: None):
            gui = app.AmbilightGUI()
            try:
                gui.root.withdraw()

                gui.lang_combo.current(app.LANG_CODES.index("en"))
                gui._on_language_change()
                self.assertEqual(gui.lang, "en")
                self.assertEqual(gui.display_lbl.cget("text"), "Display")
                self.assertEqual(gui.btn.cget("text"), "▶  START")
                self.assertEqual(gui.status_var.get(), "Stopped")
                self.assertEqual(gui.hotkey_var.get(), "None")
                self.assertEqual(gui.live_algo, "Dominant")

                gui.algo_combo.current(2)
                gui._on_setting_change()
                self.assertEqual(gui.live_algo, "Center Focus")

                gui.lang_combo.current(app.LANG_CODES.index("ru"))
                gui._on_language_change()
                self.assertEqual(gui.lang, "ru")
                self.assertEqual(gui.display_lbl.cget("text"), "Монитор")
                self.assertEqual(gui.btn.cget("text"), "▶  СТАРТ")
                self.assertEqual(gui.status_var.get(), "Остановлено")
                self.assertEqual(gui.hotkey_var.get(), "Нет")
                self.assertEqual(gui.live_algo, "Center Focus")
                self.assertEqual(gui.algo_var.get(), "Центр экрана")
                self.assertIn("мс", gui.smooth_hint_var.get())
            finally:
                gui.root.destroy()


if __name__ == "__main__":
    unittest.main()
