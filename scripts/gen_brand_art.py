"""Generate the Route 81 brand art: the route sign's grass and the tall-grass
tuft.

Writes:
  frontend/brand-art.css   CSS variables holding the pixel art as SVG data URIs
                           --px-grass         one tuft (logo greens)
                           --px-grass-dim     the same tuft in quiet greys, for
                                              empty squares
                           --px-sign-back     grass bed behind the route sign
                           --px-sign-front    clumps in front of the sign
                           (both are 2-frame sprite sheets, frames stacked
                           vertically, for the sway animation in shell.css)
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
import urllib.parse

N = 16
CX, CY = 8.0, 10.5
# (angle from straight up, clockwise +; length; half-width), back to front
LEAVES = [(-22, 9.4, 1.5), (24, 9.0, 1.5), (-62, 8.2, 1.45), (64, 8.0, 1.45),
          (-106, 6.4, 1.35), (108, 6.2, 1.35), (0, 6.2, 1.4)]

LOGO = {"o": "#16361f", "d": "#2c6b3c", "m": "#4d9a55", "l": "#93d17c"}
DIM = {"o": "#3d3a47", "d": "#24222b", "m": "#2a2832", "l": "#34313d"}    # empty squares

# ── Route sign grass ───────────────────────────────────────────────────────
SIGN_W = 150                  # art pixels; the sign plate sits 10px in from each side
SIGN_BACK_H, SIGN_FRONT_H = 20, 22
SIGN_PAL = {"o": "#0f2416", "d": "#235a33", "m": "#3c8a4a", "l": "#6cc070", "h": "#b6ea8f"}
# Front blades, hand placed: (x, height, half-width, lean). A clump over each
# bottom corner of the sign, short blades along its base below the letters.
CORNER = [(-7, 13, 2.1, -2.6), (-3, 18, 2.4, -1.4), (1, 15, 2.3, 1.8), (5, 20, 2.5, 2.8),
          (9, 12, 2.0, 2.4), (-10, 9, 1.8, -2.0), (12, 10, 1.8, 2.2)]
BASE_BLADES = [(30, 7, 1.8, -1.6), (42, 8, 1.9, 1.8), (47, 6, 1.7, -1.4), (60, 6, 1.7, 1.5),
               (71, 7, 1.8, -2.0), (86, 6, 1.7, 1.7), (98, 6, 1.7, 1.6), (104, 8, 1.9, -1.8),
               (118, 7, 1.8, 1.5)]


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


def _blade(g, bx, base, height, width, lean, sway):
    """One tapered blade, lit on the side it leans away from, outlined."""
    H, W = len(g), len(g[0])
    flip = lean < 0
    pts = {}
    for k in range(height + 1):
        t = k / height
        cx = bx + (lean + sway) * (t ** 1.6)
        w = width * (1 - t) ** 0.9
        y = base - k
        for x in range(W):
            d = x + 0.5 - cx
            if abs(d) <= w + 0.35 and 0 <= y < H:
                rel = d / (w + 0.35)
                lit = rel > 0.1 if flip else rel < -0.1
                if t > 0.78:
                    c = "h" if lit else "l"
                elif lit:
                    c = "l"
                elif abs(rel) < 0.45:
                    c = "m"
                else:
                    c = "d"
                pts[(x, y)] = c
    for (x, y) in pts:
        for dx, dy in ((1, 0), (-1, 0), (0, -1)):
            p = (x + dx, y + dy)
            if p not in pts and 0 <= p[0] < W and 0 <= p[1] < H:
                g[p[1]][p[0]] = "o"
    for (x, y), c in pts.items():
        g[y][x] = c


def _grass(blades, height, frame):
    g = [["."] * SIGN_W for _ in range(height)]
    for bx, base, h, w, lean, phase in sorted(blades, key=lambda b: -b[2]):   # tall first
        sway = (1 if (frame + phase) % 2 else 0) * (1 if lean >= 0 else -1)
        _blade(g, bx, base, h, w, lean, sway)
    for x in range(SIGN_W):                                  # dark ground line
        if any(g[y][x] != "." for y in range(height - 3, height)):
            g[height - 1][x] = "o"
    return [[SIGN_PAL[c] if c != "." else None for c in row] for row in g]


def sign_back():
    import random
    rnd = random.Random(7)
    n, bl = 64, []
    for i in range(n):
        lean = rnd.choice([-1, 1]) * rnd.uniform(1.0, 3.2)
        bl.append(((i + rnd.uniform(0.1, 0.9)) * SIGN_W / n, SIGN_BACK_H - 1 - rnd.randint(0, 2),
                   rnd.randint(9, 18), rnd.uniform(1.7, 2.7), lean, rnd.randint(0, 1)))
    return [_grass(bl, SIGN_BACK_H, f) for f in (0, 1)]


def sign_front():
    bl = []
    for cx, side in ((12, 1), (138, -1)):
        for i, (dx, h, w, lean) in enumerate(CORNER):
            bl.append((cx + dx * side, SIGN_FRONT_H - 1, h, w, lean * side, i % 2))
    for i, (x, h, w, lean) in enumerate(BASE_BLADES):
        bl.append((x, SIGN_FRONT_H - 1, h, w, lean, i % 2))
    return [_grass(bl, SIGN_FRONT_H, f) for f in (0, 1)]


def frames(fr):
    """Stack 2 frames vertically into one sprite sheet."""
    return fr[0] + fr[1]


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
        f'  --px-sign-back: url("{uri(svg(frames(sign_back())))}");\n'
        f'  --px-sign-front: url("{uri(svg(frames(sign_front())))}");\n'
        "}\n"
    )
    open("frontend/brand-art.css", "w").write(css)
    open("frontend/favicon.svg", "w").write(svg(paint(t, LOGO), bg="#121117", pad=2) + "\n")
    print(f"tuft {len(t[0])}x{len(t)}; sign grass {SIGN_W}x{SIGN_BACK_H} (back), "
          f"{SIGN_W}x{SIGN_FRONT_H} (front), 2 frames each")
