"""Build the local catalog from downloaded OWID CSV + indicator metadata (offline)."""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
SOURCE_URL = 'https://ourworldindata.org/grapher/ghg-per-kg-poore'


def build_catalog(csv_path, metadata_path):
    content = csv_path.read_bytes()
    metadata = json.loads(metadata_path.read_text())
    if metadata.get('id') != 1206176 or metadata.get('nonRedistributable') is not False:
        raise ValueError('Unexpected indicator or redistribution restriction; review source metadata.')
    records = []
    names = set()
    for row in csv.DictReader(content.decode('utf-8-sig').splitlines()):
        name = row['Entity']
        value = float(row['Greenhouse gas emissions per kilogram'])
        if name in names or not math.isfinite(value) or value < 0 or row['Year'] != '2010':
            raise ValueError('Unexpected category, value, or year; review the updated source.')
        names.add(name)
        records.append({'id': re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_'),
                        'label': name, 'kg_co2e_per_kg': value,
                        'statistic': 'global_mean', 'source_id': 'poore_nemecek_2018_owid',
                        'producer_range': None})
    if len(records) != 38:
        raise ValueError('Expected the reviewed 38-row source snapshot; review schema before updating.')
    return {'schema_version': 1, 'source': {
        'id': 'poore_nemecek_2018_owid',
        'citation': 'Poore and Nemecek (2018), Reducing food’s environmental impacts through producers and consumers; processed by Our World in Data.',
        'url': SOURCE_URL, 'csv_url': SOURCE_URL + '.csv',
        'metadata_url': 'https://api.ourworldindata.org/v1/indicators/1206176.metadata.json',
        'dataset_version': metadata['datasetVersion'], 'reference_year': 2010,
        'reference_year_note': 'Dataset time marker; not a measurement year for a scanned product.',
        'geography': 'Global; not US-specific', 'unit': 'kg_CO2e_per_kg_food',
        'boundary': 'cradle_to_retail',
        'included': ['land_use_change', 'agriculture_and_feed', 'processing', 'packaging', 'transport_to_retail', 'retail'],
        'excluded': ['consumer_transport', 'home_storage', 'home_cooking', 'household_waste'],
        'methodology_url': 'https://ourworldindata.org/faqs-environmental-impacts-food',
        'license': {'owid_processing': 'CC BY (attribute OWID and original authors)',
                    'owid_non_redistributable': metadata['nonRedistributable'],
                    'third_party_notice': 'Original third-party terms also apply. Supplementary producer-level files are not included.'},
        'csv_sha256': hashlib.sha256(content).hexdigest(),
        'limitations': ['Category proxy, not a measured brand footprint.',
                       'No numerical uncertainty or producer percentiles in this imported table.',
                       'No stage-level figures in this imported table.',
                       'Does not cover all foods or processed recipes.'],
    }, 'factors': records}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--csv', type=Path, required=True)
    parser.add_argument('--metadata', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT / 'data' / 'factors.json')
    args = parser.parse_args()
    catalog = build_catalog(args.csv, args.metadata)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + '\n')
    print('Imported ' + str(len(catalog['factors'])) + ' global category factors.')
