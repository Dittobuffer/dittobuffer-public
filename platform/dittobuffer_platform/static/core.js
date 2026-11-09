'use strict';

/* ------------------------------------------------------------------ helpers */
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]
));
const clone = (value) => (typeof structuredClone === 'function'
  ? structuredClone(value) : JSON.parse(JSON.stringify(value)));
const isObject = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
const kindOf = (value) => (Array.isArray(value) ? 'array' : value === null ? 'null' : typeof value);

function fmtBytes(value) {
  const number = Number(value) || 0;
  if (number < 1024) return number + ' B';
  const units = ['KB', 'MB', 'GB', 'TB'];
  let size = number;
  for (const unit of units) {
    size /= 1024;
    if (size < 1024 || unit === 'TB') return size.toFixed(size < 10 ? 1 : 0) + ' ' + unit;
  }
  return number + ' B';
}

function ago(ms) {
  if (!ms) return 'never';
  const seconds = Math.round((Date.now() - Number(ms)) / 1000);
  if (seconds < 5) return 'just now';
  if (seconds < 60) return seconds + ' s ago';
  if (seconds < 3600) return Math.round(seconds / 60) + ' min ago';
  if (seconds < 86400) return Math.round(seconds / 3600) + ' h ago';
  return Math.round(seconds / 86400) + ' d ago';
}

function dateTime(ms) {
  if (!ms) return '—';
  try { return new Date(Number(ms)).toLocaleString(); } catch (error) { return '—'; }
}

const ICONS = {
  overview: '<rect x="3" y="3" width="7" height="7" rx="1.6"/><rect x="14" y="3" width="7" height="7" rx="1.6"/><rect x="3" y="14" width="7" height="7" rx="1.6"/><rect x="14" y="14" width="7" height="7" rx="1.6"/>',
  server: '<rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="13" width="18" height="7" rx="2"/><path d="M7 7.5h.01M7 16.5h.01"/>',
  tune: '<path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h9M17 18h3"/><circle cx="15" cy="6" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="15" cy="18" r="2"/>',
  sparkles: '<path d="M12 3l1.7 4.3L18 9l-4.3 1.7L12 15l-1.7-4.3L6 9l4.3-1.7L12 3z"/><path d="M18.5 15l.8 2.2L21.5 18l-2.2.8-.8 2.2-.8-2.2L15.5 18l2.2-.8.8-2.2z"/>',
  swords: '<path d="M14.5 3.5L21 3l-.5 6.5-8.2 8.2"/><path d="M3 3l8.3 8.3"/><path d="M3 21l4-4"/><path d="M14 14l4 4"/><path d="M3 3l4 4"/><path d="M10 16l-2 2"/>',
  calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 10h18"/><path d="M8 14h.01M12 14h.01M16 14h.01M8 18h.01M12 18h.01"/>',
  flask: '<path d="M9 3h6M10 3v5L4.5 18a2 2 0 001.8 3h11.4a2 2 0 001.8-3L14 8V3"/><path d="M7 15h10"/>',
  rocket: '<path d="M5 15c-1.5 1.5-2 6-2 6s4.5-.5 6-2"/><path d="M9 15l-3-3c1-4 4-8 9-9 1-1 4-1 6-1 0 2 0 5-1 6-1 5-5 8-9 9l-2-2z"/><circle cx="15" cy="9" r="1.6"/>',
  trophy: '<path d="M8 4h8v5a4 4 0 01-8 0V4z"/><path d="M8 5H5a3 3 0 003 3M16 5h3a3 3 0 01-3 3"/><path d="M12 13v4M8 21h8M10 17h4"/>',
  bolt: '<path d="M13 2L4 14h7l-1 8 9-12h-7l1-8z"/>',
  gift: '<rect x="3" y="8" width="18" height="4" rx="1"/><path d="M5 12v8a1 1 0 001 1h12a1 1 0 001-1v-8"/><path d="M10 8V5a2 2 0 014 0v3M8 8h8"/><path d="M12 8v13"/>',
  globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.6 3 2.6 15 0 18M12 3c-2.6 3-2.6 15 0 18"/>',
  users: '<circle cx="9" cy="8" r="3.2"/><path d="M2.5 20a6.5 6.5 0 0113 0"/><path d="M16 5.2a3.2 3.2 0 010 5.6M18.5 20a6.5 6.5 0 00-2-4.6"/>',
  folder: '<path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V7z"/>',
  database: '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/><path d="M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/>',
  layers: '<path d="M12 3l9 5-9 5-9-5 9-5z"/><path d="M3 12l9 5 9-5M3 16l9 5 9-5"/>',
  cloud: '<path d="M7 18a4 4 0 010-8 6 6 0 0111.5-1.5A4.5 4.5 0 0118 18H7z"/>',
  terminal: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 9l3 3-3 3M13 15h4"/>',
  palette: '<path d="M12 3a9 9 0 100 18c1.2 0 2-.9 2-2 0-.6-.3-1-.6-1.5-.2-.4-.4-.8-.4-1.4 0-1.1.9-2 2-2h2c2 0 3-1.3 3-3 0-4.4-3.6-8-8-8z"/><circle cx="7.5" cy="11" r="1"/><circle cx="10" cy="7" r="1"/><circle cx="15" cy="7" r="1"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  refresh: '<path d="M20 11a8 8 0 10-2.5 6"/><path d="M20 4v7h-7"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>',
  save: '<path d="M4 4h12l4 4v12H4V4z"/><path d="M8 4v6h8V4M8 20v-6h8v6"/>',
  undo: '<path d="M9 7L4 12l5 5"/><path d="M4 12h10a6 6 0 010 12"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  minus: '<path d="M5 12h14"/>',
  trash: '<path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14"/><path d="M10 10v6M14 10v6"/>',
  edit: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 013 3L7 19l-4 1 1-4 12.5-12.5z"/>',
  chevron: '<path d="M9 6l6 6-6 6"/>',
  check: '<path d="M20 6L9 17l-5-5"/>',
  warning: '<path d="M12 3L2 21h20L12 3z"/><path d="M12 9v5M12 18h.01"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
  close: '<path d="M6 6l12 12M18 6L6 18"/>',
  play: '<path d="M7 4l13 8-13 8V4z"/>',
  stop: '<rect x="5" y="5" width="14" height="14" rx="2"/>',
  restart: '<path d="M20 11a8 8 0 10-2.5 6"/><path d="M20 4v7h-7"/><path d="M12 8v4l2.5 2.5"/>',
  external: '<path d="M14 4h6v6M20 4l-9 9"/><path d="M19 14v5a1 1 0 01-1 1H5a1 1 0 01-1-1V6a1 1 0 011-1h5"/>',
  copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15H4a1 1 0 01-1-1V4a1 1 0 011-1h10a1 1 0 011 1v1"/>',
  hash: '<path d="M4 9h16M4 15h16M10 3L8 21M16 3l-2 18"/>',
  lock: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 018 0v3"/>',
  eye: '<path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  up: '<path d="M12 19V5M5 12l7-7 7 7"/>',
  down: '<path d="M12 5v14M5 12l7 7 7-7"/>',
  table: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M9 10v10M15 10v10"/>',
  code: '<path d="M8 8l-4 4 4 4M16 8l4 4-4 4M14 4l-4 16"/>',
  box: '<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9L12 3z"/><path d="M4 7.5l8 4.5 8-4.5M12 12v9"/>',
  pin: '<path d="M12 21s7-6.1 7-11a7 7 0 10-14 0c0 4.9 7 11 7 11z"/><circle cx="12" cy="10" r="2.5"/>',
  shield: '<path d="M12 3l8 3v6c0 5-3.4 7.8-8 9-4.6-1.2-8-4-8-9V6l8-3z"/><path d="M9 12l2 2 4-4"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  key: '<circle cx="8" cy="15" r="4"/><path d="M11 12l9-9M17 6l2 2M14 9l2 2"/>',
  file: '<path d="M6 3h8l4 4v14H6V3z"/><path d="M14 3v5h5"/>',
  sliders: '<path d="M4 7h10M18 7h2M4 17h2M10 17h10"/><circle cx="16" cy="7" r="2"/><circle cx="8" cy="17" r="2"/>',
  power: '<path d="M12 3v9"/><path d="M6.5 6.5a8 8 0 1011 0"/>',
  arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
};

