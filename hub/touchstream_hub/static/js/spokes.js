// Spokes: overview cards, the drill-in page, pairing, destinations, settings
import { html, useState, useEffect } from '../vendor/preact-htm.module.js';
import { api, ago, bytes, clock, dayLabel, hubAddress, until, usePoll, useNow, QUALITY } from './lib.js';
import { Avatar, Icon, IconButton, Modal, Preview, Switch } from './ui.js';
import { Select, usePopover } from './fields.js';
import { confirmAction, toast } from './dialogs.js';
import { Agenda, OtherRecordings, editorFor, withPlanned } from './schedule.js';

// ---- Pairing ----
/** Top bar: this hub's address for spokes, in a small popover with a copy button. */
export function HubAddressButton() {
  const { anchor, popover, open, setOpen, style } = usePopover(300);
  const [hub, setHub] = useState(null);
  const [copied, setCopied] = useState(false);
  useEffect(() => { if (open) api('GET', '/api/hub').then(setHub).catch(() => setHub({})); }, [open]);
  const address = hub && (hub.public_host && !hub.ip ? hub.public_host : hubAddress(hub));
  const copy = () => navigator.clipboard && navigator.clipboard.writeText(address)
    .then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500); });
  return html`<span class="select-wrap">
    <button ref=${anchor} class="icon-btn" aria-label="This hub's address" title="This hub's address" aria-expanded=${open}
      onClick=${() => setOpen(!open)}><${Icon} name="network" /></button>
    ${open && style && html`<div class="popover hub-pop" ref=${popover} style=${style}>
      <span class="eyebrow">This hub's address</span>
      <div class="copy"><code>${address || '…'}</code>
        <button class="btn small" disabled=${!address} onClick=${copy}>${copied ? 'Copied' : 'Copy'}</button></div>
      <p class="muted small">Spokes connect to this address. On a spoke's screen, tap <b>Hub</b> and enter it, or
        use${' '}<a href="#/spokes" onClick=${() => setOpen(false)}>Add spoke</a> to adopt one on your network.</p>
    </div>`}
  </span>`;
}

