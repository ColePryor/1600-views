// Refresh mailbox for the public dashboard.
//
// The TikTok scraper only runs on the Mac, so the live site cannot refresh
// itself. Instead, POST here drops a request into Vercel Blob; refresh_worker.py
// on the Mac polls the store, runs fetch -> publish -> deploy, and writes a
// status marker back. GET reports the current state so the page can poll.
//
// State lives in blob *pathnames* (no content reads, no CDN caching issues):
//   refresh-request/<ms>.json                 queued by the site, deleted by the worker
//   refresh-status/<running|done|error>-<ms>.json   written by the worker
import { list, put, get } from '@vercel/blob';

async function status() {
  const [req, st] = await Promise.all([
    list({ prefix: 'refresh-request/' }),
    list({ prefix: 'refresh-status/' }),
  ]);
  const pending = req.blobs.map(b => parseInt(b.pathname.split('/')[1], 10)).filter(Number.isFinite);
  let newest = null;
  for (const b of st.blobs) {
    const m = b.pathname.match(/^refresh-status\/(running|done|error)-(\d+)\.json$/);
    if (!m) continue;
    const at = parseInt(m[2], 10);
    if (!newest || at > newest.at) newest = { state: m[1], at, url: b.url };
  }
  let error = '';
  if (newest && newest.state === 'error') {
    try {
      const r = await get(newest.url, { access: 'private' });
      error = (await new Response(r.stream).text()).slice(0, 400);
    } catch (e) { error = 'refresh failed'; }
  }
  return {
    pending: pending.length,
    oldestPending: pending.length ? Math.min(...pending) : null,
    state: newest ? newest.state : null,
    at: newest ? newest.at : null,
    error,
  };
}

export default async function handler(req, res) {
  res.setHeader('Cache-Control', 'no-store');
  try {
    if (req.method === 'POST') {
      const now = Date.now();
      const cur = await status();
      // one queued request is enough; do not pile them up
      if (!cur.pending) {
        await put(`refresh-request/${now}.json`, '{}', { access: 'private', addRandomSuffix: false, contentType: 'application/json' });
      }
      res.status(200).json({ ok: true, requestedAt: now });
      return;
    }
    res.status(200).json(await status());
  } catch (e) {
    res.status(500).json({ error: String(e && e.message || e) });
  }
}
