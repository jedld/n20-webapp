import * as THREE from 'three';
import { cellCenter, chasmDepthWorld, nearestWallEdge, stairStepBoxes, torchWorldOffset } from '../geometry.js';
import {
  makeGothicWallTexture,
  makeMansionWallTexture,
  makeOfficeWallTexture,
  normalizeWallStyle,
  texturedBoxGeometry,
} from './materials.js';
import {
  fixtureKind,
  lightColor,
  lightOrigin,
  parseHexColor,
  pointLightParams,
  shouldEmitLight,
  yawFromEdge,
  yawFromFacing,
} from './fixtureKinds.js';

function stairMaterial(obj, mats) {
  const raw = String(obj.style || '').trim().toLowerCase();
  if (!raw) return mats?.wall || mats?.stone;
  const style = normalizeWallStyle(raw);
  if (style === 'wood') return mats?.wood || mats?.wall;
  if (style === 'dungeon') return mats?.stone || mats?.wall;
  if (style === mats?.style) return mats?.wall || mats?.stone;
  const makers = {
    mansion: makeMansionWallTexture,
    gothic: makeGothicWallTexture,
    office: makeOfficeWallTexture,
  };
  const make = makers[style];
  if (!make) return mats?.wall || mats?.stone;
  mats._stairStyles = mats._stairStyles || {};
  if (!mats._stairStyles[style]) {
    const map = make();
    mats._stairStyles[style] = new THREE.MeshStandardMaterial({
      color: 0xffffff,
      map,
      bumpMap: map,
      bumpScale: style === 'gothic' ? 0.07 : 0.04,
      roughness: 0.86,
      metalness: 0.02,
    });
  }
  return mats._stairStyles[style];
}

function annularWedgeGeometry(innerR, outerR, height, theta0, theta1) {
  const segs = Math.max(5, Math.ceil(Math.abs(theta1 - theta0) / 0.09));
  const hw = height * 0.5;
  const positions = [];
  const uvs = [];
  const indices = [];
  const inner = Math.max(innerR, 1e-4);
  for (let i = 0; i < segs; i += 1) {
    const a0 = theta0 + (theta1 - theta0) * (i / segs);
    const a1 = theta0 + (theta1 - theta0) * ((i + 1) / segs);
    const c0 = Math.cos(a0);
    const s0 = Math.sin(a0);
    const c1 = Math.cos(a1);
    const s1 = Math.sin(a1);
    const b = positions.length / 3;
    const u0 = i / segs;
    const u1 = (i + 1) / segs;
    const pts = [
      [inner * c0, -hw, inner * s0, u0, 0],
      [outerR * c0, -hw, outerR * s0, u0, 0.15],
      [outerR * c1, -hw, outerR * s1, u1, 0.15],
      [inner * c1, -hw, inner * s1, u1, 0],
      [inner * c0, hw, inner * s0, u0, 0.85],
      [outerR * c0, hw, outerR * s0, u0, 1],
      [outerR * c1, hw, outerR * s1, u1, 1],
      [inner * c1, hw, inner * s1, u1, 0.85],
    ];
    for (const p of pts) {
      positions.push(p[0], p[1], p[2]);
      uvs.push(p[3], p[4]);
    }
    const faces = [
      [4, 5, 6], [4, 6, 7],
      [0, 2, 1], [0, 3, 2],
      [1, 2, 6], [1, 6, 5],
      [0, 4, 7], [0, 7, 3],
      [0, 1, 5], [0, 5, 4],
      [3, 7, 6], [3, 6, 2],
    ];
    for (const f of faces) {
      indices.push(b + f[0], b + f[1], b + f[2]);
    }
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geo.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2));
  geo.setIndex(indices);
  geo.computeVertexNormals();
  return geo;
}

