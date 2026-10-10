"""
pokedex_store.py — lightweight SQLite persistence for per-client Pokedex tracking.

Tracks which Pokemon a given "client" has correctly used as an answer on a
DAILY board. `client_id` is deliberately just an opaque string:
- Today, it's a UUID the frontend generates and stores in localStorage
  (see app.js's getClientId()) — no account needed.
- Later, when real accounts exist, it can be a real account id instead.
- merge_client() moves rows from one id to another for exactly that
  anonymous -> real-account migration, so nothing about this schema needs
  to change when accounts are added.

Which boards count as "daily" is decided by the caller (api_main.py's
is_daily_seed()), not by this module — this module just records whatever
it's told to record.

This uses stdlib sqlite3 with a simple lock rather than an ORM or
connection pool, matching the project's "no admin required" philosophy:
a single file, no server process, nothing to install.
"""

import sqlite3
import threading
from pathlib import Path
from datetime import datetime, timezone
from typing import Callable, List, Optional
from datetime import date, timedelta

import os
# PDD_DB_PATH points the server at a different database file (handy for
# testing shinies without touching your real Pokédex).
DB_PATH = Path(os.environ.get("PDD_DB_PATH") or (Path(__file__).resolve().parent / "data" / "pokedex.db"))

_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_db() -> None:
    """Create the table/index if they don't exist yet, and migrate older
    databases forward. Safe to call on every startup — this is idempotent,
    including for databases created before row_rule_label/col_rule_label/
    is_shiny existed."""
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pokedex_entries (
                    client_id       TEXT NOT NULL,
                    pokemon_key     TEXT NOT NULL,
                    first_caught    TEXT NOT NULL,
                    board_seed      TEXT NOT NULL,
                    row_rule_label  TEXT NOT NULL DEFAULT '',
                    col_rule_label  TEXT NOT NULL DEFAULT '',
                    is_shiny        INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (client_id, pokemon_key)
                );
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_pokedex_client ON pokedex_entries (client_id);"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pokedex_rule_uses (
                    client_id    TEXT NOT NULL,
                    pokemon_key  TEXT NOT NULL,
                    rule_label   TEXT NOT NULL,
                    first_used   TEXT NOT NULL,
                    PRIMARY KEY (client_id, pokemon_key, rule_label)
                );
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_rule_uses_client ON pokedex_rule_uses (client_id);"
            )
            # ── Shinies ────────────────────────────────────────────────────
            # The Shiny Dex. Kept apart from pokedex_entries on purpose: when a
            # player completes their Pokédex and restarts it (prestige), their
            # regular entries are cleared but every shiny stays here forever.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS shiny_catches (
                    client_id    TEXT NOT NULL,
                    pokemon_key  TEXT NOT NULL,
                    first_seen   TEXT NOT NULL,
                    board_seed   TEXT NOT NULL,
                    PRIMARY KEY (client_id, pokemon_key)
                );
                """
            )
            # One shiny roll per cell per daily board. Without this, reloading
            # the daily board and re-entering the same answers would re-roll
            # every cell, so the ledger makes 81 rolls a day the ceiling.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS shiny_rolls (
                    client_id    TEXT NOT NULL,
                    board_seed   TEXT NOT NULL,
                    row_i        INTEGER NOT NULL,
                    col_j        INTEGER NOT NULL,
                    pokemon_key  TEXT NOT NULL,
                    shiny        INTEGER NOT NULL,
                    PRIMARY KEY (client_id, board_seed, row_i, col_j)
                );
                """
            )
            # Days the player made at least one correct Daily pick — drives the
            # streak bonus. (pokedex_entries only records first catches, so it
            # can't tell you whether someone played on a given day.)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_activity (
                    client_id   TEXT NOT NULL,
                    board_seed  TEXT NOT NULL,
                    PRIMARY KEY (client_id, board_seed)
                );
                """
            )
            # Shiny Charms earned by completing (and restarting) the Pokédex.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pokedex_prestige (
                    client_id     TEXT NOT NULL,
                    rank          INTEGER NOT NULL,
                    sealed_at     TEXT NOT NULL,
                    caught_count  INTEGER NOT NULL,
                    PRIMARY KEY (client_id, rank)
                );
                """
            )
            # Migrate databases created before these columns existed. SQLite
            # has no "ADD COLUMN IF NOT EXISTS", so we just swallow the
            # "duplicate column" error on databases that already have it.
            for ddl in (
                "ALTER TABLE pokedex_entries ADD COLUMN row_rule_label TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE pokedex_entries ADD COLUMN col_rule_label TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE pokedex_entries ADD COLUMN is_shiny INTEGER NOT NULL DEFAULT 0",
            ):
                try:
                    conn.execute(ddl)
                except sqlite3.OperationalError:
                    pass  # column already exists
            _backfill_rule_uses_from_entries(conn)
            conn.commit()
        finally:
            conn.close()


