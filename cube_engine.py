"""
cube_engine.py — the Cube: a Pokémon puzzle on the outside of a 3×3×3 cube.

Six faces of 3×3 squares, 54 squares in all. Every face has its own rule, and
every row and column runs all the way around the cube as a ring, the way a
slice of a Rubik's cube does: the right-hand column of the front face carries
on up the top, down the back and along the bottom.

    axis 0 = x (screen right)    axis 1 = y (screen down)    axis 2 = z (towards you)
    faces  0 +x   1 -x   2 +y   3 -y   4 +z   5 -z           (index = 2*axis + (sign < 0))
    rings  rings[a][k] = the ring of squares whose coordinate on axis a is k

A square sits on one face and in two rings: the rings of the two axes that
lie along that face. Square (f, i, j) on a face with normal axis A has
coordinate i on the lower of the other two axes and j on the higher one.
Its Pokémon must fit all three rules: the face, ring one, ring two.

Each axis is one family of rules so the cube reads at a glance: e.g. the x
rings and the ±x faces are types, the y rings and ±y faces regions, the z
rings and ±z faces traits. Every square then needs one of each.

Guarantees, checked on every generated cube (tests/test_cube.py):
  1. every square has at least `min_per_cell` valid Pokémon;
  2. all 54 squares can be filled at once with 54 different Pokémon
     (a perfect matching, kept so hints can use it);
  3. every rule narrows every square it touches (no rule that does nothing);
  4. no rule is contained in another rule it ever meets in a square.

The Reverse Cube shows a filled cube and hides the 15 rules: 6 faces of 9
Pokémon and 9 rings of 12. Each accepts any reasonable rule its Pokémon all
share; fillings are picked to keep those lists short.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

import numpy as np
import pandas as pd

import reverse_engine

N_FACES = 6
N_LINES = 15                      # 6 faces + 9 rings


def face_axis(f: int) -> int:
    return f // 2


def face_others(f: int) -> Tuple[int, int]:
    """The two axes that lie along face f, lower first."""
    a = face_axis(f)
    return tuple(x for x in range(3) if x != a)  # type: ignore[return-value]


CELLS: List[Tuple[int, int, int]] = [(f, i, j) for f in range(N_FACES) for i in range(3) for j in range(3)]
CELL_INDEX = {c: n for n, c in enumerate(CELLS)}


def ring_line(axis: int, k: int) -> int:
    return 6 + axis * 3 + k


def line_cells(line: int) -> List[Tuple[int, int, int]]:
    """Squares in a line: a face (0-5, 9 squares) or a ring (6-14, 12 squares)."""
    if line < 6:
        return [c for c in CELLS if c[0] == line]
    axis, k = divmod(line - 6, 3)
    out = []
    for f, i, j in CELLS:
        b, c = face_others(f)
        if (b == axis and i == k) or (c == axis and j == k):
            out.append((f, i, j))
    return out


# ── Rule pool ─────────────────────────────────────────────────────────────────

EXCLUDED = {"not_legendary", "evo_premature"}   # filler, and a duplicate of "baby"
MIN_SUPPORT = 15

GROUP_WEIGHT = {
    "type": 1.35, "region": 1.30, "evolution": 1.15, "flag": 0.95,
    "special": 0.80, "ability": 0.25, "move": 0.05,
}
CUBE_CAP = {"move": 1, "ability": 1}
SPECIAL = {"legendary", "mythical", "mega", "gmax", "starter", "fossil", "paradox",
           "regional_form", "baby", "ultra_beast"}


def rule_group(rid: str) -> str:
    rid = str(rid)
    if rid.startswith("type_"):
        return "type"
    if rid.startswith("region_"):
        return "region"
    if rid.startswith("move_"):
        return "move"
    if rid.startswith("ability_"):
        return "ability"
    if rid.startswith(("evo_", "evolves_by_")) or rid == "branched":
        return "evolution"
    if rid in SPECIAL:
        return "special"
    return "flag"


def family(rid: str) -> str:
    g = rule_group(rid)
    return g if g in ("type", "region") else "trait"


@dataclass
class Masks:
    """Rule masks as one bool matrix, for fast crossing counts."""
    ids: List[str]
    mat: np.ndarray
    keys: np.ndarray
    pos: Dict[str, int] = field(default_factory=dict)

    @classmethod
    def build(cls, df: pd.DataFrame, rule_masks: Dict[str, pd.Series], rule_ids: Iterable[str]) -> "Masks":
        ids = [r for r in rule_ids if r in rule_masks]
        mat = np.vstack([rule_masks[r].fillna(False).to_numpy(dtype=bool, copy=False) for r in ids])
        return cls(ids=ids, mat=mat, keys=df["key"].astype(str).to_numpy(), pos={r: i for i, r in enumerate(ids)})

    def m(self, rid: str) -> np.ndarray:
        return self.mat[self.pos[rid]]


def cube_pool(rule_masks: Dict[str, pd.Series], n_pokemon: int,
              allowed: Optional[Set[str]] = None) -> List[str]:
    out = []
    for rid, m in rule_masks.items():
        if rid in EXCLUDED or (allowed is not None and rid not in allowed):
            continue
        s = int(m.fillna(False).sum())
        g = rule_group(rid)
        if s < MIN_SUPPORT:
            continue
        if g == "move" and s > 0.35 * n_pokemon:
            continue
        if g not in ("type", "move") and s > 0.80 * n_pokemon:
            continue
        out.append(rid)
    return sorted(out)


# ── The board ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CubeBoard:
    seed: str
    rings: Tuple[Tuple[str, str, str], Tuple[str, str, str], Tuple[str, str, str]]
    faces: Tuple[str, str, str, str, str, str]
    solution: Tuple[str, ...]            # 54 keys in CELLS order

    def rules_at(self, f: int, i: int, j: int) -> Tuple[str, str, str]:
        b, c = face_others(f)
        return (self.faces[f], self.rings[b][i], self.rings[c][j])

    def line_rule(self, line: int) -> str:
        if line < 6:
            return self.faces[line]
        axis, k = divmod(line - 6, 3)
        return self.rings[axis][k]

    def solution_at(self, f, i, j) -> str:
        return self.solution[CELL_INDEX[(f, i, j)]]

    def all_rules(self) -> List[str]:
        return list(self.faces) + [r for ax in self.rings for r in ax]


def cell_mask(masks: Masks, board: CubeBoard, f: int, i: int, j: int) -> np.ndarray:
    a, b, c = board.rules_at(f, i, j)
    return masks.m(a) & masks.m(b) & masks.m(c)


def candidates(masks: Masks, board: CubeBoard, f, i, j) -> List[str]:
    return masks.keys[cell_mask(masks, board, f, i, j)].tolist()


# ── Matching (54 squares ↔ distinct Pokémon) ─────────────────────────────────

def match_cells(adj: List[List[int]]) -> Optional[List[int]]:
    """Kuhn's augmenting paths; adj[cell] = candidate pokemon indices in try order."""
    match_r: Dict[int, int] = {}
    match_l = [-1] * len(adj)

    def try_cell(c: int, seen: Set[int]) -> bool:
        for p in adj[c]:
            if p in seen:
                continue
            seen.add(p)
            if p not in match_r or try_cell(match_r[p], seen):
                match_r[p] = c
                match_l[c] = p
                return True
        return False

    for c in sorted(range(len(adj)), key=lambda i: len(adj[i])):
        if not try_cell(c, set()):
            return None
    return match_l


