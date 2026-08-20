/**
 * Deterministic cave-stone albedo / height fields.
 * No DOM or Three.js — Vitest can sample this in Node.
 *
 * UVs are periodic in [0, 1). The same seed always yields the same pixels.
 */

export const CAVE_STONE_SEED = 0xC4E57E01;
export const CAVE_STONE_SIZE = 512;
export const CAVE_NOISE_PERIOD = 12;

function u32(n) {
  return n >>> 0;
}

export function hash2i(ix, iy, seed) {
  let h = Math.imul(ix | 0, 374761393)
    + Math.imul(iy | 0, 668265263)
    + Math.imul(seed | 0, 1442695041);
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  return u32(h ^ (h >>> 16)) / 4294967296;
}

function fade(t) {
  return t * t * t * (t * (t * 6 - 15) + 10);
}

function lerp(a, b, t) {
  return a + (b - a) * t;
}

function clamp01(n) {
  return n < 0 ? 0 : n > 1 ? 1 : n;
}

function smoothstep(edge0, edge1, x) {
  const t = clamp01((x - edge0) / (edge1 - edge0));
  return t * t * (3 - 2 * t);
}

function wrapIndex(i, period) {
  const p = period | 0;
  return ((i % p) + p) % p;
}

/** Seamless value noise. ``x``/``y`` live in the same units as ``period``. */
export function valueNoise(x, y, period, seed) {
  const x0 = Math.floor(x);
  const y0 = Math.floor(y);
  const fx = x - x0;
  const fy = y - y0;
  const xa = wrapIndex(x0, period);
  const ya = wrapIndex(y0, period);
  const xb = wrapIndex(x0 + 1, period);
  const yb = wrapIndex(y0 + 1, period);
  const u = fade(fx);
  const v = fade(fy);
  const n00 = hash2i(xa, ya, seed);
  const n10 = hash2i(xb, ya, seed);
  const n01 = hash2i(xa, yb, seed);
  const n11 = hash2i(xb, yb, seed);
  return lerp(lerp(n00, n10, u), lerp(n01, n11, u), v);
}

export function fbm(x, y, period, seed, octaves = 4) {
  let amp = 1;
  let freq = 1;
  let sum = 0;
  let norm = 0;
  for (let i = 0; i < octaves; i += 1) {
    const p = period * freq;
    sum += amp * valueNoise(x * freq, y * freq, p, seed + i * 19);
    norm += amp;
    amp *= 0.5;
    freq *= 2;
  }
  return sum / (norm || 1);
}

function mix3(a, b, t) {
  return [
    lerp(a[0], b[0], t),
    lerp(a[1], b[1], t),
    lerp(a[2], b[2], t),
  ];
}

/**
 * Sample one periodic UV. ``u``/``v`` in [0, 1).
 * Returns sRGB 0–1, bump height, and PBR roughness (1 = matte, ~0.2 = wet/polished).
 */