function svgIcon(name) {
  return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" '
    + 'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    + (ICONS[name] || ICONS.info) + '</svg>';
}

function hydrateIcons(root = document) {
  $$('[data-icon]', root).forEach((node) => {
    const name = node.dataset.icon;
    if (name && !node.querySelector('svg')) node.innerHTML = svgIcon(name);
  });
}

/* -------------------------------------------------------------------- state */
const state = {
  route: 'overview', status: null, statusAt: 0, platformStatus: null,
  configName: '', configDraft: null, configDirty: false, configSearch: '',
  filePath: '', fileDoc: null, fileDraft: null, fileDirty: false,
  docs: null, docsAt: 0,
  dbPath: '', dbTables: null, dbTable: '', dbSchema: null, dbRows: null,
  dbOffset: 0, dbQuery: '', dbTab: 'rows',
  logs: null, logName: '', logText: '', logAuto: true,
  pageTimer: null, logTimer: null,
};

window.PAGES = window.PAGES || {};

const NAV = [
  { group: 'Platform', items: [
    { id: 'platform', label: 'Platform & rooms', icon: 'cloud' },
    { id: 'world', label: 'World simulator', icon: 'rocket' },
    { id: 'map', label: 'POI map', icon: 'pin' },
    { id: 'payload', label: 'Room payload', icon: 'code' },
  ] },
  { group: 'Live systems', items: [
    { id: 'spawns', label: 'Spawns & algorithm', icon: 'sparkles' },
    { id: 'raids', label: 'Raids & bosses', icon: 'swords' },
    { id: 'events', label: 'Events & Community Days', icon: 'calendar' },
    { id: 'shiny', label: 'Shiny & Shadow', icon: 'bolt' },
  ] },
];

/* -------------------------------------------------------------------- theme */
const THEME_KEY = 'dittobuffer-admin-theme';
const SEEDS = [['violet', '#6750A4'], ['green', '#3F6B3A'], ['orange', '#9A4600'],
  ['blue', '#2F5DA8'], ['rose', '#9C4059']];

function readTheme() {
  try {
    const value = JSON.parse(localStorage.getItem(THEME_KEY) || '{}');
    return { mode: value.mode || '', seed: value.seed || '', ...value };
  } catch (error) { return { mode: '', seed: '' }; }
}

