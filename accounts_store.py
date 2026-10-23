"""
accounts_store.py — player accounts, sign-in methods and sessions.

Two ways to sign in, one kind of account:
  • Google (google_sub)        — created on first Google sign-in
  • Email + password           — created with the sign-up form

If someone signs up with a password and later uses Google with the same
email (or the other way round), both land on the same account.

An account's id ("acct_…") becomes that player's client id everywhere else,
so the Pokédex, Daily, Reverse and stats stores work unchanged: they just
see a stable id that follows the player across devices.

Personal touches kept here:
  • trainer_name — what they want to be called; unique (case-insensitive),
                   it's the name leaderboards will show
  • partner      — a Pokémon they pick; its sprite is their avatar

Security notes:
  • Passwords: scrypt (stdlib hashlib), per-user salt. Never stored plain.
  • Sessions: random tokens in an HttpOnly cookie; only a SHA-256 hash of
    each token is stored.
  • Reset codes: 6 digits, hashed, 15 minutes, 5 tries.

Same conventions as the other stores: stdlib sqlite3, one lock, the same
database file (PDD_DB_PATH). Older databases are migrated in place.
"""

import base64
import hashlib
import hmac
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

SESSION_DAYS = 180
TRAINER_NAME_MIN = 3
TRAINER_NAME_MAX = 16
PASSWORD_MIN = 8
RESET_MINUTES = 15
RESET_TRIES = 5
_NAME_OK = re.compile(r"^[A-Za-z0-9 _.\-]+$")
_EMAIL_OK = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# scrypt cost: 16 MB of memory and ~50 ms per hash. Slow enough to make
# guessing expensive, fast enough for a sign-in.
_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}

_lock = threading.Lock()
_session_cache: dict = {}
_CACHE_TTL = 120.0


class AccountError(Exception):
    """A problem the player can fix; the message is shown to them as is."""
    def __init__(self, message: str, field: str = ""):
        super().__init__(message)
        self.field = field


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def norm_email(email: str) -> str:
    return (email or "").strip().lower()


def name_key(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip()).lower()


# ── Schema ────────────────────────────────────────────────────────────────────

