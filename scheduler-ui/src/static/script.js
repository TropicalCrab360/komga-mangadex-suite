console.debug('[scheduler-ui] script loaded');

function showError(message) {
  const banner = document.getElementById('error-banner');
  const text = document.getElementById('error-text');
  if (text) text.textContent = message || 'An error occurred.';
  if (banner) banner.style.display = 'block';
}

function hideError() {
  const banner = document.getElementById('error-banner');
  if (banner) banner.style.display = 'none';
}

const dismissBtn = document.getElementById('error-dismiss');
if (dismissBtn) {
  dismissBtn.addEventListener('click', hideError);
}

const BASE_PREFIX = (function() {
  const p = window.location.pathname || '/';
  // When served under /scheduler/, ensure API calls are prefixed accordingly
  return p.startsWith('/scheduler') ? '/scheduler' : '';
})();

async function api(path, opts) {
  const url = path.startsWith('http') ? path : (BASE_PREFIX + path);
  console.debug('[api] fetch', url, opts || {});
  const res = await fetch(url, opts).catch((e) => {
    console.error('[api] network error', e);
    showError('Network error: ' + (e?.message || e));
    throw e;
  });
  if (!res.ok) {
    const txt = await res.text().catch(() => '');
    console.error('[api] bad status', res.status, txt);
    showError(`Request failed (${res.status})`);
    throw new Error(`${res.status}: ${txt || 'error'}`);
  }
  const ct = res.headers.get('content-type') || '';
  if (ct.includes('application/json')) {
    return res.json();
  }
  // fallback: return empty object
  return {};
}

const els = {
  title: document.getElementById('title'),
  language: document.getElementById('language'),
  searchBtn: document.getElementById('searchBtn'),
  results: document.getElementById('results'),
  selectedId: document.getElementById('selectedId'),
  chapterStart: document.getElementById('chapterStart'),
  chapterEnd: document.getElementById('chapterEnd'),
  chaptersList: document.getElementById('chaptersList'),
  volumesList: document.getElementById('volumesList'),
  queueBtn: document.getElementById('queueBtn'),
  queueStatus: document.getElementById('queueStatus'),
  jobs: document.getElementById('jobs'),
  refreshJobs: document.getElementById('refreshJobs'),
  loadPreview: document.getElementById('loadPreview'),
  volumesPreview: document.getElementById('volumesPreview'),
  volumesCount: document.getElementById('volumesCount'),
  chaptersPreview: document.getElementById('chaptersPreview'),
  chapterFilter: document.getElementById('chapterFilter'),
  prevPage: document.getElementById('prevPage'),
  nextPage: document.getElementById('nextPage'),
  pageInfo: document.getElementById('pageInfo'),
  tailLogs: document.getElementById('tailLogs'),
  logsBox: document.getElementById('logsBox'),
};

let selectedMangaId = null;
let previewState = { volumes: [], chapters: [], page: 1, pageSize: 30, filter: '', total: 0, hasPrev: false, hasNext: false, volumesLoaded: false };
let jobRefreshTimer = null;
const JOB_REFRESH_INTERVAL_MS = 10000;

async function search() {
  const title = els.title.value.trim();
  if (!title) return;
  els.results.innerHTML = '<li class="muted">Searching…</li>';
  try {
    const data = await api(`/search?title=${encodeURIComponent(title)}`);
    const items = (data.data || []).map((d) => {
      const id = d.id;
      const attrs = d.attributes || {};
      const name = (attrs.title && (attrs.title.en || Object.values(attrs.title)[0])) || id;
      return { id, name };
    });
    els.results.innerHTML = '';
    if (items.length === 0) {
      els.results.innerHTML = '<li class="muted">No results</li>';
    }
    items.forEach((it) => {
      const li = document.createElement('li');
      const btn = document.createElement('button');
      btn.textContent = 'Select';
      btn.onclick = () => selectManga(it.id);
      li.textContent = `${it.name} `;
      li.appendChild(btn);
      els.results.appendChild(li);
    });
  } catch (e) {
    els.results.innerHTML = `<li class="muted">Error: ${e.message}</li>`;
  }
}

function selectManga(id) {
  selectedMangaId = id;
  els.selectedId.textContent = id;
  els.queueBtn.disabled = false;
  // reset preview state on new selection
  previewState.page = 1;
  previewState.filter = '';
  previewState.total = 0;
  previewState.hasPrev = false;
  previewState.hasNext = false;
  previewState.volumesLoaded = false;
  previewState.volumes = [];
  previewState.chapters = [];
}

