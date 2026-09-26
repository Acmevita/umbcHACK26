import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import server


class LookupTests(unittest.TestCase):
    def setUp(self):
        server.CACHE.clear()
        self.key = patch('server.api_key', return_value='test-secret')
        self.key.start()
        self.addCleanup(self.key.stop)

    def response(self, products):
        return io.BytesIO(json.dumps({'products': products}).encode())

    def test_matching_and_equivalent_barcode_cache(self):
        with patch('server.urlopen', return_value=self.response([
            {'barcode_number': '082657500638', 'title': 'Fixture product', 'brand': 'Test',
             'images': ['javascript:alert(1)', 'https://example.com/product.png']}
        ])) as fetch:
            product = server.lookup_product('082657500638')
            self.assertEqual(product['title'], 'Fixture product')
            self.assertEqual(product['image'], 'https://example.com/product.png')
            self.assertNotIn('test-secret', json.dumps(product))
            server.lookup_product('0082657500638')
            self.assertEqual(fetch.call_count, 1)
            self.assertIn('barcode=082657500638', fetch.call_args.args[0].full_url)

    def test_missing_key_and_invalid_barcode_do_not_call_provider(self):
        with patch('server.urlopen') as fetch:
            with self.assertRaises(server.LookupError) as error:
                server.lookup_product('082657500639')
            self.assertEqual(error.exception.status, 400)
            with patch('server.api_key', return_value=''):
                with self.assertRaises(server.LookupError) as error:
                    server.lookup_product('082657500638')
                self.assertEqual(error.exception.status, 503)
            fetch.assert_not_called()

    def test_provider_errors_hide_credentials(self):
        for upstream, expected in [(401, 502), (403, 502), (404, 404), (429, 429), (500, 502)]:
            with self.subTest(upstream=upstream), patch('server.urlopen', side_effect=HTTPError(
                    'https://example.com/?key=test-secret', upstream, 'test-secret', {}, None)):
                with self.assertRaises(server.LookupError) as error:
                    server.lookup_product('082657500638')
                self.assertEqual(error.exception.status, expected)
                self.assertNotIn('test-secret', error.exception.message)

    def test_empty_mismatched_and_malformed_responses(self):
        for body, expected in [(b'{"products":[]}', 404),
                               (b'{"products":[{"barcode_number":"012345678905"}]}', 502),
                               (b'{}', 502), (b'not json', 502)]:
            with self.subTest(body=body), patch('server.urlopen', return_value=io.BytesIO(body)):
                with self.assertRaises(server.LookupError) as error:
                    server.lookup_product('082657500638')
                self.assertEqual(error.exception.status, expected)

    def test_network_error(self):
        with patch('server.urlopen', side_effect=URLError('test-secret')):
            with self.assertRaises(server.LookupError) as error:
                server.lookup_product('082657500638')
            self.assertEqual(error.exception.status, 502)
            self.assertNotIn('test-secret', error.exception.message)

    def request(self, path, headers=None):
        handler = object.__new__(server.Handler)
        handler.path = path
        handler.headers = headers or {}
        with patch.object(handler, 'send_body') as send:
            handler.do_GET()
            return send.call_args.args

    def test_secret_files_are_not_served(self):
        for path in ['/.env', '/.env.example', '/.git/config', '/server.py', '/../.env']:
            self.assertEqual(self.request(path)[0], 404)
        self.assertEqual(self.request('/')[0], 200)

    def test_endpoint_and_cross_origin(self):
        with patch('server.lookup_product', return_value={'title': 'Fixture'}) as lookup:
            code, body, _ = self.request('/api/products?barcode=082657500638')
            self.assertEqual(code, 200)
            self.assertEqual(json.loads(body)['product']['title'], 'Fixture')
            lookup.assert_called_once_with('082657500638')
        with patch('server.lookup_product') as lookup:
            self.assertEqual(self.request('/api/products?barcode=082657500638',
                             {'Origin': 'https://other.example', 'Host': 'localhost:8001'})[0], 403)
            lookup.assert_not_called()


if __name__ == '__main__':
    unittest.main()
