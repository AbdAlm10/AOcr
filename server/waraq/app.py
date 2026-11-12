from __future__ import annotations

import io
import json
import math
import re
import shutil
import uuid
import zipfile
from collections.abc import Iterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from waraq import __version__
from waraq.config import Settings, get_settings
from waraq.exports import document_json, embed_images, join_parts, rewrite_asset_urls
from waraq.hardware import Probe, decide, hf_stack_installed, query_nvidia_smi, vllm_reachable
from waraq.network import phone_urls
from waraq.pages import SUPPORTED_EXTENSIONS, is_supported
from waraq.store import Store
from waraq.worker import Worker

UPLOAD_ID = re.compile(r"^[a-f0-9]{32}$")
ASSET_NAME = re.compile(r"^[A-Za-z0-9._-]{1,180}$")


class UploadInit(BaseModel):
    filename: str
    size: int = Field(gt=0)
    content_type: str = "application/octet-stream"


class UploadComplete(BaseModel):
    include_headers_footers: bool = False


def create_app(settings: Settings | None = None, engine_factory=None) -> FastAPI:
    settings = settings or get_settings()
    settings.ensure_dirs()
    store = Store(settings)
    worker = Worker(settings, store, engine_factory=engine_factory)

    app = FastAPI(title="AOcr", version=__version__)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.settings = settings
    app.state.store = store
    app.state.worker = worker

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> Iterator[None]:
        worker.start()
        yield
        worker.stop()
        store.close()

    app.router.lifespan_context = lifespan

    @app.middleware("http")
    async def access_gate(request: Request, call_next):
        token = request.app.state.settings.access_token
        if token and request.url.path.startswith("/api/") and request.url.path != "/api/health":
            if request.headers.get("x-access-token", "") != token:
                return JSONResponse(
                    {"detail": "This desk is locked. Enter the access token from .env."},
                    status_code=401,
                )
        return await call_next(request)

    @app.get("/api/health")
    def health() -> dict:
        decision, probe = _inspect(settings)
        return {
            "ok": True,
            "version": __version__,
            "engine": decision.engine,
            "engine_ready": decision.ready,
            "demo": decision.demo,
            "auth_required": bool(settings.access_token),
            "title_en": decision.title_en,
            "title_ar": decision.title_ar,
            "detail_en": decision.detail_en,
            "detail_ar": decision.detail_ar,
            "speed_en": decision.speed_en,
            "speed_ar": decision.speed_ar,
            "gpu": [
                {"name": gpu.name, "memory_mib": gpu.memory_mib, "driver": gpu.driver}
                for gpu in probe.gpus
            ],
            "vllm_reachable": probe.vllm_reachable,
            "datalab_configured": probe.datalab_configured,
            "hf_installed": probe.hf_installed,
            "lan_urls": phone_urls(settings),
            "chunk_size": settings.chunk_size,
            "max_upload_bytes": settings.max_upload_bytes,
            "max_pages": settings.max_pages,
        }

    @app.post("/api/uploads")
    def start_upload(body: UploadInit) -> dict:
        filename = Path(body.filename).name
        if not filename or not is_supported(Path(filename)):
            raise HTTPException(
                status_code=415,
                detail="Use a PDF or an image: pdf, png, jpeg, webp, tiff, gif, bmp.",
            )
        if body.size > settings.max_upload_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"This file is over the {settings.max_upload_bytes} byte limit.",
            )
        upload_id = uuid.uuid4().hex
        folder = _upload_dir(settings, upload_id)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "chunks").mkdir(exist_ok=True)
        meta = {
            "filename": filename,
            "size": body.size,
            "content_type": body.content_type,
            "chunk_size": settings.chunk_size,
            "parts": _part_count(body.size, settings.chunk_size),
        }
        (folder / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        return {"id": upload_id, **meta}

    @app.put("/api/uploads/{upload_id}/chunks/{index}")
    async def put_chunk(upload_id: str, index: int, request: Request) -> dict:
        meta = _read_meta(settings, upload_id)
        if index < 0 or index >= meta["parts"]:
            raise HTTPException(status_code=400, detail="Chunk index is out of range.")
        dest = _upload_dir(settings, upload_id) / "chunks" / f"{index}.part"
        tmp = dest.with_suffix(".partial")
        size = 0
        try:
            with tmp.open("wb") as handle:
                async for piece in request.stream():
                    size += len(piece)
                    if size > settings.chunk_size:
                        raise HTTPException(status_code=413, detail="Chunk is larger than the agreed size.")
                    handle.write(piece)
        except HTTPException:
            tmp.unlink(missing_ok=True)
            raise
        if size == 0:
            tmp.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail="Chunk is empty.")
        tmp.replace(dest)
        return {"index": index, "size": size}

    @app.post("/api/uploads/{upload_id}/complete")
    def complete_upload(upload_id: str, body: UploadComplete) -> dict:
        meta = _read_meta(settings, upload_id)
        folder = _upload_dir(settings, upload_id)
        assembled = folder / "document"
        # Validate every chunk before opening the output: Windows cannot unlink
        # a file that is still open, so failing mid-write would leak a 500.
        parts = [folder / "chunks" / f"{index}.part" for index in range(meta["parts"])]
        for index, part in enumerate(parts):
            if not part.exists():
                raise HTTPException(status_code=400, detail=f"Missing chunk {index}.")
            if index < meta["parts"] - 1 and part.stat().st_size != meta["chunk_size"]:
                raise HTTPException(status_code=400, detail=f"Chunk {index} is the wrong size.")
        written = 0
        with assembled.open("wb") as handle:
            for part in parts:
                blob = part.read_bytes()
                handle.write(blob)
                written += len(blob)
        if written != meta["size"]:
            assembled.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail="Uploaded bytes do not match the declared size.")
        suffix = Path(meta["filename"]).suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            assembled.unlink(missing_ok=True)
            raise HTTPException(status_code=415, detail="Unsupported file type.")
        decision, _probe = _inspect(settings)
        if not decision.ready or not decision.engine:
            assembled.unlink(missing_ok=True)
            raise HTTPException(
                status_code=409,
                detail={"en": decision.detail_en, "ar": decision.detail_ar, "title_en": decision.title_en},
            )
        job_id = uuid.uuid4().hex
        job_dir = settings.data_dir / "jobs" / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        target = job_dir / f"source{suffix}"
        shutil.move(str(assembled), target)
        shutil.rmtree(folder, ignore_errors=True)
        store.create_job(
            job_id=job_id,
            filename=meta["filename"],
            content_type=meta["content_type"],
            source_path=target,
            include_headers_footers=body.include_headers_footers,
            engine=decision.engine,
        )
        job = store.get_job(job_id)
        assert job is not None
        return _public_job(job)

    @app.get("/api/jobs")
    def list_jobs() -> dict:
        return {"jobs": [_public_job(job) for job in store.list_jobs()]}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict:
        job = store.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="No document with that id.")
        return _public_job(job)

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str) -> dict:
        if store.get_job(job_id) is None:
            raise HTTPException(status_code=404, detail="No document with that id.")
        store.cancel(job_id)
        job = store.get_job(job_id)
        assert job is not None
        return _public_job(job)

    @app.delete("/api/jobs/{job_id}")
    def delete_job(job_id: str) -> dict:
        job = store.delete_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="No document with that id.")
        job_dir = settings.data_dir / "jobs" / job_id
        shutil.rmtree(job_dir, ignore_errors=True)
        return {"deleted": job_id}

    @app.get("/api/jobs/{job_id}/pages/{index}")
    def get_page(job_id: str, index: int) -> dict:
        if store.get_job(job_id) is None:
            raise HTTPException(status_code=404, detail="No document with that id.")
        page = store.get_page(job_id, index)
        if page is None:
            raise HTTPException(status_code=404, detail="That page is not ready.")
        assets = settings.data_dir / "jobs" / job_id / "assets"
        page["html"] = rewrite_asset_urls(page["html"], job_id, assets)
        page.pop("preview_path", None)
        return page

    @app.get("/api/jobs/{job_id}/pages/{index}/preview")
    def preview(job_id: str, index: int) -> FileResponse:
        path = settings.data_dir / "jobs" / job_id / "previews" / f"{index}.jpg"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Preview is not ready.")
        return FileResponse(path, media_type="image/jpeg")

    @app.get("/api/jobs/{job_id}/assets/{name}")
    def asset(job_id: str, name: str) -> FileResponse:
        if not ASSET_NAME.fullmatch(name):
            raise HTTPException(status_code=404, detail="Missing figure.")
        path = settings.data_dir / "jobs" / job_id / "assets" / name
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Missing figure.")
        return FileResponse(path)

    @app.get("/api/jobs/{job_id}/export")
    def export(job_id: str, format: str = "md", page: str = "all", download: int = 1):
        job = store.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="No document with that id.")
        pages = store.list_page_records(job_id)
        if page != "all":
            try:
                wanted = int(page)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="Page must be an index or 'all'.") from exc
            pages = [item for item in pages if item["index"] == wanted]
            if not pages:
                raise HTTPException(status_code=404, detail="That page is not ready.")
        kind = format.lower()
        assets = settings.data_dir / "jobs" / job_id / "assets"
        payload, mime, suffix = _render_export(job, pages, kind, assets, for_download=bool(download))
        stem = Path(job["filename"]).stem or "document"
        if page != "all":
            stem = f"{stem}-page-{int(page) + 1}"
        filename = f"{stem}.{suffix}"
        if not download:
            if isinstance(payload, bytes):
                raise HTTPException(status_code=400, detail="Zip exports are downloads.")
            return {"filename": filename, "mime": mime, "content": payload}
        if isinstance(payload, bytes):
            body = payload
        else:
            body = payload.encode("utf-8")
        return Response(
            content=body,
            media_type=mime,
            headers={"Content-Disposition": _attachment(filename)},
        )

    return app


