// Physics-driven toppling for 3D figure tokens (prone / dead pose).
//
// When a figure token goes prone we do not just rotate it 90 degrees: the
// figure gets a rigid body built from the convex hull of its actual
// geometry (cannon-es), is nudged in its facing direction, and the fall is
// simulated against the floor. The piece settles according to its own
// shape — boots and cloaks bump the ground, a sword side tips it further —
// then the final pose is cached so map refreshes keep it at rest.
//
// Everything happens in the token group's local space: the group origin is
// the base centre and the floor is y = 0, so stairs / elevated bases need
// no special handling.
//
// Facing: ``yawToDirection`` / ``shortestAngle`` back the "tokens face
// where they walk" behaviour in MapScene.playMove. ``rotate_y`` in the
// model spec aligns the figure's front at yaw 0 (world +Z).

import * as THREE from 'three';
import * as CANNON from 'cannon-es';
import { ConvexGeometry } from 'three/examples/jsm/geometries/ConvexGeometry.js';

export const TOPPLE_MAX_SECONDS = 2.5;
const FIXED_DT = 1 / 60;
// hull input is voxel-quantised so a 40k-vertex miniature becomes a few
// thousand hull-support points at most
const HULL_QUANTIZE = 0.035; // world units (~2 in at 5 ft per square)
const HULL_MAX_POINTS = 2500;

/** Movement-vector -> facing yaw (radians; 0 = world +Z, matches rotate_y). */
export function yawToDirection(dx, dz) {
  if (!dx && !dz) return null;
  return Math.atan2(dx, dz);
}

/** Signed shortest rotation from ``from`` to ``to`` (radians, [-pi, pi]). */
export function shortestAngle(from, to) {
  const twoPi = Math.PI * 2;
  let d = (to - from) % twoPi;
  if (d > Math.PI) d -= twoPi;
  if (d < -Math.PI) d += twoPi;
  return d;
}

/**
 * Collect the figure's surface points in ``group`` local space,
 * deduplicated on a coarse voxel grid (shared cached geometry is read-only).
 * All vertices are scanned; ``maxPoints`` only caps the hull INPUT, which is
 * uniformly stride-sampled and always includes the lowest points so the hull
 * reaches the figure's true sole (a truncated scan could leave the feet out,
 * letting the settled pose sink the real mesh through the floor).
 */
export function gatherHullPoints(group, figure, {
  quantize = HULL_QUANTIZE,
  maxPoints = HULL_MAX_POINTS,
} = {}) {
  group.updateMatrixWorld(true);
  const toFrame = new THREE.Matrix4().copy(group.matrixWorld).invert();
  const seen = new Map();
  const v = new THREE.Vector3();
  figure.traverse((node) => {
    if (!node.isMesh || !node.geometry?.attributes?.position) return;
    const pos = node.geometry.attributes.position;
    const chain = node.matrixWorld;
    for (let i = 0; i < pos.count; i += 1) {
      v.fromBufferAttribute(pos, i).applyMatrix4(chain).applyMatrix4(toFrame);
      const kx = Math.round(v.x / quantize);
      const ky = Math.round(v.y / quantize);
      const kz = Math.round(v.z / quantize);
      const key = `${kx},${ky},${kz}`;
      if (seen.has(key)) continue;
      seen.set(key, new THREE.Vector3(kx * quantize, ky * quantize, kz * quantize));
    }
  });
  const all = [...seen.values()];
  if (all.length <= maxPoints) return all;
  // uniform stride over the (deduped) surface + the 24 lowest points
  const picked = new Map();
  const step = all.length / maxPoints;
  for (let i = 0; i < maxPoints; i += 1) {
    const p = all[Math.floor(i * step)];
    picked.set(`${p.x},${p.y},${p.z}`, p);
  }
  all.slice().sort((a, b) => a.y - b.y).slice(0, 24).forEach((p) => {
    picked.set(`${p.x},${p.y},${p.z}`, p);
  });
  return [...picked.values()];
}

/**
 * Convex hull of ``points`` as cannon-es inputs: { vertices, faces } with
 * outward (CCW) triangle winding, matching cannon's face-normal convention.
 * Returns null for degenerate point clouds (< 4 non-coplanar points).
 */
