import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import alternatives
import examples
import server

ENERGY_DRINK = '18352'


def gemini_reply(answer):
    body = {'candidates': [{'content': {'parts': [{'text': json.dumps(answer)}]}}]}
    return io.BytesIO(json.dumps(body).encode())


class AlternativesTests(unittest.TestCase):
    def setUp(self):
        # Never read or write the real precomputed store in tests.
        self.store = {'schema_version': 1, 'model': 'test', 'picks': {}}
        for target, value in [('STORE', self.store), ('_save_store', lambda: None),
                              ('_merge_from_disk', lambda: None), ('gemini_key', lambda: 'test-key')]:
            patcher = patch.object(alternatives, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(examples, 'examples_for', lambda code: [])
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_candidates_are_same_subgroup_and_meaningfully_lower(self):
        food = alternatives.FOODS[ENERGY_DRINK]
        rows = alternatives.candidates(ENERGY_DRINK)
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(row['subgroup'], food['subgroup'])
            self.assertLessEqual(row['kg_co2e_per_kg'], food['kg_co2e_per_kg'] * .8)
        self.assertEqual(rows, sorted(rows, key=lambda row: (row['kg_co2e_per_kg'], row['code'])))

    def test_invented_duplicate_and_malformed_picks_are_dropped(self):
        rows = alternatives.candidates(ENERGY_DRINK)
        good = rows[0]['code']
        answer = {'alternatives': [
            {'code': good, 'fit': 'same_use', 'reason': 'Similar fizzy drink.'},
            {'code': good, 'fit': 'same_use', 'reason': 'Duplicate.'},
            {'code': '99999', 'fit': 'same_use', 'reason': 'Invented code.'},
            {'code': ENERGY_DRINK, 'fit': 'same_use', 'reason': 'Itself.'},
            {'code': rows[1]['code'], 'fit': 'better', 'reason': 'Bad enum.'},
            {'code': rows[2]['code'], 'fit': 'similar_use', 'reason': '   '},
            'not an object']}
        self.assertEqual([pick['code'] for pick in alternatives.validate_picks(answer, rows)], [good])
        self.assertEqual(alternatives.validate_picks(['nonsense'], rows), [])

    def test_savings_are_computed_from_the_database_not_the_model(self):
        pick = alternatives.candidates(ENERGY_DRINK)[0]
        reply = gemini_reply({'alternatives': [{'code': pick['code'], 'fit': 'same_use', 'reason': 'Also fizzy.'}]})
        with patch('alternatives.urlopen', return_value=reply) as fetch:
            result = alternatives.lookup_alternatives(ENERGY_DRINK, .25)
        self.assertEqual((result['status'], result['origin']), ('ok', 'live'))
        item = result['alternatives'][0]
        saved = alternatives.FOODS[ENERGY_DRINK]['kg_co2e_per_kg'] - pick['kg_co2e_per_kg']
        self.assertAlmostEqual(item['kg_co2e_saved_per_package'], round(saved * .25, 6))
        self.assertEqual(item['kg_co2e_per_kg'], pick['kg_co2e_per_kg'])
        self.assertEqual(fetch.call_args.args[0].get_header('X-goog-api-key'), 'test-key')
        # A second lookup for the same food reuses the stored picks.
        with patch('alternatives.urlopen') as fetch:
            self.assertEqual(alternatives.lookup_alternatives(ENERGY_DRINK)['origin'], 'precomputed')
            fetch.assert_not_called()

    def test_lowest_food_skips_the_model(self):
        tea = '18020'
        self.assertEqual(alternatives.candidates(tea), [])
        with patch('alternatives.urlopen') as fetch:
            result = alternatives.lookup_alternatives(tea, .5)
            fetch.assert_not_called()
        self.assertEqual((result['status'], result['reason']), ('none_found', 'no_lower_candidates'))

    def test_failures_are_reported_not_invented(self):
        for failure, reason in [(HTTPError('https://example.com', 429, '', {}, None), 'gemini_rate_limited'),
                                (io.BytesIO(b'not json'), 'gemini_invalid_response')]:
            with self.subTest(reason=reason):
                kwargs = {'side_effect': failure} if isinstance(failure, Exception) else {'return_value': failure}
                with patch('alternatives.urlopen', **kwargs):
                    result = alternatives.lookup_alternatives(ENERGY_DRINK, .25)
                self.assertEqual((result['status'], result['reason'], result['alternatives']),
                                 ('unavailable', reason, []))
                self.assertNotIn(ENERGY_DRINK, self.store['picks'])
        with patch.object(alternatives, 'gemini_key', lambda: ''):
            self.assertEqual(alternatives.lookup_alternatives(ENERGY_DRINK)['reason'], 'gemini_missing_api_key')

    def test_unknown_code_and_bad_mass(self):
        self.assertEqual(alternatives.lookup_alternatives('999999')['reason'], 'unknown_agribalyse_code')
        self.store['picks'][ENERGY_DRINK] = []
        for mass in [float('nan'), -1.0, 5000.0]:
            self.assertIsNone(alternatives.lookup_alternatives(ENERGY_DRINK, mass)['mass_kg'])

    def test_batch_rechecks_each_target_against_its_own_candidates(self):
        targets = [ENERGY_DRINK, '18020']
        allowed = alternatives.candidates(ENERGY_DRINK)[0]['code']
        reply = gemini_reply({'foods': [
            {'code': ENERGY_DRINK, 'alternatives': [{'code': allowed, 'fit': 'same_use', 'reason': 'Fizzy.'}]},
            # Tea has no lower-carbon candidates, so any pick for it must be dropped.
            {'code': '18020', 'alternatives': [{'code': allowed, 'fit': 'same_use', 'reason': 'Wrong.'}]},
            {'code': '12345', 'alternatives': []}]})
        with patch('alternatives.urlopen', return_value=reply):
            stored = alternatives.precompute_batch(targets)
        self.assertEqual(sorted(stored), sorted(targets))
        self.assertEqual([pick['code'] for pick in self.store['picks'][ENERGY_DRINK]], [allowed])
        self.assertEqual(self.store['picks']['18020'], [])
        self.assertNotIn('12345', self.store['picks'])

    def test_endpoint_validation_and_response(self):
        handler = object.__new__(server.Handler)
        handler.headers = {}
        self.store['picks'][ENERGY_DRINK] = []
        for path, status in [('/api/alternatives?code=abc', 400), ('/api/alternatives?code=18352&mass_kg=0.25', 200),
                             ('/api/alternatives?code=18352&mass_kg=oops', 200)]:
            with self.subTest(path=path), patch.object(handler, 'send_body') as send:
                handler.path = path
                handler.do_GET()
                self.assertEqual(send.call_args.args[0], status)
                if status == 200:
                    self.assertEqual(json.loads(send.call_args.args[1])['scanned']['code'], ENERGY_DRINK)


class ExampleProductTests(unittest.TestCase):
    def hit(self, **updates):
        hit = {'code': '01201303', 'product_name': 'Pepsi', 'brands': ['Pepsi', 'PepsiCo'],
               'quantity': '12 oz (355 ml)', 'categories_tags': ['en:beverages', 'en:colas', 'en:cola-with-sugar'],
               'image_front_small_url': 'https://images.openfoodfacts.org/images/products/pepsi.jpg'}
        hit.update(updates)
        return hit

    def test_tags_and_resolution(self):
        self.assertEqual(examples.tags_for('18018')[0], 'en:cola-with-sugar')
        self.assertTrue(examples.resolves_to(['en:colas', 'en:cola-with-sugar'], '18018'))
        # A proxy category alone counts; a more specific food elsewhere does not.
        self.assertTrue(examples.resolves_to(['en:colas'], '18018'))
        self.assertFalse(examples.resolves_to(['en:colas', 'en:plain-tofu'], '18018'))
        self.assertFalse(examples.resolves_to(['en:energy-drinks'], '18018'))

    def test_pick_examples_filters_and_cleans(self):
        hits = [self.hit(), self.hit(code='1'),  # duplicate name + brand
                self.hit(code='2', product_name='Plain tofu', categories_tags=['en:plain-tofu']),
                self.hit(code='3', brands=None), self.hit(code='x'), 'junk',
                self.hit(code='4', product_name='Dr Pepper', brands='Dr Pepper, Keurig',
                         image_front_small_url='https://evil.example.com/a.jpg')]
        picked = examples.pick_examples(hits, '18018')
        self.assertEqual([item['barcode'] for item in picked], ['01201303', '4'])
        self.assertEqual((picked[0]['brand'], picked[1]['brand']), ('Pepsi', 'Dr Pepper'))
        self.assertTrue(picked[0]['image'].startswith('https://images.openfoodfacts.org/'))
        self.assertEqual(picked[1]['image'], '')
        self.assertEqual(picked[0]['url'], 'https://world.openfoodfacts.org/product/01201303')

    def test_search_failure_keeps_category_swap(self):
        item = {'code': '18018'}
        with patch.object(examples, 'examples_for', side_effect=examples.SearchError('provider_error')):
            alternatives.attach_examples([item])
        self.assertEqual(item['examples'], [])


if __name__ == '__main__':
    unittest.main()
