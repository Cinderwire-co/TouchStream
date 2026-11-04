// Form controls drawn by the dashboard instead of the browser: dropdowns, date and time pickers
import { html, useState, useEffect, useRef } from '../vendor/preact-htm.module.js';
import { Icon } from './ui.js';

/** A popover under (or above) an anchor. Fixed to the viewport, so scrolling modals and panels don't clip it. */
export function usePopover(width) {
  const anchor = useRef();
  const popover = useRef();
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState(null);
  useEffect(() => {
    if (!open) { setPos(null); return; }
    const place = () => {
      if (!anchor.current) return;
      const r = anchor.current.getBoundingClientRect();
      const below = innerHeight - r.bottom, above = r.top;
      const up = below < 280 && above > below;
      const w = Math.min(innerWidth - 16, Math.max(width || 0, r.width));
      setPos({ left: Math.max(8, Math.min(r.left, innerWidth - w - 8)), width: w,
        top: up ? null : r.bottom + 6, bottom: up ? innerHeight - r.top + 6 : null,
        max: Math.max(160, Math.min(340, (up ? above : below) - 16)) });
    };
    const outside = (e) => {
      if (!anchor.current.contains(e.target) && !(popover.current && popover.current.contains(e.target))) setOpen(false);
    };
    place();
    addEventListener('pointerdown', outside, true);
    addEventListener('scroll', place, true);
    addEventListener('resize', place);
    return () => {
      removeEventListener('pointerdown', outside, true);
      removeEventListener('scroll', place, true);
      removeEventListener('resize', place);
    };
  }, [open]);
  const style = pos && `left:${pos.left}px;width:${pos.width}px;max-height:${pos.max}px;${
    pos.top != null ? `top:${pos.top}px` : `bottom:${pos.bottom}px`}`;
  return { anchor, popover, open, setOpen, style };
}

/** Escape closes the popover only, not the modal behind it. */
const escape = (e, close) => {
  if (e.key !== 'Escape') return false;
  e.preventDefault(); e.stopPropagation(); close();
  return true;
};

/**
 * A dropdown. Options are [value, label] or [value, label, note] pairs, or {value, label, note}.
 * Arrow keys, Enter and typing the first letter work as in a native one.
 */
