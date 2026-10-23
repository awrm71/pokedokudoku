from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple, Any, Set
import random
import pandas as pd
import numpy as np


# -----------------------------
# Core types
# -----------------------------

RuleFn = Callable[[pd.DataFrame], pd.Series]


@dataclass(frozen=True)
class Rule:
    rule_id: str          # stable id, good for API / Next.js
    label: str            # display label (e.g. "Gen: 3", "Type: Fire")
    fn: RuleFn            # returns boolean mask over df rows


@dataclass(frozen=True)
class Board:
    n_rows: int
    n_cols: int
    seed: str
    row_rule_ids: List[str]
    col_rule_ids: List[str]


# -----------------------------
# Normalization / lookup helpers
# -----------------------------

def normalize_name(s: str) -> str:
    s = (s or "").strip().lower()
    # keep letters/numbers only, remove spaces/punct for forgiving matching
    return "".join(ch for ch in s if ch.isalnum())


# ── Pokémon names: official spellings, forgiving search ──────────────────────
#
# The dataset's display names come from PokeAPI keys, which drop punctuation
# ("Ho Oh", "Kommo O", "Farfetchd", "Nidoran F") and tag the default form of
# some species ("Mimikyu Disguised"). official_display_names() fixes both.
#
# Search matches what players actually type:
#   exact      "ho-oh", "hooh", "ho oh", "mimikyu", "nidoran f"  -> one Pokémon
#   prefix     "char"            -> Charmander, Charmeleon, Charizard …
#   word       "koko", "tusk"    -> Tapu Koko, Great Tusk
#   words      "alolan vulpix", "mega charizard", "gmax snorlax"
#   typos      "picachu", "feraligator", "gyrados"   (4+ letters)
#   sound      "kamoo", "garados", "sharizard"       (4+ letters)
# Suggestions never look at the board, so they can't give an answer away.

import unicodedata as _ud
import re as _re

# Prefix rewrites on the dataset's display names (forms keep their suffix:
# "Mr Mime (Galar)" -> "Mr. Mime (Galar)").
_NAME_FIXES = [
    ("Ho Oh", "Ho-Oh"), ("Kommo O", "Kommo-o"), ("Jangmo O", "Jangmo-o"), ("Hakamo O", "Hakamo-o"),
    ("Mr Mime", "Mr. Mime"), ("Mr Rime", "Mr. Rime"), ("Mime Jr", "Mime Jr."),
    ("Farfetchd", "Farfetch'd"), ("Sirfetchd", "Sirfetch'd"),
    ("Nidoran F", "Nidoran♀"), ("Nidoran M", "Nidoran♂"),
    ("Flabebe", "Flabébé"), ("Type Null", "Type: Null"), ("Porygon Z", "Porygon-Z"),
    ("Chi Yu", "Chi-Yu"), ("Ting Lu", "Ting-Lu"), ("Wo Chien", "Wo-Chien"), ("Chien Pao", "Chien-Pao"),
]

# Extra words people use for forms.
_FORM_WORDS = {
    "alola": ["alolan"], "galar": ["galarian"], "hisui": ["hisuian"], "paldea": ["paldean"],
    "gmax": ["gigantamax", "g max"], "mega": [], "f": ["female"], "m": ["male"], "mr": ["mister"],
}


def fold(s: str) -> str:
    """Lowercase ASCII letters/digits only: accents dropped (é -> e), ♀/♂ -> f/m,
    everything else removed. "Flabébé" -> "flabebe", "Ho-Oh" -> "hooh"."""
    s = (s or "").replace("♀", " f").replace("♂", " m")
    s = _ud.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii").lower()
    return "".join(ch for ch in s if ch.isalnum())


def _words(s: str) -> List[str]:
    s = (s or "").replace("♀", " f").replace("♂", " m")
    s = _ud.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii").lower()
    s = s.replace("'", "").replace(".", " ")
    return [w for w in _re.split(r"[^a-z0-9]+", s) if w]


def _sound(s: str) -> str:
    """A rough 'sounds like' key: similar consonants merged, every vowel the
    same, doubles collapsed. "kommoo" and "kamoo" both become "kama"."""
    s = fold(s)
    for a, b in (("ph", "f"), ("ck", "k"), ("ch", "s"), ("sh", "s"), ("qu", "k"),
                 ("c", "k"), ("q", "k"), ("x", "ks"), ("z", "s"), ("y", "i")):
        s = s.replace(a, b)
    s = _re.sub(r"[aeiou]", "a", s)
    s = _re.sub(r"(.)\1+", r"\1", s)
    return s


def _dl_distance(a: str, b: str, cap: int) -> int:
    """Damerau-Levenshtein (adjacent swaps count as one) with an early exit."""
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    if a == b:
        return 0
    prev2 = None
    prev = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if prev2 is not None and i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        if min(cur) > cap:
            return cap + 1
        prev2, prev = prev, cur
    return prev[len(b)]


# Kept for older callers.
def _edit_distance(a: str, b: str, cap: int) -> int:
    return _dl_distance(a, b, cap)


