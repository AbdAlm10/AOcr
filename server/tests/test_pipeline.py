import io
import zipfile
from pathlib import Path

from PIL import Image

from tests.helpers import upload, wait_job, write_pdf, write_png
from waraq.pages import iter_pages


def test_chunked_upload_runs_every_page_and_exports(client, tmp_path):
    pdf_path = tmp_path / "notes.pdf"
    write_pdf(pdf_path, 3)
    data = pdf_path.read_bytes()
    assert len(data) > 256

    job_id = upload(client, data, "notes.pdf", reverse_chunks=True)
    job = wait_job(client, job_id)

    assert job["status"] == "complete"
    assert job["engine"] == "mock"
    assert job["pages_total"] == 3
    assert job["pages_done"] == 3
    assert len(job["pages"]) == 3
    assert all(page["has_preview"] and page["ready"] for page in job["pages"])

    stored = list(tmp_path.glob("jobs/*/source.pdf"))
    assert len(stored) == 1
    assert stored[0].read_bytes() == data

    page0 = client.get(f"/api/jobs/{job_id}/pages/0")
    page1 = client.get(f"/api/jobs/{job_id}/pages/1")
    assert page0.status_code == 200
    assert "مذكرة داخلية" in page0.json()["plain"]
    assert "Follow-up" in page1.json()["plain"]

    preview = client.get(f"/api/jobs/{job_id}/pages/0/preview")
    assert preview.status_code == 200
    assert preview.headers["content-type"].startswith("image/jpeg")
    with Image.open(io.BytesIO(preview.content)) as image:
        assert image.width > 0 and image.height > 0

    for fmt, needle in (
        ("md", "مذكرة داخلية"),
        ("txt", "اجتماع المتابعة"),
        ("html", "data-page=\"1\""),
        ("json", "\"page\": 1"),
    ):
        exported = client.get(f"/api/jobs/{job_id}/export", params={"format": fmt, "download": 0})
        assert exported.status_code == 200, exported.text
        assert needle in exported.json()["content"]

    whole = client.get(f"/api/jobs/{job_id}/export", params={"format": "md", "download": 0})
    assert "Follow-up" in whole.json()["content"]
    one_page = client.get(
        f"/api/jobs/{job_id}/export",
        params={"format": "md", "page": 1, "download": 0},
    )
    assert "Follow-up" in one_page.json()["content"]
    assert "مذكرة داخلية" not in one_page.json()["content"]

    archive = client.get(f"/api/jobs/{job_id}/export", params={"format": "zip"})
    assert archive.status_code == 200
    with zipfile.ZipFile(io.BytesIO(archive.content)) as zipped:
        names = set(zipped.namelist())
    assert names == {"notes.md", "notes.html", "notes.json", "notes.txt"}


def test_headers_flag_and_image_cap(client, tmp_path, monkeypatch):
    png = tmp_path / "scan.png"
    write_png(png, (900, 700))
    job_id = upload(client, png.read_bytes(), "scan.png", headers=True)
    job = wait_job(client, job_id)
    assert job["status"] == "complete"
    assert job["pages_total"] == 1
    text = client.get(f"/api/jobs/{job_id}/export", params={"format": "md", "download": 0})
    assert "AOcr · sample" in text.json()["content"]
    page = client.get(f"/api/jobs/{job_id}/pages/0").json()
    assert page["width"] * page["height"] <= 6_291_456


def test_oversized_image_is_capped_before_ocr(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "desk"))
    monkeypatch.setenv("CHANDRA_ENGINE", "mock")
    monkeypatch.setenv("ACCESS_TOKEN", "")
    monkeypatch.setenv("CHUNK_SIZE", "2048")
    monkeypatch.setenv("SAFE_DECODE_PIXELS", "100000")
    monkeypatch.setenv("INFERENCE_MAX_PIXELS", "200000")
    from waraq.config import get_settings

    get_settings.cache_clear()
    from fastapi.testclient import TestClient
    from waraq.app import create_app

    with TestClient(create_app()) as client:
        png = tmp_path / "huge.png"
        write_png(png, (800, 800))
        job_id = upload(client, png.read_bytes(), "huge.png")
        job = wait_job(client, job_id)
    get_settings.cache_clear()
    assert job["status"] == "complete", job
    page = job["pages"][0]
    assert page["width"] * page["height"] <= 200_000
    assert page["width"] < 800


