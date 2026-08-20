/**
 * Organic cave wall forms (no Three.js).
 * Exposed faces + speleothems are deterministic for a fixed seed.
 */

import { CELL_SIZE, WALL_HEIGHT } from '../geometry.js';
import { CAVE_STONE_SEED, fbm, hash2i } from './caveStone.js';

export const CAVE_FORM_SEED = CAVE_STONE_SEED;
export const CAVE_FACE_U = 8;
export const CAVE_FACE_V = 11;
export const CAVE_CAP_DIV = 4;

export function cellKey(x, y) {
  return `${x},${y}`;
}

export function solidCellSet(solidWalls) {
  const set = new Set();
  for (const cell of solidWalls || []) {
    const x = Array.isArray(cell) ? Number(cell[0]) : Number(cell.x);
    const y = Array.isArray(cell) ? Number(cell[1]) : Number(cell.y);
    if (Number.isFinite(x) && Number.isFinite(y)) set.add(cellKey(x, y));
  }
  return set;
}

function distToSegment(px, pz, x0, z0, x1, z1) {
  const dx = x1 - x0;
  const dz = z1 - z0;
  const len2 = dx * dx + dz * dz;
  if (len2 < 1e-10) return Math.hypot(px - x0, pz - z0);
  let t = ((px - x0) * dx + (pz - z0) * dz) / len2;
  t = t < 0 ? 0 : t > 1 ? 1 : t;
  return Math.hypot(px - (x0 + t * dx), pz - (z0 + t * dz));
}

export function occupancyFromGeometry(solidWalls, edgeWalls, cellSize = CELL_SIZE) {
  const solids = solidCellSet(solidWalls);
  const edges = [];
  for (const wall of edgeWalls || []) {
    const x = Number(wall.x);
    const y = Number(wall.y);
    for (const edge of wall.edges || []) {
      const seg = edgeSegment(x, y, edge, cellSize);
      if (seg) edges.push(seg);
    }
  }
  return { solids, edges };
}

function edgeSegment(gx, gy, edge, cellSize) {
  const x0 = gx * cellSize;
  const z0 = gy * cellSize;
  const x1 = (gx + 1) * cellSize;
  const z1 = (gy + 1) * cellSize;
  if (edge === 'n') return { x0, z0, x1, z1: z0 };
  if (edge === 's') return { x0, z0: z1, x1, z1 };
  if (edge === 'w') return { x0, z0, x1: x0, z1 };
  if (edge === 'e') return { x0: x1, z0, x1, z1 };
  return null;
}

export function rockDensity(wx, wz, occupancy, cellSize = CELL_SIZE) {
  let d = 0;
  const gx = Math.floor(wx / cellSize);
  const gy = Math.floor(wz / cellSize);
  const solids = occupancy?.solids;
  for (let i = -1; i <= 1; i += 1) {
    for (let j = -1; j <= 1; j += 1) {
      if (!solids || !solids.has(cellKey(gx + i, gy + j))) continue;
      const cx = (gx + i + 0.5) * cellSize;
      const cz = (gy + j + 0.5) * cellSize;
      d += Math.max(0, 0.7 - Math.hypot(wx - cx, wz - cz));
    }
  }
  for (const edge of occupancy?.edges || []) {
    d += Math.max(0, 0.16 - distToSegment(wx, wz, edge.x0, edge.z0, edge.x1, edge.z1));
  }
  return d;
}

export function densityGradient(wx, wz, occupancy, cellSize = CELL_SIZE, eps = 0.06) {
  const dx = rockDensity(wx + eps, wz, occupancy, cellSize)
    - rockDensity(wx - eps, wz, occupancy, cellSize);
  const dz = rockDensity(wx, wz + eps, occupancy, cellSize)
    - rockDensity(wx, wz - eps, occupancy, cellSize);
  return { x: dx / (2 * eps), z: dz / (2 * eps) };
}

function smooth01(t) {
  const x = t < 0 ? 0 : t > 1 ? 1 : t;
  return x * x * (3 - 2 * x);
}

/**
 * World-space warp so shared corners stay welded and every stretch of
 * wall reads as a different limestone face.
 */