def official_display_names(df: pd.DataFrame) -> pd.Series:
    """Display names with official punctuation, and the default form of a
    species shown as just the species ("Mimikyu", not "Mimikyu Disguised")."""
    names = df["display_name"].astype(str).copy()

    def fix(n: str) -> str:
        for old, new in _NAME_FIXES:
            if n == old or n.startswith(old + " "):
                return new + n[len(old):]
        return n

    names = names.map(fix)
    if "species_key" in df.columns and "is_default" in df.columns:
        for i in df.index[(df["is_default"] == True) & (df["key"] != df["species_key"])]:
            base = str(df.at[i, "species_key"]).replace("-", " ").title()
            base = fix(base)
            # Only when the name is "<species> <form>" (not a regional form).
            if names.at[i].lower().startswith(base.lower() + " ") and "(" not in names.at[i]:
                names.at[i] = base
    return names


def build_name_index(df: pd.DataFrame) -> pd.DataFrame:
    """
    Returns: columns [key, display_name, norm, aliases, tokens, sounds, is_default]
    norm     folded display name ("hooh")
    aliases  every folded spelling that should resolve exactly
    tokens   words for word/multi-word matching, incl. "alolan", "gigantamax"
    sounds   _sound() of each alias
    """
    idx = df[["key", "display_name"]].copy()
    idx["is_default"] = df["is_default"].astype(bool) if "is_default" in df.columns else True
    idx["norm"] = idx["display_name"].astype(str).map(fold)
    aliases_col, tokens_col, sounds_col = [], [], []
    for _, row in df.iterrows():
        disp = str(row["display_name"])
        key = str(row["key"])
        words = _words(disp) + _words(key.replace("-", " "))
        extra = []
        for w in list(words):
            extra += _FORM_WORDS.get(w, [])
        toks = list(dict.fromkeys(words + [x for e in extra for x in e.split()]))
        aliases = {fold(disp), fold(key)}
        # Regional forms also answer to "alolan vulpix" / "vulpix alolan".
        if "(" in disp:
            base = fold(disp.split("(")[0])
            region = fold(disp.split("(")[1].split(")")[0])
            for adj in [region] + _FORM_WORDS.get(region, []):
                aliases.add(fold(adj) + base)
                aliases.add(base + fold(adj))
        # ♀/♂ also spelled out.
        if "♀" in disp or "♂" in disp:
            aliases.add(fold(disp.replace("♀", " female").replace("♂", " male")))
        aliases.discard("")
        aliases_col.append(sorted(aliases, key=len))
        tokens_col.append(toks)
        sounds_col.append(sorted({_sound(a) for a in aliases}, key=len))
    idx["aliases"] = aliases_col
    idx["tokens"] = tokens_col
    idx["sounds"] = sounds_col
    return idx


def resolve_input_to_key(user_text: str, name_index: pd.DataFrame) -> Optional[str]:
    """
    Resolves free text to a Pokémon key when it names exactly one: any spelling
    in that Pokémon's aliases ("Ho-Oh", "hooh", "ho oh", "Mimikyu", "alolan
    vulpix"). The species name picks its default form. Typos never resolve
    here; they only show up as suggestions to tap.
    """
    t = fold(user_text)
    if not t:
        return None
    hits = [(not r.is_default, len(r.display_name), r.key)
            for r in name_index.itertuples() if t in r.aliases]
    if not hits:
        return None
    return str(sorted(hits)[0][2])


def _fuzzy_cap(n: int) -> int:
    return 1 if n <= 5 else 2 if n <= 9 else 3


def suggest_names_global(prefix: str, name_index: pd.DataFrame, limit: int = 10) -> List[Tuple[str, str]]:
    """
    Ranked suggestions for what's been typed so far (2+ letters):
      0 exact spelling     1 start of the name     2 start of a word / every word
      3 inside the name    4 typo or sounds alike (4+ letters)
    Ties go to the default form, then the shorter name.
    """
    q = fold(prefix)
    if len(q) < 2:
        return []
    qwords = [w for w in _words(prefix) if w]
    qsound = _sound(q)
    fuzzy = len(q) >= 4
    cap = _fuzzy_cap(len(q))

    scored = []
    for r in name_index.itertuples():
        best = None
        if q in r.aliases:
            best = 0.0
        elif any(a.startswith(q) for a in r.aliases):
            best = 1.0
        elif any(t.startswith(q) for t in r.tokens) or (
                len(qwords) > 1 and all(any(t.startswith(w) for t in r.tokens) for w in qwords)):
            best = 2.0
        elif len(q) >= 3 and any(q in a for a in r.aliases):
            best = 3.0
        elif fuzzy:
            d = min(min(_dl_distance(q, a[:len(q)], cap), _dl_distance(q, a, cap)) for a in r.aliases)
            # Sounds alike: same "sound key" start ("kamoo" ~ "kommoo"). Ranked
            # between a one-letter typo and a two-letter one.
            sounds = len(qsound) >= 3 and any(x.startswith(qsound) for x in r.sounds)
            if d <= cap:
                best = 4.0 + d / 10
            if sounds:
                best = min(best if best is not None else 9.0, 4.15)
        if best is not None:
            scored.append((best, not r.is_default, len(r.display_name), str(r.display_name), str(r.key)))

    scored.sort()
    return [(k, d) for _, _, _, d, k in scored[:limit]]


