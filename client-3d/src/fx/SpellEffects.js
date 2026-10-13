import { gridPos, recipeDuration, recipeFor } from './spellCatalog.js';

function playCue(name) {
  if (!name) return;
  try {
    if (typeof window !== 'undefined' && window.SFX && typeof window.SFX.play === 'function') {
      window.SFX.play(name);
    }
  } catch {
    /* optional audio */
  }
}

function wait(ms) {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

function asList(value) {
  if (value == null) return [];
  return Array.isArray(value) ? value : [value];
}

function sourceOf(payload) {
  return gridPos(payload.source_pos) || payload.source || null;
}

function targetsOf(payload) {
  const fromSquares = (payload.target_squares || []).map(gridPos).filter(Boolean);
  const t = payload.target;
  const isPoint = Array.isArray(t) && t.length === 2 && typeof t[0] === 'number' && typeof t[1] === 'number';
  const explicit = isPoint ? [t] : asList(t).filter(Boolean);
  const pos = gridPos(payload.target_pos);
  if (explicit.length) return explicit;
  if (pos) return [pos];
  return fromSquares;
}

function resolveAnchor(fx, payload, which, fallbackHeight) {
  if (which === 'source') {
    return fx.resolveWorld(sourceOf(payload), fallbackHeight)
      || fx.resolveWorld(payload.source, fallbackHeight);
  }
  const t = targetsOf(payload)[0];
  return fx.resolveWorld(t, fallbackHeight)
    || fx.resolveWorld(payload.target_pos, fallbackHeight)
    || fx.resolveWorld(payload.target, fallbackHeight);
}

function playKind(fx, recipe, payload) {
  const color = recipe.color;
  const duration = recipe.duration || 0.45;
  const from = fx.resolveWorld(sourceOf(payload), recipe.height ?? 0.7)
    || fx.resolveWorld(payload.source, recipe.height ?? 0.7);
  const targetList = targetsOf(payload);
  const firstTarget = fx.resolveWorld(targetList[0], recipe.height ?? 0.7);
  const miss = Boolean(payload.miss || payload.missed);
  const squares = payload.target_squares;

  playCue(recipe.sfx);

  switch (recipe.kind) {
    case 'projectile': {
      const dests = targetList.length ? targetList : [payload.target_pos];
      const count = Math.max(1, Number(recipe.count) || dests.length || 1);
      const stagger = Number(recipe.stagger) || 0;
      for (let i = 0; i < count; i += 1) {
        const destRaw = dests[i % dests.length];
        const to = fx.resolveWorld(destRaw, recipe.height ?? 0.7);
        const delay = stagger * i * 1000;
        setTimeout(() => {
          fx.projectile({ from, to, color, duration, arc: recipe.arc ?? 0.4, miss, radius: recipe.radius ?? 0.11 });
          if (recipe.impactSfx) playCue(recipe.impactSfx);
        }, delay);
      }
      break;
    }
    case 'beam':
      fx.beam({ from, to: firstTarget, color, duration });
      if (recipe.impactSfx) playCue(recipe.impactSfx);
      break;
    case 'burst': {
      const at = recipe.at === 'source' ? from : (firstTarget || from);
      fx.burst({
        at,
        color,
        duration,
        radius: recipe.radius ?? 0.7,
        fromAbove: Boolean(recipe.fromAbove),
      });
      if (recipe.useSquares && Array.isArray(squares) && squares.length) {
        fx.cellGlow(squares, color, duration, 0.38);
      }
      if (recipe.impactSfx) playCue(recipe.impactSfx);
      break;
    }
    case 'cone':
      fx.cone({ from, to: firstTarget || from, color, duration, radius: recipe.radius ?? 1.3 });
      if (Array.isArray(squares) && squares.length) fx.cellGlow(squares, color, duration, 0.28);
      break;
    case 'area':
    case 'cube':
      if (Array.isArray(squares) && squares.length) fx.cellGlow(squares, color, duration);
      else if (firstTarget) fx.burst({ at: firstTarget, color, duration, radius: recipe.radius ?? 1.1 });
      break;
    case 'heal': {
      const spots = recipe.allTargets
        ? targetList.map((t) => fx.resolveWorld(t, recipe.height ?? 0.7)).filter(Boolean)
        : [recipe.at === 'source' ? from : (firstTarget || from)];
      for (const at of spots) {
        fx.particles({ at, color, duration, count: 20, mode: 'up' });
        fx.auraBurst({ at, color, duration });
      }
      break;
    }
    case 'aura': {
      const at = resolveAnchor(fx, payload, recipe.at || 'target', 0.15) || firstTarget || from;
      fx.auraBurst({ at, color, duration });
      fx.particles({ at, color, duration, count: 10, mode: 'up' });
      break;
    }
    case 'teleport':
      fx.teleport({ from, to: firstTarget, color, duration });
      break;
    case 'melee':
      fx.melee({ from, to: firstTarget, color, duration });
      if (recipe.impactSfx) playCue(recipe.impactSfx);
      break;
    case 'look': {
      const at = from || firstTarget;
      fx.look({ at, color, duration });
      break;
    }
    default:
      if (from && firstTarget) fx.projectile({ from, to: firstTarget, color, duration, miss });
      else if (Array.isArray(squares) && squares.length) fx.cellGlow(squares, color, duration);
      break;
  }
}

async function playCombo(fx, recipe, payload) {
  const steps = recipe.steps || [];
  for (let i = 0; i < steps.length; i += 1) {
    playKind(fx, steps[i], payload);
    const waitMs = Math.max(0, (Number(steps[i].duration) || 0.3) * 850);
    if (i < steps.length - 1) await wait(waitMs);
  }
}

export function playSpellEffect(scene, name, payload = {}) {
  const fx = scene?.fx;
  if (!fx) return Promise.resolve();
  const recipe = recipeFor(name, payload);
  if (recipe.kind === 'combo') {
    return playCombo(fx, recipe, payload).then(() => undefined);
  }
  playKind(fx, recipe, payload);
  return wait(recipeDuration(recipe) * 1000);
}

export function playAttackEffect(scene, payload = {}) {
  return playSpellEffect(scene, 'attack', payload);
}
