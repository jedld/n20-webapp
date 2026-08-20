/**
 * Classify / orient 3D map fixtures. Pure — no Three.js.
 */

const SKIP_TYPES = new Set([
  'wooden_door', 'ground', 'water',
  'spawn_point', 'note', 'switch', 'window',
]);
const CHEST_TYPES = new Set(['chest', 'tavern_guest_chest', 'tavern_till_safe']);
const BARREL_TYPES = new Set(['barrel', 'crate', 'cask']);
const COVER_LEVELS = new Set(['half', 'three_quarter', 'total']);
export const LIGHT_KINDS = new Set(['campfire', 'brazier', 'fireplace', 'torch', 'lamp']);
export const MAX_DECOR_LIGHTS = 20;
const NAMED_LIGHT_COLORS = {
  white: 0xfff4d6,
  blue: 0x9ad4ff,
  red: 0xff6a4a,
  green: 0x88e0a0,
  yellow: 0xffe08a,
  purple: 0xc9a6ff,
};

export function fixtureKind(obj = {}) {
  if (obj.kind) return obj.kind;
  const typeName = String(obj.type || '').toLowerCase();
  const name = String(obj.name || obj.label || '').toLowerCase();
  if (typeName === 'pit_trap' || name === 'pit trap') return 'pit_trap';
  if (typeName.includes('trap_door') || typeName.includes('trapdoor') || name.includes('trapdoor')) {
    return obj.secret ? 'secret_trapdoor' : 'trap_door';
  }
  if (typeName === 'stairs' || typeName === 'stair') return 'stairs';
  if (typeName === 'chasm' || typeName === 'bottomless_pit' || typeName.includes('chasm')) return 'chasm';
  if (obj.door_highlight || obj.secret_door_marker) return null;
  if (
    SKIP_TYPES.has(typeName)
    || typeName.startsWith('stone_wall')
    || typeName.startsWith('door_')
    || typeName.startsWith('corner_door')
  ) {
    return null;
  }
  if (obj.teleporter_marker || typeName === 'teleporter') return 'teleporter';
  if (typeName === 'campfire' || name === 'fire' || name === 'campfire') return 'campfire';
  if (typeName === 'brazier' || name.includes('brazier')) return 'brazier';
  if (typeName === 'fireplace' || name.includes('fireplace')) return 'fireplace';
  if (['torch', 'torch_mount', 'sconce', 'wall_torch'].includes(typeName) || name.includes('torch')) {
    return 'torch';
  }
  const light = obj.light && typeof obj.light === 'object' ? obj.light : null;
  const hasLight = Boolean(Number(light?.bright) || Number(light?.dim) || Number(obj.bright) || Number(obj.dim));
  if (obj.hide_map_token && !obj.teleporter_marker && !hasLight) return null;
  if (CHEST_TYPES.has(typeName) || typeName.includes('chest') || name.includes('chest')) return 'chest';
  if (BARREL_TYPES.has(typeName) || name.includes('barrel')) return 'barrel';
  if (typeName === 'tree') return 'tree';
  if (typeName === 'briar') return 'briar';
  if (COVER_LEVELS.has(obj.cover)) return 'cover';
  if (
    hasLight
    || ['candle', 'lantern', 'hooded_lantern', 'bullseye_lantern', 'light_source'].includes(typeName)
  ) {
    return 'lamp';
  }
  return null;
}

export function yawFromFacing(facing) {
  const value = String(facing || 'down').toLowerCase();
  if (value === 'up') return Math.PI;
  if (value === 'left') return -Math.PI / 2;
  if (value === 'right') return Math.PI / 2;
  return 0;
}

/** Opening / flame faces into the room when the fixture hangs on `edge`. */
export function yawFromEdge(edge) {
  if (edge === 'n') return 0;
  if (edge === 's') return Math.PI;
  if (edge === 'e') return -Math.PI / 2;
  if (edge === 'w') return Math.PI / 2;
  return 0;
}

export function parseHexColor(value, fallback = 0x3d8bfd) {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.startsWith('#')) {
    const n = parseInt(value.slice(1), 16);
    return Number.isFinite(n) ? n : fallback;
  }
  return fallback;
}

export function lightRadii(obj = {}) {
  const nested = obj.light && typeof obj.light === 'object' ? obj.light : null;
  const bright = Number(nested?.bright ?? obj.bright ?? 0);
  const dim = Number(nested?.dim ?? obj.dim ?? 0);
  return {
    bright: Number.isFinite(bright) ? bright : 0,
    dim: Number.isFinite(dim) ? dim : 0,
  };
}

export function shouldEmitLight(obj = {}) {
  if (obj.lit === false || obj.lit === 0) return false;
  const { bright, dim } = lightRadii(obj);
  if (bright > 0 || dim > 0) return true;
  const kind = fixtureKind(obj);
  return kind === 'campfire' || kind === 'torch' || kind === 'brazier' || kind === 'lamp';
}

export function lightOrigin(obj = {}) {
  return String(obj.light?.origin || obj.origin || '').toLowerCase();
}

export function lightColor(obj = {}, fallback = 0xff9a4a) {
  const named = String(obj.light?.color || obj.color || '').toLowerCase();
  if (NAMED_LIGHT_COLORS[named]) return NAMED_LIGHT_COLORS[named];
  if (lightOrigin(obj) === 'effect') return NAMED_LIGHT_COLORS.white;
  return fallback;
}

/** Three.js r170 PointLight: intensity in candela, decay 2. Visual only. */
export function pointLightParams(obj = {}, feetPerGrid = 5, cellSize = 1) {
  let { bright, dim } = lightRadii(obj);
  if (bright <= 0 && dim <= 0) {
    bright = 20;
    dim = 10;
  }
  const feet = Math.max(1, Number(feetPerGrid) || 5);
  const size = Number(cellSize) > 0 ? Number(cellSize) : 1;
  return {
    color: lightColor(obj),
    intensity: 8 * (Math.max(bright, 1) / 20),
    distance: ((bright + dim) / feet) * 1.15 * size,
    decay: 2,
  };
}
