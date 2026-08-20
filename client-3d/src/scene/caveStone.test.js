import { describe, expect, it } from 'vitest';
import {
  CAVE_STONE_SEED,
  fillCaveStoneMaps,
  sampleCaveStone,
  valueNoise,
} from './caveStone.js';

describe('cave stone', () => {
  it('is deterministic for a fixed seed', () => {
    const a = sampleCaveStone(0.31, 0.44, CAVE_STONE_SEED);
    const b = sampleCaveStone(0.31, 0.44, CAVE_STONE_SEED);
    expect(a).toEqual(b);
    const maps = fillCaveStoneMaps(32, 32, CAVE_STONE_SEED);
    const again = fillCaveStoneMaps(32, 32, CAVE_STONE_SEED);
    expect(maps.albedo).toEqual(again.albedo);
    expect(maps.bump).toEqual(again.bump);
    expect(maps.roughness).toEqual(again.roughness);
  });

  it('changes when the seed changes', () => {
    const a = sampleCaveStone(0.31, 0.44, CAVE_STONE_SEED);
    const b = sampleCaveStone(0.31, 0.44, CAVE_STONE_SEED + 1);
    expect(a.r !== b.r || a.g !== b.g || a.height !== b.height).toBe(true);
  });

  it('wraps seamlessly on the U and V edges', () => {
    const period = 12;
    expect(valueNoise(0, 3.2, period, 7)).toBeCloseTo(valueNoise(period, 3.2, period, 7), 10);
    expect(valueNoise(4.1, 0, period, 7)).toBeCloseTo(valueNoise(4.1, period, period, 7), 10);
    const left = sampleCaveStone(0, 0.4);
    const right = sampleCaveStone(1, 0.4);
    expect(Math.abs(left.r - right.r)).toBeLessThan(1e-6);
    expect(Math.abs(left.height - right.height)).toBeLessThan(1e-6);
    const w = 64;
    const { albedo } = fillCaveStoneMaps(w, w, CAVE_STONE_SEED);
    let edgeJump = 0;
    for (let y = 0; y < w; y += 1) {
      const a = (y * w) * 4;
      const b = (y * w + (w - 1)) * 4;
      edgeJump += Math.abs(albedo[a] - albedo[b]);
    }
    expect(edgeJump / w).toBeLessThan(22);
  });

  it('blends neighboring pixels instead of posterizing', () => {
    const w = 96;
    const { albedo } = fillCaveStoneMaps(w, w, CAVE_STONE_SEED);
    let jump = 0;
    let count = 0;
    for (let y = 0; y < w; y += 1) {
      for (let x = 0; x < w - 1; x += 1) {
        const i = (y * w + x) * 4;
        jump += Math.abs(albedo[i] - albedo[i + 4]);
        count += 1;
      }
    }
    expect(jump / count).toBeLessThan(22);
  });

  it('is mostly rough limestone with distinct smooth patches', () => {
    let smooth = 0;
    let rough = 0;
    let sum = 0;
    const n = 48;
    for (let y = 0; y < n; y += 1) {
      for (let x = 0; x < n; x += 1) {
        const r = sampleCaveStone(x / n, y / n).roughness;
        expect(r).toBeGreaterThanOrEqual(0);
        expect(r).toBeLessThanOrEqual(1);
        sum += r;
        if (r < 0.42) smooth += 1;
        if (r > 0.78) rough += 1;
      }
    }
    const total = n * n;
    expect(rough).toBeGreaterThan(smooth);
    expect(smooth).toBeGreaterThan(total * 0.04);
    expect(rough).toBeGreaterThan(total * 0.35);
    expect(sum / total).toBeGreaterThan(0.55);
  });

  it('wraps roughness seamlessly', () => {
    const left = sampleCaveStone(0, 0.37);
    const right = sampleCaveStone(1, 0.37);
    expect(Math.abs(left.roughness - right.roughness)).toBeLessThan(1e-6);
  });
});
