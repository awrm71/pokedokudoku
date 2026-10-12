const CDN_BASE = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/other/official-artwork";
const PLACEHOLDER =
  "data:image/svg+xml;utf8," +
  encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96" viewBox="0 0 96 96"><rect width="96" height="96" rx="14" fill="#eef3ed"/><circle cx="48" cy="48" r="20" fill="#d5dfd6"/><text x="48" y="54" text-anchor="middle" font-family="Arial,sans-serif" font-size="24" fill="#466057">?</text></svg>'
  );

const state = {
  apiBase: "",
  mode: "auto",
  runtimeMode: "api",
  board: null,
  cells: new Map(),
  activeCellId: null,
  lives: 3,
  hints: 3,
  gameOver: false,
};

// ── Custom type badges — no copyrighted images ───────────────────────────────
const TYPE_CONFIG = {
  normal:   { bg: "#888888", fg: "#fff",    icon: "○" },
  fire:     { bg: "#E84000", fg: "#fff",    icon: "☀" },
  fighting: { bg: "#B81414", fg: "#fff",    icon: "⚔" },
  water:    { bg: "#1B7FE8", fg: "#fff",    icon: "≋" },
  ice:      { bg: "#20CCDD", fg: "#222",    icon: "❄" },
  grass:    { bg: "#2DAA4C", fg: "#fff",    icon: "✿" },
  bug:      { bg: "#7DA900", fg: "#fff",    icon: "✳" },
  ground:   { bg: "#C47A00", fg: "#fff",    icon: "≡" },
  rock:     { bg: "#A08045", fg: "#fff",    icon: "⛏" },
  flying:   { bg: "#91B2F5", fg: "#222",    icon: "✈" },
  electric: { bg: "#FFD700", fg: "#222",    icon: "⚡" },
  psychic:  { bg: "#E8205A", fg: "#fff",    icon: "✦" },
  fairy:    { bg: "#E85FAA", fg: "#fff",    icon: "♥" },
  poison:   { bg: "#8B2BAE", fg: "#fff",    icon: "◎" },
  ghost:    { bg: "#5B3E7A", fg: "#fff",    icon: "☠" },
  dragon:   { bg: "#3425C8", fg: "#fff",    icon: "∞" },
  dark:     { bg: "#2E2B3A", fg: "#eee",    icon: "☽" },
  steel:    { bg: "#6A8FAD", fg: "#fff",    icon: "⬡" },
};
const TYPE_SET = new Set(Object.keys(TYPE_CONFIG));

// ── Deterministic board display shuffle ──────────────────────────────────────
function seededPermutation(n, seedStr) {
  let s = 0;
  for (let i = 0; i < seedStr.length; i++) {
    s = Math.imul(s, 31) + seedStr.charCodeAt(i) | 0;
  }
  s = Math.abs(s) || 1;
  const perm = Array.from({ length: n }, (_, i) => i);
  for (let i = n - 1; i > 0; i--) {
    s = Math.imul(s, 1664525) + 1013904223 | 0;
    const j = Math.abs(s) % (i + 1);
    [perm[i], perm[j]] = [perm[j], perm[i]];
  }
  return perm;
}
function shuffleBoardDisplay(board) {
  board.rowPerm = seededPermutation(board.n_rows, board.seed + "_R");
  board.colPerm = seededPermutation(board.n_cols, board.seed + "_C");
  // Defensive: row_rule_ids/col_rule_ids are only used for the hint-tooltip
  // type-badge icon (see ruleHeadContent) — labels are what actually drive
  // gameplay. If ids are ever missing (e.g. a stale server response), fall
  // back to placeholders instead of crashing the whole board load.
  const rowIds = board.row_rule_ids || board.row_rule_labels.map((_, i) => `row_${i}`);
  const colIds = board.col_rule_ids || board.col_rule_labels.map((_, i) => `col_${i}`);
  board.row_rule_ids    = board.rowPerm.map(i => rowIds[i]);
  board.row_rule_labels = board.rowPerm.map(i => board.row_rule_labels[i]);
  board.col_rule_ids    = board.colPerm.map(i => colIds[i]);
  board.col_rule_labels = board.colPerm.map(i => board.col_rule_labels[i]);
}

const boardWrap = document.getElementById("boardWrap");
// boardMeta / message now target whichever mode context panel is visible, so a
// Draft board's status shows under the Draft tab and Daily's under Daily. Falls
// back to the Daily panel's elements if nothing's active yet.
const _dailyMeta = document.getElementById("boardMeta");
const _dailyMsg = document.getElementById("message");
function _activeCtxEl(cls, fallback) {
  const panel = document.querySelector(".ctx-panel:not(.hidden)");
  return (panel && panel.querySelector(cls)) || fallback;
}
const boardMeta = {
  set textContent(v) { const el = _activeCtxEl(".ctx-meta", _dailyMeta); if (el) el.textContent = v; },
  get textContent() { const el = _activeCtxEl(".ctx-meta", _dailyMeta); return el ? el.textContent : ""; },
};
const message = _dailyMsg; // legacy ref kept for any direct users; setMessage below is panel-aware
const apiBaseInput = document.getElementById("apiBase");
const modeSelect = document.getElementById("modeSelect");
// Old Daily/Shuffle buttons are gone; Shuffle now lives in the Daily context bar.
const loadRandomBtn = document.getElementById("ctxShuffleBtn");
const cellTemplate = document.getElementById("cellTemplate");

