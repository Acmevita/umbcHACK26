/* Camera frames stay local. Only the barcode is sent to our product lookup backend. */
const $ = (id) => document.getElementById(id);
const video = $('video');
const scans = [];
let stream = null;
let controls = null;
let generation = 0;
let phase = 'idle';
let latest = null;

function status(message, error = false) {
  $('status').textContent = message;
  $('status').classList.toggle('error', error);
}

function setPhase(value) {
  phase = value;
  $('start').disabled = value !== 'idle';
  $('stop').disabled = value === 'idle';
  $('camera').disabled = value === 'starting';
  $('state').textContent = {idle: 'Camera off', starting: 'Connecting…', live: 'Camera live'}[value];
  $('placeholder').hidden = value === 'live';
}

function stopCamera(message = 'Camera stopped. Your scans are still here.') {
  generation++;
  controls?.stop();
  controls = null;
  stream?.getTracks().forEach((track) => track.stop());
  stream = null;
  video.srcObject = null;
  setPhase('idle');
  status(message);
}

// GTIN check digit validation; preserve leading zeroes as strings.
function validBarcode(code) {
  if (!/^(\d{8}|\d{12}|\d{13}|\d{14})$/.test(code)) return false;
  let sum = 0;
  for (let i = code.length - 2, weight = 3; i >= 0; i--, weight = 4 - weight) {
    sum += Number(code[i]) * weight;
  }
  return (10 - sum % 10) % 10 === Number(code.at(-1));
}

function recordBarcode(code, source) {
  if (!validBarcode(code)) return false;
  // A zero-prefixed EAN-13 and its UPC-A equivalent represent one item.
  const gtin = code.padStart(14, '0');
  if (scans.some((scan) => scan.gtin === gtin)) {
    status('Already in your scans. Try another product.');
    return true;
  }
  latest = {barcode: code, gtin, source, scannedAt: new Date().toISOString()};
  scans.unshift(latest);
  $('result-title').textContent = 'Barcode captured.';
  $('result-description').textContent = 'Looking up this product…';
  $('result-code').textContent = code;
  $('result-code').hidden = false;
  $('copy').hidden = false;
  $('copy').textContent = 'Copy barcode';
  renderHistory();
  status('Barcode captured. Scan another product whenever you’re ready.');
  // Integration point for the product database in the next phase.
  window.dispatchEvent(new CustomEvent('barcode:scanned', {detail: {...latest}}));
  lookupProduct(latest);
  lookupImpact(latest);
  return true;
}

function renderProduct(scan) {
  if (latest !== scan) return;
  const product = scan.product;
  $('result-title').textContent = product ? product.title : 'Barcode captured.';
  $('result-description').textContent = product
    ? [product.brand, product.size, product.description].filter(Boolean).join(' · ') || 'Product identified by Barcode Lookup.'
    : scan.lookupError || 'Looking up this product…';
  $('product-image').hidden = true;
  $('product-image').removeAttribute('src');
  if (product?.image) {
    $('product-image').src = product.image;
    $('product-image').alt = product.title;
    $('product-image').hidden = false;
  }
  $('product-source').hidden = !product;
  if (product) $('product-source').href = product.sourceUrl;
  $('retry-lookup').hidden = !scan.lookupError;
  $('lookup-status').textContent = product ? 'Product found' : scan.lookupError ? 'Lookup unavailable' : 'Searching Barcode Lookup…';
}

async function lookupProduct(scan) {
  if (scan.lookupPending) return;
  scan.lookupPending = true;
  scan.lookupError = '';
  renderProduct(scan);
  const abort = new AbortController();
  const timeout = setTimeout(() => abort.abort(), 20000);
  try {
    const response = await fetch(`/api/products?barcode=${encodeURIComponent(scan.barcode)}`, {signal: abort.signal});
    if (!response.headers.get('content-type')?.includes('application/json')) {
      throw new Error('Start the app with python3 server.py and open localhost:8001 to enable product lookup.');
    }
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Product lookup failed. Please retry.');
    if (!data.product?.title) throw new Error('The lookup returned no product details.');
    scan.product = data.product;
  } catch (error) {
    scan.lookupError = error.name === 'AbortError' ? 'Product lookup timed out. Please retry.'
      : error instanceof TypeError ? 'Could not reach the lookup server. Check your connection and retry.' : error.message;
  } finally {
    clearTimeout(timeout);
    scan.lookupPending = false;
    if (scans.includes(scan)) { renderProduct(scan); renderHistory(); }
  }
}