_NEW_COLUMNS = {
    "email_key": "TEXT",
    "password_hash": "TEXT",
    "email_verified": "INTEGER NOT NULL DEFAULT 0",
    "trainer_key": "TEXT",
    "partner_key": "TEXT NOT NULL DEFAULT ''",
    "partner_name": "TEXT NOT NULL DEFAULT ''",
    "partner_id": "INTEGER",
}


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
            have = {r[1] for r in conn.execute("PRAGMA table_info(accounts)")}
            for col, decl in _NEW_COLUMNS.items():
                if col not in have:
                    conn.execute(f"ALTER TABLE accounts ADD COLUMN {col} {decl}")
            _backfill_keys(conn)
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_email ON accounts (email_key) WHERE email_key IS NOT NULL AND email_key != ''")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_trainer ON accounts (trainer_key) WHERE trainer_key IS NOT NULL")
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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS password_resets (
                    email_key   TEXT PRIMARY KEY,
                    code_hash   TEXT NOT NULL,
                    expires_at  TEXT NOT NULL,
                    tries       INTEGER NOT NULL DEFAULT 0
                );
                """
            )
            conn.commit()
        finally:
            conn.close()


def _backfill_keys(conn: sqlite3.Connection) -> None:
    """Fill email_key/trainer_key for accounts made before they existed.
    Trainer names must now be unique, so any duplicate gets a number."""
    rows = conn.execute("SELECT id, email, trainer_name, trainer_key, email_key FROM accounts ORDER BY created_at").fetchall()
    taken = {r[3] for r in rows if r[3]}
    for acct_id, email, trainer, tkey, ekey in rows:
        if not ekey and email:
            conn.execute("UPDATE accounts SET email_key = ? WHERE id = ?", (norm_email(email), acct_id))
        if not tkey:
            name = trainer or "Trainer"
            key = name_key(name)
            n = 2
            while key in taken:
                suffix = str(n)
                name = (trainer or "Trainer")[: TRAINER_NAME_MAX - len(suffix)] + suffix
                key = name_key(name)
                n += 1
            taken.add(key)
            conn.execute("UPDATE accounts SET trainer_name = ?, trainer_key = ? WHERE id = ?", (name, key, acct_id))


# ── Reading accounts ──────────────────────────────────────────────────────────

_COLS = ("id, email, name, picture, trainer_name, created_at, partner_key, partner_name, partner_id, "
         "password_hash IS NOT NULL AND password_hash != '', google_sub IS NOT NULL")
_KEYS = ("id", "email", "name", "picture", "trainer_name", "created_at", "partner_key", "partner_name",
         "partner_id", "has_password", "has_google")


def _row(row) -> Optional[dict]:
    if not row:
        return None
    d = dict(zip(_KEYS, row))
    d["has_password"] = bool(d["has_password"])
    d["has_google"] = bool(d["has_google"])
    return d


def get_account(account_id: str) -> Optional[dict]:
    with _lock:
        conn = _connect()
        try:
            return _row(conn.execute(f"SELECT {_COLS} FROM accounts WHERE id = ?", (account_id,)).fetchone())
        finally:
            conn.close()


# ── Trainer names ─────────────────────────────────────────────────────────────

def check_trainer_name(name: str) -> str:
    """Clean the name, or raise AccountError with a friendly reason."""
    name = re.sub(r"\s+", " ", (name or "").strip())
    if not (TRAINER_NAME_MIN <= len(name) <= TRAINER_NAME_MAX):
        raise AccountError(f"Trainer names are {TRAINER_NAME_MIN} to {TRAINER_NAME_MAX} characters.", "trainer_name")
    if not _NAME_OK.match(name):
        raise AccountError("Use letters, numbers, spaces, _ . or -", "trainer_name")
    return name


def name_available(name: str, except_account: str = "") -> bool:
    with _lock:
        conn = _connect()
        try:
            row = conn.execute("SELECT id FROM accounts WHERE trainer_key = ?", (name_key(name),)).fetchone()
            return not row or row[0] == except_account
        finally:
            conn.close()


def _free_name(conn: sqlite3.Connection, wanted: str) -> Tuple[str, str]:
    """wanted, or wanted2, wanted3… — the first that isn't taken."""
    base = wanted[:TRAINER_NAME_MAX]
    name, n = base, 2
    while conn.execute("SELECT 1 FROM accounts WHERE trainer_key = ?", (name_key(name),)).fetchone():
        suffix = str(n)
        name = base[: TRAINER_NAME_MAX - len(suffix)] + suffix
        n += 1
    return name, name_key(name)


def default_trainer_name(name: str, email: str) -> str:
    """First name from Google (or the email's local part), cleaned to fit."""
    base = (name or "").strip().split(" ")[0] or (email or "").split("@")[0]
    base = re.sub(r"[^A-Za-z0-9 _.\-]", "", base)[:TRAINER_NAME_MAX].strip()
    return base if len(base) >= TRAINER_NAME_MIN else f"Trainer{secrets.randbelow(9000) + 1000}"


# ── Passwords ─────────────────────────────────────────────────────────────────

def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, dklen=32, maxmem=64 * 1024 * 1024, **_SCRYPT)
    return "scrypt${n}${r}${p}${s}${h}".format(
        n=_SCRYPT["n"], r=_SCRYPT["r"], p=_SCRYPT["p"],
        s=base64.b64encode(salt).decode(), h=base64.b64encode(dk).decode())


def _check_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, s, h = stored.split("$")
        if algo != "scrypt":
            return False
        dk = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(s), dklen=32,
                            n=int(n), r=int(r), p=int(p), maxmem=64 * 1024 * 1024)
        return hmac.compare_digest(dk, base64.b64decode(h))
    except Exception:
        return False


def check_password_rules(password: str) -> None:
    if len(password or "") < PASSWORD_MIN:
        raise AccountError(f"Use at least {PASSWORD_MIN} characters.", "password")
    if len(password) > 200:
        raise AccountError("That password is too long.", "password")


# A hash to compare against when the email doesn't exist, so a failed
# sign-in takes the same time either way (no hint whether an email is used).
_DUMMY_HASH = None


def _dummy_hash() -> str:
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = _hash_password(secrets.token_hex(8))
    return _DUMMY_HASH


# ── Creating and signing in ───────────────────────────────────────────────────

