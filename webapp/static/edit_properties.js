/* Edit-mode property editor — schema-driven forms (editor-only metadata). */

(function (window, $) {
  'use strict';

  let activeFixture = null;
  let activeSchema = null;
  let activeBinding = null;
  let activeObjectType = null;
  let pathPick = null;

  function ensureModal() {
    if ($('#edit-properties-modal').length) {
      return;
    }
    const $modal = $(`
      <div id="edit-properties-modal" class="modal fade" tabindex="-1" role="dialog" aria-labelledby="edit-properties-title">
        <div class="modal-dialog" role="document">
          <div class="modal-content edit-properties-modal-content">
            <div class="modal-header">
              <button type="button" class="close" data-dismiss="modal" aria-label="Close"><span aria-hidden="true">&times;</span></button>
              <h4 class="modal-title" id="edit-properties-title">Edit properties</h4>
            </div>
            <div class="modal-body" id="edit-properties-body"></div>
            <div class="modal-footer">
              <p class="edit-properties-status text-danger" id="edit-properties-status"></p>
              <button type="button" class="btn btn-default" data-dismiss="modal">Cancel</button>
              <button type="button" class="btn btn-primary" id="edit-properties-save">Save</button>
            </div>
          </div>
        </div>
      </div>
    `);
    $('body').append($modal);
    $modal.on('hidden.bs.modal', function () {
      if (pathPick) {
        return;
      }
      activeFixture = null;
      activeSchema = null;
      activeBinding = null;
      activeObjectType = null;
      $('#edit-properties-status').text('');
    });
    $('#edit-properties-save').on('click', saveProperties);
  }

  function fixturePayload($handle) {
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

  function fixturePayloadFromItem(item) {
    if (window.EditMode && typeof window.EditMode.fixturePayloadFromItem === 'function') {
      return window.EditMode.fixturePayloadFromItem(item);
    }
    if (item.source === 'inline_note' && item.parent_id) {
      return {
        item_id: String(item.parent_id),
        kind: String(item.parent_kind || item.kind || 'object'),
        token: item.token || undefined,
        layer: item.layer || undefined,
        source: item.parent_source || undefined,
        index: item.parent_index != null ? item.parent_index : undefined,
        object_type: item.parent_object_type || item.object_type || undefined,
        label: item.label,
      };
    }
    return {
      item_id: String(item.id),
      kind: String(item.kind),
      token: item.token || undefined,
      layer: item.layer || undefined,
      source: item.source || undefined,
      index: item.index != null ? item.index : undefined,
      object_type: item.object_type || undefined,
      label: item.label,
    };
  }

  function openPropertiesEditor(fixture) {
    ensureModal();
    if (pathPick) {
      finishPathPick(true);
    }
    activeFixture = fixture;
    if (!activeFixture.object_type) {
      return;
    }
    $('#edit-properties-status').text('');
    $('#edit-properties-title').text(`Edit: ${activeFixture.label || activeFixture.object_type}`);
    $.getJSON('/edit/object/properties', activeFixture, function (resp) {
      if (!resp || !resp.success) {
        alert((resp && resp.error) || 'Could not load object properties');
        return;
      }
      activeSchema = resp.schema;
      activeBinding = resp.binding;
      activeObjectType = resp.object_type;
      renderForm(activeSchema, resp.values || {});
      $('#edit-properties-modal').modal('show');
    }).fail(function (xhr) {
      let message = 'Could not load object properties';
      try {
        const body = JSON.parse(xhr.responseText || '{}');
        message = body.error || message;
      } catch (err) {
        /* ignore */
      }
      alert(message);
    });
  }

  function openFromHandle($handle) {
    openPropertiesEditor(fixturePayload($handle));
  }

  function openFromFixture(item) {
    openPropertiesEditor(fixturePayloadFromItem(item));
  }

  function fieldId(key) {
    return `edit-prop-${String(key).replace(/[^a-z0-9_-]+/gi, '-')}`;
  }

  function normalizeNoteEntry(entry) {
    if (entry == null) {
      return { note: '' };
    }
    if (typeof entry === 'string') {
      return { note: entry };
    }
    if (typeof entry !== 'object') {
      return { note: String(entry) };
    }
    const note = Object.assign({}, entry);
    let text = note.note != null ? note.note : (note.content != null ? note.content : note.message);
    if (text != null && typeof text === 'object') {
      text = JSON.stringify(text);
    } else if (text != null) {
      text = String(text);
    } else {
      text = '';
    }
    note.note = text;
    return note;
  }

  function coerceNoteListValue(value) {
    if (Array.isArray(value)) {
      return value;
    }
    if (value == null || value === '') {
      return [];
    }
    if (typeof value === 'string') {
      if (value.indexOf('[object Object]') !== -1) {
        return [{ note: '', perception_dc: 0 }];
      }
      try {
        const parsed = JSON.parse(value);
        if (Array.isArray(parsed)) {
          return parsed;
        }
      } catch (err) {
        /* ignore */
      }
      return [{ note: value }];
    }
    if (typeof value === 'object') {
      return Object.values(value);
    }
    return [];
  }

  function isNoteListField(field, value) {
    const type = field && field.type;
    if (type === 'note_list') {
      return true;
    }
    if (field && field.key === 'notes') {
      return true;
    }
    if (Array.isArray(value) && value.length && typeof value[0] === 'object') {
      return field && (field.key === 'notes' || type === 'note_list');
    }
    return false;
  }

  function isInventoryListField(field) {
    const type = field && field.type;
    if (type === 'inventory_list') {
      return true;
    }
    return field && field.key === 'inventory';
  }

  function coerceInventoryListValue(value) {
    if (Array.isArray(value)) {
      return value.map(function (entry) {
        if (typeof entry === 'string') {
          return { type: entry, qty: 1 };
        }
        if (!entry || typeof entry !== 'object') {
          return null;
        }
        const type = entry.type || entry.item || entry.name;
        if (!type) {
          return null;
        }
        let qty = Number(entry.qty);
        if (!Number.isFinite(qty) || qty < 1) {
          qty = 1;
        }
        return { type: String(type), qty: qty };
      }).filter(Boolean);
    }
    if (value && typeof value === 'object') {
      return Object.keys(value).map(function (key) {
        const entry = value[key];
        let qty = 1;
        if (entry && typeof entry === 'object') {
          qty = Number(entry.qty);
        } else {
          qty = Number(entry);
        }
        if (!Number.isFinite(qty) || qty < 1) {
          qty = 1;
        }
        return { type: String(key), qty: qty };
      });
    }
    return [];
  }

  function formatItemLabel(item) {
    if (!item) {
      return '';
    }
    const label = item.label || item.value || '';
    const value = item.value || '';
    if (label && value && label !== value) {
      return label + ' (' + value + ')';
    }
    return label || value;
  }

  function renderInventoryList(field, inventory) {
    const key = field.key;
    const catalogUrl = field.catalog_url || '/edit/catalog/items';
    const $wrap = $('<div class="edit-inventory-editor"></div>').attr('data-field-key', key);
    const $list = $('<div class="edit-inventory-list"></div>').attr('data-field-key', key);
    const $empty = $('<p class="help-block edit-inventory-empty">No items in this container.</p>');

    function syncEmptyState() {
      $empty.toggle($list.children('.edit-inventory-row').length === 0);
    }

    function formatContentsSummary(contents) {
      const rows = coerceInventoryListValue(contents);
      if (!rows.length) {
        return '';
      }
      return rows.map(function (row) {
        return row.qty + '× ' + row.type;
      }).join(', ');
    }

    function addRow(entry) {
      const itemType = String((entry && entry.type) || '').trim();
      const qty = Math.max(1, Number((entry && entry.qty) || 1) || 1);
      if (!itemType) {
        return;
      }
      const hasContents = entry && (Array.isArray(entry.contents) || entry.is_container);
      if (!hasContents) {
        const $existing = $list.children('.edit-inventory-row').filter(function () {
          return String($(this).attr('data-item-type') || '') === itemType
            && !$(this).attr('data-contents');
        });
        if ($existing.length) {
          const $qty = $existing.find('.edit-inventory-qty');
          $qty.val(Math.max(1, Number($qty.val() || 0) + qty));
          syncEmptyState();
          return;
        }
      }
      const $row = $('<div class="edit-inventory-row"></div>').attr('data-item-type', itemType);
      if (hasContents) {
        const contents = Array.isArray(entry.contents) ? entry.contents : [];
        $row.attr('data-contents', JSON.stringify(contents));
        if (entry.is_container) {
          $row.attr('data-is-container', '1');
        }
      }
      const $meta = $('<div class="edit-inventory-meta"></div>');
      $meta.append($('<div class="edit-inventory-type"></div>').text(itemType));
      if (entry && entry.label && entry.label !== itemType) {
        $meta.append($('<div class="edit-inventory-label text-muted"></div>').text(entry.label));
      }
      const contentsSummary = formatContentsSummary(entry && entry.contents);
      if (contentsSummary) {
        $meta.append(
          $('<div class="edit-inventory-contents text-muted"></div>').text('Contains: ' + contentsSummary),
        );
      } else if (hasContents) {
        $meta.append($('<div class="edit-inventory-contents text-muted"></div>').text('Empty container'));
      }
      const $qty = $('<input type="number" class="form-control input-sm edit-inventory-qty" min="1">').val(qty);
      const $remove = $('<button type="button" class="btn btn-link btn-xs text-danger edit-inventory-remove">Remove</button>');
      $remove.on('click', function () {
        $row.remove();
        syncEmptyState();
      });
      $row.append($meta, $qty, $remove);
      $list.append($row);
      syncEmptyState();
    }

    coerceInventoryListValue(inventory).forEach(addRow);
    syncEmptyState();

    const $adder = $('<div class="edit-inventory-adder"></div>');
    const $searchWrap = $('<div class="edit-inventory-search-wrap"></div>');
    const $search = $(
      '<input type="text" class="form-control edit-inventory-search" placeholder="Search items (e.g. healing potion, longsword)" autocomplete="off">',
    );
    const $suggestions = $('<div class="edit-inventory-suggestions" role="listbox"></div>').hide();
    const $qtyAdd = $('<input type="number" class="form-control input-sm edit-inventory-add-qty" min="1" value="1" title="Quantity">');
    const $addBtn = $('<button type="button" class="btn btn-default btn-sm edit-inventory-add">Add</button>');
    $searchWrap.append($search, $suggestions);
    $adder.append($searchWrap, $qtyAdd, $addBtn);

    let searchTimer = null;
    let activeIndex = -1;
    let currentSuggestions = [];

    function hideSuggestions() {
      $suggestions.hide().empty();
      activeIndex = -1;
      currentSuggestions = [];
    }

    function selectSuggestion(item) {
      if (!item) {
        return;
      }
      $search.val(item.value || '');
      $search.data('selected-item', item);
      hideSuggestions();
      $qtyAdd.focus().select();
    }

    function renderSuggestions(items) {
      currentSuggestions = items || [];
      $suggestions.empty();
      if (!currentSuggestions.length) {
        $suggestions.append($('<div class="edit-inventory-suggestion muted">No matching items</div>'));
        $suggestions.show();
        activeIndex = -1;
        return;
      }
      currentSuggestions.forEach(function (item, idx) {
        const $opt = $('<button type="button" class="edit-inventory-suggestion" role="option"></button>');
        $opt.attr('data-index', idx);
        $opt.append($('<span class="edit-inventory-suggestion-label"></span>').text(formatItemLabel(item)));
        if (item.category) {
          $opt.append($('<span class="edit-inventory-suggestion-cat"></span>').text(item.category));
        }
        $opt.on('mousedown', function (e) {
          e.preventDefault();
          selectSuggestion(item);
        });
        $suggestions.append($opt);
      });
      activeIndex = 0;
      $suggestions.children('.edit-inventory-suggestion').eq(0).addClass('is-active');
      $suggestions.show();
    }

    function fetchSuggestions(query) {
      $.getJSON(catalogUrl, { q: query, limit: 12 }, function (resp) {
        if (!resp || !resp.success) {
          hideSuggestions();
          return;
        }
        renderSuggestions(resp.items || []);
      }).fail(function () {
        hideSuggestions();
      });
    }

    $search.on('input', function () {
      $search.removeData('selected-item');
      const query = String($search.val() || '').trim();
      if (searchTimer) {
        clearTimeout(searchTimer);
      }
      if (!query) {
        hideSuggestions();
        return;
      }
      searchTimer = setTimeout(function () {
        fetchSuggestions(query);
      }, 160);
    });

    $search.on('keydown', function (e) {
      const $opts = $suggestions.children('.edit-inventory-suggestion[data-index]');
      if (e.key === 'ArrowDown' && $opts.length) {
        e.preventDefault();
        activeIndex = Math.min($opts.length - 1, activeIndex + 1);
        $opts.removeClass('is-active').eq(activeIndex).addClass('is-active');
        return;
      }
      if (e.key === 'ArrowUp' && $opts.length) {
        e.preventDefault();
        activeIndex = Math.max(0, activeIndex - 1);
        $opts.removeClass('is-active').eq(activeIndex).addClass('is-active');
        return;
      }
      if (e.key === 'Enter') {
        e.preventDefault();
        if (activeIndex >= 0 && currentSuggestions[activeIndex]) {
          selectSuggestion(currentSuggestions[activeIndex]);
          return;
        }
        $addBtn.trigger('click');
        return;
      }
      if (e.key === 'Escape') {
        hideSuggestions();
      }
    });

    $search.on('blur', function () {
      setTimeout(hideSuggestions, 120);
    });

    $addBtn.on('click', function () {
      const selected = $search.data('selected-item');
      const typed = String($search.val() || '').trim();
      const itemType = (selected && selected.value) || typed;
      if (!itemType) {
        $search.focus();
        return;
      }
      let qty = Number($qtyAdd.val());
      if (!Number.isFinite(qty) || qty < 1) {
        qty = 1;
      }
      addRow({
        type: itemType,
        qty: qty,
        label: selected && selected.label,
      });
      $search.val('').removeData('selected-item');
      $qtyAdd.val(1);
      hideSuggestions();
      $search.focus();
    });

    $wrap.append($list, $empty, $adder);
    return $wrap;
  }

  function renderNoteList(key, notes) {
    const $list = $('<div class="edit-note-list"></div>').attr('data-field-key', key);
    function addRow(entry) {
      const note = normalizeNoteEntry(entry);
      const $row = $('<div class="edit-note-row panel panel-default"></div>');
      const $body = $('<div class="panel-body"></div>');
      $body.append($('<label>Text</label>'));
      $body.append(
        $('<textarea class="form-control edit-note-text" rows="3"></textarea>').val(note.note || note.content || ''),
      );
      const $dcRow = $('<div class="row"></div>');
      const $perc = $('<div class="col-xs-6"></div>');
      $perc.append($('<label>Perception DC</label>'));
      $perc.append(
        $('<input type="number" class="form-control edit-note-perception" min="0">').val(
          note.perception_dc != null ? note.perception_dc : 0,
        ),
      );
      const $inv = $('<div class="col-xs-6"></div>');
      $inv.append($('<label>Investigation DC</label>'));
      $inv.append(
        $('<input type="number" class="form-control edit-note-investigation" min="0" placeholder="optional">').val(
          note.investigation_dc != null ? note.investigation_dc : '',
        ),
      );
      $dcRow.append($perc, $inv);
      $body.append($dcRow);
      const $skillRow = $('<div class="row"></div>');
      const $rel = $('<div class="col-xs-6"></div>');
      $rel.append($('<label>Religion DC</label>'));
      $rel.append(
        $('<input type="number" class="form-control edit-note-religion" min="0" placeholder="optional">').val(
          note.religion_dc != null ? note.religion_dc : '',
        ),
      );
      const $arc = $('<div class="col-xs-6"></div>');
      $arc.append($('<label>Arcana DC</label>'));
      $arc.append(
        $('<input type="number" class="form-control edit-note-arcana" min="0" placeholder="optional">').val(
          note.arcana_dc != null ? note.arcana_dc : '',
        ),
      );
      $skillRow.append($rel, $arc);
      $body.append($skillRow);
      $body.append($('<label>Image filename</label>'));
      $body.append(
        $('<input type="text" class="form-control edit-note-image" placeholder="e.g. pit_trap_image">').val(note.image || ''),
      );
      $body.append($('<p class="help-block">Optional image under static/assets/objects/</p>'));
      const $remove = $('<button type="button" class="btn btn-link btn-xs text-danger">Remove note</button>');
      $remove.on('click', function () {
        $row.remove();
      });
      $body.append($remove);
      $row.append($body);
      $list.append($row);
    }
    const rows = coerceNoteListValue(notes);
    rows.forEach((entry) => addRow(entry));
    if (!rows.length) {
      addRow({ note: '', perception_dc: 0 });
    }
    const $add = $('<button type="button" class="btn btn-default btn-sm edit-note-add">Add note</button>');
    $add.on('click', function () {
      addRow({ note: '', perception_dc: 0 });
    });
    const $wrap = $('<div></div>');
    $wrap.append($list);
    $wrap.append($add);
    return $wrap;
  }

  function coercePointList(value) {
    const points = [];
    const seen = {};
    const source = Array.isArray(value) ? value : [];
    source.forEach((item) => {
      const x = Number(Array.isArray(item) ? item[0] : item && item.x);
      const y = Number(Array.isArray(item) ? item[1] : item && item.y);
      if (!Number.isFinite(x) || !Number.isFinite(y)) {
        return;
      }
      const key = `${x},${y}`;
      if (seen[key]) {
        return;
      }
      seen[key] = true;
      points.push([x, y]);
    });
    return points;
  }

  function readPointList(key) {
    const raw = $(`.edit-properties-point-list[data-field-key="${key}"] input[data-point-list="1"]`).val();
    try {
      return coercePointList(JSON.parse(raw || '[]'));
    } catch (err) {
      return [];
    }
  }

  function writePointList(key, points, $root) {
    const pts = coercePointList(points);
    const $wrap = $root && $root.length
      ? $root
      : $(`.edit-properties-point-list[data-field-key="${key}"]`);
    $wrap.find('input[data-point-list="1"]').val(JSON.stringify(pts));
    const $chips = $wrap.find('.edit-point-list-chips').empty();
    if (!pts.length) {
      $chips.append($('<span class="text-muted"></span>').text('No squares yet — pick the base, then each step to the top.'));
      return pts;
    }
    pts.forEach((pt, index) => {
      let label = String(index + 1);
      if (index === 0) {
        label = 'Base';
      } else if (index === pts.length - 1) {
        label = 'Top';
      }
      $chips.append(
        $('<span class="edit-point-chip"></span>').text(`${label} (${pt[0]}, ${pt[1]})`),
      );
    });
    return pts;
  }

  function renderPointList(field, value) {
    const key = field.key;
    const $wrap = $('<div class="edit-properties-point-list"></div>').attr('data-field-key', key);
    const $hidden = $('<input type="hidden">')
      .attr('data-field-key', key)
      .attr('data-point-list', '1');
    const $chips = $('<div class="edit-point-list-chips"></div>');
    const $btns = $('<div class="edit-point-list-actions"></div>');
    const $pick = $('<button type="button" class="btn btn-default btn-sm">Pick squares on map</button>');
    const $undo = $('<button type="button" class="btn btn-default btn-sm">Undo</button>');
    const $clear = $('<button type="button" class="btn btn-default btn-sm">Clear</button>');
    $btns.append($pick, $undo, $clear);
    $wrap.append($hidden, $chips, $btns);
    writePointList(key, value, $wrap);
    $pick.on('click', function () {
      startPathPick(field);
    });
    $undo.on('click', function () {
      const pts = readPointList(key);
      pts.pop();
      writePointList(key, pts, $wrap);
    });
    $clear.on('click', function () {
      writePointList(key, [], $wrap);
    });
    return $wrap;
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

  function isPickingPath() {
    return !!pathPick;
  }

  function isUsablePathTile($tile) {
    return !!(
      $tile
      && $tile.length
      && $tile.closest('.tiles-container').length
      && !$tile.hasClass('map-stack-underlay-tile')
    );
  }

  function ignorePathPickClick(event) {
    return $(event.target).closest(
      '#edit-path-pick-banner, .modal, #centerActionBar, .popover-menu, .popover-menu-2, .map-annotations-panel, .edit-mode-banner, .navbar, #console-container',
    ).length > 0;
  }

  function tileFromPointerEvent(event) {
    let $tile = $(event.target).closest('.tiles-container .tile');
    if (isUsablePathTile($tile)) {
      return $tile;
    }
    if (typeof document.elementsFromPoint !== 'function') {
      return $();
    }
    const stack = document.elementsFromPoint(event.clientX, event.clientY) || [];
    for (let i = 0; i < stack.length; i += 1) {
      $tile = $(stack[i]).closest('.tiles-container .tile');
      if (isUsablePathTile($tile)) {
        return $tile;
      }
    }
    return $();
  }

  function ensurePickBanner() {
    if ($('#edit-path-pick-banner').length) {
      return;
    }
    const $banner = $(`
      <div id="edit-path-pick-banner" class="edit-path-pick-banner" hidden>
        <span class="edit-path-pick-label">Click squares from the stair base to the top.</span>
        <span class="edit-path-pick-actions">
          <button type="button" class="btn btn-default btn-sm" data-path-pick="undo">Undo</button>
          <button type="button" class="btn btn-default btn-sm" data-path-pick="clear">Clear</button>
          <button type="button" class="btn btn-primary btn-sm" data-path-pick="done">Done — use this path</button>
          <button type="button" class="btn btn-default btn-sm" data-path-pick="cancel">Cancel</button>
        </span>
      </div>
    `);
    $('body').append($banner);
    $(document).off('keydown.editPathPick').on('keydown.editPathPick', function (event) {
      if (!pathPick) {
        return;
      }
      if (event.key === 'Enter') {
        event.preventDefault();
        finishPathPick(false);
      } else if (event.key === 'Escape') {
        event.preventDefault();
        finishPathPick(true);
      }
    });
    $banner.on('click', '[data-path-pick]', function () {
      const op = $(this).attr('data-path-pick');
      if (op === 'undo') {
        if (pathPick && pathPick.points.length) {
          pathPick.points.pop();
          writePointList(pathPick.key, pathPick.points);
          paintPathPick();
        }
      } else if (op === 'clear') {
        if (pathPick) {
          pathPick.points = [];
          writePointList(pathPick.key, []);
          paintPathPick();
        }
      } else if (op === 'done') {
        finishPathPick(false);
      } else if (op === 'cancel') {
        finishPathPick(true);
      }
    });
  }

  function paintPathPick() {
    $('.edit-path-pick-index').remove();
    $('.tile').removeClass('edit-path-pick-tile edit-path-pick-base edit-path-pick-top');
    if (!pathPick) {
      $('#edit-path-pick-banner').attr('hidden', true);
      $('body').removeClass('edit-properties-picking-path');
      return;
    }
    pathPick.points.forEach((pt, index) => {
      const $tile = $(`.tiles-container .tile[data-coords-x="${pt[0]}"][data-coords-y="${pt[1]}"]`)
        .not('.map-stack-underlay-tile')
        .first();
      $tile.addClass('edit-path-pick-tile');
      if (index === 0) {
        $tile.addClass('edit-path-pick-base');
      }
      if (index === pathPick.points.length - 1) {
        $tile.addClass('edit-path-pick-top');
      }
      $tile.append($('<span class="edit-path-pick-index"></span>').text(String(index + 1)));
    });
    const n = pathPick.points.length;
    let label = 'Click the base square, then each step toward the top.';
    if (n === 1) {
      label = 'Base set. Click each step toward the top, then click that last square again (or Done).';
    } else if (n > 1) {
      label = `${n} squares — last is the top. Click it again, press Enter, or click Done to commit.`;
    }
    $('#edit-path-pick-banner .edit-path-pick-label').text(label);
    $('#edit-path-pick-banner').removeAttr('hidden');
    $('body').addClass('edit-properties-picking-path');
  }

  function startPathPick(field) {
    ensurePickBanner();
    const key = field.key;
    pathPick = {
      key: key,
      bounds: field.bounds || {},
      original: readPointList(key),
      points: readPointList(key),
    };
    $('#edit-properties-modal').modal('hide');
    paintPathPick();
  }

  function finishPathPick(cancelled) {
    if (!pathPick) {
      return;
    }
    const key = pathPick.key;
    const pts = cancelled ? pathPick.original : pathPick.points;
    pathPick = null;
    paintPathPick();
    writePointList(key, pts);
    if (activeFixture) {
      $('#edit-properties-modal').modal('show');
    }
  }

  function handleTileClick(event, $tileArg) {
    if (!pathPick) {
      return false;
    }
    const $tile = $tileArg && $tileArg.length ? $tileArg : tileFromPointerEvent(event);
    if (!isUsablePathTile($tile)) {
      return false;
    }
    const coords = tileCoords($tile);
    if (!coords) {
      return false;
    }
    const bounds = pathPick.bounds || {};
    if (bounds.width && (coords.x < 0 || coords.x >= bounds.width)) {
      return true;
    }
    if (bounds.height && (coords.y < 0 || coords.y >= bounds.height)) {
      return true;
    }
    const last = pathPick.points[pathPick.points.length - 1];
    const isLast = last && last[0] === coords.x && last[1] === coords.y;
    if (isLast && pathPick.points.length >= 2) {
      finishPathPick(false);
      return true;
    }
    const already = pathPick.points.some((pt) => pt[0] === coords.x && pt[1] === coords.y);
    if (!already) {
      pathPick.points.push([coords.x, coords.y]);
      writePointList(pathPick.key, pathPick.points);
      paintPathPick();
    }
    return true;
  }

  function renderField(field, values) {
    const key = field.key;
    const id = fieldId(key);
    const label = field.label || key;
    const value = values[key];
    const $group = $('<div class="form-group edit-properties-field"></div>').attr('data-field-key', key);
    const $label = $(`<label for="${id}"></label>`).text(label);
    $group.append($label);

    const type = field.type || 'string';
    const isChoiceField = type === 'map_ref' || type === 'enum' || type === 'select';
    if (isChoiceField) {
      const $select = $(`<select class="form-control" id="${id}"></select>`).attr('data-field-key', key);
      const choices = field.choices || [];
      $select.append($('<option value="">Select…</option>'));
      choices.forEach((choice) => {
        const val = choice.value != null ? choice.value : choice;
        const text = choice.label != null ? choice.label : choice;
        const $opt = $('<option></option>').attr('value', val).text(text);
        if (String(value) === String(val)) {
          $opt.prop('selected', true);
        }
        $select.append($opt);
      });
      if (type === 'map_ref') {
        $select.on('change', function () {
          const current = collectValues(activeSchema || { groups: [] });
          refreshSchema(current, { [key]: $(this).val() });
        });
      }
      $group.append($select);
    } else if (type === 'point') {
      const coords = Array.isArray(value) ? value : [0, 0];
      const bounds = field.bounds || {};
      const maxX = bounds.width != null ? bounds.width - 1 : null;
      const maxY = bounds.height != null ? bounds.height - 1 : null;
      const $row = $('<div class="row edit-properties-point"></div>');
      const $xCol = $('<div class="col-xs-6"></div>');
      const $yCol = $('<div class="col-xs-6"></div>');
      const $x = $(`<input type="number" class="form-control" id="${id}-x" min="0">`).val(coords[0]);
      const $y = $(`<input type="number" class="form-control" id="${id}-y" min="0">`).val(coords[1]);
      if (maxX != null) {
        $x.attr('max', maxX);
      }
      if (maxY != null) {
        $y.attr('max', maxY);
      }
      $x.attr('data-field-key', key).attr('data-point-part', 'x');
      $y.attr('data-field-key', key).attr('data-point-part', 'y');
      $xCol.append($('<label>X</label>')).append($x);
      $yCol.append($('<label>Y</label>')).append($y);
      $row.append($xCol, $yCol);
      $group.append($row);
      if (bounds.width && bounds.height) {
        $group.append(
          $('<p class="help-block"></p>').text(`Valid range: x 0–${bounds.width - 1}, y 0–${bounds.height - 1}`),
        );
      }
    } else if (type === 'point_list') {
      $group.append(renderPointList(field, value));
    } else if (type === 'integer') {
      const $input = $(`<input type="number" step="1" class="form-control" id="${id}">`)
        .attr('data-field-key', key)
        .val(value == null || value === '' ? (field.default != null ? field.default : '') : value);
      $group.append($input);
    } else if (type === 'boolean') {
      const $checkbox = $(`<input type="checkbox" id="${id}">`).attr('data-field-key', key);
      if (value) {
        $checkbox.prop('checked', true);
      }
      $group.addClass('checkbox').append($('<label></label>').append($checkbox).append(` ${label}`));
      $group.find('> label').first().remove();
    } else if (type === 'color') {
      const $input = $(`<input type="color" class="form-control edit-properties-color" id="${id}">`)
        .attr('data-field-key', key)
        .val(value || field.default || '#22c55e');
      $group.append($input);
    } else if (type === 'offset_px') {
      const coords = Array.isArray(value) ? value : [0, 0];
      const $row = $('<div class="row edit-properties-point"></div>');
      const $topCol = $('<div class="col-xs-6"></div>');
      const $leftCol = $('<div class="col-xs-6"></div>');
      const $top = $(`<input type="number" class="form-control" id="${id}-top">`).val(coords[0] || 0);
      const $left = $(`<input type="number" class="form-control" id="${id}-left">`).val(coords[1] || 0);
      $top.attr('data-field-key', key).attr('data-offset-part', 'top');
      $left.attr('data-field-key', key).attr('data-offset-part', 'left');
      $topCol.append($('<label>Top (px)</label>')).append($top);
      $leftCol.append($('<label>Left (px)</label>')).append($left);
      $row.append($topCol, $leftCol);
      $group.append($row);
    } else if (isInventoryListField(field)) {
      $group.append(renderInventoryList(field, value));
    } else if (isNoteListField(field, value)) {
      $group.append(renderNoteList(key, value));
    } else if (type === 'text') {
      const $textarea = $(`<textarea class="form-control" rows="3" id="${id}"></textarea>`)
        .attr('data-field-key', key)
        .val(value || '');
      $group.append($textarea);
    } else {
      const $input = $(`<input type="text" class="form-control" id="${id}">`)
        .attr('data-field-key', key)
        .val(value == null ? '' : value);
      $group.append($input);
    }
    if (field.help) {
      $group.append($('<p class="help-block"></p>').text(field.help));
    }
    return $group;
  }

  function renderForm(schema, values) {
    const $body = $('#edit-properties-body').empty();
    (schema.groups || []).forEach((group) => {
      const $section = $('<div class="edit-properties-group"></div>');
      $section.append($('<h5></h5>').text(group.label || group.id || 'Properties'));
      (group.fields || []).forEach((field) => {
        $section.append(renderField(field, values));
      });
      $body.append($section);
    });
  }

  function collectValues(schema) {
    const values = {};
    (schema.groups || []).forEach((group) => {
      (group.fields || []).forEach((field) => {
        const key = field.key;
        const type = field.type || 'string';
        if (type === 'point') {
          const x = Number($(`[data-field-key="${key}"][data-point-part="x"]`).val());
          const y = Number($(`[data-field-key="${key}"][data-point-part="y"]`).val());
          values[key] = [x, y];
        } else if (type === 'point_list') {
          values[key] = pathPick && pathPick.key === key ? pathPick.points.slice() : readPointList(key);
        } else if (type === 'offset_px') {
          const top = Number($(`[data-field-key="${key}"][data-offset-part="top"]`).val() || 0);
          const left = Number($(`[data-field-key="${key}"][data-offset-part="left"]`).val() || 0);
          values[key] = [top, left];
        } else if (isInventoryListField(field)) {
          const items = [];
          $(`.edit-inventory-list[data-field-key="${key}"] .edit-inventory-row`).each(function () {
            const $row = $(this);
            const itemType = String($row.attr('data-item-type') || '').trim();
            if (!itemType) {
              return;
            }
            let qty = Number($row.find('.edit-inventory-qty').val());
            if (!Number.isFinite(qty) || qty < 1) {
              qty = 1;
            }
            const row = { type: itemType, qty: qty };
            const contentsRaw = $row.attr('data-contents');
            if (contentsRaw != null && contentsRaw !== '') {
              try {
                row.contents = JSON.parse(contentsRaw);
                row.is_container = true;
              } catch (err) {
                row.contents = [];
                row.is_container = true;
              }
            } else if ($row.attr('data-is-container') === '1') {
              row.contents = [];
              row.is_container = true;
            }
            items.push(row);
          });
          values[key] = items;
        } else if (isNoteListField(field, values[key])) {
          const notes = [];
          $(`.edit-note-list[data-field-key="${key}"] .edit-note-row`).each(function () {
            const $row = $(this);
            const text = String($row.find('.edit-note-text').val() || '');
            const perception = $row.find('.edit-note-perception').val();
            const investigation = $row.find('.edit-note-investigation').val();
            const religion = $row.find('.edit-note-religion').val();
            const arcana = $row.find('.edit-note-arcana').val();
            const image = String($row.find('.edit-note-image').val() || '').trim();
            const entry = { note: text };
            if (perception !== '') {
              entry.perception_dc = Number(perception);
            }
            if (investigation !== '') {
              entry.investigation_dc = Number(investigation);
            }
            if (religion !== '') {
              entry.religion_dc = Number(religion);
            }
            if (arcana !== '') {
              entry.arcana_dc = Number(arcana);
            }
            if (image) {
              entry.image = image;
            }
            notes.push(entry);
          });
          values[key] = notes;
        } else if (type === 'boolean') {
          values[key] = $(`[data-field-key="${key}"]`).prop('checked');
        } else if (type === 'map_ref' || type === 'enum' || type === 'select') {
          values[key] = $(`select[data-field-key="${key}"]`).val();
        } else {
          values[key] = $(`[data-field-key="${key}"]`).val();
        }
      });
    });
    return values;
  }

  function refreshSchema(currentValues, patch) {
    if (!activeFixture || !activeObjectType) {
      return;
    }
    const merged = Object.assign({}, currentValues, patch || {});
    $.getJSON(
      '/edit/object/schema',
      {
        type: activeObjectType,
        values: JSON.stringify(merged),
      },
      function (resp) {
        if (!resp || !resp.success) {
          return;
        }
        activeSchema = resp.schema;
        renderForm(activeSchema, merged);
      },
    );
  }

  function saveProperties() {
    if (!activeFixture || !activeSchema || !activeBinding || !activeObjectType) {
      return;
    }
    const values = collectValues(activeSchema);
    $('#edit-properties-status').text('');
    $('#edit-properties-save').prop('disabled', true);
    $.ajax({
      url: '/edit/object/properties',
      method: 'POST',
      contentType: 'application/json',
      data: JSON.stringify({
        item_id: activeFixture.item_id,
        kind: activeFixture.kind,
        token: activeFixture.token,
        layer: activeFixture.layer,
        source: activeFixture.source,
        index: activeFixture.index,
        object_type: activeObjectType,
        binding: activeBinding,
        values: values,
      }),
    })
      .done(function (resp) {
        if (!resp || !resp.success) {
          $('#edit-properties-status').text((resp && resp.error) || 'Save failed');
          return;
        }
        $('#edit-properties-modal').modal('hide');
        if (window.EditMode && typeof window.EditMode.loadOverlay === 'function') {
          window.EditMode.loadOverlay();
        }
        if (window.Utils && typeof window.Utils.refreshTileSet === 'function') {
          window.Utils.refreshTileSet();
        }
      })
      .fail(function (xhr) {
        let message = 'Save failed';
        try {
          const body = JSON.parse(xhr.responseText || '{}');
          message = body.error || message;
        } catch (err) {
          /* ignore */
        }
        $('#edit-properties-status').text(message);
      })
      .always(function () {
        $('#edit-properties-save').prop('disabled', false);
      });
  }

  document.addEventListener(
    'click',
    function (event) {
      if (!pathPick || event.button) {
        return;
      }
      if (ignorePathPickClick(event)) {
        return;
      }
      const $tile = tileFromPointerEvent(event);
      if (!isUsablePathTile($tile)) {
        return;
      }
      if (!handleTileClick(event, $tile)) {
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      if (typeof event.stopImmediatePropagation === 'function') {
        event.stopImmediatePropagation();
      }
    },
    true,
  );

  window.EditProperties = {
    openFromHandle,
    openFromFixture,
    handleTileClick,
    isPickingPath,
  };
})(window, jQuery);
