let debounceTimer = null;
let autoRefreshInterval = null;
let currentWebhooks = [];
let currentInspectedId = null;
let returnLocation = null;

(function initReturnLocation() {
  if (typeof window === 'undefined') return;
  const params = new URLSearchParams(window.location.search);
  const fromParam = params.get('from');
  if (fromParam === 'cards') {
    returnLocation = '/cards';
  } else if (fromParam === 'transactions') {
    returnLocation = '/transactions';
  }
})();

function debounceFetch() {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(fetchWebhooks, 300);
    }

    async function fetchStats() {
      try {
        const data = await api('GET', '/api/stats');
        document.getElementById('statTotal').innerText = data.total_count.toLocaleString();
        
        let smsCount = 0;
        if (data.providers) {
          for (const [k, v] of Object.entries(data.providers)) {
            if (k !== 'generic' && k !== 'unknown') smsCount += v;
          }
        }
        document.getElementById('statSms').innerText = smsCount.toLocaleString();

        const sizeKb = (data.db_size_bytes / 1024).toFixed(1);
        document.getElementById('statDbSize').innerText = sizeKb > 1024 ? (sizeKb / 1024).toFixed(2) + ' MB' : sizeKb + ' KB';

        if (data.latest_received_at) {
          const d = new Date(data.latest_received_at);
          document.getElementById('statLastActivity').innerText = d.toLocaleTimeString([], {hour: '2-digit', minute:'2-digit', second:'2-digit'}) + ' (' + d.toLocaleDateString() + ')';
        } else {
          document.getElementById('statLastActivity').innerText = 'No events yet';
        }
      } catch (err) {
        console.error("Failed to load stats", err);
      }
    }

    function renderTypeBadge(type) {
      const t = type || 'Others';
      const configs = {
        'Transaction': {
          bg: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
          icon: 'fa-indian-rupee-sign',
        },
        'OTP': {
          bg: 'bg-amber-500/15 text-amber-400 border-amber-500/30',
          icon: 'fa-key',
        },
        'Statement': {
          bg: 'bg-sky-500/15 text-sky-400 border-sky-500/30',
          icon: 'fa-file-invoice-dollar',
        },
        'Toll / FASTag': {
          bg: 'bg-purple-500/15 text-purple-400 border-purple-500/30',
          icon: 'fa-car',
        },
        'Mandate / Autopay': {
          bg: 'bg-indigo-500/15 text-indigo-400 border-indigo-500/30',
          icon: 'fa-arrows-rotate',
        },
        'Broker / Regulatory': {
          bg: 'bg-cyan-500/15 text-cyan-400 border-cyan-500/30',
          icon: 'fa-chart-line',
        },
        'Others': {
          bg: 'bg-slate-700/80 text-slate-300 border-slate-600',
          icon: 'fa-message',
        },
      };
      const c = configs[t] || configs['Others'];
      return `<span class="inline-flex items-center gap-1 px-2 py-0.5 rounded border text-[10px] font-medium tracking-wide ${c.bg}" title="${escapeHtml(t)}"><i class="fa-solid ${c.icon} text-[9px]"></i><span>${escapeHtml(t)}</span></span>`;
    }

    function renderWebhookRow(w) {
      const isRead = Boolean(w.is_read);
      const dateObj = new Date(w.received_at);
      const timeStr = dateObj.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
      const dateStr = dateObj.toLocaleDateString();

      const senderStr = w.sender
        ? `<span class="block max-w-[110px] sm:max-w-[240px] truncate font-mono text-emerald-300 font-semibold" title="${escapeHtml(w.sender)}">${escapeHtml(w.sender)}</span>`
        : '<span class="block text-slate-500 italic">-</span>';

      const typeStr = renderTypeBadge(w.detected_type || w.message_type);

      let preview = '';
      if (w.message_body) {
        preview = `<span class="text-slate-200">${escapeHtml(w.message_body)}</span>`;
      } else if (w.payload_raw) {
        preview = `<span class="text-slate-400 font-mono text-[11px]">${escapeHtml(w.payload_raw.slice(0, 400))}${w.payload_raw.length > 400 ? '…' : ''}</span>`;
      } else {
        preview = '<span class="text-slate-500 italic">(Empty payload)</span>';
      }

      const accent = accentClass(isRead);

      return `
          <tr id="webhook-${w.id}" class="${rowClass(isRead)} cursor-pointer" onclick="inspectWebhook(${w.id})">
            <td class="py-2.5 pl-3 pr-1 sm:py-3 sm:pl-4 sm:pr-1 keep accent-cell ${accent}" onclick="event.stopPropagation()">
              <input type="checkbox" ${isRead ? 'checked' : ''} onchange="toggleWebhookRead(${w.id}, this)" title="Mark as read"
                class="w-4 h-4 rounded bg-slate-800 border-slate-600 accent-emerald-500 focus:ring-0 cursor-pointer">
            </td>
            <td class="py-2.5 px-2 sm:py-3 sm:px-4 align-top">
              ${senderStr}
              <span class="mt-0.5 block whitespace-nowrap font-mono text-[10px] sm:text-[11px] text-slate-400">${timeStr}<span class="hidden sm:inline text-slate-500"> &middot; ${dateStr}</span></span>
              <span class="flex items-center gap-1.5 sm:hidden">
                <span class="whitespace-nowrap text-[10px] text-slate-500">${dateStr}</span>
                <span class="shrink-0">${typeStr}</span>
              </span>
            </td>
            <td class="hidden sm:table-cell py-2.5 px-3 sm:py-3 sm:px-4 align-top whitespace-nowrap">${typeStr}</td>
            <td class="w-full py-2.5 px-2 sm:py-3 sm:px-4 align-top">
              <div class="preview-3 leading-snug">${preview}</div>
            </td>
            <td class="py-2.5 pl-1 pr-2 sm:py-3 sm:px-4 align-top whitespace-nowrap text-right">
              <div class="flex items-center justify-end gap-1">
                <button onclick="event.stopPropagation(); inspectWebhook(${w.id})" class="p-1.5 sm:px-2.5 sm:py-1 text-slate-300 hover:text-white bg-slate-700/70 hover:bg-slate-700 rounded transition" title="View All Details">
                  <i class="fa-solid fa-eye"></i>
                </button>
                <button onclick="event.stopPropagation(); deleteWebhook(${w.id})" class="p-1.5 sm:px-2.5 sm:py-1 text-rose-400 hover:text-rose-300 hover:bg-rose-500/10 rounded transition" title="Delete">
                  <i class="fa-solid fa-trash-can"></i>
                </button>
              </div>
            </td>
          </tr>
        `;
    }

    async function fetchWebhooks() {
      const icon = document.getElementById('refreshIcon');
      icon.classList.add('fa-spin');

      const search = document.getElementById('searchInput').value.trim();
      const source = document.getElementById('sourceFilter').value;
      const typeFilterEl = document.getElementById('typeFilter');
      const type = typeFilterEl ? typeFilterEl.value : '';
      const readFilterEl = document.getElementById('readFilter');
      const read = readFilterEl ? readFilterEl.value : '';

      let url = '/api/webhooks?limit=100';
      if (search) url += '&search=' + encodeURIComponent(search);
      if (source) url += '&source=' + encodeURIComponent(source);
      if (type) url += '&type=' + encodeURIComponent(type);
      if (read) url += '&read=' + encodeURIComponent(read);

      try {
        const list = await api('GET', url);
        currentWebhooks = list || [];
        renderWebhooks(currentWebhooks);
        updateInspectorNavButtons();
        await fetchStats();
      } catch (err) {
        console.error("Fetch error:", err);
      } finally {
        setTimeout(() => icon.classList.remove('fa-spin'), 300);
      }
    }

    function renderWebhooks(items) {
      const tbody = document.getElementById('webhooksBody');
      if (!items || items.length === 0) {
        tbody.innerHTML = `
          <tr>
            <td colspan="5" class="py-12 text-center text-slate-400">
              <i class="fa-solid fa-inbox text-3xl mb-2 text-slate-600 block"></i>
              No webhooks received yet.
            </td>
          </tr>
        `;
        return;
      }

      tbody.innerHTML = items.map(w => renderWebhookRow(w)).join('');
    }

    async function inspectWebhook(id) {
      currentInspectedId = Number(id);
      try {
        const w = await api('GET', '/api/webhooks/' + id);
        currentInspectedId = Number(w.id);

        document.getElementById('modalEventIdBadge').innerText = w.event_id;
        document.getElementById('modalId').innerText = '#' + w.id;
        document.getElementById('modalReceivedAt').innerText = w.received_at;
        document.getElementById('modalMethodPath').innerText = `${w.method} ${w.path} (${w.response_status})`;
        document.getElementById('modalClientIp').innerText = w.client_ip || 'unknown';
        document.getElementById('modalContentType').innerText = w.content_type || 'none';

        document.getElementById('modalDeviceCode').innerText = w.device_code || 'N/A';
        const devIdEl = document.getElementById('modalDeviceId');
        devIdEl.innerText = w.device_id || 'N/A';
        devIdEl.title = w.device_id || '';
        document.getElementById('modalSource').innerText = `${w.source_name || w.source || 'N/A'} (${w.provider || 'generic'})`;

        const modalTypeBadge = document.getElementById('modalTypeBadge');
        if (modalTypeBadge) {
          modalTypeBadge.innerHTML = renderTypeBadge(w.detected_type || w.message_type);
        }
        const modalReadCheckbox = document.getElementById('modalReadCheckbox');
        if (modalReadCheckbox) {
          modalReadCheckbox.checked = Boolean(w.is_read);
        }
        document.getElementById('modalSmsFrom').innerText = w.sender || '(No Sender)';
        document.getElementById('modalOccurredAt').innerText = w.occurred_at ? 'Occurred on Phone: ' + w.occurred_at : '';
        document.getElementById('modalSmsBody').innerText = w.message_body || w.payload_raw || '(Empty Body)';

        // Full payload formatted
        let payloadContent = w.payload_raw || '';
        if (w.payload_json) {
          try {
            payloadContent = JSON.stringify(JSON.parse(w.payload_json), null, 2);
          } catch(e) {
            payloadContent = w.payload_json;
          }
        }
        document.getElementById('modalPayload').innerText = payloadContent || '(Empty Body)';

        // Full headers formatted
        try {
          const hdrs = JSON.parse(w.headers);
          document.getElementById('modalHeaders').innerText = JSON.stringify(hdrs, null, 2);
        } catch(e) {
          document.getElementById('modalHeaders').innerText = w.headers;
        }

        document.getElementById('inspectorModal').classList.remove('hidden');
        updateInspectorNavButtons();
        return true;
      } catch (err) {
        console.error("Inspector error", err);
        return false;
      }
    }

    function updateInspectorNavButtons() {
      const prevBtn = document.getElementById('modalPrevBtn');
      const nextBtn = document.getElementById('modalNextBtn');
      const counterEl = document.getElementById('modalNavCounter');
      if (!prevBtn || !nextBtn) return;

      if (!currentInspectedId || !currentWebhooks || currentWebhooks.length === 0) {
        prevBtn.disabled = true;
        nextBtn.disabled = true;
        if (counterEl) counterEl.innerText = '';
        return;
      }

      const idx = currentWebhooks.findIndex(w => Number(w.id) === currentInspectedId);
      if (idx === -1) {
        prevBtn.disabled = true;
        nextBtn.disabled = true;
        if (counterEl) counterEl.innerText = '';
        return;
      }

      prevBtn.disabled = (idx <= 0);
      nextBtn.disabled = (idx >= currentWebhooks.length - 1);
      if (counterEl) {
        counterEl.innerText = `${idx + 1} of ${currentWebhooks.length}`;
      }
    }

    function navigateInspector(delta) {
      if (!currentWebhooks || currentWebhooks.length === 0) return;
      let idx = currentWebhooks.findIndex(w => Number(w.id) === currentInspectedId);
      if (idx === -1) return;
      const targetIdx = idx + delta;
      if (targetIdx >= 0 && targetIdx < currentWebhooks.length) {
        returnLocation = null;
        inspectWebhook(currentWebhooks[targetIdx].id);
      }
    }

    function closeInspectorModal() {
      if (returnLocation) {
        const dest = returnLocation;
        returnLocation = null;
        window.location.href = dest;
        return;
      }
      document.getElementById('inspectorModal').classList.add('hidden');
      currentInspectedId = null;
      // Drop the ?webhook= deep link so a reload doesn't reopen the inspector
      if (new URLSearchParams(window.location.search).has('webhook')) {
        history.replaceState(null, '', window.location.pathname);
      }
    }

    if (typeof window !== 'undefined') {
      window.addEventListener('keydown', (e) => {
        const curlModal = document.getElementById('curlModal');
        if (curlModal && !curlModal.classList.contains('hidden')) {
          if (e.key === 'Escape') {
            e.preventDefault();
            closeCurlModal();
          }
          return;
        }

        const modal = document.getElementById('inspectorModal');
        if (!modal || modal.classList.contains('hidden')) return;

        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') {
          if (e.key === 'Escape') {
            e.target.blur();
            closeInspectorModal();
          }
          return;
        }

        if (e.key === 'Escape') {
          e.preventDefault();
          closeInspectorModal();
        } else if (e.key === 'ArrowLeft') {
          e.preventDefault();
          navigateInspector(-1);
        } else if (e.key === 'ArrowRight') {
          e.preventDefault();
          navigateInspector(1);
        }
      });
    }

    function openCurlModal() {
      const curlEl = document.getElementById('modalCurlUrl');
      if (curlEl && typeof window !== 'undefined' && window.location.origin) {
        curlEl.innerText = window.location.origin + '/webhook/message487';
      }
      document.getElementById('curlModal').classList.remove('hidden');
    }

    function closeCurlModal() {
      document.getElementById('curlModal').classList.add('hidden');
    }

    async function toggleWebhookRead(id, checkbox) {
      checkbox.disabled = true;
      try {
        await api('PUT', `/api/webhooks/${id}/read`, { read: checkbox.checked });

        const item = currentWebhooks.find(w => Number(w.id) === Number(id));
        if (item) item.is_read = checkbox.checked ? 1 : 0;

        const row = document.getElementById('webhook-' + id);
        if (row) {
          row.className = `${rowClass(checkbox.checked)} cursor-pointer`;
          const accentCell = row.querySelector('.accent-cell');
          if (accentCell) accentCell.classList.toggle('accent-unread', !checkbox.checked);
          const rowCheck = row.querySelector('input[type="checkbox"]');
          if (rowCheck && rowCheck !== checkbox) rowCheck.checked = checkbox.checked;
        }

        if (currentInspectedId === Number(id)) {
          const modalCheck = document.getElementById('modalReadCheckbox');
          if (modalCheck && modalCheck !== checkbox) modalCheck.checked = checkbox.checked;
        }
      } catch (err) {
        checkbox.checked = !checkbox.checked;
        showToast("Failed to update read status", false);
      } finally {
        checkbox.disabled = false;
      }
    }

    async function toggleModalRead(checkbox) {
      if (!currentInspectedId) return;
      await toggleWebhookRead(currentInspectedId, checkbox);
    }

    async function deleteWebhook(id) {
      if (!confirm("Delete this webhook event?")) return;
      try {
        await api('DELETE', '/api/webhooks/' + id);
        await fetchWebhooks();
      } catch (err) {
        showToast("Failed to delete webhook: " + err.message, false);
      }
    }

    async function runBackfill() {
      const btn = document.getElementById('backfillBtn');
      const icon = document.getElementById('backfillIcon');
      if (btn) btn.disabled = true;
      if (icon) icon.className = 'fa-solid fa-spinner fa-spin mr-1';
      try {
        const data = await api('POST', '/api/webhooks/backfill');
        await fetchWebhooks();
        await fetchStats();
        showToast(`Backfill complete: ${data.total_retagged || 0} messages retagged`);
      } catch (err) {
        console.error('Backfill failed:', err);
        showToast('Backfill failed: ' + err.message, false);
      } finally {
        if (btn) btn.disabled = false;
        if (icon) icon.className = 'fa-solid fa-wand-magic-sparkles mr-1';
      }
    }

    async function clearAllData() {
      if (!confirm("Are you sure you want to clear ALL saved webhooks from the database?")) return;
      try {
        await api('DELETE', '/api/webhooks');
        await fetchWebhooks();
      } catch (err) {
        showToast("Failed to clear webhooks: " + err.message, false);
      }
    }

    if (typeof document !== 'undefined') {
      // Auto-refresh control
      const autoToggle = document.getElementById('autoRefreshToggle');
      function setupAutoRefresh() {
        if (autoToggle.checked) {
          if (!autoRefreshInterval) {
            autoRefreshInterval = setInterval(fetchWebhooks, 5000);
          }
        } else {
          if (autoRefreshInterval) {
            clearInterval(autoRefreshInterval);
            autoRefreshInterval = null;
          }
        }
      }
      autoToggle.addEventListener('change', setupAutoRefresh);

      // Deep link filter pre-selection: /dashboard?type=...
      const initialType = new URLSearchParams(window.location.search).get('type');
      if (initialType && document.getElementById('typeFilter')) {
        document.getElementById('typeFilter').value = initialType;
      }

      fetchWebhooks();
      setupAutoRefresh();

      // Deep link from the transactions page: /dashboard?webhook=<id>
      const deepLinkId = new URLSearchParams(window.location.search).get('webhook');
      if (deepLinkId) {
        inspectWebhook(deepLinkId).then(ok => {
          if (!ok) alert(`Message #${deepLinkId} was not found. It may have been deleted.`);
        });
      }
    }

    if (typeof module !== 'undefined' && module.exports) {
      module.exports = {
        renderTypeBadge,
        renderWebhookRow,
        renderWebhooks,
        toggleWebhookRead,
        toggleModalRead,
      };
    }
