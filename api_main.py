from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel
from typing import List, Optional
import pandas as pd
import json
import os
import secrets
import uuid
from pathlib import Path
from datetime import date

import engine
import pokedex_store
import daily_store

app = FastAPI()

PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
SPRITES_REPO_DIR = PROJECT_ROOT / "sprites_repo" / "sprites" / "pokemon"
SPRITES_CDN_BASE = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/other/official-artwork"

# Dev-friendly CORS so a static frontend served from another local port can call the API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

N = 9

class ValidatePickRequest(BaseModel):
    seed: str
    n_rows: int
    n_cols: int
    row_i: int
    col_j: int
    pokemon_key: str
    used_keys: List[str] = []

class ValidatePickResponse(BaseModel):
    ok: bool
    reason: str
    # Set on a correct pick that rolled shiny. shiny_registered is True only
    # on today's Daily Board, where the shiny goes into the Shiny Dex; on
    # Draft and Shuffle boards it's a sighting that isn't kept.
    shiny: bool = False
    shiny_registered: bool = False
    # Daily Board only (None elsewhere):
    lives_left: Optional[int] = None    # the server's count, so a refresh can't reset it
    pct: Optional[float] = None         # share of players who answered this square with this Pokemon
    finished: bool = False              # the board just ended (filled, or out of lives)


# ── Shiny odds ──────────────────────────────────────────────────────────────
# Mirrors the main-series games from Gen 6 on: every Pokémon is a 1-in-4096
# roll, and the Shiny Charm works by re-rolling (+2 extra rolls), the same
# way the real charm takes 1/4096 to ~1/1365.
#   • Each completed-and-restarted Pokédex = one Shiny Charm = +2 rolls.
#   • Daily streak bonus, our take on chaining: +1 roll at 7 days, +2 at 30.
# Charms stop adding rolls after MAX_CHARMS so shinies never stop being rare.
SHINY_BASE = 4096
# Test mode: start the server with PDD_SHINY_BASE=2 (for example) to make
# shinies 1-in-2 so you can see the whole flow. Never set this in production.
_test_base = os.environ.get("PDD_SHINY_BASE", "").strip()
if _test_base.isdigit() and int(_test_base) >= 1:
    SHINY_BASE = int(_test_base)
    print(f"[shiny] TEST MODE: base odds 1 in {SHINY_BASE}")
ROLLS_PER_CHARM = 2
MAX_CHARMS = 10
STREAK_TIERS = ((30, 2), (7, 1))   # (days needed, bonus rolls), highest first


def streak_bonus(streak: int) -> int:
    for days, bonus in STREAK_TIERS:
        if streak >= days:
            return bonus
    return 0


def shiny_rolls_for(charms: int, streak: int) -> int:
    return 1 + ROLLS_PER_CHARM * min(max(charms, 0), MAX_CHARMS) + streak_bonus(streak)


def shiny_odds(rolls: int) -> int:
    """'1 in N' for a given number of rolls, rounded the way the games' wikis do."""
    chance = 1 - (1 - 1 / SHINY_BASE) ** max(rolls, 1)
    return max(1, round(1 / chance))


def roll_shiny(rolls: int) -> bool:
    # secrets uses the OS's secure RNG, so outcomes can't be predicted.
    return any(secrets.randbelow(SHINY_BASE) == 0 for _ in range(max(rolls, 1)))


class PokemonLookupResponse(BaseModel):
    key: str
    display_name: str
    pokemon_id: Optional[int] = None

DEFAULT_CONSTRAINTS = engine.BoardConstraints(
    min_candidates_per_cell=1,
    max_candidates_per_cell=None,
    max_tries=5000,
    require_distinct_solution=True,
)

def compact_rule_label(label: str) -> str:
    out = str(label or "").strip()

    # Safety: suppress stale gen-number labels
    if out.startswith("Gen: "):
        return ""

    # Type labels
    if out.startswith("Type: "):
        return out[6:]

    # Move labels
    if out.startswith("Move: "):
        return "Learns " + out[6:]

    # Ability labels
    if out.startswith("Has Ability: "):
        return "Ability: " + out[13:]
    if out.startswith("Ability: "):
        return out

    # Evolution position
    positions = {
        "Position: Start":     "Base Stage",
        "Position: Middle":    "Middle Stage",
        "Position: Final":     "Final Form",
        "Position: None":      "Doesn't Evolve",
        "Position: Premature": "Baby Pokemon",
        "Base Stage":          "Base Stage",
        "Mid Stage":           "Middle Stage",
        "Final Form":          "Final Form",
        "Does Not Evolve":     "Doesn't Evolve",
        "Baby Stage":          "Baby Pokemon",
    }
    if out in positions:
        return positions[out]

    # Evolution method — consistent "Evolves by X" pattern
    methods = {
        "Evolves By: Level-up":   "Evolved by Level Up",
        "Evolves By: Level-Up":   "Evolved by Level Up",
        "Evolves By: Trade":      "Evolved by Trade",
        "Evolves By: Friendship": "Evolved by Friendship",
        "Evolves By: Item":       "Evolved by Item",
        "Evolves: Level Up":      "Evolved by Level Up",
        "Evolves: Trading":       "Evolved by Trade",
        "Evolves: Friendship":    "Evolved by Friendship",
        "Evolves: Item":          "Evolved by Item",
    }
    if out in methods:
        return methods[out]

    # Flag / categorical labels
    flags = {
        "Is Branched":    "Branched Evolution",
        "Branched Line":  "Branched Evolution",
        "Single-type":    "Single Type",
        "Mono-Type":      "Single Type",
        "Dual-type":      "Dual Type",
        "Dual-Type":      "Dual Type",
        "Baby":           "Baby Pokemon",
        "Fossil":         "Fossil Pokemon",
        "Mega Evolved":   "Mega Evolution",
        "Mega":           "Mega Evolution",
        "G-Max":          "Gigantamax",
        "Gmax":           "Gigantamax",
        "Starter":        "Starter Pokemon",
        "Paradox":        "Paradox Pokemon",
        "Ultra Beast":    "Ultra Beast",
        "Regional Form":  "Regional Form",
        "Not Legendary":  "",
    }
    if out in flags:
        return flags[out]

    return out


