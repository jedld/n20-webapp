import * as THREE from 'three';
import { CELL_SIZE, floorPlane, fogMaskBytes } from '../geometry.js';

const STEPS = 32;

const FOG_VERT = /* glsl */ `
void main() {
  gl_Position = vec4(position.xy, 0.0, 1.0);
}
`;

const FOG_FRAG = /* glsl */ `
uniform sampler2D tColor;
uniform sampler2D tDepth;
uniform sampler2D uMask;
uniform vec2 uResolution;
uniform vec2 uMapSize;
uniform vec3 uColor;
uniform vec3 uCameraPos;
uniform vec3 uSunDir;
uniform vec3 uBoxMin;
uniform vec3 uBoxMax;
uniform mat4 uInverseProjection;
uniform mat4 uInverseView;
uniform float uTime;
uniform float uSpeed;
uniform float uNoise;
uniform float uExtinction;
uniform float uNear;
uniform float uFar;

float hash13(vec3 p) {
  p = fract(p * 0.1031);
  p += dot(p, p.zyx + 31.32);
  return fract((p.x + p.y) * p.z);
}

float noise3(vec3 p) {
  vec3 i = floor(p);
  vec3 f = fract(p);
  f = f * f * (3.0 - 2.0 * f);
  float n000 = hash13(i);
  float n100 = hash13(i + vec3(1.0, 0.0, 0.0));
  float n010 = hash13(i + vec3(0.0, 1.0, 0.0));
  float n110 = hash13(i + vec3(1.0, 1.0, 0.0));
  float n001 = hash13(i + vec3(0.0, 0.0, 1.0));
  float n101 = hash13(i + vec3(1.0, 0.0, 1.0));
  float n011 = hash13(i + vec3(0.0, 1.0, 1.0));
  float n111 = hash13(i + vec3(1.0, 1.0, 1.0));
  float nx00 = mix(n000, n100, f.x);
  float nx10 = mix(n010, n110, f.x);
  float nx01 = mix(n001, n101, f.x);
  float nx11 = mix(n011, n111, f.x);
  return mix(mix(nx00, nx10, f.y), mix(nx01, nx11, f.y), f.z);
}

float hash12(vec2 p) {
  return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453);
}

vec2 intersectAABB(vec3 ro, vec3 rd, vec3 bmin, vec3 bmax) {
  vec3 inv = 1.0 / (rd + vec3(1e-5));
  vec3 t0 = (bmin - ro) * inv;
  vec3 t1 = (bmax - ro) * inv;
  vec3 tsm = min(t0, t1);
  vec3 tlg = max(t0, t1);
  float tEnter = max(max(tsm.x, tsm.y), tsm.z);
  float tExit = min(min(tlg.x, tlg.y), tlg.z);
  return vec2(tEnter, tExit);
}

float sampleDensity(vec3 p) {
  vec2 uv = vec2(p.x / max(0.001, uMapSize.x), p.z / max(0.001, uMapSize.y));
  if (uv.x < -0.02 || uv.y < -0.02 || uv.x > 1.02 || uv.y > 1.02) return 0.0;
  float mask = texture2D(uMask, clamp(uv, 0.0, 1.0)).r;
  if (mask < 0.02) return 0.0;
  float top = max(0.15, uBoxMax.y - uBoxMin.y);
  float h = clamp(1.0 - (p.y - uBoxMin.y) / top, 0.0, 1.0);
  h *= h;
  vec3 q = p * uNoise;
  q.x += uTime * uSpeed * 0.18;
  q.z += uTime * uSpeed * 0.11;
  q.y += uTime * uSpeed * 0.04;
  float n = noise3(q) * 0.62 + noise3(q * 2.17 + 19.1) * 0.38;
  n = smoothstep(0.22, 0.82, n);
  return uExtinction * mask * h * (0.28 + 0.82 * n);
}

void main() {
  vec2 uv = gl_FragCoord.xy / uResolution;
  vec4 scene = texture2D(tColor, uv);
  float depth = texture2D(tDepth, uv).x;
  vec4 clip = vec4(uv * 2.0 - 1.0, depth * 2.0 - 1.0, 1.0);
  vec4 view = uInverseProjection * clip;
  view.xyz /= max(1e-6, view.w);
  vec3 worldHit = (uInverseView * vec4(view.xyz, 1.0)).xyz;
  vec3 ro = uCameraPos;
  vec3 rd = worldHit - ro;
  float hitDist = length(rd);
  rd /= max(1e-5, hitDist);
  bool hasHit = depth < 0.999;
  float sceneDist = hasHit ? hitDist : uFar;

  vec2 span = intersectAABB(ro, rd, uBoxMin, uBoxMax);
  float t0 = max(span.x, 0.0);
  float t1 = min(span.y, sceneDist);
  if (span.y < span.x || t1 <= t0) {
    gl_FragColor = scene;
  } else {
    float dt = (t1 - t0) / float(${STEPS});
    float jitter = hash12(gl_FragCoord.xy + vec2(uTime * 13.7, uTime * 7.1));
    float t = t0 + dt * jitter;
    float trans = 1.0;
    vec3 fogCol = vec3(0.0);
    vec3 sun = normalize(uSunDir);
    float phase = 0.72 + 0.55 * pow(max(dot(rd, sun), 0.0), 6.0);

    for (int i = 0; i < ${STEPS}; i++) {
      vec3 p = ro + rd * t;
      float dens = sampleDensity(p);
      float absorb = 1.0 - exp(-dens * dt);
      fogCol += trans * uColor * absorb * phase;
      trans *= (1.0 - absorb);
      t += dt;
      if (trans < 0.02) break;
    }

    gl_FragColor = vec4(scene.rgb * trans + fogCol, 1.0);
  }
  #include <colorspace_fragment>
}
`;