export function Select({ value, options, onChange, disabled, label, className = '' }) {
  const items = options.map((o) => (Array.isArray(o) ? { value: o[0], label: o[1], note: o[2] } : o));
  const index = items.findIndex((o) => String(o.value) === String(value));
  const { anchor, popover, open, setOpen, style } = usePopover(240);
  const [active, setActive] = useState(0);
  useEffect(() => { if (open) setActive(Math.max(0, index)); }, [open]);
  useEffect(() => {
    const el = popover.current && popover.current.children[active];
    if (el) el.scrollIntoView({ block: 'nearest' });
  }, [active, style]);
  const choose = (o) => {
    setOpen(false);
    anchor.current.focus();
    if (String(o.value) !== String(value)) onChange(o.value);
  };
  const key = (e) => {
    if (!open) {
      if (['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(e.key)) { e.preventDefault(); setOpen(true); }
      return;
    }
    if (escape(e, () => setOpen(false))) return;
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive((a) => Math.min(items.length - 1, a + 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((a) => Math.max(0, a - 1)); }
    else if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); if (items[active]) choose(items[active]); }
    else if (e.key === 'Tab') setOpen(false);
    else if (e.key.length === 1) {
      const i = items.findIndex((o) => String(o.label).toLowerCase().startsWith(e.key.toLowerCase()));
      if (i >= 0) setActive(i);
    }
  };
  const current = items[index];
  return html`<span class=${`select-wrap ${className}`}>
    <button type="button" ref=${anchor} class="select dropdown" disabled=${disabled} aria-haspopup="listbox" aria-expanded=${open}
      aria-label=${label} onClick=${() => setOpen(!open)} onKeyDown=${key}>
      <span class="ellipsis">${current ? current.label : value != null && value !== '' ? String(value) : 'Choose…'}</span>
      <${Icon} name="chevron" size=${16} />
    </button>
    ${open && style && html`<div class="popover listbox" role="listbox" ref=${popover} style=${style}>
      ${items.map((o, i) => html`<div role="option" aria-selected=${i === index}
        class=${`option ${i === active ? 'active' : ''} ${i === index ? 'selected' : ''}`}
        onPointerEnter=${() => setActive(i)} onClick=${() => choose(o)}>
        <span class="grow min0"><span class="ellipsis">${o.label}</span>${o.note && html`<span class="note">${o.note}</span>`}</span>
        ${i === index && html`<${Icon} name="check" size=${16} />`}
      </div>`)}
    </div>`}
  </span>`;
}

// ---- Dates and times (local time; dates as 'YYYY-MM-DD', times as minutes after midnight) ----
const pad = (n) => String(n).padStart(2, '0');
export const isoDate = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const parseDate = (s) => { const [y, m, d] = s.split('-').map(Number); return new Date(y, m - 1, d); };

/** epoch seconds -> { date, minutes } in local time, and back */
export const splitTime = (t) => { const d = new Date(t * 1000); return { date: isoDate(d), minutes: d.getHours() * 60 + d.getMinutes() }; };
export const joinTime = (date, minutes) => { const d = parseDate(date); d.setHours(0, minutes, 0, 0); return d.getTime() / 1000; };

export const timeLabel = (minutes) => new Date(2000, 0, 1, 0, minutes).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
const twelveHour = /[ap]m/i.test(timeLabel(13 * 60));
export const lengthLabel = (minutes) => (minutes < 60 ? `${minutes} min`
  : `${Math.floor(minutes / 60)} h${minutes % 60 ? ` ${minutes % 60} min` : ''}`);

/** "10", "10:30", "1030", "10:30pm", "7p", "22:15" -> minutes after midnight (null if it isn't a time) */
export function parseTime(text, near = 12 * 60) {
  const m = text.trim().toLowerCase().replace(/\./g, '').match(/^(\d{1,2})(?::?(\d{2}))?\s*([ap])?m?$/);
  if (!m) return null;
  let h = Number(m[1]);
  const min = Number(m[2] || 0);
  if (h > 23 || min > 59) return null;
  if (m[3]) {
    if (h < 1 || h > 12) return null;
    h = (h % 12) + (m[3] === 'p' ? 12 : 0);
  } else if (twelveHour && h >= 1 && h <= 12) {
    // No am/pm typed: take whichever is nearer the time it replaces
    const am = (h % 12) * 60 + min, pm = am + 720;
    return Math.abs(pm - near) < Math.abs(am - near) ? pm : am;
  }
  return h * 60 + min;
}

/** The day weeks start on here: 0 Sunday ... 6 Saturday (as Date.getDay counts). */
export const weekStart = () => {
  try { return (new Intl.Locale(navigator.language).getWeekInfo?.().firstDay ?? 7) % 7; } catch (e) { return 0; }
};

/** Toggle buttons for picking several things, e.g. days of the week. `value` is the list picked. */
export function Chips({ options, value, onChange, label }) {
  const toggle = (v) => onChange(value.includes(v) ? value.filter((x) => x !== v) : [...value, v]);
  return html`<div class="chips" role="group" aria-label=${label}>${options.map(([v, text, title]) => html`
    <button type="button" class=${`chip-btn ${value.includes(v) ? 'on' : ''}`} aria-pressed=${value.includes(v)}
      title=${title} onClick=${() => toggle(v)}>${text}</button>`)}</div>`;
}

/** A calendar dropdown for picking a day. */
export function DateField({ value, onChange, label }) {
  const { anchor, popover, open, setOpen, style } = usePopover(296);
  const chosen = parseDate(value);
  const [month, setMonth] = useState(new Date(chosen.getFullYear(), chosen.getMonth(), 1));
  useEffect(() => { if (open) setMonth(new Date(chosen.getFullYear(), chosen.getMonth(), 1)); }, [open]);
  const first = weekStart();
  const start = new Date(month);
  start.setDate(1 - ((month.getDay() - first + 7) % 7));
  const days = Array.from({ length: 42 }, (_, i) => new Date(start.getFullYear(), start.getMonth(), start.getDate() + i));
  const today = isoDate(new Date());
  const step = (n) => setMonth(new Date(month.getFullYear(), month.getMonth() + n, 1));
  const pick = (d) => { onChange(isoDate(d)); setOpen(false); anchor.current.focus(); };
  const key = (e) => {
    if (open && escape(e, () => setOpen(false))) return;
    const moves = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 };
    if (open && moves[e.key]) {
      e.preventDefault();
      const d = new Date(chosen);
      d.setDate(d.getDate() + moves[e.key]);
      onChange(isoDate(d));
      setMonth(new Date(d.getFullYear(), d.getMonth(), 1));
    } else if (e.key === 'Enter' && open) { e.preventDefault(); setOpen(false); }
  };
  return html`<span class="select-wrap">
    <button type="button" ref=${anchor} class="select" aria-label=${label} aria-expanded=${open}
      onClick=${() => setOpen(!open)} onKeyDown=${key}>
      <span class="ellipsis">${chosen.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' })}</span>
      <${Icon} name="calendar" size=${16} />
    </button>
    ${open && style && html`<div class="popover calendar" ref=${popover} style=${style}>
      <div class="cal-head">
        <button type="button" class="icon-btn" aria-label="Previous month" onClick=${() => step(-1)}><${Icon} name="back" size=${16} /></button>
        <b>${month.toLocaleDateString([], { month: 'long', year: 'numeric' })}</b>
        <button type="button" class="icon-btn" aria-label="Next month" onClick=${() => step(1)}><${Icon} name="forward" size=${16} /></button>
      </div>
      <div class="cal-grid">
        ${days.slice(0, 7).map((d) => html`<span class="cal-dow">${d.toLocaleDateString([], { weekday: 'narrow' })}</span>`)}
        ${days.map((d) => {
          const iso = isoDate(d);
          return html`<button type="button" onClick=${() => pick(d)}
            class=${`cal-day ${d.getMonth() !== month.getMonth() ? 'outside' : ''} ${iso === today ? 'today' : ''} ${iso === value ? 'on' : ''}`}>
            ${d.getDate()}</button>`;
        })}
      </div>
    </div>`}
  </span>`;
}

