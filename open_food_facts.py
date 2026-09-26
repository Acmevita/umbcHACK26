"""Read-only OFF adapter. Keep its ODbL records distinct from commercial data."""
from collections import deque
from concurrent.futures import Future, TimeoutError as FutureTimeout
from copy import deepcopy
import json
import math
import os
import re
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

FIELDS = ('code,product_name,brands,quantity,product_quantity,product_quantity_unit,categories_tags,'
          'categories_properties,ecoscore_data,ingredients_text,countries_tags,last_modified_t')
STAGES = ('agriculture', 'processing', 'packaging', 'transportation', 'distribution', 'consumption')
ATTRIBUTION = {'provider': 'Open Food Facts', 'license': 'ODbL-1.0',
               'url': 'https://world.openfoodfacts.org',
               'terms_url': 'https://world.openfoodfacts.org/terms-of-use'}
TIMEOUT_SECONDS = 6
MAX_RESPONSE_BYTES = 1024 * 1024
CACHE = {}
INFLIGHT = {}
REQUEST_TIMES = deque()
LOCK = threading.Lock()


def _fetch(barcode):
    url = 'https://world.openfoodfacts.org/api/v2/product/' + barcode + '.json?' + urlencode({'fields': FIELDS})
    request = Request(url, headers={
        'Accept': 'application/json',
        'User-Agent': os.environ.get('OPEN_FOOD_FACTS_USER_AGENT',
                                    'BetterBasket/0.2 (local hackathon prototype; Python backend)'),
    })
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            return {'status': 'unavailable', 'reason': 'response_too_large'}
        payload = json.loads(body)
    except HTTPError as error:
        return {'status': 'not_found' if error.code == 404 else 'unavailable',
                'reason': 'not_found' if error.code == 404 else 'rate_limited' if error.code == 429 else 'provider_error'}
    except (URLError, OSError, TimeoutError):
        return {'status': 'unavailable', 'reason': 'network_or_timeout'}
    except (ValueError, TypeError):
        return {'status': 'unavailable', 'reason': 'invalid_response'}
    if not isinstance(payload, dict):
        return {'status': 'unavailable', 'reason': 'invalid_response'}
    if payload.get('status') == 0:
        return {'status': 'not_found', 'reason': 'not_found'}
    product = payload.get('product')
    if payload.get('status') != 1 or not isinstance(product, dict):
        return {'status': 'unavailable', 'reason': 'invalid_response'}
    code = str(product.get('code', payload.get('code', '')))
    if not code.isascii() or not code.isdigit() or code.zfill(14) != barcode.zfill(14):
        return {'status': 'unavailable', 'reason': 'barcode_mismatch'}

    def text(name):
        value = product.get(name)
        return value.strip()[:4000] if isinstance(value, str) else ''

    def tags(name):
        value = product.get(name)
        return [tag for tag in value[:200] if isinstance(tag, str) and len(tag) < 200] if isinstance(value, list) else []

    return {'status': 'ok', 'reason': None, 'product': {
        'agribalyse': _agribalyse_reference(product), 'net_quantity': _net_quantity(product),
        'barcode': barcode, 'title': text('product_name'), 'brand': text('brands'),
        'quantity': text('quantity'), 'category_tags': tags('categories_tags'),
        'ingredients_text': text('ingredients_text'), 'countries_tags': tags('countries_tags'),
        'source_url': 'https://world.openfoodfacts.org/product/' + barcode,
    }}


def _agribalyse_reference(product):
    """OFF's own category-to-AGRIBALYSE assignment: exact code, else a category proxy."""
    ecoscore = product.get('ecoscore_data')
    data = ecoscore.get('agribalyse') if isinstance(ecoscore, dict) else None
    data = data if isinstance(data, dict) else {}
    properties = product.get('categories_properties')
    properties = properties if isinstance(properties, dict) else {}
    for match, candidates in [('exact', [data.get('agribalyse_food_code'), properties.get('agribalyse_food_code:en')]),
                              ('proxy', [data.get('agribalyse_proxy_food_code'), properties.get('agribalyse_proxy_food_code:en')])]:
        code = next((str(value) for value in candidates if isinstance(value, (str, int))
                     and re.fullmatch(r'\d{1,6}(?:_\d)?', str(value))), None)
        if code:
            break
    else:
        return None
    stages = None
    # Stage split is only usable when OFF computed it for this same code.
    if str(data.get('code')) == code:
        values = {stage: data.get('co2_' + stage) for stage in STAGES}
        if all(isinstance(value, (int, float)) and math.isfinite(value) for value in values.values()):
            stages = values
    version = data.get('version')
    return {'code': code, 'match': match, 'stages_kg_co2e_per_kg': stages,
            'off_version': version if isinstance(version, str) else None}


def _net_quantity(product):
    value, unit = product.get('product_quantity'), product.get('product_quantity_unit')
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if unit not in ('g', 'ml') or not math.isfinite(value) or not 0 < value <= 1000000:
        return None
    return {'value': value, 'unit': unit}


def lookup(barcode):
    """Bound cache size, coalesce duplicate requests, and limit calls to 15/min."""
    # Validation here is independent of server.py to keep this adapter reusable.
    if not isinstance(barcode, str) or not barcode.isascii() or not barcode.isdigit() or len(barcode) not in (8, 12, 13, 14):
        return {'status': 'unavailable', 'reason': 'invalid_barcode', 'attribution': ATTRIBUTION}
    identity = barcode.zfill(14)
    now = time.monotonic()
    with LOCK:
        cached = CACHE.get(identity)
        if cached and cached[0] > now:
            result = deepcopy(cached[1])
            result['cache_hit'] = True
            return result
        future = INFLIGHT.get(identity)
        owner = future is None
        if owner:
            while REQUEST_TIMES and REQUEST_TIMES[0] <= now - 60:
                REQUEST_TIMES.popleft()
            if len(REQUEST_TIMES) >= 15:
                return {'status': 'unavailable', 'reason': 'local_rate_limit', 'attribution': ATTRIBUTION, 'cache_hit': False}
            REQUEST_TIMES.append(now)
            future = INFLIGHT[identity] = Future()
    if not owner:
        try:
            return deepcopy(future.result(timeout=TIMEOUT_SECONDS + 1))
        except FutureTimeout:
            return {'status': 'unavailable', 'reason': 'network_or_timeout', 'attribution': ATTRIBUTION, 'cache_hit': False}
    try:
        result = _fetch(barcode)
    except Exception:
        # Do not leak provider URLs or untrusted response bodies; always resolve waiters.
        result = {'status': 'unavailable', 'reason': 'invalid_response'}
    result.update({'attribution': ATTRIBUTION, 'cache_hit': False})
    ttl = 86400 if result['status'] == 'ok' else 600 if result['status'] == 'not_found' else 30
    with LOCK:
        if len(CACHE) >= 256:
            CACHE.pop(next(iter(CACHE)))
        CACHE[identity] = (time.monotonic() + ttl, deepcopy(result))
        INFLIGHT.pop(identity, None)
        future.set_result(deepcopy(result))
    return result
