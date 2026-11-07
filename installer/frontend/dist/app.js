// The TouchStream installer: the hub, then a card for each spoke, one page at a time.
// The Go side (app.go) is window.go.main.App; long jobs report through events.
'use strict';

// Looked up on each call: Wails sets these up as the page loads
const api = new Proxy({}, { get: (_, name) => (...args) => window.go.main.App[name](...args) });
const on = (name, fn) => window.runtime.EventsOn(name, fn);
const ready = () => new Promise((resolve) => {
  const check = () => (window.go && window.go.main && window.runtime ? resolve() : setTimeout(check, 20));
  check();
});

const state = {
  info: {}, hub: null, hubURL: '', signedIn: false,
  names: [''], index: 0, image: null, written: [],
};

// ---- A little DOM ----
function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v == null) continue;
    if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k === 'html') el.innerHTML = v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat()) if (c != null && c !== false) el.append(c.nodeType ? c : document.createTextNode(c));
  return el;
}
const $ = (id) => document.getElementById(id);
const page = (...children) => { const body = h('div', { class: 'body' }, ...children); $('page').replaceChildren(body); return body; };
const spinner = () => h('span', { class: 'spinner' });
const sizeLabel = (n) => `${(n / 1e9).toFixed(n < 10e9 ? 1 : 0)} GB`;
const percent = (p) => (p.total ? Math.round((p.done / p.total) * 100) : 0);

const STEPS = ['The hub', 'Password', 'Spokes', 'Your box', 'Cards', 'Done'];
function rail(now) {
  $('steps').replaceChildren(...STEPS.map((name, i) => h('li', { class: i < now ? 'done' : i === now ? 'now' : '' },
    h('i', {}, i < now ? '✓' : String(i + 1)), name)));
}
function problem(err) {
  return h('div', { class: 'panel bad' }, h('p', {}, String(err && err.message || err).replace(/^Error: /, '')));
}

// Events go to whichever page is listening now
const listeners = {};

// ---- 1. Welcome ----
function welcome() {
  rail(-1);
  page(
    h('h1', {}, 'Set up TouchStream'),
    h('p', { class: 'lead' }, 'This puts the hub on this computer, then writes a microSD card for each spoke. Put a card in its box, plug it in, and it shows up in the dashboard, ready to go live.'),
    h('div', { class: 'actions' }, h('button', { class: 'primary', onclick: findHub }, 'Get started')));
}

// ---- 2. The hub ----
async function findHub() {
  rail(0);
  page(h('h1', {}, 'The hub'), h('div', { class: 'row' }, spinner(), h('span', { class: 'muted' }, 'Looking for a hub on this computer…')));
  const found = await api.FindHub();
  state.hub = found;
  if (found.running) {
    state.hubURL = found.url;
    page(
      h('h1', {}, 'The hub is here'),
      h('div', { class: 'panel' }, h('div', { class: 'row' }, h('span', { class: 'ok' }, '●'),
        h('div', { class: 'grow' }, h('b', {}, 'Installed and running'), h('div', { class: 'muted small mono' }, `${found.url} · version ${found.version}`)))),
      h('p', {}, 'You can carry on with it as it is, or update it to this installer’s version first. Updating keeps your settings and recordings.'),
      h('div', { class: 'actions' }, h('button', { class: 'primary', onclick: password }, 'Continue'),
        h('button', { onclick: installHub }, 'Update it'),
        h('span', { class: 'spacer' }), h('button', { class: 'link', onclick: remoteHub }, 'Use a hub on another computer')));
    return;
  }
  page(
    h('h1', {}, found.installed ? 'The hub isn’t running' : 'Install the hub'),
    found.installed
      ? h('p', { class: 'lead' }, 'The hub is installed on this computer, but it isn’t answering. Installing it again starts it, and keeps your settings and recordings.')
      : h('p', { class: 'lead' }, 'The hub runs your schedule, records every broadcast, and is where you watch and edit. It installs everything it needs, and starts with this computer from now on.'),
    h('div', { class: 'actions' }, h('button', { class: 'primary', onclick: installHub }, found.installed ? 'Start it again' : 'Install the hub'),
      h('span', { class: 'spacer' }), h('button', { class: 'link', onclick: remoteHub }, 'The hub is on another computer')));
}

