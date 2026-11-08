// Scheduling: agenda, broadcast editor (one-off or recurring), connected YouTube channels
import { html, useState, useEffect } from '../vendor/preact-htm.module.js';
import { api, bytes, clock, dayLabel, hubEnv, reveal, revealLabel, until, usePoll, useNow, MARK } from './lib.js';
import { Avatar, Icon, IconButton, Modal, Preview, Switch } from './ui.js';
import { Chips, DateField, Select, TimeField, isoDate, joinTime, lengthLabel, splitTime, weekStart } from './fields.js';
import { ask, confirmAction, toast } from './dialogs.js';
import { fmt } from './editor.js';

const PRIVACY = [['public', 'Public'], ['unlisted', 'Unlisted'], ['private', 'Private']];
const LATENCY = [['normal', 'Normal', 'Best quality'], ['low', 'Low'], ['ultraLow', 'Ultra-low', 'No captions']];
const KIDS = [['no', "No, it's not made for kids"], ['yes', "Yes, it's made for kids"]];

// ---- Recurring broadcasts ----
// A rule is {days: weekdays 0-6 with Monday first (as the hub counts), weeks: 'every' | 'other' | [1-4, -1 for last],
// months: 1-12, none meaning every month}
const dateOf = (iso) => new Date(`${iso}T12:00`);
const hubDay = (iso) => (dateOf(iso).getDay() + 6) % 7;                   // Monday 0 ... Sunday 6
const dayName = (d, style = 'long') => new Date(2023, 0, 2 + d).toLocaleDateString([], { weekday: style });   // 2 Jan 2023 was a Monday
const monthName = (m, style = 'short') => new Date(2023, m - 1, 1).toLocaleDateString([], { month: style });
const ORDINALS = { 1: '1st', 2: '2nd', 3: '3rd', 4: '4th', '-1': 'last' };
const joinAnd = (items) => (items.length < 2 ? items.join('') : `${items.slice(0, -1).join(', ')} & ${items[items.length - 1]}`);
/** Days in the order this browser's weeks run (Sunday first in the US) */
const weekOrder = () => Array.from({ length: 7 }, (_, i) => (weekStart() + 6 + i) % 7);

function monthsText(months) {
  // Runs of three or more read as ranges: "Sep–Dec, Jan–May"
  const runs = [];
  for (const m of [...months].sort((x, y) => x - y)) {
    const last = runs[runs.length - 1];
    if (last && m === last[1] + 1) last[1] = m; else runs.push([m, m]);
  }
  return runs.map(([a, b]) => (b - a >= 2 ? `${monthName(a)}–${monthName(b)}` : a === b ? monthName(a)
    : `${monthName(a)}, ${monthName(b)}`)).join(', ');
}

/** "Every Sunday", "Weekdays", "1st & 3rd Wednesday", "Every other Sun & Wed · Jun–Aug" */
export function repeatLabel(series) {
  const r = series.repeat;
  const days = weekOrder().filter((d) => r.days.includes(d));
  const key = [...r.days].sort().join();
  const many = days.length > 1;
  const names = joinAnd(days.map((d) => dayName(d, many ? 'short' : 'long')));
  let text = key === '0,1,2,3,4,5,6' ? 'Every day' : key === '0,1,2,3,4' ? 'Weekdays' : key === '5,6' ? 'Weekends' : names;
  if (r.weeks === 'other') text = `Every other ${text === names ? names : text.toLowerCase()}`;
  else if (Array.isArray(r.weeks)) text = `${joinAnd(r.weeks.map((w) => ORDINALS[w]))} ${names}`.replace(/^last/, 'Last');
  else if (!many && text === names) text = `Every ${names}`;
  return r.months.length ? `${text} · ${monthsText(r.months)}` : text;
}

/** Whether a rule broadcasts on a date (mirrors the hub's series.matches). */
function onRule(rule, first, until, iso) {
  if (iso < first || (until && iso > until)) return false;
  const d = dateOf(iso);
  if (!rule.days.includes(hubDay(iso)) || (rule.months.length && !rule.months.includes(d.getMonth() + 1))) return false;
  if (rule.weeks === 'other') return Math.floor(Math.round((d - dateOf(first)) / 86400000) / 7) % 2 === 0;
  if (Array.isArray(rule.weeks)) {
    const last = new Date(d.getFullYear(), d.getMonth(), d.getDate() + 7).getMonth() !== d.getMonth();
    return rule.weeks.includes(Math.floor((d.getDate() - 1) / 7) + 1) || (rule.weeks.includes(-1) && last);
  }
  return true;
}

/** The next few dates a rule gives, from its first date. */
function nextDates(rule, first, until, count = 4) {
  const out = [];
  for (let d = dateOf(first), i = 0; out.length < count && i < 800; i += 1, d.setDate(d.getDate() + 1)) {
    if (onRule(rule, first, until, isoDate(d))) out.push(isoDate(d));
  }
  return out;
}

/** The title with its date, as the hub writes it for each broadcast of a series. */
const withDate = (base, iso) => `${base} · ${dateOf(iso).toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' })}`;
const stripDate = (title, iso) => (title.endsWith(withDate('', iso)) ? title.slice(0, -withDate('', iso).length) : null);

const WEEK_CHOICES = [['every', 'Every week'], ['other', 'Every other week'], ['nth', 'Certain weeks of the month']];

