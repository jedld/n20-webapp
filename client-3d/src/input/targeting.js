/** Targeting-mode helpers for /target + /action. */

export function targetingModeForStep(stepType) {
  if (stepType === 'select_cone') return 'cone';
  if (stepType === 'select_cube') return 'cube';
  return null;
}

export function isTargetingStep(stepType) {
  return (
    stepType === 'select_target'
    || stepType === 'select_cone'
    || stepType === 'select_cube'
    || stepType === 'select_radius'
    || stepType === 'select_empty_space'
  );
}

export function isAttackTargeting(stepType) {
  return stepType === 'select_target';
}

/**
 * Melee vs ranged for the attack arrow style. Attack actions carry their
 * reach in feet (melee = 5, none = melee by convention).
 */
export function isMeleeRange(range) {
  const feet = Number(range);
  if (!Number.isFinite(feet)) return true;
  return feet <= 5;
}

export function rangeFeet(from, to, feetPerGrid = 5) {
  const dx = Number(to?.x) - Number(from?.x);
  const dy = Number(to?.y) - Number(from?.y);
  if (!Number.isFinite(dx) || !Number.isFinite(dy)) return 0;
  const step = Number(feetPerGrid) || 5;
  return Math.floor(Math.hypot(dx, dy) * step);
}
