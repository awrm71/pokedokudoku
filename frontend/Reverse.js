/* ============================================================================
   reverse.js — Reverse Board (deduction mode) frontend
   ----------------------------------------------------------------------------
   The forward game: you see the rules, you fill in Pokemon.
   Reverse Board:    you see the Pokemon, you deduce the hidden row/column rule.

   Two sub-modes:
     • Daily   — one shared Hard board per day, locked once solved. Naming a
                 line registers that category to your Category Index AND adds
                 it to every Pokemon in that line's Pokédex entry.
     • Custom  — difficulty picker + optional category restriction. Practice
                 only; never registers.

   Like the forward board there are lives and a hint: 3 lives, 1 hint per
   board (only 18 lines here vs 81 cells). The server keeps count, so a
   reload can't reset them on the Daily. Running out of lives locks the
   board and reveals the lines that were left.

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
  const MAX_LIVES = 3;
  const MAX_HINTS = 1;

  const rev = {
    board: null,          // the current ReverseBoardResponse
    catalog: null,        // { groups: {type:[{rule_id,label}],...} }, cached
    catalogFlat: null,    // [{rule_id, label, group}] for search
    solved: {},           // "row:3" -> {rule_id, label, hinted}
    revealed: {},         // "row:3" -> {rule_id, label}  (shown after losing)
    tried: {},            // "row:3" -> Set(rule_id) of wrong guesses on that line
    activeLine: null,     // { axis, index } currently being guessed
    isDaily: false,
    poolRuleIds: null,    // Set of rule_ids in this board's pool (easy-mode helper), or null = all
    difficulty: "hard",
    searchText: "",
    groupFilter: "all",
    lives: MAX_LIVES,
    hints: MAX_HINTS,
    gameOver: false,      // out of lives: board locked, answers revealed
    pick: null,           // the category picked in the sheet, waiting for "Lock in"
    foot: "idle",         // sheet footer: "idle" | "pick" | "hint" | "busy"
    busy: false,
  };

  // ── DOM refs (populated on init) ─────────────────────────────────────────────
  let elWrap, elMeta, elMsg, elProgressText, elProgressBar, elGuessModal, elBanner;

  function lineKey(axis, index) { return `${axis}:${index}`; }
  function lineName(axis, index) { return `${axis === "row" ? "Row" : "Column"} ${index + 1}`; }
  function totalLines() { return rev.board ? rev.board.n_rows + rev.board.n_cols : 0; }
  function solvedCount() { return Object.keys(rev.solved).length; }
  function isComplete() { return !!rev.board && solvedCount() === totalLines(); }

  // ── Rendering the filled board ───────────────────────────────────────────────
  function renderBoard() {
    const b = rev.board;
    elWrap.innerHTML = "";
    if (!b) return;

    const grid = document.createElement("div");
    grid.className = "board-grid rev-grid";
    grid.setAttribute("role", "grid");
    grid.style.setProperty("--board-cols", String(b.n_cols));
    if (rev.gameOver) grid.classList.add("rev-over");

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
        // Shiny sighting: a stable per-board roll (shiny.js). Just for fun,
        // Reverse sightings aren't registered in the Shiny Dex.
        const shiny = !!window.PDD_SHINY?.reverseSighting(b, i, j, mon);
        if (shiny) {
          cell.classList.add("shiny");
          img.title = `Shiny ${mon.display_name}`;
        }
        setSpriteSafe(img, shiny ? window.PDD_SHINY.spriteUrl(mon.pokemon_id) : spriteFromPokemonId(mon.pokemon_id));
        const name = document.createElement("span");
        name.className = "rev-cell-name";
        name.textContent = mon.display_name;
        cell.appendChild(img);
        cell.appendChild(name);
        grid.appendChild(cell);
      }
    }

    elWrap.appendChild(grid);
    updateHud();
    window.PDD_SHINY?.announceReverse(b);
  }

  function buildHeader(axis, index) {
    const head = document.createElement("div");
    head.className = axis === "col" ? "rule-head rev-head" : "row-head rev-head";
    head.dataset.axis = axis;
    head.dataset.index = String(index);
    const key = lineKey(axis, index);
    const solved = rev.solved[key];
    const revealed = rev.revealed[key];

    if (solved || revealed) {
      // Reuse the forward game's badge renderer so a solved Type looks like a
      // real type badge, everything else like a normal rule chip.
      const done = solved || revealed;
      head.classList.add("rev-solved");
      head.appendChild(ruleHeadContent(done.label, done.rule_id));
      if (solved?.hinted) {
        head.classList.add("rev-hinted");
        head.title = "Revealed by your hint";
      } else if (revealed) {
        // Lost board: the answer, shown dimmed so it doesn't read as a solve.
        head.classList.add("rev-revealed");
        head.title = "You ran out of lives — this was the answer";
      }
      // Daily: your % in the corner, and a tap shows how everyone named it.
      if (rev.isDaily && window.PDD_STATS) {
        if (solved && !solved.hinted && solved.pct != null) {
          const t = window.PDD_STATS.tag(solved.pct);
          t.title = `${window.PDD_STATS.fmt(solved.pct)} of players named this line the same way`;
          head.appendChild(t);
        }
        head.classList.add("rev-statable");
        head.setAttribute("role", "button");
        head.setAttribute("tabindex", "0");
        head.setAttribute("aria-label", `How everyone named ${lineName(axis, index)}`);
        const open = () => openLineStats(axis, index);
        head.addEventListener("click", open);
        head.addEventListener("keydown", (e) => {
          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); }
        });
      }
    } else {
      head.classList.add("rev-mystery");
      head.setAttribute("role", "button");
      head.setAttribute("tabindex", "0");
      head.setAttribute("aria-label", `Guess ${lineName(axis, index)}`);
      const q = document.createElement("span");
      q.className = "rule-badge text rev-qmark";
      q.textContent = "?";
      const tap = document.createElement("span");
      tap.className = "rev-tap-hint";
      tap.textContent = "tap to guess";
      head.appendChild(q);
      head.appendChild(tap);
      const open = () => openGuess(axis, index);
      head.addEventListener("click", open);
      head.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); }
      });
    }
    return head;
  }

  // ── HUD: lives | progress | hint, plus the end-of-board banner ──────────────
  function paintLives(container, cls) {
    if (!container) return;
    container.querySelectorAll(`.${cls}`).forEach((pip, i) => {
      const alive = i < rev.lives;
      pip.classList.toggle("spent", !alive);
      pip.setAttribute("aria-label", alive ? `Life ${i + 1}` : `Life ${i + 1} lost`);
    });
  }

  function updateHud() {
    const b = rev.board;
    const total = totalLines();
    const done = solvedCount();
    if (elProgressText) elProgressText.textContent = `${done} / ${total || 18}`;
    if (elProgressBar) elProgressBar.style.width = `${total ? (done / total) * 100 : 0}%`;
    document.getElementById("revProgressWrap")?.setAttribute("aria-valuenow", String(done));
    document.getElementById("revProgressWrap")?.setAttribute("aria-valuemax", String(total || 18));

    paintLives(document.getElementById("revLives"), "rev-life");
    const hintBtn = document.getElementById("revHintBtn");
    if (hintBtn) {
      const usable = !!b && rev.hints > 0 && !rev.gameOver && !isComplete();
      hintBtn.classList.toggle("spent", rev.hints <= 0);
      hintBtn.disabled = !usable;
      hintBtn.title = rev.hints > 0
        ? "1 hint: reveals one row or column. Open a line, then tap ⭐ Hint."
        : "Hint used for this board";
    }
    updateBanner();
  }

  function updateBanner() {
    if (!elBanner) return;
    const text = elBanner.querySelector(".hud-alert-text");
    const icon = elBanner.querySelector(".hud-alert-icon");
    const btn = document.getElementById("revBannerBtn");
    const mistakes = MAX_LIVES - rev.lives;
    const usedHint = rev.hints < MAX_HINTS;
    let show = false;

    if (rev.board && rev.gameOver) {
      show = true;
      elBanner.className = "hud-alert hud-alert-dead visible";
      icon.textContent = "💀";
      text.innerHTML = "<strong>Out of lives</strong><span></span>";
      text.lastChild.textContent = rev.isDaily
        ? ` The answers are shown in grey. ${solvedCount()} of ${totalLines()} lines solved. Tap any category to see how everyone named it.`
        : ` The answers are shown in grey. You solved ${solvedCount()} of ${totalLines()}.`;
    } else if (rev.board && isComplete()) {
      show = true;
      elBanner.className = "hud-alert hud-alert-win visible";
      icon.textContent = "🏆";
      text.innerHTML = "<strong>Board solved!</strong><span></span>";
      const bits = [mistakes === 0 ? "No mistakes" : `${mistakes} mistake${mistakes === 1 ? "" : "s"}`,
        usedHint ? "1 hint" : "no hints"];
      text.lastChild.textContent = ` ${bits.join(", ")}.` + (rev.isDaily ? " Tap any category to see how everyone named it." : "");
    }
    if (!show) elBanner.className = "hud-alert";
    elBanner.hidden = !show;
    if (btn) {
      // Daily: nothing left to do today, so offer a practice board instead.
      btn.hidden = !show;
      btn.textContent = rev.isDaily ? "Practice board" : "New board";
    }
  }

  // ── Daily line stats ─────────────────────────────────────────────────────────
  // Lines often accept more than one category, so this shows the spread: how
  // many players named each one, plus the ones that fit but nobody found.
  async function openLineStats(axis, index) {
    if (!rev.isDaily || !window.PDD_STATS) return;
    let st;
    try {
      st = await fetchJson(`${state.apiBase}/reverse/stats`);
    } catch (e) {
      setMsg(`Couldn't load the stats: ${e.message || e}`, "err");
      return;
    }
    const line = st.lines.find((l) => l.axis === axis && l.index === index);
    if (!line || !line.open) return;
    const named = line.answers.filter((a) => a.count > 0).length;
    const unnamed = line.answers.length - named;
    const rows = line.answers.map((a) => {
      const mine = a.rule_id === line.mine;
      const badge = document.createElement("span");
      badge.className = "sp-badge";
      badge.appendChild(ruleHeadContent(a.label, a.rule_id));
      return {
        label: a.label, badge, pct: a.pct,
        mine, mineText: mine && line.hinted ? "your hint" : "you",
        dim: a.count === 0,
        pctText: a.count === 0 ? "—" : undefined,
      };
    });
    const fits = line.answers.length;
    let note = "";
    if (line.hinted) note = "You used your hint on this line, so it isn't counted.";
    else if (unnamed > 0) note = `${unnamed === 1 ? "The faded one fits" : "The faded ones fit"} too — nobody has named ${unnamed === 1 ? "it" : "them"} yet.`;
    window.PDD_STATS.openSheet({
      title: lineName(axis, index),
      sub: (line.total
        ? `${line.total.toLocaleString()} ${line.total === 1 ? "player" : "players"} named this line`
        : "Nobody has named this line yet")
        + (fits > 1 ? ` · ${fits} categories fit` : ""),
      rows,
      note,
    });
  }

  // ── Guess sheet ──────────────────────────────────────────────────────────────
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
    if (!rev.board) return;
    if (rev.gameOver) { setMsg("Out of lives — this board is locked.", "err"); return; }
    if (rev.solved[lineKey(axis, index)]) return;
    rev.activeLine = { axis, index };
    try { await ensureCatalog(); }
    catch (e) { rev.activeLine = null; setMsg("Couldn't load categories.", "err"); return; }

    rev.searchText = "";
    rev.groupFilter = "all";
    rev.pick = null;
    rev.foot = "idle";
    setSearch("");
    const count = axis === "row" ? rev.board.n_cols : rev.board.n_rows;
    document.getElementById("revGuessKicker").textContent = lineName(axis, index);
    document.getElementById("revGuessTitle").textContent = `What do these ${count} share?`;
    document.getElementById("revGuessList").scrollTop = 0;
    renderGuessLinePreview(axis, index);
    renderGuessGroupChips();
    renderGuessList();
    renderFoot();
    setGuessStatus("", "");
    elGuessModal.classList.add("open");
    document.documentElement.classList.add("rg-lock");
    // Phones: don't pop the keyboard over the list; desktop: ready to type.
    if (window.matchMedia("(hover: hover)").matches) {
      setTimeout(() => document.getElementById("revGuessSearch").focus(), 60);
    }
  }

  function closeGuess() {
    if (!elGuessModal) return;
    elGuessModal.classList.remove("open");
    document.documentElement.classList.remove("rg-lock");
    rev.activeLine = null;
    rev.pick = null;
    rev.foot = "idle";
  }

  function setSearch(value) {
    const input = document.getElementById("revGuessSearch");
    if (input) input.value = value;
    rev.searchText = value;
    document.getElementById("revGuessClear")?.toggleAttribute("hidden", !value);
  }

  // The guess picker. Layout: the line's Pokémon up top, search + group tabs,
  // the categories, and a footer that holds lives, the hint and the confirm
  // step. Tapping a category only selects it; "Lock in" sends the guess, so
  // a stray tap never costs a life. Wrong guesses stay crossed out.
  const GROUP_ORDER = ["type", "region", "evolution", "move", "ability", "other"];
  const GROUP_LABELS = { all: "All", type: "Types", region: "Regions",
    evolution: "Evolution", move: "Moves", ability: "Abilities", other: "Other" };

  function renderGuessLinePreview(axis, index) {
    const b = rev.board;
    const strip = document.getElementById("revGuessLine");
    strip.replaceChildren();
    const cells = [];
    if (axis === "row") {
      for (let j = 0; j < b.n_cols; j += 1) cells.push([index, j]);
    } else {
      for (let i = 0; i < b.n_rows; i += 1) cells.push([i, index]);
    }
    cells.forEach(([i, j], n) => {
      const mon = b.grid[i][j];
      const tile = document.createElement("span");
      tile.className = "rg-mon";
      tile.style.setProperty("--n", String(n));
      const img = document.createElement("img");
      img.alt = mon.display_name;
      img.decoding = "async";
      const shiny = !!window.PDD_SHINY?.reverseSighting(b, i, j, mon);
      if (shiny) tile.classList.add("shiny");
      setSpriteSafe(img, shiny ? window.PDD_SHINY.spriteUrl(mon.pokemon_id) : spriteFromPokemonId(mon.pokemon_id));
      const name = document.createElement("span");
      name.className = "rg-mon-name";
      name.textContent = mon.display_name;
      tile.append(img, name);
      strip.appendChild(tile);
    });
  }

  function poolItems() {
    const poolOk = (rid) => !rev.poolRuleIds || rev.poolRuleIds.has(rid);
    return rev.catalogFlat.filter((e) => poolOk(e.rule_id));
  }

  function renderGuessGroupChips() {
    const row = document.getElementById("revGuessGroups");
    row.replaceChildren();
    const items = poolItems();
    ["all", ...GROUP_ORDER].forEach((g) => {
      const n = g === "all" ? items.length : items.filter((e) => e.group === g).length;
      if (!n) return;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "rg-tab";
      btn.setAttribute("role", "tab");
      btn.setAttribute("aria-selected", String(rev.groupFilter === g));
      btn.innerHTML = `<span></span><small>${n}</small>`;
      btn.firstChild.textContent = GROUP_LABELS[g] || g;
      btn.addEventListener("click", () => {
        rev.groupFilter = g;
        renderGuessGroupChips();
        renderGuessList();
        document.getElementById("revGuessList").scrollTop = 0;
        btn.scrollIntoView({ block: "nearest", inline: "nearest" });
      });
      row.appendChild(btn);
    });
  }

  // "Ability: Intimidate" -> { kicker: "Ability", main: "Intimidate" }
  function splitLabel(label) {
    const text = formatRuleLabel(label);
    const m = /^(Ability|Move|Learns|Type|Region|Evolved By)\s*:?\s+(.+)$/i.exec(text);
    return m ? { kicker: m[1], main: m[2] } : { kicker: "", main: text };
  }

  function triedSet(key = rev.activeLine && lineKey(rev.activeLine.axis, rev.activeLine.index)) {
    if (!key) return new Set();
    rev.tried[key] ||= new Set();
    return rev.tried[key];
  }

  // The line a category was already named on ("Row 2"), or "". Each
  // category names one line per board; the server turns reuse away.
  function usedOn(ruleId) {
    const here = rev.activeLine && lineKey(rev.activeLine.axis, rev.activeLine.index);
    for (const [k, v] of Object.entries(rev.solved)) {
      if (k !== here && v.rule_id === ruleId) {
        const [axis, idx] = k.split(":");
        return lineName(axis, Number(idx));
      }
    }
    return "";
  }

  function makeOption(e) {
    const tried = triedSet().has(e.rule_id);
    const used = usedOn(e.rule_id);
    const btn = document.createElement("button");
    btn.type = "button";
    btn.dataset.rule = e.rule_id;
    const typeKey = e.rule_id.startsWith("type_") ? e.rule_id.slice(5) : null;
    const cfg = typeKey && typeof TYPE_CONFIG !== "undefined" ? TYPE_CONFIG[typeKey] : null;

    if (cfg) {
      btn.className = "rg-type";
      btn.style.setProperty("--tb", cfg.bg);
      btn.style.setProperty("--tf", cfg.fg);
      btn.innerHTML = `<span class="rg-type-icon" aria-hidden="true"></span><span></span>`;
      btn.firstChild.textContent = cfg.icon;
      btn.lastChild.textContent = typeKey.charAt(0).toUpperCase() + typeKey.slice(1);
      btn.title = `Must have ${btn.lastChild.textContent} as one of its types.`;
    } else {
      const { kicker, main } = splitLabel(e.label);
      btn.className = "rg-opt";
      btn.innerHTML = `<span class="rg-opt-top"><span class="rg-opt-kicker"></span><span class="rg-opt-main"></span></span><span class="rg-opt-help"></span>`;
      btn.querySelector(".rg-opt-kicker").textContent = kicker;
      btn.querySelector(".rg-opt-main").textContent = main;
      // Moves and abilities explain themselves ("Must be able to learn
      // Surf"), so they get compact tiles without the extra line.
      const explain = (e.group === "move" || e.group === "ability")
        ? "" : (window.PDD_CATEGORY_HELP?.explain(e.label) || "");
      btn.querySelector(".rg-opt-help").textContent = explain;
      if (!explain) btn.querySelector(".rg-opt-help").remove();
    }
    if (used && !tried) {
      // Dimmed with where it went. Still tappable: if it's genuinely the
      // only answer left for this line, the server accepts it.
      btn.classList.add("used");
      btn.title = `Already named on ${used}`;
      const tag = document.createElement("span");
      tag.className = "rg-used";
      tag.textContent = used.replace("Column", "Col");
      btn.appendChild(tag);
    }
    if (tried) {
      btn.classList.add("tried");
      btn.disabled = true;
      btn.setAttribute("aria-label", `${btn.textContent} (already tried)`);
    } else {
      btn.setAttribute("aria-pressed", String(rev.pick?.rule_id === e.rule_id));
      if (rev.pick?.rule_id === e.rule_id) btn.classList.add("picked");
    }
    btn.addEventListener("click", () => choose(e));
    return btn;
  }

  function filteredItems() {
    const q = rev.searchText.trim().toLowerCase();
    let items = poolItems();
    if (rev.groupFilter !== "all") items = items.filter((e) => e.group === rev.groupFilter);
    if (q) {
      items = items.filter((e) => {
        const label = formatRuleLabel(e.label).toLowerCase();
        return label.includes(q) || (GROUP_LABELS[e.group] || "").toLowerCase().includes(q);
      });
    }
    return items;
  }

  function renderGuessList() {
    const list = document.getElementById("revGuessList");
    list.replaceChildren();
    const q = rev.searchText.trim();
    const items = filteredItems();

    if (items.length === 0) {
      const empty = document.createElement("p");
      empty.className = "rg-empty";
      empty.textContent = q ? `Nothing matches "${q}".` : "No categories here.";
      list.appendChild(empty);
      return;
    }

    // One section per group so a long list stays scannable.
    GROUP_ORDER.forEach((g) => {
      const inGroup = items.filter((e) => e.group === g);
      if (!inGroup.length) return;
      const sec = document.createElement("section");
      sec.className = "rg-sec" + (g === "type" ? " rg-sec-types" : "");
      if (rev.groupFilter === "all" || q) {
        const h = document.createElement("h3");
        h.className = "rg-sec-head";
        h.innerHTML = `<span></span><small>${inGroup.length}</small>`;
        h.firstChild.textContent = GROUP_LABELS[g];
        sec.appendChild(h);
      }
      const grid = document.createElement("div");
      grid.className = g === "type" ? "rg-type-grid"
        : (g === "move" || g === "ability") ? "rg-opt-grid compact" : "rg-opt-grid";
      inGroup.forEach((e) => grid.appendChild(makeOption(e)));
      sec.appendChild(grid);
      list.appendChild(sec);
    });
  }

  // Select (or unselect) a category. Nothing is sent until "Lock in".
  function choose(entry) {
    if (rev.busy || triedSet().has(entry.rule_id)) return;
    rev.pick = rev.pick?.rule_id === entry.rule_id ? null : entry;
    rev.foot = rev.pick ? "pick" : "idle";
    syncPicked();
    renderFoot();
  }

  function syncPicked() {
    document.querySelectorAll("#revGuessList [data-rule]").forEach((btn) => {
      const on = rev.pick?.rule_id === btn.dataset.rule;
      btn.classList.toggle("picked", on);
      if (!btn.disabled) btn.setAttribute("aria-pressed", String(on));
    });
  }

  function heartsHtml(cls) {
    let html = "";
    for (let i = 0; i < MAX_LIVES; i += 1) html += `<span class="${cls}" aria-hidden="true">❤️</span>`;
    return html;
  }

  // The footer has four states: idle (lives + hint), pick (confirm the
  // guess), hint (confirm spending the hint) and busy (waiting on the server).
  function renderFoot() {
    const foot = document.getElementById("revGuessFoot");
    if (!foot) return;
    foot.dataset.state = rev.foot;
    foot.replaceChildren();

    const lives = document.createElement("span");
    lives.className = "rg-lives";
    lives.setAttribute("role", "img");
    lives.setAttribute("aria-label", `${rev.lives} of ${MAX_LIVES} lives left`);
    lives.innerHTML = heartsHtml("rg-life");
    paintLives(lives, "rg-life");

    const mid = document.createElement("div");
    mid.className = "rg-foot-mid";
    const actions = document.createElement("div");
    actions.className = "rg-foot-actions";
    const mkBtn = (cls, label, onClick, disabled = false) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = cls;
      b.textContent = label;
      b.disabled = disabled;
      b.addEventListener("click", onClick);
      return b;
    };

    if (rev.foot === "pick" && rev.pick) {
      const { kicker, main } = splitLabel(rev.pick.label);
      mid.innerHTML = `<small class="rg-foot-kicker"></small><strong class="rg-foot-pick"></strong>`;
      mid.firstChild.textContent = rev.lives === 1 ? "Your guess · last life!" : "Your guess";
      mid.lastChild.textContent = kicker ? `${kicker}: ${main}` : main;
      actions.append(
        mkBtn("rg-btn quiet", "Cancel", () => { rev.pick = null; rev.foot = "idle"; syncPicked(); renderFoot(); }),
        mkBtn("rg-btn go", "Lock in", lockIn),
      );
    } else if (rev.foot === "hint") {
      mid.innerHTML = `<small class="rg-foot-kicker">Use your hint?</small><strong class="rg-foot-pick"></strong>`;
      mid.lastChild.textContent = `Reveals this line. ${MAX_HINTS === 1 ? "Only 1 per board" : `${MAX_HINTS} per board`}`
        + (rev.isDaily ? ", and it won't count toward your Index." : ".");
      actions.append(
        mkBtn("rg-btn quiet", "Cancel", () => { rev.foot = rev.pick ? "pick" : "idle"; renderFoot(); }),
        mkBtn("rg-btn star", "Reveal", useHint),
      );
    } else if (rev.foot === "busy") {
      mid.innerHTML = `<small class="rg-foot-kicker">Checking…</small><span class="rg-spinner" aria-hidden="true"></span>`;
    } else {
      mid.innerHTML = `<small class="rg-foot-kicker"></small><span class="rg-foot-tip">Tap a category, then lock it in.</span>`;
      mid.firstChild.textContent = rev.lives === 1 ? "Last life" : `${rev.lives} lives`;
      const hint = mkBtn("rg-btn star-quiet", rev.hints > 0 ? "⭐ Hint" : "Hint used",
        () => { rev.foot = "hint"; renderFoot(); }, rev.hints <= 0);
      hint.title = rev.hints > 0 ? "Reveal this line's category (1 per board)" : "You've used this board's hint";
      actions.append(hint);
    }

    foot.append(lives, mid, actions);
    if (rev.foot === "pick") foot.querySelector(".rg-btn.go")?.focus({ preventScroll: true });
  }

  // Server calls that recover from a restarted server: the Daily board id is
  // fixed per day, so reloading the Daily re-registers it and we retry once.
  async function postReverse(path, payload) {
    const send = () => fetchJson(`${state.apiBase}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...payload, board_id: rev.board.board_id }),
    });
    let res = await send();
    if (res.reason === "unknown_board" && rev.isDaily) {
      const fresh = await fetchJson(`${state.apiBase}/board/reverse/daily`);
      if (fresh.day_seed !== rev.board.day_seed) return { reason: "new_day" };
      res = await send();
    }
    return res;
  }

  function applyStatus(res) {
    if (typeof res.lives_left === "number") rev.lives = res.lives_left;
    if (typeof res.hints_left === "number") rev.hints = res.hints_left;
  }

  async function lockIn() {
    const entry = rev.pick;
    if (!entry || !rev.activeLine || !rev.board || rev.busy) return;
    const { axis, index } = rev.activeLine;
    const key = lineKey(axis, index);
    const btn = document.querySelector(`#revGuessList [data-rule="${CSS.escape(entry.rule_id)}"]`);
    rev.busy = true;
    rev.foot = "busy";
    renderFoot();
    btn?.classList.add("checking");
    try {
      const res = await postReverse("/reverse/validate", { axis, index, guessed_rule_id: entry.rule_id });
      applyStatus(res);
      if (res.ok) {
        rev.solved[key] = { rule_id: res.canonical_rule_id, label: res.canonical_label, pct: res.pct ?? null };
        btn?.classList.add("right");
        setGuessStatus(`${res.canonical_label} ✓`, "ok");
        let m = `${lineName(axis, index)}: ${res.canonical_label} ✓`;
        if (res.pct != null) m += ` (${window.PDD_STATS?.fmt(res.pct) || `${res.pct}%`} named it that)`;
        if (res.newly_indexed) m += " — new category added to your Index!";
        setMsg(m, "ok");
        setTimeout(() => { closeGuess(); renderBoard(); }, 620);
        return;
      }
      if (res.reason === "new_day") {
        setGuessStatus("A new day's board is out. Loading it…", "err");
        setTimeout(() => { closeGuess(); loadDaily(); }, 900);
        return;
      }
      if (res.reason === "unknown_board") {
        setGuessStatus("This practice board expired (the server restarted). Start a new one from Custom.", "err");
        rev.foot = "idle";
        return;
      }
      if (res.reason === "already_used") {
        const [ax, ix] = String(res.used_on || "").split(":");
        const where = ax ? lineName(ax, Number(ix)) : "another line";
        setGuessStatus(`You already named ${splitLabel(entry.label).main} on ${where}. Each category counts once — no life lost.`, "err");
        rev.pick = null;
        rev.foot = "idle";
        btn?.classList.remove("picked");
        return;
      }
      if (res.reason === "out_of_lives") {
        endBoard(res.reveal);
        return;
      }
      // Wrong (or a repeat that didn't cost anything).
      triedSet().add(entry.rule_id);
      rev.pick = null;
      rev.foot = "idle";
      if (btn) {
        btn.classList.remove("picked");
        btn.classList.add("wrong");
        setTimeout(() => {
          btn.classList.remove("wrong");
          btn.classList.add("tried");
          btn.disabled = true;
          btn.removeAttribute("aria-pressed");
        }, 450);
      }
      if (res.game_over) {
        setGuessStatus(`Not ${splitLabel(entry.label).main}. That was your last life.`, "err");
        updateHud();
        setTimeout(() => endBoard(res.reveal), 1100);
        return;
      }
      const left = `${rev.lives} ${rev.lives === 1 ? "life" : "lives"} left`;
      setGuessStatus(res.reason === "repeat"
        ? `Already tried ${splitLabel(entry.label).main}. No life lost.`
        : `Not ${splitLabel(entry.label).main} — ${left}.`, "err");
      bumpLives();
      updateHud();
    } catch (e) {
      setGuessStatus(`Error: ${e.message || e}`, "err");
      rev.foot = rev.pick ? "pick" : "idle";
    } finally {
      rev.busy = false;
      btn?.classList.remove("checking");
      if (elGuessModal.classList.contains("open") && rev.foot !== "busy") renderFoot();
    }
  }

  async function useHint() {
    if (!rev.activeLine || !rev.board || rev.busy || rev.hints <= 0) return;
    const { axis, index } = rev.activeLine;
    rev.busy = true;
    rev.foot = "busy";
    renderFoot();
    try {
      const res = await postReverse("/reverse/hint", { axis, index });
      applyStatus(res);
      if (res.ok) {
        rev.solved[lineKey(axis, index)] = { rule_id: res.rule_id, label: res.label, hinted: true };
        setGuessStatus(`⭐ ${res.label}`, "ok");
        setMsg(`${lineName(axis, index)} revealed: ${res.label}`
          + (rev.isDaily ? " (hinted lines don't count toward your Index)." : "."), "ok");
        setTimeout(() => { closeGuess(); renderBoard(); }, 900);
        return;
      }
      rev.foot = "idle";
      if (res.reason === "new_day") {
        setGuessStatus("A new day's board is out. Loading it…", "err");
        setTimeout(() => { closeGuess(); loadDaily(); }, 900);
      } else if (res.reason === "no_hints") {
        rev.hints = 0;
        setGuessStatus("You've already used this board's hint.", "err");
      } else if (res.reason === "unknown_board") {
        setGuessStatus("This practice board expired (the server restarted). Start a new one from Custom.", "err");
      } else {
        setGuessStatus("Couldn't use the hint here.", "err");
      }
      updateHud();
    } catch (e) {
      rev.foot = "idle";
      setGuessStatus(`Error: ${e.message || e}`, "err");
    } finally {
      rev.busy = false;
      if (elGuessModal.classList.contains("open") && rev.foot !== "busy") renderFoot();
    }
  }

  // A heart breaks: shake the HUD so the loss registers outside the sheet too.
  function bumpLives() {
    const el = document.getElementById("revLives");
    if (!el) return;
    el.classList.remove("hit");
    void el.offsetWidth;
    el.classList.add("hit");
  }

  // Out of lives: lock the board and show what the unsolved lines were.
  function endBoard(reveal) {
    rev.gameOver = true;
    rev.lives = 0;
    rev.revealed = {};
    (reveal || []).forEach((r) => {
      rev.revealed[lineKey(r.axis, r.index)] = { rule_id: r.rule_id, label: r.label };
    });
    closeGuess();
    renderBoard();
    setMsg("💀 Out of lives — board locked.", "err");
    elBanner?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  function setGuessStatus(text, tone) {
    const el = document.getElementById("revGuessStatus");
    if (!el) return;
    el.textContent = text;
    el.hidden = !text;
    el.className = "rg-status" + (tone ? " " + tone : "");
    if (text && tone) {
      el.classList.remove("flash");
      void el.offsetWidth;
      el.classList.add("flash");
    }
  }

  // ── Loading boards ───────────────────────────────────────────────────────────
  // Lives/hint state comes from the server: restored for the Daily (so a
  // reload can't reset it), fresh for a custom board.
  function applyBoard(b) {
    rev.board = b;
    rev.solved = {};
    rev.revealed = {};
    rev.tried = {};
    (b.solved_lines || []).forEach((s) => {
      rev.solved[lineKey(s.axis, s.index)] = {
        rule_id: s.rule_id, label: s.label, hinted: !!s.hinted, pct: s.pct ?? null,
      };
    });
    (b.missed || []).forEach((m) => triedSet(lineKey(m.axis, m.index)).add(m.rule_id));
    rev.lives = typeof b.lives_left === "number" ? b.lives_left : MAX_LIVES;
    rev.hints = typeof b.hints_left === "number" ? b.hints_left : MAX_HINTS;
    rev.gameOver = !!b.game_over;
    (b.reveal || []).forEach((r) => {
      rev.revealed[lineKey(r.axis, r.index)] = { rule_id: r.rule_id, label: r.label };
    });
  }

  async function loadDaily() {
    closeGuess();
    setMsg("Loading today's Reverse Board…");
    try {
      const b = await fetchJson(`${state.apiBase}/board/reverse/daily`);
      applyBoard(b);
      rev.isDaily = true;
      rev.difficulty = b.difficulty || "hard";
      rev.poolRuleIds = null;
      renderBoard();
      if (elMeta) elMeta.textContent =
        `Daily Reverse (Hard) | ${b.day_seed} | ${b.n_rows}×${b.n_cols}`;
      const done = solvedCount();
      if (rev.gameOver) setMsg("Out of lives on today's board. Come back tomorrow!", "err");
      else if (isComplete()) setMsg("Today's board is solved. Come back tomorrow!", "ok");
      else setMsg(done > 0
        ? `Welcome back — ${done} line${done === 1 ? "" : "s"} already solved today.`
        : "Name the hidden category of each row and column. 3 lives, 1 hint.", "ok");
    } catch (e) {
      setMsg(`Couldn't load the Daily Reverse Board: ${e.message || e}`, "err");
    }
  }

  async function loadCustom(opts) {
    closeGuess();
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
      applyBoard(b);
      rev.isDaily = false;
      rev.difficulty = b.difficulty;
      // Easy custom boards restrict the guess list to the board's own pool.
      rev.poolRuleIds = (body.difficulty === "easy" && body.allowed_rule_ids)
        ? new Set(body.allowed_rule_ids) : null;
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
    elBanner = document.getElementById("revBanner");
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

    document.getElementById("revGuessClose")?.addEventListener("click", () => { if (!rev.busy) closeGuess(); });
    document.getElementById("revGuessModal")?.addEventListener("click", (e) => {
      if (e.target.id === "revGuessModal" && !rev.busy) closeGuess();
    });
    const search = document.getElementById("revGuessSearch");
    search?.addEventListener("input", (e) => {
      setSearch(e.target.value);
      renderGuessList();
    });
    // Enter in the search box: with one match left, select it (then Enter
    // again on "Lock in"). With a pick already made, lock it in.
    search?.addEventListener("keydown", (e) => {
      if (e.key !== "Enter") return;
      e.preventDefault();
      if (rev.pick) { lockIn(); return; }
      const open = filteredItems().filter((x) => !triedSet().has(x.rule_id));
      if (open.length === 1) choose(open[0]);
    });
    document.getElementById("revGuessClear")?.addEventListener("click", () => {
      setSearch("");
      renderGuessList();
      search?.focus();
    });

    // HUD star: the hint is spent from inside a line's sheet.
    document.getElementById("revHintBtn")?.addEventListener("click", () => {
      if (rev.activeLine) { rev.foot = "hint"; renderFoot(); return; }
      setMsg("Open a row or column, then tap ⭐ Hint inside it.", "warn");
    });
    // Banner button: a fresh practice board.
    document.getElementById("revBannerBtn")?.addEventListener("click", openCustomPanel);

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
        if (elGuessModal.classList.contains("open")) {
          // First Escape backs out of a confirm step, the next one closes.
          if (rev.foot === "pick" || rev.foot === "hint") {
            rev.pick = null; rev.foot = "idle"; syncPicked(); renderFoot();
          } else if (!rev.busy) closeGuess();
        }
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