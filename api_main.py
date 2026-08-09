from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel
from typing import List, Optional
import pandas as pd
import json
import uuid
from pathlib import Path
from datetime import date

import engine
import pokedex_store

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
    n: int
    row_i: int
    col_j: int
    pokemon_key: str
    used_keys: List[str] = []

class ValidatePickResponse(BaseModel):
    ok: bool
    reason: str


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

    # Dataset quirk
    if out in ("Paldea + DLC",):
        return "Paldea"

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
        "Evolves By: Level-up":   "Evolves by Level Up",
        "Evolves By: Level-Up":   "Evolves by Level Up",
        "Evolves By: Trade":      "Evolves by Trade",
        "Evolves By: Friendship": "Evolves by Friendship",
        "Evolves By: Item":       "Evolves by Item",
        "Evolves: Level Up":      "Evolves by Level Up",
        "Evolves: Trading":       "Evolves by Trade",
        "Evolves: Friendship":    "Evolves by Friendship",
        "Evolves: Item":          "Evolves by Item",
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

def is_daily_seed(seed: str) -> bool:
    """A board only counts toward the Pokedex if its seed is today's canonical
    daily seed — i.e. the same value /board falls back to when no seed is given.
    Shuffle boards use engine.py's randomSeed()-style large ints and will never
    match this, so this check can't be spoofed by a client claiming 'daily'."""
    return seed == str(date.today())

