"""
auth_api.py — accounts: Google or email sign-in, sessions, the trainer profile.

Wired into api_main.py with:

    import auth_api
    auth_api.attach(app, find_pokemon=...)

How it fits the rest of the app
-------------------------------
Every game endpoint identifies the player by the X-Client-Id header. Before
accounts that was a random id kept in the browser. This module adds an ASGI
middleware that runs before every request:

  • signed in (valid session cookie)  -> X-Client-Id is replaced with the
                                         account id ("acct_…")
  • not signed in                     -> the browser's own id is used, as
                                         before, but an "acct_…" id sent by
                                         the browser is dropped, so nobody
                                         can act as someone else's account

So the Pokédex, Daily, Reverse and stats code didn't change at all.

Whichever way someone signs in, everything the current device had as a
guest is merged into the account (the account's own data wins on a clash).

Endpoints
  GET  /auth/config            which sign-in methods are on
  GET  /auth/me                the signed-in account (or null) + progress
  GET  /auth/name-available    is a trainer name free?
  POST /auth/google            sign in with a Google ID token
  POST /auth/register          create an account with email + password
  POST /auth/login             sign in with email + password
  POST /auth/password/forgot   email a 6-digit reset code
  POST /auth/password/reset    set a new password with that code
  POST /auth/logout            sign out (this device)
  POST /auth/profile           change trainer name and/or partner Pokémon

Settings (environment variables)
  GOOGLE_CLIENT_ID   OAuth "Web application" client id (not a secret).
                     Without it the Google button is hidden.
  RESEND_API_KEY     Resend.com API key, for password-reset emails.
  MAIL_FROM          Sender, e.g. "PokeDokuDoku <hello@pokedokudoku.com>"
                     (the domain must be verified in Resend).
                     Without these two, "Forgot password?" is hidden.
"""

from __future__ import annotations

import os
import time
from collections import defaultdict, deque
from http.cookies import SimpleCookie
from typing import Callable, Optional

from fastapi import HTTPException, Request, Response
from pydantic import BaseModel

import accounts_store
from accounts_store import AccountError
import daily_store
import pokedex_store
import reverse_store

COOKIE = "pdd_session"
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "").strip()
MAIL_FROM = os.environ.get("MAIL_FROM", "").strip()
RESET_ENABLED = bool(RESEND_API_KEY and MAIL_FROM)
GOOGLE_ISSUERS = ("accounts.google.com", "https://accounts.google.com")


# ── Middleware: who is this request for? ──────────────────────────────────────

