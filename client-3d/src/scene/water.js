import * as THREE from 'three';
import {
  CELL_SIZE,
  WATER_DEPTH,
  groupBoxesBySize,
  waterBasinBoxes,
  waterSurfaceBoxes,
} from '../geometry.js';
import { texturedBoxGeometry } from './materials.js';

const WATER_VERT = /* glsl */ `
uniform float uTime;
uniform float uRain;
varying vec3 vWorld;
varying vec3 vView;
varying vec3 vNrm;

void gerstner(inout vec3 pos, inout vec3 nrm, vec2 dir, float steep, float waveLen, float speed) {
  float k = 6.28318 / max(0.15, waveLen);
  float c = sqrt(9.8 / k);
  vec2 d = normalize(dir);
  float f = k * dot(d, pos.xz) - c * speed * uTime;
  float a = steep / k * (0.55 + uRain * 0.7);
  pos.x += d.x * a * cos(f);
  pos.z += d.y * a * cos(f);
  pos.y += a * sin(f);
  float wa = a * k;
  nrm.x -= d.x * wa * cos(f);
  nrm.z -= d.y * wa * cos(f);
  nrm.y += 1.0 - steep * sin(f);
}

void main() {
  vec3 pos = position;
  vec3 nrm = vec3(0.0, 1.0, 0.0);
  gerstner(pos, nrm, vec2(1.0, 0.35), 0.18, 1.15, 1.05);
  gerstner(pos, nrm, vec2(-0.4, 1.0), 0.12, 0.62, 1.35);
  gerstner(pos, nrm, vec2(0.2, -0.9), 0.08, 0.34, 1.7);
  pos.y += uRain * 0.012 * sin(uTime * 18.0 + position.x * 14.0 + position.z * 11.0);
  vec4 world = modelMatrix * vec4(pos, 1.0);
  vWorld = world.xyz;
  vView = cameraPosition - world.xyz;
  vNrm = normalize(mat3(modelMatrix) * normalize(nrm));
  gl_Position = projectionMatrix * viewMatrix * world;
}
`;

const WATER_FRAG = /* glsl */ `
uniform vec3 uShallow;
uniform vec3 uDeep;
uniform vec3 uSpec;
uniform float uTime;
uniform float uRain;
uniform float uOpacity;
varying vec3 vWorld;
varying vec3 vView;
varying vec3 vNrm;

void main() {
  vec3 n = normalize(vNrm);
  vec3 v = normalize(vView);
  float fresnel = pow(1.0 - max(dot(n, v), 0.0), 4.0);
  float depthHint = clamp(0.35 + 0.45 * (1.0 - n.y), 0.0, 1.0);
  vec3 albedo = mix(uShallow, uDeep, depthHint);
  vec3 lightDir = normalize(vec3(0.35, 0.82, 0.28));
  float spec = pow(max(dot(n, normalize(lightDir + v)), 0.0), 64.0);
  spec *= 0.55 + uRain * 0.25;
  float sparkle = uRain * 0.12 * step(0.92, fract(sin(dot(vWorld.xz, vec2(12.9898, 78.233)) + uTime) * 43758.5453));
  vec3 color = albedo + uSpec * (spec + sparkle) + vec3(0.55, 0.72, 0.85) * fresnel * 0.35;
  float alpha = mix(uOpacity, min(0.7, uOpacity + 0.22), fresnel);
  gl_FragColor = vec4(color, alpha);
}
`;

export function createWaterMaterial() {
  return new THREE.ShaderMaterial({
    uniforms: {
      uTime: { value: 0 },
      uRain: { value: 0 },
      uOpacity: { value: 0.38 },
      uShallow: { value: new THREE.Color(0x5ec4d8) },
      uDeep: { value: new THREE.Color(0x1a5a78) },
      uSpec: { value: new THREE.Color(0xe8f6ff) },
    },
    vertexShader: WATER_VERT,
    fragmentShader: WATER_FRAG,
    transparent: true,
    depthWrite: false,
    side: THREE.DoubleSide,
  });
}

export function addWaterTerrain(root, state, mats, cellSize = CELL_SIZE) {
  const cells = state.geometry?.water || [];
  if (!cells.length) return null;
  const group = new THREE.Group();
  const basins = waterBasinBoxes(cells, { cellSize, depth: WATER_DEPTH });
  if (basins.length && mats?.stone) {
    const dummy = new THREE.Object3D();
    for (const boxes of groupBoxesBySize(basins).values()) {
      const sample = boxes[0];
      const geo = texturedBoxGeometry(sample.width, sample.height, sample.depth, 1.6);
      const mesh = new THREE.InstancedMesh(geo, mats.stone, boxes.length);
      boxes.forEach((box, index) => {
        dummy.position.set(box.x, box.y, box.z);
        dummy.updateMatrix();
        mesh.setMatrixAt(index, dummy.matrix);
      });
      mesh.instanceMatrix.needsUpdate = true;
      group.add(mesh);
    }
  }

  const patches = waterSurfaceBoxes(cells, { cellSize });
  const waterMat = createWaterMaterial();
  const sample = patches[0];
  const tile = new THREE.PlaneGeometry(sample.width, sample.depth, 10, 10);
  tile.rotateX(-Math.PI / 2);
  const src = tile.attributes.position;
  const verts = src.count;
  const positions = new Float32Array(verts * patches.length * 3);
  const indices = [];
  const srcIndex = tile.index;
  patches.forEach((box, p) => {
    for (let i = 0; i < verts; i += 1) {
      const o = (p * verts + i) * 3;
      positions[o] = src.getX(i) + box.x;
      positions[o + 1] = src.getY(i);
      positions[o + 2] = src.getZ(i) + box.z;
    }
    if (srcIndex) {
      for (let i = 0; i < srcIndex.count; i += 1) {
        indices.push(srcIndex.getX(i) + p * verts);
      }
    }
  });
  tile.dispose();
  const surfaceGeo = new THREE.BufferGeometry();
  surfaceGeo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  surfaceGeo.setIndex(indices);
  surfaceGeo.computeVertexNormals();
  const surface = new THREE.Mesh(surfaceGeo, waterMat);
  surface.renderOrder = 2;
  group.add(surface);
  root.add(group);
  return {
    group,
    material: waterMat,
    tick(dt, raining = false) {
      waterMat.uniforms.uTime.value += dt;
      const target = raining ? 1 : 0;
      waterMat.uniforms.uRain.value += (target - waterMat.uniforms.uRain.value) * Math.min(1, dt * 2.5);
    },
    dispose() {
      root.remove(group);
    },
  };
}
