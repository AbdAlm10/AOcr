from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image


def write_pdf(path: Path, pages: int) -> None:
    document = pdfium.PdfDocument.new()
    for _ in range(pages):
        document.new_page(612, 792)
    document.save(str(path))
    document.close()


def write_png(path: Path, size: tuple[int, int] = (640, 800)) -> None:
    image = Image.new("RGB", size, "white")
    image.save(path, "PNG")
    image.close()


def upload(client, data: bytes, name: str, *, headers: bool = False, reverse_chunks: bool = False) -> str:
    started = client.post(
        "/api/uploads",
        json={"filename": name, "size": len(data), "content_type": "application/octet-stream"},
    )
    assert started.status_code == 200, started.text
    info = started.json()
    chunk_size = info["chunk_size"]
    order = list(range(info["parts"]))
    if reverse_chunks:
        order.reverse()
    for index in order:
        blob = data[index * chunk_size : (index + 1) * chunk_size]
        sent = client.put(
            f"/api/uploads/{info['id']}/chunks/{index}",
            content=blob,
        )
        assert sent.status_code == 200, sent.text
    finished = client.post(
        f"/api/uploads/{info['id']}/complete",
        json={"include_headers_footers": headers},
    )
    assert finished.status_code == 200, finished.text
    return finished.json()["id"]


def wait_job(client, job_id: str, timeout: float = 30) -> dict:
    import time

    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        response = client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200, response.text
        last = response.json()
        if last["status"] in {"complete", "failed", "cancelled"}:
            return last
        time.sleep(0.05)
    raise AssertionError(f"job did not finish: {last}")
