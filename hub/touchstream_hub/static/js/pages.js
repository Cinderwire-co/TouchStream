// Settings page
import { html, useState, useEffect } from '../vendor/preact-htm.module.js';
import { ago, api, bytes, reveal, revealLabel, usePoll, CINDERWIRE } from './lib.js';
import { Avatar, Icon, Modal, Switch } from './ui.js';
import { Select } from './fields.js';
import { confirmAction, toast } from './dialogs.js';
import { AssistantSettings } from './assistant.js';
import { BrandsSettings } from './brands.js';
import { ConnectChannelModal } from './schedule.js';

function YouTubeSettings() {
  const [yt, setYt] = useState(null);
  const [form, setForm] = useState({ client_id: '', client_secret: '' });
  const [connecting, setConnecting] = useState(false);
  const load = () => api('GET', '/api/youtube').then((d) => { setYt(d); setForm({ client_id: d.client_id, client_secret: '' }); });
  useEffect(() => { load(); }, []);
  if (!yt) return null;
  const save = async (e) => {
    e.preventDefault();
    try { await api('PUT', '/api/youtube/client', form); toast('Saved', 'ok'); load(); } catch (err) { toast(err); }
  };
  const disconnect = async (c) => {
    if (!(await confirmAction({ title: `Disconnect ${c.title}?`, message: 'Its scheduled broadcasts stop streaming to it, and the hub gives up its access to the channel at Google.',
      confirm: 'Disconnect', danger: true }))) return;
    await api('DELETE', `/api/youtube/channels/${c.id}`); load();
  };
  const client = html`<form class="form" onSubmit=${save}>
    <ol class="steps small">
      <li>In <a href="https://console.cloud.google.com" target="_blank" rel="noopener">Google Cloud Console</a>, create a project
        and enable <b>YouTube Data API v3</b>.</li>
      <li>Under <b>Google Auth Platform</b>, set up an <b>External</b> app and <b>publish</b> it (testing-mode sign-ins expire after 7 days).</li>
      <li>Create a client of type <b>TVs and Limited Input devices</b> and paste its ID and secret here.</li>
    </ol>
    <div class="form-row">
      <label class="field"><span>Client ID</span><input type="text" class="mono" value=${form.client_id}
        onInput=${(e) => setForm({ ...form, client_id: e.target.value })} /></label>
      <label class="field"><span>Client secret ${yt.configured ? '(saved; leave empty to keep)' : ''}</span>
        <input type="password" autocomplete="off" value=${form.client_secret}
          onInput=${(e) => setForm({ ...form, client_secret: e.target.value })} /></label>
    </div>
    <div><button class="btn">Save</button></div>
  </form>`;
  return html`<div class="panel form">
      <div class="panel-head"><h2>Channels</h2>
        ${yt.configured && html`<button class="btn ghost small" onClick=${() => setConnecting(true)}><${Icon} name="plus" size=${14} /> Connect channel</button>`}</div>
      <p class="muted">The hub creates each broadcast on the channels you pick, with its title, description and
        thumbnail, and makes the stream keys itself. It talks to YouTube directly: nothing goes through Cinderwire.</p>
      ${yt.channels.length > 0 ? html`<div>${yt.channels.map((c) => html`<div class="row"><${Avatar} channel=${c} size=${28} />
          <div class="grow"><b>${c.title}</b>${c.error && html`<div class="error small">${c.error}</div>`}</div>
          <button class="btn ghost small" onClick=${() => disconnect(c)}>Disconnect</button></div>`)}</div>`
        : html`<p class="muted small">${yt.configured ? 'No channels yet.' : 'Add your Google OAuth client below first.'}${' '}
            Without YouTube, every broadcast is still recorded to download and upload yourself.</p>`}
    </div>
    <div class="panel form">
      <h2>Google sign-in</h2>
      ${yt.from_env ? html`<p class="muted">The OAuth client is set by environment variables.</p>`
        : yt.configured ? html`<p class="muted">Channels sign in with your own Google OAuth client:
            <span class="mono small">${yt.client_id}</span></p>
          <details class="more"><summary>Change the OAuth client</summary>${client}</details>`
        : html`<p class="muted">Connecting channels needs a Google OAuth client of your own. It takes about ten minutes, once.</p>${client}`}
    </div>
    ${connecting && html`<${ConnectChannelModal} onClose=${() => { setConnecting(false); load(); }} />`}`;
}