def create_email_account(email: str, password: str, trainer_name: str) -> dict:
    email = (email or "").strip()
    ekey = norm_email(email)
    if not _EMAIL_OK.match(ekey) or len(ekey) > 254:
        raise AccountError("That doesn't look like an email address.", "email")
    check_password_rules(password)
    name = check_trainer_name(trainer_name)
    pw_hash = _hash_password(password)
    now = _now().isoformat()
    with _lock:
        conn = _connect()
        try:
            row = conn.execute("SELECT google_sub, password_hash FROM accounts WHERE email_key = ?", (ekey,)).fetchone()
            if row:
                if row[0] and not row[1]:
                    raise AccountError("That email already signs in with Google. Use the Google button.", "email")
                raise AccountError("There's already an account with that email. Sign in instead.", "email")
            if conn.execute("SELECT 1 FROM accounts WHERE trainer_key = ?", (name_key(name),)).fetchone():
                raise AccountError("That trainer name is taken.", "trainer_name")
            acct_id = "acct_" + secrets.token_hex(12)
            conn.execute(
                """
                INSERT INTO accounts (id, google_sub, email, email_key, password_hash, email_verified,
                                      name, picture, trainer_name, trainer_key, created_at, last_login)
                VALUES (?, NULL, ?, ?, ?, 0, '', '', ?, ?, ?, ?)
                """,
                (acct_id, email, ekey, pw_hash, name, name_key(name), now, now),
            )
            conn.commit()
            return _row(conn.execute(f"SELECT {_COLS} FROM accounts WHERE id = ?", (acct_id,)).fetchone())
        except sqlite3.IntegrityError:
            raise AccountError("That email or trainer name was just taken. Try again.", "email")
        finally:
            conn.close()


def email_sign_in(email: str, password: str) -> Optional[dict]:
    """The account if email + password match, else None."""
    ekey = norm_email(email)
    with _lock:
        conn = _connect()
        try:
            row = conn.execute("SELECT id, password_hash FROM accounts WHERE email_key = ?", (ekey,)).fetchone()
        finally:
            conn.close()
    stored = row[1] if row and row[1] else None
    ok = _check_password(password or "", stored or _dummy_hash())
    if not (row and stored and ok):
        return None
    with _lock:
        conn = _connect()
        try:
            conn.execute("UPDATE accounts SET last_login = ? WHERE id = ?", (_now().isoformat(), row[0]))
            conn.commit()
        finally:
            conn.close()
    return get_account(row[0])


def email_uses_google_only(email: str) -> bool:
    with _lock:
        conn = _connect()
        try:
            row = conn.execute("SELECT google_sub, password_hash FROM accounts WHERE email_key = ?", (norm_email(email),)).fetchone()
            return bool(row and row[0] and not row[1])
        finally:
            conn.close()


def upsert_google_account(sub: str, email: str, name: str, picture: str) -> Tuple[dict, bool]:
    """Find the account for this Google user, or link/create it.
    Returns (account, is_new).

    Linking: if a password account already uses this (Google-verified)
    email, Google is added to that account. If that account never proved
    it owned the email, its password is cleared, because Google just proved
    someone else might have typed this email in first."""
    now = _now().isoformat()
    ekey = norm_email(email)
    with _lock:
        conn = _connect()
        try:
            row = conn.execute("SELECT id FROM accounts WHERE google_sub = ?", (sub,)).fetchone()
            if row:
                conn.execute("UPDATE accounts SET name = ?, picture = ?, last_login = ? WHERE id = ?",
                             (name, picture, now, row[0]))
                conn.commit()
                acct_id, is_new = row[0], False
            else:
                match = conn.execute("SELECT id, email_verified FROM accounts WHERE email_key = ? AND google_sub IS NULL",
                                     (ekey,)).fetchone() if ekey else None
                if match:
                    acct_id, verified = match
                    conn.execute(
                        "UPDATE accounts SET google_sub = ?, name = ?, picture = ?, email_verified = 1, last_login = ?"
                        + ("" if verified else ", password_hash = NULL") + " WHERE id = ?",
                        (sub, name, picture, now, acct_id),
                    )
                    is_new = False
                else:
                    acct_id = "acct_" + secrets.token_hex(12)
                    tname, tkey = _free_name(conn, default_trainer_name(name, email))
                    conn.execute(
                        """
                        INSERT INTO accounts (id, google_sub, email, email_key, email_verified, name, picture,
                                              trainer_name, trainer_key, created_at, last_login)
                        VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)
                        """,
                        (acct_id, sub, email, ekey or None, name, picture, tname, tkey, now, now),
                    )
                    is_new = True
                conn.commit()
            return _row(conn.execute(f"SELECT {_COLS} FROM accounts WHERE id = ?", (acct_id,)).fetchone()), is_new
        finally:
            conn.close()


