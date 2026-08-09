"""Utility to sanity-check and map numeric type badge PNGs to Pokémon types.

It infers the type for each badge by sampling background pixels and matching
against canonical type colors, then solves a global minimum-cost assignment.

Run:
    ./.venv/Scripts/python.exe scripts/map_type_badges.py
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Dict, List, Tuple

from PIL import Image


# Canonical-ish type colors used by many charts (sRGB).
TYPE_COLORS: Dict[str, Tuple[int, int, int]] = {
    "normal": (168, 167, 122),
    "fire": (238, 129, 48),
    "water": (99, 144, 240),
    "electric": (247, 208, 44),
    "grass": (122, 199, 76),
    "ice": (150, 217, 214),
    "fighting": (194, 46, 40),
    "poison": (163, 62, 161),
    "ground": (226, 191, 101),
    "flying": (169, 143, 243),
    "psychic": (249, 85, 135),
    "bug": (166, 185, 26),
    "rock": (182, 161, 54),
    "ghost": (115, 87, 151),
    "dragon": (111, 53, 252),
    "dark": (112, 87, 70),
    "steel": (183, 183, 206),
    "fairy": (214, 133, 173),
}


def _dist_rgb(a: Tuple[int, int, int], b: Tuple[int, int, int]) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def _sample_background_rgb(img: Image.Image) -> Tuple[int, int, int]:
    rgba = img.convert("RGBA")
    w, h = rgba.size
    px = rgba.load()

    # Sample a set of points near the edges/corners, avoiding the very edge where
    # some sprites have a 1px border.
    points = []
    offsets = [3, 6, 9]
    for o in offsets:
        points.extend(
            [
                (o, o),
                (w - 1 - o, o),
                (o, h - 1 - o),
                (w - 1 - o, h - 1 - o),
                (w // 2, o),
                (w // 2, h - 1 - o),
                (o, h // 2),
                (w - 1 - o, h // 2),
            ]
        )

    samples: List[Tuple[int, int, int]] = []
    for x, y in points:
        r, g, b, a = px[x, y]
        if a < 200:
            continue
        samples.append((r, g, b))

    if not samples:
        # Fallback: just pick center.
        r, g, b, _a = px[w // 2, h // 2]
        return (r, g, b)

    # Use median per channel for robustness.
    rs = sorted(c[0] for c in samples)
    gs = sorted(c[1] for c in samples)
    bs = sorted(c[2] for c in samples)
    mid = len(samples) // 2
    return (rs[mid], gs[mid], bs[mid])


def _hungarian(cost: List[List[float]]) -> List[int]:
    """Solve assignment: row->col minimizing total cost.

    Returns: assignment list `assign[row] = col`.
    """

    n = len(cost)
    u = [0.0] * (n + 1)
    v = [0.0] * (n + 1)
    p = [0] * (n + 1)
    way = [0] * (n + 1)

    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [float("inf")] * (n + 1)
        used = [False] * (n + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = float("inf")
            j1 = 0
            for j in range(1, n + 1):
                if used[j]:
                    continue
                cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                if cur < minv[j]:
                    minv[j] = cur
                    way[j] = j0
                if minv[j] < delta:
                    delta = minv[j]
                    j1 = j
            for j in range(0, n + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break

    assign = [-1] * n
    for j in range(1, n + 1):
        if p[j] != 0:
            assign[p[j] - 1] = j - 1
    return assign


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    badge_dir = root / "sprites_repo" / "sprites" / "types" / "generation-viii" / "sword-shield"

    files = sorted(badge_dir.glob("*.png"), key=lambda p: int(p.stem))
    if len(files) < 18:
        raise SystemExit(f"Expected at least 18 pngs in {badge_dir}, found {len(files)}")

    file_nums = [int(p.stem) for p in files[:18]]
    file_rgbs = []
    for p in files[:18]:
        img = Image.open(p)
        rgb = _sample_background_rgb(img)
        file_rgbs.append(rgb)

    types = list(TYPE_COLORS.keys())
    cost = [[_dist_rgb(file_rgbs[i], TYPE_COLORS[t]) for t in types] for i in range(18)]
    assign = _hungarian(cost)  # row=fileIndex -> col=typeIndex

    file_to_type: Dict[int, str] = {}
    type_to_file: Dict[str, int] = {}
    for i, t_idx in enumerate(assign):
        fnum = file_nums[i]
        t = types[t_idx]
        file_to_type[fnum] = t
        type_to_file[t] = fnum

    print("# Inferred mapping for generation-viii/sword-shield (numeric badge filenames)")
    print("# file -> type")
    for fnum in sorted(file_to_type):
        print(f"{fnum:>2}.png -> {file_to_type[fnum]}")

    print("\n# type -> file (use this in frontend mapping)")
    for t in types:
        print(f"{t:>9} -> {type_to_file[t]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