// Plain-language text for the backend's abstention reasons; never show null as zero.
const IMPACT_REASONS = {
  no_supported_category: 'This food type isn’t in our emissions table yet.',
  conflicting_categories: 'This product matched more than one food category, so we won’t guess.',
  processed_or_composite_food_needs_its_own_factor: 'Processed or mixed foods need their own factor. A raw-ingredient average would mislead.',
  open_food_facts_not_found: 'Open Food Facts has no record of this barcode.',
  open_food_facts_local_rate_limit: 'Too many lookups in the last minute. Retry shortly.',
  open_food_facts_rate_limited: 'Open Food Facts is rate-limiting requests. Retry shortly.',
  missing_package_quantity: 'The package size is not listed, so only the per-kg figure is shown.',
  unsupported_quantity_format: 'The package size couldn’t be read, so only the per-kg figure is shown.',
  ambiguous_multipack_quantity: 'The multipack size is ambiguous, so only the per-kg figure is shown.',
  invalid_quantity: 'The listed package size is invalid, so only the per-kg figure is shown.',
  conflicting_quantity_labels: 'The package lists conflicting sizes, so only the per-kg figure is shown.',
  density_required_for_mass_factor: 'The size is a volume. We don’t guess density, so only the per-kg figure is shown.',
};
const kg = (value) => `${Number(value.toFixed(value < 1 ? 3 : 2))}`;

function impactReason(reason) {
  return IMPACT_REASONS[reason] || (reason?.startsWith('open_food_facts_')
    ? 'Open Food Facts is unavailable right now. Retry shortly.' : 'Not enough data for an estimate.');
}

function renderImpact(scan) {
  if (latest !== scan) return;
  const card = $('impact-card');
  card.hidden = !scan;
  if (!scan) return;
  const data = scan.impact;
  const impact = data?.impact;
  const offProduct = data?.open_food_facts?.product;
  const transient = impact => impact?.reason?.startsWith('open_food_facts_') && impact.reason !== 'open_food_facts_not_found';
  $('retry-impact').hidden = !(scan.impactError || transient(data?.impact));
  $('impact-number').textContent = $('impact-unit').textContent = '';
  $('impact-geo').hidden = $('impact-details').hidden = true;
  if (!impact) {
    $('impact-state').textContent = scan.impactError ? 'Unavailable' : 'Estimating…';
    $('impact-summary').textContent = scan.impactError || 'Matching this product to a food category…';
    return;
  }
  const factor = impact.factor;
  if (impact.status === 'estimated') {
    $('impact-state').textContent = 'Estimated';
    $('impact-number').textContent = kg(impact.per_package_kg_co2e);
    $('impact-unit').textContent = 'kg CO₂e per package';
    $('impact-summary').textContent = `Based on the average for ${factor.label.toLowerCase()}: ${kg(impact.per_kg_food_kg_co2e)} kg CO₂e per kg.`;
  } else if (impact.status === 'category_only') {
    $('impact-state').textContent = 'Per kg only';
    $('impact-number').textContent = kg(impact.per_kg_food_kg_co2e);
    $('impact-unit').textContent = `kg CO₂e per kg of ${factor.label.toLowerCase()}`;
    $('impact-summary').textContent = impactReason(impact.reason);
  } else {
    $('impact-state').textContent = 'No estimate';
    $('impact-summary').textContent = impactReason(impact.reason);
  }
  renderAlternatives(scan);
  if (!factor) return;
  $('impact-geo').hidden = $('impact-details').hidden = false;
  const agribalyse = factor.source_id === 'agribalyse_3_2';
  const proxy = impact.classification.method === 'off_agribalyse_proxy';
  $('impact-geo').textContent = `${agribalyse ? 'AGRIBALYSE' : 'Global'} category average${proxy ? ' · closest-match proxy' : ''} · ${impact.geography.split(';')[0]} data, not US-specific`;
  const calc = impact.calculation;
  const source = impact.source;
  const rows = [
    ['Product', offProduct ? `${offProduct.title}${offProduct.quantity ? ' · ' + offProduct.quantity : ''}` : '—'],
    ['Category', agribalyse ? `${factor.label} (AGRIBALYSE ${factor.agribalyse_code}, ${proxy ? 'category proxy' : 'matched'} by Open Food Facts)`
      : `${factor.label} (matched tag ${impact.classification.matched_tags.join(', ')})`],
    ['Formula', calc ? `${calc.mass_kg} kg × ${calc.factor_kg_co2e_per_kg} kg CO₂e/kg = ${kg(impact.per_package_kg_co2e)} kg CO₂e` : 'Package mass unknown; no total calculated'],
    ['Stages', impact.stages ? Object.entries(impact.stages.per_package || impact.stages.per_kg)
      .map(([stage, value]) => `${stage === 'transportation' ? 'transport' : stage} ${kg(value)}`).join(' · ')
      + (impact.stages.per_package ? ' kg CO₂e' : ' kg CO₂e per kg') : 'No stage breakdown for this product'],
    ['Boundary', source.boundary_label || `Farm to retail shelf. Excludes ${source.excluded.map((item) => item.replace(/_/g, ' ')).join(', ')}.`],
    ['Source', source.citation + (factor.dqr ? ` Data quality rating ${factor.dqr.toFixed(1)} (1 = best, 5 = worst).` : '')],
    ['Caveats', [impact.classification.validation, ...impact.assumptions].filter(Boolean).join(' ')],
  ];
  $('impact-evidence').replaceChildren(...rows.flatMap(([label, text]) => {
    const dt = document.createElement('dt');
    const dd = document.createElement('dd');
    dt.textContent = label;
    dd.textContent = text;
    return [dt, dd];
  }));
  const links = document.createElement('dd');
  links.append(...[[offProduct?.source_url, 'Open Food Facts record'], [source.url, 'Emissions dataset']]
    .filter(([href]) => href).flatMap(([href, text], index) => {
      const link = document.createElement('a');
      link.href = href; link.target = '_blank'; link.rel = 'noopener noreferrer';
      link.textContent = text + ' ↗';
      return index ? [' · ', link] : [link];
    }));
  const linksLabel = document.createElement('dt');
  linksLabel.textContent = 'Links';
  $('impact-evidence').append(linksLabel, links);
}