# ── Profile ───────────────────────────────────────────────────────────────────

def update_profile(account_id: str, trainer_name: Optional[str] = None,
                   partner: Optional[Tuple[str, str, Optional[int]]] = None) -> dict:
    """partner: (key, display_name, pokemon_id), or ("", "", None) to clear."""
    with _lock:
        conn = _connect()
        try:
            if trainer_name is not None:
                name = check_trainer_name(trainer_name)
                row = conn.execute("SELECT id FROM accounts WHERE trainer_key = ?", (name_key(name),)).fetchone()
                if row and row[0] != account_id:
                    raise AccountError("That trainer name is taken.", "trainer_name")
                conn.execute("UPDATE accounts SET trainer_name = ?, trainer_key = ? WHERE id = ?",
                             (name, name_key(name), account_id))
            if partner is not None:
                conn.execute("UPDATE accounts SET partner_key = ?, partner_name = ?, partner_id = ? WHERE id = ?",
                             (partner[0], partner[1], partner[2], account_id))
            conn.commit()
        finally:
            conn.close()
    _session_cache.clear()   # cached copies carry the old profile
    return get_account(account_id)


# ── Password reset ────────────────────────────────────────────────────────────

def start_reset(email: str) -> Optional[str]:
    """A fresh 6-digit code for this email, or None if there's no account.
    (The caller says the same thing either way.)"""
    ekey = norm_email(email)
    code = f"{secrets.randbelow(1_000_000):06d}"
    with _lock:
        conn = _connect()
        try:
            if not conn.execute("SELECT 1 FROM accounts WHERE email_key = ?", (ekey,)).fetchone():
                return None
            conn.execute(
                "INSERT OR REPLACE INTO password_resets (email_key, code_hash, expires_at, tries) VALUES (?, ?, ?, 0)",
                (ekey, _hash(ekey + ":" + code), (_now() + timedelta(minutes=RESET_MINUTES)).isoformat()),
            )
            conn.commit()
            return code
        finally:
            conn.close()


def finish_reset(email: str, code: str, new_password: str) -> dict:
    """Check the code, set the new password, and mark the email as proven."""
    check_password_rules(new_password)
    ekey = norm_email(email)
    bad = AccountError("That code isn't right, or it expired. Ask for a new one.", "code")
    with _lock:
        conn = _connect()
        try:
            row = conn.execute("SELECT code_hash, expires_at, tries FROM password_resets WHERE email_key = ?", (ekey,)).fetchone()
            if not row or row[1] < _now().isoformat() or row[2] >= RESET_TRIES:
                raise bad
            if not hmac.compare_digest(row[0], _hash(ekey + ":" + (code or "").strip())):
                conn.execute("UPDATE password_resets SET tries = tries + 1 WHERE email_key = ?", (ekey,))
                conn.commit()
                raise bad
            conn.execute("DELETE FROM password_resets WHERE email_key = ?", (ekey,))
            conn.execute("UPDATE accounts SET password_hash = ?, email_verified = 1 WHERE email_key = ?",
                         (_hash_password(new_password), ekey))
            acct_id = conn.execute("SELECT id FROM accounts WHERE email_key = ?", (ekey,)).fetchone()[0]
            # A reset signs out every other device.
            conn.execute("DELETE FROM sessions WHERE account_id = ?", (acct_id,))
            conn.commit()
        finally:
            conn.close()
    _session_cache.clear()
    return get_account(acct_id)


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
                "SELECT s.account_id, s.expires_at FROM sessions s WHERE s.token_hash = ?", (h,)).fetchone()
            acct = _row(conn.execute(f"SELECT {_COLS} FROM accounts WHERE id = ?", (row[0],)).fetchone()) if row else None
        finally:
            conn.close()
    if not row or not acct or row[1] < _now().isoformat():
        _session_cache.pop(h, None)
        return None
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
