// DM-only "Figures" panel for the 3D VTT: upload HeroForge-style models and
// bind them to entities (uid / NPC type / name) via /dm/token_models*.
//
// The panel is plain DOM built at runtime; index_3d.html only provides a
// trigger button and the #tokenModelModal shell.

const API = '/dm/token_models';

function fmtBytes(n) {
  const v = Number(n) || 0;
  if (v > 1024 * 1024) return `${(v / 1024 / 1024).toFixed(1)} MB`;
  if (v > 1024) return `${(v / 1024).toFixed(0)} KB`;
  return `${v} B`;
}

async function api(path, options = {}) {
  const res = await fetch(`${API}${path}`, {
    credentials: 'include',
    ...options,
  });
  let body = null;
  try {
    body = await res.json();
  } catch {
    body = { error: `HTTP ${res.status}` };
  }
  if (!res.ok || (body && body.error)) {
    throw new Error((body && body.error) || `HTTP ${res.status}`);
  }
  return body || {};
}

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
}

export function initTokenModelPanel({ isDm } = {}) {
  const button = document.getElementById('vtt3d-token-models');
  const modal = document.getElementById('tokenModelModal');
  if (!modal) return null;
  if (!isDm || !isDm()) {
    if (button) button.style.display = 'none';
    return null;
  }

  const body = modal.querySelector('#tokenModelModalContent') || modal;
  let lastData = null;

  function setStatus(text, isError) {
    const box = modal.querySelector('.t3d-tm-status');
    if (box) {
      box.textContent = text || '';
      box.classList.toggle('t3d-tm-status--error', Boolean(isError));
    }
  }

  function close() {
    modal.hidden = true;
  }

  async function load() {
    setStatus('Loading…');
    try {
      lastData = await api('');
      render();
      setStatus('');
    } catch (err) {
      setStatus(`Could not load figures: ${err.message}`, true);
    }
  }

  function render() {
    body.innerHTML = '';
    body.appendChild(renderUpload());
    body.appendChild(renderAssignments());
    body.appendChild(renderLibrary());
  }

  function keyInput(existing = '') {
    const row = el('div', 't3d-tm-keyrow');
    const input = el('input', 't3d-tm-key');
    input.type = 'text';
    input.placeholder = 'entity uid, NPC type, or name';
    input.value = existing;
    const select = el('select', 't3d-tm-entity');
    select.add(new Option('pick an entity…', ''));
    (lastData?.entities || []).forEach((e) => {
      const label = `${e.name}${e.type && e.type !== e.name ? ` (${e.type})` : ''}`;
      select.add(new Option(label, e.uid));
      if (e.type && !select.querySelector(`option[value="${CSS.escape(e.type)}"]`)) {
        select.add(new Option(`${label} — all ${e.type}`, e.type));
      }
    });
    select.addEventListener('change', () => {
      if (select.value) input.value = select.value;
    });
    row.append(select, input);
    return { row, input };
  }

  function renderUpload() {
    const box = el('div', 't3d-tm-section');
    box.appendChild(el('h3', null, 'Upload figure'));
    const form = el('div', 't3d-tm-form');
    const file = el('input');
    file.type = 'file';
    file.accept = '.glb,.gltf,.obj,.stl,.unity3d,.zip';
    file.multiple = false;
    const color = el('input');
    color.type = 'color';
    color.title = 'Tint for mono (STL) figures';
    const { row: keyRow, input: keyInputEl } = keyInput();
    const btn = el('button', 'btn btn-primary btn-sm', 'Upload');
    const note = el('div', 't3d-tm-note',
      'GLB/OBJ render with color. STL and legacy HeroForge .unity3d bundles '
      + 'are converted automatically (STL uses the tint color).');
    btn.addEventListener('click', async () => {
      if (!file.files || !file.files.length) {
        setStatus('Choose a model file first', true);
        return;
      }
      btn.disabled = true;
      setStatus('Uploading and converting…');
      try {
        const form = new FormData();
        form.append('model', file.files[0]);
        if (color.value && color.value !== '#000000') form.append('color', color.value);
        if (keyInputEl.value.trim()) form.append('key', keyInputEl.value.trim());
        await api('/upload', { method: 'POST', body: form });
        setStatus('');
        await load();
      } catch (err) {
        setStatus(`Upload failed: ${err.message}`, true);
      } finally {
        btn.disabled = false;
      }
    });
    form.append(file, color, keyRow, btn, note);
    box.append(form);
    return box;
  }

  function renderAssignments() {
    const box = el('div', 't3d-tm-section');
    box.appendChild(el('h3', null, 'Bound figures'));
    const assignments = lastData?.assignments || {};
    const keys = Object.keys(assignments);
    if (!keys.length) {
      box.appendChild(el('div', 't3d-tm-note', 'Nothing bound yet. Upload a model and bind it below.'));
    }
    keys.forEach((key) => {
      const spec = assignments[key];
      const row = el('div', 't3d-tm-row');
      row.append(el('span', 't3d-tm-keylabel', key));
      row.append(el('span', 't3d-tm-speclabel', spec.src || ''));
      const height = el('input', 't3d-tm-num');
      height.type = 'number';
      height.min = '1';
      height.step = '0.5';
      height.title = 'Height (feet)';
      height.value = spec.height ?? '';
      const rot = el('input', 't3d-tm-num');
      rot.type = 'number';
      rot.step = '15';
      rot.title = 'Rotate (degrees)';
      rot.value = spec.rotate_y ?? '';
      const tint = el('input');
      tint.type = 'color';
      tint.title = 'Tint';
      if (/^#[0-9a-f]{6}$/i.test(spec.color || '')) tint.value = spec.color;
      const save = el('button', 'btn btn-default btn-sm', 'Save');
      save.addEventListener('click', async () => {
        try {
          await api('/assign', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              key,
              src: spec.src,
              height: height.value === '' ? undefined : Number(height.value),
              rotate_y: rot.value === '' ? undefined : Number(rot.value),
              color: tint.value,
            }),
          });
          await load();
        } catch (err) {
          setStatus(`Save failed: ${err.message}`, true);
        }
      });
      const remove = el('button', 'btn btn-default btn-sm', 'Unbind');
      remove.addEventListener('click', async () => {
        try {
          await api('/unassign', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key }),
          });
          await load();
        } catch (err) {
          setStatus(`Unbind failed: ${err.message}`, true);
        }
      });
      row.append(height, rot, tint, save, remove);
      box.appendChild(row);
    });
    return box;
  }

  function renderLibrary() {
    const box = el('div', 't3d-tm-section');
    box.appendChild(el('h3', null, 'Model library'));
    const library = lastData?.library || [];
    if (!library.length) {
      box.appendChild(el('div', 't3d-tm-note', 'No model files uploaded yet.'));
      return box;
    }
    library.forEach((entry) => {
      const row = el('div', 't3d-tm-row');
      row.append(el('span', 't3d-tm-speclabel', entry.name));
      row.append(el('span', 't3d-tm-bytes', fmtBytes(entry.bytes)));
      const { row: keyRow, input: keyInputEl } = keyInput();
      const bind = el('button', 'btn btn-default btn-sm', 'Bind');
      bind.addEventListener('click', async () => {
        if (!keyInputEl.value.trim()) {
          setStatus('Pick or type what to bind this model to', true);
          return;
        }
        try {
          await api('/assign', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key: keyInputEl.value.trim(), src: entry.src }),
          });
          await load();
        } catch (err) {
          setStatus(`Bind failed: ${err.message}`, true);
        }
      });
      const del = el('button', 'btn btn-default btn-sm', 'Delete');
      del.addEventListener('click', async () => {
        if (!window.confirm(`Delete ${entry.name}? Bound figures are unbound.`)) return;
        try {
          await api('/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ src: entry.src }),
          });
          await load();
        } catch (err) {
          setStatus(`Delete failed: ${err.message}`, true);
        }
      });
      row.append(keyRow, bind, del);
      box.appendChild(row);
    });
    return box;
  }

  button?.addEventListener('click', () => {
    modal.hidden = false;
    load();
  });
  modal.querySelector('#tokenModelModalClose')?.addEventListener('click', close);
  modal.addEventListener('click', (ev) => {
    if (ev.target === modal) close();
  });

  return { reload: load, close };
}
