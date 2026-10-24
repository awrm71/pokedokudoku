r"""
recurate_categories.py
──────────────────────
Pass 1b. Rebuilds the move categories and fixes the fossil flag. Offline:
everything is derived from the `moves` column already in the dataset.

RUN repair_dataset.py FIRST. This script needs a correct species_key to
inherit anything, and it refuses to run if it finds the numeric ones.

What it does:

  1. moves inheritance — all 34 Gigantamax rows ship from PokeAPI with an
     empty move list, so today they satisfy zero move rules. They inherit
     their base species' list, since a Gigantamax Charizard learns what
     Charizard learns.

  2. learns_* rebuild — the existing 60 columns were chosen by raw frequency,
     which surfaced TM filler: Protect (95% of the dex), Frustration, Return,
     Confide, Snore, Double Team, Hidden Power, Strength, Reflect. A category
     that excludes almost nobody is a free cell. These are replaced with a
     curated list built around the genre's standard 21, expanded for variety.

  3. is_fossil — currently 6 rows, and the wrong 6 (Aerodactyl, its Mega, and
     the four Galar chimeras), missing every real fossil line. Set from a
     curated species list, applied by species_key so forms inherit. Lands on
     26, matching the standard set exactly.

Run from the project root:
    .\.venv\Scripts\python.exe -u scripts\recurate_categories.py --dry-run

Idempotent. Drop --dry-run to write.

NOTE for later: once this has run, do NOT run
`build_pokedex.py --augment-existing --onehot-moves N` again, or it will
re-add frequency-picked columns alongside these. Folding this list into the
builder is a Pass 2 job.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CSV_PATH = DATA_DIR / "pokedex.csv"
PQ_PATH = DATA_DIR / "pokedex.parquet"

# ── Move categories ───────────────────────────────────────────────────────────
# The genre's standard 21, for parity with what players expect.
CORE_MOVES = [
    "acrobatics", "brick-break", "calm-mind", "close-combat", "crunch",
    "dazzling-gleam", "earthquake", "flamethrower", "fly", "hydro-pump",
    "ice-beam", "ice-punch", "metronome", "protect", "psychic", "razor-leaf",
    "shadow-ball", "sludge-bomb", "surf", "tail-slap", "thunderbolt",
]

# Expansion set. Every entry is a recognisable, type-flavoured move rather
# than universal TM coverage, which is the whole point of the swap.
EXTRA_MOVES = [
    "aerial-ace", "air-slash", "aura-sphere", "bug-buzz", "bulk-up",
    "dark-pulse", "dragon-claw", "dragon-dance", "drain-punch", "earth-power",
    "energy-ball", "extrasensory", "fire-blast", "fire-punch", "flash-cannon",
    "focus-blast", "giga-drain", "grass-knot", "heavy-slam", "hex",
    "high-horsepower", "ice-fang", "icicle-crash", "iron-head", "iron-tail",
    "knock-off", "leech-life", "liquidation", "megahorn", "moonblast",
    "night-slash", "outrage", "play-rough", "poison-jab", "power-whip",
    "psyshock", "rock-blast", "rock-slide", "scald", "seed-bomb",
    "shadow-claw", "sludge-wave", "solar-beam", "stone-edge", "sucker-punch",
    "superpower", "swords-dance", "thunder", "thunder-punch", "toxic-spikes",
    "triple-axel", "u-turn", "waterfall", "wild-charge", "will-o-wisp",
    "x-scissor", "zen-headbutt",
]

CURATED_MOVES = CORE_MOVES + EXTRA_MOVES

# engine.generate_board drops move rules covering more than 35% of the answer
# pool from daily board generation. Anything above this still works as a Draft
# Board category, it just won't show up on a Daily or Shuffle board.
BROAD_MOVE_PCT = 35.0

# ── Fossil species ────────────────────────────────────────────────────────────
# Applied by species_key, so Aerodactyl Mega inherits from Aerodactyl.
FOSSIL_SPECIES = {
    # Kanto
    "omanyte", "omastar", "kabuto", "kabutops", "aerodactyl",
    # Hoenn
    "lileep", "cradily", "anorith", "armaldo",
    # Sinnoh
    "cranidos", "rampardos", "shieldon", "bastiodon",
    # Unova
    "tirtouga", "carracosta", "archen", "archeops",
    # Kalos
    "tyrunt", "tyrantrum", "amaura", "aurorus",
    # Galar (fossil chimeras)
    "dracozolt", "arctozolt", "dracovish", "arctovish",
}

BOOL_PREFIXES = ("is_", "has_", "evolves_by_", "evolved_by_", "learns_", "has_ability_")


def move_col(token: str) -> str:
    return "learns_" + token.replace("-", "_")


def parse_list(raw) -> list[str]:
    """The moves/abilities columns are JSON-ish. Most rows are valid JSON with
    double quotes; some were written by a patch script using Python repr with
    single quotes. Handle both, same as api_main does."""
    s = str(raw)
    if not s.startswith("["):
        return []
    try:
        parsed = json.loads(s)
    except Exception:
        try:
            parsed = json.loads(s.replace("'", '"'))
        except Exception:
            return []
    return [str(x) for x in parsed if x] if isinstance(parsed, list) else []


def coerce_bools(df: pd.DataFrame) -> pd.DataFrame:
    for col in [c for c in df.columns if str(c).startswith(BOOL_PREFIXES)]:
        s = df[col]
        if s.dtype == bool:
            continue
        if pd.api.types.is_numeric_dtype(s):
            df[col] = pd.to_numeric(s, errors="coerce").fillna(0).astype(int).astype(bool)
            continue
        df[col] = (
            s.astype(str).str.strip().str.lower()
            .map({"true": True, "false": False, "1": True, "0": False,
                  "yes": True, "no": False})
            .fillna(False).astype(bool)
        )
    return df


def load() -> pd.DataFrame:
    if CSV_PATH.exists():
        df = pd.read_csv(CSV_PATH, low_memory=False)
    elif PQ_PATH.exists():
        df = pd.read_parquet(PQ_PATH)
    else:
        sys.exit(f"ERROR: no pokedex.csv or pokedex.parquet found in {DATA_DIR}")
    if "type2" in df.columns:
        df["type2"] = df["type2"].fillna("")
    return coerce_bools(df)


def require_repaired(df: pd.DataFrame) -> None:
    numeric = df["species_key"].astype(str).str.fullmatch(r"\d+").fillna(False)
    if numeric.any():
        sys.exit(
            f"ERROR: {int(numeric.sum())} rows still have a numeric species_key.\n"
            "       Run scripts/repair_dataset.py first — this script inherits\n"
            "       moves and fossil flags via species_key and would silently\n"
            "       skip those rows."
        )


def inherit_moves(df: pd.DataFrame, log: list[str]) -> int:
    """Form rows with an empty move list take their base species' list."""
    base_moves: dict[str, str] = {}
    for _, r in df[df["is_default"] == True].iterrows():  # noqa: E712
        sk = str(r["species_key"])
        if sk not in base_moves and parse_list(r["moves"]):
            base_moves[sk] = r["moves"]

    filled = 0
    for idx, r in df.iterrows():
        if bool(r["is_default"]) or parse_list(r["moves"]):
            continue
        src = base_moves.get(str(r["species_key"]))
        if src is None:
            log.append(f"  WARNING no base move list to inherit for: {r['key']}")
            continue
        df.at[idx, "moves"] = src
        filled += 1

    log.append(f"moves: inherited a move list for {filled} form rows with empty lists")
    return filled


