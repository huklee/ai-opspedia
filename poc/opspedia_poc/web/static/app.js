// ai-opspedia PoC — 상단 검색 자동완성 · ⌘K · 최근 본 페이지 · 토글 상태 · 지표 차트
(function () {
  const q = document.getElementById('gq'), box = document.getElementById('suggest');
  const store = {
    get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* 저장 불가 환경 */ } },
  };
  window.opsStore = store;
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const ICON = { system: '🏢', dag: '🔁', table: '🗄️', index: '📇', alias: '🔗', incident: '🚨', runbook: '📕', team: '👥', metric: '📈', category: '📊' };

  // ⌘K · Ctrl+K · / → 검색창 포커스
  document.addEventListener('keydown', e => {
    const tag = (e.target.tagName || '').toLowerCase();
    if (((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') || (e.key === '/' && tag !== 'input' && tag !== 'textarea')) {
      e.preventDefault(); q && (q.focus(), q.select());
    }
    if (e.key === 'Escape' && box) box.hidden = true;
  });

  // 자동완성
  if (q && box) {
    let timer, seq = 0, sel = -1;
    const run = () => {
      const v = q.value.trim(), my = ++seq;
      if (!v) { box.hidden = true; return; }
      fetch('/api/search?k=7&q=' + encodeURIComponent(v)).then(r => r.ok ? r.json() : Promise.reject(r.status)).then(d => {
        if (my !== seq) return;
        sel = -1;
        box.innerHTML = d.results.map(r => `<a href="${esc(r.url)}"><span>${ICON[r.type] || ''}</span><span>${esc((r.status ? r.status + ' ' : '') + r.title)}</span><small>${esc(r.type)}</small></a>`).join('')
          + `<a href="/search?q=${encodeURIComponent(v)}"><span>🔎</span><span>"${esc(v)}" 전체 검색</span></a>`;
        box.hidden = false;
      }).catch(() => { box.hidden = true; });
    };
    q.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(run, 120); });
    q.addEventListener('focus', () => { if (q.value.trim() && box.innerHTML) box.hidden = false; });
    q.addEventListener('keydown', e => {
      const items = [...box.querySelectorAll('a')];
      if (box.hidden || !items.length) return;
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        sel = (sel + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
        items.forEach((a, i) => a.classList.toggle('sel', i === sel));
      } else if (e.key === 'Enter' && sel >= 0) { e.preventDefault(); location.href = items[sel].href; }
    });
    document.addEventListener('click', e => { if (!e.target.closest('.gsearch')) box.hidden = true; });
  }

  // 사이드바 토글 상태 기억
  document.querySelectorAll('details.tg[data-k]').forEach(d => {
    const k = 'tg:' + d.dataset.k, v = store.get(k, null);
    if (v !== null) d.open = v;
    d.addEventListener('toggle', () => store.set(k, d.open));
    if (d.querySelector('.leaf.on')) d.open = true;
  });

  // 접이식 callout
  document.querySelectorAll('.admonition.is-collapsible > .admonition-title').forEach(t =>
    t.addEventListener('click', () => t.parentElement.classList.toggle('collapsible-closed')));

  // 최근 본 페이지
  window.opsTrack = p => {
    const list = store.get('recent', []).filter(x => x.id !== p.id);
    list.unshift({ ...p, at: Date.now() });
    store.set('recent', list.slice(0, 12));
  };
  window.opsRecent = id => {
    const el = document.getElementById(id), list = store.get('recent', []);
    if (!el || !list.length) return;
    const ago = t => { const m = Math.round((Date.now() - t) / 60000); return m < 1 ? '방금' : m < 60 ? m + '분 전' : m < 1440 ? Math.round(m / 60) + '시간 전' : Math.round(m / 1440) + '일 전'; };
    el.innerHTML = list.slice(0, 8).map(x => `<a class="card" href="/e/${esc(x.id)}"><div class="rc-ico">${esc(x.icon)}</div><div class="rc-t">${esc(x.title)}</div><div class="rc-m">${esc(x.type)} · ${ago(x.at)}</div></a>`).join('');
  };

  // 지표 차트 (Chart.js)
  window.opsChart = (id, d) => {
    if (!window.Chart) return;
    const css = getComputedStyle(document.documentElement);
    const c = n => css.getPropertyValue(n).trim();
    new Chart(document.getElementById(id), {
      type: 'line',
      data: { labels: d.labels, datasets: [
        { label: d.title, data: d.values, borderColor: c('--accent'), backgroundColor: 'transparent', tension: .25, pointRadius: 3, spanGaps: false },
        { label: 'SLO', data: d.labels.map(() => d.slo), borderColor: c('--bad'), borderDash: [5, 4], pointRadius: 0, borderWidth: 1 },
      ] },
      options: { responsive: true, plugins: { legend: { labels: { color: c('--muted') } } },
        scales: { x: { ticks: { color: c('--muted') }, grid: { color: c('--line') } },
                  y: { ticks: { color: c('--muted') }, grid: { color: c('--line') }, title: { display: true, text: d.unit, color: c('--muted') } } } },
    });
  };
})();