function applyTheme(mode, seed, remember = false) {
  const root = document.documentElement;
  root.dataset.theme = mode || (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  root.dataset.seed = seed || 'violet';
  if (remember) {
    try { localStorage.setItem(THEME_KEY, JSON.stringify({ mode: root.dataset.theme, seed: root.dataset.seed })); } catch (error) { /* private mode */ }
  }
}

(function initialTheme() {
  const saved = readTheme();
  applyTheme(saved.mode, saved.seed, false);
})();

function openThemeDialog() {
  const currentMode = document.documentElement.dataset.theme || 'light';
  const currentSeed = document.documentElement.dataset.seed || 'violet';
  const dialog = dialogBase(`
    <div class="dialog-head">
      <div class="grow"><h2>Appearance</h2><p>Light, dark and accent color for this browser.</p></div>
      <button class="button text icon-only" data-close aria-label="Close">${svgIcon('close')}</button>
    </div>
    <div class="dialog-body">
      <div class="section-label">Tone</div>
      <div class="segmented" id="mode-choice" style="margin-top:8px">
        <button data-mode="light" class="${currentMode === 'light' ? 'active' : ''}">Light</button>
        <button data-mode="dark" class="${currentMode === 'dark' ? 'active' : ''}">Dark</button>
      </div>
      <div class="section-label" style="margin-top:22px">Accent seed</div>
      <div class="quick-grid" id="seed-choice" style="margin-top:8px">
        ${SEEDS.map(([name, colour]) => `<button class="quick-action" data-seed="${name}" style="border-color:${name === currentSeed ? colour : 'var(--m3-outline-variant)'}">
          <span style="width:20px;height:20px;border-radius:50%;background:${colour};display:inline-block"></span>
          <span style="flex:1;text-transform:capitalize">${name}</span>
          ${name === currentSeed ? `<span class="chip success">${svgIcon('check')}</span>` : ''}
        </button>`).join('')}
      </div>
      <p class="muted small" style="margin-top:18px">Stored only in this browser. The default follows your operating system.</p>
    </div>
    <div class="dialog-actions"><button class="button tonal" id="theme-install">${svgIcon('box')} Install app</button><button class="button" data-close>Done</button></div>`);
  const install = $('#theme-install', dialog);
  if (install) install.onclick = () => { dialog.close(); promptInstall(); };
  $$('[data-mode]', dialog).forEach((button) => {
    button.onclick = () => {
      $$('[data-mode]', dialog).forEach((item) => item.classList.toggle('active', item === button));
      applyTheme(button.dataset.mode, document.documentElement.dataset.seed, true);
    };
  });
  $$('[data-seed]', dialog).forEach((button) => {
    button.onclick = () => {
      applyTheme(document.documentElement.dataset.theme, button.dataset.seed, true);
      dialog.close();
      openThemeDialog();
    };
  });
}

/* --------------------------------------------------------------------- api */
function adminToken() {
  try { return localStorage.getItem('dittobuffer-platform-token') || ''; } catch (error) { return ''; }
}

async function api(path, options = {}) {
  const { silentAuth = false, ...rest } = options;
  const opts = { ...rest, headers: { ...(options.headers || {}) } };
  if (opts.body !== undefined && typeof opts.body !== 'string') opts.body = JSON.stringify(opts.body);
  if (opts.body !== undefined && !opts.headers['Content-Type']) opts.headers['Content-Type'] = 'application/json';
  const token = adminToken();
  if (token) opts.headers['X-Platform-Token'] = token;
  let response;
  try {
    response = await fetch(path, opts);
  } catch (error) {
    throw new Error('Cannot reach the platform API. Is the platform still running?');
  }
  if (response.status === 401 && !silentAuth) {
    const supplied = await promptDialog({
      title: 'Platform token required',
      label: 'Admin token',
      type: 'password',
      help: 'This is the admin_token in the platform configuration. It is stored in this browser only.',
      confirm: 'Unlock',
    });
    if (supplied) {
      try { localStorage.setItem('dittobuffer-platform-token', supplied); } catch (error) { /* ignore */ }
      opts.headers['X-Platform-Token'] = supplied;
      response = await fetch(path, opts);
    }
  }
  const text = await response.text();
  let data = {};
  if (text) {
    try { data = JSON.parse(text); } catch (error) { data = { raw: text }; }
  }
  if (!response.ok) throw new Error(data.error || response.statusText || ('HTTP ' + response.status));
  return data;
}

const getJson = (path) => api(path);
const postJson = (path, body = {}) => api(path, { method: 'POST', body });

async function getStatus(force = false, silentAuth = false) {
  if (!force && state.status && Date.now() - state.statusAt < 3000) return state.status;
  const status = await api('/api/admin/status', { silentAuth });
  state.status = status;
  state.statusAt = Date.now();
  applyStatus(status);
  return status;
}

function applyStatus(status) {
  const server = status.server || {};
  const chip = $('#server-chip');
  if (chip) {
    chip.classList.toggle('up', !!server.running);
    chip.classList.toggle('down', status.available && !server.running);
    const label = chip.querySelector('span:last-child');
    if (label) label.textContent = server.running ? 'Server online'
      : status.available ? 'Server offline' : 'Platform online';
  }
  const db = $('#db-chip');
  if (db) {
    const label = db.querySelector('span:last-child');
    if (label) label.textContent = (status.database || {}).path || 'No database';
  }
  const path = $('#workspace-path');
  if (path) path.textContent = status.available ? status.root : 'Standalone platform';
}

async function getPlatformStatus(force = false) {
  if (!force && state.platformStatus) return state.platformStatus;
  try {
    state.platformStatus = await api('/api/status');
  } catch (error) {
    state.platformStatus = null;
  }
  return state.platformStatus;
}

async function getDocuments(force = false) {
  if (!force && state.docs && Date.now() - state.docsAt < 15000) return state.docs;
  state.docs = await api('/api/admin/documents');
  state.docsAt = Date.now();
  return state.docs;
}

async function openFile(path) {
  if (!await guardLeave('Open a file and lose edits that have not been saved?')) return;
  state.filePath = path;
  state.fileDoc = null;
  state.fileDraft = null;
  state.fileDirty = false;
  if (currentRoute() === 'files') renderRoute();
  else location.hash = '#/files';
}

/* ------------------------------------------------------------------ dialogs */
function dialogBase(html, className = '') {
  const dialog = $('#dialog');
  if (!dialog) return null;
  if (dialog.open) dialog.close();
  dialog.className = ('dialog ' + className).trim();
  dialog.innerHTML = html;
  hydrateIcons(dialog);
  $$('[data-close]', dialog).forEach((button) => { button.onclick = () => dialog.close(); });
  dialog.showModal();
  return dialog;
}

function promptDialog(options = {}) {
  return new Promise((resolve) => {
    const type = options.type || 'text';
    const field = options.multiline
      ? `<textarea class="input" id="prompt-input" rows="5" placeholder="${esc(options.placeholder || '')}"></textarea>`
      : `<input class="input" id="prompt-input" type="${esc(type)}" value="${esc(options.value || '')}" placeholder="${esc(options.placeholder || '')}" ${options.autocomplete === false ? 'autocomplete="off"' : ''}>`;
    const dialog = dialogBase(`
      <form>
        <div class="dialog-head"><div class="grow"><h2>${esc(options.title || 'Input')}</h2>
          ${options.message ? `<p>${esc(options.message)}</p>` : ''}</div></div>
        <div class="dialog-body">
          ${options.label ? `<div class="field"><label for="prompt-input">${esc(options.label)}</label>${field}
            ${options.help ? `<span class="field-help">${esc(options.help)}</span>` : ''}</div>` : field}
        </div>
        <div class="dialog-actions">
          <button type="button" class="button text" data-cancel>Cancel</button>
          <button type="submit" class="button">${esc(options.confirm || 'Continue')}</button>
        </div>
      </form>`, 'narrow');
    if (!dialog) { resolve(null); return; }
    const input = $('#prompt-input', dialog);
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      if (dialog.open) dialog.close();
      resolve(value);
    };
    const form = $('form', dialog);
    form.onsubmit = (event) => { event.preventDefault(); finish(input.value.trim()); };
    $('[data-cancel]', dialog).onclick = () => finish(null);
    dialog.addEventListener('cancel', (event) => { event.preventDefault(); finish(null); });
    dialog.addEventListener('close', () => finish(null));
    setTimeout(() => { input.focus(); if (type !== 'password') input.select(); }, 40);
  });
}