/** Picks the days, weeks and months a series runs on. */
function RepeatRule({ rule, first, until, onChange, onUntil }) {
  const set = (patch) => onChange({ ...rule, ...patch });
  const upcoming = rule.days.length ? nextDates(rule, first, until) : [];
  return html`<div class="rule">
    <div class="field"><span>On</span>
      <${Chips} label="Days of the week" value=${rule.days} onChange=${(days) => set({ days })}
        options=${weekOrder().map((d) => [d, dayName(d, 'short'), dayName(d)])} />
      ${!rule.days.length && html`<span class="error small">Pick at least one day.</span>`}</div>
    <div class="field"><span>Weeks</span>
      <${Select} label="Weeks" value=${Array.isArray(rule.weeks) ? 'nth' : rule.weeks} options=${WEEK_CHOICES}
        onChange=${(v) => set({ weeks: v === 'nth' ? [Math.floor((dateOf(first).getDate() - 1) / 7) + 1] : v })} />
      ${Array.isArray(rule.weeks) && html`<${Chips} label="Weeks of the month" value=${rule.weeks}
        onChange=${(weeks) => weeks.length && set({ weeks })}
        options=${[1, 2, 3, 4, -1].map((w) => [w, ORDINALS[w][0].toUpperCase() + ORDINALS[w].slice(1)])} />`}</div>
    <div class="field"><span>Months · ${rule.months.length ? monthsText(rule.months) : 'every month'}</span>
      <${Chips} label="Months" value=${rule.months} onChange=${(months) => set({ months: months.length === 12 ? [] : months })}
        options=${Array.from({ length: 12 }, (_, i) => [i + 1, monthName(i + 1), monthName(i + 1, 'long')])} /></div>
    <div class="field"><span>Until</span>
      ${until ? html`<div class="row-tight"><${DateField} label="Last date" value=${until} onChange=${onUntil} />
          <${IconButton} icon="close" label="No end date" onClick=${() => onUntil(null)} /></div>`
        : html`<${Select} label="Until" value="never" options=${[['never', 'No end date'], ['on', 'A date…']]}
            onChange=${() => { const d = dateOf(first); d.setMonth(d.getMonth() + 3); onUntil(isoDate(d)); }} />`}</div>
    ${upcoming.length > 0 && html`<p class="muted small">${repeatLabel({ repeat: rule })}. Next: ${
      upcoming.map((d) => dateOf(d).toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' })).join(', ')}…</p>`}
  </div>`;
}

// ---- Connected channels ----
function ConnectChannelModal({ onClose }) {
  const [flow, setFlow] = useState(null);
  const [state, setState] = useState({ status: 'starting' });
  useEffect(() => {
    api('POST', '/api/youtube/connect').then(setFlow).catch((e) => setState({ status: 'error', error: e.message }));
  }, []);
  usePoll(async () => {
    if (!flow || !['starting', 'pending'].includes(state.status)) return;
    const result = await api('GET', `/api/youtube/connect/${flow.id}`);
    setState(result);
    if (result.status === 'connected') setTimeout(onClose, 1500);
  }, 2000, [flow, state.status]);
  return html`<${Modal} title="Connect a YouTube channel" onClose=${onClose}>
    ${state.status === 'error' || state.status === 'denied' || state.status === 'expired'
      ? html`<p class="error">${state.status === 'denied' ? 'Access was declined.' : state.status === 'expired'
        ? 'The code expired. Close this and try again.' : state.error}</p>`
      : state.status === 'connected' ? html`<p><b>${state.channel.title}</b> is connected.</p>`
      : html`<div class="form">
        <ol class="steps">
          <li>On any phone or computer, open <a href=${flow ? flow.verification_url : '#'} target="_blank" rel="noopener">
            ${flow ? flow.verification_url.replace('https://', '') : '…'}</a></li>
          <li>Enter this code, then pick the channel (or Brand Account) to connect.</li>
        </ol>
        <div class="code">${flow ? flow.user_code : '········'}</div>
        <p class="muted small" style="text-align:center">Waiting for you to approve…</p>
      </div>`}
  </${Modal}>`;
}

export function ChannelsStrip({ channels, configured, refresh }) {
  const [connecting, setConnecting] = useState(false);
  return html`<div class="channels">
    <span class="eyebrow">YouTube channels</span>
    ${channels.map((c) => html`<span class="channel" title=${c.error || c.title}>
      <${Avatar} channel=${c} /> ${c.title} ${c.error && html`<span class="dot bad"></span>`}</span>`)}
    ${configured
      ? html`<button class="btn ghost small" onClick=${() => setConnecting(true)}><${Icon} name="plus" size=${14} /> Connect channel</button>`
      : html`<span class="muted small grow">Optional. Without YouTube, every broadcast is still recorded here to download
          and upload yourself.</span><a class="btn ghost small" href="#/settings">Set up YouTube</a>`}
    ${connecting && html`<${ConnectChannelModal} onClose=${() => { setConnecting(false); refresh(); }} />`}
  </div>`;
}

// ---- Broadcast editor ----
/** The form's starting values: a new broadcast, an existing one, or a planned date of a series. */
function initial({ event, planned, spokes, channels, defaultSpoke }) {
  const series = (event && event.series) || (planned && planned.series);
  if (event || planned) {
    const src = event || planned;
    const { date, minutes } = splitTime(src.start_at);
    const day = event ? event.occurrence || date : planned.date;
    const base = series ? stripDate(src.title, day) : null;
    const from = event || series;
    return {
      title: base ?? src.title, add_date: base != null, date, start: minutes,
      end: src.end_at ? splitTime(src.end_at).minutes : null,
      description: from.description, spoke_id: src.spoke_id, privacy: from.privacy, latency: from.latency,
      made_for_kids: from.made_for_kids, auto_start: from.auto_start,
      channel_ids: event ? event.targets.map((t) => t.channel_id) : series.channel_ids,
      rule: series ? series.repeat : null, until: series ? series.until_date : null,
    };
  }
  const soon = new Date(Math.ceil(Date.now() / 900000) * 900000 + 900000);   // next quarter hour, plus one
  const start = soon.getHours() * 60 + soon.getMinutes();
  return { title: '', add_date: true, date: isoDate(soon), start, end: (start + 90) % 1440, description: '',
    spoke_id: defaultSpoke || (spokes[0] && spokes[0].id), privacy: 'public', latency: 'normal', made_for_kids: false,
    auto_start: true, channel_ids: channels.map((c) => c.id), rule: null, until: null };
}

/** A day in a sentence: "today", "tomorrow", "yesterday", or "on Sunday, Nov 15". */
const dayPhrase = (t) => { const d = dayLabel(t); return d.includes(' ') ? `on ${d}` : d.toLowerCase(); };

export function BroadcastModal({ event, planned, spokes, channels, defaultSpoke, onClose }) {
  const series = (event && event.series) || (planned && planned.series);
  const [f, setF] = useState(() => initial({ event, planned, spokes, channels, defaultSpoke }));
  const [start] = useState(f);   // to tell what changed
  const [image, setImage] = useState(null);
  const [error, setError] = useState('');
  const [step, setStep] = useState('');   // what saving is doing now ('': not saving)
  const set = (k, v) => setF((old) => ({ ...old, [k]: v }));
  const toggleChannel = (id) => set('channel_ids', f.channel_ids.includes(id)
    ? f.channel_ids.filter((c) => c !== id) : [...f.channel_ids, id]);
  const preview = image ? URL.createObjectURL(image) : (event && event.thumbnail_url) || (series && series.thumbnail_url);
  const repeating = !!f.rule;
  const length = f.end == null ? null : (f.end - f.start + 1440) % 1440 || 1440;
  const fullTitle = repeating && f.add_date ? withDate(f.title, f.date) : f.title;
  const setRepeating = (on) => set('rule', on ? (start.rule || { days: [hubDay(f.date)], weeks: 'every', months: [] }) : null);

  const save = async (e) => {
    e.preventDefault();
    setError('');
    if (repeating && !f.rule.days.length) return setError('Pick at least one day of the week for it to repeat on.');
    const startAt = joinTime(f.date, f.start);
    const common = { description: f.description, privacy: f.privacy, latency: f.latency, made_for_kids: f.made_for_kids,
      auto_start: f.auto_start, channel_ids: f.channel_ids };
    const one = { ...common, title: fullTitle, spoke_id: f.spoke_id, start_at: startAt, end_at: length ? startAt + length * 60 : null };
    const rule = { ...common, title: f.title, add_date: f.add_date, repeat: f.rule, start_minutes: f.start,
      length_minutes: length, until_date: f.until, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone };
    const thumb = async (path) => {
      if (!image) return;
      setStep(f.channel_ids.length ? 'Uploading the thumbnail to YouTube…' : 'Saving the thumbnail…');
      await api('PUT', `${path}/thumbnail`, null, { raw: image });
    };
    const ruleChanged = JSON.stringify(f.rule) !== JSON.stringify(start.rule) || f.until !== start.until;
    let scope = 'this';
    if (series && !repeating) {
      const stop = await confirmAction({ title: 'Stop repeating?', confirm: 'Stop repeating', danger: true,
        message: `This broadcast stays. The series' other upcoming broadcasts are deleted, from YouTube too.` });
      if (!stop) return;
      scope = 'stop';
    } else if (series && ruleChanged) {
      scope = 'all';   // a new rule is for the whole series
    } else if (series && f.date !== start.date) {
      scope = 'this';  // moving one date moves only that broadcast
    } else if (series) {
      scope = await ask({ title: 'Change which broadcasts?', message: `This broadcast is part of a series: ${repeatLabel(series)}.`,
        buttons: [{ label: 'Cancel', value: null }, { label: 'Only this one', value: 'this' },
          { label: 'All upcoming', value: 'all', kind: 'primary' }] });
      if (!scope) return;
    }
    setStep(!f.channel_ids.length ? 'Saving…' : repeating && !series ? 'Setting up the series on YouTube…'
      : event || series ? 'Updating it on YouTube…' : 'Creating the broadcast on YouTube…');
    try {
      let saved;
      if (!series && !repeating) {
        saved = event ? await api('PATCH', `/api/events/${event.id}`, one) : await api('POST', '/api/events', one);
        await thumb(`/api/events/${saved.id}`);
      } else if (!series) {
        // A new series, or this broadcast becomes the first of one
        const made = await api('POST', '/api/series', { ...rule, spoke_id: f.spoke_id, first_date: f.date, from_event: event && event.id });
        await thumb(`/api/series/${made.id}`);
      } else if (scope === 'all') {
        await api('PATCH', `/api/series/${series.id}`, { ...rule, ...(f.date !== start.date && { first_date: f.date }),
          attach: event && event.id });
        await thumb(`/api/series/${series.id}`);
      } else {
        // Only this date (or the one that stays when the series stops)
        saved = event ? await api('PATCH', `/api/events/${event.id}`, one)
          : await api('POST', `/api/series/${series.id}/dates/${planned.date}`, one);
        await thumb(`/api/events/${saved.id}`);
        if (scope === 'stop') await api('DELETE', `/api/series/${series.id}?keep=${saved.id}`);
      }
      const failed = saved ? saved.targets.filter((t) => t.error) : [];
      if (failed.length) { setError(`YouTube: ${failed[0].error}`); setStep(''); return; }
      const name = (spokes.find((s) => s.id === f.spoke_id) || {}).name || 'The spoke';
      let copy = '';
      if (saved && saved.on_spoke != null && saved.targets.length) {
        // The spoke picks up its copy with its next check-in, every few seconds
        if (!saved.spoke_online) copy = ` ${name} is offline: it gets its copy when it's back.`;
        else {
          setStep(`Sending it to ${name}…`);
          for (let tries = 0; !saved.on_spoke && tries < 8; tries++) {
            await new Promise((resolve) => setTimeout(resolve, 1250));
            saved = await api('GET', `/api/events/${saved.id}`);
          }
          copy = saved.on_spoke ? ` ${name} has a copy, to stream straight to YouTube if the hub is down.` : '';
        }
      }
      toast(`${event || series ? 'Saved' : 'Scheduled'}: ${repeating ? f.title : fullTitle}, ${dayPhrase(startAt)} at ${
        clock(startAt)}.${copy}`, 'ok');
      onClose();
    } catch (err) { setError(err.message); setStep(''); }
  };

  return html`<${Modal} title=${event || planned ? 'Edit broadcast' : 'Schedule a broadcast'} onClose=${onClose} wide>
    <form class="form" onSubmit=${save}>
      <div class="editor">
        <div class="form">
          <span class="eyebrow">Details</span>
          <label class="field"><span>Title</span>
            <input type="text" maxlength="100" required placeholder="Sunday Service" value=${f.title}
              onInput=${(e) => set('title', e.target.value)} /></label>
          <label class="field"><span>Description</span>
            <textarea rows="3" maxlength="5000" value=${f.description} onInput=${(e) => set('description', e.target.value)}></textarea></label>
          <label class="field"><span>Thumbnail · 1280×720 JPG or PNG, under 2 MB</span>
            <div class="thumb-drop" style=${preview ? `background-image:url(${preview})` : ''}>
              ${!preview && html`<span class="muted small">Choose an image</span>`}
              <input type="file" accept="image/jpeg,image/png" onChange=${(e) => setImage(e.target.files[0] || null)} />
            </div></label>
          <div class="form-row">
            <div class="field"><span>Visibility</span>
              <${Select} label="Visibility" value=${f.privacy} options=${PRIVACY} onChange=${(v) => set('privacy', v)} /></div>
            <div class="field"><span>Latency</span>
              <${Select} label="Latency" value=${f.latency} options=${LATENCY} onChange=${(v) => set('latency', v)} /></div>
          </div>
          <div class="field"><span>Made for kids?</span>
            <${Select} label="Made for kids" value=${f.made_for_kids ? 'yes' : 'no'} options=${KIDS}
              onChange=${(v) => set('made_for_kids', v === 'yes')} /></div>
        </div>
        <div class="form">
          <span class="eyebrow">When</span>
          <div class="field"><span>${repeating && !series ? 'First date' : 'Date'}</span>
            <${DateField} label="Date" value=${f.date} onChange=${(v) => set('date', v)} /></div>
          <div class="form-row tight">
            <div class="field"><span>Starts</span><${TimeField} label="Start time" value=${f.start}
              onChange=${(v) => setF((old) => ({ ...old, start: v, end: old.end == null ? null : (v + (old.end - old.start + 1440) % 1440) % 1440 }))} /></div>
            <div class="field"><span>Ends</span><${TimeField} label="End time" value=${f.end} after=${f.start}
              optional="No set end" onChange=${(v) => set('end', v)} /></div>
          </div>
          <div class="segmented wide" role="group" aria-label="Repeat">
            <button type="button" class=${repeating ? '' : 'on'} onClick=${() => setRepeating(false)}>Once</button>
            <button type="button" class=${repeating ? 'on' : ''} onClick=${() => setRepeating(true)}><${Icon} name="repeat" size=${14} /> Repeats</button>
          </div>
          ${repeating && html`<${RepeatRule} rule=${f.rule} first=${f.date} until=${f.until}
            onChange=${(v) => set('rule', v)} onUntil=${(v) => set('until', v)} />
            <div class="check-row">
              <div class="min0"><b>Add the date to each title</b>
                <div class="muted small ellipsis">${fullTitle || 'Title'}</div></div>
              <${Switch} checked=${f.add_date} onChange=${(v) => set('add_date', v)} label="Add the date to each title" />
            </div>`}
          <span class="eyebrow section-gap">Where</span>
          <div class="field"><span>Spoke</span>
            <${Select} label="Spoke" value=${f.spoke_id} disabled=${!!(planned || (event && (event.state !== 'scheduled' || series)))}
              onChange=${(v) => set('spoke_id', v)}
              options=${spokes.map((s) => [s.id, `${s.name}${s.location ? ` · ${s.location}` : ''}`])} /></div>
          <div class="field"><span>Stream to</span>
            ${channels.length === 0 ? html`<p class="muted small"><b>Record only.</b> The hub records this broadcast (plus
              the spoke's own copy) for you to download, edit and upload yourself. Connect YouTube channels in
              Settings to stream live too.</p>`
            : html`<div>${channels.map((c) => html`<label class="check">
                <input type="checkbox" checked=${f.channel_ids.includes(c.id)} onChange=${() => toggleChannel(c.id)} />
                <${Avatar} channel=${c} size=${22} /> ${c.title}</label>`)}</div>`}
            ${channels.length > 0 && f.channel_ids.length === 0 && html`<p class="muted small">No channels selected:
              recorded on the hub only (plus any stream keys switched on for this spoke).</p>`}
          </div>
          <div class="check-row">
            <div><b>Start automatically</b>
              <div class="muted small">The hub puts the spoke live at the start time${length ? ' and ends it after ' + lengthLabel(length) : ''}.</div></div>
            <${Switch} checked=${f.auto_start} onChange=${(v) => set('auto_start', v)} label="Start automatically" />
          </div>
          ${repeating && !series && html`<p class="muted small">Each date gets its own broadcast, on YouTube too, a week
            before it airs. Change one on its own, or the whole series, any time.</p>`}
        </div>
      </div>
      ${error && html`<p class="error">${error}</p>`}
      <div class="modal-foot">
        ${step && html`<span class="saving-step" role="status"><span class="spinner"></span>${step}</span>`}
        <button type="button" class="btn ghost" onClick=${onClose} disabled=${!!step}>Cancel</button>
        <button class="btn primary" disabled=${!!step}>${step ? 'Saving…' : event || planned ? 'Save changes' : 'Schedule'}</button>
      </div>
    </form>
  </${Modal}>`;
}

// ---- Recordings of a broadcast ----
const COPY_LABEL = { hub: 'Hub recording', spoke: 'Spoke recording' };

/** One Download menu per broadcast: the hub's copy, the spoke's copy (and parts, if any) and the edited video. */
export function DownloadMenu({ copies, edits = [] }) {
  const ready = edits.filter((e) => e.status === 'done' && e.output);
  if (!copies.length && !ready.length) return html`<span class="muted small">No recording</span>`;
  const count = { hub: 0, spoke: 0 };
  copies.forEach((c) => { count[c.source] += 1; });
  const seen = { hub: 0, spoke: 0 };
  return html`<details class="menu">
    <summary class="btn ghost small">Download <${Icon} name="chevron" size=${14} /></summary>
    <div class="menu-list">${copies.map((c) => {
      seen[c.source] += 1;
      const label = COPY_LABEL[c.source] + (count[c.source] > 1 ? ` · part ${seen[c.source]}` : '');
      return c.path
        ? html`<a href=${`/api/recordings/file/${c.path}`} download>${label}<span class="muted">${bytes(c.size)}</span></a>`
        : html`<span class="disabled">${label}<span class="muted">uploading… ${bytes(c.size)}</span></span>`;
    })}${ready.map((e) => html`<a href=${`/api/recordings/file/${e.output}`} download>Edited video<span class="muted">${fmt(e.duration)}</span></a>`)}</div>
  </details>`;
}

/** When a broadcast was on air: the hub's own times, else the span of its recordings. */
const aired = (e) => {
  const recs = e.recordings.filter((c) => c.start);
  return {
    start: e.started_at || (recs.length ? Math.min(...recs.map((c) => c.start)) : e.start_at),
    end: e.ended_at || (recs.length ? Math.max(...recs.map((c) => c.end)) : Date.now() / 1000),
  };
};
const duration = (e) => {
  const { start, end } = aired(e);
  const m = Math.round((end - start) / 60);
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`;
};

export function OtherRecordings({ items, spokes }) {
  if (!items.length) return null;
  const names = Object.fromEntries(spokes.map((s) => [s.id, s.name]));
  return html`<div>
    <h2 class="section-title">Other recordings</h2>
    <p class="muted small" style="margin-bottom:10px">Recordings that don't line up with a broadcast here (for example, older ones).</p>
    <div class="agenda">${items.map((c) => html`<div class="event ended">
      <div class="when"><b>${clock(c.start)}</b><span class="muted small">${dayLabel(c.start)}</span></div>
      <div class="thumb"><img src=${MARK} alt="" /></div>
      <div class="what"><b>${names[c.spoke_id] || 'Removed spoke'}</b><div class="meta">
        <span class=${`chip ${c.source === 'spoke' ? 'ember' : ''}`}>${COPY_LABEL[c.source]}</span></div></div>
      <div class="actions"><${DownloadMenu} copies=${[c]} /></div>
    </div>`)}</div>
  </div>`;
}

// ---- Agenda ----
/** Upcoming broadcasts plus the planned dates of recurring ones, in time order. */
export const withPlanned = (events, planned) => [...events,
  ...planned.map((p) => ({ ...p, id: `p${p.series_id}-${p.date}`, state: 'planned', targets: [], recordings: [], edits: [],
    thumbnail_url: p.series.thumbnail_url, auto_start: p.series.auto_start }))].sort((a, b) => a.start_at - b.start_at);

async function removeBroadcast(event, refresh) {
  const series = event.series;
  if (series && ['scheduled', 'planned'].includes(event.state)) {
    const choice = await ask({ title: `Delete "${event.title}"?`,
      message: `It's part of a series (${repeatLabel(series)}). Deleting removes it from YouTube too.`,
      buttons: [{ label: 'Cancel', value: null }, { label: 'All upcoming', value: 'all', kind: 'danger' },
        { label: 'Only this one', value: 'this', kind: 'danger' }] });
    if (choice === 'all') await api('DELETE', `/api/series/${series.id}`);
    else if (choice === 'this' && event.state === 'planned') await api('POST', `/api/series/${series.id}/skip`, { date: event.date });
    else if (choice === 'this') await api('DELETE', `/api/events/${event.id}`);
    return choice && refresh();
  }
  const ok = event.state === 'scheduled'
    ? await confirmAction({ title: `Delete "${event.title}"?`, message: 'It is also removed from YouTube.', confirm: 'Delete', danger: true })
    : await confirmAction({ title: `Delete "${event.title}"?`, confirm: 'Delete', danger: true,
      message: 'Its recordings, edited video and transcript are deleted from the hub. The video on YouTube isn\'t affected.' });
  if (ok) await api('DELETE', `/api/events/${event.id}${event.state === 'scheduled' ? '' : '?recordings_too=true'}`);
  if (ok) refresh();
}

