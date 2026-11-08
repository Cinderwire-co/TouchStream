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
  plain: false,   // writing plain cards, for spokes that go to someone else (no hub, no name)
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
const PLAIN_STEPS = ['Plain cards', 'Cards', 'Done'];
function rail(now) {
  const steps = state.plain ? PLAIN_STEPS : STEPS;
  if (state.plain) now = Math.max(0, now - 3);
  $('steps').replaceChildren(...steps.map((name, i) => h('li', { class: i < now ? 'done' : i === now ? 'now' : '' },
    h('i', {}, i < now ? '✓' : String(i + 1)), name)));
}
function problem(err) {
  return h('div', { class: 'panel bad' }, h('p', {}, String(err && err.message || err).replace(/^Error: /, '')));
}

// Events go to whichever page is listening now
const listeners = {};

// ---- 1. Welcome ----
function welcome() {
  state.plain = false;
  rail(-1);
  page(
    h('h1', {}, 'Set up TouchStream'),
    h('p', { class: 'lead' }, 'This puts the hub on this computer, then adds your spokes: it pairs one that came ready to go, or writes a microSD card for a PicoKVM you bought yourself. Either way it shows up in the dashboard, ready to go live.'),
    h('div', { class: 'actions' }, h('button', { class: 'primary', onclick: findHub }, 'Get started'),
      h('span', { class: 'spacer' }), h('button', { class: 'link', onclick: plainCards }, 'Setting up spokes to sell or give away?')));
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
      h('div', { class: 'actions' }, h('button', { class: 'primary', onclick: () => password() }, 'Continue'),
        h('button', { onclick: installHub }, 'Update it'),
        h('span', { class: 'spacer' }), h('button', { class: 'link', onclick: remoteHub }, 'Use a hub on another computer')),
      h('p', { class: 'small' }, h('button', { class: 'link', onclick: removeHub }, 'Remove the hub from this computer')));
    return;
  }
  page(
    h('h1', {}, found.installed ? 'The hub isn’t running' : 'Install the hub'),
    found.installed
      ? h('p', { class: 'lead' }, 'The hub is installed on this computer, but it isn’t answering. Installing it again starts it, and keeps your settings and recordings.')
      : h('p', { class: 'lead' }, 'The hub runs your schedule, records every broadcast, and is where you watch and edit. It installs everything it needs, and starts with this computer from now on.'),
    h('div', { class: 'actions' }, h('button', { class: 'primary', onclick: installHub }, found.installed ? 'Start it again' : 'Install the hub'),
      h('span', { class: 'spacer' }), h('button', { class: 'link', onclick: remoteHub }, 'The hub is on another computer')),
    found.installed && h('p', { class: 'small' }, h('button', { class: 'link', onclick: removeHub }, 'Remove the hub from this computer')));
}

// ---- Removing the hub ----
function removeHub() {
  rail(0);
  const trash = state.info.os === 'windows' ? 'Recycle Bin' : 'Trash';
  const everything = h('input', { type: 'checkbox' });
  const sure = h('label', { class: 'check', hidden: true }, h('input', { type: 'checkbox', onchange: (e) => { go.disabled = !e.target.checked; } }),
    h('span', {}, 'Yes, my recordings and YouTube connections go too'));
  const go = h('button', { class: 'primary', onclick: () => uninstall(everything.checked) }, 'Remove the hub');
  everything.addEventListener('change', () => {
    sure.hidden = !everything.checked;
    sure.querySelector('input').checked = false;
    go.className = everything.checked ? 'danger' : 'primary';
    go.textContent = everything.checked ? 'Remove everything' : 'Remove the hub';
    go.disabled = everything.checked;
  });
  page(
    h('h1', {}, 'Remove the hub'),
    h('p', { class: 'lead' }, 'The hub stops, stops starting with this computer, and leaves ', state.info.os === 'windows' ? 'the Start menu' : 'Applications',
      '. Your spokes stay as they are: they wait for a hub until you install one again.'),
    h('div', { class: 'panel' },
      h('label', { class: 'check' }, everything, h('span', {}, 'Remove its settings and recordings too',
        h('div', { class: 'muted small' }, `The dashboard password, connected YouTube channels, schedule and the recordings in ${state.info.os === 'windows' ? 'Videos' : 'Movies'} › TouchStream go to the ${trash}. Without this, they stay for a hub installed again.`))),
      sure),
    h('div', { class: 'actions' }, go, h('button', { onclick: findHub }, 'Back')));
}

