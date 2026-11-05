// Shorts (hub only), the editor's Shorts tab: suggested moments, making a vertical short of one, and the shorts made
import { html, useState } from '../vendor/preact-htm.module.js';
import { api, ago, hubEnv, reveal, revealLabel } from './lib.js';
import { Avatar, Icon, IconButton, Modal, Switch } from './ui.js';
import { Select } from './fields.js';
import { confirmAction, toast } from './dialogs.js';

const LEAD = 0.15, TAIL = 0.4;   // a breath before the first sentence and after the last (as the hub's shorts.py)
export const SHORT_BUSY = ['queued', 'analyzing', 'rendering', 'publishing'];
export const SHORT_LOOK = { framing: 'fill', style: 'bold', accent: 'ember', trim_pauses: true };
const FRAMINGS = [['fill', 'Follow speaker'], ['fit', 'Whole picture']];

const fmt = (s) => {
  s = Math.max(0, Math.round(s || 0));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}` : `${m}:${String(sec).padStart(2, '0')}`;
};

/** [start, end] widened to the sentences they fall in, with a breath either side. */
export function widen([start, end], sentences) {
  for (const s of sentences || []) {
    if (s.start <= start && start < s.end) start = s.start;
    if (s.start < end && end <= s.end) end = s.end;
  }
  return sentences && sentences.length ? [Math.max(0, start - LEAD), end + TAIL] : [start, end];
}

function PublishShort({ short, event, channels, onClose }) {
  const fromEvent = event.targets.map((t) => t.channel_id).filter((id) => channels.some((c) => c.id === id));
  const [f, setF] = useState({ channel_ids: fromEvent.length ? fromEvent : channels.slice(0, 1).map((c) => c.id),
    title: short.title, description: '', privacy: 'public' });
  const [error, setError] = useState('');
  const toggle = (id) => setF({ ...f, channel_ids: f.channel_ids.includes(id) ? f.channel_ids.filter((c) => c !== id) : [...f.channel_ids, id] });
  const submit = async (e) => {
    e.preventDefault();
    try { await api('POST', `/api/shorts/${short.id}/publish`, f); onClose(); } catch (err) { setError(err.message); }
  };
  return html`<${Modal} title="Publish as a YouTube Short" onClose=${onClose}>
    <form class="form" onSubmit=${submit}>
      <p class="muted small">YouTube shows a vertical video up to three minutes long as a Short. #Shorts is added to the
        description. YouTube allows about 6 uploads a day per Google project.</p>
      <label class="field"><span>Title</span><input type="text" maxlength="100" value=${f.title} onInput=${(e) => setF({ ...f, title: e.target.value })} /></label>
      <label class="field"><span>Description</span><textarea rows="3" value=${f.description} onInput=${(e) => setF({ ...f, description: e.target.value })}></textarea></label>
      <div class="field"><span>Channels</span>
        ${channels.map((c) => html`<label class="check"><input type="checkbox" checked=${f.channel_ids.includes(c.id)}
          onChange=${() => toggle(c.id)} /><${Avatar} channel=${c} size=${22} /> ${c.title}</label>`)}</div>
      <div class="field"><span>Visibility</span><${Select} label="Visibility" value=${f.privacy} onChange=${(v) => setF({ ...f, privacy: v })}
        options=${[['public', 'Public'], ['unlisted', 'Unlisted'], ['private', 'Private']]} /></div>
      ${error && html`<p class="error">${error}</p>`}
      <div class="modal-foot"><button type="button" class="btn ghost" onClick=${onClose}>Cancel</button>
        <button class="btn primary" disabled=${!f.channel_ids.length}>Publish</button></div>
    </form>
  </${Modal}>`;
}

/** A finished short, played in a phone-shaped frame. */
function Watch({ short, onClose }) {
  return html`<${Modal} title=${short.title} onClose=${onClose}>
    <div class="phone watch"><video src=${`/api/recordings/file/${short.output}`} controls autoplay playsinline></video></div>
  </${Modal}>`;
}

function ShortCard({ short, event, channels, onChange, onPick, refresh }) {
  const [publishing, setPublishing] = useState(false);
  const [watching, setWatching] = useState(false);
  const working = SHORT_BUSY.includes(short.status);
  const remove = async () => (await confirmAction({ title: `Delete "${short.title}"?`, confirm: 'Delete', danger: true,
    message: 'Its video is deleted from the hub. Anything already on YouTube stays there.' }))
    && api('DELETE', `/api/shorts/${short.id}`).then(refresh).catch(toast);
  const status = short.status === 'queued' ? 'Waiting its turn…'
    : short.status === 'analyzing' ? 'Finding the speaker…'
    : short.status === 'rendering' ? `Making it… ${Math.round(short.progress * 100)}%`
    : short.status === 'publishing' ? `Uploading to YouTube ${Math.round(short.progress * 100)}%`
    : short.status === 'failed' ? html`<span class="error">Failed: ${short.error}</span>`
    : `${fmt(short.duration)} · made ${ago(short.created_at)}`;
  return html`<div class="short-card">
    <button class="short-thumb" disabled=${!short.output || working} onClick=${() => setWatching(true)} aria-label=${`Play ${short.title}`}>
      ${short.output && !working ? html`<video src=${`/api/recordings/file/${short.output}#t=1`} preload="metadata" muted></video>`
        : html`<span class="spinner"></span>`}
    </button>
    <div class="min0 short-body" onClick=${(e) => !e.target.closest('button, a') && onPick()} title="Go to it on the timeline">
      <button class="link-title ellipsis" onClick=${onPick}>${short.title}</button>
      <div class="muted small">${status}${short.status === 'done' && short.error ? html` <span class="error">${short.error}</span>` : ''}</div>
      ${working && html`<div class="progress"><i style=${`width:${Math.round(short.progress * 100)}%`}></i></div>`}
      ${short.published.length > 0 && html`<div class="published">${short.published.map((p) => html`<a href=${p.url}
        target="_blank" rel="noopener">${p.channel} ↗</a>`)}</div>`}
      ${short.status === 'done' && html`<div class="version-actions">
        ${channels.length > 0 && html`<button class="btn small" onClick=${() => setPublishing(true)}>Publish</button>`}
        <a class="btn ghost small" href=${`/api/recordings/file/${short.output}`} download>Download</a>
        <button class="btn ghost small" onClick=${onChange}>Change</button>
        ${hubEnv.local && html`<${IconButton} icon="folder" label=${revealLabel()} onClick=${() => reveal({ file: short.output })} />`}
      </div>`}
    </div>
    ${!working && html`<${IconButton} icon="trash" label="Delete" onClick=${remove} />`}
    ${publishing && html`<${PublishShort} short=${short} event=${event} channels=${channels}
      onClose=${() => { setPublishing(false); refresh(); }} />`}
    ${watching && html`<${Watch} short=${short} onClose=${() => setWatching(false)} />`}
  </div>`;
}

/**
 * The editor's Shorts tab. range: [start, end] in the recording (from the In and Out marks, or a picked moment);
 * pick(moment): show a moment on the timeline; preview(a, b): play that stretch of the recording.
 */
export function ShortsTab({ eventId, data, source, range, picked, pick, preview, look, setLook, refresh, reload }) {
  const [title, setTitle] = useState('');
  const [changing, setChanging] = useState(null);
  const [suggesting, setSuggesting] = useState(false);
  const [error, setError] = useState('');
  const options = data.shorts_options;
  if (!options.available) return html`<div class="panel"><p class="muted">Shorts come with transcription: the installers
    and the Docker image include it. For other installs add the <span class="mono">transcribe</span> extra.</p></div>`;

  const moment = range && widen(range, data.sentences);
  const length = moment ? moment[1] - moment[0] : 0;
  const tooLong = length > options.max_length + 0.5;
  const set = (patch) => setLook({ ...look, ...patch });
  const firstWords = () => {
    const s = moment && (data.sentences || []).find((x) => x.end > moment[0] + 0.2);
    const words = s ? s.text.replace(/[.?!,]+$/, '').split(/\s+/) : [];
    return (picked && picked.title) || (words.length ? words.slice(0, 8).join(' ') + (words.length > 8 ? '…' : '') : data.event.title);
  };
  const suggest = async () => {
    setSuggesting(true); setError('');
    try { await api('POST', `/api/events/${eventId}/suggestions`); await reload(); } catch (err) { setError(err.message); }
    setSuggesting(false);
  };
  const make = async () => {
    setError('');
    const body = { ...look, title: title || firstWords(), source: source.path, start: moment[0], end: moment[1] };
    try {
      if (changing) await api('PUT', `/api/shorts/${changing.id}`, body);
      else await api('POST', `/api/events/${eventId}/shorts`, body);
      setChanging(null); setTitle('');
      refresh();
    } catch (err) { setError(err.message); }
  };
  const change = (short) => {
    setChanging(short); setTitle(short.title);
    setLook({ framing: short.spec.framing, style: short.spec.style, accent: short.spec.accent, trim_pauses: short.spec.trim_pauses });
    pick({ start: short.spec.start, end: short.spec.end, title: short.title, made: true });
  };
  const suggestions = (data.suggestions && data.suggestions.moments) || [];

  return html`<div class="shorts-tab">
    <div class="panel">
      <div class="row-tight"><h3 class="grow" style="margin:0">Suggested moments</h3>
        ${data.ai && data.sentences && html`<button class="btn ghost small" disabled=${suggesting} onClick=${suggest}>
          <${Icon} name="spark" size=${14} /> ${suggesting ? 'Reading the transcript…' : suggestions.length ? 'Suggest again' : 'Suggest moments'}</button>`}</div>
      ${!data.sentences ? html`<p class="muted small">${['queued', 'running'].includes(data.transcript_status)
          ? 'The transcript is being made: suggestions and captions need it.'
          : html`Transcribe this broadcast for suggested moments and captions.${' '}<a href=${`#/transcript/${eventId}`}>Transcript</a>`}</p>`
        : !data.ai ? html`<p class="muted small">Turn on the assistant (Settings · Assistant) and it suggests the moments that
          would make the best shorts. Or mark one yourself: I and O on the timeline.</p>`
        : !suggestions.length ? html`<p class="muted small">The assistant reads the transcript and picks the moments that stand
          on their own: a clear point, a story, a strong line.</p>`
        : html`<div class="moments">${suggestions.map((m) => html`<button class=${`moment ${picked && picked.start === m.start && picked.end === m.end ? 'on' : ''}`}
            onClick=${() => pick(m)}>
            <span class="row-tight"><b class="grow">${m.title}</b><span class="mono small muted">${fmt(m.end - m.start)}</span></span>
            <span class="muted small">${fmt(m.start)} · ${m.why}</span></button>`)}</div>`}
      ${error && !moment && html`<p class="error small">${error}</p>`}
    </div>

    <div class="panel form">
      <div class="row-tight"><h3 class="grow" style="margin:0">${changing ? `Changing "${changing.title}"` : 'New short'}</h3>
        ${changing && html`<button class="btn ghost small" onClick=${() => { setChanging(null); setTitle(''); }}>Cancel</button>`}</div>
      ${moment ? html`<div class="row-tight">
          <span class="grow small">${fmt(moment[0])} – ${fmt(moment[1])} · <b class=${tooLong ? 'error' : ''}>${fmt(length)}</b>
            ${tooLong ? html` <span class="error">· up to 3 minutes</span>` : ''}</span>
          <button class="btn ghost small" onClick=${() => preview(moment[0], moment[1])}><${Icon} name="play" size=${13} /> Play</button></div>
          ${data.sentences && html`<p class="muted small">Starts and ends on whole sentences.</p>`}`
        : html`<p class="muted small">Pick a suggested moment, click one on the Shorts track, or mark one on the timeline:
            I where it starts, O where it ends.</p>`}
      <div class="field"><span>Framing</span>
        <div class="segmented wide" role="group" aria-label="Framing">${FRAMINGS.map(([v, label]) => html`<button type="button"
          class=${look.framing === v ? 'on' : ''} onClick=${() => set({ framing: v })}>${label}</button>`)}</div>
        <small class="muted">${look.framing === 'fill' ? 'A vertical window that moves with whoever is speaking (the outline on the picture), and the whole picture while nobody\'s in it, like a slide.'
          : 'The whole picture across the middle, over a blurred copy of itself.'}</small></div>
      ${data.sentences && html`<div class="field"><span>Captions</span>
        <div class="styles">
          ${options.styles.map(([v, label]) => html`<button type="button" class=${`style-pick ${look.style === v ? 'on' : ''}`}
            onClick=${() => set({ style: v })} title=${label}>
            <img src=${`/api/shorts/style.png?style=${v}&accent=${look.accent}`} alt="" /><span>${label}</span></button>`)}
          <button type="button" class=${`style-pick none ${look.style === 'none' ? 'on' : ''}`} onClick=${() => set({ style: 'none' })}>
            <span>No captions</span></button>
        </div>
        ${look.style !== 'none' && html`<div class="swatches" role="group" aria-label="Accent color">
          ${options.accents.map(([name, color]) => html`<button type="button" class=${look.accent === name ? 'on' : ''}
            style=${`--swatch:${color}`} title=${name} aria-label=${name} onClick=${() => set({ accent: name })}></button>`)}</div>`}
      </div>`}
      <label class="inline-switch"><${Switch} checked=${look.trim_pauses} label="Take out pauses"
        onChange=${(v) => set({ trim_pauses: v })} /> Take out pauses</label>
      <label class="field"><span>Title</span><input type="text" maxlength="100" placeholder=${moment ? firstWords() : 'Named from its first words'}
        value=${title} onInput=${(e) => setTitle(e.target.value)} /></label>
      ${error && moment && html`<p class="error">${error}</p>`}
      <button class="btn primary" disabled=${!moment || tooLong || length < 3} onClick=${make}>
        <${Icon} name="spark" size=${16} /> ${changing ? 'Make it again' : 'Make short'}${moment ? ` · ${fmt(length)}` : ''}</button>
    </div>

    <div class="panel">
      <h3>Shorts${data.shorts.length ? ` · ${data.shorts.length}` : ''}</h3>
      ${data.shorts.length === 0 ? html`<p class="muted small">None yet. Each is saved in the broadcast's folder, ready to
          download or publish.</p>`
      : html`<div class="short-list">${[...data.shorts].reverse().map((s) => html`<${ShortCard} key=${s.id} short=${s} event=${data.event}
          channels=${data.channels} onChange=${() => change(s)} refresh=${refresh}
          onPick=${() => pick({ start: s.spec.start, end: s.spec.end, title: s.title, made: true })} />`)}</div>`}
    </div>
  </div>`;
}
