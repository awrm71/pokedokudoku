# Frontend Scaffold (No Admin Required)

This folder is a static HTML/CSS/JS frontend that talks to the FastAPI backend.

It also includes a built-in Mock mode so you can test UI interactions without Python.

## Run backend

From the project root:

python -m uvicorn api_main:app --reload --port 8000

## Run frontend static server

From this frontend folder:

python -m http.server 8080

Then open:

http://127.0.0.1:8080

## Using Mock Mode (no backend)

1. Open the page.
2. Set Data Mode to `Mock only` or keep `Auto (API then Mock)`.
3. Click `Load Random` or `Load Today`.

In mock mode:
- Board generation, suggestions, and validation run in-browser.
- Validation is deterministic per seed/cell from a local sample dataset.
- This is for UI testing only (not production rule accuracy).

## Notes

- API default is http://127.0.0.1:8000 and can be changed in the UI.
- Sprites use official-artwork CDN URLs by pokemon_id.
- No Node.js required.
