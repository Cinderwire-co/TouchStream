// The editor's timeline, drawn on a canvas: the color adjustment layer, titles, the video track, its sound,
// music, the transcript's words and the shorts. Clips sit where they are (`at`); gaps stay until you close them.
import { html, useEffect, useRef, useState } from '../vendor/preact-htm.module.js';
import { MIN_CLIP, clipAt, clipEnd, cuts, gapAt, partner, total as lengthOf } from './edit-model.js';

const EDGE = 7;           // pixels from an edge that grab it
const TILES = 60;         // frames in the hub's filmstrip (editor.py)
const TRACKS = [
  { id: 'ruler', h: 22 },
  { id: 'adjust', label: 'Color', h: 18 },
  { id: 'titles', label: 'Titles', h: 22 },
  { id: 'video', label: 'Video', h: 52 },
  { id: 'audio', label: 'Audio', h: 38 },
  { id: 'music', label: 'Music', h: 22 },
  { id: 'words', label: 'Words', h: 20 },
  { id: 'shorts', label: 'Shorts', h: 20 },
];
const GAP = 3;
let y = 0;
for (const t of TRACKS) { t.y = y; y += t.h + GAP; }
const HEIGHT = y - GAP;
const T = Object.fromEntries(TRACKS.map((t) => [t.id, t]));
const C = { bg: '#141414', track: '#1b1b1b', clip: '#24272d', media: '#2b2340', audio: '#14262a', mediaAudio: '#22203a',
  wave: '#4ade80', mediaWave: '#a78bfa', line: 'rgba(242,242,243,.22)', text: '#f2f2f3', smoke: '#8e8e93',
  ember: '#fe5c09', emberSoft: 'rgba(254,92,9,.16)', word: '#2a2a2a', ink: '#0e0e0e', music: '#1d3550', title: '#3a3260' };
