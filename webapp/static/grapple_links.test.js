/**
 * Tests for grapple_links.js (chain overlay between grappler and grappled tokens).
 */
const GrappleLinks = require('./grapple_links.js');

function tokenTile({ id, label, x, y, squares = 1, grappledBy, grappling }, tileSize = 50) {
  const attrs = [
    `data-id="${id}"`,
    `data-label="${label}"`,
    `data-token-squares="${squares}"`,
  ];
  if (grappledBy) attrs.push(`data-grappled-by='${JSON.stringify(grappledBy)}'`);
  if (grappling) attrs.push(`data-grappling='${JSON.stringify(grappling)}'`);
  return `<div class="tile" data-coords-x="${x}" data-coords-y="${y}"
    style="left: ${x * tileSize + tileSize}px; top: ${y * tileSize + tileSize}px;">
    <div class="entity" ${attrs.join(' ')}></div></div>`;
}

function mountMap(tilesHtml) {
  document.body.innerHTML = `<div id="tiles-area" class="tiles-container" data-tile-size="50">${tilesHtml}</div>`;
  GrappleLinks.reset();
  return document.getElementById('tiles-area');
}

describe('GrappleLinks', () => {
  test('draws one chain per grapple pair with a descriptive title', () => {
    const container = mountMap([
      tokenTile({ id: 'ogre', label: 'Ogre', x: 1, y: 1, grappling: [{ id: 'pc', name: 'Hero' }] }),
      tokenTile({ id: 'pc', label: 'Hero', x: 2, y: 1, grappledBy: [{ id: 'ogre', name: 'Ogre', escape_dc: 13 }] }),
      tokenTile({ id: 'goblin', label: 'Goblin', x: 5, y: 5 }),
    ].join(''));

    const links = GrappleLinks.render(container);
    expect(links).toHaveLength(1);
    expect(links[0].from.id).toBe('ogre');
    expect(links[0].to.id).toBe('pc');
    expect(links[0].from.x).toBeCloseTo(100 + 25);
    expect(links[0].to.x).toBeCloseTo(150 + 25);

    const layer = container.querySelector('#grapple-links-layer');
    expect(layer).not.toBeNull();
    expect(layer.querySelectorAll('g.grapple-link')).toHaveLength(1);
    const badge = layer.querySelector('.grapple-link-badge');
    expect(badge.title).toBe('Ogre is grappling Hero (escape DC 13)');
    expect(badge.getAttribute('data-grappler-id')).toBe('ogre');
  });

  test('large tokens are centred on their full footprint', () => {
    const container = mountMap([
      tokenTile({ id: 'troll', label: 'Troll', x: 0, y: 0, squares: 2, grappling: [{ id: 'pc', name: 'Hero' }] }),
      tokenTile({ id: 'pc', label: 'Hero', x: 2, y: 0 }),
    ].join(''));
    const [link] = GrappleLinks.render(container);
    expect(link.from.x).toBeCloseTo(50 + 50);
    expect(link.from.radius).toBeCloseTo(50);
  });

  test('skips links whose other end is not rendered and removes stale layers', () => {
    const container = mountMap(
      tokenTile({ id: 'pc', label: 'Hero', x: 2, y: 1, grappledBy: [{ id: 'unseen', name: 'Unseen' }] }),
    );
    expect(GrappleLinks.render(container)).toHaveLength(0);
    expect(container.querySelector('#grapple-links-layer')).toBeNull();

    container.innerHTML = [
      tokenTile({ id: 'a', label: 'A', x: 0, y: 0, grappling: [{ id: 'b', name: 'B' }] }),
      tokenTile({ id: 'b', label: 'B', x: 1, y: 0 }),
    ].join('');
    expect(GrappleLinks.render(container)).toHaveLength(1);
    expect(container.querySelector('#grapple-links-layer')).not.toBeNull();

    container.querySelector('[data-id="a"]').removeAttribute('data-grappling');
    expect(GrappleLinks.render(container)).toHaveLength(0);
    expect(container.querySelector('#grapple-links-layer')).toBeNull();
  });
});
