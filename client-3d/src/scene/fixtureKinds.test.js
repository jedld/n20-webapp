import { describe, expect, it } from 'vitest';
import {
  fixtureKind,
  lightColor,
  lightRadii,
  pointLightParams,
  shouldEmitLight,
  yawFromEdge,
} from './fixtureKinds.js';

describe('fixtureKind lights', () => {
  it('classifies campfires, braziers, fireplaces, and torches', () => {
    expect(fixtureKind({ type: 'campfire' })).toBe('campfire');
    expect(fixtureKind({ name: 'fire' })).toBe('campfire');
    expect(fixtureKind({ type: 'brazier' })).toBe('brazier');
    expect(fixtureKind({ type: 'fireplace' })).toBe('fireplace');
    expect(fixtureKind({ type: 'torch' })).toBe('torch');
    expect(fixtureKind({ kind: 'torch', hide_map_token: true })).toBe('torch');
    expect(fixtureKind({ type: 'fireplace', hide_map_token: true })).toBe('fireplace');
    expect(fixtureKind({ type: 'candle', light: { bright: 5, dim: 5 } })).toBe('lamp');
  });

  it('classifies floor hatches even when the 2D client marks them as doors', () => {
    expect(fixtureKind({ type: 'trap_door', door_highlight: true })).toBe('trap_door');
    expect(fixtureKind({ type: 'church_trapdoor' })).toBe('trap_door');
    expect(fixtureKind({ type: 'trap_door', secret: true })).toBe('secret_trapdoor');
    expect(fixtureKind({ type: 'pit_trap' })).toBe('pit_trap');
    expect(fixtureKind({ type: 'chasm' })).toBe('chasm');
    expect(fixtureKind({ type: 'bottomless_pit' })).toBe('chasm');
    expect(fixtureKind({ kind: 'secret_trapdoor', door_highlight: true })).toBe('secret_trapdoor');
    expect(fixtureKind({ type: 'stairs', hide_map_token: true })).toBe('stairs');
    expect(fixtureKind({ kind: 'stairs', hide_map_token: true })).toBe('stairs');
    expect(fixtureKind({ type: 'teleporter', name: 'Stairs Up' })).toBe('teleporter');
  });

  it('does not hide light fixtures behind hide_map_token', () => {
    expect(fixtureKind({ type: 'chest', hide_map_token: true })).toBeNull();
    expect(fixtureKind({ type: 'campfire', hide_map_token: true })).toBe('campfire');
  });

  it('emits light for campfires and unlit fireplaces stay dark', () => {
    expect(shouldEmitLight({ type: 'campfire', light: { bright: 20, dim: 10 } })).toBe(true);
    expect(shouldEmitLight({ type: 'fireplace', lit: false, light: { bright: 20, dim: 10 } })).toBe(false);
    expect(shouldEmitLight({ type: 'fireplace', lit: true, light: { bright: 20, dim: 10 } })).toBe(true);
    expect(shouldEmitLight({ kind: 'torch', bright: 5, dim: 5 })).toBe(true);
    expect(shouldEmitLight({ light: { bright: 20, dim: 20, origin: 'item' } })).toBe(true);
    expect(shouldEmitLight({ light: { bright: 20, dim: 20 }, lit: false })).toBe(false);
  });

  it('uses cool white for spell auras and orange for carried lamps', () => {
    expect(lightColor({ light: { origin: 'effect', color: 'blue' } })).toBe(0x9ad4ff);
    expect(lightColor({ light: { origin: 'effect' } })).toBe(0xfff4d6);
    expect(lightColor({ light: { origin: 'item', bright: 20 } })).toBe(0xff9a4a);
  });

  it('sizes PointLights from bright/dim feet', () => {
    expect(lightRadii({ bright: 10, dim: 5 })).toEqual({ bright: 10, dim: 5 });
    const params = pointLightParams({ light: { bright: 20, dim: 10 } }, 5, 1);
    expect(params.intensity).toBe(8);
    expect(params.distance).toBeCloseTo(6.9);
    expect(params.decay).toBe(2);
  });

  it('orients wall fixtures into the room', () => {
    expect(yawFromEdge('n')).toBe(0);
    expect(yawFromEdge('s')).toBeCloseTo(Math.PI);
    expect(yawFromEdge('e')).toBeCloseTo(-Math.PI / 2);
    expect(yawFromEdge('w')).toBeCloseTo(Math.PI / 2);
  });
});
