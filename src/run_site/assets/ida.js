const $ = (selector) => document.querySelector(selector);
const app = $('#ida-app');
const indexURL = new URL(app.dataset.index, location.href);
const cache = new Map();
let db, names, types, functionRanges;
const DEFAULT_VIEW = 'pseudocode';
let view = DEFAULT_VIEW, selected = null, selectedType = null, sidebar = 'functions';
let browsingView = DEFAULT_VIEW;
let listLimit = 200, revision = 0;
let displayedHash = null;
const scrollPositions = new Map();

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}

function address(value) {
  try {
    if (typeof value !== 'string' || !/^(?:0x)?[\da-f]+$/i.test(value)) return null;
    return BigInt(value.startsWith('0x') ? value : '0x' + value);
  } catch { return null; }
}

function canonical(value) {
  const parsed = address(value);
  return parsed === null ? null : '0x' + parsed.toString(16);
}

function initialAddress() {
  return db.entry_points?.[0]?.address || db.functions[0]?.address || db.chunks[0]?.start || '0x0';
}

function addressHash(ea, mode = browsingView) {
  return '#addr=' + encodeURIComponent(ea) + '&view=' + mode
    + (mode === 'xrefs' ? '&return=' + browsingView : '');
}

function typeHash(id) {
  return '#type=' + encodeURIComponent(id) + '&addr=' + encodeURIComponent(selected || '0x0')
    + '&view=' + browsingView;
}

function link(label, ea, mode = browsingView) {
  const anchor = node('a', label);
  anchor.href = addressHash(ea, mode);
  return anchor;
}

async function data(file) {
  if (cache.has(file)) return cache.get(file);
  const response = await fetch(new URL(file, indexURL));
  if (!response.ok) throw new Error(`Unable to load ${file} (HTTP ${response.status}).`);
  const value = await response.json();
  cache.set(file, value);
  if (cache.size > 12) cache.delete(cache.keys().next().value);
  return value;
}

// Upper bound over sorted address ranges. BigInt keeps 64-bit addresses exact.
function rangeIndex(ranges, ea) {
  let low = 0, high = ranges.length;
  while (low < high) {
    const middle = (low + high) >>> 1;
    if (address(ranges[middle].start) <= ea) low = middle + 1;
    else high = middle;
  }
  return low - 1;
}

function functionAt(ea) {
  // A tail may be shared by multiple functions; prefer its first containing owner.
  const position = rangeIndex(functionRanges, ea);
  for (let i = position; i >= 0; i--) {
    const range = functionRanges[i];
    if (ea < address(range.end)) return range.function;
  }
  return null;
}

function setStatus(text) { $('#ida-status').textContent = text; }

