"""
accounts_store.py — player accounts and login sessions.

An account is created the first time someone signs in with Google. Its id
("acct_…") becomes that player's client id everywhere else, so the Pokédex,
Daily, Reverse and stats stores all work unchanged: they just see a
different, stable id that follows the player across devices.

Sessions are random tokens kept in an HttpOnly cookie. Only a SHA-256 hash
of each token is stored, so a copy of the database can't be used to log in.

Same conventions as the other stores: stdlib sqlite3, one lock, the same
database file (PDD_DB_PATH).
"""

import hashlib
import os
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Tuple

DB_PATH = Path(os.environ.get("PDD_DB_PATH") or (Path(__file__).resolve().parent / "data" / "pokedex.db"))

SESSION_DAYS = 180          # stay signed in for about six months
TRAINER_NAME_MIN = 3
TRAINER_NAME_MAX = 16
_NAME_OK = re.compile(r"^[A-Za-z0-9 _.\-]+$")

_lock = threading.Lock()

# token hash -> (account dict, cached_at). Every API request checks the
# session, so a short in-memory cache keeps that off the database.
_session_cache: dict = {}
_CACHE_TTL = 120.0


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def init_db() -> None:
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    id            TEXT PRIMARY KEY,
                    google_sub    TEXT UNIQUE,
                    email         TEXT NOT NULL DEFAULT '',
                    name          TEXT NOT NULL DEFAULT '',
                    picture       TEXT NOT NULL DEFAULT '',
                    trainer_name  TEXT NOT NULL DEFAULT '',
                    created_at    TEXT NOT NULL,
                    last_login    TEXT NOT NULL
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash  TEXT PRIMARY KEY,
                    account_id  TEXT NOT NULL,
                    created_at  TEXT NOT NULL,
                    expires_at  TEXT NOT NULL
                );
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_account ON sessions (account_id);")
            conn.commit()
        finally:
            conn.close()


_COLS = "id, email, name, picture, trainer_name, created_at"


def _row_to_account(row) -> Optional[dict]:
    if not row:
        return None
    keys = ("id", "email", "name", "picture", "trainer_name", "created_at")
    return dict(zip(keys, row))


def get_account(account_id: str) -> Optional[dict]:
    with _lock:
        conn = _connect()
        try:
            return _row_to_account(conn.execute(
                f"SELECT {_COLS} FROM accounts WHERE id = ?", (account_id,)).fetchone())
        finally:
            conn.close()


def default_trainer_name(name: str, email: str) -> str:
    """First name from Google (or the email's local part), cleaned to fit."""
    base = (name or "").strip().split(" ")[0] or (email or "").split("@")[0]
    base = re.sub(r"[^A-Za-z0-9 _.\-]", "", base)[:TRAINER_NAME_MAX].strip()
    return base if len(base) >= TRAINER_NAME_MIN else f"Trainer{secrets.randbelow(9000) + 1000}"


def upsert_google_account(sub: str, email: str, name: str, picture: str) -> Tuple[dict, bool]:
    """Find the account for this Google user, or create it. Returns
    (account, is_new). Email/name/picture are refreshed on every sign-in;
    the trainer name is the player's own and is never overwritten."""
    now = _now().isoformat()
    with _lock:
        conn = _connect()
        try:
            row = conn.execute("SELECT id FROM accounts WHERE google_sub = ?", (sub,)).fetchone()
            if row:
                conn.execute(
                    "UPDATE accounts SET email = ?, name = ?, picture = ?, last_login = ? WHERE id = ?",
                    (email, name, picture, now, row[0]),
                )
                conn.commit()
                acct_id, is_new = row[0], False
            else:
                acct_id = "acct_" + secrets.token_hex(12)
                conn.execute(
                    """
                    INSERT INTO accounts (id, google_sub, email, name, picture, trainer_name, created_at, last_login)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (acct_id, sub, email, name, picture, default_trainer_name(name, email), now, now),
                )
                conn.commit()
                is_new = True
            acct = _row_to_account(conn.execute(
                f"SELECT {_COLS} FROM accounts WHERE id = ?", (acct_id,)).fetchone())
            return acct, is_new
        finally:
            conn.close()


def set_trainer_name(account_id: str, trainer_name: str) -> Tuple[bool, str]:
    """Returns (ok, message). Names are 3-16 letters, numbers, spaces, _ . -"""
    name = re.sub(r"\s+", " ", (trainer_name or "").strip())
    if not (TRAINER_NAME_MIN <= len(name) <= TRAINER_NAME_MAX):
        return False, f"Use {TRAINER_NAME_MIN}–{TRAINER_NAME_MAX} characters."
    if not _NAME_OK.match(name):
        return False, "Letters, numbers, spaces, _ . and - only."
    with _lock:
        conn = _connect()
        try:
            conn.execute("UPDATE accounts SET trainer_name = ? WHERE id = ?", (name, account_id))
            conn.commit()
        finally:
            conn.close()
    _session_cache.clear()   # cached accounts carry the old name
    return True, name


# ── Sessions ──────────────────────────────────────────────────────────────────

def create_session(account_id: str) -> str:
    token = secrets.token_urlsafe(32)
    now = _now()
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO sessions (token_hash, account_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (_hash(token), account_id, now.isoformat(), (now + timedelta(days=SESSION_DAYS)).isoformat()),
            )
            conn.commit()
        finally:
            conn.close()
    return token


def account_for_session(token: Optional[str]) -> Optional[dict]:
    """The signed-in account for a session token, or None."""
    if not token or len(token) > 200:
        return None
    h = _hash(token)
    hit = _session_cache.get(h)
    if hit and time.monotonic() - hit[1] < _CACHE_TTL:
        return hit[0]
    with _lock:
        conn = _connect()
        try:
            row = conn.execute(
                f"""
                SELECT a.id, a.email, a.name, a.picture, a.trainer_name, a.created_at, s.expires_at
                FROM sessions s JOIN accounts a ON a.id = s.account_id
                WHERE s.token_hash = ?
                """,
                (h,),
            ).fetchone()
        finally:
            conn.close()
    if not row or row[6] < _now().isoformat():
        _session_cache.pop(h, None)
        return None
    acct = _row_to_account(row[:6])
    _session_cache[h] = (acct, time.monotonic())
    if len(_session_cache) > 5000:
        _session_cache.clear()
    return acct


def delete_session(token: Optional[str]) -> None:
    if not token:
        return
    h = _hash(token)
    _session_cache.pop(h, None)
    with _lock:
        conn = _connect()
        try:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (h,))
            conn.commit()
        finally:
            conn.close()