// AI 답변 (자연어 질의) — /api/ask
window.opsAsk = function (id) {
  const box = document.getElementById(id); if (!box) return;
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const ICON = { system: '🏢', dag: '🔁', table: '🗄️', index: '📇', alias: '🔗', incident: '🚨', runbook: '📕', team: '👥', metric: '📈', category: '📊' };
  const run = () => {
    box.innerHTML = '<div class="ai-h">✨ AI 답변 <span class="muted">질의 해석 · 검색 · 답변 생성 중…</span></div><div class="ai-skel"><i></i><i></i><i></i></div>';
    fetch('/api/ask?q=' + encodeURIComponent(box.dataset.q)).then(r => r.ok ? r.json() : Promise.reject(r.status)).then(d => {
      const a = d.answer, p = d.plan, lp = p.llm || {};
      const cite = t => esc(t).replace(/\[S(\d+)\]/g, (m, n) => { const s = d.sources[n - 1]; return s ? `<a class="ai-c" href="${esc(s.url)}" title="${esc(s.title)}">${n}</a>` : ''; });
      const how = { rule: '규칙 · 구조화 사실', ok: 'LLM · 인용 검증 통과', partial: 'LLM · 일부 문장 제외', rejected: 'LLM 거절 → 규칙 답변', off: '규칙 답변', error: 'LLM 오류 → 규칙 답변' }[a.status] || a.status;
      const tone = { rule: 'ok', ok: 'ok', partial: 'warn', rejected: 'warn', error: 'bad', off: '' }[a.status] || '';
      box.innerHTML = `
        <div class="ai-h">✨ AI 답변 <span class="chip ${tone}">${esc(how)}</span><span class="muted ai-m">${esc(d.model || 'LLM 없음')} · 해석 ${d.timing_ms.plan} · 검색 ${d.timing_ms.retrieve} · 답변 ${d.timing_ms.answer} ms${a.cached ? ' · 캐시' : ''}</span></div>
        <div class="ai-plan"><span class="chip">의도 ${esc(p.intent)}</span>${p.entities.map(e => `<a class="chip" href="/e/${esc(e)}">${esc(e)}</a>`).join('')}${lp.rewritten ? `<span class="chip">재작성 “${esc(lp.rewritten)}”</span>` : ''}${p.rule.when ? `<span class="chip">시점 ${esc(p.rule.when)}</span>` : ''}</div>
        <ul class="ai-a">${a.sentences.map(s => `<li>${cite(s)}</li>`).join('')}</ul>
        ${(a.rejected || []).length ? `<details class="ai-rej"><summary>검증에서 제외된 문장 ${a.rejected.length}개</summary><ul>${a.rejected.map(r => `<li><s>${esc(r[0])}</s> <span class="muted">${esc(r[1])}</span></li>`).join('')}</ul></details>` : ''}
        <div class="ai-src">${d.sources.map(s => `<a href="${esc(s.url)}"><b>${s.n}</b> ${ICON[s.kind] || '🧩'} ${esc(s.title)}</a>`).join('')}</div>`;
    }).catch(e => { box.innerHTML = `<div class="ai-h">✨ AI 답변 <span class="chip bad">실패</span> <span class="muted">${esc(String(e))} · LLM 서버(Ollama) 확인</span></div>`; });
  };
  if (box.dataset.auto === '1') run();
  const b = box.querySelector('.ai-go'); b && b.addEventListener('click', run);
};