def rebuild_move_columns(
    df: pd.DataFrame, log: list[str]
) -> tuple[pd.DataFrame, int, list[tuple[str, int, float]]]:
    existing = sorted(c for c in df.columns if str(c).startswith("learns_"))
    wanted = [move_col(m) for m in CURATED_MOVES]

    # Drop ALL existing learns_* columns, including ones that happen to be in
    # the curated list. Keeping the overlap and concatenating the rebuilt
    # version on top would produce duplicate column names, which survives a
    # CSV round trip as learns_protect / learns_protect.1 and quietly breaks
    # both the engine's rule build and this script's own idempotency.
    if existing:
        df = df.drop(columns=existing)
    kept = len([c for c in existing if c in wanted])
    log.append(f"learns_*: dropped {len(existing)} existing columns "
               f"({len(existing) - kept} frequency-picked, {kept} superseded by the curated build)")

    move_sets = df["moves"].map(lambda x: set(parse_list(x)))
    n = len(df)
    stats: list[tuple[str, int, float]] = []
    new_cols: dict[str, pd.Series] = {}

    for token in CURATED_MOVES:
        col = move_col(token)
        series = move_sets.map(lambda s, t=token: t in s).astype(bool)
        new_cols[col] = series
        count = int(series.sum())
        stats.append((token, count, 100.0 * count / n if n else 0.0))

    # Concat in one shot rather than assigning column by column. Dropping the
    # old columns leaves the frame fragmented, and inserting 78 more would
    # trigger pandas' fragmentation warning and copy the block repeatedly.
    df = pd.concat([df, pd.DataFrame(new_cols, index=df.index)], axis=1).copy()

    log.append(f"learns_*: built {len(CURATED_MOVES)} curated columns "
               f"({len(CORE_MOVES)} core, {len(EXTRA_MOVES)} added)")

    empty = [t for t, c, _ in stats if c == 0]
    for t in empty:
        log.append(f"  WARNING no Pokemon in the dataset learns: {t}")

    return df, len(existing) + len(CURATED_MOVES), stats


