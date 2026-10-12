/* ============================================================================
   stats.js — Daily Board answer stats ("how many players picked that?")
   ----------------------------------------------------------------------------
   While playing: every correct answer on the Daily gets a small % tag, the
   share of players who filled that square with the same Pokémon. Under 5% is
   tinted, 1% or less turns gold.

   Once the board ends (filled, out of lives, or ended early with the 📊
   button), a bar above the board switches every square between:
     Yours        your answers and their %
     Most picked  the crowd favourite in each square
     Rarest       the least-picked answer anyone found
   and tapping a square shows its top 5. The server only sends the crowd's
   answers after your board is finished, so they can't be used to solve it.

   The Daily Reverse board uses the same tag and sheet (see Reverse.js).

   Only the Daily has stats (Shuffle and Draft boards are personal). Loads
   after app.js and uses its globals: state, boardWrap, fetchJson,
   setMessage, spriteFromPokemonId, setSpriteSafe, formatRuleLabel.
   ============================================================================ */
(function () {
  "use strict";

  if (typeof state === "undefined" || typeof boardWrap === "undefined") return;

  const VIEWS = [["mine", "Yours"], ["top", "Most picked"], ["rare", "Rarest"]];
  let bar = null;
  let pop = null;
  let loading = null;
  let confirmTimer = 0;
  const btn = document.getElementById("ctxStatsBtn");

  // ── Formatting ────────────────────────────────────────────────────────────
  function fmt(p) {
    if (p == null) return "";
    if (p >= 99.95) return "100%";
    if (p > 0 && p < 0.1) return "<0.1%";
    return `${p < 10 ? p.toFixed(1) : Math.round(p)}%`;
  }
  function tier(p) { return p == null ? "" : p <= 1 ? "gold" : p <= 5 ? "rare" : ""; }
  const num = (n) => Number(n || 0).toLocaleString();

  function isDaily() {
    return !!state.board?.is_daily && state.runtimeMode === "api";
  }
  function finished() {
    return !!state.board?.__finished;
  }

  // Server cells use canonical indices; the page shows a shuffled order.
  function cellElFor(rowI, colJ) {
    const b = state.board;
    const i = b.rowPerm ? b.rowPerm.indexOf(rowI) : rowI;
    const j = b.colPerm ? b.colPerm.indexOf(colJ) : colJ;
    return boardWrap.querySelector(`.cell[data-cell-id="${i}:${j}"]`);
  }
  function canonOf(cellEl) {
    const [i, j] = (cellEl.dataset.cellId || "").split(":").map(Number);
    const b = state.board;
    return { i, j, rowI: b.rowPerm ? b.rowPerm[i] : i, colJ: b.colPerm ? b.colPerm[j] : j };
  }

  // ── The % tag on a square ─────────────────────────────────────────────────
  function tag(pct, extra = "") {
    const t = document.createElement("span");
    t.className = `cs-pct ${tier(pct)} ${extra}`.trim();
    t.textContent = (pct != null && pct <= 1 ? "✦ " : "") + fmt(pct);
    return t;
  }

  function paintPct(cellEl, pct) {
    if (!cellEl) return;
    cellEl.querySelector(":scope > .cs-pct")?.remove();
    if (pct == null || !isDaily()) return;
    const t = tag(pct, "pop");
    t.title = `${fmt(pct)} of players who filled this square picked it`;
    cellEl.appendChild(t);
  }

  // ── Crowd answers drawn over each square ──────────────────────────────────
  function paintCrowd() {
    const st = state.board?.__stats;
    if (!st) return;
    st.cells.forEach((c) => {
      const el = cellElFor(c.row_i, c.col_j);
      if (!el) return;
      el.querySelectorAll(":scope > .cs-alt").forEach((n) => n.remove());
      // Your own % may have moved since you answered; refresh it.
      if (c.mine && !c.mine.hinted) paintPct(el, c.mine.pct);
      [["top", c.top[0]], ["rare", c.rarest]].forEach(([kind, pick]) => {
        const alt = document.createElement("div");
        alt.className = `cs-alt cs-${kind}`;
        if (!pick) {
          alt.classList.add("empty");
          alt.innerHTML = `<span class="cs-none">No answers yet</span>`;
        } else {
          const img = document.createElement("img");
          img.alt = "";
          img.decoding = "async";
          setSpriteSafe(img, spriteFromPokemonId(pick.pokemon_id));
          const name = document.createElement("span");
          name.className = "cs-name";
          name.textContent = pick.display_name;
          alt.append(img, name, tag(pick.pct));
          if (c.mine && c.mine.key === pick.key) {
            alt.classList.add("same");
            const you = document.createElement("span");
            you.className = "cs-you";
            you.textContent = "You";
            alt.appendChild(you);
          }
        }
        el.appendChild(alt);
      });
    });
  }

  // ── The bar above the board ───────────────────────────────────────────────
  function ensureBar() {
    if (bar) return bar;
    bar = document.createElement("section");
    bar.className = "sb";
    bar.id = "statsBar";
    bar.setAttribute("aria-label", "Today's answer stats");
    bar.hidden = true;
    bar.innerHTML =
      `<div class="sb-text">` +
        `<p class="sb-kicker">Today's answers</p>` +
        `<p class="sb-line"></p>` +
      `</div>` +
      `<div class="sb-seg" role="tablist" aria-label="Show"></div>` +
      `<p class="sb-tip">Tap any square for its top 5.</p>`;
    const seg = bar.querySelector(".sb-seg");
    VIEWS.forEach(([key, label]) => {
      const b = document.createElement("button");
      b.type = "button";
      b.dataset.view = key;
      b.setAttribute("role", "tab");
      b.textContent = label;
      b.addEventListener("click", () => setView(key));
      seg.appendChild(b);
    });
    boardWrap.parentNode.insertBefore(bar, boardWrap);
    return bar;
  }

  function fillBar() {
    const st = state.board.__stats;
    const line = bar.querySelector(".sb-line");
    line.replaceChildren();
    const add = (strong, rest) => {
      const s = document.createElement("span");
      s.innerHTML = "<b></b>";
      s.firstChild.textContent = strong;
      s.append(rest);
      line.appendChild(s);
    };
    add(num(st.players), st.players === 1 ? " player so far" : " players today");
    if (st.avg_pct != null) add(fmt(st.avg_pct), " your average");
    // Your rarest find is the fun bit, so it gets named.
    const mine = st.cells.map((c) => c.mine).filter((m) => m && !m.hinted && m.pct != null);
    if (mine.length && st.players > 1) {
      const best = mine.reduce((a, b) => (b.pct < a.pct ? b : a));
      add(best.display_name, ` ${fmt(best.pct)}, your rarest`);
    }
  }

  function setView(key) {
    const grid = boardWrap.querySelector(":scope > .board-grid");
    if (!grid) return;
    state.board.__view = key;
    grid.classList.remove("sv-mine", "sv-top", "sv-rare");
    grid.classList.add(`sv-${key}`);
    bar?.querySelectorAll(".sb-seg button").forEach((b) => {
      b.setAttribute("aria-selected", String(b.dataset.view === key));
    });
  }

  // Shows or hides everything to match the board on screen. app.js calls this
  // from updateHUD, which also runs when draft.js swaps Daily/Draft boards.
  function sync() {
    const daily = isDaily();
    if (btn) {
      btn.hidden = !daily;
      if (!daily) resetConfirm();
    }
    const has = daily && !!state.board.__stats;
    boardWrap.classList.toggle("has-stats", has);
    if (!has) { if (bar) bar.hidden = true; closePop(); return; }
    ensureBar();
    fillBar();
    bar.hidden = false;
    setView(state.board.__view || "mine");
  }

  // ── Board end ─────────────────────────────────────────────────────────────
  async function boardEnded() {
    if (!isDaily()) return;
    const board = state.board;
    board.__finished = true;
    if (loading) return loading;
    loading = (async () => {
      try {
        const st = await fetchJson(`${state.apiBase}/daily/stats`);
        if (state.board !== board) return;
        apply(board, st);
      } catch (e) {
        // Not finished on the server yet (shouldn't happen) or offline.
        console.warn("[stats]", e);
      } finally {
        loading = null;
      }
    })();
    return loading;
  }

  function apply(board, st) {
    board.__stats = st;
    board.__finished = true;
    paintCrowd();
    sync();
  }

  // End the board early: two taps on 📊 (the first one asks).
  async function endEarly() {
    const board = state.board;
    try {
      const st = await fetchJson(`${state.apiBase}/daily/finish`, { method: "POST" });
      if (state.board !== board) return;
      state.gameOver = true;
      boardWrap.classList.add("board-locked");
      document.querySelectorAll(".pip-hint").forEach((p) => { p.disabled = true; });
      if (typeof closeCellModal === "function") closeCellModal();
      apply(board, st);
      setMessage("Board ended. Here's how everyone answered.", "var(--ok)");
      bar?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    } catch (e) {
      setMessage(`Couldn't end the board: ${e.message || e}`, "var(--err)");
    }
  }

  function resetConfirm() {
    clearTimeout(confirmTimer);
    if (!btn) return;
    btn.classList.remove("confirm");
    btn.textContent = "📊 Stats";
  }

  btn?.addEventListener("click", () => {
    if (!isDaily()) return;
    if (state.board.__stats) {
      boardEnded();                                   // refresh the numbers
      bar?.scrollIntoView({ block: "nearest", behavior: "smooth" });
      return;
    }
    if (finished() || state.gameOver) { boardEnded(); return; }
    if (btn.classList.contains("confirm")) { resetConfirm(); endEarly(); return; }
    btn.classList.add("confirm");
    btn.textContent = "End board?";
    setMessage("Tap again to end today's board and see everyone's answers. You can't add more after.", "var(--warn)");
    confirmTimer = setTimeout(resetConfirm, 4000);
  });

  // ── Square details: top 5 ─────────────────────────────────────────────────
  function closePop() {
    if (!pop) return;
    pop.remove();
    pop = null;
  }

  // A small sheet with a ranked list. Shared with the Reverse board (lines
  // instead of squares, categories instead of Pokémon).
  //   rows: [{ label, pct, img?, badge?, mine?, mineText?, dim?, gapBefore? }]
  function openSheet({ title, sub, rows, empty = "", note = "" }) {
    closePop();
    pop = document.createElement("div");
    pop.className = "sp";
    pop.setAttribute("role", "dialog");
    pop.setAttribute("aria-modal", "true");
    pop.setAttribute("aria-label", title);
    pop.innerHTML =
      `<div class="sp-card">` +
        `<header class="sp-head"><div><p class="sp-rules"></p><p class="sp-sub"></p></div>` +
        `<button type="button" class="sp-close" aria-label="Close">✕</button></header>` +
        `<ol class="sp-list"></ol>` +
        `<p class="sp-note" hidden></p>` +
      `</div>`;
    pop.querySelector(".sp-rules").textContent = title;
    pop.querySelector(".sp-sub").textContent = sub || "";
    const list = pop.querySelector(".sp-list");
    rows.forEach((r) => {
      if (r.gapBefore) {
        const sep = document.createElement("li");
        sep.className = "sp-gap";
        sep.setAttribute("aria-hidden", "true");
        list.appendChild(sep);
      }
      const li = document.createElement("li");
      li.className = "sp-row" + (r.mine ? " mine" : "") + (r.dim ? " dim" : "") + (r.img ? "" : " no-img");
      li.style.setProperty("--w", `${r.pct ? Math.max(2, r.pct) : 0}%`);
      if (r.img) {
        const img = document.createElement("img");
        img.alt = "";
        setSpriteSafe(img, r.img);
        li.appendChild(img);
      }
      const name = document.createElement("span");
      name.className = "sp-name";
      if (r.badge) name.appendChild(r.badge);
      else name.append(r.label);
      if (r.mine) {
        const you = document.createElement("small");
        you.textContent = r.mineText || "you";
        name.appendChild(you);
      }
      const pct = document.createElement("span");
      pct.className = `sp-pct ${r.dim ? "" : tier(r.pct)}`;
      pct.textContent = r.pctText ?? fmt(r.pct);
      li.append(name, pct);
      list.appendChild(li);
    });
    if (!rows.length && empty) {
      const li = document.createElement("li");
      li.className = "sp-empty";
      li.textContent = empty;
      list.appendChild(li);
    }
    if (note) {
      const n = pop.querySelector(".sp-note");
      n.textContent = note;
      n.hidden = false;
    }
    pop.addEventListener("click", (e) => { if (e.target === pop) closePop(); });
    pop.querySelector(".sp-close").addEventListener("click", closePop);
    document.body.appendChild(pop);
    pop.querySelector(".sp-close").focus({ preventScroll: true });
  }

  function openPop(cellEl) {
    const st = state.board?.__stats;
    if (!st) return;
    const { i, j, rowI, colJ } = canonOf(cellEl);
    const c = st.cells.find((x) => x.row_i === rowI && x.col_j === colJ);
    if (!c) return;
    const rowL = formatRuleLabel(state.board.row_rule_labels[i]);
    const colL = formatRuleLabel(state.board.col_rule_labels[j]);
    const rows = c.top.map((p) => ({
      label: p.display_name, pct: p.pct, img: spriteFromPokemonId(p.pokemon_id),
      mine: !!c.mine && c.mine.key === p.key,
    }));
    // Your answer, if it isn't already in the top 5.
    if (c.mine && !c.top.some((p) => p.key === c.mine.key)) {
      rows.push({
        label: c.mine.display_name, pct: c.mine.pct, img: spriteFromPokemonId(c.mine.pokemon_id),
        mine: true, mineText: c.mine.hinted ? "your hint" : "you",
        pctText: c.mine.hinted ? "—" : undefined, gapBefore: true,
      });
    }
    openSheet({
      title: [rowL, colL].filter((x) => x && x.trim()).join(" × "),
      sub: c.total ? `${num(c.total)} ${c.total === 1 ? "player" : "players"} filled this square`
        : "Nobody has filled this square yet",
      rows,
      empty: "Be the first tomorrow?",
    });
  }

  // Squares are only clickable for details once stats are showing (app.js
  // ignores clicks on a finished board, so there's no clash).
  boardWrap.addEventListener("click", (e) => {
    if (!boardWrap.classList.contains("has-stats")) return;
    const cellEl = e.target.closest(".cell");
    if (cellEl && boardWrap.contains(cellEl)) openPop(cellEl);
  });
  boardWrap.addEventListener("keydown", (e) => {
    if (!boardWrap.classList.contains("has-stats") || (e.key !== "Enter" && e.key !== " ")) return;
    const cellEl = e.target.closest(".cell");
    if (cellEl) { e.preventDefault(); openPop(cellEl); }
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closePop(); });

  window.PDD_STATS = { paintPct, boardEnded, sync, apply, openSheet, closePop, tag, fmt, tier };
  sync();
})();