def apply_answer_policy(df: pd.DataFrame, policy: str = "species_only") -> pd.DataFrame:
    """
    Controls what rows are eligible as answers.

    policy:
      - "species_only": simplest. Assumes df rows already represent one playable entry per species.
        If your df includes forms, this will need additional columns to filter forms out.
      - "all": keep everything

    For now, we keep it conservative and do no-op unless you later add form flags.
    """
    policy = (policy or "species_only").strip().lower()
    if policy == "all":
        return df.copy()

    # species_only (default): keep default forms, mega/gmax variants,
    # regional forms (Alolan, Galarian, Hisuian, Paldean, Primal, etc.),
    # and alternate/battle forms (Necrozma fusions, Rotom appliances,
    # Therian formes, etc.) so all those form categories are valid answers.
    if "is_default" in df.columns:
        keep = df["is_default"] == True
        if "is_mega_form" in df.columns:
            keep = keep | (df["is_mega_form"] == True)
        if "is_gmax_form" in df.columns:
            keep = keep | (df["is_gmax_form"] == True)
        if "is_regional_form" in df.columns:
            keep = keep | (df["is_regional_form"] == True)
        if "is_alt_form" in df.columns:
            keep = keep | (df["is_alt_form"] == True)
        return df[keep].copy()

    return df.copy()


# -----------------------------
# Rules library
# -----------------------------

def build_default_rules(df: pd.DataFrame) -> Dict[str, Rule]:
    """
    Builds a rule dictionary using columns you already have.
    """
    rules: Dict[str, Rule] = {}

    def _has_any_true(col: str) -> bool:
        if col not in df.columns:
            return False
        try:
            return bool((df[col] == True).any())
        except Exception:
            return False

    # Types
    type1 = df["type1"].fillna("").astype(str) if "type1" in df.columns else pd.Series([], dtype=str)
    type2 = df["type2"].fillna("").astype(str) if "type2" in df.columns else pd.Series([], dtype=str)
    types = sorted(set(type1).union(set(type2)) - {"", "nan", "None"})
    for t in types:
        tid = f"type_{t}"
        label = f"Type: {t.title()}"
        rules[tid] = Rule(
            rule_id=tid,
            label=label,
            fn=lambda d, t=t: (d["type1"] == t) | (d["type2"] == t),
        )

    # Gen number rules intentionally removed — region names (Kanto, Johto, Hoenn, etc.)
    # cover the same Pokémon sets and are more recognisable to players.
    # Having both would create duplicate categories on the same board.

    # Flags
    # (these assume columns exist; if not, you can remove them or add guards)
    rules["legendary"] = Rule("legendary", "Legendary", lambda d: d["is_legendary"] == True)
    rules["mythical"] = Rule("mythical", "Mythical", lambda d: d["is_mythical"] == True)
    rules["baby"] = Rule("baby", "Baby", lambda d: d["is_baby"] == True)
    rules["not_legendary"] = Rule("not_legendary", "Not Legendary", lambda d: d["is_legendary"] == False)
    rules["single_type"] = Rule("single_type", "Single-type", lambda d: d["type2"].fillna("") == "")
    rules["dual_type"] = Rule("dual_type", "Dual-type", lambda d: d["type2"].fillna("") != "")

    # Form / feature flags (if present)
    # "Mega" / "Gmax" should refer to the actual variant rows (answers are e.g. "gardevoir-mega"),
    # not base species that merely *have* a form.
    if _has_any_true("is_mega_form"):
        rules["mega"] = Rule("mega", "Mega Evolved", lambda d: d["is_mega_form"] == True)
    if _has_any_true("is_gmax_form"):
        rules["gmax"] = Rule("gmax", "Gigantamax", lambda d: d["is_gmax_form"] == True)

    if _has_any_true("is_fossil"):
        rules["fossil"] = Rule("fossil", "Fossil Pokémon", lambda d: d["is_fossil"] == True)

    # Starter Pokémon lines (populated by patch_flags.py)
    if _has_any_true("is_first_partner"):
        rules["starter"] = Rule("starter", "Starter", lambda d: d["is_first_partner"] == True)

    # Ultra Beasts
    if _has_any_true("is_ultra_beast"):
        rules["ultra_beast"] = Rule("ultra_beast", "Ultra Beast", lambda d: d["is_ultra_beast"] == True)

    # Paradox Pokémon
    if _has_any_true("is_paradox"):
        rules["paradox"] = Rule("paradox", "Paradox", lambda d: d["is_paradox"] == True)

    # Regional forms rule (if augmentation has been run)
    if _has_any_true("is_regional_form"):
        rules["regional_form"] = Rule("regional_form", "Regional Form", lambda d: d["is_regional_form"] == True)

    # Regions (if present)
    if "region" in df.columns:
        regions = sorted(set([str(x) for x in df["region"].fillna("").unique().tolist() if str(x)]))
        for r in regions:
            rid = f"region_{normalize_name(r)}"
            rules[rid] = Rule(rid, r, lambda d, r=r: d["region"].fillna("") == r)

    # Evolution metadata (if present)
    if _has_any_true("is_branched_line"):
        rules["branched"] = Rule("branched", "Is Branched", lambda d: d["is_branched_line"] == True)
    if "evo_position" in df.columns:
        # Keep labels aligned with the UI screenshots.
        for pos, label in [
            ("premature", "Position: Premature"),
            ("start", "Position: Start"),
            ("middle", "Position: Middle"),
            ("final", "Position: Final"),
            ("none", "Position: None"),
        ]:
            rid = f"evo_{pos}"
            if bool((df["evo_position"].fillna("") == pos).any()):
                rules[rid] = Rule(rid, label, lambda d, pos=pos: d["evo_position"].fillna("") == pos)

    # Evolution methods (if present)
    # Evolution method rules: PokeDoku-style semantics are "this Pokémon evolves by X"
    # (outgoing), not "this Pokémon was evolved by X" (incoming).
    if _has_any_true("evolves_by_levelup"):
        rules["evolves_by_levelup"] = Rule(
            "evolves_by_levelup",
            "Evolves By: Level-up",
            lambda d: d["evolves_by_levelup"] == True,
        )

    if _has_any_true("evolves_by_friendship"):
        rules["evolves_by_friendship"] = Rule(
            "evolves_by_friendship",
            "Evolves By: Friendship",
            lambda d: d["evolves_by_friendship"] == True,
        )

    if _has_any_true("evolves_by_trade"):
        rules["evolves_by_trade"] = Rule(
            "evolves_by_trade",
            "Evolves By: Trade",
            lambda d: d["evolves_by_trade"] == True,
        )

    if _has_any_true("evolves_by_item"):
        rules["evolves_by_item"] = Rule(
            "evolves_by_item",
            "Evolves By: Item",
            lambda d: d["evolves_by_item"] == True,
        )

    # Optional one-hot features (generated by scripts/build_pokedex.py)
    # Moves: learns_<move>
    for col in sorted([c for c in df.columns if str(c).startswith("learns_")]):
        try:
            if not bool((df[col] == True).any()):
                continue
        except Exception:
            continue
        token = str(col)[len("learns_"):]
        rid = f"move_{token}"
        label = f"Move: {token.replace('_', ' ').title()}"
        rules[rid] = Rule(rid, label, lambda d, col=col: d[col] == True)

    # Abilities: has_ability_<ability>
    for col in sorted([c for c in df.columns if str(c).startswith("has_ability_")]):
        try:
            if not bool((df[col] == True).any()):
                continue
        except Exception:
            continue
        token = str(col)[len("has_ability_"):]
        rid = f"ability_{token}"
        label = f"Ability: {token.replace('_', ' ').title()}"
        rules[rid] = Rule(rid, label, lambda d, col=col: d[col] == True)

    return rules


