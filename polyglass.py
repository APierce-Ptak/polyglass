"""
Polyglass - transparent click-through overlay that translates on-screen
text (Chinese, Japanese, Korean, Russian, Arabic, English, ... or other
Latin-script languages) into the language you choose, drawn in place over the
original.  Windows only.

Hotkeys
    Ctrl+Alt+T   translate the screen once
    Ctrl+Alt+L   toggle live mode (re-scans about every 1.5 s)
    Ctrl+Alt+D   switch the output language: English <-> your chosen language
    Ctrl+Alt+C   clear the overlay
    Ctrl+Alt+Q   quit

Settings live in polyglass.json next to this file (the setup wizard writes it):
    "other_language"   your chosen output language besides English (e.g. "ja", "es")
    "translate_to"     the current output language, "en" or other_language

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
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import unicodedata

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
from pynput import keyboard

import argostranslate.package
import argostranslate.translate
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
HINT = "Ctrl+Alt+  T translate  L live  D direction  C clear  Q quit"
LIVE_INTERVAL = 1.5
CHANGE_THRESHOLD = 2.0   # mean abs diff on a 160x90 thumbnail to trigger a rescan
MIN_SCRIPT_FRACTION = 0.3

# Script prefixes (from unicodedata.name) used to pick the OCR language.
SCRIPTS = {
    "zh": ("CJK",), "ja": ("CJK", "HIRAGANA", "KATAKANA"), "ko": ("HANGUL",),
    "ru": ("CYRILLIC",), "uk": ("CYRILLIC",), "bg": ("CYRILLIC",),
    "sr": ("CYRILLIC",), "ar": ("ARABIC",), "fa": ("ARABIC",),
    "he": ("HEBREW",), "th": ("THAI",), "el": ("GREEK",), "hi": ("DEVANAGARI",),
}
NAMES = {"en": "English", "zh": "Chinese", "zt": "Chinese (Traditional)", "ja": "Japanese",
         "ko": "Korean", "ru": "Russian", "ar": "Arabic", "es": "Spanish", "fr": "French",
         "de": "German", "pt": "Portuguese", "it": "Italian"}
# Segoe UI has no CJK glyphs; use the matching Windows UI font for those outputs.
FONTS = {"ja": "Yu Gothic UI", "zh": "Microsoft YaHei UI", "zt": "Microsoft JhengHei UI",
         "ko": "Malgun Gothic"}
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "polyglass.json")
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


def ocr_all(bgra, w, h, rgb=None, target="en"):
    """Return (argos_from_code, [(text, rect), ...]) for the best language that is not
    already `target`, or (None, [])."""
    if rgb is not None:
        code, lines = rapid_ocr(rgb)
        print(f"[ocr] RapidOCR: {len(lines)} CJK lines", flush=True)
        if lines and family(code) != family(target):
            return code, lines
    bmp = make_bitmap(bgra, w, h)
    langs = list(OcrEngine.available_recognizer_languages)
    print("[ocr] installed OCR languages:", [l.language_tag for l in langs], flush=True)
    if not langs:
        raise RuntimeError("No Windows OCR languages installed (see README).")

    async def run():
        best, best_score, latin = None, 0, None
        for lang in langs:
            tag = lang.language_tag
            p = primary(tag)
            engine = OcrEngine.try_create_from_language(Language(tag))
            if engine is None or p == "en":
                if p == "en" and engine is not None and latin is None:
                    latin = (tag, await _ocr(engine, bmp))
                continue
            prefixes = SCRIPTS.get(p)
            if prefixes and family(argos_code(tag)) == family(target):
                continue                               # already in the output language
            lines = await _ocr(engine, bmp)
            if prefixes is None:                       # Latin-script language pack
                if latin is None:
                    latin = (tag, lines)
                continue
            kept, score = [], 0
            for text, rect in lines:
                n = script_chars(text, prefixes)
                if n and n / max(1, len(text.replace(" ", ""))) >= MIN_SCRIPT_FRACTION:
                    kept.append((text, rect))
                    score += n
            if score > best_score:
                best, best_score = (argos_code(tag), kept), score
        if best and best_score >= 2:
            return best
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
class Translator:
    def __init__(self, status):
        self.status = status
        self.cache = {}
        self.unavailable = set()

    @staticmethod
    def installed(src, dst):
        langs = {l.code: l for l in argostranslate.translate.get_installed_languages()}
        # get_translation also finds a route through English (e.g. ja -> en -> es).
        return src in langs and dst in langs and bool(langs[src].get_translation(langs[dst]))

    def ensure(self, code, target):
        """Make sure an offline route code -> target exists, downloading models if needed.
        Returns the source code to translate from (zt may fall back to zh), or None."""
        if (code, target) in self.unavailable:
            return None
        if self.installed(code, target):
            return code
        self.status(f"Downloading offline model {code} -> {target} (first time only)...")
        try:
            argostranslate.package.update_package_index()
            pkgs = argostranslate.package.get_available_packages()
            find = lambda a, b: next((p for p in pkgs if p.from_code == a and p.to_code == b), None)
            direct = find(code, target)
            # No direct model: go through English, e.g. ja -> en -> es.
            route = [direct] if direct else [find(code, "en"), find("en", target)]
            if None in route and code == "zt":
                return self.ensure("zh", target)
            if None in route:
                self.unavailable.add((code, target))
                self.status(f"No offline model for {name(code)} -> {name(target)}.")
                return None
            for pkg in route:
                if not self.installed(pkg.from_code, pkg.to_code):
                    argostranslate.package.install_from_path(pkg.download())
            return code
        except Exception as e:
            self.status(f"Model download failed: {e}")
            return None

    def translate(self, code, target, text):
        text = CJK_RE.sub("", text).strip()
        if not text:
            return ""
        key = (code, target, text)
        if key not in self.cache:
            try:
                self.cache[key] = argostranslate.translate.translate(text, code, target)
            except Exception:
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
        self.other = self.config.get("other_language") or None
        self.target = self.config.get("translate_to", "en")
        if self.target not in ("en", self.other):
            self.target = "en"

        with mss.MSS() as sct:
            mon = sct.monitors[1]
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

        keys = {
            "<ctrl>+<alt>+t": lambda: self.cmds.put("once"),
            "<ctrl>+<alt>+l": lambda: self.cmds.put("live"),
            "<ctrl>+<alt>+d": lambda: self.cmds.put("direction"),
            "<ctrl>+<alt>+c": lambda: self.cmds.put("clear"),
            "<ctrl>+<alt>+q": lambda: self.cmds.put("quit"),
        }
        keyboard.GlobalHotKeys(keys).start()
        self.build_bar()
        self.show_status(f"Ready: press Ctrl+Alt+T to translate into {name(self.target)}", 6000)
        self.root.after(50, self.pump)
        threading.Thread(target=self.live_loop, daemon=True).start()

    # -- window setup --------------------------------------------------------
    def _make_clickthrough(self):
        import os
        from ctypes import wintypes
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
                elif cmd == "direction":
                    self.switch_direction()
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
                elif kind == "hide":
                    self.canvas.itemconfigure("tr", state="hidden")
                    self.canvas.update_idletasks()
                    payload.set()
                elif kind == "show":
                    self.canvas.itemconfigure("tr", state="normal")
        except Exception:
            traceback.print_exc()
        self.root.after(50, self.pump)

    def switch_direction(self):
        if not self.other:
            self.show_status("No output language set: run Install.bat and pick one under Also translate into", 5000)
            return
        self.target = self.other if self.target == "en" else "en"
        self.config["translate_to"] = self.target
        save_config(self.config)
        self.canvas.delete("tr")
        self.last_thumb = None                  # make live mode rescan right away
        self.regions, self.region_sigs = [], []
        self.show_status(f"Now translating into {name(self.target)}", 2500)

    # -- persistent top bar ---------------------------------------------------
    def build_bar(self):
        W = self.mon["width"]
        bw, bh, y0 = 760, 40, 8
        x0 = (W - bw) // 2
        x1, y1, r = x0 + bw, y0 + bh, 16
        pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
               x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
        c = self.canvas
        c.create_polygon(pts, smooth=True, fill="#1b1d23", outline="#3a3f4b", width=1, tags="bar")
        cy = y0 + bh // 2
        self.dot = c.create_oval(x0 + 16, cy - 7, x0 + 30, cy + 7, fill="#3ddc84", outline="", tags="bar")
        self.spin = c.create_arc(x0 + 14, cy - 10, x0 + 34, cy + 10, start=0, extent=270,
                                 style="arc", outline="#4fc3f7", width=3, tags="bar", state="hidden")
        self.msg_id = c.create_text(x0 + 46, cy, anchor="w", text="", fill="#f5f5f5",
                                    font=("Segoe UI", 11, "bold"), tags="bar")
        self.hint_id = c.create_text(x1 - 16, cy, anchor="e", fill="#8b93a7",
                                     text=HINT,
                                     font=("Segoe UI", 9), tags="bar")
        self.msg = ""
        self.msg_until = 0.0
        self.angle = 0
        self.working = False
        self.tick()

    def tick(self):
        now = time.time()
        c = self.canvas
        if self.msg and now < self.msg_until:
            text, hint = self.msg, ""
        else:
            text = ("Polyglass  \u2022  " + ("LIVE" if self.live else "Ready")
                    + f"  \u2022  \u2192 {self.target.upper()}")
            hint = HINT
        c.itemconfigure(self.msg_id, text=text)
        c.itemconfigure(self.hint_id, text=hint)
        if self.working:
            self.angle = (self.angle - 25) % 360
            c.itemconfigure(self.spin, state="normal", start=self.angle)
            c.itemconfigure(self.dot, state="hidden")
        else:
            c.itemconfigure(self.spin, state="hidden")
            c.itemconfigure(self.dot, state="normal", fill="#ffb300" if self.live else "#3ddc84")
        c.tag_raise("bar")
        self.root.after(50, self.tick)

    def show_status(self, msg, ms):
        print(f"[status] {msg}", flush=True)
        if hasattr(self, "msg_id"):
            self.msg = msg
            self.msg_until = time.time() + ms / 1000.0

    def wrap(self, text, font, width):
        # Chinese and Japanese have no spaces, so wrap those by character.
        sep = " " if " " in text.strip() else ""
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
                if len(lines) * font.metrics("linespace") <= h + 2 * pad:
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

    def changed(self, img, thumb):
        """True when the screen changed in a way that could alter the text."""
        if self.last_thumb is None:
            return True
        g = float(np.abs(thumb - self.last_thumb).mean())
        if not self.regions:                 # nothing translated last time: watch for new text
            return g >= 4.0
        if g >= 10.0:                        # big scene change (new menu / level)
            return True
        if not self.exclude_from_capture:    # fallback mode: overlay pollutes crops
            return False
        for box, old in zip(self.regions, self.region_sigs):
            if float(np.abs(self.sig(img, box) - old).mean()) >= 14.0:
                return True                  # text (or what's behind it) changed
        return False

    def work(self, force):
        hidden = False
        target = self.target
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
            code, lines = ocr_all(shot.bgra, w, h, np.asarray(img), target)
            print(f"[ocr] source language={code}, target={target}, lines found={len(lines)}", flush=True)
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
