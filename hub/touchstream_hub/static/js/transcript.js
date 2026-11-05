// Transcript of a broadcast (hub only): Whisper speech-to-text, correct it, download captions
import { html, useState, useEffect, useRef } from '../vendor/preact-htm.module.js';
import { api, usePoll } from './lib.js';
import { Icon } from './ui.js';
import { confirmAction, useLeaveGuard } from './dialogs.js';
import { fmt } from './editor.js';

export function TranscriptPage({ eventId }) {
  const [data, setData] = useState(null);
  const [segments, setSegments] = useState(null);   // local copy while editing
  const [dirty, setDirty] = useState(false);
  const [time, setTime] = useState(0);
  const [query, setQuery] = useState('');
  const [msg, setMsg] = useState('');
  const video = useRef();
  const list = useRef();
  useLeaveGuard(dirty, () => api('PUT', `/api/events/${eventId}/transcript`, { segments }),
    "Your corrections to the transcript aren't saved.", 'Save corrections');

  const load = async () => {
    const d = await api('GET', `/api/events/${eventId}/transcript`);
    setData(d);
    if (d.transcript && d.transcript.status === 'done' && !dirty) setSegments(d.transcript.segments);
  };
  const t = data && data.transcript;
  const working = t && ['queued', 'running'].includes(t.status);
  usePoll(load, working || !data ? 3000 : 30000, [eventId, working, dirty]);

  // Keep the line being spoken in view while playing
  const current = segments ? segments.findIndex((s) => time >= s.start && time < s.end) : -1;
  useEffect(() => {
    const el = list.current && list.current.querySelector('.line.now');
    if (el && video.current && !video.current.paused) el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }, [current]);

  if (!data) return null;
  const event = data.event;
  const start = async () => {
    setMsg('');
    try { await api('POST', `/api/events/${eventId}/transcript`, {}); setSegments(null); setDirty(false); load(); }
    catch (err) { setMsg(err.message); }
  };
  const edit = (i, text) => { setSegments(segments.map((s, j) => (j === i ? { ...s, text } : s))); setDirty(true); };
  const save = async () => { await api('PUT', `/api/events/${eventId}/transcript`, { segments }); setDirty(false); setMsg('Saved'); load(); };
  const seek = (s) => { video.current.currentTime = s.start; video.current.play(); };
  const shown = segments ? segments.map((s, i) => [s, i]).filter(([s]) => !query || s.text.toLowerCase().includes(query.toLowerCase())) : [];

  return html`<div>
    <a class="crumb" href=${`#/spokes/${event.spoke_id}`}><${Icon} name="back" size=${15} /> ${event.title}</a>
    <div class="page-head"><div><h1>Transcript</h1>
      <p class="muted">Made on this hub with Whisper: nothing leaves your server. Correct anything it misheard, then
        download captions for YouTube Studio or any player.</p></div>
      ${t && t.status === 'done' && html`<div class="actions">
        <a class="btn small" href=${`#/edit/${eventId}/shorts`}>Make a short</a>
        ${['srt', 'vtt', 'txt'].map((f) => html`<a class="btn ghost small" href=${`/api/events/${eventId}/transcript.${f}`} download>.${f}</a>`)}
      </div>`}
    </div>
    ${!data.options.available ? html`<div class="empty small-empty"><p class="muted">Transcription isn't installed on this hub.
        The Docker image includes it; for other installs add the <span class="mono">transcribe</span> extra.</p></div>`
    : !t || t.status === 'failed' ? html`<div class="empty small-empty">
        ${t && html`<p class="error">Last try failed: ${t.error}</p>`}
        <p class="muted">${data.copies.length ? "Transcribe this broadcast's recording." : "The recording isn't ready yet."}</p>
        <button class="btn primary" disabled=${!data.copies.length} onClick=${start}>Transcribe</button>
        ${msg && html`<p class="error">${msg}</p>`}</div>`
    : working ? html`<div class="panel">
        <b>${t.status === 'queued' ? 'Waiting its turn…' : `Transcribing… ${Math.round(t.progress * 100)}%`}</b>
        <p class="muted small">With the ${t.model || data.options.model} model. The first run downloads the model.</p>
        <div class="progress"><i style=${`width:${Math.round(t.progress * 100)}%`}></i></div></div>`
    : html`<div class="editor-layout">
        <div class="panel flush">
          <div class="preview large"><video ref=${video} src=${`/api/recordings/file/${t.source}`} preload="metadata" controls
            onTimeUpdate=${(e) => setTime(e.target.currentTime)}></video></div>
          ${current >= 0 && html`<p class="caption-now">${segments[current].text}</p>`}
        </div>
        <div class="side">
          <div class="panel transcript">
            <div class="row" style="border:0;padding-top:0">
              <input type="text" placeholder="Search the transcript" value=${query} onInput=${(e) => setQuery(e.target.value)} />
            </div>
            ${segments.length === 0 && html`<p class="muted small" style="padding:12px 0">No speech found in this recording.</p>`}
            <div class="lines" ref=${list}>${shown.map(([s, i]) => html`<div class=${`line ${i === current ? 'now' : ''}`}>
              <button class="stamp mono" onClick=${() => seek(s)}>${fmt(s.start)}</button>
              <textarea rows=${Math.max(1, Math.ceil(s.text.length / 30))} value=${s.text}
                onInput=${(e) => edit(i, e.target.value)}></textarea></div>`)}</div>
            <div class="row" style="border:0">
              <span class="muted small grow">${segments.length} lines · ${t.language || ''} · ${t.model}${t.edited ? ' · edited' : ''}</span>
              ${msg && html`<span class="muted small">${msg}</span>`}
              <button class="btn ghost small" onClick=${async () => (!t.edited || await confirmAction({ title: 'Transcribe again?',
                message: 'Your corrections will be replaced.', confirm: 'Transcribe again', danger: true })) && start()}>Redo</button>
              <button class="btn primary small" disabled=${!dirty} onClick=${save}>Save</button>
            </div>
          </div>
        </div>
      </div>`}
  </div>`;
}
