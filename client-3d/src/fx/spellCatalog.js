/**
 * Spell / attack recipes for the 3D VTT.
 * Pure data — no Three.js — so Vitest can cover dispatch without WebGL.
 */

export const SPELL_KEYS = [
  'magic_missile',
  'firebolt',
  'chromatic_orb',
  'witch_bolt',
  'witch_bolt_sustain',
  'color_spray',
  'leomunds_tiny_hut',
  'message',
  'grease',
  'eldritch_blast',
  'misty_step',
  'guiding_bolt',
  'sacred_flame',
  'attack',
  'ray_of_frost',
  'armor_of_agathys',
  'shield_of_faith',
  'healing_word',
  'cure_wounds',
  'inflict_wounds',
  'toll_the_dead',
  'ice_knife',
  'burning_hands',
  'chill_touch',
  'poison_spray',
  'thunderwave',
  'spare_the_dying',
  'fireball',
  'sleep',
  'hold_person',
  'bane',
  'bless',
  'guidance',
  'bardic_inspiration',
  'detect_magic',
  'resistance',
  'second_wind',
  'action_surge',
  'rage',
  'end_rage',
  'stinking_cloud',
  'web',
  'mirror_image',
  'absorb_elements',
  'look',
  'blight',
  'invisibility',
  'greater_invisibility',
  'spirit_guardians',
  'revivify',
  'mass_healing_word',
  'call_lightning',
  'call_lightning_bolt',
  'sleet_storm',
];

export const ALIASES = {
  perception: 'look',
  chromaticorb: 'chromatic_orb',
  witchbolt: 'witch_bolt',
  witchboltsustain: 'witch_bolt_sustain',
  colorspray: 'color_spray',
  leomundstinyhut: 'leomunds_tiny_hut',
  eldritchblast: 'eldritch_blast',
  mistystep: 'misty_step',
  guidingbolt: 'guiding_bolt',
  sacredflame: 'sacred_flame',
  rayoffrost: 'ray_of_frost',
  armorofagathys: 'armor_of_agathys',
  shieldoffaith: 'shield_of_faith',
  healingword: 'healing_word',
  curewounds: 'cure_wounds',
  inflictwounds: 'inflict_wounds',
  tollthedead: 'toll_the_dead',
  iceknife: 'ice_knife',
  burninghands: 'burning_hands',
  chilltouch: 'chill_touch',
  poisonspray: 'poison_spray',
  sparethedying: 'spare_the_dying',
  holdperson: 'hold_person',
  bardicinspiration: 'bardic_inspiration',
  detectmagic: 'detect_magic',
  secondwind: 'second_wind',
  actionsurge: 'action_surge',
  endrage: 'end_rage',
  stinkingcloud: 'stinking_cloud',
  mirrorimage: 'mirror_image',
  absorbelements: 'absorb_elements',
  greaterinvisibility: 'greater_invisibility',
  spiritguardians: 'spirit_guardians',
  masshealingword: 'mass_healing_word',
  calllightning: 'call_lightning',
  calllightningbolt: 'call_lightning_bolt',
  sleetstorm: 'sleet_storm',
};

export const CHROMATIC_COLORS = {
  acid: 0x78ff78,
  cold: 0x96dcff,
  fire: 0xff8228,
  lightning: 0xc8ebff,
  poison: 0x91dc6e,
  thunder: 0xbeb4ff,
};

