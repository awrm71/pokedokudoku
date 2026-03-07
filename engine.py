from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple, Any
import random
import pandas as pd


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
    n: int
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


def build_name_index(df: pd.DataFrame) -> pd.DataFrame:
    """
    Returns: columns [key, display_name, norm]
    """
    idx = df[["key", "display_name"]].copy()
    idx["norm"] = idx["display_name"].astype(str).map(normalize_name)
    return idx


def resolve_input_to_key(user_text: str, name_index: pd.DataFrame) -> Optional[str]:
    """
    Resolves free-text input to a pokemon key if there is an exact normalized match.
    """
    t = normalize_name(user_text)
    if not t:
        return None
    matches = name_index[name_index["norm"] == t]
    if matches.empty:
        return None
    # if collisions ever exist, just take first
    return str(matches.iloc[0]["key"])


def suggest_names_global(prefix: str, name_index: pd.DataFrame, limit: int = 10) -> List[Tuple[str, str]]:
    """
    Global autocomplete suggestions (key, display_name) by prefix.
    Not filtered by row/col constraints (so it doesn't spoil).
    """
    p = normalize_name(prefix)
    if len(p) < 2:
        return []
    m = name_index[name_index["norm"].str.startswith(p)].head(limit)
    return list(zip(m["key"].tolist(), m["display_name"].tolist()))


# -----------------------------
# Answer policy (forms/variants)
# -----------------------------

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

    # species_only (default): if you later add columns like is_form / form_name, filter here.
    # For now, no-op.
    return df.copy()


# -----------------------------
# Rules library
# -----------------------------

def build_default_rules(df: pd.DataFrame) -> Dict[str, Rule]:
    """
    Builds a rule dictionary using columns you already have.
    """
    rules: Dict[str, Rule] = {}

    # Types
    types = sorted(set(df["type1"]).union(set(df["type2"])) - {""})
    for t in types:
        tid = f"type_{t}"
        label = f"Type: {t.title()}"
        rules[tid] = Rule(
            rule_id=tid,
            label=label,
            fn=lambda d, t=t: (d["type1"] == t) | (d["type2"] == t),
        )

    # Generations
    gens = sorted([int(x) for x in df["gen"].dropna().unique().tolist()])
    for g in gens:
        rid = f"gen_{g}"
        rules[rid] = Rule(
            rule_id=rid,
            label=f"Gen: {g}",
            fn=lambda d, g=g: d["gen"] == g,
        )

    # Flags
    # (these assume columns exist; if not, you can remove them or add guards)
    rules["legendary"] = Rule("legendary", "Legendary", lambda d: d["is_legendary"] == True)
    rules["mythical"] = Rule("mythical", "Mythical", lambda d: d["is_mythical"] == True)
    rules["baby"] = Rule("baby", "Baby", lambda d: d["is_baby"] == True)
    rules["not_legendary"] = Rule("not_legendary", "Not Legendary", lambda d: d["is_legendary"] == False)
    rules["single_type"] = Rule("single_type", "Single-type", lambda d: d["type2"].fillna("") == "")
    rules["dual_type"] = Rule("dual_type", "Dual-type", lambda d: d["type2"].fillna("") != "")

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


def generate_board(
    df: pd.DataFrame,
    rules: Dict[str, Rule],
    rule_masks: Dict[str, pd.Series],
    seed: str,
    n: int,
    constraints: BoardConstraints = BoardConstraints(),
) -> Optional[Board]:
    """
    Randomly samples row/col rules until all intersections meet constraints.
    Deterministic given seed.
    """
    rng = random.Random(seed)
    rule_ids = list(rules.keys())

    if len(rule_ids) < n:
        return None

    for _ in range(constraints.max_tries):
        row_rule_ids = rng.sample(rule_ids, k=n)
        col_rule_ids = rng.sample(rule_ids, k=n)

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
            return Board(n=n, seed=seed, row_rule_ids=row_rule_ids, col_rule_ids=col_rule_ids)

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