"""Build data/off_categories.json (OFF category tag <-> AGRIBALYSE code) from the OFF taxonomy (offline)."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE_URL = 'https://static.openfoodfacts.org/data/taxonomies/categories.json'


def build(path):
    content = path.read_bytes()
    taxonomy = json.loads(content)
    tags = {}
    for tag, node in taxonomy.items():
        codes = []
        for key in ('agribalyse_food_code', 'agribalyse_proxy_food_code'):
            value = node.get(key)
            value = value.get('en') if isinstance(value, dict) else value
            codes.append(str(value) if value else None)
        if any(codes):
            tags[tag] = codes  # [exact code, proxy code]
    return {'schema_version': 1, 'source': {'url': SOURCE_URL, 'license': 'ODbL-1.0 (Open Food Facts)',
                                            'sha256': hashlib.sha256(content).hexdigest()},
            'tags': tags}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--taxonomy', type=Path, required=True, help='Downloaded ' + SOURCE_URL)
    parser.add_argument('--output', type=Path, default=ROOT / 'data' / 'off_categories.json')
    args = parser.parse_args()
    catalog = build(args.taxonomy)
    args.output.write_text(json.dumps(catalog, ensure_ascii=False, separators=(',', ':'), sort_keys=True) + '\n')
    print('Mapped ' + str(len(catalog['tags'])) + ' OFF categories to AGRIBALYSE codes.')
