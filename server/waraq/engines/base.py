from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from PIL import Image


@dataclass
class PageOCR:
    markdown: str
    html: str
    plain: str
    chunks: list[dict]
    token_count: int
    images: dict[str, Image.Image] = field(default_factory=dict)
    error: str | None = None


class Engine(Protocol):
    name: str

    def ocr_page(
        self,
        image: Image.Image,
        *,
        include_headers_footers: bool,
        page_index: int,
    ) -> PageOCR: ...

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
        """Return pages for engines that read the whole file remotely.

        Local engines return None so the worker can call ``ocr_page``.
        """
