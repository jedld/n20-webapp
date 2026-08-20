import * as THREE from 'three';
import { boxFaceUVRepeats } from '../geometry.js';
import {
  CAVE_STONE_SEED,
  CAVE_STONE_SIZE,
  fillCaveStoneMaps,
} from './caveStone.js';

export const VTT3D_TEXTURES = {
  stone: '/assets/vtt3d/dungeon_stone.jpg',
  wood: '/assets/vtt3d/oak_planks.jpg',
  door: '/assets/vtt3d/wooden_door_panel.jpg',
};

export const WALL_STYLES = new Set(['dungeon', 'cave', 'mansion', 'gothic', 'wood', 'office']);
export const CAVE_TRIPLANAR_SCALE = 0.38;
const HEIGHT_LOCKED_STYLES = new Set(['mansion', 'gothic', 'office']);

export function normalizeWallStyle(raw) {
  const text = String(raw || '').trim().toLowerCase();
  if (WALL_STYLES.has(text)) return text;
  if (['cavern', 'grotto', 'limestone'].includes(text)) return 'cave';
  if (['stone', 'cellar', 'basement'].includes(text)) return 'dungeon';
  if (['manor', 'interior', 'house', 'plaster'].includes(text)) return 'mansion';
  if (['gothic_mansion', 'haunted', 'victorian'].includes(text)) return 'gothic';
  if (['timber', 'oak', 'tavern'].includes(text)) return 'wood';
  if (['modern', 'corporate', 'workplace', 'startup'].includes(text)) return 'office';
  return 'dungeon';
}

export function wallSpecFromState(state) {
  const raw = state?.map?.walls || {};
  const style = normalizeWallStyle(raw.style);
  const albedo = raw.albedo ? String(raw.albedo) : null;
  return { style, albedo };
}

export function applyBoxUVs(geometry, width, height, depth, tilesPerUnit = 1, options = {}) {
  const uv = geometry.getAttribute('uv');
  if (!uv) return geometry;
  const repeats = boxFaceUVRepeats(width, height, depth, tilesPerUnit, options);
  for (let face = 0; face < 6; face += 1) {
    const ru = repeats[face][0];
    const rv = repeats[face][1];
    const base = face * 4;
    const corners = [[0, 1], [1, 1], [0, 0], [1, 0]];
    for (let i = 0; i < 4; i += 1) {
      uv.setXY(base + i, corners[i][0] * ru, corners[i][1] * rv);
    }
  }
  uv.needsUpdate = true;
  return geometry;
}

export function texturedBoxGeometry(width, height, depth, tilesPerUnit = 1, options = {}) {
  const geo = new THREE.BoxGeometry(width, height, depth);
  applyBoxUVs(geo, width, height, depth, tilesPerUnit, options);
  return geo;
}

function configureAlbedo(tex, { repeat = false, anisotropy = 8, wrapT } = {}) {
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = anisotropy;
  tex.wrapS = repeat ? THREE.RepeatWrapping : THREE.ClampToEdgeWrapping;
  tex.wrapT = wrapT || (repeat ? THREE.RepeatWrapping : THREE.ClampToEdgeWrapping);
  tex.needsUpdate = true;
  return tex;
}

function hash2(x, y) {
  const n = Math.sin(x * 127.1 + y * 311.7) * 43758.5453;
  return n - Math.floor(n);
}

