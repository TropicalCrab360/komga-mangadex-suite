# Komga + MangaDex Suite

This repository contains a full, containerized suite for managing manga with Komga:
- An async MangaDex downloader (API + At‑Home) that packages chapters to CBZ with ComicInfo metadata and cover art
- A lightweight Scheduler UI to search, preview volumes/chapters, queue jobs, and view live logs
- Nginx reverse proxy exposing Komga at `/`, the Scheduler at `/scheduler/`, and the Downloader API at `/downloader/`

It’s designed to run on Windows and macOS (Apple Silicon) with Docker Compose, and can optionally sync outputs to cloud folders (e.g., OneDrive) and enable HTTPS.

## Features
- Preview available volumes and paged chapters by language (server-side pagination).
- Queue downloads with optional filters: specific volumes, explicit chapter list, or range.
- Datasaver support: uses `dataSaver` images when `USE_DATASAVER=true`, with fallback.
- Job status with counts: processed, packaged, files, skipped, external, no-images, errors.
- Tail logs from the UI: view recent downloader logs with one click.
- Optional auto-scan for Komga after jobs (debounced ~30s).

## Services
Access via Nginx reverse proxy (recommended):
- Komga (root): `http://localhost:8080/`
- Scheduler UI: `http://localhost:8080/scheduler/`
- Downloader API health: `http://localhost:8080/downloader/health`

Direct access (bypassing proxy):
- Komga: `http://localhost:25600`
- Downloader API: `http://localhost:8000`
- Scheduler UI: `http://localhost:8088`

## Quick Start (Windows PowerShell)
1. Copy env template:
```powershell
Copy-Item .env.example .env
```
2. Edit `.env` and set `USER_AGENT` with contact info (per MangaDex ToS).
	 - To enable personal client auth, set one of:
		 - `MANGADEX_USERNAME` and `MANGADEX_PASSWORD` (client logs in, stores token, auto-refreshes on 401)
		- `MANGADEX_CLIENT_ID` and `MANGADEX_CLIENT_SECRET` (required for personal client OIDC password grant)
		- or `MANGADEX_TOKEN` (pre-issued access token)
	 - Optionally set `KOMGA_LIBRARY_ID` and `KOMGA_TOKEN` to trigger Komga scans.
	 - Recommended: use Docker secrets for credentials. Create files under `./.secrets/`:
		 - `./.secrets/mangadex_username` (contents: your username/email)
		 - `./.secrets/mangadex_password` (contents: your password)
		- `./.secrets/mangadex_client_id` (contents: your personal client id)
		- `./.secrets/mangadex_client_secret` (contents: your personal client secret)
		 - `./.secrets/mangadex_token` (optional: contents: access token)
		 The downloader reads `/run/secrets/...` automatically and falls back to env vars.
	 - Alternative: bind a JSON file `manga-downloader/config/mangadex_credentials.json` with keys `username`, `password`, `client_id`, `client_secret`, `token`. See `manga-downloader/config/mangadex_credentials.example.json`.
 	 - Optional logging: set `LOG_LEVEL=DEBUG` for detailed logs; `LOG_FILE` defaults to `/app/data/downloader.log`.
 	 - Images: set `USE_DATASAVER=true` to prefer MangaDex data-saver images.
3. Launch:
```powershell
docker compose up -d
```
4. Open Komga and add `/library` as your library path if not auto-scanned.

## UI Guide
- Search for a title, select it, choose language.
- Click "Load Preview" to fetch volumes and the first page of chapters; use Prev/Next to page.
- Click "Add" on volumes/chapters to populate the filters, then "Queue Download".
- Monitor jobs under "Job Status"; auto-refresh runs every 10s while jobs are active.
- Use "Downloader Logs" → "Tail Logs" for recent entries (set `LOG_LEVEL=DEBUG` for more detail).

## Notes on Limits
- API limiter ~4 req/s and 100 req/min.
- At-Home per-baseUrl limiter 1 req/sec; honors `Retry-After`.
- `USE_DATASAVER=true` reduces bandwidth by using dataSaver images.

