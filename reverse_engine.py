"""
reverse_engine.py — engine layer for Reverse Board (a.k.a. the deduction mode).

Normal PokeDokuDoku: the player sees the rules and fills in the Pokemon.
Reverse Board: the player sees the Pokemon and deduces the rules.

That inversion needs three things the base engine.py doesn't provide:

  1. A concrete assignment — WHICH Pokemon fills each cell. engine.generate_board
     proves a distinct solution EXISTS (via Kuhn matching) but throws the actual
     matching away and returns only the rule ids. Reverse Board has to show a
     real filled grid, so we recover an assignment here.

  2. Line satisfaction — for a filled row/column, the full set of rules that
     EVERY Pokemon in that line satisfies. This is the answer key: a guess is
     correct if the guessed rule is in this set. A line built as "Fire" may
     also be entirely "Kanto" by coincidence, and naming Kanto is a fair win.

  3. Ambiguity control — because of (2), a naively generated board can have a
     row satisfiable by a dozen rules, so the player names the easiest
     coincidental one and never engages with the puzzle. We generate several
     candidate boards and keep the one whose lines are LEAST ambiguous, capped
     by a threshold that the difficulty setting controls.

This module imports engine.py and reuses its rule masks, matching solver, and
board generator. It adds nothing to engine.py's own surface.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

import numpy as np
import pandas as pd

import engine


# ── Assignment recovery ───────────────────────────────────────────────────────

def _assign_distinct(
    df: pd.DataFrame,
    rule_masks: Dict[str, pd.Series],
    row_rule_ids: List[str],
    col_rule_ids: List[str],
    seed: str,
) -> Optional[List[List[str]]]:
    """Recover an all-distinct assignment of Pokemon keys to every cell.

    engine.has_distinct_solution proves one exists; this reconstructs it. Same
    Kuhn augmenting-path idea, but we keep the matching and read it back as a
    grid[row][col] = pokemon_key.

    Cells are ordered most-constrained-first (fewest candidates), which both
    matches faster and tends to place rare Pokemon deterministically. A per-seed
    shuffle of each cell's candidate list keeps different seeds producing
    different fillings for the same rule pair.
    """
    import random

    rng = random.Random(f"{seed}:assign")
    n_rows, n_cols = len(row_rule_ids), len(col_rule_ids)
    keys = df["key"].astype(str).to_numpy()

    row_np = {rr: rule_masks[rr].fillna(False).to_numpy(dtype=bool, copy=False) for rr in set(row_rule_ids)}
    col_np = {cc: rule_masks[cc].fillna(False).to_numpy(dtype=bool, copy=False) for cc in set(col_rule_ids)}

    # Left nodes = cells; right nodes = Pokemon indices. Build each cell's
    # candidate list, shuffled per-seed so different seeds fill differently.
    n_cells = n_rows * n_cols
    adj: List[List[int]] = []
    cell_rc: List[Tuple[int, int]] = []
    for i, rr in enumerate(row_rule_ids):
        rm = row_np[rr]
        for j, cc in enumerate(col_rule_ids):
            idxs = np.flatnonzero(rm & col_np[cc]).tolist()
            rng.shuffle(idxs)
            adj.append(idxs)
            cell_rc.append((i, j))

    # Match the most-constrained cells first (fewest candidates). This is the
    # heuristic that makes Kuhn fast here: hard cells rarely need to displace.
    order = sorted(range(n_cells), key=lambda c: len(adj[c]))

    # match_r[pokemon_index] = cell node currently holding it, or -1.
    n_pokemon = len(keys)
    match_r = [-1] * n_pokemon
    match_l = [-1] * n_cells
    seen_tag = [0] * n_pokemon
    tag = 0

    def aug(cell: int) -> bool:
        nonlocal tag
        stack = [(cell, 0)]
        # Iterative DFS augmenting path to avoid Python recursion limits on 81+ cells.
        path: List[Tuple[int, int]] = []
        while stack:
            c, ptr = stack[-1]
            cands = adj[c]
            found = False
            while ptr < len(cands):
                pk = cands[ptr]
                ptr += 1
                if seen_tag[pk] == tag:
                    continue
                seen_tag[pk] = tag
                stack[-1] = (c, ptr)
                if match_r[pk] == -1:
                    # Free Pokemon: unwind the alternating path, flipping matches.
                    match_r[pk] = c
                    match_l[c] = pk
                    for pc, ppk in reversed(path):
                        match_r[ppk] = pc
                        match_l[pc] = ppk
                    return True
                else:
                    path.append((c, pk))
                    stack.append((match_r[pk], 0))
                    found = True
                    break
            if not found:
                stack.pop()
                if path:
                    path.pop()
        return False

    for cell in order:
        tag += 1
        if not aug(cell):
            return None

    grid = [["" for _ in range(n_cols)] for _ in range(n_rows)]
    for cell in range(n_cells):
        pk = match_l[cell]
        if pk == -1:
            return None
        i, j = cell_rc[cell]
        grid[i][j] = str(keys[pk])

    flat = [grid[i][j] for i in range(n_rows) for j in range(n_cols)]
    if any(not x for x in flat) or len(set(flat)) != len(flat):
        return None
    return grid


# ── Line satisfaction (the answer key) ────────────────────────────────────────

# Rules that should never appear in an answer set, even when every Pokemon in a
# line satisfies them, because the player can't meaningfully pick them:
#
#   - "not_legendary" is suppressed in the UI (compact_rule_label maps it to "")
#     and is true of ~93% of the dex, so it's pure noise as an answer.
#   - Very broad rules (a large fraction of the whole answer pool) turn every
#     line into a trivial win: on a 9-Pokemon line, a rule covering 98% of the
#     dex is almost always satisfied by coincidence. The generator already keeps
#     these off Daily/Shuffle boards; we keep them out of Reverse answer keys
#     for the same reason.
#
# The broad threshold is a FRACTION of the playable pool, matched to the
# generator's own 0.80 non-type / 0.35 move cutoffs in engine.generate_board.
SUPPRESSED_RULE_IDS: Set[str] = {"not_legendary"}
BROAD_OTHER_FRACTION = 0.80
BROAD_MOVE_FRACTION = 0.35


def _too_broad(rid: str, support: int, pool_size: int) -> bool:
    if pool_size <= 0:
        return False
    frac = support / pool_size
    if str(rid).startswith("move_"):
        return frac > BROAD_MOVE_FRACTION
    if str(rid).startswith("type_"):
        return False  # types are core; never too broad to be an answer
    return frac > BROAD_OTHER_FRACTION


def rules_satisfied_by_keys(
    df: pd.DataFrame,
    rule_masks: Dict[str, pd.Series],
    keys: List[str],
    candidate_rule_ids: Optional[Set[str]] = None,
    pool_size: Optional[int] = None,
) -> Set[str]:
    """Every rule id that ALL of `keys` satisfy AND that a player could
    reasonably be expected to name.

    Restricting to candidate_rule_ids (the pool the board was built from) keeps
    the answer key aligned with what the player can pick. Suppressed and overly
    broad rules are dropped so a line's acceptable answers are the ones actually
    worth deducing, not coincidental filler like "Not Legendary" or "Protect".
    """
    if not keys:
        return set()
    key_set = set(keys)
    row_mask = df["key"].astype(str).isin(key_set)
    n = int(row_mask.sum())
    if n == 0:
        return set()
    if pool_size is None:
        pool_size = len(df)

    out: Set[str] = set()
    rids = candidate_rule_ids if candidate_rule_ids is not None else set(rule_masks.keys())
    for rid in rids:
        if rid in SUPPRESSED_RULE_IDS:
            continue
        m = rule_masks.get(rid)
        if m is None:
            continue
        support = int(m.fillna(False).sum())
        if _too_broad(rid, support, pool_size):
            continue
        # All keys satisfy rid  <=>  count of (in-line AND rule) == line size.
        if int((row_mask & m.fillna(False)).sum()) == n:
            out.add(rid)
    return out


# ── The Reverse Board object ──────────────────────────────────────────────────

@dataclass(frozen=True)
class ReverseBoard:
    n_rows: int
    n_cols: int
    seed: str
    row_rule_ids: List[str]          # the INTENDED answer per row
    col_rule_ids: List[str]          # the INTENDED answer per col
    grid: List[List[str]]            # grid[i][j] = pokemon_key, shown to player
    row_answer_sets: List[List[str]] # every rule id each row satisfies (answer key)
    col_answer_sets: List[List[str]] # every rule id each col satisfies (answer key)

    def ambiguity(self) -> int:
        """Worst-case number of acceptable answers on any single line. 1 means
        every line has exactly one right answer; higher is more forgiving but
        less pointed."""
        return max(
            max((len(s) for s in self.row_answer_sets), default=0),
            max((len(s) for s in self.col_answer_sets), default=0),
        )


def _build_reverse_from_board(
    df: pd.DataFrame,
    rule_masks: Dict[str, pd.Series],
    board: engine.Board,
    seed: str,
    candidate_rule_ids: Optional[Set[str]],
) -> Optional[ReverseBoard]:
    grid = _assign_distinct(df, rule_masks, board.row_rule_ids, board.col_rule_ids, seed)
    if grid is None:
        return None

    pool_size = len(df)
    row_sets: List[List[str]] = []
    for i in range(board.n_rows):
        keys = [grid[i][j] for j in range(board.n_cols)]
        s = rules_satisfied_by_keys(df, rule_masks, keys, candidate_rule_ids, pool_size)
        s.add(board.row_rule_ids[i])  # intended answer is always accepted
        row_sets.append(sorted(s))

    col_sets: List[List[str]] = []
    for j in range(board.n_cols):
        keys = [grid[i][j] for i in range(board.n_rows)]
        s = rules_satisfied_by_keys(df, rule_masks, keys, candidate_rule_ids, pool_size)
        s.add(board.col_rule_ids[j])
        col_sets.append(sorted(s))

    return ReverseBoard(
        n_rows=board.n_rows,
        n_cols=board.n_cols,
        seed=seed,
        row_rule_ids=list(board.row_rule_ids),
        col_rule_ids=list(board.col_rule_ids),
        grid=grid,
        row_answer_sets=row_sets,
        col_answer_sets=col_sets,
    )


def generate_reverse_board(
    df: pd.DataFrame,
    rules: Dict[str, engine.Rule],
    rule_masks: Dict[str, pd.Series],
    seed: str,
    n_rows: int = 9,
    n_cols: Optional[int] = None,
    allowed_rule_ids: Optional[Set[str]] = None,
    max_ambiguity: int = 3,
    candidate_boards: int = 12,
    constraints: Optional[engine.BoardConstraints] = None,
    allow_shrink: bool = False,
) -> Optional[ReverseBoard]:
    """Generate a filled board whose lines are minimally ambiguous.

    Strategy: ask engine.generate_board for several candidate boards (varying
    the seed), fill each, score each by its worst-line ambiguity, and return the
    least ambiguous one that clears max_ambiguity. If none clears the bar, return
    the best we found anyway rather than failing — a slightly ambiguous board is
    better than no board, and the caller can surface the achieved ambiguity.

    max_ambiguity is the difficulty knob:
      - low  (1-2): lines have almost exactly one right answer, needs a rich pool
      - mid  (3-4): a little slack, comfortable default
      - high (6+):  loose; easy presets with a small pool live here

    candidate_boards trades generation time for board quality.
    """
    if n_cols is None:
        n_cols = n_rows
    # A suppressed rule (e.g. "Not Legendary": no visible label, so it's not in
    # the guess picker) must never be a line's intended answer, or that line
    # can't be named at all. Answer sets already skip these; this keeps the
    # forward-board generator from choosing one as a row/column rule.
    allowed_rule_ids = (set(allowed_rule_ids) if allowed_rule_ids is not None
                        else set(rule_masks.keys())) - SUPPRESSED_RULE_IDS
    if constraints is None:
        # Bounded per-candidate try budget. We generate several candidate boards
        # and keep the least ambiguous, so each individual generate_board call
        # must be cheap even when it FAILS — a small restricted pool (e.g. types
        # + regions only) can otherwise burn a 5000-try budget hunting for a 9x9.
        # This mirrors Draft Board's own 300-try cap for the same reason.
        constraints = engine.BoardConstraints(
            min_candidates_per_cell=1,
            max_candidates_per_cell=None,
            max_tries=400,
            require_distinct_solution=True,
        )

    # Precompute the scoring matrix once (independent of board size), then try
    # the requested size, optionally shrinking if a restricted pool can't fill it.
    # --- scoring setup below is size-independent ---

    # Precompute a numpy matrix of the candidate rules once, so scoring a board's
    # ambiguity is a handful of vector ops rather than a 145-rule Python loop per
    # line per board. This is what keeps generation fast: we score cheaply, then
    # pay for the full answer key exactly once, on the winner.
    pool_ids = sorted(allowed_rule_ids) if allowed_rule_ids is not None else sorted(rule_masks.keys())
    pool_size = len(df)
    scoreable: List[str] = []
    mask_rows: List[np.ndarray] = []
    for rid in pool_ids:
        if rid in SUPPRESSED_RULE_IDS:
            continue
        m = rule_masks.get(rid)
        if m is None:
            continue
        support = int(m.fillna(False).sum())
        if _too_broad(rid, support, pool_size):
            continue
        scoreable.append(rid)
        mask_rows.append(m.fillna(False).to_numpy(dtype=bool, copy=False))
    mask_mat = np.vstack(mask_rows) if mask_rows else np.zeros((0, pool_size), dtype=bool)
    key_to_pos = {k: i for i, k in enumerate(df["key"].astype(str).to_numpy())}

    def score_ambiguity(grid: List[List[str]], nr: int, nc: int) -> int:
        """Worst-line count of scoreable rules the whole line satisfies. Uses
        the precomputed matrix: for a line, a rule is satisfied by all iff its
        column-sum over the line's Pokemon equals the line length."""
        worst = 0
        # rows
        for i in range(nr):
            cols = [key_to_pos[grid[i][j]] for j in range(nc)]
            sub = mask_mat[:, cols]  # (rules, line)
            cnt = int((sub.sum(axis=1) == nc).sum())
            worst = max(worst, cnt)
        for j in range(nc):
            rowsidx = [key_to_pos[grid[i][j]] for i in range(nr)]
            sub = mask_mat[:, rowsidx]
            cnt = int((sub.sum(axis=1) == nr).sum())
            worst = max(worst, cnt)
        return worst

    def attempt(nr: int, nc: int):
        best_board = best_grid = None
        best_sub = seed
        best_amb = 10**9
        for k in range(max(1, candidate_boards)):
            sub_seed = f"{seed}:rev:{nr}x{nc}:{k}"
            board = engine.generate_board(
                df=df, rules=rules, rule_masks=rule_masks,
                seed=sub_seed, n_rows=nr, n_cols=nc,
                constraints=constraints, allowed_rule_ids=allowed_rule_ids,
            )
            if board is None:
                continue
            grid = _assign_distinct(df, rule_masks, board.row_rule_ids, board.col_rule_ids, sub_seed)
            if grid is None:
                continue
            amb = score_ambiguity(grid, board.n_rows, board.n_cols)
            if amb < best_amb:
                best_board, best_grid, best_sub, best_amb = board, grid, sub_seed, amb
                if amb <= 1:
                    break
            if best_amb <= max_ambiguity and k >= 2:
                break
        return best_board, best_grid, best_sub

    best_board, best_grid, best_seed = attempt(n_rows, n_cols)

    if best_board is None and allow_shrink and (n_rows > 3 or n_cols > 3):
        fb_r = max(3, n_rows - 3)
        fb_c = max(3, n_cols - 3)
        best_board, best_grid, best_seed = attempt(fb_r, fb_c)

    if best_board is None or best_grid is None:
        return None

    # Pay for the full answer key exactly once, on the chosen board.
    row_sets: List[List[str]] = []
    for i in range(best_board.n_rows):
        keys = [best_grid[i][j] for j in range(best_board.n_cols)]
        s = rules_satisfied_by_keys(df, rule_masks, keys, allowed_rule_ids, pool_size)
        s.add(best_board.row_rule_ids[i])
        row_sets.append(sorted(s))
    col_sets: List[List[str]] = []
    for j in range(best_board.n_cols):
        keys = [best_grid[i][j] for i in range(best_board.n_rows)]
        s = rules_satisfied_by_keys(df, rule_masks, keys, allowed_rule_ids, pool_size)
        s.add(best_board.col_rule_ids[j])
        col_sets.append(sorted(s))

    return ReverseBoard(
        n_rows=best_board.n_rows,
        n_cols=best_board.n_cols,
        seed=best_seed,
        row_rule_ids=list(best_board.row_rule_ids),
        col_rule_ids=list(best_board.col_rule_ids),
        grid=best_grid,
        row_answer_sets=row_sets,
        col_answer_sets=col_sets,
    )


# ── Guess checking ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class GuessResult:
    ok: bool
    reason: str          # "ok", "already_solved", "wrong", "unknown_axis"
    canonical_rule_id: str = ""   # the rule the guess resolved to, on success


def check_line_guess(
    board: ReverseBoard,
    axis: str,          # "row" or "col"
    index: int,
    guessed_rule_id: str,
) -> GuessResult:
    """A guess is correct if the guessed rule is one that the whole line
    satisfies (its answer set). Any acceptable answer counts, not only the
    intended one."""
    if axis == "row":
        if index < 0 or index >= board.n_rows:
            return GuessResult(False, "unknown_axis")
        answer_set = set(board.row_answer_sets[index])
    elif axis == "col":
        if index < 0 or index >= board.n_cols:
            return GuessResult(False, "unknown_axis")
        answer_set = set(board.col_answer_sets[index])
    else:
        return GuessResult(False, "unknown_axis")

    if guessed_rule_id in answer_set:
        return GuessResult(True, "ok", canonical_rule_id=guessed_rule_id)
    return GuessResult(False, "wrong")