const MOCK_POKEMON = [
  ["bulbasaur", "Bulbasaur", 1, "grass", "poison", 1, false, false],
  ["ivysaur", "Ivysaur", 2, "grass", "poison", 1, false, false],
  ["venusaur", "Venusaur", 3, "grass", "poison", 1, false, false],
  ["charmander", "Charmander", 4, "fire", "", 1, false, false],
  ["charmeleon", "Charmeleon", 5, "fire", "", 1, false, false],
  ["charizard", "Charizard", 6, "fire", "flying", 1, false, false],
  ["squirtle", "Squirtle", 7, "water", "", 1, false, false],
  ["wartortle", "Wartortle", 8, "water", "", 1, false, false],
  ["blastoise", "Blastoise", 9, "water", "", 1, false, false],
  ["caterpie", "Caterpie", 10, "bug", "", 1, false, false],
  ["pikachu", "Pikachu", 25, "electric", "", 1, false, false],
  ["raichu", "Raichu", 26, "electric", "", 1, false, false],
  ["sandshrew", "Sandshrew", 27, "ground", "", 1, false, false],
  ["nidoran-f", "Nidoran F", 29, "poison", "", 1, false, false],
  ["nidoran-m", "Nidoran M", 32, "poison", "", 1, false, false],
  ["clefairy", "Clefairy", 35, "fairy", "", 1, false, false],
  ["vulpix", "Vulpix", 37, "fire", "", 1, false, false],
  ["jigglypuff", "Jigglypuff", 39, "normal", "fairy", 1, false, false],
  ["zubat", "Zubat", 41, "poison", "flying", 1, false, false],
  ["oddish", "Oddish", 43, "grass", "poison", 1, false, false],
  ["diglett", "Diglett", 50, "ground", "", 1, false, false],
  ["meowth", "Meowth", 52, "normal", "", 1, false, false],
  ["psyduck", "Psyduck", 54, "water", "", 1, false, false],
  ["growlithe", "Growlithe", 58, "fire", "", 1, false, false],
  ["abra", "Abra", 63, "psychic", "", 1, false, false],
  ["machop", "Machop", 66, "fighting", "", 1, false, false],
  ["bellsprout", "Bellsprout", 69, "grass", "poison", 1, false, false],
  ["geodude", "Geodude", 74, "rock", "ground", 1, false, false],
  ["gastly", "Gastly", 92, "ghost", "poison", 1, false, false],
  ["onix", "Onix", 95, "rock", "ground", 1, false, false],
  ["eevee", "Eevee", 133, "normal", "", 1, false, false],
  ["snorlax", "Snorlax", 143, "normal", "", 1, false, false],
  ["dratini", "Dratini", 147, "dragon", "", 1, false, false],
  ["dragonair", "Dragonair", 148, "dragon", "", 1, false, false],
  ["dragonite", "Dragonite", 149, "dragon", "flying", 1, false, false],
  ["mewtwo", "Mewtwo", 150, "psychic", "", 1, true, false],
  ["mew", "Mew", 151, "psychic", "", 1, false, true],
].map(([key, display_name, pokemon_id, type1, type2, gen, is_legendary, is_mythical]) => ({
  key,
  display_name,
  pokemon_id,
  type1,
  type2,
  gen,
  is_legendary,
  is_mythical,
}));

const MOCK_RULES = [
  "Fire",
  "Water",
  "Grass",
  "Electric",
  "Flying",
  "Poison",
  "G1",
  "G2",
  "Legendary",
  "Myth",
  "Dual-type",
  "Single-type",
  "Dark",
  "Psychic",
  "Dragon",
];

function normalizeName(s) {
  return (s || "").toLowerCase().replace(/[^a-z0-9]/g, "");
}

function hashString(s) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i += 1) {
    h ^= s.charCodeAt(i);
    h += (h << 1) + (h << 4) + (h << 7) + (h << 8) + (h << 24);
  }
  return h >>> 0;
}

function seededRng(seedText) {
  let seed = hashString(seedText || "seed");
  return () => {
    seed = (1664525 * seed + 1013904223) >>> 0;
    return seed / 4294967296;
  };
}

function sampleUnique(arr, count, rng) {
  const pool = [...arr];
  const out = [];
  while (pool.length > 0 && out.length < count) {
    const idx = Math.floor(rng() * pool.length);
    out.push(pool.splice(idx, 1)[0]);
  }
  return out;
}

function buildMockBoard(seed, n = 9) {
  const rng = seededRng(seed || String(Date.now()));
  const tries = 300;

  for (let t = 0; t < tries; t += 1) {
    const row_rule_labels = sampleUnique(MOCK_RULES, n, rng);
    const col_rule_labels = sampleUnique([...MOCK_RULES].reverse(), n, rng);

    let valid = true;
    for (let i = 0; i < n && valid; i += 1) {
      for (let j = 0; j < n; j += 1) {
        const candidates = MOCK_POKEMON.filter(
          (p) => matchesRuleLabel(p, row_rule_labels[i]) && matchesRuleLabel(p, col_rule_labels[j])
        );
        if (candidates.length === 0) {
          valid = false;
          break;
        }
      }
    }

    if (valid) {
      return {
        seed: seed || String(Date.now()),
        n,
        row_rule_labels,
        col_rule_labels,
        row_rule_ids: row_rule_labels.map((x, i) => `mock_r_${i}_${normalizeName(x)}`),
        col_rule_ids: col_rule_labels.map((x, i) => `mock_c_${i}_${normalizeName(x)}`),
      };
    }
  }

  // Fallback deterministic board if random sampling didn't find a fully valid one.
  const row_rule_labels = ["Flying", "Dragon", "Fire", "Water", "Grass", "Poison", "Electric", "Psychic", "Single-type"].slice(0, n);
  const col_rule_labels = ["Dragon", "Flying", "Poison", "Fire", "Water", "Grass", "Electric", "Legendary", "Myth"].slice(0, n);
  return {
    seed: seed || String(Date.now()),
    n,
    row_rule_labels,
    col_rule_labels,
    row_rule_ids: row_rule_labels.map((x, i) => `mock_r_${i}_${normalizeName(x)}`),
    col_rule_ids: col_rule_labels.map((x, i) => `mock_c_${i}_${normalizeName(x)}`),
  };
}

function matchesRuleLabel(pokemon, ruleLabel) {
  const label = String(ruleLabel || "").trim().toLowerCase();
  if (!label) return true;

  if (label === "legendary") return !!pokemon.is_legendary;
  if (label === "myth" || label === "mythical") return !!pokemon.is_mythical;
  if (label === "single-type") return !pokemon.type2;
  if (label === "dual-type") return !!pokemon.type2;
  if (label === "g1") return pokemon.gen === 1;
  if (label === "g2") return pokemon.gen === 2;

  // Type rules should match either primary or secondary type.
  return pokemon.type1 === label || pokemon.type2 === label;
}

async function suggestPokemon(prefix) {
  if (state.runtimeMode === "mock") {
    const pfx = normalizeName(prefix);
    if (pfx.length < 2) return [];
    return MOCK_POKEMON.filter((p) => normalizeName(p.display_name).startsWith(pfx)).slice(0, 6);
  }

  // Multi-word handling: "mega charizard", "gmax snorlax", "alolan vulpix"
  // Dataset stores names as "Charizard Mega X" so prefix-searching "mega charizard" fails.
  // Fix: search using the LONGEST word (most specific), then AND-filter by all others.
  const rawWords = prefix.trim().toLowerCase().split(/\s+/).filter(w => w.length >= 2);
  if (rawWords.length >= 2) {
    const normWord = w => w.replace(/[^a-z0-9]/g, "");
    const normedWords = rawWords.map(normWord).filter(w => w.length >= 2);
    // Pick the longest word as the search term (most distinctive)
    const searchTerm = normedWords.reduce((a, b) => a.length >= b.length ? a : b);
    const otherTerms = normedWords.filter(w => w !== searchTerm);
    // Fetch more results so filters have enough to work with
    const data = await fetchJson(
      `${state.apiBase}/pokemon/suggest?prefix=${encodeURIComponent(searchTerm)}&limit=50`
    );
    if (!Array.isArray(data)) return [];
    // Keep only results whose normalized name contains ALL other terms
    return data.filter(item => {
      const normName = normWord((item.display_name || item.key || "").toLowerCase());
      return otherTerms.every(term => normName.includes(term));
    }).slice(0, 6);
  }

  return fetchJson(
    `${state.apiBase}/pokemon/suggest?prefix=${encodeURIComponent(prefix)}&limit=6`
  );
}

