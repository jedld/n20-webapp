import { describe, expect, it } from 'vitest';
import {
  ALIASES,
  CHROMATIC_COLORS,
  SPELL_KEYS,
  attackStyleFromPayload,
  gridPos,
  normalizeSpellKey,
  recipeDuration,
  recipeFor,
  sfxForAttackStyle,
} from './spellCatalog.js';
import { aurasForEffects, normalizeEffectKey } from './statusAuras.js';

describe('spellCatalog', () => {
  it('normalizes spaces, apostrophes, and aliases to underscore keys', () => {
    expect(normalizeSpellKey('chromatic orb')).toBe('chromatic_orb');
    expect(normalizeSpellKey("Leomund's Tiny Hut")).toBe('leomunds_tiny_hut');
    expect(normalizeSpellKey('witch bolt')).toBe('witch_bolt');
    expect(normalizeSpellKey('perception')).toBe('look');
    expect(normalizeSpellKey('greater invisibility')).toBe('greater_invisibility');
    expect(normalizeSpellKey('hold person')).toBe('hold_person');
  });

  it('covers every 2D-registered spell key with a recipe kind', () => {
    for (const key of SPELL_KEYS) {
      const recipe = recipeFor(key);
      expect(recipe.kind, key).toBeTruthy();
      expect(recipeDuration(recipe), key).toBeGreaterThan(0);
    }
  });

  it('maps cone and cube targeting kinds', () => {
    expect(recipeFor('burning_hands').kind).toBe('cone');
    expect(recipeFor('color_spray').kind).toBe('cone');
    expect(recipeFor('poison_spray').kind).toBe('cone');
    expect(recipeFor('thunderwave').kind).toBe('area');
    expect(recipeFor('fireball').kind).toBe('combo');
  });

  it('covers the level 3 SRD spells and their persistent auras', () => {
    expect(normalizeSpellKey('Spirit Guardians')).toBe('spirit_guardians');
    expect(normalizeSpellKey('mass healing word')).toBe('mass_healing_word');
    expect(recipeFor('mass_healing_word').allTargets).toBe(true);
    expect(recipeFor('call_lightning_bolt').fromAbove).toBe(true);
    expect(recipeFor('sleet_storm').kind).toBe('area');
    expect(aurasForEffects(['spirit_guardians', 'call_lightning']).map((a) => a.key))
      .toEqual(['spirit_guardians', 'call_lightning']);
  });

  it('covers the ranger and wizard utility spells', () => {
    expect(normalizeSpellKey('Hail of Thorns')).toBe('hail_of_thorns');
    expect(normalizeSpellKey('Pass without Trace')).toBe('pass_without_trace');
    expect(normalizeSpellKey('zephyr strike')).toBe('zephyr_strike');
    expect(recipeFor('spike_growth').kind).toBe('area');
    expect(recipeFor('silence').kind).toBe('area');
    expect(recipeFor('fear').kind).toBe('area');
    expect(recipeFor('ensnaring_strike').at).toBe('target');
  });

  it('picks chromatic orb color from damage_type', () => {
    expect(recipeFor('chromatic_orb', { damage_type: 'fire' }).color).toBe(CHROMATIC_COLORS.fire);
    expect(recipeFor('chromatic orb', { damage_type: 'cold' }).color).toBe(CHROMATIC_COLORS.cold);
  });

  it('classifies weapon attacks like the 2D client', () => {
    expect(attackStyleFromPayload({ label: 'attack with longbow' })).toBe('ranged_arrow');
    expect(attackStyleFromPayload({ label: 'attack with light crossbow' })).toBe('ranged_bolt');
    expect(attackStyleFromPayload({ label: 'attack with longsword' })).toBe('melee_slash');
    expect(attackStyleFromPayload({ ranged: true })).toBe('ranged_generic');
    expect(recipeFor('attack', { label: 'attack with longbow' }).kind).toBe('projectile');
    expect(recipeFor('attack', { label: 'attack with mace' }).kind).toBe('melee');
    expect(sfxForAttackStyle('ranged_arrow')).toBe('attack_arrow');
  });

  it('falls back to area glow when the spell is unknown but has squares', () => {
    const recipe = recipeFor('unknown_blast', { target_squares: [[1, 2], [2, 2]] });
    expect(recipe.kind).toBe('area');
  });

  it('parses grid positions from arrays and objects', () => {
    expect(gridPos([3, 4])).toEqual([3, 4]);
    expect(gridPos({ x: 1, y: 8 })).toEqual([1, 8]);
    expect(gridPos({ pos: [5, 2] })).toEqual([5, 2]);
    expect(gridPos('entity-uid')).toBeNull();
  });

  it('keeps compact alias table pointing at real recipes', () => {
    for (const [alias, key] of Object.entries(ALIASES)) {
      expect(SPELL_KEYS.includes(key), `${alias} -> ${key}`).toBe(true);
    }
  });
});

describe('statusAuras', () => {
  it('normalizes synonyms used by the 2D overlay', () => {
    expect(normalizeEffectKey('Blessed')).toBe('bless');
    expect(normalizeEffectKey('shield of faith')).toBe('shield');
    expect(normalizeEffectKey('greater invisibility')).toBe('invisibility');
    expect(normalizeEffectKey('paralyzed')).toBe('hold_person');
  });

  it('returns unique aura styles for known effects only', () => {
    const auras = aurasForEffects(['bless', 'Blessed', 'prone', 'mage armor']);
    expect(auras.map((a) => a.key)).toEqual(['bless', 'mage_armor']);
    expect(auras[0].color).toBeTruthy();
  });
});
