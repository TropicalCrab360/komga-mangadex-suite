import os
import io
import zipfile
import httpx
import asyncio
import shutil
from typing import Dict, Any, List
from datetime import datetime, timedelta
from PIL import Image
from .mangadex_client import MangaDexClient
import logging

class DownloadManager:
    def __init__(self, client: MangaDexClient, library_root: str, work_dir: str, language: str,
                 komga_base: str | None, komga_library_id: str | None, komga_token: str | None,
                 auto_scan: bool = True, use_datasaver: bool = False):
        self.client = client
        self.library_root = library_root
        self.work_dir = work_dir
        self.language = language
        self.komga_base = komga_base.rstrip('/') if komga_base else None
        self.komga_library_id = komga_library_id
        self.komga_token = komga_token
        self.auto_scan = auto_scan
        self.use_datasaver = use_datasaver
        self._last_scan: datetime | None = None
        os.makedirs(work_dir, exist_ok=True)
        self.logger = logging.getLogger("downloader")

    async def _set_komga_series_thumbnail(self, series_dir: str, series_name: str):
        """Use Komga API to set the series thumbnail from cover.jpg if available."""
        try:
            if not (self.komga_base and self.komga_library_id and self.komga_token):
                return
            cover_path = os.path.join(series_dir, "cover.jpg")
            if not os.path.exists(cover_path):
                return
            # Find series in Komga by name (paged response has 'content')
            search_url = f"{self.komga_base}/api/v1/series"
            async with httpx.AsyncClient(timeout=30.0) as hc:
                r = await hc.get(search_url, params={"search": series_name, "page": 0, "size": 50}, headers={"Authorization": f"Bearer {self.komga_token}"})
                r.raise_for_status()
                payload = r.json()
            content = payload.get("content") if isinstance(payload, dict) else None
            if not content:
                async with httpx.AsyncClient(timeout=30.0) as hc:
                    r2 = await hc.get(search_url, params={"page": 0, "size": 50}, headers={"Authorization": f"Bearer {self.komga_token}"})
                    r2.raise_for_status()
                    payload = r2.json()
                content = payload.get("content") if isinstance(payload, dict) else None
            series_id = None
            if isinstance(content, list):
                for s in content:
                    if (s.get("title") or "").strip().lower() == series_name.strip().lower():
                        series_id = s.get("id")
                        break
                if not series_id and content:
                    series_id = content[0].get("id")
            if not series_id:
                return
            # Upload thumbnail
            thumb_url = f"{self.komga_base}/api/v1/series/{series_id}/thumbnail"
            async with httpx.AsyncClient(timeout=30.0) as hc:
                with open(cover_path, "rb") as f:
                    files = {"file": ("cover.jpg", f, "image/jpeg")}
                    r = await hc.post(thumb_url, files=files, headers={"Authorization": f"Bearer {self.komga_token}"})
                    r.raise_for_status()
            self.logger.info("komga thumbnail updated for series=%s", series_name)
        except Exception as e:
            # Non-fatal
            self.logger.warning("failed to set komga thumbnail: %s", e)

    async def _download_series_metadata(self, series_dir: str, manga_data: Dict[str, Any], manga_id: str):
        """Download cover image and create series.json with metadata if they don't exist."""
        try:
            cover_path = os.path.join(series_dir, "cover.jpg")
            series_json_path = os.path.join(series_dir, "series.json")
            
            # Skip if both already exist
            if os.path.exists(cover_path) and os.path.exists(series_json_path):
                self.logger.debug("series metadata already exists for %s", series_dir)
                return
            
            attrs = manga_data.get("attributes", {})
            relationships = manga_data.get("relationships", [])
            
            # Download cover image
            if not os.path.exists(cover_path):
                cover_rel = next((r for r in relationships if r.get("type") == "cover_art"), None)
                if cover_rel:
                    cover_filename = cover_rel.get("attributes", {}).get("fileName")
                    if cover_filename:
                        cover_url = f"https://uploads.mangadex.org/covers/{manga_id}/{cover_filename}"
                        try:
                            async with self.client.api_rps:
                                response = await self.client.client.get(cover_url)
                            response.raise_for_status()
                            with open(cover_path, "wb") as f:
                                f.write(response.content)
                            self.logger.info("downloaded cover image to %s", cover_path)
                        except Exception as e:
                            self.logger.warning("failed to download cover: %s", e)
            
            # Create series.json with metadata
            if not os.path.exists(series_json_path):
                import json
                title_obj = attrs.get("title", {})
                desc_obj = attrs.get("description", {})
                metadata = {
                    "title": title_obj.get("en") or next(iter(title_obj.values()), "Unknown"),
                    "description": desc_obj.get("en") or next(iter(desc_obj.values()), ""),
                    "status": attrs.get("status", ""),
                    "year": attrs.get("year"),
                    "tags": [tag.get("attributes", {}).get("name", {}).get("en", "") for tag in attrs.get("tags", [])],
                    "authors": [r.get("attributes", {}).get("name") for r in relationships if r.get("type") == "author"],
                    "artists": [r.get("attributes", {}).get("name") for r in relationships if r.get("type") == "artist"],
                    "mangadex_id": manga_id,
                }
                with open(series_json_path, "w", encoding="utf-8") as f:
                    json.dump(metadata, f, indent=2, ensure_ascii=False)
                self.logger.info("created series metadata file %s", series_json_path)
        except Exception as e:
            self.logger.warning("failed to download series metadata: %s", e)

    def _create_comic_info_xml(self, manga_data: Dict[str, Any] | None, chapter: Dict[str, Any], chapter_number: str, chapter_title: str) -> str:
        """Create ComicInfo.xml content for embedding in CBZ."""
        if not manga_data:
            return ""
        
        try:
            import xml.etree.ElementTree as ET
            attrs = manga_data.get("attributes", {})
            relationships = manga_data.get("relationships", [])
            
            root = ET.Element("ComicInfo")
            
            # Series info
            title_obj = attrs.get("title", {})
            series_title = title_obj.get("en") or next(iter(title_obj.values()), "Unknown")
            ET.SubElement(root, "Series").text = series_title
            ET.SubElement(root, "Number").text = str(chapter_number)
            if chapter_title:
                ET.SubElement(root, "Title").text = chapter_title
            
            # Description
            desc_obj = attrs.get("description", {})
            desc = desc_obj.get("en") or next(iter(desc_obj.values()), "")
            if desc:
                ET.SubElement(root, "Summary").text = desc
            
            # Year
            if attrs.get("year"):
                ET.SubElement(root, "Year").text = str(attrs["year"])
            
            # Authors and artists
            writers = [r.get("attributes", {}).get("name") for r in relationships if r.get("type") == "author"]
            if writers:
                ET.SubElement(root, "Writer").text = ", ".join(w for w in writers if w)
            
            artists = [r.get("attributes", {}).get("name") for r in relationships if r.get("type") == "artist"]
            if artists:
                ET.SubElement(root, "Penciller").text = ", ".join(a for a in artists if a)
            
            # Genre/Tags
            tags = [tag.get("attributes", {}).get("name", {}).get("en", "") for tag in attrs.get("tags", [])]
            if tags:
                ET.SubElement(root, "Genre").text = ", ".join(t for t in tags[:5] if t)  # Limit to 5 tags
            
            # Manga format
            ET.SubElement(root, "Manga").text = "Yes"
            
            # Language
            chapter_lang = chapter.get("attributes", {}).get("translatedLanguage", "en")
            ET.SubElement(root, "LanguageISO").text = chapter_lang
            
            return ET.tostring(root, encoding="unicode", method="xml")
        except Exception as e:
            self.logger.warning("failed to create ComicInfo.xml: %s", e)
            return ""

    async def process_job(self, job: Dict[str, Any]) -> Dict[str, Any]:
        manga_id = job["manga_id"]
        language = job.get("language") or self.language
        self.logger.info("process_job start manga_id=%s language=%s", manga_id, language)
        
        # Fetch manga details for title, description, and cover
        manga_title = None
        manga_data_full = None
        try:
            manga_data = await self.client.get_manga(manga_id)
            manga_data_full = manga_data.get("data", {})
            title_obj = manga_data_full.get("attributes", {}).get("title", {})
            manga_title = title_obj.get("en") or title_obj.get("ja-ro") or title_obj.get("ja") or next(iter(title_obj.values()), None)
            if manga_title:
                # Sanitize for filesystem
                manga_title = "".join(c for c in manga_title if c.isalnum() or c in " -_.,()[]").strip()
                self.logger.info("manga title: %s", manga_title)
        except Exception:
            self.logger.warning("failed to fetch manga title, using ID")
        
        # Fetch all chapters with pagination (fallback to smaller page size on 400; guard unexpected errors)
        chapters = []
        offset = 0
        limit = 500
        fallback_limit = 100
        try:
            while True:
                try:
                    chapters_json = await self.client.get_manga_chapters(manga_id, language, limit=limit, offset=offset)
                except httpx.HTTPStatusError as e:
                    # Some series can trigger 400 with large limits; retry with smaller page size
                    if e.response is not None and e.response.status_code == 400 and limit != fallback_limit:
                        limit = fallback_limit
                        # retry immediately without advancing offset
                        try:
                            chapters_json = await self.client.get_manga_chapters(manga_id, language, limit=limit, offset=offset)
                        except httpx.HTTPStatusError:
                            # Give up on chapter listing gracefully
                            chapters_json = {"data": [], "total": 0}
                            self.logger.warning("chapter listing 400 even with fallback limit; proceeding empty")
                    else:
                        # Other HTTP errors: stop listing gracefully
                        chapters_json = {"data": [], "total": 0}
                        self.logger.warning("chapter listing HTTP error; proceeding partial")
                batch = chapters_json.get("data", [])
                if not batch:
                    break
                chapters.extend(batch)
                offset += len(batch)
                total = chapters_json.get("total", 0)
                self.logger.debug("chapters batch fetched: size=%s offset=%s total=%s", len(batch), offset, total)
                if offset >= total:
                    break
        except Exception:
            # Unexpected error—proceed with whatever we have (possibly empty)
            self.logger.exception("unexpected error during chapter listing; proceeding with partial list")

        # Filtering: volumes and chapters
        volumes_filter = job.get("volumes") or []
        chapters_filter = job.get("chapters") or []

        def chapter_str_to_float(chval: Any) -> float | None:
            try:
                if chval is None:
                    return None
                return float(str(chval))
            except Exception:
                return None

        # Normalize filters
        range_mode = False
        ch_start = ch_end = None
        if isinstance(chapters_filter, list) and len(chapters_filter) == 2:
            # Treat as inclusive range if both parse
            ch_start = chapter_str_to_float(chapters_filter[0])
            ch_end = chapter_str_to_float(chapters_filter[1])
            range_mode = ch_start is not None and ch_end is not None

        def include_chapter(ch: Dict[str, Any]) -> bool:
            attrs = ch.get("attributes", {})
            vol = attrs.get("volume")
            chnum = attrs.get("chapter")
            # If chapter number missing, include by default unless explicit list provided
            chnum_f = chapter_str_to_float(chnum)
            # Volumes filter
            if volumes_filter:
                if vol is None or str(vol) not in set(map(str, volumes_filter)):
                    return False
            if chapters_filter:
                if range_mode:
                    if chnum_f is None or ch_start is None or ch_end is None:
                        return False
                    if not (ch_start <= chnum_f <= ch_end):
                        return False
                else:
                    # treat as explicit list of chapter numbers (strings)
                    if chnum is None or str(chnum) not in set(map(str, chapters_filter)):
                        return False
            return True

        if volumes_filter or chapters_filter:
            before = len(chapters)
            chapters = [c for c in chapters if include_chapter(c)]
            self.logger.info("filter applied volumes=%s chapters=%s count=%s (from %s)", volumes_filter, chapters_filter, len(chapters), before)
        else:
            self.logger.info("no filter applied; chapters=%s", len(chapters))
        created_files: List[str] = []
        skipped_chapters = 0
        skipped_external = 0
        skipped_no_images = 0
        errors = 0
        metadata_downloaded = False
        # Simplified: iterate chapters, download images, package CBZ
        for ch in chapters:
            chapter_id = ch.get("id")
            if not chapter_id:
                skipped_chapters += 1
                continue
            attrs_all = ch.get("attributes", {})
            # Skip external chapters (hosted elsewhere, no at-home images)
            if attrs_all.get("externalUrl"):
                skipped_external += 1
                skipped_chapters += 1
                continue
            try:
                at_home = await self.client.get_at_home(chapter_id)
            except Exception:
                # Skip chapters that fail to resolve at-home server
                errors += 1
                skipped_chapters += 1
                self.logger.warning("at-home lookup failed chapter_id=%s", chapter_id)
                continue
            base_url = at_home.get("baseUrl")
            chapter_hash = at_home.get("chapter", {}).get("hash")
            ch_info = at_home.get("chapter", {})
            data_regular = ch_info.get("data", [])
            data_saver = ch_info.get("dataSaver", [])
            # Choose images list based on configuration, with fallback to the other list if preferred is empty
            prefer_datasaver = self.use_datasaver
            if prefer_datasaver:
                data = data_saver
                data_kind = "data-saver"
                if not data and data_regular:
                    data = data_regular
                    data_kind = "data"
            else:
                data = data_regular
                data_kind = "data"
                if not data and data_saver:
                    data = data_saver
                    data_kind = "data-saver"
            if not (base_url and chapter_hash and data):
                skipped_no_images += 1
                skipped_chapters += 1
                self.logger.info("no images for chapter chapter_id=%s kind=%s", chapter_id, data_kind)
                continue
            image_bytes: List[bytes] = []
            consecutive_400s = 0
            # Fetch images sequentially respecting global limits via client
            for img in data:
                url = f"{base_url}/{data_kind}/{chapter_hash}/{img}"
                # Direct fetch with retry for robustness
                for attempt in range(3):
                    try:
                        async with self.client.api_rps:  # reuse limiter
                            image_headers = dict(self.client.headers)
                            image_headers["Referer"] = "https://mangadex.org"
                            r = await self.client.client.get(url, headers=image_headers)
                        if r.status_code == 429:
                            await asyncio.sleep(float(r.headers.get("Retry-After", 2)))
                            continue
                        r.raise_for_status()
                        image_bytes.append(r.content)
                        consecutive_400s = 0
                        break
                    except Exception as e:
                        if attempt == 2:
                            errors += 1
                            error_str = str(e)
                            self.logger.warning("image download failed after 3 attempts chapter_id=%s url=%s error=%s", chapter_id, url, error_str)
                            if "400" in error_str:
                                consecutive_400s += 1
                                # If 5+ consecutive 400s on datasaver, abort and try regular data
                                if consecutive_400s >= 5 and data_kind == "data-saver" and data_regular:
                                    self.logger.warning("aborting datasaver due to consecutive 400s, retrying chapter with regular images chapter_id=%s", chapter_id)
                                    break
                        else:
                            await asyncio.sleep(1)
                if consecutive_400s >= 5 and data_kind == "data-saver" and data_regular:
                    break
            
            # Retry with regular images if datasaver completely failed
            if not image_bytes and data_kind == "data-saver" and data_regular:
                self.logger.info("retrying chapter with regular images chapter_id=%s", chapter_id)
                image_bytes = []
                errors_before = errors
                for img in data_regular:
                    url = f"{base_url}/data/{chapter_hash}/{img}"
                    for attempt in range(3):
                        try:
                            async with self.client.api_rps:
                                image_headers = dict(self.client.headers)
                                image_headers["Referer"] = "https://mangadex.org"
                                r = await self.client.client.get(url, headers=image_headers)
                            if r.status_code == 429:
                                await asyncio.sleep(float(r.headers.get("Retry-After", 2)))
                                continue
                            r.raise_for_status()
                            image_bytes.append(r.content)
                            break
                        except Exception as e:
                            if attempt == 2:
                                errors += 1
                                self.logger.warning("regular image download failed after 3 attempts chapter_id=%s url=%s error=%s", chapter_id, url, str(e))
                            else:
                                await asyncio.sleep(1)
                if image_bytes:
                    self.logger.info("fallback to regular images succeeded chapter_id=%s images=%s/%s", chapter_id, len(image_bytes), len(data_regular))
                    # Adjust error count since we succeeded
                    errors = errors_before
            
            if not image_bytes:
                skipped_no_images += 1
                skipped_chapters += 1
                self.logger.info("image downloads failed chapter_id=%s images_expected=%s images_fetched=0", chapter_id, len(data))
                continue
            self.logger.debug("fetched images chapter_id=%s count=%s/%s", chapter_id, len(image_bytes), len(data))
            # Use manga title for directory if available, otherwise use ID
            series_name = manga_title if manga_title else manga_id
            series_dir = os.path.join(self.library_root, series_name)
            os.makedirs(series_dir, exist_ok=True)
            
            # Download series metadata once per job if not exists
            if not metadata_downloaded and manga_data_full:
                await self._download_series_metadata(series_dir, manga_data_full, manga_id)
                metadata_downloaded = True
                # Trigger Komga scan then set thumbnail
                try:
                    await self.trigger_komga_scan()
                    await asyncio.sleep(2)
                except Exception:
                    pass
                await self._set_komga_series_thumbnail(series_dir, series_name)
            
            chapter_number = ch.get("attributes", {}).get("chapter") or chapter_id
            chapter_title = ch.get("attributes", {}).get("title") or ""
            safe_chapter_title = "".join(c for c in chapter_title if c.isalnum() or c in " -_.").strip()
            filename = f"c{chapter_number} {safe_chapter_title}.cbz" if safe_chapter_title else f"c{chapter_number}.cbz"
            tmp_path = os.path.join(self.work_dir, f"{chapter_id}.cbz.tmp")
            final_path = os.path.join(series_dir, filename)
            
            # Create ComicInfo.xml for Komga
            comic_info_xml = self._create_comic_info_xml(manga_data_full, ch, chapter_number, chapter_title)
            
            with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                # Add ComicInfo.xml first
                if comic_info_xml:
                    zf.writestr("ComicInfo.xml", comic_info_xml)
                # Add images
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
            shutil.move(tmp_path, final_path)
            created_files.append(final_path)
            self.logger.info("packaged chapter chapter_id=%s file=%s", chapter_id, final_path)
        if self.auto_scan:
            await self.trigger_komga_scan()
        result = {
            "chapters_processed": len(chapters),
            "chapters_packaged": len(created_files),
            "files_created": created_files,
            "skipped_chapters": skipped_chapters,
            "skipped_external": skipped_external,
            "skipped_no_images": skipped_no_images,
            "errors": errors,
        }
        self.logger.info("process_job summary manga_id=%s %s", manga_id, {
            k: (v if (not isinstance(v, list)) else len(v)) for k, v in result.items()
        })
        return result

    async def trigger_komga_scan(self) -> Dict[str, Any]:
        if not (self.komga_base and self.komga_library_id and self.komga_token):
            return {"scan": "skipped", "reason": "missing_config"}
        now = datetime.utcnow()
        if self._last_scan and now - self._last_scan < timedelta(seconds=30):
            return {"scan": "skipped", "reason": "debounced"}
        url = f"{self.komga_base}/api/v1/libraries/{self.komga_library_id}/scan"
        headers = {"Authorization": f"Bearer {self.komga_token}"}
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                r = await client.post(url, headers=headers)
                if r.status_code == 202:
                    self._last_scan = now
                    return {"scan": "triggered"}
                return {"scan": "failed", "status": r.status_code}
            except Exception as e:
                return {"scan": "error", "error": str(e)}
