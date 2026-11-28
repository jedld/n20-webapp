import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import {
  CELL_SIZE,
  DOOR_HEIGHT,
  WALL_HEIGHT,
  cellCenter,
  cellMaskBytes,
  doorAssemblies,
  edgeWallBoxes,
  floorPlane,
  floorQuads,
  foggedCells,
  groupBoxesBySize,
  hatchHoleCells,
  inBounds,
  portcullisAssemblies,
  windowAssemblies,
  secretDoorAssemblies,
  solidWallBoxes,
  stairHoleCells,
  stairSurfaceY,
  waterCellSet,
  worldToGrid,
  clampIllumination,
  ambientLightIntensities,
  distanceFogRange,
} from '../geometry.js';
import { tokenAssetUrl, isFlyingPose, isPronePose, tokenBaseY, FLY_BOB } from './tokens.js';
import { AOE_COLOR, SELECT_COLOR } from './overlays.js';
import { buildAttackArrow, buildFlowArrow, flatRingGeometry } from './flowArrow.js';
import { FxRuntime } from '../fx/FxRuntime.js';
import { aurasForEffects, creatureAuraRings } from '../fx/statusAuras.js';
import { doorPanelMaterials, disposeVttMaterials, loadVttMaterials, texturedBoxGeometry, wallSpecFromState } from './materials.js';
import { addCaveWalls } from './caveWalls.js';
import { addFixture, addTokenLight } from './fixtures.js';
import { addCampaignModel, addPortcullis, addSecretDoor, addWindow } from './barriers.js';
import { buildFigure } from './tokenModels.js';
import {
  TOPPLE_MAX_SECONDS,
  applyTopplePose,
  createToppler,
  fallbackTopplePose,
  gatherHullPoints,
  isAtRest,
  runTopple,
  shortestAngle,
  yawToDirection,
} from './topple.js';
import { addWaterTerrain } from './water.js';
import { MAX_DECOR_LIGHTS } from './fixtureKinds.js';

const FLOOR_FALLBACK = 0x3a3f4a;
const FOG_COLOR = 0x05060a;
const GRID_COLOR = 0x1c2230;

function disposeObject(obj) {
  obj.traverse((child) => {
    if (child.geometry && !(child.geometry.userData && child.geometry.userData.shared)) {
      child.geometry.dispose();
    }
    const mat = child.material;
    if (!mat) return;
    const mats = Array.isArray(mat) ? mat : [mat];
    for (const m of mats) {
      if (!m || m.userData.shared) continue;
      if (m.map) m.map.dispose();
      if (m.alphaMap) m.alphaMap.dispose();
      if (m.bumpMap && m.bumpMap !== m.map) m.bumpMap.dispose();
      if (m.roughnessMap && m.roughnessMap !== m.map) m.roughnessMap.dispose();
      m.dispose();
    }
  });
}

export class MapScene {
  constructor(canvas) {
    this.canvas = canvas;
    this.cellSize = CELL_SIZE;
    this.state = null;
    this.tokenMeshes = new Map();
    this.hpChips = new Map();
    this.rangeChip = null;
    this.hudLayer = document.getElementById('token-hud-layer');
    this.hudHooks = [];
    this.raycaster = new THREE.Raycaster();
    this.pointer = new THREE.Vector2();
    this.clock = new THREE.Clock();
    this.tweens = [];
    this.fxTtl = [];
    // 3D figure pose state, persisted across full scene rebuilds (every
    // /map_state refresh recreates the token groups):
    this.tokenYaw = new Map();       // uid -> facing yaw (radians)
    this._proneState = new Map();    // uid -> last prone flag (transition detect)
    this._topplePoses = new Map();   // uid -> settled physics pose {quaternion, position}
    this._selectedUid = null;        // token with the green base ring + rotate knob
    this._selectionGroup = null;
    this._rotationGhost = null;      // drag-time "facing" line
    this._dragRotationUid = null;
    this._toppling = [];             // live physics fall animations

    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.setClearColor(0x0b0d12, 1);
    this.renderer.shadowMap.enabled = false;

    this.scene = new THREE.Scene();
    this.scene.fog = new THREE.Fog(0x0b0d12, 48, 140);

    this.camera = new THREE.PerspectiveCamera(45, 1, 0.1, 500);
    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;
    this.controls.maxPolarAngle = Math.PI / 2.05;
    this.controls.minDistance = 4;
    this.controls.maxDistance = 80;
    this.controls.screenSpacePanning = true;
    this.controls.mouseButtons = {
      LEFT: THREE.MOUSE.PAN,
      MIDDLE: THREE.MOUSE.DOLLY,
      RIGHT: THREE.MOUSE.ROTATE,
    };

    this.root = new THREE.Group();
    this.scene.add(this.root);
    this.overlayRoot = new THREE.Group();
    this.scene.add(this.overlayRoot);
    this.fxRoot = new THREE.Group();
    this.scene.add(this.fxRoot);
    this.fx = new FxRuntime(this);

    this.hemi = new THREE.HemisphereLight(0xc9d6e8, 0x2a241c, 0.85);
    this.scene.add(this.hemi);
    this.dir = new THREE.DirectionalLight(0xfff3d6, 0.65);
    this.dir.position.set(8, 18, 6);
    this.scene.add(this.dir);
    this.ambientFill = new THREE.AmbientLight(0x404040, 0.25);
    this.scene.add(this.ambientFill);
    this.decorLights = [];
    this._illumination = 1;

    this.floorMesh = null;
    this._vttMaterials = null;
    this._vttMaterialsKey = null;
    this.waterSim = null;
    this._onResize = () => this.resize();
    window.addEventListener('resize', this._onResize);
    this.resize();
    this._loop = this._loop.bind(this);
    requestAnimationFrame(this._loop);
  }

  resize() {
    const w = this.canvas.clientWidth || window.innerWidth;
    const h = this.canvas.clientHeight || window.innerHeight;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / Math.max(1, h);
    this.camera.updateProjectionMatrix();
  }