function parseColor(value, fallback = 0xaab4c8) {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.startsWith('#')) {
    const n = parseInt(value.slice(1), 16);
    return Number.isFinite(n) ? n : fallback;
  }
  return fallback;
}

export function fogVolumeParams(config = {}) {
  const opacity = Math.max(0.08, Math.min(0.92, Number(config.opacity) || Number(config.density) || 0.5));
  const density = Number(config.density);
  const heightRaw = Number(config.height);
  const top = Number.isFinite(heightRaw) && heightRaw > 0.4 ? heightRaw : 1.75;
  const densMul = Number.isFinite(density) ? 0.75 + Math.max(0, density) * 1.6 : 1.1;
  const extinction = (-Math.log(1 - opacity) / 4.2) * densMul;
  const speed = Number(config.speed) > 0 ? Number(config.speed) : 0.2;
  const noiseScale = Math.max(0.12, Number(config.noise_scale) || 0.35);
  return {
    opacity,
    top,
    extinction,
    speed,
    noise: 0.42 / noiseScale,
    color: parseColor(config.color, 0xaab4c8),
  };
}

function makeMaskTexture(config, size) {
  const { width, height, data } = fogMaskBytes(config, size);
  const rgba = new Uint8Array(width * height * 4);
  for (let i = 0; i < data.length; i += 1) {
    const v = data[i];
    const o = i * 4;
    rgba[o] = v;
    rgba[o + 1] = v;
    rgba[o + 2] = v;
    rgba[o + 3] = 255;
  }
  const tex = new THREE.DataTexture(rgba, width, height, THREE.RGBAFormat);
  tex.flipY = false;
  tex.magFilter = THREE.LinearFilter;
  tex.minFilter = THREE.LinearFilter;
  tex.wrapS = THREE.ClampToEdgeWrapping;
  tex.wrapT = THREE.ClampToEdgeWrapping;
  tex.needsUpdate = true;
  return tex;
}

