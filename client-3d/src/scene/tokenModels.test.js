import { describe, expect, it } from 'vitest';
import * as THREE from 'three';
import {
  buildFigure,
  defaultFigureHeight,
  feetToUnits,
  fitFigure,
  modelExt,
  normalizeUpAxis,
  tintFigure,
  tokenModelUrl,
} from './tokenModels.js';

describe('tokenModelUrl', () => {
  it('maps campaign asset paths to the /assets route', () => {
    expect(tokenModelUrl('models/tokens/paladin.glb')).toBe('/assets/models/tokens/paladin.glb');
    expect(tokenModelUrl('assets/models/tokens/paladin.glb')).toBe('/assets/models/tokens/paladin.glb');
    expect(tokenModelUrl('/assets/models/x.glb')).toBe('/assets/models/x.glb');
  });

  it('passes absolute URLs through', () => {
    expect(tokenModelUrl('https://cdn.example/x.glb')).toBe('https://cdn.example/x.glb');
    expect(tokenModelUrl(null)).toBeNull();
  });
});

describe('modelExt', () => {
  it('reads lowercased extensions', () => {
    expect(modelExt('a/b/paladin.GLB')).toBe('.glb');
    expect(modelExt('figure.stl')).toBe('.stl');
    expect(modelExt('no-ext')).toBe('');
  });
});

describe('unit conversion', () => {
  it('converts feet to map squares', () => {
    expect(feetToUnits(10, 5)).toBe(2);
    expect(feetToUnits(6)).toBe(1.2); // default 5 ft per grid
  });

  it('scales default height with token size', () => {
    expect(defaultFigureHeight(1)).toBe(6);
    expect(defaultFigureHeight(2)).toBeCloseTo(6 * Math.SQRT2);
    expect(defaultFigureHeight(0)).toBe(6);
  });
});

describe('fitFigure', () => {
  function tallBox(half = 1) {
    // Box of size (2, 2*h, 2): set scale so world height == h feet-ish
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(2, 2, 2));
    mesh.scale.y = half;
    mesh.updateMatrixWorld(true);
    return mesh;
  }

  it('normalizes height and plants the base at y=0, centered in x/z', () => {
    const figure = fitFigure(tallBox(3), 6); // box spans y -3..3
    const box = new THREE.Box3().setFromObject(figure);
    expect(box.min.y).toBeCloseTo(0);
    expect(box.max.y).toBeCloseTo(6);
    const center = box.getCenter(new THREE.Vector3());
    expect(center.x).toBeCloseTo(0);
    expect(center.z).toBeCloseTo(0);
  });

  it('rotates Z-up models to Y-up', () => {
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(2, 2, 10)); // z is tallest
    const root = new THREE.Group();
    root.add(mesh);
    expect(normalizeUpAxis(root, new THREE.Box3().setFromObject(root))).toBe(true);
    const box = new THREE.Box3().setFromObject(root);
    const size = box.getSize(new THREE.Vector3());
    expect(size.y).toBeCloseTo(10);
    expect(size.z).toBeCloseTo(2);
  });

  it('leaves Y-up models alone', () => {
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(2, 10, 2));
    expect(normalizeUpAxis(mesh, new THREE.Box3().setFromObject(mesh))).toBe(false);
  });
});

describe('tintFigure', () => {
  it('tints untextured materials without touching the cached source', () => {
    const mat = new THREE.MeshStandardMaterial({ color: 0xffffff });
    const source = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), mat);
    const clone = source.clone(true);
    tintFigure(clone, '#8899ff');
    expect(clone.material.color.getHexString()).toBe('8899ff');
    expect(mat.color.getHexString()).toBe('ffffff'); // source cache untouched
  });

  it('keeps textured materials as-is', () => {
    const tex = new THREE.Texture();
    const mat = new THREE.MeshStandardMaterial({ map: tex });
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), mat);
    tintFigure(mesh, '#ff0000');
    expect(mesh.material).toBe(mat);
  });

  it('ignores invalid colors', () => {
    const mat = new THREE.MeshStandardMaterial({ color: 0x336699 });
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), mat);
    tintFigure(mesh, 'not-a-color');
    expect(mesh.material.color.getHexString()).toBe('336699');
  });
});

describe('buildFigure', () => {
  it('returns null without a src (billboard token fallback)', async () => {
    expect(await buildFigure(null)).toBeNull();
    expect(await buildFigure({})).toBeNull();
  });
});

describe('size-change spell scaling (size_scale)', () => {
  const fakeModel = async () => {
    const group = new THREE.Group();
    group.add(new THREE.Mesh(new THREE.BoxGeometry(1, 2, 1)));
    return group;
  };

  const figureHeight = async (model, opts) => {
    const fig = await buildFigure(model, { feetPerGrid: 5, size: 1, load: fakeModel, ...opts });
    const box = new THREE.Box3().setFromObject(fig);
    return box.max.y - box.min.y;
  };

  it('scales a figure whose explicit height was authored at natural size', async () => {
    // 5 ft / 5 ft-per-grid = 1 unit; Enlarge doubles linear dimensions
    expect(await figureHeight({ src: 'x.glb', height: 5, size_scale: 2 })).toBeCloseTo(2);
    expect(await figureHeight({ src: 'x.glb', height: 5 })).toBeCloseTo(1);
    expect(await figureHeight({ src: 'x.glb', height: 5, size_scale: 0.5 })).toBeCloseTo(0.5);
  });

  it('does not double-scale the default square-count height', async () => {
    // defaultFigureHeight already follows the (current) size in squares
    const h = await figureHeight({ src: 'x.glb', size_scale: 2 }, { size: 2 });
    expect(h).toBeCloseTo(defaultFigureHeight(2) / 5);
  });
});
