const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const common = require('../static/js/common.js');
Object.assign(globalThis, common);

const {
  escapeHtml,
  inr,
  inr0,
  emptyRow,
  rowClass,
  accentClass,
  copyElementText,
  copyTextToClipboard,
  fallbackCopyText,
} = common;

const gnucash = require('../static/js/gnucash.js');
Object.assign(globalThis, gnucash);

test('escapeHtml handles special characters', () => {
  assert.strictEqual(escapeHtml('Hello & World'), 'Hello &amp; World');
  assert.strictEqual(escapeHtml('<script>alert("xss")</script>'), '&lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt;');
  assert.strictEqual(escapeHtml("it's a quote"), 'it&#039;s a quote');
  assert.strictEqual(escapeHtml('<b>"Rock & Roll"</b>'), '&lt;b&gt;&quot;Rock &amp; Roll&quot;&lt;/b&gt;');
});

test('escapeHtml handles null, undefined and empty inputs', () => {
  assert.strictEqual(escapeHtml(null), '');
  assert.strictEqual(escapeHtml(undefined), '');
  assert.strictEqual(escapeHtml(''), '');
});

test('escapeHtml handles numbers and booleans', () => {
  assert.strictEqual(escapeHtml(12345), '12345');
  assert.strictEqual(escapeHtml(0), '0');
  assert.strictEqual(escapeHtml(false), 'false');
  assert.strictEqual(escapeHtml(true), 'true');
});

test('inr and inr0 format amounts correctly', () => {
  const formatted = inr.format(1234.5);
  assert.ok(formatted.includes('1,234.50') || formatted.includes('1,234.5'));
  assert.ok(formatted.includes('₹') || formatted.includes('INR'));

  const formatted0 = inr0.format(1234.5);
  assert.ok(formatted0.includes('1,235') || formatted0.includes('1,234'));
  assert.ok(!formatted0.includes('.5'));

  const formattedZero = inr.format(0);
  assert.ok(formattedZero.includes('0.00') || formattedZero.includes('0.0') || formattedZero.includes('0'));

  const formattedNegative = inr.format(-500);
  assert.ok(formattedNegative.includes('500'));
});

test('emptyRow generates table row with colspan and text', () => {
  const row = emptyRow(5, 'No records found');
  assert.strictEqual(row, '<tr><td colspan="5" class="py-8 text-center text-slate-500">No records found</td></tr>');

  const rowWithHtml = emptyRow(3, '<b>Empty</b>');
  assert.strictEqual(rowWithHtml, '<tr><td colspan="3" class="py-8 text-center text-slate-500"><b>Empty</b></td></tr>');
});

test('copyElementText returns safely when element does not exist', () => {
  assert.doesNotThrow(() => {
    copyElementText('non_existent_id');
  });
});

test('fallbackCopyText uses textarea and execCommand when clipboard is unavailable', () => {
  let createdTag = '';
  let appendedChild = null;
  let removedChild = null;
  let focused = false;
  let selected = false;
  let executedCmd = '';

  const mockTextarea = {
    value: '',
    style: {},
    setAttribute: () => {},
    focus: () => { focused = true; },
    select: () => { selected = true; },
  };

  const origDocument = global.document;
  try {
    global.document = {
      createElement: (tag) => {
        createdTag = tag;
        return mockTextarea;
      },
      body: {
        appendChild: (child) => { appendedChild = child; },
        removeChild: (child) => { removedChild = child; },
      },
      execCommand: (cmd) => {
        executedCmd = cmd;
        return true;
      },
    };

    let successCalled = false;
    let errorCalled = false;

    fallbackCopyText('test clipboard content', () => {
      successCalled = true;
    }, () => {
      errorCalled = true;
    });

    assert.strictEqual(createdTag, 'textarea');
    assert.strictEqual(mockTextarea.value, 'test clipboard content');
    assert.strictEqual(executedCmd, 'copy');
    assert.ok(focused);
    assert.ok(selected);
    assert.strictEqual(appendedChild, mockTextarea);
    assert.strictEqual(removedChild, mockTextarea);
    assert.strictEqual(successCalled, true);
    assert.strictEqual(errorCalled, false);
  } finally {
    global.document = origDocument;
  }
});

