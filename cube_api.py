"""
cube_api.py — endpoints for the 3×3×3 Cube (cube_engine.py), mounted onto the
main app the same way reverse_api.py is:

    import cube_api
    cube_api.attach(app, cube_api.Ctx(...))

Cube (fill the squares)
  GET  /cube/board?seed=         today's Daily Cube, or a practice Cube for any other seed
  POST /cube/validate            check one square
  POST /cube/hint                reveal a Pokémon for one square
  POST /cube/finish              end today's Cube early (to see the answers)
  GET  /cube/solution?seed=      one answer and the count of answers per square

Reverse Cube (name the nine hidden rules)
  GET  /cube/reverse/daily       today's Reverse Cube
  POST /cube/reverse             a practice Reverse Cube {difficulty}
  POST /cube/reverse/validate    guess one slice's rule
  POST /cube/reverse/hint        reveal one slice's rule (one per Cube)

Daily runs (lives, answers, hints) are saved with daily_store under their own
seeds, "cube:YYYY-MM-DD" and "rcube:YYYY-MM-DD", so they never mix with the
flat Daily Board and a reload can't reset them. In those rows a square is
stored as row_i = layer*3 + row, col_j = column; a Reverse slice as
row_i = axis, col_j = index, pokemon_key = the rule id that solved it.

Each day's Cube is pinned the first time it's made (daily_store.save_pin),
like the Daily Board, so a later data fix can't reshuffle a Cube people are
already playing. The Cube doesn't write to the Pokédex yet: correct answers
can still roll a shiny sighting, the way Draft boards do.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from datetime import date
from typing import Callable, Dict, List, Optional, Tuple

import pandas as pd
from fastapi import Header, HTTPException
from pydantic import BaseModel

import cube_engine as ce
import daily_store

CUBE_LIVES = 3
CUBE_HINTS = 2
REVERSE_LIVES = 3
REVERSE_HINTS = 1
_TIMEOUT = 25.0
_CACHE_MAX = 40


class Ctx:
    def __init__(self, df_play, rules, rule_masks, executor, compact_rule_label,
                 roll_shiny_for: Callable[[Optional[str]], bool]):
        self.df_play = df_play
        self.rules = rules
        self.rule_masks = rule_masks
        self.executor = executor
        self.label = lambda rid: compact_rule_label(rules[rid].label) if rid in rules else rid
        self.roll_shiny_for = roll_shiny_for


# ── Models ────────────────────────────────────────────────────────────────────

class RuleOut(BaseModel):
    id: str
    label: str


class AxisOut(BaseModel):
    axis: str                     # "col" | "row" | "layer"
    rules: List[RuleOut]


class MonOut(BaseModel):
    key: str
    display_name: str
    pokemon_id: Optional[int] = None


class PickOut(BaseModel):
    z: int
    y: int
    x: int
    key: str
    display_name: str
    pokemon_id: Optional[int] = None
    hinted: bool = False
    shiny: bool = False
    pct: Optional[float] = None


class CubeRun(BaseModel):
    lives_left: int
    hints_left: int
    finished: bool
    picks: List[PickOut] = []


class CubeBoardOut(BaseModel):
    seed: str
    is_daily: bool
    size: int = 3
    axes: List[AxisOut]
    lives: int = CUBE_LIVES
    hints: int = CUBE_HINTS
    run: Optional[CubeRun] = None          # Daily only; practice runs live in the browser


class CellIn(BaseModel):
    seed: str
    z: int
    y: int
    x: int
    used_keys: List[str] = []


class ValidateIn(CellIn):
    pokemon_key: str


class FilledIn(BaseModel):
    z: int
    y: int
    x: int
    key: str


class HintIn(CellIn):
    filled: List[FilledIn] = []            # practice: what the browser has placed


class ValidateOut(BaseModel):
    ok: bool
    reason: str
    mon: Optional[MonOut] = None
    shiny: bool = False
    lives_left: Optional[int] = None
    pct: Optional[float] = None
    finished: bool = False
    stranded: bool = False                 # the empty squares can no longer all be filled


class HintOut(BaseModel):
    ok: bool
    reason: str = "ok"
    mon: Optional[MonOut] = None
    hints_left: Optional[int] = None
    finished: bool = False


class SolutionCell(BaseModel):
    z: int
    y: int
    x: int
    count: int
    example: MonOut


class SolutionOut(BaseModel):
    cells: List[SolutionCell]


class SeedIn(BaseModel):
    seed: str


class RevCell(BaseModel):
    z: int
    y: int
    x: int
    mon: MonOut


class RevSolved(BaseModel):
    axis: int
    index: int
    rule_id: str
    label: str
    hinted: bool = False


class RevRun(BaseModel):
    lives_left: int
    hints_left: int
    finished: bool
    won: bool = False
    solved: List[RevSolved] = []
    reveal: List[RevSolved] = []           # the intended rules for unsolved slices, once finished


class RevBoardOut(BaseModel):
    board_id: str
    seed: str
    is_daily: bool
    difficulty: str
    cells: List[RevCell]
    lives: int = REVERSE_LIVES
    hints: int = REVERSE_HINTS
    run: RevRun


class RevNewIn(BaseModel):
    difficulty: str = "medium"


class RevGuessIn(BaseModel):
    board_id: str
    axis: int
    index: int
    rule_id: str = ""


class RevGuessOut(BaseModel):
    ok: bool
    reason: str
    label: Optional[str] = None
    run: RevRun


# ── Attach ────────────────────────────────────────────────────────────────────

def attach(app, ctx: Ctx) -> None:
    df = ctx.df_play
    pool = ce.cube_pool(ctx.rule_masks, len(df))
    scoreable = ce._scoreable(ctx.rule_masks, len(df), ctx.rule_masks.keys())
    masks = ce.Masks.build(df, ctx.rule_masks, sorted(set(pool) | set(scoreable)))

    mons: Dict[str, MonOut] = {}
    for k, n, pid in zip(df["key"].astype(str), df["display_name"].astype(str), df["pokemon_id"]):
        mons[k] = MonOut(key=k, display_name=n, pokemon_id=int(pid) if pd.notna(pid) else None)

    boards: Dict[str, ce.CubeBoard] = {}
    rev_boards: Dict[str, dict] = {}       # board_id -> {"rc", "difficulty", "daily", state for practice}
    lock = threading.Lock()

    def today() -> str:
        return str(date.today())

    def is_today(seed: str) -> bool:
        return seed == today()

    def dated(seed: str) -> bool:
        try:
            return date.fromisoformat(seed) <= date.today()
        except ValueError:
            return False

    def remember(store: dict, key: str, val) -> None:
        with lock:
            if len(store) >= _CACHE_MAX:
                store.pop(next(iter(store)))
            store[key] = val

    # ── Boards ────────────────────────────────────────────────────────────────

    def get_board(seed: str) -> ce.CubeBoard:
        with lock:
            if seed in boards:
                return boards[seed]
        board = None
        pin_key = f"cube:{seed}" if dated(seed) else None
        if pin_key:
            pin = daily_store.get_pin(pin_key)
            if pin:
                board = ce.board_from_ids(df, ctx.rule_masks, seed, pin["cols"], pin["rows"], pin["layers"], masks=masks)
        if board is None:
            board = ce.generate_cube(df, ctx.rule_masks, seed, masks=masks)
            if board is None:
                raise HTTPException(status_code=503, detail="Couldn't build that Cube. Try another one.")
            if pin_key:
                kept = daily_store.save_pin(pin_key, {"cols": list(board.cols), "rows": list(board.rows),
                                                      "layers": list(board.layers)})
                if list(kept["cols"]) != list(board.cols):     # another worker pinned first
                    board = ce.board_from_ids(df, ctx.rule_masks, seed, kept["cols"], kept["rows"],
                                              kept["layers"], masks=masks) or board
        remember(boards, seed, board)
        return board

    async def off_thread(fn):
        loop = asyncio.get_event_loop()
        try:
            return await asyncio.wait_for(loop.run_in_executor(ctx.executor, fn), timeout=_TIMEOUT)
        except asyncio.TimeoutError:
            raise HTTPException(status_code=503, detail="The Cube took too long to build. Try again.")

    def axes_out(board: ce.CubeBoard) -> List[AxisOut]:
        return [AxisOut(axis=name, rules=[RuleOut(id=r, label=ctx.label(r)) for r in ids])
                for name, ids in zip(ce.AXES, board.axes)]

    def cell_ok(z, y, x):
        if not all(0 <= v < 3 for v in (z, y, x)):
            raise HTTPException(status_code=400, detail="That square isn't on the Cube.")

    def daily_seed(seed: str) -> str:
        return f"cube:{seed}"

    def picks_map(run) -> Dict[Tuple[int, int, int], dict]:
        return {(p["row_i"] // 3, p["row_i"] % 3, p["col_j"]): p for p in run["picks"]}

    def run_status(run) -> Tuple[int, int, bool]:
        lives = max(0, CUBE_LIVES - run["misses"])
        hints = max(0, CUBE_HINTS - run["hints_used"])
        finished = run["finished"] or lives <= 0 or len(run["picks"]) >= 27
        return lives, hints, finished

    def pct(ds: str, z, y, x, key) -> Optional[float]:
        same, total = daily_store.cell_share(ds, z * 3 + y, x, key)
        return round(100.0 * same / total, 1) if total else None

    def run_out(client_id: str, seed: str) -> CubeRun:
        ds = daily_seed(seed)
        run = daily_store.get_run(client_id, ds)
        lives, hints, finished = run_status(run)
        picks = []
        for (z, y, x), p in picks_map(run).items():
            m = mons.get(p["pokemon_key"])
            if not m:
                continue
            picks.append(PickOut(z=z, y=y, x=x, key=m.key, display_name=m.display_name,
                                 pokemon_id=m.pokemon_id, hinted=p["hinted"], shiny=p["shiny"],
                                 pct=None if p["hinted"] else pct(ds, z, y, x, m.key)))
        return CubeRun(lives_left=lives, hints_left=hints, finished=finished, picks=picks)

    @app.get("/cube/board", response_model=CubeBoardOut)
    async def cube_board(seed: Optional[str] = None,
                         x_client_id: Optional[str] = Header(default=None, alias="X-Client-Id")):
        s = (seed or today()).strip()[:64]
        board = await off_thread(lambda: get_board(s))
        daily = is_today(s)
        return CubeBoardOut(seed=s, is_daily=daily, axes=axes_out(board),
                            run=run_out(x_client_id, s) if (daily and x_client_id) else None)

    @app.post("/cube/validate", response_model=ValidateOut)
    def cube_validate(body: ValidateIn, x_client_id: Optional[str] = Header(default=None, alias="X-Client-Id")):
        cell_ok(body.z, body.y, body.x)
        board = get_board(body.seed)
        used = set(body.used_keys)
        daily = is_today(body.seed) and bool(x_client_id)
        ds = daily_seed(body.seed)
        run = None
        filled: Dict[Tuple[int, int, int], str] = {}
        if daily:
            run = daily_store.get_run(x_client_id, ds)
            lives, _, finished = run_status(run)
            if finished:
                return ValidateOut(ok=False, reason="board_locked", lives_left=lives, finished=True)
            saved = picks_map(run)
            if (body.z, body.y, body.x) in saved:
                return ValidateOut(ok=False, reason="already_filled", lives_left=lives)
            filled = {c: p["pokemon_key"] for c, p in saved.items()}
            used |= set(filled.values())

        reason = ce.validate(masks, board, body.z, body.y, body.x, body.pokemon_key, used)
        if reason != "ok":
            if daily and reason in ("does_not_match", "duplicate"):
                misses = daily_store.add_miss(x_client_id, ds)
                lives = max(0, CUBE_LIVES - misses)
                if lives <= 0:
                    daily_store.finish(x_client_id, ds)
                return ValidateOut(ok=False, reason=reason, lives_left=lives, finished=lives <= 0)
            return ValidateOut(ok=False, reason=reason)

        mon = mons[body.pokemon_key]
        shiny = bool(ctx.roll_shiny_for(x_client_id))
        if not daily:
            return ValidateOut(ok=True, reason="ok", mon=mon, shiny=shiny)

        cols, rows, layers = board.cols, board.rows, board.layers
        daily_store.record_pick(x_client_id, ds, body.z * 3 + body.y, body.x,
                                f"{cols[body.x]}|{rows[body.y]}", layers[body.z],
                                body.pokemon_key, hinted=False, shiny=shiny)
        filled[(body.z, body.y, body.x)] = body.pokemon_key
        done = len(filled) >= 27
        if done:
            daily_store.finish(x_client_id, ds)
        lives = max(0, CUBE_LIVES - run["misses"])
        return ValidateOut(ok=True, reason="ok", mon=mon, shiny=shiny, lives_left=lives,
                           pct=pct(ds, body.z, body.y, body.x, body.pokemon_key), finished=done,
                           stranded=not done and not ce.still_solvable(masks, board, filled))

    @app.post("/cube/hint", response_model=HintOut)
    def cube_hint(body: HintIn, x_client_id: Optional[str] = Header(default=None, alias="X-Client-Id")):
        cell_ok(body.z, body.y, body.x)
        board = get_board(body.seed)
        daily = is_today(body.seed) and bool(x_client_id)
        ds = daily_seed(body.seed)
        if daily:
            run = daily_store.get_run(x_client_id, ds)
            _, hints, finished = run_status(run)
            if finished:
                return HintOut(ok=False, reason="board_locked", finished=True)
            if hints <= 0:
                return HintOut(ok=False, reason="no_hints", hints_left=0)
            saved = picks_map(run)
            if (body.z, body.y, body.x) in saved:
                return HintOut(ok=False, reason="already_filled", hints_left=hints)
            filled = {c: p["pokemon_key"] for c, p in saved.items()}
        else:
            filled = {(f.z, f.y, f.x): f.key for f in body.filled}
        used = set(body.used_keys) | set(filled.values())
        key = ce.hint_for(masks, board, body.z, body.y, body.x, used, filled)
        if key is None:
            return HintOut(ok=False, reason="no_answer_left")
        if not daily:
            return HintOut(ok=True, mon=mons[key])
        daily_store.record_pick(x_client_id, ds, body.z * 3 + body.y, body.x,
                                f"{board.cols[body.x]}|{board.rows[body.y]}", board.layers[body.z],
                                key, hinted=True)
        done = len(filled) + 1 >= 27
        if done:
            daily_store.finish(x_client_id, ds)
        return HintOut(ok=True, mon=mons[key], hints_left=hints - 1, finished=done)

    @app.post("/cube/finish", response_model=CubeRun)
    def cube_finish(body: SeedIn, x_client_id: Optional[str] = Header(default=None, alias="X-Client-Id")):
        if not (is_today(body.seed) and x_client_id):
            raise HTTPException(status_code=400, detail="Only today's Cube is saved.")
        daily_store.finish(x_client_id, daily_seed(body.seed))
        return run_out(x_client_id, body.seed)

    @app.get("/cube/solution", response_model=SolutionOut)
    def cube_solution(seed: str, x_client_id: Optional[str] = Header(default=None, alias="X-Client-Id")):
        if is_today(seed):
            run = daily_store.get_run(x_client_id, daily_seed(seed)) if x_client_id else None
            if not run or not run_status(run)[2]:
                raise HTTPException(status_code=403, detail="Finish today's Cube to see the answers.")
        board = get_board(seed)
        cells = []
        for z, y, x in ce.CELLS:
            n = int(ce.cell_mask(masks, board, z, y, x).sum())
            cells.append(SolutionCell(z=z, y=y, x=x, count=n, example=mons[board.solution_at(z, y, x)]))
        return SolutionOut(cells=cells)

    # ── Reverse Cube ──────────────────────────────────────────────────────────

    def rev_daily_board(seed: str) -> ce.ReverseCube:
        pin_key = f"rcube:{seed}"
        pin = daily_store.get_pin(pin_key)
        if pin:
            rc = ce.reverse_from_saved(df, ctx.rule_masks, seed, pin["cols"], pin["rows"], pin["layers"], pin["grid"])
            if rc is not None:
                return rc
        rc = ce.generate_reverse_cube(df, ctx.rule_masks, seed, "hard")
        if rc is None:
            raise HTTPException(status_code=503, detail="Couldn't build today's Reverse Cube.")
        kept = daily_store.save_pin(pin_key, {"cols": list(rc.board.cols), "rows": list(rc.board.rows),
                                              "layers": list(rc.board.layers), "grid": list(rc.grid)})
        if list(kept["grid"]) != list(rc.grid):
            rc = ce.reverse_from_saved(df, ctx.rule_masks, seed, kept["cols"], kept["rows"],
                                       kept["layers"], kept["grid"]) or rc
        return rc

    def rev_get(board_id: str) -> dict:
        with lock:
            entry = rev_boards.get(board_id)
        if entry:
            return entry
        if board_id.startswith("rcube:") and is_today(board_id[6:]):
            rc = rev_daily_board(board_id[6:])
            entry = {"rc": rc, "difficulty": "hard", "daily": True, "day": board_id[6:]}
            remember(rev_boards, board_id, entry)
            return entry
        raise HTTPException(status_code=404, detail="That Reverse Cube has expired. Start a new one.")

    def rev_state(entry: dict, client_id: Optional[str]) -> dict:
        """{misses, hints_used, solved: {(axis, i): (rule_id, hinted)}, finished}"""
        if entry["daily"]:
            if not client_id:
                return {"misses": 0, "hints_used": 0, "solved": {}, "finished": False}
            run = daily_store.get_run(client_id, f"rcube:{entry['day']}")
            solved = {(p["row_i"], p["col_j"]): (p["pokemon_key"], p["hinted"]) for p in run["picks"]}
            return {"misses": run["misses"], "hints_used": run["hints_used"], "solved": solved,
                    "finished": run["finished"]}
        return entry["state"]

    def rev_run_out(entry: dict, st: dict) -> RevRun:
        rc: ce.ReverseCube = entry["rc"]
        lives = max(0, REVERSE_LIVES - st["misses"])
        won = len(st["solved"]) >= 9
        finished = st["finished"] or lives <= 0 or won
        solved = [RevSolved(axis=a, index=i, rule_id=r, label=ctx.label(r), hinted=h)
                  for (a, i), (r, h) in sorted(st["solved"].items())]
        reveal = []
        if finished:
            for a in range(3):
                for i in range(3):
                    if (a, i) not in st["solved"]:
                        r = rc.board.axes[a][i]
                        reveal.append(RevSolved(axis=a, index=i, rule_id=r, label=ctx.label(r)))
        return RevRun(lives_left=lives, hints_left=max(0, REVERSE_HINTS - st["hints_used"]),
                      finished=finished, won=won, solved=solved, reveal=reveal)

    def rev_board_out(board_id: str, entry: dict, client_id: Optional[str]) -> RevBoardOut:
        rc: ce.ReverseCube = entry["rc"]
        cells = [RevCell(z=z, y=y, x=x, mon=mons[rc.key_at(z, y, x)]) for z, y, x in ce.CELLS]
        return RevBoardOut(board_id=board_id, seed=entry.get("day") or board_id, is_daily=entry["daily"],
                           difficulty=entry["difficulty"], cells=cells,
                           run=rev_run_out(entry, rev_state(entry, client_id)))

    @app.get("/cube/reverse/daily", response_model=RevBoardOut)
    async def rev_daily(x_client_id: Optional[str] = Header(default=None, alias="X-Client-Id")):
        board_id = f"rcube:{today()}"
        entry = await off_thread(lambda: rev_get(board_id))
        return rev_board_out(board_id, entry, x_client_id)

    @app.post("/cube/reverse", response_model=RevBoardOut)
    async def rev_new(body: RevNewIn):
        diff = body.difficulty if body.difficulty in ce.REVERSE_PRESETS else "medium"
        board_id = uuid.uuid4().hex[:16]
        rc = await off_thread(lambda: ce.generate_reverse_cube(df, ctx.rule_masks, board_id, diff))
        if rc is None:
            raise HTTPException(status_code=503, detail="Couldn't build a Reverse Cube. Try again.")
        entry = {"rc": rc, "difficulty": diff, "daily": False,
                 "state": {"misses": 0, "hints_used": 0, "solved": {}, "finished": False}}
        remember(rev_boards, board_id, entry)
        return rev_board_out(board_id, entry, None)

    def rev_slice_ok(axis: int, index: int):
        if not (0 <= axis < 3 and 0 <= index < 3):
            raise HTTPException(status_code=400, detail="That slice isn't on the Cube.")

    @app.post("/cube/reverse/validate", response_model=RevGuessOut)
    def rev_validate(body: RevGuessIn, x_client_id: Optional[str] = Header(default=None, alias="X-Client-Id")):
        rev_slice_ok(body.axis, body.index)
        entry = rev_get(body.board_id)
        rc: ce.ReverseCube = entry["rc"]
        st = rev_state(entry, x_client_id)
        run = rev_run_out(entry, st)
        if run.finished:
            return RevGuessOut(ok=False, reason="board_locked", run=run)
        if (body.axis, body.index) in st["solved"]:
            return RevGuessOut(ok=False, reason="already_solved", run=run)
        if body.rule_id not in ctx.rules:
            return RevGuessOut(ok=False, reason="unknown_rule", run=run)
        # A rule may name two slices if both really share it: blocking reuse
        # could strand a slice whose only answer was spent elsewhere.
        right = body.rule_id in rc.answers[body.axis][body.index]
        daily = entry["daily"] and bool(x_client_id)
        ds = f"rcube:{entry.get('day')}"
        if right:
            if daily:
                daily_store.record_pick(x_client_id, ds, body.axis, body.index, "", "", body.rule_id)
            elif not entry["daily"]:
                st["solved"][(body.axis, body.index)] = (body.rule_id, False)
        else:
            if daily:
                daily_store.add_miss(x_client_id, ds)
            elif not entry["daily"]:
                st["misses"] += 1
        st = rev_state(entry, x_client_id) if daily else st
        if not entry["daily"] or daily:
            out = rev_run_out(entry, st)
            if out.finished and daily:
                daily_store.finish(x_client_id, ds)
        else:
            out = run
        return RevGuessOut(ok=right, reason="ok" if right else "wrong", label=ctx.label(body.rule_id), run=out)

    @app.post("/cube/reverse/hint", response_model=RevGuessOut)
    def rev_hint(body: RevGuessIn, x_client_id: Optional[str] = Header(default=None, alias="X-Client-Id")):
        rev_slice_ok(body.axis, body.index)
        entry = rev_get(body.board_id)
        rc: ce.ReverseCube = entry["rc"]
        st = rev_state(entry, x_client_id)
        run = rev_run_out(entry, st)
        if run.finished:
            return RevGuessOut(ok=False, reason="board_locked", run=run)
        if run.hints_left <= 0:
            return RevGuessOut(ok=False, reason="no_hints", run=run)
        if (body.axis, body.index) in st["solved"]:
            return RevGuessOut(ok=False, reason="already_solved", run=run)
        rid = rc.board.axes[body.axis][body.index]
        daily = entry["daily"] and bool(x_client_id)
        if daily:
            ds = f"rcube:{entry.get('day')}"
            daily_store.record_pick(x_client_id, ds, body.axis, body.index, "", "", rid, hinted=True)
            st = rev_state(entry, x_client_id)
            out = rev_run_out(entry, st)
            if out.finished:
                daily_store.finish(x_client_id, ds)
        elif not entry["daily"]:
            st["solved"][(body.axis, body.index)] = (rid, True)
            st["hints_used"] += 1
            out = rev_run_out(entry, st)
        else:
            out = run
        return RevGuessOut(ok=True, reason="ok", label=ctx.label(rid), run=out)
