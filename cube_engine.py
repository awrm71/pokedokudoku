"""
cube_engine.py — the 3×3×3 Cube board.

The flat board crosses two sets of rules (rows × columns). The Cube adds a
third: three layers stacked on top of each other. Every one of the 27 squares
sits where one column rule, one row rule and one layer rule meet, and needs a
Pokémon that fits all three.

    axis 0  columns  "across"   x = 0..2
    axis 1  rows     "down"     y = 0..2
    axis 2  layers   "layer"    z = 0..2      cell = (z, y, x)

What a generated Cube guarantees (checked in tests/test_cube.py):

  1. Every square has at least `min_per_cell` valid Pokémon.
  2. All 27 squares can be filled at once with 27 different Pokémon
     (a perfect matching, recovered and kept so hints can use it).
  3. Every rule matters in every square: for each square, dropping any one
     of its three rules lets in more Pokémon. No square where, say, every
     Kanto Legendary happens to be a Final Form, so "Final Form" does nothing.
  4. No rule on one axis is contained in a rule on another (e.g. Baby on
     the rows and Base Stage on the layers), which would make it redundant
     everywhere it meets.

The Reverse Cube fills a generated Cube with 27 Pokémon and hides the nine
rules; each slice (9 Pokémon) is guessed. Like Reverse Board, a slice accepts
any reasonable rule that all nine share, and fillings are chosen so slices
have as few of those as possible.

Only numpy/pandas and engine.py's rules and masks. Nothing in engine.py changes.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

import numpy as np
import pandas as pd

import reverse_engine

AXES = ("col", "row", "layer")
SIZE = 3
CELLS = [(z, y, x) for z in range(SIZE) for y in range(SIZE) for x in range(SIZE)]


# ── Rule pool ─────────────────────────────────────────────────────────────────

# Never used on a Cube: filler or a duplicate of another rule.
EXCLUDED = {"not_legendary", "evo_premature"}   # evo_premature == "Baby Pokemon" (baby)
MIN_SUPPORT = 15                                 # three-way crossings need room

GROUP_WEIGHT = {
    "type": 1.35,
    "region": 1.30,
    "evolution": 1.15,
    "flag": 0.95,
    "special": 0.80,     # legendary, mythical, mega, gmax, starter, fossil, paradox, regional, baby
    "ability": 0.25,
    "move": 0.05,
}
# At most this many rules from a group on one axis (types are the backbone).
AXIS_CAP = {"move": 1, "ability": 1, "special": 2, "flag": 2, "evolution": 2}
# At most this many from a group on the whole Cube.
CUBE_CAP = {"move": 1, "ability": 1, "special": 3}

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


@dataclass
class Masks:
    """Rule masks as one bool matrix, for fast three-way counts."""
    ids: List[str]
    mat: np.ndarray                     # (n_rules, n_pokemon)
    keys: np.ndarray                    # pokemon keys, aligned with columns
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
    cols: Tuple[str, str, str]
    rows: Tuple[str, str, str]
    layers: Tuple[str, str, str]
    solution: Tuple[str, ...]           # 27 keys in CELLS order: one all-distinct filling

    @property
    def axes(self) -> Tuple[Tuple[str, ...], ...]:
        return (self.cols, self.rows, self.layers)

    def rules_at(self, z: int, y: int, x: int) -> Tuple[str, str, str]:
        return (self.cols[x], self.rows[y], self.layers[z])

    def solution_at(self, z: int, y: int, x: int) -> str:
        return self.solution[CELLS.index((z, y, x))]


def cell_mask(masks: Masks, board: CubeBoard, z: int, y: int, x: int) -> np.ndarray:
    a, b, c = board.rules_at(z, y, x)
    return masks.m(a) & masks.m(b) & masks.m(c)


def candidates(masks: Masks, board: CubeBoard, z: int, y: int, x: int) -> List[str]:
    return masks.keys[cell_mask(masks, board, z, y, x)].tolist()


# ── Matching (27 squares ↔ distinct Pokémon) ─────────────────────────────────

def match_cells(adj: List[List[int]]) -> Optional[List[int]]:
    """Kuhn's augmenting paths. adj[cell] = candidate pokemon indices, in the
    order to try them. Returns pokemon index per cell, or None."""
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

    # Hardest squares first: they rarely need to be displaced.
    for c in sorted(range(len(adj)), key=lambda i: len(adj[i])):
        if not try_cell(c, set()):
            return None
    return match_l


def _solve(masks: Masks, cols, rows, layers, rng: random.Random) -> Optional[List[str]]:
    adj = []
    for z, y, x in CELLS:
        idx = np.flatnonzero(masks.m(cols[x]) & masks.m(rows[y]) & masks.m(layers[z])).tolist()
        rng.shuffle(idx)
        adj.append(idx)
    got = match_cells(adj)
    if got is None:
        return None
    return [str(masks.keys[p]) for p in got]


# ── Quality checks ────────────────────────────────────────────────────────────

def redundant_pair(masks: Masks, a: str, b: str) -> bool:
    """True if one rule's Pokémon are all inside the other's."""
    ma, mb = masks.m(a), masks.m(b)
    both = int((ma & mb).sum())
    return both == int(ma.sum()) or both == int(mb.sum())


def cell_report(masks: Masks, cols, rows, layers, z: int, y: int, x: int) -> Tuple[int, bool]:
    """(candidates in the square, every one of its three rules matters)."""
    a, b, c = masks.m(cols[x]), masks.m(rows[y]), masks.m(layers[z])
    ab, ac, bc = a & b, a & c, b & c
    n = int((ab & c).sum())
    matters = n < int(ab.sum()) and n < int(ac.sum()) and n < int(bc.sum())
    return n, matters


def check_board(masks: Masks, cols, rows, layers, min_per_cell: int = 1) -> Tuple[bool, List[int]]:
    """(ok, candidate counts per cell). ok covers checks 1, 3 and 4 above."""
    ids = list(cols) + list(rows) + list(layers)
    if len(set(ids)) != 9:
        return False, []
    for ax1, ax2 in ((cols, rows), (cols, layers), (rows, layers)):
        for a in ax1:
            for b in ax2:
                if redundant_pair(masks, a, b):
                    return False, []
    counts = []
    for z, y, x in CELLS:
        n, matters = cell_report(masks, cols, rows, layers, z, y, x)
        if n < min_per_cell or not matters:
            return False, counts
        counts.append(n)
    return True, counts


# ── Generation ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CubeSettings:
    min_per_cell: int = 2           # Daily: every square has a choice
    pair_floor: int = 10            # two-rule crossings needed before a third is tried
    tries: int = 1500
    keep: int = 6                   # valid boards to score before picking one


# Each axis is one family, so a player can read the Cube at a glance:
# "columns are types, rows are regions, layers are traits". The template
# changes day to day; which family lands on which axis is shuffled too.
FAMILY = {"type": "type", "region": "region"}            # everything else is "trait"
# (families, how often a day gets it). The first is the fallback: it always
# generates quickly. The others are rarer to build, so they get a budget of
# tries and hand over to the fallback if they run out.
TEMPLATES = [
    (("type", "region", "trait"), 5.0),
    (("region", "trait", "trait"), 2.0),
    (("type", "trait", "trait"), 1.0),
]
ALT_TEMPLATE_TRIES = 350
# Broad traits that match a third or more of all Pokémon. Three at most (one full axis): a
# Cube of "Final Form × Single Type × Evolved by Level Up" says very little.
BROAD = {"evo_final", "evolves_by_levelup", "evo_start", "single_type", "dual_type", "evo_none"}
BROAD_CAP = 3


def family(rid: str) -> str:
    return FAMILY.get(rule_group(rid), "trait")


def _weight(rid: str, support: int) -> float:
    g = rule_group(rid)
    w = GROUP_WEIGHT.get(g, 1.0)
    if g in ("region", "ability"):
        return w * 10.0                         # flat: every region equally likely
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


def _caps_ok(rid: str, axis: List[str], chosen: List[str]) -> bool:
    g = rule_group(rid)
    if g in AXIS_CAP and sum(rule_group(r) == g for r in axis) >= AXIS_CAP[g]:
        return False
    if g in CUBE_CAP and sum(rule_group(r) == g for r in chosen) >= CUBE_CAP[g]:
        return False
    if rid in BROAD and sum(r in BROAD for r in chosen) >= BROAD_CAP:
        return False
    return True


def _score(cols, rows, layers, counts: List[int]) -> float:
    """Prefer boards where squares have some room but aren't wide open, and
    where the trait rules come from varied groups."""
    groups = {rule_group(r) for r in list(cols) + list(rows) + list(layers)}
    lo = min(counts)
    tight = sum(1 for c in counts if c <= 2)
    wide = sum(1 for c in counts if c >= 25)
    return min(lo, 4) * 1.0 + len(groups) * 1.2 - tight * 0.3 - wide * 0.15


def _template_order(fams: Tuple[str, str, str]) -> List[int]:
    """Fill narrow families first (types, then regions), traits last: the
    last axis is the one checked against all nine crossings."""
    rank = {"type": 0, "region": 1, "trait": 2}
    return sorted(range(3), key=lambda i: rank[fams[i]])


def generate_cube(
    df: pd.DataFrame,
    rule_masks: Dict[str, pd.Series],
    seed: str,
    settings: CubeSettings = CubeSettings(),
    allowed_rule_ids: Optional[Set[str]] = None,
    masks: Optional[Masks] = None,
) -> Optional[CubeBoard]:
    """Deterministic for a given seed + dataset."""
    rng = random.Random(f"cube:{seed}")
    pool = cube_pool(rule_masks, len(df), allowed_rule_ids)
    if len(pool) < 9:
        return None
    if masks is None:
        masks = Masks.build(df, rule_masks, pool)
    support = {r: int(masks.m(r).sum()) for r in pool}
    base_w = {r: _weight(r, support[r]) for r in pool}
    by_family = {f: [r for r in pool if family(r) == f] for f in ("type", "region", "trait")}
    templates = [t for t, _ in TEMPLATES if all(len(by_family[f]) >= 3 * t.count(f) for f in set(t))]
    t_weights = [w for t, w in TEMPLATES if t in templates]
    if not templates:
        # A restricted pool that can't fill a family axis: mix freely.
        templates, t_weights = [("any", "any", "any")], [1.0]
        by_family["any"] = pool
    fam_mat = {f: (np.vstack([masks.m(r) for r in ids]).astype(np.float32) if ids else None)
               for f, ids in by_family.items()}

    found: List[Tuple[float, tuple, List[int]]] = []
    day_template = rng.choices(templates, weights=t_weights)[0]
    for attempt in range(settings.tries):
        fams = day_template
        if fams != templates[0] and attempt >= ALT_TEMPLATE_TRIES and not found:
            fams = day_template = templates[0]
        order = _template_order(fams) if fams[0] != "any" else [0, 1, 2]
        chosen: List[str] = []
        axes: List[List[str]] = [[], [], []]
        ok = True
        for step, ai in enumerate(order):
            done = [axes[j] for j in order[:step]]
            if step == 2:
                cross = [masks.m(a) & masks.m(b) for a in done[0] for b in done[1]]
                floor = settings.min_per_cell
            elif step == 1:
                cross = [masks.m(a) for a in done[0]]
                floor = settings.pair_floor
            else:
                cross, floor = [], 0
            fam_ids = by_family[fams[ai]]
            if cross:
                # One matrix product gives every candidate's count against
                # every crossing: (rules × pokemon) · (pokemon × crossings).
                cm = np.vstack(cross).astype(np.float32)
                lows = (fam_mat[fams[ai]] @ cm.T).min(axis=1)
            else:
                lows = None
            while len(axes[ai]) < 3:
                c, w = [], []
                for k, r in enumerate(fam_ids):
                    if r in chosen or not _caps_ok(r, axes[ai], chosen):
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
                    ok = False
                    break
                axes[ai].append(p); chosen.append(p)
            if not ok:
                break
        if not ok:
            continue

        # Which family lands on which axis shouldn't be predictable.
        rng.shuffle(axes)
        for a in axes:
            rng.shuffle(a)
        cols, rows, layers = (tuple(a) for a in axes)
        good, counts = check_board(masks, cols, rows, layers, settings.min_per_cell)
        if not good:
            continue
        found.append((_score(cols, rows, layers, counts), (cols, rows, layers), counts))
        if len(found) >= settings.keep:
            break

    # Best scored first; ties keep generation order (deterministic).
    for _, (cols, rows, layers), _ in sorted(found, key=lambda t: -t[0]):
        sol = _solve(masks, cols, rows, layers, rng)
        if sol is not None:
            return CubeBoard(seed=seed, cols=cols, rows=rows, layers=layers, solution=tuple(sol))
    return None


def board_from_ids(df, rule_masks, seed: str, cols, rows, layers,
                   masks: Optional[Masks] = None) -> Optional[CubeBoard]:
    """Rebuild a saved (pinned) board. None if it no longer solves."""
    ids = list(cols) + list(rows) + list(layers)
    if any(r not in rule_masks for r in ids):
        return None
    if masks is None:
        masks = Masks.build(df, rule_masks, ids)
    sol = _solve(masks, tuple(cols), tuple(rows), tuple(layers), random.Random(f"cube:{seed}:pin"))
    if sol is None:
        return None
    return CubeBoard(seed=seed, cols=tuple(cols), rows=tuple(rows), layers=tuple(layers), solution=tuple(sol))


def validate(masks: Masks, board: CubeBoard, z: int, y: int, x: int,
             pokemon_key: str, used: Set[str]) -> str:
    """'ok', 'duplicate', 'does_not_match' or 'unknown_pokemon'."""
    if not pokemon_key:
        return "unknown_pokemon"
    if pokemon_key in used:
        return "duplicate"
    hit = np.flatnonzero(masks.keys == pokemon_key)
    if hit.size == 0:
        return "unknown_pokemon"
    return "ok" if bool(cell_mask(masks, board, z, y, x)[hit[0]]) else "does_not_match"


def hint_for(masks: Masks, board: CubeBoard, z: int, y: int, x: int,
             used: Set[str], filled: Dict[Tuple[int, int, int], str]) -> Optional[str]:
    """A Pokémon for this square that still leaves every empty square fillable.

    Prefers the stored solution's Pokémon; otherwise re-solves the empty
    squares around what's already placed, so a hint never strands the board."""
    empty = [c for c in CELLS if c not in filled and c != (z, y, x)]
    pref = board.solution_at(z, y, x)
    cands = candidates(masks, board, z, y, x)
    order = ([pref] if pref in cands else []) + [k for k in cands if k != pref]
    for k in order:
        if k in used:
            continue
        taken = used | {k}
        adj = []
        for c in empty:
            idx = [i for i in np.flatnonzero(cell_mask(masks, board, *c)).tolist() if masks.keys[i] not in taken]
            adj.append(idx)
        if match_cells(adj) is not None:
            return k
    # Board already stranded by the player's own picks: any unused fit will do.
    for k in order:
        if k not in used:
            return k
    return None