  resetCamera() {
    const size = this.state?.map?.size || [16, 16];
    const width = size[0] * this.cellSize;
    const depth = size[1] * this.cellSize;
    const cx = width / 2;
    const cz = depth / 2;
    this.controls.target.set(cx, 0, cz);
    this.camera.position.set(cx + Math.max(width, 8) * 0.35, Math.max(width, depth) * 0.7, cz + Math.max(depth, 8) * 0.7);
    this.controls.update();
  }

  setTopDown(enabled) {
    const size = this.state?.map?.size || [16, 16];
    const width = size[0] * this.cellSize;
    const depth = size[1] * this.cellSize;
    const cx = width / 2;
    const cz = depth / 2;
    this.controls.target.set(cx, 0, cz);
    if (enabled) {
      this.camera.position.set(cx, Math.max(width, depth) * 1.1, cz + 0.01);
    } else {
      this.resetCamera();
    }
    this.controls.update();
  }

  zoomBy(delta) {
    this.camera.position.multiplyScalar(delta > 0 ? 0.9 : 1.1);
    this.controls.update();
  }

  gridFromEvent(event) {
    const rect = this.canvas.getBoundingClientRect();
    this.pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    this.pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
    this.raycaster.setFromCamera(this.pointer, this.camera);
    if (!this.floorMesh) return null;
    const hits = this.raycaster.intersectObject(this.floorMesh, false);
    if (!hits.length) return null;
    const p = hits[0].point;
    const g = worldToGrid(p.x, p.z, this.cellSize);
    if (!inBounds(g.x, g.y, this.state?.map?.size)) return null;
    return g;
  }

  focusEntity(uid) {
    const mesh = this.tokenMeshes.get(uid);
    if (!mesh) return;
    this.controls.target.copy(mesh.position);
    const offset = new THREE.Vector3(4, 7, 5);
    this.camera.position.copy(mesh.position).add(offset);
    this.controls.update();
  }

  async applyState(state) {
    this.state = state;
    // cosmetic facing persisted by the server (entities[].rotation, degrees)
    for (const e of state.entities || []) {
      if (Number.isFinite(e.rotation) && this._dragRotationUid !== e.uid) {
        const yaw = THREE.MathUtils.degToRad(e.rotation);
        if (this.tokenYaw.get(e.uid) !== yaw) this.tokenYaw.set(e.uid, yaw);
      }
    }
    this._clearRoot();
    await this._buildFloor(state);
    await this._buildWater(state);
    await this._buildWalls(state);
    this._buildFog(state);
    await this._buildTokens(state);
    await this._buildObjectMarkers(state);
    this._syncHpChips();
    this.fx.onMapReady();
    this.fx.applyMapStateEffects(state.effects || []);
    // keep the selection ring + rotate knob glued to the (rebuilt) token
    if (this._selectedUid) {
      const keep = this._selectedUid;
      this.clearSelection();
      if ((state.entities || []).some((e) => e.uid === keep)) this.setSelected(keep);
    }
    if (!this._didInitCamera) {
      this.resetCamera();
      this._didInitCamera = true;
    }
  }

  _clearRoot() {
    this.waterSim = null;
    disposeObject(this.root);
    this.scene.remove(this.root);
    this.root = new THREE.Group();
    this.scene.add(this.root);
    this.tokenMeshes.clear();
    this.floorMesh = null;
    this.decorLights = [];
    this._clearOverlays();
  }

  _clearOverlays() {
    disposeObject(this.overlayRoot);
    this.scene.remove(this.overlayRoot);
    this.overlayRoot = new THREE.Group();
    this.scene.add(this.overlayRoot);
    this._rangeLabel = null;
    this._pathFlow = null;
  }

  async _buildFloor(state) {
    const plane = floorPlane(state.map.size, this.cellSize);
    const holes = hatchHoleCells(state.objects || []).concat(stairHoleCells(state.objects || []));
    const quads = floorQuads(state.map.size, state.geometry?.water || [], this.cellSize, holes);
    let material;
    const bg = state.map.background;
    if (bg) {
      const texture = await this._loadTexture(bg);
      if (texture) {
        texture.colorSpace = THREE.SRGBColorSpace;
        material = new THREE.MeshStandardMaterial({ map: texture, roughness: 0.95, metalness: 0 });
      }
    }
    if (!material) {
      material = new THREE.MeshStandardMaterial({ color: FLOOR_FALLBACK, roughness: 1, metalness: 0 });
    }
    if (quads.quadCount) {
      const geo = new THREE.BufferGeometry();
      geo.setAttribute('position', new THREE.Float32BufferAttribute(quads.positions, 3));
      geo.setAttribute('normal', new THREE.Float32BufferAttribute(quads.normals, 3));
      geo.setAttribute('uv', new THREE.Float32BufferAttribute(quads.uvs, 2));
      geo.setIndex(quads.indices);
      const mesh = new THREE.Mesh(geo, material);
      mesh.receiveShadow = true;
      this.floorMesh = mesh;
      this.root.add(mesh);
    } else {
      this.floorMesh = null;
    }

    const grid = new THREE.GridHelper(Math.max(plane.width, plane.depth), Math.max(state.map.size[0], state.map.size[1]), GRID_COLOR, GRID_COLOR);
    grid.position.set(plane.x, 0.01, plane.z);
    grid.material.transparent = true;
    grid.material.opacity = 0.22;
    this.root.add(grid);
    this.setIllumination(state.map?.illumination);
  }

  setIllumination(value) {
    const illum = clampIllumination(value, this._illumination ?? 0);
    this._illumination = illum;
    if (this.state?.map) this.state.map.illumination = illum;
    const lights = ambientLightIntensities(illum);
    this.hemi.intensity = lights.hemi;
    this.dir.intensity = lights.dir;
    this.ambientFill.intensity = lights.ambient;
    this.renderer.toneMappingExposure = 0.72 + illum * 0.55;
    const fog = this.scene.fog;
    if (fog && typeof fog.near === 'number') {
      const range = distanceFogRange(this.state?.map?.size, illum);
      fog.near = range.near;
      fog.far = range.far;
    }
    this.fx?.syncDistanceFog?.(illum);
    return illum;
  }

