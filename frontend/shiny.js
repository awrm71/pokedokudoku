/* ============================================================================
   shiny.js — shiny Pokémon: sprites, the "you found a shiny!" moment, and
   Reverse Board sightings.
   ----------------------------------------------------------------------------
   The odds are rolled on the server for every correct pick (see api_main.py):
   1 in 4096, re-rolled +2 times per Shiny Charm like the real games, plus a
   small bonus for a Daily streak. This file only presents the result.

   • Daily Board shinies are registered in the Shiny Dex for good.
   • Draft and Shuffle boards roll too, as "sightings" that aren't kept.
   • Reverse Board has nothing to catch, so each Pokémon on it gets a quiet
     roll when the board is shown (stable for that board, so re-rendering
     never changes it) and a sighting shows up as a sparkling tile.

   Loads after app.js; uses state, getClientId, PLACEHOLDER, setSpriteSafe.
   ============================================================================ */
(function () {
  "use strict";

  const SHINY_CDN = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/other/official-artwork/shiny";
  const TRAINER_KEY = "pdd.trainer";
  const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)");

  // ── Sprites ───────────────────────────────────────────────────────────────
  function spriteUrl(pokemonId) {
    if (!pokemonId) return typeof PLACEHOLDER !== "undefined" ? PLACEHOLDER : "";
    if (typeof state !== "undefined" && state.runtimeMode === "mock") return `${SHINY_CDN}/${pokemonId}.png`;
    return `${(typeof state !== "undefined" && state.apiBase) || ""}/sprite/shiny/${pokemonId}.png`;
  }

  // ── Trainer info (charms/odds), cached by pokedex.js ─────────────────────
  function trainer() {
    try { return JSON.parse(localStorage.getItem(TRAINER_KEY)) || null; } catch (_) { return null; }
  }
  function saveTrainer(t) {
    try { localStorage.setItem(TRAINER_KEY, JSON.stringify(t)); } catch (_) { /* storage blocked */ }
  }

  // Charm tiers: our own names and colours. Charm n is the nth completed
  // Pokédex; beyond the last named tier it's Starlight with a counter.
  const TIERS = [
    { name: "No charm yet", a: "#5d4a63", b: "#3a2a40" },
    { name: "Spark", a: "#e6ecf5", b: "#8fa3bd" },
    { name: "Glimmer", a: "#ffe27a", b: "#d18b12" },
    { name: "Prism", a: "#ff8fd0", b: "#8d4bff" },
    { name: "Radiant", a: "#7ff3ff", b: "#2a7bff" },
    { name: "Starlight", a: "#fff6c9", b: "#ff5c9a" },
  ];
  function tier(charms) {
    const n = Math.max(0, charms | 0);
    const t = TIERS[Math.min(n, TIERS.length - 1)];
    return { ...t, label: n >= TIERS.length ? `${t.name} ×${n - TIERS.length + 2}` : t.name };
  }

  // ── The big moment ────────────────────────────────────────────────────────
  // cellEl: the board cell (or null), name/pokemonId: what was found,
  // registered: true for a Daily Board shiny that went into the Shiny Dex.
  function celebrate({ cellEl = null, name = "", pokemonId = null, registered = false } = {}) {
    if (cellEl) {
      cellEl.classList.add("shiny");
      cellEl.classList.remove("shiny-hit");
      void cellEl.offsetWidth;
      cellEl.classList.add("shiny-hit");
      setTimeout(() => cellEl.classList.remove("shiny-hit"), 1600);
      if (!REDUCED.matches) burst(cellEl.getBoundingClientRect());
    }

    const old = document.querySelector(".shiny-banner");
    if (old) old.remove();
    const b = document.createElement("div");
    b.className = "shiny-banner";
    b.setAttribute("role", "status");
    b.setAttribute("aria-live", "assertive");
    b.innerHTML =
      `<div class="shiny-banner-card">` +
      `<span class="shiny-banner-rays" aria-hidden="true"></span>` +
      `<img class="shiny-banner-img" alt="">` +
      `<p class="shiny-banner-kicker">✦ Shiny ✦</p>` +
      `<p class="shiny-banner-name"></p>` +
      `<p class="shiny-banner-sub"></p>` +
      `</div>`;
    const img = b.querySelector("img");
    if (pokemonId && typeof setSpriteSafe === "function") setSpriteSafe(img, spriteUrl(pokemonId)); else img.remove();
    b.querySelector(".shiny-banner-name").textContent = name ? `${name}!` : "A shiny appeared!";
    b.querySelector(".shiny-banner-sub").textContent = registered
      ? "Registered in your Shiny Dex. It stays forever."
      : "A sighting! Only Daily Board shinies join your Shiny Dex.";
    // Hold the card back until the shiny sprite has loaded (or 500 ms), so it
    // never pops in empty.
    b.classList.add("wait");
    const show = () => b.classList.remove("wait");
    if (img.isConnected) { img.addEventListener("load", show, { once: true }); img.addEventListener("error", show, { once: true }); }
    setTimeout(show, img.isConnected ? 500 : 0);
    document.body.appendChild(b);
    const kill = () => { if (!b.isConnected) return; b.classList.add("out"); setTimeout(() => b.remove(), 300); };
    b.addEventListener("click", kill);
    setTimeout(kill, 3600);

    // The Pokédex refreshes itself on open; just make sure it does.
    window.PDD_POKEDEX?.markStale?.();
  }

  // Stars that fly out of the cell.
  function burst(rect) {
    const cx = rect.left + rect.width / 2;
    const cy = rect.top + rect.height / 2;
    const layer = document.createElement("div");
    layer.className = "shiny-burst";
    layer.style.left = `${cx}px`;
    layer.style.top = `${cy}px`;
    const glyphs = ["✦", "✧", "★", "✦"];
    for (let i = 0; i < 14; i += 1) {
      const s = document.createElement("i");
      const ang = (i / 14) * Math.PI * 2 + Math.random() * 0.4;
      const dist = 60 + Math.random() * 70;
      s.textContent = glyphs[i % glyphs.length];
      s.style.setProperty("--x", `${Math.cos(ang) * dist}px`);
      s.style.setProperty("--y", `${Math.sin(ang) * dist}px`);
      s.style.setProperty("--s", String(0.7 + Math.random() * 0.9));
      s.style.setProperty("--d", `${Math.random() * 120}ms`);
      layer.appendChild(s);
    }
    const ring = document.createElement("b");
    layer.appendChild(ring);
    document.body.appendChild(layer);
    setTimeout(() => layer.remove(), 1400);
  }

  // ── Reverse Board sightings ───────────────────────────────────────────────
  // Deterministic per board + Pokémon + player, so the same board always
  // shows the same sightings (Reverse re-draws the grid after every solve).
  function hash53(str) {
    let h1 = 0xdeadbeef, h2 = 0x41c6ce57;
    for (let i = 0; i < str.length; i += 1) {
      const ch = str.charCodeAt(i);
      h1 = Math.imul(h1 ^ ch, 2654435761);
      h2 = Math.imul(h2 ^ ch, 1597334677);
    }
    h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
    h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
    return 4294967296 * (2097151 & h2) + (h1 >>> 0);
  }

  function chance() {
    const t = trainer();
    const charms = Math.min(t?.charms || 0, t?.max_charms || 10);
    const rolls = 1 + 2 * charms;          // charms count off the Daily; streaks don't
    const base = Math.max(1, t?.base_odds || 4096);   // follows the server, incl. test mode
    return 1 - Math.pow(1 - 1 / base, rolls);
  }

  function reverseSighting(board, i, j, mon) {
    if (!board || !mon?.pokemon_id) return false;
    // Daily boards are stable across reloads; custom ones for this session.
    if (!board.__shinySalt) board.__shinySalt = board.day_seed || `custom-${Math.random()}`;
    const who = typeof getClientId === "function" ? getClientId() : "";
    const u = hash53(`${who}|${board.__shinySalt}|${i}|${j}|${mon.pokemon_id}`) / 9007199254740992;
    const hit = u < chance();
    if (hit) (board.__shinyHits ||= new Map()).set(`${i},${j}`, mon);
    return hit;
  }

  function announceReverse(board) {
    if (!board?.__shinyHits?.size || board.__shinyAnnounced) return;
    board.__shinyAnnounced = true;
    const mon = board.__shinyHits.values().next().value;
    setTimeout(() => celebrate({ name: mon.display_name, pokemonId: mon.pokemon_id, registered: false }), 600);
  }

  window.PDD_SHINY = { spriteUrl, celebrate, trainer, saveTrainer, tier, reverseSighting, announceReverse };
})();