const INSTALL = { macos: 'the Mac installer', windows: 'the Windows installer', linux: 'the Linux installer', docker: 'Docker', source: 'source (a developer’s copy)' };

/** Updates: the hub's version, whether it updates itself, and each spoke's. */
function UpdatesSettings() {
  const [u, setU] = useState(null);
  const [checking, setChecking] = useState(false);
  const load = () => api('GET', '/api/updates').then(setU).catch(() => {});
  const moving = u && (u.applying || u.spokes.some((s) => ['updating', 'restarting'].includes(s.update.state)));
  usePoll(load, moving ? 3000 : 30000, [moving]);
  if (!u) return null;
  const act = async (method, path, body) => { try { setU(await api(method, path, body)); } catch (err) { toast(err); } };
  const check = async () => { setChecking(true); await act('POST', '/api/updates/check'); setChecking(false); };
  const status = u.applying ? html`<span class="chip ember">Updating to ${u.applying.version}…</span>`
    : u.available ? html`<span class="chip ember">${u.latest} is ready</span>`
    : u.latest || u.checked_at ? html`<span class="chip ok">Up to date</span>` : null;
  return html`<div class="panel form">
    <div class="panel-head"><h2>Updates</h2>${status}</div>
    ${!u.enabled ? html`<p class="muted">Updates are turned off for this hub (TOUCHSTREAM_UPDATES).</p>` : html`
    <div class="update-line">
      <div class="grow"><b>TouchStream ${u.version}</b>
        <div class="muted small">${u.checked_at ? `Checked ${ago(u.checked_at)}` : 'Not checked yet'}</div></div>
      <button class="btn ghost small" disabled=${checking} onClick=${check}>${checking ? 'Checking…' : 'Check now'}</button>
      ${u.available && u.can_update_self && !u.applying && html`<button class="btn primary small" disabled=${u.requested}
        onClick=${() => act('POST', '/api/updates/apply')}>${u.requested ? 'Waiting…' : `Update to ${u.latest}`}</button>`}
    </div>
    ${u.error && html`<p class="error small">${u.error}</p>`}
    ${u.available && u.requested && u.waiting_for && html`<p class="muted small">It updates as soon as nothing’s going on: ${u.waiting_for}.</p>`}
    ${u.available && !u.can_update_self && html`<p class="muted small">${u.install === 'docker'
      ? html`This hub runs in Docker, so it can’t update itself: pull <span class="mono">${u.docker_image}:latest</span> and start it again (on Coolify, choose Redeploy). Its spokes still update themselves.`
      : 'This hub runs from source: pull the new version and start it again.'}</p>`}
    ${u.available && u.notes && html`<details class="more"><summary>What’s new in ${u.latest}</summary>
      <div class="notes">${u.notes}</div>${u.notes_url && html`<a href=${u.notes_url} target="_blank" rel="noopener">The release on GitHub</a>`}</details>`}
    <div class="check-row"><div><b>Install updates automatically</b>
      <div class="muted small">The hub and its spokes update themselves when nothing’s live or due within the hour. Off: you’re told here and at the top of every page, and choose when.</div></div>
      <${Switch} checked=${u.auto} onChange=${(v) => act('PUT', '/api/updates/auto', { auto: v })} label="Install updates automatically" /></div>
    ${u.spokes.length > 0 && html`<div><span class="eyebrow">Spokes</span>
      ${u.spokes.map((s) => html`<${SpokeVersion} key=${s.id} spoke=${s} auto=${u.auto} update=${() => act('POST', `/api/spokes/${s.id}/update`)} />`)}</div>`}`}
  </div>`;
}