  async _buildWater(state) {
    const mats = await this._wallMaterials(state);
    this.waterSim = addWaterTerrain(this.root, state, mats, this.cellSize);
  }

  _loadTexture(url) {
    return new Promise((resolve) => {
      const loader = new THREE.TextureLoader();
      loader.setCrossOrigin('use-credentials');
      loader.load(
        url,
        (tex) => resolve(tex),
        undefined,
        () => resolve(null),
      );
    });
  }

  async _wallMaterials(state = this.state) {
    const spec = wallSpecFromState(state);
    const key = `${spec.style}|${spec.albedo || ''}`;
    if (this._vttMaterials && this._vttMaterialsKey === key) return this._vttMaterials;
    disposeVttMaterials(this._vttMaterials);
    const loader = new THREE.TextureLoader();
    loader.setCrossOrigin('use-credentials');
    const anisotropy = this.renderer.capabilities.getMaxAnisotropy();
    this._vttMaterials = await loadVttMaterials(loader, {
      anisotropy,
      style: spec.style,
      albedo: spec.albedo,
    });
    this._vttMaterialsKey = key;
    return this._vttMaterials;
  }

  _addInstancedBoxes(boxes, material, { lockHeight = false } = {}) {
    for (const group of groupBoxesBySize(boxes).values()) {
      const sample = group[0];
      const geo = texturedBoxGeometry(sample.width, sample.height, sample.depth, 1, { lockHeight });
      const mesh = new THREE.InstancedMesh(geo, material, group.length);
      const dummy = new THREE.Object3D();
      group.forEach((box, index) => {
        dummy.position.set(box.x, box.y, box.z);
        dummy.updateMatrix();
        mesh.setMatrixAt(index, dummy.matrix);
      });
      mesh.instanceMatrix.needsUpdate = true;
      this.root.add(mesh);
    }
  }

  _addDoorLeaf(group, leaf, mats) {
    const hinge = new THREE.Group();
    hinge.position.set(leaf.hinge.x, leaf.hinge.y, leaf.hinge.z);
    hinge.rotation.y = leaf.yaw;

    const panel = leaf.panel;
    const panelGeo = mats.style === 'office'
      ? texturedBoxGeometry(panel.width, panel.height, panel.depth, 1, { lockHeight: true })
      : new THREE.BoxGeometry(panel.width, panel.height, panel.depth);
    const mesh = new THREE.Mesh(
      panelGeo,
      doorPanelMaterials(mats.style === 'office' ? (mats.frame || mats.wood) : mats.wood, mats.doorFace),
    );
    mesh.position.set(panel.localX, panel.localY, panel.localZ);
    hinge.add(mesh);

    const hardware = mats.handle || mats.iron;
    if (mats.style !== 'office') {
      const strapGeo = new THREE.BoxGeometry(0.09, 0.055, 0.018);
      for (const y of [panel.height * 0.22, panel.height * 0.72]) {
        const strap = new THREE.Mesh(strapGeo, hardware);
        strap.position.set(0.05, y, panel.depth / 2 + 0.01);
        hinge.add(strap);
      }
    }

    const handle = new THREE.Mesh(new THREE.TorusGeometry(0.04, 0.01, 8, 14), hardware);
    handle.rotation.y = Math.PI / 2;
    handle.position.set(leaf.handle.localX, leaf.handle.localY, leaf.handle.localZ);
    hinge.add(handle);
    const knob = new THREE.Mesh(new THREE.SphereGeometry(0.018, 10, 10), hardware);
    knob.position.set(leaf.handle.localX, leaf.handle.localY, leaf.handle.localZ - 0.01);
    hinge.add(knob);
    group.add(hinge);
  }

  _addDoor(assembly, mats) {
    const frameMat = mats.frame || mats.stone;
    const group = new THREE.Group();
    for (const box of assembly.frame) {
      const mesh = new THREE.Mesh(
        texturedBoxGeometry(box.width, box.height, box.depth, 1.4),
        frameMat,
      );
      mesh.position.set(box.x, box.y, box.z);
      group.add(mesh);
    }
    const leaves = assembly.leaves?.length ? assembly.leaves : [assembly];
    for (const leaf of leaves) {
      this._addDoorLeaf(group, leaf, mats);
    }
    this.root.add(group);
  }

  async _buildWalls(state) {
    const mats = await this._wallMaterials(state);
    const wallMat = mats.wall || mats.stone;
    const lockHeight = Boolean(mats.wallLockHeight);
    if (mats.style === 'cave') {
      addCaveWalls(this.root, state, wallMat, this.cellSize);
    } else {
      this._addInstancedBoxes(solidWallBoxes(state.geometry?.solid_walls || []), wallMat, { lockHeight });
      this._addInstancedBoxes(edgeWallBoxes(state.geometry?.edge_walls || []), wallMat, { lockHeight });
    }
    const doors = state.geometry?.doors || [];
    for (const door of doors) {
      if (door.model?.src) {
        await addCampaignModel(this.root, door, this.cellSize, mats);
      }
    }
    for (const door of doorAssemblies(doors, { edgeWalls: state.geometry?.edge_walls || [] })) {
      this._addDoor(door, mats);
    }
    for (const pane of windowAssemblies(doors)) {
      addWindow(this.root, pane, mats);
    }
    for (const slab of secretDoorAssemblies(doors)) {
      addSecretDoor(this.root, slab, mats);
    }
    for (const grate of portcullisAssemblies(doors)) {
      addPortcullis(this.root, grate, mats);
    }
  }

  _buildFog(state) {
    const water = waterCellSet(state.geometry?.water || []);
    const size = state.map?.size || [0, 0];
    const fogCells = foggedCells(state.tiles || []).filter(
      (cell) => !water.has(`${cell.x},${cell.y}`)
    );
    const lightCells = (state.tiles || [])
      .filter(
        (tile) =>
          tile.line_of_sight !== false &&
          Number(tile.light) > 0.55 &&
          !water.has(`${tile.x},${tile.y}`)
      )
      .map((tile) => ({ x: tile.x, y: tile.y }));
    // One floor-wide overlay per layer with an upsampled mask texture: the
    // linear texture filter feathers the border between shadowed and lit
    // cells, so dim areas read as soft pools of shade instead of sharp,
    // tile-aligned squares. Interiors of adjacent shadowed cells stay uniform.
    this._addSoftCellOverlay(fogCells, size, FOG_COLOR, 0.72, 0.04, 4);
    this._addSoftCellOverlay(lightCells, size, 0xffe08a, 0.07, 0.03, 4);
  }

