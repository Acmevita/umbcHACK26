"""Local development server; keep Barcode Lookup credentials on the server."""
import argparse
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
STATIC = {'/': ('index.html', 'text/html'), '/index.html': ('index.html', 'text/html'),
          '/app.js': ('app.js', 'text/javascript'), '/styles.css': ('styles.css', 'text/css')}
CACHE = {}
LOCK = threading.Lock()


class LookupError(Exception):
    def __init__(self, status, message):
        self.status = status
        self.message = message


def api_key():
    key = os.environ.get('BARCODE_LOOKUP_API_KEY', '').strip()
    if key:
        return key
    path = ROOT / '.env'
    if path.exists():
        for line in path.read_text().splitlines():
            name, separator, value = line.partition('=')
            if separator and name.strip() == 'BARCODE_LOOKUP_API_KEY':
                return value.strip().strip('\"\'')
    return ''


def valid_barcode(code):
    if not re.fullmatch(r'(?:[0-9]{8}|[0-9]{12}|[0-9]{13}|[0-9]{14})', code):
        return False
    total = sum(int(digit) * (3 if index % 2 == 0 else 1)
                for index, digit in enumerate(reversed(code[:-1])))
    return (10 - total % 10) % 10 == int(code[-1])


def lookup_product(barcode):
    if not valid_barcode(barcode):
        raise LookupError(400, 'Enter a valid UPC/EAN barcode including its check digit.')
    key = api_key()
    if not key or key == 'your_api_key_here':
        raise LookupError(503, 'Product lookup needs a Barcode Lookup API key. Add it to the server’s .env file, then retry.')
    identity = barcode.zfill(14)
    # Serialize requests so simultaneous scans cannot create duplicate paid calls.
    with LOCK:
        cached = CACHE.get(identity)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        query = urlencode({'barcode': barcode, 'key': key})
        request = Request('https://api.barcodelookup.com/v3/products?' + query,
                          headers={'Accept': 'application/json', 'User-Agent': 'BetterBasket/0.1'})
        try:
            with urlopen(request, timeout=12) as response:
                payload = json.load(response)
        except HTTPError as error:
            messages = {
                401: (502, 'Barcode Lookup rejected the API key. Check your server configuration.'),
                403: (502, 'Barcode Lookup denied access. Check your API key and subscription.'),
                404: (404, 'No product found for this barcode in Barcode Lookup.'),
                429: (429, 'Barcode Lookup’s request limit was reached. Please try again later.'),
            }
            code, message = messages.get(error.code, (502, 'Barcode Lookup is temporarily unavailable. Try again later.'))
            raise LookupError(code, message) from None
        except (URLError, TimeoutError, OSError):
            raise LookupError(502, 'Could not reach Barcode Lookup. Check the server’s internet connection and retry.') from None
        except (ValueError, TypeError):
            raise LookupError(502, 'Barcode Lookup returned an unreadable response.') from None
        if not isinstance(payload, dict) or not isinstance(payload.get('products'), list):
            raise LookupError(502, 'Barcode Lookup returned an unexpected response.')
        products = payload['products']
        if not products:
            raise LookupError(404, 'No product found for this barcode in Barcode Lookup.')
        product = next((item for item in products if isinstance(item, dict)
                        and str(item.get('barcode_number', '')).zfill(14) == identity), None)
        if product is None:
            raise LookupError(502, 'The returned product did not match the scanned barcode.')

        def field(name):
            value = product.get(name)
            return value.strip() if isinstance(value, str) else ''

        images = product.get('images', [])
        image = next((url for url in images if isinstance(url, str)
                      and urlsplit(url).scheme == 'https' and urlsplit(url).netloc), '') if isinstance(images, list) else ''
        result = {'barcode': barcode, 'title': field('title') or 'Unnamed product',
                  'brand': field('brand'), 'description': field('description'),
                  'category': field('category'), 'size': field('size'), 'image': image,
                  'source': 'Barcode Lookup', 'sourceUrl': 'https://www.barcodelookup.com/' + barcode}
        if len(CACHE) >= 256:
            CACHE.pop(next(iter(CACHE)))
        CACHE[identity] = (time.monotonic() + 3600, result)
        return result


class Handler(BaseHTTPRequestHandler):
    def send_body(self, code, body, content_type):
        self.send_response(code)
        self.send_header('Content-Type', content_type + '; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path)
        if path.path == '/api/products':
            # No cross-origin use of the local credentialed endpoint.
            origin = self.headers.get('Origin')
            if origin and urlsplit(origin).netloc != self.headers.get('Host'):
                self.send_body(403, b'{"error":"Cross-origin lookup is not allowed."}', 'application/json')
                return
            barcode = parse_qs(path.query).get('barcode', [''])[0]
            try:
                result = {'product': lookup_product(barcode)}
                code = 200
            except LookupError as error:
                result, code = {'error': error.message}, error.status
            self.send_body(code, json.dumps(result).encode(), 'application/json')
        elif path.path in STATIC:
            filename, mime = STATIC[path.path]
            self.send_body(200, (ROOT / filename).read_bytes(), mime)
        else:
            # Never serve .env, source files, directory listings, or .git.
            self.send_body(404, b'Not found', 'text/plain')

    def log_message(self, format, *args):
        # Avoid logging full request URLs, barcodes, or provider credentials.
        pass


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8001)
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print('Better Basket: http://localhost:' + str(args.port), flush=True)
    print('Product lookup: ' + ('configured' if api_key() else 'add BARCODE_LOOKUP_API_KEY to .env'), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
