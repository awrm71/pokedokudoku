import pandas as pd
import streamlit as st

st.set_page_config(page_title="Local PokeDoku", layout="wide")

# ---------- Data ----------
@st.cache_data
def load_data(path: str = "pokemon.csv") -> pd.DataFrame:
    df = pd.read_csv(path)
    # Normalize blanks to empty strings
    df["type2"] = df["type2"].fillna("").astype(str)
    # Ensure bool columns are bool
    for col in ["legendary", "mega"]:
        df[col] = df[col].astype(str).str.lower().map({"true": True, "false": False})
    return df

df = load_data()

# ---------- Rule system ----------
# Each rule returns a boolean mask over df
TYPES = sorted(set(df["type1"]).union(set(df["type2"])) - {""})
GENS = sorted(df["gen"].unique().tolist())

RULES = {}

# Types
for t in TYPES:
    RULES[f"Type: {t}"] = lambda d, t=t: (d["type1"] == t) | (d["type2"] == t)

# Generations
for g in GENS:
    RULES[f"Gen: {g}"] = lambda d, g=g: d["gen"] == g

# Flags
RULES["Legendary"] = lambda d: d["legendary"] == True
RULES["Has Mega"] = lambda d: d["mega"] == True
RULES["Not Legendary"] = lambda d: d["legendary"] == False

RULE_NAMES = sorted(RULES.keys())

def candidates_for_cell(row_rule: str, col_rule: str) -> pd.DataFrame:
    m1 = RULES[row_rule](df)
    m2 = RULES[col_rule](df)
    return df[m1 & m2].copy()

# ---------- UI ----------
st.title("Local PokeDoku (3×3)")

with st.sidebar:
    st.header("Board Setup")

    if st.button("New Game (clear picks)"):
        st.session_state.clear()

    st.caption("Pick 3 row rules and 3 column rules.")
    row_rules = []
    col_rules = []

    for i in range(3):
        row_rules.append(
            st.selectbox(
                f"Row {i+1} rule",
                RULE_NAMES,
                key=f"row_rule_{i}",
            )
        )

    for j in range(3):
        col_rules.append(
            st.selectbox(
                f"Col {j+1} rule",
                RULE_NAMES,
                key=f"col_rule_{j}",
            )
        )

# Track selected Pokémon across grid
used = set()
grid_picks = {}

# ---------- Render board ----------
# Header row: column rules
header_cols = st.columns([1.2, 1, 1, 1])
header_cols[0].markdown("### ")
for j in range(3):
    header_cols[j+1].markdown(f"### {col_rules[j]}")

st.divider()

for i in range(3):
    row = st.columns([1.2, 1, 1, 1])
    row[0].markdown(f"### {row_rules[i]}")

    for j in range(3):
        cell_key = f"cell_{i}_{j}"
        cand = candidates_for_cell(row_rules[i], col_rules[j])

        # Remove already-used Pokémon from options (except whatever is currently selected here)
        current = st.session_state.get(cell_key, "")
        options = [""] + sorted(cand["name"].tolist())

        if current and current not in options:
            # If rules changed and current pick no longer valid
            current = ""

        filtered_options = [options[0]] + [o for o in options[1:] if (o not in used) or (o == current)]

        pick = row[j+1].selectbox(
            label=f"({i+1},{j+1})",
            options=filtered_options,
            index=filtered_options.index(current) if current in filtered_options else 0,
            key=cell_key,
            label_visibility="collapsed",
        )

        grid_picks[(i, j)] = pick

        # Visual feedback
        if pick == "":
            row[j+1].caption("—")
        else:
            valid = pick in cand["name"].values
            if valid:
                row[j+1].success("✅ Valid")
            else:
                row[j+1].error("❌ Invalid")

    # Update used set after row render to avoid “order” weirdness
    used = set(v for v in grid_picks.values() if v)

# ---------- Summary ----------
st.divider()

filled = [v for v in grid_picks.values() if v]
st.write(f"Filled: **{len(filled)}/9**  |  Unique used: **{len(set(filled))}**")

# Show mistakes / duplicates
dupes = [name for name in set(filled) if filled.count(name) > 1]
if dupes:
    st.error(f"Duplicate Pokémon used: {', '.join(dupes)}")

# Optional: show candidate counts per cell to help debugging / balancing
with st.expander("Show candidate counts per cell"):
    counts = []
    for i in range(3):
        for j in range(3):
            cand = candidates_for_cell(row_rules[i], col_rules[j])
            counts.append({"row": i+1, "col": j+1, "count": len(cand)})
    st.dataframe(pd.DataFrame(counts))