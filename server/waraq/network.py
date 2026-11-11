import socket

from waraq.config import Settings


def local_ipv4() -> list[str]:
    found: list[str] = []

    def add(ip: str) -> None:
        if ip and ip not in found and not ip.startswith("127."):
            found.append(ip)

    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("8.8.8.8", 80))
        add(probe.getsockname()[0])
        probe.close()
    except OSError:
        pass

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            add(info[4][0])
    except OSError:
        pass
    return found


def phone_urls(settings: Settings) -> list[str]:
    hosts = [h.strip() for h in settings.advertised_hosts.split(",") if h.strip()]
    if not hosts:
        hosts = local_ipv4()
    return [f"http://{host}:{settings.web_port}" for host in hosts]
