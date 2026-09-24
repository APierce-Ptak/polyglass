"""
Polyglass - transparent click-through overlay that translates on-screen
text (Chinese, Japanese, Korean, Russian, Arabic, English, ... or other
Latin-script languages) into the language you choose, drawn in place over the
original.  Windows only.

Hotkeys
    Ctrl+Alt+T   translate the screen once
    Ctrl+Alt+L   toggle live mode (re-scans about every 1.5 s)
    Ctrl+Alt+D   swap the From and To languages
    Ctrl+Alt+C   clear the overlay
    Ctrl+Alt+Q   quit

Click the languages in the bar at the top to change them, like Google Translate:
installed ones show a check, others download when you pick them.

Settings live in polyglass.json next to this file (the setup wizard and the bar write it):
    "from"   the language on screen, or "auto" to detect it (e.g. "ja", "auto")
    "to"     the language to translate into (e.g. "en", "es")
    "monitor"  optional: which screen to cover, 1 = the first, 2 = the second, ...
               (leave it out for the main screen)
With "from" set to a language, the screen is read as that language; with "auto" it is detected.

How it works
    1. mss grabs the primary monitor.
    2. Windows' built-in OCR reads it, once per installed OCR language; the
       language whose script best matches the text wins.  Latin-script text
       falls back to langdetect.
       Text already in the output language is skipped.
    3. Argos Translate (offline) translates each line, pivoting through
       English when there is no direct model.
    4. A borderless, topmost, click-through Tk window (transparent colour key)
       paints the translations exactly where the source lines were.  The window is
       excluded from screen capture, so the OCR never reads its own output.
"""
import os
import sys

# Launched with pythonw (desktop shortcut): there is no console, so log to a file instead.
if sys.stdout is None or sys.stderr is None:
    _log = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "polyglass.log"),
                "a", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = _log
    if sys.stderr is None:
        sys.stderr = _log

import asyncio
import json
import traceback
import ctypes
import queue
import re
import subprocess
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import unicodedata
import zipfile
from ctypes import wintypes

if sys.platform != "win32":
    sys.exit("Polyglass only runs on Windows.")

# Must happen before Tk creates any window so coordinates are real pixels.
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    ctypes.windll.user32.SetProcessDPIAware()

import mss
import numpy as np
from PIL import Image

# Argos Translate is only used to download and install models; they run through
# CTranslate2 directly. (argostranslate.translate imports stanza, which needs PyTorch.)
import argostranslate.package
import ctranslate2

import model_sizes
from winsdk.windows.globalization import Language
from winsdk.windows.graphics.imaging import (BitmapAlphaMode, BitmapPixelFormat,
                                             SoftwareBitmap)
from winsdk.windows.media.ocr import OcrEngine
from winsdk.windows.storage.streams import DataWriter

try:
    from langdetect import DetectorFactory, detect_langs
    DetectorFactory.seed = 0
except Exception:  # optional
    detect_langs = None

TRANSPARENT = "#010101"
HINT = "Ctrl+Alt+  T translate  L live  D swap  C clear  Q quit"
LIVE_INTERVAL = 1.5
# Live mode compares a 160x90 grey thumbnail of the screen with the last scan's, in 16x9 blocks
# of 10x10 (about 120x120 screen pixels each). New text changes one block a lot but the whole
# screen hardly at all: a line of 14 pt text moves its block by about 7, the screen by 0.05.
BLOCK_THRESHOLD = 6.0
MIN_SCRIPT_FRACTION = 0.3

# Script prefixes (from unicodedata.name) used to pick the OCR language.
SCRIPTS = {
    "zh": ("CJK",), "ja": ("CJK", "HIRAGANA", "KATAKANA"), "ko": ("HANGUL",),
    "ru": ("CYRILLIC",), "uk": ("CYRILLIC",), "bg": ("CYRILLIC",),
    "sr": ("CYRILLIC",), "ar": ("ARABIC",), "fa": ("ARABIC",),
    "he": ("HEBREW",), "th": ("THAI",), "el": ("GREEK",), "hi": ("DEVANAGARI",),
}
# Languages offered in the bar's dropdowns, in menu order (same list as the setup wizard).
LANG_ORDER = ["en", "zh", "zt", "ja", "ko", "ru", "ar", "es", "fr", "de", "pt", "it"]
# Windows OCR packs for a chosen From language. Korean, Russian and Arabic can't be read
# without theirs; the Latin-script ones read their accents properly (English OCR reads the
# rest, but turns "llegan" into "Ilegan"). Chinese and Japanese use RapidOCR instead.
OCR_PACK = {"ko": "ko-KR", "ru": "ru-RU", "ar": "ar-SA",
            "es": "es-ES", "fr": "fr-FR", "de": "de-DE", "pt": "pt-BR", "it": "it-IT"}
NAMES = {"en": "English", "zh": "Chinese", "zt": "Chinese (Traditional)", "ja": "Japanese",
         "ko": "Korean", "ru": "Russian", "ar": "Arabic", "es": "Spanish", "fr": "French",
         "de": "German", "pt": "Portuguese", "it": "Italian"}