  _addSoftCellOverlay(cells, size, color, opacity, height, samplesPerCell) {
    const gw = Number(size?.[0]) || 0;
    const gh = Number(size?.[1]) || 0;
    if (!cells.length || gw <= 0 || gh <= 0) return;
    const mask = cellMaskBytes(cells, [gw, gh], samplesPerCell);
    const rgba = new Uint8Array(mask.width * mask.height * 4);
    for (let i = 0; i < mask.data.length; i += 1) {
      const v = mask.data[i];
      const o = i * 4;
      rgba[o] = v;
      rgba[o + 1] = v;
      rgba[o + 2] = v;
      rgba[o + 3] = 255;
    }
    const tex = new THREE.DataTexture(rgba, mask.width, mask.height, THREE.RGBAFormat);
    tex.flipY = false;
    tex.magFilter = THREE.LinearFilter;
    tex.minFilter = THREE.LinearFilter;
    tex.wrapS = THREE.ClampToEdgeWrapping;
    tex.wrapT = THREE.ClampToEdgeWrapping;
    tex.needsUpdate = true;
    // Floor-sized quad with uv = world (x, z) / map size — same sampling
    // convention as the volumetric fog mask (texture rows are grid rows).
    const plane = floorPlane([gw, gh], this.cellSize);
    const geo = new THREE.PlaneGeometry(plane.width, plane.depth);
    geo.rotateX(-Math.PI / 2);
    const uv = geo.attributes.uv;
    for (let i = 0; i < uv.count; i += 1) uv.setY(i, 1 - uv.getY(i));
    const mesh = new THREE.Mesh(
      geo,
      new THREE.MeshBasicMaterial({
        color,
        transparent: true,
        opacity,
        alphaMap: tex,
        depthWrite: false,
      })
    );
    mesh.position.set(plane.x, height, plane.z);
    this.root.add(mesh);
  }

  _onDecorLight(light) {
    if (this.decorLights.length >= MAX_DECOR_LIGHTS) return false;
    this.decorLights.push(light);
    return true;
  }

  async _buildTokens(state) {
    const mats = await this._wallMaterials(state);
    const seen = new Set();
    for (const entity of state.entities || []) {
      seen.add(entity.uid);
      this._addToken(entity, mats);
    }
    for (const uid of this._proneState.keys()) {
      if (!seen.has(uid)) this._proneState.delete(uid);
    }
  }

  _addToken(entity, mats) {
    const size = Math.max(1, Number(entity.size) || 1);
    const c = cellCenter(entity.x, entity.y, this.cellSize);
    const group = new THREE.Group();
    const radius = 0.38 * size;
    const flying = isFlyingPose(entity);
    const prone = isPronePose(entity);
    const base = new THREE.Mesh(
      new THREE.CylinderGeometry(radius, radius, 0.08, 20),
      new THREE.MeshStandardMaterial({
        color: entity.dead ? 0x4a3030 : entity.hiding ? 0x445566 : 0xd6c49a,
        roughness: 0.6,
      }),
    );
    base.position.y = 0.04;
    base.userData.tokenBase = true;
    if (flying) {
      const shadow = new THREE.Mesh(
        new THREE.CircleGeometry(radius * 0.85, 20),
        new THREE.MeshBasicMaterial({
          color: 0x000000,
          transparent: true,
          opacity: 0.32,
          depthWrite: false,
        }),
      );
      shadow.rotation.x = -Math.PI / 2;
      shadow.position.y = 0.015;
      shadow.userData.groundShadow = true;
      group.add(shadow);
    } else {
      group.add(base);
    }

    const portrait = this._makeTokenPortrait(entity, size, prone);
    group.add(portrait);
    const wasProne = this._proneState.get(entity.uid);
    this._proneState.set(entity.uid, prone);
    this._attachStatusAuras(group, portrait, entity, size);
    this._attachCreatureAuras(group, entity);
    addTokenLight(group, entity, mats, this.cellSize, {
      feetPerGrid: this.state?.map?.feet_per_grid,
      onLight: (light) => this._onDecorLight(light),
    });
    const stairY = this._stairY(entity.x, entity.y);
    const baseY = tokenBaseY(entity, { stairY });
    group.position.set(c.x + (size - 1) * 0.5, baseY, c.z + (size - 1) * 0.5);
    group.userData.entity = entity;
    group.userData.flying = flying;
    group.userData.baseY = baseY;
    group.userData.stairY = stairY;
    group.userData.flyPhase = (Number(entity.x) + Number(entity.y)) * 0.7;
    this.root.add(group);
    this.tokenMeshes.set(entity.uid, group);

    const url = tokenAssetUrl(entity.token);
    if (url) {
      this._loadTexture(url).then((tex) => {
        if (!tex) return;
        tex.colorSpace = THREE.SRGBColorSpace;
        const mat = portrait.material;
        if (mat) {
          mat.map = tex;
          mat.needsUpdate = true;
        }
      });
    }

    if (entity.model && entity.model.src) {
      this._attachTokenModel(group, entity, portrait, wasProne);
    }
  }

  /**
   * Replace the billboard portrait with the campaign 3D figure when the DM
   * mapped this entity to a model (``entity.model`` from /map_state). On any
   * load failure the 2D token stays.
   */
  _attachTokenModel(group, entity, portrait, wasProne) {
    const uid = entity.uid;
    buildFigure(entity.model, {
      feetPerGrid: this.state?.map?.feet_per_grid,
      size: Math.max(1, Number(entity.size) || 1),
    }).then((figure) => {
      if (!figure) return;
      if (this.tokenMeshes.get(uid) !== group) return; // token rebuilt meanwhile
      group.add(figure);
      group.userData.figure = figure;
      // facing: rotate_y from the spec is the figure's "yaw 0" pose; the
      // token's current facing (kept across refreshes) adds to it
      figure.userData.specYaw = figure.rotation.y;
      const faceYaw = this.tokenYaw.get(uid);
      if (Number.isFinite(faceYaw)) figure.rotation.y += faceYaw;
      if (portrait) portrait.visible = false;
      group.traverse((node) => {
        if (node.userData?.tokenBase) node.visible = false;
      });
      if (isPronePose(entity)) this._toppleFigure(uid, group, figure, wasProne);
    });
  }

