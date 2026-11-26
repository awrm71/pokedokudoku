/* ============================================================================
   category-colors.js — one colour and one small pixel icon for every rule.

   Used by the Cube (cube.js) to tint the face toward you and to draw rule
   badges. Plain script; exposes window.R81_CAT.

   How the colours are chosen
   • Colour is never the only cue: every badge also has an icon and its name
     (WCAG 1.4.1). Two rules may share a hue; they never share an icon + name.
   • Families, not 150 separate colours. People can keep about six to eight
     colours apart at a glance, so related rules share a family look:
       Types        the type colours players already know, solid badge.
       Moves        outlined in the colour of the move's own type, with a
                    disc icon, so "Learns Surf" reads as watery but is
                    clearly not the Water type.
       Regions      one invented colour each, taken from the place's feel
                    (Hoenn's sea, Sinnoh's snow, Alola's sunsets), pin icon.
       Stages       an ordered green ramp: Base light, Middle mid, Final
                    dark, because stages are a sequence.
       Evolve by    one hue per method with a matching icon (arrow up for
                    level, gem for item, swap arrows for trade, heart for
                    friendship).
       Status       gold for Legendary and purple for Mythical, the rarity
                    colours most games use; others get their own hue + icon.
       Abilities    one lavender family, outlined, spark icon.
   • These hues are this site's own, not taken from any game's palette.
   ============================================================================ */
