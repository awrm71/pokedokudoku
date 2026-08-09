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
from typing import List

DB_PATH = Path(__file__).resolve().parent / "data" / "pokedex.db"

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
    "caught via Fire x Legendary"). is_shiny is accepted now so the schema
    doesn't need another migration once shiny catching actually exists;
    every caller today passes False.

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
            conn.commit()
            return cur.rowcount > 0
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

            conn.commit()
            return moved
        finally:
            conn.close()