export function createMapFog(state, config = {}, cellSize = CELL_SIZE) {
  const size = state?.map?.size || [16, 16];
  const plane = floorPlane(size, cellSize);
  const params = fogVolumeParams(config);
  const mask = makeMaskTexture(config, size);
  const uniforms = {
    tColor: { value: null },
    tDepth: { value: null },
    uMask: { value: mask },
    uResolution: { value: new THREE.Vector2(1, 1) },
    uMapSize: { value: new THREE.Vector2(plane.width, plane.depth) },
    uColor: { value: new THREE.Color(params.color) },
    uCameraPos: { value: new THREE.Vector3() },
    uSunDir: { value: new THREE.Vector3(0.32, 0.88, 0.22).normalize() },
    uBoxMin: { value: new THREE.Vector3(-0.15, 0, -0.15) },
    uBoxMax: { value: new THREE.Vector3(plane.width + 0.15, params.top, plane.depth + 0.15) },
    uInverseProjection: { value: new THREE.Matrix4() },
    uInverseView: { value: new THREE.Matrix4() },
    uTime: { value: 0 },
    uSpeed: { value: params.speed },
    uNoise: { value: params.noise },
    uExtinction: { value: params.extinction },
    uNear: { value: 0.1 },
    uFar: { value: 500 },
  };
  const mat = new THREE.ShaderMaterial({
    uniforms,
    vertexShader: FOG_VERT,
    fragmentShader: FOG_FRAG,
    depthTest: false,
    depthWrite: false,
    toneMapped: false,
    fog: false,
  });
  const quad = new THREE.Mesh(new THREE.PlaneGeometry(2, 2), mat);
  quad.frustumCulled = false;
  const passScene = new THREE.Scene();
  passScene.add(quad);
  const passCamera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
  const drawing = new THREE.Vector2();
  let target = null;

  function ensureTarget(renderer) {
    renderer.getDrawingBufferSize(drawing);
    const w = Math.max(1, drawing.x);
    const h = Math.max(1, drawing.y);
    if (!target) {
      const depth = new THREE.DepthTexture(w, h);
      depth.format = THREE.DepthFormat;
      depth.type = THREE.UnsignedIntType;
      depth.minFilter = THREE.NearestFilter;
      depth.magFilter = THREE.NearestFilter;
      target = new THREE.WebGLRenderTarget(w, h, {
        minFilter: THREE.LinearFilter,
        magFilter: THREE.LinearFilter,
        depthBuffer: true,
        depthTexture: depth,
        stencilBuffer: false,
      });
      target.texture.colorSpace = THREE.LinearSRGBColorSpace;
      target.texture.generateMipmaps = false;
    } else if (target.width !== w || target.height !== h) {
      target.setSize(w, h);
    }
    uniforms.tColor.value = target.texture;
    uniforms.tDepth.value = target.depthTexture;
    uniforms.uResolution.value.set(w, h);
    return target;
  }

  return {
    masked: Boolean(config.mask && (config.mask_layers || config.mask_layer)),
    setIllumination(illumination) {
      const illum = Math.max(0, Math.min(1, Number(illumination) || 0));
      uniforms.uExtinction.value = params.extinction * (1.05 - illum * 0.7);
    },
    update(dt) {
      uniforms.uTime.value += dt;
    },
    composite(renderer, scene, camera, sunDir) {
      if (sunDir) uniforms.uSunDir.value.copy(sunDir).normalize();
      uniforms.uInverseProjection.value.copy(camera.projectionMatrixInverse);
      uniforms.uInverseView.value.copy(camera.matrixWorld);
      uniforms.uCameraPos.value.copy(camera.position);
      uniforms.uNear.value = camera.near;
      uniforms.uFar.value = camera.far;
      const rt = ensureTarget(renderer);
      renderer.setRenderTarget(rt);
      renderer.render(scene, camera);
      renderer.setRenderTarget(null);
      renderer.render(passScene, passCamera);
    },
    dispose() {
      if (target) {
        target.dispose();
        target = null;
      }
      quad.geometry.dispose();
      mat.dispose();
      mask.dispose();
    },
  };
}
