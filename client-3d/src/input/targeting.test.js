import { describe, expect, it } from 'vitest';
import { isAttackTargeting, isTargetingStep, rangeFeet, targetingModeForStep } from './targeting.js';

describe('targeting helpers', () => {
  it('treats select_target as attack aiming', () => {
    expect(isTargetingStep('select_target')).toBe(true);
    expect(isAttackTargeting('select_target')).toBe(true);
    expect(isAttackTargeting('select_cone')).toBe(false);
    expect(targetingModeForStep('select_cone')).toBe('cone');
  });

  it('reports range in feet for attack lines', () => {
    expect(rangeFeet({ x: 0, y: 0 }, { x: 3, y: 4 }, 5)).toBe(25);
    expect(rangeFeet({ x: 1, y: 1 }, { x: 1, y: 1 })).toBe(0);
  });
});
