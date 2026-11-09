'use strict';

/* Overview, server process control, Config Studio and the curated game-system editors. */

function metricCard(label, value, note, icon, className = '') {
  return `<div class="metric ${className}">
    <span class="metric-label">${svgIcon(icon)}${esc(label)}</span>
    <span class="metric-value">${esc(value)}</span>
    <span class="metric-note">${esc(note)}</span>
  </div>`;
}

function showErrorDialog(title, error) {
  const dialog = dialogBase(`
    <div class="dialog-head"><div class="grow"><h2>${esc(title)}</h2>
      <p>The action failed. Nothing was written unless the message says otherwise.</p></div>
      <button class="button text icon-only" data-close aria-label="Close">${svgIcon('close')}</button></div>
    <div class="dialog-body"><div class="code" style="max-height:380px">${esc(error && error.message ? error.message : error)}</div></div>
    <div class="dialog-actions"><button class="button" data-close>Close</button></div>`, 'wide');
  return dialog;
}

async function serverAction(action) {
  const labels = { start: 'Starting the game server…', stop: 'Stopping the game server…', restart: 'Restarting the game server…' };
  snack(labels[action] || 'Working…');
  try {
    const result = await postJson('/api/admin/server/' + action);
    snack(result.message || 'Server ' + action + ' complete');
    await getStatus(true);
    renderRoute();
  } catch (error) {
    await getStatus(true).catch(() => {});
    showErrorDialog('Server ' + action + ' failed', error);
  }
}

async function restoreBackup(backup) {
  if (!await confirmDialog('Restore backup?', 'The current file is backed up first, then replaced by ' + backup + '.', 'Restore')) return;
  try {
    const result = await postJson('/api/admin/restore', { backup });
    snack('Restored ' + result.restored);
    await getStatus(true);
    renderRoute();
  } catch (error) { snack(error.message, true); }
}

