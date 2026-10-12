"""
auth_api.py — Sign in with Google, sessions, and the player's account.

Wired into api_main.py with:

    import auth_api
    auth_api.attach(app)

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

On sign-in, everything the current device had as a guest is merged into the
account (the account's own data wins on any clash). Signing in on a second
device merges that device's guest progress too.

Endpoints
  GET  /auth/config    is Google sign-in set up? (+ the public client id)
  GET  /auth/me        the signed-in account, or null; plus a progress summary
  POST /auth/google    sign in with a Google ID token
  POST /auth/logout    sign out (this device)
  POST /auth/profile   change the trainer name

Settings (environment variables)
  GOOGLE_CLIENT_ID   the OAuth "Web application" client id from Google Cloud.
                     Not a secret. Without it, sign-in is simply hidden.
"""

from __future__ import annotations

import os
from http.cookies import SimpleCookie
from typing import Optional

from fastapi import HTTPException, Request, Response
from pydantic import BaseModel

import accounts_store
import daily_store
import pokedex_store
import reverse_store

COOKIE = "pdd_session"
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
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
        # The browser's own (guest) id, kept for the sign-in merge.
        scope.setdefault("state", {})
        scope["state"]["guest_id"] = client_id if client_id and not client_id.startswith("acct_") else None
        scope["state"]["account"] = acct
        await self.app(scope, receive, send)


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


class MeResponse(BaseModel):
    enabled: bool
    account: Optional[AccountOut] = None
    progress: Progress          # this player's progress (account, or this device as a guest)


class GoogleSignIn(BaseModel):
    credential: str             # the ID token from Google Identity Services


class SignInResponse(BaseModel):
    account: AccountOut
    is_new: bool
    merged: int                 # Pokémon moved over from this device
    progress: Progress


class ProfileIn(BaseModel):
    trainer_name: str


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
    return AccountOut(**{k: acct.get(k) or "" for k in AccountOut.model_fields})


def _secure(request: Request) -> bool:
    # Railway (and most hosts) terminate HTTPS at a proxy and forward plain HTTP.
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    return proto.split(",")[0].strip() == "https"


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


# ── Attach ────────────────────────────────────────────────────────────────────

def attach(app) -> None:
    accounts_store.init_db()
    app.add_middleware(SessionMiddleware)

    @app.get("/auth/config")
    def auth_config():
        return {"enabled": bool(GOOGLE_CLIENT_ID), "google_client_id": GOOGLE_CLIENT_ID or None}

    @app.get("/auth/me", response_model=MeResponse)
    def auth_me(request: Request):
        acct = request.scope.get("state", {}).get("account")
        who = acct["id"] if acct else request.scope.get("state", {}).get("guest_id")
        return MeResponse(enabled=bool(GOOGLE_CLIENT_ID),
                          account=_out(acct) if acct else None,
                          progress=_progress(who))

    @app.post("/auth/google", response_model=SignInResponse)
    def auth_google(payload: GoogleSignIn, request: Request, response: Response):
        if not GOOGLE_CLIENT_ID:
            raise HTTPException(status_code=503, detail="Sign-in isn't set up on this server yet.")
        info = _verify_google(payload.credential)
        acct, is_new = accounts_store.upsert_google_account(
            sub=str(info["sub"]), email=info.get("email", ""),
            name=info.get("name", ""), picture=info.get("picture", ""),
        )

        # Fold this device's guest progress into the account.
        guest = request.scope.get("state", {}).get("guest_id")
        merged = 0
        if guest and guest != acct["id"]:
            merged = pokedex_store.merge_client(guest, acct["id"])
            reverse_store.merge_client(guest, acct["id"])
            daily_store.merge_client(guest, acct["id"])

        # Replace any session this browser already had.
        old = request.cookies.get(COOKIE)
        if old:
            accounts_store.delete_session(old)
        token = accounts_store.create_session(acct["id"])
        response.set_cookie(
            COOKIE, token,
            max_age=accounts_store.SESSION_DAYS * 86400,
            httponly=True, samesite="lax", secure=_secure(request), path="/",
        )
        return SignInResponse(account=_out(acct), is_new=is_new, merged=merged,
                              progress=_progress(acct["id"]))

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
        ok, msg = accounts_store.set_trainer_name(acct["id"], payload.trainer_name)
        if not ok:
            raise HTTPException(status_code=400, detail=msg)
        return _out(accounts_store.get_account(acct["id"]))
