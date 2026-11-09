// The media library (videos, images and sound uploaded to the hub) and brands (Settings > Brands)
import { html, useState, useEffect, useRef } from '../vendor/preact-htm.module.js';
import { api } from './lib.js';
import { Avatar, Icon, IconButton, Modal } from './ui.js';
import { Select } from './fields.js';
import { confirmAction, toast } from './dialogs.js';

export const fmtLength = (s) => {
  s = Math.round(s || 0);
  return s >= 60 ? `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}` : `${s} s`;
};
const KIND = { video: 'Video', image: 'Image', audio: 'Sound' };

/** Upload a file to the media library; onProgress(0..1). Resolves to the new item. */
export function uploadMedia(file, onProgress = () => {}) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/media');
    xhr.setRequestHeader('X-File-Name', file.name);
    xhr.setRequestHeader('Content-Type', file.type || 'application/octet-stream');
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onload = () => {
      const body = JSON.parse(xhr.responseText || '{}');
      xhr.status < 300 ? resolve(body) : reject(new Error(typeof body.detail === 'string' ? body.detail : 'Upload failed'));
    };
    xhr.onerror = () => reject(new Error('Upload failed'));
    xhr.send(file);
  });
}

/** An upload button: picks files, uploads them, calls done(item) for each. */
export function UploadButton({ accept = 'video/*,image/*,audio/*', label = 'Upload', done, small = true }) {
  const input = useRef();
  const [progress, setProgress] = useState(null);
  const pick = async (e) => {
    for (const file of e.target.files) {
      try { setProgress(0); done(await uploadMedia(file, setProgress)); } catch (err) { toast(err.message); }
    }
    setProgress(null);
    e.target.value = '';
  };
  return html`<span>
    <input ref=${input} type="file" multiple accept=${accept} hidden onChange=${pick} />
    <button type="button" class=${`btn ${small ? 'small' : ''}`} disabled=${progress !== null} onClick=${() => input.current.click()}>
      <${Icon} name="plus" size=${14} /> ${progress === null ? label : `Uploading… ${Math.round(progress * 100)}%`}</button>
  </span>`;
}

export const thumbOf = (m) => (m.poster ? html`<img class="media-thumb" src=${m.poster} alt="" />`
  : html`<span class="media-thumb sound">♪</span>`);

/** Pick a library item for a brand's intro, outro or watermark (or upload one). */
function MediaPick({ label, value, media, kinds, onChange, onUploaded }) {
  const options = [['', 'None'], ...media.filter((m) => kinds.includes(m.kind)).map((m) => [String(m.id), m.name, `${KIND[m.kind]}${m.duration ? ` · ${fmtLength(m.duration)}` : ''}`])];
  return html`<div class="field"><span>${label}</span>
    <div class="row-tight"><div class="grow"><${Select} label=${label} value=${value ? String(value) : ''} options=${options}
      onChange=${(v) => onChange(v ? Number(v) : null)} /></div>
      <${UploadButton} accept=${kinds.includes('video') ? 'video/*,image/*' : label.startsWith('Default thumbnail') ? 'image/jpeg,image/png' : 'image/*'} label="Upload" done=${(m) => { onUploaded(m); onChange(m.id); }} /></div></div>`;
}

// Title colors to pick from (the first, no color of its own, is TouchStream's orange)
const TITLE_COLORS = [['', '#FE5C09', 'TouchStream orange'], ['#2F6B4F', '#2F6B4F', 'Forest'], ['#7A9C78', '#7A9C78', 'Sage'],
  ['#1E4E8C', '#1E4E8C', 'Navy'], ['#3B82C4', '#3B82C4', 'Blue'], ['#6D3FC0', '#6D3FC0', 'Purple'], ['#B3261E', '#B3261E', 'Crimson'],
  ['#C9A227', '#C9A227', 'Gold'], ['#FFFFFF', '#FFFFFF', 'White']];