const RECIPES = {
  magic_missile: {
    kind: 'projectile', color: 0xc77dff, duration: 0.38, arc: 0.85, count: 3, stagger: 0.07,
    sfx: 'magic_missile_fire', impactSfx: 'magic_missile_impact',
  },
  firebolt: {
    kind: 'projectile', color: 0xff6a1a, duration: 0.4, arc: 0.35,
    sfx: 'firebolt_fire', impactSfx: 'firebolt_impact',
  },
  chromatic_orb: {
    kind: 'projectile', color: 0x78ff78, duration: 0.36, arc: 0.25,
  },
  witch_bolt: {
    kind: 'beam', color: 0xb266ff, duration: 0.55, sfx: 'ray_of_frost_fire',
  },
  witch_bolt_sustain: {
    kind: 'beam', color: 0x9b4dff, duration: 0.35,
  },
  color_spray: {
    kind: 'cone', color: 0xff66cc, duration: 0.55, radius: 1.4,
  },
  leomunds_tiny_hut: {
    kind: 'burst', color: 0x88c8ff, duration: 0.9, radius: 1.8, at: 'source',
  },
  message: {
    kind: 'projectile', color: 0xddeeff, duration: 0.32, arc: 0.55,
  },
  grease: {
    kind: 'area', color: 0x8a6a3a, duration: 0.7,
  },
  eldritch_blast: {
    kind: 'beam', color: 0xd8c8ff, duration: 0.42, sfx: 'ray_of_frost_fire',
  },
  misty_step: {
    kind: 'teleport', color: 0xb0e0ff, duration: 0.55,
  },
  guiding_bolt: {
    kind: 'projectile', color: 0xffe566, duration: 0.45, arc: 0.4,
    sfx: 'guiding_bolt_charge', impactSfx: 'guiding_bolt_impact',
  },
  sacred_flame: {
    kind: 'burst', color: 0xfff1a8, duration: 0.55, radius: 0.7, fromAbove: true,
    sfx: 'sacred_flame_start', impactSfx: 'sacred_flame_impact',
  },
  ray_of_frost: {
    kind: 'beam', color: 0x9ee8ff, duration: 0.45,
    sfx: 'ray_of_frost_fire', impactSfx: 'ray_of_frost_impact',
  },
  armor_of_agathys: {
    kind: 'aura', color: 0xa8e8ff, duration: 0.7, at: 'source',
  },
  shield_of_faith: {
    kind: 'aura', color: 0xffe27a, duration: 0.65, at: 'target',
    sfx: 'shield_of_faith_start',
  },
  healing_word: {
    kind: 'heal', color: 0x7cff9a, duration: 0.55, sfx: 'healing_word_cast',
  },
  cure_wounds: {
    kind: 'heal', color: 0x6ee88a, duration: 0.6, sfx: 'cure_wounds_cast',
  },
  inflict_wounds: {
    kind: 'melee', color: 0x8b1a1a, duration: 0.35,
    sfx: 'inflict_wounds_cast', impactSfx: 'inflict_wounds_impact',
  },
  toll_the_dead: {
    kind: 'burst', color: 0x6b5b8c, duration: 0.55, radius: 0.65,
  },
  ice_knife: {
    kind: 'combo',
    steps: [
      { kind: 'projectile', color: 0xa8e8ff, duration: 0.34, arc: 0.9, sfx: 'ice_knife_throw' },
      { kind: 'burst', color: 0xd0f4ff, duration: 0.45, radius: 1.1, sfx: 'ice_knife_shatter', useSquares: true },
    ],
  },
  burning_hands: {
    kind: 'cone', color: 0xff5a1f, duration: 0.55, radius: 1.5, sfx: 'burning_hands_cast',
  },
  chill_touch: {
    kind: 'projectile', color: 0x66ff99, duration: 0.4, arc: 0.15,
    sfx: 'chill_touch_cast', impactSfx: 'chill_touch_grab',
  },
  poison_spray: {
    kind: 'cone', color: 0x7dcc4a, duration: 0.5, radius: 1.1, sfx: 'poison_spray_cast',
  },
  thunderwave: {
    kind: 'area', color: 0xc8b8ff, duration: 0.6,
  },
  spare_the_dying: {
    kind: 'heal', color: 0xffe9a0, duration: 0.65, sfx: 'spare_the_dying_cast',
  },
  fireball: {
    kind: 'combo',
    steps: [
      { kind: 'projectile', color: 0xff7a1a, duration: 0.4, arc: 0.7, sfx: 'firebolt_fire' },
      { kind: 'burst', color: 0xff4d00, duration: 0.7, radius: 1.8, useSquares: true, impactSfx: 'firebolt_impact' },
    ],
  },
  sleep: {
    kind: 'area', color: 0x6a5acd, duration: 0.75,
  },
  hold_person: {
    kind: 'aura', color: 0xb48cff, duration: 0.7, at: 'target',
  },
  bane: {
    kind: 'aura', color: 0x8b2252, duration: 0.65, at: 'target', sfx: 'bane_cast',
  },
  bless: {
    kind: 'aura', color: 0xffd24a, duration: 0.7, at: 'target', sfx: 'bless_cast',
  },
  guidance: {
    kind: 'aura', color: 0xffe9a0, duration: 0.55, at: 'target', sfx: 'guidance_cast',
  },
  bardic_inspiration: {
    kind: 'aura', color: 0xffc14a, duration: 0.6, at: 'target',
  },
  detect_magic: {
    kind: 'look', color: 0x88aaff, duration: 0.7, at: 'source',
  },
  resistance: {
    kind: 'aura', color: 0x9ad4ff, duration: 0.55, at: 'target', sfx: 'resistance_cast',
  },
  second_wind: {
    kind: 'heal', color: 0x7cff9a, duration: 0.55, at: 'source',
  },
  action_surge: {
    kind: 'aura', color: 0xffdd66, duration: 0.5, at: 'source',
  },
  rage: {
    kind: 'aura', color: 0xff3b1f, duration: 0.7, at: 'source', sfx: 'rage_roar',
  },
  end_rage: {
    kind: 'aura', color: 0x886666, duration: 0.45, at: 'source', sfx: 'rage_calm',
  },
  stinking_cloud: {
    kind: 'area', color: 0x6a8f3a, duration: 0.8,
  },
  web: {
    kind: 'area', color: 0xe8e0d0, duration: 0.75,
  },
  mirror_image: {
    kind: 'aura', color: 0xc0e0ff, duration: 0.6, at: 'source',
  },
  absorb_elements: {
    kind: 'aura', color: 0x66ddaa, duration: 0.5, at: 'source',
  },
  look: {
    kind: 'look', color: 0xf4d35e, duration: 0.55, at: 'source',
  },
  blight: {
    kind: 'beam', color: 0x4a7a3a, duration: 0.5,
  },
  invisibility: {
    kind: 'aura', color: 0xc8e6ff, duration: 0.55, at: 'target',
  },
  greater_invisibility: {
    kind: 'aura', color: 0xe6f0ff, duration: 0.6, at: 'target',
  },
  spirit_guardians: {
    kind: 'combo',
    steps: [
      { kind: 'aura', color: 0xffe08a, duration: 0.7, at: 'source', sfx: 'spirit_guardians_cast' },
      { kind: 'burst', color: 0xfff1c0, duration: 0.9, radius: 3.2, at: 'source' },
    ],
  },
  revivify: {
    kind: 'combo',
    steps: [
      { kind: 'burst', color: 0xd8f0ff, duration: 0.35, radius: 0.4, sfx: 'revivify_cast' },
      { kind: 'heal', color: 0xfff1c0, duration: 0.8 },
    ],
  },
  mass_healing_word: {
    kind: 'heal', color: 0x8cffb4, duration: 0.6, allTargets: true, sfx: 'healing_word_cast',
  },
  call_lightning: {
    kind: 'burst', color: 0xd8ecff, duration: 0.6, radius: 1.1, fromAbove: true,
    sfx: 'lightning_strike',
  },
  call_lightning_bolt: {
    kind: 'burst', color: 0xd8ecff, duration: 0.55, radius: 1.1, fromAbove: true,
    sfx: 'lightning_strike',
  },
  sleet_storm: {
    kind: 'area', color: 0xbcd8ee, duration: 0.9, radius: 4, sfx: 'sleet_storm_cast',
  },
};

