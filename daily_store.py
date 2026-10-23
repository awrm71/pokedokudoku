"""
daily_store.py — the Daily Board's saved progress and its answer stats.

Two jobs, one table each:

  daily_runs   one row per player per day: wrong guesses, hints used,
               revives (an extra life after watching an ad), and whether
               the board is finished (won, out of lives, or ended early to
               see the stats). This is what makes the Daily survive
               a refresh, and what stops a reload from handing lives back.

  daily_picks  one row per player per square: the FIRST correct answer they
               gave there (or the hint they used). A square is identified by
               its canonical row/column index on that day's board, so the
               same Pokemon in two different squares counts separately.

Answer stats come straight from daily_picks:
    % for a Pokemon in a square = players who answered it there
                                  / players who filled that square
Hinted squares are saved (so they restore) but never counted.

Same conventions as pokedex_store / reverse_store: stdlib sqlite3, one lock,
an opaque client_id (the browser id today, an account id later — see
merge_client), and the same database file.
"""

import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

DB_PATH = Path(os.environ.get("PDD_DB_PATH") or (Path(__file__).resolve().parent / "data" / "pokedex.db"))

_lock = threading.Lock()

# Board-wide stats are one GROUP BY over the day's picks. They're cached for a
# few seconds so a burst of players finishing at once doesn't re-run it for
# every request. Single-square lookups (the % shown right after a pick) are
# always live.
_STATS_TTL = 20.0
_stats_cache: Dict[str, Tuple[float, dict]] = {}


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db() -> None:
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_runs (
                    day_seed     TEXT NOT NULL,
                    client_id    TEXT NOT NULL,
                    misses       INTEGER NOT NULL DEFAULT 0,
                    hints_used   INTEGER NOT NULL DEFAULT 0,
                    finished_at  TEXT,
                    started_at   TEXT NOT NULL,
                    PRIMARY KEY (day_seed, client_id)
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_picks (
                    day_seed     TEXT NOT NULL,
                    client_id    TEXT NOT NULL,
                    row_i        INTEGER NOT NULL,
                    col_j        INTEGER NOT NULL,
                    row_rule_id  TEXT NOT NULL DEFAULT '',
                    col_rule_id  TEXT NOT NULL DEFAULT '',
                    pokemon_key  TEXT NOT NULL,
                    hinted       INTEGER NOT NULL DEFAULT 0,
                    shiny        INTEGER NOT NULL DEFAULT 0,
                    picked_at    TEXT NOT NULL,
                    PRIMARY KEY (day_seed, client_id, row_i, col_j)
                );
                """
            )
            # Added later: revives used on this board (an ad-for-a-life).
            cols = {r[1] for r in conn.execute("PRAGMA table_info(daily_runs)")}
            if "revives" not in cols:
                conn.execute("ALTER TABLE daily_runs ADD COLUMN revives INTEGER NOT NULL DEFAULT 0")
            # Each day's boards, saved the first time they're made. A server
            # restart after a data fix must not reshuffle a board people are
            # already playing (picks are saved by row/column).
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS board_pins (
                    pin_key     TEXT PRIMARY KEY,
                    payload     TEXT NOT NULL,
                    created_at  TEXT NOT NULL
                );
                """
            )
            # Serves both the per-square % and the board-wide GROUP BY.
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_daily_picks_cell "
                "ON daily_picks (day_seed, row_i, col_j, hinted, pokemon_key);"
            )
            conn.commit()
        finally:
            conn.close()


# ── A player's run ────────────────────────────────────────────────────────────

def _ensure_run(conn: sqlite3.Connection, client_id: str, day_seed: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO daily_runs (day_seed, client_id, started_at) VALUES (?, ?, ?)",
        (day_seed, client_id, _now()),
    )