function addStairs(group, obj, mats, cellSize, opts = {}) {
  const origin = cellCenter(obj.x, obj.y, cellSize);
  const openWell = obj.open_well === true || obj.openWell === true;
  const wallAttached = obj.wall_attached === true || obj.wallAttached === true;
  const boxes = stairStepBoxes(obj.squares || [[obj.x, obj.y]], {
    heightFt: obj.height,
    feetPerGrid: opts.feetPerGrid,
    cellSize,
    solid: obj.solid !== false,
    openWell,
    wallAttached,
  });
  const mat = stairMaterial(obj, mats);
  const wellDark = new THREE.MeshStandardMaterial({
    color: 0x0c0908,
    roughness: 1,
    metalness: 0,
    side: THREE.DoubleSide,
  });
  for (const box of boxes) {
    let mesh;
    if (box.shape === 'wedge') {
      mesh = new THREE.Mesh(
        annularWedgeGeometry(box.innerR, box.outerR, box.height, box.theta0, box.theta1),
        mat,
      );
      mesh.position.set(box.cx - origin.x, box.y, box.cz - origin.z);
    } else if (box.shape === 'newel' || box.part === 'newel') {
      const radius = box.radius || Math.min(box.width, box.depth) * 0.5;
      mesh = new THREE.Mesh(
        new THREE.CylinderGeometry(radius, radius, box.height, 14),
        mat,
      );
      mesh.position.set(box.x - origin.x, box.y, box.z - origin.z);
    } else if (box.shape === 'well_void') {
      mesh = new THREE.Mesh(new THREE.CircleGeometry(box.radius, 24), wellDark);
      mesh.rotation.x = -Math.PI / 2;
      mesh.position.set(box.x - origin.x, box.y, box.z - origin.z);
    } else if (box.shape === 'well_liner') {
      mesh = new THREE.Mesh(
        new THREE.CylinderGeometry(box.radius, box.radius, box.height, 24, 1, true),
        wellDark,
      );
      mesh.position.set(box.x - origin.x, box.y, box.z - origin.z);
    } else if (box.shape === 'well_wall') {
      mesh = new THREE.Mesh(
        texturedBoxGeometry(box.width, box.height, box.depth, 1.6),
        mat,
      );
      mesh.position.set(box.x - origin.x, box.y, box.z - origin.z);
    } else {
      mesh = new THREE.Mesh(
        texturedBoxGeometry(box.width, box.height, box.depth, 1.6),
        mat,
      );
      mesh.position.set(box.x - origin.x, box.y, box.z - origin.z);
      mesh.rotation.y = box.yaw || 0;
    }
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    group.add(mesh);
  }
}

function addPitShaft(group, { depth = 0.42, spikes = false, mats, opening = 0.7 }) {
  const stone = mats?.stone;
  const dark = new THREE.MeshStandardMaterial({ color: 0x140f0c, roughness: 1, metalness: 0 });
  const wallT = 0.055;
  const wallH = depth;
  const inner = opening;
  const n = new THREE.Mesh(new THREE.BoxGeometry(inner + wallT * 2, wallH, wallT), stone || dark);
  n.position.set(0, -wallH / 2, -inner / 2 - wallT / 2);
  const s = n.clone();
  s.position.z = inner / 2 + wallT / 2;
  const w = new THREE.Mesh(new THREE.BoxGeometry(wallT, wallH, inner), stone || dark);
  w.position.set(-inner / 2 - wallT / 2, -wallH / 2, 0);
  const e = w.clone();
  e.position.x = inner / 2 + wallT / 2;
  group.add(n, s, w, e);
  const floor = new THREE.Mesh(new THREE.BoxGeometry(inner, 0.04, inner), dark);
  floor.position.y = -depth + 0.02;
  group.add(floor);
  if (spikes && mats?.iron) {
    for (let i = 0; i < 7; i += 1) {
      const spike = new THREE.Mesh(new THREE.ConeGeometry(0.035, 0.22, 6), mats.rust || mats.iron);
      const angle = (i / 7) * Math.PI * 2;
      const r = 0.16 + (i % 2) * 0.08;
      spike.position.set(Math.cos(angle) * r, -depth + 0.14, Math.sin(angle) * r);
      group.add(spike);
    }
  }
}