# Segoe UI has no CJK glyphs; use the matching Windows UI font for those outputs.
FONTS = {"ja": "Yu Gothic UI", "zh": "Microsoft YaHei UI", "zt": "Microsoft JhengHei UI",
         "ko": "Malgun Gothic"}
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "polyglass.json")
CJK_CHARS = re.compile(r"[⺀-鿿　-ヿ가-힯＀-￯]")
CJK_RE = re.compile(r"(?<=[⺀-鿿　-ヿ＀-￯]) +(?=[⺀-鿿　-ヿ＀-￯])")


def load_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except OSError as e:
        print(f"[config] could not save: {e}", flush=True)


def name(code):
    return NAMES.get(code, code.upper())


def family(code):
    """Treat Simplified and Traditional Chinese as one language when skipping text."""
    return "zh" if code in ("zh", "zt") else code


# --------------------------------------------------------------------------- OCR
def script_chars(text, prefixes):
    n = 0
    for ch in text:
        if ch.isspace():
            continue
        try:
            name = unicodedata.name(ch)
        except ValueError:
            continue
        if name.startswith(prefixes):
            n += 1
    return n


def primary(tag):
    return tag.split("-")[0].lower()


def argos_code(tag):
    p = primary(tag)
    if p == "zh" and any(s in tag for s in ("Hant", "TW", "HK", "MO")):
        return "zt"
    return {"nb": "nb", "no": "nb"}.get(p, p)


def make_bitmap(bgra, w, h):
    writer = DataWriter()
    writer.write_bytes(bytearray(bgra))
    bmp = SoftwareBitmap(BitmapPixelFormat.BGRA8, w, h, BitmapAlphaMode.PREMULTIPLIED)
    bmp.copy_from_buffer(writer.detach_buffer())
    return bmp


async def _ocr(engine, bmp):
    result = await engine.recognize_async(bmp)
    out = []
    for line in result.lines:
        words = list(line.words)
        if not words:
            continue
        x0 = min(w.bounding_rect.x for w in words)
        y0 = min(w.bounding_rect.y for w in words)
        x1 = max(w.bounding_rect.x + w.bounding_rect.width for w in words)
        y1 = max(w.bounding_rect.y + w.bounding_rect.height for w in words)
        out.append((line.text, (x0, y0, x1 - x0, y1 - y0)))
    return out


# ---------------------------------------------------------- RapidOCR (CJK, game text)
_rapid = None
_rapid_failed = False


def rapid_ocr(rgb):
    """Return (argos_code, [(text, rect)]) using RapidOCR, or (None, []) if unavailable/empty."""
    global _rapid, _rapid_failed
    if _rapid_failed:
        return None, []
    if _rapid is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
            _rapid = RapidOCR()
        except Exception as e:
            _rapid_failed = True
            print(f"[ocr] RapidOCR unavailable ({e}); using Windows OCR only.", flush=True)
            return None, []
    result, _ = _rapid(rgb)
    lines, kana = [], 0
    for box, text, score in (result or []):
        if float(score) < 0.5:
            continue
        n = script_chars(text, ("CJK", "HIRAGANA", "KATAKANA"))
        if not n or n / max(1, len(text.replace(" ", ""))) < MIN_SCRIPT_FRACTION:
            continue
        kana += script_chars(text, ("HIRAGANA", "KATAKANA"))
        xs = [pt[0] for pt in box]
        ys = [pt[1] for pt in box]
        lines.append((text, (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))))
    return ("ja" if kana else "zh"), lines


def ocr_all(bgra, w, h, rgb=None, target="en", source="auto"):
    """Return (argos_from_code, [(text, rect), ...]) for the text to translate, or (None, []).
    With a `source` language only that language is read; with "auto" the best language that
    is not already `target` wins."""
    cjk = source in ("zh", "zt", "ja")
    if rgb is not None and (source == "auto" or cjk):
        code, lines = rapid_ocr(rgb)
        print(f"[ocr] RapidOCR: {len(lines)} CJK lines", flush=True)
        # When detecting, a lone character (a tray icon, a logo) is not enough to win.
        enough = cjk or sum(len(t.replace(" ", "")) for t, _ in lines) >= 2
        if lines and enough and family(code) != family(target):
            return (source if cjk else code), lines
    bmp = make_bitmap(bgra, w, h)
    langs = list(OcrEngine.available_recognizer_languages)
    print("[ocr] installed OCR languages:", [l.language_tag for l in langs], flush=True)
    if not langs:
        raise RuntimeError("No Windows OCR languages installed (see README).")

    async def run():
        # Latin script (English, Spanish, French, ...): read once, with the pack for the From
        # language when it is installed (it knows that language's accents), else English.
        latin_tags = [l.language_tag for l in langs if SCRIPTS.get(primary(l.language_tag)) is None]
        latin = None
        if latin_tags:
            tag = next((t for t in latin_tags if primary(t) == source),
                       next((t for t in latin_tags if primary(t) == "en"), latin_tags[0]))
            print(f"[ocr] Latin text read with {tag}", flush=True)
            latin = (tag, await _ocr(OcrEngine.try_create_from_language(Language(tag)), bmp))
        best, best_score = None, 0
        for lang in langs:
            tag = lang.language_tag
            prefixes = SCRIPTS.get(primary(tag))
            if prefixes is None or (source != "auto" and SCRIPTS.get(family(source)) is None):
                continue                               # Latin, or a Latin From: nothing to add
            if family(argos_code(tag)) == family(target):
                continue                               # already in the output language
            if source != "auto" and family(argos_code(tag)) != family(source):
                continue                               # not the language picked in From
            engine = OcrEngine.try_create_from_language(Language(tag))
            if engine is None:
                continue
            kept, score = [], 0
            for text, rect in await _ocr(engine, bmp):
                n = script_chars(text, prefixes)
                if n and n / max(1, len(text.replace(" ", ""))) >= MIN_SCRIPT_FRACTION:
                    kept.append((text, rect))
                    score += n
            if score > best_score:
                best, best_score = (argos_code(tag), kept), score
        if best and best_score >= 2:
            return best
        if source != "auto":
            # A Latin-script From (Spanish, French, ...): read the screen as that language.
            return (source, latin[1]) if latin and SCRIPTS.get(source) is None and not cjk else (None, [])
        # Latin-script fallback: detect the language of the whole screen.
        if latin and detect_langs:
            joined = " ".join(t for t, _ in latin[1])
            if len(joined) > 20:
                try:
                    top = detect_langs(joined)[0]
                    if top.lang != target and top.prob > 0.8:
                        return top.lang, latin[1]
                except Exception:
                    pass
        return None, []

    return asyncio.run(run())


