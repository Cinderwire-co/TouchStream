// Edit profiles (Settings > Editing): how a broadcast's edit is made by itself once it's transcribed, and what each
// profile has learned from the edits people make (autoedit.py)
import { html, useState, useEffect } from '../vendor/preact-htm.module.js';
import { ago, api } from './lib.js';
import { Icon, IconButton, Modal, Switch } from './ui.js';
import { Select } from './fields.js';
import { confirmAction, toast } from './dialogs.js';

const KEEP = [['message', 'Just the message'], ['service', 'The whole service']];
const AFTER = [['review', 'Wait for me to look it over'], ['publish', 'Publish it to YouTube by itself']];
const PRIVACY = [['public', 'Public'], ['unlisted', 'Unlisted'], ['private', 'Private']];
const HIDE = [['keep', 'Leave it as it is'], ['private', 'Make it private'], ['unlisted', 'Make it unlisted']];
const NEW = { name: '', keep: 'message', cut_music: true, trim_pauses: false, instructions: '', brand: true, after: 'review',
  privacy: 'public', hide_live: 'keep', learn: true };

/** What a profile does, in a line. */
export const profileLine = (p) => [p.keep === 'message' ? 'Just the message' : 'The whole service', p.cut_music && 'no music',
  p.trim_pauses && 'no long pauses', p.brand && 'branded', p.after === 'publish' ? 'publishes by itself' : 'waits for you']
  .filter(Boolean).join(' · ');

function Toggle({ title, about, checked, onChange }) {
  return html`<div class="check-row"><div><b>${title}</b><div class="muted small">${about}</div></div>
    <${Switch} checked=${checked} onChange=${onChange} label=${title} /></div>`;
}

function ProfileModal({ profile, ai, onClose }) {
  const [f, setF] = useState({ ...NEW, ...(profile || {}) });
  const [examples, setExamples] = useState(null);
  const [error, setError] = useState('');
  const set = (patch) => setF({ ...f, ...patch });
  useEffect(() => { if (profile) api('GET', `/api/edit-profiles/${profile.id}/examples`).then(setExamples).catch(toast); }, []);
  const save = async (e) => {
    e.preventDefault();
    try {
      await api(profile ? 'PATCH' : 'POST', profile ? `/api/edit-profiles/${profile.id}` : '/api/edit-profiles', f);
      onClose(true);
    } catch (err) { setError(err.message); }
  };
  const forget = (x) => api('DELETE', `/api/edit-profiles/${profile.id}/examples/${x.id}`)
    .then(() => setExamples(examples.filter((y) => y.id !== x.id))).catch(toast);
  const needsAi = f.keep === 'message' || f.cut_music || f.instructions.trim();
  return html`<${Modal} title=${profile ? `Edit profile: ${profile.name}` : 'New edit profile'} onClose=${() => onClose(false)}>
    <form class="form" onSubmit=${save}>
      <label class="field"><span>Name</span><input type="text" maxlength="60" placeholder="Sunday sermon" value=${f.name}
        onInput=${(e) => set({ name: e.target.value })} /></label>
      <div class="field"><span>Keep</span>
        <div class="segmented wide" role="group" aria-label="Keep">${KEEP.map(([v, label]) => html`<button type="button"
          class=${f.keep === v ? 'on' : ''} onClick=${() => set({ keep: v })}>${label}</button>`)}</div>
        <small class="muted">${f.keep === 'message' ? 'The sermon or talk, from where it begins to where it ends: the assistant finds it in the transcript.'
          : 'Everything, with the dead air before the first words and after the last trimmed off.'}</small></div>
      <${Toggle} title="Take out the music" about="The songs and special music, found by their words." checked=${f.cut_music}
        onChange=${(v) => set({ cut_music: v })} />
      <${Toggle} title="Take out long pauses" about="Most sermons don't need it: a pause is part of the preaching. It's for fast-paced videos."
        checked=${f.trim_pauses} onChange=${(v) => set({ trim_pauses: v })} />
      <label class="field"><span>In your own words (optional)</span><textarea rows="3" maxlength="2000"
        placeholder="We open with announcements and two songs. Keep from the scripture reading to the closing prayer, and cut the offering."
        value=${f.instructions} onInput=${(e) => set({ instructions: e.target.value })}></textarea></label>
      ${!ai && needsAi && html`<p class="muted small">Finding the message, taking out the music and following your words need the
        assistant: add an OpenRouter key in <a href="#/settings/ai">Assistant and API</a>. Without it, the edit runs from the first
        words to the last.</p>`}
      <${Toggle} title="Put the channel's brand on" about="Its intro, outro, watermark and title color (Settings › Brands)."
        checked=${f.brand} onChange=${(v) => set({ brand: v })} />
      <div class="field"><span>Then</span><${Select} label="Then" value=${f.after} options=${AFTER} onChange=${(v) => set({ after: v })} /></div>
      ${f.after === 'publish' && html`<div class="form-row">
        <div class="field"><span>Visibility</span><${Select} label="Visibility" value=${f.privacy} options=${PRIVACY}
          onChange=${(v) => set({ privacy: v })} /></div>
        <div class="field"><span>The live video</span><${Select} label="The live video" value=${f.hide_live} options=${HIDE}
          onChange=${(v) => set({ hide_live: v })} /></div></div>`}
      <${Toggle} title="Learn from my edits" about="Change an edit it made, or make one yourself, and it does it your way next time."
        checked=${f.learn} onChange=${(v) => set({ learn: v })} />
      ${examples && examples.length > 0 && html`<details class="more"><summary class="small">${`What it has learned: ${examples.length} ${
          examples.length === 1 ? 'edit' : 'edits'}`}</summary>
        ${examples.map((x) => html`<div class="learned">
          <div class="row-tight"><b class="grow">${x.title}</b><span class="muted small">${ago(x.created_at)}</span>
            <${IconButton} icon="trash" label="Forget this one" onClick=${() => forget(x)} /></div>
          <div class="muted small" style="white-space:pre-wrap">${x.summary}</div></div>`)}</details>`}
      ${error && html`<p class="error">${error}</p>`}
      <div class="modal-foot"><button type="button" class="btn ghost" onClick=${() => onClose(false)}>Cancel</button>
        <button class="btn primary">${profile ? 'Save' : 'Add profile'}</button></div>
    </form>
  </${Modal}>`;
}

