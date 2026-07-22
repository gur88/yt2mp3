const urlInput     = document.getElementById('urlInput');
const startBtn     = document.getElementById('startBtn');
const statusBox    = document.getElementById('statusBox');
const stageLabel   = document.getElementById('stageLabel');
const pctDisplay   = document.getElementById('pctDisplay');
const progressFill = document.getElementById('progressFill');
const statusSub    = document.getElementById('statusSub');
const downloadBtn  = document.getElementById('downloadBtn');
const formatRow    = document.getElementById('formatRow');
const fmtNote      = document.getElementById('fmtNote');
const qualitySection = document.getElementById('qualitySection');
const qualityRow   = document.getElementById('qualityRow');
const previewBox   = document.getElementById('previewBox');
const previewThumb = document.getElementById('previewThumb');
const previewTitleInput  = document.getElementById('previewTitleInput');
const previewArtistInput = document.getElementById('previewArtistInput');
const trimToggle    = document.getElementById('trimToggle');
const trimSection   = document.getElementById('trimSection');
const trimStartInput = document.getElementById('trimStart');
const trimEndInput   = document.getElementById('trimEnd');
const trimError     = document.getElementById('trimError');
const trimReset     = document.getElementById('trimReset');
const pasteBtn      = document.getElementById('pasteBtn');
const normalizeToggle = document.getElementById('normalizeToggle');
const normalizeNote   = document.getElementById('normalizeNote');
const previewSizeEl   = document.getElementById('previewSize');

let selectedFmt     = 'aac';
let selectedQuality = 192;
let pollTimer       = null;
let pollStopped     = true;
let rateLimitTimer  = null;
let previewTimer    = null;
let previewRequestId = 0;
let currentTrackUrl = null; // URL the trim fields currently belong to
let currentDuration  = null;

function collapseTrim() {
  trimToggle.classList.remove('expanded');
  trimSection.style.display = 'none';
}

function resetTrimFields() {
  trimStartInput.value = '';
  trimEndInput.value = '';
  trimError.textContent = '';
}

trimToggle.addEventListener('click', () => {
  const expanding = trimSection.style.display === 'none';
  trimSection.style.display = expanding ? 'block' : 'none';
  trimToggle.classList.toggle('expanded', expanding);
  updateSizeEstimate();
});

trimReset.addEventListener('click', () => {
  trimStartInput.value = '';
  trimEndInput.value = currentDuration ? formatSecondsAsTime(currentDuration) : '';
  trimError.textContent = '';
  updateSizeEstimate();
});

trimStartInput.addEventListener('input', updateSizeEstimate);
trimEndInput.addEventListener('input', updateSizeEstimate);

normalizeToggle.addEventListener('change', () => {
  normalizeNote.style.display = normalizeToggle.checked ? 'block' : 'none';
  updateSizeEstimate();
});

function parseTimeToSeconds(str) {
  str = str.trim();
  if (!str) return null;
  if (/^\d+(\.\d+)?$/.test(str)) return parseFloat(str);
  const parts = str.split(':');
  if (parts.length < 2 || parts.length > 3 || parts.some(p => !/^\d+$/.test(p))) return NaN;
  return parts.reduce((acc, p) => acc * 60 + Number(p), 0);
}

function formatSecondsAsTime(totalSeconds) {
  totalSeconds = Math.round(totalSeconds);
  const h = Math.floor(totalSeconds / 3600);
  const m = Math.floor((totalSeconds % 3600) / 60);
  const s = totalSeconds % 60;
  return h > 0
    ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
    : `${m}:${String(s).padStart(2, '0')}`;
}

// Read-only version of the trim range, for the size estimate. Deliberately
// doesn't reuse validateTrim() — that function writes error text as a side
// effect, and recalculating the estimate on every quality/format click
// shouldn't silently clear an error the user hasn't fixed yet. Anything
// unparseable/invalid here just falls back to the full track duration
// instead of surfacing an error — this is an estimate, not a submission.
function getEffectiveDuration() {
  if (!currentDuration) return null;
  if (trimSection.style.display === 'none') return currentDuration;

  const startRaw = trimStartInput.value.trim();
  const endRaw   = trimEndInput.value.trim();
  if (!startRaw && !endRaw) return currentDuration;

  const start = startRaw ? parseTimeToSeconds(startRaw) : null;
  const end   = endRaw ? parseTimeToSeconds(endRaw) : null;
  if (Number.isNaN(start) || Number.isNaN(end)) return currentDuration;

  const s = start ?? 0;
  const e = end ?? currentDuration;
  return e > s ? e - s : currentDuration;
}

const FORMAT_BITRATES_KBPS = { aac: 192, opus: 160 }; // mp3 uses selectedQuality — it's user-adjustable