# ----------------------------------------------------------------- translation
def install_model(path):
    """Unpack a downloaded .argosmodel into Argos's models folder. (Argos's own
    install_from_path does the same, then imports its translate module, which needs PyTorch.)"""
    with zipfile.ZipFile(path) as z:
        z.extractall(argostranslate.package.settings.package_data_dir)
    os.remove(path)                    # the download isn't needed once it's unpacked


class Translator:
    def __init__(self, status):
        self.status = status
        self.cache = {}
        self.unavailable = set()
        self.engines = {}              # model folder -> loaded CTranslate2 model

    @staticmethod
    def installed_models():
        """{(from_code, to_code): package} for every installed translation model."""
        return {(p.from_code, p.to_code): p for p in argostranslate.package.get_installed_packages()
                if p.type == "translate"}

    @staticmethod
    def route(src, dst, models):
        """The models that translate src -> dst: a direct one, or two through English
        (e.g. ja -> en -> es). None when they aren't all installed."""
        if (src, dst) in models:
            return [models[(src, dst)]]
        if (src, "en") in models and ("en", dst) in models:
            return [models[(src, "en")], models[("en", dst)]]
        return None

    @staticmethod
    def installed(src, dst, models=None):
        models = Translator.installed_models() if models is None else models
        return Translator.route(src, dst, models) is not None

    @staticmethod
    def ready(src, dst, models):
        """True when src -> dst can be translated offline right now (Traditional Chinese
        can fall back to the Simplified models, like ensure() does)."""
        if src == dst:
            return True
        return any(Translator.installed(a, b, models)
                   for a in ({src, "zh"} if src == "zt" else {src})
                   for b in ({dst, "zh"} if dst == "zt" else {dst}))

    def ensure(self, code, target, quiet=False):
        """Make sure an offline route code -> target exists, downloading models if needed.
        Returns the source code to translate from (zt may fall back to zh), or None.
        `quiet` skips the "Downloading" message (the language bar shows its own)."""
        if (code, target) in self.unavailable:
            return None
        if self.installed(code, target):
            return code
        if not quiet:
            self.status(f"Downloading {name(code)} -> {name(target)} (first time only)...")
        try:
            argostranslate.package.update_package_index()
            pkgs = argostranslate.package.get_available_packages()
            find = lambda a, b: next((p for p in pkgs if p.from_code == a and p.to_code == b), None)
            direct = find(code, target)
            # No direct model: go through English, e.g. ja -> en -> es.
            route = [direct] if direct else [find(code, "en"), find("en", target)]
            if any(p is None for p in route) and code == "zt":
                return self.ensure("zh", target, quiet)
            if any(p is None for p in route):     # not "None in route": Package.__eq__ breaks on None
                self.unavailable.add((code, target))
                self.status(f"No offline model for {name(code)} -> {name(target)}.")
                return None
            for pkg in route:
                if not self.installed(pkg.from_code, pkg.to_code):
                    install_model(pkg.download())
            return code
        except Exception as e:
            self.status(f"Model download failed: {e}")
            return None

    def run_model(self, pkg, text):
        """Translate one line with one model, the way Argos Translate does it."""
        path = str(pkg.package_path / "model")
        if path not in self.engines:
            # "default" keeps the precision the model was saved in; int8 (what "auto" picks
            # on most CPUs) turns the Spanish -> English model's output into "mainstremainstre...".
            self.engines[path] = ctranslate2.Translator(path, device="cpu", compute_type="default")
        prefix = [[pkg.target_prefix]] if pkg.target_prefix else None
        result = self.engines[path].translate_batch(
            [pkg.tokenizer.encode(text)], target_prefix=prefix, replace_unknowns=True,
            beam_size=4, length_penalty=0.2)
        out = pkg.tokenizer.decode(result[0].hypotheses[0])
        if pkg.target_prefix and out.startswith(pkg.target_prefix):
            out = out[len(pkg.target_prefix):]
        return out.strip()

    def translate(self, code, target, text):
        text = CJK_RE.sub("", text).strip()
        if not text:
            return ""
        key = (code, target, text)
        if key not in self.cache:
            try:
                route = self.route(code, target, self.installed_models())
                out = text
                for pkg in route:
                    out = self.run_model(pkg, out)
                self.cache[key] = out
            except Exception:
                traceback.print_exc()
                self.cache[key] = text
        return self.cache[key]