  /**
   * Lay a 3D figure prone: a rigid-body fall over its own convex hull, so
   * the resting pose follows the miniature's geometry. If the token was
   * standing earlier in this session, the fall is animated live; otherwise
   * (page load of an already-prone entity) it is simulated to rest and the
   * settled pose is applied instantly. Settled poses are cached per uid so
   * map refreshes keep the figure at rest.
   */
  _toppleFigure(uid, group, figure, wasProne) {
    if (figure.userData.toppled) return;
    for (const tp of this._toppling) {
      if (tp.uid !== uid) continue;
      tp.figure = figure; // token rebuilt mid-fall: keep animating on the new copy
      applyTopplePose(figure, tp.sim.pose());
      figure.userData.toppled = false; // still falling
      return;
    }
    const cached = this._topplePoses.get(uid);
    if (cached) {
      applyTopplePose(figure, cached);
      return;
    }
    const fallYaw = this.tokenYaw.get(uid) || 0;
    const points = gatherHullPoints(group, figure);
    const sim = createToppler(points, { fallYaw, baseYaw: figure.rotation.y });
    if (!sim) {
      const pose = fallbackTopplePose(fallYaw);
      this._topplePoses.set(uid, pose);
      applyTopplePose(figure, pose);
      return;
    }
    if (wasProne === false) {
      this._toppling.push({
        uid, figure, sim, t: 0, calm: 0, maxT: TOPPLE_MAX_SECONDS,
      });
    } else {
      const pose = runTopple(sim);
      this._topplePoses.set(uid, pose);
      applyTopplePose(figure, pose);
    }
  }

  _makeTokenPortrait(entity, size, prone) {
    if (prone) {
      const plane = new THREE.Mesh(
        new THREE.PlaneGeometry(size * 0.92, size * 0.58),
        new THREE.MeshBasicMaterial({
          color: entity.dead ? 0x888888 : 0xffffff,
          transparent: true,
          side: THREE.DoubleSide,
          depthWrite: false,
        }),
      );
      plane.rotation.x = -Math.PI / 2;
      plane.rotation.z = Math.PI / 2;
      plane.position.y = 0.04;
      plane.userData.portrait = true;
      return plane;
    }
    const spriteMat = new THREE.SpriteMaterial({
      color: 0xffffff,
      depthTest: true,
    });
    const sprite = new THREE.Sprite(spriteMat);
    sprite.scale.set(size * 0.9, size * 0.9, 1);
    sprite.position.y = 0.55 * size;
    sprite.userData.portrait = true;
    return sprite;
  }

  _attachStatusAuras(group, portrait, entity, size) {
    const auras = aurasForEffects(entity.effects);
    auras.forEach((aura, index) => {
      const inner = (0.38 + index * 0.05) * size;
      const ring = new THREE.Mesh(
        new THREE.RingGeometry(inner, inner + 0.1 * size, 28),
        new THREE.MeshBasicMaterial({
          color: aura.color,
          transparent: true,
          opacity: aura.opacity,
          side: THREE.DoubleSide,
          depthWrite: false,
        }),
      );
      ring.rotation.x = -Math.PI / 2;
      ring.position.y = 0.09 + index * 0.02;
      group.add(ring);
      if (aura.fadeSprite && portrait?.material) {
        portrait.material.transparent = true;
        portrait.material.opacity = aura.fadeSprite;
      }
    });
  }

  /**
   * Ground footprint rings for creature-centered emanations delivered as
   * ``entity.creature_auras`` (Investiture of Flame, Spirit Guardians,
   * paladin auras). Sized to the aura's real radius, not the token size.
   */
  _attachCreatureAuras(group, entity) {
    const rings = creatureAuraRings(entity, {
      feetPerGrid: this.state?.map?.feet_per_grid,
    });
    rings.forEach((aura, index) => {
      const outer = Math.max(0.3, aura.radiusCells * this.cellSize + 0.08);
      const inner = Math.max(0.2, outer - 0.16);
      const ring = new THREE.Mesh(
        new THREE.RingGeometry(inner, outer, 40),
        new THREE.MeshBasicMaterial({
          color: aura.color,
          transparent: true,
          opacity: aura.opacity,
          side: THREE.DoubleSide,
          depthWrite: false,
        }),
      );
      ring.rotation.x = -Math.PI / 2;
      ring.position.y = 0.05 + index * 0.015;
      ring.userData.creatureAura = aura.name;
      group.add(ring);
      const disc = new THREE.Mesh(
        new THREE.CircleGeometry(inner, 40),
        new THREE.MeshBasicMaterial({
          color: aura.color,
          transparent: true,
          opacity: aura.opacity * 0.22,
          depthWrite: false,
        }),
      );
      disc.rotation.x = -Math.PI / 2;
      disc.position.y = 0.045 + index * 0.015;
      disc.userData.creatureAura = aura.name;
      group.add(disc);
    });
  }

  async _buildObjectMarkers(state) {
    const mats = await this._wallMaterials(state);
    const opts = {
      geometry: state.geometry,
      feetPerGrid: state.map?.feet_per_grid,
      onLight: (light) => this._onDecorLight(light),
    };
    for (const obj of state.objects || []) {
      if (obj.model?.src) {
        await addCampaignModel(this.root, obj, this.cellSize, mats);
        continue;
      }
      addFixture(this.root, obj, mats, this.cellSize, opts);
    }
    for (const lamp of state.lights || []) {
      addFixture(this.root, { ...lamp, kind: lamp.kind || 'torch' }, mats, this.cellSize, opts);
    }
  }