async function queueJob() {
  if (!selectedMangaId) return;
  const language = els.language.value;
  const start = els.chapterStart.value.trim();
  const end = els.chapterEnd.value.trim();
  const chaptersList = els.chaptersList.value.trim();
  const volumesList = els.volumesList.value.trim();

  const req = { manga_id: selectedMangaId, language };
  // Note: downloader currently ignores chapter range; send placeholders for future filtering
  if (start || end) {
    req.chapters = [start || null, end || null].filter(Boolean);
  }
  if (chaptersList) {
    req.chapters = chaptersList.split(',').map(s => s.trim()).filter(Boolean);
  }
  if (volumesList) {
    req.volumes = volumesList.split(',').map(s => s.trim()).filter(Boolean);
  }

  els.queueStatus.textContent = 'Queuing…';
  try {
    const data = await api('/jobs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req),
    });
    els.queueStatus.textContent = `Queued: ${data.job_id || ''}`;
    await refreshJobs();
  } catch (e) {
    els.queueStatus.textContent = `Error: ${e.message}`;
  }
}

async function abortJob(jobId) {
  if (!confirm(`Abort job ${jobId}?`)) return;
  try {
    const data = await api(`/jobs/${jobId}/abort`, { method: 'POST' });
    alert(data.message || 'Job aborted');
    await refreshJobs();
  } catch (e) {
    alert(`Error aborting job: ${e.message}`);
  }
}

async function refreshJobs() {
  try {
    const data = await api('/jobs');
    els.jobs.innerHTML = '';
    if (!Array.isArray(data) || data.length === 0) {
      els.jobs.innerHTML = '<li class="muted">No jobs</li>';
      return;
    }
    let hasActive = false;
    const fmt = (label, val) => {
      const n = (typeof val === 'number') ? val : 0;
      const cls = n > 0 ? 'count-pos' : 'count-zero';
      return `<span class="${cls}">${label}: ${n}</span>`;
    };
    data.forEach((job) => {
      const li = document.createElement('li');
      const status = job.status;
      const res = job.result || {};
      const processed = (typeof res.chapters_processed === 'number') ? res.chapters_processed : null;
      const packaged = (typeof res.chapters_packaged === 'number') ? res.chapters_packaged : null;
      const created = Array.isArray(res.files_created) ? res.files_created.length : null;
      const skipped = (typeof res.skipped_chapters === 'number') ? res.skipped_chapters : null;
      const skippedExternal = (typeof res.skipped_external === 'number') ? res.skipped_external : null;
      const skippedNoImages = (typeof res.skipped_no_images === 'number') ? res.skipped_no_images : null;
      const errors = (typeof res.errors === 'number') ? res.errors : null;
      let summary = '';
      if (status === 'completed') {
        summary = [
          fmt('processed', processed ?? 0),
          fmt('packaged', packaged ?? 0),
          fmt('files', created ?? 0),
          fmt('skipped', skipped ?? 0),
          fmt('external', skippedExternal ?? 0),
          fmt('no-images', skippedNoImages ?? 0),
          fmt('errors', errors ?? 0),
        ].join(' ');
      } else if (status === 'failed' && job.error) {
        summary = `error: ${job.error}`;
      } else if (status === 'queued' || status === 'running') {
        hasActive = true;
      }
      if (summary) {
        li.innerHTML = `<span>${job.id || ''} — ${status} — </span>${summary}`;
      } else {
        li.textContent = `${job.id || ''} — ${status}`;
      }
      // add abort button for running/queued jobs
      if (status === 'running' || status === 'queued') {
        const abortBtn = document.createElement('button');
        abortBtn.textContent = 'Abort';
        abortBtn.style.marginLeft = '10px';
        abortBtn.onclick = () => abortJob(job.id);
        li.appendChild(abortBtn);
      }
      els.jobs.appendChild(li);
    });
    // manage auto-refresh interval
    if (hasActive && !jobRefreshTimer) {
      jobRefreshTimer = setInterval(refreshJobs, JOB_REFRESH_INTERVAL_MS);
    } else if (!hasActive && jobRefreshTimer) {
      clearInterval(jobRefreshTimer);
      jobRefreshTimer = null;
    }
  } catch (e) {
    els.jobs.innerHTML = `<li class="muted">Error: ${e.message}</li>`;
  }
}

