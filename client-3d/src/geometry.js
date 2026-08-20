/**
 * Pure grid → mesh-descriptor helpers for the 3D VTT.
 * No Three.js imports so Vitest can run this in Node.
 */

export const CELL_SIZE = 1;
export const WALL_HEIGHT = 1.6;
export const THIN_WALL_THICKNESS = 0.11;
export const DOOR_HEIGHT = 1.45;
export const DOOR_FRAME_WIDTH = 0.1;
export const DOOR_FRAME_DEPTH = 0.16;
export const DOOR_PANEL_THICKNESS = 0.055;
export const DOOR_OPEN_ANGLE = Math.PI * 0.88;
export const DOOR_SILL = 0.025;
export const DOOR_MEET_GAP = 0.016;
export const WINDOW_SILL = 0.48;
export const WINDOW_OPENING_HEIGHT = 0.72;

export function clampIllumination(value, fallback = 1) {
  const number = Number(value);
  if (!Number.isFinite(number)) return fallback;
  // Light slider is 0–100 in the DOM; APIs use 0–1.
  const unit = number >= 2 && number <= 100 ? number / 100 : number;
  return Math.max(0, Math.min(1, unit));
}

/** Hemisphere / directional / fill intensities for map ambient (0–1). */
export function ambientLightIntensities(illumination) {
  const illum = clampIllumination(illumination, 0);
  return {
    hemi: 0.32 + illum * 1.05,
    dir: 0.18 + illum * 1.15,
    ambient: 0.14 + illum * 0.42,
  };
}

/** Orbit-camera distance used by MapScene.resetCamera (world units). */
export function orbitCameraDistance(size) {
  const width = Math.max(1, Number(size?.[0]) || 16);
  const depth = Math.max(1, Number(size?.[1]) || 16);
  const span = Math.max(width, depth, 8);
  return Math.hypot(Math.max(width, 8) * 0.35, span * 0.7, Math.max(depth, 8) * 0.7);
}

/**
 * Distance fog so the default orbit camera stays in the clear range.
 * Illumination pushes the haze back (dark maps keep a closer falloff).
 */
export function distanceFogRange(size, illumination) {
  const width = Math.max(1, Number(size?.[0]) || 16);
  const depth = Math.max(1, Number(size?.[1]) || 16);
  const span = Math.max(width, depth, 8);
  const illum = clampIllumination(illumination, 1);
  const camera = orbitCameraDistance([width, depth]);
  const near = camera + 8 + span * 0.35 + illum * span * 0.55;
  const far = near + 18 + span * 1.25;
  return { near, far };
}

export function cellCenter(x, y, cellSize = CELL_SIZE) {
  return {
    x: (Number(x) + 0.5) * cellSize,
    y: 0,
    z: (Number(y) + 0.5) * cellSize,
  };
}

export function worldToGrid(worldX, worldZ, cellSize = CELL_SIZE) {
  return {
    x: Math.floor(worldX / cellSize),
    y: Math.floor(worldZ / cellSize),
  };
}

export function inBounds(x, y, size) {
  const width = size ? size[0] : 0;
  const height = size ? size[1] : 0;
  return x >= 0 && y >= 0 && x < width && y < height;
}

export function floorPlane(size, cellSize = CELL_SIZE) {
  const width = Number(size[0]) * cellSize;
  const depth = Number(size[1]) * cellSize;
  return {
    width,
    depth,
    // Plane centered so cell (0,0) occupies [0, cellSize]
    x: width / 2,
    y: 0,
    z: depth / 2,
  };
}

export function solidWallBoxes(solidWalls, { cellSize = CELL_SIZE, wallHeight = WALL_HEIGHT } = {}) {
  return (solidWalls || []).map(([x, y]) => {
    const c = cellCenter(x, y, cellSize);
    return {
      x: c.x,
      y: wallHeight / 2,
      z: c.z,
      width: cellSize * 0.98,
      height: wallHeight,
      depth: cellSize * 0.98,
      gridX: x,
      gridY: y,
    };
  });
}

function edgeBox(x, y, edge, { cellSize, wallHeight, thickness }) {
  const c = cellCenter(x, y, cellSize);
  const t = thickness;
  const h = wallHeight;
  if (edge === 'n') {
    return { x: c.x, y: h / 2, z: y * cellSize, width: cellSize, height: h, depth: t, edge };
  }
  if (edge === 's') {
    return { x: c.x, y: h / 2, z: (y + 1) * cellSize, width: cellSize, height: h, depth: t, edge };
  }
  if (edge === 'w') {
    return { x: x * cellSize, y: h / 2, z: c.z, width: t, height: h, depth: cellSize, edge };
  }
  return { x: (x + 1) * cellSize, y: h / 2, z: c.z, width: t, height: h, depth: cellSize, edge };
}

export function edgeWallBoxes(edgeWalls, opts = {}) {
  const cellSize = opts.cellSize ?? CELL_SIZE;
  const wallHeight = opts.wallHeight ?? WALL_HEIGHT;
  const thickness = opts.thickness ?? THIN_WALL_THICKNESS;
  const boxes = [];
  for (const wall of edgeWalls || []) {
    for (const edge of wall.edges || []) {
      boxes.push({
        ...edgeBox(wall.x, wall.y, edge, { cellSize, wallHeight, thickness }),
        gridX: wall.x,
        gridY: wall.y,
      });
    }
  }
  return boxes;
}

export function doorBoxes(doors, opts = {}) {
  const cellSize = opts.cellSize ?? CELL_SIZE;
  const wallHeight = opts.wallHeight ?? DOOR_HEIGHT;
  const thickness = opts.thickness ?? THIN_WALL_THICKNESS * 1.2;
  return (doors || []).flatMap((door) => {
    return (door.edges || ['n']).map((edge) => ({
      ...edgeBox(door.x, door.y, edge, { cellSize, wallHeight, thickness }),
      gridX: door.x,
      gridY: door.y,
      id: door.id,
      open: Boolean(door.open),
      locked: Boolean(door.locked),
    }));
  });
}

function framePost(x, y, z, width, height, depth) {
  return { x, y, z, width, height, depth };
}

export function doorModelKind(door) {
  const raw = door?.model?.kind || door?.kind || 'door';
  const kind = String(raw || 'door').toLowerCase();
  if (!kind || kind === 'auto') return 'door';
  return kind;
}

export function doorsOfKind(doors, kind) {
  return (doors || []).filter((door) => {
    if (door?.model?.src) return false;
    return doorModelKind(door) === kind;
  });
}

/**
 * Hinged wooden door + stone frame on a cell edge.
 * Panel local space: hinge at origin, leaf extends +X, faces ±Z.
 * Adjacent same-edge oak doors (N/S neighbors for E/W walls, E/W neighbors
 * for N/S walls) become one double door: shared frame, two leaves, outer hinges.
 */
export function doorAssemblies(doors, opts = {}) {
  return barrierAssemblies(doorsOfKind(doors, 'door'), opts);
}

function doorEdgeKey(door, edge) {
  return `${Number(door.x)},${Number(door.y)},${edge}`;
}

function wallPartnerOffset(edge) {
  if (edge === 'n' || edge === 's') return { dx: 1, dy: 0 };
  return { dx: 0, dy: 1 };
}

function allowsDoubleDoor(door) {
  return door?.double !== false;
}