def _public_job(job: dict) -> dict:
    public = dict(job)
    public.pop("source_path", None)
    return public


def _render_export(job: dict, pages: list[dict], kind: str, assets: Path, for_download: bool):
    if kind in {"md", "markdown"}:
        text = join_parts([page["markdown"] for page in pages], "md")
        return text, "text/markdown; charset=utf-8", "md"
    if kind in {"txt", "text"}:
        text = join_parts([page["plain"] for page in pages], "txt")
        return text, "text/plain; charset=utf-8", "txt"
    if kind == "json":
        text = json.dumps(document_json(job, pages), ensure_ascii=False, indent=2)
        return text, "application/json; charset=utf-8", "json"
    if kind == "html":
        html_parts = []
        for page in pages:
            html = embed_images(page["html"], assets) if for_download else rewrite_asset_urls(page["html"], job["id"], assets)
            html_parts.append(html)
        body = join_parts(html_parts, "html")
        document = (
            "<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
            f"<title>{_escape(job['filename'])}</title>"
            "<style>body{margin:32px;font:16px/1.65 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#1d1d1f}"
            "table{border-collapse:collapse;width:100%}td,th{border:1px solid #e5e5e7;padding:8px 10px;vertical-align:top}"
            "img{max-width:100%;height:auto}section{margin:0 0 28px}</style></head><body>"
            f"{body}</body></html>"
        )
        return document, "text/html; charset=utf-8", "html"
    if kind == "zip":
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for fmt, suffix in (("md", "md"), ("html", "html"), ("json", "json"), ("txt", "txt")):
                content, _mime, _suffix = _render_export(job, pages, fmt, assets, for_download=True)
                archive.writestr(f"{Path(job['filename']).stem}.{suffix}", content)
            if assets.exists():
                for path in assets.iterdir():
                    if path.is_file():
                        archive.write(path, arcname=f"assets/{path.name}")
        return buffer.getvalue(), "application/zip", "zip"
    raise HTTPException(status_code=400, detail="Format must be md, html, json, txt, or zip.")