/* ------------------------------------------------------------------ overview */
async function renderOverview() {
  const view = $('#view');
  const status = await getStatus(true);
  const platform = await getPlatformStatus(true);
  let backups = [];
  let backupRoot = '';
  try {
    const backupData = await getJson('/api/admin/backups');
    backups = backupData.backups || [];
    backupRoot = backupData.root || '';
  } catch (error) { /* optional */ }
  const profile = status.profile || {};
  const server = status.server || {};
  const database = status.database || {};
  const gm = status.game_master || {};
  const validation = status.validation || { errors: [], warnings: [] };
  const platformRooms = (platform && platform.rooms_public) || [];
  const mqtt = (platform && platform.mqtt) || {};
  const features = profile.features || {};
  const errors = validation.errors || [];
  const warnings = validation.warnings || [];

  view.innerHTML = `
    <section class="hero">
      <div class="hero-mark">D</div>
      <div class="grow">
        <div class="pill-row" style="margin-bottom:8px">
          <span class="chip ${server.running ? 'success' : 'outline'}">
            <span class="dot-live ${server.running ? '' : 'off'}"></span>${server.running ? 'Server listening' : 'Server stopped'}
          </span>
          <span class="chip outline">${esc(status.active_profile || 'No profile')}</span>
          ${gm.gmts_present ? `<span class="chip success">${svgIcon('check')}Game Master ready</span>` : `<span class="chip warn">${svgIcon('warning')}Game Master incomplete</span>`}
        </div>
        <h1>Server overview</h1>
        <p>Status for the game server, active profile, database, files, Game Master and platform rooms.</p>
      </div>
      <div class="row">
        ${server.running
          ? `<button class="button danger-filled" data-action="stop">${svgIcon('stop')} Stop server</button>`
          : `<button class="button" data-action="start">${svgIcon('play')} Start server</button>`}
        <button class="button tonal" data-go="config">${svgIcon('tune')} Config Studio</button>
      </div>
    </section>

    <div class="grid four" style="margin-top:16px">
      ${metricCard('Game server', server.running ? 'Online' : 'Offline',
        server.port ? (server.url || ('port ' + server.port)) : 'No port configured', 'server', server.running ? 'primary' : 'error')}
      ${metricCard('Active profile', profile.name || 'none', profile.path || 'Choose a profile in Config Studio', 'tune')}
      ${metricCard('Accounts', String(database.accounts ?? 0),
        (database.players ?? 0) + ' players · ' + (database.pokemon ?? 0) + ' Pokémon', 'users')}
      ${metricCard('Editable files', String(status.documents ?? 0),
        (status.databases ?? 0) + ' SQLite databases · ' + (status.backups ?? 0) + ' backups', 'folder')}
    </div>

    ${errors.length || warnings.length ? `<div style="margin-top:16px">${validationNotice(validation)}</div>` : ''}

    <div class="grid two" style="margin-top:16px">
      <section class="card">
        <div class="card-header">
          <div class="grow"><h3>Server process</h3><p>Start, stop and watch the game server that uses the active profile.</p></div>
          <span class="dot-live ${server.running ? '' : 'off'}"></span>
        </div>
        <div class="kv">
          <span class="k">State</span><span class="v">${esc(server.message || (server.running ? 'Listening' : 'Stopped'))}</span>
          <span class="k">Profile</span><span class="v">${esc(server.profile || status.active_profile || '—')}</span>
          <span class="k">Listen address</span><span class="v mono">${esc(server.host || '—')}:${esc(server.port ?? '—')}</span>
          <span class="k">Managed PID</span><span class="v mono">${esc(server.pid ?? 'not managed')}</span>
          <span class="k">Health</span><span class="v mono">${esc(server.health_url || '—')}</span>
        </div>
        <div class="row" style="margin-top:16px">
          ${server.running
            ? `<button class="button danger" data-action="stop">${svgIcon('stop')} Stop</button>
               <button class="button tonal" data-action="restart">${svgIcon('restart')} Restart</button>`
            : `<button class="button" data-action="start">${svgIcon('play')} Start</button>`}
          <button class="button text" data-go="server">${svgIcon('arrow')} Server control</button>
        </div>
      </section>

      <section class="card">
        <div class="card-header">
          <div class="grow"><h3>Active profile</h3><p>${esc(profile.path || 'No config profile detected')}</p></div>
          <span class="badge">${esc(profile.network || 'network?')}</span>
        </div>
        <div class="kv">
          <span class="k">Database</span><span class="v mono">${esc(database.path || '—')}</span>
          <span class="k">Listen</span><span class="v mono">${esc(profile.host || '—')}:${esc(profile.port ?? '—')}</span>
          <span class="k">Authentication</span><span class="v">${esc(profile.authentication || '—')} · ${esc(profile.registration || '—')}</span>
          <span class="k">Portable profile</span><span class="v mono">${esc(profile.profile || '—')}</span>
          <span class="k">Modified</span><span class="v">${esc(ago(profile.modified_ms))}</span>
        </div>
        <div class="pill-row" style="margin-top:12px">${featureChips(features)}</div>
        <div class="row" style="margin-top:16px">
          <button class="button tonal" data-go="config">${svgIcon('edit')} Edit profile</button>
          <button class="button text" data-go="files">${svgIcon('folder')} All files</button>
        </div>
      </section>

      <section class="card">
        <div class="card-header">
          <div class="grow"><h3>Platform &amp; rooms</h3><p>The local HiveMQ room service and automatic POIs.</p></div>
          <span class="chip ${mqtt.connected ? 'success' : 'warn'}">${mqtt.connected ? 'MQTT online' : 'MQTT offline'}</span>
        </div>
        <div class="kv">
          <span class="k">Hosted room</span><span class="v mono">${esc((platform && platform.hosted) || 'none')}</span>
          <span class="k">Visible rooms</span><span class="v">${platformRooms.length}</span>
          <span class="k">Broker</span><span class="v mono">${esc(mqtt.host || '—')}:${esc(mqtt.port ?? '—')}</span>
          <span class="k">Public URL</span><span class="v mono">${esc((platform && platform.public_url) || 'local only')}</span>
        </div>
        <div class="row" style="margin-top:16px">
          <button class="button tonal" data-go="platform">${svgIcon('cloud')} Manage rooms</button>
        </div>
      </section>

      <section class="card">
        <div class="card-header">
          <div class="grow"><h3>Game Master &amp; databases</h3><p>Read-only health summary; edit the sources on their pages.</p></div>
          <span class="chip ${gm.gmts_present ? 'success' : 'warn'}">${gm.gmts_present ? 'GMTS present' : 'GMTS missing'}</span>
        </div>
        <div class="kv">
          <span class="k">Game Master</span><span class="v">${(gm.groups || []).filter((group) => group.present).length} folders · ${(gm.sources || []).length} sources</span>
          <span class="k">APK in release</span><span class="v">${gm.apk_present ? 'present (unexpected)' : 'no APK — as intended'}</span>
          <span class="k">Database</span><span class="v">${database.exists ? fmtBytes(database.bytes) + ' · ' + (database.tables || 0) + ' tables' : 'missing'}</span>
          <span class="k">World data</span><span class="v">${(status.assets || {})['Power Spot index'] ? ((status.assets || {})['Power Spot index'].present ? 'power spots included' : 'no power spot index') : '—'}</span>
        </div>
        <div class="row" style="margin-top:16px">
          <button class="button tonal" data-go="gamemaster">${svgIcon('layers')} Game Master</button>
          <button class="button text" data-go="database">${svgIcon('database')} Database</button>
        </div>
      </section>
    </div>

    <div class="grid two" style="margin-top:16px">
      <section class="card">
        <div class="card-header"><div class="grow"><h3>Quick actions</h3><p>Open a section directly.</p></div></div>
        <div class="quick-grid">
          ${[['config', 'tune', 'Every config key'], ['spawns', 'sparkles', 'Spawns & catching'],
             ['raids', 'swords', 'Raids'], ['liveops', 'calendar', 'LiveOps'],
             ['research', 'flask', 'Research'], ['rocket', 'rocket', 'Rocket'],
             ['pvp', 'trophy', 'PvP'], ['max', 'bolt', 'Max system'],
             ['loot', 'gift', 'Loot & rewards'], ['world', 'globe', 'World & weather'],
             ['players', 'users', 'Players'], ['files', 'folder', 'File studio'],
             ['database', 'database', 'Database rows & SQL'], ['logs', 'terminal', 'Live logs']]
            .map(([route, icon, label]) => `<button class="quick-action" data-go="${route}">${svgIcon(icon)}<span>${esc(label)}</span></button>`).join('')}
        </div>
      </section>

      <section class="card">
        <div class="card-header"><div class="grow"><h3>Recent backups</h3><p>Every mutation is copied here first.</p></div>
          <span class="chip outline">${backups.length} recent</span></div>
        ${backups.length ? `<div class="timeline">${backups.slice(0, 7).map((row) => `
          <div class="timeline-item">
            <span class="when">${esc(ago(row.modified_ms))}</span>
            <span class="grow mono" style="flex:1">${esc(row.target || row.backup)}</span>
            <button class="button text small" data-restore="${esc(row.backup)}">Restore</button>
          </div>`).join('')}</div>`
          : `<div class="muted small">No backups yet. The first save will create one.</div>`}
        <div class="row" style="margin-top:12px"><span class="muted small mono">${esc(backupRoot)}</span></div>
      </section>
    </div>`;

  hydrateIcons(view);
  bindGo(view);
  $$('[data-action]', view).forEach((button) => {
    button.onclick = () => {
      const action = button.dataset.action;
      if (action === 'start' || action === 'stop' || action === 'restart') serverAction(action);
    };
  });
  $$('[data-restore]', view).forEach((button) => {
    button.onclick = () => restoreBackup(button.dataset.restore);
  });
}

