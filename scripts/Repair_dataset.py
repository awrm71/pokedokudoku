r"""
repair_dataset.py
─────────────────
Pass 1 repair for data/pokedex.csv + data/pokedex.parquet.

Fixes four classes of damage introduced by the one-off patch scripts, all of
which wrote fields they never actually computed:

  1. species_key  — 53 alternate-form rows hold the base species' national dex
                    NUMBER as a string ("646") instead of the species slug
                    ("kyurem"), so they aren't linked to their base species at
                    all. Repaired by joining the number to the default row's
                    pokemon_id.

  2. evo_position — those same rows were hardcoded to "final" regardless of
                    truth, so Rotom Wash, Meloetta Pirouette, the Therian
                    formes and the Calyrex riders all answer "Final Form"
                    despite none of their species evolving. Repaired by
                    inheriting from the base species row.

  3. evolves_by_* / is_branched_line — the 111 regional/alt form rows were
                    hardcoded all-False, so e.g. Hisuian Growlithe doesn't
                    register as evolving by item. Repaired by inheriting from
                    the base species row.

  4. region       — "Paldea + DLC" and "Paldea" existed as two separate board
                    categories for one region, and the Legends Arceus native
                    species were filed under Galar. Both repaired.

NO NETWORK REQUIRED. Everything is derived from data already in the file.
Fully idempotent: running it twice changes nothing the second time.

Run from the project root:
    .\.venv\Scripts\python.exe -u scripts\repair_dataset.py

Add --dry-run to see the report without writing anything.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CSV_PATH = DATA_DIR / "pokedex.csv"
PQ_PATH = DATA_DIR / "pokedex.parquet"

# ── Curated overrides ─────────────────────────────────────────────────────────
# Inheritance from the base species is correct for every form currently in the
# dataset, so this starts empty. It exists so that when a form genuinely
# differs from its species (a regional form that evolves when the base doesn't,
# for example) the exception is RECORDED here rather than hand-edited into the
# CSV where the next rebuild would silently drop it.
#
# Format: "pokemon_key": "premature" | "start" | "middle" | "final" | "none"
EVO_POSITION_OVERRIDES: dict[str, str] = {
    # "basculin-blue-striped": "none",   # example: only White-Striped evolves
}

# Rows whose pokemon_id points at the wrong PokeAPI variety. Recorded here for
# the same reason as above: hand-editing the CSV means the next rebuild loses
# the correction silently.
#
#   zygarde-10 held 10181, which is zygarde-10-power-construct — a separate
#   variety with no sprite in the sprite repo. The 10% Forme is 10118.
POKEMON_ID_OVERRIDES: dict[str, int] = {
    "zygarde-10": 10118,
}

# Species introduced in Legends: Arceus. PokeAPI reports these as generation
# viii, which build_pokedex.py maps to Galar. They belong to Hisui.
HISUI_NATIVE_SPECIES = {
    "wyrdeer",
    "kleavor",
    "ursaluna",
    "basculegion",
    "sneasler",
    "overqwil",
    "enamorus",
}

# Region label consolidation. The main builder writes "Paldea + DLC" while
# augment_regional_forms.py writes "Paldea", which split one region into two
# separate board categories.
REGION_ALIASES = {
    "Paldea + DLC": "Paldea",
}

BOOL_PREFIXES = ("is_", "has_", "evolves_by_", "evolved_by_", "learns_", "has_ability_")

INHERITED_BOOL_COLS = [
    "evolves_by_levelup",
    "evolves_by_friendship",
    "evolves_by_trade",
    "evolves_by_item",
    "is_branched_line",
]


def coerce_bools(df: pd.DataFrame) -> pd.DataFrame:
    """Match api_main.load_data()'s coercion so this script sees the same
    values the running server does."""
    for col in [c for c in df.columns if str(c).startswith(BOOL_PREFIXES)]:
        s = df[col]
        if s.dtype == bool:
            continue
        if pd.api.types.is_numeric_dtype(s):
            df[col] = pd.to_numeric(s, errors="coerce").fillna(0).astype(int).astype(bool)
            continue
        df[col] = (
            s.astype(str)
            .str.strip()
            .str.lower()
            .map({"true": True, "false": False, "1": True, "0": False,
                  "yes": True, "no": False})
            .fillna(False)
            .astype(bool)
        )
    return df


def load() -> pd.DataFrame:
    if CSV_PATH.exists():
        df = pd.read_csv(CSV_PATH, low_memory=False)
    elif PQ_PATH.exists():
        df = pd.read_parquet(PQ_PATH)
    else:
        sys.exit(f"ERROR: no pokedex.csv or pokedex.parquet found in {DATA_DIR}")
    df["type2"] = df["type2"].fillna("") if "type2" in df.columns else ""
    return coerce_bools(df)


def base_rows_by_species(df: pd.DataFrame) -> dict[str, pd.Series]:
    """species_key -> the default (base) row for that species."""
    out: dict[str, pd.Series] = {}
    for _, r in df[df["is_default"] == True].iterrows():  # noqa: E712
        sk = str(r["species_key"])
        if sk not in out:
            out[sk] = r
    return out


def fix_species_keys(df: pd.DataFrame, log: list[str]) -> int:
    """Numeric species_key -> species slug, via the default row's pokemon_id."""
    numeric = df["species_key"].astype(str).str.fullmatch(r"\d+").fillna(False)
    if not numeric.any():
        log.append("species_key: already clean, nothing to do")
        return 0

    # National dex number -> species slug, taken from default rows only.
    id_to_species: dict[int, str] = {}
    for _, r in df[df["is_default"] == True].iterrows():  # noqa: E712
        if pd.notna(r.get("pokemon_id")):
            id_to_species.setdefault(int(r["pokemon_id"]), str(r["species_key"]))

    fixed = 0
    orphans: list[str] = []
    for idx in df.index[numeric]:
        dex = int(str(df.at[idx, "species_key"]))
        slug = id_to_species.get(dex)
        if slug is None:
            orphans.append(f"{df.at[idx, 'key']} (species_key={dex})")
            continue
        df.at[idx, "species_key"] = slug
        fixed += 1

    log.append(f"species_key: repaired {fixed} numeric values")
    for o in orphans:
        log.append(f"  WARNING unresolved species_key: {o}")
    return fixed