export function displaceCavePoint(x, y, z, occupancy, opts = {}) {
  const cellSize = opts.cellSize ?? CELL_SIZE;
  const seed = opts.seed ?? CAVE_FORM_SEED;
  const wallHeight = opts.wallHeight ?? WALL_HEIGHT;
  const fallback = opts.outward || { x: 0, z: 0 };
  const t = y / Math.max(1e-4, wallHeight);

  const curlX = (fbm(x * 1.42, z * 1.42 + y * 0.52, 18, seed + 3, 4) - 0.5) * 0.18;
  const curlY = (fbm(y * 2.55, x * 0.92 + z * 0.92, 18, seed + 9, 3) - 0.5) * 0.12;
  const curlZ = (fbm(z * 1.42, x * 1.42 + y * 0.52, 18, seed + 17, 4) - 0.5) * 0.18;

  const g = densityGradient(x, z, occupancy, cellSize);
  let ox = -g.x;
  let oz = -g.z;
  const glen = Math.hypot(ox, oz);
  if (glen < 0.04) {
    ox = fallback.x;
    oz = fallback.z;
  } else {
    ox /= glen;
    oz /= glen;
  }

  const flow = fbm(x * 0.85, z * 0.85, 18, seed + 41, 3);
  const base = (1 - t) * (1 - t) * (0.1 + flow * 0.12);
  const overhang = smooth01((t - 0.52) / 0.48) * (0.05 + flow * 0.24);
  const waist = Math.sin(t * Math.PI) * (flow - 0.45) * 0.14;
  const push = base + overhang + waist;
  const topRipple = t > 0.84
    ? (fbm(x * 3.05, z * 3.05, 18, seed + 53, 2) - 0.5) * 0.22
    : 0;

  return {
    x: x + curlX + ox * push,
    y: Math.max(0, y + curlY + topRipple),
    z: z + curlZ + oz * push,
  };
}

function faceKey(x0, z0, x1, z1) {
  const a = `${x0.toFixed(4)},${z0.toFixed(4)}`;
  const b = `${x1.toFixed(4)},${z1.toFixed(4)}`;
  return a < b ? `${a}|${b}` : `${b}|${a}`;
}

const SOLID_EDGES = [
  { edge: 'n', nx: 0, nz: -1, dx: 0, dy: -1 },
  { edge: 's', nx: 0, nz: 1, dx: 0, dy: 1 },
  { edge: 'w', nx: -1, nz: 0, dx: -1, dy: 0 },
  { edge: 'e', nx: 1, nz: 0, dx: 1, dy: 0 },
];

function makeFace(gx, gy, edge, cellSize, wallHeight, source) {
  const seg = edgeSegment(gx, gy, edge, cellSize);
  if (!seg) return null;
  const meta = SOLID_EDGES.find((item) => item.edge === edge);
  return {
    gridX: gx,
    gridY: gy,
    edge,
    source,
    x0: seg.x0,
    z0: seg.z0,
    x1: seg.x1,
    z1: seg.z1,
    height: wallHeight,
    outward: { x: meta?.nx || 0, z: meta?.nz || 0 },
  };
}

export function exposedCaveFaces(solidWalls, edgeWalls, opts = {}) {
  const cellSize = opts.cellSize ?? CELL_SIZE;
  const wallHeight = opts.wallHeight ?? WALL_HEIGHT;
  const solids = solidCellSet(solidWalls);
  const seen = new Set();
  const faces = [];

  const add = (face) => {
    if (!face) return;
    const key = faceKey(face.x0, face.z0, face.x1, face.z1);
    if (seen.has(key)) return;
    seen.add(key);
    faces.push(face);
  };

  for (const key of solids) {
    const [gx, gy] = key.split(',').map(Number);
    for (const dir of SOLID_EDGES) {
      if (solids.has(cellKey(gx + dir.dx, gy + dir.dy))) continue;
      add(makeFace(gx, gy, dir.edge, cellSize, wallHeight, 'solid'));
    }
  }
  for (const wall of edgeWalls || []) {
    const gx = Number(wall.x);
    const gy = Number(wall.y);
    for (const edge of wall.edges || []) {
      add(makeFace(gx, gy, edge, cellSize, wallHeight, 'edge'));
    }
  }
  return faces;
}