const startNow = async (event, refresh) => (await confirmAction({ title: `Start "${event.title}" now?`, confirm: 'Start now' }))
  && api('POST', `/api/events/${event.id}/start`).then(refresh).catch(toast);
const endNow = async (event, refresh) => (await confirmAction({ title: `End "${event.title}"?`,
  message: 'The stream stops on every channel.', confirm: 'End broadcast', danger: true }))
  && api('POST', `/api/events/${event.id}/end`).then(refresh).catch(toast);

function StateChip({ event }) {
  return event.state === 'live' ? html`<span class="chip live">● Live</span>`
    : event.state === 'ended' ? html`<span class="chip">${event.started_at || event.recordings.some((c) => c.source === 'hub')
      ? `Aired · ${duration(event)}` : event.recordings.length ? `Aired on the spoke · ${duration(event)}` : 'Missed'}</span>`
    : event.state === 'planned' ? html`<span class="chip" title="Its broadcast is created, on YouTube too, a week before">Planned</span>`
    : event.spoke_removed ? html`<span class="chip bad" title="Its spoke was removed: edit it to move it to another one">Needs a spoke</span>`
    : html`<span class="chip ember">${event.auto_start ? `Starts ${until(event.start_at)}` : 'Manual start'}</span>`;
}

/** Whether the spoke has its own copy of an upcoming broadcast, to go straight to YouTube if the hub is down. */
function SpokeCopyChip({ event }) {
  if (event.on_spoke == null || event.state !== 'scheduled' || !event.targets.length) return null;
  return event.on_spoke
    ? html`<span class="chip ok" title=${`If the hub is down, ${event.spoke_name} streams it straight to YouTube`}>
        <${Icon} name="check" size=${12} /> Spoke has a copy</span>`
    : html`<span class="chip" title=${event.spoke_online ? 'It gets its copy with its next check-in, in a few seconds'
        : `${event.spoke_name} is offline: it gets its copy when it's back`}>Not on the spoke yet</span>`;
}

