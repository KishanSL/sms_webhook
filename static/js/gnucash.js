// Shared gnucash helpers: the leaf account list and a searchable picker.
// Loaded on the Transactions and Settings pages, after common.js. The page sets
// window.GNUCASH_ENABLED before this runs.

globalThis.GNUCASH = globalThis.GNUCASH || { accounts: [], nameById: {}, pathById: {}, idByName: {} };

async function loadGnucashAccounts(refresh = false) {
  if (!globalThis.GNUCASH_ENABLED) return;
  try {
    const res = await fetch('/api/gnucash/accounts' + (refresh ? '?refresh=true' : ''));
    const data = await res.json();
    if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Failed to load gnucash accounts');
    globalThis.GNUCASH.accounts = data.accounts || [];
    globalThis.GNUCASH.nameById = Object.fromEntries(globalThis.GNUCASH.accounts.map(a => [a.id, a.name]));
    globalThis.GNUCASH.pathById = Object.fromEntries(globalThis.GNUCASH.accounts.map(a => [a.id, a.path]));
    // Leaf names are unique, so the name is enough to resolve the account id.
    globalThis.GNUCASH.idByName = Object.fromEntries(globalThis.GNUCASH.accounts.map(a => [a.name, a.id]));
    gnucashRenderDatalist();
  } catch (err) {
    console.error('Failed to load gnucash accounts', err);
  }
}

// One shared <datalist> of leaf names powers every searchable account field on the page.
function gnucashRenderDatalist() {
  if (typeof document === 'undefined') return;
  let dl = document.getElementById('gnucashAccountList');
  if (!dl) {
    dl = document.createElement('datalist');
    dl.id = 'gnucashAccountList';
    document.body.appendChild(dl);
  }
  dl.innerHTML = (globalThis.GNUCASH.accounts || [])
    .map(a => `<option value="${escapeHtml(a.name)}"></option>`)
    .join('');
}

// The account id for a leaf name typed/chosen in a picker ('' when it doesn't match a leaf).
function gnucashResolveId(value) {
  return (globalThis.GNUCASH.idByName && globalThis.GNUCASH.idByName[value]) || '';
}

function gnucashNameFor(id) {
  return (globalThis.GNUCASH.nameById && globalThis.GNUCASH.nameById[id]) || '';
}

function gnucashPathFor(id) {
  return (globalThis.GNUCASH.pathById && globalThis.GNUCASH.pathById[id]) || '';
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    loadGnucashAccounts,
    gnucashRenderDatalist,
    gnucashResolveId,
    gnucashNameFor,
    gnucashPathFor,
  };
}