function hasThinEdge(edgeWalls, x, y, side) {
  const gx = Number(x);
  const gy = Number(y);
  for (const wall of edgeWalls || []) {
    if (Number(wall.x) !== gx || Number(wall.y) !== gy) continue;
    if ((wall.edges || []).includes(side)) return true;
  }
  return false;
}

function doorsSeparatedByWall(near, far, edge, edgeWalls) {
  if (edge === 'e' || edge === 'w') {
    return hasThinEdge(edgeWalls, near.x, near.y, 's')
      || hasThinEdge(edgeWalls, far.x, far.y, 'n');
  }
  return hasThinEdge(edgeWalls, near.x, near.y, 'e')
    || hasThinEdge(edgeWalls, far.x, far.y, 'w');
}

function pairAdjacentDoorEdges(doors, edgeWalls) {
  const items = [];
  for (const door of doors || []) {
    if (!allowsDoubleDoor(door)) continue;
    for (const edge of door.edges || ['n']) {
      items.push({ door, edge });
    }
  }
  const byKey = new Map();
  for (const item of items) {
    byKey.set(doorEdgeKey(item.door, item.edge), item);
  }
  const pairs = new Map();
  const used = new Set();
  const sorted = [...items].sort((a, b) => {
    const dy = Number(a.door.y) - Number(b.door.y);
    if (dy !== 0) return dy;
    return Number(a.door.x) - Number(b.door.x);
  });
  for (const item of sorted) {
    const key = doorEdgeKey(item.door, item.edge);
    if (used.has(key)) continue;
    const { dx, dy } = wallPartnerOffset(item.edge);
    const otherKey = `${Number(item.door.x) + dx},${Number(item.door.y) + dy},${item.edge}`;
    const other = byKey.get(otherKey);
    if (!other || used.has(otherKey)) continue;
    if (doorsSeparatedByWall(item.door, other.door, item.edge, edgeWalls)) continue;
    used.add(key);
    used.add(otherKey);
    pairs.set(key, { role: 'near', other: other.door, edge: item.edge });
    pairs.set(otherKey, { role: 'far', other: item.door, edge: item.edge });
  }
  return pairs;
}

function doorLeaf(door, { hinge, closedYaw, openDelta, leafWidth, panelH, pt }) {
  return {
    id: door.id,
    open: Boolean(door.open),
    locked: Boolean(door.locked),
    hinge,
    closedYaw,
    openDelta,
    yaw: Boolean(door.open) ? closedYaw + openDelta : closedYaw,
    panel: {
      width: leafWidth,
      height: panelH,
      depth: pt,
      localX: leafWidth / 2,
      localY: panelH / 2,
      localZ: 0,
    },
    handle: {
      localX: leafWidth * 0.82,
      localY: panelH * 0.48,
      localZ: pt / 2 + 0.02,
    },
  };
}

function singleDoorAssembly(door, edge, opts) {
  const cellSize = opts.cellSize;
  const doorHeight = opts.doorHeight;
  const fw = opts.frameWidth;
  const fd = opts.frameDepth;
  const pt = opts.panelThickness;
  const openAngle = opts.openAngle;
  const sill = opts.sill;
  const x0 = Number(door.x) * cellSize;
  const z0 = Number(door.y) * cellSize;
  const x1 = x0 + cellSize;
  const z1 = z0 + cellSize;
  const midX = (x0 + x1) / 2;
  const midZ = (z0 + z1) / 2;
  const opening = cellSize - fw * 2;
  const panelH = Math.max(0.4, doorHeight - fw - sill);
  const ns = edge === 'n' || edge === 's';
  const frame = [];
  let hinge;
  let closedYaw = 0;
  let openDelta = openAngle;

  if (ns) {
    const z = edge === 'n' ? z0 : z1;
    frame.push(framePost(x0 + fw / 2, doorHeight / 2, z, fw, doorHeight, fd));
    frame.push(framePost(x1 - fw / 2, doorHeight / 2, z, fw, doorHeight, fd));
    frame.push(framePost(midX, doorHeight - fw / 2, z, cellSize, fw, fd));
    hinge = { x: x0 + fw, y: sill, z };
    closedYaw = 0;
    openDelta = (edge === 'n' ? 1 : -1) * openAngle;
  } else {
    const x = edge === 'w' ? x0 : x1;
    frame.push(framePost(x, doorHeight / 2, z0 + fw / 2, fd, doorHeight, fw));
    frame.push(framePost(x, doorHeight / 2, z1 - fw / 2, fd, doorHeight, fw));
    frame.push(framePost(x, doorHeight - fw / 2, midZ, fd, fw, cellSize));
    hinge = { x, y: sill, z: z0 + fw };
    closedYaw = Math.PI / 2;
    openDelta = (edge === 'w' ? -1 : 1) * openAngle;
  }

  const leaf = doorLeaf(door, { hinge, closedYaw, openDelta, leafWidth: opening, panelH, pt });
  return {
    id: door.id,
    kind: doorModelKind(door),
    edge,
    gridX: door.x,
    gridY: door.y,
    open: leaf.open,
    locked: leaf.locked,
    hinge: leaf.hinge,
    closedYaw: leaf.closedYaw,
    openDelta: leaf.openDelta,
    yaw: leaf.yaw,
    frame,
    panel: leaf.panel,
    handle: leaf.handle,
    leaves: [leaf],
    ns,
  };
}

function doubleDoorAssembly(near, far, edge, opts) {
  const cellSize = opts.cellSize;
  const doorHeight = opts.doorHeight;
  const fw = opts.frameWidth;
  const fd = opts.frameDepth;
  const pt = opts.panelThickness;
  const openAngle = opts.openAngle;
  const sill = opts.sill;
  const meet = opts.meetGap ?? DOOR_MEET_GAP;
  const ns = edge === 'n' || edge === 's';
  const x0 = Math.min(Number(near.x), Number(far.x)) * cellSize;
  const z0 = Math.min(Number(near.y), Number(far.y)) * cellSize;
  const span = cellSize * 2;
  const panelH = Math.max(0.4, doorHeight - fw - sill);
  const leafWidth = Math.max(0.2, cellSize - fw - meet);
  const frame = [];
  let leaves;

  if (ns) {
    const z = edge === 'n' ? z0 : z0 + cellSize;
    const xStart = x0;
    const xEnd = x0 + span;
    const midX = (xStart + xEnd) / 2;
    frame.push(framePost(xStart + fw / 2, doorHeight / 2, z, fw, doorHeight, fd));
    frame.push(framePost(xEnd - fw / 2, doorHeight / 2, z, fw, doorHeight, fd));
    frame.push(framePost(midX, doorHeight - fw / 2, z, span, fw, fd));
    const nearOpenDelta = (edge === 'n' ? 1 : -1) * openAngle;
    leaves = [
      doorLeaf(near, {
        hinge: { x: xStart + fw, y: sill, z },
        closedYaw: 0,
        openDelta: nearOpenDelta,
        leafWidth,
        panelH,
        pt,
      }),
      doorLeaf(far, {
        hinge: { x: xEnd - fw, y: sill, z },
        closedYaw: Math.PI,
        openDelta: -nearOpenDelta,
        leafWidth,
        panelH,
        pt,
      }),
    ];
  } else {
    const x = edge === 'w' ? x0 : x0 + cellSize;
    const zStart = z0;
    const zEnd = z0 + span;
    const midZ = (zStart + zEnd) / 2;
    frame.push(framePost(x, doorHeight / 2, zStart + fw / 2, fd, doorHeight, fw));
    frame.push(framePost(x, doorHeight / 2, zEnd - fw / 2, fd, doorHeight, fw));
    frame.push(framePost(x, doorHeight - fw / 2, midZ, fd, fw, span));
    const nearOpenDelta = (edge === 'w' ? -1 : 1) * openAngle;
    leaves = [
      doorLeaf(near, {
        hinge: { x, y: sill, z: zStart + fw },
        closedYaw: Math.PI / 2,
        openDelta: nearOpenDelta,
        leafWidth,
        panelH,
        pt,
      }),
      doorLeaf(far, {
        hinge: { x, y: sill, z: zEnd - fw },
        closedYaw: -Math.PI / 2,
        openDelta: -nearOpenDelta,
        leafWidth,
        panelH,
        pt,
      }),
    ];
  }

  const first = leaves[0];
  return {
    id: near.id,
    partnerId: far.id,
    double: true,
    kind: doorModelKind(near),
    edge,
    gridX: near.x,
    gridY: near.y,
    span: 2,
    open: Boolean(near.open) || Boolean(far.open),
    locked: Boolean(near.locked) && Boolean(far.locked),
    hinge: first.hinge,
    closedYaw: first.closedYaw,
    openDelta: first.openDelta,
    yaw: first.yaw,
    frame,
    panel: first.panel,
    handle: first.handle,
    leaves,
    ns,
  };
}

