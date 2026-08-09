import json
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Optional
import argparse
import re
from collections import Counter

import pandas as pd
import requests


OUT_DIR = Path(__file__).resolve().parents[1] / "data"
OUT_DIR.mkdir(parents=True, exist_ok=True)

POKEAPI = "https://pokeapi.co/api/v2"

_CACHE: Dict[str, dict] = {}


def get(url: str) -> dict:
    if url in _CACHE:
        return _CACHE[url]
    last_err: Exception | None = None
    for attempt in range(7):
        try:
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            data = r.json()
            _CACHE[url] = data
            return data
        except Exception as e:
            last_err = e
            # Exponential backoff with a small floor.
            sleep_s = min(20.0, 0.75 * (2**attempt))
            time.sleep(sleep_s)

    raise last_err  # type: ignore[misc]


def slug_to_display(name: str) -> str:
    # e.g. "mr-mime" -> "Mr Mime", "type-null" -> "Type Null"
    parts = name.replace("-", " ").split()
    return " ".join(p.capitalize() for p in parts)


def safe_feature_token(s: str) -> str:
    s = (s or "").strip().lower().replace("-", "_")
    s = re.sub(r"[^a-z0-9_]+", "", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "x"


def _parse_json_list(x: Any) -> list[str]:
    if x is None:
        return []
    if isinstance(x, float) and pd.isna(x):
        return []
    if isinstance(x, list):
        return [str(v) for v in x if v]
    try:
        v = json.loads(str(x))
        if isinstance(v, list):
            return [str(i) for i in v if i]
        return []
    except Exception:
        return []


def add_onehot_topk(
    df: pd.DataFrame,
    *,
    source_col: str,
    prefix: str,
    top_k: int,
    min_freq: int,
) -> None:
    """Add boolean one-hot columns for the top-K most common items in a JSON-list column.

    Example: source_col='moves', prefix='learns_' -> learns_protect, learns_rest, ...
    """
    if top_k <= 0:
        return
    if source_col not in df.columns:
        return

    counts: Counter[str] = Counter()
    parsed: list[list[str]] = []
    for x in df[source_col].tolist():
        items = sorted(set(_parse_json_list(x)))
        parsed.append(items)
        for it in items:
            counts[it] += 1

    vocab = [it for it, n in counts.most_common() if n >= int(min_freq)]
    vocab = vocab[: int(top_k)]
    if not vocab:
        return

    # Make safe, unique column names.
    safe_map: Dict[str, str] = {}
    used: set[str] = set()
    for it in vocab:
        tok = safe_feature_token(it)
        col = f"{prefix}{tok}"
        if col in used:
            i = 2
            while f"{col}_{i}" in used:
                i += 1
            col = f"{col}_{i}"
        used.add(col)
        safe_map[it] = col

    # Initialize all selected columns to False.
    for col in safe_map.values():
        if col not in df.columns:
            df[col] = False

    # Fill True where applicable.
    for row_i, items in enumerate(parsed):
        for it in items:
            col = safe_map.get(it)
            if col is not None:
                df.at[row_i, col] = True


def roman_to_int(s: str) -> int | None:
    if not s:
        return None
    s = s.lower()
    vals = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
    total = 0
    prev = 0
    for ch in reversed(s):
        v = vals.get(ch)
        if v is None:
            return None
        if v < prev:
            total -= v
        else:
            total += v
            prev = v
    return total


GEN_TO_REGION = {
    1: "Kanto",
    2: "Johto",
    3: "Hoenn",
    4: "Sinnoh",
    5: "Unova",
    6: "Kalos",
    7: "Alola",
    8: "Galar",
    9: "Paldea + DLC",
}


def _walk_evo_chain(node: dict, parent_species: Optional[str], depth: int, out: Dict[str, dict]) -> None:
    species_name = node["species"]["name"]
    children = []
    for child in node.get("evolves_to") or []:
        children.append(
            {
                "species": (child.get("species") or {}).get("name"),
                "evolution_details": child.get("evolution_details") or [],
            }
        )
    out[species_name] = {
        "parent": parent_species,
        "depth": depth,
        "evolution_details": node.get("evolution_details") or [],
        "child_count": len(node.get("evolves_to") or []),
        "children": children,
    }
    for child in node.get("evolves_to") or []:
        _walk_evo_chain(child, species_name, depth + 1, out)


def parse_evolution_chain(chain_json: dict) -> tuple[Dict[str, dict], bool]:
    """Return (species_name -> info, has_any_branching)."""
    node_map: Dict[str, dict] = {}
    root = chain_json.get("chain")
    if not root:
        return node_map, False

    # Branching if any node has >1 evolves_to.
    has_branch = False
    stack: list[dict] = [root]
    while stack:
        n = stack.pop()
        children = n.get("evolves_to") or []
        if len(children) > 1:
            has_branch = True
        stack.extend(children)

    _walk_evo_chain(root, None, 0, node_map)
    return node_map, has_branch


def evolution_flags_for_species(
    species_name: str,
    is_baby: bool,
    node_map: Dict[str, dict],
    has_branching: bool,
) -> dict:
    """Compute evolution metadata for one species.

    Note: We only persist outgoing method flags (evolves_by_*). We intentionally
    do not persist incoming method flags (evolved_by_*) to avoid confusion.
    """
    info = node_map.get(species_name)
    if not info:
        # Unknown (shouldn't happen), keep defaults.
        return {
            "evo_position": "unknown",
            "is_branched_line": bool(has_branching),
            "evolves_by_levelup": False,
            "evolves_by_friendship": False,
            "evolves_by_trade": False,
            "evolves_by_item": False,
        }

    parent = info.get("parent")
    child_count = int(info.get("child_count") or 0)

    if is_baby:
        evo_position = "premature"
    elif parent is None and child_count == 0:
        evo_position = "none"
    elif parent is None:
        evo_position = "start"
    elif child_count == 0:
        evo_position = "final"
    else:
        evo_position = "middle"

    # Outgoing evolution method (how this species evolves *into* something else).
    child_details: list[dict] = []
    for child in info.get("children") or []:
        for d in child.get("evolution_details") or []:
            if isinstance(d, dict):
                child_details.append(d)
    child_triggers = {str((d.get("trigger") or {}).get("name") or "") for d in child_details}
    child_min_happiness = any(d.get("min_happiness") is not None for d in child_details)

    return {
        "evo_position": evo_position,
        "is_branched_line": bool(has_branching),
        "evolves_by_levelup": "level-up" in child_triggers,
        "evolves_by_friendship": bool(child_min_happiness),
        "evolves_by_trade": "trade" in child_triggers,
        "evolves_by_item": "use-item" in child_triggers,
    }


_EVOLVES_BY_COLS = [
    "evolves_by_levelup",
    "evolves_by_friendship",
    "evolves_by_trade",
    "evolves_by_item",
]

_EVOLVED_BY_COLS = [
    "evolved_by_levelup",
    "evolved_by_friendship",
    "evolved_by_trade",
    "evolved_by_item",
]


def _evolves_by_from_info(info: dict) -> dict:
    """Compute outgoing evolution method flags from a parse_evolution_chain node info."""
    child_details: list[dict] = []
    for child in info.get("children") or []:
        for d in child.get("evolution_details") or []:
            if isinstance(d, dict):
                child_details.append(d)
    child_triggers = {str((d.get("trigger") or {}).get("name") or "") for d in child_details}
    child_min_happiness = any(d.get("min_happiness") is not None for d in child_details)

    return {
        "evolves_by_levelup": "level-up" in child_triggers,
        "evolves_by_friendship": bool(child_min_happiness),
        "evolves_by_trade": "trade" in child_triggers,
        "evolves_by_item": "use-item" in child_triggers,
    }


def augment_evolution_flags(
    df: pd.DataFrame,
    limit_species: int | None = None,
    print_every: int = 25,
) -> None:
    """Augment an existing dataset with evolves_by_* flags.

    This re-fetches evolution chain metadata from PokéAPI but does NOT re-fetch
    moves/abilities lists.
    """

    if "species_key" not in df.columns:
        raise ValueError("augment_evolution_flags requires a 'species_key' column")

    # Ensure outgoing columns exist.
    for col in _EVOLVES_BY_COLS:
        if col not in df.columns:
            df[col] = False

    # If the old incoming columns exist in an older dataset, drop them.
    # They aren't used by the game logic anymore and are confusing.
    drop_cols = [c for c in _EVOLVED_BY_COLS if c in df.columns]
    if drop_cols:
        df.drop(columns=drop_cols, inplace=True)

    flags_by_species: Dict[str, dict] = {}

    # Fast path: iterate evolution-chain resources directly.
    # This avoids fetching /pokemon-species for every species_key.
    chains = get(f"{POKEAPI}/evolution-chain?limit=200000").get("results") or []
    if limit_species is not None:
        chains = chains[: max(0, int(limit_species))]

    total = len(chains)
    print(f"Evolution augment: starting ({total} evolution chains)...")
    print_every = max(1, int(print_every or 1))
    for idx, item in enumerate(chains, start=1):
        try:
            evo_chain = get(str(item.get("url") or ""))
            node_map, _has_branching = parse_evolution_chain(evo_chain)
            for sk, info in node_map.items():
                if sk not in flags_by_species:
                    flags_by_species[sk] = {k: False for k in _EVOLVES_BY_COLS}
                flags = _evolves_by_from_info(info)
                # OR across multiple chains shouldn't happen, but OR is safe.
                for k in _EVOLVES_BY_COLS:
                    flags_by_species[sk][k] = bool(flags_by_species[sk].get(k)) or bool(flags.get(k))
        except Exception:
            # Skip bad chains; defaults remain False.
            pass

        if idx == 1 or idx % print_every == 0 or idx == total:
            print(f"Evolution augment: {idx}/{total} chains processed...")

    for col in _EVOLVES_BY_COLS:
        df[col] = (
            df["species_key"]
            .astype(str)
            .map(lambda s, c=col: flags_by_species.get(s, {}).get(c, False))
            .fillna(False)
            .astype(bool)
        )


def main(
    limit_species: int | None = None,
    default_only: bool = False,
    onehot_moves: int = 0,
    onehot_abilities: int = 0,
    onehot_min_freq: int = 30,
    augment_existing: bool = False,
    augment_evolution: bool = False,
    augment_evolution_limit: int | None = None,
    augment_evolution_print_every: int = 25,
):
    if augment_existing:
        out_parquet = OUT_DIR / "pokedex.parquet"
        out_csv = OUT_DIR / "pokedex.csv"

        if out_parquet.exists():
            df = pd.read_parquet(out_parquet)
        elif out_csv.exists():
            df = pd.read_csv(out_csv)
        else:
            raise FileNotFoundError(f"No existing pokedex.parquet or pokedex.csv found in {OUT_DIR}")

        add_onehot_topk(
            df,
            source_col="moves",
            prefix="learns_",
            top_k=int(onehot_moves or 0),
            min_freq=int(onehot_min_freq or 0),
        )
        add_onehot_topk(
            df,
            source_col="abilities",
            prefix="has_ability_",
            top_k=int(onehot_abilities or 0),
            min_freq=int(onehot_min_freq or 0),
        )

        if not augment_evolution:
            print(
                "NOTE: --augment-existing ran WITHOUT --augment-evolution; "
                "evolves_by_* columns were not recomputed."
            )

        if augment_evolution:
            augment_evolution_flags(
                df,
                limit_species=augment_evolution_limit,
                print_every=augment_evolution_print_every,
            )

            try:
                counts = {c: int(df[c].fillna(False).astype(bool).sum()) for c in _EVOLVES_BY_COLS if c in df.columns}
                print(f"Evolution augment: true counts {counts}")
            except Exception:
                pass

        df.to_parquet(out_parquet, index=False)
        df.to_csv(out_csv, index=False)
        print(f"Augmented: {out_parquet}")
        print(f"Augmented: {out_csv}")
        print(f"Rows: {len(df)}")
        return

    # Pull all pokemon species (this is the canonical place for legendary/mythical/baby)
    species_list = get(f"{POKEAPI}/pokemon-species?limit=200000")["results"]
    if limit_species is not None:
        species_list = species_list[: max(0, int(limit_species))]

    rows = []
    total = len(species_list)

    for idx, item in enumerate(species_list, start=1):
        species = get(item["url"])

        # species["varieties"] contains the actual pokemon forms in /pokemon/
        # We'll include each varie
        # Above is a quick fallback; we’ll replace with a clean mapping in a later polish pass.

        gen_name = species["generation"]["name"]  # "generation-ix"
        roman = gen_name.split("-")[-1]
        gen_num = roman_to_int(roman)

        base_flags = {
            "is_legendary": bool(species.get("is_legendary")),
            "is_mythical": bool(species.get("is_mythical")),
            "is_baby": bool(species.get("is_baby")),
        }

        # "Genus" is human-facing category text; fossil species are reliably tagged here.
        genus_en = ""
        for g in species.get("genera") or []:
            if (g.get("language") or {}).get("name") == "en":
                genus_en = str(g.get("genus") or "")
                break

        is_fossil = "fossil" in genus_en.lower()

        # Evolution chain metadata (computed once per species)
        evo_url = (species.get("evolution_chain") or {}).get("url")
        node_map: Dict[str, dict] = {}
        has_branching = False
        if evo_url:
            evo_chain = get(evo_url)
            node_map, has_branching = parse_evolution_chain(evo_chain)
        evo_flags = evolution_flags_for_species(
            species_name=species["name"],
            is_baby=base_flags["is_baby"],
            node_map=node_map,
            has_branching=has_branching,
        )

        # Form metadata derived from varieties.
        variety_names = [v.get("pokemon", {}).get("name", "") for v in (species.get("varieties") or [])]
        has_mega = any("-mega" in n for n in variety_names)
        has_gmax = any(n.endswith("-gmax") for n in variety_names)

        for v in species.get("varieties", []):
            p = get(v["pokemon"]["url"])  # /pokemon/<id or name> gives types

            if default_only:
                name = str(p.get("name") or "")
                is_mega_form = "-mega" in name
                is_gmax_form = name.endswith("-gmax")
                if not (bool(p.get("is_default")) or is_mega_form or is_gmax_form):
                    continue

            types_sorted = sorted(
                [(t["slot"], t["type"]["name"]) for t in p.get("types", [])],
                key=lambda x: x[0],
            )
            type1 = types_sorted[0][1] if len(types_sorted) >= 1 else ""
            type2 = types_sorted[1][1] if len(types_sorted) >= 2 else ""

            key = p["name"]  # stable slug key
            display = slug_to_display(key)
            pokemon_id = p["id"]

            is_default = bool(p.get("is_default"))
            is_mega_form = "-mega" in key
            is_gmax_form = key.endswith("-gmax")
            region = GEN_TO_REGION.get(gen_num, "")

            abilities = sorted({a.get("ability", {}).get("name", "") for a in (p.get("abilities") or []) if a.get("ability")})
            abilities = [a for a in abilities if a]
            moves = sorted({m.get("move", {}).get("name", "") for m in (p.get("moves") or []) if m.get("move")})
            moves = [m for m in moves if m]



            rows.append(
                {
                    "key": key,
                    "display_name": display,
                    "species_key": species["name"],
                    "pokemon_id": pokemon_id,
                    "gen": gen_num,
                    "region": region,
                    "type1": type1,
                    "type2": type2,
                    "is_default": is_default,
                    "is_monotype": type2 == "",
                    "is_dual_type": type2 != "",
                    "has_mega": bool(has_mega),
                    "has_gmax": bool(has_gmax),
                    "is_mega_form": bool(is_mega_form),
                    "is_gmax_form": bool(is_gmax_form),
                    "is_fossil": bool(is_fossil),
                    "abilities": json.dumps(abilities, separators=(",", ":")),
                    "moves": json.dumps(moves, separators=(",", ":")),
                    # Placeholders for categories not directly available from PokeAPI in a robust way.
                    # (Keep these as False until you decide on a curated source of truth.)
                    "is_first_partner": False,
                    "is_paradox": False,
                    "is_ultra_beast": False,
                    **evo_flags,
                    **base_flags,
                }
            )

        # be polite: REST has no auth and docs encourage not hammering them
        if idx % 25 == 0:
            time.sleep(0.25)

        if idx % 250 == 0:
            print(f"Processed {idx}/{total} species...")

    df = pd.DataFrame(rows)

    # Basic cleanup
    df["type2"] = df["type2"].fillna("")
    df = df.drop_duplicates(subset=["key"]).reset_index(drop=True)

    # Optional: add fast boolean features for the most common moves/abilities.
    # (Avoids exploding to hundreds/thousands of columns.)
    add_onehot_topk(
        df,
        source_col="moves",
        prefix="learns_",
        top_k=int(onehot_moves or 0),
        min_freq=int(onehot_min_freq or 0),
    )
    add_onehot_topk(
        df,
        source_col="abilities",
        prefix="has_ability_",
        top_k=int(onehot_abilities or 0),
        min_freq=int(onehot_min_freq or 0),
    )

    # Ensure expected new columns exist even if some rows missed them.
    for col, default in {
        "region": "",
        "is_default": False,
        "is_monotype": False,
        "is_dual_type": False,
        "has_mega": False,
        "has_gmax": False,
        "is_mega_form": False,
        "is_gmax_form": False,
        "evo_position": "unknown",
        "is_branched_line": False,
        "evolves_by_levelup": False,
        "evolves_by_friendship": False,
        "evolves_by_trade": False,
        "evolves_by_item": False,
        "is_fossil": False,
        "abilities": "[]",
        "moves": "[]",
        "is_first_partner": False,
        "is_paradox": False,
        "is_ultra_beast": False,
    }.items():
        if col not in df.columns:
            df[col] = default

    out_parquet = OUT_DIR / "pokedex.parquet"
    out_csv = OUT_DIR / "pokedex.csv"
    df.to_parquet(out_parquet, index=False)
    df.to_csv(out_csv, index=False)

    print(f"Saved: {out_parquet}")
    print(f"Saved: {out_csv}")
    print(f"Rows: {len(df)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build local pokedex dataset from PokeAPI")
    parser.add_argument(
        "--limit-species",
        type=int,
        default=None,
        help="For quick dev runs: only process the first N species.",
    )
    parser.add_argument(
        "--default-only",
        action="store_true",
        help="Only include default forms (recommended for gameplay datasets).",
    )
    parser.add_argument(
        "--augment-existing",
        action="store_true",
        help="Skip PokeAPI fetch; load existing data/pokedex and only add one-hot move/ability columns.",
    )
    parser.add_argument(
        "--augment-evolution",
        action="store_true",
        help="With --augment-existing: re-fetch evolution chains and add evolves_by_* columns.",
    )
    parser.add_argument(
        "--augment-evolution-limit",
        type=int,
        default=None,
        help="With --augment-evolution: only process the first N evolution chains (debug/smoke testing).",
    )
    parser.add_argument(
        "--augment-evolution-print-every",
        type=int,
        default=25,
        help="With --augment-evolution: print progress every N chains (default: 25).",
    )
    parser.add_argument(
        "--onehot-moves",
        type=int,
        default=0,
        help="Add boolean columns for the top-K most common moves (e.g. learns_protect). 0 disables.",
    )
    parser.add_argument(
        "--onehot-abilities",
        type=int,
        default=0,
        help="Add boolean columns for the top-K most common abilities (e.g. has_ability_intimidate). 0 disables.",
    )
    parser.add_argument(
        "--onehot-min-freq",
        type=int,
        default=30,
        help="Minimum number of Pokémon that must have the move/ability to be one-hot encoded.",
    )
    args = parser.parse_args()
    main(
        limit_species=args.limit_species,
        default_only=args.default_only,
        onehot_moves=args.onehot_moves,
        onehot_abilities=args.onehot_abilities,
        onehot_min_freq=args.onehot_min_freq,
        augment_existing=args.augment_existing,
        augment_evolution=args.augment_evolution,
        augment_evolution_limit=args.augment_evolution_limit,
        augment_evolution_print_every=args.augment_evolution_print_every,
    )