def compute_rule_masks(df: pd.DataFrame, rules: Dict[str, Rule]) -> Dict[str, pd.Series]:
    """
    Precompute each rule mask once for speed.
    """
    masks: Dict[str, pd.Series] = {}
    for rid, rule in rules.items():
        masks[rid] = rule.fn(df).fillna(False)
    return masks


# -----------------------------
# Candidate math
# -----------------------------

def candidate_count(rule_masks: Dict[str, pd.Series], row_rule_id: str, col_rule_id: str) -> int:
    return int((rule_masks[row_rule_id] & rule_masks[col_rule_id]).sum())


def candidates_for_cell(df: pd.DataFrame, rule_masks: Dict[str, pd.Series], row_rule_id: str, col_rule_id: str) -> pd.DataFrame:
    m = rule_masks[row_rule_id] & rule_masks[col_rule_id]
    return df[m].copy()


# -----------------------------
# Board generation
# -----------------------------

@dataclass(frozen=True)
class BoardConstraints:
    min_candidates_per_cell: int = 1
    max_candidates_per_cell: Optional[int] = None
    max_tries: int = 1000
    require_distinct_solution: bool = False


def _hopcroft_karp_has_perfect_matching(adj: List[List[int]], right_size: int) -> bool:
    """Return True if there exists a matching that covers all left nodes.

    adj: left node -> list of right node indices.
    right_size: number of nodes on right.
    """

    left_size = len(adj)
    if left_size == 0:
        return True

    # Quick fail: any empty neighborhood.
    if any(len(nbrs) == 0 for nbrs in adj):
        return False

    # Pairings: -1 indicates free.
    pair_u = [-1] * left_size
    pair_v = [-1] * right_size
    dist = [0] * left_size

    INF = 10**9

    from collections import deque

    def bfs() -> bool:
        q = deque()
        for u in range(left_size):
            if pair_u[u] == -1:
                dist[u] = 0
                q.append(u)
            else:
                dist[u] = INF

        found_free_on_right = False
        while q:
            u = q.popleft()
            for v in adj[u]:
                pu = pair_v[v]
                if pu != -1 and dist[pu] == INF:
                    dist[pu] = dist[u] + 1
                    q.append(pu)
                if pu == -1:
                    found_free_on_right = True
        return found_free_on_right

    def dfs(u: int) -> bool:
        for v in adj[u]:
            pu = pair_v[v]
            if pu == -1 or (dist[pu] == dist[u] + 1 and dfs(pu)):
                pair_u[u] = v
                pair_v[v] = u
                return True
        dist[u] = INF
        return False

    matching = 0
    while bfs():
        for u in range(left_size):
            if pair_u[u] == -1 and dfs(u):
                matching += 1
                if matching == left_size:
                    return True

    return matching == left_size


def _kuhn_has_perfect_matching(adj: List[List[int]], right_size: int) -> bool:
    """Return True if there exists a matching that covers all left nodes.

    For our use-case (81 left nodes max), a simple DFS augmenting-path
    implementation is fast and robust.
    """

    left_size = len(adj)
    if left_size == 0:
        return True
    if right_size < left_size:
        return False
    if any(len(nbrs) == 0 for nbrs in adj):
        return False

    match_r = [-1] * right_size

    # Avoid allocating a large boolean array for every left node.
    seen_tag = [0] * right_size
    tag = 0

    def aug(u: int) -> bool:
        nonlocal tag
        for v in adj[u]:
            if seen_tag[v] == tag:
                continue
            seen_tag[v] = tag
            if match_r[v] == -1 or aug(match_r[v]):
                match_r[v] = u
                return True
        return False

    for u in range(left_size):
        tag += 1
        if not aug(u):
            return False
    return True