// Recalculated on: format change, mp3 quality change, trim range edits,
// and the normalize toggle (normalize doesn't change the bitrate, but the
// hook needs to not break when wired to it). Always prefixed with ≈ — for
// stream-copy cases the real source bitrate differs from our target and we
// don't know it in advance, so an honest approximation beats a confident
// wrong number.
function updateSizeEstimate() {
  if (!currentDuration) {
    previewSizeEl.textContent = '';
    return;
  }
  const bitrateKbps = selectedFmt === 'mp3' ? selectedQuality : FORMAT_BITRATES_KBPS[selectedFmt];
  const duration = getEffectiveDuration();
  const megabytes = (duration * bitrateKbps * 1000 / 8) / 1_000_000;
  previewSizeEl.textContent = `≈ ${megabytes.toFixed(1).replace('.', ',')} МБ`;
}

// Validates the trim inputs before submit. Returns { active:false } when
// trim isn't in play (section collapsed, or expanded but both fields blank),
// { active:false, invalid:true } when it's in play but invalid (error text
// already set on trimError), or { active:true, start, end } (seconds or null
// for an open-ended boundary) when good to send.
function validateTrim() {
  trimError.textContent = '';
  if (trimSection.style.display === 'none') return { active: false };

  const startRaw = trimStartInput.value.trim();
  const endRaw   = trimEndInput.value.trim();
  if (!startRaw && !endRaw) return { active: false };

  const start = startRaw ? parseTimeToSeconds(startRaw) : null;
  const end   = endRaw ? parseTimeToSeconds(endRaw) : null;

  if (Number.isNaN(start) || Number.isNaN(end)) {
    trimError.textContent = 'Не удалось распознать время. Используйте формат мм:сс или чч:мм:сс.';
    return { active: false, invalid: true };
  }
  if (start !== null && end !== null && start >= end) {
    trimError.textContent = 'Начало должно быть раньше конца.';
    return { active: false, invalid: true };
  }
  if (currentDuration) {
    if (end !== null && end > currentDuration) {
      trimError.textContent = 'Конец не может быть больше длительности трека.';
      return { active: false, invalid: true };
    }
    if (start !== null && end === null && start >= currentDuration) {
      trimError.textContent = 'Начало не может быть больше длительности трека.';
      return { active: false, invalid: true };
    }
  }
  return { active: true, start, end };
}

function hidePreview() {
  previewBox.classList.remove('visible');
  previewThumb.src = '';
  currentTrackUrl = null;
  currentDuration = null;
  updateSizeEstimate();
}

async function fetchPreview(url) {
  const requestId = ++previewRequestId;
  try {
    const res = await fetch('/api/info', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
    });
    const data = await res.json();
    if (requestId !== previewRequestId) return; // stale response, URL changed since
    if (data.error) { hidePreview(); return; }

    if (url !== currentTrackUrl) {
      // Genuinely different track from whatever the trim fields were last
      // set for — clear them so trim from the previous track can't silently
      // carry over onto this one.
      resetTrimFields();
      collapseTrim();
      currentTrackUrl = url;
    }

    previewThumb.src = data.thumbnail || '';
    previewTitleInput.value = data.title || '';
    previewArtistInput.value = data.artist || '';
    previewBox.classList.add('visible');

    currentDuration = typeof data.duration === 'number' ? data.duration : null;
    if (currentDuration && !trimEndInput.value.trim()) {
      trimEndInput.value = formatSecondsAsTime(currentDuration);
    }
    updateSizeEstimate();
  } catch {
    if (requestId === previewRequestId) hidePreview();
  }
}

urlInput.addEventListener('input', () => {
  clearTimeout(previewTimer);
  const url = urlInput.value.trim();
  if (!url) { hidePreview(); return; }
  previewTimer = setTimeout(() => fetchPreview(url), 600);
});

// Shared entry point for anything that sets the URL programmatically
// (share-target handoff, the paste button) instead of the user typing —
// reuses the exact same debounce->fetchPreview path as manual input via a
// synthetic 'input' event, rather than each caller duplicating that logic.
function setUrlAndPreview(url) {
  urlInput.value = url;
  urlInput.dispatchEvent(new Event('input'));
}

// Incoming Web Share Target (Android): the OS share sheet lands here as
// /?title=...&text=...&url=... — Android apps are inconsistent about which
// param carries the actual link (YouTube commonly uses `text`), so `url` is
// checked first, then `text`.
(function handleIncomingShare() {
  const params = new URLSearchParams(location.search);
  const urlMatch = /https?:\/\/\S+/;
  const found = (params.get('url') || '').match(urlMatch)
             || (params.get('text') || '').match(urlMatch);
  if (!found) return;

  setUrlAndPreview(found[0]);
  history.replaceState(null, '', location.pathname);
})();

