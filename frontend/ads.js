/* ============================================================================
   ads.js — display ads and the "watch an ad to revive" offer.
   ----------------------------------------------------------------------------
   Reads GET /ads/config (ads_api.py). With nothing configured it does
   nothing at all: no script is loaded and no space is reserved.

   Display ads
     • Wide screens (1600px+): one tall ad on each side of the page. The page
       narrows a little to make room; the board never sits under an ad.
     • Everything else (laptops, tablets, phones): one ad below the board,
       in the normal flow of the page. Nothing sticky, nothing over the game.
     • Results: when a board ends (won, out of lives, Reverse solved), an ad
       appears under the end banner, while the player is looking at the
       result. It goes away when the next board starts.
     • Panels: one ad near the top of the Pokédex and the Category Index,
       filled the first time each is opened (a hidden ad can't be sized).

   Between boards (ADS_INTERSTITIAL)
     boardBreak() is called when the player starts a new Shuffle, Draft or
     Reverse board. It shows a full-screen ad only if they finished a board
     since the last one and at least BREAK_GAP_MS has passed. Starting a
     board never waits on it.

   Revive (Daily and the Daily Reverse Board)
     When a player runs out of lives, app.js / Reverse.js call
     PDD_ADS.offerRevive({ onRevive, onEnd }). If Google has a rewarded ad
     ready, the player sees "Watch an ad for 1 more life" or "End board".
     The life is granted only after the ad is watched to the end
     (adViewed), and the server allows one revive per board per day.
     No ad available (or an ad blocker) means the board simply ends.

   Google Analytics loads too when GA_MEASUREMENT_ID is set. Consent Mode
   starts "denied" for visitors in the EEA, UK and Switzerland, so nothing
   is stored until they accept in Google's consent message (the AdSense
   Privacy & messaging CMP updates it). Everyone else is counted normally.
   Microsoft Clarity loads when CLARITY_PROJECT_ID is set; it is told
   about consent from Google's consent message (TCF), and records without
   cookies until then for visitors who need to consent.
   SUPPORT_URL adds a "Support Route 81" link (footer + win banner).
   Game events: support_click, board_won, board_lost, reverse_solved, reverse_lost,
   new_board, revive_offered, revive_watched, revive_declined.

   Demo mode (ADS_DEMO=1): grey "Ad" boxes and a 5-second stand-in ad, so the
   layout and the revive flow can be checked before AdSense approval.
   ============================================================================ */
