import json
from pathlib import Path
import unittest

from loto_archive_parser import (
    ARCHIVE_URLS_649, LotoDataExtractionError, archive_request_url,
    extract_draws_from_html, normalize_draws,
)

FIXTURES = json.loads((Path(__file__).parent / "fixtures/loto-archive-layouts.json").read_text())
HEADER = '<h2>Loto 6/49</h2><table><tr><th>Data tragerii-&gt;</th>' + ''.join(f'<th>Nr.{n}</th>' for n in range(1, 7)) + '</tr>'


def row(day, numbers):
    return '<tr><td>' + day + '</td>' + ''.join(f'<td>{n}</td>' for n in numbers) + '</tr>'


class ArchiveParserTests(unittest.TestCase):
    def test_all_real_source_layouts_and_cross_game_rejection(self):
        for fixture in FIXTURES:
            with self.subTest(source=fixture['source']):
                result = extract_draws_from_html(fixture['html'], fixture['source'])
                self.assertEqual(result, fixture['expected'] if fixture['game'] == '649' else [])

    def test_mixed_games_and_redirected_sources(self):
        fixtures = [next(f for f in FIXTURES if f['game'] == game) for game in ['649', '540', 'joker']]
        html = ''.join(f['html'] for f in fixtures)
        self.assertEqual(extract_draws_from_html(html, fixtures[0]['source']), fixtures[0]['expected'])
        self.assertEqual(extract_draws_from_html(html, 'https://elsewhere.example/arhiva-loto49.php'), [])
        self.assertEqual(extract_draws_from_html(html, fixtures[2]['source']), [])

    def test_unpadded_dates_missing_rows_and_supplementary_draws(self):
        html = HEADER + (row('2026-10-1', [1, 2, 3, 4, 5, 6]) +
                         row('2026-10-1', [1, 2, 3, 4, 5, 7]) +
                         row('2026-10-4', [7, 8, 9, 10, 11, 12])).replace('<tr>', '').replace('</tr>', '') + '</table>'
        result = extract_draws_from_html(html)
        self.assertEqual([d['date'] for d in result], ['2026-10-01', '2026-10-01', '2026-10-04'])
        self.assertEqual(len(normalize_draws(result + result)), 3)

    def test_invalid_rows_are_not_repaired_with_other_rows_or_joker(self):
        good = row('2026-10-11', [13, 14, 23, 33, 34, 39])
        bad = [row('2026-10-11', [1, 2, 3, 4, 5]), row('2026-10-11', [6]),
               row('2026-10-11', [1, 2, 3, 4, 5, 6, 7]), row('2026-02-30', [1, 2, 3, 4, 5, 6]),
               row('2026-10-11', [1, 2, 3, 4, 5, 50]), row('2026-10-11', [1, 2, 3, 4, 5, 5]),
               row('2026-10-11', [1, 2, 3, 4, 5, '<span>+ 2</span>']),
               row('2026-10-11', [1, 2, 3, 4, 5, 6]).replace('<tr>', '<tr data-game="joker">')]
        self.assertEqual(extract_draws_from_html(HEADER + ''.join(bad) + good + '</table>'),
                         [{'date': '2026-10-11', 'numbers': [13, 14, 23, 33, 34, 39]}])
        self.assertEqual(extract_draws_from_html('<table>' + good + '</table>'), [])

    def test_comment_and_script_rows_do_not_create_results(self):
        fake = HEADER + row('2026-10-11', [1, 2, 3, 4, 5, 6]) + '</table>'
        self.assertEqual(extract_draws_from_html('<!--' + fake + '--><script>' + fake + '</script>'), [])

    def test_normalization_rejects_other_games_and_refreshes_only_current_archive(self):
        for marker in [{'joker': 9}, {'game': 'joker'}, {'game': '540'}]:
            with self.assertRaises(LotoDataExtractionError):
                normalize_draws([{'date': '2026-10-11', 'numbers': [1, 2, 3, 4, 5, 6], **marker}])
        for url in ARCHIVE_URLS_649:
            refreshed = archive_request_url(url, 123)
            self.assertEqual('_history_refresh=123' in refreshed, url.endswith('/arhiva-loto49.php'))


if __name__ == '__main__':
    unittest.main()