def has_distinct_solution(
    df: pd.DataFrame,
    rule_masks: Dict[str, pd.Series],
    row_rule_ids: List[str],
    col_rule_ids: List[str],
) -> bool:
    """Check whether there exists an assignment of distinct Pokémon keys to all cells.

    This prevents generating boards where a Pokémon is the only candidate for
    multiple different cells (forcing duplicates).
    """
    if "key" not in df.columns:
        return False

    # Build a compact right-side key set using only candidates that appear in any cell.
    keys = df["key"].astype(str).to_numpy()
    key_to_idx: Dict[str, int] = {}

    # Precompute masks once for this check.
    row_np: Dict[str, np.ndarray] = {
        rr: rule_masks[rr].fillna(False).to_numpy(dtype=bool, copy=False) for rr in row_rule_ids
    }
    col_np: Dict[str, np.ndarray] = {
        cc: rule_masks[cc].fillna(False).to_numpy(dtype=bool, copy=False) for cc in col_rule_ids
    }

    adj: List[List[int]] = []
    for rr in row_rule_ids:
        rr_mask = row_np[rr]
        for cc in col_rule_ids:
            cc_mask = col_np[cc]
            idxs = np.flatnonzero(rr_mask & cc_mask)

            nbrs: List[int] = []
            for i in idxs:
                k = str(keys[i])
                j = key_to_idx.get(k)
                if j is None:
                    j = len(key_to_idx)
                    key_to_idx[k] = j
                nbrs.append(j)
            adj.append(nbrs)

    # Quick fail: not enough distinct candidates overall.
    if len(key_to_idx) < len(adj):
        return False

    return _kuhn_has_perfect_matching(adj, right_size=len(key_to_idx))