function barrierAssemblies(doors, opts = {}) {
  const resolved = {
    cellSize: opts.cellSize ?? CELL_SIZE,
    doorHeight: opts.doorHeight ?? DOOR_HEIGHT,
    frameWidth: opts.frameWidth ?? DOOR_FRAME_WIDTH,
    frameDepth: opts.frameDepth ?? DOOR_FRAME_DEPTH,
    panelThickness: opts.panelThickness ?? DOOR_PANEL_THICKNESS,
    openAngle: opts.openAngle ?? DOOR_OPEN_ANGLE,
    sill: opts.sill ?? DOOR_SILL,
    meetGap: opts.meetGap ?? DOOR_MEET_GAP,
  };
  const pairs = pairAdjacentDoorEdges(doors, opts.edgeWalls);
  return (doors || []).flatMap((door) => (door.edges || ['n']).flatMap((edge) => {
    const pair = pairs.get(doorEdgeKey(door, edge));
    if (pair?.role === 'far') return [];
    if (pair?.role === 'near') return [doubleDoorAssembly(door, pair.other, edge, resolved)];
    return [singleDoorAssembly(door, edge, resolved)];
  }));
}

/**
 * Casement window: apron wall under a sill, frame, glass or shutter pane.
 * Open swings like a door. Barred windows keep iron bars in the opening.
 */
export function windowAssemblies(doors, opts = {}) {
  const cellSize = opts.cellSize ?? CELL_SIZE;
  const wallHeight = opts.wallHeight ?? WALL_HEIGHT;
  const fw = opts.frameWidth ?? DOOR_FRAME_WIDTH;
  const fd = opts.frameDepth ?? DOOR_FRAME_DEPTH;
  const pt = opts.panelThickness ?? 0.03;
  const openAngle = opts.openAngle ?? DOOR_OPEN_ANGLE;
  const sillH = opts.sill ?? WINDOW_SILL;
  const openingH = opts.openingHeight ?? WINDOW_OPENING_HEIGHT;

  return doorsOfKind(doors, 'window').flatMap((door) => (door.edges || ['n']).map((edge) => {
    const x0 = Number(door.x) * cellSize;
    const z0 = Number(door.y) * cellSize;
    const x1 = x0 + cellSize;
    const z1 = z0 + cellSize;
    const midX = (x0 + x1) / 2;
    const midZ = (z0 + z1) / 2;
    const opening = cellSize - fw * 2;
    const ns = edge === 'n' || edge === 's';
    const paneH = Math.max(0.25, Math.min(openingH, wallHeight - sillH - fw));
    const lintelTop = sillH + paneH + fw;
    const transomH = Math.max(0, wallHeight - lintelTop);
    const wallT = opts.thickness ?? THIN_WALL_THICKNESS;
    const frame = [];
    const apron = [];
    const transom = [];
    let hinge;
    let closedYaw = 0;
    let openDelta = openAngle;
    let planeZ;
    let planeX;

    if (ns) {
      const z = edge === 'n' ? z0 : z1;
      planeZ = z;
      frame.push(framePost(x0 + fw / 2, sillH + paneH / 2, z, fw, paneH + fw, fd));
      frame.push(framePost(x1 - fw / 2, sillH + paneH / 2, z, fw, paneH + fw, fd));
      frame.push(framePost(midX, sillH + paneH + fw / 2, z, cellSize, fw, fd));
      frame.push(framePost(midX, sillH - fw / 2, z, cellSize, fw, fd));
      apron.push(framePost(midX, sillH / 2, z, cellSize, Math.max(0.08, sillH - fw), fd * 0.95));
      if (transomH > 0.001) {
        transom.push(framePost(midX, lintelTop + transomH / 2, z, cellSize, transomH, wallT));
      }
      hinge = { x: x0 + fw, y: sillH, z };
      closedYaw = 0;
      openDelta = (edge === 'n' ? 1 : -1) * openAngle;
    } else {
      const x = edge === 'w' ? x0 : x1;
      planeX = x;
      frame.push(framePost(x, sillH + paneH / 2, z0 + fw / 2, fd, paneH + fw, fw));
      frame.push(framePost(x, sillH + paneH / 2, z1 - fw / 2, fd, paneH + fw, fw));
      frame.push(framePost(x, sillH + paneH + fw / 2, midZ, fd, fw, cellSize));
      frame.push(framePost(x, sillH - fw / 2, midZ, fd, fw, cellSize));
      apron.push(framePost(x, sillH / 2, midZ, fd * 0.95, Math.max(0.08, sillH - fw), cellSize));
      if (transomH > 0.001) {
        transom.push(framePost(x, lintelTop + transomH / 2, midZ, wallT, transomH, cellSize));
      }
      hinge = { x, y: sillH, z: z0 + fw };
      closedYaw = Math.PI / 2;
      openDelta = (edge === 'w' ? -1 : 1) * openAngle;
    }

    const paneKind = String(door.cover_pane || 'glass').toLowerCase() === 'opaque' ? 'opaque' : 'glass';
    return {
      id: door.id,
      kind: 'window',
      edge,
      gridX: door.x,
      gridY: door.y,
      open: Boolean(door.open),
      barred: Boolean(door.barred),
      coverPane: paneKind,
      windowMaterial: String(door.window_material || 'wood').toLowerCase(),
      wallMaterial: String(door.wall_material || 'stone').toLowerCase(),
      hinge,
      closedYaw,
      openDelta,
      yaw: Boolean(door.open) ? closedYaw + openDelta : closedYaw,
      frame,
      apron,
      transom,
      panel: {
        width: opening,
        height: paneH,
        depth: pt,
        localX: opening / 2,
        localY: paneH / 2,
        localZ: 0,
      },
      bars: {
        count: 5,
        ns,
        x: ns ? midX : planeX,
        y: sillH + paneH / 2,
        z: ns ? planeZ : midZ,
        width: ns ? opening : fd * 0.35,
        height: paneH,
        depth: ns ? fd * 0.35 : opening,
      },
      ns,
    };
  }));
}

