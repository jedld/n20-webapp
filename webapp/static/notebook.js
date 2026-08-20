/* Player / campaign notebook panel (not character journals, not map pins). */

(function (window, $) {
  'use strict';

  const STORAGE_OPEN = 'n20.notebook.open';
  const STORAGE_TAB = 'n20.notebook.tab';

  const state = {
    open: false,
    tab: 'player',
    scope: 'player',
    owner: null,
    tree: { folders: [], items: [] },
    query: '',
    sort: 'order',
    order: 'asc',
    selectedId: null,
    collapsed: {},
    loading: false,
    viewerItem: null,
    saveTimer: null,
    saveStatus: '',
    isDm: false,
    username: '',
    mapLinks: [],
    mapDrag: null,
  };

  function isDmUser() {
    const role = $('body').attr('data-role') || '';
    return String(role).toLowerCase().indexOf('dm') >= 0;
  }

  function escapeHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function safeUrl(url) {
    const raw = String(url || '').trim();
    if (!raw) {
      return '';
    }
    const lower = raw.toLowerCase();
    if (lower.startsWith('javascript:') || lower.startsWith('data:text/html')) {
      return '';
    }
    if (
      raw.startsWith('/notebook/')
      || lower.startsWith('https://')
      || lower.startsWith('http://')
      || raw.startsWith('./')
      || raw.startsWith('../')
    ) {
      return raw;
    }
    return '';
  }

  function renderMarkdown(src) {
    const text = String(src || '').replace(/\r\n/g, '\n');
    const blocks = [];
    const parts = text.split(/```/);
    for (let i = 0; i < parts.length; i += 1) {
      if (i % 2 === 1) {
        const chunk = parts[i];
        const nl = chunk.indexOf('\n');
        const code = nl >= 0 ? chunk.slice(nl + 1) : chunk;
        blocks.push(`<pre><code>${escapeHtml(code.replace(/\n$/, ''))}</code></pre>`);
        continue;
      }
      const escaped = escapeHtml(parts[i]);
      const html = escaped
        .replace(/^###### (.*)$/gm, '<h6>$1</h6>')
        .replace(/^##### (.*)$/gm, '<h5>$1</h5>')
        .replace(/^#### (.*)$/gm, '<h4>$1</h4>')
        .replace(/^### (.*)$/gm, '<h3>$1</h3>')
        .replace(/^## (.*)$/gm, '<h2>$1</h2>')
        .replace(/^# (.*)$/gm, '<h1>$1</h1>')
        .replace(/^&gt; (.*)$/gm, '<blockquote>$1</blockquote>')
        .replace(/^\s*[-*] (.*)$/gm, '<li>$1</li>')
        .replace(/^\s*\d+\. (.*)$/gm, '<li>$1</li>')
        .replace(/^(?:---|\*\*\*)\s*$/gm, '<hr>')
        .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
        .replace(/__([^_]+)__/g, '<strong>$1</strong>')
        .replace(/(^|[^\*])\*([^*]+)\*/g, '$1<em>$2</em>')
        .replace(/`([^`]+)`/g, '<code>$1</code>')
        .replace(/!\[([^\]]*)\]\(([^)]+)\)/g, function (_, alt, href) {
          const url = safeUrl(href);
          return url ? `<img alt="${alt}" src="${escapeHtml(url)}">` : '';
        })
        .replace(/\[([^\]]+)\]\(([^)]+)\)/g, function (_, label, href) {
          const url = safeUrl(href);
          return url ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener">${label}</a>` : label;
        });
      const withLists = html.replace(/(?:<li>.*<\/li>\n?)+/g, function (m) {
        return `<ul>${m}</ul>`;
      });
      const paras = withLists
        .split(/\n{2,}/)
        .map(function (chunk) {
          const trimmed = chunk.trim();
          if (!trimmed) {
            return '';
          }
          if (/^<(h[1-6]|ul|ol|pre|blockquote|hr)/.test(trimmed)) {
            return trimmed.replace(/\n/g, '');
          }
          return `<p>${trimmed.replace(/\n/g, '<br>')}</p>`;
        })
        .join('\n');
      blocks.push(paras);
    }
    return blocks.join('\n');
  }

  function setStatus(message) {
    state.saveStatus = message || '';
    $('.notebook-status').text(state.saveStatus);
  }

  function persistOpen() {
    try {
      window.localStorage.setItem(STORAGE_OPEN, state.open ? 'true' : 'false');
      window.localStorage.setItem(STORAGE_TAB, state.tab);
    } catch (_) { }
  }

  function loadPrefs() {
    try {
      state.open = window.localStorage.getItem(STORAGE_OPEN) === 'true';
      const tab = window.localStorage.getItem(STORAGE_TAB);
      if (tab) {
        state.tab = tab;
      }
    } catch (_) { }
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

  function loadMapLinks(mapName) {
    const map = mapName || currentMapName();
    if (!map) {
      state.mapLinks = [];
      renderMapPins();
      return;
    }
    api('/notebook/map_links', { data: { map_name: map } }).done(function (resp) {
      state.mapLinks = (resp && resp.links) || [];
      renderMapPins();
    }).fail(function () {
      state.mapLinks = [];
      renderMapPins();
    });
  }

  function renderMapPins() {
    $('.notebook-map-pin').remove();
    const counts = {};
    (state.mapLinks || []).forEach(function (link) {
      const key = `${link.map}:${link.x}:${link.y}`;
      const offset = counts[key] || 0;
      counts[key] = offset + 1;
      const $tile = findNoteTile(link.map, link.x, link.y);
      if (!$tile.length) {
        return;
      }
      const title = link.title || 'Note';
      const $pin = $('<button type="button" class="notebook-map-pin"></button>');
      $pin.attr('data-link-id', link.id);
      $pin.attr('data-item-id', link.item_id);
      $pin.attr('data-offset', String(Math.min(offset, 3)));
      $pin.attr('title', title + ' (only you can see this)');
      $pin.attr('aria-label', title);
      $pin.text(link.kind === 'file' ? '📎' : '📄');
      $tile.append($pin);
    });
  }

  function pinItemToMap(itemId, mapName, x, y) {
    if (!itemId) {
      return;
    }
    api('/notebook/map_links', {
      method: 'POST',
      data: JSON.stringify({
        item_id: itemId,
        map_name: mapName,
        x: x,
        y: y,
      }),
    }).done(function () {
      loadMapLinks(mapName);
      setStatus('Pinned to map (only you can see it)');
    }).fail(function (xhr) {
      setStatus((xhr.responseJSON && xhr.responseJSON.error) || 'Could not pin note');
    });
  }

  function removeMapLink(linkId) {
    if (!linkId) {
      return;
    }
    api(`/notebook/map_links/${linkId}`, { method: 'DELETE' }).done(function () {
      loadMapLinks();
    });
  }

  function handleMapDrop(event, hit) {
    const drag = state.mapDrag;
    let itemId = drag && drag.id;
    if (!itemId && event && event.originalEvent && event.originalEvent.dataTransfer) {
      try {
        const raw = event.originalEvent.dataTransfer.getData('application/x-n20-notebook')
          || event.originalEvent.dataTransfer.getData('text/plain');
        if (raw) {
          if (raw.charAt(0) === '{') {
            const parsed = JSON.parse(raw);
            itemId = parsed.itemId || parsed.id;
          } else if (raw.indexOf('notebook:') === 0) {
            itemId = raw.slice('notebook:'.length);
          }
        }
      } catch (_) { }
    }
    if (!itemId || (drag && drag.type === 'folder')) {
      return false;
    }
    if (!hit || hit.x == null || hit.y == null) {
      return false;
    }
    const mapName = hit.mapName || currentMapName();
    if (!mapName) {
      return false;
    }
    pinItemToMap(itemId, mapName, hit.x, hit.y);
    $('body').removeClass('notebook-map-drag-active');
    $('.tile').removeClass('notebook-map-drop-target');
    state.mapDrag = null;
    return true;
  }

  function currentScope() {
    if (state.tab === 'campaign') {
      return { scope: 'campaign', owner: null };
    }
    return { scope: 'player', owner: state.username };
  }

  function ensureDom() {
    if ($('#notebook-panel').length) {
      return;
    }
    const dm = isDmUser();
    state.isDm = dm;
    if (dm && !window.localStorage.getItem(STORAGE_TAB)) {
      state.tab = 'campaign';
    }
    const tabs = [];
    if (dm) {
      tabs.push('<button type="button" class="notebook-tab" data-tab="campaign">Campaign</button>');
    }
    tabs.push('<button type="button" class="notebook-tab" data-tab="player">My Notes</button>');
    if (dm) {
      tabs.push('<button type="button" class="notebook-tab" data-tab="map-pins">Map Pins</button>');
      tabs.push('<button type="button" class="notebook-tab" data-tab="assistant">Assistant</button>');
    }
    $('body').append(`
      <button type="button" id="notebook-handle" title="Notes">Notes</button>
      <aside id="notebook-panel" role="complementary" aria-label="Notes and journals">
        <div class="notebook-header">
          <h2>Notes</h2>
          <div class="notebook-header-actions">
            <button type="button" class="btn btn-default btn-xs notebook-close" title="Close">×</button>
          </div>
        </div>
        <div class="notebook-tabs">${tabs.join('')}</div>
        <div class="notebook-body-main">
          <div class="notebook-toolbar">
            <input type="search" class="notebook-search" placeholder="Search notes..." aria-label="Search notes">
            <select class="notebook-sort" aria-label="Sort">
              <option value="order">Manual</option>
              <option value="title">Name</option>
              <option value="updated">Updated</option>
              <option value="created">Created</option>
              <option value="kind">Type</option>
            </select>
            <button type="button" class="btn btn-default btn-xs notebook-new-note">+ Note</button>
            <button type="button" class="btn btn-default btn-xs notebook-new-folder">+ Folder</button>
            <button type="button" class="btn btn-default btn-xs notebook-upload">Upload</button>
            <input type="file" class="notebook-file-input" hidden>
          </div>
          <ul class="notebook-tree"></ul>
          <p class="notebook-status">Drag a note onto the map to pin it. Only you can see the pin.</p>
        </div>
        <div id="notebook-map-pins-host" style="display:none;"></div>
        <div id="notebook-ai-host" style="display:none;"></div>
      </aside>
      <div class="notebook-overlay" id="notebook-overlay">
        <div class="notebook-viewer" role="dialog" aria-modal="true" aria-label="Note editor">
          <div class="notebook-viewer-header">
            <input class="notebook-viewer-title" type="text" maxlength="200" aria-label="Title">
            <button type="button" class="btn btn-default btn-xs notebook-mode-edit">Edit</button>
            <button type="button" class="btn btn-default btn-xs notebook-mode-split">Split</button>
            <button type="button" class="btn btn-default btn-xs notebook-mode-preview">Preview</button>
            <button type="button" class="btn btn-warning btn-xs notebook-share-btn">Share</button>
            <button type="button" class="btn btn-default btn-xs notebook-viewer-close">Close</button>
          </div>
          <div class="notebook-md-toolbar">
            <button type="button" class="btn btn-default btn-xs" data-md="bold"><b>B</b></button>
            <button type="button" class="btn btn-default btn-xs" data-md="italic"><i>I</i></button>
            <button type="button" class="btn btn-default btn-xs" data-md="h2">H2</button>
            <button type="button" class="btn btn-default btn-xs" data-md="ul">List</button>
            <button type="button" class="btn btn-default btn-xs" data-md="quote">Quote</button>
            <button type="button" class="btn btn-default btn-xs" data-md="code">Code</button>
            <button type="button" class="btn btn-default btn-xs" data-md="link">Link</button>
          </div>
          <div class="notebook-editor-split is-edit-only">
            <textarea class="notebook-editor" spellcheck="true"></textarea>
            <div class="notebook-preview"></div>
          </div>
          <div class="notebook-file-view" style="display:none;"></div>
          <div class="notebook-share-dialog" style="display:none;">
            <strong>Share view with</strong>
            <div class="notebook-share-list"></div>
            <label><input type="checkbox" class="notebook-share-all"> Everyone online</label>
            <div style="margin-top:8px;display:flex;gap:6px;justify-content:flex-end;">
              <button type="button" class="btn btn-default btn-xs notebook-share-cancel">Cancel</button>
              <button type="button" class="btn btn-warning btn-xs notebook-share-send">Share</button>
            </div>
          </div>
        </div>
      </div>
    `);
  }

  function isTreeTab(tab) {
    return tab !== 'map-pins' && tab !== 'assistant';
  }

  function applyOpen() {
    $('body').toggleClass('notebook-open', state.open);
    persistOpen();
    if (state.open) {
      loadTree();
      mountMapPins();
      mountAiAssistant();
    }
  }

  function setOpen(open) {
    state.open = !!open;
    applyOpen();
  }

  function setTab(tab) {
    let next = tab || state.tab;
    if (!state.isDm && (next === 'campaign' || next === 'map-pins' || next === 'assistant')) {
      next = 'player';
    }
    state.tab = next;
    $('.notebook-tab').removeClass('is-active');
    $(`.notebook-tab[data-tab="${next}"]`).addClass('is-active');
    const pins = next === 'map-pins';
    const assistant = next === 'assistant';
    $('.notebook-body-main').css('display', (pins || assistant) ? 'none' : 'flex');
    $('#notebook-map-pins-host').css('display', pins ? 'flex' : 'none');
    $('#notebook-ai-host').css('display', assistant ? 'flex' : 'none');
    persistOpen();
    if (pins) {
      mountMapPins();
    } else if (assistant) {
      mountAiAssistant();
    } else {
      loadTree();
    }
  }

  function mountMapPins() {
    if (!state.isDm) {
      return;
    }
    const $host = $('#notebook-map-pins-host');
    if (window.DmNotes && typeof window.DmNotes.mountPanel === 'function') {
      window.DmNotes.mountPanel($host);
    }
  }

  function mountAiAssistant() {
    if (!state.isDm) {
      return;
    }
    const $host = $('#notebook-ai-host');
    const $panel = $('#ai-chat-panel');
    if (!$host.length || !$panel.length) {
      return;
    }
    $panel.addClass('is-embedded').removeClass('minimized dragging');
    $panel.find('.panel-body').show();
    $panel.css({
      display: 'flex',
      position: 'relative',
      top: 'auto',
      left: 'auto',
      right: 'auto',
      bottom: 'auto',
      width: '100%',
      height: '100%',
      maxWidth: 'none',
      maxHeight: 'none',
      minWidth: '0',
      minHeight: '0',
      zIndex: 'auto',
      transform: 'none',
      boxShadow: 'none',
      border: 'none',
      background: 'transparent',
    });
    if ($panel.parent()[0] !== $host[0]) {
      $host.append($panel);
    }
  }

  function api(url, opts) {
    return $.ajax(Object.assign({
      url,
      method: 'GET',
      contentType: 'application/json',
    }, opts || {}));
  }

  function loadTree() {
    if (!isTreeTab(state.tab)) {
      return;
    }
    const loc = currentScope();
    state.loading = true;
    const params = {
      scope: loc.scope,
      sort: state.sort,
      order: state.order,
    };
    if (state.query) {
      params.q = state.query;
    }
    api('/notebook', { data: params }).done(function (resp) {
      state.tree = resp || { folders: [], items: [] };
      state.isDm = !!resp.is_dm;
      state.username = resp.username || state.username;
      renderTree();
      setStatus('');
    }).fail(function (xhr) {
      setStatus((xhr.responseJSON && xhr.responseJSON.error) || 'Failed to load notes');
    }).always(function () {
      state.loading = false;
    });
  }

  function foldersByParent() {
    const map = { '': [] };
    (state.tree.folders || []).forEach(function (folder) {
      const key = folder.parent_id || '';
      if (!map[key]) {
        map[key] = [];
      }
      map[key].push(folder);
    });
    Object.keys(map).forEach(function (key) {
      map[key].sort(function (a, b) {
        return (a.sort_order - b.sort_order) || String(a.name).localeCompare(String(b.name));
      });
    });
    return map;
  }

  function itemsByFolder() {
    const map = { '': [] };
    (state.tree.items || []).forEach(function (item) {
      const key = item.folder_id || '';
      if (!map[key]) {
        map[key] = [];
      }
      map[key].push(item);
    });
    return map;
  }

  function nodeIcon(node, kind) {
    if (kind === 'folder') {
      return state.collapsed[node.id] ? '▸' : '▾';
    }
    if (node.kind === 'file') {
      return node.is_image ? '🖼' : '📎';
    }
    return '📄';
  }

  function renderNode(node, kind) {
    const selected = state.selectedId === node.id ? ' is-selected' : '';
    const label = escapeHtml(kind === 'folder' ? node.name : node.title);
    return $(`
      <li>
        <div class="notebook-node${selected}" draggable="true"
             data-id="${escapeHtml(node.id)}" data-kind="${kind === 'folder' ? 'folder' : node.kind}"
             data-type="${kind}">
          <span class="notebook-node-icon">${nodeIcon(node, kind)}</span>
          <span class="notebook-node-label">${label}</span>
        </div>
      </li>
    `);
  }

  function renderBranch($parent, parentId, folderMap, itemMap) {
    const folders = folderMap[parentId] || [];
    const items = itemMap[parentId] || [];
    folders.forEach(function (folder) {
      const $li = renderNode(folder, 'folder');
      const $kids = $('<ul class="notebook-children"></ul>');
      if (state.collapsed[folder.id]) {
        $kids.addClass('is-collapsed');
      }
      renderBranch($kids, folder.id, folderMap, itemMap);
      $li.append($kids);
      $parent.append($li);
    });
    items.forEach(function (item) {
      $parent.append(renderNode(item, 'item'));
    });
  }

  function renderTree() {
    const $tree = $('.notebook-tree');
    $tree.empty();
    const folders = state.tree.folders || [];
    const items = state.tree.items || [];
    if (!folders.length && !items.length) {
      $tree.append('<li class="notebook-empty">No notes yet. Create a note, folder, or upload a file.</li>');
      return;
    }
    if (state.query) {
      items.forEach(function (item) {
        $tree.append(renderNode(item, 'item'));
      });
      return;
    }
    renderBranch($tree, '', foldersByParent(), itemsByFolder());
  }

  function selectedFolderId() {
    const id = state.selectedId;
    if (!id) {
      return null;
    }
    const folder = (state.tree.folders || []).find(function (row) { return row.id === id; });
    if (folder) {
      return folder.id;
    }
    const item = (state.tree.items || []).find(function (row) { return row.id === id; });
    return item ? (item.folder_id || null) : null;
  }

  function createNote() {
    const loc = currentScope();
    api('/notebook/items', {
      method: 'POST',
      data: JSON.stringify({
        scope: loc.scope,
        title: 'Untitled note',
        content: '',
        folder_id: selectedFolderId(),
      }),
    }).done(function (resp) {
      loadTree();
      if (resp && resp.item) {
        openViewer(resp.item.id);
      }
    }).fail(function (xhr) {
      setStatus((xhr.responseJSON && xhr.responseJSON.error) || 'Could not create note');
    });
  }

  function createFolder() {
    const name = window.prompt('Folder name', 'New folder');
    if (!name) {
      return;
    }
    const loc = currentScope();
    api('/notebook/folders', {
      method: 'POST',
      data: JSON.stringify({
        scope: loc.scope,
        name: name,
        parent_id: selectedFolderId(),
      }),
    }).done(function () {
      loadTree();
    }).fail(function (xhr) {
      setStatus((xhr.responseJSON && xhr.responseJSON.error) || 'Could not create folder');
    });
  }

  function uploadSelectedFile(file) {
    if (!file) {
      return;
    }
    const loc = currentScope();
    const form = new FormData();
    form.append('file', file);
    form.append('scope', loc.scope);
    const folderId = selectedFolderId();
    if (folderId) {
      form.append('folder_id', folderId);
    }
    $.ajax({
      url: '/notebook/items',
      method: 'POST',
      data: form,
      processData: false,
      contentType: false,
    }).done(function () {
      loadTree();
      setStatus('Uploaded ' + file.name);
    }).fail(function (xhr) {
      setStatus((xhr.responseJSON && xhr.responseJSON.error) || 'Upload failed');
    });
  }

  function renameNode(nodeType, id, currentName) {
    const name = window.prompt(nodeType === 'folder' ? 'Rename folder' : 'Rename', currentName || '');
    if (!name) {
      return;
    }
    const url = nodeType === 'folder' ? `/notebook/folders/${id}` : `/notebook/items/${id}`;
    api(url, {
      method: 'PATCH',
      data: JSON.stringify(nodeType === 'folder' ? { name: name } : { title: name }),
    }).done(function () {
      loadTree();
    });
  }

  function deleteNode(nodeType, id) {
    if (!window.confirm('Delete this ' + (nodeType === 'folder' ? 'folder and its contents' : 'item') + '?')) {
      return;
    }
    const url = nodeType === 'folder' ? `/notebook/folders/${id}` : `/notebook/items/${id}`;
    api(url, { method: 'DELETE' }).done(function () {
      if (state.viewerItem && state.viewerItem.id === id) {
        closeViewer();
      }
      loadTree();
    });
  }

  function wrapSelection(before, after) {
    const el = $('.notebook-editor')[0];
    if (!el) {
      return;
    }
    const start = el.selectionStart;
    const end = el.selectionEnd;
    const value = el.value;
    const selected = value.slice(start, end) || 'text';
    el.value = value.slice(0, start) + before + selected + after + value.slice(end);
    el.focus();
    el.selectionStart = start + before.length;
    el.selectionEnd = start + before.length + selected.length;
    scheduleSave();
    refreshPreview();
  }

  function refreshPreview() {
    const md = $('.notebook-editor').val() || '';
    $('.notebook-preview').html(renderMarkdown(md));
  }

  function scheduleSave() {
    if (!state.viewerItem || state.viewerItem.kind !== 'note' || state.viewerItem.readOnly) {
      return;
    }
    setStatus('Saving…');
    window.clearTimeout(state.saveTimer);
    state.saveTimer = window.setTimeout(persistViewer, 450);
  }

  function persistViewer() {
    if (!state.viewerItem || state.viewerItem.kind !== 'note' || state.viewerItem.readOnly) {
      return;
    }
    const title = $('.notebook-viewer-title').val();
    const content = $('.notebook-editor').val();
    api(`/notebook/items/${state.viewerItem.id}`, {
      method: 'PATCH',
      data: JSON.stringify({ title: title, content: content }),
    }).done(function (resp) {
      state.viewerItem = resp.item;
      setStatus('Saved');
      loadTree();
    }).fail(function (xhr) {
      setStatus((xhr.responseJSON && xhr.responseJSON.error) || 'Save failed');
    });
  }

  function setEditorMode(mode) {
    const $split = $('.notebook-editor-split');
    $split.removeClass('is-edit-only is-preview-only');
    if (mode === 'preview') {
      $split.addClass('is-preview-only');
    } else if (mode === 'edit') {
      $split.addClass('is-edit-only');
    }
    $('.notebook-mode-edit, .notebook-mode-split, .notebook-mode-preview').removeClass('is-active');
    $(`.notebook-mode-${mode}`).addClass('is-active');
    refreshPreview();
  }

  function fillFileView(item, shareId) {
    const $view = $('.notebook-file-view').empty().show();
    $('.notebook-editor-split, .notebook-md-toolbar').hide();
    const query = [];
    if (shareId) {
      query.push('share=' + encodeURIComponent(shareId));
    }
    const src = item.file_url + (query.length ? '?' + query.join('&') : '');
    const downloadQuery = query.concat(['download=1']);
    const downloadSrc = item.file_url + '?' + downloadQuery.join('&');
    if (item.is_image) {
      $view.append(`<img src="${escapeHtml(src)}" alt="${escapeHtml(item.title || '')}">`);
    } else {
      $view.append(`<p>${escapeHtml(item.filename || item.title || 'File')}</p>`);
      $view.append(`<a class="btn btn-default" href="${escapeHtml(downloadSrc)}">Download</a>`);
    }
  }

  function openViewer(itemId, opts) {
    const options = opts || {};
    api(`/notebook/items/${itemId}`).done(function (resp) {
      const item = resp.item;
      state.viewerItem = item;
      state.viewerItem.readOnly = !!options.readOnly;
      $('#notebook-overlay').addClass('is-open');
      $('.notebook-viewer-title').val(item.title || '').prop('readonly', !!options.readOnly);
      $('.notebook-share-btn').toggle(!options.readOnly);
      $('.notebook-share-dialog').hide();
      if (item.kind === 'file') {
        fillFileView(item, options.shareId);
      } else {
        $('.notebook-file-view').hide();
        $('.notebook-editor-split, .notebook-md-toolbar').show();
        $('.notebook-editor').val(item.content || '').prop('readonly', !!options.readOnly);
        setEditorMode(options.readOnly ? 'preview' : 'edit');
        refreshPreview();
      }
    }).fail(function (xhr) {
      setStatus((xhr.responseJSON && xhr.responseJSON.error) || 'Could not open note');
    });
  }

  function closeViewer() {
    if (state.viewerItem && state.viewerItem.kind === 'note' && !state.viewerItem.readOnly) {
      persistViewer();
    }
    $('#notebook-overlay').removeClass('is-open');
    state.viewerItem = null;
  }

  function loadShareRecipients() {
    api('/notebook/recipients').done(function (resp) {
      const $list = $('.notebook-share-list').empty();
      (resp.recipients || []).forEach(function (person) {
        const online = person.online ? ' (online)' : '';
        $list.append(
          `<label><input type="checkbox" value="${escapeHtml(person.username)}"> ${escapeHtml(person.username)}${online}</label>`
        );
      });
      if (!(resp.recipients || []).length) {
        $list.append('<p class="notebook-empty">No other players in this campaign.</p>');
      }
    });
  }

  function sendShare() {
    if (!state.viewerItem) {
      return;
    }
    const usernames = [];
    $('.notebook-share-list input:checked').each(function () {
      usernames.push($(this).val());
    });
    const all = $('.notebook-share-all').prop('checked');
    api(`/notebook/items/${state.viewerItem.id}/share`, {
      method: 'POST',
      data: JSON.stringify({ usernames: usernames, all: all }),
    }).done(function () {
      $('.notebook-share-dialog').hide();
      setStatus('Shared');
    }).fail(function (xhr) {
      setStatus((xhr.responseJSON && xhr.responseJSON.error) || 'Share failed');
    });
  }

  function showShared(payload) {
    const shareId = payload && payload.share_id;
    if (!shareId) {
      return;
    }
    api(`/notebook/shared/${shareId}`).done(function (resp) {
      const item = resp.item || {};
      const fromName = (resp.share && resp.share.from_username) || payload.from_username || 'Player';
      $('.notebook-share-popup').remove();
      const $popup = $(`
        <div class="notebook-share-popup" role="dialog" aria-label="Shared document">
          <div class="notebook-share-popup-actions">
            <button type="button" class="btn btn-default btn-xs notebook-share-expand">Open</button>
            <button type="button" class="btn btn-default btn-xs notebook-share-dismiss">Dismiss</button>
          </div>
          <h3>${escapeHtml(fromName)} shared: ${escapeHtml(item.title || payload.title || 'Document')}</h3>
          <div class="notebook-share-popup-body"></div>
        </div>
      `);
      const $body = $popup.find('.notebook-share-popup-body');
      if (item.kind === 'note') {
        $body.html(renderMarkdown(item.content || ''));
      } else if (item.is_image) {
        const src = `${item.file_url}?share=${encodeURIComponent(shareId)}`;
        $body.append(`<img src="${escapeHtml(src)}" alt="" style="max-width:100%;">`);
      } else {
        const src = `${item.file_url}?share=${encodeURIComponent(shareId)}&download=1`;
        $body.append(`<a class="btn btn-default" href="${escapeHtml(src)}">Download ${escapeHtml(item.filename || 'file')}</a>`);
      }
      $popup.on('click', '.notebook-share-dismiss', function () {
        $popup.remove();
      });
      $popup.on('click', '.notebook-share-expand', function () {
        $popup.remove();
        state.viewerItem = item;
        state.viewerItem.readOnly = true;
        $('#notebook-overlay').addClass('is-open');
        $('.notebook-viewer-title').val(item.title || '').prop('readonly', true);
        $('.notebook-share-btn').hide();
        if (item.kind === 'file') {
          fillFileView(item, shareId);
        } else {
          $('.notebook-file-view').hide();
          $('.notebook-editor-split, .notebook-md-toolbar').show();
          $('.notebook-editor').val(item.content || '').prop('readonly', true);
          setEditorMode('preview');
        }
      });
      $('body').append($popup);
    });
  }

  function hideContext() {
    $('.notebook-ctx').remove();
  }

  function showContext(event, $node) {
    hideContext();
    const id = $node.attr('data-id');
    const type = $node.attr('data-type');
    const kind = $node.attr('data-kind');
    const label = $node.find('.notebook-node-label').text();
    const $menu = $(`
      <div class="notebook-ctx">
        <button type="button" data-act="open">Open</button>
        <button type="button" data-act="rename">Rename</button>
        <button type="button" data-act="delete">Delete</button>
      </div>
    `);
    $menu.css({ left: event.pageX + 'px', top: event.pageY + 'px' });
    $menu.on('click', 'button', function () {
      const act = $(this).attr('data-act');
      hideContext();
      if (act === 'open') {
        if (type === 'folder') {
          state.collapsed[id] = !state.collapsed[id];
          renderTree();
        } else {
          openViewer(id);
        }
      } else if (act === 'rename') {
        renameNode(type, id, label);
      } else if (act === 'delete') {
        deleteNode(type, id);
      }
    });
    $('body').append($menu);
  }

  function bindDrag($tree) {
    $tree.on('dragstart', '.notebook-node', function (e) {
      const $node = $(this);
      const dragId = $node.attr('data-id');
      const dragType = $node.attr('data-type');
      const kind = $node.attr('data-kind');
      const title = $node.find('.notebook-node-label').text();
      state.mapDrag = { id: dragId, type: dragType, kind: kind, title: title };
      $node.addClass('is-dragging');
      try {
        const payload = JSON.stringify({
          itemId: dragId,
          type: dragType,
          kind: kind,
          title: title,
        });
        e.originalEvent.dataTransfer.setData('application/x-n20-notebook', payload);
        e.originalEvent.dataTransfer.setData('text/plain', 'notebook:' + dragId);
        e.originalEvent.dataTransfer.effectAllowed = dragType === 'folder' ? 'move' : 'copyMove';
      } catch (_) { }
      if (dragType !== 'folder') {
        $('body').addClass('notebook-map-drag-active');
      }
    });
    $tree.on('dragend', '.notebook-node', function () {
      $(this).removeClass('is-dragging');
      $('.notebook-node').removeClass('is-drop-target');
      $('.tile').removeClass('notebook-map-drop-target');
      $('body').removeClass('notebook-map-drag-active');
      state.mapDrag = null;
    });
    $tree.on('dragover', '.notebook-node', function (e) {
      e.preventDefault();
      $('.notebook-node').removeClass('is-drop-target');
      $(this).addClass('is-drop-target');
    });
    $tree.on('drop', '.notebook-node', function (e) {
      e.preventDefault();
      e.stopPropagation();
      $('.notebook-node').removeClass('is-drop-target');
      const $target = $(this);
      const targetType = $target.attr('data-type');
      const targetId = $target.attr('data-id');
      if (!state.mapDrag || state.mapDrag.id === targetId) {
        return;
      }
      let folderId = null;
      if (targetType === 'folder') {
        folderId = targetId;
      } else {
        const item = (state.tree.items || []).find(function (row) { return row.id === targetId; });
        folderId = item ? item.folder_id : null;
      }
      api('/notebook/move', {
        method: 'POST',
        data: JSON.stringify({ type: state.mapDrag.type, id: state.mapDrag.id, folder_id: folderId }),
      }).done(function () {
        loadTree();
      });
    });
    $tree.on('dragover', function (e) {
      e.preventDefault();
    });
    $tree.on('drop', function (e) {
      if (!$(e.target).closest('.notebook-node').length && state.mapDrag) {
        e.preventDefault();
        api('/notebook/move', {
          method: 'POST',
          data: JSON.stringify({ type: state.mapDrag.type, id: state.mapDrag.id, folder_id: null }),
        }).done(function () {
          loadTree();
        });
      }
    });
  }

  function bindUi() {
    $('#notebook-handle, #toggle-notebook').off('click.notebook').on('click.notebook', function (e) {
      e.preventDefault();
      setOpen(!state.open);
    });
    $('#toggle-ai-assistant').off('click.notebook').on('click.notebook', function (e) {
      e.preventDefault();
      state.tab = 'assistant';
      setOpen(true);
      setTab('assistant');
    });
    $('.notebook-close').off('click.notebook').on('click.notebook', function () {
      setOpen(false);
    });
    $('.notebook-tab').off('click.notebook').on('click.notebook', function () {
      setTab($(this).attr('data-tab'));
    });
    $('.notebook-search').off('input.notebook').on('input.notebook', function () {
      state.query = String($(this).val() || '').trim();
      loadTree();
    });
    $('.notebook-sort').off('change.notebook').on('change.notebook', function () {
      state.sort = $(this).val() || 'order';
      state.order = state.sort === 'title' || state.sort === 'order' ? 'asc' : 'desc';
      loadTree();
    });
    $('.notebook-new-note').off('click.notebook').on('click.notebook', createNote);
    $('.notebook-new-folder').off('click.notebook').on('click.notebook', createFolder);
    $('.notebook-upload').off('click.notebook').on('click.notebook', function () {
      $('.notebook-file-input').trigger('click');
    });
    $('.notebook-file-input').off('change.notebook').on('change.notebook', function () {
      const file = this.files && this.files[0];
      this.value = '';
      uploadSelectedFile(file);
    });
    $('.notebook-tree').off('click.notebook').on('click.notebook', '.notebook-node', function (e) {
      const $node = $(this);
      state.selectedId = $node.attr('data-id');
      $('.notebook-node').removeClass('is-selected');
      $node.addClass('is-selected');
      if ($node.attr('data-type') === 'folder') {
        const id = $node.attr('data-id');
        state.collapsed[id] = !state.collapsed[id];
        renderTree();
      }
    });
    $('.notebook-tree').off('dblclick.notebook').on('dblclick.notebook', '.notebook-node', function () {
      const $node = $(this);
      if ($node.attr('data-type') === 'folder') {
        return;
      }
      openViewer($node.attr('data-id'));
    });
    $('.notebook-tree').off('contextmenu.notebook').on('contextmenu.notebook', '.notebook-node', function (e) {
      e.preventDefault();
      showContext(e, $(this));
    });
    $(document).off('click.notebookCtx').on('click.notebookCtx', function () {
      hideContext();
    });
    bindDrag($('.notebook-tree'));
    $(document).off('click.notebookPin').on('click.notebookPin', '.notebook-map-pin', function (e) {
      e.preventDefault();
      e.stopPropagation();
      const itemId = $(this).attr('data-item-id');
      if (itemId) {
        openViewer(itemId);
      }
    });
    $(document).off('contextmenu.notebookPin').on('contextmenu.notebookPin', '.notebook-map-pin', function (e) {
      e.preventDefault();
      e.stopPropagation();
      const linkId = $(this).attr('data-link-id');
      const itemId = $(this).attr('data-item-id');
      hideContext();
      const $menu = $(`
        <div class="notebook-ctx">
          <button type="button" data-act="open">Open note</button>
          <button type="button" data-act="unpin">Remove pin</button>
        </div>
      `);
      $menu.css({ left: e.pageX + 'px', top: e.pageY + 'px' });
      $menu.on('click', 'button', function () {
        const act = $(this).attr('data-act');
        hideContext();
        if (act === 'open' && itemId) {
          openViewer(itemId);
        } else if (act === 'unpin') {
          removeMapLink(linkId);
        }
      });
      $('body').append($menu);
    });
    $('.notebook-viewer-close, #notebook-overlay').off('click.notebook').on('click.notebook', function (e) {
      if (e.target === this || $(e.target).hasClass('notebook-viewer-close')) {
        closeViewer();
      }
    });
    $('.notebook-editor').off('input.notebook').on('input.notebook', function () {
      refreshPreview();
      scheduleSave();
    });
    $('.notebook-viewer-title').off('input.notebook').on('input.notebook', scheduleSave);
    $('.notebook-mode-edit').on('click', function () { setEditorMode('edit'); });
    $('.notebook-mode-split').on('click', function () { setEditorMode('split'); });
    $('.notebook-mode-preview').on('click', function () { setEditorMode('preview'); });
    $('.notebook-md-toolbar').on('click', '[data-md]', function () {
      const cmd = $(this).attr('data-md');
      if (cmd === 'bold') wrapSelection('**', '**');
      if (cmd === 'italic') wrapSelection('*', '*');
      if (cmd === 'h2') wrapSelection('\n## ', '\n');
      if (cmd === 'ul') wrapSelection('\n- ', '\n');
      if (cmd === 'quote') wrapSelection('\n> ', '\n');
      if (cmd === 'code') wrapSelection('`', '`');
      if (cmd === 'link') wrapSelection('[', '](url)');
    });
    $('.notebook-share-btn').on('click', function () {
      const $dlg = $('.notebook-share-dialog');
      $dlg.toggle();
      if ($dlg.is(':visible')) {
        loadShareRecipients();
      }
    });
    $('.notebook-share-cancel').on('click', function () {
      $('.notebook-share-dialog').hide();
    });
    $('.notebook-share-send').on('click', sendShare);
    $(document).off('keydown.notebook').on('keydown.notebook', function (e) {
      if (e.key === 'Escape' && $('#notebook-overlay').hasClass('is-open')) {
        closeViewer();
      }
    });
  }

  function init() {
    loadPrefs();
    state.isDm = isDmUser();
    ensureDom();
    bindUi();
    mountAiAssistant();
    setTab(state.tab);
    applyOpen();
    loadMapLinks();
  }

  window.Notebook = {
    init: init,
    toggle: function () { setOpen(!state.open); },
    open: function (tab) {
      if (tab) {
        state.tab = tab;
      }
      setOpen(true);
      setTab(state.tab);
    },
    close: function () { setOpen(false); },
    reload: function () {
      loadTree();
      loadMapLinks();
    },
    reloadMapLinks: loadMapLinks,
    handleMapDrop: handleMapDrop,
    showShared: showShared,
    renderMarkdown: renderMarkdown,
  };

  $(function () {
    init();
  });
})(window, jQuery);
