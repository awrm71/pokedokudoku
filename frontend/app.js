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
}

function triggerWin(correct, total) {
  state.gameOver = true;
  const banner = document.getElementById("winBanner");
  if (banner) banner.classList.add("visible");
  setMessage(`🏆 Board complete! ${correct}/${total} correct!`, "var(--ok)");
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
  } catch {
    setMessage("Couldn't find a hint for this cell — try another!", "var(--warn)");
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
    closeCellModal();
  } catch {
    setModalStatus("Couldn't find a hint — try another cell!", "warn");
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
  } catch (err) {
    if (state.mode === "auto") {
      const board = buildMockBoard(seed || String(Date.now()), 9);
      applyLoadedBoard(board, "mock", "API unavailable; running mock mode", "var(--warn)");
      return;
    }
    setMessage(`Unable to load board: ${err.message || err}`, "var(--err)");
  }
}

function randomSeed() {
  return String(Math.floor(Math.random() * 1_000_000_000));
}

// ── Draft Board ───────────────────────────────────────────────────────────
// Build-your-own board: pick which rule categories are allowed in, then
// generate a board using only those. Draft boards use a random (non-daily)
// seed, so — same as Shuffle Board — they never count toward the Pokedex;
// that's enforced server-side via is_daily_seed(), nothing extra needed here.
const draftModal            = document.getElementById("draftModal");
const draftAllowSingle       = document.getElementById("draftAllowSingle");
const draftGuaranteedSolvable = document.getElementById("draftGuaranteedSolvable");
const draftRowsSelect       = document.getElementById("draftRowsSelect");
const draftColsSelect       = document.getElementById("draftColsSelect");
const draftCategoryRow      = document.getElementById("draftCategoryRow");
const draftDropdownPanel    = document.getElementById("draftDropdownPanel");
const draftDropdownTitle    = document.getElementById("draftDropdownTitle");
const draftDropdownAllToggle = document.getElementById("draftDropdownAllToggle");
const draftDropdownSearch   = document.getElementById("draftDropdownSearch");
const draftDropdownList     = document.getElementById("draftDropdownList");
const draftSummary          = document.getElementById("draftSummary");
const draftInfoBanner       = document.getElementById("draftInfoBanner");
const draftErrorBanner      = document.getElementById("draftErrorBanner");
const draftGenerateBtn      = document.getElementById("draftGenerateBtn");

const DRAFT_GROUP_LABELS = {
  type: "Types", region: "Regions", evolution: "Evolution",
  move: "Moves", ability: "Abilities", other: "Other",
};
// Only the largest lists actually need a search box to be useful.
const DRAFT_SEARCHABLE_GROUPS = new Set(["move", "ability"]);

const draftState = {
  catalog: null,     // { groups: { type: [{rule_id,label}], ... }, total_rules }
  selected: {},       // { type: Set(rule_id), region: Set(...), ... } — starts as "everything on"
  openGroup: null,    // which category's checklist is currently expanded
  rows: 9,
  cols: 9,
};

async function openDraftModal() {
  draftModal.classList.add("open");
  if (!draftState.catalog) {
    await loadDraftCatalog();
  }
}

function closeDraftModal() {
  draftModal.classList.remove("open");
  draftDropdownPanel.classList.add("hidden");
  draftState.openGroup = null;
  draftCategoryRow.querySelectorAll(".draft-category-btn.open").forEach((b) => b.classList.remove("open"));
}

async function loadDraftCatalog() {
  draftSummary.textContent = "Loading categories…";
  draftGenerateBtn.disabled = true;
  try {
    const data = await fetchJson(`${state.apiBase}/rules/catalog`);
    draftState.catalog = data;
    // Default: everything enabled, same convention as PokeDoku's Unlimited
    // Mode — narrowing down is an opt-out action, not required to start.
    Object.keys(data.groups).forEach((g) => {
      draftState.selected[g] = new Set(data.groups[g].map((e) => e.rule_id));
    });
    updateDraftCategoryCounts();
    updateDraftSummary();
    draftGenerateBtn.disabled = false;
  } catch (err) {
    draftSummary.textContent = "Couldn't load categories — make sure the API is running.";
  }
}

function updateDraftCategoryCounts() {
  if (!draftState.catalog) return;
  Object.keys(DRAFT_GROUP_LABELS).forEach((g) => {
    const el = document.getElementById(`draftCount-${g}`);
    if (!el) return;
    const total = draftState.catalog.groups[g]?.length || 0;
    const sel = draftState.selected[g]?.size || 0;
    el.textContent = `(${sel}/${total})`;
  });
}

