/* Map landmark authoring in edit mode — points, rectangles, polygons. */

(function (window, $) {
  'use strict';

  const KIND_LABELS = {
    point: 'Point',
    area: 'Rectangle',
    polygon: 'Polygon',
    object_ref: 'Object',
  };

  const state = {
    mode: null,
    points: [],
    rectStart: null,
    annotations: [],
    selectedId: null,
    pendingDraft: null,
  };

  let dragHandle = null;
  let dragGhost = null;
  let dragSourceTile = null;
  let currentDropTarget = null;
  let $panel = null;

  function isEditMode() {
    return String($('body').data('edit-mode')).toLowerCase() === 'true';
  }

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

  function tileAtClient(clientX, clientY) {
    const hideGhost = dragGhost && dragGhost.length;
    if (hideGhost) {
      dragGhost.css('visibility', 'hidden');
    }

    let hit = null;
    try {
      const elementBelow = document.elementFromPoint(clientX, clientY);
      hit = tileCoordsFromElement($(elementBelow).closest('.tile'));
      if (hit) {
        return hit;
      }

      $('.tiles-container .tile').each(function () {
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

  function annotationById(annotationId) {
    return (state.annotations || []).find((entry) => entry.id === annotationId) || null;
  }

  function setStatus(message) {
    if ($panel && $panel.length) {
      $panel.find('.map-annotations-panel-status').text(message || '');
    }
  }

  function updateModeUi() {
    if (!$panel || !$panel.length) {
      return;
    }
    $panel.find('[data-annotation-mode]').removeClass('active');
    $panel.find('.map-annotation-finish-polygon').toggle(state.mode === 'polygon');
    if (state.mode) {
      $panel.find(`[data-annotation-mode="${state.mode}"]`).addClass('active');
    }
    if (state.mode === 'point') {
      setStatus('Click a map tile to place a point landmark.');
    } else if (state.mode === 'rect') {
      setStatus(
        state.rectStart
          ? 'Click the opposite corner to finish the rectangle.'
          : 'Click the first corner of the rectangle.',
      );
    } else if (state.mode === 'polygon') {
      setStatus(
        `Click vertices on the map (${state.points.length} placed). Press Finish when done.`,
      );
    } else {
      setStatus('Add a landmark or click one in the list to edit.');
    }
  }

  function persistAnnotationMove(annotationId, x, y) {
    $.ajax({
      url: '/edit/annotations/move',
      method: 'POST',
      contentType: 'application/json',
      data: JSON.stringify({ id: annotationId, x, y }),
      success(resp) {
        if (resp && resp.success && resp.annotation) {
          const next = (state.annotations || []).filter((a) => a.id !== resp.annotation.id);
          next.push(resp.annotation);
          renderPersistedAnnotations(next);
          if (window.EditMode && typeof window.EditMode.reloadOverlay === 'function') {
            window.EditMode.reloadOverlay();
          }
        } else {
          window.alert((resp && resp.error) || 'Failed to move landmark');
          renderPersistedAnnotations(state.annotations);
        }
      },
      error(xhr) {
        const msg = (xhr.responseJSON && xhr.responseJSON.error) || 'Failed to move landmark';
        window.alert(msg);
        renderPersistedAnnotations(state.annotations);
      },
    });
  }

  function createDragGhost($handle, clientX, clientY) {
    const size = tileSize();
    const label = $handle.text();
    dragGhost = $('<div class="map-annotation-drag-ghost" aria-hidden="true"></div>');
    dragGhost.append(
      $('<div class="map-annotation-drag-ghost-tile"></div>').css({
        width: `${size}px`,
        height: `${size}px`,
      }),
      $('<span class="map-annotation-drag-ghost-label"></span>').text(label),
    );
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
    $('.tile').removeClass('map-annotation-drop-target');
    currentDropTarget = null;
    const target = tileAtClient(clientX, clientY);
    if (!target) {
      return;
    }
    const $targetTile = $(`.tile[data-coords-x="${target.x}"][data-coords-y="${target.y}"]`);
    if ($targetTile.length && (!dragSourceTile || $targetTile[0] !== dragSourceTile[0])) {
      $targetTile.addClass('map-annotation-drop-target');
      currentDropTarget = target;
    }
  }

  function cleanupHandleDrag() {
    if (dragHandle) {
      dragHandle.removeClass('map-annotation-drag-handle-dragging map-annotation-drag-handle-source');
    }
    if (dragSourceTile) {
      dragSourceTile.removeClass('map-annotation-drag-source');
      dragSourceTile = null;
    }
    if (dragGhost) {
      dragGhost.remove();
      dragGhost = null;
    }
    $('.tile').removeClass('map-annotation-drop-target');
    currentDropTarget = null;
    dragHandle = null;
    $('body').removeClass('map-annotation-dragging');
  }

  function finishHandleDrag($handle, clientX, clientY) {
    const sourceX = Number($handle.data('annotation-x'));
    const sourceY = Number($handle.data('annotation-y'));
    const annotationId = String($handle.data('annotation-id') || '');
    const target = currentDropTarget || tileAtClient(clientX, clientY);
    if (!annotationId || !target || (target.x === sourceX && target.y === sourceY)) {
      return;
    }
    persistAnnotationMove(annotationId, target.x, target.y);
  }

  function beginHandleDrag($handle, e) {
    dragHandle = $handle;
    dragSourceTile = $handle.closest('.tile');
    dragSourceTile.addClass('map-annotation-drag-source');
    $handle.addClass('map-annotation-drag-handle-source map-annotation-drag-handle-dragging');
    createDragGhost($handle, e.clientX, e.clientY);
    $('body').addClass('map-annotation-dragging');
    e.preventDefault();
    e.stopPropagation();
    if (typeof e.stopImmediatePropagation === 'function') {
      e.stopImmediatePropagation();
    }
  }

  function bindDragHandles() {
    document.addEventListener(
      'mousedown',
      function (e) {
        if (!isEditMode() || e.button !== 0 || !e.shiftKey || state.mode) {
          return;
        }
        const $handle = $(e.target).closest('.map-annotation-drag-handle');
        if (!$handle.length) {
          return;
        }
        beginHandleDrag($handle, e);
      },
      true,
    );

    $(document)
      .off('mousemove.mapAnnotationDrag mouseup.mapAnnotationDrag')
      .on('mousemove.mapAnnotationDrag', function (e) {
        if (!dragHandle) {
          return;
        }
        updateDragGhostPosition(e.clientX, e.clientY);
        updateDropTargetHighlight(e.clientX, e.clientY);
      })
      .on('mouseup.mapAnnotationDrag', function (e) {
        if (!dragHandle) {
          return;
        }
        finishHandleDrag(dragHandle, e.clientX, e.clientY);
        cleanupHandleDrag();
      });

    $(document)
      .off('click.mapAnnotationSelect', '.map-annotation-drag-handle')
      .on('click.mapAnnotationSelect', '.map-annotation-drag-handle', function (e) {
        if (!isEditMode() || e.shiftKey || state.mode) {
          return;
        }
        e.preventDefault();
        e.stopPropagation();
        const annotationId = String($(this).data('annotation-id') || '');
        const ann = annotationById(annotationId);
        if (ann) {
          selectAnnotation(annotationId);
          openEditorModal(ann, { isNew: false });
        }
      });
  }

  function tileSize() {
    return Number($('.tiles-container').data('tile-size')) || 70;
  }

  function tileFromEvent(event) {
    const $tile = $(event.target).closest('.tile');
    if (!$tile.length) {
      return null;
    }
    return {
      x: Number($tile.data('coords-x')),
      y: Number($tile.data('coords-y')),
      $tile,
    };
  }

  function clearDraftGraphics() {
    $('.map-annotation-draft, .map-annotation-marker, .map-annotation-polygon').remove();
    $('.tile').removeClass('map-annotation-draft-tile map-annotation-selected-tile');
  }

  function selectAnnotation(annotationId) {
    state.selectedId = annotationId || null;
    $('.map-annotation-marker').removeClass('map-annotation-marker-selected');
    $('.tile').removeClass('map-annotation-selected-tile');
    if (!annotationId) {
      renderList();
      return;
    }
    const ann = annotationById(annotationId);
    if (!ann) {
      renderList();
      return;
    }
    if (ann.kind === 'point' && ann.pos) {
      highlightAnnotationTiles([[ann.pos[0], ann.pos[1]]], true);
    } else if (ann.kind === 'area' && ann.bounds) {
      const tiles = [];
      const b = ann.bounds;
      for (let y = b.y1; y <= b.y2; y += 1) {
        for (let x = b.x1; x <= b.x2; x += 1) {
          tiles.push([x, y]);
        }
      }
      highlightAnnotationTiles(tiles, true);
    } else if (ann.kind === 'polygon' && ann.points) {
      highlightAnnotationTiles(ann.points, true);
    }
    renderList();
  }

  function highlightAnnotationTiles(coords, selected) {
    coords.forEach((pt) => {
      const $tile = $(`.tile[data-coords-x="${pt[0]}"][data-coords-y="${pt[1]}"]`);
      if (selected) {
        $tile.addClass('map-annotation-selected-tile');
      }
      $tile.find('.map-annotation-marker').last().addClass('map-annotation-marker-selected');
    });
  }

  function renderPersistedAnnotations(annotations) {
    const selected = state.selectedId;
    clearDraftGraphics();
    state.annotations = annotations || [];
    (annotations || []).forEach((ann) => {
      if (ann.kind === 'point' && ann.pos) {
        markTile(ann.pos[0], ann.pos[1], ann.label || ann.id, 'point', null, ann);
      } else if (ann.kind === 'area' && ann.bounds) {
        const b = ann.bounds;
        for (let y = b.y1; y <= b.y2; y += 1) {
          for (let x = b.x1; x <= b.x2; x += 1) {
            markTile(x, y, null, 'area', 0.35, ann);
          }
        }
        markTile(b.x1, b.y1, ann.label || ann.id, 'area-label', null, ann);
      } else if (ann.kind === 'polygon' && ann.points) {
        ann.points.forEach((pt) => markTile(pt[0], pt[1], null, 'polygon', 0.45, ann));
        if (ann.points.length) {
          markTile(
            ann.points[0][0],
            ann.points[0][1],
            ann.label || ann.id,
            'polygon-label',
            null,
            ann,
          );
        }
      }
    });
    if (selected) {
      selectAnnotation(selected);
    } else {
      renderList();
    }
  }

  function markTile(x, y, label, kind, opacity, annotation) {
    const $tile = $(`.tile[data-coords-x="${x}"][data-coords-y="${y}"]`);
    if (!$tile.length) {
      return;
    }
    $tile.addClass('map-annotation-draft-tile');
    const $marker = $('<div class="map-annotation-marker"></div>');
    $marker.addClass(`map-annotation-marker-${kind}`);
    if (opacity) {
      $marker.css('opacity', opacity);
    }
    if (label) {
      $marker.text(label);
    }
    const isInteractiveLabel =
      annotation &&
      annotation.id &&
      (kind === 'point' || kind === 'area-label' || kind === 'polygon-label');
    if (isInteractiveLabel) {
      $marker
        .addClass('map-annotation-drag-handle')
        .attr('title', 'Click to edit · Shift+drag to move')
        .data('annotation-id', annotation.id)
        .data('annotation-x', x)
        .data('annotation-y', y);
      if (state.selectedId === annotation.id) {
        $marker.addClass('map-annotation-marker-selected');
        $tile.addClass('map-annotation-selected-tile');
      }
    }
    $tile.append($marker);
  }

  function slugify(text) {
    return String(text || '')
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '_')
      .replace(/^_+|_+$/g, '')
      .slice(0, 64);
  }

  function ensureEditorModal() {
    if ($('#map-annotation-editor-modal').length) {
      return;
    }
    const $modal = $(`
      <div id="map-annotation-editor-modal" class="modal fade" tabindex="-1" role="dialog" aria-labelledby="map-annotation-editor-title">
        <div class="modal-dialog" role="document">
          <div class="modal-content">
            <div class="modal-header">
              <button type="button" class="close" data-dismiss="modal" aria-label="Close"><span aria-hidden="true">&times;</span></button>
              <h4 class="modal-title" id="map-annotation-editor-title">Landmark</h4>
            </div>
            <div class="modal-body">
              <p class="map-annotation-editor-kind text-muted"></p>
              <div class="form-group">
                <label for="map-annotation-editor-id">Id (slug)</label>
                <input type="text" class="form-control" id="map-annotation-editor-id" placeholder="behind_bar">
                <p class="help-block">Used in YAML and NPC <code>[MOVE: target=...]</code> commands.</p>
              </div>
              <div class="form-group">
                <label for="map-annotation-editor-label">Label</label>
                <input type="text" class="form-control" id="map-annotation-editor-label" placeholder="Behind the bar">
              </div>
              <div class="form-group">
                <label for="map-annotation-editor-description">Description</label>
                <textarea class="form-control" id="map-annotation-editor-description" rows="3" placeholder="NPC/DM context (optional)"></textarea>
              </div>
              <p class="text-danger map-annotation-editor-error" style="display:none;"></p>
            </div>
            <div class="modal-footer">
              <button type="button" class="btn btn-danger pull-left map-annotation-editor-delete" style="display:none;">Delete</button>
              <button type="button" class="btn btn-default" data-dismiss="modal">Cancel</button>
              <button type="button" class="btn btn-primary map-annotation-editor-save">Save landmark</button>
            </div>
          </div>
        </div>
      </div>
    `);
    $('body').append($modal);
    $modal.on('hidden.bs.modal', function () {
      state.pendingDraft = null;
      $modal.find('.map-annotation-editor-error').hide().text('');
    });
    $modal.find('.map-annotation-editor-save').on('click', submitEditorModal);
    $modal.find('.map-annotation-editor-delete').on('click', function () {
      const id = String($('#map-annotation-editor-id').val() || '').trim();
      if (id) {
        deleteAnnotation(id, true);
      }
    });
  }

  function openEditorModal(annotation, options) {
    ensureEditorModal();
    const isNew = !!(options && options.isNew);
    const draft = options && options.draft ? options.draft : annotation;
    state.pendingDraft = draft;
    const $modal = $('#map-annotation-editor-modal');
    $('#map-annotation-editor-title').text(isNew ? 'New landmark' : 'Edit landmark');
    $modal.find('.map-annotation-editor-kind').text(
      `Type: ${KIND_LABELS[draft.kind] || draft.kind}`,
    );
    const idDefault = draft.id || slugify(draft.label) || 'landmark';
    $('#map-annotation-editor-id').val(idDefault).prop('readonly', !isNew);
    $('#map-annotation-editor-label').val(draft.label || idDefault);
    $('#map-annotation-editor-description').val(draft.description || '');
    $modal.find('.map-annotation-editor-delete').toggle(!isNew);
    $modal.find('.map-annotation-editor-error').hide().text('');
    $modal.modal('show');
    setTimeout(() => $('#map-annotation-editor-label').trigger('focus'), 200);
  }

  function submitEditorModal() {
    const draft = state.pendingDraft;
    if (!draft) {
      return;
    }
    const id = slugify($('#map-annotation-editor-id').val());
    const label = String($('#map-annotation-editor-label').val() || '').trim();
    const description = String($('#map-annotation-editor-description').val() || '').trim();
    const $error = $('.map-annotation-editor-error');
    if (!id) {
      $error.text('Id is required (letters, numbers, underscores).').show();
      return;
    }
    if (!label) {
      $error.text('Label is required.').show();
      return;
    }
    $error.hide();
    const annotation = Object.assign({}, draft, { id, label });
    if (description) {
      annotation.description = description;
    } else {
      delete annotation.description;
    }
    saveAnnotation(annotation);
  }

  function saveAnnotation(annotation) {
    const payload = { annotation: Object.assign({}, annotation) };

    $.ajax({
      url: '/edit/annotations',
      method: 'POST',
      contentType: 'application/json',
      data: JSON.stringify(payload),
      success(resp) {
        if (resp && resp.success) {
          $('#map-annotation-editor-modal').modal('hide');
          cancelMode();
          const next = (state.annotations || [])
            .filter((a) => a.id !== resp.annotation.id)
            .concat([resp.annotation]);
          state.selectedId = resp.annotation.id;
          renderPersistedAnnotations(next);
          if (window.EditMode && typeof window.EditMode.reloadOverlay === 'function') {
            window.EditMode.reloadOverlay();
          }
        } else {
          $('.map-annotation-editor-error')
            .text((resp && resp.error) || 'Failed to save landmark')
            .show();
        }
      },
      error(xhr) {
        const msg = (xhr.responseJSON && xhr.responseJSON.error) || 'Failed to save landmark';
        if ($('#map-annotation-editor-modal').is(':visible')) {
          $('.map-annotation-editor-error').text(msg).show();
        } else {
          window.alert(msg);
        }
      },
    });
  }

  function deleteAnnotation(annotationId, fromModal) {
    const ann = annotationById(annotationId);
    const label = (ann && ann.label) || annotationId;
    if (
      !fromModal &&
      !window.confirm(`Delete landmark "${label}"? This removes it from the map YAML.`)
    ) {
      return;
    }
    if (fromModal && !window.confirm(`Delete landmark "${label}"?`)) {
      return;
    }
    $.ajax({
      url: `/edit/annotations/${encodeURIComponent(annotationId)}`,
      method: 'DELETE',
      success(resp) {
        if (resp && resp.success) {
          $('#map-annotation-editor-modal').modal('hide');
          if (state.selectedId === annotationId) {
            state.selectedId = null;
          }
          state.annotations = (state.annotations || []).filter((a) => a.id !== annotationId);
          renderPersistedAnnotations(state.annotations);
          if (window.EditMode && typeof window.EditMode.reloadOverlay === 'function') {
            window.EditMode.reloadOverlay();
          }
        } else {
          window.alert((resp && resp.error) || 'Failed to delete landmark');
        }
      },
      error(xhr) {
        const msg = (xhr.responseJSON && xhr.responseJSON.error) || 'Failed to delete landmark';
        window.alert(msg);
      },
    });
  }

  function cancelMode() {
    state.mode = null;
    state.points = [];
    state.rectStart = null;
    state.pendingDraft = null;
    clearDraftGraphics();
    renderPersistedAnnotations(state.annotations);
    $('body').removeClass('map-annotation-mode-point map-annotation-mode-rect map-annotation-mode-polygon');
    updateModeUi();
  }

  function startMode(mode) {
    cancelMode();
    state.mode = mode;
    $('body').addClass(`map-annotation-mode-${mode}`);
    updateModeUi();
  }

  function handleTileClick(event) {
    if (!state.mode) {
      return;
    }
    if ($(event.target).closest('.map-annotation-drag-handle').length) {
      return;
    }
    const tile = tileFromEvent(event);
    if (!tile) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    if (typeof event.stopImmediatePropagation === 'function') {
      event.stopImmediatePropagation();
    }

    if (state.mode === 'point') {
      openEditorModal(
        {
          kind: 'point',
          label: `Landmark ${tile.x},${tile.y}`,
          pos: [tile.x, tile.y],
        },
        { isNew: true },
      );
      return;
    }

    if (state.mode === 'rect') {
      if (!state.rectStart) {
        state.rectStart = { x: tile.x, y: tile.y };
        markTile(tile.x, tile.y, 'corner 1', 'rect');
        updateModeUi();
        return;
      }
      const x1 = Math.min(state.rectStart.x, tile.x);
      const y1 = Math.min(state.rectStart.y, tile.y);
      const x2 = Math.max(state.rectStart.x, tile.x);
      const y2 = Math.max(state.rectStart.y, tile.y);
      openEditorModal(
        {
          kind: 'area',
          label: 'Area landmark',
          bounds: { x1, y1, x2, y2 },
        },
        { isNew: true },
      );
      return;
    }

    if (state.mode === 'polygon') {
      state.points.push([tile.x, tile.y]);
      markTile(tile.x, tile.y, String(state.points.length), 'polygon');
      updateModeUi();
    }
  }

  function finishPolygon() {
    if (state.mode !== 'polygon' || state.points.length < 3) {
      window.alert('Add at least 3 vertices before finishing the polygon.');
      return;
    }
    openEditorModal(
      {
        kind: 'polygon',
        label: 'Polygon landmark',
        points: state.points.slice(),
      },
      { isNew: true },
    );
  }

  function renderList() {
    if (!$panel || !$panel.length) {
      return;
    }
    const $list = $panel.find('.map-annotations-list');
    $list.empty();
    const items = (state.annotations || []).slice().sort((a, b) => {
      return String(a.label || a.id).localeCompare(String(b.label || b.id));
    });
    if (!items.length) {
      $list.append('<li class="map-annotations-list-empty">No landmarks on this map yet.</li>');
      return;
    }
    items.forEach((ann) => {
      const $item = $('<li class="map-annotations-list-item"></li>');
      if (state.selectedId === ann.id) {
        $item.addClass('is-selected');
      }
      const $main = $('<button type="button" class="map-annotations-list-select"></button>');
      $main.append($('<span class="map-annotations-list-label"></span>').text(ann.label || ann.id));
      $main.append(
        $('<span class="map-annotations-list-meta"></span>').text(
          `${KIND_LABELS[ann.kind] || ann.kind} · ${ann.id}`,
        ),
      );
      $main.data('annotation-id', ann.id);
      const $delete = $(
        '<button type="button" class="btn btn-danger btn-xs map-annotations-list-delete" title="Delete landmark">&times;</button>',
      );
      $delete.data('annotation-id', ann.id);
      $item.append($main, $delete);
      $list.append($item);
    });
  }

  function ensurePanel() {
    if ($('#map-annotations-panel').length) {
      $panel = $('#map-annotations-panel');
      return;
    }
    $panel = $(`
      <div id="map-annotations-panel" class="map-annotations-panel" role="region" aria-label="Map landmarks">
        <div class="map-annotations-panel-header">
          <strong>Landmarks</strong>
          <button type="button" class="btn btn-link btn-xs map-annotations-panel-collapse" title="Collapse panel">−</button>
        </div>
        <div class="map-annotations-panel-body">
          <div class="map-annotations-toolbar" role="toolbar" aria-label="Add landmarks">
            <button type="button" class="btn btn-default btn-xs" data-annotation-mode="point" title="Single-tile point">+ Point</button>
            <button type="button" class="btn btn-default btn-xs" data-annotation-mode="rect" title="Drag two corners">+ Rectangle</button>
            <button type="button" class="btn btn-default btn-xs" data-annotation-mode="polygon" title="Click vertices, then Finish">+ Polygon</button>
            <button type="button" class="btn btn-warning btn-xs map-annotation-finish-polygon" style="display:none;">Finish polygon</button>
            <button type="button" class="btn btn-link btn-xs map-annotation-cancel">Cancel</button>
          </div>
          <p class="map-annotations-panel-status text-muted"></p>
          <ul class="map-annotations-list"></ul>
        </div>
      </div>
    `);
    $('body').append($panel);

    $panel.on('click', '[data-annotation-mode]', function () {
      startMode($(this).data('annotation-mode'));
    });
    $panel.on('click', '.map-annotation-finish-polygon', finishPolygon);
    $panel.on('click', '.map-annotation-cancel', cancelMode);
    $panel.on('click', '.map-annotations-list-select', function () {
      const id = String($(this).data('annotation-id') || '');
      const ann = annotationById(id);
      if (!ann) {
        return;
      }
      selectAnnotation(id);
      openEditorModal(ann, { isNew: false });
    });
    $panel.on('click', '.map-annotations-list-delete', function (e) {
      e.stopPropagation();
      deleteAnnotation(String($(this).data('annotation-id') || ''));
    });
    $panel.on('click', '.map-annotations-panel-collapse', function () {
      $panel.toggleClass('is-collapsed');
      $(this).text($panel.hasClass('is-collapsed') ? '+' : '−');
    });
  }

  function ensureStyles() {
    if ($('#map-annotation-styles').length) {
      return;
    }
    $('head').append(`
      <style id="map-annotation-styles">
        .map-annotations-panel {
          position: fixed; left: 12px; bottom: 12px; z-index: 6200;
          width: 280px; max-height: 42vh; display: flex; flex-direction: column;
          background: rgba(24, 24, 27, 0.96); color: #fafafa; border: 1px solid #52525b;
          border-radius: 8px; box-shadow: 0 8px 24px rgba(0,0,0,0.45); pointer-events: auto;
        }
        .map-annotations-panel.is-collapsed .map-annotations-panel-body { display: none; }
        .map-annotations-panel-header {
          display: flex; align-items: center; justify-content: space-between;
          padding: 8px 10px; border-bottom: 1px solid #3f3f46; font-size: 13px;
        }
        .map-annotations-panel-body { padding: 8px 10px 10px; overflow: hidden; display: flex; flex-direction: column; gap: 6px; }
        .map-annotations-panel-status { margin: 0; font-size: 11px; line-height: 1.35; min-height: 1.35em; }
        .map-annotations-toolbar { display: flex; flex-wrap: wrap; gap: 4px; align-items: center; }
        .map-annotations-toolbar .active { background: #2563eb; color: #fff; border-color: #1d4ed8; }
        .map-annotations-list {
          list-style: none; margin: 0; padding: 0; overflow-y: auto; max-height: 24vh;
          border-top: 1px solid #3f3f46; padding-top: 4px;
        }
        .map-annotations-list-empty { padding: 8px 4px; font-size: 12px; color: #a1a1aa; }
        .map-annotations-list-item {
          display: flex; align-items: stretch; gap: 4px; margin-bottom: 4px;
        }
        .map-annotations-list-select {
          flex: 1; text-align: left; background: #27272a; border: 1px solid #3f3f46;
          color: #fafafa; border-radius: 4px; padding: 6px 8px; cursor: pointer;
        }
        .map-annotations-list-select:hover { background: #3f3f46; }
        .map-annotations-list-item.is-selected .map-annotations-list-select {
          border-color: #f59e0b; box-shadow: inset 0 0 0 1px rgba(245, 158, 11, 0.35);
        }
        .map-annotations-list-label { display: block; font-size: 12px; font-weight: 600; }
        .map-annotations-list-meta { display: block; font-size: 10px; color: #a1a1aa; margin-top: 2px; }
        .map-annotations-list-delete { flex: 0 0 auto; min-width: 28px; padding: 2px 6px; }
        .map-annotation-marker {
          position: absolute; inset: 2px; border: 2px dashed #f1c40f; pointer-events: none;
          font-size: 10px; color: #f1c40f; display: flex; align-items: flex-start; justify-content: center;
          background: rgba(241, 196, 15, 0.12); z-index: 6;
        }
        .map-annotation-drag-handle { pointer-events: auto; cursor: pointer; }
        body.edit-mode-shift-armed .map-annotation-drag-handle { cursor: grab; outline: 1px dashed rgba(255,255,255,0.65); }
        .map-annotation-marker-selected { border-color: #f59e0b !important; box-shadow: 0 0 0 2px rgba(245,158,11,0.45); }
        .map-annotation-selected-tile { outline: 2px solid rgba(245, 158, 11, 0.55) !important; }
        .map-annotation-drag-handle-dragging { cursor: grabbing; }
        .map-annotation-drag-source { outline: 2px solid #f1c40f; }
        .map-annotation-drop-target { outline: 2px solid #2ecc71 !important; }
        .map-annotation-drag-ghost { display: flex; flex-direction: column; align-items: center; justify-content: center; pointer-events: none; }
        .map-annotation-drag-ghost-tile { border: 2px dashed #f1c40f; background: rgba(241, 196, 15, 0.25); }
        .map-annotation-drag-ghost-label {
          margin-top: 2px; font-size: 10px; font-weight: bold; color: #fff;
          background: rgba(0, 0, 0, 0.65); padding: 1px 4px; border-radius: 2px;
        }
        .map-annotation-marker-point { border-style: solid; border-color: #e67e22; color: #e67e22; }
        .map-annotation-marker-area, .map-annotation-marker-polygon { border-color: #9b59b6; background: rgba(155, 89, 182, 0.15); }
        .map-annotation-marker-area-label, .map-annotation-marker-polygon-label {
          border: none; background: rgba(0,0,0,0.55); color: #fff; font-weight: bold;
        }
        .map-annotation-draft-tile { outline: 1px solid rgba(241, 196, 15, 0.35); }
        body.map-annotation-mode-point .tile,
        body.map-annotation-mode-rect .tile,
        body.map-annotation-mode-polygon .tile { cursor: crosshair; }
      </style>
    `);
  }

  function bindTileClicks() {
    $(document).off('click.mapAnnotations', '.tile');
    $(document).on('click.mapAnnotations', '.tile', handleTileClick);
  }

  function loadAnnotations() {
    return $.getJSON('/edit/annotations').then((resp) => {
      if (resp && resp.success) {
        state.selectedId = null;
        renderPersistedAnnotations(resp.annotations || []);
      }
    });
  }

  function init() {
    if (!isEditMode()) {
      return;
    }
    ensureStyles();
    ensurePanel();
    bindTileClicks();
    bindDragHandles();
    updateModeUi();
    loadAnnotations();
  }

  function reload() {
    if (!isEditMode()) {
      return;
    }
    cancelMode();
    loadAnnotations();
  }

  window.MapAnnotationsEdit = {
    init,
    reload,
    render: renderPersistedAnnotations,
    cancelMode,
  };

  $(function () {
    init();
  });
})(window, jQuery);