def _backfill_rule_uses_from_entries(conn: sqlite3.Connection) -> None:
    """Catches made before pokedex_rule_uses existed have a valid row/col
    catch context on pokedex_entries but no corresponding row here — so they
    show up with 0 confirmed categories despite demonstrably having satisfied
    their own two rules. This copies each entry's row/col labels into
    pokedex_rule_uses if not already present.

    Idempotent (INSERT OR IGNORE) and cheap — safe to run on every startup;
    it's a no-op once everything's caught up."""
    rows = conn.execute(
        "SELECT client_id, pokemon_key, row_rule_label, col_rule_label, first_caught FROM pokedex_entries"
    ).fetchall()
    for client_id, pokemon_key, row_label, col_label, first_caught in rows:
        for label in (row_label, col_label):
            if label:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO pokedex_rule_uses (client_id, pokemon_key, rule_label, first_used)
                    VALUES (?, ?, ?, ?)
                    """,
                    (client_id, pokemon_key, label, first_caught),
                )


def record_catch(
    client_id: str,
    pokemon_key: str,
    board_seed: str,
    row_rule_label: str = "",
    col_rule_label: str = "",
    is_shiny: bool = False,
) -> bool:
    """Record that `client_id` correctly used `pokemon_key` on a daily board.

    row_rule_label/col_rule_label capture *how* it was caught — the two
    board rules it satisfied — so the UI can tell that story later (e.g.
    "caught via Fire x Legendary"). is_shiny marks a catch that was shiny;
    if the Pokémon was already caught normally and is now caught shiny, the
    existing row is upgraded to shiny (the rest of its history is kept).

    Idempotent: if this pokemon was already caught by this client, the
    original row (including its original catch context) is preserved
    (INSERT OR IGNORE) rather than overwritten.

    Returns True if this was a brand-new catch, False if it was already caught.
    """
    if not client_id or not pokemon_key:
        return False
    with _lock:
        conn = _connect()
        try:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO pokedex_entries
                    (client_id, pokemon_key, first_caught, board_seed,
                     row_rule_label, col_rule_label, is_shiny)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    client_id, pokemon_key, datetime.now(timezone.utc).isoformat(), board_seed,
                    row_rule_label, col_rule_label, int(bool(is_shiny)),
                ),
            )
            is_new = cur.rowcount > 0
            if not is_new and is_shiny:
                conn.execute(
                    "UPDATE pokedex_entries SET is_shiny = 1 WHERE client_id = ? AND pokemon_key = ?",
                    (client_id, pokemon_key),
                )
            conn.commit()
            return is_new
        finally:
            conn.close()


def get_caught_keys(client_id: str) -> List[str]:
    """All pokemon keys this client has ever caught, oldest first."""
    if not client_id:
        return []
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT pokemon_key FROM pokedex_entries WHERE client_id = ? ORDER BY first_caught ASC",
                (client_id,),
            ).fetchall()
            return [r[0] for r in rows]
        finally:
            conn.close()


def record_rule_use(client_id: str, pokemon_key: str, rule_label: str) -> bool:
    """Record that `client_id` has, at some point, used `pokemon_key` to
    satisfy `rule_label` on a daily board. Call this for BOTH the row and
    column rule on every correct daily catch — including repeat catches of
    a Pokemon that's already in the dex. That's what makes a Pokemon's
    confirmed-category list keep growing every time you reuse it, even
    though catching it again doesn't add to your overall caught_count.

    Idempotent per (client, pokemon, rule): the first time a given rule is
    satisfied by a given Pokemon is the only one that's kept.

    Returns True if this was a brand-new (pokemon, rule) combination.
    """
    if not client_id or not pokemon_key or not rule_label:
        return False
    with _lock:
        conn = _connect()
        try:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO pokedex_rule_uses (client_id, pokemon_key, rule_label, first_used)
                VALUES (?, ?, ?, ?)
                """,
                (client_id, pokemon_key, rule_label, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


def get_rule_uses(client_id: str) -> dict:
    """All confirmed rule categories per pokemon_key this client has ever
    satisfied with that Pokemon on a daily board, oldest first. This is the
    data behind each dex entry's growing 'Also:' list."""
    if not client_id:
        return {}
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(
                """
                SELECT pokemon_key, rule_label FROM pokedex_rule_uses
                WHERE client_id = ? ORDER BY first_used ASC
                """,
                (client_id,),
            ).fetchall()
            out: dict = {}
            for key, label in rows:
                out.setdefault(key, []).append(label)
            return out
        finally:
            conn.close()


def get_caught_map(client_id: str) -> dict:
    """All pokemon keys this client has caught, mapped to their first-caught
    ISO-8601 timestamp. Kept simple/stable for callers that only need the
    caught/uncaught state — see get_caught_details() for the full picture."""
    if not client_id:
        return {}
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT pokemon_key, first_caught FROM pokedex_entries WHERE client_id = ?",
                (client_id,),
            ).fetchall()
            return {k: t for k, t in rows}
        finally:
            conn.close()


