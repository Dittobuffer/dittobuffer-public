'use strict';

/* File studio, database studio, players, Game Master, loot, platform rooms and logs. */

/* ------------------------------------------------------------------ file studio */
async function loadFileDocument(path) {
  const doc = await api('/api/admin/document?path=' + encodeURIComponent(path));
  state.fileDoc = doc;
  state.fileDraft = doc.json && isObject(doc.value) ? clone(doc.value) : null;
  state.fileText = doc.text || '';
  state.fileTab = doc.json && isObject(doc.value) ? 'form' : 'source';
  state.fileDirty = false;
}

function renderFileList() {
  const host = $('#file-list');
  if (!host) return;
  const rows = (state.docs && state.docs.documents) || [];
  const needle = (state.fileQuery || '').toLowerCase();
  const category = state.fileCategory || '';
  const filtered = rows.filter((row) => {
    if (category && row.category !== category) return false;
    return !needle || row.path.toLowerCase().includes(needle);
  });
  host.innerHTML = filtered.length ? filtered.map((row) => `
    <button class="file-row ${row.path === state.filePath ? 'active' : ''}" data-file="${esc(row.path)}">
      <span class="file-icon" data-icon="${row.json ? 'code' : 'file'}"></span>
      <span class="name">${esc(row.path)}</span>
      <span class="meta">${fmtBytes(row.bytes)}</span>
    </button>`).join('')
    : `<div class="muted small" style="padding:12px">No files match.</div>`;
  hydrateIcons(host);
  $$('[data-file]', host).forEach((button) => {
    button.onclick = () => selectFile(button.dataset.file);
  });
}

async function selectFile(path) {
  if (path === state.filePath) return;
  if (state.fileDirty && !await confirmDialog('Discard changes?', 'Open another file and lose unsaved edits?', 'Discard', true)) return;
  state.filePath = path;
  state.fileDoc = null;
  state.fileDraft = null;
  state.fileDirty = false;
  try { await loadFileDocument(path); } catch (error) { snack(error.message, true); }
  renderRoute();
}

function renderFileEditor() {
  const host = $('#file-editor');
  if (!host) return;
  if (!state.filePath) {
    host.innerHTML = emptyState('Choose a file', 'Every text file inside the release server folder is listed on the left.', 'folder');
    return;
  }
  if (!state.fileDoc) {
    host.innerHTML = emptyState('File unavailable', 'The file could not be opened. It may be binary or larger than 4 MB.', 'warning');
    return;
  }
  const doc = state.fileDoc;
  host.innerHTML = `
    <div class="editor-head">
      <div class="grow">
        <div class="row"><h3 class="mono" style="font-size:14px">${esc(doc.path)}</h3>
          <button class="button text small icon-only" data-copy="${esc(doc.path)}" title="Copy path">${svgIcon('copy')}</button></div>
        <div class="row" style="margin-top:5px">
          <span class="chip outline">${esc(doc.category)}</span>
          <span class="chip outline">${fmtBytes(doc.bytes)}</span>
          <span class="chip outline">${esc(doc.suffix || '')}</span>
          <span class="muted tiny">modified ${esc(ago(doc.modified_ms))}</span>
          <span class="chip ${state.fileDirty ? 'warn' : 'success'}">${state.fileDirty ? 'Unsaved changes' : 'Saved'}</span>
        </div>
      </div>
      <div class="row">
        <button class="button text small" id="file-reload">${svgIcon('undo')} Reload</button>
        <button class="button" id="file-save">${svgIcon('save')} Save file</button>
      </div>
    </div>
    ${doc.json_error ? `<div class="notice error">${svgIcon('warning')}<div class="notice-body"><strong>Invalid JSON on disk</strong>${esc(doc.json_error)}<br>Fix it in Source, or replace it with fresh JSON.</div></div>` : ''}
    ${doc.json ? `<div class="tabs">
      <button class="tab ${state.fileTab === 'form' ? 'active' : ''}" data-file-tab="form">${svgIcon('tune')} Structured</button>
      <button class="tab ${state.fileTab === 'source' ? 'active' : ''}" data-file-tab="source">${svgIcon('code')} Source</button>
    </div>` : ''}
    <div id="file-editor-body"></div>`;
  hydrateIcons(host);
  bindCopy(host);

  const body = $('#file-editor-body', host);
  if (doc.json && state.fileTab === 'form') {
    if (isObject(doc.value) && state.fileDraft) {
      body.appendChild(createJsonEditor(state.fileDraft, {
        rootLabel: doc.name,
        onChange: () => { state.fileDirty = true; updateFileStatus(); },
      }));
      if (Array.isArray(doc.value)) {
        body.innerHTML = `<div class="notice warn">${svgIcon('warning')}<div class="notice-body"><strong>Top-level array</strong>Edit this document in Source. The structured editor works with JSON objects.</div></div>`;
      }
    } else {
      body.innerHTML = `<div class="notice warn">${svgIcon('warning')}<div class="notice-body"><strong>Source required</strong>This JSON value is not an editable object.</div></div>`;
    }
  } else {
    const area = document.createElement('textarea');
    area.className = 'code-editor';
    area.id = 'file-source';
    area.spellcheck = false;
    area.value = state.fileText || '';
    area.oninput = () => { state.fileText = area.value; state.fileDirty = true; updateFileStatus(); };
    body.appendChild(area);
  }

  $$('[data-file-tab]', host).forEach((button) => {
    button.onclick = () => {
      const source = $('#file-source', host);
      if (source) state.fileText = source.value;
      state.fileTab = button.dataset.fileTab;
      renderFileEditor();
    };
  });
  const reload = $('#file-reload', host);
  if (reload) reload.onclick = async () => {
    if (state.fileDirty && !await confirmDialog('Discard changes?', 'Reload this file from disk?', 'Reload', true)) return;
    try { await loadFileDocument(state.filePath); snack('Reloaded ' + state.filePath); renderRoute(); } catch (error) { snack(error.message, true); }
  };
  const save = $('#file-save', host);
  if (save) save.onclick = async () => {
    try {
      let result;
      if (doc.json && state.fileTab === 'form' && isObject(state.fileDraft)) {
        result = await postJson('/api/admin/document', { path: doc.path, value: state.fileDraft });
      } else {
        const source = $('#file-source', host);
        if (source) state.fileText = source.value;
        result = await postJson('/api/admin/document', { path: doc.path, text: state.fileText });
      }
      state.fileDoc = result;
      state.fileDraft = result.json && isObject(result.value) ? clone(result.value) : null;
      state.fileText = result.text || '';
      state.fileDirty = false;
      snack('Saved ' + result.path + ' (backup created)');
      await getDocuments(true);
      renderRoute();
    } catch (error) { snack(error.message, true); }
  };
}

function updateFileStatus() {
  if (state.route !== 'files') return;
  const chip = $('.editor-head .chip.warn, .editor-head .chip.success');
  if (chip) { chip.textContent = state.fileDirty ? 'Unsaved changes' : 'Saved'; chip.className = 'chip ' + (state.fileDirty ? 'warn' : 'success'); }
}

async function renderFilesPage() {
  const view = $('#view');
  const data = await getDocuments(true);
  state.docs = data;
  if (!state.filePath) {
    const preferred = (data.documents || []).find((row) => row.path === 'config/server.json')
      || (data.documents || []).find((row) => row.path.endsWith('.json'));
    if (preferred) state.filePath = preferred.path;
  }
  if (state.filePath && (!state.fileDoc || state.fileDoc.path !== state.filePath)) {
    try { await loadFileDocument(state.filePath); } catch (error) { snack(error.message, true); state.fileDoc = null; }
  }
  view.innerHTML = `
    ${pageHead('File studio', 'Browse and edit every UTF-8 text file in the release server workspace. Saves validate JSON and keep a timestamped backup. All files: <strong>${(data.documents || []).length}</strong>.',
      `<button class="button text" id="file-refresh">${svgIcon('refresh')} Refresh index</button>`)}
    <div class="split">
      <aside class="pane">
        <div class="pane-head">
          <div class="search-field" style="width:100%">${svgIcon('search')}
            <input class="input small" id="file-search" placeholder="Filter files" value="${esc(state.fileQuery || '')}"></div>
        </div>
        <div style="padding:10px 12px 0">
          <select class="select input small" id="file-category">
            <option value="">All categories (${(data.documents || []).length})</option>
            ${(data.categories || []).map((name) => `<option value="${esc(name)}" ${name === state.fileCategory ? 'selected' : ''}>${esc(name)}</option>`).join('')}
          </select>
        </div>
        <div class="pane-body"><div class="file-list" id="file-list"></div></div>
      </aside>
      <section class="card" id="file-editor"></section>
    </div>`;
  hydrateIcons(view);
  renderFileList();
  renderFileEditor();
  $('#file-search', view).oninput = (event) => {
    state.fileQuery = event.target.value;
    renderFileList();
  };
  $('#file-category', view).onchange = (event) => {
    state.fileCategory = event.target.value;
    renderFileList();
  };
  $('#file-refresh', view).onclick = async () => {
    const fresh = await getDocuments(true);
    state.docs = fresh;
    renderRoute();
    snack('File index refreshed');
  };
}

/* -------------------------------------------------------------- database studio */
function cellHtml(value) {
  if (value === null || value === undefined) return '<span class="null">NULL</span>';
  if (typeof value === 'object') {
    if (value.$base64) return `<span class="blob">blob · ${fmtBytes(value.bytes || 0)}</span>`;
    return esc(JSON.stringify(value));
  }
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  const text = String(value);
  return esc(text.length > 140 ? text.slice(0, 140) + '…' : text);
}

function editText(value) {
  if (value === null || value === undefined) return 'null';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function parseDbCell(text, columnType = '') {
  const trimmed = String(text).trim();
  if (trimmed === 'null') return null;
  if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
    try { return JSON.parse(trimmed); } catch (error) { return text; }
  }
  if (/INT|REAL|NUM|DEC|DOUBLE|FLOAT|SERIAL/i.test(columnType || '') && trimmed !== '' && Number.isFinite(Number(trimmed))) {
    return Number(trimmed);
  }
  return text;
}

function dbKeyForRow(row) {
  const data = state.dbRows;
  if (!data) return null;
  if (data.key && data.key.mode === 'rowid') return { __rowid: row.__rowid };
  const primary = (state.dbSchema && state.dbSchema.primary_key) || [];
  if (!primary.length) return null;
  const key = {};
  primary.forEach((column) => { key[column] = row[column]; });
  return key;
}

function renderDbList() {
  const host = $('#db-table-list');
  if (!host) return;
  const tables = (state.dbTables && state.dbTables.tables) || [];
  const needle = (state.dbTableQuery || '').toLowerCase();
  const filtered = tables.filter((table) => !needle || table.name.toLowerCase().includes(needle));
  host.innerHTML = `
    <div class="section-label" style="padding:0 3px 8px">${tables.length} tables &amp; views</div>
    ${filtered.map((table) => `<button class="table-row ${table.name === state.dbTable ? 'active' : ''}" data-db-table="${esc(table.name)}">
      ${svgIcon(table.type === 'view' ? 'eye' : 'table')}
      <span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(table.name)}</span>
      <span class="meta">${table.rows === null ? '—' : table.rows}</span>
    </button>`).join('') || '<div class="muted small" style="padding:10px">No tables match.</div>'}`;
  $$('[data-db-table]', host).forEach((button) => {
    button.onclick = async () => {
      state.dbTable = button.dataset.dbTable;
      state.dbOffset = 0;
      state.dbQuery = '';
      state.dbTab = 'rows';
      await refreshDbTable();
      renderDbList();
    };
  });
}

function renderDbSchema() {
  const schema = state.dbSchema;
  if (!schema) return '<div class="muted small">No schema loaded.</div>';
  return `
    <div class="schema-list">        ${(schema.columns || []).map((column) => `<div class="schema-row">
        <span class="mono">${esc(column.name)}</span>
        <span class="type">${esc(column.type || 'ANY')}</span>
        <span class="flags">${column.pk ? 'PK · ' : ''}${column.notnull ? 'NOT NULL' : ''}${column.dflt_value !== null ? ' · default ' + esc(column.dflt_value) : ''}</span>
      </div>`).join('')}
    </div>
    <details class="panel" style="margin-top:12px"><summary>${svgIcon('code')} <span>CREATE statement</span></summary>
      <pre class="code">${esc(schema.sql || '')}</pre></details>
    <div class="grid two" style="margin-top:12px">
      <div><div class="section-label">Indexes</div><div class="mono small" style="margin-top:6px">
        ${(schema.indexes || []).map((index) => `${esc(index.name)} ${index.unique ? '(unique)' : ''} → ${esc((index.columns || []).join(', '))}`).join('<br>') || 'None'}</div></div>
      <div><div class="section-label">Foreign keys</div><div class="mono small" style="margin-top:6px">
        ${(schema.foreign_keys || []).map((key) => `${esc(key.from)} → ${esc(key.table)}.${esc(key.to || '?')} (${esc(key.on_delete || 'NO ACTION')})`).join('<br>') || 'None'}</div></div>
    </div>`;
}