function EventRow({ event, spoke, showSpoke, onEdit, refresh }) {
  useNow(15000);
  const errors = event.targets.filter((t) => t.error);
  const hasCopy = event.recordings.some((c) => c.path);
  const page = event.state !== 'planned' && `#/broadcast/${event.id}`;
  return html`<div class=${`event ${event.state}`}>
    <div class="when"><b>${clock(event.started_at || event.start_at)}</b>${(event.ended_at || event.end_at)
      && html`<span class="muted small">to ${clock(event.ended_at || event.end_at)}</span>`}</div>
    <a class="thumb" href=${page || undefined} style=${event.thumbnail_url ? `background-image:url(${event.thumbnail_url})` : ''}>
      ${!event.thumbnail_url && html`<img src=${MARK} alt="" />`}</a>
    <div class="what">
      ${page ? html`<a class="title-link" href=${page}><b>${event.title}</b></a>` : html`<b>${event.title}</b>`}
      <div class="meta">
        <${StateChip} event=${event} />
        <${SpokeCopyChip} event=${event} />
        ${event.recordings_removed_at && !hasCopy && html`<span class="chip" title="Settings · Old recordings">Recordings removed</span>`}
        ${event.series && event.state !== 'ended' && html`<span class="chip" title=${event.detached ? 'Changed on its own' : ''}>
          <${Icon} name="repeat" size=${12} /> ${repeatLabel(event.series)}${event.detached ? ' · edited' : ''}</span>`}
        ${showSpoke && spoke && html`<a class="chip" href=${`#/spokes/${spoke.id}`}>${spoke.name}</a>`}
        ${showSpoke && !spoke && event.spoke_name && html`<span class="chip" title="This spoke was removed from the hub">${event.spoke_name} · removed</span>`}
        ${event.targets.map((t) => html`<span class="channel small" title=${t.error || t.channel}>
          <${Avatar} channel=${t} size=${18} /> ${t.channel}
          ${t.watch_url && html`<a href=${t.watch_url} target="_blank" rel="noopener" title="Watch"><${Icon} name="external" size=${13} /></a>`}
        </span>`)}
        ${event.targets.length === 0 && event.state !== 'planned' && html`<span class="muted small">${event.adhoc ? 'Went live from the dashboard' : 'Record only'}</span>`}
      </div>
      ${errors.length > 0 && html`<p class="error small">${errors[0].channel}: ${errors[0].error}</p>`}
    </div>
    <div class="actions">
      ${event.state === 'scheduled' && !event.spoke_removed && html`<button class="btn small" onClick=${() => startNow(event, refresh)}>
        <${Icon} name="play" size=${14} /> Start now</button>`}
      ${event.state === 'live' && html`<button class="btn danger small" onClick=${() => endNow(event, refresh)}>
        <${Icon} name="stop" size=${14} /> End</button>`}
      ${event.state === 'ended' && hasCopy && html`<a class="btn small" href=${`#/edit/${event.id}`}>
        <${Icon} name="scissors" size=${14} /> ${event.edits.length ? 'Edited' : 'Edit'}</a>`}
      ${event.state === 'ended' && hasCopy && html`<a class="btn ghost small" href=${`#/transcript/${event.id}`}>${
        !event.transcript ? 'Transcript' : event.transcript.status === 'done' ? 'Transcript ✓'
        : event.transcript.status === 'failed' ? 'Transcript !' : `Transcribing ${Math.round(event.transcript.progress * 100)}%`}</a>`}
      ${event.state === 'ended' && hasCopy && html`<a class="btn ghost small" href=${`#/edit/${event.id}/shorts`}>${
        event.shorts ? `Shorts · ${event.shorts}` : 'Shorts'}</a>`}
      ${['live', 'ended'].includes(event.state) && html`<${DownloadMenu} copies=${event.recordings} edits=${event.edits} />`}
      ${hubEnv.local && event.folder && html`<${IconButton} icon="folder" label=${revealLabel()} onClick=${() => reveal({ event_id: event.id })} />`}
      ${!['ended', 'live'].includes(event.state) && !event.adhoc && html`<${IconButton} icon="edit" label="Edit" onClick=${() => onEdit(event)} />`}
      <${IconButton} icon="trash" label="Delete" onClick=${() => removeBroadcast(event, refresh).catch(toast)} />
    </div>
  </div>`;
}