function draftTotalSelected() {
  return Object.values(draftState.selected).reduce((sum, s) => sum + s.size, 0);
}

function updateDraftSummary() {
  const total = draftTotalSelected();
  draftSummary.textContent = total === 0
    ? "Nothing selected yet — pick at least a few categories to draft a board."
    : `${total} categories selected — the more you enable, the more likely a full board can be built.`;
}

function openDraftGroup(group) {
  draftState.openGroup = group;
  draftCategoryRow.querySelectorAll(".draft-category-btn").forEach((b) => {
    b.classList.toggle("open", b.dataset.group === group);
  });
  draftDropdownPanel.classList.remove("hidden");
  draftDropdownTitle.textContent = DRAFT_GROUP_LABELS[group] || group;
  draftDropdownSearch.classList.toggle("hidden", !DRAFT_SEARCHABLE_GROUPS.has(group));
  draftDropdownSearch.value = "";
  renderDraftDropdownList();
  syncDraftAllToggle();
}

function renderDraftDropdownList() {
  const group = draftState.openGroup;
  if (!group || !draftState.catalog) return;
  const entries = draftState.catalog.groups[group] || [];
  const query = normalizeName(draftDropdownSearch.value || "");
  const filtered = query ? entries.filter((e) => normalizeName(e.label).includes(query)) : entries;

  if (filtered.length === 0) {
    draftDropdownList.innerHTML = `<div class="draft-dropdown-empty">No matches.</div>`;
    return;
  }

  const selectedSet = draftState.selected[group];
  draftDropdownList.innerHTML = filtered.map((e) => {
    const checked = selectedSet.has(e.rule_id) ? "checked" : "";
    return `<label class="draft-dropdown-item">` +
      `<input type="checkbox" data-rule-id="${e.rule_id}" ${checked} />` +
      `<span>${e.label}</span>` +
    `</label>`;
  }).join("");
}

function syncDraftAllToggle() {
  const group = draftState.openGroup;
  if (!group || !draftState.catalog) return;
  const entries = draftState.catalog.groups[group] || [];
  const selectedSet = draftState.selected[group];
  draftDropdownAllToggle.checked = entries.length > 0 && entries.every((e) => selectedSet.has(e.rule_id));
}

document.getElementById("openDraftBtn")?.addEventListener("click", openDraftModal);
document.getElementById("draftClose")?.addEventListener("click", closeDraftModal);
draftModal?.addEventListener("click", (e) => {
  if (e.target === draftModal) closeDraftModal();
});

draftCategoryRow?.querySelectorAll(".draft-category-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    const group = btn.dataset.group;
    if (draftState.openGroup === group) {
      draftState.openGroup = null;
      draftDropdownPanel.classList.add("hidden");
      btn.classList.remove("open");
    } else {
      openDraftGroup(group);
    }
  });
});

draftDropdownList?.addEventListener("change", (e) => {
  const input = e.target.closest("input[data-rule-id]");
  if (!input) return;
  const group = draftState.openGroup;
  if (input.checked) draftState.selected[group].add(input.dataset.ruleId);
  else draftState.selected[group].delete(input.dataset.ruleId);
  updateDraftCategoryCounts();
  updateDraftSummary();
  syncDraftAllToggle();
});

draftDropdownAllToggle?.addEventListener("change", () => {
  const group = draftState.openGroup;
  if (!group || !draftState.catalog) return;
  const entries = draftState.catalog.groups[group] || [];
  if (draftDropdownAllToggle.checked) {
    entries.forEach((e) => draftState.selected[group].add(e.rule_id));
  } else {
    draftState.selected[group].clear();
  }
  renderDraftDropdownList();
  updateDraftCategoryCounts();
  updateDraftSummary();
});

draftDropdownSearch?.addEventListener("input", renderDraftDropdownList);

draftRowsSelect?.addEventListener("change", () => {
  draftState.rows = Number(draftRowsSelect.value);
});
draftColsSelect?.addEventListener("change", () => {
  draftState.cols = Number(draftColsSelect.value);
});

draftGenerateBtn?.addEventListener("click", generateDraftBoard);

