// The edit, as tracks: what the editor changes and the hub renders (editor.py's normalize).
//
//   video:  [{id, at, start, end, media?, link?, transition?}]   at: where it sits on the timeline;
//   audio:  [{id, at, start, end, media?, link?}]                start, end: the part of its media used;
//   music:  [{id, at, start, end, media, volume}]                media: a library item's id, or none for
//   titles: [{id, start, end, text, sub, style}]                  the broadcast's recording
//   color, brand, fade_in, fade_out
//
// A video clip and its sound are linked (the same `link`) and change together, unless unlinked. Gaps
// between clips are black and silent. Every operation returns a new edit; nothing is changed in place.

export const MIN_CLIP = 0.5;
export const NO_COLOR = { brightness: 0, contrast: 0, saturation: 0, warmth: 0 };
const r3 = (x) => Math.round(x * 1000) / 1000;
let counter = 0;
export const uid = () => `c${Date.now().toString(36)}${(counter++).toString(36)}`;

export const clipEnd = (c) => c.at + c.end - c.start;
export const total = (e) => Math.max(0, ...e.video.map(clipEnd), ...e.audio.map(clipEnd));
const sorted = (list) => [...list].sort((a, b) => a.at - b.at);
export const clipAt = (list, t) => list.find((c) => t >= c.at - 1e-6 && t < clipEnd(c) - 1e-6) || null;
export const partner = (e, track, c) => (c && c.link ? (track === 'video' ? e.audio : e.video).find((x) => x.link === c.link) : null);

/** The parts kept by an edit from before tracks (in, out, cuts), as [start, end]. */
function legacyRanges(spec, duration) {
  const start = Math.max(0, spec.in || 0), end = Math.min(duration, spec.out ?? duration);
  const keep = [];
  let cursor = start;
  for (const [a0, b0] of [...(spec.cuts || [])].sort((x, y) => x[0] - y[0])) {
    const a = Math.max(a0, start), b = Math.min(b0, end);
    if (b <= a || b <= cursor) continue;
    if (a > cursor) keep.push([cursor, a]);
    cursor = Math.max(cursor, b);
  }
  if (end > cursor) keep.push([cursor, end]);
  return keep.filter(([a, b]) => b - a >= MIN_CLIP);
}

/** Back-to-back pieces [{start, end, media, transition}] -> linked video and audio clips. */
function laidOut(pieces, hasSound) {
  const video = [], audio = [];
  let at = 0;
  for (const p of pieces) {
    const link = uid();
    video.push({ id: uid(), at, start: p.start, end: p.end, media: p.media || null, link, transition: p.transition || null });
    if (hasSound(p.media)) audio.push({ id: uid(), at, start: p.start, end: p.end, media: p.media || null, link });
    at += p.end - p.start;
  }
  return { video, audio };
}

/** The hub's spec -> the editor's edit. mediaById(id) gives a library item. */
export function fromSpec(spec, duration, mediaById = () => null) {
  const base = { music: [], titles: [], color: { ...NO_COLOR }, brand: null, fade_in: 1, fade_out: 2 };
  const hasSound = (id) => !id || (mediaById(id) || {}).has_audio;
  if (!spec) return { ...base, ...laidOut([{ start: 0, end: duration }], hasSound) };
  const common = { titles: (spec.titles || []).map((t) => ({ ...t, id: uid() })), color: { ...NO_COLOR, ...(spec.color || {}) },
    brand: spec.brand || null, fade_in: spec.fade_in ?? 1, fade_out: spec.fade_out ?? 2,
    music: (spec.music || []).map((m) => ({ ...m, id: uid() })) };
  if (spec.video || spec.audio) {
    return { ...base, ...common, video: sorted((spec.video || []).map((c) => ({ ...c, id: uid() }))),
      audio: sorted((spec.audio || []).map((c) => ({ ...c, id: uid() }))) };
  }
  if (spec.clips) {
    const pieces = spec.clips.map((c, i) => ({ ...c, transition: (spec.transitions || [])[i] }));
    return { ...base, ...common, ...laidOut(pieces, hasSound) };
  }
  const pieces = legacyRanges(spec, duration).map(([start, end]) => ({ start, end,
    transition: spec.dip ? { type: 'dip_black', duration: spec.dip * 2 } : null }));
  return { ...base, ...common, ...laidOut(pieces.length ? pieces : [{ start: 0, end: duration }], hasSound) };
}

/** The editor's edit -> the spec the hub keeps and renders. */
export function toSpec(e) {
  const clip = ({ id, ...c }) => ({ ...c, at: r3(c.at), start: r3(c.start), end: r3(c.end) });
  return { video: e.video.map(clip), audio: e.audio.map(clip), music: e.music.map(clip),
    titles: e.titles.map(({ id, ...t }) => ({ ...t, start: r3(t.start), end: r3(t.end) })),
    color: e.color, brand: e.brand, fade_in: e.fade_in, fade_out: e.fade_out };
}