def fix_pokemon_ids(df: pd.DataFrame, log: list[str]) -> int:
    """Apply recorded pokemon_id corrections. Runs BEFORE species_key repair,
    since that repair joins on the default rows' pokemon_id values."""
    changed = 0
    for key, want in POKEMON_ID_OVERRIDES.items():
        mask = df["key"].astype(str) == key
        if not mask.any():
            log.append(f"  WARNING pokemon_id override for unknown key: {key}")
            continue
        idx = df.index[mask][0]
        current = df.at[idx, "pokemon_id"]
        current = int(current) if pd.notna(current) else None
        if current != want:
            log.append(f"  {key:32s} {current} -> {want}")
            df.at[idx, "pokemon_id"] = want
            changed += 1
    log.append(f"pokemon_id: applied {changed} corrections")
    return changed


def fix_evo_position(df: pd.DataFrame, log: list[str]) -> int:
    """Non-default rows inherit evo_position from their base species, unless
    an explicit override says otherwise."""
    bases = base_rows_by_species(df)
    changed = 0
    details: list[str] = []
    missing_base: list[str] = []

    for idx, r in df.iterrows():
        if bool(r["is_default"]):
            continue
        key = str(r["key"])

        if key in EVO_POSITION_OVERRIDES:
            want = EVO_POSITION_OVERRIDES[key]
        else:
            base = bases.get(str(r["species_key"]))
            if base is None:
                missing_base.append(key)
                continue
            want = str(base["evo_position"])

        if str(r["evo_position"]) != want:
            details.append(f"  {key:32s} {str(r['evo_position']):9s} -> {want}")
            df.at[idx, "evo_position"] = want
            changed += 1

    log.append(f"evo_position: corrected {changed} rows")
    log.extend(details)
    for m in missing_base:
        log.append(f"  WARNING no base row for species of: {m}")
    return changed


def fix_inherited_flags(df: pd.DataFrame, log: list[str]) -> int:
    """Evolution-method flags and is_branched_line are species-level facts.
    Regional/alt form rows were written all-False; inherit them instead.

    Note the known imprecision: PokeAPI evolution chains are species-level, so
    a regional form whose evolution METHOD differs from the base (Galarian
    Slowpoke evolving by item rather than by level) inherits the union of the
    species' methods. That is the same granularity the rest of the dataset
    already uses, and is fixed by adding a curated override later if needed.
    """
    bases = base_rows_by_species(df)
    cols = [c for c in INHERITED_BOOL_COLS if c in df.columns]
    if not cols:
        log.append("inherited flags: no relevant columns present")
        return 0

    changed_rows = 0
    changed_cells = 0
    for idx, r in df.iterrows():
        if bool(r["is_default"]):
            continue
        base = bases.get(str(r["species_key"]))
        if base is None:
            continue
        row_changed = False
        for c in cols:
            want = bool(base[c])
            if bool(r[c]) != want:
                df.at[idx, c] = want
                changed_cells += 1
                row_changed = True
        if row_changed:
            changed_rows += 1

    log.append(
        f"inherited flags: updated {changed_cells} values across {changed_rows} form rows"
    )
    return changed_cells


def fix_regions(df: pd.DataFrame, log: list[str]) -> int:
    changed = 0

    before = df["region"].astype(str)
    df["region"] = before.replace(REGION_ALIASES)
    merged = int((before != df["region"]).sum())
    changed += merged
    if merged:
        log.append(f"region: merged {merged} rows into consolidated labels {REGION_ALIASES}")
    else:
        log.append("region: alias merge already applied")

    hisui_mask = df["species_key"].astype(str).isin(HISUI_NATIVE_SPECIES)
    needs = hisui_mask & (df["region"].astype(str) != "Hisui")
    n = int(needs.sum())
    if n:
        for k in df.loc[needs, "key"]:
            log.append(f"  region -> Hisui: {k}")
        df.loc[needs, "region"] = "Hisui"
        changed += n
    log.append(f"region: reassigned {n} Legends Arceus natives to Hisui")

    return changed


