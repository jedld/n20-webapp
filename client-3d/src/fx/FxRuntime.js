import * as THREE from 'three';
import { cellCenter, distanceFogRange, floorPlane } from '../geometry.js';
import { createWaterMaterial } from '../scene/water.js';
import { createMapFog } from './mapFog.js';

function disposeObject(obj) {
  obj.traverse((child) => {
    if (child.geometry) child.geometry.dispose();
    const mat = child.material;
    if (!mat) return;
    const mats = Array.isArray(mat) ? mat : [mat];
    for (const m of mats) {
      if (m.map) m.map.dispose();
      m.dispose();
    }
  });
}

function parseColor(value, fallback = 0xffdd57) {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.startsWith('#')) {
    const n = parseInt(value.slice(1), 16);
    return Number.isFinite(n) ? n : fallback;
  }
  return fallback;
}

function parseFirePoint(raw) {
  if (raw == null) return null;
  if (Array.isArray(raw) && raw.length >= 2) {
    return { x: Number(raw[0]), y: Number(raw[1]), intensity: 0.8, color: 0xff7a2b };
  }
  if (typeof raw !== 'object') return null;
  const pos = Array.isArray(raw.pos) ? raw.pos : [raw.x, raw.y];
  if (!Number.isFinite(Number(pos[0])) || !Number.isFinite(Number(pos[1]))) return null;
  return {
    x: Number(pos[0]),
    y: Number(pos[1]),
    intensity: Number(raw.intensity) > 0 ? Number(raw.intensity) : 0.8,
    color: parseColor(raw.color, 0xff7a2b),
    shape: raw.shape || 'campfire',
  };
}

export class FxRuntime {
  constructor(mapScene) {
    this.map = mapScene;
    this.transients = [];
    this.mapFx = new Map();
    this._pendingMapEffects = [];
    this._effectConfigKeys = new Map();
    this._mapStateEffectsKey = null;
    this._mapStateEffectNames = new Set();
    this.weatherRoot = new THREE.Group();
    this.map.scene.add(this.weatherRoot);
    this._baseFog = {
      color: mapScene.scene.fog?.color?.getHex?.() ?? 0x0b0d12,
      near: mapScene.scene.fog?.near ?? 28,
      far: mapScene.scene.fog?.far ?? 64,
    };
  }

  tick(dt) {
    const keep = [];
    for (const fx of this.transients) {
      fx.t += dt;
      const k = fx.ttl > 0 ? Math.min(1, fx.t / fx.ttl) : 1;
      fx.update?.(fx.obj, k, dt, fx);
      if (fx.ttl > 0 && fx.t >= fx.ttl) {
        this.map.fxRoot.remove(fx.obj);
        disposeObject(fx.obj);
      } else {
        keep.push(fx);
      }
    }
    this.transients = keep;
    for (const entry of this.mapFx.values()) {
      entry.update?.(dt);
    }
  }

  spawn(obj, { ttl = 0.5, update } = {}) {
    this.map.fxRoot.add(obj);
    this.transients.push({ obj, t: 0, ttl, update });
    return obj;
  }

  worldFromGrid(x, y, height = 0.7) {
    const c = cellCenter(x, y, this.map.cellSize);
    return new THREE.Vector3(c.x, height, c.z);
  }

  worldFromEntity(uid, height = 0.7) {
    const mesh = this.map.tokenMeshes.get(uid);
    if (!mesh) return null;
    return new THREE.Vector3(mesh.position.x, height, mesh.position.z);
  }

  resolveWorld(idOrPos, height = 0.7) {
    if (idOrPos == null) return null;
    if (typeof idOrPos === 'string') {
      return this.worldFromEntity(idOrPos, height) || null;
    }
    if (Array.isArray(idOrPos) && idOrPos.length >= 2 && typeof idOrPos[0] === 'number') {
      return this.worldFromGrid(idOrPos[0], idOrPos[1], height);
    }
    if (typeof idOrPos === 'object' && Number.isFinite(Number(idOrPos.x)) && Number.isFinite(Number(idOrPos.y))) {
      return this.worldFromGrid(idOrPos.x, idOrPos.y, height);
    }
    return null;
  }

