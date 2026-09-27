"""Fill data/substitutes.json ahead of time so scans never wait on Gemini. Safe to rerun.

Foods are batched by AGRIBALYSE subgroup so ~2,200 foods need only ~150 requests,
which fits Gemini's free tier (15 requests/minute).
"""
import argparse
import time

import alternatives


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codes', nargs='*', help='Only these AGRIBALYSE codes (default: all).')
    parser.add_argument('--batch', type=int, default=15)
    args = parser.parse_args()
    codes = args.codes or sorted(alternatives.FOODS)
    todo = [code for code in codes if code not in alternatives.STORE['picks'] and alternatives.candidates(code)]
    groups = {}
    for code in todo:
        groups.setdefault(alternatives.FOODS[code]['subgroup'], []).append(code)
    batches = [group[i:i + args.batch] for group in groups.values() for i in range(0, len(group), args.batch)]
    print(f'{len(todo)} foods need Gemini in {len(batches)} requests.', flush=True)
    start, stored, failed = time.monotonic(), 0, 0
    for number, batch in enumerate(batches, 1):
        for attempt in range(6):
            try:
                stored += len(alternatives.precompute_batch(batch))
                break
            except alternatives.GeminiError as error:
                if str(error) == 'missing_api_key':
                    raise SystemExit('Add GEMINI_API_KEY to .env first.')
                time.sleep(65 if str(error) == 'rate_limited' else 5 * (attempt + 1))
        else:
            failed += 1
        print(f'{number}/{len(batches)} requests, {stored} foods stored, {failed} failed batches, '
              f'{time.monotonic() - start:.0f}s', flush=True)