def serialize_board(board):
    return {
        "seed": board.seed,
        "n": board.n,
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

def _get_or_generate_board(seed: str, n: int):
    key = f"{seed}:{n}"
    with _cache_lock:
        if key in _board_cache:
            return _board_cache[key]

    draft_cfg = _draft_configs.get(seed)
    if draft_cfg is not None:
        board = engine.generate_board(
            df=df_play, rules=rules, rule_masks=rule_masks,
            seed=seed, n=n, constraints=draft_cfg["constraints"],
            allowed_rule_ids=draft_cfg["allowed_rule_ids"],
        )
    else:
        board = engine.generate_board(
            df=df_play, rules=rules, rule_masks=rule_masks,
            seed=seed, n=n, constraints=DEFAULT_CONSTRAINTS,
        )
    with _cache_lock:
        if len(_board_cache) >= _CACHE_MAX:
            del _board_cache[next(iter(_board_cache))]
        _board_cache[key] = board
    return board

@app.get("/board")
async def get_board(seed: str | None = None, n: int = 9):
    board_seed = seed or str(date.today())
    loop = asyncio.get_event_loop()
    try:
        board = await asyncio.wait_for(
            loop.run_in_executor(
                _board_executor,
                lambda: _get_or_generate_board(board_seed, n)
            ),
            timeout=25.0
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=503, detail="Board generation timed out — try shuffling")
    if board is None:
        raise HTTPException(status_code=500, detail="Could not generate board")
    return serialize_board(board)


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
    n: int = 9
    allow_single_answers: bool = True
    guaranteed_solvable: bool = True


class DraftBoardResponse(BaseModel):
    seed: str
    n: int
    row_rule_labels: List[str]
    col_rule_labels: List[str]
    adjusted_n: Optional[int] = None  # set only if the requested size wasn't feasible and we shrank it
    note: Optional[str] = None


def _draft_constraints(allow_single_answers: bool, guaranteed_solvable: bool) -> engine.BoardConstraints:
    return engine.BoardConstraints(
        min_candidates_per_cell=1 if allow_single_answers else 2,
        max_candidates_per_cell=None,
        max_tries=_DRAFT_MAX_TRIES,
        require_distinct_solution=guaranteed_solvable,
    )


def _attempt_draft_board(size: int, allowed: set, constraints: engine.BoardConstraints):
    seed = f"draft-{uuid.uuid4().hex}"
    board = engine.generate_board(
        df=df_play, rules=rules, rule_masks=rule_masks,
        seed=seed, n=size, constraints=constraints, allowed_rule_ids=allowed,
    )
    return board, seed


@app.post("/board/draft", response_model=DraftBoardResponse)
async def create_draft_board(payload: DraftBoardRequest):
    allowed = {rid for rid in payload.allowed_rule_ids if rid in rules}
    if len(allowed) < 2:
        raise HTTPException(status_code=422, detail="Select at least a couple of categories to build a board from.")

    requested_n = max(3, min(9, payload.n))
    constraints = _draft_constraints(payload.allow_single_answers, payload.guaranteed_solvable)
    loop = asyncio.get_event_loop()

    async def _try(size: int):
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(_board_executor, lambda: _attempt_draft_board(size, allowed, constraints)),
                timeout=_DRAFT_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            return None, None

    board, seed = await _try(requested_n)

    adjusted_n = None
    note = None
    if board is None and requested_n > 4:
        fallback_n = max(4, requested_n - 3)
        board, seed = await _try(fallback_n)
        if board is not None:
            adjusted_n = fallback_n
            note = (
                f"A {requested_n}\u00d7{requested_n} board wasn't feasible with these categories, "
                f"so this is a {fallback_n}\u00d7{fallback_n} board instead. Enabling more categories "
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
        _board_cache[f"{seed}:{board.n}"] = board

    resp = serialize_board(board)
    return DraftBoardResponse(
        seed=resp["seed"], n=resp["n"],
        row_rule_labels=resp["row_rule_labels"], col_rule_labels=resp["col_rule_labels"],
        adjusted_n=adjusted_n, note=note,
    )


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
    board = _get_or_generate_board(payload.seed, payload.n)

    if board is None:
        raise HTTPException(status_code=500, detail="Could not regenerate board for validation")

    if payload.row_i < 0 or payload.row_i >= len(board.row_rule_ids):
        raise HTTPException(status_code=400, detail=f"row_i {payload.row_i} out of range")
    if payload.col_j < 0 or payload.col_j >= len(board.col_rule_ids):
        raise HTTPException(status_code=400, detail=f"col_j {payload.col_j} out of range")

    res = engine.validate_pick(
        df=df_play,
        rule_masks=rule_masks,
        board=board,
        row_i=payload.row_i,
        col_j=payload.col_j,
        pokemon_key=payload.pokemon_key,
        used_keys=set(payload.used_keys),
    )

    # Pokedex tracking: only correct picks, only on today's daily board, only
    # when the client sent an id. Shuffle boards (random-int seeds) never
    # reach this branch because is_daily_seed() checks against today's date,
    # which the client cannot spoof by relabeling a shuffle board as "daily".
    if res.ok and x_client_id and is_daily_seed(payload.seed):
        row_rule_id = board.row_rule_ids[payload.row_i]
        col_rule_id = board.col_rule_ids[payload.col_j]
        row_label = compact_rule_label(rules[row_rule_id].label)
        col_label = compact_rule_label(rules[col_rule_id].label)

        pokedex_store.record_catch(
            client_id=x_client_id,
            pokemon_key=payload.pokemon_key,
            board_seed=payload.seed,
            row_rule_label=row_label,
            col_rule_label=col_label,
        )
        # Recorded on every correct catch, not just the first — this is what
        # lets a Pokemon you already have keep building out a fuller list of
        # confirmed categories every time you reuse it on a later board.
        pokedex_store.record_rule_use(x_client_id, payload.pokemon_key, row_label)
        pokedex_store.record_rule_use(x_client_id, payload.pokemon_key, col_label)

    return ValidatePickResponse(ok=res.ok, reason=res.reason)


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


class PokedexResponse(BaseModel):
    caught_count: int
    total_count: int
    entries: List[PokedexEntry]


class ClaimPokedexRequest(BaseModel):
    anonymous_client_id: str
    account_id: str


class ClaimPokedexResponse(BaseModel):
    merged_entries: int


@app.get("/pokedex", response_model=PokedexResponse)
def get_pokedex(x_client_id: str | None = Header(default=None, alias="X-Client-Id")):
    if not x_client_id:
        raise HTTPException(status_code=400, detail="Missing X-Client-Id header")

    caught_details = pokedex_store.get_caught_details(x_client_id)
    entries: List[PokedexEntry] = []
    for _, r0 in df_play.iterrows():
        key = str(r0["key"])
        pokemon_id = int(r0["pokemon_id"]) if "pokemon_id" in r0 and pd.notna(r0["pokemon_id"]) else None
        gen = int(r0["gen"]) if "gen" in r0 and pd.notna(r0["gen"]) else None
        type1 = str(r0["type1"]) if "type1" in r0 and pd.notna(r0["type1"]) and str(r0["type1"]) else None
        type2_raw = str(r0["type2"]).strip() if "type2" in r0 else ""
        type2 = type2_raw if type2_raw else None
        evo_position = str(r0["evo_position"]) if "evo_position" in r0 and pd.notna(r0["evo_position"]) and str(r0["evo_position"]) else None

        detail = caught_details.get(key)
        entries.append(PokedexEntry(
            key=key,
            display_name=str(r0["display_name"]),
            pokemon_id=pokemon_id,
            type1=type1,
            type2=type2,
            gen=gen,
            evo_position=evo_position,
            is_legendary=bool(r0["is_legendary"]) if "is_legendary" in r0 else False,
            is_mythical=bool(r0["is_mythical"]) if "is_mythical" in r0 else False,
            caught=detail is not None,
            is_shiny=detail["is_shiny"] if detail else False,
            first_caught=detail["first_caught"] if detail else None,
            board_seed=detail["board_seed"] if detail else None,
            row_rule_label=detail["row_rule_label"] if detail else None,
            col_rule_label=detail["col_rule_label"] if detail else None,
        ))

    return PokedexResponse(
        caught_count=len(caught_details),
        total_count=len(df_play),
        entries=entries,
    )


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
    if detail is None:
        # Keep the silhouette mystery intact: don't leak name/details for
        # something this client hasn't actually caught yet.
        raise HTTPException(status_code=404, detail="Not caught yet")

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
        is_shiny=detail["is_shiny"],
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
    return ClaimPokedexResponse(merged_entries=moved)


@app.get("/hint")
def get_hint(seed: str, n: int = 9, row_i: int = 0, col_j: int = 0, used_keys: str = ""):
    board = _get_or_generate_board(seed, n)
    if board is None:
        raise HTTPException(status_code=500, detail="Could not regenerate board")

    if row_i < 0 or row_i >= len(board.row_rule_ids):
        raise HTTPException(status_code=400, detail="row_i out of range")
    if col_j < 0 or col_j >= len(board.col_rule_ids):
        raise HTTPException(status_code=400, detail="col_j out of range")

    used = set(k for k in used_keys.split(",") if k) if used_keys else set()
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

    return {
        "key": str(row["key"]),
        "display_name": str(row["display_name"]),
        "pokemon_id": pokemon_id,
    }

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
# Serve sprites_repo BEFORE the catch-all frontend mount so type badge paths resolve.
app.mount("/sprites_repo", StaticFiles(directory="sprites_repo"), name="sprites_repo")
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")