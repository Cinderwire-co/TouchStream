"""Recurring broadcasts: which dates a rule gives, times across daylight saving, and keeping
each date's broadcast in step with its series"""
import json
import shutil
import tempfile
import time
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from touchstream_hub import settings
from touchstream_hub.broadcasts import Broadcasts
from touchstream_hub.db import Database
from touchstream_hub.series import Series, dates, times, title

CHICAGO = 'America/Chicago'


def rule(days, first, weeks='every', months=(), until=None, skips=(), add_date=1, start_minutes=600, length=90):
    return {'repeat': json.dumps({'days': list(days), 'weeks': weeks, 'months': list(months)}), 'first_date': first,
            'until_date': until, 'skips': json.dumps(list(skips)), 'title': 'Sunday Service', 'add_date': add_date,
            'start_minutes': start_minutes, 'length_minutes': length, 'timezone': CHICAGO}


SUN, WED = 6, 2


def between(series, a, b):
    return dates(series, date.fromisoformat(a), date.fromisoformat(b))


class Rules(unittest.TestCase):
    def test_every_sunday_from_the_first_date(self):
        self.assertEqual(between(rule([SUN], '2026-11-04'), '2026-11-01', '2026-11-30'),
                         ['2026-11-08', '2026-11-15', '2026-11-22', '2026-11-29'])

    def test_two_days_every_other_week_counted_from_the_first_date(self):
        s = rule([SUN, WED], '2026-11-08', weeks='other')
        self.assertEqual(between(s, '2026-11-01', '2026-11-30'), ['2026-11-08', '2026-11-11', '2026-11-22', '2026-11-25'])

    def test_until_a_date_with_one_skipped(self):
        s = rule([SUN], '2026-11-08', until='2026-11-29', skips=['2026-11-15'])
        self.assertEqual(between(s, '2026-11-01', '2027-02-01'), ['2026-11-08', '2026-11-22', '2026-11-29'])

    def test_weekdays(self):
        self.assertEqual(between(rule(range(5), '2026-11-06'), '2026-11-06', '2026-11-10'),
                         ['2026-11-06', '2026-11-09', '2026-11-10'])

    def test_first_and_last_sunday_of_the_month(self):
        self.assertEqual(between(rule([SUN], '2026-11-01', weeks=[1, -1]), '2026-11-01', '2027-01-31'),
                         ['2026-11-01', '2026-11-29', '2026-12-06', '2026-12-27', '2027-01-03', '2027-01-31'])

    def test_only_some_months(self):
        # Sundays in June and July only (none picked would mean every month)
        got = between(rule([SUN], '2027-01-01', months=[6, 7]), '2027-01-01', '2027-12-31')
        self.assertEqual({d[5:7] for d in got}, {'06', '07'})
        self.assertEqual(len(got), 8)

    def test_time_of_day_holds_across_daylight_saving(self):
        s = rule([SUN], '2026-10-25')
        for day in ('2026-10-25', '2026-11-01', '2026-11-08'):   # clocks go back on 1 November
            start, end = times(s, day)
            local = datetime.fromtimestamp(start, ZoneInfo(CHICAGO))
            self.assertEqual((local.hour, local.minute), (10, 0))
            self.assertEqual(end - start, 90 * 60)

    def test_title_with_and_without_the_date(self):
        self.assertEqual(title(rule([SUN], '2026-11-08'), '2026-11-08'), 'Sunday Service · November 8, 2026')
        self.assertEqual(title(rule([SUN], '2026-11-08', add_date=0), '2026-11-08'), 'Sunday Service')


class Upkeep(unittest.TestCase):
    """A series makes the coming week's broadcasts and keeps them in line (no YouTube channels here)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.saved = settings.DATA_DIR
        settings.DATA_DIR = self.tmp
        self.db = Database(self.tmp / 'hub.db')
        self.db.run("INSERT INTO spokes (id, token, stream_key, name, created_at) VALUES ('s1', 't', 'k', 'Hall', ?)", time.time())
        self.series = Series(self.db, Broadcasts(self.db, youtube=None))
        tomorrow = (datetime.now(ZoneInfo(CHICAGO)).date() + timedelta(days=1)).isoformat()
        fields = {'spoke_id': 's1', 'title': 'Morning Prayer', 'add_date': 1, 'description': '', 'privacy': 'public',
                  'latency': 'normal', 'made_for_kids': 0, 'auto_start': 1, 'first_date': tomorrow,
                  'repeat': json.dumps({'days': list(range(7)), 'weeks': 'every', 'months': []}),
                  'start_minutes': 7 * 60, 'length_minutes': 30, 'until_date': None, 'timezone': CHICAGO}
        self.id = self.series.create(fields, [])

    def tearDown(self):
        settings.DATA_DIR = self.saved
        shutil.rmtree(self.tmp)

    def events(self):
        return self.db.all('SELECT * FROM events WHERE series_id = ? ORDER BY start_at', self.id)

    def test_makes_the_coming_week_and_plans_the_rest(self):
        made = self.events()
        self.assertTrue(7 <= len(made) <= 8)
        self.assertTrue(all(e['title'].startswith('Morning Prayer · ') for e in made))
        planned = self.series.planned()
        self.assertTrue(planned and planned[0]['start_at'] > made[-1]['start_at'])

    def test_series_changes_skip_broadcasts_changed_on_their_own(self):
        first, second = self.events()[:2]
        self.db.run('UPDATE events SET title = ?, detached = 1 WHERE id = ?', 'Special guest', second['id'])
        self.series.update(self.id, {'title': 'Dawn Prayer', 'add_date': 0, 'start_minutes': 8 * 60})
        after = {e['id']: e for e in self.events()}
        self.assertEqual(after[first['id']]['title'], 'Dawn Prayer')
        self.assertEqual(after[first['id']]['start_at'] - first['start_at'], 3600)
        self.assertEqual(after[second['id']]['title'], 'Special guest')

    def test_a_new_rule_removes_dates_it_no_longer_has(self):
        first = date.fromisoformat(self.events()[0]['occurrence'])
        self.series.update(self.id, {'repeat': json.dumps({'days': [first.weekday()], 'weeks': 'every', 'months': []})})
        left = [date.fromisoformat(e['occurrence']) for e in self.events()]
        self.assertEqual(left[0], first)
        self.assertTrue(len(left) <= 2 and all((d - first).days % 7 == 0 for d in left))

    def test_a_skipped_date_is_not_made_again(self):
        gone = self.events()[1]
        self.series.skip(self.id, gone['occurrence'])
        self.db.run('DELETE FROM events WHERE id = ?', gone['id'])
        self.series.create_due(self.id)
        self.assertNotIn(gone['occurrence'], [e['occurrence'] for e in self.events()])

    def test_ending_a_series_keeps_one_broadcast(self):
        keep = self.events()[0]['id']
        self.series.delete(self.id, keep=keep)
        left = self.db.all('SELECT id, series_id FROM events')
        self.assertEqual(left, [{'id': keep, 'series_id': None}])
        self.assertIsNone(self.series.get(self.id))


if __name__ == '__main__':
    unittest.main()