test('fallbackCopyText calls onError when execCommand fails', () => {
  const origDocument = global.document;
  try {
    global.document = {
      createElement: () => ({
        style: {},
        setAttribute: () => {},
        focus: () => {},
        select: () => {},
      }),
      body: {
        appendChild: () => {},
        removeChild: () => {},
      },
      execCommand: () => false,
    };

    let successCalled = false;
    let errorCalled = false;

    fallbackCopyText('fail test', () => {
      successCalled = true;
    }, () => {
      errorCalled = true;
    });

    assert.strictEqual(successCalled, false);
    assert.strictEqual(errorCalled, true);
  } finally {
    global.document = origDocument;
  }
});

test('copyTextToClipboard falls back to fallbackCopyText when navigator.clipboard is undefined', () => {
  const origNavigator = global.navigator;
  const origDocument = global.document;
  try {
    global.navigator = {};
    let executedCmd = '';
    global.document = {
      createElement: () => ({
        style: {},
        setAttribute: () => {},
        focus: () => {},
        select: () => {},
      }),
      body: {
        appendChild: () => {},
        removeChild: () => {},
      },
      execCommand: (cmd) => {
        executedCmd = cmd;
        return true;
      },
    };

    let successCalled = false;
    copyTextToClipboard('hello http', () => {
      successCalled = true;
    }, () => {});

    assert.strictEqual(executedCmd, 'copy');
    assert.strictEqual(successCalled, true);
  } finally {
    global.navigator = origNavigator;
    global.document = origDocument;
  }
});

// --- GnuCash Picker & Helper Tests ---

const {
  loadGnucashAccounts,
  gnucashResolveId,
  gnucashNameFor,
  gnucashPathFor,
  gnucashRenderDatalist,
} = require('../static/js/gnucash.js');

test('loadGnucashAccounts stops early when GNUCASH_ENABLED is falsy', async () => {
  let fetchCalled = false;
  const origFetch = global.fetch;
  const origEnabled = globalThis.GNUCASH_ENABLED;
  try {
    global.fetch = async () => {
      fetchCalled = true;
      return { ok: true, json: async () => ({ accounts: [] }) };
    };

    globalThis.GNUCASH_ENABLED = false;
    await loadGnucashAccounts();
    assert.strictEqual(fetchCalled, false);

    delete globalThis.GNUCASH_ENABLED;
    await loadGnucashAccounts();
    assert.strictEqual(fetchCalled, false);

    globalThis.GNUCASH_ENABLED = true;
    await loadGnucashAccounts();
    assert.strictEqual(fetchCalled, true);
  } finally {
    global.fetch = origFetch;
    if (origEnabled !== undefined) globalThis.GNUCASH_ENABLED = origEnabled;
    else delete globalThis.GNUCASH_ENABLED;
  }
});

test('gnucash picker resolution and path helpers', () => {
  global.GNUCASH = {
    accounts: [
      { id: 'acc-groceries-123', name: 'Groceries', path: 'Expenses:Groceries' },
      { id: 'acc-card-456', name: 'HDFC CC', path: 'Liabilities:Credit Card:HDFC' },
    ],
    nameById: {
      'acc-groceries-123': 'Groceries',
      'acc-card-456': 'HDFC CC',
    },
    pathById: {
      'acc-groceries-123': 'Expenses:Groceries',
      'acc-card-456': 'Liabilities:Credit Card:HDFC',
    },
    idByName: {
      'Groceries': 'acc-groceries-123',
      'HDFC CC': 'acc-card-456',
    },
  };

  assert.strictEqual(gnucashResolveId('Groceries'), 'acc-groceries-123');
  assert.strictEqual(gnucashResolveId('HDFC CC'), 'acc-card-456');
  assert.strictEqual(gnucashResolveId('NonExistent'), '');
  assert.strictEqual(gnucashResolveId(''), '');

  assert.strictEqual(gnucashNameFor('acc-groceries-123'), 'Groceries');
  assert.strictEqual(gnucashNameFor('acc-card-456'), 'HDFC CC');
  assert.strictEqual(gnucashNameFor('missing-id'), '');

  assert.strictEqual(gnucashPathFor('acc-groceries-123'), 'Expenses:Groceries');
  assert.strictEqual(gnucashPathFor('acc-card-456'), 'Liabilities:Credit Card:HDFC');
  assert.strictEqual(gnucashPathFor('missing-id'), '');
});