/* -------------------------------------------------------------- server control */
async function renderServer() {
  const view = $('#view');
  const status = await getStatus(true);
  const profiles = await getJson('/api/admin/profiles').catch(() => ({ profiles: [], active: '' }));
  const server = status.server || {};
  const profile = status.profile || {};
  const database = status.database || {};
  const validation = status.validation || { errors: [], warnings: [] };
  let logs = { logs: [] };
  try { logs = await getJson('/api/admin/logs'); } catch (error) { /* optional */ }
  const logName = (logs.logs || []).find((row) => row.name === 'server-platform.log')
    ? 'server-platform.log' : ((logs.logs || [])[0] || {}).name;
  let logText = '';
  if (logName) {
    try { logText = (await getJson('/api/admin/log?name=' + encodeURIComponent(logName) + '&tail=120')).text || ''; } catch (error) { /* optional */ }
  }

  view.innerHTML = `
    ${pageHead('Server control', 'Run the game server from the release profile and watch its process health. The platform admin stays on its own port.',
      `${server.running
        ? `<button class="button danger-filled" data-action="stop">${svgIcon('stop')} Stop server</button>
           <button class="button tonal" data-action="restart">${svgIcon('restart')} Restart</button>`
        : `<button class="button" data-action="start">${svgIcon('play')} Start server</button>`}
       <button class="button text" id="refresh-server">${svgIcon('refresh')} Refresh</button>
       <button class="button text" data-go="logs">${svgIcon('terminal')} Logs</button>`)}

    <div class="grid four">
      ${metricCard('Process', server.running ? 'Online' : 'Stopped', server.message || '', 'power', server.running ? 'primary' : 'error')}
      ${metricCard('Port', String(server.port ?? '—'), server.host || 'No host', 'server')}
      ${metricCard('PID', String(server.pid ?? '—'), server.managed ? 'started by this manager' : 'external or stopped', 'hash')}
      ${metricCard('Database', database.exists ? fmtBytes(database.bytes) : 'missing', database.path || '—', 'database')}
    </div>

    <div id="server-live-note" class="muted tiny" style="margin-top:8px">Live status updates every 10 seconds.</div>

    <div class="grid two" style="margin-top:16px">
      <section class="card">
        <div class="card-header"><div class="grow"><h3>Process controls</h3><p>Starts <span class="inline-code">python -m dittobuffer.runtime.http</span> with PYTHONPATH pointing at the release source.</p></div></div>
        <div class="notice info">${svgIcon('info')}<div class="notice-body">
          <strong>The admin stays separate</strong>
          The game server uses the profile port (${esc(server.port ?? '—')}). The platform admin keeps its own listener. Starting the server here follows the active profile exactly.
        </div></div>
        <div class="row" style="margin-top:14px">
          ${server.running
            ? `<button class="button danger-filled" data-action="stop">${svgIcon('stop')} Stop server</button>
               <button class="button tonal" data-action="restart">${svgIcon('restart')} Restart</button>`
            : `<button class="button" data-action="start">${svgIcon('play')} Start server</button>`}
          <button class="button text" id="open-health" ${server.health_url ? '' : 'disabled'}>${svgIcon('external')} Open health</button>
        </div>
        <div class="divider"></div>
        <div class="kv">
          <span class="k">URL</span><span class="v mono">${esc(server.url || '—')}</span>
          <span class="k">Health</span><span class="v mono">${esc(server.health_url || '—')}</span>
          <span class="k">Game Master</span><span class="v">${status.game_master && status.game_master.gmts_present ? 'GMTS present' : 'missing'}</span>
        </div>
      </section>

      <section class="card">
        <div class="card-header"><div class="grow"><h3>Release profile</h3><p>Switching here writes the platform's <span class="inline-code">server_profile</span> and applies immediately.</p></div></div>
        <div class="field">
          <label for="server-profile">Active profile</label>
          <select class="select" id="server-profile">
            ${(profiles.profiles || []).map((row) => `<option value="${esc(row.name)}" ${row.name === profiles.active ? 'selected' : ''}>${esc(row.name)} — ${esc(row.host || '?')}:${esc(row.port ?? '?')} (${esc(row.network || '?')})</option>`).join('')}
          </select>
          <span class="field-help">Profiles live directly in <span class="inline-code">server/config/</span>. Choose carefully: the server reads the selected file on its next start.</span>
        </div>
        <div class="row" style="margin-top:14px">
          <span class="chip ${validation.errors.length ? 'error' : 'success'}">${validation.errors.length} errors</span>
          <span class="chip ${validation.warnings.length ? 'warn' : 'outline'}">${validation.warnings.length} warnings</span>
          <span class="spacer"></span>
          <button class="button tonal" data-go="config">${svgIcon('tune')} Edit this profile</button>
        </div>
        <div style="margin-top:14px">${validationNotice(validation, true)}</div>
      </section>
    </div>

    <section class="card" style="margin-top:16px">
      <div class="card-header"><div class="grow"><h3>Recent server output</h3><p>${logName ? esc(logName) : 'No log file yet — start the server to create logs/server-platform.log'} · ${esc(profile.name || '')}</p></div>
        ${logName ? `<button class="button text small" data-go="logs">${svgIcon('external')} Open logs</button>` : ''}</div>
      ${logText ? `<pre class="code" style="max-height:300px">${esc(logText)}</pre>` : `<div class="muted small">No output yet.</div>`}
    </section>`;

  hydrateIcons(view);
  bindGo(view);
  $$('[data-action]', view).forEach((button) => {
    button.onclick = () => serverAction(button.dataset.action);
  });
  const health = $('#open-health', view);
  if (health) health.onclick = () => { if (server.health_url) window.open(server.health_url, '_blank', 'noopener'); };
  const refresh = $('#refresh-server', view);
  if (refresh) refresh.onclick = () => renderRoute();
  const select = $('#server-profile', view);
  if (select) {
    select.onchange = async () => {
      try {
        await postJson('/api/admin/platform', { server_profile: 'config/' + select.value });
        state.configDraft = null;
        state.configName = select.value;
        snack('Active profile switched to ' + select.value);
        await getStatus(true);
        renderRoute();
      } catch (error) { snack(error.message, true); }
    };
  }
  state.pageTimer = setInterval(async () => {
    try {
      const fresh = await getStatus(true);
      applyStatus(fresh);
      const note = $('#server-live-note');
      if (note) note.textContent = 'Live status · checked ' + new Date().toLocaleTimeString();
    } catch (error) { /* keep the page usable */ }
  }, 10000);
}