export function AddSpokeModal({ discovered, hub, onClose }) {
  const [pairing, setPairing] = useState(null);
  const [error, setError] = useState('');
  const [now, setNow] = useState(Date.now() / 1000);
  useEffect(() => { api('POST', '/api/pairing').then(setPairing).catch((e) => setError(e.message)); }, []);
  useEffect(() => { const t = setInterval(() => setNow(Date.now() / 1000), 1000); return () => clearInterval(t); }, []);
  const left = pairing ? Math.max(0, Math.round(pairing.expires_at - now)) : 0;
  const adopt = async (ip) => {
    try { await api('POST', `/api/discovered/${ip}/adopt`); onClose(); } catch (e) { setError(e.message); }
  };
  return html`<${Modal} title="Add a spoke" onClose=${onClose}>
    ${discovered.length > 0 && html`<div class="form" style="margin-bottom:22px">
      <span class="eyebrow">On your network</span>
      ${discovered.map((s) => html`<div class="row">
        <span class="dot ok"></span><div class="grow"><b>${s.name}</b><div class="muted small mono">${s.ip}</div></div>
        <button class="btn primary small" onClick=${() => adopt(s.ip)}>Adopt</button></div>`)}
    </div>`}
    <span class="eyebrow">Pair with a code</span>
    <ol class="steps" style="margin:12px 0 16px">
      <li>On the spoke's screen, tap anywhere, then <b>Connect to hub</b>.</li>
      <li>Enter this hub's address: <b class="mono">${hubAddress(hub)}</b></li>
      <li>Enter the code below.</li>
    </ol>
    <div class="code">${pairing ? pairing.code : '······'}</div>
    <p class="muted small" style="text-align:center;margin-top:10px">
      ${left > 0 ? `Expires in ${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')}` : 'Code expired'}</p>
    ${error && html`<p class="error">${error}</p>`}
    <div class="modal-foot"><button class="btn ghost" onClick=${onClose}>Done</button></div>
  </${Modal}>`;
}

// ---- Stream-key destinations ----
function DestinationModal({ spoke, platforms, dest, onClose }) {
  const platform = dest ? platforms.find((p) => p.id === dest.platform) || platforms[0] : platforms[0];
  const [form, setForm] = useState(dest
    ? { name: dest.name, url: dest.url, stream_key: '', transcode_kbps: dest.transcode_kbps }
    : { name: platform.name, url: platform.url, stream_key: '', transcode_kbps: 0 });
  const [error, setError] = useState('');
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });
  const save = async (e) => {
    e.preventDefault();
    const body = { ...form, platform: platform.id, transcode_kbps: Number(form.transcode_kbps) || 0 };
    try {
      if (dest) await api('PATCH', `/api/destinations/${dest.id}`, body);
      else await api('POST', `/api/spokes/${spoke.id}/destinations`, body);
      onClose();
    } catch (err) { setError(err.message); }
  };
  return html`<${Modal} title=${dest ? `Edit ${dest.name}` : `Stream key for ${platform.name}`} onClose=${onClose}>
    <form class="form" onSubmit=${save}>
      <p class="muted small">Prefer <b>connected channels</b> (Schedule page): the hub creates the stream and
        broadcast for you. Use a stream key for channels you can't connect.</p>
      <ol class="steps">${platform.steps.map((s) => html`<li>${s}</li>`)}</ol>
      <label class="field"><span>Name</span><input type="text" value=${form.name} onInput=${set('name')} /></label>
      <label class="field"><span>Server</span>
        <input type="text" class="mono" value=${form.url} onInput=${set('url')} /></label>
      <label class="field"><span>Stream key ${dest && dest.key_hint ? `(current ${dest.key_hint}; leave empty to keep)` : ''}</span>
        <input type="password" autocomplete="off" value=${form.stream_key} onInput=${set('stream_key')} /></label>
      <label class="field"><span>Quality</span>
        <${Select} label="Quality" value=${form.transcode_kbps} onChange=${(v) => setForm({ ...form, transcode_kbps: v })}
          options=${[[0, 'Same as the spoke', 'No re-encoding'], ...[9000, 6000, 4500, 3000].map((k) => [k, `Re-encode to ${k / 1000} Mbps`])]} /></label>
      ${error && html`<p class="error">${error}</p>`}
      <div class="modal-foot"><button class="btn primary">${dest ? 'Save' : 'Add'}</button></div>
    </form>
  </${Modal}>`;
}

function destState(dest, live, scheduled) {
  if (dest.channel_id && !scheduled) return ['', 'Streams during scheduled broadcasts'];
  if (!dest.channel_id && !dest.enabled) return ['', 'Off'];
  if (!live) return ['', 'Ready'];
  return dest.state === 'live' ? ['live', 'Live'] : dest.state === 'retrying' ? ['bad', 'Retrying'] : ['', 'Waiting for video'];
}

function DestinationRow({ dest, live, scheduled, onEdit, refresh }) {
  const toggle = (enabled) => api('PATCH', `/api/destinations/${dest.id}`, { enabled }).then(refresh);
  const remove = async () => (await confirmAction({ title: `Remove ${dest.name}?`, confirm: 'Remove', danger: true }))
    && api('DELETE', `/api/destinations/${dest.id}`).then(refresh).catch(toast);
  const [cls, label] = destState(dest, live, scheduled);
  return html`<div class="row">
    <span class=${`dot ${cls}`}></span>
    <div class="grow min0"><b>${dest.name}</b>
      <div class="muted small ellipsis" title=${dest.error || ''}>${label}${dest.transcode_kbps ? ` · ${dest.transcode_kbps / 1000} Mbps` : ''}
        ${dest.state === 'retrying' && dest.error ? ` · ${dest.error}` : ''}</div></div>
    ${!dest.channel_id && html`<${Switch} checked=${dest.enabled} onChange=${toggle} label="Stream to ${dest.name}" />
      <${IconButton} icon="edit" label="Edit" onClick=${onEdit} />`}
    <${IconButton} icon="trash" label="Remove" onClick=${remove} />
  </div>`;
}

// ---- Settings ----
export const FRAME_RATES = [['24', '1080p24 · film look'], ['25', '1080p25 · PAL regions'],
  ['30', '1080p30 · most common'], ['50', '1080p50 · smooth motion'], ['60', '1080p60 · smoothest motion']];

/** The frame rates this spoke can capture (it says in its heartbeat); all of them until it has. */
const ratesFor = (spoke) => {
  const own = spoke.status && spoke.status.framerates;
  return Array.isArray(own) && own.length ? FRAME_RATES.filter(([r]) => own.includes(r)) : FRAME_RATES;
};
// More motion needs more bits: 50 and 60 fps get a higher bitrate unless one is already set
const bitsFor = (rate, kbps) => (Number(rate) >= 50 && kbps < 10000 ? 12000 : kbps);

/** The source's actual rate differs from the setting (29.97 counts as 30). */
function rateMismatch(spoke) {
  const fps = spoke.status && spoke.status.signal_fps;
  return spoke.online && fps && Math.abs(fps - Number(spoke.framerate)) > 0.1 && !(spoke.framerate === '30' && Math.abs(fps - 29.97) < 0.05);
}

function SpokeSettingsModal({ spoke, onClose }) {
  const [form, setForm] = useState({ name: spoke.name, location: spoke.location, bitrate_kbps: spoke.bitrate_kbps,
    record_local: spoke.record_local, framerate: spoke.framerate });
  const setRate = (rate) => setForm({ ...form, framerate: rate, bitrate_kbps: bitsFor(rate, form.bitrate_kbps) });
  const [error, setError] = useState('');
  const [sent, setSent] = useState('');
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });
  const save = async (e) => {
    e.preventDefault();
    try { await api('PATCH', `/api/spokes/${spoke.id}`, { ...form, bitrate_kbps: Number(form.bitrate_kbps) }); onClose(); }
    catch (err) { setError(err.message); }
  };
  const command = async (c) => { await api('POST', `/api/spokes/${spoke.id}/command`, { command: c }); setSent(c === 'update' ? 'Update sent' : 'Restart sent'); };
  const forget = async () => {
    if (!(await confirmAction({ title: `Remove ${spoke.name}?`, confirm: 'Remove spoke', danger: true,
      message: 'The hub forgets this device. Its broadcasts, recordings, edits and shorts all stay here. Upcoming broadcasts stay on the schedule until you move them to another spoke.' }))) return;
    await api('DELETE', `/api/spokes/${spoke.id}`); location.hash = '#/spokes'; onClose();
  };
  return html`<${Modal} title="Spoke settings" onClose=${onClose}>
    <form class="form" onSubmit=${save}>
      <div class="form-row">
        <label class="field"><span>Name</span><input type="text" value=${form.name} onInput=${set('name')} /></label>
        <label class="field"><span>Location</span><input type="text" placeholder="Main sanctuary" value=${form.location} onInput=${set('location')} /></label>
      </div>
      <div class="form-row">
        <div class="field"><span>Frame rate</span>
          <${Select} label="Frame rate" value=${form.framerate} options=${ratesFor(spoke)} onChange=${setRate} /></div>
        <div class="field"><span>Quality (H.264 High)</span>
          <${Select} label="Quality" value=${form.bitrate_kbps} onChange=${(v) => setForm({ ...form, bitrate_kbps: v })}
            options=${QUALITY.map((q) => [q.kbps, `${q.label} · ${q.kbps / 1000} Mbps`, q.note])} /></div>
      </div>
      <p class="muted small" style="margin-top:-6px">The spoke asks its HDMI source for this rate. Most cameras follow
        automatically; if yours doesn't, set its HDMI output to match (29.97 is fine for 30). The list shows what this spoke
        can capture. Live, a spoke always keeps its own copy too, and uploads it here: nothing is lost if the internet
        drops.</p>
      ${error && html`<p class="error">${error}</p>`}
      <div class="modal-foot split">
        <span class="foot-group">
          <button type="button" class="btn ghost small" onClick=${() => command('reboot')}>Restart</button>
          <button type="button" class="btn ghost small" title="Get the latest TouchStream software and restart" onClick=${() => command('update')}>Update software</button>
          <button type="button" class="btn danger small" onClick=${forget}>Remove</button>
        </span>
        <button class="btn primary">Save</button>
      </div>
      ${sent && html`<p class="muted small">${sent}; the spoke picks it up within a few seconds.</p>`}
    </form>
  </${Modal}>`;
}

/** The same frame rate, quality and local copy on every spoke (and new ones). Each can still change on its own. */
export function AllSpokesModal({ onClose }) {
  const [d, setD] = useState(null);
  const [form, setForm] = useState(null);
  const [error, setError] = useState('');
  useEffect(() => { api('GET', '/api/spoke-defaults').then((r) => { setD(r); setForm({ framerate: r.framerate,
    bitrate_kbps: r.bitrate_kbps, record_local: r.record_local }); }); }, []);
  if (!form) return null;
  const own = (k) => (d.differ[k] ? ` · ${d.differ[k]} spoke${d.differ[k] === 1 ? ' has' : 's have'} its own` : '');
  const save = async (e) => {
    e.preventDefault();
    try { await api('PUT', '/api/spoke-defaults', form); onClose(); } catch (err) { setError(err.message); }
  };
  return html`<${Modal} title="Settings for all spokes" onClose=${onClose}>
    <form class="form" onSubmit=${save}>
      <p class="muted small">Saving sets these on all ${d.spokes} spoke${d.spokes === 1 ? '' : 's'}, and on spokes you add
        later (a spoke that can't capture the frame rate keeps its own). You can still change one spoke on its own in its
        settings.</p>
      <div class="form-row">
        <div class="field"><span>Frame rate${own('framerate')}</span>
          <${Select} label="Frame rate" value=${form.framerate} options=${FRAME_RATES} onChange=${(v) => setForm({ ...form, framerate: v,
            bitrate_kbps: bitsFor(v, form.bitrate_kbps) })} /></div>
        <div class="field"><span>Quality${own('bitrate_kbps')}</span>
          <${Select} label="Quality" value=${form.bitrate_kbps} onChange=${(v) => setForm({ ...form, bitrate_kbps: v })}
            options=${QUALITY.map((q) => [q.kbps, `${q.label} · ${q.kbps / 1000} Mbps`, q.note])} /></div>
      </div>
      ${error && html`<p class="error">${error}</p>`}
      <div class="modal-foot"><button type="button" class="btn ghost" onClick=${onClose}>Cancel</button>
        <button class="btn primary">Save for all spokes</button></div>
    </form>
  </${Modal}>`;
}

// ---- Shared pieces ----
function ModeControl({ spoke, refresh }) {
  const setMode = async (mode) => {
    if (spoke.mode === 'live' && mode !== 'live' && !(await confirmAction({ title: `End the stream from ${spoke.name}?`,
      message: 'It stops on every channel.', confirm: 'End stream', danger: true }))) return;
    await api('POST', `/api/spokes/${spoke.id}/mode`, { mode });
    refresh();
  };
  return html`<div class="segmented" role="group" aria-label="Mode">
    ${[['off', 'Off'], ['preview', 'Preview'], ['live', 'Live']].map(([m, label]) => html`
      <button class=${`${spoke.mode === m ? 'on' : ''} ${m === 'live' ? 'live' : ''}`} onClick=${() => setMode(m)}
        disabled=${!spoke.online && m !== 'off'}>${label}</button>`)}
  </div>`;
}

function StatusChips({ spoke }) {
  const st = spoke.status || {};
  return html`<div class="meta">
    <span class=${`chip ${spoke.online ? 'ok' : 'bad'}`}>${spoke.online ? 'Online' : 'Offline'}</span>
    ${spoke.online && html`<span class=${`chip ${st.signal ? '' : 'bad'}`}>${st.signal ? `HDMI ${st.signal_label || ''}` : 'No HDMI signal'}</span>`}
    ${rateMismatch(spoke) && html`<span class="chip bad" title="Set the source's HDMI output to match, or change the spoke's frame rate">
      Source sends ${st.signal_label}, set to 1080p${spoke.framerate}</span>`}
    ${st.direct && html`<span class="chip bad">Direct to YouTube</span>`}
    ${st.recording && html`<span class="chip ember">● Rec on spoke</span>`}
    ${st.uploading && html`<span class="chip">Uploading ${st.uploading}</span>`}
  </div>`;
}

function NextUp({ spoke, events }) {
  useNow(30000);
  const live = events.find((e) => e.spoke_id === spoke.id && e.state === 'live');
  const next = events.find((e) => e.spoke_id === spoke.id && e.state === 'scheduled');
  if (live) return html`<div class="nextup live"><span class="dot live"></span><b>${live.title}</b><span class="muted">live now</span></div>`;
  if (next) return html`<div class="nextup"><${Icon} name="calendar" size=${15} /><b>${next.title}</b>
    <span class="muted">${dayLabel(next.start_at)} ${clock(next.start_at)} · ${until(next.start_at)}</span></div>`;
  return html`<div class="nextup muted"><${Icon} name="calendar" size=${15} />Nothing scheduled</div>`;
}

// ---- Overview card ----
export function SpokeCard({ spoke, events, refresh }) {
  const open = () => { location.hash = `#/spokes/${spoke.id}`; };
  const channelDests = spoke.destinations.filter((d) => d.channel_id).length;
  const keyDests = spoke.destinations.filter((d) => !d.channel_id && d.enabled).length;
  return html`<article class=${`card ${spoke.mode === 'live' ? 'is-live' : ''}`}>
    <div class="card-head clickable" onClick=${open}>
      <div class="min0">
        <h3>${spoke.name}</h3>
        ${spoke.location && html`<div class="muted small">${spoke.location}</div>`}
        <${StatusChips} spoke=${spoke} />
      </div>
      <span class="muted"><${Icon} name="forward" size=${16} /></span>
    </div>
    <div class="clickable" onClick=${open}><${Preview} spoke=${spoke} /></div>
    <div class="controls">
      <${ModeControl} spoke=${spoke} refresh=${refresh} />
      <span class="muted small" style="margin-left:auto">1080p${spoke.framerate} · ${spoke.bitrate_kbps / 1000} Mbps</span>
    </div>
    <${NextUp} spoke=${spoke} events=${events} />
    <div class="card-foot muted small">
      ${channelDests} channel${channelDests === 1 ? '' : 's'} · ${keyDests} stream key${keyDests === 1 ? '' : 's'}
      <a href=${`#/spokes/${spoke.id}`}>Open</a>
    </div>
  </article>`;
}

// ---- Drill-in page ----
export function SpokePage({ id }) {
  const [data, setData] = useState(null);
  const [events, setEvents] = useState([]);
  const [planned, setPlanned] = useState([]);
  const [other, setOther] = useState([]);
  const [modal, setModal] = useState(null);
  const refresh = async () => {
    const [d, ev] = await Promise.all([api('GET', '/api/dashboard'), api('GET', `/api/events?spoke_id=${id}`)]);
    setData(d); setEvents(ev.events); setPlanned(ev.planned); setOther(ev.other_recordings);
  };
  usePoll(refresh, 2000, [id]);
  if (!data) return null;
  const spoke = data.spokes.find((s) => s.id === id);
  if (!spoke) return html`<div class="empty"><h2>Spoke not found</h2><a href="#/spokes">Back to spokes</a></div>`;
  const st = spoke.status || {};
  const live = spoke.mode === 'live';
  const liveEvent = events.find((e) => e.state === 'live');
  const channelDests = spoke.destinations.filter((d) => d.channel_id);
  const keyDests = spoke.destinations.filter((d) => !d.channel_id);
  const upcoming = withPlanned(events.filter((e) => e.state !== 'ended'), planned);
  const past = events.filter((e) => e.state === 'ended').reverse().slice(0, 20);
  const toggleBars = (on) => api('POST', `/api/spokes/${id}/test_pattern`, { on }).then(refresh);
  const health = [
    ['Source', !spoke.online ? '—' : st.source === 'hdmi' ? `HDMI ${st.signal_label || ''}` : st.source === 'slate'
      ? 'Holding screen (no HDMI)' : st.source === 'bars' ? 'Test pattern' : st.signal ? 'HDMI connected' : 'No HDMI signal'],
    ['Link to hub', !spoke.online ? `Offline · last seen ${ago(spoke.last_seen)}` : spoke.mode === 'off' ? 'Connected · not sending'
      : st.uplink === 'sending' ? (spoke.receiving ? 'Sending video' : 'Connecting…') : st.uplink === 'retrying' ? `Retrying · ${st.uplink_error || ''}` : 'Connecting…'],
    ['Network', st.network || '—'],
    ['Temperature', st.temp != null ? `${Math.round(st.temp)}°C` : '—'],
    ['CPU', st.cpu != null ? `${Math.round(st.cpu)}%` : '—'],
    ['Local copy', spoke.record_local ? (st.recording ? 'Recording now' : st.uploading ? `Uploading ${st.uploading}` : 'On') : 'Off'],
    ['Storage', st.storage ? `${bytes(st.storage.free)} free of ${bytes(st.storage.total)} · ${st.storage.recordings} recording${
      st.storage.recordings === 1 ? '' : 's'}` : '—'],
    ['Internet', st.speed && st.speed.at ? `↓ ${Math.round(st.speed.down_mbps)} ↑ ${Math.round(st.speed.up_mbps)} Mbps · ${ago(st.speed.at)}` : '—'],
    ['Version', st.version || '—'],
  ];
  return html`<div>
    <a class="crumb" href="#/spokes"><${Icon} name="back" size=${15} /> Spokes</a>
    <div class="page-head">
      <div><h1>${spoke.name}</h1>
        ${spoke.location && html`<p class="muted">${spoke.location}</p>`}
        <${StatusChips} spoke=${spoke} /></div>
      <button class="btn ghost" onClick=${() => setModal('settings')}><${Icon} name="gear" size=${16} /> Settings</button>
    </div>
    <div class="spoke-layout">
      <div class="panel flush">
        <${Preview} spoke=${spoke} large />
        <div class="controls">
          <${ModeControl} spoke=${spoke} refresh=${refresh} />
          <label class="inline-switch muted small"><${Switch} checked=${spoke.test_pattern} onChange=${toggleBars} label="Test pattern" /> Test pattern</label>
          <span class="muted small" style="margin-left:auto">1080p${spoke.framerate} · ${spoke.bitrate_kbps / 1000} Mbps</span>
        </div>
        ${liveEvent && html`<div class="nextup live"><span class="dot live"></span><b>${liveEvent.title}</b>
          <span class="muted">live on ${liveEvent.targets.map((t) => t.channel).join(', ') || 'stream keys'}</span></div>`}
      </div>
      <div class="side">
        <div class="panel">
          <h3>Health</h3>
          <div class="kv small">${health.map(([k, v]) => html`<span class="muted">${k}</span><span>${v}</span>`)}</div>
        </div>
        <div class="panel">
          <h3>Destinations</h3>
          ${channelDests.length > 0 && html`<span class="eyebrow">YouTube channels</span>
            ${channelDests.map((d) => html`<${DestinationRow} dest=${d} live=${live} scheduled=${liveEvent && liveEvent.targets.some((t) => t.destination_id === d.id)}
              refresh=${refresh} />`)}`}
          ${keyDests.length > 0 && html`<span class="eyebrow" style="display:block;margin-top:12px">Stream keys</span>
            ${keyDests.map((d) => html`<${DestinationRow} dest=${d} live=${live} scheduled onEdit=${() => setModal(d)} refresh=${refresh} />`)}`}
          ${!spoke.destinations.length && html`<p class="muted small">Schedule a broadcast to a connected channel, or add a stream key.</p>`}
          <button class="btn ghost small" style="margin-top:12px" onClick=${() => setModal('add')}>
            <${Icon} name="plus" size=${14} /> Add stream key</button>
        </div>
      </div>
    </div>
    <div class="section-head"><h2>Schedule</h2>
      <button class="btn primary small" onClick=${() => setModal('broadcast')}><${Icon} name="calendar" size=${14} /> Schedule broadcast</button></div>
    <${Agenda} events=${upcoming} spokes=${data.spokes} onEdit=${(e) => setModal({ event: e })} refresh=${refresh}
      empty="Nothing scheduled for this spoke." />
    <div class="section-head"><h2>Past broadcasts</h2></div>
    <${Agenda} events=${past} spokes=${data.spokes} onEdit=${(e) => setModal({ event: e })} refresh=${refresh}
      empty="Nothing has aired from this spoke yet. Every live stream, scheduled or not, shows up here with its recordings." />
    <${OtherRecordings} items=${other} spokes=${data.spokes} />
    ${modal === 'settings' && html`<${SpokeSettingsModal} spoke=${spoke} onClose=${() => { setModal(null); refresh(); }} />`}
    ${(modal === 'add' || (modal && modal.id)) && html`<${DestinationModal} spoke=${spoke} platforms=${data.platforms}
      dest=${modal === 'add' ? null : modal} onClose=${() => { setModal(null); refresh(); }} />`}
    ${(modal === 'broadcast' || (modal && modal.event)) && editorFor(modal === 'broadcast' ? 'new' : modal.event,
      { spokes: data.spokes, channels: data.channels, defaultSpoke: spoke.id, onClose: () => { setModal(null); refresh(); } })}
  </div>`;
}
