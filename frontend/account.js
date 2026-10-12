/* ============================================================================
   account.js — Trainer accounts (Sign in with Google)
   ----------------------------------------------------------------------------
   • Header: "Sign in" when you're a guest; your avatar + trainer name when
     you're signed in.
   • Sign-in sheet: shows what this device would save (Pokémon, shinies,
     streak), why an account is worth it, and Google's sign-in button.
   • Trainer card: your name (editable — it'll be your leaderboard name),
     your stats, sign out.
   • Nudges, for guests only and never more than once a day: right after a
     shiny, or once you've caught a few Pokémon. Plus a quiet "Save" chip in
     the Pokédex header.

   The server does the real work (auth_api.py): signing in moves this device's
   guest progress onto the account, and from then on every request is
   answered for the account. So after signing in or out we simply reload the
   page and everything (board, Pokédex, Reverse) comes back for the right
   player.

   Sign-in only appears once GOOGLE_CLIENT_ID is set on the server.
   Loads after app.js; uses fetchJson, state, setMessage.
   ============================================================================ */
(function () {
  "use strict";

  if (typeof fetchJson !== "function") return;

  const GIS_SRC = "https://accounts.google.com/gsi/client";
  const NUDGE_KEY = "pdd.acctNudge";       // date of the last nudge shown
  const WELCOME_KEY = "pdd.acctWelcome";   // message to show after the reload
  const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)");

  const A = {
    enabled: false,
    clientId: null,
    account: null,
    progress: { caught: 0, shinies: 0, streak: 0, charms: 0, categories: 0 },
    gisReady: null,
  };

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const plural = (n, one, many = one + "s") => `${n.toLocaleString()} ${n === 1 ? one : many}`;
  const api = (path, opts) => fetchJson(`${state.apiBase}${path}`, opts);

  // ── Start ──────────────────────────────────────────────────────────────────
  async function init() {
    try {
      const me = await api("/auth/me");
      A.enabled = !!me.enabled;
      A.account = me.account || null;
      A.progress = me.progress || A.progress;
      if (A.enabled && !A.account) {
        const cfg = await api("/auth/config");
        A.clientId = cfg.google_client_id;
      }
    } catch (e) {
      return;     // older server, or offline: no account UI at all
    }
    renderHeader();
    dexChip();
    showWelcomeBack();
    if (A.enabled && !A.account) {
      watchShinies();
      // A first gentle nudge once there's something worth saving.
      setTimeout(() => {
        if (A.progress.caught >= 3) nudge(`You've caught ${plural(A.progress.caught, "Pokémon", "Pokémon")} on this device. Save them to an account so they're on every device.`);
      }, 12000);
    }
  }

  // ── Header button ──────────────────────────────────────────────────────────
  function renderHeader() {
    const bar = document.querySelector(".topbar-actions");
    if (!bar || (!A.enabled && !A.account)) return;
    let btn = $("acctBtn");
    if (!btn) {
      btn = document.createElement("button");
      btn.type = "button";
      btn.id = "acctBtn";
      btn.className = "icon-btn acct-btn";
      bar.appendChild(btn);
      btn.addEventListener("click", () => (A.account ? openCard() : openSignIn()));
    }
    if (A.account) {
      btn.classList.add("signed-in");
      btn.setAttribute("aria-label", `Trainer card: ${A.account.trainer_name}`);
      btn.innerHTML = `<span class="acct-avatar" aria-hidden="true"></span><span class="icon-btn-label"></span>`;
      paintAvatar(btn.querySelector(".acct-avatar"), A.account);
      btn.querySelector(".icon-btn-label").textContent = A.account.trainer_name;
    } else {
      btn.classList.remove("signed-in");
      btn.setAttribute("aria-label", "Sign in");
      btn.innerHTML = `<span class="acct-ghost" aria-hidden="true"></span><span class="icon-btn-label">Sign in</span>`;
    }
  }

  function paintAvatar(el, acct) {
    el.textContent = "";
    if (acct.picture) {
      const img = document.createElement("img");
      img.alt = "";
      img.referrerPolicy = "no-referrer";       // Google avatars need this
      img.src = acct.picture;
      img.onerror = () => { img.remove(); el.textContent = initial(acct); };
      el.appendChild(img);
    } else {
      el.textContent = initial(acct);
    }
  }
  const initial = (acct) => (acct.trainer_name || acct.name || "?").trim().charAt(0).toUpperCase();

  // ── Sheets (sign-in + trainer card share one shell) ────────────────────────
  let sheet = null;
  function openSheet(html, cls) {
    closeSheet();
    sheet = document.createElement("div");
    sheet.className = `acct ${cls}`;
    sheet.setAttribute("role", "dialog");
    sheet.setAttribute("aria-modal", "true");
    sheet.innerHTML = `<div class="acct-card"><button type="button" class="acct-x" aria-label="Close">✕</button>${html}</div>`;
    sheet.addEventListener("click", (e) => { if (e.target === sheet) closeSheet(); });
    sheet.querySelector(".acct-x").addEventListener("click", closeSheet);
    document.body.appendChild(sheet);
    document.documentElement.classList.add("acct-lock");
    return sheet;
  }
  function closeSheet() {
    if (!sheet) return;
    sheet.remove();
    sheet = null;
    document.documentElement.classList.remove("acct-lock");
  }
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeSheet(); });

  // What this device would bring along, in one friendly line.
  function haulLine() {
    const p = A.progress;
    const bits = [];
    if (p.caught) bits.push(`caught <b>${plural(p.caught, "Pokémon", "Pokémon")}</b>`);
    if (p.shinies) bits.push(`found <b>${plural(p.shinies, "shiny", "shinies")}</b>`);
    if (p.streak > 1) bits.push(`built a <b>${p.streak}-day streak</b>`);
    if (!bits.length) return "Start your journey with an account and every catch is saved from day one.";
    const list = bits.length > 1 ? `${bits.slice(0, -1).join(", ")} and ${bits[bits.length - 1]}` : bits[0];
    return `You've ${list} on this device. Sign in and it all comes with you.`;
  }

  function openSignIn(reason) {
    const s = openSheet(
      `<p class="acct-kicker">Trainer account</p>` +
      `<h2 class="acct-title">Save your journey</h2>` +
      `<p class="acct-lead">${reason ? esc(reason) : haulLine()}</p>` +
      `<ul class="acct-perks">` +
        `<li><span class="acct-perk-ico" aria-hidden="true">📱</span><span><b>Play anywhere.</b> Start on your laptop, finish on your phone.</span></li>` +
        `<li><span class="acct-perk-ico" aria-hidden="true">✨</span><span><b>Never lose a shiny.</b> Your Pokédex lives on your account, not this browser.</span></li>` +
        `<li><span class="acct-perk-ico" aria-hidden="true">🔥</span><span><b>Keep your streak</b> and your Shiny Charm odds wherever you play.</span></li>` +
        `<li><span class="acct-perk-ico" aria-hidden="true">🏆</span><span><b>Leaderboards are coming.</b> Claim your trainer name early.</span></li>` +
      `</ul>` +
      `<div class="acct-google" id="acctGoogle"><span class="acct-loading">Loading Google sign-in…</span></div>` +
      `<p class="acct-fine">We only use your name, email and profile picture. No passwords, no posting. <a href="./privacy.html" target="_blank" rel="noopener">Privacy</a></p>`,
      "acct-signin",
    );
    loadGis().then(() => {
      if (!sheet || sheet !== s) return;
      const slot = $("acctGoogle");
      slot.textContent = "";
      window.google.accounts.id.renderButton(slot, {
        type: "standard", theme: "filled_black", size: "large", shape: "pill",
        text: "continue_with", logo_alignment: "left", width: Math.min(320, slot.clientWidth || 320),
      });
    }).catch(() => {
      const slot = $("acctGoogle");
      if (slot) slot.innerHTML = `<span class="acct-loading">Couldn't reach Google. Check your connection (or an ad blocker) and try again.</span>`;
    });
  }

  function loadGis() {
    if (A.gisReady) return A.gisReady;
    A.gisReady = new Promise((resolve, reject) => {
      const done = () => {
        try {
          window.google.accounts.id.initialize({
            client_id: A.clientId,
            callback: onCredential,
            auto_select: false,
            cancel_on_tap_outside: true,
            use_fedcm_for_prompt: true,
          });
          resolve();
        } catch (e) { reject(e); }
      };
      if (window.google?.accounts?.id) return done();
      const sc = document.createElement("script");
      sc.src = GIS_SRC;
      sc.async = true;
      sc.defer = true;
      sc.onload = done;
      sc.onerror = () => { A.gisReady = null; reject(new Error("gis")); };
      document.head.appendChild(sc);
    });
    return A.gisReady;
  }

  async function onCredential(resp) {
    const slot = $("acctGoogle");
    if (slot) slot.innerHTML = `<span class="acct-loading">Signing you in…</span>`;
    try {
      const res = await api("/auth/google", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ credential: resp.credential }),
      });
      const name = res.account.trainer_name;
      const msg = res.is_new
        ? `Welcome to PokeDokuDoku, ${name}!` + (res.merged ? ` ${plural(res.merged, "Pokémon", "Pokémon")} saved to your account.` : "")
        : `Welcome back, ${name}!` + (res.merged ? ` ${plural(res.merged, "new Pokémon", "new Pokémon")} added from this device.` : "");
      try { sessionStorage.setItem(WELCOME_KEY, JSON.stringify({ msg, isNew: res.is_new })); } catch (_) { /* ignore */ }
      // Reload so the board, Pokédex and Reverse all come back for the account.
      location.reload();
    } catch (e) {
      if (slot) slot.innerHTML = `<span class="acct-loading err">${esc(e.message || "Sign-in failed. Try again.")}</span>`;
    }
  }

  function showWelcomeBack() {
    let w = null;
    try { w = JSON.parse(sessionStorage.getItem(WELCOME_KEY) || "null"); sessionStorage.removeItem(WELCOME_KEY); } catch (_) { /* ignore */ }
    if (!w) return;
    setTimeout(() => {
      if (typeof setMessage === "function") setMessage(w.msg, "var(--ok)");
      // New trainers pick their name first: it's what leaderboards will show.
      if (w.isNew && A.account) openCard(true);
    }, 700);
  }

  // ── Trainer card ───────────────────────────────────────────────────────────
  function openCard(pickName = false) {
    const a = A.account;
    if (!a) return;
    const p = A.progress;
    const since = a.created_at ? new Date(a.created_at).toLocaleDateString(undefined, { month: "short", year: "numeric" }) : "";
    const s = openSheet(
      `<div class="acct-id">` +
        `<span class="acct-avatar big" aria-hidden="true"></span>` +
        `<div class="acct-id-text">` +
          `<p class="acct-kicker">${pickName ? "Pick your trainer name" : "Trainer card"}</p>` +
          `<form class="acct-name-form" id="acctNameForm" autocomplete="off">` +
            `<input id="acctName" maxlength="16" spellcheck="false" aria-label="Trainer name">` +
            `<button type="submit" class="acct-save" id="acctSave">Save</button>` +
          `</form>` +
          `<p class="acct-name-msg" id="acctNameMsg">${since ? `Trainer since ${esc(since)}` : ""}</p>` +
        `</div>` +
      `</div>` +
      `<div class="acct-stats">` +
        stat(p.caught, "Pokémon") + stat(p.shinies, p.shinies === 1 ? "Shiny" : "Shinies") +
        stat(p.streak, "Day streak") + stat(p.categories, "Categories") +
      `</div>` +
      `<p class="acct-soon"><span aria-hidden="true">🏆</span> Leaderboards are on the way — this name is how you'll show up.</p>` +
      `<div class="acct-foot"><span class="acct-email">${esc(a.email)}</span>` +
      `<button type="button" class="acct-out" id="acctOut">Sign out</button></div>`,
      "acct-trainer",
    );
    paintAvatar(s.querySelector(".acct-avatar"), a);
    const input = $("acctName");
    input.value = a.trainer_name;
    $("acctNameForm").addEventListener("submit", saveName);
    $("acctOut").addEventListener("click", signOut);
    if (pickName) setTimeout(() => { input.focus(); input.select(); }, 80);
  }
  const stat = (n, label) => `<div class="acct-stat"><b>${Number(n || 0).toLocaleString()}</b><span>${esc(label)}</span></div>`;

  async function saveName(e) {
    e.preventDefault();
    const input = $("acctName");
    const msg = $("acctNameMsg");
    const val = input.value.trim();
    if (val === A.account.trainer_name) { closeSheet(); return; }
    $("acctSave").disabled = true;
    try {
      const acct = await api("/auth/profile", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ trainer_name: val }),
      });
      A.account = acct;
      renderHeader();
      msg.textContent = "Saved ✓";
      msg.className = "acct-name-msg ok";
      setTimeout(closeSheet, 650);
    } catch (err) {
      msg.textContent = err.message || "Couldn't save that name.";
      msg.className = "acct-name-msg err";
    } finally {
      const b = $("acctSave");
      if (b) b.disabled = false;
    }
  }

  async function signOut() {
    try { await api("/auth/logout", { method: "POST" }); } catch (_) { /* ignore */ }
    try { window.google?.accounts?.id?.disableAutoSelect(); } catch (_) { /* ignore */ }
    try { sessionStorage.setItem(WELCOME_KEY, JSON.stringify({ msg: "Signed out. Your progress is saved to your account." })); } catch (_) { /* ignore */ }
    location.reload();
  }

  // ── Nudges (guests only, at most once a day) ───────────────────────────────
  function nudge(text) {
    if (!A.enabled || A.account || sheet || document.querySelector(".acct-nudge")) return;
    const today = new Date().toDateString();
    try { if (localStorage.getItem(NUDGE_KEY) === today) return; localStorage.setItem(NUDGE_KEY, today); } catch (_) { /* ignore */ }
    const n = document.createElement("div");
    n.className = "acct-nudge";
    n.setAttribute("role", "status");
    n.innerHTML =
      `<span class="acct-nudge-ico" aria-hidden="true">💾</span>` +
      `<p></p>` +
      `<div class="acct-nudge-actions"><button type="button" class="acct-nudge-go">Save progress</button>` +
      `<button type="button" class="acct-nudge-no" aria-label="Not now">Not now</button></div>`;
    n.querySelector("p").textContent = text;
    const close = () => { n.classList.add("out"); setTimeout(() => n.remove(), REDUCED.matches ? 0 : 220); };
    n.querySelector(".acct-nudge-go").addEventListener("click", () => { close(); openSignIn(); });
    n.querySelector(".acct-nudge-no").addEventListener("click", close);
    document.body.appendChild(n);
    setTimeout(() => { if (n.isConnected) close(); }, 14000);
  }

  // Right after a shiny is the moment an account matters most.
  function watchShinies() {
    const S = window.PDD_SHINY;
    if (!S || S.__acctWrapped) return;
    const orig = S.celebrate;
    S.celebrate = function (opts) {
      orig.apply(this, arguments);
      if (opts && opts.registered) {
        A.progress.shinies += 1;
        try { localStorage.removeItem(NUDGE_KEY); } catch (_) { /* this one always shows */ }
        setTimeout(() => nudge(`A shiny ${opts.name || ""}! Sign in so it's never lost, on any device.`), 3800);
      }
    };
    S.__acctWrapped = true;
  }

  // A quiet "Save" chip in the Pokédex header for guests.
  function dexChip() {
    if (!A.enabled || A.account) return;
    const head = document.querySelector("#pokedexModal .dex-heading");
    if (!head || head.querySelector(".acct-dex-cta")) return;
    const b = document.createElement("button");
    b.type = "button";
    b.className = "acct-dex-cta";
    b.innerHTML = `<span aria-hidden="true">💾</span> Playing as guest · <u>Save to account</u>`;
    b.addEventListener("click", () => { window.PDD_POKEDEX?.close?.(); openSignIn(); });
    head.appendChild(b);
  }

  window.PDD_ACCOUNT = { openSignIn, openCard, get account() { return A.account; } };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