def get_run(client_id: str, day_seed: str) -> dict:
    """{misses, hints_used, revives, finished, picks: [{row_i, col_j, pokemon_key, hinted, shiny}]}.
    A player who hasn't started yet gets an empty run (nothing is written)."""
    out = {"misses": 0, "hints_used": 0, "revives": 0, "finished": False, "picks": []}
    if not client_id or not day_seed:
        return out
    with _lock:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT misses, hints_used, finished_at, revives FROM daily_runs WHERE day_seed = ? AND client_id = ?",
                (day_seed, client_id),
            ).fetchone()
            if row:
                out["misses"], out["hints_used"], out["finished"] = row[0], row[1], row[2] is not None
                out["revives"] = row[3]
            picks = conn.execute(
                """
                SELECT row_i, col_j, pokemon_key, hinted, shiny FROM daily_picks
                WHERE day_seed = ? AND client_id = ? ORDER BY picked_at ASC
                """,
                (day_seed, client_id),
            ).fetchall()
            out["picks"] = [
                {"row_i": r, "col_j": c, "pokemon_key": k, "hinted": bool(h), "shiny": bool(s)}
                for r, c, k, h, s in picks
            ]
            return out
        finally:
            conn.close()


def record_pick(client_id: str, day_seed: str, row_i: int, col_j: int,
                row_rule_id: str, col_rule_id: str, pokemon_key: str,
                hinted: bool = False, shiny: bool = False) -> bool:
    """Save a player's answer for one square. The first answer wins; returns
    False if this square already had one. A hint also counts toward hints_used."""
    if not client_id or not day_seed:
        return False
    with _lock:
        conn = _connect()
        try:
            _ensure_run(conn, client_id, day_seed)
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO daily_picks
                    (day_seed, client_id, row_i, col_j, row_rule_id, col_rule_id,
                     pokemon_key, hinted, shiny, picked_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (day_seed, client_id, row_i, col_j, row_rule_id, col_rule_id,
                 pokemon_key, int(hinted), int(shiny), _now()),
            )
            inserted = cur.rowcount > 0
            if inserted and hinted:
                conn.execute(
                    "UPDATE daily_runs SET hints_used = hints_used + 1 WHERE day_seed = ? AND client_id = ?",
                    (day_seed, client_id),
                )
            conn.commit()
            return inserted
        finally:
            conn.close()


def add_miss(client_id: str, day_seed: str) -> int:
    """Count one wrong guess; returns the new total."""
    if not client_id or not day_seed:
        return 0
    with _lock:
        conn = _connect()
        try:
            _ensure_run(conn, client_id, day_seed)
            conn.execute(
                "UPDATE daily_runs SET misses = misses + 1 WHERE day_seed = ? AND client_id = ?",
                (day_seed, client_id),
            )
            conn.commit()
            row = conn.execute(
                "SELECT misses FROM daily_runs WHERE day_seed = ? AND client_id = ?",
                (day_seed, client_id),
            ).fetchone()
            return row[0] if row else 0
        finally:
            conn.close()


