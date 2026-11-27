// Flat, 2D-style path/attack arrows for the 3D tabletop.
//
// Mirrors the 2D VTT canvas conventions: straight green segments between
// cell centers with an arrow head on the destination — but drawn as flat
// ribbons hugging the floor. Normal-terrain steps use a *flowing* dash
// texture (the dashes slide toward the destination so the direction of
// travel reads at a glance); difficult-terrain steps use a fine *dotted*
// style, like the 2D preview's dashed segments.
//
// Kept deliberately cheap: a few quads per step (vs. the old CatmullRom
// TubeGeometry + spheres + cone, which rebuilt thousands of triangles on
// every hover-driven path refresh). The geometry builders are pure so they
// can be unit tested headless; the textures need a canvas and are only
// created in the browser.

import * as THREE from 'three';

export const FLOW_COLOR = 0x2ecc71; // green, like the 2D movement preview
export const ATTACK_COLOR = 0xe63946; // red, like the 2D targeting line

/**
 * Flat ribbon geometry along a polyline (points in the token floor plane,
 * THREE.Vector3 with y ~= 0), split into two BufferGeometries by style:
 *
 *  - ``flow``: normal segments (flowing dash texture)
 *  - ``dot``:  difficult-terrain segments (fine dots), or null when the
 *    path has no difficult steps
 *
 * ``uv.x`` runs along the whole route in ``dashPeriod``-sized repeats (so
 * one texture repeat is one dash/dot period regardless of style split),
 * and ``uv.y`` runs across the width.
 *
 * Each step gets its own quad; interior corners get a small square patch
 * so 45/90-degree turns read as one connected line. ``styles[i]`` styles
 * segment points[i] -> points[i+1] (true = dotted), mirroring the 2D
 * convention where the *entered* cell decides the segment style.
 */
export function ribbonGeometries(points, width, dashPeriod, styles = null) {
  const half = Math.max(0.001, width) / 2;
  const period = Math.max(0.001, dashPeriod);
  const buf = {
    flow: { positions: [], uvs: [] },
    dot: { positions: [], uvs: [] },
  };
  const push = (style, verts, uvPairs) => {
    const t = buf[style ? 'dot' : 'flow'];
    for (const v of verts) t.positions.push(v[0], v[1], v[2]);
    for (const u of uvPairs) t.uvs.push(u[0], u[1]);
  };
  let along = 0;
  for (let i = 0; i + 1 < points.length; i += 1) {
    const a = points[i];
    const b = points[i + 1];
    const len = Math.hypot(b.x - a.x, b.z - a.z);
    const style = styles ? Boolean(styles[i]) : false;
    const dx = b.x - a.x;
    const dz = b.z - a.z;
    const l = len || 1;
    const px = (dz / l) * half;
    const pz = (-dx / l) * half;
    const u0 = along / period;
    const u1 = (along + len) / period;
    const y = a.y;
    push(style, [
      [a.x + px, y, a.z + pz], [b.x + px, y, b.z + pz], [b.x - px, y, b.z - pz],
      [a.x + px, y, a.z + pz], [b.x - px, y, b.z - pz], [a.x - px, y, a.z - pz],
    ], [[u0, 0], [u1, 0], [u1, 1], [u0, 0], [u1, 1], [u0, 1]]);
    along += len;
    if (i + 2 < points.length) {
      // corner patch: square centred on the vertex (dir/perp of this step)
      const fx = (dx / l) * half;
      const fz = (dz / l) * half;
      const u = along / period;
      const uc = u + half / period;
      const c = a;
      push(style, [
        [c.x + fx + px, y, c.z + fz + pz], [c.x + fx - px, y, c.z + fz - pz],
        [c.x - fx - px, y, c.z - fz - pz],
        [c.x + fx + px, y, c.z + fz + pz], [c.x - fx - px, y, c.z - fz - pz],
        [c.x - fx + px, y, c.z - fz + pz],
      ], [[u, 0], [u, 1], [uc, 1], [u, 0], [uc, 1], [u, 0]]);
    }
  }
  const toGeo = (t) => {
    if (!t.positions.length) return null;
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.Float32BufferAttribute(t.positions, 3));
    geo.setAttribute('uv', new THREE.Float32BufferAttribute(t.uvs, 2));
    return geo;
  };
  return { flow: toGeo(buf.flow), dot: styles ? toGeo(buf.dot) : null };
}

/** Single-style convenience wrapper (no difficult-terrain split). */
export function ribbonGeometry(points, width, dashPeriod) {
  return ribbonGeometries(points, width, dashPeriod).flow;
}

/** Flat triangle arrow head lying on the floor, tip at ``tip`` pointing along ``dir``. */
export function arrowHeadGeometry(tip, dir, size) {
  const dx = dir.x;
  const dz = dir.z;
  const len = Math.hypot(dx, dz) || 1;
  const ux = dx / len;
  const uz = dz / len;
  const px = uz;
  const pz = -ux;
  const y = tip.y;
  const back = size * 0.9;
  const wing = size * 0.55;
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute([
    tip.x, y, tip.z,
    tip.x - ux * back + px * wing, y, tip.z - uz * back + pz * wing,
    tip.x - ux * back - px * wing, y, tip.z - uz * back - pz * wing,
  ], 3));
  return geo;
}