def get_caught_details(client_id: str) -> dict:
    """All pokemon keys this client has caught, mapped to their full catch
    context: first_caught, board_seed (which daily board), row_rule_label /
    col_rule_label (which rule pairing they satisfied), and is_shiny."""
    if not client_id:
        return {}
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(
                """
                SELECT pokemon_key, first_caught, board_seed,
                       row_rule_label, col_rule_label, is_shiny
                FROM pokedex_entries WHERE client_id = ?
                """,
                (client_id,),
            ).fetchall()
            return {
                key: {
                    "first_caught": first_caught,
                    "board_seed": board_seed,
                    "row_rule_label": row_rule_label,
                    "col_rule_label": col_rule_label,
                    "is_shiny": bool(is_shiny),
                }
                for key, first_caught, board_seed, row_rule_label, col_rule_label, is_shiny in rows
            }
        finally:
            conn.close()


def merge_client(from_client_id: str, to_client_id: str) -> int:
    """Move all entries from one client_id to another.

    Intended use: at signup/login time, call this once with
    (anonymous_device_id, real_account_id) to fold the guest's progress
    into their new account. Entries the target id already has are kept;
    conflicting duplicates from the source id are dropped rather than
    overwriting the target's (earlier) first_caught timestamp. Also merges
    pokedex_rule_uses so a Pokemon's confirmed-category history isn't lost.

    Returns the number of pokedex_entries rows actually moved (not counting
    dropped dupes; rule_uses are merged the same way but not counted).
    """
    if not from_client_id or not to_client_id or from_client_id == to_client_id:
        return 0
    with _lock:
        conn = _connect()
        try:
            cur = conn.execute(
                """
                UPDATE pokedex_entries
                SET client_id = ?
                WHERE client_id = ?
                  AND pokemon_key NOT IN (
                      SELECT pokemon_key FROM pokedex_entries WHERE client_id = ?
                  )
                """,
                (to_client_id, from_client_id, to_client_id),
            )
            moved = cur.rowcount
            # Whatever's left under the old id is a dupe already present under
            # the new id (kept there with its original timestamp) — drop it.
            conn.execute("DELETE FROM pokedex_entries WHERE client_id = ?", (from_client_id,))

            conn.execute(
                """
                UPDATE pokedex_rule_uses
                SET client_id = ?
                WHERE client_id = ?
                  AND (pokemon_key, rule_label) NOT IN (
                      SELECT pokemon_key, rule_label FROM pokedex_rule_uses WHERE client_id = ?
                  )
                """,
                (to_client_id, from_client_id, to_client_id),
            )
            conn.execute("DELETE FROM pokedex_rule_uses WHERE client_id = ?", (from_client_id,))

            # Shinies, activity days and the roll ledger: move, keep the
            # target's copy on any clash.
            for table in ("shiny_catches", "daily_activity"):
                conn.execute(
                    f"""
                    UPDATE OR IGNORE {table} SET client_id = ?
                    WHERE client_id = ?
                    """,
                    (to_client_id, from_client_id),
                )
                conn.execute(f"DELETE FROM {table} WHERE client_id = ?", (from_client_id,))
            conn.execute("UPDATE OR IGNORE shiny_rolls SET client_id = ? WHERE client_id = ?", (to_client_id, from_client_id))
            conn.execute("DELETE FROM shiny_rolls WHERE client_id = ?", (from_client_id,))
            # Charms: keep whichever id had completed the Pokédex more times.
            src = conn.execute("SELECT COUNT(*) FROM pokedex_prestige WHERE client_id = ?", (from_client_id,)).fetchone()[0]
            dst = conn.execute("SELECT COUNT(*) FROM pokedex_prestige WHERE client_id = ?", (to_client_id,)).fetchone()[0]
            if src > dst:
                conn.execute("DELETE FROM pokedex_prestige WHERE client_id = ?", (to_client_id,))
                conn.execute("UPDATE pokedex_prestige SET client_id = ? WHERE client_id = ?", (to_client_id, from_client_id))
            conn.execute("DELETE FROM pokedex_prestige WHERE client_id = ?", (from_client_id,))

            conn.commit()
            return moved
        finally:
            conn.close()

