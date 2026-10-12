"""
reverse_store.py — SQLite persistence for the Reverse Board "Category Index".

Reverse Board is the deduction mode: the grid comes pre-filled and the player
names the hidden row/column rule. The Category Index logs which rules a player
has ever *correctly identified* this way, and tracks progress toward naming
every category at least once.

Same design philosophy as pokedex_store.py, deliberately so:
  - client_id is an opaque string (localStorage UUID today, real account id
    later); merge_client() folds guest progress into an account with no schema
    change.
  - stdlib sqlite3 + a single lock, no server, nothing to install.
  - shares the SAME database file as pokedex_store, in its own table, so the
    two features can't collide and a single merge covers both.

Lives and hints for the daily board live here too (reverse_daily_misses,
reverse_daily_hints), so reloading the page can't reset them.

What counts as "identified": the caller (api_main) decides, exactly like
is_daily_seed() gates the Pokedex. Reverse Board only logs on full-size 9x9
boards, so custom/shrunk boards are practice and don't pad the Index.
"""

import os
import sqlite3
import threading
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Optional

# Same file as the Pokedex store, so one DB holds both features. PDD_DB_PATH
# (the shiny test mode uses it) moves both stores to the same other file.
DB_PATH = Path(os.environ.get("PDD_DB_PATH") or (Path(__file__).resolve().parent / "data" / "pokedex.db"))

_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_db() -> None:
    """Create the Category Index table/index if absent. Idempotent; safe to
    call on every startup alongside pokedex_store.init_db()."""
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS reverse_identified (
                    client_id     TEXT NOT NULL,
                    rule_id       TEXT NOT NULL,
                    rule_label    TEXT NOT NULL DEFAULT '',
                    first_named   TEXT NOT NULL,
                    times_named   INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY (client_id, rule_id)
                );
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_reverse_client ON reverse_identified (client_id);"
            )
            # Per-line progress on the DAILY reverse board, so a solved line
            # survives a refresh and the board stays "locked for the day". Keyed
            # by the daily date-seed; one row per solved line. axis is 'row'/'col'.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS reverse_daily_progress (
                    client_id      TEXT NOT NULL,
                    day_seed       TEXT NOT NULL,
                    axis           TEXT NOT NULL,
                    line_index     INTEGER NOT NULL,
                    named_rule_id  TEXT NOT NULL,
                    named_label    TEXT NOT NULL DEFAULT '',
                    solved_at      TEXT NOT NULL,
                    PRIMARY KEY (client_id, day_seed, axis, line_index)
                );
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_reverse_daily ON reverse_daily_progress (client_id, day_seed);"
            )
            # Wrong guesses on the daily board. Each row costs one life, so a
            # refresh can't hand lives back. The same wrong guess on the same
            # line is stored once and never charged twice.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS reverse_daily_misses (
                    client_id   TEXT NOT NULL,
                    day_seed    TEXT NOT NULL,
                    axis        TEXT NOT NULL,
                    line_index  INTEGER NOT NULL,
                    rule_id     TEXT NOT NULL,
                    missed_at   TEXT NOT NULL,
                    PRIMARY KEY (client_id, day_seed, axis, line_index, rule_id)
                );
                """
            )
            # The one hint per daily board. The primary key is the limit: a
            # second hint on the same day can't be inserted.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS reverse_daily_hints (
                    client_id   TEXT NOT NULL,
                    day_seed    TEXT NOT NULL,
                    axis        TEXT NOT NULL,
                    line_index  INTEGER NOT NULL,
                    rule_id     TEXT NOT NULL,
                    label       TEXT NOT NULL DEFAULT '',
                    used_at     TEXT NOT NULL,
                    PRIMARY KEY (client_id, day_seed)
                );
                """
            )
            conn.commit()
        finally:
            conn.close()


