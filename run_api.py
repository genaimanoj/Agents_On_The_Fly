#!/usr/bin/env python3
"""Start the FlyAgent API server."""

import uvicorn
from flyagent.config import load_config


def main():
    config = load_config()
    print(f"Starting FlyAgent API on http://{config.server.host}:{config.server.port}")
    print(f"  Docs: http://localhost:{config.server.port}/docs")
    uvicorn.run(
        "api.app:app",
        host=config.server.host,
        port=config.server.port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
