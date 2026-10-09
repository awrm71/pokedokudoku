/* ============================================================================
   mobile.js — phone layouts for the Daily/Draft board and the Reverse board
   ----------------------------------------------------------------------------
   On screens ≤767px, boards bigger than 5×5 switch to a "focus" layout:

     ┌ Rows | Columns | Full grid ┐   view switch (remembered per board kind)
     ┌─────────┐  Row 3 of 9
     │ overview│  [ LEARN EARTHQUAKE ]   ← Reverse: "?" + Guess button
     │   map   │  rule explainer
     │ (tap to │  4 of 9 filled  ▓▓▓░░
     │  jump)  │  ‹  ›
     └─────────┘
     ┌────┬────┬────┐
     │tile│tile│tile│   the focused row (or column) as big tiles, each
     ├────┼────┼────┤   labelled with the OTHER axis' rule; swipe to move
     └────┴────┴────┘

   Boards 5×5 and smaller (Draft) skip all that and render as a grid scaled to
   fit the screen: no horizontal scroll, headers always visible.

   Like Reverse.js, this never duplicates app.js or Reverse.js. It re-uses the
   real cell/header elements and only toggles classes and injects a small rule
   chip into each tile. Clicks, modals, validation and solving stay owned by
   the original files. Both grids get rebuilt by their owners (board loads,
   and Reverse re-renders after every solve), so each controller watches its
   wrap and re-attaches, keeping the focused line.
   ============================================================================ */
