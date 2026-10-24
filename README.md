# Route 81

A daily Pokémon logic grid: 81 squares, and each one needs a Pokémon that fits
both its row rule and its column rule. Includes Daily, Draft and Reverse modes,
a Pokédex of everything you've caught, accounts and ads.

- Backend: FastAPI (`api_main.py`), served by Railway (`Procfile`)
- Frontend: static HTML/CSS/JS in `frontend/`
- Project notes: `PROJECT_CONTEXT.md`

Run locally from the repo root:

    python -m uvicorn api_main:app --reload --port 8000

Then open http://127.0.0.1:8000.

Brand art (logo, favicon) and the type icons are generated:

    python scripts/gen_brand_art.py
    python scripts/gen_type_icons.py
