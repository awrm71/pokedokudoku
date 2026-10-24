/* ============================================================================
   draft.js — Draft Board builder page, mode switching, and per-mode boards
   ----------------------------------------------------------------------------
   Replaces app.js's old Draft overlay and its mode-tab controller.

   Why this exists:
   • The old controller only reloaded Daily when `!state.board`. Daily and
     Draft shared one board slot, so after drafting, clicking Daily found a
     board already loaded (the draft) and never swapped back. That's the
     "stuck on Draft" bug.
   • Drafting called PDD_SETMODE("draft") while already in Draft, which hit
     the "re-click reopens the configurator" branch, so the overlay reopened
     itself after every draft.
   • The overlay sat over the board, so on phones scrolling moved the board
     behind it and the Draft button could be unreachable.

   Now:
   • Daily and Draft each keep their own board. Leaving a mode stashes its
     grid DOM + game state; coming back restores it with progress intact.
   • Draft is a real page, not an overlay. Clicking the Draft tab shows the
     builder in place of the board; "Draft this board" deals the board and
     the builder goes away. "Configure board" brings it back, with a button
     to return to the board you were playing.

   Loads after app.js (it uses app.js's globals: state, boardWrap,
   applyLoadedBoard, loadBoard, fetchJson, updateHUD, setMessage,
   closeCellModal, formatRuleLabel, normalizeName, TYPE_CONFIG).
   ============================================================================ */