function renderSqlResult() {
  const result = state.dbSqlResult;
  if (!result) return '<div class="muted small">Run a statement to see its result.</div>';
  if (result.error) return `<div class="notice error">${svgIcon('warning')}<div class="notice-body">${esc(result.error)}</div></div>`;
  if (!result.columns || !result.columns.length) {
    return `<div class="notice success">${svgIcon('check')}<div class="notice-body">
      <strong>Statement complete</strong>${result.rowcount ?? 0} rows affected · ${result.elapsed_ms ?? 0} ms${result.write ? ' · backup created' : ' · read-only'}</div></div>`;
  }
  return `<div class="row between" style="margin-bottom:8px"><span class="muted small">${result.rows.length} rows${result.truncated ? ' (truncated)' : ''} · ${result.elapsed_ms ?? 0} ms</span></div>
    <div class="table-wrap"><table class="data-table"><thead><tr>${result.columns.map((column) => `<th>${esc(column)}</th>`).join('')}</tr></thead>
      <tbody>${result.rows.map((row) => `<tr>${result.columns.map((column) => `<td>${cellHtml(row[column])}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}

function renderDbMain() {
  const host = $('#db-main');
  if (!host) return;
  if (!state.dbPath) {
    host.innerHTML = `<section class="card">${emptyState('Choose a database', 'No SQLite database was found in the server workspace.', 'database')}</section>`;
    return;
  }
  const tables = state.dbTables || { tables: [] };
  const tableInfo = (tables.tables || []).find((row) => row.name === state.dbTable);
  if (!state.dbTable) {
    host.innerHTML = `<section class="card">${emptyState('Choose a table', 'Pick a table or view on the left to browse, edit and query its rows.', 'table')}</section>`;
    return;
  }
  let content = '';
  if (state.dbTab === 'schema') content = renderDbSchema();
  else if (state.dbTab === 'sql') {
    content = `
      <div class="sql-grid">
        <div class="field"><label for="db-sql">SQL statement</label>
          <textarea class="code-editor" id="db-sql" style="min-height:170px;max-height:300px" spellcheck="false" placeholder="SELECT * FROM players LIMIT 20">${esc(state.dbSql || '')}</textarea>
          <span class="field-help">Writes are disabled unless you tick the box. A write creates a database backup first.</span></div>
        <label class="checkline"><input type="checkbox" id="db-write" ${state.dbWrite ? 'checked' : ''}>
          <span class="check-text"><strong>Allow write</strong><small>INSERT, UPDATE, DELETE, CREATE, ALTER or DROP.</small></span></label>
        <div class="row"><button class="button" id="db-run">${svgIcon('play')} Run statement</button>
          <span class="muted small">${esc(state.dbPath)}</span></div>
        <div id="db-sql-result">${renderSqlResult()}</div>
      </div>`;
  } else {
    const rows = state.dbRows || { columns: [], rows: [], total: 0, limit: 50, offset: 0 };
    const canEdit = (rows.key && rows.key.mode === 'rowid') || ((state.dbSchema && state.dbSchema.primary_key) || []).length;
    content = `
      <div class="row" style="margin-bottom:10px">
        <div class="search-field" style="max-width:300px">${svgIcon('search')}
          <input class="input small" id="db-query" placeholder="Search all columns" value="${esc(state.dbQuery)}"></div>
        <button class="button text small" id="db-search">Search</button>
        <span class="spacer"></span>
        <button class="button tonal small" id="db-add">${svgIcon('plus')} Add row</button>
      </div>
      <div class="table-wrap"><table class="data-table clickable"><thead><tr>
        ${(rows.columns || []).map((column) => `<th>${esc(column)}</th>`).join('')}
        <th></th></tr></thead>
        <tbody>${(rows.rows || []).map((row, index) => `<tr data-db-row="${index}">
          ${(rows.columns || []).map((column) => `<td>${cellHtml(row[column])}</td>`).join('')}
          <td class="actions">${canEdit ? `<button class="button text small" data-db-delete="${index}" title="Delete row">${svgIcon('trash')}</button>` : ''}</td>
        </tr>`).join('') || `<tr><td colspan="${(rows.columns || []).length + 1}" class="muted">No rows.</td></tr>`}</tbody>
      </table></div>
      <div class="pager">
        <button class="button text small" id="db-prev" ${rows.offset <= 0 ? 'disabled' : ''}>${svgIcon('up')} Previous</button>
        <button class="button text small" id="db-next" ${rows.offset + rows.limit >= rows.total ? 'disabled' : ''}>Next ${svgIcon('down')}</button>
        <span class="info">Rows ${rows.total ? rows.offset + 1 : 0}–${Math.min(rows.offset + rows.limit, rows.total)} of ${rows.total} · limit ${rows.limit}</span>
      </div>`;
  }

  host.innerHTML = `<section class="card">
    <div class="card-header">
      <div class="grow"><h3 class="mono" style="font-size:15px">${esc(state.dbTable)}</h3>
        <p>${tableInfo ? (tableInfo.type === 'view' ? 'View' : 'Table') + ' · ' + (tableInfo.rows ?? '?') + ' rows · ' + (tableInfo.columns ?? '?') + ' columns' : esc(state.dbPath)}</p></div>
      <div class="row"><span class="chip outline">${esc(state.dbPath)}</span>
        <button class="button text small" id="db-refresh">${svgIcon('refresh')} Refresh</button></div>
    </div>
    <div class="tabs">
      <button class="tab ${state.dbTab === 'rows' ? 'active' : ''}" data-db-tab="rows">Rows</button>
      <button class="tab ${state.dbTab === 'schema' ? 'active' : ''}" data-db-tab="schema">Schema</button>
      <button class="tab ${state.dbTab === 'sql' ? 'active' : ''}" data-db-tab="sql">SQL console</button>
    </div>
    ${content}
  </section>`;
  hydrateIcons(host);

  $$('[data-db-tab]', host).forEach((button) => {
    button.onclick = () => { state.dbTab = button.dataset.dbTab; renderDbMain(); };
  });
  const refresh = $('#db-refresh', host);
  if (refresh) refresh.onclick = () => refreshDbTable();
  const search = $('#db-query', host);
  if (search) {
    const run = async () => { state.dbQuery = search.value; state.dbOffset = 0; await refreshDbTable(); };
    search.onkeydown = (event) => { if (event.key === 'Enter') run(); };
    $('#db-search', host).onclick = run;
  }
  const add = $('#db-add', host);
  if (add) add.onclick = () => openDbRowDialog('insert');
  $$('[data-db-row]', host).forEach((rowElement) => {
    rowElement.onclick = (event) => {
      if (event.target.closest('[data-db-delete]')) return;
      openDbRowDialog('edit', Number(rowElement.dataset.dbRow));
    };
  });
  $$('[data-db-delete]', host).forEach((button) => {
    button.onclick = async (event) => {
      event.stopPropagation();
      await deleteDbRow(Number(button.dataset.dbDelete));
    };
  });
  const prev = $('#db-prev', host);
  if (prev) prev.onclick = () => refreshDbTable(Math.max(0, state.dbOffset - (state.dbRows ? state.dbRows.limit : 50)));
  const next = $('#db-next', host);
  if (next) next.onclick = () => refreshDbTable(state.dbOffset + (state.dbRows ? state.dbRows.limit : 50));
  const sqlArea = $('#db-sql', host);
  if (sqlArea) sqlArea.oninput = () => { state.dbSql = sqlArea.value; };
  const write = $('#db-write', host);
  if (write) write.onchange = () => { state.dbWrite = write.checked; };
  const run = $('#db-run', host);
  if (run) run.onclick = runDbSql;
}

async function refreshDbTable(offset = state.dbOffset) {
  if (!state.dbPath || !state.dbTable) return;
  try {
    const query = new URLSearchParams({
      db: state.dbPath, table: state.dbTable, limit: '50', offset: String(offset || 0),
    });
    if (state.dbQuery) query.set('q', state.dbQuery);
    const [schema, rows] = await Promise.all([
      api('/api/admin/db/schema?' + new URLSearchParams({ db: state.dbPath, table: state.dbTable })),
      api('/api/admin/db/rows?' + query),
    ]);
    state.dbSchema = schema;
    state.dbRows = rows;
    state.dbOffset = rows.offset;
    renderDbMain();
  } catch (error) {
    snack(error.message, true);
  }
}

async function openDbRowDialog(mode, rowIndex = -1) {
  const schema = state.dbSchema;
  if (!schema) return;
  const row = mode === 'edit' ? (state.dbRows.rows[rowIndex] || {}) : {};
  const key = mode === 'edit' ? dbKeyForRow(row) : null;
  const dialog = dialogBase(`
    <div class="dialog-head"><div class="grow"><h2>${mode === 'edit' ? 'Edit row' : 'Insert row'}</h2>
      <p>${esc(schema.table)} · ${mode === 'edit' ? 'Only changed fields are written.' : 'Blank fields are omitted so SQLite defaults apply.'}</p></div>
      <button class="button text icon-only" data-close aria-label="Close">${svgIcon('close')}</button></div>
    <div class="dialog-body">
      <div class="notice info">${svgIcon('info')}<div class="notice-body"><strong>Cell format</strong>
        Use <span class="inline-code">null</span> for SQL NULL, JSON for objects, and <span class="inline-code">{"$base64":"…"}</span> for blobs.</div></div>
      <div class="form-grid" style="margin-top:14px">
        ${(schema.columns || []).map((column) => `
          <div class="field full"><label>${esc(column.name)} <span class="muted">${esc(column.type || 'ANY')}${column.pk ? ' · PK' : ''}</span></label>
            <textarea class="input mono db-cell" data-column="${esc(column.name)}" data-type="${esc(column.type || '')}" rows="2" spellcheck="false" placeholder="${mode === 'insert' && column.dflt_value !== null ? 'default: ' + esc(column.dflt_value) : 'omit / value'}">${mode === 'edit' ? esc(editText(row[column.name])) : ''}</textarea>
          </div>`).join('')}
      </div>
    </div>
    <div class="dialog-actions">
      <button class="button text" data-close>Cancel</button>
      <button class="button" id="db-save-row">${svgIcon('save')} ${mode === 'edit' ? 'Save changes' : 'Insert row'}</button>
    </div>`, 'wide');
  if (!dialog) return;
  $('#db-save-row', dialog).onclick = async () => {
    const values = {};
    $$('.db-cell', dialog).forEach((input) => {
      const name = input.dataset.column;
      const text = input.value;
      if (mode === 'insert' && text === '') return;
      if (mode === 'edit') {
        const original = editText(row[name]);
        if (text === original) return;
      }
      values[name] = parseDbCell(text, input.dataset.type);
    });
    if (!Object.keys(values).length) { snack('Nothing changed', true); return; }
    try {
      if (mode === 'edit') {
        if (!key) throw new Error('This table has no usable row key');
        await postJson('/api/admin/db/update', { database: state.dbPath, table: schema.table, key, values });
      } else {
        await postJson('/api/admin/db/insert', { database: state.dbPath, table: schema.table, values });
      }
      dialog.close();
      snack(mode === 'edit' ? 'Row updated' : 'Row inserted');
      await refreshDbTable();
    } catch (error) { snack(error.message, true); }
  };
}

async function deleteDbRow(index) {
  const row = state.dbRows && state.dbRows.rows[index];
  if (!row) return;
  const key = dbKeyForRow(row);
  if (!key) { snack('This table has no usable row key; use the SQL console', true); return; }
  if (!await confirmDialog('Delete row?', 'This row will be deleted from ' + state.dbTable + '. A backup is created first.', 'Delete', true)) return;
  try {
    await postJson('/api/admin/db/delete', { database: state.dbPath, table: state.dbTable, key });
    snack('Row deleted');
    await refreshDbTable();
  } catch (error) { snack(error.message, true); }
}

async function runDbSql() {
  const area = $('#db-sql');
  const write = $('#db-write');
  if (!area) return;
  state.dbSql = area.value;
  state.dbWrite = !!(write && write.checked);
  try {
    const result = await postJson('/api/admin/db/sql', {
      database: state.dbPath, statement: area.value, write: state.dbWrite,
    });
    state.dbSqlResult = result;
    renderDbMain();
    snack('SQL complete · ' + result.elapsed_ms + ' ms');
  } catch (error) {
    state.dbSqlResult = { error: error.message };
    renderDbMain();
  }
}

async function renderDatabasePage() {
  const view = $('#view');
  const status = await getStatus();
  const data = await api('/api/admin/databases');
  const databases = data.databases || [];
  if (!databases.length) {
    view.innerHTML = pageHead('Database studio', 'No SQLite databases were found inside the server workspace.')
      + emptyState('No databases', 'The release server has no .sqlite3 files yet. Start it once to create the configured database.', 'database');
    return;
  }
  if (!state.dbPath || !databases.some((row) => row.path === state.dbPath)) {
    state.dbPath = (status.database && status.database.path) || databases[0].path;
  }
  if (!state.dbTables || state.dbTables.database !== state.dbPath) {
    state.dbTables = await api('/api/admin/db/tables?db=' + encodeURIComponent(state.dbPath));
    state.dbTable = '';
    state.dbRows = null;
    state.dbSchema = null;
  }
  const tables = state.dbTables.tables || [];
  if (!state.dbTable || !tables.some((row) => row.name === state.dbTable)) {
    state.dbTable = (tables.find((row) => row.name === 'players') || tables[0] || {}).name || '';
    state.dbOffset = 0;
    state.dbQuery = '';
  }
  if (state.dbTable && (!state.dbRows || state.dbRows.table !== state.dbTable || state.dbRows.database !== state.dbPath)) {
    await refreshDbTable(0);
  }

  view.innerHTML = `
    ${pageHead('Database studio', 'Browse every SQLite database in the release: tables, views, 50-row pages, live row editing, schema inspection and a guarded SQL console.',
      `<button class="button text" id="db-reload-all">${svgIcon('refresh')} Reload databases</button>`)}
    <div class="split">
      <aside class="pane">
        <div class="pane-head">
          <select class="select input small" id="db-select" style="width:100%">
            ${databases.map((row) => `<option value="${esc(row.path)}" ${row.path === state.dbPath ? 'selected' : ''}>${esc(row.path)} · ${row.tables ?? '?'} tables</option>`).join('')}
          </select>
        </div>
        <div class="pane-head" style="border-top:1px solid var(--m3-outline-variant)">
          <div class="search-field" style="width:100%">${svgIcon('search')}
            <input class="input small" id="db-table-search" placeholder="Filter tables" value="${esc(state.dbTableQuery || '')}"></div>
        </div>
        <div class="pane-body"><div class="table-list" id="db-table-list"></div></div>
      </aside>
      <div id="db-main"></div>
    </div>`;
  hydrateIcons(view);
  renderDbList();
  renderDbMain();
  $('#db-select', view).onchange = async (event) => {
    state.dbPath = event.target.value;
    state.dbTables = null;
    state.dbTable = '';
    state.dbRows = null;
    state.dbSchema = null;
    state.dbSqlResult = null;
    renderRoute();
  };
  $('#db-table-search', view).oninput = (event) => {
    state.dbTableQuery = event.target.value;
    renderDbList();
  };
  $('#db-reload-all', view).onclick = () => { state.dbTables = null; state.dbRows = null; renderRoute(); };
}

/* ---------------------------------------------------------------------- players */
const SENSITIVE_COLUMN = /pass|hash|salt|token|secret|private|key/i;

function pickSafeColumns(columns, limit = 8) {
  return columns.filter((column) => !SENSITIVE_COLUMN.test(column)).slice(0, limit);
}

function playersTable(rows, title, note, tableName) {
  const columns = pickSafeColumns((rows && rows.columns) || []);
  return `<section class="card">
    <div class="card-header"><div class="grow"><h3>${esc(title)}</h3><p>${esc(note)}</p></div>
      <span class="chip outline">${rows ? rows.total : 0}</span></div>
    ${rows && columns.length ? `<div class="table-wrap"><table class="data-table"><thead><tr>
        ${columns.map((column) => `<th>${esc(column)}</th>`).join('')}<th></th></tr></thead>
      <tbody>${rows.rows.map((row) => `<tr>${columns.map((column) => `<td>${cellHtml(row[column])}</td>`).join('')}
        <td class="actions"><button class="button text small" data-open-table="${esc(tableName)}">Open</button></td></tr>`).join('')}</tbody></table></div>`
      : `<div class="muted small">No rows to show${rows ? ' for this search' : ' — table not present'}.</div>`}
  </section>`;
}

async function renderPlayersPage() {
  const view = $('#view');
  const status = await getStatus();
  const database = status.database || {};
  if (!database.path || !database.exists) {
    view.innerHTML = pageHead('Players', 'Account and player records in the active database.')
      + emptyState('No active database', 'Start the server once or point the profile at a SQLite database, then return here.', 'users');
    return;
  }
  const tablesData = await api('/api/admin/db/tables?db=' + encodeURIComponent(database.path));
  const names = new Set((tablesData.tables || []).map((row) => row.name));
  const query = state.playerQuery || '';
  let accounts = null;
  let players = null;
  if (names.has('family_accounts')) {
    accounts = await api('/api/admin/db/rows?' + new URLSearchParams({ db: database.path, table: 'family_accounts', limit: '100', q: query }));
  }
  if (names.has('players')) {
    players = await api('/api/admin/db/rows?' + new URLSearchParams({ db: database.path, table: 'players', limit: '100', q: query }));
  }
  view.innerHTML = `
    ${pageHead('Players', 'Read-only overview of accounts and player rows. Sensitive columns are hidden here; the Database studio exposes the complete schema.',
      `<span class="chip outline mono">${esc(database.path)}</span>`)}
    <div class="grid four">
      ${metricCard('Accounts', String(database.accounts ?? 0), 'family_accounts', 'users')}
      ${metricCard('Players', String(database.players ?? 0), 'player rows', 'users')}
      ${metricCard('Pokémon', String(database.pokemon ?? 0), 'stored creatures', 'sparkles')}
      ${metricCard('Tables', String(database.tables ?? 0), fmtBytes(database.bytes), 'database')}
    </div>
    <div class="toolbar" style="margin-top:16px">
      <div class="search-field" style="max-width:360px">${svgIcon('search')}
        <input class="input" id="player-search" placeholder="Search accounts and players" value="${esc(query)}"></div>
      <span class="muted small">Finds matching rows across all text columns.</span>
      <span class="spacer"></span>
      <button class="button tonal" data-go="database">${svgIcon('database')} Open Database studio</button>
    </div>
    <div class="grid two" style="margin-top:16px">
      ${playersTable(accounts, 'Family accounts', 'Usernames, trainer identity and capacity settings.', 'family_accounts')}
      ${playersTable(players, 'Players', 'Live player state rows.', 'players')}
    </div>`;
  hydrateIcons(view);
  bindGo(view);
  $$('[data-open-table]', view).forEach((button) => {
    button.onclick = () => {
      state.dbPath = database.path;
      state.dbTables = null;
      state.dbTable = button.dataset.openTable;
      state.dbOffset = 0;
      state.dbQuery = '';
      navigate('database');
    };
  });
  const search = $('#player-search', view);
  let timer = null;
  search.oninput = () => {
    clearTimeout(timer);
    timer = setTimeout(() => { state.playerQuery = search.value; renderRoute(); }, 280);
  };
}

/* ------------------------------------------------------------------ game master */
async function renderGameMasterPage() {
  const view = $('#view');
  const status = await getStatus(true);
  const gm = status.game_master || { groups: [], sources: [], gmts_present: false, apk_present: false };
  view.innerHTML = `
    ${pageHead('Game Master', 'Integrity view for the binary Game Master sources included in the release. No APK is shipped; the Game Master data is.',
      `<span class="chip ${gm.gmts_present ? 'success' : 'error'}">${gm.gmts_present ? 'GMTS present' : 'GMTS missing'}</span>
       <span class="chip outline">No APK</span>
       <button class="button text" id="gm-refresh">${svgIcon('refresh')} Re-verify</button>`)}
    <div class="grid two">
      ${(gm.groups || []).map((group) => `<section class="card">
        <div class="card-header"><div class="grow"><h3>${esc(group.folder)}</h3>
          <p>${group.present ? 'Present in the release workspace.' : 'Folder missing from this workspace.'}</p></div>
          <span class="chip ${group.present ? 'success' : 'error'}">${group.present ? 'included' : 'missing'}</span></div>
        ${group.present ? `<div class="stack tight">${(group.files || []).map((file) => `
          <div class="row between" style="border-top:1px solid var(--m3-outline-variant);padding-top:9px">
            <div><div class="mono">${esc(file.name)}</div>
              ${file.present ? `<div class="tiny muted">${fmtBytes(file.bytes)} · modified ${esc(ago(file.modified_ms))}</div>` : ''}</div>
            ${!file.present ? '<span class="chip error">missing</span>'
              : file.verified === true ? '<span class="chip success">sha256 verified</span>'
              : file.verified === false ? `<span class="chip error">hash mismatch</span>`
              : '<span class="chip outline">no manifest entry</span>'}
          </div>
          ${file.present && file.sha256 ? `<div class="file-path">${esc(file.sha256)}</div>` : ''}
        `).join('')}</div>` : '<div class="muted small">Nothing to verify.</div>'}
      </section>`).join('')}
    </div>
    <div class="grid two" style="margin-top:16px">
      <section class="card">
        <div class="card-header"><div class="grow"><h3>Reference sources</h3><p>Editable JSON that feeds Max, PvP and the Game Master manifest.</p></div></div>
        ${(gm.sources || []).length ? `<div class="file-list">${gm.sources.map((source) => `
          <button class="file-row" data-gm-file="${esc(source.path)}">
            <span class="file-icon" data-icon="code"></span>
            <span class="name">${esc(source.path)}</span>
            <span class="meta">${fmtBytes(source.bytes)}</span>${svgIcon('arrow')}
          </button>`).join('')}</div>` : '<div class="muted small">No reference sources found.</div>'}
      </section>
      <section class="card">
        <div class="card-header"><div class="grow"><h3>Release policy</h3><p>What is included and what stays out.</p></div></div>
        <div class="kv">
          <span class="k">Game Master</span><span class="v">${gm.gmts_present ? 'included' : 'missing'}</span>
          <span class="k">APK</span><span class="v">${gm.apk_present ? 'present — remove it from a clean release' : 'not shipped'}</span>
          <span class="k">Assets</span><span class="v">Pokémon artwork, avatars and client runtime assets are excluded.</span>
          <span class="k">Source</span><span class="v">Full server and app source, including the Game Master, is in the workspace.</span>
        </div>
      </section>
    </div>`;
  hydrateIcons(view);
  $('#gm-refresh', view).onclick = () => renderRoute();
  $$('[data-gm-file]', view).forEach((button) => {
    button.onclick = () => openFile(button.dataset.gmFile);
  });
}

/* ------------------------------------------------------------------------ loot */
async function renderLootPage() {
  const view = $('#view');
  const docs = await getDocuments();
  const meta = (docs.documents || []).find((row) => row.path === 'data/admin/stop-loot.json');
  const related = (docs.documents || []).filter((row) => row.section === 'loot' && row.path !== 'data/admin/stop-loot.json');
  let doc = null;
  let loadError = '';
  if (meta) {
    try { doc = await api('/api/admin/document?path=' + encodeURIComponent(meta.path)); } catch (error) { loadError = error.message; }
  }
  if (doc && (!state.lootDraft || state.lootPath !== doc.path)) {
    state.lootDraft = clone(doc.value || {});
    state.lootPath = doc.path;
    state.lootDirty = false;
  }
  view.innerHTML = `
    ${pageHead('Loot &amp; rewards', 'Edit the stop loot table as a structured document, plus every reward file in game data.',
      `${doc ? `<button class="button text" id="loot-raw">${svgIcon('code')} Raw JSON</button>
        <button class="button" id="loot-save">${svgIcon('save')} Save loot table</button>` : ''}`)}
    ${loadError ? `<div class="notice error">${svgIcon('warning')}<div class="notice-body">${esc(loadError)}</div></div>` : ''}
    ${doc ? `<div class="card">
      <div class="card-header"><div class="grow"><h3 class="mono" style="font-size:14px">${esc(doc.path)}</h3>
        <p>${fmtBytes(doc.bytes)} · modified ${esc(ago(doc.modified_ms))} · structured editing with validation</p></div>
        <span class="chip ${state.lootDirty ? 'warn' : 'success'}" id="loot-dirty">${state.lootDirty ? 'Unsaved changes' : 'Saved'}</span></div>
      <div id="loot-editor"></div>
    </div>` : emptyState('Loot table not found', 'The release does not contain data/admin/stop-loot.json. Create an empty table and build it from the Game Master item list.', 'gift',
      '<button class="button" id="loot-create">Create stop-loot.json</button>')}
    <section class="card" style="margin-top:16px">
      <div class="card-header"><div class="grow"><h3>Related loot documents</h3><p>Raid, research and event reward tables found in the workspace.</p></div>
        <span class="chip outline">${related.length}</span></div>
      ${related.length ? `<div class="file-list">${related.map((row) => `<button class="file-row" data-loot-file="${esc(row.path)}">
        <span class="file-icon" data-icon="file"></span><span class="name">${esc(row.path)}</span>
        <span class="meta">${fmtBytes(row.bytes)}</span>${svgIcon('arrow')}</button>`).join('')}</div>`
        : '<div class="muted small">No other loot documents matched.</div>'}
    </section>`;
  hydrateIcons(view);
  const create = $('#loot-create', view);
  if (create) create.onclick = async () => {
    try {
      const result = await postJson('/api/admin/document', { path: 'data/admin/stop-loot.json', value: {} });
      state.lootPath = result.path;
      state.lootDraft = clone(result.value || {});
      state.lootDirty = false;
      await getDocuments(true);
      snack('Created data/admin/stop-loot.json');
      renderRoute();
    } catch (error) { snack(error.message, true); }
  };
  if (!doc) return;
  const host = $('#loot-editor', view);
  host.appendChild(createJsonEditor(state.lootDraft, {
    rootLabel: 'stop loot table',
    onChange: () => {
      state.lootDirty = true;
      const chip = $('#loot-dirty'); if (chip) { chip.textContent = 'Unsaved changes'; chip.className = 'chip warn'; }
    },
  }));
  $('#loot-save', view).onclick = async () => {
    try {
      const result = await postJson('/api/admin/document', { path: doc.path, value: state.lootDraft });
      state.lootDraft = clone(result.value || {});
      state.lootDirty = false;
      const chip = $('#loot-dirty'); if (chip) { chip.textContent = 'Saved'; chip.className = 'chip success'; }
      snack('Loot table saved (backup created)');
      await getDocuments(true);
    } catch (error) { snack(error.message, true); }
  };
  $('#loot-raw', view).onclick = () => openRawJsonDialog({
    title: 'Raw stop loot JSON', value: state.lootDraft,
    onApply: (parsed) => {
      state.lootDraft = parsed;
      state.lootDirty = true;
      const chip = $('#loot-dirty'); if (chip) { chip.textContent = 'Unsaved changes'; chip.className = 'chip warn'; }
      host.replaceChildren();
      host.appendChild(createJsonEditor(state.lootDraft, {
        rootLabel: 'stop loot table',
        onChange: () => { state.lootDirty = true; },
      }));
      snack('Raw JSON applied in the browser; press Save loot table to write it');
    },
  });
  $$('[data-loot-file]', view).forEach((button) => {
    button.onclick = () => openFile(button.dataset.lootFile);
  });
}

/* -------------------------------------------------------------------- platform */
function roomCard(room, hosted) {
  return `<article class="card room-card">
    <div class="row between"><div><div class="room-name">${esc(room.title || room.domain)}</div>
      <div class="room-id">${esc(room.domain)}</div></div>
      <div class="pill-row"><span class="chip ${room.online ? 'success' : 'outline'}">${room.online ? 'live' : 'idle'}</span>
        <span class="chip ${hosted ? 'primary' : 'outline'}">${hosted ? 'hosted here' : 'remote'}</span></div></div>
    <div class="muted small">${esc(room.description || 'No description.')}</div>
    <div class="row tiny muted">
      <span class="chip">${room.players || 0} players</span><span class="chip">v${room.version || 0}</span>
      <span class="chip">${fmtBytes(room.state_bytes || 0)}</span>
      <span>updated ${esc(ago(room.last_publish_ms))}</span>
    </div>
    <div class="row" style="margin-top:auto">
      <button class="button tonal small" data-room-action="open" data-domain="${esc(room.domain)}">${svgIcon('eye')} Open</button>
      <button class="button text small" data-room-action="copy" data-domain="${esc(room.domain)}">${svgIcon('copy')} Copy ID</button>
      ${hosted ? `<button class="button text small" data-room-action="unhost" data-domain="${esc(room.domain)}">Stop hosting</button>`
        : `<button class="button text small" data-room-action="host" data-domain="${esc(room.domain)}">Host</button>`}
      <span class="spacer"></span>
      <button class="button text small danger" data-room-action="delete" data-domain="${esc(room.domain)}">${svgIcon('trash')}</button>
    </div>
  </article>`;
}

async function openRoomDialog(domain) {
  try {
    const info = await api('/api/admin/rooms/' + encodeURIComponent(domain));
    const bootstrap = await api('/api/rooms/' + encodeURIComponent(domain) + '/bootstrap').catch((error) => ({ error: error.message }));
    const room = info.room;
    dialogBase(`
      <div class="dialog-head"><div class="grow"><h2>${esc(room.title || room.domain)}</h2>
        <p class="mono">${esc(room.domain)}</p></div>
        <button class="button text icon-only" data-close>${svgIcon('close')}</button></div>
      <div class="dialog-body">
        <div class="kv">
          <span class="k">Room id</span><span class="v mono">${esc(room.room_id || '—')}</span>
          <span class="k">Host key</span><span class="v mono">${esc(room.host_fingerprint || '—')}</span>
          <span class="k">State version</span><span class="v">${room.version || 0}</span>
          <span class="k">Players</span><span class="v">${room.players || 0}</span>
          <span class="k">Online</span><span class="v">${room.online ? 'yes' : 'no'}</span>
          <span class="k">Updated</span><span class="v">${esc(ago(room.last_publish_ms))}</span>
        </div>
        <details class="panel" style="margin-top:14px"><summary>${svgIcon('code')} <span>Signed bootstrap payload</span></summary>
          <pre class="code" style="max-height:300px">${esc(JSON.stringify(bootstrap, null, 2))}</pre></details>
        <details class="panel" style="margin-top:10px"><summary>${svgIcon('clock')} <span>Recent activity</span></summary>
          <div class="timeline">${(info.events || []).map((event) => `<div class="timeline-item"><span class="when">${esc(ago(event.ms))}</span><span class="grow">${esc(event.text)}</span></div>`).join('') || '<div class="muted small">Nothing yet.</div>'}</div></details>
      </div>
      <div class="dialog-actions">
        <button class="button text" data-copy="${esc(domain)}">${svgIcon('copy')} Copy room ID</button>
        <button class="button" data-close>Close</button>
      </div>`, 'wide');
    bindCopy($('#dialog'));
  } catch (error) { snack(error.message, true); }
}

async function renderPlatformPage() {
  const view = $('#view');
  const [settings, platform, roomsData] = await Promise.all([
    api('/api/admin/platform'),
    getPlatformStatus(true),
    api('/api/admin/rooms'),
  ]);
  const poi = await api('/api/poi-import').catch(() => null);
  const pois = poi ? await api('/api/pois?limit=25').catch(() => ({ pois: [], total: 0 })) : { pois: [], total: 0 };
  const mqtt = settings.mqtt || {};
  const defaults = (platform && platform.defaults) || {};
  const mix = defaults.spawn_percent || {};
  const rooms = roomsData.rooms || [];
  const hosted = roomsData.hosted || '';
  const center = (poi && poi.center) || null;

  view.innerHTML = `
    ${pageHead('Platform &amp; rooms', 'The platform admin runs on its configured local port. Host signed rooms, tune the HiveMQ bridge and import OSM points of interest without leaving it.',
      `<span class="chip ${(platform && platform.mqtt && platform.mqtt.connected) ? 'success' : 'warn'}">${(platform && platform.mqtt && platform.mqtt.connected) ? 'HiveMQ connected' : 'HiveMQ offline'}</span>
       <button class="button text" id="platform-refresh">${svgIcon('refresh')} Refresh</button>`)}
    <div class="grid two">
      <section class="card">
        <div class="card-header"><div class="grow"><h3>Platform settings</h3><p>Identity, the active server profile and the MQTT bridge. Listener host/port are read-only here.</p></div>
          <span class="chip outline">:${esc(settings.port ?? 8780)}</span></div>
        <div class="form-grid">
          <div class="field full"><label>Platform name</label><input class="input" id="pf-name" value="${esc(settings.name || '')}"></div>
          <div class="field full"><label>Public URL</label><input class="input" id="pf-public-url" value="${esc(settings.public_url || '')}" placeholder="Optional public URL">
            <span class="field-help">Advertised to clients. Leave blank for a purely local room service.</span></div>
          <div class="field full"><label>Admin token</label><div class="input-group">
            <form id="pf-token-form"></form>
            <input class="input mono" id="pf-token" type="password" form="pf-token-form" value="${esc(settings.admin_token || '')}" autocomplete="new-password">
            <button class="button text small" id="pf-token-show" type="button">${svgIcon('eye')}</button></div>
            <span class="field-help">Leave blank to keep the API open on this machine. A changed token takes effect after the platform restarts.</span></div>
          <div class="field full"><label class="checkline"><input type="checkbox" id="pf-mqtt-on" ${mqtt.enabled ? 'checked' : ''}>
            <span class="check-text"><strong>MQTT bridge</strong><small>Connect rooms to the HiveMQ broker.</small></span></label></div>
          <div class="field"><label>Broker host</label><input class="input" id="pf-mqtt-host" value="${esc(mqtt.host || 'broker.hivemq.com')}"></div>
          <div class="field"><label>Broker port</label><input class="input" id="pf-mqtt-port" type="number" value="${esc(mqtt.port ?? 8883)}"></div>
          <div class="field"><label>Transport</label><label class="checkline"><input type="checkbox" id="pf-mqtt-tls" ${mqtt.tls ? 'checked' : ''}> <span class="check-text"><strong>TLS</strong><small>Port 8883 usually.</small></span></label></div>
        </div>
        <div class="row" style="margin-top:16px"><button class="button" id="platform-save">${svgIcon('save')} Save platform settings</button>
          <span class="muted small">Host ${esc(settings.host || '127.0.0.1')} · port ${esc(settings.port ?? 8780)} · standalone room service</span></div>
        <div class="notice warn" style="margin-top:12px">${svgIcon('warning')}<div class="notice-body">
          <strong>Bridge and listener changes need a restart</strong>Room hosting itself applies immediately; broker and admin listener settings do not.</div></div>
      </section>

      <section class="card">
        <div class="card-header"><div class="grow"><h3>Hosted rooms</h3><p>Only the room hosted on this PC is advertised and served.</p></div>
          <span class="chip outline">${rooms.length} rooms</span></div>
        <form id="room-create-form" class="stack tight" style="margin-bottom:16px">
          <div class="form-grid">
            <div class="field full"><label>Room ID</label><input class="input" id="room-name" maxlength="60" placeholder="fairview-community" required>
              <span class="field-help">Letters, digits, spaces and dashes become a lowercase ID.</span></div>
            <div class="field full"><label>Description</label><input class="input" id="room-description" maxlength="400" placeholder="A quiet room for friends."></div>
            <div class="field"><label>Owner</label><input class="input" id="room-owner" maxlength="60" placeholder="Trainer name"></div>
            <div class="field" style="justify-content:flex-end"><button class="button" type="submit">${svgIcon('plus')} Host a room</button></div>
          </div>
        </form>
        ${rooms.length ? `<div class="stack tight">${rooms.map((room) => roomCard(room, room.domain === hosted)).join('')}</div>`
          : emptyState('No rooms yet', 'Create a room above. Players join by typing the same room ID in Dittobuffer.', 'cloud')}
      </section>
    </div>

    <div class="grid two" style="margin-top:16px">
      <section class="card">
        <div class="card-header"><div class="grow"><h3>Automatic OSM POIs</h3><p>Scans OpenStreetMap around the platform centre while it runs.</p></div>
          <span class="chip ${poi && poi.enabled ? 'success' : 'outline'}">${poi ? (poi.scanning ? 'scanning' : (poi.enabled ? 'enabled' : 'disabled')) : 'unavailable'}</span></div>
        ${poi ? `<div class="form-grid">
          <div class="field"><label>Latitude</label><input class="input" id="poi-lat" type="number" step="any" value="${center ? center[0] : ''}" placeholder="51.5074"></div>
          <div class="field"><label>Longitude</label><input class="input" id="poi-lng" type="number" step="any" value="${center ? center[1] : ''}" placeholder="-0.1278"></div>
          <div class="field"><label>Radius (m)</label><input class="input" id="poi-radius" type="number" min="200" max="5000" step="50" value="${esc(poi.radius_m ?? 1200)}"></div>
          <div class="field"><label>Interval (s)</label><input class="input" id="poi-interval" type="number" min="15" max="3600" step="5" value="${esc(poi.interval_s ?? 60)}"></div>
          <div class="field"><label>Places per scan</label><input class="input" id="poi-limit" type="number" min="10" max="300" step="10" value="${esc(poi.limit ?? 80)}"></div>
          <div class="field"><label>Gym chance (%)</label><input class="input" id="poi-gym" type="number" min="0" max="100" step="1" value="${esc(poi.gym_percent ?? 20)}"></div>
          <div class="field full"><label class="checkline"><input type="checkbox" id="poi-enabled" ${poi.enabled ? 'checked' : ''}>
            <span class="check-text"><strong>Scan automatically</strong><small>While the platform is running.</small></span></label></div>
        </div>
        <div class="row" style="margin-top:14px"><button class="button" id="poi-save">${svgIcon('save')} Save POI settings</button>
          <button class="button tonal" id="poi-scan">${svgIcon('refresh')} Scan now</button>
          <button class="button text danger" id="poi-clear">${svgIcon('trash')} Clear generated</button></div>
        <div class="muted small" style="margin-top:10px">${(poi.auto_pois || 0)} imported · ${(poi.auto_gyms || 0)} gyms · ${(poi.auto_stops || 0)} stops${poi.last_scan_ms ? ' · last scan ' + esc(ago(poi.last_scan_ms)) : ''}${poi.last_error ? ' · ' + esc(poi.last_error) : ''}</div>
        <details class="panel" style="margin-top:12px"><summary>${svgIcon('pin')} <span>Imported places</span></summary>
          <div class="file-list">${(pois.pois || []).map((row) => `<div class="row between" style="border-top:1px solid var(--m3-outline-variant);padding:7px 0">
            <span>${esc(row.name || row.id)}</span><span class="chip">${Number(row.kind) === 0 ? 'gym' : 'stop'}</span></div>`).join('') || '<div class="muted small">No imported places yet.</div>'}</div>
        </details>` : `<div class="notice warn">${svgIcon('warning')}<div class="notice-body">POI import is disabled in this platform build.</div></div>`}
      </section>

      <section class="card">
        <div class="card-header"><div class="grow"><h3>Room defaults</h3><p>The live system pages own the full spawn, raid, event and shiny payload.</p></div></div>
        <div class="notice info">${svgIcon('info')}<div class="notice-body"><strong>Complete controls</strong>Use <button class="button text small" data-go="spawns">Spawns &amp; algorithm</button>, <button class="button text small" data-go="raids">Raids</button> and <button class="button text small" data-go="events">Events</button> for the full signed room payload.</div></div>
        <div class="field" style="margin-top:12px"><label for="defaults-mix">Quick spawn shares · <span class="inline-code">species=percent</span></label>
          <textarea class="input mono" id="defaults-mix" rows="8" spellcheck="false">${Object.entries(mix).map(([key, value]) => key + '=' + value).join('\n')}</textarea>
          <span class="field-help">Legacy quick default. The full algorithm is on the Spawns page. <span id="defaults-other" class="chip outline">other 100%</span></span></div>
        <div class="row" style="margin-top:12px"><button class="button" id="defaults-save">${svgIcon('save')} Save quick shares</button></div>
        <div class="divider"></div>
        <div class="kv">
          <span class="k">Public URL</span><span class="v mono">${esc((platform && platform.public_url) || 'local only')}</span>
          <span class="k">Hosted</span><span class="v mono">${esc(hosted || 'none')}</span>
          <span class="k">Fingerprint</span><span class="v mono">${esc((platform && platform.fingerprint) || '—')}</span>
        </div>
      </section>
    </div>

    <section class="card" style="margin-top:16px">
      <div class="card-header"><div class="grow"><h3>Recovery</h3><p>Replace the platform signing key and recreate the hosted room in one step. Existing clients must rejoin after a key reset.</p></div>
        <span class="chip outline">one click</span></div>
      <div class="form-grid">
        <div class="field"><label>New room address</label><input class="input" id="recovery-room" maxlength="60" value="${esc(hosted || '')}" placeholder="fairview-community" required>
          <span class="field-help">The name players type in Dittobuffer. No DNS record is needed.</span></div>
        <div class="field"><label>Public URL</label><input class="input" id="recovery-public-url" value="${esc(settings.public_url || '')}" placeholder="Optional public URL"></div>
      </div>
      <div class="notice warn" style="margin-top:12px">${svgIcon('warning')}<div class="notice-body">
        <strong>This clears room bindings</strong>Platform JSON is backed up first. The signing key is replaced, all room entries are cleared, and the room above is created and hosted. Existing app installs must join again.</div></div>
      <div class="row" style="margin-top:14px"><button class="button danger" id="recovery-run">${svgIcon('refresh')} Reset signature and room</button></div>
    </section>`;
  hydrateIcons(view);
  const tokenForm = $('#pf-token-form', view);
  if (tokenForm) tokenForm.addEventListener('submit', (event) => event.preventDefault());
  const show = $('#pf-token-show', view);
  if (show) show.onclick = () => {
    const input = $('#pf-token', view);
    input.type = input.type === 'password' ? 'text' : 'password';
  };
  $('#platform-refresh', view).onclick = () => { state.platformStatus = null; renderRoute(); };
  const recoveryButton = $('#recovery-run', view);
  if (recoveryButton) recoveryButton.onclick = async () => {
    const roomAddress = $('#recovery-room', view).value.trim();
    if (!roomAddress) return snack('Enter a room address first', true);
    if (!await confirmDialog('Reset signing key and room?',
      'The platform key will be replaced, every room binding cleared, and ' + roomAddress + ' hosted. Existing clients must rejoin.',
      'Reset', true)) return;
    recoveryButton.disabled = true;
    try {
      const result = await postJson('/api/admin/recovery', {
        room_address: roomAddress,
        public_url: $('#recovery-public-url', view).value.trim(),
        rotate_key: true,
        clear_rooms: true,
      });
      await copyText(result.room_address);
      snack('Signature reset. ' + result.room_address + ' is hosted; existing clients must rejoin.');
      state.platformStatus = null;
      renderRoute();
    } catch (error) {
      recoveryButton.disabled = false;
      snack(error.message, true);
    }
  };
  $('#platform-save', view).onclick = async () => {
    try {
      await postJson('/api/admin/platform', {
        name: $('#pf-name', view).value,
        public_url: $('#pf-public-url', view).value,
        admin_token: $('#pf-token', view).value,
        mqtt: {
          enabled: $('#pf-mqtt-on', view).checked,
          host: $('#pf-mqtt-host', view).value,
          port: Number($('#pf-mqtt-port', view).value),
          tls: $('#pf-mqtt-tls', view).checked,
        },
      });
      snack('Platform settings saved; restart the platform to apply bridge changes');
      state.platformStatus = null;
      renderRoute();
    } catch (error) { snack(error.message, true); }
  };
  $('#room-create-form', view).onsubmit = async (event) => {
    event.preventDefault();
    try {
      const created = await postJson('/api/rooms', {
        name: $('#room-name', view).value.trim(),
        description: $('#room-description', view).value.trim(),
        owner: $('#room-owner', view).value.trim(),
        host: true,
      });
      await copyText(created.room.domain);
      snack('Room hosted. Type ' + created.room.domain + ' in Dittobuffer.');
      state.platformStatus = null;
      renderRoute();
    } catch (error) { snack(error.message, true); }
  };
  $$('[data-room-action]', view).forEach((button) => {
    button.onclick = async () => {
      const domain = button.dataset.domain;
      const action = button.dataset.roomAction;
      if (action === 'open') return openRoomDialog(domain);
      if (action === 'copy') return copyText(domain);
      try {
        if (action === 'unhost') {
          await postJson('/api/rooms/' + encodeURIComponent(domain) + '/unhost');
          snack('Stopped hosting ' + domain);
        } else if (action === 'host') {
          await postJson('/api/rooms/' + encodeURIComponent(domain) + '/host');
          snack('Hosting ' + domain);
        } else if (action === 'delete') {
          if (!await confirmDialog('Delete room?', 'Delete ' + domain + ' permanently?', 'Delete', true)) return;
          await api('/api/rooms/' + encodeURIComponent(domain), { method: 'DELETE' });
          snack('Room deleted');
        }
        state.platformStatus = null;
        renderRoute();
      } catch (error) { snack(error.message, true); }
    };
  });
  const mixArea = $('#defaults-mix', view);
  const updateOther = () => {
    let total = 0;
    (mixArea.value || '').split(/\n+/).map((line) => line.trim()).filter(Boolean).forEach((line) => {
      const value = Number(line.slice(line.indexOf('=') + 1));
      if (Number.isFinite(value)) total += value;
    });
    const other = $('#defaults-other', view);
    if (other) other.textContent = 'other ' + Math.max(0, Math.round((100 - total) * 10) / 10) + '%';
  };
  if (mixArea) { mixArea.oninput = updateOther; updateOther(); }
  const saveDefaults = $('#defaults-save', view);
  if (saveDefaults) saveDefaults.onclick = async () => {
    try {
      const output = {};
      (mixArea.value || '').split(/\n+/).map((line) => line.trim()).filter(Boolean).forEach((line) => {
        const at = line.indexOf('=');
        if (at < 0) throw new Error('Use species=percent lines');
        const key = line.slice(0, at).trim();
        const value = Number(line.slice(at + 1));
        if (!key || !Number.isFinite(value)) throw new Error('Invalid line: ' + line);
        output[key] = value;
      });
      await postJson('/api/defaults', { spawn_percent: output });
      snack('Room defaults saved');
      state.platformStatus = null;
      renderRoute();
    } catch (error) { snack(error.message, true); }
  };
  if (poi) {
    $('#poi-save', view).onclick = async () => {
      try {
        const lat = $('#poi-lat', view).value.trim();
        const lng = $('#poi-lng', view).value.trim();
        if ((lat === '') !== (lng === '')) throw new Error('Set both latitude and longitude, or leave both blank.');
        await postJson('/api/poi-import', {
          enabled: $('#poi-enabled', view).checked,
          radius_m: Number($('#poi-radius', view).value),
          interval_s: Number($('#poi-interval', view).value),
          limit: Number($('#poi-limit', view).value),
          gym_percent: Number($('#poi-gym', view).value),
          center: lat === '' ? null : { lat: Number(lat), lng: Number(lng) },
        });
        snack('POI settings saved');
        renderRoute();
      } catch (error) { snack(error.message, true); }
    };
    $('#poi-scan', view).onclick = async () => {
      try { await postJson('/api/poi-import/scan'); snack('POI scan queued'); setTimeout(renderRoute, 1600); }
      catch (error) { snack(error.message, true); }
    };
    $('#poi-clear', view).onclick = async () => {
      if (!await confirmDialog('Clear generated POIs?', 'Remove every automatically imported place?', 'Clear', true)) return;
      try { await postJson('/api/poi-import/clear'); snack('Generated POIs cleared'); renderRoute(); }
      catch (error) { snack(error.message, true); }
    };
  }
}

/* ------------------------------------------------------------------------ logs */
async function renderLogsPage() {
  const view = $('#view');
  let data;
  try { data = await api('/api/admin/logs'); } catch (error) { data = { logs: [] }; }
  const logs = data.logs || [];
  if (!logs.length) {
    view.innerHTML = pageHead('Logs', 'Server output written under <span class="inline-code">server/logs/</span>.')
      + emptyState('No log files yet', 'Start the game server from Server control. Its output is written to logs/server-platform.log.', 'terminal',
        '<button class="button" data-go="server">Open server control</button>');
    bindGo(view);
    return;
  }
  if (!state.logName || !logs.some((row) => row.name === state.logName)) state.logName = logs[0].name;
  const log = await api('/api/admin/log?' + new URLSearchParams({ name: state.logName, tail: '400' }));
  state.logText = log.text || '';
  view.innerHTML = `
    ${pageHead('Logs', 'Tail the server logs without opening a terminal. Output refreshes every three seconds while auto-refresh is on.',
      `<label class="checkline" style="min-height:40px"><input type="checkbox" id="log-auto" ${state.logAuto ? 'checked' : ''}>
        <span class="check-text"><strong>Auto-refresh</strong><small>Every 3 seconds</small></span></label>
       <button class="button text" id="log-refresh">${svgIcon('refresh')} Refresh</button>
       <button class="button danger" id="log-clear">${svgIcon('trash')} Clear log</button>`)}
    <div class="split">
      <aside class="pane">
        <div class="pane-head"><strong class="small">Log files</strong><span class="spacer"></span><span class="chip outline">${logs.length}</span></div>
        <div class="pane-body"><div class="file-list">
          ${logs.map((row) => `<button class="file-row ${row.name === state.logName ? 'active' : ''}" data-log="${esc(row.name)}">
            <span class="file-icon" data-icon="terminal"></span><span class="name">${esc(row.name)}</span>
            <span class="meta">${fmtBytes(row.bytes)}</span></button>`).join('')}
        </div></div>
      </aside>
      <section class="card">
        <div class="editor-head"><div class="grow"><div class="row"><h3 class="mono" style="font-size:14px">${esc(state.logName)}</h3>
          <button class="button text small icon-only" data-copy="${esc(state.logName)}">${svgIcon('copy')}</button></div>
          <div class="row" style="margin-top:5px"><span class="chip outline" id="log-meta">${fmtBytes(log.bytes)} · modified ${esc(ago(log.modified_ms))}</span>
          <span class="chip success" id="log-live">tail 400 lines</span></div></div>
          <button class="button text small" id="log-copy-text">${svgIcon('copy')} Copy output</button></div>
        <pre class="code" id="log-text" style="max-height:62vh;min-height:400px">${esc(state.logText)}</pre>
      </section>
    </div>`;
  hydrateIcons(view);
  bindCopy(view);
  $$('[data-log]', view).forEach((button) => {
    button.onclick = () => { state.logName = button.dataset.log; renderRoute(); };
  });
  const refresh = async () => {
    try {
      const fresh = await api('/api/admin/log?' + new URLSearchParams({ name: state.logName, tail: '400' }));
      state.logText = fresh.text || '';
      const text = $('#log-text', view);
      if (text) { const atBottom = text.scrollTop + text.clientHeight >= text.scrollHeight - 30; text.textContent = state.logText; if (atBottom) text.scrollTop = text.scrollHeight; }
      const meta = $('#log-meta', view);
      if (meta) meta.textContent = fmtBytes(fresh.bytes) + ' · modified ' + ago(fresh.modified_ms);
    } catch (error) { snack(error.message, true); }
  };
  $('#log-refresh', view).onclick = refresh;
  $('#log-copy-text', view).onclick = () => copyText(state.logText);
  const auto = $('#log-auto', view);
  auto.onchange = () => { state.logAuto = auto.checked; if (!auto.checked && state.logTimer) { clearInterval(state.logTimer); state.logTimer = null; } else if (auto.checked) startAuto(); };
  const startAuto = () => {
    if (state.logTimer) clearInterval(state.logTimer);
    state.logTimer = setInterval(() => { if (document.visibilityState === 'visible') refresh(); }, 3000);
  };
  if (state.logAuto) startAuto();
  $('#log-clear', view).onclick = async () => {
    if (!await confirmDialog('Clear log?', 'Empty ' + state.logName + '? A backup copy is kept first.', 'Clear', true)) return;
    try { await postJson('/api/admin/logs/clear', { name: state.logName }); snack('Log cleared'); renderRoute(); }
    catch (error) { snack(error.message, true); }
  };
}


/* ------------------------------------------------------------------- map */
const platformMap = { lat: 52.3676, lng: 4.9041, zoom: 13, data: null, selected: null };

function mapProject(lat, lng, zoom) {
  const size = 256 * Math.pow(2, zoom);
  const sin = Math.sin(lat * Math.PI / 180);
  return {
    x: (lng + 180) / 360 * size,
    y: (0.5 - Math.log((1 + sin) / (1 - sin)) / (4 * Math.PI)) * size,
  };
}

function mapUnproject(x, y, zoom) {
  const size = 256 * Math.pow(2, zoom);
  const n = Math.PI - 2 * Math.PI * y / size;
  return {
    lat: 180 / Math.PI * Math.atan(0.5 * (Math.exp(n) - Math.exp(-n))),
    lng: x / size * 360 - 180,
  };
}

function drawPlatformMap() {
  const host = $('#world-map');
  if (!host || !platformMap.data) return;
  const width = host.clientWidth || 700;
  const height = host.clientHeight || 560;
  const centre = mapProject(platformMap.lat, platformMap.lng, platformMap.zoom);
  const left = centre.x - width / 2;
  const top = centre.y - height / 2;
  const count = Math.pow(2, platformMap.zoom);
  const tiles = $('#map-tile-layer', host);
  const markers = $('#map-marker-layer', host);
  tiles.innerHTML = '';
  for (let x = Math.floor(left / 256); x <= Math.floor((left + width) / 256); x++) {
    for (let y = Math.floor(top / 256); y <= Math.floor((top + height) / 256); y++) {
      if (y < 0 || y >= count) continue;
      const image = document.createElement('img');
      const wrapped = ((x % count) + count) % count;
      image.src = '/api/tiles/' + platformMap.zoom + '/' + wrapped + '/' + y + '.png';
      image.alt = '';
      image.style.left = (x * 256 - left) + 'px';
      image.style.top = (y * 256 - top) + 'px';
      tiles.appendChild(image);
    }
  }
  markers.innerHTML = '';
  (platformMap.data.points || []).forEach((point) => {
    const spot = mapProject(point.lat, point.lng, platformMap.zoom);
    const marker = document.createElement('div');
    marker.className = 'map-marker ' + point.kind;
    marker.style.left = (spot.x - left) + 'px';
    marker.style.top = (spot.y - top) + 'px';
    if (point.kind === 'player') {
      marker.innerHTML = '<span class="player-dot"></span>';
    } else {
      const glyph = point.kind === 'gym' ? 'G' : point.kind === 'stop' ? 'P'
        : point.kind === 'raid' ? 'R' : point.kind === 'spawn' ? '●'
        : point.kind === 'max' ? '⚡' : '★';
      marker.innerHTML = '<span class="map-pin"><span>' + glyph + '</span></span>';
    }
    marker.title = point.label || '';
    markers.appendChild(marker);
  });
  if (platformMap.selected) {
    const spot = mapProject(platformMap.selected.lat, platformMap.selected.lng, platformMap.zoom);
    const marker = document.createElement('div');
    marker.className = 'map-marker';
    marker.style.left = (spot.x - left) + 'px';
    marker.style.top = (spot.y - top) + 'px';
    marker.innerHTML = '<span class="map-pin" style="background:#1a73e8"><span>+</span></span>';
    markers.appendChild(marker);
  }
}

function bindPlatformMap(host) {
  if (!host) return;
  let drag = null;
  host.addEventListener('pointerdown', (event) => {
    if (event.target.closest('.map-controls')) return;
    drag = { x: event.clientX, y: event.clientY, lat: platformMap.lat, lng: platformMap.lng, moved: false };
    host.setPointerCapture(event.pointerId);
  });
  host.addEventListener('pointermove', (event) => {
    if (!drag) return;
    drag.moved = drag.moved || Math.abs(event.clientX - drag.x) + Math.abs(event.clientY - drag.y) > 5;
    const start = mapProject(drag.lat, drag.lng, platformMap.zoom);
    const next = mapUnproject(start.x - (event.clientX - drag.x), start.y - (event.clientY - drag.y), platformMap.zoom);
    platformMap.lat = next.lat;
    platformMap.lng = next.lng;
    drawPlatformMap();
  });
  host.addEventListener('pointerup', (event) => {
    if (!drag) return;
    const moved = drag.moved;
    drag = null;
    if (!moved) {
      const rect = host.getBoundingClientRect();
      const centre = mapProject(platformMap.lat, platformMap.lng, platformMap.zoom);
      platformMap.selected = mapUnproject(centre.x + event.clientX - rect.left - host.clientWidth / 2,
        centre.y + event.clientY - rect.top - host.clientHeight / 2, platformMap.zoom);
      const label = $('#map-selected');
      if (label) label.textContent = platformMap.selected.lat.toFixed(6) + ', ' + platformMap.selected.lng.toFixed(6);
      drawPlatformMap();
    }
  });
  host.addEventListener('pointercancel', () => { drag = null; });
  host.addEventListener('wheel', (event) => {
    event.preventDefault();
    platformMap.zoom = Math.max(3, Math.min(19, platformMap.zoom + (event.deltaY < 0 ? 1 : -1)));
    drawPlatformMap();
  }, { passive: false });
  $('#map-zoom-in', host).onclick = () => { platformMap.zoom = Math.min(19, platformMap.zoom + 1); drawPlatformMap(); };
  $('#map-zoom-out', host).onclick = () => { platformMap.zoom = Math.max(3, platformMap.zoom - 1); drawPlatformMap(); };
  $('#map-centre', host).onclick = () => {
    if (platformMap.selected) {
      platformMap.lat = platformMap.selected.lat;
      platformMap.lng = platformMap.selected.lng;
    }
    drawPlatformMap();
  };
}

async function renderMapPage() {
  const view = $('#view');
  const [data, status] = await Promise.all([
    api('/api/admin/map'),
    api('/api/admin/status').catch(() => null),
  ]);
  const centre = data.center || { lat: 52.3676, lng: 4.9041 };
  platformMap.lat = centre.lat;
  platformMap.lng = centre.lng;
  platformMap.zoom = 13;
  platformMap.selected = null;
  platformMap.data = {
    ...data,
    points: [
      ...(data.players || []).map((row) => ({ ...row, kind: 'player', label: row.name || row.player_id })),
      ...(data.forts || []).map((row) => ({ ...row, kind: Number(row.kind) === 0 ? 'gym' : 'stop', label: row.name || row.id })),
      ...(data.spawns || []).map((row) => ({ ...row, kind: 'spawn', label: 'Spawn #' + row.id })),
      ...(data.max_stations || []).map((row) => ({ ...row, kind: 'max', label: row.name || row.id })),
    ],
  };
  const db = (status && status.database) || {};
  const server = (status && status.server) || {};
  const counts = {
    forts: (data.forts || []).length,
    spawns: (data.spawns || []).length,
    players: (data.players || []).length,
    max: (data.max_stations || []).length,
  };
  view.innerHTML = `
    ${pageHead('Map &amp; spawns', 'The live world around the active server database, with community map tiles and the same map shell as the server admin.',
      `<button class="button text" id="map-refresh">${svgIcon('refresh')} Refresh</button>`)}
    <div class="map-grid">
      <section class="card map-card">
        <div class="world-map" id="world-map">
          <div class="map-tile-layer" id="map-tile-layer"></div>
          <div class="map-marker-layer" id="map-marker-layer"></div>
          <span class="map-credit">© OpenStreetMap contributors</span>
          <div class="map-controls">
            <button class="icon-button" id="map-zoom-in" title="Zoom in">${svgIcon('plus')}</button>
            <button class="icon-button" id="map-zoom-out" title="Zoom out">${svgIcon('minus')}</button>
            <button class="icon-button" id="map-centre" title="Centre on selected point">${svgIcon('pin')}</button>
          </div>
        </div>
        <div class="map-meta">
          <div><span class="label">Selected point</span><strong id="map-selected">click the map</strong></div>
          <div class="row"><button class="button text" id="map-refresh-bottom">${svgIcon('refresh')} Reload live layer</button></div>
        </div>
      </section>
      <aside class="card">
        <div class="card-header"><div class="grow"><h3>Live layer</h3><p>${esc(data.database || 'No active database')}</p></div>
          <span class="chip ${db.exists ? 'success' : 'warn'}">${db.exists ? 'database ready' : 'missing database'}</span></div>
        <div class="map-summary">
          <div class="map-stat"><span>Gyms &amp; stops</span><strong>${counts.forts}</strong></div>
          <div class="map-stat"><span>Active spawns</span><strong>${counts.spawns}</strong></div>
          <div class="map-stat"><span>Players</span><strong>${counts.players}</strong></div>
          <div class="map-stat"><span>Power Spots</span><strong>${counts.max}</strong></div>
        </div>
        <div class="divider"></div>
        <div class="kv">
          <span class="k">Workspace</span><span class="v mono">${esc((status && status.root) || '—')}</span>
          <span class="k">Server</span><span class="v">${server.running ? 'listening' : 'stopped'}${server.port ? ' · :' + esc(server.port) : ''}</span>
          <span class="k">Profile</span><span class="v mono">${esc((status && status.active_profile) || '—')}</span>
        </div>
        <div class="map-legend">
          <span><i class="legend-dot" style="background:#7d5260"></i>gym</span>
          <span><i class="legend-dot" style="background:#386a20"></i>stop</span>
          <span><i class="legend-dot" style="background:#6750a4"></i>spawn</span>
          <span><i class="legend-dot" style="background:#6d4c00"></i>Power Spot</span>
          <span><i class="legend-dot" style="background:#1a73e8"></i>player</span>
        </div>
        <div class="notice warn" style="margin-top:14px">${svgIcon('info')}<div class="notice-body">
          <strong>First launch</strong>The platform creates the configured database automatically. Start the server to stream live spawns, gyms and player locations.</div></div>
      </aside>
    </div>`;
  hydrateIcons(view);
  bindPlatformMap($('#world-map', view));
  $('#map-refresh', view).onclick = () => renderRoute();
  $('#map-refresh-bottom', view).onclick = () => renderRoute();
  requestAnimationFrame(drawPlatformMap);
}

/* ------------------------------------------------------ platform live systems */
function loLines(value) {
  return Object.entries(value || {}).map(([key, item]) => key + '=' + item).join('\n');
}

function loPairs(text, label = 'values') {
  const out = {};
  String(text || '').split(/\n+/).forEach((line) => {
    const clean = line.trim();
    if (!clean || clean.startsWith('#')) return;
    const at = clean.indexOf('=');
    if (at < 1) throw new Error('Use key=value lines in ' + label);
    const key = clean.slice(0, at).trim();
    const value = Number(clean.slice(at + 1).trim());
    if (!key || !Number.isFinite(value)) throw new Error('Invalid ' + label + ' line: ' + clean);
    out[key] = value;
  });
  return out;
}

function loSpeciesIds(text) {
  return String(text || '').split(/[,\s]+/).map((item) => Number(item)).filter((item) => Number.isInteger(item) && item > 0);
}

function loLocal(ms) {
  if (!ms) return '';
  const date = new Date(Number(ms));
  const pad = (value) => String(value).padStart(2, '0');
  return date.getFullYear() + '-' + pad(date.getMonth() + 1) + '-' + pad(date.getDate())
    + 'T' + pad(date.getHours()) + ':' + pad(date.getMinutes());
}

function loMs(value) {
  const stamp = value ? new Date(value).getTime() : 0;
  return Number.isFinite(stamp) ? stamp : 0;
}

function loTotal(settings) {
  return Object.values(settings.spawn_percent || {}).reduce((sum, value) => sum + Number(value || 0), 0);
}

function loPreview(settings) {
  const percent = settings.spawn_percent || {};
  const weights = settings.spawn_weights || {};
  const keys = Array.from(new Set([...Object.keys(percent), ...Object.keys(weights)]));
  if (!keys.length) return '<div class="muted small">Add species percentages or weights above to preview the spawn algorithm.</div>';
  return `<div class="table-wrap"><table class="data-table"><thead><tr><th>Species/form</th><th>Absolute share</th><th>Weight multiplier</th><th>Relative weight</th></tr></thead><tbody>${keys.map((key) => {
    const share = Number(percent[key] || 0);
    const weight = weights[key] === undefined ? 1 : Number(weights[key]);
    return `<tr><td class="mono">${esc(key)}</td><td>${share.toFixed(2)}%</td><td>${weight.toFixed(2)}×</td><td>${(share * weight).toFixed(2)}</td></tr>`;
  }).join('')}</tbody></table></div>`;
}

function loEventRows(settings, kind = '') {
  const rows = (settings.events || []).filter((entry) => !kind || entry.kind === kind);
  if (!rows.length) return '<div class="muted small">No events scheduled.</div>';
  return `<div class="timeline">${rows.map((entry) => `<div class="timeline-item">
    <span class="when">${esc(entry.kind)}</span><span class="grow">${esc(entry.name)}<br><small class="muted">${esc(entry.species.length ? 'species ' + entry.species.join(', ') : 'all species')} · ${esc(dateTime(entry.starts_ms))} → ${esc(dateTime(entry.ends_ms))}</small></span>
    <button class="button text small" data-lo-remove="${esc(entry.id)}">Remove</button></div>`).join('')}</div>`;
}

async function loSave(settings, message) {
  const saved = await postJson('/api/admin/liveops', { settings });
  state.platformStatus = null;
  snack(message || 'Live-ops saved; the signed room has been updated');
  return saved;
}

async function renderSpawnsPage() {
  const view = $('#view');
  const settings = await api('/api/admin/liveops');
  const total = loTotal(settings);
  view.innerHTML = `${pageHead('Spawns &amp; algorithm', 'Platform-owned wild spawn rules. The app reads these from the signed room over HiveMQ; no game server is required.',
    `<button class="button text" data-go="events">${svgIcon('calendar')} Events</button><button class="button" id="spawns-save">${svgIcon('save')} Save spawn rules</button>`)}
    <div class="grid two">
      <section class="card">
        <div class="card-header"><div class="grow"><h3>Wild spawn algorithm</h3><p>Explicit species shares plus per-species weight multipliers and an optional allow-list.</p></div>
          <span class="chip ${total > 100 ? 'warn' : 'success'}" id="spawn-total">${total.toFixed(1)}% of 100%</span></div>
        <div class="form-grid">
          <div class="field full"><label class="checkline"><input type="checkbox" id="spawn-enabled" ${settings.enabled ? 'checked' : ''}>
            <span class="check-text"><strong>Live-ops enabled</strong><small>Turn off to stop applying every platform live rule.</small></span></label></div>
          <div class="field full"><label>Species shares · <span class="inline-code">species=percent</span></label>
            <textarea class="input mono" id="spawn-percent" rows="9" spellcheck="false">${esc(loLines(settings.spawn_percent))}</textarea>
            <span class="field-help">Absolute share of the wild pool. Remaining percentage is shared by the server's normal pool. Forms use <span class="inline-code">25:0=12</span>.</span></div>
          <div class="field full"><label>Weight multipliers · <span class="inline-code">species=multiplier</span></label>
            <textarea class="input mono" id="spawn-weights" rows="6" spellcheck="false">${esc(loLines(settings.spawn_weights))}</textarea>
            <span class="field-help">Multiplies the normal pool weight for a species or form. Zero excludes it.</span></div>
          <div class="field full"><label>Allow-list · species ids separated by commas</label>
            <input class="input mono" id="spawn-allow" value="${esc((settings.spawn_allow || []).join(', '))}" placeholder="1, 4, 7">
            <span class="field-help">Empty means every species is eligible.</span></div>
        </div>
        <div class="row" style="margin-top:14px"><button class="button tonal" id="spawn-normalise">${svgIcon('tune')} Normalise shares to 100%</button>
          <button class="button" id="spawns-save-bottom">${svgIcon('save')} Save spawn rules</button></div>
      </section>
      <section class="card">
        <div class="card-header"><div class="grow"><h3>Algorithm preview</h3><p>Read-only projection of the saved values. The app applies the same percentages and multipliers.</p></div></div>
        <div id="spawn-preview">${loPreview(settings)}</div>
        <div class="notice info" style="margin-top:14px">${svgIcon('info')}<div class="notice-body"><strong>How it works</strong>Percentages pick the absolute share first; weights and the allow-list then shape the normal pool. Scheduled spotlight and Community Day rules override at runtime.</div></div>
        <div class="divider"></div>
        <div class="kv"><span class="k">Allow-list</span><span class="v">${(settings.spawn_allow || []).length ? esc(settings.spawn_allow.join(', ')) : 'all species'}</span>
          <span class="k">Spawn events</span><span class="v">${(settings.events || []).filter((entry) => entry.kind === 'spawn' || entry.kind === 'spotlight').length} scheduled</span></div>
      </section>
    </div>`;
  hydrateIcons(view); bindGo(view);
  const read = () => ({
    ...settings, enabled: $('#spawn-enabled', view).checked,
    spawn_percent: loPairs($('#spawn-percent', view).value, 'spawn shares'),
    spawn_weights: loPairs($('#spawn-weights', view).value, 'spawn weights'),
    spawn_allow: loSpeciesIds($('#spawn-allow', view).value),
  });
  const save = async () => {
    try { await loSave(read(), 'Spawn algorithm saved'); renderRoute(); } catch (error) { snack(error.message, true); }
  };
  $('#spawns-save', view).onclick = save;
  $('#spawns-save-bottom', view).onclick = save;
  $('#spawn-normalise', view).onclick = () => {
    try {
      const values = loPairs($('#spawn-percent', view).value, 'spawn shares');
      const sum = Object.values(values).reduce((total, value) => total + value, 0);
      if (!sum) return snack('Add at least one species share first', true);
      Object.keys(values).forEach((key) => { values[key] = Math.round(values[key] / sum * 1000) / 10; });
      $('#spawn-percent', view).value = loLines(values);
      $('#spawn-total', view).textContent = '100.0% of 100%';
      snack('Shares normalised in the editor; press Save to publish');
    } catch (error) { snack(error.message, true); }
  };
}

async function renderRaidsPage() {
  const view = $('#view');
  const settings = await api('/api/admin/liveops');
  const tiers = settings.raid_tier_weights || {};
  view.innerHTML = `${pageHead('Raids &amp; bosses', 'Raid tier shares, per-boss weight multipliers and scheduled raid takeovers. Published in the room document.',
    `<button class="button text" data-go="events">${svgIcon('calendar')} Raid events</button><button class="button" id="raids-save">${svgIcon('save')} Save raids</button>`)}
    <div class="grid two">
      <section class="card">
        <div class="card-header"><div class="grow"><h3>Raid pool weights</h3><p>Tier share first, then an optional per-boss multiplier. Zero removes a tier or boss.</p></div></div>
        <div class="form-grid">
          ${[1, 3, 5, 6].map((tier) => `<div class="field"><label>Tier ${tier}${tier === 6 ? ' · Mega' : ''}</label><input class="input" id="raid-tier-${tier}" type="number" min="0" step="any" value="${esc(tiers[String(tier)] ?? 0)}"></div>`).join('')}
          <div class="field full"><label>Boss multipliers · <span class="inline-code">species=multiplier</span></label>
            <textarea class="input mono" id="raid-boss-weights" rows="8" spellcheck="false">${esc(loLines(settings.raid_boss_weights))}</textarea>
            <span class="field-help">The final boss weight is the boss pool weight × tier share × this multiplier.</span></div>
        </div>
        <div class="row" style="margin-top:14px"><button class="button" id="raids-save-bottom">${svgIcon('save')} Save raids</button></div>
      </section>
      <section class="card">
        <div class="card-header"><div class="grow"><h3>Raid events</h3><p>Active raid takeovers override the tier shares above.</p></div><span class="chip outline">${(settings.events || []).filter((entry) => entry.kind === 'raid').length} scheduled</span></div>
        ${loEventRows(settings, 'raid')}
        <div class="notice info" style="margin-top:14px">${svgIcon('info')}<div class="notice-body"><strong>Raid tiers</strong>Use 1, 3, 5 and 6. The full boss list still comes from the game's raid pool; this platform only publishes the weights.</div></div>
      </section>
    </div>`;
  hydrateIcons(view); bindGo(view);
  const save = async () => {
    try {
      const raidTiers = {};
      [1, 3, 5, 6].forEach((tier) => { raidTiers[tier] = Number($('#raid-tier-' + tier, view).value) || 0; });
      await loSave({ ...settings, raid_tier_weights: raidTiers, raid_boss_weights: loPairs($('#raid-boss-weights', view).value, 'boss multipliers') }, 'Raid weights saved');
      renderRoute();
    } catch (error) { snack(error.message, true); }
  };
  $('#raids-save', view).onclick = save;
  $('#raids-save-bottom', view).onclick = save;
  $$('[data-lo-remove]', view).forEach((button) => { button.onclick = () => removeLoEvent(settings, button.dataset.loRemove); });
}

async function removeLoEvent(settings, id) {
  try {
    const events = (settings.events || []).filter((entry) => entry.id !== id);
    await loSave({ ...settings, events }, 'Event removed');
    renderRoute();
  } catch (error) { snack(error.message, true); }
}

async function renderEventsPage() {
  const view = $('#view');
  const settings = await api('/api/admin/liveops');
  view.innerHTML = `${pageHead('Events &amp; Community Days', 'Schedule shiny boosts, spotlight hours, raid takeovers and Community Days. Every entry is plain JSON in the signed room.',
    `<span class="chip outline">${(settings.events || []).length} events · ${(settings.community_days || []).length} Community Days</span>`)}
    <div class="grid two">
      <section class="card">
        <div class="card-header"><div class="grow"><h3>Schedule an event</h3><p>Events apply only inside their window.</p></div></div>
        <div class="form-grid">
          <div class="field full"><label>Name</label><input class="input" id="event-name" placeholder="Weekend spotlight"></div>
          <div class="field"><label>Kind</label><select class="select" id="event-kind"><option value="spotlight">Spotlight</option><option value="spawn">Spawn boost</option><option value="shiny">Shiny boost</option><option value="raid">Raid takeover</option></select></div>
          <div class="field"><label>Species ids</label><input class="input mono" id="event-species" placeholder="25, 133"></div>
          <div class="field"><label>Shiny rate · 1 in</label><input class="input" id="event-shiny" type="number" min="1" step="1" placeholder="512"></div>
          <div class="field"><label>Spawn multiplier</label><input class="input" id="event-multiplier" type="number" min="0" step="any" value="1"></div>
          <div class="field"><label>Starts</label><input class="input" id="event-starts" type="datetime-local"></div>
          <div class="field"><label>Ends</label><input class="input" id="event-ends" type="datetime-local"></div>
          <div class="field full"><label>Raid tier weights · JSON</label><input class="input mono" id="event-tiers" value="{}" placeholder='{"5": 1}'></div>
        </div>
        <div class="row" style="margin-top:14px"><button class="button" id="event-add">${svgIcon('plus')} Add event</button></div>
      </section>
      <section class="card">
        <div class="card-header"><div class="grow"><h3>Community Day</h3><p>One species, one shiny rate, one window.</p></div></div>
        <div class="form-grid">
          <div class="field full"><label>Name</label><input class="input" id="day-name" placeholder="Community Day"></div>
          <div class="field"><label>Species id</label><input class="input" id="day-species" type="number" min="1" placeholder="25"></div>
          <div class="field"><label>Form</label><input class="input" id="day-form" type="number" min="0" value="0"></div>
          <div class="field"><label>Shiny rate · 1 in</label><input class="input" id="day-shiny" type="number" min="1" value="25"></div>
          <div class="field"><label>Starts</label><input class="input" id="day-starts" type="datetime-local"></div>
          <div class="field"><label>Ends</label><input class="input" id="day-ends" type="datetime-local"></div>
        </div>
        <div class="row" style="margin-top:14px"><button class="button" id="day-add">${svgIcon('plus')} Add Community Day</button></div>
      </section>
    </div>
    <div class="grid two" style="margin-top:16px">
      <section class="card"><div class="card-header"><div class="grow"><h3>Scheduled events</h3><p>All event kinds.</p></div></div>${loEventRows(settings)}</section>
      <section class="card"><div class="card-header"><div class="grow"><h3>Community Days</h3><p>Active and upcoming.</p></div></div>
        ${(settings.community_days || []).length ? `<div class="timeline">${(settings.community_days || []).map((entry) => `<div class="timeline-item"><span class="when">#${esc(entry.species)}</span><span class="grow">${esc(entry.name)}<br><small class="muted">${esc(dateTime(entry.starts_ms))} → ${esc(dateTime(entry.ends_ms))} · 1 in ${Math.round(1 / entry.shiny_rate)} shiny</small></span><button class="button text small" data-lo-remove="${esc(entry.id)}">Remove</button></div>`).join('')}</div>` : '<div class="muted small">No Community Days scheduled.</div>'}
      </section>
    </div>`;
  hydrateIcons(view); bindGo(view);
  const addEvent = async () => {
    try {
      const starts = loMs($('#event-starts', view).value);
      const ends = loMs($('#event-ends', view).value);
      if (!starts || !ends || ends <= starts) throw new Error('Choose a valid event window');
      const rate = Number($('#event-shiny', view).value);
      let raidTiers = {};
      try { raidTiers = JSON.parse($('#event-tiers', view).value || '{}'); } catch (error) { throw new Error('Raid tier weights must be valid JSON'); }
      const entry = { id: 'event-' + Date.now(), name: $('#event-name', view).value.trim() || 'Event',
        kind: $('#event-kind', view).value, species: loSpeciesIds($('#event-species', view).value),
        shiny_rate: rate > 0 ? 1 / rate : null, spawn_multiplier: Number($('#event-multiplier', view).value) || 1,
        raid_tier_weights: raidTiers, starts_ms: starts, ends_ms: ends, enabled: true };
      await loSave({ ...settings, events: [...(settings.events || []), entry] }, 'Event scheduled');
      renderRoute();
    } catch (error) { snack(error.message, true); }
  };
  const addDay = async () => {
    try {
      const starts = loMs($('#day-starts', view).value);
      const ends = loMs($('#day-ends', view).value);
      if (!starts || !ends || ends <= starts) throw new Error('Choose a valid Community Day window');
      const species = Number($('#day-species', view).value);
      if (!species) throw new Error('Choose a Community Day species');
      const entry = { id: 'community-day-' + Date.now(), name: $('#day-name', view).value.trim() || 'Community Day',
        species, form: Number($('#day-form', view).value) || 0,
        shiny_rate: 1 / Math.max(1, Number($('#day-shiny', view).value) || 25),
        starts_ms: starts, ends_ms: ends, enabled: true, spawn_share: 1 };
      await loSave({ ...settings, community_days: [...(settings.community_days || []), entry] }, 'Community Day scheduled');
      renderRoute();
    } catch (error) { snack(error.message, true); }
  };
  $('#event-add', view).onclick = addEvent;
  $('#day-add', view).onclick = addDay;
  $$('[data-lo-remove]', view).forEach((button) => {
    button.onclick = async () => {
      const id = button.dataset.loRemove;
      try {
        await loSave({ ...settings, events: (settings.events || []).filter((entry) => entry.id !== id),
          community_days: (settings.community_days || []).filter((entry) => entry.id !== id) }, 'Entry removed');
        renderRoute();
      } catch (error) { snack(error.message, true); }
    };
  });
}

async function renderShinyPage() {
  const view = $('#view');
  const settings = await api('/api/admin/liveops');
  view.innerHTML = `${pageHead('Shiny &amp; Shadow', 'Per-species shiny overrides plus Shadow rescue rules. Leave a table empty to use the game defaults.',
    `<button class="button" id="shiny-save">${svgIcon('save')} Save shiny rules</button>`)}
    <div class="grid two">
      <section class="card"><div class="card-header"><div class="grow"><h3>Shiny rates</h3><p>Fractional rates: 0.002 is 1 in 500. Forms use <span class="inline-code">25:0</span>.</p></div></div>
        <div class="field"><label>Species rates · <span class="inline-code">species=rate</span></label><textarea class="input mono" id="shiny-rates" rows="12" spellcheck="false">${esc(loLines(settings.shiny_rates))}</textarea></div>
        <div class="notice info" style="margin-top:12px">${svgIcon('info')}<div class="notice-body"><strong>Highest wins</strong>Community Day then active event overrides then this table then the base game rate.</div></div>
      </section>
      <section class="card"><div class="card-header"><div class="grow"><h3>Shadow rescue overrides</h3><p>Optional dust and candy costs per species. Empty means normal rules.</p></div></div>
        <div class="field"><label>Shadow JSON</label><textarea class="input mono" id="shadow-overrides" rows="12" spellcheck="false">${esc(JSON.stringify(settings.shadow_overrides || {}, null, 2))}</textarea></div>
      </section>
    </div>`;
  hydrateIcons(view); bindGo(view);
  $('#shiny-save', view).onclick = async () => {
    try {
      let shadow = {};
      try { shadow = JSON.parse($('#shadow-overrides', view).value || '{}'); } catch (error) { throw new Error('Shadow overrides must be valid JSON'); }
      await loSave({ ...settings, shiny_rates: loPairs($('#shiny-rates', view).value, 'shiny rates'), shadow_overrides: shadow }, 'Shiny and Shadow rules saved');
      renderRoute();
    } catch (error) { snack(error.message, true); }
  };
}

async function renderWorldPage() {
  const view = $('#view');
  const [status, spawnData, raidData, poiData] = await Promise.all([
    api('/api/admin/world'), api('/api/admin/spawns'), api('/api/admin/raids'),
    api('/api/admin/pois?limit=5000').catch(() => ({ pois: [] })),
  ]);
  const spawnRules = status.settings.spawns;
  const raidRules = status.settings.raids;
  const pois = poiData.pois || [];
  const gyms = pois.filter((row) => Number(row.kind) === 0);
  const spawns = spawnData.spawns || [];
  const raids = raidData.raids || [];
  const poiOptions = (rows, label) => '<option value="">Random ' + label + '</option>'
    + rows.map((row) => `<option value="${esc(row.id)}">${esc(row.name || row.id)}</option>`).join('');
  const spawnRows = spawns.slice().reverse().map((row) => `<div class="row between" style="border-top:1px solid var(--m3-outline-variant);padding:8px 0">
    <span><strong>#${esc(row.species)}</strong> <small class="muted">L${esc(row.level)} · IV ${(row.ivs || []).join('/')} · ${row.manual ? 'manual' : 'auto'} · ${esc(dateTime(row.expires_ms))}</small></span>
    <button class="button text small danger" data-spawn-delete="${esc(row.id)}">Delete</button></div>`).join('');
  const raidRows = raids.slice().reverse().map((row) => `<div class="row between" style="border-top:1px solid var(--m3-outline-variant);padding:8px 0">
    <span><strong>T${esc(row.tier)} #${esc(row.species)}</strong> <small class="muted">${row.manual ? 'manual' : 'auto'} · ${esc(dateTime(row.starts_ms))} → ${esc(dateTime(row.expires_ms))}</small></span>
    <button class="button text small danger" data-raid-delete="${esc(row.id)}">Delete</button></div>`).join('');
  view.innerHTML = `${pageHead('World simulator', 'The platform builds its own live world from automatically downloaded POIs. No game server, workspace or opt-in is required.',
    `<button class="button text" id="world-refresh">${svgIcon('refresh')} Refresh</button><button class="button" id="world-save">${svgIcon('save')} Save algorithms</button>`)}
    <div class="grid four">
      <div class="card stat-card"><span class="stat-label">Active wild spawns</span><strong class="stat-value">${spawns.length}</strong><small class="muted">${spawnRules.enabled ? 'automatic' : 'paused'}</small></div>
      <div class="card stat-card"><span class="stat-label">Active raids</span><strong class="stat-value">${raids.length}</strong><small class="muted">${raidRules.enabled ? 'automatic' : 'paused'}</small></div>
      <div class="card stat-card"><span class="stat-label">Platform POIs</span><strong class="stat-value">${pois.length}</strong><small class="muted">${gyms.length} gyms</small></div>
      <div class="card stat-card"><span class="stat-label">Simulator</span><strong class="stat-value" style="font-size:20px">${status.running ? 'Running' : 'Stopped'}</strong><small class="muted">${status.last_error ? esc(status.last_error) : 'POIs and timers healthy'}</small></div>
    </div>
    <div class="grid two" style="margin-top:16px">
      <section class="card">
        <div class="card-header"><div class="grow"><h3>Automatic wild spawns</h3><p>Choose the algorithm and the limits. It runs on its own, using live-ops shares and weights when present.</p></div><span class="chip ${spawnRules.enabled ? 'success' : 'outline'}">${spawnRules.enabled ? 'on' : 'paused'}</span></div>
        <div class="form-grid">
          <div class="field full"><label class="checkline"><input type="checkbox" id="w-spawn-enabled" ${spawnRules.enabled ? 'checked' : ''}><span class="check-text"><strong>Run automatically</strong><small>Creates new wild spawns without an admin request.</small></span></label></div>
          <div class="field"><label>Algorithm</label><select class="select" id="w-spawn-algorithm">${['weighted', 'percent', 'hybrid', 'uniform'].map((value) => `<option value="${value}" ${spawnRules.algorithm === value ? 'selected' : ''}>${value}</option>`).join('')}</select></div>
          <div class="field"><label>Species pool</label><input class="input mono" id="w-spawn-species" value="${esc((spawnRules.species || []).join(', '))}" placeholder="1, 4, 7, 25"></div>
          <div class="field"><label>Every seconds</label><input class="input" id="w-spawn-interval" type="number" min="5" value="${esc(spawnRules.interval_s)}"></div>
          <div class="field"><label>Spawns per cycle</label><input class="input" id="w-spawn-per" type="number" min="1" value="${esc(spawnRules.per_cycle)}"></div>
          <div class="field"><label>Maximum active</label><input class="input" id="w-spawn-max" type="number" min="1" value="${esc(spawnRules.max_active)}"></div>
          <div class="field"><label>Lifetime · seconds</label><input class="input" id="w-spawn-life" type="number" min="60" value="${esc(spawnRules.lifetime_s)}"></div>
          <div class="field"><label>Spread from POI · metres</label><input class="input" id="w-spawn-radius" type="number" min="10" value="${esc(spawnRules.radius_m)}"></div>
          <div class="field"><label>Level range</label><div class="row"><input class="input" id="w-spawn-level-min" type="number" min="1" max="50" value="${esc(spawnRules.level_min)}"><input class="input" id="w-spawn-level-max" type="number" min="1" max="50" value="${esc(spawnRules.level_max)}"></div></div>
          <div class="field"><label>IV range</label><div class="row"><input class="input" id="w-spawn-iv-min" type="number" min="0" max="15" value="${esc(spawnRules.iv_min)}"><input class="input" id="w-spawn-iv-max" type="number" min="0" max="15" value="${esc(spawnRules.iv_max)}"></div></div>
        </div>
        <div class="row" style="margin-top:14px"><button class="button tonal" id="world-run">${svgIcon('play')} Generate now</button><button class="button text" id="world-clear-spawns">${svgIcon('trash')} Clear automatic spawns</button></div>
      </section>
      <section class="card">
        <div class="card-header"><div class="grow"><h3>Automatic raids</h3><p>Raids appear at gym POIs. Tier shares and boss multipliers from live-ops are applied on top of this pool.</p></div><span class="chip ${raidRules.enabled ? 'success' : 'outline'}">${raidRules.enabled ? 'on' : 'paused'}</span></div>
        <div class="form-grid">
          <div class="field full"><label class="checkline"><input type="checkbox" id="w-raid-enabled" ${raidRules.enabled ? 'checked' : ''}><span class="check-text"><strong>Run automatically</strong><small>Creates raids at available gyms without an admin request.</small></span></label></div>
          <div class="field"><label>Algorithm</label><select class="select" id="w-raid-algorithm">${['tier_weights', 'uniform'].map((value) => `<option value="${value}" ${raidRules.algorithm === value ? 'selected' : ''}>${value}</option>`).join('')}</select></div>
          <div class="field"><label>Every seconds</label><input class="input" id="w-raid-interval" type="number" min="10" value="${esc(raidRules.interval_s)}"></div>
          <div class="field"><label>Maximum active</label><input class="input" id="w-raid-max" type="number" min="1" value="${esc(raidRules.max_active)}"></div>
          <div class="field"><label>Raid lifetime · seconds</label><input class="input" id="w-raid-life" type="number" min="60" value="${esc(raidRules.lifetime_s)}"></div>
          <div class="field"><label>Egg time · seconds</label><input class="input" id="w-raid-egg" type="number" min="0" value="${esc(raidRules.egg_s)}"></div>
          <div class="field full"><label>Boss pool · JSON</label><textarea class="input mono" id="w-raid-bosses" rows="7" spellcheck="false">${esc(JSON.stringify(raidRules.bosses || [], null, 2))}</textarea><span class="field-help">Each row: species, tier (1/3/5/6), weight.</span></div>
        </div>
        <div class="row" style="margin-top:14px"><button class="button text" id="world-clear-raids">${svgIcon('trash')} Clear automatic raids</button></div>
      </section>
    </div>
    <div class="grid two" style="margin-top:16px">
      <section class="card">
        <div class="card-header"><div class="grow"><h3>Manual wild spawn</h3><p>Spawn a specific Pokémon now. Leave coordinates empty to place it near a POI.</p></div></div>
        <div class="form-grid">
          <div class="field"><label>Species id</label><input class="input" id="m-spawn-species" type="number" min="1" placeholder="25"></div>
          <div class="field"><label>Level</label><input class="input" id="m-spawn-level" type="number" min="1" max="50" placeholder="auto"></div>
          <div class="field full"><label>Near POI</label><select class="select" id="m-spawn-poi">${poiOptions(pois, 'POI')}</select></div>
          <div class="field"><label>Latitude · optional</label><input class="input" id="m-spawn-lat" type="number" step="any" placeholder="52.3676"></div>
          <div class="field"><label>Longitude · optional</label><input class="input" id="m-spawn-lng" type="number" step="any" placeholder="4.9041"></div>
        </div>
        <div class="row" style="margin-top:14px"><button class="button" id="m-spawn-add">${svgIcon('plus')} Spawn Pokémon</button></div>
        <div class="divider"></div><div class="card-header"><div class="grow"><h3>Live wild spawns</h3><p>Newest first.</p></div><span class="chip outline">${spawns.length}</span></div>
        ${spawnRows || '<div class="muted small">No active spawns yet. The simulator will fill this list after the first POIs arrive.</div>'}
      </section>
      <section class="card">
        <div class="card-header"><div class="grow"><h3>Manual raid</h3><p>Create a raid at a gym immediately, or let the platform pick one.</p></div></div>
        <div class="form-grid">
          <div class="field"><label>Boss species id</label><input class="input" id="m-raid-species" type="number" min="1" placeholder="150"></div>
          <div class="field"><label>Tier</label><select class="select" id="m-raid-tier"><option value="1">1</option><option value="3">3</option><option value="5" selected>5</option><option value="6">6 · Mega</option></select></div>
          <div class="field full"><label>Gym</label><select class="select" id="m-raid-poi">${poiOptions(gyms, 'gym')}</select></div>
          <div class="field"><label>Lifetime · seconds</label><input class="input" id="m-raid-life" type="number" min="60" placeholder="3600"></div>
        </div>
        <div class="row" style="margin-top:14px"><button class="button" id="m-raid-add">${svgIcon('plus')} Create raid</button></div>
        <div class="divider"></div><div class="card-header"><div class="grow"><h3>Live raids</h3><p>Newest first.</p></div><span class="chip outline">${raids.length}</span></div>
        ${raidRows || '<div class="muted small">No active raids yet. Add a gym or wait for an automatic raid.</div>'}
      </section>
    </div>
    <div class="notice info" style="margin-top:16px">${svgIcon('info')}<div class="notice-body"><strong>Automatic, not opt-in</strong>POI import starts with the platform and has a default centre. Change it under Platform &amp; rooms; manual POIs always survive imports. Spawns and raids are published in the signed room document immediately.</div></div>`;
  hydrateIcons(view);
  const number = (id, fallback) => {
    const value = Number($(id, view).value);
    return Number.isFinite(value) && value !== 0 ? value : fallback;
  };
  const rules = () => ({
    spawns: {
      enabled: $('#w-spawn-enabled', view).checked, algorithm: $('#w-spawn-algorithm', view).value,
      species: loSpeciesIds($('#w-spawn-species', view).value),
      interval_s: number('#w-spawn-interval', spawnRules.interval_s), per_cycle: number('#w-spawn-per', spawnRules.per_cycle),
      max_active: number('#w-spawn-max', spawnRules.max_active), lifetime_s: number('#w-spawn-life', spawnRules.lifetime_s),
      radius_m: number('#w-spawn-radius', spawnRules.radius_m), level_min: number('#w-spawn-level-min', spawnRules.level_min),
      level_max: number('#w-spawn-level-max', spawnRules.level_max), iv_min: number('#w-spawn-iv-min', spawnRules.iv_min),
      iv_max: number('#w-spawn-iv-max', spawnRules.iv_max),
    },
    raids: {
      enabled: $('#w-raid-enabled', view).checked, algorithm: $('#w-raid-algorithm', view).value,
      interval_s: number('#w-raid-interval', raidRules.interval_s), max_active: number('#w-raid-max', raidRules.max_active),
      lifetime_s: number('#w-raid-life', raidRules.lifetime_s), egg_s: number('#w-raid-egg', 0),
      bosses: JSON.parse($('#w-raid-bosses', view).value || '[]'),
    },
  });
  const save = async () => {
    try { await postJson('/api/admin/world', rules()); snack('World algorithms saved'); renderRoute(); }
    catch (error) { snack(error.message, true); }
  };
  $('#world-save', view).onclick = save;
  $('#world-refresh', view).onclick = () => renderRoute();
  $('#world-run', view).onclick = async () => {
    try { await save(); await postJson('/api/admin/world/tick'); renderRoute(); } catch (error) { snack(error.message, true); }
  };
  const clear = async (path, title, message) => {
    if (!await confirmDialog(title, message, 'Clear', true)) return;
    try { await postJson(path, { auto_only: true }); snack('Cleared'); renderRoute(); } catch (error) { snack(error.message, true); }
  };
  $('#world-clear-spawns', view).onclick = () => clear('/api/admin/spawns/clear', 'Clear automatic spawns?', 'Manual spawns are kept.');
  $('#world-clear-raids', view).onclick = () => clear('/api/admin/raids/clear', 'Clear automatic raids?', 'Manual raids are kept.');
  $('#m-spawn-add', view).onclick = async () => {
    try {
      const species = Number($('#m-spawn-species', view).value);
      if (!species) throw new Error('Enter a Pokémon species id');
      const lat = $('#m-spawn-lat', view).value.trim();
      const lng = $('#m-spawn-lng', view).value.trim();
      if ((lat && !lng) || (!lat && lng)) throw new Error('Enter both latitude and longitude, or neither');
      await postJson('/api/admin/spawns', { species, level: Number($('#m-spawn-level', view).value) || undefined,
        poi_id: $('#m-spawn-poi', view).value || undefined, lat: lat ? Number(lat) : undefined, lng: lng ? Number(lng) : undefined });
      snack('Pokémon spawned'); renderRoute();
    } catch (error) { snack(error.message, true); }
  };
  $('#m-raid-add', view).onclick = async () => {
    try {
      const species = Number($('#m-raid-species', view).value);
      if (!species) throw new Error('Enter a raid boss species id');
      await postJson('/api/admin/raids', { species, tier: Number($('#m-raid-tier', view).value),
        poi_id: $('#m-raid-poi', view).value || undefined,
        lifetime_s: Number($('#m-raid-life', view).value) || undefined });
      snack('Raid created'); renderRoute();
    } catch (error) { snack(error.message, true); }
  };
  $$('[data-spawn-delete]', view).forEach((button) => { button.onclick = async () => {
    try { await postJson('/api/admin/spawns/delete', { id: button.dataset.spawnDelete }); snack('Spawn removed'); renderRoute(); } catch (error) { snack(error.message, true); }
  }; });
  $$('[data-raid-delete]', view).forEach((button) => { button.onclick = async () => {
    try { await postJson('/api/admin/raids/delete', { id: button.dataset.raidDelete }); snack('Raid removed'); renderRoute(); } catch (error) { snack(error.message, true); }
  }; });
}

async function renderPlatformPoiMapPage() {
  const view = $('#view');
  const [data, importer, spawnData, raidData] = await Promise.all([
    api('/api/admin/pois?limit=5000'), api('/api/poi-import').catch(() => null),
    api('/api/admin/spawns').catch(() => ({ spawns: [] })),
    api('/api/admin/raids').catch(() => ({ raids: [] })),
  ]);
  const rows = data.pois || [];
  const spawns = spawnData.spawns || [];
  const raids = raidData.raids || [];
  const centre = (importer && importer.center) || (rows[0] ? [rows[0].lat, rows[0].lng] : null) || [52.3676, 4.9041];
  platformMap.lat = Number(centre[0]); platformMap.lng = Number(centre[1]);
  platformMap.zoom = 14; platformMap.selected = null;
  platformMap.data = { points: [
    ...rows.map((row) => ({ ...row, kind: Number(row.kind) === 0 ? 'gym' : 'stop', label: row.name || row.id })),
    ...spawns.map((row) => ({ ...row, kind: 'spawn', label: 'Spawn #' + row.species })),
    ...raids.map((row) => ({ ...row, kind: 'raid', label: 'T' + row.tier + ' raid #' + row.species })),
  ] };
  const manual = rows.filter((row) => !row.auto_osm);
  view.innerHTML = `${pageHead('POI map &amp; spawn points', 'Platform-owned gyms and Pokéstops. Click the map to choose a location, then add or edit a place. These rows are served by the platform and shown on the signed room map.',
    `<button class="button text" data-go="platform">${svgIcon('tune')} Import settings</button><button class="button text" id="poi-refresh">${svgIcon('refresh')} Refresh</button>`)}
    <div class="map-grid">
      <section class="card map-card">
        <div class="world-map" id="world-map"><div class="map-tile-layer" id="map-tile-layer"></div><div class="map-marker-layer" id="map-marker-layer"></div><span class="map-credit">© OpenStreetMap contributors</span>
          <div class="map-controls"><button class="icon-button" id="map-zoom-in" title="Zoom in">${svgIcon('plus')}</button><button class="icon-button" id="map-zoom-out" title="Zoom out">${svgIcon('minus')}</button><button class="icon-button" id="map-centre" title="Centre">${svgIcon('pin')}</button></div></div>
        <div class="map-meta"><div><span class="label">Selected point</span><strong id="map-selected">click the map</strong></div><span class="chip outline">${rows.length} POIs · ${spawns.length} spawns · ${raids.length} raids</span></div>
      </section>
      <aside class="card">
        <div class="card-header"><div class="grow"><h3>Place a POI</h3><p>Click the map first. Manual rows survive scans.</p></div></div>
        <div class="form-grid"><div class="field full"><label>Name</label><input class="input" id="poi-name" maxlength="80" placeholder="Harbour Park"></div>
          <div class="field full"><label>Kind</label><select class="select" id="poi-kind"><option value="1">Pokéstop</option><option value="0">Gym</option></select></div></div>
        <div class="row" style="margin-top:14px"><button class="button" id="poi-add">${svgIcon('plus')} Add POI</button><button class="button text" id="poi-clear">${svgIcon('trash')} Clear generated</button></div>
        <div class="divider"></div>
        <div class="kv"><span class="k">Gyms</span><span class="v">${rows.filter((row) => Number(row.kind) === 0).length}</span><span class="k">Pokéstops</span><span class="v">${rows.filter((row) => Number(row.kind) === 1).length}</span><span class="k">Manual</span><span class="v">${manual.length}</span><span class="k">Imported</span><span class="v">${rows.length - manual.length}</span></div>
        <div class="map-legend" style="margin-top:12px"><span><i class="legend-dot" style="background:#7d5260"></i>gym</span><span><i class="legend-dot" style="background:#386a20"></i>stop</span><span><i class="legend-dot" style="background:#6750a4"></i>spawn</span><span><i class="legend-dot" style="background:#9c2b2b"></i>raid</span></div>
        <div class="notice info" style="margin-top:14px">${svgIcon('info')}<div class="notice-body"><strong>Platform-owned</strong>No server database is involved. Spawns and raids shown here are generated or placed by the platform and published through the room.</div></div>
      </aside>
    </div>
    <section class="card" style="margin-top:16px"><div class="card-header"><div class="grow"><h3>Manual POIs</h3><p>Hand-placed rows are never removed by an OSM scan.</p></div></div>
      ${manual.length ? `<div class="file-list">${manual.map((row) => `<div class="row between" style="border-top:1px solid var(--m3-outline-variant);padding:8px 0"><span>${esc(row.name)} <small class="muted">${esc(row.id)}</small></span><span class="chip">${Number(row.kind) === 0 ? 'gym' : 'stop'}</span><button class="button text small danger" data-poi-delete="${esc(row.id)}">Delete</button></div>`).join('')}</div>` : '<div class="muted small">No manual POIs yet.</div>'}
    </section>`;
  hydrateIcons(view); bindGo(view); bindPlatformMap($('#world-map', view));
  $('#poi-refresh', view).onclick = () => renderRoute();
  $('#poi-add', view).onclick = async () => {
    try {
      if (!platformMap.selected) throw new Error('Click the map to choose a location first');
      const name = $('#poi-name', view).value.trim();
      if (!name) throw new Error('Give the place a name');
      await postJson('/api/admin/pois', { name, lat: platformMap.selected.lat, lng: platformMap.selected.lng, kind: Number($('#poi-kind', view).value) });
      snack('POI added to the platform');
      renderRoute();
    } catch (error) { snack(error.message, true); }
  };
  $('#poi-clear', view).onclick = async () => {
    if (!await confirmDialog('Clear generated POIs?', 'Imported OSM rows will be removed. Manual POIs are kept.', 'Clear', true)) return;
    try { await postJson('/api/admin/pois/clear', { auto_only: true }); snack('Generated POIs cleared'); renderRoute(); } catch (error) { snack(error.message, true); }
  };
  $$('[data-poi-delete]', view).forEach((button) => { button.onclick = async () => {
    try { await postJson('/api/admin/pois/delete', { id: button.dataset.poiDelete }); snack('POI deleted'); renderRoute(); } catch (error) { snack(error.message, true); }
  }; });
  requestAnimationFrame(drawPlatformMap);
}

async function renderRoomPayloadPage() {
  const view = $('#view');
  const [liveops, platform, rooms, pois, world, spawnData, raidData] = await Promise.all([
    api('/api/admin/liveops'), api('/api/status'), api('/api/admin/rooms'),
    api('/api/admin/pois?limit=5000').catch(() => ({ pois: [] })),
    api('/api/admin/world').catch(() => null), api('/api/admin/spawns').catch(() => ({ spawns: [] })),
    api('/api/admin/raids').catch(() => ({ raids: [] })),
  ]);
  const payload = { liveops, platform: platform && platform.platform, defaults: (platform && platform.defaults) || {},
    hosted: (rooms && rooms.hosted) || '', rooms: (rooms && rooms.rooms) || [], pois: (pois.pois || []).length,
    world: world && world.settings, spawns: spawnData.spawns || [], raids: raidData.raids || [] };
  view.innerHTML = `${pageHead('Room payload', 'Everything the platform publishes in the signed room document. This is the data clients receive over HiveMQ.',
    `<button class="button text" id="payload-copy">${svgIcon('copy')} Copy JSON</button>`)}
    <section class="card"><div class="card-header"><div class="grow"><h3>Live payload</h3><p>Read-only snapshot. Use the system pages to edit.</p></div><span class="chip outline">${(payload.rooms || []).length} rooms</span></div>
      <pre class="code" id="payload-json" style="max-height:600px;overflow:auto;white-space:pre-wrap">${esc(JSON.stringify(payload, null, 2))}</pre></section>`;
  hydrateIcons(view); bindGo(view);
  $('#payload-copy', view).onclick = () => copyText(JSON.stringify(payload, null, 2));
}

Object.assign(window.PAGES, {
  world: renderWorldPage,
  map: renderPlatformPoiMapPage,
  spawns: renderSpawnsPage,
  raids: renderRaidsPage,
  events: renderEventsPage,
  shiny: renderShinyPage,
  payload: renderRoomPayloadPage,
  files: renderFilesPage,
  database: renderDatabasePage,
  players: renderPlayersPage,
  gamemaster: renderGameMasterPage,
  loot: renderLootPage,
  platform: renderPlatformPage,
  logs: renderLogsPage,
});