async function loadPreview(loadVolumesOverride) {
  // decide whether to aggregate volumes: only on first load or when explicitly requested
  const loadVolumes = (typeof loadVolumesOverride === 'boolean') ? loadVolumesOverride : !previewState.volumesLoaded;
  if (!selectedMangaId) {
    els.volumesPreview.innerHTML = '<li class="muted">Select a manga first</li>';
    els.chaptersPreview.innerHTML = '';
    return;
  }
  const lang = els.language.value;
  if (loadVolumes) {
    els.volumesPreview.innerHTML = '<li class="muted">Loading…</li>';
  }
  els.chaptersPreview.innerHTML = '<li class="muted">Loading…</li>';
  try {
    const params = new URLSearchParams({
      manga_id: selectedMangaId,
      language: lang,
      limit: String(previewState.pageSize),
      offset: String((previewState.page - 1) * previewState.pageSize),
      aggregate_volumes: loadVolumes ? 'true' : 'false',
    });
    const data = await api(`/mangadex/chapters?${params.toString()}`);
    const vols = data.volumes || [];
    const chs = data.chapters || [];
    const externalMap = data.external_chapters || {};
    // Only update volumes when requested and the backend provided any
    if (loadVolumes && vols.length > 0) {
      previewState.volumes = vols;
      previewState.volumesLoaded = true;
    }
    previewState.chapters = chs;
    previewState.externalMap = externalMap;
    previewState.total = Number(data.total || 0);
    previewState.hasPrev = Boolean(data.has_prev);
    previewState.hasNext = Boolean(data.has_next);
    if (loadVolumes) {
      // Render volumes only if we attempted to load them
      els.volumesPreview.innerHTML = '';
      if (previewState.volumes.length === 0) {
        els.volumesPreview.innerHTML = '<li class="muted">None</li>';
      } else {
        previewState.volumes.forEach(v => {
          const li = document.createElement('li');
          const btn = document.createElement('button');
          btn.textContent = 'Add';
          btn.onclick = () => addVolume(v);
          li.textContent = `Vol ${v} `;
          li.appendChild(btn);
          els.volumesPreview.appendChild(li);
        });
      }
      els.volumesCount.textContent = previewState.volumes.length ? `${previewState.volumes.length} total` : '';
    }
    renderChapterPage();
  } catch (e) {
    if (loadVolumes) {
      els.volumesPreview.innerHTML = `<li class="muted">Error: ${e.message}</li>`;
    }
    els.chaptersPreview.innerHTML = '';
  }
}

function renderChapterPage() {
  const { chapters, page, pageSize, filter, total } = previewState;
  const filtered = filter ? chapters.filter(c => String(c).includes(filter)) : chapters;
  const totalPages = total > 0 ? Math.max(1, Math.ceil(total / pageSize)) : undefined;
  const curPage = page;
  const slice = filtered;
  els.chaptersPreview.innerHTML = '';
  if (slice.length === 0) {
    if (filter && chapters.length > 0) {
      els.chaptersPreview.innerHTML = '<li class="muted">No matching chapters for current filter</li>';
    } else {
      els.chaptersPreview.innerHTML = '<li class="muted">No chapters</li>';
    }
  }
  slice.forEach(c => {
    const li = document.createElement('li');
    const btn = document.createElement('button');
    btn.textContent = 'Add';
    btn.onclick = () => addChapter(c);
    const isExternal = previewState.externalMap && previewState.externalMap[String(c)];
    if (isExternal) {
      li.innerHTML = `Ch ${c} <span class="badge-external">external</span> `;
      li.appendChild(btn);
    } else {
      li.textContent = `Ch ${c} `;
      li.appendChild(btn);
    }
    els.chaptersPreview.appendChild(li);
  });
  if (totalPages) {
    els.pageInfo.textContent = `Page ${curPage}/${totalPages}`;
  } else {
    const start = (curPage - 1) * pageSize + 1;
    const end = start + slice.length - 1;
    els.pageInfo.textContent = `Showing ${start}-${end}`;
  }
  previewState.page = curPage;
  // Enable/disable nav buttons based on hints
  els.prevPage.disabled = !previewState.hasPrev || curPage <= 1;
  // If total unknown, enable Next when we have a full page
  const canNext = totalPages ? (curPage < totalPages) : (slice.length >= pageSize);
  els.nextPage.disabled = !previewState.hasNext && !canNext;
}