def test_missing_chunk_is_rejected(client, tmp_path):
    png = tmp_path / "one.png"
    write_png(png, (200, 120))
    data = png.read_bytes()
    started = client.post(
        "/api/uploads",
        json={"filename": "one.png", "size": len(data), "content_type": "image/png"},
    )
    info = started.json()
    if info["parts"] > 1:
        client.put(f"/api/uploads/{info['id']}/chunks/0", content=data[: info["chunk_size"]])
    finished = client.post(f"/api/uploads/{info['id']}/complete", json={})
    assert finished.status_code == 400
    assert "Missing chunk" in finished.json()["detail"]


def test_unsupported_type_and_page_limit(client, tmp_path, monkeypatch):
    rejected = client.post(
        "/api/uploads",
        json={"filename": "notes.docx", "size": 10, "content_type": "application/octet-stream"},
    )
    assert rejected.status_code == 415

    monkeypatch.setenv("DATA_DIR", str(tmp_path / "limited"))
    monkeypatch.setenv("MAX_PAGES", "1")
    monkeypatch.setenv("CHANDRA_ENGINE", "mock")
    monkeypatch.setenv("ACCESS_TOKEN", "")
    from waraq.config import get_settings

    get_settings.cache_clear()
    from fastapi.testclient import TestClient
    from waraq.app import create_app

    with TestClient(create_app()) as limited:
        pdf = tmp_path / "long.pdf"
        write_pdf(pdf, 2)
        job_id = upload(limited, pdf.read_bytes(), "long.pdf")
        job = wait_job(limited, job_id)
    get_settings.cache_clear()
    assert job["status"] == "failed"
    assert "limit" in job["error"].lower()


def test_pages_are_read_one_at_a_time(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CHANDRA_ENGINE", "mock")
    monkeypatch.setenv("ACCESS_TOKEN", "")
    monkeypatch.setenv("CHUNK_SIZE", "4096")
    from waraq.config import get_settings
    from waraq.engines.base import PageOCR

    get_settings.cache_clear()
    state = {"current": 0, "peak": 0}

    class RecordingEngine:
        name = "mock"

        def ocr_document(self, *args, **kwargs):
            return None

        def ocr_page(self, image, **kwargs):
            state["current"] += 1
            state["peak"] = max(state["peak"], state["current"])
            try:
                return PageOCR(
                    markdown=f"page {kwargs['page_index']}",
                    html=f"<p>page {kwargs['page_index']}</p>",
                    plain=f"page {kwargs['page_index']}",
                    chunks=[],
                    token_count=1,
                )
            finally:
                state["current"] -= 1

    from fastapi.testclient import TestClient
    from waraq.app import create_app

    with TestClient(create_app(engine_factory=lambda name: RecordingEngine())) as client:
        pdf = tmp_path / "three.pdf"
        write_pdf(pdf, 3)
        job_id = upload(client, pdf.read_bytes(), "three.pdf")
        job = wait_job(client, job_id)
    get_settings.cache_clear()
    assert job["status"] == "complete"
    assert state["peak"] == 1


def test_cancel_between_pages(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CHANDRA_ENGINE", "mock")
    from waraq.config import get_settings
    from waraq.engines.base import PageOCR
    from waraq.store import Store
    from waraq.worker import Worker

    get_settings.cache_clear()
    settings = get_settings()
    store = Store(settings)
    pdf = tmp_path / "cancel.pdf"
    write_pdf(pdf, 3)

    class StoppingEngine:
        name = "mock"

        def ocr_document(self, *args, **kwargs):
            return None

        def ocr_page(self, image, **kwargs):
            if kwargs["page_index"] == 0:
                store.cancel(job_id)
            return PageOCR("x", "<p>x</p>", "x", [], 1)

    job_id = store.create_job(
        filename="cancel.pdf",
        content_type="application/pdf",
        source_path=pdf,
        include_headers_footers=False,
        engine="mock",
    )
    worker = Worker(settings, store, engine_factory=lambda name: StoppingEngine())
    worker.process(job_id)
    job = store.get_job(job_id)
    store.close()
    get_settings.cache_clear()
    assert job["status"] == "cancelled"
    assert job["pages_done"] == 1
    assert job["pages_total"] == 3


def test_sample_note_preview_is_not_blank():
    pdf = Path(__file__).resolve().parents[2] / "web" / "public" / "sample-note.pdf"
    index, image = next(iter_pages(pdf, client_settings()))
    assert index == 0
    dark = sum(1 for pixel in image.convert("L").get_flattened_data() if pixel < 80)
    image.close()
    assert dark > 1000


def client_settings():
    from waraq.config import Settings

    return Settings(
        data_dir=Path("/tmp/waraq-sample-preview"),
        max_pages=5,
        inference_max_pixels=8_000_000,
        safe_decode_pixels=32_000_000,
    )