def _solve(masks: Masks, rings, faces, rng: random.Random) -> Optional[List[str]]:
    adj = []
    for f, i, j in CELLS:
        b, c = face_others(f)
        idx = np.flatnonzero(masks.m(faces[f]) & masks.m(rings[b][i]) & masks.m(rings[c][j])).tolist()
        rng.shuffle(idx)
        adj.append(idx)
    got = match_cells(adj)
    if got is None:
        return None
    return [str(masks.keys[p]) for p in got]


# ── Quality checks ────────────────────────────────────────────────────────────

def redundant_pair(masks: Masks, a: str, b: str) -> bool:
    ma, mb = masks.m(a), masks.m(b)
    both = int((ma & mb).sum())
    return both == int(ma.sum()) or both == int(mb.sum())


def meeting_pairs(rings, faces) -> Set[Tuple[str, str]]:
    """Every pair of rules that share at least one square."""
    pairs = set()
    for f, i, j in CELLS:
        b, c = face_others(f)
        r = (faces[f], rings[b][i], rings[c][j])
        pairs |= {(r[0], r[1]), (r[0], r[2]), (r[1], r[2])}
    return pairs


def check_board(masks: Masks, rings, faces, min_per_cell: int = 1) -> Tuple[bool, List[int]]:
    ids = list(faces) + [r for ax in rings for r in ax]
    if len(set(ids)) != N_LINES:
        return False, []
    for a, b in meeting_pairs(rings, faces):
        if redundant_pair(masks, a, b):
            return False, []
    counts = []
    for f, i, j in CELLS:
        b, c = face_others(f)
        x, y, z = masks.m(faces[f]), masks.m(rings[b][i]), masks.m(rings[c][j])
        xy, xz, yz = x & y, x & z, y & z
        n = int((xy & z).sum())
        if n < min_per_cell or not (n < xy.sum() and n < xz.sum() and n < yz.sum()):
            return False, counts
        counts.append(n)
    return True, counts