async function generateDraftBoard() {
  const allowedRuleIds = Object.values(draftState.selected).flatMap((s) => Array.from(s));
  draftInfoBanner.classList.add("hidden");
  draftErrorBanner.classList.add("hidden");
  draftGenerateBtn.disabled = true;
  const originalLabel = draftGenerateBtn.textContent;
  draftGenerateBtn.textContent = "🃏 Drafting…";

  try {
    const result = await fetchJson(`${state.apiBase}/board/draft`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        allowed_rule_ids: allowedRuleIds,
        n_rows: draftState.rows,
        n_cols: draftState.cols,
        allow_single_answers: draftAllowSingle.checked,
        guaranteed_solvable: draftGuaranteedSolvable.checked,
      }),
    });

    const board = {
      seed: result.seed, n_rows: result.n_rows, n_cols: result.n_cols,
      row_rule_ids: result.row_rule_ids, col_rule_ids: result.col_rule_ids,
      row_rule_labels: result.row_rule_labels, col_rule_labels: result.col_rule_labels,
    };
    const message = result.note ? "Draft board loaded (size adjusted — see note)" : "Draft board loaded";
    applyLoadedBoard(board, "api", message);
    closeDraftModal();
    if (window.PDD_SETMODE) window.PDD_SETMODE("draft");

    if (result.note) {
      // Surface the size-adjustment explanation as a normal game message too,
      // since the Draft modal (where the banner lives) is now closed.
      setMessage(result.note, "var(--warn)");
    }
  } catch (err) {
    // Log the full error (with stack trace) to the console — the banner only
    // ever showed err.message, which threw away exactly the information
    // needed to pin down where a client-side crash actually happened.
    console.error("Draft Board generation failed:", err);
    draftErrorBanner.textContent = err.message || "That combination didn't work out — try adjusting your selections.";
    draftErrorBanner.classList.remove("hidden");
  } finally {
    draftGenerateBtn.disabled = false;
    draftGenerateBtn.textContent = originalLabel;
  }
}

// ── Pokedex Palette ──────────────────────────────────────────────────────
// A collection tracker scoped to Daily Board catches only (the backend
// enforces this — see is_daily_seed() in api_main.py — so nothing here needs
// to re-check it). Shows every playable Pokemon; uncaught ones render as a
// silhouette of their real sprite so the shape teases without spoiling colors.
const pokedexModal        = document.getElementById("pokedexModal");
const pokedexGrid         = document.getElementById("pokedexGrid");
const pokedexLogList      = document.getElementById("pokedexLogList");
const pokedexEmpty        = document.getElementById("pokedexEmpty");
const pokedexSearchInput  = document.getElementById("pokedexSearch");
const pokedexGenSelect    = document.getElementById("pokedexGenSelect");
const pokedexPaletteStrip = document.getElementById("pokedexPaletteStrip");
const pokedexStatusChips  = document.getElementById("pokedexStatusChips");
const pokedexViewToggle   = document.getElementById("pokedexViewToggle");
const pokedexCountText    = document.getElementById("pokedexCountText");
const pokedexSubtitle     = document.getElementById("pokedexSubtitle");
const pokedexRingFill     = document.getElementById("pokedexRingFill");
const pokedexRingPct      = document.getElementById("pokedexRingPct");
const pokedexStreak       = document.getElementById("pokedexStreak");
const pokedexStreakText   = document.getElementById("pokedexStreakText");
const pokedexMilestoneToast = document.getElementById("pokedexMilestoneToast");

const pokedexModalLayout   = document.getElementById("pokedexModalLayout");
const pokedexDetailPane    = document.getElementById("pokedexDetailPane");
const detailPaneBack       = document.getElementById("detailPaneBack");
const detailSprite         = document.getElementById("detailSprite");
const detailTitle          = document.getElementById("detailTitle");
const detailTypeRow        = document.getElementById("detailTypeRow");
const detailCatchRow       = document.getElementById("detailCatchRow");
const detailGeneralGrid     = document.getElementById("detailGeneralGrid");
const detailGeneralProgress = document.getElementById("detailGeneralProgress");
const detailAbilitiesSection = document.getElementById("detailAbilitiesSection");
const detailAbilitiesGrid   = document.getElementById("detailAbilitiesGrid");
const detailAbilitiesProgress = document.getElementById("detailAbilitiesProgress");
const detailMovesSection   = document.getElementById("detailMovesSection");
const detailMovesGrid      = document.getElementById("detailMovesGrid");
const detailMovesProgress  = document.getElementById("detailMovesProgress");

const RING_CIRCUMFERENCE = 2 * Math.PI * 52; // must match r=52 on the SVG circle

// Categories a Pokemon has confirmed (or could still confirm) arrive from the
// backend already as display-ready labels — e.g. "Base Stage", "Fire",
// "Legendary" — the same strings compact_rule_label() puts on the board
// itself, so no client-side relabeling is needed here. See the Detail modal
// functions further down for how these render as confirmed vs. locked chips.

// Caught counts worth celebrating. Checked once per pokedex load; the highest
// one already shown is remembered in localStorage so it never repeats.
const POKEDEX_MILESTONES = [1, 10, 25, 50, 100, 150, 200, 250, 300, 400, 500, 750, 1000];
const MILESTONE_SEEN_KEY = "pokedokudoku_pokedex_milestone_seen";

