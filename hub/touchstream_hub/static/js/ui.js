// Shared components: brand, icons, switches, modals, the live preview player
import { html, useState, useEffect, useRef } from '../vendor/preact-htm.module.js';
import { MARK } from './lib.js';

export const Brand = () => html`<div class="brand"><img src=${MARK} alt="" />TouchStream</div>`;

// Simple line icons (24px grid), drawn in the current text color
const PATHS = {
  gear: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Zm7.4-3a7.4 7.4 0 0 0-.1-1.2l2-1.6-2-3.4-2.4 1a7 7 0 0 0-2-1.2L14.5 3h-5l-.4 2.6a7 7 0 0 0-2 1.2l-2.4-1-2 3.4 2 1.6a7.4 7.4 0 0 0 0 2.4l-2 1.6 2 3.4 2.4-1a7 7 0 0 0 2 1.2l.4 2.6h5l.4-2.6a7 7 0 0 0 2-1.2l2.4 1 2-3.4-2-1.6c.1-.4.1-.8.1-1.2Z',
  edit: 'M4 20h4L19 9l-4-4L4 16v4Zm9-13 4 4',
  trash: 'M4 7h16M10 11v6m4-6v6M6 7l1 13h10l1-13M9 7V4h6v3',
  back: 'M15 5l-7 7 7 7',
  forward: 'M9 5l7 7-7 7',
  external: 'M14 4h6v6m0-6-9 9M18 14v6H4V6h6',
  plus: 'M12 5v14M5 12h14',
  calendar: 'M4 6h16v14H4zM4 10h16M8 3v4m8-4v4',
  play: 'M7 5v14l11-7z',
  stop: 'M7 7h10v10H7z',
  close: 'M6 6l12 12M18 6 6 18',
  folder: 'M3 6h6l2 2h10v11H3z',
  scissors: 'M6 9a3 3 0 1 0 0-6 3 3 0 0 0 0 6Zm0 12a3 3 0 1 0 0-6 3 3 0 0 0 0 6ZM8.1 7.9 20 18M8.1 16.1 20 6',
  chevron: 'M6 9l6 6 6-6',
  check: 'M5 12.5l4.5 4.5L19 7.5',
  clock: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18ZM12 7v5l3.5 2',
  repeat: 'M17 3l3 3-3 3M4 11V9a3 3 0 0 1 3-3h13M7 21l-3-3 3-3m16-2v2a3 3 0 0 1-3 3H4',
  copy: 'M9 9h11v11H9zM5 15H4V4h11v1',
  network: 'M12 3a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5Zm-7 13a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5Zm14 0a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5ZM12 8v4m0 0-5.5 4.3M12 12l5.5 4.3',
  speed: 'M4.2 17.5a9 9 0 1 1 15.6 0M12 14l4.5-4.5M12 15.5a1.5 1.5 0 1 0 0-3',
  spark: 'M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8zM19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z',
  send: 'M4 12l16-8-6 16-2.5-6.5z',
  pause: 'M8 5v14M16 5v14',
  undo: 'M9 14 4 9l5-5M4 9h11a5 5 0 0 1 0 10h-3',
  redo: 'M15 14l5-5-5-5M20 9H9a5 5 0 0 0 0 10h3',
  pointer: 'M6 3l12 9-5.5 1.2L15 19l-2.4 1-2.6-5.8L6 18z',
  minus: 'M5 12h14',
  prev: 'M18 6v12l-9-6zM6 6v12',
  next: 'M6 6v12l9-6zM18 6v12',
  'mark-in': 'M10 5H6v14h4M14 12h6',
  'mark-out': 'M14 5h4v14h-4M4 12h6',
  fit: 'M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5',
};
export const Icon = ({ name, size = 18 }) => html`<svg width=${size} height=${size} viewBox="0 0 24 24" fill="none"
  stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
  <path d=${PATHS[name]} /></svg>`;

export const IconButton = ({ icon, label, onClick }) => html`<button class="icon-btn" onClick=${onClick}
  aria-label=${label} title=${label}><${Icon} name=${icon} /></button>`;

export function Switch({ checked, onChange, label }) {
  return html`<label class="switch" title=${label}>
    <input type="checkbox" checked=${checked} aria-label=${label} onChange=${(e) => onChange(e.target.checked)} /><i></i>
  </label>`;
}

/** A channel's YouTube avatar (through the hub), or its initial when there isn't one, or it won't load. */
export function Avatar({ channel, size = 24 }) {
  const src = channel.thumbnail || channel.channel_thumbnail;
  const [failed, setFailed] = useState(false);
  return src && !failed
    ? html`<img class="avatar" width=${size} height=${size} src=${src} alt="" onError=${() => setFailed(true)} />`
    : html`<span class="avatar" style=${`width:${size}px;height:${size}px`}>${(channel.title || channel.channel || '?')[0]}</span>`;
}

export function Modal({ title, onClose, wide, children }) {
  useEffect(() => {
    const esc = (e) => e.key === 'Escape' && onClose();
    addEventListener('keydown', esc);
    return () => removeEventListener('keydown', esc);
  }, []);
  return html`<div class="backdrop" onClick=${(e) => e.target === e.currentTarget && onClose()}>
    <div class=${`modal ${wide ? 'wide' : ''}`} role="dialog" aria-label=${title}>
      <div class="modal-head"><h2>${title}</h2><${IconButton} icon="close" label="Close" onClick=${onClose} /></div>
      ${children}
    </div>
  </div>`;
}

/** Live preview from the hub's HLS proxy; restarts itself if the picture freezes. */
export function HlsVideo({ src }) {
  const ref = useRef();
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const video = ref.current;
    let hls = null;
    // hls.js wherever MSE exists (predictable for live streams); native HLS only as a fallback (iOS)
    if (window.Hls && window.Hls.isSupported()) {
      hls = new window.Hls({ liveSyncDurationCount: 2, manifestLoadingMaxRetry: 30, manifestLoadingRetryDelay: 1000 });
      hls.loadSource(src);
      hls.attachMedia(video);
    } else {
      video.src = src;
    }
    // Watchdog: if the picture stops advancing for 8 s, start over with a fresh player
    let last = -1, still = 0;
    const timer = setInterval(() => {
      still = video.currentTime === last ? still + 1 : 0;
      last = video.currentTime;
      if (still >= 4) setAttempt((a) => a + 1);
    }, 2000);
    return () => {
      clearInterval(timer);
      if (hls) hls.destroy();
      video.removeAttribute('src');
      video.load();
    };
  }, [src, attempt]);
  return html`<video ref=${ref} autoplay muted playsinline></video>`;
}

/** The 16:9 picture box: live preview when the hub is receiving, otherwise a branded placeholder. */
export function Preview({ spoke, large }) {
  const st = spoke.status || {};
  const message = !spoke.online ? 'Spoke offline'
    : spoke.mode === 'off' ? 'Press Preview to see the picture'
    : 'Connecting…';
  return html`<div class=${`preview ${large ? 'large' : ''}`}>
    ${spoke.receiving ? html`<${HlsVideo} src=${`/hls/${spoke.preview_path}/index.m3u8`} />`
      : html`<div class="placeholder"><img src=${MARK} alt="" /><span class="small">${message}</span></div>`}
    ${spoke.mode === 'live' && html`<span class="badge chip live">● Live</span>`}
    ${spoke.receiving && st.source && st.source !== 'hdmi' && html`<span class="badge right chip">${
      st.source === 'slate' ? 'Holding screen' : 'Test pattern'}</span>`}
  </div>`;
}