function addTrapDoor(group, obj, mats) {
  const opened = Boolean(obj.opened);
  const wood = mats.wood;
  const frame = new THREE.Mesh(texturedBoxGeometry(0.86, 0.03, 0.86, 1.6), wood);
  frame.position.y = 0.015;
  group.add(frame);
  const hinge = new THREE.Group();
  hinge.position.set(0, 0.03, -0.34);
  hinge.rotation.x = opened ? -Math.PI * 0.78 : 0;
  const lid = new THREE.Mesh(texturedBoxGeometry(0.68, 0.032, 0.68, 2.2), wood);
  lid.position.set(0, 0.016, 0.34);
  hinge.add(lid);
  const ring = new THREE.Mesh(new THREE.TorusGeometry(0.045, 0.01, 8, 12), mats.iron);
  ring.position.set(0.16, 0.04, 0.52);
  hinge.add(ring);
  group.add(hinge);
  if (obj.locked && !opened) {
    const lock = new THREE.Mesh(new THREE.BoxGeometry(0.08, 0.05, 0.045), mats.iron);
    lock.position.set(0, 0.055, 0.22);
    group.add(lock);
    const chain = new THREE.Mesh(new THREE.TorusGeometry(0.055, 0.012, 6, 10), mats.rust || mats.iron);
    chain.rotation.x = Math.PI / 2;
    chain.position.set(0, 0.07, 0.22);
    group.add(chain);
  }
  if (opened) {
    addPitShaft(group, { depth: 0.42, spikes: false, mats });
  }
}

function addPitTrap(group, obj, mats) {
  const sprung = Boolean(obj.activated);
  const disarmed = Boolean(obj.disarmed);
  if (!sprung && !disarmed) {
    const cover = new THREE.Mesh(texturedBoxGeometry(0.92, 0.03, 0.92, 1.4), mats.stone);
    cover.position.y = 0.015;
    group.add(cover);
    const crack = new THREE.Mesh(
      new THREE.BoxGeometry(0.7, 0.01, 0.04),
      new THREE.MeshStandardMaterial({ color: 0x1a120c, roughness: 1 }),
    );
    crack.position.y = 0.032;
    crack.rotation.y = 0.4;
    group.add(crack);
    return;
  }
  addPitShaft(group, { depth: 0.48, spikes: sprung && !disarmed, mats });
}

function addChasm(group, obj, mats, opts = {}) {
  const depth = chasmDepthWorld(obj, {
    feetPerGrid: opts.feetPerGrid,
    cellSize: opts.cellSize,
  });
  addPitShaft(group, { depth, spikes: false, mats, opening: 0.94 });
}

