// Shared helpers for every page (loaded before the page scripts).

function escapeHtml(str) {
  if (str == null || str === '') return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

const inr = new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR' });
const inr0 = new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 });

async function api(method, url, body) {
  const res = await fetch(url, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = Array.isArray(data.detail)
      ? data.detail.map(d => d.msg).join(', ')
      : (typeof data.detail === 'string' ? data.detail : null);
    const err = new Error(detail || `Request failed (${res.status})`);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

function showToast(text, ok = true) {
  const el = document.getElementById('toast');
  if (!el) return;
  el.className = `fixed bottom-4 right-4 z-50 px-4 py-2.5 rounded-lg text-xs font-medium shadow-lg ${ok ? 'bg-emerald-600 text-white' : 'bg-rose-600 text-white'}`;
  el.innerText = text;
  clearTimeout(el._timer);
  el._timer = setTimeout(() => el.classList.add('hidden'), 3000);
}

function emptyRow(colspan, text) {
  return `<tr><td colspan="${colspan}" class="py-8 text-center text-slate-500">${text}</td></tr>`;
}

function copyTextToClipboard(text, onSuccess, onError) {
  if (typeof navigator !== 'undefined' && navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
    navigator.clipboard.writeText(text).then(onSuccess).catch(() => {
      fallbackCopyText(text, onSuccess, onError);
    });
  } else {
    fallbackCopyText(text, onSuccess, onError);
  }
}

function fallbackCopyText(text, onSuccess, onError) {
  try {
    if (typeof document === 'undefined' || !document.createElement || !document.body) {
      if (onError) onError();
      return;
    }
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.left = '-9999px';
    ta.style.top = '-9999px';
    ta.setAttribute('readonly', '');
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    const successful = typeof document.execCommand === 'function' ? document.execCommand('copy') : false;
    document.body.removeChild(ta);
    if (successful) {
      if (onSuccess) onSuccess();
    } else {
      if (onError) onError();
    }
  } catch (err) {
    if (onError) onError();
  }
}

function copyElementText(elId, btn) {
  if (typeof document === 'undefined' || !document.getElementById) return;
  const el = document.getElementById(elId);
  if (!el) return;
  const text = el.innerText || el.textContent || '';
  const onDone = () => {
    if (btn) {
      const orig = btn.innerHTML;
      btn.innerHTML = '<i class="fa-solid fa-check mr-1 text-emerald-400"></i>Copied';
      setTimeout(() => { btn.innerHTML = orig; }, 1500);
    }
    if (typeof showToast === 'function') {
      showToast('Copied to clipboard');
    }
  };
  const onFail = () => {
    if (typeof showToast === 'function') {
      showToast('Failed to copy', false);
    }
  };
  copyTextToClipboard(text, onDone, onFail);
}

// Read rows are dimmed via `tr.is-read td:not(.keep)`; unread rows get an accent bar
// on the row's first cell (`.accent-cell`).
function rowClass(isRead) {
  return 'hover:bg-slate-800/60 transition' + (isRead ? ' is-read' : '');
}

function accentClass(isRead) {
  return isRead ? '' : 'accent-unread';
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    escapeHtml,
    inr,
    inr0,
    emptyRow,
    api,
    showToast,
    copyElementText,
    copyTextToClipboard,
    fallbackCopyText,
    rowClass,
    accentClass,
  };
}
