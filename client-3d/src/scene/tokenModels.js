// Campaign 3D token models (HeroForge-style figures) for the 3D VTT.
//
// The server sends ``entity.model`` on ``/map_state`` entries when the DM
// mapped the entity (uid / NPC type / name) to a file under
// ``<campaign>/assets`` — see natural20/web/vtt3d_catalog.py. Specs:
//   { src, height?, scale?, y?, rotate_y?, color?, label? }  (feet / degrees)
//
// Supported sources: GLB/glTF (best: full color textures), OBJ(+MTL),
// and STL (mono geometry, tinted via ``color``). Anything else falls back
// to the regular billboard token.

import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { OBJLoader } from 'three/examples/jsm/loaders/OBJLoader.js';
import { MTLLoader } from 'three/examples/jsm/loaders/MTLLoader.js';
import { STLLoader } from 'three/examples/jsm/loaders/STLLoader.js';

const modelCache = new Map();

export function tokenModelUrl(src) {
  if (!src) return null;
  const text = String(src).replace(/^\/+/, '');
  if (text.startsWith('http://') || text.startsWith('https://')) return text;
  if (text.startsWith('assets/')) return `/${text}`;
  return `/assets/${text}`;
}

export function modelExt(src) {
  const match = /\.[a-z0-9]+$/i.exec(String(src || ''));
  return match ? match[0].toLowerCase() : '';
}

/** Feet -> world units (one unit == one map square). */
export function feetToUnits(feet, feetPerGrid) {
  const fpGrid = Number(feetPerGrid) || 5;
  return (Number(feet) || 0) / fpGrid;
}

/** Default figure height in feet for a creature of ``size`` squares. */
export function defaultFigureHeight(size) {
  const squares = Math.max(1, Number(size) || 1);
  return 6 * Math.sqrt(squares); // Medium ~6 ft, Large (2 sq) ~8.5 ft...
}

/**
 * Pick the up axis from the model's bounding box and rotate so +Y is up.
 * Many 3D-print uploads (HeroForge STL/OBJ) are Z-up. Returns true when a
 * rotation was applied.
 */
export function normalizeUpAxis(root, box) {
  const size = new THREE.Vector3();
  box.getSize(size);
  if (size.z > size.y && size.z > size.x) {
    root.rotation.x = -Math.PI / 2; // Z-up -> Y-up
    root.updateMatrixWorld(true);
    return true;
  }
  return false;
}

/**
 * Fit a loaded model into a figure of ``heightUnits`` world units standing
 * on y = 0 at the group origin. Shared with the cache: returns a configured
 * clone-free wrapper the caller adds to the token group.
 */
export function fitFigure(root, heightUnits) {
  normalizeUpAxis(root, new THREE.Box3().setFromObject(root));
  const box = new THREE.Box3().setFromObject(root);
  const size = new THREE.Vector3();
  const center = new THREE.Vector3();
  box.getSize(size);
  box.getCenter(center);
  const height = Math.max(size.y, 0.001);
  const scale = (Number(heightUnits) > 0 ? heightUnits : 1) / height;
  const wrapper = new THREE.Group();
  wrapper.scale.setScalar(scale);
  root.position.set(-center.x, -box.min.y, -center.z);
  wrapper.add(root);
  wrapper.userData.figureHeight = Number(heightUnits) || height;
  return wrapper;
}