/** A spoke's version, on the Updates panel and its own page. */
export function SpokeVersion({ spoke, auto, update }) {
  const v = spoke.update || {};
  const pct = v.progress != null ? ` ${Math.round(v.progress * 100)}%` : '';
  const [label, action] = {
    current: [html`<span class="ok-text">Up to date</span>`],
    available: [auto || v.requested ? `Updates to ${v.hub_version} when it’s idle` : `${v.hub_version} is ready`,
      !auto && !v.requested && 'Update'],
    updating: [`Updating to ${v.hub_version}…${pct}`],
    restarting: ['Restarting…'],
    failed: [html`<span class="warn-text">${v.hub_version} didn’t start, so it went back to ${v.version}</span>`, v.requested ? null : 'Try again'],
    ahead: ['Newer than the hub'],
    developer: ['A developer’s build'],
    unknown: ['Not heard from yet'],
  }[v.state] || [''];
  return html`<div class="row">
    <div class="grow min0"><b>${spoke.name}</b> <span class="muted small mono">${v.version || ''}</span>
      <div class="muted small">${spoke.online === false && v.state !== 'restarting' ? 'Offline · ' : ''}${label}</div></div>
    ${action && html`<button class="btn ghost small" onClick=${update}>${action}</button>`}
  </div>`;
}

const LANGUAGES = [['', 'Detect automatically'], ['en', 'English'], ['es', 'Spanish'], ['pt', 'Portuguese'], ['fr', 'French'],
  ['de', 'German'], ['it', 'Italian'], ['ko', 'Korean'], ['zh', 'Chinese'], ['ja', 'Japanese'], ['tl', 'Tagalog'], ['vi', 'Vietnamese']];
const MODEL_NOTES = { tiny: 'fastest, rough', base: 'fast, good for clear speech', small: 'slower, more accurate', medium: 'slowest, most accurate' };

function TranscriptionSettings({ options, saved }) {
  const [o, setO] = useState(options);
  const update = async (patch) => { setO({ ...o, ...patch }); await api('PATCH', '/api/settings', {
    ...(patch.enabled !== undefined && { transcribe_enabled: patch.enabled }),
    ...(patch.model && { transcribe_model: patch.model }),
    ...(patch.language !== undefined && { transcribe_language: patch.language }),
    ...(patch.vocabulary !== undefined && { transcribe_vocabulary: patch.vocabulary }) }); saved(); };
  return html`<div class="panel form">
    <h2>Transcripts</h2>
    ${!o.available ? html`<p class="muted">Not installed on this hub. The Docker image includes it; for other installs add the
      <span class="mono">transcribe</span> extra.</p>`
    : html`<p class="muted">The hub turns each broadcast's speech into text with Whisper, on this server. Correct it in
        the dashboard and download captions (.srt) for YouTube Studio.</p>
      <div class="check-row"><div><b>Transcribe every broadcast</b>
        <div class="muted small">A few minutes after it ends. You can always transcribe one by hand.</div></div>
        <${Switch} checked=${o.enabled} onChange=${(v) => update({ enabled: v })} label="Transcribe every broadcast" /></div>
      <div class="form-row">
        <div class="field"><span>Accuracy</span><${Select} label="Accuracy" value=${o.model} onChange=${(v) => update({ model: v })}
          options=${options.models.map((m) => [m, m[0].toUpperCase() + m.slice(1), MODEL_NOTES[m]])} /></div>
        <div class="field"><span>Language</span><${Select} label="Language" value=${o.language} options=${LANGUAGES}
          onChange=${(v) => update({ language: v })} /></div>
      </div>
      <label class="field"><span>Words to recognize · names and terms it should spell right</span>
        <input type="text" value=${o.vocabulary} placeholder="TouchStream, Cinderwire, Pastor Okafor"
          onChange=${(e) => update({ vocabulary: e.target.value })} /></label>`}
  </div>`;
}

