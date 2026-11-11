from __future__ import annotations

import gc
import json
import logging
import threading
import traceback
from pathlib import Path

from PIL import Image

from waraq.config import Settings
from waraq.engines import build_engine
from waraq.engines.base import PageOCR
from waraq.pages import DocumentError, count_pages, iter_pages, save_preview
from waraq.store import Store

log = logging.getLogger("waraq.worker")


class Worker:
    def __init__(self, settings: Settings, store: Store, engine_factory=None):
        self.settings = settings
        self.store = store
        self.engine_factory = engine_factory or (lambda name: build_engine(name, settings))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self.store.recover_interrupted()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="waraq-ocr", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            job_id = self.store.claim_next()
            if job_id is None:
                self._stop.wait(0.2)
                continue
            try:
                self.process(job_id)
            except Exception:
                log.exception("job %s failed", job_id)
                self.store.update_job(
                    job_id,
                    status="failed",
                    phase="failed",
                    error=traceback.format_exc(limit=4),
                )

    def process(self, job_id: str) -> None:
        job = self.store.get_job(job_id)
        if job is None or job["status"] == "cancelled":
            return
        source = Path(job["source_path"])
        try:
            engine = self.engine_factory(job["engine"])
        except Exception as exc:
            self.store.update_job(job_id, status="failed", phase="failed", error=str(exc))
            return

        if getattr(engine, "name", "") == "datalab":
            self._process_remote(job_id, job, source, engine)
            return
        self._process_local(job_id, job, source, engine)

    def _process_remote(self, job_id: str, job: dict, source: Path, engine) -> None:
        total = self._write_previews(job_id, source)
        if total is None or self.store.is_cancelled(job_id):
            return
        try:
            document_pages = engine.ocr_document(
                source,
                filename=job["filename"],
                content_type=job["content_type"] or "application/octet-stream",
                page_count=total,
                include_headers_footers=job["include_headers_footers"],
                on_tick=lambda phase, done, total_pages, message: self._tick(
                    job_id, phase, done, total_pages, message
                ),
            )
        except Exception as exc:
            if self.store.is_cancelled(job_id) or str(exc) == "cancelled":
                return
            self.store.update_job(job_id, status="failed", phase="failed", error=str(exc))
            return
        if not document_pages:
            self.store.update_job(job_id, status="failed", phase="failed", error="The hosted API returned nothing.")
            return
        asset_dir = self.settings.data_dir / "jobs" / job_id / "assets"
        asset_dir.mkdir(parents=True, exist_ok=True)
        failures = 0
        for index in range(total):
            if self.store.is_cancelled(job_id):
                return
            page = (
                document_pages[index]
                if index < len(document_pages)
                else PageOCR("", "", "", [], 0, error="This page was not in the hosted result.")
            )
            current = self.store.get_page(job_id, index) or {}
            _store_page(
                self.store,
                job_id,
                index,
                page,
                asset_dir,
                int(current.get("width") or 0),
                int(current.get("height") or 0),
            )
            if page.error:
                failures += 1
            self.store.update_job(job_id, pages_done=index + 1, phase="ocr")
        warnings = []
        filled = sum(1 for page in document_pages if page.markdown or page.html or page.plain)
        if total > 1 and filled <= 1:
            warnings.append("Page breaks were not marked, so the full reading is on the first page.")
        self._finish(job_id, total, failures, warnings)

    def _process_local(self, job_id: str, job: dict, source: Path, engine) -> None:
        preview_dir = self.settings.data_dir / "jobs" / job_id / "previews"
        asset_dir = self.settings.data_dir / "jobs" / job_id / "assets"
        asset_dir.mkdir(parents=True, exist_ok=True)
        try:
            total = count_pages(source)
        except Exception as exc:
            self.store.update_job(job_id, status="failed", phase="failed", error=str(exc))
            return
        if total <= 0:
            self.store.update_job(job_id, status="failed", phase="failed", error="This file has no pages.")
            return
        self.store.update_job(job_id, pages_total=total, pages_done=0, phase="ocr", message=None)
        failures = 0
        seen = 0
        try:
            for index, image in iter_pages(source, self.settings):
                seen += 1
                if self.store.is_cancelled(job_id):
                    image.close()
                    return
                try:
                    save_preview(image, preview_dir / f"{index}.jpg")
                    self.store.update_job(job_id, phase="ocr", pages_done=index)
                    page = _read_page(engine, image, job["include_headers_footers"], index)
                    _store_page(self.store, job_id, index, page, asset_dir, image.width, image.height)
                    if page.error:
                        failures += 1
                except Exception as exc:
                    if index == 0 and _setup_error(exc):
                        image.close()
                        self.store.update_job(job_id, status="failed", phase="failed", error=str(exc))
                        return
                    failures += 1
                    self.store.save_page(
                        job_id,
                        index,
                        error=str(exc),
                        width=image.width,
                        height=image.height,
                        preview_path=str(preview_dir / f"{index}.jpg"),
                    )
                finally:
                    image.close()
                    gc.collect()
                self.store.update_job(job_id, pages_done=index + 1)
        except DocumentError as exc:
            self.store.update_job(job_id, status="failed", phase="failed", error=str(exc))
            return
        if seen == 0:
            self.store.update_job(job_id, status="failed", phase="failed", error="This file has no pages.")
            return
        if self.store.is_cancelled(job_id):
            return
        self._finish(job_id, total, failures, [])

    def _write_previews(self, job_id: str, source: Path) -> int | None:
        preview_dir = self.settings.data_dir / "jobs" / job_id / "previews"
        self.store.update_job(job_id, phase="preview", pages_done=0, message=None)
        seen = 0
        try:
            for index, image in iter_pages(source, self.settings):
                if self.store.is_cancelled(job_id):
                    image.close()
                    return None
                try:
                    save_preview(image, preview_dir / f"{index}.jpg")
                    self.store.save_page(
                        job_id,
                        index,
                        preview_path=str(preview_dir / f"{index}.jpg"),
                        width=image.width,
                        height=image.height,
                    )
                finally:
                    image.close()
                    gc.collect()
                seen += 1
                self.store.update_job(job_id, pages_done=seen, pages_total=seen, phase="preview")
        except DocumentError as exc:
            self.store.update_job(job_id, status="failed", phase="failed", error=str(exc))
            return None
        if seen == 0:
            self.store.update_job(job_id, status="failed", phase="failed", error="This file has no pages.")
            return None
        total = count_pages(source)
        self.store.update_job(job_id, pages_total=total, pages_done=0, phase="remote")
        return total

    def _finish(self, job_id: str, total: int, failures: int, warnings: list[str]) -> None:
        if failures and failures < total:
            warnings.append(f"{failures} page(s) could not be read. The rest are ready.")
        if total and failures >= total:
            self.store.update_job(
                job_id,
                status="failed",
                phase="failed",
                pages_done=total,
                pages_total=total,
                error="Every page failed.",
                warnings=json.dumps(warnings),
            )
            return
        self.store.update_job(
            job_id,
            status="complete",
            phase="complete",
            pages_done=total,
            pages_total=total,
            message=None,
            warnings=json.dumps(warnings),
        )

    def _tick(self, job_id: str, phase: str, done: int, total: int, message: str | None) -> None:
        if self.store.is_cancelled(job_id):
            raise RuntimeError("cancelled")
        fields = {"phase": phase, "message": message}
        if total:
            fields["pages_total"] = total
        fields["pages_done"] = done
        self.store.update_job(job_id, **fields)