function addChest(group, obj, mats) {
  const bodyW = 0.56;
  const bodyH = 0.28;
  const bodyD = 0.36;
  const body = new THREE.Mesh(texturedBoxGeometry(bodyW, bodyH, bodyD, 2.2), mats.wood);
  body.position.y = bodyH / 2 + 0.02;
  group.add(body);

  for (const z of [-bodyD * 0.28, bodyD * 0.28]) {
    const band = new THREE.Mesh(new THREE.BoxGeometry(bodyW + 0.02, bodyH * 0.12, 0.03), mats.iron);
    band.position.set(0, bodyH * 0.55, z);
    group.add(band);
  }

  const hinge = new THREE.Group();
  hinge.position.set(0, bodyH + 0.02, -bodyD / 2);
  hinge.rotation.x = obj.opened ? -Math.PI * 0.72 : 0;
  const lid = new THREE.Mesh(texturedBoxGeometry(bodyW + 0.02, 0.07, bodyD + 0.02, 2.2), mats.wood);
  lid.position.set(0, 0.035, bodyD / 2);
  hinge.add(lid);
  const lidBand = new THREE.Mesh(new THREE.BoxGeometry(bodyW + 0.04, 0.02, 0.03), mats.iron);
  lidBand.position.set(0, 0.06, bodyD * 0.15);
  hinge.add(lidBand);
  group.add(hinge);

  if (obj.opened) {
    const cavity = new THREE.Mesh(
      new THREE.BoxGeometry(bodyW * 0.78, 0.12, bodyD * 0.7),
      new THREE.MeshStandardMaterial({ color: 0x1a120c, roughness: 1, metalness: 0 }),
    );
    cavity.position.y = bodyH * 0.72;
    group.add(cavity);
  }

  if (obj.locked) {
    const lock = new THREE.Mesh(new THREE.BoxGeometry(0.07, 0.09, 0.045), mats.iron);
    lock.position.set(0, bodyH * 0.62, bodyD / 2 + 0.03);
    group.add(lock);
    const shackle = new THREE.Mesh(new THREE.TorusGeometry(0.028, 0.007, 8, 12, Math.PI), mats.iron);
    shackle.rotation.z = Math.PI;
    shackle.position.set(0, bodyH * 0.62 + 0.055, bodyD / 2 + 0.03);
    group.add(shackle);
  }
}

function addBarrel(group, mats) {
  const barrel = new THREE.Mesh(new THREE.CylinderGeometry(0.18, 0.2, 0.42, 18), mats.wood);
  barrel.position.y = 0.22;
  group.add(barrel);
  for (const y of [0.08, 0.22, 0.36]) {
    const hoop = new THREE.Mesh(new THREE.TorusGeometry(0.205, 0.012, 8, 18), mats.iron);
    hoop.rotation.x = Math.PI / 2;
    hoop.position.y = y;
    group.add(hoop);
  }
  const lid = new THREE.Mesh(new THREE.CylinderGeometry(0.175, 0.175, 0.03, 16), mats.wood);
  lid.position.y = 0.43;
  group.add(lid);
}

function addCrate(group, mats, { height = 0.34, width = 0.48 } = {}) {
  const crate = new THREE.Mesh(texturedBoxGeometry(width, height, width, 1.6), mats.wood);
  crate.position.y = height / 2;
  group.add(crate);
  const rim = new THREE.Mesh(new THREE.BoxGeometry(width + 0.02, 0.04, width + 0.02), mats.iron);
  rim.position.y = height - 0.02;
  group.add(rim);
}

function addTree(group) {
  const trunk = new THREE.Mesh(
    new THREE.CylinderGeometry(0.06, 0.08, 0.45, 8),
    new THREE.MeshStandardMaterial({ color: 0x5a3a22, roughness: 0.95 }),
  );
  trunk.position.y = 0.22;
  group.add(trunk);
  const foliage = new THREE.Mesh(
    new THREE.ConeGeometry(0.28, 0.55, 10),
    new THREE.MeshStandardMaterial({ color: 0x2f6b38, roughness: 0.85 }),
  );
  foliage.position.y = 0.62;
  group.add(foliage);
}

function addBriar(group) {
  const mat = new THREE.MeshStandardMaterial({ color: 0x3d5a28, roughness: 0.95 });
  for (let i = 0; i < 5; i += 1) {
    const spike = new THREE.Mesh(new THREE.ConeGeometry(0.06, 0.28, 5), mat);
    const angle = (i / 5) * Math.PI * 2;
    spike.position.set(Math.cos(angle) * 0.12, 0.14, Math.sin(angle) * 0.12);
    spike.rotation.z = Math.cos(angle) * 0.35;
    spike.rotation.x = Math.sin(angle) * 0.35;
    group.add(spike);
  }
}