async function uninstall(everything) {
  rail(0);
  const stepsLog = h('div', { class: 'steps-log' });
  page(h('h1', {}, 'Removing the hub'), stepsLog);
  let current = null;
  listeners['hub:line'] = (line) => {
    if (!line.step) return;
    if (current) current.replaceChildren(h('span', { class: 'tick ok' }, '✓'), current.dataset.text);
    current = h('div', { class: 'now', 'data-text': line.text }, spinner(), line.text);
    stepsLog.append(current);
  };
  try {
    await api.UninstallHub(everything);
    state.hub = null;
    state.hubURL = '';
    state.signedIn = false;
    page(
      h('h1', {}, 'The hub is removed'),
      h('p', { class: 'lead' }, everything
        ? `Its settings and recordings are in the ${state.info.os === 'windows' ? 'Recycle Bin' : 'Trash'} until you empty it. A recordings folder you chose yourself is left as it was.`
        : 'Its settings and recordings are still on this computer: install the hub again and it picks up where it left off.'),
      h('div', { class: 'actions' }, h('button', { class: 'primary', onclick: () => window.runtime.Quit() }, 'Close'),
        h('button', { onclick: findHub }, 'Install it again')));
  } catch (err) {
    page(h('h1', {}, 'The hub wasn’t removed'), problem(err),
      h('div', { class: 'actions' }, h('button', { class: 'primary', onclick: () => uninstall(everything) }, 'Try again'), h('button', { onclick: findHub }, 'Back')));
  } finally {
    delete listeners['hub:line'];
  }
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
// then on to next (pairing a spoke needs it)
function password(next = spokesAsk) {
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
      next();
    } catch (err) { status.replaceChildren(problem(err)); }
  };
  page(
    h('h1', {}, fresh ? 'Choose the dashboard’s password' : 'Sign in to the hub'),
    h('p', {}, fresh ? 'You’ll use it to open the dashboard, on this computer or any other. At least 8 characters.'
      : 'The dashboard’s password. Signing in lets the installer pair each spoke with the hub.'),
    h('label', {}, 'Password', pw), fresh && h('label', {}, 'Again', again), status,
    h('div', { class: 'actions' }, h('button', { class: 'primary', onclick: go }, 'Continue'),
      h('span', { class: 'spacer' }), !fresh && next === spokesAsk && h('button', { class: 'link', onclick: () => { state.signedIn = false; spokesAsk(); } }, 'Skip: I’ll adopt spokes in the dashboard')));
  for (const input of [pw, again]) input.addEventListener('keydown', (e) => e.key === 'Enter' && go());
}

// ---- 4. Spokes? Ready to go, or a PicoKVM of your own ----
function choice(title, body, onclick) {
  return h('button', { class: 'card choice', onclick }, h('div', { class: 'grow' }, h('b', {}, title), h('div', { class: 'muted small' }, body)), h('span', { class: 'go' }, '→'));
}

function spokesAsk() {
  rail(2);
  page(
    h('h1', {}, 'Where did your spoke come from?'),
    h('p', { class: 'lead' }, 'A spoke is the small box beside your camera or video switcher.'),
    h('div', { class: 'cards' },
      choice('Ready to go, from Cinderwire', 'TouchStream is already on it, with its 32 GB card in. Plug it in and pair it: there’s no card to write.', readySpokes),
      choice('A PicoKVM I bought myself', 'From Waveshare, Amazon or another shop. The installer writes a microSD card that puts TouchStream on it.', box)),
    h('div', { class: 'actions' }, h('button', { onclick: done }, 'No spokes now')));
}