const pokedexState = {
  entries: [],
  loaded: false,
  view: "grid",        // "grid" | "log"
  statusFilter: "all",  // "all" | "caught" | "uncaught" — grid view only
  typeFilter: null,     // null or a TYPE_CONFIG key
  genFilter: "",        // "" or a gen number as a string
  search: "",
  selectedKey: null,    // whichever entry the detail pane is currently showing
};

function openPokedex() {
  pokedexModal.classList.add("open");
  loadPokedex();
}

function closePokedex() {
  pokedexModal.classList.remove("open");
  closePokedexDetail();
}

async function loadPokedex() {
  pokedexSubtitle.textContent = "Loading your collection…";
  pokedexMilestoneToast.classList.add("hidden");
  try {
    const data = await fetchJson(`${state.apiBase}/pokedex`);
    pokedexState.entries = data.entries || [];
    pokedexState.loaded = true;
    updatePokedexStats(data.caught_count, data.total_count);
    updatePokedexStreak();
    checkPokedexMilestone(data.caught_count, data.total_count);
    buildPaletteStrip();
    buildGenOptions();
    renderPokedexContent();
  } catch (err) {
    pokedexSubtitle.textContent = "Couldn't load your palette — make sure the API is running.";
  }
}

function updatePokedexStats(caught, total) {
  const pct = total > 0 ? Math.round((caught / total) * 100) : 0;
  pokedexCountText.textContent = `${caught} / ${total} caught`;
  pokedexRingPct.textContent = `${pct}%`;
  pokedexRingFill.style.strokeDasharray = `${RING_CIRCUMFERENCE}`;
  pokedexRingFill.style.strokeDashoffset = `${RING_CIRCUMFERENCE * (1 - pct / 100)}`;
  pokedexSubtitle.textContent = pct >= 100
    ? "Complete palette — every type, every form. Incredible work."
    : "Every correct pick on a Daily Board adds to your palette below.";
}

// Streak = consecutive Daily Boards (by board_seed, the puzzle's own date —
// not first_caught's timestamp, which could drift a little around midnight)
// on which at least one new Pokemon was caught, counting back from today.
function computeStreak(entries) {
  const days = new Set(entries.filter((e) => e.caught && e.board_seed).map((e) => e.board_seed));
  if (days.size === 0) return 0;

  const fmt = (d) => {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    return `${y}-${m}-${day}`;
  };

  const cursor = new Date();
  if (!days.has(fmt(cursor))) cursor.setDate(cursor.getDate() - 1); // today not played yet — start from yesterday

  let streak = 0;
  while (days.has(fmt(cursor))) {
    streak += 1;
    cursor.setDate(cursor.getDate() - 1);
  }
  return streak;
}

function updatePokedexStreak() {
  const streak = computeStreak(pokedexState.entries);
  if (streak < 2) {
    pokedexStreak.classList.add("hidden");
    return;
  }
  pokedexStreak.classList.remove("hidden");
  pokedexStreakText.textContent = `${streak} day streak`;
}

function checkPokedexMilestone(caught, total) {
  const lastSeen = Number(localStorage.getItem(MILESTONE_SEEN_KEY) || "0");
  const isFullDex = total > 0 && caught === total;
  const nextMilestone = POKEDEX_MILESTONES.filter((m) => m <= caught && m > lastSeen).pop();

  if (isFullDex && lastSeen < total) {
    showMilestoneToast(`🎉 Full palette complete — all ${total} caught!`);
    localStorage.setItem(MILESTONE_SEEN_KEY, String(total));
  } else if (nextMilestone) {
    showMilestoneToast(`🎉 Milestone reached — ${nextMilestone} Pokémon caught!`);
    localStorage.setItem(MILESTONE_SEEN_KEY, String(nextMilestone));
  }
}

function showMilestoneToast(text) {
  pokedexMilestoneToast.textContent = text;
  pokedexMilestoneToast.classList.remove("hidden");
  setTimeout(() => pokedexMilestoneToast.classList.add("hidden"), 5000);
}