/** Settings > Editing. */
export function EditProfilesSettings() {
  const [data, setData] = useState(null);
  const [editing, setEditing] = useState(null);   // a profile, or {} for a new one
  const load = () => api('GET', '/api/edit-profiles').then(setData);
  useEffect(() => { load(); }, []);
  if (!data) return null;
  const remove = async (p) => (await confirmAction({ title: `Delete the edit profile "${p.name}"?`, confirm: 'Delete', danger: true,
    message: "Broadcasts and series that use it won't be edited by themselves any more. Edits it made stay, and so does what's on YouTube." }))
    && api('DELETE', `/api/edit-profiles/${p.id}`).then(load).catch(toast);
  return html`<div class="panel">
    <div class="row-tight"><h2 class="grow">Edit profiles</h2>
      <button class="btn small" onClick=${() => setEditing({})}><${Icon} name="plus" size=${14} /> New profile</button></div>
    <p class="muted small">How a broadcast's edit is made by itself once it's transcribed: just the message or the whole service, with
      or without the music, with your brand, then waiting for you or published. Pick one when you schedule a broadcast or a
      series, or try one on a past broadcast from its page. It learns from the edits you make.</p>
    ${!data.transcribing && html`<p class="error small">Transcripts are off (Settings › Recordings): edits made by themselves need them.</p>`}
    ${data.profiles.length === 0 ? html`<p class="muted small">No edit profiles yet.</p>` : html`<div class="brand-list">
      ${data.profiles.map((p) => html`<div class="row">
        <div class="grow min0"><b>${p.name}</b>
          <div class="muted small">${profileLine(p)}${p.examples ? ` · learned from ${p.examples} ${p.examples === 1 ? 'edit' : 'edits'}` : ''}</div></div>
        <button class="btn ghost small" onClick=${() => setEditing(p)}>Edit</button>
        <${IconButton} icon="trash" label="Delete" onClick=${() => remove(p)} />
      </div>`)}</div>`}
    ${editing && html`<${ProfileModal} profile=${editing.id ? editing : null} ai=${data.ai}
      onClose=${(changed) => { setEditing(null); if (changed) load(); }} />`}
  </div>`;
}