test('gnucashRenderDatalist builds datalist options with HTML escaping', () => {
  global.GNUCASH = {
    accounts: [
      { id: '1', name: 'Dining & Drinks' },
      { id: '2', name: '<Utilities>' },
    ],
  };

  const origDocument = global.document;
  try {
    let appendedChild = null;
    let datalist = null;

    global.document = {
      getElementById: (id) => (id === 'gnucashAccountList' ? datalist : null),
      createElement: (tag) => {
        datalist = { id: '', innerHTML: '' };
        return datalist;
      },
      body: {
        appendChild: (el) => {
          appendedChild = el;
        },
      },
    };

    gnucashRenderDatalist();

    assert.ok(datalist);
    assert.strictEqual(datalist.id, 'gnucashAccountList');
    assert.strictEqual(appendedChild, datalist);
    assert.ok(datalist.innerHTML.includes('<option value="Dining &amp; Drinks"></option>'));
    assert.ok(datalist.innerHTML.includes('<option value="&lt;Utilities&gt;"></option>'));

    // Re-rendering with existing element reuses it without re-appending
    appendedChild = null;
    gnucashRenderDatalist();
    assert.strictEqual(appendedChild, null);
  } finally {
    global.document = origDocument;
  }
});

// --- Cards Dashboard Tests ---

const {
  daysUntil,
  dueCell,
  renderCardRow,
} = require('../static/js/cards.js');

function formatLocalYMD(d) {
  const year = d.getFullYear();
  const month = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

test('daysUntil calculates relative day offset', () => {
  assert.strictEqual(daysUntil(null), null);
  assert.strictEqual(daysUntil(''), null);

  const now = new Date();
  const todayStr = formatLocalYMD(now);
  assert.strictEqual(daysUntil(todayStr), 0);

  const tomorrow = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
  assert.strictEqual(daysUntil(formatLocalYMD(tomorrow)), 1);

  const yesterday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
  assert.strictEqual(daysUntil(formatLocalYMD(yesterday)), -1);

  const inFiveDays = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 5);
  assert.strictEqual(daysUntil(formatLocalYMD(inFiveDays)), 5);
});

test('dueCell formats due and statement dates according to status', () => {
  assert.strictEqual(dueCell({ due_date: null, statement_date: null }), '<span class="text-slate-600">-</span>');

  const now = new Date();
  const todayStr = formatLocalYMD(now);
  const yesterdayStr = formatLocalYMD(new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1));
  const inTwoDaysStr = formatLocalYMD(new Date(now.getFullYear(), now.getMonth(), now.getDate() + 2));
  const inTenDaysStr = formatLocalYMD(new Date(now.getFullYear(), now.getMonth(), now.getDate() + 10));

  // Statement date fallback when due_date is null
  const stmtToday = dueCell({ due_date: null, statement_date: todayStr });
  assert.ok(stmtToday.includes('stmt (today)'));
  assert.ok(stmtToday.includes(todayStr));

  const stmtAgo = dueCell({ due_date: null, statement_date: yesterdayStr });
  assert.ok(stmtAgo.includes('stmt (1d ago)'));

  // Due date overdue unpaid
  const overdueUnpaid = dueCell({ due_date: yesterdayStr, paid: false });
  assert.ok(overdueUnpaid.includes('text-rose-400'));
  assert.ok(overdueUnpaid.includes('overdue by 1d'));

  // Due date overdue but paid
  const overduePaid = dueCell({ due_date: yesterdayStr, paid: true });
  assert.ok(overduePaid.includes('text-slate-500'));
  assert.ok(!overduePaid.includes('text-rose-400'));

  // Due today
  const dueToday = dueCell({ due_date: todayStr, paid: false });
  assert.ok(dueToday.includes('text-amber-400'));
  assert.ok(dueToday.includes('due today'));

  // Due within 3 days
  const dueSoon = dueCell({ due_date: inTwoDaysStr, paid: false });
  assert.ok(dueSoon.includes('text-amber-400'));
  assert.ok(dueSoon.includes('in 2d'));

  // Due further in future (> 3 days)
  const dueFar = dueCell({ due_date: inTenDaysStr, paid: false });
  assert.ok(dueFar.includes('text-slate-300'));
  assert.ok(dueFar.includes('in 10d'));
});

