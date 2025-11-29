# Contributing Guide

Thanks for helping improve the Komga + MangaDex Downloader + Scheduler UI.

## Setup
- Requirements: Docker and Docker Compose; optional Python 3.11 for local runs.
- Copy env: `Copy-Item .env.example .env` (Windows PowerShell) and set `USER_AGENT` with contact info per MangaDex ToS.
- Auth: Prefer Docker secrets under `./.secrets/` or `manga-downloader/config/mangadex_credentials.json`.

### macOS (Apple Silicon)
- Docker Desktop for Mac (Apple Silicon) is supported. Most images are multi-arch.
- Optional: set `DOCKER_DEFAULT_PLATFORM=linux/arm64/v8` when building/pulling if you encounter architecture issues.
- Local virtual environment:
  - Create: `python3 -m venv .venv`
  - Activate: `source .venv/bin/activate`
  - Upgrade tooling: `pip install --upgrade pip wheel`
  - Install deps: `pip install -r manga-downloader/requirements.txt`
  - Run downloader locally: `python -m uvicorn manga-downloader.src.main:app --reload --port 8000`
  - Deactivate: `deactivate`
  - If Pillow fails to build wheels, install Xcode CLT: `xcode-select --install`.

## Run
- Start stack:
  - `docker compose up -d`
- Services:
  - Komga: `http://localhost:25600`
  - Downloader API: `http://localhost:8000`
  - Scheduler UI: `http://localhost:8088`

## Logging & Debugging
- Set `LOG_LEVEL=DEBUG` in `.env` for detailed logs.
- View recent logs:
  - UI: "Downloader Logs" → "Tail Logs"
  - API: `GET http://localhost:8000/logs/tail?lines=300`

## Coding Guidelines
- Keep changes minimal and focused; avoid unrelated refactors.
- Use Python type hints; prefer clear variable names over single letters.
- Handle API rate limits and transient failures gracefully; retry on 429/5xx.
- Favor server-side pagination for large lists; avoid returning huge payloads.
- Update documentation (`README.md`, `docs/chat-context.md`) when behavior or endpoints change.

## UI Notes
- Mount static files after API routes to avoid shadowing.
- Use concise, accessible UI elements; keep buttons and inputs clear.
- Avoid re-fetching expensive aggregates (volumes) on every page change.

## Pull Requests
- Describe the problem and the solution clearly.
- Include acceptance criteria or screenshots for UI changes.
- Add logging where it helps diagnose failures; avoid logging credentials.
- Test locally (queue a job, preview pages, review logs) before submitting.

## Known Areas for Improvement
- External chapter indicator in preview and an "exclude external" toggle.
- Better messaging when filters match zero chapters.
- Optional WebSocket live log tail.