function confirmDialog(title, message, confirmLabel = 'Confirm', danger = false) {
  return new Promise((resolve) => {
    const dialog = dialogBase(`
      <div class="dialog-head"><div class="grow"><h2>${esc(title)}</h2><p>${esc(message)}</p></div></div>
      <div class="dialog-actions">
        <button class="button text" data-cancel>Cancel</button>
        <button class="button ${danger ? 'danger-filled' : ''}" data-confirm>${esc(confirmLabel)}</button>
      </div>`, 'narrow');
    if (!dialog) { resolve(false); return; }
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      if (dialog.open) dialog.close();
      resolve(value);
    };
    $('[data-cancel]', dialog).onclick = () => finish(false);
    $('[data-confirm]', dialog).onclick = () => finish(true);
    dialog.addEventListener('cancel', (event) => { event.preventDefault(); finish(false); });
    dialog.addEventListener('close', () => finish(false));
  });
}

/* --------------------------------------------------------- unsaved changes */
function isDirty() {
  return !!(state.configDirty || state.fileDirty || (state.topic && state.topic.dirty) || state.lootDirty);
}

function discardDrafts() {
  state.configDraft = null;
  state.configDirty = false;
  state.fileDoc = null;
  state.fileDraft = null;
  state.fileText = '';
  state.fileDirty = false;
  state.topic = null;
  state.lootDraft = null;
  state.lootPath = '';
  state.lootDirty = false;
}

async function guardLeave(message = 'Leave this page and lose edits that have not been saved?') {
  if (!isDirty()) return true;
  const approved = await confirmDialog('Discard unsaved changes?', message, 'Discard', true);
  if (!approved) return false;
  discardDrafts();
  return true;
}

/* ---------------------------------------------------------- install / PWA */
let installPromptEvent = null;
window.addEventListener('beforeinstallprompt', (event) => {
  event.preventDefault();
  installPromptEvent = event;
});

async function promptInstall() {
  if (!installPromptEvent) {
    snack('Open the browser menu and choose “Add to Home screen” or “Install app”.');
    return;
  }
  const prompt = installPromptEvent;
  installPromptEvent = null;
  try {
    prompt.prompt();
    const choice = await prompt.userChoice;
    snack(choice && choice.outcome === 'accepted' ? 'Admin Studio added to your home screen' : 'Install dismissed');
  } catch (error) {
    snack('The browser could not open the install prompt', true);
  }
}

function registerServiceWorker() {
  if (!('serviceWorker' in navigator) || !window.isSecureContext) return;
  navigator.serviceWorker.register('/assets/sw.js', { scope: '/' })
    .catch((error) => console.warn('Admin shell service worker failed', error));
}

/* ------------------------------------------------------------- quick jump */
const PALETTE_ACTIONS = [
  { label: 'Refresh this section', meta: 'Reload the current page from the server', icon: 'refresh',
    run: async () => { if (await guardLeave('Refresh this page and lose unsaved edits?')) renderRoute(); } },
  { label: 'Change appearance', meta: 'Light, dark and accent color', icon: 'palette', run: openThemeDialog },
  { label: 'Install admin app', meta: 'Add a standalone icon to this device', icon: 'box', run: promptInstall },
  { label: 'Forget saved admin token', meta: 'Clear this browser’s X-Platform-Token', icon: 'lock',
    run: () => {
      try { localStorage.removeItem('dittobuffer-platform-token'); } catch (error) { /* private mode */ }
      snack('Admin token cleared — reload to enter it again');
      setTimeout(() => location.reload(), 500);
    } },
];

