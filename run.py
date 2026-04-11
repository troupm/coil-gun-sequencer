#!/usr/bin/env python3
"""Entry point for the Coil-Gun Sequencer application."""

import logging
import os
import sys

# Ensure the project root is on sys.path
sys.path.insert(0, os.path.dirname(__file__))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

from app import create_app

app = create_app()

if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "5000"))

    # Use waitress if available (production-grade, works on Windows + Linux),
    # otherwise fall back to Flask's built-in threaded server.
    try:
        from waitress import serve
        logging.info(f"Starting waitress on {host}:{port}")
        serve(app, host=host, port=port, threads=8)
    except ImportError:
        logging.info(f"Starting Flask dev server on {host}:{port}")
        app.run(host=host, port=port, threaded=True, debug=False)