/** Gothic manor plaster + dark oak wainscot. V maps once over wall height. */
export function makeMansionWallTexture() {
  const w = 512;
  const h = 512;
  const canvas = document.createElement('canvas');
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext('2d');
  const wainscot = 0.34;
  const rail = 0.03;
  const base = 0.06;
  const crown = 0.05;

  for (let y = 0; y < h; y += 1) {
    const v = 1 - y / (h - 1);
    let r;
    let g;
    let b;
    if (v < base) {
      r = 42 + hash2(1, y) * 8;
      g = 28 + hash2(2, y) * 6;
      b = 18 + hash2(3, y) * 5;
    } else if (v < wainscot) {
      r = 68 + hash2(4, y) * 10;
      g = 42 + hash2(5, y) * 8;
      b = 24 + hash2(6, y) * 6;
    } else if (v < wainscot + rail) {
      r = 92 + hash2(7, y) * 10;
      g = 62 + hash2(8, y) * 8;
      b = 36;
    } else if (v > 1 - crown) {
      r = 168 + hash2(9, y) * 12;
      g = 154 + hash2(10, y) * 10;
      b = 128 + hash2(11, y) * 8;
    } else {
      const soot = Math.max(0, (v - 0.7) * 40);
      r = 196 - soot + hash2(12, y) * 10;
      g = 182 - soot * 0.85 + hash2(13, y) * 8;
      b = 158 - soot * 0.7 + hash2(14, y) * 8;
    }
    ctx.fillStyle = `rgb(${r | 0},${g | 0},${b | 0})`;
    ctx.fillRect(0, y, w, 1);
  }

  const panelTop = Math.floor((1 - wainscot) * h);
  const panelBot = Math.floor((1 - base) * h);
  const boards = 4;
  ctx.lineWidth = 3;
  for (let i = 0; i <= boards; i += 1) {
    const x = Math.round((i / boards) * w);
    ctx.strokeStyle = 'rgba(24, 14, 8, 0.55)';
    ctx.beginPath();
    ctx.moveTo(x + 0.5, panelTop);
    ctx.lineTo(x + 0.5, panelBot);
    ctx.stroke();
    ctx.strokeStyle = 'rgba(120, 86, 48, 0.28)';
    ctx.beginPath();
    ctx.moveTo(x + 2.5, panelTop);
    ctx.lineTo(x + 2.5, panelBot);
    ctx.stroke();
  }
  for (let i = 0; i < 1800; i += 1) {
    const x = (hash2(i, 21) * w) | 0;
    const y = (hash2(i, 22) * h) | 0;
    const v = 1 - y / h;
    ctx.fillStyle = v < wainscot
      ? `rgba(20, 12, 6, ${0.04 + hash2(i, 23) * 0.08})`
      : `rgba(90, 70, 50, ${0.03 + hash2(i, 24) * 0.07})`;
    ctx.fillRect(x, y, 1 + (hash2(i, 25) * 2), 1 + (hash2(i, 26) * 2));
  }
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.wrapS = THREE.RepeatWrapping;
  tex.wrapT = THREE.ClampToEdgeWrapping;
  tex.needsUpdate = true;
  return tex;
}

function wrapX(x, w) {
  return ((x % w) + w) % w;
}

function fillWrappedRect(ctx, x, y, rw, rh, w) {
  ctx.fillRect(x, y, rw, rh);
  if (x < 0) ctx.fillRect(x + w, y, rw, rh);
  if (x + rw > w) ctx.fillRect(x - w, y, rw, rh);
}

function strokeWrappedPath(ctx, draw, w) {
  ctx.save();
  draw();
  ctx.restore();
  ctx.save();
  ctx.translate(-w, 0);
  draw();
  ctx.restore();
  ctx.save();
  ctx.translate(w, 0);
  draw();
  ctx.restore();
}