export function Agenda({ events, spokes, showSpoke, onEdit, refresh, empty }) {
  if (!events.length) return html`<div class="empty small-empty"><p class="muted">${empty}</p></div>`;
  const byId = Object.fromEntries(spokes.map((s) => [s.id, s]));
  const days = [];
  for (const e of events) {
    const label = dayLabel(e.start_at);
    if (!days.length || days[days.length - 1].label !== label) days.push({ label, events: [] });
    days[days.length - 1].events.push(e);
  }
  return html`<div class="agenda">${days.map((d) => html`<section>
    <h3 class="day">${d.label}</h3>
    ${d.events.map((e) => html`<${EventRow} key=${e.id} event=${e} spoke=${byId[e.spoke_id]} showSpoke=${showSpoke}
      onEdit=${onEdit} refresh=${refresh} />`)}
  </section>`)}</div>`;
}

/** Opens the broadcast editor for a broadcast or a planned date (null closes it). */
export const editorFor = (item, props) => item && html`<${BroadcastModal} ...${props}
  event=${item === 'new' || item.state === 'planned' ? null : item} planned=${item.state === 'planned' ? item : null} />`;

// ---- Schedule page ----
export function SchedulePage() {
  const [data, setData] = useState(null);
  const [events, setEvents] = useState({ events: [], planned: [], other_recordings: [] });
  const [editing, setEditing] = useState(null);   // null | 'new' | event | planned date
  const [filter, setFilter] = useState('');
  const [tab, setTab] = useState('upcoming');
  const refresh = async () => {
    const [d, ev] = await Promise.all([api('GET', '/api/dashboard'), api('GET', '/api/events')]);
    setData(d); setEvents(ev);
  };
  usePoll(refresh, 5000);
  if (!data) return null;
  const mine = (e) => !filter || e.spoke_id === filter;
  const upcoming = withPlanned(events.events.filter((e) => e.state !== 'ended' && mine(e)), events.planned.filter(mine));
  const past = events.events.filter((e) => e.state === 'ended' && mine(e)).reverse();
  return html`<div>
    <div class="page-head">
      <div><h1>Schedule</h1><p class="muted">Every broadcast lives here: what's coming up, and what aired with its
        recordings. Spokes keep a copy of their schedule and stream straight to YouTube if the hub is down.</p></div>
      <button class="btn primary" disabled=${!data.spokes.length} onClick=${() => setEditing('new')}>
        <${Icon} name="calendar" size=${16} /> Schedule broadcast</button>
    </div>
    <${ChannelsStrip} channels=${data.channels} configured=${data.youtube} refresh=${refresh} />
    <div class="toolbar">
      <div class="segmented">
        <button class=${tab === 'upcoming' ? 'on' : ''} onClick=${() => setTab('upcoming')}>Upcoming</button>
        <button class=${tab === 'past' ? 'on' : ''} onClick=${() => setTab('past')}>Past · ${past.length}</button>
      </div>
      ${data.spokes.length > 1 && html`<${Select} className="filter" label="Spoke" value=${filter} onChange=${setFilter}
        options=${[['', 'All spokes'], ...data.spokes.map((s) => [s.id, s.name])]} />`}
    </div>
    ${tab === 'upcoming'
      ? html`<${Agenda} events=${upcoming} spokes=${data.spokes} showSpoke onEdit=${setEditing} refresh=${refresh}
          empty="Nothing scheduled. Schedule a broadcast, once or every week, and the hub runs it for you." />`
      : html`<${Agenda} events=${past} spokes=${data.spokes} showSpoke onEdit=${setEditing} refresh=${refresh}
          empty="Nothing has aired yet. Past broadcasts and their recordings appear here." />
        <${OtherRecordings} items=${events.other_recordings.filter(mine)} spokes=${data.spokes} />`}
    ${editorFor(editing, { spokes: data.spokes, channels: data.channels, defaultSpoke: filter,
      onClose: () => { setEditing(null); refresh(); } })}
  </div>`;
}

