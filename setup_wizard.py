"""
Polyglass - friendly setup wizard (Windows).

Steps: welcome -> pick languages -> install (venv, packages, Windows OCR packs,
offline translation models, desktop shortcut) -> done / launch.

Start it by double-clicking Install.bat (it finds a suitable Python first).
"""
import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import venv
from tkinter import ttk

import model_sizes

APP_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_DIR = os.path.join(APP_DIR, ".venv")
VPY = os.path.join(VENV_DIR, "Scripts", "python.exe")
VPYW = os.path.join(VENV_DIR, "Scripts", "pythonw.exe")
APP = os.path.join(APP_DIR, "polyglass.py")
CONFIG = os.path.join(APP_DIR, "polyglass.json")
ICON = os.path.join(APP_DIR, "polyglass.ico")
OCR_ADDED = os.path.join(APP_DIR, "ocr_added.txt")   # read by Uninstall.bat
NO_WINDOW = 0x08000000
# Installed without its dependencies: the app only uses Argos to download models (running them
# through CTranslate2 itself), so it doesn't need stanza/spaCy/PyTorch, about 750 MB.
ARGOS = "argostranslate==1.11.0"

# (label, Windows OCR pack tag, Argos code). English OCR ships with Windows. Latin-script packs
# are only added for a chosen From/To (they read that language's accents); English reads the rest.
LANGUAGES = [
    ("English", None, "en"),
    ("Chinese (Simplified)", "zh-CN", "zh"),
    ("Chinese (Traditional)", "zh-TW", "zt"),
    ("Japanese", "ja-JP", "ja"),
    ("Korean", "ko-KR", "ko"),
    ("Russian", "ru-RU", "ru"),
    ("Arabic", "ar-SA", "ar"),
    ("Spanish", "es-ES", "es"),
    ("French", "fr-FR", "fr"),
    ("German", "de-DE", "de"),
    ("Portuguese", "pt-BR", "pt"),
    ("Italian", "it-IT", "it"),
]
LATIN = {"es", "fr", "de", "pt", "it"}
DETECT = "Detect language"
LABEL = {c: l for l, _, c in LANGUAGES}
CODE = {l: c for l, _, c in LANGUAGES}
TAG = {c: t for _, t, c in LANGUAGES}


def ocr_pack_command(tags):
    """Command that adds Windows OCR packs for `tags` (e.g. ["es-ES"]) behind one UAC prompt.
    Records only the packs it actually adds, so Uninstall.bat never removes ones Windows (or
    the user) already had. The app uses this too, to add a pack from its language bar."""
    quoted = ",".join(f"'{t}'" for t in tags)
    script = os.path.join(os.environ.get("TEMP", APP_DIR), "polyglass_add_ocr.ps1")
    with open(script, "w", encoding="utf-8-sig") as f:
        f.write(
            f"$added = '{OCR_ADDED.replace(chr(39), chr(39) * 2)}'\n"
            f"foreach ($t in @({quoted})) {{\n"
            "  $n = \"Language.OCR~~~$t~~~0.0.1.0\"\n"
            "  if ((Get-WindowsCapability -Online -Name $n).State -ne 'Installed') {\n"
            "    Add-WindowsCapability -Online -Name $n | Out-Null\n"
            "    Add-Content -LiteralPath $added -Value $n\n"
            "  }\n"
            "}\n")
    ps = ("Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden "
          f"-ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','\"{script}\"'")
    return ["powershell", "-NoProfile", "-Command", ps]



def model_pairs(src, dst):
    """Models setup downloads for From `src` / To `dst`: both directions, so swapping works,
    going through English between two other languages. With Detect, only English -> To."""
    pairs = []
    for a, b in [("en", dst)] if src == "auto" else [(src, dst), (dst, src)]:
        for leg in [(a, b)] if "en" in (a, b) else [(a, "en"), ("en", b)]:
            if leg[0] != leg[1] and leg not in pairs:
                pairs.append(leg)
    return pairs


