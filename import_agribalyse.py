"""Build data/agribalyse.json from the downloaded ADEME AGRIBALYSE synthesis CSV (offline)."""
import argparse
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
DATASET_URL = 'https://data.ademe.fr/datasets/agribalyse-31-synthese'
CSV_URL = 'https://data.ademe.fr/data-fair/api/v1/datasets/agribalyse-31-synthese/raw'
# Liquids whose AGRIBALYSE value is per kg but which are sold by volume.
LIQUID_SUBGROUPS = {'boisson alcoolisées', 'boissons sans alcool', 'eaux', 'laits',
                    'laits et boissons infantiles'}


def build_catalog(csv_path):
    content = csv_path.read_bytes()
    foods = {}
    for row in csv.DictReader(io.StringIO(content.decode('utf-8-sig'), newline='')):
        code = row['Code AGB'].strip()
        if not code:
            continue  # The export pads the file with empty rows.
        value = float(row['Changement climatique'])
        if code in foods or not re.fullmatch(r'\d+(?:_\d+)?', code) or not math.isfinite(value) or value < 0:
            raise ValueError('Unexpected code or climate value for ' + code + '; review the source.')
        foods[code] = {'code': code, 'label': row['LCI Name'].strip(),
                       'label_fr': row['Nom du Produit en Français'].strip(),
                       'group': row["Groupe d'aliment"].strip(),
                       'subgroup': row["Sous-groupe d'aliment"].strip(),
                       'kg_co2e_per_kg': value, 'dqr': float(row['DQR']),
                       'liquid': row["Sous-groupe d'aliment"].strip() in LIQUID_SUBGROUPS}
    if len(foods) != 2451:
        raise ValueError('Expected the reviewed 2451-row v3.2 snapshot; review schema before updating.')
    return {'schema_version': 1, 'source': {
        'id': 'agribalyse_3_2',
        'citation': 'AGRIBALYSE v3.2 synthesis, ADEME / INRAE.',
        'url': DATASET_URL, 'csv_url': CSV_URL, 'dataset_version': '3.2',
        'geography': 'France; not US-specific', 'unit': 'kg_CO2e_per_kg_food',
        'boundary': 'farm_to_plate',
        'boundary_label': 'Farm to plate: farming, processing, packaging, transport, retail and home preparation.',
        'included': ['agriculture', 'processing', 'packaging', 'transport', 'retail', 'consumption'],
        'excluded': [],
        'methodology_url': 'https://doc.agribalyse.fr/documentation-en/',
        'license': {'name': 'Licence Ouverte / Open Licence 2.0 (Etalab)',
                    'url': 'https://www.etalab.gouv.fr/licence-ouverte-open-licence'},
        'csv_sha256': hashlib.sha256(content).hexdigest(),
        'limitations': ['Average French-market product, not a measured brand footprint.',
                        'DQR is a data-quality rating (1 best to 5 worst), not a confidence interval.'],
    }, 'foods': foods}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--csv', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT / 'data' / 'agribalyse.json')
    args = parser.parse_args()
    catalog = build_catalog(args.csv)
    args.output.write_text(json.dumps(catalog, ensure_ascii=False, separators=(',', ':')) + '\n')
    print('Imported ' + str(len(catalog['foods'])) + ' AGRIBALYSE foods.')
