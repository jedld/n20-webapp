/* Campaign edit mode — label drag handles for fixtures; persists moves to campaign YAML. */

(function (window, $) {
  'use strict';

  const overlayState = {
    itemsById: {},
    itemsByRuntimeUid: {},
    terrainById: {},
    loaded: false,
  };

  let dragHandle = null;
  let dragGhost = null;
  let dragSourceTile = null;
  let currentDropTarget = null;
  let shiftHeld = false;
  let suppressNextEditClick = false;

  function isEditMode() {
    return String($('body').data('edit-mode')).toLowerCase() === 'true';
  }

  function tileSize() {
    return Number($('.tiles-container').data('tile-size')) || 70;
  }

  function isShiftDraggableItem(item) {
    if (item.source === 'inline_note') {
      return false;
    }
    return (
      item.kind === 'spawn_point' ||
      item.kind === 'object' ||
      item.kind === 'entity' ||
      item.kind === 'terrain' ||
      item.kind === 'meta'
    );
  }

  function shouldRenderFixtureLabel(item) {
    if (item.source === 'inline_note') {
      return true;
    }
    return isShiftDraggableItem(item);
  }

  function clearOverlay() {
    $('.edit-mode-layer, .edit-mode-banner').remove();
    $('.tile').removeClass(
      'edit-highlight-spawn edit-fixture-drag-source edit-has-fixture-labels edit-highlight-wall edit-highlight-door edit-highlight-teleporter edit-fixture-drop-target',
    );
    overlayState.itemsById = {};
    overlayState.itemsByRuntimeUid = {};
    overlayState.terrainById = {};
    overlayState.loaded = false;
  }

  function ensureBanner() {
    if ($('.edit-mode-banner').length) {
      return;
    }
    const $banner = $('<div class="edit-mode-banner" role="status"></div>');
    $banner.text(
      'Edit mode — drag NPCs to reposition. Use the Landmarks panel (bottom-left) to add areas and points. Click fixture or landmark labels to edit; Shift+drag to move. Changes save to campaign YAML.',
    );
    $('body').append($banner);
  }

  function updateShiftArmed() {
    $('body').toggleClass('edit-mode-shift-armed', isEditMode() && shiftHeld);
  }

  function categoryClass(category) {
    return `edit-marker-${String(category || 'object').replace(/[^a-z0-9_-]+/gi, '-')}`;
  }

  function handleClassForItem(item) {
    if (item.kind === 'spawn_point') {
      return 'edit-marker-spawn';
    }
    if (item.kind === 'terrain') {
      return 'edit-marker-terrain';
    }
    if (item.category === 'note' || item.source === 'inline_note') {
      return 'edit-marker-note';
    }
    if (item.category === 'trigger') {
      return 'edit-marker-trigger';
    }
    if (item.category === 'container') {
      return 'edit-marker-container';
    }
    if (item.category === 'trap') {
      return 'edit-marker-trap';
    }
    return categoryClass(item.category);
  }

  // Lower rank renders higher in the column; higher rank gets a larger z-index for hit-testing.
  function fixtureStackRank(item) {
    const categoryRank = {
      wall: 100,
      door: 200,
      teleporter: 300,
      trigger: 310,
      trap: 315,
      note: 320,
      container: 330,
      object: 400,
      spawn_point: 500,
    };
    const kindRank = {
      terrain: 0,
      meta: 40,
      object: 80,
      spawn_point: 120,
    };
    const layerRank = {
      base: 0,
      base_1: 5,
      base_2: 10,
    };
    const category = categoryRank[item.category] || 350;
    const kind = kindRank[item.kind] || 60;
    const layer = item.layer ? (layerRank[item.layer] || 0) : 0;
    const labelTiebreak = String(item.label || item.id || '');
    return { score: category + kind + layer, label: labelTiebreak };
  }

  function compareFixtureStack(a, b) {
    const rankA = fixtureStackRank(a);
    const rankB = fixtureStackRank(b);
    if (rankA.score !== rankB.score) {
      return rankA.score - rankB.score;
    }
    return rankA.label.localeCompare(rankB.label);
  }

  function ensureEditLayer($tile) {
    let $layer = $tile.children('.edit-mode-layer').first();
    if (!$layer.length) {
      $layer = $('<div class="edit-mode-layer" aria-hidden="true"></div>');
      $tile.append($layer);
    }
    return $layer;
  }

  function stackEditContext() {
    const meta = document.querySelector('.map-metadata[data-stack-composite="1"]');
    if (!meta) {
      return null;
    }
    const mapName = meta.getAttribute('data-stack-active-map') || meta.getAttribute('data-map-name');
    if (!mapName) {
      return null;
    }
    const anchorX = parseInt(meta.getAttribute('data-stack-anchor-x'), 10);
    const anchorY = parseInt(meta.getAttribute('data-stack-anchor-y'), 10);
    return {
      mapName,
      anchorX: Number.isFinite(anchorX) ? anchorX : 0,
      anchorY: Number.isFinite(anchorY) ? anchorY : 0,
    };
  }

  function scopedEditTiles() {
    const stack = stackEditContext();
    if (stack) {
      return $(`.map-stack-overlay[data-layer-map="${stack.mapName}"] .map-stack-tile`);
    }
    return $('.tiles-container .tile').not('.map-stack-underlay-tile');
  }

  function findEditTile(x, y) {
    const stack = stackEditContext();
    if (stack && stack.mapName) {
      const $overlay = $(`.map-stack-overlay[data-layer-map="${stack.mapName}"]`);
      if ($overlay.length) {
        let $tile = $overlay.find(
          `.map-stack-tile[data-coords-x="${x}"][data-coords-y="${y}"]`,
        );
        if ($tile.length) {
          return $tile.first();
        }

        const worldX = x + stack.anchorX;
        const worldY = y + stack.anchorY;
        $tile = $overlay.find(
          `.map-stack-tile[data-world-x="${worldX}"][data-world-y="${worldY}"]`,
        );
        if ($tile.length) {
          return $tile.first();
        }

        if (x >= stack.anchorX && y >= stack.anchorY) {
          const localX = x - stack.anchorX;
          const localY = y - stack.anchorY;
          $tile = $overlay.find(
            `.map-stack-tile[data-coords-x="${localX}"][data-coords-y="${localY}"]`,
          );
          if ($tile.length) {
            return $tile.first();
          }
        }
      }
    }
    const $tile = scopedEditTiles().filter(`[data-coords-x="${x}"][data-coords-y="${y}"]`);
    return $tile.length ? $tile.first() : $();
  }

  function createDragHandle(item, stackIndex) {
    const $handle = $('<span class="edit-mode-drag-handle" role="button"></span>');
    $handle.addClass(categoryClass(item.category));
    $handle.addClass(handleClassForItem(item));
    $handle.attr({
      'data-edit-id': item.id,
      'data-edit-kind': item.kind,
      'data-edit-token': item.token || '',
      'data-edit-layer': item.layer || '',
      'data-edit-source': item.source || '',
      'data-edit-index': item.index != null ? item.index : '',
      'data-edit-x': item.x,
      'data-edit-y': item.y,
      'data-edit-stack': stackIndex,
      'data-edit-object-type': item.object_type || '',
      title: `${item.label} (${item.x}, ${item.y}) — click to edit, Shift+drag to move`,
    });
    if (item.editable) {
      $handle.addClass('edit-mode-drag-handle-editable');
    }
    $handle.text(item.label);
    $handle.css('z-index', 10 + stackIndex);
    return $handle;
  }

  function groupFixturesByTile(fixtures) {
    const groups = {};
    fixtures.forEach((item) => {
      const key = `${item.x},${item.y}`;
      if (!groups[key]) {
        groups[key] = [];
      }
      groups[key].push(item);
    });
    return groups;
  }

  function renderTileHandles(items) {
    if (!items.length) {
      return;
    }
    const sorted = items.slice().sort(compareFixtureStack);
    const head = sorted[0];
    const $tile = findEditTile(head.x, head.y);
    if (!$tile.length) {
      return;
    }
    const $layer = ensureEditLayer($tile);
    $tile.addClass('edit-has-fixture-labels');
    sorted.forEach((item, index) => {
      $layer.append(createDragHandle(item, index));
    });
  }

  function highlightSpawnTiles() {
    Object.values(overlayState.itemsById).forEach((item) => {
      if (item.kind !== 'spawn_point') {
        return;
      }
      $(findEditTile(item.x, item.y)).addClass('edit-highlight-spawn');
    });
  }

  function highlightExistingTiles() {
    scopedEditTiles().each(function () {
      const $tile = $(this);
      const door = String($tile.attr('data-door') || '').toLowerCase() === 'true';
      const blocked = String($tile.attr('data-blocked') || '').toLowerCase() === 'true';
      if (door || $tile.find('.secret-door-marker').length) {
        $tile.addClass('edit-highlight-door');
      }
      if ($tile.find('.teleporter-marker').length) {
        $tile.addClass('edit-highlight-teleporter');
      }
      if (blocked) {
        $tile.addClass('edit-highlight-wall');
      }
    });
  }

  function edgesAreActive(edges) {
    return Boolean(
      edges && (edges.top || edges.right || edges.bottom || edges.left),
    );
  }

  function collectFixtureEdgesByTile(entries) {
    const byTile = {};
    (entries || []).forEach((item) => {
      if (!edgesAreActive(item.wall_edges) && !edgesAreActive(item.door_edges)) {
        return;
      }
      const key = `${item.x},${item.y}`;
      if (!byTile[key]) {
        byTile[key] = {
          x: item.x,
          y: item.y,
          wall_edges: {},
          door_edges: {},
        };
      }
      ['top', 'right', 'bottom', 'left'].forEach((side) => {
        if (item.wall_edges && item.wall_edges[side]) {
          byTile[key].wall_edges[side] = true;
        }
        if (item.door_edges && item.door_edges[side]) {
          byTile[key].door_edges[side] = true;
        }
      });
    });
    return Object.values(byTile);
  }

  function appendFixtureEdgeLines($container, edges, lineClass) {
    if (!edgesAreActive(edges)) {
      return;
    }
    ['top', 'right', 'bottom', 'left'].forEach((side) => {
      if (!edges[side]) {
        return;
      }
      $container.append(
        $(`<div class="edit-fixture-edge-line ${lineClass} edit-fixture-edge-${side}"></div>`),
      );
    });
  }

  function renderFixtureEdgeMarkers(entries) {
    collectFixtureEdgesByTile(entries).forEach((tileEdges) => {
      const $tile = findEditTile(tileEdges.x, tileEdges.y);
      if (!$tile.length) {
        return;
      }
      const $layer = ensureEditLayer($tile);
      const $marker = $('<div class="edit-fixture-edge-marker" aria-hidden="true"></div>');
      appendFixtureEdgeLines($marker, tileEdges.wall_edges, 'edit-fixture-wall-edge');
      appendFixtureEdgeLines($marker, tileEdges.door_edges, 'edit-fixture-door-edge');
      $layer.append($marker);
    });
  }

  function renderOverlay(overlay) {
    clearOverlay();
    ensureBanner();

    const fixtures = [];
    (overlay.items || []).forEach((item) => {
      overlayState.itemsById[item.id] = item;
      if (item.runtime_uid) {
        overlayState.itemsByRuntimeUid[item.runtime_uid] = item;
      }
      if (shouldRenderFixtureLabel(item)) {
        fixtures.push(item);
      }
    });
    (overlay.terrain || []).forEach((item) => {
      overlayState.terrainById[item.id] = item;
      fixtures.push(item);
    });

    const tileGroups = groupFixturesByTile(fixtures);
    Object.keys(tileGroups)
      .sort()
      .forEach((key) => {
        renderTileHandles(tileGroups[key]);
      });

    highlightSpawnTiles();
    highlightExistingTiles();
    renderFixtureEdgeMarkers((overlay.items || []).concat(overlay.terrain || []));
    if (window.MapAnnotationsEdit && overlay.annotations) {
      window.MapAnnotationsEdit.render(overlay.annotations);
    }
    overlayState.loaded = true;
  }

  function loadOverlay() {
    if (!isEditMode()) {
      return;
    }
    $.getJSON('/edit/overlay', function (resp) {
      if (!resp || !resp.success) {
        console.warn('Edit overlay failed', resp);
        return;
      }
      renderOverlay(resp.overlay || {});
    }).fail(function (xhr) {
      console.warn('Edit overlay request failed', xhr.status, xhr.responseText);
    });
  }

  let overlayReloadTimer = null;

  function scheduleOverlayReload(delayMs = 400) {
    if (!isEditMode()) {
      return;
    }
    if (overlayReloadTimer) {
      clearTimeout(overlayReloadTimer);
    }
    overlayReloadTimer = setTimeout(() => {
      overlayReloadTimer = null;
      loadOverlay();
    }, delayMs);
  }

  function getEditableFixturesAt(x, y) {
    const fixtures = [];
    Object.values(overlayState.itemsById).forEach((item) => {
      if (item.x === x && item.y === y && item.editable) {
        fixtures.push(item);
      }
    });
    Object.values(overlayState.terrainById).forEach((item) => {
      if (item.x === x && item.y === y && item.editable) {
        fixtures.push(item);
      }
    });
    return fixtures.slice().sort(compareFixtureStack);
  }

  function collectTileFixtures(x, y, $tile) {
    const seen = new Set();
    const fixtures = [];

    function addFixture(item) {
      if (!item) {
        return;
      }
      const key = item.id != null ? String(item.id) : `${item.kind || 'item'}:${x}:${y}:${seen.size}`;
      if (seen.has(key)) {
        return;
      }
      seen.add(key);
      fixtures.push(item);
    }

    getFixturesAt(x, y).forEach(addFixture);
    if (!fixtures.length) {
      getEditableFixturesAt(x, y).forEach(addFixture);
    }

    if ($tile && $tile.length) {
      $tile.find('.object-container[data-id]').each(function () {
        const entityUid = $(this).data('id');
        const fixture = findFixtureForEntityUid(entityUid);
        if (fixture) {
          addFixture(fixture);
        }
      });
    }

    const sorted = fixtures.slice().sort(compareFixtureStack);
    const editableOnly = sorted.filter((item) => item.editable || item.object_type);
    return editableOnly.length ? editableOnly : sorted;
  }

  function showFixturePropertiesBar(fixtures, x, y) {
    const $content = $('#centerActionBarContent');
    const $stack = $('<div class="popover-actions-stack edit-fixture-actions"></div>');
    fixtures.forEach((item) => {
      const $entry = $('<div class="popover-actions-entry center-action-entry edit-fixture-entry"></div>');
      const $header = $('<div class="popover-actions-header"></div>');
      $('<span class="popover-actions-title"></span>').text(item.label || item.id).appendTo($header);
      $('<span class="popover-actions-type"></span>')
        .text(item.category || item.kind || 'fixture')
        .appendTo($header);
      $entry.append($header);
      const $body = $('<div class="popover-actions-body"></div>');
      const $btn = $(
        '<button type="button" class="btn btn-primary btn-sm edit-fixture-properties-btn">Edit properties</button>',
      );
      $btn.data('fixture', item);
      $body.append($btn);
      const $removeBtn = $(
        '<button type="button" class="btn btn-danger btn-sm edit-fixture-remove-btn">Remove</button>',
      );
      $removeBtn.data('fixture', item);
      $body.append($removeBtn);
      $entry.append($body);
      $stack.append($entry);
    });
    $content.empty().append($stack);
    $('#centerActionBarTitle').text('Map fixtures');
    $('#centerActionBarSubtitle').text(
      `Tile (${x}, ${y}) — ${fixtures.length} editable fixture${fixtures.length === 1 ? '' : 's'}.`,
    );
    $('#actionBarCustomizeToggle, #actionBarHotkeyToggle').hide();
    $('#centerActionBarInstructions').text(
      'Use Edit properties to change map object fields. Shift+drag labels to move fixtures.',
    );
    $('#centerActionBar').show();
  }

  function openTileFixtureEditor(x, y, $tile) {
    const fixtures = collectTileFixtures(x, y, $tile);
    if (!fixtures.length) {
      return false;
    }
    const editable = fixtures.filter((item) => item.object_type && item.editable !== false);
    if (
      editable.length === 1
      && window.EditProperties
      && typeof window.EditProperties.openFromFixture === 'function'
    ) {
      window.EditProperties.openFromFixture(editable[0]);
      return true;
    }
    showFixturePropertiesBar(fixtures, x, y);
    return true;
  }

  function fixturePayloadFromItem(item) {
    return {
      item_id: String(item.id),
      kind: String(item.kind),
      token: item.token || undefined,
      layer: item.layer || undefined,
      source: item.source || undefined,
      index: item.index != null ? item.index : undefined,
      object_type: item.object_type || undefined,
      label: item.label,
      x: item.x != null ? item.x : undefined,
      y: item.y != null ? item.y : undefined,
    };
  }

  function payloadForHandle($handle) {
    const indexRaw = $handle.data('edit-index');
    return {
      item_id: String($handle.data('edit-id')),
      kind: String($handle.data('edit-kind')),
      token: $handle.data('edit-token') || undefined,
      layer: $handle.data('edit-layer') || undefined,
      source: $handle.data('edit-source') || undefined,
      index: indexRaw === '' || indexRaw == null ? undefined : Number(indexRaw),
      object_type: $handle.data('edit-object-type') || undefined,
      label: $handle.text(),
    };
  }

  function refreshTilesAfterEditChange() {
    if (window.Utils && typeof window.Utils.refreshTileSet === 'function') {
      window.Utils.refreshTileSet(false, false, 0, 0, null, null, true);
      return;
    }
    loadOverlay();
  }

  function persistMove(payload) {
    return $.ajax({
      url: '/edit/move',
      method: 'POST',
      contentType: 'application/json',
      data: JSON.stringify(payload),
    })
      .done(function (resp) {
        if (!resp || !resp.success) {
          alert('Edit save failed: ' + ((resp && resp.error) || 'unknown error'));
          loadOverlay();
          return;
        }
        refreshTilesAfterEditChange();
      })
      .fail(function (xhr) {
        let message = 'Edit save failed';
        try {
          const body = JSON.parse(xhr.responseText || '{}');
          message = body.error || message;
        } catch (err) {
          /* ignore */
        }
        alert(message);
        loadOverlay();
      });
  }

  function persistRemove(payload) {
    return $.ajax({
      url: '/edit/remove',
      method: 'POST',
      contentType: 'application/json',
      data: JSON.stringify(payload),
    })
      .done(function (resp) {
        if (!resp || !resp.success) {
          alert('Edit remove failed: ' + ((resp && resp.error) || 'unknown error'));
          loadOverlay();
          return;
        }
        $('#centerActionBar').hide();
        refreshTilesAfterEditChange();
      })
      .fail(function (xhr) {
        let message = 'Edit remove failed';
        try {
          const body = JSON.parse(xhr.responseText || '{}');
          message = body.error || message;
        } catch (err) {
          /* ignore */
        }
        alert(message);
        loadOverlay();
      });
  }

  function removeFixture(item, options) {
    if (!item) {
      return;
    }
    const label = item.label || item.id || 'this fixture';
    const skipConfirm = options && options.skipConfirm;
    if (!skipConfirm && !window.confirm(`Remove ${label} from the campaign map YAML?`)) {
      return;
    }
    persistRemove(fixturePayloadFromItem(item));
  }

  function getFixturesAt(x, y) {
    const fixtures = [];
    Object.values(overlayState.itemsById).forEach((item) => {
      if (item.x === x && item.y === y) {
        fixtures.push(item);
      }
    });
    Object.values(overlayState.terrainById).forEach((item) => {
      if (item.x === x && item.y === y) {
        fixtures.push(item);
      }
    });
    return fixtures.slice().sort(compareFixtureStack);
  }

  function findFixtureForEntityUid(entityUid) {
    if (!entityUid) {
      return null;
    }
    const $obj = $(
      `.object-container[data-id="${entityUid}"], .entity[data-id="${entityUid}"]`,
    ).first();
    const $tile = $obj.closest('.tile');
    if (!$tile.length) {
      return null;
    }
    const x = Number($tile.data('coords-x'));
    const y = Number($tile.data('coords-y'));
    if (!Number.isFinite(x) || !Number.isFinite(y)) {
      return null;
    }
    const fixtures = getFixturesAt(x, y);
    if (!fixtures.length) {
      return {
        id: `pos:${x}:${y}`,
        kind: 'terrain',
        x,
        y,
      };
    }
    const byRuntime = fixtures.find((item) => item.runtime_uid === entityUid);
    if (byRuntime) {
      return byRuntime;
    }
    const terrain = fixtures.find(
      (item) => item.kind === 'terrain' || item.source === 'layer_placements',
    );
    if (terrain) {
      return terrain;
    }
    return fixtures[0];
  }

  window.editMoveItem = function (itemId, targetX, targetY, meta) {
    const payload = Object.assign(
      {
        item_id: itemId,
        x: targetX,
        y: targetY,
      },
      meta || {},
    );
    return persistMove(payload);
  };

  function resolveEditOverlayItem(entityId) {
    if (overlayState.itemsById[entityId]) {
      return overlayState.itemsById[entityId];
    }
    if (overlayState.itemsByRuntimeUid[entityId]) {
      return overlayState.itemsByRuntimeUid[entityId];
    }
    const $sourceTile = $(`.tile[data-coords-id="${entityId}"]`);
    if (!$sourceTile.length) {
      return null;
    }
    const sx = Number($sourceTile.data('coords-x'));
    const sy = Number($sourceTile.data('coords-y'));
    return (
      Object.values(overlayState.itemsById).find(
        (entry) =>
          entry.source === 'entities' &&
          entry.kind === 'entity' &&
          Number(entry.x) === sx &&
          Number(entry.y) === sy,
      ) || null
    );
  }

  window.moveEntityInEditMode = function (entityId, targetX, targetY) {
    const item = resolveEditOverlayItem(entityId);
    const meta = item
      ? {
          kind: item.kind,
          token: item.token,
          source: item.source,
          index: item.index,
          from_x: item.x,
          from_y: item.y,
        }
      : { kind: 'entity', source: 'entities' };
    const itemId = item ? item.id : entityId;
    return window.editMoveItem(itemId, targetX, targetY, meta);
  };

  function tileCoordsFromElement($tile) {
    if (!$tile || !$tile.length) {
      return null;
    }
    const x = $tile.data('coords-x');
    const y = $tile.data('coords-y');
    if (x === undefined || y === undefined) {
      return null;
    }
    return { x: Number(x), y: Number(y) };
  }

  function tileAtClient(clientX, clientY) {
    const hideGhost = dragGhost && dragGhost.length;
    if (hideGhost) {
      dragGhost.css('visibility', 'hidden');
    }

    try {
      if (typeof window.tileCoordsFromClientPoint === 'function') {
        const resolved = window.tileCoordsFromClientPoint(clientX, clientY);
        if (resolved) {
          return { x: resolved.x, y: resolved.y, mapName: resolved.mapName || null };
        }
        return null;
      }

      const elementBelow = document.elementFromPoint(clientX, clientY);
      let hit = tileCoordsFromElement($(elementBelow).closest('.tile'));
      if (hit) {
        return hit;
      }

      scopedEditTiles().each(function () {
        const rect = this.getBoundingClientRect();
        if (
          clientX >= rect.left &&
          clientX < rect.right &&
          clientY >= rect.top &&
          clientY < rect.bottom
        ) {
          hit = tileCoordsFromElement($(this));
          return false;
        }
        return undefined;
      });
      if (hit) {
        return hit;
      }
    } finally {
      if (hideGhost) {
        dragGhost.css('visibility', '');
      }
    }

    return snapToTile(clientX, clientY);
  }

  function snapToTile(clientX, clientY) {
    const $container = $('.tiles-container');
    if (!$container.length) {
      return null;
    }
    const rect = $container[0].getBoundingClientRect();
    const sampleTile = $container.find('.tile').get(0);
    const size = sampleTile
      ? sampleTile.getBoundingClientRect().width || tileSize()
      : tileSize();
    if (!size) {
      return null;
    }
    const x = Math.floor((clientX - rect.left - size) / size);
    const y = Math.floor((clientY - rect.top - size) / size);
    if (x < 0 || y < 0) {
      return null;
    }
    return { x, y };
  }

  function finishHandleDrag($handle, clientX, clientY) {
    const sourceX = Number($handle.data('edit-x'));
    const sourceY = Number($handle.data('edit-y'));
    const target = currentDropTarget || tileAtClient(clientX, clientY);
    if (!target || (target.x === sourceX && target.y === sourceY)) {
      return;
    }
    suppressNextEditClick = true;
    const payload = payloadForHandle($handle);
    payload.x = target.x;
    payload.y = target.y;
    const stack = stackEditContext();
    if (stack) {
      payload.map_name = stack.mapName;
    } else if (target.mapName) {
      payload.map_name = target.mapName;
    }
    persistMove(payload);
  }

  function markerClassesFromHandle($handle) {
    return ($handle.attr('class') || '')
      .split(/\s+/)
      .filter((className) => className.startsWith('edit-marker-'));
  }

  function createDragGhost($handle, clientX, clientY) {
    const size = tileSize();
    const label = $handle.text();
    dragGhost = $('<div class="edit-mode-drag-ghost" aria-hidden="true"></div>');
    markerClassesFromHandle($handle).forEach((className) => {
      dragGhost.addClass(className);
    });

    const $tileOutline = $('<div class="edit-mode-drag-ghost-tile"></div>').css({
      width: `${size}px`,
      height: `${size}px`,
    });
    const $label = $('<span class="edit-mode-drag-ghost-label"></span>').text(label);
    dragGhost.append($tileOutline, $label);
    $('body').append(dragGhost);
    updateDragGhostPosition(clientX, clientY);
  }

  function updateDragGhostPosition(clientX, clientY) {
    if (!dragGhost) {
      return;
    }
    const size = tileSize();
    dragGhost.css({
      position: 'fixed',
      left: `${clientX - size / 2}px`,
      top: `${clientY - size / 2}px`,
      width: `${size}px`,
      height: `${size}px`,
      zIndex: 12050,
      pointerEvents: 'none',
    });
  }

  function updateDropTargetHighlight(clientX, clientY) {
    $('.tile').removeClass('edit-fixture-drop-target');
    currentDropTarget = null;
    const target = tileAtClient(clientX, clientY);
    if (!target) {
      return;
    }
    const $targetTile = findEditTile(target.x, target.y);
    if ($targetTile.length && (!dragSourceTile || $targetTile[0] !== dragSourceTile[0])) {
      $targetTile.addClass('edit-fixture-drop-target');
      currentDropTarget = target;
    }
  }

  function cleanupHandleDrag() {
    if (dragHandle) {
      dragHandle.removeClass('edit-mode-drag-handle-source edit-mode-drag-handle-dragging');
    }
    if (dragSourceTile) {
      dragSourceTile.removeClass('edit-fixture-drag-source');
      dragSourceTile = null;
    }
    if (dragGhost) {
      dragGhost.remove();
      dragGhost = null;
    }
    $('.tile').removeClass('edit-fixture-drop-target');
    currentDropTarget = null;
    dragHandle = null;
    $('body').removeClass('edit-mode-dragging');
  }

  function resetDragHandleStyles($handle) {
    $handle.removeClass('edit-mode-drag-handle-dragging edit-mode-drag-handle-source');
  }

  function beginHandleDrag($handle, e) {
    dragHandle = $handle;
    dragSourceTile = $handle.closest('.tile');
    dragSourceTile.addClass('edit-fixture-drag-source');
    $handle.addClass('edit-mode-drag-handle-source edit-mode-drag-handle-dragging');
    createDragGhost($handle, e.clientX, e.clientY);
    $('body').addClass('edit-mode-dragging');
    e.preventDefault();
    e.stopPropagation();
    if (typeof e.stopImmediatePropagation === 'function') {
      e.stopImmediatePropagation();
    }
  }

  // Capture phase so fixture label drags win over entity tile handlers.
  document.addEventListener(
    'mousedown',
    function (e) {
      if (!isEditMode() || e.button !== 0 || !e.shiftKey) {
        return;
      }
      const $handle = $(e.target).closest('.edit-mode-drag-handle');
      if (!$handle.length) {
        return;
      }
      beginHandleDrag($handle, e);
    },
    true,
  );

  $(document).on('click', '.edit-fixture-properties-btn', function (e) {
    e.preventDefault();
    e.stopPropagation();
    const item = $(this).data('fixture');
    if (item && window.EditProperties && typeof window.EditProperties.openFromFixture === 'function') {
      window.EditProperties.openFromFixture(item);
    }
  });

  $(document).on('click', '.edit-fixture-remove-btn', function (e) {
    e.preventDefault();
    e.stopPropagation();
    removeFixture($(this).data('fixture'));
  });

  document.addEventListener(
    'click',
    function (e) {
      if (!isEditMode() || e.button !== 0 || e.shiftKey) {
        return;
      }
      if (
        $(e.target).closest(
          '.edit-mode-drag-handle, .modal, #centerActionBar, .popover-menu, .popover-menu-2, .edit-fixture-properties-btn, .edit-fixture-remove-btn',
        ).length
      ) {
        return;
      }
      const $tile = $(e.target).closest('.tiles-container .tile');
      if (!$tile.length) {
        return;
      }
      const x = Number($tile.data('coords-x'));
      const y = Number($tile.data('coords-y'));
      if (!openTileFixtureEditor(x, y, $tile)) {
        return;
      }
      e.preventDefault();
      e.stopPropagation();
      if (typeof e.stopImmediatePropagation === 'function') {
        e.stopImmediatePropagation();
      }
    },
    true,
  );

  $(document).on('click', '.edit-mode-drag-handle-editable', function (e) {
    if (!isEditMode() || e.shiftKey || dragHandle) {
      return;
    }
    if (suppressNextEditClick) {
      suppressNextEditClick = false;
      return;
    }
    e.preventDefault();
    e.stopPropagation();
    if (window.EditProperties && typeof window.EditProperties.openFromHandle === 'function') {
      window.EditProperties.openFromHandle($(this));
    }
  });

  $(document).on('keydown', function (e) {
    if (!isEditMode() || e.key !== 'Shift') {
      return;
    }
    shiftHeld = true;
    updateShiftArmed();
  });

  $(document).on('keyup', function (e) {
    if (e.key !== 'Shift') {
      return;
    }
    shiftHeld = false;
    updateShiftArmed();
  });

  $(document).on('mousemove', function (e) {
    if (!dragHandle) {
      return;
    }
    updateDragGhostPosition(e.clientX, e.clientY);
    updateDropTargetHighlight(e.clientX, e.clientY);
  });

  $(document).on('mouseup', function (e) {
    if (!dragHandle) {
      return;
    }
    const $handle = dragHandle;
    finishHandleDrag($handle, e.clientX, e.clientY);
    resetDragHandleStyles($handle);
    cleanupHandleDrag();
  });

  $(document).on('keydown', function (e) {
    if (e.key === 'Escape' && dragHandle) {
      resetDragHandleStyles(dragHandle);
      cleanupHandleDrag();
    }
  });

  window.EditMode = {
    isEditMode,
    loadOverlay,
    clearOverlay,
    isShiftDraggableItem,
    compareFixtureStack,
    fixtureStackRank,
    tileAtClient,
    snapToTile,
    getEditableFixturesAt,
    getFixturesAt,
    collectTileFixtures,
    openTileFixtureEditor,
    showFixturePropertiesBar,
    fixturePayloadFromItem,
    removeFixture,
    findFixtureForEntityUid,
    reloadOverlay: scheduleOverlayReload,
    stackEditContext,
    findEditTile,
    scopedEditTiles,
  };

  $(function () {
    if (!isEditMode()) {
      return;
    }
    ensureBanner();
    loadOverlay();
    const originalRefresh = window.Utils && window.Utils.refreshTileSet;
    if (originalRefresh) {
      window.Utils.refreshTileSet = function (
        is_setup,
        pov,
        x,
        y,
        entity_uid,
        callback,
        forceFullRefresh,
      ) {
        const wrappedCallback = function () {
          if (typeof callback === 'function') {
            callback();
          }
          if (isEditMode() && !forceFullRefresh) {
            scheduleOverlayReload(400);
          }
        };
        return originalRefresh.call(
          this,
          is_setup,
          pov,
          x,
          y,
          entity_uid,
          wrappedCallback,
          forceFullRefresh,
        );
      };
    }
  });
})(window, jQuery);
