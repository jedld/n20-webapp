import { describe, expect, it } from 'vitest';
import { CELL_SIZE, WALL_HEIGHT } from '../geometry.js';
import {
  caveSpeleothems,
  displaceCavePoint,
  exposedCaveFaces,
  occupancyFromGeometry,
  rockDensity,
  solidCaveCaps,
  solidCellSet,
} from './caveForm.js';

describe('cave forms', () => {
  it('exposes four faces and a cap for a lone solid cell', () => {
    const faces = exposedCaveFaces([[3, 4]], []);
    expect(faces).toHaveLength(4);
    expect(new Set(faces.map((f) => f.edge))).toEqual(new Set(['n', 's', 'e', 'w']));
    expect(solidCaveCaps([[3, 4]])).toHaveLength(1);
  });

  it('omits the shared face between adjacent solid cells', () => {
    const faces = exposedCaveFaces([[1, 1], [2, 1]], []);
    expect(faces).toHaveLength(6);
    const keys = faces.map((f) => `${f.gridX}:${f.edge}`);
    expect(keys).not.toContain('1:e');
    expect(keys).not.toContain('2:w');
  });

  it('does not double-draw a solid face that matches an edge wall', () => {
    const faces = exposedCaveFaces([[2, 2]], [{ x: 2, y: 3, edges: ['n'] }]);
    const south = faces.filter((f) => f.edge === 's' || (f.gridX === 2 && f.gridY === 3 && f.edge === 'n'));
    expect(south).toHaveLength(1);
  });

  it('displaces a world point deterministically and uniquely', () => {
    const occ = occupancyFromGeometry([[5, 5]], []);
    const a = displaceCavePoint(5.5, 0.8, 5.0, occ, { outward: { x: 0, z: -1 } });
    const b = displaceCavePoint(5.5, 0.8, 5.0, occ, { outward: { x: 0, z: -1 } });
    expect(a).toEqual(b);
    const c = displaceCavePoint(6.5, 0.8, 5.0, occ, { outward: { x: 0, z: -1 } });
    expect(Math.abs(a.x - c.x) + Math.abs(a.z - c.z)).toBeGreaterThan(0.01);
    expect(rockDensity(5.5, 5.5, occ)).toBeGreaterThan(rockDensity(8.5, 8.5, occ));
  });

  it('grows different speleothems on neighboring faces', () => {
    const faces = exposedCaveFaces([[0, 0], [3, 2]], []);
    const items = caveSpeleothems(faces, { wallHeight: WALL_HEIGHT, cellSize: CELL_SIZE });
    expect(items.some((s) => s.kind === 'stalactite')).toBe(true);
    expect(items.some((s) => s.kind === 'stalagmite' || s.kind === 'column')).toBe(true);
    const a = items.filter((s) => Math.hypot(s.x, s.z) < 2);
    const b = items.filter((s) => Math.hypot(s.x - 3.5, s.z - 2.5) < 2);
    const same = a.length === b.length && a.every((item, i) => (
      item.kind === b[i]?.kind && Math.abs(item.height - b[i].height) < 1e-6
    ));
    expect(same).toBe(false);
  });

  it('tracks solid occupancy by cell', () => {
    expect(solidCellSet([[1, 2], { x: 3, y: 4 }])).toEqual(new Set(['1,2', '3,4']));
  });
});
