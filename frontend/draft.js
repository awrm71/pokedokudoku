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
  // ══════════════════════════════════════════════════════════════════════════
  const root = document.createElement("section");
  root.id = "draftBuilder";
  root.className = "db";
  root.hidden = true;
  root.setAttribute("aria-labelledby", "dbTitle");
  root.innerHTML = `
    <header class="db-head">
      <h2 id="dbTitle" class="db-title">Draft Board</h2>
      <p class="db-lede">Pick a size and the categories you want in play. We'll deal a board built only from them.</p>
      <button type="button" class="db-resume" hidden></button>
    </header>

    <section class="db-step" aria-labelledby="dbSizeH">
      <div class="db-step-head">
        <h3 id="dbSizeH">Board size</h3>
        <span class="db-size-read" aria-live="polite"></span>
      </div>
      <div class="db-size">
        <div class="db-size-grid" role="group" aria-label="Board size picker. Use arrow keys to change rows and columns."></div>
        <div class="db-presets" role="group" aria-label="Size presets"></div>
      </div>
    </section>

    <section class="db-step" aria-labelledby="dbCatH">
      <div class="db-step-head">
        <h3 id="dbCatH">Categories</h3>
        <span class="db-cat-total"></span>
      </div>
      <div class="db-groups"><p class="db-loading">Loading categories…</p></div>
    </section>

    <section class="db-step" aria-labelledby="dbRulesH">
      <div class="db-step-head"><h3 id="dbRulesH">Rules</h3></div>
      <div class="db-switches">
        <button type="button" class="db-switch" role="switch" data-key="allowSingle">
          <span class="db-switch-track"><span class="db-switch-knob"></span></span>
          <span class="db-switch-text">
            <strong>Allow single-answer cells</strong>
            <small>Some cells may have only one correct Pokémon. Turn off for a gentler board where every cell has at least two.</small>
          </span>
        </button>
        <button type="button" class="db-switch" role="switch" data-key="solvable">
          <span class="db-switch-track"><span class="db-switch-knob"></span></span>
          <span class="db-switch-text">
            <strong>Guaranteed solvable</strong>
            <small>We check that a full solution exists before dealing. Turn off for stranger combinations that might not be completely fillable.</small>
          </span>
        </button>
      </div>
    </section>

    <p class="db-error" role="alert" hidden></p>

    <div class="db-footer">
      <p class="db-summary" aria-live="polite"></p>
      <button type="button" class="db-go">Draft this board</button>
    </div>
  `;

  const anchor = document.getElementById("gameHud") || boardWrap;
  anchor.parentNode.insertBefore(root, anchor);

  const $ = (sel) => root.querySelector(sel);
  const elResume = $(".db-resume");
  const elSizeGrid = $(".db-size-grid");
  const elPresets = $(".db-presets");
  const elSizeRead = $(".db-size-read");
  const elGroups = $(".db-groups");
  const elCatTotal = $(".db-cat-total");
  const elError = $(".db-error");
  const elSummary = $(".db-summary");
  const elGo = $(".db-go");

  // ── Size picker: paint the board shape on a 9×9 grid ─────────────────────
  const sizeCells = [];
  for (let r = 0; r < MAX; r += 1) {
    for (let c = 0; c < MAX; c += 1) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "db-sq";
      b.dataset.r = String(r);
      b.dataset.c = String(c);
      b.setAttribute("aria-label", `${r + 1} rows by ${c + 1} columns`);
      b.addEventListener("click", () => setSize(r + 1, c + 1));
      b.addEventListener("pointerenter", (e) => { if (e.pointerType === "mouse") preview(r + 1, c + 1); });
      elSizeGrid.appendChild(b);
      sizeCells.push(b);
    }
  }
  elSizeGrid.addEventListener("pointerleave", () => preview(0, 0));
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
    b.textContent = `${r} × ${c}`;
    b.addEventListener("click", () => setSize(r, c));
    elPresets.appendChild(b);
  });

  function setSize(r, c) {
    B.rows = clamp(r);
    B.cols = clamp(c);
    persist();
    renderSize();
    renderFooter();
  }

  function preview(r, c) {
    sizeCells.forEach((b) => {
      const inside = r > 0 && +b.dataset.r < r && +b.dataset.c < c;
      b.classList.toggle("pv", inside);
    });
    elSizeGrid.classList.toggle("previewing", r > 0);
  }

  function renderSize() {
    sizeCells.forEach((b) => {
      const r = +b.dataset.r, c = +b.dataset.c;
      b.classList.toggle("on", r < B.rows && c < B.cols);
      const corner = r === B.rows - 1 && c === B.cols - 1;
      b.classList.toggle("corner", corner);
      b.tabIndex = corner ? 0 : -1;
      b.setAttribute("aria-pressed", String(corner));
    });
    elPresets.querySelectorAll(".db-preset").forEach((b) => {
      b.setAttribute("aria-pressed", String(b.dataset.size === `${B.rows}x${B.cols}`));
    });
    const cells = B.rows * B.cols;
    elSizeRead.textContent = `${B.rows} × ${B.cols}, ${cells} cell${cells === 1 ? "" : "s"}`;
  }

  // ── Category groups: accordion of chip sets ──────────────────────────────
  function renderGroups() {
    elGroups.replaceChildren();
    GROUPS.forEach(([g, label]) => {
      if (!entries(g).length) return;
      const wrap = document.createElement("div");
      wrap.className = "db-group";
      wrap.dataset.group = g;
      wrap.innerHTML = `
        <button type="button" class="db-group-head" aria-expanded="false">
          <span class="db-g-name">${label}</span>
          <span class="db-g-count"></span>
          <span class="db-g-meter" aria-hidden="true"><i></i></span>
          <span class="db-g-caret" aria-hidden="true"></span>
        </button>
        <div class="db-group-body" hidden>
          <div class="db-tools">
            ${SEARCHABLE.has(g) ? `<input type="search" class="db-search" placeholder="Search ${label.toLowerCase()}" aria-label="Search ${label.toLowerCase()}">` : ""}
            <button type="button" class="db-bulk" data-bulk="all">All on</button>
            <button type="button" class="db-bulk" data-bulk="none">All off</button>
          </div>
          <div class="db-chips"></div>
        </div>`;
      wrap.querySelector(".db-group-head").addEventListener("click", () => toggleGroup(g));
      wrap.querySelectorAll(".db-bulk").forEach((b) => b.addEventListener("click", () => bulk(g, b.dataset.bulk === "all")));
      wrap.querySelector(".db-search")?.addEventListener("input", (e) => filterChips(g, e.target.value));
      wrap.querySelector(".db-chips").addEventListener("click", (e) => {
        const chip = e.target.closest(".db-chip");
        if (!chip) return;
        const id = chip.dataset.id;
        if (B.off.has(id)) B.off.delete(id); else B.off.add(id);
        chip.setAttribute("aria-pressed", String(!B.off.has(id)));
        persist();
        renderCounts();
        renderFooter();
      });
      elGroups.appendChild(wrap);
    });
    renderCounts();
  }

  function groupEl(g) { return elGroups.querySelector(`.db-group[data-group="${g}"]`); }

  function toggleGroup(g) {
    const next = B.open === g ? null : g;
    if (B.open) {
      const prev = groupEl(B.open);
      prev.querySelector(".db-group-head").setAttribute("aria-expanded", "false");
      prev.querySelector(".db-group-body").hidden = true;
      prev.classList.remove("open");
    }
    B.open = next;
    if (!next) return;
    const el = groupEl(next);
    el.classList.add("open");
    el.querySelector(".db-group-head").setAttribute("aria-expanded", "true");
    el.querySelector(".db-group-body").hidden = false;
    renderChips(next);
    // On phones the opened list can push the header off-screen.
    el.scrollIntoView({ block: "nearest", behavior: prefersReducedMotion() ? "auto" : "smooth" });
  }

  function renderChips(g) {
    const box = groupEl(g).querySelector(".db-chips");
    if (box.childElementCount) { syncChips(g); return; }
    const help = window.PDD_CATEGORY_HELP;
    entries(g).forEach((e) => {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "db-chip";
      chip.dataset.id = e.rule_id;
      const display = formatRuleLabel(e.label);
      chip.dataset.search = normalizeName(display);
      const typeKey = e.rule_id.startsWith("type_") ? e.rule_id.slice(5) : null;
      const cfg = typeKey && TYPE_CONFIG[typeKey];
      if (cfg) {
        chip.classList.add("db-chip-type");
        chip.style.setProperty("--t-bg", cfg.bg);
        chip.style.setProperty("--t-fg", cfg.fg);
        chip.innerHTML = `<span class="db-chip-icon" aria-hidden="true"></span><span class="db-chip-label"></span>`;
        chip.firstChild.textContent = cfg.icon;
        chip.lastChild.textContent = typeKey.charAt(0).toUpperCase() + typeKey.slice(1);
      } else {
        chip.innerHTML = `<span class="db-chip-label"></span>`;
        chip.firstChild.textContent = display;
      }
      const explain = help?.explain?.(display);
      if (explain) chip.title = explain;
      chip.setAttribute("aria-pressed", String(!B.off.has(e.rule_id)));
      box.appendChild(chip);
    });
  }

  function syncChips(g) {
    groupEl(g).querySelectorAll(".db-chip").forEach((chip) => {
      chip.setAttribute("aria-pressed", String(!B.off.has(chip.dataset.id)));
    });
  }

  function filterChips(g, q) {
    const needle = normalizeName(q || "");
    groupEl(g).querySelectorAll(".db-chip").forEach((chip) => {
      chip.hidden = !!needle && !chip.dataset.search.includes(needle);
    });
  }

  function bulk(g, on) {
    // Only affects chips currently visible, so "search 'fire' → All off" works.
    const el = groupEl(g);
    const visible = Array.from(el.querySelectorAll(".db-chip:not([hidden])")).map((c) => c.dataset.id);
    const ids = visible.length ? visible : entries(g).map((e) => e.rule_id);
    ids.forEach((id) => (on ? B.off.delete(id) : B.off.add(id)));
    persist();
    syncChips(g);
    renderCounts();
    renderFooter();
  }

  function renderCounts() {
    let total = 0, all = 0;
    GROUPS.forEach(([g]) => {
      const el = groupEl(g);
      if (!el) return;
      const n = onCount(g), m = entries(g).length;
      total += n; all += m;
      el.querySelector(".db-g-count").textContent = `${n} of ${m}`;
      el.querySelector(".db-g-meter i").style.width = `${m ? (n / m) * 100 : 0}%`;
      el.classList.toggle("empty", n === 0);
    });
    elCatTotal.textContent = B.catalog ? `${total} of ${all} on` : "";
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

  // ── Footer ────────────────────────────────────────────────────────────────
  function renderFooter() {
    const n = B.catalog ? selectedIds().length : 0;
    // Every row and column needs its own category, so a board can't have
    // fewer categories than lines.
    const need = B.rows + B.cols;
    const short = B.catalog && n < need;
    elGo.disabled = B.busy || !B.catalog || short;
    elSummary.classList.toggle("warn", !!short);
    if (!B.catalog) elSummary.textContent = B.loadError ? "Categories unavailable" : "Loading categories…";
    else if (short) elSummary.textContent = `Turn on at least ${need} categories for a ${B.rows} × ${B.cols} board (${n} on now).`;
    else elSummary.textContent = `${B.rows} × ${B.cols} board from ${n} categories`;
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
    elError.hidden = true;
    const label = elGo.textContent;
    elGo.textContent = "Dealing your board…";
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
      elGo.textContent = label;
      renderFooter();
    }
  }

  // ── Helpers ───────────────────────────────────────────────────────────────
  function clamp(n) { return Math.max(1, Math.min(MAX, Number(n) || 1)); }
  function prefersReducedMotion() { return window.matchMedia("(prefers-reduced-motion: reduce)").matches; }
  function readSaved() {
    try { return JSON.parse(localStorage.getItem(STORE_KEY)) || {}; } catch (_) { return {}; }
  }
})();