def fix_fossils(df: pd.DataFrame, log: list[str]) -> int:
    if "is_fossil" not in df.columns:
        df["is_fossil"] = False

    before = int(df["is_fossil"].sum())
    want = df["species_key"].astype(str).isin(FOSSIL_SPECIES)

    missing = FOSSIL_SPECIES - set(df["species_key"].astype(str))
    for m in sorted(missing):
        log.append(f"  WARNING fossil species not present in dataset: {m}")

    changed = int((df["is_fossil"] != want).sum())
    df["is_fossil"] = want
    after = int(df["is_fossil"].sum())
    log.append(f"is_fossil: {before} -> {after} rows ({changed} values changed)")
    return changed


def report_moves(stats: list[tuple[str, int, float]]) -> list[str]:
    lines = []
    broad = [(t, c, p) for t, c, p in stats if p > BROAD_MOVE_PCT]
    if broad:
        lines.append(f"Above the generator's {BROAD_MOVE_PCT:.0f}% broad-move cutoff "
                     f"(Draft Board only, not Daily/Shuffle):")
        for t, c, p in sorted(broad, key=lambda x: -x[2]):
            lines.append(f"  {t:20s} {c:5d}  {p:5.1f}%")
    lines.append("")
    lines.append("Narrowest ten (the hardest, most interesting categories):")
    for t, c, p in sorted(stats, key=lambda x: x[1])[:10]:
        lines.append(f"  {t:20s} {c:5d}  {p:5.1f}%")
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description="Recurate move categories and fossil flags.")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-backup", action="store_true")
    ap.add_argument("--show-all-moves", action="store_true",
                    help="Print the full coverage table for every curated move.")
    args = ap.parse_args()

    df = load()
    require_repaired(df)
    print(f"Loaded {len(df)} rows\n")

    log: list[str] = []
    total = 0
    total += inherit_moves(df, log)
    df, changed, stats = rebuild_move_columns(df, log)
    total += changed
    total += fix_fossils(df, log)

    print("── Changes ─────────────────────────────────────────────")
    for line in log:
        print(line)

    print("\n── Move coverage ───────────────────────────────────────")
    if args.show_all_moves:
        for t, c, p in sorted(stats, key=lambda x: -x[1]):
            print(f"  {t:20s} {c:5d}  {p:5.1f}%")
    else:
        for line in report_moves(stats):
            print(line)

    print("\n── Summary ─────────────────────────────────────────────")
    print(f"rows: {len(df)}    columns: {len(df.columns)}")
    print(f"learns_* columns: {sum(1 for c in df.columns if str(c).startswith('learns_'))}")
    print(f"has_ability_* columns: {sum(1 for c in df.columns if str(c).startswith('has_ability_'))} (unchanged)")
    print(f"is_fossil: {int(df['is_fossil'].sum())}")

    if args.dry_run:
        print("\n(dry run: nothing written)")
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

    print("\nDone. Restart uvicorn to pick up the new categories.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())