function buildPaletteStrip() {
  pokedexPaletteStrip.innerHTML = "";
  Object.entries(TYPE_CONFIG).forEach(([typeKey, cfg]) => {
    const inType = pokedexState.entries.filter((e) => e.type1 === typeKey || e.type2 === typeKey);
    const caughtInType = inType.filter((e) => e.caught).length;
    const totalInType = inType.length;
    const isFull = totalInType > 0 && caughtInType === totalInType;
    const fillPct = totalInType ? (caughtInType / totalInType) * 100 : 0;

    const swatch = document.createElement("button");
    swatch.type = "button";
    swatch.className = "palette-swatch"
      + (isFull ? " full" : "")
      + (pokedexState.typeFilter === typeKey ? " active" : "");
    swatch.style.setProperty("--swatch-color", cfg.bg);
    swatch.title = `${typeKey.charAt(0).toUpperCase()}${typeKey.slice(1)}: ${caughtInType}/${totalInType} caught`;
    swatch.innerHTML =
      `<span class="swatch-fill" style="height:${fillPct}%"></span>` +
      `<span class="swatch-icon">${cfg.icon}</span>`;
    swatch.addEventListener("click", () => {
      pokedexState.typeFilter = pokedexState.typeFilter === typeKey ? null : typeKey;
      buildPaletteStrip();
      renderPokedexContent();
    });
    pokedexPaletteStrip.appendChild(swatch);
  });
}

function buildGenOptions() {
  const gens = Array.from(new Set(pokedexState.entries.map((e) => e.gen).filter((g) => g != null))).sort((a, b) => a - b);
  const current = pokedexGenSelect.value;
  pokedexGenSelect.innerHTML =
    `<option value="">All Gens</option>` +
    gens.map((g) => `<option value="${g}">Gen ${g}</option>`).join("");
  pokedexGenSelect.value = current;
}

function matchesPokedexFilters(entry) {
  if (pokedexState.view === "grid") {
    if (pokedexState.statusFilter === "caught" && !entry.caught) return false;
    if (pokedexState.statusFilter === "uncaught" && entry.caught) return false;
  } else {
    if (!entry.caught) return false; // the Log is a history of catches — nothing to log for uncaught entries
  }
  if (pokedexState.typeFilter && entry.type1 !== pokedexState.typeFilter && entry.type2 !== pokedexState.typeFilter) return false;
  if (pokedexState.genFilter && String(entry.gen) !== pokedexState.genFilter) return false;
  if (pokedexState.search && !normalizeName(entry.display_name).includes(normalizeName(pokedexState.search))) return false;
  return true;
}

function isRecentCatch(iso) {
  if (!iso) return false;
  return (Date.now() - new Date(iso).getTime()) < 48 * 60 * 60 * 1000; // 48h glow window
}

function ruleChipsHtml(entry) {
  if (!entry.row_rule_label && !entry.col_rule_label) return "";
  const row = entry.row_rule_label || "?";
  const col = entry.col_rule_label || "?";
  return `<div class="dex-rule-chips">` +
    `<span class="dex-rule-chip">${row}</span>` +
    `<span class="dex-rule-x">×</span>` +
    `<span class="dex-rule-chip">${col}</span>` +
  `</div>`;
}

// Cards and log rows are deliberately fixed-length now (sprite/name/types/date
// only) — nothing here grows with how many ways a Pokemon has been used.
// Anything variable-length (the rule pairing that caught it, and the full
// confirmed-vs-locked category checklist) lives in the Detail modal instead,
// opened by clicking any caught entry. See openPokedexDetail() below.

function buildPokedexCard(entry, index) {
  const card = document.createElement("div");
  card.className = "dex-card " + (entry.caught ? "caught" : "uncaught") + (entry.is_shiny ? " shiny" : "")
    + (entry.key === pokedexState.selectedKey ? " selected" : "");
  card.dataset.pokemonKey = entry.key;
  card.style.setProperty("--card-index", index % 60);
  card.setAttribute("role", "listitem");
  if (entry.caught && isRecentCatch(entry.first_caught)) card.classList.add("recent");

  const img = document.createElement("img");
  img.className = "dex-sprite";
  img.loading = "lazy";
  img.alt = entry.caught ? entry.display_name : "Not yet caught";
  setSpriteSafe(img, entry.pokemon_id ? spriteFromPokemonId(entry.pokemon_id) : PLACEHOLDER);
  card.appendChild(img);

  const nameEl = document.createElement("p");
  nameEl.className = "dex-name";
  nameEl.textContent = entry.caught ? entry.display_name : "???";
  if (entry.caught && (entry.is_legendary || entry.is_mythical)) {
    nameEl.textContent += " ";
    const rare = document.createElement("span");
    rare.className = "dex-rare-mark";
    rare.textContent = "✦";
    nameEl.appendChild(rare);
  }
  card.appendChild(nameEl);

  const typeRow = document.createElement("div");
  typeRow.className = "dex-type-row";
  [entry.type1, entry.type2].filter((t) => t && TYPE_SET.has(t)).forEach((t) => {
    const dot = document.createElement("span");
    dot.className = "dex-type-dot";
    dot.style.background = TYPE_CONFIG[t].bg;
    dot.title = t;
    typeRow.appendChild(dot);
  });
  card.appendChild(typeRow);

  if (entry.caught && entry.first_caught) {
    const dateEl = document.createElement("p");
    dateEl.className = "dex-date-caption";
    dateEl.textContent = new Date(entry.first_caught).toLocaleDateString(undefined, { month: "short", day: "numeric" });
    card.appendChild(dateEl);
    card.addEventListener("click", () => openPokedexDetail(entry.key));
  }

  return card;
}

