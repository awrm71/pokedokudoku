"""Generate Route 81's app icons and the link-preview (share) image.

Writes to frontend/brand/:
  favicon-16.png, favicon-32.png   browser tab icons (pixel art, hand-sized)
  apple-touch-icon.png             180x180 iPhone/iPad home screen (full bleed;
                                   iOS rounds the corners itself)
  icon-192.png, icon-512.png       Android / web manifest (rounded tile)
  icon-maskable-512.png            Android adaptive icon (full bleed)
  og-image.png                     1200x630 preview for iMessage, LinkedIn,
                                   Discord, X, etc.
and frontend/favicon.ico (16/32/48).

Everything is pixel art drawn here and scaled up by whole numbers, using the
same grass, flowers and sign as the logo (gen_brand_art.py). The share image
sets "Route 81" and the tagline in Pixelify Sans (scripts/fonts, SIL OFL).

Run from the repo root after gen_brand_art.py:
    python scripts/gen_share_art.py
"""
import math
import os
import random
import sys

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(__file__))
import gen_brand_art as G  # noqa: E402

OUT = "frontend/brand"
FONT = os.path.join(os.path.dirname(__file__), "fonts", "PixelifySans.ttf")
INK = "#0b0a0e"
ICON_SKY = ["#7fbfe2", "#95cae7", "#abd5ec", "#c2e0ef", "#d8ebf0"]


def to_img(grid):
    h, w = len(grid), len(grid[0])
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    px = img.load()
    for y, row in enumerate(grid):
        for x, c in enumerate(row):
            if c:
                px[x, y] = (int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16), 255)
    return img


def put_flower(layer, x, y, k):
    n = len(layer)
    petal, centre = G.FLOWERS[k]
    for dx, dy, c in ((0, -1, petal), (-1, 0, petal), (1, 0, petal), (0, 1, petal), (0, 0, centre)):
        if 0 <= x + dx < len(layer[0]) and 0 <= y + dy < n:
            layer[y + dy][x + dx] = c
    for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1), (0, -2), (-2, 0), (2, 0)):
        X, Y = x + dx, y + dy
        if 0 <= X < len(layer[0]) and 0 <= Y < n and layer[Y][X] is None:
            layer[Y][X] = G.GR["o"]


# ── Icon: a little square of sky with tall grass in front ──────────────────
def icon(n, blades, flowers, cloud, hill=True, rounded=True):
    g = [[None] * n for _ in range(n)]
    horizon = int(n * 0.62)
    for y in range(n):
        band = min(len(ICON_SKY) - 1, y * len(ICON_SKY) // horizon)
        for x in range(n):
            g[y][x] = ICON_SKY[band]
    if hill:
        for x in range(n):
            d = (x - n * 0.78) / (n * 0.55)
            if abs(d) < 1:
                top = int(horizon - n * 0.17 * math.sqrt(1 - d * d))
                for y in range(top, n):
                    g[y][x] = "#9cc9a6"
                g[top][x] = "#b6dabc"
    cx, cy, rows = cloud
    for dy, row in enumerate(rows):
        for dx, c in enumerate(row):
            if c != ".":
                g[cy + dy][cx + dx] = "#ffffff" if c == "w" else "#e2edf2"
    layer = [[None] * n for _ in range(n)]
    for x, h, w, lean in sorted(blades, key=lambda b: -b[1]):
        G.blade(layer, G.GR, x, n - 1, h, w, lean, 0)
    for x, y, k in flowers:
        put_flower(layer, x, y, k)
    for y in range(n):
        for x in range(n):
            if layer[y][x]:
                g[y][x] = layer[y][x]
    if rounded:
        cut = [5, 3, 2, 1, 1] if n >= 32 else [2, 1]          # pixels off each corner row
        for i, c in enumerate(cut):
            for x in range(c):
                for X, Y in ((x, i), (n - 1 - x, i), (x, n - 1 - i), (n - 1 - x, n - 1 - i)):
                    g[Y][X] = None
        rim = [(x, y) for y in range(n) for x in range(n) if g[y][x] and any(
            not (0 <= x + dx < n and 0 <= y + dy < n) or g[y + dy][x + dx] is None
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))]
        for x, y in rim:
            g[y][x] = INK
    return g