def load_data() -> pd.DataFrame:
    pq = DATA_DIR / "pokedex.parquet"
    csv = DATA_DIR / "pokedex.csv"

    if pq.exists():
        df = pd.read_parquet(pq)
    elif csv.exists():
        df = pd.read_csv(csv)
    else:
        raise FileNotFoundError(f"No pokedex.parquet or pokedex.csv found in {DATA_DIR}")

    if "type2" not in df.columns:
        df["type2"] = ""
    df["type2"] = df["type2"].fillna("")

    def _coerce_bool_col(col: str) -> None:
        if col not in df.columns:
            return
        s = df[col]
        if s.dtype == bool:
            return
        if pd.api.types.is_numeric_dtype(s):
            df[col] = pd.to_numeric(s, errors="coerce").fillna(0).astype(int).astype(bool)
            return
        df[col] = (
            s.astype(str)
            .str.lower()
            .map({
                "true": True,
                "false": False,
                "1": True,
                "0": False,
                "yes": True,
                "no": False,
            })
            .fillna(False)
        )

    # Coerce expected boolean columns.
    for col in [c for c in df.columns if str(c).startswith(("is_", "has_", "evolves_by_", "learns_", "has_ability_"))]:
        _coerce_bool_col(str(col))

    if "gen" in df.columns:
        df["gen"] = pd.to_numeric(df["gen"], errors="coerce").astype("Int64")

    if "pokemon_id" in df.columns:
        df["pokemon_id"] = pd.to_numeric(df["pokemon_id"], errors="coerce").astype("Int64")

    return df

df = load_data()
df_play = engine.apply_answer_policy(df, policy="species_only")
rules = engine.build_default_rules(df_play)
rule_masks = engine.compute_rule_masks(df_play, rules)
name_index = engine.build_name_index(df_play)

pokedex_store.init_db()
daily_store.init_db()

# The Daily Board keeps its lives and hints on the server (daily_store), so the
# numbers here are the source of truth; app.js starts from the same values.
DAILY_LIVES = 3
DAILY_HINTS = 3

def is_daily_seed(seed: str) -> bool:
    """A board only counts toward the Pokedex if its seed is today's canonical
    daily seed — i.e. the same value /board falls back to when no seed is given.
    Shuffle boards use engine.py's randomSeed()-style large ints and will never
    match this, so this check can't be spoofed by a client claiming 'daily'."""
    return seed == str(date.today())

def serialize_board(board):
    return {
        "seed": board.seed,
        "n_rows": board.n_rows,
        "n_cols": board.n_cols,
        "row_rule_ids": board.row_rule_ids,
        "col_rule_ids": board.col_rule_ids,
        "row_rule_labels": [compact_rule_label(rules[rid].label) for rid in board.row_rule_ids],
        "col_rule_labels": [compact_rule_label(rules[cid].label) for cid in board.col_rule_ids],
    }


def lookup_pokemon(name: str) -> Optional[dict]:
    key = engine.resolve_input_to_key(name, name_index)
    if not key:
        return None

    row = df_play.loc[df_play["key"] == key]
    if row.empty:
        return None

    r0 = row.iloc[0]
    pokemon_id = None
    if "pokemon_id" in r0 and pd.notna(r0["pokemon_id"]):
        pokemon_id = int(r0["pokemon_id"])

    return {
        "key": str(r0["key"]),
        "display_name": str(r0["display_name"]),
        "pokemon_id": pokemon_id,
    }

import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading

_board_executor = ThreadPoolExecutor(max_workers=2)

# Cache boards by (seed, n) so validate and hint don't regenerate on every call
_board_cache: dict = {}
_cache_lock = threading.Lock()
_CACHE_MAX = 30

# Draft Board seeds map to their own restricted rule pool + constraints, so
# validate/hint can correctly regenerate the SAME board later (e.g. after a
# cache eviction or restart) instead of falling back to the full rule pool.
_draft_configs: dict = {}
_DRAFT_CONFIG_MAX = 30

def _get_or_generate_board(seed: str, n_rows: int, n_cols: int):
    key = f"{seed}:{n_rows}x{n_cols}"
    with _cache_lock:
        if key in _board_cache:
            return _board_cache[key]

    draft_cfg = _draft_configs.get(seed)
    if draft_cfg is not None:
        board = engine.generate_board(
            df=df_play, rules=rules, rule_masks=rule_masks,
            seed=seed, n_rows=n_rows, n_cols=n_cols, constraints=draft_cfg["constraints"],
            allowed_rule_ids=draft_cfg["allowed_rule_ids"],
        )
    else:
        board = engine.generate_board(
            df=df_play, rules=rules, rule_masks=rule_masks,
            seed=seed, n_rows=n_rows, n_cols=n_cols, constraints=DEFAULT_CONSTRAINTS,
        )
    with _cache_lock:
        if len(_board_cache) >= _CACHE_MAX:
            del _board_cache[next(iter(_board_cache))]
        _board_cache[key] = board
    return board

@app.get("/board")
async def get_board(seed: str | None = None, n: int = 9, n_rows: int | None = None, n_cols: int | None = None):
    # n_rows/n_cols take priority when given; n stays as the simple square shorthand
    # (and the fallback for any old client still calling /board?n=9).
    row_count = n_rows if n_rows is not None else n
    col_count = n_cols if n_cols is not None else n
    board_seed = seed or str(date.today())
    loop = asyncio.get_event_loop()
    try:
        board = await asyncio.wait_for(
            loop.run_in_executor(
                _board_executor,
                lambda: _get_or_generate_board(board_seed, row_count, col_count)
            ),
            timeout=25.0
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=503, detail="Board generation timed out — try shuffling")
    if board is None:
        raise HTTPException(status_code=500, detail="Could not generate board")
    out = serialize_board(board)
    # Only today's board is saved and has answer stats; app.js keys off this.
    out["is_daily"] = is_daily_seed(board_seed)
    return out


# ── Draft Board: user-customized category selection ─────────────────────
# Groups mirror how players think about categories, not the internal
# generation-weighting groups engine.py uses (which are more granular, e.g.
# splitting out mega/gmax/starter/ultra/paradox) — those are all folded into
# "other" here for a simpler, 6-bucket picker UI.
_CATALOG_GROUP_ORDER = ["type", "region", "evolution", "move", "ability", "other"]


