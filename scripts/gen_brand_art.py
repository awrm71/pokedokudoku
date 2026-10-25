"""Generate the Route 81 brand art: the route sign's grass and the tall-grass
tuft.

Writes:
  frontend/brand-art.css   CSS variables holding the pixel art as SVG data URIs
                           --px-grass         one tuft (logo greens)
                           --px-grass-dim     the same tuft in quiet greys, for
                                              empty squares
                           --px-sign-plate    the sign: frame, sky, hills, trees
                           --px-sign-clouds   cloud strip that drifts across it
                           --px-sign-back     grass bed behind the sign
                           --px-sign-front    grass clumps + flowers in front
                           (the two grass layers are 4-frame sprite sheets,
                           frames stacked vertically: a wind gust that travels
                           across the grass)
  frontend/brand/*.png     the route sign layers (PNG: exact pixels, tiny files)
  frontend/favicon.svg     one tuft on a dark tile

The tuft is our own drawing in the spirit of route tall grass: seven pointed
leaves fanning out from a centre, painted back to front with a dark outline per
leaf. Edit LEAVES, the palettes or the placements, then rerun from the repo
root:
    python scripts/gen_brand_art.py
The sign CSS in shell.css assumes the sizes printed at the end (shown at 2x on
desktop and 4/3x on phones).
"""
import math
import random
import urllib.parse

N = 16
CX, CY = 8.0, 10.5
# (angle from straight up, clockwise +; length; half-width), back to front
LEAVES = [(-22, 9.4, 1.5), (24, 9.0, 1.5), (-62, 8.2, 1.45), (64, 8.0, 1.45),
          (-106, 6.4, 1.35), (108, 6.2, 1.35), (0, 6.2, 1.4)]

LOGO = {"o": "#16361f", "d": "#2c6b3c", "m": "#4d9a55", "l": "#93d17c"}
DIM = {"o": "#3d3a47", "d": "#24222b", "m": "#2a2832", "l": "#34313d"}    # empty squares

# ── Route sign ────────────────────────────────────────────────────────────
# A location banner in the spirit of the DS games: a framed plate with a small
# landscape (sky, drifting clouds, hills, trees), standing in tall grass with a
# few flowers. All sizes are art pixels; shell.css shows them at 2x (desktop)
# and 4/3x (phones). Canvas is CW x CH; the plate sits at (PX0, PY0).
CW, CH = 164, 40                           # room on each side so swaying blades never hit an edge
PX0, PY0, PW, PH = 17, 3, 130, 28           # plate rect in canvas
GROUND = CH - 1
FRAMES = 16                                 # wind cycle; shown at ~100ms a frame

# ── palettes ───────────────────────────────────────────────────────────────
OUT = "#0b0a0e"
GR = {"o": "#10261a", "d": "#1f5530", "m": "#368342", "l": "#5fb763", "h": "#a8e486"}
GR_BACK = {"o": "#0e2017", "d": "#1a4429", "m": "#2b6a39", "l": "#4a9852", "h": "#7fc46f"}
FLOWERS = [("#f2f0ea", "#f6d24a"), ("#ef6a5a", "#ffd9a8"), ("#f6d24a", "#ffffff")]   # petal, centre

def grid(w, h):
    return [[None] * w for _ in range(h)]