B32 = [(4, 15, 2.3, -2.0), (9, 21, 2.7, -1.0), (14, 17, 2.5, 1.2), (19, 24, 2.9, 1.6),
       (24, 16, 2.4, 2.2), (28, 12, 2.2, 1.4), (2, 9, 2.0, -1.0), (12, 9, 2.2, -1.2), (22, 9, 2.2, 1.0),
       (16, 11, 2.3, -1.5), (30, 8, 2.0, 1.0), (7, 8, 2.0, 1.2)]
F32 = [(6, 23, 1), (26, 25, 2)]
C32 = (5, 5, ["..www..", ".wwwwww", "wwwwwww", ".sssss."])
B16 = [(4, 7, 1.7, -1.2), (8, 10, 2.0, 0.8), (12, 7, 1.7, 1.2), (6, 4, 1.4, 1.0), (14, 4, 1.3, 1.0), (2, 4, 1.3, -0.8)]


def scaled(blades, s):
    return [(x * s, round(h * s), w * s, lean * s) for x, h, w, lean in blades]


def icons():
    i16 = to_img(icon(16, B16, [(11, 12, 1)], (3, 3, [".ww.", "wwww"]), hill=False))
    i32 = to_img(icon(32, B32, F32, C32))
    full32 = to_img(icon(32, B32, F32, C32, rounded=False))
    # 180 = 36 x 5: redraw the full-bleed scene at 36px so it scales by a whole number
    s = 36 / 32
    full36 = to_img(icon(36, scaled(B32, s), [(7, 26, 1), (29, 28, 2)],
                         (6, 6, ["..www..", ".wwwwww", "wwwwwww", ".sssss."]), rounded=False))
    i16.save(f"{OUT}/favicon-16.png")
    i32.save(f"{OUT}/favicon-32.png")
    full36.resize((180, 180), Image.NEAREST).save(f"{OUT}/apple-touch-icon.png")
    i32.resize((192, 192), Image.NEAREST).save(f"{OUT}/icon-192.png")
    i32.resize((512, 512), Image.NEAREST).save(f"{OUT}/icon-512.png")
    full32.resize((512, 512), Image.NEAREST).save(f"{OUT}/icon-maskable-512.png")
    ico48 = i16.resize((48, 48), Image.NEAREST)
    ico48.save("frontend/favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)],
               append_images=[i16, i32])