const STEPS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600];
const fmt = (s) => {
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = Math.floor(s % 60);
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}` : `${m}:${String(sec).padStart(2, '0')}`;
};
const isSelected = (sel, kind, id) => sel && sel.kind === kind && (sel.id === id || (sel.ids && sel.ids.includes(id)));

/**
 * edit: the edit (edit-model.js); clock: {t, playing}, read every frame; duration: the recording's length
 * strip, waveform: the recording's filmstrip and waveform URLs; mediaById: library items (with poster URLs)
 * sentences, moments: [{start, end, ...}] in the recording's time; selected: what's selected (see editor.js)
 * on: {seek, select, split, change(edit, record), dragEnd(before), pickMoment}; ops: {trim, move}
 */
export function Timeline({ edit, clock, listen, duration, strip, waveform, mediaById, marks, selected, tool, zoom, setZoom,
  sentences, moments, picked, colorLabel, brandName, on, ops }) {
  const scroller = useRef();
  const canvas = useRef();
  const drag = useRef(null);
  const [hover, setHover] = useState(null);
  const assets = useRef({ strip: null, peaks: null, pps: 40, posters: {} });
  const props = useRef();
  props.current = { edit, duration, marks, selected, zoom, sentences, moments, picked, hover, tool, colorLabel, brandName, mediaById };

  useEffect(() => {
    assets.current = { ...assets.current, strip: null, peaks: null };
    const img = new Image();
    img.onload = () => { assets.current.strip = img; draw(); };
    img.src = strip;
    fetch(waveform, { credentials: 'same-origin' }).then(async (r) => {
      if (!r.ok) return;
      assets.current.pps = Number(r.headers.get('X-Peaks-Per-Second')) || 40;
      assets.current.peaks = new Uint8Array(await r.arrayBuffer());
      draw();
    }).catch(() => {});
  }, [strip, waveform]);

  const poster = (m) => {
    if (!m || !m.poster) return null;
    const cache = assets.current.posters;
    if (!cache[m.id]) { const img = new Image(); img.onload = () => draw(); img.src = m.poster; cache[m.id] = img; }
    return cache[m.id].complete && cache[m.id].naturalWidth ? cache[m.id] : null;
  };

  // Redraw every frame while playing (the playhead), and whenever anything changes
  useEffect(() => {
    let frame;
    const loop = () => {
      if (clock.current.playing) {
        const el = scroller.current, x = clock.current.t * props.current.zoom - el.scrollLeft;
        if (x > el.clientWidth - 60 || x < 0) el.scrollLeft = clock.current.t * props.current.zoom - el.clientWidth * 0.25;
        draw();
      }
      frame = requestAnimationFrame(loop);
    };
    frame = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(frame);
  }, []);
  useEffect(() => { draw(); });
  useEffect(() => listen(() => { if (!clock.current.playing) draw(); }), []);

  function draw() {
    const c = canvas.current, el = scroller.current;
    if (!c || !el) return;
    const p = props.current, e = p.edit, dpr = devicePixelRatio || 1, w = el.clientWidth;
    if (c.width !== Math.round(w * dpr)) { c.width = Math.round(w * dpr); c.height = Math.round(HEIGHT * dpr); c.style.width = `${w}px`; }
    const g = c.getContext('2d');
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, w, HEIGHT);
    const sl = el.scrollLeft, z = p.zoom, X = (t) => t * z - sl, total = lengthOf(e);
    for (const t of TRACKS.slice(1)) { g.fillStyle = C.track; g.fillRect(0, t.y, w, t.h); }

    // Ruler
    const step = STEPS.find((s) => s * z >= 90) || 3600, minor = step >= 60 ? step / 6 : step / 5;
    g.fillStyle = C.bg; g.fillRect(0, 0, w, T.ruler.h);
    g.font = '10px JetBrains, ui-monospace, monospace'; g.textBaseline = 'top';
    for (let t = Math.floor(sl / z / minor) * minor; X(t) < w; t += minor) {
      const major = Math.abs(t / step - Math.round(t / step)) < 1e-6;
      g.fillStyle = major ? C.smoke : 'rgba(142,142,147,.45)';
      g.fillRect(Math.round(X(t)), major ? 12 : 17, 1, major ? 12 : 7);
      if (major && t >= 0) g.fillText(fmt(t), X(t) + 4, 3);
    }
    if (p.marks.length) {
      const [a, b] = [p.marks[0], p.marks[1] ?? p.marks[0]];
      g.fillStyle = C.emberSoft; g.fillRect(X(a), 0, Math.max(2, (b - a) * z), HEIGHT);
      g.fillStyle = C.ember;
      for (const m of p.marks) g.fillRect(Math.round(X(m)), 0, 1, HEIGHT);
    }
    const label = (text, x0, cw, track, color = C.text) => {
      if (cw < 26) return;
      g.save(); g.beginPath(); g.rect(x0, track.y, cw - 4, track.h); g.clip();
      g.fillStyle = color; g.font = '11px Bricolage, system-ui'; g.textBaseline = 'middle';
      g.fillText(text, Math.max(x0, 0) + 6, track.y + track.h / 2); g.restore();
    };

    // The color adjustment, across everything
    if (total) {
      const A = T.adjust, on = p.selected && p.selected.kind === 'adjust';
      g.fillStyle = on ? 'rgba(254,92,9,.35)' : 'rgba(254,92,9,.14)'; g.fillRect(X(0), A.y + 2, total * z, A.h - 4);
      if (on) { g.strokeStyle = C.ember; g.lineWidth = 1.5; g.strokeRect(X(0) + 0.5, A.y + 2.5, total * z - 1, A.h - 5); }
      label(`${p.colorLabel}${p.brandName ? `  ·  Watermark: ${p.brandName}` : ''}`, X(0), total * z, A);
    }

    // Titles
    for (const t of e.titles) {
      const x0 = X(t.start), cw = (t.end - t.start) * z, S = T.titles;
      if (x0 > w || x0 + cw < 0) continue;
      const on = isSelected(p.selected, 'title', t.id);
      g.fillStyle = C.title; g.fillRect(x0, S.y + 2, cw, S.h - 4);
      g.strokeStyle = on ? C.ember : 'rgba(180,160,255,.6)'; g.lineWidth = on ? 2 : 1; g.strokeRect(x0 + 0.5, S.y + 2.5, cw - 1, S.h - 5);
      label(`T  ${t.text || 'Title'}`, x0, cw, S);
    }

    // Video and audio clips, where they sit
    const { strip: img, peaks, pps } = assets.current;
    for (const c of e.video) {
      const x0 = X(c.at), cw = (c.end - c.start) * z, V = T.video, m = c.media ? p.mediaById(c.media) : null;
      if (x0 > w || x0 + cw < 0) continue;
      g.save(); g.beginPath(); g.rect(x0, V.y, cw, V.h); g.clip();
      g.fillStyle = m ? C.media : C.clip; g.fillRect(x0, V.y, cw, V.h);
      const tw = V.h * 16 / 9;
      const pic = m ? poster(m) : img;
      if (pic) {
        for (let x = Math.max(x0, x0 + Math.floor(-x0 / tw) * tw); x < Math.min(w, x0 + cw); x += tw) {
          if (m) { g.drawImage(pic, x, V.y, tw, V.h); continue; }
          const src = c.start + (x - x0) / z, n = Math.min(TILES - 1, Math.floor((src / (p.duration || 1)) * TILES)), sw = pic.width / TILES;
          g.drawImage(pic, n * sw, 0, sw, pic.height, x, V.y, tw, V.h);
        }
        g.fillStyle = 'rgba(0,0,0,.2)'; g.fillRect(x0, V.y, cw, V.h);
      }
      if (cw > 60) {
        const name = m ? m.name : fmt(c.start);
        g.font = '10px JetBrains, ui-monospace, monospace'; g.textBaseline = 'top';
        const tw2 = g.measureText(name).width + 8;
        g.fillStyle = 'rgba(0,0,0,.6)'; g.fillRect(x0 + 4, V.y + 4, Math.min(tw2, cw - 8), 15);
        g.fillStyle = C.text; g.fillText(name, x0 + 8, V.y + 7);
      }
      g.restore();
      const on = isSelected(p.selected, 'clip', c.id);
      g.strokeStyle = on ? C.ember : C.line; g.lineWidth = on ? 2 : 1; g.strokeRect(x0 + 0.5, V.y + 0.5, cw - 1, V.h - 1);
    }
    for (const c of e.audio) {
      const x0 = X(c.at), cw = (c.end - c.start) * z, A = T.audio;
      if (x0 > w || x0 + cw < 0) continue;
      g.fillStyle = c.media ? C.mediaAudio : C.audio; g.fillRect(x0, A.y, cw, A.h);
      const mid = A.y + A.h / 2;
      g.fillStyle = c.media ? C.mediaWave : C.wave;
      if (!c.media && peaks) {
        const per = pps / z;
        for (let x = Math.max(0, Math.floor(x0)); x < Math.min(w, x0 + cw); x++) {
          const k = Math.floor((c.start + (x - x0) / z) * pps);
          let v = 0;
          for (let j = k; j < k + Math.max(1, per) && j < peaks.length; j++) v = Math.max(v, peaks[j]);
          const hh = Math.max(1, (v / 255) * (A.h / 2 - 3));
          g.fillRect(x, mid - hh, 1, hh * 2);
        }
      } else if (c.media) {
        g.fillRect(Math.max(0, x0), mid - 1, Math.min(w, x0 + cw) - Math.max(0, x0), 2);
        label((p.mediaById(c.media) || {}).name || 'Sound', x0, cw, A);
      }
      if (!c.link) label('unlinked', x0 + cw - 70, 70, { y: A.y + A.h - 16, h: 14 }, C.smoke);
      const on = isSelected(p.selected, 'clip', c.id);
      g.strokeStyle = on ? C.ember : C.line; g.lineWidth = on ? 2 : 1; g.strokeRect(x0 + 0.5, A.y + 0.5, cw - 1, A.h - 1);
    }
    // A selected gap
    if (p.selected && p.selected.kind === 'gap') {
      const { from, to } = p.selected, top = T.video.y, h = T.audio.y + T.audio.h - top;
      g.fillStyle = 'rgba(254,92,9,.12)'; g.fillRect(X(from), top, (to - from) * z, h);
      g.setLineDash([5, 4]); g.strokeStyle = C.ember; g.lineWidth = 1.5;
      g.strokeRect(X(from) + 0.5, top + 0.5, (to - from) * z - 1, h - 1); g.setLineDash([]);
    }
    // The edge being pointed at, to trim
    const hv = p.hover;
    if (hv && hv.kind === 'edge') {
      const c = e[hv.track].find((x) => x.id === hv.id);
      if (c) { const tr = T[hv.track]; g.fillStyle = C.ember; g.fillRect(hv.side === 'start' ? X(c.at) : X(clipEnd(c)) - 4, tr.y, 4, tr.h); }
    }
    // Transitions on the cuts
    for (const cut of cuts(e)) {
      const V = T.video, t = cut.transition, on = isSelected(p.selected, 'cut', cut.id), x = X(cut.at);
      const half = t && t.type !== 'cut' ? Math.max(6, (t.duration * z) / 2) : 0;
      if (x + half < 0 || x - half > w) continue;
      if (half) {
        g.fillStyle = on ? 'rgba(254,92,9,.8)' : 'rgba(254,92,9,.55)';
        g.beginPath(); g.moveTo(x - half, V.y + V.h - 4); g.lineTo(x, V.y + 8); g.lineTo(x + half, V.y + V.h - 4); g.fill();
      }
      if (on) { g.strokeStyle = C.ember; g.lineWidth = 2; g.strokeRect(x - Math.max(half, 6), V.y + 1, Math.max(half, 6) * 2, V.h - 2); }
    }
    // Fades at the ends: drag the round handles
    if (total) {
      const V = T.video;
      g.fillStyle = 'rgba(0,0,0,.55)';
      if (e.fade_in) { g.beginPath(); g.moveTo(X(0), V.y); g.lineTo(X(e.fade_in), V.y); g.lineTo(X(0), V.y + V.h); g.fill(); }
      if (e.fade_out) { g.beginPath(); g.moveTo(X(total), V.y); g.lineTo(X(total - e.fade_out), V.y); g.lineTo(X(total), V.y + V.h); g.fill(); }
      g.strokeStyle = 'rgba(255,255,255,.85)'; g.lineWidth = 1;
      if (e.fade_in) { g.beginPath(); g.moveTo(X(0), V.y + V.h); g.lineTo(X(e.fade_in), V.y); g.stroke(); }
      if (e.fade_out) { g.beginPath(); g.moveTo(X(total), V.y + V.h); g.lineTo(X(total - e.fade_out), V.y); g.stroke(); }
      for (const [x, which] of [[X(e.fade_in), 'fade_in'], [X(total - e.fade_out), 'fade_out']]) {
        g.fillStyle = hv && hv.kind === 'fade' && hv.which === which ? C.ember : C.text;
        g.beginPath(); g.arc(x, V.y + 5, 5, 0, Math.PI * 2); g.fill();
        g.strokeStyle = C.ink; g.stroke();
      }
    }
    // Music
    for (const m of e.music) {
      const x0 = X(m.at), cw = (m.end - m.start) * z, M = T.music, on = isSelected(p.selected, 'music', m.id);
      if (x0 > w || x0 + cw < 0) continue;
      g.fillStyle = C.music; g.fillRect(x0, M.y + 2, cw, M.h - 4);
      g.strokeStyle = on ? C.ember : 'rgba(96,165,250,.6)'; g.lineWidth = on ? 2 : 1; g.strokeRect(x0 + 0.5, M.y + 2.5, cw - 1, M.h - 5);
      label(`♪  ${(p.mediaById(m.media) || {}).name || 'Music'} · ${Math.round(m.volume * 100)}%`, x0, cw, M);
    }
    // Words and shorts: in the recording's time, drawn where that part of the recording's sound is
    const pieces = (start, end) => e.audio.filter((c) => !c.media && c.start < end && c.end > start)
      .map((c) => [c.at + Math.max(start, c.start) - c.start, c.at + Math.min(end, c.end) - c.start]);
    for (const s of p.sentences || []) {
      for (const [a, b] of pieces(s.start, s.end)) {
        const x0 = X(a), cw = (b - a) * z, W = T.words;
        if (x0 > w || x0 + cw < 0 || cw < 2) continue;
        g.fillStyle = C.word; g.fillRect(x0 + 1, W.y + 3, cw - 2, W.h - 6);
        label(s.text, x0, cw, W, 'rgba(242,242,243,.8)');
      }
    }
    for (const m of p.moments || []) {
      for (const [a, b] of pieces(m.start, m.end)) {
        const x0 = X(a), cw = (b - a) * z, S = T.shorts;
        if (x0 > w || x0 + cw < 0) continue;
        const on = p.picked && p.picked.start === m.start && p.picked.end === m.end;
        g.fillStyle = m.made ? (on ? C.ember : 'rgba(254,92,9,.7)') : (on ? 'rgba(254,92,9,.3)' : 'rgba(254,92,9,.1)');
        g.fillRect(x0, S.y + 3, cw, S.h - 6);
        g.setLineDash(m.made ? [] : [4, 3]); g.strokeStyle = C.ember; g.lineWidth = on ? 2 : 1;
        g.strokeRect(x0 + 0.5, S.y + 3.5, cw - 1, S.h - 7); g.setLineDash([]);
        label(`${m.made ? '' : '✦ '}${m.title}`, x0, cw, S, m.made ? C.ink : C.text);
      }
    }

    // Playhead
    const ph = X(clock.current.t);
    g.fillStyle = C.ember;
    g.fillRect(Math.round(ph) - 1, 0, 2, HEIGHT);
    g.beginPath(); g.moveTo(ph - 6, 0); g.lineTo(ph + 6, 0); g.lineTo(ph, 9); g.fill();
  }

  // ---- What's under the pointer ----
  const local = (ev) => { const r = canvas.current.getBoundingClientRect(); return { x: ev.clientX - r.left, y: ev.clientY - r.top }; };
  const trackAt = (yy) => TRACKS.find((t) => yy >= t.y && yy < t.y + t.h + GAP) || TRACKS[TRACKS.length - 1];
  const timeAt = (x) => Math.max(0, (x + scroller.current.scrollLeft) / zoom);
  const near = (x, t) => Math.abs(t * zoom - scroller.current.scrollLeft - x) <= EDGE;
  const hit = (x, yy) => {
    const track = trackAt(yy), t = timeAt(x), e = edit;
    if (track.id === 'video' && yy - track.y < 12) {
      const total = lengthOf(e);
      if (near(x, e.fade_in)) return { kind: 'fade', which: 'fade_in' };
      if (near(x, total - e.fade_out)) return { kind: 'fade', which: 'fade_out' };
    }
    if (track.id === 'video' || track.id === 'audio') {
      const list = e[track.id];
      for (const c of list) {
        if (near(x, c.at)) return { kind: 'edge', track: track.id, id: c.id, side: 'start', cut: track.id === 'video' && cuts(e).find((k) => Math.abs(k.at - c.at) < 0.02) };
        if (near(x, clipEnd(c))) return { kind: 'edge', track: track.id, id: c.id, side: 'end', cut: track.id === 'video' && cuts(e).find((k) => k.id === c.id) };
      }
      const c = clipAt(list, t);
      return c ? { kind: 'clip', track: track.id, id: c.id, t } : { kind: 'empty', track: track.id, t };
    }
    if (track.id === 'titles' || track.id === 'music') {
      const list = track.id === 'titles' ? e.titles.map((x) => ({ ...x, at: x.start, len: x.end - x.start }))
        : e.music.map((x) => ({ ...x, len: x.end - x.start }));
      for (const it of [...list].reverse()) {
        if (t >= it.at - EDGE / zoom && t <= it.at + it.len + EDGE / zoom) {
          const side = near(x, it.at) ? 'start' : near(x, it.at + it.len) ? 'end' : 'move';
          return { kind: track.id === 'titles' ? 'title' : 'music', id: it.id, side, t };
        }
      }
      return { kind: 'empty', track: track.id, t };
    }
    return { kind: track.id, t };
  };
  const both = (track, c, alone) => { const p2 = alone ? null : partner(edit, track, c); return [c.id, ...(p2 ? [p2.id] : [])]; };

  const onPointerDown = (ev) => {
    const { x, y: yy } = local(ev), h = hit(x, yy), t = timeAt(x), alone = ev.altKey;
    canvas.current.setPointerCapture(ev.pointerId);
    drag.current = { h, x, t0: t, moved: false, alone, start: edit };
    if (h.kind === 'ruler' || (h.kind === 'empty' && (h.track === 'titles' || h.track === 'music'))) {
      drag.current.kind = 'scrub'; on.select(null); on.seek(t); return;
    }
    if (h.kind === 'adjust') { on.select({ kind: 'adjust' }); drag.current = null; return; }
    if (h.kind === 'fade') { drag.current.kind = 'fade'; return; }
    if (tool === 'blade' && (h.kind === 'clip' || h.kind === 'edge')) {
      on.split(t, both(h.track, edit[h.track].find((k) => k.id === h.id), alone));
      drag.current = null;
      return;
    }
    if (h.kind === 'edge') { drag.current.kind = 'edge'; return; }
    if (h.kind === 'clip') {
      const c = edit[h.track].find((k) => k.id === h.id);
      on.select({ kind: 'clip', track: h.track, id: c.id, ids: both(h.track, c, alone) });
      drag.current.kind = 'move'; drag.current.offset = t - c.at;
      return;
    }
    if (h.kind === 'empty') {
      const gap = gapAt(edit, t);
      on.select(gap ? { kind: 'gap', ...gap } : null); on.seek(t); drag.current.kind = 'scrub'; return;
    }
    if (h.kind === 'title' || h.kind === 'music') {
      on.select({ kind: h.kind, id: h.id });
      drag.current.kind = h.kind;
      drag.current.orig = (h.kind === 'title' ? edit.titles : edit.music).find((k) => k.id === h.id);
      return;
    }
    if (h.kind === 'words') { on.seek(t); drag.current = null; return; }
    if (h.kind === 'shorts') {
      const a = clipAt(edit.audio, t), src = a && !a.media ? a.start + t - a.at : null;
      const m = src === null ? null : [...(moments || [])].reverse().find((x2) => src >= x2.start && src < x2.end);
      if (m) on.pickMoment(m); else on.seek(t);
      drag.current = null;
    }
  };
  const onPointerMove = (ev) => {
    const { x, y: yy } = local(ev), d = drag.current;
    if (!d) {
      const h = hit(x, yy);
      const key = (v) => v && `${v.kind}${v.id}${v.side}${v.which}`;
      if (key(h) !== key(hover)) setHover(h.kind === 'edge' || h.kind === 'fade' ? h : null);
      canvas.current.style.cursor = h.kind === 'edge' || h.kind === 'fade' || ((h.kind === 'title' || h.kind === 'music') && h.side !== 'move') ? 'ew-resize'
        : tool === 'blade' && (h.kind === 'clip' || h.kind === 'edge') ? 'crosshair'
        : h.kind === 'clip' || h.kind === 'title' || h.kind === 'music' ? 'grab' : h.kind === 'shorts' || h.kind === 'adjust' ? 'pointer' : 'default';
      return;
    }
    if (!d.moved && Math.abs(x - d.x) < 3) return;
    d.moved = true;
    const t = timeAt(x);
    if (d.kind === 'scrub') on.seek(t);
    else if (d.kind === 'fade') {
      const total = lengthOf(d.start);
      const v = Math.max(0, Math.min(10, total / 2, d.h.which === 'fade_in' ? t : total - t));
      on.change({ ...d.start, [d.h.which]: Math.round(v * 10) / 10 }, false);
    } else if (d.kind === 'edge') on.change(ops.trim(d.start, d.h.track, d.h.id, d.h.side, t, d.alone), false);
    else if (d.kind === 'move') on.change(ops.move(d.start, d.h.track, d.h.id, t - d.offset, d.alone), false);
    else if (d.kind === 'title' || d.kind === 'music') {
      const o = d.orig, list = d.kind === 'title' ? 'titles' : 'music', total = lengthOf(d.start);
      const a = d.kind === 'title' ? o.start : o.at, len = o.end - o.start;
      let patch;
      if (d.h.side === 'move') {
        const at = Math.max(0, Math.min(total - (d.kind === 'title' ? len : MIN_CLIP), a + t - d.t0));
        patch = d.kind === 'title' ? { start: at, end: at + len } : { at };
      } else if (d.h.side === 'start') {
        const at = Math.max(d.kind === 'title' ? 0 : a - o.start, Math.min(a + len - MIN_CLIP, t));
        patch = d.kind === 'title' ? { start: at } : { at, start: o.start + at - a };
      } else {
        const end = Math.max(a + MIN_CLIP, Math.min(total, t));
        patch = d.kind === 'title' ? { end } : { end: Math.min(o.start + end - a, (props.current.mediaById(o.media) || {}).duration || Infinity) };
      }
      on.change({ ...d.start, [list]: d.start[list].map((k) => (k.id === o.id ? { ...k, ...patch } : k)) }, false);
    }
  };
  const onPointerUp = () => {
    const d = drag.current;
    drag.current = null;
    if (!d) return;
    if (d.moved && d.kind !== 'scrub') { on.dragEnd(d.start); return; }
    // A click on a cut between two clips picks its transition; on any other edge, the clip
    if (!d.moved && d.kind === 'edge') {
      if (d.h.cut) on.select({ kind: 'cut', id: d.h.cut.id });
      else {
        const c = edit[d.h.track].find((k) => k.id === d.h.id);
        on.select({ kind: 'clip', track: d.h.track, id: c.id, ids: both(d.h.track, c, d.alone) });
      }
    }
    if (!d.moved && d.kind === 'move') on.seek(d.t0);
  };
  const onWheel = (ev) => {
    if (!(ev.ctrlKey || ev.metaKey)) return;
    ev.preventDefault();
    const el = scroller.current, { x } = local(ev), t = (x + el.scrollLeft) / zoom;
    const next = setZoom(zoom * (ev.deltaY < 0 ? 1.15 : 1 / 1.15));
    requestAnimationFrame(() => { el.scrollLeft = t * next - x; draw(); });
  };

  return html`<div class="tl">
    <div class="tl-labels">${TRACKS.map((t) => html`<div style=${`height:${t.h}px;margin-bottom:${GAP}px`}>${t.label || ''}</div>`)}</div>
    <div class="tl-scroll" ref=${scroller} onScroll=${() => draw()} onWheel=${onWheel}>
      <canvas ref=${canvas} class="tl-canvas" style=${`height:${HEIGHT}px`} onPointerDown=${onPointerDown}
        onPointerMove=${onPointerMove} onPointerUp=${onPointerUp} onPointerLeave=${() => hover && setHover(null)}></canvas>
      <div style=${`width:${Math.max(400, lengthOf(edit) * zoom + 200)}px;height:1px`}></div>
    </div>
  </div>`;
}
