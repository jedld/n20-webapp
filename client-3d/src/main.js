import './style.css';
import { createGameClient } from './gameClient.js';
import { MapScene } from './scene/MapScene.js';
import { isClickNotDrag } from './input/pointer.js';
import { isAttackTargeting, isTargetingStep, rangeFeet, targetingModeForStep } from './input/targeting.js';
import { pathReaches } from './input/movement.js';
import { playAttackEffect, playSpellEffect } from './fx/SpellEffects.js';
import { createObjectHud } from './scene/objectHud.js';

const cfg = window.N20_3D || {};
const statusEl = document.getElementById('vtt3d-status');
const mapNameEl = document.getElementById('vtt3d-map-name');
const actionBar = document.getElementById('centerActionBar');
const actionBarContent = document.getElementById('centerActionBarContent');
const actionBarTitle = document.getElementById('centerActionBarTitle');
const moveHint = document.getElementById('movement-mode-hint');
const targetHint = document.getElementById('target-validation-hint');

function setStatus(text) {
  if (statusEl) statusEl.textContent = text || '';
}

function isDm() {
  const role = cfg.role;
  if (Array.isArray(role)) {
    return role.some((item) => String(item).trim().toLowerCase() === 'dm');
  }
  if (role && typeof role === 'object') {
    return Boolean(role.dm);
  }
  return String(role || '').toLowerCase().split(/[\s,]+/).includes('dm');
}

function illuminationPercent(value) {
  return Math.round(Math.max(0, Math.min(1, Number(value) || 0)) * 100);
}

function hideActionBar() {
  if (actionBar) actionBar.style.display = 'none';
  if (actionBarContent) actionBarContent.innerHTML = '';
}

function showActionBar(title) {
  if (actionBarTitle && title) actionBarTitle.textContent = title;
  if (actionBar) actionBar.style.display = 'block';
}

function setMoveHint(on) {
  if (moveHint) moveHint.style.display = on ? 'block' : 'none';
}

function parseOpts(el) {
  const raw = el.getAttribute('data-action-opts');
  let opts = {};
  if (raw) {
    try {
      opts = JSON.parse(raw);
    } catch {
      opts = {};
    }
  }
  if (isKnockUnconsciousMode() && el.getAttribute('data-can-knock-unconscious') === '1') {
    return { ...opts, knock_unconscious: true };
  }
  return opts;
}

const KNOCKOUT_MODE_STORAGE_KEY = 'n20_knock_unconscious';

function isKnockUnconsciousMode() {
  try {
    return sessionStorage.getItem(KNOCKOUT_MODE_STORAGE_KEY) === '1';
  } catch {
    return false;
  }
}

function setKnockUnconsciousMode(enabled) {
  try {
    sessionStorage.setItem(KNOCKOUT_MODE_STORAGE_KEY, enabled ? '1' : '0');
  } catch {
    /* ignore */
  }
  syncKnockoutModeUI();
}

function syncKnockoutModeUI(root = document) {
  const on = isKnockUnconsciousMode();
  root.querySelectorAll('.action-bar-tabs').forEach((el) => {
    el.classList.toggle('knockout-mode', on);
  });
  root.querySelectorAll('.knockout-toggle').forEach((btn) => {
    btn.classList.toggle('is-active', on);
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    const label = btn.querySelector('.knockout-toggle-label');
    if (label) label.textContent = on ? 'Knock' : 'Kill';
    btn.title = on
      ? 'Knock out: melee attacks that drop a creature to 0 HP leave them unconscious and stable. Click for lethal attacks.'
      : 'Melee attacks are lethal. Click to knock foes unconscious instead.';
  });
  root.querySelectorAll('.knockout-attack-overlay').forEach((el) => {
    el.hidden = !on;
  });
}

function stepType(data) {
  if (!data || typeof data !== 'object') return null;
  const param0 = Array.isArray(data.param) && data.param[0];
  return (param0 && param0.type) || data.type || null;
}

function entityAt(state, x, y) {
  if (!state) return null;
  return (state.entities || []).find((e) => Number(e.x) === x && Number(e.y) === y) || null;
}

function occupantsAt(state, x, y) {
  return (state?.entities || []).filter((e) => {
    const size = Math.max(1, Number(e.size) || 1);
    return x >= e.x && y >= e.y && x < e.x + size && y < e.y + size;
  });
}

