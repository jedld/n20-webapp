/** Pointer → grid helpers (scene.gridFromEvent is the runtime source). */

export function isClickNotDrag(down, up, thresholdSq = 25) {
  if (!down || !up) return true;
  const dx = up.x - down.x;
  const dy = up.y - down.y;
  return dx * dx + dy * dy <= thresholdSq;
}
