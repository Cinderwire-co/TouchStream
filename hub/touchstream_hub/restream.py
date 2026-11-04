"""Restreaming: one FFmpeg process per destination while its spoke is live

The spoke's stream is passed through untouched (no quality loss, almost no CPU)
unless a destination asks for a lower bitrate, in which case it is re-encoded.
"""
import collections
import subprocess
import threading
import time

from . import settings


def build_command(spoke_id, dest):
    source = f'rtsp://127.0.0.1:{settings.MTX_RTSP_PORT}/spoke/{spoke_id}'
    cmd = [settings.FFMPEG_BIN, '-hide_banner', '-nostdin', '-loglevel', 'warning',
           '-rtsp_transport', 'tcp', '-i', source]
    kbps = dest['transcode_kbps']
    if kbps:
        cmd += ['-c:v', 'libx264', '-preset', 'veryfast', '-pix_fmt', 'yuv420p',
                '-b:v', f'{kbps}k', '-maxrate', f'{kbps}k', '-bufsize', f'{kbps * 2}k',
                '-g', '60', '-keyint_min', '60', '-sc_threshold', '0',
                '-c:a', 'aac', '-b:a', '160k', '-ar', '48000']
    else:
        cmd += ['-c', 'copy']
    target = dest['url'].rstrip('/')
    if dest['stream_key']:
        target += '/' + dest['stream_key']
    if target.startswith('srt://'):
        cmd += ['-f', 'mpegts', target]
    else:
        cmd += ['-f', 'flv', target]
    return cmd


class Worker:
    def __init__(self, spoke_id, dest):
        self.spoke_id = spoke_id
        self.dest = dest
        self.proc = None
        self.log = collections.deque(maxlen=20)
        self.started_at = None
        self.failures = 0
        self.next_try = 0

    @property
    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self):
        self.proc = subprocess.Popen(build_command(self.spoke_id, self.dest),
                                     stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        self.started_at = time.time()
        threading.Thread(target=self._read_log, args=(self.proc,), daemon=True).start()

    def _read_log(self, proc):
        for line in proc.stderr:
            # Never keep the stream key in logs shown in the UI
            line = line.rstrip()
            if self.dest['stream_key']:
                line = line.replace(self.dest['stream_key'], '****')
            self.log.append(line)

    def stop(self):
        if self.running:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

    def check(self):
        """Restart a crashed process with backoff (5s, 10s, ... up to 60s)."""
        if self.running or time.time() < self.next_try:
            return
        if self.proc is not None:
            ran_for = time.time() - (self.started_at or 0)
            self.failures = 0 if ran_for > 60 else self.failures + 1
            self.next_try = time.time() + min(60, 5 * 2 ** min(self.failures, 4))
            self.proc = None
            return
        self.start()

    def state(self):
        if self.running:
            # FFmpeg only logs at warning level, so a quiet process is a healthy one
            return {'state': 'live', 'since': self.started_at, 'error': None}
        return {'state': 'retrying', 'since': None, 'error': self.log[-1] if self.log else 'Disconnected'}


class Restreamer:
    def __init__(self, db, mtx, broadcasts):
        self.db = db
        self.mtx = mtx
        self.broadcasts = broadcasts
        self.workers = {}   # destination id -> Worker
        self.lock = threading.Lock()

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                self.reconcile()
            except Exception as e:
                print(f"Restream error: {e}")
            time.sleep(2)

    def reconcile(self):
        wanted = {}
        for spoke in self.db.spokes():
            if spoke['mode'] != 'live' or not self.mtx.ready(f"spoke/{spoke['id']}"):
                continue
            # Channel destinations only during the broadcast that targets them;
            # stream-key destinations whenever they're switched on
            scheduled = self.broadcasts.streaming_destination_ids(spoke['id'])
            for dest in self.db.destinations(spoke['id']):
                if (dest['id'] in scheduled) if dest['channel_id'] else dest['enabled']:
                    wanted[dest['id']] = (spoke['id'], dest)
        with self.lock:
            for dest_id in list(self.workers):
                worker = self.workers[dest_id]
                # Stop workers that are no longer wanted, or whose settings changed
                if dest_id not in wanted or wanted[dest_id][1] != worker.dest:
                    worker.stop()
                    del self.workers[dest_id]
            for dest_id, (spoke_id, dest) in wanted.items():
                if dest_id not in self.workers:
                    self.workers[dest_id] = Worker(spoke_id, dest)
                self.workers[dest_id].check()

    def state(self, dest_id):
        with self.lock:
            worker = self.workers.get(dest_id)
            return worker.state() if worker else {'state': 'idle', 'since': None, 'error': None}

    def stop_all(self):
        with self.lock:
            for worker in self.workers.values():
                worker.stop()
            self.workers.clear()