function openPalette() {
  const dialog = dialogBase(`
    <div class="dialog-head"><div class="grow">
      <div class="palette-search">${svgIcon('search')}
        <input class="input" id="palette-input" type="search" placeholder="Jump to a section, action or file…"
          autocomplete="off" spellcheck="false" aria-label="Search sections and files">
      </div>
    </div></div>
    <div class="palette-body" id="palette-results"></div>
    <div class="palette-foot"><span><kbd>↑</kbd><kbd>↓</kbd> move</span><span><kbd>Enter</kbd> open</span><span><kbd>Esc</kbd> close</span><span class="spacer"></span><span>Type 2+ characters to search files</span></div>`, 'palette-dialog');
  if (!dialog) return;
  const input = $('#palette-input', dialog);
  const list = $('#palette-results', dialog);
  const navRows = NAV.flatMap((group) => group.items.map((item) => ({
    kind: 'nav', id: item.id, label: item.label, meta: group.group, icon: item.icon,
  })));
  const actionRows = PALETTE_ACTIONS.map((action) => ({
    kind: 'action', id: action.label, label: action.label, meta: action.meta, icon: action.icon, run: action.run,
  }));
  let fileRows = [];
  let rows = [];
  let active = 0;
  let searchSeq = 0;
  let searchTimer = null;

  const paint = () => {
    const needle = (input.value || '').trim().toLowerCase();
    const matches = (row) => !needle
      || row.label.toLowerCase().includes(needle) || row.meta.toLowerCase().includes(needle);
    const nav = navRows.filter(matches);
    const actions = actionRows.filter(matches);
    rows = [...nav, ...actions, ...fileRows];
    active = Math.max(0, Math.min(active, rows.length - 1));
    let index = 0;
    let html = '';
    for (const [label, items] of [['Sections', nav], ['Actions', actions], ['Files', fileRows]]) {
      if (!items.length) continue;
      html += `<div class="command-group">${esc(label)}</div>`;
      html += items.map((row) => {
        const own = index++;
        return `<button type="button" class="command-item ${own === active ? 'active' : ''}" data-command-index="${own}">
          ${svgIcon(row.icon)}<span class="command-copy"><span class="command-label">${esc(row.label)}</span>
          <span class="command-meta">${esc(row.meta)}</span></span>${own === active ? '<kbd>↵</kbd>' : ''}</button>`;
      }).join('');
    }
    list.innerHTML = html || '<div class="command-empty">No sections, actions or files match that search.</div>';
    $$('[data-command-index]', list).forEach((button) => {
      button.onclick = () => execute(rows[Number(button.dataset.commandIndex)]);
    });
  };
  const select = (next) => {
    if (!rows.length) return;
    active = (next + rows.length) % rows.length;
    paint();
    const current = $('.command-item.active', list);
    if (current) current.scrollIntoView({ block: 'nearest' });
  };
  const execute = (row) => {
    if (!row) return;
    searchSeq += 1;
    dialog.close();
    if (row.kind === 'nav') navigate(row.id);
    else if (row.kind === 'file') openFile(row.id);
    else if (row.kind === 'action') row.run();
  };
  input.addEventListener('input', () => {
    active = 0;
    fileRows = [];
    paint();
    clearTimeout(searchTimer);
    searchSeq += 1;
    const query = input.value.trim();
    if (query.length < 2) return;
    const sequence = searchSeq;
    searchTimer = setTimeout(async () => {
      try {
        const data = await api('/api/admin/documents?q=' + encodeURIComponent(query), { silentAuth: true });
        if (sequence !== searchSeq) return;
        fileRows = (data.documents || []).slice(0, 6).map((row) => ({
          kind: 'file', id: row.path, label: row.path, meta: row.category + ' · ' + fmtBytes(row.bytes), icon: row.json ? 'code' : 'file',
        }));
        paint();
      } catch (error) { /* the palette still works offline for sections and actions */ }
    }, 180);
  });
  input.addEventListener('keydown', (event) => {
    if (event.key === 'ArrowDown') { event.preventDefault(); select(active + 1); }
    else if (event.key === 'ArrowUp') { event.preventDefault(); select(active - 1); }
    else if (event.key === 'Enter') { event.preventDefault(); execute(rows[active]); }
  });
  dialog.addEventListener('close', () => { searchSeq += 1; clearTimeout(searchTimer); });
  paint();
  setTimeout(() => input.focus(), 30);
}

let snackTimer = null;
function snack(message, error = false) {
  const element = $('#snackbar');
  if (!element) return;
  element.textContent = String(message || '');
  element.classList.toggle('error', !!error);
  element.classList.add('show');
  clearTimeout(snackTimer);
  snackTimer = setTimeout(() => element.classList.remove('show'), error ? 6000 : 3400);
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    snack('Copied to clipboard');
  } catch (error) {
    snack('Copy failed; select the text manually', true);
  }
}

function bindCopy(root = document) {
  $$('[data-copy]', root).forEach((button) => {
    button.onclick = () => copyText(button.dataset.copy || '');
  });
}

function bindGo(root = document) {
  $$('[data-go]', root).forEach((button) => {
    button.onclick = () => navigate(button.dataset.go);
  });
}

/* ------------------------------------------------------------------- router */
function currentRoute() {
  const raw = (location.hash || '').replace(/^#\/?/, '');
  return raw.split('?')[0] || 'platform';
}

async function navigate(id) {
  if (currentRoute() === id) {
    if (!await guardLeave('Refresh this page and lose unsaved edits?')) return;
    renderRoute();
    return;
  }
  if (!await guardLeave('Leave this page and lose edits that have not been saved?')) return;
  location.hash = '#/' + id;
}

async function handleHashChange() {
  const target = currentRoute();
  if (target !== state.route && !await guardLeave('Leave this page and lose edits that have not been saved?')) {
    history.replaceState(null, '', '#/' + state.route);
    return;
  }
  renderRoute();
}

let healthTimer = null;
function startHealthPolling() {
  clearInterval(healthTimer);
  healthTimer = setInterval(() => {
    if (document.hidden || !state.status) return;
    getStatus(true, true).catch(() => {});
  }, 20000);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && state.status) getStatus(true, true).catch(() => {});
  });
}

function setNav(id) {
  $$('.nav-item', $('#rail-nav')).forEach((item) => {
    const active = item.dataset.route === id;
    item.classList.toggle('active', active);
    if (active) item.setAttribute('aria-current', 'page');
    else item.removeAttribute('aria-current');
  });
}

function closeRail() {
  document.body.classList.remove('rail-open');
}

function clearTimers() {
  if (state.pageTimer) { clearInterval(state.pageTimer); state.pageTimer = null; }
  if (state.logTimer) { clearInterval(state.logTimer); state.logTimer = null; }
}

async function renderRoute() {
  let id = currentRoute();
  const view = $('#view');
  if (!view) return;
  if (!NAV.some((group) => group.items.some((item) => item.id === id))) id = 'platform';
  const changed = state.route !== id;
  state.route = id;
  clearTimers();
  closeRail();
  const route = NAV.flatMap((group) => group.items).find((item) => item.id === id)
    || { label: 'Platform & rooms' };
  const crumb = $('#crumb');
  if (crumb) crumb.textContent = route.label;
  setNav(id);
  if (changed) window.scrollTo(0, 0);
  view.innerHTML = loadingBlock('Loading ' + route.label.toLowerCase() + '…');
  try {
    const page = window.PAGES[id];
    if (!page) throw new Error('Unknown section: ' + id);
    await page();
  } catch (error) {
    console.error(error);
    view.innerHTML = errorBlock(error);
    const retry = $('#error-retry');
    if (retry) retry.onclick = () => renderRoute();
  }
}

