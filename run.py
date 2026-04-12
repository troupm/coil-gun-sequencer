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

from app import create_app, socketio

app = create_app()

if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "5000"))

    logging.info(f"Starting SocketIO server on {host}:{port}")
    socketio.run(app, host=host, port=port, allow_unsafe_werkzeug=True)

