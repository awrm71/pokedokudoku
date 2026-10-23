"""
reverse_api.py — Reverse Board endpoints, mounted onto the main FastAPI app.

api_main.py includes this with two lines (see the handoff notes at the bottom
of this file):

    import reverse_api
    reverse_api.attach(app, ctx)

`ctx` hands over the already-built dataframe, rules, masks, the board executor,
and the compact_rule_label / is_daily_seed helpers, so nothing is rebuilt or
duplicated. Everything Reverse-Board-specific lives here.

Endpoints:
  POST /board/reverse            generate a filled board with hidden labels
  POST /reverse/validate         check one row/col guess, log on success
  POST /reverse/hint             spend the board's hint: reveal one line
  GET  /reverse/stats            daily only: how everyone named each line
  GET  /reverse/index            the caller's Category Index progress
  GET  /reverse/presets          difficulty preset definitions for the UI

The answer key (which rules each line accepts) NEVER leaves the server. The
client only gets the filled grid and the board id; guesses are checked here.
The only exceptions are deliberate: the hint reveals one line, and running
out of lives reveals the lines that were left.

Lives and hints (same idea as the forward board, scaled to 18 lines):
  MAX_LIVES wrong guesses end the board; MAX_HINTS hint(s) per board.
  Daily: kept in the database per player and day (reverse_store), so a
  reload can't reset them. Custom: kept with the board in memory.
  A hinted line is solved but does NOT register to the Category Index or the
  Pokedex, since the player didn't deduce it.
"""

from __future__ import annotations

import asyncio
import uuid
import threading
from typing import Dict, List, Optional, Set

import pandas as pd
from fastapi import HTTPException, Header
from pydantic import BaseModel

import engine
import reverse_engine
import reverse_store
import pokedex_store
import ads_api


# ── Difficulty presets ────────────────────────────────────────────────────────
# KEY FINDING from engine stress-testing: a small category pool cannot tile a
# 9x9 board (types+regions alone can't even make 5x5). So difficulty is NOT
# "fewer category types" — it's the ambiguity cap, i.e. how close each line is to
# having exactly one correct answer. All presets keep the full pool so 9x9
# generates in ~1-3s.
#
#   easy   loose cap: lines may have several valid answers, so an easy
#          observation ("these are all Water") usually wins.
#   medium moderate.
#   hard   tight cap: lines are near-unique, so you must find the specific rule.
PRESETS: Dict[str, dict] = {
    "easy":   {"max_ambiguity": 6, "candidate_boards": 8,  "label": "Easy"},
    "medium": {"max_ambiguity": 4, "candidate_boards": 10, "label": "Medium"},
    "hard":   {"max_ambiguity": 2, "candidate_boards": 16, "label": "Hard"},
}
DEFAULT_PRESET = "medium"

_REVERSE_TIMEOUT_SECONDS = 30.0
_STORE_MAX = 40

MAX_LIVES = 3
MAX_HINTS = 1


class _Ctx:
    """Everything reverse_api borrows from api_main, passed in at attach time."""
    def __init__(self, df_play, rules, rule_masks, executor, compact_rule_label,
                 is_daily_seed, today_seed, pokedex_store_mod=pokedex_store):
        self.df_play = df_play
        self.rules = rules
        self.rule_masks = rule_masks
        self.executor = executor
        self.compact_rule_label = compact_rule_label
        self.is_daily_seed = is_daily_seed
        self.today_seed = today_seed          # callable -> today's canonical daily seed string
        self.pokedex_store = pokedex_store_mod


# Server-side store of generated reverse boards, keyed by an opaque board id.
# Holds the answer key so /reverse/validate can check guesses without the client
# ever seeing acceptable answers. LRU-ish: oldest evicted past a cap.
_boards: Dict[str, reverse_engine.ReverseBoard] = {}
_boards_meta: Dict[str, dict] = {}   # board_id -> {"is_qualifying": bool, "day_seed": str|None}
_boards_lock = threading.Lock()

# The daily reverse board is generated once per day and reused, so every client
# gets the same board and it can register to the Category Index + Pokedex.
# Keyed by the date-seed string. Value: (board_id, ReverseBoard).
_daily_cache: Dict[str, tuple] = {}
_daily_lock = threading.Lock()


def _remember(board_id: str, rb: reverse_engine.ReverseBoard, is_qualifying: bool, day_seed: Optional[str] = None) -> None:
    with _boards_lock:
        if len(_boards) >= _STORE_MAX:
            oldest = next(iter(_boards))
            _boards.pop(oldest, None)
            _boards_meta.pop(oldest, None)
        _boards[board_id] = rb
        old = _boards_meta.get(board_id) or {}
        _boards_meta[board_id] = {"is_qualifying": is_qualifying, "day_seed": day_seed,
                                  # Custom boards keep lives/hint state here;
                                  # re-remembering a board must not reset it.
                                  "play": old.get("play") or {"solved": {}, "misses": set(), "hint": None}}


