"""
patch_flags.py
──────────────
Populates three flag columns that the build script left as all-False:
  • is_first_partner  — the 81 starter Pokémon (all 9 gens, full evolution lines)
  • is_ultra_beast    — the 11 Ultra Beasts from Gen 7
  • is_paradox        — the 20 Paradox Pokémon from Gen 9

Run from the project root:
    .\.venv\Scripts\python.exe -u scripts\patch_flags.py

Safe to re-run — just overwrites flags with correct values.
"""

from pathlib import Path
import pandas as pd

ROOT     = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CSV_OUT  = DATA_DIR / "pokedex.csv"
PQ_OUT   = DATA_DIR / "pokedex.parquet"

# ── Starter Pokémon — all 9 generations, complete evolution lines ──────────────
STARTERS = {
    # Gen 1 — Kanto
    "bulbasaur", "ivysaur", "venusaur",
    "charmander", "charmeleon", "charizard",
    "squirtle", "wartortle", "blastoise",
    # Gen 2 — Johto
    "chikorita", "bayleef", "meganium",
    "cyndaquil", "quilava", "typhlosion",
    "totodile", "croconaw", "feraligatr",
    # Gen 3 — Hoenn
    "treecko", "grovyle", "sceptile",
    "torchic", "combusken", "blaziken",
    "mudkip", "marshtomp", "swampert",
    # Gen 4 — Sinnoh
    "turtwig", "grotle", "torterra",
    "chimchar", "monferno", "infernape",
    "piplup", "prinplup", "empoleon",
    # Gen 5 — Unova
    "snivy", "servine", "serperior",
    "tepig", "pignite", "emboar",
    "oshawott", "dewott", "samurott",
    # Gen 6 — Kalos
    "chespin", "quilladin", "chesnaught",
    "fennekin", "braixen", "delphox",
    "froakie", "frogadier", "greninja",
    # Gen 7 — Alola
    "rowlet", "dartrix", "decidueye",
    "litten", "torracat", "incineroar",
    "popplio", "brionne", "primarina",
    # Gen 8 — Galar
    "grookey", "thwackey", "rillaboom",
    "scorbunny", "raboot", "cinderace",
    "sobble", "drizzile", "inteleon",
    # Gen 9 — Paldea
    "sprigatito", "floragato", "meowscarada",
    "fuecoco", "crocalor", "skeledirge",
    "quaxly", "quaxwell", "quaquaval",
}

# Also flag Hisuian starter evolutions if regional augmentation has been run
STARTERS_REGIONAL = {
    "decidueye-hisui",   # Hisuian Decidueye (starter in Legends: Arceus)
    "typhlosion-hisui",
    "samurott-hisui",
}

# ── Ultra Beasts ───────────────────────────────────────────────────────────────
ULTRA_BEASTS = {
    "nihilego",
    "buzzwole",
    "pheromosa",
    "xurkitree",
    "celesteela",
    "kartana",
    "guzzlord",
    "poipole",
    "naganadel",
    "stakataka",
    "blacephalon",
}

# ── Paradox Pokémon ────────────────────────────────────────────────────────────
PARADOX = {
    # Past paradoxes (Scarlet)
    "great-tusk",
    "scream-tail",
    "brute-bonnet",
    "flutter-mane",
    "slither-wing",
    "sandy-shocks",
    "roaring-moon",
    "walking-wake",
    "gouging-fire",
    "raging-bolt",
    # Future paradoxes (Violet)
    "iron-treads",
    "iron-bundle",
    "iron-hands",
    "iron-jugulis",
    "iron-moth",
    "iron-thorns",
    "iron-valiant",
    "iron-leaves",
    "iron-boulder",
    "iron-crown",
}


def main():
    if PQ_OUT.exists():
        df = pd.read_parquet(PQ_OUT)
    elif CSV_OUT.exists():
        df = pd.read_csv(CSV_OUT)
    else:
        raise FileNotFoundError("No pokedex data found in data/")

    print(f"Loaded {len(df)} rows")

    # Ensure columns exist (default False)
    for col in ("is_first_partner", "is_ultra_beast", "is_paradox"):
        if col not in df.columns:
            df[col] = False
        df[col] = df[col].fillna(False).astype(bool)

    all_starters = STARTERS | STARTERS_REGIONAL

    # Apply flags by key
    before_starter = df["is_first_partner"].sum()
    before_ub      = df["is_ultra_beast"].sum()
    before_paradox = df["is_paradox"].sum()

    df.loc[df["key"].isin(all_starters),  "is_first_partner"] = True
    df.loc[df["key"].isin(ULTRA_BEASTS),  "is_ultra_beast"]   = True
    df.loc[df["key"].isin(PARADOX),       "is_paradox"]       = True

    after_starter  = df["is_first_partner"].sum()
    after_ub       = df["is_ultra_beast"].sum()
    after_paradox  = df["is_paradox"].sum()

    print(f"is_first_partner: {before_starter} → {after_starter}")
    print(f"is_ultra_beast:   {before_ub} → {after_ub}")
    print(f"is_paradox:       {before_paradox} → {after_paradox}")

    # Show which starters were found vs missing
    found    = set(df.loc[df["is_first_partner"], "key"].tolist())
    missing  = all_starters - found
    if missing:
        print(f"\nStarters NOT found in dataset (may need regional augmentation):")
        for m in sorted(missing):
            print(f"  {m}")

    # Save
    df.to_csv(CSV_OUT, index=False)
    df.to_parquet(PQ_OUT, index=False)
    print(f"\n✓ Saved to {CSV_OUT} and {PQ_OUT}")


if __name__ == "__main__":
    main()