/** Flat ring on the floor (selection indicator, melee impact marker). */
export function flatRingGeometry(inner, outer, segments = 48) {
  const geo = new THREE.RingGeometry(inner, outer, segments);
  geo.rotateX(-Math.PI / 2);
  return geo;
}

let dashTexture = null;
let dotTexture = null;

// one dash + gap per repeat; soft rounded dash so it reads as a marker pen
function makeDashTexture(w, h, from, to, radius) {
  const canvas = document.createElement('canvas');
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = '#ffffff';
  ctx.beginPath();
  if (ctx.roundRect) ctx.roundRect(from, 2, to - from, h - 4, radius);
  else ctx.rect(from, 2, to - from, h - 4);
  ctx.fill();
  const tex = new THREE.CanvasTexture(canvas);
  tex.wrapS = THREE.RepeatWrapping;
  tex.wrapT = THREE.ClampToEdgeWrapping;
  tex.anisotropy = 4;
  return tex;
}

/** Shared scrolling dash texture (browser only; needs a canvas).
 *  Near-solid stroke with a narrow travelling gap: reads as the 2D VTT's
 *  solid line, but the gap keeps sliding so direction of travel reads. */
export function flowDashTexture() {
  if (!dashTexture) dashTexture = makeDashTexture(64, 16, 2, 58, 7);
  return dashTexture;
}

/** Fine dotted texture for difficult terrain (browser only). */
export function flowDotTexture() {
  if (!dotTexture) dotTexture = makeDashTexture(48, 16, 2, 14, 6);
  return dotTexture;
}

// per-color material sets, marked `shared` so overlay teardown (disposeObject)
// never disposes the shared textures
const materialSets = new Map();

export function flowMaterials(color) {
  let set = materialSets.get(color);
  if (!set) {
    set = {
      flow: new THREE.MeshBasicMaterial({
        color, map: flowDashTexture(), transparent: true, opacity: 0.92,
        depthWrite: false, side: THREE.DoubleSide,
      }),
      dot: new THREE.MeshBasicMaterial({
        color, map: flowDotTexture(), transparent: true, opacity: 0.95,
        depthWrite: false, side: THREE.DoubleSide,
      }),
      solid: new THREE.MeshBasicMaterial({
        color, transparent: true, opacity: 0.95, depthWrite: false,
        side: THREE.DoubleSide,
      }),
    };
    for (const m of Object.values(set)) m.userData.shared = true;
    materialSets.set(color, set);
  }
  return set;
}

/**
 * Movement preview: { group, flowMaterial, dashPeriod }.
 * Animate with ``flowMaterial.map.offset.x -= dt * repeatsPerSecond``
 * (negative offset drift slides the dashes toward the destination).
 * ``styles``: boolean per segment (true = difficult terrain, dotted).
 */
export function buildFlowArrow(points, {
  width,
  dashPeriod,
  styles = null,
  color = FLOW_COLOR,
} = {}) {
  const group = new THREE.Group();
  const mats = flowMaterials(color);
  const geos = ribbonGeometries(points, width, dashPeriod, styles);
  if (geos.flow) group.add(new THREE.Mesh(geos.flow, mats.flow));
  if (geos.dot) group.add(new THREE.Mesh(geos.dot, mats.dot));

  const last = points[points.length - 1];
  const prev = points[points.length - 2] || last;
  const dir = new THREE.Vector3(last.x - prev.x, 0, last.z - prev.z);
  if (dir.lengthSq() > 1e-8) {
    const head = new THREE.Mesh(arrowHeadGeometry(last, dir, width * 2.4), mats.solid);
    group.add(head);
  }
  return { group, flowMaterial: mats.flow, dashPeriod };
}

/**
 * Attack preview: thicker than the movement arrow, and visually split by
 * attack kind:
 *  - ranged:  flowing red line + solid arrow head on the target cell
 *  - melee:   dotted red line (short reach) + impact ring around the target
 */
export function buildAttackArrow(points, {
  width,
  dashPeriod,
  melee = false,
  valid = true,
  color,
} = {}) {
  const group = new THREE.Group();
  const base = color ?? (valid ? ATTACK_COLOR : 0x888888);
  const mats = flowMaterials(base);
  const styles = melee ? points.slice(0, -1).map(() => true) : null;
  const geos = ribbonGeometries(points, width, dashPeriod, styles);
  if (geos.flow) group.add(new THREE.Mesh(geos.flow, mats.flow));
  if (geos.dot) group.add(new THREE.Mesh(geos.dot, mats.dot));

  const last = points[points.length - 1];
  const prev = points[points.length - 2] || last;
  const dir = new THREE.Vector3(last.x - prev.x, 0, last.z - prev.z);
  if (melee) {
    // impact ring on the target square instead of an arrow head
    const ring = new THREE.Mesh(
      flatRingGeometry(width * 1.6, width * 2.1, 32),
      mats.solid,
    );
    ring.position.set(last.x, last.y + 0.01, last.z);
    group.add(ring);
  } else if (dir.lengthSq() > 1e-8) {
    const head = new THREE.Mesh(arrowHeadGeometry(last, dir, width * 2.6), mats.solid);
    group.add(head);
  }
  return { group, flowMaterial: mats.flow, dashPeriod };
}