def _catalog_group_for(rid: str) -> str:
    if rid.startswith("type_"):
        return "type"
    if rid.startswith("region_"):
        return "region"
    if rid.startswith("move_"):
        return "move"
    if rid.startswith("ability_"):
        return "ability"
    if rid.startswith("evo_") or rid.startswith("evolves_by_") or rid == "branched":
        return "evolution"
    return "other"


class CatalogEntry(BaseModel):
    rule_id: str
    label: str


class RuleCatalogResponse(BaseModel):
    groups: dict[str, List[CatalogEntry]]
    total_rules: int


@app.get("/rules/catalog", response_model=RuleCatalogResponse)
def get_rules_catalog():
    groups: dict[str, List[CatalogEntry]] = {g: [] for g in _CATALOG_GROUP_ORDER}
    for rid, rule in rules.items():
        label = compact_rule_label(rule.label)
        if not label:
            continue  # e.g. "Not Legendary" maps to "" and is intentionally suppressed elsewhere; keep it out of the picker too
        groups[_catalog_group_for(rid)].append(CatalogEntry(rule_id=rid, label=label))
    for g in groups:
        groups[g].sort(key=lambda e: e.label.lower())
    return RuleCatalogResponse(groups=groups, total_rules=sum(len(v) for v in groups.values()))


# Kept deliberately small — measured against the real dataset, a FAILING
# attempt costs roughly 0.04s per try, so 300 tries bounds a single failing
# attempt to ~12s. Successful generation is fast regardless of this cap
# (never needed anywhere near 300 tries in testing) — this only affects how
# long we're willing to keep trying before concluding a selection won't work.
_DRAFT_MAX_TRIES = 300
_DRAFT_TIMEOUT_SECONDS = 20.0


class DraftBoardRequest(BaseModel):
    allowed_rule_ids: List[str]
    n_rows: int = 9
    n_cols: int = 9
    allow_single_answers: bool = True
    guaranteed_solvable: bool = True


class DraftBoardResponse(BaseModel):
    seed: str
    n_rows: int
    n_cols: int
    row_rule_ids: List[str]
    col_rule_ids: List[str]
    row_rule_labels: List[str]
    col_rule_labels: List[str]
    adjusted_n_rows: Optional[int] = None  # set only if the requested size wasn't feasible and we shrank it
    adjusted_n_cols: Optional[int] = None
    note: Optional[str] = None


def _draft_constraints(allow_single_answers: bool, guaranteed_solvable: bool) -> engine.BoardConstraints:
    return engine.BoardConstraints(
        min_candidates_per_cell=1 if allow_single_answers else 2,
        max_candidates_per_cell=None,
        max_tries=_DRAFT_MAX_TRIES,
        require_distinct_solution=guaranteed_solvable,
    )


def _attempt_draft_board(n_rows: int, n_cols: int, allowed: set, constraints: engine.BoardConstraints):
    seed = f"draft-{uuid.uuid4().hex}"
    board = engine.generate_board(
        df=df_play, rules=rules, rule_masks=rule_masks,
        seed=seed, n_rows=n_rows, n_cols=n_cols, constraints=constraints, allowed_rule_ids=allowed,
    )
    return board, seed


@app.post("/board/draft", response_model=DraftBoardResponse)
async def create_draft_board(payload: DraftBoardRequest):
    allowed = {rid for rid in payload.allowed_rule_ids if rid in rules}
    if len(allowed) < 2:
        raise HTTPException(status_code=422, detail="Select at least a couple of categories to build a board from.")

    # Each axis independently clamped to 1-9 — rectangular boards (e.g. 6x3)
    # are fully supported, not just square ones.
    requested_n_rows = max(1, min(9, payload.n_rows))
    requested_n_cols = max(1, min(9, payload.n_cols))
    constraints = _draft_constraints(payload.allow_single_answers, payload.guaranteed_solvable)
    loop = asyncio.get_event_loop()

    async def _try(n_rows: int, n_cols: int):
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(_board_executor, lambda: _attempt_draft_board(n_rows, n_cols, allowed, constraints)),
                timeout=_DRAFT_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            return None, None

    board, seed = await _try(requested_n_rows, requested_n_cols)

    adjusted_n_rows = None
    adjusted_n_cols = None
    note = None
    if board is None and (requested_n_rows > 3 or requested_n_cols > 3):
        # Shrink whichever axes are still above a modest floor — an
        # unrequested auto-shrink all the way down to 1 or 2 would feel like
        # a bad surprise, so the automatic rescue only goes down to 3.
        fallback_n_rows = max(3, requested_n_rows - 3)
        fallback_n_cols = max(3, requested_n_cols - 3)
        board, seed = await _try(fallback_n_rows, fallback_n_cols)
        if board is not None:
            adjusted_n_rows = fallback_n_rows
            adjusted_n_cols = fallback_n_cols
            note = (
                f"A {requested_n_rows}\u00d7{requested_n_cols} board wasn't feasible with these categories, "
                f"so this is a {fallback_n_rows}\u00d7{fallback_n_cols} board instead. Enabling more categories "
                f"\u2014 Moves and Abilities especially \u2014 usually opens up more room."
            )

    if board is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "That combination of categories couldn't produce a valid board, even at a smaller size. "
                "Try enabling more categories \u2014 Moves and Abilities in particular add a lot of flexibility "
                "\u2014 or loosen your Type/Region selections."
            ),
        )

    with _cache_lock:
        if len(_draft_configs) >= _DRAFT_CONFIG_MAX:
            del _draft_configs[next(iter(_draft_configs))]
        _draft_configs[seed] = {"allowed_rule_ids": allowed, "constraints": constraints}
        if len(_board_cache) >= _CACHE_MAX:
            del _board_cache[next(iter(_board_cache))]
        _board_cache[f"{seed}:{board.n_rows}x{board.n_cols}"] = board

    resp = serialize_board(board)
    return DraftBoardResponse(
        seed=resp["seed"], n_rows=resp["n_rows"], n_cols=resp["n_cols"],
        row_rule_ids=resp["row_rule_ids"], col_rule_ids=resp["col_rule_ids"],
        row_rule_labels=resp["row_rule_labels"], col_rule_labels=resp["col_rule_labels"],
        adjusted_n_rows=adjusted_n_rows, adjusted_n_cols=adjusted_n_cols, note=note,
    )

