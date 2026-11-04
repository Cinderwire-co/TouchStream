// The assistant: a chat on every page that runs the hub (assistant.py), and turning it on
import { html, useState, useEffect, useRef } from '../vendor/preact-htm.module.js';
import { api, MARK } from './lib.js';
import { Icon } from './ui.js';
import { Select } from './fields.js';

// What each tool did, in words, under the reply
const DONE = { hub_overview: 'Looked at the hub', list_broadcasts: 'Looked at the schedule', get_broadcast: 'Looked at a broadcast',
  get_transcript: 'Read the transcript', schedule_broadcast: 'Scheduled', update_broadcast: 'Changed a broadcast',
  delete_broadcast: 'Deleted', go_live: 'Went live', end_live: 'Ended the broadcast', edit_video: 'Started the edit',
  publish_video: 'Started publishing' };
const IDEAS = ["What's coming up this week?", 'Schedule our Sunday service every week at 10 am', 'How are the spokes doing?'];

/** The floating button and chat panel, on every page while the assistant is on. */
export function Assistant() {
  const [open, setOpen] = useState(false);
  const [turns, setTurns] = useState([]);        // what's shown: {who: 'you' | 'it', text, steps, pending, error}
  const [messages, setMessages] = useState([]);  // the conversation as the hub needs it
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const list = useRef();
  const input = useRef();
  useEffect(() => { if (list.current) list.current.scrollTop = list.current.scrollHeight; }, [turns, busy]);
  useEffect(() => { if (open && input.current) input.current.focus(); }, [open]);

  const send = async (body, shown) => {
    setBusy(true);
    if (shown) setTurns((t) => [...t, shown]);
    try {
      const r = await api('POST', '/api/assistant/chat', { page: location.hash, ...body });
      setMessages(r.messages);
      setTurns((t) => [...t.map((x) => ({ ...x, pending: null })),
        { who: 'it', text: r.reply, steps: r.steps, pending: r.pending }]);
    } catch (e) {
      setTurns((t) => [...t, { who: 'it', error: e.message }]);
    }
    setBusy(false);
  };
  const ask = (question) => {
    const q = (question || text).trim();
    if (!q || busy) return;
    setText('');
    send({ messages: [...messages, { role: 'user', content: q }] }, { who: 'you', text: q });
  };
  const answer = (pending, approved) => send({ messages, approve: { tool_call_id: pending.tool_call_id, approved } },
    { who: 'you', text: approved ? 'Yes, do it' : 'No' });
  const restart = () => { setTurns([]); setMessages([]); };

  if (!open) {
    return html`<button class="assistant-fab" onClick=${() => setOpen(true)} title="Ask the assistant" aria-label="Ask the assistant">
      <${Icon} name="spark" size=${22} /></button>`;
  }
  return html`<section class="assistant" role="dialog" aria-label="Assistant">
    <header><b>Assistant</b>
      ${turns.length > 0 && html`<button class="btn ghost small" onClick=${restart}>New chat</button>`}
      <button class="icon-btn" aria-label="Close" onClick=${() => setOpen(false)}><${Icon} name="close" /></button></header>
    <div class="assistant-list" ref=${list}>
      ${turns.length === 0 && html`<div class="assistant-hello">
        <img src=${MARK} alt="" width="36" />
        <p class="muted small">Ask for anything the dashboard does: scheduling, going live, cutting a video, the
          spokes' health. It works on the page you're on, and asks before deleting, publishing or going live.</p>
        ${IDEAS.map((idea) => html`<button class="idea" onClick=${() => ask(idea)}>${idea}</button>`)}
      </div>`}
      ${turns.map((t) => html`<div class=${`say ${t.who}`}>
        ${t.error ? html`<p class="error small">${t.error}</p>` : t.text && html`<p>${t.text}</p>`}
        ${t.steps && t.steps.length > 0 && html`<div class="steps-done">${t.steps.map((s) => html`<span class=${`chip ${s.ok ? '' : 'bad'}`}>
          ${s.ok ? '' : '! '}${DONE[s.tool] || s.tool}</span>`)}</div>`}
        ${t.pending && html`<div class="confirm-card"><p>${t.pending.text}</p>
          <div class="row-tight"><button class="btn ghost small" onClick=${() => answer(t.pending, false)}>No</button>
            <button class="btn primary small" onClick=${() => answer(t.pending, true)}>Yes, do it</button></div></div>`}
      </div>`)}
      ${busy && html`<div class="say it"><p class="muted thinking">Working<span>.</span><span>.</span><span>.</span></p></div>`}
    </div>
    <form class="assistant-input" onSubmit=${(e) => { e.preventDefault(); ask(); }}>
      <textarea ref=${input} rows="1" placeholder="Ask TouchStream…" value=${text} disabled=${busy}
        onInput=${(e) => setText(e.target.value)}
        onKeyDown=${(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(); } }}></textarea>
      <button class="btn primary small" disabled=${busy || !text.trim()} aria-label="Send"><${Icon} name="send" size=${16} /></button>
    </form>
  </section>`;
}