function addTeleporter(group, obj) {
  const color = parseHexColor(obj.teleporter_marker_color, 0x3d8bfd);
  const glow = new THREE.MeshBasicMaterial({
    color,
    transparent: true,
    opacity: 0.55,
    depthWrite: false,
  });
  const ring = new THREE.Mesh(new THREE.TorusGeometry(0.28, 0.035, 8, 24), glow);
  ring.rotation.x = -Math.PI / 2;
  ring.position.y = 0.04;
  group.add(ring);
  const disc = new THREE.Mesh(
    new THREE.CircleGeometry(0.24, 24),
    new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.22, depthWrite: false }),
  );
  disc.rotation.x = -Math.PI / 2;
  disc.position.y = 0.03;
  group.add(disc);
  const column = new THREE.Mesh(
    new THREE.CylinderGeometry(0.04, 0.04, 0.55, 8),
    new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.28, depthWrite: false }),
  );
  column.position.y = 0.28;
  group.add(column);
}

function addFlame(group, { y = 0.4, scale = 1 } = {}) {
  const outer = new THREE.Mesh(
    new THREE.ConeGeometry(0.055 * scale, 0.18 * scale, 8),
    new THREE.MeshBasicMaterial({ color: 0xff7a2b, transparent: true, opacity: 0.92 }),
  );
  outer.position.y = y;
  group.add(outer);
  const inner = new THREE.Mesh(
    new THREE.ConeGeometry(0.028 * scale, 0.11 * scale, 6),
    new THREE.MeshBasicMaterial({ color: 0xfff1b0 }),
  );
  inner.position.y = y - 0.02 * scale;
  group.add(inner);
}

function addTorch(group, mats, { standing = false } = {}) {
  if (standing) {
    const post = new THREE.Mesh(new THREE.CylinderGeometry(0.03, 0.035, 0.95, 8), mats.wood);
    post.position.y = 0.48;
    group.add(post);
    const cup = new THREE.Mesh(new THREE.CylinderGeometry(0.045, 0.03, 0.08, 8), mats.iron);
    cup.position.y = 0.98;
    group.add(cup);
    addFlame(group, { y: 1.08, scale: 0.85 });
    return 1.12;
  }
  const bracket = new THREE.Mesh(new THREE.BoxGeometry(0.08, 0.06, 0.12), mats.iron);
  bracket.position.set(0, 1.12, -0.02);
  group.add(bracket);
  const arm = new THREE.Mesh(new THREE.BoxGeometry(0.04, 0.04, 0.16), mats.iron);
  arm.position.set(0, 1.12, 0.08);
  group.add(arm);
  const stick = new THREE.Mesh(new THREE.CylinderGeometry(0.022, 0.028, 0.28, 8), mats.wood);
  stick.rotation.x = Math.PI / 2.4;
  stick.position.set(0, 1.18, 0.14);
  group.add(stick);
  const cup = new THREE.Mesh(new THREE.CylinderGeometry(0.04, 0.028, 0.06, 8), mats.iron);
  cup.position.set(0, 1.28, 0.2);
  group.add(cup);
  addFlame(group, { y: 1.36, scale: 0.7 });
  return 1.4;
}

function addCampfire(group, mats) {
  const ash = new THREE.Mesh(
    new THREE.CylinderGeometry(0.22, 0.24, 0.04, 12),
    new THREE.MeshStandardMaterial({ color: 0x2a2218, roughness: 1 }),
  );
  ash.position.y = 0.02;
  group.add(ash);
  for (let i = 0; i < 8; i += 1) {
    const angle = (i / 8) * Math.PI * 2;
    const rock = new THREE.Mesh(
      new THREE.SphereGeometry(0.055, 6, 6),
      mats.stone,
    );
    rock.position.set(Math.cos(angle) * 0.22, 0.05, Math.sin(angle) * 0.22);
    rock.scale.set(1, 0.7, 1.1);
    group.add(rock);
  }
  for (const rot of [0.4, -0.55]) {
    const log = new THREE.Mesh(new THREE.CylinderGeometry(0.035, 0.04, 0.32, 8), mats.wood);
    log.rotation.z = Math.PI / 2;
    log.rotation.y = rot;
    log.position.y = 0.08;
    group.add(log);
  }
  addFlame(group, { y: 0.28, scale: 1.35 });
}