const COUNTS = [1, 2, 3, 4, 5, 6].map((n) => [n, `${n} Short${n > 1 ? 's' : ''}`]);

/** Shorts made by themselves after each broadcast (from its transcript, by the assistant), as drafts to look over. */
function AutoShorts({ options, saved }) {
  const [o, setO] = useState(options);
  const update = async (patch) => {
    setO({ ...o, ...patch });
    await api('PATCH', '/api/settings', { ...(patch.on !== undefined && { shorts_auto: patch.on }), ...(patch.count && { shorts_auto_count: patch.count }) });
    saved();
  };
  return html`<div class="panel form">
    <h2>Shorts</h2>
    <p class="muted">Once a broadcast is transcribed, the assistant picks its best moments and the hub makes Shorts of them:
      vertical, following the speaker, with captions and the pauses taken out. They wait for you on the broadcast’s
      page: nothing is published until you choose to.</p>
    ${!o.available ? html`<p class="muted small">Shorts need the transcription add-on, which this hub doesn’t have.</p>`
    : html`<div class="check-row"><div><b>Make Shorts after each broadcast</b>
        <div class="muted small">${o.assistant ? 'A few minutes after its transcript is ready.'
          : html`It needs the assistant: add an OpenRouter key in <a href="#/settings/ai">Assistant and API</a>.`}</div></div>
        <${Switch} checked=${o.on} onChange=${(v) => update({ on: v })} label="Make Shorts after each broadcast" /></div>
      <div class="field" style="max-width:260px"><span>How many</span>
        <${Select} label="How many Shorts" value=${o.count} options=${COUNTS} onChange=${(v) => update({ count: v })} /></div>`}
  </div>`;
}

function RecordingsFolder({ s, saved }) {
  const [folder, setFolder] = useState(s.recordings_folder);
  const [current, setCurrent] = useState(s.recordings_folder);
  const [picking, setPicking] = useState(false);
  const [error, setError] = useState('');
  const save = async (path) => {
    try {
      await api('PATCH', '/api/settings', { recordings_folder: path });
      const now = path || s.recordings_folder_default;
      setFolder(now); setCurrent(now); setError(''); saved();
    } catch (e) { setError(e.message); }
  };
  const choose = async () => {
    setPicking(true);
    try { const { path } = await api('POST', '/api/recordings-folder/choose'); if (path) await save(path); }
    catch (e) { setError(e.message); }
    setPicking(false);
  };
  return html`<form class="panel form" onSubmit=${(e) => { e.preventDefault(); save(folder); }}>
    <h2>Recordings folder</h2>
    <p class="muted">After each broadcast, its recordings, edits, thumbnail, captions and details are saved together
      in a folder named for it, like <span class="mono">2026-11-04 Sunday Service</span>. The videos carry the title,
      description, date and thumbnail inside them too. A new folder applies to broadcasts saved from then on.</p>
    ${s.local ? html`<div class="folder-pick">
        <span class="mono path" title=${current}>${current}</span>
        <button type="button" class="btn" disabled=${picking} onClick=${choose}>${picking ? 'Waiting for your choice…' : 'Choose…'}</button>
        <button type="button" class="btn ghost" onClick=${() => reveal()}>${revealLabel()}</button></div>`
      : html`<label class="field"><span>${s.docker ? 'Folder inside the container (mount a folder of yours there to see the files)' : "Folder on the hub's computer"}</span>
          <input type="text" class="mono" value=${folder} onInput=${(e) => setFolder(e.target.value)} /></label>
        <div><button class="btn" disabled=${folder === current}>Save</button></div>`}
    ${current !== s.recordings_folder_default && html`<div><button type="button" class="btn ghost small" onClick=${() => save('')}>
      Use the default: <span class="mono">${s.recordings_folder_default}</span></button></div>`}
    ${error && html`<p class="error">${error}</p>`}
  </form>`;
}