async function installHub() {
  if (await api.NeedsHomebrew()) return homebrew();
  rail(0);
  const log = [];
  const stepsLog = h('div', { class: 'steps-log' });
  const detail = h('div', { class: 'detail mono' });
  page(h('h1', {}, 'Installing the hub'), h('p', {}, 'This takes a few minutes the first time. It may ask for permission along the way.'), stepsLog, detail);
  let current = null;
  listeners['hub:line'] = (line) => {
    log.push(line.text);
    if (!line.step) { detail.textContent = line.text; return; }
    if (current) current.replaceChildren(h('span', { class: 'tick ok' }, '✓'), current.dataset.text);
    current = h('div', { class: 'now', 'data-text': line.text }, spinner(), line.text);
    stepsLog.append(current);
    detail.textContent = '';
  };
  try {
    state.hubURL = await api.InstallHub();
    state.hub = await api.UseHub(state.hubURL);
    password();
  } catch (err) {
    page(h('h1', {}, 'The hub didn’t install'), problem(err),
      h('details', {}, h('summary', { class: 'muted small' }, 'Details'), h('pre', { class: 'log' }, log.join('\n'))),
      h('div', { class: 'actions' }, h('button', { class: 'primary', onclick: installHub }, 'Try again'),
        h('button', { onclick: findHub }, 'Back')));
  } finally {
    delete listeners['hub:line'];
  }
}

function homebrew() {
  rail(0);
  page(
    h('h1', {}, 'First, Homebrew'),
    h('p', { class: 'lead' }, 'The hub uses FFmpeg for video, which comes from Homebrew, the usual way to add tools like it to a Mac. Install Homebrew with its own installer, then come back here.'),
    h('div', { class: 'panel' }, h('p', {}, 'On Homebrew’s page, download the file ending in ', h('b', {}, '.pkg'), ', open it, and follow its steps. It asks for your Mac password.')),
    h('div', { class: 'actions' }, h('button', { class: 'primary', onclick: () => api.HomebrewPackage() }, 'Get Homebrew'),
      h('button', { onclick: installHub }, 'I’ve installed it')));
}

function remoteHub() {
  rail(0);
  const address = h('input', { type: 'text', placeholder: '192.168.1.20:8080', autofocus: true });
  const status = h('div');
  const connect = async () => {
    status.replaceChildren(h('div', { class: 'row' }, spinner(), h('span', { class: 'muted' }, 'Connecting…')));
    try {
      state.hub = await api.UseHub(address.value);
      state.hubURL = state.hub.url;
      password();
    } catch (err) { status.replaceChildren(problem(err)); }
  };
  page(
    h('h1', {}, 'The hub on another computer'),
    h('p', {}, 'Its address is on the dashboard, under the network sign at the top. This computer needs to be on the same network.'),
    h('label', {}, 'The hub’s address', address), status,
    h('div', { class: 'actions' }, h('button', { class: 'primary', onclick: connect }, 'Connect'), h('button', { onclick: findHub }, 'Back')));
  address.addEventListener('keydown', (e) => e.key === 'Enter' && connect());
}

// ---- 3. The dashboard's password ----
function password() {
  rail(1);
  const fresh = state.hub && state.hub.setup_needed;
  const pw = h('input', { type: 'password', autofocus: true });
  const again = h('input', { type: 'password' });
  const status = h('div');
  const go = async () => {
    try {
      if (fresh) {
        if (pw.value !== again.value) throw new Error('The two passwords are different.');
        await api.SetPassword(state.hubURL, pw.value);
      } else {
        await api.SignIn(state.hubURL, pw.value);
      }
      state.signedIn = true;
      spokesAsk();
    } catch (err) { status.replaceChildren(problem(err)); }
  };
  page(
    h('h1', {}, fresh ? 'Choose the dashboard’s password' : 'Sign in to the hub'),
    h('p', {}, fresh ? 'You’ll use it to open the dashboard, on this computer or any other. At least 8 characters.'
      : 'The dashboard’s password. Signing in lets the installer pair each spoke with the hub.'),
    h('label', {}, 'Password', pw), fresh && h('label', {}, 'Again', again), status,
    h('div', { class: 'actions' }, h('button', { class: 'primary', onclick: go }, 'Continue'),
      h('span', { class: 'spacer' }), !fresh && h('button', { class: 'link', onclick: () => { state.signedIn = false; spokesAsk(); } }, 'Skip: I’ll adopt spokes in the dashboard')));
  for (const input of [pw, again]) input.addEventListener('keydown', (e) => e.key === 'Enter' && go());
}

