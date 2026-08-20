/** Token sprite helpers for the 3D VTT. */

export const FLY_HEIGHT = 0.78;
export const FLY_BOB = 0.055;
export const PRONE_LIFT = 0.035;

export function tokenAssetUrl(filename) {
  if (!filename) return null;
  const text = String(filename);
  if (text.startsWith('http') || text.startsWith('/')) return text;
  return `/assets/${text}`;
}

export function isPronePose(entity = {}) {
  return Boolean(entity.prone || entity.dead || entity.unconscious);
}

export function isFlyingPose(entity = {}) {
  return Boolean(entity.flying) && !isPronePose(entity);
}

/** Standing disc / hover height for the token group. */
export function tokenBaseY(entity = {}, { stairY = 0 } = {}) {
  const lift = Number(stairY);
  const surface = Number.isFinite(lift) ? lift : 0;
  return (isFlyingPose(entity) ? FLY_HEIGHT : 0) + surface;
}

export function tokenBillboardHeight(entity = {}, size = 1) {
  if (isPronePose(entity)) return PRONE_LIFT;
  return 0.55 * Math.max(1, Number(size) || 1);
}