export function secretDoorAssemblies(doors, opts = {}) {
  const cellSize = opts.cellSize ?? CELL_SIZE;
  const height = opts.wallHeight ?? WALL_HEIGHT;
  const thickness = opts.thickness ?? THIN_WALL_THICKNESS * 1.15;
  return doorsOfKind(doors, 'secret_door').flatMap((door) => (
    (door.edges || ['n']).map((edge) => {
      const slab = edgeBox(door.x, door.y, edge, { cellSize, wallHeight: height, thickness });
      return {
        id: door.id,
        kind: 'secret_door',
        edge,
        gridX: door.x,
        gridY: door.y,
        open: Boolean(door.open),
        slab,
        lift: Boolean(door.open) ? height * 0.92 : 0,
      };
    })
  ));
}

export function portcullisAssemblies(doors, opts = {}) {
  const cellSize = opts.cellSize ?? CELL_SIZE;
  const height = opts.doorHeight ?? DOOR_HEIGHT;
  const fw = opts.frameWidth ?? 0.07;
  const fd = opts.frameDepth ?? 0.1;
  return doorsOfKind(doors, 'portcullis').flatMap((door) => (
    (door.edges || ['n']).map((edge) => {
      const x0 = Number(door.x) * cellSize;
      const z0 = Number(door.y) * cellSize;
      const x1 = x0 + cellSize;
      const z1 = z0 + cellSize;
      const midX = (x0 + x1) / 2;
      const midZ = (z0 + z1) / 2;
      const ns = edge === 'n' || edge === 's';
      const frame = [];
      let grate;
      if (ns) {
        const z = edge === 'n' ? z0 : z1;
        frame.push(framePost(x0 + fw / 2, height / 2, z, fw, height, fd));
        frame.push(framePost(x1 - fw / 2, height / 2, z, fw, height, fd));
        frame.push(framePost(midX, height - fw / 2, z, cellSize, fw, fd));
        grate = {
          x: midX,
          y: height / 2,
          z,
          width: cellSize - fw * 2,
          height,
          depth: fd * 0.45,
          ns: true,
        };
      } else {
        const x = edge === 'w' ? x0 : x1;
        frame.push(framePost(x, height / 2, z0 + fw / 2, fd, height, fw));
        frame.push(framePost(x, height / 2, z1 - fw / 2, fd, height, fw));
        frame.push(framePost(x, height - fw / 2, midZ, fd, fw, cellSize));
        grate = {
          x,
          y: height / 2,
          z: midZ,
          width: fd * 0.45,
          height,
          depth: cellSize - fw * 2,
          ns: false,
        };
      }
      return {
        id: door.id,
        kind: 'portcullis',
        edge,
        gridX: door.x,
        gridY: door.y,
        open: Boolean(door.open),
        frame,
        grate,
        bars: 9,
        lift: Boolean(door.open) ? height * 0.88 : 0,
      };
    })
  ));
}

export function groupBoxesBySize(boxes) {
  const groups = new Map();
  for (const box of boxes || []) {
    const key = `${Number(box.width).toFixed(4)}:${Number(box.height).toFixed(4)}:${Number(box.depth).toFixed(4)}`;
    const list = groups.get(key);
    if (list) list.push(box);
    else groups.set(key, [box]);
  }
  return groups;
}

/** Per-face UV repeats for a box ( +X -X +Y -Y +Z -Z ). */
export function boxFaceUVRepeats(width, height, depth, tilesPerUnit = 1, options = {}) {
  const w = Math.max(0.05, Number(width) * tilesPerUnit);
  const h = Math.max(0.05, Number(height) * tilesPerUnit);
  const d = Math.max(0.05, Number(depth) * tilesPerUnit);
  const hv = options.lockHeight ? 1 : h;
  return [
    [d, hv],
    [d, hv],
    [w, d],
    [w, d],
    [w, hv],
    [w, hv],
  ];
}

export function tileByCoord(tiles, x, y) {
  if (!tiles) return null;
  for (const tile of tiles) {
    if (Number(tile.x) === Number(x) && Number(tile.y) === Number(y)) {
      return tile;
    }
  }
  return null;
}

export function foggedCells(tiles) {
  return (tiles || []).filter((t) => t.line_of_sight === false).map((t) => ({
    x: t.x,
    y: t.y,
    light: Number(t.light) || 0,
  }));
}

export function pointInPolygon(x, y, points) {
  const pts = points || [];
  if (pts.length < 3) return false;
  let inside = false;
  for (let i = 0, j = pts.length - 1; i < pts.length; j = i, i += 1) {
    const xi = Number(pts[i][0]);
    const yi = Number(pts[i][1]);
    const xj = Number(pts[j][0]);
    const yj = Number(pts[j][1]);
    const denom = (yj - yi) || 1e-9;
    const intersect = ((yi > y) !== (yj > y)) && (x < ((xj - xi) * (y - yi)) / denom + xi);
    if (intersect) inside = !inside;
  }
  return inside;
}

export function cellInFogMaskLayer(x, y, layer) {
  if (!layer || typeof layer !== 'object') return false;
  const kind = String(layer.type || layer.kind || 'polygon').toLowerCase();
  const cx = Number(x) + 0.5;
  const cy = Number(y) + 0.5;
  if (kind === 'rectangle' || kind === 'rect') {
    const pos = layer.position || layer.pos || [0, 0];
    const size = layer.size || [0, 0];
    const x0 = Number(pos[0]) || 0;
    const y0 = Number(pos[1]) || 0;
    const w = Number(size[0]) || 0;
    const h = Number(size[1]) || 0;
    return cx >= x0 && cx < x0 + w && cy >= y0 && cy < y0 + h;
  }
  return pointInPolygon(cx, cy, layer.points || layer.vertices || []);
}

/** Grid cells that receive map-weather fog. Null layers / mask off → every cell. */
export function fogMaskCells(config, size) {
  const width = Math.max(0, Number(size?.[0]) || 0);
  const height = Math.max(0, Number(size?.[1]) || 0);
  const layers = config?.mask ? (config.mask_layers || config.mask_layer || []) : null;
  const list = Array.isArray(layers) ? layers : (layers ? [layers] : []);
  const cells = [];
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      if (!list.length || list.some((layer) => cellInFogMaskLayer(x, y, layer))) {
        cells.push({ x, y });
      }
    }
  }
  return cells;
}

export function fogMaskBytes(config, size) {
  const width = Math.max(1, Number(size?.[0]) || 1);
  const height = Math.max(1, Number(size?.[1]) || 1);
  const marked = new Set(fogMaskCells(config, [width, height]).map((c) => `${c.x},${c.y}`));
  const data = new Uint8Array(width * height);
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      data[y * width + x] = marked.has(`${x},${y}`) ? 255 : 0;
    }
  }
  return { width, height, data };
}