/** Dark damask wallpaper, ebonized panels, soot and water stains. V maps once over wall height. */
export function makeGothicWallTexture() {
  const w = 1024;
  const h = 1024;
  const canvas = document.createElement('canvas');
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext('2d');
  const base = 0.07;
  const wainscot = 0.38;
  const rail = 0.035;
  const crown = 0.07;

  for (let y = 0; y < h; y += 1) {
    const v = 1 - y / (h - 1);
    let r;
    let g;
    let b;
    if (v < base) {
      r = 18 + hash2(1, y) * 6;
      g = 12 + hash2(2, y) * 4;
      b = 10 + hash2(3, y) * 3;
    } else if (v < wainscot) {
      r = 32 + hash2(4, y) * 8;
      g = 18 + hash2(5, y) * 5;
      b = 14 + hash2(6, y) * 4;
    } else if (v < wainscot + rail) {
      r = 72 + hash2(7, y) * 14;
      g = 52 + hash2(8, y) * 10;
      b = 28 + hash2(9, y) * 8;
    } else if (v > 1 - crown) {
      r = 36 + hash2(10, y) * 8;
      g = 28 + hash2(11, y) * 6;
      b = 24 + hash2(12, y) * 5;
    } else {
      const soot = Math.max(0, (v - 0.62) * 90);
      r = 92 - soot * 0.55 + hash2(13, y) * 8;
      g = 28 - soot * 0.18 + hash2(14, y) * 5;
      b = 32 - soot * 0.16 + hash2(15, y) * 6;
    }
    ctx.fillStyle = `rgb(${Math.max(0, r) | 0},${Math.max(0, g) | 0},${Math.max(0, b) | 0})`;
    ctx.fillRect(0, y, w, 1);
  }

  const paperTop = Math.floor(crown * h);
  const paperBot = Math.floor((1 - wainscot - rail) * h);
  const cellW = w / 4;
  const cellH = (paperBot - paperTop) / 3.2;
  ctx.lineWidth = 2;
  for (let row = -1; row < 6; row += 1) {
    for (let col = -1; col < 6; col += 1) {
      const ox = col * cellW + (row % 2 ? cellW / 2 : 0);
      const oy = paperTop + row * cellH * 0.62;
      if (oy > paperBot - 8 || oy + cellH < paperTop) continue;
      strokeWrappedPath(ctx, () => {
        ctx.beginPath();
        ctx.moveTo(ox + cellW / 2, oy + 6);
        ctx.lineTo(ox + cellW * 0.82, oy + cellH * 0.5);
        ctx.lineTo(ox + cellW / 2, oy + cellH - 6);
        ctx.lineTo(ox + cellW * 0.18, oy + cellH * 0.5);
        ctx.closePath();
        ctx.fillStyle = 'rgba(28, 8, 12, 0.28)';
        ctx.fill();
        ctx.strokeStyle = 'rgba(18, 6, 8, 0.45)';
        ctx.stroke();
        ctx.beginPath();
        ctx.moveTo(ox + cellW / 2, oy + cellH * 0.22);
        ctx.lineTo(ox + cellW * 0.68, oy + cellH * 0.5);
        ctx.lineTo(ox + cellW / 2, oy + cellH * 0.78);
        ctx.lineTo(ox + cellW * 0.32, oy + cellH * 0.5);
        ctx.closePath();
        ctx.fillStyle = 'rgba(120, 42, 48, 0.12)';
        ctx.fill();
      }, w);
    }
  }

  const panelTop = Math.floor((1 - wainscot) * h);
  const panelBot = Math.floor((1 - base) * h);
  const panels = 3;
  const insetX = 28;
  const insetY = 22;
  for (let i = 0; i < panels; i += 1) {
    const x0 = (i / panels) * w + insetX;
    const x1 = ((i + 1) / panels) * w - insetX;
    const y0 = panelTop + insetY;
    const y1 = panelBot - insetY;
    ctx.fillStyle = 'rgba(8, 4, 3, 0.55)';
    ctx.fillRect(x0, y0, x1 - x0, y1 - y0);
    ctx.strokeStyle = 'rgba(70, 42, 28, 0.35)';
    ctx.lineWidth = 4;
    ctx.strokeRect(x0 + 3, y0 + 3, x1 - x0 - 6, y1 - y0 - 6);
    ctx.strokeStyle = 'rgba(12, 8, 6, 0.8)';
    ctx.lineWidth = 2;
    ctx.strokeRect(x0 + 10, y0 + 10, x1 - x0 - 20, y1 - y0 - 20);
    ctx.fillStyle = 'rgba(48, 28, 20, 0.2)';
    ctx.fillRect(x0 + 14, y0 + 14, x1 - x0 - 28, y1 - y0 - 28);
  }

  const railY = Math.floor((1 - wainscot - rail) * h);
  ctx.fillStyle = 'rgba(92, 64, 32, 0.55)';
  ctx.fillRect(0, railY, w, 6);
  ctx.fillStyle = 'rgba(18, 10, 6, 0.7)';
  ctx.fillRect(0, railY + 6, w, 4);

  for (let i = 0; i < 14; i += 1) {
    const x = wrapX(hash2(i, 40) * w, w);
    const stainW = 18 + hash2(i, 41) * 40;
    const stainH = 40 + hash2(i, 42) * 90;
    const grd = ctx.createLinearGradient(x, 8, x, 8 + stainH);
    grd.addColorStop(0, 'rgba(12, 16, 14, 0.45)');
    grd.addColorStop(1, 'rgba(12, 16, 14, 0)');
    ctx.fillStyle = grd;
    fillWrappedRect(ctx, x - stainW / 2, 8, stainW, stainH, w);
  }

  ctx.strokeStyle = 'rgba(8, 6, 5, 0.35)';
  ctx.lineWidth = 1;
  for (let i = 0; i < 12; i += 1) {
    const x = hash2(i, 50) * w;
    ctx.beginPath();
    ctx.moveTo(x, paperTop + hash2(i, 51) * 40);
    ctx.lineTo(x + (hash2(i, 52) - 0.5) * 30, paperBot - hash2(i, 53) * 60);
    ctx.stroke();
  }

  for (let i = 0; i < 2800; i += 1) {
    const x = (hash2(i, 21) * w) | 0;
    const y = (hash2(i, 22) * h) | 0;
    const v = 1 - y / h;
    const mold = v > 0.85 ? 0.06 : 0;
    ctx.fillStyle = v < wainscot
      ? `rgba(6, 3, 2, ${0.05 + hash2(i, 23) * 0.1})`
      : `rgba(${20 + mold * 80}, ${10 + mold * 40}, ${8 + mold * 20}, ${0.04 + hash2(i, 24) * 0.08 + mold})`;
    ctx.fillRect(x, y, 1 + (hash2(i, 25) * 2), 1 + (hash2(i, 26) * 3));
  }

  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.wrapS = THREE.RepeatWrapping;
  tex.wrapT = THREE.ClampToEdgeWrapping;
  tex.needsUpdate = true;
  return tex;
}