(function () {
  "use strict";

  const MQ = window.matchMedia("(max-width: 767px)");
  const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)");
  const FIT_MAX = 5;   // boards with both sides ≤ this render as a fitted grid
  const VIEWS = [["rows", "Rows"], ["cols", "Columns"], ["grid", "Full grid"]];

  // ── Forward board (Daily + Draft) ──────────────────────────────────────────
  const forward = {
    kind: "fwd",
    wrapId: "boardWrap",
    storeKey: "pdd.mobileView",
    gridSelector: ":scope > .board-grid:not(.rev-grid)",
    showCount: true,

    decorate(B) {
      // Cells persist for the board's lifetime, so chips are injected once.
      forEachCell(B, (cell, r, c) => {
        if (cell.querySelector(".m-chip")) return;
        cell.insertBefore(chip(cloneHead(B.rowHeads[r]), "m-chip-row"), cell.firstChild);
        cell.insertBefore(chip(cloneHead(B.colHeads[c]), "m-chip-col"), cell.firstChild);
      });
    },

    cellState(cell) {
      const cl = cell.classList;
      const filled = cl.contains("locked") || cl.contains("ok") || cl.contains("hint-cell");
      const sprite = cell.querySelector(".summary-sprite");
      return {
        filled,
        state: cl.contains("hint-cell") ? "hint" : filled ? "ok"
          : cl.contains("err") ? "err" : cl.contains("drafted") ? "draft" : "",
        src: filled && sprite && !sprite.classList.contains("hidden") ? sprite.src : "",
        name: filled ? text(cell.querySelector(".picked-name")) : "",
      };
    },

    info(B, axis, i, ui) {
      const head = axis === "row" ? B.rowHeads[i] : B.colHeads[i];
      ui.rule.replaceChildren(...cloneHead(head));
      const labels = window.PDD?.state?.board?.[axis === "row" ? "row_rule_labels" : "col_rule_labels"];
      ui.setHelp(labels ? explain(labels[i]) : "");
      ui.guess.hidden = true;
      ui.status.hidden = true;
    },
  };

  // ── Reverse board ──────────────────────────────────────────────────────────
  const reverse = {
    kind: "rev",
    wrapId: "revBoardWrap",
    storeKey: "pdd.mobileView.rev",
    gridSelector: ":scope > .board-grid.rev-grid",
    showCount: false,

    decorate(B) {
      // Reverse rebuilds the grid after each solve, so chips are always fresh.
      forEachCell(B, (cell, r, c) => {
        cell.insertBefore(chip(revChip(B.rowHeads[r]), "m-chip-row"), cell.firstChild);
        cell.insertBefore(chip(revChip(B.colHeads[c]), "m-chip-col"), cell.firstChild);
      });
    },

    cellState(cell, r, c, B) {
      const solved = isSolved(B.rowHeads[r]) + isSolved(B.colHeads[c]);
      const sprite = cell.querySelector(".rev-cell-sprite");
      return {
        filled: true,
        state: solved === 2 ? "ok" : solved === 1 ? "half" : "",
        src: sprite ? sprite.src : "",
        name: text(cell.querySelector(".rev-cell-name")),
      };
    },

    info(B, axis, i, ui) {
      const head = axis === "row" ? B.rowHeads[i] : B.colHeads[i];
      const solved = isSolved(head);
      ui.status.hidden = false;
      ui.status.textContent = solved ? "Solved" : "Unsolved";
      ui.status.classList.toggle("done", solved);
      if (solved) {
        ui.rule.replaceChildren(...cloneHead(head));
        ui.setHelp(explain(headLabel(head)));
        ui.guess.hidden = true;
      } else {
        ui.rule.replaceChildren(qmark());
        ui.setHelp("");
        ui.guess.hidden = false;
        ui.guess.textContent = axis === "row" ? "Guess this row" : "Guess this column";
        // The real header is hidden in focus view but still owns the guess
        // flow, so the button just clicks it — Reverse.js does the rest.
        ui.guess.onclick = () => head.click();
      }
    },
  };

  [forward, reverse].forEach(createController);

  // ════════════════════════════════════════════════════════════════════════════
  function createController(A) {
    const wrap = document.getElementById(A.wrapId);
    if (!wrap) return;

    let view = readView(A.storeKey);
    const pos = { rows: 0, cols: 0 };
    let B = null;
    let gridObs = null;
    let raf = 0;

    // ── UI (lives outside the wrap so board rebuilds don't wipe it) ──────────
    const bar = el("div", `m-bar m-bar-${A.kind}`);
    const views = el("div", "m-views");
    views.setAttribute("role", "group");
    views.setAttribute("aria-label", "Board view");
    const viewBtns = VIEWS.map(([key, label]) => {
      const b = button("m-view-btn", label);
      b.dataset.view = key;
      b.addEventListener("click", () => setView(key));
      views.appendChild(b);
      return b;
    });

    const dash = el("div", "m-dash");
    const mini = el("div", "m-mini");
    mini.setAttribute("role", "group");
    mini.setAttribute("aria-label", "Board overview — tap a square to jump to it");
    const band = el("div", "m-band");
    band.setAttribute("aria-hidden", "true");

    const info = el("div", "m-info");
    const top = el("div", "m-top");
    const where = el("p", "m-where");
    where.setAttribute("aria-live", "polite");
    const status = el("span", "m-status");
    top.append(where, status);
    const rule = el("div", "m-rule");
    const help = el("p", "m-help");
    const guess = button("m-guess", "Guess");
    const count = el("div", "m-count");
    const countText = el("span", "m-count-text");
    const track = el("span", "m-track");
    const fill = el("span", "m-fill");
    track.appendChild(fill);
    count.append(countText, track);
    count.hidden = !A.showCount;

    const nav = el("div", "m-nav");
    const prevBtn = button("m-nav-btn", "‹");
    const nextBtn = button("m-nav-btn", "›");
    prevBtn.setAttribute("aria-label", "Previous");
    nextBtn.setAttribute("aria-label", "Next");
    prevBtn.addEventListener("click", () => step(-1));
    nextBtn.addEventListener("click", () => step(1));
    nav.append(prevBtn, nextBtn);

    info.append(top, rule, help, guess, count, nav);
    dash.append(mini, info);
    bar.append(views, dash);
    wrap.parentNode.insertBefore(bar, wrap);

    const ui = {
      rule, guess, status,
      setHelp(t) { help.textContent = t; help.hidden = !t; },
    };
    const scrollHint = A.kind === "fwd" ? document.querySelector(".board-scroll-hint") : null;

    new MutationObserver(attach).observe(wrap, { childList: true });
    MQ.addEventListener ? MQ.addEventListener("change", apply) : MQ.addListener(apply);
    attach();

    // ── Board lifecycle ──────────────────────────────────────────────────────
    function attach() {
      const grid = wrap.querySelector(A.gridSelector);
      if (!grid) { B = null; apply(); return; }
      if (B && B.grid === grid) return;

      const nCols = parseInt(grid.style.getPropertyValue("--board-cols"), 10) || 9;
      const kids = Array.from(grid.children);
      const colHeads = kids.slice(1, 1 + nCols);
      const body = kids.slice(1 + nCols);
      const nRows = Math.floor(body.length / (nCols + 1));
      if (!nRows) return; // still being built

      const rowHeads = [];
      const cells = [];
      for (let r = 0; r < nRows; r += 1) {
        rowHeads.push(body[r * (nCols + 1)]);
        cells.push(body.slice(r * (nCols + 1) + 1, (r + 1) * (nCols + 1)));
      }
      grid.style.setProperty("--board-rows", String(nRows));

      B = { grid, nRows, nCols, cells, rowHeads, colHeads };
      pos.rows = Math.min(pos.rows, nRows - 1);
      pos.cols = Math.min(pos.cols, nCols - 1);

      A.decorate(B);
      buildMini();
      if (gridObs) gridObs.disconnect();
      gridObs = new MutationObserver(schedule);
      gridObs.observe(grid, {
        subtree: true, childList: true, characterData: true,
        attributes: true, attributeFilter: ["class", "src"],
      });
      apply();
    }

    // ── View switching ───────────────────────────────────────────────────────
    function setView(key) {
      view = key;
      try { localStorage.setItem(A.storeKey, key); } catch (_) { /* storage blocked */ }
      apply();
    }

    function apply() {
      const mobile = MQ.matches && !!B;
      const small = !!B && B.nRows <= FIT_MAX && B.nCols <= FIT_MAX;
      const focusable = mobile && !small;

      bar.classList.toggle("m-ready", focusable);
      wrap.classList.remove("m-focus", "m-rows", "m-cols", "m-grid", "m-fit", "m-dense");
      if (scrollHint) scrollHint.classList.toggle("m-hide", mobile && (small || view !== "grid"));
      if (!mobile) return;
      if (small) {
        wrap.classList.add("m-fit");
        // 5 columns leaves ~50px each on a phone: run column headers vertically.
        wrap.classList.toggle("m-dense", B.nCols >= FIT_MAX);
        return;
      }

      viewBtns.forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.view === view)));
      if (view === "grid") {
        wrap.classList.add("m-grid");
        dash.hidden = true;
        return;
      }
      dash.hidden = false;
      wrap.classList.add("m-focus", view === "rows" ? "m-rows" : "m-cols");
      renderLine();
    }

    function lineCount() { return view === "rows" ? B.nRows : B.nCols; }

    function step(dir) {
      if (!B || !wrap.classList.contains("m-focus")) return;
      const n = lineCount();
      pos[view] = (pos[view] + dir + n) % n;   // wraps, so swiping never dead-ends
      renderLine();
    }

    function renderLine() {
      if (!B) return;
      const i = pos[view];
      const byRow = view === "rows";

      // Show only this line. Re-displaying a tile replays the existing
      // cell-rise entrance; --m-k staggers it left→right.
      forEachCell(B, (cell, r, c) => {
        const inLine = byRow ? r === i : c === i;
        cell.classList.toggle("m-in", inLine);
        if (inLine) cell.style.setProperty("--m-k", String(byRow ? c : r));
      });

      where.textContent = `${byRow ? "Row" : "Column"} ${i + 1} of ${lineCount()}`;
      A.info(B, byRow ? "row" : "col", i, ui);

      band.style.gridRow = byRow ? `${i + 1} / span 1` : "1 / -1";
      band.style.gridColumn = byRow ? "1 / -1" : `${i + 1} / span 1`;
      syncMini();
    }

    // ── Overview map ─────────────────────────────────────────────────────────
    function buildMini() {
      mini.replaceChildren();
      mini.style.setProperty("--m-rows", String(B.nRows));
      mini.style.setProperty("--m-cols", String(B.nCols));
      B.squares = B.cells.map((row, r) => row.map((_, c) => {
        const sq = button("m-sq", "");
        sq.style.gridRow = String(r + 1);
        sq.style.gridColumn = String(c + 1);
        const img = el("img");
        img.alt = "";
        img.decoding = "async";
        sq.appendChild(img);
        sq.addEventListener("click", () => jumpTo(r, c));
        mini.appendChild(sq);
        return sq;
      }));
      mini.appendChild(band);
    }

    function jumpTo(r, c) {
      pos.rows = r;
      pos.cols = c;
      renderLine();
      // Web Animations, not a CSS class: swapping the animation property would
      // restart the tile's entrance animation and make it flicker.
      const tile = B.cells[r][c];
      if (tile.animate && !REDUCED.matches) {
        tile.animate([
          { boxShadow: "0 0 0 0 rgba(255, 92, 154, 0)" },
          { boxShadow: "0 0 0 3px rgba(255, 92, 154, 0.85), 0 0 18px rgba(255, 92, 154, 0.5)", offset: 0.3 },
          { boxShadow: "0 0 0 0 rgba(255, 92, 154, 0)" },
        ], { duration: 850, easing: "ease" });
      }
    }

    function schedule() {
      if (raf) return;
      raf = requestAnimationFrame(() => { raf = 0; syncMini(); });
    }

    function syncMini() {
      if (!B || !B.squares) return;
      const i = pos[view];
      let lineFilled = 0;

      forEachCell(B, (cell, r, c) => {
        const sq = B.squares[r][c];
        const s = A.cellState(cell, r, c, B);
        if (sq.dataset.state !== s.state) sq.dataset.state = s.state;
        const img = sq.firstChild;
        if (img.getAttribute("src") !== s.src) {
          if (s.src) img.setAttribute("src", s.src); else img.removeAttribute("src");
        }
        sq.setAttribute("aria-label", `Row ${r + 1}, column ${c + 1}: ${s.name || "empty"}`);
        const inLine = view === "rows" ? r === i : view === "cols" ? c === i : false;
        sq.classList.toggle("is-line", inLine);
        if (inLine && s.filled) lineFilled += 1;
      });

      if (A.showCount && view !== "grid") {
        const n = view === "rows" ? B.nCols : B.nRows;
        countText.textContent = `${lineFilled} of ${n} filled`;
        fill.style.width = `${(lineFilled / n) * 100}%`;
        count.classList.toggle("done", lineFilled === n);
      }
    }

    // ── Swipe between lines ──────────────────────────────────────────────────
    let sx = 0, sy = 0, st = 0;
    wrap.addEventListener("touchstart", (e) => {
      if (!wrap.classList.contains("m-focus")) return;
      const t = e.changedTouches[0];
      sx = t.clientX; sy = t.clientY; st = Date.now();
    }, { passive: true });
    wrap.addEventListener("touchend", (e) => {
      if (!wrap.classList.contains("m-focus") || !st) return;
      const t = e.changedTouches[0];
      const dx = t.clientX - sx, dy = t.clientY - sy;
      const quick = Date.now() - st < 700;
      st = 0;
      if (quick && Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy) * 1.4) step(dx < 0 ? 1 : -1);
    }, { passive: true });
  }

  // ── Shared helpers ───────────────────────────────────────────────────────────
  function forEachCell(B, fn) {
    for (let r = 0; r < B.nRows; r += 1) {
      for (let c = 0; c < B.nCols; c += 1) fn(B.cells[r][c], r, c, B);
    }
  }

  function cloneHead(head) {
    return Array.from(head.childNodes).map((n) => {
      const copy = n.cloneNode(true);
      if (copy.nodeType === 1) {
        copy.classList.remove("has-cat-tip");
        copy.removeAttribute("tabindex");
      }
      return copy;
    });
  }

  function chip(nodes, cls) {
    const c = el("div", `m-chip ${cls}`);
    c.setAttribute("aria-hidden", "true");
    c.append(...nodes);
    return c;
  }

  function isSolved(head) { return head.classList.contains("rev-solved") ? 1 : 0; }
  function revChip(head) { return isSolved(head) ? cloneHead(head) : [qmark()]; }
  function qmark() {
    const q = el("span", "rule-badge text rev-qmark");
    q.textContent = "?";
    return q;
  }

  function headLabel(head) {
    const type = head.querySelector(".type-badge-custom");
    return type ? type.getAttribute("title") || "" : text(head);
  }

  function explain(label) {
    const helpApi = window.PDD_CATEGORY_HELP;
    if (!helpApi || label == null || label === "") return "";
    const fmt = window.PDD?.formatRuleLabel || ((x) => x);
    try { return helpApi.explain(fmt(label)) || ""; } catch (_) { return ""; }
  }

  function readView(key) {
    try {
      const v = localStorage.getItem(key);
      if (VIEWS.some(([k]) => k === v)) return v;
    } catch (_) { /* storage blocked */ }
    return "rows";
  }

  function text(node) { return (node?.textContent || "").trim(); }

  function el(tag, cls) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    return n;
  }

  function button(cls, label) {
    const b = el("button", cls);
    b.type = "button";
    b.textContent = label;
    return b;
  }
})();