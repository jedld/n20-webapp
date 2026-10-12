/**
 * Basic tests for engine.js behavior. Focuses on pure functions and EventQueue.
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { JSDOM } = require('jsdom');

// Create a DOM window and expose global variables expected by engine.js
const dom = new JSDOM('<!doctype html><html><body><div id="game-time-text" data-seconds="0"></div></body></html>', {
  url: 'http://localhost/'
});
global.window = dom.window;
global.document = dom.window.document;
global.navigator = dom.window.navigator;
global.Utils = {};
// Provide a stable localStorage stub for all tests to prevent async callbacks from failing
global.localStorage = {
  getItem: () => null,
  setItem: () => {}
};

// Provide a minimal jQuery-like stub globally for engine.js to avoid heavy DOM wiring
function makeJqStub() {
  const stub = {
    length: 0,
    is: () => false,
    text: () => stub,
    css: () => stub,
    data: () => undefined,
    attr: () => stub,
    append: () => stub,
    html: () => stub,
    show: () => stub,
    hide: () => stub,
    on: () => stub,
    off: () => stub,
    keydown: () => stub,
    click: () => stub,
    fadeIn: () => stub,
    fadeOut: () => stub,
    toggle: () => stub,
    addClass: () => stub,
    removeClass: () => stub,
    find: () => stub,
    first: () => stub,
    closest: () => stub,
    each: () => stub,
    val: () => '',
    width: () => 0,
    height: () => 0,
    offset: () => ({ left: 0, top: 0 }),
    outerWidth: () => 0,
    scrollTop: () => stub,
    ready: () => stub,
    modal: () => stub,
    appendTo: () => stub,
    css: () => stub
  };
  return stub;
}

global.$ = function () { return makeJqStub(); };
global.jQuery = global.$;
global.window.$ = global.$;
global.window.jQuery = global.$;

// Stub setInterval/clearInterval to avoid background timers started by engine.js
global.setInterval = function () { return 0; };
global.clearInterval = function () {};

// Mock alert/confirm/prompt used in engine.js
global.alert = () => {};
global.confirm = () => true;
global.prompt = () => null;

// Provide default Chat and dialog trigger stubs globally so engine.js can reference them
global.Chat = {
  getCurrentPovEntity: () => null,
  addDialogMessage: () => {},
  showConversationBubble: () => {},
  handleLocalConversationEvent: () => {},
  shouldSkipConversationReply: () => false,
};
global.window.Chat = global.Chat;
global.showDialogTriggerBubble = () => {};
global.window.showDialogTriggerBubble = global.showDialogTriggerBubble;

// Mock canvas getContext to provide a minimal 2D API used by engine.js
class Canvas2DMock {
  constructor() {
    this.lineWidth = 1;
    this.strokeStyle = '#000';
    this.fillStyle = '#000';
    this.font = '10px Arial';
    this.textAlign = 'left';
  }
  save() {}
  restore() {}
  beginPath() {}
  moveTo() {}
  lineTo() {}
  quadraticCurveTo() {}
  stroke() {}
  fillText() {}
  clearRect() {}
  arc() {}
  fill() {}
  measureText() { return { width: 0 }; }
}
// Ensure calls to document.createElement('canvas') return an object with getContext
const originalCreateElement = global.document.createElement.bind(global.document);
global.document.createElement = function (tagName) {
  if (tagName && tagName.toLowerCase() === 'canvas') {
    return {
      width: 0,
      height: 0,
      style: {},
      getContext: () => new Canvas2DMock()
    };
  }
  return originalCreateElement(tagName);
};

// Load engine.js into the JSDOM environment
const enginePath = path.resolve(__dirname, 'engine.js');
const engineCode = fs.readFileSync(enginePath, 'utf8');

// Sanity check jQuery
if (typeof global.$ !== 'function' || typeof global.window.$ !== 'function') {
  throw new Error('jQuery not initialized as function before loading engine.js; typeof global.$=' + typeof global.$);
}

// Build a dynamic sandbox that forwards frequently-swapped globals via getters
const sandbox = {
  window: global.window,
  document: global.document,
  navigator: global.navigator,
  console: global.console,
};
const forward = (name) => {
  Object.defineProperty(sandbox, name, {
    get() { return global[name]; },
    set(v) { global[name] = v; },
    configurable: true,
    enumerable: true
  });
};
['$', 'jQuery', 'Utils', 'Chat', 'showDialogTriggerBubble', 'DMSoundManager', 'UserVolumeControl', 'Audio', 'localStorage', 'alert', 'confirm', 'prompt', 'setTimeout', 'setInterval', 'clearInterval'].forEach(forward);

const context = vm.createContext(sandbox);
// Run engine.js with proper filename so V8 coverage can attribute executed lines
new vm.Script(engineCode, { filename: enginePath }).runInContext(context);

// Expose selected symbols from the VM context
const expose = new vm.Script(`({
  formatGameTime: (typeof formatGameTime !== 'undefined') ? formatGameTime : undefined,
  EventQueue: (typeof EventQueue !== 'undefined') ? EventQueue : undefined,
  updateGameTimeDisplay: (typeof updateGameTimeDisplay !== 'undefined') ? updateGameTimeDisplay : undefined,
  drawLine: (typeof drawLine !== 'undefined') ? drawLine : undefined,
  applyViewportTransform: (typeof applyViewportTransform !== 'undefined') ? applyViewportTransform : undefined,
  centerOnTile: (typeof centerOnTile !== 'undefined') ? centerOnTile : undefined,
  centerOnTileXY: (typeof centerOnTileXY !== 'undefined') ? centerOnTileXY : undefined,
  centerOnEntityId: (typeof centerOnEntityId !== 'undefined') ? centerOnEntityId : undefined,
  getTilePositionInContainer: (typeof getTilePositionInContainer !== 'undefined') ? getTilePositionInContainer : undefined,
  getTilePositionForGridCoords: (typeof getTilePositionForGridCoords !== 'undefined') ? getTilePositionForGridCoords : undefined,
  renderMoveQuickAction: (typeof renderMoveQuickAction !== 'undefined') ? renderMoveQuickAction : undefined,
  executeMoveQuickAction: (typeof executeMoveQuickAction !== 'undefined') ? executeMoveQuickAction : undefined,
  updateCoordsBox: (typeof updateCoordsBox !== 'undefined') ? updateCoordsBox : undefined,
  appendTeleporterHoverHint: (typeof appendTeleporterHoverHint !== 'undefined') ? appendTeleporterHoverHint : undefined,
  DmDeleteUndo: (typeof DmDeleteUndo !== 'undefined') ? DmDeleteUndo : undefined,
  actionBarState: (typeof actionBarState !== 'undefined') ? actionBarState : undefined
})`);
const Engine = expose.runInContext(context);

describe('engine.js basic behavior', () => {
  test('formatGameTime formats seconds correctly', () => {
    expect(Engine.formatGameTime(0)).toBe('0 seconds');
    expect(Engine.formatGameTime(1)).toBe('1 second');
    expect(Engine.formatGameTime(62)).toBe('1 minute, 2 seconds');
    // engine.js omits minutes/seconds when zero
    expect(Engine.formatGameTime(3600)).toBe('1 hour');
    expect(Engine.formatGameTime(86400 + 3661)).toBe('1 day, 1 hour, 1 minute, 1 second');
  // multiple days with no remainder
    expect(Engine.formatGameTime(2 * 24 * 60 * 60)).toBe('2 days');
  });

  test('updateCoordsBox writes coordinates and tooltip into #coords-box', () => {
    const orig$ = global.$;
    let written = null;
    global.$ = (sel) => {
      if (sel === '#coords-box') {
        return { html: (html) => { written = html; } };
      }
      return orig$(sel);
    };
    try {
      Engine.updateCoordsBox(11, 7, '<p>Difficult Terrain</p><p>Iron Gates</p>');
      expect(written).toContain('X: 11');
      expect(written).toContain('Y: 7');
      expect(written).toContain('Difficult Terrain');
      expect(written).toContain('Iron Gates');
    } finally {
      global.$ = orig$;
    }
  });

  test('appendTeleporterHoverHint adds party travel interact copy', () => {
    const marker = {
      length: 1,
      first() { return this; },
      attr(name) {
        if (name === 'data-destination') return 'Forest Clearing';
        if (name === 'data-party-travel') return '1';
        return '';
      },
    };
    const $tile = { find: () => marker };
    const html = Engine.appendTeleporterHoverHint($tile, '<p>Bright Light</p>');
    expect(html).toContain('→ Forest Clearing');
    expect(html).toContain('Interact to travel with the party');
  });

  test('EventQueue enqueues and processes events in order', async () => {
    const results = [];
    const q = new Engine.EventQueue();
    q.setDebugMode(false);

    // Override processEvent to record order and resolve quickly
    q.processEvent = async (event) => {
      results.push(event.type + ':' + event.message);
    };

    q.enqueue({ type: 'message', message: 'first' });
    q.enqueue({ type: 'message', message: 'second' });
    q.enqueue({ type: 'message', message: 'third' });

    // wait for queue to finish processing (poll via setTimeout to avoid stubbed setInterval)
    await new Promise((resolve, reject) => {
      const start = Date.now();
      const check = () => {
        if (!q.processing) return resolve();
        if (Date.now() - start > 1000) return reject(new Error('Queue did not finish'));
        setTimeout(check, 10);
      };
      setTimeout(check, 10);
    });

    expect(results).toEqual(['message:first', 'message:second', 'message:third']);
  });

  test('drawLine returns early when tile centers are missing (no ctx calls)', () => {
    // Fake ctx that throws if methods are called
    const calls = [];
    const ctx = new Proxy({}, {
      get: () => () => { calls.push('called'); }
    });

    // Use coordinates that won't map to existing tiles with our stub
    Engine.drawLine(ctx, { x: 1, y: 1 }, { x: 2, y: 2 }, { withArrow: true, text: 'test' });

    // When centers are missing, drawLine should exit without invoking ctx
    expect(calls.length).toBe(0);
  });

  test('getTilePositionInContainer uses tile grid layout under .image-container', () => {
    const origQuery = document.querySelector.bind(document);
    const tilesEl = {
      offsetLeft: -70,
      offsetTop: -70,
      getAttribute: (name) => (name === 'data-tile-size' ? '70' : null)
    };
    const mapEl = { offsetLeft: 0, offsetTop: 0 };
    document.querySelector = (sel) => {
      if (sel === '.tiles-container') return tilesEl;
      if (sel === '.image-container') return mapEl;
      return origQuery(sel);
    };

    const $tile = {
      length: 1,
      data: (key) => {
        if (key === 'coords-x') return 2;
        if (key === 'coords-y') return 3;
        return undefined;
      }
    };

    const pos = Engine.getTilePositionInContainer($tile, 70, 70);
    expect(pos).toEqual({ left: 189, top: 253 });

    document.querySelector = origQuery;
  });

  test('applyViewportTransform invalidates movement grid cache before updating transform', () => {
    const originalUtils = global.Utils;
    const original$ = global.$;
    const cssCalls = [];
    let invalidations = 0;

    global.Utils = {
      invalidateMovementGridCache: () => {
        invalidations += 1;
      }
    };

    global.$ = (selector) => {
      if (selector === '.image-container') {
        return {
          length: 1,
          css: (styles) => {
            cssCalls.push(styles);
          }
        };
      }
      return original$(selector);
    };

    Engine.applyViewportTransform();

    expect(invalidations).toBe(1);
    expect(cssCalls).toHaveLength(1);
    expect(cssCalls[0]).toHaveProperty('transform');

    global.Utils = originalUtils;
    global.$ = original$;
  });

  test('updateGameTimeDisplay writes formatted text into #game-time-text', () => {
    let written = null;
    const orig$ = global.$;
    // Swap $ just for this test to capture text writes
    global.$ = (selector) => {
      if (selector === '#game-time-text') {
        return {
          text: (val) => { written = val; return this; }
        };
      }
      return orig$(selector);
    };

    Engine.updateGameTimeDisplay(3661); // 1h 1m 1s

    expect(written).toBe('1 hour, 1 minute, 1 second');

    // restore
    global.$ = orig$;
  });

  test('EventQueue drops oldest when exceeding maxQueueSize', async () => {
    const q = new Engine.EventQueue();
    q.setDebugMode(false);
    q.maxQueueSize = 2;

    const processed = [];
    q.processEvent = async (evt) => { processed.push(evt.message); };

    // Prevent auto-processing so we can overflow the queue
    q.processing = true;
    q.enqueue({ type: 'message', message: 'a' });
    q.enqueue({ type: 'message', message: 'b' });
    q.enqueue({ type: 'message', message: 'c' }); // should drop 'a'

    expect(q.queue.length).toBe(2);

    q.processing = false;
    await q.processNext();

    expect(processed).toEqual(['b', 'c']);
  });

  test('EventQueue inserts ~100ms delay between consecutive move events', async () => {
    const q = new Engine.EventQueue();
    q.setDebugMode(false);

    // Make processEvent very fast
    q.processEvent = async () => {};

    const start = Date.now();
    q.enqueue({ type: 'move', message: 'm1' });
    q.enqueue({ type: 'move', message: 'm2' });

    await new Promise((resolve) => {
      const poll = () => {
        if (!q.processing && q.queue.length === 0) return resolve();
        setTimeout(poll, 5);
      };
      setTimeout(poll, 5);
    });

    const elapsed = Date.now() - start;
    expect(elapsed).toBeGreaterThanOrEqual(95); // allow small scheduling jitter
  });

  test('EventQueue getStatus reflects processing and counts', async () => {
    const q = new Engine.EventQueue();
    q.setDebugMode(false);
    q.processEvent = async () => {};

    // Initially empty
    let status = q.getStatus();
    expect(typeof status.queueLength).toBe('number');
    expect(typeof status.processing).toBe('boolean');
    expect(typeof status.processedCount).toBe('number');
    expect(typeof status.maxQueueSize).toBe('number');

    q.enqueue({ type: 'message', message: 'one' });
    q.enqueue({ type: 'message', message: 'two' });

    await new Promise((resolve) => {
      const poll = () => {
        if (!q.processing && q.queue.length === 0) return resolve();
        setTimeout(poll, 5);
      };
      setTimeout(poll, 5);
    });

    status = q.getStatus();
    expect(status.queueLength).toBe(0);
    expect(status.processing).toBe(false);
    expect(status.processedCount).toBeGreaterThanOrEqual(2);
  });

  test('EventQueue.processEvent logs message for type "message"', async () => {
    const q = new Engine.EventQueue();
    q.setDebugMode(false);
    const spy = jest.spyOn(console, 'log').mockImplementation(() => {});
    try {
      await q.processEvent({ type: 'message', message: 'hello-world' });
      const calls = spy.mock.calls.map(args => args.join(' '));
      expect(calls.some(c => c.includes('hello-world'))).toBe(true);
    } finally {
      spy.mockRestore();
    }
  });

  test('EventQueue.processEvent logs error for type "error"', async () => {
    const q = new Engine.EventQueue();
    q.setDebugMode(false);
    const spy = jest.spyOn(console, 'error').mockImplementation(() => {});
    try {
      await q.processEvent({ type: 'error', message: 'bad-thing' });
      const calls = spy.mock.calls.map(args => args.join(' '));
      expect(calls.some(c => c.includes('bad-thing'))).toBe(true);
    } finally {
      spy.mockRestore();
    }
  });

  test('centerOnTile animates scroll and toggles highlights', () => {
    const calls = { animate: null, faded: 0, addedRed: false, removedRed: false };

    // Build a fake tile element API with the minimal methods used
    const tileEl = {
      classList: new Set(),
      getBoundingClientRect: () => ({ left: 100, top: 200, width: 50, height: 50 }),
    };

    // Local $ override for this test
    const orig$ = global.$;
    const origUtils = global.Utils;
    global.Utils = {
      ...(origUtils || {}),
      viewportCoordsFromTile: null,
    };
    global.$ = (sel) => {
      // The function should receive the tile as a jQuery-like object, so we return our stub when it gets passed in
      if (sel === tileStub) return tileStub;
      // .tile and .tile .entity selections
      if (sel === '.tile .entity') {
        return { removeClass: () => { calls.removedRed = true; return this; } };
      }
      if (sel === '.tile') {
        return { removeClass: (cls) => { if (cls === 'focus-highlight-red') calls.removedRed = true; return this; } };
      }
      if (sel === 'html, body') {
        return { animate: (obj, ms, cb) => { calls.animate = { obj, ms }; if (cb) cb(); return this; } };
      }
      if (sel === '.image-container') {
        return { length: 1, css: () => {} };
      }
      if (sel === '#main-map-area') {
        return { length: 1, 0: { getBoundingClientRect: () => ({ width: 800, height: 600, left: 0, top: 0 }) } };
      }
      if (sel === '.tiles-container') {
        return { data: () => 70 };
      }
      return orig$(sel);
    };

    const tileStub = {
      0: tileEl,
      length: 1,
      width: () => 50,
      height: () => 50,
      offset: () => ({ left: 100, top: 200 }),
      data: (key) => key === 'coords-x' ? 5 : (key === 'coords-y' ? 3 : null),
      find: () => ({ addClass: (cls) => { if (cls === 'focus-highlight') {} return tileStub; } }),
      fadeOut: (ms) => { calls.faded += 1; return tileStub; },
      fadeIn: (ms) => { calls.faded += 1; return tileStub; },
      addClass: (cls) => { if (cls === 'focus-highlight-red') calls.addedRed = true; return tileStub; }
    };

    // Mock window size
    Object.defineProperty(global.window, 'innerWidth', { value: 800, configurable: true });
    Object.defineProperty(global.window, 'innerHeight', { value: 600, configurable: true });

    // centerOnTile is exposed as Engine.centerOnTile
    Engine.centerOnTile(tileStub, true);

    // Now uses viewport transform instead of scroll animation
    expect(calls.faded).toBe(2); // fadeOut + fadeIn
    expect(calls.addedRed).toBe(true);

    global.$ = orig$;
    global.Utils = origUtils;
  });

  test('centerOnTileXY selects tile by coords and triggers centering', () => {
    const seen = { selected: null };
    const orig$ = global.$;

    const tileStub = {
      length: 1,
      width: () => 50,
      height: () => 50,
      offset: () => ({ left: 100, top: 200 }),
      data: (key) => key === 'coords-x' ? 7 : (key === 'coords-y' ? 9 : null),
      find: () => ({ addClass: () => tileStub }),
      fadeOut: () => tileStub,
      fadeIn: () => tileStub,
      addClass: () => tileStub
    };

    global.$ = (sel) => {
      const m = sel && sel.match?.(/\.tile\[data-coords-x="(\d+)"\]\[data-coords-y="(\d+)"\]/);
      if (m) { seen.selected = { x: Number(m[1]), y: Number(m[2]) }; return tileStub; }
      if (sel === '.tile') { return { removeClass: () => ({}) } }
      if (sel === '.tile .entity') { return { removeClass: () => ({}) } }
      if (sel === '.image-container') { return { length: 1, css: () => {} }; }
      if (sel === '#main-map-area') { return { length: 1, 0: { getBoundingClientRect: () => ({ width: 800, height: 600, left: 0, top: 0 }) } }; }
      if (sel === '.tiles-container') { return { data: () => 70 }; }
      return orig$(sel);
    };

    // Window size
    Object.defineProperty(global.window, 'innerWidth', { value: 800, configurable: true });
    Object.defineProperty(global.window, 'innerHeight', { value: 600, configurable: true });

    Engine.centerOnTileXY(7, 9, true);

    expect(seen.selected).toEqual({ x: 7, y: 9 });

    global.$ = orig$;
  });

  test('centerOnEntityId selects tile by id and triggers centering', () => {
    const seen = { id: null };
    const orig$ = global.$;
    const origUtils = global.Utils;
    global.Utils = {
      ...(origUtils || {}),
      getEntityTile: null,
      viewportCoordsFromTile: null,
    };

    const tileStub = {
      length: 1,
      first: () => tileStub,
      width: () => 50,
      height: () => 50,
      offset: () => ({ left: 100, top: 200 }),
      data: (key) => key === 'coords-x' ? 5 : (key === 'coords-y' ? 3 : null),
      find: () => ({ addClass: () => tileStub }),
      fadeOut: () => tileStub,
      fadeIn: () => tileStub,
      addClass: () => tileStub
    };

    global.$ = (sel) => {
      const m = sel && sel.match?.(/\.tile\[data-coords-id="(.+?)"\]/);
      if (m) { seen.id = m[1]; return tileStub; }
      if (sel === '.tile') { return { removeClass: () => ({}) } }
      if (sel === '.tile .entity') { return { removeClass: () => ({}) } }
      if (sel === '.image-container') { return { length: 1, css: () => {} }; }
      if (sel === '#main-map-area') { return { length: 1, 0: { getBoundingClientRect: () => ({ width: 800, height: 600, left: 0, top: 0 }) } }; }
      if (sel === '.tiles-container') { return { data: () => 70 }; }
      return orig$(sel);
    };

    // Window size
    Object.defineProperty(global.window, 'innerWidth', { value: 800, configurable: true });
    Object.defineProperty(global.window, 'innerHeight', { value: 600, configurable: true });

    Engine.centerOnEntityId('E-123');

    expect(seen.id).toBe('E-123');

    global.$ = orig$;
    global.Utils = origUtils;
  });

  test('EventQueue focus event centers on coords with highlight', async () => {
    const q = new Engine.EventQueue();
    const seen = { selected: null };
    const orig$ = global.$;

    const tileStub = {
      length: 1,
      width: () => 50,
      height: () => 50,
      offset: () => ({ left: 100, top: 200 }),
      data: (key) => key === 'coords-x' ? 3 : (key === 'coords-y' ? 5 : null),
      find: () => ({ addClass: () => tileStub }),
      fadeOut: () => tileStub,
      fadeIn: () => tileStub,
      addClass: () => tileStub
    };

    global.$ = (sel) => {
      const m = sel && sel.match?.(/\.tile\[data-coords-x="(\d+)"\]\[data-coords-y="(\d+)"\]/);
      if (m) { seen.selected = { x: Number(m[1]), y: Number(m[2]) }; return tileStub; }
      if (sel === 'html, body') { return { animate: (o, ms, cb) => { calls.animate = { o, ms }; if (cb) cb(); return this; } }; }
      if (sel === '.tile') { return { removeClass: () => ({}) } }
      if (sel === '.tile .entity') { return { removeClass: () => ({}) } }
      if (sel === '.image-container') { return { length: 1, css: () => {} }; }
      if (sel === '#main-map-area') { return { length: 1, 0: { getBoundingClientRect: () => ({ width: 800, height: 600, left: 0, top: 0 }) } }; }
      if (sel === '.tiles-container') { return { data: () => 70 }; }
      return orig$(sel);
    };

    Object.defineProperty(global.window, 'innerWidth', { value: 800, configurable: true });
    Object.defineProperty(global.window, 'innerHeight', { value: 600, configurable: true });

    q.enqueue({ type: 'focus', message: { x: 3, y: 5 } });

    await new Promise((resolve) => {
      const poll = () => { if (!q.processing && q.queue.length === 0) return resolve(); setTimeout(poll, 5); };
      setTimeout(poll, 5);
    });

    expect(seen.selected).toEqual({ x: 3, y: 5 });

    global.$ = orig$;
  });

  test('EventQueue turn event updates game time display when game_time is provided', async () => {
    const q = new Engine.EventQueue();

    // Stub Utils.ajaxGet used by refreshTurn
    const originalUtils = global.Utils;
    global.Utils = { ajaxGet: (url, data, cb) => { if (typeof cb === 'function') cb('<div/>'); } };

    // Capture writes to #game-time-text
    let written = null;
    const orig$ = global.$;
    global.$ = (sel) => {
      if (sel === '#game-time-text') {
        return { text: (val) => { written = val; return this; } };
      }
      if (sel === '.game-turn-container') {
        return { html: () => ({ show: () => ({}) }) };
      }
      return orig$(sel);
    };

    q.enqueue({ type: 'turn', message: { game_time: 3661 } });

    await new Promise((resolve) => {
      const poll = () => { if (!q.processing && q.queue.length === 0) return resolve(); setTimeout(poll, 5); };
      setTimeout(poll, 5);
    });

    expect(written).toBe('1 hour, 1 minute, 1 second');

    // Restore
    global.$ = orig$;
    global.Utils = originalUtils;
  });

  test('EventQueue console event appends message to console container', async () => {
    const q = new Engine.EventQueue();
    const appended = [];
    const orig$ = global.$;

    const consoleInner = { append: (html) => { appended.push(html); return consoleInner; } };
    const consoleContainer = { 0: { scrollHeight: 100 }, scrollTop: () => consoleContainer };

    global.$ = (sel) => {
      if (sel === '#console-container #console') {
        return consoleInner;
      }
      if (sel === '#console-container') {
        return consoleContainer;
      }
      return orig$(sel);
    };

    q.enqueue({ type: 'console', message: 'hello-console' });

    await new Promise((resolve) => {
      const poll = () => { if (!q.processing && q.queue.length === 0) return resolve(); setTimeout(poll, 5); };
      setTimeout(poll, 5);
    });

    expect(appended.some(x => String(x).includes('<p>hello-console</p>'))).toBe(true);

    global.$ = orig$;
  });

  test('EventQueue map event updates image, containers, canvas, and refreshes tiles', async () => {
    const q = new Engine.EventQueue();

    const calls = { imageAttr: null, imageCss: null, imageContainerCss: null, tilesAreaCss: null, tilesDataSet: null };
    const orig$ = global.$;
    const origQS = global.document.querySelector.bind(global.document);

    const tilesContainerData = { width: null, height: null, tileSize: 32 };

    const imageObj = {
      attr: (k, v) => { if (k === 'src') calls.imageAttr = v; return imageObj; },
      css: (o) => { calls.imageCss = o; return imageObj; }
    };
    const imageContainerObj = { css: (o) => { calls.imageContainerCss = o; return imageContainerObj; } };
    const tilesAreaObj = { css: (o) => { calls.tilesAreaCss = o; return tilesAreaObj; } };
    const tilesContainerObj = {
      data: (arg) => {
        if (arg && typeof arg === 'object') { calls.tilesDataSet = arg; return tilesContainerObj; }
        if (arg === 'tile-size') return tilesContainerData.tileSize;
        return undefined;
      }
    };

    global.$ = (sel) => {
      if (sel === '.tiles-container') return tilesContainerObj;
      if (sel === '.image-container img') return imageObj;
      if (sel === '.image-container') return imageContainerObj;
      if (sel === '#tiles-area') return tilesAreaObj;
      return orig$(sel);
    };

    const canvasStub = { width: 0, height: 0 };
    global.document.querySelector = (selector) => {
      if (selector === 'canvas') return canvasStub;
      return origQS(selector);
    };

    const origUtils = global.Utils;
    global.Utils = { refreshTileSet: jest.fn() };

    q.enqueue({ type: 'map', message: 'http://example/map.png', width: 640, height: 480, image_offset_px: [10, 20] });

    await new Promise((resolve) => {
      const poll = () => { if (!q.processing && q.queue.length === 0) return resolve(); setTimeout(poll, 5); };
      setTimeout(poll, 5);
    });

    expect(calls.tilesDataSet).toEqual({ width: 640, height: 480 });
    expect(calls.imageAttr).toBe('http://example/map.png');
    expect(calls.imageCss).toEqual({ width: '640px', objectFit: 'cover', objectPosition: 'top' });
    // Matches Utils.updateMapDisplay: image_offset_px is applied to the tiles
    // area only; the image container stays at (tile_size, tile_size).
    expect(calls.imageContainerCss).toEqual({ height: '480px', top: tilesContainerData.tileSize, left: tilesContainerData.tileSize });
    expect(calls.tilesAreaCss).toEqual({ top: -tilesContainerData.tileSize - 20, left: -tilesContainerData.tileSize - 10 });
    expect(canvasStub.width).toBe(640 + tilesContainerData.tileSize);
    expect(canvasStub.height).toBe(480 + tilesContainerData.tileSize);
    expect(global.Utils.refreshTileSet).toHaveBeenCalled();

    global.$ = orig$;
    global.document.querySelector = origQS;
    global.Utils = origUtils;
  });

  test('renderMoveQuickAction shows quick icon for reachable empty hovered tile', () => {
    const orig$ = global.$;

    const tileStub = {
      length: 1,
      is: (selector) => selector === ':hover',
      find: () => ({ length: 0, remove: () => {} })
    };

    const rendered = { count: 0 };
    const originals = new vm.Script(`({
      shouldEnableMoveQuickAction,
      isTileOccupiedForMoveQuickAction,
      resolveMoveQuickActionPathToTile,
      ensureMoveQuickActionLayer
    })`).runInContext(context);

    new vm.Script(`
      shouldEnableMoveQuickAction = () => true;
      isTileOccupiedForMoveQuickAction = () => false;
      resolveMoveQuickActionPathToTile = (_tile, done) => done({ path: [[1,1],[1,2]], placeable: true, cost: { budget: 0, original_budget: 30 } });
      ensureMoveQuickActionLayer = () => { __renderedCounter.count += 1; return {}; };
    `).runInContext(context);

    context.__renderedCounter = rendered;
    Engine.renderMoveQuickAction(tileStub);
    expect(rendered.count).toBe(1);
    delete context.__renderedCounter;

    context.__origQuickMoveFns = originals;
    new vm.Script(`
      shouldEnableMoveQuickAction = __origQuickMoveFns.shouldEnableMoveQuickAction;
      isTileOccupiedForMoveQuickAction = __origQuickMoveFns.isTileOccupiedForMoveQuickAction;
      resolveMoveQuickActionPathToTile = __origQuickMoveFns.resolveMoveQuickActionPathToTile;
      ensureMoveQuickActionLayer = __origQuickMoveFns.ensureMoveQuickActionLayer;
    `).runInContext(context);
    delete context.__origQuickMoveFns;

    global.$ = orig$;
  });

  test('executeMoveQuickAction posts MoveAction with resolved path', () => {
    const orig$ = global.$;
    const origUtils = global.Utils;
    const oldAjax = global.$.ajax;
    const calls = [];
    const originals = new vm.Script(`({
      getMoveQuickActionPovUid,
      resolveMoveQuickActionPathToTile,
      noteMoveActionCompleted,
      hideMoveQuickActionLayer,
      clearMoveQuickActionPreview
    })`).runInContext(context);

    const tileStub = {
      length: 1,
      find: () => ({ remove: () => {} })
    };

    const btnStub = {};
    const btnJq = {
      length: 1,
      closest: () => tileStub,
      prop: () => btnJq,
      is: () => false,
    };

    global.$ = (selectorOrObj) => {
      if (selectorOrObj === btnStub) {
        return btnJq;
      }
      if (selectorOrObj === 'body') {
        return { attr: () => 'hero-1' };
      }
      return orig$(selectorOrObj);
    };

    global.Utils = {
      ...(global.Utils || {}),
      showToast: jest.fn(),
    };

    new vm.Script(`
      getMoveQuickActionPovUid = () => 'hero-1';
      resolveMoveQuickActionPathToTile = (_tile, done) => done({ path: [[3,3],[3,4]], placeable: true, cost: { budget: 0, original_budget: 30 } });
      noteMoveActionCompleted = () => {};
      hideMoveQuickActionLayer = () => {};
      clearMoveQuickActionPreview = () => {};
    `).runInContext(context);

    context.__ajaxCalls = calls;
    global.$.ajax = (opts) => {
      calls.push({ url: opts.url, payload: JSON.parse(opts.data || '{}') });
      if (opts && typeof opts.success === 'function') {
        opts.success({ status: 'ok' });
      }
    };
    Engine.executeMoveQuickAction(btnStub);

    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe('/action');
    expect(calls[0].payload.id).toBe('hero-1');
    expect(calls[0].payload.action).toBe('MoveAction');
    expect(calls[0].payload.path).toEqual([[3, 3], [3, 4]]);

    delete context.__ajaxCalls;
    context.__origExecuteMoveQuickFns = originals;
    new vm.Script(`
      getMoveQuickActionPovUid = __origExecuteMoveQuickFns.getMoveQuickActionPovUid;
      resolveMoveQuickActionPathToTile = __origExecuteMoveQuickFns.resolveMoveQuickActionPathToTile;
      noteMoveActionCompleted = __origExecuteMoveQuickFns.noteMoveActionCompleted;
      hideMoveQuickActionLayer = __origExecuteMoveQuickFns.hideMoveQuickActionLayer;
      clearMoveQuickActionPreview = __origExecuteMoveQuickFns.clearMoveQuickActionPreview;
    `).runInContext(context);
    delete context.__origExecuteMoveQuickFns;

    global.$.ajax = oldAjax;
    global.$ = orig$;
    global.Utils = origUtils;
  });

  test('EventQueue refresh_map calls Utils.refreshTileSet safely', async () => {
    const q = new Engine.EventQueue();
    const orig$ = global.$;

    global.$ = (sel) => {
      if (sel === 'body' || sel === 'body') {
        return {
          data: (key) => (key === 'role' ? 'dm' : undefined),
          attr: () => '',
        };
      }
      if (sel === '.tile') {
        return {
          removeClass: () => ({ }),
          each: () => {}
        };
      }
      if (sel === '.moving-entity-sprite') {
        return { remove: () => {} };
      }
      if (sel === '.entity') {
        return { css: () => {} };
      }
      return orig$(sel);
    };

    const origUtils = global.Utils;
    // Mock refreshTileSet to call the callback immediately
    global.Utils = { 
      refreshTileSet: jest.fn((a, b, c, d, e, callback) => {
        if (typeof callback === 'function') callback();
      })
    };

    q.enqueue({ type: 'refresh_map' });

    await new Promise((resolve) => {
      const poll = () => { if (!q.processing && q.queue.length === 0) return resolve(); setTimeout(poll, 5); };
      setTimeout(poll, 5);
    });

    await new Promise((resolve) => setTimeout(resolve, 240));

    expect(global.Utils.refreshTileSet).toHaveBeenCalled();

    global.$ = orig$;
    global.Utils = origUtils;
  });

  test('EventQueue party_membership refreshes portraits, toasts, and hides invite', async () => {
    const q = new Engine.EventQueue();
    const orig$ = global.$;
    const origUtils = global.Utils;
    const origChat = global.Chat;
    const dialogMessages = [];
    let inviteHidden = false;

    global.$ = (sel) => {
      if (sel === '#dialogEntityName') {
        return { data: (key) => (key === 'entity-id' ? 'npc_sister_anya' : undefined) };
      }
      if (sel === '#dialogInviteParty') {
        return {
          hide: () => { inviteHidden = true; },
          show: () => { inviteHidden = false; },
          toggle: (visible) => { inviteHidden = !visible; },
        };
      }
      return orig$(sel);
    };
    global.Utils = {
      showToast: jest.fn(),
      refreshPortraits: jest.fn(),
      refreshTileSet: jest.fn((a, b, c, d, e, callback) => {
        if (typeof callback === 'function') callback();
      }),
    };
    const chatMock = {
      ...origChat,
      addDialogMessage: jest.fn((kind, text, type) => {
        dialogMessages.push({ kind, text, type });
      }),
    };
    global.Chat = chatMock;
    global.window.Chat = chatMock;

    q.enqueue({
      type: 'party_membership',
      message: {
        action: 'join',
        entity_uid: 'npc_sister_anya',
        name: 'Sister Anya',
        text: 'Sister Anya joined the party.',
        owners: ['keo'],
      },
    });

    await new Promise((resolve) => {
      const poll = () => { if (!q.processing && q.queue.length === 0) return resolve(); setTimeout(poll, 5); };
      setTimeout(poll, 5);
    });

    expect(global.Utils.showToast).toHaveBeenCalledWith('Sister Anya joined the party.');
    expect(global.Utils.refreshPortraits).toHaveBeenCalled();
    expect(inviteHidden).toBe(true);
    expect(dialogMessages.some((row) => row.text === 'Sister Anya joined the party.')).toBe(true);

    global.$ = orig$;
    global.Utils = origUtils;
    global.Chat = origChat;
    global.window.Chat = origChat;
  });

  test('EventQueue prompt shows alert and posts response', async () => {
    const q = new Engine.EventQueue();
    const alerts = [];
    const origAlert = global.alert;
    global.alert = (msg) => alerts.push(msg);

    const oldAjax = global.$.ajax;
    const ajaxCalls = [];
    global.$.ajax = (opts) => { ajaxCalls.push(opts); if (opts && typeof opts.success === 'function') opts.success('ok'); };

    q.enqueue({ type: 'prompt', message: 'Say something', callback: 'cb123' });

    await new Promise((resolve) => {
      const poll = () => { if (!q.processing && q.queue.length === 0) return resolve(); setTimeout(poll, 5); };
      setTimeout(poll, 5);
    });

    expect(alerts[0]).toBe('Say something');
    expect(ajaxCalls.length).toBe(1);
    expect(ajaxCalls[0].url).toBe('/response');

    // Restore
    global.alert = origAlert;
    global.$.ajax = oldAjax;
  });

  test('EventQueue initiative hides start buttons and shows end-battle', async () => {
    const q = new Engine.EventQueue();
    const actions = { hide: [], show: [] };
    const orig$ = global.$;

    const turnOrderObj = { html: () => ({ }), show: () => ({ }) };

    global.$ = (sel) => {
      if (sel === '#turn-order') return turnOrderObj;
      if (sel === '#battle-turn-order') return { show: () => ({}) };
      if (sel === '#start-initiative, #start-battle') return { hide: () => { actions.hide.push(sel); return this; } };
      if (sel === '#end-battle') return { show: () => { actions.show.push(sel); return this; } };
      return orig$(sel);
    };

    const origUtils = global.Utils;
    global.Utils = { ajaxGet: (url, data, cb) => { if (cb) cb('<li/>'); } };

    q.enqueue({ type: 'initiative' });

    await new Promise((resolve) => { const poll = () => { if (!q.processing && q.queue.length === 0) return resolve(); setTimeout(poll, 5); }; setTimeout(poll, 5); });

    expect(actions.hide).toContain('#start-initiative, #start-battle');
    expect(actions.show).toContain('#end-battle');

    global.$ = orig$;
    global.Utils = origUtils;
  });

  test('EventQueue stop clears turn order, hides panels, toggles buttons', async () => {
    const q = new Engine.EventQueue();
    const record = { cleared: false, gameTurnHidden: false, orderFaded: false, startShown: false, endHidden: false };
    const orig$ = global.$;

    global.$ = (sel) => {
      if (sel === '#turn-order') return { html: (v) => { if (v === '') record.cleared = true; return this; } };
      if (sel === '.game-turn-container') return { hide: () => { record.gameTurnHidden = true; return this; } };
      if (sel === '#battle-turn-order') return { fadeOut: () => { record.orderFaded = true; return this; } };
      if (sel === '#start-initiative, #start-battle') return { show: () => { record.startShown = true; return this; } };
      if (sel === '#end-battle') return { hide: () => { record.endHidden = true; return this; } };
      return orig$(sel);
    };

    q.enqueue({ type: 'stop' });

    await new Promise((resolve) => { const poll = () => { if (!q.processing && q.queue.length === 0) return resolve(); setTimeout(poll, 5); }; setTimeout(poll, 5); });

    expect(record.cleared).toBe(true);
    expect(record.gameTurnHidden).toBe(true);
    expect(record.orderFaded).toBe(true);
    expect(record.startShown).toBe(true);
    expect(record.endHidden).toBe(true);

    global.$ = orig$;
  });

  test('EventQueue switch_map calls Utils.switchMap and runs callback', async () => {
    const q = new Engine.EventQueue();
    const origUtils = global.Utils;
    const calls = [];
    global.Utils = { switchMap: (mapId, canvas, cb) => { calls.push({ mapId, canvas: !!canvas }); if (cb) cb(); } };

    q.enqueue({ type: 'switch_map', message: { map: 'MAP-42' } });

    await new Promise((resolve) => { const poll = () => { if (!q.processing && q.queue.length === 0) return resolve(); setTimeout(poll, 5); }; setTimeout(poll, 5); });

    expect(calls[0].mapId).toBe('MAP-42');
    expect(typeof calls[0].canvas === 'boolean').toBe(true);

    global.Utils = origUtils;
  });

  test('EventQueue reaction fetches modal content and shows modal', async () => {
    const q = new Engine.EventQueue();
    const orig$ = global.$;
    const events = { htmlSet: null, modalShown: false };

    global.$ = (sel) => {
      if (sel === '#reaction-modal .reaction-content') return { html: (v) => { events.htmlSet = v; return this; } };
      if (sel === '#reaction-modal') return { modal: (cmd) => { if (cmd === 'show') events.modalShown = true; return this; } };
      return orig$(sel);
    };

    const origUtils = global.Utils;
    global.Utils = { ajaxGet: (url, data, cb) => { if (url === '/reaction' && cb) cb('<div>content</div>'); } };

    q.enqueue({ type: 'reaction' });

    await new Promise((resolve) => { const poll = () => { if (!q.processing && q.queue.length === 0) return resolve(); setTimeout(poll, 5); }; setTimeout(poll, 5); });

    expect(events.htmlSet).toContain('content');
    expect(events.modalShown).toBe(true);

    global.$ = orig$;
    global.Utils = origUtils;
  });

  test('EventQueue command_response appends to #command-output', async () => {
    const q = new Engine.EventQueue();
    const orig$ = global.$;
    const appended = [];

    global.$ = (sel) => {
      if (sel === '#command-output') return { append: (v) => { appended.push(v); return this; } };
      return orig$(sel);
    };

    q.enqueue({ type: 'command_response', message: 'ACK' });

    await new Promise((resolve) => { const poll = () => { if (!q.processing && q.queue.length === 0) return resolve(); setTimeout(poll, 5); }; setTimeout(poll, 5); });

    expect(appended).toContain('ACK\n');

    global.$ = orig$;
  });

  test('EventQueue narration event calls Utils.showNarration with message and map_name', async () => {
    const q = new Engine.EventQueue();
    const calls = [];
    const origUtils = global.Utils;
    global.Utils = { ...origUtils, showNarration: (narration, mapName) => { calls.push({ narration, mapName }); } };

    q.enqueue({ type: 'narration', message: { on_enter: { title: 'The Library', text: 'A grand room.', once: true } }, map_name: '2nd_floor' });

    await new Promise((resolve) => { const poll = () => { if (!q.processing && q.queue.length === 0) return resolve(); setTimeout(poll, 5); }; setTimeout(poll, 5); });

    expect(calls.length).toBe(1);
    expect(calls[0].narration.on_enter.title).toBe('The Library');
    expect(calls[0].mapName).toBe('2nd_floor');

    global.Utils = origUtils;
  });

  test('EventQueue stoptrack fades out and clears active sound (pauses audio)', async () => {
    const q = new Engine.EventQueue();

    // Mock Audio and seed via track event
    const originalAudio = global.Audio;
    const created = [];
    global.Audio = function (url) { this.url = url; this.volume = 0.5; this.loop = false; this.currentTime = 0; this.play = jest.fn(); this.pause = jest.fn(); created.push(this); };

    // Ensure slider stub exists and modal not visible to avoid updateUI
    const orig$ = global.$;
    global.$ = (sel) => { if (sel === '.volume-slider') return { val: () => ({}) }; if (sel === '#modal-1') return { hasClass: () => false, is: () => false }; return orig$(sel); };

    // Seed active audio
    q.enqueue({ type: 'track', message: { url: 'u.mp3', track_id: 'TR-X', volume: 50, id: 'TR-X' } });
    await new Promise((resolve) => { const poll = () => { if (!q.processing && q.queue.length === 0) return resolve(); setTimeout(poll, 5); }; setTimeout(poll, 5); });

    // Provide async interval that runs callbacks until cleared
    const origSetInterval = global.setInterval;
    const origClearInterval = global.clearInterval;
    global.setInterval = (cb, _ms) => { const handle = { cleared: false }; const tick = () => { if (handle.cleared) return; cb(); setTimeout(tick, 0); }; setTimeout(tick, 0); return handle; };
    global.clearInterval = (handle) => { if (handle) handle.cleared = true; };

    q.enqueue({ type: 'stoptrack' });

    await new Promise((resolve) => { setTimeout(resolve, 20); });
    await new Promise((resolve) => { const poll = () => { if (!q.processing && q.queue.length === 0) return resolve(); setTimeout(poll, 5); }; setTimeout(poll, 5); });

    expect(created[0].pause).toHaveBeenCalled();

    // Restore
    global.setInterval = origSetInterval;
    global.clearInterval = origClearInterval;
    global.Audio = originalAudio;
    global.$ = orig$;
  });

  test('EventQueue volume updates active sound volume and slider, and schedules user volume apply', async () => {
    const q = new Engine.EventQueue();

    // Mock Audio and seed via track event
    const originalAudio = global.Audio;
    const created = [];
    global.Audio = function (url) { this.url = url; this.volume = 1; this.loop = false; this.currentTime = 0; this.play = jest.fn(); this.pause = jest.fn(); created.push(this); };

    // Stub localStorage to avoid errors in DMSoundManager/UserVolumeControl
    const originalLocalStorage = global.localStorage;
    global.localStorage = { getItem: () => null, setItem: () => {} };

    const originalUVC = global.UserVolumeControl;
    global.UserVolumeControl = { applyUserVolume: jest.fn() };

    const orig$ = global.$;
    let sliderValSet = null;
    global.$ = (sel) => {
      if (sel === '.volume-slider') return { val: (v) => { sliderValSet = v; return this; } };
      if (sel === '#modal-1') return { hasClass: () => false, is: () => false }; // avoid updateUI
      if (sel === '.volume-display') return { text: () => ({}) };
      if (sel === 'body' || sel === 'body') return { attr: () => undefined }; // default DM volume 50
      return orig$(sel);
    };

    // Seed via track event
    q.enqueue({ type: 'track', message: { url: 'seed.mp3', track_id: 'T-1', volume: 60, id: 'T-1' } });
    await new Promise((resolve) => { const poll = () => { if (!q.processing && q.queue.length === 0) return resolve(); setTimeout(poll, 5); }; setTimeout(poll, 5); });

    // Now send volume event
    q.enqueue({ type: 'volume', message: { volume: 42 } });

    await new Promise((resolve) => { const poll = () => { if (!q.processing && q.queue.length === 0) return resolve(); setTimeout(poll, 5); }; setTimeout(poll, 5); });

    expect(created[created.length - 1].volume).toBeCloseTo(0.42, 5);
    expect(sliderValSet).toBe(42);

    // Restore
    global.Audio = originalAudio;
    global.$ = orig$;
    global.UserVolumeControl = originalUVC;
    global.localStorage = originalLocalStorage;
  });

  test('EventQueue track creates and plays Audio with correct volume', async () => {
    const q = new Engine.EventQueue();

    const originalAudio = global.Audio;
    const created = [];
    global.Audio = function (url) { this.url = url.startsWith('/assets/') ? url : '/assets/' + url; this.volume = 0; this.loop = false; this.currentTime = 0; this.play = jest.fn(); this.pause = jest.fn(); created.push(this); };

    const orig$ = global.$;
    global.$ = (sel) => { if (sel === '.volume-slider') return { val: () => ({}) }; if (sel === '#modal-1') return { hasClass: () => false, is: () => false }; if (sel === 'body' || sel === 'body') return { attr: () => undefined }; return orig$(sel); };

    // Stub localStorage to avoid errors in UserVolumeControl
    const originalLocalStorage = global.localStorage;
    global.localStorage = { getItem: () => null, setItem: () => {} };

    q.enqueue({ type: 'track', message: { url: 'u.mp3', track_id: 'TR-9', volume: 77, id: 'TR-9' } });

    await new Promise((resolve) => { const poll = () => { if (!q.processing && q.queue.length === 0) return resolve(); setTimeout(poll, 5); }; setTimeout(poll, 5); });

    expect(created[0].url).toBe('/assets/u.mp3');
    // Verify initial volume set by playSound (prior to any user-volume adjustment)
    expect(created[0].volume).toBeCloseTo(0.77, 5);
    expect(created[0].play).toHaveBeenCalled();

    // Restore
    global.Audio = originalAudio;
    global.$ = orig$;
    global.localStorage = originalLocalStorage;
  });

  test('Conversation event: dialog-visible shows dialog; directed-to-POV triggers opener; visual-only skips bubbles; else bubble; invalid payload warns', async () => {
    const q = new Engine.EventQueue();

    // Create DOM nodes referenced by selectors
    const panel = document.createElement('div');
    panel.id = 'jrpgDialogPanel';
    const nameEl = document.createElement('div');
    nameEl.id = 'dialogEntityName';
    nameEl.setAttribute('data-entity-id', 'E1');
    document.body.appendChild(panel);
    document.body.appendChild(nameEl);

    const tile = document.createElement('div');
    tile.className = 'tile';
    tile.setAttribute('data-coords-id', 'E1');
    const nameplate = document.createElement('div');
    nameplate.className = 'nameplate';
    nameplate.textContent = 'NPC-1';
    tile.appendChild(nameplate);
    document.body.appendChild(tile);

    // Spies
    const dialogSpy = jest.spyOn(global.Chat, 'addDialogMessage').mockImplementation(() => {});
    const bubbleSpy = jest.spyOn(global.Chat, 'showConversationBubble').mockImplementation(() => {});
    const triggerSpy = jest.spyOn(global, 'showDialogTriggerBubble').mockImplementation(() => {});
    const povSpy = jest.spyOn(global.Chat, 'getCurrentPovEntity').mockImplementation(() => 'POV');
    const localConversationSpy = jest.spyOn(global.Chat, 'handleLocalConversationEvent').mockImplementation(() => {});

    const orig$ = global.$;

    // Case 1: panel visible and matching entity -> addDialogMessage
    global.$ = (sel) => {
      if (sel === '#jrpgDialogPanel') return { is: (s) => s === ':visible' ? true : false };
      if (sel === '#dialogEntityName') return { data: (k) => k === 'entity-id' ? 'E1' : undefined };
      if (sel === '.tile[data-coords-id="E1"]' || sel === `.tile[data-coords-id="E1"]`) {
        return { length: 1, find: () => ({ length: 1, text: () => 'NPC-1' }) };
      }
      return orig$(sel);
    };
    await q.processEvent({ type: 'conversation', message: { entity_id: 'E1', message: 'Hello' } });
    expect(dialogSpy).toHaveBeenCalledWith('entity', 'Hello', 'entity', expect.objectContaining({
      audio_url: null,
      tts_emotion: 'calm',
      entity_id: 'E1',
    }));
    expect(bubbleSpy).not.toHaveBeenCalled();
    expect(triggerSpy).not.toHaveBeenCalled();

    // Case 2: not visible, directed to POV -> trigger opener
    dialogSpy.mockClear(); bubbleSpy.mockClear(); triggerSpy.mockClear();
    global.$ = (sel) => {
      if (sel === '#jrpgDialogPanel') return { is: () => false };
      if (sel === '#dialogEntityName') return { data: () => 'OTHER' };
      return orig$(sel);
    };
    await q.processEvent({ type: 'conversation', message: { entity_id: 'E2', message: 'Hey POV', targets: ['POV'] } });
    expect(triggerSpy).toHaveBeenCalledWith('E2', 'Hey POV', expect.any(Object));
    expect(dialogSpy).not.toHaveBeenCalled();

    // Case 3: default bubble
    dialogSpy.mockClear(); bubbleSpy.mockClear(); triggerSpy.mockClear();
    await q.processEvent({ type: 'conversation', message: { entity_id: 'E3', message: 'Bubble' } });
    expect(bubbleSpy).toHaveBeenCalledWith('E3', 'Bubble', expect.any(Object));

    // Case 4: visual-only placeholder updates local chat but does not show dialog or bubble
    dialogSpy.mockClear(); bubbleSpy.mockClear(); triggerSpy.mockClear(); localConversationSpy.mockClear();
    await q.processEvent({ type: 'conversation', message: { entity_id: 'E4', message: 'You can see them whispering, but cannot hear the words.', visual_only: true } });
    expect(localConversationSpy).toHaveBeenCalled();
    expect(dialogSpy).not.toHaveBeenCalled();
    expect(bubbleSpy).not.toHaveBeenCalled();
    expect(triggerSpy).not.toHaveBeenCalled();

    // Case 5: invalid payload -> warning only
    dialogSpy.mockClear(); bubbleSpy.mockClear(); triggerSpy.mockClear();
    const warnSpy = jest.spyOn(console, 'warn').mockImplementation(() => {});
    await q.processEvent({ type: 'conversation', message: { entity_id: '', message: '' } });
    expect(warnSpy).toHaveBeenCalled();
    expect(dialogSpy).not.toHaveBeenCalled();
    expect(bubbleSpy).not.toHaveBeenCalled();
    expect(triggerSpy).not.toHaveBeenCalled();
    warnSpy.mockRestore();

    // Case 6: duplicate reply_id (socket + HTTP fallback) renders once
    localConversationSpy.mockClear();
    const renderedReplyIds = new Set();
    global.Chat.shouldSkipConversationReply = (payload) => {
      const replyId = payload && payload.reply_id;
      return replyId ? renderedReplyIds.has(replyId) : false;
    };
    global.Chat.markConversationReplyRendered = (payload) => {
      const replyId = payload && payload.reply_id;
      if (replyId) {
        renderedReplyIds.add(replyId);
      }
    };
    const duplicatePayload = {
      entity_id: 'pip_barmaid',
      message: 'Well, aren\'t you a gentleman?',
      reply_id: 'reply-dup-test',
    };
    await q.processEvent({ type: 'conversation', message: duplicatePayload });
    await q.processEvent({ type: 'conversation', message: duplicatePayload });
    expect(localConversationSpy).toHaveBeenCalledTimes(1);

    // restore
    global.$ = orig$;
    dialogSpy.mockRestore();
    bubbleSpy.mockRestore();
    triggerSpy.mockRestore();
    povSpy.mockRestore();
    localConversationSpy.mockRestore();
  });

  test('tileCoordsFromClientPoint uses transformed tile bounds', () => {
    const orig$ = global.$;
    document.body.innerHTML = `
      <div class="tiles-container">
        <div class="tile" data-coords-x="5" data-coords-y="0"
             style="position:absolute;left:0;top:0;width:50px;height:50px;"></div>
        <div class="tile" data-coords-x="6" data-coords-y="0"
             style="position:absolute;left:50px;top:0;width:50px;height:50px;"></div>
      </div>
    `;
    const tiles = document.querySelectorAll('.tile');
    tiles[0].getBoundingClientRect = () => ({
      left: 0, top: 0, right: 50, bottom: 50, width: 50, height: 50,
    });
    tiles[1].getBoundingClientRect = () => ({
      left: 50, top: 0, right: 100, bottom: 50, width: 50, height: 50,
    });
    document.elementFromPoint = (x, y) => {
      if (x >= 50 && x < 100) return tiles[1];
      if (x >= 0 && x < 50) return tiles[0];
      return null;
    };

    global.$ = (arg) => {
      if (arg && arg.nodeType === 1) {
        const element = arg;
        const tileEl = element.classList && element.classList.contains('tile')
          ? element
          : (element.closest ? element.closest('.tile') : null);
        return {
          length: tileEl ? 1 : 0,
          closest: () => {
            if (!tileEl) {
              return { length: 0, attr: () => undefined };
            }
            return {
              length: 1,
              attr: (name) => tileEl.getAttribute(name),
            };
          },
          attr: (name) => (tileEl ? tileEl.getAttribute(name) : undefined),
        };
      }
      return orig$(arg);
    };

    const hit = global.tileCoordsFromClientPoint(75, 25);
    expect(hit).toEqual(expect.objectContaining({ x: 6, y: 0 }));

    global.$ = orig$;
  });
});

describe('DM Delete / Ctrl+Z (DmDeleteUndo)', () => {
  const $ = global.$;

  afterEach(() => {
    global.$ = $;
  });

  function setBodyRole(role, povUid = null) {
    global.$ = (sel) => {
      if (sel === 'body') {
        return {
          attr: (name) => {
            if (name === 'data-role') return role;
            if (name === 'data-pov-entity') return povUid;
            return null;
          },
          data: () => null,
        };
      }
      return $(sel);
    };
  }

  test('exposes the DmDeleteUndo surface', () => {
    expect(typeof Engine.DmDeleteUndo).toBe('object');
    expect(typeof Engine.DmDeleteUndo.pushUndo).toBe('function');
    expect(typeof Engine.DmDeleteUndo.hotkeyActive).toBe('function');
    expect(typeof Engine.DmDeleteUndo.focusedEntityUid).toBe('function');
    expect(typeof Engine.DmDeleteUndo.stack).toBe('function');
    expect(Engine.DmDeleteUndo.limit).toBe(20);
  });

  test('hotkeyActive requires the DM role and no typing target', () => {
    setBodyRole('dm');
    expect(Engine.DmDeleteUndo.hotkeyActive()).toBe(true);

    setBodyRole('player');
    expect(Engine.DmDeleteUndo.hotkeyActive()).toBe(false);

    setBodyRole(['player']);
    expect(Engine.DmDeleteUndo.hotkeyActive()).toBe(false);

    setBodyRole(['dm', 'player']);
    expect(Engine.DmDeleteUndo.hotkeyActive()).toBe(true);
  });

  test('focusedEntityUid prefers the action-bar entry over the POV entity', () => {
    setBodyRole('dm', 'pov-1');
    expect(Engine.DmDeleteUndo.focusedEntityUid()).toBe('pov-1');

    const bar = Engine.actionBarState;
    const savedVisible = bar.visible;
    const savedUid = bar.activeEntityUid;
    bar.visible = true;
    bar.activeEntityUid = 'npc-42';
    try {
      expect(Engine.DmDeleteUndo.focusedEntityUid()).toBe('npc-42');
    } finally {
      bar.visible = savedVisible;
      bar.activeEntityUid = savedUid;
    }
  });

  test('pushUndo caps the stack at the limit, newest first', () => {
    for (let i = 1; i <= 25; i++) {
      Engine.DmDeleteUndo.pushUndo({ entity_uid: `npc-${i}` });
    }
    const stack = Engine.DmDeleteUndo.stack();
    expect(stack).toHaveLength(20);
    // Oldest five entries were dropped.
    expect(stack[0].entity_uid).toBe('npc-6');
    expect(stack[stack.length - 1].entity_uid).toBe('npc-25');
    // Pushing junk is ignored.
    Engine.DmDeleteUndo.pushUndo(null);
    Engine.DmDeleteUndo.pushUndo({});
    expect(Engine.DmDeleteUndo.stack()).toHaveLength(20);
  });
});