def still_solvable(masks: Masks, board: CubeBoard, filled: Dict[Tuple[int, int, int], str]) -> bool:
    used = set(filled.values())
    adj = []
    for c in CELLS:
        if c in filled:
            continue
        adj.append([i for i in np.flatnonzero(cell_mask(masks, board, *c)).tolist() if masks.keys[i] not in used])
    return match_cells(adj) is not None


# ── Reverse Cube ──────────────────────────────────────────────────────────────

def slice_cells(axis: int, index: int) -> List[Tuple[int, int, int]]:
    """The nine squares of one slice. axis 0 = a column (x fixed), 1 = a row
    (y fixed), 2 = a layer (z fixed)."""
    if axis == 0:
        return [c for c in CELLS if c[2] == index]
    if axis == 1:
        return [c for c in CELLS if c[1] == index]
    return [c for c in CELLS if c[0] == index]


@dataclass(frozen=True)
class ReverseCube:
    board: CubeBoard
    grid: Tuple[str, ...]                       # 27 keys, CELLS order (what the player sees)
    answers: Tuple[Tuple[Tuple[str, ...], ...], ...]   # answers[axis][index] = accepted rule ids

    def key_at(self, z, y, x) -> str:
        return self.grid[CELLS.index((z, y, x))]

    def ambiguity(self) -> int:
        return max(len(s) for ax in self.answers for s in ax)


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


