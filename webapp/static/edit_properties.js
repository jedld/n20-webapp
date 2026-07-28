/* Edit-mode property editor — schema-driven forms (editor-only metadata). */

(function (window, $) {
  'use strict';

  let activeFixture = null;
  let activeSchema = null;
  let activeBinding = null;
  let activeObjectType = null;

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

  function renderField(field, values) {
    const key = field.key;
    const id = fieldId(key);
    const label = field.label || key;
    const value = values[key];
    const $group = $('<div class="form-group edit-properties-field"></div>').attr('data-field-key', key);
    const $label = $(`<label for="${id}"></label>`).text(label);
    $group.append($label);

    const type = field.type || 'string';
    if (type === 'map_ref' || type === 'enum') {
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
        } else if (type === 'boolean') {
          values[key] = $(`[data-field-key="${key}"]`).prop('checked');
        } else if (type === 'map_ref' || type === 'enum') {
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

  window.EditProperties = {
    openFromHandle,
    openFromFixture,
  };
})(window, jQuery);