(function () {
  "use strict";

  const ADS_SRC = "https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js";
  const RAIL_MIN = 1600;            // viewport width that fits two 160px rails
  const BREAK_GAP_MS = 3 * 60 * 1000; // at most one between-boards ad per 3 minutes
  const END_BANNERS = ["gameOverBanner", "winBanner", "revBanner"];
  const PANELS = [
    // [modal id, element the ad goes after]
    ["pokedexModal", ".dx-stats"],
    ["revIndexModal", ".rev-index-progress"],
  ];

  const S = { cfg: null, cfgP: null, loaded: null, ready: false };

  function apiBase() {
    return (typeof state !== "undefined" && state.apiBase) || "";
  }

  // The config is fetched once; offerRevive waits for it too, so a board
  // restored "out of lives" on page load still gets its offer.
  function getConfig() {
    if (!S.cfgP) {
      S.cfgP = fetch(`${apiBase()}/ads/config`)
        .then((r) => (r.ok ? r.json() : null))
        .catch(() => null)
        .then((c) => { S.cfg = c; return c; });
    }
    return S.cfgP;
  }

  async function init() {
    await getConfig();
    if (S.cfg && S.cfg.ga) { loadAnalytics(S.cfg.ga); watchOutcomes(); }
    if (S.cfg && S.cfg.clarity) loadClarity(S.cfg.clarity);
    if (S.cfg && S.cfg.support) addSupport(S.cfg.support);
    if (!S.cfg || !S.cfg.enabled) return;
    if (!S.cfg.demo) loadScript();
    placeInline();
    placeRails();
    window.addEventListener("resize", debounce(placeRails, 200));
    watchEndBanners();
    watchPanels();
  }

  // ── Google's script ────────────────────────────────────────────────────────
  // One script serves both the display units and the rewarded ad (the H5
  // Games "Ad Placement API": adBreak / adConfig).
  function loadScript() {
    if (S.loaded) return S.loaded;
    window.adsbygoogle = window.adsbygoogle || [];
    window.adBreak = window.adConfig = function (o) { window.adsbygoogle.push(o); };
    S.loaded = new Promise((resolve) => {
      const sc = document.createElement("script");
      sc.async = true;
      sc.crossOrigin = "anonymous";
      sc.src = `${ADS_SRC}?client=${encodeURIComponent(S.cfg.client)}`;
      sc.setAttribute("data-ad-client", S.cfg.client);
      if (S.cfg.test) sc.setAttribute("data-adbreak-test", "on");
      sc.onload = () => { S.ready = true; resolve(true); };
      sc.onerror = () => resolve(false);           // blocked or offline
      document.head.appendChild(sc);
    });
    // Preload rewarded ads so one is ready the moment a player needs it.
    window.adConfig({ preloadAdBreaks: "on", sound: "on" });
    return S.loaded;
  }

  // Optional Google Analytics 4 (GA_MEASUREMENT_ID).
  const CONSENT_REGIONS = [
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU",
    "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES",
    "SE", "IS", "LI", "NO", "GB", "CH",
  ];
  function loadAnalytics(id) {
    window.dataLayer = window.dataLayer || [];
    window.gtag = function () { window.dataLayer.push(arguments); };
    const denied = { ad_storage: "denied", ad_user_data: "denied", ad_personalization: "denied", analytics_storage: "denied" };
    window.gtag("consent", "default", { ...denied, region: CONSENT_REGIONS, wait_for_update: 500 });
    window.gtag("consent", "default", {
      ad_storage: "granted", ad_user_data: "granted", ad_personalization: "granted", analytics_storage: "granted",
    });
    window.gtag("js", new Date());
    window.gtag("config", id);
    const sc = document.createElement("script");
    sc.async = true;
    sc.src = `https://www.googletagmanager.com/gtag/js?id=${encodeURIComponent(id)}`;
    document.head.appendChild(sc);
  }

  // Microsoft Clarity. Consent comes from Google's CMP through the IAB TCF
  // API: no GDPR → granted; GDPR → granted only with purpose 1 (storage).
  function loadClarity(id) {
    (function (c, l, a, r, i, t, y) {
      c[a] = c[a] || function () { (c[a].q = c[a].q || []).push(arguments); };
      t = l.createElement(r); t.async = 1; t.src = "https://www.clarity.ms/tag/" + i;
      y = l.getElementsByTagName(r)[0]; y.parentNode.insertBefore(t, y);
    })(window, document, "clarity", "script", id);
    const send = (ok) => window.clarity("consentv2", {
      analytics_Storage: ok ? "granted" : "denied",
      ad_Storage: "denied",
    });
    let tries = 0;
    const hook = () => {
      if (typeof window.__tcfapi !== "function") {
        if (++tries < 40) setTimeout(hook, 250);      // the CMP loads with Google's ad script
        return;
      }
      window.__tcfapi("addEventListener", 2, (d, ok) => {
        if (!ok || !d) return;
        if (d.gdprApplies === false) { send(true); return; }
        if (d.eventStatus === "useractioncomplete" || d.eventStatus === "tcloaded") {
          send(!!(d.purpose && d.purpose.consents && d.purpose.consents[1]));
        }
      });
    };
    hook();
  }

  // "Support Route 81": footer link, plus a small line on the win banner.
  function addSupport(url) {
    const link = (text, cls) => {
      const a = document.createElement("a");
      a.href = url;
      a.target = "_blank";
      a.rel = "noopener";
      a.className = cls;
      a.textContent = text;
      a.addEventListener("click", () => track("support_click", { where: cls }));
      return a;
    };
    const links = document.querySelector(".site-foot span");
    if (links && !links.querySelector(".support-foot")) {
      links.append(" · ", link("Support Route 81", "support-foot"));
    }
    const win = document.querySelector("#winBanner .hud-alert-text");
    if (win && !win.querySelector(".support-win")) {
      const line = document.createElement("span");
      line.className = "support-line";
      line.append("Enjoying Route 81? ", link("Buy me a coffee", "support-win"));
      win.appendChild(line);
    }
  }

  function track(name, params) {
    if (typeof window.gtag === "function") window.gtag("event", name, params || {});
  }

  function boardKind() {
    const b = typeof state !== "undefined" && state.board;
    return b && b.is_daily ? "daily" : "shuffle";
  }

  // One event each time an end-of-board banner appears.
  function watchOutcomes() {
    const seen = {};
    const check = () => {
      const g = document.getElementById("gameOverBanner");
      const w = document.getElementById("winBanner");
      const r = document.getElementById("revBanner");
      const now = {
        board_lost: !!(g && !g.hidden && g.classList.contains("visible")),
        board_won: !!(w && !w.hidden && w.classList.contains("visible")),
        reverse_lost: !!(r && !r.hidden && r.classList.contains("hud-alert-dead")),
        reverse_solved: !!(r && !r.hidden && r.classList.contains("hud-alert-win")),
      };
      Object.keys(now).forEach((k) => {
        if (now[k] && !seen[k]) track(k, k.startsWith("board") ? { board: boardKind() } : {});
        seen[k] = now[k];
      });
    };
    const obs = new MutationObserver(check);
    ["gameOverBanner", "winBanner", "revBanner"].forEach((id) => {
      const b = document.getElementById(id);
      if (b) obs.observe(b, { attributes: true, attributeFilter: ["class", "hidden"] });
    });
    check();
  }

  // ── Display slots ──────────────────────────────────────────────────────────
  function unit(slot, kind) {
    const box = document.createElement("aside");
    box.className = `ad-slot ad-${kind}`;
    box.setAttribute("aria-label", "Advertisement");
    const label = document.createElement("span");
    label.className = "ad-label";
    label.textContent = "Ad";
    box.appendChild(label);
    if (S.cfg.demo || !slot) {
      const ph = document.createElement("div");
      ph.className = "ad-demo";
      ph.textContent = S.cfg.demo ? (kind === "rail" ? "160 × 600" : `Responsive ad (${kind})`) : "";
      box.appendChild(ph);
      if (!S.cfg.demo) return null;                // no slot id: show nothing
      return box;
    }
    const ins = document.createElement("ins");
    ins.className = "adsbygoogle";
    ins.setAttribute("data-ad-client", S.cfg.client);
    ins.setAttribute("data-ad-slot", slot);
    if (S.cfg.test) ins.setAttribute("data-adtest", "on");
    if (kind === "rail") {
      ins.style.cssText = "display:inline-block;width:160px;height:600px";
    } else {
      ins.style.display = "block";
      ins.setAttribute("data-ad-format", "auto");
      ins.setAttribute("data-full-width-responsive", "true");
    }
    box.appendChild(ins);
    return box;
  }

  function fill(box) {
    if (S.cfg.demo || !box) return;
    const ins = box.querySelector("ins.adsbygoogle");
    if (!ins || ins.dataset.pushed) return;
    ins.dataset.pushed = "1";
    try { (window.adsbygoogle = window.adsbygoogle || []).push({}); } catch (_) { /* blocked */ }
  }

  function placeInline() {
    const main = document.querySelector("main.app-shell");
    if (!main || document.querySelector(".ad-inline")) return;
    const box = unit(S.cfg.slots.inline, "inline");
    if (!box) return;
    main.appendChild(box);
    fill(box);
  }

  function placeRails() {
    const wide = window.innerWidth >= RAIL_MIN && !!(S.cfg.demo || S.cfg.slots.rail);
    document.body.classList.toggle("ads-rails", wide);
    if (!wide) return;                              // rails stay in the page, hidden by CSS
    ["left", "right"].forEach((side) => {
      if (document.querySelector(`.ad-rail.${side}`)) return;
      const box = unit(S.cfg.slots.rail, "rail");
      if (!box) return;
      box.classList.add(side);
      document.body.appendChild(box);
      fill(box);
    });
  }

  // ── Results ad: under the end-of-board banner ──────────────────────────────
  function bannerShown(b) {
    return !b.hidden && (b.id === "revBanner" || b.classList.contains("visible"));
  }

  function syncResults() {
    END_BANNERS.forEach((id) => {
      const b = document.getElementById(id);
      if (!b) return;
      const next = b.nextElementSibling;
      const has = next && next.classList.contains("ad-results");
      if (bannerShown(b)) {
        S.boardDone = true;                       // a board was finished: a break may follow
        if (has) return;
        const box = unit(S.cfg.slots.results, "results");
        if (!box) return;
        b.after(box);
        fill(box);
      } else if (has) {
        next.remove();                            // new board: the result ad goes
      }
    });
  }

  function watchEndBanners() {
    const obs = new MutationObserver(syncResults);
    END_BANNERS.forEach((id) => {
      const b = document.getElementById(id);
      if (b) obs.observe(b, { attributes: true, attributeFilter: ["class", "hidden"] });
    });
    syncResults();
  }

  // ── Panel ads: Pokédex and Category Index ──────────────────────────────────
  function watchPanels() {
    PANELS.forEach(([modalId, afterSel]) => {
      const modal = document.getElementById(modalId);
      if (!modal) return;
      const place = () => {
        if (!modal.classList.contains("open") || modal.querySelector(".ad-page")) return;
        const anchor = modal.querySelector(afterSel);
        if (!anchor) return;
        const box = unit(S.cfg.slots.page, "page");
        if (!box) return;
        anchor.after(box);
        // Wait a frame so the panel has its width before Google sizes the ad.
        requestAnimationFrame(() => fill(box));
      };
      new MutationObserver(place).observe(modal, { attributes: true, attributeFilter: ["class"] });
      place();
    });
  }

  // ── Between boards ─────────────────────────────────────────────────────────
  // Fire and forget: the new board loads underneath whether or not an ad shows.
  function boardBreak() {
    track("new_board");
    if (!S.cfg || !S.cfg.interstitial || !S.boardDone) return;
    if (Date.now() - (S.lastBreak || 0) < BREAK_GAP_MS) return;
    S.boardDone = false;
    S.lastBreak = Date.now();
    if (S.cfg.demo) { demoAd(() => {}, "Between-boards ad"); return; }
    loadScript().then((ok) => {
      if (!ok) return;
      window.adBreak({ type: "next", name: "new-board" });
    });
  }

  // ── Rewarded ad ────────────────────────────────────────────────────────────
  // Asks Google for a rewarded ad. Calls back once with
  //   ready(show)  an ad is loaded; call show() from a click to play it
  //   none()       no ad right now
  // and later done(viewed) after it plays.
  function requestReward({ ready, none, done }) {
    if (S.cfg.demo) { ready(() => demoAd(done)); return; }
    let settled = false;
    const noAd = () => { if (!settled) { settled = true; none(); } };
    const timer = setTimeout(noAd, 4000);
    let viewed = false;
    loadScript().then((ok) => {
      if (!ok) { clearTimeout(timer); noAd(); return; }
      window.adBreak({
        type: "reward",
        name: "revive",
        beforeAd: () => { /* the game is already paused */ },
        afterAd: () => {},
        beforeReward: (showAdFn) => {
          if (settled) return;
          settled = true;
          clearTimeout(timer);
          ready(() => showAdFn());
        },
        adViewed: () => { viewed = true; },
        adDismissed: () => { viewed = false; },
        adBreakDone: (info) => {
          clearTimeout(timer);
          if (!settled) { noAd(); return; }
          if (info && info.breakStatus === "viewed") viewed = true;
          done(viewed);
        },
      });
    });
  }

  // A stand-in for the real ad in demo mode: 5 seconds, can be skipped
  // (skipping means no revive, same as closing a real one early).
  function demoAd(done, title = "Demo ad") {
    const el = document.createElement("div");
    el.className = "ad-demo-player";
    el.innerHTML = `<div class="ad-demo-card"><span class="ad-label">Ad</span>` +
      `<p>${title}</p><p class="ad-demo-count">5</p>` +
      `<button type="button" class="ghost">Close</button></div>`;
    document.body.appendChild(el);
    let left = 5;
    const count = el.querySelector(".ad-demo-count");
    const t = setInterval(() => {
      left -= 1;
      count.textContent = String(left);
      if (left <= 0) { clearInterval(t); el.remove(); done(true); }
    }, 1000);
    el.querySelector("button").addEventListener("click", () => { clearInterval(t); el.remove(); done(false); });
  }

  // ── Revive offer ───────────────────────────────────────────────────────────
  let sheet = null;

  function close() {
    if (sheet) { sheet.remove(); sheet = null; }
  }

  async function offerRevive({ onRevive, onEnd, what = "board" }) {
    await getConfig();
    if (!S.cfg || !S.cfg.rewarded) { onEnd(); return; }
    if (sheet) return;                              // already showing
    close();
    track("revive_offered");
    let finished = false;
    const end = () => { if (finished) return; finished = true; close(); track("revive_declined"); onEnd(); };

    sheet = document.createElement("div");
    sheet.className = "revive";
    sheet.setAttribute("role", "dialog");
    sheet.setAttribute("aria-modal", "true");
    sheet.setAttribute("aria-labelledby", "reviveTitle");
    sheet.innerHTML =
      `<div class="revive-card">` +
        `<span class="revive-icon" aria-hidden="true"></span>` +
        `<h2 id="reviveTitle">Out of lives</h2>` +
        `<p class="revive-text">Checking for an ad…</p>` +
        `<div class="revive-actions">` +
          `<button type="button" class="revive-go" disabled>Watch ad for +1 life</button>` +
          `<button type="button" class="ghost revive-end">End ${what}</button>` +
        `</div>` +
        `<p class="revive-fine">One revive per board. The answers stay hidden until you choose.</p>` +
      `</div>`;
    document.body.appendChild(sheet);
    const text = sheet.querySelector(".revive-text");
    const go = sheet.querySelector(".revive-go");
    sheet.querySelector(".revive-end").addEventListener("click", end);

    requestReward({
      ready: (show) => {
        if (finished) return;
        text.textContent = "Watch a short ad to keep going with one more life.";
        go.disabled = false;
        go.focus({ preventScroll: true });
        go.onclick = () => { go.disabled = true; text.textContent = "Playing the ad…"; show(); };
      },
      none: () => {
        if (finished) return;
        text.textContent = "No ad is available right now, so this board is over.";
        go.hidden = true;
        sheet.querySelector(".revive-end").textContent = "See answers";
      },
      done: async (viewed) => {
        if (finished) return;
        if (!viewed) {
          text.textContent = "The ad was closed early, so no extra life. You can try again.";
          // A fresh ad for a second try.
          go.disabled = true;
          requestReward({
            ready: (show) => { if (finished) return; go.disabled = false; go.onclick = () => { go.disabled = true; show(); }; },
            none: () => { if (!finished) { go.hidden = true; text.textContent = "No ad is available right now, so this board is over."; } },
            done: (v) => { if (v && !finished) grant(); },
          });
          return;
        }
        grant();
      },
    });

    async function grant() {
      finished = true;
      track("revive_watched");
      text.textContent = "Adding a life…";
      try {
        await onRevive();
        close();
      } catch (e) {
        finished = false;
        text.textContent = (e && e.message) || "Couldn't add the life. Try again or end the board.";
      }
    }
  }

  function debounce(fn, ms) {
    let t = 0;
    return () => { clearTimeout(t); t = setTimeout(fn, ms); };
  }

  window.PDD_ADS = {
    offerRevive,
    boardBreak,
    track,
    get reviveOn() { return !!(S.cfg && S.cfg.rewarded); },
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