// ---- 5. Spokes that came ready to go: plug in, pair, name ----
let watching = null;
function readySpokes() {
  if (!state.signedIn) return password(readySpokes);
  rail(3);
  clearInterval(watching);
  const art = h('div', { html: boxArt() });
  const found = h('div', { class: 'cards' });
  const fallback = h('div');
  const status = h('div');
  const naming = {};      // ip or id -> the name typed so far (kept as the list redraws)
  let known = null;       // the hub's spokes when this page opened: a new one paired with a code
  let code = null;
  const finish = h('button', { class: 'primary', onclick: () => { clearInterval(watching); done(); } }, 'Finish');
  const named = () => state.written.filter((s) => s && s.ready);

  const nameInput = (key, placeholder) => {
    const input = h('input', { type: 'text', placeholder, maxlength: '60', value: naming[key] || '' });
    input.addEventListener('input', () => { naming[key] = input.value; });
    return input;
  };
  const pair = async (s, button) => {
    button.disabled = true;
    try {
      const name = (naming[s.ip] || '').trim();
      known.push(await api.Adopt(s.ip, name));
      state.written.push({ name: name || s.name, paired: true, ready: true });
      status.replaceChildren();
      refresh();
    } catch (err) { button.disabled = false; status.replaceChildren(problem(err)); }
  };
  const keep = async (s, button) => {   // one paired with a code: name it
    button.disabled = true;
    try {
      const name = (naming[s.id] || '').trim();
      if (name) await api.NameSpoke(s.id, name);
      known.push(s.id);
      state.written.push({ name: name || s.name, paired: true, ready: true });
      refresh();
    } catch (err) { button.disabled = false; status.replaceChildren(problem(err)); }
  };
  const showCode = async (n) => {
    try { code = code || await api.PairingCode(); } catch (err) { return fallback.replaceChildren(problem(err)); }
    const address = n.hub && n.hub.ip ? `${n.hub.ip}:${n.hub.port}` : 'this computer’s IP address';
    fallback.replaceChildren(h('div', { class: 'panel' },
      h('b', {}, n.discovery === false ? 'Pair it on its own screen' : 'Not showing up? Pair it on its own screen'),
      h('ol', { class: 'how' },
        h('li', {}, 'On the spoke’s screen, tap anywhere, then ', h('b', {}, 'Connect to hub'), '.'),
        h('li', {}, 'Enter the hub’s address: ', h('b', { class: 'mono' }, address)),
        h('li', {}, 'Enter the code ', h('b', { class: 'mono' }, code.code), '. It works for 10 minutes.'))));
  };

  const draw = (n) => {
    const fresh = known ? n.spokes.filter((s) => !known.includes(s.id)) : [];
    const rows = [
      ...named().map((s) => h('div', { class: 'card' }, h('span', { class: 'ok' }, '✓'), h('div', { class: 'grow' }, h('b', {}, s.name), h('div', { class: 'muted small' }, 'Paired with the hub')))),
      ...fresh.map((s) => {
        const button = h('button', { class: 'primary' }, 'Save');
        button.addEventListener('click', () => keep(s, button));
        return h('div', { class: 'card found' }, h('span', { class: 'ok' }, '●'),
          h('div', { class: 'grow' }, h('b', {}, `${s.name} is paired`), h('div', { class: 'muted small' }, 'Name it for its room')),
          nameInput(s.id, 'Sanctuary'), button);
      }),
      ...n.found.map((s) => {
        const button = h('button', { class: 'primary' }, 'Pair');
        button.addEventListener('click', () => pair(s, button));
        return h('div', { class: 'card found' }, h('span', { class: 'ok' }, '●'),
          h('div', { class: 'grow' }, h('b', {}, s.name), h('div', { class: 'muted small mono' }, s.ip)),
          nameInput(s.ip, 'Name it: Sanctuary'), button);
      }),
    ];
    if (!n.found.length && !fresh.length && n.discovery !== false) {
      rows.push(h('div', { class: 'panel row' }, spinner(), h('span', { class: 'muted' }, named().length ? 'Looking for more spokes…' : 'Looking for it on the network… It shows up a minute or so after it’s plugged in.')));
    }
    // keep what's being typed: only redraw when the list itself changes
    const key = JSON.stringify([named().length, fresh.map((s) => s.id), n.found.map((s) => s.ip), n.discovery]);
    if (found.dataset.key !== key) { found.dataset.key = key; found.replaceChildren(...rows); }
    if (n.discovery === false && !code) showCode(n);
    finish.textContent = named().length ? 'Finish' : 'Skip: I’ll pair it later';
    finish.className = named().length ? 'primary' : '';
  };
  let last = null;
  const refresh = async () => {
    try {
      const n = await api.Nearby();
      last = { discovery: n.discovery, found: n.discovered || [], spokes: n.spokes || [], hub: n.hub };
      if (!known) known = last.spokes.map((s) => s.id);
      draw(last);
    } catch (err) { status.replaceChildren(problem(err)); }
  };

  page(
    h('h1', {}, 'Plug in your spoke'),
    h('div', { class: 'box wide' }, art.firstElementChild, h('ol', { class: 'how' },
      h('li', {}, 'Plug HDMI from your camera or switcher into ', h('b', {}, 'HDMI IN'), '.'),
      h('li', {}, 'Plug Ethernet into the same network as the hub.'),
      h('li', {}, 'Plug in power (USB-C) last. Its screen lights up with its own name, like ', h('b', {}, 'Spoke 4F2A'), '.'))),
    found, status, fallback,
    h('div', { class: 'actions' }, finish, h('span', { class: 'spacer' }),
      h('button', { class: 'link', onclick: () => last && showCode(last) }, 'Pair it on its own screen instead')));
  refresh();
  watching = setInterval(refresh, 2000);
}