# ── Reverse Board: deduction mode ───────────────────────────────────────
import reverse_api
reverse_api.attach(app, reverse_api._Ctx(
    df_play=df_play, rules=rules, rule_masks=rule_masks,
    executor=_board_executor,
    compact_rule_label=compact_rule_label,
    is_daily_seed=is_daily_seed,
    today_seed=lambda: str(date.today()),
    pokedex_store_mod=pokedex_store,
))

@app.get("/sprite/shiny/{pokemon_id}.png")
def get_shiny_sprite(pokemon_id: int):
    """Shiny version of /sprite/{id}.png: local file if the sprites repo has
    it, otherwise the matching shiny official artwork on the CDN."""
    local = SPRITES_REPO_DIR / "shiny" / f"{pokemon_id}.png"
    if local.exists():
        return FileResponse(local, media_type="image/png")
    return RedirectResponse(url=f"{SPRITES_CDN_BASE}/shiny/{pokemon_id}.png")


@app.get("/sprite/{pokemon_id}.png")
def get_sprite(pokemon_id: int):
    """Serve a local sprite when available, otherwise redirect to the official-artwork CDN."""
    local = SPRITES_REPO_DIR / f"{pokemon_id}.png"
    if local.exists():
        return FileResponse(local, media_type="image/png")

    return RedirectResponse(url=f"{SPRITES_CDN_BASE}/{pokemon_id}.png")


@app.get("/pokemon/resolve", response_model=PokemonLookupResponse)
def resolve_pokemon(name: str):
    result = lookup_pokemon(name)
    if not result:
        raise HTTPException(status_code=404, detail="Unknown pokemon")
    return result


@app.get("/pokemon/suggest", response_model=List[PokemonLookupResponse])
def suggest_pokemon(prefix: str, limit: int = 8):
    suggestions = engine.suggest_names_global(prefix, name_index, limit=max(1, min(limit, 20)))
    out: List[PokemonLookupResponse] = []
    for key, _display_name in suggestions:
        row = df_play.loc[df_play["key"] == key]
        if row.empty:
            continue
        r0 = row.iloc[0]
        pokemon_id = None
        if "pokemon_id" in r0 and pd.notna(r0["pokemon_id"]):
            pokemon_id = int(r0["pokemon_id"])
        out.append(
            PokemonLookupResponse(
                key=str(r0["key"]),
                display_name=str(r0["display_name"]),
                pokemon_id=pokemon_id,
            )
        )
    return out

@app.post("/validate", response_model=ValidatePickResponse)
def validate_pick_api(
    payload: ValidatePickRequest,
    x_client_id: str | None = Header(default=None, alias="X-Client-Id"),
):
    board = _get_or_generate_board(payload.seed, payload.n_rows, payload.n_cols)

    if board is None:
        raise HTTPException(status_code=500, detail="Could not regenerate board for validation")

    if payload.row_i < 0 or payload.row_i >= len(board.row_rule_ids):
        raise HTTPException(status_code=400, detail=f"row_i {payload.row_i} out of range")
    if payload.col_j < 0 or payload.col_j >= len(board.col_rule_ids):
        raise HTTPException(status_code=400, detail=f"col_j {payload.col_j} out of range")

    # Daily Board: the saved run decides what's allowed. A finished board (won,
    # out of lives, or ended to see the stats) takes no more answers, a square
    # keeps its first answer, and every saved answer counts as "used" even if
    # this browser lost track of it.
    used_keys = set(payload.used_keys)
    daily = bool(x_client_id) and is_daily_seed(payload.seed)
    run = None
    if daily:
        run = daily_store.get_run(x_client_id, payload.seed)
        lives_left = max(0, DAILY_LIVES - run["misses"])
        if run["finished"] or lives_left <= 0:
            return ValidatePickResponse(ok=False, reason="board_locked", lives_left=lives_left, finished=True)
        saved = {(p["row_i"], p["col_j"]): p["pokemon_key"] for p in run["picks"]}
        if (payload.row_i, payload.col_j) in saved:
            return ValidatePickResponse(ok=False, reason="already_filled", lives_left=lives_left)
        used_keys |= set(saved.values())

    res = engine.validate_pick(
        df=df_play,
        rule_masks=rule_masks,
        board=board,
        row_i=payload.row_i,
        col_j=payload.col_j,
        pokemon_key=payload.pokemon_key,
        used_keys=used_keys,
    )

    if daily and not res.ok:
        if res.reason in ("does_not_match", "duplicate"):
            misses = daily_store.add_miss(x_client_id, payload.seed)
            lives_left = max(0, DAILY_LIVES - misses)
            if lives_left <= 0:
                daily_store.finish(x_client_id, payload.seed)
            return ValidatePickResponse(ok=False, reason=res.reason, lives_left=lives_left,
                                        finished=lives_left <= 0)
        return ValidatePickResponse(ok=False, reason=res.reason,
                                    lives_left=max(0, DAILY_LIVES - run["misses"]))

    # Pokedex tracking: only correct picks, only on today's daily board, only
    # when the client sent an id. Shuffle boards (random-int seeds) never
    # reach this branch because is_daily_seed() checks against today's date,
    # which the client cannot spoof by relabeling a shuffle board as "daily".
    shiny = False
    shiny_registered = False
    if res.ok and x_client_id and is_daily_seed(payload.seed):
        row_rule_id = board.row_rule_ids[payload.row_i]
        col_rule_id = board.col_rule_ids[payload.col_j]
        row_label = compact_rule_label(rules[row_rule_id].label)
        col_label = compact_rule_label(rules[col_rule_id].label)

        # Shiny roll: once per cell per day (see pokedex_store.roll_once),
        # using this player's charms and streak (today counts).
        pokedex_store.record_daily_activity(x_client_id, payload.seed)
        rolls = shiny_rolls_for(
            len(pokedex_store.get_prestige(x_client_id)),
            pokedex_store.get_streak(x_client_id),
        )
        shiny = pokedex_store.roll_once(
            x_client_id, payload.seed, payload.row_i, payload.col_j,
            payload.pokemon_key, lambda: roll_shiny(rolls),
        )
        if shiny:
            pokedex_store.record_shiny(x_client_id, payload.pokemon_key, payload.seed)
            shiny_registered = True

        pokedex_store.record_catch(
            client_id=x_client_id,
            pokemon_key=payload.pokemon_key,
            board_seed=payload.seed,
            row_rule_label=row_label,
            col_rule_label=col_label,
            is_shiny=shiny,
        )
        # Recorded on every correct catch, not just the first — this is what
        # lets a Pokemon you already have keep building out a fuller list of
        # confirmed categories every time you reuse it on a later board.
        pokedex_store.record_rule_use(x_client_id, payload.pokemon_key, row_label)
        pokedex_store.record_rule_use(x_client_id, payload.pokemon_key, col_label)

        # Save the answer (first one per square wins), then report how many
        # players picked the same Pokemon in this exact square.
        daily_store.record_pick(
            x_client_id, payload.seed, payload.row_i, payload.col_j,
            row_rule_id, col_rule_id, payload.pokemon_key, hinted=False, shiny=shiny,
        )
        same, total = daily_store.cell_share(payload.seed, payload.row_i, payload.col_j, payload.pokemon_key)
        filled = len(run["picks"]) + 1
        done = filled >= payload.n_rows * payload.n_cols
        if done:
            daily_store.finish(x_client_id, payload.seed)
        return ValidatePickResponse(
            ok=True, reason=res.reason, shiny=shiny, shiny_registered=shiny_registered,
            lives_left=max(0, DAILY_LIVES - run["misses"]),
            pct=_pct(same, total), finished=done,
        )

    elif res.ok:
        # Draft and Shuffle boards: same odds (charms count; streaks don't,
        # since only the Daily builds one), but nothing is registered.
        charms = len(pokedex_store.get_prestige(x_client_id)) if x_client_id else 0
        shiny = roll_shiny(shiny_rolls_for(charms, 0))

    return ValidatePickResponse(ok=res.ok, reason=res.reason, shiny=shiny, shiny_registered=shiny_registered)


