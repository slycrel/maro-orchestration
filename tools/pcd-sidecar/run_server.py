#!/usr/bin/env python3
"""Entrypoint: `python run_server.py` (or `python -m pcd_sidecar.server`).
Reads PCD_* env vars -- see README.md."""
from pcd_sidecar.server import main

if __name__ == "__main__":
    main()
