import { describe, expect, it } from 'vitest';
import * as THREE from 'three';
import {
  applyTopplePose,
  createToppler,
  fallbackTopplePose,
  gatherHullPoints,
  hullFromPoints,
  runTopple,
  shortestAngle,
  yawToDirection,
} from './topple.js';

describe('yawToDirection', () => {
  it('maps movement vectors to facing yaw (0 = +Z)', () => {
    expect(yawToDirection(0, 1)).toBeCloseTo(0);
    expect(yawToDirection(1, 0)).toBeCloseTo(Math.PI / 2);
    expect(yawToDirection(0, -1)).toBeCloseTo(Math.PI);
    expect(yawToDirection(1, 1)).toBeCloseTo(Math.PI / 4);
    expect(yawToDirection(0, 0)).toBeNull();
  });
});

describe('shortestAngle', () => {
  it('wraps across the +/-pi seam the short way', () => {
    expect(shortestAngle(Math.PI - 0.1, -Math.PI + 0.1)).toBeCloseTo(0.2);
    expect(shortestAngle(-Math.PI + 0.1, Math.PI - 0.1)).toBeCloseTo(-0.2);
    expect(shortestAngle(0, Math.PI / 2)).toBeCloseTo(Math.PI / 2);
    expect(shortestAngle(0.5, 0.5)).toBe(0);
  });
});

describe('hullFromPoints', () => {
  it('builds a closed convex hull from a cube cloud', () => {
    const points = [];
    for (let i = 0; i < 5; i++) {
      for (let j = 0; j < 5; j++) {
        for (const [x, y] of [[0, 0], [1, 0], [1, 1], [0, 1]]) {
          points.push(new THREE.Vector3(x, y * 3, i * 0.25));
        }
      }
    }
    const hull = hullFromPoints(points);
    expect(hull).not.toBeNull();
    expect(hull.vertices.length).toBeGreaterThanOrEqual(8);
    expect(hull.faces.length).toBeGreaterThanOrEqual(12);
  });

  it('returns null for degenerate clouds', () => {
    expect(hullFromPoints([])).toBeNull();
    expect(hullFromPoints([
      new THREE.Vector3(0, 0, 0),
      new THREE.Vector3(1, 0, 0),
      new THREE.Vector3(0, 1, 0),
    ])).toBeNull();
    // coplanar cloud
    const flat = [];
    for (let x = 0; x < 4; x++) {
      for (let z = 0; z < 4; z++) flat.push(new THREE.Vector3(x, 0, z));
    }
    expect(hullFromPoints(flat)).toBeNull();
  });
});

describe('gatherHullPoints', () => {
  it('collects mesh surface points in the token group frame', () => {
    const group = new THREE.Group();
    group.position.set(4, 0, 7);
    const figure = new THREE.Group();
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(0.5, 1.2, 0.5));
    figure.add(mesh);
    group.add(figure);
    const points = gatherHullPoints(group, figure);
    // box centre sits at the group origin-ish: all points near the origin
    // even though the group is at (4, 0, 7)
    for (const p of points) {
      expect(Math.abs(p.x)).toBeLessThan(1);
      expect(Math.abs(p.z)).toBeLessThan(1);
    }
    const ys = points.map((p) => p.y);
    expect(Math.min(...ys)).toBeCloseTo(-0.6, 1);
    expect(Math.max(...ys)).toBeCloseTo(0.6, 1);
  });

  it('keeps the lowest points when the hull input is capped', () => {
    // a dense upper body mesh blows past maxPoints first; the short "feet"
    // mesh below it must still reach the hull, or the settled pose sinks the
    // real mesh through the floor
    const group = new THREE.Group();
    const figure = new THREE.Group();
    const body = new THREE.Mesh(new THREE.BoxGeometry(0.5, 1.2, 0.5, 40, 80, 40));
    body.position.y = 0.8; // spans y 0.2..1.4
    figure.add(body);
    const feet = new THREE.Mesh(new THREE.BoxGeometry(0.4, 0.04, 0.4));
    feet.position.y = 0.02; // sole at y = 0
    figure.add(feet);
    group.add(figure);
    const points = gatherHullPoints(group, figure, { maxPoints: 800 });
    expect(points.length).toBeLessThanOrEqual(824 + 800);
    const minY = Math.min(...points.map((p) => p.y));
    expect(minY).toBeLessThan(0.1); // feet made it into the hull input
  });
});

