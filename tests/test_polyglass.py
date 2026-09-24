"""Tests for Polyglass's logic. Run from the app folder:

    .venv\\Scripts\\python -m unittest -v

The translation tests use the installed offline models and are skipped when one is missing.
"""
import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import model_sizes  # noqa: E402
import polyglass  # noqa: E402
import setup_wizard  # noqa: E402

Overlay, Translator = polyglass.Overlay, polyglass.Translator


def pair(source, target, side, code):
    """Overlay.next_pair without building the overlay window."""
    return Overlay.next_pair(SimpleNamespace(source=source, target=target), side, code)


class LanguagePicking(unittest.TestCase):
    def test_pick_a_new_from(self):
        self.assertEqual(pair("es", "en", "from", "fr"), ("fr", "en"))

    def test_pick_a_new_to(self):
        self.assertEqual(pair("es", "en", "to", "de"), ("es", "de"))

    def test_picking_the_other_side_swaps(self):
        # Like Google Translate: choosing To's language as From swaps them, and vice versa.
        self.assertEqual(pair("es", "en", "from", "en"), ("en", "es"))
        self.assertEqual(pair("es", "en", "to", "es"), ("en", "es"))

    def test_detect_then_pick_the_to_language_as_from(self):
        self.assertEqual(pair("auto", "en", "from", "en"), ("en", "es"))
        self.assertEqual(pair("auto", "es", "from", "es"), ("es", "en"))

    def test_detect(self):
        self.assertEqual(pair("es", "en", "from", "auto"), ("auto", "en"))

    def test_routes_cover_both_directions_for_swapping(self):
        self.assertEqual(Overlay.routes("es", "en"), [("es", "en"), ("en", "es")])

    def test_routes_when_detecting(self):
        self.assertEqual(Overlay.routes("auto", "en"), [])
        self.assertEqual(Overlay.routes("auto", "es"), [("en", "es")])


class Routing(unittest.TestCase):
    MODELS = {("es", "en"): "es>en", ("en", "es"): "en>es", ("en", "fr"): "en>fr",
              ("fr", "en"): "fr>en", ("zh", "en"): "zh>en"}

    def test_direct(self):
        self.assertEqual(Translator.route("es", "en", self.MODELS), ["es>en"])

    def test_through_english(self):
        self.assertEqual(Translator.route("es", "fr", self.MODELS), ["es>en", "en>fr"])

    def test_missing(self):
        self.assertIsNone(Translator.route("ja", "en", self.MODELS))
        self.assertIsNone(Translator.route("en", "zh", self.MODELS))

    def test_ready(self):
        self.assertTrue(Translator.ready("fr", "es", self.MODELS))
        self.assertTrue(Translator.ready("en", "en", self.MODELS))
        self.assertFalse(Translator.ready("ja", "en", self.MODELS))

    def test_traditional_chinese_falls_back_to_simplified(self):
        self.assertTrue(Translator.ready("zt", "en", self.MODELS))


class FakeFont:
    """10 px per character."""
    def measure(self, text):
        return 10 * len(text)


class Wrapping(unittest.TestCase):
    wrap = staticmethod(lambda text, width: Overlay.wrap(None, text, FakeFont(), width))

    def test_wraps_between_words(self):
        self.assertEqual(self.wrap("Net profits have tripled", 120), ["Net profits", "have tripled"])

    def test_single_long_word_is_not_split(self):
        # "Confidential" used to come out as "Confiden" / "tial".
        self.assertEqual(self.wrap("Confidential", 50), ["Confidential"])

    def test_chinese_wraps_by_character(self):
        self.assertEqual(self.wrap("净利润增长", 30), ["净利润", "增长"])


class OcrPackScript(unittest.TestCase):
    def test_script_adds_and_records_the_pack(self):
        cmd = setup_wizard.ocr_pack_command(["es-ES", "ko-KR"])
        self.assertIn("RunAs", cmd[-1])
        with open(os.path.join(os.environ["TEMP"], "polyglass_add_ocr.ps1"), encoding="utf-8-sig") as f:
            script = f.read()
        self.assertIn("@('es-ES','ko-KR')", script)
        self.assertIn("Add-WindowsCapability", script)
        self.assertIn(setup_wizard.OCR_ADDED, script)

    def test_every_pack_language_is_offered_in_the_bar(self):
        self.assertTrue(set(polyglass.OCR_PACK) <= set(polyglass.LANG_ORDER))
        self.assertEqual(polyglass.OCR_PACK, {c: t for _, t, c in setup_wizard.LANGUAGES
                                              if t and c not in ("zh", "zt", "ja")})