def generate_reverse_cube(
    df: pd.DataFrame,
    rule_masks: Dict[str, pd.Series],
    seed: str,
    difficulty: str = "medium",
    boards: int = 5,
) -> Optional[ReverseCube]:
    """A filled Cube whose nine slices are as pinned-down as possible."""
    preset = REVERSE_PRESETS.get(difficulty, REVERSE_PRESETS["medium"])
    scoreable = _scoreable(rule_masks, len(df), rule_masks.keys())
    masks = Masks.build(df, rule_masks, scoreable)
    key_pos = {k: i for i, k in enumerate(masks.keys.tolist())}
    rng = random.Random(f"rcube:{seed}:{difficulty}")

    best: Optional[Tuple[int, CubeBoard, List[str], list]] = None
    for b in range(boards):
        board = generate_cube(df, rule_masks, f"{seed}:r{b}:{difficulty}",
                              CubeSettings(min_per_cell=1, pair_floor=4, tries=800, keep=3), masks=masks)
        if board is None:
            continue
        for _ in range(preset["fillings"]):
            sol = _solve(masks, board.cols, board.rows, board.layers, rng)
            if sol is None:
                break
            answers = []
            worst = 0
            for axis in range(3):
                ax = []
                for i in range(3):
                    idx = [key_pos[sol[CELLS.index(c)]] for c in slice_cells(axis, i)]
                    sat = masks.mat[:, idx].all(axis=1)
                    acc = [masks.ids[j] for j in np.flatnonzero(sat)]
                    worst = max(worst, len(acc))
                    ax.append(tuple(sorted(acc)))
                answers.append(tuple(ax))
            if best is None or worst < best[0]:
                best = (worst, board, sol, answers)
            if worst <= preset["max_ambiguity"] and best[0] <= preset["max_ambiguity"]:
                break
        if best is not None and best[0] <= preset["max_ambiguity"]:
            break
    if best is None:
        return None
    _, board, sol, answers = best
    # The intended rule is always an accepted answer (it holds by construction).
    for axis in range(3):
        for i in range(3):
            assert board.axes[axis][i] in answers[axis][i]
    return ReverseCube(board=board, grid=tuple(sol), answers=tuple(answers))