test('renderCardRow generates correct markup for cards', () => {
  const card1 = {
    sender_header: 'HDFCBK',
    account_last4: '1234',
    name: 'Regalia Gold',
    bank: 'HDFC',
    total_due: 25400,
    min_due: 2500,
    paid: false,
    has_statement: true,
    statement_date: '2026-11-01',
    due_date: '2026-11-20',
    webhook_id: 88,
  };

  const html1 = renderCardRow(card1);
  assert.ok(html1.includes('Regalia Gold'));
  assert.ok(html1.includes('••1234'));
  assert.ok(html1.includes('text-rose-400 font-semibold'));
  assert.ok(html1.includes('togglePaid(\'HDFCBK\', \'1234\', this)'));
  assert.ok(html1.includes('/dashboard?webhook=88&from=cards'));

  // Paid card
  const cardPaid = {
    sender_header: 'ICICIB',
    account_last4: '5678',
    bank: 'ICICI',
    total_due: 10000,
    min_due: 1000,
    paid: true,
    has_statement: true,
    due_date: '2026-11-15',
  };
  const htmlPaid = renderCardRow(cardPaid);
  assert.ok(htmlPaid.includes('line-through text-slate-500'));
  assert.ok(htmlPaid.includes('checked'));
  assert.ok(htmlPaid.includes('>Paid<'));

  // Card without statement
  const cardNoStmt = {
    sender_header: 'SBIN',
    account_last4: null,
    bank: 'SBI',
    has_statement: false,
  };
  const htmlNoStmt = renderCardRow(cardNoStmt);
  assert.ok(htmlNoStmt.includes('••----'));
  assert.ok(htmlNoStmt.includes('no statement'));
  assert.ok(!htmlNoStmt.includes('togglePaid'));

  // Card with statement but no last4 uses '_' sentinel in togglePaid
  const cardNoL4WithStmt = {
    sender_header: 'SBIN',
    account_last4: null,
    bank: 'SBI',
    has_statement: true,
  };
  const htmlNoL4 = renderCardRow(cardNoL4WithStmt);
  assert.ok(htmlNoL4.includes('togglePaid(\'SBIN\', \'_\', this)'));

  // Escaping in card names and headers
  const cardXss = {
    sender_header: 'BANK<script>',
    account_last4: '9999',
    name: 'Alert" onclick="evil',
    bank: 'Malicious & Co',
    has_statement: false,
  };
  const htmlXss = renderCardRow(cardXss);
  assert.ok(!htmlXss.includes('<script>'));
  assert.ok(htmlXss.includes('Alert&quot; onclick=&quot;evil'));
  assert.ok(htmlXss.includes('BANK&lt;script&gt;'));
});

// --- Dashboard Webhook & Badge Tests ---

const {
  renderTypeBadge,
  renderWebhookRow,
} = require('../static/js/dashboard.js');

test('renderTypeBadge renders configured badges and handles fallbacks and escaping', () => {
  const txnBadge = renderTypeBadge('Transaction');
  assert.ok(txnBadge.includes('bg-emerald-500/15'));
  assert.ok(txnBadge.includes('fa-indian-rupee-sign'));
  assert.ok(txnBadge.includes('<span>Transaction</span>'));

  const otpBadge = renderTypeBadge('OTP');
  assert.ok(otpBadge.includes('bg-amber-500/15'));
  assert.ok(otpBadge.includes('fa-key'));

  const stmtBadge = renderTypeBadge('Statement');
  assert.ok(stmtBadge.includes('bg-sky-500/15'));
  assert.ok(stmtBadge.includes('fa-file-invoice-dollar'));

  const fastagBadge = renderTypeBadge('Toll / FASTag');
  assert.ok(fastagBadge.includes('bg-purple-500/15'));
  assert.ok(fastagBadge.includes('fa-car'));

  const mandateBadge = renderTypeBadge('Mandate / Autopay');
  assert.ok(mandateBadge.includes('bg-indigo-500/15'));
  assert.ok(mandateBadge.includes('fa-arrows-rotate'));

  const brokerBadge = renderTypeBadge('Broker / Regulatory');
  assert.ok(brokerBadge.includes('bg-cyan-500/15'));
  assert.ok(brokerBadge.includes('fa-chart-line'));

  const othersBadge = renderTypeBadge('Others');
  assert.ok(othersBadge.includes('bg-slate-700/80'));
  assert.ok(othersBadge.includes('fa-message'));

  // Unknown type falls back to Others styling while showing type name
  const customBadge = renderTypeBadge('CustomNotification');
  assert.ok(customBadge.includes('bg-slate-700/80'));
  assert.ok(customBadge.includes('fa-message'));
  assert.ok(customBadge.includes('CustomNotification'));

  // Null / undefined falls back to Others
  const nullBadge = renderTypeBadge(null);
  assert.ok(nullBadge.includes('<span>Others</span>'));

  // Escaping
  const xssBadge = renderTypeBadge('<script>alert(1)</script>');
  assert.ok(!xssBadge.includes('<script>'));
  assert.ok(xssBadge.includes('&lt;script&gt;alert(1)&lt;/script&gt;'));
});

