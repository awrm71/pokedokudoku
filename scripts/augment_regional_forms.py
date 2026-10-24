"""
augment_regional_forms.py
─────────────────────────
Adds missing Pokémon forms to data/pokedex.csv and data/pokedex.parquet:
  • Alolan forms (18 Pokémon)
  • Galarian forms (19 Pokémon)
  • Hisuian forms (16 Pokémon)
  • Primal Kyogre and Primal Groudon
  • Urshifu Rapid Strike

Run from the project root:
    .\.venv\Scripts\python.exe -u scripts\augment_regional_forms.py

The script is safe to re-run — it skips any key already in the dataset.
"""

import json
import sys
import time
from pathlib import Path

import pandas as pd
import requests

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT     = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CSV_OUT  = DATA_DIR / "pokedex.csv"
PQ_OUT   = DATA_DIR / "pokedex.parquet"

# ── API helpers ────────────────────────────────────────────────────────────────
BASE = "https://pokeapi.co/api/v2"
SESSION = requests.Session()
SESSION.headers["User-Agent"] = "Route81-augment/1.0"

def get(url: str, retries: int = 3) -> dict:
    for attempt in range(retries):
        try:
            r = SESSION.get(url, timeout=15)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 404:
                raise ValueError(f"404: {url}")
        except ValueError:
            raise
        except Exception as e:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)
    return {}

def poke(name: str) -> dict:
    return get(f"{BASE}/pokemon/{name}")

def species(name: str) -> dict:
    return get(f"{BASE}/pokemon-species/{name}")

def evo_chain(url: str) -> dict:
    return get(url)

# ── Tracked moves and abilities (must match existing CSV columns) ──────────────
TRACKED_MOVES = [
    "protect","substitute","sleep-talk","rest","facade","endure","snore","round",
    "toxic","swagger","double-team","confide","hidden-power","frustration","return",
    "attract","secret-power","rain-dance","tera-blast","sunny-day","take-down",
    "giga-impact","hyper-beam","double-edge","helping-hand","body-slam","swift",
    "natural-gift","headbutt","thief","mud-slap","rock-smash","captivate","curse",
    "fling","dig","tackle","rock-tomb","rock-slide","strength","mimic","bulldoze",
    "taunt","aerial-ace","brick-break","scary-face","grass-knot","iron-tail",
    "solar-beam","shadow-ball","reflect","light-screen","zen-headbutt","uproar",
    "psych-up","flash","earthquake","knock-off","icy-wind","endeavor",
]
# Normalise to column name format (hyphens → underscores)
TRACKED_MOVES_SET = {m.replace("-","_") for m in TRACKED_MOVES}

TRACKED_ABILITIES = [
    "swift-swim","sturdy","keen-eye","chlorophyll","intimidate","inner-focus",
    "levitate","sheer-force","swarm","overgrow","blaze","torrent","run-away",
    "frisk","gluttony","pressure",
]
TRACKED_ABILITIES_SET = {a.replace("-","_") for a in TRACKED_ABILITIES}

# ── Regional form metadata ─────────────────────────────────────────────────────
# Maps a suffix in the key to (gen, region, form_tag)
REGION_META = {
    "alola":  (7, "Alola",  "alolan"),
    "galar":  (8, "Galar",  "galarian"),
    "hisui":  (8, "Hisui",  "hisuian"),
    "paldea": (9, "Paldea", "paldean"),
}

# All forms to add — use the exact name PokéAPI accepts
TARGETS = [
    # Primal legends
    "kyogre-primal",
    "groudon-primal",
    # Urshifu Rapid Strike
    "urshifu-rapid-strike",
    # Alolan forms
    "rattata-alola",
    "raticate-alola",
    "raichu-alola",
    "sandshrew-alola",
    "sandslash-alola",
    "vulpix-alola",
    "ninetales-alola",
    "diglett-alola",
    "dugtrio-alola",
    "meowth-alola",
    "persian-alola",
    "geodude-alola",
    "graveler-alola",
    "golem-alola",
    "grimer-alola",
    "muk-alola",
    "exeggutor-alola",
    "marowak-alola",
    # Galarian forms
    "meowth-galar",
    "ponyta-galar",
    "rapidash-galar",
    "slowpoke-galar",
    "slowbro-galar",
    "farfetchd-galar",
    "weezing-galar",
    "mr-mime-galar",
    "corsola-galar",
    "zigzagoon-galar",
    "linoone-galar",
    "darumaka-galar",
    "darmanitan-galar-standard",
    "yamask-galar",
    "stunfisk-galar",
    "articuno-galar",
    "zapdos-galar",
    "moltres-galar",
    "slowking-galar",
    # Hisuian forms
    "growlithe-hisui",
    "arcanine-hisui",
    "voltorb-hisui",
    "electrode-hisui",
    "typhlosion-hisui",
    "qwilfish-hisui",
    "sneasel-hisui",
    "samurott-hisui",
    "lilligant-hisui",
    "zorua-hisui",
    "zoroark-hisui",
    "braviary-hisui",
    "sliggoo-hisui",
    "goodra-hisui",
    "avalugg-hisui",
    "decidueye-hisui",
]

