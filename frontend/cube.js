/* ============================================================================
   cube.js — the 3×3×3 Cube page (cube.html, cube.css; server: cube_api.py).

   Two views of the same 27 squares:
     • The 3D view: three platforms stacked, Pokémon standing on them. Drag
       or use the turn buttons to look around it; tap a platform to open it.
     • The flat layer: the open platform as a 3×3 board with its column and
       row rules, plus the layer's own rule in the corner. This is where you
       play. Swipe across it (or press PageUp / PageDown) for the next layer.

   Fill:    put a Pokémon in every square that fits its column, row and layer.
   Reverse: all 27 are shown; name the rule each column, row and layer shares.

   Today's Cube keeps lives and answers on the server (a reload can't reset
   them). A practice Cube keeps them in this browser.
   ============================================================================ */
(function () {
  "use strict";

  const CDN = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon";
  const CLIENT_ID_KEY = "route81_client_id";          // same id as the rest of the site
  const PRACTICE_KEY = "route81.cube.practice";
  const PLACEHOLDER = "data:image/svg+xml;utf8," + encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 96 96"><rect x="36" y="36" width="24" height="24" fill="#3a3745"/></svg>');

  const TYPE_CONFIG = {
    normal: ["#888888", "#fff"], fire: ["#E84000", "#fff"], fighting: ["#B81414", "#fff"],
    water: ["#1B7FE8", "#fff"], ice: ["#20CCDD", "#222"], grass: ["#2DAA4C", "#fff"],
    bug: ["#7DA900", "#fff"], ground: ["#C47A00", "#fff"], rock: ["#A08045", "#fff"],
    flying: ["#91B2F5", "#222"], electric: ["#FFD700", "#222"], psychic: ["#E8205A", "#fff"],
    fairy: ["#E85FAA", "#fff"], poison: ["#8B2BAE", "#fff"], ghost: ["#5B3E7A", "#fff"],
    dragon: ["#3425C8", "#fff"], dark: ["#2E2B3A", "#eee"], steel: ["#6A8FAD", "#fff"],
  };
  const AXIS = ["Column", "Row", "Layer"];
  const LAYER = ["Top layer", "Middle layer", "Bottom layer"];
  const CELLS = [];
  for (let z = 0; z < 3; z++) for (let y = 0; y < 3; y++) for (let x = 0; x < 3; x++) CELLS.push([z, y, x]);

  const $ = (id) => document.getElementById(id);
  const ck = (z, y, x) => `${z},${y},${x}`;
  const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)");

  // ── State ────────────────────────────────────────────────────────────────
  const S = {
    kind: "fill",            // "fill" | "reverse"
    which: "daily",          // "daily" | "practice"
    layer: 0,
    sel: null,               // [z, y, x] being answered
    // Fill
    board: null,
    picks: new Map(),        // "z,y,x" -> {mon, hinted, shiny, pct}
    answers: new Map(),      // "z,y,x" -> {mon, count} once a Cube is over
    lives: 3, hints: 2, maxLives: 3, maxHints: 2,
    finished: false,
    // Reverse
    rev: null,               // server board
    revRun: null,
    revPractice: null,       // the practice board, kept for this visit
    guess: null,             // {axis, index, rule}
    catalog: null,
  };

  // ── Server ───────────────────────────────────────────────────────────────
  function clientId() {
    let id = null;
    try { id = localStorage.getItem(CLIENT_ID_KEY); } catch (_) { /* storage blocked */ }
    if (!id) {
      id = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
      try { localStorage.setItem(CLIENT_ID_KEY, id); } catch (_) { /* storage blocked */ }
    }
    return id;
  }

  async function api(path, body) {
    const opts = { headers: { "X-Client-Id": clientId() } };
    if (body !== undefined) {
      opts.method = "POST";
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    const res = await fetch(path, opts);
    if (!res.ok) {
      let detail = null;
      try { detail = (await res.json()).detail; } catch (_) { /* not JSON */ }
      throw new Error(typeof detail === "string" ? detail : `Request failed (${res.status})`);
    }
    return res.json();
  }

  function store(key, val) {
    try { val === null ? localStorage.removeItem(key) : localStorage.setItem(key, JSON.stringify(val)); } catch (_) { /* ignore */ }
  }
  function recall(key) {
    try { return JSON.parse(localStorage.getItem(key) || "null"); } catch (_) { return null; }
  }

  // ── Small pieces ─────────────────────────────────────────────────────────
  function sprite(mon, shiny) {
    if (!mon || !mon.pokemon_id) return PLACEHOLDER;
    return `${CDN}/${shiny ? "shiny/" : ""}${mon.pokemon_id}.png`;
  }
  function img(mon, shiny, cls) {
    const el = document.createElement("img");
    el.alt = mon ? mon.display_name : "";
    el.decoding = "async";
    if (cls) el.className = cls;
    el.onerror = () => { el.onerror = null; el.src = PLACEHOLDER; };
    el.src = sprite(mon, shiny);
    return el;
  }
  function badge(rule) {
    const el = document.createElement("span");
    el.className = "rb";
    if (!rule) return el;
    const t = rule.id && rule.id.startsWith("type_") ? rule.id.slice(5) : null;
    if (t && TYPE_CONFIG[t]) {
      el.classList.add("rb-type");
      el.style.setProperty("--tb", TYPE_CONFIG[t][0]);
      el.style.setProperty("--tf", TYPE_CONFIG[t][1]);
      const ic = document.createElement("span");
      ic.className = `px-ti px-ti-${t}`;
      ic.setAttribute("aria-hidden", "true");
      el.appendChild(ic);
    }
    el.appendChild(document.createTextNode(rule.label));
    return el;
  }
  function pips(el, total, left, cls) {
    el.textContent = "";
    for (let i = 0; i < total; i++) {
      const p = document.createElement("span");
      p.className = cls + (i >= left ? " spent" : "");
      el.appendChild(p);
    }
    el.setAttribute("aria-label", `${left} of ${total} left`);
  }
  function toast(text, tone) {
    const t = document.createElement("div");
    t.className = "cc-toast" + (tone ? ` ${tone}` : "");
    t.textContent = text;
    $("ccToasts").appendChild(t);
    while ($("ccToasts").children.length > 3) $("ccToasts").firstChild.remove();
    setTimeout(() => t.remove(), tone === "err" ? 4800 : 3000);
  }
  function animate(still, mon, shiny, ms) {
    if (still && mon && mon.pokemon_id) window.PDD_MOTION?.play(still, mon.pokemon_id, !!shiny, ms);
  }
  function prettyDate(iso) {
    const d = new Date(`${iso}T12:00:00`);
    return isNaN(d) ? "" : d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  }

  // Rules for one square / one slice.
  function rulesAt(z, y, x) {
    const a = S.board.axes;
    return [a[0].rules[x], a[1].rules[y], a[2].rules[z]];
  }
  function sliceCells(axis, index) {
    return CELLS.filter(([z, y, x]) => (axis === 0 ? x : axis === 1 ? y : z) === index);
  }

  // ── Loading ──────────────────────────────────────────────────────────────
  async function load() {
    $("ccNew").hidden = S.which !== "practice";
    $("lvGrid").setAttribute("aria-busy", "true");
    try {
      if (S.kind === "fill") await loadFill(); else await loadReverse();
    } catch (e) {
      toast(e.message || "Couldn't load the Cube.", "err");
    }
    $("lvGrid").removeAttribute("aria-busy");
    render();
  }

  function newPracticeSeed() {
    return `p-${Math.random().toString(36).slice(2, 10)}`;
  }

  async function loadFill(fresh) {
    S.answers = new Map();
    S.picks = new Map();
    let seed;
    let saved = null;
    if (S.which === "practice") {
      saved = fresh ? null : recall(PRACTICE_KEY);
      seed = saved?.seed || newPracticeSeed();
    }
    const b = await api(`/cube/board${seed ? `?seed=${encodeURIComponent(seed)}` : ""}`);
    S.board = b;
    S.maxLives = b.lives;
    S.maxHints = b.hints;
    if (b.run) {
      S.lives = b.run.lives_left;
      S.hints = b.run.hints_left;
      S.finished = b.run.finished;
      for (const p of b.run.picks) {
        S.picks.set(ck(p.z, p.y, p.x), {
          mon: { key: p.key, display_name: p.display_name, pokemon_id: p.pokemon_id },
          hinted: p.hinted, shiny: p.shiny, pct: p.pct,
        });
      }
    } else if (saved && saved.seed === b.seed) {
      S.lives = saved.lives; S.hints = saved.hints; S.finished = saved.finished;
      for (const [k, v] of saved.picks) S.picks.set(k, v);
    } else {
      S.lives = b.lives; S.hints = b.hints; S.finished = false;
    }
    if (S.which === "practice") savePractice();
    if (S.finished) await loadAnswers();
    if (!S.finished) S.layer = firstOpenLayer();
  }

  function savePractice() {
    if (S.which !== "practice" || !S.board) return;
    store(PRACTICE_KEY, { seed: S.board.seed, lives: S.lives, hints: S.hints, finished: S.finished,
                          picks: Array.from(S.picks.entries()) });
  }

  function firstOpenLayer() {
    for (let z = 0; z < 3; z++) {
      for (let y = 0; y < 3; y++) for (let x = 0; x < 3; x++) if (!S.picks.has(ck(z, y, x))) return z;
    }
    return 0;
  }

  async function loadAnswers() {
    try {
      const sol = await api(`/cube/solution?seed=${encodeURIComponent(S.board.seed)}`);
      S.answers = new Map(sol.cells.map((c) => [ck(c.z, c.y, c.x), { mon: c.example, count: c.count }]));
    } catch (_) {
      S.answers = new Map();
    }
  }

  async function loadReverse(fresh) {
    if (S.which === "daily") {
      S.rev = await api("/cube/reverse/daily");
    } else {
      if (fresh || !S.revPractice) S.revPractice = await api("/cube/reverse", { difficulty: "medium" });
      S.rev = S.revPractice;
    }
    S.revRun = S.rev.run;
  }

  // ── Render ───────────────────────────────────────────────────────────────
  function render() {
    renderConsole();
    renderStack();
    renderLayer();
    renderBanner();
  }

  function renderConsole() {
    document.querySelectorAll("[data-kind]").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.kind === S.kind)));
    document.querySelectorAll("[data-which]").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.which === S.which)));
    const chip = $("ccChip");
    if (S.kind === "fill") {
      const n = S.picks.size;
      $("ccScore").textContent = `${n} / 27`;
      $("ccFill").style.width = `${(n / 27) * 100}%`;
      pips($("ccLives"), S.maxLives, S.lives, "pip-life");
      pips($("ccHints"), S.maxHints, S.hints, "pip-hint");
      chip.textContent = S.board ? (S.board.is_daily ? `Today's Cube, ${prettyDate(S.board.seed)}` : "Practice Cube") : "";
    } else {
      const run = S.revRun;
      const n = run ? run.solved.length : 0;
      $("ccScore").textContent = `${n} / 9`;
      $("ccFill").style.width = `${(n / 9) * 100}%`;
      pips($("ccLives"), S.rev ? S.rev.lives : 3, run ? run.lives_left : 3, "pip-life");
      pips($("ccHints"), S.rev ? S.rev.hints : 1, run ? run.hints_left : 1, "pip-hint");
      chip.textContent = S.rev ? (S.rev.is_daily ? `Today's Reverse Cube, ${prettyDate(S.rev.seed)}` : "Practice Reverse Cube") : "";
    }
  }

  // What stands on a square, for either kind of Cube.
  function occupant(z, y, x) {
    const k = ck(z, y, x);
    if (S.kind === "reverse") {
      const c = S.rev?.cells.find((c) => c.z === z && c.y === y && c.x === x);
      return c ? { mon: c.mon } : null;
    }
    const p = S.picks.get(k);
    if (p) return p;
    const a = S.answers.get(k);
    return a ? { mon: a.mon, answer: true, count: a.count } : null;
  }

  function highlighted() {
    // Squares to light up in the 3D view: the slice being guessed, or the square being answered.
    const out = new Set();
    if (S.guess) sliceCells(S.guess.axis, S.guess.index).forEach((c) => out.add(ck(...c)));
    return out;
  }

  function renderStack() {
    const stack = $("cvStack");
    stack.textContent = "";
    if (!(S.kind === "fill" ? S.board : S.rev)) return;
    const hl = highlighted();
    const sel = S.sel ? ck(...S.sel) : null;
    for (let z = 0; z < 3; z++) {
      const plate = document.createElement("div");
      plate.className = "cv-plate" + (z === S.layer ? " open" : "");
      plate.dataset.z = z;
      // Back rows first so nearer Pokémon are drawn over farther ones.
      for (let y = 0; y < 3; y++) {
        for (let x = 0; x < 3; x++) {
          const k = ck(z, y, x);
          const tile = document.createElement("div");
          tile.className = "cv-tile";
          tile.dataset.k = k;
          if (hl.has(k)) tile.classList.add("hl");
          if (k === sel) tile.classList.add("sel");
          const o = occupant(z, y, x);
          if (o && o.mon) {
            const sh = document.createElement("span");
            sh.className = "cv-shadow";
            tile.appendChild(sh);
            const st = document.createElement("span");
            st.className = "cv-stand" + (o.hinted ? " hint" : "");
            st.appendChild(img(o.mon, o.shiny));
            if (o.answer) st.style.opacity = "0.45";
            tile.appendChild(st);
          } else {
            tile.classList.add("empty");
          }
          plate.appendChild(tile);
        }
      }
      stack.appendChild(plate);
    }
  }

  function renderLayer() {
    const z = S.layer;
    const tabs = $("lvTabs");
    const grid = $("lvGrid");
    tabs.textContent = "";
    grid.textContent = "";
    const ready = S.kind === "fill" ? S.board : S.rev;
    if (!ready) return;

    // Layer tabs
    for (let i = 0; i < 3; i++) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "lv-tab";
      b.setAttribute("role", "tab");
      b.setAttribute("aria-selected", String(i === z));
      const name = document.createElement("span");
      name.className = "lv-tab-name";
      name.textContent = LAYER[i].split(" ")[0];
      const word = document.createElement("span");
      word.className = "lv-word";
      word.textContent = " layer";
      name.appendChild(word);
      if (S.kind === "fill") {
        let n = 0;
        for (let y = 0; y < 3; y++) for (let x = 0; x < 3; x++) if (S.picks.has(ck(i, y, x))) n++;
        const c = document.createElement("span");
        c.className = "lv-tab-count";
        c.textContent = `${n}/9`;
        name.appendChild(c);
        b.append(name, badge(S.board.axes[2].rules[i]));
      } else {
        b.append(name, revBadge(2, i));
      }
      b.addEventListener("click", () => openLayer(i));
      tabs.appendChild(b);
    }

    // Corner: the open layer's own rule (the third rule of every square here).
    const corner = document.createElement(S.kind === "reverse" ? "button" : "div");
    corner.className = S.kind === "reverse" ? "lv-head" : "lv-corner";
    if (S.kind === "fill") {
      const cap = document.createElement("span");
      cap.textContent = "On this layer";
      corner.append(cap, badge(S.board.axes[2].rules[z]));
      corner.title = `Every square on the ${LAYER[z].toLowerCase()} also needs this`;
    } else {
      corner.type = "button";
      corner.append(revBadge(2, z));
      corner.setAttribute("aria-label", `${LAYER[z]}: ${revLabel(2, z)}`);
      corner.addEventListener("click", () => openGuess(2, z));
    }
    grid.appendChild(corner);

    // Column heads
    for (let x = 0; x < 3; x++) grid.appendChild(head(0, x));
    // Rows
    for (let y = 0; y < 3; y++) {
      grid.appendChild(head(1, y));
      for (let x = 0; x < 3; x++) grid.appendChild(cell(z, y, x));
    }

    if (S.kind === "fill") {
      const r = S.board.axes[2].rules[z];
      $("lvHelp").textContent = `Each square needs a Pokémon that fits its column and its row, and every square on the ${LAYER[z].toLowerCase()} adds a third rule: ${r.label}.`;
    } else {
      $("lvHelp").textContent = "Tap a column, row or layer name and guess what its nine Pokémon share. A column or row runs through all three layers, so check the 3D view.";
    }
  }

  function head(axis, index) {
    const reverse = S.kind === "reverse";
    const el = document.createElement(reverse ? "button" : "div");
    el.className = "lv-head";
    if (S.guess && S.guess.axis === axis && S.guess.index === index) el.classList.add("hl");
    if (reverse) {
      el.type = "button";
      el.append(revBadge(axis, index));
      el.setAttribute("aria-label", `${AXIS[axis]} ${index + 1}: ${revLabel(axis, index)}`);
      el.addEventListener("click", () => openGuess(axis, index));
    } else {
      el.append(badge(S.board.axes[axis].rules[index]));
      el.setAttribute("role", axis === 0 ? "columnheader" : "rowheader");
    }
    return el;
  }

  function cell(z, y, x) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "lv-cell";
    b.dataset.z = z; b.dataset.y = y; b.dataset.x = x;
    b.setAttribute("role", "gridcell");
    const o = occupant(z, y, x);
    if (S.sel && ck(...S.sel) === ck(z, y, x)) b.classList.add("sel");
    if (o && o.mon) {
      const im = img(o.mon, o.shiny);
      b.appendChild(im);
      const n = document.createElement("span");
      n.className = "lv-name";
      n.textContent = o.mon.display_name;
      b.appendChild(n);
      if (o.answer) {
        b.classList.add("answer");
        const c = document.createElement("span");
        c.className = "lv-pct";
        c.textContent = `${o.count} fit`;
        b.appendChild(c);
        b.setAttribute("aria-label", `One answer: ${o.mon.display_name}. ${o.count} Pokémon fit this square.`);
      } else {
        if (S.kind === "fill") b.classList.add("filled");
        if (o.hinted) b.classList.add("hinted");
        if (o.shiny) b.classList.add("shiny");
        if (o.pct != null) {
          const c = document.createElement("span");
          c.className = "lv-pct";
          c.textContent = `${Math.round(o.pct)}%`;
          c.title = "Players who picked the same Pokémon here";
          b.appendChild(c);
        }
        b.setAttribute("aria-label", `${o.mon.display_name}${o.shiny ? ", shiny" : ""}`);
      }
      b.addEventListener("mouseenter", () => animate(im, o.mon, o.shiny, 0));
      b.addEventListener("mouseleave", () => window.PDD_MOTION?.stop(im));
    } else {
      const p = document.createElement("span");
      p.className = "lv-plus";
      p.setAttribute("aria-hidden", "true");
      b.appendChild(p);
      const [c, r, l] = rulesAt(z, y, x);
      b.setAttribute("aria-label", `Empty: ${c.label}, ${r.label}, ${l.label}`);
    }
    b.addEventListener("click", () => {
      if (swiped) return;
      const im = b.querySelector("img");
      if (o && o.mon) animate(im, o.mon, o.shiny, 2600);
      else if (S.kind === "fill") openPick(z, y, x);
    });
    return b;
  }

  // Reverse headers: solved, revealed, or still hidden.
  function revSlice(axis, index) {
    const run = S.revRun;
    if (!run) return null;
    return run.solved.find((s) => s.axis === axis && s.index === index)
      || run.reveal.find((s) => s.axis === axis && s.index === index && (s.revealed = true));
  }
  function revLabel(axis, index) {
    const s = revSlice(axis, index);
    return s ? s.label : "not named yet";
  }
  function revBadge(axis, index) {
    const s = revSlice(axis, index);
    if (!s) {
      const el = document.createElement("span");
      el.className = "rb rb-hidden";
      el.textContent = "Name it";
      return el;
    }
    const el = badge({ id: s.rule_id, label: s.label });
    el.classList.add(s.revealed ? "rb-revealed" : "rb-solved");
    return el;
  }

  function renderBanner() {
    const bn = $("ccBanner");
    const text = bn.querySelector(".cc-banner-text");
    const btn = $("ccBannerBtn");
    let msg = null, lost = false;
    if (S.kind === "fill" && S.board && S.finished) {
      if (S.picks.size >= 27) msg = S.board.is_daily ? "Cube complete. Come back tomorrow for a new one." : "Cube complete.";
      else { msg = "Out of lives. The open squares show one Pokémon that fit."; lost = true; }
    }
    if (S.kind === "reverse" && S.revRun && S.revRun.finished) {
      if (S.revRun.won) msg = "All nine rules named.";
      else { msg = "Out of lives. The rules you missed are shown in red."; lost = true; }
    }
    bn.hidden = !msg;
    bn.classList.toggle("lost", lost);
    if (!msg) return;
    text.textContent = msg;
    const daily = S.kind === "fill" ? S.board.is_daily : S.rev.is_daily;
    btn.textContent = daily ? "Play a practice Cube" : "New Cube";
  }

  // ── Opening a layer (tabs, the 3D view, swipes, keys) ────────────────────
  function openLayer(z) {
    if (z < 0 || z > 2) return;
    S.layer = z;
    renderStack();
    renderLayer();
  }

  // ── 3D view: drag to turn ────────────────────────────────────────────────
  const view = { spin: -38, tilt: 58 };
  function setView() {
    document.body.style.setProperty("--spin", `${view.spin}deg`);
    document.body.style.setProperty("--tilt", `${view.tilt}deg`);
  }
  function setupStack() {
    const scene = $("cvScene");
    let drag = null;
    let moved = false;
    scene.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      drag = { x: e.clientX, y: e.clientY, spin: view.spin, tilt: view.tilt, touch: e.pointerType !== "mouse" };
      moved = false;
      scene.classList.remove("settle");
    });
    window.addEventListener("pointermove", (e) => {
      if (!drag) return;
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      if (Math.abs(dx) + Math.abs(dy) > 6) { moved = true; scene.classList.add("dragging"); }
      if (!moved) return;
      view.spin = drag.spin + dx * 0.45;
      if (!drag.touch) view.tilt = Math.max(30, Math.min(78, drag.tilt - dy * 0.3));
      setView();
    });
    const end = () => { drag = null; scene.classList.remove("dragging"); };
    window.addEventListener("pointerup", end);
    window.addEventListener("pointercancel", end);
    scene.addEventListener("click", (e) => {
      if (moved) { moved = false; return; }
      const plate = e.target.closest(".cv-plate");
      if (!plate) return;
      const z = Number(plate.dataset.z);
      const tile = e.target.closest(".cv-tile");
      openLayer(z);
      if (tile && S.kind === "fill") {
        const [tz, ty, tx] = tile.dataset.k.split(",").map(Number);
        if (!occupant(tz, ty, tx)) openPick(tz, ty, tx);
      }
    });
    scene.addEventListener("dblclick", () => { turnTo(-38, 58); });
    document.querySelectorAll(".cv-turn").forEach((b) => b.addEventListener("click", () => turnTo(view.spin + Number(b.dataset.turn), view.tilt)));
    setView();
  }
  function turnTo(spin, tilt) {
    const scene = $("cvScene");
    if (!reduced?.matches) scene.classList.add("settle");
    view.spin = spin; view.tilt = tilt;
    setView();
  }

  // ── Flat layer: swipe for the next layer, arrow keys between squares ─────
  let swiped = false;
  function setupGrid() {
    const grid = $("lvGrid");
    let start = null;
    grid.addEventListener("pointerdown", (e) => { start = { x: e.clientX, y: e.clientY }; swiped = false; });
    grid.addEventListener("pointerup", (e) => {
      if (!start) return;
      const dx = e.clientX - start.x, dy = e.clientY - start.y;
      start = null;
      if (Math.abs(dx) > 60 && Math.abs(dy) < 45) {
        swiped = true;
        openLayer(S.layer + (dx < 0 ? 1 : -1));
        setTimeout(() => { swiped = false; }, 0);
      }
    });
    grid.addEventListener("keydown", (e) => {
      const c = e.target.closest(".lv-cell");
      if (e.key === "PageDown" || e.key === "PageUp") {
        e.preventDefault();
        const pos = c ? [Number(c.dataset.y), Number(c.dataset.x)] : [0, 0];
        openLayer(S.layer + (e.key === "PageDown" ? 1 : -1));
        focusCell(pos[0], pos[1]);
        return;
      }
      if (!c) return;
      let y = Number(c.dataset.y), x = Number(c.dataset.x);
      if (e.key === "ArrowRight") x = Math.min(2, x + 1);
      else if (e.key === "ArrowLeft") x = Math.max(0, x - 1);
      else if (e.key === "ArrowDown") y = Math.min(2, y + 1);
      else if (e.key === "ArrowUp") y = Math.max(0, y - 1);
      else return;
      e.preventDefault();
      focusCell(y, x);
    });
  }
  function focusCell(y, x) {
    $("lvGrid").querySelector(`.lv-cell[data-y="${y}"][data-x="${x}"]`)?.focus();
  }

  // ── Fill: choosing a Pokémon ─────────────────────────────────────────────
  let suggestTimer = 0;
  let suggestions = [];
  let active = -1;

  function usedKeys() {
    return Array.from(S.picks.values()).map((p) => p.mon.key);
  }

  function openPick(z, y, x) {
    if (S.finished || S.kind !== "fill") return;
    S.sel = [z, y, x];
    const rules = $("cmRules");
    rules.textContent = "";
    rulesAt(z, y, x).forEach((r, i) => {
      const box = document.createElement("div");
      box.className = "cm-rule";
      const ax = document.createElement("span");
      ax.className = "cm-rule-axis";
      ax.textContent = i === 2 ? LAYER[z] : AXIS[i];
      box.append(ax, badge(r));
      rules.appendChild(box);
    });
    $("cmInput").value = "";
    $("cmSuggest").textContent = "";
    suggestions = []; active = -1;
    setStatus("cmStatus", "");
    pips($("cmLives"), S.maxLives, S.lives, "pip-life");
    $("cmHint").disabled = S.hints <= 0;
    $("cmHint").textContent = S.hints > 0 ? `Use a hint (${S.hints} left)` : "No hints left";
    renderStack();
    renderLayer();
    show($("cmPick"));
    setTimeout(() => $("cmInput").focus(), 30);
  }

  function setStatus(id, text, tone) {
    const el = $(id);
    el.textContent = text;
    el.className = "cm-status" + (tone ? ` ${tone}` : "");
  }

  function show(dlg) {
    if (typeof dlg.showModal === "function") dlg.showModal(); else dlg.setAttribute("open", "");
  }
  function hide(dlg) {
    if (typeof dlg.close === "function") dlg.close(); else dlg.removeAttribute("open");
  }

  async function suggest() {
    const q = $("cmInput").value.trim();
    if (!q) { $("cmSuggest").textContent = ""; suggestions = []; return; }
    try {
      const list = await api(`/pokemon/suggest?prefix=${encodeURIComponent(q)}&limit=8`);
      if ($("cmInput").value.trim() !== q) return;
      suggestions = list; active = list.length ? 0 : -1;
      drawSuggestions();
    } catch (_) { /* keep the old list */ }
  }
  function drawSuggestions() {
    const ul = $("cmSuggest");
    ul.textContent = "";
    const used = new Set(usedKeys());
    suggestions.forEach((m, i) => {
      const li = document.createElement("li");
      const b = document.createElement("button");
      b.type = "button";
      b.setAttribute("role", "option");
      if (i === active) b.classList.add("active");
      b.append(img(m, false));
      const t = document.createElement("span");
      t.textContent = m.display_name + (used.has(m.key) ? " (already used)" : "");
      b.appendChild(t);
      b.addEventListener("click", () => submitPick(m));
      li.appendChild(b);
      ul.appendChild(li);
    });
  }

  let submitting = false;
  async function submitPick(mon) {
    if (submitting || !S.sel) return;
    submitting = true;
    const [z, y, x] = S.sel;
    try {
      const r = await api("/cube/validate", { seed: S.board.seed, z, y, x, pokemon_key: mon.key, used_keys: usedKeys() });
      if (r.ok) {
        S.picks.set(ck(z, y, x), { mon: r.mon, hinted: false, shiny: r.shiny, pct: r.pct });
        hide($("cmPick"));
        S.sel = null;
        if (r.shiny) toast(`A shiny ${r.mon.display_name}!`, "ok");
        if (r.stranded) toast("Heads up: with the Pokémon used so far, not every open square can still be filled.", "err");
        afterAnswer(z, y, x, r);
      } else {
        const msgs = {
          does_not_match: `${mon.display_name} doesn't fit all three rules.`,
          duplicate: `${mon.display_name} is already on this Cube. Each Pokémon goes in once.`,
          unknown_pokemon: "Pick a Pokémon from the list.",
          already_filled: "That square is already filled.",
          board_locked: "This Cube is finished.",
        };
        setStatus("cmStatus", msgs[r.reason] || "That didn't work.", "err");
        if (r.reason === "does_not_match" || r.reason === "duplicate") {
          S.lives = r.lives_left != null ? r.lives_left : Math.max(0, S.lives - 1);
          pips($("cmLives"), S.maxLives, S.lives, "pip-life");
          const cellEl = $("lvGrid").querySelector(`.lv-cell[data-z="${z}"][data-y="${y}"][data-x="${x}"]`);
          cellEl?.classList.add("err");
          if (S.lives <= 0) {
            S.finished = true;
            hide($("cmPick"));
            S.sel = null;
            await loadAnswers();
          }
        }
        if (r.reason === "board_locked") { S.finished = true; hide($("cmPick")); await loadAnswers(); }
        renderConsole();
        renderBanner();
        if (S.finished) { renderStack(); renderLayer(); }
        savePractice();
      }
    } catch (e) {
      setStatus("cmStatus", e.message, "err");
    }
    submitting = false;
  }

  async function afterAnswer(z, y, x, r) {
    if (r && r.lives_left != null) S.lives = r.lives_left;
    if (S.picks.size >= 27 || (r && r.finished)) {
      S.finished = true;
      if (S.picks.size < 27) await loadAnswers();
    }
    savePractice();
    render();
    // The new Pokémon moves for a moment, in both views.
    const k = ck(z, y, x);
    $("cvStack").querySelector(`.cv-tile[data-k="${k}"]`)?.classList.add("ok-flash");
    const cellEl = $("lvGrid").querySelector(`.lv-cell[data-z="${z}"][data-y="${y}"][data-x="${x}"]`);
    const p = S.picks.get(k);
    if (cellEl && p) setTimeout(() => animate(cellEl.querySelector("img"), p.mon, p.shiny, 2600), 120);
    // Done with this layer? Open the next one with space left.
    if (!S.finished && cellEl) {
      let full = true;
      for (let yy = 0; yy < 3; yy++) for (let xx = 0; xx < 3; xx++) if (!S.picks.has(ck(z, yy, xx))) full = false;
      if (full) setTimeout(() => openLayer(firstOpenLayer()), 900);
      else cellEl.focus();
    }
  }

  async function useHint() {
    if (!S.sel || S.hints <= 0) return;
    const [z, y, x] = S.sel;
    const filled = Array.from(S.picks.entries()).map(([k, v]) => {
      const [a, b, c] = k.split(",").map(Number);
      return { z: a, y: b, x: c, key: v.mon.key };
    });
    try {
      const r = await api("/cube/hint", { seed: S.board.seed, z, y, x, used_keys: usedKeys(), filled });
      if (!r.ok) {
        setStatus("cmStatus", r.reason === "no_hints" ? "No hints left." : "No hint for this square.", "err");
        return;
      }
      S.hints = r.hints_left != null ? r.hints_left : S.hints - 1;
      S.picks.set(ck(z, y, x), { mon: r.mon, hinted: true, shiny: false, pct: null });
      hide($("cmPick"));
      S.sel = null;
      afterAnswer(z, y, x, { finished: r.finished });
    } catch (e) {
      setStatus("cmStatus", e.message, "err");
    }
  }

  function setupPick() {
    const input = $("cmInput");
    input.addEventListener("input", () => {
      clearTimeout(suggestTimer);
      suggestTimer = setTimeout(suggest, 120);
    });
    input.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        if (!suggestions.length) return;
        e.preventDefault();
        active = (active + (e.key === "ArrowDown" ? 1 : -1) + suggestions.length) % suggestions.length;
        drawSuggestions();
      } else if (e.key === "Enter") {
        e.preventDefault();
        if (suggestions[active]) submitPick(suggestions[active]);
      }
    });
    $("cmPickForm").addEventListener("submit", (e) => e.preventDefault());
    $("cmHint").addEventListener("click", useHint);
    $("cmPick").addEventListener("close", () => { S.sel = null; renderStack(); renderLayer(); });
  }

  // ── Reverse: guessing a slice ────────────────────────────────────────────
  let groupFilter = "all";
  const GROUP_NAMES = { all: "All", type: "Types", region: "Regions", evolution: "Evolution", move: "Moves", ability: "Abilities", other: "Other" };

  async function openGuess(axis, index) {
    if (!S.revRun || S.revRun.finished || revSlice(axis, index)) return;
    if (!S.catalog) {
      try { S.catalog = await api("/rules/catalog"); } catch (e) { toast(e.message, "err"); return; }
    }
    S.guess = { axis, index, rule: null };
    if (axis === 2) S.layer = index;
    $("cmGuessTitle").textContent = axis === 2
      ? `What do all nine on the ${LAYER[index].toLowerCase()} share?`
      : `What do all nine in ${AXIS[axis].toLowerCase()} ${index + 1} share?`;
    const slice = $("cgSlice");
    slice.textContent = "";
    for (const [z, y, x] of sliceCells(axis, index)) {
      const o = occupant(z, y, x);
      const f = document.createElement("figure");
      const cap = document.createElement("figcaption");
      cap.textContent = o.mon.display_name;
      f.append(img(o.mon, false), cap);
      slice.appendChild(f);
    }
    $("cgSearch").value = "";
    groupFilter = "all";
    drawGroups();
    drawRules();
    setStatus("cgStatus", "");
    updateGuessFoot();
    renderStack();
    renderLayer();
    show($("cmGuess"));
  }

  function drawGroups() {
    const box = $("cgGroups");
    box.textContent = "";
    ["all", ...Object.keys(S.catalog.groups).filter((g) => S.catalog.groups[g].length)].forEach((g) => {
      const b = document.createElement("button");
      b.type = "button";
      b.setAttribute("role", "tab");
      b.setAttribute("aria-selected", String(g === groupFilter));
      b.textContent = GROUP_NAMES[g] || g;
      b.addEventListener("click", () => { groupFilter = g; drawGroups(); drawRules(); });
      box.appendChild(b);
    });
  }

  function drawRules() {
    const q = $("cgSearch").value.trim().toLowerCase();
    const list = $("cgList");
    list.textContent = "";
    const groups = groupFilter === "all" ? Object.keys(S.catalog.groups) : [groupFilter];
    let n = 0;
    for (const g of groups) {
      for (const r of S.catalog.groups[g]) {
        if (q && !r.label.toLowerCase().includes(q)) continue;
        const b = document.createElement("button");
        b.type = "button";
        b.setAttribute("role", "option");
        b.setAttribute("aria-selected", String(S.guess?.rule?.rule_id === r.rule_id));
        b.textContent = r.label;
        b.addEventListener("click", () => {
          S.guess.rule = r;
          drawRules();
          updateGuessFoot();
        });
        list.appendChild(b);
        n++;
      }
    }
    if (!n) {
      const p = document.createElement("p");
      p.className = "cm-status";
      p.textContent = "No category matches that search.";
      list.appendChild(p);
    }
  }

  function updateGuessFoot() {
    const run = S.revRun;
    pips($("cgLives"), S.rev.lives, run.lives_left, "pip-life");
    $("cgHint").disabled = run.hints_left <= 0;
    $("cgHint").textContent = run.hints_left > 0 ? "Reveal this one" : "No reveals left";
    $("cgGo").disabled = !S.guess?.rule;
    $("cgGo").textContent = S.guess?.rule ? `Guess ${S.guess.rule.label}` : "Guess";
  }

  async function submitGuess(hint) {
    const g = S.guess;
    if (!g || (!hint && !g.rule)) return;
    try {
      const r = await api(hint ? "/cube/reverse/hint" : "/cube/reverse/validate",
        { board_id: S.rev.board_id, axis: g.axis, index: g.index, rule_id: hint ? "" : g.rule.rule_id });
      S.revRun = r.run;
      if (S.rev === S.revPractice) S.revPractice.run = r.run;
      if (r.ok) {
        hide($("cmGuess"));
        toast(hint ? `Revealed: ${r.label}` : `Right, they're all ${r.label}.`, "ok");
      } else if (r.reason === "wrong") {
        setStatus("cgStatus", `Not ${r.label}. ${r.run.lives_left} ${r.run.lives_left === 1 ? "life" : "lives"} left.`, "err");
        updateGuessFoot();
        if (r.run.finished) hide($("cmGuess"));
      } else if (r.reason === "board_locked") {
        hide($("cmGuess"));
      } else {
        setStatus("cgStatus", r.reason === "no_hints" ? "No reveals left." : "That didn't work.", "err");
      }
      render();
    } catch (e) {
      setStatus("cgStatus", e.message, "err");
    }
  }

  function setupGuess() {
    $("cgSearch").addEventListener("input", drawRules);
    $("cgGo").addEventListener("click", () => submitGuess(false));
    $("cgHint").addEventListener("click", () => submitGuess(true));
    $("cmGuess").addEventListener("close", () => { S.guess = null; renderStack(); renderLayer(); });
  }

  // ── Console + dialogs ────────────────────────────────────────────────────
  function setupConsole() {
    document.querySelectorAll("[data-kind]").forEach((b) => b.addEventListener("click", () => {
      if (S.kind === b.dataset.kind) return;
      S.kind = b.dataset.kind;
      S.layer = 0;
      writeHash();
      load();
    }));
    document.querySelectorAll("[data-which]").forEach((b) => b.addEventListener("click", () => {
      if (S.which === b.dataset.which) return;
      S.which = b.dataset.which;
      S.layer = 0;
      writeHash();
      load();
    }));
    $("ccNew").addEventListener("click", newPractice);
    $("ccBannerBtn").addEventListener("click", () => {
      if (S.which === "daily") { S.which = "practice"; writeHash(); }
      newPractice();
    });
    document.querySelectorAll("dialog.cm [data-close]").forEach((b) => b.addEventListener("click", () => hide(b.closest("dialog"))));
    document.querySelectorAll("dialog.cm").forEach((d) => d.addEventListener("click", (e) => { if (e.target === d) hide(d); }));
    $("cubeHowBtn").addEventListener("click", () => show($("cmHow")));
  }

  async function newPractice() {
    $("ccNew").hidden = false;
    S.layer = 0;
    try {
      if (S.kind === "fill") { store(PRACTICE_KEY, null); await loadFill(true); }
      else await loadReverse(true);
    } catch (e) { toast(e.message, "err"); }
    render();
  }

  // #reverse, #practice and #reverse-practice open those directly.
  function readHash() {
    const h = (location.hash || "").slice(1);
    S.kind = h.startsWith("reverse") ? "reverse" : "fill";
    S.which = h.endsWith("practice") ? "practice" : "daily";
  }
  function writeHash() {
    const h = [S.kind === "reverse" ? "reverse" : "", S.which === "practice" ? "practice" : ""].filter(Boolean).join("-");
    history.replaceState(null, "", h ? `#${h}` : location.pathname);
  }

  // ── Start ────────────────────────────────────────────────────────────────
  readHash();
  setupStack();
  setupGrid();
  setupPick();
  setupGuess();
  setupConsole();
  load();
  try {
    if (!localStorage.getItem("route81.cube.seenHow")) {
      localStorage.setItem("route81.cube.seenHow", "1");
      setTimeout(() => show($("cmHow")), 300);
    }
  } catch (_) { /* storage blocked */ }
})();
