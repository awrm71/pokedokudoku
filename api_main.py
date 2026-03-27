from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import pandas as pd
from pathlib import Path
from datetime import date

import engine

app = FastAPI()

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
    max_candidates_per_cell=1050,
    max_tries=5000,
)

def compact_rule_label(label: str) -> str:
    label = label.replace("Type: ", "")
    label = label.replace("Gen: ", "G")
    label = label.replace("Legendary", "Leg")
    label = label.replace("Mythical", "Myth")
    return label

def load_data() -> pd.DataFrame:
    data_dir = Path("data")
    pq = data_dir / "pokedex.parquet"
    csv = data_dir / "pokedex.csv"

    if pq.exists():
        df = pd.read_parquet(pq)
    elif csv.exists():
        df = pd.read_csv(csv)
    else:
        raise FileNotFoundError("No pokedex.parquet or pokedex.csv found in /data")

    if "type2" not in df.columns:
        df["type2"] = ""
    df["type2"] = df["type2"].fillna("")

    for col in ["is_legendary", "is_mythical", "is_baby"]:
        if col in df.columns:
            df[col] = (
                df[col].astype(str).str.lower().map({"true": True, "false": False}).fillna(False)
            )
        else:
            df[col] = False

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

@app.get("/board")
def get_board(seed: str | None = None, n: int = 9):
    board_seed = seed or str(date.today())

    board = engine.generate_board(
        df=df_play,
        rules=rules,
        rule_masks=rule_masks,
        seed=board_seed,
        n=n,
        constraints=DEFAULT_CONSTRAINTS,
    )

    if board is None:
        raise HTTPException(status_code=500, detail="Could not generate board")

    return serialize_board(board)


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
def validate_pick_api(payload: ValidatePickRequest):
    board = engine.generate_board(
        df=df_play,
        rules=rules,
        rule_masks=rule_masks,
        seed=payload.seed,
        n=payload.n,
        constraints=DEFAULT_CONSTRAINTS,
    )

    if board is None:
        raise HTTPException(status_code=500, detail="Could not regenerate board for validation")

    if payload.row_i < 0 or payload.row_i >= len(board.row_rule_ids):
        raise HTTPException(
            status_code=400,
            detail=f"row_i {payload.row_i} out of range for board with {len(board.row_rule_ids)} rows"
        )

    if payload.col_j < 0 or payload.col_j >= len(board.col_rule_ids):
        raise HTTPException(
            status_code=400,
            detail=f"col_j {payload.col_j} out of range for board with {len(board.col_rule_ids)} cols"
        )

    res = engine.validate_pick(
        df=df_play,
        rule_masks=rule_masks,
        board=board,
        row_i=payload.row_i,
        col_j=payload.col_j,
        pokemon_key=payload.pokemon_key,
        used_keys=set(payload.used_keys) - {payload.pokemon_key},
    )

    return ValidatePickResponse(ok=res.ok, reason=res.reason)
