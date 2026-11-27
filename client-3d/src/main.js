import './style.css';
import { createGameClient } from './gameClient.js';
import { MapScene } from './scene/MapScene.js';
import { isClickNotDrag } from './input/pointer.js';
import { isAttackTargeting, isMeleeRange, isTargetingStep, rangeFeet, targetingModeForStep } from './input/targeting.js';
import { pathReaches } from './input/movement.js';
import { playAttackEffect, playSpellEffect } from './fx/SpellEffects.js';
import { createObjectHud } from './scene/objectHud.js';
import { initTokenModelPanel } from './ui/tokenModelPanel.js';

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
  // dev/debug handle (console + automated tests): drive the scene directly
  window.n20vtt3d = { scene, client, get state() { return state; } };
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
    // 2D-style hover-move: clicking the chip walks the POV entity straight to
    // the hovered square (no move-mode round trip through the action bar).
    onMove: async ({ x, y }) => {
      const actor = hoverMoveActor();
      if (!actor) return;
      if (hoverMoveInFlight) return;
      hoverMoveInFlight = true;
      try {
        const data = await client.getPath({ x: actor.x, y: actor.y }, { x, y });
        if (!data || !data.path || data.placeable === false || data.path.length <= 1) {
          setStatus('Cannot move to that tile from here');
          return;
        }
        objectHud.setMoveTarget(null);
        scene.setPath(null);
        const res = await client.postAction({
          id: actor.uid,
          action: 'MoveAction',
          opts: { action_type: 'move', source: actor.uid },
          path: data.path,
        });
        if (!res || res.status !== 'ok') {
          setStatus('Could not move to that tile.');
        }
        scheduleRefresh(80);
      } catch (err) {
        console.warn(err);
      } finally {
        hoverMoveInFlight = false;
      }
    },
    onMoveEnter: () => {
      // preview the route while the cursor rests on the chip (2D parity)
      if (hoverMovePath) scene.setPath(hoverMovePath, hoverMoveTerrain);
    },
    onMoveLeave: () => {
      if (mode.kind === 'idle') scene.setPath(null);
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
  initTokenModelPanel({ isDm });
  let state = null;
  let refreshTimer = null;
  let hoverTimer = null;
  let lastMapName = null;
  let pointerDown = null;
  let rotateDrag = null;   // dragging a token's rotation knob (cosmetic)

  const mode = {
    kind: 'idle',
    entityUid: null,
    action: null,
    opts: {},
    source: null,
    path: null,
    targetSquares: null,
    waypoints: [],   // accumulated path walked through Q-pivots (like the 2D view)
    hover: null,     // last hovered square with a valid path
  };

  function resetMode() {
    mode.kind = 'idle';
    mode.entityUid = null;
    mode.action = null;
    mode.opts = {};
    mode.source = null;
    mode.path = null;
    mode.targetSquares = null;
    mode.waypoints = [];
    mode.hover = null;
    setMoveHint(false);
    if (targetHint) targetHint.textContent = '';
    scene.setPath(null);
  }

  // Join the accumulated waypoint path with the latest path segment exactly
  // like the 2D view does: drop the accumulated tail square (the pivot) since
  // the new segment starts from it.
  function fullMovePath(segment) {
    if (!Array.isArray(segment) || !segment.length) return null;
    const tail = segment.map(([x, y]) => [Number(x), Number(y)]);
    if (!mode.waypoints.length) return tail;
    return mode.waypoints.slice(0, -1).concat(tail);
  }

  // Q key: fix the current hover square as a waypoint and path from there,
  // mirroring the 2D movement mode's pivot-point behaviour.
  function commitWaypoint() {
    if (mode.kind !== 'move' || !mode.source || !mode.path || !mode.hover) return;
    if (mode.hover.x === mode.source.x && mode.hover.y === mode.source.y) return;
    // Same convention as the 2D accumulatedPath: raw concat (the shared
    // pivot square is de-duplicated server-side).
    mode.waypoints = mode.waypoints.concat(
      mode.path.map(([x, y]) => [Number(x), Number(y)]),
    );
    mode.source = { x: mode.hover.x, y: mode.hover.y, entityUid: mode.source.entityUid };
    mode.path = null;
    mode.hover = null;
    scene.setPath(null);
    setStatus('Waypoint added · hover a destination to continue');
  }

  // Hover-move quick action (2D parity): hovering a reachable empty square
  // shows a "Move here" chip; hovering the chip previews the path arrow.
  // Disabled during battle, matching 2D's initiative-based movement gating.
  let hoverMoveTimer = null;
  let hoverMoveToken = 0;
  let hoverMoveInFlight = false;
  let hoverMovePath = null;
  let hoverMoveTerrain = null;

  // Hover chips come from tile fixtures (chests, doors) and from creatures
  // carrying quick-interact metadata (loot/carry on corpses, give on NPCs).
  function hoverInteractables() {
    const objs = state?.objects || [];
    const ents = (state?.entities || [])
      .filter((e) => Array.isArray(e.quick_interact) && e.quick_interact.length)
      .map((e) => ({
        id: e.uid,
        x: e.x,
        y: e.y,
        name: e.name,
        quick_interact: e.quick_interact,
      }));
    return objs.concat(ents);
  }

  function hoverMoveActor() {
    if (state?.battle_in_progress) return null;
    const pov = cfg.povEntity;
    if (!pov) return null;
    const ent = (state?.entities || []).find((e) => String(e.uid) === String(pov));
    if (!ent || ent.dead || ent.unconscious) return null;
    return ent;
  }

  function clearHoverMoveChip() {
    clearTimeout(hoverMoveTimer);
    hoverMoveToken += 1;
    hoverMovePath = null;
    hoverMoveTerrain = null;
    objectHud.setMoveTarget(null);
  }

  function queueHoverMoveChip(grid) {
    const actor = hoverMoveActor();
    if (
      !grid
      || !actor
      || (grid.x === actor.x && grid.y === actor.y)
      || occupantsAt(state, grid.x, grid.y).length
    ) {
      clearHoverMoveChip();
      return;
    }
    clearTimeout(hoverMoveTimer);
    hoverMoveTimer = setTimeout(async () => {
      const token = ++hoverMoveToken;
      try {
        const data = await client.getPath({ x: actor.x, y: actor.y }, grid);
        if (token !== hoverMoveToken) return;
        if (
          data
          && Array.isArray(data.path)
          && data.path.length > 1
          && data.placeable !== false
          && pathReaches(data.path, grid.x, grid.y)
        ) {
          hoverMovePath = data.path.map(([px, py]) => [Number(px), Number(py)]);
          hoverMoveTerrain = data.terrain_info;
          objectHud.setMoveTarget(grid);
        } else {
          hoverMovePath = null;
          hoverMoveTerrain = null;
          objectHud.setMoveTarget(null);
        }
      } catch (err) {
        if (token === hoverMoveToken) {
          hoverMovePath = null;
          objectHud.setMoveTarget(null);
        }
      }
    }, 120);
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
      clearHoverMoveChip();
      if (mapNameEl && state.map?.name) mapNameEl.textContent = state.map.name;
      const mapId = `${state.map?.background || ''}|${(state.map?.size || []).join('x')}`;
      if (mapId !== lastMapName) {
        lastMapName = mapId;
        closeLootPanel();
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
    // Q: add a movement waypoint at the hovered square (like the 2D view).
    if ((e.key === 'q' || e.key === 'Q') && !e.ctrlKey && !e.metaKey && !e.altKey) {
      const el = e.target;
      if (el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable)) return;
      if (mode.kind === 'move') {
        e.preventDefault();
        commitWaypoint();
      }
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
      return;
    }
    if (type === 'select_items') {
      // loot / item-transfer panel (corpses, chests): same /action opts.items
      // contract as the 2D transfer modal
      hideActionBar();
      openLootPanel(uid, action, opts || {}, data);
    }
  }

  // --- loot / take-items panel (2D modal-1 equivalent for the 3D VTT) ---
  let lootPanelEl = null;

  function closeLootPanel() {
    if (lootPanelEl) {
      lootPanelEl.remove();
      lootPanelEl = null;
    }
  }

  function lootItemsOf(stepData) {
    const p0 = (Array.isArray(stepData.param) && stepData.param[0]) || {};
    return p0.items || stepData.valid_items || {};
  }

  function escapeHtml3d(text) {
    return String(text)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function openLootPanel(entityUid, action, opts, stepData) {
    closeLootPanel();
    const items = lootItemsOf(stepData);
    const names = Object.keys(items);
    const targetUid = String(opts?.target || '');
    const found = (state?.entities || []).find((e) => String(e.uid) === targetUid)
      || (state?.objects || []).find((o) => String(o.id) === targetUid);
    const target = found?.name || found?.label || '';
    const wrap = document.createElement('div');
    wrap.className = 'vtt-3d-note-modal vtt-3d-loot-modal';
    const head = names.length
      ? `<h3>Loot ${escapeHtml3d(target)}</h3>`
      : '<h3>Loot</h3>';
    const rows = names.map((name) => {
      const it = items[name] || {};
      const qty = Math.max(1, Number(it.qty) || 1);
      const label = (it.name || name).replace(/_/g, ' ');
      return `<div class="vtt-3d-loot-row" data-item="${escapeHtml3d(name)}">`
        + `<span class="vtt-3d-loot-name">${escapeHtml3d(label)}</span>`
        + `<span class="vtt-3d-loot-qty">x${qty}</span>`
        + `<input class="vtt-3d-loot-count" type="number" min="1" max="${qty}" value="${qty}">`
        + `<button type="button" class="btn btn-xs btn-default vtt-3d-loot-take">Take</button>`
        + '</div>';
    }).join('');
    wrap.innerHTML = `<div class="vtt-3d-note-modal-panel">`
      + `${head}`
      + (names.length
        ? `${rows}<div class="vtt-3d-loot-actions">`
          + '<button type="button" class="btn btn-sm btn-primary vtt-3d-loot-all">Take All</button>'
          + '</div>'
        : '<p>Nothing left to take.</p>')
      + '<button type="button" class="btn btn-xs vtt-3d-loot-close" title="Close">✕</button>'
      + '</div>';
    document.body.appendChild(wrap);
    lootPanelEl = wrap;

    wrap.querySelector('.vtt-3d-loot-close')?.addEventListener('click', () => {
      closeLootPanel();
      scheduleRefresh(80);
    });
    const take = (list) => postLootTransfer(entityUid, action, opts, list);
    wrap.querySelectorAll('.vtt-3d-loot-take').forEach((btn) => {
      btn.addEventListener('click', () => {
        const row = btn.closest('.vtt-3d-loot-row');
        const qty = Number(row.querySelector('.vtt-3d-loot-count')?.value) || 1;
        take([{ name: row.dataset.item, qty }]);
      });
    });
    wrap.querySelector('.vtt-3d-loot-all')?.addEventListener('click', () => {
      take(names.map((name) => ({ name, qty: Number(items[name]?.qty) || 1 })));
    });
  }

  async function postLootTransfer(entityUid, action, opts, list) {
    const nextOpts = {
      ...opts,
      items: {
        from: {
          items: list.map((l) => l.name),
          qty: list.map((l) => l.qty),
        },
        to: { items: [], qty: [] },
      },
    };
    const resp = await client.postAction({ id: entityUid, action, opts: nextOpts });
    if (!resp) return;
    if (resp.status === 'ok' || resp.error || resp.status === 'error') {
      closeLootPanel();
      if (resp.error || resp.status === 'error') {
        setStatus(resp.primary_error || resp.error || 'Could not take those items');
      }
      scheduleRefresh(80);
      return;
    }
    if (stepType(resp) === 'select_items') {
      // keep the panel open with the updated contents (2D parity)
      openLootPanel(entityUid, action, { ...opts }, resp);
      scheduleRefresh(200);
    }
  }

  canvas.addEventListener('pointermove', (event) => {
    if (rotateDrag) {
      const yaw = scene.rotationFromEvent(event);
      if (yaw != null) scene.setTokenYaw(rotateDrag.uid, yaw);
      return;
    }
    const grid = scene.gridFromEvent(event);
    if (mode.kind === 'idle') {
      if (grid) objectHud.setHover(grid, hoverInteractables());
      else objectHud.setHover(null);
      queueHoverMoveChip(grid);
      return;
    }
    objectHud.setHover(null);
    clearHoverMoveChip();
    if (!grid) return;
    if (mode.kind && mode.kind.startsWith('select_')) {
      // instant feedback while the debounced /target round-trip is in flight
      if (isAttackTargeting(mode.kind) && mode.source) {
        scene.setTargetLine(mode.source, grid, {
          valid: true,
          melee: isMeleeRange(mode.range),
        });
      }
    }
    clearTimeout(hoverTimer);
    hoverTimer = setTimeout(async () => {
      if (mode.kind === 'move' && mode.source) {
        try {
          const data = await client.getPath(mode.source, grid, {
            accumulatedPath: mode.waypoints.length ? mode.waypoints : undefined,
          });
          if (data?.path && data.placeable !== false && pathReaches(data.path, grid.x, grid.y)) {
            mode.path = data.path;
            mode.hover = { x: grid.x, y: grid.y };
            scene.setPath(fullMovePath(data.path), data.terrain_info);
          } else {
            mode.path = null;
            mode.hover = null;
            scene.setPath(mode.waypoints.length > 1 ? mode.waypoints : null);
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
              melee: isMeleeRange(mode.range),
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
    if (mode.kind === 'idle') {
      objectHud.setHover(null);
      clearHoverMoveChip();
    }
  });
  canvas.addEventListener('pointerdown', (event) => {
    if (event.button !== 0) return;
    // rotation gesture: grabbing the knob on a selected token's base ring
    if (mode.kind === 'idle' && scene.rotateHandleHit(event)) {
      const uid = scene._selectedUid;
      if (uid) {
        rotateDrag = { uid, start: scene.tokenYaw.get(uid) || 0 };
        scene._dragRotationUid = uid;
        scene._updateRotationGhost();
        scene.controls.enabled = false;
        canvas.setPointerCapture?.(event.pointerId);
        event.preventDefault();
      }
      return;
    }
    pointerDown = { x: event.clientX, y: event.clientY };
  });
  canvas.addEventListener('pointerup', async (event) => {
    if (event.button !== 0) return;
    if (rotateDrag) {
      const { uid, start } = rotateDrag;
      rotateDrag = null;
      scene._dragRotationUid = null;
      scene._updateRotationGhost();
      scene.controls.enabled = true;
      canvas.releasePointerCapture?.(event.pointerId);
      if (scene._selectedUid === uid) scene._repositionSelection();
      const yaw = scene.tokenYaw.get(uid) || 0;
      if (Math.abs(yaw - start) >= 0.01) {
        client.rotateToken(uid, (yaw * 180) / Math.PI);
      }
      return;
    }
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
        path: fullMovePath(path),
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
    if (event.ctrlKey || event.metaKey) {
      // DM POV switch (same convention as the 2D map): Ctrl+click a token to
      // view from that entity, Ctrl+click an empty square to see everything.
      if (!isDm()) return;
      const target = hits.length ? hits[0].uid : null;
      const data = await client.switchPov(target);
      if (data && data.error) {
        setStatus(data.error);
      } else {
        hideActionBar();
        scene.clearSelection();
        resetMode();
        setStatus(target ? `POV: ${hits[0].name || hits[0].uid}` : 'POV: all');
        scheduleRefresh(80);
      }
      return;
    }
    if (hits.length) {
      openActions(hits[0]);
      scene.setSelected(hits[0].uid);
    } else {
      hideActionBar();
      scene.clearSelection();
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

  // cosmetic token rotation from other players/DM (degrees -> radians)
  client.onTokenRotation((data) => {
    if (!data?.uid) return;
    if (rotateDrag?.uid === data.uid) return; // our own drag echo
    const yaw = (Number(data.yaw) || 0) * Math.PI / 180;
    scene.setTokenYaw(data.uid, yaw);
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
