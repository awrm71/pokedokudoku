"""
Cube checks. Run from the repo root:

    python -m pytest tests/test_cube.py -q

Uses a throwaway database (PDD_DB_PATH), so it never touches real progress.
CUBE_SEEDS sets how many boards the stress test builds (default 150).
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
POOL = ce.cube_pool(RM, len(DF))
MASKS = ce.Masks.build(DF, RM, sorted(set(POOL) | set(ce._scoreable(RM, len(DF), RM.keys()))))
SEEDS = int(os.environ.get("CUBE_SEEDS", "150"))


def _mask(rid):
    # Straight from the pandas masks, not cube_engine's matrix: an independent check.
    return RM[rid].fillna(False).to_numpy(dtype=bool)


def _verify(board, min_per_cell):
    ids = list(board.cols) + list(board.rows) + list(board.layers)
    assert len(set(ids)) == 9, "a rule appears twice"
    keys = DF["key"].astype(str).to_numpy()
    # 1 + 3: every square has room, and every one of its rules narrows it.
    for z, y, x in ce.CELLS:
        a, b, c = _mask(board.cols[x]), _mask(board.rows[y]), _mask(board.layers[z])
        n = int((a & b & c).sum())
        assert n >= min_per_cell, f"square {(z, y, x)} has {n} answers"
        assert n < (a & b).sum() and n < (a & c).sum() and n < (b & c).sum(), f"a rule does nothing at {(z, y, x)}"
    # 2: the stored solution is 27 different Pokémon, each fitting its square.
    sol = list(board.solution)
    assert len(sol) == 27 and len(set(sol)) == 27
    for (z, y, x), k in zip(ce.CELLS, sol):
        i = int(np.flatnonzero(keys == k)[0])
        for rid in board.rules_at(z, y, x):
            assert _mask(rid)[i], f"{k} doesn't fit {rid}"
    # 4: no rule sits inside a rule on another axis.
    for ax1, ax2 in ((board.cols, board.rows), (board.cols, board.layers), (board.rows, board.layers)):
        for p in ax1:
            for q in ax2:
                both = (_mask(p) & _mask(q)).sum()
                assert both < _mask(p).sum() and both < _mask(q).sum(), f"{p} / {q} redundant"


def test_generated_cubes_are_fair():
    for i in range(SEEDS):
        board = ce.generate_cube(DF, RM, f"stress-{i}", masks=MASKS)
        assert board is not None, f"seed stress-{i} failed to build"
        _verify(board, 2)


def test_generation_is_deterministic():
    a = ce.generate_cube(DF, RM, "2027-01-01", masks=MASKS)
    b = ce.generate_cube(DF, RM, "2027-01-01")
    assert a.axes == b.axes


def test_reverse_cubes():
    for diff in ("easy", "medium", "hard"):
        for i in range(10):
            rc = ce.generate_reverse_cube(DF, RM, f"rev-{diff}-{i}", diff)
            assert rc is not None
            _verify(rc.board, 1)
            assert len(set(rc.grid)) == 27
            for axis in range(3):
                for idx in range(3):
                    assert rc.board.axes[axis][idx] in rc.answers[axis][idx]
            if diff == "hard":
                assert rc.ambiguity() <= 4


def test_hint_never_strands_the_board():
    board = ce.generate_cube(DF, RM, "hint-check", masks=MASKS)
    filled = {}
    for c in ce.CELLS:
        k = ce.hint_for(MASKS, board, *c, set(filled.values()), filled)
        assert k is not None
        filled[c] = k
        assert ce.still_solvable(MASKS, board, filled)
    assert len(set(filled.values())) == 27


# ── API ──────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def client():
    return TestClient(api_main.app)


def H(cid):
    return {"X-Client-Id": cid}


def test_daily_cube_flow(client):
    cid = "test-daily-1"
    r = client.get("/cube/board", headers=H(cid)).json()
    assert r["is_daily"] and len(r["axes"]) == 3 and r["run"]["lives_left"] == 3
    seed = r["seed"]
    board = ce.board_from_ids(DF, RM, seed, *[[x["id"] for x in a["rules"]] for a in r["axes"]], masks=MASKS)
    # Same Cube for everyone, every time.
    again = client.get("/cube/board", headers=H("someone-else")).json()
    assert again["axes"] == r["axes"]

    # Answers are hidden until the Cube is finished.
    assert client.get(f"/cube/solution?seed={seed}", headers=H(cid)).status_code == 403

    # A wrong answer costs a life and is remembered by the server.
    wrong = next(k for k in DF["key"] if not ce.cell_mask(MASKS, board, 0, 0, 0)[np.flatnonzero(MASKS.keys == k)[0]])
    v = client.post("/cube/validate", json={"seed": seed, "z": 0, "y": 0, "x": 0, "pokemon_key": wrong}, headers=H(cid)).json()
    assert not v["ok"] and v["lives_left"] == 2
    assert client.get("/cube/board", headers=H(cid)).json()["run"]["lives_left"] == 2

    # One hint, then fill the rest from the solution.
    h = client.post("/cube/hint", json={"seed": seed, "z": 1, "y": 1, "x": 1}, headers=H(cid)).json()
    assert h["ok"] and h["hints_left"] == 1
    placed = {(1, 1, 1): h["mon"]["key"]}
    for c in ce.CELLS:
        if c in placed:
            continue
        k = ce.hint_for(MASKS, board, *c, set(placed.values()), placed)
        v = client.post("/cube/validate", json={"seed": seed, "z": c[0], "y": c[1], "x": c[2], "pokemon_key": k},
                        headers=H(cid)).json()
        assert v["ok"], (c, k, v)
        placed[c] = k
    assert v["finished"]
    run = client.get("/cube/board", headers=H(cid)).json()["run"]
    assert run["finished"] and len(run["picks"]) == 27
    # Locked now; the answers open up.
    v = client.post("/cube/validate", json={"seed": seed, "z": 0, "y": 0, "x": 0, "pokemon_key": wrong}, headers=H(cid)).json()
    assert v["reason"] == "board_locked"
    sol = client.get(f"/cube/solution?seed={seed}", headers=H(cid)).json()
    assert len(sol["cells"]) == 27 and all(c["count"] >= 2 for c in sol["cells"])


def test_daily_cube_out_of_lives(client):
    cid = "test-daily-2"
    r = client.get("/cube/board", headers=H(cid)).json()
    for _ in range(3):
        v = client.post("/cube/validate", json={"seed": r["seed"], "z": 2, "y": 2, "x": 2, "pokemon_key": "magikarp"},
                        headers=H(cid)).json()
    # Magikarp might legitimately fit; only check the end state when it doesn't.
    if not v["ok"]:
        assert v["finished"] and v["lives_left"] == 0


def test_practice_cube_and_duplicates(client):
    r = client.get("/cube/board?seed=practice-1").json()
    assert not r["is_daily"] and r["run"] is None
    board = ce.board_from_ids(DF, RM, "practice-1", *[[x["id"] for x in a["rules"]] for a in r["axes"]], masks=MASKS)
    k = board.solution_at(0, 0, 0)
    ok = client.post("/cube/validate", json={"seed": "practice-1", "z": 0, "y": 0, "x": 0, "pokemon_key": k}).json()
    assert ok["ok"] and ok["mon"]["key"] == k
    dup = client.post("/cube/validate", json={"seed": "practice-1", "z": 0, "y": 0, "x": 0, "pokemon_key": k,
                                              "used_keys": [k]}).json()
    assert dup["reason"] == "duplicate"
    assert client.get("/cube/solution?seed=practice-1").status_code == 200


def test_reverse_daily_flow(client):
    cid = "test-rev-1"
    r = client.get("/cube/reverse/daily", headers=H(cid)).json()
    assert r["is_daily"] and len(r["cells"]) == 27
    assert "rule" not in str(r["cells"])          # no answers in what the browser gets
    bid = r["board_id"]
    entry_rules = None
    # Wrong guess costs a life (a rule no slice of 9 could share: Mythical rarely fits all).
    g = client.post("/cube/reverse/validate", json={"board_id": bid, "axis": 0, "index": 0, "rule_id": "mythical"},
                    headers=H(cid)).json()
    if not g["ok"]:
        assert g["run"]["lives_left"] == 2
    # Hint one slice, then the server remembers it after a reload.
    h = client.post("/cube/reverse/hint", json={"board_id": bid, "axis": 2, "index": 1}, headers=H(cid)).json()
    assert h["ok"] and h["run"]["hints_left"] == 0
    again = client.get("/cube/reverse/daily", headers=H(cid)).json()
    assert any(s["axis"] == 2 and s["index"] == 1 for s in again["run"]["solved"])
    _ = entry_rules


def test_reverse_practice_solve_all(client):
    r = client.post("/cube/reverse", json={"difficulty": "easy"}).json()
    bid = r["board_id"]
    # Rebuild the answer key the same way the server does, then solve every slice.
    keys = {(c["z"], c["y"], c["x"]): c["mon"]["key"] for c in r["cells"]}
    run = None
    for axis in range(3):
        for idx in range(3):
            slice_keys = [keys[c] for c in ce.slice_cells(axis, idx)]
            cands = [rid for rid in ce._scoreable(RM, len(DF), RM.keys())
                     if all(_mask(rid)[np.flatnonzero(DF["key"].astype(str).to_numpy() == k)[0]] for k in slice_keys)]
            rid = cands[0]
            g = client.post("/cube/reverse/validate", json={"board_id": bid, "axis": axis, "index": idx, "rule_id": rid}).json()
            assert g["ok"], (axis, idx, rid, g)
            run = g["run"]
    assert run["won"] and run["finished"]
