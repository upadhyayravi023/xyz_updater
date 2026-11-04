# OpenTxt licensed desktop app

This transparent desktop client and separate service ask before screen capture, show AI responses in the app, and do not install background keyboard hooks, simulate typing/clicking, or request administrator privileges.

## License service

Requires Python 3.10+. Install `license_server/requirements.txt`, set `MONGODB_URI`, `MONGODB_DB`, a unique `ADMIN_USER`, a strong `ADMIN_PASSWORD`, and a random `LICENSE_PEPPER` of at least 32 characters. Add at least one provider API key. Keep these server secrets private. MongoDB Atlas or another managed MongoDB service can host the database. The built-in server binds to localhost by default. For internet use, deploy behind a maintained HTTPS reverse proxy or production WSGI/ASGI gateway, set `HOST` appropriately, firewall the service, and use HTTPS. Do not expose the development HTTP server directly to the internet.

Open `/admin` to create keys with a duration from 1 to 3650 days, see status, release a device, or revoke a key. A key starts its expiry countdown on first activation. A license is limited to one active device. Its short lease expires after five minutes without heartbeats; the client renews periodically. When a machine is replaced, release it in the admin page. A release or revoke applies on the next online request.

The server stores keyed hashes of license keys (pepper protected), not original keys. The generated key is displayed once, so copy it at creation time. Back up the database and pepper together; losing the pepper invalidates stored key hashes.

## Build the one-file Windows client

1. Edit `API_URL` near the top of `client.py` to your deployed service's HTTPS base URL (no trailing slash) before building.
2. Install dependencies with `python -m pip install -r requirements-build.txt`.
3. Run `pyinstaller --clean --noconfirm client.spec` from this directory.
4. The single distributable is `dist/OpenTxtAssistant.exe`. Copy only that executable into a new empty share folder. It stores user settings and a short-lived lease under `%APPDATA%\OpenTxt`.

The endpoint is a client setting and can be discovered by users. No administrator or provider secret is embedded. Provider API keys remain on the server. Replace the example URL with your actual HTTPS service URL before packaging.

For local development only, temporarily set `API_URL` to `http://127.0.0.1:8765` in `client.py`. Do not use plain HTTP over a network. The sample `.env` is documentation only; load environment variables from your private server file before running `python server.py`.