function addVolume(v) {
  const cur = els.volumesList.value.trim();
  const arr = cur ? cur.split(',').map(s => s.trim()).filter(Boolean) : [];
  if (!arr.includes(String(v))) arr.push(String(v));
  els.volumesList.value = arr.join(',');
}

function addChapter(c) {
  const curList = els.chaptersList.value.trim();
  const arr = curList ? curList.split(',').map(s => s.trim()).filter(Boolean) : [];
  if (!arr.includes(String(c))) arr.push(String(c));
  els.chaptersList.value = arr.join(',');
}

els.searchBtn.onclick = () => { console.debug('[ui] search click'); search(); };
els.queueBtn.onclick = queueJob;
els.refreshJobs.onclick = refreshJobs;
els.loadPreview.onclick = () => { console.debug('[ui] load preview click'); previewState.page = 1; previewState.volumesLoaded = false; loadPreview(true); };
els.chapterFilter.oninput = () => { previewState.filter = els.chapterFilter.value.trim(); previewState.page = 1; renderChapterPage(); };
els.prevPage.onclick = async () => {
  if (previewState.page > 1) {
    console.debug('[ui] prev page');
    previewState.page--;
    await loadPreview(false);
  }
};
els.nextPage.onclick = async () => {
  const totalPages = previewState.total > 0 ? Math.max(1, Math.ceil(previewState.total / previewState.pageSize)) : undefined;
  if ((totalPages && previewState.page < totalPages) || (!totalPages && previewState.chapters.length >= previewState.pageSize)) {
    console.debug('[ui] next page');
    previewState.page++;
    await loadPreview(false);
  }
};

els.tailLogs.onclick = async () => {
  els.logsBox.textContent = 'Loading…';
  try {
    const res = await fetch(BASE_PREFIX + '/logs/tail?lines=300');
    if (!res.ok) throw new Error(String(res.status));
    const txt = await res.text();
    els.logsBox.textContent = txt || '(empty)';
  } catch (e) {
    els.logsBox.textContent = `Error: ${e.message}`;
    console.error('[ui] tail logs error', e);
    showError('Failed to load logs');
  }
};

// Optional: Live logs via WebSocket
let logSocket;
function connectLogsWebSocket() {
  try {
    const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
    const url = `${scheme}://${location.host}${BASE_PREFIX}/ws/logs`;
    console.debug('[ws] connecting', url);
    logSocket = new WebSocket(url);
    logSocket.onopen = () => console.debug('[ws] live logs connected');
    logSocket.onmessage = (evt) => {
      const logsEl = document.getElementById('logsBox');
      const data = evt.data || '';
      if (logsEl) {
        logsEl.textContent += (data.endsWith('\n') ? data : (data + '\n'));
        logsEl.scrollTop = logsEl.scrollHeight;
      }
    };
    logSocket.onerror = (e) => console.warn('[ws] error', e);
    logSocket.onclose = () => console.debug('[ws] closed');
  } catch (e) {
    console.warn('[ws] init failed', e);
  }
}

// Attempt WebSocket connection after page load
setTimeout(connectLogsWebSocket, 1000);

// Collapsible section handlers
function setupCollapsible(toggleId, contentId) {
  const toggle = document.getElementById(toggleId);
  const content = document.getElementById(contentId);
  if (toggle && content) {
    toggle.addEventListener('click', () => {
      toggle.classList.toggle('collapsed');
      content.classList.toggle('collapsed');
    });
    // Start collapsed
    toggle.classList.add('collapsed');
    content.classList.add('collapsed');
  }
}

setupCollapsible('jobsToggle', 'jobsContent');
setupCollapsible('logsToggle', 'logsContent');

// Status button handler - scrolls to and expands jobs section
const statusBtn = document.getElementById('statusBtn');
if (statusBtn) {
  statusBtn.addEventListener('click', () => {
    const jobsSection = document.getElementById('jobsSection');
    const jobsToggle = document.getElementById('jobsToggle');
    const jobsContent = document.getElementById('jobsContent');
    if (jobsToggle && jobsContent) {
      jobsToggle.classList.remove('collapsed');
      jobsContent.classList.remove('collapsed');
    }
    if (jobsSection) {
      jobsSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  });
}

// initial
refreshJobs();