describe('topple simulation', () => {
  // a standing slab: 0.6 x 0.6 footprint, 1.5 tall, base at y = 0
  function slabPoints() {
    const pts = [];
    const w = 0.3;
    const h = 1.5;
    for (let y = 0; y <= h; y += 0.15) {
      for (const x of [-w, w]) {
        for (const z of [-w, w]) pts.push(new THREE.Vector3(x, y, z));
      }
    }
    return pts;
  }

  it('topples a standing slab until it rests on its side', () => {
    const sim = createToppler(slabPoints(), { fallYaw: 0 });
    expect(sim).not.toBeNull();
    const pose = runTopple(sim);
    // figure up axis (local +Y) must end up roughly horizontal
    const q = new THREE.Quaternion(pose.quaternion[0], pose.quaternion[1],
      pose.quaternion[2], pose.quaternion[3]);
    const up = new THREE.Vector3(0, 1, 0).applyQuaternion(q);
    expect(Math.abs(up.y)).toBeLessThan(0.4);
    // body came to rest (not still bouncing/awake) and lies near the floor —
    // but not through it (guards against silent collision breakage)
    expect(sim.body.sleepState).toBe(2); // CANNON.Body.SLEEPING
    expect(pose.position[1]).toBeGreaterThanOrEqual(0);
    expect(pose.position[1]).toBeLessThan(0.6);
    // ... and the bulk of the mass fell toward its facing direction (+Z for yaw 0)
    const center = slabPoints().reduce(
      (acc, p) => acc.add(p.clone().applyQuaternion(q)),
      new THREE.Vector3(0, 0, 0),
    );
    expect(center.z / slabPoints().length).toBeGreaterThan(0.25);
  });

  it('the applied pose renders where the simulation collided: on the floor and over its own square', () => {
    const pts = slabPoints();
    const sim = createToppler(pts, { fallYaw: 0 });
    const pose = runTopple(sim);
    const q = new THREE.Quaternion(pose.quaternion[0], pose.quaternion[1],
      pose.quaternion[2], pose.quaternion[3]);
    const base = new THREE.Vector3(pose.position[0], pose.position[1], pose.position[2]);
    // every rendered vertex = pose.position + q·v must sit on/above the floor
    // (catches a sign flip between the com-recentred sim hull and the pose)
    let minY = Infinity, minX = Infinity, maxX = -Infinity, minZ = Infinity, maxZ = -Infinity;
    for (const p of pts) {
      const r = p.clone().applyQuaternion(q).add(base);
      if (r.y < minY) minY = r.y;
      if (r.x < minX) minX = r.x;
      if (r.x > maxX) maxX = r.x;
      if (r.z < minZ) minZ = r.z;
      if (r.z > maxZ) maxZ = r.z;
    }
    expect(minY).toBeGreaterThanOrEqual(-0.01); // never clips through the floor
    // the settled footprint stays centred on the token's own square rather
    // than toppling a whole square over into the next one
    expect(Math.abs((minX + maxX) / 2)).toBeLessThan(0.1);
    expect(Math.abs((minZ + maxZ) / 2)).toBeLessThan(0.1);
  });

  it('createToppler returns null for degenerate geometry', () => {
    expect(createToppler([new THREE.Vector3(0, 0, 0), new THREE.Vector3(1, 0, 0)])).toBeNull();
  });

  it('the pose compensates the figure wrapper yaw (rotate_y + facing)', () => {
    // hull points are baked with the wrapper's yaw; applyTopplePose replaces
    // that quaternion, so the reported pose must post-multiply it back in:
    // render point = pose.position + poseQuat·localVertex reproduces the sim
    //
    // rectangular slab: long axis along LOCAL Z (so a 90-degree wrapper yaw
    // moves the long axis to group X — a missing yaw compensation would keep
    // the narrow axis there instead)
    const localPts = [];
    const h = 1.5;
    for (let y = 0; y <= h; y += 0.15) {
      for (const x of [-0.2, 0.2]) {
        for (const z of [-0.5, 0.5]) localPts.push(new THREE.Vector3(x, y, z));
      }
    }
    const yaw = Math.PI / 2;
    const groupPts = localPts.map((p) => p.clone().applyAxisAngle(new THREE.Vector3(0, 1, 0), yaw));
    const sim = createToppler(groupPts, { fallYaw: 0, baseYaw: yaw });
    const pose = runTopple(sim);
    const q = new THREE.Quaternion(pose.quaternion[0], pose.quaternion[1],
      pose.quaternion[2], pose.quaternion[3]);
    const base = new THREE.Vector3(pose.position[0], pose.position[1], pose.position[2]);
    let minY = Infinity, minX = Infinity, maxX = -Infinity, minZ = Infinity, maxZ = -Infinity;
    for (const p of localPts) {
      const r = p.clone().applyQuaternion(q).add(base);
      if (r.y < minY) minY = r.y;
      if (r.x < minX) minX = r.x;
      if (r.x > maxX) maxX = r.x;
      if (r.z < minZ) minZ = r.z;
      if (r.z > maxZ) maxZ = r.z;
    }
    expect(minY).toBeGreaterThanOrEqual(-0.01);
    expect(Math.abs((minX + maxX) / 2)).toBeLessThan(0.1);
    expect(Math.abs((minZ + maxZ) / 2)).toBeLessThan(0.1);
    // the piece's long axis must lie along group X (the yawed orientation),
    // and the 1.5 body it fell along must run along the fall direction Z
    expect(maxX - minX).toBeGreaterThan(0.9);
    expect(maxZ - minZ).toBeGreaterThan(1.2);
  });

  it('fallback pose tips the figure onto its side', () => {
    const pose = fallbackTopplePose(0);
    const q = new THREE.Quaternion(pose.quaternion[0], pose.quaternion[1],
      pose.quaternion[2], pose.quaternion[3]);
    const up = new THREE.Vector3(0, 1, 0).applyQuaternion(q);
    expect(Math.abs(up.y)).toBeLessThan(0.2);
  });

  it('applyTopplePose writes the pose onto the figure wrapper', () => {
    const figure = new THREE.Group();
    applyTopplePose(figure, fallbackTopplePose(Math.PI / 2));
    expect(figure.userData.toppled).toBe(true);
    // fallback stays in place over the token square
    expect(Math.abs(figure.position.x)).toBeLessThan(0.01);
    expect(Math.abs(figure.position.z)).toBeLessThan(0.01);
  });
});
