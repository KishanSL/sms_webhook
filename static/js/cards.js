function daysUntil(dateStr) {
  if (!dateStr) return null;
  const due = new Date(dateStr + 'T00:00:00');
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return Math.round((due - today) / 86400000);
}

function dueCell(card) {
  if (card.due_date == null) {
    if (card.statement_date) {
      const d = daysUntil(card.statement_date);
      const when = d < 0 ? `${-d}d ago` : d === 0 ? 'today' : `in ${d}d`;
      return `<span class="text-slate-300 font-mono text-xs">${escapeHtml(card.statement_date)}<span class="text-[10px] block text-slate-500">stmt (${when})</span></span>`;
    }
    return '<span class="text-slate-600">-</span>';
  }
  const d = daysUntil(card.due_date);
  const when = d < 0 ? `overdue by ${-d}d` : d === 0 ? 'due today' : `in ${d}d`;
  const cls = card.paid ? 'text-slate-500' : d < 0 ? 'text-rose-400' : d <= 3 ? 'text-amber-400' : 'text-slate-300';
  return `<span class="${cls}">${escapeHtml(card.due_date)}<span class="text-[10px] block">${when}</span></span>`;
}

function renderCardRow(c) {
  const key = `${c.sender_header}:${c.account_last4}`;
  const l4 = c.account_last4 == null ? '_' : c.account_last4;  // sentinel for a card with no number
  const label = c.account_last4 == null ? '••----' : `••${escapeHtml(c.account_last4)}`;
  const cardName = c.name ? `${escapeHtml(c.name)} <span class="font-mono text-slate-400">${label}</span>` : `${escapeHtml(c.bank || '')} <span class="font-mono">${label}</span>`;
  const hasStmt = Boolean(c.has_statement ?? (c.statement_date != null || c.total_due != null));
  const due = c.total_due != null
    ? `<span class="${c.paid ? 'line-through text-slate-500' : 'text-rose-400 font-semibold'} font-mono">${inr0.format(c.total_due)}</span>`
    : (hasStmt ? `<span class="${c.paid ? 'line-through text-slate-500' : 'text-slate-400'} text-xs italic">generated</span>` : '<span class="text-slate-600">-</span>');
  const min = c.min_due != null ? `<span class="font-mono ${c.paid ? 'text-slate-600' : 'text-slate-300'}">${inr0.format(c.min_due)}</span>` : '<span class="text-slate-600">-</span>';
  const paidBox = !hasStmt
    ? '<span class="text-slate-600 text-[10px]">no statement</span>'
    : `<label class="inline-flex items-center gap-1.5 cursor-pointer" title="Mark this statement paid">
         <input type="checkbox" ${c.paid ? 'checked' : ''} onchange="togglePaid('${escapeHtml(c.sender_header)}', '${l4}', this)"
           class="w-4 h-4 rounded bg-slate-800 border-slate-600 accent-emerald-500 focus:ring-0 cursor-pointer">
         <span class="text-[11px] ${c.paid ? 'text-emerald-400' : 'text-slate-500'}">${c.paid ? 'Paid' : ''}</span>
       </label>`;
  const viewTitle = c.raw_text || (hasStmt ? 'View statement message' : 'View transaction message');
  const linkStr = c.webhook_id
    ? `<a href="/dashboard?webhook=${encodeURIComponent(c.webhook_id)}&from=cards" class="px-2.5 py-1 text-slate-300 hover:text-white bg-slate-700/70 hover:bg-slate-700 rounded transition inline-block" title="${escapeHtml(viewTitle)}"><i class="fa-solid fa-message mr-1"></i>View</a>`
    : '<span class="text-slate-600">-</span>';
  return `
    <tr id="card-${escapeHtml(key)}" class="${c.paid ? 'opacity-50' : ''} hover:bg-slate-800/60 transition">
      <td class="py-3 px-4 whitespace-nowrap">${cardName}<span class="text-[10px] text-slate-500 font-mono block">${escapeHtml(c.bank || '')}</span></td>
      <td class="py-3 px-4 text-right whitespace-nowrap">
        <div>${due}</div>
        <div class="mt-0.5 text-[10px] text-slate-500 font-mono">min ${min}</div>
      </td>
      <td class="py-3 px-4 whitespace-nowrap">${dueCell(c)}</td>
      <td class="py-3 px-4 text-center">${paidBox}</td>
      <td class="py-3 px-4 text-right whitespace-nowrap">${linkStr}</td>
    </tr>
  `;
}

function renderCards(cards) {
  if (typeof document === 'undefined') return;
  const tbody = document.getElementById('cardsBody');
  const cardCountEl = document.getElementById('cardCount');
  if (cardCountEl) cardCountEl.innerText = cards.length;

  const pending = cards.filter(c => !c.paid && (c.total_due != null || c.has_statement));
  const total = cards.filter(c => !c.paid && c.total_due != null).reduce((sum, c) => sum + c.total_due, 0);
  const totalPendingEl = document.getElementById('totalPending');
  if (totalPendingEl) totalPendingEl.innerText = inr0.format(total);

  const upcoming = pending
    .filter(c => c.due_date)
    .sort((a, b) => a.due_date.localeCompare(b.due_date))[0];
  const nextDueEl = document.getElementById('nextDue');
  if (nextDueEl) {
    nextDueEl.innerText = upcoming
      ? `${upcoming.name || ('••' + (upcoming.account_last4 || '----'))} · ${upcoming.due_date}`
      : 'Nothing pending';
  }

  if (!cards.length) {
    if (tbody) tbody.innerHTML = '<tr><td colspan="5" class="py-12 text-center text-slate-400"><i class="fa-solid fa-credit-card text-3xl mb-2 text-slate-600 block"></i>No credit cards found yet.</td></tr>';
    return;
  }

  if (tbody) tbody.innerHTML = cards.map(c => renderCardRow(c)).join('');
}

async function fetchCards() {
  if (typeof document === 'undefined') return;
  const icon = document.getElementById('refreshIcon');
  if (icon) icon.classList.add('fa-spin');
  try {
    const cards = await api('GET', '/api/cards');
    renderCards(cards);
  } catch (err) {
    console.error(err);
  } finally {
    if (icon) setTimeout(() => icon.classList.remove('fa-spin'), 300);
  }
}

async function togglePaid(header, last4, checkbox) {
  checkbox.disabled = true;
  try {
    await api('PUT', `/api/cards/${encodeURIComponent(header)}/${encodeURIComponent(last4)}/paid`, { paid: checkbox.checked });
    await fetchCards();
  } catch (err) {
    checkbox.checked = !checkbox.checked;
    alert(err.message);
  } finally {
    checkbox.disabled = false;
  }
}

if (typeof document !== 'undefined') {
  fetchCards();
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    daysUntil,
    dueCell,
    renderCardRow,
    renderCards,
  };
}