## Environment Variables
- `USER_AGENT`: required, include contact per MangaDex ToS.
- `LANGUAGE`: default language for downloads (UI can override).
- `USE_DATASAVER`: `true|false`, prefer data-saver images (falls back when missing).
- `LOG_LEVEL`: `INFO|DEBUG`, controls verbosity; defaults to `INFO`.
- `LOG_FILE`: path for rotating logs (100MB cap); defaults to `/app/data/downloader.log`.
- `KOMGA_BASE_URL`, `KOMGA_LIBRARY_ID`, `KOMGA_TOKEN`: for Komga scan triggers.
- `AUTO_SCAN`: `true|false`, trigger Komga scan after job completion (debounced ~30s).
- MangaDex auth: `MANGADEX_USERNAME`, `MANGADEX_PASSWORD`, `MANGADEX_CLIENT_ID`, `MANGADEX_CLIENT_SECRET`, or `MANGADEX_TOKEN`.

## Library Structure
Downloader saves CBZ as:
```
/Series Name/Series Name v01 c001 - Chapter Title.cbz
```
Komga picks up new files from the shared `./shared-library` folder.

## Reverse Proxy Paths
- Nginx routes:
	- `/` → Komga UI (Komga served at root for reliability)
	- `/scheduler/` → Scheduler UI
	- `/downloader/` → Downloader API
- Use trailing slashes for app roots; `/scheduler` auto-redirects to `/scheduler/`.

## HTTPS (Optional)
- Self-signed local cert:
	- Generate certs and place under `nginx/ssl/` as `cert.pem` and `key.pem`.
		- Example (OpenSSL): `openssl req -x509 -nodes -days 365 -newkey rsa:2048 -keyout nginx/ssl/key.pem -out nginx/ssl/cert.pem -subj "/CN=localhost"`
	- Update `nginx/nginx.conf` to add an SSL server listening on 443 and reference the certs:
		```
		server {
			listen 443 ssl;
			server_name localhost;
			ssl_certificate     /etc/nginx/ssl/cert.pem;
			ssl_certificate_key /etc/nginx/ssl/key.pem;
			# same location blocks as the HTTP server
		}
		```
	- Map the certs and port in `docker-compose.yml`:
		```yaml
		services:
			nginx:
				volumes:
					- ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro
					- ./nginx/ssl:/etc/nginx/ssl:ro
				ports:
					- "8080:80"
					- "8443:443"
		```
- Real certificates:
	- Use a domain and Let’s Encrypt via `certbot` or `nginx-proxy` + `acme-companion`.
	- Set `server_name` to your domain and mount the issued certificate files in the container.

## macOS (Apple Silicon) Notes
- Docker: Use Docker Desktop for Mac (Apple Silicon). Images used here are multi-arch; no special flags are needed in most cases.
- Optional platform override: If you hit architecture mismatches, you can set a default platform when building/pulling images.
	- Temporarily in your shell: `export DOCKER_DEFAULT_PLATFORM=linux/arm64/v8`
	- Or add `platform: linux/arm64/v8` under individual services in `docker-compose.yml`.
- Local venv (downloader only):
	- Create venv: `python3 -m venv .venv`; activate: `source .venv/bin/activate`
	- Upgrade tooling: `pip install --upgrade pip wheel`
	- Install deps: `pip install -r manga-downloader/requirements.txt`
	- Run API locally (from repo root): `python -m uvicorn manga-downloader.src.main:app --reload --port 8000`
	- Deactivate: `deactivate`
	- Tip: If Pillow wheels fail to build, ensure Xcode Command Line Tools are installed (`xcode-select --install`).

## macOS Setup (Quick Reference)
- Install Docker Desktop for Mac (Apple Silicon supported).
- If an image is x64-only, temporarily set:
  - `export DOCKER_DEFAULT_PLATFORM=linux/amd64`
- Optional Python local development:
  - `python3 -m venv .venv`
  - `source .venv/bin/activate`
  - `pip install --upgrade pip wheel`
  - `pip install -r manga-downloader/requirements.txt`
  - If Pillow build fails: `xcode-select --install`

## Troubleshooting
- "packaged: 0; external: >0": the chapter is external (hosted off-site); choose a different chapter or clear filters.
- "no-images: >0": no image set for that language; try another language.
- Paging errors: volumes are aggregated once; paging updates chapters only to avoid API rate/edge errors.
- Use the logs viewer in the UI or call `GET http://localhost:8000/logs/tail?lines=300` for details.

## Known Limitations
- External chapters: Some MangaDex chapters have an `externalUrl` and are hosted off-site. These cannot be downloaded via MangaDex At‑Home and will be skipped. Use the preview to avoid selecting external chapters.
- Language availability: Not every translated language has an image set for every chapter. If a chapter reports `no-images`, switch to another language or pick different chapters.
- API paging quirks: Certain series return `400 Bad Request` for large page sizes; the downloader retries with a smaller limit. Volumes are aggregated once; subsequent paging only updates chapters.
