/* DM-only map pins — play-time notes invisible to PCs and NPCs. */

(function (window, $) {
  'use strict';

  const STORAGE_VISIBLE = 'n20.dmNotes.visible';

  const state = {
    notes: [],
    mapName: null,
    visible: true,
    placing: false,
    selectedId: null,
    loading: false,
  };

  function isDmUser() {
    const role = $('body').attr('data-role') || '';
    return String(role).toLowerCase().indexOf('dm') >= 0;
  }

  function currentMapName() {
    const meta = document.querySelector('.map-metadata');
    if (meta) {
      const stackMap = meta.getAttribute('data-stack-active-map');
      if (stackMap) {
        return stackMap;
      }
      const named = meta.getAttribute('data-map-name');
      if (named) {
        return named;
      }
    }
    return $('body').attr('data-current-map') || '';
  }

  function mapNameForTile($tile) {
    if (!$tile || !$tile.length) {
      return currentMapName();
    }
    const layer = $tile.attr('data-stack-layer-map') || $tile.data('stack-layer-map');
    if (layer) {
      return String(layer);
    }
    const $overlay = $tile.closest('.map-stack-overlay');
    if ($overlay.length && $overlay.attr('data-layer-map')) {
      return String($overlay.attr('data-layer-map'));
    }
    return currentMapName();
  }

  function tileCoords($tile) {
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

  function findNoteTile(mapName, x, y) {
    const $overlay = $(`.map-stack-overlay[data-layer-map="${mapName}"]`);
    if ($overlay.length) {
      const $overlayTile = $overlay.find(
        `.map-stack-tile[data-coords-x="${x}"][data-coords-y="${y}"]`,
      );
      if ($overlayTile.length) {
        return $overlayTile.first();
      }
    }
    const $tiles = $(`.tiles-container .tile[data-coords-x="${x}"][data-coords-y="${y}"]`)
      .not('.map-stack-underlay-tile');
    return $tiles.length ? $tiles.first() : $();
  }

  function loadVisiblePreference() {
    try {
      const saved = window.localStorage.getItem(STORAGE_VISIBLE);
      if (saved === 'false') {
        return false;
      }
    } catch (_) { }
    return true;
  }

  function persistVisible() {
    try {
      window.localStorage.setItem(STORAGE_VISIBLE, state.visible ? 'true' : 'false');
    } catch (_) { }
  }

  function setStatus(message) {
    const $status = $('.dm-notes-panel-status');
    if ($status.length) {
      $status.text(message || '');
    }
  }

  function syncMenuLabel() {
    const $item = $('#toggle-dm-notes');
    if (!$item.length) {
      return;
    }
    const label = state.visible ? 'Hide DM Notes' : 'Show DM Notes';
    if (window.Utils && typeof window.Utils.setVttMenuLabel === 'function') {
      window.Utils.setVttMenuLabel($item, label);
    } else {
      $item.text(label);
    }
  }

  function ensureStyles() {
    if ($('#dm-notes-styles').length) {
      return;
    }
    $('head').append(`
      <style id="dm-notes-styles">
        .dm-notes-panel {
          position: fixed; right: 12px; bottom: 12px; z-index: 6150;
          width: 260px; max-height: 42vh; display: flex; flex-direction: column;
          background: rgba(24, 24, 27, 0.96); color: #fafafa; border: 1px solid #52525b;
          border-radius: 8px; box-shadow: 0 8px 24px rgba(0,0,0,0.45); pointer-events: auto;
        }
        .dm-notes-panel.is-collapsed { width: auto; min-width: 148px; }
        .dm-notes-panel.is-collapsed .dm-notes-panel-body { display: none; }
        .dm-notes-panel.is-collapsed .dm-notes-panel-title small { display: none; }
        .dm-notes-panel.is-hidden { display: none !important; }
        .dm-notes-panel-header {
          display: flex; align-items: center; justify-content: space-between;
          padding: 8px 10px; border-bottom: 1px solid #3f3f46; font-size: 13px;
        }
        .dm-notes-panel-title { font-weight: 600; }
        .dm-notes-panel-title small { display: block; font-weight: 400; color: #a1a1aa; font-size: 10px; }
        .dm-notes-panel-body { padding: 8px 10px 10px; overflow: hidden; display: flex; flex-direction: column; gap: 6px; }
        .dm-notes-panel-status { margin: 0; font-size: 11px; line-height: 1.35; min-height: 1.35em; color: #a1a1aa; }
        .dm-notes-toolbar { display: flex; flex-wrap: wrap; gap: 4px; align-items: center; }
        .dm-notes-toolbar .active { background: #b45309; color: #fff; border-color: #92400e; }
        .dm-notes-list {
          list-style: none; margin: 0; padding: 0; overflow-y: auto; max-height: 24vh;
          border-top: 1px solid #3f3f46; padding-top: 4px;
        }
        .dm-notes-list-empty { padding: 8px 4px; font-size: 12px; color: #a1a1aa; }
        .dm-notes-list-item { display: flex; align-items: stretch; gap: 4px; margin-bottom: 4px; }
        .dm-notes-list-select {
          flex: 1; text-align: left; background: #27272a; border: 1px solid #3f3f46;
          color: #fafafa; border-radius: 4px; padding: 6px 8px; cursor: pointer;
        }
        .dm-notes-list-select:hover { background: #3f3f46; }
        .dm-notes-list-item.is-selected .dm-notes-list-select {
          border-color: #f59e0b; box-shadow: inset 0 0 0 1px rgba(245, 158, 11, 0.35);
        }
        .dm-notes-list-label { display: block; font-size: 12px; font-weight: 600; }
        .dm-notes-list-meta { display: block; font-size: 10px; color: #a1a1aa; margin-top: 2px; }
        .dm-notes-list-delete { flex: 0 0 auto; min-width: 28px; padding: 2px 6px; }
        .dm-note-pin {
          position: absolute; top: 2px; right: 2px; z-index: 18;
          width: 18px; height: 18px; border: none; border-radius: 3px;
          background: #b45309; color: #fffbeb; font-size: 11px; line-height: 16px;
          padding: 0; cursor: pointer; box-shadow: 0 0 0 1px rgba(0,0,0,0.45);
          pointer-events: auto;
        }
        .dm-note-pin:hover, .dm-note-pin.is-selected { background: #f59e0b; color: #1c1917; }
        .dm-note-pin[data-offset="1"] { top: 2px; right: 22px; }
        .dm-note-pin[data-offset="2"] { top: 22px; right: 2px; }
        .dm-note-pin[data-offset="3"] { top: 22px; right: 22px; }
        body.dm-notes-placing .tile { cursor: crosshair; }
        body.dm-notes-placing .dm-note-pin { cursor: pointer; }
      </style>
    `);
  }

  function ensurePanel() {
    if ($('#dm-notes-panel').length) {
      return $('#dm-notes-panel');
    }
    const $panel = $(`
      <div id="dm-notes-panel" class="dm-notes-panel" role="region" aria-label="DM notes">
        <div class="dm-notes-panel-header">
          <div class="dm-notes-panel-title">
            DM Notes
            <small>Only you can see these</small>
          </div>
          <button type="button" class="btn btn-default btn-xs dm-notes-collapse" title="Collapse">−</button>
        </div>
        <div class="dm-notes-panel-body">
          <div class="dm-notes-toolbar">
            <button type="button" class="btn btn-warning btn-xs dm-notes-place">+ Note</button>
            <button type="button" class="btn btn-link btn-xs dm-notes-cancel" style="display:none;">Cancel</button>
          </div>
          <p class="dm-notes-panel-status">Alt+click a tile to add a note.</p>
          <ul class="dm-notes-list"></ul>
        </div>
      </div>
    `);
    const $dock = $('#map-chrome-right');
    if ($dock.length) {
      $dock.append($panel);
    } else {
      $('body').append($panel);
    }
    $panel.on('click', '.dm-notes-place', function () {
      setPlacing(!state.placing);
    });
    $panel.on('click', '.dm-notes-cancel', function () {
      setPlacing(false);
    });
    $panel.on('click', '.dm-notes-collapse', function () {
      $panel.toggleClass('is-collapsed');
      $(this).text($panel.hasClass('is-collapsed') ? '+' : '−');
    });
    $panel.on('click', '.dm-notes-list-select', function () {
      const id = String($(this).data('note-id') || '');
      const note = noteById(id);
      if (note) {
        openEditor(note, { isNew: false });
      }
    });
    $panel.on('click', '.dm-notes-list-delete', function (e) {
      e.preventDefault();
      e.stopPropagation();
      const id = String($(this).data('note-id') || '');
      if (id) {
        deleteNote(id);
      }
    });
    return $panel;
  }

  function ensureEditorModal() {
    if ($('#dm-note-editor-modal').length) {
      return;
    }
    const $modal = $(`
      <div class="modal fade" id="dm-note-editor-modal" tabindex="-1" role="dialog" aria-labelledby="dm-note-editor-title">
        <div class="modal-dialog" role="document">
          <div class="modal-content">
            <div class="modal-header">
              <button type="button" class="close" data-dismiss="modal" aria-label="Close"><span aria-hidden="true">&times;</span></button>
              <h4 class="modal-title" id="dm-note-editor-title">DM Note</h4>
            </div>
            <div class="modal-body">
              <p class="text-muted dm-note-editor-where"></p>
              <div class="form-group">
                <label for="dm-note-editor-title-input">Title</label>
                <input type="text" class="form-control" id="dm-note-editor-title-input" maxlength="80" placeholder="Optional short label">
              </div>
              <div class="form-group">
                <label for="dm-note-editor-text">Note</label>
                <textarea class="form-control" id="dm-note-editor-text" rows="5" maxlength="8000" placeholder="Only the DM can see this."></textarea>
              </div>
              <input type="hidden" id="dm-note-editor-id">
              <input type="hidden" id="dm-note-editor-map">
              <input type="hidden" id="dm-note-editor-x">
              <input type="hidden" id="dm-note-editor-y">
              <p class="text-danger dm-note-editor-error" style="display:none;"></p>
            </div>
            <div class="modal-footer">
              <button type="button" class="btn btn-danger pull-left dm-note-editor-delete" style="display:none;">Delete</button>
              <button type="button" class="btn btn-default" data-dismiss="modal">Cancel</button>
              <button type="button" class="btn btn-primary dm-note-editor-save">Save note</button>
            </div>
          </div>
        </div>
      </div>
    `);
    $('body').append($modal);
    $modal.find('.dm-note-editor-save').on('click', submitEditor);
    $modal.find('.dm-note-editor-delete').on('click', function () {
      const id = String($('#dm-note-editor-id').val() || '');
      if (id) {
        deleteNote(id, true);
      }
    });
  }

  function noteById(id) {
    return (state.notes || []).find((note) => note.id === id) || null;
  }

  function notesOnTile(mapName, x, y) {
    return (state.notes || []).filter((note) => note.map === mapName && note.x === x && note.y === y);
  }

  function renderList() {
    const $list = $('.dm-notes-list');
    if (!$list.length) {
      return;
    }
    $list.empty();
    if (!state.notes.length) {
      $list.append('<li class="dm-notes-list-empty">No notes on this map.</li>');
      return;
    }
    state.notes.forEach((note) => {
      const selected = note.id === state.selectedId ? ' is-selected' : '';
      $list.append(`
        <li class="dm-notes-list-item${selected}">
          <button type="button" class="dm-notes-list-select" data-note-id="${note.id}">
            <span class="dm-notes-list-label"></span>
            <span class="dm-notes-list-meta"></span>
          </button>
          <button type="button" class="btn btn-default btn-xs dm-notes-list-delete" data-note-id="${note.id}" title="Delete">&times;</button>
        </li>
      `);
      const $item = $list.children().last();
      $item.find('.dm-notes-list-label').text(note.title || 'Note');
      $item.find('.dm-notes-list-meta').text(`(${note.x}, ${note.y})`);
    });
  }

  function clearPins() {
    $('.dm-note-pin').remove();
  }

  function renderPins() {
    clearPins();
    if (!state.visible) {
      return;
    }
    const counts = {};
    state.notes.forEach((note) => {
      const key = `${note.map}:${note.x}:${note.y}`;
      const offset = counts[key] || 0;
      counts[key] = offset + 1;
      const $tile = findNoteTile(note.map, note.x, note.y);
      if (!$tile.length) {
        return;
      }
      const $pin = $('<button type="button" class="dm-note-pin" aria-label="DM note"></button>');
      $pin.attr('data-note-id', note.id);
      $pin.attr('data-offset', String(Math.min(offset, 3)));
      $pin.attr('title', note.title || 'DM note');
      $pin.text('✎');
      if (note.id === state.selectedId) {
        $pin.addClass('is-selected');
      }
      $tile.append($pin);
    });
  }

  function renderAll() {
    const $panel = ensurePanel();
    $panel.toggleClass('is-hidden', !state.visible);
    $('body').toggleClass('dm-notes-placing', !!(state.visible && state.placing));
    $('.dm-notes-place').toggleClass('active', state.placing);
    $('.dm-notes-cancel').toggle(state.placing);
    syncMenuLabel();
    renderList();
    renderPins();
  }

  function setPlacing(on) {
    state.placing = !!on && state.visible;
    if (state.placing) {
      setStatus('Click a tile to add a note. Esc cancels.');
    } else {
      setStatus('Alt+click a tile to add a note.');
    }
    renderAll();
  }

  function setVisible(on) {
    state.visible = !!on;
    if (!state.visible) {
      state.placing = false;
    }
    persistVisible();
    renderAll();
  }

  function openEditor(note, options) {
    ensureEditorModal();
    const isNew = !!(options && options.isNew);
    state.selectedId = note.id || null;
    $('#dm-note-editor-title').text(isNew ? 'New DM note' : 'Edit DM note');
    $('.dm-note-editor-where').text(`${note.map || currentMapName()} — (${note.x}, ${note.y})`);
    $('#dm-note-editor-id').val(note.id || '');
    $('#dm-note-editor-map').val(note.map || currentMapName());
    $('#dm-note-editor-x').val(note.x);
    $('#dm-note-editor-y').val(note.y);
    $('#dm-note-editor-title-input').val(note.title || '');
    $('#dm-note-editor-text').val(note.text || '');
    $('.dm-note-editor-delete').toggle(!isNew);
    $('.dm-note-editor-error').hide().text('');
    $('#dm-note-editor-modal').modal('show');
    setTimeout(() => $('#dm-note-editor-text').trigger('focus'), 200);
    renderAll();
  }

  function submitEditor() {
    const text = String($('#dm-note-editor-text').val() || '').trim();
    const $error = $('.dm-note-editor-error');
    if (!text) {
      $error.text('Note text is required.').show();
      return;
    }
    const payload = {
      id: String($('#dm-note-editor-id').val() || '').trim() || undefined,
      map_name: String($('#dm-note-editor-map').val() || currentMapName()),
      x: Number($('#dm-note-editor-x').val()),
      y: Number($('#dm-note-editor-y').val()),
      title: String($('#dm-note-editor-title-input').val() || '').trim(),
      text,
    };
    $.ajax({
      url: '/dm/notes',
      method: 'POST',
      contentType: 'application/json',
      data: JSON.stringify(payload),
      success(resp) {
        if (resp && resp.success && resp.note) {
          $('#dm-note-editor-modal').modal('hide');
          setPlacing(false);
          upsertLocal(resp.note);
        } else {
          $error.text((resp && resp.error) || 'Could not save note.').show();
        }
      },
      error(xhr) {
        const err = (xhr.responseJSON && xhr.responseJSON.error) || 'Could not save note.';
        $error.text(err).show();
      },
    });
  }

  function upsertLocal(note) {
    state.notes = (state.notes || []).filter((row) => row.id !== note.id).concat([note]);
    state.notes.sort((a, b) => (a.y - b.y) || (a.x - b.x) || String(a.id).localeCompare(String(b.id)));
    state.mapName = note.map || state.mapName;
    renderAll();
  }

  function deleteNote(noteId, closeModal) {
    $.ajax({
      url: '/dm/notes/' + encodeURIComponent(noteId),
      method: 'DELETE',
      success(resp) {
        if (resp && resp.success) {
          state.notes = (state.notes || []).filter((row) => row.id !== noteId);
          if (state.selectedId === noteId) {
            state.selectedId = null;
          }
          if (closeModal) {
            $('#dm-note-editor-modal').modal('hide');
          }
          renderAll();
        }
      },
    });
  }

  function loadNotes(mapName) {
    if (!isDmUser() || state.loading) {
      return;
    }
    const map = mapName || currentMapName();
    if (!map) {
      return;
    }
    state.loading = true;
    $.getJSON('/dm/notes', { map_name: map })
      .done((resp) => {
        if (resp && resp.success) {
          state.mapName = resp.map || map;
          state.notes = resp.notes || [];
          renderAll();
        }
      })
      .always(() => {
        state.loading = false;
      });
  }

  function handleTileClick(event, $tileArg) {
    if (!isDmUser()) {
      return false;
    }
    const $tile = $tileArg && $tileArg.length ? $tileArg : $(event.target).closest('.map-stack-tile, .tile');
    if (!$tile.length || $tile.hasClass('map-stack-underlay-tile')) {
      return false;
    }
    const $pin = $(event.target).closest('.dm-note-pin');
    if (!state.visible && !event.altKey && !$pin.length) {
      return false;
    }
    if (!state.visible) {
      setVisible(true);
    }
    if ($pin.length) {
      const note = noteById(String($pin.attr('data-note-id') || ''));
      if (note) {
        openEditor(note, { isNew: false });
      }
      return true;
    }
    const placing = state.placing || event.altKey;
    if (!placing) {
      return false;
    }
    const coords = tileCoords($tile);
    if (!coords) {
      return false;
    }
    const mapName = mapNameForTile($tile);
    const existing = notesOnTile(mapName, coords.x, coords.y);
    if (existing.length === 1 && !state.placing) {
      openEditor(existing[0], { isNew: false });
      return true;
    }
    openEditor({
      map: mapName,
      x: coords.x,
      y: coords.y,
      title: '',
      text: '',
    }, { isNew: true });
    return true;
  }

  function bindUi() {
    $(document).off('click.dmNotes', '.dm-note-pin').on('click.dmNotes', '.dm-note-pin', function (e) {
      e.preventDefault();
      e.stopPropagation();
      const note = noteById(String($(this).attr('data-note-id') || ''));
      if (note) {
        openEditor(note, { isNew: false });
      }
    });
    $(document).off('keydown.dmNotes').on('keydown.dmNotes', function (e) {
      if (e.key === 'Escape' && state.placing) {
        setPlacing(false);
      }
    });
    $('#toggle-dm-notes').off('click.dmNotes').on('click.dmNotes', function () {
      setVisible(!state.visible);
    });
  }

  function init() {
    if (!isDmUser()) {
      return;
    }
    state.visible = loadVisiblePreference();
    ensureStyles();
    ensurePanel();
    ensureEditorModal();
    bindUi();
    renderAll();
    loadNotes();
  }

  function reload(mapName) {
    if (!isDmUser()) {
      return;
    }
    loadNotes(mapName);
  }

  window.DmNotes = {
    init,
    reload,
    handleTileClick,
    setVisible,
    setPlacing,
  };

  $(function () {
    init();
  });
})(window, jQuery);