  hasDecorLightAt(x, y) {
    const gx = Number(x);
    const gy = Number(y);
    return this.decorLights.some((light) => (
      Number(light.userData.gridX) === gx && Number(light.userData.gridY) === gy
    ));
  }

  setPath(path, terrainInfo = null) {
    this._clearOverlays();
    if (!path || path.length < 2) return;
    const pathY = 0.06;
    const points = path.map(([gx, gy]) => {
      const c = cellCenter(gx, gy, this.cellSize);
      return new THREE.Vector3(c.x, pathY, c.z);
    });
    // Flat 2D-style green arrow (see flowArrow.js): flowing dash on normal
    // steps, fine dots when the segment ENTERS difficult terrain — same
    // convention as the 2D VTT's solid-vs-dashed path preview. ``terrainInfo``
    // comes straight from /path ([{x, y, difficult}]).
    let styles = null;
    if (Array.isArray(terrainInfo) && terrainInfo.length) {
      const lookup = new Map();
      for (const t of terrainInfo) {
        if (t && t.difficult) lookup.set(`${t.x},${t.y}`, true);
      }
      styles = [];
      for (let i = 0; i + 1 < path.length; i += 1) {
        styles.push(lookup.has(`${path[i + 1][0]},${path[i + 1][1]}`));
      }
    }
    const arrow = buildFlowArrow(points, {
      width: this.cellSize * 0.16,
      dashPeriod: this.cellSize * 0.6,
      styles,
    });
    this.overlayRoot.add(arrow.group);
    this._pathFlow = arrow.flowMaterial;
    this._pathFlowPeriod = arrow.dashPeriod;
    const last = path[path.length - 1];
    this._addCellHighlight(last[0], last[1], SELECT_COLOR, 0.35);
  }

  setHighlights(cells, color = AOE_COLOR) {
    this._clearOverlays();
    this._rangeLabel = null;
    for (const cell of cells || []) {
      const x = Array.isArray(cell) ? cell[0] : cell.x;
      const y = Array.isArray(cell) ? cell[1] : cell.y;
      this._addCellHighlight(x, y, color, 0.32);
    }
  }

  setTargetLine(from, to, { valid = true, rangeLabel = null, melee = false } = {}) {
    this._clearOverlays();
    if (!from || !to) {
      this._rangeLabel = null;
      return;
    }
    const a = cellCenter(from.x, from.y, this.cellSize);
    const b = cellCenter(to.x, to.y, this.cellSize);
    const y = 0.07;
    const start = new THREE.Vector3(a.x, y, a.z);
    const end = new THREE.Vector3(b.x, y, b.z);
    // Flat thick attack arrow (see flowArrow.js): a ranged attack is a
    // flowing red ribbon with an arrow head on the target cell; a melee
    // attack is a dotted red line with an impact ring around the target.
    const arrow = buildAttackArrow([start, end], {
      width: this.cellSize * 0.22,
      dashPeriod: this.cellSize * 0.55,
      melee,
      valid,
    });
    this.overlayRoot.add(arrow.group);
    this._pathFlow = arrow.flowMaterial;
    this._pathFlowPeriod = arrow.dashPeriod;
    this._addCellHighlight(to.x, to.y, valid ? SELECT_COLOR : 0x888888, 0.32);
    this._rangeLabel = rangeLabel ? { text: rangeLabel, pos: end.clone() } : null;
  }

  /**
   * Selection indicator: a flat green ring hugging the floor around the
   * token's base, plus a grab knob on the ring used to rotate the figure
   * with the mouse (cosmetic facing only, synced to other players).
   */
  setSelected(uid) {
    if (this._selectedUid === uid && this._selectionGroup?.parent) {
      this._repositionSelection();
      return;
    }
    this.clearSelection();
    if (!uid) return;
    const group = this.tokenMeshes.get(uid);
    if (!group) return;
    const ring = new THREE.Mesh(
      flatRingGeometry(this.cellSize * 0.5, this.cellSize * 0.62, 48),
      new THREE.MeshBasicMaterial({
        color: 0x3ddc84, transparent: true, opacity: 0.9, depthWrite: false, side: THREE.DoubleSide,
      }),
    );
    ring.position.set(group.position.x, 0.05, group.position.z);
    ring.userData.selectionRing = uid;
    const knob = new THREE.Mesh(
      flatRingGeometry(this.cellSize * 0.001, this.cellSize * 0.2, 20),
      new THREE.MeshBasicMaterial({
        color: 0x3ddc84, transparent: true, opacity: 0.95, depthWrite: false, side: THREE.DoubleSide,
      }),
    );
    const yaw = this.tokenYaw?.get(uid) || 0;
    knob.position.set(
      ring.position.x + Math.sin(yaw) * this.cellSize * 0.56,
      0.07,
      ring.position.z + Math.cos(yaw) * this.cellSize * 0.56,
    );
    knob.userData.rotateHandle = uid;
    this._selectionGroup = new THREE.Group();
    this._selectionGroup.add(ring, knob);
    this.scene.add(this._selectionGroup);
    this._selectedUid = uid;
    this._updateRotationGhost();
  }

  clearSelection() {
    if (this._selectionGroup) {
      disposeObject(this._selectionGroup);
      this.scene.remove(this._selectionGroup);
      this._selectionGroup = null;
    }
    if (this._rotationGhost) {
      disposeObject(this._rotationGhost);
      this.scene.remove(this._rotationGhost);
      this._rotationGhost = null;
    }
    this._selectedUid = null;
  }

  /** Follow a token that moved or turned while selected. */
  _repositionSelection() {
    const group = this.tokenMeshes.get(this._selectedUid);
    if (!group || !this._selectionGroup) return;
    const [ring, knob] = this._selectionGroup.children;
    ring.position.set(group.position.x, 0.05, group.position.z);
    const yaw = this.tokenYaw.get(this._selectedUid) || 0;
    knob.position.set(
      group.position.x + Math.sin(yaw) * this.cellSize * 0.56,
      0.07,
      group.position.z + Math.cos(yaw) * this.cellSize * 0.56,
    );
    this._updateRotationGhost();
  }

