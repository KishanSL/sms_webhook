const inputClass = "w-full bg-slate-900 border border-slate-700 rounded-lg px-2 py-1.5 text-xs text-white placeholder-slate-500 focus:outline-none focus:border-emerald-500";

    // --- Banks ---

    async function loadBanks() {
      const banks = await api('GET', '/api/banks');
      const tbody = document.getElementById('banksBody');
      document.getElementById('bankNameOptions').innerHTML = [...new Set(banks.map(b => b.name))]
        .map(n => `<option value="${escapeHtml(n)}">`).join('');

      if (!banks.length) {
        tbody.innerHTML = emptyRow(4, 'No bank codes linked.');
        return;
      }
      tbody.innerHTML = banks.map(b => `
        <tr class="hover:bg-slate-800/60">
          <td class="py-2.5 px-4 font-mono text-emerald-300 font-semibold">${escapeHtml(b.sender_header)}</td>
          <td class="py-2.5 px-4">
            <input id="bank-${escapeHtml(b.sender_header)}" value="${escapeHtml(b.name)}" list="bankNameOptions"
              onkeydown="if (event.key === 'Enter') saveBank('${escapeHtml(b.sender_header)}')" class="${inputClass}">
          </td>
          <td class="py-2.5 px-4 text-right font-mono">${b.txn_count}</td>
          <td class="py-2.5 px-4 text-right whitespace-nowrap">
            <button onclick="saveBank('${escapeHtml(b.sender_header)}')" class="px-2.5 py-1 text-slate-300 hover:text-white bg-slate-700/70 hover:bg-slate-700 rounded transition mr-1" title="Save name">
              <i class="fa-solid fa-floppy-disk"></i>
            </button>
            <button onclick="removeBank('${escapeHtml(b.sender_header)}')" class="px-2.5 py-1 text-rose-400 hover:text-rose-300 hover:bg-rose-500/10 rounded transition" title="Unlink">
              <i class="fa-solid fa-link-slash"></i>
            </button>
          </td>
        </tr>
      `).join('');
    }

    async function linkBank(header, name) {
      try {
        const res = await api('PUT', `/api/banks/${encodeURIComponent(header)}`, { name });
        const added = res.transactions_added;
        showToast(`${res.sender_header} → ${res.name}` + (added ? ` · ${added} new transaction${added > 1 ? 's' : ''} parsed` : ''));
        await loadAll();
        return true;
      } catch (err) {
        showToast(err.message, false);
        return false;
      }
    }

    function saveBank(header) {
      return linkBank(header, document.getElementById('bank-' + header).value.trim());
    }

    async function addBank() {
      const headerEl = document.getElementById('newHeader');
      const nameEl = document.getElementById('newBankName');
      if (await linkBank(headerEl.value.trim().toUpperCase(), nameEl.value.trim())) {
        headerEl.value = '';
        nameEl.value = '';
      }
    }

    async function removeBank(header) {
      if (!confirm(`Unlink ${header}? Its existing transactions are kept, but new SMS from it won't be parsed.`)) return;
      try {
        await api('DELETE', `/api/banks/${encodeURIComponent(header)}`);
        showToast(`${header} unlinked`);
        await loadAll();
      } catch (err) {
        showToast(err.message, false);
      }
    }

    // --- Unlinked senders ---

    async function loadSenders() {
      const senders = (await api('GET', '/api/senders')).filter(s => !s.bank);
      const tbody = document.getElementById('sendersBody');
      if (!senders.length) {
        tbody.innerHTML = emptyRow(4, '<i class="fa-solid fa-circle-check text-emerald-500 mr-1"></i>Every sender code seen is linked.');
        return;
      }
      tbody.innerHTML = senders.map(s => `
        <tr class="hover:bg-slate-800/60">
          <td class="py-2.5 px-4 font-mono text-amber-300 font-semibold">${escapeHtml(s.sender_header)}</td>
          <td class="py-2.5 px-4 font-mono text-slate-400 text-[11px]">${s.senders.map(escapeHtml).join(', ')}</td>
          <td class="py-2.5 px-4 text-right font-mono">${s.message_count}</td>
          <td class="py-2.5 px-4 text-right whitespace-nowrap">
            <button onclick="startLink('${escapeHtml(s.sender_header)}')" class="px-2.5 py-1 text-emerald-300 hover:text-white bg-emerald-600/20 hover:bg-emerald-600 rounded transition">
              <i class="fa-solid fa-link mr-1"></i>Link to bank
            </button>
          </td>
        </tr>
      `).join('');
    }

    function startLink(header) {
      document.getElementById('newHeader').value = header;
      const nameEl = document.getElementById('newBankName');
      nameEl.focus();
      nameEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }

    // --- Cards & accounts ---

    async function loadAccounts() {
      const accounts = await api('GET', '/api/accounts');
      const tbody = document.getElementById('accountsBody');
      if (!accounts.length) {
        tbody.innerHTML = emptyRow(GNUCASH_ENABLED ? 7 : 6, 'No cards or accounts seen in transactions yet.');
        return;
      }
      tbody.innerHTML = accounts.map(a => {
        const key = `${a.sender_header}:${a.account_last4}`;
        const kind = a.instrument === 'account' ? 'Account' : 'Credit Card';
        const lastUsed = a.last_used_at ? new Date(a.last_used_at).toLocaleDateString() : '-';
        return `
          <tr class="hover:bg-slate-800/60">
            <td class="py-2.5 px-4 whitespace-nowrap">
              <span class="text-slate-200 font-medium">${escapeHtml(a.bank)}</span>
              <span class="text-[10px] text-slate-500 font-mono block">${escapeHtml(a.sender_header)}</span>
            </td>
            <td class="py-2.5 px-4 whitespace-nowrap">
              <span class="text-slate-400">${kind}</span> <span class="font-mono text-slate-200">••${escapeHtml(a.account_last4)}</span>
            </td>
            <td class="py-2.5 px-4 min-w-[200px]">
              <input id="acct-${escapeHtml(key)}" value="${escapeHtml(a.name || '')}" placeholder="e.g. HDFC Tata Neu"
                onkeydown="if (event.key === 'Enter') saveAccount('${escapeHtml(a.sender_header)}', '${escapeHtml(a.account_last4)}')" class="${inputClass}">
            </td>
            ${GNUCASH_ENABLED ? `
            <td class="py-2.5 px-4 min-w-[240px]">
              <input type="text" list="gnucashAccountList" autocomplete="off" spellcheck="false"
                value="${escapeHtml(a.gnucash_account_id ? gnucashNameFor(a.gnucash_account_id) : '')}"
                placeholder="— not mapped —"
                onchange="saveGnucashMapping('${escapeHtml(a.sender_header)}', '${escapeHtml(a.account_last4)}', this.value)"
                title="${escapeHtml(a.gnucash_account_id ? gnucashPathFor(a.gnucash_account_id) : 'Search a leaf account to map this card')}"
                class="w-full bg-slate-900 border border-slate-700 rounded-lg px-2 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-emerald-500">
            </td>` : ''}
            <td class="py-2.5 px-4 text-right font-mono">${a.txn_count}</td>
            <td class="py-2.5 px-4 font-mono text-slate-400 whitespace-nowrap">${lastUsed}</td>
            <td class="py-2.5 px-4 text-right whitespace-nowrap">
              <button onclick="saveAccount('${escapeHtml(a.sender_header)}', '${escapeHtml(a.account_last4)}')" class="px-2.5 py-1 text-slate-300 hover:text-white bg-slate-700/70 hover:bg-slate-700 rounded transition mr-1" title="Save name">
                <i class="fa-solid fa-floppy-disk"></i>
              </button>
              <a href="/transactions?account=${encodeURIComponent(key)}" class="px-2.5 py-1 text-slate-300 hover:text-white bg-slate-700/70 hover:bg-slate-700 rounded transition inline-block" title="View transactions">
                <i class="fa-solid fa-list mr-1"></i>Txns
              </a>
            </td>
          </tr>
        `;
      }).join('');
    }

    async function saveAccount(header, last4) {
      const name = document.getElementById(`acct-${header}:${last4}`).value.trim();
      try {
        await api('PUT', `/api/accounts/${encodeURIComponent(header)}/${encodeURIComponent(last4)}`, { name });
        showToast(name ? `••${last4} → ${name}` : `Name cleared for ••${last4}`);
        await loadAccounts();
      } catch (err) {
        showToast(err.message, false);
      }
    }

    // --- GnuCash mapping ---

    async function saveGnucashMapping(header, last4, value) {
      const accountId = gnucashResolveId(value);
      if (value.trim() && !accountId) {
        showToast('Pick an account from the list', false);
        await loadAccounts();
        return;
      }
      try {
        await api('PUT', `/api/accounts/${encodeURIComponent(header)}/${encodeURIComponent(last4)}/gnucash`, { gnucash_account_id: accountId });
        showToast(accountId ? `••${last4} mapped to gnucash` : `••${last4} unmapped`);
      } catch (err) {
        showToast(err.message, false);
        await loadAccounts();
      }
    }

    // --- Data export / import / reset ---

    function exportData() {
      window.location.href = '/api/export';
    }

    async function importData(input) {
      const file = input.files && input.files[0];
      input.value = '';
      if (!file) return;
      const mode = prompt(`Import ${file.name}.\n\nType "replace" to wipe all current data first, or "merge" to keep it and add/skip duplicates:`, 'merge');
      if (mode !== 'merge' && mode !== 'replace') return;
      if (mode === 'replace' && !confirm('Replace ALL current data with this file? This cannot be undone.')) return;
      try {
        const payload = JSON.parse(await file.text());
        const res = await api('POST', `/api/import?mode=${mode}`, payload);
        const counts = res.counts || {};
        const summary = Object.entries(counts).map(([k, v]) => `${k} ${v}`).join(', ');
        showToast(`Imported (${mode}): ${summary}`);
        await loadAll();
      } catch (err) {
        showToast(err.message, false);
      }
    }

    async function clearAllData() {
      if (!confirm('Delete ALL data — messages, transactions, banks, card names, gnucash mapping and rules? The database goes back to a fresh state.')) return;
      if (prompt('Type DELETE to confirm:') !== 'DELETE') return;
      try {
        const res = await api('DELETE', '/api/data');
        const total = Object.values(res.deleted || {}).reduce((a, b) => a + b, 0);
        showToast(`Cleared ${total} rows`);
        await loadAll();
      } catch (err) {
        showToast(err.message, false);
      }
    }

    async function loadAll() {
      const icon = document.getElementById('refreshIcon');
      icon.classList.add('fa-spin');
      try {
        await loadGnucashAccounts();
        await Promise.all([loadBanks(), loadSenders(), loadAccounts()]);
      } catch (err) {
        console.error("Load error:", err);
        showToast(err.message, false);
      } finally {
        setTimeout(() => icon.classList.remove('fa-spin'), 300);
      }
    }

    loadAll();
