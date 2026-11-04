# OpenTxt client and license service

This repository contains the transparent desktop client and its MongoDB-backed license and AI service.

- `safe_app/` contains the Windows GUI client and one-file PyInstaller build spec.
- `license_server/` contains the HTTP service, admin portal, MongoDB dependency, and server environment example.

See [safe_app/README.md](safe_app/README.md) for deployment and build instructions. Configure the HTTPS service URL in `safe_app/client.py` before building the distributable. The backend API keys, admin password, MongoDB URI, and license pepper must be configured as server secrets; never commit them.

The desktop client has a visible license screen, asks before screen capture, and displays AI responses. It does not run a global keyboard listener, simulate typing or clicking, or elevate itself.