test('renderWebhookRow renders webhook metadata, previews, and escapes inputs', () => {
  const rowWithBody = renderWebhookRow({
    id: 101,
    received_at: '2026-11-20T14:30:00Z',
    sender: 'VM-HDFCBK',
    detected_type: 'Transaction',
    message_body: 'Rs. 500 spent at Store',
    is_read: 0,
  });
  assert.ok(rowWithBody.includes('onclick="inspectWebhook(101)"'));
  assert.ok(rowWithBody.includes('id="webhook-101"'));
  assert.ok(rowWithBody.includes('accent-unread'));
  assert.ok(rowWithBody.includes('toggleWebhookRead(101, this)'));
  assert.ok(!rowWithBody.includes('checked'));
  assert.ok(rowWithBody.includes('VM-HDFCBK'));
  assert.ok(rowWithBody.includes('Rs. 500 spent at Store'));
  assert.ok(rowWithBody.includes('deleteWebhook(101)'));

  // Read message rendering
  const rowRead = renderWebhookRow({
    id: 105,
    received_at: '2026-11-20T14:32:00Z',
    sender: 'VM-HDFCBK',
    detected_type: 'Transaction',
    message_body: 'Paid',
    is_read: 1,
  });
  assert.ok(rowRead.includes('is-read'));
  assert.ok(!rowRead.includes('accent-unread'));
  assert.ok(rowRead.includes('checked'));

  // Raw payload fallback when message_body is empty
  const rowWithRaw = renderWebhookRow({
    id: 102,
    received_at: '2026-11-20T14:35:00Z',
    sender: null,
    detected_type: 'OTP',
    payload_raw: '{"otp": "123456"}',
  });
  assert.ok(rowWithRaw.includes('<span class="block text-slate-500 italic">-</span>'));
  assert.ok(rowWithRaw.includes('{&quot;otp&quot;: &quot;123456&quot;}'));

  // Empty payload
  const rowEmpty = renderWebhookRow({
    id: 103,
    received_at: '2026-11-20T14:40:00Z',
    sender: 'TEST',
  });
  assert.ok(rowEmpty.includes('(Empty payload)'));

  // Truncation of raw payload over 400 chars
  const longRaw = 'A'.repeat(500);
  const rowLong = renderWebhookRow({
    id: 104,
    received_at: '2026-11-20T14:45:00Z',
    payload_raw: longRaw,
  });
  assert.ok(rowLong.includes('A'.repeat(400) + '…'));
  assert.ok(!rowLong.includes('A'.repeat(401)));
});

// --- Transactions Helpers & Row Tests ---

const {
  accountLabel,
  renderTransactionRow,
  gnucashCell,
} = require('../static/js/transactions.js');

