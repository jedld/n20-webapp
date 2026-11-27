import { describe, expect, it } from 'vitest';
import * as THREE from 'three';
import { arrowHeadGeometry, flatRingGeometry, ribbonGeometries, ribbonGeometry } from './flowArrow.js';

describe('ribbonGeometries style split', () => {
  const pts = [
    new THREE.Vector3(0.5, 0.06, 0.5),
    new THREE.Vector3(1.5, 0.06, 0.5),
    new THREE.Vector3(1.5, 0.06, 1.5),
  ];

  it('routes difficult segments (entered-cell style) into the dot geometry', () => {
    const geos = ribbonGeometries(pts, 0.16, 0.6, [false, true]);
    // segment 0 (+corner patch shared with segment 1? corner uses segment-0 style) in flow
    expect(geos.flow).not.toBeNull();
    expect(geos.dot).not.toBeNull();
    // 2 segments: flow = seg0 + corner = 12 verts, dot = seg1 = 6 verts
    expect(geos.flow.getAttribute('position').count).toBe(12);
    expect(geos.dot.getAttribute('position').count).toBe(6);
  });

  it('dot geometry is null when no styles are given', () => {
    const geos = ribbonGeometries(pts, 0.16, 0.6);
    expect(geos.dot).toBeNull();
    expect(geos.flow.getAttribute('position').count).toBe((2 + 1) * 6);
  });
});

describe('flatRingGeometry', () => {
  it('lies flat on the xz plane', () => {
    const geo = flatRingGeometry(0.4, 0.5, 16);
    const pos = geo.getAttribute('position');
    let minY = Infinity, maxY = -Infinity;
    for (let i = 0; i < pos.count; i += 1) {
      minY = Math.min(minY, pos.getY(i));
      maxY = Math.max(maxY, pos.getY(i));
    }
    expect(minY).toBeCloseTo(maxY); // planar after rotateX(-90deg)
  });
});

describe('ribbonGeometry', () => {
  const pts = [
    new THREE.Vector3(0.5, 0.06, 0.5),
    new THREE.Vector3(1.5, 0.06, 0.5),
    new THREE.Vector3(1.5, 0.06, 1.5),
    new THREE.Vector3(2.5, 0.06, 1.5),
  ];

  it('emits one quad per step plus corner patches', () => {
    const geo = ribbonGeometry(pts, 0.16, 0.6);
    const pos = geo.getAttribute('position');
    const uv = geo.getAttribute('uv');
    // 3 segments -> 3 quads (6 verts each); 2 interior corners -> 2 patches (6 verts each)
    expect(pos.count).toBe((3 + 2) * 6);
    expect(uv.count).toBe(pos.count);
    for (let i = 0; i < pos.count * 3; i += 1) {
      expect(Number.isFinite(pos.array[i])).toBe(true);
    }
    for (let i = 0; i < uv.count * 2; i += 1) {
      expect(Number.isFinite(uv.array[i])).toBe(true);
    }
  });

  it('uv.x runs along the route in dashPeriod repeats', () => {
    const geo = ribbonGeometry([pts[0], pts[1]], 0.16, 0.6);
    const uv = geo.getAttribute('uv');
    let minU = Infinity, maxU = -Infinity;
    for (let i = 0; i < uv.count; i += 1) {
      minU = Math.min(minU, uv.getX(i));
      maxU = Math.max(maxU, uv.getX(i));
    }
    expect(minU).toBeCloseTo(0);
    expect(maxU).toBeCloseTo(1 / 0.6); // 1 world unit / 0.6 dash period
  });

  it('single-segment paths work (no corner patches)', () => {
    const geo = ribbonGeometry([pts[0], pts[1]], 0.16, 0.6);
    expect(geo.getAttribute('position').count).toBe(6);
  });
});

describe('arrowHeadGeometry', () => {
  it('is a floor triangle whose tip sits at the destination', () => {
    const tip = new THREE.Vector3(2.5, 0.06, 1.5);
    const geo = arrowHeadGeometry(tip, new THREE.Vector3(1, 0, 0), 0.4);
    const pos = geo.getAttribute('position');
    expect(pos.count).toBe(3);
    expect(pos.getX(0)).toBeCloseTo(tip.x);
    expect(pos.getZ(0)).toBeCloseTo(tip.z);
    // the other two vertices sit behind the tip along -dir
    expect(pos.getX(1)).toBeLessThan(tip.x);
    expect(pos.getX(2)).toBeLessThan(tip.x);
    // and are flat on the floor
    for (let i = 0; i < 3; i += 1) expect(pos.getY(i)).toBeCloseTo(0.06);
  });
});