/**
 * A time box: type a time, or pick one from a list in 15-minute steps.
 * With `after` (the start time), the list runs from then and shows each choice's length.
 * With `optional`, the first choice is no time at all (value null).
 */
export function TimeField({ value, onChange, label, after = null, optional = '' }) {
  const { anchor, popover, open, setOpen, style } = usePopover(after != null ? 220 : 150);
  const [text, setText] = useState('');
  const [editing, setEditing] = useState(false);
  const base = after != null ? after : 0;
  const choices = Array.from({ length: 96 }, (_, i) => (after != null ? base + 15 * (i + 1) : i * 15));
  const items = [...(optional ? [null] : []), ...choices];
  // As minutes from `base`, so an end time after midnight sorts after the start
  const rel = value == null ? null : after != null ? ((value - after + 1440 - 1) % 1440) + 1 + after : value;
  const show = (m) => (m == null ? optional : timeLabel(m % 1440));
  const nearest = rel == null ? (optional ? 0 : 0) : items.findIndex((m) => m != null && m >= rel);
  useEffect(() => {
    const el = popover.current && popover.current.children[Math.max(0, nearest)];
    if (el) el.scrollIntoView({ block: 'center' });
  }, [style && open]);
  const commit = () => {
    setEditing(false);
    if (!text.trim()) { if (optional) onChange(null); return; }
    const m = parseTime(text, value ?? after ?? 12 * 60);
    if (m != null) onChange(m);
  };
  const pick = (m) => { onChange(m == null ? null : m % 1440); setOpen(false); setEditing(false); };
  return html`<span class="select-wrap">
    <span class="time-box" ref=${anchor}>
      <input type="text" class="time-input" aria-label=${label} placeholder=${optional || timeLabel(600)}
        value=${editing ? text : value == null ? '' : timeLabel(value)}
        onFocus=${(e) => { setEditing(true); setText(value == null ? '' : timeLabel(value)); setOpen(true); e.target.select(); }}
        onInput=${(e) => setText(e.target.value)}
        onBlur=${commit}
        onKeyDown=${(e) => {
          if (open && escape(e, () => setOpen(false))) return;
          if (e.key === 'Enter') { e.preventDefault(); commit(); setOpen(false); e.target.blur(); }
        }} />
      <${Icon} name="clock" size=${16} />
    </span>
    ${open && style && html`<div class="popover listbox" ref=${popover} style=${style}>
      ${items.map((m) => html`<div class=${`option ${m === rel || (m == null && value == null) ? 'selected' : ''}`}
        onPointerDown=${(e) => e.preventDefault()} onClick=${() => pick(m)}>
        <span class="grow">${show(m)}</span>
        ${after != null && m != null && html`<span class="note inline">${lengthLabel(m - after)}</span>`}
      </div>`)}
    </div>`}
  </span>`;
}
