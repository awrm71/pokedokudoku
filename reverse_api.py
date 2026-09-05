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
  GET  /reverse/index            the caller's Category Index progress
  GET  /reverse/presets          difficulty preset definitions for the UI

The answer key (which rules each line accepts) NEVER leaves the server. The
client only gets the filled grid and the board id; guesses are checked here.
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
        _boards_meta[board_id] = {"is_qualifying": is_qualifying, "day_seed": day_seed}


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
    solved_lines: List[dict] = []      # [{axis, index, label, rule_id}] to restore a locked daily board
    total_lines: Optional[int] = None  # n_rows + n_cols
    # NOTE: no rule ids or labels for UNSOLVED lines — that's the whole puzzle.


class ReverseValidateRequest(BaseModel):
    board_id: str
    axis: str          # "row" | "col"
    index: int
    guessed_rule_id: str


class ReverseValidateResponse(BaseModel):
    ok: bool
    reason: str                       # "ok" | "wrong" | "unknown_axis" | "unknown_board"
    canonical_rule_id: str = ""
    canonical_label: str = ""
    newly_indexed: bool = False       # advanced the Category Index (first-ever + qualifying board)


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

    def _generate_daily(day_seed: str):
        """Deterministic Hard board for the given day. Same seed -> same board,
        so every client shares today's puzzle and it can register progress."""
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

        # Restore already-solved lines so the board stays locked for the day.
        solved = reverse_store.get_daily_progress(x_client_id, day_seed) if x_client_id else {}
        solved_out = [
            {"axis": v["axis"], "index": v["line_index"],
             "label": v["named_label"], "rule_id": v["named_rule_id"]}
            for v in solved.values()
        ]

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
        )

    @app.post("/reverse/validate", response_model=ReverseValidateResponse)
    def validate_reverse(payload: ReverseValidateRequest, x_client_id: Optional[str] = Header(default=None)):
        with _boards_lock:
            rb = _boards.get(payload.board_id)
            meta = _boards_meta.get(payload.board_id, {})
        if rb is None:
            # Board evicted from the server cache (restart, or too many since).
            return ReverseValidateResponse(ok=False, reason="unknown_board")

        if payload.guessed_rule_id not in ctx.rules:
            return ReverseValidateResponse(ok=False, reason="wrong")

        result = reverse_engine.check_line_guess(rb, payload.axis, payload.index, payload.guessed_rule_id)
        if not result.ok:
            return ReverseValidateResponse(ok=False, reason=result.reason)

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

        return ReverseValidateResponse(
            ok=True, reason="ok",
            canonical_rule_id=named_id,
            canonical_label=label,
            newly_indexed=newly,
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