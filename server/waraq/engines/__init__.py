from waraq.config import Settings
from waraq.engines.base import Engine
from waraq.engines.chandra import ChandraEngine
from waraq.engines.datalab import DatalabEngine
from waraq.engines.mock import MockEngine


def build_engine(name: str, settings: Settings) -> Engine:
    if name == "mock":
        return MockEngine(delay_s=settings.mock_page_delay)
    if name == "datalab":
        return DatalabEngine(settings)
    if name in ("vllm", "hf"):
        return ChandraEngine(name, settings)
    raise RuntimeError(f"No engine named {name}.")
