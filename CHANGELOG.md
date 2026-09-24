# Changelog

Stable releases of Polyglass. Download the latest from the [Releases page](https://github.com/APierce-Ptak/polyglass/releases/latest).

## 0.3.3 (2026-09-24)

- **`polyglass.log` starts fresh each launch.** It records the text Polyglass reads off the screen, and it used to keep everything from every session, growing without limit in live mode. Now it only holds the current session.
- Added a Privacy section to the README and the website: what Polyglass downloads and from where (nothing is ever uploaded).

## 0.3.2 (2026-09-24)

- **Live mode now notices new text on a still screen.** It compared the whole screen at once, where a few lines of text barely register, so new dialogue was missed until something big changed (like the background). It now compares small blocks of the screen. In a test with six text changes, it went from 3 translated to all 6, each within about 2 seconds.

## 0.3.1 (2026-09-24)

- **Adding a Windows text-recognition pack is more reliable, and failures are explained.** If Windows' first installer silently does nothing (as on some Windows 10 PCs), Polyglass retries with DISM. If that fails too, the bar offers to open Windows' language settings, where adding the language installs it.
- Packs are recorded for `Uninstall.bat` only once Windows confirms they're installed (a failed install was recorded as added).
- Each step and error of the pack install is logged to `%TEMP%\polyglass_add_ocr.log`.

## 0.3.0 (2026-09-24)

- **Download sizes are shown before downloading.** The language bar's dropdowns show each missing language's size (for example ⬇ 238 MB) instead of just "Download", and the download message includes it. Setup shows the size for the languages you pick, and skips models that are already installed.
- Sizes come from the model server in about a second, in the background; offline, it falls back to "Download".

## 0.2.0 (2026-09-24)

- **About 60% smaller install.** The app's packages dropped from 1.2 GB to about 450 MB: PyTorch, stanza and spaCy are no longer installed. Argos Translate is now only used to download models, which Polyglass runs through CTranslate2 directly.
- **Starts faster:** about 0.4 s to load instead of 3.4 s.
- Downloaded model files are deleted once unpacked, instead of being kept (about 115 MB each).
- Added a test suite (`.venv\Scripts\python -m unittest`).

## 0.1.0 (2026-09-24)

First stable release.

- Transparent, click-through overlay that translates the text on screen in place, fully offline.
- Language bar at the top of the screen: From and To dropdowns (a check when installed, Download when not) with a swap button.
- Detect language, or a fixed From language.
- Reads Latin text with the From language's Windows text-recognition pack, and offers to add a missing one.
- Hotkeys: Ctrl+Alt+T translate, L live mode, D swap, C clear, Q quit.
- Setup wizard (`Install.bat`) and uninstaller (`Uninstall.bat`).
- Optional `"monitor"` setting to use a screen other than the main one.
