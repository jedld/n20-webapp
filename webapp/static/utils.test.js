/**
 * Tests for utils.js movement-path grid helpers.
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { JSDOM } = require('jsdom');

function loadUtils(dom) {
  const jq = require('jquery');
  const $ = function (selector, context) {
    if (typeof selector === 'string' && arguments.length === 1) {
      return jq(selector, dom.window.document);
    }
    return jq.apply(jq, arguments);
  };
  Object.assign($, jq);
  $.fn = jq.fn;
  const sandbox = {
    window: dom.window,
    document: dom.window.document,
    $,
    jQuery: $,
    console,
    setTimeout,
    clearTimeout,
    requestAnimationFrame: (cb) => { cb(); return 0; },
    cancelAnimationFrame: () => {},
    pageXOffset: 0,
    pageYOffset: 0,
    alert: () => {},
    ajaxPost: () => {},
    ajaxGet: () => {},
    clearMovePathCache: () => {},
  };
  vm.createContext(sandbox);
  let utilsSrc = fs.readFileSync(path.join(__dirname, 'utils.js'), 'utf8');
  utilsSrc = utilsSrc.replace(/\n\$\(document\)\.ready[\s\S]*$/m, '\n');
  vm.runInContext(utilsSrc + ';\nthis.Utils = Utils;', sandbox);
  return sandbox.Utils;
}

describe('Utils movement grid origin', () => {
  test('uses displayed tile size from getBoundingClientRect (zoom-safe)', () => {
    const dom = new JSDOM(`
      <div class="image-container">
        <div class="tiles-container" data-tile-size="50">
          <div class="tile" data-coords-x="0" data-coords-y="0"></div>
        </div>
      </div>
    `, { url: 'http://localhost/' });

    const container = dom.window.document.querySelector('.tiles-container');
    const tile = dom.window.document.querySelector('.tile');

    container.getBoundingClientRect = () => ({
      left: 100,
      top: 200,
      right: 900,
      bottom: 800,
      width: 800,
      height: 600,
      x: 100,
      y: 200,
      toJSON: () => ({}),
    });
    tile.getBoundingClientRect = () => ({
      left: 150,
      top: 250,
      right: 230,
      bottom: 330,
      width: 80,
      height: 80,
      x: 150,
      y: 250,
      toJSON: () => ({}),
    });

    const Utils = loadUtils(dom);
    Utils.invalidateMovementGridCache();
    const grid = Utils._getMovementGridOrigin();

    expect(grid).not.toBeNull();
    expect(grid.tile).toBe(80);
    expect(grid.ox).toBe(100 + 80);
    expect(grid.oy).toBe(200 + 80);
  });

  test('tile center math matches scaled grid step', () => {
    const dom = new JSDOM(`
      <div class="tiles-container" data-tile-size="50">
        <div class="tile" data-coords-x="0" data-coords-y="0"></div>
      </div>
    `, { url: 'http://localhost/' });

    const container = dom.window.document.querySelector('.tiles-container');
    const tile = dom.window.document.querySelector('.tile');
    container.getBoundingClientRect = () => ({
      left: 0, top: 0, width: 400, height: 400, right: 400, bottom: 400, x: 0, y: 0, toJSON: () => ({}),
    });
    tile.getBoundingClientRect = () => ({
      left: 64, top: 64, width: 64, height: 64, right: 128, bottom: 128, x: 64, y: 64, toJSON: () => ({}),
    });

    const Utils = loadUtils(dom);
    Utils.invalidateMovementGridCache();
    const grid = Utils._getMovementGridOrigin();
    const half = grid.tile / 2;
    const center10 = { x: grid.ox + 1 * grid.tile + half, y: grid.oy + 0 * grid.tile + half };
    expect(center10.x).toBe(64 + 64 + 32);
    expect(center10.y).toBe(64 + 32);
  });
});

describe('Utils.setVttMenuLabel', () => {
  test('updates the inner label without dropping the icon', () => {
    const dom = new JSDOM(`
      <li id="open-console">
        <button type="button" class="vtt-menu-item">
          <span class="glyphicon glyphicon-list-alt"></span>
          <span class="vtt-menu-label">Combat Log</span>
        </button>
      </li>
    `);
    const Utils = loadUtils(dom);
    Utils.setVttMenuLabel('#open-console', 'Hide Combat Log');
    expect(dom.window.document.querySelector('.vtt-menu-label').textContent).toBe('Hide Combat Log');
  });
});

describe('Utils NPC spawner CR grouping', () => {
  test('groups NPCs by CR and sorts fractions first', () => {
    const Utils = loadUtils(new JSDOM('<div></div>'));
    const groups = Utils.groupNpcsByCr([
      { name: 'Ogre', cr: 2 },
      { name: 'Goblin', cr: 0.25 },
      { name: 'Ireena', cr: 0.125, unique: true },
      { name: 'Mystery', cr: 'Unknown' },
    ]);
    expect(groups.map((g) => g.label)).toEqual(['CR 1/8', 'CR 1/4', 'CR 2', 'Unrated']);
    expect(groups[0].npcs[0].name).toBe('Ireena');
  });

  test('search matches name, unique, and CR folder labels', () => {
    const dom = new JSDOM(`
      <div id="npc-list">
        <div class="npc-cr-group" data-cr-label="CR 1/8" data-user-collapsed="1">
          <div class="npc-cr-group-items">
            <div class="npc-item" data-search="ireena unique named cr 0.125">Ireena</div>
          </div>
        </div>
        <div class="npc-cr-group collapsed" data-cr-label="CR 2" data-user-collapsed="1">
          <div class="npc-cr-group-items">
            <div class="npc-item" data-search="ogre cr 2">Ogre</div>
          </div>
        </div>
      </div>
    `);
    const sandboxUtils = loadUtils(dom);
    const list = dom.window.document.getElementById('npc-list');
    sandboxUtils.applyNpcSpawnerSearch('unique', list);
    const ireena = dom.window.document.querySelector('[data-search*="ireena"]');
    const ogre = dom.window.document.querySelector('[data-search*="ogre"]');
    const cr8 = ireena.closest('.npc-cr-group');
    const cr2 = ogre.closest('.npc-cr-group');
    expect(ireena.style.display).not.toBe('none');
    expect(ogre.style.display).toBe('none');
    expect(cr8.classList.contains('collapsed')).toBe(false);
    expect(cr2.style.display).toBe('none');

    sandboxUtils.applyNpcSpawnerSearch('cr 2', list);
    expect(ogre.style.display).not.toBe('none');
    expect(cr2.classList.contains('collapsed')).toBe(false);
    expect(cr8.style.display).toBe('none');
  });
});

describe('Utils compact die-roll popover', () => {
  function setupStatBlock() {
    const dom = new JSDOM(`
      <table class="stat-block-abilities">
        <tr>
          <td>
            <span class="stat-block-score">10</span>
            <span class="stat-block-mod auto-die-roll"
                  data-entity="npc-1"
                  data-compact="true"
                  data-roll="1d20+0"
                  data-description="WIS Check">( +0 )</span>
          </td>
          <td>
            <span class="stat-block-score">14</span>
            <span class="stat-block-mod auto-die-roll"
                  data-entity="npc-1"
                  data-compact="true"
                  data-roll="1d20+2"
                  data-description="CHA Check">(+2)</span>
          </td>
        </tr>
      </table>
    `, { url: 'http://localhost/' });
    const Utils = loadUtils(dom);
    Utils.autoInsertDieRoll();
    Utils.initDieRollComponent();
    return { dom, Utils };
  }

  test('inserts a dice toggle without an inline display toggle panel', () => {
    const { dom } = setupStatBlock();
    const panel = dom.window.document.querySelector('.die-roll-component');
    const toggle = dom.window.document.querySelector('.die-roll-toggle');
    expect(toggle).not.toBeNull();
    expect(panel).not.toBeNull();
    expect(panel.style.display).toBe('');
    expect(panel.classList.contains('is-open')).toBe(false);
    expect(panel.querySelector('.rollable-die-label').textContent).toBe('Roll');
  });

  test('opening a compact roll panel does not use in-flow toggle display', () => {
    const { dom } = setupStatBlock();
    const cell = dom.window.document.querySelector('td');
    const toggle = cell.querySelector('.die-roll-toggle');
    const panel = cell.querySelector('.die-roll-component');
    toggle.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }));
    expect(panel.classList.contains('is-open')).toBe(true);
    expect(panel.style.display).toBe('');
  });

  test('only one compact roll popover is open at a time', () => {
    const { dom } = setupStatBlock();
    const toggles = dom.window.document.querySelectorAll('.die-roll-toggle');
    const panels = dom.window.document.querySelectorAll('.die-roll-component');
    toggles[0].dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }));
    expect(panels[0].classList.contains('is-open')).toBe(true);
    toggles[1].dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }));
    expect(panels[0].classList.contains('is-open')).toBe(false);
    expect(panels[1].classList.contains('is-open')).toBe(true);
  });

  test('clicking the same toggle closes the popover', () => {
    const { dom } = setupStatBlock();
    const toggle = dom.window.document.querySelector('.die-roll-toggle');
    const panel = dom.window.document.querySelector('.die-roll-component');
    toggle.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }));
    expect(panel.classList.contains('is-open')).toBe(true);
    toggle.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }));
    expect(panel.classList.contains('is-open')).toBe(false);
  });
});
