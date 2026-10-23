"""
fix_evolution_data.py: rebuild the "Evolved by ..." flags and Mega abilities
in data/pokedex.csv from PokeAPI's own source tables.

Why: Mega Evolutions and Gigantamax forms were copying their base Pokémon's
evolution flags (Mega Crabominable counted as "evolved by item" and "by
level up"), regional forms copied the wrong method (Galarian Slowbro as
level-up), and the Legends Z-A Megas copied their base abilities.

Rules (PokeDoku style: how a Pokémon was evolved INTO):
  level-up                         -> Level up
  level-up with friendship         -> Friendship (only)
  level-up holding an item         -> Item and Level up   (Weavile, Chansey)
  trade                            -> Trade (+ Item if it holds one: Steelix)
  use an item (stones, cuffs …)    -> Item
  spin holding a Sweet (Alcremie)  -> Item
  evolves on level-up after a condition (Annihilape, Kingambit,
    Gholdengo, Basculegion, Wyrdeer, Overqwil, Maushold, Shedinja) -> Level up
  no level-up or item at all (Sirfetch'd, Runerigus, Urshifu,
    Melmetal)                      -> none
  Mega Evolutions, Gigantamax      -> none (they don't evolve)
  other alternate forms (Aegislash Blade, Darmanitan Zen …) -> same as the
    Pokémon's normal form

Usage (from the repo root):
    python scripts/fix_evolution_data.py            # downloads PokeAPI tables
    python scripts/fix_evolution_data.py --dry-run  # report only
Only the changed cells are rewritten; the rest of the file is untouched.
"""
from __future__ import annotations

import ast
import csv
import io
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "data" / "pokedex.csv"
POKEAPI = "https://raw.githubusercontent.com/PokeAPI/pokeapi/master/data/v2/csv"
METHODS = ["levelup", "friendship", "trade", "item"]

LEVEL_LIKE = {4, 10, 11, 12, 13, 14, 15, 16}   # shed, in-battle level-up, style/move/recoil/bisharp/coins
ITEM_LIKE = {5}                                # spin with a Sweet
NONE_LIKE = {6, 7, 8, 9, 17, 18}               # towers, 3 crits, damage, candies, unclassified


def table(name: str) -> list[dict]:
    with urllib.request.urlopen(f"{POKEAPI}/{name}.csv", timeout=60) as r:
        return list(csv.DictReader(io.StringIO(r.read().decode("utf-8"))))


def methods_of(row: dict) -> set[str]:
    t = int(row["evolution_trigger_id"])
    held = bool(row.get("held_item_id"))
    happy = bool(row.get("minimum_happiness")) or bool(row.get("minimum_affection"))
    if t == 1:
        if happy:
            return {"friendship"}
        return {"item", "levelup"} if held else {"levelup"}
    if t == 2:
        return {"trade", "item"} if held else {"trade"}
    if t == 3 or t in ITEM_LIKE:
        return {"item"}
    if t in LEVEL_LIKE:
        return {"levelup"}
    return set()


def truthy(v: str) -> bool:
    return str(v).strip().lower() in ("true", "1")


def main() -> None:
    dry = "--dry-run" in sys.argv
    pokemon = table("pokemon")
    forms = table("pokemon_forms")
    evo = table("pokemon_evolution")
    abilities = {r["id"]: r["identifier"] for r in table("abilities")}
    poke_abil = table("pokemon_abilities")

    pid_by_key = {r["identifier"]: int(r["id"]) for r in pokemon}
    default_pid = {int(r["species_id"]): int(r["id"]) for r in pokemon if r["is_default"] == "1"}
    form_pid = {int(r["id"]): int(r["pokemon_id"]) for r in forms}

    incoming: dict[int, set[str]] = {}
    for r in evo:
        f = r.get("evolved_pokemon_form_id")
        target = form_pid.get(int(f)) if f else default_pid.get(int(r["evolved_species_id"]))
        if target:
            incoming.setdefault(target, set()).update(methods_of(r))

    abil_by_pid: dict[int, set[str]] = {}
    for r in poke_abil:
        abil_by_pid.setdefault(int(r["pokemon_id"]), set()).add(abilities.get(r["ability_id"], ""))

    text = CSV_PATH.read_text(encoding="utf-8")
    newline = "\r\n" if "\r\n" in text else "\n"
    rows = list(csv.reader(io.StringIO(text)))
    head = rows[0]
    col = {name: i for i, name in enumerate(head)}
    abil_cols = [c for c in head if c.startswith("has_ability_")]

    # Methods of each species' normal form, for alternate forms to share.
    by_species_default: dict[str, set[str]] = {}
    for r in rows[1:]:
        if truthy(r[col["is_default"]]):
            pid = pid_by_key.get(r[col["key"]])
            by_species_default[r[col["species_key"]]] = incoming.get(pid, set()) if pid else set()

    changes = []
    for r in rows[1:]:
        key = r[col["key"]]
        name = r[col["display_name"]]
        pid = pid_by_key.get(key)
        mega = truthy(r[col["is_mega_form"]]) or "-mega" in key
        gmax = truthy(r[col["is_gmax_form"]]) or key.endswith("-gmax")
        regional = truthy(r[col["is_regional_form"]])
        default = truthy(r[col["is_default"]])

        if mega or gmax:
            want = set()
        elif default or regional:
            want = incoming.get(pid, set()) if pid else set()
        else:   # alternate form: same as the species' normal form
            want = by_species_default.get(r[col["species_key"]], set())

        for m in METHODS:
            c = col[f"evolves_by_{m}"]
            new = "True" if m in want else "False"
            if truthy(r[c]) != (new == "True"):
                changes.append((name, f"evolves_by_{m}", r[c], new))
                r[c] = new

        # Megas and other forms: abilities from PokeAPI (Z-A Megas copied
        # their base Pokémon's abilities).
        if (mega or gmax or not default) and pid and abil_by_pid.get(pid):
            real = sorted(a for a in abil_by_pid[pid] if a)
            for c in abil_cols:
                ab = c.replace("has_ability_", "").replace("_", "-")
                new = "True" if ab in real else "False"
                if truthy(r[col[c]]) != (new == "True"):
                    changes.append((name, c, r[col[c]], new))
                    r[col[c]] = new
            if "abilities" in col:
                cur = r[col["abilities"]]
                try:
                    cur_set = sorted(json.loads(cur)) if cur else []
                except ValueError:
                    try:
                        cur_set = sorted(ast.literal_eval(cur))
                    except (ValueError, SyntaxError):
                        cur_set = []
                if cur_set != real:
                    newval = json.dumps(real, separators=(",", ":"))
                    changes.append((name, "abilities", cur, newval))
                    r[col["abilities"]] = newval

    for c in changes:
        print(f"{c[0]:32} {c[1]:28} {c[2]:>8} -> {c[3]}")
    print(f"\n{len(changes)} cells changed")
    if not dry and changes:
        out = io.StringIO()
        csv.writer(out, lineterminator=newline).writerows(rows)
        body = out.getvalue()
        if not text.endswith(("\n", "\r\n")):
            body = body.rstrip("\r\n")
        CSV_PATH.write_text(body, encoding="utf-8")
        print(f"wrote {CSV_PATH}")


if __name__ == "__main__":
    main()
