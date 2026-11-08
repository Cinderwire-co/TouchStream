// Shared helpers: API calls, formatting, small hooks
import { useState, useEffect } from '../vendor/preact-htm.module.js';
import { canLeave, hasUnsaved, toast } from './dialogs.js';

export const MARK = '/static/img/touchstream-mark.svg';
export const CINDERWIRE = '/static/img/cinderwire_mark.png';
export const QUALITY = [
  { kbps: 12000, label: 'Maximum', note: '12 Mbps · best recording, needs fast upload' },
  { kbps: 8000, label: 'High', note: '8 Mbps · great 1080p for YouTube' },
  { kbps: 6000, label: 'Standard', note: '6 Mbps · safe on most connections' },
  { kbps: 4000, label: 'Light', note: '4 Mbps · slower connections' },
];

// Where the hub runs (from /api/state): is this browser on the hub's own computer, and which OS
export const hubEnv = { local: false, platform: 'linux' };
export const revealLabel = () => ({ mac: 'Show in Finder', windows: 'Show in File Explorer' })[hubEnv.platform] || 'Open folder';
export const reveal = (target = {}) => api('POST', '/api/reveal', target).catch(toast);

/** The address to give a spoke for this hub: its LAN IP when the hub knows it, else this page's. */
export function hubAddress(hub) {
  if (hub && hub.ip) return `${hub.ip}:${hub.port}`;
  return ['localhost', '127.0.0.1'].includes(location.hostname) ? `this computer's IP address:${location.port || 80}` : location.host;
}

export async function api(method, path, body, { raw } = {}) {
  const res = await fetch(path, {
    method, credentials: 'same-origin',
    headers: raw ? { 'Content-Type': raw.type } : body ? { 'Content-Type': 'application/json' } : {},
    body: raw || (body ? JSON.stringify(body) : undefined),
  });
  const data = await res.json().catch(() => ({}));
  if (res.status === 401 && !path.startsWith('/api/login')) window.dispatchEvent(new Event('ts-auth'));
  if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : res.statusText);
  return data;
}

/** 1234 -> "1.2K" */
export const compact = (n) => new Intl.NumberFormat([], { notation: 'compact', maximumFractionDigits: 1 }).format(n || 0);
export const viewsLabel = (n) => `${compact(n)} view${n === 1 ? '' : 's'}`;
export const bytes = (n) => n > 1e9 ? `${(n / 1e9).toFixed(1)} GB` : n > 1e6 ? `${(n / 1e6).toFixed(0)} MB` : `${Math.ceil(n / 1e3)} KB`;
export const when = (t) => new Date(t * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
export const clock = (t) => new Date(t * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
export const ago = (t) => {
  if (!t) return 'never';
  const s = Date.now() / 1000 - t;
  return s < 60 ? 'just now' : s < 3600 ? `${Math.floor(s / 60)} min ago` : s < 86400 ? `${Math.floor(s / 3600)} h ago` : when(t);
};
export const until = (t) => {
  const s = t - Date.now() / 1000;
  if (s <= 0) return 'now';
  // Round once, then split, so it never reads "2 h 60 min"
  const minutes = Math.ceil(s / 60);
  if (minutes < 60) return `in ${minutes} min`;
  if (minutes < 1440) return `in ${Math.floor(minutes / 60)} h${minutes % 60 ? ` ${minutes % 60} min` : ''}`;
  return `in ${Math.round(minutes / 1440)} days`;
};

/** "Today", "Tomorrow", or "Sunday 9 Nov" for grouping an agenda. */
export function dayLabel(t) {
  const d = new Date(t * 1000);
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const days = Math.round((new Date(d).setHours(0, 0, 0, 0) - today) / 86400000);
  if (days === 0) return 'Today';
  if (days === 1) return 'Tomorrow';
  if (days === -1) return 'Yesterday';
  return d.toLocaleDateString([], { weekday: 'long', day: 'numeric', month: 'short' });
}

export function usePoll(fn, ms, deps = []) {
  useEffect(() => {
    let alive = true, timer;
    const tick = async () => { try { await fn(); } catch (e) { /* callers show errors */ } if (alive) timer = setTimeout(tick, ms); };
    tick();
    return () => { alive = false; clearTimeout(timer); };
  }, deps);
}

/** The current #/route. Leaving a page with unsaved work (link or back button) asks first. */
export function useHash() {
  const [hash, setHash] = useState(location.hash || '#/');
  useEffect(() => {
    let current = location.hash || '#/';
    const on = async () => {
      const next = location.hash || '#/';
      if (next === current) return;
      if (hasUnsaved()) {
        history.replaceState(null, '', current);   // stay put while asking
        if (!(await canLeave())) return;
        history.replaceState(null, '', next);
      }
      current = next;
      setHash(next);
      scrollTo(0, 0);
    };
    addEventListener('hashchange', on);
    return () => removeEventListener('hashchange', on);
  }, []);
  return hash;
}

/** Re-render every `ms` so countdowns stay current. */
export function useNow(ms = 15000) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), ms); return () => clearInterval(t); }, [ms]);
  return now;
}
