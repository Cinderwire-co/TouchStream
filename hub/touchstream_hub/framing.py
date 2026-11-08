"""Framing for vertical shorts: find the speaker, and move a 9:16 window to follow them

Faces are found with YuNet, a 230 KB face detector (MIT licensed, in models/), run with ONNX
Runtime, which comes with transcription. A few frames a second are enough. The window then moves
the way a camera operator would: it holds still while the speaker stays near its middle, and
eases over when they walk out of it. With no face in sight (a slide, the congregation) it holds
where it was, and when the speaker comes back somewhere else it cuts straight to them.
"""
import subprocess
from pathlib import Path
from statistics import median

import numpy

from . import settings
from .editor import video_info   # noqa: F401 (shorts use it from here too)

MODEL = Path(__file__).parent / 'models' / 'face_detection_yunet_2023mar.onnx'
SAMPLE_FPS = 4
SIZE = 640              # the detector's input: frames are scaled to 640 wide and padded square
MIN_SCORE = 0.6
STEP = 1 / 30           # seconds between points of the window's path
DEADZONE = 0.2          # how far (in window widths) the speaker can drift from the middle before it moves
EASE = 3.0              # per second: how quickly it closes the distance once moving
MAX_SPEED = 0.9         # window widths per second
ACCEL = 2.5             # window widths per second, per second
CUT_AFTER = 1.0         # seconds without a face after which the speaker reappearing far away is a cut

try:
    import onnxruntime
    AVAILABLE = MODEL.exists()
except ImportError:
    AVAILABLE = False

_session = None


def faces(frame):
    """Faces in an RGB frame at most 640 wide and tall: [(score, x, y, size)], the center and
    width as fractions of the frame's width and height."""
    global _session
    if _session is None:
        _session = onnxruntime.InferenceSession(str(MODEL), providers=['CPUExecutionProvider'])
    h, w = frame.shape[:2]
    padded = numpy.zeros((SIZE, SIZE, 3), numpy.float32)
    padded[:h, :w] = frame[:, :, ::-1]          # the model wants BGR
    names = [o.name for o in _session.get_outputs()]
    out = dict(zip(names, _session.run(None, {'input': padded.transpose(2, 0, 1)[None]})))
    found = []
    for stride in (8, 16, 32):
        cols = SIZE // stride
        score = numpy.sqrt(numpy.clip(out[f'cls_{stride}'][0, :, 0], 0, 1) * numpy.clip(out[f'obj_{stride}'][0, :, 0], 0, 1))
        box = out[f'bbox_{stride}'][0]
        for i in numpy.nonzero(score > MIN_SCORE)[0]:
            row, col = divmod(int(i), cols)
            found.append((float(score[i]), float((col + box[i, 0]) * stride / w), float((row + box[i, 1]) * stride / h),
                          float(numpy.exp(box[i, 2]) * stride / w)))
    # Neighboring anchors fire for the same face: keep the best of each
    kept = []
    for f in sorted(found, reverse=True):
        if all(abs(f[1] - k[1]) > k[3] or abs(f[2] - k[2]) > k[3] for k in kept):
            kept.append(f)
    return kept


