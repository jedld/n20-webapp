import { describe, expect, it } from 'vitest';
import {
  FLY_HEIGHT,
  isFlyingPose,
  isPronePose,
  tokenBaseY,
  tokenBillboardHeight,
} from './tokens.js';

describe('token poses', () => {
  it('lays prone and dead tokens on the floor', () => {
    expect(isPronePose({ prone: true })).toBe(true);
    expect(isPronePose({ dead: true })).toBe(true);
    expect(isPronePose({ unconscious: true })).toBe(true);
    expect(isPronePose({})).toBe(false);
    expect(tokenBillboardHeight({ prone: true }, 1)).toBeLessThan(0.1);
  });

  it('lifts flying tokens and ignores fly when prone', () => {
    expect(isFlyingPose({ flying: true })).toBe(true);
    expect(tokenBaseY({ flying: true })).toBe(FLY_HEIGHT);
    expect(isFlyingPose({ flying: true, prone: true })).toBe(false);
    expect(tokenBaseY({ flying: true, prone: true })).toBe(0);
    expect(tokenBaseY({}, { stairY: 0.8 })).toBeCloseTo(0.8);
    expect(tokenBaseY({ flying: true }, { stairY: 0.8 })).toBeCloseTo(FLY_HEIGHT + 0.8);
  });
});
