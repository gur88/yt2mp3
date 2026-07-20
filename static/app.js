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
const previewTitle = document.getElementById('previewTitle');
const previewArtist = document.getElementById('previewArtist');

let selectedFmt     = 'aac';
let selectedQuality = 192;
let pollTimer       = null;
let pollStopped     = true;
let rateLimitTimer  = null;
let previewTimer    = null;
let previewRequestId = 0;

function hidePreview() {
  previewBox.classList.remove('visible');
  previewThumb.src = '';
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

    previewThumb.src = data.thumbnail || '';
    previewTitle.textContent = data.title || '';
    previewArtist.textContent = data.artist || '';
    previewBox.classList.add('visible');
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

formatRow.addEventListener('click', e => {
  const btn = e.target.closest('.fmt-btn');
  if (!btn) return;
  formatRow.querySelectorAll('.fmt-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  selectedFmt = btn.dataset.fmt;
  const n = fmtNotes[selectedFmt];
  fmtNote.textContent = n.text;
  fmtNote.className = 'fmt-note' + (n.cls ? ' ' + n.cls : '');
  qualitySection.style.display = selectedFmt === 'mp3' ? 'block' : 'none';
});

qualityRow.addEventListener('click', e => {
  const btn = e.target.closest('.q-btn');
  if (!btn) return;
  qualityRow.querySelectorAll('.q-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  selectedQuality = parseInt(btn.dataset.q);
});

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

  reset();
  startBtn.disabled = true;
  statusBox.classList.add('visible');
  setStage('pending', 0, '');

  let jobId;
  try {
    const res = await fetch('/api/download', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, format: selectedFmt, quality: selectedQuality }),
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