export function normalizeSpellKey(name) {
  const raw = String(name || '')
    .toLowerCase()
    .trim()
    .replace(/['’`]/g, '')
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_|_$/g, '');
  if (!raw) return '';
  if (RECIPES[raw]) return raw;
  const compact = raw.replace(/_/g, '');
  return ALIASES[compact] || ALIASES[raw] || raw;
}

export function gridPos(value) {
  if (value == null) return null;
  if (Array.isArray(value) && value.length >= 2 && Number.isFinite(Number(value[0])) && Number.isFinite(Number(value[1]))) {
    return [Number(value[0]), Number(value[1])];
  }
  if (typeof value === 'object') {
    if (Array.isArray(value.pos) && value.pos.length >= 2) {
      return [Number(value.pos[0]), Number(value.pos[1])];
    }
    if (Number.isFinite(Number(value.x)) && Number.isFinite(Number(value.y))) {
      return [Number(value.x), Number(value.y)];
    }
  }
  return null;
}

export function attackStyleFromPayload(payload = {}) {
  const label = String(payload.label || '').toLowerCase();
  const weaponMatch = /attack with\s+([^\->\n]+)/i.exec(label);
  const weapon = (weaponMatch && weaponMatch[1] ? weaponMatch[1] : label).toLowerCase();
  const rangedFlag = Boolean(payload.ranged);
  if (/\b(shortbow|longbow|bow)\b/.test(weapon)) return 'ranged_arrow';
  if (/\bcrossbow\b/.test(weapon)) return 'ranged_bolt';
  if (/\b(javelin|dart|handaxe|throw|thrown)\b/.test(weapon)) return 'ranged_thrown';
  if (/\bsling\b/.test(weapon)) return 'ranged_generic';
  if (/\b(bite|beak)\b/.test(weapon)) return 'natural_bite';
  if (/\b(claw|talon)\b/.test(weapon)) return 'natural_claw';
  if (/\b(unarmed|punch|fist|kick)\b/.test(weapon)) return 'melee_blunt';
  if (/\b(axe|greataxe|battleaxe)\b/.test(weapon)) return 'melee_chop';
  if (/\b(mace|club|hammer|maul|staff)\b/.test(weapon)) return 'melee_blunt';
  if (/\b(rapier|spear|pike|lance|trident|dagger)\b/.test(weapon)) return 'melee_thrust';
  if (/\b(sword|scimitar|sabre|saber|katana|greatsword|longsword|shortsword)\b/.test(weapon)) return 'melee_slash';
  return rangedFlag ? 'ranged_generic' : 'melee_generic';
}

export function sfxForAttackStyle(style) {
  if (style === 'ranged_arrow') return 'attack_arrow';
  if (style === 'ranged_bolt') return 'attack_bolt';
  if (style === 'ranged_thrown') return 'attack_thrown';
  if (style === 'melee_chop' || style === 'melee_slash') return 'attack_slash';
  if (style === 'melee_blunt') return 'attack_blunt';
  if (style === 'melee_thrust') return 'attack_thrust';
  if (style === 'natural_bite') return 'attack_bite';
  if (style === 'natural_claw') return 'attack_claw';
  return String(style || '').startsWith('ranged') ? 'attack_generic_ranged' : 'attack_generic_melee';
}

function cloneRecipe(recipe) {
  if (!recipe) return null;
  const copy = { ...recipe };
  if (Array.isArray(recipe.steps)) {
    copy.steps = recipe.steps.map((step) => ({ ...step }));
  }
  return copy;
}

function fallbackRecipe(payload = {}) {
  if (Array.isArray(payload.target_squares) && payload.target_squares.length) {
    return { kind: 'area', color: 0xe63946, duration: 0.55 };
  }
  if (gridPos(payload.source_pos) && gridPos(payload.target_pos)) {
    return { kind: 'projectile', color: 0xffdd57, duration: 0.4, arc: 0.35 };
  }
  return { kind: 'burst', color: 0xc9a227, duration: 0.4, radius: 0.55 };
}

function attackRecipe(payload) {
  const style = attackStyleFromPayload(payload);
  const ranged = String(style).startsWith('ranged');
  return {
    kind: ranged ? 'projectile' : 'melee',
    color: ranged ? 0xe8e0d0 : 0xc9c4b8,
    duration: ranged ? 0.42 : 0.26,
    arc: ranged ? 1.15 : 0.12,
    sfx: sfxForAttackStyle(style),
    attackStyle: style,
  };
}

export function recipeFor(name, payload = {}) {
  const key = normalizeSpellKey(name || payload.spell || payload.label);
  if (key === 'attack') return attackRecipe(payload);
  const base = cloneRecipe(RECIPES[key]) || fallbackRecipe(payload);
  if (key === 'chromatic_orb') {
    const typeKey = String(payload.damage_type || payload.spell?.damage_type || 'acid').toLowerCase();
    base.color = CHROMATIC_COLORS[typeKey] || CHROMATIC_COLORS.acid;
    base.damageType = typeKey;
  }
  if (key === 'magic_missile' && Number(payload.count) > 0) {
    base.count = Number(payload.count);
  }
  base.key = key || 'unknown';
  return base;
}

export function recipeDuration(recipe) {
  if (!recipe) return 0;
  if (recipe.kind === 'combo' && Array.isArray(recipe.steps)) {
    return recipe.steps.reduce((sum, step) => sum + (Number(step.duration) || 0), 0);
  }
  const n = Math.max(1, Number(recipe.count) || 1);
  const stagger = Number(recipe.stagger) || 0;
  return (Number(recipe.duration) || 0) + stagger * (n - 1);
}