  projectile({ from, to, color = 0xffdd57, duration = 0.4, arc = 0.4, miss = false, radius = 0.11 }) {
    if (!from || !to) return;
    const start = from.clone();
    const end = to.clone();
    if (miss) {
      end.x += 0.45;
      end.y += 0.25;
    }
    const mat = new THREE.MeshBasicMaterial({
      color,
      transparent: true,
      opacity: 0.95,
      depthWrite: false,
    });
    const ball = new THREE.Mesh(new THREE.SphereGeometry(radius, 12, 12), mat);
    ball.position.copy(start);
    this.spawn(ball, {
      ttl: duration,
      update: (obj, k) => {
        obj.position.lerpVectors(start, end, k);
        obj.position.y += Math.sin(k * Math.PI) * arc;
        mat.opacity = 0.95 * (1 - k * 0.35);
      },
    });
  }

  beam({ from, to, color = 0x9ee8ff, duration = 0.45 }) {
    if (!from || !to) return;
    const dir = to.clone().sub(from);
    const len = Math.max(0.05, dir.length());
    const mid = from.clone().add(to).multiplyScalar(0.5);
    const axis = dir.length() > 0.001 ? dir.normalize() : new THREE.Vector3(0, 0, 1);
    const geo = new THREE.CylinderGeometry(0.045, 0.045, len, 8);
    const mat = new THREE.MeshBasicMaterial({
      color,
      transparent: true,
      opacity: 0.85,
      depthWrite: false,
    });
    const mesh = new THREE.Mesh(geo, mat);
    mesh.position.copy(mid);
    mesh.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), axis);
    this.spawn(mesh, {
      ttl: duration,
      update: (_obj, k) => {
        mat.opacity = 0.85 * (1 - k);
        mesh.scale.set(1, 1, 1 + k * 0.05);
      },
    });
  }

  burst({ at, color = 0xff4d00, duration = 0.55, radius = 0.7, fromAbove = false }) {
    if (!at) return;
    const origin = at.clone();
    if (fromAbove) origin.y += 1.4;
    const mat = new THREE.MeshBasicMaterial({
      color,
      transparent: true,
      opacity: 0.7,
      depthWrite: false,
    });
    const mesh = new THREE.Mesh(new THREE.SphereGeometry(radius * 0.25, 16, 16), mat);
    mesh.position.copy(origin);
    this.spawn(mesh, {
      ttl: duration,
      update: (obj, k) => {
        const s = 0.4 + k * 2.2;
        obj.scale.setScalar(s);
        if (fromAbove) obj.position.y = origin.y - k * 1.35;
        mat.opacity = 0.7 * (1 - k);
      },
    });
  }

  cone({ from, to, color = 0xff5a1f, duration = 0.55, radius = 1.4 }) {
    if (!from || !to) return;
    const dir = to.clone().sub(from);
    const len = Math.max(0.6, dir.length());
    const mid = from.clone().lerp(to, 0.55);
    const axis = dir.length() > 0.001 ? dir.normalize() : new THREE.Vector3(0, 0, 1);
    const geo = new THREE.ConeGeometry(radius, len, 18, 1, true);
    const mat = new THREE.MeshBasicMaterial({
      color,
      transparent: true,
      opacity: 0.38,
      side: THREE.DoubleSide,
      depthWrite: false,
    });
    const mesh = new THREE.Mesh(geo, mat);
    mesh.position.copy(mid);
    mesh.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), axis);
    this.spawn(mesh, {
      ttl: duration,
      update: (_obj, k) => {
        mat.opacity = 0.42 * (1 - k * 0.7);
        mesh.scale.set(0.7 + k * 0.5, 1, 0.7 + k * 0.5);
      },
    });
  }

  cellGlow(cells, color = 0xe63946, duration = 0.55, opacity = 0.42) {
    for (const cell of cells || []) {
      const x = Array.isArray(cell) ? cell[0] : cell.x;
      const y = Array.isArray(cell) ? cell[1] : cell.y;
      const c = cellCenter(x, y, this.map.cellSize);
      const geo = new THREE.CircleGeometry(this.map.cellSize * 0.42, 20);
      const mat = new THREE.MeshBasicMaterial({
        color,
        transparent: true,
        opacity,
        side: THREE.DoubleSide,
        depthWrite: false,
      });
      const mesh = new THREE.Mesh(geo, mat);
      mesh.rotation.x = -Math.PI / 2;
      mesh.position.set(c.x, 0.08, c.z);
      this.spawn(mesh, {
        ttl: duration,
        update: (_obj, k) => {
          mat.opacity = opacity * (1 - k * 0.55);
        },
      });
    }
  }

  particles({ at, color = 0x7cff9a, duration = 0.55, count = 18, mode = 'up' }) {
    if (!at) return;
    const positions = new Float32Array(count * 3);
    const velocities = [];
    for (let i = 0; i < count; i += 1) {
      positions[i * 3] = at.x + (Math.random() - 0.5) * 0.35;
      positions[i * 3 + 1] = at.y + (Math.random() - 0.2) * 0.2;
      positions[i * 3 + 2] = at.z + (Math.random() - 0.5) * 0.35;
      if (mode === 'burst') {
        velocities.push(new THREE.Vector3((Math.random() - 0.5) * 2.2, Math.random() * 1.6, (Math.random() - 0.5) * 2.2));
      } else if (mode === 'down') {
        velocities.push(new THREE.Vector3((Math.random() - 0.5) * 0.3, -1.8 - Math.random(), (Math.random() - 0.5) * 0.3));
      } else {
        velocities.push(new THREE.Vector3((Math.random() - 0.5) * 0.25, 1.1 + Math.random() * 0.8, (Math.random() - 0.5) * 0.25));
      }
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    const mat = new THREE.PointsMaterial({
      color,
      size: 0.08,
      transparent: true,
      opacity: 0.9,
      depthWrite: false,
    });
    const points = new THREE.Points(geo, mat);
    this.spawn(points, {
      ttl: duration,
      update: (_obj, k, dt) => {
        const arr = geo.attributes.position.array;
        for (let i = 0; i < count; i += 1) {
          arr[i * 3] += velocities[i].x * dt;
          arr[i * 3 + 1] += velocities[i].y * dt;
          arr[i * 3 + 2] += velocities[i].z * dt;
        }
        geo.attributes.position.needsUpdate = true;
        mat.opacity = 0.9 * (1 - k);
      },
    });
  }

  auraBurst({ at, color = 0xffd24a, duration = 0.6 }) {
    if (!at) return;
    const mat = new THREE.MeshBasicMaterial({
      color,
      transparent: true,
      opacity: 0.55,
      side: THREE.DoubleSide,
      depthWrite: false,
    });
    const mesh = new THREE.Mesh(new THREE.RingGeometry(0.2, 0.38, 28), mat);
    mesh.rotation.x = -Math.PI / 2;
    mesh.position.set(at.x, 0.12, at.z);
    this.spawn(mesh, {
      ttl: duration,
      update: (obj, k) => {
        const s = 1 + k * 2.4;
        obj.scale.set(s, s, s);
        mat.opacity = 0.55 * (1 - k);
      },
    });
  }

  melee({ from, to, color = 0xc9c4b8, duration = 0.26 }) {
    if (!from || !to) return;
    const mid = from.clone().lerp(to, 0.55);
    mid.y += 0.15;
    const points = [
      from.clone(),
      mid,
      to.clone(),
    ];
    const geom = new THREE.BufferGeometry().setFromPoints(points);
    const mat = new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.95 });
    const line = new THREE.Line(geom, mat);
    this.spawn(line, {
      ttl: duration,
      update: (_obj, k) => {
        mat.opacity = 1 - k;
      },
    });
    this.burst({ at: to, color, duration: duration * 0.85, radius: 0.28 });
  }

  look({ at, color = 0xf4d35e, duration = 0.55 }) {
    this.auraBurst({ at, color, duration });
    this.particles({ at, color, duration, count: 12, mode: 'up' });
  }

  teleport({ from, to, color = 0xb0e0ff, duration = 0.55 }) {
    if (from) {
      this.burst({ at: from, color, duration: duration * 0.7, radius: 0.55 });
      this.particles({ at: from, color, duration, count: 16, mode: 'burst' });
    }
    if (to) {
      this.burst({ at: to, color, duration, radius: 0.55 });
      this.particles({ at: to, color, duration, count: 16, mode: 'up' });
    }
  }

  hasMapEffect(name) {
    return this.mapFx.has(name);
  }

  applyEffectSet(data) {
    if (!data || !data.effect) return;
    const action = data.action || 'start';
    if (action === 'stop') {
      this.stopMapEffect(data.effect);
      return;
    }
    if (action === 'start') {
      const exclusive = data.exclusive === true;
      if (exclusive) this.stopAllMapEffectsExcept(data.effect);
      this.startMapEffect(data.effect, data.config || {});
      return;
    }
    if (action === 'update') {
      this.startMapEffect(data.effect, data.config || {});
    }
  }

  stopAllMapEffectsExcept(keepName) {
    for (const name of [...this.mapFx.keys()]) {
      if (name !== keepName) this.stopMapEffect(name);
    }
  }

  stopMapEffect(name) {
    this._pendingMapEffects = this._pendingMapEffects.filter((p) => p.name !== name);
    this._effectConfigKeys.delete(name);
    const entry = this.mapFx.get(name);
    if (entry) {
      entry.dispose?.();
      this.mapFx.delete(name);
    }
    if (name === 'fog') this._restoreFog();
  }

  startMapEffect(name, config = {}) {
    const needsMap = name === 'rain' || name === 'snow' || name === 'water' || name === 'point_fire' || name === 'wall_of_fire' || name === 'fog';
    const key = JSON.stringify(config || {});
    if (this.mapFx.has(name) && this._effectConfigKeys.get(name) === key) return;
    if (!this.map.state && needsMap) {
      this._pendingMapEffects = this._pendingMapEffects.filter((p) => p.name !== name);
      this._pendingMapEffects.push({ name, config });
      if (name === 'fog') this._applySceneFog(config);
      return;
    }
    this.stopMapEffect(name);
    this._effectConfigKeys.set(name, key);
    if (name === 'fog') this._startFog(config);
    else if (name === 'rain') this._startRain(config);
    else if (name === 'snow') this._startWeather(name, config, { color: 0xffffff, size: 0.07, speed: 1.6, count: 500, drift: 0.9 });
    else if (name === 'water') this._startWater(config);
    else if (name === 'point_fire') this._startPointFire(config);
    else if (name === 'wall_of_fire') this._startWallOfFire(config);
  }

  applyMapStateEffects(effects) {
    const list = Array.isArray(effects) ? effects : [];
    const key = JSON.stringify(list);
    if (this._mapStateEffectsKey === key) return;
    this._mapStateEffectsKey = key;
    const wanted = new Set(list.map((item) => item && item.effect).filter(Boolean));
    for (const name of this._mapStateEffectNames || []) {
      if (!wanted.has(name)) this.stopMapEffect(name);
    }
    this._mapStateEffectNames = wanted;
    for (const payload of list) {
      if (!payload || payload.action === 'stop') {
        if (payload?.effect) this.stopMapEffect(payload.effect);
        continue;
      }
      this.applyEffectSet({ ...payload, exclusive: false });
    }
  }

  onMapReady() {
    const pending = this._pendingMapEffects.splice(0, this._pendingMapEffects.length);
    for (const item of pending) {
      this.startMapEffect(item.name, item.config);
    }
  }

  _mapBounds() {
    const size = this.map.state?.map?.size || [16, 16];
    return floorPlane(size, this.map.cellSize);
  }

  _restoreFog() {
    const fog = this.map.scene.fog;
    if (!fog) return;
    fog.color.setHex(this._baseFog.color);
    this.syncDistanceFog(this.map._illumination ?? 1);
  }

  syncDistanceFog(illumination) {
    const fog = this.map.scene.fog;
    if (!fog) return;
    const range = distanceFogRange(this.map.state?.map?.size, illumination);
    const fogFx = this.mapFx.get('fog');
    const config = fogFx?.config || {};
    const masked = Boolean(config.mask && (config.mask_layers || config.mask_layer));
    if (fogFx) {
      const density = Math.max(0.05, Number(config.density) || 0.45);
      const pull = masked ? 0.12 : 0.22 + density * 0.15;
      fog.near = range.near * (1 - pull);
      fog.far = range.far;
      if (fogFx.volume?.setIllumination) fogFx.volume.setIllumination(illumination);
      return;
    }
    fog.near = range.near;
    fog.far = range.far;
  }

  _applySceneFog(config) {
    const fog = this.map.scene.fog;
    if (!fog) return;
    fog.color.setHex(parseColor(config.color, 0x8a97a8));
    const fogFx = this.mapFx.get('fog');
    if (fogFx) fogFx.config = config;
    this.syncDistanceFog(this.map._illumination ?? 1);
  }

  _startFog(config) {
    const fogFx = createMapFog(this.map.state, config, this.map.cellSize);
    this.mapFx.set('fog', {
      config,
      volume: fogFx,
      update: (dt) => fogFx.update(dt),
      composite: (renderer, scene, camera) => {
        fogFx.composite(renderer, scene, camera, this.map.dir?.position);
      },
      dispose: () => {
        fogFx.dispose();
        this._restoreFog();
      },
    });
    this._applySceneFog(config);
  }

  compositeFog(renderer, scene, camera) {
    const fog = this.mapFx.get('fog');
    if (!fog?.composite) return false;
    fog.composite(renderer, scene, camera);
    return true;
  }

  _startWeather(name, config, opts) {
    const plane = this._mapBounds();
    const count = Number(config.count) > 0 ? Number(config.count) : opts.count;
    const positions = new Float32Array(count * 3);
    const height = 6.5;
    for (let i = 0; i < count; i += 1) {
      positions[i * 3] = Math.random() * plane.width;
      positions[i * 3 + 1] = Math.random() * height;
      positions[i * 3 + 2] = Math.random() * plane.depth;
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    const mat = new THREE.PointsMaterial({
      color: opts.color,
      size: opts.size,
      transparent: true,
      opacity: 0.72,
      depthWrite: false,
    });
    const points = new THREE.Points(geo, mat);
    this.weatherRoot.add(points);
    const speed = opts.speed;
    const drift = opts.drift;
    this.mapFx.set(name, {
      update: (dt) => {
        const arr = geo.attributes.position.array;
        for (let i = 0; i < count; i += 1) {
          arr[i * 3] += Math.sin(i * 0.17) * drift * dt;
          arr[i * 3 + 1] -= speed * dt;
          if (arr[i * 3 + 1] < 0) {
            arr[i * 3] = Math.random() * plane.width;
            arr[i * 3 + 1] = height;
            arr[i * 3 + 2] = Math.random() * plane.depth;
          }
        }
        geo.attributes.position.needsUpdate = true;
      },
      dispose: () => {
        this.weatherRoot.remove(points);
        disposeObject(points);
      },
    });
  }

  _startRain(config) {
    const plane = this._mapBounds();
    const intensity = Math.max(0.15, Math.min(1, Number(config.intensity) || 0.6));
    const count = Math.floor(500 + intensity * 1600);
    const wind = Number(config.wind) || 0;
    const speedMul = Number(config.speed) > 0 ? Number(config.speed) : 1;
    const color = parseColor(config.color, 0xa8c0e6);
    const streak = 0.32;
    const positions = new Float32Array(count * 6);
    const seeds = new Float32Array(count);
    for (let i = 0; i < count; i += 1) {
      const x = Math.random() * plane.width;
      const y = Math.random() * 7.5;
      const z = Math.random() * plane.depth;
      seeds[i] = 8 + Math.random() * 6;
      positions[i * 6] = x;
      positions[i * 6 + 1] = y;
      positions[i * 6 + 2] = z;
      positions[i * 6 + 3] = x + wind * 0.25;
      positions[i * 6 + 4] = y - streak;
      positions[i * 6 + 5] = z;
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    const mat = new THREE.LineBasicMaterial({
      color,
      transparent: true,
      opacity: 0.35 + intensity * 0.35,
      depthWrite: false,
    });
    const lines = new THREE.LineSegments(geo, mat);
    this.weatherRoot.add(lines);
    const splashGeo = new THREE.BufferGeometry();
    const splashPos = new Float32Array(Math.floor(count * 0.15) * 3);
    splashGeo.setAttribute('position', new THREE.BufferAttribute(splashPos, 3));
    const splashes = new THREE.Points(splashGeo, new THREE.PointsMaterial({
      color,
      size: 0.05,
      transparent: true,
      opacity: 0.35,
      depthWrite: false,
    }));
    this.weatherRoot.add(splashes);
    let lightningT = 0;
    const lightning = Boolean(config.lightning);
    const fill = this.map.ambientFill;
    const baseFill = fill ? fill.intensity : 0;
    this.mapFx.set('rain', {
      update: (dt) => {
        const arr = geo.attributes.position.array;
        let si = 0;
        for (let i = 0; i < count; i += 1) {
          const vy = seeds[i] * speedMul * dt;
          arr[i * 6] += wind * 2.2 * dt;
          arr[i * 6 + 1] -= vy;
          arr[i * 6 + 3] = arr[i * 6] + wind * 0.28;
          arr[i * 6 + 4] = arr[i * 6 + 1] - streak;
          arr[i * 6 + 5] = arr[i * 6 + 2];
          if (arr[i * 6 + 1] < 0) {
            arr[i * 6] = Math.random() * plane.width;
            arr[i * 6 + 1] = 6.5 + Math.random() * 1.4;
            arr[i * 6 + 2] = Math.random() * plane.depth;
            if (si < splashPos.length / 3) {
              splashPos[si * 3] = arr[i * 6];
              splashPos[si * 3 + 1] = 0.04;
              splashPos[si * 3 + 2] = arr[i * 6 + 2];
              si += 1;
            }
          }
        }
        geo.attributes.position.needsUpdate = true;
        splashGeo.attributes.position.needsUpdate = true;
        if (lightning && fill) {
          lightningT -= dt;
          if (lightningT <= 0 && Math.random() < (Number(config.lightningFreq) || 0.01) * dt * 60) {
            fill.intensity = baseFill + 1.4 * (Number(config.lightningIntensity) || 1);
            lightningT = 0.08;
          } else if (lightningT <= 0) {
            fill.intensity = baseFill;
          }
        }
      },
      dispose: () => {
        this.weatherRoot.remove(lines);
        this.weatherRoot.remove(splashes);
        disposeObject(lines);
        disposeObject(splashes);
        if (fill) fill.intensity = baseFill;
      },
    });
  }

  _startWater(config) {
    if (this.map.waterSim) {
      this.mapFx.set('water', {
        dispose: () => {},
      });
      return;
    }
    const plane = this._mapBounds();
    const geo = new THREE.PlaneGeometry(plane.width, plane.depth, 48, 48);
    geo.rotateX(-Math.PI / 2);
    const mat = createWaterMaterial();
    if (config.color) mat.uniforms.uShallow.value.set(parseColor(config.color, 0x3aa1c7));
    mat.uniforms.uOpacity.value = 0.42;
    const mesh = new THREE.Mesh(geo, mat);
    mesh.position.set(plane.x, 0, plane.z);
    this.weatherRoot.add(mesh);
    this.mapFx.set('water', {
      update: (dt) => {
        mat.uniforms.uTime.value += dt;
      },
      dispose: () => {
        this.weatherRoot.remove(mesh);
        disposeObject(mesh);
      },
    });
  }

  _addFireAt(point) {
    const c = cellCenter(point.x, point.y, this.map.cellSize);
    const group = new THREE.Group();
    const height = point.shape === 'candle' ? 0.35 : 0.55;
    const core = new THREE.Mesh(
      new THREE.SphereGeometry(point.shape === 'candle' ? 0.08 : 0.16, 10, 10),
      new THREE.MeshBasicMaterial({ color: point.color, transparent: true, opacity: 0.9 }),
    );
    core.position.set(0, height, 0);
    group.add(core);
    let light = null;
    if (!this.map.hasDecorLightAt?.(point.x, point.y)) {
      light = new THREE.PointLight(point.color, 0.55 * point.intensity, 4.5);
      light.position.set(0, height + 0.15, 0);
      group.add(light);
    }
    group.position.set(c.x, 0, c.z);
    this.weatherRoot.add(group);
    return { group, core, light, intensity: point.intensity };
  }

  _startPointFire(config) {
    const points = (config.points || []).map(parseFirePoint).filter(Boolean);
    const emitters = points.map((p) => this._addFireAt(p));
    this.mapFx.set('point_fire', {
      update: () => {
        const t = performance.now() * 0.008;
        for (const em of emitters) {
          const flicker = 0.85 + Math.sin(t + em.group.position.x) * 0.15;
          em.core.scale.setScalar(flicker);
          if (em.light) em.light.intensity = 0.45 * em.intensity * flicker;
        }
      },
      dispose: () => {
        for (const em of emitters) {
          this.weatherRoot.remove(em.group);
          disposeObject(em.group);
        }
      },
    });
  }

  _startWallOfFire(config) {
    const squares = config.squares || config.points || [];
    const emitters = [];
    for (const raw of squares) {
      const point = parseFirePoint(raw) || (Array.isArray(raw) ? parseFirePoint(raw) : null);
      if (!point) continue;
      point.intensity = point.intensity || 1;
      point.shape = 'campfire';
      emitters.push(this._addFireAt(point));
    }
    this.mapFx.set('wall_of_fire', {
      update: () => {
        const t = performance.now() * 0.01;
        for (const em of emitters) {
          em.core.scale.setScalar(0.9 + Math.sin(t + em.group.position.z) * 0.2);
        }
      },
      dispose: () => {
        for (const em of emitters) {
          this.weatherRoot.remove(em.group);
          disposeObject(em.group);
        }
      },
    });
  }
}
