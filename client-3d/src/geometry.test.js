import { describe, expect, it } from 'vitest';
import {
  cellCenter,
  doorAssemblies,
  doorBoxes,
  doorModelKind,
  edgeWallBoxes,
  floorPlane,
  floorQuads,
  foggedCells,
  fogMaskBytes,
  fogMaskCells,
  cellMaskBytes,
  pointInPolygon,
  groupBoxesBySize,
  classifyStairLayout,
  hatchHoleCells,
  chasmDepthWorld,
  CHASM_DEFAULT_DEPTH,
  boxFaceUVRepeats,
  orbitCameraDistance,
  clampIllumination,
  ambientLightIntensities,
  distanceFogRange,
  inBounds,
  nearestWallEdge,
  portcullisAssemblies,
  secretDoorAssemblies,
  windowAssemblies,
  solidWallBoxes,
  stairHoleCells,
  stairStepBoxes,
  stairStepCount,
  stairSurfaceY,
  DEFAULT_STAIR_HEIGHT_FT,
  torchWorldOffset,
  waterBasinBoxes,
  waterCellSet,
  waterMaskBytes,
  waterSurfaceBoxes,
  WATER_DEPTH,
  worldToGrid,
} from './geometry.js';

describe('geometry', () => {
  it('maps cell centers and inverse world→grid', () => {
    const c = cellCenter(3, 4);
    expect(c.x).toBe(3.5);
    expect(c.z).toBe(4.5);
    expect(worldToGrid(3.1, 4.9)).toEqual({ x: 3, y: 4 });
    expect(worldToGrid(0, 0)).toEqual({ x: 0, y: 0 });
  });

  it('maps illumination 0–1 onto 3D ambient light intensities', () => {
    expect(clampIllumination(-1)).toBe(0);
    expect(clampIllumination(2)).toBe(0.02);
    expect(clampIllumination(50)).toBe(0.5);
    expect(clampIllumination(100)).toBe(1);
    expect(clampIllumination('nope', 0.4)).toBe(0.4);
    const dark = ambientLightIntensities(0);
    const bright = ambientLightIntensities(1);
    expect(dark.hemi).toBeLessThan(bright.hemi);
    expect(dark.dir).toBeLessThan(bright.dir);
    expect(dark.ambient).toBeLessThan(bright.ambient);
    expect(dark.hemi).toBeGreaterThan(0.25);
    expect(bright.hemi).toBeCloseTo(1.37);
  });

  it('keeps the default orbit camera in front of distance fog', () => {
    const tavern = [40, 30];
    const dark = distanceFogRange(tavern, 0);
    const lit = distanceFogRange(tavern, 1);
    const camera = orbitCameraDistance(tavern);
    expect(dark.near).toBeGreaterThan(camera);
    expect(lit.near).toBeGreaterThan(dark.near);
    expect(lit.far).toBeGreaterThan(lit.near);
  });

  it('builds a floor plane covering the grid', () => {
    const floor = floorPlane([10, 6]);
    expect(floor.width).toBe(10);
    expect(floor.depth).toBe(6);
    expect(floor.x).toBe(5);
    expect(floor.z).toBe(3);
  });

  it('extrudes solid # walls as boxes on cell centers', () => {
    const boxes = solidWallBoxes([[0, 0], [2, 1]]);
    expect(boxes).toHaveLength(2);
    expect(boxes[0].x).toBe(0.5);
    expect(boxes[0].z).toBe(0.5);
    expect(boxes[0].height).toBeGreaterThan(1);
    expect(boxes[1].x).toBe(2.5);
    expect(boxes[1].z).toBe(1.5);
  });

  it('places thin walls on the named cell edges', () => {
    const boxes = edgeWallBoxes([{ x: 3, y: 4, edges: ['n', 'e'] }]);
    expect(boxes).toHaveLength(2);
    const north = boxes.find((b) => b.edge === 'n');
    const east = boxes.find((b) => b.edge === 'e');
    expect(north.z).toBe(4);
    expect(east.x).toBe(4);
  });

  it('marks open doors without dropping the mesh descriptor', () => {
    const boxes = doorBoxes([{ id: 'd1', x: 1, y: 2, edges: ['s'], open: true }]);
    expect(boxes).toHaveLength(1);
    expect(boxes[0].open).toBe(true);
    expect(boxes[0].id).toBe('d1');
    expect(boxes[0].z).toBe(3);
  });

  it('models a hinged door with a stone frame and swinging panel', () => {
    const north = doorAssemblies([{ id: 'd1', x: 2, y: 3, edges: ['n'], open: false }])[0];
    expect(north.frame).toHaveLength(3);
    expect(north.panel.width).toBeLessThan(1);
    expect(north.panel.height).toBeGreaterThan(1);
    expect(north.hinge.x).toBeCloseTo(2.1);
    expect(north.hinge.z).toBe(3);
    expect(north.yaw).toBe(0);
    expect(north.openDelta).toBeGreaterThan(0);

    const southOpen = doorAssemblies([{ id: 'd2', x: 2, y: 3, edges: ['s'], open: true }])[0];
    expect(southOpen.open).toBe(true);
    expect(southOpen.openDelta).toBeLessThan(0);
    expect(southOpen.yaw).toBeCloseTo(southOpen.closedYaw + southOpen.openDelta);

    const west = doorAssemblies([{ id: 'd3', x: 4, y: 1, edges: ['w'] }])[0];
    expect(west.closedYaw).toBeCloseTo(Math.PI / 2);
    expect(west.openDelta).toBeLessThan(0);
  });

  it('pairs adjacent same-edge oak doors into one double door', () => {
    const east = doorAssemblies([
      { id: 'north', x: 2, y: 10, edges: ['e'], open: false },
      { id: 'south', x: 2, y: 11, edges: ['e'], open: true },
    ]);
    expect(east).toHaveLength(1);
    expect(east[0].double).toBe(true);
    expect(east[0].partnerId).toBe('south');
    expect(east[0].frame).toHaveLength(3);
    const lintel = east[0].frame.find((box) => box.depth > 1.5);
    expect(lintel.depth).toBe(2);
    expect(east[0].leaves).toHaveLength(2);
    expect(east[0].leaves[0].hinge.z).toBeCloseTo(10.1);
    expect(east[0].leaves[1].hinge.z).toBeCloseTo(11.9);
    expect(east[0].leaves[0].hinge.x).toBe(3);
    expect(east[0].leaves[1].hinge.x).toBe(3);
    expect(east[0].leaves[0].panel.width).toBeLessThan(1);
    expect(east[0].leaves[1].open).toBe(true);
    expect(east[0].leaves[0].openDelta).toBeGreaterThan(0);
    expect(east[0].leaves[1].openDelta).toBeLessThan(0);

    const alongX = doorAssemblies([
      { id: 'west', x: 4, y: 1, edges: ['n'] },
      { id: 'east', x: 5, y: 1, edges: ['n'] },
    ])[0];
    expect(alongX.double).toBe(true);
    expect(alongX.leaves[0].hinge.x).toBeCloseTo(4.1);
    expect(alongX.leaves[1].hinge.x).toBeCloseTo(5.9);
    expect(alongX.leaves[1].closedYaw).toBeCloseTo(Math.PI);

    expect(doorAssemblies([
      { id: 'a', x: 2, y: 10, edges: ['e'] },
      { id: 'b', x: 3, y: 10, edges: ['e'] },
    ])).toHaveLength(2);

    const skipped = doorAssemblies([
      { id: 'keep', x: 2, y: 10, edges: ['e'], double: false },
      { id: 'pair', x: 2, y: 11, edges: ['e'] },
    ]);
    expect(skipped).toHaveLength(2);
    expect(skipped.every((door) => !door.double)).toBe(true);

    const triple = doorAssemblies([
      { id: 'a', x: 1, y: 3, edges: ['w'] },
      { id: 'b', x: 1, y: 4, edges: ['w'] },
      { id: 'c', x: 1, y: 5, edges: ['w'] },
    ]);
    expect(triple).toHaveLength(2);
    expect(triple.filter((door) => door.double)).toHaveLength(1);
  });

  it('does not pair same-edge doors when a wall splits the opening', () => {
    const doors = [
      { id: 'bedroom', x: 2, y: 9, edges: ['e'] },
      { id: 'northLeaf', x: 2, y: 10, edges: ['e'] },
      { id: 'southLeaf', x: 2, y: 11, edges: ['e'] },
    ];
    const edgeWalls = [
      { x: 2, y: 9, edges: ['s'] },
      { x: 2, y: 10, edges: ['n'] },
      { x: 2, y: 11, edges: ['s'] },
    ];
    const assemblies = doorAssemblies(doors, { edgeWalls });
    expect(assemblies).toHaveLength(2);
    const paired = assemblies.find((door) => door.double);
    const single = assemblies.find((door) => !door.double);
    expect(paired.gridY).toBe(10);
    expect(paired.partnerId).toBe('southLeaf');
    expect(single.id).toBe('bedroom');
  });

  it('keeps oak doors distinct from secret slabs and portcullises', () => {
    const mixed = [
      { id: 'oak', x: 1, y: 1, edges: ['n'] },
      { id: 'hid', x: 2, y: 1, edges: ['n'], kind: 'secret_door', open: false },
      { id: 'gate', x: 3, y: 1, edges: ['s'], kind: 'portcullis', open: true },
    ];
    expect(doorAssemblies(mixed)).toHaveLength(1);
    expect(doorAssemblies(mixed)[0].id).toBe('oak');
    const secret = secretDoorAssemblies(mixed)[0];
    expect(secret.kind).toBe('secret_door');
    expect(secret.lift).toBe(0);
    expect(secret.slab.height).toBeGreaterThan(1);
    const openSecret = secretDoorAssemblies([{ ...mixed[1], open: true }])[0];
    expect(openSecret.lift).toBeGreaterThan(1);
    const gate = portcullisAssemblies(mixed)[0];
    expect(gate.kind).toBe('portcullis');
    expect(gate.lift).toBeGreaterThan(1);
    expect(gate.bars).toBe(9);
    expect(doorModelKind({ kind: 'portcullis' })).toBe('portcullis');
  });

  it('models a casement window with a sill, glass pane, and optional bars', () => {
    const closed = windowAssemblies([{
      id: 'w1', x: 2, y: 3, edges: ['n'], kind: 'window', cover_pane: 'glass',
    }])[0];
    expect(closed.kind).toBe('window');
    expect(closed.coverPane).toBe('glass');
    expect(closed.apron).toHaveLength(1);
    expect(closed.transom).toHaveLength(1);
    expect(closed.frame.length).toBeGreaterThanOrEqual(4);
    expect(closed.panel.height).toBeLessThan(1);
    expect(closed.hinge.y).toBeGreaterThan(0.3);
    const transomTop = closed.transom[0].y + closed.transom[0].height / 2;
    expect(transomTop).toBeCloseTo(1.6);
    expect(closed.open).toBe(false);

    const openBarred = windowAssemblies([{
      id: 'w2', x: 2, y: 3, edges: ['s'], kind: 'window', open: true, barred: true,
      cover_pane: 'opaque', window_material: 'wood', wall_material: 'stone',
    }])[0];
    expect(openBarred.open).toBe(true);
    expect(openBarred.barred).toBe(true);
    expect(openBarred.coverPane).toBe('opaque');
    expect(openBarred.yaw).toBeCloseTo(openBarred.closedYaw + openBarred.openDelta);
  });

  it('groups boxes by size for instanced wall meshes', () => {
    const groups = groupBoxesBySize([
      { width: 1, height: 1.6, depth: 1 },
      { width: 1, height: 1.6, depth: 1 },
      { width: 1, height: 1.6, depth: 0.11 },
    ]);
    expect(groups.size).toBe(2);
    expect([...groups.values()][0]).toHaveLength(2);
  });

  it('scales box-face UVs by world size so stone tiles across a wall', () => {
    const faces = boxFaceUVRepeats(1, 1.6, 0.11);
    expect(faces).toHaveLength(6);
    expect(faces[4]).toEqual([1, 1.6]);
    expect(faces[0][0]).toBeCloseTo(0.11);
    expect(faces[0][1]).toBe(1.6);
    const locked = boxFaceUVRepeats(1, 1.6, 0.11, 1, { lockHeight: true });
    expect(locked[4]).toEqual([1, 1]);
    expect(locked[0][1]).toBe(1);
  });

  it('filters fogged tiles and bounds-checks clicks', () => {
    expect(inBounds(0, 0, [4, 3])).toBe(true);
    expect(inBounds(4, 0, [4, 3])).toBe(false);
    const fog = foggedCells([
      { x: 0, y: 0, line_of_sight: true },
      { x: 1, y: 1, line_of_sight: false, light: 0 },
    ]);
    expect(fog).toEqual([{ x: 1, y: 1, light: 0 }]);
  });

  it('masks weather fog to polygon cells', () => {
    expect(pointInPolygon(1.5, 1.5, [[0, 0], [3, 0], [3, 3], [0, 3]])).toBe(true);
    expect(pointInPolygon(4.5, 1.5, [[0, 0], [3, 0], [3, 3], [0, 3]])).toBe(false);
    const cells = fogMaskCells({
      mask: true,
      mask_layers: [{ type: 'polygon', points: [[0, 0], [2, 0], [2, 2], [0, 2]] }],
    }, [4, 3]);
    const keys = cells.map((c) => `${c.x},${c.y}`).sort();
    expect(keys).toContain('0,0');
    expect(keys).toContain('1,1');
    expect(keys).not.toContain('3,0');
    const bytes = fogMaskBytes({ mask: true, mask_layers: [{ type: 'rectangle', position: [1, 1], size: [1, 1] }] }, [3, 3]);
    expect(bytes.data[1 * 3 + 1]).toBe(255);
    expect(bytes.data[0]).toBe(0);
  });

  it('builds an upsampled soft-edge cell mask for overlay textures', () => {
    const mask = cellMaskBytes([{ x: 1, y: 1 }], [3, 2], 4);
    expect(mask.width).toBe(12);
    expect(mask.height).toBe(8);
    // Interior of the marked cell stays uniform...
    expect(mask.data[5 * 12 + 5]).toBe(255);
    expect(mask.data[6 * 12 + 6]).toBe(255);
    // ...and unmarked cells stay fully transparent.
    expect(mask.data[0]).toBe(0);
    expect(mask.data[1 * 12 + 0]).toBe(0);
    // The ramp lives in the one-sample step across the cell border, so the
    // sample just inside the border is still lit (feather stays narrow).
    expect(mask.data[4 * 12 + 4]).toBe(255); // just inside (1,1)
    expect(mask.data[4 * 12 + 3]).toBe(0); // just outside on the x side
  });

  it('keeps adjacent marked cells seamless in the cell mask', () => {
    const mask = cellMaskBytes([{ x: 0, y: 0 }, { x: 1, y: 0 }], [2, 1], 4);
    // Every sample along the shared border column pair is fully marked, so
    // neighboring shadowed squares merge without a dark seam or bright gap.
    expect(mask.data[0 * 8 + 3]).toBe(255);
    expect(mask.data[0 * 8 + 4]).toBe(255);
    expect(mask.data.every((v) => v === 255)).toBe(true);
  });

  it('hangs a torch on the nearest solid wall edge', () => {
    const geometry = {
      solid_walls: [[0, 0], [1, 0], [2, 0], [0, 1], [2, 1], [0, 2], [1, 2], [2, 2]],
      edge_walls: [],
    };
    expect(nearestWallEdge(1, 1, geometry)).toBe('n');
    const offset = torchWorldOffset('n', 1);
    expect(offset.z).toBeCloseTo(-0.42);
    expect(offset.x).toBe(0);
    expect(torchWorldOffset('e', 1).x).toBeCloseTo(0.42);
  });

  it('builds an open water basin and drapes the battlemap on the pool bed', () => {
    const basins = waterBasinBoxes([[2, 3]]);
    expect(basins.some((box) => box.part === 'bed')).toBe(false);
    expect(basins.filter((box) => ['n', 's', 'e', 'w'].includes(box.part))).toHaveLength(4);
    const lids = basins.filter((box) => {
      const top = box.y + box.height / 2;
      return Math.abs(top) < 0.02 && box.width > 0.5 && box.depth > 0.5;
    });
    expect(lids).toHaveLength(0);
    const surface = waterSurfaceBoxes([[2, 3]]);
    expect(surface[0].y).toBe(0);
    expect(surface[0].width).toBe(1);
    const joined = waterBasinBoxes([[0, 0], [1, 0]]);
    expect(joined.some((box) => box.part === 'e' && box.gridX === 0)).toBe(false);
    expect(joined.some((box) => box.part === 'w' && box.gridX === 1)).toBe(false);
    const mask = waterMaskBytes([4, 3], [[1, 1], [3, 2]]);
    expect(mask.data[1 * 4 + 1]).toBe(255);
    expect(mask.data[0]).toBe(0);
    const quads = floorQuads([3, 2], [[1, 0], [2, 1]]);
    expect(quads.quadCount).toBe(6);
    expect(waterCellSet([[1, 0]]).has('1,0')).toBe(true);
    expect(quads.uvs[0]).toBe(0);
    expect(quads.uvs[1]).toBe(1);
    expect(quads.positions[1]).toBe(0);
    const waterVert = 4;
    expect(quads.positions[waterVert * 3 + 1]).toBeCloseTo(-WATER_DEPTH);
    const i0 = quads.indices[0];
    const i1 = quads.indices[1];
    const i2 = quads.indices[2];
    const ax = quads.positions[i1 * 3] - quads.positions[i0 * 3];
    const ay = quads.positions[i1 * 3 + 1] - quads.positions[i0 * 3 + 1];
    const az = quads.positions[i1 * 3 + 2] - quads.positions[i0 * 3 + 2];
    const bx = quads.positions[i2 * 3] - quads.positions[i0 * 3];
    const by = quads.positions[i2 * 3 + 1] - quads.positions[i0 * 3 + 1];
    const bz = quads.positions[i2 * 3 + 2] - quads.positions[i0 * 3 + 2];
    expect(ay * bz - az * by).toBeCloseTo(0);
    expect(az * bx - ax * bz).toBeGreaterThan(0);
    expect(ax * by - ay * bx).toBeCloseTo(0);
  });

  it('omits floor quads for open hatches and sprung pits', () => {
    const objects = [
      { kind: 'trap_door', x: 1, y: 0, opened: true },
      { kind: 'trap_door', x: 0, y: 0, opened: false },
      { kind: 'pit_trap', x: 2, y: 1, activated: true },
      { kind: 'pit_trap', x: 0, y: 1, activated: false, disarmed: false },
    ];
    expect(hatchHoleCells(objects)).toEqual([[1, 0], [2, 1]]);
    const quads = floorQuads([3, 2], [], 1, hatchHoleCells(objects));
    expect(quads.quadCount).toBe(4);
    expect(hatchHoleCells([{ kind: 'chasm', x: 0, y: 1 }])).toEqual([[0, 1]]);
    expect(chasmDepthWorld({})).toBe(CHASM_DEFAULT_DEPTH);
    expect(chasmDepthWorld({ fall_distance: 10 })).toBeCloseTo(2);
    expect(chasmDepthWorld({ fall_distance: 100 })).toBe(3.2);
  });

  it('builds medium-creature stair steps and token heights', () => {
    expect(stairStepCount(DEFAULT_STAIR_HEIGHT_FT)).toBe(13);
    const boxes = stairStepBoxes([[0, 0], [0, 1]], { heightFt: 8, cellSize: 1 });
    expect(boxes.length).toBe(13);
    expect(boxes[0].y).toBeGreaterThan(0);
    expect(boxes[boxes.length - 1].y).toBeCloseTo(1.6 - (1.6 / 13) * 0.5, 5);
    const stairs = [{
      kind: 'stairs',
      x: 2,
      y: 3,
      squares: [[2, 3], [2, 4], [2, 5]],
      height: 8,
    }];
    expect(stairSurfaceY(2, 3, stairs)).toBe(0);
    expect(stairSurfaceY(2, 4, stairs)).toBeCloseTo(0.8);
    expect(stairSurfaceY(2, 5, stairs)).toBeCloseTo(1.6);
    expect(stairSurfaceY(0, 0, stairs)).toBe(0);
    const down = [{
      kind: 'stairs',
      x: 1,
      y: 1,
      squares: [[1, 1], [1, 2], [1, 3]],
      height: -8,
    }];
    expect(stairHoleCells(down)).toEqual([[1, 2], [1, 3]]);
    expect(stairHoleCells(stairs)).toEqual([]);
    expect(stairSurfaceY(1, 3, down)).toBeCloseTo(-1.6);
    const filled = stairStepBoxes([[0, 0], [0, 1]], { heightFt: 8, cellSize: 1, solid: true });
    expect(filled[filled.length - 1].height).toBeCloseTo(1.6);
    expect(filled[filled.length - 1].y).toBeCloseTo(0.8);
    const open = stairStepBoxes([[0, 0], [0, 1]], { heightFt: 8, cellSize: 1, solid: false });
    expect(open[open.length - 1].height).toBeLessThan(0.2);
  });

  it('detects double-wide and spiral stair layouts', () => {
    const column = [[2, 3], [2, 4], [2, 5]];
    expect(classifyStairLayout(column).kind).toBe('straight');
    const double = [
      [0, 0], [1, 0],
      [0, 1], [1, 1],
      [0, 2], [1, 2],
    ];
    const dbl = classifyStairLayout(double);
    expect(dbl.kind).toBe('double');
    expect(dbl.width).toBe(2);
    const snake = [
      [0, 0], [1, 0], [1, 1], [0, 1], [0, 2], [1, 2],
    ];
    expect(classifyStairLayout(snake).kind).toBe('double');
    const wideBoxes = stairStepBoxes(double, { heightFt: 8, cellSize: 1 });
    expect(wideBoxes[0].width).toBeCloseTo(1.9);
    expect(stairSurfaceY(0, 1, [{ kind: 'stairs', squares: double, height: 8 }]))
      .toBeCloseTo(stairSurfaceY(1, 1, [{ kind: 'stairs', squares: double, height: 8 }]));
    const spiralSq = [[0, 0], [1, 0], [1, 1], [0, 1]];
    expect(classifyStairLayout(spiralSq).kind).toBe('spiral');
    const spiralLayout = classifyStairLayout(spiralSq);
    expect(Math.abs(spiralLayout.a1 - spiralLayout.a0)).toBeCloseTo(Math.PI * 2, 5);
    expect(spiralLayout.outerR).toBeGreaterThan(spiralLayout.innerR);
    const spiralBoxes = stairStepBoxes(spiralSq, { heightFt: 8, cellSize: 1 });
    const wedges = spiralBoxes.filter((b) => b.shape === 'wedge');
    const yaws = new Set(wedges.map((b) => b.yaw.toFixed(2)));
    expect(yaws.size).toBeGreaterThan(3);
    expect(wedges.length).toBe(13);
    expect(spiralBoxes.some((b) => b.part === 'newel' && b.shape === 'newel')).toBe(true);
    for (let i = 1; i < wedges.length; i += 1) {
      const gap = wedges[i].theta0 - wedges[i - 1].theta1;
      expect(Math.sign(spiralLayout.a1 - spiralLayout.a0) * gap).toBeLessThanOrEqual(0.001);
    }
    const ring = [
      [0, 0], [1, 0], [2, 0],
      [2, 1], [2, 2], [1, 2], [0, 2], [0, 1],
    ];
    expect(classifyStairLayout(ring).kind).toBe('spiral');
    const twoFlight = [[4, 7], [5, 7], [6, 7], [6, 6], [6, 5]];
    expect(classifyStairLayout(twoFlight).kind).toBe('spiral');
    const twoFlightBoxes = stairStepBoxes(twoFlight, { heightFt: 8, cellSize: 1 });
    expect(twoFlightBoxes.filter((b) => b.shape === 'wedge').length).toBe(13);
    expect(twoFlightBoxes.some((b) => b.part === 'newel')).toBe(true);
    const openWell = stairStepBoxes(spiralSq, {
      heightFt: 8, cellSize: 1, openWell: true, wallAttached: true,
    });
    const openWedges = openWell.filter((b) => b.shape === 'wedge');
    const closedWedges = spiralBoxes.filter((b) => b.shape === 'wedge');
    expect(openWell.some((b) => b.part === 'newel')).toBe(false);
    expect(openWell.some((b) => b.shape === 'well_void')).toBe(true);
    expect(openWell.filter((b) => b.shape === 'well_wall').length).toBe(4);
    expect(openWedges[0].innerR).toBeGreaterThan(closedWedges[0].innerR);
    expect(openWedges[0].outerR).toBeCloseTo(1, 5);
  });
});