def generate_board(
    df: pd.DataFrame,
    rules: Dict[str, Rule],
    rule_masks: Dict[str, pd.Series],
    seed: str,
    n_rows: int,
    n_cols: Optional[int] = None,
    constraints: BoardConstraints = BoardConstraints(),
    allowed_rule_ids: Optional[Set[str]] = None,
) -> Optional[Board]:
    """
    Randomly samples row/col rules until all intersections meet constraints.
    Deterministic given seed.

    n_cols defaults to n_rows (a square board) for full backward compatibility
    with every existing caller — pass it explicitly for a rectangular board
    (e.g. n_rows=6, n_cols=3).

    allowed_rule_ids: if given, restricts the entire candidate pool to just
    these rule ids (e.g. for a user-customized "Draft Board" where only
    certain categories are enabled). Every selection/weighting/diversity path
    below operates on `rule_ids`, so filtering it here is sufficient — no
    other logic needs to change. If the restriction leaves too few rules to
    fill the board, this returns None exactly like any other
    infeasible-constraints case.
    """
    if n_cols is None:
        n_cols = n_rows

    rng = random.Random(seed)
    rule_ids = list(rules.keys())
    if allowed_rule_ids is not None:
        rule_ids = [rid for rid in rule_ids if rid in allowed_rule_ids]

    def _rule_group(rid: str) -> str:
        rid = str(rid)
        if rid.startswith("move_"):                                    return "move"
        if rid.startswith("ability_"):                                 return "ability"
        if rid.startswith("type_"):                                    return "type"
        if rid.startswith("gen_"):                                     return "gen"
        if rid.startswith("region_"):                                  return "region"
        if rid.startswith("evo_") or rid.startswith("evolves_by_") or rid == "branched": return "evolution"
        if rid in {"mega"}:                                            return "mega"
        if rid in {"gmax"}:                                            return "gmax"
        if rid in {"starter", "first_partner"}:                        return "starter"
        if rid in {"ultra_beast"}:                                     return "ultra"
        if rid in {"paradox"}:                                         return "paradox"
        return "flag"

    rule_support: Dict[str, int] = {}
    if constraints.require_distinct_solution:
        for rid in rule_ids:
            try:
                rule_support[rid] = int(rule_masks[rid].fillna(False).sum())
            except Exception:
                rule_support[rid] = 0

    # To avoid boards that are almost entirely move-based (since common moves are extremely broad),
    # apply gentle down-weighting + a per-axis cap for move/ability groups. Caps relax late in the
    # search so we don't reintroduce generation failures.
    GROUP_WEIGHT = {
        "move":      0.18,   # down-weighted; broad filter keeps ~22 moves
        "ability":   0.85,
        "type":      1.30,   # capped to 2-3 per axis
        "evolution": 1.60,   # position + method rules
        "region":    1.55,   # Kanto/Johto/Hoenn/Alola etc.
        "flag":      1.20,   # legendary, baby, fossil, mono-type, dual-type, branched
        "mega":      1.25,   # mega evolution forms
        "gmax":      1.00,   # gigantamax forms
        "starter":   1.40,   # starter Pokémon lines
        "ultra":     1.15,   # ultra beasts
        "paradox":   1.10,   # paradox Pokémon
    }

    def _weighted_choice(items: List[str], weights: List[float]) -> Optional[str]:
        total = float(sum(weights))
        if total <= 0:
            return None
        r = rng.uniform(0.0, total)
        acc = 0.0
        for it, w in zip(items, weights):
            acc += float(w)
            if acc >= r:
                return it
        return items[-1] if items else None

    def _support_weight(rid: str) -> int:
        if not constraints.require_distinct_solution:
            return 1
        g    = _rule_group(rid)
        base = max(1, rule_support.get(rid, 1))

        # Region rules: FLAT weight — all 9 regions equally probable.
        if g == "region":
            return max(1, int(round(float(GROUP_WEIGHT.get(g, 1.0)) * 100)))

        # Ability rules: flat-ish so all abilities appear equally often.
        if g == "ability":
            return max(1, int(round(float(GROUP_WEIGHT.get(g, 1.0)) * 60)))

        # Categorical flags (mega, gmax, starter, ultra, paradox).
        if g in ("flag", "mega", "gmax", "starter", "ultra", "paradox"):
            base = max(1, int(round(float(base) ** 0.6)))
            return max(1, int(round(base * float(GROUP_WEIGHT.get(g, 1.0)))))

        if str(rid).startswith("evolves_by_"):
            base = max(1, int(round(float(base) ** 0.5)))
            base *= 8
        elif g == "evolution":
            base = max(1, int(round(float(base) ** 0.5)))

        scale = float(GROUP_WEIGHT.get(g, 1.0))
        return max(1, int(round(base * scale)))

    def _pool_for_attempt(attempt: int) -> List[str]:
        if not constraints.require_distinct_solution:
            return rule_ids

        # Start biased toward broad rules, then gradually allow narrower rules.
        df_size = max(1, len(df))
        if attempt < int(constraints.max_tries * 0.35):
            min_support = int(df_size * 0.15)
        elif attempt < int(constraints.max_tries * 0.70):
            min_support = int(df_size * 0.08)
        else:
            min_support = 0

        # Types are core categories but individual types can be < the early min_support.
        # Use a lower threshold for type rules so they remain eligible.
        if attempt < int(constraints.max_tries * 0.70):
            type_min_support = int(df_size * 0.04)  # ~4% of answers (keeps rarest out, includes most types)
        else:
            type_min_support = 0

        # Evolution method rules (evolves_by_*) are intentionally narrower.
        # If we apply the global min_support early, they almost never show up.
        # Give the evolution group a lower floor so "Evolves By: ..." categories
        # can appear without waiting until the very end of the search.
        if attempt < int(constraints.max_tries * 0.70):
            evolution_min_support = int(df_size * 0.01)  # ~1% of answers
        else:
            evolution_min_support = 0

        pool: List[str] = []
        for rid in rule_ids:
            s = int(rule_support.get(rid, 0))
            g = _rule_group(rid)
            if g == "type":
                if s >= type_min_support:
                    pool.append(rid)
            elif g == "evolution":
                if s >= evolution_min_support:
                    pool.append(rid)
            elif g == "ability":
                # All 16 tracked abilities have 30-46 Pokémon — always eligible.
                if s >= 10:
                    pool.append(rid)
            elif g == "region":
                # All 9 regions always eligible — without this Kanto (184) is the only
                # region that clears the 15% early threshold (173). Others are blocked.
                if s >= 5:
                    pool.append(rid)
            elif g in ("flag", "mega", "gmax", "starter", "ultra", "paradox"):
                if s >= 5:
                    pool.append(rid)
            else:
                if s >= min_support:
                    pool.append(rid)
        # Filter moves that cover >35% of pool (Protect 91%, Substitute 91% etc.)
        # Also filter non-type rules >80% coverage (e.g. "Not Legendary" 93%)
        BROAD_MOVE_CUTOFF  = int(df_size * 0.35)
        BROAD_OTHER_CUTOFF = int(df_size * 0.80)
        filtered = [
            rid for rid in pool
            if not (_rule_group(rid) == "move"  and rule_support.get(rid, 0) > BROAD_MOVE_CUTOFF)
            and not (_rule_group(rid) not in ("type","move") and rule_support.get(rid, 0) > BROAD_OTHER_CUTOFF)
        ]
        pool = filtered if len(filtered) >= max(n_rows, n_cols) else pool
        return pool if len(pool) >= max(n_rows, n_cols) else rule_ids

    def _caps_for_attempt(attempt: int) -> Dict[str, int]:
        if not constraints.require_distinct_solution:
            return {}

        # Keep variety but avoid making generation expensive.
        # Relax caps late so we don't fail to generate.
        if attempt < int(constraints.max_tries * 0.40):
            move_cap    = 2          # max 2 move rules per axis — forces category variety
            ability_cap = 1
        elif attempt < int(constraints.max_tries * 0.70):
            move_cap    = 3
            ability_cap = 1
        else:
            move_cap    = max(n_rows, n_cols)  # full relaxation in last 30% of tries
            ability_cap = max(n_rows, n_cols)

        return {"move": int(move_cap), "ability": int(ability_cap), "region": 1}

    def _cap_ok(candidate: str, current_axis: List[str], caps: Dict[str, int]) -> bool:
        g = _rule_group(candidate)
        cap = caps.get(g)
        if cap is None:
            return True
        used = sum(1 for rid in current_axis if _rule_group(rid) == g)
        return (used + 1) <= int(cap)


    def _compatible_weight(candidate: str, other_axis: List[str], picking_row: bool, compat_min: int) -> float:
        # Weight by (min intersection count across the other axis) * support.
        if not other_axis:
            return float(_support_weight(candidate))

        # Evolution-method rules (evolves_by_*) are naturally narrow.
        # When require_distinct_solution=True we default compat_min to 2 to avoid
        # ultra-narrow boards, but that effectively starves these categories.
        # Allow them with a floor of 1 while still enforcing distinct solutions.
        local_min = 1 if str(candidate).startswith("evolves_by_") else int(compat_min)

        min_cnt = None
        for other in other_axis:
            cnt = candidate_count(rule_masks, candidate, other) if picking_row else candidate_count(rule_masks, other, candidate)
            if cnt < local_min:
                return 0.0
            if constraints.max_candidates_per_cell is not None and cnt > constraints.max_candidates_per_cell:
                return 0.0
            min_cnt = cnt if min_cnt is None else min(min_cnt, cnt)

        return float(max(1, min_cnt or 1) * _support_weight(candidate))

    def _group_count(rids: List[str], group: str) -> int:
        return sum(1 for rid in rids if _rule_group(rid) == group)

    if len(rule_ids) < max(n_rows, n_cols):
        return None

    for _ in range(constraints.max_tries):
        attempt = _
        if constraints.require_distinct_solution:
            pool = _pool_for_attempt(attempt)
            caps = _caps_for_attempt(attempt)

            # When we require a distinct full-board solution, low-count intersections make
            # perfect matchings much less likely. Use a slightly higher compatibility floor
            # during selection to avoid ultra-narrow rule combinations.
            compat_min = max(constraints.min_candidates_per_cell, 2)

            row_rule_ids: List[str] = []
            col_rule_ids: List[str] = []

            type_pool = [rid for rid in pool if _rule_group(rid) == "type"]
            # Prefer to include types early; relaxing late keeps generation robust.
            prefer_type = attempt < int(constraints.max_tries * 0.85)

            # Require a reasonable amount of types per axis to keep boards readable/varied.
            # We enforce a random target in [2, 4] for both rows and columns.
            # (If an axis is smaller, clamp accordingly.)
            row_min_types = min(2, n_rows)
            row_max_types = min(3, n_rows)   # cap at 3, not 4 — leaves room for region/evo rules
            col_min_types = min(2, n_cols)
            col_max_types = min(3, n_cols)
            target_row_types = rng.randint(row_min_types, row_max_types) if row_max_types > 0 else 0
            target_col_types = rng.randint(col_min_types, col_max_types) if col_max_types > 0 else 0

            def _type_gate(candidate: str, axis: List[str], target: int, axis_n: int) -> bool:
                """Return True if candidate keeps axis able to reach exactly `target` types."""
                cur_types = _group_count(axis, "type")
                remaining_after = axis_n - (len(axis) + 1)
                is_type = (_rule_group(candidate) == "type")
                new_types = cur_types + (1 if is_type else 0)

                # Can't exceed target.
                if new_types > target:
                    return False

                # Must still be able to reach target with remaining slots.
                need = target - new_types
                if need < 0:
                    return False
                if need > remaining_after:
                    return False
                return True

            def _must_pick_type(axis: List[str], target: int, axis_n: int) -> bool:
                """True if all remaining picks must be types to hit the target."""
                cur_types = _group_count(axis, "type")
                remaining = axis_n - len(axis)
                need = max(0, target - cur_types)
                return need == remaining

            # Seed with a broad row rule.
            # If we need types, start with a type when possible.
            seed_base = type_pool if (prefer_type and target_row_types > 0 and len(type_pool) >= 1) else pool
            seed_candidates = [
                rid
                for rid in seed_base
                if _cap_ok(rid, row_rule_ids, caps)
                and _type_gate(rid, row_rule_ids, target_row_types, n_rows)
            ]
            seed_weights = [float(_support_weight(rid)) for rid in seed_candidates]
            first_row = _weighted_choice(seed_candidates, seed_weights)
            if first_row is None:
                continue
            row_rule_ids.append(first_row)

            # Seed with a column rule (prefer type too), compatible with the first row.
            col_seed_base = type_pool if (prefer_type and target_col_types > 0 and len(type_pool) >= 2) else pool
            def _seed_floor(rid: str, default_floor: int) -> int:
                return 1 if str(rid).startswith("evolves_by_") else int(default_floor)

            col_seed_candidates = [
                rid
                for rid in col_seed_base
                if rid not in col_rule_ids
                and rid not in row_rule_ids
                and _cap_ok(rid, col_rule_ids, caps)
                and _type_gate(rid, col_rule_ids, target_col_types, n_cols)
                and candidate_count(rule_masks, first_row, rid) >= _seed_floor(rid, compat_min)
            ]
            if not col_seed_candidates:
                col_seed_candidates = [
                    rid
                    for rid in pool
                    if rid not in col_rule_ids
                    and _cap_ok(rid, col_rule_ids, caps)
                    and _type_gate(rid, col_rule_ids, target_col_types, n_cols)
                    and candidate_count(rule_masks, first_row, rid) >= _seed_floor(rid, compat_min)
                ]
            col_seed_weights = [float(_support_weight(rid)) for rid in col_seed_candidates]
            first_col = _weighted_choice(col_seed_candidates, col_seed_weights)
            if first_col is None:
                continue
            col_rule_ids.append(first_col)

            # Alternate picking columns/rows, enforcing compatibility with the opposite axis.
            while len(row_rule_ids) < n_rows or len(col_rule_ids) < n_cols:
                if len(col_rule_ids) < n_cols:
                    # If we've already picked many move rules for columns, nudge away from ultra-narrow intersections.
                    axis_compat_min = compat_min
                    if _group_count(col_rule_ids, "move") >= 4:
                        axis_compat_min = max(axis_compat_min, 3)

                    must_type = _must_pick_type(col_rule_ids, target_col_types, n_cols)
                    col_candidates = [
                        rid
                        for rid in pool
                        if rid not in col_rule_ids
                        and rid not in row_rule_ids
                        and _cap_ok(rid, col_rule_ids, caps)
                        and _type_gate(rid, col_rule_ids, target_col_types, n_cols)
                        and (not must_type or _rule_group(rid) == "type")
                    ]
                    col_weights = [
                        _compatible_weight(rid, other_axis=row_rule_ids, picking_row=False, compat_min=axis_compat_min)
                        for rid in col_candidates
                    ]
                    pick = _weighted_choice(col_candidates, col_weights)
                    if pick is None:
                        break
                    col_rule_ids.append(pick)

                if len(row_rule_ids) < n_rows:
                    axis_compat_min = compat_min
                    if _group_count(row_rule_ids, "move") >= 4:
                        axis_compat_min = max(axis_compat_min, 3)

                    must_type = _must_pick_type(row_rule_ids, target_row_types, n_rows)
                    row_candidates = [
                        rid
                        for rid in pool
                        if rid not in row_rule_ids
                        and rid not in col_rule_ids
                        and _cap_ok(rid, row_rule_ids, caps)
                        and _type_gate(rid, row_rule_ids, target_row_types, n_rows)
                        and (not must_type or _rule_group(rid) == "type")
                    ]
                    row_weights = [
                        _compatible_weight(rid, other_axis=col_rule_ids, picking_row=True, compat_min=axis_compat_min)
                        for rid in row_candidates
                    ]
                    pick = _weighted_choice(row_candidates, row_weights)
                    if pick is None:
                        break
                    row_rule_ids.append(pick)

            if len(row_rule_ids) != n_rows or len(col_rule_ids) != n_cols:
                continue
        else:
            row_rule_ids = rng.sample(rule_ids, k=n_rows)
            col_rule_ids = rng.sample(rule_ids, k=n_cols)

        ok = True
        for rr in row_rule_ids:
            for cc in col_rule_ids:
                cnt = candidate_count(rule_masks, rr, cc)
                if cnt < constraints.min_candidates_per_cell:
                    ok = False
                    break
                if constraints.max_candidates_per_cell is not None and cnt > constraints.max_candidates_per_cell:
                    ok = False
                    break
            if not ok:
                break

        if ok:
            if constraints.require_distinct_solution:
                # Fast necessary conditions for a perfect matching.
                row_any = pd.Series(False, index=df.index)
                col_any = pd.Series(False, index=df.index)
                for rr in row_rule_ids:
                    row_any |= rule_masks[rr].fillna(False)
                for cc in col_rule_ids:
                    col_any |= rule_masks[cc].fillna(False)

                # There must be at least n_rows*n_cols distinct Pokémon that match at least one
                # row rule and at least one column rule (i.e., appear in at least one cell).
                if int((row_any & col_any).sum()) < (n_rows * n_cols):
                    continue

                # Each row must have at least n_cols distinct candidates across its cells
                # (one per column), and each column at least n_rows (one per row).
                for rr in row_rule_ids:
                    if int((rule_masks[rr].fillna(False) & col_any).sum()) < n_cols:
                        ok = False
                        break
                if not ok:
                    continue
                for cc in col_rule_ids:
                    if int((rule_masks[cc].fillna(False) & row_any).sum()) < n_rows:
                        ok = False
                        break
                if not ok:
                    continue

                if not has_distinct_solution(df=df, rule_masks=rule_masks, row_rule_ids=row_rule_ids, col_rule_ids=col_rule_ids):
                    continue
            # Shuffle axis order so types don't always land at index 0.
            # The selection is unaffected; only display position changes.
            rng.shuffle(row_rule_ids)
            rng.shuffle(col_rule_ids)
            return Board(n_rows=n_rows, n_cols=n_cols, seed=seed, row_rule_ids=row_rule_ids, col_rule_ids=col_rule_ids)

    return None