/* -------------------------------------------------------------- config studio */
function updateConfigDirty() {
  const chip = $('#config-dirty');
  if (chip) {
    chip.textContent = state.configDirty ? 'Unsaved changes' : 'Saved';
    chip.className = 'chip ' + (state.configDirty ? 'warn' : 'success');
  }
  const save = $('#config-save');
  if (save) save.disabled = !state.configDirty;
}

function renderConfigEditor() {
  const host = $('#config-editor');
  if (!host || !state.configDraft) return;
  host.replaceChildren();
  host.appendChild(createJsonEditor(state.configDraft, {
    search: state.configSearch,
    rootLabel: 'server config',
    onChange: () => { state.configDirty = true; updateConfigDirty(); },
  }));
}

async function renderConfigPage() {
  const view = $('#view');
  const status = await getStatus();
  if (!status.available) {
    view.innerHTML = pageHead('Config Studio', 'Server editing is an optional extra.') + emptyState(
      'Standalone platform', 'Rooms, HiveMQ and OSM POIs work without a game server, Game Master or assets.',
      'cloud', `<button class="button" data-go="platform">Open platform</button>`);
    bindGo(view);
    return;
  }
  const profiles = await getJson('/api/admin/profiles');
  let name = state.configName;
  if (!name || !(profiles.profiles || []).some((row) => row.name === name)) {
    name = profiles.active || ((profiles.profiles || [])[0] || {}).name || '';
  }
  if (!name) {
    view.innerHTML = pageHead('Config Studio', 'No JSON profile exists in server/config.') + emptyState(
      'No profiles found', 'Restore a release config or create one with the platform files.', 'tune');
    return;
  }
  const profile = await getJson('/api/admin/profile?name=' + encodeURIComponent(name));
  if (!state.configDraft || state.configName !== name) {
    state.configDraft = clone(profile.data);
    state.configDirty = false;
    state.configSearch = '';
  }
  state.configName = name;
  state.configValidation = profile.validation;

  view.innerHTML = `
    ${pageHead('Config Studio', 'A recursive editor for <strong>every key</strong> in the active server profile. Objects, arrays, booleans, numbers, strings and null are editable; raw JSON is also available.',
      `<button class="button tonal" id="config-new">${svgIcon('plus')} New profile</button>
       <button class="button text" id="config-raw">${svgIcon('code')} Raw JSON</button>
       <button class="button" id="config-save" ${state.configDirty ? '' : 'disabled'}>${svgIcon('save')} Save profile</button>`)}

    <div class="card">
      <div class="toolbar">
        <div class="field" style="min-width:230px">
          <label for="config-profile">Profile</label>
          <select class="select" id="config-profile">
            ${(profiles.profiles || []).map((row) => `<option value="${esc(row.name)}" ${row.name === name ? 'selected' : ''}>${esc(row.name)} · ${esc(row.host || '?')}:${esc(row.port ?? '?')}</option>`).join('')}
          </select>
        </div>
        <div class="search-field" style="max-width:340px">
          ${svgIcon('search')}<input class="input" id="config-search" placeholder="Filter every key and value" value="${esc(state.configSearch)}">
        </div>
        <span class="spacer"></span>
        <span class="chip outline">${esc(profile.path)}</span>
        <span class="chip outline">${fmtBytes(profile.bytes || JSON.stringify(profile.data).length)}</span>
        <span class="chip" id="config-dirty">${state.configDirty ? 'Unsaved changes' : 'Saved'}</span>
      </div>
      <div class="divider"></div>
      <div class="row">
        <button class="button text small" data-expand="1">Expand all</button>
        <button class="button text small" data-expand="0">Collapse all</button>
        <span class="spacer"></span>
        <button class="button text small" id="config-reload">${svgIcon('undo')} Reload from disk</button>
        <button class="button text small danger" id="config-delete">${svgIcon('trash')} Delete profile</button>
      </div>
      <div id="config-validation" style="margin-top:12px">${validationNotice(profile.validation)}</div>
      <div style="margin-top:16px" id="config-editor"></div>
    </div>

    <div class="notice info" style="margin-top:16px">${svgIcon('info')}<div class="notice-body">
      <strong>The whole profile is one save</strong>
      Changes stay in this browser until you press Save profile. Every save validates the result and writes a timestamped backup under the platform data folder.
    </div></div>`;

  hydrateIcons(view);
  renderConfigEditor();
  bindGo(view);
  updateConfigDirty();

  const search = $('#config-search', view);
  let searchTimer = null;
  search.oninput = () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      state.configSearch = search.value;
      renderConfigEditor();
      const again = $('#config-search', view);
      if (again) { again.focus(); again.setSelectionRange(again.value.length, again.value.length); }
    }, 160);
  };
  $$('[data-expand]', view).forEach((button) => {
    button.onclick = () => $$('.json-object', view).forEach((detail) => {
      detail.open = button.dataset.expand === '1';
    });
  });
  $('#config-save', view).onclick = async () => {
    try {
      const result = await postJson('/api/admin/profile', { name: state.configName, data: state.configDraft });
      state.configDraft = clone(result.data);
      state.configDirty = false;
      state.configValidation = result.validation;
      $('#config-validation', view).innerHTML = validationNotice(result.validation);
      updateConfigDirty();
      renderConfigEditor();
      snack('Saved ' + result.name + ' (backup created)');
      await getStatus(true);
    } catch (error) { snack(error.message, true); }
  };
  $('#config-profile', view).onchange = async (event) => {
    const next = event.target.value;
    if (next === state.configName) return;
    if (!await guardLeave('Switch profiles and lose edits that have not been saved?')) {
      event.target.value = state.configName;
      return;
    }
    state.configName = next;
    state.configDraft = null;
    state.configDirty = false;
    state.configSearch = '';
    renderRoute();
  };
  $('#config-reload', view).onclick = async () => {
    if (state.configDirty && !await confirmDialog('Discard changes?', 'Reload the profile from disk and lose unsaved edits?', 'Reload', true)) return;
    state.configDraft = null;
    state.configDirty = false;
    renderRoute();
  };
  $('#config-new', view).onclick = async () => {
    const chosen = await promptDialog({
      title: 'New server profile', label: 'Profile file name', confirm: 'Create',
      placeholder: 'server', help: 'Created inside server/config/. The current profile is used as the starting point so the result boots immediately.',
    });
    if (!chosen) return;
    const safe = chosen.endsWith('.json') ? chosen : chosen + '.json';
    if (!/^[A-Za-z0-9_.-]+\.json$/.test(safe)) { snack('Use letters, digits, dashes, underscores and .json', true); return; }
    try {
      await postJson('/api/admin/profile', { name: safe, data: clone(state.configDraft) });
      state.configName = safe;
      state.configDraft = null;
      state.configDirty = false;
      snack('Created ' + safe);
      renderRoute();
    } catch (error) { snack(error.message, true); }
  };
  $('#config-delete', view).onclick = async () => {
    if (!await confirmDialog('Delete profile?', 'Delete ' + state.configName + '? A backup is kept, and at least one profile must remain.', 'Delete', true)) return;
    try {
      await postJson('/api/admin/profile/delete', { name: state.configName });
      state.configName = '';
      state.configDraft = null;
      state.configDirty = false;
      snack('Profile deleted');
      renderRoute();
    } catch (error) { snack(error.message, true); }
  };
  $('#config-raw', view).onclick = () => openRawJsonDialog({
    title: 'Raw profile JSON',
    value: state.configDraft,
    onApply: (parsed) => {
      state.configDraft = parsed;
      state.configDirty = true;
      updateConfigDirty();
      renderConfigEditor();
      snack('Raw JSON applied in the browser; press Save profile to write it');
    },
  });
}