# ══════════════════════════════════════════════════════════════════════════
# Shinies, streaks and Shiny Charms
# ══════════════════════════════════════════════════════════════════════════

def record_daily_activity(client_id: str, board_seed: str) -> None:
    """Mark that this client made a correct pick on this daily board."""
    if not client_id or not board_seed:
        return
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO daily_activity (client_id, board_seed) VALUES (?, ?)",
                (client_id, board_seed),
            )
            conn.commit()
        finally:
            conn.close()


def get_streak(client_id: str, today: Optional[date] = None) -> int:
    """Consecutive daily boards played, counting back from today (or from
    yesterday if today hasn't been played yet, so a streak isn't shown as
    broken first thing in the morning)."""
    if not client_id:
        return 0
    today = today or date.today()
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT board_seed FROM daily_activity WHERE client_id = ?", (client_id,)
            ).fetchall()
        finally:
            conn.close()
    days = {r[0] for r in rows}
    cursor = today if today.isoformat() in days else today - timedelta(days=1)
    streak = 0
    while cursor.isoformat() in days:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def roll_once(
    client_id: str,
    board_seed: str,
    row_i: int,
    col_j: int,
    pokemon_key: str,
    roll: Callable[[], bool],
) -> bool:
    """Shiny roll for one cell of one daily board, at most once.

    The first correct pick in a cell calls `roll()` and stores the result.
    Re-submitting the same Pokémon in that cell (say, after a page reload)
    returns the stored result, so a shiny stays shiny. A different Pokémon
    in an already-rolled cell is never shiny: the roll was spent."""
    with _lock:
        conn = _connect()
        try:
            row = conn.execute(
                """
                SELECT pokemon_key, shiny FROM shiny_rolls
                WHERE client_id = ? AND board_seed = ? AND row_i = ? AND col_j = ?
                """,
                (client_id, board_seed, row_i, col_j),
            ).fetchone()
            if row is not None:
                return bool(row[1]) and row[0] == pokemon_key
            shiny = bool(roll())
            conn.execute(
                """
                INSERT OR IGNORE INTO shiny_rolls
                    (client_id, board_seed, row_i, col_j, pokemon_key, shiny)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (client_id, board_seed, row_i, col_j, pokemon_key, int(shiny)),
            )
            conn.commit()
            return shiny
        finally:
            conn.close()


def record_shiny(client_id: str, pokemon_key: str, board_seed: str) -> bool:
    """Add a Pokémon to the client's Shiny Dex. Returns True if it's new."""
    if not client_id or not pokemon_key:
        return False
    with _lock:
        conn = _connect()
        try:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO shiny_catches (client_id, pokemon_key, first_seen, board_seed)
                VALUES (?, ?, ?, ?)
                """,
                (client_id, pokemon_key, datetime.now(timezone.utc).isoformat(), board_seed),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


def get_shiny_map(client_id: str) -> dict:
    """Every Pokémon in this client's Shiny Dex -> {first_seen, board_seed}."""
    if not client_id:
        return {}
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT pokemon_key, first_seen, board_seed FROM shiny_catches WHERE client_id = ?",
                (client_id,),
            ).fetchall()
            return {k: {"first_seen": t, "board_seed": s} for k, t, s in rows}
        finally:
            conn.close()


def get_prestige(client_id: str) -> List[dict]:
    """Completed-and-restarted Pokédexes, oldest first. len() == charms."""
    if not client_id:
        return []
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(
                """
                SELECT rank, sealed_at, caught_count FROM pokedex_prestige
                WHERE client_id = ? ORDER BY rank ASC
                """,
                (client_id,),
            ).fetchall()
            return [{"rank": r, "sealed_at": t, "caught_count": c} for r, t, c in rows]
        finally:
            conn.close()


def prestige(client_id: str, caught_count: int) -> int:
    """Seal a completed Pokédex: log it, earn a charm, and clear the regular
    entries so the next run starts fresh. The Shiny Dex, the record of which
    categories each Pokémon has confirmed, and the streak are all kept.
    Returns the new charm count. The caller checks the dex is complete."""
    with _lock:
        conn = _connect()
        try:
            rank = conn.execute(
                "SELECT COALESCE(MAX(rank), 0) + 1 FROM pokedex_prestige WHERE client_id = ?",
                (client_id,),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO pokedex_prestige (client_id, rank, sealed_at, caught_count) VALUES (?, ?, ?, ?)",
                (client_id, rank, datetime.now(timezone.utc).isoformat(), caught_count),
            )
            conn.execute("DELETE FROM pokedex_entries WHERE client_id = ?", (client_id,))
            conn.commit()
            return rank
        finally:
            conn.close()