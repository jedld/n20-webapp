import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { cellCenter } from '../geometry.js';
import { texturedBoxGeometry } from './materials.js';

const gltfLoader = new GLTFLoader();
const gltfCache = new Map();

function loadGltf(url) {
  if (!url) return Promise.resolve(null);
  if (gltfCache.has(url)) return gltfCache.get(url);
  const pending = new Promise((resolve) => {
    gltfLoader.load(url, (gltf) => resolve(gltf.scene), undefined, () => resolve(null));
  });
  gltfCache.set(url, pending);
  return pending;
}

function assetUrl(src) {
  if (!src) return null;
  const text = String(src).replace(/^\/+/, '');
  if (text.startsWith('http://') || text.startsWith('https://')) return src;
  if (text.startsWith('assets/')) return `/${text}`;
  return `/assets/${text}`;
}

export function addSecretDoor(root, assembly, mats) {
  const group = new THREE.Group();
  const slabMat = mats.wall || mats.stone;
  const slab = new THREE.Mesh(
    texturedBoxGeometry(
      assembly.slab.width,
      assembly.slab.height,
      assembly.slab.depth,
      1.4,
      { lockHeight: Boolean(mats.wallLockHeight) },
    ),
    slabMat,
  );
  slab.position.set(assembly.slab.x, assembly.slab.y + assembly.lift, assembly.slab.z);
  group.add(slab);
  root.add(group);
  return group;
}

export function addPortcullis(root, assembly, mats) {
  const iron = mats.rust || mats.iron;
  const group = new THREE.Group();
  for (const box of assembly.frame) {
    const post = new THREE.Mesh(
      texturedBoxGeometry(box.width, box.height, box.depth, 1.2),
      iron,
    );
    post.position.set(box.x, box.y, box.z);
    group.add(post);
  }

  const grate = new THREE.Group();
  const g = assembly.grate;
  const bars = Math.max(5, Number(assembly.bars) || 9);
  const ns = Boolean(g.ns);
  const span = ns ? g.width : g.depth;
  const barRadius = 0.018;
  for (let i = 0; i < bars; i += 1) {
    const t = (i + 0.5) / bars - 0.5;
    const bar = new THREE.Mesh(
      new THREE.CylinderGeometry(barRadius, barRadius, g.height * 0.96, 6),
      iron,
    );
    if (ns) bar.position.set(g.x + t * span, g.y, g.z);
    else bar.position.set(g.x, g.y, g.z + t * span);
    grate.add(bar);
  }
  for (const frac of [0.18, 0.5, 0.82]) {
    const rod = new THREE.Mesh(
      new THREE.CylinderGeometry(barRadius * 0.85, barRadius * 0.85, span * 0.96, 6),
      iron,
    );
    rod.rotation.z = ns ? Math.PI / 2 : 0;
    rod.rotation.x = ns ? 0 : Math.PI / 2;
    rod.position.set(g.x, g.height * frac, g.z);
    grate.add(rod);
  }
  const spikeCount = bars;
  for (let i = 0; i < spikeCount; i += 1) {
    const t = (i + 0.5) / spikeCount - 0.5;
    const spike = new THREE.Mesh(new THREE.ConeGeometry(barRadius * 1.4, 0.08, 6), iron);
    spike.rotation.x = Math.PI;
    if (ns) spike.position.set(g.x + t * span, 0.04, g.z);
    else spike.position.set(g.x, 0.04, g.z + t * span);
    grate.add(spike);
  }
  grate.position.y = assembly.lift;
  group.add(grate);
  root.add(group);
  return group;
}

export function addWindow(root, assembly, mats) {
  const group = new THREE.Group();
  const wallMat = materialForKind(mats, assembly.wallMaterial, mats.wall || mats.stone);
  const frameMat = materialForKind(mats, assembly.windowMaterial, mats.frame || mats.wood);
  const shutterMat = assembly.windowMaterial === 'iron'
    ? (mats.iron || mats.rust)
    : materialForKind(mats, assembly.windowMaterial, mats.wood || mats.doorFace);

  for (const box of [...(assembly.apron || []), ...(assembly.transom || [])]) {
    const mesh = new THREE.Mesh(
      texturedBoxGeometry(box.width, box.height, box.depth, 1.2, {
        lockHeight: Boolean(mats.wallLockHeight),
      }),
      wallMat,
    );
    mesh.position.set(box.x, box.y, box.z);
    group.add(mesh);
  }
  for (const box of assembly.frame || []) {
    const mesh = new THREE.Mesh(
      texturedBoxGeometry(box.width, box.height, box.depth, 1.4),
      frameMat,
    );
    mesh.position.set(box.x, box.y, box.z);
    group.add(mesh);
  }

  const hinge = new THREE.Group();
  hinge.position.set(assembly.hinge.x, assembly.hinge.y, assembly.hinge.z);
  hinge.rotation.y = assembly.yaw;
  const panel = assembly.panel;
  const glass = assembly.coverPane !== 'opaque';
  const paneMat = glass
    ? new THREE.MeshPhysicalMaterial({
      color: 0xcfe8f6,
      transparent: true,
      opacity: 0.38,
      roughness: 0.08,
      metalness: 0.05,
      transmission: 0.72,
      thickness: 0.02,
      side: THREE.DoubleSide,
    })
    : shutterMat;
  const leaf = new THREE.Mesh(
    new THREE.BoxGeometry(panel.width, panel.height, panel.depth),
    paneMat,
  );
  leaf.position.set(panel.localX, panel.localY, panel.localZ);
  hinge.add(leaf);
  group.add(hinge);

  if (assembly.barred && assembly.bars) {
    const iron = mats.iron || mats.rust || frameMat;
    const bars = assembly.bars;
    const count = Math.max(3, Number(bars.count) || 5);
    const ns = Boolean(bars.ns);
    const span = ns ? bars.width : bars.depth;
    for (let i = 0; i < count; i += 1) {
      const t = (i + 0.5) / count - 0.5;
      const bar = new THREE.Mesh(
        new THREE.CylinderGeometry(0.012, 0.012, bars.height * 0.92, 6),
        iron,
      );
      if (ns) bar.position.set(bars.x + t * span, bars.y, bars.z);
      else bar.position.set(bars.x, bars.y, bars.z + t * span);
      group.add(bar);
    }
  }

  root.add(group);
  return group;
}

function materialForKind(mats, kind, fallback) {
  const key = String(kind || '').toLowerCase();
  if (key === 'wood') return mats.wood || mats.doorFace || fallback;
  if (key === 'stone' || key === 'plaster') return mats.wall || mats.stone || fallback;
  if (key === 'iron') return mats.iron || mats.rust || fallback;
  if (key === 'glass') return mats.wood || fallback;
  return fallback;
}

export async function addCampaignModel(root, obj, cellSize, mats) {
  const spec = obj.model || {};
  const src = spec.src;
  if (!src) return null;
  const scene = await loadGltf(assetUrl(src));
  if (!scene) return null;
  const group = new THREE.Group();
  const c = cellCenter(obj.x, obj.y, cellSize);
  group.position.set(c.x, Number(spec.y) || 0, c.z);
  const scale = Number(spec.scale) > 0 ? Number(spec.scale) : 1;
  group.scale.setScalar(scale);
  if (Number.isFinite(Number(spec.rotate_y))) {
    group.rotation.y = Number(spec.rotate_y);
  }
  group.add(scene.clone());
  root.add(group);
  return group;
}
