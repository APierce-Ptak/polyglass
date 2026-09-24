"""Split docs/logo.png into parrot.png (pane pixels removed) for the video."""
import os
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))

SRC = os.path.join(HERE, "..", "docs", "logo.png")
OUT = os.path.join(HERE, "parrot.png")
QUAD = [(207, 222), (420, 174), (378, 424), (158, 468)]

im = Image.open(SRC).convert("RGBA")
px = im.load()
W, H = im.size

# Region the pane covers, grown a little so its anti-aliased edge goes too.
mask = Image.new("L", (W, H), 0)
d = ImageDraw.Draw(mask)
d.polygon(QUAD, fill=255)
d.line(QUAD + [QUAD[0]], fill=255, width=16, joint="curve")
m = mask.load()


def pane_colour(r, g, b):
    teal = r < 150 and g > 120 and b > 140 and b - r > 50       # border and its edge
    glass = r > 150 and g > 200 and b > 220 and b >= r          # tint and highlights
    return teal or glass


# Pane on its own: a drawn pane (so nothing is missing under the parrot),
# with the logo's real pane pixels on top.
pane = Image.new("RGBA", (W, H), (0, 0, 0, 0))
pd = ImageDraw.Draw(pane)
inner = [(212, 229), (412, 182), (372, 417), (166, 460)]
pd.polygon(QUAD, fill=(44, 167, 184, 255))
pd.polygon(inner, fill=(198, 236, 245, 255))
pp = pane.load()

for y in range(H):
    for x in range(W):
        r, g, b, a = px[x, y]
        if m[x, y] and a and pane_colour(r, g, b):
            if pp[x, y][3]:
                pp[x, y] = (r, g, b, a)
            px[x, y] = (0, 0, 0, 0)
im.save(OUT)
pane.save(OUT.replace("parrot.png", "pane.png"))
print("saved", OUT)