# ── Generation ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CubeSettings:
    min_per_cell: int = 2           # every square has a choice
    pair_floor: int = 10            # ring × ring crossings needed before faces are tried
    tries: int = 900
    keep: int = 4                   # valid cubes to score before picking one


# (families per axis, weight). The first always builds quickly and is the
# fallback when a rarer one runs out of tries.
TEMPLATES = [
    (("type", "region", "trait"), 5.0),
    (("region", "trait", "trait"), 1.5),
    (("type", "trait", "trait"), 1.0),
]
ALT_TEMPLATE_TRIES = 250
# Broad traits: any number may appear (a trait axis needs five), but a cube
# made only of them is dull, so they're weighted down a little.
BROAD = {"evo_final", "evolves_by_levelup", "evo_start", "single_type", "dual_type", "evo_none"}


def _weight(rid: str, support: int) -> float:
    g = rule_group(rid)
    w = GROUP_WEIGHT.get(g, 1.0)
    if g in ("region", "ability"):
        return w * 10.0
    if rid in BROAD:
        w *= 0.8
    return w * max(1.0, support) ** 0.5


def _pick(rng: random.Random, items: Sequence[str], weights: Sequence[float]) -> Optional[str]:
    total = float(sum(weights))
    if not items or total <= 0:
        return None
    r = rng.uniform(0.0, total)
    acc = 0.0
    for it, w in zip(items, weights):
        acc += w
        if acc >= r:
            return it
    return items[-1]


def _caps_ok(rid: str, chosen: List[str]) -> bool:
    g = rule_group(rid)
    if g in CUBE_CAP and sum(rule_group(r) == g for r in chosen) >= CUBE_CAP[g]:
        return False
    return True


def _score(rings, faces, counts: List[int]) -> float:
    ids = list(faces) + [r for ax in rings for r in ax]
    groups = {rule_group(r) for r in ids}
    broad = sum(r in BROAD for r in ids)
    lo = min(counts)
    tight = sum(1 for c in counts if c <= 2)
    return min(lo, 4) + len(groups) * 1.2 - tight * 0.15 - broad * 0.2


