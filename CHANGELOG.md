# Changelog

Stable releases of Polyglass. Download the latest from the [Releases page](https://github.com/APierce-Ptak/polyglass/releases/latest).

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
