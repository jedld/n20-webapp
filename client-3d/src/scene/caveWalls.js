/**
 * Three.js meshes for organic cave walls and speleothems.
 */

import * as THREE from 'three';
import { CELL_SIZE, WALL_HEIGHT } from '../geometry.js';
import { CAVE_STONE_SEED, fbm } from './caveStone.js';
import {
  CAVE_CAP_DIV,
  CAVE_FACE_U,
  CAVE_FACE_V,
  CAVE_FORM_SEED,
  caveSpeleothems,
  displaceCavePoint,
  exposedCaveFaces,
  occupancyFromGeometry,
  solidCaveCaps,
} from './caveForm.js';

function pushVertex(positions, x, y, z) {
  positions.push(x, y, z);
}

function pushQuad(indices, a, b, c, d) {
  indices.push(a, b, c, a, c, d);
}

function displacedFaceVertices(face, occupancy, opts) {
  const positions = [];
  const indices = [];
  const nu = CAVE_FACE_U;
  const nv = CAVE_FACE_V;
  for (let iv = 0; iv <= nv; iv += 1) {
    const v = iv / nv;
    const y = v * face.height;
    for (let iu = 0; iu <= nu; iu += 1) {
      const u = iu / nu;
      const x = face.x0 + (face.x1 - face.x0) * u;
      const z = face.z0 + (face.z1 - face.z0) * u;
      const p = displaceCavePoint(x, y, z, occupancy, {
        ...opts,
        outward: face.outward,
      });
      pushVertex(positions, p.x, p.y, p.z);
    }
  }
  const cols = nu + 1;
  for (let iv = 0; iv < nv; iv += 1) {
    for (let iu = 0; iu < nu; iu += 1) {
      const a = iv * cols + iu;
      const b = a + 1;
      const c = a + cols + 1;
      const d = a + cols;
      pushQuad(indices, a, b, c, d);
    }
  }
  return { positions, indices };
}

function displacedCapVertices(cap, occupancy, opts) {
  const positions = [];
  const indices = [];
  const n = CAVE_CAP_DIV;
  for (let iz = 0; iz <= n; iz += 1) {
    const vz = iz / n;
    const z = cap.z0 + (cap.z1 - cap.z0) * vz;
    for (let ix = 0; ix <= n; ix += 1) {
      const vx = ix / n;
      const x = cap.x0 + (cap.x1 - cap.x0) * vx;
      const p = displaceCavePoint(x, cap.y, z, occupancy, opts);
      pushVertex(positions, p.x, p.y, p.z);
    }
  }
  const cols = n + 1;
  for (let iz = 0; iz < n; iz += 1) {
    for (let ix = 0; ix < n; ix += 1) {
      const a = iz * cols + ix;
      const b = a + 1;
      const c = a + cols + 1;
      const d = a + cols;
      pushQuad(indices, a, b, c, d);
    }
  }
  return { positions, indices };
}

function appendMeshData(target, part) {
  const base = target.positions.length / 3;
  target.positions.push(...part.positions);
  for (const i of part.indices) target.indices.push(i + base);
}

function sculptWorldPositions(positions, seed) {
  for (let i = 0; i < positions.length; i += 3) {
    const x = positions[i];
    const y = positions[i + 1];
    const z = positions[i + 2];
    const n = fbm(x * 4.2, z * 4.2 + y * 3.1, 24, seed + 201, 3);
    const m = fbm(y * 5.1, x * 3.4 + z * 2.2, 24, seed + 223, 2);
    positions[i] += (n - 0.5) * 0.035;
    positions[i + 1] += (m - 0.5) * 0.028;
    positions[i + 2] += (n + m - 1) * 0.018;
  }
}

function speleothemGeometry(item, seed) {
  const radial = 7;
  const geo = item.kind === 'column'
    ? new THREE.CylinderGeometry(
      item.radiusTop || item.radius * 0.55,
      item.radius,
      item.height,
      radial,
      5,
      false,
    )
    : new THREE.ConeGeometry(item.radius, item.height, radial, 3);
  if (item.kind === 'stalactite') geo.rotateX(Math.PI);
  geo.translate(item.x, item.y, item.z);
  const positions = Array.from(geo.attributes.position.array);
  sculptWorldPositions(positions, seed + Math.round(item.x * 17 + item.z * 13));
  const indices = geo.index ? Array.from(geo.index.array) : [];
  if (!indices.length) {
    for (let i = 0; i < positions.length / 3; i += 3) indices.push(i, i + 1, i + 2);
  }
  geo.dispose();
  return { positions, indices };
}

export function addCaveWalls(root, state, material, cellSize = CELL_SIZE) {
  const solid = state.geometry?.solid_walls || [];
  const edges = state.geometry?.edge_walls || [];
  const opts = {
    cellSize,
    wallHeight: WALL_HEIGHT,
    seed: CAVE_FORM_SEED,
  };
  const occupancy = occupancyFromGeometry(solid, edges, cellSize);
  const faces = exposedCaveFaces(solid, edges, opts);
  const caps = solidCaveCaps(solid, opts);
  const speleo = caveSpeleothems(faces, { ...opts, seed: CAVE_STONE_SEED });

  const packed = { positions: [], indices: [] };
  for (const face of faces) {
    appendMeshData(packed, displacedFaceVertices(face, occupancy, opts));
  }
  for (const cap of caps) {
    appendMeshData(packed, displacedCapVertices(cap, occupancy, opts));
  }
  for (const item of speleo) {
    appendMeshData(packed, speleothemGeometry(item, opts.seed));
  }
  if (!packed.positions.length) return null;

  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(packed.positions, 3));
  geo.setIndex(packed.indices);
  geo.computeVertexNormals();
  const mesh = new THREE.Mesh(geo, material);
  mesh.userData.caveWalls = true;
  root.add(mesh);
  return mesh;
}