/** Painted drywall, aluminum baseboard and ceiling reveal. V maps once over wall height. */
export function makeOfficeWallTexture() {
  const w = 1024;
  const h = 1024;
  const canvas = document.createElement('canvas');
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext('2d');
  const base = 0.08;
  const dado = 0.3;
  const rail = 0.025;
  const crown = 0.055;

  for (let y = 0; y < h; y += 1) {
    const v = 1 - y / (h - 1);
    let r;
    let g;
    let b;
    if (v < base) {
      r = 48 + hash2(1, y) * 8;
      g = 52 + hash2(2, y) * 8;
      b = 58 + hash2(3, y) * 8;
    } else if (v < dado) {
      r = 214 + hash2(4, y) * 8;
      g = 218 + hash2(5, y) * 7;
      b = 224 + hash2(6, y) * 6;
    } else if (v < dado + rail) {
      r = 138 + hash2(7, y) * 10;
      g = 146 + hash2(8, y) * 8;
      b = 156 + hash2(9, y) * 8;
    } else if (v > 1 - crown) {
      r = 72 + hash2(10, y) * 8;
      g = 78 + hash2(11, y) * 7;
      b = 86 + hash2(12, y) * 6;
    } else {
      const wash = (v - 0.55) * 18;
      r = 232 + wash + hash2(13, y) * 6;
      g = 234 + wash * 0.8 + hash2(14, y) * 5;
      b = 238 + wash * 0.5 + hash2(15, y) * 5;
    }
    ctx.fillStyle = `rgb(${Math.max(0, Math.min(255, r)) | 0},${Math.max(0, Math.min(255, g)) | 0},${Math.max(0, Math.min(255, b)) | 0})`;
    ctx.fillRect(0, y, w, 1);
  }

  const dadoTop = Math.floor((1 - dado) * h);
  const dadoBot = Math.floor((1 - base) * h);
  ctx.fillStyle = 'rgba(255, 255, 255, 0.18)';
  ctx.fillRect(0, dadoTop, w, 3);
  ctx.fillStyle = 'rgba(20, 24, 28, 0.22)';
  ctx.fillRect(0, dadoBot - 2, w, 3);

  const boards = 4;
  ctx.lineWidth = 2;
  for (let i = 1; i < boards; i += 1) {
    const x = Math.round((i / boards) * w) + 0.5;
    ctx.strokeStyle = 'rgba(170, 176, 184, 0.45)';
    ctx.beginPath();
    ctx.moveTo(x, Math.floor(crown * h));
    ctx.lineTo(x, dadoTop);
    ctx.stroke();
  }

  const crownY = Math.floor(crown * h);
  ctx.fillStyle = 'rgba(210, 216, 222, 0.45)';
  ctx.fillRect(0, crownY, w, 4);
  ctx.fillStyle = 'rgba(18, 20, 24, 0.5)';
  ctx.fillRect(0, crownY + 4, w, 3);

  for (let i = 0; i < 2200; i += 1) {
    const x = (hash2(i, 21) * w) | 0;
    const y = (hash2(i, 22) * h) | 0;
    const v = 1 - y / h;
    ctx.fillStyle = v < dado
      ? `rgba(90, 96, 104, ${0.03 + hash2(i, 23) * 0.06})`
      : `rgba(180, 186, 194, ${0.02 + hash2(i, 24) * 0.05})`;
    ctx.fillRect(x, y, 1 + (hash2(i, 25) * 2), 1 + (hash2(i, 26) * 2));
  }
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.wrapS = THREE.RepeatWrapping;
  tex.wrapT = THREE.ClampToEdgeWrapping;
  tex.needsUpdate = true;
  return tex;
}

