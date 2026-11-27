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

export function createObjectHud({ layer, scene, onNote, onInteract, onMove, onMoveEnter, onMoveLeave }) {
  if (!layer) {
    return { rebuild() {}, setHover() {}, setMoveTarget() {}, project() {} };
  }
  const notesRoot = document.createElement('div');
  notesRoot.className = 'hud-notes-root';
  const interactRoot = document.createElement('div');
  interactRoot.className = 'hud-interact-root';
  const moveRoot = document.createElement('div');
  moveRoot.className = 'hud-move-root';
  layer.appendChild(notesRoot);
  layer.appendChild(interactRoot);
  layer.appendChild(moveRoot);

  let noteMarkers = [];
  let hover = null;
  // Hover stickiness: the interact chips are DOM buttons floating over the
  // canvas; moving the cursor from the hovered cell onto a button briefly
  // leaves the trigger cell (very visible when zoomed in). Keep the current
  // chips alive for a short grace period, pin them while the pointer is on
  // the chip strip, and apply the newest hover once it leaves.
  const HOVER_GRACE_MS = 280;
  let pinned = false;
  let pendingHover = null;
  let graceTimer = null;

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
    if (pinned) {
      pendingHover = { cell, objects };
      return;
    }
    if (hover && hover.hasButtons) {
      // buttons are on screen: remember the newest hover and keep the strip
      // alive briefly, so stepping a pixel off the cell (or travelling onto
      // the strip itself) does not yank the buttons out from under the cursor
      pendingHover = { cell, objects };
      if (!graceTimer) {
        graceTimer = setTimeout(() => {
          graceTimer = null;
          if (!pinned && pendingHover) {
            const p = pendingHover;
            applyHover(p.cell, p.objects);
          }
        }, HOVER_GRACE_MS);
      }
      return;
    }
    applyHover(cell, objects);
  }

  function applyHover(cell, objects) {
    if (graceTimer) { clearTimeout(graceTimer); graceTimer = null; }
    pendingHover = null;
    const key = cell ? `${cell.x},${cell.y}` : '';
    if (!cell) {
      hover = null;
      interactRoot.innerHTML = '';
      return;
    }
    const matches = (objects || []).filter((obj) => (
      Number(obj.x) === cell.x && Number(obj.y) === cell.y && obj.quick_interact?.length
    ));
    hover = { key, cell, objects: matches, hasButtons: matches.length > 0 };
    interactRoot.innerHTML = '';
    if (!matches.length) return;
    const wrap = document.createElement('div');
    wrap.className = 'hud-object-interact';
    wrap.addEventListener('pointerenter', () => { pinned = true; });
    wrap.addEventListener('pointerleave', () => {
      pinned = false;
      if (pendingHover) {
        const p = pendingHover;
        applyHover(p.cell, p.objects);
      }
    });
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

  // 2D-style hover-move: a "Move here" chip on reachable empty squares.
  // Same pin/grace behavior as the object chips so the button stays
  // reachable while the cursor walks onto it.
  let moveCell = null;
  let movePinned = false;
  let movePending;
  let moveGraceTimer = null;

  function setMoveTarget(cell) {
    const key = cell ? `${Number(cell.x)},${Number(cell.y)}` : '';
    const cur = moveCell ? `${moveCell.x},${moveCell.y}` : '';
    if (key === cur && movePending === undefined) return;
    if (movePinned) {
      movePending = cell || null;
      return;
    }
    if (moveCell && !moveGraceTimer) {
      movePending = cell || null;
      moveGraceTimer = setTimeout(() => {
        moveGraceTimer = null;
        if (!movePinned) {
          const p = movePending;
          movePending = undefined;
          applyMoveTarget(p);
        }
      }, HOVER_GRACE_MS);
      return;
    }
    applyMoveTarget(cell);
  }

  function applyMoveTarget(cell) {
    if (moveGraceTimer) { clearTimeout(moveGraceTimer); moveGraceTimer = null; }
    movePending = undefined;
    moveCell = cell ? { x: Number(cell.x), y: Number(cell.y) } : null;
    moveRoot.innerHTML = '';
    onMoveLeave?.();
    if (!moveCell) return;
    const wrap = document.createElement('div');
    wrap.className = 'hud-object-interact hud-move-interact';
    wrap.addEventListener('pointerenter', () => {
      movePinned = true;
      onMoveEnter?.({ x: moveCell.x, y: moveCell.y });
    });
    wrap.addEventListener('pointerleave', () => {
      movePinned = false;
      onMoveLeave?.();
      if (movePending !== undefined) {
        const p = movePending;
        movePending = undefined;
        applyMoveTarget(p);
      }
    });
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'object-quick-action move-quick-action';
    btn.title = 'Move here';
    btn.innerHTML = '<i class="glyphicon glyphicon-move" aria-hidden="true"></i>';
    btn.addEventListener('click', (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      onMove?.({ x: moveCell.x, y: moveCell.y });
    });
    wrap.appendChild(btn);
    moveRoot.appendChild(wrap);
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
      // anchor near the floor of the hovered cell: at high zoom the elevated
      // projection lands over a different square than the hover trigger,
      // leaving the buttons "far" from where the cursor activated them
      const p = scene.projectCell(hover.cell.x, hover.cell.y, 0.55);
      toolbar.style.display = p.behind ? 'none' : 'flex';
      toolbar.style.left = `${p.x}px`;
      toolbar.style.top = `${p.y}px`;
    }
    const moveBar = moveRoot.firstElementChild;
    if (moveBar && moveCell) {
      const p = scene.projectCell(moveCell.x, moveCell.y, 0.55);
      moveBar.style.display = p.behind ? 'none' : 'flex';
      moveBar.style.left = `${p.x}px`;
      moveBar.style.top = `${p.y}px`;
    }
  }

  return { rebuild, setHover, setMoveTarget, project };
}