# ── plate ──────────────────────────────────────────────────────────────────
def plate():
    g = grid(PW, PH)
    sky = ["#8cc6e4", "#a3d1ea", "#bcdeee", "#d4e9ef", "#e4f0ee"]
    inner_top, inner_bot = 3, PH - 4
    for y in range(PH):
        for x in range(PW):
            # rounded corners: cut 2px
            corner = (x < 2 or x > PW - 3) and (y < 2 or y > PH - 3)
            if corner and not ((x in (1, PW - 2)) and (y in (1, PH - 2))):
                continue
            edge = x == 0 or y == 0 or x == PW - 1 or y == PH - 1 or ((x in (1, PW - 2)) and (y in (1, PH - 2)))
            if edge: g[y][x] = OUT; continue
            if x in (1, 2, PW - 2, PW - 3) or y in (1, 2, PH - 2, PH - 3):
                g[y][x] = "#f8f5ee"; continue                    # white frame
            if x == 3 or x == PW - 4 or y == 3 or y == PH - 4:
                g[y][x] = "#a9b8bd"; continue                    # inner line
            # sky bands with a checker dither on each boundary row
            yy = y - 4
            band = min(len(sky) - 1, yy * len(sky) // (PH - 8))
            nb = min(len(sky) - 1, band + 1)
            c = sky[band]
            g[y][x] = c
    # hills on the right: far (pale) then near
    def hill(cx, r, top, col, edge):
        for x in range(4, PW - 4):
            d = (x - cx) / r
            if abs(d) >= 1: continue
            h = top + (PH - 5 - top) * (1 - math.sqrt(1 - d * d)) ** 1.0
            for y in range(int(h), PH - 4):
                g[y][x] = col
            g[int(h)][x] = edge
    hill(108, 34, 11, "#9cc9a6", "#b6dabc")
    hill(126, 22, 14, "#7db58a", "#98c9a2")
    hill(84, 20, 18, "#7db58a", "#98c9a2")
    # a few trees on the near hill: little rounded pixel trees
    for tx in (112, 117, 121):
        ty = 15 if tx != 117 else 14
        for dy, row in enumerate([".ttt.", "ttttt", "tTTTt", ".ttt.", "..b.."]):
            for dx, c in enumerate(row):
                X, Y = tx + dx - 2, ty + dy
                if c != "." and 4 <= X < PW - 4 and Y < PH - 4:
                    g[Y][X] = {"t": "#4f9a5e", "T": "#3c7f4b", "b": "#6b5a43"}[c]
    # ground strip along the bottom of the scene
    for x in range(4, PW - 4):
        g[PH - 5][x] = "#7cbf6e"; g[PH - 6][x] = g[PH - 6][x] if (x % 3) else "#8fcb7c"
    return g

# ── clouds (a tile the width of the plate's inside, scrolls) ───────────────
def clouds():
    W, H = PW - 8, 12
    g = grid(W, H)
    shapes = [(10, 2, ["..www...", ".wwwwww.", "wwwwwwww", ".ssssss."]),
              (62, 5, ["...ww..", ".wwwwww", "wwwwwww", ".sssss."]),
              (96, 1, [".www..", "wwwwww", ".ssss."])]
    for x0, y0, rows in shapes:
        for dy, row in enumerate(rows):
            for dx, c in enumerate(row):
                if c != ".": g[y0 + dy][(x0 + dx) % W] = "#ffffff" if c == "w" else "#e3eef3"
    return g

# ── grass ──────────────────────────────────────────────────────────────────
def blade(g, pal, bx, base, height, width, lean, sway):
    H, W = len(g), len(g[0]); flip = lean < 0; pts = {}
    for k in range(height + 1):
        t = k / height
        cx = bx + (lean + sway) * (t ** 1.6)
        w = width * (1 - t) ** 0.9
        y = base - k
        for x in range(int(cx - w - 2), int(cx + w + 3)):
            if not (0 <= x < W and 0 <= y < H): continue
            d = x + 0.5 - cx
            if abs(d) <= w + 0.35:
                rel = d / (w + 0.35)
                lit = rel > 0.1 if flip else rel < -0.1
                c = ("h" if lit else "l") if t > 0.78 else ("l" if lit else ("m" if abs(rel) < 0.45 else "d"))
                pts[(x, y)] = c
    for (x, y) in pts:
        for dx, dy in ((1, 0), (-1, 0), (0, -1)):
            p = (x + dx, y + dy)
            if p not in pts and 0 <= p[0] < W and 0 <= p[1] < H: g[p[1]][p[0]] = pal["o"]
    for (x, y), c in pts.items(): g[y][x] = pal[c]

def wind(x, frame, amp):
    """Travelling wave: a gust moves left to right across the bed. Every blade
    at a given x bends the same way, the way a real gust does."""
    return round(amp * math.sin(2 * math.pi * (frame / FRAMES - x / 80)))


def taper(x):
    """0 at the canvas edges rising to 1 a little way in, so the grass ends in
    a soft mound instead of being cut off square."""
    m = min(x, CW - x)
    t = max(0.0, min(1.0, (m - 5) / 16))
    return t * t * (3 - 2 * t)

def flower(g, x, y, petal, centre, dy):
    y += dy
    for (dx, dyy, c) in ((0, -1, petal), (-1, 0, petal), (1, 0, petal), (0, 1, petal), (0, 0, centre)):
        X, Y = x + dx, y + dyy
        if 0 <= X < CW and 0 <= Y < CH: g[Y][X] = c
    for (dx, dyy) in ((-1, -1), (1, -1), (-1, 1), (1, 1), (0, -2), (-2, 0), (2, 0)):
        X, Y = x + dx, y + dyy
        if 0 <= X < CW and 0 <= Y < CH and g[Y][X] is None: g[Y][X] = OUT
    # stem
    for s in range(2, 5):
        if y + s < CH and g[y + s][x] is None: g[y + s][x] = GR["m"]

def back_bed(frame):
    rnd = random.Random(7); g = grid(CW, CH); bl = []
    n, lo, hi = 60, 6, CW - 6
    for i in range(n):
        x = lo + (i + rnd.uniform(0.1, 0.9)) * (hi - lo) / n
        edge = abs(x - CW / 2) / (PW / 2)           # 0 mid-sign, 1 at the sign's sides
        hmax = 8 + 15 * min(1, edge) ** 2           # taller at the sides: wraps the plate
        h = int(rnd.uniform(0.7, 1.0) * hmax * taper(x))
        if h < 3:
            continue
        lean = rnd.choice([-1, 1]) * rnd.uniform(1.0, 2.6)
        if min(x, CW - x) < 22:                     # outer blades lean inward
            lean = abs(lean) if x < CW / 2 else -abs(lean)
        bl.append((x, GROUND - rnd.randint(0, 2), h, rnd.uniform(1.7, 2.7), lean))
    for (x, base, h, w, lean) in sorted(bl, key=lambda b: -b[2]):
        blade(g, GR_BACK, x, base, h, w, lean, wind(x, frame, 1.5))
    return g

CORNER = [(-7, 14, 2.1, -2.6), (-3, 20, 2.4, -1.4), (1, 16, 2.3, 1.8), (5, 23, 2.5, 2.8),
          (9, 13, 2.0, 2.4), (-10, 10, 1.8, -2.0), (12, 11, 1.8, 2.2), (-5, 8, 1.9, 1.5)]
BASE = [(37, 8, 1.8, -1.6), (53, 9, 1.9, 1.8), (65, 6, 1.7, -1.4), (82, 7, 1.8, -2.0),
        (99, 6, 1.7, 1.7), (111, 9, 1.9, -1.6), (127, 7, 1.8, 1.5)]
FLOWER_SPOTS = [(12, 30, 0), (27, 31, 1), (45, 35, 2), (73, 35, 0), (105, 34, 1), (137, 30, 2), (152, 30, 1)]

def front(frame):
    g = grid(CW, CH); bl = []
    for cx, side in ((PX0 + 1, 1), (PX0 + PW - 1, -1)):
        for (dx, h, w, lean) in CORNER: bl.append((cx + dx * side, h, w, lean * side))
    bl += BASE
    for (x, h, w, lean) in sorted(bl, key=lambda b: -b[1]):
        blade(g, GR, x, GROUND, h, w, lean, wind(x, frame, 2.0))
    for (x, y, k) in FLOWER_SPOTS:
        petal, centre = FLOWERS[k]
        flower(g, x + wind(x, frame, 1.4), y, petal, centre, 0)
    for x in range(CW):
        if any(g[y][x] for y in range(CH - 3, CH)): g[GROUND][x] = GR["o"]
    return g



def leaf_cells(angle, length, hw):
    th = math.radians(angle)
    ux, uy = math.sin(th), -math.cos(th)
    px, py = -uy, ux
    cells = {}
    for y in range(N):
        for x in range(N):
            dx, dy = x + 0.5 - CX, y + 0.5 - CY
            s, t = dx * ux + dy * uy, dx * px + dy * py
            if s < 0.3 or s > length:
                continue
            f = s / length
            w = hw * min(1, f / 0.4) ** 0.6 if f < 0.4 else hw * ((1 - f) / 0.6) ** 0.75
            if abs(t) <= w + 0.25:
                lit = (t / (w + 0.25) < 0) == (ux >= 0)
                cells[(x, y)] = ("l" if f > 0.45 else "m") if lit else ("d" if f < 0.5 else "m")
    return cells


def tuft():
    g = [["."] * N for _ in range(N)]
    for leaf in LEAVES:
        cells = leaf_cells(*leaf)
        for (x, y) in cells:
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                p = (x + dx, y + dy)
                if p not in cells and 0 <= p[0] < N and 0 <= p[1] < N:
                    g[p[1]][p[0]] = "o"
        for (x, y), c in cells.items():
            g[y][x] = c
    rows = [y for y, r in enumerate(g) if any(c != "." for c in r)]
    cols = [x for x in range(N) if any(g[y][x] != "." for y in range(N))]
    return [r[min(cols):max(cols) + 1] for r in g[min(rows):max(rows) + 1]]


def paint(tile, pal):
    return [[pal[c] if c != "." else None for c in row] for row in tile]


def sheet(frames_):
    """Frames stacked vertically into one sprite sheet (a list of rows)."""
    out = []
    for f in frames_:
        out += f
    return out


def png(colors, path):
    """Write a colour grid as a PNG with transparency. Small and exact, which
    matters for the sign: as SVG rects the four-frame grass ran to ~1 MB."""
    from PIL import Image
    h, w = len(colors), len(colors[0])
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    px = img.load()
    for y, row in enumerate(colors):
        for x, c in enumerate(row):
            if c:
                px[x, y] = (int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16), 255)
    img.save(path, optimize=True)