/** Cardinal wall next to a walkable cell — same rule as the server torch placer. */
export function nearestWallEdge(x, y, geometry) {
  const xi = Number(x);
  const yi = Number(y);
  const solid = new Set();
  for (const cell of geometry?.solid_walls || []) {
    solid.add(`${Number(cell[0])},${Number(cell[1])}`);
  }
  const neighbors = [
    ['n', xi, yi - 1],
    ['s', xi, yi + 1],
    ['e', xi + 1, yi],
    ['w', xi - 1, yi],
  ];
  for (const [edge, nx, ny] of neighbors) {
    if (solid.has(`${nx},${ny}`)) return edge;
  }
  for (const wall of geometry?.edge_walls || []) {
    if (Number(wall.x) === xi && Number(wall.y) === yi) {
      const edges = wall.edges || [];
      if (edges.length) return edges[0];
    }
  }
  const opposite = { n: 's', s: 'n', e: 'w', w: 'e' };
  const byCell = new Map();
  for (const wall of geometry?.edge_walls || []) {
    byCell.set(`${Number(wall.x)},${Number(wall.y)}`, wall.edges || []);
  }
  for (const [edge, nx, ny] of neighbors) {
    const edges = byCell.get(`${nx},${ny}`) || [];
    if (edges.includes(opposite[edge])) return edge;
  }
  return null;
}

export function torchWorldOffset(edge, cellSize) {
  const inset = cellSize * 0.42;
  if (edge === 'n') return { x: 0, z: -inset };
  if (edge === 's') return { x: 0, z: inset };
  if (edge === 'w') return { x: -inset, z: 0 };
  if (edge === 'e') return { x: inset, z: 0 };
  return { x: 0, z: 0 };
}

export const WATER_DEPTH = 0.22;
export const WATER_WALL = 0.07;

function waterCellXY(cell) {
  if (Array.isArray(cell)) return { x: Number(cell[0]), y: Number(cell[1]) };
  return { x: Number(cell.x), y: Number(cell.y) };
}

export function waterCellKey(x, y) {
  return `${Number(x)},${Number(y)}`;
}

export function waterCellSet(cells) {
  const set = new Set();
  for (const cell of cells || []) {
    const { x, y } = waterCellXY(cell);
    set.add(waterCellKey(x, y));
  }
  return set;
}

/**
 * Open stone basin walls only. The battlemap is draped on the pool bed
 * so it can show through transparent water.
 */
export function waterBasinBoxes(cells, {
  cellSize = CELL_SIZE,
  depth = WATER_DEPTH,
  wall = WATER_WALL,
} = {}) {
  const set = waterCellSet(cells);
  const boxes = [];
  const wallH = depth;
  const wallY = -wallH / 2;
  for (const cell of cells || []) {
    const { x, y } = waterCellXY(cell);
    const c = cellCenter(x, y, cellSize);
    if (!set.has(waterCellKey(x, y - 1))) {
      boxes.push({
        x: c.x, y: wallY, z: y * cellSize,
        width: cellSize, height: wallH, depth: wall,
        gridX: x, gridY: y, part: 'n',
      });
    }
    if (!set.has(waterCellKey(x, y + 1))) {
      boxes.push({
        x: c.x, y: wallY, z: (y + 1) * cellSize,
        width: cellSize, height: wallH, depth: wall,
        gridX: x, gridY: y, part: 's',
      });
    }
    if (!set.has(waterCellKey(x - 1, y))) {
      boxes.push({
        x: x * cellSize, y: wallY, z: c.z,
        width: wall, height: wallH, depth: cellSize,
        gridX: x, gridY: y, part: 'w',
      });
    }
    if (!set.has(waterCellKey(x + 1, y))) {
      boxes.push({
        x: (x + 1) * cellSize, y: wallY, z: c.z,
        width: wall, height: wallH, depth: cellSize,
        gridX: x, gridY: y, part: 'e',
      });
    }
  }
  return boxes;
}

/** Water surface patches at y=0 (same as the battlemap floor plane). */
export function waterSurfaceBoxes(cells, { cellSize = CELL_SIZE } = {}) {
  return (cells || []).map((cell) => {
    const { x, y } = waterCellXY(cell);
    const c = cellCenter(x, y, cellSize);
    return {
      x: c.x,
      y: 0,
      z: c.z,
      width: cellSize,
      depth: cellSize,
      gridX: x,
      gridY: y,
    };
  });
}

/**
 * Battlemap floor quads. Water cells are lowered to the pool bed so the
 * map image shows through the transparent water surface.
 * Open trapdoors / sprung pits omit the quad (a hole mesh fills the cell).
 * UVs match PlaneGeometry + rotateX(-PI/2) + TextureLoader flipY
 * (grid y=0 is the top of the image, v=1).
 */
export function floorQuads(size, waterCells, cellSize = CELL_SIZE, holeCells = []) {
  const width = Number(size?.[0]) || 0;
  const height = Number(size?.[1]) || 0;
  const water = waterCellSet(waterCells);
  const holes = waterCellSet(holeCells);
  const positions = [];
  const uvs = [];
  const normals = [];
  const indices = [];
  let vert = 0;
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      if (holes.has(waterCellKey(x, y))) continue;
      const x0 = x * cellSize;
      const x1 = (x + 1) * cellSize;
      const z0 = y * cellSize;
      const z1 = (y + 1) * cellSize;
      const y0 = water.has(waterCellKey(x, y)) ? -WATER_DEPTH : 0;
      const u0 = width ? x / width : 0;
      const u1 = width ? (x + 1) / width : 1;
      const v0 = height ? 1 - y / height : 1;
      const v1 = height ? 1 - (y + 1) / height : 0;
      positions.push(x0, y0, z0, x1, y0, z0, x1, y0, z1, x0, y0, z1);
      uvs.push(u0, v0, u1, v0, u1, v1, u0, v1);
      normals.push(0, 1, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0);
      indices.push(vert, vert + 3, vert + 2, vert, vert + 2, vert + 1);
      vert += 4;
    }
  }
  return {
    positions,
    uvs,
    normals,
    indices,
    quadCount: vert / 4,
    width: width * cellSize,
    depth: height * cellSize,
  };
}

/** Cells whose battlemap floor should be omitted (open hatch or sprung pit). */
export function hatchHoleCells(objects) {
  const cells = [];
  for (const obj of objects || []) {
    const kind = String(obj.kind || '').toLowerCase();
    if (kind === 'trap_door' || kind === 'secret_trapdoor') {
      if (obj.opened) cells.push([obj.x, obj.y]);
    } else if (kind === 'pit_trap') {
      if (obj.activated || obj.disarmed) cells.push([obj.x, obj.y]);
    } else if (kind === 'chasm') {
      cells.push([obj.x, obj.y]);
    }
  }
  return cells;
}

/** World-space depth of an open chasm shaft (capped so the tabletop stays readable). */
export const CHASM_DEFAULT_DEPTH = 0.85;

export function chasmDepthWorld(obj, { feetPerGrid = 5, cellSize = CELL_SIZE } = {}) {
  const fpg = Number(feetPerGrid) > 0 ? Number(feetPerGrid) : 5;
  const size = Number(cellSize) > 0 ? Number(cellSize) : 1;
  const feet = Number(obj?.fall_distance);
  let cells = CHASM_DEFAULT_DEPTH;
  if (Number.isFinite(feet) && feet > 0) {
    cells = Math.min(3.2, Math.max(0.55, feet / fpg));
  }
  return cells * size;
}

/** Medium-creature stair riser (7.5 in) in feet. */
export const STAIR_RISER_FT = 7.5 / 12;
/** Default rise matches 8 ft walls (`WALL_HEIGHT` at 5 ft/square). */
export const DEFAULT_STAIR_HEIGHT_FT = 8;

