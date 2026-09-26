# Backend testing

Run these commands from `UMBC2026HACK`. The backend uses only Python's standard library; no pip packages are needed.

```sh
# Already created for this project. Run only if .venv does not exist:
python3 -m venv --without-pip .venv

# Isolated unit tests: no API calls or credits used.
.venv/bin/python -m unittest -v test_server.py

# Temporary localhost server + simulated provider response.
.venv/bin/python smoke_test.py

# Temporary localhost server + one real Barcode Lookup API request.
.venv/bin/python smoke_test.py --live

# Run the app backend normally:
.venv/bin/python server.py
```

Open http://localhost:8001 for the app. Tests use an automatically assigned port and shut down their own server afterward, so they do not interfere with the app server.

The live test requires `BARCODE_LOOKUP_API_KEY=...` in the project's `.env` file or in the process environment. The test reports only the key source, never its value. Each live run makes one provider request for `082657500638`; a second equivalent EAN request must use the cache. This can consume one request from your provider quota.

## VS Code environment-file notification

`python.terminal.useEnvFile` controls whether VS Code injects an environment file into terminals. Our `server.api_key()` reads the project `.env` file itself, so enabling that setting is unnecessary for this backend. A process environment variable takes precedence over the file. If an old injected key is present in a terminal, unset that variable before using the updated `.env` value.

Using `.venv/bin/python` explicitly runs inside the virtual environment without activating the terminal. Optional activation on macOS/zsh: `source .venv/bin/activate`.

## Verified 2026-09-26

- Nine unit tests passed, including `.env` loading without terminal injection and environment-variable precedence.
- Simulated HTTP test passed: product response, equivalent UPC/EAN caching, invalid barcode rejection, and private-file blocking.
- Live HTTP test passed: `082657500638` returned **Deer Park Water**, brand **Deer Park**, with HTTP 200. The key came from `.env`.
- No UI files changed for these tests.