function BrandModal({ brand, seed, media, channels, onClose, onUploaded }) {
  const [f, setF] = useState({ name: '', intro: null, outro: null, watermark: null, thumbnail: null, watermark_corner: 'bottom_right',
    watermark_size: 12, watermark_opacity: 80, intro_seconds: 5, outro_seconds: 5, title_color: '', channel_ids: [], ...(seed || {}),
    ...(brand || {}) });
  const [error, setError] = useState('');
  const set = (patch) => setF({ ...f, ...patch });
  const kindOf = (id) => (media.find((m) => m.id === id) || {}).kind;
  const save = async (e) => {
    e.preventDefault();
    try {
      await api(brand ? 'PATCH' : 'POST', brand ? `/api/brands/${brand.id}` : '/api/brands', f);
      onClose(true);
    } catch (err) { setError(err.message); }
  };
  const toggle = (id) => set({ channel_ids: f.channel_ids.includes(id) ? f.channel_ids.filter((c) => c !== id) : [...f.channel_ids, id] });
  return html`<${Modal} title=${brand ? `Brand: ${brand.name}` : 'New brand'} onClose=${() => onClose(false)}>
    <form class="form" onSubmit=${save}>
      <label class="field"><span>Name</span><input type="text" maxlength="60" placeholder="Sunday services" value=${f.name}
        onInput=${(e) => set({ name: e.target.value })} /></label>
      <${MediaPick} label="Default thumbnail (a JPG or PNG, 1280×720)" value=${f.thumbnail} media=${media} kinds=${['image']}
        onChange=${(v) => set({ thumbnail: v })} onUploaded=${onUploaded} />
      <p class="muted small" style="margin-top:-6px">Broadcasts and edited videos on this brand's channels that have no thumbnail of their own
        get this one on YouTube.</p>
      <${MediaPick} label="Intro" value=${f.intro} media=${media} kinds=${['video', 'image']} onChange=${(v) => set({ intro: v })} onUploaded=${onUploaded} />
      ${kindOf(f.intro) === 'image' && html`<label class="field"><span>Show the intro image for (seconds)</span>
        <input type="number" min="1" max="30" value=${f.intro_seconds} onInput=${(e) => set({ intro_seconds: Number(e.target.value) })} /></label>`}
      <${MediaPick} label="Outro" value=${f.outro} media=${media} kinds=${['video', 'image']} onChange=${(v) => set({ outro: v })} onUploaded=${onUploaded} />
      ${kindOf(f.outro) === 'image' && html`<label class="field"><span>Show the outro image for (seconds)</span>
        <input type="number" min="1" max="30" value=${f.outro_seconds} onInput=${(e) => set({ outro_seconds: Number(e.target.value) })} /></label>`}
      <${MediaPick} label="Watermark (a PNG with a transparent background is best)" value=${f.watermark} media=${media} kinds=${['image']}
        onChange=${(v) => set({ watermark: v })} onUploaded=${onUploaded} />
      ${f.watermark && html`<div class="form-row">
        <div class="field"><span>Corner</span><${Select} label="Corner" value=${f.watermark_corner} onChange=${(v) => set({ watermark_corner: v })}
          options=${[['top_left', 'Top left'], ['top_right', 'Top right'], ['bottom_left', 'Bottom left'], ['bottom_right', 'Bottom right']]} /></div>
        <label class="field"><span>Size (% of the width)</span><input type="number" min="4" max="40" value=${f.watermark_size}
          onInput=${(e) => set({ watermark_size: Number(e.target.value) })} /></label>
        <label class="field"><span>Opacity (%)</span><input type="number" min="10" max="100" value=${f.watermark_opacity}
          onInput=${(e) => set({ watermark_opacity: Number(e.target.value) })} /></label></div>`}
      <div class="field"><span>Title color (lower thirds, titles and corner tags in the editor)</span>
        <div class="row-tight"><div class="swatches" role="group" aria-label="Title color">
          ${TITLE_COLORS.map(([value, swatch, name]) => html`<button type="button" class=${(f.title_color || '') === value ? 'on' : ''}
            style=${`--swatch:${swatch}`} title=${name} aria-label=${name} onClick=${() => set({ title_color: value })}></button>`)}</div>
          <input type="text" maxlength="7" placeholder="#2F6B4F" aria-label="Title color, as a hex code" style="width:7.5em"
            value=${f.title_color || ''} onInput=${(e) => set({ title_color: e.target.value.trim() })} /></div></div>
      ${channels.length > 0 && html`<div class="field"><span>Use it for these channels' broadcasts</span>
        ${channels.map((c) => html`<label class="check"><input type="checkbox" checked=${f.channel_ids.includes(c.id)} onChange=${() => toggle(c.id)} />
          <${Avatar} channel=${c} size=${22} /> ${c.title}</label>`)}</div>`}
      ${error && html`<p class="error">${error}</p>`}
      <div class="modal-foot"><button type="button" class="btn ghost" onClick=${() => onClose(false)}>Cancel</button>
        <button class="btn primary">${brand ? 'Save' : 'Add brand'}</button></div>
    </form>
  </${Modal}>`;
}

