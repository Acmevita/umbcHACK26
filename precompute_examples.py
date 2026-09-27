"""Fill data/swap_examples.json with real US products for every suggested swap. Safe to rerun."""
import time

import alternatives
import examples

if __name__ == '__main__':
    targets = sorted({pick['code'] for picks in alternatives.STORE['picks'].values() for pick in picks})
    todo = [code for code in targets if code not in examples.STORE['examples']]
    print(f'{len(todo)} of {len(targets)} swap foods need a product search.', flush=True)
    found = 0
    for number, code in enumerate(todo, 1):
        for attempt in range(4):
            try:
                found += bool(examples.examples_for(code))
                break
            except examples.SearchError:
                time.sleep(10 * (attempt + 1))
        time.sleep(2.2)  # Stay under the local 30 searches/minute limit.
        if number % 25 == 0 or number == len(todo):
            print(f'{number}/{len(todo)} searched, {found} with real products', flush=True)
