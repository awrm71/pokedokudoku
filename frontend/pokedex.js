/* ============================================================================
   pokedex.js — the Pokédex (formerly "Your Palette")
   ----------------------------------------------------------------------------
   A collection tracker for Daily Board catches. Replaces the old Palette
   section of app.js.

   Why it was slow, and what changed:
   • Every open re-fetched /pokedex and rebuilt ~1,300 cards from scratch, and
     every keystroke in the search box did the same (new <img>s each time).
     Now the cards are built ONCE, in small batches so the modal paints
     immediately, and filters just show/hide them.
   • The list is fetched quietly in the background shortly after the page
     loads, so the first open is instant. Later opens show the cached list
     right away and refresh in the background; only cards whose state changed
     are touched.
   • Off-screen cards are skipped by the browser (content-visibility in
     pokedex.css) and sprites lazy-load, so scrolling stays smooth.
   • Detail responses are cached per Pokémon.

   What's new:
   • Pokédex numbers, a reveal animation for anything caught since your last
     visit, a confetti milestone banner, streak + next-milestone + latest
     catch stats, a Catch Log grouped by day, a Progress tab (by generation,
     type and rarity), and a detail panel you can page through with ← / →.
   • Uncaught entries open a teaser instead of doing nothing.
   • Shinies: shiny sprites, a Shiny filter (the Shiny Dex, which survives a
     restart), your current shiny odds and Shiny Charm, and the "seal and
     restart" flow once the Pokédex is complete. Odds live in api_main.py.

   Uses app.js globals: state, fetchJson, TYPE_CONFIG, TYPE_SET,
   spriteFromPokemonId, setSpriteSafe, PLACEHOLDER, normalizeName.
   Load after app.js.
   ============================================================================ */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const modal = $("pokedexModal");
  if (!modal || typeof fetchJson !== "function") return;

  const el = {
    close: $("dexClose"),
    body: $("dexBody"),
    ring: $("dexRingFill"),
    ringPct: $("dexRingPct"),
    caught: $("dexCaught"),
    total: $("dexTotal"),
    streak: $("dexStreak"),
    streakSub: $("dexStreakSub"),
    next: $("dexNext"),
    nextSub: $("dexNextSub"),
    nextBar: $("dexNextBar"),
    latest: $("dexLatest"),
    latestImg: $("dexLatestImg"),
    latestName: $("dexLatestName"),
    types: $("dexTypes"),
    tabs: $("dexTabs"),
    toolbar: $("dexToolbar"),
    search: $("dexSearch"),
    status: $("dexStatus"),
    gen: $("dexGen"),
    showing: $("dexShowing"),
    grid: $("dexGrid"),
    log: $("dexLog"),
    progress: $("dexProgress"),
    empty: $("dexEmpty"),
    banner: $("dexBanner"),
    detail: $("dexDetail"),
    detailInner: $("dexDetailInner"),
    layout: $("dexLayout"),
    shinyStat: $("dexShinyStat"),
    sealBar: $("dexSealBar"),
  };

  const RING_C = 2 * Math.PI * 34;               // matches r=34 in the SVG
  const MILESTONES = [1, 10, 25, 50, 100, 150, 200, 250, 300, 400, 500, 750, 1000];
  const MILESTONE_KEY = "pokedokudoku_pokedex_milestone_seen";   // same key the old Palette used
  const SEEN_KEY = "pdd.dexSeen";
  const RECENT_MS = 48 * 60 * 60 * 1000;
  const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)");

  const S = {
    entries: [],
    byKey: new Map(),
    cards: new Map(),
    caughtCount: 0,
    totalCount: 0,
    trainer: null,
    fetchedAt: 0,
    inflight: null,
    view: "dex",
    status: "all",
    type: null,
    gen: "",
    search: "",
    searchN: "",
    selected: null,
    detailCache: new Map(),
    revealQueue: new Set(),
    lastFocus: null,
  };

  // ══════════════════════════════════════════════════════════════════════
  // Data
  // ══════════════════════════════════════════════════════════════════════
  function fetchDex() {
    if (S.inflight) return S.inflight;
    S.inflight = fetchJson(`${state.apiBase}/pokedex`)
      .then((data) => { ingest(data); S.fetchedAt = Date.now(); return data; })
      .finally(() => { S.inflight = null; });
    return S.inflight;
  }

  function ingest(data) {
    const prev = S.byKey;
    S.entries = data.entries || [];
    S.entries.forEach((e) => { e._n = normalizeName(e.display_name || ""); });   // search key, computed once
    S.byKey = new Map(S.entries.map((e) => [e.key, e]));
    S.caughtCount = data.caught_count ?? S.entries.filter((e) => e.caught).length;
    S.totalCount = data.total_count ?? S.entries.length;
    S.trainer = data.trainer || null;
    if (S.trainer) window.PDD_SHINY?.saveTrainer(S.trainer);

    // Anything caught that the player hasn't seen in the Pokédex yet gets the
    // reveal animation. First visit ever: nothing is "new", just record.
    const seenRaw = readJSON(SEEN_KEY);
    const caughtKeys = S.entries.filter((e) => e.caught).map((e) => e.key);
    if (Array.isArray(seenRaw)) {
      const seen = new Set(seenRaw);
      caughtKeys.forEach((k) => { if (!seen.has(k)) S.revealQueue.add(k); });
    }

    // Detail payloads for anything whose catch changed are stale now.
    S.entries.forEach((e) => {
      const p = prev.get(e.key);
      if (p && p.caught !== e.caught) S.detailCache.delete(e.key);
    });

    if (S.cards.size) {
      S.entries.forEach((e) => {
        const p = prev.get(e.key);
        if (!p || p.caught !== e.caught || p.is_shiny !== e.is_shiny || p.first_caught !== e.first_caught) {
          const card = S.cards.get(e.key);
          if (card) paintCard(card, e);
        }
      });
    }
  }

  function markSeen() {
    const keys = S.entries.filter((e) => e.caught).map((e) => e.key);
    writeJSON(SEEN_KEY, keys);
  }

  // Warm the cache once the page has settled, so the first open is instant.
  const idle = window.requestIdleCallback || ((fn) => setTimeout(fn, 1200));
  setTimeout(() => idle(() => { fetchDex().catch(() => {}); }), 2500);

  // ══════════════════════════════════════════════════════════════════════
  // Open / close
  // ══════════════════════════════════════════════════════════════════════
  async function open() {
    S.lastFocus = document.activeElement;
    modal.classList.add("open");
    document.documentElement.classList.add("dex-lock");
    requestAnimationFrame(() => el.close?.focus({ preventScroll: true }));

    if (S.entries.length) {
      render();
      // Stale-while-revalidate: refresh quietly if it's been more than a few seconds.
      if (Date.now() - S.fetchedAt > 5000) {
        fetchDex().then(() => { renderStats(); renderTypes(); applyFilters(); renderSideViews(); celebrate(); })
          .catch(() => {});
      } else {
        celebrate();
      }
      return;
    }

    showSkeleton();
    try {
      await fetchDex();
      render();
      celebrate();
    } catch (err) {
      clearGrid();
      showEmpty("Couldn't load your Pokédex. Make sure the API is running, then try again.");
    }
  }

  function close() {
    modal.classList.remove("open");
    document.documentElement.classList.remove("dex-lock");
    closeDetail();
    S.lastFocus?.focus?.({ preventScroll: true });
  }

  // A milestone banner (with confetti) wins over the "new entries" banner;
  // the reveal animation on the cards plays either way.
  function celebrate() {
    const milestone = checkMilestone();
    runReveals(milestone);
    markSeen();
  }

  document.getElementById("openPokedexBtn")?.addEventListener("click", open);
  el.close?.addEventListener("click", close);
  modal.addEventListener("click", (e) => { if (e.target === modal) close(); });
  document.addEventListener("keydown", (e) => {
    if (!modal.classList.contains("open")) return;
    if (e.key === "Escape") {
      e.stopPropagation();
      if (S.selected) closeDetail(); else close();
    } else if (S.selected && (e.key === "ArrowRight" || e.key === "ArrowLeft")
      && !/input|select|textarea/i.test(document.activeElement?.tagName || "")) {
      e.preventDefault();
      stepDetail(e.key === "ArrowRight" ? 1 : -1);
    }
  });

  // ══════════════════════════════════════════════════════════════════════
  // Rendering
  // ══════════════════════════════════════════════════════════════════════
  function render() {
    renderStats();
    renderTypes();
    buildGen();
    applyFilters();
    renderSideViews();
  }

  function renderSideViews() {
    if (S.view === "log") renderLog();
    if (S.view === "progress") renderProgress();
  }

  // ── Stats strip ─────────────────────────────────────────────────────────
  function renderStats() {
    const { caughtCount: c, totalCount: t } = S;
    const pct = t ? (c / t) * 100 : 0;
    el.caught.textContent = String(c);
    el.total.textContent = String(t);
    el.ringPct.textContent = pct > 0 && pct < 1 ? "<1%" : `${Math.round(pct)}%`;
    el.ring.style.strokeDasharray = String(RING_C);
    // Start empty, then fill — the transition in CSS animates it.
    el.ring.style.strokeDashoffset = String(RING_C);
    requestAnimationFrame(() => requestAnimationFrame(() => {
      el.ring.style.strokeDashoffset = String(RING_C * (1 - Math.min(1, pct / 100)));
    }));

    // The server counts every day you played (not just days with a new
    // catch); fall back to the old estimate if it isn't available.
    const streak = S.trainer ? S.trainer.streak : computeStreak(S.entries);
    el.streak.textContent = streak ? `${streak} day${streak === 1 ? "" : "s"}` : "—";
    el.streakSub.textContent = streak
      ? (playedToday() ? "Streak alive today" : "Play today to keep it")
      : "Catch one today to start";
    el.streak.closest(".dx-stat").classList.toggle("hot", streak >= 3);

    const next = MILESTONES.find((m) => m > c) ?? (t > c ? t : null);
    const prevM = [...MILESTONES].reverse().find((m) => m <= c) ?? 0;
    if (next) {
      el.next.textContent = `${next - c} to go`;
      el.nextSub.textContent = next === t ? "to a complete Pokédex" : `to ${next} caught`;
      el.nextBar.style.width = `${Math.max(4, ((c - prevM) / (next - prevM)) * 100)}%`;
    } else {
      el.next.textContent = "Complete!";
      el.nextSub.textContent = "Every entry registered";
      el.nextBar.style.width = "100%";
    }

    renderShinyStat();

    const latest = S.entries.filter((e) => e.caught && e.first_caught)
      .sort((a, b) => new Date(b.first_caught) - new Date(a.first_caught))[0];
    el.latest.hidden = !latest;
    if (latest) {
      setSpriteSafe(el.latestImg, sprite(latest, latest.is_shiny));
      el.latestImg.alt = "";
      el.latestName.textContent = latest.display_name;
      el.latest.onclick = () => openDetail(latest.key);
    }
  }

  // ── Type strip ──────────────────────────────────────────────────────────
  function renderTypes() {
    const counts = {};
    S.entries.forEach((e) => [e.type1, e.type2].forEach((t) => {
      if (!t || !TYPE_SET.has(t)) return;
      counts[t] ||= [0, 0];
      counts[t][1] += 1;
      if (e.caught) counts[t][0] += 1;
    }));
    el.types.replaceChildren(...Object.entries(TYPE_CONFIG).map(([key, cfg], i) => {
      const [c, t] = counts[key] || [0, 0];
      const b = document.createElement("button");
      b.type = "button";
      b.className = "dx-type" + (S.type === key ? " on" : "") + (t && c === t ? " full" : "");
      b.style.setProperty("--tc", cfg.bg);
      b.style.setProperty("--i", String(i));
      b.setAttribute("aria-pressed", String(S.type === key));
      b.title = `${cap(key)}: ${c} of ${t} caught`;
      b.innerHTML = `<span class="dx-type-fill" style="--f:${t ? (c / t) * 100 : 0}%"></span>`
        + `<span class="dx-type-icon" aria-hidden="true"></span><span class="dx-type-name"></span>`;
      b.querySelector(".dx-type-icon").textContent = cfg.icon;
      b.querySelector(".dx-type-name").textContent = cap(key);
      b.addEventListener("click", () => {
        S.type = S.type === key ? null : key;
        renderTypes();
        applyFilters();
        renderSideViews();
      });
      return b;
    }));
  }

  function buildGen() {
    const gens = [...new Set(S.entries.map((e) => e.gen).filter((g) => g != null))].sort((a, b) => a - b);
    const cur = el.gen.value;
    el.gen.innerHTML = `<option value="">All gens</option>` + gens.map((g) => `<option value="${g}">Gen ${g}</option>`).join("");
    el.gen.value = gens.map(String).includes(cur) ? cur : "";
  }

  // ── Cards ───────────────────────────────────────────────────────────────
  const mounted = new Map();   // key -> card currently in the virtual grid

  function showSkeleton() {
    const frag = document.createDocumentFragment();
    for (let i = 0; i < 24; i += 1) {
      const s = document.createElement("div");
      s.className = "dx-card dx-skel";
      s.style.setProperty("--i", String(i));
      frag.appendChild(s);
    }
    clearGrid();
    el.grid.style.height = "";
    el.grid.classList.add("is-skel");     // skeleton uses a plain CSS grid
    el.grid.replaceChildren(frag);
    hideEmpty();
  }

  // Empty the grid and forget which cards were mounted.
  function clearGrid() {
    el.grid.replaceChildren();
    el.grid.classList.remove("is-skel");
    mounted.clear();
    V.r0 = -1; V.r1 = -1;
  }

  // ── Virtual grid ──────────────────────────────────────────────────────
  // Only the rows on screen (plus a few either side) are in the page. Cards
  // are created the first time they're needed and reused after that.
  // Each card is absolutely positioned at its slot, so scrolling one row only
  // adds the cards coming into view and removes the ones leaving; the cards
  // in between are never touched (no re-layout of the whole grid).
  const V = { list: [], cols: 1, rowH: 1, cardH: 1, gap: 0, colW: 1, r0: -1, r1: -1, ver: 0, drawn: -1 };
  let firstPaint = true;

  function getCard(e) {
    let card = S.cards.get(e.key);
    if (!card) {
      card = makeCard(e, firstPaint ? S.cards.size : 999);
      S.cards.set(e.key, card);
    }
    return card;
  }

  function measure() {
    const cs = getComputedStyle(el.grid);
    const min = parseFloat(cs.getPropertyValue("--card-min")) || 112;
    V.cardH = parseFloat(cs.getPropertyValue("--card-h")) || 140;
    V.gap = parseFloat(cs.getPropertyValue("--gap")) || 8;
    const w = el.grid.clientWidth;
    V.cols = Math.max(1, Math.floor((w + V.gap) / (min + V.gap)));
    V.rowH = V.cardH + V.gap;
    V.colW = (w - V.gap * (V.cols - 1)) / V.cols;
    el.grid.style.setProperty("--cols", String(V.cols));
    el.grid.style.setProperty("--cw", `${V.colW}px`);
  }

  function gridTop() {
    // Distance from the top of the scrolling area to the top of the grid.
    return el.grid.getBoundingClientRect().top - el.body.getBoundingClientRect().top + el.body.scrollTop;
  }

  function draw(force) {
    if (S.view !== "dex" || el.grid.hidden || !el.grid.clientWidth) return;
    // Read layout first, then write, so the browser never has to re-run
    // layout in the middle of a scroll frame.
    const top = el.body.scrollTop - gridTop();
    const viewH = el.body.clientHeight;
    const rows = Math.ceil(V.list.length / V.cols);
    const r0 = Math.max(0, Math.floor(top / V.rowH) - 3);
    const r1 = Math.min(rows, Math.ceil((top + viewH) / V.rowH) + 3);
    if (!force && r0 === V.r0 && r1 === V.r1 && V.drawn === V.ver) return;
    V.r0 = r0; V.r1 = r1; V.drawn = V.ver;
    el.grid.style.height = rows ? `${rows * V.rowH - V.gap}px` : "0px";

    const from = r0 * V.cols;
    const to = Math.min(V.list.length, r1 * V.cols);
    const want = new Set();
    for (let i = from; i < to; i += 1) want.add(V.list[i].key);
    mounted.forEach((card, key) => {
      if (!want.has(key)) { card.remove(); mounted.delete(key); }
    });
    const add = document.createDocumentFragment();
    for (let i = from; i < to; i += 1) {
      const e = V.list[i];
      const card = getCard(e);
      // Slot position; only rewritten when it actually changes (a filter
      // or a resize), never for cards that just stay on screen.
      const x = (i % V.cols) * (V.colW + V.gap);
      const y = Math.floor(i / V.cols) * V.rowH;
      if (card.__x !== x || card.__y !== y) {
        card.style.left = `${x}px`;
        card.style.top = `${y}px`;
        card.__x = x; card.__y = y;
      }
      if (!mounted.has(e.key)) { mounted.set(e.key, card); add.appendChild(card); }
    }
    if (add.childNodes.length) el.grid.appendChild(add);
    firstPaint = false;
  }

  // Build the rest of the cards while the browser is idle, so scrolling far
  // down never has to create them mid-scroll.
  let warming = false;
  function warmCards() {
    if (warming) return;
    warming = true;
    let i = 0;
    const step = (deadline) => {
      while (i < S.entries.length && (!deadline || deadline.timeRemaining() > 4)) {
        getCard(S.entries[i]);
        i += 1;
        if (!deadline && i % 120 === 0) break;
      }
      if (i < S.entries.length) idle(step); else warming = false;
    };
    idle(step);
  }

  // While scrolling, the always-on decorative animations (glows, shimmer,
  // the floating hero) pause: repainting them every frame was most of the
  // scroll cost. They pick up again a moment after you stop.
  let scrollRaf = 0;
  let scrollIdle = 0;
  el.body.addEventListener("scroll", () => {
    const root = document.getElementById("pokedexModal");
    root?.classList.add("dx-scrolling");
    clearTimeout(scrollIdle);
    scrollIdle = setTimeout(() => root?.classList.remove("dx-scrolling"), 160);
    if (scrollRaf) return;
    scrollRaf = requestAnimationFrame(() => { scrollRaf = 0; draw(false); });
  }, { passive: true });
  if (window.ResizeObserver) {
    new ResizeObserver(() => { if (S.view === "dex" && S.entries.length) { measure(); draw(true); } }).observe(el.grid);
  }

  function scrollToKey(key, smooth) {
    const i = V.list.findIndex((e) => e.key === key);
    if (i < 0) return;
    const y = gridTop() + Math.floor(i / V.cols) * V.rowH - (el.body.clientHeight - V.cardH) / 2;
    const target = Math.max(0, y);
    const visible = Math.abs(el.body.scrollTop - target) < el.body.clientHeight / 2 - V.cardH;
    if (visible) return;
    el.body.scrollTo({ top: target, behavior: smooth && !REDUCED.matches ? "smooth" : "auto" });
    if (!smooth || REDUCED.matches) draw(true);
  }

  function makeCard(entry, i) {
    const b = document.createElement("button");
    b.type = "button";
    b.dataset.key = entry.key;
    // Only the first screenful staggers in; the rest appear as you scroll.
    if (i < 36) b.style.setProperty("--i", String(i));
    b.innerHTML =
      `<span class="dx-num"></span>` +
      `<span class="dx-art"><img alt="" loading="lazy" decoding="async"></span>` +
      `<span class="dx-name"></span>` +
      `<span class="dx-dots"></span>`;
    b.addEventListener("click", () => openDetail(entry.key));
    // Cards are detached and re-attached as you scroll; drop one-shot
    // animation classes once they've played so they don't replay.
    b.addEventListener("animationend", () => b.classList.remove("enter", "reveal"));
    paintCard(b, entry, i < 36);
    return b;
  }

  function paintCard(card, e, first = false) {
    // In the Shiny filter every Shiny Dex entry shows in full, even ones not
    // caught in the current run (shinies survive a Pokédex restart).
    const caught = !!e.caught || (S.status === "shiny" && e.is_shiny);
    card.className = "dx-card " + (caught ? "caught" : "missing")
      + (caught && e.is_shiny ? " shiny" : "")
      + (!caught && e.is_shiny ? " shiny-kept" : "")
      + (caught && (e.is_legendary || e.is_mythical) ? " rare" : "")
      + (caught && isRecent(e.first_caught) ? " recent" : "")
      + (first ? " enter" : "")
      + (S.selected === e.key ? " selected" : "")
      + (card.classList.contains("fresh") ? " fresh" : "");
    const t1 = TYPE_CONFIG[e.type1]?.bg || "#6b4a66";
    const t2 = TYPE_CONFIG[e.type2]?.bg || t1;
    card.style.setProperty("--t1", t1);
    card.style.setProperty("--t2", t2);
    card.setAttribute("aria-label", caught ? `${e.display_name}, caught` : `Unknown Pokémon ${dexNo(e) || ""}, not caught`);

    card.querySelector(".dx-num").textContent = dexNo(e);
    const img = card.querySelector("img");
    const src = sprite(e, caught && e.is_shiny);
    if (img.dataset.src !== src) { img.dataset.src = src; setSpriteSafe(img, src); }
    card.querySelector(".dx-name").textContent = caught ? e.display_name : "???";
    const dots = card.querySelector(".dx-dots");
    dots.replaceChildren(...[e.type1, e.type2].filter((t) => t && TYPE_SET.has(t)).map((t) => {
      const d = document.createElement("i");
      d.style.background = TYPE_CONFIG[t].bg;
      return d;
    }));
  }

  // Filters toggle visibility only; nothing is rebuilt.
  function matches(e, forLog) {
    if (!forLog) {
      if (S.status === "caught" && !e.caught) return false;
      if (S.status === "missing" && e.caught) return false;
      if (S.status === "shiny" && !e.is_shiny) return false;
    } else if (!e.caught) return false;
    if (S.type && e.type1 !== S.type && e.type2 !== S.type) return false;
    if (S.gen && String(e.gen) !== S.gen) return false;
    if (S.search) {
      // Uncaught names stay secret: only caught names (or a dex number) match.
      const q = S.search;
      const num = q.replace(/^#/, "");
      const byNum = /^\d+$/.test(num) && e.pokemon_id && String(e.pokemon_id) === String(+num);
      const byName = (e.caught || e.is_shiny) && e._n.includes(S.searchN);
      if (!byNum && !byName) return false;
    }
    return true;
  }

  function applyFilters() {
    if (S.view !== "dex") return;
    if (!S.entries.length) return;
    V.list = S.entries.filter((e) => matches(e, false));
    V.ver += 1;
    if (el.grid.classList.contains("is-skel")) clearGrid();
    measure();
    draw(true);
    warmCards();
    const shown = V.list.length;
    if (!shown && S.status === "shiny") {
      showEmpty("No shinies yet. Every correct Daily Board pick has a chance; they're rare, so keep playing!");
    }
    const filtered = S.status !== "all" || S.type || S.gen || S.search;
    el.showing.textContent = filtered ? `${shown} shown` : `${S.entries.length} entries`;
    if (shown) hideEmpty();
    else if (S.status === "shiny") { /* message set above */ }
    else if (!S.caughtCount && S.status === "caught") showEmpty("Nothing caught yet. Solve today's Daily Board to register your first Pokémon.");
    else showEmpty(S.search && !/^#?\d+$/.test(S.search)
      ? "No caught Pokémon match that name. Uncaught names stay hidden until you catch them."
      : "No Pokémon match these filters.");
  }

  // ── Reveal animation for new catches ────────────────────────────────────
  function runReveals(quiet) {
    if (!S.revealQueue.size || S.view !== "dex") return;
    const keys = [...S.revealQueue];
    S.revealQueue.clear();
    const cards = keys.map((k) => S.byKey.get(k)).filter(Boolean).map(getCard);
    if (!cards.length) return;
    // Bring the first one into view, then flip them one after another.
    setTimeout(() => {
      scrollToKey(keys[0], true);
      cards.slice(0, 12).forEach((c, n) => setTimeout(() => {
        c.classList.remove("reveal");
        void c.offsetWidth;
        c.classList.add("reveal", "fresh");
      }, 350 + n * 260));
    }, 250);
    if (!quiet) banner(`${keys.length} new entr${keys.length === 1 ? "y" : "ies"} registered!`, "new");
  }

  // ── Milestones ──────────────────────────────────────────────────────────
  function checkMilestone() {
    const last = Number(localStorage.getItem(MILESTONE_KEY) || "0");
    const c = S.caughtCount, t = S.totalCount;
    if (t > 0 && c === t && last < t) {
      banner(`Pokédex complete! All ${t} registered.`, "party");
      localStorage.setItem(MILESTONE_KEY, String(t));
      return true;
    }
    const hit = MILESTONES.filter((m) => m <= c && m > last).pop();
    if (hit) {
      banner(hit === 1 ? "First catch registered. Welcome to your Pokédex!" : `Milestone: ${hit} Pokémon caught!`, "party");
      localStorage.setItem(MILESTONE_KEY, String(hit));
      return true;
    }
    return false;
  }

  let bannerTimer = 0;
  function banner(text, kind) {
    const b = el.banner;
    b.className = `dx-banner ${kind}`;
    b.replaceChildren();
    const span = document.createElement("span");
    span.textContent = text;
    b.appendChild(span);
    if (kind === "party" && !REDUCED.matches) {
      const colors = ["#e0306f", "#f59e0b", "#22c7de", "#8b5cf6", "#10d98a", "#fff"];
      for (let n = 0; n < 18; n += 1) {
        const c = document.createElement("i");
        c.className = "dx-confetti";
        c.style.setProperty("--x", `${(Math.random() * 2 - 1) * 160}px`);
        c.style.setProperty("--y", `${-40 - Math.random() * 90}px`);
        c.style.setProperty("--r", `${Math.random() * 540 - 270}deg`);
        c.style.setProperty("--d", `${Math.random() * 120}ms`);
        c.style.background = colors[n % colors.length];
        b.appendChild(c);
      }
    }
    b.hidden = false;
    clearTimeout(bannerTimer);
    bannerTimer = setTimeout(() => { b.hidden = true; }, 4200);
  }

  // ── Catch log: grouped by the day it was caught ─────────────────────────
  function renderLog() {
    const rows = S.entries.filter((e) => matches(e, true))
      .sort((a, b) => new Date(b.first_caught) - new Date(a.first_caught));
    if (!rows.length) {
      el.log.replaceChildren();
      showEmpty(S.caughtCount
        ? "No catches match these filters."
        : "Your log is empty. Solve today's Daily Board to start it.");
      return;
    }
    hideEmpty();
    const groups = new Map();
    rows.forEach((e) => {
      const day = (e.first_caught || "").slice(0, 10) || "unknown";
      if (!groups.has(day)) groups.set(day, []);
      groups.get(day).push(e);
    });
    const frag = document.createDocumentFragment();
    let gi = 0;
    groups.forEach((list, day) => {
      const sec = document.createElement("section");
      sec.className = "dx-day";
      sec.style.setProperty("--i", String(Math.min(gi++, 12)));
      const h = document.createElement("h3");
      h.innerHTML = `<span class="dx-day-date"></span><span class="dx-day-count"></span>`;
      h.firstChild.textContent = prettyDay(day);
      h.lastChild.textContent = `${list.length} caught`;
      sec.appendChild(h);
      const strip = document.createElement("div");
      strip.className = "dx-day-list";
      list.forEach((e) => {
        const r = document.createElement("button");
        r.type = "button";
        r.className = "dx-logrow" + (e.is_shiny ? " shiny" : "");
        r.style.setProperty("--t1", TYPE_CONFIG[e.type1]?.bg || "#6b4a66");
        r.innerHTML = `<img alt="" loading="lazy" decoding="async"><span class="dx-logmain">`
          + `<span class="dx-logname"></span><span class="dx-logrules"></span></span>`;
        setSpriteSafe(r.querySelector("img"), sprite(e, e.is_shiny));
        r.querySelector(".dx-logname").textContent = e.display_name + (e.is_legendary || e.is_mythical ? " ✦" : "");
        const rules = r.querySelector(".dx-logrules");
        if (e.row_rule_label || e.col_rule_label) {
          rules.textContent = `${e.row_rule_label || "?"} × ${e.col_rule_label || "?"}`;
        }
        r.addEventListener("click", () => openDetail(e.key));
        strip.appendChild(r);
      });
      sec.appendChild(strip);
      frag.appendChild(sec);
    });
    el.log.replaceChildren(frag);
  }

  // ── Progress: by generation, type and rarity ────────────────────────────
  function renderProgress() {
    hideEmpty();
    const bar = (label, c, t, color, extra = "") => {
      const pct = t ? (c / t) * 100 : 0;
      return `<div class="dx-bar${c === t && t ? " full" : ""}" style="--bc:${color};--w:${pct}%">`
        + `<span class="dx-bar-label">${label}${extra}</span>`
        + `<span class="dx-bar-track"><span class="dx-bar-fill"></span></span>`
        + `<span class="dx-bar-num">${c}<small>/${t}</small></span></div>`;
    };
    const tally = (pred) => {
      let c = 0, t = 0;
      S.entries.forEach((e) => { if (pred(e)) { t += 1; if (e.caught) c += 1; } });
      return [c, t];
    };

    const gens = [...new Set(S.entries.map((e) => e.gen).filter((g) => g != null))].sort((a, b) => a - b);
    const genHtml = gens.map((g) => { const [c, t] = tally((e) => e.gen === g); return bar(`Gen ${g}`, c, t, "#e0306f"); }).join("");
    const typeHtml = Object.entries(TYPE_CONFIG).map(([k, cfg]) => {
      const [c, t] = tally((e) => e.type1 === k || e.type2 === k);
      return bar(cap(k), c, t, cfg.bg, ` <span class="dx-bar-icon">${cfg.icon}</span>`);
    }).join("");
    const [lc, lt] = tally((e) => e.is_legendary);
    const [mc, mt] = tally((e) => e.is_mythical);
    const shinies = S.entries.filter((e) => e.caught && e.is_shiny).length;

    el.progress.innerHTML =
      shinyCardHtml()
      + `<section class="dx-pcard"><h3>Rarities</h3>`
      + bar("Legendary", lc, lt, "#f59e0b") + bar("Mythical", mc, mt, "#c084fc")
      + (shinies ? `<p class="dx-shiny-note">✨ ${shinies} shiny catch${shinies === 1 ? "" : "es"}</p>` : "")
      + `</section>`
      + `<section class="dx-pcard"><h3>By generation</h3>${genHtml}</section>`
      + `<section class="dx-pcard dx-pcard-wide"><h3>By type</h3><div class="dx-bar-cols">${typeHtml}</div></section>`;
    el.progress.querySelector(".dx-seal-btn")?.addEventListener("click", confirmSeal);
    // Animate the bars from empty.
    el.progress.classList.remove("grow");
    requestAnimationFrame(() => requestAnimationFrame(() => el.progress.classList.add("grow")));
  }

  // ── Tabs + toolbar ──────────────────────────────────────────────────────
  el.tabs.querySelectorAll("[data-view]").forEach((b) => b.addEventListener("click", () => {
    S.view = b.dataset.view;
    el.tabs.querySelectorAll("[data-view]").forEach((x) => x.setAttribute("aria-selected", String(x === b)));
    el.body.dataset.view = S.view;
    el.grid.hidden = S.view !== "dex";
    el.log.hidden = S.view !== "log";
    el.progress.hidden = S.view !== "progress";
    el.body.scrollTop = 0;
    if (S.view === "dex") applyFilters();
    else renderSideViews();
  }));

  let searchTimer = 0;
  el.search.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      S.search = el.search.value.trim();
      S.searchN = normalizeName(S.search);
      applyFilters();
      if (S.view === "log") renderLog();
    }, 90);
  });
  el.gen.addEventListener("change", () => { S.gen = el.gen.value; applyFilters(); renderSideViews(); });
  el.status.querySelectorAll("[data-status]").forEach((b) => b.addEventListener("click", () => {
    const wasShiny = S.status === "shiny";
    S.status = b.dataset.status;
    // Shiny view reveals kept shinies; repaint those cards either way.
    if (wasShiny !== (S.status === "shiny")) {
      S.entries.forEach((e) => { if (e.is_shiny && !e.caught) { const c = S.cards.get(e.key); if (c) paintCard(c, e); } });
    }
    el.status.querySelectorAll("[data-status]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    applyFilters();
  }));

  // ══════════════════════════════════════════════════════════════════════
  // Detail panel
  // ══════════════════════════════════════════════════════════════════════
  async function openDetail(key) {
    const e = S.byKey.get(key);
    if (!e) return;
    S.selected = key;
    document.querySelectorAll(".dx-card.selected").forEach((c) => c.classList.remove("selected"));
    S.cards.get(key)?.classList.add("selected");
    el.layout.classList.add("detail-open");
    el.detail.setAttribute("aria-hidden", "false");
    el.detail.scrollTop = 0;

    if (!e.caught && !e.is_shiny) { el.detailInner.replaceChildren(teaser(e)); return; }

    const cached = S.detailCache.get(key);
    el.detailInner.replaceChildren(hero(cached || e, !cached));
    if (cached) { el.detailInner.appendChild(sections(cached)); return; }
    try {
      const d = await fetchJson(`${state.apiBase}/pokedex/${encodeURIComponent(key)}`);
      S.detailCache.set(key, d);
      if (S.selected !== key) return;          // user moved on while it loaded
      el.detailInner.replaceChildren(hero(d, false), sections(d));
    } catch (_) {
      if (S.selected !== key) return;
      const p = document.createElement("p");
      p.className = "dx-detail-err";
      p.textContent = "Couldn't load the details for this entry.";
      el.detailInner.appendChild(p);
    }
  }

  function closeDetail() {
    S.selected = null;
    document.querySelectorAll(".dx-card.selected").forEach((c) => c.classList.remove("selected"));
    el.layout.classList.remove("detail-open");
    el.detail.setAttribute("aria-hidden", "true");
  }

  // Pages through what's visible in the active view. From a caught entry it
  // only stops on caught ones; from a silhouette it walks every entry.
  function stepDetail(dir) {
    const cur = S.byKey.get(S.selected);
    const fromCaught = !!(cur?.caught || cur?.is_shiny);
    const list = S.view === "log"
      ? S.entries.filter((e) => matches(e, true)).sort((a, b) => new Date(b.first_caught) - new Date(a.first_caught))
      : S.entries.filter((e) => matches(e, false) && (!fromCaught || e.caught || e.is_shiny));
    if (!list.length) return;
    const i = list.findIndex((e) => e.key === S.selected);
    const next = list[(i + dir + list.length) % list.length];
    openDetail(next.key);
    if (S.view === "dex") scrollToKey(next.key, true);
  }

  function detailNav() {
    const nav = document.createElement("div");
    nav.className = "dx-dnav";
    nav.innerHTML = `<button type="button" class="dx-back">← Back</button>`
      + `<span class="dx-dnav-step"><button type="button" aria-label="Previous entry">‹</button>`
      + `<button type="button" aria-label="Next entry">›</button></span>`;
    nav.querySelector(".dx-back").addEventListener("click", closeDetail);
    const [p, n] = nav.querySelectorAll(".dx-dnav-step button");
    p.addEventListener("click", () => stepDetail(-1));
    n.addEventListener("click", () => stepDetail(1));
    return nav;
  }

  function hero(d, loading) {
    const e = S.byKey.get(d.key) || d;
    const wrap = document.createElement("div");
    wrap.className = "dx-hero" + (e.is_shiny ? " shiny" : "");
    wrap.style.setProperty("--t1", TYPE_CONFIG[e.type1]?.bg || "#cc2255");
    wrap.style.setProperty("--t2", TYPE_CONFIG[e.type2]?.bg || TYPE_CONFIG[e.type1]?.bg || "#9b1fcc");
    wrap.appendChild(detailNav());
    wrap.insertAdjacentHTML("beforeend",
      `<div class="dx-stage"><span class="dx-stage-ring"></span><img class="dx-hero-img" alt=""></div>`
      + `<p class="dx-hero-num"></p><h3 class="dx-hero-name"></h3>`
      + `<div class="dx-hero-tags"></div><p class="dx-hero-caught"></p>`);
    const img = wrap.querySelector(".dx-hero-img");
    setSpriteSafe(img, sprite(e, e.is_shiny));
    img.alt = e.is_shiny ? `Shiny ${e.display_name}` : e.display_name;
    if (e.is_shiny) {
      // Flip between the shiny and normal colours.
      const flip = document.createElement("button");
      flip.type = "button";
      flip.className = "dx-shiny-flip on";
      flip.setAttribute("aria-pressed", "true");
      flip.textContent = "✨ Shiny";
      flip.addEventListener("click", () => {
        const on = flip.getAttribute("aria-pressed") !== "true";
        flip.setAttribute("aria-pressed", String(on));
        flip.classList.toggle("on", on);
        flip.textContent = on ? "✨ Shiny" : "Normal";
        wrap.classList.toggle("shiny", on);
        img.classList.remove("swap");
        void img.offsetWidth;
        img.classList.add("swap");
        setSpriteSafe(img, sprite(e, on));
      });
      wrap.querySelector(".dx-stage").appendChild(flip);
    }
    wrap.querySelector(".dx-hero-num").textContent = dexNo(e);
    wrap.querySelector(".dx-hero-name").textContent = e.display_name;
    const tags = wrap.querySelector(".dx-hero-tags");
    [e.type1, e.type2].filter((t) => t && TYPE_SET.has(t)).forEach((t) => {
      const s = document.createElement("span");
      s.className = "dx-tag type";
      s.style.background = TYPE_CONFIG[t].bg;
      s.style.color = TYPE_CONFIG[t].fg;
      s.textContent = `${TYPE_CONFIG[t].icon} ${cap(t)}`;
      tags.appendChild(s);
    });
    if (e.gen != null) tags.insertAdjacentHTML("beforeend", `<span class="dx-tag">Gen ${e.gen}</span>`);
    if (e.is_legendary) tags.insertAdjacentHTML("beforeend", `<span class="dx-tag gold">✦ Legendary</span>`);
    if (e.is_mythical) tags.insertAdjacentHTML("beforeend", `<span class="dx-tag myth">✦ Mythical</span>`);
    if (e.is_shiny) tags.insertAdjacentHTML("beforeend", `<span class="dx-tag shiny">✨ Shiny Dex</span>`);

    const caught = wrap.querySelector(".dx-hero-caught");
    if (!e.caught && e.is_shiny) {
      caught.textContent = `Shiny found ${fmtDate(e.shiny_first_seen)}. Catch it again to re-register it this run.`;
    } else if (d.first_caught) {
      const date = new Date(d.first_caught).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
      caught.textContent = `Caught ${date}`;
      if (d.row_rule_label || d.col_rule_label) {
        const how = document.createElement("span");
        how.className = "dx-how";
        how.textContent = `${d.row_rule_label || "?"} × ${d.col_rule_label || "?"}`;
        caught.append(" as ", how);
      }
    }
    if (loading) wrap.insertAdjacentHTML("beforeend", `<div class="dx-detail-loading"><i></i><i></i><i></i></div>`);
    return wrap;
  }

  function sections(d) {
    const frag = document.createDocumentFragment();
    frag.appendChild(section("General", "general", d.general_categories || [], 99));
    if ((d.ability_categories || []).length) frag.appendChild(section("Abilities", "abilities", d.ability_categories, 99));
    if ((d.move_categories || []).length) frag.appendChild(section("Moves", "moves", d.move_categories, 0));
    return frag;
  }

  // Lockable categories (things a board can ask for) come first, confirmed
  // ones lit. Purely informational ones fold away behind a toggle so the
  // panel doesn't turn into a wall of move names.
  function section(title, kind, cats, infoInline) {
    const lock = cats.filter((c) => c.lockable);
    const info = cats.filter((c) => !c.lockable);
    const got = lock.filter((c) => c.confirmed).length;
    const sec = document.createElement("section");
    sec.className = `dx-sec ${kind}`;
    sec.innerHTML = `<header><h4></h4><span class="dx-sec-count"></span></header>`
      + (lock.length ? `<span class="dx-sec-bar"><span style="--w:${(got / lock.length) * 100}%"></span></span>` : "")
      + `<div class="dx-chips"></div>`;
    sec.querySelector("h4").textContent = title;
    sec.querySelector(".dx-sec-count").textContent = lock.length ? `${got} / ${lock.length} confirmed` : "";
    const box = sec.querySelector(".dx-chips");
    lock.sort((a, b) => Number(b.confirmed) - Number(a.confirmed)).forEach((c, i) => box.appendChild(chip(c, i)));

    const shownInfo = info.slice(0, infoInline);
    shownInfo.forEach((c, i) => box.appendChild(chip(c, lock.length + i)));
    const rest = info.slice(infoInline);
    if (rest.length) {
      const more = document.createElement("button");
      more.type = "button";
      more.className = "dx-more";
      more.textContent = `Show ${rest.length} other ${title.toLowerCase()}`;
      more.addEventListener("click", () => {
        rest.forEach((c, i) => box.insertBefore(chip(c, i), more));
        more.remove();
      });
      box.appendChild(more);
    }
    if (!lock.length && !info.length) box.innerHTML = `<span class="dx-none">Nothing here yet.</span>`;
    return sec;
  }

  function chip(c, i) {
    const s = document.createElement("span");
    s.style.setProperty("--i", String(Math.min(i, 24)));
    if (c.lockable && c.confirmed) {
      s.className = "dx-chip on";
      s.textContent = c.label;
    } else if (c.lockable) {
      // Real label keeps the width honest but is blurred past legibility,
      // and hidden from screen readers so it doesn't leak the answer.
      s.className = "dx-chip locked";
      s.innerHTML = `<span class="dx-lock" aria-hidden="true">🔒</span><span class="dx-blur" aria-hidden="true"></span><span class="sr-only">Locked category</span>`;
      s.querySelector(".dx-blur").textContent = c.label;
    } else {
      s.className = "dx-chip info";
      s.textContent = c.label;
    }
    return s;
  }

  function teaser(e) {
    const wrap = document.createElement("div");
    wrap.className = "dx-hero mystery";
    wrap.style.setProperty("--t1", TYPE_CONFIG[e.type1]?.bg || "#6b4a66");
    wrap.style.setProperty("--t2", TYPE_CONFIG[e.type2]?.bg || TYPE_CONFIG[e.type1]?.bg || "#6b4a66");
    wrap.appendChild(detailNav());
    wrap.insertAdjacentHTML("beforeend",
      `<div class="dx-stage"><span class="dx-stage-ring"></span><img class="dx-hero-img" alt="Unknown Pokémon"></div>`
      + `<p class="dx-hero-num"></p><h3 class="dx-hero-name">???</h3><div class="dx-hero-tags"></div>`
      + `<p class="dx-mystery-copy">Not registered yet. Use it as a correct answer on a Daily Board to add it to your Pokédex.</p>`);
    setSpriteSafe(wrap.querySelector(".dx-hero-img"), sprite(e));
    wrap.querySelector(".dx-hero-num").textContent = dexNo(e);
    const tags = wrap.querySelector(".dx-hero-tags");
    [e.type1, e.type2].filter((t) => t && TYPE_SET.has(t)).forEach((t) => {
      const s = document.createElement("span");
      s.className = "dx-tag type";
      s.style.background = TYPE_CONFIG[t].bg;
      s.style.color = TYPE_CONFIG[t].fg;
      s.textContent = `${TYPE_CONFIG[t].icon} ${cap(t)}`;
      tags.appendChild(s);
    });
    if (e.gen != null) tags.insertAdjacentHTML("beforeend", `<span class="dx-tag">Gen ${e.gen}</span>`);
    return wrap;
  }

  // ══════════════════════════════════════════════════════════════════════
  // Helpers
  // ══════════════════════════════════════════════════════════════════════
  function sprite(e, shiny = false) {
    if (!e.pokemon_id) return PLACEHOLDER;
    return shiny && window.PDD_SHINY ? window.PDD_SHINY.spriteUrl(e.pokemon_id) : spriteFromPokemonId(e.pokemon_id);
  }
  function fmtDate(iso) {
    return iso ? new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) : "";
  }
  // National Dex numbers run to 1025; higher ids are alternate forms.
  function dexNo(e) { return e.pokemon_id && e.pokemon_id <= 1025 ? `#${String(e.pokemon_id).padStart(3, "0")}` : "Form"; }
  function cap(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : ""; }
  function isRecent(iso) { return !!iso && Date.now() - new Date(iso).getTime() < RECENT_MS; }
  function showEmpty(t) { el.empty.textContent = t; el.empty.hidden = false; }
  function hideEmpty() { el.empty.hidden = true; }

  function ymd(d) {
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  }
  function playedToday() {
    const today = ymd(new Date());
    return S.entries.some((e) => e.caught && e.board_seed === today);
  }
  // Consecutive Daily Boards (by the board's own date) with at least one catch.
  function computeStreak(entries) {
    const days = new Set(entries.filter((e) => e.caught && e.board_seed).map((e) => e.board_seed));
    if (!days.size) return 0;
    const cur = new Date();
    if (!days.has(ymd(cur))) cur.setDate(cur.getDate() - 1);
    let n = 0;
    while (days.has(ymd(cur))) { n += 1; cur.setDate(cur.getDate() - 1); }
    return n;
  }
  function prettyDay(day) {
    const d = new Date(`${day}T12:00:00`);
    if (Number.isNaN(d.getTime())) return "Earlier";
    const today = ymd(new Date());
    const y = new Date(); y.setDate(y.getDate() - 1);
    if (day === today) return "Today";
    if (day === ymd(y)) return "Yesterday";
    return d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
  }
  function readJSON(k) { try { return JSON.parse(localStorage.getItem(k)); } catch (_) { return null; } }
  function writeJSON(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (_) { /* storage blocked */ } }

  // ══════════════════════════════════════════════════════════════════════
  // Shinies: odds stat, Shiny Charm card, sealing a complete Pokédex
  // ══════════════════════════════════════════════════════════════════════
  function charmGem(charms, big) {
    const t = window.PDD_SHINY ? window.PDD_SHINY.tier(charms) : { a: "#ccc", b: "#888", label: "" };
    return `<span class="charm-gem${charms ? "" : " none"}${big ? " big" : ""}" style="--ga:${t.a};--gb:${t.b}" aria-hidden="true"></span>`;
  }

  function renderShinyStat() {
    const box = el.shinyStat;
    if (!box) return;
    const t = S.trainer;
    if (!t) { box.hidden = true; return; }
    box.hidden = false;
    const tier = window.PDD_SHINY?.tier(t.charms);
    box.innerHTML = charmGem(t.charms)
      + `<div class="dx-stat-text"><strong>1 in ${t.odds.toLocaleString()}</strong>`
      + `<span>${t.charms ? `${tier.label} charm` : "Shiny odds"}${t.streak_bonus ? ` · streak +${t.streak_bonus}` : ""}</span></div>`;
    box.onclick = () => el.tabs.querySelector('[data-view="progress"]').click();
    el.sealBar.hidden = !t.complete;
  }

  function shinyCardHtml() {
    const t = S.trainer;
    if (!t) return "";
    const tier = window.PDD_SHINY?.tier(t.charms) || { label: "" };
    const charmRolls = 2 * Math.min(t.charms, t.max_charms);
    const nextCharm = t.charms < t.max_charms
      ? `Complete your Pokédex to earn ${t.charms ? "another" : "a"} charm: +2 rolls.`
      : "Charm bonus maxed. Shinies stay rare on purpose.";
    const streakLine = t.next_streak_days
      ? `${t.next_streak_days - t.streak} more day${t.next_streak_days - t.streak === 1 ? "" : "s"} of Daily play for +${t.next_streak_days >= 30 ? 2 : 1} roll${t.next_streak_days >= 30 ? "s" : ""}.`
      : "Top streak bonus active.";
    const history = (t.history || []).map((h) =>
      `<li>${charmGem(h.rank)}<span>Pokédex ${h.rank} sealed</span><small>${fmtDate(h.sealed_at)}</small></li>`).join("");
    return `<section class="dx-pcard dx-pcard-wide dx-shinycard">
      <div class="dx-shiny-head">
        ${charmGem(t.charms, true)}
        <div>
          <h3>${t.charms ? `${tier.label} Shiny Charm` : "Shiny odds"}</h3>
          <p class="dx-odds">1 in <b>${t.odds.toLocaleString()}</b> <small>per correct Daily pick</small></p>
        </div>
        <div class="dx-shiny-count"><b>${t.shiny_count}</b><small>in Shiny Dex</small></div>
      </div>
      <ul class="dx-rolls">
        <li><span>Base roll</span><b>1</b><small>1 in ${t.base_odds.toLocaleString()}, same as the games</small></li>
        <li><span>Shiny Charms ×${t.charms}</span><b>+${charmRolls}</b><small>${nextCharm}</small></li>
        <li><span>Daily streak · ${t.streak} day${t.streak === 1 ? "" : "s"}</span><b>+${t.streak_bonus}</b><small>${streakLine}</small></li>
      </ul>
      <p class="dx-shiny-note2">Draft and Shuffle boards roll at your charm odds too, and Reverse boards can show shiny sightings, but only Daily Board shinies are registered.</p>
      ${t.complete ? `<button type="button" class="dx-seal-btn">✦ Seal this Pokédex & earn a charm</button>` : ""}
      ${history ? `<ul class="dx-history">${history}</ul>` : ""}
    </section>`;
  }

  function confirmSeal() {
    const t = S.trainer;
    if (!t?.complete) return;
    const wrap = document.createElement("div");
    wrap.className = "dx-confirm";
    wrap.innerHTML = `<div class="dx-confirm-card" role="alertdialog" aria-labelledby="dxSealTitle">
        ${charmGem(t.charms + 1, true)}
        <h3 id="dxSealTitle">Seal Pokédex ${t.charms + 1}?</h3>
        <p>You'll earn the <b>${window.PDD_SHINY?.tier(t.charms + 1).label}</b> Shiny Charm (+2 shiny rolls), and your Pokédex starts fresh for the next run.</p>
        <p class="dx-keep">Kept: your Shiny Dex, streak and every confirmed category.</p>
        <div class="dx-confirm-actions">
          <button type="button" class="dx-cancel">Not yet</button>
          <button type="button" class="dx-go">Seal it</button>
        </div>
        <p class="dx-confirm-err" hidden></p>
      </div>`;
    el.body.closest(".dex-shell").appendChild(wrap);
    const go = wrap.querySelector(".dx-go");
    wrap.querySelector(".dx-cancel").addEventListener("click", () => wrap.remove());
    wrap.addEventListener("click", (ev) => { if (ev.target === wrap) wrap.remove(); });
    go.focus();
    go.addEventListener("click", async () => {
      go.disabled = true;
      go.textContent = "Sealing…";
      try {
        await fetchJson(`${state.apiBase}/pokedex/prestige`, { method: "POST" });
        wrap.querySelector(".dx-confirm-card").classList.add("sealed");
        S.detailCache.clear();
        await fetchDex();
        setTimeout(() => {
          wrap.remove();
          render();
          banner(`Pokédex sealed! ${window.PDD_SHINY?.tier(S.trainer?.charms || 1).label} Shiny Charm earned.`, "party");
        }, 900);
      } catch (err) {
        const e = wrap.querySelector(".dx-confirm-err");
        e.textContent = err.message || "Couldn't seal the Pokédex. Try again.";
        e.hidden = false;
        go.disabled = false;
        go.textContent = "Seal it";
      }
    });
  }

  el.sealBar?.querySelector("button")?.addEventListener("click", () => {
    el.tabs.querySelector('[data-view="progress"]').click();
    confirmSeal();
  });

  window.PDD_POKEDEX = {
    open,
    close,
    refresh: () => fetchDex().then(render),
    markStale: () => { S.fetchedAt = 0; },
  };
})();