// Post-broadcast editor (hub only): trim, fades, cut out sections, render, publish to YouTube
import { html, useState, useEffect, useRef } from '../vendor/preact-htm.module.js';
import { api, ago, bytes, hubEnv, reveal, revealLabel, usePoll } from './lib.js';
import { Avatar, Icon, IconButton, Modal } from './ui.js';
import { Select } from './fields.js';
import { confirmAction, toast, useLeaveGuard } from './dialogs.js';

export const fmt = (s) => {
  s = Math.max(0, s || 0);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = Math.floor(s % 60);
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}` : `${m}:${String(sec).padStart(2, '0')}`;
};

/** The parts kept by an edit, as [start, end] pairs (mirrors the hub's keep_ranges). */
function keepRanges(spec, duration) {
  const start = Math.max(0, spec.in), end = Math.min(duration, spec.out ?? duration);
  const keep = [];
  let cursor = start;
  for (const [a0, b0] of [...spec.cuts].sort((x, y) => x[0] - y[0])) {
    const a = Math.max(a0, start), b = Math.min(b0, end);
    if (b <= a || b <= cursor) continue;   // outside the trimmed span, or already cut
    if (a > cursor) keep.push([cursor, a]);
    cursor = Math.max(cursor, b);
  }
  if (end > cursor) keep.push([cursor, end]);
  return keep.filter(([a, b]) => b - a >= 0.5);
}

const DEFAULT_SPEC = { in: 0, out: null, cuts: [], fade_in: 1, fade_out: 2, dip: 0.5 };
const FADES = [[0, 'None'], [0.5, '0.5 s'], [1, '1 s'], [2, '2 s'], [3, '3 s'], [5, '5 s']];
const DIPS = [[0, 'Straight cut'], [0.5, 'Quick dip to black'], [1, 'Slow dip to black']];
const HIDE = [['private', 'Make it private'], ['unlisted', 'Make it unlisted'], ['keep', 'Leave it as it is']];
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

function PublishModal({ edit, event, channels, onClose }) {
  const earlier = edit.published.filter((p) => !p.hidden);
  const [f, setF] = useState({
    channel_ids: event.targets.map((t) => t.channel_id).filter((id) => channels.some((c) => c.id === id)),
    title: event.title, description: event.description || '', privacy: 'public', hide_original: 'private', hide_previous: 'private',
  });
  const [error, setError] = useState('');
  const toggle = (id) => setF({ ...f, channel_ids: f.channel_ids.includes(id) ? f.channel_ids.filter((c) => c !== id) : [...f.channel_ids, id] });
  const submit = async (e) => {
    e.preventDefault();
    try { await api('POST', `/api/edits/${edit.id}/publish`, f); onClose(); } catch (err) { setError(err.message); }
  };
  return html`<${Modal} title="Publish to YouTube" onClose=${onClose}>
    <form class="form" onSubmit=${submit}>
      <p class="muted small">Uploads the edited video as a new YouTube video, with the broadcast's thumbnail. YouTube
        allows about 6 uploads a day per Google project.</p>
      <label class="field"><span>Title</span><input type="text" maxlength="100" value=${f.title} onInput=${(e) => setF({ ...f, title: e.target.value })} /></label>
      <label class="field"><span>Description</span><textarea rows="3" value=${f.description} onInput=${(e) => setF({ ...f, description: e.target.value })}></textarea></label>
      <div class="field"><span>Channels</span>
        ${channels.map((c) => html`<label class="check"><input type="checkbox" checked=${f.channel_ids.includes(c.id)}
          onChange=${() => toggle(c.id)} /><${Avatar} channel=${c} size=${22} /> ${c.title}</label>`)}</div>
      <div class="form-row">
        <div class="field"><span>Visibility</span><${Select} label="Visibility" value=${f.privacy} onChange=${(v) => setF({ ...f, privacy: v })}
          options=${[['public', 'Public'], ['unlisted', 'Unlisted'], ['private', 'Private']]} /></div>
        <div class="field"><span>The original live video</span><${Select} label="The original live video" value=${f.hide_original}
          options=${HIDE} onChange=${(v) => setF({ ...f, hide_original: v })} /></div>
      </div>
      ${earlier.length > 0 && html`<div class="field"><span>The earlier upload of this edit
          (${earlier.map((p) => html`<a href=${p.url} target="_blank" rel="noopener">${p.channel}</a> `)})</span>
        <${Select} label="The earlier upload" value=${f.hide_previous} options=${HIDE} onChange=${(v) => setF({ ...f, hide_previous: v })} />
        <p class="muted small">YouTube can't replace a video's footage, so this is uploaded as a new video.</p></div>`}
      ${error && html`<p class="error">${error}</p>`}
      <div class="modal-foot"><button type="button" class="btn ghost" onClick=${onClose}>Cancel</button>
        <button class="btn primary" disabled=${!f.channel_ids.length}>Publish</button></div>
    </form>
  </${Modal}>`;
}

/** The broadcast's edited video: rendering, ready to publish and download, or out of date with the editor. */
function EditedVideo({ edit, event, channels, changed, refresh }) {
  const [publishing, setPublishing] = useState(false);
  if (!edit) return html`<p class="muted small">Render to make the edited video. It's saved in the broadcast's folder,
    ready to download or publish. Rendering again replaces it; the original recording is never changed.</p>`;
  const working = ['queued', 'rendering', 'publishing'].includes(edit.status);
  const remove = async () => (await confirmAction({ title: 'Delete the edited video?', confirm: 'Delete', danger: true,
    message: 'The original recording stays. Anything already published on YouTube stays there.' }))
    && api('DELETE', `/api/edits/${edit.id}`).then(refresh).catch(toast);
  return html`<div class="version">
    <div class="version-head">
      <span class=${`dot ${edit.status === 'failed' ? 'bad' : edit.status === 'done' ? 'ok' : 'live'}`}></span>
      <b>${fmt(edit.duration)}</b><span class="muted small">rendered ${ago(edit.created_at)}</span>
      ${!working && html`<${IconButton} icon="trash" label="Delete the edited video" onClick=${remove} />`}
    </div>
    <div class="min0">
      <div class="muted small">${
        edit.status === 'queued' ? 'Waiting to render…'
        : edit.status === 'rendering' ? `Rendering ${Math.round(edit.progress * 100)}%`
        : edit.status === 'publishing' ? `Uploading to YouTube ${Math.round(edit.progress * 100)}%`
        : edit.status === 'failed' ? html`<span class="error">Failed: ${edit.error}</span>`
        : changed ? "Your changes since aren't in it: render again to update it."
        : 'Ready'}${edit.status === 'done' && edit.error ? html` <span class="error">${edit.error}</span>` : ''}</div>
      ${working && html`<div class="progress"><i style=${`width:${Math.round(edit.progress * 100)}%`}></i></div>`}
      ${edit.published.length > 0 && html`<div class="published">${edit.published.map((p) => html`<a href=${p.url} target="_blank"
        rel="noopener" class=${p.at < edit.created_at ? 'earlier' : ''}>${p.channel}${p.at < edit.created_at ? ' · earlier cut' : ''}${
        p.hidden ? ` · ${p.hidden}` : ''} ↗</a>`)}</div>`}
    </div>
    ${edit.status === 'done' && html`<div class="version-actions">
      ${channels.length > 0 && html`<button class="btn small" onClick=${() => setPublishing(true)}>Publish</button>`}
      <a class="btn ghost small" href=${`/api/recordings/file/${edit.output}`} download>Download</a>
      ${event.transcript && event.transcript.status === 'done' && html`<a class="btn ghost small" title="Captions timed to the edited video"
        href=${`/api/events/${event.id}/transcript.srt?edit=${edit.id}`} download>Captions</a>`}
      ${hubEnv.local && html`<${IconButton} icon="folder" label=${revealLabel()} onClick=${() => reveal({ file: edit.output })} />`}
    </div>`}
    ${publishing && html`<${PublishModal} edit=${edit} event=${event} channels=${channels} onClose=${() => { setPublishing(false); refresh(); }} />`}
  </div>`;
}

export function EditorPage({ eventId }) {
  const [data, setData] = useState(null);
  const [source, setSource] = useState(null);
  const [spec, setSpec] = useState(DEFAULT_SPEC);
  const [saved, setSaved] = useState(null);      // what's kept on the hub: {source, spec} of the draft or the edit
  const [time, setTime] = useState(0);
  const [cutStart, setCutStart] = useState(null);
  const [previewing, setPreviewing] = useState(false);
  const [error, setError] = useState('');
  const video = useRef();
  const strip = useRef();
  const drag = useRef(null);

  const load = async () => {
    const d = await api('GET', `/api/events/${eventId}/editor`);
    setData(d);
    if (!source && d.copies.length) {
      // Pick up where we left off: the draft, else the rendered edit, else the whole original
      const base = d.draft || (d.edit && { source: d.edit.source, spec: d.edit.spec });
      const copy = (base && d.copies.find((c) => c.path === base.source))
        || d.copies.find((c) => c.source === 'spoke') || d.copies[0];
      const start = base && copy.path === base.source ? { ...DEFAULT_SPEC, ...base.spec } : DEFAULT_SPEC;
      setSource(copy); setSpec(start); setSaved({ source: copy.path, spec: start });
    }
  };
  usePoll(load, 3000, [eventId]);

  const current = source && { source: source.path, spec };
  const dirty = !!(saved && current && !same(current, saved));
  const saveDraft = async () => {
    await api('PUT', `/api/events/${eventId}/edit-draft`, current);
    setSaved(current);
  };
  useLeaveGuard(dirty, saveDraft, "Your changes to this edit aren't saved.");

  const duration = source ? source.duration : 0;
  const out = spec.out ?? duration;
  const keep = source ? keepRanges(spec, duration) : [];
  const finalLength = keep.reduce((t, [a, b]) => t + b - a, 0);
  const seek = (t) => { video.current.currentTime = Math.max(0, Math.min(duration, t)); };
  const set = (patch) => setSpec((s) => ({ ...s, ...patch }));
  const markCut = () => {
    const t = video.current.currentTime;
    if (cutStart === null) return setCutStart(t);
    const [a, b] = [Math.min(cutStart, t), Math.max(cutStart, t)];
    if (b - a >= 0.5) set({ cuts: [...spec.cuts, [a, b]] });
    setCutStart(null);
  };

  // Keyboard: space play/pause, I/O start/end, C cut, arrows nudge (not while typing, choosing or in a dialog)
  useEffect(() => {
    const key = (e) => {
      if (['INPUT', 'TEXTAREA'].includes(e.target.tagName) || e.target.closest('.select-wrap, .modal')
        || document.querySelector('.backdrop') || !video.current) return;
      const v = video.current;
      if (e.key === ' ') { e.preventDefault(); v.paused ? v.play() : v.pause(); }
      else if (e.key === 'i') set({ in: v.currentTime });
      else if (e.key === 'o') set({ out: v.currentTime });
      else if (e.key === 'c') markCut();
      else if (e.key === 'ArrowLeft') seek(v.currentTime - (e.shiftKey ? 5 : 1));
      else if (e.key === 'ArrowRight') seek(v.currentTime + (e.shiftKey ? 5 : 1));
    };
    addEventListener('keydown', key);
    return () => removeEventListener('keydown', key);
  });

  // Preview: play only what's kept, and show the fades
  const onTime = () => {
    const v = video.current;
    setTime(v.currentTime);
    if (!previewing || v.paused) return;
    const t = v.currentTime;
    const inCut = keep.findIndex(([a, b]) => t >= a - 0.05 && t < b);
    if (inCut === -1) {
      const next = keep.find(([a]) => a > t);
      if (next) seek(next[0]); else { v.pause(); setPreviewing(false); }
    }
  };
  const fadeOpacity = () => {
    if (!previewing || !keep.length) return 0;
    let elapsed = 0;
    for (const [i, [a, b]] of keep.entries()) {
      if (time >= a && time < b) {
        const pos = elapsed + time - a;
        let o = 0;
        if (spec.fade_in && pos < spec.fade_in) o = 1 - pos / spec.fade_in;
        if (spec.fade_out && finalLength - pos < spec.fade_out) o = Math.max(o, 1 - (finalLength - pos) / spec.fade_out);
        if (spec.dip && keep.length > 1) {
          if (i > 0 && time - a < spec.dip) o = Math.max(o, 1 - (time - a) / spec.dip);
          if (i < keep.length - 1 && b - time < spec.dip) o = Math.max(o, 1 - (b - time) / spec.dip);
        }
        return o;
      }
      elapsed += b - a;
    }
    return 0;
  };
  const startPreview = () => { setPreviewing(true); seek(keep.length ? keep[0][0] : 0); video.current.play(); };

  // Timeline dragging for the start/end handles; clicking seeks
  const toTime = (clientX) => {
    const r = strip.current.getBoundingClientRect();
    return Math.max(0, Math.min(1, (clientX - r.left) / r.width)) * duration;
  };
  const onPointerDown = (e) => {
    const handle = e.target.dataset.handle;
    if (handle) { drag.current = handle; e.target.setPointerCapture(e.pointerId); } else seek(toTime(e.clientX));
  };
  const onPointerMove = (e) => {
    if (!drag.current) return;
    const t = toTime(e.clientX);
    if (drag.current === 'in') set({ in: Math.min(t, out - 1) }); else set({ out: Math.max(t, spec.in + 1) });
    seek(t);
  };
  const pct = (t) => `${(t / (duration || 1)) * 100}%`;

  const render = async () => {
    setError('');
    const edit = data.edit;
    if (edit && edit.published.some((p) => !p.hidden) && !(await confirmAction({ title: 'Render a new edited video?',
      message: 'It replaces the edited video on the hub. What you published to YouTube stays until you publish again.',
      confirm: 'Render' }))) return;
    try {
      await api('POST', `/api/events/${eventId}/edits`, { source: source.path, ...spec });
      setSaved(current);
      load();
    } catch (err) { setError(err.message); }
  };
  const startOver = async () => {
    if (!(await confirmAction({ title: 'Start over from the original?', confirm: 'Start over',
      message: 'Clears the start, end, cuts and fades in the editor. The edited video stays until you render again.' }))) return;
    setSpec(DEFAULT_SPEC);
    setCutStart(null);
  };

  if (!data) return null;
  const event = data.event;
  if (!data.copies.length) return html`<div>
    <a class="crumb" href="#/schedule"><${Icon} name="back" size=${15} /> Schedule</a>
    <div class="empty">${event.recordings_removed_at
      ? html`<h2>The recordings were deleted</h2><p class="muted">They were older than Settings · Old recordings keeps.
          ${data.edit ? 'The edited video is still in the Download menu and the broadcast\'s folder.' : ''}</p>`
      : html`<h2>No recording to edit yet</h2><p class="muted">The recording appears here once it has finished uploading.</p>`}</div></div>`;
  if (!source) return null;
  const rendered = data.edit && { source: data.edit.source, spec: { ...DEFAULT_SPEC, ...data.edit.spec } };
  const busy = data.edit && ['queued', 'rendering', 'publishing'].includes(data.edit.status);
  const changeSource = (path) => { setSource(data.copies.find((c) => c.path === path)); setSpec(DEFAULT_SPEC); setCutStart(null); };
  return html`<div>
    <a class="crumb" href="#/schedule"><${Icon} name="back" size=${15} /> Schedule</a>
    <div class="page-head"><div><h1 class="ellipsis">${event.title}</h1>
      <p class="muted">Trim the start and end, cut out sections and add fades. Edits are always made from the original
        recording, and rendering replaces the edited video.</p></div>
      ${data.copies.length > 1 && html`<${Select} className="source-pick" label="Recording" value=${source.path} onChange=${changeSource}
        options=${data.copies.map((c) => [c.path, `${c.source === 'hub' ? 'Hub recording' : 'Spoke recording'} · ${fmt(c.duration)}`, bytes(c.size)])} />`}
    </div>
    <div class="editor-layout">
      <div class="panel flush">
        <div class="preview large">
          <video ref=${video} src=${`/api/recordings/file/${source.path}`} preload="metadata"
            onTimeUpdate=${onTime} onClick=${(e) => (e.target.paused ? e.target.play() : e.target.pause())}></video>
          <div class="fade-overlay" style=${`opacity:${fadeOpacity()}`}></div>
          ${previewing && html`<span class="badge chip ember">Previewing edit</span>`}
        </div>
        <div class="timeline" ref=${strip} onPointerDown=${onPointerDown} onPointerMove=${onPointerMove}
          onPointerUp=${() => { drag.current = null; }} style=${`background-image:url(/api/recordings/filmstrip/${source.path})`}>
          <div class="trimmed" style=${`left:0;width:${pct(spec.in)}`}></div>
          <div class="trimmed" style=${`left:${pct(out)};right:0`}></div>
          ${spec.cuts.map(([a, b]) => html`<div class="cut" style=${`left:${pct(a)};width:${pct(b - a)}`}></div>`)}
          ${cutStart !== null && html`<div class="cut pending" style=${`left:${pct(Math.min(cutStart, time))};width:${pct(Math.abs(time - cutStart))}`}></div>`}
          <div class="handle" data-handle="in" style=${`left:${pct(spec.in)}`} title="Start"></div>
          <div class="handle" data-handle="out" style=${`left:${pct(out)}`} title="End"></div>
          <div class="playhead" style=${`left:${pct(time)}`}></div>
        </div>
        <div class="controls wrap">
          <span class="mono small">${fmt(time)} / ${fmt(duration)}</span>
          <button class="btn small" onClick=${() => set({ in: video.current.currentTime })} title="Shortcut: I">Start here</button>
          <button class="btn small" onClick=${() => set({ out: video.current.currentTime })} title="Shortcut: O">End here</button>
          <button class=${`btn small ${cutStart !== null ? 'primary' : ''}`} onClick=${markCut} title="Shortcut: C">
            ${cutStart === null ? 'Cut from here' : `Cut to here (from ${fmt(cutStart)})`}</button>
          <button class="btn ghost small" style="margin-left:auto" onClick=${() => (previewing ? (video.current.pause(), setPreviewing(false)) : startPreview())}>
            <${Icon} name=${previewing ? 'stop' : 'play'} size=${14} /> ${previewing ? 'Stop preview' : 'Preview edit'}</button>
        </div>
      </div>
      <div class="side">
        <div class="panel form">
          <div class="row-tight"><h3 class="grow" style="margin:0">Edit</h3>
            ${dirty ? html`<span class="chip ember">Unsaved</span>` : data.draft ? html`<span class="chip">Draft saved</span>` : ''}</div>
          <div class="kv small">
            <span class="muted">Starts at</span><span class="mono">${fmt(spec.in)}</span>
            <span class="muted">Ends at</span><span class="mono">${fmt(out)}</span>
            <span class="muted">Final length</span><span class="mono"><b>${fmt(finalLength)}</b></span>
          </div>
          ${spec.cuts.length > 0 && html`<div><span class="eyebrow">Cut out</span>
            ${spec.cuts.map(([a, b], i) => html`<div class="row"><span class="mono small grow">${fmt(a)} – ${fmt(b)}</span>
              <${IconButton} icon="trash" label="Keep this section" onClick=${() => set({ cuts: spec.cuts.filter((_, j) => j !== i) })} /></div>`)}</div>`}
          <div class="form-row">
            <div class="field"><span>Fade in</span><${Select} label="Fade in" value=${spec.fade_in} options=${FADES}
              onChange=${(v) => set({ fade_in: Number(v) })} /></div>
            <div class="field"><span>Fade out</span><${Select} label="Fade out" value=${spec.fade_out} options=${FADES}
              onChange=${(v) => set({ fade_out: Number(v) })} /></div>
          </div>
          <div class="field"><span>At each cut</span><${Select} label="At each cut" value=${spec.dip} options=${DIPS}
            onChange=${(v) => set({ dip: Number(v) })} /></div>
          ${error && html`<p class="error">${error}</p>`}
          <button class="btn primary" disabled=${busy} onClick=${render}>${data.edit ? 'Render again' : 'Render'} · ${fmt(finalLength)}</button>
          <div class="row-tight">
            <button class="btn ghost small grow" disabled=${!dirty} onClick=${() => saveDraft().catch(toast)}>Save draft</button>
            <button class="btn ghost small grow" disabled=${same(spec, DEFAULT_SPEC)} onClick=${startOver}>Start over</button>
          </div>
          <p class="muted small">Space plays, I and O set the start and end, C marks a cut, ←/→ nudge (Shift for 5 s).</p>
        </div>
        <div class="panel">
          <h3>Edited video</h3>
          <${EditedVideo} edit=${data.edit} event=${event} channels=${data.channels} refresh=${load}
            changed=${!!(rendered && !same(current, rendered))} />
        </div>
      </div>
    </div>
  </div>`;
}
