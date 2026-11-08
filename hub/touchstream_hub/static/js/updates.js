// The update banner, under the top bar on every page: an update to install (when they aren't installed
// automatically, or can't be), one under way, or one just done. Settings > General has the rest.
import { html, useState } from '../vendor/preact-htm.module.js';
import { api, usePoll } from './lib.js';
import { toast } from './dialogs.js';

const seen = (key) => { try { return localStorage.getItem(key); } catch (e) { return null; } };
const remember = (key) => { try { localStorage.setItem(key, '1'); } catch (e) { /* private window */ } };

export function UpdateBanner({ version }) {
  const [u, setU] = useState(null);
  const [away, setAway] = useState(false);   // the hub not answering: it's restarting
  const [, redraw] = useState(0);
  usePoll(async () => {
    try {
      const s = await api('GET', '/api/state');
      // The hub came back as a new version: its pages are new too
      if (s.version !== version) location.reload();
      setU(s.update || null); setAway(false);
    } catch (e) { setAway(true); }
  }, u && u.applying ? 3000 : 60000, [u && !!u.applying]);
  if (!u) return null;
  const dismiss = (key) => { remember(key); redraw((n) => n + 1); };
  const install = async () => {
    try { const r = await api('POST', '/api/updates/apply'); setU({ ...u, requested: r.requested, applying: r.applying }); }
    catch (err) { toast(err); }
  };

  if (u.applying) {
    return html`<div class="update-banner" role="status"><span class="spinner"></span>
      <div class="grow"><b>Updating TouchStream to ${u.applying.version}.</b> ${away ? 'The hub is restarting…' : 'The hub restarts in a moment; this page reloads by itself.'}</div></div>`;
  }
  if (u.updated && u.updated.to === u.version && Date.now() / 1000 - u.updated.at < 86400 && !seen(`ts-updated-${u.version}`)) {
    return html`<div class="update-banner done" role="status">
      <div class="grow"><b>Updated to TouchStream ${u.version}.</b>${u.spokes_behind.length ? ' Its spokes follow as each one goes idle.' : ''}</div>
      <a class="btn ghost small" href="#/settings/general">Details</a>
      <button class="btn ghost small" onClick=${() => dismiss(`ts-updated-${u.version}`)}>Dismiss</button></div>`;
  }
  if (u.available && !(u.auto && u.can_update_self) && !seen(`ts-later-${u.latest}`)) {
    return html`<div class="update-banner" role="status">
      <div class="grow"><b>TouchStream ${u.latest} is ready.</b> ${u.requested
        ? `It installs as soon as nothing’s going on${u.waiting_for ? `: ${u.waiting_for}` : ''}.`
        : u.can_update_self ? 'You’re on ' + u.version + '. Updating takes a minute and nothing’s lost.'
        : 'This hub can’t update itself: see how in Settings.'}</div>
      ${u.can_update_self && !u.requested && html`<button class="btn primary small" onClick=${install}>Update now</button>`}
      <a class="btn ghost small" href="#/settings/general">What’s new</a>
      ${!u.requested && html`<button class="btn ghost small" onClick=${() => dismiss(`ts-later-${u.latest}`)}>Later</button>`}</div>`;
  }
  const behind = u.spokes_behind || [];
  if (behind.length && !u.auto && !seen(`ts-spokes-${u.version}-${behind.join(',')}`)) {
    return html`<div class="update-banner" role="status">
      <div class="grow"><b>${behind.length === 1 ? behind[0] : `${behind.length} spokes`}</b> ${behind.length === 1 ? 'has' : 'have'} an update waiting, to TouchStream ${u.version}.</div>
      <a class="btn primary small" href="#/settings/general">Update</a>
      <button class="btn ghost small" onClick=${() => dismiss(`ts-spokes-${u.version}-${behind.join(',')}`)}>Later</button></div>`;
  }
  return null;
}
