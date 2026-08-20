/** Screen-space notes and object-interact chips for the 3D VTT. */

function noteHtml(notes) {
  return (notes || []).map((note) => {
    const bits = [];
    if (note.perception_dc) bits.push(`<p class="note-dc-badge">Perception DC ${note.perception_dc}</p>`);
    if (note.religion_dc) bits.push(`<p class="note-dc-badge">Religion DC ${note.religion_dc}</p>`);
    if (note.arcana_dc) bits.push(`<p class="note-dc-badge">Arcana DC ${note.arcana_dc}</p>`);
    if (note.image) {
      bits.push(`<img class="note-image" src="/static/assets/objects/${note.image}" alt="">`);
    }
    if (note.note) bits.push(`<p>${escapeHtml(note.note)}</p>`);
    return bits.join('');
  }).join('');
}

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function actionIcon(qa) {
  const glyph = `<i class="glyphicon glyphicon-${escapeHtml(qa.icon || 'wrench')}"></i>`;
  if (!qa.image) {
    if (qa.show_label) return '';
    return glyph;
  }
  return `<img class="object-quick-action-icon" src="/static/actions/${encodeURIComponent(qa.image)}.png" alt="" aria-hidden="true" data-fallback-icon="${escapeHtml(qa.icon || 'wrench')}">`;
}

export function createObjectHud({ layer, scene, onNote, onInteract }) {
  if (!layer) {
    return { rebuild() {}, setHover() {}, project() {} };
  }
  const notesRoot = document.createElement('div');
  notesRoot.className = 'hud-notes-root';
  const interactRoot = document.createElement('div');
  interactRoot.className = 'hud-interact-root';
  layer.appendChild(notesRoot);
  layer.appendChild(interactRoot);

  let noteMarkers = [];
  let hover = null;

  function rebuild(objects) {
    notesRoot.innerHTML = '';
    noteMarkers = [];
    for (const obj of objects || []) {
      if (!obj.notes || !obj.notes.length) continue;
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'show-note-btn hud-note-btn';
      btn.innerHTML = '<i class="glyphicon glyphicon-info-sign"></i>';
      btn.title = obj.label || obj.name || 'Notes';
      btn.addEventListener('click', (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        onNote?.({
          title: obj.label || obj.name || 'Notes',
          html: noteHtml(obj.notes),
          id: obj.id,
        });
      });
      notesRoot.appendChild(btn);
      noteMarkers.push({ el: btn, obj });
    }
  }

  function setHover(cell, objects) {
    const key = cell ? `${cell.x},${cell.y}` : '';
    if (hover && hover.key === key) return;
    if (!cell) {
      hover = null;
      interactRoot.innerHTML = '';
      return;
    }
    const matches = (objects || []).filter((obj) => (
      Number(obj.x) === cell.x && Number(obj.y) === cell.y && obj.quick_interact?.length
    ));
    hover = { key, cell, objects: matches };
    interactRoot.innerHTML = '';
    if (!matches.length) return;
    const wrap = document.createElement('div');
    wrap.className = 'hud-object-interact';
    for (const obj of matches) {
      for (const qa of obj.quick_interact) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = `object-quick-action${qa.disabled ? ' is-disabled' : ''}${qa.show_label ? ' object-quick-action--labeled' : ''}`;
        btn.title = qa.label || qa.action;
        btn.disabled = Boolean(qa.disabled);
        btn.innerHTML = `${actionIcon(qa)}${qa.show_label ? `<span class="object-quick-action-label">${escapeHtml(qa.label || qa.action)}</span>` : ''}`;
        btn.addEventListener('click', (ev) => {
          ev.preventDefault();
          ev.stopPropagation();
          onInteract?.({ obj, qa });
        });
        wrap.appendChild(btn);
      }
    }
    wrap.querySelectorAll('img.object-quick-action-icon').forEach((img) => {
      img.addEventListener('error', () => {
        const icon = img.getAttribute('data-fallback-icon') || 'wrench';
        const glyph = document.createElement('i');
        glyph.className = `glyphicon glyphicon-${icon}`;
        img.replaceWith(glyph);
      });
    });
    interactRoot.appendChild(wrap);
  }

  function project() {
    for (const marker of noteMarkers) {
      const p = scene.projectCell(marker.obj.x, marker.obj.y, 0.85);
      marker.el.style.display = p.behind ? 'none' : 'block';
      marker.el.style.left = `${p.x}px`;
      marker.el.style.top = `${p.y}px`;
    }
    const toolbar = interactRoot.firstElementChild;
    if (toolbar && hover?.cell) {
      const p = scene.projectCell(hover.cell.x, hover.cell.y, 1.05);
      toolbar.style.display = p.behind ? 'none' : 'flex';
      toolbar.style.left = `${p.x}px`;
      toolbar.style.top = `${p.y}px`;
    }
  }

  return { rebuild, setHover, project };
}