(function () {
  "use strict";

  if (typeof applyLoadedBoard !== "function" || typeof state === "undefined") {
    console.warn("[draft] app.js globals missing; Draft builder disabled.");
    return;
  }

  const GROUPS = [
    ["type", "Types"], ["region", "Regions"], ["evolution", "Evolution"],
    ["move", "Moves"], ["ability", "Abilities"], ["other", "Other"],
  ];
  const SEARCHABLE = new Set(["move", "ability"]);
  const PRESETS = [[3, 3], [4, 4], [5, 5], [9, 9]];
  const MAX = 9;
  const STORE_KEY = "pdd.draftBuilder";

  // ══════════════════════════════════════════════════════════════════════════
  // Per-mode boards
  // ══════════════════════════════════════════════════════════════════════════
  const slots = { daily: null, draft: null };
  let activeMode = "daily";
  let building = false;

  const winBanner = () => document.getElementById("winBanner");
  const loseBanner = () => document.getElementById("gameOverBanner");

  // Move the live board (grid DOM + game state) out of the page into a slot.
  // The grid element keeps its event listeners, so restoring is instant.
  function stash(mode) {
    if (!state.board || !boardWrap.firstElementChild) return;
    if (document.getElementById("cellModal")?.classList.contains("open")) closeCellModal();
    slots[mode] = {
      board: state.board,
      runtimeMode: state.runtimeMode,
      cells: state.cells,
      lives: state.lives,
      hints: state.hints,
      gameOver: state.gameOver,
      grid: boardWrap.firstElementChild,
      locked: boardWrap.classList.contains("board-locked"),
      won: !!winBanner()?.classList.contains("visible"),
      lost: !!loseBanner()?.classList.contains("visible"),
    };
    // Fresh Map, not .clear(): the old one now belongs to the slot.
    state.board = null;
    state.cells = new Map();
    state.activeCellId = null;
    boardWrap.replaceChildren();
    boardWrap.classList.remove("board-locked");
    winBanner()?.classList.remove("visible");
    loseBanner()?.classList.remove("visible");
  }

  function restore(mode) {
    const s = slots[mode];
    if (!s) return false;
    slots[mode] = null;
    Object.assign(state, {
      board: s.board, runtimeMode: s.runtimeMode, cells: s.cells,
      lives: s.lives, hints: s.hints, gameOver: s.gameOver, activeCellId: null,
    });
    boardWrap.replaceChildren(s.grid);
    boardWrap.classList.toggle("board-locked", s.locked);
    winBanner()?.classList.toggle("visible", s.won);
    loseBanner()?.classList.toggle("visible", s.lost);
    updateHUD();
    return true;
  }

  // Every board app.js loads goes through applyLoadedBoard. Tag-less boards
  // are Daily (loadBoard); drafts are tagged. If a load finishes after the
  // player already left that mode, drop it instead of painting it over the
  // wrong mode. Daily just reloads the next time it's opened.
  const baseApply = window.applyLoadedBoard;
  window.applyLoadedBoard = function (board, ...rest) {
    const target = board && board.__slot === "draft" ? "draft" : "daily";
    if (target !== activeMode || building) return;
    slots[target] = null;
    return baseApply.call(this, board, ...rest);
  };

  // ══════════════════════════════════════════════════════════════════════════
  // Mode tabs
  // ══════════════════════════════════════════════════════════════════════════
  const tabs = Array.from(document.querySelectorAll(".mode-tab"));
  const panels = Array.from(document.querySelectorAll(".ctx-panel"));

  function setMode(mode) {
    if (mode === activeMode) return;

    if (activeMode === "daily" || activeMode === "draft") stash(activeMode);
    if (activeMode === "draft") hideBuilder();
    if (activeMode === "reverse") window.PDD_REVERSE?.exit();

    activeMode = mode;
    tabs.forEach((t) => t.classList.toggle("active", t.dataset.mode === mode));
    panels.forEach((p) => p.classList.toggle("hidden", p.dataset.ctx !== mode));

    if (mode === "daily") {
      if (!restore("daily")) loadBoard();
    } else if (mode === "draft") {
      showBuilder();
    } else if (mode === "reverse") {
      window.PDD_REVERSE?.enter();
    }
  }

  tabs.forEach((t) => t.addEventListener("click", () => setMode(t.dataset.mode)));
  window.PDD_SETMODE = setMode;
  tabs.forEach((t) => t.classList.toggle("active", t.dataset.mode === "daily"));
  panels.forEach((p) => p.classList.toggle("hidden", p.dataset.ctx !== "daily"));

  // "Configure board" in the Draft context bar: back to the builder, keeping
  // the current draft resumable.
  ["ctxConfigureDraftBtn", "openDraftBtn"].forEach((id) => {
    document.getElementById(id)?.addEventListener("click", () => {
      if (activeMode !== "draft" || building) return;
      stash("draft");
      showBuilder();
    });
  });

  // Try Again / New Board inside Draft should re-deal a draft with the same
  // settings, not load a random Daily-style board. Capture phase runs before
  // app.js's own listeners on those buttons, so we can take over in Draft only.
  window.addEventListener("click", (e) => {
    if (activeMode !== "draft" || building) return;
    if (!e.target.closest?.("#retryBtn, #newAfterWinBtn")) return;
    e.stopImmediatePropagation();
    e.preventDefault();
    generate();
  }, true);

  // ══════════════════════════════════════════════════════════════════════════
  // Builder state
  // ══════════════════════════════════════════════════════════════════════════
  const saved = readSaved();
  const B = {
    catalog: null,
    off: new Set(saved.off || []),     // rule_ids switched off (default: all on)
    rows: clamp(saved.rows ?? 9),
    cols: clamp(saved.cols ?? 9),
    allowSingle: saved.allowSingle ?? true,
    solvable: saved.solvable ?? true,
    open: null,
    busy: false,
    loadError: "",
  };

  function persist() {
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify({
        off: [...B.off], rows: B.rows, cols: B.cols,
        allowSingle: B.allowSingle, solvable: B.solvable,
      }));
    } catch (_) { /* storage blocked */ }
  }

  function entries(group) { return B.catalog?.groups?.[group] || []; }
  function onCount(group) { return entries(group).filter((e) => !B.off.has(e.rule_id)).length; }
  function selectedIds() {
    return GROUPS.flatMap(([g]) => entries(g)).map((e) => e.rule_id).filter((id) => !B.off.has(id));
  }

  // ══════════════════════════════════════════════════════════════════════════
  // Builder DOM
  //   Desktop: categories on the left, a sticky "board card" on the right
  //   (size, rules, Deal button) that stays in view while you browse.
  //   Phones: one column, size → categories → rules, with the Deal button
  //   pinned to the bottom of the screen.
  // ══════════════════════════════════════════════════════════════════════════
  const root = document.createElement("section");
  root.id = "draftBuilder";
  root.className = "db";
  root.hidden = true;
  root.setAttribute("aria-labelledby", "dbTitle");
  root.innerHTML = `
    <header class="db-head">
      <div class="db-head-text">
        <p class="db-kicker">Draft Board</p>
        <h2 id="dbTitle" class="db-title">Build your own board</h2>
      </div>
      <button type="button" class="db-resume" hidden></button>
    </header>

    <div class="db-cols">
      <section class="db-main" aria-labelledby="dbCatH">
        <div class="db-row-head">
          <h3 id="dbCatH">Categories</h3>
          <span class="db-cat-total" aria-live="polite"></span>
        </div>
        <div class="db-decks" role="group" aria-label="Quick picks"></div>
        <div class="db-tabs" role="tablist" aria-label="Category groups"></div>
        <div class="db-panel" role="tabpanel">
          <p class="db-loading">Loading categories…</p>
        </div>
      </section>

      <aside class="db-side">
        <section class="db-card db-size-card" aria-labelledby="dbSizeH">
          <div class="db-row-head">
            <h3 id="dbSizeH">Board size</h3>
            <span class="db-size-read" aria-live="polite"></span>
          </div>
          <div class="db-size-grid" role="group" aria-label="Board size. Tap or drag to set rows and columns; arrow keys work too."></div>
          <div class="db-presets" role="group" aria-label="Size presets"></div>
        </section>

        <section class="db-card db-rules-card" aria-labelledby="dbRulesH">
          <h3 id="dbRulesH">Rules</h3>
          <button type="button" class="db-switch" role="switch" data-key="allowSingle">
            <span class="db-switch-text"><strong>Single-answer cells</strong><small>Some cells may have just one right Pokémon</small></span>
            <span class="db-switch-track"><span class="db-switch-knob"></span></span>
          </button>
          <button type="button" class="db-switch" role="switch" data-key="solvable">
            <span class="db-switch-text"><strong>Guaranteed solvable</strong><small>Checked for a full solution before dealing</small></span>
            <span class="db-switch-track"><span class="db-switch-knob"></span></span>
          </button>
        </section>

        <div class="db-deal">
          <p class="db-error" role="alert" hidden></p>
          <p class="db-summary" aria-live="polite"></p>
          <button type="button" class="db-go"><span class="db-go-icon" aria-hidden="true">🃏</span><span class="db-go-label">Deal my board</span></button>
        </div>
      </aside>
    </div>
  `;

  // Sits right above the board (the HUD lives inside the console panel,
  // which is hidden while the builder is open).
  const anchor = boardWrap;
  anchor.parentNode.insertBefore(root, anchor);

  const $ = (sel) => root.querySelector(sel);
  const elResume = $(".db-resume");
  const elSizeGrid = $(".db-size-grid");
  const elPresets = $(".db-presets");
  const elSizeRead = $(".db-size-read");
  const elDecks = $(".db-decks");
  const elTabs = $(".db-tabs");
  const elGroups = $(".db-panel");          // name kept: loadCatalog writes errors here
  const elCatTotal = $(".db-cat-total");
  const elError = $(".db-error");
  const elSummary = $(".db-summary");
  const elGo = $(".db-go");
  const elGoLabel = $(".db-go-label");

  // ── Size picker: tap or drag out the board shape ─────────────────────────
  const sizeCells = [];
  for (let r = 0; r < MAX; r += 1) {
    for (let c = 0; c < MAX; c += 1) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "db-sq";
      b.dataset.r = String(r);
      b.dataset.c = String(c);
      b.setAttribute("aria-label", `${r + 1} rows by ${c + 1} columns`);
      elSizeGrid.appendChild(b);
      sizeCells.push(b);
    }
  }
  let dragging = false;
  const sqAt = (x, y) => document.elementFromPoint(x, y)?.closest?.(".db-sq");
  elSizeGrid.addEventListener("pointerdown", (e) => {
    const sq = e.target.closest(".db-sq");
    if (!sq) return;
    dragging = true;
    elSizeGrid.setPointerCapture?.(e.pointerId);
    setSize(+sq.dataset.r + 1, +sq.dataset.c + 1);
  });
  elSizeGrid.addEventListener("pointermove", (e) => {
    if (dragging) {
      const sq = sqAt(e.clientX, e.clientY);
      if (sq) setSize(+sq.dataset.r + 1, +sq.dataset.c + 1);
    } else if (e.pointerType === "mouse") {
      const sq = e.target.closest(".db-sq");
      preview(sq ? +sq.dataset.r + 1 : 0, sq ? +sq.dataset.c + 1 : 0);
    }
  });
  const endDrag = () => { dragging = false; };
  elSizeGrid.addEventListener("pointerup", endDrag);
  elSizeGrid.addEventListener("pointercancel", endDrag);
  elSizeGrid.addEventListener("pointerleave", () => { if (!dragging) preview(0, 0); });
  elSizeGrid.addEventListener("keydown", (e) => {
    const moves = { ArrowRight: [0, 1], ArrowLeft: [0, -1], ArrowDown: [1, 0], ArrowUp: [-1, 0] };
    const m = moves[e.key];
    if (!m) return;
    e.preventDefault();
    setSize(clamp(B.rows + m[0]), clamp(B.cols + m[1]));
    sizeCells[(B.rows - 1) * MAX + (B.cols - 1)].focus();
  });

  PRESETS.forEach(([r, c]) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "db-preset";
    b.dataset.size = `${r}x${c}`;
    b.textContent = `${r}×${c}`;
    b.addEventListener("click", () => setSize(r, c));
    elPresets.appendChild(b);
  });

  function setSize(r, c) {
    r = clamp(r); c = clamp(c);
    if (r === B.rows && c === B.cols) return;
    B.rows = r;
    B.cols = c;
    persist();
    renderSize();
    renderFooter();
  }

  function preview(r, c) {
    sizeCells.forEach((b) => b.classList.toggle("pv", r > 0 && +b.dataset.r < r && +b.dataset.c < c));
    elSizeGrid.classList.toggle("previewing", r > 0);
  }

  function renderSize() {
    sizeCells.forEach((b) => {
      const r = +b.dataset.r, c = +b.dataset.c;
      const on = r < B.rows && c < B.cols;
      b.classList.toggle("on", on);
      // Ripple outward from the top-left when the size grows.
      b.style.setProperty("--d", `${(r + c) * 14}ms`);
      const corner = r === B.rows - 1 && c === B.cols - 1;
      b.classList.toggle("corner", corner);
      b.tabIndex = corner ? 0 : -1;
      b.setAttribute("aria-pressed", String(corner));
    });
    elPresets.querySelectorAll(".db-preset").forEach((b) => {
      b.setAttribute("aria-pressed", String(b.dataset.size === `${B.rows}x${B.cols}`));
    });
    const cells = B.rows * B.cols;
    elSizeRead.innerHTML = `<b>${B.rows} × ${B.cols}</b> · ${cells} cell${cells === 1 ? "" : "s"}`;
  }

  // ── Quick picks ("decks") ────────────────────────────────────────────────
  // Whole-group presets plus a random mix. A deck lights up when the current
  // selection exactly matches it.
  const DECKS = [
    { id: "all", icon: "", label: "Everything", groups: GROUPS.map(([g]) => g) },
    { id: "classic", icon: "", label: "Classic", groups: ["type", "region", "evolution", "other"] },
    { id: "battle", icon: "", label: "Battle", groups: ["type", "move", "ability"] },
    { id: "random", icon: "", label: "Surprise me" },
  ];
  DECKS.forEach((d) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "db-deck";
    b.dataset.deck = d.id;
    b.innerHTML = `<span></span>`;
    b.lastChild.textContent = d.label;
    b.addEventListener("click", () => {
      if (d.id === "random") surprise(); else applyGroups(d.groups);
      b.classList.remove("pop");
      void b.offsetWidth;
      b.classList.add("pop");
    });
    elDecks.appendChild(b);
  });

  function allIds(g) { return entries(g).map((e) => e.rule_id); }

  function applyGroups(groups) {
    const keep = new Set(groups);
    GROUPS.forEach(([g]) => allIds(g).forEach((id) => (keep.has(g) ? B.off.delete(id) : B.off.add(id))));
    afterSelection();
  }

  // Random mix that's always big enough to deal the chosen size.
  function surprise() {
    const ids = GROUPS.flatMap(([g]) => allIds(g));
    const need = Math.max((B.rows + B.cols) * 2, 24);
    B.off = new Set(ids);
    // Keep a random share of every group so the mix feels varied, then top up.
    const share = 0.2 + Math.random() * 0.35;
    GROUPS.forEach(([g]) => {
      const list = shuffle(allIds(g));
      list.slice(0, Math.max(2, Math.round(list.length * share))).forEach((id) => B.off.delete(id));
    });
    const rest = shuffle(ids.filter((id) => B.off.has(id)));
    while (ids.length - B.off.size < need && rest.length) B.off.delete(rest.pop());
    afterSelection();
  }

  function deckMatch() {
    if (!B.catalog) return null;
    const onGroups = GROUPS.map(([g]) => g).filter((g) => entries(g).length);
    const full = onGroups.filter((g) => onCount(g) === entries(g).length);
    const empty = onGroups.filter((g) => onCount(g) === 0);
    if (full.length + empty.length !== onGroups.length) return null;
    const set = full.slice().sort().join();
    return DECKS.find((d) => d.groups && d.groups.filter((g) => onGroups.includes(g)).sort().join() === set)?.id || null;
  }

  // ── Group tabs + chip panel ──────────────────────────────────────────────
  function renderGroups() {
    elTabs.replaceChildren();
    const present = GROUPS.filter(([g]) => entries(g).length);
    if (!present.some(([g]) => g === B.open)) B.open = present[0]?.[0] || null;
    present.forEach(([g, label]) => {
      const t = document.createElement("button");
      t.type = "button";
      t.className = "db-tab";
      t.dataset.group = g;
      t.setAttribute("role", "tab");
      t.innerHTML = `<span class="db-tab-name"></span><span class="db-tab-count"></span><span class="db-tab-meter" aria-hidden="true"><i></i></span>`;
      t.querySelector(".db-tab-name").textContent = label;
      t.addEventListener("click", () => { B.open = g; renderTabs(); renderPanel(); });
      elTabs.appendChild(t);
    });
    renderTabs();
    renderPanel();
    renderCounts();
  }

  function renderTabs() {
    elTabs.querySelectorAll(".db-tab").forEach((t) => {
      const g = t.dataset.group;
      const n = onCount(g), m = entries(g).length;
      t.setAttribute("aria-selected", String(g === B.open));
      t.querySelector(".db-tab-count").textContent = `${n}/${m}`;
      t.querySelector(".db-tab-meter i").style.width = `${m ? (n / m) * 100 : 0}%`;
      t.classList.toggle("empty", n === 0);
    });
  }

  function renderPanel() {
    const g = B.open;
    if (!g) return;
    const label = GROUPS.find(([k]) => k === g)?.[1] || "";
    const isType = g === "type";
    elGroups.replaceChildren();
    elGroups.dataset.group = g;

    const tools = document.createElement("div");
    tools.className = "db-tools";
    tools.innerHTML =
      (SEARCHABLE.has(g) ? `<label class="db-search"><span aria-hidden="true">⌕</span><input type="search" placeholder="Search ${label.toLowerCase()}" aria-label="Search ${label.toLowerCase()}"></label>` : `<span class="db-tools-label"></span>`)
      + `<span class="db-bulk-wrap"><button type="button" class="db-bulk" data-bulk="all">All on</button><button type="button" class="db-bulk" data-bulk="none">All off</button></span>`;
    if (!SEARCHABLE.has(g)) tools.querySelector(".db-tools-label").textContent = `Tap to switch ${label.toLowerCase()} on or off`;
    tools.querySelectorAll(".db-bulk").forEach((b) => b.addEventListener("click", () => bulk(g, b.dataset.bulk === "all")));
    const input = tools.querySelector("input");
    elGroups.appendChild(tools);

    const box = document.createElement("div");
    box.className = "db-chips" + (isType ? " db-chips-types" : "");
    const help = window.PDD_CATEGORY_HELP;
    entries(g).forEach((e, i) => {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "db-chip";
      chip.dataset.id = e.rule_id;
      chip.style.setProperty("--i", String(Math.min(i, 30)));
      const display = formatRuleLabel(e.label);
      // Group name is already on the tab, so "Ability: Levitate" shows as "Levitate".
      const short = display.replace(/^(Ability|Move|Learns|Type|Region|Evolved By)\s*:\s*/i, "");
      chip.dataset.search = normalizeName(display);
      const typeKey = e.rule_id.startsWith("type_") ? e.rule_id.slice(5) : null;
      const cfg = typeKey && TYPE_CONFIG[typeKey];
      if (cfg) {
        chip.classList.add("db-chip-type");
        chip.style.setProperty("--t-bg", cfg.bg);
        chip.style.setProperty("--t-fg", cfg.fg);
        chip.innerHTML = `${typeIconHtml(typeKey, "db-chip-icon")}<span class="db-chip-label"></span>`;
        chip.lastChild.textContent = typeKey.charAt(0).toUpperCase() + typeKey.slice(1);
      } else {
        chip.innerHTML = `<span class="db-chip-check" aria-hidden="true"></span><span class="db-chip-label"></span>`;
        chip.lastChild.textContent = short;
      }
      const explain = help?.explain?.(display);
      chip.title = explain || display;
      chip.setAttribute("aria-pressed", String(!B.off.has(e.rule_id)));
      box.appendChild(chip);
    });
    box.addEventListener("click", (ev) => {
      const chip = ev.target.closest(".db-chip");
      if (!chip) return;
      const id = chip.dataset.id;
      if (B.off.has(id)) B.off.delete(id); else B.off.add(id);
      chip.setAttribute("aria-pressed", String(!B.off.has(id)));
      chip.classList.remove("pop");
      void chip.offsetWidth;
      chip.classList.add("pop");
      persist();
      renderTabs();
      renderCounts();
      renderFooter();
    });
    elGroups.appendChild(box);

    input?.addEventListener("input", () => {
      const needle = normalizeName(input.value || "");
      box.querySelectorAll(".db-chip").forEach((c) => { c.hidden = !!needle && !c.dataset.search.includes(needle); });
    });
  }

  function bulk(g, on) {
    // Only affects chips currently visible, so "search 'fire' → All off" works.
    const visible = Array.from(elGroups.querySelectorAll(".db-chip:not([hidden])")).map((c) => c.dataset.id);
    const ids = visible.length ? visible : allIds(g);
    ids.forEach((id) => (on ? B.off.delete(id) : B.off.add(id)));
    afterSelection(true);
  }

  function afterSelection(keepPanel) {
    persist();
    if (keepPanel) {
      elGroups.querySelectorAll(".db-chip").forEach((c) => c.setAttribute("aria-pressed", String(!B.off.has(c.dataset.id))));
    } else {
      renderPanel();
    }
    renderTabs();
    renderCounts();
    renderFooter();
  }

  function renderCounts() {
    let total = 0, all = 0;
    GROUPS.forEach(([g]) => { total += onCount(g); all += entries(g).length; });
    elCatTotal.textContent = B.catalog ? `${total} of ${all} in play` : "";
    const match = deckMatch();
    elDecks.querySelectorAll(".db-deck").forEach((d) => d.setAttribute("aria-pressed", String(d.dataset.deck === match)));
  }

  // ── Rules switches ────────────────────────────────────────────────────────
  root.querySelectorAll(".db-switch").forEach((sw) => {
    const key = sw.dataset.key;
    sw.setAttribute("aria-checked", String(B[key]));
    sw.addEventListener("click", () => {
      B[key] = !B[key];
      sw.setAttribute("aria-checked", String(B[key]));
      persist();
    });
  });

  // ── Deal button + summary ────────────────────────────────────────────────
  function renderFooter() {
    const n = B.catalog ? selectedIds().length : 0;
    // Every row and column needs its own category, so a board can't have
    // fewer categories than lines.
    const need = B.rows + B.cols;
    const short = B.catalog && n < need;
    elGo.disabled = B.busy || !B.catalog || short;
    elSummary.classList.toggle("warn", !!short);
    if (!B.catalog) elSummary.textContent = B.loadError ? "Categories unavailable" : "Loading categories…";
    else if (short) elSummary.textContent = `Turn on at least ${need} categories for a ${B.rows}×${B.cols} board (${n} on)`;
    else elSummary.textContent = `${B.rows}×${B.cols} board · ${n} categories`;
  }

  // ══════════════════════════════════════════════════════════════════════════
  // Show / hide / generate
  // ══════════════════════════════════════════════════════════════════════════
  async function showBuilder() {
    building = true;
    document.body.classList.add("draft-building");
    root.hidden = false;
    elError.hidden = true;
    const s = slots.draft;
    elResume.hidden = !s;
    if (s) elResume.textContent = `Back to your ${s.board.n_rows} × ${s.board.n_cols} board`;
    renderSize();
    renderCounts();
    renderFooter();
    root.scrollIntoView({ block: "start", behavior: "auto" });
    if (!B.catalog) await loadCatalog();
  }

  function hideBuilder() {
    building = false;
    document.body.classList.remove("draft-building");
    root.hidden = true;
  }

  elResume.addEventListener("click", () => {
    hideBuilder();
    restore("draft");
  });

  async function loadCatalog() {
    B.loadError = "";
    try {
      B.catalog = await fetchJson(`${state.apiBase}/rules/catalog`);
      // Forget saved "off" ids that no longer exist in the catalog.
      const known = new Set(GROUPS.flatMap(([g]) => entries(g)).map((e) => e.rule_id));
      [...B.off].forEach((id) => { if (!known.has(id)) B.off.delete(id); });
      renderGroups();
    } catch (err) {
      console.error("[draft] /rules/catalog failed:", err);
      B.loadError = err?.message || String(err);
      const p = document.createElement("p");
      p.className = "db-loading";
      p.textContent = `Couldn't load categories (${B.loadError}). Check that the API is running, then reopen Draft Board.`;
      elGroups.replaceChildren(p);
    }
    renderFooter();
  }

  elGo.addEventListener("click", generate);

  async function generate() {
    if (B.busy) return;
    B.busy = true;
    window.PDD_ADS?.boardBreak?.();   // shows only after a finished board, rate-limited
    elError.hidden = true;
    const label = elGoLabel.textContent;
    elGoLabel.textContent = "Dealing…";
    root.classList.add("dealing");
    renderFooter();
    try {
      if (!B.catalog) await loadCatalog();
      const result = await fetchJson(`${state.apiBase}/board/draft`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          allowed_rule_ids: selectedIds(),
          n_rows: B.rows,
          n_cols: B.cols,
          allow_single_answers: B.allowSingle,
          guaranteed_solvable: B.solvable,
        }),
      });
      const board = {
        seed: result.seed, n_rows: result.n_rows, n_cols: result.n_cols,
        row_rule_ids: result.row_rule_ids, col_rule_ids: result.col_rule_ids,
        row_rule_labels: result.row_rule_labels, col_rule_labels: result.col_rule_labels,
        __slot: "draft",
      };
      slots.draft = null;          // a new deal replaces any resumable draft
      hideBuilder();
      window.applyLoadedBoard(board, "api", result.note ? "Draft board loaded (size adjusted)" : "Draft board loaded");
      if (result.note) setMessage(result.note, "var(--warn)");
      boardWrap.scrollIntoView({ block: "nearest" });
    } catch (err) {
      console.error("Draft Board generation failed:", err);
      const msg = err.message || "That combination didn't work out. Try turning on more categories or a smaller size.";
      if (building) {
        elError.textContent = msg;
        elError.hidden = false;
      } else {
        setMessage(msg, "var(--err)");
      }
    } finally {
      B.busy = false;
      elGoLabel.textContent = label;
      root.classList.remove("dealing");
      renderFooter();
    }
  }

  // ── Helpers ───────────────────────────────────────────────────────────────
  function clamp(n) { return Math.max(1, Math.min(MAX, Number(n) || 1)); }
  function shuffle(a) {
    for (let i = a.length - 1; i > 0; i -= 1) {
      const j = Math.floor(Math.random() * (i + 1));
      [a[i], a[j]] = [a[j], a[i]];
    }
    return a;
  }
  function readSaved() {
    try { return JSON.parse(localStorage.getItem(STORE_KEY)) || {}; } catch (_) { return {}; }
  }
})();