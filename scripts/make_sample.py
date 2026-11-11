"""Draw the two-page sample note shipped in web/public/sample-note.pdf.

Page one matches the Arabic memo the demo engine returns. Page two matches the
English follow-up. With a real Chandra engine the model reads the page instead.
"""

from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "web" / "public" / "sample-note.pdf"
AR = ImageFont.truetype("/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf", 40)
AR_BOLD = ImageFont.truetype("/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf", 68)
AR_SMALL = ImageFont.truetype("/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf", 32)
EN = ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf", 34)
EN_BOLD = ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf", 54)
EN_SMALL = ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", 28)

INK = "#1d1d1f"
MUTED = "#6e6e73"
LINE = "#d2d2d7"
PAPER = "#fffcf8"


def page_canvas() -> Image.Image:
    image = Image.new("RGB", (1400, 1860), PAPER)
    draw = ImageDraw.Draw(image)
    draw.rectangle((48, 48, 1352, 1812), outline="#ece7df", width=2)
    return image


def arabic_page() -> Image.Image:
    image = page_canvas()
    draw = ImageDraw.Draw(image)
    draw.text((1260, 120), "مذكرة داخلية", font=AR_BOLD, fill=INK, anchor="ra", direction="rtl", language="ar")
    draw.text((1260, 220), "اجتماع المتابعة · الخميس", font=AR_SMALL, fill=MUTED, anchor="ra", direction="rtl", language="ar")
    lines = [
        "اجتماع المتابعة يوم الخميس في غرفة القراءة.",
        "تُراجع الجداول والحسابات المكتوبة بخط اليد",
        "قبل إرسال النسخة النهائية.",
    ]
    y = 340
    for line in lines:
        draw.text((1260, y), line, font=AR, fill=INK, anchor="ra", direction="rtl", language="ar")
        y += 72
    top = 680
    draw.rectangle((160, top, 1240, top + 64), fill="#f3efe8")
    draw.text((1180, top + 32), "البند", font=AR_BOLD.font_variant(size=36), fill=INK, anchor="rm", direction="rtl", language="ar")
    draw.text((520, top + 32), "المبلغ", font=AR_BOLD.font_variant(size=36), fill=INK, anchor="rm", direction="rtl", language="ar")
    rows = [("ورق وطباعة", "48,000"), ("مراجعة", "12,500")]
    y = top + 64
    for label, amount in rows:
        draw.line((160, y, 1240, y), fill=LINE, width=2)
        draw.text((1180, y + 48), label, font=AR, fill=INK, anchor="rm", direction="rtl", language="ar")
        draw.text((520, y + 48), amount, font=EN, fill=INK, anchor="rm")
        y += 96
    draw.rectangle((160, top, 1240, y), outline=LINE, width=2)
    draw.line((700, top, 700, y), fill=LINE, width=2)
    draw.text((160, 1180), "E  =  mc²", font=EN_BOLD, fill=INK)
    draw.text((1260, 1680), "صفحة ١", font=AR_SMALL, fill=MUTED, anchor="ra", direction="rtl", language="ar")
    return image


def english_page() -> Image.Image:
    image = page_canvas()
    draw = ImageDraw.Draw(image)
    draw.text((140, 130), "Follow-up", font=EN_BOLD, fill=INK)
    draw.text((140, 220), "After the meeting", font=EN_SMALL, fill=MUTED)
    lines = [
        "The handwritten totals on the scan match the",
        "table on the first page. Keep the reading order",
        "of the two columns.",
    ]
    y = 340
    for line in lines:
        draw.text((140, y), line, font=EN, fill=INK)
        y += 58
    items = [
        "Confirm the Arabic heading.",
        "Export markdown and HTML.",
        "Check the formula renders.",
    ]
    y = 620
    for item in items:
        draw.ellipse((140, y + 10, 156, y + 26), fill=INK)
        draw.text((180, y), item, font=EN, fill=INK)
        y += 70
    draw.text((140, 1680), "Page 2", font=EN_SMALL, fill=MUTED)
    return image


def jpeg(image: Image.Image, path: Path) -> None:
    image.save(path, "JPEG", quality=90, optimize=True)


def build_pdf(images: list[Path], dest: Path) -> None:
    document = pdfium.PdfDocument.new()
    try:
        for image_path in images:
            with Image.open(image_path) as probe:
                width, height = probe.size
            page_w = 612
            page_h = 612 * height / width
            image = pdfium.PdfImage.new(document)
            image.load_jpeg(str(image_path), inline=True)
            # PdfImage's base box is 1×1 point. Scale by the page size, not by
            # pixel size, or the scan shrinks to a fraction of a point and the
            # preview is a blank page.
            image.set_matrix(pdfium.PdfMatrix().scale(page_w, page_h))
            page = document.new_page(page_w, page_h)
            page.insert_obj(image)
            page.gen_content()
        document.save(str(dest))
    finally:
        document.close()


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    folder = OUT.parent
    first = folder / "_page1.jpg"
    second = folder / "_page2.jpg"
    jpeg(arabic_page(), first)
    jpeg(english_page(), second)
    build_pdf([first, second], OUT)
    first.unlink()
    second.unlink()
    print(OUT, OUT.stat().st_size)


if __name__ == "__main__":
    main()
