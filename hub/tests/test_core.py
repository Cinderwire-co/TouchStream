"""Core logic that's easy to break quietly. Run: python -m unittest discover -s tests"""
import unittest
from types import SimpleNamespace

from touchstream_hub.editor import keep_ranges, normalize, placements
from touchstream_hub.recordings import match
from touchstream_hub.transcribe import captions_from_words, remap, to_srt


class EditRanges(unittest.TestCase):
    def test_trim_and_cut(self):
        self.assertEqual(keep_ranges({'in': 5, 'out': 120, 'cuts': [[40, 60]]}, 130), [(5, 40), (60, 120)])

    def test_overlapping_cuts_merge(self):
        self.assertEqual(keep_ranges({'in': 0, 'cuts': [[10, 20], [15, 30]]}, 60), [(0, 10), (30, 60)])

    def test_cut_outside_trim_is_ignored(self):
        self.assertEqual(keep_ranges({'in': 10, 'out': 50, 'cuts': [[0, 5], [55, 60]]}, 60), [(10, 50)])

    def test_edits_as_tracks(self):
        edit = normalize({'in': 0, 'cuts': [[10, 20]], 'dip': 0.5}, 60)           # an edit from before tracks
        self.assertEqual([(c['at'], c['start'], c['end']) for c in edit['video']], [(0, 0, 10), (10, 20, 60)])
        self.assertEqual(len(edit['audio']), 2)                                     # linked sound
        self.assertEqual(edit['video'][0]['transition'], {'type': 'dip_black', 'duration': 1.0})
        edit = normalize({'video': [{'at': 0, 'start': 0, 'end': 4, 'transition': {'type': 'dissolve', 'duration': 2}},
                                    {'at': 4, 'start': 6, 'end': 7}, {'at': 10, 'start': 20, 'end': 30}],
                          'audio': [{'at': 0, 'start': 0, 'end': 5}]}, 60)
        self.assertEqual(edit['video'][0]['transition'], {'type': 'dissolve', 'duration': 1.0})   # no longer than a side
        self.assertEqual(edit['total'], 20)                                         # the gap from 5 to 10 stays
        self.assertEqual(placements(edit), [(0, 5, 0)])                             # what's said, for captions


class Captions(unittest.TestCase):
    def test_remap_follows_cuts(self):
        segs = [{'start': 0, 'end': 3, 'text': 'a'}, {'start': 5, 'end': 7, 'text': 'cut'},
                {'start': 9, 'end': 11, 'text': 'b'}]
        out = remap(segs, [(0, 4), (8, 12)])
        self.assertEqual([s['text'] for s in out], ['a', 'b'])
        self.assertEqual((out[1]['start'], out[1]['end']), (5, 7))   # 9-11 minus the 4 s cut

    def test_words_group_into_short_lines(self):
        words = [SimpleNamespace(start=i * 0.5, end=i * 0.5 + 0.4, word=f' w{i}.' if i % 5 == 4 else f' w{i}')
                 for i in range(30)]
        lines = captions_from_words(words)
        self.assertTrue(all(l['end'] - l['start'] <= 6.5 for l in lines))
        self.assertEqual(' '.join(l['text'] for l in lines).count('w'), 30)

    def test_srt_format(self):
        self.assertIn('00:00:01,500 --> 00:00:02,000\nhi', to_srt([{'start': 1.5, 'end': 2, 'text': 'hi'}]))


class RecordingMatch(unittest.TestCase):
    def test_copies_join_the_overlapping_broadcast(self):
        events = [{'id': 1, 'start_at': 100, 'started_at': 100, 'ended_at': 200, 'state': 'ended'},
                  {'id': 2, 'start_at': 1000, 'started_at': 1000, 'ended_at': 1100, 'state': 'ended'}]
        copies = [{'start': 90, 'end': 205, 'source': 'spoke'}, {'start': 101, 'end': 199, 'source': 'hub'},
                  {'start': 5000, 'end': 5100, 'source': 'hub'}]
        grouped, unmatched = match(events, copies, now=6000)
        self.assertEqual(len(grouped[1]), 2)
        self.assertNotIn(2, grouped)
        self.assertEqual(len(unmatched), 1)

    def test_a_recording_just_before_a_broadcast_stays_on_its_own(self):
        # Recorded on the touchscreen, stopped, then live a minute later
        events = [{'id': 1, 'start_at': 1000, 'started_at': 1000, 'ended_at': 1070, 'state': 'ended'}]
        copies = [{'start': 847, 'end': 938, 'source': 'spoke'}, {'start': 1002, 'end': 1071, 'source': 'spoke'},
                  {'start': 1072, 'end': 1080, 'source': 'hub'}]   # the last few seconds, after the end
        grouped, unmatched = match(events, copies, now=6000)
        self.assertEqual([c['start'] for c in grouped[1]], [1002, 1072])
        self.assertEqual([c['start'] for c in unmatched], [847])


if __name__ == '__main__':
    unittest.main()
