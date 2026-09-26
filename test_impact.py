from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
import io
import json
import threading
import time
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

import impact
import open_food_facts as off
from quantity import parse_size
import server


class QuantityTests(unittest.TestCase):
    def test_mass_units_and_multipacks(self):
        for label, mass in [('500 g', .5), ('16 oz', .45359237), ('1.5 lb', .680388555),
                            ('2 x 250g', .5), ('NET WT 16 OZ (454 g)', .45359237),
                            ('0.5 kg', .5), ('6 × 25 g', .15)]:
            with self.subTest(label=label):
                result = parse_size(label)
                self.assertEqual(result['status'], 'known_mass')
                self.assertAlmostEqual(result['mass_kg'], mass)

    def test_fluid_ounces_never_become_mass(self):
        result = parse_size('12 x 12 fl oz')
        self.assertEqual(result['status'], 'known_volume')
        self.assertEqual(result['pack_count'], 12)
        self.assertIsNone(result['mass_kg'])
        self.assertAlmostEqual(result['volume_liters'], 4.258588257)
        self.assertTrue(result['assumptions'])
        self.assertEqual(parse_size('1.5 L')['volume_liters'], 1.5)

    def test_missing_count_only_or_conflicting_quantity(self):
        for label in ['', None, '12 eggs', '12 pack', '500 g (1 lb)', '500 g / 1 kg',
                      '12 x 12 oz (4 kg)', '1,000 g', '-5 g', '0 g', '0 x 20g',
                      '500 g 6 pack', 'one bag', '2 cups', '100 ml (100 g)']:
            with self.subTest(label=label):
                result = parse_size(label)
                self.assertEqual(result['status'], 'unknown')
                self.assertIsNone(result['mass_kg'])


class AssessmentTests(unittest.TestCase):
    def test_catalog_has_all_verified_rows(self):
        self.assertEqual(len(impact.FACTORS), 38)
        self.assertEqual(impact.FACTORS['tofu']['kg_co2e_per_kg'], 3.16)
        self.assertEqual(impact.FACTORS['rice']['kg_co2e_per_kg'], 4.45)
        self.assertTrue(all(row['producer_range'] is None for row in impact.FACTORS.values()))

    def test_hand_calculation_and_provenance(self):
        result = impact.assess_product({'title': 'Plain tofu', 'category_tags': ['en:tofu'], 'quantity': '500g'})
        self.assertEqual(result['status'], 'estimated')
        self.assertEqual(result['per_package_kg_co2e'], 1.58)
        self.assertEqual(result['calculation']['mass_kg'], .5)
        self.assertIn('not US-specific', result['geography'])
        self.assertEqual(result['source']['boundary'], 'cradle_to_retail')
        self.assertIsNone(result['producer_range'])

    def test_unknown_and_processed_food_abstain(self):
        cases = [('Potato chips', ['en:potatoes', 'en:potato-chips']),
                 ('Tomato sauce', ['en:tomatoes', 'en:sauces']),
                 ('Beef', ['en:beef']), ('Almond milk', ['en:plant-milks']),
                 ('Water', ['en:waters']), ('Cooked rice', ['en:rices']),
                 ('Apple juice', ['en:apples']), ('Vegan cheeses', ['en:cheeses'])]
        for title, tags in cases:
            with self.subTest(title=title):
                result = impact.assess_product({'title': title, 'category_tags': tags, 'quantity': '500g'})
                self.assertEqual(result['status'], 'insufficient_data')
                self.assertIsNone(result['per_package_kg_co2e'])

    def test_conflicting_tags_abstain(self):
        result = impact.assess_product({'title': 'Food mix', 'category_tags': ['en:tofu', 'en:rices'], 'quantity': '500g'})
        self.assertEqual(result['reason'], 'conflicting_categories')

    def test_volume_or_count_only_returns_category_not_package(self):
        for tags, quantity in [(['en:cow-milks'], '1 L'), (['en:eggs'], '12 eggs')]:
            result = impact.assess_product({'category_tags': tags, 'quantity': quantity})
            self.assertEqual(result['status'], 'category_only')
            self.assertIsNone(result['per_package_kg_co2e'])
            self.assertIsNotNone(result['per_kg_food_kg_co2e'])