def add_revive(client_id: str, day_seed: str, limit: int) -> bool:
    """Spend one revive on an unfinished board. False if the limit is used up
    or the board is already finished (one UPDATE, so two tabs can't both win)."""
    if not client_id or not day_seed:
        return False
    with _lock:
        conn = _connect()
        try:
            _ensure_run(conn, client_id, day_seed)
            cur = conn.execute(
                """
                UPDATE daily_runs SET revives = revives + 1
                WHERE day_seed = ? AND client_id = ? AND revives < ? AND finished_at IS NULL
                """,
                (day_seed, client_id, limit),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


def finish(client_id: str, day_seed: str) -> None:
    """Lock the player's board for the day (idempotent)."""
    if not client_id or not day_seed:
        return
    with _lock:
        conn = _connect()
        try:
            _ensure_run(conn, client_id, day_seed)
            conn.execute(
                "UPDATE daily_runs SET finished_at = COALESCE(finished_at, ?) WHERE day_seed = ? AND client_id = ?",
                (_now(), day_seed, client_id),
            )
            conn.commit()
        finally:
            conn.close()


# ── Answer stats ──────────────────────────────────────────────────────────────

def cell_share(day_seed: str, row_i: int, col_j: int, pokemon_key: str) -> Tuple[int, int]:
    """(players who answered pokemon_key in this square, players who filled it).
    Always live; used for the % shown right after a correct pick."""
    with _lock:
        conn = _connect()
        try:
            row = conn.execute(
                """
                SELECT SUM(CASE WHEN pokemon_key = ? THEN 1 ELSE 0 END), COUNT(*)
                FROM daily_picks
                WHERE day_seed = ? AND row_i = ? AND col_j = ? AND hinted = 0
                """,
                (pokemon_key, day_seed, row_i, col_j),
            ).fetchone()
            return int(row[0] or 0), int(row[1] or 0)
        finally:
            conn.close()


def board_counts(day_seed: str, fresh: bool = False) -> dict:
    """Every square's answer counts for the day:
        {"cells": {(row_i, col_j): {pokemon_key: count}}, "players": n}
    players = distinct people with at least one counted answer today."""
    now = time.monotonic()
    hit = _stats_cache.get(day_seed)
    if hit and not fresh and now - hit[0] < _STATS_TTL:
        return hit[1]
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(
                """
                SELECT row_i, col_j, pokemon_key, COUNT(*) FROM daily_picks
                WHERE day_seed = ? AND hinted = 0
                GROUP BY row_i, col_j, pokemon_key
                """,
                (day_seed,),
            ).fetchall()
            players = conn.execute(
                "SELECT COUNT(DISTINCT client_id) FROM daily_picks WHERE day_seed = ? AND hinted = 0",
                (day_seed,),
            ).fetchone()[0]
        finally:
            conn.close()
    cells: Dict[Tuple[int, int], Dict[str, int]] = {}
    for r, c, k, n in rows:
        cells.setdefault((r, c), {})[k] = n
    data = {"cells": cells, "players": int(players or 0)}
    _stats_cache[day_seed] = (now, data)
    if len(_stats_cache) > 4:            # only today (and maybe yesterday) matter
        _stats_cache.pop(next(iter(_stats_cache)), None)
    return data


def forget_cache(day_seed: Optional[str] = None) -> None:
    if day_seed is None:
        _stats_cache.clear()
    else:
        _stats_cache.pop(day_seed, None)


# ── Accounts (later) ──────────────────────────────────────────────────────────

# ── Pinned boards ─────────────────────────────────────────────────────────────

def get_pin(pin_key: str) -> Optional[dict]:
    """The saved board for this key ("daily:2026-10-23:9x9"), or None."""
    with _lock:
        conn = _connect()
        try:
            row = conn.execute("SELECT payload FROM board_pins WHERE pin_key = ?", (pin_key,)).fetchone()
        finally:
            conn.close()
    if not row:
        return None
    try:
        return json.loads(row[0])
    except ValueError:
        return None


def save_pin(pin_key: str, payload: dict) -> dict:
    """Save a board unless one is already saved; returns whichever is kept
    (so two workers generating at once still agree)."""
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO board_pins (pin_key, payload, created_at) VALUES (?, ?, ?)",
                (pin_key, json.dumps(payload), _now()),
            )
            conn.commit()
            row = conn.execute("SELECT payload FROM board_pins WHERE pin_key = ?", (pin_key,)).fetchone()
        finally:
            conn.close()
    return json.loads(row[0]) if row else payload


def seed_pins(path) -> int:
    """Load boards shipped with a release (data/board_pins.json) so a data
    fix deployed mid-day keeps that day's boards. Never overwrites."""
    try:
        pins = json.loads(open(path, encoding="utf-8").read())
    except (OSError, ValueError):
        return 0
    n = 0
    for key, payload in pins.items():
        if get_pin(key) is None:
            save_pin(key, payload)
            n += 1
    return n


def merge_client(from_client_id: str, to_client_id: str) -> int:
    """Move a guest's Daily runs onto an account. For a day both of them
    played, the account's own run is kept. Returns the number of days moved."""
    if not from_client_id or not to_client_id or from_client_id == to_client_id:
        return 0
    with _lock:
        conn = _connect()
        try:
            cur = conn.execute(
                """
                UPDATE daily_runs SET client_id = ?
                WHERE client_id = ?
                  AND day_seed NOT IN (SELECT day_seed FROM daily_runs WHERE client_id = ?)
                """,
                (to_client_id, from_client_id, to_client_id),
            )
            moved = cur.rowcount
            conn.execute(
                """
                UPDATE daily_picks SET client_id = ?
                WHERE client_id = ?
                  AND day_seed NOT IN (SELECT DISTINCT day_seed FROM daily_picks WHERE client_id = ?)
                """,
                (to_client_id, from_client_id, to_client_id),
            )
            conn.execute("DELETE FROM daily_runs WHERE client_id = ?", (from_client_id,))
            conn.execute("DELETE FROM daily_picks WHERE client_id = ?", (from_client_id,))
            conn.commit()
            forget_cache()
            return moved
        finally:
            conn.close()
