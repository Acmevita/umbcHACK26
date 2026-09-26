"""HTTP verification for the new impact endpoint; --live uses free OFF reads."""
import argparse
from contextlib import ExitStack
import io
import json
import threading
import time
from unittest.mock import patch
from urllib.request import urlopen

import open_food_facts as off
import server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--barcode', default='082657500638')
    args = parser.parse_args()
    if not server.valid_barcode(args.barcode):
        parser.error('Invalid barcode checksum or length')
    print('Mode: ' + ('LIVE Open Food Facts; no Barcode Lookup calls' if args.live else 'SIMULATED plain tofu, not actual barcode contents'))
    with off.LOCK:
        off.CACHE.clear()
        off.REQUEST_TIMES.clear()
    with ExitStack() as stack:
        if not args.live:
            mock = stack.enter_context(patch('open_food_facts.urlopen', return_value=io.BytesIO(json.dumps({
                'status': 1, 'product': {'code': args.barcode, 'product_name': 'Synthetic plain tofu fixture',
                                        'quantity': '500 g', 'categories_tags': ['en:tofu']}
            }).encode())))
        httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            url = 'http://127.0.0.1:' + str(httpd.server_address[1]) + '/api/impact?barcode=' + args.barcode
            timings = []
            for mode in ['cold', 'cached']:
                start = time.perf_counter()
                with urlopen(url, timeout=20) as response:
                    result = json.load(response)
                elapsed = round((time.perf_counter() - start) * 1000, 2)
                timings.append(elapsed)
                print(json.dumps({'request': mode, 'http_ms': elapsed,
                                  'provider_status': result['open_food_facts']['status'],
                                  'provider_reason': result['open_food_facts'].get('reason'),
                                  'cache_hit': result['open_food_facts']['cache_hit'],
                                  'impact_status': result['impact']['status'],
                                  'reason': result['impact'].get('reason'),
                                  'kg_co2e_per_package': result['impact']['per_package_kg_co2e']}))
                if result['open_food_facts']['status'] != 'ok':
                    print('Live provider lookup did not return product facts; no footprint invented.')
                    return 1
                assert 'not US-specific' in result['impact']['geography']
                if not args.live:
                    assert result['impact']['per_package_kg_co2e'] == 1.58
                if mode == 'cached':
                    assert result['open_food_facts']['cache_hit']
            if not args.live:
                assert mock.call_count == 1
            assert max(timings) < 30000
            print('PASS: HTTP integration and cache; two timing samples, not a latency percentile benchmark.')
            return 0
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join()


if __name__ == '__main__':
    raise SystemExit(main())