def reverse_from_saved(df, rule_masks, seed: str, cols, rows, layers, grid: Sequence[str]) -> Optional[ReverseCube]:
    """Rebuild a pinned Reverse Cube. None if the saved filling no longer fits."""
    ids = list(cols) + list(rows) + list(layers)
    if any(r not in rule_masks for r in ids) or len(grid) != 27:
        return None
    scoreable = _scoreable(rule_masks, len(df), rule_masks.keys())
    masks = Masks.build(df, rule_masks, sorted(set(scoreable) | set(ids)))
    key_pos = {k: i for i, k in enumerate(masks.keys.tolist())}
    if any(k not in key_pos for k in grid):
        return None
    board = CubeBoard(seed=seed, cols=tuple(cols), rows=tuple(rows), layers=tuple(layers), solution=tuple(grid))
    sc = set(scoreable)
    answers = []
    for axis in range(3):
        ax = []
        for i in range(3):
            idx = [key_pos[grid[CELLS.index(c)]] for c in slice_cells(axis, i)]
            sat = masks.mat[:, idx].all(axis=1)
            acc = sorted(masks.ids[j] for j in np.flatnonzero(sat) if masks.ids[j] in sc)
            if board.axes[axis][i] not in acc:
                return None
            ax.append(tuple(acc))
        answers.append(tuple(ax))
    return ReverseCube(board=board, grid=tuple(grid), answers=tuple(answers))
