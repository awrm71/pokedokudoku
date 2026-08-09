#!/usr/bin/env python3
"""
fix_forms.py — Run from the project root AFTER patch_dataset.py.
  1. Adds is_alt_form=True to all alternate forms in the dataset so
     apply_answer_policy includes them as valid answers.
  2. Manually adds the 3 Necrozma fusion forms (PokeAPI slugs were
     unreachable during patch_dataset.py run).
  3. Verifies key missing forms and reports what's still absent.

Run:  .venv\\Scripts\\python.exe scripts/fix_forms.py
No internet required.
"""

import pathlib
import pandas as pd
import numpy as np

DATA_DIR = pathlib.Path("data")
CSV_PATH = DATA_DIR / "pokedex.csv"
OUT_PQ   = DATA_DIR / "pokedex.parquet"

df = pd.read_csv(CSV_PATH)
print(f"Loaded {len(df)} rows.")

# ─── 1. Add is_alt_form column if missing ────────────────────────────────────
if "is_alt_form" not in df.columns:
    df["is_alt_form"] = False
    print("Added is_alt_form column.")

# Mark every row that IS an alternate form but isn't already flagged as
# mega/gmax/regional.  The rule: key != species_key  AND  is_default == False.
# This catches Rotom appliances, Deoxys formes, Kyurem fusions, Calyrex riders,
# Therian formes, battle forms, gender variants, etc.
alt_mask = (
    (df["is_default"] == False) &
    (df["is_mega_form"] == False) &
    (df["is_gmax_form"] == False) &
    (df["is_regional_form"] == False) &
    (df["key"] != df.get("species_key", df["key"]))
)
# Also catch rows where species_key column may not exist or matches key
if "species_key" in df.columns:
    alt_mask = (
        (df["is_default"] == False) &
        (df["is_mega_form"] == False) &
        (df["is_gmax_form"] == False) &
        (df["is_regional_form"] == False)
    )

newly_flagged = alt_mask.sum() - (df["is_alt_form"] == True).sum()
df.loc[alt_mask, "is_alt_form"] = True
print(f"Flagged {(df['is_alt_form']==True).sum()} rows as is_alt_form=True.")

# ─── 2. Manually add Necrozma fusion forms ───────────────────────────────────
MOVE_COLS    = [c for c in df.columns if c.startswith("learns_")]
ABILITY_COLS = [c for c in df.columns if c.startswith("has_ability_")]

# Base Necrozma row for reference values
base = df[df["key"] == "necrozma"]
if len(base) == 0:
    print("ERROR: base Necrozma not found — skipping Necrozma additions.")
else:
    base = base.iloc[0]

    necrozma_forms = [
        {
            "key": "necrozma-dusk-mane",
            "display_name": "Necrozma Dusk Mane",
            "type1": "psychic", "type2": "steel",
            "note": "Necrozma fused with Solgaleo",
        },
        {
            "key": "necrozma-dawn-wings",
            "display_name": "Necrozma Dawn Wings",
            "type1": "psychic", "type2": "ghost",
            "note": "Necrozma fused with Lunala",
        },
        {
            "key": "necrozma-ultra",
            "display_name": "Necrozma Ultra",
            "type1": "psychic", "type2": "dragon",
            "note": "Ultra Necrozma",
        },
    ]

    present_keys = set(df["key"].tolist())
    for form in necrozma_forms:
        if form["key"] in present_keys:
            print(f"  Already present: {form['key']}")
            continue

        row = {}
        # Copy most fields from base Necrozma
        for col in df.columns:
            row[col] = base[col]

        # Override form-specific fields
        row["key"]            = form["key"]
        row["display_name"]   = form["display_name"]
        row["species_key"]    = "necrozma"
        row["type1"]          = form["type1"]
        row["type2"]          = form["type2"]
        row["is_default"]     = False
        row["is_monotype"]    = False
        row["is_dual_type"]   = True
        row["is_mega_form"]   = False
        row["is_gmax_form"]   = False
        row["is_regional_form"] = False
        row["is_alt_form"]    = True

        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        print(f"  Added: {form['display_name']} ({form['type1']}/{form['type2']})")

# ─── 3. Verify coverage of key forms ─────────────────────────────────────────
MUST_HAVE = [
    "necrozma-dusk-mane", "necrozma-dawn-wings", "necrozma-ultra",
    "kyurem-black", "kyurem-white",
    "rotom-heat", "rotom-wash", "rotom-frost", "rotom-fan", "rotom-mow",
    "calyrex-ice", "calyrex-shadow",
    "deoxys-attack", "deoxys-defense", "deoxys-speed",
    "tornadus-therian", "thundurus-therian", "landorus-therian",
    "aegislash-blade", "lycanroc-midnight", "lycanroc-dusk",
    "toxtricity-low-key", "palafin-hero",
]
present = set(df["key"].tolist())
print("\nCoverage check:")
for k in MUST_HAVE:
    row = df[df["key"] == k]
    if len(row):
        r = row.iloc[0]
        status = "OK (visible)" if (r.get("is_alt_form") or r.get("is_default") or
                                     r.get("is_mega_form") or r.get("is_gmax_form") or
                                     r.get("is_regional_form")) else "PRESENT BUT FILTERED"
    else:
        status = "MISSING — rerun patch_dataset.py"
    print(f"  {k:40s} {status}")

# ─── 4. Save ──────────────────────────────────────────────────────────────────
df.to_csv(CSV_PATH, index=False)
df.to_parquet(OUT_PQ, index=False)
print(f"\nSaved. Total rows: {len(df)}")
print("Restart server (delete __pycache__ first) to pick up changes.")