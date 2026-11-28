/**
 * Persistent status auras drawn on 3D tokens.
 * Mirrors webapp/static/status_effects.js keys (colors only — no DOM/CSS).
 */

export const AURA_STYLES = {
  bless: { color: 0xffd24a, opacity: 0.55, ring: true },
  shield: { color: 0x409cff, opacity: 0.5, ring: true },
  mage_armor: { color: 0x78beff, opacity: 0.4, ring: true },
  armor_of_agathys: { color: 0xa8e8ff, opacity: 0.55, ring: true },
  bane: { color: 0x8b2252, opacity: 0.5, ring: true },
  hold_person: { color: 0xb48cff, opacity: 0.5, ring: true },
  sleep: { color: 0x6a5acd, opacity: 0.4, ring: true },
  mirror_image: { color: 0xc0e0ff, opacity: 0.45, ring: true },
  absorb_elements: { color: 0x66ddaa, opacity: 0.45, ring: true },
  web: { color: 0xe8e0d0, opacity: 0.35, ring: true },
  resistance: { color: 0x9ad4ff, opacity: 0.45, ring: true },
  shell_defense: { color: 0xc4a574, opacity: 0.45, ring: true },
  in_shell: { color: 0xc4a574, opacity: 0.45, ring: true },
  light: { color: 0xfff4d6, opacity: 0.5, ring: true },
  guiding_bolt: { color: 0xffe08a, opacity: 0.55, ring: true },
  invisibility: { color: 0xc8e6ff, opacity: 0.28, fadeSprite: 0.38 },
  spirit_guardians: { color: 0xffe08a, opacity: 0.5, ring: true },
  call_lightning: { color: 0x9fb8ff, opacity: 0.45, ring: true },
  beacon_of_hope: { color: 0xffecaa, opacity: 0.45, ring: true },
  aura_of_vitality: { color: 0x8cffaa, opacity: 0.45, ring: true },
  sacred_weapon: { color: 0xfff5d2, opacity: 0.45, ring: true },
  faerie_fire: { color: 0xbe8cff, opacity: 0.5, ring: true },
  sanctuary: { color: 0xcde6ff, opacity: 0.4, ring: true },
  gaseous_form: { color: 0xcdcdd7, opacity: 0.35, fadeSprite: 0.55 },
  ray_of_sickness: { color: 0x96dc50, opacity: 0.45, ring: true },
  acid_arrow: { color: 0xafff46, opacity: 0.45, ring: true },
  burning: { color: 0xff8c28, opacity: 0.55, ring: true },
  ensnaring_strike: { color: 0x6ebe46, opacity: 0.5, ring: true },
  magic_weapon: { color: 0xaad2ff, opacity: 0.4, ring: true },
  pass_without_trace: { color: 0x788296, opacity: 0.35, ring: true },
  silence_ward: { color: 0xa0aae6, opacity: 0.4, ring: true },
  fear: { color: 0x823ca0, opacity: 0.5, ring: true },
  zephyr_strike: { color: 0xc8f0ff, opacity: 0.4, ring: true },
  disguise_self: { color: 0xe6beff, opacity: 0.35, ring: true },
  hunters_mark: { color: 0xc83c3c, opacity: 0.45, ring: true },
  investiture_of_flame: { color: 0xff7a1e, opacity: 0.55, ring: true },
};

const SYNONYMS = {
  blessed: 'bless',
  paralyzed: 'hold_person',
  hold_person: 'hold_person',
  asleep: 'sleep',
  sleeping: 'sleep',
  shield_of_faith: 'shield',
  shieldoffaith: 'shield',
  mage_armor: 'mage_armor',
  magearmour: 'mage_armor',
  magearmor: 'mage_armor',
  restrained: 'web',
  webbed: 'web',
  invisible: 'invisibility',
  greater_invisibility: 'invisibility',
  greaterinvisibility: 'invisibility',
  light: 'light',
  guiding_bolt: 'guiding_bolt',
  guidingbolt: 'guiding_bolt',
};

export function normalizeEffectKey(name) {
  if (!name) return '';
  let k = String(name).toLowerCase().trim();
  k = k.replace(/^[#\[]?([a-z0-9_\-\s]+)[\]]?$/, '$1');
  k = k.replace(/['’`]/g, '').replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '');
  if (SYNONYMS[k]) return SYNONYMS[k];
  const compact = k.replace(/_/g, '');
  return SYNONYMS[compact] || k;
}

export function aurasForEffects(effects) {
  const seen = new Set();
  const auras = [];
  for (const raw of effects || []) {
    const key = normalizeEffectKey(raw);
    if (!key || seen.has(key)) continue;
    const style = AURA_STYLES[key];
    if (!style) continue;
    seen.add(key);
    auras.push({ key, ...style });
  }
  return auras;
}

/**
 * Ground rings for creature-centered emanations (Investiture of Flame's
 * 5-ft flames, Spirit Guardians, paladin auras). Unlike the small status
 * rings above these are sized to the aura's real radius from the server
 * payload (``entity.creature_auras`` from /map_state, see
 * natural20/utils/creature_auras.py).
 */
export const CREATURE_AURA_STYLES = {
  investiture_of_flame: { color: 0xff7a1e, opacity: 0.5 },
  spirit_guardians: { color: 0x9ee6b4, opacity: 0.4 },
  aura_of_protection: { color: 0xffd24a, opacity: 0.3 },
  aura_of_courage: { color: 0xffe9a8, opacity: 0.3 },
  aura_of_devotion: { color: 0xd8e8ff, opacity: 0.3 },
};

export function creatureAuraRings(entity, { feetPerGrid = 5 } = {}) {
  const rings = [];
  const fpg = Number(feetPerGrid) > 0 ? Number(feetPerGrid) : 5;
  for (const aura of entity?.creature_auras || []) {
    const radiusCells = Number(aura?.radius_ft) / fpg;
    if (!(radiusCells > 0)) continue;
    const style = CREATURE_AURA_STYLES[normalizeEffectKey(aura?.name)]
      || { color: 0xc8cde6, opacity: 0.28 };
    rings.push({ name: String(aura.name), radiusCells, ...style });
  }
  return rings;
}
