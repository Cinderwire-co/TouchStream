"""Core logic that's easy to break quietly. Run: python -m unittest discover -s tests"""
import unittest
from types import SimpleNamespace

from touchstream_hub.editor import keep_ranges
from touchstream_hub.recordings import match
from touchstream_hub.transcribe import captions_from_words, remap, to_srt


class EditRanges(unittest.TestCase):
    def test_trim_and_cut(self):
        self.assertEqual(keep_ranges({'in': 5, 'out': 120, 'cuts': [[40, 60]]}, 130), [(5, 40), (60, 120)])

    def test_overlapping_cuts_merge(self):
        self.assertEqual(keep_ranges({'in': 0, 'cuts': [[10, 20], [15, 30]]}, 60), [(0, 10), (30, 60)])

    def test_cut_outside_trim_is_ignored(self):
        self.assertEqual(keep_ranges({'in': 10, 'out': 50, 'cuts': [[0, 5], [55, 60]]}, 60), [(10, 50)])


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


if __name__ == '__main__':
    unittest.main()
