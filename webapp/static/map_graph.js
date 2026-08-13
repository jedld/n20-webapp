/* Campaign map relationship graph — teleporter edges between map nodes (edit mode). */
(function () {
  'use strict';

  // Fixed node width; thumbnail height scales to preserve map aspect ratio
  const NODE_WIDTH = 200;
  const MIN_THUMB_HEIGHT = 60;
  const MAX_THUMB_HEIGHT = 280;
  const NODE_META_HEIGHT = 44;
  const NODE_GAP_X = 56;
  const NODE_GAP_Y = 56;
  const CANVAS_PADDING = 80;

  let graph = { nodes: [], edges: [] };
  let nodePositions = {};
  let nodeSizes = {};  // { id: { width, thumbHeight, totalHeight } }
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

  // Compute thumb height for a map so that tiles render as squares
  function computeThumbHeight(node) {
    var mapW = Math.max(1, node.width || 1);
    var mapH = Math.max(1, node.height || 1);
    // tilePixelSize keeps tiles square; thumbHeight = mapH * tilePixelSize
    var tilePixelSize = NODE_WIDTH / mapW;
    var thumbHeight = Math.round(mapH * tilePixelSize);
    return Math.max(MIN_THUMB_HEIGHT, Math.min(MAX_THUMB_HEIGHT, thumbHeight));
  }

  function getNodeSize(node) {
    if (nodeSizes[node.id]) return nodeSizes[node.id];
    var thumbHeight = computeThumbHeight(node);
    var totalHeight = thumbHeight + NODE_META_HEIGHT;
    nodeSizes[node.id] = {
      width: NODE_WIDTH,
      thumbHeight: thumbHeight,
      totalHeight: totalHeight,
    };
    return nodeSizes[node.id];
  }

  function gridPointOnNode(nodeEl, pos, mapSize) {
    var wrap = nodeEl.querySelector('.thumb-wrap');
    if (!wrap || !pos || !mapSize) return null;
    var nodeLeft = parseFloat(nodeEl.style.left) || 0;
    var nodeTop = parseFloat(nodeEl.style.top) || 0;
    var width = Math.max(1, mapSize[0] || 1);
    var height = Math.max(1, mapSize[1] || 1);
    // Use actual thumb-wrap height for Y positioning
    var thumbH = wrap.offsetHeight || computeThumbHeight(nodeById(nodeEl.dataset.mapId));
    var x = ((pos[0] + 0.5) / width) * NODE_WIDTH;
    var y = ((pos[1] + 0.5) / height) * thumbH;
    return {
      x: nodeLeft + x,
      y: nodeTop + y,
    };
  }

  function initialLayout(nodes) {
    var count = nodes.length;
    var cols = Math.max(1, Math.ceil(Math.sqrt(count)));
    var positions = {};
    var colMaxWidth = 0;
    var rowMaxHeight = 0;

    // Compute max sizes for grid cells
    nodes.forEach(function (node) {
      var size = getNodeSize(node);
      colMaxWidth = Math.max(colMaxWidth, size.width);
      rowMaxHeight = Math.max(rowMaxHeight, size.totalHeight);
    });

    var cellW = colMaxWidth + NODE_GAP_X;
    var cellH = rowMaxHeight + NODE_GAP_Y;

    nodes.forEach(function (node, index) {
      var col = index % cols;
      var row = Math.floor(index / cols);
      positions[node.id] = {
        x: CANVAS_PADDING + col * cellW,
        y: CANVAS_PADDING + row * cellH,
      };
    });
    return positions;
  }

  function separateOverlappingNodes(positions, ids) {
    var pad = 20;
    for (var i = 0; i < ids.length; i += 1) {
      for (var j = i + 1; j < ids.length; j += 1) {
        var idA = ids[i];
        var idB = ids[j];
        var a = positions[idA];
        var b = positions[idB];
        var sizeA = nodeSizes[idA] || { width: NODE_WIDTH, totalHeight: NODE_HEIGHT };
        var sizeB = nodeSizes[idB] || { width: NODE_WIDTH, totalHeight: NODE_HEIGHT };
        var cx_a = a.x + sizeA.width / 2;
        var cy_a = a.y + sizeA.totalHeight / 2;
        var cx_b = b.x + sizeB.width / 2;
        var cy_b = b.y + sizeB.totalHeight / 2;
        var dx = cx_b - cx_a;
        var dy = cy_b - cy_a;
        var minDistX = (sizeA.width + sizeB.width) / 2 + pad;
        var minDistY = (sizeA.totalHeight + sizeB.totalHeight) / 2 + pad;
        var overlapX = minDistX - Math.abs(dx);
        var overlapY = minDistY - Math.abs(dy);
        if (overlapX > 0 && overlapY > 0) {
          if (overlapX < overlapY) {
            var shiftX = overlapX / 2 + 1;
            var dirX = dx >= 0 ? 1 : -1;
            a.x -= shiftX * dirX;
            b.x += shiftX * dirX;
          } else {
            var shiftY = overlapY / 2 + 1;
            var dirY = dy >= 0 ? 1 : -1;
            a.y -= shiftY * dirY;
            b.y += shiftY * dirY;
          }
        }
      }
    }
  }

  function runForceLayout(nodes, edges, positions, iterations) {
    var repulsion = 42000;
    var attraction = 0.012;
    var ids = nodes.map(function (n) { return n.id; });

    for (var step = 0; step < iterations; step += 1) {
      var forces = {};
      ids.forEach(function (id) {
        forces[id] = { x: 0, y: 0 };
      });

      for (var i = 0; i < ids.length; i += 1) {
        for (var j = i + 1; j < ids.length; j += 1) {
          var a = ids[i];
          var b = ids[j];
          var pa = positions[a];
          var pb = positions[b];
          var sizeA = nodeSizes[a] || { width: NODE_WIDTH, totalHeight: NODE_WIDTH };
          var sizeB = nodeSizes[b] || { width: NODE_WIDTH, totalHeight: NODE_WIDTH };
          var dx = (pb.x + sizeB.width / 2) - (pa.x + sizeA.width / 2);
          var dy = (pb.y + sizeB.totalHeight / 2) - (pa.y + sizeA.totalHeight / 2);
          var distSq = dx * dx + dy * dy;
          if (distSq < 1) {
            dx = (Math.random() - 0.5) * 2;
            dy = (Math.random() - 0.5) * 2;
            distSq = dx * dx + dy * dy || 1;
          }
          var force = repulsion / distSq;
          var dist = Math.sqrt(distSq);
          dx /= dist;
          dy /= dist;
          forces[a].x -= dx * force;
          forces[a].y -= dy * force;
          forces[b].x += dx * force;
          forces[b].y += dy * force;
        }
      }

      (edges || []).forEach(function (edge) {
        var ea = edge.from_map;
        var eb = edge.to_map;
        if (!positions[ea] || !positions[eb] || ea === eb) return;
        var pea = positions[ea];
        var peb = positions[eb];
        var sizeA2 = nodeSizes[ea] || { width: NODE_WIDTH, totalHeight: NODE_WIDTH };
        var sizeB2 = nodeSizes[eb] || { width: NODE_WIDTH, totalHeight: NODE_WIDTH };
        var edx = (peb.x + sizeB2.width / 2) - (pea.x + sizeA2.width / 2);
        var edy = (peb.y + sizeB2.totalHeight / 2) - (pea.y + sizeA2.totalHeight / 2);
        var edist = Math.sqrt(edx * edx + edy * edy) || 1;
        var desired = (sizeA2.width + sizeB2.width) / 2 + NODE_GAP_X;
        var eforce = (edist - desired) * attraction;
        forces[ea].x += (edx / edist) * eforce;
        forces[ea].y += (edy / edist) * eforce;
        forces[eb].x -= (edx / edist) * eforce;
        forces[eb].y -= (edy / edist) * eforce;
      });

      ids.forEach(function (id) {
        var p = positions[id];
        p.x += forces[id].x;
        p.y += forces[id].y;
        p.x = Math.max(CANVAS_PADDING, p.x);
        p.y = Math.max(CANVAS_PADDING, p.y);
      });

      separateOverlappingNodes(positions, ids);
    }
  }

  function resizeCanvas() {
    var canvas = document.getElementById('map-graph-canvas');
    var svg = document.getElementById('map-graph-edges');
    if (!canvas || !svg) return;

    var maxX = 400;
    var maxY = 300;
    Object.keys(nodePositions).forEach(function (id) {
      var p = nodePositions[id];
      var size = nodeSizes[id] || { width: NODE_WIDTH, totalHeight: NODE_WIDTH + NODE_META_HEIGHT };
      maxX = Math.max(maxX, p.x + size.width + CANVAS_PADDING);
      maxY = Math.max(maxY, p.y + size.totalHeight + CANVAS_PADDING);
    });
    canvas.style.width = maxX + 'px';
    canvas.style.height = maxY + 'px';
    svg.setAttribute('width', String(maxX));
    svg.setAttribute('height', String(maxY));
  }

  function drawGridOverlay(canvas, node, edges) {
    var ctx = canvas.getContext('2d');
    if (!ctx) return;
    var width = canvas.width;
    var height = canvas.height;
    ctx.clearRect(0, 0, width, height);

    var mapW = Math.max(1, node.width || 1);
    var mapH = Math.max(1, node.height || 1);

    ctx.strokeStyle = 'rgba(255,255,255,0.08)';
    ctx.lineWidth = 1;
    var stepX = width / mapW;
    var stepY = height / mapH;
    for (var x = 0; x <= mapW; x += 1) {
      ctx.beginPath();
      ctx.moveTo(x * stepX, 0);
      ctx.lineTo(x * stepX, height);
      ctx.stroke();
    }
    for (var y = 0; y <= mapH; y += 1) {
      ctx.beginPath();
      ctx.moveTo(0, y * stepY);
      ctx.lineTo(width, y * stepY);
      ctx.stroke();
    }

    function drawDot(pos, color) {
      if (!pos) return;
      var px = ((pos[0] + 0.5) / mapW) * width;
      var py = ((pos[1] + 0.5) / mapH) * height;
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
    var canvas = document.getElementById('map-graph-canvas');
    if (!canvas) return;

    canvas.querySelectorAll('.map-graph-node').forEach(function (el) { el.remove(); });

    (graph.nodes || []).forEach(function (node) {
      var pos = nodePositions[node.id] || { x: 40, y: 40 };
      var size = getNodeSize(node);
      var el = document.createElement('div');
      el.className = 'map-graph-node' + (node.is_current ? ' current' : '');
      el.dataset.mapId = node.id;
      el.style.left = pos.x + 'px';
      el.style.top = pos.y + 'px';
      el.style.width = size.width + 'px';

      var thumbWrap = document.createElement('div');
      thumbWrap.className = 'thumb-wrap';
      thumbWrap.style.height = size.thumbHeight + 'px';

      var img = document.createElement('img');
      img.className = 'thumb';
      img.alt = node.label;
      img.src = node.background_url || '/static/info.png';
      img.onerror = function () {
        this.onerror = null;
        this.src = '/static/info.png';
      };

      var overlay = document.createElement('canvas');
      overlay.className = 'grid-overlay';
      overlay.width = NODE_WIDTH;
      overlay.height = size.thumbHeight;

      thumbWrap.appendChild(img);
      thumbWrap.appendChild(overlay);

      var label = document.createElement('div');
      label.className = 'label';
      label.textContent = node.label;

      var meta = document.createElement('div');
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
    var canvas = document.getElementById('map-graph-canvas');
    var svg = document.getElementById('map-graph-edges');
    if (canvas && svg) {
      canvas.appendChild(svg);
    }
  }

  function addEdgeEndpoint(svg, point, fill) {
    var halo = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    halo.setAttribute('cx', String(point.x));
    halo.setAttribute('cy', String(point.y));
    halo.setAttribute('r', '9');
    halo.setAttribute('fill', 'rgba(8, 10, 14, 0.9)');
    svg.appendChild(halo);

    var dot = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    dot.setAttribute('cx', String(point.x));
    dot.setAttribute('cy', String(point.y));
    dot.setAttribute('r', '5.5');
    dot.setAttribute('fill', fill);
    dot.setAttribute('stroke', '#f4f7fb');
    dot.setAttribute('stroke-width', '1.75');
    svg.appendChild(dot);
  }

  function createArrowMarker(defs) {
    var marker = document.createElementNS('http://www.w3.org/2000/svg', 'marker');
    marker.setAttribute('id', 'map-graph-arrow');
    marker.setAttribute('viewBox', '0 0 12 12');
    marker.setAttribute('markerWidth', '12');
    marker.setAttribute('markerHeight', '12');
    marker.setAttribute('refX', '10.5');
    marker.setAttribute('refY', '6');
    marker.setAttribute('markerUnits', 'userSpaceOnUse');
    marker.setAttribute('orient', 'auto');
    var arrowPath = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    arrowPath.setAttribute('d', 'M1 1 L11 6 L1 11 Z');
    arrowPath.setAttribute('fill', '#7ec8ff');
    arrowPath.setAttribute('stroke', 'rgba(8, 10, 14, 0.95)');
    arrowPath.setAttribute('stroke-width', '1.25');
    arrowPath.setAttribute('stroke-linejoin', 'round');
    marker.appendChild(arrowPath);
    defs.appendChild(marker);
  }

  function appendEdgePath(svg, d, markerEnd) {
    var underlay = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    underlay.setAttribute('d', d);
    underlay.setAttribute('class', 'edge-path-underlay');
    underlay.setAttribute('fill', 'none');
    underlay.setAttribute('stroke', 'rgba(8, 10, 14, 0.92)');
    underlay.setAttribute('stroke-width', '7');
    underlay.setAttribute('stroke-linecap', 'round');
    underlay.setAttribute('stroke-linejoin', 'round');
    svg.appendChild(underlay);

    var path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
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
    var svg = document.getElementById('map-graph-edges');
    if (!svg) return;
    while (svg.firstChild) svg.removeChild(svg.firstChild);

    var defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');
    createArrowMarker(defs);
    svg.appendChild(defs);

    (graph.edges || []).forEach(function (edge) {
      var fromNode = document.querySelector('.map-graph-node[data-map-id="' + edge.from_map + '"]');
      var toNode = document.querySelector('.map-graph-node[data-map-id="' + edge.to_map + '"]');
      if (!fromNode || !toNode) return;

      var fromMap = nodeById(edge.from_map);
      var toMap = nodeById(edge.to_map);
      var start = gridPointOnNode(fromNode, edge.from_pos, [fromMap.width, fromMap.height]);
      var end = gridPointOnNode(toNode, edge.to_pos, [toMap.width, toMap.height]);
      if (!start || !end) return;

      var dx = end.x - start.x;
      var dy = end.y - start.y;
      var dist = Math.sqrt(dx * dx + dy * dy) || 1;
      var bend = Math.min(120, dist * 0.28);
      var cx1 = start.x + dx * 0.35;
      var cy1 = start.y + (dy >= 0 ? -bend : bend);
      var cx2 = end.x - dx * 0.35;
      var cy2 = end.y + (dy >= 0 ? bend : -bend);
      var trim = 10;
      var endX = end.x - (dx / dist) * trim;
      var endY = end.y - (dy / dist) * trim;
      var d = 'M' + start.x + ',' + start.y
        + ' C' + cx1 + ',' + cy1 + ' ' + cx2 + ',' + cy2 + ' ' + endX + ',' + endY;

      appendEdgePath(svg, d, 'url(#map-graph-arrow)');

      addEdgeEndpoint(svg, start, '#e67e22');
      addEdgeEndpoint(svg, end, '#5cb85c');

      var labelText = (edge.label || edge.kind || 'link')
        + ' (' + edge.from_pos.join(',') + ' → ' + edge.to_pos.join(',') + ')';
      var midX = (start.x + end.x) / 2;
      var midY = (start.y + end.y) / 2 - 10;
      var text = document.createElementNS('http://www.w3.org/2000/svg', 'text');
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
    // Reset node sizes cache
    nodeSizes = {};
    // Pre-compute sizes for all nodes
    (graph.nodes || []).forEach(function (node) {
      getNodeSize(node);
    });
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
    var stage = document.getElementById('map-graph-stage');
    var canvas = document.getElementById('map-graph-canvas');
    if (!stage || !canvas) return;

    var minX = Infinity;
    var minY = Infinity;
    var maxX = -Infinity;
    var maxY = -Infinity;
    Object.keys(nodePositions).forEach(function (id) {
      var p = nodePositions[id];
      var size = nodeSizes[id] || { width: NODE_WIDTH, totalHeight: NODE_WIDTH + NODE_META_HEIGHT };
      minX = Math.min(minX, p.x);
      minY = Math.min(minY, p.y);
      maxX = Math.max(maxX, p.x + size.width);
      maxY = Math.max(maxY, p.y + size.totalHeight);
    });
    if (!isFinite(minX)) {
      stage.scrollLeft = 0;
      stage.scrollTop = 0;
      return;
    }

    var pad = 40;
    var contentW = maxX - minX + pad * 2;
    var contentH = maxY - minY + pad * 2;
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

    var body = 'map=' + encodeURIComponent(mapId);
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
    var canvas = document.getElementById('map-graph-canvas');
    var stage = document.getElementById('map-graph-stage');
    if (!canvas || !stage) return;

    canvas.addEventListener('click', function (ev) {
      var nodeEl = ev.target.closest('.map-graph-node');
      if (!nodeEl || suppressNodeClick) {
        suppressNodeClick = false;
        return;
      }
      navigateToMap(nodeEl.dataset.mapId);
    });

    canvas.addEventListener('mousedown', function (ev) {
      var nodeEl = ev.target.closest('.map-graph-node');
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
        var dx = ev.clientX - dragState.startX;
        var dy = ev.clientY - dragState.startY;
        if (Math.abs(dx) > 3 || Math.abs(dy) > 3) dragState.moved = true;
        nodePositions[dragState.id] = {
          x: dragState.origin.x + dx,
          y: dragState.origin.y + dy,
        };
        var nodeEl = canvas.querySelector('.map-graph-node[data-map-id="' + dragState.id + '"]');
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
      var canvas = document.getElementById('map-graph-canvas');
      if (canvas) {
        canvas.innerHTML = '<div class="map-graph-empty">No maps registered in this campaign.</div>';
      }
      return;
    }
    render();
    setupInteractions();
  });
})();
