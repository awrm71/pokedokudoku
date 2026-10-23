/* ============================================================================
   motion.js — animated Pokémon, Black/White (Gen 5) pixel style.
   ----------------------------------------------------------------------------
   Where a Pokémon moves:
     • Board squares: while you hover (or tap) a filled square, and for a few
       seconds right after you fill it. Only that one square animates.
     • Reverse Board: same, hover or tap a Pokémon.
     • Pokédex: the grid stays still. Opening a Pokémon plays a short
       battle-style entrance and then its animation loops on the platform.

   The animations are the Black/White sprites (Gen 1–5, from the games) and
   the Smogon community's Gen 5-style animations for Gen 6–9, served by the
   PokeAPI sprite repo. Not every newer Pokémon has one yet (most of Gen 9 and
   the Legends Z-A Megas don't): those keep their still sprite and give a
   small hop instead, and pick up an animation automatically once one is
   added upstream.

   Nothing loads until it's needed: one GIF, for the Pokémon you're looking
   at. Turned off entirely for people who ask their system for less motion.
   ============================================================================ */
(function () {
  "use strict";

  const BASE = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/versions/generation-v/black-white/animated";
  const CANVAS = 96;            // still sprites sit on a 96×96 canvas
  const PLAY_MS = 2600;         // how long a square animates after filling / tapping

  const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)");
  const calm = () => !!reduced?.matches;

  const missing = new Set();    // URLs with no animation (don't ask twice)
  const ready = new Map();      // URL -> Promise<HTMLImageElement|null>

  function animUrl(id, shiny) {
    if (!id) return null;
    return `${BASE}/${shiny ? "shiny/" : ""}${id}.gif`;
  }

  // Load an animation once; resolves null when there isn't one.
  function load(url) {
    if (!url || missing.has(url)) return Promise.resolve(null);
    if (ready.has(url)) return ready.get(url);
    const p = new Promise((resolve) => {
      const im = new Image();
      im.decoding = "async";
      im.onload = () => resolve(im);
      im.onerror = () => { missing.add(url); ready.delete(url); resolve(null); };
      im.src = url;
    });
    ready.set(url, p);
    return p;
  }

  // ── Overlay on a still sprite ──────────────────────────────────────────────
  // The animation goes in a layer over the still image, sized from the
  // 96px canvas so it matches the still exactly, so nothing on the page moves.
  function stop(still) {
    const o = still.__pxAnim;
    if (o) { clearTimeout(o.timer); o.el.remove(); still.__pxAnim = null; }
    still.classList.remove("px-hidden", "px-hop");
  }

  async function play(still, id, shiny, ms) {
    if (!still || !id || calm() || !still.isConnected) return;
    const url = animUrl(id, shiny);
    const cur = still.__pxAnim;
    if (cur && cur.url === url) {                  // already playing: just extend
      clearTimeout(cur.timer);
      if (ms) cur.timer = setTimeout(() => stop(still), ms);
      return;
    }
    stop(still);
    const token = (still.__pxToken = (still.__pxToken || 0) + 1);
    const gif = await load(url);
    if (token !== still.__pxToken || !still.isConnected) return;
    if (!gif) { hop(still); return; }

    const host = still.parentElement;
    if (getComputedStyle(host).position === "static") host.style.position = "relative";
    const scale = (still.clientWidth || CANVAS) / CANVAS;
    const w = gif.naturalWidth * scale;
    const h = gif.naturalHeight * scale;
    const el = document.createElement("img");
    el.className = "px-anim";
    el.alt = "";
    el.src = url;
    el.style.width = `${w}px`;
    el.style.height = `${h}px`;
    // Centre on the still sprite, feet on the same line (sprites sit low on
    // their canvas, so align to the bottom with a little inset).
    const left = still.offsetLeft + (still.offsetWidth - w) / 2;
    const top = still.offsetTop + still.offsetHeight - h - still.offsetHeight * 0.06;
    el.style.left = `${left}px`;
    el.style.top = `${Math.max(still.offsetTop - h * 0.15, top)}px`;
    host.appendChild(el);
    still.classList.add("px-hidden");
    still.__pxAnim = { el, url, timer: ms ? setTimeout(() => stop(still), ms) : 0 };
  }

  // No animation for this one: a small two-step hop on the still sprite.
  function hop(still) {
    still.classList.remove("px-hop");
    void still.offsetWidth;
    still.classList.add("px-hop");
    setTimeout(() => still.classList.remove("px-hop"), 700);
  }

  // ── Board squares (Daily / Draft) ─────────────────────────────────────────
  function boardInfo(cellEl) {
    if (!cellEl?.classList.contains("locked") || typeof state === "undefined") return null;
    const cell = state.cells?.get(cellEl.dataset.cellId);
    const still = cellEl.querySelector(".summary-sprite");
    if (!cell?.pokemonId || !still || still.classList.contains("hidden")) return null;
    return { still, id: cell.pokemonId, shiny: !!cell.shiny };
  }

  // ── Reverse Board squares ─────────────────────────────────────────────────
  function reverseInfo(cellEl) {
    const still = cellEl?.querySelector(".rev-cell-sprite");
    const id = Number(still?.dataset.pid);
    if (!still || !id) return null;
    return { still, id, shiny: still.dataset.shiny === "1" };
  }

  const finders = [
    [".cell.locked", boardInfo],
    [".rev-cell", reverseInfo],
  ];
  function infoFor(target) {
    for (const [sel, fn] of finders) {
      const cellEl = target.closest?.(sel);
      if (cellEl) return fn(cellEl);
    }
    return null;
  }

  // Hover (mouse only) plays while the pointer is on the square.
  let hovered = null;
  document.addEventListener("pointerover", (e) => {
    if (e.pointerType !== "mouse") return;
    const info = infoFor(e.target);
    if (!info || info.still === hovered) return;
    if (hovered) stop(hovered);
    hovered = info.still;
    play(info.still, info.id, info.shiny, 0);
  });
  document.addEventListener("pointerout", (e) => {
    if (!hovered || e.pointerType !== "mouse") return;
    const to = e.relatedTarget;
    const sq = hovered.closest(".cell, .rev-cell");
    if (to && sq && sq.contains(to)) return;
    stop(hovered);
    hovered = null;
  });
  // Tap on a phone (or click a filled square): play for a few seconds.
  document.addEventListener("click", (e) => {
    const info = infoFor(e.target);
    if (info && info.still !== hovered) play(info.still, info.id, info.shiny, PLAY_MS);
  });

  // Called by app.js right after a correct pick (or a hint) fills a square.
  function celebrate(cellEl) {
    const info = boardInfo(cellEl);
    if (info) setTimeout(() => play(info.still, info.id, info.shiny, PLAY_MS), 120);
  }

  // ── Pokédex detail: battle-style entrance, then the loop ──────────────────
  // `img` is the detail page's sprite. The still shows first (instant), then
  // swaps to the animation once it has loaded.
  async function hero(img, id, shiny) {
    if (!img) return;
    img.classList.remove("px-live", "px-idle");
    img.style.width = img.style.height = "";
    const stage = img.closest(".dx-stage");
    stage?.classList.remove("px-enter");
    void stage?.offsetWidth;
    stage?.classList.add("px-enter");
    if (!id || calm()) return;
    const url = animUrl(id, shiny);
    const token = (img.__pxToken = (img.__pxToken || 0) + 1);
    const gif = await load(url);
    if (token !== img.__pxToken || !img.isConnected) return;
    if (!gif) { img.classList.add("px-idle"); return; }
    const box = img.clientWidth || 168;
    const scale = box / CANVAS;
    img.classList.remove("px-idle");
    img.classList.add("px-live");
    img.style.width = `${Math.round(gif.naturalWidth * scale)}px`;
    img.style.height = `${Math.round(gif.naturalHeight * scale)}px`;
    img.src = url;
  }

  window.PDD_MOTION = { animUrl, play, stop, celebrate, hero };
})();
