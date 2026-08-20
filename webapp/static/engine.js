// --- Global Helpers & Utilities ---

// Game Time Formatting Functions
function formatGameTime(totalSeconds) {
  const days = Math.floor(totalSeconds / (24 * 60 * 60));
  const hours = Math.floor((totalSeconds % (24 * 60 * 60)) / (60 * 60));
  const minutes = Math.floor((totalSeconds % (60 * 60)) / 60);
  const seconds = totalSeconds % 60;

  const parts = [];
  if (days > 0) parts.push(`${days} day${days !== 1 ? 's' : ''}`);
  if (hours > 0) parts.push(`${hours} hour${hours !== 1 ? 's' : ''}`);
  if (minutes > 0) parts.push(`${minutes} minute${minutes !== 1 ? 's' : ''}`);
  if (seconds > 0 || parts.length === 0) parts.push(`${seconds} second${seconds !== 1 ? 's' : ''}`);

  return parts.join(', ');
}

function updateGameTimeDisplay(gameTimeSeconds) {
  const formattedTime = formatGameTime(gameTimeSeconds);
  $('#game-time-text').text(formattedTime);
}

let scale = 1;
let keyboardMovementMode = false;
let keyboardMovementSource = null;
let keyboardMovementPath = [];
let keyboardMovementPivotPoints = [];
let globalCanvas = null;
let globalCtx = null;
let talkToEntityMode = false; // Flag to track when user is talking to an entity
let dialogPartnerEntityId = null;
let dialogNpcManualMode = false;
let currentDialogNpcId = null;
let forcedConversationMode = false; // Blocks movement/actions during scripted confrontations
let goalArrivalConversationMode = false; // NPC-initiated arrival debrief; lighter than forced mode
function isInteractionLocked() {
  return talkToEntityMode || forcedConversationMode;
}

function enterForcedConversationMode(entityId, entityName) {
  forcedConversationMode = true;
  $('body').addClass('forced-conversation-active');
  $('#close-dialog').hide();
  if (entityId && typeof handleDialogBubbleClick === 'function') {
    handleDialogBubbleClick(entityId, entityName, { forced: true });
  }
}

function exitForcedConversationMode() {
  forcedConversationMode = false;
  $('body').removeClass('forced-conversation-active');
  $('#close-dialog').show();
  if (!talkToEntityMode) {
    $('#jrpgDialogPanel').hide();
  }
}

function enterGoalArrivalConversationMode(entityId, entityName) {
  goalArrivalConversationMode = true;
  $('body').addClass('goal-arrival-active');
  $('#dialogNoResponse').show();
  if (entityId && typeof handleDialogBubbleClick === 'function') {
    handleDialogBubbleClick(entityId, entityName, { goalArrival: true });
  }
}

function exitGoalArrivalConversationMode() {
  goalArrivalConversationMode = false;
  $('body').removeClass('goal-arrival-active');
  $('#dialogNoResponse').hide();
  if (!talkToEntityMode && !forcedConversationMode) {
    $('#jrpgDialogPanel').hide();
  }
}
window.enterGoalArrivalConversationMode = enterGoalArrivalConversationMode;
window.exitGoalArrivalConversationMode = exitGoalArrivalConversationMode;
window.enterForcedConversationMode = enterForcedConversationMode;
window.exitForcedConversationMode = exitForcedConversationMode;
let dialogMessageProcessing = false; // Flag to track if a dialog message is being processed
window.__n20_dialogMessageProcessing = false;
let insightCheckProcessing = false; // Separate flag so insight rolls work while waiting for NPC replies
let perceptionCheckProcessing = false;
let activeDialogWaitingId = null; // Waiting indicator cleared when NPC reply arrives over socket

// Pan and Zoom state (Roll20-style viewport)
let viewportPan = { x: 0, y: 0 };
let viewportZoom = 1.0;
const ZOOM_MIN = 0.25;
const ZOOM_MAX = 3.0;
const ZOOM_STEP = 0.1;
let isPanning = false;
let panStart = { x: 0, y: 0 };
let panStartViewport = { x: 0, y: 0 };
// Right-click panning: we only commit to a pan once the cursor moves beyond a
// small drag threshold so a regular right-click is still available for
// cancel-mode handlers / context menus that need it.
let rightPanArmed = false;          // mousedown happened with button=2
let rightPanDragged = false;        // exceeded drag threshold -> actually panning
let suppressNextContextMenu = false; // set after a real right-drag pan
const RIGHT_PAN_DRAG_THRESHOLD = 4;  // pixels

// Ghost token system for pending moves
let pendingMoves = new Map(); // entityId -> { sourceX, sourceY, targetX, targetY, ghostElement }
let moveRequestTimeouts = new Map(); // entityId -> timeoutId for cleanup

// Mode & State Variables (global scope for access by functions)
let valid_target_cache = {};

function extractTargetValidation(data) {
  if (!data || typeof data !== 'object') {
    return { valid: true, primary_error: null, error_messages: [] };
  }
  const hasValidityFlag = Object.prototype.hasOwnProperty.call(data, 'valid_target');
  const valid = hasValidityFlag ? data.valid_target !== false : true;
  let primary_error = null;
  if (!valid) {
    primary_error = data.primary_error
      || (data.error_messages && data.error_messages.length ? data.error_messages[0] : null)
      || (data.errors && data.errors.length ? data.errors[0] : null);
  }
  return {
    valid,
    primary_error,
    error_messages: data.error_messages || [],
  };
}

function cacheTargetValidation(cacheKey, validation) {
  valid_target_cache[cacheKey] = validation;
}

function getCachedTargetValidation(cacheKey) {
  return Object.prototype.hasOwnProperty.call(valid_target_cache, cacheKey)
    ? valid_target_cache[cacheKey]
    : null;
}

function showTargetValidationHint(message) {
  const $hint = $('#target-validation-hint');
  if (!$hint.length) {
    return;
  }
  if (message) {
    $hint.text(message).addClass('is-visible');
  } else {
    $hint.removeClass('is-visible').text('');
  }
}

function hideTargetValidationHint() {
  showTargetValidationHint(null);
}

function formatTargetInvalidTooltip(reason) {
  if (!reason) {
    return '';
  }
  const escaped = $('<div>').text(reason).html();
  return `<p class="target-invalid-reason"><span style="color: #ffb74d;">${escaped}</span></p>`;
}

function updateCoordsBox(showX, showY, tooltip) {
  $("#coords-box").html(`<p>X: ${showX}</p><p>Y: ${showY}</p>${tooltip || ''}`);
}

function blockInvalidTargetClick(cacheKey, e) {
  const cached = getCachedTargetValidation(cacheKey);
  if (cached && cached.valid === false) {
    showTargetValidationHint(cached.primary_error || 'Invalid target');
    if (e) {
      e.stopPropagation();
    }
    return true;
  }
  return false;
}

function exitTargetMode() {
  targetMode = false;
  coneMode = false;
  aoePointTargetMode = false;
  valid_target_cache = {};
  hideTargetValidationHint();
  setStackTargetPointerEvents(false);
  document.body.classList.remove('aoe-point-targeting');
  if (globalCtx && globalCanvas) {
    globalCtx.clearRect(0, 0, globalCanvas.width, globalCanvas.height);
  }
  $(".tile").css("border", "none");
}

function enterAoePointTargeting() {
  aoePointTargetMode = true;
  document.body.classList.add('aoe-point-targeting');
}

function targetDistanceFeet(sourceX, sourceY, coordsx, coordsy) {
  if (aoePointTargetMode || coneMode) {
    return Math.max(Math.abs(sourceX - coordsx), Math.abs(sourceY - coordsy)) * 5;
  }
  return Math.floor(Utils.euclideanDistance(sourceX, sourceY, coordsx, coordsy)) * 5;
}

// --- In-flight /path request coalescing -------------------------------------
// When multiple callers request the same from→to path simultaneously, share a
// single XHR instead of firing duplicate network requests.  Key format:
//   "fromX,fromY→toX,toY"
// Values are jqXHR objects.  Callers must check the returned XHR before
// attaching success/error handlers — the coalesced XHR is returned directly.
const _pathRequestCoalesce = new Map();

let move_path_cache = {};
let move_path_cache_keys = []; // insertion order for bounded LRU eviction
const MOVE_PATH_CACHE_LIMIT = 256;
const HIDE_PREVIEW_CACHE_TTL_MS = 2500;
const hidePreviewCache = new Map(); // entity_uid -> { ts, spots }
let hidePreviewHoverTimer = null;

function clearHidePreviewCache() {
  hidePreviewCache.clear();
}

function setMovePathCache(key, value) {
  if (!Object.prototype.hasOwnProperty.call(move_path_cache, key)) {
    move_path_cache_keys.push(key);
    if (move_path_cache_keys.length > MOVE_PATH_CACHE_LIMIT) {
      const oldest = move_path_cache_keys.shift();
      delete move_path_cache[oldest];
    }
  }
  move_path_cache[key] = value;
}
function clearMovePathCache() {
  move_path_cache = {};
  move_path_cache_keys = [];
  invalidateLocalPathCostMap();
  if (typeof Utils !== 'undefined' && typeof Utils.invalidateMovementGridCache === 'function') {
    Utils.invalidateMovementGridCache();
  }
}

function invalidateMovementPlanningCaches() {
  move_path_cache = {};
  move_path_cache_keys = [];
  clearHidePreviewCache();
  invalidateLocalPathCostMap();
  if (typeof Utils !== 'undefined' && typeof Utils.invalidateMovementGridCache === 'function') {
    Utils.invalidateMovementGridCache();
  }
}

/** Precomputed passability grid for client-side A* (path_compute.js). */
let localPathCostMap = null;
let localPathMovementBudget = null;
let localPathCostMapFromSnapshot = false;
let localPathCanMove = true; // Whether the POV entity is physically able to move

function invalidateLocalPathCostMap() {
  localPathCostMap = null;
  localPathMovementBudget = null;
  localPathCostMapFromSnapshot = false;
  localPathCanMove = true;
}

function loadLocalPathCostMap(fromCoords, done) {
  if (typeof PathComputeModule === 'undefined') {
    if (done) done(false);
    return;
  }
  Utils.ajaxGet(
    '/path/cost_map',
    { 'from[x]': fromCoords.x, 'from[y]': fromCoords.y },
    (data) => {
      if (data && data.snapshot) {
        localPathCostMap = PathComputeModule.PathfindingCostMap.fromSnapshot(data.snapshot);
        localPathMovementBudget = data.available_movement;
        localPathCostMapFromSnapshot = true;
        // Track whether the entity can physically move (not unconscious, has movement)
        localPathCanMove = data.can_move !== false;
        if (done) done(true);
        return;
      }
      invalidateLocalPathCostMap();
      if (done) done(false);
    }
  );
}

function tryBuildLocalPathCostMapFromTiles() {
  if (typeof PathComputeModule === 'undefined') {
    return false;
  }
  let tiles = document.querySelectorAll('.tile');
  const stackOff = Utils.getStackCompositeOffset && Utils.getStackCompositeOffset();
  if (stackOff && stackOff.active) {
    const overlayTiles = document.querySelectorAll('.map-stack-overlay .map-stack-tile');
    if (overlayTiles.length) {
      tiles = overlayTiles;
    }
  }
  if (!tiles.length) {
    return false;
  }
  const ent = document.body.getAttribute('data-pov-entity');
  const prone = document.body.getAttribute('data-pov-prone') === 'true';
  const flying = document.body.getAttribute('data-pov-flying') === 'true';
  localPathCostMap = PathComputeModule.PathfindingCostMap.fromTiles(tiles, {
    entity: { prone, flying },
    feet_per_grid: 5,
  });
  localPathCostMapFromSnapshot = false;
  return !!localPathCostMap;
}

function computeLocalPathResponse(from, to, accumulatedPath) {
  // Disable local preview when the entity cannot move (unconscious / speed 0).
  if (localPathCanMove === false || localPathMovementBudget === 0) {
    return null;
  }
  // In battle, only trust backend-generated snapshots because they encode
  // opposing-entity occupancy; tile-derived fallback can miss blockers.
  if (isBattleInProgress() && (!localPathCostMap || !localPathCostMapFromSnapshot)) {
    return null;
  }
  if (!localPathCostMap && !tryBuildLocalPathCostMapFromTiles()) {
    return null;
  }
  if (typeof PathComputeModule === 'undefined') {
    return null;
  }
  const pc = new PathComputeModule.PathCompute(localPathCostMap);
  const accumulated = accumulatedPath || [];
  const opts = {
    accumulated_path: accumulated,
    available_movement_cost: localPathMovementBudget,
  };
  let path = pc.computePath(from.x, from.y, to.x, to.y, opts);
  const reached =
    path &&
    path.length > 0 &&
    path[path.length - 1][0] === to.x &&
    path[path.length - 1][1] === to.y;
  if (!reached) {
    path = pc.computePath(from.x, from.y, to.x, to.y, Object.assign({}, opts, { door_navigation: true }));
  }
  if (!path) {
    return null;
  }
  const cm = localPathCostMap;
  const feetPer = cm.feetPerGrid || 5;
  const terrain_info = path.map(([x, y]) => ({
    x,
    y,
    difficult: !!cm.difficult[cm._idx(x, y)],
  }));
  // Path cost accounting must use integer GRID units to match server
  // Movement.to_dict() and processMovementData's
  // `(original_budget - budget) * 5` display math. Do not use
  // `_baseMoveCost(..., src)` — that adds a 0.1 diagonal A* nudge which
  // leaks into the "XXft" label as trailing decimals.
  let spentGrids = 0;
  for (let i = 1; i < path.length; i++) {
    spentGrids += Math.floor(pc._baseMoveCost(path[i][0], path[i][1]));
  }
  let originalBudgetGrids;
  let remainingBudgetGrids;
  if (localPathMovementBudget == null) {
    // Exploration: uncapped pathfinding (same as server available_movement=null).
    originalBudgetGrids = spentGrids;
    remainingBudgetGrids = 0;
  } else {
    originalBudgetGrids = Math.round(Number(localPathMovementBudget) / feetPer);
    remainingBudgetGrids = originalBudgetGrids - spentGrids;
  }
  return {
    path,
    cost: {
      budget: remainingBudgetGrids,
      original_budget: originalBudgetGrids,
    },
    placeable: true,
    terrain_info,
    can_move: true,
  };
}
let currentPathXhr = null;     // in-flight /path jqXHR, aborted on new hover
let lastHoverCoords = null;    // last (x,y) processed by movement-mode hover
let currentPosition = null;
let accumulatedPath = [];
let pivotPoints = [];
let moveMode = false,
  targetMode = false,
  coneMode = false,
  aoePointTargetMode = false,
  multiTargetMode = false,
  multiTargetModeUnique = false;
let movePath = [],
  multiTargetList = [];
// Jump UI state for movement
let jumpMode = false;          // toggled within movement to mark a jump segment
let jumpStartIndex = null;     // index within movePath where jump starts (inclusive)
let lastPathForJump = null;    // cache the last data.path returned while hovering
let ctrlHeldForDescent = false; // hold Ctrl to pathfind/jump to a lower floor
let lastStackDescent = null;   // last /path stack_descent preview payload
let max_targets = 1;
let targetModeCallback = null,
  moveModeCallback = null;
let targetModeMaxRange = 0,
  actionPostTimestamp = 0; // Monotonic timestamp of the last /action POST for cooldown tracking.
  source = null,
  battle_setup = false;
let globalActionInfo = null,
  globalOpts = null,
  globalSourceEntity = null;
let pathDebounceTimer = null;

function resetMouseMoveMode(options = {}) {
  const clearCanvas = options.clearCanvas !== false;
  moveMode = false;
  source = null;
  accumulatedPath = [];
  pivotPoints = [];
  currentPosition = null;
  lastHoverCoords = null;
  movePath = [];
  moveModeCallback = null;
  lastPathForJump = null;
  lastStackDescent = null;
  jumpMode = false;
  jumpStartIndex = null;
  if (pathDebounceTimer) {
    try { clearTimeout(pathDebounceTimer); } catch (_) { }
    pathDebounceTimer = null;
  }
  clearMovePathCache();
  if (currentPathXhr && typeof currentPathXhr.abort === 'function') {
    try { currentPathXhr.abort(); } catch (_) { }
  }
  currentPathXhr = null;
  if (clearCanvas && globalCtx && globalCanvas) {
    globalCtx.clearRect(0, 0, globalCanvas.width, globalCanvas.height);
  }
  try { $(".tile").css("border", "none"); } catch (_) { }
}

const ACTION_BAR_STORAGE_PREFIX = 'natural20.actionBarHotkeys';
const ACTION_BAR_PREFS_PREFIX = 'natural20.actionBarPrefs';
const ACTION_BAR_BUTTON_ORDER_PREFIX = 'natural20.actionBarButtonOrder';
let actionBarState = {
  visible: false,
  bindingMode: false,
  customizeMode: false,
  expanded: false,
  pendingBinding: null,
  activeEntityUid: null,
  activeCoords: null,
};

function isTypingIntoField(target) {
  const $target = $(target);
  return (
    $target.is('input, textarea, select, [contenteditable="true"]') ||
    $target.closest(
      '.modal, #jrpgDialogPanel, #command-form, #ai-chat-panel, #chat-form, #draggable-chat-form, #notebook-panel'
    ).length > 0
  );
}

function shouldSuppressSoundtrackOnClick(target) {
  if (isTypingIntoField(target) || isTypingIntoField(document.activeElement)) {
    return true;
  }
  const $target = $(target);
  if (!$target.length) {
    return false;
  }
  return $target.closest(
    '#ai-chat-panel, #toggle-ai-chat, #notebook-panel, #console-container, .modal, ' +
    '#jrpgDialogPanel, #narration-overlay, #command-form, .draggable-panel, ' +
    '#local-conversation-panel, .chat-interface, .chat-transcript-actions'
  ).length > 0;
}

function stackDescentModeActive() {
  return !!(jumpMode || ctrlHeldForDescent);
}

function stackPathRequestExtras(destX, destY) {
  if (!stackDescentModeActive()) {
    return {};
  }
  const extras = { allow_stack_descent: 1 };
  const meta = document.querySelector('.map-metadata[data-stack-composite="1"]');
  const baseMap = meta && meta.getAttribute('data-stack-base-map');
  if (baseMap) {
    extras.to_map = baseMap;
  }
  if (destX != null && destY != null) {
    extras['to[x]'] = destX;
    extras['to[y]'] = destY;
  }
  return extras;
}

function resolveStackMovementDest($tile) {
  const localX = Number($tile.data('coords-x'));
  const localY = Number($tile.data('coords-y'));
  const inOverlay = $tile.closest('.map-stack-overlay').length > 0;
  const isPeek = $tile.hasClass('peek-through') || $tile.hasClass('map-stack-underlay-tile');
  const descentX = $tile.data('descent-target-x');
  const descentY = $tile.data('descent-target-y');
  const worldX = $tile.data('world-x');
  const worldY = $tile.data('world-y');

  if (stackDescentModeActive() && (isPeek || !inOverlay)) {
    if (descentX !== undefined && descentY !== undefined) {
      return { x: Number(descentX), y: Number(descentY), localX, localY };
    }
    if (!inOverlay || $tile.hasClass('map-stack-underlay-tile')) {
      const wx = worldX !== undefined ? Number(worldX) : localX;
      const wy = worldY !== undefined ? Number(worldY) : localY;
      return { x: wx, y: wy, localX, localY };
    }
  }
  return { x: localX, y: localY, localX, localY };
}

function resolveStackTargetCoords($tile) {
  const localX = Number($tile.data('coords-x'));
  const localY = Number($tile.data('coords-y'));
  const isUnderlay = $tile.hasClass('map-stack-underlay-tile');
  const isPeek = $tile.hasClass('peek-through') || isUnderlay;
  const peekX = $tile.data('descent-target-x');
  const peekY = $tile.data('descent-target-y');
  const worldX = $tile.data('world-x');
  const worldY = $tile.data('world-y');

  if (isUnderlay && worldX !== undefined && worldY !== undefined) {
    return { x: Number(worldX), y: Number(worldY), localX, localY };
  }
  if (isPeek && peekX !== undefined && peekY !== undefined) {
    return { x: Number(peekX), y: Number(peekY), localX, localY };
  }
  return { x: localX, y: localY, localX, localY };
}

/** Map-local coords for movement/path APIs (not peek-through descent targets). */
function resolveStackPathCoords($tile) {
  if (Utils.pathCoordsFromTile) {
    const coords = Utils.pathCoordsFromTile($tile);
    if (coords) {
      return {
        x: coords.x,
        y: coords.y,
        localX: coords.x,
        localY: coords.y,
      };
    }
  }
  return resolveStackMovementDest($tile);
}

function getEntityTile(entityUid) {
  if (Utils.getEntityTile) {
    return Utils.getEntityTile(entityUid);
  }
  const $tile = $(`.tile[data-coords-id="${entityUid}"]`);
  if ($tile.length) {
    return $tile.first();
  }
  return $(`.entity[data-id="${entityUid}"]`).closest('.tile');
}

let objectQuickInteractInFlight = false;
let teleporterApproachInFlight = false;
let talkApproachInFlight = false;
let moveQuickActionPathRequest = null;
let moveQuickActionHoverRequestToken = 0;
let moveQuickActionPreviewActive = false;
let moveQuickActionInFlight = false;

function isBattleInProgress() {
  return String($('body').attr('data-battle_in_progress') || '').toLowerCase() === 'true';
}

function clearMoveQuickActionPreview() {
  moveQuickActionPreviewActive = false;
  if (
    !moveMode
    && !targetMode
    && !multiTargetMode
    && !coneMode
    && !aoePointTargetMode
    && !keyboardMovementMode
    && globalCtx
    && globalCanvas
  ) {
    globalCtx.clearRect(0, 0, globalCanvas.width, globalCanvas.height);
  }
}

function hideMoveQuickActionLayer($tile) {
  if (!$tile || !$tile.length) {
    return;
  }
  $tile.find('.move-quick-action-layer').remove();
}

function cancelMoveQuickActionPathRequest() {
  if (moveQuickActionPathRequest && typeof moveQuickActionPathRequest.abort === 'function') {
    try { moveQuickActionPathRequest.abort(); } catch (_) { /* ignore */ }
  }
  moveQuickActionPathRequest = null;
}

function isTileOccupiedForMoveQuickAction($tile) {
  if (!$tile || !$tile.length) {
    return true;
  }
  const primaryId = $tile.data('coords-id');
  if (primaryId) {
    return true;
  }
  return $tile.find('.entity[data-id], .npc[data-id]').length > 0;
}

function shouldEnableMoveQuickAction() {
  if (isInteractionLocked()) {
    return false;
  }
  if (forcedConversationMode) {
    return false;
  }
  // Disable move quick actions during battle (initiative phase, turn-based movement)
  if ($("body").data("battle_in_progress")) {
    return false;
  }
  if (moveMode || targetMode || multiTargetMode || coneMode || aoePointTargetMode || keyboardMovementMode) {
    return false;
  }
  // Disable movement when the entity is unconscious or physically unable to move
  if (localPathCanMove === false) {
    return false;
  }
  return true;
}

function getMoveQuickActionPovUid() {
  return $('body').attr('data-pov-entity')
    || (typeof Chat !== 'undefined' && Chat.getCurrentPovEntity && Chat.getCurrentPovEntity())
    || null;
}

function resolveMoveQuickActionSourceCoords(povUid) {
  if (!povUid) {
    return null;
  }
  const $srcTile = getEntityTile(povUid);
  if (!$srcTile.length) {
    return null;
  }
  const sourceCoords = Utils.pathCoordsFromTile
    ? Utils.pathCoordsFromTile($srcTile)
    : { x: Number($srcTile.data('coords-x')), y: Number($srcTile.data('coords-y')) };
  if (!sourceCoords || !Number.isFinite(sourceCoords.x) || !Number.isFinite(sourceCoords.y)) {
    return null;
  }
  return sourceCoords;
}

function resolveMoveQuickActionPathToTile($tile, done) {
  if (!$tile || !$tile.length) {
    done(null);
    return;
  }

  const povUid = getMoveQuickActionPovUid();
  const from = resolveMoveQuickActionSourceCoords(povUid);
  const to = resolveStackPathCoords($tile);
  if (!from || !to || !Number.isFinite(to.x) || !Number.isFinite(to.y)) {
    done(null);
    return;
  }
  if (from.x === to.x && from.y === to.y) {
    done(null);
    return;
  }

  const cacheKey = `quick-${povUid}-${from.x}-${from.y}-${to.x}-${to.y}-${stackDescentModeActive() ? 'sd' : 'n'}`;
  if (move_path_cache[cacheKey]) {
    done(move_path_cache[cacheKey]);
    return;
  }

  const localData = computeLocalPathResponse(from, to, []);
  if (localData && localData.path && localData.placeable && pathReachesTile(localData.path, to.x, to.y)) {
    setMovePathCache(cacheKey, localData);
    done(localData);
    return;
  }

  cancelMoveQuickActionPathRequest();
  moveQuickActionPathRequest = $.ajax({
    url: '/path',
    type: 'GET',
    data: Object.assign({
      from,
      to,
      accumulatedPath: null,
    }, stackPathRequestExtras(to.x, to.y)),
    success: (data) => {
      moveQuickActionPathRequest = null;
      if (data && data.path && data.placeable !== false && pathReachesTile(data.path, to.x, to.y)) {
        setMovePathCache(cacheKey, data);
        done(data);
        return;
      }
      done(null);
    },
    error: (jqXHR, textStatus) => {
      moveQuickActionPathRequest = null;
      if (textStatus !== 'abort') {
        done(null);
      }
    },
  });
}

function ensureMoveQuickActionLayer($tile) {
  let $layer = $tile.find('.move-quick-action-layer');
  if ($layer.length) {
    return $layer;
  }

  $layer = $(
    '<div class="move-quick-action-layer" aria-hidden="false">'
    + '<button type="button" class="move-quick-action" title="Move here" aria-label="Move here">'
    + '<i class="fas fa-location-arrow" aria-hidden="true"></i>'
    + '</button>'
    + '</div>'
  );
  $layer.appendTo($tile);
  return $layer;
}

function renderMoveQuickAction($tile) {
  if (!$tile || !$tile.length || !$tile.is(':hover')) {
    return;
  }
  if (!shouldEnableMoveQuickAction()) {
    hideMoveQuickActionLayer($tile);
    return;
  }
  if (isTileOccupiedForMoveQuickAction($tile)) {
    hideMoveQuickActionLayer($tile);
    return;
  }

  const currentToken = ++moveQuickActionHoverRequestToken;
  resolveMoveQuickActionPathToTile($tile, (data) => {
    if (currentToken !== moveQuickActionHoverRequestToken) {
      return;
    }
    if (!$tile.is(':hover')) {
      return;
    }
    if (!data || !Array.isArray(data.path) || data.path.length <= 1) {
      hideMoveQuickActionLayer($tile);
      return;
    }
    ensureMoveQuickActionLayer($tile);
  });
}

function executeMoveQuickAction(btn) {
  const $btn = $(btn);
  if (!$btn.length || moveQuickActionInFlight) {
    return;
  }

  const povUid = getMoveQuickActionPovUid();
  if (!povUid) {
    if (typeof Utils !== 'undefined' && Utils.showToast) {
      Utils.showToast('Select a character first.');
    }
    return;
  }

  const $tile = $btn.closest('.tile');
  if (!$tile.length) {
    return;
  }

  moveQuickActionInFlight = true;
  $btn.prop('disabled', true);
  const finish = () => {
    moveQuickActionInFlight = false;
    $btn.prop('disabled', false);
  };

  resolveMoveQuickActionPathToTile($tile, (data) => {
    if (!data || !Array.isArray(data.path) || data.path.length <= 1) {
      if (typeof Utils !== 'undefined' && Utils.showToast) {
        Utils.showToast('Cannot move to that tile from here.');
      }
      finish();
      return;
    }
    ajaxPost(
      '/action',
      {
        id: povUid,
        action: 'MoveAction',
        opts: { action_type: 'move', source: povUid },
        path: data.path,
      },
      (moveData) => {
        noteMoveActionCompleted(povUid, moveData);
        if (!moveData || moveData.status !== 'ok') {
          if (typeof Utils !== 'undefined' && Utils.showToast) {
            Utils.showToast('Could not move to that tile.');
          }
        }
        hideMoveQuickActionLayer($tile);
        clearMoveQuickActionPreview();
        finish();
      },
      true,
    );
  });
}

function neighborApproachCoords(objectX, objectY) {
  return [
    { x: objectX - 1, y: objectY },
    { x: objectX + 1, y: objectY },
    { x: objectX, y: objectY - 1 },
    { x: objectX, y: objectY + 1 },
  ];
}

const DOOR_APPROACH_ANCHOR_OFFSETS = {
  top: { x: 0, y: -1 },
  bottom: { x: 0, y: 1 },
  left: { x: -1, y: 0 },
  right: { x: 1, y: 0 },
};

function doorApproachCoordsFromButton(btn) {
  const layer = btn && btn.closest ? btn.closest('.object-quick-interact-layer') : null;
  if (!layer) {
    return null;
  }
  const raw = layer.getAttribute('data-door-approach') || '';
  const anchors = raw.split(',').map((part) => part.trim()).filter(Boolean);
  if (!anchors.length) {
    return null;
  }
  const tile = btn.closest('.tile');
  if (!tile) {
    return null;
  }
  const objectX = Number(tile.getAttribute('data-coords-x') ?? tile.dataset.coordsX);
  const objectY = Number(tile.getAttribute('data-coords-y') ?? tile.dataset.coordsY);
  if (!Number.isFinite(objectX) || !Number.isFinite(objectY)) {
    return null;
  }
  return anchors.map((anchor) => {
    const offset = DOOR_APPROACH_ANCHOR_OFFSETS[anchor] || { x: 0, y: 0 };
    return { x: objectX + offset.x, y: objectY + offset.y };
  });
}

function talkApproachCoords(npcX, npcY) {
  const coords = [];
  for (let dx = -2; dx <= 2; dx += 1) {
    for (let dy = -2; dy <= 2; dy += 1) {
      if (dx === 0 && dy === 0) {
        continue;
      }
      const dist = Math.sqrt((dx * dx) + (dy * dy));
      if (dist <= 2) {
        coords.push({ x: npcX + dx, y: npcY + dy });
      }
    }
  }
  return coords;
}

function entityTileCoords(entityUid) {
  const $tile = getEntityTile(entityUid);
  if (!$tile.length) {
    return null;
  }
  const x = Number($tile.data('coords-x'));
  const y = Number($tile.data('coords-y'));
  if (!Number.isFinite(x) || !Number.isFinite(y)) {
    return null;
  }
  return { x, y };
}

function tileCoordsWithinTalkDistance(a, b) {
  if (!a || !b) {
    return false;
  }
  const dist = Math.sqrt(((a.x - b.x) ** 2) + ((a.y - b.y) ** 2));
  return dist <= 2;
}

function isWithinTalkTileDistance(povUid, npcUid) {
  const povCoords = entityTileCoords(povUid);
  const npcCoords = entityTileCoords(npcUid);
  return tileCoordsWithinTalkDistance(povCoords, npcCoords);
}

function pathReachesTile(path, x, y) {
  if (!Array.isArray(path) || !path.length) {
    return false;
  }
  const last = path[path.length - 1];
  return last[0] === x && last[1] === y;
}

function fetchPathToTile(from, to, callback) {
  const localData = computeLocalPathResponse(from, to, []);
  if (localData && localData.path && localData.placeable && pathReachesTile(localData.path, to.x, to.y)) {
    callback(localData.path);
    return;
  }
  // Coalesce duplicate in-flight requests: build a key from from/to coordinates
  // so that simultaneous callers share a single XHR.
  const coalesceKey = `${from.x},${from.y}→${to.x},${to.y}`;
  const existing = _pathRequestCoalesce.get(coalesceKey);
  if (existing && typeof existing.done === 'function') {
    // Share the in-flight XHR; attach this caller's callbacks.
    const sharedXhr = existing;
    sharedXhr
      .done((data) => {
        if (data && data.path && data.placeable !== false && pathReachesTile(data.path, to.x, to.y)) {
          callback(data.path);
        } else {
          callback(null);
        }
      })
      .fail(() => callback(null));
    return;
  }

  const xhr = $.ajax({
    url: '/path',
    type: 'GET',
    data: Object.assign({
      from,
      to,
      accumulatedPath: null,
    }, stackPathRequestExtras(to.x, to.y)),
    success: (data) => {
      _pathRequestCoalesce.delete(coalesceKey);
      if (data && data.path && data.placeable !== false && pathReachesTile(data.path, to.x, to.y)) {
        callback(data.path);
      } else {
        callback(null);
      }
    },
    error: () => {
      _pathRequestCoalesce.delete(coalesceKey);
      callback(null);
    },
  });
  _pathRequestCoalesce.set(coalesceKey, xhr);
}

function findApproachPathToCoords(povUid, candidates, callback) {
  const $povTile = getEntityTile(povUid);
  if (!$povTile.length) {
    callback(null);
    return;
  }
  const from = Utils.pathCoordsFromTile
    ? Utils.pathCoordsFromTile($povTile)
    : { x: Number($povTile.data('coords-x')), y: Number($povTile.data('coords-y')) };
  if (!Number.isFinite(from.x) || !Number.isFinite(from.y)) {
    callback(null);
    return;
  }

  let bestPath = null;
  let settled = false;       // true once we've called the callback
  const pending = { value: candidates.length };
  const xhrs = [];           // track in-flight XHRs so we can abort them

  // Abort all pending in-flight requests.
  const abortAll = () => {
    for (const x of xhrs) {
      try { x.abort(); } catch (_) { /* ignore */ }
    }
    xhrs.length = 0;
  };

  if (!pending.value) {
    callback(null);
    return;
  }

  candidates.forEach((dest) => {
    if (settled) return;        // skip enqueuing more requests once we have a path

    fetchPathToTile(from, dest, (path) => {
      if (settled) return;      // another candidate already won

      if (path && (!bestPath || path.length < bestPath.length)) {
        bestPath = path;
        // First valid path found — abort all remaining requests to save CPU.
        abortAll();
        settled = true;
        callback(bestPath);
        return;
      }

      pending.value -= 1;
      if (pending.value === 0 && !settled) {
        settled = true;
        callback(bestPath);
      }
    });
  });
}

function findApproachPathForObject(povUid, objectX, objectY, callback, btn) {
  const doorCoords = btn ? doorApproachCoordsFromButton(btn) : null;
  const candidates = doorCoords && doorCoords.length
    ? doorCoords
    : neighborApproachCoords(objectX, objectY);
  findApproachPathToCoords(povUid, candidates, callback);
}

function findApproachPathForTalk(povUid, npcX, npcY, callback) {
  findApproachPathToCoords(povUid, talkApproachCoords(npcX, npcY), callback);
}

function findApproachPathForTeleporter(povUid, teleporterX, teleporterY, callback) {
  findApproachPathToCoords(povUid, [{ x: teleporterX, y: teleporterY }], callback);
}

function appendTeleporterHoverHint($tile, tooltip) {
  const $tpMarker = $tile.find('.teleporter-marker[data-destination]').first();
  if (!$tpMarker.length) {
    return tooltip;
  }
  const dest = $tpMarker.attr('data-destination') || '';
  const color = 'var(--teleporter-marker-color, #22c55e)';
  if (dest) {
    tooltip += `<p class="teleporter-destination-hint"><span style="color: ${color};">→ ${dest}</span></p>`;
  }
  if ($tpMarker.attr('data-party-travel') === '1') {
    tooltip += `<p class="teleporter-destination-hint">Interact to travel with the party</p>`;
  }
  return tooltip;
}

function runTeleporterApproach(povUid, tileX, tileY) {
  if (!povUid || teleporterApproachInFlight || isBattleInProgress() || isInteractionLocked()) {
    return;
  }

  const povCoords = entityTileCoords(povUid);
  if (povCoords && povCoords.x === tileX && povCoords.y === tileY) {
    return;
  }

  teleporterApproachInFlight = true;
  const finish = () => {
    teleporterApproachInFlight = false;
  };

  findApproachPathForTeleporter(povUid, tileX, tileY, (path) => {
    if (!path || path.length <= 1) {
      if (typeof Utils !== 'undefined' && Utils.showToast) {
        Utils.showToast('Cannot reach that teleporter from here.');
      }
      finish();
      return;
    }
    ajaxPost(
      '/action',
      {
        id: povUid,
        action: 'MoveAction',
        opts: { action_type: 'move', source: povUid },
        path,
      },
      (moveData) => {
        noteMoveActionCompleted(povUid, moveData);
        if (moveData && moveData.status === 'ok') {
          finish();
          return;
        }
        if (typeof Utils !== 'undefined' && Utils.showToast) {
          Utils.showToast('Could not move to teleporter.');
        }
        finish();
      },
      true,
    );
  });
}

function postObjectInteract(povUid, objectUid, objectAction, tileX, tileY, done) {
  const opts = {
    action_type: 'interact',
    target: objectUid,
    object_action: objectAction,
  };
  $.ajax({
    url: '/action',
    type: 'POST',
    data: JSON.stringify({ id: povUid, action: 'InteractAction', opts }),
    contentType: 'application/json',
    success: (data) => {
      if (data && data.status === 'ok') {
        actionPostTimestamp = Date.now();
        try { refreshTurn(); } catch (_) { }
        try { scheduleRefreshMap(80); } catch (_) { }
        if (typeof done === 'function') {
          done();
        }
        return;
      }
      handleAction(povUid, 'InteractAction', opts, tileX, tileY, data);
      if (typeof done === 'function') {
        done();
      }
    },
    error: () => {
      if (typeof Utils !== 'undefined' && Utils.showToast) {
        Utils.showToast('Could not interact with that object.');
      }
      if (typeof done === 'function') {
        done();
      }
    },
  });
}

function activateObjectQuickAction(btn) {
  const $btn = $(btn);
  if (!$btn.length || $btn.is(':disabled') || $btn.hasClass('is-disabled')) {
    return;
  }
  const povUid = $('body').attr('data-pov-entity')
    || (typeof Chat !== 'undefined' && Chat.getCurrentPovEntity && Chat.getCurrentPovEntity());
  if (!povUid) {
    if (typeof Utils !== 'undefined' && Utils.showToast) {
      Utils.showToast('Select a character first.');
    }
    return;
  }
  const objectAction = $btn.attr('data-object-action') || $btn.data('objectAction');
  const quickKind = $btn.attr('data-quick-kind') || $btn.data('quickKind');
  if (objectAction === 'perception_check' || quickKind === 'pov_perception') {
    runPovPerceptionQuickAction(povUid);
    return;
  }
  const objectUid = $btn.attr('data-object-id') || $btn.data('objectId');
  const needsApproach = String($btn.attr('data-needs-approach') ?? $btn.data('needsApproach')) === '1';
  const $tile = $btn.closest('.tile');
  const pathCoords = resolveStackPathCoords($tile);
  runObjectQuickInteract(
    povUid,
    objectUid,
    objectAction,
    needsApproach,
    pathCoords.x,
    pathCoords.y,
    btn,
  );
}

function handleObjectQuickActionActivate(event) {
  if (document.body.classList.contains('edit-properties-picking-path')) {
    return;
  }
  const btn = event.target && event.target.closest
    ? event.target.closest('.object-quick-action')
    : null;
  if (!btn) {
    return;
  }
  if (!btn.closest('.tiles-container, .map-stack-overlay-tiles')) {
    return;
  }
  event.preventDefault();
  event.stopPropagation();
  if (typeof event.stopImmediatePropagation === 'function') {
    event.stopImmediatePropagation();
  }
  activateObjectQuickAction(btn);
}

document.addEventListener('click', handleObjectQuickActionActivate, true);

let povPerceptionQuickActionSuppressedFor = null;

function applyPovPerceptionQuickActionVisibility() {
  $('.pov-self-quick-interact-layer').each(function () {
    const $layer = $(this);
    const objectId = $layer.attr('data-object-id');
    const suppress = povPerceptionQuickActionSuppressedFor
      && objectId === povPerceptionQuickActionSuppressedFor;
    $layer.toggleClass('is-suppressed', !!suppress);
  });
}

function suppressPovPerceptionQuickAction(entityUid) {
  if (!entityUid) {
    return;
  }
  povPerceptionQuickActionSuppressedFor = entityUid;
  applyPovPerceptionQuickActionVisibility();
}

function restorePovPerceptionQuickAction(entityUid) {
  if (!entityUid || povPerceptionQuickActionSuppressedFor === entityUid) {
    povPerceptionQuickActionSuppressedFor = null;
    applyPovPerceptionQuickActionVisibility();
  }
}

function noteMoveActionCompleted(entityUid, data) {
  if (entityUid && data && data.status === 'ok') {
    restorePovPerceptionQuickAction(entityUid);
  }
}

function runPovPerceptionQuickAction(povUid) {
  if (!povUid || isInteractionLocked()) {
    return;
  }
  if (typeof perceptionCheckProcessing !== 'undefined' && perceptionCheckProcessing) {
    return;
  }

  suppressPovPerceptionQuickAction(povUid);

  const finishWithNarrative = () => {
    if (typeof window.sendDialogPerceptionCheck === 'function') {
      window.sendDialogPerceptionCheck({ targetId: povUid });
      return;
    }
    if (typeof Utils !== 'undefined' && Utils.showToast) {
      Utils.showToast('Perception check unavailable.');
    }
  };

  ajaxPost(
    '/action',
    { id: povUid, action: 'LookAction', opts: { action_type: 'look' } },
    (data) => {
      if (data && data.status === 'error') {
        finishWithNarrative();
        return;
      }
      if (data && data.status === 'ok' && data.perception_roll != null && typeof playPerceptionCheckFeedback === 'function') {
        playPerceptionCheckFeedback(povUid, data.perception_roll, data.perception_roll_label);
      }
      if (typeof continueActionFromResponse === 'function') {
        continueActionFromResponse(povUid, 'LookAction', { action_type: 'look' }, null, null, data);
      }
      if (typeof refreshTurn === 'function') {
        refreshTurn();
      }
    },
    true,
  );
}

function runObjectQuickInteract(povUid, objectUid, objectAction, needsApproach, tileX, tileY, btn) {
  if (!povUid || !objectUid || !objectAction || objectQuickInteractInFlight) {
    return;
  }
  if (isInteractionLocked()) {
    return;
  }

  const finish = () => {
    objectQuickInteractInFlight = false;
  };

  const executeInteract = () => {
    postObjectInteract(povUid, objectUid, objectAction, tileX, tileY, finish);
  };

  objectQuickInteractInFlight = true;

  if (!needsApproach) {
    executeInteract();
    return;
  }

  findApproachPathForObject(povUid, tileX, tileY, (path) => {
    if (!path || path.length <= 1) {
      if (typeof Utils !== 'undefined' && Utils.showToast) {
        Utils.showToast('Cannot reach that object from here.');
      }
      finish();
      return;
    }
    ajaxPost(
      '/action',
      {
        id: povUid,
        action: 'MoveAction',
        opts: { action_type: 'move', source: povUid },
        path,
      },
      (moveData) => {
        noteMoveActionCompleted(povUid, moveData);
        if (moveData && moveData.status === 'ok') {
          executeInteract();
          return;
        }
        if (typeof Utils !== 'undefined' && Utils.showToast) {
          Utils.showToast('Could not move into range.');
        }
        finish();
      },
      true,
    );
  }, btn || null);
}

function runTalkWithApproach(povUid, npcUid, npcName, options) {
  const opts = options || {};
  if (!povUid || !npcUid || talkApproachInFlight) {
    return;
  }
  if (isInteractionLocked() && !opts.forced && !opts.goalArrival) {
    return;
  }

  const openDialog = () => {
    if (typeof handleDialogBubbleClick === 'function') {
      handleDialogBubbleClick(npcUid, npcName, opts);
    }
  };

  if (opts.forced || opts.goalArrival || isBattleInProgress() || isWithinTalkTileDistance(povUid, npcUid)) {
    openDialog();
    return;
  }

  const npcCoords = entityTileCoords(npcUid);
  if (!npcCoords) {
    openDialog();
    return;
  }

  talkApproachInFlight = true;
  const finish = () => {
    talkApproachInFlight = false;
  };

  findApproachPathForTalk(povUid, npcCoords.x, npcCoords.y, (path) => {
    if (!path || path.length <= 1) {
      if (typeof Utils !== 'undefined' && Utils.showToast) {
        Utils.showToast('Cannot reach that character from here.');
      }
      finish();
      return;
    }
    ajaxPost(
      '/action',
      {
        id: povUid,
        action: 'MoveAction',
        opts: { action_type: 'move', source: povUid },
        path,
      },
      (moveData) => {
        noteMoveActionCompleted(povUid, moveData);
        if (moveData && moveData.status === 'ok') {
          openDialog();
          finish();
          return;
        }
        if (typeof Utils !== 'undefined' && Utils.showToast) {
          Utils.showToast('Could not move into range.');
        }
        finish();
      },
      true,
    );
  });
}

function setStackTargetPointerEvents(active) {
  $('.map-stack-underlay').css('pointer-events', active ? 'auto' : 'none');
  if (active) {
    $('.map-stack-underlay-tile').css('z-index', 2);
    $('.tile.peek-through').css('pointer-events', 'none');
  } else {
    $('.map-stack-underlay-tile').css('z-index', '');
    $('.tile.peek-through').css('pointer-events', '');
  }
}

function movementPathDrawOptions(data) {
  const opts = { suppressArrow: jumpMode };
  if (data && data.stack_descent) {
    opts.stackDescent = data.stack_descent;
    opts.basePath = data.stack_descent.base_path;
  }
  return opts;
}

function setActionBarInstructions(message) {
  $('#centerActionBarInstructions').text(message);
}

function setActionBarExpanded(expanded) {
  actionBarState.expanded = !!expanded;
  const $panel = $('#centerActionBar .center-action-bar-panel');
  const $btn = $('#centerActionBarExpand');
  $panel.toggleClass('is-expanded', actionBarState.expanded);
  $btn.toggleClass('is-expanded', actionBarState.expanded)
    .attr('aria-expanded', actionBarState.expanded ? 'true' : 'false');
  const label = actionBarState.expanded ? 'Collapse action bar' : 'Expand action bar';
  $btn.attr('title', label).attr('aria-label', label);
  $btn.find('.center-action-bar-expand-icon').text(actionBarState.expanded ? '▼' : '▲');
}

function showPickerInCenterActionBar(entityUid, title, html, coordsx, coordsy) {
  const $content = $('#centerActionBarContent');
  const $entry = $('<div class="popover-actions-entry center-action-entry is-active action-picker-entry"></div>')
    .attr('data-entity-uid', entityUid)
    .attr('data-coords-x', coordsx)
    .attr('data-coords-y', coordsy);
  const $header = $('<div class="popover-actions-header"></div>');
  $('<span class="popover-actions-title"></span>').text(title).appendTo($header);
  $entry.append($header);
  const $body = $('<div class="popover-actions-body"></div>').html(html);
  $entry.append($body);
  $content.html($('<div class="popover-actions-stack"></div>').append($entry));
  decorateCenterActionSection($body, { id: entityUid }, coordsx, coordsy);
  $('#centerActionBarTitle').text(title);
  $('#centerActionBarSubtitle').text('Choose an option below.');
  $('#centerActionBar').show();
  actionBarState.visible = true;
  activateCenterActionEntry($entry);
}

function actionBarStorageKey(entityUid) {
  return `${ACTION_BAR_STORAGE_PREFIX}:${entityUid || 'global'}`;
}

function loadActionBarBindings(entityUid) {
  try {
    const raw = window.localStorage.getItem(actionBarStorageKey(entityUid));
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch (_error) {
    return {};
  }
}

function saveActionBarBindings(entityUid, bindings) {
  try {
    window.localStorage.setItem(actionBarStorageKey(entityUid), JSON.stringify(bindings || {}));
  } catch (_error) {
    // ignore storage errors
  }
}

function currentActionBarUsername() {
  return ($('body').data('username') || 'player').toString();
}

function actionBarPrefsKey(entityUid) {
  return `${ACTION_BAR_PREFS_PREFIX}:${currentActionBarUsername()}:${entityUid || 'global'}`;
}

function loadActionBarPrefs(entityUid) {
  try {
    const raw = window.localStorage.getItem(actionBarPrefsKey(entityUid));
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch (_error) {
    return {};
  }
}

function saveActionBarPrefs(entityUid, prefs) {
  try {
    window.localStorage.setItem(actionBarPrefsKey(entityUid), JSON.stringify(prefs || {}));
  } catch (_error) {
    // ignore storage errors
  }
}

// --- Action bar button drag-and-drop reorder ---

function actionBarButtonOrderKey() {
  return `${ACTION_BAR_BUTTON_ORDER_PREFIX}:${currentActionBarUsername()}`;
}

function loadActionBarButtonOrder() {
  try {
    const raw = window.localStorage.getItem(actionBarButtonOrderKey());
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch (_error) {
    return {};
  }
}

function saveActionBarButtonOrder(order) {
  try {
    window.localStorage.setItem(actionBarButtonOrderKey(), JSON.stringify(order || {}));
  } catch (_error) {
    // ignore storage errors
  }
}

function applyActionBarButtonOrder($tabs) {
  const order = loadActionBarButtonOrder();
  if (!Object.keys(order).length) {
    return;
  }
  // Reorder cells within each panel according to saved order
  Object.entries(order).forEach(([tabId, cellOrder]) => {
    const $panel = $tabs.find(`.action-bar-tab-panel[data-tab="${tabId}"]`);
    if (!$panel.length || !Array.isArray(cellOrder) || !cellOrder.length) return;
    const $rows = $panel.find('tr');
    cellOrder.forEach((descriptor) => {
      const $cell = $panel.find(`[data-action-descriptor="${CSS.escape(descriptor)}"]`).first();
      if ($cell.length) {
        // Find the parent row and append the cell there
        const $parentRow = $cell.closest('tr');
        if ($parentRow.length) {
          $parentRow.append($cell);
        } else {
          // If no parent row, find the last table or append to panel-inner
          const $lastTable = $panel.find('table').last();
          if ($lastTable.length) {
            let $lastRow = $lastTable.find('tr').last();
            if (!$lastRow.length) {
              $lastRow = $('<tr>').appendTo($lastTable);
            }
            $lastRow.append($cell);
          } else {
            $panel.find('.action-bar-tab-panel-inner, .action-bar-tab-panel').first().append($cell.closest('tr, td'));
          }
        }
      }
    });
  });
}

function initActionBarButtonDragDrop($tabs) {
  if (!$tabs || !$tabs.length) return;
  const $panelInner = $tabs.find('.action-bar-tab-panel-inner, .action-bar-tab-panel').first();
  
  $panelInner.find('.action-bar-action-cell').each(function () {
    const $cell = $(this);
    if ($cell.hasClass('action-bar-knockout-cell')) return;
    if ($cell.data('draggable-initialized')) return;
    $cell.data('draggable-initialized', true);
    
    $cell.attr('draggable', 'true');
    
    $cell.on('dragstart', function (e) {
      if (!actionBarState.customizeMode) {
        e.preventDefault();
        return;
      }
      const descriptor = $cell.attr('data-action-descriptor');
      e.originalEvent.dataTransfer.setData('text/plain', descriptor);
      e.originalEvent.dataTransfer.effectAllowed = 'move';
      $cell.addClass('action-bar-dragging');
      setTimeout(() => $cell.css('opacity', '0.4'), 0);
    });
    
    $cell.on('dragend', function () {
      $cell.removeClass('action-bar-dragging').css('opacity', '');
      $tabs.find('.action-bar-action-cell').removeClass('action-bar-drop-target');
    });
  });
  
  // Allow dropping on rows within the panel
  $panelInner.on('dragover', 'tr', function (e) {
    if (!actionBarState.customizeMode) return;
    e.preventDefault();
    e.originalEvent.dataTransfer.dropEffect = 'move';
    $(this).addClass('action-bar-drop-row');
  });
  
  $panelInner.on('dragleave', 'tr', function () {
    $(this).removeClass('action-bar-drop-row');
  });
  
  $panelInner.on('drop', 'tr', function (e) {
    if (!actionBarState.customizeMode) return;
    e.preventDefault();
    e.stopPropagation();
    $(this).removeClass('action-bar-drop-row');
    
    const descriptor = e.originalEvent.dataTransfer.getData('text/plain');
    if (!descriptor) return;
    
    const $targetRow = $(this);
    const $dragCell = $tabs.find(`[data-action-descriptor="${CSS.escape(descriptor)}"]`).first();
    if (!$dragCell.length) return;
    
    // Determine insert position
    const $cellsInRow = $targetRow.find('.action-bar-action-cell').toArray();
    let inserted = false;
    for (const cell of $cellsInRow) {
      const $cell = $(cell);
      const cellDescriptor = $cell.attr('data-action-descriptor');
      if ($cell.before($dragCell)) {
        inserted = true;
        break;
      }
    }
    if (!inserted) {
      $targetRow.append($dragCell);
    }
    
    // Save new order
    saveCurrentActionBarButtonOrder($tabs);
  });
  
  // Also allow dropping directly on action cells
  $panelInner.on('drop', '.action-bar-action-cell', function (e) {
    if (!actionBarState.customizeMode) return;
    e.preventDefault();
    e.stopPropagation();
    
    const descriptor = e.originalEvent.dataTransfer.getData('text/plain');
    if (!descriptor) return;
    
    const $targetCell = $(this);
    const $dragCell = $tabs.find(`[data-action-descriptor="${CSS.escape(descriptor)}"]`).first();
    if (!$dragCell.length || $dragCell.is($targetCell)) return;
    
    $targetCell.before($dragCell);
    saveCurrentActionBarButtonOrder($tabs);
  });
}

function saveCurrentActionBarButtonOrder($tabs) {
  if (!$tabs || !$tabs.length) return;
  const order = {};
  $tabs.find('.action-bar-tab-panel').each(function () {
    const tabId = $(this).attr('data-tab');
    if (!tabId) return;
    const descriptors = [];
    $(this).find('.action-bar-action-cell').each(function () {
      const d = $(this).attr('data-action-descriptor');
      if (d) descriptors.push(d);
    });
    if (descriptors.length) {
      order[tabId] = descriptors;
    }
  });
  saveActionBarButtonOrder(order);
}

function selectActionBarTab($tabs, tabId) {
  if (!$tabs || !$tabs.length || !tabId) {
    return;
  }
  const $buttons = $tabs.find('.action-bar-tab-btn');
  const $panels = $tabs.find('.action-bar-tab-panel');
  const $targetBtn = $buttons.filter(`[data-tab="${tabId}"]`);
  const $targetPanel = $panels.filter(`[data-tab="${tabId}"]`);
  if (!$targetBtn.length || !$targetPanel.length) {
    return;
  }
  $buttons.removeClass('is-active').attr('aria-selected', 'false');
  $panels.removeClass('is-active').attr('hidden', true);
  $targetBtn.addClass('is-active').attr('aria-selected', 'true');
  $targetPanel.addClass('is-active').removeAttr('hidden');
}

function hideEmptyActionBarTabs($tabs) {
  $tabs.find('.action-bar-tab-btn').each(function () {
    const tabId = $(this).data('tab');
    const $panel = $tabs.find(`.action-bar-tab-panel[data-tab="${tabId}"]`);
    const hasActions = $panel.find('.action-button, .action-end-turn, .talk-action, .merchant-shop-action, .jrpg-dialog-action, .set-pov-action, .delete-entity-btn, .action-bar-spell-card').length > 0;
    // Always show these tabs; hide "all" when it has no content
    const alwaysShow = tabId === 'common' || tabId === 'utility';
    const visible = alwaysShow || hasActions || tabId === 'all';
    $(this).toggle(visible);
    if (!visible && tabId !== 'all') {
      $panel.removeClass('is-active').attr('hidden', true);
    }
  });
}

function applyActionBarTabOrder($tabs, tabOrder) {
  if (!Array.isArray(tabOrder) || !tabOrder.length) {
    return;
  }
  const $nav = $tabs.find('.action-bar-tab-nav').first();
  tabOrder.forEach((tabId) => {
    const $btn = $nav.find(`.action-bar-tab-btn[data-tab="${tabId}"]`);
    if ($btn.length) {
      $nav.append($btn);
    }
  });
}

function applyActionBarPins($tabs) {
  const entityUid = $tabs.data('entityUid');
  const prefs = loadActionBarPrefs(entityUid);
  const pins = Array.isArray(prefs.pins) ? prefs.pins : [];
  const $pinnedRow = $tabs.find('.action-bar-pinned-row').first();
  $pinnedRow.empty();
  if (!pins.length) {
    $pinnedRow.attr('hidden', true);
    return;
  }
  pins.forEach((descriptor) => {
    const $sourceCell = $tabs.find('[data-action-descriptor]').filter(function () {
      return $(this).attr('data-action-descriptor') === descriptor;
    }).first();
    if (!$sourceCell.length) {
      return;
    }
    const $clone = $sourceCell.clone(false);
    $clone.addClass('is-pinned-clone');
    $clone.find('.action-bar-pin-btn').toggleClass('is-pinned', true);
    $pinnedRow.append($clone);
  });
  $pinnedRow.removeAttr('hidden');
}

function syncActionBarPinButtons($tabs) {
  const entityUid = $tabs.data('entityUid');
  const prefs = loadActionBarPrefs(entityUid);
  const pins = new Set(Array.isArray(prefs.pins) ? prefs.pins : []);
  $tabs.find('.action-bar-pin-btn').each(function () {
    const $btn = $(this);
    const descriptor = $btn.closest('[data-action-descriptor]').attr('data-action-descriptor');
    $btn.toggleClass('is-pinned', pins.has(descriptor));
  });
}

function toggleActionBarPin(entityUid, descriptor) {
  const prefs = loadActionBarPrefs(entityUid);
  const pins = new Set(Array.isArray(prefs.pins) ? prefs.pins : []);
  if (pins.has(descriptor)) {
    pins.delete(descriptor);
  } else {
    pins.add(descriptor);
  }
  prefs.pins = Array.from(pins);
  saveActionBarPrefs(entityUid, prefs);
}

const KNOCKOUT_MODE_STORAGE_KEY = 'n20_knock_unconscious';

function isKnockUnconsciousMode() {
  try {
    return sessionStorage.getItem(KNOCKOUT_MODE_STORAGE_KEY) === '1';
  } catch (e) {
    return !!window.__n20KnockUnconsciousMode;
  }
}

function setKnockUnconsciousMode(enabled) {
  const on = !!enabled;
  window.__n20KnockUnconsciousMode = on;
  try {
    sessionStorage.setItem(KNOCKOUT_MODE_STORAGE_KEY, on ? '1' : '0');
  } catch (e) { /* ignore quota / private mode */ }
  syncKnockoutModeUI();
}

function withKnockUnconsciousOpts(opts, $button) {
  const next = Object.assign({}, opts || {});
  if (isKnockUnconsciousMode() && $button && $button.attr('data-can-knock-unconscious') === '1') {
    next.knock_unconscious = true;
  }
  return next;
}

function syncKnockoutModeUI($root) {
  const on = isKnockUnconsciousMode();
  const $scope = $root && $root.length ? $root : $('#centerActionBar');
  $scope.find('.action-bar-tabs').addBack('.action-bar-tabs').toggleClass('knockout-mode', on);
  $scope.find('.knockout-toggle').each(function () {
    const $btn = $(this);
    $btn.toggleClass('is-active', on).attr('aria-pressed', on ? 'true' : 'false');
    $btn.find('.knockout-toggle-label').text(on ? 'Knock' : 'Kill');
    $btn.attr(
      'title',
      on
        ? 'Knock out: melee attacks that drop a creature to 0 HP leave them unconscious and stable. Click for lethal attacks.'
        : 'Melee attacks are lethal. Click to knock foes unconscious instead.',
    );
  });
  $scope.find('.knockout-attack-overlay').prop('hidden', !on);
}

function initializeActionBarTabs($root) {
  const $tabs = $root.find('.action-bar-tabs').addBack('.action-bar-tabs');
  $tabs.each(function () {
    const $tabRoot = $(this);
    const entityUid = $tabRoot.data('entityUid');
    const prefs = loadActionBarPrefs(entityUid);
    const defaultTab = prefs.lastTab || $tabRoot.data('defaultTab') || 'common';
    const tabOrder = prefs.tabOrder || $tabRoot.data('tabOrder') || [];

    hideEmptyActionBarTabs($tabRoot);
    applyActionBarTabOrder($tabRoot, tabOrder);
    applyActionBarPins($tabRoot);
    syncActionBarPinButtons($tabRoot);
    applyActionBarButtonOrder($tabRoot);
    initActionBarButtonDragDrop($tabRoot);
    selectActionBarTab($tabRoot, defaultTab);
    syncKnockoutModeUI($tabRoot);
  });
}

function setActionBarCustomizeMode(enabled) {
  actionBarState.customizeMode = !!enabled;
  $('#actionBarCustomizeToggle').toggleClass('active', actionBarState.customizeMode)
    .text(actionBarState.customizeMode ? 'Done' : 'Customize');
  $('#centerActionBar').toggleClass('action-bar-customize-mode', actionBarState.customizeMode);
  $('#centerActionBar .action-bar-pin-btn').prop('hidden', !actionBarState.customizeMode);
  if (actionBarState.customizeMode) {
    setActionBarInstructions('Click ★ to pin actions to the Common tab. Pins are saved per character for your account.');
  } else {
    setActionBarInstructions('Press 1-9 to trigger assigned actions. Use Bind Hotkeys to customize them.');
  }
}

function actionDescriptorForButton($button) {
  const kind =
    $button.hasClass('talk-action') ? 'talk' :
    $button.hasClass('action-end-turn') ? 'end-turn' :
    $button.hasClass('action-info') ? 'info' :
    'action';
  const actionType = $button.data('action-type') || kind;
  const opts = $button.data('action-opts') || {};
  const label = ($button.attr('title') || $button.text() || actionType || '').trim();
  return `${kind}|${actionType}|${JSON.stringify(opts)}|${label}`;
}

function clearPendingHotkeySelection() {
  actionBarState.pendingBinding = null;
  $('#centerActionBar .hotkey-capable-action').removeClass('hotkey-pending');
}

function closeCenterActionBar() {
  actionBarState.visible = false;
  actionBarState.bindingMode = false;
  actionBarState.customizeMode = false;
  actionBarState.activeEntityUid = null;
  actionBarState.activeCoords = null;
  clearPendingHotkeySelection();
  closeUpcastMenus();
  setActionBarExpanded(false);
  $('#actionBarHotkeyToggle').removeClass('active').text('Bind Hotkeys');
  $('#actionBarCustomizeToggle').removeClass('active').text('Customize');
  $('#centerActionBar').removeClass('action-bar-customize-mode');
  $('#actionBarCustomizeToggle, #actionBarHotkeyToggle').show();
  $('#centerActionBar').hide();
  $('#centerActionBarContent').empty();
  setActionBarInstructions('Press 1-9 to trigger assigned actions. Use Bind Hotkeys to customize them.');
}
window.closeCenterActionBar = closeCenterActionBar;

function activateCenterActionEntry($entry) {
  if (!$entry || !$entry.length) {
    return;
  }
  $('#centerActionBar .center-action-entry').removeClass('is-active');
  $entry.addClass('is-active');
  actionBarState.activeEntityUid = $entry.data('entityUid') || null;
  actionBarState.activeCoords = {
    x: $entry.data('coordsX'),
    y: $entry.data('coordsY'),
  };
  applyActionBarHotkeyBadges();
}

function applyActionBarHotkeyBadges() {
  $('#centerActionBar .hotkey-capable-action').each(function () {
    const $button = $(this);
    const $entry = $button.closest('.center-action-entry');
    const entityUid = $entry.data('entityUid');
    const bindings = loadActionBarBindings(entityUid);
    const descriptor = actionDescriptorForButton($button);
    const hotkey = Object.keys(bindings).find((digit) => bindings[digit] === descriptor);

    $button.removeClass('hotkey-bound');
    $button.find('.action-hotkey-badge').remove();
    if (hotkey) {
      $button.addClass('hotkey-bound');
      $('<span class="action-hotkey-badge"></span>').text(hotkey).appendTo($button);
    }
  });
}

function decorateCenterActionSection($body, entry, coordsx, coordsy) {
  $body.find('.action-button, .action-end-turn, .talk-action, .action-info').each(function () {
    $(this)
      .attr('data-id', entry.id)
      .attr('data-coords-x', coordsx)
      .attr('data-coords-y', coordsy)
      .attr('data-overlay-entity-uid', entry.id);
  });

  $body.find('.action-button, .action-end-turn, .talk-action, .action-bar-upcast-level').addClass('hotkey-capable-action');
  initializeActionBarTabs($body);
}

function closeUpcastMenus($exceptCard) {
  $('#centerActionBar .action-bar-spell-card').each(function () {
    const $card = $(this);
    if ($exceptCard && $exceptCard.length && $card.is($exceptCard)) {
      return;
    }
    $card.removeClass('is-upcast-open');
    $card.find('.action-bar-upcast-menu').attr('hidden', true);
    $card.find('.action-bar-upcast-toggle').removeClass('is-open').attr('aria-expanded', 'false');
  });
}

function fetchActionBarSections(occupantEntries) {
  const ids = occupantEntries.map((entry) => entry.id).join(",");
  return new Promise((resolve, reject) => {
    $.ajax({
      url: "/actions/batch",
      type: "GET",
      data: { ids },
      success: (payload) => {
        const byId = {};
        (payload.sections || []).forEach((section) => {
          byId[section.id] = section;
        });
        resolve(
          occupantEntries.map((entry) => {
            const section = byId[entry.id];
            if (!section) {
              return { entry, error: "No actions available." };
            }
            if (section.error) {
              return { entry, error: section.error };
            }
            const htmlPayload = section.html || "";
            if (!htmlPayload.trim()) {
              return { entry, error: "No actions available." };
            }
            return { entry, html: htmlPayload };
          }),
        );
      },
      error: (jqXHR, textStatus) => {
        let message = textStatus || "Failed to load actions.";
        if (jqXHR.responseJSON && jqXHR.responseJSON.error) {
          message = jqXHR.responseJSON.error;
        }
        reject(new Error(message));
      },
    });
  });
}

// Decide whether a /actions HTML payload contains anything actionable.
// The endpoint always emits an "info" button for every entity (including
// inert objects like ground / wall tiles). We only want to render a panel
// when the entity has at least one real action the user can perform.
function actionHtmlHasActionableContent(html) {
  if (!html || !`${html}`.trim()) {
    return false;
  }
  try {
    const $probe = $('<div></div>').html(html);
    // .action-info is always present for every entity and is purely
    // informational; ignore it. A panel is meaningful only if there is
    // at least one of: a regular action button, end-turn, talk button,
    // or DM delete control.
    const actionable = $probe.find(
      '.action-button, .action-end-turn, .talk-action, .jrpg-dialog-action, .set-pov-action, .delete-entity-btn, .action-bar-spell-card'
    );
    return actionable.length > 0;
  } catch (e) {
    // If parsing fails, fall back to showing the html.
    return true;
  }
}

function renderCenteredActionBarSections(sections, coordsx, coordsy) {
  const $content = $('#centerActionBarContent');
  const $stack = $('<div class="popover-actions-stack"></div>');

  // Drop sections that have no actionable content. This prevents empty
  // panels for environment objects like ground / wall tiles that have no
  // applicable actions for the current entity.
  const renderable = sections.filter(({ html }) => actionHtmlHasActionableContent(html));

  if (renderable.length === 0) {
    closeCenterActionBar();
    return;
  }

  renderable.forEach(({ entry, html }) => {
    const typeLabel = entry.type === 'object' ? 'Object' : 'Creature';
    const displayLabel = entry.label || entry.id;
    const $entry = $('<div class="popover-actions-entry center-action-entry"></div>')
      .attr('data-entity-uid', entry.id)
      .attr('data-coords-x', coordsx)
      .attr('data-coords-y', coordsy);
    const $header = $('<div class="popover-actions-header"></div>');
    $('<span class="popover-actions-title"></span>').text(displayLabel).appendTo($header);
    $('<span class="popover-actions-type"></span>').text(typeLabel).appendTo($header);
    $entry.append($header);

    const $body = $('<div class="popover-actions-body"></div>');
    $body.html(html);
    decorateCenterActionSection($body, entry, coordsx, coordsy);
    $entry.append($body);
    $stack.append($entry);
  });

  $content.empty().append($stack);
  $('#centerActionBarTitle').text('Actions');
  $('#centerActionBarSubtitle').text('Select an entity section, then use its action bar or hotkeys.');
  $('#centerActionBar').show();
  actionBarState.visible = true;

  const $firstEntry = $stack.find('.center-action-entry').first();
  activateCenterActionEntry($firstEntry);
}

function showCenteredActionBarLoading(occupantEntries) {
  $('#centerActionBarTitle').text('Actions');
  $('#centerActionBarSubtitle').text(`${occupantEntries.length} layer${occupantEntries.length === 1 ? '' : 's'} available on this tile.`);
  $('#centerActionBarContent').html(
    '<div class="popover-actions-stack"><div class="popover-actions-entry center-action-entry"><div class="popover-actions-header"><span class="popover-actions-title">Loading...</span></div></div></div>'
  );
  $('#centerActionBar').show();
  actionBarState.visible = true;
}

function beginHotkeyAssignment($button) {
  const $entry = $button.closest('.center-action-entry');
  clearPendingHotkeySelection();
  actionBarState.pendingBinding = {
    entityUid: $entry.data('entityUid'),
    descriptor: actionDescriptorForButton($button),
  };
  $button.addClass('hotkey-pending');
  setActionBarInstructions('Press 1-9 to bind this action. Press Backspace or Delete to clear its binding.');
}

function assignPendingActionHotkey(digit) {
  if (!actionBarState.pendingBinding) {
    return;
  }
  const { entityUid, descriptor } = actionBarState.pendingBinding;
  const bindings = loadActionBarBindings(entityUid);

  Object.keys(bindings).forEach((key) => {
    if (bindings[key] === descriptor || key === digit) {
      delete bindings[key];
    }
  });
  bindings[digit] = descriptor;
  saveActionBarBindings(entityUid, bindings);
  clearPendingHotkeySelection();
  applyActionBarHotkeyBadges();
  setActionBarInstructions(`Bound ${digit}. Press 1-9 to trigger assigned actions. Use Bind Hotkeys to customize them.`);
}

function clearPendingActionHotkey() {
  if (!actionBarState.pendingBinding) {
    return;
  }
  const { entityUid, descriptor } = actionBarState.pendingBinding;
  const bindings = loadActionBarBindings(entityUid);
  Object.keys(bindings).forEach((key) => {
    if (bindings[key] === descriptor) {
      delete bindings[key];
    }
  });
  saveActionBarBindings(entityUid, bindings);
  clearPendingHotkeySelection();
  applyActionBarHotkeyBadges();
  setActionBarInstructions('Binding cleared. Press 1-9 to trigger assigned actions. Use Bind Hotkeys to customize them.');
}

function triggerActionBarHotkey(digit) {
  if (!actionBarState.activeEntityUid) {
    return false;
  }
  const bindings = loadActionBarBindings(actionBarState.activeEntityUid);
  const descriptor = bindings[digit];
  if (!descriptor) {
    return false;
  }
  const $entry = $('#centerActionBar .center-action-entry.is-active');
  const $button = $entry.find('.hotkey-capable-action').filter(function () {
    return actionDescriptorForButton($(this)) === descriptor;
  }).first();
  if (!$button.length || $button.is(':disabled')) {
    return false;
  }
  $button.trigger('click');
  return true;
}

function shouldShowMessageInJrpgDialog(entityId, targets, options) {
  options = options || {};
  const $dialogPanel = $('#jrpgDialogPanel');
  if (!$dialogPanel.is(':visible')) {
    return false;
  }

  const currentDialogEntityId = $('#dialogEntityName').data('entity-id');
  const currentPovEntity = Chat.getCurrentPovEntity();

  if (goalArrivalConversationMode && currentDialogEntityId && entityId === currentDialogEntityId) {
    return true;
  }

  if (options.perceptionCheck && options.perceptionCheck.target_uid) {
    if (currentDialogEntityId && options.perceptionCheck.target_uid === currentDialogEntityId) {
      return true;
    }
  }
  if (options.insightCheck && options.insightCheck.message_key) {
    return true;
  }

  if (talkToEntityMode && currentDialogEntityId) {
    if (entityId !== currentDialogEntityId) {
      return false;
    }
    return Array.isArray(targets) && targets.includes(currentPovEntity);
  }

  return currentDialogEntityId === entityId
    || (Array.isArray(targets) && targets.includes(currentPovEntity));
}

function tryPlayConversationTts(message) {
  if (!message || typeof window.TTSPlayer === 'undefined' || !window.TTSPlayer) {
    return;
  }
  const audioUrl = message.audio_url;
  const ttsStatus = message.tts_status || '';
  if (!audioUrl || ttsStatus === 'pending' || ttsStatus === 'streaming') {
    return;
  }
  const TTSPlayer = window.TTSPlayer;
  const replyId = message.reply_id || null;
  if (replyId
      && typeof TTSPlayer.hasReplyPlayed === 'function'
      && TTSPlayer.hasReplyPlayed(replyId)) {
    return;
  }
  const deliveryMode = typeof TTSPlayer.getDeliveryMode === 'function'
    ? TTSPlayer.getDeliveryMode()
    : '';
  // Async/stream/manual replies autoplay via tts_ready / stream socket events only.
  if (deliveryMode === 'async' || deliveryMode === 'stream' || deliveryMode === 'manual') {
    return;
  }
  const shouldPlay = (
    typeof TTSPlayer.shouldAutoplay === 'function' && TTSPlayer.shouldAutoplay()
  ) || deliveryMode === 'sync';
  if (!shouldPlay) {
    return;
  }
  try {
    if (typeof TTSPlayer.playReplyAudio === 'function') {
      TTSPlayer.playReplyAudio({
        audio_url: audioUrl,
        reply_id: replyId,
        speaker_name: message.speaker_name || message.entity_name || message.entity_id,
        entity_id: message.entity_id,
        tts_emotion: message.tts_emotion || 'calm',
        tts_gain: message.tts_gain,
      });
    } else if (typeof TTSPlayer.onTtsReady === 'function') {
      TTSPlayer.onTtsReady({
        audio_url: audioUrl,
        reply_id: replyId,
        speaker_name: message.speaker_name || message.entity_name || message.entity_id,
        entity_id: message.entity_id,
        tts_emotion: message.tts_emotion || 'calm',
        tts_gain: message.tts_gain,
      });
    }
  } catch (ttsErr) {
    console.warn('[Engine] TTS playback failed:', ttsErr);
  }
}

function deliverConversationPayload(messagePayload, resolve) {
  const data = { message: messagePayload };
  const { entity_id, message, targets, visual_only, narrative, segments } = data.message;
  const narrativeEntries = Array.isArray(narrative)
    ? narrative.filter((entry) => `${entry || ''}`.trim().length > 0)
    : [];
  const segmentEntries = Array.isArray(segments)
    ? segments.filter((segment) => segment && `${segment.text || ''}`.trim().length > 0)
    : [];
  const actionEntries = Array.isArray(data.message.actions)
    ? data.message.actions.filter((entry) => `${entry || ''}`.trim().length > 0)
    : [];
  const skillCheckRequest = data.message.skill_check_request || null;
  const hasRenderableContent = (message && String(message).trim())
    || narrativeEntries.length > 0
    || segmentEntries.length > 0
    || actionEntries.length > 0
    || !!skillCheckRequest;

  if (typeof Chat.shouldSkipConversationReply === 'function' && Chat.shouldSkipConversationReply(data.message)) {
    if (typeof resolve === 'function') {
      resolve();
    }
    return;
  }

  if (typeof Chat.markConversationReplyRendered === 'function') {
    Chat.markConversationReplyRendered(data.message);
  }

  if (!entity_id || !hasRenderableContent) {
    console.warn('Invalid conversation event received:', data.message);
    if (typeof resolve === 'function') {
      resolve();
    }
    return;
  }

  try {
    Chat.handleLocalConversationEvent(data.message);
  } catch (error) {
    console.warn('Failed to update local conversation panel', error);
  }

  const finish = () => {
    if (typeof resolve === 'function') {
      resolve();
    }
  };

  if (data.message.local_chat_only) {
    tryPlayConversationTts(data.message);
    finish();
    return;
  }

  if (visual_only) {
    finish();
    return;
  }

  const isSystemMessage = data.message.system === true || !!data.message.insight_check || !!data.message.perception_check;
  const systemSpeakerName = data.message.speaker_name || 'Dungeon Master';
  const $dialogPanel = $('#jrpgDialogPanel');
  const currentPovEntity = Chat.getCurrentPovEntity();

  const bubbleOptions = {
    reply_id: data.message.reply_id || null,
  };

  if (isSystemMessage) {
    const insightCheck = data.message.insight_check || null;
    const perceptionCheck = data.message.perception_check || null;
    if (shouldShowMessageInJrpgDialog(entity_id, targets, { insightCheck, perceptionCheck })) {
      try {
        if (insightCheck && insightCheck.assessment) {
          const attached = insightCheck.message_key
            ? Chat.applyInsightResultToDialogMessage(insightCheck.message_key, insightCheck)
            : false;
          if (!attached) {
            Chat.addDialogInsightResult(insightCheck, message, {
              attach_to_message_key: insightCheck.message_key || null,
            });
          }
        } else if (perceptionCheck && perceptionCheck.description) {
          Chat.addDialogPerceptionResult(perceptionCheck, message);
        } else {
          Chat.addDialogMessage('system', message, 'system', {
            displayName: systemSpeakerName,
            insight_check: insightCheck,
            perception_check: perceptionCheck,
          });
        }
      } catch (error) {
        console.error('Error adding system message to dialog panel:', error);
        Chat.showConversationBubble(entity_id, message, bubbleOptions);
      }
    } else {
      Chat.showConversationBubble(entity_id, message, bubbleOptions);
    }
    finish();
    return;
  }

  if (shouldShowMessageInJrpgDialog(entity_id, targets)) {
    console.log('Adding conversation message to dialog panel:', message);
    try {
      if (!isSystemMessage && (data.message.gallery_image || data.message.gallery_image_id)) {
        const gallery = $('#dialogEntityProfilePanel').data('imageGallery') || [];
        applyDialogEntityPortrait({
          name: data.message.speaker_name || $('#dialogEntityName').text(),
          token_image: null,
          profile_image: data.message.gallery_image || null,
          has_full_portrait: true,
          gallery_image: data.message.gallery_image || null,
          gallery_image_id: data.message.gallery_image_id || null,
          image_gallery: gallery,
        });
      }
      Chat.addDialogMessage('entity', message || '', 'entity', {
        displayName: data.message.speaker_name || null,
        narrative: narrativeEntries,
        segments: segmentEntries,
        audio_url: data.message.audio_url || null,
        tts_emotion: data.message.tts_emotion || 'calm',
        tts_status: data.message.tts_status || null,
        tts_gain: data.message.tts_gain,
        tts_skipped: data.message.tts_skipped,
        entity_id: entity_id,
        target_id: data.message.target_id || entity_id,
        message_key: data.message.message_key || null,
        reply_id: data.message.reply_id || null,
        skill_check_request: skillCheckRequest,
        message: message || '',
      });
      if (typeof clearDialogWaitingState === 'function') { clearDialogWaitingState(); }
      if (data.message.continuation_id && (data.message.continuation_beats_remaining || 0) > 0) {
        Chat.scheduleDialogAutoContinue(data.message);
      }
    } catch (error) {
      console.error('Error adding message to dialog modal:', error);
      Chat.showConversationBubble(entity_id, message, bubbleOptions);
    }
  } else if (targets && Array.isArray(targets) && targets.includes(currentPovEntity) && !$dialogPanel.is(':visible')) {
    console.log('Message directed to POV entity, but dialog panel not open:', message);
    showDialogTriggerBubble(entity_id, message, bubbleOptions);
  } else {
    Chat.showConversationBubble(entity_id, message, bubbleOptions);
  }

  const audioUrl = data.message.audio_url;
  const ttsStatus = data.message.tts_status || '';
  if (audioUrl && ttsStatus !== 'pending' && ttsStatus !== 'streaming') {
    tryPlayConversationTts(data.message);
  } else if (audioUrl) {
    console.log('[Engine] TTS audio ready (click speaker to play):', audioUrl);
  }

  finish();
}

window.ingestTalkConversationReplies = function (replies) {
  (replies || []).forEach((payload) => {
    deliverConversationPayload(payload);
  });
};

// Debounced map refresh — coalesces rapid spawn/place events so /update fetches
// run after the server has applied every placement.
let refreshMapDebounceTimer = null;
let refreshMapResolvers = [];

function scheduleRefreshMap(delayMs = 180, options = {}) {
  const forceFullRefresh = !!options.forceFullRefresh || isEditMode();
  // Suppress /update requests for 500ms after the player commits an action.
  // After clicking attack/target/any multi-step action, the server emits
  // multiple refresh_map/refresh_tiles events while the action resolves,
  // causing the client to hammer /update (6+ requests per turn).  The
  // cooldown filter ensures only the first (or post-cooldown) request fires,
  // and those are already coalesced by the existing debounce timer.
  if (typeof actionPostTimestamp === 'number' && (Date.now() - actionPostTimestamp) < 500) {
    return Promise.resolve();
  }
  return new Promise((resolve) => {
    refreshMapResolvers.push(resolve);
    if (refreshMapDebounceTimer) {
      clearTimeout(refreshMapDebounceTimer);
    }
    refreshMapDebounceTimer = setTimeout(() => {
      refreshMapDebounceTimer = null;
      const resolvers = refreshMapResolvers;
      refreshMapResolvers = [];
      try {
        invalidateMovementPlanningCaches();
      } catch (_) { /* ignore */ }
      try {
        Utils.refreshTileSet(false, false, 0, 0, null, () => {
          try { updateDraggableEntityClasses(); } catch (_) { }
          try { Chat.scheduleLocalConversationPresenceRefresh(); } catch (_) { }
          try { cleanupAllPendingMoves(); } catch (_) { }
          try { cleanupMovementSprites(); } catch (_) { }
          if (pendingViewportCenterUid) {
            scheduleViewportCenterOnEntity(pendingViewportCenterUid, {
              delayMs: 0,
              maxAttempts: 15,
            });
          }
          resolvers.forEach((done) => {
            try { done(); } catch (_) { /* ignore */ }
          });
        }, forceFullRefresh);
      } catch (e) {
        console.warn('scheduleRefreshMap failed, continuing', e);
        resolvers.forEach((done) => {
          try { done(); } catch (_) { /* ignore */ }
        });
      }
    }, delayMs);
  });
}

function afterMapPlacementRefresh() {
  scheduleRefreshMap(80);
}

/** Resolve map tile coords from viewport pointer position (pan/zoom-safe). */
function tileCoordsFromClientPoint(clientX, clientY) {
  if (clientX == null || clientY == null) {
    return null;
  }

  const stackOff = Utils.getStackCompositeOffset && Utils.getStackCompositeOffset();
  const stackActive = stackOff && stackOff.active && stackOff.mapName;
  const overlayTileSelector = stackActive
    ? `.map-stack-overlay[data-layer-map="${stackOff.mapName}"] .map-stack-tile`
    : null;

  const elementBelow = document.elementFromPoint(clientX, clientY);
  if (elementBelow) {
    const $tile = stackActive
      ? $(elementBelow).closest(overlayTileSelector)
      : $(elementBelow).closest('.tile');
    if ($tile.length) {
      const x = parseInt($tile.attr('data-coords-x'), 10);
      const y = parseInt($tile.attr('data-coords-y'), 10);
      if (!Number.isNaN(x) && !Number.isNaN(y)) {
        return {
          x,
          y,
          $tile,
          mapName: $tile.attr('data-layer-map') || (stackActive ? stackOff.mapName : null),
        };
      }
    }
  }

  let hit = null;
  const candidates = stackActive
    ? document.querySelectorAll(overlayTileSelector)
    : document.querySelectorAll('.tiles-container .tile');
  candidates.forEach((tileEl) => {
    if (hit) {
      return;
    }
    const rect = tileEl.getBoundingClientRect();
    if (
      clientX >= rect.left
      && clientX < rect.right
      && clientY >= rect.top
      && clientY < rect.bottom
    ) {
      const $tile = $(tileEl);
      const x = parseInt($tile.attr('data-coords-x'), 10);
      const y = parseInt($tile.attr('data-coords-y'), 10);
      if (!Number.isNaN(x) && !Number.isNaN(y)) {
        hit = {
          x,
          y,
          $tile,
          mapName: $tile.attr('data-layer-map') || (stackActive ? stackOff.mapName : null),
        };
      }
    }
  });
  if (hit) {
    return hit;
  }

  if (!stackActive) {
    return null;
  }

  // Fallback: base-map tiles when pointer is outside the overlay footprint.
  $('.tiles-container .tile').each(function () {
    if (hit) {
      return false;
    }
    const rect = this.getBoundingClientRect();
    if (
      clientX >= rect.left
      && clientX < rect.right
      && clientY >= rect.top
      && clientY < rect.bottom
    ) {
      const $tile = $(this);
      const x = parseInt($tile.attr('data-coords-x'), 10);
      const y = parseInt($tile.attr('data-coords-y'), 10);
      if (!Number.isNaN(x) && !Number.isNaN(y)) {
        hit = { x, y, $tile, mapName: null };
        return false;
      }
    }
  });
  return hit;
}

function centerDragImageOnCursor(e, $source) {
  const transfer = e.originalEvent && e.originalEvent.dataTransfer;
  if (!transfer || typeof transfer.setDragImage !== 'function') {
    return;
  }
  const node = ($source && $source.length)
    ? ($source.find('img').get(0) || $source.get(0))
    : null;
  if (!node) {
    return;
  }
  const rect = node.getBoundingClientRect ? node.getBoundingClientRect() : null;
  const offsetX = rect && rect.width ? Math.floor(rect.width / 2) : 16;
  const offsetY = rect && rect.height ? Math.floor(rect.height / 2) : 16;
  try {
    transfer.setDragImage(node, offsetX, offsetY);
  } catch (_) { /* ignore */ }
}

window.scheduleRefreshMap = scheduleRefreshMap;
window.afterMapPlacementRefresh = afterMapPlacementRefresh;
window.tileCoordsFromClientPoint = tileCoordsFromClientPoint;

// Serialize DM map placements so rapid drag-and-drop cannot overlap requests.
let mapPlacementTail = Promise.resolve();

function enqueueMapPlacement(run) {
  const job = mapPlacementTail.then(() => run());
  mapPlacementTail = job.catch(() => { });
  return job;
}

function postJsonPlacement(url, payload, isSuccess) {
  return new Promise((resolve, reject) => {
    $.ajax({
      url,
      method: 'POST',
      contentType: 'application/json',
      data: JSON.stringify(payload),
    })
      .done((resp) => {
        if (typeof isSuccess === 'function' ? isSuccess(resp) : (resp && (resp.status === 'ok' || resp.success))) {
          resolve(resp);
          return;
        }
        reject(new Error((resp && resp.error) || 'Placement failed'));
      })
      .fail((xhr) => {
        let message = 'Placement failed';
        try {
          message = JSON.parse(xhr.responseText || '{}').error || message;
        } catch (_) { /* ignore */ }
        reject(new Error(message));
      });
  });
}

window.enqueueMapPlacement = enqueueMapPlacement;

// Event queue system for FIFO processing
class EventQueue {
  constructor() {
    this.queue = [];
    this.processing = false;
    this.maxQueueSize = 100; // Prevent memory leaks
    this.processedCount = 0;
    this.debugMode = true; // Set to true for debugging
  }

  setDebugMode(enabled) {
    this.debugMode = enabled;
  }

  getStatus() {
    return {
      queueLength: this.queue.length,
      processing: this.processing,
      processedCount: this.processedCount,
      maxQueueSize: this.maxQueueSize
    };
  }

  enqueue(event) {
    // Drop invalid events early to avoid breaking the queue
    if (!event || typeof event !== 'object') {
      console.warn('[EventQueue] Ignoring invalid event:', event);
      return;
    }
    // Coalesce tile refreshes: only the latest refresh_tiles matters.
    if (event.type === 'refresh_tiles') {
      this.queue = this.queue.filter((queued) => queued.type !== 'refresh_tiles');
    }
    // Coalesce map refreshes outside the FIFO queue; debounce fetches latest state.
    if (event.type === 'refresh_map') {
      scheduleRefreshMap();
      return;
    }
    if (this.queue.length >= this.maxQueueSize) {
      console.warn('Event queue is full, dropping oldest event');
      this.queue.shift();
    }

    if (this.debugMode) {
      console.log(`[EventQueue] Enqueuing event: ${event.type}, queue length: ${this.queue.length + 1}`);
    }

    this.queue.push(event);
    this.processNext();
  }

  async processNext() {
    if (this.processing || this.queue.length === 0) { return; }

    this.processing = true;

    while (this.queue.length > 0) {
      const event = this.queue.shift();
      this.processedCount++;

      if (this.debugMode) {
        console.log(`[EventQueue] Processing event #${this.processedCount}: ${event.type}, remaining: ${this.queue.length}`);
      }

      try {
        await this.processEvent(event);
        // Small delay between move events to smooth visuals
        if (event.type === 'move' && this.queue.length > 0) {
          await new Promise(resolve => setTimeout(resolve, 100));
        }
      } catch (error) {
        console.error('Error processing event:', error, event);
      }
    }

    this.processing = false;

    if (this.debugMode) {
      console.log(`[EventQueue] Processing complete. Total processed: ${this.processedCount}`);
    }
  }

  async processEvent(data) {
    return new Promise((resolve) => {
      // Validate event payload
      if (!data || typeof data !== 'object') {
        console.warn('[EventQueue] Skipping invalid event payload:', data);
        resolve();
        return;
      }
      const evtType = data.type;
      if (this.debugMode) {
        console.log(`[EventQueue] Processing event: ${evtType}`, data);
      } else {
        console.log('Processing event:', data);
      }
      if (!evtType) {
        console.warn('[EventQueue] Event missing type, skipping:', data);
        resolve();
        return;
      }

      switch (evtType) {
        case "refresh_tiles": {
          try {
            invalidateMovementPlanningCaches();
            const msg = (data && data.message) || {};
            const is_setup = !!msg.is_setup;
            const pov = !!msg.pov;
            const x = msg.x || 0;
            const y = msg.y || 0;
            const entity_uid = msg.entity_uid || null;
            const cb = typeof msg.callback === 'function' ? msg.callback : null;
            const forceFullRefresh = !!msg.force_full_refresh;
            Utils.refreshTileSet(is_setup, pov, x, y, entity_uid, () => {
              try { cleanupMovementSprites(); } catch (_) { }
              try { if (cb) cb(); } catch (_) { }
              resolve();
            }, forceFullRefresh);
          } catch (e) {
            console.warn('refresh_tiles failed, continuing', e);
            resolve();
          }
          break;
        }
        case "refresh_map": {
          scheduleRefreshMap().then(() => resolve());
          break;
        }
        case "open_entity_dialog": {
          try {
            const entityId = data.entity_id || data.entity_uid;
            const entityName = data.entity_name || entityId;
            if (entityId && data.goal_arrival && typeof enterGoalArrivalConversationMode === 'function') {
              enterGoalArrivalConversationMode(entityId, entityName);
            } else if (entityId && data.forced && typeof enterForcedConversationMode === 'function') {
              enterForcedConversationMode(entityId, entityName);
            } else if (entityId && typeof runTalkWithApproach === 'function') {
              const povUid = $('body').attr('data-pov-entity') || (typeof Chat !== 'undefined' && Chat.getCurrentPovEntity && Chat.getCurrentPovEntity());
              if (povUid) {
                runTalkWithApproach(povUid, entityId, entityName);
              } else if (typeof handleDialogBubbleClick === 'function') {
                handleDialogBubbleClick(entityId, entityName);
              }
            } else if (entityId && typeof handleDialogBubbleClick === 'function') {
              handleDialogBubbleClick(entityId, entityName);
            }
          } catch (e) {
            console.warn('open_entity_dialog failed', e, data);
          }
          resolve();
          break;
        }
        case "goal_arrival_start": {
          try {
            const entityId = data.entity_id || data.entity_uid;
            const entityName = data.entity_name || entityId;
            if (entityId && typeof enterGoalArrivalConversationMode === 'function') {
              enterGoalArrivalConversationMode(entityId, entityName);
            }
          } catch (e) {
            console.warn('goal_arrival_start failed', e, data);
          }
          resolve();
          break;
        }
        case "goal_arrival_end": {
          try {
            if (typeof exitGoalArrivalConversationMode === 'function') {
              exitGoalArrivalConversationMode();
            }
          } catch (e) {
            console.warn('goal_arrival_end failed', e, data);
          }
          resolve();
          break;
        }
        case "forced_conversation_start": {
          try {
            const entityId = data.entity_id || data.entity_uid;
            const entityName = data.entity_name || entityId;
            if (entityId && typeof enterForcedConversationMode === 'function') {
              enterForcedConversationMode(entityId, entityName);
            }
          } catch (e) {
            console.warn('forced_conversation_start failed', e, data);
          }
          resolve();
          break;
        }
        case "forced_conversation_end": {
          try {
            if (typeof exitForcedConversationMode === 'function') {
              exitForcedConversationMode();
            }
          } catch (e) {
            console.warn('forced_conversation_end failed', e, data);
          }
          resolve();
          break;
        }
        case "pickpocket_detected": {
          try {
            const msg = (data && data.message) || {};
            const text = msg.text || msg.message || 'Your pickpocket attempt was noticed.';
            showMapToast(text, null, null, 12000);
          } catch (e) {
            console.warn('Failed to show pickpocket_detected', e, data);
          }
          resolve();
          break;
        }
        case "steal_detected": {
          try {
            const msg = (data && data.message) || {};
            const text = msg.text || msg.message || 'Someone noticed your theft.';
            showMapToast(text, null, null, 12000);
            const $lootModal = $('#modal-1:visible .loot-items-form');
            if ($lootModal.length) {
              let $alert = $lootModal.find('.steal-detected-alert');
              if (!$alert.length) {
                $alert = $('<div class="alert alert-danger steal-detected-alert" role="alert"></div>')
                  .prependTo($lootModal.find('.panel-body').first());
              }
              $alert.text(text).show();
            }
          } catch (e) {
            console.warn('Failed to show steal_detected', e, data);
          }
          resolve();
          break;
        }
        case "message_toaster": {
          try {
            const msg = (data && data.message !== undefined) ? data.message : data;
            const text = (typeof msg === 'string') ? msg : (msg && (msg.text || msg.message || msg.msg)) || '';
            // If msg is a string, pull position/source from the original data object
            const position = (typeof msg === 'string')
              ? (data && (data.position || data.pos || data.coords))
              : (msg && (msg.position || msg.pos || msg.coords));
            const source = (typeof msg === 'string')
              ? (data && (data.source || data.entity_id))
              : (msg && (msg.source || msg.entity_id));
            // Show toast but don't block the queue for the full duration
            showMapToast(text, position, source, 10000);
          } catch (e) { console.warn('Failed to show message_toaster', e, data); }
          resolve();
          break;
        }
        case "message_spell_link": {
          try {
            const text = (data && data.message) || 'Message link opened. Whisper in Local Chat.';
            const source = data && (data.source || data.entity_id);
            showMapToast(text, null, source, 12000);
            if (window.Chat && typeof Chat.openMessageSpellLink === 'function') {
              Chat.openMessageSpellLink(data);
            }
          } catch (e) { console.warn('Failed to show message_spell_link', e, data); }
          resolve();
          break;
        }
        case "contextual_sound": {
          try {
            showContextualSound(
              data && data.message,
              data && (data.position || data.pos || data.coords),
              data && (data.source || data.entity_id),
              data && data.duration_ms,
              data && data.label,
            );
          } catch (e) { console.warn('Failed to show contextual_sound', e, data); }
          resolve();
          break;
        }
        case "xp_awarded": {
          try {
            const msg = (data && data.message) || {};
            const text = `${msg.name || 'A character'} gained ${msg.amount || 0} XP (${msg.new_xp || 0} total).`;
            showMapToast(text, null, msg.entity_uid, 8000);
          } catch (e) { console.warn('Failed to show xp_awarded', e, data); }
          resolve();
          break;
        }
        case "level_up_available": {
          try {
            const msg = (data && data.message) || {};
            const text = `${msg.name || 'A character'} can level up to ${msg.eligible_level || 'the next level'}.`;
            showMapToast(text, null, msg.entity_uid, 12000);
          } catch (e) { console.warn('Failed to show level_up_available', e, data); }
          resolve();
          break;
        }
        case "level_up_applied": {
          try {
            const msg = (data && data.message) || {};
            const text = `${msg.name || 'A character'} advanced to level ${msg.level || ''}.`;
            showMapToast(text, null, msg.entity_uid, 10000);
          } catch (e) { console.warn('Failed to show level_up_applied', e, data); }
          resolve();
          break;
        }
        case "party_membership": {
          try {
            const msg = (data && data.message) || data || {};
            const text = msg.text || msg.message || '';
            const entityUid = msg.entity_uid || msg.entity_id || data.entity_uid;
            const action = msg.action || data.action || 'join';
            if (text && typeof Utils !== 'undefined' && Utils.showToast) {
              Utils.showToast(text);
            }
            if (text && entityUid) {
              showMapToast(text, null, entityUid, 10000);
            }
            if (typeof Utils !== 'undefined' && Utils.refreshPortraits) {
              Utils.refreshPortraits();
            }
            const dialogUid = $('#dialogEntityName').data('entity-id');
            if (entityUid && dialogUid && String(dialogUid) === String(entityUid)) {
              if (action === 'join' || msg.hostile) {
                $('#dialogInviteParty').hide();
              } else if (action === 'leave') {
                $('#dialogInviteParty').show();
              }
              if (text && window.Chat && typeof window.Chat.addDialogMessage === 'function') {
                window.Chat.addDialogMessage('system', text, 'system');
              }
            }
          } catch (e) { console.warn('Failed to handle party_membership', e, data); }
          resolve();
          break;
        }
        case "journal_update": {
          try {
            if (window.JournalNotifications) {
              JournalNotifications.refresh();
              const reason = data.reason || '';
              if (reason === 'narration' || reason === 'npc_hook') {
                const total = (JournalNotifications.summary && JournalNotifications.summary.total_unread) || 0;
                if (total > 0) {
                  showMapToast(`New journal ${total === 1 ? 'entry' : 'entries'} (${total} unread). Open the menu → Journal.`, null, null, 8000);
                }
              }
            }
          } catch (e) { console.warn('Failed to refresh journal notifications', e, data); }
          resolve();
          break;
        }
        case "map": {
          this.processMapEvent(data, resolve);
          break;
        }
        case "conversation": {
          this.processConversationEvent(data, resolve);
          break;
        }
        case "conversation_refresh": {
          try { Chat.scheduleLocalConversationPresenceRefresh(); } catch (_) { }
          resolve();
          break;
        }
        case "move": {
          this.processMoveEvent(data, resolve);
          try { Chat.scheduleLocalConversationPresenceRefresh(); } catch (_) { }
          break;
        }
        case "spell": {
          this.processSpellEvent(data, resolve);
          break;
        }
        case "attack": {
          this.processAttackEvent(data, resolve);
          break;
        }
        case "message":
          console.log(data.message);
          resolve();
          break;
        case "error":
          console.error(data.message);
          resolve();
          break;
        case "console":
          $("#console-container #console").append(`<p>${data.message}</p>`);
          $("#console-container").scrollTop(
            $("#console-container")[0].scrollHeight,
          );
          resolve();
          break;
        case "track":
          console.log("Playing track:", data.message);
          playSound(
            data.message.url || data.message.file,
            data.message.track_id || data.message.id,
            data.message.volume,
            0,
          );

          // Update DM Sound Manager state when track changes
          if (typeof DMSoundManager !== 'undefined') {
            DMSoundManager.currentTrackId = data.message.track_id || data.message.id;
            DMSoundManager.isPlaying = true;
            // Update UI if the modal is open
            if ($('#modal-1').hasClass('in') || $('#modal-1').is(':visible')) {
              DMSoundManager.updateUI();
            }
          }

          resolve();
          break;
        case "open_merchant_shop": {
          const msg = data.message || {};
          if (msg.prompt) {
            alert(msg.prompt);
          }
          const merchantUid = msg.merchant_uid;
          const buyerUid = msg.buyer_uid || Chat.getCurrentPovEntity();
          if (merchantUid && buyerUid) {
            openMerchantTradeModal(merchantUid, buyerUid);
          }
          resolve();
          break;
        }
        case "open_item_transfer": {
          const msg = data.message || {};
          const operatorUid = msg.operator_entity_uid;
          const counterpartyUid = msg.counterparty_entity_uid;
          const actionType = msg.action_type || "loot";
          const transferSessionId = msg.transfer_session_id || null;
          let transferCompleted = false;
          const reportGiftTransferComplete = (payload) => {
            if (!transferSessionId) {
              return;
            }
            ajaxPost("/talk/item_transfer_complete", payload, () => {}, true);
          };
          if (msg.prompt) {
            alert(msg.prompt);
          }
          if (!operatorUid || !counterpartyUid) {
            resolve();
            break;
          }
          const objectAction = actionType === "give" ? "give" : "loot";
          Utils.ajaxGet(
            "/items",
            {
              id: operatorUid,
              action: "InteractAction",
              opts: {
                action_type: "interact",
                target: counterpartyUid,
                object_action: objectAction,
              },
            },
            (html) => {
              $("#modal-1 .modal-content").html(html);
              $("#modal-1")
                .off("hidden.bs.npcGiftTransfer")
                .on("hidden.bs.npcGiftTransfer", function () {
                  if (!transferCompleted && transferSessionId) {
                    reportGiftTransferComplete({
                      transfer_session_id: transferSessionId,
                      cancelled: true,
                    });
                  }
                  $("#modal-1").off("hidden.bs.npcGiftTransfer");
                  $(".loot-items-form").off("submit.npcTrade");
                });
              $("#modal-1").modal("show");
              $(".loot-items-form")
                .off("submit.npcTrade")
                .on("submit.npcTrade", function (e) {
                  e.preventDefault();
                  const fromItems = [];
                  const fromItemsQty = [];
                  $(this)
                    .find('input[name="selected_items_target"]')
                    .each(function () {
                      fromItems.push($(this).val());
                    });
                  $(this)
                    .find("input.transfer-qty")
                    .each(function () {
                      fromItemsQty.push($(this).val());
                    });
                  const toItems = [];
                  const toItemsQty = [];
                  $(this)
                    .find('input[name="selected_items_source"]')
                    .each(function () {
                      toItems.push($(this).val());
                    });
                  $(this)
                    .find("input.transfer-qty-source")
                    .each(function () {
                      toItemsQty.push($(this).val());
                    });
                  const itemsToTransfer = {
                    from: { items: fromItems, qty: fromItemsQty },
                    to: { items: toItems, qty: toItemsQty },
                  };
                  ajaxPost(
                    "/action",
                    {
                      id: operatorUid,
                      action: "InteractAction",
                      opts: {
                        action_type: "interact",
                        target: counterpartyUid,
                        object_action: objectAction,
                        items: itemsToTransfer,
                      },
                    },
                    () => {
                      transferCompleted = true;
                      $("#modal-1").modal("hide");
                      refreshTurn();
                      if (transferSessionId) {
                        reportGiftTransferComplete({
                          transfer_session_id: transferSessionId,
                          cancelled: false,
                          items: itemsToTransfer,
                        });
                      }
                      resolve();
                    },
                    true,
                  );
                });
            },
          );
          break;
        }
        case "prompt": {
          const opts = Array.isArray(data.options) ? data.options : [];
          const hasOptions = opts.length > 0;
          const postPromptResponse = (responseText) => {
            ajaxPost(
              "/response",
              { response: responseText || "", callback: data.callback },
              () => {
                console.log("Response sent successfully");
                resolve();
              },
              true,
            );
          };

          if (!hasOptions) {
            alert(data.message);
            postPromptResponse("");
            break;
          }

          // Render a custom modal for option-based prompts (e.g. Accept Item Yes/No).
          $('#n20PromptModal').remove();
          const $modal = $(`
            <div id="n20PromptModal" class="modal fade in" tabindex="-1" role="dialog" aria-labelledby="n20PromptModalLabel" style="display:block; background: rgba(0,0,0,.45);">
              <div class="modal-dialog" role="document" style="margin-top: 80px; max-width: 520px;">
                <div class="modal-content">
                  <div class="modal-header" style="background:#2c2c2c; color:#fff;">
                    <h4 class="modal-title" id="n20PromptModalLabel">Prompt</h4>
                  </div>
                  <div class="modal-body">
                    <p id="n20PromptModalMessage" style="margin: 0; white-space: pre-wrap;"></p>
                  </div>
                  <div class="modal-footer" id="n20PromptModalButtons"></div>
                </div>
              </div>
            </div>
          `);

          $modal.find('#n20PromptModalMessage').text(data.message || '');
          $modal.find('#n20PromptModalLabel').text(data.title || 'Prompt');
          const $buttons = $modal.find('#n20PromptModalButtons');
          const fallbackOption = String(opts[opts.length - 1] || 'No');
          let responded = false;

          const complete = (selected) => {
            if (responded) return;
            responded = true;
            $(document).off('keydown.n20Prompt');
            $modal.remove();
            $('.modal-backdrop.n20-prompt-backdrop').remove();
            postPromptResponse(selected);
          };

          opts.forEach((option, index) => {
            const optionText = String(option);
            const isPrimary = index === 0;
            const btnClass = isPrimary ? 'btn btn-primary' : 'btn btn-default';
            const $btn = $(`<button type="button" class="${btnClass}"></button>`).text(optionText);
            $btn.on('click', () => complete(optionText));
            $buttons.append($btn);
          });

          const $backdrop = $('<div class="modal-backdrop fade in n20-prompt-backdrop"></div>');
          $('body').append($backdrop).append($modal);

          // Closing the modal without explicit choice is treated as decline/default.
          $modal.on('click', function (evt) {
            if (evt.target === this) {
              complete(fallbackOption);
            }
          });

          $(document).on('keydown.n20Prompt', function (evt) {
            if (evt.key === 'Escape') {
              complete(fallbackOption);
            }
          });
          break;
        }
        case "turn":
          // Turn updates can follow enemy movement, so invalidate any cached
          // client-side occupancy grid before computing local paths.
          invalidateMovementPlanningCaches();
          refreshTurn();
          // Update game time if provided in the message
          if (data.message && data.message.game_time !== undefined) {
            updateGameTimeDisplay(data.message.game_time);
          }
          if (data.message && data.message.time_of_day && data.message.time_of_day.background) {
            try {
              Utils.updateMapDisplay({ background: data.message.time_of_day.background }, globalCanvas);
            } catch (err) {
              console.warn('Failed to apply time-of-day background', err);
            }
          }
          try { Chat.scheduleLocalConversationPresenceRefresh(); } catch (_) { }
          resolve();
          break;
        case "focus":
          centerOnTileXY(data.message.x, data.message.y, true);
          resolve();
          break;
        case "stoptrack":
          this.processStopTrackEvent(data, resolve);
          break;
        case "volume":
          if (active_background_sound) {
            active_background_sound.volume = data.message.volume / 100;
            $(".volume-slider").val(data.message.volume);

            // Update DM Sound Manager if volume changed externally
            if (typeof DMSoundManager !== 'undefined' && DMSoundManager.currentTrackId && DMSoundManager.currentTrackId !== "-1") {
              DMSoundManager.setTrackVolume(DMSoundManager.currentTrackId, data.message.volume);
              // Update UI if the modal is open
              if ($('#modal-1').hasClass('in') || $('#modal-1').is(':visible')) {
                $('.volume-display').text(data.message.volume + '%');
                DMSoundManager.updateVolumeDisplays();
              }
            }

            // Apply user volume control on top of DM volume change
            if (typeof UserVolumeControl !== 'undefined') {
              setTimeout(() => UserVolumeControl.applyUserVolume(), 50);
            }
          }
          resolve();
          break;
        case "initiative":
          $("body").attr("data-battle_in_progress", "true");
          refreshTurnOrder();
          $("#start-initiative, #start-battle").hide();
          $("#end-battle").show();
          const isBattleStart = !data.message || data.message.index === undefined;
          if (isBattleStart && !playPendingSoundtrackIfAny()) {
            const battleTrackUrl = $("body").data("soundtrack-url");
            const battleTrackId = $("body").data("soundtrack-id");
            const battleTrackVolume = $("body").data("soundtrack-volume");
            if (battleTrackUrl && battleTrackId) {
              playSound(battleTrackUrl, battleTrackId, battleTrackVolume, 0);
            }
          }
          resolve();
          break;
        case "stop":
          $("body").attr("data-battle_in_progress", "false");
          $("#turn-order").html("");
          $(".game-turn-container").hide();
          $("#battle-turn-order").fadeOut();
          $("#start-initiative, #start-battle").show();
          $("#end-battle").hide();
          resolve();
          break;
        case "reaction":
          Utils.ajaxGet("/reaction", {}, (dataHtml) => {
            $("#reaction-modal .reaction-content").html(dataHtml);
            $("#reaction-modal").modal("show");
            resolve();
          });
          break;
        case "dismiss_reaction":
          $("#reaction-modal").modal("hide");
          resolve();
          break;
        case "bardic_inspiration_prompt": {
          const msg = (data && data.message) || {};
          const die = msg.die || '1d6';
          const base = msg.base_total != null ? msg.base_total : '?';
          const threshold = msg.threshold != null ? msg.threshold : '?';
          const promptText = `Use Bardic Inspiration (${die})? Your roll is ${base} vs ${threshold}.`;
          const useDie = window.confirm(promptText);
          Utils.ajaxPost('/bardic_inspiration', { use: useDie }, () => {
            Utils.refreshTileSet(false, false, 0, 0, null, resolve);
          }, () => resolve());
          break;
        }
        case "dismiss_bardic_inspiration_prompt":
          resolve();
          break;
        case "switch_map":
          var map_id = data.message.map;
          var triggered_entity_uid = data.message.entity_uid || null;
          invalidateMovementPlanningCaches();
          Utils.switchMap(map_id, globalCanvas, () => {
            createGlobalCanvas();
            const targetUid = triggered_entity_uid || (function () {
              try { return Chat.getCurrentPovEntity(); } catch (e) { return null; }
            })();
            if (targetUid) {
              scheduleViewportCenterOnEntity(targetUid, { resetZoom: true });
            }
            try { Chat.scheduleLocalConversationPresenceRefresh(); } catch (_) { }
            resolve();
          });
          break;
        case "command_response":
          $("#command-output").append(data.message + "\n");
          resolve();
          break;
        case "narration":
          Utils.showNarration(data.message, data.map_name);
          resolve();
          break;
        case "control_override":
        case "turn_skipped": {
          try {
            const toastText = (data && (data.toast || data.message)) || '';
            const targetUid = (data && data.target && data.target.uid) || null;
            const pos = (data && data.position) || null;
            if (toastText) {
              showMapToast(toastText, pos, targetUid, 6000);
            }
            // Always also drop a console line so it survives in the chat log
            // even if the toast is missed.
            const consoleMsg = (data && data.message) || toastText;
            if (consoleMsg) {
              $("#console-container #console").append(`<p><em>${consoleMsg}</em></p>`);
              $("#console-container").scrollTop(
                $("#console-container")[0].scrollHeight,
              );
            }
            // Refresh the action panel so any disabled actions or skipped
            // turns reflect the new state for the local player.
            try { refreshTurn(); } catch (e) { /* non-fatal */ }
          } catch (e) {
            console.warn('Failed to surface control override notification', e, data);
          }
          resolve();
          break;
        }
        case "shell_defense_enter":
        case "shell_enter": {
          try {
            const sourceId = data.source || data.entity_id || data.entity_uid;
            if (sourceId && typeof PersistentEffects !== 'undefined' && PersistentEffects.updateForEntity) {
              PersistentEffects.updateForEntity(sourceId);
            }
          } catch (e) {
            console.warn('Failed to update shell_defense visual', e, data);
          }
          resolve();
          break;
        }
        case "shell_defense_exit":
        case "shell_exit": {
          try {
            const sourceId = data.source || data.entity_id || data.entity_uid;
            if (sourceId && typeof PersistentEffects !== 'undefined' && PersistentEffects.updateForEntity) {
              PersistentEffects.updateForEntity(sourceId);
            }
          } catch (e) {
            console.warn('Failed to update shell_defense exit visual', e, data);
          }
          resolve();
          break;
        }
        case "prone_enter":
        case "prone": {
          try {
            const sourceId = data.source || data.entity_id || data.entity_uid;
            if (sourceId && typeof PersistentEffects !== 'undefined' && PersistentEffects.updateForEntity) {
              PersistentEffects.updateForEntity(sourceId);
            }
          } catch (e) {
            console.warn('Failed to update prone visual', e, data);
          }
          resolve();
          break;
        }
        case "prone_exit": {
          try {
            const sourceId = data.source || data.entity_id || data.entity_uid;
            if (sourceId && typeof PersistentEffects !== 'undefined' && PersistentEffects.updateForEntity) {
              PersistentEffects.updateForEntity(sourceId);
            }
          } catch (e) {
            console.warn('Failed to update prone exit visual', e, data);
          }
          resolve();
          break;
        }
        case "dm_notes_changed": {
          try {
            if (window.DmNotes && typeof window.DmNotes.reload === 'function') {
              window.DmNotes.reload(data && data.map);
            }
          } catch (e) {
            console.warn('dm_notes_changed failed', e);
          }
          resolve();
          break;
        }
        case "notebook_changed": {
          try {
            if (window.Notebook && typeof window.Notebook.reload === 'function') {
              window.Notebook.reload();
            }
          } catch (e) {
            console.warn('notebook_changed failed', e);
          }
          resolve();
          break;
        }
        case "notebook_share": {
          try {
            if (window.Notebook && typeof window.Notebook.showShared === 'function') {
              window.Notebook.showShared(data);
            }
          } catch (e) {
            console.warn('notebook_share failed', e);
          }
          resolve();
          break;
        }
        case "notebook_map_links_changed": {
          try {
            if (window.Notebook && typeof window.Notebook.reloadMapLinks === 'function') {
              window.Notebook.reloadMapLinks(data && data.map);
            }
          } catch (e) {
            console.warn('notebook_map_links_changed failed', e);
          }
          resolve();
          break;
        }
        default:
          console.log("Unknown message type:", data.type);
          resolve();
      }
    });
  }

  processMapEvent(data, resolve) {
    const { message: map_url, width, height } = data;
    const image_offset_px = data.image_offset_px || [0, 0];
    const offsetX = Number(image_offset_px[0]) || 0;
    const offsetY = Number(image_offset_px[1]) || 0;
    $(".tiles-container").data({ width, height });
    $(".image-container img")
      .attr("src", map_url)
      .css({ width: `${width}px`, objectFit: 'cover', objectPosition: 'top' });
    const tile_size = parseInt($(".tiles-container").data("tile-size"), 10) || 0;
    // Match index.html / Utils.updateMapDisplay: offset on tiles only, not the image container.
    $(".image-container").css({
      height: `${height}px`,
      top: tile_size,
      left: tile_size,
    });
    $("#tiles-area").css({
      top: -tile_size - offsetY,
      left: -tile_size - offsetX,
    });
    const canvas = document.querySelector("canvas");
    canvas.width = width + tile_size;
    canvas.height = height + tile_size;
    // Schedule a tile refresh as a queued event to keep ordering
    try {
      eventQueue.enqueue({ type: 'refresh_tiles', message: { is_setup: false, pov: false, x: 0, y: 0, entity_uid: null } });
    } catch (_) {
      try { Utils.refreshTileSet(); } catch (_) { }
    }
    resolve();
  }

  processConversationEvent(data, resolve) {
    deliverConversationPayload(data.message, resolve);
  }

  processMoveEvent(data, resolve) {
    // Entity occupancy changes after any move; clear local path caches now so
    // the next local path query sees updated blockers.
    invalidateMovementPlanningCaches();
    if (!data.message || !data.message.animation_log) {
      // Legacy move event without animation_log — just refresh the map
      try {
        Utils.refreshTileSet(false, false, 0, 0, null, () => { resolve(); });
      } catch (_) { resolve(); }
      return;
    }
    const animationBuffer = data.message.animation_log;
    // Track originals hidden during movement so we only restore them after
    // a server tile refresh confirms final positions.
    const hiddenOriginals = new Map(); // entity_uid -> jQuery element
    const movedEntities = new Set();
    // Track entities whose tiles are missing to avoid repeated retries and warnings
    const missingEntities = new Set();
    const warnedMissingEntities = new Set();
    const refreshedEntities = new Set();
    const lastTargetCoords = new Map(); // entity_uid -> [x, y]
    const tileSize = parseInt($('.tiles-container').data('tile-size') || 64, 10);

    const tileForCoords = (x, y) => $(`.tile[data-coords-x="${x}"][data-coords-y="${y}"]`);
    const isTileVisibleToViewer = ($tile) => !!($tile && $tile.length && $tile.find('.fog-of-war').length === 0);
    const visiblePathSegment = (pathPoints) => {
      if (!Array.isArray(pathPoints) || pathPoints.length === 0) {
        return [];
      }

      const segment = [];
      let started = false;
      for (const point of pathPoints) {
        if (!Array.isArray(point) || point.length < 2) {
          continue;
        }
        const $tile = tileForCoords(point[0], point[1]);
        const isVisible = isTileVisibleToViewer($tile);
        if (isVisible) {
          segment.push(point);
          started = true;
        } else if (started) {
          break;
        }
      }

      return segment;
    };
    const buildSyntheticSprite = (moveMeta) => {
      if (!moveMeta || moveMeta.type !== 'move' || !moveMeta.token_image) {
        return null;
      }

      const tokenSize = Number(moveMeta.token_size) > 0 ? Number(moveMeta.token_size) : 1;
      const spriteSize = tileSize * tokenSize;
      const spriteCss = {
        position: 'absolute',
        zIndex: 2000,
        pointerEvents: 'none',
        width: `${spriteSize}px`,
        height: `${spriteSize}px`,
        transformOrigin: 'center center'
      };
      if (moveMeta.transform) {
        spriteCss.transform = String(moveMeta.transform).trim();
      }

      const $sprite = $('<img class="moving-entity-sprite" />')
        .attr('src', `/assets/${moveMeta.token_image}`)
        .css(spriteCss);

      return {
        $sprite,
        spriteW: spriteSize,
        spriteH: spriteSize,
        synthetic: true,
        $original: null
      };
    };

    const mountMovementSprite = ($sprite) => {
      const $container = getMovementSpriteContainer();
      if ($container.length) {
        $container.append($sprite);
      } else {
        $('body').append($sprite);
      }
    };

    const restoreHiddenOriginals = () => {
      try {
        hiddenOriginals.forEach(($el) => {
          try { $el.css('visibility', ''); } catch (_) { }
        });
        hiddenOriginals.clear();
      } catch (_) { }
    };

    const finishMoveAnimation = (done) => {
      cleanupMovementSprites();
      restoreHiddenOriginals();
      try { $('.add-to-target, .add-to-turn-order').hide(); } catch (_) { }
      try { if (typeof done === 'function') done(); } catch (_) { }
    };

    const refreshAfterMove = (done) => {
      try {
        // forceFullRefresh bypasses tile diffing; is_setup must stay false so
        // /update does not render battle-setup "add to initiative" plus buttons.
        Utils.refreshTileSet(false, false, 0, 0, null, () => {
          try {
            // Non-battle move events can arrive slightly before server-side
            // loop_environment side-effects settle. Reconcile once more
            // after a short delay to avoid transient disappear/snap states.
            setTimeout(() => {
              Utils.refreshTileSet(false, false, 0, 0, null, () => {
                finishMoveAnimation(done);
              }, true);
            }, 140);
          } catch (_) {
            finishMoveAnimation(done);
          }
        }, true);
      } catch (_) {
        finishMoveAnimation(done);
      }
    };

    const animateFunction = (animationLog, idx) => {
      if (idx >= animationLog.length) {
        console.log('Animation sequence complete, refreshing tile set');
        movedEntities.forEach((entityUid) => restorePovPerceptionQuickAction(entityUid));
        try {
          // Keep movement sprites visible at destination until tile refresh
          // finishes so the token does not vanish during a slow /update.
          refreshAfterMove(() => {
            try { if (window.PersistentEffects && PersistentEffects.applyAll) PersistentEffects.applyAll(); } catch (e) { }
            resolve();
          });
        } catch (e) {
          console.error('Failed to refresh tile set after animations, continuing', e);
          finishMoveAnimation();
          try { if (window.PersistentEffects && PersistentEffects.applyAll) PersistentEffects.applyAll(); } catch (_) { }
          resolve();
        }
        return;
      }
      const entry = animationLog[idx];
      // Skip non-array entries (e.g., perception objects) safely
      if (!Array.isArray(entry)) {
        const t = (entry && entry.type) || (entry && entry.message && entry.message.type);
        if (t === 'spell') {
          this.processSpellEvent(entry, () => animateFunction(animationLog, idx + 1));
        } else if (t === 'attack') {
          this.processAttackEvent(entry, () => animateFunction(animationLog, idx + 1));
        } else if (t === 'message_toaster') {
          try {
            const msg = (entry && entry.message !== undefined) ? entry.message : entry;
            const text = (typeof msg === 'string') ? msg : (msg && (msg.text || msg.message || msg.msg)) || '';
            const position = (typeof msg === 'string')
              ? (entry && (entry.position || entry.pos || entry.coords))
              : (msg && (msg.position || msg.pos || msg.coords));
            const source = (typeof msg === 'string')
              ? (entry && (entry.source || entry.entity_id))
              : (msg && (msg.source || msg.entity_id));
            showMapToast(text, position, source, 10000);
          } catch (e) { console.warn('Inline message_toaster failed', e, entry); }
          // Continue immediately
          animateFunction(animationLog, idx + 1);
        } else if (t === 'message_spell_link') {
          try {
            const text = (entry && entry.message) || 'Message link opened. Whisper in Local Chat.';
            showMapToast(text, null, entry && entry.source, 12000);
            if (window.Chat && typeof Chat.openMessageSpellLink === 'function') {
              Chat.openMessageSpellLink(entry);
            }
          } catch (e) { console.warn('Inline message_spell_link failed', e, entry); }
          animateFunction(animationLog, idx + 1);
        } else if (t === 'contextual_sound') {
          try {
            showContextualSound(
              entry && entry.message,
              entry && (entry.position || entry.pos || entry.coords),
              entry && (entry.source || entry.entity_id),
              entry && entry.duration_ms,
              entry && entry.label,
            );
          } catch (e) { console.warn('Inline contextual_sound failed', e, entry); }
          animateFunction(animationLog, idx + 1);
        } else if (t === 'damage_number') {
          try {
            showDamageNumber(entry);
          } catch (e) { console.warn('Inline damage_number failed', e, entry); }
          animateFunction(animationLog, idx + 1);
        } else {
          // Unknown inline event; skip
          console.warn('Skipping unknown inline animation entry:', t);
          animateFunction(animationLog, idx + 1);
        }
        return;
      }
      const [entity_uid, path, action] = entry;
      movedEntities.add(entity_uid);

      // Record the last intended position for this entry so we can request a targeted refresh if needed
      if (Array.isArray(path) && path.length > 0) {
        const last = path[path.length - 1];
        if (Array.isArray(last) && last.length === 2) {
          lastTargetCoords.set(entity_uid, last);
        }
      }

      // If we've already established this entity has no tile in the DOM for this event,
      // skip quickly without retrying again to reduce noise and wasted time.
      if (missingEntities.has(entity_uid)) {
        animateFunction(animationLog, idx + 1);
        return;
      }

      const startForTile = ($tile) => {
        const visiblePath = visiblePathSegment(path);
        if (!visiblePath.length) {
          animateFunction(animationLog, idx + 1);
          return;
        }

        if (action && action.target) {
          // Check if both source and target tiles exist before drawing action line
          const $targetTile = $(`.tile[data-coords-id="${action.target}"]`);
          if ($tile.length > 0 && $targetTile.length > 0) {
            // const opts = {
            //   lineWidth: 3,
            //   withArrow: true,
            //   randomCurve: true,
            //   strokeStyle: action.type === "attack" ? "red" : "blue",
            //   text: action.label,
            // };
            // drawLine(
            //   globalCtx,
            //   { x: $tile.data("coords-x"), y: $tile.data("coords-y") },
            //   `.tile[data-coords-id="${action.target}"]`,
            //   opts,
            // );
          } else {
            console.warn('Action line drawing skipped: source or target tile not found', {
              entity_uid,
              target: action.target,
              sourceFound: $tile.length > 0,
              targetFound: $targetTile.length > 0
            });
          }
        }

        // Find the visible token image inside the tile (handles flying wrapper or direct .npc)
        const $origImg = $tile.find('.entity').first();
        let spriteInfo = null;
        if ($origImg.length) {
          spriteInfo = {
            $sprite: $origImg.clone().addClass('moving-entity-sprite'),
            spriteW: $origImg.width() || tileSize,
            spriteH: $origImg.height() || tileSize,
            synthetic: false,
            $original: $origImg
          };
        } else {
          spriteInfo = buildSyntheticSprite(action);
        }

        if (!spriteInfo || !spriteInfo.$sprite || !spriteInfo.$sprite.length) {
          console.warn('No entity image found to animate for entity', entity_uid);
          // Nothing to animate visually; proceed to next entry
          animateFunction(animationLog, idx + 1);
          return;
        }

        const { $sprite, spriteW, spriteH, synthetic, $original } = spriteInfo;

        const centerToTopLeft = ($t) => getTilePositionInContainer($t, spriteW, spriteH);

        if (!synthetic) {
          $sprite.css({
            position: 'absolute',
            zIndex: 2000,
            pointerEvents: 'none',
            transformOrigin: 'center center'
          });
        }

        // Hide the original during animation to avoid duplicates
        if (!synthetic && $original && $original.length) {
          try {
            $original.css('visibility', 'hidden');
            hiddenOriginals.set(entity_uid, $original);
          } catch (_) { }
        }

        // Mount the sprite at the initial tile center
        const moveFunc = (p, index) => {
          if (index >= p.length) {
            // Leave the sprite on the overlay until refresh completes.
            animateFunction(animationLog, idx + 1);
            return;
          }
          const [x, y] = p[index];
          const $newTile = tileForCoords(x, y);

          // Check if the target tile exists
          if (!$newTile.length) {
            console.warn('Target tile not found, skipping move step:', { x, y, entity_uid });
            moveFunc(p, index + 1);
            return;
          }
          const tl = centerToTopLeft($newTile);
          if (!tl) { moveFunc(p, index + 1); return; }

          // Set initial sprite position on first step
          if (index === 0) {
            try { mountMovementSprite($sprite); } catch (_) { }
            $sprite.css({ left: tl.left, top: tl.top });
            // Continue to next step to actually animate
            moveFunc(p, index + 1);
            return;
          }

          // Animate sprite to the next tile center
          $sprite.css('transition', 'none');
          requestAnimationFrame(() => {
            $sprite.css({
              left: tl.left,
              top: tl.top,
              transition: 'left 0.3s ease-in-out, top 0.3s ease-in-out'
            });

            let advanced = false;
            let timeoutId = null;
            const advanceOnce = () => {
              if (advanced) return;
              advanced = true;
              if (timeoutId !== null) {
                try { clearTimeout(timeoutId); } catch (_) { }
              }
              moveFunc(p, index + 1);
            };
            const onEnd = (e) => {
              // We rely on timeout fallback as some browsers may not emit for both properties
              $sprite.off('transitionend', onEnd);
              advanceOnce();
            };
            $sprite.on('transitionend', onEnd);
            timeoutId = setTimeout(() => {
              try { $sprite.off('transitionend', onEnd); } catch (_) { }
              advanceOnce();
            }, 350);
          });
        };

        // Begin movement sequence
        if (visiblePath.length > 0) {
          moveFunc(visiblePath, 0);
        } else {
          // No path to animate, restore hidden original and continue
          try {
            if (hiddenOriginals.has(entity_uid)) {
              hiddenOriginals.get(entity_uid).css('visibility', '');
              hiddenOriginals.delete(entity_uid);
            }
          } catch (_) { }
          animateFunction(animationLog, idx + 1);
        }
      };

      // Try to resolve the entity tile with a brief retry window to tolerate DOM refresh/map switches
      const tryResolveTile = (attemptsLeft = 6) => {
        // Primary: tile is indexed by coords-id matching entity uid (usual case)
        let $tile = getEntityTile(entity_uid);
        // Fallback: find the entity DOM and climb to its containing tile
        if (!$tile.length) {
          const $entityNode = $(`.entity[data-id="${entity_uid}"]`).closest('.tile');
          if ($entityNode && $entityNode.length) {
            $tile = $entityNode;
          }
        }
        if ($tile.length) {
          startForTile($tile);
        } else if (attemptsLeft > 0) {
          setTimeout(() => tryResolveTile(attemptsLeft - 1), 100);
        } else {
          if (action && action.type === 'move' && action.token_image) {
            startForTile($());
            return;
          }

          if (!warnedMissingEntities.has(entity_uid)) {
            console.warn('Entity tile not found; attempting server render for animation:', entity_uid);
            warnedMissingEntities.add(entity_uid);
          }

          // Try to fetch a server-rendered map fragment and synthesize a sprite to animate
          const attemptServerSprite = (() => {
            let attempted = false;
            return () => {
              if (attempted) return;
              attempted = true;
              try {
                // Request a partial map render; we'll extract the entity token image
                $.get('/update', { entity_uid: entity_uid, pov: false, is_setup: false, x: 0, y: 0 })
                  .done((html) => {
                    try {
                      const $tmp = $('<div></div>').html(html);
                      // Prefer an <img.entity> source; fallback to data-attr if present
                      let spriteSrc = null;
                      const $img = $tmp.find(`.tile[data-coords-id="${entity_uid}"] .entity`).first();
                      if ($img && $img.length) {
                        spriteSrc = $img.attr('src') || $img.data('src') || $img.attr('data-src') || null;
                      }
                      // If we still don't have a source, try a conventional token path guess
                      if (!spriteSrc) {
                        spriteSrc = `/assets/token_${entity_uid}.png`;
                      }

                      // If no path is available or empty, we cannot animate; skip
                      if (!Array.isArray(path) || path.length === 0) {
                        animateFunction(animationLog, idx + 1);
                        return;
                      }

                      // Build a floating sprite and animate along the path using tile centers
                      const tileSize = parseInt($('.tiles-container').data('tile-size') || 64, 10);
                      const $sprite = $('<img class="moving-entity-sprite" />')
                        .attr('src', spriteSrc)
                        .css({ position: 'absolute', zIndex: 2000, pointerEvents: 'none', width: `${tileSize}px`, height: `${tileSize}px`, transformOrigin: 'center center' });

                      const moveFuncNoOrig = (p, index) => {
                        if (index >= p.length) {
                          try { $sprite.remove(); } catch (_) { }
                          animateFunction(animationLog, idx + 1);
                          return;
                        }
                        const [nx, ny] = p[index];
                        const $t = $(`.tile[data-coords-x="${nx}"][data-coords-y="${ny}"]`);
                        if (!$t.length) { moveFuncNoOrig(p, index + 1); return; }
                        const tl = getTilePositionInContainer($t, tileSize, tileSize);
                        if (!tl) { moveFuncNoOrig(p, index + 1); return; }

                        if (index === 0) {
                          try { mountMovementSprite($sprite); } catch (_) { }
                          $sprite.css({ left: tl.left, top: tl.top });
                          moveFuncNoOrig(p, index + 1);
                          return;
                        }

                        $sprite.css('transition', 'none');
                        requestAnimationFrame(() => {
                          $sprite.css({ left: tl.left, top: tl.top, transition: 'left 0.3s ease-in-out, top 0.3s ease-in-out' });
                          let advanced = false;
                          let timeoutId = null;
                          const advanceOnce = () => {
                            if (advanced) return;
                            advanced = true;
                            if (timeoutId !== null) {
                              try { clearTimeout(timeoutId); } catch (_) { }
                            }
                            moveFuncNoOrig(p, index + 1);
                          };
                          const onEnd = () => {
                            $sprite.off('transitionend', onEnd);
                            advanceOnce();
                          };
                          $sprite.on('transitionend', onEnd);
                          timeoutId = setTimeout(() => {
                            try { $sprite.off('transitionend', onEnd); } catch (_) { }
                            advanceOnce();
                          }, 350);
                        });
                      };

                      moveFuncNoOrig(path, 0);
                    } catch (e) {
                      console.warn('Failed to animate with server-rendered sprite', e);
                      animateFunction(animationLog, idx + 1);
                    }
                  })
                  .fail(() => {
                    // As a last resort, request a targeted tile refresh then continue
                    if (!refreshedEntities.has(entity_uid)) {
                      const last = lastTargetCoords.get(entity_uid);
                      if (last && Array.isArray(last)) {
                        try { enqueueTileRefresh({ pov: true, x: last[0], y: last[1], entity_uid }); refreshedEntities.add(entity_uid); } catch (_) { }
                      }
                    }
                    animateFunction(animationLog, idx + 1);
                  });
              } catch (e) {
                console.warn('Error requesting server render for entity', entity_uid, e);
                animateFunction(animationLog, idx + 1);
              }
            };
          })();

          // Kick off the server-sprite attempt
          // attemptServerSprite();

          // Mark this entity as missing so subsequent entries in the same event skip noisy retries
          missingEntities.add(entity_uid);
          animateFunction(animationLog, idx + 1);
        }
      };

      tryResolveTile();
    };

    if (animationBuffer && animationBuffer.length > 0) {
      animateFunction(animationBuffer, 0);
    } else {
      // No animation; still apply effects and continue
      try { if (window.PersistentEffects && PersistentEffects.applyAll) PersistentEffects.applyAll(); } catch (e) { }
      resolve();
    }
  }

  // Render animated spell effects (e.g., Bless) and resolve when finished
  processSpellEvent(data, resolve) {
    try {
      const msg = data && data.message ? data.message : data;
      const spellKey = (msg && (msg.spell || msg.label)) || '';
      const spellKeyNorm = String(spellKey || '').toLowerCase().replace(/\s+/g, '_');
      const refreshAfter = (cb) => {
        try {
          // Targeted refresh for source and all targets to update effect icons and concentration
          const targets = Array.isArray(msg && msg.target) ? msg.target : (msg && msg.target ? [msg.target] : []);
          const ids = [];
          if (msg && msg.source) ids.push(msg.source);
          targets.forEach(t => { if (t != null) ids.push(t); });
          // If we have entity ids, do one refresh; server can choose to optimize by entity
          const done = () => { try { if (window.PersistentEffects && PersistentEffects.applyAll) PersistentEffects.applyAll(); } catch (e) { } if (typeof cb === 'function') cb(); };
          if (ids.length > 1) {
            // Multiple affected entities; do a full refresh to update all icons/portraits
            Utils.refreshTileSet(false, false, 0, 0, null, done);
          } else if (ids.length === 1) {
            // Targeted refresh for a single entity
            Utils.refreshTileSet(false, true, 0, 0, ids[0], done);
          } else {
            Utils.refreshTileSet(false, false, 0, 0, null, done);
          }
        } catch (e) {
          try { Utils.refreshTileSet(false, false, 0, 0, null, () => { try { if (window.PersistentEffects && PersistentEffects.applyAll) PersistentEffects.applyAll(); } catch (e) { } if (typeof cb === 'function') cb(); }); } catch (_) { if (typeof cb === 'function') cb(); }
        }
      };
      if ((spellKeyNorm === 'look' || spellKeyNorm === 'perception') && msg && msg.source) {
        playPerceptionCheckFeedback(msg.source, msg.perception_roll, msg.perception_roll_label)
          .then(() => refreshAfter(resolve))
          .catch(() => refreshAfter(resolve));
        return;
      }
      const finishSpell = () => {
        try { playDamageEvents(msg && msg.damage_events); } catch (_) { }
        refreshAfter(resolve);
      };
      if (window.SpellEffects && typeof window.SpellEffects.play === 'function') {
        window.SpellEffects.play(spellKey, msg).then(() => { finishSpell(); }).catch(() => { finishSpell(); });
        return;
      }
      // Fallback: log only if SpellEffects registry isn’t loaded
      console.log('Casting spell (no SpellEffects registry found):', msg);
      try { finishSpell(); } catch (e) { resolve(); }
    } catch (e) {
      console.warn('processSpellEvent failed', e);
      try { Utils.refreshTileSet(false, false, 0, 0, null, () => { try { if (window.PersistentEffects && PersistentEffects.applyAll) PersistentEffects.applyAll(); } catch (e) { } try { resolve(); } catch (_) { } }); } catch (_) { try { resolve(); } catch (__) { } }

    }
  }

  // Render animated attack effects (melee/ranged) and resolve when finished
  processAttackEvent(data, resolve) {
    try {
      const msg = data && data.message ? data.message : data;
      const finishAttack = () => {
        try { playDamageEvents(msg && msg.damage_events); } catch (_) { }
        try { if (window.PersistentEffects && PersistentEffects.applyAll) PersistentEffects.applyAll(); } catch (e) { }
        resolve();
      };
      if (window.SpellEffects && typeof window.SpellEffects.play === 'function') {
        window.SpellEffects.play('attack', msg).then(() => { finishAttack(); }).catch(() => { finishAttack(); });
        return;
      }
      console.log('Attack animation (no SpellEffects registry found):', msg);
      finishAttack();
    } catch (e) {
      console.warn('processAttackEvent failed', e);
      try { resolve(); } catch (_) { }
    }
  }

  processStopTrackEvent(data, resolve) {
    if (!active_background_sound) { resolve(); return; }

    // Simple fade-out using element volume, then stop and resolve
    let steps = 10;
    const originalVolume = active_background_sound.volume;
    const stepDown = originalVolume / steps;
    const interval = setInterval(() => {
      if (!active_background_sound) { clearInterval(interval); resolve(); return; }
      const nextVol = Math.max(0, active_background_sound.volume - stepDown);
      active_background_sound.volume = nextVol;
      if (nextVol <= 0) {
        clearInterval(interval);
        try { active_background_sound.pause(); } catch (e) { }
        active_background_sound = null;
        active_track_id = -1;

        // Update DM Sound Manager state
        if (typeof DMSoundManager !== 'undefined') {
          DMSoundManager.currentTrackId = "-1";
          DMSoundManager.isPlaying = false;
          if ($('#modal-1').hasClass('in') || $('#modal-1').is(':visible')) {
            DMSoundManager.updateUI();
          }
        }
        resolve();
      }
    }, 50);
  }
}

// Create global event queue instance
const eventQueue = new EventQueue();

const JournalNotifications = {
  summary: { total_unread: 0, characters: {} },

  refresh() {
    return fetch('/journal/unread_summary', { credentials: 'same-origin' })
      .then((r) => r.json())
      .then((data) => {
        this.summary = data || { total_unread: 0, characters: {} };
        this.render();
        return this.summary;
      })
      .catch(() => {
        this.summary = { total_unread: 0, characters: {} };
        this.render();
        return this.summary;
      });
  },

  render() {
    const total = this.summary.total_unread || 0;
    const $menuItem = $('#journal-notification-item');
    const $badge = $('#journal-menu-unread-badge');
    const $menuBtn = $('#expand-menu');
    $menuItem.show();

    if (total > 0) {
      $badge.text(String(total)).addClass('visible');
      $menuBtn.addClass('has-journal-unread');
    } else {
      $badge.text('0').removeClass('visible');
      $menuBtn.removeClass('has-journal-unread');
    }

    $('.journal-unread-dot').each(function () {
      const uid = $(this).data('entity-uid');
      const unread = uid && JournalNotifications.summary.characters[uid]
        ? JournalNotifications.summary.characters[uid].unread
        : 0;
      if (unread > 0) {
        $(this).prop('hidden', false).attr('title', `${unread} unread journal entr${unread === 1 ? 'y' : 'ies'}`);
      } else {
        $(this).prop('hidden', true).removeAttr('title');
      }
    });
  },

  openFirstUnread() {
    const chars = this.summary.characters || {};
    const keys = Object.keys(chars);
    if (!keys.length) return;
    const pick = keys.sort((a, b) => (chars[b].unread || 0) - (chars[a].unread || 0))[0];
    if (pick) {
      window.open(`info?id=${encodeURIComponent(pick)}`, '_blank');
    }
  },
};
window.JournalNotifications = JournalNotifications;

// Helper to request a tile refresh through the EventQueue
function enqueueTileRefresh(opts = {}) {
  try {
    eventQueue.enqueue({ type: 'refresh_tiles', message: opts });
  } catch (e) {
    // Fallback to direct call if queue not available
    try {
      Utils.refreshTileSet(
        !!opts.is_setup,
        !!opts.pov,
        opts.x || 0,
        opts.y || 0,
        opts.entity_uid || null,
        typeof opts.callback === 'function' ? opts.callback : null,
        !!opts.force_full_refresh
      );
    } catch (_) { }
  }
}

// Helper: show an on-map "toaster" message near a tile coordinate or an entity id
function isDmUser() {
  return ($('body').attr('data-role') || '').indexOf('dm') >= 0;
}

function isDamageTargetVisibleToPov(targetUid, position) {
  if (isDmUser()) {
    return true;
  }
  let $tile = $();
  if (targetUid) {
    $tile = $(`.tile[data-coords-id="${targetUid}"]`);
  }
  if ((!$tile || !$tile.length) && Array.isArray(position) && position.length >= 2) {
    $tile = $(`.tile[data-coords-x="${position[0]}"][data-coords-y="${position[1]}"]`);
  }
  if (!$tile || !$tile.length) {
    return false;
  }
  return $tile.find('.fog-of-war').length === 0;
}

function damageNumberLabel(event) {
  const amount = event && event.amount;
  if (amount == null) {
    return '';
  }
  const suffix = event.critical ? '!' : '';
  return `-${amount}${suffix}`;
}

function mountFloatingCombatNumber(event, options = {}) {
  const kind = options.kind || 'damage';
  const value = options.value;
  if (value == null || value === '') {
    return;
  }

  const mountAtTile = ($tile) => {
    const anchor = getTileAnchorInFxLayer($tile);
    if (!anchor) {
      return;
    }
    const $layer = ensureMapFxLayer();
    const $anchor = $('<div class="floating-combat-number-anchor"></div>').css({
      position: 'absolute',
      left: `${anchor.left}px`,
      top: `${anchor.top}px`,
      transform: 'translate(-50%, -50%)',
      zIndex: kind === 'hit' ? 2101 : 2102,
    });
    const $num = $('<div class="floating-combat-number"></div>');
    if (kind === 'hit') {
      $num.addClass('is-hit-roll');
    } else {
      $num.addClass('damage-number');
      if (event.critical) {
        $num.addClass('is-critical');
      }
      if (event.half) {
        $num.addClass('is-half');
      }
      if (event.damage_type) {
        $num.addClass(`dtype-${String(event.damage_type).toLowerCase().replace(/[^a-z0-9_-]+/g, '-')}`);
      }
    }
    $num.text(String(value));
    $anchor.append($num);
    $layer.append($anchor);
    setTimeout(() => {
      try { $anchor.remove(); } catch (_) { }
    }, options.durationMs || 1400);
  };

  const resolveTile = () => {
    if (event.target) {
      const $byEntity = $(`.tile[data-coords-id="${event.target}"]`);
      if ($byEntity.length) {
        return $byEntity;
      }
    }
    if (Array.isArray(event.position) && event.position.length >= 2) {
      const $byPos = $(`.tile[data-coords-x="${event.position[0]}"][data-coords-y="${event.position[1]}"]`);
      if ($byPos.length) {
        return $byPos;
      }
    }
    return $();
  };

  const attemptMount = (attemptsLeft = 8) => {
    const $tile = resolveTile();
    if ($tile && $tile.length) {
      mountAtTile($tile);
      return;
    }
    if (attemptsLeft <= 0) {
      return;
    }
    try {
      if (event.target) {
        enqueueTileRefresh({ is_setup: false, pov: true, x: 0, y: 0, entity_uid: event.target });
      } else if (Array.isArray(event.position) && event.position.length >= 2) {
        enqueueTileRefresh({
          is_setup: false,
          pov: true,
          x: event.position[0],
          y: event.position[1],
        });
      }
    } catch (_) { }
    setTimeout(() => attemptMount(attemptsLeft - 1), 120);
  };

  attemptMount();
}

function showDamageNumber(event) {
  if (!event || event.amount == null || event.amount <= 0) {
    return;
  }
  if (!isDamageTargetVisibleToPov(event.target, event.position)) {
    return;
  }

  if (event.attack_roll_total != null) {
    mountFloatingCombatNumber(event, { kind: 'hit', value: event.attack_roll_total, durationMs: 1100 });
    setTimeout(() => {
      mountFloatingCombatNumber(event, {
        kind: 'damage',
        value: damageNumberLabel(event),
        durationMs: 1400,
      });
    }, 120);
    return;
  }

  mountFloatingCombatNumber(event, { kind: 'damage', value: damageNumberLabel(event), durationMs: 1400 });
}

function playDamageEvents(damageEvents, staggerMs = 110) {
  if (!Array.isArray(damageEvents) || !damageEvents.length) {
    return;
  }
  damageEvents.forEach((evt, idx) => {
    setTimeout(() => showDamageNumber(evt), idx * staggerMs);
  });
}
window.playDamageEvents = playDamageEvents;

function showPerceptionRollToast(entityUid, rollTotal, rollLabel) {
  if (rollTotal == null && !rollLabel) {
    return;
  }
  const text = rollLabel ? String(rollLabel) : `Perception ${rollTotal}`;
  showMapToast(text, null, entityUid, 3800, {
    className: 'map-toast perception-check-toast',
    prefix: '👁',
  });
}

function playPerceptionCheckFeedback(entityUid, rollTotal, rollLabel) {
  if (!entityUid) {
    return Promise.resolve();
  }
  const key = `${entityUid}:${rollTotal == null ? '' : rollTotal}:${rollLabel || ''}`;
  const now = Date.now();
  if (window.__n20_lastPerceptionFeedbackKey === key && now - (window.__n20_lastPerceptionFeedbackAt || 0) < 800) {
    return Promise.resolve();
  }
  window.__n20_lastPerceptionFeedbackKey = key;
  window.__n20_lastPerceptionFeedbackAt = now;
  const payload = {
    source: entityUid,
    spell: 'look',
    perception_roll: rollTotal,
    perception_roll_label: rollLabel,
  };
  showPerceptionRollToast(entityUid, rollTotal, rollLabel);
  if (window.SpellEffects && typeof window.SpellEffects.play === 'function') {
    return window.SpellEffects.play('look', payload);
  }
  return Promise.resolve();
}
window.playPerceptionCheckFeedback = playPerceptionCheckFeedback;

function showMapToast(text, position, sourceEntityId = null, durationMs = 10000, options = null) {
  try {
    const opts = (options && typeof options === 'object') ? options : {};
    const dur = typeof durationMs === 'number' && durationMs > 0 ? durationMs : 10000;
    const safeText = (text == null) ? '' : String(text);
    const expiryTs = Date.now() + dur;
    const extraClass = opts.className || '';
    const prefix = opts.prefix || '';

    const resolveTile = () => {
      // 1) By explicit grid position [x, y] or {x, y}
      let x = null, y = null;
      if (Array.isArray(position) && position.length >= 2) {
        x = position[0]; y = position[1];
      } else if (position && typeof position === 'object' && position.x != null && position.y != null) {
        x = position.x; y = position.y;
      }
      if (x != null && y != null) {
        const $t = $(`.tile[data-coords-x="${x}"][data-coords-y="${y}"]`);
        if ($t.length) return $t;
      }
      // 2) Fallback by source entity tile
      if (sourceEntityId) {
        const $t2 = $(`.tile[data-coords-id="${sourceEntityId}"]`);
        if ($t2.length) return $t2;
      }
      return $();
    };

    const mountToastAtTile = ($tile) => {
      try {
        // If a previous toast exists on the same tile, remove it to avoid stacking
        const $existing = $tile.find('.map-toast');
        if ($existing.length) { try { $existing.remove(); } catch (_) { } }

        const $toast = $('<div class="map-toast"></div>');
        if (extraClass) $toast.addClass(extraClass);
        if (prefix) {
          $('<span class="map-toast-prefix"></span>').text(prefix).appendTo($toast);
        }
        $('<span class="map-toast-text"></span>').text(safeText).appendTo($toast);
        $toast.attr('data-toast-expiry', expiryTs);
        if (extraClass) $toast.attr('data-toast-class', extraClass);
        // Position above the tile center
        $toast.css({ position: 'absolute', left: '50%', top: '-6px', transform: 'translate(-50%, -100%)' });
        $tile.append($toast);
        // Auto-remove after duration
        setTimeout(() => { try { $toast.fadeOut(400, () => $toast.remove()); } catch (_) { } }, dur);
      } catch (e) { console.warn('Failed to mount map toast on tile', e); }
    };

    const attemptMount = (attemptsLeft = 6) => {
      const $tile = resolveTile();
      if ($tile && $tile.length) { mountToastAtTile($tile); return; }
      if (attemptsLeft <= 0) { return; }
      try {
        // Try a targeted refresh if we have explicit coords
        if (Array.isArray(position) && position.length >= 2) {
          enqueueTileRefresh({ is_setup: false, pov: true, x: position[0], y: position[1], entity_uid: sourceEntityId || undefined });
        } else if (position && typeof position === 'object' && position.x != null && position.y != null) {
          enqueueTileRefresh({ is_setup: false, pov: true, x: position.x, y: position.y, entity_uid: sourceEntityId || undefined });
        } else if (sourceEntityId) {
          enqueueTileRefresh({ is_setup: false, pov: true, x: 0, y: 0, entity_uid: sourceEntityId });
        }
      } catch (_) { }
      setTimeout(() => attemptMount(attemptsLeft - 1), 120);
    };

    attemptMount();
  } catch (e) {
    console.warn('showMapToast failed', e, { text, position, sourceEntityId, durationMs });
  }
}

function showContextualSound(text, position, sourceEntityId = null, durationMs = 4000, label = null) {
  const prefix = label ? `${label}: ` : '♪ ';
  showMapToast(text, position, sourceEntityId, durationMs || 4000, {
    className: 'contextual-sound',
    prefix,
  });
}

// Add debug panel for monitoring event queue
function createEventQueueDebugPanel() {
  const debugPanel = $(`
    <div id="event-queue-debug" style="position: fixed; top: 10px; right: 10px; 
         background: rgba(0,0,0,0.8); color: white; padding: 10px; 
         border-radius: 5px; font-family: monospace; font-size: 12px; 
         z-index: 9999; display: none;">
      <div><strong>Event Queue Status</strong></div>
      <div>Queue Length: <span id="queue-length">0</span></div>
      <div>Processing: <span id="processing-status">false</span></div>
      <div>Total Processed: <span id="processed-count">0</span></div>
      <div>
        <button onclick="eventQueue.setDebugMode(true)">Enable Debug</button>
        <button onclick="eventQueue.setDebugMode(false)">Disable Debug</button>
        <button onclick="toggleEventQueueDebug()">Hide</button>
      </div>
    </div>
  `);

  $('body').append(debugPanel);
}

function updateEventQueueDebugPanel() {
  const status = eventQueue.getStatus();
  $('#queue-length').text(status.queueLength);
  $('#processing-status').text(status.processing);
  $('#processed-count').text(status.processedCount);
}

function toggleEventQueueDebug() {
  $('#event-queue-debug').toggle();
}

// Update debug panel every 100ms when visible
setInterval(() => {
  if ($('#event-queue-debug').is(':visible')) {
    updateEventQueueDebugPanel();
  }
}, 100);

// Add keyboard shortcut to toggle debug panel (Ctrl+Shift+Q)
$(document).keydown(function (e) {
  if (e.ctrlKey && e.shiftKey && e.keyCode === 81) { // Q key
    if ($('#event-queue-debug').length === 0) {
      createEventQueueDebugPanel();
    }
    toggleEventQueueDebug();
  }
});

// Global functions used by EventQueue and other components
const refreshTurnOrder = () => {
  Utils.ajaxGet("/turn_order", {}, (data) => {
    $("#turn-order").html(data);
    $("#battle-turn-order").show();
  });
};

const refreshTurn = () => {
  Utils.ajaxGet("/turn", {}, (data) => {
    $(".game-turn-container").html(data).show();
  });
};

// Global variables used by EventQueue and other components
let active_background_sound = null;
let active_track_id = -1;
let pendingSoundtrack = null;
let backgroundSoundStartTime = null;
let pageRenderTime = null;

const switchPOV = (entity_uid, canvas) => {
  // Close all interactive UI elements before switching characters
  if (Utils && Utils.closeAllInteractiveElements) {
    Utils.closeAllInteractiveElements();
  } else {
    // Fallback for older versions or if Utils is not available
    $(".popover-menu, .popover-menu-2").hide();
    $('#targetSelectionModal').modal('hide');
    targetMode = multiTargetMode = moveMode = coneMode = aoePointTargetMode = false;
    $(".add-to-target").hide();
    $(".highlighted").removeClass("highlighted");
    $(".target-selection, .active-selection").hide();
    $(".die-roll-component:visible").hide();
    if (globalCanvas && globalCtx) {
      globalCtx.clearRect(0, 0, globalCanvas.width, globalCanvas.height);
    }
  }

  ajaxPost("/switch_pov", { entity_uid }, (data) => {
    console.log("Switched POV:", data);
    // Determine if the map is changing - we need this for both background update and tile refresh
    const currentMap = $('body').attr('data-current-map');
    const isMapChange = !currentMap || (data.name && data.name !== currentMap);
    console.log("Map change detection:", { currentMap, newMap: data.name, isMapChange, hasBackground: !!data.background });
    
    // Always update map display if background data is present - the server knows best
    if (data.background) {
      Utils.updateMapDisplay(data, canvas);
      // Re-apply persistent status overlays after map display update
      try { if (window.PersistentEffects && window.PersistentEffects.applyAll) window.PersistentEffects.applyAll(); } catch (e) { }
      // Apply map-default effects if provided and DM has no active override.
      // If no default and no DM override, clear any previous effects.
      try {
        if (!data.dm_active && typeof Effects !== 'undefined') {
          var arr = Array.isArray(data.map_default_effects) ? data.map_default_effects.slice() : [];
          if (!arr.length && data.map_default_effect) arr = [data.map_default_effect];
          if (arr.length && Effects.applyEffect) {
            arr = arr.map(function (p) { try { if (p && typeof p === 'object' && p.exclusive === undefined) p.exclusive = false; } catch (e) { } return p; });
            Effects.applyEffect(arr);
          } else if (Effects.stopAll) {
            Effects.stopAll();
          }
        }
      } catch (e) { console.warn('Failed to apply/clear effect on POV switch', e); }
      // Ask server to (re)send effects after map switch in case client missed anything
      try { if (typeof socket !== 'undefined' && socket && socket.emit) socket.emit('request_effects'); } catch (e) { }
      // Re-apply persistent status overlays on map change
      try { if (window.PersistentEffects && PersistentEffects.applyAll) PersistentEffects.applyAll(); } catch (e) { }
    }
    // update the pov entity id in the body data
    $('body').attr('data-pov-entity', data.pov_entity);
    // Full refresh on map change; preserve battle-setup plus buttons when active.
    Utils.refreshTileSet(
      battle_setup,
      true,
      0,
      0,
      entity_uid,
      () => {
        // Clean up any pending move ghosts and reset tile positioning to prevent artifacts
        try { if (typeof cleanupAllPendingMoves === 'function') cleanupAllPendingMoves(); } catch (e) { }
        // Avoid resetting .tile positioning globally; some layouts rely on absolute positioning per tile
        // If needed, specific animated tiles should clear transition in the animation handler itself
        try { updateDraggableEntityClasses(); } catch (e) { }
        scheduleViewportCenterOnEntity(entity_uid, { resetZoom: isMapChange });
        try { if (window.PersistentEffects && PersistentEffects.applyAll) PersistentEffects.applyAll(); } catch (e) { }
      },
      isMapChange
    );
  });
};

const ajaxPost = (url, data, onSuccess, isJSON = false) => {
  const isMoveActionRequest = (
    url === '/action'
    && data
    && typeof data === 'object'
    && data.action === 'MoveAction'
  );

  const buildMoveRequestFingerprint = (payload) => {
    if (!payload || typeof payload !== 'object') {
      return null;
    }
    const entityId = String(payload.id || '');
    const path = Array.isArray(payload.path) ? payload.path : [];
    const jumps = Array.isArray(payload.manual_jump) ? payload.manual_jump : [];
    const stackDescent = payload.stack_descent || null;
    try {
      return JSON.stringify([entityId, path, jumps, stackDescent]);
    } catch (_) {
      return `${entityId}:${path.length}`;
    }
  };

  // Global move-request guard to prevent accidental duplicate submits from
  // overlapping input flows (click, keyboard, hover quick-action).
  if (!window.__n20MovePostGuard) {
    window.__n20MovePostGuard = {
      byEntity: new Map(),
      DUP_WINDOW_MS: 900,
    };
  }

  let guardEntityId = null;
  let guardFingerprint = null;
  if (isMoveActionRequest) {
    guardEntityId = String(data.id || '');
    guardFingerprint = buildMoveRequestFingerprint(data);

    if (guardEntityId && guardFingerprint) {
      const guard = window.__n20MovePostGuard;
      const now = Date.now();
      const state = guard.byEntity.get(guardEntityId) || {
        inFlight: false,
        fingerprint: null,
        lastSentAt: 0,
      };

      const duplicateInFlight = state.inFlight && state.fingerprint === guardFingerprint;
      const duplicateRapid = (
        !state.inFlight
        && state.fingerprint === guardFingerprint
        && (now - state.lastSentAt) < guard.DUP_WINDOW_MS
      );

      if (duplicateInFlight || duplicateRapid) {
        console.warn('Suppressed duplicate MoveAction POST', {
          entity: guardEntityId,
          duplicateInFlight,
          duplicateRapid,
          pathLen: Array.isArray(data.path) ? data.path.length : 0,
        });
        return;
      }

      guard.byEntity.set(guardEntityId, {
        inFlight: true,
        fingerprint: guardFingerprint,
        lastSentAt: now,
      });
    }
  }

  $.ajax({
    url,
    type: "POST",
    data: isJSON ? JSON.stringify(data) : data,
    contentType: isJSON
      ? "application/json"
      : "application/x-www-form-urlencoded",
    success: (resp) => {
      if (isMoveActionRequest && guardEntityId) {
        const guard = window.__n20MovePostGuard;
        const state = guard.byEntity.get(guardEntityId);
        if (state && state.fingerprint === guardFingerprint) {
          state.inFlight = false;
          state.lastSentAt = Date.now();
          guard.byEntity.set(guardEntityId, state);
        }
      }
      if (typeof onSuccess === 'function') {
        onSuccess(resp);
      }
    },
    error: (jqXHR, textStatus, errorThrown) => {
      if (isMoveActionRequest && guardEntityId) {
        const guard = window.__n20MovePostGuard;
        const state = guard.byEntity.get(guardEntityId);
        if (state && state.fingerprint === guardFingerprint) {
          state.inFlight = false;
          state.lastSentAt = Date.now();
          guard.byEntity.set(guardEntityId, state);
        }
      }
      console.error(`Error with POST ${url}:`, textStatus, errorThrown);
    },
  });
};

function resolveActionStepType(data) {
  if (!data || typeof data !== 'object') {
    return null;
  }
  const param0 = Array.isArray(data.param) && data.param.length > 0 ? data.param[0] : null;
  return (param0 && param0.type) || data.type || null;
}

function continueActionFromResponse(entity_uid, action, opts, coordsx, coordsy, data) {
  if (data && data.status === 'ok') {
    if (data.pickpocket_attempt && data.pickpocket_attempt.success === false) {
      const attempt = data.pickpocket_attempt;
      const attemptMessage =
        attempt.message ||
        (attempt.roll_total != null
          ? `Sleight of Hand ${attempt.roll_total} vs passive Insight ${attempt.passive_insight} — failed`
          : 'Pickpocket attempt failed.');
      if (attemptMessage && typeof Utils !== "undefined" && Utils.showToast) {
        Utils.showToast(attemptMessage);
      }
    }
    $(".popover-menu").hide();
    closeCenterActionBar();
    $("#modal-1").modal("hide");
    refreshTurn();
    return;
  }
  if (data && resolveActionStepType(data)) {
    if (typeof window.__n20_handleAction === 'function') {
      window.__n20_handleAction(entity_uid, action, opts, coordsx, coordsy, data);
      return;
    }
    console.warn('continueActionFromResponse: handleAction not ready', data);
  }
  if (data && data.status === 'error') {
    const message = data.primary_error
      || data.error
      || data.message
      || (data.error_messages && data.error_messages.length ? data.error_messages[0] : null)
      || (data.errors && data.errors.join(', '));
    if (message && typeof Utils !== 'undefined' && Utils.showToast) {
      Utils.showToast(message);
    } else if (message) {
      console.warn('Action failed:', message);
    }
  }
  if (data && data.status === 'multiple_targets') {
    const target_list = data.entities || [];
    const targets = target_list.map(([label, target_uid]) => ({
      id: target_uid,
      name: label,
      type: 'entity',
      image: null,
    }));
    showTargetSelectionModal(
      targets,
      { x: coordsx, y: coordsy },
      { id: entity_uid, action, opts: opts || {} },
    );
    return;
  }
  refreshTurn();
}

// DM Sound Manager - Enhanced soundtrack control for DMs
// DM Sound Manager - Enhanced soundtrack control for DMs
const DMSoundManager = {
  // Store volume preferences for each track
  trackVolumes: {},
  currentTrackId: null,
  currentTrackDuration: 0,
  currentTrackTime: 0,
  isPlaying: false,
  progressInterval: null,

  // Load track volumes from localStorage
  loadTrackVolumes: function () {
    const saved = localStorage.getItem('dmTrackVolumes');
    if (saved) {
      try {
        this.trackVolumes = JSON.parse(saved);
      } catch (e) {
        console.warn('Failed to load track volumes:', e);
        this.trackVolumes = {};
      }
    }
  },

  // Save track volumes to localStorage
  saveTrackVolumes: function () {
    try {
      localStorage.setItem('dmTrackVolumes', JSON.stringify(this.trackVolumes));
    } catch (e) {
      console.warn('Failed to save track volumes:', e);
    }
  },

  // Get saved volume for a track (default 50)
  getTrackVolume: function (trackId) {
    return this.trackVolumes[trackId] || 50;
  },

  // Set volume for a track
  setTrackVolume: function (trackId, volume) {
    this.trackVolumes[trackId] = volume;
    this.saveTrackVolumes();
  },

  // Switch to a track (one-click switching)
  switchToTrack: function (trackId) {
    if (trackId === this.currentTrackId && this.isPlaying) {
      // Same track and playing - toggle pause
      this.togglePlayPause();
      return;
    }

    // Different track or resuming - switch to it
    const volume = this.getTrackVolume(trackId);

    // Send track switch request to server
    ajaxPost(
      "/sound",
      { track_id: trackId },
      (data) => {
        console.log("Track switched successfully:", data);
        this.currentTrackId = trackId;
        this.isPlaying = trackId !== "-1";

        // Reset time tracking
        this.currentTrackTime = 0;
        this.currentTrackDuration = 0; // Will be set from UI or server response if available

        // Update global state
        active_track_id = trackId;

        // If stopping music, clear the audio
        if (trackId === "-1") {
          if (active_background_sound) {
            active_background_sound.pause();
            active_background_sound = null;
          }
          this.stopProgressUpdater();
        } else {
          // Find track duration from DOM if possible
          const trackItem = $(`.track-item[data-track-id="${trackId}"]`);
          // This assumes the server provides duration in some way, or we default to 0
          // For now, we rely on the updateUI to set max from server rendered template initially, 
          // but we should probably fetch it or store it. 
          // Ideally the server response 'data' or the initial template execution has it.
          // We'll rely on the socket 'track' message to update exact details usually, 
          // but here we just start the counter.
          this.startProgressUpdater();
        }

        this.updateUI();
      },
      true
    );

    // Also send volume if not stopping
    if (trackId !== "-1") {
      setTimeout(() => {
        ajaxPost(
          "/volume",
          { volume: volume },
          () => console.log("Volume set for track:", trackId, volume),
          true
        );
      }, 100);
    }
  },

  seekTo: function (time) {
    this.currentTrackTime = time;
    ajaxPost(
      "/seek",
      { time: time },
      (data) => {
        console.log("Seeked to:", time);
        this.updateUI();
      },
      true
    );
  },

  startProgressUpdater: function () {
    this.stopProgressUpdater();
    this.progressInterval = setInterval(() => {
      if (this.isPlaying) {
        this.currentTrackTime++;
        // Clamp to duration if known
        if (this.currentTrackDuration > 0 && this.currentTrackTime > this.currentTrackDuration) {
          this.currentTrackTime = this.currentTrackDuration;
        }
        this.updateSeekUI();
      }
    }, 1000);
  },

  stopProgressUpdater: function () {
    if (this.progressInterval) {
      clearInterval(this.progressInterval);
      this.progressInterval = null;
    }
  },

  // Toggle play/pause for current track
  togglePlayPause: function () {
    if (this.currentTrackId && this.currentTrackId !== "-1") {
      if (this.isPlaying) {
        // Pause - stop the track
        ajaxPost(
          "/sound",
          { track_id: "-1" },
          (data) => {
            console.log("Track paused successfully:", data);
            // Keep track ID but mark as not playing (paused state)
            this.isPlaying = false;
            this.stopProgressUpdater();
            this.updateUI();
          },
          true
        );
      } else {
        // Resume - play the track again
        const volume = this.getTrackVolume(this.currentTrackId);
        ajaxPost(
          "/sound",
          { track_id: this.currentTrackId },
          (data) => {
            console.log("Track resumed successfully:", data);
            this.isPlaying = true;
            this.startProgressUpdater();
            this.updateUI();
          },
          true
        );

        // Set volume
        setTimeout(() => {
          ajaxPost(
            "/volume",
            { volume: volume },
            () => console.log("Volume restored for resumed track:", volume),
            true
          );
        }, 100);
      }
    }
  },

  // Stop current track
  stopTrack: function () {
    ajaxPost(
      "/sound",
      { track_id: "-1" },
      (data) => {
        console.log("Track stopped successfully:", data);
        this.currentTrackId = "-1";
        this.isPlaying = false;
        active_track_id = -1;
        this.stopProgressUpdater();

        if (active_background_sound) {
          active_background_sound.pause();
          active_background_sound = null;
        }

        this.updateUI();
      },
      true
    );
  },

  // Update UI elements
  updateUI: function () {
    const currentDisplay = $('#current-track-display');
    const playBtn = $('.play-btn');
    const pauseBtn = $('.pause-btn');
    const stopBtn = $('.stop-btn');
    const volumeSection = $('.volume-controls');
    const seekSection = $('.track-seek-container');

    // Update current track display
    if (this.currentTrackId && this.currentTrackId !== "-1") {
      const trackItem = $(`.track-item[data-track-id="${this.currentTrackId}"]`);
      const trackName = trackItem.find('.track-name').text().replace('🎵 ', '');
      currentDisplay.text(trackName);

      // Show/hide playback controls
      if (this.isPlaying) {
        playBtn.hide();
        pauseBtn.show();
        stopBtn.show();
        volumeSection.show();
        seekSection.show();
      } else {
        playBtn.show();
        pauseBtn.hide();
        stopBtn.hide();
        volumeSection.hide();
        // keep seek section visible if paused? Maybe. Let's hide it if stopped, show if paused.
        // Logic above was "if isPlaying". If paused, isPlaying is false.
        // But we might want to see seekbar when paused to seek before resuming.
        // For now, follow existing strict "if isPlaying" toggle for other controls, 
        // but maybe we should show it if track selected?
        // Let's verify existing behavior: pauseBtn.hide() when paused.
        // Actually, when paused, we usually want to see the controls to Resume (Play btn).
        // If simply paused, currentTrackId is still set.

        // Re-evaluating: 'isPlaying' is toggle between Play/Pause buttons.
        // If paused, we show Play button.
        // We SHOULD show volume and seek controls even if paused, as long as a track is active (currentTrackId != -1).
        if (this.currentTrackId && this.currentTrackId !== "-1") {
          volumeSection.show();
          seekSection.show();
        } else {
          volumeSection.hide();
          seekSection.hide();
        }
      }
    } else {
      currentDisplay.text('None');
      playBtn.hide();
      pauseBtn.hide();
      stopBtn.hide();
      volumeSection.hide();
      seekSection.hide();
    }

    // Update track list highlighting
    $('.track-item').removeClass('active-track');
    if (this.currentTrackId) {
      $(`.track-item[data-track-id="${this.currentTrackId}"]`).addClass('active-track');
    }

    // Update volume displays
    this.updateVolumeDisplays();

    // Initialize seekbar if not already handled
    const $seekbar = $('#track-seekbar');
    if ($seekbar.length > 0) {
      $seekbar.off('change input').on('change', function () {
        DMSoundManager.seekTo($(this).val());
      });

      // Update local state from DOM if we just opened modal or reloaded
      if (this.currentTrackDuration === 0) {
        const max = parseInt($seekbar.attr('max')) || 0;
        this.currentTrackDuration = max;
      }

      // Also update the value visually
      this.updateSeekUI();
    }
  },

  updateSeekUI: function () {
    const $seekbar = $('#track-seekbar');
    const $timeDisplay = $('#track-current-time');
    if ($seekbar.length) {
      $seekbar.val(this.currentTrackTime);
      const mins = Math.floor(this.currentTrackTime / 60);
      const secs = Math.floor(this.currentTrackTime % 60);
      $timeDisplay.text(`${mins}:${secs < 10 ? '0' : ''}${secs}`);
    }
  },

  // Update volume displays for all tracks
  updateVolumeDisplays: function () {
    $('.track-item').each((index, element) => {
      const $element = $(element);
      const trackId = $element.data('track-id');
      if (trackId && trackId !== "-1") {
        const volume = this.getTrackVolume(trackId);
        $element.find('.track-vol-display').text(volume);
      }
    });
  },

  // Set volume for current track
  setCurrentVolume: function (volume) {
    if (this.currentTrackId && this.currentTrackId !== "-1") {
      this.setTrackVolume(this.currentTrackId, volume);

      // Send to server
      ajaxPost(
        "/volume",
        { volume: volume },
        () => console.log("Volume updated for current track:", volume),
        true
      );

      // Update display
      $('.volume-display').text(volume + '%');
      this.updateVolumeDisplays();
    }
  },

  // Initialize the sound manager
  init: function () {
    this.loadTrackVolumes();

    // Check DOM for initial state
    const $activeTrack = $('.active-track');
    if ($activeTrack.length) {
      this.currentTrackId = $activeTrack.data('track-id');
      // If the pause button is visible, it means we are playing
      if ($activeTrack.find('.pause-btn').is(':visible')) {
        this.isPlaying = true;
      }
    } else {
      // If no active track class, check if ANY track shows pause button (just in case)
      const $playingTrack = $('.pause-btn:visible').closest('.track-item');
      if ($playingTrack.length) {
        this.currentTrackId = $playingTrack.data('track-id');
        this.isPlaying = true;
      }
    }

    // Check global state as fallback
    if (!this.currentTrackId && typeof active_track_id !== 'undefined' && active_track_id !== -1) {
      this.currentTrackId = active_track_id;
      this.isPlaying = true;
    }

    const $seekbar = $('#track-seekbar');
    if ($seekbar.length) {
      this.currentTrackDuration = parseInt($seekbar.attr('max')) || 0;
      this.currentTrackTime = parseInt($seekbar.val()) || 0;

      // If we have an active track and it appears to be playing
      if (this.currentTrackId && this.isPlaying) {
        this.startProgressUpdater();
      }

      // Attach listener immediately
      $seekbar.off('change input').on('change', function () {
        DMSoundManager.seekTo($(this).val());
      });

      // Initial UI update to sync everything
      this.updateSeekUI();
    }

    // Ensure global active_track_id is synced
    if (this.currentTrackId) {
      active_track_id = this.currentTrackId;
    }

    this.updateUI();
  }
};

$(document).ready(function () {
  DMSoundManager.init();
});

// Plays a background sound (stopping any previous one).
const playPendingSoundtrackIfAny = () => {
  if (!pendingSoundtrack) {
    return false;
  }
  const pending = pendingSoundtrack;
  pendingSoundtrack = null;
  playSound(pending.url, pending.track_id, pending.volume, pending.time_override);
  return true;
};

const playSound = (url, track_id, volume, time_override = null) => {
  const elapsed = (Date.now() - pageRenderTime) / 1000;
  var seekTime;
  if (time_override !== null) {
    seekTime = time_override;
  } else {
    seekTime = backgroundSoundStartTime + elapsed;
  }

  if (active_background_sound) active_background_sound.pause();

  active_background_sound = new Audio(`/assets/${url}`);
  active_background_sound.loop = true;
  active_background_sound.currentTime = seekTime;
  active_background_sound.volume = volume ? volume / 100 : 0.5;
  active_track_id = track_id;
  const playAttempt = active_background_sound.play();
  if (playAttempt && typeof playAttempt.catch === 'function') {
    playAttempt.catch((error) => {
      console.warn('Background soundtrack autoplay blocked; will retry on next interaction', error);
      pendingSoundtrack = { url, track_id, volume, time_override: seekTime };
    });
  }
  $("body").attr('data-soundtrack-url', url);
  $("body").attr('data-soundtrack-id', track_id);
  $("body").attr('data-soundtrack-volume', volume || 50);
  $(".volume-slider").val(active_background_sound.volume * 100);

  // Update DM Sound Manager state
  if (typeof DMSoundManager !== 'undefined') {
    DMSoundManager.currentTrackId = track_id;
    DMSoundManager.isPlaying = true;
  }

  // Apply user volume control if available (for non-DM users)
  if (typeof UserVolumeControl !== 'undefined') {
    setTimeout(() => UserVolumeControl.applyUserVolume(), 100);
  }
};

// User Volume Control System (for non-DM users)
const UserVolumeControl = {
  // Get user's saved volume preference (0-100)
  getUserVolume: function () {
    const saved = localStorage.getItem('userMusicVolume');
    return saved !== null ? parseInt(saved) : 70; // Default to 70%
  },

  // Save user's volume preference
  setUserVolume: function (volume) {
    localStorage.setItem('userMusicVolume', volume.toString());
  },

  // Get user's mute preference
  getUserMuted: function () {
    return localStorage.getItem('userMusicMuted') === 'true';
  },

  // Save user's mute preference
  setUserMuted: function (muted) {
    localStorage.setItem('userMusicMuted', muted.toString());
  },

  // Apply user volume on top of DM volume
  applyUserVolume: function () {
    if (active_background_sound) {
      const dmVolume = parseFloat($('body').attr('data-soundtrack-volume')) || 50;
      const userVolume = this.getUserVolume();
      const isMuted = this.getUserMuted();

      // Calculate effective volume: DM volume * user volume multiplier
      const effectiveVolume = isMuted ? 0 : (dmVolume / 100) * (userVolume / 100);
      active_background_sound.volume = Math.max(0, Math.min(1, effectiveVolume));

      // Update widget display
      this.updateWidget();
    }
  },

  // Update the music control widget
  updateWidget: function () {
    const widget = $('#music-control-widget');
    const userVolume = this.getUserVolume();
    const isMuted = this.getUserMuted();
    const trackName = $('body').attr('data-soundtrack-id') || 'No music playing';

    // Update track name
    $('#current-track-name').text(trackName);

    // Update volume slider
    $('#user-volume-slider').val(userVolume);
    $('#volume-display').text(userVolume + '%');

    // Update mute button
    const muteIcon = $('#mute-icon');
    const muteText = $('#mute-text');
    if (isMuted) {
      muteIcon.removeClass('glyphicon-volume-up').addClass('glyphicon-volume-off');
      muteText.text('Unmute');
      $('#mute-toggle').addClass('btn-warning').removeClass('btn-default');
    } else {
      muteIcon.removeClass('glyphicon-volume-off').addClass('glyphicon-volume-up');
      muteText.text('Mute');
      $('#mute-toggle').removeClass('btn-warning').addClass('btn-default');
    }

    // Show widget if music is playing
    if (active_background_sound && trackName !== 'No music playing') {
      widget.show().addClass('fade-in');
    } else {
      widget.hide();
    }
  },

  // Initialize the user volume control system
  init: function () {
    const self = this;

    // Set up volume slider
    $('#user-volume-slider').on('input', function () {
      const volume = parseInt($(this).val());
      self.setUserVolume(volume);
      self.applyUserVolume();
    });

    // Set up mute toggle
    $('#mute-toggle').on('click', function () {
      const isMuted = !self.getUserMuted();
      self.setUserMuted(isMuted);
      self.applyUserVolume();
    });

    // Set up widget collapse/expand
    $('#toggle-music-widget').on('click', function () {
      $('#music-control-widget').toggleClass('collapsed');
    });

    // Initialize widget state
    this.updateWidget();

    // Apply saved volume if music is already playing
    if (active_background_sound) {
      this.applyUserVolume();
    }
  }
};

// Returns the center coordinates of a tile element (document/page space).
// Use for viewport-fixed overlays (canvas, etc.). Token movement sprites use
// getTilePositionInContainer instead so they stay aligned during pan/zoom.
const getTileCenter = ($tile) => {
  if (typeof $tile === 'string') {
    $tile = $($tile);
  }
  if (!$tile.length || !$tile[0] || !$tile[0].getBoundingClientRect) {
    console.warn('getTileCenter: invalid tile or tile not found', $tile);
    return null;
  }
  const rect = $tile[0].getBoundingClientRect();
  const scrollLeft = window.pageXOffset || document.documentElement.scrollLeft;
  const scrollTop = window.pageYOffset || document.documentElement.scrollTop;
  // Use rect.width/height to get the actual displayed center (accounts for zoom)
  return {
    x: rect.left + scrollLeft + rect.width / 2,
    y: rect.top + scrollTop + rect.height / 2
  };
};

// Overlay inside .image-container so sprites survive $('.tiles-container').html(...).
const ensureMovementSpriteLayer = () => {
  const $mapRoot = $('.image-container').first();
  if (!$mapRoot.length) {
    return $('.tiles-container').first();
  }
  let $layer = $mapRoot.children('#movement-sprite-layer');
  if (!$layer.length) {
    $layer = $('<div id="movement-sprite-layer"></div>').css({
      position: 'absolute',
      left: 0,
      top: 0,
      width: '100%',
      height: '100%',
      pointerEvents: 'none',
      zIndex: 1500,
      overflow: 'visible'
    });
    $mapRoot.append($layer);
  }
  return $layer;
};

// Persistent overlay for floating combat numbers (survives tile refresh).
const ensureMapFxLayer = () => {
  const $mapRoot = $('.image-container').first();
  if (!$mapRoot.length) {
    return $('.tiles-container').first();
  }
  let $layer = $mapRoot.children('#map-fx-layer');
  if (!$layer.length) {
    $layer = $('<div id="map-fx-layer"></div>').css({
      position: 'absolute',
      left: 0,
      top: 0,
      width: '100%',
      height: '100%',
      pointerEvents: 'none',
      zIndex: 2100,
      overflow: 'visible',
    });
    $mapRoot.append($layer);
  }
  return $layer;
};

function getTileAnchorInFxLayer($tile) {
  if (!$tile || !$tile.length || !$tile[0] || !$tile[0].getBoundingClientRect) {
    return null;
  }
  const $layer = ensureMapFxLayer();
  if (!$layer.length || !$layer[0].getBoundingClientRect) {
    return null;
  }
  const tileRect = $tile[0].getBoundingClientRect();
  const layerRect = $layer[0].getBoundingClientRect();
  if (tileRect.width <= 0 || layerRect.width <= 0) {
    return null;
  }
  return {
    left: tileRect.left - layerRect.left + tileRect.width / 2,
    top: tileRect.top - layerRect.top + tileRect.height / 2,
  };
}

// Parent for movement sprites — must live inside the panned/zoomed map tree.
const getMovementSpriteContainer = () => ensureMovementSpriteLayer();

// Tile top-left for sprites (layer coords under .image-container).
const getTilePositionInContainer = ($tile, spriteW, spriteH) => {
  if (typeof $tile === 'string') {
    $tile = $($tile);
  }
  if (!$tile.length) {
    return null;
  }
  const tileSize = parseInt($('.tiles-container').data('tile-size') || 64, 10);
  const halfW = (spriteW || tileSize) / 2;
  const halfH = (spriteH || tileSize) / 2;

  const tileEl = $tile[0];
  const $spriteContainer = getMovementSpriteContainer();
  if (tileEl && tileEl.getBoundingClientRect && $spriteContainer.length) {
    const tileRect = tileEl.getBoundingClientRect();
    const containerRect = $spriteContainer[0].getBoundingClientRect();
    if (tileRect.width > 0 && containerRect.width > 0) {
      return {
        left: tileRect.left - containerRect.left + tileRect.width / 2 - halfW,
        top: tileRect.top - containerRect.top + tileRect.height / 2 - halfH,
      };
    }
  }

  let x = Number($tile.data('coords-x'));
  let y = Number($tile.data('coords-y'));
  if (!Number.isFinite(x) || !Number.isFinite(y)) {
    return null;
  }
  const stackOff = Utils.getStackCompositeOffset ? Utils.getStackCompositeOffset() : { active: false, x: 0, y: 0 };
  if (stackOff.active && $tile.closest('.map-stack-overlay').length) {
    x += stackOff.x;
    y += stackOff.y;
  }
  const pos = {
    left: x * tileSize + tileSize + tileSize / 2 - halfW,
    top: y * tileSize + tileSize + tileSize / 2 - halfH
  };
  const $tilesContainer = $('.tiles-container');
  const $tiles = ($tilesContainer && typeof $tilesContainer.first === 'function') ? $tilesContainer.first() : $tilesContainer;
  if ($tiles.length && typeof $tiles.position === 'function') {
    const offset = $tiles.position();
    if (offset && typeof offset === 'object') {
      pos.left += offset.left || 0;
      pos.top += offset.top || 0;
    }
  }
  return pos;
};

const cleanupMovementSprites = () => {
  try {
    $('.moving-entity-sprite').remove();
    $('.tile .entity, .tile .npc').css('visibility', '');
  } catch (_) { }
};

// Adds or removes an entity from the battle initiative.
function addToInitiative($btn, battleEntityList) {
  const { id, name } = $btn.data();
  const index = battleEntityList.findIndex((entity) => entity.id === id);

  if (index === -1) {
    battleEntityList.push({ id, group: "a", name });
    Utils.ajaxGet("/add", { id }, (data) => {
      $("#turn-order").append(data);
      $btn
        .find("i.glyphicon")
        .removeClass("glyphicon-plus")
        .addClass("glyphicon-minus");
      $btn.css("background-color", "red");
    });
  } else {
    battleEntityList.splice(index, 1);
    $btn
      .find("i.glyphicon")
      .removeClass("glyphicon-minus")
      .addClass("glyphicon-plus");
    $btn.css("background-color", "green");
    $(".turn-order-item")
      .filter((_, el) => $(el).text() === name)
      .remove();
  }
}

// Draws a line from a source tile to a target tile. Options let you customize
// the line width, stroke color, whether to add an arrowhead, use a curved path, etc.
function drawLine(ctx, from, to, opts = {}) {
  const {
    lineWidth = 5,
    withArrow = false,
    randomCurve = false,
    strokeStyle = "green",
    text = null,
  } = opts;

  // Ensure from and to are objects with x and y
  // If to is a selector string, convert to jQuery object and extract x/y
  let fromCoords = from;
  let toCoords = to;
  if (typeof from === 'string') {
    const $fromTile = $(from);
    fromCoords = { x: $fromTile.data('coords-x'), y: $fromTile.data('coords-y') };
  }
  if (typeof to === 'string') {
    const $toTile = $(to);
    toCoords = { x: $toTile.data('coords-x'), y: $toTile.data('coords-y') };
  }

  // Now get centers
  const fromCenter = getTileCenter($(`.tile[data-coords-x="${fromCoords.x}"][data-coords-y="${fromCoords.y}"]`));
  const toCenter = getTileCenter($(`.tile[data-coords-x="${toCoords.x}"][data-coords-y="${toCoords.y}"]`));

  // Check if we successfully got tile centers
  if (!fromCenter || !toCenter) {
    console.warn('drawLine: Could not find valid tile centers', { fromCoords, toCoords });
    return;
  }

  ctx.save();
  ctx.lineWidth = lineWidth;
  ctx.strokeStyle = strokeStyle;

  const dx = toCenter.x - fromCenter.x;
  const dy = toCenter.y - fromCenter.y;
  let angle = Math.atan2(dy, dx);

  // Draw the main line
  ctx.beginPath();
  if (randomCurve) {
    // Create a nice curve by offsetting control point along the perpendicular
    const midX = (fromCenter.x + toCenter.x) / 2;
    const midY = (fromCenter.y + toCenter.y) / 2;
    const len = Math.hypot(dx, dy) || 1;
    // Perpendicular unit vector
    const nx = -dy / len;
    const ny = dx / len;
    // Offset magnitude: 25% of segment length with slight randomness
    const mag = Math.max(20, len * (0.25 + Math.random() * 0.1));
    const dir = Math.random() < 0.5 ? -1 : 1;
    const controlX = midX + nx * mag * dir;
    const controlY = midY + ny * mag * dir;
    ctx.moveTo(fromCenter.x, fromCenter.y);
    ctx.quadraticCurveTo(controlX, controlY, toCenter.x, toCenter.y);
  } else {
    ctx.moveTo(fromCenter.x, fromCenter.y);
    ctx.lineTo(toCenter.x, toCenter.y);
  }

  // Draw arrow if needed
  if (withArrow) {
    const headlen = 10;
    ctx.moveTo(toCenter.x, toCenter.y);
    ctx.lineTo(
      toCenter.x - headlen * Math.cos(angle - Math.PI / 6),
      toCenter.y - headlen * Math.sin(angle - Math.PI / 6),
    );
    ctx.moveTo(toCenter.x, toCenter.y);
    ctx.lineTo(
      toCenter.x - headlen * Math.cos(angle + Math.PI / 6),
      toCenter.y - headlen * Math.sin(angle + Math.PI / 6),
    );
  }

  ctx.stroke();

  if (text !== null) {
    const textOffset = 15;
    const textX = toCenter.x + textOffset * Math.cos(angle);
    const textY =
      toCenter.y +
      (toCenter.y > fromCenter.y ? 1 : -1) * textOffset * Math.sin(angle);
    ctx.fillStyle = strokeStyle;
    ctx.font = "16px Arial";
    ctx.textAlign = "center";
    ctx.fillText(text, textX, textY);
  }
}

// Sends a simple command via Socket.IO.
function command(cmd) {
  socket.emit('message', {
    type: "command",
    user: username,
    message: { action: "command", command: cmd }
  });
}

// Centers the viewport on a given tile and (optionally) highlights it.
function centerOnTile(tile, highlight = false) {
  const $tile = (tile && typeof tile.jquery !== 'undefined') ? tile : $(tile);
  if (!$tile.length) return;

  const viewportCoords = Utils.viewportCoordsFromTile
    ? Utils.viewportCoordsFromTile($tile)
    : null;
  const tileX = viewportCoords ? viewportCoords.x : $tile.data('coords-x');
  const tileY = viewportCoords ? viewportCoords.y : $tile.data('coords-y');

  $(".tile .entity").removeClass("focus-highlight");
  $tile.find(".entity").addClass("focus-highlight");

  if (centerViewportOnTileElement($tile)) {
    // DOM-based centering (stack overlays, anchors, offsets).
  } else if (tileX !== undefined && tileY !== undefined) {
    centerViewportOnTile(tileX, tileY);
  }
  
  // Visual feedback
  if (typeof $tile.fadeOut === 'function') {
    $tile.fadeOut(150).fadeIn(150);
  }
  if (highlight) {
    $(".tile").removeClass("focus-highlight-red");
    if (typeof $tile.addClass === 'function') {
      $tile.addClass("focus-highlight-red");
    }
  }
}

const centerOnTileXY = (x, y, highlight = false) => {
  const $tile = $(`.tile[data-coords-x="${x}"][data-coords-y="${y}"]`);
  centerOnTile($tile, highlight);
};

const centerOnEntityId = (id) => {
  centerOnTile(getEntityTile(id));
};

// Function to show target selection modal
const showTargetSelectionModal = (targets, position, actionData = null) => {
  const $modal = $('#targetSelectionModal');
  const $targetList = $('#targetList');

  // Clear previous content
  $targetList.empty();

  // Add target options
  targets.forEach((target, index) => {
    // Image filenames returned by the server (e.g. "token_crysania.png",
    // "footlocker.png") are bare names served from /assets/. Avoid
    // double-prefixing if the value is already an absolute path or URL.
    let imageSrc = '';
    if (target.image) {
      if (/^(https?:)?\/\//i.test(target.image) || target.image.startsWith('/')) {
        imageSrc = target.image;
      } else {
        imageSrc = `/assets/${target.image}`;
      }
    }
    const $targetOption = $(`
      <div class="target-option" data-target-id="${target.id}" data-target-type="${target.type}">
        <img class="target-image" src="${imageSrc}" alt="${target.name}" style="${!imageSrc ? 'display: none;' : ''}">
        <div class="target-info">
          <div class="target-name">${target.name}</div>
          <div class="target-type">${target.type}</div>
        </div>
      </div>
    `);

    $targetOption.on('click', function () {
      // Remove selection from all options
      $('.target-option').removeClass('selected');
      // Add selection to clicked option
      $(this).addClass('selected');
    });

    $targetList.append($targetOption);
  });

  // Handle modal confirmation
  $modal.off('click', '.btn-primary').on('click', '.btn-primary', function () {
    const $selectedTarget = $('.target-option.selected');
    if ($selectedTarget.length > 0) {
      const targetId = $selectedTarget.data('target-id');
      const targetType = $selectedTarget.data('target-type');

      // Close modal
      $modal.modal('hide');

      if (actionData) {
        // This is a multiple target case - call the action again with the specific target
        const newActionData = { ...actionData };
        if (targetType === 'position') {
          // For position targets, use coordinates
          newActionData.target = { x: position.x, y: position.y };
        } else {
          // For entity/object targets, use the target ID
          newActionData.target = targetId;
        }

        // Call the action again with the specific target
        ajaxPost("/action", newActionData, (data) => {
          continueActionFromResponse(
            actionData.id,
            actionData.action,
            actionData.opts || {},
            position.x,
            position.y,
            data,
          );
        }, true);
      } else {
        // This is a regular target mode case
        if (targetType === 'position') {
          // For position targets, use coordinates
          targetModeCallback({ x: position.x, y: position.y });
        } else {
          // For entity/object targets, use the target ID
          targetModeCallback({ target_id: targetId, x: position.x, y: position.y });
        }
      }

      // Reset target mode
      exitTargetMode();
    }
  });

  // Handle modal cancellation
  $modal.off('hidden.bs.modal').on('hidden.bs.modal', function () {
    // Reset target mode if modal is closed without selection
    exitTargetMode();
  });

  // Show the modal
  $modal.modal('show');
};

// Keyboard movement controls
function handleKeyboardMovement(key, entity_uid, coordsx, coordsy) {
  console.log("handleKeyboardMovement called with:", { key, entity_uid, coordsx, coordsy });

  if (!keyboardMovementMode) {
    // Initialize keyboard movement mode
    console.log("Initializing keyboard movement mode");
    keyboardMovementMode = true;
    keyboardMovementSource = { x: coordsx, y: coordsy };
    keyboardMovementPath = [];
    keyboardMovementPivotPoints = [];
    moveMode = false; // Disable mouse-based movement
    globalCtx.clearRect(0, 0, globalCanvas.width, globalCanvas.height);
  }

  let newX = keyboardMovementSource.x;
  let newY = keyboardMovementSource.y;

  // Calculate new position based on key
  switch (key) {
    case 'ArrowUp':
    case 'w':
    case 'W':
      newY--;
      break;
    case 'ArrowDown':
    case 's':
    case 'S':
      newY++;
      break;
    case 'ArrowLeft':
    case 'a':
    case 'A':
      newX--;
      break;
    case 'ArrowRight':
    case 'd':
    case 'D':
      newX++;
      break;
  }

  console.log("Attempting to move to:", { newX, newY });

  // Check if this is a backtracking move
  if (keyboardMovementPath.length > 0) {
    const lastMove = keyboardMovementPath[keyboardMovementPath.length - 1];
    const isBacktracking =
      (key === 'ArrowUp' || key === 'w' || key === 'W') && lastMove[1] < newY ||
      (key === 'ArrowDown' || key === 's' || key === 'S') && lastMove[1] > newY ||
      (key === 'ArrowLeft' || key === 'a' || key === 'A') && lastMove[0] < newX ||
      (key === 'ArrowRight' || key === 'd' || key === 'D') && lastMove[0] > newX;

    if (isBacktracking) {
      console.log("Backtracking detected, canceling last move");
      // Remove the last move from the path
      keyboardMovementPath.pop();
      // Update source position to the previous position
      if (keyboardMovementPath.length > 0) {
        const newSource = keyboardMovementPath[keyboardMovementPath.length - 1];
        keyboardMovementSource = { x: newSource[0], y: newSource[1] };
      } else {
        keyboardMovementSource = { x: coordsx, y: coordsy };
      }
      // Redraw the path
      Utils.drawMovementPath(globalCtx, keyboardMovementPath, 0, true, null);
      return;
    }
  }

  // Check if the new position is valid
  Utils.ajaxGet(
    "/path",
    {
      from: keyboardMovementSource,
      to: { x: newX, y: newY },
      accumulatedPath: keyboardMovementPath.length > 0 ? JSON.stringify(keyboardMovementPath) : null
    },
    (data) => {
      console.log("Path check response:", data);
      if (data.cost.budget >= 0 && data.path) {
        // Update source position
        keyboardMovementSource = { x: newX, y: newY };

        // Add to path
        if (keyboardMovementPath.length > 0) {
          keyboardMovementPath.pop();
        }
        keyboardMovementPath = [...keyboardMovementPath, ...data.path];

        console.log("Updated path:", keyboardMovementPath);

        // Draw the path
        Utils.drawMovementPath(globalCtx, keyboardMovementPath, data.cost.budget, data.placeable, data.terrain_info);
      } else {
        console.log("Invalid move - path not available or budget exceeded");
      }
    }
  );
}

function executeKeyboardMovement(entity_uid, action, opts) {
  console.log("Executing keyboard movement");
  ajaxPost(
    "/action",
    {
      id: entity_uid,
      action: "MoveAction",
      opts: {
        action_type: "move",
        source: entity_uid
      },
      path: keyboardMovementPath
    },
    (data) => {
      console.log("Movement executed:", data);
      noteMoveActionCompleted(entity_uid, data);
      if (data.status === 'ok') {
        refreshTurn();
        // hide the popover menu
        $(".popover-menu").hide();
      } else if (data.param && data.param[0].type === "movement") {
        // Handle movement selection if needed
        moveModeCallback = (path) => {
          ajaxPost(
            "/action",
            { id: entity_uid, action, opts, path },
            (data) => {
              console.log("Action request successful:", data);
              noteMoveActionCompleted(entity_uid, data);
              refreshTurn();
            },
            true
          );
        };
        $(".popover-menu").hide();
        moveMode = true;
        source = { x: keyboardMovementSource.x, y: keyboardMovementSource.y };
        accumulatedPath = [];
        pivotPoints = [];
      }
      resetKeyboardMovement();
    },
    true
  );
}

function resetKeyboardMovement() {
  keyboardMovementMode = false;
  keyboardMovementSource = null;
  keyboardMovementPath = [];
  keyboardMovementPivotPoints = [];
  globalCtx.clearRect(0, 0, globalCanvas.width, globalCanvas.height);
}

// Handle direct WSAD movement on focused entity (without needing to click first)
function handleDirectWSADMovement(event) {
  // Check if we're in a text input or already in another mode
  if (isTypingIntoField(event.target) || isTypingIntoField(document.activeElement)) {
    return;
  }
  if (targetMode || multiTargetMode || coneMode || isInteractionLocked()) {
    return;
  }

  // Only handle WSAD / Arrow keys
  const key = event.key;
  if (!['w', 'a', 's', 'd', 'W', 'A', 'S', 'D', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(key)) {
    return;
  }

  // Get the focused entity (POV entity)
  const entity_uid = $('body').attr('data-pov-entity');
  if (!entity_uid) {
    return; // No entity in focus
  }

  const $entityTile = getEntityTile(entity_uid);
  if (!$entityTile.length) {
    return; // Entity not found on map
  }

  const pathCoords = resolveStackPathCoords($entityTile);
  const coordsx = pathCoords.x;
  const coordsy = pathCoords.y;

  // Initiate move mode if not already active, using the entity's current position as source
  if (!moveMode && !keyboardMovementMode) {
    moveMode = true;
    source = { x: coordsx, y: coordsy };
    accumulatedPath = [];
    pivotPoints = [];
    currentPosition = null;
    lastHoverCoords = null;
    movePath = [];

    // Store the movement callback that will be called when the user commits (via Space/Enter)
    moveModeCallback = (path) => {
      ajaxPost(
        "/action",
        { id: entity_uid, action: "MoveAction", opts: {}, path, manual_jump: null },
        (data) => {
          console.log("Direct WSAD movement executed:", data);
          noteMoveActionCompleted(entity_uid, data);
          refreshTurn();
        },
        true
      );
      moveMode = false;
      movePath = [];
      globalCtx.clearRect(0, 0, globalCanvas.width, globalCanvas.height);
    };

    console.log("Entered WSAD move mode for entity:", entity_uid, "at position:", { x: coordsx, y: coordsy });
  }

  // If we're now in moveMode, handle the directional input
  if (moveMode && source) {
    event.preventDefault(); // Prevent page scrolling

    const currentStep = movePath.length > 0 ? movePath[movePath.length - 1] : [source.x, source.y];
    let newX = currentStep[0];
    let newY = currentStep[1];

    // Calculate new position based on key
    switch (key.toLowerCase()) {
      case 'w':
      case 'arrowup':
        newY--;
        break;
      case 's':
      case 'arrowdown':
        newY++;
        break;
      case 'a':
      case 'arrowleft':
        newX--;
        break;
      case 'd':
      case 'arrowright':
        newX++;
        break;
    }

    // Fetch and validate the path
    const cacheKey = `${source.x}-${source.y}-${newX}-${newY}-${pivotPoints.join("-")}`;

    const applyMovementData = (data) => {
      if (!data || !data.cost || !data.path || data.cost.budget < 0) {
        console.log("Invalid move via WSAD - path not available or budget exceeded");
        return;
      }

      const available_cost = (data.cost.original_budget - data.cost.budget) * 5;
      currentPosition = {
        x: newX,
        y: newY,
        cost: available_cost,
        path: data.path
      };
      lastPathForJump = data.path;
      movePath = data.path;
      Utils.drawMovementPath(globalCtx, movePath, available_cost, data.placeable, data.terrain_info);
    };

    const makePathRequest = () => {
      const localData = computeLocalPathResponse(
        source,
        { x: newX, y: newY },
        accumulatedPath.length > 0 ? accumulatedPath : []
      );
      if (localData) {
        move_path_cache[cacheKey] = localData;
        applyMovementData(localData);
        return;
      }
      Utils.ajaxGet(
        "/path",
        Object.assign({
          from: source,
          to: { x: newX, y: newY },
          accumulatedPath: accumulatedPath.length > 0 ? JSON.stringify(accumulatedPath) : null
        }, stackPathRequestExtras()),
        (data) => {
          console.log("WSAD path check response:", data);
          move_path_cache[cacheKey] = data;
          applyMovementData(data);
        }
      );
    };

    // Check cache or make request
    if (move_path_cache[cacheKey]) {
      console.log("Using cached path for WSAD movement");
      applyMovementData(move_path_cache[cacheKey]);
    } else {
      makePathRequest();
    }
  }
}

function createGlobalCanvas() {
  if (globalCanvas) {
    document.body.removeChild(globalCanvas);
  }
  globalCanvas = document.createElement("canvas");
  globalCanvas.width = window.innerWidth;
  globalCanvas.height = window.innerHeight;
  globalCanvas.style.position = "fixed";
  globalCanvas.style.top = "0";
  globalCanvas.style.left = "0";
  globalCanvas.style.width = "100%";
  globalCanvas.style.height = "100%";
  globalCanvas.style.zIndex = 1000;
  globalCanvas.style.pointerEvents = "none";
  $("body").append(globalCanvas);
  globalCtx = globalCanvas.getContext("2d");
}

// --- Pan and Zoom (Roll20-style viewport) ---
// Apply the current pan/zoom transform to the map container
function applyViewportTransform() {
  const $container = $('.image-container');
  if (typeof Utils !== 'undefined' && typeof Utils.invalidateMovementGridCache === 'function') {
    Utils.invalidateMovementGridCache();
  }
  if ($container.length) {
    $container.css({
      'transform': `translate(${viewportPan.x}px, ${viewportPan.y}px) scale(${viewportZoom})`,
      'transform-origin': '0 0'
    });
  }
  // Redraw movement path overlay so it follows the panned/zoomed map
  redrawMovementPathOverlay();
}

// Redraw the current movement path on the global canvas (called after pan/zoom)
function redrawMovementPathOverlay() {
  if (typeof globalCtx === 'undefined' || !globalCtx || typeof globalCanvas === 'undefined' || !globalCanvas) {
    return;
  }
  if (typeof Utils === 'undefined' || typeof Utils.drawMovementPath !== 'function') {
    return;
  }
  // Keyboard movement mode
  if (typeof keyboardMovementMode !== 'undefined' && keyboardMovementMode &&
      typeof keyboardMovementPath !== 'undefined' && keyboardMovementPath && keyboardMovementPath.length > 0) {
    Utils.drawMovementPath(globalCtx, keyboardMovementPath, 0, true, null);
    return;
  }
  // Mouse-based movement mode
  if (typeof moveMode !== 'undefined' && moveMode &&
      typeof movePath !== 'undefined' && movePath && movePath.length > 0) {
    Utils.drawMovementPath(globalCtx, movePath, 0, true, null);
  }
}

// Pan viewport by trackpad/mouse-wheel deltas (pixels).
function panViewportByDelta(deltaX, deltaY) {
  viewportPan.x -= deltaX;
  viewportPan.y -= deltaY;
  applyViewportTransform();
}

// macOS maps trackpad pinch to ctrl+wheel; plain two-finger scroll should pan.
function resolveViewportWheelGesture(ctrlKey, deltaY) {
  if (ctrlKey) {
    return {
      action: 'zoom',
      zoomDelta: deltaY > 0 ? -ZOOM_STEP : ZOOM_STEP,
    };
  }
  return { action: 'pan' };
}

if (typeof window !== 'undefined') {
  window.N20ViewportWheel = { resolveViewportWheelGesture };
}

// Zoom at a specific point (for scroll wheel zoom)
function zoomAtPoint(delta, clientX, clientY) {
  const $mapArea = $('#main-map-area');
  const $container = $('.image-container');
  if (!$container.length) return;
  
  // Get container position relative to map area
  const mapRect = $mapArea[0].getBoundingClientRect();
  
  // Calculate point in container space before zoom
  const pointX = (clientX - mapRect.left - viewportPan.x) / viewportZoom;
  const pointY = (clientY - mapRect.top - viewportPan.y) / viewportZoom;
  
  // Apply zoom
  const oldZoom = viewportZoom;
  viewportZoom = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, viewportZoom + delta));
  
  // Adjust pan to keep the point under cursor
  viewportPan.x = clientX - mapRect.left - pointX * viewportZoom;
  viewportPan.y = clientY - mapRect.top - pointY * viewportZoom;
  
  applyViewportTransform();
  
  // Update display
  updateZoomDisplay();
}

// Simple zoom (without point tracking)
function setZoom(newZoom) {
  viewportZoom = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, newZoom));
  applyViewportTransform();
  updateZoomDisplay();
}

// Reset viewport to default
function resetViewport() {
  viewportPan = { x: 0, y: 0 };
  viewportZoom = 1.0;
  applyViewportTransform();
  updateZoomDisplay();
}

// Center viewport on specific tile coordinates
function centerViewportOnTile(tileX, tileY) {
  const $mapArea = $('#main-map-area');
  const $container = $('.image-container');
  const tileSize = parseInt($('.tiles-container').data('tile-size')) || 70;
  
  if (!$mapArea.length || !$container.length) return;
  
  const mapRect = $mapArea[0].getBoundingClientRect();
  
  // Calculate tile center position in container space
  const tileCenterX = (tileX + 0.5) * tileSize;
  const tileCenterY = (tileY + 0.5) * tileSize;
  
  // Calculate pan to center this tile
  viewportPan.x = (mapRect.width / 2) - (tileCenterX * viewportZoom);
  viewportPan.y = (mapRect.height / 2) - (tileCenterY * viewportZoom);
  
  applyViewportTransform();
}

/** Center using live tile geometry (handles stack overlays and layout offsets). */
function centerViewportOnTileElement($tile, options = {}) {
  const $mapArea = $('#main-map-area');
  if (!$mapArea.length || !$tile || !$tile.length) {
    return false;
  }
  if (options.resetZoom) {
    viewportZoom = 1.0;
    applyViewportTransform();
    updateZoomDisplay();
  }
  const mapRect = $mapArea[0].getBoundingClientRect();
  const tileRect = $tile[0].getBoundingClientRect();
  if (!tileRect.width || !tileRect.height) {
    return false;
  }
  const tileCenterX = tileRect.left + tileRect.width / 2;
  const tileCenterY = tileRect.top + tileRect.height / 2;
  const mapCenterX = mapRect.left + mapRect.width / 2;
  const mapCenterY = mapRect.top + mapRect.height / 2;
  viewportPan.x += mapCenterX - tileCenterX;
  viewportPan.y += mapCenterY - tileCenterY;
  applyViewportTransform();
  return true;
}

let viewportCenterTimer = null;
let pendingViewportCenterUid = null;

/** Retry centering until stack overlay tiles are mounted (map switch / stairs). */
function scheduleViewportCenterOnEntity(entityUid, options = {}) {
  if (!entityUid) {
    return;
  }
  const delayMs = Number(options.delayMs) >= 0 ? Number(options.delayMs) : 50;
  const maxAttempts = Number(options.maxAttempts) > 0 ? Number(options.maxAttempts) : 25;
  pendingViewportCenterUid = entityUid;
  if (viewportCenterTimer) {
    clearTimeout(viewportCenterTimer);
    viewportCenterTimer = null;
  }

  let attempts = 0;
  const resetZoom = !!options.resetZoom;
  const tryCenter = () => {
    viewportCenterTimer = null;
    if (pendingViewportCenterUid !== entityUid) {
      return;
    }
    const $tile = getEntityTile(entityUid);
    if ($tile.length && centerViewportOnTileElement($tile, { resetZoom: resetZoom && attempts === 0 })) {
      pendingViewportCenterUid = null;
      return;
    }
    if (++attempts < maxAttempts) {
      viewportCenterTimer = setTimeout(tryCenter, delayMs);
    }
  };
  viewportCenterTimer = setTimeout(tryCenter, delayMs);
}

// Update zoom display in UI
function updateZoomDisplay() {
  const zoomPercent = Math.round(viewportZoom * 100);
  $('#zoom-level').text(`${zoomPercent}%`);
}

// Initialize viewport handlers
function initViewportControls() {
  const $mapArea = $('#main-map-area');
  if (!$mapArea.length) return;
  
  // Disable native scroll on map area
  $mapArea.css('overflow', 'hidden');
  
  // Middle mouse button pan (mousedown)
  $mapArea.on('mousedown', function(e) {
    // Middle mouse button (button 1) — immediate pan
    if (e.button === 1) {
      e.preventDefault();
      isPanning = true;
      panStart = { x: e.clientX, y: e.clientY };
      panStartViewport = { x: viewportPan.x, y: viewportPan.y };
      $mapArea.addClass('panning');
      $('body').css('cursor', 'grabbing');
      return;
    }
    // Right mouse button (button 2) — arm a pan; only commit once the
    // cursor is dragged past the threshold. preventDefault on mousedown
    // blocks the browser context menu so panning works cleanly.
    if (e.button === 2) {
      // Don't arm if a UI overlay owns the click.
      if ($(e.target).closest('.modal, .panel, .popover-menu, .popover-menu-2, #centerActionBar, #turn-order, #console-container').length) {
        return;
      }
      e.preventDefault();
      rightPanArmed = true;
      rightPanDragged = false;
      panStart = { x: e.clientX, y: e.clientY };
      panStartViewport = { x: viewportPan.x, y: viewportPan.y };
    }
  });
  
  // Mouse move for panning
  $(document).on('mousemove.viewport', function(e) {
    // If a right-button drag is armed, see if we've crossed the threshold
    // and (if so) promote it into a real pan.
    if (rightPanArmed && !isPanning) {
      const ddx = e.clientX - panStart.x;
      const ddy = e.clientY - panStart.y;
      if (Math.abs(ddx) > RIGHT_PAN_DRAG_THRESHOLD || Math.abs(ddy) > RIGHT_PAN_DRAG_THRESHOLD) {
        isPanning = true;
        rightPanDragged = true;
        suppressNextContextMenu = true;
        $mapArea.addClass('panning');
        $('body').css('cursor', 'grabbing');
      }
    }
    if (!isPanning) return;

    const dx = e.clientX - panStart.x;
    const dy = e.clientY - panStart.y;

    viewportPan.x = panStartViewport.x + dx;
    viewportPan.y = panStartViewport.y + dy;

    applyViewportTransform();
  });

  // Mouse up to end panning
  $(document).on('mouseup.viewport', function(e) {
    if (e.button === 1 && isPanning) {
      isPanning = false;
      $mapArea.removeClass('panning');
      $('body').css('cursor', '');
      return;
    }
    if (e.button === 2 && rightPanArmed) {
      rightPanArmed = false;
      if (isPanning) {
        isPanning = false;
        $mapArea.removeClass('panning');
        $('body').css('cursor', '');
      }
      // suppressNextContextMenu remains set so the contextmenu handler
      // below can swallow the menu that browsers fire after a right-up.
    }
  });
  
  // Trackpad: two-finger scroll pans; pinch (ctrl+wheel on macOS) zooms.
  $mapArea.on('wheel', function(e) {
    // Check if we're over a modal or UI element
    if ($(e.target).closest('.modal, .panel, #turn-order, #console-container, .popover-menu, .popover-menu-2, #centerActionBar').length) {
      return; // Allow normal scroll in UI elements
    }
    
    e.preventDefault();
    const oe = e.originalEvent;
    const gesture = resolveViewportWheelGesture(!!oe.ctrlKey, oe.deltaY);
    if (gesture.action === 'zoom') {
      zoomAtPoint(gesture.zoomDelta, e.clientX, e.clientY);
      return;
    }
    panViewportByDelta(oe.deltaX, oe.deltaY);
  });
  
  // Prevent context menu on right-click (used for map panning) and middle
  // click.  Right-click is exclusively used for panning, so we always
  // suppress the browser context menu for it.  NOTE: another contextmenu
  // handler (the mode-cancel one further below) will consume
  // `suppressNextContextMenu` so it knows not to also cancel an active
  // target/move mode.  Ordering between the two handlers is not
  // guaranteed, so we keep the flag set.
  $mapArea.on('contextmenu', function(e) {
    if (e.button === 2 || e.button === 1) {
      e.preventDefault();
      return;
    }
    if (suppressNextContextMenu || rightPanDragged) {
      e.preventDefault();
      e.stopPropagation();
    }
  });
  
  // Zoom button controls
  $('#zoom-in').on('click', function() {
    const $mapArea = $('#main-map-area');
    const rect = $mapArea[0].getBoundingClientRect();
    // Zoom at center of viewport
    zoomAtPoint(ZOOM_STEP, rect.left + rect.width / 2, rect.top + rect.height / 2);
  });
  
  $('#zoom-out').on('click', function() {
    const $mapArea = $('#main-map-area');
    const rect = $mapArea[0].getBoundingClientRect();
    // Zoom at center of viewport
    zoomAtPoint(-ZOOM_STEP, rect.left + rect.width / 2, rect.top + rect.height / 2);
  });
  
  $('#zoom-reset').on('click', function() {
    resetViewport();
  });
  
  // Keyboard shortcuts for zoom (when not typing in an input)
  $(document).on('keydown.viewport', function(e) {
    // Skip if typing in an input
    if ($(e.target).is('input, textarea, select')) return;
    
    const $mapArea = $('#main-map-area');
    if (!$mapArea.length) return;
    const rect = $mapArea[0].getBoundingClientRect();
    const centerX = rect.left + rect.width / 2;
    const centerY = rect.top + rect.height / 2;
    
    if (e.key === '=' || e.key === '+') {
      e.preventDefault();
      zoomAtPoint(ZOOM_STEP, centerX, centerY);
    } else if (e.key === '-' || e.key === '_') {
      e.preventDefault();
      zoomAtPoint(-ZOOM_STEP, centerX, centerY);
    } else if (e.key === '0' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      resetViewport();
    }
  });
  
  // Initialize transform
  applyViewportTransform();
  updateZoomDisplay();
}

// Check if user is DM
function isDM() {
  const role = $('body').attr('data-role') || $('body').data('role') || '';
  if (Array.isArray(role)) {
    return role.some((entry) => String(entry).toLowerCase().includes('dm'));
  }
  return String(role).toLowerCase().includes('dm');
}

function isEditMode() {
  return String($('body').attr('data-edit-mode') || '').toLowerCase() === 'true';
}

// Update draggable entity classes for proper cursor styling
function updateDraggableEntityClasses() {
  if (!isDM()) return;

  $('.tile').removeClass('has-draggable-entity');
  $('.tile').each(function () {
    const $tile = $(this);
    const entityId = $tile.data("coords-id");

    // Add class if tile has an entity or NPC
    if (entityId && $tile.find('.entity, .npc').length) {
      $tile.addClass('has-draggable-entity');
    }
  });
}

// --- Ghost Token System Functions ---
// Move entity to target position
function moveEntityTo(entityId, targetX, targetY) {
  // Check if there's already a pending move for this entity
  if (pendingMoves.has(entityId)) {
    console.log(`Entity ${entityId} already has a pending move, ignoring new request`);
    return;
  }

  // Find the source tile using coords-id
  const $sourceTile = $(`.tile[data-coords-id="${entityId}"]`);

  if (!$sourceTile.length) {
    console.error(`Could not find source tile for entity with ID: ${entityId}`);
  } else {
    const sourceX = $sourceTile.data('coords-x');
    const sourceY = $sourceTile.data('coords-y');
    const $sourceEntity = $sourceTile.find('.entity, .npc').first();

    if ($sourceEntity.length) {
      console.log(`Creating ghost token for entity ${entityId} moving from (${sourceX}, ${sourceY}) to (${targetX}, ${targetY})`);
      // Create ghost token at target position
      createGhostToken(entityId, sourceX, sourceY, targetX, targetY, $sourceEntity);

      // Mark source entity as pending move
      $sourceEntity.addClass('entity-pending-move');
    }
  }

  if (isEditMode() && typeof window.moveEntityInEditMode === 'function') {
    window.moveEntityInEditMode(entityId, targetX, targetY)
      .done(function (response) {
        if (response.success) {
          console.log(`Entity ${entityId} moved to (${targetX}, ${targetY})`);
          cleanupPendingMove(entityId);
        } else {
          alert('Failed to move entity: ' + (response.error || 'Unknown error'));
          cleanupPendingMove(entityId);
        }
      })
      .fail(function (xhr, status, error) {
        console.error('Error moving entity:', error);
        alert('Error moving entity: ' + error);
        cleanupPendingMove(entityId);
      });
    return;
  }

  $.ajax({
    url: '/dm_move_entity',
    method: 'POST',
    contentType: 'application/json',
    data: JSON.stringify({
      entity_id: entityId,
      x: targetX,
      y: targetY
    }),
    success: function (response) {
      if (response.success) {
        console.log(`Entity ${entityId} moved to (${targetX}, ${targetY})`);
        // Clean up ghost token and pending state
        cleanupPendingMove(entityId);
        // The server will emit a refresh_map message, so no need to manually refresh
      } else {
        alert('Failed to move entity: ' + (response.error || 'Unknown error'));
        cleanupPendingMove(entityId);
      }
    },
    error: function (xhr, status, error) {
      console.error('Error moving entity:', error);
      alert('Error moving entity: ' + error);
      cleanupPendingMove(entityId);
    }
  });
}

// Create a ghost token at the target location
function createGhostToken(entityId, sourceX, sourceY, targetX, targetY, $sourceEntity) {
  // Don't create ghost if target is same as source
  if (sourceX === targetX && sourceY === targetY) {
    console.log('Target is same as source, skipping ghost token creation');
    return;
  }

  const $targetTile = $(`.tile[data-coords-x="${targetX}"][data-coords-y="${targetY}"]`);
  if (!$targetTile.length) {
    console.error(`Could not find target tile at (${targetX}, ${targetY})`);
    return;
  }

  // Clone the entity for the ghost
  const $ghost = $sourceEntity.clone();
  $ghost.removeClass('entity npc').addClass('entity-move-ghost');
  $ghost.removeAttr('data-entity-id'); // Remove ID to avoid conflicts
  $ghost.removeAttr('data-entity-uid');
  $ghost.removeAttr('data-entityId');

  // Add ghost to target tile
  $targetTile.append($ghost);

  // Store pending move info
  pendingMoves.set(entityId, {
    sourceX: sourceX,
    sourceY: sourceY,
    targetX: targetX,
    targetY: targetY,
    ghostElement: $ghost
  });

  // Set up timeout to clean up ghost if request takes too long (30 seconds)
  const timeoutId = setTimeout(() => {
    console.warn(`Move request for entity ${entityId} timed out, cleaning up ghost`);
    cleanupPendingMove(entityId);
  }, 30000);

  moveRequestTimeouts.set(entityId, timeoutId);
}

// Clean up pending move state and ghost token
function cleanupPendingMove(entityId) {
  console.log(`Cleaning up pending move for entity: ${entityId}`);
  const pendingMove = pendingMoves.get(entityId);
  if (pendingMove) {
    // Remove ghost token
    if (pendingMove.ghostElement) {
      pendingMove.ghostElement.remove();
      console.log(`Removed ghost token for entity: ${entityId}`);
    }
    pendingMoves.delete(entityId);
  }

  // Clear timeout
  const timeoutId = moveRequestTimeouts.get(entityId);
  if (timeoutId) {
    clearTimeout(timeoutId);
    moveRequestTimeouts.delete(entityId);
  }

  // Remove pending move styling from source entity using coords-id
  const $sourceTile = $(`.tile[data-coords-id="${entityId}"]`);
  if ($sourceTile.length) {
    $sourceTile.find('.entity, .npc').removeClass('entity-pending-move');
  }
}

// Clean up all pending moves (useful for page refresh or errors)
function cleanupAllPendingMoves() {
  for (const entityId of pendingMoves.keys()) {
    cleanupPendingMove(entityId);
  }
}

// Move PC entity to target position (with ghost token support)
function movePCEntityTo(entityUid, targetX, targetY) {
  // Check if there's already a pending move for this entity
  if (pendingMoves.has(entityUid)) {
    console.log(`PC ${entityUid} already has a pending move, ignoring new request`);
    return;
  }

  // Find the source tile using coords-id
  const $sourceTile = $(`.tile[data-coords-id="${entityUid}"]`);

  if (!$sourceTile.length) {
    console.log(`Could not find source tile for PC entity with UID: ${entityUid}, proceeding with move anyway`);
  } else {
    const sourceX = $sourceTile.data('coords-x');
    const sourceY = $sourceTile.data('coords-y');
    const $sourceEntity = $sourceTile.find('.entity, .npc').first();

    if ($sourceEntity.length) {
      console.log(`Creating ghost token for PC ${entityUid} moving from (${sourceX}, ${sourceY}) to (${targetX}, ${targetY})`);
      // Create ghost token at target position
      createGhostToken(entityUid, sourceX, sourceY, targetX, targetY, $sourceEntity);

      // Mark source entity as pending move
      $sourceEntity.addClass('entity-pending-move');
    }
  }

  ajaxPost("/move_entity", {
    entity_uid: entityUid,
    x: targetX,
    y: targetY
  }, (data) => {
    if (data.status === 'ok') {
      console.log("PC moved successfully:", data.entity_uid);
      cleanupPendingMove(entityUid);
    } else {
      alert("Failed to move PC: " + (data.error || "Unknown error"));
      cleanupPendingMove(entityUid);
    }
  }, true);
}

// --- Document Ready: Event Bindings & Main Logic ---
$(document).ready(() => {
  let lastMovedEntityBeforeRefresh = null;
  const battleEntityList = [];

  // Invalidate the cached tile-grid origin used by the movement-path
  // renderer whenever layout might shift.
  const invalidateGrid = () => {
    if (typeof Utils !== 'undefined' && typeof Utils.invalidateMovementGridCache === 'function') {
      Utils.invalidateMovementGridCache();
    }
  };
  window.addEventListener('resize', invalidateGrid, { passive: true });
  window.addEventListener('scroll', invalidateGrid, { passive: true });

  // Initialize global variables
  backgroundSoundStartTime = $("body").data("soundtrack-time");
  pageRenderTime = new Date().getTime();

  // Initialize game time display
  const gameTimeElement = $('#game-time-text');
  if (gameTimeElement.length > 0) {
    const initialGameTime = gameTimeElement.data('seconds');
    if (initialGameTime !== undefined) {
      updateGameTimeDisplay(initialGameTime);
    }
  }

  // Initialize user volume control for non-DM users
  if (typeof UserVolumeControl !== 'undefined' && $('#music-control-widget').length > 0) {
    UserVolumeControl.init();
  }

  $(document).on('change', '#stack-layer-select', function () {
    const mapName = $(this).val();
    const currentMap = $('body').attr('data-current-map');
    if (!mapName || mapName === currentMap) {
      return;
    }
    Utils.switchMap(mapName, globalCanvas, () => {
      $('#stack-layer-select').val(mapName);
    });
  });

  $('#mapModal').on('shown.bs.modal', function () {
    const currentMap = $('body').attr('data-current-map') || '';
    if (currentMap && typeof Utils.syncMapPickerUI === 'function') {
      Utils.syncMapPickerUI(currentMap);
    }
  });

  // --- Canvas Setup ---
  const tile_size = $(".tiles-container").data("tile-size");

  // Update draggable entity classes for cursor styling
  updateDraggableEntityClasses();

  // Initialize viewport pan/zoom controls (Roll20-style)
  initViewportControls();

  createGlobalCanvas();
  // Update canvas size on window resize
  $(window).on('resize', function () {
    globalCanvas.width = window.innerWidth;
    globalCanvas.height = window.innerHeight;
  });

  const username = $("body").data("username");

  // Configure Socket.IO client with proper settings — always use the page origin
  // so TTS events and other real-time updates hit the same server as HTTP.
  const socket = io({
    transports: ['polling', 'websocket'],
    reconnection: true,
    reconnectionAttempts: 10,
    reconnectionDelay: 1000,
    reconnectionDelayMax: 5000,
    timeout: 20000,
    autoConnect: true,
    path: '/socket.io',
    forceNew: false,
    multiplex: true,
    withCredentials: true,
  });
  window.socket = socket;

  // Bind TTS player to the shared socket once it exists
  try {
    if (typeof window.TTSPlayer !== 'undefined' && window.TTSPlayer && typeof window.TTSPlayer.init === 'function') {
      window.TTSPlayer.init(socket);
    }
  } catch (ttsInitErr) {
    console.warn('[TTS] Failed to bind player to socket:', ttsInitErr);
  }

  // Initialize effects socket handlers if Effects exists
  if (typeof Effects !== 'undefined' && Effects.initSocketHandlers) {
    try {
      var serverEffectsEnabled = $('body').attr('data-special-effects-enabled') !== 'false';

      if (!serverEffectsEnabled) {
        if (Effects.hardReset) {
          Effects.hardReset();
        } else {
          Effects.setEnabled(false);
        }
      } else {
        // Load persisted effects setting
        try {
          var saved = localStorage.getItem('vtt.effects.enabled');
          if (saved === 'false') { Effects.setEnabled(false); }
        } catch (e) { }
      }

      Effects.initSocketHandlers(socket);
      // Ask the server to resend any active or map-default effects now that handlers are registered
      if (serverEffectsEnabled && Effects.isEnabled()) {
        try { socket.emit('request_effects'); } catch (e) { console.warn('Failed to request effects', e); }
      }
    } catch (e) {
      console.warn('Failed to init Effects socket handlers', e);
    }
  }

  if ($("body").data("waiting-for-reaction")) {
    $("#reaction-modal").modal("show");
  }

  socket.on("connect", () => {
    console.log("Connected to the server");
    socket.emit("register", { username });
  });

  socket.on('connect_error', (error) => {
    console.log('Connection error:', error);
  });

  socket.on('reconnect_attempt', (attemptNumber) => {
    console.log('Reconnection attempt:', attemptNumber);
  });

  socket.on('reconnect', () => {
    console.log('Reconnected successfully');
    // Re-register after reconnection
    socket.emit("register", { username });
  });

  socket.on('disconnect', (reason) => {
    console.log('Disconnected:', reason);
  });

  socket.on('error', (error) => {
    console.error('Socket error:', error);
  });

  if ($('.tiles-container .tile').length === 0) {
    enqueueTileRefresh();
  }

  // --- Effects Toggle UI (top-right unobtrusive button) ---
  try {
    if (typeof Effects !== 'undefined') {
      var serverEffectsEnabled = $('body').attr('data-special-effects-enabled') !== 'false';
      var btn = document.getElementById('effects-toggle-btn');
      if (!btn) {
        btn = document.createElement('button');
        btn.id = 'effects-toggle-btn';
        btn.type = 'button';
        btn.style.position = 'fixed';
        btn.style.top = '8px';
        btn.style.left = '200px';
        btn.style.zIndex = 1000;
        btn.style.padding = '6px 10px';
        btn.style.fontSize = '12px';
        btn.style.opacity = '0.75';
        btn.style.background = '#222';
        btn.style.color = '#fff';
        btn.style.border = '1px solid #555';
        btn.style.borderRadius = '4px';
        document.body.appendChild(btn);
      }

      if (!serverEffectsEnabled) {
        btn.textContent = 'Effects: Disabled';
        btn.disabled = true;
        btn.style.cursor = 'not-allowed';
        btn.style.opacity = '0.55';
        btn.title = 'Visual effects are disabled by server configuration';
      } else {
        btn.textContent = Effects.isEnabled() ? 'Effects: On' : 'Effects: Off';
        btn.disabled = false;
        btn.style.cursor = 'pointer';
        btn.style.opacity = '0.75';
        btn.title = 'Toggle visual effects (for performance)';
        btn.addEventListener('mouseenter', function () { btn.style.opacity = '1.0'; });
        btn.addEventListener('mouseleave', function () { btn.style.opacity = '0.75'; });
        btn.addEventListener('click', function () {
          var next = !Effects.isEnabled();
          Effects.setEnabled(next);
          btn.textContent = next ? 'Effects: On' : 'Effects: Off';
          try { localStorage.setItem('vtt.effects.enabled', next ? 'true' : 'false'); } catch (e) { }
          // When turning on, re-request current effects from server (or map defaults)
          if (next) {
            try { if (socket && socket.emit) socket.emit('request_effects'); } catch (e) { }
          }
        });
      }
    }
  } catch (e) { console.warn('Failed to setup effects toggle', e); }

  // --- Socket Message Handler ---
  socket.on("message", (data) => {
    // Enqueue the event for FIFO processing.
    // Reset the action post cooldown when the server signals turn state changes
    // (end_of_turn, start_of_turn) so subsequent /update requests are allowed.
    if (data && typeof data === 'object' && (data.type === 'end_of_turn' || data.type === 'start_of_turn')) {
      actionPostTimestamp = 0;
    }
    // Enqueue the event for FIFO processing
    eventQueue.enqueue(data);
  });

  let currentSoundtrackId = $("body").data("soundtrack-id");
  $("body").on("click", (event) => {
    if (shouldSuppressSoundtrackOnClick(event.target)) {
      return;
    }
    // Recover soundtrack state on first click.
    if (playPendingSoundtrackIfAny()) {
      if (typeof UserVolumeControl !== 'undefined') {
        setTimeout(() => UserVolumeControl.updateWidget(), 200);
      }
      return;
    }
    let currentSoundtrackUrl = $("body").data("soundtrack-url");
    let currentSoundtrackVolume = $("body").data("soundtrack-volume");
    if (currentSoundtrackId) {
      playSound(
        currentSoundtrackUrl,
        currentSoundtrackId,
        currentSoundtrackVolume,
      );
      currentSoundtrackId = null;

      // Update user volume control widget after music starts
      if (typeof UserVolumeControl !== 'undefined') {
        setTimeout(() => UserVolumeControl.updateWidget(), 200);
      }
    }
  });

  // --- Form & Slider Handlers ---
  $("#reaction-form").on("submit", (event) => {
    event.preventDefault();
    const reaction = $('#reaction-form input[name="reaction"]:checked').val();
    ajaxPost("/reaction", { reaction }, (data) => {
      console.log("Reaction submitted successfully:", data);
      $("#reaction-modal").modal("hide");
    });
  });

  $("#modal-1 .modal-content").on("input", ".volume-slider", function () {
    const volume = parseInt($(this).val());
    $('.volume-display').text(volume + '%');

    if (active_background_sound) {
      // Update DM Sound Manager volume
      if (typeof DMSoundManager !== 'undefined') {
        DMSoundManager.setCurrentVolume(volume);
      } else {
        // Fallback to original behavior
        ajaxPost(
          "/volume",
          { volume: volume },
          () => console.log("Volume updated successfully"),
          true,
        );
      }
    }
  });

  $("#reloadModal").on("submit", "#reload-map-form", function (event) {
    event.preventDefault();
    ajaxPost("/reload_map", {}, (data) => {
      console.log("Reload request successful:", data);
      $("#reloadModal").modal("hide");
      enqueueTileRefresh();
    });
  });

  $("#mapModal").on("change", "#map-select", function (event) {
    event.preventDefault();
    const map_id = $("#map-select").val();
    resetViewport();
    Utils.switchMap(map_id, globalCanvas, () => {
      createGlobalCanvas();
      // Center on POV entity after map loads
      setTimeout(() => {
        try {
          const povEntity = Chat.getCurrentPovEntity();
          if (povEntity) centerOnEntityId(povEntity);
        } catch (e) { }
      }, 100);
    });
  });

  // Handle tile-based map selection
  $("#mapModal").on("click", ".map-card", function (event) {
    if ($(event.target).closest('.upload-overlay, .map-card-actions, .activate-map-set').length) {
      return;
    }
    const mapName = $(this).data("map-name");
    if (!mapName) return; // ignore non-map tiles (e.g., create-new)
    event.preventDefault();
    if (typeof Utils.syncMapPickerUI === 'function') {
      Utils.syncMapPickerUI(mapName);
    }
    resetViewport();
    Utils.switchMap(mapName, globalCanvas, () => {
      createGlobalCanvas();
    });
  });

  $("#mapModal").on("click", ".activate-map-set", function (event) {
    event.preventDefault();
    event.stopPropagation();
    const setId = $(this).data("map-set");
    if (!setId) return;
    ajaxPost("/admin/map_set/activate", { map_set: setId }, (data) => {
      if (data && data.error) {
        alert(data.error);
        return;
      }
      if (data && data.skipped && data.skipped.length) {
        const names = data.skipped.map((row) => row.entity_uid || row.username).join(", ");
        alert("Some characters have no token on this map set. Place Party first: " + names);
      }
      window.location.reload();
    });
  });

  $("#mapModal").on("click", ".place-party-on-map", function (event) {
    event.preventDefault();
    event.stopPropagation();
    const mapName = $(this).data("map-name");
    if (!mapName) return;
    ajaxPost("/admin/map_set/place_party", { map_name: mapName }, (data) => {
      if (data && data.error) {
        alert(data.error);
        return;
      }
      enqueueTileRefresh();
    });
  });

  $("#mapModal").on("click", "#create-new-map-set", function (event) {
    event.preventDefault();
    event.stopPropagation();
    let setId = window.prompt("New map set id (letters, numbers, underscores):", "encounter");
    if (!setId) return;
    setId = (setId || "").trim().toLowerCase().replace(/[^a-z0-9_\-]/g, "_");
    const label = window.prompt("Label for this map set:", setId.replace(/_/g, " "));
    ajaxPost("/admin/map_set/create", { id: setId, label: label || setId }, (data) => {
      if (data && data.error) {
        alert(data.error);
        return;
      }
      window.location.reload();
    });
  });

  // Map Connections graph (edit mode) can request a switch from a child tab.
  window.addEventListener("message", function (event) {
    try {
      if (event.origin !== window.location.origin) return;
      const data = event.data;
      if (!data || data.type !== "n20_switch_map" || !data.map) return;
      resetViewport();
      Utils.switchMap(data.map, globalCanvas, () => {
        createGlobalCanvas();
      });
    } catch (e) {
      console.warn("Map switch message failed", e);
    }
  });

  // Handle create new map tile
  $("#mapModal").on("click", "#create-new-map", function () {
    // simple prompt for map name
    let mapName = window.prompt("Enter a name for the new map (letters, numbers, underscores):", "new_map");
    if (!mapName) return;
    mapName = (mapName || "").trim().toLowerCase().replace(/[^a-z0-9_\-]/g, "_");
    if (!mapName) {
      alert("Invalid map name.");
      return;
    }
    let width = parseInt(window.prompt("Map width (tiles)", "16"), 10);
    let height = parseInt(window.prompt("Map height (tiles)", "8"), 10);
    if (isNaN(width) || isNaN(height)) { width = 16; height = 8; }
    width = Math.max(2, Math.min(100, width));
    height = Math.max(2, Math.min(100, height));
    let mapSet = window.prompt("Add to map set id (blank = current/root, or a new id for an adhoc scene):", "");
    mapSet = (mapSet || "").trim().toLowerCase().replace(/[^a-z0-9_\-]/g, "_");
    ajaxPost(
      "/create_map",
      { name: mapName, width, height, map_set: mapSet },
      (data) => {
        if (data && data.error) {
          alert(data.error);
          return;
        }
        // Add the new tile to the grid if not present
        const $grid = $("#map-grid");
        if ($grid.length && $grid.find(`.map-card[data-map-name='${mapName}']`).length === 0) {
          const tileHtml = `
            <div class="map-card" data-map-name="${mapName}">
              <div class="upload-overlay">
                <button class="btn btn-xs btn-default upload-map-bg" title="Upload background" data-map-name="${mapName}">
                  <span class="glyphicon glyphicon-upload"></span>
                </button>
                <button class="btn btn-xs btn-danger delete-map" title="Delete map" data-map-name="${mapName}">
                  <span class="glyphicon glyphicon-trash"></span>
                </button>
                <input type="file" class="map-bg-input" data-map-name="${mapName}" accept="image/*" style="display:none;" />
              </div>
              <img class="map-thumb" src="/assets/maps/${mapName}.png" alt="${mapName}" onerror="this.onerror=null;this.src='/static/info.png';">
              <div class="map-name">${mapName}</div>
            </div>`;
          $(tileHtml).insertBefore($("#create-new-map"));
        }
        // Switch to the new map
        resetViewport();
        Utils.switchMap(mapName, globalCanvas, () => {
          createGlobalCanvas();
          // Center on POV entity after map loads
          setTimeout(() => {
            try {
              const povEntity = Chat.getCurrentPovEntity();
              if (povEntity) centerOnEntityId(povEntity);
            } catch (e) { }
          }, 100);
        });
      }
    );
  });

  // Trigger file chooser for upload button
  $("#mapModal").on("click", ".upload-map-bg", function (e) {
    e.stopPropagation();
    const mapName = $(this).data('map-name');
    $(this).closest('.map-card').find(`.map-bg-input[data-map-name='${mapName}']`).trigger('click');
  });

  // Handle delete map with warnings
  $("#mapModal").on("click", ".delete-map", function (e) {
    e.stopPropagation();
    const mapName = $(this).data('map-name');
    if (!mapName) return;
    // Show warnings
    const warn1 = `Delete map "${mapName}"? This will remove its YAML and background image.`;
    const warn2 = 'This action cannot be undone. Are you sure?';
    if (!window.confirm(warn1)) return;
    if (!window.confirm(warn2)) return;

    ajaxPost('/delete_map', { name: mapName }, (resp) => {
      if (resp && resp.error) {
        alert(resp.error);
        return;
      }
      // Remove tile from UI
      $(`.map-card[data-map-name='${mapName}']`).remove();

      // If current map deleted, switch to index or any remaining map
      const currentMap = $('body').attr('data-current-map');
      if (currentMap === mapName) {
        const $first = $("#map-grid .map-card[data-map-name]").first();
        const target = $first.data('map-name') || 'index';
        resetViewport();
        Utils.switchMap(target, globalCanvas, () => {
          createGlobalCanvas();
          // Center on POV entity after map loads
          setTimeout(() => {
            try {
              const povEntity = Chat.getCurrentPovEntity();
              if (povEntity) centerOnEntityId(povEntity);
            } catch (e) { }
          }, 100);
        });
      }
    });
  });

  // Handle image file selection and upload
  $("#mapModal").on("change", ".map-bg-input", function () {
    const input = this;
    const files = input.files;
    const mapName = $(this).data('map-name');
    if (!files || files.length === 0) return;
    const formData = new FormData();
    formData.append('map', mapName);
    formData.append('image', files[0]);

    $.ajax({
      url: '/upload_map_background',
      type: 'POST',
      data: formData,
      processData: false,
      contentType: false,
      success: (data) => {
        if (data && data.error) {
          alert(data.error);
          return;
        }
        // Refresh tile thumbnail
        const $tile = $(`.map-card[data-map-name='${mapName}'] .map-thumb`);
        $tile.attr('src', `/assets/maps/${mapName}.png?ts=${Date.now()}`);

        // If currently on this map, update background immediately
        const currentMap = $('body').attr('data-current-map');
        if (currentMap === mapName && data.background) {
          const resp = { background: data.background, name: mapName, width: $("#tiles-area").data('width'), height: $("#tiles-area").data('height'), image_offset_px: [0, 0] };
          Utils.updateMapDisplay(resp, globalCanvas);
        }
      },
      error: (jqXHR) => {
        alert('Upload failed');
      }
    });
  });

  // --- Tile & Action Event Handlers ---
  $(".tiles-container").on("click", ".execute-action", (e) => {
    targetModeCallback(multiTargetList);
    targetMode = multiTargetMode = false;
    valid_target_cache = {};
    multiTargetList = [];
    $(".add-to-target, .popover-menu-2").hide();
    if (typeof hideMultiTargetBar === 'function') { hideMultiTargetBar(); }
    globalCtx.clearRect(0, 0, globalCanvas.width, globalCanvas.height);
    e.stopPropagation();
  });

  $(".tiles-container").on("click", ".tile", function (e) {
    if (!targetMode && !moveMode && window.EditProperties && typeof window.EditProperties.handleTileClick === 'function') {
      if (window.EditProperties.handleTileClick(e, $(this))) {
        e.stopPropagation();
        return;
      }
    }
    if ($(e.target).closest('.object-quick-action, .object-quick-actions, .object-quick-interact-layer, .move-quick-action, .move-quick-action-layer').length) {
      return;
    }
    if (isInteractionLocked()) {
      e.stopPropagation();
      return;
    }
    if (!targetMode && !moveMode && window.DmNotes && typeof window.DmNotes.handleTileClick === 'function') {
      if (window.DmNotes.handleTileClick(e, $(this))) {
        e.stopPropagation();
        return;
      }
    }
    const $tile = $(this);
    const targetCoords = resolveStackTargetCoords($tile);
    const pathCoords = resolveStackPathCoords($tile);
    const coordsx = targetMode ? targetCoords.x : pathCoords.x;
    const coordsy = targetMode ? targetCoords.y : pathCoords.y;
    const cacheKey = `${coordsx}-${coordsy}`;

    if (targetMode) {
      if (aoePointTargetMode || coneMode) {
        targetModeCallback({ x: coordsx, y: coordsy });
        exitTargetMode();
        e.stopPropagation();
        return;
      }
      if (blockInvalidTargetClick(cacheKey, e)) {
        return;
      }
      // Check if there are multiple valid targets at this position
      if (globalActionInfo && (
        globalActionInfo === 'AttackAction' ||
        globalActionInfo === 'LinkedAttackAction' ||
        globalActionInfo === 'SpellAction' ||
        globalActionInfo === 'PickpocketAction' ||
        globalActionInfo === 'PickpocketBonusAction'
      )) {
        Utils.ajaxGet("/targets_at_position", {
          entity_id: globalSourceEntity,
          x: coordsx,
          y: coordsy,
          action_info: globalActionInfo,
          opts: JSON.stringify(globalOpts || {})
        }, (data) => {
          if (data.success && data.targets && data.targets.length > 1) {
            // Show target selection modal
            showTargetSelectionModal(data.targets, { x: coordsx, y: coordsy });
          } else if (data.success && data.targets && data.targets.length === 1) {
            if (blockInvalidTargetClick(cacheKey, e)) {
              return;
            }
            targetModeCallback({ x: coordsx, y: coordsy, target_id: data.targets[0].id });
            exitTargetMode();
          } else {
            if (blockInvalidTargetClick(cacheKey, e)) {
              return;
            }
            // No targets, proceed with coordinates (server validates)
            targetModeCallback({ x: coordsx, y: coordsy });
            exitTargetMode();
          }
        });
      } else {
        // Not a point target action, proceed normally
        targetModeCallback({ x: coordsx, y: coordsy });
        exitTargetMode();
      }
    } else if (moveMode) {
      if (coordsx !== source.x || coordsy !== source.y) {
        moveMode = false;
        clearMovePathCache();
        globalCtx.clearRect(0, 0, globalCanvas.width, globalCanvas.height);
        $(".tile").css("border", "none");
        moveModeCallback(movePath);
        movePath = [];
      }
    } else {
      if (e.metaKey || e.ctrlKey) {
        const focusEntityId = $tile.data('coords-id') || null;
        if (isDM() && focusEntityId) {
          switchPOV(focusEntityId, globalCanvas);
        } else {
          enqueueTileRefresh({ pov: true, x: coordsx, y: coordsy, entity_uid: focusEntityId });
        }
      } else if (e.shiftKey && isEditMode()) {
        // Fixture placement uses Shift+label drag handles in edit_mode.js.
      } else if (e.metaKey || e.shiftKey) {
        ajaxPost("/focus", { x: coordsx, y: coordsy }, (data) => {
          console.log("Focus request successful:", data);
        });
      } else if (isEditMode()) {
        // Tile clicks in edit mode open the fixture property editor (edit_mode.js).
      } else {
        $(".popover-menu").hide();
        closeCenterActionBar();
        const primaryEntityId = $tile.data("coords-id");
        if (
          !primaryEntityId
          && !isBattleInProgress()
          && $tile.find('.teleporter-marker').length
        ) {
          const povUid = $('body').attr('data-pov-entity')
            || (typeof Chat !== 'undefined' && Chat.getCurrentPovEntity && Chat.getCurrentPovEntity());
          if (povUid) {
            const pathCoords = resolveStackPathCoords($tile);
            runTeleporterApproach(povUid, pathCoords.x, pathCoords.y);
            return;
          }
        }

        const occupantEntries = [];
        const seenOccupants = new Set();

        const pushOccupant = (id, label, type) => {
          if (!id || seenOccupants.has(id)) {
            return;
          }
          seenOccupants.add(id);
          const trimmedLabel = (label && `${label}`.trim()) || "";
          occupantEntries.push({ id, label: trimmedLabel, type });
        };

        if (primaryEntityId) {
          const $primaryEntity = $tile
            .find(`.entity[data-id="${primaryEntityId}"]`)
            .first();
          const primaryLabel =
            $primaryEntity.data("label") ||
            $tile.find(".nameplate").first().text() ||
            primaryEntityId;
          pushOccupant(primaryEntityId, primaryLabel, "creature");
        }

        $tile.find(".entity[data-id]").each(function () {
          const $entity = $(this);
          const id = $entity.data("id");
          const label = $entity.data("label") ||
            $entity.find(".nameplate").first().text();
          pushOccupant(id, label, "creature");
        });

        $tile.find(".object-container[data-id]").each(function () {
          const $object = $(this);
          const id = $object.data("id");
          const label =
            $object.data("label") ||
            $object.data("tooltip") ||
            `Object ${id}`;
          pushOccupant(id, label, "object");
        });

        if (occupantEntries.length === 0) {
          return;
        }

        showCenteredActionBarLoading(occupantEntries);

        fetchActionBarSections(occupantEntries)
          .then((sections) => {
            // NOTE: do NOT bail here if $tile is no longer attached.
            // The game frequently emits refresh_tiles while the /actions
            // request is in flight, which replaces tile DOM nodes. The
            // centered action bar is independent of the tile, so we render
            // it regardless.
            renderCenteredActionBarSections(sections, coordsx, coordsy);
            try {
              if (window.JournalNotifications) JournalNotifications.render();
            } catch (e) { /* optional */ }
          })
          .catch(() => {
            $('#centerActionBarContent').html(
              "<div class='popover-actions-stack'><div class='popover-actions-entry center-action-entry'><div class='popover-actions-empty'>Unable to load actions.</div></div></div>"
            );
            $('#centerActionBar').show();
            actionBarState.visible = true;
          });
      }
    }
  });

  $(".tiles-container").on(
    "click",
    ".show-note-btn, .object-note-overlay",
    function (e) {
      e.stopPropagation();
      // Call the note modal function if it's a note button
      if ($(this).hasClass('show-note-btn')) {
        if (window.__n20SuppressNoteModal) {
          return;
        }
        Utils.showNoteModal(this);
      }
    },
  );

  $(".zoom-in").on("click", () => {
    scale += 0.1;
    $("#main-map-area").css({
      "transform": `scale(${scale})`,
      "transform-origin": "center center"
    });
    globalCtx.clearRect(0, 0, globalCanvas.width, globalCanvas.height);
  });

  $(".zoom-out").on("click", () => {
    scale -= 0.1;
    $("#main-map-area").css({
      "transform": `scale(${scale})`,
      "transform-origin": "center center"
    });
    globalCtx.clearRect(0, 0, globalCanvas.width, globalCanvas.height);
  });

  // Draws a target line from a source to a given set of coordinates.
  function drawTargetLine(ctx, source, coordsx, coordsy, valid_target = true, $tgtTile = null) {
    const currentDistance = targetDistanceFeet(source.x, source.y, coordsx, coordsy);
    const scrollLeft =
      window.pageXOffset || document.documentElement.scrollLeft;
    const scrollTop = window.pageYOffset || document.documentElement.scrollTop;
    ctx.clearRect(0, 0, globalCanvas.width, globalCanvas.height);
    ctx.beginPath();
    ctx.strokeStyle = "red";
    ctx.lineWidth = 5;
    const srcCenter = getTileCenter(
      `.tile[data-coords-x="${source.x}"][data-coords-y="${source.y}"]`,
    );
    const tgtCenter = $tgtTile && $tgtTile.length
      ? getTileCenter($tgtTile)
      : getTileCenter(
        `.tile[data-coords-x="${coordsx}"][data-coords-y="${coordsy}"]`,
      );
    ctx.moveTo(srcCenter.x, srcCenter.y);
    ctx.lineTo(tgtCenter.x, tgtCenter.y);
    const arrowSize = 10;
    const angle = Math.atan2(
      tgtCenter.y - srcCenter.y,
      tgtCenter.x - srcCenter.x,
    );
    if (Math.floor(currentDistance) <= targetModeMaxRange && valid_target) {
      ctx.moveTo(
        tgtCenter.x - arrowSize * Math.cos(angle - Math.PI / 6),
        tgtCenter.y - arrowSize * Math.sin(angle - Math.PI / 6),
      );
      ctx.lineTo(tgtCenter.x, tgtCenter.y);
      ctx.lineTo(
        tgtCenter.x - arrowSize * Math.cos(angle + Math.PI / 6),
        tgtCenter.y - arrowSize * Math.sin(angle + Math.PI / 6),
      );
    } else {
      ctx.moveTo(tgtCenter.x - arrowSize, tgtCenter.y - arrowSize);
      ctx.lineTo(tgtCenter.x + arrowSize, tgtCenter.y + arrowSize);
      ctx.moveTo(tgtCenter.x + arrowSize, tgtCenter.y - arrowSize);
      ctx.lineTo(tgtCenter.x - arrowSize, tgtCenter.y + arrowSize);
    }
    ctx.font = "20px Arial";
    ctx.fillStyle = "red";
    ctx.fillText(
      `${currentDistance}ft`,
      tgtCenter.x,
      tgtCenter.y + $(".tile").height() / 2,
    );
    ctx.stroke();
  }

  function applyTargetPreview(targetX, targetY, showX, showY, $tile, data, baseTooltip) {
    const validation = extractTargetValidation(data);
    const cacheKey = `${targetX}-${targetY}`;
    let tooltip = baseTooltip || '';

    if (data && data.target_squares && data.target_squares.length > 0) {
      $(".tile").css("border", "none");
      data.target_squares.forEach((value) => {
        $(`.tile[data-coords-x="${value[0]}"][data-coords-y="${value[1]}"]`).css("border", "2px solid red");
      });
    } else {
      cacheTargetValidation(cacheKey, validation);
      drawTargetLine(globalCtx, source, targetX, targetY, validation.valid, $tile);
    }

    if (data && data.adv_info) {
      data.adv_info[0].forEach(
        (value) => (tooltip += `<p><span style="color: green;">+${value}</span></p>`),
      );
      data.adv_info[1].forEach(
        (value) => (tooltip += `<p><span style="color: red;">-${value}</span></p>`),
      );
    }

    if (!validation.valid && validation.primary_error) {
      tooltip += formatTargetInvalidTooltip(validation.primary_error);
      showTargetValidationHint(validation.primary_error);
    } else {
      hideTargetValidationHint();
    }

    updateCoordsBox(showX, showY, tooltip);
    return validation;
  }


  // Character switcher
  window.initDmPortraitSections = function initDmPortraitSections() {
    const $dock = $('#floating-entity-portraits.dm-portrait-dock');
    if (!$dock.length) {
      return;
    }

    $dock.find('.dm-portrait-section').each(function () {
      const $section = $(this);
      const key = $section.data('section');
      const storageKey = `natural20.dmPortraitSection.${key}`;
      const stored = window.localStorage.getItem(storageKey);
      if (stored === '1') {
        $section.removeClass('collapsed');
        $section.find('.dm-portrait-section-toggle').attr('aria-expanded', 'true');
      } else if (stored === '0') {
        $section.addClass('collapsed');
        $section.find('.dm-portrait-section-toggle').attr('aria-expanded', 'false');
      }
    });
    syncDmPortraitDockCompact($dock);

    $dock.off('click.dmPortraitToggle', '.dm-portrait-section-toggle');
    $dock.on('click.dmPortraitToggle', '.dm-portrait-section-toggle', function (event) {
      event.stopPropagation();
      event.preventDefault();
      const $section = $(this).closest('.dm-portrait-section');
      const key = $section.data('section');
      $section.toggleClass('collapsed');
      const expanded = !$section.hasClass('collapsed');
      $(this).attr('aria-expanded', expanded ? 'true' : 'false');
      if (key) {
        window.localStorage.setItem(`natural20.dmPortraitSection.${key}`, expanded ? '1' : '0');
      }
      syncDmPortraitDockCompact($dock);
    });
  };

  function syncDmPortraitDockCompact($dock) {
    const allCollapsed = $dock.find('.dm-portrait-section').length > 0
      && $dock.find('.dm-portrait-section:not(.collapsed)').length === 0;
    $dock.toggleClass('is-compact', allCollapsed);
  }

  initDmPortraitSections();

  $('#floating-entity-portraits').on('click', '.floating-entity-portrait', function (event) {
    if ($(event.target).closest('.dm-portrait-section-toggle, .portrait-quick-action').length) {
      return;
    }
    const entity_uid = $(this).data('id');
    switchPOV(entity_uid, globalCanvas);
    setTimeout(() => {
      try { Chat.refreshLocalConversationPresence({ silent: true }); } catch (_) { }
    }, 150);
  });

  $('#floating-entity-portraits').on('click', '.portrait-quick-action[data-portrait-action="character-sheet"]', function (event) {
    event.preventDefault();
    event.stopPropagation();
    const entityUid = $(this).data('entityUid') || $(this).attr('data-entity-uid');
    if (!entityUid) {
      return;
    }
    window.open(`info?id=${encodeURIComponent(entityUid)}`, '_blank');
  });

  // Highlight tile info or draw movement data on hover.
  $(".tiles-container").on("mouseenter", ".tile", function () {
    renderMoveQuickAction($(this));
  });

  $(".tiles-container").on("mouseleave", ".tile", function () {
    hideMoveQuickActionLayer($(this));
    clearMoveQuickActionPreview();
    cancelMoveQuickActionPathRequest();
  });

  $(".tiles-container").on('mouseenter', '.move-quick-action', function (event) {
    event.stopPropagation();
    const $btn = $(this);
    const $tile = $btn.closest('.tile');
    resolveMoveQuickActionPathToTile($tile, (data) => {
      if (!$btn.is(':hover')) {
        return;
      }
      if (!data || !data.path || data.path.length <= 1) {
        return;
      }
      moveQuickActionPreviewActive = true;
      const availableCost = data.cost && data.cost.original_budget != null && data.cost.budget != null
        ? (data.cost.original_budget - data.cost.budget) * 5
        : 0;
      Utils.drawMovementPath(
        globalCtx,
        data.path,
        availableCost,
        data.placeable !== false,
        data.terrain_info,
        movementPathDrawOptions(data),
      );
    });
  });

  $(".tiles-container").on('mouseleave', '.move-quick-action', function () {
    clearMoveQuickActionPreview();
  });

  $(".tiles-container").on('click', '.move-quick-action', function (event) {
    event.stopPropagation();
    event.preventDefault();
    executeMoveQuickAction(this);
  });

  $(".tiles-container").on("mouseover", ".tile", function () {
    const $tile = $(this);
    const moveDest = resolveStackMovementDest($tile);
    const targetCoords = resolveStackTargetCoords($tile);
    const coordsx = moveDest.localX;
    const coordsy = moveDest.localY;
    const pathDestX = moveDest.x;
    const pathDestY = moveDest.y;
    const targetX = targetCoords.x;
    const targetY = targetCoords.y;
    const displayX = $tile.data("world-x");
    const displayY = $tile.data("world-y");
    const showX = stackDescentModeActive() && (pathDestX !== coordsx || pathDestY !== coordsy)
      ? pathDestX
      : (targetMode && (targetX !== coordsx || targetY !== coordsy) ? targetX : (displayX !== undefined ? displayX : coordsx));
    const showY = stackDescentModeActive() && (pathDestX !== coordsx || pathDestY !== coordsy)
      ? pathDestY
      : (targetMode && (targetX !== coordsx || targetY !== coordsy) ? targetY : (displayY !== undefined ? displayY : coordsy));
    let tooltip = $tile.data("tooltip") || "";
    tooltip = appendTeleporterHoverHint($tile, tooltip);
    if (targetMode) {
      const cacheKey = `${targetX}-${targetY}`;
      $(".highlighted").removeClass("highlighted");
      $(".tile").css("z-index", 0);
      $(this).css("z-index", 999);
      const data_payload = JSON.stringify({
        id: globalSourceEntity,
        x: targetX,
        y: targetY,
        coneMode: coneMode,
        action_info: globalActionInfo,
        opts: globalOpts,
      });

      const cachedValidation = getCachedTargetValidation(cacheKey);
      if (cachedValidation) {
        let tooltip = $tile.data("tooltip") || "";
        tooltip = appendTeleporterHoverHint($tile, tooltip);
        applyTargetPreview(targetX, targetY, showX, showY, $tile, {
          valid_target: cachedValidation.valid,
          primary_error: cachedValidation.primary_error,
          error_messages: cachedValidation.error_messages,
        }, tooltip);
      } else {
        Utils.ajaxGet("/target", { payload: data_payload }, (data) => {
          applyTargetPreview(targetX, targetY, showX, showY, $tile, data, tooltip);
        });
      }
    } else {
      // Always show tile inspect info on hover (coords, terrain, entity/object
      // labels). Path preview stays gated on moveMode so targeting hover does
      // not fire /path requests.
      hideTargetValidationHint();
      updateCoordsBox(showX, showY, tooltip);
      if (moveMode && !keyboardMovementMode && source && (pathDestX !== source.x || pathDestY !== source.y)) {
        // Skip duplicate work when the mouseover bubbles from child elements
        // (entity sprites, tooltips, etc.) without changing the tile.
        if (lastHoverCoords && lastHoverCoords.x === pathDestX && lastHoverCoords.y === pathDestY) {
          return;
        }
        lastHoverCoords = { x: pathDestX, y: pathDestY };
        if (!localPathCostMap) {
          loadLocalPathCostMap(source, function () {});
        }
        const cacheKey = `${source.x}-${source.y}-${pathDestX}-${pathDestY}-${pivotPoints.join("-")}-${stackDescentModeActive() ? 'sd' : 'n'}`;

        // Function to process and draw movement path
        const processMovementData = (data) => {
          if (!data || !data.cost) return;
          if (data.can_move === false) return;
          if (data.cost.budget < 0) return;

          const available_cost = (data.cost.original_budget - data.cost.budget) * 5;
          if (data.path) {
            // Create a new movement path starting with accumulated path
            movePath = [...accumulatedPath];
            if (accumulatedPath.length > 0) {
              movePath.pop();
            }

            // Store current position for potential pivot points
            currentPosition = {
              x: pathDestX,
              y: pathDestY,
              localX: coordsx,
              localY: coordsy,
              cost: available_cost,
              path: data.path
            };
            lastPathForJump = data.path;
            lastStackDescent = data.stack_descent || null;

            // Add the new path segment to the movement path
            movePath = [...movePath, ...data.path];
            // Draw the complete path; if jumpMode, suppress base arrow to avoid double arrows
            Utils.drawMovementPath(
              globalCtx,
              movePath,
              available_cost,
              data.placeable,
              data.terrain_info,
              movementPathDrawOptions(data)
            );

            if (data.stack_descent && data.stack_descent.label) {
              const warn = `<p style="color:#cc5500;font-weight:bold;">${data.stack_descent.label}</p>`;
              $("#coords-box").append(warn);
            }

            // If in jump mode, draw a curved green segment preview and enforce jump range limit
            if (jumpMode && movePath.length > 1) {
              const entity_uid = $("body").attr('data-pov-entity');
              // Decide running vs standing: if we moved at least 2 tiles before the jump start, it's running
              const preJumpLen = jumpStartIndex !== null ? Math.max(0, jumpStartIndex) : movePath.length - 1;
              const running = preJumpLen >= 1; // running if we had at least one step before jump start
              Utils.ajaxGet('/jump_info', { id: entity_uid, running: running ? 1 : 0 }, (jdata) => {
                const grids = jdata.grids || jdata.running_grids || 0;
                // Determine start and end indices for the jump segment
                const startIdx = (jumpStartIndex !== null) ? jumpStartIndex : (movePath.length - 2);
                let maxEndIdx = Math.min(movePath.length - 1, startIdx + grids);
                // Draw curved line from start to clamped end
                const from = { x: movePath[startIdx][0], y: movePath[startIdx][1] };
                const to = { x: movePath[maxEndIdx][0], y: movePath[maxEndIdx][1] };
                // Always render a smooth curve even for straight (horizontal/vertical) jumps by forcing a control offset
                drawLine(globalCtx, from, to, { lineWidth: 4, withArrow: true, randomCurve: true, strokeStyle: 'green', text: 'Jump' });

                // Optionally visualize disallowed further squares beyond maxEndIdx by marking borders
                $(".tile").css("border", "none");
                for (let i = maxEndIdx + 1; i < movePath.length; i++) {
                  const x = movePath[i][0], y = movePath[i][1];
                  $(`.tile[data-coords-x="${x}"][data-coords-y="${y}"]`).css('border', '2px solid #999');
                }
              });
            }
          }
        };

        // Check if we have cached data for this path
        if (move_path_cache[cacheKey]) {
          processMovementData(move_path_cache[cacheKey]);
        } else {
          // Clear any existing timer
          if (pathDebounceTimer) {
            clearTimeout(pathDebounceTimer);
          }
          // Abort any in-flight request from a previous hover so the latest
          // destination always wins (prevents arrow lag/flicker on fast moves).
          if (currentPathXhr && typeof currentPathXhr.abort === 'function') {
            try { currentPathXhr.abort(); } catch (e) { /* ignore */ }
            currentPathXhr = null;
          }

          // Set a new timer to delay the path request.
          // Increased from 50ms to 100ms to reduce excessive /path requests during
          // attack targeting hover. The original 50ms caused 15+ requests per turn
          // because mouseover events fire rapidly as the cursor moves over tile
          // children (sprites, tooltips, etc.).
          const pendingDest = { x: pathDestX, y: pathDestY, localX: coordsx, localY: coordsy };
          pathDebounceTimer = setTimeout(() => {
            pathDebounceTimer = null;
            if (!moveMode || !source) {
              return;
            }
            // If the cursor has moved off this tile by the time the timer
            // fires, skip the fetch entirely.
            if (!lastHoverCoords ||
                lastHoverCoords.x !== pendingDest.x ||
                lastHoverCoords.y !== pendingDest.y) {
              return;
            }
            const localData = computeLocalPathResponse(
              source,
              { x: pendingDest.x, y: pendingDest.y },
              accumulatedPath,
            );
            if (localData) {
              setMovePathCache(cacheKey, localData);
              if (lastHoverCoords &&
                  lastHoverCoords.x === pendingDest.x &&
                  lastHoverCoords.y === pendingDest.y) {
                processMovementData(localData);
              }
              return;
            }
            // Fetch path data from server using a jqXHR we can abort.
            currentPathXhr = $.ajax({
              url: "/path",
              type: 'GET',
              data: Object.assign({
                from: source,
                to: { x: pendingDest.x, y: pendingDest.y },
                accumulatedPath: accumulatedPath.length > 0 ? JSON.stringify(accumulatedPath) : null
              }, stackPathRequestExtras(pendingDest.x, pendingDest.y)),
              success: (data) => {
                currentPathXhr = null;
                setMovePathCache(cacheKey, data);
                // Update localPathCanMove from the server response so that
                // mouse-over movement preview is disabled when the entity
                // cannot physically move (e.g., unconscious).
                if (data.can_move !== undefined) {
                  localPathCanMove = data.can_move !== false;
                }
                // Only render if the cursor is still on the requested tile.
                if (lastHoverCoords &&
                    lastHoverCoords.x === pendingDest.x &&
                    lastHoverCoords.y === pendingDest.y) {
                  processMovementData(data);
                }
              },
              error: (jqXHR, textStatus) => {
                if (textStatus !== 'abort') {
                  console.error('Error with GET /path:', textStatus);
                }
                currentPathXhr = null;
              }
            });
          }, 100);
        }
      }
    }
  });

  // Add mouseout handler to clear the debounce timer
  $(".tiles-container").on("mouseout", ".tile", function () {
    if (pathDebounceTimer) {
      clearTimeout(pathDebounceTimer);
      pathDebounceTimer = null;
    }
    // Don't reset lastHoverCoords here — mouseout fires when bubbling into
    // child elements as well, and we still want to suppress duplicate work.
  });

  // Example: add all entities to initiative.
  $("#add-all-entities").on("click", function (event) {
    $(".tile").each(function () {
      const entity_uid = $(this).data("coords-id");
      const $btn = $(this).find("button.add-to-turn-order");
      if (entity_uid && $btn.length) {
        addToInitiative($btn, battleEntityList);
      }
    });
  });

  // Cancel ongoing interactions on Escape.
  $(document).on("keydown", (event) => {
    if (event.keyCode === 27) {
      if (moveMode || targetMode || multiTargetMode || coneMode || keyboardMovementMode) {
        moveMode = targetMode = multiTargetMode = coneMode = aoePointTargetMode = false;
        accumulatedPath = [];
        pivotPoints = [];
        valid_target_cache = {};
        clearMovePathCache();
        multiTargetList = [];
        if (typeof hideMultiTargetBar === 'function') { hideMultiTargetBar(); }
        jumpMode = false;
        jumpStartIndex = null;
        lastPathForJump = null;
        movePath = [];
        source = null;
        currentPosition = null;
        lastHoverCoords = null;
        if (typeof resetKeyboardMovement === 'function' && keyboardMovementMode) {
          try { resetKeyboardMovement(); } catch (err) { keyboardMovementMode = false; }
        }
        if (globalCtx && globalCanvas) {
          globalCtx.clearRect(0, 0, globalCanvas.width, globalCanvas.height);
        }
        $(".tile").css("border", "none");
        globalActionInfo = globalOpts = null;
      }
      $(".add-to-target, .popover-menu-2, .popover-menu").hide();
      closeCenterActionBar();
    }
    // if 'q' is pressed, toggle move path
    if (event.keyCode === 81) {
      if (isTypingIntoField(event.target) || isTypingIntoField(document.activeElement)) {
        return;
      }
      // get current mouse tile coords
      if (currentPosition) {
        const coordsx = currentPosition.x;
        const coordsy = currentPosition.y;
        if (source.x !== coordsx || source.y !== coordsy) {
          pivotPoints.push([source.x, source.y]);
          accumulatedPath = accumulatedPath.concat(currentPosition.path);
          source = { x: coordsx, y: coordsy };
        }
      }
    }
    // Toggle jump mode with 'J' key
    if (event.key && event.key.toLowerCase() === 'j') {
      if (moveMode) {
        // Toggle
        jumpMode = !jumpMode;
        if (!jumpMode) {
          jumpStartIndex = null;
          // clear any jump overlay by redrawing base path
          if (movePath && movePath.length > 1 && typeof Utils.drawMovementPath === 'function') {
            Utils.drawMovementPath(globalCtx, movePath, currentPosition ? currentPosition.cost : 0, true, null, { suppressArrow: false });
            $(".tile").css("border", "none");
          }
        } else {
          // entering jump mode: mark current end as start
          jumpStartIndex = (movePath && movePath.length > 0) ? (movePath.length - 1) : null;
        }
      }
    }
    if (event.key === 'Control') {
      ctrlHeldForDescent = true;
    }
  });

  $(document).on('keyup.stackDescent', (event) => {
    if (event.key === 'Control') {
      ctrlHeldForDescent = false;
    }
  });

  // Cancel interactions on right-click.
  $("#main-map-area").on("contextmenu", function (e) {
    // Always suppress the browser context menu for right-click (button 2)
    // since right-click is exclusively used for map panning.
    if (e.button === 2) {
      e.preventDefault();
      return;
    }
    // If a right-drag pan just finished, swallow the menu without cancelling
    // an active targeting/move mode.
    if (suppressNextContextMenu || rightPanDragged) {
      e.preventDefault();
      suppressNextContextMenu = false;
      rightPanDragged = false;
      return;
    }
    if (targetMode || multiTargetMode || moveMode) {
      e.preventDefault();
      targetMode = multiTargetMode = moveMode = coneMode = aoePointTargetMode = false;
      accumulatedPath = [];
      pivotPoints = [];
      valid_target_cache = {};
      clearMovePathCache();
      jumpMode = false;
      jumpStartIndex = null;
      lastPathForJump = null;
      globalCtx.clearRect(0, 0, globalCanvas.width, globalCanvas.height);
      $(".tile").css("border", "none");
      $(".add-to-target, .popover-menu-2").hide();
      if (typeof hideMultiTargetBar === 'function') { hideMultiTargetBar(); }
    }
  });

  // Multi-target selection.
  function showMultiTargetBar() {
    $('#multi-target-confirm-bar').show();
    updateMultiTargetBar();
  }
  function hideMultiTargetBar() {
    $('#multi-target-confirm-bar').hide();
    $('#multi-target-confirm').prop('disabled', true);
  }
  function updateMultiTargetBar() {
    const cur = multiTargetList.length;
    const max = max_targets || 0;
    $('#multi-target-confirm-bar__current').text(cur);
    $('#multi-target-confirm-bar__max').text(max);
    $('#multi-target-confirm').prop('disabled', cur < 1);
  }

  $(document).on('click', '#multi-target-confirm', function (e) {
    if (!multiTargetMode) { return; }
    if (multiTargetList.length < 1) { return; }
    if (typeof targetModeCallback === 'function') {
      targetModeCallback(multiTargetList);
    }
    targetMode = multiTargetMode = false;
    valid_target_cache = {};
    multiTargetList = [];
    $('.add-to-target, .popover-menu-2').hide();
    hideMultiTargetBar();
    if (globalCtx && globalCanvas) {
      globalCtx.clearRect(0, 0, globalCanvas.width, globalCanvas.height);
    }
    e.stopPropagation();
  });

  $(document).on('click', '#multi-target-cancel', function (e) {
    targetMode = multiTargetMode = false;
    valid_target_cache = {};
    multiTargetList = [];
    $('.add-to-target, .popover-menu-2').hide();
    hideMultiTargetBar();
    if (globalCtx && globalCanvas) {
      globalCtx.clearRect(0, 0, globalCanvas.width, globalCanvas.height);
    }
    e.stopPropagation();
  });

  $(".tiles-container").on("click", ".add-to-target", function (e) {
    const $tile = $(this).closest(".tile");
    const entity_uid = $tile.data("coords-id");
    const coordsx = $tile.data("coords-x");
    const coordsy = $tile.data("coords-y");
    if (!multiTargetList.includes(entity_uid) || !multiTargetModeUnique) {
      if (multiTargetList.length < max_targets) {
        multiTargetList.push(entity_uid);
        if (multiTargetModeUnique) $(this).hide();
        drawLine(globalCtx, source, { x: coordsx, y: coordsy }, {
          lineWidth: 3,
          withArrow: false,
          randomCurve: true,
        });
        updateMultiTargetBar();
      }
    }
    e.stopPropagation();
  });

  // --- Floating Menu Handlers ---
  $("#expand-menu").click(() => {
    $("#menu").fadeIn();
    $("#expand-menu").hide().attr('aria-expanded', 'false');
    $("#collapse-menu").show().attr('aria-expanded', 'true');
  });

  $("#collapse-menu").click(() => {
    $("#menu").fadeOut();
    $("#expand-menu").show().attr('aria-expanded', 'false');
    $("#collapse-menu").hide().attr('aria-expanded', 'false');
  });

  $("#start-battle").click(() => {
    $("#battle-turn-order").fadeIn();
    battle_setup = true;
    enqueueTileRefresh({ is_setup: true });
  });

  $("#start-match").click(() => {
    ajaxPost(
      "/battle",
      { battle_turn_order: [] },
      () => {
        $("#start-match").hide();
        $("#end-battle").show();
        battle_setup = false;
        enqueueTileRefresh({ is_setup: false, pov: false, x: 0, y: 0 });
      },
      true,
    );
  });

  const showConsole = () => {
    $("#console-container").fadeIn();
    Utils.setVttMenuLabel("#open-console", "Hide Combat Log");
  };

  const hideConsole = () => {
    $("#console-container").fadeOut();
    Utils.setVttMenuLabel("#open-console", "Combat Log");
  };

  $("#open-console").click(() => {
    $("#console-container").is(":visible") ? hideConsole() : showConsole();
  });

  $("#reset-narrations").click(() => {
    try {
      var keys = [];
      for (var i = 0; i < localStorage.length; i++) {
        var k = localStorage.key(i);
        if (k && k.indexOf('narration_shown_') === 0) keys.push(k);
      }
      keys.forEach(function(k) { localStorage.removeItem(k); });
      $.post('/reset_narrations', function() {
        alert('Narrations reset (' + keys.length + ' cleared). They will show again when you enter their areas.');
      }).fail(function() {
        alert('Narrations partially reset (client only). Server reset failed.');
      });
    } catch (e) {
      alert('Could not clear narration data.');
    }
  });

  $("#hide-combat-log").click(hideConsole);

  $("#start-initiative").click(() => {
    const battle_turn_order = $(".turn-order-item")
      .map(function () {
        const { id } = $(this).data();
        const group = $(this).find(".group-select").val();
        const controller = $(this).find(".controller-select").val();
        return { id, group, controller };
      })
      .get();
    ajaxPost(
      "/battle",
      { battle_turn_order },
      (data) => {
        $(".add-to-turn-order").hide();
      },
      true,
    );
  });

  $("#end-battle").click(() => {
    ajaxPost("/stop", {}, (data) => {
      console.log("Battle stopped successfully");
    });
  });

  $(".tiles-container").on("click", ".add-to-turn-order", function (event) {
    addToInitiative($(this), battleEntityList);
    event.stopPropagation();
  });

  $("#turn-order").on("click", ".remove-turn-order-item", function (e) {
    e.preventDefault();
    e.stopPropagation();
    const $item = $(this).closest(".turn-order-item");
    const entity_uid = $item.data("id");
    if (!entity_uid) {
      $item.remove();
      return;
    }
    ajaxPost("/remove_from_battle", { entity_uid: entity_uid }, (data) => {
      // The DOM row will be replaced when the server-side broadcast
      // triggers a turn-order refresh; remove eagerly for snappier UX.
      $item.remove();
    });
  });

  $("#turn-order").on("click", ".token-image", function () {
    centerOnEntityId($(this).data("id"));
  });

  $("#turn-order").on("click", "#next-turn", function () {
    ajaxPost("/next_turn", {}, (data) => {
      console.log("Next turn request successful:", data);
    });
  });

  $("#turn-order").on("click", ".turn-order-item", function () {
    centerOnEntityId($(this).data("id"));
  });

  // Drag and drop functionality for turn order reordering (DM only)
  let draggedElement = null;
  let draggedIndex = -1;

  $("#turn-order").on("dragstart", ".turn-order-item[draggable='true']", function (e) {
    // Only allow dragging from the drag handle or empty areas, not from form elements
    const target = e.originalEvent.target;
    if (target.tagName === 'INPUT' || target.tagName === 'SELECT' || target.tagName === 'BUTTON' ||
      target.closest('input, select, button')) {
      e.preventDefault();
      return false;
    }

    draggedElement = this;
    draggedIndex = $(this).index();
    $(this).addClass("dragging");

    // Set drag data
    e.originalEvent.dataTransfer.effectAllowed = "move";
    e.originalEvent.dataTransfer.setData("text/html", this.outerHTML);

    console.log("Started dragging entity:", $(this).data("id"));
  });

  $("#turn-order").on("dragend", ".turn-order-item[draggable='true']", function (e) {
    $(this).removeClass("dragging");
    $(".turn-order-item").removeClass("drag-over drag-over-bottom");
    draggedElement = null;
    draggedIndex = -1;
  });

  $("#turn-order").on("dragover", ".turn-order-item", function (e) {
    e.preventDefault();
    e.originalEvent.dataTransfer.dropEffect = "move";

    if (this !== draggedElement) {
      const rect = this.getBoundingClientRect();
      const midpoint = rect.top + rect.height / 2;
      const mouseY = e.originalEvent.clientY;

      $(".turn-order-item").removeClass("drag-over drag-over-bottom");

      if (mouseY < midpoint) {
        $(this).addClass("drag-over");
      } else {
        $(this).addClass("drag-over-bottom");
      }
    }
  });

  $("#turn-order").on("drop", ".turn-order-item", function (e) {
    e.preventDefault();

    if (this !== draggedElement) {
      const targetIndex = $(this).index();
      const rect = this.getBoundingClientRect();
      const midpoint = rect.top + rect.height / 2;
      const mouseY = e.originalEvent.clientY;

      let newIndex = targetIndex;
      if (mouseY >= midpoint) {
        newIndex = targetIndex + 1;
      }

      // Adjust for moving elements within the same container
      if (draggedIndex < newIndex) {
        newIndex--;
      }

      // Get the new order of entity IDs
      const $turnOrderItems = $("#turn-order .turn-order-item");
      const entityOrder = [];

      $turnOrderItems.each(function (index) {
        if (this !== draggedElement) {
          entityOrder.push($(this).data("id"));
        }
      });

      // Insert the dragged element at the new position
      const draggedEntityId = $(draggedElement).data("id");
      entityOrder.splice(newIndex, 0, draggedEntityId);

      console.log("Reordering initiative:", entityOrder);

      // Send the new order to the server
      ajaxPost("/reorder_initiative",
        { entity_order: entityOrder },
        (data) => {
          if (data.status === 'ok') {
            console.log("Initiative reordered successfully");
            // Refresh the turn order display
            refreshTurnOrder();
          } else {
            console.error("Failed to reorder initiative:", data.error);
            alert("Failed to reorder initiative: " + (data.error || "Unknown error"));
          }
        },
        true
      );
    }

    $(".turn-order-item").removeClass("drag-over drag-over-bottom");
  });

  // Prevent default drag behavior on the turn order container
  $("#turn-order").on("dragover", function (e) {
    e.preventDefault();
  });

  // Prevent dragging when clicking on form elements
  $("#turn-order").on("mousedown", "input, select, button", function (e) {
    e.stopPropagation();
  });

  $("#select-soundtrack").click(() => {
    $.get("/tracks", { track_id: active_track_id }, (data) => {
      $("#modal-1 .modal-content").html(data);
      $("#modal-1").modal("show");

      // Initialize DM Sound Manager after modal loads
      setTimeout(() => {
        DMSoundManager.init();
      }, 100);
    });
  });

  // Character Builder menu entry
  $(document).on('click', '#character-builder', function () {
    window.location.href = '/character_builder';
  });

  // NPC Spawner functionality
  let availableNpcs = [];
  const npcCrGroupCollapsed = {};

  $("#toggle-npc-spawner").click(() => {
    const $npcSpawner = $("#npc-spawner");
    if ($npcSpawner.is(":visible")) {
      $npcSpawner.hide();
    } else {
      loadAvailableNPCs();
      $npcSpawner.show();
    }
  });

  $("#close-npc-spawner").click(() => {
    $("#npc-spawner").hide();
  });

  // Load available NPCs function
  function loadAvailableNPCs() {
    $.get("/available_npcs", (data) => {
      if (data.npcs) {
        availableNpcs = data.npcs;
        displayNPCs(availableNpcs);
        if (typeof Utils !== 'undefined' && Utils.applyNpcSpawnerSearch) {
          Utils.applyNpcSpawnerSearch($("#npc-search-input").val());
        }
      }
    }).fail((jqXHR) => {
      console.error("Failed to load NPCs:", jqXHR.responseJSON ? jqXHR.responseJSON.error : jqXHR.statusText);
      $("#npc-list").html('<div class="alert alert-danger">Failed to load NPCs</div>');
    });
  }

  function npcSpawnerEscape(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/"/g, '&quot;');
  }

  function buildNpcSpawnerItem(npc) {
    const unique = !!npc.unique;
    const onMap = npc.on_map ? String(npc.on_map) : '';
    const uniqueBadge = unique
      ? '<span class="npc-unique-badge" title="Named unique NPC — one token per map set">Unique</span>'
      : '';
    const onMapBadge = onMap
      ? `<span class="npc-on-map-badge" title="Already on ${npcSpawnerEscape(onMap)} — drop to move">On ${npcSpawnerEscape(onMap)}</span>`
      : '';
    const titleParts = [`AC: ${npc.ac}`, `HP: ${npc.hp}`];
    if (unique) {
      titleParts.push(onMap ? `Drop to move from ${onMap}` : 'Unique NPC');
    }
    const haystack = (typeof Utils !== 'undefined' && Utils.npcSpawnerSearchHaystack)
      ? Utils.npcSpawnerSearchHaystack(npc)
      : String(npc.name || '').toLowerCase();
    const $npcItem = $(`
      <div class="npc-item" draggable="true" data-npc-type="${npcSpawnerEscape(npc.id)}"
           data-unique="${unique ? '1' : '0'}"
           data-entity-uid="${npcSpawnerEscape(npc.entity_uid || '')}"
           data-on-map="${npcSpawnerEscape(onMap)}"
           data-search="${npcSpawnerEscape(haystack)}"
           title="${npcSpawnerEscape(titleParts.join(', '))}">
        <img class="npc-item-image" src="/assets/${npcSpawnerEscape(npc.image)}" alt="${npcSpawnerEscape(npc.name)}" onerror="this.onerror=null;this.src='/static/assets/token_player.png'">
        <div class="npc-item-info">
          <div class="npc-item-name-row">
            <div class="npc-item-name">${npcSpawnerEscape(npc.name)}</div>
            ${uniqueBadge}
          </div>
          <div class="npc-item-type">CR ${npcSpawnerEscape(npc.cr)} • ${npcSpawnerEscape(npc.size)} • AC ${npcSpawnerEscape(npc.ac)}${onMapBadge ? ' ' + onMapBadge : ''}</div>
        </div>
      </div>
    `);
    $npcItem.on('click', function (e) {
      e.stopPropagation();
      const npcType = $(this).data('npc-type');
      const entityUid = $(this).attr('data-entity-uid');
      const alreadyOnMap = $(this).attr('data-on-map');
      if ($(this).attr('data-unique') === '1' && alreadyOnMap && entityUid) {
        if (typeof centerOnEntityId === 'function') {
          centerOnEntityId(entityUid);
        }
        return;
      }
      ajaxPost(
        "/spawn_npc",
        { npc_type: npcType, x: 0, y: 0 },
        (data) => {
          if (data.status === 'ok') {
            if (data.moved && typeof Utils !== 'undefined' && Utils.showToast) {
              const from = data.from_map ? (' from ' + data.from_map) : '';
              Utils.showToast('Moved unique NPC' + from);
            }
            if (data.entity_uid) {
              centerOnEntityId(data.entity_uid);
            }
            loadAvailableNPCs();
          } else {
            alert("Failed to spawn NPC: " + (data.error || "Unknown error"));
          }
        },
        true
      );
    });
    return $npcItem;
  }

  // Display NPCs in the spawner window, grouped by CR accordion folders
  function displayNPCs(npcs) {
    const $npcList = $("#npc-list");
    $npcList.empty();
    const groups = (typeof Utils !== 'undefined' && Utils.groupNpcsByCr)
      ? Utils.groupNpcsByCr(npcs)
      : [{ label: 'NPCs', sort: 0, npcs: npcs || [] }];

    groups.forEach((group) => {
      const collapsed = Object.prototype.hasOwnProperty.call(npcCrGroupCollapsed, group.label)
        ? !!npcCrGroupCollapsed[group.label]
        : true;
      const $group = $(`
        <div class="npc-cr-group${collapsed ? ' collapsed' : ''}" data-cr-label="${npcSpawnerEscape(group.label)}" data-user-collapsed="${collapsed ? '1' : '0'}">
          <button type="button" class="npc-cr-group-header">
            <span class="npc-cr-group-chevron" aria-hidden="true"></span>
            <span class="npc-cr-group-label">${npcSpawnerEscape(group.label)}</span>
            <span class="npc-cr-group-count">${group.npcs.length}</span>
          </button>
          <div class="npc-cr-group-items"></div>
        </div>
      `);
      const $items = $group.find('.npc-cr-group-items');
      group.npcs.forEach((npc) => {
        $items.append(buildNpcSpawnerItem(npc));
      });
      $npcList.append($group);
    });
  }

  $("#npc-list").on("click", ".npc-cr-group-header", function (e) {
    e.preventDefault();
    e.stopPropagation();
    const $group = $(this).closest('.npc-cr-group');
    $group.toggleClass('collapsed');
    const collapsed = $group.hasClass('collapsed');
    $group.attr('data-user-collapsed', collapsed ? '1' : '0');
    npcCrGroupCollapsed[$group.attr('data-cr-label')] = collapsed;
  });

  // NPC search functionality — filters items and expands matching CR folders
  $("#npc-search-input").on("input", function () {
    if (typeof Utils !== 'undefined' && Utils.applyNpcSpawnerSearch) {
      Utils.applyNpcSpawnerSearch($(this).val());
      return;
    }
    const searchTerm = $(this).val().toLowerCase();
    $(".npc-item").each(function () {
      const npcName = $(this).find(".npc-item-name").text().toLowerCase();
      if (npcName.includes(searchTerm)) {
        $(this).show();
      } else {
        $(this).hide();
      }
    });
  });

  // NPC drag and drop functionality
  let draggedNPC = null;
  let draggedObject = null;

  $("#npc-list").on("dragstart", ".npc-item", function (e) {
    draggedNPC = $(this).data("npc-type");
    $(this).addClass("dragging");
    e.originalEvent.dataTransfer.effectAllowed = "copy";
    e.originalEvent.dataTransfer.setData("text/plain", draggedNPC);
    centerDragImageOnCursor(e, $(this));

    // Add visual feedback for empty tiles
    $("body").addClass("npc-drag-active");
    $(".tile").each(function () {
      if ($(this).find(".entity").length === 0) {
        $(this).addClass("empty-tile-highlight");
      }
    });

    console.log("Started dragging NPC:", draggedNPC);
  });

  $("#npc-list").on("dragend", ".npc-item", function (e) {
    $(this).removeClass("dragging");
    $(".tile").removeClass("battlefield-drop-zone drag-over empty-tile-highlight");
    $("body").removeClass("npc-drag-active");
    draggedNPC = null;
  });

  // Battlefield drop zone functionality — resolve tile from pointer, not event target
  // (avoids -1 offsets when the map is panned/zoomed or the drag hotspot is top-left).
  $(".tiles-container").on("dragover", function (e) {
    if (!(draggedNPC || draggedObject || $('body').hasClass('object-drag-active') || $('body').hasClass('notebook-map-drag-active'))) {
      return;
    }
    e.preventDefault();
    if (e.originalEvent.dataTransfer) {
      e.originalEvent.dataTransfer.dropEffect = $('body').hasClass('notebook-map-drag-active') ? 'copy' : 'copy';
    }

    const hit = tileCoordsFromClientPoint(e.originalEvent.clientX, e.originalEvent.clientY);
    $(".tile").removeClass("battlefield-drop-zone drag-over notebook-map-drop-target");
    if (!hit) {
      return;
    }

    if ($('body').hasClass('notebook-map-drag-active')) {
      hit.$tile.addClass('notebook-map-drop-target');
      return;
    }

    if (draggedNPC && hit.$tile.find(".entity").length > 0) {
      return;
    }
    hit.$tile.addClass("battlefield-drop-zone drag-over");
  });

  $(".tiles-container").on("drop", function (e) {
    e.preventDefault();

    const hit = tileCoordsFromClientPoint(e.originalEvent.clientX, e.originalEvent.clientY);
    if (!hit) {
      return;
    }
    const { x, y, $tile, mapName } = hit;

    if (window.Notebook && typeof window.Notebook.handleMapDrop === 'function') {
      if (window.Notebook.handleMapDrop(e, hit)) {
        $(".tile").removeClass("battlefield-drop-zone drag-over notebook-map-drop-target");
        return;
      }
    }

    if (draggedNPC) {
      // Handle NPC spawning (only on empty tiles)
      const hasEntity = $tile.find(".entity").length > 0;
      if (!hasEntity) {
        const npcType = draggedNPC;
        console.log(`Placing ${npcType} at (${x}, ${y})`);
        enqueueMapPlacement(() => {
          if (isEditMode()) {
            const payload = { npc_type: npcType, x, y };
            if (mapName) {
              payload.map_name = mapName;
            }
            return postJsonPlacement('/edit/place_entity', payload);
          }
          return postJsonPlacement('/spawn_npc', {
            npc_type: npcType,
            x: x,
            y: y,
          });
        })
          .then((data) => {
            if (data && data.placement) {
              console.log("NPC placed in campaign YAML:", data.placement);
              if (data.placement.moved && typeof Utils !== 'undefined' && Utils.showToast) {
                const from = data.placement.from_map ? (' from ' + data.placement.from_map) : '';
                Utils.showToast('Moved unique NPC' + from);
              }
            } else if (data && data.entity_uid) {
              console.log("NPC spawned successfully:", data.entity_uid);
              if (data.moved && typeof Utils !== 'undefined' && Utils.showToast) {
                const from = data.from_map ? (' from ' + data.from_map) : '';
                Utils.showToast('Moved unique NPC' + from);
              }
            }
            afterMapPlacementRefresh();
            loadAvailableNPCs();
          })
          .catch((err) => {
            alert("Failed to place NPC: " + (err.message || "Unknown error"));
          });
      } else {
        console.log("Cannot place NPC: position is occupied");
      }
    } else if (draggedObject || $('body').hasClass('object-drag-active')) {
      const objectType = draggedObject || e.originalEvent.dataTransfer.getData('text/plain');
      if (!objectType) {
        console.warn('Drop received without object type');
        $(".tile").removeClass("battlefield-drop-zone drag-over empty-tile-highlight");
        $('body').removeClass('object-drag-active');
        draggedObject = null;
        return;
      }

      const placementLabel = (isEditMode() && isTerrainPreset(objectType))
        ? `terrain ${objectType}`
        : `object ${objectType}`;
      console.log(`Placing ${placementLabel} at (${x}, ${y})`);

      enqueueMapPlacement(() => {
        if (isEditMode()) {
          return placeInEditMode(objectType, x, y, mapName);
        }
        return postJsonPlacement('/spawn_object', {
          object_type: objectType,
          x: x,
          y: y,
        });
      })
        .then((data) => {
          if (data && data.entity_uid) {
            console.log("Object spawned successfully:", data.entity_uid);
          }
          afterMapPlacementRefresh();
        })
        .catch((err) => {
          alert("Failed to place on map: " + (err.message || "Unknown error"));
        });
    } else if ($('body').hasClass('pc-drag-active')) {
      // Handle PC placement/movement (only on empty tiles)
      const hasEntity = $tile.find(".entity").length > 0;
      if (!hasEntity) {
        const draggedData = e.originalEvent.dataTransfer.getData('text/plain');
        if (draggedData) {
          console.log(`Placing PC ${draggedData} at (${x}, ${y})`);

          if (isEditMode()) {
            // In edit mode, persist to campaign YAML
            const payload = { entity_uid: draggedData, x, y };
            if (mapName) {
              payload.map_name = mapName;
            }
            enqueueMapPlacement(() => postJsonPlacement('/edit/place_entity', payload))
              .then((data) => {
                if (data && data.placement) {
                  console.log("PC placed in campaign YAML:", data.placement);
                }
                afterMapPlacementRefresh();
              })
              .catch((err) => {
                alert("Failed to place PC: " + (err.message || "Unknown error"));
              });
          } else {
            // Use ghost token for PC moves in runtime mode
            movePCEntityTo(draggedData, x, y);
          }
        }
      } else {
        console.log("Cannot place PC: position is occupied");
      }
    }

    $(".tile").removeClass("battlefield-drop-zone drag-over notebook-map-drop-target");
  });

  // PC Spawner functionality
  $("#toggle-pc-spawner").click(() => {
    const $pcSpawner = $("#pc-spawner");
    if ($pcSpawner.is(":visible")) {
      $pcSpawner.hide();
    } else {
      loadPCs();
      $pcSpawner.show();
    }
  });

  $("#close-pc-spawner").click(() => {
    $("#pc-spawner").hide();
  });

  // Load PCs from server
  function loadPCs() {
    Utils.ajaxGet("/available_pcs", {}, (data) => {
      if (data.status === 'ok') {
        displayPCs(data.pcs);
      } else {
        console.error("Failed to load PCs:", data.error);
        $("#pc-list").html('<div class="alert alert-danger">Failed to load PCs</div>');
      }
    });
  }

  // Display PCs in the spawner window
  function displayPCs(pcs) {
    const $pcList = $("#pc-list");
    $pcList.empty();

    if (!pcs || pcs.length === 0) {
      $pcList.append('<div class="alert alert-info">No player characters available</div>');
      return;
    }

    pcs.forEach((pc) => {
      const classInfo = pc.class_and_level.length > 0 ? `Lvl ${pc.class_and_level[0][1] || '?'} ${pc.class_and_level[0][0] || 'Unknown'}` : 'Unknown Class';

      const $pcItem = $(`
        <div class="pc-item" draggable="true" data-entity-uid="${pc.entity_uid}" title="${pc.race} ${classInfo}">
          <img class="pc-item-image" src="/assets/${pc.token_image}" alt="${pc.name}" onerror="this.onerror=null;this.src='/static/assets/token_player.png'">
          <div class="pc-item-info">
            <div class="pc-item-name">${pc.label}</div>
            <div class="pc-item-type">${pc.race} ${classInfo}</div>
          </div>
        </div>
      `);

      // Add drag event handlers for PCs
      $pcItem.on('dragstart', (e) => {
        e.originalEvent.dataTransfer.setData('text/plain', pc.entity_uid);
        e.originalEvent.dataTransfer.effectAllowed = 'move';
        $(e.currentTarget).addClass('dragging');
        $('body').addClass('pc-drag-active');

        // Add visual feedback for empty tiles
        $(".tile").each(function () {
          if ($(this).find(".entity").length === 0) {
            $(this).addClass("empty-tile-highlight");
          }
        });
      });

      $pcItem.on('dragend', (e) => {
        $(e.currentTarget).removeClass('dragging');
        $('body').removeClass('pc-drag-active');
        $(".tile").removeClass("battlefield-drop-zone drag-over empty-tile-highlight");
      });

      $pcList.append($pcItem);
    });

    // Add search functionality for PCs
    $("#pc-search-input").off('input').on('input', function () {
      const searchTerm = $(this).val().toLowerCase();
      $(".pc-item").each(function () {
        const pcName = $(this).find('.pc-item-name').text().toLowerCase();
        const pcType = $(this).find('.pc-item-type').text().toLowerCase();
        if (pcName.includes(searchTerm) || pcType.includes(searchTerm)) {
          $(this).show();
        } else {
          $(this).hide();
        }
      });
    });
  }

  // Object Spawner functionality
  const TERRAIN_PRESETS = [
    {
      id: 'water',
      name: 'Water',
      description: 'Difficult terrain (2× movement). Swimmable.',
      swatch: '#4a90d9',
    },
    {
      id: 'difficult_terrain',
      name: 'Difficult Terrain',
      description: 'Generic rough ground (2× movement).',
      swatch: '#8b7355',
    },
    {
      id: 'briar',
      name: 'Briar / Undergrowth',
      description: 'Difficult terrain with half cover.',
      swatch: '#3d7a3d',
    },
  ];
  const TERRAIN_PRESET_IDS = new Set(TERRAIN_PRESETS.map((terrain) => terrain.id));
  let availableObjects = [];
  let objectSpawnerCategories = [];
  let activeObjectSpawnerCategory = 'all';

  function isTerrainPreset(objectType) {
    return TERRAIN_PRESET_IDS.has(objectType);
  }

  function placeInEditMode(objectType, x, y, mapName) {
    const payload = { object_type: objectType, x, y };
    if (mapName) {
      payload.map_name = mapName;
    }
    return postJsonPlacement('/edit/layer/place', payload);
  }

  function placeTerrainInEditMode(objectType, x, y) {
    return placeInEditMode(objectType, x, y);
  }

  window.placeTerrainInEditMode = placeTerrainInEditMode;
  window.placeInEditMode = placeInEditMode;

  function bindTerrainDragHandlers($terrainItem) {
    $terrainItem.on('dragstart', (e) => {
      draggedObject = $(e.currentTarget).data('object-type');
      $(e.currentTarget).addClass('dragging');
      e.originalEvent.dataTransfer.effectAllowed = 'copy';
      e.originalEvent.dataTransfer.setData('text/plain', draggedObject);
      centerDragImageOnCursor(e, $(e.currentTarget));
      $('body').addClass('object-drag-active');
      $('.tile').each(function () {
        $(this).addClass('empty-tile-highlight');
      });
    });

    $terrainItem.on('dragend', (e) => {
      $(e.currentTarget).removeClass('dragging');
      // Defer cleanup so the tile drop handler can read draggedObject/dataTransfer.
      setTimeout(() => {
        $('body').removeClass('object-drag-active');
        $('.tile').removeClass('battlefield-drop-zone drag-over empty-tile-highlight');
        draggedObject = null;
      }, 0);
    });
  }

  function renderTerrainPresets() {
    const $terrainList = $('#terrain-list');
    $terrainList.empty();

    TERRAIN_PRESETS.forEach((terrain) => {
      const $terrainItem = $(`
        <div class="terrain-item object-item" draggable="true" data-object-type="${terrain.id}" data-terrain-preset="true" title="${terrain.description}">
          <div class="terrain-item-swatch" style="background-color: ${terrain.swatch};"></div>
          <div class="object-item-info">
            <div class="object-item-name">${terrain.name}</div>
            <div class="object-item-type">${isEditMode() ? 'Drag to paint into map YAML' : 'Drag onto map tiles'}</div>
          </div>
        </div>
      `);
      bindTerrainDragHandlers($terrainItem);
      $terrainList.append($terrainItem);
    });
  }

  $("#toggle-object-spawner").click(() => {
    const $objectSpawner = $("#object-spawner");
    if ($objectSpawner.is(":visible")) {
      $objectSpawner.hide();
    } else {
      renderTerrainPresets();
      loadAvailableObjects();
      $objectSpawner.show();
    }
  });

  $("#close-object-spawner").click(() => {
    $("#object-spawner").hide();
  });

  function loadAvailableObjects() {
    $.get("/available_objects", (data) => {
      availableObjects = data.objects || [];
      objectSpawnerCategories = data.categories || [];
      renderObjectSpawnerFilters();
      displayObjects(availableObjects);
    }).fail((jqXHR) => {
      console.error("Failed to load objects:", jqXHR.responseJSON ? jqXHR.responseJSON.error : jqXHR.statusText);
      $("#object-list").html('<div class="alert alert-danger">Failed to load objects</div>');
    });
  }

  function renderObjectSpawnerFilters() {
    const $filters = $("#object-spawner-filters");
    $filters.empty();
    if (!objectSpawnerCategories.length) {
      return;
    }

    objectSpawnerCategories.forEach((category) => {
      const categoryId = category.id;
      const $button = $(`
        <button type="button"
                class="object-spawner-filter${categoryId === activeObjectSpawnerCategory ? ' active' : ''}"
                data-category="${categoryId}">
          ${category.label}
        </button>
      `);
      $filters.append($button);
    });

    $filters.off('click').on('click', '.object-spawner-filter', function () {
      activeObjectSpawnerCategory = $(this).data('category') || 'all';
      $('.object-spawner-filter').removeClass('active');
      $(this).addClass('active');
      applyObjectSpawnerFilters();
    });
  }

  function applyObjectSpawnerFilters() {
    const searchTerm = ($("#object-search-input").val() || '').toLowerCase();
    $(".object-item[data-spawner-object='true']").each(function () {
      const $item = $(this);
      const objectName = $item.find('.object-item-name').text().toLowerCase();
      const objectMeta = $item.find('.object-item-type').text().toLowerCase();
      const objectId = String($item.data('object-type') || '').toLowerCase();
      const category = String($item.data('object-category') || 'props');
      const matchesSearch = !searchTerm
        || objectName.includes(searchTerm)
        || objectMeta.includes(searchTerm)
        || objectId.includes(searchTerm);
      const matchesCategory = activeObjectSpawnerCategory === 'all' || category === activeObjectSpawnerCategory;
      $item.toggle(matchesSearch && matchesCategory);
    });
  }

  // Display objects in the spawner window
  function displayObjects(objects) {
    const $objectList = $("#object-list");
    $objectList.empty();

    if (!objects || objects.length === 0) {
      $objectList.append('<div class="alert alert-info">No objects available</div>');
      return;
    }

    objects.forEach((object) => {
      const passableText = object.passable ? "Passable" : "Blocks Movement";
      const categoryLabel = (objectSpawnerCategories.find((cat) => cat.id === object.category) || {}).label
        || (object.category || 'Props').replace(/_/g, ' ').replace(/\b\w/g, (ch) => ch.toUpperCase());

      const $objectItem = $(`
        <div class="object-item"
             draggable="true"
             data-spawner-object="true"
             data-object-type="${object.id}"
             data-object-category="${object.category || 'props'}"
             title="${object.id}${object.description ? ' — ' + object.description : ''}">
          <img class="object-item-image" src="/assets/editor/${object.image}" alt="${object.name}">
          <div class="object-item-info">
            <div class="object-item-name">${object.name}</div>
            <div class="object-item-type">${object.id} • ${categoryLabel} • AC ${object.ac} • HP ${object.hp} • ${passableText}</div>
          </div>
        </div>
      `);

      // Add drag event handlers for objects
      $objectItem.on('dragstart', (e) => {
        draggedObject = $(e.currentTarget).data('object-type');
        $(e.currentTarget).addClass('dragging');
        e.originalEvent.dataTransfer.effectAllowed = 'copy';
        e.originalEvent.dataTransfer.setData('text/plain', draggedObject);
        centerDragImageOnCursor(e, $(e.currentTarget));

        // Add visual feedback for tiles
        $('body').addClass('object-drag-active');
        $(".tile").each(function () {
          $(this).addClass("empty-tile-highlight");
        });

        console.log("Started dragging object:", draggedObject);
      });

      $objectItem.on('dragend', (e) => {
        $(e.currentTarget).removeClass('dragging');
        setTimeout(() => {
          $('body').removeClass('object-drag-active');
          $(".tile").removeClass("battlefield-drop-zone drag-over empty-tile-highlight");
          draggedObject = null;
        }, 0);
      });

      $objectList.append($objectItem);
    });

    $("#object-search-input").off('input').on('input', applyObjectSpawnerFilters);
    applyObjectSpawnerFilters();
  }

  // (Deprecated confirm-based handler removed; see unified modal-based handler below)

  // Handle action-bar delete button clicks (also used in turn order)
  $(document).on('click', '.delete-entity-btn', function (e) {
    e.preventDefault();
    e.stopPropagation();

    const entityUid = $(this).data('entity-uid');
    let entityName = 'this entity';

    // Try to find a nearby name label if in turn order
    const $row = $(this).closest('.turn-order-item');
    if ($row.length) {
      entityName = $row.find('.entity-label').text() || entityName;
    } else {
      // If triggered from tile action bar, try to infer from nameplate in tile
      const $tile = $(this).closest('.tile');
      const $nameplate = $tile.find('.nameplate');
      if ($nameplate.length) {
        entityName = $nameplate.text();
      }
    }

    $('#entityToDeleteName').text(entityName);
    $('#deleteEntityModal').modal('show');
    $('#confirmDeleteEntity').data('entity-uid', entityUid);
  });

  // Handle confirmation modal delete button
  $('#confirmDeleteEntity').click(function () {
    const entityUid = $(this).data('entity-uid');

    if (entityUid) {
      if (
        isEditMode() &&
        window.EditMode &&
        typeof window.EditMode.findFixtureForEntityUid === 'function'
      ) {
        const fixture = window.EditMode.findFixtureForEntityUid(entityUid);
        if (fixture && typeof window.EditMode.removeFixture === 'function') {
          $('#deleteEntityModal').modal('hide');
          window.EditMode.removeFixture(fixture, { skipConfirm: true });
          return;
        }
      }

      ajaxPost("/delete_entity", {
        entity_uid: entityUid
      }, (data) => {
        if (data.status === 'ok') {
          console.log("Entity deleted successfully:", data.entity_uid);
          $('#deleteEntityModal').modal('hide');
          // The map will be updated via socket message
        } else {
          alert("Failed to delete entity: " + (data.error || "Unknown error"));
        }
      }, true);
    }
  });

  // DM Sound Manager Event Handlers
  $("#modal-1 .modal-content").on("click", ".track-item", function () {
    const trackId = $(this).data('track-id');
    DMSoundManager.switchToTrack(trackId);
  });

  $("#modal-1 .modal-content").on("click", ".play-btn", function () {
    DMSoundManager.togglePlayPause();
  });

  $("#modal-1 .modal-content").on("click", ".pause-btn", function () {
    DMSoundManager.togglePlayPause();
  });

  $("#modal-1 .modal-content").on("click", ".stop-btn", function () {
    DMSoundManager.stopTrack();
  });



  $("#reload-map").click(() => {
    Utils.ajaxPost("/reload_map", {}, (data) => {
      console.log("Map reloaded successfully:", data);
      enqueueTileRefresh();
    });
  });

  function handleAction(entity_uid, action, opts, coordsx, coordsy, data) {
    if (data && data.status === 'ok') {
      $(".popover-menu").hide();
      closeCenterActionBar();
      $("#modal-1").modal("hide");
      // Record the action submission timestamp so we can suppress rapid
      // /update polling for a short window after the player commits an action.
      actionPostTimestamp = Date.now();
      refreshTurn();
      return;
    }
    // Basic validation and graceful fallback
    if (!data || typeof data !== 'object') {
      console.warn('handleAction: invalid response payload', data);
      return;
    }
    if (data.error) {
      console.error('handleAction: server error', data.error);
      // Optionally surface to user
      try { alert(data.error); } catch (e) { }
      return;
    }
    if (data.type === 'requires_dialog' && data.dialog === 'ready_action') {
      openReadyActionDialog(entity_uid, data);
      return;
    }
    const param0 = (Array.isArray(data.param) && data.param.length > 0) ? data.param[0] : null;
    const stepType = resolveActionStepType(data);
    if (!stepType) {
      console.warn('handleAction: missing param/type in response', data);
      return;
    }

    switch (stepType) {
      case "movement":
        closeCenterActionBar();
        moveModeCallback = (path) => {
          // If a jump segment was marked, compute manual_jump indices
          let manual_jump = null;
          if (Array.isArray(path) && jumpStartIndex !== null && jumpStartIndex >= 0) {
            // end index defaults to the last element in path when jump mode was active
            const _endIndex = (lastPathForJump && lastPathForJump.length > 0)
              ? (path.length - 1) // last segment end
              : (path.length - 1);
            if (_endIndex >= jumpStartIndex) {
              manual_jump = [jumpStartIndex, _endIndex];
            }
          }
          ajaxPost(
            "/action",
            {
              id: entity_uid,
              action,
              opts,
              path,
              manual_jump,
              stack_descent: lastStackDescent || null,
            },
            (data) => {
              console.log("Action request successful:", data);
              noteMoveActionCompleted(entity_uid, data);
              refreshTurn();
            },
            true,
          );
          resetMouseMoveMode();
        };
        $(".popover-menu").hide();
        moveMode = true;
        source = { x: coordsx, y: coordsy };
        accumulatedPath = [];
        pivotPoints = [];
        currentPosition = null;
        lastHoverCoords = null;
        movePath = [];
        clearMovePathCache();
        if (globalCtx && globalCanvas) {
          globalCtx.clearRect(0, 0, globalCanvas.width, globalCanvas.height);
        }
        break;
      case "text_input": {
        closeCenterActionBar();
        const field = param0.field || 'value';
        const label = param0.label || data.label || 'Enter a value';
        const placeholder = param0.placeholder || data.placeholder || '';
        const itemName = window.prompt(`${label}${placeholder ? `\n(${placeholder})` : ''}`);
        if (!itemName || !String(itemName).trim()) {
          return;
        }
        const nextOpts = Object.assign({}, opts || {});
        nextOpts[field] = String(itemName).trim();
        ajaxPost(
          "/action",
          { id: entity_uid, action, opts: nextOpts },
          (response) => {
            handleAction(entity_uid, action, nextOpts, coordsx, coordsy, response);
          },
          true,
        );
        break;
      }
      case "select_spell":
        Utils.ajaxGet("/spells", { id: entity_uid, action, opts }, (data) => {
          showPickerInCenterActionBar(entity_uid, 'Cast Spell', data, coordsx, coordsy);
        });
        break;
      case "select_item": {
        Utils.ajaxGet(
          "/usable_items",
          { id: entity_uid, action, opts },
          (data) => {
            showPickerInCenterActionBar(entity_uid, 'Use Item', data, coordsx, coordsy);
          },
        );
        break;
      }
      case "select_weapon": {
        // Weapon selection for spells like Booming Blade / Green-Flame Blade
        const validWeapons = data.valid_weapons || [];
        if (!validWeapons || validWeapons.length === 0) {
          console.warn('handleAction: select_weapon without valid weapons', data);
          return;
        }
        closeCenterActionBar();
        // create a modal with a list of weapons to select from
        const $modal = $(
          '<div class="modal fade" id="select-weapon-modal" tabindex="-1" role="dialog" aria-labelledby="select-weapon-modal-label" aria-hidden="true">',
        );
        $modal.html(`
          <div class="modal-dialog" role="document">
            <div class="modal-content">
              <div class="modal-header">
                <h5 class="modal-title" id="select-weapon-modal-label">Select a weapon</h5>
                <button type="button" class="close" data-dismiss="modal" aria-label="Close">
                  <span aria-hidden="true">&times;</span>
                </button>
              </div>
              <div class="modal-body">
                <ul class="list-group">
                  ${validWeapons.map((weapon) => `<li class="list-group-item choice-item" data-weapon="${weapon}">${weapon}</li>`).join('')}
                </ul>
              </div>
            </div>
          </div>
        `);
        $('body').append($modal);
        $modal.modal('show');

        // Add click handler directly to the modal
        $modal.on('click', '.choice-item', function () {
          const weapon = $(this).data('weapon');
          $modal.modal('hide');
          const nextOpts = Object.assign({}, opts || {});
          nextOpts.weapon = weapon;
          ajaxPost(
            '/action',
            { id: entity_uid, action, opts: nextOpts },
            (response) => {
              $('#modal-1').modal('hide');
              handleAction(entity_uid, action, nextOpts, coordsx, coordsy, response);
            },
            true,
          );
        });
        break;
      }
      case "select_choice": {
        closeCenterActionBar();
        const choices = (param0 && Array.isArray(param0.choices)) ? param0.choices : [];
        if (!choices || choices.length === 0) {
          console.warn('handleAction: select_choice without choices', data);
          return;
        }
        // create a modal with a list of choices
        const $modal = $(
          '<div class="modal fade" id="select-choice-modal" tabindex="-1" role="dialog" aria-labelledby="select-choice-modal-label" aria-hidden="true">',
        );
        $modal.html(`
          <div class="modal-dialog" role="document">
            <div class="modal-content">
              <div class="modal-header">
                <h5 class="modal-title" id="select-choice-modal-label">Select an option</h5>
                <button type="button" class="close" data-dismiss="modal" aria-label="Close">
                  <span aria-hidden="true">&times;</span>
                </button>
              </div>
              <div class="modal-body">
                <ul class="list-group">
                  ${choices.map((choice) => `<li class="list-group-item choice-item" data-choice="${choice[1]}">${choice[0]}</li>`).join("")}
                </ul>
              </div>
            </div>
          </div>
        `);
        $("body").append($modal);
        $modal.modal("show");

        // Add click handler directly to the modal
        $modal.on("click", ".choice-item", function () {
          const choice = $(this).data("choice");
          $modal.modal("hide");
          ajaxPost(
            "/action",
            { id: entity_uid, action, opts, choice },
            (data) => {
              $("#modal-1").modal("hide");
              opts["choice"] = choice;
              handleAction(entity_uid, action, opts, coordsx, coordsy, data);
            },
            true,
          );
        });
        break;
      }
      case "select_cone":
        resetMouseMoveMode();
        $(".popover-menu").hide();
        closeCenterActionBar();
        $("#modal-1").modal("hide");
        source = { x: coordsx, y: coordsy, entity_uid };
        targetModeMaxRange =
          data.range_max !== undefined ? data.range_max : data.range;
        coneMode = true
        targetMode = true;
        globalActionInfo = action;
        globalOpts = opts;
        globalSourceEntity = entity_uid;
        targetModeCallback = (target) => {
          ajaxPost(
            "/action",
            { id: entity_uid, mode: 'cone', action, opts, target },
            (data) => {
              console.log("Action request successful:", data);
              refreshTurn();
            },
            true,
          );
        };
        break;
      case "select_cube":
        resetMouseMoveMode();
        $(".popover-menu").hide();
        closeCenterActionBar();
        $("#modal-1").modal("hide");
        source = { x: coordsx, y: coordsy, entity_uid };
        targetModeMaxRange =
          data.range_max !== undefined ? data.range_max : data.range;
        // No special mode needed for drawing; server returns target_squares
        targetMode = true;
        globalActionInfo = action;
        globalOpts = opts;
        globalSourceEntity = entity_uid;
        targetModeCallback = (target) => {
          ajaxPost(
            "/action",
            { id: entity_uid, mode: 'cube', action, opts, target },
            (data) => {
              console.log("Action request successful:", data);
              refreshTurn();
            },
            true,
          );
        };
        break;
      case "select_radius":
        resetMouseMoveMode();
        $(".popover-menu").hide();
        closeCenterActionBar();
        $("#modal-1").modal("hide");
        source = { x: coordsx, y: coordsy, entity_uid };
        targetModeMaxRange =
          data.range_max !== undefined ? data.range_max : data.range;
        targetMode = true;
        enterAoePointTargeting();
        globalActionInfo = action;
        globalOpts = opts;
        globalSourceEntity = entity_uid;
        targetModeCallback = (target) => {
          ajaxPost(
            "/action",
            { id: entity_uid, mode: 'radius', action, opts, target },
            (data) => {
              console.log("Action request successful:", data);
              refreshTurn();
            },
            true,
          );
        };
        break;
      case "select_square":
        resetMouseMoveMode();
        $(".popover-menu").hide();
        closeCenterActionBar();
        $("#modal-1").modal("hide");
        source = { x: coordsx, y: coordsy, entity_uid };
        targetModeMaxRange =
          data.range_max !== undefined ? data.range_max : data.range;
        targetMode = true;
        enterAoePointTargeting();
        globalActionInfo = action;
        globalOpts = opts;
        globalSourceEntity = entity_uid;
        targetModeCallback = (target) => {
          ajaxPost(
            "/action",
            { id: entity_uid, mode: 'square', action, opts, target },
            (data) => {
              console.log("Action request successful:", data);
              refreshTurn();
            },
            true,
          );
        };
        break;
      case "select_line":
        resetMouseMoveMode();
        $(".popover-menu").hide();
        closeCenterActionBar();
        $("#modal-1").modal("hide");
        source = { x: coordsx, y: coordsy, entity_uid };
        targetModeMaxRange =
          data.range_max !== undefined ? data.range_max : data.range;
        targetMode = true;
        enterAoePointTargeting();
        globalActionInfo = action;
        globalOpts = opts;
        globalSourceEntity = entity_uid;
        targetModeCallback = (target) => {
          ajaxPost(
            "/action",
            { id: entity_uid, mode: 'line', action, opts, target },
            (data) => {
              console.log("Action request successful:", data);
              refreshTurn();
            },
            true,
          );
        };
        break;
      case "select_target":
        resetMouseMoveMode();
        $(".popover-menu").hide();
        closeCenterActionBar();
        $("#modal-1").modal("hide");
        source = { x: coordsx, y: coordsy, entity_uid };
        targetModeMaxRange =
          data.range_max !== undefined ? data.range_max : data.range;
        if (data.target_hints) {
          data.target_hints.forEach((value) => {
            $(`.tile[data-coords-id="${value}"] .add-to-target`).show();
          });
          multiTargetMode = true;
          max_targets = data.total_targets;
          multiTargetModeUnique = data.unique_targets === true;
          targetMode = false;
          coneMode = false;
          aoePointTargetMode = false;
          valid_target_cache = {};
          if (typeof showMultiTargetBar === 'function') {
            showMultiTargetBar();
          }
        } else {
          targetMode = true;
          coneMode = false;
          aoePointTargetMode = false;
          setStackTargetPointerEvents(true);
          globalActionInfo = action;
          globalOpts = opts;
          globalSourceEntity = entity_uid;
        }
        targetModeCallback = (target) => {
          // Handle both coordinate-based and target_id-based targeting
          const mergedOpts = { ...(globalOpts || {}), ...(opts || {}) };
          const actionData = { id: entity_uid, action, opts: mergedOpts };

          if (target.target_id) {
            // Use target_id for specific entity/object targeting
            actionData.target = target.target_id;
          } else {
            // Use coordinates for position-based targeting
            actionData.target = target;
          }

          ajaxPost(
            "/action",
            actionData,
            (data) => {
              continueActionFromResponse(
                entity_uid,
                action,
                mergedOpts,
                target.x,
                target.y,
                data,
              );
            },
            true,
          );
        };
        break;
      case "select_empty_space":
        resetMouseMoveMode();
        $(".popover-menu").hide();
        closeCenterActionBar();
        $("#modal-1").modal("hide");

        source = { x: coordsx, y: coordsy, mode: 'point_target', entity_uid };
        targetModeMaxRange =
          data.range_max !== undefined ? data.range_max : data.range;
        targetMode = true;
        globalActionInfo = action;
        globalOpts = opts;
        globalSourceEntity = entity_uid;
        targetModeCallback = (target) => {
          ajaxPost(
            "/action",
            { id: entity_uid, action, opts, target },
            (data) => {
              continueActionFromResponse(
                entity_uid,
                action,
                opts,
                target.x,
                target.y,
                data,
              );
            },
            true,
          );
        };
        break;
      case "select_pickpocket_item": {
        closeCenterActionBar();
        const targetUid = data.target_uid || opts.target || param0.target_uid;
        const mergedOpts = { ...(opts || {}), target: targetUid };
        if (data.pickpocket_attempt) {
          mergedOpts.pickpocket_attempt = data.pickpocket_attempt;
        }
        const requestData = {
          id: entity_uid,
          target: targetUid,
          action,
          opts: JSON.stringify(mergedOpts),
        };
        if (mergedOpts.pickpocket_attempt) {
          requestData.pickpocket_attempt = JSON.stringify(mergedOpts.pickpocket_attempt);
        }
        $.ajax({
          url: "/pickpocket_items",
          type: "GET",
          data: requestData,
          success: (html) => {
            $("#modal-1 .modal-content").html(html);
            $("#modal-1").modal("show");
            $(".pickpocket-items-form")
              .off("submit")
              .on("submit", function (e) {
                e.preventDefault();
                const selected = $(this).find('input[name="selected_item"]:checked').val();
                if (!selected) {
                  if (typeof Utils !== "undefined" && Utils.showToast) {
                    Utils.showToast("Select an item to steal.");
                  }
                  return;
                }
                const nextOpts = { ...mergedOpts, item_name: selected };
                ajaxPost(
                  "/action",
                  { id: entity_uid, action, opts: nextOpts, target: targetUid },
                  (resp) => {
                    $("#modal-1").modal("hide");
                    continueActionFromResponse(
                      entity_uid,
                      action,
                      nextOpts,
                      coordsx,
                      coordsy,
                      resp,
                    );
                  },
                  true,
                );
              });
          },
          error: (jqXHR) => {
            let message = "Could not load stealable items.";
            try {
              const payload = jqXHR.responseJSON;
              if (payload && payload.error) {
                message = payload.error;
              }
            } catch (e) { /* noop */ }
            if (typeof Utils !== "undefined" && Utils.showToast) {
              Utils.showToast(message);
            } else {
              console.warn(message);
            }
          },
        });
        break;
      }
      case "select_items":
        closeCenterActionBar();
        function initiateTransfer() {
          Utils.ajaxGet("/items", { id: entity_uid, action, opts }, (data) => {
            $("#modal-1 .modal-content").html(data);
            $("#modal-1").modal("show");
            $(".loot-items-form").on("submit", function (e) {
              e.preventDefault();
              fromItems = [];
              fromItemsQty = [];
              $(this)
                .find('input[name="selected_items_target"]')
                .each(function () {
                  fromItems.push($(this).val());
                });

              $(this)
                .find("input.transfer-qty")
                .each(function () {
                  fromItemsQty.push($(this).val());
                });
              toItems = [];
              toItemsQty = [];
              $(this)
                .find('input[name="selected_items_source"]')
                .each(function () {
                  toItems.push($(this).val());
                });

              $(this)
                .find("input.transfer-qty-source")
                .each(function () {
                  toItemsQty.push($(this).val());
                });

              const itemsToTransfer = {
                from: {
                  items: fromItems,
                  qty: fromItemsQty,
                },
                to: {
                  items: toItems,
                  qty: toItemsQty,
                },
              };
              opts["items"] = itemsToTransfer;

              ajaxPost(
                "/action",
                { id: entity_uid, action, opts },
                (data) => {
                  initiateTransfer();
                },
                true,
              );
            });
          });
        }
        initiateTransfer();
        break;
      default:
        console.log("Unknown action type:", stepType);
    }
    console.log("Action request successful:", data);
  }
  window.__n20_handleAction = handleAction;

  // --- Ready Action chat dialog ---
  function openReadyActionDialog(entity_uid, info) {
    $(".popover-menu").hide();
    closeCenterActionBar();

    const promptText = (info && info.prompt)
      ? info.prompt
      : 'Describe the trigger and the action you are readying. (e.g., "When the goblin steps adjacent to me, I attack with my longsword.")';

    // Remove any stale modal
    $('#readyActionModal').remove();

    const modalHtml = `
      <div id="readyActionModal" class="modal fade in" tabindex="-1" role="dialog"
           style="display:block; background:rgba(0,0,0,0.5); z-index:20000;">
        <div class="modal-dialog" role="document" style="margin-top:80px;">
          <div class="modal-content">
            <div class="modal-header" style="background:#2c2c2c; color:#fff;">
              <h4 class="modal-title">
                <i class="glyphicon glyphicon-time"></i>
                Ready an Action &mdash; Dungeon Master
              </h4>
            </div>
            <div class="modal-body">
              <p id="readyActionPrompt"></p>
              <div id="readyActionDmReply" class="alert alert-warning"
                   style="display:none; white-space:pre-wrap;"></div>
              <textarea id="readyActionInput" class="form-control" rows="4"
                placeholder="When ... I will ..."></textarea>
              <div id="readyActionStatus" style="margin-top:8px; color:#888; font-size:90%;"></div>
            </div>
            <div class="modal-footer">
              <button type="button" class="btn btn-default" id="readyActionCancel">Cancel</button>
              <button type="button" class="btn btn-primary" id="readyActionSubmit">Ready Action</button>
            </div>
          </div>
        </div>
      </div>`;

    const $modal = $(modalHtml).appendTo('body');
    $modal.find('#readyActionPrompt').text(promptText);
    const $input = $modal.find('#readyActionInput');
    const $submit = $modal.find('#readyActionSubmit');
    const $cancel = $modal.find('#readyActionCancel');
    const $status = $modal.find('#readyActionStatus');
    const $dmReply = $modal.find('#readyActionDmReply');
    setTimeout(() => $input.focus(), 50);

    function close() {
      $modal.remove();
    }

    function setBusy(busy) {
      $submit.prop('disabled', busy);
      $cancel.prop('disabled', busy);
      $input.prop('disabled', busy);
      $status.text(busy ? 'Asking the DM...' : '');
    }

    function showDmReply(reason, kind) {
      $dmReply
        .removeClass('alert-warning alert-danger alert-success alert-info')
        .addClass(kind === 'ok' ? 'alert-success' : 'alert-warning')
        .text('DM: ' + (reason || 'No response.'))
        .show();
    }

    function submit() {
      const description = String($input.val() || '').trim();
      if (!description) {
        $status.text('Please describe the trigger and action.');
        return;
      }
      $dmReply.hide();
      setBusy(true);
      submitReadyAction(entity_uid, description, function (data) {
        if (data && data.status === 'rejected') {
          // Keep modal open so the user can revise.
          showDmReply(data.reason || 'The DM rejected that ready action.', 'reject');
          setBusy(false);
          $input.focus();
          return;
        }
        if (data && data.status === 'ok' && data.reason) {
          // Briefly flash the DM reply before closing so the player sees it.
          showDmReply(data.reason, 'ok');
          setBusy(false);
          setTimeout(close, 1200);
          return;
        }
        close();
      }, function (err) {
        $status.text(err || 'Failed to submit. Please try again.');
        setBusy(false);
      });
    }

    $submit.on('click', submit);
    $cancel.on('click', close);
    $input.on('keydown', function (e) {
      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        submit();
      } else if (e.key === 'Escape') {
        e.preventDefault();
        close();
      }
    });
  }

  function submitReadyAction(entity_uid, description, onDone, onError) {
    ajaxPost(
      '/ready_action',
      { id: entity_uid, description },
      (data) => {
        try {
          if (typeof onDone === 'function') onDone(data);
        } catch (e) { /* noop */ }
        if (data && data.status === 'ok') {
          refreshTurn();
        }
      },
      true,
    );
  }

  $('#centerActionBarClose').on('click', function () {
    closeCenterActionBar();
  });

  $('#centerActionBarExpand').on('click', function (e) {
    e.preventDefault();
    e.stopPropagation();
    setActionBarExpanded(!actionBarState.expanded);
  });

  $('#actionBarHotkeyToggle').on('click', function () {
    if (actionBarState.customizeMode) {
      setActionBarCustomizeMode(false);
    }
    actionBarState.bindingMode = !actionBarState.bindingMode;
    clearPendingHotkeySelection();
    $(this).toggleClass('active', actionBarState.bindingMode).text(actionBarState.bindingMode ? 'Cancel Binding' : 'Bind Hotkeys');
    if (actionBarState.bindingMode) {
      setActionBarInstructions('Binding mode enabled. Click an action, then press 1-9 to assign it.');
    } else {
      setActionBarInstructions('Press 1-9 to trigger assigned actions. Use Bind Hotkeys to customize them.');
    }
  });

  $('#actionBarCustomizeToggle').on('click', function () {
    if (actionBarState.bindingMode) {
      actionBarState.bindingMode = false;
      $('#actionBarHotkeyToggle').removeClass('active').text('Bind Hotkeys');
      clearPendingHotkeySelection();
    }
    setActionBarCustomizeMode(!actionBarState.customizeMode);
  });

  $(document).on('click', '#centerActionBar .action-bar-tab-btn', function (e) {
    e.preventDefault();
    const tabId = $(this).data('tab');
    const $tabs = $(this).closest('.action-bar-tabs');
    selectActionBarTab($tabs, tabId);
    const entityUid = $tabs.data('entityUid');
    const prefs = loadActionBarPrefs(entityUid);
    prefs.lastTab = tabId;
    saveActionBarPrefs(entityUid, prefs);
  });

  $(document).on('click', '#centerActionBar .action-bar-pin-btn', function (e) {
    e.preventDefault();
    e.stopPropagation();
    const $cell = $(this).closest('[data-action-descriptor]');
    const descriptor = $cell.attr('data-action-descriptor');
    const $tabs = $(this).closest('.action-bar-tabs');
    const entityUid = $tabs.data('entityUid');
    if (!descriptor || !entityUid) {
      return;
    }
    toggleActionBarPin(entityUid, descriptor);
    applyActionBarPins($tabs);
    syncActionBarPinButtons($tabs);
  });

  $(document).on('click', '#centerActionBar .action-bar-upcast-toggle', function (e) {
    e.preventDefault();
    e.stopPropagation();
    if (actionBarState.bindingMode) {
      return;
    }
    const $card = $(this).closest('.action-bar-spell-card');
    const $menu = $card.find('.action-bar-upcast-menu');
    const isOpen = !$menu.prop('hidden');
    closeUpcastMenus();
    if (!isOpen) {
      $menu.removeAttr('hidden');
      $card.addClass('is-upcast-open');
      $(this).addClass('is-open').attr('aria-expanded', 'true');
    }
  });

  $(document).on('click', '#centerActionBar .action-bar-upcast-level', function () {
    closeUpcastMenus();
  });

  $(document).on('click', function (e) {
    if ($(e.target).closest('.action-bar-upcast-toggle, .action-bar-upcast-menu').length) {
      return;
    }
    closeUpcastMenus();
  });

  $(document).on('click', '#centerActionBar .center-action-entry', function (e) {
    if ($(e.target).closest('.action-button, .action-end-turn, .talk-action, .action-info, .delete-entity-btn, .edit-fixture-remove-btn, .edit-fixture-properties-btn').length) {
      return;
    }
    activateCenterActionEntry($(this));
  });

  $(".actions-container").on("click", ".knockout-toggle", function (e) {
    e.preventDefault();
    e.stopPropagation();
    setKnockUnconsciousMode(!isKnockUnconsciousMode());
  });

  // --- Action Button Handler ---
  $(".actions-container").on("click", ".action-button", function (e) {
    if ($(this).hasClass('edit-fixture-remove-btn') || $(this).hasClass('edit-fixture-properties-btn')) {
      return;
    }
    if ($(this).hasClass('action-bar-upcast-level')) {
      closeUpcastMenus();
    }
    e.stopPropagation();
    const $button = $(this);
    const $overlayEntry = $button.closest('.center-action-entry');
    if ($overlayEntry.length) {
      activateCenterActionEntry($overlayEntry);
      if (actionBarState.bindingMode) {
        beginHotkeyAssignment($button);
        return;
      }
    }
    const action = $(this).data("action-type");
    const opts = withKnockUnconsciousOpts($(this).data("action-opts"), $(this));
    let entity_uid =
      $(this).closest('.center-action-entry').data('entityUid') ||
      $(this).closest(".tile").data("coords-id") ||
      $(this).data("id") ||
      $(this).closest(".tile").find(".object-container").data("id");
    if (!entity_uid) {
      console.warn('Action button clicked without entity id');
      return;
    }
    let coordsx =
      $(this).closest('.center-action-entry').data('coordsX') !== undefined
        ? $(this).closest('.center-action-entry').data('coordsX')
        : $(this).closest(".tile").data("coords-x") !== undefined
        ? $(this).closest(".tile").data("coords-x")
        : $(this).data("coords-x");
    let coordsy =
      $(this).closest('.center-action-entry').data('coordsY') !== undefined
        ? $(this).closest('.center-action-entry').data('coordsY')
        : $(this).closest(".tile").data("coords-y") !== undefined
        ? $(this).closest(".tile").data("coords-y")
        : $(this).data("coords-y");

    if (moveMode) {
      moveMode = false;
      accumulatedPath = [];
      clearMovePathCache();
      pivotPoints = [];
      globalCtx.clearRect(0, 0, globalCanvas.width, globalCanvas.height);
      $(".tile").css("border", "none");
    }
    const dataPayload = { id: entity_uid, action, opts };
    ajaxPost(
      "/action",
      dataPayload,
      (data) => handleAction(entity_uid, action, opts, coordsx, coordsy, data),
      true,
    );
  });

  // Add keyboard event handler for movement
  $(document).on("keydown", function (e) {
    // Space / Enter on the focused (POV) character opens its action bar
    // when nothing else is open. This lets the player open the action bar
    // without having to click their token first.
    if (
      (e.key === ' ' || e.key === 'Spacebar' || e.key === 'Enter') &&
      !actionBarState.visible &&
      !$('.popover-menu:visible').length &&
      !targetMode && !multiTargetMode && !coneMode && !moveMode &&
      !keyboardMovementMode &&
      !isTypingIntoField(e.target)
    ) {
      const povUid = $('body').attr('data-pov-entity');
      if (povUid) {
        const $povTile = getEntityTile(povUid);
        if ($povTile.length) {
          e.preventDefault();
          // Reuse the normal tile-click flow so we get the same occupant
          // detection and centered action bar rendering.
          $povTile.trigger('click');
          return;
        }
      }
    }

    // Check for popover menu instead of actions-container
    const $popoverMenu = $(".popover-menu:visible");
    if ($popoverMenu.length || actionBarState.visible) {
      const $overlayEntry = $('#centerActionBar .center-action-entry.is-active');
      const $tile = $popoverMenu.closest(".tile");
      if ($tile.length || $overlayEntry.length) {
        const entity_uid = $overlayEntry.length ? $overlayEntry.data('entityUid') : $tile.data("coords-id");
        const coordsx = $overlayEntry.length ? $overlayEntry.data('coordsX') : $tile.data("coords-x");
        const coordsy = $overlayEntry.length ? $overlayEntry.data('coordsY') : $tile.data("coords-y");

        console.log("Action bar visible for entity:", entity_uid, "at coords:", coordsx, coordsy);

        if (!keyboardMovementMode && actionBarState.visible && !actionBarState.bindingMode && !isTypingIntoField(e.target)) {
          if (/^[1-9]$/.test(e.key) && triggerActionBarHotkey(e.key)) {
            e.preventDefault();
            return;
          }
        }

        if (actionBarState.bindingMode && actionBarState.visible && !isTypingIntoField(e.target)) {
          if (/^[1-9]$/.test(e.key) && actionBarState.pendingBinding) {
            e.preventDefault();
            assignPendingActionHotkey(e.key);
            return;
          }
          if ((e.key === 'Backspace' || e.key === 'Delete' || e.key === '0') && actionBarState.pendingBinding) {
            e.preventDefault();
            clearPendingActionHotkey();
            return;
          }
        }

        // Handle movement keys (skip while typing in chat/command inputs)
        if (
          !isTypingIntoField(e.target) &&
          ["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "w", "a", "s", "d", "W", "A", "S", "D"].includes(e.key)
        ) {
          e.preventDefault(); // Prevent page scrolling
          console.log("Movement key pressed:", e.key);
          handleKeyboardMovement(e.key, entity_uid, coordsx, coordsy);
        }
        // Handle Enter / Space to execute movement
        else if ((e.key === "Enter" || e.key === " " || e.key === "Spacebar") && keyboardMovementMode) {
          e.preventDefault();
          console.log("Executing keyboard movement");
          const action = "move";
          const opts = {};
          executeKeyboardMovement(entity_uid, action, opts);
        }
        // Handle Escape key to cancel movement
        else if (e.key === "Escape" && keyboardMovementMode) {
          e.preventDefault();
          console.log("Cancelling keyboard movement");
          resetKeyboardMovement();
        }
      }
    }
  });

  $("#turn-order").on("click", "#add-more", function () {
    battle_setup = !battle_setup;
    enqueueTileRefresh({ is_setup: !!battle_setup });
  });

  $(".game-turn-container").on("click", "#player-end-turn", function () {
    ajaxPost("/end_turn", {}, (data) => {
      // Optionally hide game turn container
    });
  });

  //on mouse over an action button if there is a target, highlight the target
  $(".actions-container").on("mouseover", ".action-button", function () {
    const opts = $(this).data("action-opts");
    // Optionally highlight targets if opts carries target info in the future
  });

  $(".actions-container").on("click", ".action-end-turn", function () {
    if ($(this).closest('.center-action-entry').length) {
      activateCenterActionEntry($(this).closest('.center-action-entry'));
      if (actionBarState.bindingMode) {
        beginHotkeyAssignment($(this));
        return;
      }
    }
    ajaxPost("/end_turn", {}, (data) => {
      //hide actions
      $(".popover-menu").hide();
      closeCenterActionBar();
    });
  });

  $(".floating-window").on("click", function () {
    let maxZIndex = 0;
    $(".floating-window").each(function () {
      maxZIndex = Math.max(maxZIndex, parseInt($(this).css("z-index")) || 0);
    });
    $(this).css("z-index", maxZIndex + 1);
  });

  $(document).on("mouseenter", ".hide-action", function () {
    const entity_uid = $(this).closest('.center-action-entry').data('entityUid') || $(this).closest(".tile").data("coords-id");
    if (!entity_uid) return;

    if (hidePreviewHoverTimer) {
      clearTimeout(hidePreviewHoverTimer);
      hidePreviewHoverTimer = null;
    }

    const paintHideSpots = (spots) => {
      (spots || []).forEach((value) => {
        $(
          `.tile[data-coords-x="${value[0]}"][data-coords-y="${value[1]}"]`,
        ).css("background-color", "rgba(0, 255, 0, 0.5)");
      });
    };

    const cached = hidePreviewCache.get(String(entity_uid));
    const now = Date.now();
    if (cached && (now - cached.ts) <= HIDE_PREVIEW_CACHE_TTL_MS) {
      paintHideSpots(cached.spots);
      return;
    }

    // Debounce hover-triggered hide lookups so quick cursor passes do not
    // trigger expensive server-side hide spot scans.
    hidePreviewHoverTimer = setTimeout(() => {
      Utils.ajaxGet("/hide", { id: entity_uid }, (data) => {
        const spots = (data && Array.isArray(data.hiding_spots)) ? data.hiding_spots : [];
        hidePreviewCache.set(String(entity_uid), { ts: Date.now(), spots });
        paintHideSpots(spots);
      });
    }, 120);
  });

  $(document).on("mouseleave", ".hide-action", () => {
    if (hidePreviewHoverTimer) {
      clearTimeout(hidePreviewHoverTimer);
      hidePreviewHoverTimer = null;
    }
    $(".tile").css("background-color", "");
  });

  Utils.draggable("#battle-turn-order");
  Utils.draggable("#console-container");

  if ($("body").data("battle-in-progress")) {
    $("#start-initiative").hide();
  }

  // Handle command form submission
  $("#command-form").on("submit", (e) => {
    e.preventDefault();
    const cmd = $("#command-input").val().trim();
    if (cmd === "") return;

    // send the command via WebSocket using the existing command function
    $.ajax({
      type: "POST",
      url: "/command",
      data: { command: cmd },
      success: (data) => {
        $("#command-output").append("> " + cmd + "\n");
        $("#command-input").val("");
        if (data.error) {
          console.log("Command request failed:", data);
          $("#command-output").append(data.error + "\n");
        } else {
          console.log("Command request successful:", data);
          $("#command-output").append(data + "\n");
        }
        // Scroll to bottom of output
        $("#command-output").scrollTop($("#command-output")[0].scrollHeight);
      },
    });
  });

  // Handle Enter key press in command input
  $("#command-input").on("keypress", function (e) {
    if (e.which === 13) { // Enter key
      $("#command-form").submit();
    }
  });

  // Append server responses tagged with type 'command_response' to the command output
  socket.on("command_response", (data) => {
    // Enqueue command response events for FIFO processing
    eventQueue.enqueue(data);
  });

  // Handle talk action
  function handleTalk(entityId) {
    // Check if we're in talk to entity mode (JRPG dialog)
    if (talkToEntityMode) {
      // Use the JRPG dialog modal instead
      handleDialogBubbleClick(entityId, 'Entity');
      return;
    }

    // Original talk modal behavior
    $('#talkModal').modal('show');

    // Get the tile data to access conversation languages
    const povEntity = Chat.getCurrentPovEntity();
    const $tile = $(`.tile[data-coords-id="${povEntity}"]`);
    const languages = $tile.data('conversation-languages');

    const languagesArray = languages.split(',');

    // Populate language dropdown
    const $languageSelect = $('#languageSelect');
    $languageSelect.empty();

    // Add available languages to dropdown
    if (languages && languages.length > 0) {
      languagesArray.forEach(language => {
        if (language.trim() !== 'common') {
          $languageSelect.append(`<option value="${language}">${language}</option>`);
        } else {
          $languageSelect.append(`<option value="Common" selected>Common</option>`);
        }
      });
    }

    const refreshNearbyEntities = () => {
      const selectedVolume = $('input[name="speechVolume"]:checked');
      const volume = selectedVolume.val() || 'normal';
      const distance = Number(selectedVolume.data('distance') || 30);
      $.ajax({
        url: '/nearby_entities',
        type: 'GET',
        data: {
          entity_id: entityId,
          range: distance,
          volume: volume,
        },
        success: (data) => {
          const $nearbyEntities = $('#nearbyEntities');
          $nearbyEntities.empty();

          if (data.entities && data.entities.length > 0) {
            data.entities.forEach(entity => {
              $nearbyEntities.append(`
                <label class="list-group-item">
                  <input type="checkbox" name="targets" value="${entity.id}">
                  ${entity.name} (@${entity.mention_handle}, ${entity.distance}ft away)
                </label>
              `);
            });
          } else {
            $nearbyEntities.append('<div class="list-group-item">No entities within earshot range</div>');
          }
        }
      });
    };

    refreshNearbyEntities();
    $('input[name="speechVolume"]').off('change.talkNearby').on('change.talkNearby', refreshNearbyEntities);

    $('#submitTalk').off('click').on('click', function () {
      const message = $('#talkMessage').val().trim();
      if (message) {
        const selectedTargets = [];
        $('input[name="targets"]:checked').each(function () {
          selectedTargets.push($(this).val());
        });

        const noSpecificTarget = $('#noSpecificTarget').is(':checked');
        const selectedLanguage = $('#languageSelect').val();
        const selectedVolume = $('input[name="speechVolume"]:checked');
        const requestedVolume = selectedVolume.val() || 'normal';
        const volume = Chat.getEffectiveConversationVolume(message, requestedVolume);
        const distance_ft = Chat.speechDistanceForVolume(volume);

        $.ajax({
          url: '/talk',
          type: 'POST',
          contentType: 'application/json',
          data: JSON.stringify({
            entity_id: entityId,
            message: message,
            targets: selectedTargets,
            no_specific_target: noSpecificTarget,
            language: selectedLanguage,
            distance_ft: distance_ft,
            volume: volume
          }),
          success: (data) => {
            if (data.success) {
              $('#talkModal').modal('hide');
              $('#talkMessage').val('');
              $('input[name="targets"]').prop('checked', false);
              $('#noSpecificTarget').prop('checked', false);
            }
          }
        });
      }
    });
  }

  // Update the popover menu click handler
  $(document).on('click', '.merchant-shop-action', function (event) {
    event.stopPropagation();
    event.preventDefault();
    const $entry = $(this).closest('.center-action-entry');
    const $tile = $(this).closest('.tile');
    const merchantUid = $entry.data('entityUid') || $tile.data('coords-id');
    const buyerUid = Chat.getCurrentPovEntity();

    if ($entry.length) {
      activateCenterActionEntry($entry);
      if (actionBarState.bindingMode) {
        beginHotkeyAssignment($(this));
        return;
      }
    }

    if (!merchantUid || !buyerUid) {
      alert('Select a player character before shopping.');
      return;
    }

    openMerchantTradeModal(merchantUid, buyerUid);
    closeCenterActionBar();
  });

  $(document).on('click', '.talk-action', function (event) {
    event.stopPropagation();
    event.preventDefault();
    const $entry = $(this).closest('.center-action-entry');
    const $tile = $(this).closest('.tile');
    const entityId = $entry.data('entityUid') || $tile.data('coords-id');

    if ($entry.length) {
      activateCenterActionEntry($entry);
      if (actionBarState.bindingMode) {
        beginHotkeyAssignment($(this));
        return;
      }
    }

    // Ensure we're not in talk to entity mode for regular talk actions
    talkToEntityMode = false;

    handleTalk(entityId);
    closeCenterActionBar();
  });

  $(document).on('click', '.jrpg-dialog-action', function (event) {
    event.stopPropagation();
    event.preventDefault();
    const $entry = $(this).closest('.center-action-entry');
    const $tile = $(this).closest('.tile');
    const entityId = $entry.data('entityUid') || $tile.data('coords-id');
    const entityName = $(this).data('entityName') || $entry.find('.popover-actions-title').text() || entityId;

    if ($entry.length) {
      activateCenterActionEntry($entry);
    }

    const povUid = $('body').attr('data-pov-entity') || (typeof Chat !== 'undefined' && Chat.getCurrentPovEntity && Chat.getCurrentPovEntity());
    if (entityId && povUid && typeof runTalkWithApproach === 'function') {
      runTalkWithApproach(povUid, entityId, entityName);
    } else if (entityId && typeof handleDialogBubbleClick === 'function') {
      handleDialogBubbleClick(entityId, entityName);
    }
    closeCenterActionBar();
  });

  $(document).on('click', '.set-pov-action', function (event) {
    event.stopPropagation();
    event.preventDefault();
    const $entry = $(this).closest('.center-action-entry');
    const $tile = $(this).closest('.tile');
    const entityId = $entry.data('entityUid') || $tile.data('coords-id');
    if ($entry.length) {
      activateCenterActionEntry($entry);
    }
    if (entityId) {
      switchPOV(entityId, globalCanvas);
    }
    closeCenterActionBar();
  });

  $(document).on('click', '.conversation-bubble', function (event) {
    event.stopPropagation();
    Utils.toggleBubble(this);
  });

  // Handle dialog bubble clicks for dialog-capable entities
  function getDialogPartnerEntityId() {
    const selected = ($('#dialogPartnerSelect').val() || dialogPartnerEntityId || '').trim();
    if (selected) {
      return selected;
    }
    return Chat.getCurrentPovEntity() || null;
  }

  function updateDialogManualModeUi() {
    const npcName = $('#dialogEntityName').text() || 'this NPC';
    if (dialogNpcManualMode) {
      $('body').addClass('dialog-manual-mode-active');
      $('#dialogChatInput').attr(
        'placeholder',
        `Speak as ${npcName}… (manual mode — no LLM)`,
      );
    } else {
      $('body').removeClass('dialog-manual-mode-active');
      $('#dialogChatInput').attr('placeholder', 'Say something, or *describe an action*');
    }
  }

  function loadDialogDmNpcControls(entityId, entity) {
    const $panel = $('#dialogDmNpcControls');
    const $profilePanel = $('#dialogEntityProfilePanel');
    if (!isDM() || !entity || !entity.is_npc) {
      $panel.hide();
      $profilePanel.removeClass('has-dm-npc-controls');
      dialogNpcManualMode = false;
      currentDialogNpcId = null;
      $('body').removeClass('dialog-manual-mode-active');
      return;
    }

    currentDialogNpcId = entityId;
    $panel.show();
    $profilePanel.addClass('has-dm-npc-controls');
    dialogNpcManualMode = entity.conversation_mode === 'manual';
    $('#dialogManualModeToggle').prop('checked', dialogNpcManualMode);
    updateDialogManualModeUi();

    const $partner = $('#dialogPartnerSelect');
    $partner.empty().append('<option value="">Conversation partner…</option>');
    const candidates = entity.partner_candidates || [];
    candidates.forEach((row) => {
      const safeName = $('<div>').text(row.name || row.id).html();
      $partner.append(`<option value="${row.id}">${safeName}</option>`);
    });
    const pov = Chat.getCurrentPovEntity();
    const defaultPartner = candidates.some((row) => row.id === pov) ? pov : '';
    if (defaultPartner) {
      $partner.val(defaultPartner);
      dialogPartnerEntityId = defaultPartner;
    } else {
      dialogPartnerEntityId = null;
    }
    $partner.toggle(candidates.length > 0);
  }

  function setDialogNpcConversationMode(entityId, mode) {
    if (!entityId || !isDM()) {
      return;
    }
    $.ajax({
      url: '/admin/npc_conversation_mode',
      type: 'POST',
      contentType: 'application/json',
      data: JSON.stringify({ entity_id: entityId, mode }),
      success: (data) => {
        if (!data || !data.success) {
          Chat.addDialogMessage('system', (data && data.error) || 'Failed to update conversation mode.', 'system');
          return;
        }
        dialogNpcManualMode = data.conversation_mode === 'manual';
        $('#dialogManualModeToggle').prop('checked', dialogNpcManualMode);
        updateDialogManualModeUi();
        const label = dialogNpcManualMode ? 'Manual mode enabled — you control this NPC.' : 'LLM mode restored for this NPC.';
        Chat.addDialogMessage('system', label, 'system');
        if (data.partner_candidates) {
          loadDialogDmNpcControls(entityId, data);
        }
      },
      error: () => {
        Chat.addDialogMessage('system', 'Failed to update conversation mode.', 'system');
      },
    });
  }

  function handleDialogBubbleClick(entityId, entityName, options) {
    const opts = options || {};
    console.log('Dialog bubble clicked for entity:', entityId, entityName);

    // Set talk to entity mode flag
    talkToEntityMode = true;
    currentDialogNpcId = entityId;
    $('#dialogEntityName').text(entityName || entityId).data('entity-id', entityId);
    $('#jrpgDialogPanel').show();
    if (opts.forced) {
      forcedConversationMode = true;
      $('body').addClass('forced-conversation-active');
      $('#close-dialog').hide();
      $('#dialogNoResponse').hide();
    } else if (opts.goalArrival) {
      goalArrivalConversationMode = true;
      $('body').addClass('goal-arrival-active');
      $('#dialogNoResponse').show();
    }

    // Update panel title to indicate talk to entity mode
    $('#jrpgDialogPanelLabel').text('Talk to Entity');


    // Load entity information for the profile panel
    loadEntityProfile(entityId);

    // Load conversation languages
    loadDialogLanguages(entityId);

    // Initialize chat interface
    initializeDialogChat(entityId);

    // Add welcome message with the provided entity name
    const currentPovEntity = Chat.getCurrentPovEntity();
    if (currentPovEntity) {
      Chat.addDialogMessage('system', `You are now talking to ${entityName || 'this entity'}.`, 'system');
    } else {
      Chat.addDialogMessage('system', `Welcome to the conversation with ${entityName || 'this entity'}!`, 'system');
    }
  }
  window.handleDialogBubbleClick = handleDialogBubbleClick;

  function openMerchantTradeModal(merchantUid, buyerUid) {
    if (!merchantUid || !buyerUid) return;
    Utils.ajaxGet(
      '/merchant',
      { merchant_uid: merchantUid, buyer_uid: buyerUid },
      (html) => {
        $('#modal-1 .modal-content').html(html);
        $('#modal-1').modal('show');
      },
    );
  }
  window.openMerchantTradeModal = openMerchantTradeModal;

  function updateMerchantDialogButton(entityInfo) {
    const $btn = $('#dialogBrowseShop');
    const $profilePanel = $('#dialogEntityProfilePanel');
    const buyerUid = Chat.getCurrentPovEntity();
    const merchantUid = $('#dialogEntityName').data('entity-id');
    const show = !!(entityInfo && entityInfo.is_merchant && buyerUid && merchantUid && buyerUid !== merchantUid);
    $btn.toggle(show);
    $profilePanel.toggleClass('has-merchant-shop', show);
    if (show && entityInfo.merchant_shop_title) {
      $btn.attr('title', `Browse ${entityInfo.merchant_shop_title}`);
    }
  }

  function updateInvitePartyButton(entityInfo) {
    const $btn = $('#dialogInviteParty');
    const show = !!(entityInfo && entityInfo.can_invite_party && entityInfo.is_npc && !entityInfo.is_sidekick);
    $btn.toggle(show);
  }
  window.updateInvitePartyButton = updateInvitePartyButton;

  // Load entity profile information
  function isDialogDescriptionSuppressed() {
    const role = ($('body').attr('data-role') || '').toLowerCase();
    if (role.includes('dm')) {
      return false;
    }
    const raw = $('body').attr('data-suppress-dialog-descriptions');
    return raw === 'true' || raw === '1';
  }

  function setDialogEntityDescription(description, suppressed) {
    const $panel = $('.entity-description');
    const $overlay = $('.entity-portrait-overlay');
    if (isDialogDescriptionSuppressed() || suppressed || description == null) {
      $panel.hide();
      $overlay.addClass('description-hidden');
      $('#dialogEntityDescription').text('');
      return;
    }
    $panel.show();
    $overlay.removeClass('description-hidden');
    $('#dialogEntityDescription').text(description || 'No description available.');
  }

  function dialogAssetUrl(relativePath) {
    if (!relativePath) {
      return '';
    }
    const normalized = String(relativePath).replace(/^\/+/, '');
    return `/assets/${normalized}`;
  }

  function applyDialogEntityPortrait(entity) {
    const $profilePanel = $('#dialogEntityProfilePanel');
    const $portrait = $('#dialogEntityPortrait');
    const gallery = (entity && entity.image_gallery) || [];
    if (gallery.length) {
      $profilePanel.data('imageGallery', gallery);
    }
    const tokenPath = entity && entity.token_image;
    const profilePath = entity && entity.profile_image;
    const hasFullPortrait = !!(entity && entity.has_full_portrait) || gallery.length > 0;
    const tokenSrc = dialogAssetUrl(tokenPath) || '/static/assets/token_player.png';
    const profileSrc = dialogAssetUrl(profilePath) || tokenSrc;

    let preferredSrc = hasFullPortrait ? profileSrc : tokenSrc;
    if (entity && entity.gallery_image) {
      preferredSrc = dialogAssetUrl(entity.gallery_image) || preferredSrc;
    } else if (entity && entity.gallery_image_id && gallery.length) {
      const match = gallery.find((entry) => entry.id === entity.gallery_image_id);
      if (match && match.image) {
        preferredSrc = dialogAssetUrl(match.image) || preferredSrc;
      }
    }

    $profilePanel
      .toggleClass('has-full-portrait', hasFullPortrait || preferredSrc !== tokenSrc)
      .toggleClass('has-token-portrait', !hasFullPortrait && preferredSrc === tokenSrc);
    $portrait.off('error.dialogPortrait').on('error.dialogPortrait', function onPortraitError() {
      const $img = $(this);
      $img.off('error.dialogPortrait');
      if (hasFullPortrait && tokenSrc && $img.attr('src') !== tokenSrc) {
        $profilePanel.removeClass('has-full-portrait').addClass('has-token-portrait');
        $img.attr('src', tokenSrc);
        $img.on('error.dialogPortrait', function onTokenError() {
          $img.off('error.dialogPortrait');
          $img.attr('src', '/static/assets/token_player.png');
        });
        return;
      }
      $img.attr('src', '/static/assets/token_player.png');
    });
    $portrait.attr('src', preferredSrc);
    $portrait.attr('alt', (entity && entity.name) ? `${entity.name} portrait` : 'Entity portrait');
  }
  window.applyDialogEntityPortrait = applyDialogEntityPortrait;

  function loadEntityProfile(entityId) {
    // Get entity information from the tile data
    const $tile = $(`.tile[data-coords-id="${entityId}"]`);

    // Get entity name from the tile label or entity name
    let entityName = 'Unknown Entity';
    const $nameplate = $tile.find('.nameplate');
    if ($nameplate.length) {
      entityName = $nameplate.text();
    } else {
      // Try to get from entity data attribute
      entityName = $tile.data('entity-name') || 'Unknown Entity';
    }

    // Set entity name initially and store entity ID
    $('#dialogEntityName').text(entityName).data('entity-id', entityId);

    // Show token immediately while detailed entity info loads.
    applyDialogEntityPortrait({
      name: entityName,
      token_image: null,
      profile_image: null,
      has_full_portrait: false,
    });
    const $entityImg = $tile.find('.npc');
    if ($entityImg.length) {
      const tileSrc = $entityImg.attr('src');
      if (tileSrc) {
        $('#dialogEntityPortrait').attr('src', tileSrc);
      }
    }

    // Load detailed entity information via AJAX
    $.ajax({
      url: '/entity_info',
      type: 'GET',
      data: { entity_id: entityId },
      success: (data) => {
        if (data.success) {
          const entity = data.entity;

          // Update entity name with the proper name from server
          if (entity.name) {
            $('#dialogEntityName').text(entity.name).data('entity-id', entityId);
          }

          applyDialogEntityPortrait(entity);

          // Update entity stats
          $('#dialogEntityHP').text(`${entity.hp || 0}/${entity.max_hp || 0}`);
          $('#dialogEntityAC').text(entity.ac || 'Unknown');
          $('#dialogEntityLevel').text(entity.level || 'Unknown');
          $('#dialogEntityRace').text(entity.race || 'Unknown');
          $('#dialogEntityClass').text(entity.class || 'Unknown');

          // Update description (campaign may suppress spoilers)
          setDialogEntityDescription(entity.description, entity.description_suppressed);

          updateMerchantDialogButton(entity);
          updateInvitePartyButton(entity);
          loadDialogDmNpcControls(entityId, entity);
        } else {
          // Set default values if entity info not available
          $('#dialogEntityHP').text('0/0');
          $('#dialogEntityAC').text('Unknown');
          $('#dialogEntityLevel').text('Unknown');
          $('#dialogEntityRace').text('Unknown');
          $('#dialogEntityClass').text('Unknown');
          setDialogEntityDescription(null, isDialogDescriptionSuppressed());
          updateMerchantDialogButton(null);
          updateInvitePartyButton(null);
          loadDialogDmNpcControls(entityId, null);
        }
      },
      error: () => {
        // Set default values on error
        $('#dialogEntityHP').text('0/0');
        $('#dialogEntityAC').text('Unknown');
        $('#dialogEntityLevel').text('Unknown');
        $('#dialogEntityRace').text('Unknown');
        $('#dialogEntityClass').text('Unknown');
        setDialogEntityDescription(null, isDialogDescriptionSuppressed());
        updateMerchantDialogButton(null);
        loadDialogDmNpcControls(entityId, null);
      }
    });
  }

  $('#dialogManualModeToggle').off('change.dialogManualMode').on('change.dialogManualMode', function () {
    const entityId = $('#dialogEntityName').data('entity-id');
    if (!entityId) {
      return;
    }
    setDialogNpcConversationMode(entityId, $(this).is(':checked') ? 'manual' : 'llm');
  });

  $('#dialogPartnerSelect').off('change.dialogPartner').on('change.dialogPartner', function () {
    dialogPartnerEntityId = ($(this).val() || '').trim() || null;
    const entityId = $('#dialogEntityName').data('entity-id');
    if (entityId) {
      loadDialogHistory(entityId);
    }
  });

  $('#dialogBrowseShop').off('click.merchantShop').on('click.merchantShop', function () {
    const merchantUid = $('#dialogEntityName').data('entity-id');
    const buyerUid = Chat.getCurrentPovEntity();
    openMerchantTradeModal(merchantUid, buyerUid);
  });

  $('#dialogInviteParty').off('click.inviteParty').on('click.inviteParty', function () {
    const entityUid = $('#dialogEntityName').data('entity-id');
    if (!entityUid) {
      return;
    }
    const force = isDM() && !!(window.event && window.event.shiftKey);
    ajaxPost(
      '/party/invite',
      { entity_uid: entityUid, force },
      (data) => {
        if (data && data.error) {
          if (typeof Utils !== 'undefined' && Utils.showToast) {
            Utils.showToast(data.error);
          }
          return;
        }
        if (data && data.replies && typeof Chat !== 'undefined' && Chat.ingestTalkReplies) {
          Chat.ingestTalkReplies(data.replies);
        }
        $('#dialogInviteParty').hide();
      },
      true
    );
  });

  // Load available languages for the dialog
  function loadDialogLanguages(entityId) {
    const $tile = $(`.tile[data-coords-id="${Chat.getCurrentPovEntity()}"]`);
    const languages = $tile.data('conversation-languages');

    const $languageSelect = $('#dialogLanguageSelect');
    $languageSelect.empty();

    if (languages && languages.length > 0) {
      const languagesArray = languages.split(',');
      languagesArray.forEach(language => {
        const lang = language.trim();
        if (lang !== 'common') {
          $languageSelect.append(`<option value="${lang}">${lang}</option>`);
        } else {
          $languageSelect.append(`<option value="Common" selected>Common</option>`);
        }
      });
    } else {
      $languageSelect.append(`<option value="Common" selected>Common</option>`);
    }
  }

  // Initialize the dialog chat interface
  function initializeDialogChat(entityId) {
    // Clear previous messages
    $('#dialogChatMessages').empty();

    // Handle send message button
    $('#dialogSendMessage').off('click').on('click', function () {
      sendDialogMessage(entityId);
    });

    $('#dialogInCharacterBtn').off('click').on('click', function () {
      const povEntity = Chat.getCurrentPovEntity();
      if (!povEntity || (isDM() && dialogNpcManualMode && talkToEntityMode)) {
        return;
      }
      Chat.polishConversationInput({
        inputSelector: '#dialogChatInput',
        buttonSelector: '#dialogInCharacterBtn',
        entityId: povEntity,
        language: $('#dialogLanguageSelect').val(),
        onStatus: (msg) => {
          if (msg) {
            Chat.addDialogMessage('system', msg, 'system');
          }
        },
      });
    });

    $('#dialogNoResponse').off('click').on('click', function () {
      sendDialogNoResponse(entityId);
    });

    // Handle Enter key in chat input
    $('#dialogChatInput').off('keypress').on('keypress', function (e) {
      if (e.which === 13) { // Enter key
        e.preventDefault(); // Prevent default to avoid form submission
        sendDialogMessage(entityId);
      }
    });
    $('#dialogChatInput').off('input.dialogAutoContinue keydown.dialogAutoContinue')
      .on('input.dialogAutoContinue keydown.dialogAutoContinue', function () {
        if (typeof Chat.cancelDialogAutoContinue === 'function') {
          Chat.cancelDialogAutoContinue();
        }
      });

    // Handle volume button clicks
    $('.volume-btn').off('click').on('click', function () {
      $('.volume-btn').removeClass('active');
      $(this).addClass('active');
    });

    loadDialogHistory(entityId);
    bindDialogPerceptionButton();

    // Handle mode toggle button
    $('#toggleTalkMode').off('click').on('click', function () {
      talkToEntityMode = !talkToEntityMode;
      updateTalkModeDisplay();
    });

    // Focus on input
    $('#dialogChatInput').focus();
  }

  // Update the display based on talk mode
  function updateTalkModeDisplay() {
    if (talkToEntityMode) {
      $('#dialogModeIndicator').show();
      $('#jrpgDialogPanelLabel').text('Talk to Entity');
      $('#toggleTalkMode').removeClass('btn-info').addClass('btn-warning').html('<i class="glyphicon glyphicon-comment"></i> Regular Mode');
      Chat.addDialogMessage('system', 'Switched to Talk to Entity mode. You are now talking to the entity.', 'system');
    } else {
      $('#dialogModeIndicator').hide();
      $('#jrpgDialogPanelLabel').text('Dialog');
      $('#toggleTalkMode').removeClass('btn-warning').addClass('btn-info').html('<i class="glyphicon glyphicon-comment"></i> Talk Mode');
      Chat.addDialogMessage('system', 'Switched to Regular Dialog mode.', 'system');
    }
  }

  // Send a message in the dialog
  function sendDialogInsightCheck({ messageKey, targetId, statement }) {
    if (!messageKey || !targetId || !statement) {
      Chat.addDialogMessage('system', 'Could not roll Insight on this line.', 'system');
      return;
    }
    if (insightCheckProcessing) {
      return;
    }
    const currentPovEntity = Chat.getCurrentPovEntity();
    if (!currentPovEntity) {
      Chat.addDialogMessage('system', 'Select a character before rolling Insight.', 'system');
      return;
    }

    insightCheckProcessing = true;
    const waitingId = addWaitingIndicator();
    updateWaitingIndicator(waitingId, 'Rolling Insight...');

    $.ajax({
      url: '/talk',
      type: 'POST',
      timeout: 120000,
      contentType: 'application/json',
      data: JSON.stringify({
        entity_id: currentPovEntity,
        insight_check: {
          target_id: targetId,
          statement: statement,
        },
      }),
      success: (data) => {
        removeWaitingIndicator(waitingId);
        if (data.success && data.insight && typeof data.insight === 'object' && data.insight.assessment) {
          const attachKey = data.insight.message_key || messageKey;
          const attached = Chat.applyInsightResultToDialogMessage(attachKey, data.insight);
          if (!attached) {
            Chat.addDialogInsightResult(data.insight, null, {
              attach_to_message_key: attachKey,
            });
          }
        } else if (data.insight === 'needs_target' || data.insight === 'needs_purpose') {
          Chat.addDialogMessage('system', 'Could not complete the Insight check. Try typing a more specific request.', 'system');
        } else if (!data.success) {
          Chat.addDialogMessage('system', 'Insight check failed.', 'system');
        }
      },
      error: () => {
        removeWaitingIndicator(waitingId);
        Chat.addDialogMessage('system', 'Insight check failed.', 'system');
      },
      complete: () => {
        insightCheckProcessing = false;
      },
    });
  }
  window.sendDialogInsightCheck = sendDialogInsightCheck;

  function sendDialogPerceptionCheck({ targetId, focus }) {
    if (!targetId) {
      Chat.addDialogMessage('system', 'Could not roll Perception without a target.', 'system');
      return;
    }
    if (perceptionCheckProcessing) {
      return;
    }
    const currentPovEntity = Chat.getCurrentPovEntity();
    if (!currentPovEntity) {
      Chat.addDialogMessage('system', 'Select a character before rolling Perception.', 'system');
      return;
    }

    perceptionCheckProcessing = true;
    const waitingId = addWaitingIndicator();
    updateWaitingIndicator(waitingId, 'Rolling Perception...');

    const payload = {
      entity_id: currentPovEntity,
      perception_check: {
        target_id: targetId,
      },
    };
    if (focus && String(focus).trim()) {
      payload.perception_check.focus = String(focus).trim();
    }

    $.ajax({
      url: '/talk',
      type: 'POST',
      timeout: 120000,
      contentType: 'application/json',
      data: JSON.stringify(payload),
      success: (data) => {
        removeWaitingIndicator(waitingId);
        if (data.perception && data.perception.roll_total != null) {
          playPerceptionCheckFeedback(currentPovEntity, data.perception.roll_total);
        }
        if (data.success && data.perception && data.perception.description) {
          const targetLabel = data.perception.target || 'the scene';
          const messageText = data.already_checked
            ? `You already studied ${targetLabel}.`
            : null;
          Chat.addDialogPerceptionResult(
            data.perception,
            messageText,
            data.already_checked ? { force: true } : {},
          );
        } else if (data.perception === 'needs_target') {
          Chat.addDialogMessage('system', 'Specify whom you are studying for Perception.', 'system');
        } else if (!data.success) {
          Chat.addDialogMessage('system', 'Perception check failed.', 'system');
        }
      },
      error: () => {
        removeWaitingIndicator(waitingId);
        Chat.addDialogMessage('system', 'Perception check failed.', 'system');
      },
      complete: () => {
        perceptionCheckProcessing = false;
      },
    });
  }
  window.sendDialogPerceptionCheck = sendDialogPerceptionCheck;

  function bindDialogPerceptionButton() {
    $('#dialogPerceptionCheck').off('click.dialogPerception').on('click.dialogPerception', function () {
      const targetId = $('#dialogEntityName').data('entity-id');
      const focus = $('#dialogChatInput').val().trim() || null;
      sendDialogPerceptionCheck({ targetId, focus });
    });
  }

  let skillCheckProcessing = false;

  function sendDialogSkillCheck({ requesterUid, skill, dc, replyId }) {
    if (!requesterUid || !skill) {
      Chat.addDialogMessage('system', 'Could not roll this skill check.', 'system');
      return;
    }
    if (skillCheckProcessing) {
      return;
    }
    const currentPovEntity = Chat.getCurrentPovEntity();
    if (!currentPovEntity) {
      Chat.addDialogMessage('system', 'Select a character before rolling.', 'system');
      return;
    }

    skillCheckProcessing = true;
    const waitingId = addWaitingIndicator();
    updateWaitingIndicator(waitingId, `Rolling ${skill.replace(/_/g, ' ')}...`);

    const payload = {
      entity_id: currentPovEntity,
      skill_check: {
        requester_uid: requesterUid,
        skill: skill,
        reply_id: replyId || null,
      },
    };
    if (dc !== undefined && dc !== null && `${dc}`.trim() !== '') {
      const parsedDc = Number(dc);
      if (!Number.isNaN(parsedDc)) {
        payload.skill_check.dc = parsedDc;
      }
    }

    $.ajax({
      url: '/talk',
      type: 'POST',
      timeout: 120000,
      contentType: 'application/json',
      data: JSON.stringify(payload),
      success: (data) => {
        removeWaitingIndicator(waitingId);
        if (data.success && data.skill_check && data.skill_check.roll_total != null) {
          const replyKey = data.skill_check.reply_id || replyId;
          const attached = replyKey
            ? Chat.applySkillCheckResultToDialogMessage(replyKey, data.skill_check)
            : false;
          if (!attached) {
            const label = data.skill_check.skill_label || skill;
            Chat.addDialogMessage(
              'system',
              `${label} check: ${data.skill_check.roll_total}`,
              'system',
              { displayName: 'Dungeon Master', skill_check_result: data.skill_check },
            );
          }
          Chat.ingestTalkReplies(data.replies);
          if (data.npc_reply_failed) {
            Chat.addDialogMessage(
              'system',
              'Your roll was recorded, but the NPC could not respond right now.',
              'system',
              { displayName: 'Dungeon Master' },
            );
          }
          if (typeof clearDialogWaitingState === 'function') {
            clearDialogWaitingState();
          }
        } else if (!data.success) {
          const err = (data && data.error) ? String(data.error) : 'Skill check failed.';
          Chat.addDialogMessage('system', err, 'system');
          if (typeof Chat.setLocalConversationStatus === 'function') {
            Chat.setLocalConversationStatus(err);
          }
        }
      },
      error: (xhr) => {
        removeWaitingIndicator(waitingId);
        let err = 'Skill check failed.';
        try {
          const body = xhr.responseJSON || JSON.parse(xhr.responseText || '{}');
          if (body && body.error) {
            err = String(body.error);
          }
        } catch (_) {
        }
        Chat.addDialogMessage('system', err, 'system');
        if (typeof Chat.setLocalConversationStatus === 'function') {
          Chat.setLocalConversationStatus(err);
        }
      },
      complete: () => {
        skillCheckProcessing = false;
      },
    });
  }
  window.sendDialogSkillCheck = sendDialogSkillCheck;

  function sendDialogNoResponse(npcEntityId) {
    if (!goalArrivalConversationMode || dialogMessageProcessing) {
      return;
    }
    const currentPovEntity = Chat.getCurrentPovEntity();
    if (!currentPovEntity || !npcEntityId) {
      return;
    }

    dialogMessageProcessing = true;
    window.__n20_dialogMessageProcessing = true;

    const $input = $('#dialogChatInput');
    const $sendButton = $('#dialogSendMessage');
    const $noResponseButton = $('#dialogNoResponse');
    const $languageSelect = $('#dialogLanguageSelect');
    const $inputContainer = $('.chat-input-container');

    $input.prop('disabled', true);
    $sendButton.prop('disabled', true);
    $noResponseButton.prop('disabled', true);
    $languageSelect.prop('disabled', true);
    $inputContainer.addClass('disabled');

    $.ajax({
      url: '/talk',
      type: 'POST',
      timeout: 30000,
      contentType: 'application/json',
      data: JSON.stringify({
        entity_id: currentPovEntity,
        no_response: true,
        targets: [npcEntityId],
      }),
      success: (data) => {
        if (typeof clearDialogWaitingState === 'function') {
          clearDialogWaitingState();
        }
        Chat.addDialogMessage('system', 'You give no response.', 'system');
        exitGoalArrivalConversationMode();
        talkToEntityMode = false;
        $('#jrpgDialogPanel').hide();
      },
      error: (xhr) => {
        const errText = (xhr.responseJSON && xhr.responseJSON.error) || 'Could not dismiss conversation.';
        Chat.addDialogMessage('system', errText, 'system');
      },
      complete: () => {
        dialogMessageProcessing = false;
        window.__n20_dialogMessageProcessing = false;
        $input.prop('disabled', false);
        $sendButton.prop('disabled', false).html('<i class="glyphicon glyphicon-send"></i> Send');
        $noResponseButton.prop('disabled', false);
        $languageSelect.prop('disabled', false);
        $inputContainer.removeClass('disabled');
      },
    });
  }
  window.sendDialogNoResponse = sendDialogNoResponse;

  function sendDialogMessage(entityId) {
    const rawMessage = $('#dialogChatInput').val().trim();
    if (!rawMessage) return;
    const parsedMessage = Chat.parsePlayerConversationInput(rawMessage);
    if (!parsedMessage.spoken && !(parsedMessage.actions || []).length) return;

    const dmManualReply = isDM() && dialogNpcManualMode && talkToEntityMode;
    const partnerEntityId = getDialogPartnerEntityId();
    if (dmManualReply && !partnerEntityId) {
      Chat.addDialogMessage('system', 'Select a conversation partner (player character) before speaking as this NPC.', 'system');
      return;
    }

    // Prevent multiple messages from being sent simultaneously
    if (dialogMessageProcessing) return;

    if (typeof Chat.resetDialogAutoContinue === 'function') {
      Chat.resetDialogAutoContinue();
    }

    dialogMessageProcessing = true;
    window.__n20_dialogMessageProcessing = true;

    try {
      if (window.TTSPlayer && typeof window.TTSPlayer.unlockAudioForPlayback === 'function') {
        window.TTSPlayer.unlockAudioForPlayback();
      }
    } catch (ttsUnlockErr) {
      console.warn('[Dialog] TTS audio unlock failed:', ttsUnlockErr);
    }

    const selectedLanguage = $('#dialogLanguageSelect').val();
    const selectedVolume = $('#localConversationVolume').val() || 'normal';

    if (dmManualReply) {
      Chat.addDialogMessage('entity', parsedMessage.spoken, 'entity', {
        displayName: $('#dialogEntityName').text() || 'NPC',
        actions: parsedMessage.actions,
        entity_id: entityId,
      });
    } else {
      // Add player message to chat
      Chat.addDialogMessage('player', parsedMessage.spoken, 'player', {
        actions: parsedMessage.actions,
      });
    }

    // Clear input
    $('#dialogChatInput').val('');

    // Disable input and send button while processing
    const $input = $('#dialogChatInput');
    const $sendButton = $('#dialogSendMessage');
    const $languageSelect = $('#dialogLanguageSelect');
    const $inputContainer = $('.chat-input-container');

    $input.prop('disabled', true);
    $sendButton.prop('disabled', true).html('<i class="glyphicon glyphicon-refresh spinning"></i> Sending...');
    $languageSelect.prop('disabled', true);
    $inputContainer.addClass('disabled');

    // Add waiting indicator
    const waitingId = addWaitingIndicator();
    activeDialogWaitingId = waitingId;

    // Set up timeout indicators for longer processing times
    const timeout1 = setTimeout(() => {
      updateWaitingIndicator(waitingId, "Processing your message...");
    }, 2000);

    const timeout2 = setTimeout(() => {
      updateWaitingIndicator(waitingId, dmManualReply ? 'Broadcasting NPC line…' : 'Entity is thinking...');
    }, 5000);

    const timeout3 = setTimeout(() => {
      updateWaitingIndicator(waitingId, dmManualReply ? 'Still sending…' : 'Almost ready to respond...');
    }, 10000);

    // Determine the source entity based on mode
    let sourceEntityId = entityId;
    let talkTargets = [];
    let dmImpersonate = false;
    if (dmManualReply) {
      sourceEntityId = entityId;
      talkTargets = [partnerEntityId];
      dmImpersonate = true;
    } else if (talkToEntityMode) {
      // In talk to entity mode, the POV user is talking to the entity
      const currentPovEntity = Chat.getCurrentPovEntity();
      if (currentPovEntity) {
        sourceEntityId = currentPovEntity;
      }
      talkTargets = [entityId];
    }

    // Send message to server
    $.ajax({
      url: '/talk',
      type: 'POST',
      timeout: 120000,
      contentType: 'application/json',
      data: JSON.stringify({
        entity_id: sourceEntityId,
        message: rawMessage,
        targets: talkTargets,
        no_specific_target: !talkToEntityMode && !dmManualReply,
        language: selectedLanguage,
        volume: selectedVolume,
        dm_impersonate: dmImpersonate,
      }),
      success: (data) => {
        // Clear timeouts
        clearTimeout(timeout1);
        clearTimeout(timeout2);
        clearTimeout(timeout3);

        if (typeof clearDialogWaitingState === 'function') { clearDialogWaitingState(); }
        $input.focus();

        if (data.success) {
          if (data.goal_arrival_ended && typeof exitGoalArrivalConversationMode === 'function') {
            exitGoalArrivalConversationMode();
          }
          Chat.ingestTalkReplies(data.replies);
          if (data.manual_mode_npcs && data.manual_mode_npcs.length) {
            Chat.addDialogMessage(
              'system',
              'This NPC is in manual DM mode — waiting for the Dungeon Master to respond in character.',
              'system',
            );
          }
          // Insight checks return via HTTP as well as Socket.IO. Prefer the
          // HTTP payload for the open dialog so the badge always appears even
          // if the socket event is delayed; dedupe prevents a double render.
          if (data.insight && typeof data.insight === 'object' && data.insight.assessment) {
            const attachKey = data.insight.message_key || null;
            const attached = attachKey
              ? Chat.applyInsightResultToDialogMessage(attachKey, data.insight)
              : false;
            if (!attached) {
              Chat.addDialogInsightResult(data.insight, null, {
                attach_to_message_key: attachKey,
              });
            }
          } else if (data.perception && typeof data.perception === 'object' && data.perception.description) {
            Chat.addDialogPerceptionResult(data.perception, null);
          } else if (data.skill_check && data.skill_check.roll_total != null) {
            const replyKey = data.skill_check.reply_id || null;
            const attached = replyKey
              ? Chat.applySkillCheckResultToDialogMessage(replyKey, data.skill_check)
              : false;
            if (!attached) {
              Chat.addDialogMessage('system', `Skill check: ${data.skill_check.roll_total}`, 'system', {
                displayName: 'Dungeon Master',
                skill_check_result: data.skill_check,
              });
            }
            Chat.ingestTalkReplies(data.replies);
          } else if (data.response) {
            Chat.addDialogMessage('entity', data.response, 'entity');
          }
        } else {
          Chat.addDialogMessage('system', 'Message sent successfully.', 'system');
        }
      },
      error: (xhr, status) => {
        // Clear timeouts
        clearTimeout(timeout1);
        clearTimeout(timeout2);
        clearTimeout(timeout3);

        if (typeof clearDialogWaitingState === 'function') { clearDialogWaitingState(); }
        $input.focus();

        if (status === 'timeout') {
          Chat.addDialogMessage('system', 'The server is taking too long to respond. The reply may still arrive shortly.', 'system');
          return;
        }

        Chat.addDialogMessage('system', 'Failed to send message.', 'system');
      }
    });
  }



  // Helper function to show a regular conversation bubble


  // Helper function to show a dialog-trigger conversation bubble
  function showDialogTriggerBubble(entity_id, message, options) {
    const bubbleOptions = Object.assign({}, options || {}, {
      dialogTrigger: true,
      onDialogTriggerClick: function (_e, $tile) {
        const entityName = $tile.find('.nameplate').text() || $tile.data('entity-name') || 'Entity';
        const povUid = $('body').attr('data-pov-entity') || (typeof Chat !== 'undefined' && Chat.getCurrentPovEntity && Chat.getCurrentPovEntity());
        if (povUid && typeof runTalkWithApproach === 'function') {
          runTalkWithApproach(povUid, entity_id, entityName);
        } else if (typeof handleDialogBubbleClick === 'function') {
          handleDialogBubbleClick(entity_id, entityName);
        }
      },
    });
    Chat.showConversationBubble(entity_id, message, bubbleOptions);
  }

  // Add a waiting indicator to the dialog chat
  function addWaitingIndicator() {
    const waitingId = 'waiting-' + Date.now();
    const waitingHtml = `
      <div id="${waitingId}" class="dialog-chat-message waiting">
        <div class="message-sender">System</div>
        <div class="message-content">
          <i class="glyphicon glyphicon-refresh spinning"></i> Waiting for response...
        </div>
        <div class="message-timestamp">${new Date().toLocaleTimeString()}</div>
      </div>
    `;

    $('#dialogChatMessages').append(waitingHtml);

    // Scroll to bottom
    const $messages = $('#dialogChatMessages');
    $messages.scrollTop($messages[0].scrollHeight);

    return waitingId;
  }

  // Update the waiting indicator message
  function updateWaitingIndicator(waitingId, message) {
    const $waiting = $(`#${waitingId}`);
    if ($waiting.length) {
      $waiting.find('.message-content').html(`<i class="glyphicon glyphicon-refresh spinning"></i> ${message}`);
    }
  }

  // Remove the waiting indicator
  function removeWaitingIndicator(waitingId) {
    const $waiting = $(`#${waitingId}`);
    if ($waiting.length) {
      $waiting.fadeOut(300, function () {
        $(this).remove();
      });
    }
    if (activeDialogWaitingId === waitingId) {
      activeDialogWaitingId = null;
    }
  }

  function clearDialogWaitingState() {
    if (!activeDialogWaitingId) {
      return;
    }
    const waitingId = activeDialogWaitingId;
    activeDialogWaitingId = null;
    removeWaitingIndicator(waitingId);

    const $input = $('#dialogChatInput');
    const $sendButton = $('#dialogSendMessage');
    const $languageSelect = $('#dialogLanguageSelect');
    const $inputContainer = $('.chat-input-container');
    $input.prop('disabled', false);
    $sendButton.prop('disabled', false).html('<i class="glyphicon glyphicon-send"></i> Send');
    $languageSelect.prop('disabled', false);
    $inputContainer.removeClass('disabled');
    dialogMessageProcessing = false;
    window.__n20_dialogMessageProcessing = false;
  }

  // Load dialog history
  function loadDialogHistory(entityId) {
    $.ajax({
      url: '/dialog_history',
      type: 'GET',
      data: {
        entity_id: entityId,
        entity_pov: getDialogPartnerEntityId(),
      },
      dataType: 'json',
      beforeSend: () => {
        $('#dialogChatMessages').html('<div class="loading">Loading history...</div>');
      },
      success: (data) => {
        if (data.success) {
          const $historyContainer = $('#dialogChatMessages');
          $historyContainer.empty();

          if (data.history && data.history.length > 0) {
            data.history.forEach(entry => {
              const entryType = entry.type === 'player' ? 'player' : (entry.type === 'system' ? 'system' : 'entity');
              Chat.addDialogMessage(entryType, entry.message || '', entryType, {
                displayName: entry.source || (entryType === 'player' ? 'You' : 'Entity'),
                narrative: entry.narrative || [],
                segments: entry.segments || [],
                actions: entry.actions || [],
                audio_url: entry.audio_url || null,
                tts_emotion: entry.tts_emotion || 'calm',
                entity_id: entry.entity_id || entry.target_id || entityId,
                target_id: entry.target_id || entityId,
                message_key: entry.message_key || null,
                insight_check: entry.insight_check || null,
                insight_checked: entry.insight_checked || false,
                gallery_image: entry.gallery_image || null,
                gallery_image_id: entry.gallery_image_id || null,
              });
            });
            const lastEntityEntry = data.history.filter((entry) => entry.type === 'entity').pop();
            if (lastEntityEntry && (lastEntityEntry.gallery_image || lastEntityEntry.gallery_image_id)) {
              const gallery = $('#dialogEntityProfilePanel').data('imageGallery') || [];
              applyDialogEntityPortrait({
                name: lastEntityEntry.source || $('#dialogEntityName').text(),
                has_full_portrait: true,
                gallery_image: lastEntityEntry.gallery_image || null,
                gallery_image_id: lastEntityEntry.gallery_image_id || null,
                image_gallery: gallery,
              });
            }
            Chat.refreshDialogInsightButton();
          } else {
            $historyContainer.append('<div class="no-history">No conversation history available.</div>');
          }
        } else {
          $('#dialogHistoryContainer').html('<div class="error">Failed to load history.</div>');
        }
      },
      error: () => {
        $('#dialogHistoryContainer').html('<div class="error">Error loading history.</div>');
      }
    });
  }


  // Reset talk to entity mode when panel is closed
  $('#close-dialog').on('click', function () {
    if (forcedConversationMode) {
      return;
    }
    if (goalArrivalConversationMode) {
      exitGoalArrivalConversationMode();
    }
    $('#jrpgDialogPanel').hide();
    talkToEntityMode = false;
    dialogPartnerEntityId = null;
    dialogNpcManualMode = false;
    currentDialogNpcId = null;
    $('#dialogDmNpcControls').hide();
    $('#dialogEntityProfilePanel').removeClass('has-dm-npc-controls');
    $('body').removeClass('dialog-manual-mode-active');
    if (typeof Chat.resetDialogAutoContinue === 'function') {
      Chat.resetDialogAutoContinue();
    }
    $('#dialogModeIndicator').hide();
    $('#jrpgDialogPanelLabel').text('Dialog');
    console.log('Talk to entity mode reset');
  });

  // Minimize dialog panel
  $('#minimize-dialog').on('click', function () {
    const $panel = $('#jrpgDialogPanel');
    const $body = $panel.find('.dialog-panel-body');
    const $header = $panel.find('.dialog-panel-header');

    if ($body.is(':visible')) {
      $body.hide();
      $panel.css('height', '60px');
      $(this).html('<i class="glyphicon glyphicon-plus"></i>');
    } else {
      $body.show();
      $panel.css('height', '600px');
      $(this).html('<i class="glyphicon glyphicon-minus"></i>');
    }
  });

  $("#turn-order").on("change", ".group-select", function () {
    const $turnOrderItem = $(this).closest(".turn-order-item");
    const entity_uid = $turnOrderItem.data("id");
    console.log("Changing group for entity:", entity_uid); // Debug log
    const new_group = $(this).val();
    ajaxPost(
      "/update_group",
      { entity_uid, group: new_group },
      (data) => {
        console.log("Group updated successfully:", data);
      },
      true
    );
  });

  $("#turn-order").on("change", ".controller-select", function () {
    const $turnOrderItem = $(this).closest(".turn-order-item");
    const entity_uid = $turnOrderItem.data("id");
    console.log("Changing controller for entity:", entity_uid); // Debug log
    const new_controller = $(this).val();
    ajaxPost(
      "/update_controller",
      { entity_uid, controller: new_controller, action: "set" },
      (data) => {
        console.log("Controller updated successfully:", data);
      },
      true
    );
  });

  // DM can change default NPC controller from AI tab
  $(document).on("change", "#default-npc-controller", function () {
    const value = $(this).val();
    ajaxPost(
      "/update_npc_default_controller",
      { value },
      (data) => {
        console.log("Default NPC controller set to:", data);
      },
      true
    );
  });


  $(".tiles-container").on('click', '.dialog-bubble', function (event) {
    event.stopPropagation();
    event.preventDefault();
    const entityId = $(this).data('id');
    const entityName = $(this).data('name');
    const povUid = $('body').attr('data-pov-entity') || (typeof Chat !== 'undefined' && Chat.getCurrentPovEntity && Chat.getCurrentPovEntity());
    if (!povUid) {
      if (typeof Utils !== 'undefined' && Utils.showToast) {
        Utils.showToast('Select a character first.');
      }
      return;
    }
    runTalkWithApproach(povUid, entityId, entityName);
  });

  $(".tiles-container").on('click', '.object-quick-action', function (event) {
    event.stopPropagation();
    event.preventDefault();
    activateObjectQuickAction(this);
  });

  // DM Entity Drag and Drop functionality
  let isDraggingEntity = false;
  let draggedEntityId = null;
  let draggedEntityTile = null;
  let dragOffset = { x: 0, y: 0 };
  let dragGhost = null;
  // Delay actual drag until cursor moves past a small threshold
  let dragPending = false;
  let dragStartX = 0, dragStartY = 0;
  let dragTileCenterX = 0, dragTileCenterY = 0;

  // Mouse down on entity tile (start drag)
  $(".tiles-container").on("mousedown", ".tile", function (e) {
    // Only allow DMs to drag entities
    if (!isDM()) return;

    if (forcedConversationMode) return;

    // Only respond to primary button
    if (e.button !== 0) return;

    if (isEditMode() && $(e.target).closest('.edit-mode-drag-handle').length) {
      return;
    }

    const $tile = $(this);
    let entityId = $tile.data("coords-id");

    // Edit mode: Shift+drag on object tiles falls back to label handles first.
    if (isEditMode() && e.shiftKey) {
      return;
    } else if (!entityId && isEditMode()) {
      const $obj = $tile.find('.object-container').first();
      if ($obj.length) {
        return;
      }
    }

    // Only allow dragging if there's an entity on this tile
    if (!entityId || (!$tile.find('.entity, .npc').length && !isEditMode())) return;
    if (isEditMode() && !$tile.find('.entity, .npc, .object-container').length) return;

    // Prevent dragging if clicking on action buttons or other interactive elements
    if ($(e.target).closest('.popover-menu, .popover-menu-2, .action-button, .add-to-turn-order, .dialog-bubble, .conversation-bubble, .close-bubble, .add-to-target, .object-quick-action, .object-quick-actions, .move-quick-action, .move-quick-action-layer, .dm-note-pin').length) {
      return;
    }

    // Prepare for potential drag (defer actual drag start until movement threshold exceeded)
    draggedEntityId = entityId;
    draggedEntityTile = $tile;
    const tileRect = $tile[0].getBoundingClientRect();
    const tileSize = $(".tiles-container").data("tile-size");
    dragTileCenterX = tileRect.left + tileSize / 2;
    dragTileCenterY = tileRect.top + tileSize / 2;
    dragOffset.x = e.clientX - dragTileCenterX;
    dragOffset.y = e.clientY - dragTileCenterY;
    dragStartX = e.clientX;
    dragStartY = e.clientY;
    dragPending = true;

    // Prevent text selection from starting
    e.preventDefault();
    e.stopPropagation();
  });

  // Mouse move (update drag ghost position)
  $(document).on("mousemove", function (e) {
    // If we have a pending drag but haven't started, check threshold (to avoid accidental clicks)
    if (dragPending && !isDraggingEntity) {
      const dx = e.clientX - dragStartX;
      const dy = e.clientY - dragStartY;
      const distance = Math.sqrt(dx * dx + dy * dy);
      if (distance > 5) {
        // Start the actual drag now
        isDraggingEntity = true;

        // Create drag ghost element centered on the tile
        createDragGhost(draggedEntityTile, dragTileCenterX, dragTileCenterY);

        // Add dragging class for visual feedback
        draggedEntityTile.addClass('entity-dragging');
        $('body').addClass('entity-dragging-active');

        // Hide popover menus during drag
        $('.popover-menu').hide();
      }
    }

    if (!isDraggingEntity || !dragGhost) return;

    // Update ghost position - center the ghost on the mouse cursor
    const tileSize = $(".tiles-container").data("tile-size");
    dragGhost.css({
      left: (e.clientX - tileSize / 2) + 'px',
      top: (e.clientY - tileSize / 2) + 'px'
    });

    // Highlight target tile
    const elementBelow = document.elementFromPoint(e.clientX, e.clientY);
    const $targetTile = $(elementBelow).closest('.tile');

    // Remove previous highlight
    $('.tile').removeClass('drag-target-highlight');

    if ($targetTile.length && $targetTile[0] !== draggedEntityTile[0]) {
      $targetTile.addClass('drag-target-highlight');
    }
  });

  // Mouse up (complete drag or cancel)
  $(document).on("mouseup", function (e) {
    // Clear pending drag if not started
    if (dragPending && !isDraggingEntity) {
      dragPending = false;
      draggedEntityId = null;
      draggedEntityTile = null;
      return;
    }

    if (!isDraggingEntity) return;

    // Find the target tile
    const elementBelow = document.elementFromPoint(e.clientX, e.clientY);
    const $targetTile = $(elementBelow).closest('.tile');

    if ($targetTile.length && $targetTile[0] !== draggedEntityTile[0]) {
      // Valid drop target
      const targetX = $targetTile.data("coords-x");
      const targetY = $targetTile.data("coords-y");

      if (targetX !== undefined && targetY !== undefined) {
        // Perform the move via API
        moveEntityTo(draggedEntityId, targetX, targetY);
      }
    }

    // Clean up drag state
    cleanupDrag();
  });

  // Cancel drag on escape key
  $(document).on("keydown", function (e) {
    if (e.key === "Escape" && isDraggingEntity) {
      cleanupDrag();
    }
  });

  // Create drag ghost element
  function createDragGhost($sourceTile, x, y) {
    const $entity = $sourceTile.find('.entity, .npc').first();
    if (!$entity.length) return;

    const tileSize = $(".tiles-container").data("tile-size");

    dragGhost = $('<div>')
      .addClass('entity-drag-ghost')
      .css({
        position: 'fixed',
        left: (x - tileSize / 2) + 'px',
        top: (y - tileSize / 2) + 'px',
        width: tileSize + 'px',
        height: tileSize + 'px',
        zIndex: 10000,
        pointerEvents: 'none',
        opacity: 0.7,
        border: '2px solid #007bff',
        borderRadius: '4px',
        backgroundColor: 'rgba(0, 123, 255, 0.1)'
      });

    // Clone the entity image
    const $entityClone = $entity.clone();
    $entityClone.css({
      position: 'relative',
      top: '0',
      left: '0',
      transform: 'none'
    });

    dragGhost.append($entityClone);
    $('body').append(dragGhost);
  }

  // Clean up drag state
  function cleanupDrag() {
    isDraggingEntity = false;
    dragPending = false;
    draggedEntityId = null;

    if (draggedEntityTile) {
      draggedEntityTile.removeClass('entity-dragging');
      draggedEntityTile = null;
    }

    if (dragGhost) {
      dragGhost.remove();
      dragGhost = null;
    }

    // Remove target highlights and global dragging class
    $('.tile').removeClass('drag-target-highlight');
    $('body').removeClass('entity-dragging-active');
  }

  Chat.init();

  // Make dialog panel draggable and resizable
  makeDialogPanelDraggable();
  makeDialogPanelResizable();

  // Make floating windows draggable
  makeFloatingWindowsDraggable();

  // Handle window resize to keep panel in bounds

  // Clean up ghost tokens on page unload
  $(window).on('beforeunload', function () {
    cleanupAllPendingMoves();
  });
});

// Function to make dialog panel draggable
function makeDialogPanelDraggable() {
  const $panel = $('#jrpgDialogPanel');
  const $header = $panel.find('.dialog-panel-header');
  let isDragging = false;
  let startX, startY, startLeft, startTop;

  $header.on('mousedown', function (e) {
    if (e.target.tagName === 'BUTTON' || $(e.target).closest('button').length) {
      return; // Don't drag if clicking on buttons
    }

    isDragging = true;
    startX = e.clientX;
    startY = e.clientY;
    startLeft = parseInt($panel.css('left'));
    startTop = parseInt($panel.css('top'));

    $panel.css('cursor', 'grabbing');
    e.preventDefault();
  });

  $(document).on('mousemove', function (e) {
    if (!isDragging) return;

    const deltaX = e.clientX - startX;
    const deltaY = e.clientY - startY;

    const newLeft = startLeft + deltaX;
    const newTop = startTop + deltaY;

    // Keep panel within window bounds
    const maxLeft = window.innerWidth - $panel.outerWidth();
    const maxTop = window.innerHeight - $panel.outerHeight();

    $panel.css({
      left: Math.max(0, Math.min(newLeft, maxLeft)) + 'px',
      top: Math.max(0, Math.min(newTop, maxTop)) + 'px'
    });
  });

  $(document).on('mouseup', function () {
    if (isDragging) {
      isDragging = false;
      $panel.css('cursor', 'default');
    }
  });
}

// Function to make dialog panel resizable
function makeDialogPanelResizable() {
  const $panel = $('#jrpgDialogPanel');
  const $resizeHandle = $panel.find('.dialog-resize-handle');
  let isResizing = false;
  let startX, startY, startWidth, startHeight;

  $resizeHandle.on('mousedown', function (e) {
    isResizing = true;
    startX = e.clientX;
    startY = e.clientY;
    startWidth = $panel.outerWidth();
    startHeight = $panel.outerHeight();

    e.preventDefault();
    e.stopPropagation();
  });

  $(document).on('mousemove', function (e) {
    if (!isResizing) return;

    const deltaX = e.clientX - startX;
    const deltaY = e.clientY - startY;

    const newWidth = startWidth + deltaX;
    const newHeight = startHeight + deltaY;

    // Minimum size constraints
    const minWidth = 400;
    const minHeight = 300;

    // Maximum size constraints (keep within window)
    const maxWidth = window.innerWidth - parseInt($panel.css('left'));
    const maxHeight = window.innerHeight - parseInt($panel.css('top'));

    const finalWidth = Math.max(minWidth, Math.min(newWidth, maxWidth));
    const finalHeight = Math.max(minHeight, Math.min(newHeight, maxHeight));

    $panel.css({
      width: finalWidth + 'px',
      height: finalHeight + 'px'
    });
  });

  $(document).on('mouseup', function () {
    if (isResizing) {
      isResizing = false;
    }
  });
}

// Function to make floating windows draggable
function makeFloatingWindowsDraggable() {
  $('.floating-window').each(function () {
    const $panel = $(this);
    const $header = $panel.find('.header');
    let isDragging = false;
    let startX, startY, startLeft, startTop;

    $header.on('mousedown', function (e) {
      if (e.target.tagName === 'BUTTON' || $(e.target).closest('button').length) {
        return; // Don't drag if clicking on buttons
      }

      isDragging = true;
      startX = e.clientX;
      startY = e.clientY;
      startLeft = parseInt($panel.css('left')) || 0;
      startTop = parseInt($panel.css('top')) || 0;

      $panel.css('cursor', 'grabbing');
      e.preventDefault();
    });

    $(document).on('mousemove', function (e) {
      if (!isDragging) return;

      const deltaX = e.clientX - startX;
      const deltaY = e.clientY - startY;

      const newLeft = startLeft + deltaX;
      const newTop = startTop + deltaY;

      // Keep panel within window bounds
      const maxLeft = window.innerWidth - $panel.outerWidth();
      const maxTop = window.innerHeight - $panel.outerHeight();

      $panel.css({
        left: Math.max(0, Math.min(newLeft, maxLeft)) + 'px',
        top: Math.max(0, Math.min(newTop, maxTop)) + 'px'
      });
    });

    $(document).on('mouseup', function () {
      if (isDragging) {
        isDragging = false;
        $panel.css('cursor', 'default');
      }
    });
  });

  // Global WSAD/Arrow key handler for direct movement on focused entity
  $(document).on('keydown', function(event) {
    const key = event.key;

    // Handle Space / Enter to commit movement
    if ((key === ' ' || key === 'Enter' || key === 'Spacebar') && moveMode) {
      event.preventDefault();
      moveMode = false;
      clearMovePathCache();
      globalCtx.clearRect(0, 0, globalCanvas.width, globalCanvas.height);
      $(".tile").css("border", "none");
      currentPosition = null;
      lastHoverCoords = null;
      if (moveModeCallback) {
        moveModeCallback(movePath);
        movePath = [];
      }
      return;
    }

    // Handle WSAD/Arrow keys for direct movement
    if (
      !isTypingIntoField(event.target) &&
      ['w', 'a', 's', 'd', 'W', 'A', 'S', 'D', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(key)
    ) {
      handleDirectWSADMovement(event);
    }

    // Handle Escape to cancel movement
    if (key === 'Escape' && moveMode) {
      event.preventDefault();
      moveMode = false;
      movePath = [];
      accumulatedPath = [];
      pivotPoints = [];
      source = null;
      currentPosition = null;
      lastHoverCoords = null;
      clearMovePathCache();
      globalCtx.clearRect(0, 0, globalCanvas.width, globalCanvas.height);
      $(".tile").css("border", "none");
    }
  });

  // Show/hide the movement keybinding hint while in any movement mode.
  const $movementHint = $('#movement-mode-hint');
  if ($movementHint.length) {
    setInterval(function () {
      const active = (typeof moveMode !== 'undefined' && moveMode) ||
                     (typeof keyboardMovementMode !== 'undefined' && keyboardMovementMode);
      const visible = $movementHint.is(':visible');
      if (active && !visible) {
        $movementHint.show();
      } else if (!active && visible) {
        $movementHint.hide();
      }
    }, 120);
  }

  try {
    if (window.JournalNotifications) {
      JournalNotifications.refresh();
      $('#open-journal-btn').on('click', function (ev) {
        ev.preventDefault();
        JournalNotifications.openFirstUnread();
      });
      setInterval(function () {
        JournalNotifications.refresh();
      }, 120000);
    }
  } catch (e) { console.warn('Journal notifications unavailable', e); }
}