# -----------------------------
# Validation / scoring
# -----------------------------

@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    reason: str  # "ok", "duplicate", "does_not_match", "unknown_pokemon"


def validate_pick(
    df: pd.DataFrame,
    rule_masks: Dict[str, pd.Series],
    board: Board,
    row_i: int,
    col_j: int,
    pokemon_key: str,
    used_keys: set[str],
) -> ValidationResult:
    if not pokemon_key:
        return ValidationResult(False, "unknown_pokemon")
    if pokemon_key in used_keys:
        return ValidationResult(False, "duplicate")

    rr = board.row_rule_ids[row_i]
    cc = board.col_rule_ids[col_j]
    mask = rule_masks[rr] & rule_masks[cc]

    # check membership by key
    if "key" not in df.columns:
        return ValidationResult(False, "unknown_pokemon")

    exists_and_matches = bool(((df["key"] == pokemon_key) & mask).any())
    if not exists_and_matches:
        return ValidationResult(False, "does_not_match")

    return ValidationResult(True, "ok")


def rarity_score_simple(df: pd.DataFrame, rule_masks: Dict[str, pd.Series], row_rule_id: str, col_rule_id: str) -> float:
    """
    Optional: smaller candidate set => "rarer" => higher score.
    Simple version: 1 / count.
    """
    cnt = candidate_count(rule_masks, row_rule_id, col_rule_id)
    return 0.0 if cnt <= 0 else 1.0 / float(cnt)