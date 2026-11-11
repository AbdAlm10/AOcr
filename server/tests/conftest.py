import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CHANDRA_ENGINE", "mock")
    monkeypatch.setenv("ACCESS_TOKEN", "")
    monkeypatch.setenv("DATALAB_API_KEY", "")
    monkeypatch.setenv("CHUNK_SIZE", "256")
    monkeypatch.setenv("MOCK_PAGE_DELAY", "0")
    monkeypatch.setenv("MAX_PAGES", "5000")
    from waraq.config import get_settings

    get_settings.cache_clear()
    from waraq.app import create_app

    application = create_app()
    with TestClient(application) as test_client:
        yield test_client
    get_settings.cache_clear()
