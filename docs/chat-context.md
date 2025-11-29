# Project Context & Quick Resume

Short summary you can paste at the start of a new session. See this file for details and code references.

## Summary (pasteable)
- Three containers: `komga`, `manga-downloader` (FastAPI), and `scheduler-ui` (FastAPI).
- Shared volume `./shared-library` mounted as `/library` for zero-copy Komga ingestion.
- Downloader uses MangaDex API + At‑Home with strict limits: ~4 req/s, ~100 req/min; per-baseUrl 1 req/sec; honors `Retry-After`; retries 5xx with backoff.
- Chapters saved as CBZ: `/Series Name/Series Name v01 c001 - Title.cbz` (Komga-friendly).
- Optional Komga scan trigger via `POST /api/v1/libraries/{id}/scan` using `KOMGA_LIBRARY_ID` and `KOMGA_TOKEN`.
- Automatic Komga scan after each completed job when `AUTO_SCAN=true` (debounced ~30s).
- Basic scheduler UI: search MangaDex and queue download jobs to the downloader.
- Configure via `.env` (`TZ`, `USER_AGENT` with contact per ToS, `LANGUAGE`, `USE_DATASAVER`, Komga settings).
- Run: `docker compose up -d` → Komga `:25600`, Downloader `:8000`, UI `:8088`.
 - New: preview volumes (aggregated) and paged chapters; job status shows processed/packaged/files/skipped/external/no-images/errors; logs tail endpoint and UI viewer; auto-refresh Job Status while active.

## Key Files
- Root compose: `docker-compose.yml`
- Downloader: `manga-downloader/src/main.py`, `manga-downloader/src/mangadex_client.py`, `manga-downloader/src/download_manager.py`
- UI: `scheduler-ui/src/main.py`
- Shared library: `shared-library/`

## Compose Excerpt (abridged)
```yaml
services:
  komga:
    image: gotson/komga:latest
    volumes:
      - ./komga/config:/config
      - ./shared-library:/library
    ports: ["25600:25600"]

  manga-downloader:
    build: ./manga-downloader
    environment:
      - MANGADEX_BASE_URL=${MANGADEX_BASE_URL:-https://api.mangadex.org}
      - USER_AGENT=${USER_AGENT:-KomgaDownloader/1.0 (+your_contact)}
      - LANGUAGE=${LANGUAGE:-en}
      - USE_DATASAVER=${USE_DATASAVER:-true}
      - AUTO_SCAN=${AUTO_SCAN:-true}
      - LOG_LEVEL=${LOG_LEVEL:-INFO}
      - LOG_FILE=${LOG_FILE:-/app/data/downloader.log}
      - LIBRARY_ROOT=/library
      - WORK_DIR=/app/data
      - KOMGA_BASE_URL=${KOMGA_BASE_URL:-http://komga:25600}
      - KOMGA_LIBRARY_ID=${KOMGA_LIBRARY_ID:-}
      - KOMGA_TOKEN=${KOMGA_TOKEN:-}
    volumes:
      - ./shared-library:/library
      - ./manga-downloader/data:/app/data
    ports: ["8000:8000"]

  scheduler-ui:
    build: ./scheduler-ui
    environment:
      - DOWNLOADER_API_URL=http://manga-downloader:8000
    ports: ["8088:8080"]
```

## Downloader Endpoints (FastAPI)
```python
# manga-downloader/src/main.py (abridged)
@app.post("/jobs")            # queue a new download job
@app.get("/jobs")             # list all jobs
@app.get("/jobs/{id}")        # get single job status/result
@app.post("/jobs/{id}/cancel")# cancel queued job
@app.post("/komga/scan")      # manually trigger Komga scan
@app.get("/mangadex/search")  # search MangaDex titles
@app.get("/mangadex/chapters")# paged chapters + aggregated volumes
@app.get("/logs/tail")        # tail recent downloader logs (plain text)
@app.get("/health")           # simple health probe

# Job lifecycle: queued -> running -> completed|failed|canceled
# When AUTO_SCAN=true and Komga vars present, a scan is triggered after completion (debounced 30s).
```

## MangaDex client (rate limits + At‑Home)
```python
# manga-downloader/src/mangadex_client.py
import httpx, asyncio
from aiolimiter import AsyncLimiter

API_RPS_LIMIT = 4
API_RPM_LIMIT = 100

class MangaDexClient:
    def __init__(self, base_url: str, user_agent: str):
        self.base_url = base_url.rstrip('/')
        self.headers = {"User-Agent": user_agent, "Accept": "application/json"}
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=60.0))
        self.api_rps = AsyncLimiter(API_RPS_LIMIT, 1)
        self.api_rpm = AsyncLimiter(API_RPM_LIMIT, 60)
        self.athome_limiters = {}

    async def _limited_get(self, url, params=None):
        async with self.api_rpm:
            async with self.api_rps:
                r = await self.client.get(url, params=params, headers=self.headers)
        if r.status_code == 429:
            await asyncio.sleep(float(r.headers.get("Retry-After", 2)))
            return await self._limited_get(url, params)
        if 500 <= r.status_code < 600:
            await asyncio.sleep(1)
            return await self._limited_get(url, params)
        r.raise_for_status()
        return r
```

## Packaging Chapters to CBZ
```python
# manga-downloader/src/download_manager.py (excerpt)
import os, io, zipfile
from PIL import Image

with zipfile.ZipFile(tmp_cbz, "w", compression=zipfile.ZIP_DEFLATED) as zf:
    for idx, content in enumerate(image_bytes, start=1):
        name = f"{idx:03d}.jpg"
        try:
            img = Image.open(io.BytesIO(content))
            if img.format not in ("JPEG", "JPG"):
                buf = io.BytesIO()
                img.convert("RGB").save(buf, format="JPEG", quality=90)
                content = buf.getvalue()
        except Exception:
            pass
        zf.writestr(name, content)
```

## UI Notes
- Preview volumes are fetched once; chapter pages use server-side pagination.
- Job Status auto-refreshes every 10s while jobs are queued/running.
- Logs viewer is available under "Downloader Logs" (uses `/logs/tail`).
- Status line shows counts: processed, packaged, files, skipped, external, no-images, errors.

## Troubleshooting & Tips
- External chapters are hosted off-site and cannot be packaged via At‑Home; avoid those in filters.
- If `no-images` > 0, try a different language or chapter.
- Set `LOG_LEVEL=DEBUG` to get detailed client and downloader logs.

## Next Steps (Tomorrow)
- Add external chapter indicator in preview and an "exclude external" toggle.
- Improve error messaging in UI when filters match no chapters.
- Consider streaming logs via WebSocket for live tail.

## Windows run commands (PowerShell)
```powershell
Copy-Item .env.example .env
# Edit .env: set USER_AGENT with contact info per MangaDex ToS

docker compose up -d
# Komga:       http://localhost:25600
# Downloader:  http://localhost:8000
# Scheduler:   http://localhost:8088
```

## Reverse Proxy Flow (Nginx)
```
Client (browser/iPhone)
        |
        v
  http://<host>:8080  (Nginx)
    |         |               |
    |         |               |
    v         v               v
   /       /scheduler/     /downloader/
 Komga     Scheduler UI     Downloader API
  |            |                |
  v            v                v
 komga:25600  scheduler-ui:8080  manga-downloader:8000

Notes:
- Komga is served at root `/` for reliable SPA routing.
- `/scheduler` and `/reader` auto-redirect to their trailing-slash variants.
- Direct service ports remain: Komga `25600`, UI `8088`, Downloader `8000`.
```