export function sampleCaveStone(u, v, seed = CAVE_STONE_SEED) {
  const period = CAVE_NOISE_PERIOD;
  const x = ((u % 1) + 1) % 1 * period;
  const y = ((v % 1) + 1) % 1 * period;

  const warpX = fbm(x, y, period, seed + 101, 3) * 1.25;
  const warpY = fbm(x + 5, y + 8, period, seed + 202, 3) * 1.25;
  const wx = x + warpX;
  const wy = y + warpY;
  const height = fbm(wx, wy, period, seed + 303, 5);
  const ridged = 1 - Math.abs(fbm(x * 2, y * 2, period * 2, seed + 404, 4) * 2 - 1);
  const damp = fbm(x + 8, y, period, seed + 505, 3);
  const grain = valueNoise(x * 8, y * 8, period * 8, seed + 606);
  const grit = valueNoise(x * 14, y * 14, period * 14, seed + 616);
  const fleck = valueNoise(x * 20, y * 20, period * 20, seed + 626);

  const polishField = fbm(x + warpX * 0.25, y + warpY * 0.2, period, seed + 707, 4);
  const flowMask = smoothstep(0.54, 0.76, polishField);
  const calcite = fbm(x + 4, y + 7, period, seed + 717, 3);
  const calciteMask = smoothstep(0.62, 0.84, calcite) * (1 - flowMask * 0.45);
  const smoothMask = clamp01(flowMask * 0.92 + calciteMask * 0.72);
  const dripNoise = fbm(x, y, period, seed + 727, 2);
  const drip = Math.sin((y * Math.PI * 8) / period + dripNoise * 4.2 + warpY * 1.1);
  const bands = (0.5 + 0.5 * drip) * flowMask;

  const crevice = smoothstep(0.5, 0.12, height);
  const ridge = smoothstep(0.5, 0.9, height);
  const vein = smoothstep(0.55, 0.9, ridged) * (1 - crevice);
  const moss = smoothstep(0.3, 0.68, damp) * crevice * (1 - smoothMask);
  const wet = smoothstep(0.4, 0.76, damp) * clamp01(crevice * 0.65 + flowMask * 0.5);
  const pits = smoothstep(0.76, 0.93, grit) * (1 - smoothMask);

  const limestone = [132, 122, 108];
  const wetCol = [58, 52, 48];
  const ochre = [154, 118, 74];
  const mossCol = [78, 92, 68];
  const dry = [176, 168, 154];
  const flowstone = [188, 174, 154];
  const calciteCol = [204, 196, 182];
  const stain = [142, 92, 58];
  const chert = [74, 70, 64];
  const bandShadow = [146, 130, 112];

  let rgb = mix3(wetCol, limestone, smoothstep(0.12, 0.82, height));
  rgb = mix3(rgb, ochre, vein * 0.48);
  rgb = mix3(rgb, stain, vein * (1 - smoothMask) * 0.28);
  rgb = mix3(rgb, mossCol, moss * 0.42);
  rgb = mix3(rgb, dry, ridge * 0.28);
  rgb = mix3(rgb, flowstone, flowMask * 0.78);
  rgb = mix3(rgb, calciteCol, calciteMask * 0.62);
  rgb = mix3(rgb, bandShadow, bands * 0.38);
  rgb = mix3(rgb, wetCol, wet * 0.4);
  rgb = mix3(rgb, chert, smoothstep(0.86, 0.96, fleck) * (1 - smoothMask) * 0.5);

  const ao = 1 - crevice * 0.45 * (1 - smoothMask * 0.35);
  const speckleAmt = lerp(13, 2.5, smoothMask);
  const speckle = (grain - 0.5) * speckleAmt + (1 - smoothMask) * (grit - 0.5) * 6;

  let roughness = 0.9 + (grit - 0.5) * 0.14 + (grain - 0.5) * 0.05;
  roughness = lerp(roughness, 0.3, flowMask);
  roughness = lerp(roughness, 0.22, calciteMask);
  roughness = lerp(roughness, 0.16, wet * 0.92);
  roughness = lerp(roughness, 0.98, pits);

  const micro = (1 - smoothMask) * ((grit - 0.5) * 0.1 + pits * -0.1);
  const h = height * 0.58 + ridge * 0.16 - crevice * 0.14 + micro
    + flowMask * (0.04 + bands * 0.05);
  const heightOut = lerp(clamp01(h), 0.5 + bands * 0.08, smoothMask * 0.7);

  return {
    r: clamp01((rgb[0] * ao + speckle) / 255),
    g: clamp01((rgb[1] * ao + speckle) / 255),
    b: clamp01((rgb[2] * ao + speckle) / 255),
    height: clamp01(heightOut),
    roughness: clamp01(roughness),
  };
}

export function fillCaveStoneMaps(width = CAVE_STONE_SIZE, height = CAVE_STONE_SIZE, seed = CAVE_STONE_SEED) {
  const albedo = new Uint8Array(width * height * 4);
  const bump = new Uint8Array(width * height * 4);
  const roughness = new Uint8Array(width * height * 4);
  for (let py = 0; py < height; py += 1) {
    const v = py / height;
    for (let px = 0; px < width; px += 1) {
      const sample = sampleCaveStone(px / width, v, seed);
      const i = (py * width + px) * 4;
      albedo[i] = (sample.r * 255) | 0;
      albedo[i + 1] = (sample.g * 255) | 0;
      albedo[i + 2] = (sample.b * 255) | 0;
      albedo[i + 3] = 255;
      const h = (sample.height * 255) | 0;
      bump[i] = h;
      bump[i + 1] = h;
      bump[i + 2] = h;
      bump[i + 3] = 255;
      const rough = (sample.roughness * 255) | 0;
      roughness[i] = rough;
      roughness[i + 1] = rough;
      roughness[i + 2] = rough;
      roughness[i + 3] = 255;
    }
  }
  return { albedo, bump, roughness, width, height };
}
