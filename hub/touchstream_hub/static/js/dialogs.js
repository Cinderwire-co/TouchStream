// Dialogs drawn by the dashboard instead of the browser: questions, confirmations, short messages,
// and the "leave without saving?" question when navigating away from unsaved work
import { html, useState, useEffect, useRef } from '../vendor/preact-htm.module.js';

let show = null;      // set while <Dialogs /> is mounted
let notify = null;
const waiting = [];

/**
 * Ask something. `buttons` are {label, value, kind: 'primary' | 'danger' | undefined}, left to right;
 * the last one has focus. Resolves to the chosen value, or null if dismissed (Escape, backdrop).
 */
export function ask({ title, message, buttons }) {
  return new Promise((resolve) => {
    const dialog = { title, message, buttons, resolve };
    if (show) show(dialog); else waiting.push(dialog);
  });
}

/** A yes/no question: resolves true only if confirmed. */
export const confirmAction = ({ title, message, confirm = 'OK', danger = false, cancel = 'Cancel' }) =>
  ask({ title, message, buttons: [{ label: cancel, value: false }, { label: confirm, value: true, kind: danger ? 'danger' : 'primary' }] })
    .then(Boolean);

/** A short message at the bottom of the screen that goes away by itself. */
export function toast(message, kind = 'error') {
  if (notify) notify({ message: message && message.message ? message.message : String(message), kind, id: Math.random() });
}

// ---- Unsaved work ----
let guard = null;     // the page with unsaved work: { dirty, save, message }

/** While `dirty`, leaving the page (links, back, closing the tab) asks first. `save` keeps the work. */
export function useLeaveGuard(dirty, save, message, saveLabel = 'Save draft') {
  const ref = useRef();
  ref.current = { dirty, save, message, saveLabel };
  useEffect(() => {
    guard = ref;
    const unload = (e) => { if (ref.current.dirty) { e.preventDefault(); e.returnValue = ''; } };
    addEventListener('beforeunload', unload);
    return () => { if (guard === ref) guard = null; removeEventListener('beforeunload', unload); };
  }, []);
}

export const hasUnsaved = () => !!(guard && guard.current.dirty);

/** Before leaving a page: true to go (after saving or discarding), false to stay. */
export async function canLeave() {
  if (!hasUnsaved()) return true;
  const { save, message, saveLabel } = guard.current;
  const choice = await ask({ title: 'Leave without saving?', message: message || "You've made changes that aren't saved.",
    buttons: [{ label: 'Keep editing', value: 'stay' }, { label: 'Discard changes', value: 'discard', kind: 'danger' },
      ...(save ? [{ label: saveLabel, value: 'save', kind: 'primary' }] : [])] });
  if (choice === 'save') {
    try { await save(); } catch (e) { toast(e); return false; }
  }
  if (choice === 'save' || choice === 'discard') { guard = null; return true; }
  return false;
}

function Dialog({ dialog, close }) {
  const last = useRef();
  useEffect(() => {
    if (last.current) last.current.focus();
    // Capture, so Escape closes this dialog and not the modal underneath
    const esc = (e) => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(null); } };
    addEventListener('keydown', esc, true);
    return () => removeEventListener('keydown', esc, true);
  }, []);
  return html`<div class="backdrop top" onClick=${(e) => e.target === e.currentTarget && close(null)}>
    <div class="modal dialog" role="alertdialog" aria-label=${dialog.title}>
      <h2>${dialog.title}</h2>
      ${dialog.message && html`<p class="muted">${dialog.message}</p>`}
      <div class="modal-foot">
        ${dialog.buttons.map((b, i) => html`<button type="button" ref=${i === dialog.buttons.length - 1 ? last : null}
          class=${`btn ${b.kind || 'ghost'}`} onClick=${() => close(b.value)}>${b.label}</button>`)}
      </div>
    </div>
  </div>`;
}

/** Mounted once, at the top of the app. */
export function Dialogs() {
  const [dialogs, setDialogs] = useState([]);
  const [toasts, setToasts] = useState([]);
  useEffect(() => {
    show = (d) => setDialogs((all) => [...all, d]);
    notify = (t) => {
      setToasts((all) => [...all.slice(-2), t]);
      setTimeout(() => setToasts((all) => all.filter((x) => x !== t)), 6000);
    };
    waiting.splice(0).forEach(show);
    return () => { show = null; notify = null; };
  }, []);
  const close = (d, value) => { setDialogs((all) => all.filter((x) => x !== d)); d.resolve(value); };
  return html`
    ${dialogs.map((d) => html`<${Dialog} key=${d} dialog=${d} close=${(v) => close(d, v)} />`)}
    <div class="toasts" role="status">${toasts.map((t) => html`<div key=${t.id} class=${`toast ${t.kind}`}
      onClick=${() => setToasts((all) => all.filter((x) => x !== t))}>${t.message}</div>`)}</div>`;
}
