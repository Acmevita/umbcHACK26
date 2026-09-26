# Better Basket — barcode scanner

A mobile-friendly HTML/CSS/JavaScript scanner with a Python standard-library backend. No package installation required. ZXing Browser 0.1.5 loads from jsDelivr; camera frames are decoded locally and never uploaded. Barcode Lookup product identification requires your own API key. Only barcode numbers are sent to that provider; product images load from the image URLs it supplies.

## Run on your Mac

```sh
cd /Volumes/Coding/Projects/UMBC2026HACK
cp .env.example .env
# Edit .env locally and replace your_api_key_here with your Barcode Lookup API key.
python3 server.py
```

Open http://localhost:8001, click **Start camera**, and allow access. The older port 8000 static server does not support lookup. Once permission is granted, the camera picker lists available devices, including an iPhone connected through Continuity Camera. Enable it on iPhone in Settings → General → AirPlay & Continuity → Continuity Camera; keep the phone locked and near your Mac, or connect by USB and trust the Mac.

Get a key from your Barcode Lookup account: https://www.barcodelookup.com/api . Access and quota depend on your plan. Do not put the key in `app.js`, HTML, or a chat message. `.env` is ignored by Git and cannot be served by `server.py`. An environment variable named `BARCODE_LOOKUP_API_KEY` also works and takes precedence. If `.env` already exists, edit it instead of overwriting it. The server reads the key on each lookup, so saving it then pressing **Retry lookup** is sufficient.

Scan or manually enter `082657500638` to test your example. The result card displays the returned title, brand, image, and description, with a link to the provider. Missing products, unavailable credentials, quota errors, and network errors are shown explicitly; no fallback product data is invented.

## Run directly on a phone

Use an HTTPS development tunnel to this Python server, then open that HTTPS URL in Safari/Chrome. A static host alone cannot run the product lookup backend. A plain `http://192.168...` LAN address will not enable camera access on the phone. `localhost` on a phone refers to the phone, not your Mac. This project does not deploy or create a public tunnel automatically. This is a local development server; add authentication and per-user request limits before public deployment so strangers cannot spend your API quota.

The scanner prefers the rear camera. Allow permission, use even lighting, and keep the complete barcode in view. Stop releases camera tracks; switching cameras restarts capture. Moving the page into the background stops capture too.

## Supported behavior

- Camera: UPC-A, EAN-8, EAN-13. Compressed UPC-E is intentionally excluded.
- Manual entry: checksum-validated GTIN-8/12/13/14. Example: `012345678905`.
- Repeated products are deduplicated, including equivalent UPC-A / zero-prefixed EAN-13 codes.
- History lives in memory until reload. Copy one barcode or export the list as JSON.
- Denied permissions, insecure origins, missing cameras, and decoder loading failures have visible recovery messages.
- Product identification uses `GET /api/products?barcode=...` on our server, which calls Barcode Lookup v3 with the server-held key. Successful results are cached in memory for one hour (up to 256 products), including equivalent UPC/EAN codes.
- Nutrition comparison and AI recommendations are not implemented yet.

## Integration

`app.js` dispatches an event for each new validated product:

```js
window.addEventListener('barcode:scanned', ({ detail }) => {
  // detail: { barcode, gtin, source, scannedAt }
  // Hook additional behavior into a new scan here.
});
```

Barcodes stay strings to retain leading zeroes. `gtin` is the zero-padded 14-digit identity used for deduplication. A valid checksum does not establish that a product exists in a database. Retain the scanned barcode for API lookups.

The frontend automatically looks up each new scan. Results enrich history and exported JSON. An older request cannot overwrite the latest scan or restore cleared history. Retry failed requests explicitly with **Retry lookup**. The backend only serves the three frontend assets and the API route, never arbitrary project files.

Run backend tests (mock provider responses; no credits used): `python3 -m unittest -v test_server.py`.
API reference: https://www.barcodelookup.com/api-documentation

## Manual verification

1. Add `012345678905`; confirm one result and one history entry.
2. Add `0012345678905`; confirm it does not create a duplicate.
3. Add `012345678906`; confirm a validation error.
4. Export JSON; confirm barcode strings and timestamps. Clear history.
5. Allow camera permission and scan a real supported product barcode.
6. Switch cameras, stop, and background the page; confirm the camera indicator turns off.
7. Deny permission; confirm the recovery message and usable manual entry.

Decoder documentation: https://github.com/zxing-js/browser