# ── Evo-position helpers ───────────────────────────────────────────────────────
def walk_chain(node: dict, target_key: str, depth: int, chain: list) -> list | None:
    """Return path of species keys from root to target."""
    key = node["species"]["name"]
    path = chain + [key]
    if key == target_key:
        return path
    for ev in node.get("evolves_to", []):
        result = walk_chain(ev, target_key, depth + 1, path)
        if result is not None:
            return result
    return None

def evo_position_for(poke_data: dict, species_data: dict) -> str:
    """Work out evo_position string matching the existing schema."""
    evo_url = species_data.get("evolution_chain", {}).get("url", "")
    if not evo_url:
        return "none"
    try:
        chain_data = evo_chain(evo_url)
    except Exception:
        return "none"

    root = chain_data.get("chain", {})
    species_name = species_data["name"]
    path = walk_chain(root, species_name, 0, [])
    if path is None:
        return "none"

    def count_max_depth(node: int, depth: int) -> int:
        children = node.get("evolves_to", []) if isinstance(node, dict) else []
        if not children:
            return depth
        return max(count_max_depth(c, depth + 1) for c in children)

    # Recompute from root dict
    def max_depth_dict(node: dict, d: int) -> int:
        children = node.get("evolves_to", [])
        if not children:
            return d
        return max(max_depth_dict(c, d + 1) for c in children)

    total_stages = max_depth_dict(root, 0)
    pos = len(path) - 1  # 0-indexed depth

    if total_stages == 0:
        return "none"
    is_baby = species_data.get("is_baby", False)
    if is_baby:
        return "premature"
    if pos == 0:
        return "start"
    if pos == total_stages:
        return "final"
    return "middle"