def model_download_code(pairs):
    """Python, run inside the app's environment, that downloads and installs the translation
    models for `pairs` (e.g. [("es", "en")]). It unzips them itself: Argos's install_from_path
    imports its translate module, which needs PyTorch. The downloads are deleted once unpacked."""
    return (
        "import os, zipfile, argostranslate.package as p\n"
        "p.update_package_index(); av=p.get_available_packages()\n"
        f"for a,b in {pairs!r}:\n"
        "    pk=next((x for x in av if x.from_code==a and x.to_code==b),None)\n"
        "    if not pk: print('no model for',a,'->',b); continue\n"
        "    print('downloading',a,'->',b)\n"
        "    f=pk.download()\n"
        "    with zipfile.ZipFile(f) as z: z.extractall(p.settings.package_data_dir)\n"
        "    os.remove(f)\n"
        "    print('installed',a,'->',b)\n")


BG, FG, ACCENT, MUTED = "#15171c", "#f2f3f5", "#4fc3f7", "#8b93a7"


class Wizard(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Polyglass Setup")
        if os.path.exists(ICON):
            self.iconbitmap(ICON)
        self.geometry("680x540")
        self.resizable(False, False)
        self.configure(bg=BG)
        self.q = queue.Queue()
        self.sizes = None                  # model download sizes, once known ({} offline)
        threading.Thread(target=lambda: self.q.put(("sizes", model_sizes.fetch_sizes(
            model_sizes.load_index(), set(CODE.values())))), daemon=True).start()
        self.config_data = self.load_config()
        src = self.config_data.get("from", "auto")
        dst = self.config_data.get("to", "en")
        self.src = tk.StringVar(value=LABEL.get(src, DETECT))
        self.dst = tk.StringVar(value=LABEL.get(dst, "English"))
        self.prev = (self.src.get(), self.dst.get())
        self.shortcut = tk.BooleanVar(value=True)
        self.launch = tk.BooleanVar(value=True)
        self.ok = True

        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=FG, font=("Segoe UI", 10))
        style.configure("H.TLabel", font=("Segoe UI", 20, "bold"))
        style.configure("M.TLabel", foreground=MUTED)
        style.configure("TCheckbutton", background=BG, foreground=FG, font=("Segoe UI", 10))
        style.map("TCheckbutton", background=[("active", BG)])
        style.configure("TButton", font=("Segoe UI", 10), padding=6)
        style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"))
        style.configure("Swap.TButton", font=("Segoe UI", 14), padding=(8, 2))
        style.configure("TCombobox", padding=6)
        self.option_add("*TCombobox*Listbox.font", ("Segoe UI", 10))
        style.configure("Horizontal.TProgressbar", troughcolor="#262a33",
                        background=ACCENT, bordercolor=BG, lightcolor=ACCENT, darkcolor=ACCENT)

        self.body = ttk.Frame(self, padding=(28, 24, 28, 8))
        self.body.pack(fill="both", expand=True)
        foot = ttk.Frame(self, padding=(28, 0, 28, 20))
        foot.pack(fill="x")
        self.back_btn = ttk.Button(foot, text="Back", command=self.back)
        self.next_btn = ttk.Button(foot, text="Next", style="Accent.TButton", command=self.next)
        self.next_btn.pack(side="right")
        self.back_btn.pack(side="right", padx=8)

        self.page = 0
        self.show()
        self.after(100, self.poll)

    # ------------------------------------------------------------ navigation
    def clear(self):
        for w in self.body.winfo_children():
            w.destroy()

    def show(self):
        self.clear()
        [self.page_welcome, self.page_langs, self.page_install, self.page_done][self.page]()

    def next(self):
        if self.page == 1:
            self.page = 2
            self.show()
            threading.Thread(target=self.install, daemon=True).start()
        elif self.page == 3:
            if self.launch.get() and self.ok:
                subprocess.Popen([VPYW, APP], cwd=APP_DIR, creationflags=NO_WINDOW)
            self.destroy()
        else:
            self.page += 1
            self.show()

    def back(self):
        if self.page in (1,):
            self.page -= 1
            self.show()

    # ----------------------------------------------------------------- pages
    def page_welcome(self):
        ttk.Label(self.body, text="Polyglass", style="H.TLabel").pack(anchor="w")
        ttk.Label(self.body, style="M.TLabel", text="Translate anything on your screen, in place.").pack(anchor="w", pady=(2, 18))
        ttk.Label(self.body, wraplength=620, justify="left", text=(
            "This wizard will set everything up for you:\n\n"
            "  •  Install the app and its offline translation engine\n"
            "  •  Add Windows text-recognition (OCR) for your languages\n"
            "  •  Download the offline translation models\n"
            "  •  Create a desktop shortcut\n\n"
            "Everything runs on your PC. Nothing is sent to the cloud.\n"
            "It takes a few minutes and needs an internet connection, and about 450 MB of disk\n"
            "space plus your languages (usually 150-400 MB each; you'll see the exact size).")).pack(anchor="w")
        self.back_btn.state(["disabled"])
        self.next_btn.config(text="Next")

    def page_langs(self):
        ttk.Label(self.body, text="Languages", style="H.TLabel").pack(anchor="w")
        ttk.Label(self.body, style="M.TLabel", wraplength=620, text=(
            "Choose the language on your screen and the language you want to read. "
            "You can run this wizard again to change them.")).pack(anchor="w", pady=(2, 22))

        row = ttk.Frame(self.body)
        row.pack(anchor="w")
        ttk.Label(row, text="Translate from", style="M.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(row, text="Translate to", style="M.TLabel").grid(row=0, column=2, sticky="w")
        names = [l for l, _, _ in LANGUAGES]
        src = ttk.Combobox(row, textvariable=self.src, state="readonly", width=24,
                           values=[DETECT] + names, font=("Segoe UI", 11))
        dst = ttk.Combobox(row, textvariable=self.dst, state="readonly", width=24,
                           values=names, font=("Segoe UI", 11))
        src.grid(row=1, column=0, pady=(4, 0))
        dst.grid(row=1, column=2, pady=(4, 0))
        self.swap_btn = ttk.Button(row, text="\u21c4", style="Swap.TButton", width=3, command=self.swap)
        self.swap_btn.grid(row=1, column=1, padx=14, pady=(4, 0))
        src.bind("<<ComboboxSelected>>", lambda e: self.picked())
        dst.bind("<<ComboboxSelected>>", lambda e: self.picked())
        self.lang_note = ttk.Label(self.body, style="M.TLabel", wraplength=620, justify="left")
        self.lang_note.pack(anchor="w", pady=(12, 0))
        self.size_note = ttk.Label(self.body, wraplength=620, justify="left")
        self.size_note.pack(anchor="w", pady=(8, 0))
        self.picked()

        ttk.Separator(self.body).pack(fill="x", pady=22)
        ttk.Checkbutton(self.body, text="Create a desktop shortcut", variable=self.shortcut).pack(anchor="w", pady=2)
        ttk.Checkbutton(self.body, text="Start Polyglass when setup finishes", variable=self.launch).pack(anchor="w", pady=2)
        ttk.Label(self.body, style="M.TLabel", wraplength=620, text=(
            "Windows may ask for permission (a UAC prompt) once, to add text-recognition packs.")).pack(anchor="w", pady=(14, 0))
        self.back_btn.state(["!disabled"])
        self.next_btn.config(text="Install")

    def swap(self):
        if self.src.get() == DETECT:
            return
        a, b = self.src.get(), self.dst.get()
        self.src.set(b)
        self.dst.set(a)
        self.picked()

    def picked(self):
        """Keep From and To different (like Google Translate) and refresh the swap button."""
        src, dst = self.src.get(), self.dst.get()
        if src == dst:
            old_src, old_dst = self.prev
            if src != old_src:          # From changed to match To: move To to the old From
                self.dst.set(old_src if old_src != DETECT else ("Spanish" if src == "English" else "English"))
            else:                       # To changed to match From: move From to the old To
                self.src.set(old_dst)
        self.prev = (self.src.get(), self.dst.get())
        detect = self.src.get() == DETECT
        self.swap_btn.state(["disabled"] if detect else ["!disabled"])
        self.lang_note.config(text=(
            "Polyglass works out the language on screen by itself. Translation models download "
            "the first time a new language appears."
            if detect else
            "Press Ctrl+Alt+D in the app to swap them, just like the \u21c4 button."))
        self.show_size()

    def show_size(self):
        """How much the chosen languages download (models already installed don't count)."""
        src = "auto" if self.src.get() == DETECT else CODE[self.src.get()]
        installed = model_sizes.installed_pairs()
        legs = [leg for leg in model_pairs(src, CODE[self.dst.get()]) if leg not in installed]
        later = " Other languages download when they first appear." if src == "auto" else ""
        if not legs:
            text = ("Nothing to download now." + later if src == "auto" else
                    "Nothing to download: these languages are already installed.")
        elif self.sizes is None:
            text = "Download size: checking..."
        elif model_sizes.total(legs, self.sizes) is None:
            text = "Download size: unknown (checking it needs internet)."
        else:
            text = f"Download: about {model_sizes.label(model_sizes.total(legs, self.sizes))}." + later
        self.size_note.config(text=text)

    def page_install(self):
        ttk.Label(self.body, text="Setting things up...", style="H.TLabel").pack(anchor="w")
        self.step_lbl = ttk.Label(self.body, text="Starting", style="M.TLabel")
        self.step_lbl.pack(anchor="w", pady=(2, 10))
        self.bar = ttk.Progressbar(self.body, maximum=100, mode="determinate")
        self.bar.pack(fill="x")
        self.log = tk.Text(self.body, height=16, bg="#0e1014", fg="#c8ccd6", relief="flat",
                           font=("Consolas", 9), wrap="word", state="disabled")
        self.log.pack(fill="both", expand=True, pady=(12, 0))
        self.back_btn.state(["disabled"])
        self.next_btn.state(["disabled"])

    def page_done(self):
        good = self.ok
        ttk.Label(self.body, text="All set!" if good else "Finished with problems", style="H.TLabel").pack(anchor="w")
        text = (
            "Polyglass is ready.\n\n"
            "  Ctrl+Alt+T   translate the screen once\n"
            "  Ctrl+Alt+L   turn live mode on or off\n"
            "  Ctrl+Alt+D   swap the From and To languages\n"
            "  Ctrl+Alt+C   clear the overlay\n"
            "  Ctrl+Alt+Q   quit\n\n"
            "Open the app, click the window with the text, then press Ctrl+Alt+T."
            if good else
            "Something went wrong. Scroll the log on the previous page or check\n"
            "setup_wizard.log in the app folder, then run Install.bat again.")
        ttk.Label(self.body, text=text, justify="left").pack(anchor="w", pady=(10, 0))
        self.next_btn.state(["!disabled"])
        self.back_btn.state(["disabled"])
        self.next_btn.config(text="Finish")

    # ---------------------------------------------------------------- worker
    @staticmethod
    def load_config():
        try:
            with open(CONFIG, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def save_config(self, src, dst):
        cfg = self.config_data
        cfg.update({"from": src, "to": dst})
        with open(CONFIG, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)

    def say(self, msg):
        self.q.put(("log", msg))

    def step(self, label, pct):
        self.q.put(("step", (label, pct)))

    def poll(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "log":
                    self.log.config(state="normal")
                    self.log.insert("end", val + "\n")
                    self.log.see("end")
                    self.log.config(state="disabled")
                    try:
                        with open(os.path.join(APP_DIR, "setup_wizard.log"), "a", encoding="utf-8") as f:
                            f.write(val + "\n")
                    except OSError:
                        pass
                elif kind == "step":
                    self.step_lbl.config(text=val[0])
                    self.bar["value"] = val[1]
                elif kind == "sizes":
                    self.sizes = val
                    if self.page == 1:
                        self.show_size()
                elif kind == "done":
                    self.page = 3
                    self.show()
        except queue.Empty:
            pass
        self.after(100, self.poll)

    def run(self, cmd, **kw):
        """Run a command, streaming output into the log. Returns the exit code."""
        self.say("> " + (cmd if isinstance(cmd, str) else " ".join(cmd)))
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             encoding="utf-8", errors="replace", creationflags=NO_WINDOW, cwd=APP_DIR, **kw)
        for line in p.stdout:
            line = line.rstrip()
            if line and not line.startswith(("  ", "Requirement already")):
                self.say(line[:200])
        return p.wait()

    def install(self):
        try:
            self._install()
        except Exception as e:  # noqa
            self.ok = False
            self.say(f"ERROR: {e}")
        self.q.put(("done", None))

    def _install(self):
        src = "auto" if self.src.get() == DETECT else CODE[self.src.get()]
        dst = CODE[self.dst.get()]
        self.save_config(src, dst)
        # Text-recognition packs: every script when detecting, otherwise both sides so swapping works.
        wanted = [c for _, _, c in LANGUAGES if c not in LATIN] if src == "auto" else [src, dst]
        langs = [(LABEL[c], TAG[c], c) for c in wanted if TAG[c]]
        installed = model_sizes.installed_pairs()
        pairs = [leg for leg in model_pairs(src, dst) if leg not in installed]

        self.step("Creating a private Python environment", 3)
        if not os.path.exists(VPY):
            venv.EnvBuilder(with_pip=True).create(VENV_DIR)
        self.say(f"Python {sys.version.split()[0]} environment ready.")

        self.step("Installing packages (this is the slow part)", 8)
        self.run([VPY, "-m", "pip", "install", "--upgrade", "pip"])
        code = self.run([VPY, "-m", "pip", "install", "-r", os.path.join(APP_DIR, "requirements.txt")])
        if code == 0:
            code = self.run([VPY, "-m", "pip", "install", "--no-deps", ARGOS])
        if code != 0:
            self.ok = False
            self.say("Package install failed. Check your internet connection and try again.")
            return

        self.step("Adding Windows OCR language packs", 55)
        if langs:
            self.say("Windows will ask permission to add: " + ", ".join(l for l, _, _ in langs))
            if self.run(ocr_pack_command([tag for _, tag, _ in langs])) != 0:
                self.say("Permission was declined; OCR packs were skipped. (Chinese and Japanese still work without them.)")
        self.run([VPY, "-c",
                  "from winsdk.windows.media.ocr import OcrEngine;"
                  "print('Windows OCR languages:', [l.language_tag for l in OcrEngine.available_recognizer_languages])"])

        self.step("Downloading offline translation models", 70)
        if pairs:
            if self.run([VPY, "-c", model_download_code(pairs)]) != 0:
                self.say("Some models did not download; they will download on first use instead.")

        if self.shortcut.get():
            self.step("Creating the desktop shortcut", 95)
            ps = (
                "$d=[Environment]::GetFolderPath('Desktop');"
                "$s=(New-Object -ComObject WScript.Shell).CreateShortcut(\"$d\\Polyglass.lnk\");"
                f"$s.TargetPath='{VPYW}';$s.Arguments='\"{APP}\"';$s.WorkingDirectory='{APP_DIR}';"
                f"$s.IconLocation='{ICON}';"
                "$s.Description='Translate your screen in place';$s.Save()")
            self.run(["powershell", "-NoProfile", "-Command", ps])
        self.step("Done", 100)


if __name__ == "__main__":
    if sys.platform != "win32":
        sys.exit("The setup wizard only runs on Windows.")
    Wizard().mainloop()
