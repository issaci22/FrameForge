"""``python -m frameforge_server`` — run the API server (and built-in node when FF_ROLE=all)."""

from __future__ import annotations

import uvicorn

from .config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "frameforge_server.main:app",
        host=settings.host,
        port=settings.port,
        proxy_headers=True,
        forwarded_allow_ips="*",
        log_config=None,
        ws_ping_interval=20,
        ws_ping_timeout=30,
    )


if __name__ == "__main__":
    main()
