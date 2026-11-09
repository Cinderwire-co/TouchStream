"""A spoke's settings from the dashboard: its time zone, an IANA name, or none (where it finds itself to be)"""
import unittest

from fastapi import HTTPException

from touchstream_hub.app import SPOKE_SHARED, _spoke_fields


class SpokeSettings(unittest.TestCase):
    def test_time_zone(self):
        self.assertEqual(_spoke_fields({'time_zone': 'America/Denver'}), {'time_zone': 'America/Denver'})
        self.assertEqual(_spoke_fields({'time_zone': ''}), {'time_zone': ''})   # automatic
        with self.assertRaises(HTTPException):
            _spoke_fields({'time_zone': 'Mars/Olympus'})
        # Each spoke's own: not one of the settings for all spokes
        self.assertEqual(_spoke_fields({'time_zone': 'Europe/Paris'}, SPOKE_SHARED), {})

    def test_what_each_spoke_can_do(self):
        from touchstream_hub.app import spoke_cant, spoke_kbps
        box = {'status': {'framerates': ['30', '60'], 'min_kbps': 2000, 'max_kbps': 10000}}
        self.assertEqual(spoke_kbps(box), (2000, 10000))
        self.assertIsNone(spoke_cant(box, 'bitrate_kbps', 8000))
        self.assertIn('2 to 10 Mbps', spoke_cant(box, 'bitrate_kbps', 12000))
        self.assertIn('30, 60 fps', spoke_cant(box, 'framerate', '25'))
        # One that hasn't said (0.1.5 and before): the PicoKVM's encoder, up to 20 Mbps
        self.assertEqual(spoke_kbps({'status': {}}), (1000, 20000))
        self.assertIsNotNone(spoke_cant({'status': None}, 'bitrate_kbps', 25000))
        with self.assertRaises(HTTPException):
            _spoke_fields({'bitrate_kbps': 25000})