function buildLogRow(entry, index) {
  const row = document.createElement("div");
  row.className = "log-row" + (entry.is_shiny ? " shiny" : "") + (isRecentCatch(entry.first_caught) ? " recent" : "")
    + (entry.key === pokedexState.selectedKey ? " selected" : "");
  row.dataset.pokemonKey = entry.key;
  row.style.setProperty("--card-index", index % 60);
  row.setAttribute("role", "listitem");
  row.addEventListener("click", () => openPokedexDetail(entry.key));

  const img = document.createElement("img");
  img.className = "log-sprite";
  img.loading = "lazy";
  img.alt = entry.display_name;
  setSpriteSafe(img, entry.pokemon_id ? spriteFromPokemonId(entry.pokemon_id) : PLACEHOLDER);
  row.appendChild(img);

  const main = document.createElement("div");
  main.className = "log-main";

  const nameRow = document.createElement("div");
  nameRow.className = "log-name-row";
  const nameEl = document.createElement("p");
  nameEl.className = "log-name";
  nameEl.textContent = entry.display_name + ((entry.is_legendary || entry.is_mythical) ? " ✦" : "");
  nameRow.appendChild(nameEl);
  [entry.type1, entry.type2].filter((t) => t && TYPE_SET.has(t)).forEach((t) => {
    const dot = document.createElement("span");
    dot.className = "dex-type-dot";
    dot.style.background = TYPE_CONFIG[t].bg;
    dot.title = t;
    nameRow.appendChild(dot);
  });
  main.appendChild(nameRow);

  const meta = document.createElement("div");
  meta.className = "log-meta";
  meta.innerHTML = ruleChipsHtml(entry);
  main.appendChild(meta);

  row.appendChild(main);

  const dateEl = document.createElement("span");
  dateEl.className = "log-date";
  const d = new Date(entry.first_caught);
  dateEl.textContent = d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
  row.appendChild(dateEl);

  return row;
}

function renderPokedexContent() {
  const isLog = pokedexState.view === "log";
  pokedexGrid.classList.toggle("hidden", isLog);
  pokedexLogList.classList.toggle("hidden", !isLog);
  pokedexStatusChips.classList.toggle("hidden", isLog);

  const filtered = pokedexState.entries.filter(matchesPokedexFilters);
  const sorted = isLog
    ? [...filtered].sort((a, b) => new Date(b.first_caught) - new Date(a.first_caught))
    : filtered;

  pokedexGrid.innerHTML = "";
  pokedexLogList.innerHTML = "";

  if (sorted.length === 0) {
    pokedexEmpty.classList.remove("hidden");
    if (isLog) {
      pokedexEmpty.textContent = pokedexState.entries.some((e) => e.caught)
        ? "No catches match these filters."
        : "No catches logged yet. Solve today's Daily Board to start your log.";
    } else {
      pokedexEmpty.textContent = pokedexState.entries.length && pokedexState.entries.every((e) => !e.caught)
        ? "Your palette is empty. Solve today's Daily Board to start filling it in — Shuffle Boards don't count."
        : "No Pokémon match these filters. Try clearing the search or picking a different type.";
    }
    return;
  }
  pokedexEmpty.classList.add("hidden");

  const frag = document.createDocumentFragment();
  sorted.forEach((entry, i) => frag.appendChild(isLog ? buildLogRow(entry, i) : buildPokedexCard(entry, i)));
  (isLog ? pokedexLogList : pokedexGrid).appendChild(frag);
}