async function resolvePokemon(name) {
  if (state.runtimeMode === "mock") {
    const n = normalizeName(name);
    const found = MOCK_POKEMON.find((p) => normalizeName(p.display_name) === n);
    if (!found) {
      throw new Error("Unknown pokemon");
    }
    return found;
  }
  return fetchJson(`${state.apiBase}/pokemon/resolve?name=${encodeURIComponent(name)}`);
}

async function validatePokemon(payload) {
  if (state.runtimeMode === "mock") {
    if (!payload.pokemon_key) return { ok: false, reason: "unknown_pokemon" };
    if (payload.used_keys.includes(payload.pokemon_key)) return { ok: false, reason: "duplicate" };
    const picked = MOCK_POKEMON.find((p) => p.key === payload.pokemon_key);
    if (!picked) return { ok: false, reason: "unknown_pokemon" };

    const rr = state.board?.row_rule_labels?.[payload.row_i];
    const cc = state.board?.col_rule_labels?.[payload.col_j];
    if (!matchesRuleLabel(picked, rr) || !matchesRuleLabel(picked, cc)) {
      return { ok: false, reason: "does_not_match" };
    }
    return { ok: true, reason: "ok" };
  }
  // Convert display indices → original server indices using stored permutation
  const serverPayload = { ...payload };
  if (state.board?.rowPerm) serverPayload.row_i = state.board.rowPerm[payload.row_i];
  if (state.board?.colPerm) serverPayload.col_j = state.board.colPerm[payload.col_j];
  return fetchJson(`${state.apiBase}/validate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(serverPayload),
  });
}

function setMessage(text, tone = "") {
  const el = _activeCtxEl(".ctx-msg", _dailyMsg);
  if (!el) return;
  el.textContent = text;
  el.style.color = tone || "";
}

function getCellId(row, col) {
  return `${row}:${col}`;
}

function formatRuleLabel(label) {
  const raw = String(label || "").trim();
  if (!raw) return "";

  const lower = raw.toLowerCase();
  if (lower === "single-type") return "Single Type";
  if (lower === "dual-type") return "Dual Type";
  if (lower === "legendary") return "Legendary";
  if (lower === "myth") return "Myth";
  if (/^g\d+$/.test(lower)) return lower.toUpperCase();

  // Preserve preformatted labels that include special punctuation.
  // (e.g. "Paldea + DLC", "Evolved By: Item", "Move: Protect")
  if (/[+()/]/.test(raw)) return raw;
  if (raw.includes(":")) return raw;

  // For simple tokens, normalize separators and title-case.
  const cleaned = raw.replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim();
  return cleaned
    .split(" ")
    .map((w) => (w ? w.charAt(0).toUpperCase() + w.slice(1).toLowerCase() : ""))
    .join(" ");
}

function normalizeRuleToken(label) {
  return String(label || "")
    .trim()
    .toLowerCase()
    .replace(/\s+/g, " ")
    .replace(/[^a-z0-9 -]/g, "")
    .replace(/\s/g, "-");
}

function buildTypeBadge(typeKey, cfg) {
  const wrap = document.createElement("span");
  wrap.className = "type-badge-custom";
  wrap.style.cssText = `background:${cfg.bg};color:${cfg.fg};`;
  wrap.setAttribute("title", typeKey.charAt(0).toUpperCase() + typeKey.slice(1));
  const iconEl = document.createElement("span");
  iconEl.className = "tbi";
  iconEl.textContent = cfg.icon;
  iconEl.setAttribute("aria-hidden", "true");
  const nameEl = document.createElement("span");
  nameEl.className = "tbn";
  nameEl.textContent = typeKey.toUpperCase();
  wrap.appendChild(iconEl);
  wrap.appendChild(nameEl);
  return wrap;
}

function ruleHeadContent(label, ruleId) {
  const el = _ruleHeadContentInner(label, ruleId);
  // Attach a styled hover tooltip (see attachCategoryTooltip) instead of the
  // browser's native title, so it matches the site and is readable.
  const help = window.PDD_CATEGORY_HELP;
  if (help && el) {
    const text = help.explain(formatRuleLabel(label));
    if (text) attachCategoryTooltip(el, formatRuleLabel(label), text);
  }
  return el;
}

function _ruleHeadContentInner(label, ruleId) {
  // Primary: rule ID format is always "type_<name>" — most reliable
  if (ruleId && ruleId.startsWith("type_")) {
    const typeKey = ruleId.slice(5);
    const cfg = TYPE_CONFIG[typeKey];
    if (cfg) return buildTypeBadge(typeKey, cfg);
  }
  // Fallback: normalise the label (mock mode / missing ruleId)
  const token = normalizeRuleToken(label);
  const rawType = token.startsWith("type-") ? token.slice(5) : token;
  const typeKey = rawType.replace(/-/g, "");
  if (TYPE_SET.has(typeKey)) return buildTypeBadge(typeKey, TYPE_CONFIG[typeKey]);

  const span = document.createElement("span");
  span.className = "rule-badge text";
  span.textContent = formatRuleLabel(label);
  return span;
}

// ── Styled category hover tooltip ────────────────────────────────────────────
// One shared floating element, positioned above (or beside) the hovered badge.
// Matches the site's card/pixel style. Desktop hover + keyboard focus; on touch
// there's no hover, but the cell modal already shows the same explainer inline.
let _catTip = null;
function _ensureCatTip() {
  if (_catTip) return _catTip;
  _catTip = document.createElement("div");
  _catTip.className = "cat-tip";
  _catTip.setAttribute("role", "tooltip");
  _catTip.innerHTML = '<span class="cat-tip-title"></span><span class="cat-tip-body"></span>';
  document.body.appendChild(_catTip);
  return _catTip;
}
function attachCategoryTooltip(el, title, body) {
  el.classList.add("has-cat-tip");
  const show = () => {
    const tip = _ensureCatTip();
    tip.querySelector(".cat-tip-title").textContent = title;
    tip.querySelector(".cat-tip-body").textContent = body;
    tip.classList.add("visible");
    // Position: centered above the badge; flip below if not enough room.
    const r = el.getBoundingClientRect();
    tip.style.left = "0px"; tip.style.top = "0px";  // reset for measure
    const tr = tip.getBoundingClientRect();
    let left = r.left + r.width / 2 - tr.width / 2;
    left = Math.max(8, Math.min(left, window.innerWidth - tr.width - 8));
    let top = r.top - tr.height - 10;
    tip.classList.toggle("below", top < 8);
    if (top < 8) top = r.bottom + 10;
    tip.style.left = `${left}px`;
    tip.style.top = `${top}px`;
  };
  const hide = () => { if (_catTip) _catTip.classList.remove("visible"); };
  el.addEventListener("mouseenter", show);
  el.addEventListener("mouseleave", hide);
  el.addEventListener("focus", show);
  el.addEventListener("blur", hide);
}

function spriteFromPokemonId(pokemonId) {
  if (!pokemonId) return PLACEHOLDER;
  // In API mode, prefer local sprites served by the backend (with CDN redirect fallback).
  if (state.runtimeMode !== "mock") {
    return `${state.apiBase}/sprite/${pokemonId}.png`;
  }
  // In mock mode, keep everything self-contained.
  return `${CDN_BASE}/${pokemonId}.png`;
}

function setSpriteSafe(imgEl, src) {
  imgEl.onerror = () => {
    imgEl.onerror = null;
    imgEl.src = PLACEHOLDER;
  };
  imgEl.src = src || PLACEHOLDER;
}

function setCellSprites(cellEl, src, showSummary = false) {
  const summarySprite = cellEl.querySelector(".summary-sprite");
  setSpriteSafe(summarySprite, src || PLACEHOLDER);
  summarySprite.classList.toggle("hidden", !showSummary);
}

// ── Pokedex client id ─────────────────────────────────────────────────────
// A stable, anonymous per-browser id so the backend can track which Pokemon
// this device has caught on daily boards — no account required yet. When
// real accounts exist, this same id gets passed to POST /pokedex/claim once
// at signup/login to fold guest progress into the new account; nothing here
// needs to change for that to work.
const CLIENT_ID_KEY = "pokedokudoku_client_id";
function getClientId() {
  let id = localStorage.getItem(CLIENT_ID_KEY);
  if (!id) {
    id = (typeof crypto !== "undefined" && crypto.randomUUID)
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    localStorage.setItem(CLIENT_ID_KEY, id);
  }
  return id;
}

async function fetchJson(url, options = undefined) {
  const opts = { ...(options || {}) };
  opts.headers = { ...(opts.headers || {}), "X-Client-Id": getClientId() };
  const res = await fetch(url, opts);
  if (!res.ok) {
    // FastAPI error responses are {"detail": "..."} — surface that message
    // directly when present, since it's usually the actually-useful part
    // (e.g. Draft Board's friendly "couldn't build that board" explanations).
    let detail = null;
    try {
      const body = await res.json();
      detail = body && typeof body.detail === "string" ? body.detail : null;
    } catch (_) {
      // response wasn't JSON — fall through to the generic message
    }
    throw new Error(detail || `Request failed (${res.status})`);
  }
  return res.json();
}

function clearBoardState() {
  state.cells.clear();
  state.activeCellId = null;
}

function resetGameState() {
  state.lives = 3;
  state.hints = 3;
  state.gameOver = false;
  clearBoardState();

  const gameOverBanner = document.getElementById("gameOverBanner");
  if (gameOverBanner) gameOverBanner.classList.remove("visible");
  const winBanner = document.getElementById("winBanner");
  if (winBanner) winBanner.classList.remove("visible");
  boardWrap.classList.remove("board-locked");
}

function updateHUD() {
  if (!state.board) return;

  // Progress
  let correct = 0;
  for (const cell of state.cells.values()) {
    if (cell.locked) correct++;
  }
  const total = state.board.n_rows * state.board.n_cols;
  const pct = total > 0 ? (correct / total) * 100 : 0;

  const progText = document.getElementById("progressText");
  const progBar  = document.getElementById("progressBar");
  const progWrap = document.getElementById("progressBarWrap");
  if (progText) progText.textContent = `${correct} / ${total}`;
  if (progBar)  progBar.style.width = `${pct}%`;
  if (progWrap) progWrap.setAttribute("aria-valuenow", correct);

  // Lives
  document.querySelectorAll(".pip-life").forEach((pip, i) => {
    const alive = i < state.lives;
    pip.textContent = alive ? "❤️" : "🖤";
    pip.classList.toggle("spent", !alive);
  });

  // Hints (HUD pips)
  document.querySelectorAll(".pip-hint").forEach((pip, i) => {
    const available = i < state.hints;
    pip.classList.toggle("spent", !available);
    pip.disabled = !available || state.gameOver;
  });

  // Keep modal hint stars and lives in sync
  syncModalHints();
  syncModalLives();
  // Daily answer stats follow whichever board is on screen (stats.js).
  window.PDD_STATS?.sync();

  // Trigger game over / win
  if (state.lives <= 0 && !state.gameOver) {
    triggerGameOver();
  } else if (correct === total && total > 0 && !state.gameOver) {
    triggerWin(correct, total);
  }
}

function triggerGameOver() {
  state.gameOver = true;
  boardWrap.classList.add("board-locked");
  const banner = document.getElementById("gameOverBanner");
  if (banner) banner.classList.add("visible");
  document.querySelectorAll(".pip-hint").forEach((pip) => { pip.disabled = true; });
  setMessage("💀 No lives remaining — board locked.", "var(--err)");
  window.PDD_STATS?.boardEnded();
}

function triggerWin(correct, total) {
  state.gameOver = true;
  const banner = document.getElementById("winBanner");
  if (banner) banner.classList.add("visible");
  setMessage(`🏆 Board complete! ${correct}/${total} correct!`, "var(--ok)");
  window.PDD_STATS?.boardEnded();
}

async function useHint() {
  if (state.gameOver || state.hints <= 0) return;

  const cellId = state.activeCellId;
  if (!cellId) {
    setMessage("Click a cell first, then press a ⭐ hint!", "var(--warn)");
    return;
  }

  const cell = state.cells.get(cellId);
  if (!cell || cell.locked) {
    setMessage("That cell is already filled — select an empty one first.", "var(--warn)");
    return;
  }

  const [row, col] = cellId.split(":").map(Number);
  const usedKeys = getUsedKeysExcept(cellId);

  // Mock mode: pick any valid Pokémon from MOCK_POKEMON for this cell
  if (state.runtimeMode === "mock") {
    const rr = state.board?.row_rule_labels?.[row];
    const cc = state.board?.col_rule_labels?.[col];
    const candidates = MOCK_POKEMON.filter(
      (p) => matchesRuleLabel(p, rr) && matchesRuleLabel(p, cc) && !usedKeys.includes(p.key)
    );
    if (candidates.length === 0) {
      setMessage("No valid hint found for this cell.", "var(--warn)");
      return;
    }
    const pick = candidates[Math.floor(Math.random() * candidates.length)];
    applyHintToCell(cellId, cell, { key: pick.key, display_name: pick.display_name, pokemon_id: pick.pokemon_id });
    return;
  }

  setMessage("Finding a hint…");
  try {
    const params = new URLSearchParams({
      seed: state.board.seed,
      n_rows: String(state.board.n_rows),
      n_cols: String(state.board.n_cols),
      row_i: String(state.board?.rowPerm ? state.board.rowPerm[row] : row),
      col_j: String(state.board?.colPerm ? state.board.colPerm[col] : col),
      used_keys: usedKeys.join(","),
    });
    const result = await fetchJson(`${state.apiBase}/hint?${params.toString()}`);
    applyHintToCell(cellId, cell, result);
    syncDailyHints(result);
  } catch (err) {
    setMessage(err?.message?.startsWith("Request failed") || !err?.message
      ? "Couldn't find a hint for this cell — try another!" : err.message, "var(--warn)");
  }
}

function applyHintToCell(cellId, cell, result) {
  cell.locked = true;
  cell.key    = result.key;
  cell.displayName = result.display_name;
  cell.pokemonId   = result.pokemon_id;
  cell.isHint      = true;
  state.cells.set(cellId, cell);

  const cellEl = boardWrap.querySelector(`.cell[data-cell-id="${cellId}"]`);
  if (cellEl) {
    const input = cellEl.querySelector(".name-input");
    if (input) { input.value = result.display_name; input.disabled = true; }
    setCellSprites(cellEl, spriteFromPokemonId(result.pokemon_id), true);
    setPickedName(cellEl, result.display_name);
    setResultMark(cellEl, "ok");
    paintCellStatus(cellEl, "⭐ Hint used", "ok");
    cellEl.classList.add("locked", "hint-cell");
    cellEl.classList.remove("drafted", "err");
  }

  state.hints = Math.max(0, state.hints - 1);
  updateHUD();
  setMessage(`Hint revealed! ${state.hints} hint${state.hints !== 1 ? "s" : ""} remaining.`, "var(--ok)");
}

// On the Daily the server counts hints, so its number wins over ours.
function syncDailyHints(result) {
  if (typeof result?.hints_left === "number" && result.hints_left !== state.hints) {
    state.hints = result.hints_left;
    updateHUD();
  }
}

function getUsedKeysExcept(cellId) {
  const used = [];
  for (const [id, cell] of state.cells.entries()) {
    if (id !== cellId && cell.key) {
      used.push(cell.key);
    }
  }
  return used;
}

function paintCellStatus(cellEl, statusText, tone = "warn") {
  const status = cellEl.querySelector(".cell-status");
  status.textContent = statusText;
  cellEl.classList.remove("ok", "err", "warn");
  cellEl.classList.add(tone);
}

function setPickedName(cellEl, text) {
  const picked = cellEl.querySelector(".picked-name");
  picked.textContent = text;
  // Longest single word drives a font step-down in index.html's styles, so
  // names like "Crabominable" fit on one line instead of breaking mid-word.
  const longest = Math.max(0, ...String(text || "").split(/\s+/).map((w) => w.length));
  picked.style.setProperty("--name-len", String(Math.max(9, longest)));
}

function setResultMark(cellEl, markType) {
  const mark = cellEl.querySelector(".result-mark");
  if (!mark) return;
  if (markType === "ok") {
    mark.textContent = "✓";
    mark.setAttribute("aria-label", "Correct");
    return;
  }
  if (markType === "err") {
    mark.textContent = "✕";
    mark.setAttribute("aria-label", "Incorrect");
    return;
  }
  mark.textContent = "";
  mark.removeAttribute("aria-label");
}

function setActiveCell(cellId) {
  if (state.activeCellId === cellId) {
    return;
  }
  boardWrap.querySelectorAll(".cell.active").forEach((el) => el.classList.remove("active"));
  const target = boardWrap.querySelector(`.cell[data-cell-id="${cellId}"]`);
  if (target) {
    target.classList.add("active");
    state.activeCellId = cellId;
  }
}

function bindCellEvents(cellEl, row, col, rowLabel, colLabel) {
  const cellId = getCellId(row, col);
  cellEl.dataset.cellId = cellId;

  const rowDisplay = formatRuleLabel(rowLabel);
  const colDisplay = formatRuleLabel(colLabel);

  // Two-line rule label displayed at the bottom of every cell
  const comboQual = cellEl.querySelector(".qual-combo");
  if (comboQual) {
    comboQual.innerHTML =
      `<span class="q-row">${rowDisplay}</span>` +
      `<span class="q-sep">×</span>` +
      `<span class="q-col">${colDisplay}</span>`;
    // Two long labels can't both fit at full size; drop a size for those.
    comboQual.classList.toggle("q-long", (rowDisplay + colDisplay).length > 30);
  }

  setCellSprites(cellEl, PLACEHOLDER, false);

  cellEl.addEventListener("click", () => {
    if (state.gameOver) return;
    const cell = state.cells.get(cellId);
    if (cell && cell.locked) return;
    setActiveCell(cellId);
    openCellModal(cellId, row, col, rowLabel, colLabel);
  });

  cellEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      if (state.gameOver) return;
      const cell = state.cells.get(cellId);
      if (cell && cell.locked) return;
      openCellModal(cellId, row, col, rowLabel, colLabel);
    }
  });
}

// ── Cell modal ────────────────────────────────────────────
let _modalCtx = null;     // { cellId, row, col, rowLabel, colLabel }
let _modalPicked = null;  // resolved pokemon { key, display_name, pokemon_id }
let _modalReqId = 0;

function openCellModal(cellId, row, col, rowLabel, colLabel) {
  _modalCtx = { cellId, row, col, rowLabel, colLabel };
  _modalPicked = null;

  document.getElementById("modalRowRule").textContent = formatRuleLabel(rowLabel);
  document.getElementById("modalColRule").textContent = formatRuleLabel(colLabel);

  // Per-tag explainer captions, styled to sit directly under each rule tag.
  const help = window.PDD_CATEGORY_HELP;
  const rowHelpEl = document.getElementById("modalRowHelp");
  const colHelpEl = document.getElementById("modalColHelp");
  if (help) {
    if (rowHelpEl) rowHelpEl.textContent = help.explain(formatRuleLabel(rowLabel)) || "";
    if (colHelpEl) colHelpEl.textContent = help.explain(formatRuleLabel(colLabel)) || "";
  }

  const input = document.getElementById("modalInput");
  input.value = state.cells.get(cellId)?.typed || "";

  setModalStatus("", "");
  document.getElementById("modalSuggestions").innerHTML = "";

  const sprite = document.getElementById("modalSprite");
  sprite.classList.add("hidden");
  sprite.src = PLACEHOLDER;
  document.getElementById("modalPickedName").textContent = "";
  document.getElementById("modalOk").disabled = false;

  document.getElementById("cellModal").classList.add("open");
  syncModalHints();
  syncModalLives();
  setTimeout(() => input.focus(), 60);
}

function closeCellModal() {
  // Capture context before clearing so we can update the cell draft state
  const ctx = _modalCtx;

  document.getElementById("cellModal").classList.remove("open");
  _modalCtx = null;
  _modalPicked = null;
  _modalReqId++;
  document.getElementById("modalSuggestions").innerHTML = "";
  boardWrap.querySelectorAll(".cell.active").forEach((el) => el.classList.remove("active"));
  state.activeCellId = null;

  // Reflect any typed-but-not-confirmed text on the cell
  if (ctx) updateCellDraftState(ctx.cellId);
}

// Show a faint italic name + amber border when user has typed something
// but not yet confirmed it via OK.
function updateCellDraftState(cellId) {
  const cell = state.cells.get(cellId);
  if (!cell || cell.locked) return;

  const cellEl = boardWrap.querySelector(`.cell[data-cell-id="${cellId}"]`);
  if (!cellEl) return;

  if (cell.wrongAttempt) {
    // Keep the cell red — a wrong answer was submitted here
    setPickedName(cellEl, cell.typed || "Wrong answer");
    cellEl.classList.add("err");
    cellEl.classList.remove("drafted");
  } else if (cell.typed && cell.typed.trim().length > 0) {
    setPickedName(cellEl, cell.typed.trim());
    cellEl.classList.add("drafted");
    cellEl.classList.remove("err");
  } else {
    setPickedName(cellEl, "Tap to enter");
    cellEl.classList.remove("drafted", "err");
  }
}

function setModalStatus(text, tone) {
  const el = document.getElementById("modalStatus");
  if (!el) return;
  el.textContent = text;
  el.className = `modal-status${tone ? " " + tone : ""}`;
}

// Sync the modal hint stars with the HUD star count
function syncModalHints() {
  document.querySelectorAll(".modal-pip-hint").forEach((pip, i) => {
    const available = i < state.hints;
    pip.classList.toggle("spent", !available);
    pip.disabled = !available || state.gameOver;
  });
}

// Sync the mini lives hearts inside the modal header
function syncModalLives() {
  for (let i = 0; i < 3; i++) {
    const heart = document.getElementById(`mh${i}`);
    if (!heart) continue;
    const alive = i < state.lives;
    heart.textContent = alive ? "❤️" : "🖤";
    heart.classList.toggle("spent", !alive);
  }
}

// Use a hint from inside the modal — resolves for the currently open cell
async function useHintFromModal() {
  if (!_modalCtx || state.gameOver || state.hints <= 0) return;
  const { cellId, row, col } = _modalCtx;
  const cell = state.cells.get(cellId);
  if (!cell || cell.locked) return;

  const usedKeys = getUsedKeysExcept(cellId);
  setModalStatus("Finding a hint…", "");
  document.querySelectorAll(".modal-pip-hint").forEach((b) => { b.disabled = true; });

  if (state.runtimeMode === "mock") {
    // Mock fallback
    setModalStatus("Hints not available in mock mode.", "warn");
    syncModalHints();
    return;
  }

  try {
    const params = new URLSearchParams({
      seed: state.board.seed,
      n_rows: String(state.board.n_rows),
      n_cols: String(state.board.n_cols),
      row_i: String(state.board?.rowPerm ? state.board.rowPerm[row] : row),
      col_j: String(state.board?.colPerm ? state.board.colPerm[col] : col),
      used_keys: usedKeys.join(","),
    });
    const result = await fetchJson(`${state.apiBase}/hint?${params.toString()}`);
    applyHintToCell(cellId, cell, result);
    syncDailyHints(result);
    closeCellModal();
  } catch (err) {
    setModalStatus(err?.message?.startsWith("Request failed") || !err?.message
      ? "Couldn't find a hint — try another cell!" : err.message, "warn");
    syncModalHints();
  }
}

function paintCellStatus(cellEl, statusText, tone = "warn") {
  // Cells no longer have inline status text — state is shown via CSS classes
  cellEl.classList.remove("ok", "err", "warn");
  if (tone) cellEl.classList.add(tone);
}

function createBoardGrid(board) {
  boardWrap.innerHTML = "";
  const grid = document.createElement("div");
  grid.className = "board-grid";
  grid.setAttribute("role", "grid");
  // Drives grid-template-columns in styles.css — without this, anything
  // other than a 9-wide board renders with the wrong number of columns
  // (headers and data cells wrap into the wrong shape).
  grid.style.setProperty("--board-cols", String(board.n_cols));

  const corner = document.createElement("div");
  corner.className = "rule-head corner";
  const cornerBadge = document.createElement("span");
  cornerBadge.className = "rule-badge text";
  cornerBadge.textContent = "Rules";
  corner.appendChild(cornerBadge);
  grid.appendChild(corner);

  board.col_rule_labels.forEach((label, j) => {
    const head = document.createElement("div");
    head.className = "rule-head";
    head.appendChild(ruleHeadContent(label, board.col_rule_ids && board.col_rule_ids[j]));
    grid.appendChild(head);
  });

  for (let i = 0; i < board.n_rows; i += 1) {
    const rowHead = document.createElement("div");
    rowHead.className = "row-head";
    rowHead.appendChild(ruleHeadContent(board.row_rule_labels[i], board.row_rule_ids && board.row_rule_ids[i]));
    grid.appendChild(rowHead);

    for (let j = 0; j < board.n_cols; j += 1) {
      const node = cellTemplate.content.firstElementChild.cloneNode(true);
      node.style.setProperty("--cell-index", String(i * board.n_cols + j));
      const id = getCellId(i, j);
      state.cells.set(id, {
        locked: false,
        key: "",
        displayName: "",
        pokemonId: null,
        typed: "",
      });
      bindCellEvents(node, i, j, board.row_rule_labels[i], board.col_rule_labels[j]);
      grid.appendChild(node);
    }
  }

  boardWrap.appendChild(grid);
}

// Shared by loadBoard's branches and Draft Board — everything needed to make
// a fetched/generated board object live in the actual game view.
function applyLoadedBoard(board, runtimeMode, message, tone = "var(--ok)") {
  state.runtimeMode = runtimeMode;
  state.board = board;
  shuffleBoardDisplay(state.board); // randomise display order; rowPerm/colPerm stored for validate/hint
  resetGameState();
  createBoardGrid(board);
  boardMeta.textContent = `Seed: ${board.seed} | Board: ${board.n_rows}x${board.n_cols}`;
  setMessage(message, tone);
  updateHUD();
}

async function loadBoard(seed = "") {
  // Empty string = relative URLs, which work correctly when FastAPI serves the frontend.
  // Never override with a hardcoded localhost — that breaks remote testers.
  state.apiBase = apiBaseInput?.value?.trim() ?? state.apiBase ?? "";
  state.mode = modeSelect?.value || state.mode || "auto";
  const query = new URLSearchParams({ n: "9" });
  if (seed) {
    query.set("seed", seed);
  }

  setMessage("Loading board...");
  if (state.mode === "mock") {
    const board = buildMockBoard(seed || String(Date.now()), 9);
    applyLoadedBoard(board, "mock", "Mock board loaded");
    return;
  }

  try {
    const board = await fetchJson(`${state.apiBase}/board?${query.toString()}`);
    applyLoadedBoard(board, "api", "Board loaded");
    if (board.is_daily && state.board === board) await restoreDaily(board);
  } catch (err) {
    if (state.mode === "auto") {
      const board = buildMockBoard(seed || String(Date.now()), 9);
      applyLoadedBoard(board, "mock", "API unavailable; running mock mode", "var(--warn)");
      return;
    }
    setMessage(`Unable to load board: ${err.message || err}`, "var(--err)");
  }
}

// ── Daily Board: saved progress ─────────────────────────────────────────────
// Today's answers, lives and hints live on the server, so the Daily comes
// back exactly as you left it after a refresh (and lives can't be reset).
async function restoreDaily(board) {
  let saved;
  try {
    saved = await fetchJson(`${state.apiBase}/daily/state`);
  } catch (err) {
    console.warn("[daily] couldn't restore progress", err);
    return;
  }
  // The player may have switched boards while this was loading.
  if (state.board !== board || saved.seed !== board.seed) return;

  (saved.picks || []).forEach((p) => {
    const i = board.rowPerm ? board.rowPerm.indexOf(p.row_i) : p.row_i;
    const j = board.colPerm ? board.colPerm.indexOf(p.col_j) : p.col_j;
    const cellId = getCellId(i, j);
    const cell = state.cells.get(cellId);
    if (!cell) return;
    Object.assign(cell, {
      locked: true, key: p.key, displayName: p.display_name, pokemonId: p.pokemon_id,
      isHint: p.hinted, shiny: p.shiny, pct: p.pct, wrongAttempt: false, typed: "",
    });
    const cellEl = boardWrap.querySelector(`.cell[data-cell-id="${cellId}"]`);
    if (!cellEl) return;
    const shiny = p.shiny && window.PDD_SHINY;
    setCellSprites(cellEl, shiny ? window.PDD_SHINY.spriteUrl(p.pokemon_id) : spriteFromPokemonId(p.pokemon_id), true);
    setPickedName(cellEl, p.display_name);
    setResultMark(cellEl, "ok");
    cellEl.classList.add("locked", "ok");
    cellEl.classList.toggle("hint-cell", !!p.hinted);
    cellEl.classList.toggle("shiny", !!p.shiny);
    if (!p.hinted) window.PDD_STATS?.paintPct(cellEl, p.pct);
  });

  state.lives = saved.lives_left;
  state.hints = saved.hints_left;
  const filled = (saved.picks || []).length;
  const total = board.n_rows * board.n_cols;
  if (saved.finished && saved.lives_left > 0 && filled < total) {
    // Ended early to see the stats: locked, but no win/lose banner.
    state.gameOver = true;
    boardWrap.classList.add("board-locked");
    board.__finished = true;
  }
  updateHUD();                     // shows the win / out-of-lives banner when due
  if (saved.finished) window.PDD_STATS?.boardEnded();
  else if (filled) setMessage(`Welcome back — ${filled} of ${total} squares filled today.`, "var(--ok)");
}

function randomSeed() {
  return String(Math.floor(Math.random() * 1_000_000_000));
}

// ── Pokédex ──────────────────────────────────────────────────────────────
// Lives in pokedex.js now (it was the "Palette" section here). It uses the
// globals above: state, fetchJson, TYPE_CONFIG, TYPE_SET, spriteFromPokemonId,
// setSpriteSafe, PLACEHOLDER and normalizeName.


loadRandomBtn?.addEventListener("click", () => loadBoard(randomSeed()));
apiBaseInput?.addEventListener("change", () => {
  state.apiBase = apiBaseInput.value.trim() || state.apiBase;
});
modeSelect?.addEventListener("change", () => {
  state.mode = modeSelect.value;
});

// HUD hint stars: if a cell is already active, open its modal; otherwise nudge the player
document.querySelectorAll(".pip-hint").forEach((pip) => {
  pip.addEventListener("click", () => {
    if (state.activeCellId) {
      const cell = state.cells.get(state.activeCellId);
      if (cell && !cell.locked && !state.gameOver) {
        // Reopen the modal for the active cell — modal hint bar handles the rest
        const cellEl = boardWrap.querySelector(`.cell[data-cell-id="${state.activeCellId}"]`);
        if (cellEl) cellEl.click();
        return;
      }
    }
    setMessage("Tap a cell first, then use a hint ⭐ inside the popup.", "var(--warn)");
  });
});

// Retry / new board after game over or win
document.querySelectorAll(".modal-pip-hint").forEach((pip) => {
  pip.addEventListener("click", () => useHintFromModal());
});

document.getElementById("retryBtn")?.addEventListener("click", () => loadBoard(randomSeed()));
document.getElementById("newAfterWinBtn")?.addEventListener("click", () => loadBoard(randomSeed()));

// ── Modal event listeners ─────────────────────────────────
const modalInput = document.getElementById("modalInput");

modalInput?.addEventListener("input", async () => {
  if (!_modalCtx) return;
  const typed = modalInput.value.trim();
  _modalPicked = null;

  // Save typed text in cell state + clear wrongAttempt if user is trying a new name
  const cell = state.cells.get(_modalCtx.cellId);
  if (cell) {
    const prevTyped = cell.typed || "";
    cell.typed = typed;
    // If they're typing something different from their wrong attempt, forgive the flag
    if (cell.wrongAttempt && typed.toLowerCase() !== prevTyped.toLowerCase()) {
      cell.wrongAttempt = false;
      const cellEl = boardWrap.querySelector(`.cell[data-cell-id="${_modalCtx.cellId}"]`);
      if (cellEl) {
        cellEl.classList.remove("err");
        if (typed.length > 0) cellEl.classList.add("drafted");
      }
    }
    state.cells.set(_modalCtx.cellId, cell);
  }

  const suggBox = document.getElementById("modalSuggestions");
  const sprite  = document.getElementById("modalSprite");
  const nameEl  = document.getElementById("modalPickedName");

  suggBox.innerHTML = "";
  if (typed.length < 2) {
    sprite.classList.add("hidden");
    nameEl.textContent = "";
    return;
  }

  // Suggestions list
  try {
    const data = await suggestPokemon(typed);
    suggBox.innerHTML = "";
    data.slice(0, 6).forEach((p) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "modal-suggestion-btn";
      const img = document.createElement("img");
      img.src = spriteFromPokemonId(p.pokemon_id);
      img.className = "modal-sug-sprite";
      img.loading = "lazy";
      img.onerror = () => { img.src = PLACEHOLDER; };
      btn.appendChild(img);
      const span = document.createElement("span");
      span.textContent = p.display_name;
      btn.appendChild(span);
      btn.addEventListener("click", () => {
        _modalPicked = p;
        modalInput.value = p.display_name;
        suggBox.innerHTML = "";
        setSpriteSafe(sprite, spriteFromPokemonId(p.pokemon_id));
        sprite.classList.remove("hidden");
        nameEl.textContent = p.display_name;
        setModalStatus("", "");
      });
      suggBox.appendChild(btn);
    });
  } catch { suggBox.innerHTML = ""; }

  // Live sprite preview
  const reqId = ++_modalReqId;
  try {
    const resolved = await resolvePokemon(typed);
    if (reqId !== _modalReqId) return;
    _modalPicked = resolved;
    setSpriteSafe(sprite, spriteFromPokemonId(resolved.pokemon_id));
    sprite.classList.remove("hidden");
    nameEl.textContent = resolved.display_name;
    setModalStatus("", "");
  } catch {
    if (reqId !== _modalReqId) return;
    sprite.classList.add("hidden");
    nameEl.textContent = "";
  }
});

modalInput?.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    document.getElementById("modalOk")?.click();
  }
});

document.getElementById("modalOk")?.addEventListener("click", async () => {
  if (!_modalCtx) return;
  if (!_modalPicked) { setModalStatus("Type and select a Pokémon first.", "warn"); return; }

  const { cellId, row, col } = _modalCtx;
  const okBtn = document.getElementById("modalOk");
  okBtn.disabled = true;
  setModalStatus("Validating…", "");

  const payload = {
    seed: state.board.seed,
    n_rows: state.board.n_rows,
    n_cols: state.board.n_cols,
    row_i: row,
    col_j: col,
    pokemon_key: _modalPicked.key,
    used_keys: getUsedKeysExcept(cellId),
  };

  try {
    const verdict = await validatePokemon(payload);
    const cellEl = boardWrap.querySelector(`.cell[data-cell-id="${cellId}"]`);

    if (verdict.ok) {
      const cell = state.cells.get(cellId);
      cell.locked = true;
      cell.key = _modalPicked.key;
      cell.displayName = _modalPicked.display_name;
      cell.pokemonId = _modalPicked.pokemon_id;
      cell.wrongAttempt = false;
      cell.typed = "";
      // Shiny is rolled on the server (shiny.js has the details).
      cell.shiny = !!verdict.shiny;
      cell.pct = verdict.pct ?? null;
      state.cells.set(cellId, cell);

      const shinySprite = cell.shiny && window.PDD_SHINY;
      if (cellEl) {
        setCellSprites(cellEl, shinySprite ? window.PDD_SHINY.spriteUrl(_modalPicked.pokemon_id) : spriteFromPokemonId(_modalPicked.pokemon_id), true);
        setPickedName(cellEl, _modalPicked.display_name);
        setResultMark(cellEl, "ok");
        cellEl.classList.add("locked", "ok");
        cellEl.classList.remove("err", "warn", "drafted");
        // Daily only: how many players picked the same Pokémon here.
        window.PDD_STATS?.paintPct(cellEl, cell.pct);
      }
      if (typeof verdict.lives_left === "number") state.lives = verdict.lives_left;
      setMessage(cell.shiny ? `✨ Shiny ${_modalPicked.display_name}!` : "Pick accepted ✓", "var(--ok)");
      // Capture the pick BEFORE closing: closeCellModal() clears _modalPicked.
      const found = shinySprite
        ? { cellEl, name: _modalPicked.display_name, pokemonId: _modalPicked.pokemon_id, registered: !!verdict.shiny_registered }
        : null;
      updateHUD();
      closeCellModal();
      if (found) {
        // Let the modal finish closing so the cell is visible for the burst.
        setTimeout(() => window.PDD_SHINY.celebrate(found), 260);
      }
    } else if (verdict.reason === "board_locked" || verdict.reason === "already_filled") {
      // The saved Daily disagrees with this page (another tab, or the board
      // already ended): reload it from the server.
      closeCellModal();
      setMessage(verdict.reason === "board_locked"
        ? "Today's board is already finished." : "That square was already filled — syncing.", "var(--warn)");
      loadBoard();
    } else {
      const reasonMap = {
        duplicate:       "Already used elsewhere — costs a life",
        does_not_match:  "Doesn't match the rules — costs a life",
        unknown_pokemon: "Unknown Pokémon — check the spelling",
      };
      setModalStatus(reasonMap[verdict.reason] || verdict.reason, "err");
      if (cellEl) {
        setResultMark(cellEl, "err");
        cellEl.classList.add("err");
        cellEl.classList.remove("ok", "drafted");
      }
      if (verdict.reason === "does_not_match" || verdict.reason === "duplicate") {
        // Flag the cell so it stays red when modal is closed
        const cell = state.cells.get(cellId);
        if (cell) {
          cell.wrongAttempt = true;
          cell.typed = _modalPicked.display_name;
          state.cells.set(cellId, cell);
        }
        state.lives = typeof verdict.lives_left === "number"
          ? verdict.lives_left : Math.max(0, state.lives - 1);
        updateHUD();
        if (state.lives <= 0) setTimeout(closeCellModal, 800);
      }
      okBtn.disabled = false;
    }
  } catch {
    setModalStatus("API error — could not validate.", "err");
    okBtn.disabled = false;
  }
});

document.getElementById("modalCancel")?.addEventListener("click", closeCellModal);
document.getElementById("modalClose")?.addEventListener("click", closeCellModal);
document.getElementById("cellModal")?.addEventListener("click", (e) => {
  if (e.target === document.getElementById("cellModal")) closeCellModal();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && document.getElementById("cellModal")?.classList.contains("open")) {
    closeCellModal();
  }
});

loadBoard();

window.PDD = {
  state,
  fetchJson,
  formatRuleLabel,
  ruleHeadContent,
  spriteFromPokemonId,
  setSpriteSafe,
};
//test commit