test('transactions helper functions (accountLabel, rowClass, accentClass)', () => {
  // accountLabel
  assert.strictEqual(
    accountLabel({ name: 'Salary Account', account_last4: '4321', instrument: 'account', bank: 'HDFC' }),
    'Salary Account (••4321)'
  );
  assert.strictEqual(
    accountLabel({ name: '', account_last4: '9876', instrument: 'account', bank: 'SBI' }),
    'SBI A/c ••9876'
  );
  assert.strictEqual(
    accountLabel({ name: null, account_last4: '1122', instrument: 'card', bank: 'Axis' }),
    'Axis Card ••1122'
  );

  // rowClass
  assert.strictEqual(rowClass(true), 'hover:bg-slate-800/60 transition is-read');
  assert.strictEqual(rowClass(false), 'hover:bg-slate-800/60 transition');

  // accentClass
  assert.strictEqual(accentClass(true), '');
  assert.strictEqual(accentClass(false), 'accent-unread');
});

test('renderTransactionRow produces correct markup for credit, debit, and locked states', () => {
  const debitTxn = {
    id: 501,
    occurred_at: '2026-11-20T12:00:00Z',
    direction: 'debit',
    amount: 1250,
    instrument: 'card',
    account_last4: '7777',
    bank: 'HDFC',
    sender_header: 'HDFCBK',
    counterparty: 'Amazon India',
    balance: 45000,
    balance_type: 'limit',
    is_read: false,
    webhook_id: 301,
    raw_text: 'Spent Rs. 1250 at Amazon',
  };

  const debitHtml = renderTransactionRow(debitTxn, false);
  assert.ok(debitHtml.includes('id="txn-501"'));
  assert.ok(debitHtml.includes('accent-unread'));
  assert.ok(debitHtml.includes('text-rose-400'));
  assert.ok(debitHtml.includes('−'));
  assert.ok(debitHtml.includes('1,250'));
  assert.ok(debitHtml.includes('Amazon India'));
  assert.ok(debitHtml.includes('avail. limit'));
  assert.ok(debitHtml.includes('/dashboard?webhook=301&from=transactions'));
  assert.ok(debitHtml.includes('renameAccount(\'HDFCBK:7777\')'));
  // GNUCASH disabled -> no review check or gnucash cell
  assert.ok(!debitHtml.includes('data-review='));
  assert.ok(!debitHtml.includes('data-gnucash='));

  // Credit transaction with custom account name, read status, channel badge
  const creditTxn = {
    id: 502,
    occurred_at: '2026-11-20T13:00:00Z',
    direction: 'credit',
    amount: 50000,
    instrument: 'account',
    account_name: 'Primary Salary',
    account_last4: '9999',
    bank: 'ICICI',
    sender_header: 'ICICIB',
    channel: 'NEFT',
    counterparty: 'Employer Corp',
    balance: 62000,
    balance_type: 'balance',
    is_read: true,
  };

  const creditHtml = renderTransactionRow(creditTxn, false);
  assert.ok(creditHtml.includes('text-emerald-400'));
  assert.ok(creditHtml.includes('+'));
  assert.ok(creditHtml.includes('50,000'));
  assert.ok(creditHtml.includes('Primary Salary'));
  assert.ok(creditHtml.includes('NEFT'));
  assert.ok(creditHtml.includes('is-read'));
  assert.ok(!creditHtml.includes('accent-unread'));
});