// ---- 4. Spokes? ----
function spokesAsk() {
  rail(2);
  page(
    h('h1', {}, 'Are you setting up spokes now?'),
    h('p', { class: 'lead' }, 'A spoke is the box next to your camera or video switcher. Each one needs its own microSD card, which the installer writes here.'),
    h('div', { class: 'actions' }, h('button', { class: 'primary', onclick: box }, 'Yes, set up spokes'), h('button', { onclick: done }, 'Not now')));
}

// ---- 5. Is it the right box? ----
const PICOKVM = `<svg viewBox="0 0 200 200" fill="none" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="The Luckfox PicoKVM, with its screen">
  <rect x="22" y="30" width="156" height="150" rx="26" fill="#1c1c1e" stroke="#3a3a3d" stroke-width="2"/>
  <rect x="22" y="22" width="156" height="150" rx="26" fill="#232325" stroke="#48484c" stroke-width="2"/>
  <rect x="46" y="44" width="108" height="108" rx="12" fill="#0e0e0e" stroke="#2c2c2e" stroke-width="2"/>
  <path d="M78 76 l9 5 -9 5z" fill="#fe5c09"/><text x="92" y="84" fill="#f2f2f3" font-family="Bricolage, sans-serif" font-size="9" font-weight="600">Auditorium</text>
  <rect x="68" y="92" width="64" height="18" rx="9" fill="#ef4444"/><text x="100" y="105" fill="#fff" text-anchor="middle" font-family="Bricolage, sans-serif" font-size="10" font-weight="700">LIVE</text>
  <circle cx="84" cy="130" r="7" stroke="#f2f2f3" stroke-width="1.5"/><circle cx="104" cy="130" r="7" stroke="#f2f2f3" stroke-width="1.5"/><circle cx="104" cy="130" r="3.5" fill="#ef4444"/>
  <rect x="122" y="125" width="22" height="10" rx="5" stroke="#f2f2f3" stroke-width="1.5"/>
  <rect x="178" y="70" width="6" height="16" rx="2" fill="#48484c"/><rect x="178" y="96" width="6" height="10" rx="2" fill="#48484c"/>
</svg>`;

function box() {
  rail(3);
  const have = h('input', { type: 'checkbox' });
  const cards = h('input', { type: 'checkbox' });
  const next = h('button', { class: 'primary', onclick: names, disabled: true }, 'Continue');
  const check = () => { next.disabled = !(have.checked && cards.checked); };
  have.addEventListener('change', check);
  cards.addEventListener('change', check);
  const art = h('div'); art.innerHTML = PICOKVM;
  page(
    h('h1', {}, 'Is this your box?'),
    h('div', { class: 'box' }, art.firstElementChild, h('div', {},
      h('p', { class: 'lead' }, 'TouchStream spokes run on one box: the ', h('b', {}, 'Luckfox PicoKVM'), ', the version with the ', h('b', {}, '1.54-inch screen'), ' on top.'),
      h('p', { class: 'small' }, 'It’s a small black cube, about 2 inches across, with HDMI IN, Ethernet and USB-C on one side and a microSD slot on the other.'))),
    h('div', { class: 'panel' },
      h('label', { class: 'check' }, have, h('span', {}, 'I have a Luckfox PicoKVM with the screen, for each spoke')),
      h('label', { class: 'check' }, cards, h('span', {}, 'and a microSD card for each, 32 GB or more', h('div', { class: 'muted small' }, 'A “high endurance” card lasts longest. A card reader too, if this computer has no SD slot.')))),
    h('div', { class: 'actions' }, next, h('span', { class: 'spacer' }), h('button', { class: 'link', onclick: otherBox }, 'I have a different box')));
}

function otherBox() {
  rail(3);
  page(
    h('h1', {}, 'Only the PicoKVM, for now'),
    h('p', { class: 'lead' }, 'The spoke’s software is made for the Luckfox PicoKVM with its 1.54-inch screen, and won’t work on other boxes.'),
    h('div', { class: 'panel' },
      h('p', {}, h('b', {}, 'The PicoKVM Base'), ' (no screen) isn’t supported yet.'),
      h('p', {}, h('b', {}, 'NanoKVM, JetKVM and others'), ' can’t take sound over HDMI, so they can’t be spokes.'),
      h('p', { class: 'small' }, 'Raspberry Pi spokes from earlier versions of TouchStream aren’t supported any more.')),
    h('div', { class: 'actions' }, h('button', { onclick: box }, 'Back'), h('button', { onclick: done }, 'Finish without spokes')));
}