(function () {
  "use strict";

  const TYPE = {
    normal: "#888888", fire: "#E84000", fighting: "#B81414", water: "#1B7FE8",
    ice: "#20CCDD", grass: "#2DAA4C", bug: "#7DA900", ground: "#C47A00",
    rock: "#A08045", flying: "#91B2F5", electric: "#FFD700", psychic: "#E8205A",
    fairy: "#E85FAA", poison: "#8B2BAE", ghost: "#5B3E7A", dragon: "#3425C8",
    dark: "#2E2B3A", steel: "#6A8FAD",
  };

  // What type each tracked move is.
  const MOVE_TYPE = {
    acrobatics: "flying", aerial_ace: "flying", air_slash: "flying", aura_sphere: "fighting",
    brick_break: "fighting", bug_buzz: "bug", bulk_up: "fighting", calm_mind: "psychic",
    close_combat: "fighting", crunch: "dark", dark_pulse: "dark", dazzling_gleam: "fairy",
    dragon_claw: "dragon", dragon_dance: "dragon", drain_punch: "fighting", earth_power: "ground",
    earthquake: "ground", energy_ball: "grass", extrasensory: "psychic", fire_blast: "fire",
    fire_punch: "fire", flamethrower: "fire", flash_cannon: "steel", fly: "flying",
    focus_blast: "fighting", giga_drain: "grass", grass_knot: "grass", heavy_slam: "steel",
    hex: "ghost", high_horsepower: "ground", hydro_pump: "water", ice_beam: "ice",
    ice_fang: "ice", ice_punch: "ice", icicle_crash: "ice", iron_head: "steel",
    iron_tail: "steel", knock_off: "dark", leech_life: "bug", liquidation: "water",
    megahorn: "bug", metronome: "normal", moonblast: "fairy", night_slash: "dark",
    outrage: "dragon", play_rough: "fairy", poison_jab: "poison", power_whip: "grass",
    protect: "normal", psychic: "psychic", psyshock: "psychic", razor_leaf: "grass",
    rock_blast: "rock", rock_slide: "rock", scald: "water", seed_bomb: "grass",
    shadow_ball: "ghost", shadow_claw: "ghost", sludge_bomb: "poison", sludge_wave: "poison",
    solar_beam: "grass", stone_edge: "rock", sucker_punch: "dark", superpower: "fighting",
    surf: "water", swords_dance: "normal", tail_slap: "normal", thunder: "electric",
    thunder_punch: "electric", thunderbolt: "electric", toxic_spikes: "poison", triple_axel: "ice",
    u_turn: "bug", waterfall: "water", wild_charge: "electric", will_o_wisp: "fire",
    x_scissor: "bug", zen_headbutt: "psychic",
  };

  const REGION = {
    kanto: "#d24b4b",   // the first region: a strong, simple red
    johto: "#c99a2e",   // old towns, bells and temples: aged gold
    hoenn: "#2b9c8f",   // mostly sea: teal
    sinnoh: "#7f8fd1",  // snowy mountains: cold periwinkle
    hisui: "#9a7b55",   // the same land long ago: parchment brown
    unova: "#5e6f8e",   // a big city region: slate blue
    kalos: "#cf6f98",   // cafés and fashion: rose
    alola: "#ec8a3c",   // tropical islands: sunset orange
    galar: "#4f9a5e",   // moors and fields: green
    paldea: "#b0457c",  // warm, open country: berry
  };

  // [colour, icon] for everything else.
  const RULE = {
    evo_start: ["#93d08a", "stage1"],
    evo_middle: ["#4fae6a", "stage2"],
    evo_final: ["#2f7d50", "stage3"],
    evo_none: ["#6fa3a6", "stop"],
    baby: ["#f0b0c4", "egg"],
    evo_premature: ["#f0b0c4", "egg"],
    evolves_by_levelup: ["#4aa3df", "up"],
    evolves_by_item: ["#e0a83a", "gem"],
    evolves_by_trade: ["#36b5a4", "swap"],
    evolves_by_friendship: ["#e7799f", "heart"],
    branched: ["#9a7fe0", "fork"],
    legendary: ["#e6b53d", "crown"],
    mythical: ["#a06ee0", "star"],
    ultra_beast: ["#3fc3d8", "rift"],
    paradox: ["#d153b8", "hourglass"],
    starter: ["#6cbf4a", "sprout"],
    fossil: ["#b08a5a", "shell"],
    mega: ["#e8663c", "mega"],
    gmax: ["#c4413d", "gmax"],
    regional_form: ["#5aa0c8", "compass"],
    single_type: ["#c9b48f", "one"],
    dual_type: ["#9fa9d6", "two"],
  };
  const ABILITY_COLOR = "#b48fd6";

  // 9×9 pixel icons, "#" = filled.
  const BITMAPS = {
    pin: ["...###...", "..#####..", ".###.###.", ".##...##.", ".###.###.", "..#####..", "...###...", "....#....", "....#...."],
    up: ["....#....", "...###...", "..#####..", ".###.###.", "###.#.###", "....#....", "....#....", "....#....", "....#...."],
    gem: [".........", "..#####..", ".#######.", "#########", ".#######.", "..#####..", "...###...", "....#....", "........."],
    swap: [".....#...", "########.", ".....#...", ".........", "...#.....", ".########", "...#.....", ".........", "........."],
    heart: [".........", ".##...##.", "#########", "#########", "#########", ".#######.", "..#####..", "...###...", "....#...."],
    fork: ["#...#...#", "#...#...#", ".#..#..#.", "..#.#.#..", "...###...", "....#....", "....#....", "....#....", "....#...."],
    crown: [".........", "#...#...#", "##.###.##", "#########", "#########", "#########", ".........", "#########", "........."],
    star: ["....#....", "....#....", "...###...", "#########", ".#######.", "..#####..", ".###.###.", ".##...##.", ".#.....#."],
    stage1: [".........", ".........", ".........", ".........", ".........", ".........", "###......", "###......", "###......"],
    stage2: [".........", ".........", ".........", "...###...", "...###...", "...###...", "######...", "######...", "######..."],
    stage3: ["......###", "......###", "......###", "...######", "...######", "...######", "#########", "#########", "#########"],
    stop: [".........", ".........", ".........", "#########", "#########", "#########", ".........", ".........", "........."],
    egg: ["...###...", "..#####..", ".#######.", ".#######.", "#########", "#########", "#########", ".#######.", "..#####.."],
    one: [".........", "...###...", "..#####..", ".#######.", ".#######.", ".#######.", "..#####..", "...###...", "........."],
    two: [".........", ".........", "###...###", "###...###", "###...###", ".........", ".........", ".........", "........."],
    disc: ["..#####..", ".##...##.", "##.....##", "#...#...#", "#..###..#", "#...#...#", "##.....##", ".##...##.", "..#####.."],
    spark: ["....#....", "....#....", ".#..#..#.", "..#.#.#..", "####.####", "..#.#.#..", ".#..#..#.", "....#....", "....#...."],
    shell: ["..#####..", ".#.....#.", "#..###..#", "#.#...#.#", "#.#.#.#.#", "#.#..##.#", "#..#....#", ".#..####.", "..#......"],
    mega: ["#.......#", "##.....##", "#.#...#.#", "#..#.#..#", "#...#...#", "#.......#", "#.......#", "#.......#", "#.......#"],
    gmax: ["....#....", "...#.#...", "..#...#..", ".#.....#.", "....#....", "...#.#...", "..#...#..", ".#.....#.", "#.......#"],
    rift: ["....#....", "...#.#...", "...#.#...", "..#...#..", "..#.#.#..", ".#..#..#.", ".#.....#.", "#.......#", "#########"],
    hourglass: ["#########", ".#.....#.", "..#...#..", "...#.#...", "....#....", "...#.#...", "..#...#..", ".#.....#.", "#########"],
    sprout: [".##...##.", "####.####", ".##.#.##.", "....#....", "....#....", "....#....", "..#####..", ".#######.", "........."],
    compass: ["....#....", "...###...", "....#....", ".#..#..#.", "#########", ".#..#..#.", "....#....", "...###...", "....#...."],
  };

  const iconUrl = {};
  for (const [name, rows] of Object.entries(BITMAPS)) {
    let r = "";
    rows.forEach((row, y) => [...row].forEach((ch, x) => { if (ch === "#") r += `<rect x='${x}' y='${y}' width='1' height='1'/>`; }));
    iconUrl[name] = `url("data:image/svg+xml,${encodeURIComponent(`<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 9 9' shape-rendering='crispEdges'>${r}</svg>`)}")`;
  }

  // Black or white text, whichever reads better on the colour.
  function ink(hex) {
    const n = parseInt(hex.slice(1), 16);
    const lin = (c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
    const L = 0.2126 * lin(n >> 16) + 0.7152 * lin((n >> 8) & 255) + 0.0722 * lin(n & 255);
    return L > 0.36 ? "#16151c" : "#ffffff";
  }

  /** {color, ink, icon (css url or null), typeIcon (type name or null), outline, family} */
  function style(rid) {
    rid = String(rid || "");
    if (rid.startsWith("type_")) {
      const t = rid.slice(5);
      const c = TYPE[t] || "#888888";
      return { color: c, ink: ink(c), icon: null, typeIcon: t, outline: false, family: "type" };
    }
    if (rid.startsWith("move_")) {
      const t = MOVE_TYPE[rid.slice(5)] || "normal";
      return { color: TYPE[t], ink: "#ece6dc", icon: iconUrl.disc, typeIcon: null, outline: true, family: "move" };
    }
    if (rid.startsWith("ability_")) {
      return { color: ABILITY_COLOR, ink: "#ece6dc", icon: iconUrl.spark, typeIcon: null, outline: true, family: "ability" };
    }
    if (rid.startsWith("region_")) {
      const c = REGION[rid.slice(7)] || "#7d8aa8";
      return { color: c, ink: ink(c), icon: iconUrl.pin, typeIcon: null, outline: false, family: "region" };
    }
    const r = RULE[rid];
    if (r) return { color: r[0], ink: ink(r[0]), icon: iconUrl[r[1]], typeIcon: null, outline: false, family: "trait" };
    return { color: "#7d8aa8", ink: "#ffffff", icon: null, typeIcon: null, outline: false, family: "other" };
  }

  /** A rule badge element: icon + name, coloured by family. */
  function badge(rule, extraClass) {
    const el = document.createElement("span");
    el.className = "rb rb-cat" + (extraClass ? ` ${extraClass}` : "");
    if (!rule) return el;
    const s = style(rule.id);
    el.style.setProperty("--rc", s.color);
    el.style.setProperty("--ri", s.ink);
    if (s.outline) el.classList.add("rb-outline");
    const ic = document.createElement("span");
    ic.setAttribute("aria-hidden", "true");
    if (s.typeIcon) {
      ic.className = `px-ti px-ti-${s.typeIcon}`;
    } else if (s.icon) {
      ic.className = "rb-ic";
      ic.style.setProperty("--ic", s.icon);
    }
    if (s.typeIcon || s.icon) el.appendChild(ic);
    el.appendChild(document.createTextNode(rule.label));
    return el;
  }

  window.R81_CAT = { style, badge };
})();
