// Settings page
import { html, useState, useEffect } from '../vendor/preact-htm.module.js';
import { ago, api, bytes, reveal, revealLabel, usePoll, CINDERWIRE } from './lib.js';
import { Avatar, Modal, Switch } from './ui.js';
import { Select } from './fields.js';
import { confirmAction, toast } from './dialogs.js';
import { AssistantSettings } from './assistant.js';
import { BrandsSettings } from './brands.js';

function YouTubeSettings() {
  const [yt, setYt] = useState(null);
  const [form, setForm] = useState({ client_id: '', client_secret: '' });
  const [msg, setMsg] = useState('');
  const load = () => api('GET', '/api/youtube').then((d) => { setYt(d); setForm({ client_id: d.client_id, client_secret: '' }); });
  useEffect(() => { load(); }, []);
  if (!yt) return null;
  const save = async (e) => {
    e.preventDefault();
    try { await api('PUT', '/api/youtube/client', form); setMsg('Saved'); load(); } catch (err) { setMsg(err.message); }
  };
  const disconnect = async (c) => {
    if (!(await confirmAction({ title: `Disconnect ${c.title}?`, message: 'Its scheduled broadcasts stop streaming to it.',
      confirm: 'Disconnect', danger: true }))) return;
    await api('DELETE', `/api/youtube/channels/${c.id}`); load();
  };
  return html`<div class="panel form">
    <h2>YouTube</h2>
    <p class="muted">Connecting channels lets the hub schedule broadcasts, set titles and thumbnails, and create
      stream keys for you. It uses your own Google OAuth client, so your hub talks to YouTube directly.</p>
    ${yt.from_env ? html`<p class="muted">The OAuth client is set by environment variables.</p>`
    : html`<form class="form" onSubmit=${save}>
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
      <div><button class="btn">Save</button> <span class="muted small">${msg}</span></div>
    </form>`}
    ${yt.channels.length > 0 && html`<div>
      <span class="eyebrow">Connected channels</span>
      ${yt.channels.map((c) => html`<div class="row"><${Avatar} channel=${c} size=${28} />
        <div class="grow"><b>${c.title}</b>${c.error && html`<div class="error small">${c.error}</div>`}</div>
        <button class="btn ghost small" onClick=${() => disconnect(c)}>Disconnect</button></div>`)}
    </div>`}
    ${yt.configured && html`<p class="muted small">Connect channels from the <a href="#/schedule">Schedule</a> page.</p>`}
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

export function SettingsPage() {
  const [s, setS] = useState(null);
  const [host, setHost] = useState('');
  const [pw, setPw] = useState({ current: '', new: '' });
  const [msg, setMsg] = useState('');
  useEffect(() => { api('GET', '/api/settings').then((d) => { setS(d); setHost(d.public_host); }); }, []);
  if (!s) return null;
  const saveHost = async (e) => { e.preventDefault(); await api('PATCH', '/api/settings', { public_host: host }); setMsg('Saved'); };
  const savePw = async (e) => {
    e.preventDefault();
    try { await api('POST', '/api/password', pw); setPw({ current: '', new: '' }); setMsg('Password changed'); }
    catch (err) { setMsg(err.message); }
  };
  const srtHost = host || s.detected_host || "this computer's IP address";
  return html`<div>
    <div class="page-head"><div><h1>Settings</h1><p class="muted">TouchStream Hub ${s.version}</p></div></div>
    <${YouTubeSettings} />
    <${AssistantSettings} changed=${() => dispatchEvent(new Event('ts-auth'))} />
    <${BrandsSettings} />
    <${RecordingsFolder} s=${s} saved=${() => setMsg('Saved')} />
    <${OldRecordings} saved=${() => setMsg('Saved')} />
    <${TranscriptionSettings} options=${s.transcription} saved=${() => setMsg('Saved')} />
    <form class="panel form" onSubmit=${saveHost}>
      <h2>How spokes reach this hub</h2>
      <p class="muted">Spokes send video over SRT to <b class="mono">${srtHost}:${s.srt_port}</b> (UDP).
        If this hub is behind a proxy or its address differs for spokes, set it here.</p>
      <label class="field"><span>Public address for spokes</span>
        <input type="text" class="mono" placeholder=${s.detected_host} value=${host} onInput=${(e) => setHost(e.target.value)} /></label>
      <div><button class="btn">Save</button></div>
    </form>
    <div class="panel form">
      <h2>Other encoders</h2>
      <p class="muted">OBS, vMix or an ATEM can feed the hub too: stream to${' '}
        <b class="mono">${`rtmp://${srtHost}:${s.rtmp_port}/spoke/<spoke id>`}</b> with the spoke's id as user name and its stream key as password.</p>
    </div>
    <${ApiAccess} />
    <form class="panel form" onSubmit=${savePw}>
      <h2>Password</h2>
      ${s.password_from_env ? html`<p class="muted">The password is set by the TOUCHSTREAM_ADMIN_PASSWORD environment variable.</p>`
      : html`<div class="form-row">
        <label class="field"><span>Current password</span><input type="password" value=${pw.current} onInput=${(e) => setPw({ ...pw, current: e.target.value })} /></label>
        <label class="field"><span>New password</span><input type="password" value=${pw.new} onInput=${(e) => setPw({ ...pw, new: e.target.value })} /></label>
      </div><div><button class="btn">Change password</button></div>`}
    </form>
    <div class="panel"><div class="kv small">
      <span class="muted">Data folder</span><span class="mono">${s.data_dir}</span>
      <span class="muted">SRT ingest</span><span class="mono">${s.srt_port}/udp</span>
      <span class="muted">RTMP ingest</span><span class="mono">${s.rtmp_port}/tcp</span>
    </div></div>
    ${msg && html`<p class="muted" style="margin-top:14px">${msg}</p>`}
    <a class="made-by" href="https://cinderwire.co" target="_blank" rel="noopener">
      <img src=${CINDERWIRE} alt="" /> TouchStream ${s.version} · made by Cinderwire</a>
  </div>`;
}