def validate(df: pd.DataFrame) -> list[str]:
    """The checks that would have caught every data bug found so far."""
    problems: list[str] = []

    numeric = df["species_key"].astype(str).str.fullmatch(r"\d+").fillna(False)
    if numeric.any():
        problems.append(f"{int(numeric.sum())} rows still have a numeric species_key")

    known = set(df.loc[df["is_default"] == True, "species_key"].astype(str))  # noqa: E712
    orphan = ~df["species_key"].astype(str).isin(known)
    if orphan.any():
        bad = df.loc[orphan, "key"].head(10).tolist()
        problems.append(f"{int(orphan.sum())} rows have a species_key with no base row: {bad}")

    dupes = df["key"].duplicated().sum()
    if dupes:
        problems.append(f"{dupes} duplicate keys")

    dupe_ids = df["pokemon_id"].duplicated().sum()
    if dupe_ids:
        problems.append(f"{dupe_ids} duplicate pokemon_id values")

    bases = base_rows_by_species(df)
    mismatched = []
    for _, r in df[df["is_default"] != True].iterrows():  # noqa: E712
        key = str(r["key"])
        if key in EVO_POSITION_OVERRIDES:
            continue
        base = bases.get(str(r["species_key"]))
        if base is not None and str(r["evo_position"]) != str(base["evo_position"]):
            mismatched.append(key)
    if mismatched:
        problems.append(
            f"{len(mismatched)} forms disagree with their species' evo_position "
            f"without an override: {mismatched[:10]}"
        )

    return problems


def summarise(df: pd.DataFrame) -> list[str]:
    lines = [f"rows: {len(df)}    columns: {len(df.columns)}"]
    lines.append("regions: " + ", ".join(
        f"{k} {v}" for k, v in sorted(df["region"].value_counts().items())
    ))
    lines.append("evo_position: " + ", ".join(
        f"{k} {v}" for k, v in sorted(df["evo_position"].value_counts().items())
    ))
    flags = ["is_legendary", "is_mythical", "is_baby", "is_fossil", "is_first_partner",
             "is_paradox", "is_ultra_beast", "is_mega_form", "is_gmax_form",
             "is_regional_form", "is_alt_form", "is_branched_line"]
    lines.append("flags: " + ", ".join(
        f"{c} {int(df[c].sum())}" for c in flags if c in df.columns
    ))
    lines.append("evolution methods: " + ", ".join(
        f"{c.replace('evolves_by_', '')} {int(df[c].sum())}"
        for c in INHERITED_BOOL_COLS if c.startswith("evolves_by_") and c in df.columns
    ))
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description="Repair the pokedex dataset in place.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Report what would change without writing any files.")
    ap.add_argument("--no-backup", action="store_true",
                    help="Skip writing timestamped .bak copies.")
    args = ap.parse_args()

    df = load()
    print(f"Loaded {len(df)} rows from {CSV_PATH if CSV_PATH.exists() else PQ_PATH}\n")

    log: list[str] = []
    total = 0
    total += fix_pokemon_ids(df, log)
    total += fix_species_keys(df, log)
    total += fix_evo_position(df, log)
    total += fix_inherited_flags(df, log)
    total += fix_regions(df, log)

    print("── Repairs ─────────────────────────────────────────────")
    for line in log:
        print(line)

    print("\n── Validation ──────────────────────────────────────────")
    problems = validate(df)
    if problems:
        for p in problems:
            print(f"  FAIL  {p}")
    else:
        print("  All checks passed.")

    print("\n── Dataset summary ─────────────────────────────────────")
    for line in summarise(df):
        print(line)

    if args.dry_run:
        print("\n(dry run: nothing written)")
        return 0

    if total == 0:
        print("\nNothing to change; files left untouched.")
        return 0

    if not args.no_backup:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        for p in (CSV_PATH, PQ_PATH):
            if p.exists():
                bak = p.with_suffix(p.suffix + f".{stamp}.bak")
                shutil.copy2(p, bak)
                print(f"\nBackup: {bak.name}")

    df.to_csv(CSV_PATH, index=False)
    print(f"Wrote: {CSV_PATH}")
    try:
        df.to_parquet(PQ_PATH, index=False)
        print(f"Wrote: {PQ_PATH}")
    except Exception as e:
        print(f"WARNING: could not write parquet ({e}).")
        print("         Delete data/pokedex.parquet so the API falls back to the CSV,")
        print("         or install pyarrow and re-run.")

    print("\nDone. Restart uvicorn to pick up the new dataset.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())