export function normalizeStairSquares(raw, fallback = []) {
  const points = [];
  const seen = new Set();
  const source = Array.isArray(raw) && raw.length ? raw : fallback;
  for (const item of source) {
    const x = Number(Array.isArray(item) ? item[0] : item?.x);
    const y = Number(Array.isArray(item) ? item[1] : item?.y);
    if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
    const key = `${x},${y}`;
    if (seen.has(key)) continue;
    seen.add(key);
    points.push([x, y]);
  }
  return points;
}

export function stairHeightWorld(heightFt, feetPerGrid = 5) {
  const ft = Number(heightFt);
  const rise = Number.isFinite(ft) ? ft : DEFAULT_STAIR_HEIGHT_FT;
  const fpg = Number(feetPerGrid) > 0 ? Number(feetPerGrid) : 5;
  return rise / fpg;
}

export function stairStepCount(heightFt) {
  const rise = Math.abs(Number.isFinite(Number(heightFt)) ? Number(heightFt) : DEFAULT_STAIR_HEIGHT_FT);
  if (rise < 0.05) return 1;
  return Math.max(1, Math.round(rise / STAIR_RISER_FT));
}

function _dirOf(from, to) {
  const dx = to[0] - from[0];
  const dy = to[1] - from[1];
  const len = Math.hypot(dx, dy) || 1;
  return [dx / len, dy / len];
}

function _adjacent(a, b) {
  return Math.abs(a[0] - b[0]) + Math.abs(a[1] - b[1]) === 1;
}

function _centroid(cells) {
  let x = 0;
  let y = 0;
  for (const c of cells) {
    x += c[0] + 0.5;
    y += c[1] + 0.5;
  }
  const n = cells.length || 1;
  return [x / n, y / n];
}

function _bbox(cells) {
  const xs = cells.map((c) => c[0]);
  const ys = cells.map((c) => c[1]);
  const minx = Math.min(...xs);
  const maxx = Math.max(...xs);
  const miny = Math.min(...ys);
  const maxy = Math.max(...ys);
  return {
    minx, maxx, miny, maxy,
    w: maxx - minx + 1,
    h: maxy - miny + 1,
  };
}

function _turnSigns(cells) {
  const signs = [];
  for (let i = 1; i < cells.length - 1; i += 1) {
    const ux = cells[i][0] - cells[i - 1][0];
    const uy = cells[i][1] - cells[i - 1][1];
    const vx = cells[i + 1][0] - cells[i][0];
    const vy = cells[i + 1][1] - cells[i][1];
    const z = ux * vy - uy * vx;
    if (z > 0) signs.push(1);
    else if (z < 0) signs.push(-1);
  }
  return signs;
}

function _unwrapAngles(cells, cx, cy) {
  const raw = cells.map((c) => Math.atan2((c[1] + 0.5) - cy, (c[0] + 0.5) - cx));
  if (!raw.length) return [];
  const out = [raw[0]];
  for (let i = 1; i < raw.length; i += 1) {
    let a = raw[i];
    while (a - out[i - 1] > Math.PI) a -= Math.PI * 2;
    while (a - out[i - 1] < -Math.PI) a += Math.PI * 2;
    out.push(a);
  }
  return out;
}

function _progressMap(rungs) {
  const map = new Map();
  const last = Math.max(1, rungs.length - 1);
  rungs.forEach((rung, index) => {
    const t = rungs.length === 1 ? 1 : index / last;
    for (const cell of rung) {
      map.set(`${cell[0]},${cell[1]}`, t);
    }
  });
  return map;
}

function _doubleFromStrip(cells, box) {
  const axis = box.w === 2 ? 'y' : 'x';
  const first = cells[0];
  const start = axis === 'y' ? first[1] : first[0];
  const min = axis === 'y' ? box.miny : box.minx;
  const max = axis === 'y' ? box.maxy : box.maxx;
  const from = start <= (min + max) / 2 ? min : max;
  const to = from === min ? max : min;
  const step = from <= to ? 1 : -1;
  const rungs = [];
  for (let v = from; ; v += step) {
    const rung = cells.filter((c) => (axis === 'y' ? c[1] : c[0]) === v);
    if (rung.length) rungs.push(rung);
    if (v === to) break;
  }
  return {
    kind: 'double',
    width: 2,
    cells,
    rungs,
    progress: _progressMap(rungs),
  };
}

function _doubleFromPairs(cells) {
  if (cells.length < 4 || cells.length % 2) return null;
  const rungs = [];
  for (let i = 0; i < cells.length; i += 2) {
    if (!_adjacent(cells[i], cells[i + 1])) return null;
    rungs.push([cells[i], cells[i + 1]]);
  }
  const mids = rungs.map(_centroid);
  let axis = null;
  for (let i = 1; i < mids.length; i += 1) {
    const dx = Math.abs(mids[i][0] - mids[i - 1][0]);
    const dy = Math.abs(mids[i][1] - mids[i - 1][1]);
    const here = dx >= dy ? 'x' : 'y';
    if (!axis) axis = here;
    else if (axis !== here) return null;
  }
  return {
    kind: 'double',
    width: 2,
    cells,
    rungs,
    progress: _progressMap(rungs),
  };
}

function _spiralFromCells(cells, box) {
  const cx = (box.minx + box.maxx + 1) / 2;
  const cy = (box.miny + box.maxy + 1) / 2;
  const outerR = Math.min(box.w, box.h) * 0.5 * 0.96;
  const innerR = Math.min(Math.max(0.14, outerR * 0.2), outerR * 0.34);
  let angles = _unwrapAngles(cells, cx, cy);
  const signs = _turnSigns(cells);
  const winding = signs.reduce((s, v) => s + v, 0) >= 0 ? 1 : -1;
  if (!angles.length) angles = [0];
  let a0 = angles[0];
  let a1 = angles[angles.length - 1];
  if (Math.abs(a1 - a0) < Math.PI * 0.7) {
    a1 = a0 + winding * Math.PI * 1.5;
    angles = cells.map((_, i) => a0 + (a1 - a0) * (i / Math.max(1, cells.length - 1)));
  }
  const progA0 = a0;
  const progA1 = a1;
  const progSpan = progA1 - progA0 || winding * Math.PI * 1.5;
  const closed = cells.length >= 4 && _adjacent(cells[0], cells[cells.length - 1]);
  const avgStep = (a1 - a0) / Math.max(1, cells.length - 1);
  a0 -= avgStep * 0.5;
  a1 += avgStep * 0.5;
  if (closed || (box.w === 2 && box.h === 2 && cells.length === 4)) {
    a1 = a0 + winding * Math.PI * 2;
  }
  const progress = new Map();
  cells.forEach((cell, i) => {
    const t = cells.length === 1 ? 1 : (angles[i] - progA0) / progSpan;
    progress.set(`${cell[0]},${cell[1]}`, Math.max(0, Math.min(1, t)));
  });
  return {
    kind: 'spiral',
    width: 1,
    cells,
    rungs: cells.map((c) => [c]),
    progress,
    cx,
    cy,
    radius: Math.max(0.35, (outerR + innerR) * 0.5),
    innerR,
    outerR,
    minx: box.minx,
    maxx: box.maxx,
    miny: box.miny,
    maxy: box.maxy,
    w: box.w,
    h: box.h,
    a0,
    a1,
    winding,
  };
}

