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
