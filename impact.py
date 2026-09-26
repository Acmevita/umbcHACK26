"""Category emission proxies: AGRIBALYSE via OFF first, reviewed Poore & Nemecek tags second."""
from copy import deepcopy
import json
from pathlib import Path
import re

import open_food_facts
from quantity import parse_size

DATA = Path(__file__).resolve().parent / 'data'
CATALOG = json.loads((DATA / 'factors.json').read_text())
FACTORS = {row['id']: row for row in CATALOG['factors']}
AGRIBALYSE = json.loads((DATA / 'agribalyse.json').read_text())
FOODS = AGRIBALYSE['foods']

# Reviewed mapping rules, not a trained or accuracy-validated classifier.
# No default beef mapping: herd type is not inferable from generic beef tags.
# No generic plant-milk mapping: soy milk does not represent almond/oat milk.
TAG_MAP = {
    'en:apples': 'apples', 'en:bananas': 'bananas',
    'en:grapes': 'berries_grapes', 'en:strawberries': 'berries_grapes',
    'en:blueberries': 'berries_grapes', 'en:raspberries': 'berries_grapes',
    'en:broccoli': 'brassicas', 'en:cauliflowers': 'brassicas',
    'en:cabbages': 'brassicas', 'en:beet-sugars': 'beet_sugar',
    'en:cane-sugars': 'cane_sugar', 'en:cassavas': 'cassava',
    'en:cheeses': 'cheese', 'en:oranges': 'citrus_fruit', 'en:lemons': 'citrus_fruit',
    'en:eggs': 'eggs', 'en:peanuts': 'groundnuts', 'en:cow-milks': 'milk',
    'en:oat-flakes': 'oatmeal', 'en:onions': 'onions_leeks', 'en:leeks': 'onions_leeks',
    'en:potatoes': 'potatoes', 'en:rices': 'rice', 'en:rice': 'rice',
    'en:carrots': 'root_vegetables', 'en:soy-milks': 'soy_milk',
    'en:soy-drinks': 'soy_milk', 'en:plain-tofu': 'tofu', 'en:tofu': 'tofu',
    'en:tomatoes': 'tomatoes', 'en:wines': 'wine',
}
# Derived/compound forms cannot inherit a raw/base ingredient factor.
BLOCKED = re.compile(r'\b(chips|crisps|cookies|biscuits|cakes|pizza|pizzas|sauces|soups|'
                     r'juice|juices|smoothies|jam|jams|confectionery|desserts|powders|'
                     r'flours|flour|dried|dehydrated|cooked|prepared|canned|marinated|'
                     r'flavored|flavoured|chocolate|chocolates|ice cream|baby foods|'
                     r'supplements|meals|protein bars|processed cheeses|vegan cheeses|'
                     r'plant based cheeses|filled|stuffed|sweetened|smoked|fried)\b')


def classify(product):
    tags = product.get('category_tags', [])
    evidence_text = ' '.join(tags).replace('-', ' ') + ' ' + product.get('title', '').lower()
    evidence_text = evidence_text.lower().replace('-', ' ')
    if BLOCKED.search(evidence_text):
        return {'status': 'unknown', 'method': 'off_category_tags',
                'reason': 'processed_or_composite_food_needs_its_own_factor', 'factor_id': None, 'matched_tags': []}
    matches = [(tag, TAG_MAP[tag]) for tag in tags if tag in TAG_MAP]
    candidates = {factor for _, factor in matches}
    if len(candidates) != 1:
        return {'status': 'unknown', 'method': 'off_category_tags',
                'reason': 'conflicting_categories' if candidates else 'no_supported_category',
                'factor_id': None, 'matched_tags': [tag for tag, _ in matches]}
    return {'status': 'matched', 'method': 'off_category_tags', 'factor_id': candidates.pop(),
            'matched_tags': [tag for tag, _ in matches], 'reason': None,
            'validation': 'Heuristic mapping of community tags; accuracy has not been measured.'}


def classify_agribalyse(product):
    """Tier 1: the AGRIBALYSE code Open Food Facts assigned to this product's category."""
    reference = product.get('agribalyse')
    if not reference or reference.get('code') not in FOODS:
        return None
    food = FOODS[reference['code']]
    exact = reference['match'] == 'exact'
    return {'status': 'matched', 'method': 'off_agribalyse_code' if exact else 'off_agribalyse_proxy',
            'factor_id': 'agribalyse:' + food['code'], 'matched_tags': [], 'reason': None,
            'validation': ('Open Food Facts matched this product to an AGRIBALYSE food.' if exact else
                           'Open Food Facts assigned the closest AGRIBALYSE food for this category as a proxy.')
                          + ' Accuracy of that assignment has not been measured by us.'}


