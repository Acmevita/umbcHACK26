"""Real US products for an AGRIBALYSE food, so swaps read "Coca-Cola, Pepsi" instead of "Cola, with sugar".

Uses Open Food Facts' search service. A product is kept only when its own categories
resolve to the same AGRIBALYSE food, so the footprint shown matches the example.
Results are cached per code in data/swap_examples.json (precomputed; filled live on a miss).
"""
from collections import deque
import json
import os
import threading
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from open_food_facts import ATTRIBUTION

ROOT = Path(__file__).resolve().parent
STORE_PATH = ROOT / 'data' / 'swap_examples.json'
TAGS = json.loads((ROOT / 'data' / 'off_categories.json').read_text())['tags']
SEARCH_URL = 'https://search.openfoodfacts.org/search'
MAX_EXAMPLES = 3
TIMEOUT_SECONDS = 8
MAX_REQUESTS_PER_MINUTE = 30
LOCK = threading.Lock()
REQUEST_TIMES = deque()


class SearchError(Exception):
    pass


def _load_store():
    try:
        store = json.loads(STORE_PATH.read_text())
        return store if isinstance(store.get('examples'), dict) else None
    except (OSError, ValueError, AttributeError):
        return None


STORE = _load_store() or {'schema_version': 1, 'examples': {}}


def tags_for(code):
    """OFF categories linked to this code: exact matches first, then proxies."""
    exact = sorted(tag for tag, codes in TAGS.items() if codes[0] == code)
    proxy = sorted(tag for tag, codes in TAGS.items() if codes[1] == code and tag not in exact)
    return exact + proxy


def resolves_to(category_tags, code):
    """True when a product's categories point to this AGRIBALYSE food and no other."""
    exact = {TAGS[tag][0] for tag in category_tags if tag in TAGS and TAGS[tag][0]}
    if exact:
        return exact == {code}
    proxy = {TAGS[tag][1] for tag in category_tags if tag in TAGS and TAGS[tag][1]}
    return proxy == {code}


def _text(value):
    if isinstance(value, list):
        value = ', '.join(item.strip() for item in value if isinstance(item, str) and item.strip())
    return value.strip()[:120] if isinstance(value, str) else ''


def _search(tags):
    with LOCK:
        now = time.monotonic()
        while REQUEST_TIMES and REQUEST_TIMES[0] <= now - 60:
            REQUEST_TIMES.popleft()
        if len(REQUEST_TIMES) >= MAX_REQUESTS_PER_MINUTE:
            raise SearchError('local_rate_limit')
        REQUEST_TIMES.append(now)
    query = '(' + ' OR '.join('categories_tags:"' + tag + '"' for tag in tags[:20]) + ') AND countries_tags:"en:united-states"'
    params = urlencode({'q': query, 'page_size': 40, 'langs': 'en', 'sort_by': '-unique_scans_n',
                        'fields': 'code,product_name,brands,quantity,image_front_small_url,categories_tags'})
    request = Request(SEARCH_URL + '?' + params, headers={
        'Accept': 'application/json',
        'User-Agent': os.environ.get('OPEN_FOOD_FACTS_USER_AGENT', 'BetterBasket/0.3 (local hackathon prototype; Python backend)')})
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read(2 * 1024 * 1024))
        hits = payload['hits']
        if not isinstance(hits, list):
            raise TypeError
        return hits
    except HTTPError as error:
        raise SearchError('rate_limited' if error.code == 429 else 'provider_error') from None
    except (URLError, OSError, TimeoutError):
        raise SearchError('network_or_timeout') from None
    except (ValueError, KeyError, TypeError):
        raise SearchError('invalid_response') from None


def pick_examples(hits, code):
    examples, seen = [], set()
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        barcode = str(hit.get('code', ''))
        brands = hit.get('brands') if isinstance(hit.get('brands'), list) else str(hit.get('brands') or '').split(',')
        name, brand = _text(hit.get('product_name')), _text(next((b for b in brands if isinstance(b, str) and b.strip()), ''))
        categories = hit.get('categories_tags') if isinstance(hit.get('categories_tags'), list) else []
        key = (name.lower(), brand.lower())
        if not (barcode.isdigit() and name and brand) or key in seen or not resolves_to(categories, code):
            continue
        image = hit.get('image_front_small_url')
        valid_image = isinstance(image, str) and urlsplit(image).scheme == 'https' \
            and urlsplit(image).netloc.endswith('openfoodfacts.org')
        seen.add(key)
        examples.append({'barcode': barcode, 'name': name, 'brand': brand,
                         'quantity': _text(hit.get('quantity')), 'image': image if valid_image else '',
                         'url': 'https://world.openfoodfacts.org/product/' + barcode})
        if len(examples) == MAX_EXAMPLES:
            break
    return examples


def _save_store():
    disk = _load_store()
    if disk:
        STORE['examples'] = {**disk['examples'], **STORE['examples']}
    tmp = STORE_PATH.with_suffix('.tmp.' + str(os.getpid()))
    tmp.write_text(json.dumps(STORE, ensure_ascii=False, indent=1, sort_keys=True) + '\n')
    tmp.replace(STORE_PATH)


def examples_for(code, allow_live=True):
    """Cached real products for a code; one search on a miss. Returns [] when none qualify."""
    with LOCK:
        if code in STORE['examples']:
            return STORE['examples'][code]
    if not allow_live:
        return None
    tags = tags_for(code)
    examples = pick_examples(_search(tags), code) if tags else []
    with LOCK:
        STORE['examples'][code] = examples
        _save_store()
    return examples


def attribution():
    return dict(ATTRIBUTION, note='Product names and images from Open Food Facts contributors.')