class PokedexEntry(BaseModel):
    key: str
    display_name: str
    pokemon_id: Optional[int] = None
    type1: Optional[str] = None
    type2: Optional[str] = None
    gen: Optional[int] = None
    evo_position: Optional[str] = None
    is_legendary: bool = False
    is_mythical: bool = False
    caught: bool = False
    is_shiny: bool = False
    first_caught: Optional[str] = None
    board_seed: Optional[str] = None
    row_rule_label: Optional[str] = None
    col_rule_label: Optional[str] = None
    shiny_first_seen: Optional[str] = None   # Shiny Dex date; survives a Pokédex restart


class PrestigeRecord(BaseModel):
    rank: int
    sealed_at: str
    caught_count: int


class TrainerInfo(BaseModel):
    charms: int                 # completed-and-restarted Pokédexes
    max_charms: int
    streak: int                 # consecutive Daily Boards played
    streak_bonus: int           # extra rolls from the streak right now
    next_streak_days: Optional[int] = None   # streak length that unlocks the next bonus
    rolls: int                  # total rolls per correct Daily pick
    odds: int                   # "1 in N" for a Daily pick
    base_odds: int              # 4096, for comparison in the UI
    shiny_count: int
    complete: bool              # every entry caught this run: can prestige
    history: List[PrestigeRecord] = []


class PokedexResponse(BaseModel):
    caught_count: int
    total_count: int
    entries: List[PokedexEntry]
    trainer: Optional[TrainerInfo] = None


class ClaimPokedexRequest(BaseModel):
    anonymous_client_id: str
    account_id: str


class ClaimPokedexResponse(BaseModel):
    merged_entries: int


# The species list never changes while the server runs, so the per-row parsing
# below happens once instead of on every Pokédex open (it was a full
# DataFrame.iterrows() over ~1,300 rows per request).
_POKEDEX_BASE: Optional[List[dict]] = None


def _pokedex_base() -> List[dict]:
    global _POKEDEX_BASE
    if _POKEDEX_BASE is None:
        base: List[dict] = []
        for _, r0 in df_play.iterrows():
            type1 = str(r0["type1"]) if "type1" in r0 and pd.notna(r0["type1"]) and str(r0["type1"]) else None
            type2_raw = str(r0["type2"]).strip() if "type2" in r0 else ""
            base.append({
                "key": str(r0["key"]),
                "display_name": str(r0["display_name"]),
                "pokemon_id": int(r0["pokemon_id"]) if "pokemon_id" in r0 and pd.notna(r0["pokemon_id"]) else None,
                "type1": type1,
                "type2": type2_raw if type2_raw else None,
                "gen": int(r0["gen"]) if "gen" in r0 and pd.notna(r0["gen"]) else None,
                "evo_position": str(r0["evo_position"]) if "evo_position" in r0 and pd.notna(r0["evo_position"]) and str(r0["evo_position"]) else None,
                "is_legendary": bool(r0["is_legendary"]) if "is_legendary" in r0 else False,
                "is_mythical": bool(r0["is_mythical"]) if "is_mythical" in r0 else False,
            })
        _POKEDEX_BASE = base
    return _POKEDEX_BASE


@app.get("/pokedex", response_model=PokedexResponse)
def get_pokedex(x_client_id: str | None = Header(default=None, alias="X-Client-Id")):
    if not x_client_id:
        raise HTTPException(status_code=400, detail="Missing X-Client-Id header")

    caught_details = pokedex_store.get_caught_details(x_client_id)
    shiny_map = pokedex_store.get_shiny_map(x_client_id)
    entries: List[PokedexEntry] = []
    for base in _pokedex_base():
        detail = caught_details.get(base["key"])
        shiny = shiny_map.get(base["key"])
        entries.append(PokedexEntry(
            **base,
            caught=detail is not None,
            is_shiny=shiny is not None,
            first_caught=detail["first_caught"] if detail else None,
            board_seed=detail["board_seed"] if detail else None,
            row_rule_label=detail["row_rule_label"] if detail else None,
            col_rule_label=detail["col_rule_label"] if detail else None,
            shiny_first_seen=shiny["first_seen"] if shiny else None,
        ))

    return PokedexResponse(
        caught_count=len(caught_details),
        total_count=len(df_play),
        entries=entries,
        trainer=_trainer_info(x_client_id, len(caught_details), len(shiny_map)),
    )


