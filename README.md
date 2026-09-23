# Polyglass

*See any language, in place.*

A transparent, click-through overlay for Windows that reads foreign text on your screen and draws the English translation **in the same spot**, right over the original. It works on games, videos, apps, and web pages.

- Translates Chinese, Japanese, Korean, Russian, Arabic, and Latin-script languages (Spanish, French, German, and others) into English.
- Fully offline: OCR and translation both run on your PC.
- Live mode re-translates only when the text on screen actually changes, so it doesn't waste CPU.
- Always-on status bar at the top of the screen, with a loading spinner while it works.
- Click-through: you keep using your game or app normally.

## Install

1. Download this repo (green **Code** button, then **Download ZIP**) and unzip it.
2. Double-click **`Install.bat`**.
3. Follow the setup wizard. It creates a private Python environment, installs the packages, adds Windows OCR language packs for the languages you pick, downloads the offline translation models, and makes a desktop shortcut.

Requirements: Windows 10 or 11, an internet connection for setup, and about 3 GB of free disk space. If Python 3.10-3.12 is missing, `Install.bat` installs it with winget or sends you to the download page.

## Use

Start **Polyglass** from the desktop shortcut (or `Run.bat`), click the window with foreign text, and press:

| Hotkey | Action |
|---|---|
| Ctrl+Alt+T | Translate the screen once |
| Ctrl+Alt+L | Turn live mode on or off |
| Ctrl+Alt+C | Clear the overlay |
| Ctrl+Alt+Q | Quit |

The first translation after each launch takes several seconds while the models load. After that it takes 1-3 seconds.

Games should run in **windowed or borderless** mode. Exclusive fullscreen draws above any overlay.

## How it works

1. `mss` captures the screen.
2. [RapidOCR](https://github.com/RapidAI/RapidOCR) reads Chinese and Japanese text, including stylised game fonts. Windows' built-in OCR handles other languages, with automatic language detection.
3. [Argos Translate](https://github.com/argosopentech/argos-translate) translates each line to English offline.
4. A transparent, click-through Tk window paints each translation over its source line, with the background colour sampled from the screenshot. The window is hidden from screen capture so it never reads its own output.

## Troubleshooting

Run `Debug.bat` to see a console with what the app is doing. It prints the installed OCR languages, the text found, and what it draws. The shortcut writes the same information to `polyglass.log`.

- **Says only `en-US` is installed:** re-run `Install.bat` and tick the language, then accept the Windows permission prompt.
- **Nothing appears over a game:** switch the game to windowed or borderless.
- **Wrong or odd translations:** offline models are good but not perfect. Text is translated line by line.

## Manual install

```
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python polyglass.py
```

## License

MIT