# ── Request/response models ───────────────────────────────────────────────────

class ReverseBoardRequest(BaseModel):
    difficulty: str = DEFAULT_PRESET          # "easy" | "medium" | "hard"
    n_rows: int = 9
    n_cols: int = 9
    allowed_rule_ids: Optional[List[str]] = None   # Advanced mode: restrict the pool
    max_ambiguity: Optional[int] = None            # Advanced mode: override the cap


class ReverseCell(BaseModel):
    key: str
    display_name: str
    pokemon_id: Optional[int] = None


class ReverseBoardResponse(BaseModel):
    board_id: str
    n_rows: int
    n_cols: int
    difficulty: str
    ambiguity: int                 # worst-line acceptable-answer count actually achieved
    is_qualifying: bool            # True only for the daily board: counts toward the Category Index
    grid: List[List[ReverseCell]]  # grid[i][j] — the Pokemon shown to the player
    note: Optional[str] = None
    # Daily-only fields (None/empty for custom boards):
    day_seed: Optional[str] = None
    solved_lines: List[dict] = []      # [{axis, index, label, rule_id, hinted}] to restore a locked daily board
    total_lines: Optional[int] = None  # n_rows + n_cols
    # Lives and hints (restored for the daily board, fresh for custom):
    max_lives: int = MAX_LIVES
    max_hints: int = MAX_HINTS
    lives_left: int = MAX_LIVES
    hints_left: int = MAX_HINTS
    missed: List[dict] = []            # [{axis, index, rule_id}] wrong guesses so far
    game_over: bool = False            # out of lives: the board is locked
    reveal: List[dict] = []            # only when game_over: the lines left unsolved
    revive: bool = False               # daily: out of lives, but an ad can buy one more
    # NOTE: no rule ids or labels for UNSOLVED lines — that's the whole puzzle.


class ReverseValidateRequest(BaseModel):
    board_id: str
    axis: str          # "row" | "col"
    index: int
    guessed_rule_id: str


class ReverseValidateResponse(BaseModel):
    ok: bool
    # "ok" | "wrong" | "repeat" (same wrong guess again, no life lost) |
    # "already_used" (category named on another line, no life lost) |
    # "already_solved" | "out_of_lives" | "unknown_axis" | "unknown_board"
    reason: str
    used_on: Optional[str] = None     # "row:2" when reason == "already_used"
    canonical_rule_id: str = ""
    canonical_label: str = ""
    newly_indexed: bool = False       # advanced the Category Index (first-ever + qualifying board)
    lives_left: int = MAX_LIVES
    hints_left: int = MAX_HINTS
    game_over: bool = False
    reveal: List[dict] = []           # filled only when this guess ended the board
    revive: bool = False              # daily: out of lives, but an ad can buy one more
    pct: Optional[float] = None       # daily only: share of players who named this line the same way


class ReverseReviveResponse(BaseModel):
    lives_left: int
    game_over: bool
    revive: bool = False
    reveal: List[dict] = []


class ReverseHintRequest(BaseModel):
    board_id: str
    axis: str          # "row" | "col"
    index: int


class ReverseHintResponse(BaseModel):
    ok: bool
    # "ok" | "no_hints" | "already_solved" | "out_of_lives" | "unknown_axis" | "unknown_board"
    reason: str
    rule_id: str = ""
    label: str = ""
    lives_left: int = MAX_LIVES
    hints_left: int = MAX_HINTS


class LineAnswer(BaseModel):
    rule_id: str
    label: str
    count: int
    pct: float


class LineStats(BaseModel):
    axis: str
    index: int
    open: bool                     # False = still unsolved for you; nothing else is sent
    total: int = 0                 # players who named this line (hints excluded)
    answers: List[LineAnswer] = [] # EVERY category that fits, most named first (0 = nobody yet)
    mine: Optional[str] = None     # the rule_id you named
    hinted: bool = False


class ReverseStatsResponse(BaseModel):
    day_seed: str
    players: int
    avg_pct: Optional[float] = None
    lines: List[LineStats]


class CategoryIndexEntry(BaseModel):
    rule_id: str
    label: str
    times_named: int
    first_named: str


class CategoryIndexResponse(BaseModel):
    found: int
    total: int
    entries: List[CategoryIndexEntry]           # rules the player has named
    remaining_labels: List[str]                 # labels still to find (for a checklist UI)