function renderNav() {
  const rail = $('#rail-nav');
  if (!rail) return;
  rail.innerHTML = NAV.map((group) => `
    <div class="section-label" style="padding:14px 16px 5px">${esc(group.group)}</div>
    ${group.items.map((item) => `
      <button class="nav-item" type="button" data-route="${item.id}">
        <span class="nav-icon" data-icon="${item.icon}"></span>
        <span class="nav-label">${esc(item.label)}</span>
      </button>`).join('')}
  `).join('');
  hydrateIcons(rail);
  $$('.nav-item', rail).forEach((item) => { item.onclick = () => navigate(item.dataset.route); });
}

function boot() {
  hydrateIcons();
  renderNav();
  const menu = $('#menu-button');
  if (menu) menu.onclick = () => document.body.classList.toggle('rail-open');
  const scrim = $('#scrim');
  if (scrim) scrim.onclick = closeRail;
  const theme = $('#theme-button');
  if (theme) theme.onclick = openThemeDialog;
  const refresh = $('#refresh-button');
  if (refresh) refresh.onclick = async () => {
    if (await guardLeave('Refresh this page and lose unsaved edits?')) renderRoute();
  };
  const paletteButton = $('#palette-button');
  if (paletteButton) paletteButton.onclick = openPalette;
  document.addEventListener('keydown', (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
      event.preventDefault();
      openPalette();
    }
  });
  window.addEventListener('hashchange', handleHashChange);
  window.addEventListener('beforeunload', (event) => {
    if (isDirty()) {
      event.preventDefault();
      event.returnValue = '';
    }
  });
  window.addEventListener('online', () => snack('Back online — live data will refresh automatically'));
  window.addEventListener('offline', () => snack('Offline — saved pages remain available, edits need the server', true));
  registerServiceWorker();
  startHealthPolling();
  renderRoute();
}

let adminBooted = false;
function startAdmin() {
  if (adminBooted) return;
  adminBooted = true;
  boot();
}

// Deferred scripts execute in order, but the document may already be
// "interactive" while core.js runs. Wait for the page scripts that register
// PAGES before starting the router.
if (document.readyState === 'complete') startAdmin();
else {
  document.addEventListener('DOMContentLoaded', () => setTimeout(startAdmin, 0), { once: true });
  window.addEventListener('load', startAdmin, { once: true });
}

/* -------------------------------------------------------------- shared view */
function loadingBlock(label = 'Loading…') {
  return `<div class="loading-block"><span class="spinner"></span><span>${esc(label)}</span></div>`;
}

function errorBlock(error) {
  return `<div class="empty-state">
    <div data-icon="warning"></div>
    <h3>Could not load this section</h3>
    <p>${esc(error && error.message ? error.message : error)}</p>
    <div class="row end" style="justify-content:center;margin-top:16px">
      <button class="button tonal" id="error-retry">${svgIcon('refresh')} Retry</button>
      <button class="button text" data-go="platform">Platform settings</button>
    </div>
  </div>`;
}

function pageHead(title, subtitle, actions = '') {
  return `<div class="page-head">
    <div class="grow"><h1>${esc(title)}</h1><p>${subtitle}</p></div>
    <div class="page-head-actions">${actions}</div>
  </div>`;
}

function validationNotice(validation, compact = false) {
  const errors = (validation && validation.errors) || [];
  const warnings = (validation && validation.warnings) || [];
  if (!errors.length && !warnings.length) {
    return `<div class="notice success">${svgIcon('check')}<div class="notice-body">
      <strong>Profile validates</strong>No blocking problems or warnings.
    </div></div>`;
  }
  let html = '';
  if (errors.length) {
    html += `<div class="notice error">${svgIcon('warning')}<div class="notice-body">
      <strong>${errors.length} error${errors.length === 1 ? '' : 's'} will block server start</strong>
      <ul class="warn-list">${errors.map((item) => `<li>${esc(item)}</li>`).join('')}</ul></div></div>`;
  }
  if (warnings.length) {
    html += `<div class="notice warn" style="margin-top:${errors.length ? '8px' : '0'}">${svgIcon('warning')}<div class="notice-body">
      <strong>${warnings.length} warning${warnings.length === 1 ? '' : 's'}</strong>
      ${compact ? esc(warnings[0]) + (warnings.length > 1 ? ' …' : '') : `<ul class="warn-list">${warnings.map((item) => `<li>${esc(item)}</li>`).join('')}</ul>`}
    </div></div>`;
  }
  return html;
}

function featureChips(features = {}) {
  const labels = { raids: 'Raids', pvp: 'PvP', max: 'Max', research: 'Research',
    social: 'Social', world: 'World', liveops: 'LiveOps' };
  return Object.entries(labels).map(([key, label]) => `
    <span class="chip ${features[key] ? 'success' : 'outline'}">
      ${features[key] ? svgIcon('check') : svgIcon('minus')}${label}
    </span>`).join('');
}

function emptyState(title, message, icon = 'box', action = '') {
  return `<div class="empty-state"><div data-icon="${icon}"></div><h3>${esc(title)}</h3>
    <p>${esc(message)}</p>${action ? `<div style="margin-top:16px">${action}</div>` : ''}</div>`;
}

/* ------------------------------------------------------------ JSON form engine */
function enumFor(path) {
  const joined = path.join('.').toLowerCase();
  const last = path[path.length - 1];
  const exact = {
    'network.mode': ['loopback', 'tls', 'private-vpn'],
    'authentication.mode': ['family', 'local', 'legacy'],
    'authentication.registration': ['open', 'invite'],
    'profile': ['private-transfer', 'private-transfer-family', 'developer'],
    'ai_difficulty': ['easy', 'normal', 'hard', 'expert'],
    'spawn_generation': ['gen1', 'gen2', 'gen3', 'gen4', 'gen5', 'gen6', 'gen7', 'gen8', 'gen9', 'all'],
    'world.mode': ['static', 'dynamic'],
  };
  if (exact[joined]) return exact[joined];
  if (exact[last]) return exact[last];
  if (joined.endsWith('.fold_policy')) return ['earlier', 'later'];
  if (joined.endsWith('.gap_policy')) return ['skip', 'clamp'];
  if (joined.endsWith('.cap_policy')) return ['threshold', 'strict', 'warn'];
  if (joined.endsWith('.theme')) return ['auto', 'light', 'dark'];
  if (joined.endsWith('.difficulty')) return ['easy', 'normal', 'hard', 'expert'];
  return null;
}