def _trainer_info(client_id: str, caught_count: int, shiny_count: int) -> TrainerInfo:
    history = pokedex_store.get_prestige(client_id)
    streak = pokedex_store.get_streak(client_id)
    rolls = shiny_rolls_for(len(history), streak)
    next_days = next((d for d, _ in reversed(STREAK_TIERS) if streak < d), None)
    return TrainerInfo(
        charms=len(history),
        max_charms=MAX_CHARMS,
        streak=streak,
        streak_bonus=streak_bonus(streak),
        next_streak_days=next_days,
        rolls=rolls,
        odds=shiny_odds(rolls),
        base_odds=SHINY_BASE,
        shiny_count=shiny_count,
        complete=len(df_play) > 0 and caught_count >= len(df_play),
        history=[PrestigeRecord(**h) for h in history],
    )


@app.post("/pokedex/prestige", response_model=TrainerInfo)
def prestige_pokedex(x_client_id: str | None = Header(default=None, alias="X-Client-Id")):
    """Complete Pokédex -> earn a Shiny Charm and start a fresh run.
    Rejected unless every entry is caught. The Shiny Dex is untouched."""
    if not x_client_id:
        raise HTTPException(status_code=400, detail="Missing X-Client-Id header")
    caught = len(pokedex_store.get_caught_details(x_client_id))
    if caught < len(df_play):
        raise HTTPException(status_code=400, detail=f"Pokédex isn't complete yet ({caught} / {len(df_play)})")
    pokedex_store.prestige(x_client_id, caught)
    return _trainer_info(x_client_id, 0, len(pokedex_store.get_shiny_map(x_client_id)))


def _parse_abilities(raw) -> List[str]:
    """The 'abilities' column is a JSON-ish list (single quotes), e.g.
    "['blaze','solar-power']". This is the FULL ability list (1-3 per
    Pokemon) — distinct from the ~16 has_ability_* board-rule flags, which
    only cover a curated subset used for board rules."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return []
    try:
        parsed = json.loads(str(raw).replace("'", '"'))
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(a).replace("-", " ").title() for a in parsed if a]


def _parse_raw_tokens(raw) -> List[str]:
    """Like _parse_abilities but returns the raw hyphenated tokens as-is
    (e.g. 'razor-leaf'), not title-cased — needed so we can look up whether
    a matching learns_X/has_ability_X column exists."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return []
    try:
        parsed = json.loads(str(raw).replace("'", '"'))
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(a) for a in parsed if a]


class DetailCategory(BaseModel):
    label: str
    lockable: bool   # False = informational only, can never be earned/confirmed
    confirmed: bool  # only meaningful when lockable is True


def _general_categories_for(pokemon_key: str, confirmed_set: set) -> List[DetailCategory]:
    """Coarse identity rules (type, region, evolution stage/method, and the
    legendary/mythical/baby/fossil/starter/etc. flags) — measured against the
    real dataset, these max out at 10 matches per Pokemon (median 6), so
    every one is shown, always lockable (they're all real board rules)."""
    row = df_play.loc[df_play["key"] == pokemon_key]
    if row.empty:
        return []
    idx = row.index[0]
    out: List[DetailCategory] = []
    for rid, rule in rules.items():
        if rid.startswith(("move_", "ability_")):
            continue
        mask = rule_masks.get(rid)
        if mask is None or idx not in mask.index:
            continue
        if bool(mask.loc[idx]):
            label = compact_rule_label(rule.label)
            if label:
                out.append(DetailCategory(label=label, lockable=True, confirmed=label.lower() in confirmed_set))
    return out


def _trait_categories_for(tokens: List[str], rule_prefix: str, confirmed_set: set) -> List[DetailCategory]:
    """Shared logic for both abilities and moves. For each real trait a
    Pokemon actually has (a raw ability or move token), check whether it
    corresponds to an actual board-testable rule (has_ability_X / learns_X):
    - If yes: lockable, shown blurred until confirmed via a real catch.
    - If no such rule exists at all (most moves; many abilities): there's no
      way to ever earn it through play, so it's shown plainly, always.
    rule_prefix is "move" or "ability" — matches the rid prefixes engine.py
    uses (move_<token>, ability_<token>).
    """
    out: List[DetailCategory] = []
    seen = set()
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        rid = f"{rule_prefix}_{token.replace('-', '_')}"
        rule = rules.get(rid)
        if rule is not None:
            # Reuse the exact label the board itself would show, so text
            # matches confirmed_categories reliably (same source of truth).
            label = compact_rule_label(rule.label)
            out.append(DetailCategory(label=label, lockable=True, confirmed=label.lower() in confirmed_set))
        else:
            display = token.replace("-", " ").title()
            fallback_label = f"Learns {display}" if rule_prefix == "move" else f"Ability: {display}"
            out.append(DetailCategory(label=fallback_label, lockable=False, confirmed=False))
    return out


def _sort_categories(cats: List[DetailCategory]) -> List[DetailCategory]:
    """Confirmed first (celebrate progress), then still-locked, then
    informational-only last (least "actionable"), alphabetical within
    each group."""
    def key(c: DetailCategory):
        group = 0 if (c.lockable and c.confirmed) else (1 if c.lockable else 2)
        return (group, c.label.lower())
    return sorted(cats, key=key)


class PokedexDetailResponse(BaseModel):
    key: str
    display_name: str
    pokemon_id: Optional[int] = None
    type1: Optional[str] = None
    type2: Optional[str] = None
    is_shiny: bool = False
    first_caught: Optional[str] = None
    board_seed: Optional[str] = None
    row_rule_label: Optional[str] = None
    col_rule_label: Optional[str] = None
    general_categories: List[DetailCategory] = []
    ability_categories: List[DetailCategory] = []
    move_categories: List[DetailCategory] = []