def svg(colors, bg=None, pad=0):
    h, w = len(colors), len(colors[0])
    rects = "".join(f"<rect x='{x + pad}' y='{y + pad}' width='1' height='1' fill='{c}'/>"
                    for y, row in enumerate(colors) for x, c in enumerate(row) if c)
    W, H = w + 2 * pad, h + 2 * pad
    back = f"<rect width='{W}' height='{H}' rx='3' fill='{bg}'/>" if bg else ""
    return (f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {W} {H}' "
            f"shape-rendering='crispEdges'>{back}{rects}</svg>")


def uri(s):
    return "data:image/svg+xml," + urllib.parse.quote(s, safe="/:=' ")


if __name__ == "__main__":
    t = tuft()
    css = (
        "/* brand-art.css — generated by scripts/gen_brand_art.py; edit there, not here. */\n"
        ":root {\n"
        f'  --px-grass: url("{uri(svg(paint(t, LOGO)))}");\n'
        f'  --px-grass-dim: url("{uri(svg(paint(t, DIM)))}");\n'
        '  --px-sign-plate: url("./brand/sign-plate.png");\n'
        '  --px-sign-clouds: url("./brand/sign-clouds.png");\n'
        '  --px-sign-back: url("./brand/sign-grass-back.png");\n'
        '  --px-sign-front: url("./brand/sign-grass-front.png");\n'
        "}\n"
    )
    import os
    os.makedirs("frontend/brand", exist_ok=True)
    png(plate(), "frontend/brand/sign-plate.png")
    png(clouds(), "frontend/brand/sign-clouds.png")
    png(sheet([back_bed(f) for f in range(FRAMES)]), "frontend/brand/sign-grass-back.png")
    png(sheet([front(f) for f in range(FRAMES)]), "frontend/brand/sign-grass-front.png")
    open("frontend/brand-art.css", "w").write(css)
    open("frontend/favicon.svg", "w").write(svg(paint(t, LOGO), bg="#121117", pad=2) + "\n")
    print(f"tuft {len(t[0])}x{len(t)}; sign canvas {CW}x{CH}, plate {PW}x{PH} at ({PX0},{PY0}), "
          f"grass {FRAMES} frames")