/** Settings > Brands: as many as you like, one per channel or ministry. */
export function BrandsSettings({ forChannel = null }) {
  const [data, setData] = useState(null);
  const [channels, setChannels] = useState([]);
  const [editing, setEditing] = useState(null);   // a brand, or {} for a new one
  const load = () => api('GET', '/api/media').then(setData);
  useEffect(() => { load(); api('GET', '/api/youtube').then((y) => setChannels(y.channels || [])).catch(() => {}); }, []);
  // From Connect channel (#/settings/brands/channel-<id>): that channel's brand, or a new one for it
  const [seeded, setSeeded] = useState(false);
  useEffect(() => {
    if (seeded || !forChannel || !data || !channels.length) return;
    const channel = channels.find((c) => c.id === forChannel);
    const theirs = data.brands.find((b) => b.channel_ids.includes(forChannel));
    if (channel) setEditing(theirs || { seed: { name: channel.title, channel_ids: [forChannel] } });
    setSeeded(true);
  }, [forChannel, data, channels, seeded]);
  if (!data) return null;
  const name = (id) => (data.media.find((m) => m.id === id) || {}).name;
  const remove = async (b) => (await confirmAction({ title: `Delete the brand "${b.name}"?`, confirm: 'Delete', danger: true,
    message: 'Its files stay in the media library. Edits that used it keep their intro and outro but lose the watermark.' }))
    && api('DELETE', `/api/brands/${b.id}`).then(load).catch(toast);
  return html`<div class="panel">
    <div class="row-tight"><h2 class="grow">Brands</h2>
      <button class="btn small" onClick=${() => setEditing({})}><${Icon} name="plus" size=${14} /> New brand</button></div>
    <p class="muted small">How a channel or ministry looks: a default thumbnail for its broadcasts, and an intro, an outro, a watermark
      and a title color for its edited videos (pick a brand in the editor and it puts the intro and outro on the timeline and the
      watermark over the recording). Its shorts get the watermark too. Make as many as you like.</p>
    ${data.brands.length === 0 ? html`<p class="muted small">No brands yet.</p>` : html`<div class="brand-list">
      ${data.brands.map((b) => html`<div class="row">
        <div class="grow min0"><b>${b.name}</b>
          <div class="muted small">${[b.thumbnail && `Thumbnail: ${name(b.thumbnail)}`, b.intro && `Intro: ${name(b.intro)}`, b.outro && `Outro: ${name(b.outro)}`,
            b.watermark && `Watermark: ${name(b.watermark)}`, b.title_color && `Titles: ${b.title_color}`].filter(Boolean).join(' · ') || 'Nothing added yet'}
            ${b.channel_ids.length ? ` · ${channels.filter((c) => b.channel_ids.includes(c.id)).map((c) => c.title).join(', ')}` : ''}</div></div>
        <button class="btn ghost small" onClick=${() => setEditing(b)}>Edit</button>
        <${IconButton} icon="trash" label="Delete" onClick=${() => remove(b)} />
      </div>`)}</div>`}
    ${editing && html`<${BrandModal} brand=${editing.id ? editing : null} seed=${editing.seed} media=${data.media} channels=${channels}
      onUploaded=${(m) => setData((d) => ({ ...d, media: [m, ...d.media] }))}
      onClose=${(saved) => { setEditing(null); if (saved) load(); }} />`}
  </div>`;
}