export function hullFromPoints(points) {
  if (!points || points.length < 4) return null;
  const geo = new ConvexGeometry(points);
  const pos = geo.getAttribute('position');
  if (!pos || pos.count < 12) {
    geo.dispose();
    return null;
  }
  const index = new Map();
  const vertices = [];
  const faces = [];
  const v = new THREE.Vector3();
  for (let i = 0; i < pos.count; i += 3) {
    const face = [];
    for (let j = 0; j < 3; j += 1) {
      v.fromBufferAttribute(pos, i + j);
      const key = `${v.x.toFixed(5)},${v.y.toFixed(5)},${v.z.toFixed(5)}`;
      let vi = index.get(key);
      if (vi === undefined) {
        vi = vertices.length;
        index.set(key, vi);
        vertices.push(new CANNON.Vec3(v.x, v.y, v.z));
      }
      face.push(vi);
    }
    if (new Set(face).size === 3) faces.push(face);
  }
  geo.dispose();
  if (vertices.length < 4 || faces.length < 4) return null;
  // reject degenerate "hulls" (flat / collapsed clouds): zero volume
  let volume = 0;
  for (const f of faces) {
    const a = vertices[f[0]];
    const b = vertices[f[1]];
    const c = vertices[f[2]];
    volume += (a.x * (b.y * c.z - b.z * c.y)
      + a.y * (b.z * c.x - b.x * c.z)
      + a.z * (b.x * c.y - b.y * c.x)) / 6;
  }
  if (Math.abs(volume) < 1e-7) return null;
  return { vertices, faces };
}

/** Simulation world with just a floor plane at local y = 0. */
export function makeToppleWorld() {
  const world = new CANNON.World({ gravity: new CANNON.Vec3(0, -20, 0) });
  world.defaultContactMaterial.friction = 0.7;
  world.defaultContactMaterial.restitution = 0.02;
  const ground = new CANNON.Body({ type: CANNON.Body.STATIC, shape: new CANNON.Plane() });
  ground.quaternion.setFromEuler(-Math.PI / 2, 0, 0);
  world.addBody(ground);
  return world;
}

/**
 * Create the topple simulation for a figure. Returns a toppler
 * ``{ world, body, step(dt), applyTo(figure), pose() }`` or null when the
 * geometry is too degenerate to build a hull (callers fall back to
 * ``fallbackTopplePose``).
 *
 * ``points`` are baked in the token group frame (they include the figure
 * wrapper's current yaw ``baseYaw`` from ``rotate_y`` + facing). Since the
 * pose overwrites the wrapper's quaternion, the reported rotation is
 * post-multiplied by that base yaw so ``pose.position + q·localVertex``
 * reproduces the simulated orientation exactly (the wrapper scale is uniform
 * and commutes with it).
 */
