const PAGE_SIZE = 100;
let offset = 0;
let accounts = {};  // "HDFCBK:1111" -> account record from /api/accounts
const UNCAT_LABEL = globalThis.UNCAT_LABEL || 'Uncategorised';

function accountLabel(a) {
  const kind = a.instrument === 'account' ? 'A/c' : 'Card';
  return a.name ? `${a.name} (••${a.account_last4})` : `${a.bank} ${kind} ••${a.account_last4}`;
}

    async function loadAccounts() {
      try {
        const list = await api('GET', '/api/accounts');
        accounts = Object.fromEntries(list.map(a => [`${a.sender_header}:${a.account_last4}`, a]));

        const select = document.getElementById('accountFilter');
        const selected = select.value;
        select.innerHTML = '<option value="">All Cards &amp; Accounts</option>' + list
          .map(a => `<option value="${escapeHtml(a.sender_header + ':' + a.account_last4)}">${escapeHtml(accountLabel(a))}</option>`)
          .join('');
        select.value = selected;
      } catch (err) {
        console.error("Failed to load accounts", err);
      }
    }

    async function renameAccount(key) {
      const a = accounts[key];
      if (!a) return;
      const name = prompt(`Name for ${a.bank} ••${a.account_last4} (leave empty to clear):`, a.name || '');
      if (name === null) return;

      try {
        await api('PUT', `/api/accounts/${encodeURIComponent(a.sender_header)}/${encodeURIComponent(a.account_last4)}`, { name });
        await loadAccounts();
        await fetchTransactions();
      } catch (err) {
        alert("Failed to save name: " + err.message);
      }
    }

    function resetAndFetch() {
      offset = 0;
      fetchTransactions();
    }

    function changePage(step) {
      offset = Math.max(0, offset + step * PAGE_SIZE);
      fetchTransactions();
    }

    async function fetchTransactions() {
      const icon = document.getElementById('refreshIcon');
      icon.classList.add('fa-spin');

      let bank = document.getElementById('bankFilter').value;
      const direction = document.getElementById('directionFilter').value;
      let account = '';
      // A selected card / account ("HDFCBK:1111") narrows to its bank as well
      const accountKey = document.getElementById('accountFilter').value;
      if (accountKey) [bank, account] = accountKey.split(':');
      const read = document.getElementById('readFilter').value;

      // Fetch one extra row to know whether an older page exists
      let url = `/api/transactions?limit=${PAGE_SIZE + 1}&offset=${offset}`;
      if (bank) url += '&bank=' + encodeURIComponent(bank);
      if (direction) url += '&direction=' + encodeURIComponent(direction);
      if (account) url += '&account=' + encodeURIComponent(account);
      if (read) url += '&read=' + read;

      try {
        const list = await api('GET', url);
        const hasMore = list.length > PAGE_SIZE;
        const items = list.slice(0, PAGE_SIZE);
        renderTransactions(items);

        document.getElementById('prevBtn').disabled = offset === 0;
        document.getElementById('nextBtn').disabled = !hasMore;
        document.getElementById('pageInfo').innerText = items.length
          ? `${offset + 1}–${offset + items.length}`
          : '';
      } catch (err) {
        console.error("Fetch error:", err);
      } finally {
        setTimeout(() => icon.classList.remove('fa-spin'), 300);
      }
    }

    function renderTransactionRow(t, gnucashEnabled) {
      const gnucashActive = typeof gnucashEnabled === 'boolean'
        ? gnucashEnabled
        : Boolean(globalThis.GNUCASH_ENABLED);
      const dateObj = new Date(t.occurred_at);
      const timeStr = dateObj.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      const dateStr = dateObj.toLocaleDateString();

      const isCredit = t.direction === 'credit';
      const amountStr = `<span class="font-mono font-semibold ${isCredit ? 'text-emerald-400' : 'text-rose-400'}">${isCredit ? '+' : '−'}${inr.format(t.amount)}</span>`;

      const instrumentLabel = t.instrument === 'account' ? 'A/c' : 'Card';
      const channelStr = t.channel ? `<span class="ml-1 px-1.5 py-0.5 rounded bg-slate-700/80 text-slate-300 text-[10px] uppercase font-mono">${escapeHtml(t.channel)}</span>` : '';
      const numberStr = `<span class="text-slate-400">${instrumentLabel}</span> <span class="font-mono ${t.account_name ? 'text-slate-400' : 'text-slate-200'}">••${escapeHtml(t.account_last4 || '????')}</span>${channelStr}`;
      const renameBtn = t.account_last4
        ? `<button onclick="renameAccount('${escapeHtml(t.sender_header + ':' + t.account_last4)}')" class="ml-1.5 text-slate-500 hover:text-emerald-400 transition" title="Rename card / account"><i class="fa-solid fa-pen text-[10px]"></i></button>`
        : '';
      const accountStr = t.account_name
        ? `<span class="text-slate-100 font-medium">${escapeHtml(t.account_name)}</span>${renameBtn}<span class="text-[11px] block mt-0.5">${numberStr}</span>`
        : `${numberStr}${renameBtn}`;

      const balanceStr = (t.balance !== null && t.balance !== undefined)
        ? `<span class="font-mono text-slate-300">${inr.format(t.balance)}</span><span class="text-[10px] text-slate-500 block">${t.balance_type === 'limit' ? 'avail. limit' : 'balance'}</span>`
        : '<span class="text-slate-600">-</span>';

      const linkStr = t.webhook_id
        ? `<a href="/dashboard?webhook=${encodeURIComponent(t.webhook_id)}&from=transactions" class="px-2.5 py-1 text-slate-300 hover:text-white bg-slate-700/70 hover:bg-slate-700 rounded transition inline-block" title="${escapeHtml(t.raw_text)}"><i class="fa-solid fa-message mr-1"></i>View</a>`
        : '<span class="text-slate-600">-</span>';

      // A row that is in gnucash-web (posted, or left in the ledger by the old edit flow) is
      // final: it cannot be selected for a bulk post.
      const locked = Boolean(t.in_ledger || t.gnucash_tx_id);
      const accent = accentClass(t.is_read);

      return `
          <tr id="txn-${t.id}" class="${rowClass(t.is_read)}">
            ${gnucashActive ? `<td class="py-3 pl-4 pr-1 align-middle keep accent-cell ${accent}">
              <input type="checkbox" class="review-check w-4 h-4 rounded bg-slate-800 border-slate-600 accent-sky-500 focus:ring-0 ${locked ? 'cursor-not-allowed opacity-40' : 'cursor-pointer'}" data-review="${t.id}" onchange="updateSelectAll()" title="${locked ? 'Already posted to gnucash-web' : 'Select for bulk post'}" ${locked ? 'disabled' : ''}>
            </td>` : ''}
            <td class="py-3 pl-4 pr-1 keep ${gnucashActive ? '' : `accent-cell ${accent}`}">
              <input type="checkbox" ${t.is_read ? 'checked' : ''} onchange="toggleRead(${t.id}, this)" title="Mark as read"
                class="w-4 h-4 rounded bg-slate-800 border-slate-600 accent-emerald-500 focus:ring-0 cursor-pointer">
            </td>
            <td class="py-3 px-4 whitespace-nowrap">
              <span class="text-slate-200 font-medium">${escapeHtml(t.bank)}</span>
              <span class="text-[10px] text-slate-500 font-mono block">${escapeHtml(t.sender_header)}</span>
              <span class="text-[10px] text-slate-500 font-mono block">${timeStr} &middot; ${dateStr}</span>
            </td>
            <td class="py-3 px-4 whitespace-nowrap">${accountStr}</td>
            <td class="py-3 px-4 max-w-xs truncate text-slate-200" title="${escapeHtml(t.counterparty || '')}">${escapeHtml(t.counterparty || '-')}</td>
            <td class="py-3 px-4 text-right whitespace-nowrap">${amountStr}</td>
            <td class="py-3 px-4 text-right whitespace-nowrap">${balanceStr}</td>
            <td class="py-3 px-4 text-right whitespace-nowrap">${linkStr}</td>
            ${gnucashCell(t, gnucashActive)}
          </tr>
        `;
    }

    function renderTransactions(items) {
      const tbody = document.getElementById('txnBody');
      const gnucashActive = Boolean(globalThis.GNUCASH_ENABLED);
      if (!items || items.length === 0) {
        tbody.innerHTML = `
          <tr>
            <td colspan="${gnucashActive ? 9 : 7}" class="py-12 text-center text-slate-400">
              <i class="fa-solid fa-receipt text-3xl mb-2 text-slate-600 block"></i>
              No transactions found.
            </td>
          </tr>
        `;
        return;
      }

      tbody.innerHTML = items.map(t => renderTransactionRow(t)).join('');
      const selectAll = document.getElementById('selectAll');
      if (selectAll) { selectAll.checked = false; selectAll.indeterminate = false; }
      updateSelectAll();
    }

    // --- GnuCash-web ---

    function refreshGnucashAccounts() {
      const icon = document.getElementById('gnucashRefreshIcon');
      icon.classList.add('fa-spin');
      loadGnucashAccounts(true).finally(() => {
        setTimeout(() => icon.classList.remove('fa-spin'), 300);
        fetchTransactions();
      });
    }

    function toggleSelectAll(master) {
      document.querySelectorAll('#txnBody .review-check:not(:disabled)').forEach(cb => { cb.checked = master.checked; });
      updateSelectAll();
    }

    // Keep the header checkbox (and the Post selected button) in step with the row ticks.
    function updateSelectAll() {
      const boxes = [...document.querySelectorAll('#txnBody .review-check')].filter(cb => !cb.disabled);
      const checked = boxes.filter(cb => cb.checked);
      const master = document.getElementById('selectAll');
      if (master) {
        master.checked = boxes.length > 0 && checked.length === boxes.length;
        master.indeterminate = checked.length > 0 && checked.length < boxes.length;
      }
      const postBtn = document.getElementById('postSelectedBtn');
      if (postBtn) {
        postBtn.disabled = !checked.some(cb => selectedTo(cb.getAttribute('data-review')) !== '');
      }
    }

    function gnucashCell(t, gnucashEnabled) {
      const active = typeof gnucashEnabled === 'boolean'
        ? gnucashEnabled
        : Boolean(globalThis.GNUCASH_ENABLED);
      if (!active) return '';
      const status = t.post_status || 'new';
      // Once a row is posted to gnucash-web it is final: read-only, no Post button.
      const posted = t.in_ledger || Boolean(t.gnucash_tx_id);
      let badge = '';
      if (posted) {
        badge = `<span class="px-1.5 py-0.5 rounded bg-emerald-600/20 text-emerald-300 text-[10px] font-semibold" title="Posted to gnucash-web — final there">Posted</span>`;
      } else if (status === 'posting') {
        badge = `<span class="text-[10px] text-amber-300">Posting…</span>`;
      } else if (status === 'failed') {
        badge = `<span class="text-[10px] text-rose-400">Failed</span>`;
      }
      const fromText = t.from_account_id
        ? (gnucashNameFor(t.from_account_id) || '(missing From account)')
        : 'No card mapping';
      const fromPath = t.from_account_id ? gnucashPathFor(t.from_account_id) : '';
      const fromLabel = t.from_account_id
        ? escapeHtml(fromText)
        : `<span class="text-amber-400">${escapeHtml(fromText)}</span>`;
      const selected = t.suggested_to_account_id || '';
      const selectedName = selected ? gnucashNameFor(selected) : '';
      const selectedPath = selected ? gnucashPathFor(selected) : '';
      // Post stays disabled until a From mapping and a real To exist, and never on a posted row.
      const disabled = (posted || !t.from_account_id || !selected) ? 'disabled' : '';
      const reason = t.suggestion_reason ? `<span class="text-[10px] text-slate-500 block" title="why this To was suggested">${escapeHtml(t.suggestion_reason)}</span>` : '';
      const err = !posted && status === 'failed' && t.post_error ? `<span class="text-[10px] text-rose-400 block">${escapeHtml(t.post_error)}</span>` : '';
      return `
        <td class="py-3 px-4 align-top keep" data-gnucash="${t.id}">
          <div class="flex items-center gap-1.5 mb-1">
            <i class="fa-solid fa-arrow-right-long text-slate-500"></i>
            <span class="text-[11px] text-slate-300 truncate max-w-[190px]" title="${escapeHtml(fromPath || fromText)}">${fromLabel}</span>
            ${badge}
          </div>
          <div class="flex items-center gap-1.5">
            <input type="text" list="gnucashAccountList" data-to="${t.id}" value="${escapeHtml(selectedName)}"
              placeholder="${escapeHtml(UNCAT_LABEL)}" autocomplete="off" spellcheck="false" ${posted ? 'readonly' : ''}
              onchange="onToChange(${t.id}, this.value, ${posted})"
              title="${escapeHtml(posted ? (selectedPath || 'Posted to gnucash-web') : (selectedPath || 'Search a leaf account, or leave blank for ' + UNCAT_LABEL))}"
              class="bg-slate-900 border border-slate-700 rounded-lg px-2 py-1 text-[11px] text-slate-200 focus:outline-none focus:border-emerald-500 w-[190px] ${posted ? 'opacity-70 cursor-not-allowed' : ''}">
            ${posted ? '' : `<button onclick="postTransaction(${t.id})" ${disabled} data-no-from="${t.from_account_id ? '0' : '1'}" class="px-2 py-1 rounded bg-emerald-600 hover:bg-emerald-500 disabled:opacity-40 disabled:cursor-not-allowed text-white text-[11px]" title="Post to gnucash-web">
              <i class="fa-solid fa-paper-plane"></i>
            </button>`}
          </div>
          ${reason}${err}
        </td>
      `;
    }

    function onToChange(id, value, posted) {
      const accountId = gnucashResolveId(value);
      const btn = document.querySelector(`[data-gnucash="${id}"] button`);
      if (btn) btn.disabled = !accountId || btn.dataset.noFrom === '1';
      // Save the choice (or clear it when the field is emptied); ignore unmatched partial text.
      if (!posted && (accountId || value.trim() === '')) saveToChoice(id, accountId);
      updateSelectAll();
    }

    async function saveToChoice(id, value) {
      try {
        await api('PUT', `/api/transactions/${id}/to`, { to_account_id: value });
      } catch (err) {
        alert(err.message);
        await fetchTransactions();
      }
    }

    function selectedTo(id) {
      const input = document.querySelector(`input[data-to="${id}"]`);
      return input ? gnucashResolveId(input.value) : '';
    }

    async function postTransaction(id, force = false) {
      const to = selectedTo(id);
      if (!to) { alert(`Choose a To account first (currently ${UNCAT_LABEL}).`); return; }
      const btn = document.querySelector(`[data-gnucash="${id}"] button`);
      if (btn) btn.disabled = true;
      try {
        await api('POST', `/api/transactions/${id}/post`, { to_account_id: to, force });
        await fetchTransactions();
      } catch (err) {
        if (err.status === 409 && err.data?.detail?.transaction_id) {
          const d = err.data.detail;
          if (confirm(`Possible duplicate in gnucash-web: "${d.description}" on ${d.post_date}. Post anyway?`)) {
            return postTransaction(id, true);
          }
          if (btn) btn.disabled = false;
          return;
        }
        alert(err.message);
        if (btn) btn.disabled = false;
      }
    }

    async function sendBulk(posts, force) {
      try {
        return await api('POST', '/api/transactions/post-reviewed', { posts, force });
      } catch (err) {
        alert(err.message);
        return null;
      }
    }

    async function postReviewed() {
      const posts = [];
      document.querySelectorAll('#txnBody tr').forEach(tr => {
        const cb = tr.querySelector('.review-check');
        if (!cb || !cb.checked) return;
        const id = parseInt(tr.id.replace('txn-', ''), 10);
        const to = selectedTo(id);
        if (!isNaN(id) && to) posts.push({ id, to_account_id: to });
      });
      if (!posts.length) {
        alert(`Select at least one row that has a To account. Rows showing ${UNCAT_LABEL} need an account first.`);
        return;
      }
      if (!confirm(`Post ${posts.length} selected row(s) to gnucash-web?`)) return;
      await runBulk(posts, false);
    }

    async function runBulk(posts, force) {
      const data = await sendBulk(posts, force);
      if (!data) return;
      const results = data.results || [];
      const duplicates = results.filter(r => !r.ok && r.code === 'duplicate');
      if (duplicates.length && !force) {
        const retry = duplicates
          .map(r => ({ id: r.transaction_id, to_account_id: selectedTo(r.transaction_id) }))
          .filter(p => p.to_account_id);
        if (retry.length && confirm(`${duplicates.length} possible duplicate(s):\n` +
            duplicates.slice(0, 5).map(r => r.error).join('\n') + `\n\nPost ${retry.length} of them anyway?`)) {
          await runBulk(retry, true);
          return;
        }
        // Cancelled: fall through and still show the summary + refresh.
      }
      const failed = results.filter(r => !r.ok);
      alert(`Posted ${data.posted} of ${data.total}.` +
        (failed.length ? `\nNot posted:\n` + failed.slice(0, 5).map(r => r.error || r.code).join('\n') : ''));
      await fetchTransactions();
    }

    async function learnFromGnucash() {
      if (!confirm('Read your gnucash-web history and learn merchant → account hints?')) return;
      try {
        const data = await api('POST', '/api/gnucash/learn');
        alert(data.message || `Learned ${data.learned} hints.`);
        await fetchTransactions();
      } catch (err) {
        alert(err.message);
      }
    }

    async function toggleRead(id, checkbox) {
      checkbox.disabled = true;
      try {
        await api('PUT', `/api/transactions/${id}/read`, { read: checkbox.checked });
        // Update in place rather than refetching, so a row doesn't vanish under an "Unread only" filter
        const row = document.getElementById('txn-' + id);
        row.className = rowClass(checkbox.checked);
        const accentCell = row.querySelector('.accent-cell');
        if (accentCell) accentCell.classList.toggle('accent-unread', !checkbox.checked);
      } catch (err) {
        checkbox.checked = !checkbox.checked;
        alert("Failed to update read status");
      } finally {
        checkbox.disabled = false;
      }
    }

    // Deep link from Settings: /transactions?account=HDFCBK:1111
    if (typeof document !== 'undefined') {
      (async () => {
        await loadAccounts();
        const gnucashActive = Boolean(globalThis.GNUCASH_ENABLED);
        if (gnucashActive && typeof loadGnucashAccounts === 'function') await loadGnucashAccounts();
        const accountKey = new URLSearchParams(window.location.search).get('account');
        if (accountKey && accounts[accountKey]) {
          document.getElementById('accountFilter').value = accountKey;
        }
        fetchTransactions();
      })();
    }

    if (typeof module !== 'undefined' && module.exports) {
      module.exports = {
        accountLabel,
        renderTransactionRow,
        gnucashCell,
      };
    }