# ── Share image: the sign standing in a meadow under a big sky ─────────────
def share_image():
    W, H, K = 240, 126, 5                    # 1200 x 630
    sky = ["#7fbfe2", "#8cc6e4", "#9ccde8", "#acd5ec", "#bcdcee", "#cce4ef", "#dbebf0", "#e6f0ee"]
    horizon = 74
    g = [[sky[min(len(sky) - 1, y * len(sky) // horizon)]] * W for y in range(H)]
    g = [row[:] for row in g]

    def cloud(x0, y0, rows):
        for dy, row in enumerate(rows):
            for dx, c in enumerate(row):
                if c != "." and 0 <= x0 + dx < W:
                    g[y0 + dy][x0 + dx] = "#ffffff" if c == "w" else "#e2edf2"

    def hills(cx, r, top, col, edge):
        for x in range(W):
            d = (x - cx) / r
            if abs(d) >= 1:
                continue
            h = int(top + (horizon + 10 - top) * (1 - math.sqrt(1 - d * d)))
            for y in range(h, H):
                g[y][x] = col
            g[h][x] = edge

    def tree(tx, ty):
        for dy, row in enumerate([".ttt.", "ttttt", "tTTTt", ".tTt.", "..b.."]):
            for dx, c in enumerate(row):
                X = tx + dx - 2
                if c != "." and 0 <= X < W:
                    g[ty + dy][X] = {"t": "#4f9a5e", "T": "#3c7f4b", "b": "#6b5a43"}[c]

    cloud(10, 7, ["....wwww....", "..wwwwwwww..", ".wwwwwwwwwww", "wwwwwwwwwwww", ".ssssssssss."])
    cloud(200, 12, ["...www...", ".wwwwwww.", "wwwwwwwww", ".sssssss."])
    cloud(160, 4, ["..ww..", "wwwwww", ".ssss."])
    cloud(26, 40, ["..www..", "wwwwwww", ".sssss."])
    hills(30, 72, 54, "#a6d0ae", "#bfdfc3")
    hills(200, 90, 50, "#a6d0ae", "#bfdfc3")
    hills(120, 96, 64, "#8cc29a", "#a6d3b0")
    hills(6, 48, 68, "#7ab689", "#94c8a0")
    hills(240, 56, 66, "#7ab689", "#94c8a0")
    for tx, ty in ((16, 63), (21, 61), (208, 55), (213, 57), (218, 54)):
        tree(tx, ty)
    for y in range(82, H):
        for x in range(W):
            g[y][x] = "#5c9e57" if y > 84 else "#78b86b"

    layer = [[None] * W for _ in range(H)]
    rnd = random.Random(81)
    bl = []
    n = 96
    for i in range(n):
        x = (i + rnd.uniform(0.1, 0.9)) * W / n
        edge = abs(x - W / 2) / (W / 2)
        h = int(rnd.uniform(0.6, 1.0) * (11 + 26 * edge ** 2))
        bl.append((x, H - 1 - rnd.randint(0, 3), h, rnd.uniform(2.0, 3.2), rnd.choice([-1, 1]) * rnd.uniform(1.0, 3.6)))
    for x, base, h, w, lean in sorted(bl, key=lambda b: -b[2]):
        G.blade(layer, G.GR, x, base, h, w, lean, 0)
    for i, (x, y) in enumerate([(14, 102), (36, 112), (52, 120), (80, 114), (104, 121), (138, 117),
                                (163, 111), (190, 120), (208, 108), (228, 103), (121, 112)]):
        put_flower(layer, x, y, i % 3)
    for y in range(H):
        for x in range(W):
            if layer[y][x]:
                g[y][x] = layer[y][x]

    img = to_img(g)
    sx, sy = (W - G.CW) // 2, 88 - G.CH + 1
    img.alpha_composite(to_img(G.back_bed(0)), (sx, sy))
    img.alpha_composite(to_img(G.plate()), (sx + G.PX0, sy + G.PY0))
    img.alpha_composite(to_img(G.clouds()), (sx + G.PX0 + 4, sy + G.PY0 + 4))
    img.alpha_composite(to_img(G.front(0)), (sx, sy))
    out = img.resize((W * K, H * K), Image.NEAREST)

    d = ImageDraw.Draw(out)
    d.fontmode = "1"                        # hard pixels, no anti-aliased fringe
    f_name = ImageFont.truetype(FONT, 15 * K)
    f_tag = ImageFont.truetype(FONT, 8 * K)
    for f in (f_name, f_tag):
        f.set_variation_by_axes([600])
    nx, ny = (sx + G.PX0 + 9) * K, (sy + G.PY0) * K + (G.PH * K) // 2
    d.text((nx, ny + K), "Route 81", font=f_name, fill="#f4f8f6", anchor="lm")
    d.text((nx, ny), "Route 81", font=f_name, fill="#23262b", anchor="lm")
    tx, ty = W * K // 2, (sy - 12) * K
    d.text((tx, ty + K), "Daily Pokémon logic grids", font=f_tag, fill="#e8f2f4", anchor="mm")
    d.text((tx, ty), "Daily Pokémon logic grids", font=f_tag, fill="#23262b", anchor="mm")
    out.convert("RGB").save(f"{OUT}/og-image.png", optimize=True)


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    icons()
    share_image()
    print("wrote icons, favicon.ico and og-image.png")
