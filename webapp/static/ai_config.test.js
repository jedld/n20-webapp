const { setPath, getPath, inheritedValue, parseTri, escapeHtml } = require('./ai_config');

describe('ai_config panel helpers', () => {
  test('setPath creates nested values and prunes empty parents on clear', () => {
    const draft = {};
    setPath(draft, 'npcs.goblin.combat.controller', 'llm');
    expect(getPath(draft, 'npcs.goblin.combat.controller')).toBe('llm');
    setPath(draft, 'npcs.goblin.combat.controller', '');
    expect(draft).toEqual({});
  });

  test('setPath keeps siblings when clearing one key', () => {
    const draft = { providers: { dm: { provider: 'ollama', model: 'm' } } };
    setPath(draft, 'providers.dm.model', undefined);
    expect(draft).toEqual({ providers: { dm: { provider: 'ollama' } } });
  });

  test('setPath ignores clears for paths that do not exist', () => {
    const draft = { combat: { npc_controller: 'ai' } };
    setPath(draft, 'tasks.provider', '');
    expect(draft).toEqual({ combat: { npc_controller: 'ai' } });
  });

  test('inheritedValue walks webapp layer then env for campaign scope', () => {
    const state = {
      layers: { webapp: { combat: { npc_controller: 'llm' } } },
      env: { tasks: { goal_turn_seconds: 6 }, combat: { npc_controller: 'ai' } },
    };
    expect(inheritedValue(state, 'campaign', 'combat.npc_controller')).toEqual({ value: 'llm', source: 'webapp' });
    expect(inheritedValue(state, 'webapp', 'combat.npc_controller')).toEqual({ value: 'ai', source: 'env' });
    expect(inheritedValue(state, 'campaign', 'tasks.goal_turn_seconds')).toEqual({ value: 6, source: 'env' });
    expect(inheritedValue(state, 'campaign', 'tasks.provider')).toBeNull();
  });

  test('parseTri maps select values to booleans or inherit', () => {
    expect(parseTri('true')).toBe(true);
    expect(parseTri('false')).toBe(false);
    expect(parseTri('')).toBeUndefined();
  });

  test('escapeHtml neutralizes markup', () => {
    expect(escapeHtml('<b a="1">&</b>')).toBe('&lt;b a=&quot;1&quot;&gt;&amp;&lt;/b&gt;');
  });
});