/** Frosted glass slab with aluminum stiles and rails. */
export function makeOfficeDoorTexture() {
  const w = 512;
  const h = 1024;
  const canvas = document.createElement('canvas');
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext('2d');
  const stile = 0.11;
  const topRail = 0.08;
  const botRail = 0.14;
  const midY0 = 0.38;
  const midY1 = 0.46;

  ctx.fillStyle = '#c5ccd4';
  ctx.fillRect(0, 0, w, h);

  const x0 = Math.floor(stile * w);
  const x1 = Math.floor((1 - stile) * w);
  const y0 = Math.floor(topRail * h);
  const y1 = Math.floor((1 - botRail) * h);
  const mid0 = Math.floor(midY0 * h);
  const mid1 = Math.floor(midY1 * h);

  const pane = ctx.createLinearGradient(x0, y0, x1, y1);
  pane.addColorStop(0, '#d8e4ee');
  pane.addColorStop(0.45, '#c5d4e0');
  pane.addColorStop(1, '#b7c6d4');
  ctx.fillStyle = pane;
  ctx.fillRect(x0, y0, x1 - x0, y1 - y0);

  ctx.fillStyle = '#9aa5b0';
  ctx.fillRect(x0, mid0, x1 - x0, mid1 - mid0);
  ctx.fillStyle = 'rgba(255, 255, 255, 0.28)';
  ctx.fillRect(x0, mid0, x1 - x0, 2);
  ctx.fillStyle = 'rgba(30, 36, 42, 0.28)';
  ctx.fillRect(x0, mid1 - 2, x1 - x0, 2);

  ctx.save();
  ctx.beginPath();
  ctx.rect(x0, y0, x1 - x0, mid0 - y0);
  ctx.rect(x0, mid1, x1 - x0, y1 - mid1);
  ctx.clip();
  for (let i = 0; i < 90; i += 1) {
    const y = y0 + (i / 90) * (y1 - y0);
    ctx.fillStyle = `rgba(255, 255, 255, ${0.03 + (i % 2) * 0.04})`;
    ctx.fillRect(x0, y, x1 - x0, 2);
  }
  for (let i = 0; i < 1400; i += 1) {
    const x = x0 + (hash2(i, 31) * (x1 - x0));
    const y = y0 + (hash2(i, 32) * (y1 - y0));
    ctx.fillStyle = `rgba(255, 255, 255, ${0.04 + hash2(i, 33) * 0.08})`;
    ctx.fillRect(x, y, 1 + hash2(i, 34) * 2, 1);
  }
  ctx.restore();

  ctx.strokeStyle = 'rgba(255, 255, 255, 0.35)';
  ctx.lineWidth = 3;
  ctx.strokeRect(x0 + 2, y0 + 2, x1 - x0 - 4, y1 - y0 - 4);
  ctx.strokeStyle = 'rgba(40, 48, 56, 0.35)';
  ctx.lineWidth = 2;
  ctx.strokeRect(1, 1, w - 2, h - 2);

  ctx.fillStyle = 'rgba(255, 255, 255, 0.2)';
  ctx.fillRect(0, 0, Math.floor(stile * w), h);
  ctx.fillStyle = 'rgba(20, 24, 28, 0.18)';
  ctx.fillRect(Math.floor((1 - stile) * w), 0, Math.ceil(stile * w), h);

  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.wrapS = THREE.ClampToEdgeWrapping;
  tex.wrapT = THREE.ClampToEdgeWrapping;
  tex.needsUpdate = true;
  return tex;
}