const ALTERNATIVE_REASONS = {
  no_lower_candidates: 'Already among the lowest-carbon options in its category.',
  no_realistic_substitute: 'No realistic lower-carbon swap in its category.',
  gemini_missing_api_key: 'Add GEMINI_API_KEY to the server’s .env file to enable swaps.',
  gemini_rate_limited: 'The AI service is busy. Try again shortly.',
};

function renderAlternatives(scan) {
  const box = $('alternatives');
  const code = scan.impact?.impact?.factor?.agribalyse_code;
  box.hidden = !code;
  if (!code) return;
  const data = scan.alternatives;
  const items = data?.alternatives || [];
  $('alternatives-note').hidden = !items.length;
  $('alternatives-status').hidden = items.length > 0;
  $('alternatives-status').textContent = scan.alternativesError
    || (!data ? 'Finding lower-carbon swaps…' : ALTERNATIVE_REASONS[data.reason] || 'Swaps are unavailable right now.');
  $('alternatives-list').replaceChildren(...items.map((item) => {
    const row = document.createElement('li');
    const products = document.createElement('div');
    products.className = 'swap-products';
    (item.examples || []).forEach((example) => {
      const link = document.createElement('a');
      link.className = 'swap-product';
      link.href = example.url; link.target = '_blank'; link.rel = 'noopener noreferrer';
      if (example.image) {
        const image = document.createElement('img');
        image.src = example.image; image.alt = ''; image.loading = 'lazy'; image.referrerPolicy = 'no-referrer';
        image.addEventListener('error', () => image.remove());
        link.append(image);
      }
      const text = document.createElement('span');
      const brand = document.createElement('b');
      // Show the full name when it already starts with the brand ("Mountain Dew Maui Burst").
      const branded = example.name.toLowerCase().startsWith(example.brand.toLowerCase());
      brand.textContent = branded ? example.name : example.brand;
      text.append(brand, branded ? '' : ' ' + example.name, example.quantity ? ` · ${example.quantity}` : '');
      link.append(text);
      products.append(link);
    });
    const category = document.createElement(item.examples?.length ? 'small' : 'strong');
    category.className = item.examples?.length ? 'swap-category' : '';
    category.textContent = item.examples?.length ? `Food type: ${item.label}` : item.label;
    const numbers = document.createElement('span');
    numbers.className = 'alternative-numbers';
    numbers.textContent = `${item.reduction_percent}% lower · ${kg(item.kg_co2e_per_kg)} kg CO₂e/kg`
      + (item.kg_co2e_saved_per_package ? ` · saves ~${kg(item.kg_co2e_saved_per_package)} kg per package` : '');
    const reason = document.createElement('small');
    reason.textContent = (item.fit === 'same_use' ? 'Same use. ' : 'Similar use. ') + item.reason;
    row.append(...(item.examples?.length ? [products, category] : [category]), numbers, reason);
    return row;
  }));
}

