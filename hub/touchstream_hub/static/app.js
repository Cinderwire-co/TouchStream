// TouchStream Hub dashboard (Preact + htm, no build step): sign-in, navigation, the spokes overview
import { html, render, useState, useEffect } from './vendor/preact-htm.module.js';
import { api, hubEnv, usePoll, useHash, CINDERWIRE, MARK } from './js/lib.js';
import { Brand, Icon } from './js/ui.js';
import { Dialogs } from './js/dialogs.js';
import { Assistant, AssistantSetup } from './js/assistant.js';
import { HomePage } from './js/home.js';
import { AddSpokeModal, AllSpokesModal, HubAddressButton, SpokeCard, SpokePage } from './js/spokes.js';
import { SchedulePage } from './js/schedule.js';
import { SettingsPage } from './js/pages.js';
import { EditorPage } from './js/editor.js';
import { TranscriptPage } from './js/transcript.js';

function AuthScreen({ setup, done }) {
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState('');
  const submit = async (e) => {
    e.preventDefault();
    if (setup && password !== confirm) return setError("Passwords don't match");
    try { await api('POST', setup ? '/api/setup' : '/api/login', { password }); done(); }
    catch (err) { setError(err.message); }
  };
  return html`<div class="auth"><form class="panel" onSubmit=${submit}>
    <${Brand} />
    <div>
      <h2>${setup ? 'Welcome to your hub' : 'Sign in'}</h2>
      <p class="muted small">${setup ? 'Choose the password for this hub. You will use it to sign in from any browser.' : 'Enter the hub password.'}</p>
    </div>
    <label class="field"><span>Password</span>
      <input type="password" autofocus value=${password} onInput=${(e) => setPassword(e.target.value)} /></label>
    ${setup && html`<label class="field"><span>Confirm password</span>
      <input type="password" value=${confirm} onInput=${(e) => setConfirm(e.target.value)} /></label>`}
    ${error && html`<p class="error">${error}</p>`}
    <button class="btn primary">${setup ? 'Create hub' : 'Sign in'}</button>
  </form>
  <a class="made-by" href="https://cinderwire.co" target="_blank" rel="noopener"><img src=${CINDERWIRE} alt="" /> Made by Cinderwire</a>
  </div>`;
}

function SpokesPage() {
  const [data, setData] = useState(null);
  const [adding, setAdding] = useState(false);
  const [settingAll, setSettingAll] = useState(false);
  const refresh = () => api('GET', '/api/dashboard').then(setData);
  usePoll(refresh, 2000);
  if (!data) return null;
  return html`<div>
    <div class="page-head">
      <div><h1>Spokes</h1><p class="muted">Every spoke's picture and controls. Open one for its health, destinations and schedule.</p></div>
      <div class="head-actions">
        ${data.spokes.length > 1 && html`<button class="btn ghost" onClick=${() => setSettingAll(true)}><${Icon} name="gear" size=${16} /> Settings for all</button>`}
        <button class="btn primary" onClick=${() => setAdding(true)}><${Icon} name="plus" size=${16} /> Add spoke</button>
      </div>
    </div>
    ${!data.mediamtx && html`<div class="banner"><div class="grow"><b>Streaming is off:</b> MediaMTX wasn't found on this computer.
      Re-run the installer or set TOUCHSTREAM_MEDIAMTX.</div></div>`}
    ${data.discovered.length > 0 && html`<div class="banner">
      <img src=${MARK} width="28" alt="" />
      <div class="grow"><b>${data.discovered.length === 1 ? data.discovered[0].name : `${data.discovered.length} spokes`}</b>
        ${' '}found on your network, ready to adopt.</div>
      <button class="btn primary small" onClick=${() => setAdding(true)}>Adopt</button></div>`}
    ${data.spokes.length === 0 ? html`<div class="empty">
      <img src=${MARK} alt="" /><h2>Add your first spoke</h2>
      <p class="muted">Plug an HDMI source into your TouchStream spoke and connect it to the internet.
        Then pair it here: it will show up live in this dashboard.</p>
      <button class="btn primary" onClick=${() => setAdding(true)}>Add a spoke</button></div>`
    : html`<div class="grid">${data.spokes.map((s) => html`<${SpokeCard} key=${s.id} spoke=${s} events=${data.events} refresh=${refresh} />`)}</div>`}
    ${adding && html`<${AddSpokeModal} discovered=${data.discovered} hub=${data.hub} onClose=${() => { setAdding(false); refresh(); }} />`}
    ${settingAll && html`<${AllSpokesModal} onClose=${() => { setSettingAll(false); refresh(); }} />`}
  </div>`;
}

function Shell({ onSignOut, ai }) {
  const hash = useHash();
  const [, section, id, sub] = hash.split('/');
  const page = section || 'home';
  const active = ['edit', 'transcript', 'shorts'].includes(page) ? 'schedule' : page;
  const link = (name, href, label) => html`<a href=${href} class=${active === name ? 'active' : ''}>${label}</a>`;
  const content = page === 'spokes' && id ? html`<${SpokePage} id=${id} />`
    : page === 'schedule' ? html`<${SchedulePage} />`
    : page === 'edit' && id ? html`<${EditorPage} eventId=${id} tab=${sub} />`
    : page === 'transcript' && id ? html`<${TranscriptPage} eventId=${id} />`
    : page === 'shorts' && id ? html`<${EditorPage} eventId=${id} tab="shorts" />`
    : page === 'settings' ? html`<${SettingsPage} />`
    : page === 'spokes' ? html`<${SpokesPage} />`
    : html`<${HomePage} />`;
  return html`<div>
    <header class="topbar">
      <a href="#/" class="brand-link"><${Brand} /></a>
      <nav class="nav">${link('home', '#/', 'Home')}${link('schedule', '#/schedule', 'Schedule')}${link('spokes', '#/spokes', 'Spokes')}${link('settings', '#/settings', 'Settings')}</nav>
      <div class="topbar-end">
        <${HubAddressButton} />
        <button class="btn ghost small" onClick=${onSignOut}>Sign out</button>
      </div>
    </header>
    <main key=${page}>${content}</main>
    ${ai && ai.enabled && html`<${Assistant} />`}
  </div>`;
}

function App() {
  const [state, setState] = useState(null);
  const refresh = () => api('GET', '/api/state').then((s) => {
    Object.assign(hubEnv, { local: s.local, platform: s.platform });
    setState(s);
  });
  useEffect(() => {
    refresh();
    addEventListener('ts-auth', refresh);
    return () => removeEventListener('ts-auth', refresh);
  }, []);
  if (!state) return null;
  const screen = state.setup_needed ? html`<${AuthScreen} setup done=${refresh} />`
    : !state.signed_in ? html`<${AuthScreen} done=${refresh} />`
    : state.ai && !state.ai.asked ? html`<${AssistantSetup} done=${refresh} />`
    : html`<${Shell} ai=${state.ai} onSignOut=${() => api('POST', '/api/logout').then(refresh)} />`;
  return html`${screen}<${Dialogs} />`;
}

render(html`<${App} />`, document.getElementById('app'));
