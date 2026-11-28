import { describe, it, expect } from 'vitest';
import { creatureAuraRings, aurasForEffects, normalizeEffectKey } from './statusAuras.js';

describe('creatureAuraRings', () => {
  it('sizes emanation footprints from the server payload', () => {
    const entity = {
      creature_auras: [
        { name: 'investiture_of_flame', radius_ft: 5, radius_tiles: 1 },
        { name: 'spirit_guardians', radius_ft: 15, radius_tiles: 3 },
      ],
    };
    const rings = creatureAuraRings(entity, { feetPerGrid: 5 });
    expect(rings).toHaveLength(2);
    expect(rings[0]).toMatchObject({ name: 'investiture_of_flame', radiusCells: 1 });
    expect(rings[0].color).toBe(0xff7a1e);
    expect(rings[1]).toMatchObject({ name: 'spirit_guardians', radiusCells: 3 });
  });

  it('falls back to a neutral style and defaults bad feetPerGrid', () => {
    const rings = creatureAuraRings(
      { creature_auras: [{ name: 'aura_of_warding', radius_ft: 10 }] },
      { feetPerGrid: 0 },
    );
    expect(rings).toHaveLength(1);
    expect(rings[0].radiusCells).toBe(2);
    expect(rings[0].color).toBe(0xc8cde6);
  });

  it('is empty without the payload or with junk entries', () => {
    expect(creatureAuraRings({})).toEqual([]);
    expect(creatureAuraRings({ creature_auras: [{ name: 'x', radius_ft: 0 }] })).toEqual([]);
    expect(creatureAuraRings({ creature_auras: [{ name: 'y', radius_ft: -5 }] })).toEqual([]);
  });
});

describe('status aura styles', () => {
  it('investiture_of_flame renders a status ring', () => {
    const auras = aurasForEffects(['investiture_of_flame']);
    expect(auras).toHaveLength(1);
    expect(auras[0]).toMatchObject({ key: 'investiture_of_flame', ring: true });
    expect(normalizeEffectKey('investiture_of_flame')).toBe('investiture_of_flame');
  });
});
