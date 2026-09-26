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
    meta.textContent = `${scan.product ? scan.barcode + ' · ' : ''}${scan.source === 'camera' ? 'Camera' : 'Manual entry'} · ${new Date(scan.scannedAt).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'})}`;
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
  status('Scan history cleared.');
});
$('retry-lookup').addEventListener('click', () => { if (latest) lookupProduct(latest); });
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