export function createToppler(points, { fallYaw = 0, baseYaw = 0, extraTilt = 0.15, spin = 1.5 } = {}) {
  const hull = hullFromPoints(points);
  if (!hull) return null;
  // cannon assumes the body position IS the centre of mass and computes
  // inertia about the shape origin, so recentre the hull on its centroid;
  // poses are reported back in the original (group-local) frame.
  const com = new CANNON.Vec3(0, 0, 0);
  for (const v of hull.vertices) {
    com.x += v.x; com.y += v.y; com.z += v.z;
  }
  com.x /= hull.vertices.length;
  com.y /= hull.vertices.length;
  com.z /= hull.vertices.length;
  com.y = Math.max(com.y, 0.1); // never let the "CoM" sit below the floor
  for (const v of hull.vertices) {
    v.x -= com.x; v.y -= com.y; v.z -= com.z;
  }
  const world = makeToppleWorld();
  const body = new CANNON.Body({
    mass: 1,
    // cannon-es >=0.20 takes an options object here
    shape: new CANNON.ConvexPolyhedron({ vertices: hull.vertices, faces: hull.faces }),
    linearDamping: 0.05,
    angularDamping: 0.1,
  });
  body.allowSleep = true;
  body.sleepSpeedLimit = 0.25;
  body.sleepTimeLimit = 0.4;
  // Nudge: tilt past the critical tipping angle (computed from the hull's
  // own proportions along the facing direction) so gravity completes the
  // fall; the hull's support geometry decides how it lands. Contact
  // friction eats raw angular velocity quickly, so tilting past the balance
  // point is what actually topples stocky pieces.
  const dir = new CANNON.Vec3(Math.sin(fallYaw), 0, Math.cos(fallYaw));
  let edge = 1e-4;
  for (const v of hull.vertices) {
    const along = v.x * dir.x + v.z * dir.z;
    if (along > edge) edge = along;
  }
  const tilt = Math.atan2(edge, com.y) + extraTilt;
  const axis = new CANNON.Vec3(dir.z, 0, -dir.x); // up x dir, unit
  const nudge = new CANNON.Quaternion();
  nudge.setFromAxisAngle(axis, tilt);
  body.quaternion = nudge.mult(body.quaternion);
  // rotate about the figure's bottom *edge* (not the base centre) so no
  // part of the base starts underground and the solver does not shove the
  // piece back upright
  const pivot = new CANNON.Vec3(com.x + dir.x * edge, 0, com.z + dir.z * edge);
  const off = new CANNON.Vec3(com.x - pivot.x, com.y - pivot.y, com.z - pivot.z);
  const swung = nudge.vmult(off);
  body.position.set(pivot.x + swung.x, pivot.y + swung.y, pivot.z + swung.z);
  body.angularVelocity.set(axis.x * spin, axis.y * spin, axis.z * spin);
  world.addBody(body);

  // The solver may carry the piece a whole square over while it falls. We
  // keep it toppling in place: shift the *rendered* footprint (x/z only) so
  // the settled body stays centred over the token's own square instead of
  // spilling into the square below it or into walls. The physics itself is
  // untouched; the shift is tracked continuously, so live falls slide into
  // place smoothly and there is no jump when the pose is cached.
  const centeredVerts = hull.vertices; // already re-centred on com
  const scratch = new CANNON.Vec3();
  function poseFixup() {
    const q = body.quaternion;
    let minX = Infinity, maxX = -Infinity, minZ = Infinity, maxZ = -Infinity, minY = Infinity;
    for (const u of centeredVerts) {
      q.vmult(u, scratch);
      const wx = body.position.x + scratch.x;
      const wy = body.position.y + scratch.y;
      const wz = body.position.z + scratch.z;
      if (wx < minX) minX = wx;
      if (wx > maxX) maxX = wx;
      if (wz < minZ) minZ = wz;
      if (wz > maxZ) maxZ = wz;
      if (wy < minY) minY = wy;
    }
    return {
      dx: -(minX + maxX) / 2,
      dz: -(minZ + maxZ) / 2,
      // cannon's contact solver lets the body sink a few thousandths into
      // the floor; never let that clip through visually
      lift: Math.max(0, -minY),
    };
  }

  const baseQ = new CANNON.Quaternion();
  if (baseYaw) baseQ.setFromAxisAngle(new CANNON.Vec3(0, 1, 0), baseYaw);

  function renderPose() {
    const q = body.quaternion;
    // the shape was re-centred on com (sim points are v - com), so undoing
    // that centring for rendering means *subtracting* the rotated offset:
    // render point = position + q·v  must equal  body.pos + q·(v - com)
    const o = q.vmult(new CANNON.Vec3(-com.x, -com.y, -com.z));
    const rc = poseFixup();
    // the pose replaces the wrapper quaternion, so bake the wrapper's
    // gather-time yaw into the reported rotation (post-multiply)
    const qOut = baseYaw ? q.mult(baseQ) : q;
    return {
      quaternion: [qOut.x, qOut.y, qOut.z, qOut.w],
      position: [
        body.position.x + o.x + rc.dx,
        body.position.y + o.y + rc.lift,
        body.position.z + o.z + rc.dz,
      ],
    };
  }

  return {
    world,
    body,
    step(dt) {
      this.world.step(FIXED_DT, dt, 3);
    },
    applyTo(figure) {
      const pose = renderPose();
      figure.quaternion.fromArray(pose.quaternion);
      figure.position.set(pose.position[0], pose.position[1], pose.position[2]);
    },
    pose: renderPose,
  };
}

/** True when the body has effectively stopped moving (resting on the floor). */
export function isAtRest(body) {
  return body.sleepState === CANNON.Body.SLEEPING
    || (body.velocity.length() < 0.25 && body.angularVelocity.length() < 0.5);
}

/** Step a simulation to rest synchronously (cold load of an already-prone token). */
export function runTopple(sim, { maxSeconds = TOPPLE_MAX_SECONDS, settleSeconds = 0.5 } = {}) {
  const maxSteps = Math.ceil(maxSeconds / FIXED_DT);
  let calm = 0;
  for (let i = 0; i < maxSteps; i += 1) {
    sim.world.step(FIXED_DT);
    calm = isAtRest(sim.body) ? calm + FIXED_DT : 0;
    if (calm >= settleSeconds) break;
  }
  sim.body.sleep();
  return sim.pose();
}

/**
 * Simple sideways tip for degenerate geometry: quarter turn around the
 * axis perpendicular to the facing direction, kept in place over the
 * token's own square.
 */
export function fallbackTopplePose(fallYaw = 0) {
  const q = new THREE.Quaternion().setFromAxisAngle(
    new THREE.Vector3(Math.cos(fallYaw), 0, -Math.sin(fallYaw)),
    Math.PI / 2,
  );
  return {
    quaternion: [q.x, q.y, q.z, q.w],
    position: [0, 0, 0],
    fallback: true,
  };
}

/** Apply a cached/computed pose to the figure wrapper (group-local space). */
export function applyTopplePose(figure, pose) {
  if (!figure || !pose) return;
  figure.quaternion.fromArray(pose.quaternion);
  figure.position.set(pose.position[0], pose.position[1], pose.position[2]);
  figure.userData.toppled = true;
}
