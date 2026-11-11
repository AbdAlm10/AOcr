import re

from bs4 import BeautifulSoup

ARABIC_RE = re.compile(r"[\u0600-\u06FF]")


def sanitize_html(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup.find_all(["script", "iframe", "object", "embed", "link", "meta", "style"]):
        tag.decompose()
    for tag in soup.find_all(True):
        for attribute in list(tag.attrs):
            name = attribute.lower()
            value = tag.attrs.get(attribute)
            if name.startswith("on"):
                del tag.attrs[attribute]
                continue
            if name in {"src", "href"} and isinstance(value, str):
                if value.strip().lower().startswith("javascript:"):
                    del tag.attrs[attribute]
    return "".join(str(child) for child in soup.contents) if soup.contents else ""


def html_to_plain(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    text = soup.get_text("\n", strip=True)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def looks_arabic(text: str) -> bool:
    arabic = len(ARABIC_RE.findall(text or ""))
    latin = len(re.findall(r"[A-Za-z]", text or ""))
    return arabic > latin and arabic > 0