class AgribalyseTests(unittest.TestCase):
    def test_catalog_has_reviewed_rows(self):
        self.assertEqual(len(impact.FOODS), 2451)
        self.assertEqual(impact.FOODS['18352']['kg_co2e_per_kg'], .685)
        self.assertTrue(impact.FOODS['18352']['liquid'])
        self.assertFalse(impact.FOODS['20904']['liquid'])

    def test_drink_proxy_uses_volume_with_stated_density(self):
        result = impact.assess_product({'title': 'Energy drink', 'quantity': '250 mL (8.4 FL OZ)',
                                        'agribalyse': {'code': '18352', 'match': 'proxy', 'stages_kg_co2e_per_kg': None}})
        self.assertEqual(result['status'], 'estimated')
        self.assertAlmostEqual(result['per_package_kg_co2e'], .17125)
        self.assertEqual(result['classification']['method'], 'off_agribalyse_proxy')
        self.assertIn('Drink density assumed to be 1 kg per litre.', result['assumptions'])
        self.assertEqual(result['source']['id'], 'agribalyse_3_2')

    def test_takes_precedence_over_blocked_processed_tags(self):
        result = impact.assess_product({'title': 'Tortilla chips', 'quantity': '1 oz (28.3 g)',
                                        'category_tags': ['en:potatoes', 'en:chips'],
                                        'agribalyse': {'code': '38105', 'match': 'exact', 'stages_kg_co2e_per_kg': None}})
        self.assertEqual(result['status'], 'estimated')
        self.assertAlmostEqual(result['per_package_kg_co2e'], .028349523125 * 2.33, places=5)

    def test_volume_of_solid_food_is_not_converted(self):
        result = impact.assess_product({'quantity': '1 L', 'agribalyse': {'code': '38105', 'match': 'exact', 'stages_kg_co2e_per_kg': None}})
        self.assertEqual(result['status'], 'category_only')
        self.assertIsNone(result['per_package_kg_co2e'])

    def test_stages_only_when_consistent_with_catalog(self):
        stages = {'agriculture': .0864, 'processing': .146, 'packaging': .482, 'transportation': .23,
                  'distribution': .0418, 'consumption': .0181}
        product = {'quantity': '500 g', 'agribalyse': {'code': '20904', 'match': 'exact', 'stages_kg_co2e_per_kg': stages}}
        result = impact.assess_product(product)
        self.assertAlmostEqual(result['stages']['per_package']['packaging'], .241)
        product['agribalyse']['stages_kg_co2e_per_kg'] = dict(stages, packaging=5)
        self.assertIsNone(impact.assess_product(product)['stages'])

    def test_unknown_code_and_normalized_quantity_fallback(self):
        self.assertIsNone(impact.classify_agribalyse({'agribalyse': {'code': '999999', 'match': 'exact'}}))
        result = impact.assess_product({'quantity': 'one bag', 'net_quantity': {'value': 510, 'unit': 'g'},
                                        'agribalyse': {'code': '32135', 'match': 'exact', 'stages_kg_co2e_per_kg': None}})
        self.assertAlmostEqual(result['per_package_kg_co2e'], 1.734)
        self.assertEqual(result['package_quantity']['raw'], 'one bag')

    def test_off_reference_extraction(self):
        self.assertEqual(off._agribalyse_reference({'categories_properties': {'agribalyse_proxy_food_code:en': '18352'}}),
                         {'code': '18352', 'match': 'proxy', 'stages_kg_co2e_per_kg': None, 'off_version': None})
        exact = off._agribalyse_reference({'categories_properties': {'agribalyse_food_code:en': '20904'},
                                           'ecoscore_data': {'agribalyse': {'code': '20904', 'version': '3.2', **{
                                               'co2_' + stage: .1 for stage in off.STAGES}}}})
        self.assertEqual((exact['match'], exact['off_version']), ('exact', '3.2'))
        self.assertEqual(len(exact['stages_kg_co2e_per_kg']), 6)
        for bad in [{}, {'categories_properties': {'agribalyse_food_code:en': 'abc'}}, {'ecoscore_data': []}]:
            self.assertIsNone(off._agribalyse_reference(bad))
        self.assertEqual(off._net_quantity({'product_quantity': '250', 'product_quantity_unit': 'ml'}), {'value': 250, 'unit': 'ml'})
        self.assertIsNone(off._net_quantity({'product_quantity': 'nan', 'product_quantity_unit': 'g'}))


