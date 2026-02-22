import json
import time
from pathlib import Path

import pandas as pd
import requests


OUT_DIR = Path(__file__).resolve().parents[1] / "data"
OUT_DIR.mkdir(parents=True, exist_ok=True)

POKEAPI = "https://pokeapi.co/api/v2"


def get(url: str) -> dict:
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return r.json()


def slug_to_display(name: str) -> str:
    # e.g. "mr-mime" -> "Mr Mime", "type-null" -> "Type Null"
    parts = name.replace("-", " ").split()
    return " ".join(p.capitalize() for p in parts)


def main():
    # Pull all pokemon species (this is the canonical place for legendary/mythical/baby)
    species_list = get(f"{POKEAPI}/pokemon-species?limit=200000")["results"]

    rows = []
    total = len(species_list)

    for idx, item in enumerate(species_list, start=1):
        species = get(item["url"])

        # species["varieties"] contains the actual pokemon forms in /pokemon/
        # We'll include each variety as a separate playable entry.
        gen_name = species["generation"]["name"]  # "generation-i"
        gen_num = int(gen_name.split("-")[-1].replace("i", "1").replace("v", "5")) if gen_name else None
        # Above is a quick fallback; we’ll replace with a clean mapping in a later polish pass.

        base_flags = {
            "is_legendary": bool(species.get("is_legendary")),
            "is_mythical": bool(species.get("is_mythical")),
            "is_baby": bool(species.get("is_baby")),
        }

        for v in species.get("varieties", []):
            p = get(v["pokemon"]["url"])  # /pokemon/<id or name> gives types

            types_sorted = sorted(
                [(t["slot"], t["type"]["name"]) for t in p.get("types", [])],
                key=lambda x: x[0],
            )
            type1 = types_sorted[0][1] if len(types_sorted) >= 1 else ""
            type2 = types_sorted[1][1] if len(types_sorted) >= 2 else ""

            key = p["name"]  # stable slug key
            display = slug_to_display(key)

            rows.append(
                {
                    "key": key,
                    "display_name": display,
                    "species_key": species["name"],
                    "gen_raw": gen_name,
                    "type1": type1,
                    "type2": type2,
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

    out_parquet = OUT_DIR / "pokedex.parquet"
    out_csv = OUT_DIR / "pokedex.csv"
    df.to_parquet(out_parquet, index=False)
    df.to_csv(out_csv, index=False)

    print(f"Saved: {out_parquet}")
    print(f"Saved: {out_csv}")
    print(f"Rows: {len(df)}")


if __name__ == "__main__":
    main()