function addBrazier(group, mats) {
  for (const angle of [0, (Math.PI * 2) / 3, (Math.PI * 4) / 3]) {
    const leg = new THREE.Mesh(new THREE.CylinderGeometry(0.018, 0.022, 0.42, 6), mats.iron);
    leg.position.set(Math.cos(angle) * 0.12, 0.21, Math.sin(angle) * 0.12);
    group.add(leg);
  }
  const bowl = new THREE.Mesh(new THREE.CylinderGeometry(0.16, 0.1, 0.1, 12), mats.iron);
  bowl.position.y = 0.44;
  group.add(bowl);
  addFlame(group, { y: 0.58, scale: 1.05 });
}

function addFireplace(group, mats, lit) {
  const surround = new THREE.Mesh(texturedBoxGeometry(0.72, 0.78, 0.22, 1.4), mats.stone);
  surround.position.set(0, 0.39, -0.04);
  group.add(surround);
  const opening = new THREE.Mesh(
    new THREE.BoxGeometry(0.42, 0.42, 0.16),
    new THREE.MeshStandardMaterial({ color: 0x120e0a, roughness: 1, metalness: 0 }),
  );
  opening.position.set(0, 0.32, 0.06);
  group.add(opening);
  const mantel = new THREE.Mesh(texturedBoxGeometry(0.82, 0.08, 0.26, 1.4), mats.stone);
  mantel.position.set(0, 0.82, 0);
  group.add(mantel);
  const hearth = new THREE.Mesh(texturedBoxGeometry(0.7, 0.06, 0.28, 1.4), mats.stone);
  hearth.position.set(0, 0.03, 0.06);
  group.add(hearth);
  if (lit) {
    const log = new THREE.Mesh(new THREE.CylinderGeometry(0.03, 0.035, 0.28, 8), mats.wood);
    log.rotation.z = Math.PI / 2;
    log.position.set(0, 0.1, 0.08);
    group.add(log);
    addFlame(group, { y: 0.28, scale: 1.1 });
  }
}

function addLamp(group, mats) {
  const post = new THREE.Mesh(new THREE.CylinderGeometry(0.025, 0.03, 0.55, 8), mats.iron);
  post.position.y = 0.28;
  group.add(post);
  const cage = new THREE.Mesh(new THREE.CylinderGeometry(0.08, 0.07, 0.16, 8, 1, true), mats.iron);
  cage.position.y = 0.62;
  group.add(cage);
  const cap = new THREE.Mesh(new THREE.CylinderGeometry(0.09, 0.04, 0.05, 8), mats.iron);
  cap.position.y = 0.72;
  group.add(cap);
  addFlame(group, { y: 0.62, scale: 0.7 });
  return 0.68;
}

function attachPointLight(group, obj, { feetPerGrid, cellSize, onLight, y }) {
  if (!shouldEmitLight(obj)) return null;
  const params = pointLightParams(obj, feetPerGrid, cellSize);
  const light = new THREE.PointLight(params.color, params.intensity, params.distance, params.decay);
  light.position.y = y;
  light.userData.baseIntensity = params.intensity;
  light.userData.flickerPhase = (Number(obj.x) + Number(obj.y) * 0.37) * 2.1;
  light.userData.gridX = Number(obj.x);
  light.userData.gridY = Number(obj.y);
  if (typeof onLight === 'function' && onLight(light) === false) {
    light.dispose();
    return null;
  }
  group.add(light);
  return light;
}

