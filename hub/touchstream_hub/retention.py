"""Old recordings: delete a broadcast's recordings after N days, or to keep the folder under N GB

Only the recordings themselves (the hub's and the spoke's copy) are ever deleted. Edited
videos, thumbnails, transcripts, captions and the broadcast's details stay, in its folder
and in the dashboard. A broadcast that isn't on YouTube (record-only, never published)
is kept unless the user also allows that: its recording may be the only copy anywhere.

The size limit counts every video in the recordings folder, edits included, and
deletes the oldest broadcasts' recordings until the total is under it (or nothing
more may go). Spokes look after their own cards (spoke/touchstream/recorder.py).
"""
import threading
import time
from pathlib import Path

CHECK_SECONDS = 3600
DAY = 86400
GB = 1e9


class Retention:
    def __init__(self, db, library):
        self.db = db
        self.library = library
        self.lock = threading.Lock()

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            time.sleep(CHECK_SECONDS)
            try:
                self.apply()
            except Exception as e:
                print(f"Retention error: {e}")

    def rules(self):
        """{'days': int | None, 'max_gb': int | None, 'unpublished': bool}"""
        return {'days': None, 'max_gb': None, 'unpublished': False, **(self.db.get_setting('retention') or {})}

    def on_youtube(self, event_id):
        """Whether the broadcast went out on YouTube (live, or an edit published), so YouTube has it too."""
        aired = self.db.one("SELECT 1 FROM events e JOIN event_targets t ON t.event_id = e.id "
                            "WHERE e.id = ? AND e.started_at IS NOT NULL AND t.broadcast_id IS NOT NULL", event_id)
        published = self.db.one("SELECT 1 FROM edits WHERE event_id = ? AND published IS NOT NULL AND published != '[]'",
                                event_id)
        return bool(aired or published)

    def _files(self):
        """Every video in the recordings folder: [{id, event_id, kind, path, size, start}]."""
        out = []
        for row in self.db.all('SELECT l.*, e.start_at, e.started_at FROM library l JOIN events e ON e.id = l.event_id'):
            path = Path(row['path'])
            if path.is_file():
                out.append({**row, 'size': path.stat().st_size, 'aired': row['started_at'] or row['start_at']})
        return out

    def plan(self, rules=None, now=None):
        """What these rules would delete now: {'delete': [file rows], 'bytes', 'total', 'kept_unpublished', 'over'}."""
        rules = rules or self.rules()
        now = now or time.time()
        files = self._files()
        total = sum(f['size'] for f in files)
        # Oldest broadcasts first; the newest few days are never touched by the size limit
        recordings = sorted((f for f in files if f['kind'] != 'edit'), key=lambda f: f['aired'])
        busy = self.library._in_use()   # being edited or transcribed right now
        youtube, kept, delete, freed = {}, set(), [], 0
        for f in recordings:
            if f['event_id'] not in youtube:
                youtube[f['event_id']] = self.on_youtube(f['event_id'])
            if not youtube[f['event_id']] and not rules['unpublished']:
                kept.add(f['event_id'])
                continue
            too_old = rules['days'] and f['aired'] < now - rules['days'] * DAY
            too_big = rules['max_gb'] and total - freed > rules['max_gb'] * GB and f['aired'] < now - 3 * DAY
            if (too_old or too_big) and f"library/{f['id']}" not in busy:
                delete.append(f)
                freed += f['size']
        left = total - freed
        return {'delete': delete, 'bytes': freed, 'total': total,
                'broadcasts': len({f['event_id'] for f in delete}), 'kept_unpublished': len(kept),
                'over': bool(rules['max_gb'] and left > rules['max_gb'] * GB)}

    def apply(self):
        rules = self.rules()
        if not (rules['days'] or rules['max_gb']):
            return 0
        with self.lock:
            plan = self.plan(rules)
            for f in plan['delete']:
                if self.library.delete(f"library/{f['id']}"):
                    self.db.run('UPDATE events SET recordings_removed_at = ? WHERE id = ?', time.time(), f['event_id'])
            if plan['delete']:
                print(f"Old recordings: deleted {len(plan['delete'])} file(s), {plan['bytes'] / GB:.1f} GB")
            return len(plan['delete'])