class OpenFoodFactsTests(unittest.TestCase):
    def setUp(self):
        with off.LOCK:
            off.CACHE.clear()
            off.INFLIGHT.clear()
            off.REQUEST_TIMES.clear()

    def product_response(self, **updates):
        product = {'code': '082657500638', 'product_name': 'Water', 'quantity': '500 ml',
                   'categories_tags': ['en:waters'], 'countries_tags': ['en:united-states']}
        product.update(updates)
        return io.BytesIO(json.dumps({'status': 1, 'product': product}).encode())

    def test_filtered_request_attribution_and_equivalent_cache(self):
        with patch('open_food_facts.urlopen', return_value=self.product_response()) as fetch:
            result = off.lookup('082657500638')
            self.assertEqual(result['status'], 'ok')
            self.assertEqual(result['attribution']['license'], 'ODbL-1.0')
            request = fetch.call_args.args[0]
            self.assertIn('fields=', request.full_url)
            self.assertIn('BetterBasket/', request.get_header('User-agent'))
            cached = off.lookup('0082657500638')
            self.assertTrue(cached['cache_hit'])
            self.assertEqual(fetch.call_count, 1)
            cached['product']['title'] = 'mutation'
            self.assertEqual(off.lookup('082657500638')['product']['title'], 'Water')

    def test_not_found_and_network_failures(self):
        for failure, status, reason in [
            (HTTPError('https://example.com', 404, '', {}, None), 'not_found', 'not_found'),
            (HTTPError('https://example.com', 429, '', {}, None), 'unavailable', 'rate_limited'),
            (URLError('private internal detail'), 'unavailable', 'network_or_timeout'),
        ]:
            with self.subTest(reason=reason), patch('open_food_facts.urlopen', side_effect=failure):
                off.CACHE.clear()
                result = off.lookup('082657500638')
                self.assertEqual((result['status'], result['reason']), (status, reason))
                self.assertNotIn('private internal', json.dumps(result))

    def test_bad_response_and_wrong_barcode(self):
        for body, reason in [(b'not json', 'invalid_response'), (b'[]', 'invalid_response'),
                             (b'{"status":0}', 'not_found')]:
            with self.subTest(body=body), patch('open_food_facts.urlopen', return_value=io.BytesIO(body)):
                off.CACHE.clear()
                self.assertEqual(off.lookup('082657500638')['reason'], reason)
        off.CACHE.clear()
        with patch('open_food_facts.urlopen', return_value=self.product_response(code='012345678905')):
            self.assertEqual(off.lookup('082657500638')['reason'], 'barcode_mismatch')

    def test_local_rate_limit_does_not_wait(self):
        off.REQUEST_TIMES.extend([time.monotonic()] * 15)
        with patch('open_food_facts.urlopen') as fetch:
            self.assertEqual(off.lookup('082657500638')['reason'], 'local_rate_limit')
            fetch.assert_not_called()

    def test_duplicate_inflight_queries_are_coalesced(self):
        entered, release = threading.Event(), threading.Event()
        def delayed_fetch(_):
            entered.set()
            release.wait(2)
            return {'status': 'ok', 'product': {'title': 'fixture'}}
        with patch('open_food_facts._fetch', side_effect=delayed_fetch) as fetch, ThreadPoolExecutor(2) as pool:
            first = pool.submit(off.lookup, '082657500638')
            self.assertTrue(entered.wait(1))
            second = pool.submit(off.lookup, '0082657500638')
            release.set()
            self.assertEqual(first.result()['status'], 'ok')
            self.assertEqual(second.result()['status'], 'ok')
            self.assertEqual(fetch.call_count, 1)

    def test_full_pipeline_with_synthetic_product_and_real_factor(self):
        with patch('open_food_facts.urlopen', return_value=self.product_response(
                product_name='Fixture plain tofu', quantity='2 x 250g', categories_tags=['en:tofu'])):
            result = impact.lookup_impact('082657500638')
            self.assertEqual(result['impact']['per_package_kg_co2e'], 1.58)
            self.assertIn('ODbL', result['open_food_facts']['attribution']['license'])

    def test_endpoint_and_deadline(self):
        handler = object.__new__(server.Handler)
        handler.path = '/api/impact?barcode=082657500638'
        handler.headers = {}
        with patch('open_food_facts.urlopen', return_value=self.product_response()), patch.object(handler, 'send_body') as send:
            handler.do_GET()
            code, body, _ = send.call_args.args
            self.assertEqual(code, 200)
            result = json.loads(body)
            self.assertEqual(result['impact']['status'], 'insufficient_data')
            self.assertLess(result['processing_ms'], 15000)
        job = Mock()
        job.result.side_effect = FutureTimeout()
        with patch.object(server.IMPACT_POOL, 'submit', return_value=job), patch.object(handler, 'send_body') as send:
            handler.do_GET()
            self.assertEqual(send.call_args.args[0], 504)
            job.cancel.assert_called_once()


if __name__ == '__main__':
    unittest.main()
