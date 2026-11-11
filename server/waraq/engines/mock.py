import time
from pathlib import Path

from PIL import Image

from waraq.engines.base import PageOCR
from waraq.textutil import html_to_plain


def _page_html(page_index: int, include_headers: bool) -> str:
    header = ""
    if include_headers:
        header = (
            '<div data-bbox="40 10 960 40" data-label="Page-Header">'
            "<small>AOcr · sample</small></div>"
        )
    if page_index == 0:
        body = """
<div data-bbox="48 48 960 140" data-label="Section-Header"><h1>مذكرة داخلية</h1></div>
<div data-bbox="48 150 960 360" data-label="Text"><p>اجتماع المتابعة يوم الخميس في غرفة القراءة. تُراجع الجداول والحسابات المكتوبة بخط اليد قبل إرسال النسخة النهائية.</p></div>
<div data-bbox="48 380 960 620" data-label="Table"><table><thead><tr><th>البند</th><th>المبلغ</th></tr></thead><tbody><tr><td>ورق وطباعة</td><td>48,000</td></tr><tr><td>مراجعة</td><td>12,500</td></tr></tbody></table></div>
<div data-bbox="48 640 960 780" data-label="Equation-Block"><p><math display="block">E = mc^2</math></p></div>
""".strip()
    elif page_index == 1:
        body = """
<div data-bbox="48 48 960 140" data-label="Section-Header"><h1>Follow-up</h1></div>
<div data-bbox="48 160 960 420" data-label="Text"><p>The handwritten totals on the scan match the table on the first page. Keep the reading order of the two columns.</p></div>
<div data-bbox="48 440 960 640" data-label="List-Group"><ul><li>Confirm the Arabic heading.</li><li>Export markdown and HTML.</li><li>Check the formula renders.</li></ul></div>
""".strip()
    else:
        body = (
            f'<div data-bbox="48 48 960 200" data-label="Text"><p>Sample page {page_index + 1}. '
            "In demo mode this text is not read from the image.</p></div>"
        )
    return header + body


class MockEngine:
    name = "mock"

    def __init__(self, delay_s: float = 0.0):
        self.delay_s = delay_s

    def ocr_page(
        self,
        image: Image.Image,
        *,
        include_headers_footers: bool,
        page_index: int,
    ) -> PageOCR:
        if self.delay_s:
            time.sleep(self.delay_s)
        html = _page_html(page_index, include_headers_footers)
        markdown = _markdown(page_index, include_headers_footers)
        return PageOCR(
            markdown=markdown,
            html=html,
            plain=html_to_plain(html),
            chunks=[
                {
                    "label": "Text",
                    "bbox": [48, 48, min(image.width, 960), min(image.height, 200)],
                    "content": html_to_plain(html)[:240],
                }
            ],
            token_count=max(1, len(markdown.split())),
            images={},
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


def _markdown(page_index: int, include_headers: bool) -> str:
    header = "AOcr · sample\n\n" if include_headers else ""
    if page_index == 0:
        return (
            header
            + "# مذكرة داخلية\n\n"
            + "اجتماع المتابعة يوم الخميس في غرفة القراءة. "
            + "تُراجع الجداول والحسابات المكتوبة بخط اليد قبل إرسال النسخة النهائية.\n\n"
            + "| البند | المبلغ |\n| --- | --- |\n| ورق وطباعة | 48,000 |\n| مراجعة | 12,500 |\n\n"
            + "$$\nE = mc^2\n$$\n"
        )
    if page_index == 1:
        return (
            header
            + "# Follow-up\n\n"
            + "The handwritten totals on the scan match the table on the first page. "
            + "Keep the reading order of the two columns.\n\n"
            + "- Confirm the Arabic heading.\n"
            + "- Export markdown and HTML.\n"
            + "- Check the formula renders.\n"
        )
    return header + f"Sample page {page_index + 1}. In demo mode this text is not read from the image.\n"
