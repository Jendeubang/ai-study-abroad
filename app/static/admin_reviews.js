(() => {
  'use strict';
  let token = '';
  const login = document.querySelector('#admin-login');
  const content = document.querySelector('#admin-content');
  const status = document.querySelector('#admin-status');
  const reviewList = document.querySelector('#review-list');
  const sourceList = document.querySelector('#source-list');
  const syncOverview = document.querySelector('#sync-overview');
  const programCandidateList = document.querySelector('#program-candidate-list');

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
      const scopeLabel = item.use_scope === 'unverified' ? '使用范围待登记' : `已登记：${item.use_scope === 'public_display' ? '公开展示' : '本地非商业研究'} · ${String(item.checked_at).slice(0, 10)}`;
      const latestLabel = item.last_check_at ? `最近采集：${item.last_check_status} · ${String(item.last_check_at).slice(0, 16).replace('T', ' ')}` : '尚无采集记录';
      const successLabel = item.last_success_at ? `最近成功：${String(item.last_success_at).slice(0, 16).replace('T', ' ')}` : '尚无成功记录';
      card.append(el('h3', '', item.host), el('p', 'admin-meta', scopeLabel), el('p', 'admin-meta', latestLabel), el('p', 'admin-meta', successLabel));
      if (item.has_pending_review) card.append(el('p', 'admin-meta', '有待审核的新项目或字段变化'));
      const enableForm = el('form', 'admin-form');
      const enableLabel = el('label', 'admin-inline-label', item.enabled ? '来源已启用' : '来源已停用');
      const enabled = el('input'); enabled.type = 'checkbox'; enabled.checked = Boolean(item.enabled);
      enableLabel.prepend(enabled);
      const toggle = el('button', 'button button-subtle', '更新来源状态'); toggle.type = 'submit';
      enableForm.append(enableLabel, toggle);
      enableForm.addEventListener('submit', async event => {
        event.preventDefault(); toggle.disabled = true;
        try {
          await api(`/api/admin/sources/${encodeURIComponent(item.source_id)}`, { method: 'PATCH', body: JSON.stringify({ enabled: enabled.checked }) });
          message('来源状态已更新。'); await loadSources();
        } catch (error) { message(error.message, 'error'); }
        finally { toggle.disabled = false; }
      });
      card.append(enableForm);
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
  async function loadSyncRuns() { renderSyncRuns(await api('/api/admin/sync-runs?limit=30')); }
  async function loadProgramCandidates() { renderProgramCandidates(await api('/api/admin/program-candidates')); }

  function renderSyncRuns(items) {
    syncOverview.replaceChildren();
    if (!items.length) { syncOverview.append(el('p', 'admin-empty', '尚无同步记录。安装每周任务后，这里会显示每次运行结果。')); return; }
    items.forEach(run => {
      const card = el('article', 'admin-card');
      const timestamp = String(run.started_at).slice(0, 16).replace('T', ' ');
      card.append(el('h3', '', `${timestamp} · ${run.trigger === 'schedule' ? '定时运行' : '手动运行'}`));
      card.append(el('p', 'admin-meta', `检查 ${run.checked_count}/${run.eligible_count} · 有变化 ${run.changed_count} · 待审核 ${run.pending_count} · 失败 ${run.failed_count} · 跳过 ${run.skipped_count}`));
      if (!run.finished_at) card.append(el('p', 'admin-meta', '运行尚未完成'));
      (run.source_statuses || []).forEach(source => {
        const label = `${source.source_id} · ${source.status}${source.pending ? ` · 待审核 ${source.pending}` : ''}${source.message ? ` · ${source.message}` : ''}`;
        card.append(el('p', 'admin-meta', label));
      });
      syncOverview.append(card);
    });
  }

  function renderProgramCandidates(items) {
    programCandidateList.replaceChildren();
    if (!items.length) { programCandidateList.append(el('p', 'admin-empty', '当前没有新项目待审核。')); return; }
    items.forEach(item => {
      const card = el('article', 'admin-card');
      card.append(el('h3', '', `${item.university} · ${item.program_name}`));
      card.append(el('p', 'admin-meta', `${item.country}${item.city ? ` · ${item.city}` : ''} · ${item.degree} · ${item.discipline}`));
      card.append(el('p', 'admin-meta', `采集于 ${String(item.retrieved_at).slice(0, 16).replace('T', ' ')} · 来源 ${item.source_ids.join('、')}`));
      (item.facts || []).forEach(fact => {
        const block = el('div', 'admin-fact');
        block.append(el('strong', '', fact.field_name));
        block.append(el('pre', '', pretty(fact.value)));
        block.append(el('p', 'admin-meta', `适用条件：${pretty(fact.scope)}`));
        block.append(el('p', 'admin-evidence', fact.evidence_excerpt));
        addLink(block, '查看招生来源 ↗', fact.source_url);
        card.append(block);
      });
      const form = el('form', 'admin-decision');
      const reviewer = el('input'); reviewer.name = 'reviewer'; reviewer.maxLength = 100; reviewer.required = true; reviewer.placeholder = '核查人';
      const note = el('input'); note.name = 'note'; note.minLength = 8; note.maxLength = 500; note.required = true; note.placeholder = '审核备注（至少 8 字）';
      const purpose = el('select');
      [['local_non_commercial_research', '批准用于本地研究'], ['public_display', '批准用于公开展示']].forEach(([value, label]) => {
        const option = el('option', '', label); option.value = value; purpose.append(option);
      });
      const approve = el('button', 'button button-primary', '批准导入'); approve.type = 'submit'; approve.value = 'approve';
      const reject = el('button', 'button button-subtle', '驳回'); reject.type = 'submit'; reject.value = 'reject';
      form.append(reviewer, note, purpose, approve, reject);
      form.addEventListener('submit', async event => {
        event.preventDefault();
        const decision = event.submitter?.value;
        if (!['approve', 'reject'].includes(decision)) return;
        approve.disabled = reject.disabled = true;
        try {
          await api(`/api/admin/program-candidates/${item.id}/${decision}`, {
            method: 'POST', body: JSON.stringify({
              reviewer: reviewer.value.trim(), note: note.value.trim(),
              purpose: purpose.value,
            }),
          });
          message(decision === 'approve' ? '新项目已批准并导入。' : '新项目已驳回。');
          await Promise.all([loadProgramCandidates(), loadReviews(), loadSources()]);
        } catch (error) { message(error.message, 'error'); }
        finally { approve.disabled = reject.disabled = false; }
      });
      card.append(form);
      programCandidateList.append(card);
    });
  }

  login.addEventListener('submit', async event => {
    event.preventDefault();
    token = document.querySelector('#admin-token').value;
    document.querySelector('#admin-token').value = '';
    try {
      await Promise.all([loadReviews(), loadSources(), loadSyncRuns(), loadProgramCandidates()]);
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

  document.querySelector('#refresh-sync').addEventListener('click', async () => {
    try { await Promise.all([loadSyncRuns(), loadProgramCandidates(), loadReviews(), loadSources()]); message('同步和审核状态已刷新。'); }
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