function dataTextureFromRgba(data, width, height, anisotropy) {
  const tex = new THREE.DataTexture(data, width, height, THREE.RGBAFormat);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.wrapS = THREE.RepeatWrapping;
  tex.wrapT = THREE.RepeatWrapping;
  tex.magFilter = THREE.LinearFilter;
  tex.minFilter = THREE.LinearMipmapLinearFilter;
  tex.generateMipmaps = true;
  tex.anisotropy = anisotropy;
  tex.needsUpdate = true;
  tex.flipY = false;
  return tex;
}

/** Seeded cave limestone albedo + height bump. Seamless and tileable. */
export function makeCaveWallTextures({
  seed = CAVE_STONE_SEED,
  size = CAVE_STONE_SIZE,
  anisotropy = 8,
} = {}) {
  const maps = fillCaveStoneMaps(size, size, seed);
  const albedo = dataTextureFromRgba(maps.albedo, maps.width, maps.height, anisotropy);
  const bump = dataTextureFromRgba(maps.bump, maps.width, maps.height, anisotropy);
  const roughness = dataTextureFromRgba(maps.roughness, maps.width, maps.height, anisotropy);
  bump.colorSpace = THREE.NoColorSpace;
  roughness.colorSpace = THREE.NoColorSpace;
  albedo.userData.seed = seed;
  bump.userData.seed = seed;
  roughness.userData.seed = seed;
  return { albedo, bump, roughness };
}

const CAVE_TRIPLANAR_GLSL = /* glsl */ `
vec4 caveTriplanar(sampler2D tex, vec3 pos, vec3 nor, float scale) {
  vec3 blending = abs(normalize(nor));
  blending = max(blending - 0.22, 0.0);
  float bsum = blending.x + blending.y + blending.z;
  blending = bsum > 0.0 ? blending / bsum : vec3(0.0, 1.0, 0.0);
  vec3 p = pos * scale;
  vec4 cx = texture(tex, p.zy);
  vec4 cy = texture(tex, p.xz);
  vec4 cz = texture(tex, p.xy);
  return cx * blending.x + cy * blending.y + cz * blending.z;
}
`;