def sample(source, start, end):
    """[(seconds from start, faces)] a few times a second between start and end of the recording."""
    width, height, _ = video_info(source)
    w, h = (SIZE, round(SIZE * height / width / 2) * 2) if width >= height else (round(SIZE * width / height / 2) * 2, SIZE)
    proc = subprocess.Popen([settings.FFMPEG_BIN, '-v', 'error', '-ss', f'{start:.3f}', '-to', f'{end:.3f}',
                             '-i', str(source), '-an', '-vf', f'fps={SAMPLE_FPS},scale={w}:{h}',
                             '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], stdout=subprocess.PIPE)
    out, n = [], 0
    while len(chunk := proc.stdout.read(w * h * 3)) == w * h * 3:
        out.append((n / SAMPLE_FPS, faces(numpy.frombuffer(chunk, numpy.uint8).reshape(h, w, 3))))
        n += 1
    proc.wait()
    return out


def speaker(samples):
    """[(t, x)] of the person most on screen: faces are linked into tracks from sample to sample,
    and at each moment the face from the biggest track (size x time seen) is followed."""
    tracks, joined = [], []
    for t, found in samples:
        here = []
        for score, x, _, size in found:
            near = [k for k in tracks if t - k['t'] <= 2 and abs(k['x'] - x) < max(0.06, 2 * size)]
            track = min(near, key=lambda k: abs(k['x'] - x)) if near else None
            if not track:
                track = {'weight': 0}
                tracks.append(track)
            track.update(t=t, x=x, weight=track['weight'] + size * score)
            here.append((track, x))
        joined.append((t, here))
    points = [(t, max(here, key=lambda h: h[0]['weight'])[1]) for t, here in joined if here]
    # A median of three takes out single-sample jumps
    xs = [x for _, x in points]
    return [(t, median(xs[max(0, i - 1):i + 2])) for i, (t, _) in enumerate(points)]


def follow(points, length, width):
    """The window's center (fraction of the frame's width) every STEP seconds for `length` seconds.
    points: [(t, x)] where the speaker is; width: the window's width as a fraction of the frame's."""
    half = width / 2
    clamp = lambda c: min(max(c, half), 1 - half)
    if not points:
        return [0.5] * (int(length / STEP) + 1)
    cam, speed, moving, i, last_seen = clamp(points[0][1]), 0.0, False, 0, points[0][0]
    target = points[0][1]
    path = []
    for k in range(int(length / STEP) + 1):
        t = k * STEP
        while i < len(points) and points[i][0] <= t:
            gap = points[i][0] - last_seen
            target, last_seen = points[i][1], points[i][0]
            if gap > CUT_AFTER and abs(clamp(target) - cam) > half:
                cam, speed, moving = clamp(target), 0.0, False   # back after a cutaway: cut, don't pan
            i += 1
        off = clamp(target) - cam
        if abs(off) > DEADZONE * width:
            moving = True
        elif abs(off) < 0.03 * width:
            moving = False
        wanted = max(-MAX_SPEED * width, min(MAX_SPEED * width, off * EASE)) if moving else 0.0
        speed += max(-ACCEL * width * STEP, min(ACCEL * width * STEP, wanted - speed))
        cam = clamp(cam + speed * STEP)
        path.append(cam)
    return path


def unseen(points, length, gap=1.5):
    """[(start, end)]: stretches longer than `gap` seconds with nobody in the picture (a slide, a wide shot).
    The short shows the whole picture there instead of a window on an empty part of it."""
    times = [0.0] + [t for t, _ in points] + [length]
    out = []
    for i in range(len(times) - 1):
        a, b = times[i] + (1 / SAMPLE_FPS if i else 0.0), times[i + 1]
        if b - a >= gap:
            out.append((round(a, 2), round(b, 2)))
    return out if points else [(0.0, round(length, 2))]


def plan(source, start, end):
    """How a 'fill' short of start-end will be framed, for the editor's preview: {width (the window, as a
    fraction of the frame's width), path [[t, center]] ten times a second, whole [[start, end]]}, t from start."""
    width, height, _ = video_info(source)
    crop = round(height * 9 / 16 / 2) * 2 / width
    points = speaker(sample(source, start, end))
    path = follow(points, end - start, crop)
    step = max(1, round(0.1 / STEP))
    return {'width': crop, 'path': [[round(k * STEP, 2), round(c, 4)] for k, c in enumerate(path) if k % step == 0],
            'whole': unseen(points, end - start)}


def crop_commands(path, frame_width, crop_width):
    """(FFmpeg sendcmd lines moving the crop named crop@follow along the path, its first x)."""
    xs = [max(0, min(frame_width - crop_width, int(round((c * frame_width - crop_width / 2) / 2)) * 2)) for c in path]
    lines = [f'{k * STEP:.3f} crop@follow x {x};' for k, x in enumerate(xs) if k == 0 or x != xs[k - 1]]
    return '\n'.join(lines) + '\n', xs[0] if xs else 0
