import { describe, expect, it } from 'vitest';
import * as THREE from 'three';
import { addFixture } from './fixtures.js';

describe('thorns fixture (Spike Growth)', () => {
  const cellSize = 1;

  function build(obj) {
    const scene = new THREE.Scene();
    const group = addFixture(scene, { type: 'spike_growth_tile', name: 'Spike Growth', ...obj }, {}, cellSize);
    return group;
  }

  it('renders a fixture group for spike growth squares', () => {
    const group = build({ x: 3, y: 4 });
    expect(group).not.toBeNull();
    expect(group.userData.fixture.type).toBe('spike_growth_tile');
    const meshes = group.children.filter((c) => c.isMesh);
    // ground-cover disc + scattered spikes
    expect(meshes.length).toBeGreaterThan(5);
    expect(meshes.some((m) => m.geometry.type === 'ConeGeometry')).toBe(true);
  });

  it('is deterministic per cell and varied across cells', () => {
    const a = build({ x: 3, y: 4 });
    const b = build({ x: 3, y: 4 });
    const c = build({ x: 4, y: 4 });
    const pos = (g) => g.children.map((m) => [m.position.x, m.position.y, m.position.z]);
    expect(pos(a)).toEqual(pos(b));
    expect(pos(a)).not.toEqual(pos(c));
  });

  it('keeps spikes within the cell footprint', () => {
    const group = build({ x: 1, y: 7 });
    for (const child of group.children) {
      if (!child.isMesh) continue;
      expect(Math.abs(child.position.x)).toBeLessThanOrEqual(0.5);
      expect(Math.abs(child.position.z)).toBeLessThanOrEqual(0.5);
    }
  });
});
