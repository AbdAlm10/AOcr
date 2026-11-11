import logging

import uvicorn

from waraq.app import _inspect, app
from waraq.config import get_settings
from waraq.network import phone_urls


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = get_settings()
    decision, _probe = _inspect(settings)
    print()
    print("  AOcr")
    print(f"  API on http://{settings.host}:{settings.port}")
    print(f"  Engine: {decision.title_en}")
    if not decision.ready:
        print(f"  {decision.detail_en}")
    for url in phone_urls(settings):
        print(f"  On your phone, open {url}")
    if not phone_urls(settings):
        print(f"  On your phone, open http://<this-computer-lan-ip>:{settings.web_port}")
    print()
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
