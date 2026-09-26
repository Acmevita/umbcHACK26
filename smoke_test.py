"""Exercise the backend over real local HTTP; provider is simulated unless --live."""
import argparse
from contextlib import ExitStack
import io
import json
import os
import sys
import threading
from urllib.error import HTTPError
from urllib.request import urlopen
from unittest.mock import patch

import server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Use configured API key for one real provider lookup.')
    args = parser.parse_args()
    print('Virtual environment: ' + ('active' if sys.prefix != sys.base_prefix else 'not active'))
    print('Provider mode: ' + ('LIVE' if args.live else 'SIMULATED (no credits used)'))
    if args.live:
        if not server.api_key() or server.api_key() == 'your_api_key_here':
            print('FAIL: Add BARCODE_LOOKUP_API_KEY to .env first. No request sent.')
            return 1
        print('Key source: ' + ('environment variable' if os.environ.get('BARCODE_LOOKUP_API_KEY', '').strip() else '.env file (no terminal injection needed)'))

    server.CACHE.clear()
    with ExitStack() as stack:
        if not args.live:
            stack.enter_context(patch('server.api_key', return_value='fixture-only-key'))
            provider = stack.enter_context(patch('server.urlopen', return_value=io.BytesIO(json.dumps({
                'products': [{'barcode_number': '082657500638', 'title': 'Simulated grocery item',
                              'brand': 'Test fixture', 'description': 'Not real product data.'}]
            }).encode())))
        httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base = 'http://127.0.0.1:' + str(httpd.server_address[1])

        def get(path):
            try:
                with urlopen(base + path, timeout=20) as response:
                    return response.status, response.read()
            except HTTPError as error:
                return error.code, error.read()

        try:
            status, body = get('/api/products?barcode=082657500638')
            data = json.loads(body)
            if status != 200:
                # This message is sanitized by our backend; never print provider URLs or keys.
                print('FAIL: HTTP ' + str(status) + ': ' + data.get('error', 'Lookup failed.'))
                return 1
            product = data['product']
            assert product['barcode'] == '082657500638' and product['title']
            assert server.api_key().encode() not in body, 'Credential must never be in JSON'
            print('PASS: HTTP 200; product = ' + product['title'])
            print('Brand: ' + (product['brand'] or '(not supplied)'))
            # Prove the second HTTP request cannot call the provider again.
            with patch('server.urlopen', side_effect=AssertionError('Duplicate provider request')):
                cached_status, cached_body = get('/api/products?barcode=0082657500638')
            assert cached_status == 200 and json.loads(cached_body)['product']['title'] == product['title']
            print('PASS: equivalent UPC/EAN lookup uses the cache')
            assert get('/api/products?barcode=082657500639')[0] == 400
            print('PASS: invalid barcode rejected with HTTP 400')
            assert get('/.env')[0] == 404
            assert get('/.git/config')[0] == 404
            print('PASS: .env and .git are not served')
            if not args.live:
                assert provider.call_count == 1
            print('PASS: backend HTTP smoke test complete')
            return 0
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join()


if __name__ == '__main__':
    raise SystemExit(main())