function applyCaveTriplanar(material, scale = CAVE_TRIPLANAR_SCALE) {
  material.userData.caveScale = { value: scale };
  material.onBeforeCompile = (shader) => {
    shader.uniforms.caveScale = material.userData.caveScale;
    shader.vertexShader = shader.vertexShader.replace(
      '#include <common>',
      `#include <common>
varying vec3 vCavePos;
varying vec3 vCaveNormal;`,
    ).replace(
      '#include <project_vertex>',
      `#include <project_vertex>
{
  vec4 cavePos = vec4( transformed, 1.0 );
  #ifdef USE_INSTANCING
    cavePos = instanceMatrix * cavePos;
  #endif
  vCavePos = ( modelMatrix * cavePos ).xyz;
  vec3 caveN = objectNormal;
  #ifdef USE_INSTANCING
    caveN = mat3( instanceMatrix ) * caveN;
  #endif
  vCaveNormal = normalize( mat3( modelMatrix ) * caveN );
}`,
    );
    shader.fragmentShader = shader.fragmentShader.replace(
      '#include <common>',
      `#include <common>
varying vec3 vCavePos;
varying vec3 vCaveNormal;
uniform float caveScale;
${CAVE_TRIPLANAR_GLSL}`,
    ).replace(
      'vec4 sampledDiffuseColor = texture2D( map, vMapUv );',
      'vec4 sampledDiffuseColor = caveTriplanar( map, vCavePos, vCaveNormal, caveScale );',
    ).replace(
      'vec4 sampledDiffuseColor = texture( map, vMapUv );',
      'vec4 sampledDiffuseColor = caveTriplanar( map, vCavePos, vCaveNormal, caveScale );',
    );
    const bumpSample = `
#ifdef USE_BUMPMAP
{
  float height = caveTriplanar( bumpMap, vCavePos, vCaveNormal, caveScale ).r;
  vec2 dH = vec2( dFdx( height ), dFdy( height ) ) * bumpScale * 6.0;
  normal = normalize( normal - vec3( dH, 0.0 ) );
}
#endif
`;
    shader.fragmentShader = shader.fragmentShader.replace('#include <bumpmap_fragment>', bumpSample);
    shader.fragmentShader = shader.fragmentShader.replace(
      '#include <roughnessmap_fragment>',
      `float roughnessFactor = roughness;
#ifdef USE_ROUGHNESSMAP
{
  vec4 texelRoughness = caveTriplanar( roughnessMap, vCavePos, vCaveNormal, caveScale );
  roughnessFactor *= texelRoughness.g;
}
#endif
`,
    );
  };
  material.customProgramCacheKey = () => `cave-triplanar-rough-${scale}`;
  return material;
}

