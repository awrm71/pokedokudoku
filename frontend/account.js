/* ============================================================================
   account.js — trainer accounts
   ----------------------------------------------------------------------------
   Two ways in: Google, or email + password. Either way the player picks a
   trainer name (what they're called on the site and, later, leaderboards)
   and can choose a partner Pokémon whose sprite becomes their avatar.

   • Header: "Sign in" for guests; partner sprite (or photo) + trainer name
     once signed in, and the brand line greets them by name.
   • Sign-in sheet: Google button, then "Create account" / "Sign in" with
     email. "Forgot password?" appears when the server can send email.
   • Trainer card: rename, choose a partner, progress, sign out.
   • Guests get one gentle reminder a day at most (after a shiny, or once
     they've caught a few), plus a "Save to account" link in the Pokédex.

   The server does the real work (auth_api.py): signing in moves this
   device's guest progress onto the account. After signing in or out the page
   reloads, so the board, Pokédex and Reverse all come back for the right
   player. Loads after app.js; uses fetchJson, state, setMessage.
   ============================================================================ */
(function () {
  "use strict";

  if (typeof fetchJson !== "function") return;

  const GIS_SRC = "https://accounts.google.com/gsi/client";
  const SPRITE = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon";
  const NUDGE_KEY = "pdd.acctNudge";
  const WELCOME_KEY = "pdd.acctWelcome";

  const A = {
    cfg: { google_client_id: null, reset: false, name_min: 3, name_max: 16, password_min: 8 },
    account: null,
    progress: { caught: 0, shinies: 0, streak: 0, charms: 0, categories: 0 },
    gisReady: null,
  };

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const n = (v) => Number(v || 0).toLocaleString();
  const plural = (v, one, many = one + "s") => `${n(v)} ${v === 1 ? one : many}`;

  // The account endpoints answer errors as {detail: {message, field}};
  // this keeps the message and which field it belongs to.
  async function call(path, body) {
    const opts = { method: body ? "POST" : "GET", headers: { "X-Client-Id": getClientId() } };
    if (body) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
    const res = await fetch(`${state.apiBase}${path}`, opts);
    let data = null;
    try { data = await res.json(); } catch (_) { /* empty body */ }
    if (!res.ok) {
      const d = data && data.detail;
      const err = new Error((d && (d.message || (typeof d === "string" ? d : ""))) || `Something went wrong (${res.status}).`);
      err.field = (d && d.field) || "";
      throw err;
    }
    return data;
  }

  // ── Start ──────────────────────────────────────────────────────────────────
  async function init() {
    try {
      const [me, cfg] = await Promise.all([call("/auth/me"), call("/auth/config")]);
      A.account = me.account || null;
      A.progress = me.progress || A.progress;
      A.cfg = { ...A.cfg, ...cfg };
    } catch (_) {
      return;     // older server or offline: no account UI
    }
    renderHeader();
    greet();
    dexLink();
    showWelcome();
    if (!A.account) {
      watchShinies();
      setTimeout(() => {
        if (A.progress.caught >= 3) {
          nudge(`You've caught ${plural(A.progress.caught, "Pokémon", "Pokémon")} on this device. Make an account to keep them everywhere.`);
        }
      }, 12000);
    }
  }

  // ── Header + greeting ──────────────────────────────────────────────────────
  function avatar(el, acct, big = false) {
    el.textContent = "";
    el.className = "acct-avatar" + (big ? " big" : "");
    if (acct.partner_id) {
      el.classList.add("partner");
      const img = document.createElement("img");
      img.alt = "";
      img.src = `${SPRITE}/${acct.partner_id}.png`;
      el.appendChild(img);
    } else if (acct.picture) {
      const img = document.createElement("img");
      img.alt = "";
      img.referrerPolicy = "no-referrer";
      img.src = acct.picture;
      img.onerror = () => { img.remove(); el.textContent = initial(acct); };
      el.appendChild(img);
    } else {
      el.textContent = initial(acct);
    }
  }
  const initial = (acct) => (acct.trainer_name || "?").trim().charAt(0).toUpperCase();

  function renderHeader() {
    const bar = document.querySelector(".topbar-actions");
    if (!bar) return;
    let btn = $("acctBtn");
    if (!btn) {
      btn = document.createElement("button");
      btn.type = "button";
      btn.id = "acctBtn";
      btn.className = "icon-btn acct-btn";
      bar.appendChild(btn);
      btn.addEventListener("click", () => (A.account ? openCard() : openSignIn()));
    }
    btn.classList.toggle("signed-in", !!A.account);
    if (A.account) {
      btn.setAttribute("aria-label", `Trainer card: ${A.account.trainer_name}`);
      btn.innerHTML = `<span class="acct-avatar"></span><span class="icon-btn-label"></span>`;
      avatar(btn.firstChild, A.account);
      btn.lastChild.textContent = A.account.trainer_name;
    } else {
      btn.setAttribute("aria-label", "Sign in");
      btn.innerHTML = `<span class="acct-ghost" aria-hidden="true"></span><span class="icon-btn-label">Sign in</span>`;
    }
  }

  // The line under the logo says hello to signed-in trainers.
  function greet() {
    const tag = document.querySelector(".brand-tag");
    if (!tag) return;
    if (!tag.dataset.orig) tag.dataset.orig = tag.textContent;
    if (!A.account) { tag.textContent = tag.dataset.orig; tag.classList.remove("acct-greet"); return; }
    const h = new Date().getHours();
    const part = h < 5 ? "Up late" : h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
    const streak = A.progress.streak > 1 ? ` · ${A.progress.streak}-day streak` : "";
    tag.textContent = `${part}, ${A.account.trainer_name}${streak}`;
    tag.classList.add("acct-greet");
  }

  // ── Sheet shell ────────────────────────────────────────────────────────────
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
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && sheet) closeSheet(); });

  function haulLine() {
    const p = A.progress;
    const bits = [];
    if (p.caught) bits.push(plural(p.caught, "Pokémon", "Pokémon"));
    if (p.shinies) bits.push(plural(p.shinies, "shiny", "shinies"));
    if (p.streak > 1) bits.push(`a ${p.streak}-day streak`);
    if (!bits.length) return "Keep your Pokédex, shinies and streak on every device.";
    const list = bits.length > 1 ? `${bits.slice(0, -1).join(", ")} and ${bits[bits.length - 1]}` : bits[0];
    return `You have ${list} on this device. An account keeps them on every device.`;
  }

  // ── Sign in / create account ──────────────────────────────────────────────
  function openSignIn(mode = "create", reason = "") {
    const google = !!A.cfg.google_client_id;
    const s = openSheet(
      `<h2 class="acct-title">Save your progress</h2>` +
      `<p class="acct-lead">${esc(reason || haulLine())}</p>` +
      (google ? `<div class="acct-google" id="acctGoogle"><span class="acct-note">Loading Google…</span></div>` +
                `<p class="acct-or"><span>or with email</span></p>` : "") +
      `<div class="acct-tabs" role="tablist">` +
        `<button type="button" role="tab" data-mode="create">Create account</button>` +
        `<button type="button" role="tab" data-mode="login">Sign in</button>` +
      `</div>` +
      `<form class="acct-form" id="acctForm" novalidate></form>` +
      `<p class="acct-fine">Your trainer name is public. Your email isn't. <a href="./privacy.html" target="_blank" rel="noopener">Privacy</a></p>`,
      "acct-signin",
    );
    s.querySelectorAll(".acct-tabs button").forEach((b) => b.addEventListener("click", () => showForm(b.dataset.mode)));
    showForm(mode);
    if (google) mountGoogle(s);
  }

  function field(id, label, type, attrs = "") {
    return `<label class="acct-field" for="${id}"><span>${label}</span>` +
      `<input id="${id}" type="${type}" ${attrs}><small class="acct-err" data-for="${id}"></small></label>`;
  }

  function showForm(mode) {
    const form = $("acctForm");
    if (!form) return;
    sheet.querySelectorAll(".acct-tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.mode === mode)));
    sheet.querySelector(".acct-tabs").hidden = mode === "forgot" || mode === "reset";
    form.dataset.mode = mode;
    const pw = A.cfg.password_min;
    if (mode === "create") {
      form.innerHTML =
        field("acctTrainer", "Trainer name", "text", `maxlength="${A.cfg.name_max}" autocomplete="nickname" spellcheck="false" placeholder="What should we call you?"`) +
        field("acctEmail", "Email", "email", `autocomplete="email" inputmode="email" placeholder="you@example.com"`) +
        field("acctPassword", "Password", "password", `autocomplete="new-password" placeholder="${pw}+ characters"`) +
        `<button type="submit" class="acct-go">Create account</button>`;
      watchName($("acctTrainer"));
    } else if (mode === "login") {
      form.innerHTML =
        field("acctEmail", "Email", "email", `autocomplete="email" inputmode="email"`) +
        field("acctPassword", "Password", "password", `autocomplete="current-password"`) +
        `<button type="submit" class="acct-go">Sign in</button>` +
        (A.cfg.reset ? `<button type="button" class="acct-link" id="acctForgot">Forgot password?</button>` : "");
      $("acctForgot")?.addEventListener("click", () => showForm("forgot"));
    } else if (mode === "forgot") {
      form.innerHTML =
        `<p class="acct-step">We'll email you a 6-digit code.</p>` +
        field("acctEmail", "Email", "email", `autocomplete="email" inputmode="email"`) +
        `<button type="submit" class="acct-go">Send code</button>` +
        `<button type="button" class="acct-link" id="acctBack">Back to sign in</button>`;
      $("acctBack").addEventListener("click", () => showForm("login"));
    } else if (mode === "reset") {
      form.innerHTML =
        `<p class="acct-step">Check <b>${esc(form.dataset.email || "your email")}</b> for the code.</p>` +
        field("acctCode", "Code", "text", `inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="123456"`) +
        field("acctPassword", "New password", "password", `autocomplete="new-password" placeholder="${pw}+ characters"`) +
        `<button type="submit" class="acct-go">Set password and sign in</button>` +
        `<button type="button" class="acct-link" id="acctBack">Send a new code</button>`;
      $("acctBack").addEventListener("click", () => showForm("forgot"));
    }
    form.querySelectorAll('input[type="password"]').forEach(addReveal);
    // Typing in a field clears its old error (the name field checks itself).
    form.querySelectorAll("input").forEach((inp) => {
      if (inp.id !== "acctTrainer") inp.addEventListener("input", () => setErr(inp.id, ""));
    });
    form.onsubmit = submitForm;
    setTimeout(() => form.querySelector("input")?.focus({ preventScroll: true }), 50);
  }

  function addReveal(input) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "acct-reveal";
    b.textContent = "Show";
    b.setAttribute("aria-label", "Show password");
    b.addEventListener("click", () => {
      const show = input.type === "password";
      input.type = show ? "text" : "password";
      b.textContent = show ? "Hide" : "Show";
    });
    input.after(b);
  }

  // Live "is this name free?" check while typing a trainer name.
  function watchName(input, exceptSelf = false) {
    let t = 0;
    input.addEventListener("input", () => {
      clearTimeout(t);
      const v = input.value.trim();
      setErr(input.id, "");
      if (v.length < A.cfg.name_min) return;
      t = setTimeout(async () => {
        try {
          const r = await call(`/auth/name-available?name=${encodeURIComponent(v)}`);
          if (input.value.trim() !== v) return;
          if (exceptSelf && A.account && v.toLowerCase() === A.account.trainer_name.toLowerCase()) return;
          setErr(input.id, r.available ? "" : r.reason, r.available ? "Available" : "");
        } catch (_) { /* ignore */ }
      }, 350);
    });
  }

  function setErr(id, msg, ok = "") {
    const el = sheet?.querySelector(`.acct-err[data-for="${id}"]`);
    if (!el) return;
    el.textContent = msg || ok;
    el.classList.toggle("ok", !msg && !!ok);
    $(id)?.classList.toggle("bad", !!msg);
  }

  async function submitForm(e) {
    e.preventDefault();
    const form = e.currentTarget;
    const mode = form.dataset.mode;
    const go = form.querySelector(".acct-go");
    form.querySelectorAll(".acct-err").forEach((x) => { x.textContent = ""; x.classList.remove("ok"); });
    form.querySelectorAll("input").forEach((x) => x.classList.remove("bad"));
    const val = (id) => ($(id)?.value || "").trim();
    const pw = $("acctPassword")?.value || "";
    // Quick checks first so every problem shows at once (the server checks again).
    const probs = [];
    if (mode === "create") {
      const nm = val("acctTrainer");
      if (nm.length < A.cfg.name_min || nm.length > A.cfg.name_max) probs.push(["acctTrainer", `Use ${A.cfg.name_min} to ${A.cfg.name_max} characters.`]);
    }
    if ($("acctEmail") && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(val("acctEmail"))) probs.push(["acctEmail", "That doesn't look like an email address."]);
    if ((mode === "create" || mode === "reset") && pw.length < A.cfg.password_min) probs.push(["acctPassword", `Use at least ${A.cfg.password_min} characters.`]);
    if (mode === "login" && !pw) probs.push(["acctPassword", "Enter your password."]);
    if (mode === "reset" && !/^\d{6}$/.test(val("acctCode"))) probs.push(["acctCode", "The code is 6 digits."]);
    if (probs.length) {
      probs.forEach(([id, msg]) => setErr(id, msg));
      $(probs[0][0])?.focus();
      return;
    }
    go.disabled = true;
    try {
      if (mode === "create") {
        const res = await call("/auth/register", { trainer_name: val("acctTrainer"), email: val("acctEmail"), password: pw });
        done(res);
      } else if (mode === "login") {
        const res = await call("/auth/login", { email: val("acctEmail"), password: pw });
        done(res);
      } else if (mode === "forgot") {
        await call("/auth/password/forgot", { email: val("acctEmail") });
        form.dataset.email = val("acctEmail");
        showForm("reset");
      } else if (mode === "reset") {
        const res = await call("/auth/password/reset", { email: form.dataset.email, code: val("acctCode"), password: pw });
        done(res);
      }
    } catch (err) {
      const map = { trainer_name: "acctTrainer", email: "acctEmail", password: "acctPassword", code: "acctCode" };
      const id = map[err.field] || form.querySelector("input")?.id;
      setErr(id, err.message);
      $(id)?.focus();
    } finally {
      if (go.isConnected) go.disabled = false;
    }
  }

  // Signed in by any method: remember a hello, reload as the account.
  function done(res) {
    const name = res.account.trainer_name;
    const msg = res.is_new
      ? `Welcome, ${name}!` + (res.merged ? ` ${plural(res.merged, "Pokémon", "Pokémon")} saved to your account.` : "")
      : `Welcome back, ${name}.` + (res.merged ? ` ${plural(res.merged, "Pokémon", "Pokémon")} added from this device.` : "");
    try { sessionStorage.setItem(WELCOME_KEY, JSON.stringify({ msg, isNew: res.is_new })); } catch (_) { /* ignore */ }
    location.reload();
  }

  function showWelcome() {
    let w = null;
    try { w = JSON.parse(sessionStorage.getItem(WELCOME_KEY) || "null"); sessionStorage.removeItem(WELCOME_KEY); } catch (_) { /* ignore */ }
    if (!w) return;
    setTimeout(() => {
      if (typeof setMessage === "function") setMessage(w.msg, "var(--ok)");
      // New trainers land on their card to pick a partner (and fix the
      // name Google gave them).
      if (w.isNew && A.account) openCard(true);
    }, 700);
  }

  // ── Google ─────────────────────────────────────────────────────────────────
  function mountGoogle(s) {
    loadGis().then(() => {
      if (sheet !== s) return;
      const slot = $("acctGoogle");
      slot.textContent = "";
      window.google.accounts.id.renderButton(slot, {
        type: "standard", theme: "filled_black", size: "large", shape: "rectangular",
        text: "continue_with", logo_alignment: "left", width: Math.min(360, slot.clientWidth || 360),
      });
    }).catch(() => {
      const slot = $("acctGoogle");
      if (slot) slot.innerHTML = `<span class="acct-note">Google sign-in didn't load (an ad blocker can do this). Email works below.</span>`;
    });
  }

  function loadGis() {
    if (A.gisReady) return A.gisReady;
    A.gisReady = new Promise((resolve, reject) => {
      const ready = () => {
        try {
          window.google.accounts.id.initialize({
            client_id: A.cfg.google_client_id, callback: onGoogle,
            auto_select: false, cancel_on_tap_outside: true, use_fedcm_for_prompt: true,
          });
          resolve();
        } catch (e) { reject(e); }
      };
      if (window.google?.accounts?.id) return ready();
      const sc = document.createElement("script");
      sc.src = GIS_SRC;
      sc.async = true;
      sc.onload = ready;
      sc.onerror = () => { A.gisReady = null; reject(new Error("gis")); };
      document.head.appendChild(sc);
    });
    return A.gisReady;
  }

  async function onGoogle(resp) {
    const slot = $("acctGoogle");
    if (slot) slot.innerHTML = `<span class="acct-note">Signing you in…</span>`;
    try {
      done(await call("/auth/google", { credential: resp.credential }));
    } catch (err) {
      if (slot) slot.innerHTML = `<span class="acct-note err">${esc(err.message)}</span>`;
    }
  }

  // ── Trainer card ───────────────────────────────────────────────────────────
  function openCard(isNew = false) {
    const a = A.account;
    if (!a) return;
    const p = A.progress;
    const since = a.created_at ? new Date(a.created_at).toLocaleDateString(undefined, { month: "long", year: "numeric" }) : "";
    const ways = [a.has_google && "Google", a.has_password && "email and password"].filter(Boolean).join(" or ");
    const s = openSheet(
      (isNew ? `<p class="acct-hello">Welcome, ${esc(a.trainer_name)}. Pick a partner to finish your card.</p>` : "") +
      `<div class="acct-id">` +
        `<span class="acct-avatar big"></span>` +
        `<div class="acct-id-text">` +
          `<h2 class="acct-name" id="acctNameShow"></h2>` +
          `<p class="acct-sub">${since ? `Trainer since ${esc(since)}` : ""}${a.partner_name ? ` · Partner: ${esc(a.partner_name)}` : ""}</p>` +
        `</div>` +
      `</div>` +
      `<dl class="acct-stats">` +
        `<div><dt>Pokémon</dt><dd>${n(p.caught)}</dd></div>` +
        `<div><dt>Shinies</dt><dd>${n(p.shinies)}</dd></div>` +
        `<div><dt>Streak</dt><dd>${n(p.streak)}</dd></div>` +
        `<div><dt>Categories</dt><dd>${n(p.categories)}</dd></div>` +
      `</dl>` +
      `<form class="acct-form acct-edit" id="acctNameForm" novalidate>` +
        field("acctTrainerEdit", "Trainer name", "text", `maxlength="${A.cfg.name_max}" spellcheck="false"`) +
        `<button type="submit" class="acct-go small">Save name</button>` +
      `</form>` +
      `<div class="acct-partner">` +
        `<label class="acct-field" for="acctPartner"><span>Partner Pokémon</span>` +
        `<input id="acctPartner" type="text" placeholder="${a.partner_name ? esc(a.partner_name) : "Search, e.g. Pikachu"}" autocomplete="off" spellcheck="false">` +
        `<small class="acct-err" data-for="acctPartner"></small></label>` +
        `<div class="acct-picks" id="acctPicks"></div>` +
      `</div>` +
      `<div class="acct-foot"><span class="acct-email">${esc(a.email)}${ways ? ` · signs in with ${esc(ways)}` : ""}</span>` +
      `<button type="button" class="acct-out" id="acctOut">Sign out</button></div>`,
      "acct-trainer",
    );
    avatar(s.querySelector(".acct-avatar"), a, true);
    $("acctNameShow").textContent = a.trainer_name;
    const input = $("acctTrainerEdit");
    input.value = a.trainer_name;
    watchName(input, true);
    $("acctNameForm").addEventListener("submit", saveName);
    $("acctOut").addEventListener("click", signOut);
    partnerSearch($("acctPartner"));
    if (isNew) setTimeout(() => $("acctPartner")?.focus({ preventScroll: true }), 80);
  }

  async function saveName(e) {
    e.preventDefault();
    const input = $("acctTrainerEdit");
    const v = input.value.trim();
    if (v === A.account.trainer_name) return;
    try {
      A.account = await call("/auth/profile", { trainer_name: v });
      renderHeader(); greet();
      $("acctNameShow").textContent = A.account.trainer_name;
      setErr("acctTrainerEdit", "", "Saved");
    } catch (err) {
      setErr("acctTrainerEdit", err.message);
    }
  }

  // Partner picker: type a name, tap a sprite.
  function partnerSearch(input) {
    const box = $("acctPicks");
    let t = 0;
    input.addEventListener("input", () => {
      clearTimeout(t);
      const q = input.value.trim();
      if (q.length < 2) { box.replaceChildren(); return; }
      t = setTimeout(async () => {
        let list = [];
        try { list = await fetchJson(`${state.apiBase}/pokemon/suggest?prefix=${encodeURIComponent(q)}&limit=6`); } catch (_) { /* ignore */ }
        if (input.value.trim() !== q) return;
        box.replaceChildren(...list.filter((m) => m.pokemon_id).map((m) => {
          const b = document.createElement("button");
          b.type = "button";
          b.className = "acct-pick";
          b.innerHTML = `<img alt="" src="${SPRITE}/${m.pokemon_id}.png"><span></span>`;
          b.lastChild.textContent = m.display_name;
          b.addEventListener("click", () => setPartner(m));
          return b;
        }));
      }, 220);
    });
  }

  async function setPartner(m) {
    try {
      A.account = await call("/auth/profile", { partner_key: m.key });
      renderHeader();
      const card = sheet?.querySelector(".acct-avatar.big");
      if (card) avatar(card, A.account, true);
      const sub = sheet?.querySelector(".acct-sub");
      if (sub) sub.textContent = sub.textContent.replace(/ · Partner: .*$/, "") + ` · Partner: ${A.account.partner_name}`;
      $("acctPicks").replaceChildren();
      $("acctPartner").value = "";
      $("acctPartner").placeholder = A.account.partner_name;
      sheet?.querySelector(".acct-hello")?.remove();
      setErr("acctPartner", "", `${A.account.partner_name} is your partner.`);
    } catch (err) {
      setErr("acctPartner", err.message);
    }
  }

  async function signOut() {
    try { await call("/auth/logout", {}); } catch (_) { /* ignore */ }
    try { window.google?.accounts?.id?.disableAutoSelect(); } catch (_) { /* ignore */ }
    try { sessionStorage.setItem(WELCOME_KEY, JSON.stringify({ msg: "Signed out. Your progress is saved to your account." })); } catch (_) { /* ignore */ }
    location.reload();
  }

  // ── Reminders for guests (one a day at most) ───────────────────────────────
  function nudge(text) {
    if (A.account || sheet || document.querySelector(".acct-nudge")) return;
    const today = new Date().toDateString();
    try { if (localStorage.getItem(NUDGE_KEY) === today) return; localStorage.setItem(NUDGE_KEY, today); } catch (_) { /* ignore */ }
    const el = document.createElement("div");
    el.className = "acct-nudge";
    el.setAttribute("role", "status");
    el.innerHTML = `<p></p><div class="acct-nudge-actions">` +
      `<button type="button" class="acct-go small">Save progress</button>` +
      `<button type="button" class="acct-link">Not now</button></div>`;
    el.querySelector("p").textContent = text;
    const close = () => el.remove();
    el.querySelector(".acct-go").addEventListener("click", () => { close(); openSignIn(); });
    el.querySelector(".acct-link").addEventListener("click", close);
    document.body.appendChild(el);
    setTimeout(close, 14000);
  }

  // Right after a Daily shiny is when an account matters most.
  function watchShinies() {
    const S = window.PDD_SHINY;
    if (!S || S.__acctWrapped) return;
    const orig = S.celebrate;
    S.celebrate = function (opts) {
      orig.apply(this, arguments);
      if (opts && opts.registered) {
        A.progress.shinies += 1;
        try { localStorage.removeItem(NUDGE_KEY); } catch (_) { /* always show this one */ }
        setTimeout(() => nudge(`A shiny ${opts.name || ""}! Make an account so it's never lost.`), 3800);
      }
    };
    S.__acctWrapped = true;
  }

  function dexLink() {
    if (A.account) return;
    const head = document.querySelector("#pokedexModal .dex-heading");
    if (!head || head.querySelector(".acct-dex-cta")) return;
    const b = document.createElement("button");
    b.type = "button";
    b.className = "acct-dex-cta";
    b.textContent = "Playing as a guest. Save to an account";
    b.addEventListener("click", () => { window.PDD_POKEDEX?.close?.(); openSignIn(); });
    head.appendChild(b);
  }

  window.PDD_ACCOUNT = { openSignIn, openCard, get account() { return A.account; } };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