// ---- 6. Names ----
function names() {
  rail(4);
  const list = h('div', { class: 'names' });
  const status = h('div');
  const draw = () => list.replaceChildren(...state.names.map((name, i) => {
    const input = h('input', { type: 'text', value: name, placeholder: i === 0 ? 'Auditorium' : 'Chapel', maxlength: '60' });
    input.addEventListener('input', () => { state.names[i] = input.value; });
    return h('div', { class: 'row' }, input,
      state.names.length > 1 && h('button', { onclick: () => { state.names.splice(i, 1); draw(); }, title: 'Remove' }, '×'));
  }));
  draw();
  const go = () => {
    const clean = state.names.map((n) => n.trim());
    if (clean.some((n) => !n)) return status.replaceChildren(problem('Give each spoke a name: the room it’s in is best.'));
    if (new Set(clean.map((n) => n.toLowerCase())).size !== clean.length) return status.replaceChildren(problem('Each spoke needs its own name.'));
    state.names = clean;
    state.index = 0;
    state.written = [];
    getImage();
  };
  page(
    h('h1', {}, 'Name your spokes'),
    h('p', {}, 'One for each box, named for the room it’s in. You can rename them in the dashboard later.'),
    list,
    h('button', { onclick: () => { state.names.push(''); draw(); list.lastChild.querySelector('input').focus(); } }, '+ Another spoke'),
    status,
    h('div', { class: 'actions' }, h('button', { class: 'primary', onclick: go }, 'Continue')));
}

// ---- 7. The spoke's software ----
async function getImage() {
  rail(4);
  const bar = h('i', { style: 'width:0%' });
  const text = h('p', { class: 'small' }, 'Starting…');
  page(h('h1', {}, 'Getting the spoke’s software'), h('div', { class: 'bar' }, bar), text);
  listeners['image:progress'] = (p) => {
    if (p.phase === 'unpacking') { text.textContent = 'Unpacking it…'; bar.style.width = '100%'; return; }
    bar.style.width = `${percent(p)}%`;
    text.textContent = `Downloading… ${sizeLabel(p.done)}${p.total > 0 ? ` of ${sizeLabel(p.total)}` : ''}`;
  };
  try {
    state.image = await api.GetImage();
    card();
  } catch (err) {
    page(h('h1', {}, 'The spoke’s software'), problem(err),
      h('div', { class: 'actions' }, h('button', { class: 'primary', onclick: getImage }, 'Try again'),
        state.info.dev && h('button', { onclick: pickImage }, 'Choose an image file…')));
  } finally {
    delete listeners['image:progress'];
  }
}

async function pickImage() {
  try {
    const picked = await api.PickImage();
    if (picked && picked.ready) { state.image = picked; card(); }
  } catch (err) { $('page').firstChild.append(problem(err)); }
}

// ---- 8. A card for each spoke ----
let polling = null;
function card() {
  rail(4);
  const name = state.names[state.index];
  let chosen = null;
  const list = h('div', { class: 'cards' });
  const confirm = h('div');
  const SD = '<svg class="sd" viewBox="0 0 28 34"><path d="M2 3a2 2 0 0 1 2-2h14l8 8v22a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2z" fill="#272727" stroke="#8e8e93" stroke-width="1.5"/><path d="M8 5v6M12 5v6M16 5v6" stroke="#8e8e93" stroke-width="1.5"/></svg>';
  const draw = (found) => {
    if (!found.length) {
      chosen = null;
      confirm.replaceChildren();
      list.replaceChildren(h('div', { class: 'panel row' }, spinner(), h('span', { class: 'muted' }, 'Waiting for a card…')));
      return;
    }
    if (chosen && !found.some((c) => c.id === chosen.id)) { chosen = null; confirm.replaceChildren(); }
    list.replaceChildren(...found.map((c) => {
      const icon = h('span'); icon.innerHTML = SD;
      return h('button', { class: `card${chosen && chosen.id === c.id ? ' chosen' : ''}`, onclick: () => { chosen = c; draw(found); ask(c); } },
        icon.firstChild, h('div', { class: 'grow' }, h('b', {}, c.name || 'Card'),
          h('div', { class: 'muted small' }, `${sizeLabel(c.size)}${c.volumes && c.volumes.length ? ` · ${c.volumes.join(', ')}` : ''}${c.read_only ? ' · locked' : ''}${c.virtual ? ' · disk image' : ''}`)));
    }));
  };
  const ask = (c) => {
    if (c.read_only) return confirm.replaceChildren(problem('This card is locked: slide the switch on its side up, away from LOCK, and put it back in.'));
    if (state.image && c.size < state.image.size) return confirm.replaceChildren(problem('This card is too small for the spoke: use one of 32 GB or more.'));
    confirm.replaceChildren(h('div', { class: 'panel warn' },
      h('p', {}, 'Everything on ', h('b', {}, `${c.name} (${sizeLabel(c.size)})`), ' will be erased',
        c.volumes && c.volumes.length ? [', including ', h('b', {}, c.volumes.join(', '))] : '', '.'),
      h('div', { class: 'actions' }, h('button', { class: 'primary', onclick: () => write(c) }, `Erase it and write ${name}`))));
  };
  page(
    h('h1', {}, state.names.length > 1 ? `Card ${state.index + 1} of ${state.names.length}: ${name}` : `${name}’s card`),
    h('p', {}, 'Put a microSD card in this computer, and choose it.'),
    list, confirm);
  const refresh = async () => {
    try { draw(await api.Cards()); } catch (err) { list.replaceChildren(problem(err)); }
  };
  refresh();
  clearInterval(polling);
  polling = setInterval(refresh, 2000);
}