function highlightCode(text) {
  const fragment = document.createDocumentFragment();
  // Only create DOM nodes; database strings never become HTML or executable code.
  const tokens = String(text).match(/\/\*[\s\S]*?\*\/|\/\/[^\n]*|;[^\n]*|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\b0x[\da-f]+\b|\b[\da-f]+h\b|[A-Za-z_$?@][\w$?@]*|\b\d+\b|\s+|./gi) || [];
  for (const token of tokens) {
    let element;
    if (/^(\/\/|\/\*|;)/.test(token)) element = node('span', token, 'syntax-comment');
    else if (/^["']/.test(token)) element = node('span', token, 'syntax-string');
    else if (names.has(token)) element = link(token, names.get(token));
    else if (types.has(token)) {
      element = node('a', token, 'syntax-type');
      element.href = typeHash(types.get(token).id);
    } else if (/^(?:0x[\da-f]+|[\da-f]+h)$/i.test(token)) {
      const ea = canonical(token.endsWith('h') ? token.slice(0, -1) : token);
      const position = rangeIndex(db.chunks, address(ea));
      element = position >= 0 && address(ea) < address(db.chunks[position].end)
        ? link(token, ea) : node('span', token, 'syntax-number');
    } else if (/^(if|else|return|while|for|switch|case|break|goto|do|typedef|struct|union|enum|const|void|int|char|unsigned|signed|short|long|float|double|bool|__\w+)$/i.test(token)) {
      element = node('span', token, 'syntax-keyword');
    } else if (/^\d/.test(token)) element = node('span', token, 'syntax-number');
    else element = document.createTextNode(token);
    fragment.append(element);
  }
  return fragment;
}

function renderSidebar() {
  const query = $('#filter').value.toLowerCase().trim();
  const items = (sidebar === 'functions' ? db.functions : db.types).filter(
    (item) => `${item.name} ${item.address || ''}`.toLowerCase().includes(query)
  );
  $('#item-count').textContent = `${items.length.toLocaleString()} ${sidebar}`;
  const fragment = document.createDocumentFragment();
  for (const item of items.slice(0, listLimit)) {
    const anchor = node('a', undefined, 'symbol');
    anchor.href = sidebar === 'functions' ? addressHash(item.address) : typeHash(item.id);
    if (item.address) anchor.dataset.address = item.address;
    else anchor.dataset.type = item.id;
    anchor.title = item.name;
    anchor.append(node('span', item.name));
    if (item.address) anchor.append(node('small', item.address, 'muted'));
    fragment.append(anchor);
  }
  $('#item-list').replaceChildren(fragment);
  $('#more-items').hidden = items.length <= listLimit;
  $('#functions-tab').setAttribute('aria-pressed', sidebar === 'functions');
  $('#types-tab').setAttribute('aria-pressed', sidebar === 'types');
}

function referenceList(title, refs) {
  const block = node('section', undefined, 'references');
  block.append(node('h3', `${title} (${refs.length})`));
  if (!refs.length) block.append(node('p', 'No recorded references.', 'muted'));
  for (const ref of refs) {
    const row = node('div', undefined, 'reference');
    const name = db.nameByAddress.get(ref.address);
    const owner = functionAt(address(ref.address));
    row.append(link(`${ref.address}  ${name || owner?.name || ''}`, ref.address), node('small', ref.kind, 'muted'));
    block.append(row);
  }
  return block;
}

async function listingAt(ea) {
  const position = rangeIndex(db.chunks, ea);
  if (position < 0 || ea >= address(db.chunks[position].end)) return { position: -1, rows: [], row: null };
  const rows = await data(db.chunks[position].file);
  const row = rows.find((item) => address(item.address) <= ea && ea < address(item.end));
  return { position, rows, row };
}

function listingPane(context, ea) {
  const container = node('div', undefined, 'listing-pane');
  const pager = node('nav', undefined, 'listing-pager');
  pager.setAttribute('aria-label', 'Listing pages');
  if (context.position > 0) pager.append(link('← Previous', db.chunks[context.position - 1].start, 'disassembly'));
  pager.append(node('span', `Chunk ${context.position + 1} / ${db.chunks.length}`));
  if (context.position + 1 < db.chunks.length) pager.append(link('Next →', db.chunks[context.position + 1].start, 'disassembly'));
  container.append(pager);
  const listing = node('div', undefined, 'listing');
  for (const row of context.rows) {
    const active = address(row.address) <= ea && ea < address(row.end);
    const line = node('div', undefined, 'assembly-row' + (active ? ' selected' : ''));
    if (active) line.id = 'selected-row';
    const eaLink = link(row.address.slice(2).toUpperCase(), row.address, 'disassembly');
    eaLink.className = 'address';
    eaLink.title = 'Select this address';
    const text = node('code', undefined, 'instruction');
    if (row.name) {
      text.append(node('span', row.name + ':\n', 'assembly-label'));
    }
    text.append(highlightCode(row.text));
    for (const comment of [row.comment, row.repeatable_comment]) {
      if (comment && !row.text.includes(comment)) text.append(node('span', '\n; ' + comment, 'syntax-comment'));
    }
    // Explicit xref links also cover operands whose printed names include offsets or demangling.
    if (row.outgoing.length) {
      const refs = node('span', undefined, 'inline-refs');
      for (const ref of row.outgoing) {
        const target = link('↗ ' + ref.address, ref.address, 'disassembly');
        target.title = ref.kind;
        refs.append(target);
      }
      text.append(refs);
    }
    line.append(eaLink, node('span', row.bytes, 'bytes'), text);
    if (row.incoming.length) {
      const incoming = link(`${row.incoming.length} xrefs`, row.address, 'xrefs');
      incoming.className = 'xref-count';
      line.append(incoming);
    }
    listing.append(line);
  }
  container.append(listing);
  return container;
}

function saveScroll() {
  if (displayedHash === null) return;
  const positions = {};
  for (const selector of ['.listing', '.pseudocode', '#ida-content']) {
    const pane = $(selector);
    if (pane) positions[selector] = [pane.scrollLeft, pane.scrollTop];
  }
  scrollPositions.set(displayedHash, positions);
  if (scrollPositions.size > 100) scrollPositions.delete(scrollPositions.keys().next().value);
  displayedHash = null;
}

function restoreScroll() {
  const positions = scrollPositions.get(location.hash);
  if (positions) {
    for (const [selector, [left, top]] of Object.entries(positions)) {
      const pane = $(selector);
      if (pane) { pane.scrollLeft = left; pane.scrollTop = top; }
    }
  } else {
    const listing = $('.listing'), row = $('#selected-row');
    if (listing && row) {
      // Scroll only the listing, never the document or its surrounding chrome.
      listing.scrollTop = row.getBoundingClientRect().top - listing.getBoundingClientRect().top
        - listing.clientHeight / 2 + row.clientHeight / 2;
    }
  }
  displayedHash = location.hash;
}

function updateNavigation() {
  for (const anchor of document.querySelectorAll('#item-list a, #entry-points a')) {
    anchor.href = anchor.dataset.address ? addressHash(anchor.dataset.address) : typeHash(anchor.dataset.type);
    const active = selectedType !== null ? anchor.dataset.type === selectedType
      : anchor.dataset.address === functionAt(address(selected))?.address;
    if (active) anchor.setAttribute('aria-current', 'true');
    else anchor.removeAttribute('aria-current');
  }
}

async function render() {
  saveScroll();
  const current = ++revision;
  const params = new URLSearchParams(location.hash.slice(1));
  selectedType = params.get('type');
  const requestedView = params.get('view');
  view = ['disassembly', 'pseudocode', 'xrefs'].includes(requestedView) ? requestedView : DEFAULT_VIEW;
  if (view === 'xrefs') browsingView = params.get('return') === 'disassembly' ? 'disassembly' : DEFAULT_VIEW;
  else browsingView = view;
  const rawAddress = params.get('addr') || (location.hash.match(/^#(?:0x)?[\da-f]+$/i) ? location.hash.slice(1) : null);
  selected = canonical(rawAddress || initialAddress());
  updateNavigation();
  $('#ida-content').replaceChildren(node('p', 'Loading…', 'muted'));
  $('#selection').replaceChildren();
  for (const mode of ['disassembly', 'pseudocode', 'xrefs']) {
    $('#' + mode + '-tab').setAttribute('aria-pressed', selectedType === null && view === mode);
  }
  try {
    if (selectedType !== null) {
      const item = db.types.find((type) => type.id === selectedType);
      if (!item) throw new Error('This type is not present in the export.');
      const details = await data(item.file);
      if (current !== revision) return;
      $('#selection').append(node('h2', item.name));
      const pre = node('pre', undefined, 'pseudocode');
      pre.append(highlightCode(details.declaration));
      $('#ida-content').replaceChildren(pre, node('p', details.comment || '', 'syntax-comment'));
      setStatus('Local type declaration · click named types to follow dependencies');
      restoreScroll();
      return;
    }
    if (selected === null) throw new Error('Invalid address. Enter a hexadecimal address or a symbol name.');
    const ea = address(selected);
    const func = functionAt(ea);
    const details = func ? await data(func.file) : null;
    const context = await listingAt(ea);
    if (current !== revision) return;
    $('#selection').append(node('h2', func ? func.name : db.nameByAddress.get(selected) || selected));
    const locationLine = node('p', undefined, 'selection-meta');
    locationLine.append(node('code', selected));
    if (func) locationLine.append(link('Function start ↗', func.address));
    locationLine.append(link('References ↗', selected, 'xrefs'));
    $('#selection').append(locationLine);
    const info = node('details', undefined, 'function-details');
    info.append(node('summary', 'Function info'));
    if (details?.signature) {
      const signature = node('code', undefined, 'signature');
      signature.append(highlightCode(details.signature));
      info.append(signature);
    }
    for (const comment of [details?.comment, details?.repeatable_comment]) {
      if (comment) info.append(node('p', comment, 'syntax-comment'));
    }
    if (info.children.length > 1) $('#selection').append(info);
    const content = $('#ida-content');
    content.replaceChildren();
    if (view === 'pseudocode') {
      if (!details) content.append(node('p', 'No function contains this address.'));
      else if (details.pseudocode === null) content.append(node('p', details.error || 'Decompilation unavailable.', 'warning'));
      else {
        const pre = node('pre', undefined, 'pseudocode');
        // Tokenize the entire function to preserve multiline C comments.
        pre.append(highlightCode(details.pseudocode.join('\n')));
        content.append(pre);
      }
    } else if (view === 'xrefs') {
      if (context.row) {
        content.append(referenceList('References to this item', context.row.incoming), referenceList('References from this item', context.row.outgoing));
      } else content.append(node('p', 'This address has no exported listing item.'));
      if (details && details.address !== context.row?.address) content.append(referenceList('References to function start', details.incoming));
    } else if (context.row) content.append(listingPane(context, ea));
    else content.append(node('p', 'This address is outside the exported defined items. It may be an undefined byte, a gap, or omitted by an export limit.', 'warning'));
    setStatus(`${db.functions.length.toLocaleString()} functions · ${db.head_count.toLocaleString()} defined items · ${db.types.length.toLocaleString()} types`);
    restoreScroll();
  } catch (error) {
    if (current !== revision) return;
    $('#ida-content').replaceChildren(node('p', error.message, 'warning'));
    setStatus('Unable to display selection');
  }
}

async function start() {
  const response = await fetch(indexURL);
  if (!response.ok) throw new Error(`Unable to load database index (HTTP ${response.status}).`);
  db = await response.json();
  if (db.schema !== 1) throw new Error('Unsupported database snapshot version.');
  names = new Map(db.names.map(([ea, name]) => [name, ea]));
  db.nameByAddress = new Map(db.names);
  for (const func of db.functions) names.set(func.name, func.address);
  types = new Map(db.types.map((item) => [item.name, item]));
  const setSymbols = (open) => {
    app.dataset.sidebar = open ? 'open' : 'closed';
    $('#toggle-symbols').setAttribute('aria-expanded', String(open));
  };
  setSymbols(!matchMedia('(max-width: 600px)').matches);
  $('#toggle-symbols').addEventListener('click', () => setSymbols(app.dataset.sidebar !== 'open'));
  $('#item-list').addEventListener('click', (event) => {
    if (event.target.closest('a') && matchMedia('(max-width: 600px)').matches) setSymbols(false);
  });
  $('#navigate-back').addEventListener('click', () => history.back());
  $('#navigate-forward').addEventListener('click', () => history.forward());
  functionRanges = db.functions.flatMap((func) => func.ranges.map(([start, end]) => ({ start, end, function: func })));
  functionRanges.sort((a, b) => address(a.start) < address(b.start) ? -1 : address(a.start) > address(b.start) ? 1 : 0);
  for (const warning of db.warnings) $('#ida-warning').append(node('p', warning, 'warning'));
  for (const entry of db.entry_points || []) {
    const anchor = link(`${entry.name}  ${entry.address}`, entry.address);
    anchor.dataset.address = entry.address;
    $('#entry-points').append(anchor);
  }
  for (const segment of db.segments) $('#segments').append(link(`${segment.name}  ${segment.start}`, segment.start, 'disassembly'));
  for (const mode of ['functions', 'types']) $('#' + mode + '-tab').addEventListener('click', () => {
    sidebar = mode; listLimit = 200; renderSidebar(); updateNavigation();
  });
  for (const mode of ['disassembly', 'pseudocode', 'xrefs']) $('#' + mode + '-tab').addEventListener('click', () => {
    location.hash = addressHash(selected || initialAddress(), mode);
  });
  $('#filter').addEventListener('input', () => { listLimit = 200; renderSidebar(); });
  $('#more-items').addEventListener('click', () => { listLimit += 200; renderSidebar(); });
  $('#jump-form').addEventListener('submit', (event) => {
    event.preventDefault();
    const value = $('#jump').value.trim();
    if (types.has(value)) location.hash = typeHash(types.get(value).id);
    else {
      const ea = names.get(value) || canonical(value);
      if (!ea) { setStatus('Unknown symbol or invalid hexadecimal address.'); return; }
      location.hash = addressHash(ea);
    }
  });
  document.addEventListener('keydown', (event) => {
    if (event.target.closest('input, textarea') || event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.key === 'g') { event.preventDefault(); $('#jump').focus(); }
    if (event.key === 'Escape') { event.preventDefault(); history.back(); }
    if (event.key === 'x' || event.key === 'F5') {
      event.preventDefault();
      location.hash = addressHash(selected, event.key === 'x' ? 'xrefs' : view === 'pseudocode' ? 'disassembly' : 'pseudocode');
    }
  });
  window.addEventListener('hashchange', render);
  renderSidebar();
  await render();
}

start().catch((error) => {
  setStatus(error.message + (location.protocol === 'file:' ? ' Serve this directory over HTTP, for example: python -m http.server --directory <site>.' : ''));
});
