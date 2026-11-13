'use strict';
const $ = (id) => document.getElementById(id);
const state = { all: [], matches: [], categories: [], category: '', trash: false, note: null, editing: false, original: '', saving: false, navigating: false, library: '', draftKey: null, inline: null, user: null };
let searchTimer, toastTimer, requestId = 0, selectionId = 0;
let routeIndex = Number.isInteger(history.state?.noteRouteIndex) ? history.state.noteRouteIndex : 0;
let revertingHistory = false;
history.replaceState({ noteRouteIndex: routeIndex }, '');
let diagramId = 0, markdownVersion = 0, mermaidReady = null, diagramCacheGeneration = 0;
let mermaidUseFull = false;
let taskListsRequest = null, taskListsDay = '', taskListsWeek = '', initialized = false;
const diagramTimers = new WeakMap();
const diagramCache = new Map();
const noteCache = new Map(), noteRequests = new Map();
const NOTE_CACHE_LIMIT = 8, NOTE_CACHE_BYTES = 2 * 1024 * 1024, NOTE_CACHE_TTL = 30000;
let noteCacheBytes = 0, noteCacheGeneration = 0, neighborPrefetchTimer;
let lastDiagramScroll = -Infinity;
document.addEventListener('scroll', () => { lastDiagramScroll = performance.now(); }, { capture: true, passive: true });
async function waitForDiagramIdle(current) {
  while (current()) {
    await new Promise((resolve) => {
      if (window.requestIdleCallback) window.requestIdleCallback(resolve, { timeout: 800 });
      else setTimeout(resolve, 0);
    });
    if (!current()) return false;
    const remaining = 160 - (performance.now() - lastDiagramScroll);
    if (remaining <= 0) return true;
    await new Promise((resolve) => setTimeout(resolve, remaining));
  }
  return false;
}
function loadMermaid() {
  if (!mermaidReady) {
    mermaidReady = new Promise((resolve, reject) => {
      const script = document.createElement('script');
      if (!mermaidUseFull) script.type = 'module';
      script.src = mermaidUseFull ? '/vendor/mermaid-full.js' : '/vendor/mermaid.js'; script.async = true;
      const failed = () => { script.remove(); reject(new Error('Mermaid unavailable')); };
      script.onerror = failed;
      script.onload = () => {
        try {
          window.mermaid.initialize({
            startOnLoad: false, securityLevel: 'strict', suppressErrorRendering: true,
            theme: 'default', fontFamily: 'Segoe UI, Microsoft YaHei, sans-serif',
            htmlLabels: false, flowchart: { htmlLabels: false, useMaxWidth: false },
            maxTextSize: 100000, maxEdges: 500,
            secure: ['secure', 'securityLevel', 'startOnLoad', 'maxTextSize', 'maxEdges',
              'suppressErrorRendering', 'themeCSS', 'themeVariables', 'fontFamily', 'flowchart', 'htmlLabels'],
          });
          resolve(window.mermaid);
        } catch { failed(); }
      };
      document.head.append(script);
    }).catch((error) => { mermaidUseFull = true; mermaidReady = null; throw error; });
  }
  return mermaidReady;
}
const escapeHtml = (text) => String(text).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const icon = (name) => `<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;
const normalizedCategory = (value) => value.split('/').map((part) => part.trim()).filter(Boolean).join('/') || '未分类';
const shortDate = (value) => new Date(value).toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' });
const longDate = (value) => new Date(value).toLocaleString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit' });

function notify(message, error = false) {
  clearTimeout(toastTimer);
  $('toast').textContent = message;
  $('toast').className = error ? 'error' : '';
  $('toast').hidden = false;
  toastTimer = setTimeout(() => { $('toast').hidden = true; }, error ? 9000 : 3500);
}
async function api(path, options = {}) {
  const writing = !['GET', 'HEAD', 'OPTIONS'].includes((options.method || 'GET').toUpperCase());
  if (writing) clearNoteCache();
  try { return await Auth.api(path, options); }
  finally { if (writing) clearNoteCache(); }
}
function removeCachedNote(id) {
  const cached = noteCache.get(id);
  if (cached) noteCacheBytes -= cached.bytes;
  noteCache.delete(id);
}
function clearNoteCache() {
  noteCacheGeneration++; noteCache.clear(); noteRequests.clear(); noteCacheBytes = 0;
  clearTimeout(neighborPrefetchTimer);
}
function cachedNote(id) {
  for (const [key, cached] of noteCache) if (Date.now() - cached.at >= NOTE_CACHE_TTL) removeCachedNote(key);
  const cached = noteCache.get(id);
  if (!cached) return null;
  noteCache.delete(id); noteCache.set(id, cached);
  return structuredClone(cached.note);
}
function cacheNote(note) {
  removeCachedNote(note.id);
  const bytes = new TextEncoder().encode(JSON.stringify(note)).byteLength;
  if (bytes > NOTE_CACHE_BYTES) return;
  while (noteCache.size >= NOTE_CACHE_LIMIT || noteCacheBytes + bytes > NOTE_CACHE_BYTES) removeCachedNote(noteCache.keys().next().value);
  noteCache.set(note.id, { note: structuredClone(note), bytes, at: Date.now() }); noteCacheBytes += bytes;
}
function fetchNote(id) {
  if (noteRequests.has(id)) return noteRequests.get(id);
  const generation = noteCacheGeneration;
  const request = api('/api/note?id=' + encodeURIComponent(id)).then((note) => {
    if (generation !== noteCacheGeneration || !Auth.authenticated) throw new Error('文档状态已改变，请重新打开文档。');
    cacheNote(note);
    return note;
  }).catch((error) => {
    if (generation === noteCacheGeneration) removeCachedNote(id);
    throw error;
  }).finally(() => { if (noteRequests.get(id) === request) noteRequests.delete(id); });
  noteRequests.set(id, request);
  return request;
}
async function prefetchNote(id) {
  if (!Auth.authenticated || state.trash || !state.all.some((note) => note.id === id) || cachedNote(id)) return;
  if (!noteRequests.has(id) && noteRequests.size >= 2) return;
  try { await fetchNote(id); } catch { /* Opening the note will report an error if it still fails. */ }
}
function prefetchNeighbors(id, token) {
  clearTimeout(neighborPrefetchTimer);
  const connection = navigator.connection;
  if (connection?.saveData || /^(slow-2g|2g)$/.test(connection?.effectiveType || '')) return;
  const generation = noteCacheGeneration;
  const current = () => token === selectionId && generation === noteCacheGeneration && Auth.authenticated
    && state.note?.id === id && !state.trash && !state.editing && !state.inline && !state.saving;
  const run = async () => {
    if (!current()) return;
    // Let the visible diagrams finish their initial module requests first.
    if ($('article').querySelector('.mermaid-canvas[aria-busy="true"]')) {
      neighborPrefetchTimer = setTimeout(run, 500); return;
    }
    const notes = visibleNotes();
    const index = notes.findIndex((note) => note.id === id);
    if (index < 0) return;
    const neighbors = [notes[index + 1], notes[index - 1], notes[index + 2]].filter(Boolean).slice(0, 2);
    for (const note of neighbors) { if (!current()) return; await prefetchNote(note.id); }
  };
  neighborPrefetchTimer = setTimeout(() => {
    if (window.requestIdleCallback) window.requestIdleCallback(run, { timeout: 1000 });
    else run();
  }, 350);
}
function notePermissions(note = state.note) {
  if (Auth.owner()) return { read: true, edit: true };
  const permission = note?.permissions || note?.permission || {};
  return { read: permission.read !== false, edit: Boolean(permission.edit) };
}
function canEditNote(note = state.note) { return Boolean(note && !note.trash_id && notePermissions(note).edit); }
function canCreateIn(category = state.category) { return Auth.canCreate(category || undefined); }
function updatePermissionUi() {
  const canCreate = !state.trash && canCreateIn();
  document.querySelectorAll('.new-note').forEach((button) => { button.hidden = !canCreate; });
  $('import-note').hidden = !canCreate;
  $('add-category').hidden = !Auth.owner();
  $('manage-access').hidden = !Auth.owner();
  $('deleted-notes').hidden = !Auth.owner();
  $('new-weekly').hidden = !Auth.owner();
  $('open-general-tasks').hidden = !Auth.owner();
  const lockedMetadata = Boolean(state.note && !Auth.owner());
  $('edit-title').readOnly = lockedMetadata || state.saving || state.navigating;
  $('edit-category').readOnly = lockedMetadata || state.saving || state.navigating;
}
function clearAppForLogout() {
  initialized = false; requestId++; selectionId++; taskListsDay = ''; taskListsRequest = null;
  clearNoteCache();
  diagramCache.clear(); diagramCacheGeneration++; navigationStatus();
  clearTimeout(searchTimer); clearTimeout(toastTimer); clearTimeout(diagramTimers.get($('article'))); clearTimeout(diagramTimers.get($('preview')));
  closeInline(false); state.all = []; state.matches = []; state.categories = []; state.note = null; state.user = null; state.library = ''; state.trash = false; state.category = ''; state.editing = false; state.draftKey = null; state.original = ''; state.saving = false; state.navigating = false;
  for (const id of ['note-list', 'categories', 'article', 'preview', 'toc', 'draft-list', 'category-options']) $(id).replaceChildren();
  for (const id of ['edit-title', 'edit-category', 'edit-content', 'search']) $(id).value = '';
  for (const id of ['note-category', 'note-date', 'document-footer', 'draft-banner-text', 'task-progress']) $(id).textContent = '';
  for (const id of ['reader', 'editor', 'empty-view', 'draft-banner', 'open-drafts', 'toast']) $(id).hidden = true;
  $('current-category').textContent = '全部文档'; document.body.classList.remove('has-drafts');
  history.replaceState({ noteRouteIndex: routeIndex }, '', '/');
  updatePermissionUi(); document.title = '知序 · 知识库';
}
function fields() { return { title: $('edit-title').value.trim(), category: normalizedCategory($('edit-category').value), content: $('edit-content').value }; }
function dirty() { return state.inline ? state.inline.input.value !== state.inline.initial : state.editing && JSON.stringify(fields()) !== state.original; }
function canLeave() { return !state.saving && !state.navigating && (!dirty() || confirm('当前修改尚未保存到知识库。确定离开编辑吗？可保留的修改会留在本机草稿中。')); }
function setNoteListCollapsed(collapsed, remember = true) {
  document.body.classList.toggle('note-list-collapsed', collapsed);
  $('notes-panel').hidden = collapsed;
  $('toggle-note-list').setAttribute('aria-expanded', String(!collapsed));
  $('note-list-toggle-label').textContent = collapsed ? '展开列表' : '收起列表';
  if (remember) { try { localStorage.setItem(storagePrefix() + 'noteListCollapsed', String(collapsed)); } catch {} }
}
function showView(view) {
  closeInline(false);
  for (const id of ['reader', 'editor', 'empty-view']) $(id).hidden = id !== view;
  state.editing = view === 'editor';
  $('empty-view').querySelector('h2').textContent = state.trash ? '回收站是空的' : '从一篇笔记开始';
  $('empty-view').querySelector('p').textContent = state.trash ? '删除的文档会出现在这里，可以随时恢复。' : '记录一个想法，整理一段知识。你的文档会保存在这台电脑上。';
  $('empty-view').querySelector('.new-note').hidden = state.trash || !canCreateIn();
  if (!Auth.owner() && !state.trash) {
    $('empty-view').querySelector('h2').textContent = '这里暂时没有可访问的文档';
    $('empty-view').querySelector('p').textContent = '风落分配权限后，文档会出现在左侧列表中。';
  }
}
function noteLocation(note = state.note, fragment = '') {
  const query = new URLSearchParams();
  if (note?.trash_id) query.set('deleted', note.trash_id);
  else if (note) query.set('note', note.id);
  else if (state.trash) query.set('view', 'deleted');
  return '/' + (query.size ? '?' + query.toString() : '') + (fragment ? '#' + encodeURIComponent(fragment) : '');
}
function updateLocation(replace = false, fragment = '') {
  const url = noteLocation(state.note, fragment);
  if (url !== location.pathname + location.search + location.hash) {
    if (!replace) routeIndex++;
    history[replace ? 'replaceState' : 'pushState']({ noteRouteIndex: routeIndex }, '', url);
  }
}
function scrollToFragment(fragment) {
  if (!fragment) { window.scrollTo({ top: 0, behavior: 'instant' }); return; }
  const headings = [...$('article').querySelectorAll('h1,h2,h3,h4,h5,h6,[id]')];
  const heading = headings.find((item) => item.id === fragment || item.dataset.anchor === fragment || item.dataset.legacyAnchor === fragment || item.id === 'user-content-' + fragment)
    || headings.find((item) => item.dataset.anchor === NoteLinks.headingSlug(fragment));
  if (heading) heading.scrollIntoView({ block: 'start' });
  else notify('已打开文档，但没有找到链接指定的小节。');
}
function bindNoteLinks(target) {
  const noteId = state.note?.id || `${normalizedCategory($('edit-category').value)}/${$('edit-title').value || '未命名'}.md`;
  for (const link of target.querySelectorAll('a[href]')) {
    const raw = link.getAttribute('href');
    const destination = NoteLinks.resolve(raw, noteId);
    if (!destination) {
      if (/^https?:/i.test(raw)) { link.target = '_blank'; link.rel = 'noopener noreferrer'; }
      continue;
    }
    const sameDeletedNote = raw.startsWith('#') && state.note?.trash_id && target.id === 'article';
    if (!destination.error) link.href = noteLocation(sameDeletedNote ? state.note : { id: destination.id }, destination.fragment);
    link.removeAttribute('target');
    link.addEventListener('pointerdown', (event) => { if (state.inline?.visual) event.preventDefault(); });
    link.addEventListener('click', async (event) => {
      if (event.button || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      if (destination.error) { notify(destination.error, true); return; }
      if (raw.startsWith('#') && target.id === 'article') {
        if (!await finishTaskRow()) return;
        updateLocation(false, destination.fragment); scrollToFragment(destination.fragment); return;
      }
      await openNote(destination.id, false, undefined, { linked: true, fragment: destination.fragment });
    });
  }
}
function taskTokens(tokens) {
  const tasks = [];
  marked.walkTokens(tokens, (token) => { if (token.type === 'list_item' && token.task) tasks.push(token); });
  return tasks;
}
function sourceTokens(source) {
  const normalized = source.replace(/\r\n|\r/g, '\n');
  const offsets = [0];
  for (let raw = 0; raw < source.length; raw++) {
    if (source[raw] === '\r' && source[raw + 1] === '\n') raw++;
    offsets.push(raw + 1);
  }
  const tokenizer = new marked.Tokenizer();
  for (const name of ['heading', 'lheading', 'list']) {
    const parse = tokenizer[name];
    tokenizer[name] = function (remaining) {
      const token = parse.call(this, remaining);
      if (token && normalized.endsWith(remaining) && remaining.startsWith(token.raw)) {
        const start = normalized.length - remaining.length;
        token.sourceStart = offsets[start]; token.sourceEnd = offsets[start + token.raw.length];
      }
      return token;
    };
  }
  return marked.lexer(source, { gfm: true, tokenizer });
}
function taskMarkdown(content) {
  const tokens = sourceTokens(content);
  const tasks = taskTokens(tokens);
  const lists = [], sections = [];
  let group = -1, listOrder = 0;
  tokens.forEach((token, index) => {
    if (token.type === 'heading' && token.depth <= 2) { group++; listOrder = 0; }
    if (!Number.isInteger(token.sourceEnd)) return;
    if (token.type === 'list' && taskTokens([token]).length) {
      token.taskListIndex = lists.length;
      const indent = token.raw.match(/^ */)[0];
      const bullet = indent + (token.ordered ? `${token.start + token.items.length}${token.raw.match(/^\s*\d{1,9}([.)])/)?.[1] || '.'}` : token.raw.match(/^\s*([-+*])/)?.[1] || '-');
      lists.push({ position: token.sourceEnd, bullet, group, order: listOrder++ });
    }
    if (token.type === 'heading' && token.depth === 2 && token.text !== '完成记录') {
      const next = tokens.slice(index + 1).find((item) => item.type !== 'space');
      if (!next || (next.type === 'heading' && next.depth <= 2)) {
        token.taskSectionIndex = sections.length;
        sections.push({ position: token.sourceEnd, bullet: '-', group });
      }
    }
  });
  const key = `task-${crypto.randomUUID()}`;
  let positions = [];
  if (tasks.length) {
    // The annotated copy is only for locating tasks. Render the untouched tokens,
    // so code examples, HTML, indentation and CRLF in the source stay unchanged.
    const annotated = content.replace(/^([\t >]*(?:[-+*]|\d{1,9}[.)])[\t ]+\[)([ xX])(\][\t ]+)(?=\S)/gm,
      (match, prefix, checked, suffix, offset) => `${match}<!--${key}:${offset + prefix.length}-->`);
    const located = taskTokens(marked.lexer(annotated, { gfm: true }));
    const marker = new RegExp(`<!--${key}:(\\d+)-->`, 'g');
    if (located.length === tasks.length) positions = tasks.map((task, index) => {
      const other = located[index];
      const match = other.text.match(new RegExp(`^<!--${key}:(\\d+)-->`));
      return match && other.raw.replace(marker, '') === task.raw ? Number(match[1]) : null;
    });
  }
  let index = 0;
  const renderer = new marked.Renderer();
  const list = renderer.list, heading = renderer.heading;
  renderer.list = function (token) {
    const html = list.call(this, token);
    return Number.isInteger(token.taskListIndex) ? html.replace(/^<(ul|ol)/, `<$1 data-task-list-key="${key}" data-task-list-index="${token.taskListIndex}"`) : html;
  };
  renderer.heading = function (token) {
    const html = heading.call(this, token);
    return Number.isInteger(token.taskSectionIndex) ? html.replace(/^<h2/, `<h2 data-task-section-key="${key}" data-task-section-index="${token.taskSectionIndex}"`) : html;
  };
  renderer.checkbox = ({ checked }) => `<input type="checkbox" disabled data-task-key="${key}" data-task-index="${index++}"${checked ? ' checked' : ''}>`;
  return { html: marked.parser(tokens, { gfm: true, renderer }), positions, lists, sections, key };
}
function setTaskBusy(busy) {
  document.querySelectorAll('.task-delete').forEach((button) => { button.disabled = busy || Boolean(state.inline) || !canEditNote(); });
  document.querySelectorAll('input.task-checkbox[data-task-position]').forEach((input) => { input.disabled = busy || Boolean(state.inline) || (input.closest('#article') && !canEditNote()); });
  $('insert-task').disabled = busy;
  for (const id of ['reader-edit-body', 'reader-add-task']) $(id).disabled = busy;
  for (const id of ['delete-note', 'restore-note']) $(id).disabled = busy;
}
function updateTaskProgress() {
  const inputs = [...$('article').querySelectorAll('input.task-checkbox')];
  $('task-progress').hidden = !inputs.length;
  $('task-progress').textContent = `已完成 ${inputs.filter((input) => input.checked).length} / ${inputs.length} 项 · ${state.note?.trash_id ? '已删除 · 只读' : !canEditNote() ? '只读' : '方框打勾，点击文字修改'}`;
}
function taskTextRange(source, position) {
  const start = position + 2 + (source.slice(position + 2).match(/^[\t ]*/) || [''])[0].length;
  const lineBreak = source.slice(start).search(/[\r\n]/);
  const end = lineBreak < 0 ? source.length : start + lineBreak;
  return { start, end };
}
function taskRemovalRange(source, position, task) {
  const start = Math.max(source.lastIndexOf('\n', position), source.lastIndexOf('\r', position)) + 1;
  const lines = task.raw.replace(/\r\n|\r/g, '\n').trimEnd().split('\n').length;
  let end = start;
  for (let i = 0; i < lines; i++) {
    const line = source.slice(end).match(/^[^\r\n]*(?:\r\n|\r|\n|$)/);
    if (!line?.[0]) break;
    end += line[0].length;
  }
  return { start, end };
}
async function deleteTask(target, position, index, version) {
  if (!canEditNote() || state.saving || state.navigating || state.inline || state.editing || target.dataset.markdownVersion !== version) return;
  const note = state.note;
  const task = taskTokens(marked.lexer(note.content, { gfm: true }))[index];
  if (!task || !/[ xX]/.test(note.content[position] || '')) return;
  const range = taskRemovalRange(note.content, position, task);
  const label = taskTextRange(note.content, position);
  const children = taskTokens([task]).length - 1;
  if (!confirm(`确定删除「${plainTaskText(note.content.slice(label.start, label.end))}」？${children > 0 ? `其下 ${children} 条子待办也会删除。` : ''}`)) return;
  if (state.note !== note || !canEditNote()) return;
  const content = note.content.slice(0, range.start) + note.content.slice(range.end);
  const scroll = window.scrollY;
  state.saving = true; setTaskBusy(true);
  try {
    state.note = await api('/api/note', { method: 'PUT', body: JSON.stringify({
      id: note.id, revision: note.revision, title: note.title, category: note.category, content,
    }) });
    renderNote(); window.scrollTo({ top: scroll, behavior: 'instant' });
    $('reader-save-state').textContent = '待办已删除并保存';
    try { await loadIndex(); } catch { notify('删除已保存，列表刷新失败，请刷新页面。', true); }
  } catch (error) {
    notify(error.status === 409 ? '文档已在其他地方更新，未删除任何事项。请重新打开后再操作。' : `删除未保存：${error.message}`, true);
  } finally { state.saving = false; setTaskBusy(false); }
}
function completionSection(source) {
  const headings = sourceTokens(source).filter((token) => token.type === 'heading' && token.depth <= 2);
  const index = headings.findIndex((token) => token.depth === 2 && token.text === '完成记录');
  if (index < 0 || !Number.isInteger(headings[index].sourceStart) ||
      (headings[index + 1] && !Number.isInteger(headings[index + 1].sourceStart))) return null;
  return { start: headings[index].sourceStart, end: headings[index + 1] ? headings[index + 1].sourceStart : source.length };
}
function addCompletionRecord(source, text, checked) {
  const section = completionSection(source);
  if (!section) return source;
  const now = new Date();
  const pad = (value) => String(value).padStart(2, '0');
  const date = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ${pad(now.getHours())}:${pad(now.getMinutes())}`;
  const newline = source.match(/\r\n|\r|\n/)?.[0] || '\n';
  const end = section.end;
  const prefix = source.slice(0, end);
  const separator = prefix.endsWith(newline + newline) ? '' : prefix.endsWith(newline) ? newline : newline + newline;
  // Log text is plain Markdown, not a second checkbox that changes progress.
  const safeText = text.replace(/[\\`*_{}\[\]<>]/g, '\\$&');
  return `${prefix}${separator}- ${date} ${checked ? '完成' : '重新打开'}：${safeText}${newline}${newline}${source.slice(end)}`;
}
function inlineContent(edit) {
  const newline = edit.note.content.match(/\r\n|\r|\n/)?.[0] || '\n';
  let text = edit.kind === 'body' ? edit.input.value.replace(/\r\n|\r|\n/g, newline) : edit.input.value;
  if (edit.visual) text = text.trim().replace(/[\\`*_{}\[\]<>#~&]/g, '\\$&');
  if (edit.kind === 'add') {
    return edit.note.content.slice(0, edit.start) + `${edit.start ? newline : ''}${edit.bullet || '-'} [ ] ${text.trim()}${newline}${newline}` + edit.note.content.slice(edit.end);
  }
  return edit.note.content.slice(0, edit.start) + text + edit.note.content.slice(edit.end);
}
function persistInlineDraft() {
  const edit = state.inline;
  if (!edit) return false;
  if (!dirty()) { removeDraft(edit.key); refreshDraftNotice(); return false; }
  const { id, title, category, revision, content } = edit.note;
  try {
    localStorage.setItem(edit.key, JSON.stringify({
      fields: { title, category, content: inlineContent(edit) }, note: { id, title, category, revision },
      original: JSON.stringify({ title, category, content }), updated: Date.now(),
    }));
    edit.status.textContent = edit.visual ? '' : '本机草稿已保留，点击保存后写入知识库。';
    refreshDraftNotice(); return true;
  } catch { edit.status.textContent = '草稿暂时无法保存，请及时保存或复制文字。'; return false; }
}
function closeInline(discard) {
  const edit = state.inline;
  if (!edit) return;
  if (discard) removeDraft(edit.key);
  state.inline = null;
  edit.form.remove();
  if (edit.placeholder) {
    const list = edit.placeholder.parentElement;
    edit.placeholder.remove();
    if (list?.classList.contains('task-new-list') && !list.children.length) list.remove();
  }
  if (edit.hidden) edit.hidden.hidden = false;
  setTaskBusy(state.saving);
  refreshDraftNotice();
}
function plainTaskText(source) {
  const span = document.createElement('span');
  span.innerHTML = DOMPurify.sanitize(marked.parseInline(source));
  span.querySelectorAll('img').forEach((img) => img.replaceWith(document.createTextNode(img.alt)));
  return span.textContent.trim();
}
async function finishTaskRow() {
  if (!state.inline?.visual) return !state.saving;
  return saveInline();
}
function startTaskRow(kind, host, start, end, bullet = '-') {
  if (state.editing || state.saving || state.navigating || !canEditNote() || state.inline) return;
  const note = { ...state.note };
  const form = document.createElement('div'); form.className = 'task-row-editor';
  const input = document.createElement('textarea'); input.className = 'task-row-input'; input.rows = 1;
  input.setAttribute('aria-label', kind === 'add' ? '新增待办内容' : '修改待办内容');
  input.placeholder = '填写待办事项…';
  input.value = kind === 'add' ? '' : plainTaskText(note.content.slice(start, end));
  const status = document.createElement('span'); status.className = 'task-row-status'; status.setAttribute('role', 'status');
  const retry = document.createElement('button'); retry.type = 'button'; retry.className = 'text-button task-row-retry'; retry.textContent = '重试保存'; retry.hidden = true;
  form.append(input, status, retry);
  let hidden = null, placeholder = null;
  if (kind === 'task') { hidden = host; host.hidden = true; host.after(form); }
  else {
    placeholder = document.createElement('li'); placeholder.className = 'task-item task-placeholder';
    const checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.disabled = true; checkbox.className = 'task-placeholder-checkbox'; checkbox.setAttribute('aria-label', '新待办');
    placeholder.append(checkbox, form); host.append(placeholder);
  }
  const edit = state.inline = { visual: true, kind, note, start, end, bullet, input, initial: input.value, form, hidden, placeholder, status, retry,
    key: draftPrefix() + crypto.randomUUID(), scroll: window.scrollY };
  const resize = () => { input.style.height = 'auto'; input.style.height = `${Math.max(28, input.scrollHeight)}px`; };
  input.addEventListener('input', () => { input.value = input.value.replace(/[\r\n]+/g, ' '); resize(); persistInlineDraft(); });
  input.addEventListener('keydown', (event) => {
    if (event.isComposing || event.keyCode === 229) return;
    if (event.key === 'Enter') { event.preventDefault(); saveInline(); }
    if (event.key === 'Escape' && !state.saving) { event.preventDefault(); closeInline(true); }
  });
  input.addEventListener('blur', () => { if (state.inline === edit && !edit.failed) saveInline(); });
  retry.addEventListener('pointerdown', (event) => event.preventDefault());
  retry.addEventListener('click', saveInline);
  setTaskBusy(false); resize(); input.focus({ preventScroll: true });
}
function startInline(kind, host, start, end) {
  if (state.editing || !canEditNote() || !canLeave()) return;
  closeInline(false);
  const note = { ...state.note };
  const form = document.createElement('form'); form.className = 'reader-inline-editor';
  const input = document.createElement(kind === 'body' ? 'textarea' : 'input');
  input.className = 'inline-input';
  if (kind !== 'body') input.type = 'text';
  input.setAttribute('aria-label', kind === 'body' ? '就地编辑 Markdown 正文' : kind === 'add' ? '新增待办内容' : '修改待办内容');
  input.value = kind === 'add' ? '' : note.content.slice(start, end);
  input.placeholder = '填写待办事项';
  const status = document.createElement('p'); status.className = 'inline-status';
  status.textContent = kind === 'body' ? '在这里修改 Markdown 正文，保存后回到原来的阅读位置。' : 'Enter 保存，Esc 取消。';
  const actions = document.createElement('div'); actions.className = 'inline-actions';
  const submit = document.createElement('button'); submit.type = 'submit'; submit.className = 'button primary inline-save'; submit.textContent = '保存';
  const cancel = document.createElement('button'); cancel.type = 'button'; cancel.className = 'button subtle inline-cancel'; cancel.textContent = '取消';
  actions.append(submit, cancel); form.append(input, actions, status);
  const hidden = kind === 'body' ? $('article') : null;
  if (hidden) { hidden.hidden = true; hidden.after(form); }
  else host.append(form);
  state.inline = { kind, note, start, end, input, initial: input.value, form, hidden, status,
    key: draftPrefix() + crypto.randomUUID(), scroll: window.scrollY };
  setTaskBusy(false);
  input.addEventListener('input', persistInlineDraft);
  cancel.addEventListener('click', () => { if (!state.saving) closeInline(true); });
  form.addEventListener('submit', (event) => { event.preventDefault(); saveInline(); });
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !event.isComposing && !state.saving) { event.preventDefault(); closeInline(true); }
  });
  input.focus({ preventScroll: true });
}
function saveInline() {
  const edit = state.inline;
  if (!edit) return Promise.resolve(true);
  if (edit.promise) return edit.promise;
  if (state.saving || state.navigating) return Promise.resolve(false);
  edit.promise = commitInline(edit);
  return edit.promise;
}
async function commitInline(edit) {
  if (edit.kind !== 'body' && !edit.input.value.trim()) {
    if (edit.visual) { closeInline(true); return true; }
    edit.status.textContent = '请填写待办内容。'; edit.input.focus(); return false;
  }
  if (!dirty() && edit.kind !== 'add') { closeInline(true); return true; }
  const content = inlineContent(edit);
  if (content === edit.note.content) { closeInline(true); return true; }
  persistInlineDraft();
  state.saving = true; setTaskBusy(true);
  edit.input.readOnly = true;
  edit.form.querySelectorAll('button').forEach((button) => { button.disabled = true; });
  edit.status.textContent = '正在保存…';
  try {
    const { id, revision, title, category } = edit.note;
    state.note = await api('/api/note', { method: 'PUT', body: JSON.stringify({ id, revision, title, category, content }) });
    closeInline(true); renderNote();
    window.scrollTo({ top: edit.scroll, behavior: 'instant' });
    $('reader-save-state').textContent = '修改已保存到本地';
    try { await loadIndex(); } catch { notify('修改已保存，列表刷新失败，请刷新页面。', true); }
    return true;
  } catch (error) {
    edit.failed = true;
    if (edit.retry) edit.retry.hidden = false;
    edit.status.textContent = error.status === 409 ? '文档已在其他地方更新，未覆盖新版本。修改已保留在这里及本机草稿，请复制保留后重新打开文档。' : `保存失败，修改已保留：${error.message}`;
    $('reader-save-state').textContent = '修改尚未保存';
    return false;
  } finally {
    state.saving = false; setTaskBusy(false);
    edit.input.readOnly = false;
    edit.form.querySelectorAll('button').forEach((button) => { button.disabled = false; });
    edit.promise = null;
  }
}
async function addReaderTask() {
  if (!await finishTaskRow()) return;
  if (!canEditNote()) return;
  const log = completionSection(state.note.content);
  const position = log ? log.start : state.note.content.length;
  const list = document.createElement('ul'); list.className = 'task-new-list';
  const heading = [...$('article').querySelectorAll('h2')].find((item) => item.textContent === '完成记录');
  if (heading) heading.before(list); else $('article').append(list);
  startTaskRow('add', list, position, position);
}
function addListButton(target, anchor, type, entry) {
  const button = document.createElement('button'); button.type = 'button'; button.className = 'task-list-add';
  button.textContent = '＋ 添加待办'; button.setAttribute('aria-label', '新增待办');
  anchor.after(button);
  const noteId = state.note?.id;
  button.addEventListener('pointerdown', (event) => event.preventDefault());
  button.addEventListener('click', async () => {
    if (!await finishTaskRow() || state.note?.id !== noteId) return;
    const current = taskMarkdown(state.note.content);
    const entries = type === 'list' ? current.lists : current.sections;
    const index = entries.findIndex((item) => item.group === entry.group && (type !== 'list' || item.order === entry.order));
    if (index < 0) {
      // A just-saved first row turns an empty day into a normal list.
      const listIndex = current.lists.findIndex((item) => item.group === entry.group);
      const list = target.querySelector(`[data-task-list-index="${listIndex}"]`);
      if (list) { const item = current.lists[listIndex]; startTaskRow('add', list, item.position, item.position, item.bullet); }
      return;
    }
    const item = entries[index];
    let host = target.querySelector(`[data-task-list-index="${index}"]`);
    if (type === 'section') {
      const heading = target.querySelector(`[data-task-section-index="${index}"]`);
      host = document.createElement('ul'); host.className = 'task-new-list'; heading.after(host);
    }
    if (host) startTaskRow('add', host, item.position, item.position, item.bullet);
  });
}
async function toggleTask(target, input, position, index, version) {
  const checked = input.checked;
  const revert = () => { input.checked = !checked; };
  if (state.saving || state.navigating || state.inline || (target.id === 'article' && !canEditNote()) || target.dataset.markdownVersion !== version) { revert(); return; }
  const preview = target.id === 'preview';
  if ((preview && !state.editing) || (!preview && (state.editing || !state.note))) { revert(); return; }
  const source = preview ? $('edit-content').value : state.note.content;
  let content = source.slice(0, position) + (checked ? 'x' : ' ') + source.slice(position + 1);
  const before = taskTokens(marked.lexer(source, { gfm: true }));
  const after = taskTokens(marked.lexer(content, { gfm: true }));
  // Fail closed if a source position ever points at an example or another task.
  if (!/[ xX]/.test(source[position] || '') || before.length !== after.length ||
      !before[index] || before[index].checked === checked ||
      after.some((task, i) => task.checked !== (i === index ? checked : before[i].checked))) {
    revert(); notify('这条待办暂时无法直接勾选，请在编辑文档中修改。', true); return;
  }
  const range = taskTextRange(source, position);
  content = addCompletionRecord(content, source.slice(range.start, range.end), checked);
  const hasRecord = content.length !== source.length;
  if (preview) {
    const editor = $('edit-content');
    const { selectionStart, selectionEnd, scrollTop } = editor;
    editor.value = content;
    editor.setSelectionRange(selectionStart, selectionEnd);
    editor.scrollTop = scrollTop;
    updatePreview();
    $('preview').querySelector(`input.task-checkbox[data-task-index="${index}"]`)?.focus({ preventScroll: true });
    return;
  }
  const note = state.note;
  state.saving = true;
  setTaskBusy(true);
  $('reader-save-state').textContent = '正在保存待办…';
  try {
    state.note = await api('/api/note', { method: 'PUT', body: JSON.stringify({
      id: note.id, revision: note.revision, title: note.title, category: note.category, content,
    }) });
    input.closest('li')?.classList.toggle('task-complete', checked);
    if (hasRecord) renderNote();
    $('note-date').textContent = `更新于 ${longDate(state.note.updated)}`;
    $('reader-save-state').textContent = '待办已保存到本地';
    updateTaskProgress();
    try { await loadIndex(); } catch { notify('待办已保存，列表刷新失败，请刷新页面。', true); }
  } catch (error) {
    revert();
    $('reader-save-state').textContent = '勾选未保存';
    notify(error.status === 409 ? '文档已在其他地方更新，本次勾选未保存。请重新打开文档后再勾选。' : `待办保存失败，已撤回勾选：${error.message}`, true);
  } finally {
    state.saving = false;
    setTaskBusy(false);
    (input.isConnected ? input : $('article').querySelector(`input.task-checkbox[data-task-index="${index}"]`))?.focus({ preventScroll: true });
  }
}
function renderMarkdown(target, content) {
  clearTimeout(diagramTimers.get(target));
  const version = String(++markdownVersion);
  target.dataset.markdownVersion = version;
  const tasks = taskMarkdown(content);
  target.innerHTML = DOMPurify.sanitize(tasks.html, {
    USE_PROFILES: { html: true }, SANITIZE_NAMED_PROPS: true,
    FORBID_TAGS: ['style', 'form', 'button', 'textarea', 'select', 'iframe'],
    FORBID_ATTR: ['style'],
  });
  bindNoteLinks(target);
  const readOnly = target.id === 'article' && !canEditNote();
  for (const input of target.querySelectorAll('input')) {
    input.disabled = true;
    input.classList.remove('task-checkbox');
    input.removeAttribute('data-task-position');
  }
  for (const input of target.querySelectorAll(`input[data-task-key="${tasks.key}"]`)) {
    const index = Number(input.dataset.taskIndex);
    const position = tasks.positions[index];
    input.classList.add('task-checkbox');
    input.closest('li')?.classList.add('task-item');
    input.closest('li')?.classList.toggle('task-complete', input.checked);
    input.setAttribute('aria-label', `待办：${input.closest('li')?.textContent.trim().slice(0, 160) || index + 1}`);
    if (!Number.isInteger(position)) { input.title = '请在编辑文档中修改这条待办'; continue; }
    input.dataset.taskPosition = position;
    input.disabled = readOnly || state.saving || Boolean(state.inline);
    input.addEventListener('change', () => toggleTask(target, input, position, index, version));
    if (target.id === 'article' && !readOnly) {
      const label = document.createElement('span'); label.className = 'task-label';
      label.tabIndex = 0; label.setAttribute('role', 'button'); label.title = '点击修改待办文字';
      label.setAttribute('aria-label', '编辑待办文字');
      let node = input.nextSibling;
      input.after(label);
      while (node && !(node.nodeType === 1 && /^(UL|OL|P|DIV|PRE|BLOCKQUOTE)$/.test(node.tagName))) {
        const next = node.nextSibling; label.append(node); node = next;
      }
      const noteId = state.note?.id;
      const edit = async () => {
        const previousLength = state.note?.content.length;
        const shiftsPosition = state.inline?.visual && state.inline.end <= position;
        if (!await finishTaskRow() || state.note?.id !== noteId) return;
        const currentPosition = position + (shiftsPosition ? state.note.content.length - previousLength : 0);
        const current = target.querySelector(`input.task-checkbox[data-task-position="${currentPosition}"]`);
        const currentLabel = current?.nextElementSibling;
        if (!currentLabel?.classList.contains('task-label')) return;
        const range = taskTextRange(state.note.content, Number(current.dataset.taskPosition));
        startTaskRow('task', currentLabel, range.start, range.end);
      };
      label.addEventListener('pointerdown', (event) => { if (!event.target.closest('a')) event.preventDefault(); });
      label.addEventListener('click', (event) => { if (!event.target.closest('a')) edit(); });
      label.addEventListener('keydown', (event) => {
        if (event.target === label && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); edit(); }
      });
      const remove = document.createElement('button'); remove.type = 'button';
      remove.className = 'task-delete'; remove.textContent = '删除';
      remove.setAttribute('aria-label', `删除待办：${label.textContent.trim()}`);
      remove.disabled = state.saving || Boolean(state.inline);
      remove.addEventListener('pointerdown', (event) => event.preventDefault());
      remove.addEventListener('click', () => deleteTask(target, position, index, version));
      label.after(remove);
    }
  }
  if (target.id === 'article' && !readOnly) {
    for (const list of target.querySelectorAll(`[data-task-list-key="${tasks.key}"]`)) addListButton(target, list, 'list', tasks.lists[Number(list.dataset.taskListIndex)]);
    if (state.note?.category === '待办清单') {
      for (const heading of target.querySelectorAll(`[data-task-section-key="${tasks.key}"]`)) addListButton(target, heading, 'section', tasks.sections[Number(heading.dataset.taskSectionIndex)]);
    }
  }
  const diagrams = [...target.querySelectorAll('pre > code.language-mermaid')].map((code) => {
    const source = code.textContent;
    const pre = code.parentElement;
    const figure = document.createElement('figure');
    figure.className = 'mermaid-diagram';
    const canvas = document.createElement('div');
    canvas.className = 'mermaid-canvas';
    canvas.setAttribute('aria-busy', 'true');
    canvas.textContent = '正在生成图表…';
    const details = document.createElement('details');
    details.className = 'mermaid-source';
    const summary = document.createElement('summary');
    summary.textContent = '查看图表源码';
    pre.replaceWith(figure);
    details.append(summary, pre);
    figure.append(canvas, details);
    return { source, figure, canvas, details };
  });
  if (diagrams.length) diagramTimers.set(target, setTimeout(() => {
    renderDiagrams(target, version, diagrams);
  }, target.id === 'preview' ? 220 : 0));
}
async function renderDiagrams(target, version, diagrams) {
  const cacheGeneration = diagramCacheGeneration;
  const current = () => target.isConnected && target.dataset.markdownVersion === version
    && cacheGeneration === diagramCacheGeneration && diagrams.some(({ figure }) => target.contains(figure));
  if (!current()) return;
  const loadFailure = (failedDiagrams) => {
    for (const { figure, canvas, details } of failedDiagrams) {
      figure.classList.add('mermaid-error');
      canvas.setAttribute('aria-busy', 'false');
      canvas.textContent = '图表组件加载失败，源码已保留。';
      details.open = true;
      figure.querySelector('.mermaid-retry')?.remove();
      const retry = document.createElement('button');
      retry.type = 'button'; retry.className = 'text-button mermaid-retry'; retry.textContent = '重试加载图表';
      retry.addEventListener('click', () => {
        if (!current()) return;
        for (const diagram of failedDiagrams) {
          diagram.figure.querySelector('.mermaid-retry')?.remove();
          diagram.figure.querySelector('.mermaid-zoom')?.remove();
          diagram.figure.classList.remove('mermaid-error');
          diagram.canvas.setAttribute('aria-busy', 'true');
          diagram.canvas.textContent = '正在生成图表…';
        }
        renderDiagrams(target, version, failedDiagrams);
      }, { once: true });
      canvas.after(retry);
    }
  };
  let renderer;
  try { renderer = await loadMermaid(); }
  catch {
    if (current()) loadFailure(diagrams);
    return;
  }
  for (const { source, figure, canvas, details } of diagrams) {
    if (!await waitForDiagramIdle(current)) return;
    const stage = document.createElement('div');
    stage.className = 'mermaid-stage';
    stage.setAttribute('aria-hidden', 'true');
    document.body.append(stage);
    try {
      const instanceId = `zhixu-mermaid-${++diagramId}`;
      let safeSvg = diagramCache.get(source);
      if (!safeSvg) {
        const { svg } = await renderer.render(instanceId, source, stage);
        if (!current()) return;
        safeSvg = DOMPurify.sanitize(svg, {
          USE_PROFILES: { svg: true, svgFilters: true }, ADD_TAGS: ['style'],
          FORBID_TAGS: ['foreignObject', 'script', 'image', 'a'],
          FORBID_ATTR: ['href', 'xlink:href'],
        });
      }
      const diagram = new DOMParser().parseFromString(safeSvg, 'image/svg+xml').documentElement;
      if (diagram.localName !== 'svg') throw new Error('Invalid SVG');
      const previousId = diagram.id;
      if (previousId && previousId !== instanceId) {
        diagram.id = instanceId;
        for (const style of diagram.querySelectorAll('style')) style.textContent = style.textContent.replaceAll(`#${previousId}`, `#${instanceId}`);
      }
      const bounds = (diagram.getAttribute('viewBox') || '').trim().split(/[\s,]+/).map(Number);
      if (bounds.length === 4 && bounds.every(Number.isFinite) && bounds[2] > 0 && bounds[3] > 0) {
        diagram.setAttribute('width', String(Math.ceil(bounds[2])));
        diagram.setAttribute('height', String(Math.ceil(bounds[3])));
      }
      // An SVG image keeps diagram CSS and IDs separate from the page's UI.
      const img = document.createElement('img');
      img.className = 'mermaid-image';
      img.alt = diagram.querySelector('title')?.textContent || 'Mermaid 图表（可展开查看源码）';
      img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(new XMLSerializer().serializeToString(diagram));
      await img.decode();
      if (!current()) return;
      diagramCache.delete(source);
      diagramCache.set(source, safeSvg);
      if (diagramCache.size > 20) diagramCache.delete(diagramCache.keys().next().value);
      canvas.replaceChildren(img);
      const zoom = document.createElement('button');
      zoom.type = 'button'; zoom.className = 'text-button mermaid-zoom';
      zoom.textContent = '放大图表';
      zoom.setAttribute('aria-pressed', 'false');
      zoom.addEventListener('click', () => {
        const expanded = figure.classList.toggle('mermaid-expanded');
        zoom.textContent = expanded ? '适应宽度' : '放大图表';
        zoom.setAttribute('aria-pressed', String(expanded));
      });
      canvas.after(zoom);
    } catch (error) {
      if (current()) {
        if (/failed to fetch dynamically imported module|error loading dynamically imported module|importing a module script failed|failed to load module script/i.test(String(error?.message || error))) {
          if (!mermaidUseFull) { mermaidUseFull = true; mermaidReady = null; }
          loadFailure([{ source, figure, canvas, details }]);
        } else {
          figure.classList.add('mermaid-error');
          canvas.textContent = '图表暂时无法显示，请检查 Mermaid 语法。源码已保留。';
          details.open = true;
        }
      }
    } finally {
      stage.remove();
      if (current()) canvas.setAttribute('aria-busy', 'false');
    }
  }
}
function renderNavigation() {
  $('total-count').textContent = state.all.length;
  $('all-notes').classList.toggle('active', !state.trash && !state.category);
  $('deleted-notes').classList.toggle('active', state.trash);
  $('categories').innerHTML = state.categories.map((category) => {
    const count = state.all.filter((note) => note.category === category || note.category.startsWith(category + '/')).length;
    const parts = category.split('/');
    return `<button class="nav-item ${!state.trash && state.category === category ? 'active' : ''}" data-category="${escapeHtml(category)}" title="${escapeHtml(category)}" style="padding-left:${11 + Math.min(parts.length - 1, 3) * 12}px">${icon('folder')}<span>${escapeHtml(parts.at(-1))}</span><span class="count">${count}</span></button>`;
  }).join('');
  $('category-options').innerHTML = state.categories.filter((category) => Auth.canCreate(category)).map((category) => `<option value="${escapeHtml(category)}"></option>`).join('');
  const title = state.trash ? '已删除' : state.category.split('/').at(-1) || '全部文档';
  $('list-title').textContent = title;
  $('current-category').textContent = state.trash ? '已删除' : state.category || '全部文档';
  updatePermissionUi();
}
function visibleNotes() {
  return state.matches.filter((note) => state.trash || !state.category || note.category === state.category || note.category.startsWith(state.category + '/'));
}
function renderList() {
  const notes = visibleNotes();
  $('list-count').textContent = `${notes.length} 篇`;
  $('list-subtitle').textContent = $('search').value.trim() ? '搜索结果' : state.trash ? '按删除时间排序 · 可恢复' : '最近更新';
  $('note-list').innerHTML = notes.length ? notes.map((note) => {
    const selected = note.trash_id ? state.note?.trash_id === note.trash_id : !state.note?.trash_id && state.note?.id === note.id;
    const date = note.deleted_at || note.updated;
    return `<button class="note-card ${selected ? 'selected' : ''}" data-id="${escapeHtml(note.id)}"${note.trash_id ? ` data-trash-id="${escapeHtml(note.trash_id)}"` : ''}><h2>${icon('file')}${escapeHtml(note.title)}</h2><p>${escapeHtml(note.excerpt || '这篇文档还没有正文。')}</p><div class="note-card-footer"><span>${escapeHtml(note.category)}</span><time datetime="${escapeHtml(date)}">${note.trash_id ? '删除于 ' : ''}${shortDate(date)}</time></div></button>`;
  }).join('') : `<div class="no-results"><strong>${state.trash ? '没有已删除的文档' : '还没有找到文档'}</strong>${state.trash ? '删除的文档会保留在这里。' : '换个关键词，或新建一篇笔记。'}</div>`;
}
async function loadIndex() {
  const data = await api('/api/notes');
  state.all = data.notes;
  state.categories = data.categories;
  renderNavigation();
  if (!state.trash && !$('search').value.trim()) {
    requestId++; state.matches = state.all; renderList();
  } else await search();
}
async function search() {
  const token = ++requestId;
  const trash = state.trash;
  try {
    const query = $('search').value.trim();
    const data = await api(`${trash ? '/api/trash' : '/api/notes'}?q=${encodeURIComponent(query)}`);
    if (token !== requestId || trash !== state.trash) return;
    state.matches = data.notes;
    renderList();
  } catch (error) { if (token === requestId) notify(error.message, true); }
}
function renderNote() {
  const note = state.note;
  if (!note) { showView('empty-view'); return; }
  showView('reader');
  const taskView = note.category === '待办清单';
  const deleted = Boolean(note.trash_id);
  $('reader-edit-body').hidden = taskView || !canEditNote();
  $('edit-note').hidden = taskView || !canEditNote();
  $('reader-add-task').hidden = !canEditNote();
  $('delete-note').hidden = deleted || !Auth.owner();
  $('export-note').hidden = deleted;
  $('restore-note').hidden = !deleted || !Auth.owner();
  $('deleted-banner').hidden = !deleted;
  $('note-category').textContent = note.category;
  $('note-date').textContent = deleted ? `删除于 ${longDate(note.deleted_at)}` : `更新于 ${longDate(note.updated)}`;
  renderMarkdown($('article'), note.content);
  $('reader-save-state').textContent = deleted ? '已删除 · 原文保留' : !canEditNote() ? '只读文档' : '已保存到知识库';
  updateTaskProgress();
  if (!$('article').querySelector('h1')) {
    const title = document.createElement('h1'); title.textContent = note.title; $('article').prepend(title);
  }
  const used = new Set();
  for (const heading of $('article').querySelectorAll('h1,h2,h3,h4,h5,h6')) {
    const base = NoteLinks.headingSlug(heading.textContent) || 'section';
    let id = base, suffix = 1;
    while (used.has(id)) id = `${base}-${suffix++}`;
    used.add(id); heading.dataset.anchor = id; heading.id = `note-heading-${id}`;
  }
  const headings = [...$('article').querySelectorAll('h2,h3')];
  headings.forEach((heading, index) => { heading.dataset.legacyAnchor = `section-${index}`; });
  $('toc').innerHTML = headings.length ? '<div class="toc-title">本文目录</div>' + headings.map((heading) => `<a class="${heading.tagName === 'H3' ? 'subheading' : ''}" href="#${heading.id}">${escapeHtml(heading.textContent)}</a>`).join('') : '';
  $('document-footer').textContent = deleted ? `原位置：${note.id} · 恢复后可继续编辑` : taskView && canEditNote() ? '修改自动保存到知识库' : `${note.content.length.toLocaleString()} 字符　/　Markdown 文档${!canEditNote() ? ' · 只读' : ''}`;
  document.title = `${note.title} · 知序`;
  renderList();
}
function navigationStatus(message = '') {
  let status = $('navigation-status');
  if (!status && message) {
    status = document.createElement('div'); status.id = 'navigation-status'; status.className = 'navigation-status';
    status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
    document.querySelector('.main-panel').prepend(status);
  }
  if (status) { status.textContent = message; status.hidden = !message; }
  document.querySelector('.main-panel').setAttribute('aria-busy', String(Boolean(message)));
}
function clearUnavailableNote(id) {
  removeCachedNote(id);
  if (state.note?.id !== id || state.trash) return;
  closeInline(false);
  state.note = null; state.editing = false; state.original = ''; state.draftKey = null;
  state.all = state.all.filter((note) => note.id !== id); state.matches = state.matches.filter((note) => note.id !== id);
  diagramCache.clear(); diagramCacheGeneration++;
  for (const target of [$('article'), $('preview')]) { clearTimeout(diagramTimers.get(target)); target.replaceChildren(); }
  $('toc').replaceChildren();
  for (const field of ['edit-title', 'edit-category', 'edit-content']) $(field).value = '';
  for (const field of ['note-category', 'note-date', 'document-footer', 'task-progress']) $(field).textContent = '';
  showView('empty-view'); renderNavigation(); renderList(); updateLocation(true);
  document.title = '知序 · 知识库';
}
async function refreshOpenNote(request, id, token, generation) {
  const current = () => token === selectionId && generation === noteCacheGeneration && Auth.authenticated && state.note?.id === id && !state.trash;
  try {
    const note = await request;
    if (!current() || state.editing || state.inline || dirty() || state.saving || state.navigating) return;
    if (note.revision !== state.note.revision || note.content !== state.note.content) {
      const scroll = window.scrollY;
      state.note = note; renderNote();
      window.scrollTo({ top: scroll, behavior: 'instant' });
    } else {
      // Permissions may change without a content revision. Keep the reader DOM
      // and scroll position when the document itself has not changed.
      state.note = note; updatePermissionUi();
      $('edit-note').hidden = note.category === '待办清单' || !canEditNote();
      $('reader-edit-body').hidden = note.category === '待办清单' || !canEditNote();
      $('reader-add-task').hidden = !canEditNote();
      $('article').querySelectorAll('input.task-checkbox').forEach((input) => { input.disabled = !canEditNote(); });
    }
  } catch (error) {
    if (!current()) return;
    if (error.status === 403 || error.status === 404) clearUnavailableNote(id);
    if (error.status !== 401) notify(error.status === 404 ? '文档不存在或已删除，可在「已删除」中查找。' : error.message, true);
  }
}
async function openNote(id, confirmed = false, category, options = {}) {
  if (!Auth.authenticated || (options.trash && !Auth.owner())) return false;
  if (state.inline?.visual && !await finishTaskRow()) return false;
  if (!confirmed && !canLeave()) return false;
  const token = ++selectionId;
  const generation = noteCacheGeneration;
  const cached = options.trash ? null : cachedNote(id);
  state.navigating = true;
  navigationStatus(cached ? '' : '正在读取文档…');
  for (const field of ['edit-title', 'edit-category', 'edit-content']) $(field).readOnly = true;
  try {
    const request = options.trash ? api('/api/trash/note?id=' + encodeURIComponent(id)) : fetchNote(id);
    const note = cached || await request;
    if (token !== selectionId || generation !== noteCacheGeneration) return false;
    const changedMode = state.trash !== Boolean(options.trash);
    state.trash = Boolean(options.trash);
    if (category !== undefined) state.category = category;
    if (options.linked) state.category = note.category;
    if (changedMode || options.linked) { $('search').value = ''; requestId++; }
    if (!changedMode && options.linked && !state.trash) state.matches = state.all;
    state.note = note;
    if (!state.trash) rememberLastNote(id);
    updateLocation(options.replace, options.fragment);
    renderNavigation();
    renderNote();
    scrollToFragment(options.fragment);
    if (cached) {
      // Navigation is complete once the cached document is visible. Refreshing
      // in the background must not lock subsequent navigation or editing.
      state.navigating = false; navigationStatus(); updatePermissionUi();
      refreshOpenNote(request, id, token, generation);
      if (changedMode) search();
    } else if (changedMode) await search();
    if (!state.trash) prefetchNeighbors(id, token);
    return true;
  } catch (error) {
    if (token !== selectionId || generation !== noteCacheGeneration || !Auth.authenticated) return false;
    if (error.status === 403 || error.status === 404) clearUnavailableNote(id);
    notify(error.status === 404 ? '文档不存在或已删除，可在「已删除」中查找。' : error.message, true); return false;
  }
  finally {
    if (token === selectionId) {
      state.navigating = false; navigationStatus();
      $('edit-content').readOnly = false; updatePermissionUi();
    }
  }
}
async function chooseCategory(category) {
  if (state.inline?.visual && !await finishTaskRow()) return;
  if (!canLeave()) return;
  setNoteListCollapsed(false);
  selectionId++;
  const notes = (state.trash ? state.all : state.matches).filter((note) => !category || note.category === category || note.category.startsWith(category + '/'));
  if (notes.length) await openNote(notes[0].id, true, category);
  else {
    const wasTrash = state.trash;
    state.trash = false; state.category = category; state.note = null;
    if (wasTrash) { state.matches = state.all; $('search').value = ''; requestId++; }
    updateLocation(); showView('empty-view'); renderNavigation(); renderList();
    if (wasTrash) await search();
  }
}
async function showDeleted(options = {}) {
  if (!Auth.owner()) return false;
  if (state.inline?.visual && !await finishTaskRow()) return false;
  if (!canLeave()) return false;
  setNoteListCollapsed(false);
  state.navigating = true;
  for (const field of ['edit-title', 'edit-category', 'edit-content']) $(field).readOnly = true;
  try {
    const data = await api('/api/trash');
    state.navigating = false;
    if (data.notes.length) return await openNote(data.notes[0].trash_id, true, '', { trash: true, replace: options.replace });
    state.trash = true; state.category = ''; state.note = null; state.matches = []; $('search').value = ''; requestId++;
    updateLocation(options.replace); showView('empty-view'); renderNavigation(); renderList(); return true;
  } catch (error) { notify(error.message, true); return false; }
  finally {
    state.navigating = false;
    for (const field of ['edit-title', 'edit-category', 'edit-content']) $(field).readOnly = false;
  }
}
async function deleteNote() {
  if (!Auth.owner() || !state.note || state.note.trash_id || !await finishTaskRow() || !canLeave()) return;
  if (!confirm(`将「${state.note.title}」移到「已删除」？之后可以恢复。`)) return;
  const note = state.note;
  persistDraft(); state.saving = true; setTaskBusy(true);
  try {
    const deleted = await api('/api/note', { method: 'DELETE', body: JSON.stringify({ id: note.id, revision: note.revision }) });
    state.note = deleted; state.trash = true; state.category = ''; $('search').value = ''; requestId++;
    updateLocation(); renderNavigation(); renderNote();
    notify('已移到「已删除」，可以随时恢复。');
    try { await loadIndex(); } catch { notify('文档已移到「已删除」，请刷新列表。', true); }
  } catch (error) { notify(error.status === 409 ? '文档已有新修改，本次未删除。请重新打开后再试。' : error.message, true); }
  finally { state.saving = false; setTaskBusy(false); }
}
async function restoreNote() {
  if (!Auth.owner() || !state.note?.trash_id || state.saving || state.navigating) return;
  state.saving = true; setTaskBusy(true);
  try {
    state.note = await api('/api/trash/restore', { method: 'POST', body: JSON.stringify({ trash_id: state.note.trash_id }) });
    state.trash = false; state.category = state.note.category; $('search').value = ''; requestId++;
    rememberLastNote(state.note.id); updateLocation(); renderNavigation(); renderNote();
    notify('文档已恢复到原位置。');
    try { await loadIndex(); } catch { notify('文档已恢复，请刷新列表。', true); }
  } catch (error) { notify(error.message, true); }
  finally { state.saving = false; setTaskBusy(false); }
}
function updatePreview() {
  renderMarkdown($('preview'), $('edit-content').value);
  $('word-count').textContent = `${$('edit-content').value.length.toLocaleString()} 字符`;
  $('save-state').textContent = dirty() ? '有未保存的修改' : (state.note ? '编辑文档' : '新建文档');
  return persistDraft();
}
function startEdit(note = null, imported = null) {
  if (!Auth.authenticated || !canLeave() || (note ? !canEditNote(note) : !canCreateIn())) return;
  if (state.trash) { state.trash = false; state.category = ''; state.matches = state.all; $('search').value = ''; requestId++; renderNavigation(); }
  selectionId++;
  state.draftKey = draftPrefix() + (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`);
  state.note = note;
  $('edit-title').value = imported?.title || note?.title || '';
  $('edit-category').value = imported?.category || note?.category || state.category || (Auth.owner() ? '未分类' : state.categories.find((category) => Auth.canCreate(category)) || '');
  $('edit-content').value = imported?.content ?? note?.content ?? '';
  state.original = JSON.stringify(fields());
  showView('editor'); renderList(); updatePermissionUi(); updatePreview();
  if (imported) { state.original = ''; updatePreview(); }
  $('edit-title').focus();
}
function taskListDate() {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
}
async function ensureTaskLists() {
  if (!Auth.owner()) return null;
  if (taskListsRequest) return taskListsRequest;
  const day = taskListDate();
  taskListsRequest = (async () => {
    const lists = await api('/api/task-lists/ensure', { method: 'POST', body: '{}' });
    await loadIndex();
    if (state.note?.id === lists.weekly.id && state.note.revision !== lists.weekly.revision && !state.editing && !state.inline && !state.saving && !lists.weekly.trash_id) {
      state.note = lists.weekly; renderNote();
    }
    taskListsDay = day;
    taskListsWeek = lists.week_start;
    return lists;
  })();
  try { return await taskListsRequest; }
  finally { taskListsRequest = null; }
}
async function refreshTaskListsForDate() {
  if (!Auth.owner() || !initialized || document.hidden || state.saving || state.navigating || state.editing || state.inline || taskListsDay === taskListDate()) return;
  try {
    const previous = state.note;
    const previousWeek = taskListsWeek;
    const lists = await ensureTaskLists();
    if (previousWeek && previousWeek !== lists.week_start && state.note === previous && previous?.category === '待办清单' && /^一周待办 · \d{4}-\d{2}-\d{2}$/.test(previous.title) && !lists.weekly.trash_id && previous.id !== lists.weekly.id) {
      await openNote(lists.weekly.id, false, '待办清单', { replace: true });
    }
  }
  catch (error) { notify(`待办清单更新失败：${error.message}`, true); }
}
async function openTaskList(kind) {
  if (!Auth.owner() || !await finishTaskRow() || !canLeave()) return;
  state.saving = true; setTaskBusy(true);
  for (const id of ['new-weekly', 'open-general-tasks']) $(id).disabled = true;
  try {
    const lists = await ensureTaskLists();
    const note = lists[kind];
    await openNote(note.trash_id || note.id, true, note.category, { trash: Boolean(note.trash_id), linked: true });
    if (note.trash_id) notify('这份清单已移到「已删除」，点击「恢复文档」后可继续使用。');
  } catch (error) { notify(error.message, true); }
  finally {
    state.saving = false; setTaskBusy(false);
    for (const id of ['new-weekly', 'open-general-tasks']) $(id).disabled = false;
  }
}
function insertTask() {
  if (!state.editing || state.saving || state.navigating) return;
  const editor = $('edit-content');
  const start = editor.selectionStart;
  const prefix = start > 0 && editor.value[start - 1] !== '\n' ? '\n' : '';
  const selected = editor.value.slice(start, editor.selectionEnd);
  const text = selected || '待办事项';
  const insertion = prefix + text.split(/\r?\n/).map((line) => `- [ ] ${line || '待办事项'}`).join('\n') + '\n';
  editor.setRangeText(insertion, start, editor.selectionEnd, 'end');
  updatePreview();
  editor.focus();
}
async function save() {
  if (!Auth.authenticated || !state.editing || state.saving || state.navigating) return;
  const data = fields();
  if (state.note ? !canEditNote() : !canCreateIn(data.category)) { notify('你没有此文档或分类的编辑权限。', true); return; }
  if (!data.title) { $('edit-title').focus(); notify('请先填写文档标题。', true); return; }
  persistDraft();
  state.saving = true;
  setTaskBusy(true);
  for (const id of ['edit-title', 'edit-category', 'edit-content']) $(id).readOnly = true;
  $('save-note').disabled = true;
  $('save-state').textContent = '正在保存…';
  try {
    const existing = state.note;
    const note = await api(existing ? '/api/note' : '/api/notes', { method: existing ? 'PUT' : 'POST', body: JSON.stringify({ ...data, ...(existing ? { id: existing.id, revision: existing.revision } : {}) }) });
    state.note = note;
    state.original = JSON.stringify(data);
    state.category = note.category;
    $('search').value = '';
    requestId++;
    rememberLastNote(note.id);
    updateLocation(true);
    removeDraft(state.draftKey);
    state.draftKey = null;
    renderNote();
    notify('文档已保存到知识库');
    refreshDraftNotice();
    try { await loadIndex(); } catch { notify('文档已保存，列表刷新失败，请刷新页面。', true); }
  } catch (error) { notify(error.message, true); $('save-state').textContent = '保存未完成，正文仍保留在编辑器'; }
  finally {
    state.saving = false;
    setTaskBusy(false);
    $('save-note').disabled = false;
    $('edit-content').readOnly = false; updatePermissionUi();
  }
}

$('all-notes').addEventListener('click', () => chooseCategory(''));
$('deleted-notes').addEventListener('click', () => showDeleted());
$('deleted-notes').addEventListener('pointerdown', (event) => event.preventDefault());
$('delete-note').addEventListener('click', deleteNote);
$('delete-note').addEventListener('pointerdown', (event) => event.preventDefault());
$('restore-note').addEventListener('click', restoreNote);
$('toc').addEventListener('click', async (event) => {
  const link = event.target.closest('a[href^="#"]');
  if (!link || event.button || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  if (!await finishTaskRow()) return;
  const fragment = link.getAttribute('href').slice(1);
  updateLocation(false, fragment); scrollToFragment(fragment);
});
$('categories').addEventListener('click', (event) => { const button = event.target.closest('[data-category]'); if (button) chooseCategory(button.dataset.category); });
$('note-list').addEventListener('click', (event) => { const button = event.target.closest('[data-id]'); if (button) openNote(button.dataset.trashId || button.dataset.id, false, undefined, { trash: Boolean(button.dataset.trashId) }); });
for (const type of ['pointerover', 'focusin', 'touchstart']) $('note-list').addEventListener(type, (event) => {
  const button = event.target.closest('.note-card[data-id]');
  if (button && !button.dataset.trashId && !button.contains(event.relatedTarget)) prefetchNote(button.dataset.id);
}, { passive: true });
$('search').addEventListener('input', () => { requestId++; clearTimeout(searchTimer); searchTimer = setTimeout(search, 180); });
document.querySelectorAll('.new-note').forEach((button) => button.addEventListener('click', () => startEdit()));
$('new-weekly').addEventListener('click', () => openTaskList('weekly'));
$('new-weekly').addEventListener('pointerdown', (event) => event.preventDefault());
$('open-general-tasks').addEventListener('click', () => openTaskList('general'));
$('open-general-tasks').addEventListener('pointerdown', (event) => event.preventDefault());
$('toggle-note-list').addEventListener('pointerdown', (event) => event.preventDefault());
$('toggle-note-list').addEventListener('click', () => setNoteListCollapsed(!document.body.classList.contains('note-list-collapsed')));
$('insert-task').addEventListener('click', insertTask);
$('reader-edit-body').addEventListener('click', () => { if (state.note) startInline('body', $('article'), 0, state.note.content.length); });
$('reader-add-task').addEventListener('click', addReaderTask);
$('reader-add-task').addEventListener('pointerdown', (event) => event.preventDefault());
$('edit-note').addEventListener('click', () => startEdit(state.note));
$('cancel-edit').addEventListener('click', () => {
  if (!canLeave()) return;
  if (state.note) openNote(state.note.id, true);
  else { state.editing = false; renderNote(); }
});
$('save-note').addEventListener('click', save);
$('export-note').addEventListener('click', () => { if (state.note) location.href = `/api/export?id=${encodeURIComponent(state.note.id)}`; });
for (const id of ['edit-title', 'edit-category', 'edit-content']) $(id).addEventListener('input', updatePreview);
document.querySelectorAll('[data-view][role=tab]').forEach((button) => button.addEventListener('click', () => {
  $('editor-panes').dataset.view = button.dataset.view;
  document.querySelectorAll('[role=tab]').forEach((tab) => tab.setAttribute('aria-selected', String(tab === button)));
}));
$('import-note').addEventListener('click', () => { if (!state.saving) $('import-file').click(); });
$('import-file').addEventListener('change', async () => {
  const file = $('import-file').files[0];
  if (!file) return;
  try {
    if (file.size > 2 * 1024 * 1024) throw new Error('请选择小于 2 MB 的 Markdown 文件。');
    const content = await file.text();
    startEdit(null, { title: file.name.replace(/\.(md|markdown)$/i, ''), content });
  } catch (error) { notify(error.message, true); }
  finally { $('import-file').value = ''; }
});
$('add-category').addEventListener('click', () => { if (Auth.owner()) { $('new-category').value = ''; $('category-dialog').showModal(); } });
$('close-category').addEventListener('click', () => $('category-dialog').close());
$('category-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    const name = normalizedCategory($('new-category').value);
    await api('/api/categories', { method: 'POST', body: JSON.stringify({ name }) });
    $('category-dialog').close();
    await loadIndex();
    notify(`分类「${name}」已创建`);
  } catch (error) { notify(error.message, true); }
});
window.addEventListener('beforeunload', (event) => { persistDraft(); if (dirty() || state.saving) { event.preventDefault(); event.returnValue = ''; } });
document.addEventListener('visibilitychange', () => { if (document.hidden) persistDraft(); else refreshTaskListsForDate(); });
window.addEventListener('focus', refreshTaskListsForDate);
setInterval(refreshTaskListsForDate, 60000);
document.addEventListener('keydown', (event) => {
  if (!Auth.authenticated) return;
  const input = /INPUT|TEXTAREA/.test(event.target.tagName) || event.target.isContentEditable;
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') { event.preventDefault(); if (state.inline) saveInline(); else save(); }
  if (input || event.ctrlKey || event.metaKey || event.altKey || document.querySelector('dialog[open]')) return;
  if (event.key === '/') { event.preventDefault(); setNoteListCollapsed(false); $('search').focus(); }
  if (event.key.toLowerCase() === 'n') { event.preventDefault(); startEdit(); }
});
function storagePrefix() { return `zhixu.account.${state.library}.${encodeURIComponent(state.user?.id || '')}.`; }
function migrateOwnerStorage(session) {
  if (!Auth.owner() || session.user.id !== 'fl' || !session.legacy_storage_prefix) return;
  const prefix = storagePrefix(), legacy = session.legacy_storage_prefix;
  try {
    if (localStorage.getItem(prefix + 'legacyMigrated') === 'true') return;
    const keys = Array.from({ length: localStorage.length }, (_, index) => localStorage.key(index));
    for (const key of keys) {
      if (!key?.startsWith(legacy)) continue;
      const suffix = key.slice(legacy.length);
      if (suffix !== 'lastNote' && suffix !== 'noteListCollapsed' && !suffix.startsWith('draft.')) continue;
      if (localStorage.getItem(prefix + suffix) === null) localStorage.setItem(prefix + suffix, localStorage.getItem(key));
    }
    localStorage.setItem(prefix + 'legacyMigrated', 'true');
  } catch { notify('旧的本机草稿仍保留在浏览器中，本次迁移未完成。请检查浏览器存储空间。', true); }
}
function draftPrefix() { return storagePrefix() + 'draft.'; }
function rememberLastNote(id) { try { localStorage.setItem(storagePrefix() + 'lastNote', id); } catch {} }
function lastNoteId() { try { return localStorage.getItem(storagePrefix() + 'lastNote'); } catch { return null; } }
function getDrafts() {
  if (!Auth.authenticated || !state.user) return [];
  const drafts = [];
  try {
    for (let index = 0; index < localStorage.length; index++) {
      const key = localStorage.key(index);
      if (!key?.startsWith(draftPrefix())) continue;
      try {
        const draft = JSON.parse(localStorage.getItem(key));
        if (!draft || !draft.fields || typeof draft.fields.content !== 'string' || typeof draft.fields.title !== 'string' || typeof draft.fields.category !== 'string' || typeof draft.original !== 'string') continue;
        drafts.push({ ...draft, key });
      } catch {}
    }
  } catch {}
  return drafts.sort((a, b) => b.updated - a.updated);
}
function removeDraft(key) {
  if (!key) return;
  try { localStorage.removeItem(key); } catch { notify('文档保存状态不受影响，但本机草稿暂时无法清除。', true); }
}
function refreshDraftNotice() {
  if (!Auth.authenticated || !state.user) return;
  const count = getDrafts().length;
  $('draft-count').textContent = count;
  $('open-drafts').hidden = false;
  $('draft-banner').hidden = !count;
  $('draft-banner-text').textContent = `有 ${count} 份本机草稿，尚未保存到知识库。`;
  document.body.classList.toggle('has-drafts', Boolean(count));
}
function persistDraft() {
  if (!Auth.authenticated || !state.user) return false;
  if (state.inline) return persistInlineDraft();
  if (!state.editing || !state.draftKey) return false;
  if (!dirty()) {
    removeDraft(state.draftKey);
    $('draft-status').textContent = '修改会保留为本机草稿；点击保存后写入知识库。';
    refreshDraftNotice();
    return false;
  }
  try {
    localStorage.setItem(state.draftKey, JSON.stringify({ fields: fields(), note: state.note ? { id: state.note.id, title: state.note.title, category: state.note.category, revision: state.note.revision } : null, original: state.original, updated: Date.now() }));
    $('draft-status').textContent = '本机草稿已保留；点击「保存文档」后写入知识库。';
  } catch {
    $('draft-status').textContent = '浏览器无法保存草稿，请及时保存文档或复制正文。';
    return false;
  }
  refreshDraftNotice();
  return true;
}
function showDrafts() {
  if (!Auth.authenticated) return;
  const drafts = getDrafts();
  $('draft-list').innerHTML = drafts.length ? drafts.map((draft) => `<div class="draft-row"><div><strong>${escapeHtml(draft.fields.title || '未命名文档')}</strong><small>${escapeHtml(draft.fields.category)} · ${longDate(draft.updated)}</small></div><button class="button outline" data-restore="${escapeHtml(draft.key)}">恢复</button><button class="text-button" data-discard="${escapeHtml(draft.key)}">删除草稿</button></div>`).join('') : '<p class="muted">还没有未保存的草稿。</p>';
  if (!$('drafts-dialog').open) $('drafts-dialog').showModal();
}
$('open-drafts').addEventListener('click', showDrafts);
$('review-drafts').addEventListener('click', showDrafts);
$('close-drafts').addEventListener('click', () => $('drafts-dialog').close());
$('draft-list').addEventListener('click', (event) => {
  const restore = event.target.closest('[data-restore]');
  const discard = event.target.closest('[data-discard]');
  if (restore && canLeave()) {
    const draft = getDrafts().find((item) => item.key === restore.dataset.restore);
    if (!draft) return;
    const available = draft.note && state.all.find((note) => note.id === draft.note.id);
    if (draft.note ? !available || !canEditNote(available) : !canCreateIn(draft.fields.category)) {
      notify('当前没有这份草稿对应文档或分类的编辑权限，请联系风落。', true); return;
    }
    if (state.trash) { state.trash = false; state.category = ''; state.matches = state.all; $('search').value = ''; requestId++; renderNavigation(); }
    state.note = draft.note ? { ...draft.note, permissions: available.permissions } : null; state.draftKey = draftPrefix() + crypto.randomUUID(); state.original = draft.original;
    $('edit-title').value = draft.fields.title; $('edit-category').value = draft.fields.category; $('edit-content').value = draft.fields.content;
    showView('editor'); renderList(); updatePermissionUi();
    if (updatePreview()) removeDraft(draft.key);
    refreshDraftNotice(); $('drafts-dialog').close();
    notify('已恢复草稿，点击保存后写入知识库。');
  } else if (discard && confirm('确定删除这份本机草稿？知识库中已保存的文档不受影响。')) {
    removeDraft(discard.dataset.discard); refreshDraftNotice(); showDrafts();
  }
});

async function openLocation() {
  if (!Auth.authenticated) return false;
  const params = new URLSearchParams(location.search);
  let fragment = '';
  try { fragment = decodeURIComponent(location.hash.slice(1)); } catch { notify('链接中的小节地址无效。', true); }
  if (params.has('deleted')) return openNote(params.get('deleted'), false, '', { trash: true, replace: true, fragment });
  if (params.get('view') === 'deleted') return showDeleted({ replace: true });
  if (params.has('note')) return openNote(params.get('note'), false, undefined, { linked: true, replace: true, fragment });
  const last = lastNoteId();
  const note = state.all.find((item) => item.id === last) || state.all.find((item) => item.title === '欢迎来到知序') || state.all[0];
  if (note) return openNote(note.id, false, '', { replace: true, fragment });
  if (!canLeave()) return false;
  state.note = null; state.trash = false; state.category = ''; state.matches = []; showView('empty-view'); renderNavigation(); renderList(); return true;
}
window.addEventListener('popstate', async (event) => {
  if (!Auth.authenticated || new URLSearchParams(location.hash.slice(1)).has('owner-token')) return;
  const previous = routeIndex;
  const next = event.state?.noteRouteIndex;
  if (revertingHistory) { revertingHistory = false; return; }
  if (Number.isInteger(next)) routeIndex = next;
  if (!await openLocation()) {
    if (Number.isInteger(next) && next !== previous) {
      routeIndex = previous; revertingHistory = true; history.go(previous - next);
    } else updateLocation(true);
  }
});
async function init(session) {
  try {
    state.user = session.user;
    state.library = session.library_id;
    migrateOwnerStorage(session);
    try { setNoteListCollapsed(localStorage.getItem(storagePrefix() + 'noteListCollapsed') === 'true', false); } catch {}
    refreshDraftNotice();
    await loadIndex();
    if (!await openLocation() && !state.note) showView('empty-view');
    try {
      const lists = await ensureTaskLists();
      if (lists && !state.note && !state.editing && !location.search && !lists.weekly.trash_id) await openNote(lists.weekly.id, false, '待办清单', { replace: true });
    } catch (error) { notify(`待办清单初始化失败：${error.message}`, true); }
    initialized = true;
  } catch (error) {
    $('note-list').innerHTML = '<p class="no-results">连接失败，请确认本地服务已启动后刷新页面。</p>';
    notify(error.message, true);
  }
}
Auth.start(init, clearAppForLogout, async () => {
  if (!await finishTaskRow() || !canLeave()) return false;
  persistDraft(); return true;
});
