/* ============================================================================
   reverse.js — Reverse Board (deduction mode) frontend
   ----------------------------------------------------------------------------
   The forward game: you see the rules, you fill in Pokemon.
   Reverse Board:    you see the Pokemon, you deduce the hidden row/column rule.

   Two sub-modes:
     • Daily   — one shared Hard board per day, locked once solved. Naming a
                 line registers that category to your Category Index AND adds
                 it to every Pokemon in that line's Palette entry.
     • Custom  — difficulty picker + optional category restriction. Practice
                 only; never registers.

   This file deliberately does NOT duplicate app.js. It borrows app.js's
   helpers through window.PDD (a small bridge app.js exposes). If that bridge is
   missing, Reverse Board disables itself rather than throwing.
   ============================================================================ */
(function () {
  "use strict";

  const PDD = window.PDD;
  if (!PDD) {
    console.warn("[reverse] window.PDD bridge missing; Reverse Board disabled.");
    return;
  }

  // Borrowed from app.js so styling/behavior stays identical to the forward game.
  const { state, fetchJson, formatRuleLabel, ruleHeadContent,
          spriteFromPokemonId, setSpriteSafe } = PDD;

  // ── Local state ────────────────────────────────────────────────────────────
  const rev = {
    board: null,          // the current ReverseBoardResponse
    catalog: null,        // { groups: {type:[{rule_id,label}],...} }, cached
    catalogFlat: null,    // [{rule_id, label, group}] for search
    solved: {},           // "row:3" -> {rule_id, label}
    activeLine: null,      // { axis, index } currently being guessed
    isDaily: false,
    poolRuleIds: null,     // Set of rule_ids in this board's pool (easy-mode helper), or null = all
    difficulty: "hard",
    searchText: "",
    groupFilter: "all",
  };

  // ── DOM refs (populated on init) ─────────────────────────────────────────────
  let elWrap, elMeta, elMsg, elProgressText, elProgressBar, elGuessModal;

  // ── Rendering the filled board ───────────────────────────────────────────────
  function lineKey(axis, index) { return `${axis}:${index}`; }

  function renderBoard() {
    const b = rev.board;
    elWrap.innerHTML = "";
    if (!b) return;

    const grid = document.createElement("div");
    grid.className = "board-grid rev-grid";
    grid.setAttribute("role", "grid");
    grid.style.setProperty("--board-cols", String(b.n_cols));

    // Top-left corner
    const corner = document.createElement("div");
    corner.className = "rule-head corner";
    const cb = document.createElement("span");
    cb.className = "rule-badge text";
    cb.textContent = "Guess";
    corner.appendChild(cb);
    grid.appendChild(corner);

    // Column headers — each a clickable "mystery" chip (or the solved label)
    for (let j = 0; j < b.n_cols; j += 1) {
      grid.appendChild(buildHeader("col", j));
    }

    for (let i = 0; i < b.n_rows; i += 1) {
      // Row header
      grid.appendChild(buildHeader("row", i));

      // Filled Pokemon cells (read-only)
      for (let j = 0; j < b.n_cols; j += 1) {
        const cell = document.createElement("div");
        cell.className = "cell rev-cell";
        const mon = b.grid[i][j];
        const img = document.createElement("img");
        img.className = "rev-cell-sprite";
        img.alt = mon.display_name;
        img.title = mon.display_name;
        setSpriteSafe(img, spriteFromPokemonId(mon.pokemon_id));
        const name = document.createElement("span");
        name.className = "rev-cell-name";
        name.textContent = mon.display_name;
        cell.appendChild(img);
        cell.appendChild(name);
        grid.appendChild(cell);
      }
    }

    elWrap.appendChild(grid);
    updateProgress();
  }

  function buildHeader(axis, index) {
    const b = rev.board;
    const head = document.createElement("div");
    head.className = axis === "col" ? "rule-head rev-head" : "row-head rev-head";
    const solved = rev.solved[lineKey(axis, index)];

    if (solved) {
      head.classList.add("rev-solved");
      // Reuse the forward game's badge renderer so a solved Type looks like a
      // real type badge, everything else like a normal rule chip.
      head.appendChild(ruleHeadContent(solved.label, solved.rule_id));
    } else {
      head.classList.add("rev-mystery");
      head.setAttribute("role", "button");
      head.setAttribute("tabindex", "0");
      const q = document.createElement("span");
      q.className = "rule-badge text rev-qmark";
      q.textContent = "?";
      const hint = document.createElement("span");
      hint.className = "rev-tap-hint";
      hint.textContent = "tap to guess";
      head.appendChild(q);
      head.appendChild(hint);
      const open = () => openGuess(axis, index);
      head.addEventListener("click", open);
      head.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); }
      });
    }
    return head;
  }

  function updateProgress() {
    const b = rev.board;
    if (!b) return;
    const total = b.n_rows + b.n_cols;
    const done = Object.keys(rev.solved).length;
    if (elProgressText) elProgressText.textContent = `${done} / ${total}`;
    if (elProgressBar) elProgressBar.style.width = `${total ? (done / total) * 100 : 0}%`;
    if (done === total && total > 0) {
      setMsg(rev.isDaily
        ? "Solved the whole board! Every category logged to your Index."
        : "Board fully solved — nice deduction.", "ok");
    }
  }

  // ── Guess modal ──────────────────────────────────────────────────────────────
  async function ensureCatalog() {
    if (rev.catalog) return;
    const cat = await fetchJson(`${state.apiBase}/rules/catalog`);
    rev.catalog = cat;
    rev.catalogFlat = [];
    Object.entries(cat.groups).forEach(([group, entries]) => {
      entries.forEach((e) => rev.catalogFlat.push({ ...e, group }));
    });
  }

  async function openGuess(axis, index) {
    rev.activeLine = { axis, index };
    try { await ensureCatalog(); }
    catch (e) { setMsg("Couldn't load categories.", "err"); return; }

    rev.searchText = "";
    rev.groupFilter = "all";
    document.getElementById("revGuessSearch").value = "";
    document.getElementById("revGuessTitle").textContent =
      axis === "row" ? `Row ${index + 1} — what do these share?` :
                       `Column ${index + 1} — what do these share?`;
    renderGuessLinePreview(axis, index);
    renderGuessGroupChips();
    renderGuessList();
    setGuessStatus("", "");
    elGuessModal.classList.add("open");
    setTimeout(() => document.getElementById("revGuessSearch").focus(), 60);
  }

  function closeGuess() {
    elGuessModal.classList.remove("open");
    rev.activeLine = null;
  }

  function renderGuessLinePreview(axis, index) {
    const b = rev.board;
    const strip = document.getElementById("revGuessLine");
    strip.innerHTML = "";
    const keys = [];
    if (axis === "row") {
      for (let j = 0; j < b.n_cols; j += 1) keys.push(b.grid[index][j]);
    } else {
      for (let i = 0; i < b.n_rows; i += 1) keys.push(b.grid[i][index]);
    }
    keys.forEach((mon) => {
      const img = document.createElement("img");
      img.className = "rev-line-sprite";
      img.alt = mon.display_name;
      img.title = mon.display_name;
      setSpriteSafe(img, spriteFromPokemonId(mon.pokemon_id));
      strip.appendChild(img);
    });
  }

  const GROUP_LABELS = { all: "All", type: "Types", region: "Regions",
    evolution: "Evolution", move: "Moves", ability: "Abilities", other: "Other" };

  function renderGuessGroupChips() {
    const row = document.getElementById("revGuessGroups");
    row.innerHTML = "";
    const groups = ["all", "type", "region", "evolution", "move", "ability", "other"];
    groups.forEach((g) => {
      if (g !== "all" && (!rev.catalog.groups[g] || rev.catalog.groups[g].length === 0)) return;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "rev-group-chip" + (rev.groupFilter === g ? " active" : "");
      btn.textContent = GROUP_LABELS[g] || g;
      btn.addEventListener("click", () => {
        rev.groupFilter = g;
        renderGuessGroupChips();
        renderGuessList();
      });
      row.appendChild(btn);
    });
  }

  function renderGuessList() {
    const list = document.getElementById("revGuessList");
    list.innerHTML = "";
    const q = rev.searchText.trim().toLowerCase();

    // Easy-mode helper: if the board carries a restricted pool, only show those.
    const poolOk = (rid) => !rev.poolRuleIds || rev.poolRuleIds.has(rid);

    let items = rev.catalogFlat.filter((e) => poolOk(e.rule_id));
    if (rev.groupFilter !== "all") items = items.filter((e) => e.group === rev.groupFilter);
    if (q) items = items.filter((e) => e.label.toLowerCase().includes(q));

    if (items.length === 0) {
      const empty = document.createElement("p");
      empty.className = "rev-guess-empty";
      empty.textContent = "No categories match.";
      list.appendChild(empty);
      return;
    }

    // Cap the render for performance; search narrows it fast.
    const helper = window.PDD_CATEGORY_HELP;
    items.slice(0, 200).forEach((e) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "rev-guess-item";

      // Label row (badge/chip, same as before)
      const labelRow = document.createElement("span");
      labelRow.className = "rev-guess-item-label";
      labelRow.appendChild(ruleHeadContent(e.label, e.rule_id));
      btn.appendChild(labelRow);

      // PokeDoku-style explainer line beneath the label
      const explain = helper ? helper.explain(e.label) : "";
      if (explain) {
        const sub = document.createElement("span");
        sub.className = "rev-guess-item-help";
        sub.textContent = explain;
        btn.appendChild(sub);
      }

      btn.addEventListener("click", () => submitGuess(e));
      list.appendChild(btn);
    });
  }

  async function submitGuess(entry) {
    if (!rev.activeLine || !rev.board) return;
    const { axis, index } = rev.activeLine;
    setGuessStatus("Checking…", "");
    try {
      const res = await fetchJson(`${state.apiBase}/reverse/validate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          board_id: rev.board.board_id, axis, index, guessed_rule_id: entry.rule_id,
        }),
      });
      if (res.ok) {
        rev.solved[lineKey(axis, index)] = {
          rule_id: res.canonical_rule_id, label: res.canonical_label,
        };
        closeGuess();
        renderBoard();
        let m = axis === "row" ? `Row ${index + 1}: ` : `Column ${index + 1}: `;
        m += `${res.canonical_label} ✓`;
        if (res.newly_indexed) m += " — new category added to your Index!";
        setMsg(m, "ok");
      } else if (res.reason === "unknown_board") {
        setGuessStatus("This board expired. Reload the Daily to continue.", "err");
      } else {
        setGuessStatus("Not it — that isn't true of every Pokémon in this line.", "err");
      }
    } catch (e) {
      setGuessStatus(`Error: ${e.message || e}`, "err");
    }
  }

  function setGuessStatus(text, tone) {
    const el = document.getElementById("revGuessStatus");
    if (!el) return;
    el.textContent = text;
    el.className = "rev-guess-status" + (tone ? " " + tone : "");
  }

  // ── Loading boards ───────────────────────────────────────────────────────────
  async function loadDaily() {
    setMsg("Loading today's Reverse Board…");
    try {
      const b = await fetchJson(`${state.apiBase}/board/reverse/daily`);
      rev.board = b;
      rev.isDaily = true;
      rev.difficulty = b.difficulty || "hard";
      rev.poolRuleIds = null;
      rev.solved = {};
      (b.solved_lines || []).forEach((s) => {
        rev.solved[lineKey(s.axis, s.index)] = { rule_id: s.rule_id, label: s.label };
      });
      renderBoard();
      if (elMeta) elMeta.textContent =
        `Daily Reverse (Hard) | ${b.day_seed} | ${b.n_rows}×${b.n_cols}`;
      const done = Object.keys(rev.solved).length;
      setMsg(done > 0
        ? `Welcome back — ${done} line${done === 1 ? "" : "s"} already solved today.`
        : "Deduce the hidden category for each row and column. Locked once solved.", "ok");
    } catch (e) {
      setMsg(`Couldn't load the Daily Reverse Board: ${e.message || e}`, "err");
    }
  }

  async function loadCustom(opts) {
    setMsg("Building a custom Reverse Board…");
    try {
      const body = {
        difficulty: opts.difficulty || "medium",
        n_rows: opts.n_rows || 9,
        n_cols: opts.n_cols || 9,
      };
      if (opts.allowed_rule_ids && opts.allowed_rule_ids.length) {
        body.allowed_rule_ids = opts.allowed_rule_ids;
      }
      const b = await fetchJson(`${state.apiBase}/board/reverse`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      rev.board = b;
      rev.isDaily = false;
      rev.difficulty = b.difficulty;
      // Easy custom boards restrict the guess list to the board's own pool.
      rev.poolRuleIds = (body.difficulty === "easy" && body.allowed_rule_ids)
        ? new Set(body.allowed_rule_ids) : null;
      rev.solved = {};
      renderBoard();
      if (elMeta) elMeta.textContent =
        `Custom Reverse (${b.difficulty}) | ${b.n_rows}×${b.n_cols}`;
      setMsg(b.note || "Custom board — practice only.", "warn");
    } catch (e) {
      const detail = (e && e.detail) ? e.detail : (e.message || e);
      setMsg(`Couldn't build that board: ${detail}`, "err");
    }
  }

  // ── Category Index panel ─────────────────────────────────────────────────────
  async function openIndex() {
    const modal = document.getElementById("revIndexModal");
    modal.classList.add("open");
    const body = document.getElementById("revIndexBody");
    body.innerHTML = "<p class='rev-index-loading'>Loading…</p>";
    try {
      const data = await fetchJson(`${state.apiBase}/reverse/index`);
      document.getElementById("revIndexProgress").textContent =
        `${data.found} / ${data.total} categories found`;
      const bar = document.getElementById("revIndexBar");
      if (bar) bar.style.width = `${data.total ? (data.found / data.total) * 100 : 0}%`;

      body.innerHTML = "";
      const foundWrap = document.createElement("div");
      foundWrap.className = "rev-index-section";
      const fh = document.createElement("h3");
      fh.textContent = `Found (${data.entries.length})`;
      foundWrap.appendChild(fh);
      const fg = document.createElement("div");
      fg.className = "rev-index-chips";
      data.entries.forEach((e) => {
        const chip = document.createElement("span");
        chip.className = "rev-index-chip found";
        chip.textContent = e.label + (e.times_named > 1 ? ` ×${e.times_named}` : "");
        fg.appendChild(chip);
      });
      if (data.entries.length === 0) {
        const none = document.createElement("p");
        none.className = "rev-index-empty";
        none.textContent = "None yet — solve lines on the Daily Reverse Board to fill this in.";
        foundWrap.appendChild(none);
      } else {
        foundWrap.appendChild(fg);
      }
      body.appendChild(foundWrap);

      const remWrap = document.createElement("div");
      remWrap.className = "rev-index-section";
      const rh = document.createElement("h3");
      rh.textContent = `Still to find (${data.remaining_labels.length})`;
      remWrap.appendChild(rh);
      const rg = document.createElement("div");
      rg.className = "rev-index-chips";
      data.remaining_labels.forEach((lab) => {
        const chip = document.createElement("span");
        chip.className = "rev-index-chip remaining";
        chip.textContent = lab;
        rg.appendChild(chip);
      });
      remWrap.appendChild(rg);
      body.appendChild(remWrap);
    } catch (e) {
      body.innerHTML = `<p class='rev-index-empty'>Couldn't load your Index: ${e.message || e}</p>`;
    }
  }

  function closeIndex() {
    document.getElementById("revIndexModal").classList.remove("open");
  }

  // ── Custom panel ─────────────────────────────────────────────────────────────
  function openCustomPanel() { document.getElementById("revCustomModal").classList.add("open"); }
  function closeCustomPanel() { document.getElementById("revCustomModal").classList.remove("open"); }

  // ── Mode switching ───────────────────────────────────────────────────────────
  // Reverse Board takes over the main board area. Entering it hides the forward
  // game's HUD/legend; leaving it restores them. app.js owns the forward view,
  // so we only toggle visibility, never destroy it.
  function enterReverse() {
    document.body.classList.add("reverse-active");
    if (!rev.board) loadDaily();
  }
  function exitReverse() {
    document.body.classList.remove("reverse-active");
    closeGuess();
  }

  // ── Wiring ───────────────────────────────────────────────────────────────────
  function init() {
    elWrap = document.getElementById("revBoardWrap");
    elMeta = document.getElementById("revMeta");
    elMsg = document.getElementById("revMessage");
    elProgressText = document.getElementById("revProgressText");
    elProgressBar = document.getElementById("revProgressBar");
    elGuessModal = document.getElementById("revGuessModal");
    if (!elWrap) { console.warn("[reverse] board wrap missing; markup not injected."); return; }

    // The mode-tab controller (app.js) drives enter/exit; the context bar owns
    // these three buttons. Reverse just binds its own controls here.
    document.getElementById("revDailyBtn")?.addEventListener("click", loadDaily);
    document.getElementById("revCustomBtn")?.addEventListener("click", openCustomPanel);
    document.getElementById("revIndexBtn")?.addEventListener("click", openIndex);

    // Expose the mode hooks so the controller can enter/exit Reverse.
    window.PDD_REVERSE = {
      enter: enterReverse,
      exit: exitReverse,
      loadDaily,
      hasBoard: () => !!rev.board,
    };

    document.getElementById("revGuessClose")?.addEventListener("click", closeGuess);
    document.getElementById("revGuessModal")?.addEventListener("click", (e) => {
      if (e.target.id === "revGuessModal") closeGuess();
    });
    document.getElementById("revGuessSearch")?.addEventListener("input", (e) => {
      rev.searchText = e.target.value;
      renderGuessList();
    });

    document.getElementById("revIndexClose")?.addEventListener("click", closeIndex);
    document.getElementById("revIndexModal")?.addEventListener("click", (e) => {
      if (e.target.id === "revIndexModal") closeIndex();
    });

    document.getElementById("revCustomClose")?.addEventListener("click", closeCustomPanel);
    document.getElementById("revCustomModal")?.addEventListener("click", (e) => {
      if (e.target.id === "revCustomModal") closeCustomPanel();
    });
    document.getElementById("revCustomGo")?.addEventListener("click", () => {
      const difficulty = document.querySelector("input[name='revDiff']:checked")?.value || "medium";
      closeCustomPanel();
      loadCustom({ difficulty, n_rows: 9, n_cols: 9 });
    });

    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        if (elGuessModal.classList.contains("open")) closeGuess();
        else if (document.getElementById("revIndexModal").classList.contains("open")) closeIndex();
        else if (document.getElementById("revCustomModal").classList.contains("open")) closeCustomPanel();
      }
    });
  }

  function setMsg(text, tone) {
    if (!elMsg) return;
    elMsg.textContent = text;
    elMsg.style.color = tone === "ok" ? "var(--ok)" : tone === "err" ? "var(--err)"
      : tone === "warn" ? "var(--warn)" : "";
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();