function bindActionTabs(root) {
  const buttons = [...root.querySelectorAll('.action-bar-tab-btn')];
  const panels = [...root.querySelectorAll('.action-bar-tab-panel')];
  function select(tab) {
    buttons.forEach((btn) => {
      const on = btn.getAttribute('data-tab') === tab;
      btn.classList.toggle('is-active', on);
      btn.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    panels.forEach((panel) => {
      const on = panel.getAttribute('data-tab') === tab;
      panel.classList.toggle('is-active', on);
      if (on) panel.removeAttribute('hidden');
      else panel.setAttribute('hidden', '');
    });
  }
  buttons.forEach((btn) => {
    btn.addEventListener('click', () => select(btn.getAttribute('data-tab')));
  });
  const active = buttons.find((btn) => btn.classList.contains('is-active'));
  select(active?.getAttribute('data-tab') || 'common');
}

async function main() {
  const canvas = document.getElementById('map-3d');
  if (!canvas) return;
  const client = createGameClient({ username: cfg.username });
  const scene = new MapScene(canvas);
  const noteModal = document.getElementById('noteModal');
  const noteModalTitle = document.getElementById('noteModalTitle');
  const noteModalContent = document.getElementById('noteModalContent');
  const objectHud = createObjectHud({
    layer: document.getElementById('token-hud-layer'),
    scene,
    onNote: ({ title, html, id }) => {
      if (noteModalTitle) noteModalTitle.textContent = title;
      if (noteModalContent) noteModalContent.innerHTML = html;
      if (noteModal) noteModal.hidden = false;
      if (id) {
        fetch('/mark_note_read', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ note_id: id }),
        }).catch(() => {});
      }
    },
    onInteract: ({ obj, qa }) => {
      const pov = cfg.povEntity;
      if (!pov) {
        setStatus('Select a character first');
        return;
      }
      submitAction(
        { uid: pov, x: obj.x, y: obj.y },
        'InteractAction',
        {
          action_type: 'interact',
          target: obj.id,
          object_action: qa.action,
        },
      );
    },
  });
  scene.hudHooks.push(() => objectHud.project());
  const illumPanel = document.getElementById('vtt3d-illumination');
  const illumSlider = document.getElementById('vtt3d-illumination-slider');
  const illumValue = document.getElementById('vtt3d-illumination-value');
  const illumReset = document.getElementById('vtt3d-illumination-reset');
  let illumTimer = null;
  let illumDragging = false;

  function syncIlluminationUi(stateLike) {
    const map = stateLike?.map || {};
    const current = Number.isFinite(Number(map.illumination)) ? Number(map.illumination) : scene._illumination;
    const def = Number.isFinite(Number(map.illumination_default))
      ? Number(map.illumination_default)
      : current;
    const overridden = Boolean(map.illumination_overridden);
    const pct = illuminationPercent(current);
    if (illumSlider && !illumDragging) illumSlider.value = String(pct);
    if (illumValue) illumValue.textContent = `${pct}%`;
    if (illumPanel) illumPanel.classList.toggle('is-default', !overridden);
    if (illumReset) {
      illumReset.disabled = !overridden;
      illumReset.title = overridden
        ? `Reset to this map's default (${illuminationPercent(def)}%)`
        : `Map default (${illuminationPercent(def)}%)`;
    }
  }

  function applyLocalIllumination(value) {
    const illum = scene.setIllumination(value);
    if (state?.map) {
      state.map.illumination = illum;
      const def = Number(state.map.illumination_default);
      if (Number.isFinite(def)) {
        state.map.illumination_overridden = Math.abs(illum - def) > 1e-6;
      } else {
        state.map.illumination_overridden = true;
      }
    }
    syncIlluminationUi(state);
    return illum;
  }

  function persistIllumination(value, { reset = false } = {}) {
    if (!isDm()) return;
    clearTimeout(illumTimer);
    illumTimer = setTimeout(async () => {
      try {
        const payload = reset
          ? await client.postIllumination({ reset: true, mapName: state?.map?.name })
          : await client.postIllumination({ illumination: value, mapName: state?.map?.name });
        if (payload && (payload.status === 'ok' || payload.illumination != null) && state?.map) {
          if (payload.illumination != null) state.map.illumination = payload.illumination;
          if (payload.illumination_default != null) {
            state.map.illumination_default = payload.illumination_default;
          }
          if (payload.illumination_overridden != null) {
            state.map.illumination_overridden = payload.illumination_overridden;
          }
          applyLocalIllumination(state.map.illumination);
        } else if (payload && payload.error) {
          console.warn('illumination persist failed', payload.error);
        }
      } catch (err) {
        console.warn(err);
      }
    }, 80);
  }
  document.getElementById('noteModalClose')?.addEventListener('click', () => {
    if (noteModal) noteModal.hidden = true;
  });
  noteModal?.addEventListener('click', (ev) => {
    if (ev.target === noteModal) noteModal.hidden = true;
  });
  let state = null;
  let refreshTimer = null;
  let hoverTimer = null;
  let lastMapName = null;
  let pointerDown = null;

  const mode = {
    kind: 'idle',
    entityUid: null,
    action: null,
    opts: {},
    source: null,
    path: null,
    targetSquares: null,
  };

  function resetMode() {
    mode.kind = 'idle';
    mode.entityUid = null;
    mode.action = null;
    mode.opts = {};
    mode.source = null;
    mode.path = null;
    mode.targetSquares = null;
    setMoveHint(false);
    if (targetHint) targetHint.textContent = '';
    scene.setPath(null);
  }

  async function refreshMap() {
    try {
      const pendingIllum = illumDragging ? scene._illumination : null;
      state = await client.getMapState();
      if (pendingIllum != null && state?.map) {
        state.map.illumination = pendingIllum;
        state.map.illumination_overridden = true;
      }
      await scene.applyState(state);
      objectHud.rebuild(state.objects || []);
      objectHud.setHover(null);
      if (mapNameEl && state.map?.name) mapNameEl.textContent = state.map.name;
      const mapId = `${state.map?.background || ''}|${(state.map?.size || []).join('x')}`;
      if (mapId !== lastMapName) {
        lastMapName = mapId;
        client.requestEffects();
      }
      syncIlluminationUi(state);
      setStatus('');
    } catch (err) {
      console.error(err);
      setStatus('Failed to load map');
    }
  }

  function scheduleRefresh(ms = 180) {
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(() => {
      refreshMap();
    }, ms);
  }

  document.getElementById('centerActionBarClose')?.addEventListener('click', () => {
    hideActionBar();
    resetMode();
  });
  document.getElementById('zoom-in')?.addEventListener('click', () => scene.zoomBy(1));
  document.getElementById('zoom-out')?.addEventListener('click', () => scene.zoomBy(-1));
  document.getElementById('zoom-reset')?.addEventListener('click', () => scene.resetCamera());
  let topDown = false;
  document.getElementById('camera-topdown')?.addEventListener('click', () => {
    topDown = !topDown;
    scene.setTopDown(topDown);
  });
  illumSlider?.addEventListener('input', () => {
    illumDragging = true;
    const value = Number(illumSlider.value) / 100;
    applyLocalIllumination(value);
    persistIllumination(value);
  });
  illumSlider?.addEventListener('change', () => {
    illumDragging = false;
    const value = Number(illumSlider.value) / 100;
    persistIllumination(value);
    syncIlluminationUi(state);
  });
  illumReset?.addEventListener('click', () => {
    const def = Number(state?.map?.illumination_default);
    if (Number.isFinite(def)) applyLocalIllumination(def);
    if (state?.map) state.map.illumination_overridden = false;
    persistIllumination(null, { reset: true });
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      if (noteModal && !noteModal.hidden) {
        noteModal.hidden = true;
        return;
      }
      hideActionBar();
      resetMode();
    }
  });

  async function openActions(entity) {
    hideActionBar();
    try {
      const payload = await client.getActionsBatch([entity.uid]);
      const section = (payload.sections || [])[0];
      if (!section || section.error || !section.html) {
        setStatus(section?.error || 'No actions');
        return;
      }
      actionBarContent.innerHTML = `<div class="popover-actions-stack"><div class="popover-actions-entry center-action-entry is-active" data-entity-uid="${entity.uid}" data-coords-x="${entity.x}" data-coords-y="${entity.y}"><div class="popover-actions-body">${section.html}</div></div></div>`;
      bindActionTabs(actionBarContent);
      syncKnockoutModeUI(actionBarContent);
      showActionBar(entity.name || 'Actions');
      actionBarContent.querySelectorAll('.knockout-toggle').forEach((btn) => {
        btn.addEventListener('click', (ev) => {
          ev.preventDefault();
          ev.stopPropagation();
          setKnockUnconsciousMode(!isKnockUnconsciousMode());
        });
      });
      actionBarContent.querySelectorAll('.action-button').forEach((btn) => {
        btn.addEventListener('click', (ev) => {
          ev.preventDefault();
          ev.stopPropagation();
          const action = btn.getAttribute('data-action-type');
          const opts = parseOpts(btn);
          submitAction(entity, action, opts);
        });
      });
      actionBarContent.querySelectorAll('.action-end-turn').forEach((btn) => {
        btn.addEventListener('click', async (ev) => {
          ev.preventDefault();
          ev.stopPropagation();
          hideActionBar();
          resetMode();
          await client.postEndTurn();
          scheduleRefresh(80);
        });
      });
    } catch (err) {
      console.error(err);
      setStatus('Could not load actions');
    }
  }

  async function submitAction(entity, action, opts, extra = {}) {
    const body = {
      id: entity.uid || entity,
      action,
      opts: opts || {},
      ...extra,
    };
    if (typeof body.id !== 'string' && entity.uid) body.id = entity.uid;
    const data = await client.postAction(body);
    await handleActionResponse(entity, action, opts, data);
  }

  async function handleActionResponse(entity, action, opts, data) {
    if (!data) return;
    if (data.status === 'ok') {
      hideActionBar();
      resetMode();
      scheduleRefresh(80);
      return;
    }
    if (data.error || data.status === 'error') {
      setStatus(data.primary_error || data.error || data.message || 'Action failed');
      return;
    }
    const type = stepType(data);
    const uid = entity.uid || entity;
    const source = { x: entity.x, y: entity.y, entityUid: uid };
    if (type === 'movement') {
      hideActionBar();
      mode.kind = 'move';
      mode.entityUid = uid;
      mode.action = action;
      mode.opts = opts || {};
      mode.source = source;
      setMoveHint(true);
      setStatus('Click a destination');
      return;
    }
    if (isTargetingStep(type)) {
      hideActionBar();
      mode.kind = type;
      mode.entityUid = uid;
      mode.action = action;
      mode.opts = opts || {};
      mode.source = source;
      mode.range = data.range_max ?? data.range;
      setStatus(type === 'select_target' ? 'Select a target' : 'Choose affected area');
      return;
    }
    if (type === 'select_choice' && data.param?.[0]?.choices) {
      const choice = window.prompt((data.param[0].choices || []).map((c) => c[0]).join('\n'));
      const match = (data.param[0].choices || []).find((c) => String(c[0]) === choice || String(c[1]) === choice);
      if (match) {
        await submitAction(entity, action, opts, { choice: match[1] });
      }
    }
  }

  canvas.addEventListener('pointermove', (event) => {
    const grid = scene.gridFromEvent(event);
    if (mode.kind === 'idle') {
      if (grid) objectHud.setHover(grid, state?.objects || []);
      else objectHud.setHover(null);
      return;
    }
    objectHud.setHover(null);
    if (!grid) return;
    clearTimeout(hoverTimer);
    hoverTimer = setTimeout(async () => {
      if (mode.kind === 'move' && mode.source) {
        try {
          const data = await client.getPath(mode.source, grid);
          if (data?.path && data.placeable !== false && pathReaches(data.path, grid.x, grid.y)) {
            mode.path = data.path;
            scene.setPath(data.path);
          } else {
            mode.path = null;
            scene.setPath(null);
          }
        } catch (err) {
          console.warn(err);
        }
      } else if (mode.kind && mode.kind.startsWith('select_')) {
        try {
          const payload = {
            id: mode.entityUid,
            x: grid.x,
            y: grid.y,
            action_info: mode.action,
            opts: mode.opts || {},
          };
          const data = await client.getTarget(payload);
          const squares = data?.target_squares;
          const valid = data?.valid_target !== false;
          if (Array.isArray(squares) && squares.length) {
            mode.targetSquares = squares;
            scene.setHighlights(squares);
          } else if (isAttackTargeting(mode.kind) && mode.source) {
            const feet = rangeFeet(mode.source, grid, state?.map?.feet_per_grid);
            scene.setTargetLine(mode.source, grid, {
              valid,
              rangeLabel: `${feet}ft`,
            });
          } else {
            scene.setHighlights([[grid.x, grid.y]], valid ? 0xf4d35e : 0x888888);
          }
          if (targetHint) {
            targetHint.textContent = data?.primary_error || (data?.valid_target === false ? 'Invalid target' : '');
          }
        } catch (err) {
          console.warn(err);
        }
      }
    }, 40);
  });

  canvas.addEventListener('pointerleave', (event) => {
    const hud = document.getElementById('token-hud-layer');
    if (event.relatedTarget && hud?.contains(event.relatedTarget)) return;
    if (mode.kind === 'idle') objectHud.setHover(null);
  });
  canvas.addEventListener('pointerdown', (event) => {
    if (event.button !== 0) return;
    pointerDown = { x: event.clientX, y: event.clientY };
  });
  canvas.addEventListener('pointerup', async (event) => {
    if (event.button !== 0) return;
    const start = pointerDown;
    pointerDown = null;
    if (!isClickNotDrag(start, { x: event.clientX, y: event.clientY })) return;
    const grid = scene.gridFromEvent(event);
    if (!grid) return;
    if (mode.kind === 'move') {
      event.preventDefault();
      const path = mode.path;
      if (!path) return;
      const data = await client.postAction({
        id: mode.entityUid,
        action: mode.action,
        opts: mode.opts,
        path,
      });
      resetMode();
      if (data && data.status !== 'ok' && data.error) {
        setStatus(data.error);
      }
      return;
    }
    if (mode.kind && mode.kind.startsWith('select_')) {
      event.preventDefault();
      const extra = { target: [grid.x, grid.y] };
      const tMode = targetingModeForStep(mode.kind);
      if (tMode) extra.mode = tMode;
      const occupant = entityAt(state, grid.x, grid.y);
      if (occupant && mode.kind === 'select_target') {
        extra.target = occupant.uid;
      }
      const pending = {
        uid: mode.entityUid,
        action: mode.action,
        opts: mode.opts,
        squares: mode.targetSquares,
        x: grid.x,
        y: grid.y,
      };
      const data = await client.postAction({
        id: pending.uid,
        action: pending.action,
        opts: pending.opts,
        ...extra,
      });
      if (pending.squares) {
        scene.playAoE(pending.squares);
      }
      resetMode();
      if (data && data.status === 'ok') scheduleRefresh(80);
      else if (data) {
        await handleActionResponse(
          { uid: pending.uid, x: pending.x, y: pending.y },
          pending.action,
          pending.opts,
          data,
        );
      }
      return;
    }
    const hits = occupantsAt(state, grid.x, grid.y);
    if (hits.length) {
      openActions(hits[0]);
    } else {
      hideActionBar();
    }
  });

  function animationPath(entry) {
    if (Array.isArray(entry) && Array.isArray(entry[1])) return { uid: entry[0], path: entry[1], meta: entry[2] };
    if (entry && entry.type === 'move') {
      return { uid: entry.entity_uid || entry.uid, path: entry.path, meta: entry };
    }
    return null;
  }

  function combatPayload(entry) {
    if (!entry) return null;
    if (entry.type === 'attack' || entry.type === 'spell') {
      return { type: entry.type, msg: entry.message || entry };
    }
    const nested = entry.message;
    if (nested && (nested.spell || nested.label || nested.source_pos)) {
      return { type: nested.spell ? 'spell' : 'attack', msg: nested };
    }
    return null;
  }

  async function playCombatFx(type, msg = {}) {
    if (type === 'attack') {
      await playAttackEffect(scene, msg);
      return;
    }
    const name = msg.spell || msg.label || type || 'attack';
    await playSpellEffect(scene, name, msg);
  }

  client.onEffectSet((data) => {
    scene.fx.applyEffectSet(data);
  });
  client.socket.on('connect', () => {
    client.requestEffects();
  });

  client.onMessage(async (data) => {
    const type = data?.type;
    if (type === 'refresh_map' || type === 'refresh_tiles' || type === 'map') {
      scheduleRefresh(type === 'refresh_tiles' ? 40 : 180);
      return;
    }
    if (type === 'vtt3d_illumination') {
      const lighting = data.illumination != null ? data : data.message || {};
      if (lighting.map && state?.map?.name && lighting.map !== state.map.name) return;
      if (!Number.isFinite(Number(lighting.illumination))) return;
      if (state?.map) {
        state.map.illumination = lighting.illumination;
        if (lighting.illumination_default != null) {
          state.map.illumination_default = lighting.illumination_default;
        }
        if (lighting.illumination_overridden != null) {
          state.map.illumination_overridden = lighting.illumination_overridden;
        }
      }
      if (!illumDragging) applyLocalIllumination(lighting.illumination);
      return;
    }
    if (type === 'move') {
      const log = data.message?.animation_log || [];
      for (const entry of log) {
        const move = animationPath(entry);
        if (move?.uid && move.path) {
          await scene.playMove(move.uid, move.path);
          continue;
        }
        const combat = combatPayload(entry);
        if (combat) await playCombatFx(combat.type, combat.msg);
      }
      scheduleRefresh(60);
      return;
    }
    if (type === 'attack') {
      await playCombatFx('attack', data.message || {});
      scheduleRefresh(120);
      return;
    }
    if (type === 'spell') {
      await playCombatFx('spell', data.message || {});
      scheduleRefresh(120);
      return;
    }
    if (type === 'turn' || type === 'initiative' || type === 'focus') {
      const uid = data.message?.entity_uid || data.entity_uid || data.message?.id;
      if (uid) scene.focusEntity(uid);
    }
  });

  setStatus('Loading map…');
  await refreshMap();
}

main().catch((err) => {
  console.error(err);
  setStatus('3D client failed to start');
});