async function lookupAlternatives(scan) {
  const impact = scan.impact?.impact;
  const code = impact?.factor?.agribalyse_code;
  if (!code || scan.alternativesPending) return;
  scan.alternativesPending = true;
  scan.alternativesError = '';
  scan.alternatives = null;
  const mass = impact.calculation?.mass_kg;
  try {
    const response = await fetch(`/api/alternatives?code=${encodeURIComponent(code)}${mass ? '&mass_kg=' + mass : ''}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Swaps are unavailable right now.');
    scan.alternatives = data;
  } catch (error) {
    scan.alternativesError = error instanceof TypeError ? 'Could not reach the lookup server.' : error.message;
  } finally {
    scan.alternativesPending = false;
    if (latest === scan) renderAlternatives(scan);
  }
}

async function lookupImpact(scan) {
  if (scan.impactPending) return;
  scan.impactPending = true;
  scan.impactError = '';
  renderImpact(scan);
  const abort = new AbortController();
  const timeout = setTimeout(() => abort.abort(), 20000);
  try {
    const response = await fetch(`/api/impact?barcode=${encodeURIComponent(scan.barcode)}`, {signal: abort.signal});
    if (!response.headers.get('content-type')?.includes('application/json')) {
      throw new Error('Start the app with python3 server.py and open localhost:8001 to enable footprint estimates.');
    }
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Footprint lookup failed. Please retry.');
    if (!data.impact?.status) throw new Error('The footprint lookup returned no estimate.');
    scan.impact = data;
    lookupAlternatives(scan);
  } catch (error) {
    scan.impactError = error.name === 'AbortError' ? 'Footprint lookup timed out. Please retry.'
      : error instanceof TypeError ? 'Could not reach the lookup server. Check your connection and retry.' : error.message;
  } finally {
    clearTimeout(timeout);
    scan.impactPending = false;
    if (scans.includes(scan)) { renderImpact(scan); renderHistory(); }
  }
}

function renderHistory() {
  $('count').textContent = scans.length;
  $('empty').hidden = scans.length > 0;
  $('clear').disabled = $('export').disabled = scans.length === 0;
  $('history').replaceChildren(...scans.map((scan, index) => {
    const row = document.createElement('li');
    const number = document.createElement('span');
    number.className = 'scan-number';
    number.textContent = String(scans.length - index).padStart(2, '0');
    const content = document.createElement('div');
    const code = document.createElement('strong');
    code.textContent = scan.product?.title || scan.barcode;
    const meta = document.createElement('small');
    const footprint = scan.impact?.impact?.status === 'estimated' ? `${kg(scan.impact.impact.per_package_kg_co2e)} kg CO₂e · ` : '';
    meta.textContent = `${footprint}${scan.product ? scan.barcode + ' · ' : ''}${scan.source === 'camera' ? 'Camera' : 'Manual entry'} · ${new Date(scan.scannedAt).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'})}`;
    content.append(code, meta);
    row.append(number, content);
    return row;
  }));
}

async function refreshCameras(activeId) {
  const devices = await navigator.mediaDevices.enumerateDevices();
  const select = $('camera');
  select.replaceChildren(new Option('Automatic · prefer rear camera', ''));
  devices.filter((device) => device.kind === 'videoinput').forEach((device, index) => {
    select.add(new Option(device.label || `Camera ${index + 1}`, device.deviceId));
  });
  if ([...select.options].some((option) => option.value === activeId)) select.value = activeId;
}

