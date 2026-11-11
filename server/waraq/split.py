"""Split a whole-document Datalab response back into pages when we can."""

from __future__ import annotations

import re
from typing import Any

# Chandra's own paginated markdown marker, plus a few hosted-API variants.
_MARKED = re.compile(r"\n\{(\d+)\}-{10,}\n")
_RULE = re.compile(
    r"\n+---+\n+\s*(?:page|صفحة)\s+(\d+)\s*\n+",
    re.IGNORECASE,
)
_HTML_RULE = re.compile(
    r"<hr[^>]*>\s*(?:<p[^>]*>)?\s*(?:page|صفحة)?\s*(\d+)\s*(?:</p>)?",
    re.IGNORECASE,
)


def split_marked(text: str) -> dict[int, str] | None:
    if not text:
        return None
    for pattern in (_MARKED, _RULE, _HTML_RULE):
        parsed = _split_with(pattern, text)
        if parsed and len(parsed) > 1:
            return parsed
    return None


def _split_with(pattern: re.Pattern[str], text: str) -> dict[int, str] | None:
    pieces = pattern.split(text)
    if len(pieces) < 3:
        return None
    pages: dict[int, str] = {}
    head = pieces[0].strip()
    if head:
        pages[0] = head
    index = 1
    while index + 1 < len(pieces):
        number = int(pieces[index])
        # Chandra's marker is 0-based ({1} introduces page index 1).
        # "Page N" / "صفحة N" from a hosted response is 1-based.
        if pattern is not _MARKED and number >= 1:
            number -= 1
        pages[number] = pieces[index + 1].strip()
        index += 2
    return pages or None


def pages_from_json(payload: Any) -> dict[int, dict[str, Any]] | None:
    """Group a Marker-style tree by page when the blocks carry a page index."""
    if not isinstance(payload, dict):
        return None
    grouped: dict[int, list[dict[str, Any]]] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            page = node.get("page")
            if page is None:
                page = node.get("page_index")
            if page is None:
                page = node.get("page_id")
            children = node.get("children")
            block_type = str(node.get("block_type") or node.get("label") or "")
            if block_type.lower() == "page" and isinstance(page, int):
                grouped.setdefault(page, [])
                if children:
                    grouped[page].extend(_flatten_blocks(children))
                return
            if isinstance(page, int) and (node.get("html") or node.get("markdown") or node.get("text")):
                grouped.setdefault(page, []).append(node)
            if children:
                walk(children)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload.get("children", payload))
    if not grouped:
        return None
    result: dict[int, dict[str, Any]] = {}
    for page, blocks in grouped.items():
        html = "\n".join(str(block.get("html") or "") for block in blocks if block.get("html"))
        markdown = "\n\n".join(
            str(block.get("markdown") or block.get("text") or "")
            for block in blocks
            if block.get("markdown") or block.get("text")
        )
        result[page] = {"html": html, "markdown": markdown, "chunks": blocks}
    return result


def _flatten_blocks(nodes: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("html") or node.get("markdown") or node.get("text"):
                found.append(node)
            children = node.get("children")
            if children:
                walk(children)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(nodes)
    return found