/* --------------------------------------------------------- curated topic pages */
const TOPIC_SPECS = {
  spawns: {
    title: 'Spawns & catching',
    subtitle: 'Every spawn table, area, shiny rule and catch reward in the active profile.',
    keys: ['spawn_species', 'spawn_pools', 'spawn_points_per_tile', 'spawn_level_range',
      'spawn_iv_range', 'spawn_generation', 'shiny_rate', 'shiny_eligible_forms', 'random_spawns',
      'catch_probability', 'catch_xp', 'catch_stardust', 'catch_candy', 'spawn_duration_seconds',
      'encounter_duration_seconds', 'interaction_range_meters', 'spawn_neighbor_tiles'],
    terms: ['spawn', 'catch', 'shiny'],
    sections: ['Configuration', 'Game data'],
    tips: ['Use Config Studio for keys not shown here.', 'Wild spawn definitions can also refer to data/static and world manifests.'],
  },
  raids: {
    title: 'Raids',
    subtitle: 'Raid tiers, bosses, timers, rewards and every related JSON document.',
    keys: ['raids'], terms: ['raid'], sections: ['Configuration', 'Game data'],
    tips: ['Raid bosses reference the Game Master species and moves.', 'Raid reward tables live beside the profile as JSON documents.'],
  },
  liveops: {
    title: 'LiveOps',
    subtitle: 'Events, season windows, global multipliers and the live operations switches.',
    keys: ['liveops'], terms: ['liveops', 'event', 'season'], sections: ['Configuration', 'Game data'],
    tips: ['Changes apply on server restart.', 'Player-visible event copy lives in the same profile block.'],
  },
  research: {
    title: 'Research',
    subtitle: 'Research tasks, rewards, levels and every research data file.',
    keys: ['research'], terms: ['research'], sections: ['Configuration', 'Game data'],
    tips: ['Task definitions may live in data/static JSON files.', 'Research progress is stored per player in the active database.'],
  },
  rocket: {
    title: 'Rocket',
    subtitle: 'Rocket leaders, line-ups, shadow and purified rules, plus every rocket document.',
    keys: ['rocket'], terms: ['rocket'], sections: ['Configuration', 'Game data'],
    notice: 'Rocket behaviour is often spread across config/rocket*.json files. Use the related files below and Config Studio for the complete picture.',
    tips: ['Rocket line-ups and leaders are separate JSON documents.', 'The profile block is created on first save if it does not exist.'],
  },
  pvp: {
    title: 'PvP',
    subtitle: 'Battle switches, leagues, rating rules and the PvP reference data.',
    keys: ['pvp'], terms: ['pvp', 'battle'], sections: ['Configuration', 'Game data'],
    tips: ['PvP reference data is shown on the Game Master page.', 'Battle balance lives in the reference JSON and Game Master binaries.'],
  },
  max: {
    title: 'Max system',
    subtitle: 'Dynamax, Gigantamax, Max Battles, energy and the Max reference data.',
    keys: ['max_system'], terms: ['max'], sections: ['Configuration', 'Game data'],
    tips: ['Max move data is generated from the reference JSON.', 'Power Spot indexes live in data/world/power-spots.sqlite3.'],
  },
  world: {
    title: 'World & weather',
    subtitle: 'World source, map tiles, weather, POI settings and every world document.',
    keys: ['world', 'weather'], terms: ['world', 'weather', 'map'], sections: ['Configuration', 'Game data'],
    tips: ['The release uses a loopback listener and local map tiles.', 'World JSON and power spot indexes are separate editable files.'],
  },
};