class OcrPackErrors(unittest.TestCase):
    def reason(self, text):
        with tempfile.TemporaryDirectory() as d:
            log = os.path.join(d, "log.txt")
            if text is not None:
                with open(log, "w", encoding="utf-8") as f:
                    f.write(text)
            return Overlay.ocr_pack_error(log)

    def test_declined_prompt_leaves_no_log(self):
        self.assertIn("declined", self.reason(None))

    def test_windows_error_is_shown(self):
        self.assertEqual(self.reason("started\nes-ES failed: Add-WindowsCapability failed. Error code = 0x800f0954\n"),
                         "Add-WindowsCapability failed. Error code = 0x800f0954")

    def test_dism_error_code_preferred(self):
        log = ("started\nes-ES state: NotPresent, restart needed: False\nes-ES dism exit 50, state: NotPresent\n"
               "Error: 0x800f0954\nes-ES failed: Windows reports it as NotPresent\n")
        self.assertEqual(self.reason(log), "Error: 0x800f0954")

    def test_script_falls_back_to_dism(self):
        setup_wizard.ocr_pack_command(["es-ES"])
        with open(os.path.join(os.environ["TEMP"], "polyglass_add_ocr.ps1"), encoding="utf-8-sig") as f:
            self.assertIn("dism.exe /Online /Add-Capability", f.read())

    def test_added_but_not_visible_yet(self):
        self.assertIn("restart", self.reason("started\nes-ES added\n"))

    def test_script_logs_and_records_only_confirmed_installs(self):
        setup_wizard.ocr_pack_command(["es-ES"])
        with open(os.path.join(os.environ["TEMP"], "polyglass_add_ocr.ps1"), encoding="utf-8-sig") as f:
            script = f.read()
        self.assertIn(setup_wizard.OCR_LOG, script)
        self.assertIn("-ErrorAction Stop", script)
        # The pack is recorded for Uninstall.bat only after Windows reports it installed.
        self.assertLess(script.index("-eq 'Installed') {\n      Add-Content -LiteralPath $added"),
                        script.index("catch"))


class Setup(unittest.TestCase):
    def test_model_download_code_is_valid_python(self):
        code = setup_wizard.model_download_code([("es", "en"), ("en", "es")])
        compile(code, "<download>", "exec")
        self.assertIn("[('es', 'en'), ('en', 'es')]", code)
        self.assertNotIn("install_from_path", code)      # that one imports PyTorch

    def test_argos_is_installed_without_its_dependencies(self):
        with open(os.path.join(os.path.dirname(setup_wizard.__file__), "requirements.txt")) as f:
            reqs = f.read()
        self.assertNotIn("argostranslate", [l.split("=")[0].strip() for l in reqs.splitlines()])
        self.assertTrue(setup_wizard.ARGOS.startswith("argostranslate=="))



