/**
 * DM Console > Teleport panel.
 *
 * Manages each entity's Teleport-spell destination database via
 * GET/POST /dm/teleport_destinations (DM-only). Destinations are either a
 * map square (coords) or an anchor person/object whose current position is
 * used at cast time. Familiarity drives the PHB d100 Teleport outcome; an
 * entity with no destinations cannot cast Teleport at all.
 */
(function (root) {
  'use strict';

  var state = {
    loaded: false,
    casters: [],
    maps: [],
    anchors: [],
    familiarity: [],
    destinations: [],
    selectedUid: '',
  };

  function esc(s) {
    return $('<div>').text(s == null ? '' : String(s)).html();
  }

  function api(payload) {
    return $.ajax({
      url: '/dm/teleport_destinations',
      method: 'POST',
      contentType: 'application/json',
      data: JSON.stringify(payload),
    }).then(function (resp) {
      if (resp && resp.status && resp.status !== 'ok') {
        return $.Deferred().reject(resp.error || 'Error').promise();
      }
      return resp;
    });
  }

  function loadInitial() {
    return $.get('/dm/teleport_destinations').then(function (resp) {
      state.casters = resp.casters || [];
      state.maps = resp.maps || [];
      state.anchors = resp.anchors || [];
      state.familiarity = resp.familiarity_options || [];
      var preferred = state.casters.filter(function (c) { return c.knows_teleport; });
      if (!state.selectedUid && preferred.length) state.selectedUid = preferred[0].entity_uid;
      return loadDestinations();
    });
  }

  function loadDestinations() {
    if (!state.selectedUid) {
      state.destinations = [];
      render();
      return $.Deferred().resolve().promise();
    }
    return $.get('/dm/teleport_destinations', { entity_uid: state.selectedUid })
      .then(function (resp) {
        state.destinations = resp.destinations || [];
        render();
      });
  }

  function option(value, label, selected) {
    return '<option value="' + esc(value) + '"' + (value === selected ? ' selected' : '') + '>' +
      esc(label) + '</option>';
  }

  function render() {
    var $root = $('#teleport-dest-root');
    if (!$root.length) return;

    var casters = state.casters.map(function (c) {
      var label = c.label + (c.knows_teleport ? '' : ' (does not know Teleport)') +
        ' — ' + c.destination_count + ' dest';
      return option(c.entity_uid, label, state.selectedUid);
    }).join('');
    var maps = state.maps.map(function (m) { return option(m.name, m.name); }).join('');
    var anchors = state.anchors.map(function (a) { return option(a.entity_uid, a.label); }).join('');
    var fams = state.familiarity.map(function (f) { return option(f.value, f.label); }).join('');

    var rows = state.destinations.map(function (d) {
      var where = d.kind === 'anchor'
        ? 'anchor: ' + esc(d.label || d.entity_uid) +
          (d.resolved_map ? ' → now ' + esc(d.resolved_map) + ' (' + d.resolved_pos + ')' : '')
        : esc(d.map) + ' (' + d.x + ', ' + d.y + ')';
      var warn = d.unresolved ? ' <span class="label label-warning">unresolved</span>' : '';
      var famSelect = '<select class="form-control input-sm td-fam" data-id="' + esc(d.id) + '">' +
        state.familiarity.map(function (f) {
          return option(f.value, f.label, d.familiarity);
        }).join('') + '</select>';
      return '<tr data-id="' + esc(d.id) + '">' +
        '<td>' + esc(d.name) + warn +
        (d.notes ? '<div class="text-muted"><small>' + esc(d.notes) + '</small></div>' : '') +
        (d.off_target_map ? '<div class="text-muted"><small>off-target: ' + esc(d.off_target_map) + '</small></div>' : '') +
        '</td><td>' + where + '</td>' +
        '<td>' + famSelect + '</td>' +
        '<td><button type="button" class="btn btn-xs btn-danger td-del">Remove</button></td></tr>';
    }).join('');

    $root.html([
      '<div class="form-group">',
      '  <label for="td-caster">Caster</label>',
      '  <select class="form-control" id="td-caster">' + casters + '</select>',
      '  <p class="help-block"><small>Teleport is blocked while the selected caster has ',
      '  <strong>no destinations</strong>. Add one, or let a map event/object grant it.</small></p>',
      '</div>',
      '<table class="table table-condensed" id="td-table">',
      '  <thead><tr><th>Destination</th><th>Where</th><th>Familiarity</th><th></th></tr></thead>',
      '  <tbody>' + (rows || '<tr><td colspan="4" class="text-muted">No destinations yet.</td></tr>') + '</tbody>',
      '</table>',
      '<h5>Add destination</h5>',
      '<div class="row">',
      '  <div class="col-sm-4 form-group"><label>Name</label>',
      '    <input class="form-control input-sm" id="td-name" placeholder="e.g. Tower Library"></div>',
      '  <div class="col-sm-3 form-group"><label>Type</label>',
      '    <select class="form-control input-sm" id="td-kind">',
      option('coords', 'Map square', 'coords'), option('anchor', 'Person / object anchor', 'anchor'),
      '    </select></div>',
      '  <div class="col-sm-5 form-group"><label>Map</label>',
      '    <select class="form-control input-sm" id="td-map">' + maps + '</select></div>',
      '</div>',
      '<div class="row" id="td-coords-row">',
      '  <div class="col-sm-2 form-group"><label>x</label>',
      '    <input class="form-control input-sm" id="td-x" type="number" min="0" value="0"></div>',
      '  <div class="col-sm-2 form-group"><label>y</label>',
      '    <input class="form-control input-sm" id="td-y" type="number" min="0" value="0"></div>',
      '</div>',
      '<div class="row" id="td-anchor-row" style="display:none">',
      '  <div class="col-sm-6 form-group"><label>Anchor entity</label>',
      '    <select class="form-control input-sm" id="td-anchor">' + anchors + '</select></div>',
      '</div>',
      '<div class="row">',
      '  <div class="col-sm-4 form-group"><label>Familiarity</label>',
      '    <select class="form-control input-sm" id="td-fam">' + fams + '</select>',
      '    <p class="help-block"><small>Shown in the Teleport destination picker and drives ',
      '    the d100 outcome risk.</small></p></div>',
      '  <div class="col-sm-4 form-group"><label>Off-target map (optional)</label>',
      '    <select class="form-control input-sm" id="td-off"><option value="">(same map, random spot)</option>' +
      maps + '</select></div>',
      '  <div class="col-sm-4 form-group"><label>Notes</label>',
      '    <input class="form-control input-sm" id="td-notes"></div>',
      '</div>',
      '<button type="button" class="btn btn-primary btn-sm" id="td-add">Add destination</button>',
      '<span id="td-status" class="text-muted" style="margin-left:8px"></span>',
    ].join(''));

    $root.find('#td-caster').on('change', function () {
      state.selectedUid = $(this).val();
      loadDestinations();
    });
    $root.find('#td-kind').on('change', function () {
      var anchor = $(this).val() === 'anchor';
      $root.find('#td-coords-row').toggle(!anchor);
      $root.find('#td-anchor-row').toggle(anchor);
    });
    $root.find('.td-fam').on('change', function () {
      api({ op: 'set_familiarity', entity_uid: state.selectedUid,
            dest_id: $(this).data('id'), familiarity: $(this).val() })
        .then(loadDestinations, showStatus);
    });
    $root.find('.td-del').on('click', function () {
      api({ op: 'delete', entity_uid: state.selectedUid,
            dest_id: $(this).closest('tr').data('id') })
        .then(loadDestinations, showStatus);
    });
    $root.find('#td-add').on('click', function () {
      var kind = $root.find('#td-kind').val();
      var payload = {
        op: 'add',
        entity_uid: state.selectedUid,
        name: $root.find('#td-name').val(),
        kind: kind,
        familiarity: $root.find('#td-fam').val(),
        notes: $root.find('#td-notes').val(),
      };
      if (kind === 'anchor') {
        payload.anchor_entity_uid = $root.find('#td-anchor').val();
      } else {
        payload.map = $root.find('#td-map').val();
        payload.x = parseInt($root.find('#td-x').val() || '0', 10);
        payload.y = parseInt($root.find('#td-y').val() || '0', 10);
        var off = $root.find('#td-off').val();
        if (off) payload.off_target_map = off;
      }
      if (!payload.entity_uid) return showStatus('Select a caster first');
      api(payload).then(function () {
        $root.find('#td-name').val('');
        $root.find('#td-notes').val('');
        showStatus('Added.');
        // refresh caster counts too
        return $.get('/dm/teleport_destinations').then(function (resp) {
          state.casters = resp.casters || state.casters;
          return loadDestinations();
        });
      }, showStatus);
    });
  }

  function showStatus(err) {
    var msg = typeof err === 'string' ? err
      : (err && err.responseJSON && err.responseJSON.error) || 'Request failed';
    $('#td-status').addClass('text-danger').text(msg);
  }

  $(document).ready(function () {
    $('#teleport-dest-tab').on('shown.bs.tab click', function () {
      if (!state.loaded) {
        state.loaded = true;
        loadInitial().catch(showStatus);
      } else {
        loadDestinations();
      }
    });
  });

  root.TeleportDestinations = state;
})(window);
