"""EDIDs the capture card shows to HDMI sources. Run from spoke/: python3 -m unittest discover -s tests"""
import unittest

from touchstream import edid


class Edid(unittest.TestCase):
    def test_blocks_have_valid_checksums(self):
        for rate in edid.RATES:
            data = edid.build(rate)
            self.assertEqual(len(data), 256)
            self.assertEqual(sum(data[:128]) % 256, 0, rate)
            self.assertEqual(sum(data[128:]) % 256, 0, rate)

    def test_preferred_timing_is_the_chosen_rate(self):
        for rate in edid.RATES:
            dtd = edid.build(rate)[54:72]
            clock = (dtd[0] | dtd[1] << 8) * 10_000
            h_total = 1920 + (dtd[3] | (dtd[4] & 0x0F) << 8)
            v_total = 1080 + (dtd[6] | (dtd[7] & 0x0F) << 8)
            self.assertAlmostEqual(clock / (h_total * v_total), int(rate), places=3)

    def test_only_one_hd_mode_is_offered(self):
        for rate, (_, _, _, vic) in edid.TIMINGS.items():
            ext = edid.build(rate)[128:]
            self.assertEqual(list(ext[4:7]), [0x42, vic, 1])   # 1080p at the rate, plus 640x480


if __name__ == '__main__':
    unittest.main()
