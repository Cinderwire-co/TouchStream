// Post-broadcast editor (hub only): a linear editor for the broadcast's recording, and its shorts
//
// The edit is tracks (edit-model.js): video and its linked sound, music, titles, one color adjustment
// across everything and a brand's watermark. Clips sit where you put them; deleting one leaves a gap
// until you close it. The hub renders it all from the originals (editor.py). Properties are on the
// left, the viewer beside them, the timeline underneath (timeline.js); the Shorts tab makes vertical
// shorts of moments of the same recording.
import { html, useState, useEffect, useRef } from '../vendor/preact-htm.module.js';
import { api, ago, bytes, hubEnv, reveal, revealLabel, usePoll } from './lib.js';
import { Avatar, Icon, IconButton, Modal } from './ui.js';
import { Select } from './fields.js';
import { ask, confirmAction, toast, useLeaveGuard } from './dialogs.js';
import * as M from './edit-model.js';
import { Timeline } from './timeline.js';
import { SHORT_BUSY, SHORT_LOOK, ShortsTab, widen } from './shorts.js';
import { UploadButton, fmtLength, thumbOf } from './brands.js';

export const fmt = (s) => {
  s = Math.max(0, s || 0);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = Math.floor(s % 60);
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}` : `${m}:${String(sec).padStart(2, '0')}`;
};
const fine = (s) => `${fmt(s)}.${Math.floor(((s || 0) % 1) * 10)}`;

const COLOR_LABELS = [['brightness', 'Brightness'], ['contrast', 'Contrast'], ['saturation', 'Saturation'], ['warmth', 'Warmth']];
const PRESETS = [['Natural', M.NO_COLOR], ['Warm', { warmth: 35, saturation: 10 }], ['Cool', { warmth: -30 }],
  ['Vivid', { saturation: 35, contrast: 15 }], ['Black & white', { saturation: -100, contrast: 12 }],
  ['Faded', { contrast: -25, saturation: -25, brightness: 8 }]];
const KINDS = [['cut', 'Cut'], ['dissolve', 'Dissolve'], ['dip_black', 'Dip to black'], ['dip_white', 'Dip to white'],
  ['wipe', 'Wipe'], ['slide', 'Slide'], ['circle', 'Circle']];
const LENGTHS = [[0.25, '¼ second'], [0.5, '½ second'], [1, '1 second'], [1.5, '1½ seconds'], [2, '2 seconds']];
const TITLE_STYLES = [['lower_third', 'Lower third'], ['title', 'Title'], ['caption', 'Caption'], ['corner', 'Corner tag']];
const BUSY = ['queued', 'rendering', 'publishing'];
const MAX_ZOOM = 200;    // pixels per second
const full = (c) => ({ ...M.NO_COLOR, ...c });
const colorName = (c) => {
  const p = PRESETS.find(([, v]) => JSON.stringify(full(v)) === JSON.stringify(full(c)));
  return p ? (p[0] === 'Natural' ? 'Color: as recorded' : `Color: ${p[0]}`) : 'Color: adjusted';
};
/** A rough look at the color adjustment in the browser (the render's is exact). */
const cssFilter = (c) => [`brightness(${1 + c.brightness / 160})`, `contrast(${1 + c.contrast / 200})`,
  `saturate(${Math.max(0, 1 + c.saturation / 100)})`, c.warmth > 0 ? `sepia(${c.warmth / 250})` : '',
  c.warmth < 0 ? `hue-rotate(${c.warmth / 6}deg)` : ''].join(' ');
const titleImage = (t) => `/api/titles/preview.png?${new URLSearchParams({ style: t.style, text: t.text, sub: t.sub || '' })}`;

const HIDE = [['private', 'Make it private'], ['unlisted', 'Make it unlisted'], ['keep', 'Leave it as it is']];

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


// ---- Playing the timeline: one clock drives the picture, the sound and the music, across gaps and media ----
function usePlayer(editRef, recordingRef, mediaById) {
  // source: playing a moment of the recording itself ({at, end}), for a short, whatever the timeline has
  const clock = useRef({ t: 0, playing: false, last: 0, stop: null, source: null });
  const picture = useRef(), still = useRef(), sound = useRef(), music = useRef();
  const listeners = useRef(new Set());
  const urlOf = (c) => (c.media ? `/api/media/${c.media}/file` : recordingRef.current);
  const follow = (el, url, want, hard) => {
    if (!el) return;
    if (el.dataset.src !== url) { el.src = url; el.dataset.src = url; hard = true; }
    if (hard || Math.abs(el.currentTime - want) > 0.3) { try { el.currentTime = want; } catch (e) { /* not loaded yet */ } }
    if (clock.current.playing && el.paused) el.play().catch(() => {});
    if (!clock.current.playing && !el.paused) el.pause();
  };
  const quiet = (el) => el && !el.paused && el.pause();
  const sync = (hard) => {
    const e = editRef.current, t = clock.current.t, src = clock.current.source;
    if (src) {
      follow(picture.current, recordingRef.current, src.at, hard);
      follow(sound.current, recordingRef.current, src.at, hard);
      quiet(music.current);
      listeners.current.forEach((fn) => fn());
      return;
    }
    const v = M.clipAt(e.video, t), a = M.clipAt(e.audio, t), m = e.music.find((x) => t >= x.at && t < M.clipEnd(x));
    const vm = v && v.media ? mediaById(v.media) : null;
    if (v && !(vm && vm.kind === 'image')) follow(picture.current, urlOf(v), v.start + t - v.at, hard); else quiet(picture.current);
    if (a) follow(sound.current, urlOf(a), a.start + t - a.at, hard); else quiet(sound.current);
    if (m) { follow(music.current, urlOf(m), m.start + t - m.at, hard); if (music.current) music.current.volume = Math.min(1, m.volume); }
    else quiet(music.current);
    listeners.current.forEach((fn) => fn());
  };
  useEffect(() => {
    let frame;
    const loop = (now) => {
      const c = clock.current;
      if (c.playing && c.source) {
        c.source.at += (now - c.last) / 1000;
        c.last = now;
        const t = M.timelineAt(editRef.current, c.source.at);   // the playhead follows where it can
        if (t !== null) c.t = t;
        if (c.source.at >= c.source.end) { c.playing = false; c.source = null; }
        sync(false);
      } else if (c.playing) {
        c.t += (now - c.last) / 1000;
        c.last = now;
        const end = c.stop ?? M.total(editRef.current);
        if (c.t >= end) { c.t = end; c.playing = false; c.stop = null; }
        sync(false);
      }
      frame = requestAnimationFrame(loop);
    };
    frame = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(frame);
  }, []);
  const play = (stop = null) => {
    const c = clock.current;
    c.source = null;
    if (c.t >= M.total(editRef.current) - 0.05) c.t = 0;
    Object.assign(c, { playing: true, last: performance.now(), stop });
    sync(true);
  };
  const pause = () => { Object.assign(clock.current, { playing: false, stop: null, source: null }); sync(false); };
  return {
    clock, picture, still, sound, music, play, pause,
    toggle: () => (clock.current.playing ? pause() : play()),
    seek: (t) => { clock.current.source = null; clock.current.t = Math.max(0, Math.min(M.total(editRef.current), t)); sync(true); },
    playSource: (a, b) => {
      Object.assign(clock.current, { source: { at: a, end: b }, playing: true, last: performance.now(), stop: null });
      sync(true);
    },
    refresh: () => sync(false),     // the edit changed under the playhead
    listen: (fn) => { listeners.current.add(fn); return () => listeners.current.delete(fn); },
  };
}

/** The picture: whatever's on the video track at the playhead, with the color, titles, watermark and fades. */
function Viewer({ player, edit, mediaById, brand, guide }) {
  const [, setFrame] = useState(0);
  useEffect(() => player.listen(() => setFrame((n) => n + 1)), []);
  const t = player.clock.current.t, total = M.total(edit), src = player.clock.current.source;
  // Playing a moment from the recording (for a short) shows just the recording
  const v = src ? { media: null } : M.clipAt(edit.video, t), vm = v && v.media ? mediaById(v.media) : null;
  const showing = src ? [] : edit.titles.filter((x) => t >= x.start && t < x.end);
  const fromRecording = v && !v.media && !src;
  const wm = brand && brand.watermark && mediaById(brand.watermark);
  // Fades at the ends, and dips at cuts, darken the picture while it plays
  let shade = 0, white = false;
  if (player.clock.current.playing && total) {
    if (edit.fade_in && t < edit.fade_in) shade = 1 - t / edit.fade_in;
    if (edit.fade_out && total - t < edit.fade_out) shade = Math.max(shade, 1 - (total - t) / edit.fade_out);
    for (const cut of M.cuts(edit)) {
      const tr = cut.transition;
      if (tr && (tr.type === 'dip_black' || tr.type === 'dip_white') && Math.abs(t - cut.at) < tr.duration / 2) {
        const d = 1 - Math.abs(t - cut.at) / (tr.duration / 2);
        if (d > shade) { shade = d; white = tr.type === 'dip_white'; }
      }
    }
  }
  const corner = wm && { top_left: 'top:3%;left:3%', top_right: 'top:3%;right:3%', bottom_left: 'bottom:3%;left:3%',
    bottom_right: 'bottom:3%;right:3%' }[brand.watermark_corner];
  return html`<div class="preview large" onClick=${() => player.toggle()}>
    <video ref=${player.picture} muted playsinline preload="auto" class=${v && !(vm && vm.kind === 'image') ? '' : 'off'}
      style=${fromRecording ? `filter:${cssFilter(edit.color)}` : ''}></video>
    <img ref=${player.still} class=${`still ${vm && vm.kind === 'image' ? '' : 'off'}`} alt=""
      src=${vm && vm.kind === 'image' ? `/api/media/${vm.id}/file` : ''} />
    <audio ref=${player.sound} preload="auto"></audio><audio ref=${player.music} preload="auto"></audio>
    ${fromRecording && wm && html`<img class="watermark" alt="" src=${`/api/media/${wm.id}/file`}
      style=${`${corner};width:${brand.watermark_size}%;opacity:${brand.watermark_opacity / 100}`} />`}
    ${showing.map((x) => html`<img class="title-overlay" key=${x.id} src=${titleImage(x)} alt="" />`)}
    <div class="fade-overlay" style=${`opacity:${shade};background:${white ? '#fff' : '#000'}`}></div>
    ${guide && html`<${Guide} guide=${guide} at=${src ? src.at : M.recordingAt(edit, t)} />`}
    ${src && html`<span class="badge chip ember right">From the recording</span>`}
    ${!v && !src && total > 0 && t < total && html`<span class="badge chip">Gap</span>`}
  </div>`;
}

/** The short over the picture: its window (following the speaker, or the whole picture where nobody's in it),
 * and its captions word by word, drawn by the hub exactly as the short shows them. */
function Guide({ guide, at }) {
  const { plan, moment, framing, captions } = guide;
  const inMoment = moment && at !== null && at >= moment[0] - 0.05 && at <= moment[1] + 0.05;
  let left = 34.18, width = 31.64, idle = true, badge = '', whole = framing === 'fit';
  if (framing === 'fill' && plan && plan.loading) badge = 'Finding the speaker…';
  if (whole) { left = 0; width = 100; idle = !inMoment; }
  else if (inMoment && plan && !plan.loading && at >= plan.start && at <= plan.end) {
    const t = at - plan.start;
    if (plan.whole.some(([a, b]) => t >= a && t < b)) { left = 0; width = 100; whole = true; badge = 'Whole picture: nobody in it'; }
    else {
      const k = Math.min(plan.path.length - 1, Math.max(0, Math.round(t * 10)));
      const center = plan.path[k] ? plan.path[k][1] : 0.5;
      left = (center - plan.width / 2) * 100; width = plan.width * 100;
    }
    idle = false;
  }
  // The caption showing now, and the word being said
  let caption = null;
  if (inMoment && captions && captions.pages) {
    const page = captions.pages.find((p, i) => at >= p.start - 0.05 && (at < p.end + 0.25 || (captions.pages[i + 1] && at < captions.pages[i + 1].start)));
    if (page) {
      const active = page.words.reduce((n, w, i) => (w.start <= at ? i : n), 0);
      const src = `/api/shorts/caption.png?${new URLSearchParams({ style: captions.style[0], accent: captions.accent,
        words: page.words.map((w) => w.text).join('\n'), active })}`;
      // In the window, where the style sits; over the whole picture, along its bottom (the short puts it below)
      const place = whole ? 'left:20%;width:60%;bottom:3%' : `left:${left}%;width:${width}%;top:${(captions.style[2] - captions.style[3] / 2) * 100}%`;
      caption = html`<img class="cap" alt="" src=${src} style=${place} />`;
    }
  }
  return html`<div class="vertical-guide">
    ${!whole && html`<b style=${`left:0;width:${left}%`}></b><b style=${`left:${left + width}%;right:0`}></b>`}
    <i class=${idle ? 'idle' : ''} style=${`left:${left}%;width:${width}%`}></i>
    ${caption}
    ${badge && html`<span class="badge chip">${badge}</span>`}</div>`;
}

function Timecode({ player, total }) {
  const [, setFrame] = useState(0);
  useEffect(() => player.listen(() => setFrame((n) => n + 1)), []);
  return html`<span class="mono timecode"><b>${fine(player.clock.current.t)}</b><span class="muted"> / ${fine(total)}</span></span>`;
}

function PlayButton({ player }) {
  const [, setFrame] = useState(0);
  useEffect(() => player.listen(() => setFrame((n) => n + 1)), []);
  return html`<button class="btn primary small play" title="Play / pause (Space)" onClick=${() => player.toggle()}>
    <${Icon} name=${player.clock.current.playing ? 'pause' : 'play'} size=${16} /></button>`;
}

/** Properties: whatever's selected on the timeline, or the edit as a whole. */
function Properties({ edit, selected, change, live, remember, select, mediaById, brands, setBrand, ops }) {
  const total = M.total(edit);
  if (!selected) {
    return html`<div class="props-body">
      <div class="kv small">
        <span class="muted">Length</span><span class="mono"><b>${fmt(total)}</b></span>
        <span class="muted">Clips</span><span class="mono">${edit.video.length} video · ${edit.audio.length} sound</span>
        <span class="muted">Fades</span><span class="mono">in ${edit.fade_in} s · out ${edit.fade_out} s</span>
      </div>
      <div class="field"><span>Brand</span><${Select} label="Brand" value=${edit.brand ? String(edit.brand) : ''}
        options=${[['', 'None'], ...brands.map((b) => [String(b.id), b.name])]} onChange=${(v) => setBrand(v ? Number(v) : null)} />
        <small class="muted">${brands.length ? 'Its intro and outro go on the timeline; its watermark goes over the recording.'
          : html`Make one in${' '}<a href="#/settings/brands">Settings · Brands</a>: an intro, an outro and a watermark.`}</small></div>
      <p class="muted small">Click a clip, a cut, a title or the Color track to change it. Drag the round handles at the ends
        of the video track for fades. B is the cut tool; T adds a title.</p>
    </div>`;
  }
  if (selected.kind === 'adjust') {
    const c = full(edit.color);
    const recolor = (patch, record = true) => (record ? change : live)({ ...edit, color: { ...c, ...patch } });
    return html`<div class="props-body form">
      <h3>Color</h3>
      <p class="muted small">One adjustment over the whole recording (intros, outros and slides keep their own colors).</p>
      <div class="presets">${PRESETS.map(([label, p]) => html`<button class=${`chip-btn ${JSON.stringify(full(p)) === JSON.stringify(c) ? 'on' : ''}`}
        onClick=${() => recolor(full(p))}>${label}</button>`)}</div>
      ${COLOR_LABELS.map(([k, label]) => html`<label class="slider"><span>${label}</span>
        <input type="range" min="-100" max="100" step="1" value=${c[k]} onPointerDown=${remember} onKeyDown=${remember}
          onInput=${(e) => recolor({ [k]: Number(e.target.value) }, false)} />
        <b class="mono small">${c[k] > 0 ? '+' : ''}${c[k]}</b></label>`)}
      <p class="muted small">The preview's color is close; the render is exact.</p>
    </div>`;
  }
  if (selected.kind === 'cut') {
    const clip = edit.video.find((c) => c.id === selected.id);
    if (!clip) return null;
    const t = clip.transition || { type: 'cut', duration: 0.5 };
    const set = (patch, every) => {
      const value = { ...t, duration: t.duration || 0.5, ...patch };
      const ids = every ? M.cuts(edit).map((k) => k.id) : [clip.id];
      change({ ...edit, video: edit.video.map((c) => (ids.includes(c.id) ? { ...c, transition: value.type === 'cut' ? null : value } : c)) });
    };
    return html`<div class="props-body form">
      <h3>Transition</h3>
      <div class="presets">${KINDS.map(([v, label]) => html`<button class=${`chip-btn ${t.type === v ? 'on' : ''}`}
        onClick=${() => set({ type: v })}>${label}</button>`)}</div>
      ${t.type !== 'cut' && html`<div class="field"><span>Length</span><${Select} label="Length" value=${t.duration || 0.5}
        options=${LENGTHS} onChange=${(v) => set({ duration: Number(v) })} /></div>`}
      <button class="btn ghost small" onClick=${() => set({}, true)}>Use on every cut</button>
      <p class="muted small">It plays across the cut, so nothing moves.${t.type === 'dip_black' || t.type === 'dip_white'
        ? '' : ' The preview shows a plain cut; the render has the transition.'}</p>
    </div>`;
  }
  if (selected.kind === 'gap') {
    return html`<div class="props-body form">
      <h3>Gap · ${fine(selected.to - selected.from)}</h3>
      <p class="muted small">Black and silent. Close it to move everything after it back.</p>
      <button class="btn small" onClick=${() => { change(M.closeGap(edit, selected.from, selected.to)); select(null); }}>Close the gap (Delete)</button>
    </div>`;
  }
  if (selected.kind === 'clip') {
    const c = edit[selected.track].find((x) => x.id === selected.id);
    if (!c) return null;
    const m = c.media ? mediaById(c.media) : null, p = M.partner(edit, selected.track, c);
    return html`<div class="props-body form">
      <h3>${m ? m.name : selected.track === 'video' ? 'Video clip' : 'Sound clip'}</h3>
      <div class="kv small">
        <span class="muted">On the timeline</span><span class="mono">${fine(c.at)} – ${fine(M.clipEnd(c))}</span>
        <span class="muted">${m ? 'From the file' : 'From the recording'}</span><span class="mono">${m && m.kind === 'image' ? 'a still' : `${fine(c.start)} – ${fine(c.end)}`}</span>
        <span class="muted">Sound</span><span>${p ? 'Linked' : selected.track === 'video' ? (edit.audio.some((a) => a.at < M.clipEnd(c) && M.clipEnd(a) > c.at) ? 'Unlinked' : 'None') : 'Unlinked from its picture'}</span>
      </div>
      <div class="row-tight">
        ${p && html`<button class="btn ghost small grow" onClick=${() => change(M.unlink(edit, [c.id]))}>Unlink sound</button>`}
        <button class="btn ghost small grow" onClick=${() => { change(M.lift(edit, selected.ids)); select(null); }}>Delete (leave a gap)</button>
        <button class="btn ghost small grow" onClick=${() => { change(M.rippleDelete(edit, selected.ids)); select(null); }}>Delete and close</button>
      </div>
      <p class="muted small">Drag it to move it; drag its edges to trim. Hold Option to change just the picture or just the sound.</p>
    </div>`;
  }
  if (selected.kind === 'music') {
    const it = edit.music.find((x) => x.id === selected.id);
    if (!it) return null;
    const set = (patch, record = true) => (record ? change : live)({ ...edit, music: edit.music.map((x) => (x.id === it.id ? { ...x, ...patch } : x)) });
    return html`<div class="props-body form">
      <h3>${(mediaById(it.media) || {}).name || 'Music'}</h3>
      <label class="slider"><span>Volume</span><input type="range" min="0" max="100" value=${Math.round(it.volume * 100)}
        onPointerDown=${remember} onInput=${(e) => set({ volume: Number(e.target.value) / 100 }, false)} />
        <b class="mono small">${Math.round(it.volume * 100)}%</b></label>
      <p class="muted small">It fades in and out on its own. Around 25–35% sits well under speech.</p>
      <button class="btn ghost small" onClick=${() => { change({ ...edit, music: edit.music.filter((x) => x.id !== it.id) }); select(null); }}>
        <${Icon} name="trash" size=${14} /> Remove</button>
    </div>`;
  }
  const title = selected.kind === 'title' && edit.titles.find((x) => x.id === selected.id);
  if (!title) return null;
  const retitle = (patch, record = true) => (record ? change : live)({ ...edit, titles: edit.titles.map((x) => (x.id === title.id ? { ...x, ...patch } : x)) });
  return html`<div class="props-body form">
    <div class="row-tight"><h3 class="grow">Title</h3><span class="mono small muted">${fmt(title.start)} · ${fine(title.end - title.start)}</span></div>
    <div class="field"><span>Style</span><${Select} label="Style" value=${title.style} options=${TITLE_STYLES}
      onChange=${(v) => retitle({ style: v })} /></div>
    <label class="field"><span>${title.style === 'corner' ? 'Tag' : 'Text'}</span><input type="text" maxlength="120" value=${title.text}
      onFocus=${remember} onInput=${(e) => retitle({ text: e.target.value }, false)} /></label>
    <label class="field"><span>${title.style === 'lower_third' ? 'Second line (a role, a place)' : 'Second line'}</span>
      <input type="text" maxlength="120" value=${title.sub || ''} onFocus=${remember} onInput=${(e) => retitle({ sub: e.target.value }, false)} /></label>
    <button class="btn ghost small" onClick=${() => { change({ ...edit, titles: edit.titles.filter((x) => x.id !== title.id) }); select(null); }}>
      <${Icon} name="trash" size=${14} /> Delete</button>
    <p class="muted small">Drag it on the Titles track to move it; drag its ends to change how long it shows. It fades in and out.</p>
  </div>`;
}

/** Media: the hub's library of videos, images and sound, to put on the timeline. */
function MediaTab({ media, setMedia, edit, change, now }) {
  const add = (m) => setMedia([m, ...media]);
  const remove = async (m) => (await confirmAction({ title: `Delete "${m.name}" from the library?`, confirm: 'Delete', danger: true,
    message: 'Edits that use it leave it out the next time they render.' }))
    && api('DELETE', `/api/media/${m.id}`).then(() => setMedia(media.filter((x) => x.id !== m.id))).catch(toast);
  return html`<div class="props-body">
    <div class="row-tight"><p class="muted small grow" style="margin:0">Intros, slides, logos and music, for any edit.</p>
      <${UploadButton} done=${add} /></div>
    ${media.length === 0 ? html`<p class="muted small">Nothing here yet. Upload a video, an image or a song.</p>`
    : html`<div class="media-list">${media.map((m) => html`<div class="media-row">
      ${thumbOf(m)}
      <div class="grow min0"><b class="ellipsis">${m.name}</b>
        <div class="muted small">${{ video: 'Video', image: 'Image', audio: 'Sound' }[m.kind]}${m.duration ? ` · ${fmtLength(m.duration)}` : ''}</div>
        <div class="row-tight">
          ${m.kind !== 'audio' ? html`<button class="btn ghost small" title="Put it in at the playhead, moving the rest along"
              onClick=${() => change(M.insert(edit, now(), m))}>Insert here</button>
            <button class="btn ghost small" onClick=${() => change(M.insert(edit, M.total(edit), m))}>At the end</button>`
          : html`<button class="btn ghost small" onClick=${() => change(M.addMusic(edit, now(), m))}>Add as music here</button>`}
        </div></div>
      <${IconButton} icon="trash" label="Delete from the library" onClick=${() => remove(m)} />
    </div>`)}</div>`}
  </div>`;
}

// Compare edits by what they'd render: link ids are the editor's own, so only whether clips are linked counts
const signature = (source, spec) => JSON.stringify({ source, spec }, (k, v) => (k === 'link' ? !!v : v));

export function EditorPage({ eventId, tab: initialTab }) {
  const [data, setData] = useState(null);
  const [source, setSource] = useState(null);
  const [edit, setEdit] = useState(null);
  const [saved, setSaved] = useState(null);       // what's kept on the hub (the draft or the edit)
  const [selected, setSelected] = useState(null); // {kind: clip | cut | gap | title | music | adjust, ...}
  const [marks, setMarks] = useState([]);         // In and Out, in the timeline's time
  const [tool, setTool] = useState('select');
  const [zoom, setZoomValue] = useState(null);
  const [tab, setTab] = useState(initialTab === 'shorts' ? 'shorts' : 'props');
  const [picked, setPicked] = useState(null);     // a moment for a short, in the recording's time
  const [look, setLook] = useState(SHORT_LOOK);
  const [media, setMedia] = useState([]);
  const [error, setError] = useState('');
  const [framingPlan, setFramingPlan] = useState(null);   // where the short's window goes (framing.plan)
  const [captionPages, setCaptionPages] = useState(null); // what the short's captions show, and when
  const [, setTick] = useState(0);
  const lane = useRef();
  const history = useRef({ past: [], future: [] });
  const started = useRef(false);
  const editRef = useRef(null);
  editRef.current = edit;
  const mediaRef = useRef([]);
  mediaRef.current = media;
  const recordingRef = useRef('');
  recordingRef.current = source ? `/api/recordings/file/${source.path}` : '';
  const mediaById = (id) => mediaRef.current.find((m) => m.id === id) || null;
  const player = usePlayer(editRef, recordingRef, mediaById);
  const now = () => player.clock.current.t;

  // ---- Changing the edit, with undo ----
  const live = (next) => { editRef.current = next; setEdit(next); };      // no undo step (a drag in progress)
  const remember = () => { history.current.past.push(editRef.current); history.current.future = []; setTick((n) => n + 1); };
  const change = (next) => { if (next !== editRef.current) { remember(); live(next); } };
  const undo = () => {
    const h = history.current;
    if (!h.past.length) return;
    h.future.push(editRef.current); live(h.past.pop()); setSelected(null);
  };
  const redo = () => {
    const h = history.current;
    if (!h.future.length) return;
    h.past.push(editRef.current); live(h.future.pop()); setSelected(null);
  };

  const withBrand = (e, brand) => {
    let next = { ...e, brand: brand ? brand.id : null };
    const intro = brand && brand.intro && mediaById(brand.intro), outro = brand && brand.outro && mediaById(brand.outro);
    if (intro) next = M.insert(next, 0, intro, brand.intro_seconds);
    if (outro) next = M.insert(next, M.total(next), outro, brand.outro_seconds);
    return next;
  };
  const begin = (copy, spec, d) => {
    let next = M.fromSpec(spec, copy.duration, mediaById);
    // A new edit of a channel with a brand starts with it
    const brand = !spec && d.channel_brand && d.brands.find((b) => b.id === d.channel_brand);
    if (brand) next = withBrand(next, brand);
    setSource(copy); live(next);
    setSaved(signature(copy.path, M.toSpec(next)));
    history.current = { past: [], future: [] };
    setSelected(null); setMarks([]); setZoomValue(null);
    player.seek(0);
  };
  // Everything once; afterwards only the edit's and shorts' progress, so nothing you do is reset
  const reload = async () => {
    const d = await api('GET', `/api/events/${eventId}/editor`);
    mediaRef.current = d.media;
    setMedia(d.media);
    setData(d);
    if (!started.current && d.copies.length) {
      started.current = true;
      const base = d.draft || (d.edit && { source: d.edit.source, spec: d.edit.spec });
      const copy = (base && d.copies.find((c) => c.path === base.source)) || d.copies.find((c) => c.source === 'spoke') || d.copies[0];
      begin(copy, base && base.source === copy.path ? base.spec : null, d);
    }
  };
  const refresh = () => api('GET', `/api/events/${eventId}/editor/status`).then((s) => setData((d) => ({ ...d, ...s }))).catch(() => {});
  useEffect(() => { started.current = false; reload().catch(toast); }, [eventId]);
  const working = !!data && ((data.edit && BUSY.includes(data.edit.status)) || data.shorts.some((s) => SHORT_BUSY.includes(s.status)));
  usePoll(() => (data ? refresh() : null), working ? 2000 : 20000, [eventId, working, !!data]);

  // The assistant changing the edit: its version goes on the timeline, as a change you can undo
  useEffect(() => {
    const changed = async (ev) => {
      const tools = ev.detail || [];
      if (!tools.some((t) => ['edit_video', 'make_short', 'publish_short', 'publish_video'].includes(t))) return;
      const d = await api('GET', `/api/events/${eventId}/editor`);
      mediaRef.current = d.media;
      setMedia(d.media);
      setData(d);
      if (!tools.includes('edit_video')) return;
      // Its draft, or the edit it just rendered
      const made = d.draft && d.draft.by === 'assistant' ? d.draft
        : d.edit && Date.now() / 1000 - d.edit.created_at < 300 ? { source: d.edit.source, spec: d.edit.spec } : null;
      if (!made) return;
      const copy = d.copies.find((c) => c.path === made.source) || d.copies[0];
      if (!editRef.current || copy.path !== recordingRef.current.replace('/api/recordings/file/', '')) return begin(copy, made.spec, d);
      const next = M.fromSpec(made.spec, copy.duration, mediaById);
      change(next);
      setSaved(signature(copy.path, M.toSpec(next)));
      setSelected(null);
      toast('The assistant changed the edit (⌘Z undoes it)', 'ok');
    };
    addEventListener('ts-changed', changed);
    return () => removeEventListener('ts-changed', changed);
  }, [eventId]);

  // Ask the hub where a 'fill' short's window would go, for the moment picked
  const planFor = data && data.shorts_options.available && tab === 'shorts' && source
    && (picked ? [picked.start, picked.end] : marks.length === 2 && edit ? [M.recordingAt(edit, marks[0]), M.recordingAt(edit, marks[1] - 0.01)] : null);
  const planKey = look.framing === 'fill' && planFor && planFor[0] !== null && planFor[1] !== null
    ? widen(planFor, data.sentences).map((x) => x.toFixed(1)).join('-') : '';
  useEffect(() => {
    if (!planKey) return;
    const [start, end] = planKey.split('-').map(Number);
    if (framingPlan && framingPlan.key === planKey) return;
    setFramingPlan({ key: planKey, start, end, loading: true });
    const timer = setTimeout(() => api('POST', `/api/events/${eventId}/framing`, { source: source.path, start, end })
      .then((p) => setFramingPlan((f) => (f && f.key === planKey ? { ...p, key: planKey, start, end } : f))).catch(() => {}), 300);
    return () => clearTimeout(timer);
  }, [planKey]);

  // And what its captions will show
  const captionKey = planFor && planFor[0] !== null && planFor[1] !== null && look.style !== 'none'
    ? `${widen(planFor, data.sentences).map((x) => x.toFixed(1)).join('-')}-${look.style}` : '';
  useEffect(() => {
    if (!captionKey) return;
    const [start, end] = captionKey.split('-').map(Number);
    api('POST', `/api/events/${eventId}/caption-pages`, { start, end, style: look.style })
      .then((pages) => setCaptionPages({ key: captionKey, pages })).catch(() => {});
  }, [captionKey]);

  const spec = edit ? M.toSpec(edit) : null;
  const current = source && spec && signature(source.path, spec);
  const dirty = !!(saved && current && current !== saved);
  const saveDraft = async () => {
    await api('PUT', `/api/events/${eventId}/edit-draft`, { source: source.path, ...spec });
    setSaved(current);
  };
  useLeaveGuard(dirty, saveDraft, "Your changes to this edit aren't saved.");

  const total = edit ? M.total(edit) : 0;
  useEffect(() => { if (edit) player.refresh(); }, [edit]);
  const select = (s) => { setSelected(s); if (s) setTab('props'); };
  const splitAt = (t, ids) => {
    const e = editRef.current, next = M.split(e, t, ids);
    if (next === e) return toast('Nothing to cut there (too close to the edge of a clip)');
    change(next);
  };
  const del = (ripple) => {
    const e = editRef.current, s = selected;
    if (s && s.kind === 'clip') { change(ripple ? M.rippleDelete(e, s.ids) : M.lift(e, s.ids)); setSelected(null); }
    else if (s && s.kind === 'gap') { change(M.closeGap(e, s.from, s.to)); setSelected(null); }
    else if (s && s.kind === 'cut') change({ ...e, video: e.video.map((c) => (c.id === s.id ? { ...c, transition: null } : c)) });
    else if (s && s.kind === 'title') { change({ ...e, titles: e.titles.filter((x) => x.id !== s.id) }); setSelected(null); }
    else if (s && s.kind === 'music') { change({ ...e, music: e.music.filter((x) => x.id !== s.id) }); setSelected(null); }
    else if (s && s.kind === 'adjust') change({ ...e, color: { ...M.NO_COLOR } });
    else if (marks.length === 2) { change(M.removeRange(e, marks[0], marks[1], ripple)); setMarks([]); }
    else toast('Select a clip, a gap or a title first, or mark a range with I and O');
  };
  const addTitle = () => {
    const e = editRef.current, t = now(), len = M.total(e);
    const start = Math.max(0, Math.min(t, len - 5));
    const title = { id: M.uid(), style: 'lower_third', text: 'Name', sub: 'Role or place', start, end: Math.min(len, start + 5) };
    change({ ...e, titles: [...e.titles, title] });
    select({ kind: 'title', id: title.id });
  };
  const setBrand = async (id) => {
    const brand = data.brands.find((b) => b.id === id);
    if (!brand || !(brand.intro || brand.outro)) return change({ ...edit, brand: id });
    const choice = await ask({ title: `Use ${brand.name}?`, message: 'Its watermark goes over the recording. Put its intro and outro on the timeline too?',
      buttons: [{ label: 'Cancel', value: null }, { label: 'Just the watermark', value: 'mark' }, { label: 'Intro and outro too', value: 'all', kind: 'primary' }] });
    if (choice) change(choice === 'all' ? withBrand(edit, brand) : { ...edit, brand: id });
  };
  // Marking a range by hand replaces a picked moment
  const markIn = () => { const t = now(); setPicked(null); setMarks((m) => (m[1] !== undefined && t < m[1] ? [t, m[1]] : [t])); };
  const markOut = () => { const t = now(); setPicked(null); setMarks((m) => (m.length && t > m[0] ? [m[0], t] : m.length ? [t, m[0]] : [0, t])); };
  const jump = (dir) => {
    const t = now(), e = editRef.current;
    const pts = [...new Set([0, ...e.video.flatMap((c) => [c.at, M.clipEnd(c)]), ...e.audio.flatMap((c) => [c.at, M.clipEnd(c)]), M.total(e)])].sort((a, b) => a - b);
    const to = dir < 0 ? [...pts].reverse().find((p) => p < t - 0.05) : pts.find((p) => p > t + 0.05);
    if (to !== undefined) player.seek(to);
  };
  const ops = {
    trim: (e, track, id, side, t, alone) => M.trim(e, track, id, side, t, { alone, mediaById, duration: source.duration }),
    move: (e, track, id, at, alone) => M.move(e, track, id, at, { alone }),
  };

  // ---- Zoom ----
  const fit = () => Math.max(0.01, ((lane.current ? lane.current.clientWidth : 900) - 90) / (total || 1));
  const setZoom = (z) => { const v = Math.min(MAX_ZOOM, Math.max(fit() * 0.5, z)); setZoomValue(v); return v; };
  useEffect(() => { if (zoom === null && total) setZoomValue(fit()); }, [zoom, total]);

  // ---- Keyboard: like other editors ----
  useEffect(() => {
    const key = (ev) => {
      if (['INPUT', 'TEXTAREA'].includes(ev.target.tagName) || ev.target.closest('.select-wrap, .modal')
        || document.querySelector('.backdrop') || !editRef.current) return;
      const cmd = ev.metaKey || ev.ctrlKey, k = ev.key.toLowerCase(), t = now();
      if (cmd && k === 'z') { ev.preventDefault(); return ev.shiftKey ? redo() : undo(); }
      if (cmd && k === 'y') { ev.preventDefault(); return redo(); }
      if (cmd && k === 'b') { ev.preventDefault(); return splitAt(t); }
      if (cmd) return;
      if (k === ' ' || k === 'k') { ev.preventDefault(); player.toggle(); }
      else if (k === 'i') markIn();
      else if (k === 'o') markOut();
      else if (k === 's') splitAt(t);
      else if (k === 't') addTitle();
      else if (k === 'b' || k === 'c') setTool('blade');
      else if (k === 'v' || k === 'a') setTool('select');
      else if (k === 'x') setMarks([]);
      else if (k === 'escape') { setSelected(null); setMarks([]); setTool('select'); }
      else if (k === 'delete' || k === 'backspace') { ev.preventDefault(); del(ev.shiftKey); }
      else if (k === 'arrowleft') { ev.preventDefault(); player.seek(t - (ev.shiftKey ? 5 : 1)); }
      else if (k === 'arrowright') { ev.preventDefault(); player.seek(t + (ev.shiftKey ? 5 : 1)); }
      else if (k === ',') player.seek(t - 1 / 30);
      else if (k === '.') player.seek(t + 1 / 30);
      else if (k === 'arrowup') { ev.preventDefault(); jump(-1); }
      else if (k === 'arrowdown') { ev.preventDefault(); jump(1); }
      else if (k === 'home') player.seek(0);
      else if (k === 'end') player.seek(M.total(editRef.current));
      else if (k === '=' || k === '+') setZoom((zoom || fit()) * 1.4);
      else if (k === '-') setZoom((zoom || fit()) / 1.4);
    };
    addEventListener('keydown', key);
    return () => removeEventListener('keydown', key);
  });

  const render = async () => {
    setError('');
    const done = data.edit;
    if (done && done.published.some((p) => !p.hidden) && !(await confirmAction({ title: 'Render a new edited video?',
      message: 'It replaces the edited video on the hub. What you published to YouTube stays until you publish again.',
      confirm: 'Render' }))) return;
    try {
      await api('POST', `/api/events/${eventId}/edits`, { source: source.path, ...spec });
      setSaved(current);
      setTab('export');
      refresh();
    } catch (err) { setError(err.message); }
  };
  const startOver = async () => {
    if (!(await confirmAction({ title: 'Start over from the original?', confirm: 'Start over',
      message: 'Puts the whole recording back on the timeline, with nothing else. You can undo it.' }))) return;
    change(M.fromSpec(null, source.duration, mediaById));
    setMarks([]); setSelected(null);
  };
  const pick = (m) => {
    setPicked(m); setTab('shorts');
    const span = M.spanOf(editRef.current, m.start, m.end);
    if (span) { setMarks(span); player.seek(span[0]); }
    else toast("That moment isn't on the timeline now (it's been cut out); Make short still uses it from the recording");
  };
  // A short is cut from the recording, so its moment plays from the recording, whatever the timeline has
  const preview = (a, b) => player.playSource(a, b);

  if (!data) return null;
  const event = data.event;
  const crumb = html`<a class="crumb" href=${`#/broadcast/${event.id}`}><${Icon} name="back" size=${15} /> ${event.title}</a>`;
  if (!data.copies.length) return html`<div>${crumb}
    <div class="empty">${event.recordings_removed_at
      ? html`<h2>The recordings were deleted</h2><p class="muted">They were older than Settings · Old recordings keeps.
          ${data.edit ? 'The edited video is still in the Download menu and the broadcast\'s folder.' : ''}</p>`
      : html`<h2>No recording to edit yet</h2><p class="muted">The recording appears here once it has finished uploading.</p>`}</div></div>`;
  if (!source || !edit) return null;
  const rendered = data.edit && signature(data.edit.source, M.toSpec(M.fromSpec(data.edit.spec, source.duration, mediaById)));
  const busy = data.edit && BUSY.includes(data.edit.status);
  const changeSource = async (path) => {
    if (dirty && !(await confirmAction({ title: 'Switch recordings?', confirm: 'Switch',
      message: 'The timeline starts again from the other recording, and your unsaved changes here are lost.' }))) return;
    begin(data.copies.find((c) => c.path === path), null, data);
  };
  // The moment for a new short: a picked one, else the In and Out marks (in the recording's time)
  const marked = marks.length === 2 && M.recordingAt(edit, marks[0]) !== null && M.recordingAt(edit, marks[1] - 0.01) !== null
    ? [M.recordingAt(edit, marks[0]), M.recordingAt(edit, marks[1] - 0.01)] : null;
  const range = picked ? [picked.start, picked.end] : marked;
  const moments = [...((data.suggestions && data.suggestions.moments) || []),
    ...data.shorts.map((s) => ({ start: s.spec.start, end: s.spec.end, title: s.title, made: true }))];
  const brand = data.brands.find((b) => b.id === edit.brand);
  const moment = range && widen(range, data.sentences);
  const toolButton = (name, icon, title) => html`<button class=${tool === name ? 'on' : ''} title=${title} onClick=${() => setTool(name)}>
    <${Icon} name=${icon} size=${15} /></button>`;
  const tabButton = (name, label) => html`<button role="tab" class=${tab === name ? 'on' : ''} onClick=${() => setTab(name)}>${label}</button>`;

  return html`<div class="studio-page">
    <div class="studio-head">
      ${crumb}
      <h1 class="ellipsis grow">${event.title}</h1>
      ${data.copies.length > 1 && html`<${Select} className="source-pick" label="Recording" value=${source.path} onChange=${changeSource}
        options=${data.copies.map((c) => [c.path, `${c.source === 'hub' ? 'Hub recording' : 'Spoke recording'} · ${fmt(c.duration)}`, bytes(c.size)])} />`}
      ${dirty ? html`<span class="chip ember">Unsaved</span>` : data.draft ? html`<span class="chip">Draft saved</span>` : ''}
      <button class="btn ghost small" disabled=${!dirty} onClick=${() => saveDraft().catch(toast)}>Save draft</button>
      <button class="btn primary small" disabled=${busy} onClick=${render}>${busy ? `Rendering ${Math.round(data.edit.progress * 100)}%` : `Render · ${fmt(total)}`}</button>
    </div>
    ${error && html`<p class="error">${error}</p>`}
    <div class="studio">
      <aside class="panel props">
        <div class="segmented wide tabs" role="tablist">
          ${tabButton('props', 'Properties')}${tabButton('media', 'Media')}
          ${tabButton('shorts', html`Shorts${data.shorts.length ? html` <span class="count">${data.shorts.length}</span>` : ''}`)}${tabButton('export', 'Export')}
        </div>
        <div class="props-scroll">
          ${tab === 'props' ? html`<${Properties} edit=${edit} selected=${selected} change=${change} live=${live} remember=${remember}
              select=${setSelected} mediaById=${mediaById} brands=${data.brands} setBrand=${setBrand} />`
          : tab === 'media' ? html`<${MediaTab} media=${media} setMedia=${(list) => { mediaRef.current = list; setMedia(list); }}
              edit=${edit} change=${change} now=${now} />`
          : tab === 'shorts' ? html`<${ShortsTab} eventId=${eventId} data=${data} source=${source} range=${range} picked=${picked}
              pick=${pick} preview=${preview} look=${look} setLook=${setLook} refresh=${refresh} reload=${() => reload()} />`
          : html`<div class="props-body form">
              <button class="btn primary" disabled=${busy} onClick=${render}>${data.edit ? 'Render again' : 'Render'} · ${fmt(total)}</button>
              <button class="btn ghost small" onClick=${startOver}>Start over from the original</button>
              <h3>Edited video</h3>
              <${EditedVideo} edit=${data.edit} event=${event} channels=${data.channels} refresh=${refresh}
                changed=${!!(rendered && current !== rendered)} /></div>`}
        </div>
      </aside>
      <section class="panel flush viewer">
        <${Viewer} player=${player} edit=${edit} mediaById=${mediaById} brand=${brand}
          guide=${tab === 'shorts' && { plan: framingPlan, moment, framing: look.framing,
            captions: captionPages && look.style !== 'none' && captionPages.key === `${captionKey}` && {
              pages: captionPages.pages, accent: look.accent,
              style: data.shorts_options.styles.find((x) => x[0] === look.style) || data.shorts_options.styles[0] } }} />
        <div class="transport">
          <button class="icon-btn" title="Previous edit (↑)" aria-label="Previous edit" onClick=${() => jump(-1)}><${Icon} name="prev" /></button>
          <${PlayButton} player=${player} />
          <button class="icon-btn" title="Next edit (↓)" aria-label="Next edit" onClick=${() => jump(1)}><${Icon} name="next" /></button>
          <${Timecode} player=${player} total=${total} />
          <span class="grow"></span>
          <button class="btn ghost small" title="Mark where it starts (I)" onClick=${markIn}><${Icon} name="mark-in" size=${15} /> In</button>
          <button class="btn ghost small" title="Mark where it ends (O)" onClick=${markOut}><${Icon} name="mark-out" size=${15} /> Out</button>
        </div>
      </section>
      <section class="panel flush timeline-panel" ref=${lane}>
        <div class="tl-tools">
          <div class="segmented" role="group" aria-label="Tool">
            ${toolButton('select', 'pointer', 'Select, move and trim (V)')}
            ${toolButton('blade', 'scissors', 'Cut tool: click a clip to cut it there (B)')}
          </div>
          <button class="btn ghost small" title="Cut every track at the playhead (S)" onClick=${() => splitAt(now())}>Cut here</button>
          <button class="btn ghost small" title="Add a title at the playhead (T)" onClick=${addTitle}>T  Title</button>
          <button class="btn ghost small" title="Delete what's selected, leaving a gap (Delete) · and close it (Shift+Delete)"
            onClick=${() => del(false)}><${Icon} name="trash" size=${14} /> Delete</button>
          ${tab === 'shorts' && marks.length === 2 && html`<button class="btn small" onClick=${() => setPicked(null)}>
            <${Icon} name="spark" size=${14} /> Short from In–Out</button>`}
          <span class="grow"></span>
          <button class="icon-btn" title="Undo (⌘Z)" aria-label="Undo" disabled=${!history.current.past.length} onClick=${undo}><${Icon} name="undo" /></button>
          <button class="icon-btn" title="Redo (⇧⌘Z)" aria-label="Redo" disabled=${!history.current.future.length} onClick=${redo}><${Icon} name="redo" /></button>
          <span class="tl-sep"></span>
          <button class="icon-btn" title="Zoom out (−)" aria-label="Zoom out" onClick=${() => setZoom((zoom || fit()) / 1.4)}><${Icon} name="minus" /></button>
          <button class="icon-btn" title="Fit the whole edit" aria-label="Fit" onClick=${() => setZoom(fit())}><${Icon} name="fit" /></button>
          <button class="icon-btn" title="Zoom in (+)" aria-label="Zoom in" onClick=${() => setZoom((zoom || fit()) * 1.4)}><${Icon} name="plus" /></button>
        </div>
        ${zoom !== null && html`<${Timeline} edit=${edit} clock=${player.clock} listen=${player.listen} duration=${source.duration}
          strip=${`/api/recordings/filmstrip/${source.path}`} waveform=${`/api/recordings/waveform/${source.path}`}
          mediaById=${mediaById} marks=${marks} selected=${selected} tool=${tool} zoom=${zoom} setZoom=${setZoom}
          sentences=${data.sentences} moments=${moments} picked=${picked} colorLabel=${colorName(edit.color)}
          brandName=${brand && brand.watermark ? brand.name : ''} ops=${ops}
          on=${{ seek: (t) => player.seek(t), select, split: splitAt, pickMoment: pick,
            change: (next, record) => (record ? change(next) : live(next)),
            dragEnd: (before) => { if (before !== editRef.current) { history.current.past.push(before); history.current.future = []; setTick((n) => n + 1); } } }} />`}
        <details class="tl-help muted small"><summary>Keyboard shortcuts</summary>
          Space plays · B cut tool, V select · S cuts at the playhead · Delete leaves a gap, Shift+Delete closes it · click a gap
          and Delete to close it · Option-click or Option-drag for just the picture or the sound · click a cut for a transition ·
          drag the round handles for fades · T adds a title · I and O mark a range · ⌘Z undoes · ⌘ + scroll zooms</details>
      </section>
    </div>
  </div>`;
}