async function write(c) {
  clearInterval(polling);
  const name = state.names[state.index];
  const bar = h('i', { style: 'width:0%' });
  const text = h('p', { class: 'small' }, state.info.os === 'windows'
    ? 'Windows will ask for permission: writing a card needs it.' : 'Your Mac will ask for your password: writing a card needs it.');
  page(h('h1', {}, `Writing ${name}’s card`), h('div', { class: 'bar' }, bar), text,
    h('p', { class: 'small muted' }, 'Leave the card in until it’s done. Then every byte is read back and checked.'));
  listeners['card:progress'] = (p) => {
    bar.style.width = `${percent(p)}%`;
    text.textContent = p.phase === 'checking' ? `Checking… ${percent(p)}%` : p.phase === 'writing' ? `Writing… ${percent(p)}%` : text.textContent;
  };
  try {
    const result = await api.WriteCard(c, name);
    state.written[state.index] = { name, ...result };
    const last = state.index === state.names.length - 1;
    page(
      h('h1', {}, `${name}’s card is ready`),
      h('div', { class: 'panel' }, h('div', { class: 'row' }, h('span', { class: 'ok' }, '✓'),
        h('div', {}, h('b', {}, 'Written and checked'),
          h('div', { class: 'muted small' }, result.paired ? 'Paired with the hub: it shows up in the dashboard by itself.' : 'Adopt it in the dashboard, under Add spoke, when it’s on.')))),
      h('p', {}, 'Take the card out. Unplug ', h('b', {}, `${name}’s`), ' box if it’s on, put the card in, then plug it back in: it starts from the card.'),
      result.note && h('p', { class: 'small' }, result.note),
      h('div', { class: 'actions' }, h('button', { class: 'primary', onclick: () => { if (last) done(); else { state.index += 1; card(); } } },
        last ? 'Finish' : `Next: ${state.names[state.index + 1]}’s card`)));
  } catch (err) {
    page(h('h1', {}, `${name}’s card wasn’t written`), problem(err),
      h('div', { class: 'actions' }, h('button', { class: 'primary', onclick: card }, 'Try again')));
  } finally {
    delete listeners['card:progress'];
  }
}

// ---- 9. Done ----
function done() {
  clearInterval(polling);
  rail(6);
  const spokes = state.written.filter(Boolean);
  page(
    h('h1', {}, 'TouchStream is set up'),
    spokes.length ? h('p', { class: 'lead' }, 'Put each card in its box while it’s unplugged, then plug in HDMI from your camera or switcher, Ethernet, and power last. In a minute or so it shows up in the dashboard.')
      : h('p', { class: 'lead' }, 'Run the installer again whenever you’re adding spokes.'),
    spokes.length > 0 && h('div', { class: 'summary' }, spokes.map((s) => h('div', { class: 'row' }, h('span', { class: 'ok' }, '✓'),
      h('b', { class: 'grow' }, s.name), h('span', { class: 'muted small' }, s.paired ? 'paired with the hub' : 'adopt it in the dashboard')))),
    h('div', { class: 'actions' }, state.hubURL && h('button', { class: 'primary', onclick: () => api.OpenURL(state.hubURL) }, 'Open the dashboard'),
      h('button', { onclick: () => window.runtime.Quit() }, 'Close')));
}

ready().then(() => {
  for (const name of ['hub:line', 'image:progress', 'card:progress']) on(name, (x) => listeners[name] && listeners[name](x));
  return api.Info();
}).then((info) => {
  state.info = info;
  $('version').textContent = ` · ${info.version}${info.dev ? ' · developer mode' : ''}`;
  welcome();
});