def _attachment(filename: str) -> str:
    quoted = filename.replace("\\", "\\\\").replace('"', '\\"')
    ascii_name = "".join(ch if ord(ch) < 128 else "_" for ch in filename)
    from urllib.parse import quote

    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(quoted)}"


def _escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _inspect(settings: Settings) -> tuple:
    explicit = settings.chandra_engine.strip().lower()
    probe_vllm = explicit in {"auto", "vllm"}
    probe_hf = explicit in {"auto", "hf"}
    probe = Probe(
        gpus=query_nvidia_smi() if explicit != "mock" else [],
        vllm_reachable=vllm_reachable(settings.vllm_api_base, settings.vllm_api_key) if probe_vllm else False,
        hf_installed=hf_stack_installed() if probe_hf else False,
        datalab_configured=bool(settings.datalab_api_key.strip()),
        explicit=explicit,
        allow_cpu=settings.allow_cpu,
    )
    return decide(probe), probe


def _part_count(size: int, chunk_size: int) -> int:
    return max(1, math.ceil(size / chunk_size))


def _upload_dir(settings: Settings, upload_id: str) -> Path:
    if not UPLOAD_ID.fullmatch(upload_id):
        raise HTTPException(status_code=404, detail="Unknown upload.")
    return settings.data_dir / "uploads" / upload_id


def _read_meta(settings: Settings, upload_id: str) -> dict:
    path = _upload_dir(settings, upload_id) / "meta.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Unknown upload.")
    return json.loads(path.read_text(encoding="utf-8"))


app = create_app()
