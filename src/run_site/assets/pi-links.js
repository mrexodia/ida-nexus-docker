// Runs inside the sandboxed native Pi export. No access to the parent DOM.
document.addEventListener('click', (event) => {
  const anchor = event.target.closest('a[href]');
  if (!anchor) return;
  const raw = anchor.getAttribute('href');
  let parsed;
  try { parsed = new URL(raw, location.href); } catch { return; }
  if (/^[a-z]+:/i.test(raw) && parsed.origin !== location.origin) return;
  let path;
  try { path = decodeURIComponent(parsed.pathname); } catch { return; }
  const marker = path.indexOf('/workspace/');
  let key = marker >= 0 ? path.slice(marker + 1) : 'workspace/' + raw.split(/[?#]/)[0].replace(/^\.\//, '');
  if (!artifactRoutes[key]) return;
  anchor.href = artifactRoutes[key] + parsed.hash;
  anchor.target = '_top';
}, true);
