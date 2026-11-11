"""Local Chandra via the upstream InferenceManager.

vLLM is the quality path (bfloat16 server, layout prompt). Hugging Face uses
the same prompt and token budget, one page at a time, still in bfloat16 inside
``chandra.model.hf.load_model``.
"""

from __future__ import annotations

import os
from pathlib import Path

from PIL import Image

from waraq.config import Settings
from waraq.engines.base import PageOCR
from waraq.quality import INCLUDE_IMAGES, MIN_IMAGE_DIM, PROMPT_TYPE
from waraq.textutil import html_to_plain, sanitize_html


class ChandraEngine:
    def __init__(self, method: str, settings: Settings):
        if method not in ("vllm", "hf"):
            raise ValueError(method)
        self.name = method
        self._settings = settings
        self._manager = None

    def _model(self):
        if self._manager is None:
            _export_upstream_env(self._settings)
            from chandra.model import InferenceManager

            self._manager = InferenceManager(method=self.name)
        return self._manager

    def ocr_page(
        self,
        image: Image.Image,
        *,
        include_headers_footers: bool,
        page_index: int,
    ) -> PageOCR:
        from chandra.model.schema import BatchInputItem

        prepared = _ensure_min_dim(image, MIN_IMAGE_DIM)
        batch = [BatchInputItem(image=prepared, prompt_type=PROMPT_TYPE)]
        results = self._model().generate(
            batch,
            max_output_tokens=self._settings.max_output_tokens,
            include_images=INCLUDE_IMAGES,
            include_headers_footers=include_headers_footers,
            max_workers=1,
            max_retries=6,
            vllm_api_base=self._settings.vllm_api_base,
        )
        result = results[0]
        if result.error:
            raise RuntimeError("Chandra returned an error for this page.")
        html = sanitize_html(result.html or "")
        markdown = (result.markdown or "").strip()
        chunks = list(result.chunks or [])
        images = dict(result.images or {})
        plain = html_to_plain(html) or markdown
        return PageOCR(
            markdown=markdown,
            html=html,
            plain=plain,
            chunks=chunks,
            token_count=int(result.token_count or 0),
            images=images,
        )

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
        return None


def _ensure_min_dim(image: Image.Image, min_dim: int) -> Image.Image:
    if image.width >= min_dim and image.height >= min_dim:
        return image
    short = min(image.width, image.height)
    if short <= 0:
        return image
    scale = min_dim / short
    resized = image.resize(
        (max(1, int(image.width * scale)), max(1, int(image.height * scale))),
        Image.Resampling.LANCZOS,
    )
    return resized


def _export_upstream_env(settings: Settings) -> None:
    """Chandra reads these names itself. Set them before the first import."""
    os.environ["MODEL_CHECKPOINT"] = settings.model_checkpoint
    os.environ["VLLM_API_BASE"] = settings.vllm_api_base
    os.environ["VLLM_MODEL_NAME"] = settings.vllm_model_name
    os.environ["VLLM_API_KEY"] = settings.vllm_api_key
    os.environ["MAX_OUTPUT_TOKENS"] = str(settings.max_output_tokens)
    if settings.torch_device:
        os.environ["TORCH_DEVICE"] = settings.torch_device