# ── Row builder ────────────────────────────────────────────────────────────────
def build_row(name: str, existing_keys: set) -> dict | None:
    """Fetch a Pokémon from PokeAPI and return a dataset row dict."""
    # Derive canonical key
    key = name.replace(" ", "-").lower()

    if key in existing_keys:
        print(f"  skip (already in dataset): {key}")
        return None

    print(f"  fetching: {name} … ", end="", flush=True)
    try:
        p = poke(name)
    except ValueError:
        print("NOT FOUND in PokeAPI — skipping")
        return None

    # Species
    species_name = p["species"]["name"]
    try:
        sp = species(species_name)
    except ValueError:
        print("species NOT FOUND — skipping")
        return None

    # Types
    types = sorted(p["types"], key=lambda t: t["slot"])
    type1 = types[0]["type"]["name"]
    type2 = types[1]["type"]["name"] if len(types) > 1 else ""

    # Generation from species
    gen_str = sp.get("generation", {}).get("name", "generation-i")
    gen_num = {"generation-i":1,"generation-ii":2,"generation-iii":3,
               "generation-iv":4,"generation-v":5,"generation-vi":6,
               "generation-vii":7,"generation-viii":8,"generation-ix":9}.get(gen_str, 0)

    # Region from gen (default) — override below for specific forms
    gen_to_region = {1:"Kanto",2:"Johto",3:"Hoenn",4:"Sinnoh",5:"Unova",
                     6:"Kalos",7:"Alola",8:"Galar",9:"Paldea"}
    region = gen_to_region.get(gen_num, "Unknown")

    # Detect form type
    is_regional = False
    form_region = None
    for suffix, (form_gen, form_region_name, _) in REGION_META.items():
        if key.endswith(f"-{suffix}") or f"-{suffix}-" in key:
            is_regional = True
            gen_num  = form_gen
            region   = form_region_name
            form_region = suffix
            break

    is_primal = "primal" in key
    is_urshifu_rs = "rapid-strike" in key and "urshifu" in key

    # Flags from species
    is_legendary = sp.get("is_legendary", False)
    is_mythical  = sp.get("is_mythical", False)
    is_baby      = sp.get("is_baby", False)

    # Abilities
    abilities = [a["ability"]["name"] for a in p.get("abilities", [])]

    # Moves (flat set of move names, normalised)
    move_set = {m["move"]["name"].replace("-","_") for m in p.get("moves", [])}

    # Learnset flags
    learns = {f"learns_{mv.replace('-','_')}": (mv.replace("-","_") in move_set)
              for mv in TRACKED_MOVES_SET}

    # Ability flags
    ability_set = {a.replace("-","_") for a in abilities}
    has_ab = {f"has_ability_{ab}": (ab in ability_set)
              for ab in TRACKED_ABILITIES_SET}

    # Evolution metadata
    evo_pos  = evo_position_for(p, sp)
    is_branched = False  # hard to detect without full chain walk; default False

    # Display name
    display_name = " ".join(w.capitalize() for w in name.replace("-", " ").split())
    # Clean up known formatting
    display_name = display_name.replace(" Alola", " (Alola)") if "alola" in name else display_name
    display_name = display_name.replace(" Galar", " (Galar)") if "galar" in name else display_name
    display_name = display_name.replace(" Hisui", " (Hisui)") if "hisui" in name else display_name
    display_name = display_name.replace(" Paldea", " (Paldea)") if "paldea" in name else display_name

    moves_json = json.dumps([m["move"]["name"] for m in p.get("moves", [])])
    abilities_json = json.dumps(abilities)

    row = {
        "key":             key,
        "display_name":    display_name,
        "species_key":     species_name,
        "pokemon_id":      p["id"],
        "gen":             gen_num,
        "region":          region,
        "type1":           type1,
        "type2":           type2,
        "is_default":      p.get("is_default", False),
        "is_monotype":     type2 == "",
        "is_dual_type":    type2 != "",
        "has_mega":        sp.get("has_mega_evolution", False),
        "has_gmax":        False,
        "is_mega_form":    False,
        "is_gmax_form":    False,
        "is_fossil":       False,
        "abilities":       abilities_json,
        "moves":           moves_json,
        "is_first_partner":False,
        "is_paradox":      False,
        "is_ultra_beast":  False,
        "evo_position":    evo_pos,
        "is_branched_line":is_branched,
        "is_legendary":    is_legendary,
        "is_mythical":     is_mythical,
        "is_baby":         is_baby,
        **learns,
        **has_ab,
        "evolves_by_levelup":    False,
        "evolves_by_friendship": False,
        "evolves_by_trade":      False,
        "evolves_by_item":       False,
        # New flag so the engine can include these in answer policy
        "is_regional_form": is_regional or is_primal or is_urshifu_rs,
    }

    print(f"OK ({type1}/{type2 or 'mono'}, gen {gen_num})")
    return row

# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    # Load existing
    if PQ_OUT.exists():
        df = pd.read_parquet(PQ_OUT)
    elif CSV_OUT.exists():
        df = pd.read_csv(CSV_OUT)
    else:
        sys.exit("ERROR: no pokedex.parquet or pokedex.csv found in data/")

    existing_keys = set(df["key"].tolist())
    print(f"Existing rows: {len(df)}  |  Unique keys: {len(existing_keys)}\n")

    new_rows = []
    for i, name in enumerate(TARGETS, 1):
        print(f"[{i}/{len(TARGETS)}]", end=" ")
        try:
            row = build_row(name, existing_keys)
        except Exception as e:
            print(f"  ERROR on {name}: {e}")
            row = None
        if row:
            new_rows.append(row)
            existing_keys.add(row["key"])
        time.sleep(0.4)  # be polite to PokeAPI

    if not new_rows:
        print("\nNothing new to add.")
        return

    new_df = pd.DataFrame(new_rows)

    # Ensure is_regional_form column exists in original df
    if "is_regional_form" not in df.columns:
        df["is_regional_form"] = False

    # Align columns
    for col in df.columns:
        if col not in new_df.columns:
            new_df[col] = False
    new_df = new_df[df.columns]

    combined = pd.concat([df, new_df], ignore_index=True)
    combined.to_csv(CSV_OUT, index=False)
    combined.to_parquet(PQ_OUT, index=False)

    print(f"\n✓ Added {len(new_rows)} new rows.")
    print(f"  Dataset: {len(df)} → {len(combined)} rows")
    print(f"  Saved to {CSV_OUT} and {PQ_OUT}")

if __name__ == "__main__":
    main()
