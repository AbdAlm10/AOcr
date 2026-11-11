"""Hosted fallback. Uses Datalab's accurate convert mode, not the open weights."""

from __future__ import annotations

import base64
import io
import time
from pathlib import Path

import httpx
from PIL import Image

from waraq.config import Settings
from waraq.engines.base import PageOCR
from waraq.split import pages_from_json, split_marked
from waraq.textutil import html_to_plain, sanitize_html


class DatalabEngine:
    name = "datalab"

    def __init__(self, settings: Settings):
        self._settings = settings

    def ocr_page(
        self,
        image: Image.Image,
        *,
        include_headers_footers: bool,
        page_index: int,
    ) -> PageOCR:
        raise RuntimeError("Datalab reads the whole file in one request.")

    def ocr_document(
        self,
        path: Path,
        *,
        filename: str,
        content_type: str,
        page_count: int,
        include_headers_footers: bool,
        on_tick,
    ) -> list[PageOCR] | None:
        if not self._settings.datalab_api_key:
            raise RuntimeError("DATALAB_API_KEY is empty.")
        on_tick("remote", 0, page_count, "Uploading to Datalab")
        payload = self._convert(path, filename, content_type, include_headers_footers, on_tick, page_count)
        return _pages_from_payload(payload, page_count)

    def _convert(
        self,
        path: Path,
        filename: str,
        content_type: str,
        include_headers_footers: bool,
        on_tick,
        page_count: int,
    ) -> dict:
        data = {
            "output_format": "markdown,html,json",
            "mode": "accurate",
            "paginate": "true",
            "disable_html_prettify": "true",
            "exact_transcription": "true",
            "disable_image_extraction": "false",
        }
        if include_headers_footers:
            data["additional_config"] = (
                '{"keep_pageheader_in_output": true, "keep_pagefooter_in_output": true}'
            )
        headers = {"X-API-Key": self._settings.datalab_api_key}
        url = self._settings.datalab_api_base.rstrip("/") + "/convert"
        timeout = httpx.Timeout(120.0, read=300.0)
        with httpx.Client(timeout=timeout) as client:
            with path.open("rb") as handle:
                response = client.post(
                    url,
                    headers=headers,
                    data=data,
                    files={"file": (filename, handle, content_type or "application/octet-stream")},
                )
            response.raise_for_status()
            submitted = response.json()
            if submitted.get("success") is False:
                raise RuntimeError(submitted.get("error") or "Datalab rejected the file.")
            check_url = submitted.get("request_check_url")
            if not check_url:
                raise RuntimeError("Datalab did not return a status URL.")
            deadline = time.time() + self._settings.datalab_poll_timeout_s
            while True:
                if time.time() > deadline:
                    raise RuntimeError("Datalab did not finish before the wait limit.")
                on_tick("remote", 0, page_count, "Datalab is reading the document")
                poll = client.get(check_url, headers=headers)
                poll.raise_for_status()
                body = poll.json()
                status = body.get("status")
                if status == "complete":
                    if body.get("success") is False:
                        raise RuntimeError(body.get("error") or "Datalab could not read this file.")
                    return body
                if status in {"failed", "error"}:
                    raise RuntimeError(body.get("error") or "Datalab failed.")
                time.sleep(2.0)


def _pages_from_payload(payload: dict, page_count: int) -> list[PageOCR]:
    images = _decode_images(payload.get("images") or {})
    markdown = payload.get("markdown") or ""
    html = payload.get("html") or ""
    json_pages = pages_from_json(payload.get("json"))
    md_pages = split_marked(markdown) or {}
    html_pages = split_marked(html) or {}
    count = page_count or int(payload.get("page_count") or 1) or 1
    pages: list[PageOCR] = []
    for index in range(count):
        block = (json_pages or {}).get(index) or {}
        page_html = html_pages.get(index) or block.get("html") or ""
        page_md = md_pages.get(index) or block.get("markdown") or ""
        if count == 1 and not page_html and not page_md:
            page_html = html
            page_md = markdown
        page_html = sanitize_html(page_html)
        chunks = list(block.get("chunks") or [])
        pages.append(
            PageOCR(
                markdown=page_md.strip(),
                html=page_html,
                plain=html_to_plain(page_html) or page_md.strip(),
                chunks=chunks,
                token_count=0,
                images=images if index == 0 else {},
            )
        )
    if count > 1 and not any(page.markdown or page.html or page.plain for page in pages):
        # Keep the document readable even when page markers are missing.
        pages[0] = PageOCR(
            markdown=markdown.strip(),
            html=sanitize_html(html),
            plain=html_to_plain(html) or markdown.strip(),
            chunks=[],
            token_count=0,
            images=images,
        )
    return pages


def _decode_images(raw: dict) -> dict[str, Image.Image]:
    images: dict[str, Image.Image] = {}
    for name, encoded in raw.items():
        if not isinstance(encoded, str) or not name:
            continue
        try:
            blob = base64.b64decode(encoded)
            images[Path(name).name] = Image.open(io.BytesIO(blob)).convert("RGB")
        except Exception:
            continue
    return images
