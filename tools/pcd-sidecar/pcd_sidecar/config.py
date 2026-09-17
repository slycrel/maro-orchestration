"""Env-var configuration. Every knob has a documented default; see README.md."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "")


@dataclass
class Config:
    model_id: str
    device: str
    dtype: Optional[str]
    port: int
    threads: Optional[int]
    pmi_enabled: bool
    backend: str


def load_config() -> Config:
    threads_raw = os.environ.get("PCD_THREADS")
    return Config(
        model_id=os.environ.get("PCD_MODEL", "Qwen/Qwen2.5-1.5B-Instruct"),
        device=os.environ.get("PCD_DEVICE", "auto"),
        dtype=os.environ.get("PCD_DTYPE") or None,
        port=int(os.environ.get("PCD_PORT", "8765")),
        threads=int(threads_raw) if threads_raw else None,
        # off by default (2026-09-17): on the evaluation's benign documents PMI
        # was the most over-asserting rule; the replay is how it earns it back
        pmi_enabled=_bool_env("PCD_PMI", False),
        backend=os.environ.get("PCD_BACKEND", "torch"),
    )