async function startCamera() {
  if (phase !== 'idle') return;
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
    status('Camera access needs HTTPS or localhost. On a phone, open an HTTPS version of this page. Manual entry still works.', true);
    return;
  }
  if (!window.ZXingBrowser) {
    status('The barcode library did not load. Check your internet connection and reload, or enter a barcode manually.', true);
    return;
  }
  const token = ++generation;
  setPhase('starting');
  status('Allow camera access when your browser asks.');
  let acquired = null;
  try {
    const deviceId = $('camera').value;
    acquired = await navigator.mediaDevices.getUserMedia({audio: false, video: {
      ...(deviceId ? {deviceId: {exact: deviceId}} : {facingMode: {ideal: 'environment'}}),
      width: {ideal: 1280}, height: {ideal: 720},
    }});
    if (token !== generation) { acquired.getTracks().forEach((track) => track.stop()); return; }
    stream = acquired;
    // MultiFormatOneDReader avoids relying on the browser's BarcodeDetector API.
    const reader = new ZXingBrowser.BrowserMultiFormatOneDReader();
    const nextControls = await reader.decodeFromStream(acquired, video, (result) => {
      if (token !== generation || !result) return;
      // ZXing enum values: EAN_8=6, EAN_13=7, UPC_A=14.
      // UPC-E is deliberately excluded: its compressed digits need expansion.
      if ([6, 7, 14].includes(result.getBarcodeFormat())) recordBarcode(result.getText(), 'camera');
    });
    if (token !== generation) { nextControls.stop(); return; }
    controls = nextControls;
    setPhase('live');
    status('Point the camera at a UPC-A, EAN-8, or EAN-13 barcode.');
    acquired.getVideoTracks()[0].addEventListener('ended', () => {
      if (token === generation) stopCamera('Camera disconnected. Reconnect it and start again.');
    });
    try { await refreshCameras(acquired.getVideoTracks()[0].getSettings().deviceId); } catch { /* scanning can continue without a device list */ }
  } catch (error) {
    acquired?.getTracks().forEach((track) => track.stop());
    if (token !== generation) return;
    stopCamera();
    const messages = {
      NotAllowedError: 'Camera permission was denied. Allow camera access in your browser settings, then try again.',
      NotFoundError: 'No camera found. Connect a webcam or enable iPhone Continuity Camera.',
      NotReadableError: 'The camera could not start. Close other apps using it, then try again.',
      OverconstrainedError: 'That camera is unavailable. Select Automatic and try again.',
    };
    status(messages[error.name] || 'Could not start the camera. Try another camera or enter the barcode manually.', true);
  }
}

$('start').addEventListener('click', startCamera);
$('stop').addEventListener('click', () => stopCamera());
$('camera').addEventListener('change', () => { if (phase === 'live') { stopCamera(); startCamera(); } });
$('manual-form').addEventListener('submit', (event) => {
  event.preventDefault();
  const code = $('manual').value.replace(/[\s-]/g, '');
  if (!recordBarcode(code, 'manual')) {
    $('manual-error').textContent = 'Enter a valid 8, 12, 13, or 14-digit GTIN, including its check digit. Compressed UPC-E is not supported.';
    return;
  }
  $('manual-error').textContent = '';
  $('manual').value = '';
});
$('copy').addEventListener('click', async () => {
  try { await navigator.clipboard.writeText(latest.barcode); $('copy').textContent = 'Copied!'; }
  catch { status('Copy is unavailable. Select the barcode text and copy it manually.', true); }
});
$('clear').addEventListener('click', () => {
  scans.length = 0; latest = null; renderHistory();
  $('result-title').textContent = 'Your first find is up next.';
  $('result-description').textContent = 'Scan a packaged product to capture its barcode.';
  $('result-code').hidden = $('copy').hidden = true;
  $('product-image').hidden = $('product-source').hidden = $('retry-lookup').hidden = true;
  $('product-image').removeAttribute('src');
  $('lookup-status').textContent = '';
  $('impact-card').hidden = true;
  status('Scan history cleared.');
});
$('retry-lookup').addEventListener('click', () => { if (latest) lookupProduct(latest); });
$('retry-impact').addEventListener('click', () => { if (latest) lookupImpact(latest); });
$('product-image').addEventListener('error', () => { $('product-image').hidden = true; });
$('export').addEventListener('click', () => {
  const url = URL.createObjectURL(new Blob([JSON.stringify({schemaVersion: 1, scans}, null, 2)], {type: 'application/json'}));
  const link = document.createElement('a');
  link.href = url; link.download = 'better-basket-scans.json'; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
document.addEventListener('visibilitychange', () => { if (document.hidden && phase !== 'idle') stopCamera('Camera paused while the app is in the background. Tap Start camera to resume.'); });
window.addEventListener('pagehide', () => stopCamera());
if (!window.isSecureContext) status('Use HTTPS on your phone to enable the camera. Manual entry is available here.', true);
