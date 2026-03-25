import requests
from pathlib import Path
import pandas as pd

DATA_PATH_PARQUET = Path("data/pokedex.parquet")
DATA_PATH_CSV = Path("data/pokedex.csv")
SPRITES_DIR = Path("sprites")

SPRITES_DIR.mkdir(parents=True, exist_ok=True)


def load_pokedex():
    if DATA_PATH_PARQUET.exists():
        return pd.read_parquet(DATA_PATH_PARQUET)
    elif DATA_PATH_CSV.exists():
        return pd.read_csv(DATA_PATH_CSV)
    else:
        raise FileNotFoundError("No pokedex.parquet or pokedex.csv found in /data")


def fetch_sprite_direct(pokemon_id: int) -> bytes | None:
    url = f"https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/other/official-artwork/{pokemon_id}.png"
    r = requests.get(url)
    if r.status_code == 200:
        return r.content
    return None


def main():
    df = load_pokedex()

    total = len(df)
    print(f"Fetching {total} sprites...\n")

    for idx, row in df.iterrows():
        pid = int(row["pokemon_id"])
        out_path = SPRITES_DIR / f"{pid}.png"

        if out_path.exists():
            continue

        img = fetch_sprite_direct(pid)
        if img:
            with open(out_path, "wb") as f:
                f.write(img)
            print(f"[{idx+1}/{total}] Saved {pid}.png")

    print("\nDone!")


if __name__ == "__main__":
    main()