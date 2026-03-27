const CDN_BASE = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/other/official-artwork";
const PLACEHOLDER =
  "data:image/svg+xml;utf8," +
  encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96" viewBox="0 0 96 96"><rect width="96" height="96" rx="14" fill="#eef3ed"/><circle cx="48" cy="48" r="20" fill="#d5dfd6"/><text x="48" y="54" text-anchor="middle" font-family="Arial,sans-serif" font-size="24" fill="#466057">?</text></svg>'
  );

const state = {
  apiBase: "http://127.0.0.1:8000",
  mode: "auto",
  board: null,
  cells: new Map(),
  activeCellId: null,
};

const boardWrap = document.getElementById("boardWrap");
const boardMeta = document.getElementById("boardMeta");
const message = document.getElementById("message");
const apiBaseInput = document.getElementById("apiBase");
const modeSelect = document.getElementById("modeSelect");
const loadTodayBtn = document.getElementById("loadToday");
const loadRandomBtn = document.getElementById("loadRandom");
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
  if (state.mode === "mock") {
    const pfx = normalizeName(prefix);
    if (pfx.length < 2) return [];
    return MOCK_POKEMON.filter((p) => normalizeName(p.display_name).startsWith(pfx)).slice(0, 6);
  }
  return fetchJson(
    `${state.apiBase}/pokemon/suggest?prefix=${encodeURIComponent(prefix)}&limit=6`
  );
}