class SessionMiddleware:
    """Pure ASGI (not BaseHTTPMiddleware) so it adds next to nothing to each
    request and works with streaming responses and static files."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        headers = scope.get("headers") or []
        token = None
        client_id = None
        for k, v in headers:
            if k == b"cookie":
                try:
                    c = SimpleCookie()
                    c.load(v.decode("latin-1"))
                    if COOKIE in c:
                        token = c[COOKIE].value
                except Exception:
                    pass
            elif k == b"x-client-id":
                client_id = v.decode("latin-1")

        acct = accounts_store.account_for_session(token) if token else None
        if acct:
            effective = acct["id"]
        elif client_id and not client_id.startswith("acct_"):
            effective = client_id
        else:
            effective = None

        new_headers = [(k, v) for k, v in headers if k != b"x-client-id"]
        if effective:
            new_headers.append((b"x-client-id", effective.encode("latin-1")))
        scope = dict(scope)
        scope["headers"] = new_headers
        scope.setdefault("state", {})
        # The browser's own (guest) id, kept for the sign-in merge.
        scope["state"]["guest_id"] = client_id if client_id and not client_id.startswith("acct_") else None
        scope["state"]["account"] = acct
        await self.app(scope, receive, send)


# ── Rate limits (per server process; enough for one Railway instance) ────────

class _Limiter:
    """At most `limit` events per `window` seconds for each key."""

    def __init__(self, limit: int, window: float):
        self.limit, self.window = limit, window
        self.hits: dict = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        q = self.hits[key]
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        if len(self.hits) > 20000:
            self.hits.clear()
        return True


_login_by_email = _Limiter(8, 15 * 60)       # wrong passwords for one email
_login_by_ip = _Limiter(30, 15 * 60)         # sign-in attempts from one address
_signup_by_ip = _Limiter(8, 60 * 60)         # new accounts from one address
_reset_by_email = _Limiter(3, 60 * 60)       # reset emails for one address
_reset_by_ip = _Limiter(10, 60 * 60)


def _ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "?")


def _too_many():
    raise HTTPException(status_code=429, detail="Too many tries. Wait a few minutes and try again.")


# ── Models ────────────────────────────────────────────────────────────────────

class Progress(BaseModel):
    caught: int = 0
    shinies: int = 0
    streak: int = 0
    charms: int = 0
    categories: int = 0


class AccountOut(BaseModel):
    id: str
    trainer_name: str
    name: str = ""
    email: str = ""
    picture: str = ""
    created_at: str = ""
    partner_key: str = ""
    partner_name: str = ""
    partner_id: Optional[int] = None
    has_password: bool = False
    has_google: bool = False


class MeResponse(BaseModel):
    enabled: bool
    account: Optional[AccountOut] = None
    progress: Progress


class GoogleSignIn(BaseModel):
    credential: str


class RegisterIn(BaseModel):
    email: str
    password: str
    trainer_name: str


class LoginIn(BaseModel):
    email: str
    password: str


class ForgotIn(BaseModel):
    email: str


class ResetIn(BaseModel):
    email: str
    code: str
    password: str


class ProfileIn(BaseModel):
    trainer_name: Optional[str] = None
    partner_key: Optional[str] = None     # "" clears the partner


class SignInResponse(BaseModel):
    account: AccountOut
    is_new: bool
    merged: int                 # Pokémon moved over from this device
    progress: Progress


# ── Helpers ───────────────────────────────────────────────────────────────────

def _progress(client_id: Optional[str]) -> Progress:
    if not client_id:
        return Progress()
    try:
        return Progress(
            caught=len(pokedex_store.get_caught_keys(client_id)),
            shinies=len(pokedex_store.get_shiny_map(client_id)),
            streak=pokedex_store.get_streak(client_id),
            charms=len(pokedex_store.get_prestige(client_id)),
            categories=len(reverse_store.get_identified_ids(client_id)),
        )
    except Exception:
        return Progress()


def _out(acct: dict) -> AccountOut:
    data = {k: acct.get(k) for k in AccountOut.model_fields}
    for k in ("name", "email", "picture", "created_at", "partner_key", "partner_name"):
        data[k] = data.get(k) or ""
    data["has_password"] = bool(data.get("has_password"))
    data["has_google"] = bool(data.get("has_google"))
    return AccountOut(**data)


def _secure(request: Request) -> bool:
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    return proto.split(",")[0].strip() == "https"


def _fail(err: AccountError, status: int = 400):
    raise HTTPException(status_code=status, detail={"message": str(err), "field": err.field})


def _sign_in(acct: dict, is_new: bool, request: Request, response: Response) -> SignInResponse:
    """Shared by every sign-in method: merge this device, start a session."""
    guest = request.scope.get("state", {}).get("guest_id")
    merged = 0
    if guest and guest != acct["id"]:
        merged = pokedex_store.merge_client(guest, acct["id"])
        reverse_store.merge_client(guest, acct["id"])
        daily_store.merge_client(guest, acct["id"])
    old = request.cookies.get(COOKIE)
    if old:
        accounts_store.delete_session(old)
    token = accounts_store.create_session(acct["id"])
    response.set_cookie(COOKIE, token, max_age=accounts_store.SESSION_DAYS * 86400,
                        httponly=True, samesite="lax", secure=_secure(request), path="/")
    return SignInResponse(account=_out(acct), is_new=is_new, merged=merged, progress=_progress(acct["id"]))


def _verify_google(credential: str) -> dict:
    """Check the ID token's signature, audience, issuer and expiry with
    Google's published keys. Raises 401 on anything wrong."""
    try:
        from google.oauth2 import id_token
        from google.auth.transport import requests as g_requests
    except ImportError:
        raise HTTPException(status_code=500, detail="google-auth isn't installed on the server.")
    try:
        info = id_token.verify_oauth2_token(credential, g_requests.Request(), GOOGLE_CLIENT_ID)
    except Exception:
        raise HTTPException(status_code=401, detail="Google sign-in couldn't be verified. Try again.")
    if info.get("iss") not in GOOGLE_ISSUERS or not info.get("sub"):
        raise HTTPException(status_code=401, detail="Google sign-in couldn't be verified. Try again.")
    if info.get("email") and info.get("email_verified") is False:
        raise HTTPException(status_code=401, detail="That Google account's email isn't verified.")
    return info