// ---- One broadcast: its picture, when and where it goes, and what to do with it ----
const label = (list, value) => (list.find(([v]) => v === value) || [, value])[1];

export function BroadcastPage({ id }) {
  const [event, setEvent] = useState(null);
  const [data, setData] = useState(null);
  const [missing, setMissing] = useState('');
  const [editing, setEditing] = useState(false);
  useNow(15000);
  const refresh = async () => {
    try {
      const [d, e] = await Promise.all([api('GET', '/api/dashboard'), api('GET', `/api/events/${id}`)]);
      setData(d); setEvent(e);
    } catch (err) { setMissing(err.message); }
  };
  usePoll(refresh, 3000, [id]);
  if (missing && !event) return html`<div class="empty"><h2>${missing}</h2><a href="#/schedule">Back to the schedule</a></div>`;
  if (!event || !data) return null;
  const spoke = data.spokes.find((s) => s.id === event.spoke_id);
  const hasCopy = event.recordings.some((c) => c.path);
  const ended = event.state === 'ended';
  // Once it's aired, its recording plays here: the spoke's copy, which has no network gaps, else the hub's
  const recording = ended && (event.recordings.find((c) => c.path && c.source === 'spoke') || event.recordings.find((c) => c.path));
  const start = event.started_at || event.start_at;
  const end = event.ended_at || event.end_at;
  const preview = (on) => api('POST', `/api/spokes/${spoke.id}/mode`, { mode: on ? 'preview' : 'off' }).then(refresh).catch(toast);
  const details = [
    ['When', `${dayLabel(start)} · ${clock(start)}${end ? ` to ${clock(end)}` : ''}${
      event.state === 'scheduled' ? ` · ${until(event.start_at)}` : ended ? ` · ${duration(event)}` : ''}`],
    ['Repeats', event.series ? repeatLabel(event.series) : 'No'],
    ['Spoke', spoke ? html`<a href=${`#/spokes/${spoke.id}`}>${spoke.name}</a> · ${spoke.online ? 'online' : 'offline'}`
      : `${event.spoke_name || '—'} · removed`],
    !ended && ['Starts', event.auto_start ? 'By itself at the start time' : 'When you press Start now'],
    event.on_spoke != null && event.targets.length > 0 && ['Spoke’s copy', event.on_spoke
      ? 'Yes: if the hub is down, the spoke streams it straight to YouTube'
      : event.spoke_online ? 'Not yet: it gets it in a few seconds' : 'Not yet: it gets it when the spoke is back online'],
    ['Visibility', label(PRIVACY, event.privacy)],
    ['Latency', label(LATENCY, event.latency)],
    ['Made for kids', event.made_for_kids ? 'Yes' : 'No'],
  ].filter(Boolean);
  return html`<div>
    <a class="crumb" href="#/schedule"><${Icon} name="back" size=${15} /> Schedule</a>
    <div class="page-head">
      <div class="min0"><h1>${event.title}</h1>
        <div class="meta"><${StateChip} event=${event} /><${SpokeCopyChip} event=${event} />
          ${event.series && html`<span class="chip"><${Icon} name="repeat" size=${12} /> ${repeatLabel(event.series)}</span>`}</div></div>
      <div class="head-actions">
        ${event.state === 'scheduled' && !event.spoke_removed && html`<button class="btn primary" onClick=${() => startNow(event, refresh)}>
          <${Icon} name="play" size=${16} /> Start now</button>`}
        ${event.state === 'live' && html`<button class="btn danger" onClick=${() => endNow(event, refresh)}>
          <${Icon} name="stop" size=${16} /> End broadcast</button>`}
        ${ended && hasCopy && html`<a class="btn primary" href=${`#/edit/${event.id}`}><${Icon} name="scissors" size=${16} />
          ${event.edits.length ? 'Edited video' : 'Edit the video'}</a>`}
        ${event.state === 'scheduled' && !event.adhoc && html`<button class="btn" onClick=${() => setEditing(true)}>
          <${Icon} name="edit" size=${16} /> Edit</button>`}
        <${IconButton} icon="trash" label="Delete" onClick=${() => removeBroadcast(event, () => { location.hash = '#/schedule'; }).catch(toast)} />
      </div>
    </div>
    <div class="spoke-layout">
      <div class="panel flush">
        ${recording ? html`<div class="preview large"><video controls preload="metadata" poster=${event.thumbnail_url || undefined}
            src=${`/api/recordings/file/${recording.path}`}></video></div>`
          : spoke ? html`<${Preview} spoke=${spoke} large />`
          : html`<div class="preview large"><div class="placeholder"><img src=${MARK} alt="" /><span class="small">Its spoke was removed</span></div></div>`}
        <div class="controls">
          ${spoke && event.state === 'scheduled' && spoke.mode !== 'live' && html`<button class="btn small" disabled=${!spoke.online}
            onClick=${() => preview(spoke.mode !== 'preview')}>${spoke.mode === 'preview' ? 'Stop the preview' : 'Preview the picture'}</button>`}
          ${spoke && event.state === 'scheduled' && spoke.mode === 'live' && html`<span class="muted small">${spoke.name} is live with something else</span>`}
          ${ended && html`<span class="muted small">Aired ${dayPhrase(start)} at ${clock(start)}</span>`}
          ${recording && html`<span class="muted small" style="margin-left:auto">${COPY_LABEL[recording.source] || 'Recording'}</span>`}
          ${spoke && !recording && html`<span class="muted small" style="margin-left:auto">${spoke.name} · 1080p${spoke.framerate} · ${spoke.bitrate_kbps / 1000} Mbps</span>`}
        </div>
      </div>
      <div class="side">
        <div class="panel">
          <h3>Details</h3>
          ${event.thumbnail_url && html`<div class="detail-thumb" style=${`background-image:url(${event.thumbnail_url})`}></div>`}
          <div class="kv small">${details.map(([k, v]) => html`<span class="muted">${k}</span><span>${v}</span>`)}</div>
        </div>
        <div class="panel">
          <h3>YouTube</h3>
          ${event.targets.map((t) => html`<div class="target">
            <div class="target-head"><${Avatar} channel=${t} size=${22} /><b>${t.channel}</b></div>
            ${t.error ? html`<p class="error small">${t.error}</p>` : html`<div class="links small">
              ${t.watch_url && html`<a href=${t.watch_url} target="_blank" rel="noopener">Watch <${Icon} name="external" size=${12} /></a>`}
              ${t.studio_url && html`<a href=${t.studio_url} target="_blank" rel="noopener">YouTube Studio <${Icon} name="external" size=${12} /></a>`}
            </div>`}
          </div>`)}
          ${!event.targets.length && html`<p class="muted small">${event.adhoc ? 'Went live from the dashboard, to its stream keys.'
            : 'Record only: the hub records it, plus the spoke’s own copy.'}</p>`}
        </div>
      </div>
    </div>
    ${event.description && html`<div class="panel"><h3>Description</h3><p class="description">${event.description}</p></div>`}
    ${(ended || event.state === 'live') && html`<div class="panel">
      <h3>Recordings</h3>
      ${hasCopy ? html`<div class="tool-row">
          <${DownloadMenu} copies=${event.recordings} edits=${event.edits} />
          ${ended && html`<a class="btn small ghost" href=${`#/transcript/${event.id}`}>${!event.transcript ? 'Transcript'
            : event.transcript.status === 'done' ? 'Transcript ✓' : event.transcript.status === 'failed' ? 'Transcript !'
            : `Transcribing ${Math.round(event.transcript.progress * 100)}%`}</a>`}
          ${ended && html`<a class="btn small ghost" href=${`#/edit/${event.id}/shorts`}>${event.shorts ? `Shorts · ${event.shorts}` : 'Shorts'}</a>`}
          ${hubEnv.local && event.folder && html`<${IconButton} icon="folder" label=${revealLabel()} onClick=${() => reveal({ event_id: event.id })} />`}
        </div>`
        : html`<p class="muted small">${event.state === 'live' ? 'Recording now: the recordings appear here when it ends.'
          : 'No recordings were kept.'}</p>`}
    </div>`}
    ${editing && editorFor(event, { spokes: data.spokes, channels: data.channels, onClose: () => { setEditing(false); refresh(); } })}
  </div>`;
}
