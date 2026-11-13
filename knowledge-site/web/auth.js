'use strict';
const Auth = (() => {
  const el = (id) => document.getElementById(id);
  const html = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  let session = null, generation = 0, checking = false, submitting = false, pendingId = null, onReady, onLost, beforeLogout;
  let users = [], catalog = { notes: [], categories: [] }, editingId = null;
  const owner = () => Boolean(session?.user?.owner || session?.user?.role === 'owner');
  const grantMatches = (grant, category) => grant.scope === 'category' && (category.toLowerCase() === grant.path.toLowerCase() || category.toLowerCase().startsWith(grant.path.toLowerCase() + '/'));
  const canCreate = (category) => owner() || Boolean(session?.user?.grants?.some((grant) => grant.access === 'edit' && (grant.scope === 'all' || (!category ? grant.scope === 'category' : grantMatches(grant, category)))));
  const storageKey = () => session ? `${session.library_id}.${encodeURIComponent(session.user.id)}` : '';

  function lock(message = '填写 ID，申请访问知识库。') {
    const hadSession = Boolean(session);
    generation++; session = null; pendingId = null;
    document.body.classList.add('auth-locked');
    el('login-view').hidden = false;
    document.querySelectorAll('dialog[open]').forEach((dialog) => dialog.close());
    el('login-status').textContent = message;
    el('login-submit').disabled = false;
    el('login-submit').textContent = '申请访问';
    el('account-name').textContent = ''; el('account-id').textContent = '';
    el('access-users').replaceChildren(); el('access-requests').replaceChildren(); el('access-grants').replaceChildren();
    el('access-user-form').reset(); users = []; catalog = { notes: [], categories: [] };
    if (hadSession) onLost?.();
  }

  async function api(path, options = {}) {
    const current = generation;
    const method = (options.method || 'GET').toUpperCase();
    const headers = { 'Content-Type': 'application/json', ...options.headers };
    if (!['GET', 'HEAD', 'OPTIONS'].includes(method) && session?.csrf_token) headers['X-CSRF-Token'] = session.csrf_token;
    const response = await fetch(path, { ...options, headers, credentials: 'same-origin', cache: 'no-store' });
    let data;
    try { data = await response.json(); } catch { throw new Error('服务器返回了无效响应，请稍后重试。'); }
    if (response.status === 401 && session) lock('登录已失效，请重新填写 ID 申请访问。');
    if (!response.ok) { const error = new Error(data.error || `请求失败 (${response.status})`); error.status = response.status; throw error; }
    if (current !== generation) throw new Error('登录状态已改变，请重新打开文档。');
    return data;
  }

  async function accept(data) {
    if (!data.authenticated || !data.user) {
      if (session) lock('登录已失效，请重新填写 ID 申请访问。');
      if (data.request_status === 'pending') {
        el('login-status').textContent = '申请已提交，等待风落批准此设备。批准后会自动进入。';
        el('login-submit').textContent = '等待批准';
        el('login-submit').disabled = pendingId === el('login-id').value.trim().toLowerCase();
      } else {
        pendingId = null; el('login-submit').disabled = false; el('login-submit').textContent = '申请访问';
        if (data.request_status === 'rejected') el('login-status').textContent = '本次设备申请未通过，请联系风落确认后重新申请。';
        else if (data.request_status === 'expired') el('login-status').textContent = '上次申请已过期，请重新提交。';
        else if (!submitting) el('login-status').textContent = '填写 ID，申请访问知识库。';
      }
      return;
    }
    const changed = !session || session.library_id !== data.library_id || JSON.stringify(session.user) !== JSON.stringify(data.user);
    if (changed && session) { onLost?.(); generation++; }
    session = data;
    el('account-name').textContent = data.user.display_name || data.user.id;
    el('account-id').textContent = `${data.user.id}${owner() ? ' · Owner' : ''}`;
    el('manage-access').hidden = !owner();
    el('login-view').hidden = true; document.body.classList.remove('auth-locked');
    if (changed) await onReady?.(data);
  }

  async function check() {
    if (checking || submitting || document.hidden) return;
    checking = true;
    try { await accept(await api('/api/auth/session')); }
    catch (error) { if (!session) el('login-status').textContent = `连接失败：${error.message}`; }
    finally { checking = false; }
  }

  function grantRow(grant = { scope: 'category', path: '', access: 'read' }) {
    const row = document.createElement('div'); row.className = 'grant-row';
    row.innerHTML = '<select class="grant-scope" aria-label="授权类型"><option value="category">分类</option><option value="note">文档</option><option value="all">全部文档</option></select><select class="grant-path" aria-label="授权范围" required></select><select class="grant-access" aria-label="权限"><option value="read">可阅读</option><option value="edit">可编辑</option></select><button type="button" class="text-button grant-remove" aria-label="移除这条权限">移除</button>';
    const scope = row.querySelector('.grant-scope'), path = row.querySelector('.grant-path');
    const fill = (selected = '') => {
      const all = scope.value === 'all';
      path.disabled = all; path.required = !all;
      if (all) { path.innerHTML = '<option value="">当前及以后新增的文档</option>'; return; }
      const choices = scope.value === 'category' ? catalog.categories.map((name) => ({ value: name, text: name })) : catalog.notes.map((note) => ({ value: note.id, text: `${note.title} · ${note.category}` }));
      if (selected && !choices.some((item) => item.value === selected)) choices.push({ value: selected, text: selected + '（当前不存在）' });
      path.innerHTML = '<option value="">请选择…</option>' + choices.map((item) => `<option value="${html(item.value)}">${html(item.text)}</option>`).join('');
      path.value = selected;
    };
    scope.value = grant.scope; fill(grant.path); row.querySelector('.grant-access').value = grant.access;
    scope.addEventListener('change', () => fill());
    row.querySelector('.grant-remove').addEventListener('click', () => row.remove());
    el('access-grants').append(row);
  }

  function editUser(user = null) {
    editingId = user?.id || null;
    el('access-form-title').textContent = user ? `编辑账号 · ${user.id}` : '新建账号';
    el('access-user-id').value = user?.id || ''; el('access-user-id').readOnly = Boolean(user);
    el('access-user-name').value = user?.display_name || '';
    el('access-user-enabled').checked = user?.enabled !== false;
    el('access-user-enabled').disabled = !user;
    el('access-save-user').textContent = user ? '保存权限' : '创建账号';
    el('access-status').textContent = ''; el('access-grants').replaceChildren();
    (user?.grants || []).forEach(grantRow);
  }

  async function refreshAdmin() {
    if (!owner()) return;
    const [accounts, pending] = await Promise.all([api('/api/admin/users'), api('/api/admin/requests')]);
    users = accounts.users;
    el('access-users').innerHTML = users.map((user) => `<button class="access-user" type="button" data-user-id="${html(user.id)}"${user.owner || user.role === 'owner' ? ' disabled' : ''}><strong>${html(user.display_name || user.id)}</strong><span>${html(user.id)} · ${user.owner || user.role === 'owner' ? 'Owner' : user.enabled === false ? '已停用' : `${user.grants?.length || 0} 条权限`}</span></button>`).join('');
    el('access-requests').innerHTML = pending.requests.length ? pending.requests.map((request) => {
      const stamp = request.created_at || request.requested_at;
      const date = new Date(typeof stamp === 'number' ? stamp * 1000 : stamp).toLocaleString('zh-CN');
      const enabled = users.some((user) => user.id === request.user_id && user.enabled !== false);
      return `<div class="access-request"><div><strong>${html(request.display_name || request.user_id)} <small>(${html(request.user_id)})</small></strong><p>${html(request.device_label || request.device_name || '浏览器')}${request.ip ? ' · ' + html(request.ip) : ''}</p><time>${html(date)}</time>${enabled ? '' : '<p>请先创建并启用此 ID 的账号，分配权限后再批准。</p>'}</div><button class="button outline" data-request-action="reject" data-request-id="${html(request.id)}">拒绝</button><button class="button primary" data-request-action="approve" data-request-id="${html(request.id)}"${enabled ? '' : ' disabled'}>批准</button></div>`;
    }).join('') : '<p class="access-empty">暂时没有等待批准的设备。</p>';
  }

  el('login-form').addEventListener('submit', async (event) => {
    event.preventDefault(); if (submitting) return;
    submitting = true; el('login-submit').disabled = true; el('login-status').textContent = '正在提交申请…';
    try { await api('/api/auth/request', { method: 'POST', body: JSON.stringify({ id: el('login-id').value.trim() }) }); pendingId = el('login-id').value.trim().toLowerCase(); await accept(await api('/api/auth/session')); }
    catch (error) { el('login-status').textContent = error.message; }
    finally { submitting = false; el('login-submit').disabled = pendingId === el('login-id').value.trim().toLowerCase(); }
  });
  el('login-id').addEventListener('input', () => { el('login-submit').disabled = submitting || pendingId === el('login-id').value.trim().toLowerCase(); el('login-submit').textContent = el('login-submit').disabled ? '等待批准' : '申请访问'; });
  el('logout').addEventListener('click', async () => {
    if (beforeLogout && !await beforeLogout()) return;
    try { await api('/api/auth/logout', { method: 'POST', body: '{}' }); lock('已退出。填写 ID 可再次申请访问。'); }
    catch (error) { el('login-status').textContent = error.message; alert(`退出失败：${error.message}`); }
  });
  el('manage-access').addEventListener('click', async () => {
    el('access-dialog').showModal(); el('access-status').textContent = '正在读取账号与权限…';
    try { catalog = await api('/api/notes'); await refreshAdmin(); editUser(); }
    catch (error) { el('access-status').textContent = error.message; }
  });
  el('close-access').addEventListener('click', () => el('access-dialog').close());
  el('access-new-user').addEventListener('click', () => { editUser(); el('access-user-id').focus(); });
  el('access-add-grant').addEventListener('click', () => grantRow());
  el('access-users').addEventListener('click', (event) => { const button = event.target.closest('[data-user-id]'); if (button && !button.disabled) editUser(users.find((user) => user.id === button.dataset.userId)); });
  el('access-requests').addEventListener('click', async (event) => {
    const button = event.target.closest('[data-request-action]'); if (!button || button.disabled) return;
    button.disabled = true;
    try { await api(`/api/admin/requests/${button.dataset.requestAction}`, { method: 'POST', body: JSON.stringify({ id: button.dataset.requestId }) }); await refreshAdmin(); }
    catch (error) { el('access-status').textContent = error.message; button.disabled = false; }
  });
  el('access-user-form').addEventListener('submit', async (event) => {
    event.preventDefault(); el('access-save-user').disabled = true;
    const grants = [...el('access-grants').querySelectorAll('.grant-row')].map((row) => ({ scope: row.querySelector('.grant-scope').value, path: row.querySelector('.grant-path').value, access: row.querySelector('.grant-access').value }));
    try {
      const id = el('access-user-id').value.trim().toLowerCase();
      await api('/api/admin/users', { method: editingId ? 'PUT' : 'POST', body: JSON.stringify({ id, display_name: el('access-user-name').value.trim() || id, enabled: el('access-user-enabled').checked, grants }) });
      await refreshAdmin(); editUser(users.find((user) => user.id === id)); el('access-status').textContent = '账号与权限已保存。';
    } catch (error) { el('access-status').textContent = error.message; }
    finally { el('access-save-user').disabled = false; }
  });

  async function claimOwnerLink() {
    const params = new URLSearchParams(location.hash.slice(1));
    const token = params.get('owner-token');
    if (!token) return true;
    history.replaceState(history.state, '', location.pathname + location.search);
    submitting = true;
    try { await api('/api/auth/bootstrap', { method: 'POST', body: JSON.stringify({ token }) }); return true; }
    catch (error) { lock(`Owner 入口不可用：${error.message}。请在本机重新打开 owner 入口。`); return false; }
    finally { submitting = false; }
  }
  async function start(ready, lost, leaving) {
    onReady = ready; onLost = lost; beforeLogout = leaving;
    if (await claimOwnerLink()) await check();
    setInterval(async () => { await check(); if (owner() && el('access-dialog').open) { try { await refreshAdmin(); } catch {} } }, 8000);
    window.addEventListener('focus', check);
    window.addEventListener('hashchange', async () => { if (new URLSearchParams(location.hash.slice(1)).has('owner-token') && await claimOwnerLink()) await check(); });
    document.addEventListener('visibilitychange', () => { if (!document.hidden) check(); });
  }
  return { start, api, owner, canCreate, storageKey, get user() { return session?.user; }, get authenticated() { return Boolean(session); } };
})();
