(() => {
  'use strict';
  let token = '';
  const login = document.querySelector('#admin-login');
  const content = document.querySelector('#admin-content');
  const status = document.querySelector('#admin-status');
  const reviewList = document.querySelector('#review-list');
  const sourceList = document.querySelector('#source-list');

  function el(tag, className = '', text = '') {
    const item = document.createElement(tag);
    item.className = className;
    item.textContent = text;
    return item;
  }

  function safeUrl(value) {
    try {
      const url = new URL(value);
      return url.protocol === 'https:' ? url.href : null;
    } catch (_) { return null; }
  }

  function message(value, kind = '') {
    status.textContent = value;
    status.className = `status ${kind}`.trim();
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      ...options,
      headers: { 'Content-Type': 'application/json', 'X-Admin-Token': token },
    });
    const body = await response.json();
    if (!response.ok || body.code !== 0) throw new Error(body.message || '操作失败');
    return body.data;
  }

  function addLink(parent, label, url) {
    const safe = safeUrl(url);
    if (!safe) return;
    const link = el('a', 'source-link', label);
    link.href = safe;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    parent.append(link);
  }

  function pretty(value) {
    return value == null ? '尚无已核对值' : JSON.stringify(value, null, 2);
  }

  function renderReviews(items) {
    reviewList.replaceChildren();
    if (!items.length) { reviewList.append(el('p', 'admin-empty', '当前没有待核对变化。')); return; }
    items.forEach(item => {
      const card = el('article', 'admin-card');
      card.append(el('h3', '', `${item.university} · ${item.program_name}`));
      card.append(el('p', 'admin-meta', `${item.kind === 'round' ? '申请轮次' : item.kind === 'page' ? '官网页面变化' : item.field_name} · 采集于 ${String(item.retrieved_at).slice(0, 16).replace('T', ' ')}`));
      const comparison = el('div', 'admin-comparison');
      const oldBox = el('div'); oldBox.append(el('span', '', '已发布'), el('pre', '', pretty(item.old_value)));
      const newBox = el('div'); newBox.append(el('span', '', '待审核'), el('pre', '', pretty(item.new_value)));
      comparison.append(oldBox, newBox);
      card.append(comparison, el('p', 'admin-meta', `适用条件：${pretty(item.scope)}`), el('p', 'admin-evidence', item.evidence_excerpt));
      addLink(card, '查看大学官网 ↗', item.source_url);
      const form = el('form', 'admin-decision');
      const reviewer = el('input'); reviewer.name = 'reviewer'; reviewer.maxLength = 100; reviewer.required = true; reviewer.placeholder = '核查人';
      const note = el('input'); note.name = 'note'; note.minLength = 8; note.maxLength = 500; note.required = true; note.placeholder = '核对说明（至少 8 字）';
      const approve = el('button', 'button button-primary', '批准'); approve.type = 'submit'; approve.value = 'approve';
      const reject = el('button', 'button button-subtle', '驳回'); reject.type = 'submit'; reject.value = 'reject';
      form.append(reviewer, note, approve, reject);
      form.addEventListener('submit', async event => {
        event.preventDefault();
        const decision = event.submitter?.value;
        if (!['approve', 'reject'].includes(decision)) return;
        approve.disabled = reject.disabled = true;
        try {
          await api(`/api/admin/reviews/${item.id}/${decision}`, { method: 'POST', body: JSON.stringify({ reviewer: reviewer.value.trim(), note: note.value.trim() }) });
          message(decision === 'approve' ? '已批准并更新项目。' : '已驳回并保留原项目。');
          await loadReviews();
        } catch (error) { message(error.message, 'error'); }
        finally { approve.disabled = reject.disabled = false; }
      });
      card.append(form);
      reviewList.append(card);
    });
  }

  function renderSources(items) {
    sourceList.replaceChildren();
    items.forEach(item => {
      const card = el('article', 'admin-card');
      card.append(el('h3', '', item.host), el('p', 'admin-meta', item.use_scope === 'unverified' ? '使用范围待登记' : `已登记：${item.use_scope === 'public_display' ? '公开展示' : '本地非商业研究'} · ${String(item.checked_at).slice(0, 10)}`));
      const form = el('form', 'admin-form');
      const terms = el('input'); terms.type = 'url'; terms.required = true; terms.placeholder = 'https://… 网站条款链接'; terms.value = item.terms_url || '';
      const scope = el('select');
      [['unverified', '尚未确认（禁止采集）'], ['local_non_commercial_research', '本地非商业研究'], ['public_display', '公开展示（需单独核实）']].forEach(([value, label]) => {
        const option = el('option', '', label); option.value = value; scope.append(option);
      });
      scope.value = ['unverified', 'local_non_commercial_research', 'public_display'].includes(item.use_scope) ? item.use_scope : 'unverified';
      const reviewer = el('input'); reviewer.maxLength = 100; reviewer.required = true; reviewer.placeholder = '核查人'; reviewer.value = item.reviewer || '';
      const note = el('textarea'); note.minLength = 8; note.maxLength = 500; note.required = true; note.rows = 2; note.placeholder = '条款依据及允许用途说明'; note.value = item.note || '';
      const button = el('button', 'button button-filter', '保存使用范围'); button.type = 'submit';
      form.append(terms, scope, reviewer, note, button);
      form.addEventListener('submit', async event => {
        event.preventDefault(); button.disabled = true;
        try {
          await api('/api/admin/sources', { method: 'POST', body: JSON.stringify({
            host: item.host, terms_url: terms.value.trim(), use_scope: scope.value,
            reviewer: reviewer.value.trim(), note: note.value.trim(),
          }) });
          message('网站使用范围已登记。'); await loadSources();
        } catch (error) { message(error.message, 'error'); }
        finally { button.disabled = false; }
      });
      card.append(form);
      sourceList.append(card);
    });
  }

  async function loadReviews() { renderReviews(await api('/api/admin/reviews')); }
  async function loadSources() { renderSources(await api('/api/admin/sources')); }

  login.addEventListener('submit', async event => {
    event.preventDefault();
    token = document.querySelector('#admin-token').value;
    document.querySelector('#admin-token').value = '';
    try {
      await Promise.all([loadReviews(), loadSources()]);
      const result = await fetch('/api/programs?source_type=official&page_size=100');
      const programs = (await result.json()).data.items;
      const select = document.querySelector('#round-form select[name=program_id]');
      select.replaceChildren();
      programs.forEach(program => {
        const option = el('option', '', `${program.university} · ${program.program_name}`);
        option.value = String(program.id); select.append(option);
      });
      content.hidden = false; login.hidden = true; message('已载入待核对数据。');
    } catch (error) { token = ''; message(error.message, 'error'); }
  });

  document.querySelector('#refresh-reviews').addEventListener('click', async () => {
    try { await loadReviews(); message('待核对列表已刷新。'); }
    catch (error) { message(error.message, 'error'); }
  });

  document.querySelector('#round-form').addEventListener('submit', async event => {
    event.preventDefault();
    const form = event.currentTarget;
    const values = Object.fromEntries(new FormData(form));
    values.program_id = Number(values.program_id);
    values.opens_on ||= null;
    values.deadline_on ||= null;
    try {
      await api('/api/admin/rounds/candidates', { method: 'POST', body: JSON.stringify(values) });
      form.reset(); await loadReviews(); message('申请轮次已提交待审核。');
    } catch (error) { message(error.message, 'error'); }
  });
})();
