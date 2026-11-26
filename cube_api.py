"""
cube_api.py — endpoints for the Cube (cube_engine.py), mounted onto the main
app the same way reverse_api.py is:

    import cube_api
    cube_api.attach(app, cube_api.Ctx(...))

A square is (f, i, j): face 0-5, then its coordinates along the face's two
axes (see cube_engine.py). A "line" is a face (0-5) or a ring (6-14).

Cube (fill the 54 squares)
  GET  /cube/board?seed=         today's Cube, or a practice Cube for any other seed
  POST /cube/validate            check one square
  POST /cube/hint                reveal a Pokémon for one square
  POST /cube/finish              end today's Cube early (to see the answers)
  GET  /cube/solution?seed=      one answer and the count of answers per square

Reverse Cube (name the 15 hidden rules)
  GET  /cube/reverse/daily       today's Reverse Cube
  POST /cube/reverse             a practice Reverse Cube {difficulty}
  POST /cube/reverse/validate    guess one line's rule
  POST /cube/reverse/hint        reveal one line's rule

Daily runs are saved with daily_store under "cube54:YYYY-MM-DD" and
"rcube54:YYYY-MM-DD", apart from the flat Daily Board, so a reload can't reset
lives. A square is stored as row_i = face, col_j = i*3 + j; a Reverse line as
row_i = line, col_j = 0, pokemon_key = the rule that solved it.

Each day's Cube is pinned the first time it's made (daily_store.save_pin).
The Cube doesn't write to the Pokédex yet: a correct answer can still roll a
shiny sighting, the way Draft boards do.
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
CUBE_HINTS = 3
REVERSE_LIVES = 3
REVERSE_HINTS = 2
_TIMEOUT = 25.0
_CACHE_MAX = 40
FILL_PREFIX = "cube54"
REV_PREFIX = "rcube54"


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


class MonOut(BaseModel):
    key: str
    display_name: str
    pokemon_id: Optional[int] = None


class PickOut(BaseModel):
    f: int
    i: int
    j: int
    mon: MonOut
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
    faces: List[RuleOut]                   # 6
    rings: List[List[RuleOut]]             # 3 axes × 3
    lives: int = CUBE_LIVES
    hints: int = CUBE_HINTS
    run: Optional[CubeRun] = None          # Daily only; practice runs live in the browser


class CellIn(BaseModel):
    seed: str
    f: int
    i: int
    j: int
    used_keys: List[str] = []


class ValidateIn(CellIn):
    pokemon_key: str


class FilledIn(BaseModel):
    f: int
    i: int
    j: int
    key: str


class HintIn(CellIn):
    filled: List[FilledIn] = []


class ValidateOut(BaseModel):
    ok: bool
    reason: str
    mon: Optional[MonOut] = None
    shiny: bool = False
    lives_left: Optional[int] = None
    pct: Optional[float] = None
    finished: bool = False
    stranded: bool = False


class HintOut(BaseModel):
    ok: bool
    reason: str = "ok"
    mon: Optional[MonOut] = None
    hints_left: Optional[int] = None
    finished: bool = False


class SolutionCell(BaseModel):
    f: int
    i: int
    j: int
    count: int
    example: MonOut


class SolutionOut(BaseModel):
    cells: List[SolutionCell]


class SeedIn(BaseModel):
    seed: str


class RevCell(BaseModel):
    f: int
    i: int
    j: int
    mon: MonOut


class RevSolved(BaseModel):
    line: int
    rule_id: str
    label: str
    hinted: bool = False


class RevRun(BaseModel):
    lives_left: int
    hints_left: int
    finished: bool
    won: bool = False
    solved: List[RevSolved] = []
    reveal: List[RevSolved] = []


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
    line: int
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
    rev_boards: Dict[str, dict] = {}
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

    def rule_out(rid: str) -> RuleOut:
        return RuleOut(id=rid, label=ctx.label(rid))

    async def off_thread(fn):
        loop = asyncio.get_event_loop()
        try:
            return await asyncio.wait_for(loop.run_in_executor(ctx.executor, fn), timeout=_TIMEOUT)
        except asyncio.TimeoutError:
            raise HTTPException(status_code=503, detail="The Cube took too long to build. Try again.")

    # ── Boards ────────────────────────────────────────────────────────────────

    def get_board(seed: str) -> ce.CubeBoard:
        with lock:
            if seed in boards:
                return boards[seed]
        board = None
        pin_key = f"{FILL_PREFIX}:{seed}" if dated(seed) else None
        if pin_key:
            pin = daily_store.get_pin(pin_key)
            if pin:
                board = ce.board_from_ids(df, ctx.rule_masks, seed, pin["rings"], pin["faces"], masks=masks)
        if board is None:
            board = ce.generate_cube(df, ctx.rule_masks, seed, masks=masks)
            if board is None:
                raise HTTPException(status_code=503, detail="Couldn't build that Cube. Try another one.")
            if pin_key:
                kept = daily_store.save_pin(pin_key, {"rings": [list(a) for a in board.rings],
                                                      "faces": list(board.faces)})
                if list(kept["faces"]) != list(board.faces):     # another worker pinned first
                    board = ce.board_from_ids(df, ctx.rule_masks, seed, kept["rings"], kept["faces"],
                                              masks=masks) or board
        remember(boards, seed, board)
        return board

    def cell_ok(f, i, j):
        if not (0 <= f < 6 and 0 <= i < 3 and 0 <= j < 3):
            raise HTTPException(status_code=400, detail="That square isn't on the Cube.")

    def ds_fill(seed: str) -> str:
        return f"{FILL_PREFIX}:{seed}"

    def picks_map(run) -> Dict[Tuple[int, int, int], dict]:
        return {(p["row_i"], p["col_j"] // 3, p["col_j"] % 3): p for p in run["picks"]}

    def run_status(run) -> Tuple[int, int, bool]:
        lives = max(0, CUBE_LIVES - run["misses"])
        hints = max(0, CUBE_HINTS - run["hints_used"])
        finished = run["finished"] or lives <= 0 or len(run["picks"]) >= len(ce.CELLS)
        return lives, hints, finished

    def pct(ds: str, f, i, j, key) -> Optional[float]:
        same, total = daily_store.cell_share(ds, f, i * 3 + j, key)
        return round(100.0 * same / total, 1) if total else None

    def run_out(client_id: str, seed: str) -> CubeRun:
        ds = ds_fill(seed)
        run = daily_store.get_run(client_id, ds)
        lives, hints, finished = run_status(run)
        picks = []
        for (f, i, j), p in picks_map(run).items():
            m = mons.get(p["pokemon_key"])
            if m:
                picks.append(PickOut(f=f, i=i, j=j, mon=m, hinted=p["hinted"], shiny=p["shiny"],
                                     pct=None if p["hinted"] else pct(ds, f, i, j, m.key)))
        return CubeRun(lives_left=lives, hints_left=hints, finished=finished, picks=picks)

    @app.get("/cube/board", response_model=CubeBoardOut)
    async def cube_board(seed: Optional[str] = None,
                         x_client_id: Optional[str] = Header(default=None, alias="X-Client-Id")):
        s = (seed or today()).strip()[:64]
        board = await off_thread(lambda: get_board(s))
        daily = is_today(s)
        return CubeBoardOut(
            seed=s, is_daily=daily,
            faces=[rule_out(r) for r in board.faces],
            rings=[[rule_out(r) for r in ax] for ax in board.rings],
            run=run_out(x_client_id, s) if (daily and x_client_id) else None,
        )

    @app.post("/cube/validate", response_model=ValidateOut)
    def cube_validate(body: ValidateIn, x_client_id: Optional[str] = Header(default=None, alias="X-Client-Id")):
        cell_ok(body.f, body.i, body.j)
        board = get_board(body.seed)
        used = set(body.used_keys)
        daily = is_today(body.seed) and bool(x_client_id)
        ds = ds_fill(body.seed)
        run = None
        filled: Dict[Tuple[int, int, int], str] = {}
        if daily:
            run = daily_store.get_run(x_client_id, ds)
            lives, _, finished = run_status(run)
            if finished:
                return ValidateOut(ok=False, reason="board_locked", lives_left=lives, finished=True)
            saved = picks_map(run)
            if (body.f, body.i, body.j) in saved:
                return ValidateOut(ok=False, reason="already_filled", lives_left=lives)
            filled = {c: p["pokemon_key"] for c, p in saved.items()}
            used |= set(filled.values())

        reason = ce.validate(masks, board, body.f, body.i, body.j, body.pokemon_key, used)
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
        face_rule, r1, r2 = board.rules_at(body.f, body.i, body.j)
        daily_store.record_pick(x_client_id, ds, body.f, body.i * 3 + body.j, f"{r1}|{r2}", face_rule,
                                body.pokemon_key, hinted=False, shiny=shiny)
        filled[(body.f, body.i, body.j)] = body.pokemon_key
        done = len(filled) >= len(ce.CELLS)
        if done:
            daily_store.finish(x_client_id, ds)
        return ValidateOut(ok=True, reason="ok", mon=mon, shiny=shiny,
                           lives_left=max(0, CUBE_LIVES - run["misses"]),
                           pct=pct(ds, body.f, body.i, body.j, body.pokemon_key), finished=done,
                           stranded=not done and not ce.still_solvable(masks, board, filled))

    @app.post("/cube/hint", response_model=HintOut)
    def cube_hint(body: HintIn, x_client_id: Optional[str] = Header(default=None, alias="X-Client-Id")):
        cell_ok(body.f, body.i, body.j)
        board = get_board(body.seed)
        daily = is_today(body.seed) and bool(x_client_id)
        ds = ds_fill(body.seed)
        hints = None
        if daily:
            run = daily_store.get_run(x_client_id, ds)
            _, hints, finished = run_status(run)
            if finished:
                return HintOut(ok=False, reason="board_locked", finished=True)
            if hints <= 0:
                return HintOut(ok=False, reason="no_hints", hints_left=0)
            saved = picks_map(run)
            if (body.f, body.i, body.j) in saved:
                return HintOut(ok=False, reason="already_filled", hints_left=hints)
            filled = {c: p["pokemon_key"] for c, p in saved.items()}
        else:
            filled = {(x.f, x.i, x.j): x.key for x in body.filled}
        used = set(body.used_keys) | set(filled.values())
        key = ce.hint_for(masks, board, body.f, body.i, body.j, used, filled)
        if key is None:
            return HintOut(ok=False, reason="no_answer_left")
        if not daily:
            return HintOut(ok=True, mon=mons[key])
        face_rule, r1, r2 = board.rules_at(body.f, body.i, body.j)
        daily_store.record_pick(x_client_id, ds, body.f, body.i * 3 + body.j, f"{r1}|{r2}", face_rule,
                                key, hinted=True)
        done = len(filled) + 1 >= len(ce.CELLS)
        if done:
            daily_store.finish(x_client_id, ds)
        return HintOut(ok=True, mon=mons[key], hints_left=hints - 1, finished=done)

    @app.post("/cube/finish", response_model=CubeRun)
    def cube_finish(body: SeedIn, x_client_id: Optional[str] = Header(default=None, alias="X-Client-Id")):
        if not (is_today(body.seed) and x_client_id):
            raise HTTPException(status_code=400, detail="Only today's Cube is saved.")
        daily_store.finish(x_client_id, ds_fill(body.seed))
        return run_out(x_client_id, body.seed)

    @app.get("/cube/solution", response_model=SolutionOut)
    def cube_solution(seed: str, x_client_id: Optional[str] = Header(default=None, alias="X-Client-Id")):
        if is_today(seed):
            run = daily_store.get_run(x_client_id, ds_fill(seed)) if x_client_id else None
            if not run or not run_status(run)[2]:
                raise HTTPException(status_code=403, detail="Finish today's Cube to see the answers.")
        board = get_board(seed)
        cells = [SolutionCell(f=f, i=i, j=j, count=int(ce.cell_mask(masks, board, f, i, j).sum()),
                              example=mons[board.solution_at(f, i, j)]) for f, i, j in ce.CELLS]
        return SolutionOut(cells=cells)

    # ── Reverse Cube ──────────────────────────────────────────────────────────

    def rev_daily_board(day: str) -> ce.ReverseCube:
        pin_key = f"{REV_PREFIX}:{day}"
        pin = daily_store.get_pin(pin_key)
        if pin:
            rc = ce.reverse_from_saved(df, ctx.rule_masks, day, pin["rings"], pin["faces"], pin["grid"])
            if rc is not None:
                return rc
        rc = ce.generate_reverse_cube(df, ctx.rule_masks, day, "hard")
        if rc is None:
            raise HTTPException(status_code=503, detail="Couldn't build today's Reverse Cube.")
        kept = daily_store.save_pin(pin_key, {"rings": [list(a) for a in rc.board.rings],
                                              "faces": list(rc.board.faces), "grid": list(rc.grid)})
        if list(kept["grid"]) != list(rc.grid):
            rc = ce.reverse_from_saved(df, ctx.rule_masks, day, kept["rings"], kept["faces"], kept["grid"]) or rc
        return rc

    def rev_get(board_id: str) -> dict:
        with lock:
            entry = rev_boards.get(board_id)
        if entry:
            return entry
        prefix = f"{REV_PREFIX}:"
        if board_id.startswith(prefix) and is_today(board_id[len(prefix):]):
            day = board_id[len(prefix):]
            entry = {"rc": rev_daily_board(day), "difficulty": "hard", "daily": True, "day": day}
            remember(rev_boards, board_id, entry)
            return entry
        raise HTTPException(status_code=404, detail="That Reverse Cube has expired. Start a new one.")

    def rev_ds(entry) -> str:
        return f"{REV_PREFIX}:{entry['day']}"

    def rev_state(entry: dict, client_id: Optional[str]) -> dict:
        """{misses, hints_used, solved: {line: (rule_id, hinted)}, finished}"""
        if entry["daily"]:
            if not client_id:
                return {"misses": 0, "hints_used": 0, "solved": {}, "finished": False}
            run = daily_store.get_run(client_id, rev_ds(entry))
            solved = {p["row_i"]: (p["pokemon_key"], p["hinted"]) for p in run["picks"]}
            return {"misses": run["misses"], "hints_used": run["hints_used"], "solved": solved,
                    "finished": run["finished"]}
        return entry["state"]

    def rev_run_out(entry: dict, st: dict) -> RevRun:
        rc: ce.ReverseCube = entry["rc"]
        lives = max(0, REVERSE_LIVES - st["misses"])
        won = len(st["solved"]) >= ce.N_LINES
        finished = st["finished"] or lives <= 0 or won
        solved = [RevSolved(line=l, rule_id=r, label=ctx.label(r), hinted=h)
                  for l, (r, h) in sorted(st["solved"].items())]
        reveal = []
        if finished:
            for l in range(ce.N_LINES):
                if l not in st["solved"]:
                    r = rc.board.line_rule(l)
                    reveal.append(RevSolved(line=l, rule_id=r, label=ctx.label(r)))
        return RevRun(lives_left=lives, hints_left=max(0, REVERSE_HINTS - st["hints_used"]),
                      finished=finished, won=won, solved=solved, reveal=reveal)

    def rev_board_out(board_id: str, entry: dict, client_id: Optional[str]) -> RevBoardOut:
        rc: ce.ReverseCube = entry["rc"]
        cells = [RevCell(f=f, i=i, j=j, mon=mons[rc.key_at(f, i, j)]) for f, i, j in ce.CELLS]
        return RevBoardOut(board_id=board_id, seed=entry.get("day") or board_id, is_daily=entry["daily"],
                           difficulty=entry["difficulty"], cells=cells,
                           run=rev_run_out(entry, rev_state(entry, client_id)))

    @app.get("/cube/reverse/daily", response_model=RevBoardOut)
    async def rev_daily(x_client_id: Optional[str] = Header(default=None, alias="X-Client-Id")):
        board_id = f"{REV_PREFIX}:{today()}"
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

    def line_ok(line: int):
        if not 0 <= line < ce.N_LINES:
            raise HTTPException(status_code=400, detail="That face or ring isn't on the Cube.")

    @app.post("/cube/reverse/validate", response_model=RevGuessOut)
    def rev_validate(body: RevGuessIn, x_client_id: Optional[str] = Header(default=None, alias="X-Client-Id")):
        line_ok(body.line)
        entry = rev_get(body.board_id)
        rc: ce.ReverseCube = entry["rc"]
        st = rev_state(entry, x_client_id)
        run = rev_run_out(entry, st)
        if run.finished:
            return RevGuessOut(ok=False, reason="board_locked", run=run)
        if body.line in st["solved"]:
            return RevGuessOut(ok=False, reason="already_solved", run=run)
        if body.rule_id not in ctx.rules:
            return RevGuessOut(ok=False, reason="unknown_rule", run=run)
        if entry["daily"] and not x_client_id:
            return RevGuessOut(ok=False, reason="no_player", run=run)
        # A rule may name two lines if both really share it (blocking reuse
        # could strand a line whose only answer was spent elsewhere).
        right = body.rule_id in rc.answers[body.line]
        if entry["daily"]:
            ds = rev_ds(entry)
            if right:
                daily_store.record_pick(x_client_id, ds, body.line, 0, "", "", body.rule_id)
            else:
                daily_store.add_miss(x_client_id, ds)
            out = rev_run_out(entry, rev_state(entry, x_client_id))
            if out.finished:
                daily_store.finish(x_client_id, ds)
        else:
            if right:
                st["solved"][body.line] = (body.rule_id, False)
            else:
                st["misses"] += 1
            out = rev_run_out(entry, st)
        return RevGuessOut(ok=right, reason="ok" if right else "wrong", label=ctx.label(body.rule_id), run=out)

    @app.post("/cube/reverse/hint", response_model=RevGuessOut)
    def rev_hint(body: RevGuessIn, x_client_id: Optional[str] = Header(default=None, alias="X-Client-Id")):
        line_ok(body.line)
        entry = rev_get(body.board_id)
        rc: ce.ReverseCube = entry["rc"]
        st = rev_state(entry, x_client_id)
        run = rev_run_out(entry, st)
        if run.finished:
            return RevGuessOut(ok=False, reason="board_locked", run=run)
        if run.hints_left <= 0:
            return RevGuessOut(ok=False, reason="no_hints", run=run)
        if body.line in st["solved"]:
            return RevGuessOut(ok=False, reason="already_solved", run=run)
        if entry["daily"] and not x_client_id:
            return RevGuessOut(ok=False, reason="no_player", run=run)
        rid = rc.board.line_rule(body.line)
        if entry["daily"]:
            ds = rev_ds(entry)
            daily_store.record_pick(x_client_id, ds, body.line, 0, "", "", rid, hinted=True)
            out = rev_run_out(entry, rev_state(entry, x_client_id))
            if out.finished:
                daily_store.finish(x_client_id, ds)
        else:
            st["solved"][body.line] = (rid, True)
            st["hints_used"] += 1
            out = rev_run_out(entry, st)
        return RevGuessOut(ok=True, reason="ok", label=ctx.label(rid), run=out)