const AGES = [['', 'Keep them all'], [30, 'After 30 days'], [60, 'After 60 days'], [90, 'After 90 days'],
  [180, 'After 6 months'], [365, 'After a year']];
const SIZES = [['', 'No limit'], [50, '50 GB'], [100, '100 GB'], [250, '250 GB'], [500, '500 GB'], [1000, '1 TB'], [2000, '2 TB']];
const rulesOf = (d) => ({ days: d.days || '', max_gb: d.max_gb || '', unpublished: !!d.unpublished });

/** Retention: delete broadcasts' recordings once they're old, or to keep the folder under a size. */
function OldRecordings({ saved }) {
  const [current, setCurrent] = useState(null);   // the saved rules
  const [form, setForm] = useState(null);
  const [preview, setPreview] = useState(null);   // what the rules in the form would delete now
  useEffect(() => { api('GET', '/api/retention').then((d) => { setCurrent(rulesOf(d)); setForm(rulesOf(d)); setPreview(d); }); }, []);
  useEffect(() => { if (form) api('POST', '/api/retention/preview', form).then(setPreview).catch(() => {}); }, [JSON.stringify(form)]);
  if (!form || !preview) return null;
  const set = (patch) => setForm({ ...form, ...patch });
  const changed = JSON.stringify(form) !== JSON.stringify(current);
  const save = async (e) => {
    e.preventDefault();
    if (preview.would_delete > 0 && !(await confirmAction({ title: `Delete ${bytes(preview.would_delete)} of recordings now?`,
      message: `The recordings of ${preview.broadcasts} broadcast${preview.broadcasts === 1 ? '' : 's'} are deleted from the hub. Their edited videos, thumbnails and transcripts stay.`,
      confirm: 'Delete and save', danger: true }))) return;
    const d = await api('PUT', '/api/retention', form);
    setCurrent(rulesOf(d)); setPreview(d); saved();
  };
  return html`<form class="panel form" onSubmit=${save}>
    <h2>Old recordings</h2>
    <p class="muted">Delete broadcasts' recordings once they're old, or to keep the recordings folder from filling the
      disk. Edited videos, thumbnails, transcripts and captions are always kept. Spokes clear their own cards as they fill.</p>
    <div class="form-row">
      <div class="field"><span>Delete recordings</span>
        <${Select} label="Delete recordings" value=${form.days} options=${AGES} onChange=${(v) => set({ days: v })} /></div>
      <div class="field"><span>Keep the recordings folder under</span>
        <${Select} label="Size limit" value=${form.max_gb} options=${SIZES} onChange=${(v) => set({ max_gb: v })} /></div>
    </div>
    <div class="check-row">
      <div><b>Also delete broadcasts that aren't on YouTube</b>
        <div class="muted small">Off: a record-only broadcast's recording is kept until you delete it, since it may be the only copy.</div></div>
      <${Switch} checked=${form.unpublished} onChange=${(v) => set({ unpublished: v })} label="Also delete broadcasts that aren't on YouTube" />
    </div>
    <p class="muted small">The recordings folder holds ${bytes(preview.total)} of video.
      ${' '}${!(form.days || form.max_gb) ? 'Nothing is deleted automatically.' : preview.would_delete
        ? html`<b class="warn-text">These settings delete ${bytes(preview.would_delete)} now</b> (${preview.broadcasts} broadcast${preview.broadcasts === 1 ? '' : 's'}).`
        : 'Nothing needs deleting right now.'}
      ${preview.kept_unpublished > 0 && !form.unpublished && (form.days || form.max_gb) ? ` ${preview.kept_unpublished} not on YouTube ${preview.kept_unpublished === 1 ? 'is' : 'are'} kept.` : ''}
      ${preview.over ? ' It stays over the limit: the rest is edited videos, the last few days, or broadcasts kept above.' : ''}</p>
    <div><button class="btn" disabled=${!changed}>Save</button></div>
  </form>`;
}

const SCOPES = [['read', 'Read only', 'Sees everything, changes nothing'],
  ['control', 'Control', 'Also schedules, goes live, edits and publishes'], ['admin', 'Admin', 'Also changes settings']];

