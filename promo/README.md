# Promo film

Source for the film at the top of the website: three screens (a Japanese game, a Spanish video, a Chinese PDF) are translated in place by a sliding pane of glass, then merge into the logo. Every translation shown is real Polyglass output.

- `promo.html`: the animation. `render(t)` draws the frame at `t` seconds; `?layout=full|wide|tall` picks the cut.
- `render.js`: captures frames in Edge and encodes them with ffmpeg.
- `split.py`: splits `docs/logo.png` into `parrot.png` and `pane.png`, so the parrot can land on the pane.
- `card.html`: the 1200×630 link-preview card (`docs/social.png`).

Colours come from the logo: ink `#16323a`, teal `#2ca7b8` / `#1c7f8d`, glass `#c6ecf5`, and the parrot's red `#e5392f`, yellow `#ffcd2e` and blue `#2272d6`. The font is Nunito.

## Render

Needs Node and Microsoft Edge. From this folder:

```
npm install
set LAYOUT=wide&& node render.js video ..\docs\promo-wide.mp4
set LAYOUT=tall&& set DSF=1.8&& node render.js video ..\docs\promo-tall.mp4
set LAYOUT=full&& node render.js video promo.mp4
```

`node render.js stills 4.8 12` saves single frames (`still_<layout>_<t>.png`) for checking.
