"""Turn a PDF or image into one raster at a time, capped at Chandra's input size."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from PIL import Image

from waraq.config import Settings
from waraq.quality import IMAGE_DPI, MIN_PDF_IMAGE_DIM

SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".tif",
    ".tiff",
    ".bmp",
}

Image.MAX_IMAGE_PIXELS = 250_000_000


class DocumentError(Exception):
    pass


def is_supported(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_EXTENSIONS


def count_pages(path: Path) -> int:
    if _is_pdf(path):
        document = pdfium.PdfDocument(str(path))
        try:
            return len(document)
        finally:
            document.close()
    if path.suffix.lower() == ".gif":
        return 1
    with Image.open(path) as image:
        if image.format == "TIFF":
            return int(getattr(image, "n_frames", 1) or 1)
    return 1


def iter_pages(path: Path, settings: Settings) -> Iterator[tuple[int, Image.Image]]:
    total = count_pages(path)
    if total <= 0:
        raise DocumentError("This file has no pages.")
    if total > settings.max_pages:
        raise DocumentError(
            f"This file has {total} pages. The current limit is {settings.max_pages}. "
            "Raise MAX_PAGES if you really want to read all of them."
        )
    if _is_pdf(path):
        yield from _iter_pdf(path, settings)
        return
    yield from _iter_image(path, settings)


def cap_image(image: Image.Image, max_pixels: int) -> Image.Image:
    image = image.convert("RGB")
    pixels = image.width * image.height
    if pixels <= max_pixels or pixels <= 0:
        return image
    scale = (max_pixels / pixels) ** 0.5
    width = max(1, int(image.width * scale))
    height = max(1, int(image.height * scale))
    resized = image.resize((width, height), Image.Resampling.LANCZOS)
    if resized is not image:
        image.close()
    return resized


def save_preview(image: Image.Image, dest: Path, max_edge: int = 1600) -> None:
    preview = image.copy()
    preview.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
    dest.parent.mkdir(parents=True, exist_ok=True)
    preview.save(dest, "JPEG", quality=82, optimize=True)
    preview.close()


def _is_pdf(path: Path) -> bool:
    if path.suffix.lower() == ".pdf":
        return True
    try:
        with path.open("rb") as handle:
            return handle.read(5) == b"%PDF-"
    except OSError:
        return False


def _flatten(page) -> None:
    try:
        pdfium_c.FPDFPage_Flatten(page, pdfium_c.FLAT_NORMALDISPLAY)
    except Exception:
        return


def _iter_pdf(path: Path, settings: Settings) -> Iterator[tuple[int, Image.Image]]:
    document = pdfium.PdfDocument(str(path))
    try:
        try:
            document.init_forms()
        except Exception:
            pass
        for index in range(len(document)):
            page = document[index]
            width_pt = max(float(page.get_width()), 1.0)
            height_pt = max(float(page.get_height()), 1.0)
            min_dim = min(width_pt, height_pt)
            scale_dpi = max((MIN_PDF_IMAGE_DIM / min_dim) * 72.0, float(IMAGE_DPI))
            width_px = width_pt * scale_dpi / 72.0
            height_px = height_pt * scale_dpi / 72.0
            pixels = width_px * height_px
            if pixels > settings.inference_max_pixels:
                scale_dpi *= (settings.inference_max_pixels / pixels) ** 0.5
            _flatten(page)
            page = document[index]
            rendered = page.render(scale=scale_dpi / 72.0).to_pil().convert("RGB")
            yield index, rendered
    finally:
        document.close()


def _iter_image(path: Path, settings: Settings) -> Iterator[tuple[int, Image.Image]]:
    with Image.open(path) as probe:
        fmt = probe.format
        frames = int(getattr(probe, "n_frames", 1) or 1)
        width, height = probe.size
    if path.suffix.lower() == ".gif":
        frames = 1
    if fmt == "TIFF" and frames > 1:
        yield from _iter_tiff(path, frames, settings)
        return
    if width * height > settings.safe_decode_pixels:
        yield 0, _ffmpeg_frame(path, settings)
        return
    with Image.open(path) as image:
        if path.suffix.lower() == ".gif":
            image.seek(0)
        raster = cap_image(image.convert("RGB"), settings.inference_max_pixels)
    yield 0, raster


def _iter_tiff(path: Path, frames: int, settings: Settings) -> Iterator[tuple[int, Image.Image]]:
    with Image.open(path) as probe:
        probe.seek(0)
        oversized = probe.size[0] * probe.size[1] > settings.safe_decode_pixels
    if oversized:
        if not shutil.which("ffmpeg"):
            raise DocumentError(
                "This TIFF is too large to open safely, and ffmpeg is not installed. "
                "Install ffmpeg, or downscale the scan first."
            )
        with tempfile.TemporaryDirectory(prefix="waraq-tiff-") as temp:
            folder = Path(temp)
            _run_ffmpeg(
                [
                    "ffmpeg",
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    str(path),
                    "-vf",
                    _scale_filter(settings.inference_max_pixels),
                    str(folder / "frame-%05d.jpg"),
                ]
            )
            produced = sorted(folder.glob("frame-*.jpg"))
            if not produced:
                raise DocumentError("Could not read this TIFF.")
            for index, frame_path in enumerate(produced):
                with Image.open(frame_path) as frame:
                    yield index, cap_image(frame.convert("RGB"), settings.inference_max_pixels)
        return

    with Image.open(path) as image:
        for index in range(frames):
            image.seek(index)
            yield index, cap_image(image.convert("RGB"), settings.inference_max_pixels)


def _ffmpeg_frame(path: Path, settings: Settings) -> Image.Image:
    if not shutil.which("ffmpeg"):
        raise DocumentError(
            "This image is too large to open safely, and ffmpeg is not installed. "
            "Install ffmpeg, or downscale the image below about 32 megapixels."
        )
    with tempfile.TemporaryDirectory(prefix="waraq-img-") as temp:
        dest = Path(temp) / "frame.jpg"
        _run_ffmpeg(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(path),
                "-vf",
                _scale_filter(settings.inference_max_pixels),
                "-frames:v",
                "1",
                "-q:v",
                "2",
                str(dest),
            ]
        )
        with Image.open(dest) as frame:
            return cap_image(frame.convert("RGB"), settings.inference_max_pixels)


def _scale_filter(max_pixels: int) -> str:
    # Keep the aspect ratio and stay under Chandra's pixel budget. Even dimensions
    # keep ffmpeg's default yuv pipeline happy.
    return (
        "scale="
        f"w='trunc(min(iw\\,iw*sqrt({max_pixels}/(iw*ih)))/2)*2':"
        f"h='trunc(min(ih\\,ih*sqrt({max_pixels}/(iw*ih)))/2)*2'"
    )


def _run_ffmpeg(command: list[str]) -> None:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DocumentError(f"Could not downscale the image ({exc}).") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise DocumentError(detail or "ffmpeg could not read this image.")
