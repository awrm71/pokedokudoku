/* ============================================================================
   shell.js — behaviour for the page chrome styled in shell.css
   ----------------------------------------------------------------------------
   • Board chip: turns the raw "Seed: … | Board: 9x9" text that app.js and
     Reverse.js write into each .ctx-meta into a short label ("Daily · Oct 10").
   • Toasts: status messages written to .ctx-msg pop up as a toast instead of
     sitting in the header. Routine "Board loaded" noise is skipped.
   • Mode tabs: mirrors the .active class onto aria-pressed.
   • Badge fitting: shrinks row/column rule labels until they fit their box,
     so long names ("Learns Superpower") never spill out.
   • How to play: first-visit welcome card, reopened from the "?" button.

   Reads only what the other files already write to the page, so nothing in
   app.js / Reverse.js / draft.js / mobile.js has to call into this file.
   Load last.
   ============================================================================ */
(function () {
  "use strict";

  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  // ── Board chip ────────────────────────────────────────────────────────────
  function prettyDate(iso) {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || "");
    return m ? `${MONTHS[+m[2] - 1]} ${+m[3]}` : "";
  }
  function size(s) {
    const m = /(\d+)\s*[x×]\s*(\d+)/.exec(s || "");
    return m ? `${m[1]}×${m[2]}` : "";
  }

  function chipText(raw, ctx) {
    const t = (raw || "").trim();
    if (!t || /^loading/i.test(t)) return "";

    if (ctx === "reverse") {
      // "Daily Reverse (Hard) | 2026-10-10 | 9×9"  /  "Custom Reverse (medium) | 6×6"
      const diff = (/\(([^)]+)\)/.exec(t) || [])[1] || "";
      const D = diff ? diff.charAt(0).toUpperCase() + diff.slice(1) : "";
      if (/^daily/i.test(t)) {
        const d = prettyDate((/(\d{4}-\d{2}-\d{2})/.exec(t) || [])[1]);
        return ["Daily", D, d].filter(Boolean).join(" · ");
      }
      const sz = size(t);
      return ["Custom", D, sz !== "9×9" ? sz : ""].filter(Boolean).join(" · ");
    }

    // "Seed: 2026-10-10 | Board: 9x9"
    const seed = ((/Seed:\s*([^|]+)/i.exec(t) || [])[1] || "").trim();
    const sz = size(t);
    if (ctx === "draft" || /^draft/i.test(seed)) return `Draft · ${sz}`;
    const d = prettyDate(seed);
    const label = d ? `Daily · ${d}` : "Shuffled board";
    return sz && sz !== "9×9" ? `${label} · ${sz}` : label;
  }

  document.querySelectorAll(".ctx-panel").forEach((panel) => {
    const meta = panel.querySelector(".ctx-meta");
    if (!meta) return;
    const chip = document.createElement("span");
    chip.className = "board-chip";
    panel.insertBefore(chip, panel.firstChild);
    const sync = () => { chip.textContent = chipText(meta.textContent, panel.dataset.ctx); };
    new MutationObserver(sync).observe(meta, { childList: true, characterData: true, subtree: true });
    sync();
  });

  // ── Toasts ────────────────────────────────────────────────────────────────
  const stack = document.createElement("div");
  stack.className = "toast-stack";
  stack.setAttribute("role", "status");
  stack.setAttribute("aria-live", "polite");
  document.body.appendChild(stack);

  // Things that only confirm the obvious. Anything else (errors, hint results,
  // "size adjusted", Reverse's welcome line) still shows.
  // Shiny finds get their own full-screen moment (shiny.js), so no toast.
  const QUIET = [/^board loaded\.?$/i, /^draft board loaded$/i, /^loading/i, /^pick accepted/i, /^✨ shiny/i];

  function toneColor(el, text) {
    const c = (el.style.color || "").toLowerCase();
    if (c.includes("err") || /^(💀|unable|couldn)/i.test(text)) return "var(--err, #f43f5e)";
    if (c.includes("warn")) return "var(--warn, #f59e0b)";
    if (c.includes("ok")) return "var(--ok, #10d98a)";
    return "";
  }

  let last = { text: "", at: 0 };
  function toast(text, color) {
    const now = Date.now();
    if (text === last.text && now - last.at < 1500) return;   // same message twice in a row
    last = { text, at: now };

    const t = document.createElement("div");
    t.className = "toast";
    t.textContent = text;
    if (color) t.style.setProperty("--toast", color);
    const kill = () => {
      if (!t.isConnected || t.classList.contains("out")) return;
      t.classList.add("out");
      setTimeout(() => t.remove(), 220);
    };
    t.addEventListener("click", kill);
    stack.appendChild(t);
    while (stack.children.length > 3) stack.firstChild.remove();
    setTimeout(kill, color && color.includes("err") ? 5200 : 3000);
  }

  document.querySelectorAll(".ctx-msg").forEach((el) => {
    new MutationObserver(() => {
      const text = (el.textContent || "").trim();
      if (!text || QUIET.some((r) => r.test(text))) return;
      // Only the active mode's panel should speak.
      if (el.closest(".ctx-panel.hidden")) return;
      toast(text, toneColor(el, text));
    }).observe(el, { childList: true, characterData: true, subtree: true });
  });

  // ── Mode tabs: aria-pressed follows .active ───────────────────────────────
  document.querySelectorAll(".mode-tab").forEach((tab) => {
    const sync = () => tab.setAttribute("aria-pressed", String(tab.classList.contains("active")));
    new MutationObserver(sync).observe(tab, { attributes: true, attributeFilter: ["class"] });
    sync();
  });

  // ── Badge fitting ─────────────────────────────────────────────────────────
  const BADGES = ":is(.row-head, .rule-head, .m-chip, .m-rule) :is(.rule-badge.text, .type-badge-custom)";
  const MIN_PX = 7;

  function fit(badge) {
    if (!badge.offsetParent) return;                  // hidden (other line / other mode)
    const box = badge.parentElement;
    const key = `${box.clientWidth}x${box.clientHeight}`;
    if (badge.dataset.fit === key) return;           // already fitted at this size

    badge.style.fontSize = "";
    badge.classList.remove("fit-break");
    let px = parseFloat(getComputedStyle(badge).fontSize) || 12;
    const tooBig = () =>
      badge.scrollWidth > badge.clientWidth + 1 ||
      badge.offsetWidth > box.clientWidth + 1 ||
      (box.matches(".row-head, .rule-head") && box.scrollHeight > box.clientHeight + 1);

    let guard = 0;
    while (tooBig() && px > MIN_PX && guard < 14) {
      px = Math.max(MIN_PX, px * 0.9);
      badge.style.fontSize = `${px}px`;
      // Type badges size their icon/name spans separately.
      badge.querySelectorAll(".tbn, .tbi").forEach((s) => { s.style.fontSize = "inherit"; });
      guard += 1;
    }
    if (tooBig()) badge.classList.add("fit-break");   // last resort: allow a mid-word break
    badge.dataset.fit = key;
  }

  let raf = 0;
  function fitAll() {
    if (raf) return;
    raf = requestAnimationFrame(() => {
      raf = 0;
      document.querySelectorAll(BADGES).forEach(fit);
    });
  }

  // Wait for the pixel font, otherwise measurements use the fallback font.
  (document.fonts ? document.fonts.ready : Promise.resolve()).then(fitAll);
  ["boardWrap", "revBoardWrap"].forEach((id) => {
    const w = document.getElementById(id);
    if (w) new MutationObserver(fitAll).observe(w, { childList: true, subtree: true, attributes: true, attributeFilter: ["class"] });
  });
  // mobile.js swaps the focused rule into .m-rule; it may load before or after us.
  new MutationObserver(fitAll).observe(document.body, { childList: true });
  const watchRules = () => document.querySelectorAll(".m-rule:not([data-fit-watch])").forEach((r) => {
    r.dataset.fitWatch = "1";
    new MutationObserver(fitAll).observe(r, { childList: true });
  });
  watchRules();
  setTimeout(watchRules, 0);
  window.addEventListener("resize", () => {
    document.querySelectorAll(BADGES).forEach((b) => { delete b.dataset.fit; });
    fitAll();
  });

  // ── How to play ───────────────────────────────────────────────────────────
  const dlg = document.getElementById("howToDialog");
  const openBtn = document.getElementById("howToBtn");
  const SEEN = "pdd.seenHowTo";

  function openHowTo() {
    if (!dlg) return;
    if (typeof dlg.showModal === "function") dlg.showModal();
    else dlg.setAttribute("open", "");
  }
  function closeHowTo() {
    if (!dlg) return;
    if (typeof dlg.close === "function") dlg.close();
    else dlg.removeAttribute("open");
    try { localStorage.setItem(SEEN, "1"); } catch (_) { /* storage blocked */ }
  }

  openBtn?.addEventListener("click", openHowTo);
  dlg?.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", closeHowTo));
  dlg?.addEventListener("click", (e) => { if (e.target === dlg) closeHowTo(); });  // tap outside
  dlg?.addEventListener("close", () => {
    try { localStorage.setItem(SEEN, "1"); } catch (_) { /* storage blocked */ }
  });

  let seen = false;
  try { seen = localStorage.getItem(SEEN) === "1"; } catch (_) { seen = true; }
  if (!seen) setTimeout(openHowTo, 400);
})();