def package_mass(product, liquid):
    quantity = parse_size(product.get('quantity'))
    net = product.get('net_quantity')
    if quantity['status'] == 'unknown' and net:
        # OFF's normalized net quantity, used only when the label text is unreadable.
        fallback = parse_size(f"{net['value']} {net['unit']}")
        if fallback['status'] != 'unknown':
            fallback['raw'] = quantity['raw'] or fallback['raw']
            fallback['assumptions'].append('Package size taken from Open Food Facts’ normalized quantity.')
            quantity = fallback
    if quantity['status'] == 'known_volume' and liquid:
        quantity['mass_kg'] = round(quantity['volume_liters'], 12)
        quantity['reason'] = None
        quantity['assumptions'].append('Drink density assumed to be 1 kg per litre.')
    return quantity


def assess_product(product):
    mapping = classify_agribalyse(product)
    if mapping:
        food = FOODS[product['agribalyse']['code']]
        catalog_source = AGRIBALYSE['source']
        factor = {'id': mapping['factor_id'], 'label': food['label'], 'kg_co2e_per_kg': food['kg_co2e_per_kg'],
                  'statistic': 'agribalyse_average', 'source_id': catalog_source['id'],
                  'producer_range': None, 'dqr': food['dqr'], 'agribalyse_code': food['code']}
        liquid = food['liquid']
    else:
        mapping = classify(product)
        catalog_source = CATALOG['source']
        factor = deepcopy(FACTORS[mapping['factor_id']]) if mapping['status'] == 'matched' else None
        liquid = False
    quantity = package_mass(product, liquid)
    result = {'status': 'insufficient_data', 'geography': catalog_source['geography'],
              'estimate_type': 'food_category_proxy', 'classification': mapping,
              'package_quantity': quantity, 'per_package_kg_co2e': None,
              'per_kg_food_kg_co2e': None, 'producer_range': None, 'stages': None,
              'factor': None, 'source': deepcopy(catalog_source),
              'assumptions': list(quantity['assumptions']), 'calculation': None}
    if mapping['status'] != 'matched':
        result['reason'] = mapping['reason']
        return result
    result['factor'] = factor
    result['per_kg_food_kg_co2e'] = factor['kg_co2e_per_kg']
    result['assumptions'].append('Scanned food is represented by the matched category average, not supplier-specific data.')
    stages = (product.get('agribalyse') or {}).get('stages_kg_co2e_per_kg') if factor['source_id'] == 'agribalyse_3_2' else None
    # Only show OFF's stage split if it sums to the same total as our catalog value.
    if stages and abs(sum(stages.values()) - factor['kg_co2e_per_kg']) <= .02 * factor['kg_co2e_per_kg'] + .01:
        result['stages'] = {'per_kg': stages}
    if quantity['mass_kg'] is None:
        result['status'] = 'category_only'
        result['reason'] = quantity['reason']
        return result
    mass = quantity['mass_kg']
    result.update({'status': 'estimated', 'reason': None,
                   'per_package_kg_co2e': round(mass * factor['kg_co2e_per_kg'], 6),
                   'calculation': {'operation': 'multiply',
                                   'mass_kg': mass,
                                   'factor_kg_co2e_per_kg': factor['kg_co2e_per_kg'],
                                   'output_unit': 'kg_CO2e_per_package'}})
    if result['stages']:
        result['stages']['per_package'] = {stage: round(value * mass, 6) for stage, value in stages.items()}
    result['assumptions'].append('Declared net contents represent the food in the reference category; no cooking, yield, peel or drained-weight adjustment is inferred.')
    return result


def lookup_impact(barcode):
    lookup = open_food_facts.lookup(barcode)
    # No commercial provider fields are copied into the OFF cache or response.
    result = {'schema_version': 1, 'barcode': barcode, 'open_food_facts': lookup,
              'impact': None}
    if lookup['status'] == 'ok':
        result['impact'] = assess_product(lookup['product'])
    else:
        result['impact'] = {'status': 'insufficient_data', 'reason': 'open_food_facts_' + lookup['reason'],
                            'geography': 'Unknown; not US-specific', 'per_package_kg_co2e': None,
                            'per_kg_food_kg_co2e': None}
    return result