async function resolvePokemon(name) {
  if (state.mode === "mock") {
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
  if (state.mode === "mock") {
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
  return fetchJson(`${state.apiBase}/validate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

function setMessage(text, tone = "") {
  message.textContent = text;
  message.style.color = tone || "";
}

function getCellId(row, col) {
  return `${row}:${col}`;
}

function formatRuleLabel(label) {
  const lower = String(label || "").trim().toLowerCase();
  if (lower === "single-type") return "Single Type";
  if (lower === "dual-type") return "Dual Type";
  if (lower === "legendary") return "Legendary";
  if (lower === "myth") return "Myth";
  if (/^g\d+$/.test(lower)) return lower.toUpperCase();
  return lower ? lower.charAt(0).toUpperCase() + lower.slice(1) : "";
}

function spriteFromPokemonId(pokemonId) {
  return pokemonId ? `${CDN_BASE}/${pokemonId}.png` : PLACEHOLDER;
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

async function fetchJson(url, options = undefined) {
  const res = await fetch(url, options);
  if (!res.ok) {
    throw new Error(`Request failed (${res.status})`);
  }
  return res.json();
}

function clearBoardState() {
  state.cells.clear();
  state.activeCellId = null;
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
  const input = cellEl.querySelector(".name-input");
  const submit = cellEl.querySelector(".ok-btn");
  const clear = cellEl.querySelector(".clear-btn");
  const comboQual = cellEl.querySelector(".qual-combo");

  const datalist = document.createElement("datalist");
  const listId = `suggest-${row}-${col}`;
  datalist.id = listId;
  datalist.className = "suggestions";
  cellEl.appendChild(datalist);
  input.setAttribute("list", listId);

  const cellId = getCellId(row, col);
  let resolveRequestId = 0;
  cellEl.dataset.cellId = cellId;
  const rowDisplay = formatRuleLabel(rowLabel);
  const colDisplay = formatRuleLabel(colLabel);
  const comboLabel = `${rowDisplay} • ${colDisplay}`;
  cellEl.title = comboLabel;
  comboQual.textContent = `${rowDisplay}/${colDisplay}`;
  setCellSprites(cellEl, PLACEHOLDER, false);

  cellEl.addEventListener("click", () => {
    setActiveCell(cellId);
  });

  cellEl.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      setActiveCell(cellId);
      input.focus();
    }
  });

  input.addEventListener("input", async () => {
    const typed = input.value.trim();
    const cell = state.cells.get(cellId);
    if (!cell.locked) {
      cell.typed = typed;
      state.cells.set(cellId, cell);
      paintCellStatus(cellEl, "Not submitted", "warn");
    }

    if (typed.length < 2) {
      datalist.innerHTML = "";
      if (!cell.locked) {
        setCellSprites(cellEl, PLACEHOLDER, false);
        setPickedName(cellEl, typed ? typed : "Tap to enter pick");
      }
      return;
    }

    try {
      const data = await suggestPokemon(typed);
      datalist.innerHTML = data
        .map((p) => `<option value="${p.display_name}"></option>`)
        .join("");
    } catch {
      datalist.innerHTML = "";
    }

    // Live preview: show sprite once input resolves to a known Pokemon name.
    const currentRequest = ++resolveRequestId;
    try {
      const resolved = await resolvePokemon(typed);
      if (currentRequest !== resolveRequestId || cell.locked) {
        return;
      }
      setCellSprites(cellEl, spriteFromPokemonId(resolved.pokemon_id), true);
      setPickedName(cellEl, resolved.display_name);
      setResultMark(cellEl, "none");
    } catch {
      if (currentRequest !== resolveRequestId || cell.locked) {
        return;
      }
      setCellSprites(cellEl, PLACEHOLDER, false);
      setPickedName(cellEl, typed ? typed : "Tap to enter pick");
      setResultMark(cellEl, "none");
    }
  });

  submit.addEventListener("click", async () => {
    const typed = input.value.trim();
    if (!typed) {
      paintCellStatus(cellEl, "Enter a Pokemon name first", "err");
      return;
    }

    setMessage("Validating pick...");
    try {
      const resolved = await resolvePokemon(typed);

      const payload = {
        seed: state.board.seed,
        n: state.board.n,
        row_i: row,
        col_j: col,
        pokemon_key: resolved.key,
        used_keys: getUsedKeysExcept(cellId),
      };

      const verdict = await validatePokemon(payload);

      if (verdict.ok) {
        const cell = state.cells.get(cellId);
        cell.locked = true;
        cell.key = resolved.key;
        cell.displayName = resolved.display_name;
        cell.pokemonId = resolved.pokemon_id;
        state.cells.set(cellId, cell);

        input.value = resolved.display_name;
        input.disabled = true;
        setCellSprites(cellEl, spriteFromPokemonId(resolved.pokemon_id), true);
        setPickedName(cellEl, resolved.display_name);
        setResultMark(cellEl, "ok");
        paintCellStatus(cellEl, "Correct", "ok");
        cellEl.classList.add("locked");
        setMessage("Pick accepted", "var(--ok)");
      } else {
        setCellSprites(cellEl, spriteFromPokemonId(resolved.pokemon_id), true);
        setPickedName(cellEl, resolved.display_name);
        setResultMark(cellEl, "err");
        const reasonMap = {
          duplicate: "Already used elsewhere",
          does_not_match: "Does not match row/column",
          unknown_pokemon: "Unknown pokemon",
        };
        paintCellStatus(cellEl, reasonMap[verdict.reason] || verdict.reason, "err");
        setMessage("Validation failed", "var(--err)");
      }
    } catch (err) {
      setCellSprites(cellEl, PLACEHOLDER, false);
      setPickedName(cellEl, typed);
      setResultMark(cellEl, "err");
      paintCellStatus(cellEl, "Could not validate (API unavailable)", "err");
      setMessage(String(err.message || err), "var(--err)");
    }
  });

  clear.addEventListener("click", () => {
    const cell = state.cells.get(cellId);
    cell.locked = false;
    cell.key = "";
    cell.displayName = "";
    cell.pokemonId = null;
    cell.typed = "";
    state.cells.set(cellId, cell);

    input.disabled = false;
    input.value = "";
    setCellSprites(cellEl, PLACEHOLDER, false);
    setPickedName(cellEl, "Tap to enter pick");
    setResultMark(cellEl, "none");
    paintCellStatus(cellEl, "Not submitted", "warn");
    cellEl.classList.remove("locked");
  });
}

function createBoardGrid(board) {
  boardWrap.innerHTML = "";
  const grid = document.createElement("div");
  grid.className = "board-grid";
  grid.setAttribute("role", "grid");

  const corner = document.createElement("div");
  corner.className = "rule-head corner";
  corner.textContent = "Rules";
  grid.appendChild(corner);

  board.col_rule_labels.forEach((label) => {
    const head = document.createElement("div");
    head.className = "rule-head";
    head.textContent = formatRuleLabel(label);
    grid.appendChild(head);
  });

  for (let i = 0; i < board.n; i += 1) {
    const rowHead = document.createElement("div");
    rowHead.className = "row-head";
    rowHead.textContent = formatRuleLabel(board.row_rule_labels[i]);
    grid.appendChild(rowHead);

    for (let j = 0; j < board.n; j += 1) {
      const node = cellTemplate.content.firstElementChild.cloneNode(true);
      node.style.setProperty("--cell-index", String(i * board.n + j));
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

async function loadBoard(seed = "") {
  state.apiBase = apiBaseInput?.value?.trim() || state.apiBase || "http://127.0.0.1:8000";
  state.mode = modeSelect?.value || state.mode || "auto";
  const query = new URLSearchParams({ n: "9" });
  if (seed) {
    query.set("seed", seed);
  }

  setMessage("Loading board...");
  if (state.mode === "mock") {
    const board = buildMockBoard(seed || String(Date.now()), 9);
    state.board = board;
    clearBoardState();
    createBoardGrid(board);
    boardMeta.textContent = `Seed: ${board.seed} | Board: ${board.n}x${board.n}`;
    setMessage("Mock board loaded", "var(--ok)");
    return;
  }

  try {
    const board = await fetchJson(`${state.apiBase}/board?${query.toString()}`);
    state.board = board;
    clearBoardState();
    createBoardGrid(board);
    boardMeta.textContent = `Seed: ${board.seed} | Board: ${board.n}x${board.n}`;
    setMessage("Board loaded", "var(--ok)");
  } catch (err) {
    if (state.mode === "auto") {
      const board = buildMockBoard(seed || String(Date.now()), 9);
      state.mode = "mock";
      state.board = board;
      clearBoardState();
      createBoardGrid(board);
      boardMeta.textContent = `Seed: ${board.seed} | Board: ${board.n}x${board.n}`;
      setMessage("API unavailable; running mock mode", "var(--warn)");
      return;
    }
    setMessage(`Unable to load board: ${err.message || err}`, "var(--err)");
  }
}

function randomSeed() {
  return String(Math.floor(Math.random() * 1_000_000_000));
}

loadTodayBtn.addEventListener("click", () => loadBoard());
loadRandomBtn.addEventListener("click", () => loadBoard(randomSeed()));
apiBaseInput?.addEventListener("change", () => {
  state.apiBase = apiBaseInput.value.trim() || state.apiBase;
});
modeSelect?.addEventListener("change", () => {
  state.mode = modeSelect.value;
});

loadBoard();
