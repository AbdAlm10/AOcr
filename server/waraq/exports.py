from __future__ import annotations

import base64
import io
import json
from pathlib import Path

from PIL import Image

from waraq.textutil import looks_arabic


def join_parts(parts: list[str], kind: str) -> str:
    cleaned = [part.strip() for part in parts]
    if not any(cleaned):
        return ""
    if kind == "html":
        sections = []
        for index, part in enumerate(cleaned):
            if not part:
                continue
            direction = "rtl" if looks_arabic(part) else "auto"
            sections.append(f'<section data-page="{index + 1}" dir="{direction}">{part}</section>')
        return "\n".join(sections)
    blocks = []
    for index, part in enumerate(cleaned):
        if index and len(cleaned) > 1:
            blocks.append("\n\n---\n")
        if part:
            blocks.append(part)
    return "\n".join(blocks).strip() + ("\n" if blocks else "")


def document_json(job: dict, pages: list[dict]) -> dict:
    return {
        "filename": job["filename"],
        "engine": job["engine"],
        "page_count": len(pages),
        "pages": [
            {
                "page": page["index"] + 1,
                "markdown": page["markdown"],
                "html": page["html"],
                "text": page["plain"],
                "chunks": page["chunks"],
                "token_count": page["token_count"],
                "width": page["width"],
                "height": page["height"],
                "error": page["error"],
            }
            for page in pages
        ],
    }


def embed_images(html: str, assets: Path) -> str:
    if not html or not assets.exists():
        return html
    rendered = html
    for path in sorted(assets.iterdir()):
        if not path.is_file():
            continue
        if path.name not in rendered:
            continue
        mime = "image/webp" if path.suffix.lower() == ".webp" else "image/png"
        if path.suffix.lower() in {".jpg", ".jpeg"}:
            mime = "image/jpeg"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        rendered = rendered.replace(path.name, f"data:{mime};base64,{encoded}")
    return rendered


def rewrite_asset_urls(html: str, job_id: str, assets: Path) -> str:
    if not html or not assets.exists():
        return html
    rendered = html
    for path in assets.iterdir():
        if path.is_file() and path.name in rendered:
            rendered = rendered.replace(
                f'src="{path.name}"',
                f'src="/api/jobs/{job_id}/assets/{path.name}"',
            )
    return rendered


def image_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