def record_identified(
    client_id: str,
    rule_id: str,
    rule_label: str = "",
) -> bool:
    """Record that `client_id` correctly named `rule_id` on a qualifying
    (full-size) Reverse Board.

    On the first correct identification of a rule, a row is inserted. On repeat
    identifications, times_named is bumped but first_named is preserved. This
    mirrors the Pokedex's idempotent-first-catch semantics.

    Returns True if this was the FIRST time this client named this rule (i.e.
    it advanced their Index completion), False if it was a repeat.
    """
    if not client_id or not rule_id:
        return False
    with _lock:
        conn = _connect()
        try:
            cur = conn.execute(
                """
                INSERT INTO reverse_identified (client_id, rule_id, rule_label, first_named, times_named)
                VALUES (?, ?, ?, ?, 1)
                ON CONFLICT(client_id, rule_id) DO UPDATE SET
                    times_named = times_named + 1
                """,
                (client_id, rule_id, rule_label, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            # rowcount is 1 for both insert and update on SQLite, so detect a
            # brand-new row by comparing times_named.
            row = conn.execute(
                "SELECT times_named FROM reverse_identified WHERE client_id = ? AND rule_id = ?",
                (client_id, rule_id),
            ).fetchone()
            return bool(row and row[0] == 1)
        finally:
            conn.close()


def get_identified(client_id: str) -> Dict[str, dict]:
    """Every rule this client has ever correctly named, keyed by rule_id, with
    its label, first-named timestamp, and how many times it's been named."""
    if not client_id:
        return {}
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(
                """
                SELECT rule_id, rule_label, first_named, times_named
                FROM reverse_identified WHERE client_id = ?
                ORDER BY first_named ASC
                """,
                (client_id,),
            ).fetchall()
            return {
                rule_id: {
                    "rule_label": rule_label,
                    "first_named": first_named,
                    "times_named": times_named,
                }
                for rule_id, rule_label, first_named, times_named in rows
            }
        finally:
            conn.close()


def get_identified_ids(client_id: str) -> List[str]:
    """Just the rule_ids this client has named, oldest first — the cheap call
    for computing 'X of N categories found'."""
    if not client_id:
        return []
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT rule_id FROM reverse_identified WHERE client_id = ? ORDER BY first_named ASC",
                (client_id,),
            ).fetchall()
            return [r[0] for r in rows]
        finally:
            conn.close()


def record_daily_line(
    client_id: str,
    day_seed: str,
    axis: str,
    line_index: int,
    named_rule_id: str,
    named_label: str = "",
) -> bool:
    """Persist that `client_id` solved one line of the daily reverse board for
    `day_seed`, naming `named_rule_id`. Idempotent per line: the first solve is
    kept (a line can only be solved once — the board is locked for the day).

    Returns True if this line was newly solved, False if already recorded.
    """
    if not client_id or not day_seed or axis not in ("row", "col"):
        return False
    with _lock:
        conn = _connect()
        try:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO reverse_daily_progress
                    (client_id, day_seed, axis, line_index, named_rule_id, named_label, solved_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (client_id, day_seed, axis, line_index, named_rule_id, named_label,
                 datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


def get_daily_progress(client_id: str, day_seed: str) -> Dict[str, dict]:
    """Solved lines for this client on this daily board, keyed 'row:3' / 'col:0',
    so the frontend can restore a locked-for-the-day board after a refresh."""
    if not client_id or not day_seed:
        return {}
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(
                """
                SELECT axis, line_index, named_rule_id, named_label, solved_at
                FROM reverse_daily_progress
                WHERE client_id = ? AND day_seed = ?
                """,
                (client_id, day_seed),
            ).fetchall()
            return {
                f"{axis}:{line_index}": {
                    "axis": axis,
                    "line_index": line_index,
                    "named_rule_id": named_rule_id,
                    "named_label": named_label,
                    "solved_at": solved_at,
                }
                for axis, line_index, named_rule_id, named_label, solved_at in rows
            }
        finally:
            conn.close()


def record_daily_miss(
    client_id: str,
    day_seed: str,
    axis: str,
    line_index: int,
    rule_id: str,
) -> bool:
    """Log a wrong guess on the daily board. Returns True if it's new (and so
    costs a life), False if this exact guess on this line was already logged."""
    if not client_id or not day_seed or axis not in ("row", "col") or not rule_id:
        return False
    with _lock:
        conn = _connect()
        try:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO reverse_daily_misses
                    (client_id, day_seed, axis, line_index, rule_id, missed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (client_id, day_seed, axis, line_index, rule_id,
                 datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


def get_daily_misses(client_id: str, day_seed: str) -> List[dict]:
    """Every wrong guess this client made on this daily board, oldest first."""
    if not client_id or not day_seed:
        return []
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(
                """
                SELECT axis, line_index, rule_id FROM reverse_daily_misses
                WHERE client_id = ? AND day_seed = ?
                ORDER BY missed_at ASC
                """,
                (client_id, day_seed),
            ).fetchall()
            return [{"axis": a, "line_index": i, "rule_id": r} for a, i, r in rows]
        finally:
            conn.close()


def record_daily_hint(
    client_id: str,
    day_seed: str,
    axis: str,
    line_index: int,
    rule_id: str,
    label: str = "",
) -> bool:
    """Spend the daily board's hint on one line. Returns False if the hint
    was already used today (nothing is written in that case)."""
    if not client_id or not day_seed or axis not in ("row", "col"):
        return False
    with _lock:
        conn = _connect()
        try:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO reverse_daily_hints
                    (client_id, day_seed, axis, line_index, rule_id, label, used_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (client_id, day_seed, axis, line_index, rule_id, label,
                 datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


def get_daily_hint(client_id: str, day_seed: str) -> Optional[dict]:
    """The line this client used today's hint on, or None."""
    if not client_id or not day_seed:
        return None
    with _lock:
        conn = _connect()
        try:
            row = conn.execute(
                """
                SELECT axis, line_index, rule_id, label FROM reverse_daily_hints
                WHERE client_id = ? AND day_seed = ?
                """,
                (client_id, day_seed),
            ).fetchone()
            if not row:
                return None
            return {"axis": row[0], "line_index": row[1], "rule_id": row[2], "label": row[3]}
        finally:
            conn.close()


def merge_client(from_client_id: str, to_client_id: str) -> int:
    """Fold guest Category Index progress into an account. Rules the target
    already named are kept (with their earlier timestamp) and the source's
    times_named is added on; rules only the source named are moved over.

    Returns the number of rules moved that the target didn't already have.
    """
    if not from_client_id or not to_client_id or from_client_id == to_client_id:
        return 0
    with _lock:
        conn = _connect()
        try:
            # Fold overlapping rules' counts into the target, then move the
            # non-overlapping ones.
            conn.execute(
                """
                UPDATE reverse_identified
                SET times_named = times_named + COALESCE((
                    SELECT s.times_named FROM reverse_identified s
                    WHERE s.client_id = ? AND s.rule_id = reverse_identified.rule_id
                ), 0)
                WHERE client_id = ?
                """,
                (from_client_id, to_client_id),
            )
            cur = conn.execute(
                """
                UPDATE reverse_identified
                SET client_id = ?
                WHERE client_id = ?
                  AND rule_id NOT IN (
                      SELECT rule_id FROM reverse_identified WHERE client_id = ?
                  )
                """,
                (to_client_id, from_client_id, to_client_id),
            )
            moved = cur.rowcount
            conn.execute("DELETE FROM reverse_identified WHERE client_id = ?", (from_client_id,))

            # Daily progress: move rows the target doesn't already have for the
            # same (day_seed, axis, line_index); drop the rest as duplicates.
            conn.execute(
                """
                UPDATE reverse_daily_progress
                SET client_id = ?
                WHERE client_id = ?
                  AND (day_seed, axis, line_index) NOT IN (
                      SELECT day_seed, axis, line_index FROM reverse_daily_progress WHERE client_id = ?
                  )
                """,
                (to_client_id, from_client_id, to_client_id),
            )
            conn.execute("DELETE FROM reverse_daily_progress WHERE client_id = ?", (from_client_id,))

            # Lives and hints follow the same rule: the account keeps its own
            # record for a day it already played; otherwise the guest's moves over.
            conn.execute(
                """
                UPDATE reverse_daily_misses
                SET client_id = ?
                WHERE client_id = ?
                  AND day_seed NOT IN (
                      SELECT day_seed FROM reverse_daily_misses WHERE client_id = ?
                  )
                """,
                (to_client_id, from_client_id, to_client_id),
            )
            conn.execute("DELETE FROM reverse_daily_misses WHERE client_id = ?", (from_client_id,))
            conn.execute(
                """
                UPDATE reverse_daily_hints
                SET client_id = ?
                WHERE client_id = ?
                  AND day_seed NOT IN (
                      SELECT day_seed FROM reverse_daily_hints WHERE client_id = ?
                  )
                """,
                (to_client_id, from_client_id, to_client_id),
            )
            conn.execute("DELETE FROM reverse_daily_hints WHERE client_id = ?", (from_client_id,))

            conn.commit()
            return moved
        finally:
            conn.close()


# ── Daily line stats ─────────────────────────────────────────────────────────
# How players named each line of the daily board. A line usually accepts more
# than one category, so this is a spread, not a single answer. Lines solved
# with the hint don't count. Board-wide counts are cached briefly; the single
# line share shown right after a solve is always live.
_STATS_TTL = 20.0
_line_cache: Dict[str, tuple] = {}

_COUNTED = """
    FROM reverse_daily_progress p
    LEFT JOIN reverse_daily_hints h
      ON h.client_id = p.client_id AND h.day_seed = p.day_seed
     AND h.axis = p.axis AND h.line_index = p.line_index
    WHERE p.day_seed = ? AND h.client_id IS NULL
"""


def line_share(day_seed: str, axis: str, line_index: int, rule_id: str) -> tuple:
    """(players who named rule_id for this line, players who named this line)."""
    with _lock:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT SUM(CASE WHEN p.named_rule_id = ? THEN 1 ELSE 0 END), COUNT(*)"
                + _COUNTED + " AND p.axis = ? AND p.line_index = ?",
                (rule_id, day_seed, axis, line_index),
            ).fetchone()
            return int(row[0] or 0), int(row[1] or 0)
        finally:
            conn.close()


def line_counts(day_seed: str, fresh: bool = False) -> dict:
    """{"lines": {("row", 3): {rule_id: count}}, "players": n} for one day."""
    now = time.monotonic()
    hit = _line_cache.get(day_seed)
    if hit and not fresh and now - hit[0] < _STATS_TTL:
        return hit[1]
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT p.axis, p.line_index, p.named_rule_id, COUNT(*)" + _COUNTED
                + " GROUP BY p.axis, p.line_index, p.named_rule_id",
                (day_seed,),
            ).fetchall()
            players = conn.execute(
                "SELECT COUNT(DISTINCT p.client_id)" + _COUNTED, (day_seed,),
            ).fetchone()[0]
        finally:
            conn.close()
    lines: Dict[tuple, Dict[str, int]] = {}
    for axis, idx, rid, n in rows:
        lines.setdefault((axis, idx), {})[rid] = n
    data = {"lines": lines, "players": int(players or 0)}
    _line_cache[day_seed] = (now, data)
    if len(_line_cache) > 4:
        _line_cache.pop(next(iter(_line_cache)), None)
    return data