// ---- Plain cards: for spokes that go to someone else ----
function plainCards() {
  state.plain = true;
  state.names = [''];
  state.index = 0;
  state.written = [];
  rail(3);
  page(
    h('h1', {}, 'Spokes to sell or give away'),
    h('p', { class: 'lead' }, 'Plain cards put TouchStream on a PicoKVM with no hub and no name of yours: each box calls itself by its chip, like ', h('b', {}, 'Spoke 4F2A'), ', until its new owner pairs it and names it.'),
    h('div', { class: 'panel' }, h('ol', { class: 'how' },
      h('li', {}, 'Write a card, and put it in the box while it’s unplugged.'),
      h('li', {}, 'Plug in power. It puts TouchStream on the box, restarts, and keeps the card for its recordings: leave it in.'),
      h('li', {}, 'Test it if you like: pair it with your own hub, then remove it from the hub. It goes back to waiting for a hub, with its own name.'),
      h('li', {}, 'Ship it with the card in. Its owner runs this installer and chooses ', h('b', {}, 'Ready to go'), '.'))),
    h('div', { class: 'actions' }, h('button', { class: 'primary', onclick: getImage }, 'Write plain cards'),
      h('button', { onclick: welcome }, 'Back')));
}

// ---- 5. Is it the right box? ----
function box() {
  rail(3);
  const have = h('input', { type: 'checkbox' });
  const cards = h('input', { type: 'checkbox' });
  const next = h('button', { class: 'primary', onclick: names, disabled: true }, 'Continue');
  const check = () => { next.disabled = !(have.checked && cards.checked); };
  have.addEventListener('change', check);
  cards.addEventListener('change', check);
  const art = h('div', { html: boxArt() });
  page(
    h('h1', {}, 'Is this your box?'),
    h('div', { class: 'box' }, art.firstElementChild, h('div', {},
      h('p', { class: 'lead' }, 'TouchStream spokes run on one box: the ', h('b', {}, 'Luckfox PicoKVM'), ', the version with the ', h('b', {}, '1.54-inch screen'), ' on top.'),
      h('p', { class: 'small' }, 'It’s a small black box, about 2 inches across, with HDMI IN, Ethernet and USB-C on one side, and a microSD slot with RESET and BOOT buttons on the other.'))),
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
const cardName = () => (state.plain ? `card ${state.index + 1}` : state.names[state.index]);
function card() {
  rail(4);
  const name = cardName();
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
      h('div', { class: 'actions' }, h('button', { class: 'primary', onclick: () => write(c) }, state.plain ? 'Erase it and write a plain card' : `Erase it and write ${name}`))));
  };
  page(
    h('h1', {}, state.plain ? `Plain card ${state.index + 1}` : state.names.length > 1 ? `Card ${state.index + 1} of ${state.names.length}: ${name}` : `${name}’s card`),
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
  const name = cardName();
  const bar = h('i', { style: 'width:0%' });
  const text = h('p', { class: 'small' }, state.info.os === 'windows'
    ? 'Windows will ask for permission: writing a card needs it.' : 'Your Mac will ask for your password: writing a card needs it.');
  page(h('h1', {}, state.plain ? `Writing plain ${name}` : `Writing ${name}’s card`), h('div', { class: 'bar' }, bar), text,
    h('p', { class: 'small muted' }, 'Leave the card in until it’s done. Then every byte is read back and checked.'));
  listeners['card:progress'] = (p) => {
    bar.style.width = `${percent(p)}%`;
    text.textContent = p.phase === 'checking' ? `Checking… ${percent(p)}%` : p.phase === 'writing' ? `Writing… ${percent(p)}%` : text.textContent;
  };
  try {
    const result = state.plain ? await api.WritePlainCard(c) : await api.WriteCard(c, name);
    state.written[state.index] = { name, ...result };
    if (state.plain) return plainWritten(result);
    const last = state.index === state.names.length - 1;
    const art = h('div', { html: boxArt({ screen: 'off', card: true }) });
    page(
      h('h1', {}, `${name}’s card is ready`),
      h('div', { class: 'panel' }, h('div', { class: 'row' }, h('span', { class: 'ok' }, '✓'),
        h('div', {}, h('b', {}, 'Written and checked'),
          h('div', { class: 'muted small' }, result.paired ? 'Paired with the hub: it shows up in the dashboard by itself.' : 'Adopt it in the dashboard, under Add spoke, when it’s on.')))),
      h('div', { class: 'box wide' }, art.firstElementChild, h('ol', { class: 'how' },
        h('li', {}, 'Take the card out of this computer.'),
        h('li', {}, 'Unplug ', h('b', {}, `${name}’s`), ' box, if it’s on.'),
        h('li', {}, 'Put the card in the slot beside ', h('b', {}, 'RESET'), ' and ', h('b', {}, 'BOOT'), ', gold contacts first, all the way in.'),
        h('li', {}, 'Plug the box back in. It starts from the card, puts TouchStream on the box, restarts, and keeps the card for its recordings: leave it in.'))),
      result.note && h('p', { class: 'small' }, result.note),
      h('div', { class: 'actions' }, h('button', { class: 'primary', onclick: () => { if (last) done(); else { state.index += 1; card(); } } },
        last ? 'Finish' : `Next: ${state.names[state.index + 1]}’s card`)));
  } catch (err) {
    page(h('h1', {}, state.plain ? `Plain ${name} wasn’t written` : `${name}’s card wasn’t written`), problem(err),
      h('div', { class: 'actions' }, h('button', { class: 'primary', onclick: card }, 'Try again')));
  } finally {
    delete listeners['card:progress'];
  }
}

