"""
ads_api.py — ad settings, ads.txt, and whether revives are on.

Everything is switched by environment variables, so the site runs with no
ads until you add them in Railway:

  ADSENSE_CLIENT       your publisher id, "ca-pub-1234567890123456".
                       Turns on display ads and serves /ads.txt.
  ADSENSE_SLOT_RAIL    ad unit id for the tall ads beside the board (desktop).
  ADSENSE_SLOT_INLINE  ad unit id for the ad under the board (phones, and
                       under the board on narrow laptops).
  ADSENSE_SLOT_RESULTS ad unit id for the ad shown with the end-of-board
                       banner (won, out of lives, Reverse solved).
  ADSENSE_SLOT_PAGE    ad unit id for the Pokedex and Category Index panels.
  ADS_REWARDED=1       turn on "watch an ad to revive" (needs Google's
                       H5 Games Ads approval on the same publisher id).
  ADS_INTERSTITIAL=1   a full-screen ad between boards: only after a board
                       has been finished, at most once every few minutes.
                       Same H5 Games Ads approval as the revive.
  ADS_TEST=1           ask Google for test ads only (data-adtest="on").
                       Use while checking the layout; turn off when live.
  GA_MEASUREMENT_ID    optional Google Analytics 4 id, "G-XXXXXXX". Counts
                       visits so you can see traffic next to ad earnings.
  ADS_DEMO=1           no Google at all: grey placeholder boxes and a fake
                       5-second "ad" for the revive, so you can see the whole
                       flow before AdSense approves the site.

The front end reads GET /ads/config and does the rest (ads.js).

Wire-up in api_main.py:
    import ads_api
    ads_api.attach(app)
"""
from __future__ import annotations

import os
import re

from fastapi import HTTPException
from fastapi.responses import PlainTextResponse

# How many revives one player gets per daily board (Daily and Reverse each).
REVIVES_PER_BOARD = 1
# Lives a revive gives back.
REVIVE_LIVES = 1

_PUB_RE = re.compile(r"^ca-pub-\d{10,20}$")
_GA_RE = re.compile(r"^G-[A-Z0-9]{4,20}$")


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def adsense_client() -> str:
    c = os.environ.get("ADSENSE_CLIENT", "").strip()
    return c if _PUB_RE.match(c) else ""


def demo() -> bool:
    return _flag("ADS_DEMO")


def revive_enabled() -> bool:
    """Revives are offered only when a rewarded ad can actually be shown."""
    return demo() or (bool(adsense_client()) and _flag("ADS_REWARDED"))


def interstitial_enabled() -> bool:
    return demo() or (bool(adsense_client()) and _flag("ADS_INTERSTITIAL"))


def config() -> dict:
    client = adsense_client()
    on = bool(client) or demo()
    ga = os.environ.get("GA_MEASUREMENT_ID", "").strip()
    return {
        "ga": ga if _GA_RE.match(ga) else None,
        "enabled": on,
        "demo": demo(),
        "client": client or None,
        "test": _flag("ADS_TEST"),
        "slots": {
            "rail": os.environ.get("ADSENSE_SLOT_RAIL", "").strip() or None,
            "inline": os.environ.get("ADSENSE_SLOT_INLINE", "").strip() or None,
            "results": os.environ.get("ADSENSE_SLOT_RESULTS", "").strip() or None,
            "page": os.environ.get("ADSENSE_SLOT_PAGE", "").strip() or None,
        },
        "rewarded": revive_enabled(),
        "interstitial": interstitial_enabled(),
        "revives": REVIVES_PER_BOARD,
        "revive_lives": REVIVE_LIVES,
    }


def attach(app) -> None:
    @app.get("/ads/config")
    def ads_config():
        return config()

    # Google checks this file before it pays out. It must sit at the root of
    # the domain: https://pokedokudoku.com/ads.txt
    @app.get("/ads.txt", response_class=PlainTextResponse, include_in_schema=False)
    def ads_txt():
        client = adsense_client()
        if not client:
            raise HTTPException(status_code=404)
        pub = client.replace("ca-", "", 1)
        return f"google.com, {pub}, DIRECT, f08c47fec0942fa0\n"