export function solidCaveCaps(solidWalls, opts = {}) {
  const cellSize = opts.cellSize ?? CELL_SIZE;
  const wallHeight = opts.wallHeight ?? WALL_HEIGHT;
  const caps = [];
  for (const cell of solidWalls || []) {
    const gx = Array.isArray(cell) ? Number(cell[0]) : Number(cell.x);
    const gy = Array.isArray(cell) ? Number(cell[1]) : Number(cell.y);
    caps.push({
      gridX: gx,
      gridY: gy,
      x0: gx * cellSize,
      z0: gy * cellSize,
      x1: (gx + 1) * cellSize,
      z1: (gy + 1) * cellSize,
      y: wallHeight,
    });
  }
  return caps;
}

function lerp(a, b, t) {
  return a + (b - a) * t;
}

function facePoint(face, u) {
  return {
    x: lerp(face.x0, face.x1, u),
    z: lerp(face.z0, face.z1, u),
  };
}

/**
 * Stalactites (ceiling drips), stalagmites (floor spikes), and occasional
 * floor-to-ceiling columns. Hashed per face so adjacent walls differ.
 */
export function caveSpeleothems(faces, opts = {}) {
  const seed = opts.seed ?? CAVE_FORM_SEED;
  const wallHeight = opts.wallHeight ?? WALL_HEIGHT;
  const items = [];
  for (const face of faces || []) {
    const salt = face.edge.charCodeAt(0) * 13;
    const a = hash2i(face.gridX + salt, face.gridY, seed + 70);
    const b = hash2i(face.gridX, face.gridY + salt, seed + 83);
    const c = hash2i(face.gridX * 3 + salt, face.gridY * 5, seed + 97);
    const count = a > 0.22 ? 1 + Math.floor(b * 3) : 0;
    const column = a > 0.78 && b > 0.62;
    for (let i = 0; i < count; i += 1) {
      const u = 0.14 + hash2i(face.gridX, face.gridY + i * 19 + salt, seed + 110) * 0.72;
      const p = facePoint(face, u);
      const into = 0.07 + hash2i(i + salt, face.gridY, seed + 121) * 0.22;
      const x = p.x + face.outward.x * into;
      const z = p.z + face.outward.z * into;
      const dripH = 0.16 + b * 0.42 + i * 0.04;
      const moundH = 0.1 + a * 0.38 + (count - i) * 0.03;
      const radius = 0.025 + c * 0.055 + i * 0.006;
      if (column && i === 0) {
        items.push({
          kind: 'column',
          x,
          y: wallHeight * 0.5,
          z,
          height: wallHeight * (0.9 + c * 0.08),
          radius,
          radiusTop: radius * (0.45 + a * 0.25),
        });
        continue;
      }
      if (a > 0.28 || i === 0) {
        items.push({
          kind: 'stalactite',
          x,
          y: wallHeight - dripH * 0.5,
          z,
          height: dripH,
          radius: radius * (0.85 + (i % 2) * 0.2),
        });
      }
      if (b > 0.34 + i * 0.08) {
        items.push({
          kind: 'stalagmite',
          x: x + face.outward.x * 0.04,
          y: moundH * 0.5,
          z: z + face.outward.z * 0.04,
          height: moundH,
          radius: radius * 1.15,
        });
      }
    }
    const straws = 2 + Math.floor(c * 4);
    for (let i = 0; i < straws; i += 1) {
      const u = 0.08 + hash2i(face.gridX + i * 31, face.gridY + salt, seed + 140) * 0.84;
      if (hash2i(salt + i, face.gridX, seed + 151) < 0.38) continue;
      const p = facePoint(face, u);
      const into = 0.04 + hash2i(i, salt + face.gridY, seed + 160) * 0.12;
      const h = 0.07 + hash2i(face.gridY, i + 3, seed + 171) * 0.16;
      items.push({
        kind: 'stalactite',
        x: p.x + face.outward.x * into,
        y: wallHeight - h * 0.5,
        z: p.z + face.outward.z * into,
        height: h,
        radius: 0.012 + hash2i(i, face.gridX, seed + 182) * 0.02,
      });
    }
  }
  return items;
}
