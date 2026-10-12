(function () {
  // Draws a chain between each grappler and the creature it grapples on the 2D map.
  // Source data: `.entity[data-grappled-by]` / `[data-grappling]` JSON emitted by map.html.
  // Redraws automatically whenever tiles change (full or optimized /update refresh).

  const LAYER_ID = 'grapple-links-layer';
  const SVG_NS = 'http://www.w3.org/2000/svg';

  function parseRefs(raw) {
    if (!raw) return [];
    try {
      const parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed.filter((r) => r && r.id) : [];
    } catch (_) {
      return [];
    }
  }

  function tilesContainer() {
    return document.querySelector('#main-map-area .tiles-container, #tiles-area')
      || document.querySelector('.tiles-container');
  }

  function tileSizePx(container) {
    const raw = container && container.getAttribute('data-tile-size');
    const n = parseInt(raw, 10);
    return Number.isFinite(n) && n > 0 ? n : 64;
  }

  // Layout coords relative to the tiles container (unaffected by map zoom transforms).
  function offsetWithin(el, container) {
    let left = 0;
    let top = 0;
    let node = el;
    while (node && node !== container) {
      const styleLeft = parseFloat(node.style && node.style.left);
      const styleTop = parseFloat(node.style && node.style.top);
      left += Number.isFinite(styleLeft) ? styleLeft : (node.offsetLeft || 0);
      top += Number.isFinite(styleTop) ? styleTop : (node.offsetTop || 0);
      node = node.parentElement;
    }
    return { left, top };
  }

  function collectTokens(container) {
    const tileSize = tileSizePx(container);
    const tokens = {};
    container.querySelectorAll('.tile .entity[data-id]').forEach((entityEl) => {
      const id = entityEl.getAttribute('data-id');
      if (!id || tokens[id]) return;
      const tile = entityEl.closest('.tile');
      if (!tile) return;
      const squares = parseInt(entityEl.getAttribute('data-token-squares'), 10) || 1;
      const pos = offsetWithin(tile, container);
      const half = (tileSize * squares) / 2;
      tokens[id] = {
        id,
        label: entityEl.getAttribute('data-label') || id,
        x: pos.left + half,
        y: pos.top + half,
        radius: half,
        grappledBy: parseRefs(entityEl.getAttribute('data-grappled-by')),
        grappling: parseRefs(entityEl.getAttribute('data-grappling')),
      };
    });
    return { tokens, tileSize };
  }

  function collectLinks(tokens) {
    const links = {};
    const add = (grapplerId, grappledId, escapeDc) => {
      const from = tokens[grapplerId];
      const to = tokens[grappledId];
      if (!from || !to || grapplerId === grappledId) return;
      const key = `${grapplerId}>${grappledId}`;
      if (!links[key]) links[key] = { key, from, to, escapeDc: null };
      if (escapeDc !== undefined && escapeDc !== null) links[key].escapeDc = escapeDc;
    };
    Object.keys(tokens).forEach((id) => {
      const token = tokens[id];
      token.grappledBy.forEach((ref) => add(ref.id, id, ref.escape_dc));
      token.grappling.forEach((ref) => add(id, ref.id));
    });
    return Object.keys(links).sort().map((k) => links[k]);
  }

  function linkTitle(link) {
    let text = `${link.from.label} is grappling ${link.to.label}`;
    if (link.escapeDc !== null && link.escapeDc !== undefined) {
      text += ` (escape DC ${link.escapeDc})`;
    }
    return text;
  }

  function signatureFor(links) {
    return links.map((l) => [
      l.key, Math.round(l.from.x), Math.round(l.from.y),
      Math.round(l.to.x), Math.round(l.to.y), l.escapeDc,
    ].join(':')).join('|');
  }

  // Shorten the segment so the chain spans the gap between tokens with a slight overlap.
  function trimmedSegment(link, tileSize) {
    const dx = link.to.x - link.from.x;
    const dy = link.to.y - link.from.y;
    const dist = Math.sqrt(dx * dx + dy * dy) || 1;
    const ux = dx / dist;
    const uy = dy / dist;
    const maxTrim = Math.max(0, dist / 2 - tileSize * 0.3);
    const trimFrom = Math.min(link.from.radius * 0.3, maxTrim);
    const trimTo = Math.min(link.to.radius * 0.3, maxTrim);
    return {
      x1: link.from.x + ux * trimFrom,
      y1: link.from.y + uy * trimFrom,
      x2: link.to.x - ux * trimTo,
      y2: link.to.y - uy * trimTo,
    };
  }

  function svgEl(name, attrs) {
    const el = document.createElementNS(SVG_NS, name);
    Object.keys(attrs).forEach((k) => el.setAttribute(k, String(attrs[k])));
    return el;
  }

  function buildLayer(links, tileSize) {
    const layer = document.createElement('div');
    layer.id = LAYER_ID;
    layer.className = 'grapple-links-layer';
    const svg = svgEl('svg', { class: 'grapple-links-svg', 'aria-hidden': 'true', overflow: 'visible' });
    svg.style.overflow = 'visible';
    layer.appendChild(svg);

    links.forEach((link) => {
      const seg = trimmedSegment(link, tileSize);
      const group = svgEl('g', {
        class: 'grapple-link',
        'data-grappler-id': link.from.id,
        'data-grappled-id': link.to.id,
      });
      const title = svgEl('title', {});
      title.textContent = linkTitle(link);
      group.appendChild(title);
      group.appendChild(svgEl('line', { class: 'grapple-link-shadow', ...seg }));
      group.appendChild(svgEl('line', { class: 'grapple-link-outer', ...seg }));
      group.appendChild(svgEl('line', { class: 'grapple-link-inner', ...seg }));
      svg.appendChild(group);

      const badge = document.createElement('div');
      badge.className = 'grapple-link-badge';
      badge.setAttribute('data-grappler-id', link.from.id);
      badge.setAttribute('data-grappled-id', link.to.id);
      badge.title = linkTitle(link);
      badge.setAttribute('aria-label', badge.title);
      badge.style.left = `${(seg.x1 + seg.x2) / 2}px`;
      badge.style.top = `${(seg.y1 + seg.y2) / 2}px`;
      badge.innerHTML = '<i class="glyphicon glyphicon-link"></i>';
      layer.appendChild(badge);
    });
    return layer;
  }

  let lastSignature = null;

  const GrappleLinks = {
    collect: function (container) {
      const c = container || tilesContainer();
      if (!c) return { links: [], tileSize: 64 };
      const { tokens, tileSize } = collectTokens(c);
      return { links: collectLinks(tokens), tileSize };
    },
    render: function (container) {
      const c = container || tilesContainer();
      if (!c) return [];
      const { links, tileSize } = GrappleLinks.collect(c);
      const existing = c.querySelector(`#${LAYER_ID}`);
      const signature = signatureFor(links);
      if (existing && signature === lastSignature) return links;
      if (existing) existing.remove();
      lastSignature = signature;
      if (links.length) c.appendChild(buildLayer(links, tileSize));
      return links;
    },
    reset: function () { lastSignature = null; },
    linkTitle,
  };

  if (typeof window !== 'undefined') {
    window.GrappleLinks = GrappleLinks;

    let scheduled = false;
    const schedule = () => {
      if (scheduled) return;
      scheduled = true;
      const run = () => {
        scheduled = false;
        try { GrappleLinks.render(); } catch (e) { /* non-fatal overlay */ }
      };
      if (typeof window.requestAnimationFrame === 'function') window.requestAnimationFrame(run);
      else setTimeout(run, 16);
    };

    const install = () => {
      schedule();
      if (window.__grappleLinksObserver || typeof MutationObserver === 'undefined') return;
      const obs = new MutationObserver((mutations) => {
        for (const m of mutations) {
          const target = m.target;
          if (!target || !target.closest) continue;
          if (target.closest(`#${LAYER_ID}`)) continue;
          if (!target.closest('.tiles-container')) continue;
          schedule();
          return;
        }
      });
      obs.observe(document.body, { subtree: true, childList: true });
      window.__grappleLinksObserver = obs;
    };

    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', install);
    } else {
      install();
    }
  }

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = GrappleLinks;
  }
})();