function _isSpiralFootprint(cells, box) {
  if (cells.length < 4) return false;
  if (box.w === 2 && box.h === 2 && cells.length === 4) return true;
  const centerKey = `${box.minx + 1},${box.miny + 1}`;
  const set = new Set(cells.map((c) => `${c[0]},${c[1]}`));
  if (box.w === 3 && box.h === 3 && cells.length >= 7 && !set.has(centerKey)) return true;
  const signs = _turnSigns(cells);
  const turns = signs.filter((s) => s !== 0).length;
  if (Math.max(box.w, box.h) <= 3 && Math.min(box.w, box.h) >= 2 && turns >= 1) {
    return true;
  }
  if (box.w <= 4 && box.h <= 4 && Math.min(box.w, box.h) >= 2) {
    const net = signs.reduce((s, v) => s + v, 0);
    const same = signs.filter((s) => s === Math.sign(net) || net === 0).length;
    if (Math.abs(net) >= 3 && same >= 3) return true;
  }
  return false;
}

/**
 * Infer straight, double-wide, or spiral from the picked squares.
 * First square is the base; last is the top.
 */
export function classifyStairLayout(squares) {
  const cells = normalizeStairSquares(squares);
  if (cells.length <= 1) {
    return {
      kind: 'straight',
      width: 1,
      cells,
      rungs: cells.length ? [cells] : [],
      progress: _progressMap(cells.length ? [cells] : []),
    };
  }
  const box = _bbox(cells);
  const filled = cells.length === box.w * box.h;
  if (filled && ((box.w === 2 && box.h >= 3) || (box.h === 2 && box.w >= 3))) {
    return _doubleFromStrip(cells, box);
  }
  if (_isSpiralFootprint(cells, box)) {
    return _spiralFromCells(cells, box);
  }
  const paired = _doubleFromPairs(cells);
  if (paired) return paired;
  const rungs = cells.map((c) => [c]);
  return {
    kind: 'straight',
    width: 1,
    cells,
    rungs,
    progress: _progressMap(rungs),
  };
}

function _filletMids(mids, radius) {
  if (mids.length < 3 || radius <= 0) return mids.slice();
  const pts = [mids[0]];
  for (let i = 1; i < mids.length - 1; i += 1) {
    const prev = mids[i - 1];
    const cur = mids[i];
    const next = mids[i + 1];
    const inD = _dirOf(prev, cur);
    const outD = _dirOf(cur, next);
    const cross = inD[0] * outD[1] - inD[1] * outD[0];
    const dot = inD[0] * outD[0] + inD[1] * outD[1];
    const inLen = Math.hypot(cur[0] - prev[0], cur[1] - prev[1]);
    const outLen = Math.hypot(next[0] - cur[0], next[1] - cur[1]);
    const r = Math.min(radius, inLen * 0.48, outLen * 0.48);
    if (Math.abs(dot) > 0.4 || Math.abs(cross) < 0.55 || r < 0.05) {
      pts.push(cur);
      continue;
    }
    const side = cross >= 0 ? 1 : -1;
    const rot = side > 0 ? [-inD[1], inD[0]] : [inD[1], -inD[0]];
    const p1 = [cur[0] - inD[0] * r, cur[1] - inD[1] * r];
    const center = [p1[0] + rot[0] * r, p1[1] + rot[1] * r];
    const p2 = [cur[0] + outD[0] * r, cur[1] + outD[1] * r];
    let a0 = Math.atan2(p1[1] - center[1], p1[0] - center[0]);
    let a1 = Math.atan2(p2[1] - center[1], p2[0] - center[0]);
    if (side > 0) {
      while (a1 <= a0) a1 += Math.PI * 2;
    } else {
      while (a1 >= a0) a1 -= Math.PI * 2;
    }
    const nArc = 10;
    for (let k = 0; k <= nArc; k += 1) {
      const a = a0 + (a1 - a0) * (k / nArc);
      pts.push([center[0] + r * Math.cos(a), center[1] + r * Math.sin(a)]);
    }
  }
  pts.push(mids[mids.length - 1]);
  return pts;
}

function _centerline(layout, cellSize) {
  if (layout.kind === 'spiral') {
    const pts = [];
    const n = 36;
    const cx = layout.cx * cellSize;
    const cz = layout.cy * cellSize;
    const radius = ((layout.innerR + layout.outerR) * 0.5 || layout.radius) * cellSize;
    for (let i = 0; i <= n; i += 1) {
      const t = i / n;
      const a = layout.a0 + (layout.a1 - layout.a0) * t;
      pts.push([cx + radius * Math.cos(a), cz + radius * Math.sin(a)]);
    }
    return pts;
  }
  const rungs = layout.rungs || [];
  if (!rungs.length) return [];
  const mids = rungs.map((rung) => {
    const c = _centroid(rung);
    return [c[0] * cellSize, c[1] * cellSize];
  });
  const startDir = mids.length > 1 ? _dirOf(mids[0], mids[1]) : [0, 1];
  const endDir = mids.length > 1 ? _dirOf(mids[mids.length - 2], mids[mids.length - 1]) : [0, 1];
  const half = cellSize * 0.5;
  const filleted = _filletMids(mids, cellSize * 0.52);
  return [
    [mids[0][0] - startDir[0] * half, mids[0][1] - startDir[1] * half],
    ...filleted,
    [mids[mids.length - 1][0] + endDir[0] * half, mids[mids.length - 1][1] + endDir[1] * half],
  ];
}

function _polylineLength(pts) {
  let len = 0;
  for (let i = 1; i < pts.length; i += 1) {
    len += Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
  }
  return len;
}

function _samplePolyline(pts, distance) {
  if (pts.length < 2) {
    return { x: pts[0]?.[0] || 0, z: pts[0]?.[1] || 0, dx: 0, dz: 1 };
  }
  let remain = Math.max(0, distance);
  for (let i = 1; i < pts.length; i += 1) {
    const dx = pts[i][0] - pts[i - 1][0];
    const dy = pts[i][1] - pts[i - 1][1];
    const seg = Math.hypot(dx, dy) || 1e-6;
    if (remain <= seg) {
      const t = remain / seg;
      return {
        x: pts[i - 1][0] + dx * t,
        z: pts[i - 1][1] + dy * t,
        dx: dx / seg,
        dz: dy / seg,
      };
    }
    remain -= seg;
  }
  const n = pts.length;
  const dx = pts[n - 1][0] - pts[n - 2][0];
  const dy = pts[n - 1][1] - pts[n - 2][1];
  const seg = Math.hypot(dx, dy) || 1;
  return { x: pts[n - 1][0], z: pts[n - 1][1], dx: dx / seg, dz: dy / seg };
}

function _stepElevation(i, dh, height, solid) {
  const tread = dh * (i + 1);
  const slab = Math.max(0.04, Math.abs(dh));
  const fillTo = height >= 0 ? 0 : height;
  if (!solid) {
    return { y: tread - dh * 0.5, height: slab, tread };
  }
  const lo = Math.min(fillTo, tread);
  const hi = Math.max(fillTo, tread);
  return { y: (lo + hi) * 0.5, height: Math.max(slab, hi - lo), tread };
}