document.getElementById("openPokedexBtn")?.addEventListener("click", openPokedex);
document.getElementById("pokedexClose")?.addEventListener("click", closePokedex);
pokedexModal?.addEventListener("click", (e) => {
  if (e.target === pokedexModal) closePokedex();
});
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (pokedexDetailPane?.classList.contains("open")) {
    closePokedexDetail(); // step back to the list first, same as the Back button
  } else if (pokedexModal?.classList.contains("open")) {
    closePokedex();
  }
});
pokedexSearchInput?.addEventListener("input", () => {
  pokedexState.search = pokedexSearchInput.value.trim();
  renderPokedexContent();
});
pokedexGenSelect?.addEventListener("change", () => {
  pokedexState.genFilter = pokedexGenSelect.value;
  renderPokedexContent();
});
pokedexStatusChips?.querySelectorAll(".pokedex-chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    pokedexStatusChips.querySelectorAll(".pokedex-chip").forEach((c) => c.classList.remove("active"));
    chip.classList.add("active");
    pokedexState.statusFilter = chip.dataset.filter;
    renderPokedexContent();
  });
});
pokedexViewToggle?.querySelectorAll(".pokedex-view-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    pokedexViewToggle.querySelectorAll(".pokedex-view-btn").forEach((b) => {
      b.classList.remove("active");
      b.setAttribute("aria-selected", "false");
    });
    btn.classList.add("active");
    btn.setAttribute("aria-selected", "true");
    pokedexState.view = btn.dataset.view;
    renderPokedexContent();
  });
});

// ── Pokemon Detail pane ──────────────────────────────────────────────────
// Everything variable-length about a caught Pokemon lives here instead of on
// the small cards/rows: the full possible-categories checklist (confirmed vs.
// locked), and real abilities. Gated to caught entries only — GET /pokedex/{key}
// 404s for anything not caught yet, preserving the silhouette mystery.
//
// Collapsed (zero width/opacity) until something is selected — there's no
// permanent "select a pokemon" placeholder taking up space. Opening it
// expands the split on desktop, or slides it in to fully cover the list on
// mobile; "← Back" collapses it again without touching the whole modal.
async function openPokedexDetail(key) {
  pokedexState.selectedKey = key;
  syncSelectedHighlight();

  pokedexModalLayout.classList.add("detail-open");
  pokedexDetailPane.classList.add("open");
  detailTitle.textContent = "Loading…";
  detailTypeRow.innerHTML = "";
  detailCatchRow.innerHTML = "";
  detailGeneralGrid.innerHTML = "";
  detailGeneralProgress.textContent = "";
  detailAbilitiesGrid.innerHTML = "";
  detailAbilitiesProgress.textContent = "";
  detailMovesGrid.innerHTML = "";
  detailMovesProgress.textContent = "";

  try {
    const data = await fetchJson(`${state.apiBase}/pokedex/${encodeURIComponent(key)}`);
    renderPokedexDetail(data);
  } catch (err) {
    detailTitle.textContent = "Couldn't load details";
    detailTypeRow.innerHTML = "";
  }
}

// "← Back" target, and also what closePokedex() calls on its way out — either
// way, collapses the detail pane back to nothing and clears the selection.
function closePokedexDetail() {
  pokedexState.selectedKey = null;
  syncSelectedHighlight();
  pokedexModalLayout.classList.remove("detail-open");
  pokedexDetailPane.classList.remove("open");
}

// Keeps the currently-open entry visibly highlighted in whichever list is on
// screen, without needing a full re-render — cheap enough to call on every
// selection change.
function syncSelectedHighlight() {
  document.querySelectorAll('[data-pokemon-key].selected').forEach((el) => el.classList.remove("selected"));
  if (!pokedexState.selectedKey) return;
  document.querySelectorAll(`[data-pokemon-key="${CSS.escape(pokedexState.selectedKey)}"]`)
    .forEach((el) => el.classList.add("selected"));
}

// Builds the chip markup for one category list. Every section (General,
// Abilities, Moves) uses this — they only differ in color (via the
// .section-general/.section-abilities/.section-moves ancestor class) and
// whether "informational" (non-lockable) entries can occur at all.
function categoryChipsHtml(categories) {
  return categories.map((cat) => {
    if (cat.lockable && cat.confirmed) {
      return `<span class="detail-category-chip confirmed">${cat.label}</span>`;
    }
    if (cat.lockable) {
      // Locked: real label still renders (so width matches the real answer's
      // length, no layout jump when later confirmed) but blurred past
      // legibility. aria-hidden + a plain sr-only label keeps this from
      // leaking the answer to screen readers either.
      return `<span class="detail-category-chip locked">` +
        `<span class="lock-icon" aria-hidden="true">🔒</span>` +
        `<span class="chip-label-blur" aria-hidden="true">${cat.label}</span>` +
        `<span class="sr-only">Locked category</span>` +
      `</span>`;
    }
    // Informational: no board rule exists for this trait at all (most moves;
    // some abilities), so there's nothing to "earn" — just show it plainly.
    return `<span class="detail-category-chip info">${cat.label}</span>`;
  }).join("");
}