function createJsonEditor(rootValue, options = {}) {
  const opts = {
    search: '', rootLabel: 'Configuration', onChange: () => {}, onlyKeys: null,
    ...options,
  };
  const visibleKeys = opts.onlyKeys ? new Set(opts.onlyKeys) : null;
  const host = document.createElement('div');
  host.className = 'json-form';

  const mark = () => opts.onChange();
  const refresh = () => { host.replaceChildren(); renderRoot(); hydrateIcons(host); };

  function matches(path, value) {
    if (!opts.search) return true;
    const needle = opts.search.toLowerCase();
    if (path.length && path.join('.').toLowerCase().includes(needle)) return true;
    if (value && typeof value === 'object') {
      const entries = Array.isArray(value)
        ? value.map((item, index) => [String(index), item]) : Object.entries(value);
      return entries.some(([key, item]) => matches(path.concat(key), item));
    }
    return false;
  }

  function renderRoot() {
    if (!isObject(rootValue)) {
      const notice = document.createElement('div');
      notice.className = 'notice warn';
      notice.innerHTML = `${svgIcon('warning')}<div class="notice-body"><strong>Object required</strong>This editor can only display a JSON object.</div>`;
      host.appendChild(notice);
      return;
    }
    host.appendChild(buildObject(rootValue, [], 0, true));
  }

  function objectKeys(obj, isRoot) {
    if (isRoot && visibleKeys) return Array.from(visibleKeys);
    return Object.keys(obj);
  }

  function buildObject(obj, path, depth, isRoot) {
    const details = document.createElement('details');
    details.className = 'json-object depth-' + Math.min(depth, 2);
    details.open = depth < 2 || !!opts.search;
    const summary = document.createElement('summary');
    summary.innerHTML = `<span class="summary-arrow">${svgIcon('chevron')}</span>
      <span>${esc(isRoot ? opts.rootLabel : (path[path.length - 1] ?? 'Object'))}</span>
      <span class="chip outline">${Object.keys(obj).length} keys</span>`;
    const add = document.createElement('button');
    add.type = 'button';
    add.className = 'button text small';
    add.style.marginLeft = 'auto';
    add.innerHTML = svgIcon('plus') + '<span>Add key</span>';
    add.addEventListener('click', async (event) => {
      event.preventDefault();
      event.stopPropagation();
      const key = await promptDialog({
        title: 'Add key', label: 'Property name', confirm: 'Add',
        help: isRoot ? 'A new top-level key will be written to the profile.' : 'Added beside the existing properties.',
      });
      if (!key) return;
      if (Object.prototype.hasOwnProperty.call(obj, key)) { snack('That key already exists', true); return; }
      obj[key] = null;
      if (isRoot && visibleKeys) visibleKeys.add(key);
      mark();
      refresh();
    });
    summary.appendChild(add);
    details.appendChild(summary);

    const body = document.createElement('div');
    body.className = 'json-object-body';
    const keys = objectKeys(obj, isRoot);
    let shown = 0;
    for (const key of keys) {
      if (!Object.prototype.hasOwnProperty.call(obj, key)) {
        if (isRoot && (!opts.search || key.toLowerCase().includes(opts.search.toLowerCase()))) {
          body.appendChild(missingRow(obj, key, isRoot));
          shown += 1;
        }
        continue;
      }
      if (!matches(path.concat(key), obj[key])) continue;
      body.appendChild(buildRow(obj, key, path.concat(key), isRoot));
      shown += 1;
    }
    if (!shown) {
      const empty = document.createElement('div');
      empty.className = 'muted small';
      empty.style.padding = '8px 0';
      empty.textContent = opts.search ? 'No fields match this filter.' : 'No keys here yet. Use Add key.';
      body.appendChild(empty);
    }
    details.appendChild(body);
    return details;
  }

  function missingRow(obj, key, isRoot) {
    const row = document.createElement('div');
    row.className = 'json-row';
    row.innerHTML = `<div class="json-key"><span>${esc(key)}</span><span class="kind">missing</span></div>
      <div class="json-value"><span class="muted small">Not set in this profile.</span></div>`;
    const actions = document.createElement('div');
    actions.className = 'json-row-actions';
    const add = document.createElement('button');
    add.type = 'button';
    add.className = 'button text small';
    add.textContent = 'Add';
    add.onclick = () => { obj[key] = {}; mark(); refresh(); };
    actions.appendChild(add);
    row.appendChild(actions);
    return row;
  }

  function buildRow(parent, key, path, isRoot) {
    const value = parent[key];
    const row = document.createElement('div');
    row.className = 'json-row';
    const keyCell = document.createElement('div');
    keyCell.className = 'json-key';
    keyCell.innerHTML = `<span>${esc(key)}</span><span class="kind">${kindOf(value)}</span>`;
    const valueCell = document.createElement('div');
    valueCell.className = 'json-value';
    valueCell.appendChild(buildValue(value, path, (next) => { parent[key] = next; mark(); }));
    const actions = document.createElement('div');
    actions.className = 'json-row-actions';
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'button text small icon-only';
    remove.title = 'Remove key';
    remove.innerHTML = svgIcon('trash');
    remove.onclick = async () => {
      if (!await confirmDialog('Remove key?', 'Delete "' + key + '" from this object?', 'Remove', true)) return;
      delete parent[key];
      if (isRoot && visibleKeys) visibleKeys.delete(key);
      mark();
      refresh();
    };
    actions.appendChild(remove);
    row.appendChild(keyCell);
    row.appendChild(valueCell);
    row.appendChild(actions);
    return row;
  }

  function buildValue(value, path, setter) {
    if (Array.isArray(value)) return buildArray(value, path);
    if (isObject(value)) return buildObject(value, path, path.length, false);
    return buildPrimitive(value, path, setter);
  }

  function buildPrimitive(value, path, setter) {
    const wrap = document.createElement('div');
    const options = enumFor(path);
    if (typeof value === 'boolean') {
      wrap.className = 'switch-field';
      wrap.innerHTML = `<label class="switch"><input type="checkbox" ${value ? 'checked' : ''}><span class="track"></span></label>
        <span class="small muted">${value ? 'Enabled' : 'Disabled'}</span>`;
      const input = $('input', wrap);
      input.onchange = () => {
        setter(input.checked);
        $('.small', wrap).textContent = input.checked ? 'Enabled' : 'Disabled';
      };
      return wrap;
    }
    if (options) {
      const select = document.createElement('select');
      select.className = 'select';
      const values = options.includes(String(value)) ? options : options.concat([String(value)]);
      values.forEach((option) => {
        const element = document.createElement('option');
        element.value = option;
        element.textContent = option;
        if (option === String(value)) element.selected = true;
        select.appendChild(element);
      });
      select.onchange = () => setter(select.value);
      wrap.appendChild(select);
      return wrap;
    }
    if (typeof value === 'number') {
      const input = document.createElement('input');
      input.type = 'number';
      input.className = 'input';
      input.step = Number.isInteger(value) ? '1' : 'any';
      input.value = String(value);
      input.oninput = () => {
        if (input.value === '') { setter(null); return; }
        const number = Number(input.value);
        if (Number.isFinite(number)) setter(number);
      };
      wrap.appendChild(input);
      return wrap;
    }
    if (value === null) {
      const input = document.createElement('input');
      input.className = 'input mono';
      input.placeholder = 'null — type JSON to change';
      input.value = '';
      input.onchange = () => {
        const text = input.value.trim();
        if (!text) { setter(null); return; }
        try { setter(JSON.parse(text)); } catch (error) { setter(text); }
      };
      wrap.appendChild(input);
      return wrap;
    }
    const text = String(value ?? '');
    const long = text.length > 120 || text.includes('\n')
      || /(notes?|description|provenance|reason|message|body|html|json)$/i.test(path[path.length - 1] || '');
    const input = document.createElement(long ? 'textarea' : 'input');
    input.className = 'input' + (long ? ' textarea' : '');
    if (!long) input.type = 'text';
    if (long) input.rows = 3;
    input.value = text;
    input.oninput = () => setter(input.value);
    wrap.appendChild(input);
    return wrap;
  }

  function buildArray(array, path) {
    const wrap = document.createElement('div');
    wrap.className = 'json-array';
    const head = document.createElement('div');
    head.className = 'row between';
    head.innerHTML = `<span class="small muted">${array.length} item${array.length === 1 ? '' : 's'}</span>`;
    const adds = document.createElement('div');
    adds.className = 'json-add';
    [['Object', {}], ['Text', ''], ['Number', 0], ['Boolean', false], ['Null', null]]
      .forEach(([label, sample]) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'button text small';
        button.textContent = '+ ' + label;
        button.onclick = () => { array.push(clone(sample)); mark(); refresh(); };
        adds.appendChild(button);
      });
    head.appendChild(adds);
    wrap.appendChild(head);

    array.forEach((item, index) => {
      const itemWrap = document.createElement('div');
      itemWrap.className = 'json-array-item';
      const itemHead = document.createElement('div');
      itemHead.className = 'json-array-item-head';
      itemHead.innerHTML = `<span class="json-array-item-index">#${index}</span>
        <span class="kind">${kindOf(item)}</span><span class="spacer"></span>`;
      const up = document.createElement('button');
      up.type = 'button'; up.className = 'button text small icon-only'; up.title = 'Move up';
      up.innerHTML = svgIcon('up');
      up.disabled = index === 0;
      up.onclick = () => {
        const temp = array[index - 1]; array[index - 1] = array[index]; array[index] = temp;
        mark(); refresh();
      };
      const down = document.createElement('button');
      down.type = 'button'; down.className = 'button text small icon-only'; down.title = 'Move down';
      down.innerHTML = svgIcon('down');
      down.disabled = index === array.length - 1;
      down.onclick = () => {
        const temp = array[index + 1]; array[index + 1] = array[index]; array[index] = temp;
        mark(); refresh();
      };
      const remove = document.createElement('button');
      remove.type = 'button'; remove.className = 'button text small icon-only'; remove.title = 'Remove item';
      remove.innerHTML = svgIcon('trash');
      remove.onclick = () => { array.splice(index, 1); mark(); refresh(); };
      itemHead.appendChild(up);
      itemHead.appendChild(down);
      itemHead.appendChild(remove);
      const body = document.createElement('div');
      body.className = 'json-array-item-body';
      body.appendChild(buildValue(item, path.concat(String(index)), (next) => {
        array[index] = next; mark();
      }));
      itemWrap.appendChild(itemHead);
      itemWrap.appendChild(body);
      wrap.appendChild(itemWrap);
    });
    return wrap;
  }

  refresh();
  return host;
}