export async function loadVttMaterials(loader, {
  anisotropy = 8,
  style = 'dungeon',
  albedo = null,
} = {}) {
  const wallStyle = normalizeWallStyle(style);
  const load = (url) => new Promise((resolve) => {
    if (!url) {
      resolve(null);
      return;
    }
    loader.load(url, (tex) => resolve(tex), undefined, () => resolve(null));
  });
  const [stoneMap, woodMap, doorMap, customMap] = await Promise.all([
    load(VTT3D_TEXTURES.stone),
    load(VTT3D_TEXTURES.wood),
    load(VTT3D_TEXTURES.door),
    load(albedo),
  ]);
  if (stoneMap) configureAlbedo(stoneMap, { repeat: true, anisotropy });
  if (woodMap) configureAlbedo(woodMap, { repeat: true, anisotropy });
  if (doorMap) configureAlbedo(doorMap, { repeat: false, anisotropy });
  if (customMap) {
    configureAlbedo(customMap, {
      repeat: true,
      anisotropy,
      wrapT: HEIGHT_LOCKED_STYLES.has(wallStyle) ? THREE.ClampToEdgeWrapping : THREE.RepeatWrapping,
    });
  }

  const stone = new THREE.MeshStandardMaterial({
    color: stoneMap ? 0xffffff : 0x6d6458,
    map: stoneMap,
    bumpMap: stoneMap,
    bumpScale: 0.12,
    roughness: 0.92,
    metalness: 0.02,
  });
  const wood = new THREE.MeshStandardMaterial({
    color: woodMap ? 0xffffff : 0x7a4e2d,
    map: woodMap,
    bumpMap: woodMap,
    bumpScale: 0.06,
    roughness: 0.78,
    metalness: 0.04,
  });
  let doorFace = new THREE.MeshStandardMaterial({
    color: doorMap ? 0xffffff : 0x6b3e22,
    map: doorMap,
    bumpMap: doorMap,
    bumpScale: 0.05,
    roughness: 0.72,
    metalness: 0.05,
  });
  const iron = new THREE.MeshStandardMaterial({
    color: 0x3a3c44,
    roughness: 0.38,
    metalness: 0.82,
  });
  const rust = new THREE.MeshStandardMaterial({
    color: 0x5a4034,
    roughness: 0.62,
    metalness: 0.55,
  });

  let wall = stone;
  let wallLockHeight = false;
  let frame = stone;
  if (wallStyle === 'wood') {
    wall = wood;
    frame = wood;
  } else if (wallStyle === 'mansion' || wallStyle === 'gothic') {
    const wallMap = customMap || (wallStyle === 'gothic' ? makeGothicWallTexture() : makeMansionWallTexture());
    if (!customMap) wallMap.anisotropy = anisotropy;
    wall = new THREE.MeshStandardMaterial({
      color: 0xffffff,
      map: wallMap,
      bumpMap: wallMap,
      bumpScale: wallStyle === 'gothic' ? 0.07 : 0.045,
      roughness: wallStyle === 'gothic' ? 0.9 : 0.86,
      metalness: 0.02,
    });
    wallLockHeight = true;
    if (wallStyle === 'gothic') {
      frame = wood.clone();
      frame.color.setHex(0x3a241c);
      frame.roughness = 0.62;
      doorFace = doorFace.clone();
      doorFace.color.setHex(0x5a3226);
      doorFace.roughness = 0.78;
    } else {
      frame = wood;
    }
  } else if (wallStyle === 'office') {
    const wallMap = customMap || makeOfficeWallTexture();
    if (!customMap) wallMap.anisotropy = anisotropy;
    wall = new THREE.MeshStandardMaterial({
      color: 0xffffff,
      map: wallMap,
      bumpMap: wallMap,
      bumpScale: 0.035,
      roughness: 0.84,
      metalness: 0.04,
    });
    wallLockHeight = true;
    frame = new THREE.MeshStandardMaterial({
      color: 0x9aa3ad,
      roughness: 0.3,
      metalness: 0.78,
    });
    const officeDoor = makeOfficeDoorTexture();
    officeDoor.anisotropy = anisotropy;
    doorFace = new THREE.MeshStandardMaterial({
      color: 0xffffff,
      map: officeDoor,
      bumpMap: officeDoor,
      bumpScale: 0.02,
      roughness: 0.22,
      metalness: 0.18,
      transparent: true,
      opacity: 0.9,
    });
  } else if (wallStyle === 'cave') {
    const maps = customMap
      ? { albedo: customMap, bump: customMap }
      : makeCaveWallTextures({ anisotropy, seed: CAVE_STONE_SEED });
    if (!customMap) maps.albedo.anisotropy = anisotropy;
    wall = applyCaveTriplanar(new THREE.MeshStandardMaterial({
      color: 0xffffff,
      map: maps.albedo,
      bumpMap: maps.bump,
      bumpScale: 0.34,
      roughness: customMap ? 0.94 : 1,
      roughnessMap: customMap ? null : maps.roughness,
      metalness: 0.02,
      side: THREE.DoubleSide,
    }));
    frame = wall;
  } else if (customMap) {
    wall = new THREE.MeshStandardMaterial({
      color: 0xffffff,
      map: customMap,
      bumpMap: customMap,
      bumpScale: 0.1,
      roughness: 0.9,
      metalness: 0.02,
    });
  }

  let handle = iron;
  if (wallStyle === 'office') {
    handle = new THREE.MeshStandardMaterial({
      color: 0xd5dbe2,
      roughness: 0.22,
      metalness: 0.92,
    });
  }

  for (const mat of [stone, wood, doorFace, iron, rust, wall, frame, handle]) {
    if (mat) mat.userData.shared = true;
  }
  return {
    stone,
    wood,
    doorFace,
    iron,
    rust,
    wall,
    frame,
    handle,
    style: wallStyle,
    wallLockHeight,
  };
}

export function disposeVttMaterials(mats) {
  if (!mats) return;
  const seen = new Set();
  for (const mat of Object.values(mats)) {
    if (!mat || typeof mat.dispose !== 'function' || seen.has(mat)) continue;
    seen.add(mat);
    if (mat.map && !seen.has(mat.map)) {
      seen.add(mat.map);
      mat.map.dispose();
    }
    if (mat.bumpMap && mat.bumpMap !== mat.map && !seen.has(mat.bumpMap)) {
      seen.add(mat.bumpMap);
      mat.bumpMap.dispose();
    }
    if (mat.roughnessMap && mat.roughnessMap !== mat.map && !seen.has(mat.roughnessMap)) {
      seen.add(mat.roughnessMap);
      mat.roughnessMap.dispose();
    }
    mat.dispose();
  }
}

export function doorPanelMaterials(edge, doorFace) {
  return [edge, edge, edge, edge, doorFace, doorFace];
}