/** Tint untextured meshes (mono STL uploads) with the assignment color. */
export function tintFigure(root, color) {
  if (!color || !/^(#[0-9a-f]{3,8}|[a-z]+)$/i.test(String(color).trim())) return;
  let tint;
  try {
    tint = new THREE.Color(String(color).trim());
  } catch (err) {
    return;
  }
  root.traverse((node) => {
    if (!node.isMesh || !node.material) return;
    const materials = Array.isArray(node.material) ? node.material : [node.material];
    materials.forEach((mat) => {
      if (mat.map || mat.emissiveMap) return; // keep textured figures as-is
      node.material = mat.clone();
      node.material.color = tint;
    });
  });
}

function loadObjectByExt(url) {
  const ext = modelExt(url);
  if (ext === '.glb' || ext === '.gltf') {
    return new Promise((resolve) => {
      new GLTFLoader().load(url, (gltf) => resolve(gltf.scene || null), undefined, () => resolve(null));
    });
  }
  if (ext === '.obj') {
    const mtlUrl = url.replace(/\.obj$/i, '.mtl');
    return new Promise((resolve) => {
      new MTLLoader().load(mtlUrl, (materials) => {
        materials.preload();
        new OBJLoader().setMaterials(materials).load(
          url, (obj) => resolve(obj), undefined, () => resolve(null),
        );
      }, undefined, () => {
        // no sidecar MTL: OBJ geometry still renders with a default material
        new OBJLoader().load(url, (obj) => resolve(obj), undefined, () => resolve(null));
      });
    });
  }
  if (ext === '.stl') {
    return new Promise((resolve) => {
      new STLLoader().load(url, (geometry) => {
        if (!geometry) return resolve(null);
        geometry.computeVertexNormals();
        const mesh = new THREE.Mesh(
          geometry,
          new THREE.MeshStandardMaterial({ color: 0xb8b8b8, roughness: 0.55, metalness: 0.15 }),
        );
        resolve(mesh);
      }, undefined, () => resolve(null));
    });
  }
  return Promise.resolve(null);
}

/** Load (and cache) a model file. Resolves null for missing/bad files. */
export function loadTokenModel(url) {
  if (!url) return Promise.resolve(null);
  if (modelCache.has(url)) return modelCache.get(url);
  const pending = loadObjectByExt(url);
  modelCache.set(url, pending);
  pending.catch(() => modelCache.delete(url));
  return pending;
}

/**
 * Build a ready-to-add figure group for one entity.model spec.
 * ``load`` is injectable for tests. Resolves null when the model cannot be
 * used (caller keeps the 2D token).
 */
export async function buildFigure(model, { feetPerGrid, size, load = loadTokenModel } = {}) {
  if (!model || !model.src) return null;
  const url = tokenModelUrl(model.src);
  const source = await load(url);
  if (!source) return null;
  const root = source.clone(true);
  const heightFeet = Number(model.height) > 0
    ? Number(model.height)
    : defaultFigureHeight(size);
  const heightUnits = feetToUnits(heightFeet, feetPerGrid);
  const figure = fitFigure(root, heightUnits);
  if (Number(model.scale) > 0) figure.scale.multiplyScalar(Number(model.scale));
  // Enlarge/Reduce and other size_override effects: the engine sends
  // ``size_scale`` when the creature's current size category differs from
  // its natural one. An explicit ``height`` was authored at natural size,
  // so scale the figure by the factor; the default (square-count) height
  // already tracks the current size and must not double-count.
  const sizeScale = Number(model.size_scale) > 0 ? Number(model.size_scale) : 1;
  if (Number(model.height) > 0 && sizeScale !== 1) figure.scale.multiplyScalar(sizeScale);
  const yOffset = Number(model.y);
  if (Number.isFinite(yOffset)) figure.position.y += feetToUnits(yOffset, feetPerGrid);
  const yaw = Number(model.rotate_y);
  if (Number.isFinite(yaw)) figure.rotation.y = THREE.MathUtils.degToRad(yaw);
  // tintFigure keeps textured materials untouched, so mono figures
  // (raw or converted STL uploads) pick up the DM's color
  tintFigure(figure, model.color);
  // geometry/materials are shared with the module cache: the scene's
  // per-refresh disposeObject() must skip them
  figure.traverse((node) => {
    if (node.geometry) node.geometry.userData = { ...node.geometry.userData, shared: true };
    const mats = Array.isArray(node.material) ? node.material : (node.material ? [node.material] : []);
    mats.forEach((mat) => {
      mat.userData = { ...mat.userData, shared: true };
      // printed/tabletop figures can have open surfaces (e.g. a flat base);
      // double-sided keeps them from looking like hollow shells
      mat.side = THREE.DoubleSide;
    });
  });
  figure.userData.label = model.label || null;
  return figure;
}