/** API tokens for scripts and AI agents. A new token is shown once. */
function ApiAccess() {
  const [tokens, setTokens] = useState(null);
  const [form, setForm] = useState({ name: '', scope: 'control' });
  const [made, setMade] = useState(null);
  const [copied, setCopied] = useState(false);
  const [hub, setHub] = useState(null);
  useEffect(() => {
    const load = () => api('GET', '/api/tokens').then(setTokens);
    load();
    api('GET', '/api/hub').then(setHub).catch(() => {});
    addEventListener('ts-tokens', load);   // an agent's request approved (AccessPrompt)
    return () => removeEventListener('ts-tokens', load);
  }, []);
  if (!tokens) return null;
  // On the hub's own computer the page is at localhost; agents elsewhere need its network address
  const here = ['localhost', '127.0.0.1'].includes(location.hostname) && hub && hub.ip;
  const origin = here ? `http://${hub.ip}:${hub.port}` : location.origin;
  const mcpUrl = `${origin}/mcp`;
  const create = async (e) => {
    e.preventDefault();
    try {
      const r = await api('POST', '/api/tokens', form);
      setTokens(r.tokens); setMade(r.token); setCopied(false); setForm({ ...form, name: '' });
    } catch (err) { toast(err); }
  };
  const revoke = async (t) => {
    if (!(await confirmAction({ title: `Revoke "${t.name}"?`, message: 'Anything using this token stops working at once.',
      confirm: 'Revoke', danger: true }))) return;
    await api('DELETE', `/api/tokens/${t.id}`);
    setTokens(tokens.filter((x) => x.id !== t.id));
  };
  const copy = () => navigator.clipboard && navigator.clipboard.writeText(made).then(() => setCopied(true));
  const label = Object.fromEntries(SCOPES.map(([v, l]) => [v, l]));
  return html`<div class="panel form">
    <h2>API access</h2>
    <p class="muted">Let scripts and AI agents use this hub: schedule, go live, edit, publish. Give each one its own
      token, with only the access it needs. Agents that speak MCP connect to <b class="mono">${mcpUrl}</b>;
      anything else can use the API described at <a href="/api/openapi.json" target="_blank" rel="noopener">/api/openapi.json</a>.</p>
    <p class="muted">An AI agent can ask for its own token: tell it to read <b class="mono">${origin}/agent</b>. Its
      request shows up here to approve, with a code to check, and it gets the token itself.</p>
    ${made && html`<div class="new-token">
      <b>Copy this token now: it isn't shown again.</b>
      <div class="copy"><code>${made}</code><button class="btn small" onClick=${copy}>${copied ? 'Copied' : 'Copy'}</button></div>
      <span class="muted small">Send it as <span class="mono">Authorization: Bearer …</span></span>
    </div>`}
    ${tokens.length > 0 && html`<div>${tokens.map((t) => html`<div class="row">
      <div class="grow min0"><b>${t.name}</b> <span class="chip">${label[t.scope]}</span>
        <div class="muted small">…${t.hint} · made ${ago(t.created_at)} · ${t.last_used_at ? `last used ${ago(t.last_used_at)}` : 'not used yet'}</div></div>
      <button class="btn ghost small" onClick=${() => revoke(t)}>Revoke</button></div>`)}</div>`}
    <form class="form-row token-form" onSubmit=${create}>
      <label class="field"><span>New token for</span>
        <input type="text" maxlength="60" placeholder="Claude, n8n, a script…" value=${form.name}
          onInput=${(e) => setForm({ ...form, name: e.target.value })} /></label>
      <div class="field"><span>Access</span>
        <${Select} label="Access" value=${form.scope} options=${SCOPES} onChange=${(v) => setForm({ ...form, scope: v })} /></div>
      <div><button class="btn" disabled=${!form.name.trim()}>Make token</button></div>
    </form>
  </div>`;
}

