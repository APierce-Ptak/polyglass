"""
Polyglass - friendly setup wizard (Windows).

Steps: welcome -> pick languages -> install (venv, packages, Windows OCR packs,
offline translation models, desktop shortcut) -> done / launch.

Start it by double-clicking Install.bat (it finds a suitable Python first).
"""
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import venv
from tkinter import ttk

APP_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_DIR = os.path.join(APP_DIR, ".venv")
VPY = os.path.join(VENV_DIR, "Scripts", "python.exe")
VPYW = os.path.join(VENV_DIR, "Scripts", "pythonw.exe")
APP = os.path.join(APP_DIR, "polyglass.py")
NO_WINDOW = 0x08000000

# (label, Windows OCR tag, Argos code)
LANGS = [
    ("Chinese (Simplified)", "zh-CN", "zh"),
    ("Chinese (Traditional)", "zh-TW", "zt"),
    ("Japanese", "ja-JP", "ja"),
    ("Korean", "ko-KR", "ko"),
    ("Russian", "ru-RU", "ru"),
    ("Arabic", "ar-SA", "ar"),
]
LATIN = [("Spanish", "es"), ("French", "fr"), ("German", "de"),
         ("Portuguese", "pt"), ("Italian", "it")]

BG, FG, ACCENT, MUTED = "#15171c", "#f2f3f5", "#4fc3f7", "#8b93a7"


class Wizard(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Polyglass Setup")
        self.geometry("680x540")
        self.resizable(False, False)
        self.configure(bg=BG)
        self.q = queue.Queue()
        self.lang_vars = {c: tk.BooleanVar(value=(c == "zh")) for _, _, c in LANGS}
        self.latin_vars = {c: tk.BooleanVar(value=False) for _, c in LATIN}
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
            "It takes a few minutes and needs about 3 GB of disk space and an internet connection.")).pack(anchor="w")
        self.back_btn.state(["disabled"])
        self.next_btn.config(text="Next")

    def page_langs(self):
        ttk.Label(self.body, text="Which languages?", style="H.TLabel").pack(anchor="w")
        ttk.Label(self.body, style="M.TLabel", text="Pick the languages you want translated into English. You can rerun this wizard to add more.").pack(anchor="w", pady=(2, 14))
        grid = ttk.Frame(self.body)
        grid.pack(anchor="w")
        for i, (label, _tag, code) in enumerate(LANGS):
            ttk.Checkbutton(grid, text=label, variable=self.lang_vars[code]).grid(
                row=i % 3, column=i // 3, sticky="w", padx=(0, 40), pady=3)
        ttk.Label(self.body, text="Latin-script languages (auto-detected)", style="M.TLabel").pack(anchor="w", pady=(16, 4))
        g2 = ttk.Frame(self.body)
        g2.pack(anchor="w")
        for i, (label, code) in enumerate(LATIN):
            ttk.Checkbutton(g2, text=label, variable=self.latin_vars[code]).grid(
                row=i // 3, column=i % 3, sticky="w", padx=(0, 30), pady=3)
        ttk.Separator(self.body).pack(fill="x", pady=16)
        ttk.Checkbutton(self.body, text="Create a desktop shortcut", variable=self.shortcut).pack(anchor="w", pady=2)
        ttk.Checkbutton(self.body, text="Start Polyglass when setup finishes", variable=self.launch).pack(anchor="w", pady=2)
        ttk.Label(self.body, style="M.TLabel", wraplength=620, text=(
            "Windows will ask for permission (a UAC prompt) once, to add the OCR language packs.")).pack(anchor="w", pady=(14, 0))
        self.back_btn.state(["!disabled"])
        self.next_btn.config(text="Install")

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
            "  Ctrl+Alt+C   clear the overlay\n"
            "  Ctrl+Alt+Q   quit\n\n"
            "Open the app, click the window with foreign text, then press Ctrl+Alt+T."
            if good else
            "Something went wrong. Scroll the log on the previous page or check\n"
            "setup_wizard.log in the app folder, then run Install.bat again.")
        ttk.Label(self.body, text=text, justify="left").pack(anchor="w", pady=(10, 0))
        self.next_btn.state(["!disabled"])
        self.back_btn.state(["disabled"])
        self.next_btn.config(text="Finish")

    # ---------------------------------------------------------------- worker
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
        langs = [(l, t, c) for l, t, c in LANGS if self.lang_vars[c].get()]
        argos = [c for _, _, c in langs] + [c for _, c in LATIN if self.latin_vars[c].get()]

        self.step("Creating a private Python environment", 3)
        if not os.path.exists(VPY):
            venv.EnvBuilder(with_pip=True).create(VENV_DIR)
        self.say(f"Python {sys.version.split()[0]} environment ready.")

        self.step("Installing packages (this is the slow part)", 8)
        self.run([VPY, "-m", "pip", "install", "--upgrade", "pip"])
        code = self.run([VPY, "-m", "pip", "install", "-r", os.path.join(APP_DIR, "requirements.txt")])
        if code != 0:
            self.ok = False
            self.say("Package install failed. Check your internet connection and try again.")
            return

        self.step("Adding Windows OCR language packs", 55)
        if langs:
            cmds = "; ".join(
                f'Add-WindowsCapability -Online -Name Language.OCR~~~{tag}~~~0.0.1.0' for _, tag, _ in langs)
            ps = ("Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden "
                  f"-ArgumentList '-NoProfile','-Command','{cmds}'")
            self.say("Windows will ask permission to add: " + ", ".join(l for l, _, _ in langs))
            if self.run(["powershell", "-NoProfile", "-Command", ps]) != 0:
                self.say("Permission was declined; OCR packs were skipped. (Chinese still works without them.)")
        self.run([VPY, "-c",
                  "from winsdk.windows.media.ocr import OcrEngine;"
                  "print('Windows OCR languages:', [l.language_tag for l in OcrEngine.available_recognizer_languages])"])

        self.step("Downloading offline translation models", 70)
        if argos:
            snippet = (
                "import argostranslate.package as p, argostranslate.translate as t\n"
                "have={l.code for l in t.get_installed_languages()}\n"
                "p.update_package_index(); av=p.get_available_packages()\n"
                f"for c in {argos!r}:\n"
                "    pk=next((x for x in av if x.from_code==c and x.to_code=='en'),None)\n"
                "    if not pk: print('no model for',c); continue\n"
                "    print('downloading',c,'->en'); p.install_from_path(pk.download()); print('installed',c)\n")
            if self.run([VPY, "-c", snippet]) != 0:
                self.say("Some models did not download; they will download on first use instead.")

        if self.shortcut.get():
            self.step("Creating the desktop shortcut", 95)
            ps = (
                "$d=[Environment]::GetFolderPath('Desktop');"
                "$s=(New-Object -ComObject WScript.Shell).CreateShortcut(\"$d\\Polyglass.lnk\");"
                f"$s.TargetPath='{VPYW}';$s.Arguments='\"{APP}\"';$s.WorkingDirectory='{APP_DIR}';"
                "$s.Description='Translate your screen in place';$s.Save()")
            self.run(["powershell", "-NoProfile", "-Command", ps])
        self.step("Done", 100)


if __name__ == "__main__":
    if sys.platform != "win32":
        sys.exit("The setup wizard only runs on Windows.")
    Wizard().mainloop()