  _rotationSpecYaw(uid) {
    const figure = this.tokenMeshes.get(uid)?.userData.figure;
    const spec = figure?.userData?.specYaw;
    return typeof spec === 'number' ? spec : 0;
  }

  /** Live cosmetic rotation: re-faces the figure and moves the knob. */
  setTokenYaw(uid, yaw) {
    if (!this.tokenYaw) this.tokenYaw = new Map();
    this.tokenYaw.set(uid, yaw);
    const group = this.tokenMeshes.get(uid);
    if (group && group.userData.figure && !this._topplePoses?.has?.(uid)) {
      group.userData.figure.rotation.y = this._rotationSpecYaw(uid) + yaw;
    }
    if (this._selectedUid === uid) this.setSelected(uid);
    this._updateRotationGhost();
  }

  /** Floor-plane event -> {x, z} world point (null off the map floor). */
  floorPointFromEvent(event) {
    const rect = this.canvas.getBoundingClientRect();
    this.pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    this.pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
    this.raycaster.setFromCamera(this.pointer, this.camera);
    if (!this.floorMesh) return null;
    const hits = this.raycaster.intersectObject(this.floorMesh, false);
    if (!hits.length) return null;
    return hits[0].point;
  }

  /**
   * Floor-plane event -> yaw (radians) about the selected token, or null
   * when the ray misses the floor. Feeds the rotate-drag gesture.
   */
  rotationFromEvent(event) {
    if (!this._selectedUid) return null;
    const group = this.tokenMeshes.get(this._selectedUid);
    if (!group) return null;
    const p = this.floorPointFromEvent(event);
    if (!p) return null;
    const dx = p.x - group.position.x;
    const dz = p.z - group.position.z;
    if (Math.hypot(dx, dz) < 1e-4) return null;
    return Math.atan2(dx, dz);
  }

  /** True when the event ray hits the selected token's rotation knob. */
  rotateHandleHit(event) {
    if (!this._selectedUid) return false;
    const rect = this.canvas.getBoundingClientRect();
    this.pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    this.pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
    this.raycaster.setFromCamera(this.pointer, this.camera);
    const hits = this.raycaster.intersectObject(this._selectionGroup, true);
    return hits.some((h) => h.object.userData.rotateHandle === this._selectedUid);
  }