// Paste button — feature-detected, never a dead control: only shown when
// the Clipboard Read API is actually available (missing in Firefox desktop,
// some WebViews).
if (navigator.clipboard && navigator.clipboard.readText) {
  pasteBtn.style.display = '';
  pasteBtn.addEventListener('click', async () => {
    let text;
    try {
      text = await navigator.clipboard.readText();
    } catch {
      return; // permission denied or unavailable — silent no-op, no alert
    }
    const url = (text || '').trim();
    if (!url) return;
    setUrlAndPreview(url);
  });
}

function stopPolling() {
  pollStopped = true;
  clearInterval(pollTimer);
  pollTimer = null;
}

const fmtNotes = {
  mp3:  { text: 'Конвертация из opus/aac → небольшая потеря качества', cls: '' },
  aac:  { text: '✓ Без перекодирования — оригинальное качество источника', cls: 'good' },
  opus: { text: '✓ Без перекодирования — наилучшее сжатие при том же качестве', cls: 'good' },
};

const VALID_FORMATS = ['mp3', 'aac', 'opus'];

// Shared by the click handler and the format-memory restore on load, so
// both paths apply exactly the same UI state (active button, note, quality
// section visibility, size-estimate recalc) instead of two copies drifting.
function selectFormat(fmt) {
  formatRow.querySelectorAll('.fmt-btn').forEach(b => b.classList.toggle('active', b.dataset.fmt === fmt));
  selectedFmt = fmt;
  const n = fmtNotes[fmt];
  fmtNote.textContent = n.text;
  fmtNote.className = 'fmt-note' + (n.cls ? ' ' + n.cls : '');
  qualitySection.style.display = fmt === 'mp3' ? 'block' : 'none';
  localStorage.setItem('preferredFormat', fmt);
  updateSizeEstimate();
}

formatRow.addEventListener('click', e => {
  const btn = e.target.closest('.fmt-btn');
  if (!btn) return;
  selectFormat(btn.dataset.fmt);
});

