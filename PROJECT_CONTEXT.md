# PokeDokuDoku — Project Context / Handoff Doc

This document is a **full-project context dump** intended to be pasted into another assistant/chatbot later.
It describes:
- What the project is and how it’s structured
- How data + sprites are collected and stored locally
- How boards are generated (solvable, with a distinct solution)
- Key implementation decisions and bug fixes completed so far
- Current state + how to run it
- Where we want to take it next

---

## 1) What this project is

**PokeDokuDoku** is a PokeDoku-style grid game:
- You get an $n \times n$ grid (commonly 9×9) with **row rules** and **column rules**.
- Each cell represents the **intersection** of one row rule and one column rule.
- A player fills each cell with a Pokémon that satisfies **both** rules.
- The game enforces correctness and (optionally) uniqueness constraints.

This repo is designed to run fully locally:
- A local dataset (CSV/Parquet) powers rules and validation.
- Local sprite assets are used when available.
- The backend exposes a small API used by a static frontend.

---

## 2) Repo layout (high level)

Top-level files:
- `api_main.py`: FastAPI backend used by the browser frontend (primary API)
- `engine.py`: rules library + board generator + validation logic
- `app.py`: Streamlit app (optional alternate UI)
- `README.md`: basic usage

Data + build scripts:
- `data/pokedex.csv` and `data/pokedex.parquet`: the local dataset consumed by the engine/API
- `scripts/build_pokedex.py`: dataset builder/augmenter (PokéAPI → local dataset)
- `scripts/fetch_sprites.py`: helper to fetch/organize sprites (varies by setup)

Sprites:
- `sprites_repo/…`: a local sprite repository checkout (large) containing Pokémon sprites and type icons

Frontend:
- `frontend/…`: static HTML/CSS/JS UI. It calls the API and renders the board.

---

## 3) Architecture overview

### Backend
- **FastAPI + Uvicorn** server
- Loads `data/pokedex.parquet` (preferred) or `data/pokedex.csv`
- Precomputes:
  - Playable answer set (`df_play`)
  - Rules dictionary
  - Boolean masks per rule
  - Name index for autocomplete/resolve

Primary endpoints:
- `GET /board?seed=...&n=9` → returns row/col rules for a new board
- `POST /validate` → validates a pick for a specific board cell
- `GET /pokemon/suggest?prefix=...` → autocomplete
- `GET /pokemon/resolve?name=...` → resolve free text to canonical key
- `GET /sprite/{pokemon_id}.png` → serve local sprite if available else redirect to CDN

### Frontend
- Static app that:
  - Fetches `/board`
  - Renders rule headers (including type badges)
  - Validates picks via `/validate`
  - Offers autocomplete via `/pokemon/suggest`

Important UX behavior:
- The frontend originally had a “mock mode” fallback when API was unreachable.
- This was adjusted so “auto” mode does **not** permanently get stuck in mock mode after a single failure.

---

## 4) Data: how we collect it

### Source
Data is sourced primarily from **PokéAPI**.

### Output dataset
The dataset is stored in:
- `data/pokedex.parquet` (fast load)
- `data/pokedex.csv` (human-readable)

Key columns include:
- Identity:
  - `key` (stable slug, e.g. `pikachu`)
  - `display_name` (human friendly)
  - `species_key`
  - `pokemon_id`
- Categories:
  - `type1`, `type2`
  - `gen`, `region`
  - flags like `is_legendary`, `is_mythical`, `is_baby`, `is_fossil`
  - form flags like `is_default`, `is_mega_form`, `is_gmax_form`
- Evolution metadata:
  - `evo_position` (premature/start/middle/final/none)
  - `is_branched_line`
  - **Outgoing evolution method flags**:
    - `evolves_by_levelup`
    - `evolves_by_friendship`
    - `evolves_by_trade`
    - `evolves_by_item`

Optional feature expansion:
- One-hot “top-K” moves and abilities:
  - `learns_<move>` columns
  - `has_ability_<ability>` columns

### Build script
The main builder is `scripts/build_pokedex.py`.

Two modes exist:

1) **Full rebuild** (pull everything from PokéAPI)
- Fetches Pokémon species and varieties, types, moves, abilities, evolution metadata.

2) **Augment existing dataset**
- Loads the existing CSV/Parquet and adds/updates derived columns.
- Critically: we used this to recompute evolution-method flags without fully rebuilding everything.

---

## 5) Evolution semantics (important change)

### The problem
Early iterations produced evolution-method columns that matched **incoming** semantics:
- “Evolved By: Trade” was interpreted as “this Pokémon was obtained by trade” (e.g. the evolved form).

But PokeDoku’s wording is typically understood as **outgoing** semantics:
- “Evolves By: Trade” means “this Pokémon evolves via trade” (e.g. Kadabra evolves by trade).

### The fix
We standardized on **outgoing** evolution semantics only:
- Keep only `evolves_by_*` columns.
- Drop the old `evolved_by_*` columns (to avoid user confusion).

### Implementation details
- Evolution parsing walks evolution chains and records children + evolution_details.
- For each species, outgoing triggers are derived from the evolution details stored on its child edges.

### Augmentation speed improvement
The original augmentation loop fetched `/pokemon-species/<name>` for every species, which was slow.
It was replaced with a faster approach:
- Fetch `/evolution-chain?limit=200000`
- Iterate each chain directly
- Build a species → outgoing method flags mapping