/** An agent asking for a token (POST /api/access), on whatever page is open. Approved, it collects the token
 * itself; closed, it waits (for ten minutes) until the next visit. */
export function AccessPrompt() {
  const [waiting, setWaiting] = useState([]);
  const [later, setLater] = useState([]);
  const [scope, setScope] = useState(null);
  usePoll(() => api('GET', '/api/access-requests').then(setWaiting), 3000);
  const r = waiting.find((x) => !later.includes(x.id));
  useEffect(() => { setScope(r ? r.scope : null); }, [r && r.id]);
  if (!r || !scope) return null;
  const answer = async (approve) => {
    try {
      await api('POST', `/api/access-requests/${r.id}/${approve ? 'approve' : 'deny'}`, approve ? { scope } : undefined);
      toast(approve ? `${r.name} can use the hub now` : `${r.name} was turned away`, 'ok');
      if (approve) dispatchEvent(new Event('ts-tokens'));
    } catch (err) { toast(err); }
    setWaiting(waiting.filter((x) => x.id !== r.id));
  };
  const about = Object.fromEntries(SCOPES.map(([v, l, what]) => [v, { l, what }]));
  return html`<${Modal} title="An AI agent is asking for access" onClose=${() => setLater([...later, r.id])}>
    <p><b>${r.name}</b> is asking to use this hub${['127.0.0.1', '::1'].includes(r.ip) ? ' from this computer'
      : r.ip ? html` from <span class="mono">${r.ip}</span>` : ''}.
      Approve it only if it's showing you this same code:</p>
    <div class="code">${r.code}</div>
    <div class="field access-scope"><span>Access</span>
      <div class="segmented wide" role="group" aria-label="Access">${SCOPES.map(([v, l]) => html`<button type="button"
        class=${scope === v ? 'on' : ''} onClick=${() => setScope(v)}>${l}</button>`)}</div>
      <span class="muted small">${about[scope].what}${scope !== r.scope ? ` (it asked for ${about[r.scope].l.toLowerCase()})` : ''}.
        Revoke it any time, in Settings.</span></div>
    <div class="modal-foot split">
      <button class="btn ghost" onClick=${() => answer(false)}>Deny</button>
      <button class="btn primary" onClick=${() => answer(true)}>Approve</button>
    </div>
  </${Modal}>`;
}

function Password({ s }) {
  const [pw, setPw] = useState({ current: '', new: '' });
  const save = async (e) => {
    e.preventDefault();
    try { await api('POST', '/api/password', pw); setPw({ current: '', new: '' }); toast('Password changed', 'ok'); }
    catch (err) { toast(err); }
  };
  return html`<form class="panel form" onSubmit=${save}>
    <h2>Password</h2>
    ${s.password_from_env ? html`<p class="muted">The password is set by the TOUCHSTREAM_ADMIN_PASSWORD environment variable.</p>`
    : html`<div class="form-row">
      <label class="field"><span>Current password</span><input type="password" autocomplete="current-password" value=${pw.current} onInput=${(e) => setPw({ ...pw, current: e.target.value })} /></label>
      <label class="field"><span>New password</span><input type="password" autocomplete="new-password" value=${pw.new} onInput=${(e) => setPw({ ...pw, new: e.target.value })} /></label>
    </div><div><button class="btn" disabled=${!pw.current || pw.new.length < 8}>Change password</button></div>`}
  </form>`;
}