qualityRow.addEventListener('click', e => {
  const btn = e.target.closest('.q-btn');
  if (!btn) return;
  qualityRow.querySelectorAll('.q-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  selectedQuality = parseInt(btn.dataset.q);
  updateSizeEstimate();
});

// Restore the format preference before any other UI wiring runs, so the
// size estimate initializes against the right bitrate from the start.
// An unrecognized/corrupt stored value falls back to the current default
// (AAC) rather than crashing or leaving the UI in a half-set state.
const storedFormat = localStorage.getItem('preferredFormat');
if (storedFormat && VALID_FORMATS.includes(storedFormat)) {
  selectFormat(storedFormat);
}

function setStage(stage, pct, sub) {
  const labels = {
    pending:     'Подготовка',
    downloading: 'Скачивание аудиопотока',
    converting:  'Конвертация',
    done:        'Готово',
  };

  const dotHtml = stage === 'done' ? '' : '<span class="dot"></span>';
  stageLabel.innerHTML = dotHtml + (labels[stage] || stage);

  progressFill.style.width = pct + '%';
  progressFill.className = 'progress-fill' +
    (stage === 'converting' ? ' converting' : '') +
    (stage === 'done' ? ' done-fill' : '');

  pctDisplay.textContent = stage === 'done' ? '✓' : pct.toFixed(2) + '%';
  pctDisplay.className = 'pct-display' + (stage === 'done' ? ' done' : '');

  if (sub !== undefined) statusSub.textContent = sub;
  statusSub.classList.remove('error');
}

function setError(msg) {
  stageLabel.innerHTML = 'Ошибка';
  progressFill.style.width = '100%';
  progressFill.style.background = '#ff4444';
  pctDisplay.textContent = '✗';
  pctDisplay.className = 'pct-display';
  statusSub.textContent = msg;
  statusSub.classList.add('error');
}

function clearRateLimitTimer() {
  if (rateLimitTimer) { clearInterval(rateLimitTimer); rateLimitTimer = null; }
}

function formatCountdown(seconds) {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

function startRateLimitCountdown(seconds) {
  let remaining = Math.max(0, Math.round(seconds));
  clearRateLimitTimer();
  setError(`Попробуйте через ${formatCountdown(remaining)}`);
  rateLimitTimer = setInterval(() => {
    remaining--;
    if (remaining <= 0) {
      clearRateLimitTimer();
      setError('Лимит должен был сброситься — попробуйте снова');
      return;
    }
    statusSub.textContent = `Попробуйте через ${formatCountdown(remaining)}`;
  }, 1000);
}

function reset() {
  stopPolling();
  clearRateLimitTimer();
  startBtn.disabled = false;
  progressFill.style.background = '';
  statusSub.classList.remove('error');
  downloadBtn.classList.remove('visible');
}

startBtn.addEventListener('click', async () => {
  const url = urlInput.value.trim();
  if (!url) { urlInput.focus(); return; }

  const trim = validateTrim();
  if (trim.invalid) return;

  reset();
  startBtn.disabled = true;
  statusBox.classList.add('visible');
  setStage('pending', 0, '');

  const payload = { url, format: selectedFmt, quality: selectedQuality };
  if (trim.active) {
    if (trim.start !== null) payload.trim_start = trim.start;
    if (trim.end !== null) payload.trim_end = trim.end;
  }
  const titleVal  = previewTitleInput.value.trim();
  const artistVal = previewArtistInput.value.trim();
  if (titleVal) payload.title = titleVal;
  if (artistVal) payload.artist = artistVal;
  if (normalizeToggle.checked) payload.normalize = true;

  let jobId;
  try {
    const res = await fetch('/api/download', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (data.error) {
      if (res.status === 429 && data.retry_after_seconds) {
        startRateLimitCountdown(data.retry_after_seconds);
        startBtn.disabled = false;
        return;
      }
      throw new Error(data.error);
    }
    jobId = data.job_id;
  } catch (err) {
    setError(err.message);
    startBtn.disabled = false;
    return;
  }

  pollStopped = false;
  pollTimer = setInterval(async () => {
    if (pollStopped) return;
    let res, job;
    try {
      res = await fetch(`/api/status/${jobId}`);
      job = await res.json();
    } catch {
      if (pollStopped) return;
      stopPolling();
      setError('Потеряно соединение с сервером');
      startBtn.disabled = false;
      return;
    }

    if (pollStopped) return;
    const pct = typeof job.percent === 'number' ? job.percent : 0;

    if (job.status === 'done') {
      stopPolling();
      setStage('done', 100, job.title);
      const extLabel = { mp3: 'MP3', aac: 'AAC (.m4a)', opus: 'Opus' }[selectedFmt] || selectedFmt.toUpperCase();
      downloadBtn.textContent = `⬇ Скачать ${extLabel}`;
      downloadBtn.classList.add('visible');
      downloadBtn.onclick = () => {
        stopPolling();
        window.location.href = `/api/file/${jobId}`;
        setTimeout(() => {
          reset();
          statusBox.classList.remove('visible');
        }, 2000);
      };
      startBtn.disabled = false;

    } else if (job.status === 'error') {
      stopPolling();
      setError(job.error || 'Неизвестная ошибка');
      startBtn.disabled = false;

    } else {
      const stage = job.stage || 'pending';
      setStage(stage, pct, '');
    }
  }, 500);
});

urlInput.addEventListener('keydown', e => {
  if (e.key === 'Enter') startBtn.click();
});

function openFaqItem(item, content) {
  item.open = true;
  const target = content.scrollHeight;
  content.style.height = '0px';
  content.offsetHeight; // force reflow so the 0px state is committed before animating
  content.style.height = target + 'px';
  content.addEventListener('transitionend', function onEnd() {
    content.style.height = 'auto';
    content.removeEventListener('transitionend', onEnd);
  }, { once: true });
}

function closeFaqItem(item, content) {
  content.style.height = content.scrollHeight + 'px';
  content.offsetHeight; // force reflow so the current height is committed before animating
  content.style.height = '0px';
  content.addEventListener('transitionend', function onEnd() {
    item.open = false;
    content.removeEventListener('transitionend', onEnd);
  }, { once: true });
}

document.querySelectorAll('.faq-item').forEach(item => {
  const summary = item.querySelector('summary');
  const content = item.querySelector('.faq-content');

  summary.addEventListener('click', e => {
    e.preventDefault();
    if (item.open) {
      closeFaqItem(item, content);
      return;
    }
    document.querySelectorAll('.faq-item').forEach(other => {
      if (other !== item && other.open) {
        closeFaqItem(other, other.querySelector('.faq-content'));
      }
    });
    openFaqItem(item, content);
  });
});

document.getElementById('copyYear').textContent = '© ' + new Date().getFullYear() + ' AudioGrab.ru';

const cookieBanner = document.getElementById('cookieBanner');
const cookieAcceptBtn = document.getElementById('cookieAcceptBtn');

if (!localStorage.getItem('cookie_consent')) {
  cookieBanner.classList.add('visible');
}

cookieAcceptBtn.addEventListener('click', () => {
  localStorage.setItem('cookie_consent', '1');
  cookieBanner.classList.remove('visible');
});

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    try {
      navigator.serviceWorker.register('/sw.js').catch(() => {});
    } catch {}
  });
}
