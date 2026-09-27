# Environmental backend: first implementation

Implemented: local factor catalog, free Open Food Facts product reads, conservative category-tag matching, package quantity parsing, deterministic calculation, and an independent HTTP endpoint. No frontend files changed. The existing `/api/products` route still uses Barcode Lookup.

## Try it

Restart the app server:

```sh
.venv/bin/python server.py
```

Open these URLs or call them from the UI branch:

- `http://localhost:8001/api/impact?barcode=3259011027958` — a plain-tofu example present in Open Food Facts when tested.
- `http://localhost:8001/api/impact?barcode=082657500638` — the water example; identification succeeds but the environmental category is unsupported.

```js
const response = await fetch(`/api/impact?barcode=${encodeURIComponent(barcode)}`);
const result = await response.json();
// Check response.ok first, then result.impact.status.
```

No new API key is required for this endpoint. Environmental factors are loaded once from disk; no research or factor downloads occur during scans. OFF requests have a six-second socket timeout, response-size limit, in-memory cache, duplicate-request coalescing, and a local 15-read-per-minute limit. The HTTP handler has a 15-second processing deadline, including worker-queue time, after which it returns 504. It does not promise an end-to-end browser/network latency or a successful lookup for every product. OFF failures have a short cache to avoid repeated failed calls; retry after 30 seconds.

For an eventual deployment, set `OPEN_FOOD_FACTS_USER_AGENT` as a process environment variable using your app name/version and real contact URL/email. The adapter supplies a descriptive prototype User-Agent by default. This variable is not read from `.env`; the existing API-key loader only loads the Barcode Lookup key.

## AGRIBALYSE tier (added after the first slice)

`/api/impact` now tries AGRIBALYSE first and falls back to the Poore & Nemecek tag mapping below only when Open Food Facts gives no usable AGRIBALYSE code.

- **Code:** Open Food Facts assigns many products an AGRIBALYSE food code (`agribalyse_food_code`) or a category proxy (`agribalyse_proxy_food_code`). Proxies are labeled in the response (`classification.method = off_agribalyse_proxy`) and in the UI. Example: Red Bull Zero uses "Energy drink, with sugar".
- **Factor:** the kg CO₂e/kg comes from our local `data/agribalyse.json` (AGRIBALYSE v3.2 synthesis, 2451 foods, ADEME, Licence Ouverte / Open Licence 2.0), not from OFF. OFF's stage split (agriculture, processing, packaging, transport, distribution, consumption) is shown only when it sums to the same total.
- **Boundary and geography differ from Poore & Nemecek:** farm to plate (includes home preparation) and French-market averages. Each response carries its own `source`, so don't compare numbers across the two sources as equals.
- **Drinks:** AGRIBALYSE values are per kg. For beverage and milk subgroups, volume is converted at an assumed 1 kg/L, recorded in `assumptions`.
- **Quantity fallback:** when the label text can't be parsed, OFF's normalized `product_quantity` (g or ml) is used and noted in `assumptions`.
- **DQR:** each AGRIBALYSE row's data-quality rating (1 best, 5 worst) is returned as `factor.dqr`. It is not a confidence interval.

Rebuild offline: `.venv/bin/python import_agribalyse.py --csv data/source/agribalyse-3.2-synthese.csv`. Source: https://data.ademe.fr/datasets/agribalyse-31-synthese (the dataset id says 3.1; its content is v3.2).

Live checks on 2026-09-26 (single lookups, not coverage statistics): Red Bull Zero 250 mL → 0.171; Cheerios 510 g → 1.73; tortilla chips 28.3 g → 0.066; Deer Park water 16.9 fl oz → 0.160; plain tofu 500 g → 0.5 kg CO₂e per package.

## Lower-carbon swaps (`/api/alternatives`)

`GET /api/alternatives?code=<AGRIBALYSE code>&mass_kg=<optional>`. The frontend calls it automatically after an AGRIBALYSE-based estimate.

1. **Candidates (code):** foods in the same AGRIBALYSE subgroup whose kg CO₂e/kg is at least 20% lower.
2. **Selection (Gemini):** the model picks up to 3 candidates a shopper would realistically buy instead, and gives a one-sentence reason. It uses structured JSON output and sees only the candidate list.
3. **Validation (code):** picks whose code wasn't offered, duplicates and malformed items are discarded.
4. **Numbers (code):** percent reduction and kg saved per package come from AGRIBALYSE and the package mass. The model supplies no numbers.

**Speed:** results are keyed by AGRIBALYSE code, not barcode, and precomputed into `data/substitutes.json`, so scans don't wait on Gemini. A code missing from the file triggers one live call (~5 s) whose result is saved. Foods with no lower-carbon candidates skip the model.

**Precompute:** `.venv/bin/python precompute_substitutes.py`. It's resumable and batches ~15 same-subgroup foods per request, so ~2,100 foods take ~170 requests. This fits the free tier's 15 requests/minute; rate limits are retried automatically.

**Real product examples:** each swap food gets up to 3 US products from Open Food Facts' search service (search.openfoodfacts.org), sorted by scan popularity. The OFF category taxonomy (`data/off_categories.json`, built by `import_off_categories.py`) links AGRIBALYSE codes to OFF categories. A product is kept only if its own categories resolve to that same AGRIBALYSE food and to no other. Results are cached in `data/swap_examples.json`; fill it with `.venv/bin/python precompute_examples.py` (~30 searches/minute). A food with no qualifying US product falls back to its category name.

**Config:** `GEMINI_API_KEY` in `.env` (or the environment). `GEMINI_MODEL` defaults to `gemini-3.5-flash-lite`.

