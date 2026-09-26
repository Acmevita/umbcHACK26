"""Conservative parsing of the net contents of ONE scanned retail package."""
import math
import re

MASS_KG = {'g': .001, 'gram': .001, 'grams': .001, 'kg': 1,
           'oz': .028349523125, 'ounce': .028349523125, 'ounces': .028349523125,
           'lb': .45359237, 'lbs': .45359237, 'pound': .45359237, 'pounds': .45359237}
VOLUME_L = {'ml': .001, 'cl': .01, 'l': 1, 'liter': 1, 'liters': 1,
            'litre': 1, 'litres': 1, 'fl oz': .0295735295625}
MEASURE = re.compile(r'(\d+(?:\.\d+)?|\.\d+)\s*(fl\s*oz|ounces?|oz|pounds?|lbs?|grams?|kg|g|ml|cl|litres?|liters?|l)', re.I)


def parse_size(raw):
    result = {'raw': raw if isinstance(raw, str) else '', 'status': 'unknown',
              'mass_kg': None, 'volume_liters': None, 'pack_count': None,
              'assumptions': [], 'reason': 'missing_package_quantity'}
    if not isinstance(raw, str) or not raw.strip():
        return result
    if len(raw) > 200:
        result['reason'] = 'unsupported_quantity_format'
        return result
    value = raw.lower().strip().replace('×', 'x').replace('℮', '').strip()
    value = re.sub(r'^net\s*(?:wt\.?|weight|contents?)\s*:?\s*', '', value)
    value = re.sub(r'fl\.?\s*oz\.?', 'fl oz', value).strip()
    multiplier = re.fullmatch(r'(\d+)\s*x\s*(.+)', value)
    count = int(multiplier[1]) if multiplier else 1
    expression = multiplier[2] if multiplier else value
    # A dual-unit label is acceptable only if both descriptions agree.
    dual = re.fullmatch(r'([^()]+)\s*\(([^()]+)\)', expression)
    pieces = [dual[1].strip(), dual[2].strip()] if dual else [expression]
    if dual and multiplier:
        result['reason'] = 'ambiguous_multipack_quantity'
        return result
    values = []
    for piece in pieces:
        match = MEASURE.fullmatch(piece.strip())
        if not match or not 1 <= count <= 10000:
            result['reason'] = 'unsupported_quantity_format'
            return result
        amount = float(match[1])
        unit = re.sub(r'\s+', ' ', match[2])
        unit = 'fl oz' if unit.replace(' ', '') == 'floz' else unit
        if not math.isfinite(amount) or not 0 < amount <= 1000000:
            result['reason'] = 'invalid_quantity'
            return result
        if unit in MASS_KG:
            values.append(('mass', amount * count * MASS_KG[unit]))
        else:
            values.append(('volume', amount * count * VOLUME_L[unit]))
            if unit == 'fl oz':
                result['assumptions'] = ['Fluid ounces interpreted as US fluid ounces.']
    if len(values) == 2:
        first, second = values
        if first[0] != second[0] or abs(first[1] - second[1]) > .02 * max(first[1], second[1]):
            result['reason'] = 'conflicting_quantity_labels'
            return result
    kind, amount = values[0]
    result.update({'status': 'known_mass' if kind == 'mass' else 'known_volume',
                   'pack_count': count,
                   'reason': None if kind == 'mass' else 'density_required_for_mass_factor'})
    result['mass_kg' if kind == 'mass' else 'volume_liters'] = round(amount, 12)
    return result
