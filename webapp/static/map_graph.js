/* Campaign map relationship graph — teleporter edges between map nodes (edit mode). */
(function () {
  'use strict';

  const NODE_WIDTH = 200;
  const NODE_THUMB_HEIGHT = 110;
  const NODE_META_HEIGHT = 44;
  const NODE_HEIGHT = NODE_THUMB_HEIGHT + NODE_META_HEIGHT;
  const NODE_GAP_X = 56;
  const NODE_GAP_Y = 56;
  const CANVAS_PADDING = 80;

  let graph = { nodes: [], edges: [] };
  let nodePositions = {};
  let dragState = null;
  let panState = null;
  let suppressNodeClick = false;

  function readBootstrap() {
    const el = document.getElementById('map-graph-bootstrap');
    if (!el) return { nodes: [], edges: [] };
    try {
      return JSON.parse(el.textContent || '{}');
    } catch (err) {
      console.warn('Failed to parse map graph bootstrap', err);
      return { nodes: [], edges: [] };
    }
  }

  function nodeById(id) {
    return (graph.nodes || []).find(function (n) { return n.id === id; }) || null;
  }

  function gridPointOnNode(nodeEl, pos, mapSize) {
    const wrap = nodeEl.querySelector('.thumb-wrap');
    if (!wrap || !pos || !mapSize) return null;
    const nodeLeft = parseFloat(nodeEl.style.left) || 0;
    const nodeTop = parseFloat(nodeEl.style.top) || 0;
    const width = Math.max(1, mapSize[0] || 1);
    const height = Math.max(1, mapSize[1] || 1);
    const x = ((pos[0] + 0.5) / width) * NODE_WIDTH;
    const y = ((pos[1] + 0.5) / height) * NODE_THUMB_HEIGHT;
    return {
      x: nodeLeft + x,
      y: nodeTop + y,
    };
  }

  function initialLayout(nodes) {
    const count = nodes.length;
    const cols = Math.max(1, Math.ceil(Math.sqrt(count)));
    const cellW = NODE_WIDTH + NODE_GAP_X;
    const cellH = NODE_HEIGHT + NODE_GAP_Y;
    const positions = {};
    nodes.forEach(function (node, index) {
      const col = index % cols;
      const row = Math.floor(index / cols);
      positions[node.id] = {
        x: CANVAS_PADDING + col * cellW,
        y: CANVAS_PADDING + row * cellH,
      };
    });
    return positions;
  }

  function separateOverlappingNodes(positions, ids) {
    const pad = 20;
    for (let i = 0; i < ids.length; i += 1) {
      for (let j = i + 1; j < ids.length; j += 1) {
        const a = positions[ids[i]];
        const b = positions[ids[j]];
        const dx = (b.x + NODE_WIDTH / 2) - (a.x + NODE_WIDTH / 2);
        const dy = (b.y + NODE_HEIGHT / 2) - (a.y + NODE_HEIGHT / 2);
        const overlapX = (NODE_WIDTH + pad) - Math.abs(dx);
        const overlapY = (NODE_HEIGHT + pad) - Math.abs(dy);
        if (overlapX > 0 && overlapY > 0) {
          if (overlapX < overlapY) {
            const shift = overlapX / 2 + 1;
            const dir = dx >= 0 ? 1 : -1;
            a.x -= shift * dir;
            b.x += shift * dir;
          } else {
            const shift = overlapY / 2 + 1;
            const dir = dy >= 0 ? 1 : -1;
            a.y -= shift * dir;
            b.y += shift * dir;
          }
        }
      }
    }
  }

  function runForceLayout(nodes, edges, positions, iterations) {
    const repulsion = 42000;
    const attraction = 0.012;
    const ids = nodes.map(function (n) { return n.id; });

    for (let step = 0; step < iterations; step += 1) {
      const forces = {};
      ids.forEach(function (id) {
        forces[id] = { x: 0, y: 0 };
      });

      for (let i = 0; i < ids.length; i += 1) {
        for (let j = i + 1; j < ids.length; j += 1) {
          const a = ids[i];
          const b = ids[j];
          const pa = positions[a];
          const pb = positions[b];
          let dx = pb.x - pa.x;
          let dy = pb.y - pa.y;
          let distSq = dx * dx + dy * dy;
          if (distSq < 1) {
            dx = (Math.random() - 0.5) * 2;
            dy = (Math.random() - 0.5) * 2;
            distSq = dx * dx + dy * dy || 1;
          }
          const force = repulsion / distSq;
          const dist = Math.sqrt(distSq);
          dx /= dist;
          dy /= dist;
          forces[a].x -= dx * force;
          forces[a].y -= dy * force;
          forces[b].x += dx * force;
          forces[b].y += dy * force;
        }
      }

      (edges || []).forEach(function (edge) {
        const a = edge.from_map;
        const b = edge.to_map;
        if (!positions[a] || !positions[b] || a === b) return;
        const pa = positions[a];
        const pb = positions[b];
        const dx = pb.x - pa.x;
        const dy = pb.y - pa.y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 1;
        const desired = NODE_WIDTH + NODE_GAP_X;
        const force = (dist - desired) * attraction;
        forces[a].x += (dx / dist) * force;
        forces[a].y += (dy / dist) * force;
        forces[b].x -= (dx / dist) * force;
        forces[b].y -= (dy / dist) * force;
      });

      ids.forEach(function (id) {
        const p = positions[id];
        p.x += forces[id].x;
        p.y += forces[id].y;
        p.x = Math.max(CANVAS_PADDING, p.x);
        p.y = Math.max(CANVAS_PADDING, p.y);
      });

      separateOverlappingNodes(positions, ids);
    }
  }

  function resizeCanvas() {
    const canvas = document.getElementById('map-graph-canvas');
    const svg = document.getElementById('map-graph-edges');
    if (!canvas || !svg) return;

    let maxX = 400;
    let maxY = 300;
    Object.keys(nodePositions).forEach(function (id) {
      const p = nodePositions[id];
      maxX = Math.max(maxX, p.x + NODE_WIDTH + CANVAS_PADDING);
      maxY = Math.max(maxY, p.y + NODE_HEIGHT + CANVAS_PADDING);
    });
    canvas.style.width = maxX + 'px';
    canvas.style.height = maxY + 'px';
    svg.setAttribute('width', String(maxX));
    svg.setAttribute('height', String(maxY));
  }

  function drawGridOverlay(canvas, node, edges) {
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const width = canvas.width;
    const height = canvas.height;
    ctx.clearRect(0, 0, width, height);

    const mapW = Math.max(1, node.width || 1);
    const mapH = Math.max(1, node.height || 1);

    ctx.strokeStyle = 'rgba(255,255,255,0.08)';
    ctx.lineWidth = 1;
    const stepX = width / mapW;
    const stepY = height / mapH;
    for (let x = 0; x <= mapW; x += 1) {
      ctx.beginPath();
      ctx.moveTo(x * stepX, 0);
      ctx.lineTo(x * stepX, height);
      ctx.stroke();
    }
    for (let y = 0; y <= mapH; y += 1) {
      ctx.beginPath();
      ctx.moveTo(0, y * stepY);
      ctx.lineTo(width, y * stepY);
      ctx.stroke();
    }

    function drawDot(pos, color) {
      if (!pos) return;
      const px = ((pos[0] + 0.5) / mapW) * width;
      const py = ((pos[1] + 0.5) / mapH) * height;
      ctx.beginPath();
      ctx.fillStyle = color;
      ctx.arc(px, py, 5, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = '#111';
      ctx.lineWidth = 1.5;
      ctx.stroke();
    }

    (edges || []).forEach(function (edge) {
      if (edge.from_map === node.id) {
        drawDot(edge.from_pos, '#e67e22');
      }
      if (edge.to_map === node.id) {
        drawDot(edge.to_pos, '#5cb85c');
      }
    });
  }

  function renderNodes() {
    const canvas = document.getElementById('map-graph-canvas');
    if (!canvas) return;

    canvas.querySelectorAll('.map-graph-node').forEach(function (el) { el.remove(); });

    (graph.nodes || []).forEach(function (node) {
      const pos = nodePositions[node.id] || { x: 40, y: 40 };
      const el = document.createElement('div');
      el.className = 'map-graph-node' + (node.is_current ? ' current' : '');
      el.dataset.mapId = node.id;
      el.style.left = pos.x + 'px';
      el.style.top = pos.y + 'px';

      const thumbWrap = document.createElement('div');
      thumbWrap.className = 'thumb-wrap';
      const img = document.createElement('img');
      img.className = 'thumb';
      img.alt = node.label;
      img.src = node.background_url || '/static/info.png';
      img.onerror = function () {
        this.onerror = null;
        this.src = '/static/info.png';
      };
      const overlay = document.createElement('canvas');
      overlay.className = 'grid-overlay';
      overlay.width = NODE_WIDTH;
      overlay.height = NODE_THUMB_HEIGHT;
      thumbWrap.appendChild(img);
      thumbWrap.appendChild(overlay);

      const label = document.createElement('div');
      label.className = 'label';
      label.textContent = node.label;

      const meta = document.createElement('div');
      meta.className = 'meta';
      meta.textContent = (node.width && node.height)
        ? node.width + ' × ' + node.height + ' tiles'
        : 'Map';

      el.appendChild(thumbWrap);
      el.appendChild(label);
      el.appendChild(meta);
      canvas.appendChild(el);

      drawGridOverlay(overlay, node, graph.edges || []);
    });
  }

  function ensureEdgesOnTop() {
    const canvas = document.getElementById('map-graph-canvas');
    const svg = document.getElementById('map-graph-edges');
    if (canvas && svg) {
      canvas.appendChild(svg);
    }
  }

  function addEdgeEndpoint(svg, point, fill) {
    const halo = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    halo.setAttribute('cx', String(point.x));
    halo.setAttribute('cy', String(point.y));
    halo.setAttribute('r', '9');
    halo.setAttribute('fill', 'rgba(8, 10, 14, 0.9)');
    svg.appendChild(halo);

    const dot = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    dot.setAttribute('cx', String(point.x));
    dot.setAttribute('cy', String(point.y));
    dot.setAttribute('r', '5.5');
    dot.setAttribute('fill', fill);
    dot.setAttribute('stroke', '#f4f7fb');
    dot.setAttribute('stroke-width', '1.75');
    svg.appendChild(dot);
  }

  function createArrowMarker(defs) {
    const marker = document.createElementNS('http://www.w3.org/2000/svg', 'marker');
    marker.setAttribute('id', 'map-graph-arrow');
    marker.setAttribute('viewBox', '0 0 12 12');
    marker.setAttribute('markerWidth', '12');
    marker.setAttribute('markerHeight', '12');
    marker.setAttribute('refX', '10.5');
    marker.setAttribute('refY', '6');
    marker.setAttribute('markerUnits', 'userSpaceOnUse');
    marker.setAttribute('orient', 'auto');
    const arrowPath = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    arrowPath.setAttribute('d', 'M1 1 L11 6 L1 11 Z');
    arrowPath.setAttribute('fill', '#7ec8ff');
    arrowPath.setAttribute('stroke', 'rgba(8, 10, 14, 0.95)');
    arrowPath.setAttribute('stroke-width', '1.25');
    arrowPath.setAttribute('stroke-linejoin', 'round');
    marker.appendChild(arrowPath);
    defs.appendChild(marker);
  }

  function appendEdgePath(svg, d, markerEnd) {
    const underlay = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    underlay.setAttribute('d', d);
    underlay.setAttribute('class', 'edge-path-underlay');
    underlay.setAttribute('fill', 'none');
    underlay.setAttribute('stroke', 'rgba(8, 10, 14, 0.92)');
    underlay.setAttribute('stroke-width', '7');
    underlay.setAttribute('stroke-linecap', 'round');
    underlay.setAttribute('stroke-linejoin', 'round');
    svg.appendChild(underlay);

    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', d);
    path.setAttribute('class', 'edge-path');
    path.setAttribute('fill', 'none');
    path.setAttribute('stroke', '#7ec8ff');
    path.setAttribute('stroke-width', '2.75');
    path.setAttribute('stroke-linecap', 'round');
    path.setAttribute('stroke-linejoin', 'round');
    if (markerEnd) {
      path.setAttribute('marker-end', markerEnd);
    }
    svg.appendChild(path);
    return path;
  }

  function renderEdges() {
    const svg = document.getElementById('map-graph-edges');
    if (!svg) return;
    while (svg.firstChild) svg.removeChild(svg.firstChild);

    const defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');
    createArrowMarker(defs);
    svg.appendChild(defs);

    (graph.edges || []).forEach(function (edge) {
      const fromNode = document.querySelector('.map-graph-node[data-map-id="' + edge.from_map + '"]');
      const toNode = document.querySelector('.map-graph-node[data-map-id="' + edge.to_map + '"]');
      if (!fromNode || !toNode) return;

      const fromMap = nodeById(edge.from_map);
      const toMap = nodeById(edge.to_map);
      const start = gridPointOnNode(fromNode, edge.from_pos, [fromMap.width, fromMap.height]);
      const end = gridPointOnNode(toNode, edge.to_pos, [toMap.width, toMap.height]);
      if (!start || !end) return;

      const dx = end.x - start.x;
      const dy = end.y - start.y;
      const dist = Math.sqrt(dx * dx + dy * dy) || 1;
      const bend = Math.min(120, dist * 0.28);
      const cx1 = start.x + dx * 0.35;
      const cy1 = start.y + (dy >= 0 ? -bend : bend);
      const cx2 = end.x - dx * 0.35;
      const cy2 = end.y + (dy >= 0 ? bend : -bend);
      const trim = 10;
      const endX = end.x - (dx / dist) * trim;
      const endY = end.y - (dy / dist) * trim;
      const d = 'M' + start.x + ',' + start.y
        + ' C' + cx1 + ',' + cy1 + ' ' + cx2 + ',' + cy2 + ' ' + endX + ',' + endY;

      appendEdgePath(svg, d, 'url(#map-graph-arrow)');

      addEdgeEndpoint(svg, start, '#e67e22');
      addEdgeEndpoint(svg, end, '#5cb85c');

      const labelText = (edge.label || edge.kind || 'link')
        + ' (' + edge.from_pos.join(',') + ' → ' + edge.to_pos.join(',') + ')';
      const midX = (start.x + end.x) / 2;
      const midY = (start.y + end.y) / 2 - 10;
      const text = document.createElementNS('http://www.w3.org/2000/svg', 'text');
      text.setAttribute('x', String(midX));
      text.setAttribute('y', String(midY));
      text.setAttribute('text-anchor', 'middle');
      text.setAttribute('class', 'edge-label');
      text.setAttribute('fill', '#e8eef5');
      text.setAttribute('stroke', 'rgba(8, 10, 14, 0.95)');
      text.setAttribute('stroke-width', '4');
      text.setAttribute('paint-order', 'stroke');
      text.textContent = labelText;
      svg.appendChild(text);
    });
  }

  function render() {
    nodePositions = initialLayout(graph.nodes || []);
    runForceLayout(graph.nodes || [], graph.edges || [], nodePositions, 80);
    separateOverlappingNodes(nodePositions, (graph.nodes || []).map(function (n) { return n.id; }));
    resizeCanvas();
    renderNodes();
    ensureEdgesOnTop();
    renderEdges();
    fitView();
  }

  function fitView() {
    const stage = document.getElementById('map-graph-stage');
    const canvas = document.getElementById('map-graph-canvas');
    if (!stage || !canvas) return;

    let minX = Infinity;
    let minY = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    Object.keys(nodePositions).forEach(function (id) {
      const p = nodePositions[id];
      minX = Math.min(minX, p.x);
      minY = Math.min(minY, p.y);
      maxX = Math.max(maxX, p.x + NODE_WIDTH);
      maxY = Math.max(maxY, p.y + NODE_HEIGHT);
    });
    if (!isFinite(minX)) {
      stage.scrollLeft = 0;
      stage.scrollTop = 0;
      return;
    }

    const pad = 40;
    const contentW = maxX - minX + pad * 2;
    const contentH = maxY - minY + pad * 2;
    stage.scrollLeft = Math.max(0, minX - pad);
    stage.scrollTop = Math.max(0, minY - pad);
    if (contentW > stage.clientWidth || contentH > stage.clientHeight) {
      stage.scrollLeft = Math.max(0, minX - (stage.clientWidth - (maxX - minX)) / 2);
      stage.scrollTop = Math.max(0, minY - (stage.clientHeight - (maxY - minY)) / 2);
    }
  }

  function navigateToMap(mapId) {
    if (!mapId) return;

    if (window.opener && !window.opener.closed) {
      try {
        window.opener.postMessage({ type: 'n20_switch_map', map: mapId }, window.location.origin);
        window.opener.focus();
        return;
      } catch (err) {
        console.warn('postMessage to opener failed', err);
      }
    }

    const body = 'map=' + encodeURIComponent(mapId);
    fetch('/switch_map', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8' },
      body: body,
    })
      .then(function (resp) {
        if (!resp.ok) throw new Error('switch_map failed');
        window.location.href = '/';
      })
      .catch(function (err) {
        console.error(err);
        alert('Unable to switch map. Open the main VTT and try again.');
      });
  }

  function refreshGraph() {
    fetch('/edit/map_graph/data', { credentials: 'same-origin' })
      .then(function (resp) { return resp.json(); })
      .then(function (data) {
        if (!data || !data.success) {
          throw new Error((data && data.error) || 'Failed to load map graph');
        }
        graph = data;
        render();
      })
      .catch(function (err) {
        console.error(err);
        alert('Failed to refresh map graph.');
      });
  }

  function setupInteractions() {
    const canvas = document.getElementById('map-graph-canvas');
    const stage = document.getElementById('map-graph-stage');
    if (!canvas || !stage) return;

    canvas.addEventListener('click', function (ev) {
      const nodeEl = ev.target.closest('.map-graph-node');
      if (!nodeEl || suppressNodeClick) {
        suppressNodeClick = false;
        return;
      }
      navigateToMap(nodeEl.dataset.mapId);
    });

    canvas.addEventListener('mousedown', function (ev) {
      const nodeEl = ev.target.closest('.map-graph-node');
      if (nodeEl && ev.button === 0) {
        dragState = {
          id: nodeEl.dataset.mapId,
          startX: ev.clientX,
          startY: ev.clientY,
          origin: { x: nodePositions[nodeEl.dataset.mapId].x, y: nodePositions[nodeEl.dataset.mapId].y },
          moved: false,
        };
        ev.preventDefault();
        return;
      }
      if (ev.button === 0) {
        panState = { startX: ev.clientX, startY: ev.clientY, scrollLeft: stage.scrollLeft, scrollTop: stage.scrollTop };
        stage.classList.add('dragging');
      }
    });

    window.addEventListener('mousemove', function (ev) {
      if (dragState) {
        const dx = ev.clientX - dragState.startX;
        const dy = ev.clientY - dragState.startY;
        if (Math.abs(dx) > 3 || Math.abs(dy) > 3) dragState.moved = true;
        nodePositions[dragState.id] = {
          x: dragState.origin.x + dx,
          y: dragState.origin.y + dy,
        };
        const nodeEl = canvas.querySelector('.map-graph-node[data-map-id="' + dragState.id + '"]');
        if (nodeEl) {
          nodeEl.style.left = nodePositions[dragState.id].x + 'px';
          nodeEl.style.top = nodePositions[dragState.id].y + 'px';
        }
        resizeCanvas();
        renderEdges();
        return;
      }
      if (panState) {
        stage.scrollLeft = panState.scrollLeft - (ev.clientX - panState.startX);
        stage.scrollTop = panState.scrollTop - (ev.clientY - panState.startY);
      }
    });

    window.addEventListener('mouseup', function () {
      if (dragState && dragState.moved) {
        suppressNodeClick = true;
      }
      dragState = null;
      panState = null;
      stage.classList.remove('dragging');
    });

    document.getElementById('map-graph-refresh').addEventListener('click', refreshGraph);
    document.getElementById('map-graph-fit').addEventListener('click', fitView);
  }

  document.addEventListener('DOMContentLoaded', function () {
    graph = readBootstrap();
    if (!graph.nodes || !graph.nodes.length) {
      const canvas = document.getElementById('map-graph-canvas');
      if (canvas) {
        canvas.innerHTML = '<div class="map-graph-empty">No maps registered in this campaign.</div>';
      }
      return;
    }
    render();
    setupInteractions();
  });
})();
