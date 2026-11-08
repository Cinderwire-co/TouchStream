"""Updates: finding a new release, what's offered to spokes and when, and the spoke's file from the release"""
import hashlib
import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import httpx

from touchstream_hub import settings, updates
from touchstream_hub.db import Database
from touchstream_hub.updates import Updates, newer, parse

BUNDLE = b'pretend spoke release' * 100
META = {'version': '0.3.0', 'sha256': hashlib.sha256(BUNDLE).hexdigest(), 'size': len(BUNDLE), 'signature': 'c2ln'}


class Versions(unittest.TestCase):
    def test_releases_compare_by_number(self):
        self.assertEqual(parse('v0.10.2'), (0, 10, 2))
        self.assertIsNone(parse('0.1.0-dev'))
        self.assertTrue(newer('0.10.0', '0.9.9'))
        self.assertFalse(newer('0.3.0', '0.3.0'))
        self.assertFalse(newer('0.3.0', '0.1.0-dev'))   # a developer's build is left alone

    def test_install_kind(self):
        with mock.patch.dict(os.environ, {'TOUCHSTREAM_INSTALL': 'docker'}):
            self.assertEqual(updates.install_kind(), 'docker')


class Hub(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = Database(self.tmp / 'hub.db')
        patch = mock.patch.object(settings, 'DATA_DIR', self.tmp)
        patch.start()
        self.addCleanup(patch.stop)
        self.updates = Updates(self.db, version='0.3.0')
        self.requests = []
        self.answer({})

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def answer(self, routes):
        def handler(request):
            self.requests.append(str(request.url))
            for suffix, reply in routes.items():
                if str(request.url).endswith(suffix):
                    return reply() if callable(reply) else reply
            return httpx.Response(404)
        self.updates.http = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)

    def spoke(self, version='0.2.0', **status):
        return {'id': 'abc', 'name': 'Sanctuary', 'status': {'version': version, **status}}

    def test_check_finds_the_latest_release(self):
        self.answer({'/releases/latest': httpx.Response(200, json={'tag_name': 'v0.4.0', 'body': 'Better sound',
                                                                   'html_url': 'https://x/v0.4.0'})})
        self.updates.check()
        info = self.updates.info()
        self.assertTrue(info['available'])
        self.assertEqual((info['latest'], info['notes']), ('0.4.0', 'Better sound'))

    def test_check_survives_github_being_down(self):
        self.answer({'/releases/latest': httpx.Response(503)})
        self.updates.check()
        self.assertIn("Couldn't reach GitHub", self.updates.info()['error'])
        self.assertFalse(self.updates.info()['available'])

    def release_files(self):
        self.answer({'/v0.3.0/touchstream-spoke-0.3.0-picokvm.json': httpx.Response(200, json=META),
                     '/v0.3.0/touchstream-spoke-0.3.0-picokvm.tar.gz': httpx.Response(200, content=BUNDLE)})

    def test_an_idle_spoke_behind_the_hub_is_offered_its_version(self):
        self.release_files()
        offer = self.updates.spoke_offer(self.spoke(), self.spoke()['status'], idle=True)
        self.assertEqual(offer, {'version': '0.3.0', 'url': '/api/spoke/update/0.3.0', 'sha256': META['sha256'],
                                 'size': META['size'], 'signature': 'c2ln'})
        self.assertEqual(self.updates.bundle()['path'].read_bytes(), BUNDLE)
        fetched = len(self.requests)
        self.updates.spoke_offer(self.spoke(), self.spoke()['status'], idle=True)
        self.assertEqual(len(self.requests), fetched, 'the release is fetched once, then kept')

    def test_no_offer_when_busy_current_a_developers_or_already_failed(self):
        self.release_files()
        for spoke, idle in [(self.spoke(), False), (self.spoke('0.3.0'), True), (self.spoke('0.1.0-dev'), True),
                            (self.spoke(update_failed='0.3.0'), True), (self.spoke(update={'version': '0.3.0'}), True)]:
            self.assertIsNone(self.updates.spoke_offer(spoke, spoke['status'], idle), spoke['status'])

    def test_by_hand_the_spoke_waits_for_update_to_be_chosen(self):
        self.release_files()
        self.updates.set_auto(False)
        spoke = self.spoke(update_failed='0.3.0')   # (choosing Update tries a failed one again)
        self.assertIsNone(self.updates.spoke_offer(spoke, spoke['status'], idle=True))
        self.assertEqual(self.updates.spoke_view(spoke, True)['state'], 'failed')
        self.updates.request_spoke('abc')
        self.assertIsNotNone(self.updates.spoke_offer(spoke, spoke['status'], idle=True))
        self.assertNotIn('abc', self.updates.requested_spokes(), 'asked once, offered once')

    def test_a_damaged_download_is_thrown_away(self):
        self.answer({'.json': httpx.Response(200, json=META), '.tar.gz': httpx.Response(200, content=BUNDLE[:-5])})
        self.assertIsNone(self.updates.bundle())
        self.assertFalse(any(self.updates.folder.glob('*.tar.gz')))
        self.answer({'.json': httpx.Response(200, json=META), '.tar.gz': httpx.Response(200, content=BUNDLE)})
        self.assertIsNone(self.updates.bundle(), 'not tried again straight away')

    def test_a_developers_hub_offers_nothing(self):
        self.updates.version = '0.3.0-dev'
        self.release_files()
        self.assertIsNone(self.updates.bundle())

    def test_after_restarting_an_update_worked_or_didnt(self):
        self.updates._save(applying={'version': '0.3.0', 'from': '0.2.0', 'at': time.time()})
        self.updates.started()
        self.assertEqual(self.updates.info()['updated']['from'], '0.2.0')
        self.assertIsNone(self.updates.info()['applying'])

        self.updates._save(applying={'version': '0.4.0', 'from': '0.3.0', 'at': time.time() - 3600})
        self.updates.started()
        self.assertIn("didn't finish", self.updates.info()['error'])

    def test_docker_says_how_to_update_by_hand(self):
        self.updates.kind = 'docker'
        self.updates._save(latest='0.4.0')
        with self.assertRaises(updates.UpdateError) as e:
            self.updates.request()
        self.assertIn('Redeploy', str(e.exception))

    def test_update_now_waits_while_something_is_live(self):
        self.updates.kind = 'macos'
        self.updates._save(latest='0.4.0')
        self.updates.busy = lambda: 'Sunday Service is live'
        with mock.patch.object(self.updates, 'apply') as apply:
            info = self.updates.request()
        apply.assert_not_called()
        self.assertTrue(info['requested'])
        self.assertEqual(info['waiting_for'], 'Sunday Service is live')

    def test_the_mac_update_runs_as_a_launchd_job_of_its_own(self):
        self.updates.kind = 'macos'
        self.updates._save(latest='0.4.0')
        with mock.patch('subprocess.run') as run, mock.patch('os.getuid', return_value=501, create=True):
            self.updates.apply()
        plist = (self.updates.folder / f'{updates.JOB}.plist').read_text()
        self.assertIn('v0.4.0/hub/install/macos.sh', plist)
        self.assertIn('archive/refs/tags/v0.4.0.zip#subdirectory=hub', plist)
        self.assertEqual(run.call_args_list[-1].args[0][:2], ['launchctl', 'bootstrap'])
        self.assertEqual(self.updates.info()['applying']['version'], '0.4.0')


if __name__ == '__main__':
    unittest.main()