@app.get("/pokedex/{key}", response_model=PokedexDetailResponse)
def get_pokedex_detail(key: str, x_client_id: str | None = Header(default=None, alias="X-Client-Id")):
    if not x_client_id:
        raise HTTPException(status_code=400, detail="Missing X-Client-Id header")

    row = df_play.loc[df_play["key"] == key]
    if row.empty:
        raise HTTPException(status_code=404, detail="Unknown pokemon")

    caught_details = pokedex_store.get_caught_details(x_client_id)
    detail = caught_details.get(key)
    shiny = pokedex_store.get_shiny_map(x_client_id).get(key)
    if detail is None and shiny is None:
        # Keep the silhouette mystery intact: don't leak name/details for
        # something this client hasn't actually caught yet. A Shiny Dex
        # entry counts as known even after a Pokédex restart.
        raise HTTPException(status_code=404, detail="Not caught yet")
    if detail is None:
        detail = {"first_caught": shiny["first_seen"], "board_seed": shiny["board_seed"],
                  "row_rule_label": None, "col_rule_label": None}

    r0 = row.iloc[0]
    pokemon_id = int(r0["pokemon_id"]) if "pokemon_id" in r0 and pd.notna(r0["pokemon_id"]) else None
    type1 = str(r0["type1"]) if "type1" in r0 and pd.notna(r0["type1"]) and str(r0["type1"]) else None
    type2_raw = str(r0["type2"]).strip() if "type2" in r0 else ""
    type2 = type2_raw if type2_raw else None

    rule_uses = pokedex_store.get_rule_uses(x_client_id)
    confirmed_set = {c.strip().lower() for c in rule_uses.get(key, [])}

    ability_tokens = _parse_raw_tokens(r0.get("abilities")) if "abilities" in r0 else []
    move_tokens = _parse_raw_tokens(r0.get("moves")) if "moves" in r0 else []

    return PokedexDetailResponse(
        key=key,
        display_name=str(r0["display_name"]),
        pokemon_id=pokemon_id,
        type1=type1,
        type2=type2,
        is_shiny=shiny is not None,
        first_caught=detail["first_caught"],
        board_seed=detail["board_seed"],
        row_rule_label=detail["row_rule_label"],
        col_rule_label=detail["col_rule_label"],
        general_categories=_sort_categories(_general_categories_for(key, confirmed_set)),
        ability_categories=_sort_categories(_trait_categories_for(ability_tokens, "ability", confirmed_set)),
        move_categories=_sort_categories(_trait_categories_for(move_tokens, "move", confirmed_set)),
    )


@app.post("/pokedex/claim", response_model=ClaimPokedexResponse)
def claim_pokedex(payload: ClaimPokedexRequest):
    """Call this once, at signup/login time, to move an anonymous device's
    Pokedex progress onto a real account id. Safe to call even if the
    anonymous id has no entries (returns merged_entries=0)."""
    moved = pokedex_store.merge_client(payload.anonymous_client_id, payload.account_id)
    daily_store.merge_client(payload.anonymous_client_id, payload.account_id)
    return ClaimPokedexResponse(merged_entries=moved)


@app.get("/hint")
def get_hint(seed: str, n: int = 9, n_rows: int | None = None, n_cols: int | None = None,
             row_i: int = 0, col_j: int = 0, used_keys: str = "",
             x_client_id: str | None = Header(default=None, alias="X-Client-Id")):
    row_count = n_rows if n_rows is not None else n
    col_count = n_cols if n_cols is not None else n
    board = _get_or_generate_board(seed, row_count, col_count)
    if board is None:
        raise HTTPException(status_code=500, detail="Could not regenerate board")

    if row_i < 0 or row_i >= len(board.row_rule_ids):
        raise HTTPException(status_code=400, detail="row_i out of range")
    if col_j < 0 or col_j >= len(board.col_rule_ids):
        raise HTTPException(status_code=400, detail="col_j out of range")

    used = set(k for k in used_keys.split(",") if k) if used_keys else set()

    # Daily Board: hints are counted on the server like lives, and a hinted
    # square is saved so it comes back after a refresh (it never counts
    # toward the answer stats).
    daily = bool(x_client_id) and is_daily_seed(seed)
    run = None
    if daily:
        run = daily_store.get_run(x_client_id, seed)
        if run["finished"] or run["misses"] >= DAILY_LIVES:
            raise HTTPException(status_code=409, detail="Today's board is finished.")
        if run["hints_used"] >= DAILY_HINTS:
            raise HTTPException(status_code=409, detail="No hints left today.")
        saved = {(p["row_i"], p["col_j"]): p["pokemon_key"] for p in run["picks"]}
        if (row_i, col_j) in saved:
            raise HTTPException(status_code=409, detail="That square is already filled.")
        used |= set(saved.values())

    rr = board.row_rule_ids[row_i]
    cc = board.col_rule_ids[col_j]
    candidates = engine.candidates_for_cell(df_play, rule_masks, rr, cc)
    candidates = candidates[~candidates["key"].isin(used)].copy()

    if candidates.empty:
        raise HTTPException(status_code=404, detail="No valid answer available for this cell")

    row = candidates.sample(1).iloc[0]
    pokemon_id = None
    if "pokemon_id" in row.index and pd.notna(row["pokemon_id"]):
        pokemon_id = int(row["pokemon_id"])

    out = {
        "key": str(row["key"]),
        "display_name": str(row["display_name"]),
        "pokemon_id": pokemon_id,
    }
    if daily:
        daily_store.record_pick(x_client_id, seed, row_i, col_j, rr, cc, out["key"], hinted=True)
        filled = len(run["picks"]) + 1
        if filled >= row_count * col_count:
            daily_store.finish(x_client_id, seed)
        out["hints_left"] = max(0, DAILY_HINTS - run["hints_used"] - 1)
        out["finished"] = filled >= row_count * col_count
    return out

# ── Daily Board: saved progress + answer stats ──────────────────────────
# GET  /daily/state   the player's saved Daily (picks, lives, hints) to restore
# POST /daily/finish  end the board early to see the stats
# GET  /daily/stats   per-square answer stats; only once the player's board is
#                     finished, so the most-picked answers can't be used to
#                     solve squares that are still open.
#
# A square's % = players who answered that Pokemon in that square divided by
# players who filled that square. Hinted squares never count.

def _pct(count: int, total: int) -> Optional[float]:
    return round(100.0 * count / total, 1) if total else None


_mon_info_cache: dict = {}

def _mon_info(key: str) -> dict:
    if not _mon_info_cache:
        for r in df_play[["key", "display_name", "pokemon_id"]].itertuples(index=False):
            _mon_info_cache[str(r.key)] = {
                "display_name": str(r.display_name),
                "pokemon_id": int(r.pokemon_id) if pd.notna(r.pokemon_id) else None,
            }
    return _mon_info_cache.get(key, {"display_name": key, "pokemon_id": None})