function Network({ s }) {
  const [host, setHost] = useState(s.public_host || '');
  const save = async (e) => {
    e.preventDefault();
    try { await api('PATCH', '/api/settings', { public_host: host }); s.public_host = host; toast('Saved', 'ok'); } catch (err) { toast(err); }
  };
  const srtHost = host || s.detected_host || "this computer's IP address";
  return html`<form class="panel form" onSubmit=${save}>
      <h2>How spokes reach this hub</h2>
      <p class="muted">Spokes send video over SRT to <b class="mono">${srtHost}:${s.srt_port}</b> (UDP).
        If this hub is behind a proxy or its address differs for spokes, set it here.</p>
      <label class="field"><span>Public address for spokes</span>
        <input type="text" class="mono" placeholder=${s.detected_host} value=${host} onInput=${(e) => setHost(e.target.value)} /></label>
      <div><button class="btn" disabled=${host === (s.public_host || '')}>Save</button></div>
    </form>
    <div class="panel form">
      <h2>Other encoders</h2>
      <p class="muted">OBS, vMix or an ATEM can feed the hub too: stream to${' '}
        <b class="mono">${`rtmp://${srtHost}:${s.rtmp_port}/spoke/<spoke id>`}</b> with the spoke's id as user name and its stream key as password.</p>
      <div class="kv small">
        <span class="muted">SRT, from spokes</span><span class="mono">${s.srt_port}/udp</span>
        <span class="muted">RTMP, from encoders</span><span class="mono">${s.rtmp_port}/tcp</span>
      </div>
    </div>`;
}

function AboutHub({ s }) {
  return html`<div class="panel form">
    <h2>This hub</h2>
    <div class="kv small">
      <span class="muted">Version</span><span>TouchStream ${s.version}</span>
      <span class="muted">Installed with</span><span>${INSTALL[s.install] || s.install}</span>
      <span class="muted">Its data</span><span class="mono">${s.data_dir}</span>
    </div>
    ${['macos', 'windows'].includes(s.install) && html`<p class="muted small">To remove the hub from this computer, open the
      TouchStream installer and choose <b>Remove the hub from this computer</b>. It can keep your settings and recordings, or
      move them to the ${s.install === 'windows' ? 'Recycle Bin' : 'Trash'} too.</p>`}
    <a class="made-by" href="https://cinderwire.co/touchstream" target="_blank" rel="noopener">
      <img src=${CINDERWIRE} alt="" /> TouchStream is made by Cinderwire</a>
  </div>`;
}

// The sections of Settings, each at #/settings/<key>
const SECTIONS = [
  ['general', 'General', 'Updates, password and this hub'],
  ['youtube', 'YouTube', 'Channels and Google sign-in'],
  ['recordings', 'Recordings', 'Where they go, how long they stay, transcripts and Shorts'],
  ['brands', 'Brands', 'Intros, outros and watermarks'],
  ['ai', 'Assistant and API', 'The assistant, AI agents and scripts'],
  ['network', 'Network', 'How spokes and encoders reach the hub'],
];

export function SettingsPage({ section }) {
  const [s, setS] = useState(null);
  useEffect(() => { api('GET', '/api/settings').then(setS); }, []);
  if (!s) return null;
  const key = SECTIONS.some(([k]) => k === section) ? section : 'general';
  const saved = () => toast('Saved', 'ok');
  const body = {
    general: html`<${UpdatesSettings} /><${Password} s=${s} /><${AboutHub} s=${s} />`,
    youtube: html`<${YouTubeSettings} />`,
    recordings: html`<${RecordingsFolder} s=${s} saved=${saved} /><${OldRecordings} saved=${saved} />
      <${TranscriptionSettings} options=${s.transcription} saved=${saved} /><${AutoShorts} options=${s.shorts_auto} saved=${saved} />`,
    brands: html`<${BrandsSettings} />`,
    ai: html`<${AssistantSettings} changed=${() => dispatchEvent(new Event('ts-auth'))} /><${ApiAccess} />`,
    network: html`<${Network} s=${s} />`,
  }[key];
  return html`<div>
    <div class="page-head"><div><h1>Settings</h1></div></div>
    <div class="settings">
      <nav class="settings-nav" aria-label="Settings">${SECTIONS.map(([k, label, about]) => html`<a href=${`#/settings/${k}`}
        class=${k === key ? 'active' : ''} aria-current=${k === key ? 'page' : null}><b>${label}</b><span>${about}</span></a>`)}</nav>
      <div class="settings-body" key=${key}>${body}</div>
    </div>
  </div>`;
}