def generate_cube(
    df: pd.DataFrame,
    rule_masks: Dict[str, pd.Series],
    seed: str,
    settings: CubeSettings = CubeSettings(),
    allowed_rule_ids: Optional[Set[str]] = None,
    masks: Optional[Masks] = None,
) -> Optional[CubeBoard]:
    """Deterministic for a given seed + dataset."""
    rng = random.Random(f"cube54:{seed}")
    pool = cube_pool(rule_masks, len(df), allowed_rule_ids)
    if len(pool) < N_LINES:
        return None
    if masks is None:
        masks = Masks.build(df, rule_masks, pool)
    support = {r: int(masks.m(r).sum()) for r in pool}
    base_w = {r: _weight(r, support[r]) for r in pool}
    by_family = {f: [r for r in pool if family(r) == f] for f in ("type", "region", "trait")}
    fam_mat = {f: (np.vstack([masks.m(r) for r in ids]).astype(np.float32) if ids else None)
               for f, ids in by_family.items()}
    templates = [t for t, _ in TEMPLATES if all(len(by_family[f]) >= 5 * t.count(f) for f in set(t))]
    t_weights = [w for t, w in TEMPLATES if t in templates]
    if not templates:
        return None

    def draw(fam: str, n: int, chosen: List[str], cross: List[np.ndarray], floor: int) -> Optional[List[str]]:
        """Draw n rules of a family, each crossing every mask in `cross` with
        at least `floor` Pokémon."""
        ids = by_family[fam]
        lows = (fam_mat[fam] @ np.vstack(cross).astype(np.float32).T).min(axis=1) if cross else None
        got: List[str] = []
        for _ in range(n):
            c, w = [], []
            for k, r in enumerate(ids):
                if r in chosen or r in got or not _caps_ok(r, chosen + got):
                    continue
                if lows is not None:
                    lo = int(lows[k])
                    if lo < floor:
                        continue
                    c.append(r); w.append(base_w[r] * min(lo, 20) ** 0.5)
                else:
                    c.append(r); w.append(base_w[r])
            p = _pick(rng, c, w)
            if p is None:
                return None
            got.append(p)
        return got

    found: List[Tuple[float, tuple, List[int]]] = []
    day_template = rng.choices(templates, weights=t_weights)[0]
    rank = {"type": 0, "region": 1, "trait": 2}
    for attempt in range(settings.tries):
        if day_template != templates[0] and attempt >= ALT_TEMPLATE_TRIES and not found:
            day_template = templates[0]
        fams = list(day_template)
        rng.shuffle(fams)                               # which family lands on which axis
        order = sorted(range(3), key=lambda a: rank[fams[a]])
        rings: List[Optional[List[str]]] = [None, None, None]
        chosen: List[str] = []
        ok = True
        # Rings: narrow families first; each new ring crosses every ring
        # already placed on the other axes with room to spare.
        for step, a in enumerate(order):
            cross = [masks.m(r) for b in order[:step] for r in rings[b]]
            got = draw(fams[a], 3, chosen, cross, settings.pair_floor)
            if got is None:
                ok = False
                break
            rings[a] = got
            chosen += got
        if not ok:
            continue
        # Faces: the two faces of axis a meet the rings of the other two axes
        # in all nine combinations.
        faces: List[Optional[str]] = [None] * 6
        for a in order:
            b, c = (x for x in range(3) if x != a)
            cross = [masks.m(p) & masks.m(q) for p in rings[b] for q in rings[c]]
            got = draw(fams[a], 2, chosen, cross, settings.min_per_cell)
            if got is None:
                ok = False
                break
            faces[2 * a], faces[2 * a + 1] = got
            chosen += got
        if not ok:
            continue
        for ax in rings:
            rng.shuffle(ax)
        rt = tuple(tuple(ax) for ax in rings)
        ft = tuple(faces)
        good, counts = check_board(masks, rt, ft, settings.min_per_cell)
        if not good:
            continue
        found.append((_score(rt, ft, counts), (rt, ft), counts))
        if len(found) >= settings.keep:
            break

    for _, (rt, ft), _ in sorted(found, key=lambda t: -t[0]):
        sol = _solve(masks, rt, ft, rng)
        if sol is not None:
            return CubeBoard(seed=seed, rings=rt, faces=ft, solution=tuple(sol))
    return None


def board_from_ids(df, rule_masks, seed: str, rings, faces, masks: Optional[Masks] = None) -> Optional[CubeBoard]:
    """Rebuild a saved (pinned) cube. None if it no longer solves."""
    ids = list(faces) + [r for ax in rings for r in ax]
    if any(r not in rule_masks for r in ids) or len(faces) != 6 or len(rings) != 3:
        return None
    if masks is None:
        masks = Masks.build(df, rule_masks, ids)
    rt = tuple(tuple(ax) for ax in rings)
    ft = tuple(faces)
    sol = _solve(masks, rt, ft, random.Random(f"cube54:{seed}:pin"))
    if sol is None:
        return None
    return CubeBoard(seed=seed, rings=rt, faces=ft, solution=tuple(sol))


def validate(masks: Masks, board: CubeBoard, f: int, i: int, j: int, pokemon_key: str, used: Set[str]) -> str:
    """'ok', 'duplicate', 'does_not_match' or 'unknown_pokemon'."""
    if not pokemon_key:
        return "unknown_pokemon"
    if pokemon_key in used:
        return "duplicate"
    hit = np.flatnonzero(masks.keys == pokemon_key)
    if hit.size == 0:
        return "unknown_pokemon"
    return "ok" if bool(cell_mask(masks, board, f, i, j)[hit[0]]) else "does_not_match"


def _fillable(masks: Masks, board: CubeBoard, empty: List[Tuple[int, int, int]], taken: Set[str]) -> bool:
    adj = [[p for p in np.flatnonzero(cell_mask(masks, board, *c)).tolist() if masks.keys[p] not in taken]
           for c in empty]
    return match_cells(adj) is not None


def hint_for(masks: Masks, board: CubeBoard, f: int, i: int, j: int,
             used: Set[str], filled: Dict[Tuple[int, int, int], str]) -> Optional[str]:
    """A Pokémon for this square that leaves every empty square still fillable."""
    empty = [c for c in CELLS if c not in filled and c != (f, i, j)]
    pref = board.solution_at(f, i, j)
    cands = candidates(masks, board, f, i, j)
    order = ([pref] if pref in cands else []) + [k for k in cands if k != pref]
    for k in order:
        if k not in used and _fillable(masks, board, empty, used | {k}):
            return k
    for k in order:                       # already stranded by earlier picks
        if k not in used:
            return k
    return None