// ---- Changes ----
const mapTracks = (e, fn) => ({ ...e, video: sorted(e.video.map((c) => fn(c, 'video')).flat()), audio: sorted(e.audio.map((c) => fn(c, 'audio')).flat()) });

/** Cut the clips under t: on both tracks, or just `ids` (a clip and its partner). */
export function split(e, t, ids = null) {
  const links = {};
  let changed = false;
  const next = mapTracks(e, (c) => {
    if ((ids && !ids.includes(c.id)) || t <= c.at + MIN_CLIP || t >= clipEnd(c) - MIN_CLIP) return c;
    changed = true;
    const at = c.start + (t - c.at);
    const link = c.link ? (links[c.link] = links[c.link] || uid()) : null;
    return [{ ...c, end: at, transition: null }, { ...c, id: uid(), at: t, start: at, link }];
  });
  return changed ? next : e;
}

/** Take clips out, leaving gaps. */
export const lift = (e, ids) => ({ ...e, video: e.video.filter((c) => !ids.includes(c.id)), audio: e.audio.filter((c) => !ids.includes(c.id)) });

/** The empty stretch around t on both tracks (where nothing plays), or null if something does. */
export function gapAt(e, t) {
  if (clipAt(e.video, t) || clipAt(e.audio, t) || t >= total(e)) return null;
  const all = [...e.video, ...e.audio];
  const from = Math.max(0, ...all.map(clipEnd).filter((x) => x <= t + 1e-6));
  const to = Math.min(...all.map((c) => c.at).filter((x) => x >= t - 1e-6));
  return to > from ? { from, to } : null;
}

/** Take [from, to] out of the timeline, moving everything after it back. Only for a stretch with nothing in it. */
export function closeGap(e, from, to) {
  const d = to - from;
  const shift = (c) => (c.at >= to - 1e-6 ? { ...c, at: c.at - d } : c);
  return { ...e, video: e.video.map(shift), audio: e.audio.map(shift), music: e.music.map(shift),
    titles: e.titles.map((t) => (t.start >= to - 1e-6 ? { ...t, start: t.start - d, end: t.end - d } : t)) };
}

/** Lift clips, then close the gap they leave if nothing else plays there. */
export function rippleDelete(e, ids) {
  const gone = [...e.video, ...e.audio].filter((c) => ids.includes(c.id));
  if (!gone.length) return e;
  const from = Math.min(...gone.map((c) => c.at)), to = Math.max(...gone.map(clipEnd));
  const lifted = lift(e, ids);
  const blocked = [...lifted.video, ...lifted.audio].some((c) => c.at < to - 1e-6 && clipEnd(c) > from + 1e-6);
  return blocked ? lifted : closeGap(lifted, from, to);
}

/** Room a clip has on its track: from the end of the clip before it to the start of the one after. */
function room(list, c) {
  const before = list.filter((x) => x.id !== c.id && clipEnd(x) <= c.at + 1e-6);
  const after = list.filter((x) => x.id !== c.id && x.at >= clipEnd(c) - 1e-6);
  return { min: Math.max(0, ...before.map(clipEnd)), max: Math.min(Infinity, ...after.map((x) => x.at)) };
}

/** How far a clip's media goes: [earliest start, latest end]. */
const extent = (c, mediaById, duration) => {
  const m = c.media ? mediaById(c.media) : null;
  if (m && m.kind === 'image') return [-Infinity, Infinity];
  return [0, m ? m.duration : duration];
};

/** Drag a clip's edge to t (in place: the rest of the clip doesn't move). Linked sound follows unless alone. */
export function trim(e, track, id, side, t, { alone = false, mediaById, duration }) {
  const c = e[track].find((x) => x.id === id);
  if (!c) return e;
  const d = side === 'start' ? t - c.at : t - clipEnd(c);
  const one = (list, x) => {
    const r = room(list, x), [lo, hi] = extent(x, mediaById, duration);
    if (side === 'start') {
      const at = Math.min(clipEnd(x) - MIN_CLIP, Math.max(r.min, x.at + d, x.at - (x.start - lo)));
      return { ...x, at, start: x.start + (at - x.at) };
    }
    const end = Math.max(x.start + MIN_CLIP, Math.min(x.end + d, x.end + (r.max - clipEnd(x)), hi));
    return { ...x, end };
  };
  const p = alone ? null : partner(e, track, c);
  const next = { ...e, [track]: e[track].map((x) => (x.id === id ? one(e[track], x) : x)) };
  if (p) {
    const other = track === 'video' ? 'audio' : 'video';
    next[other] = next[other].map((x) => (x.id === p.id ? one(e[other], x) : x));
  }
  return next;
}

