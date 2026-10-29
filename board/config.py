"""Paths and YAML config for the board."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"


def data_root() -> Path:
    """career-ops data root (CAREER_OPS_ROOT), default C:\\ClaudeJobBoard\\career-ops-data."""
    env = os.environ.get("CAREER_OPS_ROOT")
    return Path(env) if env else ROOT / "career-ops-data"


def history_path() -> Path:
    return data_root() / "data" / "scan-history.tsv"


def career_ops_dir() -> Path:
    return ROOT / "career-ops"


def jobs_dir() -> Path:
    return ROOT / "jobs"


def output_dir() -> Path:
    """Excel workbooks and the run JSON."""
    return ROOT / "output"


def state_dir() -> Path:
    """Stable ID ledger, review scores, overrides, last scan receipt."""
    return ROOT / "state"


def applications_dir() -> Path:
    return ROOT / "applications"


def load_yaml(name: str) -> dict:
    path = CONFIG_DIR / name
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@lru_cache(maxsize=None)
def filters_config() -> dict:
    return load_yaml("filters.yaml")


@lru_cache(maxsize=None)
def scoring_config() -> dict:
    return load_yaml("scoring.yaml")


@lru_cache(maxsize=None)
def companies_config() -> list[dict]:
    return load_yaml("companies.yaml").get("companies", [])
