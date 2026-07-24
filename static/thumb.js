const urlInput  = document.getElementById('urlInput');
const thumbGrid = document.getElementById('thumbGrid');
const thumbError = document.getElementById('thumbError');

let requestId = 0;
let debounceTimer = null;

// Defense-in-depth against a long-documented (but not reproduced during
// live testing on 2026-07-24 — see architecture.md) YouTube CDN quirk:
// historically, a missing maxresdefault (and, rarely, sddefault) could
// return HTTP 200 with a 120x90 grey placeholder instead of a 404, which
// would make onload-only probing show a broken "HD" result. Cheap to keep
// even though every case tested here 404'd cleanly instead.
const PLACEHOLDER_WIDTH = 120;
const PLACEHOLDER_HEIGHT = 90;

const QUALITIES = [
  { key: 'maxresdefault', checkPlaceholder: true },
  { key: 'sddefault',     checkPlaceholder: true },
  { key: 'hqdefault',     checkPlaceholder: false },
  { key: 'mqdefault',     checkPlaceholder: false },
];

const VIDEO_ID_RE = /^[A-Za-z0-9_-]{11}$/;

function extractVideoId(raw) {
  let url;
  try {
    url = new URL(raw.trim());
  } catch {
    return null;
  }
  const host = url.hostname.toLowerCase().replace(/^www\./, '');

  if (host === 'youtu.be') {
    const id = url.pathname.slice(1).split('/')[0];
    return VIDEO_ID_RE.test(id) ? id : null;
  }

  const isYoutube = host === 'youtube.com' || host === 'm.youtube.com' ||
                     host === 'music.youtube.com' || host === 'youtube-nocookie.com';
  if (!isYoutube) return null;

  if (url.pathname === '/watch') {
    const id = url.searchParams.get('v');
    return id && VIDEO_ID_RE.test(id) ? id : null;
  }
  const shortsMatch = url.pathname.match(/^\/shorts\/([A-Za-z0-9_-]{11})/);
  if (shortsMatch) return shortsMatch[1];
  const embedMatch = url.pathname.match(/^\/embed\/([A-Za-z0-9_-]{11})/);
  if (embedMatch) return embedMatch[1];

  return null;
}

function probeQuality(videoId, quality, checkPlaceholder) {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => {
      if (checkPlaceholder && img.naturalWidth === PLACEHOLDER_WIDTH && img.naturalHeight === PLACEHOLDER_HEIGHT) {
        resolve(null);
        return;
      }
      resolve({ quality, width: img.naturalWidth, height: img.naturalHeight });
    };
    img.onerror = () => resolve(null);
    img.src = `https://i.ytimg.com/vi/${videoId}/${quality}.jpg`;
  });
}

function renderGrid(videoId, variants) {
  thumbGrid.innerHTML = '';
  if (!variants.length) {
    thumbError.textContent = 'Не удалось получить обложку для этого видео.';
    return;
  }
  for (const v of variants) {
    const item = document.createElement('div');
    item.className = 'thumb-item';

    const img = document.createElement('img');
    img.src = `https://i.ytimg.com/vi/${videoId}/${v.quality}.jpg`;
    img.alt = `Обложка ${v.width}×${v.height}`;
    item.appendChild(img);

    const info = document.createElement('div');
    info.className = 'thumb-item-info';

    const label = document.createElement('span');
    label.className = 'thumb-item-label';
    label.textContent = `${v.width}×${v.height}`;
    info.appendChild(label);

    const btn = document.createElement('a');
    btn.className = 'thumb-download-btn';
    btn.href = `/api/thumbnail?video_id=${videoId}&quality=${v.quality}`;
    btn.textContent = 'Скачать';
    info.appendChild(btn);

    item.appendChild(info);
    thumbGrid.appendChild(item);
  }
}

async function loadThumbnails(url) {
  const myRequestId = ++requestId;
  thumbError.textContent = '';
  thumbGrid.innerHTML = '';

  const videoId = extractVideoId(url);
  if (!videoId) {
    thumbError.textContent = 'Не удалось распознать ссылку на видео YouTube.';
    return;
  }

  const results = await Promise.all(
    QUALITIES.map(q => probeQuality(videoId, q.key, q.checkPlaceholder))
  );
  if (myRequestId !== requestId) return; // stale — URL changed since this probe started

  renderGrid(videoId, results.filter(Boolean));
}

urlInput.addEventListener('input', () => {
  clearTimeout(debounceTimer);
  const url = urlInput.value.trim();
  requestId++; // invalidate any in-flight probe for the previous value
  thumbError.textContent = '';
  thumbGrid.innerHTML = '';
  if (!url) return;
  debounceTimer = setTimeout(() => loadThumbnails(url), 600);
});

urlInput.addEventListener('keydown', e => {
  if (e.key !== 'Enter') return;
  clearTimeout(debounceTimer);
  const url = urlInput.value.trim();
  if (url) loadThumbnails(url);
});

document.getElementById('copyYear').textContent = '© ' + new Date().getFullYear() + ' AudioGrab.ru';

// Cookie banner — deliberately duplicated from app.js rather than extracted
// into a shared module: this page doesn't load app.js at all, and factoring
// it out would mean touching the file all four existing tool pages depend
// on for the sake of one utility page. See architecture.md for the
// two-places-to-edit note this creates.
const cookieBanner = document.getElementById('cookieBanner');
const cookieAcceptBtn = document.getElementById('cookieAcceptBtn');

if (!localStorage.getItem('cookie_consent')) {
  cookieBanner.classList.add('visible');
}

cookieAcceptBtn.addEventListener('click', () => {
  localStorage.setItem('cookie_consent', '1');
  cookieBanner.classList.remove('visible');
});

// FAQ accordion — same duplication rationale as the cookie banner above.
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