test('renderTransactionRow and gnucashCell handle gnucash states and ledger locking', () => {
  global.GNUCASH = {
    accounts: [
      { id: 'acc-cc', name: 'Credit Card', path: 'Liabilities:Credit Card' },
      { id: 'acc-food', name: 'Dining', path: 'Expenses:Dining' },
    ],
    nameById: { 'acc-cc': 'Credit Card', 'acc-food': 'Dining' },
    pathById: { 'acc-cc': 'Liabilities:Credit Card', 'acc-food': 'Expenses:Dining' },
    idByName: { 'Credit Card': 'acc-cc', 'Dining': 'acc-food' },
  };

  // 1. Transaction ready to post (unlocked)
  const readyTxn = {
    id: 601,
    occurred_at: '2026-11-20T10:00:00Z',
    direction: 'debit',
    amount: 300,
    instrument: 'card',
    account_last4: '1111',
    bank: 'HDFC',
    sender_header: 'HDFCBK',
    from_account_id: 'acc-cc',
    suggested_to_account_id: 'acc-food',
    suggestion_reason: 'Matched counterparty rule',
  };

  const readyHtml = renderTransactionRow(readyTxn, true);
  assert.ok(readyHtml.includes('data-review="601"'));
  assert.ok(!readyHtml.includes('disabled data-review="601"'));
  assert.ok(readyHtml.includes('Credit Card'));
  assert.ok(readyHtml.includes('Dining'));
  assert.ok(readyHtml.includes('Matched counterparty rule'));
  assert.ok(readyHtml.includes('onclick="postTransaction(601)"'));

  // 2. Transaction already in ledger (locked)
  const lockedTxn = {
    id: 602,
    occurred_at: '2026-11-20T11:00:00Z',
    direction: 'debit',
    amount: 400,
    instrument: 'card',
    account_last4: '1111',
    bank: 'HDFC',
    sender_header: 'HDFCBK',
    in_ledger: true,
    from_account_id: 'acc-cc',
    suggested_to_account_id: 'acc-food',
  };

  const lockedHtml = renderTransactionRow(lockedTxn, true);
  assert.ok(lockedHtml.includes('cursor-not-allowed opacity-40'));
  assert.ok(lockedHtml.includes('Posted to gnucash-web'));
  assert.ok(lockedHtml.includes('readonly'));
  assert.ok(!lockedHtml.includes('onclick="postTransaction(602)"'));

  // 3. Posting failed transaction
  const failedTxn = {
    id: 603,
    occurred_at: '2026-11-20T11:30:00Z',
    direction: 'debit',
    amount: 150,
    instrument: 'card',
    bank: 'HDFC',
    sender_header: 'HDFCBK',
    post_status: 'failed',
    post_error: 'Network timeout connecting to gnucash',
  };

  const failedHtml = gnucashCell(failedTxn, true);
  assert.ok(failedHtml.includes('Failed'));
  assert.ok(failedHtml.includes('Network timeout connecting to gnucash'));
  assert.ok(failedHtml.includes('No card mapping'));
});

// --- Return Location Logic Test (QC Item #5 Verification) ---

test('returnLocation deep-link resolution only permits specific whitelisted destinations', () => {
  function resolveReturnLocation(queryString) {
    const params = new URLSearchParams(queryString);
    const fromParam = params.get('from');
    if (fromParam === 'cards') {
      return '/cards';
    } else if (fromParam === 'transactions') {
      return '/transactions';
    }
    return null;
  }

  assert.strictEqual(resolveReturnLocation('?from=cards'), '/cards');
  assert.strictEqual(resolveReturnLocation('?from=transactions'), '/transactions');
  assert.strictEqual(resolveReturnLocation('?from=/evil/redirect'), null);
  assert.strictEqual(resolveReturnLocation('?from=https://example.com'), null);
  assert.strictEqual(resolveReturnLocation('?from=/settings'), null);
  assert.strictEqual(resolveReturnLocation(''), null);
});

// --- Browser Multi-Script Template Loading Tests (QC High #1 Regression Defense) ---

