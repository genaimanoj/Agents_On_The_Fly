#!/usr/bin/env python3
"""Serve the FlyAgent UI (development server)."""

import http.server
import functools
import os

from flyagent.config import load_config


def main():
    config = load_config()
    port = config.server.ui_port
    ui_dir = os.path.join(os.path.dirname(__file__), "ui")

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=ui_dir)
    server = http.server.HTTPServer(("0.0.0.0", port), handler)
    print(f"FlyAgent UI: http://localhost:{port}")
    print(f"  API expected at: http://localhost:{config.server.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping UI server.")


if __name__ == "__main__":
    main()
