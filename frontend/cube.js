/* ============================================================================
   cube.js — the Cube page (cube.html, cube.css; server: cube_api.py).

   A 3D puzzle cube: six faces of nine squares. Each face has a rule; each
   column and row is a ring that wraps all the way around the cube. A square
   needs a Pokémon that fits its face, its column and its row.

   • Drag to turn the cube freely; let go and it settles with one face toward
     you. The arrows (or arrow keys) turn it a quarter at a time.
   • The face toward you is the one you answer on. Its rule is shown above the
     cube, its column rings below it and its row rings to the left.
   • Tap a square on that face to answer it. Tap a square on another face to
     turn that face toward you.
   • Placed Pokémon play their Black/White animations on the cube.

   Geometry matches cube_engine.py: axes x (right), y (down), z (toward you);
   face f has normal axis f>>1, positive for even f. Square (f, i, j) has
   coordinate i on the lower of the face's two axes and j on the higher one.
   ============================================================================ */
(function () {
  "use strict";

  const CDN = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon";
  const ANIM = `${CDN}/versions/generation-v/black-white/animated`;
  const CLIENT_ID_KEY = "route81_client_id";
  const PRACTICE_KEY = "route81.cube54.practice";
  const PLACEHOLDER = "data:image/svg+xml;utf8," + encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 96 96"><rect x="40" y="40" width="16" height="16" fill="#3a3745"/></svg>');

  const TYPE_CONFIG = {
    normal: ["#888888", "#fff"], fire: ["#E84000", "#fff"], fighting: ["#B81414", "#fff"],
    water: ["#1B7FE8", "#fff"], ice: ["#20CCDD", "#222"], grass: ["#2DAA4C", "#fff"],
    bug: ["#7DA900", "#fff"], ground: ["#C47A00", "#fff"], rock: ["#A08045", "#fff"],
    flying: ["#91B2F5", "#222"], electric: ["#FFD700", "#222"], psychic: ["#E8205A", "#fff"],
    fairy: ["#E85FAA", "#fff"], poison: ["#8B2BAE", "#fff"], ghost: ["#5B3E7A", "#fff"],
    dragon: ["#3425C8", "#fff"], dark: ["#2E2B3A", "#eee"], steel: ["#6A8FAD", "#fff"],
  };
  const FACE_NAMES = ["Face 1", "Face 2", "Face 3", "Face 4", "Face 5", "Face 6"];

  const $ = (id) => document.getElementById(id);
  const ck = (f, i, j) => `${f},${i},${j}`;
  const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)");
  const calm = () => !!reduced?.matches;

  // ── Geometry ───────────────────────────────────────────────────────────────
  const axisOf = (f) => f >> 1;
  const others = (f) => [0, 1, 2].filter((a) => a !== axisOf(f));
  const unit = (a, s = 1) => { const v = [0, 0, 0]; v[a] = s; return v; };
  const normal = (f) => unit(axisOf(f), f % 2 ? -1 : 1);
  // Each face element's own right (u) and down (v) directions, in cube space.
  const FACE_UV = [
    [[0, 0, -1], [0, 1, 0]],  // +x
    [[0, 0, 1], [0, 1, 0]],   // -x
    [[1, 0, 0], [0, 0, -1]],  // +y
    [[1, 0, 0], [0, 0, 1]],   // -y
    [[1, 0, 0], [0, 1, 0]],   // +z
    [[-1, 0, 0], [0, 1, 0]],  // -z
  ];
  const CELLS = [];
  for (let f = 0; f < 6; f++) for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) CELLS.push([f, i, j]);

  // Grid spot (row r, col c) on face f -> (i, j).
  function gridToCell(f, r, c) {
    const [u, v] = FACE_UV[f];
    const n = normal(f);
    const p = [0, 1, 2].map((k) => (c - 1) * u[k] + (r - 1) * v[k] + n[k]);
    const [b, cc] = others(f);
    return [p[b] + 1, p[cc] + 1];
  }
  function lineOf(axis, k) { return 6 + axis * 3 + k; }
  function cellsOf(line) {
    if (line < 6) return CELLS.filter((c) => c[0] === line);
    const axis = Math.floor((line - 6) / 3), k = (line - 6) % 3;
    return CELLS.filter(([f, i, j]) => { const [b, c] = others(f); return (b === axis && i === k) || (c === axis && j === k); });
  }

  // 3×3 matrices (row-major arrays of rows).
  const mul = (A, B) => A.map((r) => [0, 1, 2].map((j) => r[0] * B[0][j] + r[1] * B[1][j] + r[2] * B[2][j]));
  const tr = (A) => [0, 1, 2].map((i) => [A[0][i], A[1][i], A[2][i]]);
  const app = (A, v) => A.map((r) => r[0] * v[0] + r[1] * v[1] + r[2] * v[2]);
  const rx = (a) => { const c = Math.cos(a), s = Math.sin(a); return [[1, 0, 0], [0, c, -s], [0, s, c]]; };
  const ry = (a) => { const c = Math.cos(a), s = Math.sin(a); return [[c, 0, s], [0, 1, 0], [-s, 0, c]]; };
  const trace = (A, B) => { let t = 0; for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) t += A[i][j] * B[i][j]; return t; };
  const round = (A) => A.map((r) => r.map((x) => Math.round(x)));
  const css = (M) => `matrix3d(${M[0][0]},${M[1][0]},${M[2][0]},0,${M[0][1]},${M[1][1]},${M[2][1]},0,${M[0][2]},${M[1][2]},${M[2][2]},0,0,0,0,1)`;

  // The 24 ways a cube can sit (signed permutation matrices with det +1).
  const ROTS = (() => {
    const out = [];
    const perms = [[0, 1, 2], [0, 2, 1], [1, 0, 2], [1, 2, 0], [2, 0, 1], [2, 1, 0]];
    for (const p of perms) for (let s = 0; s < 8; s++) {
      const M = [[0, 0, 0], [0, 0, 0], [0, 0, 0]];
      for (let r = 0; r < 3; r++) M[r][p[r]] = (s >> r) & 1 ? -1 : 1;
      const det = M[0][0] * (M[1][1] * M[2][2] - M[1][2] * M[2][1]) - M[0][1] * (M[1][0] * M[2][2] - M[1][2] * M[2][0]) + M[0][2] * (M[1][0] * M[2][1] - M[1][1] * M[2][0]);
      if (det === 1) out.push(M);
    }
    return out;
  })();
  // A resting tilt so the top and right faces show around the front one.
  const TILT = mul(rx(-0.36), ry(-0.42));

  // Quaternions, for a smooth settle between orientations.
  function toQ(m) {
    const t = m[0][0] + m[1][1] + m[2][2];
    let w, x, y, z;
    if (t > 0) { const s = Math.sqrt(t + 1) * 2; w = s / 4; x = (m[2][1] - m[1][2]) / s; y = (m[0][2] - m[2][0]) / s; z = (m[1][0] - m[0][1]) / s; }
    else if (m[0][0] > m[1][1] && m[0][0] > m[2][2]) { const s = Math.sqrt(1 + m[0][0] - m[1][1] - m[2][2]) * 2; w = (m[2][1] - m[1][2]) / s; x = s / 4; y = (m[0][1] + m[1][0]) / s; z = (m[0][2] + m[2][0]) / s; }
    else if (m[1][1] > m[2][2]) { const s = Math.sqrt(1 + m[1][1] - m[0][0] - m[2][2]) * 2; w = (m[0][2] - m[2][0]) / s; x = (m[0][1] + m[1][0]) / s; y = s / 4; z = (m[1][2] + m[2][1]) / s; }
    else { const s = Math.sqrt(1 + m[2][2] - m[0][0] - m[1][1]) * 2; w = (m[1][0] - m[0][1]) / s; x = (m[0][2] + m[2][0]) / s; y = (m[1][2] + m[2][1]) / s; z = s / 4; }
    return [w, x, y, z];
  }
  function fromQ([w, x, y, z]) {
    return [
      [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
      [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
      [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ];
  }
  function slerp(a, b, t) {
    let d = a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3];
    if (d < 0) { b = b.map((x) => -x); d = -d; }
    if (d > 0.9995) {
      const r = a.map((x, k) => x + (b[k] - x) * t);
      const n = Math.hypot(...r);
      return r.map((x) => x / n);
    }
    const th = Math.acos(d), s = Math.sin(th);
    return a.map((x, k) => (x * Math.sin((1 - t) * th) + b[k] * Math.sin(t * th)) / s);
  }

  // ── State ────────────────────────────────────────────────────────────────
  const S = {
    kind: "fill", which: "daily",
    board: null,
    picks: new Map(),        // "f,i,j" -> {mon, hinted, shiny, pct}
    answers: new Map(),
    lives: 3, hints: 3, maxLives: 3, maxHints: 3,
    finished: false,
    rev: null, revRun: null, revPractice: null,
    guess: null,             // {line, rule}
    catalog: null,
    sel: null,               // [f, i, j] being answered
    G: ROTS.find((M) => trace(M, [[1, 0, 0], [0, 1, 0], [0, 0, 1]]) === 3),
    Q: null,                 // orientation on screen right now
    hover: null,             // "f,i,j" under the pointer
  };
  S.Q = mul(TILT, S.G);

  const frontFace = () => {
    for (let f = 0; f < 6; f++) { const n = app(S.G, normal(f)); if (n[2] === 1) return f; }
    return 4;
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
  function store(key, val) { try { val === null ? localStorage.removeItem(key) : localStorage.setItem(key, JSON.stringify(val)); } catch (_) { /* ignore */ } }
  function recall(key) { try { return JSON.parse(localStorage.getItem(key) || "null"); } catch (_) { return null; } }

  // ── Small pieces ─────────────────────────────────────────────────────────
  const missingAnim = new Set();
  function spriteImg(mon, shiny, animated) {
    const el = document.createElement("img");
    el.alt = "";
    el.decoding = "async";
    el.draggable = false;
    if (!mon || !mon.pokemon_id) { el.src = PLACEHOLDER; return el; }
    const still = `${CDN}/${shiny ? "shiny/" : ""}${mon.pokemon_id}.png`;
    const anim = `${ANIM}/${shiny ? "shiny/" : ""}${mon.pokemon_id}.gif`;
    if (animated && !calm() && !missingAnim.has(anim)) {
      el.onerror = () => { missingAnim.add(anim); el.onerror = () => { el.onerror = null; el.src = PLACEHOLDER; }; el.src = still; };
      el.src = anim;
    } else {
      el.onerror = () => { el.onerror = null; el.src = PLACEHOLDER; };
      el.src = still;
    }
    return el;
  }
  // Rule badges come from category-colors.js: a colour family, an icon and the name.
  function badge(rule) {
    return window.R81_CAT ? window.R81_CAT.badge(rule) : document.createTextNode(rule ? rule.label : "");
  }
  function pips(el, total, left, cls) {
    el.textContent = "";
    for (let k = 0; k < total; k++) {
      const p = document.createElement("span");
      p.className = cls + (k >= left ? " spent" : "");
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
  function prettyDate(iso) {
    const d = new Date(`${iso}T12:00:00`);
    return isNaN(d) ? "" : d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  }
  function show(d) { if (typeof d.showModal === "function") d.showModal(); else d.setAttribute("open", ""); }
  function hide(d) { if (typeof d.close === "function") d.close(); else d.removeAttribute("open"); }
  function setStatus(id, text, tone) { const el = $(id); el.textContent = text; el.className = "cm-status" + (tone ? ` ${tone}` : ""); }

  // Rules, for either kind of Cube.
  function faceRule(f) { return S.board ? S.board.faces[f] : null; }
  function ringRule(axis, k) { return S.board ? S.board.rings[axis][k] : null; }
  function rulesAt(f, i, j) { const [b, c] = others(f); return [faceRule(f), ringRule(b, i), ringRule(c, j)]; }

  function occupant(f, i, j) {
    const k = ck(f, i, j);
    if (S.kind === "reverse") {
      const c = S.rev?.cells.find((x) => x.f === f && x.i === i && x.j === j);
      return c ? { mon: c.mon } : null;
    }
    const p = S.picks.get(k);
    if (p) return p;
    const a = S.answers.get(k);
    return a ? { mon: a.mon, answer: true, count: a.count } : null;
  }

  // Reverse: a line's state.
  function revSolved(line) {
    const run = S.revRun;
    if (!run) return null;
    const s = run.solved.find((x) => x.line === line);
    if (s) return { ...s, revealed: false };
    const r = run.reveal.find((x) => x.line === line);
    return r ? { ...r, revealed: true } : null;
  }
  function revBadge(line) {
    const s = revSolved(line);
    if (!s) return window.R81_CAT.hidden(line < 6 ? "Face rule" : "Ring rule");
    const el = badge({ id: s.rule_id, label: s.label });
    if (s.revealed) el.classList.add("rc-revealed");
    return el;
  }
  const lineBadge = (line) => {
    if (S.kind === "reverse") return revBadge(line);
    if (line < 6) return badge(faceRule(line));
    const axis = Math.floor((line - 6) / 3);
    return badge(ringRule(axis, (line - 6) % 3));
  };

  // ── Building the cube ────────────────────────────────────────────────────
  const stEls = new Map();    // "f,i,j" -> button
  const faceEls = [];

  // One square's size, from the width the cube has to work with: the cube
  // plus the row labels to its left have to fit.
  function fitSize() {
    const w = $("rkScene").clientWidth || 600;
    const t = Math.max(46, Math.min(96, Math.floor(w / (w < 520 ? 6.4 : 5.5))));
    document.body.style.setProperty("--t", `${t}px`);
  }

  function buildCube() {
    fitSize();
    const cube = $("rkCube");
    cube.textContent = "";
    stEls.clear();
    faceEls.length = 0;
    const t = parseFloat(getComputedStyle(document.body).getPropertyValue("--t")) || 92;
    const h = t * 1.5;
    for (let f = 0; f < 6; f++) {
      const [u, v] = FACE_UV[f];
      const n = normal(f);
      const face = document.createElement("div");
      face.className = `rk-face fc${f}`;
      face.dataset.f = f;
      face.style.setProperty("--rot", `${faceRot[f]}deg`);
      face.style.transform = `matrix3d(${u[0]},${u[1]},${u[2]},0,${v[0]},${v[1]},${v[2]},0,${n[0]},${n[1]},${n[2]},0,${n[0] * h},${n[1] * h},${n[2] * h},1)`;
      for (let r = 0; r < 3; r++) for (let c = 0; c < 3; c++) {
        const [i, j] = gridToCell(f, r, c);
        const b = document.createElement("button");
        b.type = "button";
        b.className = "st";
        b.dataset.f = f; b.dataset.i = i; b.dataset.j = j;
        face.appendChild(b);
        stEls.set(ck(f, i, j), b);
      }
      cube.appendChild(face);
      faceEls.push(face);
    }
    applyQ();
  }

  function paintStickers() {
    const front = frontFace();
    const rings = hoverRings();
    for (const [f, i, j] of CELLS) {
      const b = stEls.get(ck(f, i, j));
      if (!b) continue;
      const o = occupant(f, i, j);
      const key = o && o.mon ? `${o.mon.key}:${o.shiny ? 1 : 0}:${o.answer ? 1 : 0}` : "";
      if (b.dataset.k !== key) {
        b.dataset.k = key;
        b.textContent = "";
        b.className = "st";
        // Everything on a square sits in one inner frame, turned so it's
        // upright however the cube is turned (see uprightFaces).
        const inner = document.createElement("span");
        inner.className = "st-in";
        b.appendChild(inner);
        if (o && o.mon) {
          const pic = document.createElement("span");
          pic.className = "st-pic";
          pic.appendChild(spriteImg(o.mon, o.shiny, !o.answer));
          inner.appendChild(pic);
          b.classList.add(o.answer ? "answer" : "filled");
          if (o.hinted) b.classList.add("hinted");
          if (o.shiny) b.classList.add("shiny");
          if (o.answer) {
            const c = document.createElement("span"); c.className = "st-count"; c.textContent = `${o.count} fit`; inner.appendChild(c);
          } else if (o.pct != null) {
            const c = document.createElement("span"); c.className = "st-pct"; c.textContent = `${Math.round(o.pct)}%`; inner.appendChild(c);
          }
        }
      }
      const [r0, r1, r2] = S.board || S.kind === "reverse" ? labelsFor(f, i, j) : ["", "", ""];
      b.setAttribute("aria-label", o && o.mon ? `${o.mon.display_name}${o.answer ? ` (one answer, ${o.count} fit)` : ""}` : `Empty square: ${r0}, ${r1}, ${r2}`);
      b.tabIndex = f === front ? 0 : -1;
      b.classList.toggle("ring", rings.some((line) => inLine(line, f, i, j)));
    }
    faceEls.forEach((el, f) => {
      el.classList.toggle("dim", f !== front);
      // Only the face toward you takes its rule's colour; the rest stay neutral.
      const rid = f === front ? faceRuleId(f) : null;
      if (rid && window.R81_CAT) el.style.setProperty("--face-c", window.R81_CAT.style(rid).color);
      else el.style.removeProperty("--face-c");
    });
  }

  // The rule id that colours face f: the face rule (Fill), or the named one (Reverse).
  function faceRuleId(f) {
    if (S.kind === "reverse") { const x = revSolved(f); return x ? x.rule_id : null; }
    return S.board ? S.board.faces[f].id : null;
  }

  function labelsFor(f, i, j) {
    if (S.kind === "reverse" || !S.board) return ["face", "column", "row"];
    return rulesAt(f, i, j).map((r) => r.label);
  }

  function inLine(line, f, i, j) {
    if (line < 6) return line === f;
    const axis = Math.floor((line - 6) / 3), k = (line - 6) % 3;
    const [b, c] = others(f);
    return (b === axis && i === k) || (c === axis && j === k);
  }

  // The two rings to light up: the square under the pointer, or the line being guessed.
  function hoverRings() {
    if (S.guess) return [S.guess.line];
    const k = S.hover || (S.sel ? ck(...S.sel) : null);
    if (!k) return [];
    const [f, i, j] = k.split(",").map(Number);
    const [b, c] = others(f);
    return [lineOf(b, i), lineOf(c, j)];
  }

  function applyQ() {
    $("rkCube").style.transform = css(S.Q);
    uprightFaces();
  }

  // Keep every face's Pokémon standing up on screen. Each face can show its
  // squares four ways (0/90/180/270°); pick the one whose "up" points most
  // up the screen right now. A little stickiness stops it flickering while
  // a face is edge-on during a drag.
  const faceRot = [0, 0, 0, 0, 0, 0];
  function uprightFaces() {
    for (let f = 0; f < 6; f++) {
      const [u, v] = FACE_UV[f];
      const neg = (a) => a.map((x) => -x);
      const options = [[neg(v), 0], [u, 90], [v, 180], [neg(u), 270]];
      let best = faceRot[f];
      let bestY = Infinity, curY = Infinity;
      for (const [dir, deg] of options) {
        const y = app(S.Q, dir)[1];
        if (deg === faceRot[f]) curY = y;
        if (y < bestY) { bestY = y; best = deg; }
      }
      if (best !== faceRot[f] && bestY < curY - 0.12) {
        faceRot[f] = best;
        faceEls[f]?.style.setProperty("--rot", `${best}deg`);
      }
    }
  }

  // ── Labels for the face toward you ───────────────────────────────────────
  // Which ring each screen column / row of the front face belongs to.
  function frontLines() {
    const Gt = tr(S.G);
    const hAxis = app(Gt, [1, 0, 0]).findIndex((x) => Math.abs(x) > 0.5);
    const vAxis = app(Gt, [0, 1, 0]).findIndex((x) => Math.abs(x) > 0.5);
    const cols = [0, 1, 2].map((k) => lineOf(hAxis, Math.round(app(Gt, [k - 1, 0, 1])[hAxis]) + 1));
    const rows = [0, 1, 2].map((k) => lineOf(vAxis, Math.round(app(Gt, [0, k - 1, 1])[vAxis]) + 1));
    return { cols, rows };
  }

  function project(p, t, P, w, hgt) {
    const q = app(S.Q, [p[0] * t, p[1] * t, p[2] * t]);
    const s = P / (P - q[2]);
    return [w / 2 + q[0] * s + originShift().x, hgt * 0.46 + q[1] * s];
  }
  function originShift() {
    const t = tile();
    return { x: t * 0.45 };
  }
  function tile() { return parseFloat(getComputedStyle(document.body).getPropertyValue("--t")) || 92; }

  function renderLabels() {
    const box = $("rkLabels");
    box.textContent = "";
    if (!(S.kind === "fill" ? S.board : S.rev)) return;
    const scene = $("rkScene");
    const w = scene.clientWidth, h = scene.clientHeight;
    const t = tile();
    const P = 1400;
    const { cols, rows } = frontLines();
    const lit = new Set(hoverRings());
    const reverse = S.kind === "reverse";
    // Front face in its untilted spot spans [-1.5t, 1.5t]; we project through the tilt.
    const untilt = tr(S.G);   // so that S.Q · (G^T · p) = TILT · p
    const place = (p) => { const cube = app(untilt, p); return project(cube, t, P, w, h); };
    cols.forEach((line, k) => {
      const el = document.createElement(reverse ? "button" : "div");
      el.className = "rk-lab col" + (lit.has(line) ? " hl" : "");
      const [x, y] = place([k - 1, 1.62, 1.5]);
      el.style.left = `${x}px`; el.style.top = `${y}px`;
      el.appendChild(lineBadge(line));
      wireLabel(el, line, `Column ${k + 1}`);
      box.appendChild(el);
    });
    // Small captions so the labels read as "these are rows / columns".
    const capRow = document.createElement("span");
    capRow.className = "rk-cap-lab row";
    capRow.textContent = "Rows";
    let [cx, cy] = place([-1.6, -1.72, 1.5]);
    capRow.style.left = `${cx}px`; capRow.style.top = `${cy}px`;
    box.appendChild(capRow);
    const capCol = document.createElement("span");
    capCol.className = "rk-cap-lab col";
    capCol.textContent = "Columns";
    [cx, cy] = place([-1.6, 1.62, 1.5]);
    capCol.style.left = `${cx}px`; capCol.style.top = `${cy}px`;
    box.appendChild(capCol);
    rows.forEach((line, k) => {
      const el = document.createElement(reverse ? "button" : "div");
      el.className = "rk-lab row" + (lit.has(line) ? " hl" : "");
      const [x, y] = place([-1.6, k - 1, 1.5]);
      el.style.left = `${x}px`; el.style.top = `${y}px`;
      el.appendChild(lineBadge(line));
      wireLabel(el, line, `Row ${k + 1}`);
      box.appendChild(el);
    });
  }
  function wireLabel(el, line, name) {
    if (el.tagName === "BUTTON") {
      el.type = "button";
      el.setAttribute("aria-label", `${name}: ${revSolved(line)?.label || "not named yet"}`);
      el.addEventListener("click", () => openGuess(line));
    } else {
      el.setAttribute("aria-label", `${name} ring: ${el.textContent}`);
    }
  }

  function renderTitle() {
    const f = frontFace();
    const box = $("rkTitle");
    box.textContent = "";
    box.className = "rk-title";
    const cap = document.createElement("span");
    cap.className = "rk-cap";
    cap.textContent = `${FACE_NAMES[f]} of 6. Every square on this face:`;
    box.appendChild(cap);
    if (S.kind === "reverse") {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "rk-name";
      const chip = revBadge(f);
      chip.classList.add("rb-big");
      b.appendChild(chip);
      b.setAttribute("aria-label", `${FACE_NAMES[f]}: ${revSolved(f)?.label || "not named yet"}`);
      b.addEventListener("click", () => openGuess(f));
      box.appendChild(b);
    } else if (S.board) {
      const big = badge(faceRule(f));
      big.classList.add("rb-big");
      box.appendChild(big);
    }
  }

  function renderFaceList() {
    const ul = $("flList");
    ul.textContent = "";
    const front = frontFace();
    for (let f = 0; f < 6; f++) {
      const li = document.createElement("li");
      const b = document.createElement("button");
      b.type = "button";
      b.className = "fl-item";
      b.setAttribute("aria-current", String(f === front));
      const sw = document.createElement("span"); sw.className = "fl-sw"; sw.textContent = String(f + 1);
      const frid = faceRuleId(f);
      if (frid && window.R81_CAT) {
        const st = window.R81_CAT.style(frid);
        sw.style.background = st.color;
        sw.style.color = st.ink;
      }
      const text = document.createElement("span"); text.className = "fl-text";
      const nm = document.createElement("span"); nm.className = "fl-name"; nm.textContent = FACE_NAMES[f];
      text.append(nm, lineBadge(f));
      const count = document.createElement("span"); count.className = "fl-count";
      if (S.kind === "fill") {
        let n = 0;
        for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) if (S.picks.has(ck(f, i, j))) n++;
        count.textContent = `${n}/9`;
      }
      b.append(sw, text, count);
      b.addEventListener("click", () => turnToFace(f));
      li.appendChild(b);
      ul.appendChild(li);
    }
  }

  function renderConsole() {
    document.querySelectorAll("[data-kind]").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.kind === S.kind)));
    document.querySelectorAll("[data-which]").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.which === S.which)));
    const chip = $("ccChip");
    if (S.kind === "fill") {
      const n = S.picks.size;
      $("ccScore").textContent = `${n} / 54`;
      $("ccFill").style.width = `${(n / 54) * 100}%`;
      pips($("ccLives"), S.maxLives, S.lives, "pip-life");
      pips($("ccHints"), S.maxHints, S.hints, "pip-hint");
      chip.textContent = S.board ? (S.board.is_daily ? `Today's Cube, ${prettyDate(S.board.seed)}` : "Practice Cube") : "";
    } else {
      const run = S.revRun;
      const n = run ? run.solved.length : 0;
      $("ccScore").textContent = `${n} / 15`;
      $("ccFill").style.width = `${(n / 15) * 100}%`;
      pips($("ccLives"), S.rev ? S.rev.lives : 3, run ? run.lives_left : 3, "pip-life");
      pips($("ccHints"), S.rev ? S.rev.hints : 2, run ? run.hints_left : 2, "pip-hint");
      chip.textContent = S.rev ? (S.rev.is_daily ? `Today's Reverse Cube, ${prettyDate(S.rev.seed)}` : "Practice Reverse Cube") : "";
    }
    $("rkHelp").textContent = S.kind === "fill"
      ? "Tap a square on the face toward you. Each one needs a Pokémon that fits the face, its column and its row."
      : "Tap the face name or a column or row name to guess what all its Pokémon share. Rings carry on around the cube.";
  }

  function renderBanner() {
    const bn = $("ccBanner");
    let msg = null, lost = false;
    if (S.kind === "fill" && S.board && S.finished) {
      if (S.picks.size >= 54) msg = S.board.is_daily ? "Cube complete. Come back tomorrow for a new one." : "Cube complete.";
      else { msg = "Out of lives. The open squares show one Pokémon that fit."; lost = true; }
    }
    if (S.kind === "reverse" && S.revRun && S.revRun.finished) {
      if (S.revRun.won) msg = "All 15 rules named.";
      else { msg = "Out of lives. The rules you missed are shown in red."; lost = true; }
    }
    bn.hidden = !msg;
    bn.classList.toggle("lost", lost);
    if (!msg) return;
    bn.querySelector(".cc-banner-text").textContent = msg;
    const daily = S.kind === "fill" ? S.board.is_daily : S.rev.is_daily;
    $("ccBannerBtn").textContent = daily ? "Play a practice Cube" : "New Cube";
  }

  function render() {
    renderConsole();
    renderTitle();
    paintStickers();
    renderLabels();
    renderFaceList();
    renderBanner();
  }
  function renderView() {       // after turning: only what depends on the front face
    renderTitle();
    paintStickers();
    renderLabels();
    renderFaceList();
  }

  // ── Turning ──────────────────────────────────────────────────────────────
  let anim = 0;
  function settleTo(G) {
    S.G = G;
    const target = mul(TILT, G);
    cancelAnimationFrame(anim);
    $("rkLabels").classList.add("hidden");
    if (calm()) { S.Q = target; applyQ(); $("rkLabels").classList.remove("hidden"); renderView(); return; }
    const a = toQ(S.Q), b = toQ(target);
    const t0 = performance.now(), dur = 320;
    const step = (now) => {
      const k = Math.min(1, (now - t0) / dur);
      const e = 1 - Math.pow(1 - k, 3);
      S.Q = fromQ(slerp(a, b, e));
      applyQ();
      if (k < 1) anim = requestAnimationFrame(step);
      else { S.Q = target; applyQ(); $("rkLabels").classList.remove("hidden"); renderView(); }
    };
    anim = requestAnimationFrame(step);
    paintStickers();
    renderTitle();
    renderFaceList();
  }
  function nearest(Q, filter) {
    let best = null, bestT = -Infinity;
    for (const M of ROTS) {
      if (filter && !filter(M)) continue;
      const t = trace(Q, mul(TILT, M));
      if (t > bestT) { bestT = t; best = M; }
    }
    return best;
  }
  function turn(dir) {
    const q = Math.PI / 2;
    const R = { left: ry(-q), right: ry(q), up: rx(q), down: rx(-q) }[dir];
    settleTo(round(mul(R, S.G)));
  }
  function turnToFace(f) {
    const n = normal(f);
    const G = nearest(mul(TILT, S.G), (M) => app(M, n)[2] === 1);
    if (G) settleTo(G);
  }

  function setupDrag() {
    const scene = $("rkScene");
    let drag = null;
    scene.addEventListener("pointerdown", (e) => {
      if (e.button !== 0 || e.target.closest(".rk-lab")) return;
      drag = { x: e.clientX, y: e.clientY, Q: S.Q, moved: false, id: e.pointerId, target: e.target };
      cancelAnimationFrame(anim);
    });
    window.addEventListener("pointermove", (e) => {
      if (!drag || e.pointerId !== drag.id) return;
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      if (!drag.moved && Math.hypot(dx, dy) < 6) return;
      if (!drag.moved) { drag.moved = true; scene.classList.add("dragging"); $("rkLabels").classList.add("hidden"); }
      const k = 0.0085;
      S.Q = mul(mul(ry(dx * k), rx(-dy * k)), drag.Q);
      applyQ();
    });
    const end = (e) => {
      if (!drag || (e.pointerId !== undefined && e.pointerId !== drag.id)) return;
      const d = drag;
      drag = null;
      scene.classList.remove("dragging");
      if (d.moved) {
        suppressClick = true;
        setTimeout(() => { suppressClick = false; }, 0);
        settleTo(nearest(S.Q));
      }
    };
    window.addEventListener("pointerup", end);
    window.addEventListener("pointercancel", end);

    scene.addEventListener("click", (e) => {
      if (suppressClick) return;
      const st = e.target.closest(".st");
      if (!st) return;
      const f = Number(st.dataset.f), i = Number(st.dataset.i), j = Number(st.dataset.j);
      if (f !== frontFace()) { turnToFace(f); return; }
      if (S.kind === "fill") {
        const o = occupant(f, i, j);
        if (!o) openPick(f, i, j);
      }
    });
    scene.addEventListener("pointerover", (e) => {
      if (e.pointerType !== "mouse" || drag) return;
      const st = e.target.closest(".st");
      const k = st && Number(st.dataset.f) === frontFace() ? ck(st.dataset.f, st.dataset.i, st.dataset.j) : null;
      if (k !== S.hover) { S.hover = k; paintStickers(); renderLabels(); }
    });
    scene.addEventListener("pointerleave", () => { if (S.hover) { S.hover = null; paintStickers(); renderLabels(); } });
    scene.addEventListener("focusin", (e) => {
      const st = e.target.closest(".st");
      if (st) { S.hover = ck(st.dataset.f, st.dataset.i, st.dataset.j); paintStickers(); renderLabels(); }
    });
    document.querySelectorAll(".rk-turn").forEach((b) => b.addEventListener("click", () => turn(b.dataset.turn)));
    document.addEventListener("keydown", (e) => {
      if (document.querySelector("dialog[open]")) return;
      if (e.target.matches?.("input, textarea")) return;
      const dir = { ArrowLeft: "left", ArrowRight: "right", ArrowUp: "up", ArrowDown: "down" }[e.key];
      if (!dir) return;
      e.preventDefault();
      turn(dir);
    });
    window.addEventListener("resize", () => { buildCube(); paintStickersForce(); renderLabels(); });
  }
  let suppressClick = false;
  function paintStickersForce() { stEls.forEach((b) => { delete b.dataset.k; }); paintStickers(); }

  // ── Loading ──────────────────────────────────────────────────────────────
  async function load(fresh) {
    $("ccNew").hidden = S.which !== "practice";
    try {
      if (S.kind === "fill") await loadFill(fresh); else await loadReverse(fresh);
    } catch (e) {
      toast(e.message || "Couldn't load the Cube.", "err");
    }
    paintStickersForce();
    render();
  }
  const newPracticeSeed = () => `p-${Math.random().toString(36).slice(2, 10)}`;

  async function loadFill(fresh) {
    S.answers = new Map();
    S.picks = new Map();
    S.rev = null; S.revRun = null;
    let seed, saved = null;
    if (S.which === "practice") {
      saved = fresh ? null : recall(PRACTICE_KEY);
      seed = saved?.seed || newPracticeSeed();
    }
    const b = await api(`/cube/board${seed ? `?seed=${encodeURIComponent(seed)}` : ""}`);
    S.board = b;
    S.maxLives = b.lives; S.maxHints = b.hints;
    if (b.run) {
      S.lives = b.run.lives_left; S.hints = b.run.hints_left; S.finished = b.run.finished;
      for (const p of b.run.picks) S.picks.set(ck(p.f, p.i, p.j), { mon: p.mon, hinted: p.hinted, shiny: p.shiny, pct: p.pct });
    } else if (saved && saved.seed === b.seed) {
      S.lives = saved.lives; S.hints = saved.hints; S.finished = saved.finished;
      for (const [k, v] of saved.picks) S.picks.set(k, v);
    } else {
      S.lives = b.lives; S.hints = b.hints; S.finished = false;
    }
    savePractice();
    if (S.finished) await loadAnswers();
  }
  function savePractice() {
    if (S.which !== "practice" || !S.board || S.kind !== "fill") return;
    store(PRACTICE_KEY, { seed: S.board.seed, lives: S.lives, hints: S.hints, finished: S.finished, picks: Array.from(S.picks.entries()) });
  }
  async function loadAnswers() {
    try {
      const sol = await api(`/cube/solution?seed=${encodeURIComponent(S.board.seed)}`);
      S.answers = new Map(sol.cells.map((c) => [ck(c.f, c.i, c.j), { mon: c.example, count: c.count }]));
    } catch (_) { S.answers = new Map(); }
  }
  async function loadReverse(fresh) {
    S.board = null;
    if (S.which === "daily") S.rev = await api("/cube/reverse/daily");
    else {
      if (fresh || !S.revPractice) S.revPractice = await api("/cube/reverse", { difficulty: "medium" });
      S.rev = S.revPractice;
    }
    S.revRun = S.rev.run;
  }

  // ── Fill: choosing a Pokémon ─────────────────────────────────────────────
  let suggestTimer = 0, suggestions = [], active = -1, submitting = false;
  const usedKeys = () => Array.from(S.picks.values()).map((p) => p.mon.key);

  function openPick(f, i, j) {
    if (S.finished || S.kind !== "fill") return;
    S.sel = [f, i, j];
    // Name the two rings the way they sit on screen right now.
    const { cols } = frontLines();
    const [b, c] = others(f);
    const [fr, r1, r2] = rulesAt(f, i, j);
    const firstIsCol = cols.includes(lineOf(b, i));
    const boxes = [
      { label: `Whole face (${FACE_NAMES[f]})`, rule: fr, face: true },
      { label: "Column", rule: firstIsCol ? r1 : r2 },
      { label: "Row", rule: firstIsCol ? r2 : r1 },
    ];
    void c;
    const box = $("cmRules");
    box.textContent = "";
    for (const x of boxes) {
      const el = document.createElement("div");
      el.className = "cm-rule" + (x.face ? " face" : "");
      const ax = document.createElement("span"); ax.className = "cm-rule-axis"; ax.textContent = x.label;
      el.append(ax, badge(x.rule));
      box.appendChild(el);
    }
    $("cmInput").value = "";
    $("cmSuggest").textContent = "";
    suggestions = []; active = -1;
    setStatus("cmStatus", "");
    pips($("cmLives"), S.maxLives, S.lives, "pip-life");
    $("cmHint").disabled = S.hints <= 0;
    $("cmHint").textContent = S.hints > 0 ? `Use a hint (${S.hints} left)` : "No hints left";
    paintStickers(); renderLabels();
    show($("cmPick"));
    setTimeout(() => $("cmInput").focus(), 30);
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
    suggestions.forEach((m, k) => {
      const li = document.createElement("li");
      const b = document.createElement("button");
      b.type = "button";
      b.setAttribute("role", "option");
      if (k === active) b.classList.add("active");
      b.append(spriteImg(m, false, false));
      const t = document.createElement("span");
      t.textContent = m.display_name + (used.has(m.key) ? " (already on the cube)" : "");
      b.appendChild(t);
      b.addEventListener("click", () => submitPick(m));
      li.appendChild(b);
      ul.appendChild(li);
    });
  }

  async function submitPick(mon) {
    if (submitting || !S.sel) return;
    submitting = true;
    const [f, i, j] = S.sel;
    try {
      const r = await api("/cube/validate", { seed: S.board.seed, f, i, j, pokemon_key: mon.key, used_keys: usedKeys() });
      if (r.ok) {
        S.picks.set(ck(f, i, j), { mon: r.mon, hinted: false, shiny: r.shiny, pct: r.pct });
        if (r.lives_left != null) S.lives = r.lives_left;
        hide($("cmPick"));
        if (r.shiny) toast(`A shiny ${r.mon.display_name}!`, "ok");
        if (r.stranded) toast("Heads up: with the Pokémon used so far, not every open square can still be filled.", "err");
        await afterAnswer(f, i, j, r.finished);
      } else {
        const msgs = {
          does_not_match: `${mon.display_name} doesn't fit all three rules.`,
          duplicate: `${mon.display_name} is already on the cube. Each Pokémon goes on once.`,
          unknown_pokemon: "Pick a Pokémon from the list.",
          already_filled: "That square is already filled.",
          board_locked: "This Cube is finished.",
        };
        setStatus("cmStatus", msgs[r.reason] || "That didn't work.", "err");
        if (r.reason === "does_not_match" || r.reason === "duplicate") {
          S.lives = r.lives_left != null ? r.lives_left : Math.max(0, S.lives - 1);
          pips($("cmLives"), S.maxLives, S.lives, "pip-life");
          const el = stEls.get(ck(f, i, j));
          el?.classList.remove("err"); void el?.offsetWidth; el?.classList.add("err");
          if (S.lives <= 0) { S.finished = true; hide($("cmPick")); await loadAnswers(); }
        }
        if (r.reason === "board_locked") { S.finished = true; hide($("cmPick")); await loadAnswers(); }
        savePractice();
        render();
      }
    } catch (e) {
      setStatus("cmStatus", e.message, "err");
    }
    submitting = false;
  }

  async function afterAnswer(f, i, j, finished) {
    S.sel = null;
    if (S.picks.size >= 54 || finished) {
      S.finished = true;
      if (S.picks.size < 54) await loadAnswers();
    }
    savePractice();
    render();
    const el = stEls.get(ck(f, i, j));
    if (el) { el.classList.add("pop"); setTimeout(() => el.classList.remove("pop"), 500); el.focus({ preventScroll: true }); }
  }

  async function useHint() {
    if (!S.sel || S.hints <= 0) return;
    const [f, i, j] = S.sel;
    const filled = Array.from(S.picks.entries()).map(([k, v]) => { const [a, b, c] = k.split(",").map(Number); return { f: a, i: b, j: c, key: v.mon.key }; });
    try {
      const r = await api("/cube/hint", { seed: S.board.seed, f, i, j, used_keys: usedKeys(), filled });
      if (!r.ok) { setStatus("cmStatus", r.reason === "no_hints" ? "No hints left." : "No hint for this square.", "err"); return; }
      S.hints = r.hints_left != null ? r.hints_left : S.hints - 1;
      S.picks.set(ck(f, i, j), { mon: r.mon, hinted: true, shiny: false, pct: null });
      hide($("cmPick"));
      await afterAnswer(f, i, j, r.finished);
    } catch (e) { setStatus("cmStatus", e.message, "err"); }
  }

  function setupPick() {
    const input = $("cmInput");
    input.addEventListener("input", () => { clearTimeout(suggestTimer); suggestTimer = setTimeout(suggest, 120); });
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
    $("cmPick").addEventListener("close", () => { S.sel = null; paintStickers(); renderLabels(); });
  }

  // ── Reverse: guessing a face or ring ─────────────────────────────────────
  let groupFilter = "all";
  const GROUP_NAMES = { all: "All", type: "Types", region: "Regions", evolution: "Evolution", move: "Moves", ability: "Abilities", other: "Other" };

  async function openGuess(line) {
    if (!S.revRun || S.revRun.finished || revSolved(line)) return;
    if (!S.catalog) {
      try { S.catalog = await api("/rules/catalog"); } catch (e) { toast(e.message, "err"); return; }
    }
    S.guess = { line, rule: null };
    let title;
    if (line < 6) title = `What do all nine on ${FACE_NAMES[line]} share?`;
    else {
      const { cols } = frontLines();
      title = `What do all twelve in this ${cols.includes(line) ? "column" : "row"} ring share?`;
    }
    $("cmGuessTitle").textContent = title;
    const slice = $("cgSlice");
    slice.textContent = "";
    for (const [f, i, j] of cellsOf(line)) {
      const o = occupant(f, i, j);
      const fig = document.createElement("figure");
      const cap = document.createElement("figcaption");
      cap.textContent = o.mon.display_name;
      fig.append(spriteImg(o.mon, false, true), cap);
      slice.appendChild(fig);
    }
    $("cgSearch").value = "";
    groupFilter = "all";
    drawGroups(); drawRules();
    setStatus("cgStatus", "");
    updateGuessFoot();
    paintStickers(); renderLabels();
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
    for (const g of groups) for (const r of S.catalog.groups[g]) {
      if (q && !r.label.toLowerCase().includes(q)) continue;
      const b = document.createElement("button");
      b.type = "button";
      b.setAttribute("role", "option");
      b.setAttribute("aria-selected", String(S.guess?.rule?.rule_id === r.rule_id));
      b.textContent = r.label;
      b.addEventListener("click", () => { S.guess.rule = r; drawRules(); updateGuessFoot(); });
      list.appendChild(b);
      n++;
    }
    if (!n) { const p = document.createElement("p"); p.className = "cm-status"; p.textContent = "No category matches that search."; list.appendChild(p); }
  }
  function updateGuessFoot() {
    const run = S.revRun;
    pips($("cgLives"), S.rev.lives, run.lives_left, "pip-life");
    $("cgHint").disabled = run.hints_left <= 0;
    $("cgHint").textContent = run.hints_left > 0 ? `Reveal this one (${run.hints_left} left)` : "No reveals left";
    $("cgGo").disabled = !S.guess?.rule;
    $("cgGo").textContent = S.guess?.rule ? `Guess ${S.guess.rule.label}` : "Guess";
  }
  async function submitGuess(hint) {
    const g = S.guess;
    if (!g || (!hint && !g.rule)) return;
    try {
      const r = await api(hint ? "/cube/reverse/hint" : "/cube/reverse/validate",
        { board_id: S.rev.board_id, line: g.line, rule_id: hint ? "" : g.rule.rule_id });
      S.revRun = r.run;
      if (S.rev === S.revPractice) S.revPractice.run = r.run;
      if (r.ok) {
        hide($("cmGuess"));
        toast(hint ? `Revealed: ${r.label}` : `Right, they're all ${r.label}.`, "ok");
      } else if (r.reason === "wrong") {
        setStatus("cgStatus", `Not ${r.label}. ${r.run.lives_left} ${r.run.lives_left === 1 ? "life" : "lives"} left.`, "err");
        updateGuessFoot();
        if (r.run.finished) hide($("cmGuess"));
      } else if (r.reason === "board_locked") hide($("cmGuess"));
      else setStatus("cgStatus", r.reason === "no_hints" ? "No reveals left." : "That didn't work.", "err");
      render();
    } catch (e) { setStatus("cgStatus", e.message, "err"); }
  }
  function setupGuess() {
    $("cgSearch").addEventListener("input", drawRules);
    $("cgGo").addEventListener("click", () => submitGuess(false));
    $("cgHint").addEventListener("click", () => submitGuess(true));
    $("cmGuess").addEventListener("close", () => { S.guess = null; paintStickers(); renderLabels(); });
  }

  // ── Console + dialogs ────────────────────────────────────────────────────
  function setupConsole() {
    document.querySelectorAll("[data-kind]").forEach((b) => b.addEventListener("click", () => {
      if (S.kind === b.dataset.kind) return;
      S.kind = b.dataset.kind; writeHash(); load();
    }));
    document.querySelectorAll("[data-which]").forEach((b) => b.addEventListener("click", () => {
      if (S.which === b.dataset.which) return;
      S.which = b.dataset.which; writeHash(); load();
    }));
    $("ccNew").addEventListener("click", () => newPractice());
    $("ccBannerBtn").addEventListener("click", () => {
      if (S.which === "daily") { S.which = "practice"; writeHash(); }
      newPractice();
    });
    document.querySelectorAll("dialog.cm [data-close]").forEach((b) => b.addEventListener("click", () => hide(b.closest("dialog"))));
    document.querySelectorAll("dialog.cm").forEach((d) => d.addEventListener("click", (e) => { if (e.target === d) hide(d); }));
    $("cubeHowBtn").addEventListener("click", () => show($("cmHow")));
  }
  async function newPractice() {
    if (S.kind === "fill") store(PRACTICE_KEY, null);
    await load(true);
  }
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
  window.addEventListener("hashchange", () => { readHash(); load(); });
  buildCube();
  setupDrag();
  setupPick();
  setupGuess();
  setupConsole();
  load();
  (document.fonts ? document.fonts.ready : Promise.resolve()).then(() => renderLabels());
  try {
    if (!localStorage.getItem("route81.cube54.seenHow")) {
      localStorage.setItem("route81.cube54.seenHow", "1");
      setTimeout(() => show($("cmHow")), 300);
    }
  } catch (_) { /* storage blocked */ }
})();
