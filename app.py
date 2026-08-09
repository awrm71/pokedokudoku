import pandas as pd
import streamlit as st
from pathlib import Path
import random
from datetime import date

import engine

st.set_page_config(layout="wide")
# -------------------------
# Config
# -------------------------
N = 9  # later: 9

DEFAULT_CONSTRAINTS = engine.BoardConstraints(
    min_candidates_per_cell=1,      # bump from 1 so boards aren't trivial; adjust as desired
    max_candidates_per_cell=None,    # no cap on broad intersections (improves generation reliability)
    max_tries=5000,
    require_distinct_solution=True,
)

SPRITES_REPO_DIR = Path("sprites_repo") / "sprites" / "pokemon"
SPRITES_DIR = Path("sprites")
SPRITES_CDN_BASE = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/other/official-artwork"
PLACEHOLDER_SPRITE = "https://placehold.co/96x96?text=%3F"

def compact_rule_label(label: str) -> str:
    label = label.replace("Type: ", "")
    label = label.replace("Gen: ", "G")
    label = label.replace("Legendary", "Leg")
    label = label.replace("Mythical", "Myth")
    return label

# -------------------------
# Data
# -------------------------
@st.cache_data
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

    # Normalize required columns
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

    for col in [c for c in df.columns if str(c).startswith(("is_", "has_", "evolves_by_", "learns_", "has_ability_"))]:
        _coerce_bool_col(str(col))

    if "gen" in df.columns:
        df["gen"] = pd.to_numeric(df["gen"], errors="coerce").astype("Int64")

    if "pokemon_id" in df.columns:
        df["pokemon_id"] = pd.to_numeric(df["pokemon_id"], errors="coerce").astype("Int64")

    # Basic sanity
    required = ["key", "display_name", "type1", "type2", "gen"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns in pokedex: {missing}")

    return df


df = load_data()

# Apply answer policy (forms/variants later live here)
df_play = engine.apply_answer_policy(df, policy="species_only")

# Engine objects (single source of truth)
rules = engine.build_default_rules(df_play)
rule_masks = engine.compute_rule_masks(df_play, rules)
name_index = engine.build_name_index(df_play)


# -------------------------
# Sprites
# -------------------------
def sprite_path(row: pd.Series) -> str:
    # Prefer numeric ID if available
    if "pokemon_id" in row and pd.notna(row["pokemon_id"]):
        pid = int(row["pokemon_id"])
        for base in [SPRITES_DIR, SPRITES_REPO_DIR]:
            p = base / f"{pid}.png"
            if p.exists():
                return str(p)

        # No local file available: use CDN sprite so the app works on lightweight/dev machines.
        return f"{SPRITES_CDN_BASE}/{pid}.png"

    # Fallback: key-based sprite (if you have it)
    if "key" in row and pd.notna(row["key"]):
        p = SPRITES_DIR / f"{row['key']}.png"
        if p.exists():
            return str(p)

    return PLACEHOLDER_SPRITE


# -------------------------
# Board / session helpers
# -------------------------
def clear_cell_state(n: int):
    for i in range(n):
        for j in range(n):
            base = f"cell_{i}_{j}"
            st.session_state.pop(base, None)
            st.session_state.pop(f"{base}_typed", None)
            st.session_state.pop(f"{base}_typed_pending", None)
            st.session_state.pop(f"{base}_submitted", None)
            st.session_state.pop(f"{base}_result", None)

def clear_one_cell(i: int, j: int):
    cell_key = f"cell_{i}_{j}"
    typed_key = f"{cell_key}_typed"
    pending_key = f"{typed_key}_pending"
    submitted_key = f"{cell_key}_submitted"
    result_key = f"{cell_key}_result"

    st.session_state[cell_key] = ""
    st.session_state[pending_key] = None
    st.session_state[submitted_key] = False
    st.session_state[result_key] = None
    # Do not directly overwrite the active text_input key after instantiation.
    # Instead, remove it so the next rerun recreates it empty.
    st.session_state.pop(typed_key, None)

def ensure_board(seed: str):
    board = engine.generate_board(
        df=df_play,
        rules=rules,
        rule_masks=rule_masks,
        seed=seed,
        n=N,
        constraints=DEFAULT_CONSTRAINTS,
    )
    if board is None:
        st.error("Could not generate a valid board with current constraints. Try lowering min_candidates_per_cell.")
        st.stop()
    st.session_state["board"] = board


if "board" not in st.session_state:
    ensure_board(seed=str(date.today()))

board: engine.Board = st.session_state["board"]


# -------------------------
# UI
# -------------------------

st.title(f"Local PokeDoku ({N}×{N})")

with st.sidebar.expander("Debug: sprites", expanded=False):
    st.write("Working dir:", str(Path(".").resolve()))
    st.write("sprites exists:", SPRITES_DIR.exists())
    st.write("example sprites:", [p.name for p in SPRITES_DIR.glob("*.png")][:10])
    st.write("columns:", list(df_play.columns))
    st.write("sample row:", df_play.iloc[0].to_dict())

with st.sidebar:
    st.header("Board Setup")

    if st.button("Clear picks only"):
        clear_cell_state(N)
        st.rerun()

    if st.button("Today (daily board)"):
        clear_cell_state(N)
        ensure_board(seed=str(date.today()))
        st.rerun()

    if st.button("New random board"):
        clear_cell_state(N)
        ensure_board(seed=str(random.randint(1, 10**9)))
        st.rerun()

    st.caption(f"Seed: {board.seed}  |  Size: {board.n}×{board.n}")


# Board rule ids + labels
row_rule_ids = board.row_rule_ids
col_rule_ids = board.col_rule_ids
row_rules = [compact_rule_label(rules[rid].label) for rid in row_rule_ids]
col_rules = [compact_rule_label(rules[cid].label) for cid in col_rule_ids]


# Track picks
used = set()
grid_picks: dict[tuple[int, int], str] = {}


# Header row
header_cols = st.columns([1.6] + [1] * N)
header_cols[0].markdown("")
for j in range(N):
    header_cols[j + 1].markdown(
        f"<div style='text-align:center; font-weight:700; font-size:20px;'>{col_rules[j]}</div>",
        unsafe_allow_html=True
    )

st.divider()


for i in range(N):
    row = st.columns([1.6] + [1] * N)
    row[0].markdown(
    f"<div style='font-weight:700; font-size:20px; line-height:1.2;'>{row_rules[i]}</div>",
    unsafe_allow_html=True
    )   

    for j in range(N):
        cell = row[j + 1]
        cell_key = f"cell_{i}_{j}"

        # Candidate set (used only for validation; NOT shown)
        _ = engine.candidates_for_cell(df_play, rule_masks, row_rule_ids[i], col_rule_ids[j])

        current_key = st.session_state.get(cell_key, "")
        typed_key = f"{cell_key}_typed"
        pending_key = f"{typed_key}_pending"

        # Apply pending set (chosen suggestion -> textbox)
        if st.session_state.get(pending_key, None) is not None:
            st.session_state[typed_key] = st.session_state[pending_key]
            st.session_state[pending_key] = None

        submitted_key = f"{cell_key}_submitted"
        result_key = f"{cell_key}_result"

        locked = bool(st.session_state.get(submitted_key, False))

        typed = cell.text_input(
            label=f"({i+1},{j+1})",
            key=typed_key,
            label_visibility="collapsed",
            placeholder="Type a Pokémon name…",
            disabled=locked,
        
        )

        # Hide suggestions if typed matches selected name
        selected_name = ""
        if current_key:
            sugs = []
            selected_name = df_play.loc[df_play["key"] == current_key, "display_name"].iloc[0]

        if not locked:
            if current_key or not typed.strip():
                sugs = []
            elif typed and engine.normalize_name(typed) == engine.normalize_name(selected_name):
                sugs = []
            else:
                sugs = engine.suggest_names_global(typed, name_index, limit=5)

            for sug_key, sug_name in sugs:
                if (sug_key in used) and (sug_key != current_key):
                    continue

                cols = cell.columns([1, 3])
                sug_row = df_play.loc[df_play["key"] == sug_key].iloc[0]
                sp = sprite_path(sug_row)
                if sp:
                    cols[0].image(sp, width=24)
                else:
                    cols[0].write("")

                if cols[1].button(sug_name, key=f"{cell_key}_pick_{sug_key}", disabled=locked):
                    st.session_state[cell_key] = sug_key
                    st.session_state[pending_key] = sug_name
                    st.rerun()

        pick = st.session_state.get(cell_key, "")
        grid_picks[(i, j)] = pick

        # Cell actions
        action_cols = cell.columns([1, 1])

        # Submit: validate and (if ok) lock
        if action_cols[0].button("OK", key=f"{cell_key}_submit", disabled=(locked or not pick)):
            used_keys = {v for v in grid_picks.values() if v}
            res = engine.validate_pick(
                df=df_play,
                rule_masks=rule_masks,
                board=board,
                row_i=i,
                col_j=j,
                pokemon_key=pick,
                used_keys=used_keys - {pick},
            )
            st.session_state[result_key] = res.reason
            # lock only if correct
            if res.ok:
                st.session_state[submitted_key] = True
            st.rerun()

        # Clear: unlock + clear pick + clear text
        action_cols[1].button(
            "X",
            key=f"{cell_key}_clear",
            disabled=(not pick and not typed and not locked),
            on_click=clear_one_cell,
            args=(i, j),
)

        if not pick:
            cell.caption("—")
        else:
            picked_row = df_play.loc[df_play["key"] == pick].iloc[0]
            sp = sprite_path(picked_row)
            if sp:
                cell.image(sp, width=48)

            used_keys = {v for v in grid_picks.values() if v}

            # Render selected sprite + status


            if locked:
                cell.success("✅ Correct")
            else:
                # show last submit result if any
                last = st.session_state.get(result_key, None)
                if last and last != "ok":
                    msg = {
                        "duplicate": "Already used.",
                        "does_not_match": "Doesn't match the row/column rules.",
                        "unknown_pokemon": "Unknown Pokémon.",
                    }.get(last, last)
                    cell.error(f"❌ {msg}")
                else:
                    cell.info("Not submitted")

    used = {v for v in grid_picks.values() if v}


# Summary
st.divider()
filled = [v for v in grid_picks.values() if v]
st.write(f"Filled: **{len(filled)}/{N*N}**  |  Unique used: **{len(set(filled))}**")

dupes = [k for k in set(filled) if filled.count(k) > 1]
if dupes:
    pretty = df_play.set_index("key").loc[dupes, "display_name"].tolist()
    st.error(f"Duplicate Pokémon used: {', '.join(pretty)}")


# Debug counts (optional)
with st.expander("Debug: candidate counts per cell"):
    counts = []
    for i in range(N):
        for j in range(N):
            cand = engine.candidates_for_cell(df_play, rule_masks, row_rule_ids[i], col_rule_ids[j])
            counts.append({"row": i + 1, "col": j + 1, "count": len(cand)})
    st.dataframe(pd.DataFrame(counts))