function plainWritten(result) {
  const art = h('div', { html: boxArt({ screen: 'off', card: true }) });
  page(
    h('h1', {}, `Plain card ${state.index + 1} is ready`),
    h('div', { class: 'box wide' }, art.firstElementChild, h('ol', { class: 'how' },
      h('li', {}, 'Put it in an unplugged box, in the slot beside ', h('b', {}, 'RESET'), ' and ', h('b', {}, 'BOOT'), ', gold contacts first.'),
      h('li', {}, 'Plug in power. TouchStream installs itself and restarts, then the screen shows the box’s own name.'),
      h('li', {}, 'Leave the card in: it’s the spoke’s 32 GB for recordings.'))),
    result.note && h('p', { class: 'small' }, result.note),
    h('div', { class: 'actions' }, h('button', { class: 'primary', onclick: () => { state.index += 1; card(); } }, 'Write another'),
      h('button', { onclick: done }, 'Finish')));
}

// ---- 9. Done ----
function done() {
  clearInterval(polling);
  clearInterval(watching);
  rail(6);
  const spokes = state.written.filter(Boolean);
  const cards = spokes.filter((s) => !s.ready);
  if (state.plain) {
    return page(
      h('h1', {}, `${spokes.length} plain card${spokes.length === 1 ? '' : 's'} written`),
      h('p', { class: 'lead' }, 'Each box installs TouchStream from its card the first time it’s plugged in. Ship them with the card in: their owners pair them with the installer’s Ready to go choice, or Add spoke in the dashboard.'),
      h('div', { class: 'actions' }, h('button', { class: 'primary', onclick: () => window.runtime.Quit() }, 'Close'), h('button', { onclick: welcome }, 'Back to the start')));
  }
  page(
    h('h1', {}, 'TouchStream is set up'),
    cards.length ? h('p', { class: 'lead' }, 'Put each card in its box while it’s unplugged, then plug in HDMI from your camera or switcher, Ethernet, and power last. In a minute or so it shows up in the dashboard.')
      : spokes.length ? h('p', { class: 'lead' }, 'Your spokes are paired with the hub. Open the dashboard to schedule your first broadcast.')
      : h('p', { class: 'lead' }, 'Run the installer again whenever you’re adding spokes, or use Add spoke in the dashboard.'),
    spokes.length > 0 && h('div', { class: 'summary' }, spokes.map((s) => h('div', { class: 'row' }, h('span', { class: 'ok' }, '✓'),
      h('b', { class: 'grow' }, s.name), h('span', { class: 'muted small' }, s.paired ? 'paired with the hub' : 'adopt it in the dashboard')))),
    h('div', { class: 'actions' }, state.hubURL && h('button', { class: 'primary', onclick: async () => { await api.OpenURL(state.hubURL); window.runtime.Quit(); } }, 'Open the dashboard'),
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
