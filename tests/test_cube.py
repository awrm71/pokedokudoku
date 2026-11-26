"""
Cube checks. Run from the repo root:

    python -m pytest tests/test_cube.py -q

Uses a throwaway database (PDD_DB_PATH), so it never touches real progress.
CUBE_SEEDS sets how many cubes the stress test builds (default 120).
"""

import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["PDD_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "test.db")

import api_main  # noqa: E402
import cube_engine as ce  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

DF = api_main.df_play
RM = api_main.rule_masks
KEYS = DF["key"].astype(str).to_numpy()
POOL = ce.cube_pool(RM, len(DF))
MASKS = ce.Masks.build(DF, RM, sorted(set(POOL) | set(ce._scoreable(RM, len(DF), RM.keys()))))
SEEDS = int(os.environ.get("CUBE_SEEDS", "120"))


def _mask(rid):
    # Straight from the pandas masks, not cube_engine's matrix: an independent check.
    return RM[rid].fillna(False).to_numpy(dtype=bool)


def test_geometry():
    assert len(ce.CELLS) == 54
    for line in range(6):
        assert len(ce.line_cells(line)) == 9
    for line in range(6, 15):
        cells = ce.line_cells(line)
        assert len(cells) == 12
        assert len({c[0] for c in cells}) == 4          # a ring wraps four faces, three squares each
    # Every square is in exactly one face and two rings.
    for c in ce.CELLS:
        assert sum(c in ce.line_cells(l) for l in range(15)) == 3


def _verify(board, min_per_cell):
    ids = board.all_rules()
    assert len(set(ids)) == 15, "a rule appears twice"
    for f, i, j in ce.CELLS:
        a, b, c = (_mask(r) for r in board.rules_at(f, i, j))
        n = int((a & b & c).sum())
        assert n >= min_per_cell, f"square {(f, i, j)} has {n} answers"
        assert n < (a & b).sum() and n < (a & c).sum() and n < (b & c).sum(), f"a rule does nothing at {(f, i, j)}"
    sol = list(board.solution)
    assert len(sol) == 54 and len(set(sol)) == 54
    for (f, i, j), k in zip(ce.CELLS, sol):
        p = int(np.flatnonzero(KEYS == k)[0])
        for rid in board.rules_at(f, i, j):
            assert _mask(rid)[p], f"{k} doesn't fit {rid}"
    for p, q in ce.meeting_pairs(board.rings, board.faces):
        both = (_mask(p) & _mask(q)).sum()
        assert both < _mask(p).sum() and both < _mask(q).sum(), f"{p} / {q} redundant"


def test_generated_cubes_are_fair():
    for n in range(SEEDS):
        board = ce.generate_cube(DF, RM, f"stress-{n}", masks=MASKS)
        assert board is not None, f"seed stress-{n} failed to build"
        _verify(board, 2)


def test_generation_is_deterministic():
    a = ce.generate_cube(DF, RM, "2027-01-01", masks=MASKS)
    b = ce.generate_cube(DF, RM, "2027-01-01")
    assert (a.rings, a.faces) == (b.rings, b.faces)


def test_reverse_cubes():
    for diff in ("easy", "medium", "hard"):
        for n in range(6):
            rc = ce.generate_reverse_cube(DF, RM, f"rev-{diff}-{n}", diff)
            assert rc is not None
            _verify(rc.board, 1)
            assert len(set(rc.grid)) == 54
            for line in range(15):
                assert rc.board.line_rule(line) in rc.answers[line]


def test_hint_never_strands_the_board():
    board = ce.generate_cube(DF, RM, "hint-check", masks=MASKS)
    filled = {}
    for c in ce.CELLS:
        k = ce.hint_for(MASKS, board, *c, set(filled.values()), filled)
        assert k is not None
        filled[c] = k
        assert ce.still_solvable(MASKS, board, filled)
    assert len(set(filled.values())) == 54


# ── API ──────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def client():
    return TestClient(api_main.app)


def H(cid):
    return {"X-Client-Id": cid}


def _board(r):
    return ce.board_from_ids(DF, RM, r["seed"], [[x["id"] for x in ax] for ax in r["rings"]],
                             [x["id"] for x in r["faces"]], masks=MASKS)


def test_daily_cube_flow(client):
    cid = "test-daily-1"
    r = client.get("/cube/board", headers=H(cid)).json()
    assert r["is_daily"] and len(r["faces"]) == 6 and len(r["rings"]) == 3 and r["run"]["lives_left"] == 3
    seed = r["seed"]
    board = _board(r)
    assert client.get("/cube/board", headers=H("someone-else")).json()["faces"] == r["faces"]
    assert client.get(f"/cube/solution?seed={seed}", headers=H(cid)).status_code == 403

    wrong = next(k for k in KEYS if not ce.cell_mask(MASKS, board, 0, 0, 0)[np.flatnonzero(MASKS.keys == k)[0]])
    v = client.post("/cube/validate", json={"seed": seed, "f": 0, "i": 0, "j": 0, "pokemon_key": wrong}, headers=H(cid)).json()
    assert not v["ok"] and v["lives_left"] == 2
    assert client.get("/cube/board", headers=H(cid)).json()["run"]["lives_left"] == 2

    h = client.post("/cube/hint", json={"seed": seed, "f": 4, "i": 1, "j": 1}, headers=H(cid)).json()
    assert h["ok"] and h["hints_left"] == 2
    placed = {(4, 1, 1): h["mon"]["key"]}
    for c in ce.CELLS:
        if c in placed:
            continue
        k = ce.hint_for(MASKS, board, *c, set(placed.values()), placed)
        v = client.post("/cube/validate", json={"seed": seed, "f": c[0], "i": c[1], "j": c[2], "pokemon_key": k},
                        headers=H(cid)).json()
        assert v["ok"], (c, k, v)
        placed[c] = k
    assert v["finished"]
    run = client.get("/cube/board", headers=H(cid)).json()["run"]
    assert run["finished"] and len(run["picks"]) == 54
    v = client.post("/cube/validate", json={"seed": seed, "f": 0, "i": 0, "j": 0, "pokemon_key": wrong}, headers=H(cid)).json()
    assert v["reason"] == "board_locked"
    sol = client.get(f"/cube/solution?seed={seed}", headers=H(cid)).json()
    assert len(sol["cells"]) == 54 and all(c["count"] >= 2 for c in sol["cells"])


def test_practice_cube_and_duplicates(client):
    r = client.get("/cube/board?seed=practice-1").json()
    assert not r["is_daily"] and r["run"] is None
    board = _board(r)
    k = board.solution_at(0, 0, 0)
    ok = client.post("/cube/validate", json={"seed": "practice-1", "f": 0, "i": 0, "j": 0, "pokemon_key": k}).json()
    assert ok["ok"] and ok["mon"]["key"] == k
    dup = client.post("/cube/validate", json={"seed": "practice-1", "f": 0, "i": 0, "j": 0, "pokemon_key": k,
                                              "used_keys": [k]}).json()
    assert dup["reason"] == "duplicate"
    assert client.get("/cube/solution?seed=practice-1").status_code == 200


def test_reverse_daily_flow(client):
    cid = "test-rev-1"
    r = client.get("/cube/reverse/daily", headers=H(cid)).json()
    assert r["is_daily"] and len(r["cells"]) == 54
    assert "rule" not in str(r["cells"])
    h = client.post("/cube/reverse/hint", json={"board_id": r["board_id"], "line": 7}, headers=H(cid)).json()
    assert h["ok"] and h["run"]["hints_left"] == 1
    again = client.get("/cube/reverse/daily", headers=H(cid)).json()
    assert any(s["line"] == 7 for s in again["run"]["solved"])


def test_reverse_practice_solve_all(client):
    r = client.post("/cube/reverse", json={"difficulty": "easy"}).json()
    keys = {(c["f"], c["i"], c["j"]): c["mon"]["key"] for c in r["cells"]}
    pos = {k: i for i, k in enumerate(KEYS)}
    scoreable = ce._scoreable(RM, len(DF), RM.keys())
    run = None
    for line in range(15):
        ks = [keys[c] for c in ce.line_cells(line)]
        rid = next(x for x in scoreable if all(_mask(x)[pos[k]] for k in ks))
        g = client.post("/cube/reverse/validate", json={"board_id": r["board_id"], "line": line, "rule_id": rid}).json()
        assert g["ok"], (line, rid, g)
        run = g["run"]
    assert run["won"] and run["finished"]
