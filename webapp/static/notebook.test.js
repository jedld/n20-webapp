/**
 * @jest-environment jsdom
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

function loadNotebook() {
  document.body.innerHTML = '';
  document.body.setAttribute('data-role', 'dm');
  const jQueryFn = require('jquery');
  jQueryFn.ajax = () => ({
    done() { return this; },
    fail() { return this; },
    always() { return this; },
  });
  const code = fs.readFileSync(path.join(__dirname, 'notebook.js'), 'utf8');
  const sandbox = {
    window,
    document,
    jQuery: jQueryFn,
    $: jQueryFn,
    setTimeout,
    clearTimeout,
    console,
    localStorage: window.localStorage,
  };
  vm.runInNewContext(code, sandbox);
  return window.Notebook;
}

describe('Notebook markdown renderer', () => {
  let Notebook;

  beforeAll(() => {
    Notebook = loadNotebook();
    if (Notebook && typeof Notebook.init === 'function') {
      Notebook.init();
    }
  });

  test('renders headings, emphasis, and links safely', () => {
    const html = Notebook.renderMarkdown('# Title\n\n**bold** and *italic*\n\n[go](https://example.com)\n\n<script>x</script>');
    expect(html).toContain('<h1>Title</h1>');
    expect(html).toContain('<strong>bold</strong>');
    expect(html).toContain('<em>italic</em>');
    expect(html).toContain('href="https://example.com"');
    expect(html).not.toContain('<script>');
  });

  test('DM panel includes Assistant tab host', () => {
    expect(document.querySelector('.notebook-tab[data-tab="assistant"]')).toBeTruthy();
    expect(document.getElementById('notebook-ai-host')).toBeTruthy();
    expect(document.querySelector('.notebook-tab[data-tab="map-pins"]')).toBeTruthy();
  });

  test('open assistant tab shows the AI host', () => {
    Notebook.open('assistant');
    expect(document.body.classList.contains('notebook-open')).toBe(true);
    expect(document.querySelector('.notebook-tab[data-tab="assistant"]').classList.contains('is-active')).toBe(true);
    expect(document.getElementById('notebook-ai-host').style.display).toBe('flex');
    expect(document.querySelector('.notebook-body-main').style.display).toBe('none');
  });
});