/** Slide a clip (and its linked partner) to start at `at`, within the free space around it. */
export function move(e, track, id, at, { alone = false } = {}) {
  const c = e[track].find((x) => x.id === id);
  if (!c) return e;
  const p = alone ? null : partner(e, track, c);
  const other = track === 'video' ? 'audio' : 'video';
  let d = at - c.at;
  for (const [list, x] of [[e[track], c], ...(p ? [[e[other], p]] : [])]) {
    const r = room(list, x);
    d = Math.max(d, r.min - x.at);
    d = Math.min(d, r.max - clipEnd(x));
  }
  const shift = (x) => (x.id === c.id || (p && x.id === p.id) ? { ...x, at: x.at + d } : x);
  return { ...e, video: sorted(e.video.map(shift)), audio: sorted(e.audio.map(shift)) };
}

/** Put a library video or image in at t, moving everything after it along. */
export function insert(e, t, item, seconds = 5) {
  const len = item.kind === 'image' ? seconds : item.duration;
  const cut = split(e, t);
  const push = (c) => (c.at >= t - 1e-6 ? { ...c, at: c.at + len } : c);
  const link = uid();
  const next = { ...cut, video: cut.video.map(push), audio: cut.audio.map(push), music: cut.music.map(push),
    titles: cut.titles.map((x) => (x.start >= t - 1e-6 ? { ...x, start: x.start + len, end: x.end + len } : x)) };
  next.video = sorted([...next.video, { id: uid(), at: t, start: 0, end: len, media: item.id, link: item.has_audio ? link : null }]);
  if (item.kind === 'video' && item.has_audio) next.audio = sorted([...next.audio, { id: uid(), at: t, start: 0, end: len, media: item.id, link }]);
  return next;
}

/** Music under the edit from t (as long as the track, or to the end of the edit). */
export function addMusic(e, t, item) {
  const len = Math.min(item.duration, Math.max(MIN_CLIP, total(e) - t));
  return { ...e, music: [...e.music, { id: uid(), at: t, start: 0, end: len, media: item.id, volume: 0.3 }] };
}

export function unlink(e, ids) {
  const links = new Set([...e.video, ...e.audio].filter((c) => ids.includes(c.id) && c.link).map((c) => c.link));
  const free = (c) => (links.has(c.link) ? { ...c, link: null } : c);
  return { ...e, video: e.video.map(free), audio: e.audio.map(free) };
}

/** Link a video clip with an audio clip that lines up with it. */
export function linkUp(e, videoId, audioId) {
  const link = uid();
  const set = (c) => (c.id === videoId || c.id === audioId ? { ...c, link } : c);
  return { ...e, video: e.video.map(set), audio: e.audio.map(set) };
}

/** Where each cut between two touching video clips is: [{id (the clip before it), at, transition}]. */
export function cuts(e) {
  const out = [];
  e.video.forEach((c, i) => {
    const next = e.video[i + 1];
    if (next && Math.abs(clipEnd(c) - next.at) < 0.02) out.push({ id: c.id, at: clipEnd(c), transition: c.transition || null });
  });
  return out;
}

/** Take [from, to] out of both tracks, leaving a gap, or closing it (ripple) if nothing else plays there. */
export function removeRange(e, from, to, ripple = false) {
  const cut = split(split(e, from), to);
  const inside = [...cut.video, ...cut.audio].filter((c) => c.at >= from - 1e-6 && clipEnd(c) <= to + 1e-6).map((c) => c.id);
  const lifted = lift(cut, inside);
  return ripple && !gapBlocked(lifted, from, to) ? closeGap(lifted, from, to) : lifted;
}
const gapBlocked = (e, from, to) => [...e.video, ...e.audio].some((c) => c.at < to - 1e-6 && clipEnd(c) > from + 1e-6);

/** Recording time -> timeline time (where that moment's sound sits), or null if it isn't on the timeline. */
export function timelineAt(e, s) {
  const c = e.audio.find((x) => !x.media && s >= x.start - 0.01 && s <= x.end + 0.01)
    || e.video.find((x) => !x.media && s >= x.start - 0.01 && s <= x.end + 0.01);
  return c ? c.at + Math.min(Math.max(s, c.start), c.end) - c.start : null;
}

/** Timeline time -> recording time (from the sound there, else the picture), or null over a gap or media. */
export function recordingAt(e, t) {
  const c = [clipAt(e.audio, t), clipAt(e.video, t)].find((x) => x && !x.media);
  return c ? c.start + t - c.at : null;
}

/** Where a stretch of the recording [start, end] is on the timeline: [first, last] of what's still there, or null. */
export function spanOf(e, start, end) {
  const parts = e.audio.concat(e.video).filter((c) => !c.media && c.start < end && c.end > start)
    .map((c) => [c.at + Math.max(start, c.start) - c.start, c.at + Math.min(end, c.end) - c.start]);
  return parts.length ? [Math.min(...parts.map((p) => p[0])), Math.max(...parts.map((p) => p[1]))] : null;
}