# ------------------------------------------------------------------------ app
class Overlay:
    def __init__(self):
        self.root = tk.Tk()
        self.jobs = queue.Queue()      # results/status from the worker -> UI
        self.cmds = queue.Queue()      # hotkeys -> UI
        self.live = False
        self.busy = threading.Event()
        self.working = False
        self.last_thumb = None
        self.regions = []
        self.region_sigs = []
        self.translator = Translator(lambda m: self.jobs.put(("status", m)))
        self.config = load_config()
        self.source = self.config.get("from", "auto")
        self.target = self.config.get("to", "en")
        if self.source == self.target:
            self.source = "auto"

        with mss.MSS() as sct:
            screens = sct.monitors[1:]
        pick = self.config.get("monitor")
        mon = (screens[pick - 1] if isinstance(pick, int) and 1 <= pick <= len(screens)
               else next((m for m in screens if m.get("is_primary")), screens[0]))
        self.mon = mon
        r = self.root
        r.overrideredirect(True)
        r.attributes("-topmost", True)
        r.attributes("-transparentcolor", TRANSPARENT)
        r.config(bg=TRANSPARENT)
        r.geometry(f"{mon['width']}x{mon['height']}+{mon['left']}+{mon['top']}")
        self.canvas = tk.Canvas(r, bg=TRANSPARENT, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        r.update()
        self.exclude_from_capture = self._make_clickthrough()

        self.downloading = False
        self.model_index, self.model_sizes = {}, {}     # filled in the background, for the menus
        threading.Thread(target=self.load_model_sizes, daemon=True).start()
        self.start_hotkeys()
        self.build_bar()
        self.show_status(f"Ready: press Ctrl+Alt+T to translate into {name(self.target)}", 6000)
        self.root.after(6000, self.offer_ocr_pack)
        self.root.after(50, self.pump)
        threading.Thread(target=self.live_loop, daemon=True).start()

    def start_hotkeys(self):
        """Global Ctrl+Alt hotkeys through Windows' own RegisterHotKey. (pynput's hotkey
        matcher never fires for Ctrl+Alt+letter on Windows, because the letter arrives
        without its character while Ctrl and Alt are held.)"""
        keys = {"T": "once", "L": "live", "D": "swap", "C": "clear", "Q": "quit"}

        def loop():
            u = ctypes.windll.user32
            taken = [f"Ctrl+Alt+{k}" for i, k in enumerate(keys, 1)
                     if not u.RegisterHotKey(None, i, 0x0001 | 0x0002 | 0x4000, ord(k))]  # ALT|CONTROL|NOREPEAT
            if taken:
                self.jobs.put(("status", f"Another app is already using {', '.join(taken)}"))
            cmds = list(keys.values())
            msg = wintypes.MSG()
            while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == 0x0312:                                  # WM_HOTKEY
                    self.cmds.put(cmds[msg.wParam - 1])

        threading.Thread(target=loop, daemon=True).start()

    # -- window setup --------------------------------------------------------
    def _make_clickthrough(self):
        import os
        u = ctypes.windll.user32
        hwnd = u.GetParent(self.root.winfo_id()) or self.root.winfo_id()
        GWL_EXSTYLE = -20
        style = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
        style |= 0x00080000 | 0x00000020 | 0x00000080 | 0x08000000  # LAYERED|TRANSPARENT|TOOLWINDOW|NOACTIVATE
        u.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        # Tell Windows explicitly: colour #010101 is see-through, everything else fully opaque.
        u.SetLayeredWindowAttributes(hwnd, 0x010101, 255, 0x1)  # LWA_COLORKEY
        # Re-assert always-on-top without stealing focus.
        u.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010 | 0x0040)  # NOSIZE|NOMOVE|NOACTIVATE|SHOWWINDOW
        excluded = False
        if os.environ.get("POLYGLASS_EXCLUDE") != "0":
            excluded = bool(u.SetWindowDisplayAffinity(hwnd, 0x11))
        rect = wintypes.RECT()
        u.GetWindowRect(hwnd, ctypes.byref(rect))
        print(f"[overlay] hwnd={hwnd} visible={bool(u.IsWindowVisible(hwnd))} "
              f"rect=({rect.left},{rect.top},{rect.right},{rect.bottom}) excluded_from_capture={excluded}",
              flush=True)
        return excluded

    # -- UI thread -----------------------------------------------------------
    def pump(self):
        try:
            while True:
                try:
                    cmd = self.cmds.get_nowait()
                except queue.Empty:
                    break
                if cmd == "once":
                    self.request(force=True)
                elif cmd == "live":
                    self.live = not self.live
                    self.show_status("Live mode ON" if self.live else "Live mode OFF", 1500)
                elif cmd == "swap":
                    self.swap_languages()
                elif cmd == "clear":
                    self.canvas.delete("tr")
                elif cmd == "quit":
                    self.root.destroy()
                    return
            while True:
                try:
                    kind, payload = self.jobs.get_nowait()
                except queue.Empty:
                    break
                if kind == "status":
                    self.show_status(payload, 3000)
                elif kind == "draw":
                    self.draw(payload)
                elif kind == "langs":
                    self.set_languages(*payload)
                elif kind == "pack_failed":
                    code, why = payload
                    print(f"[ocr] {name(code)} pack not added: {why}", flush=True)
                    # Windows' own Language settings install it reliably (add the language there).
                    self.show_status(f"Windows didn't add {name(code)} text recognition. Click to add "
                                     f"{name(code)} in Windows language settings instead", 20000,
                                     action=lambda: os.startfile("ms-settings:regionlanguage"))
                elif kind == "hide":
                    self.canvas.itemconfigure("tr", state="hidden")
                    self.canvas.update_idletasks()
                    payload.set()
                elif kind == "show":
                    self.canvas.itemconfigure("tr", state="normal")
        except Exception:
            traceback.print_exc()
        self.root.after(50, self.pump)

    def swap_languages(self):
        """Ctrl+Alt+D and the bar's swap button, like the swap button in setup."""
        if self.source == "auto":
            self.show_status("From is on Detect: click it and pick a language to swap", 4000)
            return
        self.pick("from", self.target)

    def set_languages(self, source, target):
        self.source, self.target = source, target
        self.config.update({"from": source, "to": target})
        save_config(self.config)
        self.canvas.delete("tr")
        self.last_thumb = None                  # make live mode rescan right away
        self.regions, self.region_sigs = [], []
        self.layout_langs()
        src = "Detect language" if source == "auto" else name(source)
        self.show_status(f"Now translating {src} -> {name(target)}", 2500)
        self.offer_ocr_pack()

    def offer_ocr_pack(self):
        """If From needs a Windows OCR pack that isn't installed, offer it in the bar."""
        code = self.source
        if code in OCR_PACK and not self.has_ocr_pack(code):
            why = "so accents read correctly" if SCRIPTS.get(code) is None else "to read it"
            self.show_status(f"Add Windows text recognition for {name(code)} {why}: click here",
                             15000, action=lambda: self.add_ocr_pack(code))

    def add_ocr_pack(self, code):
        """Add the Windows OCR pack for `code` (one UAC prompt), the same way setup does."""
        if self.working:
            return

        def install():
            self.working = True
            self.jobs.put(("status", f"Adding {name(code)} text recognition (Windows will ask permission)..."))
            try:
                from setup_wizard import NO_WINDOW, OCR_LOG, ocr_pack_command
                if os.path.exists(OCR_LOG):
                    os.remove(OCR_LOG)
                subprocess.run(ocr_pack_command([OCR_PACK[code]]), creationflags=NO_WINDOW)
                if self.has_ocr_pack(code):
                    self.jobs.put(("status", f"{name(code)} text recognition added"))
                else:
                    self.jobs.put(("pack_failed", (code, self.ocr_pack_error(OCR_LOG))))
            finally:
                self.working = False

        threading.Thread(target=install, daemon=True).start()

    @staticmethod
    def ocr_pack_error(log):
        """Why adding an OCR pack failed, from the install script's log."""
        try:
            with open(log, encoding="utf-8-sig", errors="replace") as f:
                lines = f.read().splitlines()
        except OSError:
            return "Windows' permission prompt was declined"
        errors = [l.strip() for l in lines if l.strip().startswith("Error")]     # from DISM
        fails = [l.split("failed:", 1)[1].strip() for l in lines if "failed:" in l]
        if errors or fails:
            return (errors or fails)[-1]
        if any(l.endswith(" added") for l in lines):
            return "Windows added it, but it isn't available yet; restart Polyglass (or the PC)"
        return "unknown error"

    @staticmethod
    def has_ocr_pack(code):
        return any(primary(l.language_tag) == code for l in OcrEngine.available_recognizer_languages)

    @staticmethod
    def routes(source, target):
        """Model routes to have for a language pair: both ways, so swapping works too."""
        if source == "auto":
            return [] if target == "en" else [("en", target)]
        return [(source, target), (target, source)]

    def next_pair(self, side, code):
        """The (from, to) pair after picking `code` on one side. Picking the language that is
        already on the other side swaps them, like Google Translate."""
        src, dst = self.source, self.target
        if side == "to":
            return (src, code) if code != src else (dst, code)
        if code != dst:
            return code, dst
        return code, src if src != "auto" else ("es" if code == "en" else "en")

    def pick(self, side, code):
        """Choose a From or To language from the bar; missing models download first."""
        if self.downloading:
            self.show_status("Still downloading, one moment...", 2000)
            return
        new = self.next_pair(side, code)
        if new == (self.source, self.target):
            return
        models = Translator.installed_models()
        missing = [r for r in self.routes(*new) if not Translator.ready(*r, models)]
        if not missing:
            self.set_languages(*new)
            return

        size = self.download_size(new, models)

        def download():
            self.downloading = self.working = True
            codes = dict.fromkeys(c for r in missing for c in r if c != "en")
            what = " and ".join(map(name, codes)) or "English"
            self.jobs.put(("status", f"Downloading {what}"
                                     f"{f', {model_sizes.label(size)}' if size else ''} (first time only)..."))
            try:
                if all(self.translator.ensure(a, b, quiet=True) for a, b in missing):
                    self.jobs.put(("langs", new))
            finally:
                self.downloading = self.working = False

        threading.Thread(target=download, daemon=True).start()

    def load_model_sizes(self):
        index = model_sizes.load_index()
        self.model_sizes = model_sizes.fetch_sizes(index, set(LANG_ORDER)) if index else {}
        self.model_index = index

    def download_size(self, pair, models):
        """Bytes to download before `pair` (from, to) works both ways, or None if unknown."""
        installed, need = set(models), []
        for r in self.routes(*pair):
            if Translator.ready(*r, models):
                continue
            legs = model_sizes.needed(*r, self.model_index, installed)
            if legs is None and r[0] == "zt":                  # like ensure(): fall back to zh
                legs = model_sizes.needed("zh", r[1], self.model_index, installed)
            if legs is None:
                return None
            need += [leg for leg in legs if leg not in need]
        return model_sizes.total(need, self.model_sizes) if need else None

    def open_menu(self, side):
        """Dropdown for the From or To button: a check when a language is ready to use
        offline, else the download size ("Download" while the sizes are unknown)."""
        if self.downloading:
            self.show_status("Still downloading, one moment...", 2000)
            return
        models = Translator.installed_models()
        current = self.source if side == "from" else self.target
        m = tk.Menu(self.bar, tearoff=0, font=("Segoe UI", 10), bg="#1b1d23", fg="#f2f3f5",
                    activebackground="#2d3340", activeforeground="#ffffff", bd=0)
        self.menu_choice = tk.StringVar(value=current)
        if side == "from":
            m.add_radiobutton(label="Detect language", variable=self.menu_choice, value="auto",
                              command=lambda: self.pick("from", "auto"))
            m.add_separator()
        for code in LANG_ORDER:
            pair = self.next_pair(side, code)
            ok = all(Translator.ready(*r, models) for r in self.routes(*pair))
            size = None if ok else self.download_size(pair, models)
            mark = "✓" if ok else f"⬇ {model_sizes.label(size)}" if size else "⬇ Download"
            m.add_radiobutton(label=name(code), variable=self.menu_choice, value=code,
                              accelerator=mark,
                              command=lambda c=code: self.pick(side, c))
        x0, _, _, y1 = self.bar_canvas.bbox(f"{side}_chip")
        m.tk_popup(self.bar.winfo_rootx() + x0, self.bar.winfo_rooty() + y1 + 4)

    # -- persistent top bar ---------------------------------------------------
    def build_bar(self):
        """A small clickable window above the click-through overlay: status, the From / To
        language buttons with a swap button between them, and the hotkey hint."""
        W = self.mon["width"]
        bw, bh, y0 = 960, 40, 8
        x0 = (W - bw) // 2
        self.bar_rect = (x0, y0, x0 + bw, y0 + bh)      # monitor coordinates; OCR skips it
        self.bar = b = tk.Toplevel(self.root)
        b.overrideredirect(True)
        b.attributes("-topmost", True)
        b.attributes("-transparentcolor", TRANSPARENT)
        b.config(bg=TRANSPARENT)
        b.geometry(f"{bw}x{bh}+{self.mon['left'] + x0}+{self.mon['top'] + y0}")
        self.bar_canvas = c = tk.Canvas(b, bg=TRANSPARENT, highlightthickness=0, bd=0)
        c.pack(fill="both", expand=True)
        b.update()
        u = ctypes.windll.user32
        hwnd = u.GetParent(b.winfo_id()) or b.winfo_id()
        u.SetWindowLongW(hwnd, -20, u.GetWindowLongW(hwnd, -20) | 0x00000080)   # TOOLWINDOW: no taskbar button
        if self.exclude_from_capture:
            u.SetWindowDisplayAffinity(hwnd, 0x11)

        x1, y1, r = bw - 1, bh - 1, 16
        pts = [r, 0, x1 - r, 0, x1, 0, x1, r, x1, y1 - r, x1, y1,
               x1 - r, y1, r, y1, 0, y1, 0, y1 - r, 0, r, 0, 0]
        c.create_polygon(pts, smooth=True, fill="#1b1d23", outline="#3a3f4b", width=1)
        cy = bh // 2
        self.dot = c.create_oval(16, cy - 7, 30, cy + 7, fill="#3ddc84", outline="")
        self.spin = c.create_arc(14, cy - 10, 34, cy + 10, start=0, extent=270,
                                 style="arc", outline="#4fc3f7", width=3, state="hidden")
        self.state_id = c.create_text(46, cy, anchor="w", text="", fill="#f5f5f5",
                                      font=("Segoe UI", 11, "bold"))
        self.chip_font = tkfont.Font(family="Segoe UI", size=10, weight="bold")
        self.msg_font = tkfont.Font(family="Segoe UI", size=9)
        self.msg_id = c.create_text(bw - 16, cy, anchor="e", fill="#8b93a7", text=HINT, font=self.msg_font)
        c.tag_bind(self.msg_id, "<Button-1>", lambda e: self.msg_click())
        c.tag_bind(self.msg_id, "<Enter>", lambda e: c.config(cursor="hand2" if self.msg_live_action() else ""))
        c.tag_bind(self.msg_id, "<Leave>", lambda e: c.config(cursor=""))
        self.msg_action = None
        self.msg = ""
        self.msg_until = 0.0
        self.angle = 0
        self.working = False
        self.layout_langs()
        self.tick()

    def layout_langs(self):
        """(Re)draw the [From v] swap [To v] buttons for the current languages."""
        c = self.bar_canvas
        c.delete("lang")
        cy = int(c.winfo_height()) // 2
        x = 190
        src = "Detect language" if self.source == "auto" else name(self.source)
        for side, label in (("from", src), ("swap", "⇄"), ("to", name(self.target))):
            text = label if side == "swap" else f"{label}  ▾"
            w = self.chip_font.measure(text) + (16 if side == "swap" else 24)
            tag = f"{side}_chip"
            off = side == "swap" and self.source == "auto"     # nothing to swap with Detect
            c.create_rectangle(x, cy - 13, x + w, cy + 13, fill="#262a33", outline="#3a3f4b",
                               tags=("lang", tag, f"{tag}_bg"))
            c.create_text(x + w // 2, cy, text=text, font=self.chip_font,
                          fill="#5d6475" if off else "#f2f3f5", tags=("lang", tag))
            if not off:
                c.tag_bind(tag, "<Enter>", lambda e, t=tag: (c.itemconfigure(f"{t}_bg", fill="#323846"),
                                                             c.config(cursor="hand2")))
                c.tag_bind(tag, "<Leave>", lambda e, t=tag: (c.itemconfigure(f"{t}_bg", fill="#262a33"),
                                                             c.config(cursor="")))
                action = self.swap_languages if side == "swap" else (lambda s=side: self.open_menu(s))
                c.tag_bind(tag, "<Button-1>", lambda e, a=action: a())
            x += w + 8
        self.langs_end = x

    def tick(self):
        now = time.time()
        c = self.bar_canvas
        c.itemconfigure(self.state_id, text="Polyglass  •  " + ("LIVE" if self.live else "Ready"))
        if self.msg and now < self.msg_until:
            text, color = self.msg, "#4fc3f7" if self.msg_action else "#f5f5f5"
        else:
            text, color = HINT, "#8b93a7"
        room = int(c.winfo_width()) - 16 - self.langs_end - 12
        if self.msg_font.measure(text) > room:
            while text and self.msg_font.measure(text + "…") > room:
                text = text[:-1]
            text += "…"
        c.itemconfigure(self.msg_id, text=text, fill=color)
        if self.working:
            self.angle = (self.angle - 25) % 360
            c.itemconfigure(self.spin, state="normal", start=self.angle)
            c.itemconfigure(self.dot, state="hidden")
        else:
            c.itemconfigure(self.spin, state="hidden")
            c.itemconfigure(self.dot, state="normal", fill="#ffb300" if self.live else "#3ddc84")
        self.root.after(50, self.tick)

    def show_status(self, msg, ms, action=None):
        """Show `msg` in the bar for `ms`; with `action`, clicking the message runs it."""
        print(f"[status] {msg}", flush=True)
        if hasattr(self, "msg_id"):
            self.msg = msg
            self.msg_until = time.time() + ms / 1000.0
            self.msg_action = action

    def msg_live_action(self):
        return self.msg_action if self.msg and time.time() < self.msg_until else None

    def msg_click(self):
        action = self.msg_live_action()
        if action:
            self.msg_until = 0
            action()

    def wrap(self, text, font, width):
        # Chinese and Japanese have no spaces, so wrap those by character. Other text wraps
        # between words only; a word too long for the box is shrunk instead of split.
        sep = " " if " " in text.strip() or not CJK_CHARS.search(text) else ""
        words = text.split() if sep else list(text.strip())
        lines, cur = [], ""
        for word in words:
            trial = f"{cur}{sep}{word}" if cur else word
            if font.measure(trial) <= width or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = word
        if cur:
            lines.append(cur)
        return lines or [""]

    def draw(self, payload):
        items, font_family = payload
        print(f"[draw] painting {len(items)} translated lines", flush=True)
        for it in items[:8]:
            print(f"      ({it[0]:.0f},{it[1]:.0f}) -> {it[4]!r}", flush=True)
        self.canvas.delete("tr")
        for x, y, w, h, text, bg, fg in items:
            pad = 3
            bx, by, bw = x - pad, y - pad, w + 2 * pad
            font, lines = None, None
            for size in range(max(9, min(40, int(h * 0.85))), 8, -1):
                font = tkfont.Font(family=font_family, size=-size)
                lines = self.wrap(text, font, bw - 2 * pad)
                if (len(lines) * font.metrics("linespace") <= h + 2 * pad
                        and max(map(font.measure, lines)) <= bw):
                    break
            bh = max(h + 2 * pad, len(lines) * font.metrics("linespace") + 2 * pad)
            self.canvas.create_rectangle(bx, by, bx + bw, by + bh, fill=bg, outline="", tags="tr")
            self.canvas.create_text(bx + pad, by + pad, anchor="nw", text="\n".join(lines),
                                    fill=fg, font=font, tags="tr")

    # -- worker --------------------------------------------------------------
    def request(self, force=False):
        if self.busy.is_set():
            return
        self.busy.set()
        threading.Thread(target=self.work, args=(force,), daemon=True).start()

    def live_loop(self):
        while True:
            time.sleep(LIVE_INTERVAL)
            if self.live:
                self.request(force=False)

    @staticmethod
    def sig(img, box):
        x, y, w, h = box
        W, H = img.size
        x0, y0 = max(0, int(x)), max(0, int(y))
        x1, y1 = min(W, int(x + w) + 1), min(H, int(y + h) + 1)
        if x1 <= x0 or y1 <= y0:
            return np.zeros((8, 24), dtype=np.int16)
        c = img.crop((x0, y0, x1, y1)).convert("L").resize((24, 8))
        return np.asarray(c, dtype=np.int16)

    @staticmethod
    def block_change(thumb, last):
        """Largest mean difference of any 10x10 block between two 160x90 thumbnails."""
        return float(np.abs(thumb - last).reshape(9, 10, 16, 10).mean(axis=(1, 3)).max())

    def changed(self, img, thumb):
        """True when the screen changed in a way that could alter the text."""
        if self.last_thumb is None:
            return True
        new_text = self.block_change(thumb, self.last_thumb) >= BLOCK_THRESHOLD
        if not self.regions:                 # nothing translated last time: watch for new text
            return new_text
        if float(np.abs(thumb - self.last_thumb).mean()) >= 10.0:
            return True                      # big scene change (new menu / level)
        if not self.exclude_from_capture:    # fallback mode: our own overlay is in the capture
            return False
        for box, old in zip(self.regions, self.region_sigs):
            if float(np.abs(self.sig(img, box) - old).mean()) >= 14.0:
                return True                  # text (or what's behind it) changed
        return new_text                      # new text somewhere else

    def work(self, force):
        hidden = False
        source, target = self.source, self.target
        try:
            with mss.MSS() as sct:
                shot = sct.grab(self.mon)
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
            thumb = np.asarray(img.convert("L").resize((160, 90)), dtype=np.int16)
            if not force and not self.changed(img, thumb):
                return
            self.last_thumb = thumb
            self.working = True
            if force:
                self.jobs.put(("status", "Translating..."))
            if not self.exclude_from_capture:
                # Hide our own overlay so the OCR only sees the real screen.
                ev = threading.Event()
                self.jobs.put(("hide", ev))
                ev.wait(2)
                hidden = True
                time.sleep(0.15)
                with mss.MSS() as sct:
                    shot = sct.grab(self.mon)
                img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
            w, h = shot.size
            print(f"[capture] {w}x{h}, mean brightness {np.asarray(img).mean():.0f}", flush=True)
            code, lines = ocr_all(shot.bgra, w, h, np.asarray(img), target, source)
            print(f"[ocr] source language={code}, target={target}, lines found={len(lines)}", flush=True)
            bx0, by0, bx1, by1 = self.bar_rect
            lines = [(t, r) for t, r in lines
                     if not (r[0] < bx1 and r[0] + r[2] > bx0 and r[1] < by1 and r[1] + r[3] > by0)]
            self.regions = [r for _, r in lines]
            self.region_sigs = [self.sig(img, r) for r in self.regions]
            for t, r in lines[:5]:
                print(f"      {t!r}", flush=True)
            if not code or not lines:
                self.jobs.put(("draw", ([], "Segoe UI")))
                if force:
                    self.jobs.put(("status", f"No text to translate into {name(target)}."))
                return
            code = self.translator.ensure(code, target)
            if not code:
                return
            items = []
            for text, (x, y, bw, bh) in lines:
                out = self.translator.translate(code, target, text)
                if not out:
                    continue
                bg = self.sample_bg(img, x, y, bw, bh)
                lum = 0.299 * bg[0] + 0.587 * bg[1] + 0.114 * bg[2]
                items.append((x, y, bw, bh, out, "#%02x%02x%02x" % bg,
                              "#111111" if lum > 140 else "#f5f5f5"))
            self.jobs.put(("draw", (items, FONTS.get(target, "Segoe UI"))))
        except Exception as e:
            traceback.print_exc()
            self.jobs.put(("status", f"Error: {e}"))
        finally:
            self.working = False
            if hidden:
                self.jobs.put(("show", None))
            self.busy.clear()

    @staticmethod
    def sample_bg(img, x, y, w, h):
        W, H = img.size
        pts = [(x - 2, y - 2), (x + w + 2, y - 2), (x - 2, y + h + 2), (x + w + 2, y + h + 2)]
        px = [img.getpixel((min(max(int(a), 0), W - 1), min(max(int(b), 0), H - 1))) for a, b in pts]
        c = tuple(int(sum(p[i] for p in px) / 4) for i in range(3))
        return (c[0] or 2, c[1], c[2]) if c == (1, 1, 1) else c

    def run(self):
        if not self.exclude_from_capture:
            print("Note: this Windows can't hide the overlay from screenshots, so it will "
                  "briefly hide itself during each scan (may flicker in live mode).")
        self.root.mainloop()


if __name__ == "__main__":
    Overlay().run()