function relatedDocuments(docs, spec) {
  const terms = spec.terms || [];
  return (docs || []).filter((row) => {
    if (!['Configuration', 'Game data'].includes(row.category)) return false;
    if (spec.sections.includes(row.section)) return true;
    const path = row.path.toLowerCase();
    return terms.some((term) => path.includes(term));
  }).slice(0, 24);
}

function renderTopicEditor() {
  const host = $('#topic-editor');
  if (!host || !state.topic) return;
  host.replaceChildren();
  host.appendChild(createJsonEditor(state.topic.draft, {
    onlyKeys: TOPIC_SPECS[state.topic.key].keys,
    search: state.topic.search,
    rootLabel: state.topic.name,
    onChange: () => {
      state.topic.dirty = true;
      const chip = $('#topic-dirty');
      if (chip) { chip.textContent = 'Unsaved changes'; chip.className = 'chip warn'; }
    },
  }));
}

async function renderTopic(key) {
  const spec = TOPIC_SPECS[key];
  const view = $('#view');
  const status = await getStatus();
  if (!status.available) {
    view.innerHTML = pageHead(spec.title, spec.subtitle) + emptyState('Optional server tools',
      'This is a standalone Dittobuffer platform. Server editing is not required for rooms, HiveMQ or OSM POIs.', 'cloud', '<button class="button" data-go="platform">Open platform</button>');
    bindGo(view);
    return;
  }
  const profile = await getJson('/api/admin/profile');
  let docs = { documents: [] };
  try { docs = await getDocuments(); } catch (error) { /* optional */ }
  if (!state.topic || state.topic.key !== key || state.topic.name !== profile.name) {
    state.topic = { key, name: profile.name, draft: clone(profile.data), dirty: false, search: '' };
  }
  const related = relatedDocuments(docs.documents || [], spec);

  view.innerHTML = `
    ${pageHead(spec.title, spec.subtitle + ` Editing <span class="inline-code">${esc(profile.name)}</span>.`,
      `<button class="button text" id="topic-raw">${svgIcon('code')} Raw JSON</button>
       <button class="button text" id="topic-full">${svgIcon('tune')} Full Config Studio</button>
       <button class="button" id="topic-save">${svgIcon('save')} Save ${esc(profile.name)}</button>`)}
    ${spec.notice ? `<div class="notice info" style="margin-bottom:14px">${svgIcon('info')}<div class="notice-body"><strong>Curated scope</strong>${esc(spec.notice)}</div></div>` : ''}
    <div id="topic-validation">${validationNotice(profile.validation)}</div>
    <div class="card" style="margin-top:14px">
      <div class="card-header"><div class="grow"><h3>${esc(spec.title)} configuration</h3>
        <p>Only the keys this feature owns are shown. Add a missing key with the button in the object header.</p></div>
        <span class="chip" id="topic-dirty">${state.topic.dirty ? 'Unsaved changes' : 'Saved'}</span></div>
      <div class="toolbar">
        <div class="search-field" style="max-width:340px">${svgIcon('search')}
          <input class="input" id="topic-search" placeholder="Filter this section" value="${esc(state.topic.search)}"></div>
        <span class="spacer"></span>
        <button class="button text small" data-expand="1">Expand all</button>
        <button class="button text small" data-expand="0">Collapse all</button>
      </div>
      <div class="divider"></div>
      <div id="topic-editor"></div>
    </div>
    <div class="grid two" style="margin-top:16px">
      <section class="card">
        <div class="card-header"><div class="grow"><h3>Related files</h3><p>Documents used by this system, editable in the File studio.</p></div>
          <span class="chip outline">${related.length}</span></div>
        ${related.length ? `<div class="file-list">${related.map((row) => `
          <button class="file-row" data-path="${esc(row.path)}">
            <span class="file-icon" data-icon="${row.json ? 'code' : 'file'}"></span>
            <span class="name">${esc(row.path)}</span>
            <span class="meta">${fmtBytes(row.bytes)}</span>
            ${svgIcon('arrow')}
          </button>`).join('')}</div>`
          : '<div class="muted small">No directly related files in configuration or game data.</div>'}
      </section>
      <section class="card">
        <div class="card-header"><div class="grow"><h3>Editing notes</h3><p>What this page does and where the rest lives.</p></div></div>
        <ul class="warn-list">${(spec.tips || []).map((tip) => `<li>${esc(tip)}</li>`).join('')}</ul>
        <div class="divider"></div>
        <div class="notice warn">${svgIcon('warning')}<div class="notice-body">
          <strong>Validate before restarting</strong>
          Save validates the whole profile. If you add a brand new block, check its shape against the server's reference JSON first.
        </div></div>
      </section>
    </div>`;

  hydrateIcons(view);
  bindGo(view);
  renderTopicEditor();
  $$('[data-expand]', view).forEach((button) => {
    button.onclick = () => $$('.json-object', view).forEach((detail) => {
      detail.open = button.dataset.expand === '1';
    });
  });
  $$('[data-path]', view).forEach((button) => {
    button.onclick = () => openFile(button.dataset.path);
  });
  const search = $('#topic-search', view);
  let timer = null;
  search.oninput = () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      state.topic.search = search.value;
      renderTopicEditor();
      const again = $('#topic-search');
      if (again) { again.focus(); again.setSelectionRange(again.value.length, again.value.length); }
    }, 160);
  };
  const save = async () => {
    try {
      const result = await postJson('/api/admin/profile', { name: state.topic.name, data: state.topic.draft });
      state.topic.draft = clone(result.data);
      state.topic.dirty = false;
      $('#topic-validation', view).innerHTML = validationNotice(result.validation);
      const chip = $('#topic-dirty'); if (chip) { chip.textContent = 'Saved'; chip.className = 'chip success'; }
      snack('Saved ' + result.name + ' (backup created)');
      await getStatus(true);
    } catch (error) { snack(error.message, true); }
  };
  $('#topic-save', view).onclick = save;
  $('#topic-raw', view).onclick = () => openRawJsonDialog({
    title: spec.title + ' — raw profile JSON', value: state.topic.draft,
    onApply: (parsed) => {
      state.topic.draft = parsed;
      state.topic.dirty = true;
      const chip = $('#topic-dirty'); if (chip) { chip.textContent = 'Unsaved changes'; chip.className = 'chip warn'; }
      renderTopicEditor();
      snack('Raw JSON applied in the browser; press Save to write it');
    },
  });
  $('#topic-full', view).onclick = async () => {
    const profileName = state.topic.name;
    if (!await guardLeave('Open Config Studio and lose edits that have not been saved?')) return;
    state.configName = profileName;
    state.configSearch = '';
    navigate('config');
  };
}

Object.assign(window.PAGES, {
  overview: renderOverview,
  server: renderServer,
  config: renderConfigPage,
  spawns: () => renderTopic('spawns'),
  raids: () => renderTopic('raids'),
  liveops: () => renderTopic('liveops'),
  research: () => renderTopic('research'),
  rocket: () => renderTopic('rocket'),
  pvp: () => renderTopic('pvp'),
  max: () => renderTopic('max'),
  world: () => renderTopic('world'),
});
