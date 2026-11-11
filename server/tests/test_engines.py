import sys
import types
from pathlib import Path

from PIL import Image

from waraq.hardware import GpuInfo, Probe, decide, parse_nvidia_csv
from waraq.quality import INFERENCE_MAX_PIXELS, PROMPT_TYPE
from waraq.split import split_marked


def test_nvidia_csv_and_engine_choice():
    text = "NVIDIA GeForce RTX 4090, 24564, 550.54\nNVIDIA T4, 16384, 535.00\n"
    gpus = parse_nvidia_csv(text)
    assert [gpu.memory_mib for gpu in gpus] == [24564, 16384]
    assert gpus[0].name.startswith("NVIDIA")

    assert decide(Probe(explicit="mock")).demo is True

    vllm = decide(Probe(vllm_reachable=True, explicit="auto"))
    assert vllm.engine == "vllm" and vllm.ready

    hf = decide(
        Probe(
            gpus=[GpuInfo("RTX 4090", 24564)],
            hf_installed=True,
            explicit="auto",
        )
    )
    assert hf.engine == "hf" and hf.ready

    hosted = decide(Probe(datalab_configured=True, explicit="auto"))
    assert hosted.engine == "datalab" and hosted.ready

    idle_gpu = decide(
        Probe(
            gpus=[GpuInfo("RTX 4090", 24564)],
            hf_installed=False,
            datalab_configured=True,
            explicit="auto",
        )
    )
    assert idle_gpu.engine == "datalab"
    assert "GPU" in idle_gpu.detail_en

    small = decide(Probe(gpus=[GpuInfo("GTX 1650", 4096)], explicit="auto"))
    assert small.engine is None
    assert "16" in small.detail_en

    nothing = decide(Probe(explicit="auto"))
    assert nothing.ready is False

    cpu_off = decide(Probe(explicit="hf", hf_installed=True))
    assert cpu_off.ready is False
    cpu_on = decide(Probe(explicit="hf", hf_installed=True, allow_cpu=True))
    assert cpu_on.ready is True
    assert cpu_on.engine == "hf"


def test_page_markers_split_chandra_and_hosted_styles():
    marked = "أول\n\n{1}" + ("-" * 48) + "\n\nSecond page"
    pages = split_marked(marked)
    assert pages is not None
    assert "أول" in pages[0]
    assert pages[1] == "Second page"

    ruled = "Alpha\n\n---\n\nPage 2\n\nBeta"
    ruled_pages = split_marked(ruled)
    assert ruled_pages is not None
    assert "Alpha" in ruled_pages[0]
    assert "Beta" in ruled_pages[1]


def test_chandra_engine_uses_layout_prompt_and_token_budget(tmp_path):
    calls: dict = {}

    class Manager:
        def __init__(self, method):
            calls["method"] = method

        def generate(self, batch, **kwargs):
            calls["kwargs"] = kwargs
            calls["prompt"] = batch[0].prompt_type
            calls["image_size"] = batch[0].image.size

            class Result:
                markdown = "read"
                html = "<p>read</p>"
                chunks = [{"label": "Text", "bbox": [0, 0, 1, 1], "content": "read"}]
                token_count = 4
                images = {}
                error = False

            return [Result()]

    schema = types.ModuleType("chandra.model.schema")

    class BatchInputItem:
        def __init__(self, image, prompt_type=None, prompt=None):
            self.image = image
            self.prompt_type = prompt_type

    schema.BatchInputItem = BatchInputItem
    model = types.ModuleType("chandra.model")
    model.InferenceManager = Manager
    package = types.ModuleType("chandra")
    keys = ("chandra", "chandra.model", "chandra.model.schema")
    saved = {key: sys.modules.get(key) for key in keys}
    sys.modules["chandra"] = package
    sys.modules["chandra.model"] = model
    sys.modules["chandra.model.schema"] = schema
    try:
        from waraq.config import Settings
        from waraq.engines.chandra import ChandraEngine

        settings = Settings(
            data_dir=tmp_path,
            chandra_engine="vllm",
            max_output_tokens=12384,
            vllm_api_base="http://127.0.0.1:8000/v1",
        )
        engine = ChandraEngine("vllm", settings)
        image = Image.new("RGB", (40, 40), "white")
        result = engine.ocr_page(image, include_headers_footers=False, page_index=0)
    finally:
        for key, module in saved.items():
            if module is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = module

    assert calls["method"] == "vllm"
    assert calls["prompt"] == PROMPT_TYPE == "ocr_layout"
    assert calls["kwargs"]["max_output_tokens"] == 12384
    assert calls["kwargs"]["include_images"] is True
    assert calls["kwargs"]["max_workers"] == 1
    assert calls["kwargs"]["vllm_api_base"] == "http://127.0.0.1:8000/v1"
    assert min(calls["image_size"]) >= 1536
    assert result.plain == "read"
    assert INFERENCE_MAX_PIXELS == 3072 * 2048


def test_datalab_engine_asks_for_accurate_mode(tmp_path, monkeypatch):
    captured: dict = {}

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, headers, data, files):
            captured["url"] = url
            captured["data"] = data
            captured["header"] = headers["X-API-Key"]
            assert files["file"][1].read(4) == b"fake"
            return FakeResponse({"success": True, "request_check_url": "https://example.test/check"})

        def get(self, url, headers):
            return FakeResponse(
                {
                    "status": "complete",
                    "success": True,
                    "page_count": 2,
                    "markdown": "أول\n\n{1}" + ("-" * 48) + "\n\nSecond",
                    "html": "<p>أول</p>",
                    "json": {"children": []},
                    "images": {},
                }
            )

    monkeypatch.setattr("waraq.engines.datalab.httpx.Client", FakeClient)
    from waraq.config import Settings
    from waraq.engines.datalab import DatalabEngine

    source = tmp_path / "doc.pdf"
    source.write_bytes(b"fake-pdf")
    settings = Settings(
        data_dir=tmp_path,
        datalab_api_key="secret-key",
        datalab_api_base="https://www.datalab.to/api/v1",
    )
    engine = DatalabEngine(settings)
    ticks: list[str] = []
    pages = engine.ocr_document(
        Path(source),
        filename="doc.pdf",
        content_type="application/pdf",
        page_count=2,
        include_headers_footers=True,
        on_tick=lambda phase, done, total, message: ticks.append(phase),
    )
    assert captured["url"].endswith("/convert")
    assert captured["header"] == "secret-key"
    assert captured["data"]["mode"] == "accurate"
    assert captured["data"]["paginate"] == "true"
    assert captured["data"]["exact_transcription"] == "true"
    assert "markdown,html,json" == captured["data"]["output_format"]
    assert "keep_pageheader_in_output" in captured["data"]["additional_config"]
    assert pages is not None
    assert len(pages) == 2
    assert "أول" in pages[0].markdown
    assert pages[1].markdown == "Second"
    assert "remote" in ticks