export function addTokenLight(group, entity, mats, cellSize, opts = {}) {
  const light = entity?.light;
  if (!light || !shouldEmitLight({ ...entity, ...light, light })) return null;
  const size = Math.max(1, Number(entity.size) || 1);
  const payload = { ...entity, light, ...light, lit: true };
  const origin = lightOrigin(payload);
  if (origin === 'effect') {
    const color = lightColor(payload, 0xfff4d6);
    const orb = new THREE.Mesh(
      new THREE.SphereGeometry(0.07 * size, 10, 10),
      new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.72 }),
    );
    orb.position.y = 0.72 * size;
    group.add(orb);
  } else if (mats) {
    const hand = new THREE.Group();
    hand.position.set(0.28 * size, 0.18, 0.1 * size);
    const stick = new THREE.Mesh(new THREE.CylinderGeometry(0.018, 0.022, 0.28, 8), mats.wood);
    stick.rotation.z = -0.4;
    stick.position.set(0, 0.2, 0);
    hand.add(stick);
    const cup = new THREE.Mesh(new THREE.CylinderGeometry(0.03, 0.022, 0.05, 8), mats.iron);
    cup.position.set(0.05, 0.34, 0);
    hand.add(cup);
    addFlame(hand, { y: 0.42, scale: 0.55 });
    group.add(hand);
  }
  return attachPointLight(group, payload, {
    feetPerGrid: opts.feetPerGrid,
    cellSize,
    onLight: opts.onLight,
    y: origin === 'effect' ? 0.75 * size : 0.55 * size,
  });
}

export function addFixture(root, obj, mats, cellSize, opts = {}) {
  const kind = fixtureKind(obj);
  if (!kind) return null;
  const c = cellCenter(obj.x, obj.y, cellSize);
  const group = new THREE.Group();
  group.position.set(c.x, 0, c.z);
  const geometry = opts.geometry;
  const edge = obj.edge || nearestWallEdge(obj.x, obj.y, geometry);
  const feetPerGrid = opts.feetPerGrid;
  const onLight = opts.onLight;
  let lightY = 0.55;

  if (kind === 'chest') {
    group.rotation.y = yawFromFacing(obj.facing);
    addChest(group, obj, mats);
  } else if (kind === 'barrel') {
    addBarrel(group, mats);
  } else if (kind === 'tree') {
    addTree(group);
  } else if (kind === 'briar') {
    addBriar(group);
  } else if (kind === 'cover') {
    const tall = obj.cover === 'three_quarter' || obj.cover === 'total';
    addCrate(group, mats, { height: tall ? 0.7 : 0.34, width: tall ? 0.42 : 0.48 });
  } else if (kind === 'teleporter') {
    addTeleporter(group, obj);
  } else if (kind === 'trap_door' || kind === 'secret_trapdoor') {
    addTrapDoor(group, obj, mats);
  } else if (kind === 'pit_trap') {
    addPitTrap(group, obj, mats);
  } else if (kind === 'chasm') {
    addChasm(group, obj, mats, { feetPerGrid, cellSize });
  } else if (kind === 'stairs') {
    addStairs(group, obj, mats, cellSize, { feetPerGrid });
  } else if (kind === 'torch') {
    const standing = !edge;
    if (edge) {
      const off = torchWorldOffset(edge, cellSize);
      group.position.x += off.x;
      group.position.z += off.z;
      group.rotation.y = yawFromEdge(edge);
    }
    lightY = addTorch(group, mats, { standing });
  } else if (kind === 'campfire') {
    addCampfire(group, mats);
    lightY = 0.45;
  } else if (kind === 'brazier') {
    addBrazier(group, mats);
    lightY = 0.62;
  } else if (kind === 'fireplace') {
    if (edge) {
      const off = torchWorldOffset(edge, cellSize);
      group.position.x += off.x;
      group.position.z += off.z;
      group.rotation.y = yawFromEdge(edge);
    }
    const lit = obj.lit !== false && obj.lit !== 0;
    addFireplace(group, mats, lit);
    lightY = 0.4;
  } else if (kind === 'lamp') {
    lightY = addLamp(group, mats);
  } else {
    return null;
  }
  attachPointLight(group, obj, { feetPerGrid, cellSize, onLight, y: lightY });
  group.userData.fixture = obj;
  root.add(group);
  return group;
}