def _setup_error(exc: Exception) -> bool:
    text = str(exc).lower()
    needles = (
        "connection",
        "connect",
        "install",
        "api key",
        "no module",
        "cuda",
        "out of memory",
        "not responding",
        "failed to load",
    )
    return any(needle in text for needle in needles)


def _read_page(engine, image, include_headers: bool, page_index: int) -> PageOCR:
    last: Exception | None = None
    for attempt in range(2):
        try:
            return engine.ocr_page(
                image,
                include_headers_footers=include_headers,
                page_index=page_index,
            )
        except Exception as exc:
            last = exc
            if attempt == 0:
                continue
    assert last is not None
    raise last


def _store_page(store: Store, job_id: str, index: int, page: PageOCR, asset_dir: Path, width: int, height: int) -> None:
    for name, figure in page.images.items():
        safe = Path(name).name
        if not safe:
            continue
        dest = asset_dir / safe
        try:
            if isinstance(figure, Image.Image):
                figure.save(dest)
                figure.close()
        except Exception:
            log.warning("could not save figure %s", safe, exc_info=True)
    store.save_page(
        job_id,
        index,
        markdown=page.markdown,
        html=page.html,
        plain=page.plain,
        chunks=page.chunks,
        token_count=page.token_count,
        width=width,
        height=height,
        error=page.error,
        preview_path=str(store.settings.data_dir / "jobs" / job_id / "previews" / f"{index}.jpg"),
    )