def still_solvable(masks: Masks, board: CubeBoard, filled: Dict[Tuple[int, int, int], str]) -> bool:
    empty = [c for c in CELLS if c not in filled]
    return _fillable(masks, board, empty, set(filled.values()))


# ── Reverse Cube ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ReverseCube:
    board: CubeBoard
    grid: Tuple[str, ...]                     # 54 keys, CELLS order
    answers: Tuple[Tuple[str, ...], ...]      # answers[line] = accepted rule ids

    def key_at(self, f, i, j) -> str:
        return self.grid[CELL_INDEX[(f, i, j)]]

    def ambiguity(self) -> int:
        return max(len(s) for s in self.answers)


REVERSE_PRESETS = {
    "easy":   {"max_ambiguity": 6, "fillings": 10},
    "medium": {"max_ambiguity": 4, "fillings": 16},
    "hard":   {"max_ambiguity": 2, "fillings": 28},
}


def _scoreable(rule_masks: Dict[str, pd.Series], n_pokemon: int, pool: Iterable[str]) -> List[str]:
    out = []
    for rid in pool:
        if rid in reverse_engine.SUPPRESSED_RULE_IDS or rid in EXCLUDED:
            continue
        m = rule_masks.get(rid)
        if m is None:
            continue
        if reverse_engine._too_broad(rid, int(m.fillna(False).sum()), n_pokemon):
            continue
        out.append(rid)
    return out


def _answers(masks: Masks, key_pos: Dict[str, int], grid: Sequence[str],
             keep: Optional[Set[str]] = None) -> Tuple[List[Tuple[str, ...]], int]:
    answers, worst = [], 0
    for line in range(N_LINES):
        idx = [key_pos[grid[CELL_INDEX[c]]] for c in line_cells(line)]
        sat = masks.mat[:, idx].all(axis=1)
        acc = tuple(sorted(masks.ids[j] for j in np.flatnonzero(sat) if keep is None or masks.ids[j] in keep))
        worst = max(worst, len(acc))
        answers.append(acc)
    return answers, worst


def generate_reverse_cube(df, rule_masks, seed: str, difficulty: str = "medium", boards: int = 4) -> Optional[ReverseCube]:
    preset = REVERSE_PRESETS.get(difficulty, REVERSE_PRESETS["medium"])
    scoreable = _scoreable(rule_masks, len(df), rule_masks.keys())
    masks = Masks.build(df, rule_masks, scoreable)
    key_pos = {k: i for i, k in enumerate(masks.keys.tolist())}
    rng = random.Random(f"rcube54:{seed}:{difficulty}")
    best = None
    for b in range(boards):
        board = generate_cube(df, rule_masks, f"{seed}:r{b}:{difficulty}",
                              CubeSettings(min_per_cell=1, pair_floor=8, tries=600, keep=2), masks=masks)
        if board is None:
            continue
        for _ in range(preset["fillings"]):
            sol = _solve(masks, board.rings, board.faces, rng)
            if sol is None:
                break
            answers, worst = _answers(masks, key_pos, sol)
            if best is None or worst < best[0]:
                best = (worst, board, sol, answers)
            if best[0] <= preset["max_ambiguity"]:
                break
        if best is not None and best[0] <= preset["max_ambiguity"]:
            break
    if best is None:
        return None
    _, board, sol, answers = best
    for line in range(N_LINES):
        assert board.line_rule(line) in answers[line]
    return ReverseCube(board=board, grid=tuple(sol), answers=tuple(answers))


def reverse_from_saved(df, rule_masks, seed: str, rings, faces, grid: Sequence[str]) -> Optional[ReverseCube]:
    ids = list(faces) + [r for ax in rings for r in ax]
    if any(r not in rule_masks for r in ids) or len(grid) != len(CELLS):
        return None
    scoreable = _scoreable(rule_masks, len(df), rule_masks.keys())
    masks = Masks.build(df, rule_masks, sorted(set(scoreable) | set(ids)))
    key_pos = {k: i for i, k in enumerate(masks.keys.tolist())}
    if any(k not in key_pos for k in grid):
        return None
    board = CubeBoard(seed=seed, rings=tuple(tuple(a) for a in rings), faces=tuple(faces), solution=tuple(grid))
    answers, _ = _answers(masks, key_pos, grid, keep=set(scoreable))
    for line in range(N_LINES):
        if board.line_rule(line) not in answers[line]:
            return None
    return ReverseCube(board=board, grid=tuple(grid), answers=tuple(answers))
