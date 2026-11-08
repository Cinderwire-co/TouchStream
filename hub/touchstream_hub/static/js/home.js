// Home: the whole system at a glance (spokes' health, what's next, the hub's internet and storage)
import { html, useState } from '../vendor/preact-htm.module.js';
import { ago, api, bytes, clock, compact, dayLabel, until, usePoll, useNow, viewsLabel, MARK } from './lib.js';
import { Icon } from './ui.js';
import { toast } from './dialogs.js';
import { repeatLabel, withPlanned } from './schedule.js';
import { AddSpokeModal } from './spokes.js';

const pct = (part, whole) => (whole ? Math.round((part / whole) * 100) : 0);
const tempClass = (t) => (t == null ? '' : t >= 80 ? 'bad' : t >= 70 ? 'warn' : 'ok');

function Meter({ used, total }) {
  const p = pct(used, total);
  return html`<div class=${`meter ${p >= 90 ? 'bad' : p >= 75 ? 'warn' : ''}`}><i style=${`width:${p}%`}></i></div>`;
}

/** Down / up speeds, or a button to measure them. */
function Speed({ speed, onTest, need }) {
  useNow(30000);
  const running = speed && speed.running;
  if (!speed || (!speed.at && !running)) {
    return html`<button class="btn ghost small" onClick=${onTest}><${Icon} name="speed" size=${14} /> Test speed</button>`;
  }
  const tight = need && speed.up_mbps && speed.up_mbps < need * 1.5;
  return html`<div class="speed">
    ${speed.at ? html`<span class="mono"><span class="muted">↓</span> ${Math.round(speed.down_mbps)} <span class="muted">↑</span>
      <b class=${tight ? 'warn-text' : ''}>${Math.round(speed.up_mbps)}</b> <span class="muted">Mbps</span></span>` : ''}
    <button class="link-btn" disabled=${running} onClick=${onTest}>${running ? 'Testing…' : 'Test again'}</button>
    ${speed.error && html`<span class="error small">${speed.error}</span>`}
  </div>`;
}

function Tile({ label, children, foot }) {
  return html`<div class="tile"><span class="eyebrow">${label}</span><div class="tile-body">${children}</div>
    ${foot && html`<div class="tile-foot muted small">${foot}</div>`}</div>`;
}