class DailyPick(BaseModel):
    row_i: int                      # canonical (server) indices, like /validate
    col_j: int
    key: str
    display_name: str
    pokemon_id: Optional[int] = None
    hinted: bool = False
    shiny: bool = False
    pct: Optional[float] = None


class DailyStateResponse(BaseModel):
    seed: str
    lives_left: int
    hints_left: int
    finished: bool
    picks: List[DailyPick]


class StatPick(BaseModel):
    key: str
    display_name: str
    pokemon_id: Optional[int] = None
    count: int
    pct: float


class CellStats(BaseModel):
    row_i: int
    col_j: int
    total: int                      # players who filled this square (hints excluded)
    top: List[StatPick]             # most picked first, up to 5
    rarest: Optional[StatPick] = None
    mine: Optional[DailyPick] = None


class DailyStatsResponse(BaseModel):
    seed: str
    players: int                    # players with at least one counted answer today
    avg_pct: Optional[float] = None # the player's average % over their counted squares
    counted: int = 0
    cells: List[CellStats]


def _stat_pick(key: str, count: int, total: int) -> StatPick:
    info = _mon_info(key)
    return StatPick(key=key, display_name=info["display_name"], pokemon_id=info["pokemon_id"],
                    count=count, pct=_pct(count, total) or 0.0)


def _counts_with_mine(seed: str, picks: List[dict]) -> dict:
    """Board counts from the short cache, refreshed if they don't yet include
    this player's own answers (so nobody ever sees 0% on their own pick)."""
    counts = daily_store.board_counts(seed)
    for p in picks:
        if not p["hinted"] and counts["cells"].get((p["row_i"], p["col_j"]), {}).get(p["pokemon_key"], 0) == 0:
            return daily_store.board_counts(seed, fresh=True)
    return counts


def _daily_pick_out(p: dict, counts: dict) -> DailyPick:
    info = _mon_info(p["pokemon_key"])
    pct = None
    if not p["hinted"]:
        cell = counts["cells"].get((p["row_i"], p["col_j"]), {})
        pct = _pct(cell.get(p["pokemon_key"], 0), sum(cell.values()))
    return DailyPick(row_i=p["row_i"], col_j=p["col_j"], key=p["pokemon_key"],
                     display_name=info["display_name"], pokemon_id=info["pokemon_id"],
                     hinted=p["hinted"], shiny=p["shiny"], pct=pct)


@app.get("/daily/state", response_model=DailyStateResponse)
def daily_state(x_client_id: str | None = Header(default=None, alias="X-Client-Id")):
    seed = str(date.today())
    run = daily_store.get_run(x_client_id, seed)
    counts = _counts_with_mine(seed, run["picks"]) if run["picks"] else {"cells": {}}
    lives_left = max(0, DAILY_LIVES - run["misses"])
    return DailyStateResponse(
        seed=seed,
        lives_left=lives_left,
        hints_left=max(0, DAILY_HINTS - run["hints_used"]),
        finished=run["finished"] or lives_left <= 0,
        picks=[_daily_pick_out(p, counts) for p in run["picks"]],
    )


def _daily_stats(client_id: str, seed: str) -> DailyStatsResponse:
    run = daily_store.get_run(client_id, seed)
    counts = _counts_with_mine(seed, run["picks"])
    mine = {(p["row_i"], p["col_j"]): p for p in run["picks"]}
    cells_out: List[CellStats] = []
    my_pcts: List[float] = []
    for i in range(N):
        for j in range(N):
            cell = counts["cells"].get((i, j), {})
            total = sum(cell.values())
            ranked = sorted(cell.items(), key=lambda kv: (-kv[1], _mon_info(kv[0])["display_name"]))
            rare = min(cell.items(), key=lambda kv: (kv[1], _mon_info(kv[0])["display_name"])) if cell else None
            my = _daily_pick_out(mine[(i, j)], counts) if (i, j) in mine else None
            if my and my.pct is not None:
                my_pcts.append(my.pct)
            cells_out.append(CellStats(
                row_i=i, col_j=j, total=total,
                top=[_stat_pick(k, n, total) for k, n in ranked[:5]],
                rarest=_stat_pick(rare[0], rare[1], total) if rare else None,
                mine=my,
            ))
    return DailyStatsResponse(
        seed=seed, players=counts["players"],
        avg_pct=round(sum(my_pcts) / len(my_pcts), 1) if my_pcts else None,
        counted=len(my_pcts), cells=cells_out,
    )


@app.get("/daily/stats", response_model=DailyStatsResponse)
def daily_stats(x_client_id: str | None = Header(default=None, alias="X-Client-Id")):
    seed = str(date.today())
    run = daily_store.get_run(x_client_id, seed)
    if not (run["finished"] or run["misses"] >= DAILY_LIVES):
        raise HTTPException(status_code=403, detail="Finish today's board to see everyone's answers.")
    return _daily_stats(x_client_id, seed)


@app.post("/daily/finish", response_model=DailyStatsResponse)
def daily_finish(x_client_id: str | None = Header(default=None, alias="X-Client-Id")):
    if not x_client_id:
        raise HTTPException(status_code=400, detail="Missing client id.")
    seed = str(date.today())
    daily_store.finish(x_client_id, seed)
    return _daily_stats(x_client_id, seed)


@app.get("/search-test")
def search_test(q: str = "piccachu"):
    """Diagnostic: verify fuzzy search is loaded. Visit /search-test?q=piccachu"""
    results = engine.suggest_names_global(q, name_index, limit=5)
    return {
        "query": q,
        "results": [{"key": k, "display_name": d} for k, d in results],
        "fuzzy_enabled": hasattr(engine, "_edit_distance"),
    }

from fastapi.staticfiles import StaticFiles
# sprites_repo is a local-only download (it isn't in git), so only mount it
# when it exists; otherwise the server would crash on a fresh deploy. Sprites
# fall back to the CDN either way (see /sprite/{id}.png). Must come before
# the catch-all frontend mount.
if (PROJECT_ROOT / "sprites_repo").is_dir():
    app.mount("/sprites_repo", StaticFiles(directory=str(PROJECT_ROOT / "sprites_repo")), name="sprites_repo")
app.mount("/", StaticFiles(directory=str(PROJECT_ROOT / "frontend"), html=True), name="frontend")