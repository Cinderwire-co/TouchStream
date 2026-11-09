"""A brand's default thumbnail (a JPG or PNG under 2 MB, the one a channel's broadcasts get when they have none),
and its title color"""
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from touchstream_hub import settings
from touchstream_hub.broadcasts import Broadcasts
from touchstream_hub.db import Database
from touchstream_hub.media import Media


class FakeYouTube:
    def __init__(self):
        self.thumbnails = []
        self.deleted = []

    def channel(self, row_id):
        return {'id': row_id, 'title': 'Test Channel'}

    def create_stream(self, channel, title):
        return 'stream1', 'rtmp://a.rtmp.youtube.com/live2', 'key'

    def create_broadcast(self, channel, event, stream_id):
        return f"bcast-{event['id']}"

    def delete_broadcast(self, channel, broadcast_id):
        self.deleted.append(broadcast_id)

    def set_thumbnail(self, channel, video_id, image, content_type):
        self.thumbnails.append((video_id, image, content_type))


class BrandThumbnail(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        patch = mock.patch.object(settings, 'DATA_DIR', self.tmp)
        patch.start()
        self.addCleanup(patch.stop)
        self.db = Database(self.tmp / 'hub.db')
        self.media = Media(self.db)
        (self.tmp / 'media').mkdir()
        self.channel = self.db.run("INSERT INTO youtube_channels (channel_id, title, refresh_token, created_at) VALUES ('UC1', 'Test', 'rt', ?)", time.time())
        self.db.run("INSERT INTO spokes (id, token, stream_key, name, created_at) VALUES ('sp1', 't', 'k', 'Sanctuary', ?)", time.time())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def image(self, name, size=1000):
        (self.tmp / 'media' / name).write_bytes(b'\xff\xd8' + b'0' * size)
        return self.db.run("INSERT INTO media (name, kind, file, created_at) VALUES (?, 'image', ?, ?)", name, name, time.time())

    def test_a_jpg_or_png_under_2_mb(self):
        brand = self.media.save_brand(None, {'name': 'Sunday', 'thumbnail': self.image('thumb.jpg'), 'channel_ids': [self.channel]})
        self.assertTrue(brand['thumbnail'])
        with self.assertRaisesRegex(ValueError, 'JPG or PNG'):
            self.media.save_brand(brand['id'], {'thumbnail': self.image('thumb.webp')})
        with self.assertRaisesRegex(ValueError, '2 MB'):
            self.media.save_brand(brand['id'], {'thumbnail': self.image('big.jpg', 3 * 1024 * 1024)})

    def test_a_channels_broadcasts_without_a_thumbnail_get_the_brands(self):
        self.media.save_brand(None, {'name': 'Sunday', 'thumbnail': self.image('thumb.png'), 'channel_ids': [self.channel]})
        image, content_type = self.media.channel_thumbnail(self.channel)
        self.assertEqual(content_type, 'image/png')
        youtube = FakeYouTube()
        broadcasts = Broadcasts(self.db, youtube)
        broadcasts.thumbnail_for = self.media.channel_thumbnail
        fields = {'spoke_id': 'sp1', 'title': 'Sunday Service', 'description': '', 'start_at': time.time() + 86400, 'end_at': None,
                  'privacy': 'public', 'latency': 'normal', 'made_for_kids': False, 'auto_start': True}
        event = broadcasts.create(fields, [self.channel])
        self.assertEqual(youtube.thumbnails, [(f'bcast-{event}', image, 'image/png')])
        # one with its own thumbnail keeps it
        (self.tmp / 'thumbnails').mkdir(exist_ok=True)
        (self.tmp / 'thumbnails' / 'own.jpg').write_bytes(b'own')
        second = self.db.run("INSERT INTO events (spoke_id, title, start_at, thumbnail, created_at) VALUES ('sp1', 'Own', ?, 'own.jpg', ?)", time.time() + 86400, time.time())
        self.db.run('INSERT INTO event_targets (event_id, channel_id) VALUES (?, ?)', second, self.channel)
        broadcasts.sync(second)
        self.assertEqual(youtube.thumbnails[-1], (f'bcast-{second}', b'own', 'image/jpeg'))

    def test_taking_a_channel_off_an_aired_broadcast_keeps_its_youtube_video(self):
        youtube = FakeYouTube()
        broadcasts = Broadcasts(self.db, youtube)
        fields = {'spoke_id': 'sp1', 'title': 'Sunday Service', 'description': '', 'start_at': time.time() + 86400, 'end_at': None,
                  'privacy': 'public', 'latency': 'normal', 'made_for_kids': False, 'auto_start': True}
        upcoming = broadcasts.create(fields, [self.channel])
        broadcasts.update(upcoming, {}, channel_ids=[])
        self.assertEqual(youtube.deleted, [f'bcast-{upcoming}'], 'an upcoming one is removed from YouTube')
        aired = broadcasts.create(fields, [self.channel])
        self.db.run("UPDATE events SET state = 'ended' WHERE id = ?", aired)
        broadcasts.update(aired, {}, channel_ids=[])
        self.assertEqual(youtube.deleted, [f'bcast-{upcoming}'], 'an aired one stays: it is the channel\'s video')
        self.assertEqual(broadcasts.targets(aired), [])

    def test_no_brand_no_thumbnail(self):
        self.assertIsNone(self.media.channel_thumbnail(self.channel))

    def test_title_color_is_a_hex_color_or_none(self):
        brand = self.media.save_brand(None, {'name': 'Sunday', 'title_color': '#2f6b4f'})
        self.assertEqual(brand['title_color'], '#2F6B4F')
        with self.assertRaisesRegex(ValueError, 'title color'):
            self.media.save_brand(brand['id'], {'title_color': 'green'})
        self.assertIsNone(self.media.save_brand(brand['id'], {'title_color': ''})['title_color'])


class TitleColor(unittest.TestCase):
    def test_titles_take_the_brands_color(self):
        try:
            from touchstream_hub import titles
        except ImportError:
            self.skipTest('needs Pillow')
        bar = (96 + 5, 1080 - 110 - 10)      # the lower third's colored bar
        self.assertEqual(titles.draw({'style': 'lower_third', 'text': 'Pastor Sam'}, accent='#2F6B4F').getpixel(bar), (47, 107, 79, 255))
        self.assertEqual(titles.draw({'style': 'lower_third', 'text': 'Pastor Sam'}).getpixel(bar), titles.EMBER)
        corner = titles.draw({'style': 'corner', 'text': 'Live'}, accent='#FFFFFF')
        self.assertEqual(corner.getpixel((66, 80)), (255, 255, 255, 255))   # inside the tag, left of its text


if __name__ == '__main__':
    unittest.main()