**Limits:** suggestions are food categories, not specific US products or brands. Many AGRIBALYSE rows share a proxy value, so several suggestions can show the same footprint. Substitutability judgments have not been evaluated yet. Next step: hand-review a random sample and report the share judged realistic.

## Response contract

`schema_version`, `barcode`, `processing_ms`, `open_food_facts`, and `impact` are top-level fields.

| `impact.status` | Meaning | UI treatment |
| --- | --- | --- |
| `estimated` | Mapped category and explicitly stated package mass | Show approximate package CO₂e, global-category label, and formula. |
| `category_only` | Category matched but mass unavailable or ambiguous | Show the reference factor per kg; do not show a package total. |
| `insufficient_data` | Unsupported/conflicting category or unavailable product facts | Show the reason; never interpret null as zero. |

For `estimated`, `impact.calculation` records the package mass, factor, operation, and output unit. `factor` identifies the specific catalog row. `source` records geography, version, boundary, citation, exclusions, and source-file hash. `classification` records matched tags and method. Its validation field explicitly says accuracy has not been measured. Method names are not confidence probabilities.

Display `Global; not US-specific` prominently. These are food-category proxies for US users who choose to use global estimates, not measurements of a US brand or its supply chain. `countries_tags` describes OFF product-market metadata, not where the food was produced.

## Data, attribution, and reproducibility

The imported OWID indicator has **38 entries**, not 43. Values are global means in kg CO₂e per kg of food, from Poore and Nemecek (2018), processed by Our World in Data. Dataset version: `2019-10-08`; retrieved 2026-09-26. The source time marker is 2010, not a new measurement date. The source boundary is cradle to retail; post-purchase activities are excluded. This table does not contain numerical producer ranges or a stage breakdown, so neither is invented.

- Chart and attribution: https://ourworldindata.org/grapher/ghg-per-kg-poore
- Method and boundary: https://ourworldindata.org/faqs-environmental-impacts-food
- Indicator metadata: https://api.ourworldindata.org/v1/indicators/1206176.metadata.json
- OFF API: https://openfoodfacts.github.io/openfoodfacts-server/api/
- OFF reuse: https://support.openfoodfacts.org/help/en-gb/12-donnees-api/94-y-a-t-il-des-conditions-pour-utiliser-l-api

Original CSV and full indicator metadata are retained in `data/source/`. Rebuild deterministically without network calls:

```sh
.venv/bin/python import_factors.py \
  --csv data/source/ghg-per-kg-poore.csv \
  --metadata data/source/ghg-per-kg-poore.metadata.json
```

The importer checks indicator identity, redistribution flag, reviewed row count, finite factors, and source year. A changed source requires review. OWID labels its own work CC BY and the metadata marks this indicator `nonRedistributable: false`; third-party source terms still apply. The catalog preserves attribution and that notice. Original supplementary producer-level files are not imported, and no blanket license claim is made about them.

OFF data carries ODbL attribution in every response. Its records/cache remain separate from commercial Barcode Lookup records. This separation is for provenance; it does not waive ODbL duties for any derived/combined database you distribute. No combined database or product dump is published by this change.

## Deliberate limits

- Only explicitly mapped OFF categories are supported, not every barcode. Generic beef does not identify herd type. Soy milk does not stand in for other plant milks. Bottled water has no entry in this table.
- A blacklist rejects recognizable compound/processed forms, but this is a heuristic, not a validated universal food classifier. Parent tags alone can be misleading. Add reviewed rules and evaluations before claiming broad coverage.
- Size parsing supports common mass/volume units, explicit `N x size` multipacks, and consistent dual-unit labels. Ambiguous formats are rejected.
- US fluid ounces remain volume. No density, cooking yield, or item weight is guessed. A dozen eggs or a missing quantity does not silently become a default serving.
- The package calculation assumes its declared net mass corresponds to the selected food reference. No peel, drained-mass, or raw/cooked conversion is inferred.
- No Gemini fallback, title-based positive classifier, recipe decomposition, alternatives, optimizer, or frontend panel is implemented in this slice.
- No per-protein comparison is possible without compatible nutrition data. It is one comparison basis, not a universal definition of interchangeability.
- Producer variation is not a confidence interval for a brand. If ranges are later imported, retain their precise definitions; overlapping ranges alone are not a statistical test of means.

## Tests and measured samples

```sh
.venv/bin/python -m unittest -v test_server.py test_impact.py
.venv/bin/python smoke_impact.py
.venv/bin/python smoke_impact.py --live
.venv/bin/python smoke_impact.py --live --barcode 3259011027958
```

The default smoke test uses synthetic product facts and a real category factor. `--live` makes one free OFF read and then verifies the cache through a second local HTTP request; no Barcode Lookup credits are used.

Live results on 2026-09-26:

| Barcode | Result | First request | Cached request |
| --- | --- | --- | --- |
| `082657500638` | Water identified; no supported category | 583.42 ms | 1.40 ms |
| `3259011027958` | TOFOU Nature, 500 g; global tofu proxy: 0.5 × 3.16 = 1.58 kg CO₂e/package | 378.80 ms | 0.59 ms |

These are individual HTTP integration measurements, not p50/p95 statistics, population coverage, or proof of brand-level footprint accuracy. The tofu product is a technical integration example, not evidence of US product availability.

## Next slice

Develop a held-out evaluation set with independently reviewed category mappings, explicit unknown cases, and no label leakage. Split related product variants together so nearly identical products do not cross train/test boundaries. OFF community labels are candidate annotations, not unquestionable ground truth. Then add constrained classifier fallbacks and measure coverage, wrong assignments, abstention, and cold/warm latency before building comparisons.