function _spiralWedgeSteps(layout, {
  height, count, dh, solid, cellSize, openWell = false, wallAttached = false,
}) {
  const cx = layout.cx * cellSize;
  const cz = layout.cy * cellSize;
  const wallT = THIN_WALL_THICKNESS;
  const half = Math.min(layout.w || 2, layout.h || 2) * 0.5 * cellSize;
  let outerR = (layout.outerR || layout.radius || 0.7) * cellSize;
  let innerR = (layout.innerR || 0.14) * cellSize;
  if (wallAttached) {
    outerR = half;
  }
  if (openWell) {
    innerR = Math.max(cellSize * 0.2, Math.min(outerR * 0.42, outerR - cellSize * 0.28));
  }
  const span = layout.a1 - layout.a0 || Math.PI * 1.5;
  const overlap = (span / Math.max(1, count)) * 0.08;
  const boxes = [];
  for (let i = 0; i < count; i += 1) {
    const t0 = i / count;
    const t1 = (i + 1) / count;
    const theta0 = layout.a0 + span * t0 - (i === 0 ? 0 : overlap);
    const theta1 = layout.a0 + span * t1 + (i === count - 1 ? 0 : overlap);
    const midA = (theta0 + theta1) * 0.5;
    const midR = (innerR + outerR) * 0.5;
    const elev = _stepElevation(i, dh, height, solid);
    boxes.push({
      shape: 'wedge',
      x: cx + midR * Math.cos(midA),
      y: elev.y,
      z: cz + midR * Math.sin(midA),
      cx,
      cz,
      innerR,
      outerR,
      theta0,
      theta1,
      width: outerR - innerR,
      height: elev.height,
      depth: Math.abs(theta1 - theta0) * midR,
      yaw: midA + Math.PI / 2,
    });
  }
  const rise = Math.abs(height) || 0.2;
  const midY = height >= 0 ? rise * 0.5 : height * 0.5;
  if (openWell) {
    boxes.push({
      shape: 'well_void',
      part: 'well',
      x: cx,
      y: height >= 0 ? 0.012 : height - 0.012,
      z: cz,
      radius: innerR * 0.98,
      width: innerR * 2,
      height: 0.02,
      depth: innerR * 2,
      yaw: 0,
    });
    boxes.push({
      shape: 'well_liner',
      part: 'well',
      x: cx,
      y: midY,
      z: cz,
      radius: innerR,
      width: innerR * 2,
      height: rise + 0.04,
      depth: innerR * 2,
      yaw: 0,
    });
  } else {
    const newelR = innerR * 1.08;
    boxes.push({
      shape: 'newel',
      part: 'newel',
      x: cx,
      y: midY,
      z: cz,
      radius: newelR,
      width: newelR * 2,
      height: rise + 0.08,
      depth: newelR * 2,
      yaw: 0,
    });
  }
  if (wallAttached && Number.isFinite(layout.minx) && Number.isFinite(layout.maxx)) {
    const x0 = layout.minx * cellSize;
    const x1 = (layout.maxx + 1) * cellSize;
    const z0 = layout.miny * cellSize;
    const z1 = (layout.maxy + 1) * cellSize;
    const spanX = x1 - x0;
    const spanZ = z1 - z0;
    const walls = [
      { x: (x0 + x1) * 0.5, z: z0, width: spanX, depth: wallT },
      { x: (x0 + x1) * 0.5, z: z1, width: spanX, depth: wallT },
      { x: x0, z: (z0 + z1) * 0.5, width: wallT, depth: spanZ },
      { x: x1, z: (z0 + z1) * 0.5, width: wallT, depth: spanZ },
    ];
    for (const wall of walls) {
      boxes.push({
        shape: 'well_wall',
        part: 'well_wall',
        x: wall.x,
        y: midY,
        z: wall.z,
        width: wall.width,
        height: rise + 0.02,
        depth: wall.depth,
        yaw: 0,
      });
    }
  }
  return boxes;
}

/** World-space step boxes along an ordered stair run (base → top). */
export function stairStepBoxes(squares, {
  heightFt = DEFAULT_STAIR_HEIGHT_FT,
  feetPerGrid = 5,
  cellSize = CELL_SIZE,
  solid = false,
  openWell = false,
  wallAttached = false,
} = {}) {
  const layout = classifyStairLayout(squares);
  if (!layout.cells.length) return [];
  const height = stairHeightWorld(heightFt, feetPerGrid);
  const count = stairStepCount(heightFt);
  const dh = height / count;
  if (layout.kind === 'spiral') {
    return _spiralWedgeSteps(layout, {
      height, count, dh, solid, cellSize, openWell, wallAttached,
    });
  }
  const pts = _centerline(layout, cellSize);
  const length = Math.max(cellSize * 0.5, _polylineLength(pts));
  const width = cellSize * (0.92 + 0.98 * Math.max(0, layout.width - 1));
  const boxes = [];
  for (let i = 0; i < count; i += 1) {
    const t0 = (i / count) * length;
    const t1 = ((i + 1) / count) * length;
    const mid = _samplePolyline(pts, (t0 + t1) * 0.5);
    const elev = _stepElevation(i, dh, height, solid);
    boxes.push({
      shape: 'box',
      x: mid.x,
      y: elev.y,
      z: mid.z,
      width,
      height: elev.height,
      depth: Math.max(0.07, (t1 - t0) * 1.22),
      yaw: Math.atan2(mid.dx, mid.dz),
    });
  }
  return boxes;
}

function _progressAt(layout, gridX, gridY) {
  const key = `${gridX},${gridY}`;
  if (layout.progress && layout.progress.has(key)) return layout.progress.get(key);
  const cells = layout.cells || [];
  const idx = cells.findIndex((pt) => pt[0] === gridX && pt[1] === gridY);
  if (idx < 0) return null;
  if (cells.length === 1) return 1;
  return idx / (cells.length - 1);
}

/** Standing height (world Y) for a token on a stair square. */
export function stairSurfaceY(gridX, gridY, objects, {
  feetPerGrid = 5,
} = {}) {
  const gx = Number(gridX);
  const gy = Number(gridY);
  for (const obj of objects || []) {
    const kind = String(obj.kind || obj.type || '').toLowerCase();
    if (kind !== 'stairs') continue;
    const layout = classifyStairLayout(obj.squares || [[obj.x, obj.y]]);
    const t = _progressAt(layout, gx, gy);
    if (t == null) continue;
    return stairHeightWorld(obj.height, feetPerGrid) * t;
  }
  return 0;
}

/** Descending stairs omit the battlemap floor after the base square / rung. */
export function stairHoleCells(objects) {
  const cells = [];
  for (const obj of objects || []) {
    const kind = String(obj.kind || obj.type || '').toLowerCase();
    if (kind !== 'stairs') continue;
    const height = Number(obj.height);
    if (!(height < 0)) continue;
    const layout = classifyStairLayout(obj.squares || [[obj.x, obj.y]]);
    for (const cell of layout.cells) {
      const t = _progressAt(layout, cell[0], cell[1]);
      if (t > 0.02) cells.push(cell);
    }
  }
  return cells;
}

export function waterMaskBytes(size, cells) {
  const width = Number(size?.[0]) || 0;
  const height = Number(size?.[1]) || 0;
  const data = new Uint8Array(Math.max(0, width * height));
  for (const cell of cells || []) {
    const { x, y } = waterCellXY(cell);
    if (x < 0 || y < 0 || x >= width || y >= height) continue;
    data[y * width + x] = 255;
  }
  return { width, height, data };
}
