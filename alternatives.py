"""Lower-carbon substitutes: the database proposes candidates, Gemini only judges substitutability.

Every number comes from AGRIBALYSE. Gemini picks codes from a closed candidate list and
anything outside that list is discarded. Results are keyed by AGRIBALYSE code (not
barcode), precomputed offline into data/substitutes.json, and filled live on a miss.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import math
import os
import threading
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import examples
from impact import AGRIBALYSE, FOODS

ROOT = Path(__file__).resolve().parent
STORE_PATH = ROOT / 'data' / 'substitutes.json'
MODEL = os.environ.get('GEMINI_MODEL', 'gemini-3.5-flash-lite')
# A substitute must cut the per-kg factor by at least this share to be worth suggesting.
MIN_REDUCTION = .2
MAX_CANDIDATES = 120
MAX_PICKS = 3
TIMEOUT_SECONDS = 20
LOCK = threading.Lock()
CODE_LOCKS = {}
SCHEMA = {'type': 'OBJECT', 'required': ['alternatives'], 'properties': {'alternatives': {
    'type': 'ARRAY', 'items': {'type': 'OBJECT', 'required': ['code', 'fit', 'reason'], 'properties': {
        'code': {'type': 'STRING'},
        'fit': {'type': 'STRING', 'enum': ['same_use', 'similar_use']},
        'reason': {'type': 'STRING'}}}}}}


BATCH_SCHEMA = {'type': 'OBJECT', 'required': ['foods'], 'properties': {'foods': {
    'type': 'ARRAY', 'items': {'type': 'OBJECT', 'required': ['code', 'alternatives'], 'properties': {
        'code': {'type': 'STRING'},
        'alternatives': SCHEMA['properties']['alternatives']}}}}}


class GeminiError(Exception):
    pass


def _load_store():
    try:
        store = json.loads(STORE_PATH.read_text())
        return store if isinstance(store.get('picks'), dict) else None
    except (OSError, ValueError, AttributeError):
        return None


STORE = _load_store() or {'schema_version': 1, 'model': MODEL, 'picks': {}}


def gemini_key():
    key = os.environ.get('GEMINI_API_KEY', '').strip()
    if key:
        return key
    path = ROOT / '.env'
    if path.exists():
        for line in path.read_text().splitlines():
            name, separator, value = line.partition('=')
            if separator and name.strip() == 'GEMINI_API_KEY':
                return value.strip().strip('\"\'')
    return ''


def candidates(code):
    """Same AGRIBALYSE subgroup, meaningfully lower per-kg footprint, lowest first."""
    food = FOODS[code]
    limit = food['kg_co2e_per_kg'] * (1 - MIN_REDUCTION)
    rows = [row for row in FOODS.values() if row['subgroup'] == food['subgroup']
            and row['code'] != code and row['kg_co2e_per_kg'] <= limit]
    return sorted(rows, key=lambda row: (row['kg_co2e_per_kg'], row['code']))[:MAX_CANDIDATES]


def build_prompt(food, rows):
    listing = '\n'.join(row['code'] + ': ' + row['label'] for row in rows)
    return ('You help grocery shoppers find realistic swaps.\n'
            'Scanned food: ' + food['label'] + '\n'
            'Candidate foods (code: name):\n' + listing + '\n\n'
            'Pick up to ' + str(MAX_PICKS) + ' candidates a typical shopper would realistically buy INSTEAD of '
            'the scanned food for the same meal or occasion. Prefer the closest in use, taste and form. '
            'Do not pick ingredients, powders to reconstitute, or baby foods unless the scanned food is one. '
            'Use only codes from the list. Give a one-sentence reason a shopper would understand. '
            'Return an empty list if no candidate is a realistic substitute.')


def build_batch_prompt(subgroup_rows, targets):
    """One prompt for several foods of one subgroup, sharing a single candidate list."""
    listing = '\n'.join(row['code'] + ' | ' + row['label'] + ' | ' + format(row['kg_co2e_per_kg'], 'g')
                        for row in subgroup_rows)
    return ('You help grocery shoppers find realistic swaps.\n'
            'Foods in one category (code | name | kg CO2e per kg):\n' + listing + '\n\n'
            'For EACH target code below, pick up to ' + str(MAX_PICKS) + ' foods from the list that a typical '
            'shopper would realistically buy INSTEAD of the target for the same meal or occasion, AND whose '
            'kg CO2e per kg is at most ' + str(int(100 * (1 - MIN_REDUCTION))) + '% of the target\'s value. '
            'Prefer the closest in use, taste and form. Do not pick ingredients, powders to reconstitute, or baby '
            'foods unless the target is one. Give a one-sentence reason a shopper would understand. '
            'Use an empty list when no food qualifies.\n'
            'Target codes: ' + ', '.join(targets))


def ask_gemini(prompt, schema=SCHEMA, timeout=TIMEOUT_SECONDS):
    key = gemini_key()
    if not key:
        raise GeminiError('missing_api_key')
    body = {'contents': [{'parts': [{'text': prompt}]}],
            'generationConfig': {'responseMimeType': 'application/json', 'responseSchema': schema,
                                 'temperature': 0, 'thinkingConfig': {'thinkingLevel': 'minimal'}}}
    request = Request('https://generativelanguage.googleapis.com/v1beta/models/' + MODEL + ':generateContent',
                      data=json.dumps(body).encode(),
                      headers={'x-goog-api-key': key, 'Content-Type': 'application/json'})
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
        return json.loads(payload['candidates'][0]['content']['parts'][0]['text'])
    except HTTPError as error:
        raise GeminiError('rate_limited' if error.code == 429 else 'provider_error') from None
    except (URLError, OSError, TimeoutError):
        raise GeminiError('network_or_timeout') from None
    except (ValueError, KeyError, IndexError, TypeError):
        raise GeminiError('invalid_response') from None


def validate_picks(answer, rows):
    """Keep only well-formed picks whose code was actually offered."""
    allowed = {row['code'] for row in rows}
    picks, seen = [], set()
    items = answer.get('alternatives') if isinstance(answer, dict) else None
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        code, fit, reason = item.get('code'), item.get('fit'), item.get('reason')
        if code in allowed and code not in seen and fit in ('same_use', 'similar_use') \
                and isinstance(reason, str) and reason.strip():
            seen.add(code)
            picks.append({'code': code, 'fit': fit, 'reason': reason.strip()[:240]})
    return picks[:MAX_PICKS]


def _merge_from_disk():
    # Another process (the precompute script or the server) may have added picks.
    disk = _load_store()
    if disk:
        STORE['picks'] = {**disk['picks'], **STORE['picks']}


def _save_store():
    _merge_from_disk()
    tmp = STORE_PATH.with_suffix('.tmp.' + str(os.getpid()))
    tmp.write_text(json.dumps(STORE, ensure_ascii=False, indent=1, sort_keys=True) + '\n')
    tmp.replace(STORE_PATH)


def picks_for(code, allow_live=True):
    """Return (picks, origin). Cached picks are reused; a miss calls Gemini once per code."""
    with LOCK:
        if code not in STORE['picks']:
            _merge_from_disk()
        if code in STORE['picks']:
            return STORE['picks'][code], 'precomputed'
        code_lock = CODE_LOCKS.setdefault(code, threading.Lock())
    rows = candidates(code)
    if not rows:
        return [], 'no_lower_candidates'
    if not allow_live:
        return None, 'not_precomputed'
    with code_lock:  # Coalesce simultaneous misses for the same food.
        with LOCK:
            if code in STORE['picks']:
                return STORE['picks'][code], 'precomputed'
        picks = validate_picks(ask_gemini(build_prompt(FOODS[code], rows)), rows)
        with LOCK:
            STORE['picks'][code] = picks
            STORE['model'] = MODEL
            _save_store()
    return picks, 'live'


def precompute_batch(targets):
    """Fill the store for several same-subgroup codes with one Gemini call. Returns codes stored."""
    subgroup = FOODS[targets[0]]['subgroup']
    rows = sorted((row for row in FOODS.values() if row['subgroup'] == subgroup),
                  key=lambda row: (row['kg_co2e_per_kg'], row['code']))
    answer = ask_gemini(build_batch_prompt(rows, targets), BATCH_SCHEMA, timeout=120)
    items = answer.get('foods') if isinstance(answer, dict) else None
    by_code = {item.get('code'): item for item in items if isinstance(item, dict)} if isinstance(items, list) else {}
    # Each pick is re-checked against that target's own lower-carbon candidates.
    picks = {code: validate_picks(by_code[code], candidates(code)) for code in targets if code in by_code}
    with LOCK:
        STORE['picks'].update(picks)
        STORE['model'] = MODEL
        _save_store()
    return list(picks)


def attach_examples(items):
    """Add real products to each swap; a failed search leaves the category name alone."""
    def fetch(item):
        try:
            item['examples'] = examples.examples_for(item['code'])
        except examples.SearchError:
            item['examples'] = []
    with ThreadPoolExecutor(max(1, len(items))) as pool:
        list(pool.map(fetch, items))


def lookup_alternatives(code, mass_kg=None):
    if code not in FOODS:
        return {'status': 'unsupported', 'reason': 'unknown_agribalyse_code', 'alternatives': []}
    if mass_kg is not None and not (isinstance(mass_kg, float) and math.isfinite(mass_kg) and 0 < mass_kg <= 1000):
        mass_kg = None
    food = FOODS[code]
    try:
        picks, origin = picks_for(code)
    except GeminiError as error:
        return {'status': 'unavailable', 'reason': 'gemini_' + str(error), 'alternatives': []}
    alternatives = []
    for pick in picks:
        row = FOODS[pick['code']]
        saved = food['kg_co2e_per_kg'] - row['kg_co2e_per_kg']
        alternatives.append({
            'code': row['code'], 'label': row['label'], 'fit': pick['fit'], 'reason': pick['reason'],
            'kg_co2e_per_kg': row['kg_co2e_per_kg'], 'dqr': row['dqr'],
            'reduction_percent': round(100 * saved / food['kg_co2e_per_kg'], 1),
            'kg_co2e_saved_per_package': round(saved * mass_kg, 6) if mass_kg else None,
        })
    attach_examples(alternatives)
    return {'status': 'ok' if alternatives else 'none_found', 'origin': origin,
            'reason': None if alternatives else origin if origin == 'no_lower_candidates' else 'no_realistic_substitute',
            'scanned': {'code': code, 'label': food['label'], 'kg_co2e_per_kg': food['kg_co2e_per_kg']},
            'mass_kg': mass_kg, 'alternatives': alternatives,
            'method': {'candidates': 'Same AGRIBALYSE subgroup, at least ' + str(int(MIN_REDUCTION * 100)) + '% lower kg CO2e per kg.',
                       'selection': 'Gemini (' + STORE.get('model', MODEL) + ') chose realistic substitutes from that list only.',
                       'numbers': 'All footprints and savings are computed from AGRIBALYSE; the model supplies no numbers.',
                       'examples': 'Example products are US items from Open Food Facts whose own category maps to the same AGRIBALYSE food; the footprint is that food\'s average, not a brand measurement.'},
            'examples_attribution': examples.attribution(),
            'source': {'citation': AGRIBALYSE['source']['citation'], 'url': AGRIBALYSE['source']['url']}}