function renderPokedexDetail(entry) {
  detailSprite.alt = entry.display_name;
  setSpriteSafe(detailSprite, entry.pokemon_id ? spriteFromPokemonId(entry.pokemon_id) : PLACEHOLDER);
  detailTitle.textContent = entry.display_name;

  detailTypeRow.innerHTML = "";
  [entry.type1, entry.type2].filter((t) => t && TYPE_SET.has(t)).forEach((t) => {
    const pill = document.createElement("span");
    pill.className = "detail-type-pill";
    pill.style.background = TYPE_CONFIG[t].bg;
    pill.textContent = t;
    detailTypeRow.appendChild(pill);
  });

  const dateStr = entry.first_caught
    ? new Date(entry.first_caught).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })
    : "";
  detailCatchRow.innerHTML =
    (dateStr ? `<span class="detail-catch-date">Caught ${dateStr}</span>` : "") +
    ruleChipsHtml(entry);

  // Confirmed/locked state is computed server-side now (in each category's
  // .confirmed flag) — no client-side string matching needed here at all.
  const general = entry.general_categories || [];
  detailGeneralGrid.innerHTML = categoryChipsHtml(general);
  detailGeneralProgress.textContent = `${general.filter((c) => c.confirmed).length} / ${general.length} confirmed`;

  const abilities = entry.ability_categories || [];
  detailAbilitiesGrid.innerHTML = categoryChipsHtml(abilities);
  const lockableAbilities = abilities.filter((c) => c.lockable);
  detailAbilitiesProgress.textContent = lockableAbilities.length
    ? `${lockableAbilities.filter((c) => c.confirmed).length} / ${lockableAbilities.length} confirmed`
    : "";

  const moves = entry.move_categories || [];
  detailMovesGrid.innerHTML = categoryChipsHtml(moves);
  const lockableMoves = moves.filter((c) => c.lockable);
  detailMovesProgress.textContent = lockableMoves.length
    ? `${lockableMoves.filter((c) => c.confirmed).length} / ${lockableMoves.length} confirmed`
    : "";
}

detailPaneBack?.addEventListener("click", closePokedexDetail);


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
      state.cells.set(cellId, cell);

      if (cellEl) {
        setCellSprites(cellEl, spriteFromPokemonId(_modalPicked.pokemon_id), true);
        setPickedName(cellEl, _modalPicked.display_name);
        setResultMark(cellEl, "ok");
        cellEl.classList.add("locked", "ok");
        cellEl.classList.remove("err", "warn", "drafted");
      }
      setMessage("Pick accepted ✓", "var(--ok)");
      updateHUD();
      closeCellModal();
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
        state.lives = Math.max(0, state.lives - 1);
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

// ── Mode-tab navigation ──────────────────────────────────────────────────────
// Three game modes (Daily, Draft, Reverse); exactly one is active. Each mode's
// controls live in its own context panel, so the active tab and its controls
// are always in sync — no more "Daily lit while Reverse is showing". Palette is
// NOT a mode; it opens from the corner over whatever mode is active.
(function initModeTabs() {
  const tabs = Array.from(document.querySelectorAll(".mode-tab"));
  const panels = Array.from(document.querySelectorAll(".ctx-panel"));
  if (!tabs.length) return;

  let current = null;

  function showPanel(mode) {
    panels.forEach((p) => p.classList.toggle("hidden", p.dataset.ctx !== mode));
  }
  function highlight(mode) {
    tabs.forEach((t) => t.classList.toggle("active", t.dataset.mode === mode));
  }

  function setMode(mode) {
    if (mode === current) {
      // Re-clicking Draft reopens the configurator; re-clicking others is a no-op.
      if (mode === "draft") openDraftModal();
      return;
    }
    current = mode;
    highlight(mode);
    showPanel(mode);

    const rev = window.PDD_REVERSE;
    if (mode === "reverse") {
      if (rev) rev.enter();
    } else {
      if (rev) rev.exit();
    }

    if (mode === "daily") {
      // Only auto-load a fresh daily if we're not already showing one.
      if (!state.board || state.runtimeMode === "mock") loadBoard();
    } else if (mode === "draft") {
      // Clicking the Draft tab lands you straight in the configurator.
      openDraftModal();
    }
  }

  tabs.forEach((t) => t.addEventListener("click", () => setMode(t.dataset.mode)));

  // When a Draft board is successfully generated, app.js already swaps the
  // board in; we just make sure the Draft tab stays lit.
  window.PDD_SETMODE = setMode;

  // Start on Daily (matches the initial loadBoard() above).
  current = "daily";
  highlight("daily");
  showPanel("daily");
})();