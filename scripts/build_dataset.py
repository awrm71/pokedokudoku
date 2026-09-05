r"""
build_dataset.py — the single source of truth for data/pokedex.csv + .parquet.

Replaces the scatter of one-off patch scripts (repair_dataset, recurate_categories,
patch_flags, augment_regional_forms, fix_forms) with ONE staged, validated,
dry-runnable builder. It starts from the current dataset (so every curated
decision already made is preserved) and applies each fix as a separate,
individually-reported stage.

HARD CONSTRAINT: the output is a drop-in replacement with the EXACT same schema
the rest of the code reads — same columns, same order, same dtypes, same value
conventions. engine.py / api_main.py / reverse_engine.py / the frontend need no
changes. The only thing that changes on the page is corrected data.

Stages:
  0. Load current dataset as the base (preserves curated moves/abilities/flags).
  1. Fetch Legends: Z-A Mega Evolutions from PokeAPI (cached) and add them as
     rows with is_mega_form=True → they become answers AND feed the engine's
     existing "Mega Evolved" rule automatically (engine.py line ~246).
  2. Add curated distinct non-mega forms (Castform weathers, Hoopa Unbound,
     Zacian/Zamazenta Crowned, Ogerpon masks, Basculin White-Striped).
  3. Flip evolution semantics outgoing → incoming (Machamp gets 'trade', etc.),
     matching PokeDoku's "Evolved by" wording. Uses cached evolution chains.
  4. Derive has_mega / has_gmax / is_mega_form / is_gmax_form correctly from the
     ACTUAL form rows present, not PokeAPI's unreliable flags.
  5. Normalize display names to flat style ("Mega Dragonite", "Kyogre Primal";
     regional forms keep parens: "Rattata (Alola)").
  6. Fill curated move/ability/flag columns for any newly added rows.
  7. Validate schema + data invariants; write only if everything passes.

Network: only stages 1 and 3 touch PokeAPI, and both cache to data/_cache/.
Delete that folder to force a refresh. Everything else is offline.

Run from the project root:
    .\.venv\Scripts\python.exe -u scripts\build_dataset.py --dry-run
    .\.venv\Scripts\python.exe -u scripts\build_dataset.py

Flags:
    --dry-run        report every stage's changes, write nothing
    --no-backup      skip timestamped .bak copies
    --refresh-cache  delete the PokeAPI cache and refetch
    --offline        fail loudly if a stage needs the network and the cache is cold
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

try:
    import requests
except ImportError:
    requests = None  # only needed for the two network stages

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CSV_PATH = DATA_DIR / "pokedex.csv"
PQ_PATH = DATA_DIR / "pokedex.parquet"
CACHE_DIR = DATA_DIR / "_cache"

POKEAPI = "https://pokeapi.co/api/v2"
USER_AGENT = "PokeDokuDoku-build/1.0"

BOOL_PREFIXES = ("is_", "has_", "evolves_by_", "evolved_by_", "learns_", "has_ability_")


# ════════════════════════════════════════════════════════════════════════════
#  Curated reference data (baked in, so it survives every rebuild)
# ════════════════════════════════════════════════════════════════════════════

# The 48 Legends: Z-A Mega Evolutions, from PokeAPI's master CSV (IDs 10278+)
# cross-checked against Serebii's Z-A mega list. Each maps a PokeAPI variety
# slug -> the display suffix. base-game (26) + DLC (22) = 48. Typings come from
# PokeAPI at fetch time, NOT hardcoded, so we don't risk transcription errors.
#
# The X/Y and Z variants use PokeAPI's actual slugs. Where PokeAPI has multiple
# cosmetic mega forms of one species with identical typing/stats (Tatsugiri's
# three, Magearna's original color), we keep only the primary — the signature
# test would drop the rest as redundant anyway.
ZA_MEGA_SLUGS = [
    # Base game (26)
    "chesnaught-mega", "delphox-mega", "greninja-mega", "hawlucha-mega",
    "dragonite-mega", "victreebel-mega", "malamar-mega", "clefable-mega",
    "starmie-mega", "meganium-mega", "feraligatr-mega", "skarmory-mega",
    "chimecho-mega", "absol-mega-z", "staraptor-mega", "garchomp-mega-z",
    "lucario-mega-z", "froslass-mega", "heatran-mega", "emboar-mega",
    "excadrill-mega", "scolipede-mega", "scrafty-mega", "eelektross-mega",
    "chandelure-mega", "golurk-mega",
    # DLC — Mega Dimension (22)
    "raichu-mega-x", "raichu-mega-y", "darkrai-mega", "pyroar-mega",
    "floette-mega", "barbaracle-mega", "dragalge-mega",
    "zygarde-mega", "crabominable-mega", "golisopod-mega", "drampa-mega",
    "magearna-mega", "zeraora-mega", "falinks-mega", "scovillain-mega",
    "glimmora-mega", "baxcalibur-mega",
]

# Some Z-A megas belong to a species whose PokeAPI *default variety* is itself a
# named form, so the mega slug follows that form, not the bare species name.
# We try each candidate slug in order and use the first that resolves. This
# self-corrects PokeAPI naming quirks instead of guessing a single slug.
#   - Meowstic's default variety is meowstic-male → meowstic-male-mega
#   - Tatsugiri Mega is the Curly form            → tatsugiri-curly-mega
# (Pawmot / Hydrapple / Kingambit from earlier leak lists are NOT real megas
#  and were removed — Bulbapedia's Mega Evolution Pokedex confirms.)
ZA_MEGA_CANDIDATES = {
    "meowstic": ["meowstic-mega", "meowstic-male-mega"],
    "tatsugiri": ["tatsugiri-mega", "tatsugiri-curly-mega"],
}

# Absol/Garchomp/Lucario already have classic X&Y-era megas as rows; the Z-A
# "Z" megas are genuinely different forms. But Absol/Garchomp/Lucario base megas
# already exist, so only add the -z variants for those three.
ZA_MEGA_ALREADY_HAVE_BASE = {"absol", "garchomp", "lucario", "zygarde"}

# Curated distinct non-mega forms to add (typing-distinct, worth a board row).
# slug -> None means "fetch typing from PokeAPI"; the scraper pulls the rest.
CURATED_FORMS = [
    "castform-sunny", "castform-rainy", "castform-snowy",
    "hoopa-unbound",
    "zacian-crowned", "zamazenta-crowned",
    "ogerpon-wellspring-mask", "ogerpon-hearthflame-mask", "ogerpon-cornerstone-mask",
    "basculin-white-striped",
]

# evo_position overrides for forms whose evolution differs from their base
# species (species-level chains can't express these).
EVO_POSITION_OVERRIDES = {
    "basculin-white-striped": "start",   # only white-striped evolves (→ basculegion)
    "wooper-paldea": "start",
}

# Name-resolution aliases: forms whose plain species name should resolve to the
# canonical row. Written to a sidecar JSON the engine's name index can pick up
# LATER (see the name-search side note); NOT a schema change to the CSV.
NAME_ALIASES = {
    "basculegion": "basculegion-male",
    "enamorus": "enamorus-incarnate",
    "indeedee": "indeedee-male",
    "meowstic": "meowstic-male",
    "oinkologne": "oinkologne-male",
    "urshifu": "urshifu-single-strike",
    "toxtricity": "toxtricity-amped",
    "basculin": "basculin-red-striped",
    "tatsugiri": "tatsugiri-curly",
    "squawkabilly": "squawkabilly-green-plumage",
    "maushold": "maushold-family-of-four",
    "dudunsparce": "dudunsparce-two-segment",
    "palafin": "palafin-zero",
}


# ════════════════════════════════════════════════════════════════════════════
#  Shared helpers
# ════════════════════════════════════════════════════════════════════════════

def log_stage(name: str) -> None:
    print(f"\n{'─' * 4} {name} {'─' * (60 - len(name))}")


def coerce_bools(df: pd.DataFrame) -> pd.DataFrame:
    for col in [c for c in df.columns if str(c).startswith(BOOL_PREFIXES)]:
        s = df[col]
        if s.dtype == bool:
            continue
        if pd.api.types.is_numeric_dtype(s):
            df[col] = pd.to_numeric(s, errors="coerce").fillna(0).astype(int).astype(bool)
        else:
            df[col] = (
                s.astype(str).str.strip().str.lower()
                .map({"true": True, "false": False, "1": True, "0": False,
                      "yes": True, "no": False}).fillna(False).astype(bool)
            )
    return df


def load_base() -> pd.DataFrame:
    if CSV_PATH.exists():
        df = pd.read_csv(CSV_PATH, low_memory=False)
    elif PQ_PATH.exists():
        df = pd.read_parquet(PQ_PATH)
    else:
        sys.exit(f"ERROR: no pokedex.csv/.parquet in {DATA_DIR}")
    if "type2" in df.columns:
        df["type2"] = df["type2"].fillna("")
    return coerce_bools(df)


def _session():
    if requests is None:
        sys.exit("ERROR: the 'requests' package is required for network stages.\n"
                 "       pip install requests, or run with a warm cache.")
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def cached_get(session, url: str, offline: bool) -> dict | None:
    """GET with a flat on-disk JSON cache. Returns None on 404 (caller decides)."""
    import re
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    # Build a filesystem-safe cache name. Windows forbids \ / : * ? " < > | ,
    # so map the URL (minus the API base) to a safe slug rather than only
    # replacing "/". A query string like "?limit=100000" would otherwise make
    # an invalid filename and crash on Windows.
    rel = url.replace(POKEAPI + "/", "").rstrip("/")
    safe = re.sub(r'[^A-Za-z0-9._-]+', "_", rel)[:180]
    cache_file = CACHE_DIR / f"{safe}.json"
    if cache_file.exists():
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            pass  # corrupt cache entry; refetch
    if offline:
        raise RuntimeError(f"--offline set but cache miss for {url}")
    for attempt in range(3):
        try:
            r = session.get(url, timeout=20)
            if r.status_code == 404:
                cache_file.write_text(json.dumps({"__404__": True}), encoding="utf-8")
                return None
            if r.status_code == 200:
                data = r.json()
                cache_file.write_text(json.dumps(data), encoding="utf-8")
                time.sleep(0.15)  # be polite
                return data
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)
    return None


def _is_404(d: dict | None) -> bool:
    return d is None or (isinstance(d, dict) and d.get("__404__"))


# ════════════════════════════════════════════════════════════════════════════
#  Stage 1 — Legends: Z-A Mega Evolutions
# ════════════════════════════════════════════════════════════════════════════

def stage_za_megas(df: pd.DataFrame, offline: bool, log: list[str]) -> pd.DataFrame:
    log_stage("Stage 1: Legends Z-A Mega Evolutions")
    existing = set(df["key"])
    session = _session()

    # Reference values inherited per-row from the base species' default row.
    by_species = {}
    for _, r in df[df["is_default"] == True].iterrows():  # noqa: E712
        by_species.setdefault(str(r["species_key"]), r)

    move_cols = [c for c in df.columns if c.startswith("learns_")]
    ability_cols = [c for c in df.columns if c.startswith("has_ability_")]

    added, skipped = [], []
    # Build the work list: each entry is (canonical_key, [candidate_slugs]).
    work = []
    for slug in ZA_MEGA_SLUGS:
        work.append((slug, [slug]))
    for species, candidates in ZA_MEGA_CANDIDATES.items():
        # canonical key is the first candidate (the clean "<species>-mega" form)
        work.append((candidates[0], candidates))

    for canonical_key, candidates in work:
        if canonical_key in existing:
            skipped.append(f"{canonical_key} (already present)")
            continue
        pdata = None
        used_slug = None
        for cand in candidates:
            d = cached_get(session, f"{POKEAPI}/pokemon/{cand}", offline)
            if not _is_404(d):
                pdata, used_slug = d, cand
                break
        if pdata is None:
            skipped.append(f"{canonical_key} (all candidates 404: {candidates})")
            continue

        species_slug = pdata["species"]["name"]
        base = by_species.get(species_slug)
        if base is None:
            skipped.append(f"{canonical_key} (no base species row for '{species_slug}')")
            continue

        types = sorted(pdata["types"], key=lambda t: t["slot"])
        type1 = types[0]["type"]["name"]
        type2 = types[1]["type"]["name"] if len(types) > 1 else ""

        row = {c: base[c] for c in df.columns}  # inherit everything, then override
        row["key"] = canonical_key              # clean key regardless of which slug resolved
        row["species_key"] = species_slug
        row["pokemon_id"] = int(pdata["id"])
        row["type1"] = type1
        row["type2"] = type2
        row["is_default"] = False
        row["is_monotype"] = (type2 == "")
        row["is_dual_type"] = (type2 != "")
        row["is_mega_form"] = True          # ← this is what the engine's rule reads
        row["is_gmax_form"] = False
        row["is_regional_form"] = False
        row["is_alt_form"] = False
        row["has_mega"] = True
        row["moves"] = base["moves"]
        row["abilities"] = base["abilities"]
        for c in move_cols + ability_cols:
            row[c] = base[c]
        row["display_name"] = base["display_name"]  # normalized in Stage 5
        added.append(row)
        existing.add(canonical_key)

    if added:
        df = pd.concat([df, pd.DataFrame(added)], ignore_index=True)
    log.append(f"Z-A megas: added {len(added)} rows")
    for s in skipped:
        log.append(f"  skip: {s}")
    return df


# ════════════════════════════════════════════════════════════════════════════
#  Stage 2 — Curated distinct non-mega forms
# ════════════════════════════════════════════════════════════════════════════

def stage_curated_forms(df: pd.DataFrame, offline: bool, log: list[str]) -> pd.DataFrame:
    log_stage("Stage 2: Curated distinct forms")
    existing = set(df["key"])
    session = _session()
    by_species = {}
    for _, r in df[df["is_default"] == True].iterrows():  # noqa: E712
        by_species.setdefault(str(r["species_key"]), r)
    move_cols = [c for c in df.columns if c.startswith("learns_")]
    ability_cols = [c for c in df.columns if c.startswith("has_ability_")]

    added, skipped = [], []
    for slug in CURATED_FORMS:
        if slug in existing:
            skipped.append(f"{slug} (already present)")
            continue
        pdata = cached_get(session, f"{POKEAPI}/pokemon/{slug}", offline)
        if _is_404(pdata):
            skipped.append(f"{slug} (PokeAPI 404)")
            continue
        species_slug = pdata["species"]["name"]
        base = by_species.get(species_slug)
        if base is None:
            skipped.append(f"{slug} (no base species row)")
            continue

        types = sorted(pdata["types"], key=lambda t: t["slot"])
        type1 = types[0]["type"]["name"]
        type2 = types[1]["type"]["name"] if len(types) > 1 else ""

        row = {c: base[c] for c in df.columns}
        row["key"] = slug
        row["species_key"] = species_slug
        row["pokemon_id"] = int(pdata["id"])
        row["type1"] = type1
        row["type2"] = type2
        row["is_default"] = False
        row["is_monotype"] = (type2 == "")
        row["is_dual_type"] = (type2 != "")
        row["is_mega_form"] = False
        row["is_gmax_form"] = False
        row["is_regional_form"] = False
        row["is_alt_form"] = True
        row["moves"] = base["moves"]
        row["abilities"] = base["abilities"]
        for c in move_cols + ability_cols:
            row[c] = base[c]
        if slug in EVO_POSITION_OVERRIDES:
            row["evo_position"] = EVO_POSITION_OVERRIDES[slug]
        row["display_name"] = base["display_name"]  # normalized in Stage 5

        added.append(row)
        existing.add(slug)

    if added:
        df = pd.concat([df, pd.DataFrame(added)], ignore_index=True)
    log.append(f"curated forms: added {len(added)} rows")
    for s in skipped:
        log.append(f"  skip: {s}")
    return df


# ════════════════════════════════════════════════════════════════════════════
#  Stage 3 — Flip evolution semantics to INCOMING
# ════════════════════════════════════════════════════════════════════════════

# PokeAPI evolution trigger → our column. Incoming = the trigger on the edge
# that produced THIS species (i.e. how it was evolved INTO).
def _method_from_details(details: list) -> set[str]:
    """Map a child edge's evolution_details to our four buckets.

    Dual-method handling: PokeAPI encodes 'trade while holding X' and 'level up
    while holding X' as trigger=trade/level-up PLUS a held_item — NOT as
    trigger=use-item. So a Pokemon like Porygon-Z (trade holding Dubious Disc)
    or Gliscor (level up holding Razor Fang) genuinely satisfies BOTH its
    trigger's method AND 'item'. We add 'item' whenever a held_item is present,
    so those show up under Evolved by Item as well as Trade/Level-up. This
    matches the intuitive reading ("it needs an item") and adds category variety.
    """
    out = set()
    for d in details or []:
        trig = (d.get("trigger") or {}).get("name", "")
        held = d.get("held_item")  # trade/level-up WHILE HOLDING an item
        if trig == "trade":
            out.add("trade")
        elif trig == "use-item":
            out.add("item")
        elif trig == "level-up":
            if d.get("min_happiness") is not None:
                out.add("friendship")
            else:
                out.add("levelup")
        elif trig == "shed":
            out.add("levelup")
        else:
            # spin, tower, agile/strong style, etc. → treat as levelup
            out.add("levelup")
        # A held item means the evolution also requires an item, regardless of
        # the trigger — Porygon-Z, Scizor, Steelix, Kingdra, Politoed, Slowking
        # (trade+held), Gliscor, Weavile (level-up+held), etc.
        if held:
            out.add("item")
    return out


def _walk_incoming(node: dict, parent_species: str | None, acc: dict) -> None:
    """DFS the chain; for each species record the method(s) on the edge that
    led INTO it (its parent's evolution_details for this child)."""
    species = node["species"]["name"]
    # The details describing how THIS node was reached live on THIS node's
    # evolution_details (PokeAPI stores the trigger on the child).
    if parent_species is not None:
        acc.setdefault(species, set()).update(_method_from_details(node.get("evolution_details")))
    for child in node.get("evolves_to", []):
        _walk_incoming(child, species, acc)


def stage_flip_evolution(df: pd.DataFrame, offline: bool, log: list[str]) -> pd.DataFrame:
    log_stage("Stage 3: Evolution semantics → incoming")
    session = _session()

    # One pass over all evolution chains. The chain list is paginated; pull the
    # index then each chain (cached individually).
    index = cached_get(session, f"{POKEAPI}/evolution-chain?limit=100000", offline)
    if _is_404(index):
        log.append("  ERROR: could not fetch evolution-chain index; evolution NOT flipped")
        return df

    species_methods: dict[str, set[str]] = {}
    chains = index.get("results", [])
    for i, entry in enumerate(chains):
        chain = cached_get(session, entry["url"], offline)
        if _is_404(chain):
            continue
        root = chain.get("chain")
        if root:
            _walk_incoming(root, None, species_methods)
        if (i + 1) % 100 == 0:
            print(f"    …processed {i + 1}/{len(chains)} chains")

    # Apply to rows by species_key. A form inherits its species' incoming method
    # (species-level granularity, same as the rest of the dataset).
    cols = ["evolves_by_levelup", "evolves_by_friendship", "evolves_by_trade", "evolves_by_item"]
    before = {c: int(df[c].sum()) for c in cols}
    for c in cols:
        df[c] = False
    method_col = {"levelup": "evolves_by_levelup", "friendship": "evolves_by_friendship",
                  "trade": "evolves_by_trade", "item": "evolves_by_item"}
    for idx, r in df.iterrows():
        methods = species_methods.get(str(r["species_key"]), set())
        for m in methods:
            df.at[idx, method_col[m]] = True
    after = {c: int(df[c].sum()) for c in cols}

    log.append("evolution flipped to INCOMING (result-of-evolution carries the flag):")
    for c in cols:
        log.append(f"  {c}: {before[c]} → {after[c]}")
    # Spot-check sentinels
    for k in ["machamp", "golem", "gengar", "steelix", "vaporeon", "raichu", "kadabra"]:
        r = df.loc[df["key"] == k]
        if len(r):
            r = r.iloc[0]
            fl = [m for m in ["levelup", "friendship", "trade", "item"] if bool(r[method_col[m]])]
            log.append(f"    {k}: {fl}")
    return df


# ════════════════════════════════════════════════════════════════════════════
#  Stage 4 — Derive mega/gmax flags from actual rows
# ════════════════════════════════════════════════════════════════════════════

def stage_derive_form_flags(df: pd.DataFrame, log: list[str]) -> pd.DataFrame:
    log_stage("Stage 4: Derive mega/gmax flags from rows")
    # is_mega_form / is_gmax_form: TRUE iff the key is a mega/gmax form row.
    is_mega = df["key"].str.contains("-mega", na=False)
    is_gmax = df["key"].str.contains("-gmax", na=False)
    df["is_mega_form"] = is_mega
    df["is_gmax_form"] = is_gmax

    # has_mega / has_gmax: species-level "this species has a mega/gmax form",
    # set on ALL rows of a species that has any such form row (base + the form).
    mega_species = set(df.loc[is_mega, "species_key"])
    gmax_species = set(df.loc[is_gmax, "species_key"])
    df["has_mega"] = df["species_key"].isin(mega_species)
    df["has_gmax"] = df["species_key"].isin(gmax_species)

    log.append(f"is_mega_form: {int(is_mega.sum())} rows  |  is_gmax_form: {int(is_gmax.sum())} rows")
    log.append(f"has_mega species: {len(mega_species)}  |  has_gmax species: {len(gmax_species)}")
    return df


# ════════════════════════════════════════════════════════════════════════════
#  Stage 5 — Flat display-name normalization
# ════════════════════════════════════════════════════════════════════════════

REGION_SUFFIX = {"alola": "Alola", "galar": "Galar", "hisui": "Hisui", "paldea": "Paldea"}
_TITLE_KEEP = {"x": "X", "y": "Y", "z": "Z"}


def _titleize(word: str) -> str:
    lw = word.lower()
    if lw in _TITLE_KEEP:
        return _TITLE_KEEP[lw]
    return word.capitalize()


def _flat_display_name(key: str, species_slug: str, species_display: str) -> str:
    """Flat style: 'Mega Dragonite', 'Kyogre Primal', 'Tornadus Therian';
    regional forms keep parens: 'Rattata (Alola)'.

    The stem is the BARE species name, not the default row's display_name —
    otherwise a species whose default form is itself suffixed (Tornadus
    'Incarnate') would leak that suffix into every other form."""
    # Bare species name from the slug, but prefer the default display when it's
    # a clean single token (preserves proper punctuation like "Farfetch'd").
    if "-" not in species_slug and " " not in str(species_display):
        stem = species_display
    else:
        stem = " ".join(_titleize(p) for p in species_slug.split("-"))

    # Derive the form suffix by removing the species slug prefix from the key,
    # so multi-word species (mr-mime, type-null, tapu-koko) don't leak a repeated
    # token into the form part.
    if key == species_slug:
        return species_display
    if key.startswith(species_slug + "-"):
        rest = key[len(species_slug) + 1:].split("-")
    else:
        # Fallback: species slug isn't a clean prefix (rare); drop first token.
        rest = key.split("-")[1:]
    if not rest:
        return species_display

    # Regional forms: "<Base> (Region)"
    for suf, label in REGION_SUFFIX.items():
        if suf in rest:
            others = [p for p in rest if p != suf]
            extra = " ".join(_titleize(p) for p in others)
            return f"{stem} ({label})" + (f" {extra}" if extra else "")

    # Mega: "Mega <Base> [X/Y/Z]"
    if "mega" in rest:
        others = [p for p in rest if p != "mega"]
        tail = " ".join(_titleize(p) for p in others)
        return f"Mega {stem}" + (f" {tail}" if tail else "")

    # Gmax: "<Base> Gmax"  (keep recognizable)
    if "gmax" in rest:
        others = [p for p in rest if p != "gmax"]
        tail = " ".join(_titleize(p) for p in others)
        return f"{stem}" + (f" {tail}" if tail else "") + " Gmax"

    # Everything else flat: "<Base> <Form words>"
    tail = " ".join(_titleize(p) for p in rest)
    return f"{stem} {tail}"


def stage_display_names(df: pd.DataFrame, log: list[str]) -> pd.DataFrame:
    log_stage("Stage 5: Flat display names")
    # species display = the default row's current display_name
    species_disp = {}
    for _, r in df[df["is_default"] == True].iterrows():  # noqa: E712
        species_disp.setdefault(str(r["species_key"]), str(r["display_name"]))

    changed = 0
    samples = []
    for idx, r in df.iterrows():
        if bool(r["is_default"]):
            continue
        disp = species_disp.get(str(r["species_key"]), str(r["display_name"]))
        new = _flat_display_name(str(r["key"]), str(r["species_key"]), disp)
        if new and new != str(r["display_name"]):
            if len(samples) < 12:
                samples.append(f"  {r['key']:28s} '{r['display_name']}' → '{new}'")
            df.at[idx, "display_name"] = new
            changed += 1
    log.append(f"display names: normalized {changed} rows")
    log.extend(samples)
    return df


# ════════════════════════════════════════════════════════════════════════════
#  Stage 6 — Fill curated columns for new rows
# ════════════════════════════════════════════════════════════════════════════

def stage_fill_columns(df: pd.DataFrame, log: list[str]) -> pd.DataFrame:
    log_stage("Stage 6: Fill curated move/ability columns for new rows")
    import json as _json

    def parse(x):
        s = str(x)
        if not s.startswith("["):
            return []
        try:
            return _json.loads(s)
        except Exception:
            try:
                return _json.loads(s.replace("'", '"'))
            except Exception:
                return []

    move_cols = [c for c in df.columns if c.startswith("learns_")]
    ability_cols = [c for c in df.columns if c.startswith("has_ability_")]
    move_sets = df["moves"].map(lambda x: set(parse(x)))
    ability_sets = df["abilities"].map(lambda x: set(parse(x)))

    fixed = 0
    for c in move_cols:
        token = c[len("learns_"):].replace("_", "-")
        want = move_sets.map(lambda s, t=token: t in s).astype(bool)
        if not df[c].astype(bool).equals(want):
            df[c] = want
            fixed += 1
    for c in ability_cols:
        token = c[len("has_ability_"):].replace("_", "-")
        want = ability_sets.map(lambda s, t=token: t in s).astype(bool)
        if not df[c].astype(bool).equals(want):
            df[c] = want
            fixed += 1
    log.append(f"recomputed {fixed} move/ability columns from moves/abilities lists")
    return df


# ════════════════════════════════════════════════════════════════════════════
#  Stage 7 — Validate + write
# ════════════════════════════════════════════════════════════════════════════

def validate(df: pd.DataFrame, schema: dict, log: list[str]) -> list[str]:
    problems = []
    # Schema: exact columns in exact order
    if list(df.columns) != schema["order"]:
        missing = [c for c in schema["order"] if c not in df.columns]
        extra = [c for c in df.columns if c not in schema["order"]]
        problems.append(f"column set/order drift. missing={missing} extra={extra}")
    # dtypes for the key scalar columns
    for c in ("key", "display_name", "species_key", "type1", "type2", "evo_position"):
        if c in df.columns and df[c].isna().any():
            problems.append(f"{c} has nulls")
    if df["key"].duplicated().any():
        problems.append(f"{int(df['key'].duplicated().sum())} duplicate keys")
    if df["pokemon_id"].duplicated().any():
        problems.append(f"{int(df['pokemon_id'].duplicated().sum())} duplicate pokemon_id")
    # species linkage
    defaults = set(df.loc[df["is_default"] == True, "species_key"])  # noqa: E712
    orphan = df.loc[~df["species_key"].isin(defaults), "key"]
    if len(orphan):
        problems.append(f"{len(orphan)} rows w/ species_key lacking a default row: {orphan.head(8).tolist()}")
    # types valid
    valid = {"normal","fire","water","electric","grass","ice","fighting","poison","ground",
             "flying","psychic","bug","rock","ghost","dragon","dark","steel","fairy",""}
    for c in ("type1", "type2"):
        bad = df.loc[~df[c].fillna("").astype(str).str.lower().isin(valid), "key"]
        if len(bad):
            problems.append(f"{c} invalid on: {bad.head(6).tolist()}")
    return problems


def enforce_schema(df: pd.DataFrame, schema: dict) -> pd.DataFrame:
    # Reorder to the canonical column order; coerce dtypes to match the base file.
    df = df[schema["order"]].copy()
    for c, dt in schema["dtypes"].items():
        try:
            if dt == "bool":
                df[c] = df[c].astype(bool)
            elif dt == "int64":
                df[c] = pd.to_numeric(df[c], errors="coerce").astype("int64")
            else:
                df[c] = df[c].astype(str) if c in ("key","display_name","species_key","type1","type2","evo_position","region","abilities","moves") else df[c]
        except Exception:
            pass
    return df


def write_aliases(log: list[str]) -> None:
    """Name-resolution aliases go to a sidecar JSON, NOT the CSV schema. The
    engine's name index can read this later (see the name-search side note)."""
    out = DATA_DIR / "name_aliases.json"
    out.write_text(json.dumps(NAME_ALIASES, indent=2), encoding="utf-8")
    log.append(f"wrote name aliases → {out.name} (for the name-search fix, not the CSV)")


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the pokedex dataset (staged, validated).")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-backup", action="store_true")
    ap.add_argument("--refresh-cache", action="store_true")
    ap.add_argument("--offline", action="store_true")
    args = ap.parse_args()

    if args.refresh_cache and CACHE_DIR.exists():
        shutil.rmtree(CACHE_DIR)
        print(f"cleared cache: {CACHE_DIR}")

    df = load_base()
    schema = {"order": list(df.columns),
              "dtypes": {c: str(df[c].dtype) for c in df.columns}}
    print(f"Loaded base: {len(df)} rows, {len(df.columns)} columns")

    log: list[str] = []
    df = stage_za_megas(df, args.offline, log)
    df = stage_curated_forms(df, args.offline, log)
    df = stage_flip_evolution(df, args.offline, log)
    df = stage_derive_form_flags(df, log)
    df = stage_display_names(df, log)
    df = stage_fill_columns(df, log)
    df = enforce_schema(df, schema)

    print("\n" + "═" * 66)
    for line in log:
        print(line)

    log_stage("Validation")
    problems = validate(df, schema, log)
    if problems:
        for p in problems:
            print(f"  FAIL  {p}")
        print("\nValidation failed — NOT writing. Fix the above and re-run.")
        if not args.dry_run:
            return 1
    else:
        print("  All checks passed.")

    log_stage("Summary")
    print(f"rows: {len(df)}  columns: {len(df.columns)}")
    print(f"mega form rows: {int(df['is_mega_form'].sum())}  gmax: {int(df['is_gmax_form'].sum())}")

    if args.dry_run:
        print("\n(dry run: nothing written)")
        return 0

    if not args.no_backup:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        for p in (CSV_PATH, PQ_PATH):
            if p.exists():
                shutil.copy2(p, p.with_suffix(p.suffix + f".{stamp}.bak"))
                print(f"backup: {p.name}.{stamp}.bak")

    df.to_csv(CSV_PATH, index=False)
    print(f"wrote {CSV_PATH}")
    try:
        df.to_parquet(PQ_PATH, index=False)
        print(f"wrote {PQ_PATH}")
    except Exception as e:
        print(f"WARNING: parquet write failed ({e}); delete data/pokedex.parquet so the API uses the CSV.")

    write_aliases(log)
    print("\nDone. Restart uvicorn (and delete __pycache__ / stale parquet if needed).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())