function openRawJsonDialog(options) {
  const { title = 'Raw JSON', value, onApply, objectOnly = true } = options;
  const dialog = dialogBase(`
    <div class="dialog-head"><div class="grow"><h2>${esc(title)}</h2>
      <p>Edit the document directly. Nothing is written until Apply.</p></div>
      <button class="button text icon-only" data-close aria-label="Close">${svgIcon('close')}</button></div>
    <div class="dialog-body">
      <textarea class="code-editor" id="raw-json" spellcheck="false"></textarea>
      <div class="notice error" id="raw-error" style="display:none;margin-top:10px"></div>
    </div>
    <div class="dialog-actions">
      <button class="button text" data-close>Cancel</button>
      <button class="button" id="raw-apply">${svgIcon('check')} Apply</button>
    </div>`, 'wide');
  if (!dialog) return;
  const area = $('#raw-json', dialog);
  area.value = JSON.stringify(value, null, 2);
  $('#raw-apply', dialog).onclick = () => {
    try {
      const parsed = JSON.parse(area.value);
      if (objectOnly && (!isObject(parsed))) throw new Error('The top-level JSON value must be an object');
      dialog.close();
      onApply(parsed);
    } catch (error) {
      const box = $('#raw-error', dialog);
      box.style.display = 'flex';
      box.textContent = 'Invalid JSON: ' + error.message;
    }
  };
}