/** Key and model, shared by the setup question and Settings. */
function KeyForm({ onSaved, onSkip, current }) {
  const [key, setKey] = useState('');
  const [model, setModel] = useState(current ? current.model : 'anthropic/claude-haiku-5.5');
  const [models, setModels] = useState(current ? current.models : [['anthropic/claude-haiku-5.5', 'Claude Haiku 5.5', 'Fast and low cost']]);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  useEffect(() => { if (!current) api('GET', '/api/assistant').then((d) => setModels(d.models)).catch(() => {}); }, []);
  const save = async (e) => {
    e.preventDefault();
    setSaving(true); setError('');
    try { onSaved(await api('PUT', '/api/assistant', { api_key: key, model })); setKey(''); }
    catch (err) { setError(err.message); }
    setSaving(false);
  };
  const needsKey = !(current && current.enabled);
  return html`<form class="form" onSubmit=${save}>
    <div class="form-row">
      <label class="field"><span>OpenRouter API key${current && current.enabled ? ` (saved ${current.key_hint}; paste a new one to change it)` : ''}</span>
        <input type="password" autocomplete="off" placeholder="sk-or-…" value=${key} onInput=${(e) => setKey(e.target.value)} /></label>
      <div class="field"><span>Model</span><${Select} label="Model" value=${model} options=${models} onChange=${setModel} /></div>
    </div>
    ${error && html`<p class="error">${error}</p>`}
    <div class="row-tight">
      <button class="btn primary" disabled=${saving || (needsKey && !key.trim())}>${saving ? 'Checking the key…' : needsKey ? 'Turn on the assistant' : 'Save'}</button>
      ${onSkip && html`<button type="button" class="btn ghost" onClick=${onSkip}>Skip for now</button>`}
    </div>
  </form>`;
}

const ABOUT = html`<p class="muted">The assistant schedules broadcasts, cuts videos, checks on spokes and changes settings
  when you ask in plain words, on any page. It uses <a href="https://openrouter.ai" target="_blank" rel="noopener">OpenRouter</a>,
  an AI service you pay for as you use it: a typical request costs well under a cent. Make a key
  at${' '}<a href="https://openrouter.ai/keys" target="_blank" rel="noopener">openrouter.ai/keys</a> and paste it here.</p>`;

/** Asked once, right after setting up the hub (or the first sign-in after an update). */
export function AssistantSetup({ done }) {
  const skip = () => api('POST', '/api/assistant/skip').then(done);
  return html`<div class="auth"><div class="panel setup-ai">
    <div class="brand"><img src=${MARK} alt="" />TouchStream</div>
    <h2>Turn on the assistant?</h2>
    ${ABOUT}
    <p class="muted small">Optional: everything else works without it. You can add a key later in Settings.</p>
    <${KeyForm} onSaved=${done} onSkip=${skip} />
  </div></div>`;
}

/** Settings > Assistant */
export function AssistantSettings({ changed }) {
  const [d, setD] = useState(null);
  useEffect(() => { api('GET', '/api/assistant').then(setD); }, []);
  if (!d) return null;
  const saved = (v) => { setD(v); changed(); };
  const off = async () => saved(await api('DELETE', '/api/assistant'));
  return html`<div class="panel form">
    <div class="row-tight"><h2 class="grow">Assistant</h2><span class=${`chip ${d.enabled ? 'ok' : ''}`}>${d.enabled ? 'On' : 'Off'}</span></div>
    ${ABOUT}
    <${KeyForm} key=${d.enabled} current=${d} onSaved=${saved} />
    ${d.enabled && html`<div><button class="btn ghost small" onClick=${off}>Turn off and remove the key</button></div>`}
  </div>`;
}
