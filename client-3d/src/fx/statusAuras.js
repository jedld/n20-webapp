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
