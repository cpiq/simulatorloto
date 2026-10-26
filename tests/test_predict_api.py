from copy import deepcopy
from datetime import date, timedelta
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import requests

import server
from perplexitymix import generate649


class PredictAPITests(unittest.TestCase):
    def setUp(self):
        self.draws = [{'date': (date(2026, 9, 1) + timedelta(days=i)).isoformat(),
                       'numbers': [1, 2, 3, 4, 5, 6]} for i in range(30)]
        self.client = server.app.test_client()

    def response(self, query='', draws=None):
        with patch.object(server, 'get_draws', return_value=(draws or self.draws, {}, False)):
            return self.client.get('/api/predict' + query)

    def test_default_api_uses_v2_shared_engine_and_six_plus_one(self):
        response = self.response()
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        expected = generate649({'cycle_date': self.draws[-1]['date']})
        self.assertEqual(payload['engine'], '649-coverage-v2')
        self.assertEqual(payload['tickets6'], expected['tickets'])
        self.assertEqual(payload['tickets9'], expected['extensions'])
        self.assertEqual(payload['coverage']['distinct_numbers'], 36)
        self.assertEqual(payload['params']['n'], 6)
        self.assertEqual(payload['params']['extra9'], 1)
        self.assertEqual(payload['model']['frequencyInfluence'], 0)

    def test_history_corrections_do_not_change_current_tickets(self):
        first = self.response().get_json()
        corrected = deepcopy(self.draws)
        corrected[0]['numbers'] = [7, 8, 9, 10, 11, 12]
        second = self.response(draws=corrected).get_json()
        self.assertNotEqual(first['data']['history_hash'], second['data']['history_hash'])
        self.assertEqual(first['tickets6'], second['tickets6'])
        self.assertEqual(first['tickets9'], second['tickets9'])

    def test_strict_parameters_large_packages_and_no_payment_routes(self):
        for query in ['?n=0', '?n=51', '?n=2.5', '?seed=-1', '?seed=4294967296', '?extra9=-1', '?extra9=11']:
            with self.subTest(query=query):
                self.assertEqual(self.response(query).status_code, 422)
        payload = self.response('?n=50&seed=0&extra9=10').get_json()
        self.assertEqual(len(payload['tickets6']), 50)
        self.assertTrue(all(len(numbers) == 16 for numbers in payload['tickets9']))
        for path in ['/api/config', '/api/price', '/api/create-checkout', '/api/verify']:
            self.assertEqual(self.client.get(path).status_code, 404)

    def test_invalid_history_is_rejected_and_other_routes_remain_registered(self):
        bad = deepcopy(self.draws)
        bad[0]['joker'] = 9
        self.assertEqual(self.response(draws=bad).status_code, 502)
        routes = {rule.rule for rule in server.app.url_map.iter_rules()}
        self.assertTrue({'/api/predict540', '/api/predictjoker', '/api/football/analyze'} <= routes)

    def test_complete_archive_download_stops_on_missing_or_redirected_source(self):
        fixtures = json.loads((Path(__file__).parent / 'fixtures/loto-archive-layouts.json').read_text())
        by_path = {f['source']: f['html'] for f in fixtures if f['game'] == '649'}
        rows = ''.join('<tr><td>' + d['date'] + '</td>' + ''.join(f'<td>{n}</td>' for n in d['numbers']) + '</tr>' for d in self.draws)
        current = '<h2>Loto 6/49</h2><table><tr><th>Data tragerii-&gt;</th>' + ''.join(f'<th>Nr.{n}</th>' for n in range(1, 7)) + '</tr>' + rows + '</table>'
        calls = []
        class FakeSession:
            def __enter__(self): return self
            def __exit__(self, *_args): pass
            def get(self, url, **_kwargs):
                calls.append(url)
                plain = url.split('?')[0]
                response = requests.Response()
                response.status_code = 200
                response.url = url
                response._content = (current if plain.endswith('/arhiva-loto49.php') else by_path[plain]).encode()
                return response
        with patch.object(server, 'build_session', return_value=FakeSession()):
            draws, meta = server.download_all()
        self.assertEqual(len(calls), 7)
        self.assertGreaterEqual(len(draws), 30)
        self.assertEqual(meta['last_date'], self.draws[-1]['date'])
        with patch.object(server, 'build_session', return_value=FakeSession()), patch.object(server, 'extract_draws_from_html', return_value=[]):
            with self.assertRaises(server.LotoDataExtractionError):
                server.download_all()


if __name__ == '__main__':
    unittest.main()
