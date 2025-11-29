import os
import logging
from logging.handlers import RotatingFileHandler
import asyncio
import uuid
from fastapi import FastAPI, HTTPException
from starlette.responses import PlainTextResponse
from pydantic import BaseModel
from typing import Dict, Any
from .mangadex_client import MangaDexClient
from .download_manager import DownloadManager

app = FastAPI(title="Manga Downloader", version="0.2.0")


def setup_logging():
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logger = logging.getLogger("downloader")
    logger.setLevel(level)
    logger.handlers = []
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    sh = logging.StreamHandler()
    sh.setLevel(level)
    sh.setFormatter(fmt)
    log_file = os.getenv("LOG_FILE", "/app/data/downloader.log")
    fh = RotatingFileHandler(log_file, maxBytes=100 * 1024 * 1024, backupCount=1)
    fh.setLevel(level)
    fh.setFormatter(fmt)
    logger.addHandler(sh)
    logger.addHandler(fh)
    logger.propagate = False
    logger.info("logger initialized: level=%s file=%s", level_name, log_file)


setup_logging()

# In-memory job tracking
jobs: Dict[str, Dict[str, Any]] = {}
queue: asyncio.Queue = asyncio.Queue()

def read_json_credentials(path: str):
    try:
        import json
        if os.path.exists(path) and os.path.isfile(path):
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        return None
    return None

def read_secret(path: str):
    try:
        if os.path.exists(path) and os.path.isfile(path):
            with open(path, 'r', encoding='utf-8') as f:
                return f.read().strip()
    except Exception:
        return None
    return None

creds = read_json_credentials("/app/config/mangadex_credentials.json") or {}

client = MangaDexClient(
    base_url=os.getenv("MANGADEX_BASE_URL", "https://api.mangadex.org"),
    user_agent=os.getenv("USER_AGENT", "KomgaDownloader/1.0 (+contact)"),
    username=(
        creds.get("username")
        or read_secret("/run/secrets/mangadex_username")
        or os.getenv("MANGADEX_USERNAME")
    ),
    password=(
        creds.get("password")
        or read_secret("/run/secrets/mangadex_password")
        or os.getenv("MANGADEX_PASSWORD")
    ),
    client_id=(
        creds.get("client_id")
        or read_secret("/run/secrets/mangadex_client_id")
        or os.getenv("MANGADEX_CLIENT_ID")
    ),
    client_secret=(
        creds.get("client_secret")
        or read_secret("/run/secrets/mangadex_client_secret")
        or os.getenv("MANGADEX_CLIENT_SECRET")
    ),
    token=(
        creds.get("token")
        or read_secret("/run/secrets/mangadex_token")
        or os.getenv("MANGADEX_TOKEN")
    )
)
manager = DownloadManager(
    client=client,
    library_root=os.getenv("LIBRARY_ROOT", "/library"),
    work_dir=os.getenv("WORK_DIR", "/app/data"),
    language=os.getenv("LANGUAGE", "en"),
    komga_base=os.getenv("KOMGA_BASE_URL"),
    komga_library_id=os.getenv("KOMGA_LIBRARY_ID"),
    komga_token=os.getenv("KOMGA_TOKEN"),
    auto_scan=os.getenv("AUTO_SCAN", "true").lower() == "true",
    use_datasaver=os.getenv("USE_DATASAVER", "false").lower() == "true",
)

class JobRequest(BaseModel):
    manga_id: str
    language: str | None = None
    volumes: list[str] | None = None
    chapters: list[str] | None = None

@app.on_event("startup")
async def start_worker():
    logging.getLogger("downloader").info("worker startup")
    asyncio.create_task(worker())

async def worker():
    while True:
        job_id = await queue.get()
        job = jobs.get(job_id)
        if not job:
            queue.task_done()
            continue
        if job["status"] == "aborted":
            logging.getLogger("downloader").info("job already aborted before start: id=%s", job_id)
            queue.task_done()
            continue
        job["status"] = "running"
        try:
            logging.getLogger("downloader").info("job running: id=%s", job_id)
            result = await manager.process_job(job["request"].copy())
            if job["status"] == "aborted":
                logging.getLogger("downloader").info("job aborted during execution: id=%s", job_id)
            else:
                job["status"] = "completed"
            job["result"] = result
            logging.getLogger("downloader").info("job completed: id=%s result=%s", job_id, {
                "chapters_processed": result.get("chapters_processed"),
                "chapters_packaged": result.get("chapters_packaged"),
                "files_created": len(result.get("files_created", [])),
                "skipped_chapters": result.get("skipped_chapters"),
                "errors": result.get("errors"),
            })
        except Exception as e:
            job["status"] = "failed"
            job["error"] = str(e)
            logging.getLogger("downloader").error("job failed: id=%s error=%s", job_id, e)
        finally:
            queue.task_done()

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/mangadex/search")
async def search(title: str):
    return await client.search_manga(title)