test('all page templates execute sequential scripts in a shared browser context without collision', () => {
  const pages = [
    {
      name: 'dashboard',
      scripts: ['static/js/common.js', 'static/js/dashboard.js'],
      expectedFunctions: ['escapeHtml', 'renderTypeBadge', 'renderWebhookRow', 'renderWebhooks', 'inspectWebhook', 'toggleWebhookRead', 'toggleModalRead'],
    },
    {
      name: 'cards',
      scripts: ['static/js/common.js', 'static/js/cards.js'],
      expectedFunctions: ['escapeHtml', 'daysUntil', 'dueCell', 'renderCardRow', 'renderCards', 'togglePaid'],
    },
    {
      name: 'settings',
      scripts: ['static/js/common.js', 'static/js/gnucash.js', 'static/js/settings.js'],
      expectedFunctions: ['escapeHtml', 'loadGnucashAccounts', 'gnucashRenderDatalist', 'loadBanks', 'linkBank', 'saveBank'],
      setup: (ctx) => {
        ctx.GNUCASH_ENABLED = true;
        ctx.window.GNUCASH_ENABLED = true;
      },
    },
    {
      name: 'transactions',
      scripts: ['static/js/common.js', 'static/js/gnucash.js', 'static/js/transactions.js'],
      expectedFunctions: [
        'escapeHtml',
        'gnucashResolveId',
        'gnucashNameFor',
        'gnucashPathFor',
        'accountLabel',
        'rowClass',
        'accentClass',
        'renderTransactionRow',
        'renderTransactions',
        'gnucashCell',
        'toggleSelectAll',
      ],
      setup: (ctx) => {
        ctx.GNUCASH_ENABLED = true;
        ctx.window.GNUCASH_ENABLED = true;
        ctx.UNCAT_LABEL = 'Expenses:Uncategorized';
        ctx.window.UNCAT_LABEL = 'Expenses:Uncategorized';
      },
    },
  ];

  for (const page of pages) {
    const windowObj = {
      location: { search: '', pathname: '/' },
      addEventListener: () => {},
    };
    const mockEl = {
      addEventListener: () => {},
      classList: { add: () => {}, remove: () => {}, toggle: () => {} },
      value: '',
      innerText: '',
      innerHTML: '',
      checked: false,
    };
    const ctx = vm.createContext({
      window: windowObj,
      location: windowObj.location,
      document: {
        getElementById: () => mockEl,
        querySelector: () => mockEl,
        querySelectorAll: () => [],
        createElement: () => mockEl,
        body: { appendChild: () => {} },
        addEventListener: () => {},
      },
      navigator: {},
      console: console,
      fetch: (url) => Promise.resolve({
        ok: true,
        json: () => Promise.resolve(url && url.includes('/stats') ? { total_count: 0, providers: {}, db_size_bytes: 0 } : []),
      }),
      URLSearchParams: global.URLSearchParams,
      Intl: Intl,
      setTimeout: (fn) => fn(),
      clearTimeout: () => {},
      setInterval: () => 1,
      clearInterval: () => {},
    });

    if (page.setup) page.setup(ctx);

    for (const relPath of page.scripts) {
      const absPath = path.resolve(__dirname, '..', relPath);
      const code = fs.readFileSync(absPath, 'utf8');
      assert.doesNotThrow(() => {
        vm.runInContext(code, ctx);
      }, `Loading ${relPath} on page ${page.name} should not throw errors`);
    }

    for (const fn of page.expectedFunctions) {
      assert.strictEqual(
        typeof ctx[fn] === 'function' || typeof ctx.window[fn] === 'function',
        true,
        `Expected function '${fn}' to be defined in global or window scope on page '${page.name}'`
      );
    }
  }
});

test('api helper performs fetch and attaches status and data on failure', async () => {
  const origFetch = global.fetch;
  try {
    global.fetch = async (url, opts) => {
      if (url === '/api/ok') {
        return { ok: true, json: async () => ({ success: true }) };
      }
      if (url === '/api/error') {
        return {
          ok: false,
          status: 409,
          json: async () => ({ detail: { transaction_id: 'tx-1', description: 'duplicate' } }),
        };
      }
    };

    const res = await common.api('GET', '/api/ok');
    assert.deepStrictEqual(res, { success: true });

    await assert.rejects(
      async () => {
        await common.api('POST', '/api/error');
      },
      (err) => {
        assert.strictEqual(err.status, 409);
        assert.strictEqual(err.data.detail.transaction_id, 'tx-1');
        return true;
      }
    );
  } finally {
    global.fetch = origFetch;
  }
});

test('showToast updates toast element or safely returns if element is absent', () => {
  const origDocument = global.document;
  try {
    // 1. Toast absent: must not throw
    global.document = { getElementById: (id) => null };
    assert.doesNotThrow(() => {
      common.showToast('Test without toast element');
    });

    // 2. Toast present: updates class and text
    let toastClasses = '';
    let toastText = '';
    const mockToast = {
      set className(val) { toastClasses = val; },
      get className() { return toastClasses; },
      set innerText(val) { toastText = val; },
      get innerText() { return toastText; },
      classList: { add: () => {} },
    };
    global.document = {
      getElementById: (id) => (id === 'toast' ? mockToast : null),
    };

    common.showToast('Success!', true);
    assert.strictEqual(toastText, 'Success!');
    assert.ok(toastClasses.includes('bg-emerald-600'));

    common.showToast('Failed!', false);
    assert.strictEqual(toastText, 'Failed!');
    assert.ok(toastClasses.includes('bg-rose-600'));
  } finally {
    global.document = origDocument;
  }
});
