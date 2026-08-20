import { describe, expect, it } from 'vitest';
import { createMapFog, fogVolumeParams } from './mapFog.js';

describe('fogVolumeParams', () => {
  it('uses a default volume height when YAML height is ground-hugging', () => {
    const params = fogVolumeParams({ height: 0, opacity: 0.5, density: 0.1 });
    expect(params.top).toBeCloseTo(1.75);
    expect(params.extinction).toBeGreaterThan(0.05);
    expect(params.extinction).toBeLessThan(0.6);
  });

  it('honors an explicit volume height', () => {
    expect(fogVolumeParams({ height: 3.2 }).top).toBeCloseTo(3.2);
  });
});

describe('createMapFog', () => {
  it('builds a compositor instead of stacked planes', () => {
    const fog = createMapFog(
      { map: { size: [4, 3] } },
      { opacity: 0.5, mask: true, mask_layers: [{ type: 'rectangle', position: [0, 0], size: [2, 2] }] },
    );
    expect(fog.masked).toBe(true);
    expect(typeof fog.composite).toBe('function');
    fog.dispose();
  });
});