This reduces the number of network calls substantially.

---

## 6) Sprites and type icons

### Pokémon sprites
There are two approaches:
- Prefer local sprites under the checked-out `sprites_repo` where available.
- If not found locally, the backend redirects to an official-artwork CDN.

Backend sprite route:
- `GET /sprite/{pokemon_id}.png`
  - If local file exists, returns it
  - Else redirects to the CDN URL

### Type badges
Type badge icons are stored locally in the sprite repo under Gen 8:
- `sprites_repo/sprites/types/generation-viii/sword-shield/*.png`

A bug occurred where type icons were mismatched (e.g. Electric showing Rock).
Root cause:
- Numeric badge file naming / ordering was fragile.
Fix:
- Use **named** type badges (e.g. `electric.png`, `rock.png`) and load by type name.

---

## 7) Rules and board generation methodology

### Rules
Rules are generated from the dataset in `engine.build_default_rules(df)`.
Examples:
- Types: `Type: Fire`
- Generations: `Gen: 1`
- Flags: `Legendary`, `Mythical`, `Baby`, `Single-type`, etc.
- Evolution:
  - position rules (`Position: Start`, etc.)
  - branching (`Is Branched`)
  - method rules (`Evolves By: Trade`, etc.)

### Board constraints
Boards are generated with constraints such as:
- Each cell must have at least `min_candidates_per_cell` possible answers.
- A **global distinct solution** can be required (perfect matching across all cells).

The backend uses `require_distinct_solution=True` so boards are solvable with an all-distinct assignment.

### Generation process (high-level)
1) Build boolean masks for each rule over the playable answer DataFrame.
2) Randomly select row rules and column rules.
3) Ensure all intersections are valid and meet candidate thresholds.
4) When distinct-solution is required:
   - Solve a matching feasibility problem to ensure one unique Pokémon per cell is possible.

### Diversity and weighting
To avoid boards becoming move-dominated or too repetitive:
- Rules are grouped (type/gen/flag/move/ability/evolution/etc.)
- Weighted selection biases toward certain groups (e.g., types are core)
- Caps limit how many move/ability rules appear per axis

### Recent evolution-rule visibility fix
After evolution augmentation, the new `evolves_by_*` rules existed but rarely appeared.
Root causes:
- Early “min_support” filtering excluded narrow evolution rules.
- Selection weights favored broad rules too strongly.
- Internal compatibility floor of 2 candidates effectively prevented narrow method rules.

Fixes applied:
- Give evolution rules a lower early support floor (similar to types).
- Soften support bias for evolution rules.
- Allow `evolves_by_*` rules to pass with 1-candidate intersections during selection while still enforcing full distinct-solution matching.
- Add a targeted weight boost so Trade/Friendship/Item can appear sometimes.

---

## 8) Reliability fixes and debugging notes

### “API unavailable” / stale backend
We repeatedly saw cases where:
- The frontend showed API unavailable
- Or types/rules didn’t match code changes

Root cause was often:
- Backend not running
- Multiple Uvicorn instances / stale instance
- Server not restarted after changing data or code

Best practice:
- Check port 8000 process PID.
- Restart server when:
  - dataset is regenerated
  - engine logic changes

### Label compaction / abbreviations
At one point rule labels were compacted too aggressively (e.g. “Leg”, “G1”).
This was reverted to keep UI labels readable (e.g. “Legendary”, “Gen 1”).

---

## 9) Exact commands (copy/paste)

### Build/augment dataset (recompute evolves_by_*)
Run from repo root:

```powershell
.\.venv\Scripts\python.exe -u scripts\build_pokedex.py --augment-existing --augment-evolution --augment-evolution-print-every 25
```

### Start the API
From repo root:

```powershell
.\.venv\Scripts\python.exe -m uvicorn api_main:app --reload --host 127.0.0.1 --port 8000
```

### Quick health check
Open in browser:
- http://127.0.0.1:8000/board?n=9

---

## 10) Current state (as of this handoff)

- Dataset includes `evolves_by_*` and **does not** include `evolved_by_*`.
- Engine builds all four evolution-method rules from the dataset.
- Board selection logic was updated so evolution-method rules can appear under strict solvability constraints.
- Type badges load by name (not fragile numeric mapping).
- API loaders coerce `evolves_by_*` booleans correctly.

---

## 11) Future goals / roadmap

Potential next steps (intentionally conservative):

1) Data quality + coverage
- Expand evolution method detection beyond the four basic buckets if desired
- Add curated flags like “Paradox”, “Ultra Beast”, “First Partner” only with a reliable source

2) Board quality
- Tune how often evolution-method rules appear (currently boosted to be visible)
- Add explicit per-board quotas (e.g., at least 1 evolution rule) if desired

3) Performance + caching
- Cache PokéAPI responses locally for faster rebuilds and offline usage
- Make augmentation idempotent and fast even on slow networks

4) UX polish
- Improve rule label formatting consistency
- Improve sprite fallback behavior and error handling

---

## 12) Notes for future assistants

If handing this project to another chatbot/assistant, key constraints/assumptions to tell them:
- This repo must work locally without downloading huge assets at runtime.
- Large sprite assets are stored in `sprites_repo/` and are often intentionally not tracked in the same way as code.
- The board generator prioritizes solvability with a global distinct assignment.
- Evolution wording is PokeDoku-style outgoing semantics: **“Evolves By”**.