  /** While dragging, a thin red line shows where the base is pointing. */
  _updateRotationGhost() {
    if (this._rotationGhost) {
      disposeObject(this._rotationGhost);
      this.scene.remove(this._rotationGhost);
      this._rotationGhost = null;
    }
    if (!this._dragRotationUid) return;
    const group = this.tokenMeshes.get(this._dragRotationUid);
    if (!group) return;
    const yaw = this.tokenYaw?.get(this._dragRotationUid) || 0;
    const y = 0.08;
    const reach = this.cellSize * 0.9;
    const geo = new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(group.position.x, y, group.position.z),
      new THREE.Vector3(group.position.x + Math.sin(yaw) * reach, y, group.position.z + Math.cos(yaw) * reach),
    ]);
    this._rotationGhost = new THREE.Line(
      geo,
      new THREE.LineBasicMaterial({ color: 0xff6b6b }),
    );
    this.scene.add(this._rotationGhost);
  }

  _addCellHighlight(x, y, color, opacity) {
    const geo = new THREE.PlaneGeometry(this.cellSize * 0.92, this.cellSize * 0.92);
    geo.rotateX(-Math.PI / 2);
    const mesh = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({
      color,
      transparent: true,
      opacity,
      depthWrite: false,
    }));
    const c = cellCenter(x, y, this.cellSize);
    mesh.position.set(c.x, 0.06, c.z);
    this.overlayRoot.add(mesh);
  }

  _stairY(x, y) {
    return stairSurfaceY(x, y, this.state?.objects || [], {
      feetPerGrid: this.state?.map?.feet_per_grid,
    });
  }

  playMove(uid, path, durationPerStep = 0.18) {
    const mesh = this.tokenMeshes.get(uid);
    if (!mesh || !path || path.length < 2) return Promise.resolve();
    const steps = path.slice(1);
    return new Promise((resolve) => {
      const run = (index) => {
        if (index >= steps.length) {
          resolve();
          return;
        }
        const [x, y] = steps[index];
        const c = cellCenter(x, y, this.cellSize);
        const stairY = this._stairY(x, y);
        const entity = mesh.userData.entity || {};
        const baseY = tokenBaseY(entity, { stairY });
        mesh.userData.stairY = stairY;
        mesh.userData.baseY = baseY;
        const from = mesh.position.clone();
        const to = new THREE.Vector3(c.x, baseY, c.z);
        // face the direction of travel (figures only; billboards face the camera)
        const faceYaw = yawToDirection(to.x - from.x, to.z - from.z);
        let yaw = null;
        if (faceYaw != null && mesh.userData.figure && !mesh.userData.figure.userData.toppled) {
          yaw = { from: mesh.userData.faceYaw || 0, to: faceYaw };
          mesh.userData.faceYaw = faceYaw;
          this.tokenYaw.set(uid, faceYaw);
        }
        this.tweens.push({
          mesh,
          from,
          to,
          t: 0,
          dur: durationPerStep,
          yaw,
          done: () => run(index + 1),
        });
      };
      run(0);
    });
  }

  playAttack(fromPos, toPos, duration = 0.28) {
    if (!fromPos || !toPos) return;
    const from = this.fx.worldFromGrid(fromPos[0], fromPos[1]);
    const to = this.fx.worldFromGrid(toPos[0], toPos[1]);
    this.fx.projectile({ from, to, duration, arc: 0.25 });
  }

  playAoE(cells, duration = 0.55) {
    this.fx.cellGlow(cells, AOE_COLOR, duration, 0.45);
  }

  projectWorld(world, lift = 0) {
    const w = this.canvas.clientWidth || window.innerWidth;
    const h = this.canvas.clientHeight || window.innerHeight;
    const v = world.clone();
    v.y += lift;
    v.project(this.camera);
    return {
      x: (v.x * 0.5 + 0.5) * w,
      y: (-v.y * 0.5 + 0.5) * h,
      behind: v.z > 1,
    };
  }

  projectCell(gx, gy, lift = 0.55) {
    const c = cellCenter(gx, gy, this.cellSize);
    return this.projectWorld(new THREE.Vector3(c.x, 0, c.z), lift);
  }

  _syncHpChips() {
    if (!this.hudLayer) return;
    const keep = new Set();
    for (const [uid, mesh] of this.tokenMeshes) {
      const entity = mesh.userData.entity || {};
      keep.add(uid);
      let el = this.hpChips.get(uid);
      if (!el) {
        el = document.createElement('div');
        el.className = 'token-hp-chip';
        this.hudLayer.appendChild(el);
        this.hpChips.set(uid, el);
      }
      const hp = entity.hp;
      const max = entity.max_hp;
      el.textContent = (hp == null || max == null) ? (entity.name || '') : `${hp}/${max}`;
      el.classList.toggle('is-dead', Boolean(entity.dead));
    }
    for (const [uid, el] of this.hpChips) {
      if (!keep.has(uid)) {
        el.remove();
        this.hpChips.delete(uid);
      }
    }
  }

  _projectHud() {
    if (!this.hudLayer) return;
    const w = this.canvas.clientWidth || window.innerWidth;
    const h = this.canvas.clientHeight || window.innerHeight;
    const v = new THREE.Vector3();
    for (const [uid, mesh] of this.tokenMeshes) {
      const el = this.hpChips.get(uid);
      if (!el) continue;
      v.copy(mesh.position);
      v.y += 1.05;
      v.project(this.camera);
      const x = (v.x * 0.5 + 0.5) * w;
      const y = (-v.y * 0.5 + 0.5) * h;
      const behind = v.z > 1;
      el.style.display = behind ? 'none' : 'block';
      el.style.left = `${x}px`;
      el.style.top = `${y}px`;
    }
    if (this._rangeLabel && this.hudLayer) {
      if (!this.rangeChip) {
        this.rangeChip = document.createElement('div');
        this.rangeChip.className = 'token-hp-chip target-range-chip';
        this.hudLayer.appendChild(this.rangeChip);
      }
      this.rangeChip.textContent = this._rangeLabel.text;
      const p = this.projectWorld(this._rangeLabel.pos, 0.12);
      this.rangeChip.style.display = p.behind ? 'none' : 'block';
      this.rangeChip.style.left = `${p.x}px`;
      this.rangeChip.style.top = `${p.y}px`;
    } else if (this.rangeChip) {
      this.rangeChip.style.display = 'none';
    }
    for (const fn of this.hudHooks) {
      try { fn(); } catch { /* ignore HUD errors */ }
    }
  }

  _loop() {
    const dt = Math.min(0.05, this.clock.getDelta());
    this.controls.update();
    const remaining = [];
    for (const tw of this.tweens) {
      tw.t += dt;
      const k = Math.min(1, tw.t / tw.dur);
      tw.mesh.position.lerpVectors(tw.from, tw.to, k);
      if (tw.yaw) {
        const figure = tw.mesh.userData.figure;
        if (figure && !figure.userData.toppled) {
          const turn = shortestAngle(tw.yaw.from, tw.yaw.to);
          figure.rotation.y = (figure.userData.specYaw || 0) + tw.yaw.from + turn * k;
        }
      }
      if (k >= 1) {
        tw.done?.();
      } else {
        remaining.push(tw);
      }
    }
    this.tweens = remaining;
    // movement path arrow: slide the dashes toward the destination
    if (this._pathFlow?.map) {
      const repeatsPerSec = (1.4 * this.cellSize) / (this._pathFlowPeriod || this.cellSize);
      this._pathFlow.map.offset.x -= repeatsPerSec * dt;
    }
    // live physics topples (a figure that just went prone)
    const keepTopple = [];
    for (const tp of this._toppling) {
      tp.sim.step(dt);
      tp.t += dt;
      tp.sim.applyTo(tp.figure);
      tp.calm = isAtRest(tp.sim.body) ? tp.calm + dt : 0;
      if (tp.calm >= 0.5 || tp.t >= tp.maxT) {
        tp.sim.body.sleep();
        this._topplePoses.set(tp.uid, tp.sim.pose());
        tp.figure.userData.toppled = true;
      } else {
        keepTopple.push(tp);
      }
    }
    this._toppling = keepTopple;
    const fxKeep = [];
    for (const fx of this.fxTtl) {
      fx.t -= dt;
      if (fx.t <= 0) {
        this.fxRoot.remove(fx.obj);
        disposeObject(fx.obj);
      } else {
        fxKeep.push(fx);
      }
    }
    this.fxTtl = fxKeep;
    this.fx.tick(dt);
    this.waterSim?.tick(dt, this.fx.hasMapEffect('rain') || this.fx.hasMapEffect('water'));
    const t = this.clock.elapsedTime;
    const tweening = new Set(this.tweens.map((tw) => tw.mesh));
    for (const mesh of this.tokenMeshes.values()) {
      if (!mesh.userData.flying || tweening.has(mesh)) continue;
      const phase = mesh.userData.flyPhase || 0;
      const baseY = mesh.userData.baseY || 0;
      mesh.position.y = baseY + Math.sin(t * 2.4 + phase) * FLY_BOB;
      mesh.traverse((child) => {
        if (child.userData.groundShadow) {
          const stairY = Number(mesh.userData.stairY) || 0;
          child.position.y = stairY - mesh.position.y + 0.015;
        }
      });
    }
    for (const light of this.decorLights) {
      const base = light.userData.baseIntensity || light.intensity;
      const phase = light.userData.flickerPhase || 0;
      light.intensity = base * (0.88 + 0.12 * Math.sin(t * 9 + phase));
    }
    if (!this.fx.compositeFog(this.renderer, this.scene, this.camera)) {
      this.renderer.render(this.scene, this.camera);
    }
    this._projectHud();
    requestAnimationFrame(this._loop);
  }
}

export { WALL_HEIGHT, DOOR_HEIGHT };
