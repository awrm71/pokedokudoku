/* ============================================================================
   category_help.js — plain-language explainers for every board category.
   ----------------------------------------------------------------------------
   Powers the PokeDoku-style "what does this category mean?" context lines in
   the guess modal (and anywhere else a category needs explaining). Pure data +
   one lookup function; no DOM, no dependencies. Load it before reverse.js.

   Usage:
     const help = window.PDD_CATEGORY_HELP.explain("Evolved by Item");
     // -> "Must be the Pokémon produced by evolving with a usable or held item."

   The lookup is tolerant: it matches the compacted player-facing label
   (what compact_rule_label emits), case-insensitively, and falls back to
   prefix rules for the open-ended families (Type / Learns / Ability / region).
   ============================================================================ */
(function () {
  "use strict";

  // The 18 types, so "Fire" / "Water" etc. can be recognized as type categories.
  const TYPES = new Set([
    "normal","fire","water","electric","grass","ice","fighting","poison","ground",
    "flying","psychic","bug","rock","ghost","dragon","dark","steel","fairy",
  ]);

  // The 10 regions (post-consolidation).
  const REGIONS = new Set([
    "kanto","johto","hoenn","sinnoh","unova","kalos","alola","galar","hisui","paldea",
  ]);

  // Exact-match explainers, keyed by the lowercased player-facing label.
  // Wording mirrors PokeDoku's concise, plain style and our INCOMING evolution
  // semantics ("Evolved by" = the Pokémon that results from the evolution).
  const EXACT = {
    // Evolution method (incoming)
    "evolved by level up":
      "Must be a Pokémon that evolves from its previous stage by leveling up.",
    "evolved by trade":
      "Must be a Pokémon obtained by trading its previous stage.",
    "evolved by friendship":
      "Must be a Pokémon that evolves from its previous stage through high friendship.",
    "evolved by item":
      "Must be a Pokémon produced by evolving with a usable or held item (an evolution stone, or an item held during trade/level-up).",

    // Evolution position / stage
    "base stage":
      "Must be the first stage in its evolution line (what everything else evolves from).",
    "middle stage":
      "Must be a middle stage — it evolves from something and into something else.",
    "mid stage":
      "Must be a middle stage — it evolves from something and into something else.",
    "final form":
      "Must be the last stage in its evolution line (it doesn't evolve further).",
    "doesn't evolve":
      "Must be a Pokémon with no evolutions at all — it neither evolves nor is evolved from.",
    "branched evolution":
      "Must belong to an evolution line that splits into more than one possible evolution (like Eevee).",

    // Category flags
    "baby pokemon":
      "Must be a Baby Pokémon — the pre-evolution introduced in a later generation (like Pichu).",
    "fossil pokemon":
      "Must be a Pokémon revived from a fossil.",
    "mega evolution":
      "Must be a Mega Evolution form.",
    "gigantamax":
      "Must be a Gigantamax form.",
    "starter pokemon":
      "Must be a first-partner Starter Pokémon or one of its evolutions.",
    "paradox pokemon":
      "Must be a Paradox Pokémon (an ancient or future form).",
    "ultra beast":
      "Must be an Ultra Beast.",
    "regional form":
      "Must be a regional variant (Alolan, Galarian, Hisuian, or Paldean).",
    "legendary":
      "Must be a Legendary Pokémon.",
    "mythical":
      "Must be a Mythical Pokémon.",

    // Typing
    "single type":
      "Must have exactly one type.",
    "dual type":
      "Must have two types.",
  };

  function explain(label) {
    if (!label) return "";
    const key = String(label).trim().toLowerCase();

    if (EXACT[key]) return EXACT[key];

    // Type category, e.g. "Fire"
    if (TYPES.has(key)) {
      return `Must have ${label} as one of its types.`;
    }
    // Region category, e.g. "Alola"
    if (REGIONS.has(key)) {
      return `Must originate from the ${label} region (introduced there).`;
    }
    // Move category, e.g. "Learns Knock Off"
    if (key.startsWith("learns ")) {
      const move = label.slice("Learns ".length);
      return `Must be able to learn ${move}.`;
    }
    // Ability category, e.g. "Ability: Levitate"
    if (key.startsWith("ability:")) {
      const ability = label.split(":").slice(1).join(":").trim();
      return `Must be able to have the ability ${ability}.`;
    }
    // Generation, e.g. "Gen 1"
    if (/^gen\s*\d+/i.test(key)) {
      return `Must have been introduced in ${label}.`;
    }

    // Unknown / no explainer — return empty so the UI can simply omit it.
    return "";
  }

  window.PDD_CATEGORY_HELP = { explain };
})();