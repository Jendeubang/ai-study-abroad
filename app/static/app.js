(() => {
  'use strict';

  const searchForm = document.querySelector('#search-form');
  if (!searchForm) return;

  const filterForm = document.querySelector('#filter-form');
  const query = document.querySelector('#query');
  const results = document.querySelector('#results');
  const status = document.querySelector('#status');
  const count = document.querySelector('#result-count');
  const pagination = document.querySelector('#pagination');
  const sort = document.querySelector('#sort');
  const chatForm = document.querySelector('#chat-form');
  const chatInput = document.querySelector('#chat-input');
  const messages = document.querySelector('#chat-messages');
  const historyList = document.querySelector('#history-list');
  const historyStatus = document.querySelector('#history-status');
  const historyOptIn = document.querySelector('#history-opt-in');
  const historyClear = document.querySelector('#history-clear');
  const historyDisable = document.querySelector('#history-disable');
  const style = document.body.dataset.style || 'editorial';
  const state = { mode: 'search', page: 1, pageSize: 8, items: [], total: 0, pending: false, request: 0 };
  const filterToggle = document.querySelector('#filter-toggle');
  if (filterToggle) {
    const panel = filterToggle.closest('.filter-panel');
    const mobile = window.matchMedia('(max-width: 760px)');
    const setOpen = open => {
      panel.classList.toggle('is-collapsed', !open);
      filterToggle.setAttribute('aria-expanded', String(open));
    };
    setOpen(!mobile.matches);
    mobile.addEventListener('change', event => setOpen(!event.matches));
    filterToggle.addEventListener('click', () => setOpen(filterToggle.getAttribute('aria-expanded') !== 'true'));
  }

  function node(tag, className, content) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (content !== undefined && content !== null) element.textContent = String(content);
    return element;
  }

  function safeUrl(value) {
    try {
      const url = new URL(value);
      return ['https:', 'http:'].includes(url.protocol) ? url.href : null;
    } catch (_) { return null; }
  }

  function setStatus(message = '', kind = '') {
    status.textContent = message;
    status.className = `status ${kind}`.trim();
  }

  function formatTuition(item) {
    return item.tuition == null ? '信息缺失' : `${Number(item.tuition).toLocaleString('zh-CN')} ${item.currency || ''}`.trim();
  }

  function addFact(parent, label, value) {
    const box = node('div');
    box.append(node('span', '', label), node('strong', '', value ?? '信息缺失'));
    parent.append(box);
  }

  function renderCard(item) {
    const article = node('article', 'program-card');
    const official = item.data_origin === 'official_reviewed';
    const top = node('div', 'program-top');
    const titleGroup = node('div');
    titleGroup.append(node('p', 'program-university', item.university || '院校信息缺失'));
    titleGroup.append(node('span', `source-badge ${official ? 'source-badge-official' : 'source-badge-demo'}`, official ? '官网已核对 · 招生要求' : '模拟项目'));
    if (official && item.review_alerts?.length) {
      titleGroup.append(node('span', 'source-badge source-badge-review', '待复核'));
    }
    const heading = node('h3');
    const detail = node('a', '', item.program_name || '项目名称缺失');
    detail.href = `/programs/${encodeURIComponent(item.id)}?style=${encodeURIComponent(style)}`;
    heading.append(detail);
    titleGroup.append(heading);
    const score = node('div', 'score');
    score.setAttribute('aria-label', `规则匹配分 ${item.match_score} 分，不能代表录取概率`);
    score.append(node('strong', '', item.match_score ?? '—'), node('small', '', style === 'editorial' ? '条件分' : '匹配分'));
    top.append(titleGroup, score);
    article.append(top);

    const meta = node('div', 'program-meta');
    const metaValues = style === 'editorial'
      ? [item.country, item.degree, item.discipline]
      : [item.country, item.city, item.degree, item.discipline, item.intake];
    metaValues.filter(Boolean).forEach(value => meta.append(node('span', '', value)));
    article.append(meta);

    const facts = node('div', 'program-facts');
    addFact(facts, item.source_id?.startsWith('hkust-') ? '2027 全日制学费' : '学费', official && item.tuition == null ? '未采集' : formatTuition(item));
    addFact(facts, '申请截止', item.deadline || (official ? '未采集' : '信息缺失'));
    addFact(facts, item.source_id?.startsWith('cityu-') ? '雅思（需提交者）' : official ? '雅思要求' : '雅思参考', item.ielts_min == null ? '信息缺失' : item.ielts_min);
    article.append(facts);
    if (official && item.review_alerts?.length) {
      article.append(node('p', 'program-review-note', `待复核：${item.review_alerts.join('；')}。请以大学官网为准。`));
    }

    if (style !== 'editorial' || item.recommendation) {
      const reason = node('p', 'program-reason');
      const reasonLabel = item.recommendation_source === 'ai_verified'
        ? '推荐依据：' : item.match_notes?.length ? '需留意：' : '初筛说明：';
      reason.append(node('strong', '', reasonLabel));
      reason.append(document.createTextNode(item.recommendation || '按现有条件检索；请打开项目来源核对要求。'));
      article.append(reason);
    }
    if (Array.isArray(item.match_notes) && item.match_notes.length && item.recommendation_source !== 'rules') {
      article.append(node('p', 'program-notes', item.match_notes.join('；')));
    }

    const footer = node('div', 'program-footer');
    const updated = String((official ? item.source_retrieved_at : item.updated_at) || '').slice(0, 10) || '未知';
    footer.append(node('span', '', official ? `数据更新于 ${updated}（官网采集） · 人工核对 ${String(item.reviewed_at || '').slice(0, 10) || '未知'}` : `模拟数据更新于 ${updated}`));
    const source = safeUrl(item.source_url);
    if (source) {
      const link = node('a', '', official ? '查看大学官网 ↗' : '模拟来源 ↗');
      link.href = source;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      footer.append(link);
    } else footer.append(node('span', '', '来源链接缺失'));
    article.append(footer);
    return article;
  }

  function renderPagination() {
    pagination.replaceChildren();
    if (state.total <= state.pageSize) return;
    const totalPages = Math.ceil(state.total / state.pageSize);
    const previous = node('button', '', '上一页');
    previous.type = 'button';
    previous.disabled = state.page === 1;
    previous.addEventListener('click', () => run(state.mode, state.page - 1));
    const next = node('button', '', '下一页');
    next.type = 'button';
    next.disabled = state.page >= totalPages;
    next.addEventListener('click', () => run(state.mode, state.page + 1));
    pagination.append(previous, node('span', '', `${state.page} / ${totalPages}`), next);
  }

  function renderResults() {
    results.replaceChildren();
    count.textContent = `(${state.total})`;
    if (!state.items.length) {
      const empty = node('div', 'empty-state');
      empty.append(node('h3', '', state.total ? '这一页没有项目' : '没有找到匹配项目'));
      empty.append(node('p', '', state.total ? '返回上一页查看结果。' : '试着放宽地区或专业条件，或换一种描述。'));
      results.append(empty);
    } else state.items.forEach(item => results.append(renderCard(item)));
    renderPagination();
  }

  function filters() {
    const values = new FormData(filterForm);
    const selected = {};
    for (const [key, value] of values.entries()) {
      if (String(value).trim()) selected[key] = String(value).trim();
    }
    return selected;
  }

  async function api(url, options = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 65000);
    try {
      const response = await fetch(url, { ...options, signal: controller.signal, headers: { 'Content-Type': 'application/json', ...(options.headers || {}) } });
      const body = await response.json();
      if (!response.ok || body.code !== 0) throw new Error(body.message || `请求失败 (${response.status})`);
      return body.data;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('请求超时，请稍后重试');
      if (error instanceof SyntaxError || error instanceof TypeError) throw new Error('网络暂不可用，请稍后重试');
      throw error;
    } finally { clearTimeout(timer); }
  }

  async function historyAction(url, method) {
    return api(url, { method, headers: { 'X-History-Action': '1' } });
  }

  function renderHistory(data) {
    historyOptIn.hidden = data.enabled;
    historyClear.hidden = !data.enabled;
    historyDisable.hidden = !data.enabled;
    historyList.replaceChildren();
    if (!data.enabled) {
      historyStatus.textContent = '未开启保存。';
      return;
    }
    const entries = Array.isArray(data.items) ? data.items : [];
    historyStatus.textContent = entries.length ? `最近 ${entries.length} 条记录` : '暂无历史记录。';
    entries.forEach(entry => {
      if (!['search', 'recommend', 'chat'].includes(entry.kind)) return;
      const row = node('article', 'history-item');
      const kind = { search: '检索', recommend: '推荐', chat: '问答' }[entry.kind];
      const when = new Date(entry.created_at);
      const date = Number.isNaN(when.getTime()) ? '' : when.toLocaleString('zh-CN');
      row.append(node('span', 'history-item-meta', `${kind} · ${date}`));
      row.append(node('p', 'history-item-query', entry.query || '筛选检索'));
      if (entry.kind === 'chat') row.append(node('p', 'history-item-answer', entry.answer || ''));
      const remove = node('button', 'text-button', '删除');
      remove.type = 'button';
      remove.setAttribute('aria-label', `删除这条${kind}记录`);
      remove.addEventListener('click', async () => {
        remove.disabled = true;
        try { await historyAction(`/api/history/${entry.kind}/${encodeURIComponent(entry.id)}`, 'DELETE'); await refreshHistory(); }
        catch (error) { historyStatus.textContent = error.message || '删除失败，请重试。'; remove.disabled = false; }
      });
      row.append(remove);
      historyList.append(row);
    });
  }

  async function refreshHistory() {
    try { renderHistory(await api('/api/history')); }
    catch (error) { historyStatus.textContent = error.message || '历史记录暂不可用。'; }
  }

  historyOptIn.addEventListener('click', async () => {
    historyOptIn.disabled = true;
    try { await historyAction('/api/history/consent', 'POST'); await refreshHistory(); }
    catch (error) { historyStatus.textContent = error.message || '开启失败，请重试。'; }
    finally { historyOptIn.disabled = false; }
  });
  historyClear.addEventListener('click', async () => {
    if (!window.confirm('确定清空此浏览器的全部历史记录吗？')) return;
    historyClear.disabled = true;
    try { await historyAction('/api/history', 'DELETE'); await refreshHistory(); }
    catch (error) { historyStatus.textContent = error.message || '清空失败，请重试。'; }
    finally { historyClear.disabled = false; }
  });
  historyDisable.addEventListener('click', async () => {
    if (!window.confirm('确定关闭保存并删除全部历史记录吗？')) return;
    historyDisable.disabled = true;
    try { await historyAction('/api/history/consent', 'DELETE'); await refreshHistory(); }
    catch (error) { historyStatus.textContent = error.message || '关闭失败，请重试。'; }
    finally { historyDisable.disabled = false; }
  });

  function setPending(value, mode) {
    state.pending = value;
    searchForm.querySelectorAll('button').forEach(button => button.disabled = value);
    filterForm.querySelector('button[type=submit]').disabled = value;
    if (value) setStatus(mode === 'recommend' ? '正在解析需求并生成推荐，请稍候…' : '正在检索项目…', 'loading');
  }

  async function run(mode = 'search', page = 1) {
    if (state.pending) return;
    const text = query.value.trim();
    if (mode === 'recommend' && (!text || !/[A-Za-z0-9\u3400-\u9fff]/.test(text))) {
      setStatus('请先输入有效的留学需求，再使用 AI 推荐。', 'error'); query.focus(); return;
    }
    if (mode === 'search' && text.length > 500) {
      setStatus('纯检索最多支持 500 字；请缩短描述后重试。', 'error'); query.focus(); return;
    }
    const currentRequest = ++state.request;
    setPending(true, mode);
    try {
      let data;
      if (mode === 'recommend') {
        data = await api('/api/recommend', {
          method: 'POST',
          body: JSON.stringify({ query: text, page, page_size: state.pageSize, filters: { ...filters(), sort: sort.value } }),
        });
      } else {
        const params = new URLSearchParams({ page: String(page), page_size: String(state.pageSize), sort: sort.value, ...filters() });
        if (text) params.set('q', text);
        data = await api(`/api/programs?${params}`);
      }
      if (currentRequest !== state.request) return;
      state.mode = mode; state.page = page; state.items = data.items || []; state.total = data.total || 0;
      renderResults();
      const vectorFallback = filters().retrieval !== 'keyword' && text && data.retrieval_mode === 'keyword'
        ? '向量索引暂不可用，已使用关键词检索。' : '';
      if (mode === 'recommend') {
        const notices = [...(data.warnings || [])];
        if (!data.ai_available) notices.unshift(data.notice || 'AI 推荐暂不可用，已显示规则检索结果。');
        if (vectorFallback) notices.push(vectorFallback);
        if (notices.length) setStatus(notices.join('；'), 'warning');
        else setStatus(style === 'editorial' ? '' : `已找到 ${state.total} 个项目。推荐依据均关联项目来源。`);
      } else if (vectorFallback) setStatus(vectorFallback, 'warning');
      else setStatus(state.total ? (style === 'editorial' ? '' : `已找到 ${state.total} 个项目。匹配分仅供初筛，不代表录取概率。`) : '没有符合当前条件的项目。');
      refreshHistory();
    } catch (error) {
      if (currentRequest === state.request) setStatus(error.message || '请求失败，请重试。', 'error');
    } finally { setPending(false, mode); }
  }

  function appendMessage(type, value, citations = []) {
    const bubble = node('div', `chat-bubble ${type}`, value);
    if (citations.length) {
      const sources = node('div');
      citations.forEach(citation => {
        const url = safeUrl(citation.source_url);
        if (!url) return;
        const link = node('a', '', `项目 #${citation.program_id} 来源 ↗`);
        link.href = url; link.target = '_blank'; link.rel = 'noopener noreferrer';
        sources.append(link, document.createTextNode(' '));
      });
      bubble.append(sources);
    }
    messages.append(bubble);
    messages.scrollTop = messages.scrollHeight;
  }

  searchForm.addEventListener('submit', event => { event.preventDefault(); run('recommend'); });
  document.querySelector('#plain-search').addEventListener('click', () => run('search'));
  filterForm.addEventListener('submit', event => { event.preventDefault(); run(state.mode); });
  document.querySelector('#clear-filters').addEventListener('click', () => { filterForm.reset(); run(state.mode); });
  document.querySelector('[data-example]')?.addEventListener('click', event => { query.value = event.currentTarget.dataset.example; query.focus(); });
  sort.addEventListener('change', () => run(state.mode));
  chatForm.addEventListener('submit', async event => {
    event.preventDefault();
    const message = chatInput.value.trim();
    if (!message || !/[A-Za-z0-9\u3400-\u9fff]/.test(message)) { appendMessage('error', '请输入有效的问题。'); return; }
    if (!state.items.length) { appendMessage('error', '请先检索项目，再基于当前结果提问。'); return; }
    const button = chatForm.querySelector('button');
    button.disabled = true;
    appendMessage('user', message); chatInput.value = '';
    const pending = node('div', 'chat-bubble', '正在核对当前结果…'); messages.append(pending);
    try {
      const data = await api('/api/chat', { method: 'POST', body: JSON.stringify({ message, program_ids: state.items.map(item => item.id) }) });
      pending.remove();
      appendMessage('', data.answer || '根据当前检索结果无法确定', data.citations || []);
      refreshHistory();
    } catch (error) { pending.remove(); appendMessage('error', `${error.message || '问答失败'}，请稍后重试。`); }
    finally { button.disabled = false; }
  });

  refreshHistory();
  run('search');
})();
