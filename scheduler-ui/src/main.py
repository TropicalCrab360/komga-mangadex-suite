import os
import uuid
from typing import Dict, Any

import httpx
from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import asyncio
from pydantic import BaseModel

app = FastAPI(title="Scheduler UI", version="0.1.1")

DOWNLOADER_API_URL = os.getenv("DOWNLOADER_API_URL", "http://manga-downloader:8000").rstrip('/')

class JobRequest(BaseModel):
    manga_id: str
    language: str | None = None
    volumes: list[str] | None = None
    chapters: list[str] | None = None

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/search")
async def search(title: str):
    url = f"{DOWNLOADER_API_URL}/mangadex/search"
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            r = await client.get(url, params={"title": title})
            r.raise_for_status()
            return r.json()
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=e.response.status_code, detail=e.response.text)

@app.post("/jobs")
async def queue_job(req: JobRequest):
    url = f"{DOWNLOADER_API_URL}/jobs"
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            r = await client.post(url, json=req.model_dump())
            r.raise_for_status()
            return r.json()
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=e.response.status_code, detail=e.response.text)

@app.get("/jobs")
async def list_jobs():
    url = f"{DOWNLOADER_API_URL}/jobs"
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(url)
        return r.json()

@app.get("/jobs/{job_id}")
async def get_job(job_id: str):
    url = f"{DOWNLOADER_API_URL}/jobs/{job_id}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(url)
        if r.status_code == 404:
            raise HTTPException(status_code=404, detail="Job not found")
        return r.json()

@app.post("/jobs/{job_id}/abort")
async def abort_job(job_id: str):
    url = f"{DOWNLOADER_API_URL}/jobs/{job_id}/abort"
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            r = await client.post(url)
            r.raise_for_status()
            return r.json()
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=e.response.status_code, detail=e.response.text)

@app.get("/mangadex/chapters")
async def chapters(manga_id: str, language: str | None = None, limit: int = 100, offset: int = 0):
    url = f"{DOWNLOADER_API_URL}/mangadex/chapters"
    params = {"manga_id": manga_id, "limit": limit, "offset": offset}
    if language:
        params["language"] = language
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(url, params=params)
        return r.json()

@app.post("/komga/scan")
async def komga_scan():
    url = f"{DOWNLOADER_API_URL}/komga/scan"
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(url)
        return r.json()

@app.get("/logs/tail")
async def logs_tail(lines: int = 200):
    url = f"{DOWNLOADER_API_URL}/logs/tail"
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(url, params={"lines": lines})
        # Return plain text as-is
        return r.text

@app.websocket("/ws/logs")
async def ws_logs(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            # Poll downloader logs and stream lines
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(f"{DOWNLOADER_API_URL}/logs/tail", params={"lines": 50})
                txt = r.text or ""
            if txt:
                for line in txt.splitlines():
                    await ws.send_text(line)
            await asyncio.sleep(2)
    except Exception:
        try:
            await ws.close()
        except Exception:
            pass

# Serve static UI
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
# Explicit script route to avoid HTML fallback caching
@app.get("/script.js")
async def get_script_js():
    return FileResponse(os.path.join(STATIC_DIR, "script.js"), media_type="application/javascript")

# Mount the rest of the static assets and index.html
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
