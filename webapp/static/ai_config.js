/**
 * DM Console > AI Settings panel.
 *
 * Edits one `.ai.yaml` layer at a time (campaign by default, or webapp) via
 * GET/POST /ai/config. Empty fields inherit from the next layer down:
 * campaign -> webapp -> environment variables. API keys come back masked and
 * are preserved server-side unless replaced.
 */
(function (root) {
  'use strict';

  const ROLE_HINTS = {
    dm: 'DM assistant chat and narration',
    npc: 'Default for NPC conversation and autonomous tasks',
    battle: 'Default for LLM combat decisions (e.g. JEV)',
  };
  const PROFILE_FIELDS = [
    ['base_url', 'Endpoint URL', 'text'],
    ['model', 'Model', 'text'],
    ['api_key', 'API key', 'password'],
    ['api_key_env', 'API key env var', 'text'],
    ['context_window', 'Context window', 'number'],
    ['timeout', 'Timeout (s)', 'number'],
    ['max_tokens', 'Max tokens', 'number'],
  ];
  const CONTROLLER_LABELS = { ai: 'Deterministic AI', llm: 'LLM', manual: 'Manual (owner / DM)' };

  function getPath(obj, path) {
    return path.split('.').reduce((node, key) => (node && typeof node === 'object' ? node[key] : undefined), obj);
  }

  /** Set (or delete when value is empty) a dotted path, pruning empty parents. */
  function setPath(obj, path, value) {
    const keys = path.split('.');
    const stack = [];
    let node = obj;
    for (let i = 0; i < keys.length - 1; i += 1) {
      if (!node[keys[i]] || typeof node[keys[i]] !== 'object') {
        if (value === undefined || value === null || value === '') return obj;
        node[keys[i]] = {};
      }
      stack.push([node, keys[i]]);
      node = node[keys[i]];
    }
    const last = keys[keys.length - 1];
    if (value === undefined || value === null || value === '') {
      delete node[last];
      for (let i = stack.length - 1; i >= 0; i -= 1) {
        const [parent, key] = stack[i];
        if (Object.keys(parent[key]).length === 0) delete parent[key];
        else break;
      }
    } else {
      node[last] = value;
    }
    return obj;
  }

  /** Value a field would inherit when left empty in `scope`, with its source label. */
  function inheritedValue(state, scope, path) {
    if (scope === 'campaign') {
      const webappValue = getPath((state.layers || {}).webapp || {}, path);
      if (webappValue !== undefined && webappValue !== null) return { value: webappValue, source: 'webapp' };
    }
    const envValue = getPath(state.env || {}, path);
    if (envValue !== undefined && envValue !== null) return { value: envValue, source: 'env' };
    return null;
  }

  function parseTri(value) {
    if (value === 'true') return true;
    if (value === 'false') return false;
    return undefined;
  }

  function escapeHtml(value) {
    return String(value === undefined || value === null ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function describeInherited(inherited) {
    if (!inherited) return 'default';
    let value = inherited.value;
    if (typeof value === 'boolean') value = value ? 'on' : 'off';
    if (CONTROLLER_LABELS[value]) value = CONTROLLER_LABELS[value];
    return `${value} · ${inherited.source}`;
  }

  const Panel = {
    state: null,
    scope: 'campaign',
    draft: {},
    dirty: false,
    $root: null,

    init($root) {
      this.$root = $root;
      this.bind();
      this.load();
    },

    load() {
      this.$root.find('.ai-config-status').text('Loading…');
      $.ajax({ url: '/ai/config', method: 'GET', dataType: 'json' })
        .done((data) => this.receive(data))
        .fail((xhr) => this.showError(xhr));
    },

    receive(data) {
      this.state = data;
      if (!data.paths || !data.paths.campaign) this.scope = 'webapp';
      this.draft = JSON.parse(JSON.stringify((data.layers || {})[this.scope] || {}));
      this.dirty = false;
      this.render();
    },

    showError(xhr) {
      const msg = (xhr && xhr.responseJSON && xhr.responseJSON.error) || (xhr && xhr.statusText) || 'Request failed';
      this.$root.find('.ai-config-status').html(`<span class="text-danger">${escapeHtml(msg)}</span>`);
    },

    markDirty() {
      this.dirty = true;
      this.$root.find('.ai-config-status').html('<span class="text-warning">Unsaved changes</span>');
    },

    profileOptions(selected, includeInherit = true, inheritLabel = 'inherit') {
      const names = (this.state.profiles || []).concat(Object.keys(this.draft.providers || {}))
        .filter((name, idx, arr) => arr.indexOf(name) === idx);
      let html = includeInherit ? `<option value="">${escapeHtml(inheritLabel)}</option>` : '';
      names.forEach((name) => {
        html += `<option value="${escapeHtml(name)}" ${selected === name ? 'selected' : ''}>${escapeHtml(name)}</option>`;
      });
      return html;
    },

    controllerOptions(selected, inheritLabel = 'inherit') {
      let html = `<option value="">${escapeHtml(inheritLabel)}</option>`;
      (this.state.controller_kinds || ['ai', 'llm', 'manual']).forEach((kind) => {
        html += `<option value="${kind}" ${selected === kind ? 'selected' : ''}>${escapeHtml(CONTROLLER_LABELS[kind] || kind)}</option>`;
      });
      return html;
    },

    triSelect(path, label, help) {
      const current = getPath(this.draft, path);
      const inherited = describeInherited(inheritedValue(this.state, this.scope, path));
      const value = current === true ? 'true' : current === false ? 'false' : '';
      return `
        <div class="form-group ai-config-field">
          <label>${escapeHtml(label)}</label>
          <select class="form-control input-sm" data-path="${path}" data-kind="tri">
            <option value="" ${value === '' ? 'selected' : ''}>inherit (${escapeHtml(inherited)})</option>
            <option value="true" ${value === 'true' ? 'selected' : ''}>On</option>
            <option value="false" ${value === 'false' ? 'selected' : ''}>Off</option>
          </select>
          ${help ? `<p class="help-block">${escapeHtml(help)}</p>` : ''}
        </div>`;
    },

    selectField(path, label, optionsHtml, help) {
      return `
        <div class="form-group ai-config-field">
          <label>${escapeHtml(label)}</label>
          <select class="form-control input-sm" data-path="${path}">${optionsHtml}</select>
          ${help ? `<p class="help-block">${escapeHtml(help)}</p>` : ''}
        </div>`;
    },

    inheritLabel(path) {
      return `inherit (${describeInherited(inheritedValue(this.state, this.scope, path))})`;
    },

    render() {
      const s = this.state;
      const paths = s.paths || {};
      const html = `
        <div class="ai-config-toolbar">
          <label>Edit layer</label>
          <select class="form-control input-sm ai-config-scope">
            <option value="campaign" ${this.scope === 'campaign' ? 'selected' : ''} ${paths.campaign ? '' : 'disabled'}>Campaign .ai.yaml</option>
            <option value="webapp" ${this.scope === 'webapp' ? 'selected' : ''}>Webapp .ai.yaml (all campaigns)</option>
          </select>
          <code class="ai-config-path" title="${escapeHtml(paths[this.scope])}">${escapeHtml(paths[this.scope] || '')}</code>
          <span class="ai-config-actions">
            <button type="button" class="btn btn-default btn-sm ai-config-reload" title="Re-read both files from disk and re-apply">Reload</button>
            <button type="button" class="btn btn-primary btn-sm ai-config-save">Save</button>
          </span>
        </div>
        <p class="ai-config-legend text-muted">Empty fields inherit: campaign → webapp → environment variables. Saving re-initializes only changed providers.</p>
        <div class="ai-config-status"></div>
        <ul class="nav nav-pills ai-config-subnav">
          <li class="active"><a href="#ai-cfg-providers" data-toggle="pill">Providers</a></li>
          <li><a href="#ai-cfg-combat" data-toggle="pill">Combat</a></li>
          <li><a href="#ai-cfg-npc-ai" data-toggle="pill">Conversation &amp; Tasks</a></li>
          <li><a href="#ai-cfg-npcs" data-toggle="pill">Per-NPC</a></li>
        </ul>
        <div class="tab-content ai-config-body">
          <div class="tab-pane active" id="ai-cfg-providers">${this.renderProviders()}</div>
          <div class="tab-pane" id="ai-cfg-combat">${this.renderCombat()}</div>
          <div class="tab-pane" id="ai-cfg-npc-ai">${this.renderNpcAi()}</div>
          <div class="tab-pane" id="ai-cfg-npcs">${this.renderNpcs()}</div>
        </div>`;
      const activePane = this.$root.find('.ai-config-subnav li.active a').attr('href');
      this.$root.html(html);
      if (activePane) this.$root.find(`.ai-config-subnav a[href="${activePane}"]`).tab('show');
      if (this.dirty) this.markDirty();
    },

    providerStatus(name) {
      const status = (this.state.status || {})[name];
      if (!status) return '';
      const ready = status.initialized || status.provider_type;
      const model = status.current_model || status.model || '';
      const type = status.provider_type || status.provider || '';
      return ready
        ? `<span class="label label-success">live: ${escapeHtml(type)} ${escapeHtml(model)}</span>`
        : '<span class="label label-default">not initialized</span>';
    },

    renderProfile(name) {
      const base = `providers.${name}`;
      const profile = getPath(this.draft, base) || {};
      const envProfile = getPath(this.state.env || {}, base) || {};
      const inheritedProvider = inheritedValue(this.state, this.scope, `${base}.provider`);
      const reserved = (this.state.reserved_profiles || []).indexOf(name) >= 0;
      let providerOpts = `<option value="">${reserved ? escapeHtml(`inherit (${describeInherited(inheritedProvider)})`) : '— choose —'}</option>`;
      (this.state.provider_names || []).forEach((p) => {
        providerOpts += `<option value="${p}" ${profile.provider === p ? 'selected' : ''}>${p}</option>`;
      });
      const fields = PROFILE_FIELDS.map(([key, label, type]) => {
        const inherited = inheritedValue(this.state, this.scope, `${base}.${key}`);
        let placeholder = inherited ? `${inherited.value} (${inherited.source})` : '';
        if (key === 'api_key') {
          placeholder = profile.api_key === this.state.secret_mask ? 'saved (hidden) — type to replace'
            : envProfile.api_key_set ? 'set via env' : '';
        }
        const value = key === 'api_key' && profile.api_key === this.state.secret_mask ? '' : (profile[key] ?? '');
        return `
          <div class="col-sm-6 form-group ai-config-field">
            <label>${escapeHtml(label)}</label>
            <input class="form-control input-sm" type="${type}" data-path="${base}.${key}" data-kind="${type === 'number' ? 'int' : 'text'}"
              value="${escapeHtml(value)}" placeholder="${escapeHtml(placeholder)}" autocomplete="off">
          </div>`;
      }).join('');
      const enabledToggle = name === 'dm' ? '' : `
          <label class="checkbox-inline ai-config-disable">
            <input type="checkbox" data-path="${base}.enabled" data-kind="disable" ${profile.enabled === false ? 'checked' : ''}> Disabled
          </label>`;
      return `
        <div class="panel panel-default ai-config-profile" data-profile="${escapeHtml(name)}">
          <div class="panel-heading">
            <strong>${escapeHtml(name)}</strong>
            <small class="text-muted">${escapeHtml(ROLE_HINTS[name] || 'Custom profile (assign it below)')}</small>
            ${this.providerStatus(name)}
            <span class="pull-right">
              ${enabledToggle}
              <button type="button" class="btn btn-xs btn-info ai-config-test" data-profile="${escapeHtml(name)}">Test</button>
              ${reserved ? '' : `<button type="button" class="btn btn-xs btn-danger ai-config-remove-profile" data-profile="${escapeHtml(name)}">Remove</button>`}
            </span>
          </div>
          <div class="panel-body">
            <div class="row">
              <div class="col-sm-6 form-group ai-config-field">
                <label>Provider</label>
                <select class="form-control input-sm" data-path="${base}.provider">${providerOpts}</select>
              </div>
              ${fields}
            </div>
            <div class="ai-config-test-result"></div>
          </div>
        </div>`;
    },

    renderProviders() {
      const names = (this.state.reserved_profiles || ['dm', 'npc', 'battle'])
        .concat(Object.keys(this.draft.providers || {}))
        .filter((name, idx, arr) => arr.indexOf(name) === idx);
      return `
        ${names.map((name) => this.renderProfile(name)).join('')}
        <div class="form-inline ai-config-add-profile">
          <input type="text" class="form-control input-sm ai-config-new-profile" placeholder="profile name (e.g. fast_chat)">
          <button type="button" class="btn btn-default btn-sm ai-config-add-profile-btn">Add custom profile</button>
          <span class="help-block">Custom profiles can be assigned to combat, conversation or tasks, globally or per NPC.</span>
        </div>`;
    },

    renderCombat() {
      return `
        <h5>NPC combat</h5>
        <div class="row">
          <div class="col-sm-6">${this.selectField('combat.npc_controller', 'Default NPC controller',
            this.controllerOptions(getPath(this.draft, 'combat.npc_controller'), this.inheritLabel('combat.npc_controller')),
            'Deterministic AI uses the built-in heuristics; LLM asks a model each turn and falls back to heuristics.')}</div>
          <div class="col-sm-6">${this.selectField('combat.llm_provider', 'LLM combat profile',
            this.profileOptions(getPath(this.draft, 'combat.llm_provider'), true, 'inherit (battle → dm)'))}</div>
          <div class="col-sm-6">${this.triSelect('combat.force_llm', 'Force LLM for every NPC', 'Overrides per-NPC controllers (NPC_LLM_COMBAT_ENABLED).')}</div>
        </div>
        <h5>Sidekicks in the party</h5>
        <p class="help-block">Sidekicks with a player owner stay under that player unless a controller is set here. Manual control with no owner goes to the DM.</p>
        <div class="row">
          <div class="col-sm-6">${this.selectField('sidekicks.combat.controller', 'Sidekick controller',
            this.controllerOptions(getPath(this.draft, 'sidekicks.combat.controller'), 'inherit (owner, else NPC default)'))}</div>
          <div class="col-sm-6">${this.selectField('sidekicks.combat.provider', 'Sidekick LLM combat profile',
            this.profileOptions(getPath(this.draft, 'sidekicks.combat.provider'), true, 'inherit'))}</div>
        </div>`;
    },

    renderNpcAi() {
      return `
        <h5>Conversation</h5>
        <div class="row">
          <div class="col-sm-6">${this.triSelect('conversation.enabled', 'Dedicated NPC provider', 'Off = NPC dialogue uses the DM provider (NPC_LLM_ENABLED).')}</div>
          <div class="col-sm-6">${this.selectField('conversation.provider', 'Conversation profile',
            this.profileOptions(getPath(this.draft, 'conversation.provider'), true, 'inherit (npc → dm)'))}</div>
        </div>
        <h5>Tasks (goals, movement, environment ticks, long-rest simulation)</h5>
        <div class="row">
          <div class="col-sm-6">${this.selectField('tasks.provider', 'Task profile',
            this.profileOptions(getPath(this.draft, 'tasks.provider'), true, 'inherit (conversation profile)'))}</div>
          <div class="col-sm-6 form-group ai-config-field">
            <label>Goal turn interval (s)</label>
            <input class="form-control input-sm" type="number" min="1" data-path="tasks.goal_turn_seconds" data-kind="int"
              value="${escapeHtml(getPath(this.draft, 'tasks.goal_turn_seconds') ?? '')}"
              placeholder="${escapeHtml(this.inheritLabel('tasks.goal_turn_seconds'))}">
          </div>
          <div class="col-sm-4">${this.triSelect('tasks.autonomous_loops', 'Autonomous loops', 'Master switch (N20_NPC_LOOPS).')}</div>
          <div class="col-sm-4">${this.triSelect('tasks.background_llm', 'Background LLM calls', 'N20_NPC_BACKGROUND_LLM')}</div>
          <div class="col-sm-4">${this.triSelect('tasks.environment_ticks', 'Environment ticks', 'N20_NPC_ENV_TICKS')}</div>
        </div>
        <h5>Sidekick defaults</h5>
        <div class="row">
          <div class="col-sm-4">${this.selectField('sidekicks.conversation.provider', 'Conversation profile',
            this.profileOptions(getPath(this.draft, 'sidekicks.conversation.provider'), true, 'inherit'))}</div>
          <div class="col-sm-4">${this.selectField('sidekicks.tasks.provider', 'Task profile',
            this.profileOptions(getPath(this.draft, 'sidekicks.tasks.provider'), true, 'inherit'))}</div>
          <div class="col-sm-4">${this.triSelect('sidekicks.tasks.enabled', 'Autonomous tasks')}</div>
        </div>`;
    },

    npcRow(key, label, meta) {
      const base = `npcs.${key}`;
      const resolved = meta.resolved || {};
      const hint = (path) => {
        const r = resolved[path];
        if (!r || r.value === null || r.value === undefined) return '';
        let value = r.value;
        if (typeof value === 'boolean') value = value ? 'on' : 'off';
        return `<div class="ai-config-resolved">→ ${escapeHtml(value)} <small>(${escapeHtml(r.source)})</small></div>`;
      };
      const sel = (path, options) => `<select class="form-control input-sm" data-path="${base}.${path}">${options}</select>${hint(path)}`;
      const tasks = getPath(this.draft, `${base}.tasks.enabled`);
      return `
        <tr data-npc-key="${escapeHtml(key)}">
          <td>
            <strong>${escapeHtml(label)}</strong>
            ${meta.sidekick ? '<span class="label label-info">sidekick</span>' : ''}
            <div class="text-muted small">${escapeHtml(key)}${meta.map ? ` · ${escapeHtml(meta.map)}` : ''}</div>
          </td>
          <td>${sel('combat.controller', this.controllerOptions(getPath(this.draft, `${base}.combat.controller`)))}</td>
          <td>${sel('combat.provider', this.profileOptions(getPath(this.draft, `${base}.combat.provider`)))}</td>
          <td>${sel('conversation.provider', this.profileOptions(getPath(this.draft, `${base}.conversation.provider`)))}</td>
          <td>
            <select class="form-control input-sm" data-path="${base}.tasks.enabled" data-kind="tri">
              <option value="" ${tasks === undefined ? 'selected' : ''}>inherit</option>
              <option value="true" ${tasks === true ? 'selected' : ''}>On</option>
              <option value="false" ${tasks === false ? 'selected' : ''}>Off</option>
            </select>${hint('tasks.enabled')}
          </td>
          <td>${sel('tasks.provider', this.profileOptions(getPath(this.draft, `${base}.tasks.provider`)))}</td>
        </tr>`;
    },

    renderNpcs() {
      const npcs = this.state.npcs || [];
      const overrides = this.draft.npcs || {};
      const known = new Set();
      const rows = [];
      npcs.forEach((npc) => {
        const key = this.npcKeyMode === 'type' && npc.npc_type ? npc.npc_type : npc.entity_uid;
        if (known.has(key)) return;
        known.add(key);
        rows.push(this.npcRow(key, npc.label, npc));
      });
      Object.keys(overrides).filter((key) => !known.has(key)).forEach((key) => {
        rows.push(this.npcRow(key, key, { resolved: {} }));
      });
      return `
        <div class="form-inline ai-config-npc-tools">
          <input type="search" class="form-control input-sm ai-config-npc-search" placeholder="Filter NPCs…">
          <label class="radio-inline"><input type="radio" name="ai-npc-key" value="uid" ${this.npcKeyMode !== 'type' ? 'checked' : ''}> Per individual</label>
          <label class="radio-inline"><input type="radio" name="ai-npc-key" value="type" ${this.npcKeyMode === 'type' ? 'checked' : ''}> Per NPC type</label>
        </div>
        <p class="help-block">Blank = inherit (individual → type → sidekick defaults → general defaults). "→" shows what is in effect now.</p>
        <div class="ai-config-npc-table-wrap">
          <table class="table table-condensed ai-config-npc-table">
            <thead><tr><th>NPC</th><th>Combat</th><th>Combat profile</th><th>Conversation</th><th>Tasks</th><th>Task profile</th></tr></thead>
            <tbody>${rows.join('') || '<tr><td colspan="6" class="text-muted">No NPCs on loaded maps.</td></tr>'}</tbody>
          </table>
        </div>`;
    },

    readInput($el) {
      const kind = $el.data('kind');
      const raw = $el.is(':checkbox') ? $el.is(':checked') : $el.val();
      if (kind === 'tri') return parseTri(raw);
      if (kind === 'disable') return raw ? false : undefined;
      if (kind === 'int') return raw === '' ? undefined : parseInt(raw, 10);
      return raw === '' ? undefined : raw;
    },

    bind() {
      const self = this;
      this.$root.on('change input', '[data-path]', function () {
        const $el = $(this);
        self.draft = setPath(self.draft, $el.data('path'), self.readInput($el));
        self.markDirty();
      });
      this.$root.on('change', '.ai-config-scope', function () {
        if (self.dirty && !window.confirm('Discard unsaved changes to this layer?')) {
          $(this).val(self.scope);
          return;
        }
        self.scope = $(this).val();
        self.draft = JSON.parse(JSON.stringify((self.state.layers || {})[self.scope] || {}));
        self.dirty = false;
        self.render();
      });
      this.$root.on('change', 'input[name="ai-npc-key"]', function () {
        self.npcKeyMode = $(this).val();
        self.$root.find('#ai-cfg-npcs').html(self.renderNpcs());
      });
      this.$root.on('input', '.ai-config-npc-search', function () {
        const term = $(this).val().toLowerCase();
        self.$root.find('.ai-config-npc-table tbody tr').each(function () {
          $(this).toggle($(this).text().toLowerCase().indexOf(term) >= 0);
        });
      });
      this.$root.on('click', '.ai-config-add-profile-btn', () => {
        const name = (self.$root.find('.ai-config-new-profile').val() || '').trim().replace(/[^A-Za-z0-9_-]/g, '_');
        if (!name) return;
        if ((self.draft.providers || {})[name]) return;
        setPath(self.draft, `providers.${name}.provider`, (self.state.provider_names || ['llama_cpp'])[0]);
        self.markDirty();
        self.render();
      });
      this.$root.on('click', '.ai-config-remove-profile', function () {
        const name = $(this).data('profile');
        if (self.draft.providers) delete self.draft.providers[name];
        self.markDirty();
        self.render();
      });
      this.$root.on('click', '.ai-config-test', function () {
        const name = $(this).data('profile');
        const profile = Object.assign({}, getPath(self.draft, `providers.${name}`) || {});
        if (!profile.provider) {
          const inherited = inheritedValue(self.state, self.scope, `providers.${name}`);
          Object.assign(profile, (inherited && inherited.value) || {});
        }
        const $result = $(this).closest('.ai-config-profile').find('.ai-config-test-result');
        $result.html('<span class="text-muted">Testing…</span>');
        $.ajax({
          url: '/ai/config/test', method: 'POST', contentType: 'application/json',
          data: JSON.stringify({ name, profile }), dataType: 'json',
        }).done((res) => {
          const models = (res.available_models || []).slice(0, 8).join(', ');
          $result.html(res.success
            ? `<span class="text-success">OK — ${escapeHtml(res.provider)} ${escapeHtml(res.model || '')}</span>${models ? `<div class="small text-muted">Models: ${escapeHtml(models)}</div>` : ''}`
            : `<span class="text-danger">Failed: ${escapeHtml(res.error || 'provider did not initialize')}</span>`);
        }).fail((xhr) => {
          $result.html(`<span class="text-danger">${escapeHtml((xhr.responseJSON && xhr.responseJSON.error) || 'Test failed')}</span>`);
        });
      });
      this.$root.on('click', '.ai-config-save', () => {
        self.$root.find('.ai-config-status').text('Saving…');
        $.ajax({
          url: '/ai/config', method: 'POST', contentType: 'application/json',
          data: JSON.stringify({ scope: self.scope, config: self.draft }), dataType: 'json',
        }).done((data) => {
          self.receive(data);
          const applied = Object.keys(data.applied || {}).filter((k) => !k.endsWith('_error'));
          const note = applied.length ? ` Re-initialized: ${applied.map((k) => `${k} ${data.applied[k] ? '✓' : '✗'}`).join(', ')}.` : '';
          self.$root.find('.ai-config-status').html(`<span class="text-success">Saved.${escapeHtml(note)}</span>`);
        }).fail((xhr) => self.showError(xhr));
      });
      this.$root.on('click', '.ai-config-reload', () => {
        $.ajax({ url: '/ai/config/reload', method: 'POST', dataType: 'json' })
          .done((data) => self.receive(data))
          .fail((xhr) => self.showError(xhr));
      });
    },
  };

  const api = { Panel, getPath, setPath, inheritedValue, parseTri, escapeHtml };
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  root.AiConfigPanel = api;

  if (typeof $ === 'function' && typeof document !== 'undefined') {
    $(function () {
      const $root = $('#ai-config-root');
      if (!$root.length) return;
      let started = false;
      $('#ai-config-tab').on('shown.bs.tab click', () => {
        if (started) return;
        started = true;
        Panel.init($root);
      });
    });
  }
}(typeof window !== 'undefined' ? window : globalThis));
