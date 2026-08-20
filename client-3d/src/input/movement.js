/** Movement-mode helpers. */

export function pathReaches(path, x, y) {
  if (!Array.isArray(path) || !path.length) return false;
  const last = path[path.length - 1];
  return Number(last[0]) === Number(x) && Number(last[1]) === Number(y);
}