@app.get("/mangadex/chapters")
async def chapters(
    manga_id: str,
    language: str | None = None,
    limit: int = 100,
    offset: int = 0,
    aggregate_volumes: bool = True,
):
    """Return paged chapters with optional aggregated volumes across all pages.

    - limit/offset: server-side pagination for chapters list
    - aggregate_volumes: when true, iterates pages to collect unique volume numbers
    """
    lang = language or os.getenv("LANGUAGE", "en")
    data = await client.get_manga_chapters(manga_id, lang, limit=limit, offset=offset)
    # Build current page chapters
    page_vols = set()
    chs = []
    external_map = {}  # chapter_number -> is_external
    for item in data.get("data", []):
        attrs = item.get("attributes", {})
        vol = attrs.get("volume")
        ch = attrs.get("chapter")
        is_external = bool(attrs.get("externalUrl"))
        if vol is not None:
            page_vols.add(str(vol))
        if ch is not None:
            chs.append(str(ch))
            external_map[str(ch)] = is_external
    # MangaDex returns total under 'total'; use it directly when present
    total_chapters = data.get("total")
    # Fallback: if total missing, do not try to guess; UI should rely on page size for navigation
    if total_chapters is None:
        total_chapters = 0
    # Optionally aggregate all volumes by paging through chapter list with a large limit
    vols: set[str] = set()
    if aggregate_volumes:
        try:
            agg_limit = 500
            agg_offset = 0
            loops = 0
            max_loops = 200  # safety cap to avoid excessive paging
            while True:
                agg_data = await client.get_manga_chapters(manga_id, lang, limit=agg_limit, offset=agg_offset)
                agg_items = agg_data.get("data", [])
                for item in agg_items:
                    v = item.get("attributes", {}).get("volume")
                    if v is not None:
                        vols.add(str(v))
                if len(agg_items) < agg_limit:
                    break
                agg_offset += agg_limit
                loops += 1
                if loops >= max_loops:
                    break
        except Exception:
            # If aggregation fails (rate limit, auth, network), fall back to page volumes
            vols = page_vols
    else:
        vols = page_vols
    # Navigation hints
    has_prev = offset > 0
    # If total known, compute using total; else use page size
    has_next = False
    if isinstance(total_chapters, int) and total_chapters > 0:
        has_next = (offset + len(chs)) < total_chapters
    else:
        has_next = len(chs) >= limit
    return {
        "volumes": sorted(vols, key=lambda x: (float(x) if x.replace('.','',1).isdigit() else x)),
        "chapters": chs,
        "external_chapters": external_map,
        "limit": limit,
        "offset": offset,
        "total": total_chapters,
        "has_prev": has_prev,
        "has_next": has_next,
    }

@app.post("/jobs")
async def add_job(req: JobRequest):
    job_id = str(uuid.uuid4())
    jobs[job_id] = {"id": job_id, "status": "queued", "request": req.model_dump(), "result": None}
    await queue.put(job_id)
    return {"job_id": job_id, "status": "queued"}

@app.get("/jobs")
async def list_jobs():
    return list(jobs.values())

@app.get("/jobs/{job_id}")
async def get_job(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job

@app.post("/jobs/{job_id}/abort")
async def abort_job(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] == "running":
        job["status"] = "aborted"
        job["error"] = "Aborted by user"
        logging.getLogger("downloader").warning("job aborted by user: id=%s", job_id)
        return {"status": "aborted", "message": "Job marked as aborted (will stop after current chapter)"}
    elif job["status"] == "queued":
        job["status"] = "aborted"
        job["error"] = "Aborted by user"
        return {"status": "aborted", "message": "Job aborted"}
    else:
        return {"status": job["status"], "message": "Job cannot be aborted (already completed, failed, or aborted)"}

@app.post("/komga/scan")
async def manual_scan():
    result = await manager.trigger_komga_scan()
    logging.getLogger("downloader").info("manual komga scan: %s", result)
    return result

@app.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "queued":
        raise HTTPException(status_code=400, detail="Only queued jobs can be canceled")
    job["status"] = "canceled"
    return {"job_id": job_id, "status": "canceled"}

# Graceful shutdown
@app.on_event("shutdown")
async def shutdown():
    await client.close()


def tail_file(path: str, lines: int = 200) -> str:
    try:
        if not os.path.exists(path):
            return ""
        with open(path, 'rb') as f:
            # Read from the end in blocks
            block_size = 8192
            data = b""
            f.seek(0, os.SEEK_END)
            end = f.tell()
            pos = end
            line_count = 0
            while pos > 0 and line_count <= lines:
                read_size = block_size if pos >= block_size else pos
                pos -= read_size
                f.seek(pos)
                chunk = f.read(read_size)
                data = chunk + data
                line_count = data.count(b"\n")
            # Decode and return last N lines
            text = data.decode('utf-8', errors='ignore')
            parts = text.splitlines()
            return "\n".join(parts[-lines:])
    except Exception:
        return ""


@app.get("/logs/tail")
async def logs_tail(lines: int = 200):
    log_file = os.getenv("LOG_FILE", "/app/data/downloader.log")
    content = tail_file(log_file, max(1, min(lines, 5000)))
    return PlainTextResponse(content or "")