# ── Attach ────────────────────────────────────────────────────────────────────

def attach(app, ctx: _Ctx) -> None:
    reverse_store.init_db()

    def _indexable_rule_ids() -> Set[str]:
        """Rules that count toward the Category Index: every rule with a visible
        label (same filter the Draft catalog uses — suppressed labels like the
        empty 'Not Legendary' are excluded)."""
        return {rid for rid, rule in ctx.rules.items() if ctx.compact_rule_label(rule.label)}

    def _resolve_pool(payload: ReverseBoardRequest) -> Optional[Set[str]]:
        if not payload.allowed_rule_ids:
            return None  # full pool
        allowed = {rid for rid in payload.allowed_rule_ids if rid in ctx.rules}
        return allowed or None

    def _generate(payload: ReverseBoardRequest):
        preset = PRESETS.get(payload.difficulty, PRESETS[DEFAULT_PRESET])
        max_amb = payload.max_ambiguity if payload.max_ambiguity is not None else preset["max_ambiguity"]
        allowed = _resolve_pool(payload)
        n_rows = max(3, min(9, payload.n_rows))
        n_cols = max(3, min(9, payload.n_cols))
        seed = f"reverse-{uuid.uuid4().hex}"
        # Custom (restricted-pool or non-9x9) boards may shrink; qualifying
        # boards must stay 9x9 to count, so they never shrink.
        is_full = (n_rows == 9 and n_cols == 9 and allowed is None)
        rb = reverse_engine.generate_reverse_board(
            df=ctx.df_play, rules=ctx.rules, rule_masks=ctx.rule_masks,
            seed=seed, n_rows=n_rows, n_cols=n_cols,
            allowed_rule_ids=allowed, max_ambiguity=max_amb,
            candidate_boards=preset["candidate_boards"],
            allow_shrink=not is_full,
        )
        return rb, is_full

    by_key_cache = ctx.df_play.set_index("key")

    def _serialize_grid(rb: reverse_engine.ReverseBoard) -> List[List[ReverseCell]]:
        grid_out: List[List[ReverseCell]] = []
        for i in range(rb.n_rows):
            row_cells: List[ReverseCell] = []
            for j in range(rb.n_cols):
                k = rb.grid[i][j]
                r = by_key_cache.loc[k]
                pid = int(r["pokemon_id"]) if pd.notna(r["pokemon_id"]) else None
                row_cells.append(ReverseCell(key=k, display_name=str(r["display_name"]), pokemon_id=pid))
            grid_out.append(row_cells)
        return grid_out

    # ── Lives / hints ─────────────────────────────────────────────────────────
    def _label(rule_id: str) -> str:
        rule = ctx.rules.get(rule_id)
        if rule is None:
            return rule_id
        return ctx.compact_rule_label(rule.label) or str(rule.label)

    def _play(meta: dict, client_id: Optional[str]):
        """(solved, misses, hint) for this player on this board.

        solved: {"row:3": {axis, line_index, named_rule_id, named_label}}
        misses: {(axis, index, rule_id)}
        hint:   (axis, index) or None

        Daily: read from the database (per player + day). Custom: the board's
        own in-memory record. A daily request without a client id gets a
        throwaway record, so anonymous calls never share one set of lives."""
        day = meta.get("day_seed")
        if meta.get("is_qualifying") and day:
            if not client_id:
                return {}, set(), None
            solved = reverse_store.get_daily_progress(client_id, day)
            misses = {(m["axis"], m["line_index"], m["rule_id"])
                      for m in reverse_store.get_daily_misses(client_id, day)}
            h = reverse_store.get_daily_hint(client_id, day)
            return solved, misses, ((h["axis"], h["line_index"]) if h else None)
        play = meta.setdefault("play", {"solved": {}, "misses": set(), "hint": None})
        return play["solved"], play["misses"], play["hint"]

    def _status(meta: dict, client_id: Optional[str], misses):
        """(lives_left, game_over, revive_available) for this player.

        On the daily board, running out of lives pauses the board while a
        revive is still available (an ad buys one more life): no guesses,
        hints, answers or stats until they revive or end the board."""
        day = meta.get("day_seed")
        daily = bool(meta.get("is_qualifying") and day and client_id)
        rv = reverse_store.get_daily_revive(client_id, day) if daily else {"revives": 0, "declined": False}
        lives = max(0, MAX_LIVES + rv["revives"] * ads_api.REVIVE_LIVES - len(misses))
        if lives > 0:
            return lives, False, False
        can = (daily and not rv["declined"] and ads_api.revive_enabled()
               and rv["revives"] < ads_api.REVIVES_PER_BOARD)
        return 0, not can, can

    def _hints_left(hint) -> int:
        return max(0, MAX_HINTS - (1 if hint else 0))

    def _line_ok(rb, axis: str, index: int) -> bool:
        if axis == "row":
            return 0 <= index < rb.n_rows
        if axis == "col":
            return 0 <= index < rb.n_cols
        return False

    def _reveal(rb, solved) -> List[dict]:
        """The intended category of every line still unsolved. Only sent once
        the board is lost, so the player can see what they missed."""
        out: List[dict] = []
        for axis, count, ids in (("row", rb.n_rows, rb.row_rule_ids), ("col", rb.n_cols, rb.col_rule_ids)):
            for i in range(count):
                if f"{axis}:{i}" in solved:
                    continue
                out.append({"axis": axis, "index": i, "rule_id": ids[i], "label": _label(ids[i])})
        return out

    # ── Daily line stats ──────────────────────────────────────────────────────
    def _pct(count: int, total: int) -> Optional[float]:
        return round(100.0 * count / total, 1) if total else None

    def _counts_with_mine(day_seed: str, solved: dict, hint) -> dict:
        """Cached counts, refreshed if they don't include this player's own
        lines yet (so nobody sees 0% on their own answer)."""
        counts = reverse_store.line_counts(day_seed)
        for v in solved.values():
            if hint == (v["axis"], v["line_index"]):
                continue
            if counts["lines"].get((v["axis"], v["line_index"]), {}).get(v["named_rule_id"], 0) == 0:
                return reverse_store.line_counts(day_seed, fresh=True)
        return counts

    def _mine_pct(counts: dict, axis: str, index: int, rule_id: str) -> Optional[float]:
        line = counts["lines"].get((axis, index), {})
        return _pct(line.get(rule_id, 0), sum(line.values()))

    def _generate_daily(day_seed: str):
        """Deterministic Hard board for the given day. Same seed -> same board,
        so every client shares today's puzzle and it can register progress.
        Saved the first time it's made (daily_store pins), so a data fix
        deployed mid-day can't change a board people are already playing."""
        import dataclasses
        import daily_store
        pin_key = f"reverse:{day_seed}"
        pin = daily_store.get_pin(pin_key)
        if pin:
            try:
                return reverse_engine.ReverseBoard(**pin)
            except TypeError:
                pass
        rb = _generate_daily_fresh(day_seed)
        if rb is None:
            return None
        kept = daily_store.save_pin(pin_key, dataclasses.asdict(rb))
        return reverse_engine.ReverseBoard(**kept)

    def _generate_daily_fresh(day_seed: str):
        preset = PRESETS["hard"]
        return reverse_engine.generate_reverse_board(
            df=ctx.df_play, rules=ctx.rules, rule_masks=ctx.rule_masks,
            seed=f"daily-reverse:{day_seed}", n_rows=9, n_cols=9,
            allowed_rule_ids=None, max_ambiguity=preset["max_ambiguity"],
            candidate_boards=preset["candidate_boards"], allow_shrink=False,
        )

    @app.get("/reverse/presets")
    def reverse_presets():
        return {
            "default": DEFAULT_PRESET,
            "presets": [
                {"id": k, "label": v["label"], "max_ambiguity": v["max_ambiguity"]}
                for k, v in PRESETS.items()
            ],
        }

    @app.get("/board/reverse/daily", response_model=ReverseBoardResponse)
    async def get_daily_reverse(x_client_id: Optional[str] = Header(default=None)):
        day_seed = ctx.today_seed()
        with _daily_lock:
            cached = _daily_cache.get(day_seed)
        if cached is None:
            loop = asyncio.get_event_loop()
            try:
                rb = await asyncio.wait_for(
                    loop.run_in_executor(ctx.executor, lambda: _generate_daily(day_seed)),
                    timeout=_REVERSE_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                raise HTTPException(status_code=503, detail="Daily reverse board generation timed out — try again shortly.")
            if rb is None:
                raise HTTPException(status_code=500, detail="Could not generate the daily reverse board.")
            board_id = f"rb-daily-{day_seed}"
            _remember(board_id, rb, is_qualifying=True, day_seed=day_seed)
            with _daily_lock:
                # Cap the daily cache; days roll over rarely so a small cap is fine.
                if len(_daily_cache) >= 5:
                    _daily_cache.pop(next(iter(_daily_cache)))
                _daily_cache[day_seed] = (board_id, rb)
        else:
            board_id, rb = cached
            # Ensure it's still in the board store (survives eviction).
            with _boards_lock:
                if board_id not in _boards:
                    pass
            _remember(board_id, rb, is_qualifying=True, day_seed=day_seed)

        # Restore solved lines, wrong guesses, lives and the hint, so the
        # board picks up exactly where the player left it today.
        with _boards_lock:
            meta = dict(_boards_meta.get(board_id, {}))
        solved, misses, hint = _play(meta, x_client_id)
        counts = _counts_with_mine(day_seed, solved, hint) if solved else {"lines": {}}
        solved_out = []
        for v in solved.values():
            hinted = hint == (v["axis"], v["line_index"])
            solved_out.append({
                "axis": v["axis"], "index": v["line_index"],
                "label": v["named_label"], "rule_id": v["named_rule_id"],
                "hinted": hinted,
                "pct": None if hinted else _mine_pct(counts, v["axis"], v["line_index"], v["named_rule_id"]),
            })
        lives_left, game_over, can_revive = _status(meta, x_client_id, misses)

        return ReverseBoardResponse(
            board_id=board_id,
            n_rows=rb.n_rows, n_cols=rb.n_cols,
            difficulty="hard",
            ambiguity=rb.ambiguity(),
            is_qualifying=True,
            grid=_serialize_grid(rb),
            note=None,
            day_seed=day_seed,
            solved_lines=solved_out,
            total_lines=rb.n_rows + rb.n_cols,
            lives_left=lives_left,
            hints_left=_hints_left(hint),
            missed=[{"axis": a, "index": i, "rule_id": r} for a, i, r in sorted(misses)],
            game_over=game_over,
            reveal=_reveal(rb, solved) if game_over else [],
            revive=can_revive,
        )

    @app.post("/board/reverse", response_model=ReverseBoardResponse)
    async def create_reverse_board(payload: ReverseBoardRequest):
        loop = asyncio.get_event_loop()
        try:
            rb, is_full = await asyncio.wait_for(
                loop.run_in_executor(ctx.executor, lambda: _generate(payload)),
                timeout=_REVERSE_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            raise HTTPException(status_code=503, detail="Reverse board generation timed out — try again or lower the difficulty.")
        if rb is None:
            raise HTTPException(
                status_code=422,
                detail=("Couldn't build a reverse board with those settings. "
                        "In Advanced mode, enabling more categories (Moves and Abilities especially) "
                        "gives the generator far more room."),
            )

        board_id = f"rb-{uuid.uuid4().hex}"
        # Only the DAILY board registers to the Category Index / Pokedex. Custom
        # boards — even a full 9x9 — are always practice. We still track whether
        # the size came out full, purely to phrase the note correctly.
        came_out_full = is_full and rb.n_rows == 9 and rb.n_cols == 9
        _remember(board_id, rb, is_qualifying=False, day_seed=None)

        grid_out = _serialize_grid(rb)

        requested = (max(3, min(9, payload.n_rows)), max(3, min(9, payload.n_cols)))
        if (rb.n_rows, rb.n_cols) != requested:
            note = (f"Shrunk to {rb.n_rows}\u00d7{rb.n_cols} — the selected categories couldn't fill the "
                    f"requested size. Try enabling more categories (Moves and Abilities especially).")
        else:
            note = "Custom board — practice only. The daily board is where categories register."

        return ReverseBoardResponse(
            board_id=board_id,
            n_rows=rb.n_rows, n_cols=rb.n_cols,
            difficulty=payload.difficulty,
            ambiguity=rb.ambiguity(),
            is_qualifying=False,
            grid=grid_out,
            note=note,
            total_lines=rb.n_rows + rb.n_cols,
        )

    @app.post("/reverse/validate", response_model=ReverseValidateResponse)
    def validate_reverse(payload: ReverseValidateRequest, x_client_id: Optional[str] = Header(default=None)):
        with _boards_lock:
            rb = _boards.get(payload.board_id)
            meta = _boards_meta.get(payload.board_id, {})
        if rb is None:
            # Board evicted from the server cache (restart, or too many since).
            return ReverseValidateResponse(ok=False, reason="unknown_board")
        if not _line_ok(rb, payload.axis, payload.index):
            return ReverseValidateResponse(ok=False, reason="unknown_axis")

        solved, misses, hint = _play(meta, x_client_id)
        key = f"{payload.axis}:{payload.index}"
        lives_left, over, can_revive = _status(meta, x_client_id, misses)
        status = dict(lives_left=lives_left, hints_left=_hints_left(hint))

        # A solved line stays solved; guessing it again changes nothing.
        if key in solved:
            done = solved[key]
            return ReverseValidateResponse(ok=True, reason="already_solved",
                                           canonical_rule_id=done["named_rule_id"],
                                           canonical_label=done["named_label"], **status)
        # Out of lives: the board is locked (or paused, waiting on a revive).
        if lives_left <= 0:
            return ReverseValidateResponse(ok=False, reason="out_of_lives", game_over=over, revive=can_revive,
                                           reveal=_reveal(rb, solved) if over else [], **status)
        # Not a real category (the UI can't send one): wrong, but no charge.
        if payload.guessed_rule_id not in ctx.rules:
            return ReverseValidateResponse(ok=False, reason="wrong", **status)

        # Each category names one line per board. A category already used on
        # another line is turned away (no life lost) — unless it's the only
        # answer this line has left, so a line can never become unsolvable
        # because an alternative answer was spent elsewhere. Whether it fits
        # isn't revealed either way.
        used_key = next((k for k, v in solved.items()
                         if v["named_rule_id"] == payload.guessed_rule_id and k != key), None)
        if used_key:
            sets = rb.row_answer_sets if payload.axis == "row" else rb.col_answer_sets
            answers = set(sets[payload.index])
            used_ids = {v["named_rule_id"] for v in solved.values()}
            others_left = any(r not in used_ids and r in ctx.rules and ctx.compact_rule_label(ctx.rules[r].label)
                              for r in answers)
            if not (payload.guessed_rule_id in answers and not others_left):
                return ReverseValidateResponse(ok=False, reason="already_used", used_on=used_key, **status)

        result = reverse_engine.check_line_guess(rb, payload.axis, payload.index, payload.guessed_rule_id)
        if not result.ok:
            if result.reason != "wrong":
                return ReverseValidateResponse(ok=False, reason=result.reason, **status)
            miss = (payload.axis, payload.index, payload.guessed_rule_id)
            if miss in misses:
                # Same wrong guess twice (double tap, two tabs): never charge twice.
                return ReverseValidateResponse(ok=False, reason="repeat", **status)
            day_seed = meta.get("day_seed")
            if meta.get("is_qualifying") and day_seed:
                if x_client_id:
                    reverse_store.record_daily_miss(x_client_id, day_seed, *miss)
            else:
                with _boards_lock:
                    misses.add(miss)
            lives_left, game_over, can_revive = _status(meta, x_client_id, misses | {miss})
            return ReverseValidateResponse(
                ok=False, reason="wrong", lives_left=lives_left, hints_left=status["hints_left"],
                game_over=game_over, reveal=_reveal(rb, solved) if game_over else [],
                revive=can_revive,
            )

        named_id = result.canonical_rule_id
        label = ctx.compact_rule_label(ctx.rules[named_id].label)
        day_seed = meta.get("day_seed")
        newly = False

        # Registration happens ONLY on the daily board (is_qualifying + a day_seed),
        # and only when we know who's playing.
        if meta.get("is_qualifying") and day_seed and x_client_id:
            # 1. Category Index: the standalone "found X of N" list.
            newly = reverse_store.record_identified(x_client_id, named_id, label)
            # 2. Lock this line for the day (idempotent; first solve wins).
            reverse_store.record_daily_line(
                x_client_id, day_seed, payload.axis, payload.index, named_id, label
            )
            # 3. Pokedex enrichment: add the NAMED rule to every Pokemon in this
            #    line, exactly as the forward daily board's record_rule_use does.
            #    A category proven by deduction is proven, so it appears on those
            #    entries indistinguishably from one proven by answering.
            if payload.axis == "row":
                line_keys = [rb.grid[payload.index][j] for j in range(rb.n_cols)]
            else:
                line_keys = [rb.grid[i][payload.index] for i in range(rb.n_rows)]
            for k in line_keys:
                try:
                    ctx.pokedex_store.record_rule_use(x_client_id, k, label)
                except Exception:
                    # Enrichment is best-effort; never fail a correct guess over it.
                    pass
        elif not meta.get("is_qualifying"):
            with _boards_lock:
                solved[key] = {"axis": payload.axis, "line_index": payload.index,
                               "named_rule_id": named_id, "named_label": label}

        # Daily: how many players named this line the same way.
        pct = None
        if meta.get("is_qualifying") and day_seed and x_client_id:
            same, total = reverse_store.line_share(day_seed, payload.axis, payload.index, named_id)
            pct = _pct(same, total)

        return ReverseValidateResponse(
            ok=True, reason="ok",
            canonical_rule_id=named_id,
            canonical_label=label,
            newly_indexed=newly,
            pct=pct,
            **status,
        )

    @app.post("/reverse/hint", response_model=ReverseHintResponse)
    def reverse_hint(payload: ReverseHintRequest, x_client_id: Optional[str] = Header(default=None)):
        """Spend the board's hint: reveal the intended category of one line.
        The line counts as solved, but it never registers to the Category
        Index or the Pokedex."""
        with _boards_lock:
            rb = _boards.get(payload.board_id)
            meta = _boards_meta.get(payload.board_id, {})
        if rb is None:
            return ReverseHintResponse(ok=False, reason="unknown_board")
        if not _line_ok(rb, payload.axis, payload.index):
            return ReverseHintResponse(ok=False, reason="unknown_axis")

        solved, misses, hint = _play(meta, x_client_id)
        status = dict(lives_left=_status(meta, x_client_id, misses)[0], hints_left=_hints_left(hint))
        if f"{payload.axis}:{payload.index}" in solved:
            return ReverseHintResponse(ok=False, reason="already_solved", **status)
        if status["lives_left"] <= 0:
            return ReverseHintResponse(ok=False, reason="out_of_lives", **status)
        if status["hints_left"] <= 0:
            return ReverseHintResponse(ok=False, reason="no_hints", **status)

        ids = rb.row_rule_ids if payload.axis == "row" else rb.col_rule_ids
        rule_id = ids[payload.index]
        label = _label(rule_id)

        day_seed = meta.get("day_seed")
        if meta.get("is_qualifying") and day_seed:
            if not x_client_id:
                return ReverseHintResponse(ok=False, reason="no_hints", **status)
            # The table's key allows one hint per player per day; if another
            # tab got there first, this insert is refused.
            if not reverse_store.record_daily_hint(x_client_id, day_seed, payload.axis,
                                                   payload.index, rule_id, label):
                return ReverseHintResponse(ok=False, reason="no_hints",
                                           lives_left=status["lives_left"], hints_left=0)
            reverse_store.record_daily_line(x_client_id, day_seed, payload.axis,
                                            payload.index, rule_id, label)
        else:
            with _boards_lock:
                play = meta.setdefault("play", {"solved": {}, "misses": set(), "hint": None})
                if play["hint"]:
                    return ReverseHintResponse(ok=False, reason="no_hints",
                                               lives_left=status["lives_left"], hints_left=0)
                play["hint"] = (payload.axis, payload.index)
                play["solved"][f"{payload.axis}:{payload.index}"] = {
                    "axis": payload.axis, "line_index": payload.index,
                    "named_rule_id": rule_id, "named_label": label}

        return ReverseHintResponse(ok=True, reason="ok", rule_id=rule_id, label=label,
                                   lives_left=status["lives_left"],
                                   hints_left=max(0, status["hints_left"] - 1))

    def _daily_meta(x_client_id: Optional[str]):
        if not x_client_id:
            raise HTTPException(status_code=400, detail="Missing client id.")
        day_seed = ctx.today_seed()
        with _daily_lock:
            cached = _daily_cache.get(day_seed)
        if cached is None:
            raise HTTPException(status_code=404, detail="Load today's Reverse Board first.")
        board_id, rb = cached
        with _boards_lock:
            meta = dict(_boards_meta.get(board_id) or {"is_qualifying": True, "day_seed": day_seed})
        return day_seed, rb, meta

    def _revive_out(rb, meta, x_client_id) -> ReverseReviveResponse:
        solved, misses, _ = _play(meta, x_client_id)
        lives, over, can = _status(meta, x_client_id, misses)
        return ReverseReviveResponse(lives_left=lives, game_over=over, revive=can,
                                     reveal=_reveal(rb, solved) if over else [])

    @app.post("/reverse/daily/revive", response_model=ReverseReviveResponse)
    def reverse_revive(x_client_id: Optional[str] = Header(default=None)):
        """One more life on today's board after a rewarded ad."""
        day_seed, rb, meta = _daily_meta(x_client_id)
        _, misses, _ = _play(meta, x_client_id)
        if not _status(meta, x_client_id, misses)[2] or \
                not reverse_store.add_daily_revive(x_client_id, day_seed, ads_api.REVIVES_PER_BOARD):
            raise HTTPException(status_code=409, detail="No revive available.")
        return _revive_out(rb, meta, x_client_id)

    @app.post("/reverse/daily/end", response_model=ReverseReviveResponse)
    def reverse_end(x_client_id: Optional[str] = Header(default=None)):
        """Skip the revive: end today's board and show the answers."""
        day_seed, rb, meta = _daily_meta(x_client_id)
        _, misses, _ = _play(meta, x_client_id)
        if _status(meta, x_client_id, misses)[0] > 0:
            raise HTTPException(status_code=409, detail="You still have lives left.")
        reverse_store.decline_daily_revive(x_client_id, day_seed)
        return _revive_out(rb, meta, x_client_id)

    @app.get("/reverse/stats", response_model=ReverseStatsResponse)
    def reverse_stats(x_client_id: Optional[str] = Header(default=None)):
        """How everyone named each line of today's board, with every category
        that would have fit (including ones nobody found). A line's breakdown
        is only sent once you've solved it, or once your board is over, so it
        can't be used to solve lines that are still open."""
        day_seed = ctx.today_seed()
        with _daily_lock:
            cached = _daily_cache.get(day_seed)
        if cached is None:
            raise HTTPException(status_code=404, detail="Load today's Reverse Board first.")
        board_id, rb = cached
        with _boards_lock:
            meta = dict(_boards_meta.get(board_id) or {"is_qualifying": True, "day_seed": day_seed})
        solved, misses, hint = _play(meta, x_client_id)
        over = _status(meta, x_client_id, misses)[1] or len(solved) >= rb.n_rows + rb.n_cols
        counts = _counts_with_mine(day_seed, solved, hint)

        lines_out: List[LineStats] = []
        my_pcts: List[float] = []
        for axis, count, sets in (("row", rb.n_rows, rb.row_answer_sets), ("col", rb.n_cols, rb.col_answer_sets)):
            for i in range(count):
                key = f"{axis}:{i}"
                mine = solved.get(key)
                hinted = hint == (axis, i)
                if not (mine or over):
                    lines_out.append(LineStats(axis=axis, index=i, open=False))
                    continue
                named = counts["lines"].get((axis, i), {})
                total = sum(named.values())
                # Every category that fits the line, so players can see what
                # else would have worked. Hidden rules (no visible label) skip.
                fits = [rid for rid in sets[i] if rid in ctx.rules and ctx.compact_rule_label(ctx.rules[rid].label)]
                for rid in named:
                    if rid not in fits:
                        fits.append(rid)
                answers = sorted(
                    (LineAnswer(rule_id=rid, label=_label(rid), count=named.get(rid, 0),
                                pct=_pct(named.get(rid, 0), total) or 0.0) for rid in fits),
                    key=lambda a: (-a.count, a.label.lower()),
                )
                if mine and not hinted:
                    p = _mine_pct(counts, axis, i, mine["named_rule_id"])
                    if p is not None:
                        my_pcts.append(p)
                lines_out.append(LineStats(
                    axis=axis, index=i, open=True, total=total, answers=answers,
                    mine=mine["named_rule_id"] if mine else None, hinted=hinted,
                ))
        return ReverseStatsResponse(
            day_seed=day_seed, players=counts["players"],
            avg_pct=round(sum(my_pcts) / len(my_pcts), 1) if my_pcts else None,
            lines=lines_out,
        )

    @app.get("/reverse/index", response_model=CategoryIndexResponse)
    def reverse_index(x_client_id: Optional[str] = Header(default=None)):
        indexable = _indexable_rule_ids()
        label_by_id = {rid: ctx.compact_rule_label(ctx.rules[rid].label) for rid in indexable}
        total = len(indexable)

        found_map = reverse_store.get_identified(x_client_id) if x_client_id else {}
        # Only count rules that are still indexable (a rule could vanish across
        # dataset changes; don't let stale rows inflate the count).
        entries = [
            CategoryIndexEntry(
                rule_id=rid,
                label=info["rule_label"] or label_by_id.get(rid, rid),
                times_named=info["times_named"],
                first_named=info["first_named"],
            )
            for rid, info in found_map.items() if rid in indexable
        ]
        found_ids = {e.rule_id for e in entries}
        remaining = sorted(
            (label_by_id[rid] for rid in indexable if rid not in found_ids),
            key=lambda s: s.lower(),
        )
        return CategoryIndexResponse(
            found=len(found_ids), total=total,
            entries=sorted(entries, key=lambda e: e.first_named),
            remaining_labels=remaining,
        )


# ── Handoff: how api_main.py wires this in ────────────────────────────────────
#
# Add these lines to api_main.py, AFTER df_play / rules / rule_masks and the
# _board_executor + compact_rule_label + is_daily_seed definitions all exist
# (anywhere after the Draft Board section is fine):
#
#     import reverse_api
#     reverse_api.attach(app, reverse_api._Ctx(
#         df_play=df_play, rules=rules, rule_masks=rule_masks,
#         executor=_board_executor,
#         compact_rule_label=compact_rule_label,
#         is_daily_seed=is_daily_seed,
#         today_seed=lambda: str(date.today()),   # same seed /board falls back to
#         pokedex_store_mod=pokedex_store,
#     ))
#
# `date` and `pokedex_store` are already imported at the top of api_main.py.
# Nothing else in api_main.py changes.