def _send_reset_email(to: str, code: str, trainer: str) -> bool:
    """Send the code through Resend. Returns False if it didn't go out."""
    try:
        import requests
        text = (f"Hi {trainer},\n\nYour PokeDokuDoku reset code is {code}\n\n"
                f"It works for {accounts_store.RESET_MINUTES} minutes. If you didn't ask for this, ignore this email; "
                "your password hasn't changed.\n")
        html = (f"<p>Hi {trainer},</p><p>Your PokeDokuDoku reset code is</p>"
                f"<p style=\"font:700 28px/1.2 monospace;letter-spacing:4px\">{code}</p>"
                f"<p>It works for {accounts_store.RESET_MINUTES} minutes. If you didn't ask for this, ignore this email; "
                "your password hasn't changed.</p>")
        r = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
            json={"from": MAIL_FROM, "to": [to], "subject": f"Your reset code: {code}", "text": text, "html": html},
            timeout=10,
        )
        return r.status_code < 300
    except Exception:
        return False


# ── Attach ────────────────────────────────────────────────────────────────────

def attach(app, find_pokemon: Optional[Callable[[str], Optional[dict]]] = None) -> None:
    """find_pokemon(key) -> {"key", "display_name", "pokemon_id"} or None;
    used to check a chosen partner Pokémon exists."""
    accounts_store.init_db()
    app.add_middleware(SessionMiddleware)

    @app.get("/auth/config")
    def auth_config():
        return {
            "enabled": True,                         # email sign-in is always available
            "google_client_id": GOOGLE_CLIENT_ID or None,
            "email": True,
            "reset": RESET_ENABLED,
            "name_min": accounts_store.TRAINER_NAME_MIN,
            "name_max": accounts_store.TRAINER_NAME_MAX,
            "password_min": accounts_store.PASSWORD_MIN,
        }

    @app.get("/auth/me", response_model=MeResponse)
    def auth_me(request: Request):
        acct = request.scope.get("state", {}).get("account")
        who = acct["id"] if acct else request.scope.get("state", {}).get("guest_id")
        return MeResponse(enabled=True, account=_out(acct) if acct else None, progress=_progress(who))

    @app.get("/auth/name-available")
    def auth_name_available(name: str, request: Request):
        acct = request.scope.get("state", {}).get("account")
        try:
            clean = accounts_store.check_trainer_name(name)
        except AccountError as e:
            return {"available": False, "reason": str(e)}
        free = accounts_store.name_available(clean, except_account=acct["id"] if acct else "")
        return {"available": free, "reason": "" if free else "That trainer name is taken."}

    @app.post("/auth/google", response_model=SignInResponse)
    def auth_google(payload: GoogleSignIn, request: Request, response: Response):
        if not GOOGLE_CLIENT_ID:
            raise HTTPException(status_code=503, detail="Google sign-in isn't set up on this server yet.")
        info = _verify_google(payload.credential)
        acct, is_new = accounts_store.upsert_google_account(
            sub=str(info["sub"]), email=info.get("email", ""),
            name=info.get("name", ""), picture=info.get("picture", ""),
        )
        return _sign_in(acct, is_new, request, response)

    @app.post("/auth/register", response_model=SignInResponse)
    def auth_register(payload: RegisterIn, request: Request, response: Response):
        if not _signup_by_ip.allow(_ip(request)):
            _too_many()
        try:
            acct = accounts_store.create_email_account(payload.email, payload.password, payload.trainer_name)
        except AccountError as e:
            _fail(e)
        return _sign_in(acct, True, request, response)

    @app.post("/auth/login", response_model=SignInResponse)
    def auth_login(payload: LoginIn, request: Request, response: Response):
        ekey = accounts_store.norm_email(payload.email)
        if not _login_by_ip.allow(_ip(request)):
            _too_many()
        acct = accounts_store.email_sign_in(payload.email, payload.password)
        if not acct:
            if not _login_by_email.allow(ekey):
                _too_many()
            if accounts_store.email_uses_google_only(payload.email):
                _fail(AccountError("That email signs in with Google. Use the Google button.", "email"), 401)
            _fail(AccountError("Email or password is wrong.", "password"), 401)
        return _sign_in(acct, False, request, response)

    @app.post("/auth/password/forgot")
    def auth_forgot(payload: ForgotIn, request: Request):
        if not RESET_ENABLED:
            raise HTTPException(status_code=503, detail="Password reset isn't set up yet.")
        ekey = accounts_store.norm_email(payload.email)
        if not _reset_by_ip.allow(_ip(request)) or not _reset_by_email.allow(ekey):
            _too_many()
        code = accounts_store.start_reset(payload.email)
        if code:
            acct_name = ""
            try:
                with accounts_store._lock:
                    conn = accounts_store._connect()
                    row = conn.execute("SELECT trainer_name, email FROM accounts WHERE email_key = ?", (ekey,)).fetchone()
                    conn.close()
                acct_name, to = (row[0], row[1]) if row else ("Trainer", payload.email)
            except Exception:
                acct_name, to = "Trainer", payload.email
            if not _send_reset_email(to, code, acct_name or "Trainer"):
                raise HTTPException(status_code=502, detail="The email didn't send. Try again in a minute.")
        # Same answer whether or not the email has an account.
        return {"ok": True, "message": "If that email has an account, a code is on its way."}

    @app.post("/auth/password/reset", response_model=SignInResponse)
    def auth_reset(payload: ResetIn, request: Request, response: Response):
        if not RESET_ENABLED:
            raise HTTPException(status_code=503, detail="Password reset isn't set up yet.")
        try:
            acct = accounts_store.finish_reset(payload.email, payload.code, payload.password)
        except AccountError as e:
            _fail(e)
        return _sign_in(acct, False, request, response)

    @app.post("/auth/logout")
    def auth_logout(request: Request, response: Response):
        accounts_store.delete_session(request.cookies.get(COOKIE))
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @app.post("/auth/profile", response_model=AccountOut)
    def auth_profile(payload: ProfileIn, request: Request):
        acct = request.scope.get("state", {}).get("account")
        if not acct:
            raise HTTPException(status_code=401, detail="Sign in first.")
        partner = None
        if payload.partner_key is not None:
            if payload.partner_key == "":
                partner = ("", "", None)
            else:
                mon = find_pokemon(payload.partner_key) if find_pokemon else None
                if not mon:
                    raise HTTPException(status_code=400, detail={"message": "Pick a Pokémon from the list.", "field": "partner"})
                partner = (mon["key"], mon["display_name"], mon.get("pokemon_id"))
        try:
            updated = accounts_store.update_profile(acct["id"], trainer_name=payload.trainer_name, partner=partner)
        except AccountError as e:
            _fail(e)
        return _out(updated)
