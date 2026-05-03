"""Entry point: `python -m pixelrelay.gateway`."""
from __future__ import annotations

import logging
import os

import uvicorn

from .config import GatewayConfig


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("PIXELRELAY_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Validate config early so misconfig fails fast with a clear error
    GatewayConfig.from_env().validate()

    host = os.environ.get("PIXELRELAY_HOST", "0.0.0.0")
    port = int(os.environ.get("PIXELRELAY_PORT", "8000"))
    workers = int(os.environ.get("PIXELRELAY_WORKERS", "1"))

    uvicorn.run(
        "pixelrelay.gateway.server:create_app",
        factory=True,
        host=host,
        port=port,
        workers=workers,
    )


if __name__ == "__main__":
    main()