class DownloadSizes(unittest.TestCase):
    AVAILABLE = {("es", "en"), ("en", "es"), ("fr", "en"), ("en", "fr"), ("es", "pt"), ("en", "pt"),
                 ("pt", "en"), ("de", "en"), ("en", "de")}
    SIZES = {p: 100_000_000 for p in AVAILABLE}

    def test_nothing_needed_when_installed(self):
        self.assertEqual(model_sizes.needed("es", "en", self.AVAILABLE, {("es", "en")}), [])

    def test_installed_route_through_english_counts(self):
        self.assertEqual(model_sizes.needed("es", "fr", self.AVAILABLE, {("es", "en"), ("en", "fr")}), [])

    def test_direct_model_preferred(self):
        self.assertEqual(model_sizes.needed("es", "pt", self.AVAILABLE, set()), [("es", "pt")])

    def test_only_missing_legs_through_english(self):
        self.assertEqual(model_sizes.needed("fr", "es", self.AVAILABLE, {("en", "es")}), [("fr", "en")])

    def test_no_route(self):
        self.assertIsNone(model_sizes.needed("ja", "en", self.AVAILABLE, set()))

    def test_total_and_label(self):
        self.assertEqual(model_sizes.total([("es", "en"), ("en", "es")], self.SIZES), 200_000_000)
        self.assertIsNone(model_sizes.total([("ja", "en")], self.SIZES))
        self.assertEqual(model_sizes.label(240_400_000), "240 MB")
        self.assertEqual(model_sizes.label(1_240_000_000), "1.2 GB")

    def test_installed_pairs_reads_metadata(self):
        with tempfile.TemporaryDirectory() as d:
            for name, meta in [("a", {"from_code": "es", "to_code": "en"}),
                               ("b", {"type": "sbd", "from_code": "es", "to_code": "es"})]:
                os.makedirs(os.path.join(d, name))
                with open(os.path.join(d, name, "metadata.json"), "w") as f:
                    json.dump(meta, f)
            self.assertEqual(model_sizes.installed_pairs(d), {("es", "en")})

    def test_bar_size_covers_both_directions(self):
        # Picking German with English as To downloads de -> en and en -> de.
        me = SimpleNamespace(routes=Overlay.routes, model_index=dict.fromkeys(self.AVAILABLE, "url"),
                             model_sizes={("de", "en"): 150_000_000, ("en", "de"): 150_000_000})
        self.assertEqual(Overlay.download_size(me, ("de", "en"), {}), 300_000_000)
        me.model_sizes = {}
        self.assertIsNone(Overlay.download_size(me, ("de", "en"), {}))    # sizes not loaded yet

    def test_setup_pairs(self):
        self.assertEqual(setup_wizard.model_pairs("es", "en"), [("es", "en"), ("en", "es")])
        self.assertEqual(setup_wizard.model_pairs("es", "fr"), [("es", "en"), ("en", "fr"), ("fr", "en"), ("en", "es")])
        self.assertEqual(setup_wizard.model_pairs("auto", "en"), [])
        self.assertEqual(setup_wizard.model_pairs("auto", "de"), [("en", "de")])

    def test_live_sizes_from_the_model_server(self):
        index = model_sizes.load_index()
        if not index:
            self.skipTest("offline")
        sizes = model_sizes.fetch_sizes({("fr", "en"): index[("fr", "en")]}, {"fr", "en"})
        self.assertGreater(sizes.get(("fr", "en"), 0), 10_000_000)



class LiveModeDetection(unittest.TestCase):
    """New text on a still screen must trigger a rescan (it used to be missed entirely)."""

    @staticmethod
    def thumb(text=None, size=30):
        from PIL import Image, ImageDraw, ImageFont
        im = Image.new("RGB", (1920, 1080), "white")
        if text:
            ImageDraw.Draw(im).text((120, 300), text, fill="black",
                                    font=ImageFont.truetype("segoeui.ttf", int(size * 1.33)))
        return polyglass.np.asarray(im.convert("L").resize((160, 90)), dtype=polyglass.np.int16)

    def overlay(self, regions=()):
        return SimpleNamespace(last_thumb=self.thumb(), regions=list(regions), region_sigs=[],
                               exclude_from_capture=True, block_change=Overlay.block_change)

    def test_text_appearing_on_a_still_screen(self):
        me = self.overlay()
        for size in (14, 20, 30):
            self.assertTrue(Overlay.changed(me, None, self.thumb("Hola, viajero.", size)), size)

    def test_unchanged_screen(self):
        self.assertFalse(Overlay.changed(self.overlay(), None, self.thumb()))

    def test_new_text_elsewhere_after_a_translation(self):
        self.assertTrue(Overlay.changed(self.overlay(regions=[]), None, self.thumb("Vuelve pronto.")))


class Translation(unittest.TestCase):
    """Real offline translation with the installed models."""

    @classmethod
    def setUpClass(cls):
        cls.t = Translator(lambda msg: None)
        cls.models = Translator.installed_models()

    def check(self, src, dst, text, expect):
        if not Translator.installed(src, dst, self.models):
            self.skipTest(f"{src} -> {dst} model not installed")
        self.assertIn(expect, self.t.translate(src, dst, text))

    def test_spanish_to_english(self):
        # The model that int8 precision turned into "mainstremainstre...".
        self.check("es", "en", "Bienvenido a nuestra tienda en línea.", "Welcome to our online store")

    def test_english_to_spanish(self):
        self.check("en", "es", "Welcome to our online store.", "Bienvenido")

    def test_chinese_to_english(self):
        self.check("zh", "en", "净利润增长了两倍。", "tripled")

    def test_japanese_short_labels(self):
        # With unknown words allowed, いいえ came back untranslated and セーブ as "ブ".
        self.check("ja", "en", "いいえ", "No")
        self.assertEqual(self.t.translate("ja", "en", "セーブ").lower(), "save")

    def test_through_english(self):
        self.check("fr", "es", "Bienvenue dans notre boutique en ligne.", "tienda")

    def test_no_pytorch(self):
        self.assertNotIn("torch", sys.modules)
        self.assertNotIn("stanza", sys.modules)


if __name__ == "__main__":
    unittest.main()
