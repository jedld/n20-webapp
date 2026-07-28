/* Map landmark authoring in edit mode — points, rectangles, polygons. */

(function (window, $) {
  'use strict';

  const state = {
    mode: null,
    points: [],
    rectStart: null,
    annotations: [],
  };

  let dragHandle = null;
  let dragGhost = null;
  let dragSourceTile = null;
  let currentDropTarget = null;

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
    $('.tile').removeClass('map-annotation-draft-tile');
  }

  function renderPersistedAnnotations(annotations) {
    clearDraftGraphics();
    state.annotations = annotations || [];
    (annotations || []).forEach((ann) => {
      if (ann.kind === 'point' && ann.pos) {
        markTile(ann.pos[0], ann.pos[1], ann.label || ann.id, 'point', null, ann);
      } else if (ann.kind === 'area' && ann.bounds) {
        const b = ann.bounds;
        for (let y = b.y1; y <= b.y2; y += 1) {
          for (let x = b.x1; x <= b.x2; x += 1) {
            markTile(x, y, null, 'area', 0.35);
          }
        }
        markTile(b.x1, b.y1, ann.label || ann.id, 'area-label', null, ann);
      } else if (ann.kind === 'polygon' && ann.points) {
        ann.points.forEach((pt) => markTile(pt[0], pt[1], null, 'polygon', 0.45));
        if (ann.points.length) {
          markTile(ann.points[0][0], ann.points[0][1], ann.label || ann.id, 'polygon-label', null, ann);
        }
      }
    });
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
    const isDraggableLabel =
      annotation &&
      annotation.id &&
      (kind === 'point' || kind === 'area-label' || kind === 'polygon-label');
    if (isDraggableLabel) {
      $marker
        .addClass('map-annotation-drag-handle')
        .attr('title', 'Hold Shift and drag to move landmark')
        .data('annotation-id', annotation.id)
        .data('annotation-x', x)
        .data('annotation-y', y);
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

  function promptAnnotationMeta(defaults, onSave) {
    const idDefault = defaults.id || slugify(defaults.label) || 'landmark';
    const id = window.prompt('Landmark id (slug, used in YAML and [MOVE: target=...]):', idDefault);
    if (!id) {
      return;
    }
    const label = window.prompt('Display label:', defaults.label || id);
    if (!label) {
      return;
    }
    const description = window.prompt('Description for NPC/DM LLM (optional):', defaults.description || '') || '';
    onSave({ id: slugify(id), label, description });
  }

  function saveAnnotation(annotation) {
    const payload = { annotation };
    if (annotation.kind === 'point') {
      payload.annotation.pos = annotation.pos;
    } else if (annotation.kind === 'area') {
      payload.annotation.bounds = annotation.bounds;
    } else if (annotation.kind === 'polygon') {
      payload.annotation.points = annotation.points;
    } else if (annotation.kind === 'object_ref') {
      payload.annotation.entity_uid = annotation.entity_uid;
    }

    $.ajax({
      url: '/edit/annotations',
      method: 'POST',
      contentType: 'application/json',
      data: JSON.stringify(payload),
      success(resp) {
        if (resp && resp.success) {
          cancelMode();
          renderPersistedAnnotations((state.annotations || []).filter((a) => a.id !== resp.annotation.id).concat([resp.annotation]));
          if (window.EditMode && typeof window.EditMode.reloadOverlay === 'function') {
            window.EditMode.reloadOverlay();
          }
        } else {
          window.alert((resp && resp.error) || 'Failed to save landmark');
        }
      },
      error(xhr) {
        const msg = (xhr.responseJSON && xhr.responseJSON.error) || 'Failed to save landmark';
        window.alert(msg);
      },
    });
  }

  function cancelMode() {
    state.mode = null;
    state.points = [];
    state.rectStart = null;
    clearDraftGraphics();
    renderPersistedAnnotations(state.annotations);
    $('body').removeClass('map-annotation-mode-point map-annotation-mode-rect map-annotation-mode-polygon');
    $('.map-annotation-toolbar .active').removeClass('active');
  }

  function startMode(mode) {
    cancelMode();
    state.mode = mode;
    $('body').addClass(`map-annotation-mode-${mode}`);
    $(`.map-annotation-toolbar [data-annotation-mode="${mode}"]`).addClass('active');
  }

  function handleTileClick(event) {
    if (!state.mode) {
      return;
    }
    const tile = tileFromEvent(event);
    if (!tile) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();

    if (state.mode === 'point') {
      promptAnnotationMeta({ label: `Landmark ${tile.x},${tile.y}` }, (meta) => {
        saveAnnotation({
          kind: 'point',
          id: meta.id,
          label: meta.label,
          description: meta.description,
          pos: [tile.x, tile.y],
        });
      });
      return;
    }

    if (state.mode === 'rect') {
      if (!state.rectStart) {
        state.rectStart = { x: tile.x, y: tile.y };
        markTile(tile.x, tile.y, 'corner 1', 'rect');
        return;
      }
      const x1 = Math.min(state.rectStart.x, tile.x);
      const y1 = Math.min(state.rectStart.y, tile.y);
      const x2 = Math.max(state.rectStart.x, tile.x);
      const y2 = Math.max(state.rectStart.y, tile.y);
      promptAnnotationMeta({ label: 'Area landmark' }, (meta) => {
        saveAnnotation({
          kind: 'area',
          id: meta.id,
          label: meta.label,
          description: meta.description,
          bounds: { x1, y1, x2, y2 },
        });
      });
      return;
    }

    if (state.mode === 'polygon') {
      state.points.push([tile.x, tile.y]);
      markTile(tile.x, tile.y, String(state.points.length), 'polygon');
    }
  }

  function finishPolygon() {
    if (state.mode !== 'polygon' || state.points.length < 3) {
      window.alert('Add at least 3 vertices before finishing the polygon.');
      return;
    }
    const points = state.points.slice();
    promptAnnotationMeta({ label: 'Polygon landmark' }, (meta) => {
      saveAnnotation({
        kind: 'polygon',
        id: meta.id,
        label: meta.label,
        description: meta.description,
        points,
      });
    });
  }

  function ensureToolbar() {
    if ($('.map-annotation-toolbar').length) {
      return;
    }
    const $toolbar = $(`
      <div class="map-annotation-toolbar" role="toolbar" aria-label="Map landmarks">
        <span class="map-annotation-toolbar-title">Landmarks</span>
        <span class="map-annotation-toolbar-hint">Shift+drag labels to move</span>
        <button type="button" class="btn btn-default btn-xs" data-annotation-mode="point">Point</button>
        <button type="button" class="btn btn-default btn-xs" data-annotation-mode="rect">Rectangle</button>
        <button type="button" class="btn btn-default btn-xs" data-annotation-mode="polygon">Polygon</button>
        <button type="button" class="btn btn-warning btn-xs map-annotation-finish-polygon" style="display:none;">Finish polygon</button>
        <button type="button" class="btn btn-link btn-xs map-annotation-cancel">Cancel</button>
      </div>
    `);
    $('.edit-mode-banner').append($toolbar);

    $toolbar.on('click', '[data-annotation-mode]', function () {
      startMode($(this).data('annotation-mode'));
      $toolbar.find('.map-annotation-finish-polygon').toggle(state.mode === 'polygon');
    });
    $toolbar.on('click', '.map-annotation-finish-polygon', finishPolygon);
    $toolbar.on('click', '.map-annotation-cancel', cancelMode);
  }

  function ensureStyles() {
    if ($('#map-annotation-styles').length) {
      return;
    }
    $('head').append(`
      <style id="map-annotation-styles">
        .map-annotation-toolbar { margin-top: 6px; display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
        .map-annotation-toolbar .active { background: #337ab7; color: #fff; }
        .map-annotation-marker {
          position: absolute; inset: 2px; border: 2px dashed #f1c40f; pointer-events: none;
          font-size: 10px; color: #f1c40f; display: flex; align-items: flex-start; justify-content: center;
          background: rgba(241, 196, 15, 0.12); z-index: 6;
        }
        .map-annotation-drag-handle {
          pointer-events: auto; cursor: grab;
        }
        body.edit-mode-shift-armed .map-annotation-drag-handle {
          outline: 1px dashed rgba(255, 255, 255, 0.65);
        }
        .map-annotation-drag-handle-dragging { cursor: grabbing; }
        .map-annotation-drag-source { outline: 2px solid #f1c40f; }
        .map-annotation-drop-target { outline: 2px solid #2ecc71 !important; }
        .map-annotation-drag-ghost {
          display: flex; flex-direction: column; align-items: center; justify-content: center;
          pointer-events: none;
        }
        .map-annotation-drag-ghost-tile {
          border: 2px dashed #f1c40f; background: rgba(241, 196, 15, 0.25);
        }
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

  function init() {
    if (!isEditMode()) {
      return;
    }
    ensureStyles();
    ensureToolbar();
    bindTileClicks();
    bindDragHandles();
    $.getJSON('/edit/annotations', (resp) => {
      if (resp && resp.success) {
        renderPersistedAnnotations(resp.annotations || []);
      }
    });
  }

  window.MapAnnotationsEdit = {
    init,
    render: renderPersistedAnnotations,
    cancelMode,
  };

  $(function () {
    init();
  });
})(window, jQuery);
