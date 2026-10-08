"""Normalize proxy URLs for Telegram and HTTPX without exposing credentials."""

from urllib.parse import urlsplit, urlunsplit


def normalize_proxy(value, *, running_in_docker=False):
    value = str(value or "").strip()
    if not value:
        return None
    parts = urlsplit(value if "://" in value else f"socks5://{value}")
    if parts.scheme == "socks":
        parts = parts._replace(scheme="socks5")
    if parts.scheme not in {"socks5", "socks5h", "socks4", "http", "https"}:
        raise ValueError("Unsupported proxy scheme")
    if not parts.hostname or not parts.port:
        raise ValueError("Proxy must include a host and port")
    if running_in_docker and parts.hostname in {"localhost", "127.0.0.1", "::1"}:
        userinfo = parts.netloc.rsplit("@", 1)[0] + "@" if "@" in parts.netloc else ""
        parts = parts._replace(netloc=f"{userinfo}host.docker.internal:{parts.port}")
    return urlunsplit(parts)