function SpokeRow({ spoke, refresh }) {
  const st = spoke.status || {};
  const store = st.storage;
  const [asked, setAsked] = useState(0);
  const test = async (e) => {
    e.stopPropagation();
    setAsked(Date.now());
    await api('POST', `/api/spokes/${spoke.id}/command`, { command: 'speedtest' }).catch(toast);
    refresh();
  };
  const speed = asked && Date.now() - asked < 8000 && !(st.speed && st.speed.running) ? { ...st.speed, running: true } : st.speed;
  const status = !spoke.online ? ['bad', `Offline · ${ago(spoke.last_seen)}`] : spoke.mode === 'live' ? ['live', 'Live']
    : spoke.mode === 'preview' ? ['ok', 'Preview'] : ['ok', 'Online'];
  return html`<div class="fleet-row clickable" onClick=${() => { location.hash = `#/spokes/${spoke.id}`; }}>
    <div class="min0 fleet-name"><span class=${`dot ${status[0]}`}></span>
      <div class="min0"><b class="ellipsis">${spoke.name}</b><div class="muted small ellipsis">${status[1]}${spoke.location ? ` · ${spoke.location}` : ''}</div></div></div>
    <div><span class="cell-label">Temperature</span>
      <span class=${`mono ${tempClass(st.temp)}-text`}>${spoke.online && st.temp != null ? `${Math.round(st.temp)}°C` : '—'}</span></div>
    <div class="min0"><span class="cell-label">Storage</span>${store ? html`<span class="small">${bytes(store.free)} free</span>
      <${Meter} used=${store.total - store.free} total=${store.total} />` : html`<span class="muted">—</span>`}</div>
    <div><span class="cell-label">On the spoke</span>${store ? html`<span class="small">${
      `${store.recordings} recording${store.recordings === 1 ? '' : 's'} · ${bytes(store.recordings_bytes || 0)}`}</span>${store.only_here > 0 && html`<div class="warn-text small">${store.only_here} not on the hub yet</div>`}`
      : html`<span class="muted">—</span>`}</div>
    <div onClick=${(e) => e.stopPropagation()}><span class="cell-label">Internet</span>
      ${spoke.online ? html`<${Speed} speed=${speed} onTest=${test} need=${spoke.bitrate_kbps / 1000} />` : html`<span class="muted">—</span>`}</div>
  </div>`;
}

export function HomePage() {
  const [data, setData] = useState(null);
  const [adding, setAdding] = useState(false);
  const onAddSpoke = () => setAdding(true);
  const [events, setEvents] = useState(null);
  const [info, setInfo] = useState(null);
  const refresh = async () => {
    const [d, ev, o] = await Promise.all([api('GET', '/api/dashboard'), api('GET', '/api/events?days=0'), api('GET', '/api/overview')]);
    setData(d); setEvents(ev); setInfo(o);
  };
  usePoll(refresh, 4000);
  useNow(30000);
  if (!data || !info) return null;
  const online = data.spokes.filter((s) => s.online);
  const live = data.spokes.filter((s) => s.mode === 'live');
  const upcoming = withPlanned(events.events.filter((e) => e.state !== 'ended'), events.planned);
  const next = upcoming.find((e) => e.state !== 'live');
  const sending = data.spokes.reduce((t, s) => t + s.bitrate_kbps / 1000, 0);
  const st = info.storage;
  const testHub = () => api('POST', '/api/speedtest').then(refresh).catch(toast);
  const names = Object.fromEntries(data.spokes.map((s) => [s.id, s.name]));

  return html`<div>
    <div class="page-head">
      <div><h1>Home</h1><p class="muted">Your spokes, what's next, and how everything is running.</p></div>
      <button class="btn primary" onClick=${onAddSpoke}><${Icon} name="plus" size=${16} /> Add spoke</button>
    </div>
    ${!data.mediamtx && html`<div class="banner"><div class="grow"><b>Streaming is off:</b> MediaMTX wasn't found on this computer.
      Re-run the installer or set TOUCHSTREAM_MEDIAMTX.</div></div>`}
    ${data.discovered.length > 0 && html`<div class="banner">
      <img src=${MARK} width="28" alt="" />
      <div class="grow"><b>${data.discovered.length === 1 ? data.discovered[0].name : `${data.discovered.length} spokes`}</b>
        ${' '}found on your network, ready to adopt.</div>
      <button class="btn primary small" onClick=${onAddSpoke}>Adopt</button></div>`}

    <div class="tiles">
      <${Tile} label="Spokes" foot=${live.length ? `${live.length} live now` : data.spokes.length ? 'None live right now' : 'None added yet'}>
        <span class="big">${online.length}<span class="muted">/${data.spokes.length}</span></span><span class="muted">online</span>
      </${Tile}>
      <${Tile} label="Next broadcast" foot=${next ? html`${names[next.spoke_id] || ''}${next.series ? html` · <${Icon} name="repeat" size=${12} /> ${repeatLabel(next.series)}` : ''}` : html`<a href="#/schedule">Schedule one</a>`}>
        ${next ? html`<a class="tile-link ellipsis" href="#/schedule">${next.title}</a>
          <span class="muted small">${dayLabel(next.start_at)} ${clock(next.start_at)} · ${until(next.start_at)}</span>`
          : html`<span class="muted">Nothing scheduled</span>`}
      </${Tile}>
      <${Tile} label="This hub's internet" foot=${info.speed.at ? `Tested ${ago(info.speed.at)} · ${info.speed.latency_ms} ms latency`
        : 'Upload matters most: the hub sends every stream on to YouTube.'}>
        <${Speed} speed=${info.speed} onTest=${testHub} need=${sending} />
      </${Tile}>
      <${Tile} label="This hub's storage" foot=${`${bytes(st.recordings)} of recordings · ${st.broadcasts} broadcast${st.broadcasts === 1 ? '' : 's'}`}>
        <span class="big">${bytes(st.free)}</span><span class="muted">free of ${bytes(st.total)}</span>
        <${Meter} used=${st.total - st.free} total=${st.total} />
      </${Tile}>
    </div>

    <div class="section-head"><h2>Spokes</h2><a class="btn ghost small" href="#/spokes">All spokes</a></div>
    ${data.spokes.length === 0 ? html`<div class="empty small-empty"><p class="muted">Plug an HDMI source into your TouchStream
        spoke, connect it to the internet, then add it here.</p>
        <button class="btn primary" onClick=${onAddSpoke}>Add a spoke</button></div>`
      : html`<div class="fleet">${data.spokes.map((s) => html`<${SpokeRow} key=${s.id} spoke=${s} refresh=${refresh} />`)}</div>`}

    <div class="home-cols">
      <div><div class="section-head"><h2>Coming up</h2><a class="btn ghost small" href="#/schedule">Schedule</a></div>
        ${upcoming.length ? html`<div class="mini-list">${upcoming.slice(0, 5).map((e) => html`<a class="mini" href=${e.state === 'planned' ? '#/schedule' : `#/broadcast/${e.id}`}>
          <span class=${`dot ${e.state === 'live' ? 'live' : ''}`}></span>
          <div class="min0 grow"><b class="ellipsis">${e.title}</b><div class="muted small">${e.state === 'live' ? 'Live now'
            : `${dayLabel(e.start_at)} ${clock(e.start_at)}`} · ${names[e.spoke_id] || e.spoke_name || ''}</div></div>
          ${e.series && html`<span class="muted" title=${repeatLabel(e.series)}><${Icon} name="repeat" size=${14} /></span>`}
        </a>`)}</div>` : html`<p class="muted small">Nothing scheduled.</p>`}</div>
      <div><div class="section-head"><h2>Recently aired</h2>${info.views && info.views.views > 0 && html`<span class="muted small"
          title="Views on YouTube of everything published in the last 30 days: broadcasts, edits and Shorts"><${Icon} name="eye" size=${14} />
          ${' '}${compact(info.views.views)} views in 30 days</span>`}</div>
        ${info.recent.length ? html`<div class="mini-list">${info.recent.map((e) => html`<div class="mini">
          <a class="min0 grow title-link" href=${`#/broadcast/${e.id}`}><b class="ellipsis">${e.title}</b><div class="muted small">${dayLabel(e.start_at)} · ${names[e.spoke_id] || e.spoke_name || ''}${e.stats ? ` · ${viewsLabel(e.stats.views)}` : ''}</div></a>
          ${e.recordings.some((c) => c.path) && html`<a class="btn ghost small" href=${`#/edit/${e.id}`}><${Icon} name="scissors" size=${13} /> ${e.edits.length ? 'Edited' : 'Edit'}</a>`}
        </div>`)}</div>` : html`<p class="muted small">Nothing has aired yet.</p>`}</div>
    </div>
    ${adding && html`<${AddSpokeModal} discovered=${data.discovered} hub=${data.hub} onClose=${() => { setAdding(false); refresh(); }} />`}
  </div>`;
}
