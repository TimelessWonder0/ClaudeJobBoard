"""Step 5(c): ask Claude whether a job title is finance-adjacent. Batches of 50, answer keep or drop only.

Used only for titles that matched none of the keyword patterns. Anything uncertain is kept: the funnel is generous on
purpose. Results are cached by title in state/title_class.json, so each distinct title is judged once, ever.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config

SYSTEM = (
    "You sort job titles for a finance candidate. For each numbered job answer keep if the role is finance or "
    "finance-adjacent: finance, FP&A, planning, strategy, business operations, pricing, deal desk, revenue, treasury, "
    "investor relations, investment, equity or credit research, valuation, corporate development, M&A, capital markets, "
    "risk, commercial or business analytics that supports a finance team. Answer drop for everything else (engineering, "
    "operations on the floor, sales, marketing, HR, legal, clinical, retail, logistics, design, general IT). When "
    "unsure, answer keep. Reply with ONLY a JSON object mapping each number to \"keep\" or \"drop\"."
)


def _cache_path(state_dir: Path | None = None) -> Path:
    return (state_dir or config.state_dir()) / "title_class.json"


def load_cache(state_dir: Path | None = None) -> dict:
    try:
        return json.loads(_cache_path(state_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_cache(cache: dict, state_dir: Path | None = None) -> None:
    path = _cache_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=0), encoding="utf-8")
    os.replace(tmp, path)


def norm_key(title: str, department: str = "") -> str:
    return re.sub(r"\s+", " ", f"{title}|{department}".lower()).strip()


def _ask(batch: list[tuple[str, str]], cfg: dict) -> dict[str, str]:
    """batch: [(key, display)]. Returns {key: keep|drop}; missing or unreadable answers are simply absent."""
    claude = shutil.which("claude")
    if not claude:
        return {}
    lines = "\n".join(f"{i}. {disp}" for i, (_k, disp) in enumerate(batch, 1))
    args = [claude, "-p", lines, "--system-prompt", SYSTEM, "--model", cfg.get("model", "haiku"),
            "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence"]
    # A neutral working folder keeps this project's CLAUDE.md and commands out of a one-line classification.
    with tempfile.TemporaryDirectory() as tmp:
        try:
            proc = subprocess.run(args, cwd=tmp, capture_output=True, text=True, encoding="utf-8",
                                  timeout=int(cfg.get("timeout_seconds", 240)))
        except (subprocess.TimeoutExpired, OSError):
            return {}
    m = re.search(r"\{.*\}", proc.stdout or "", re.S)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return {}
    out: dict[str, str] = {}
    for i, (key, _disp) in enumerate(batch, 1):
        v = str(data.get(str(i), "")).strip().lower()
        if v in ("keep", "drop"):
            out[key] = v
    return out


def classify_titles(items: list[tuple[str, str]], cache: dict, cfg: dict) -> tuple[dict[str, str], dict]:
    """items: [(cache_key, "Title - Company")]. Returns ({key: keep|drop}, stats). Updates `cache` in place."""
    result: dict[str, str] = {}
    todo: list[tuple[str, str]] = []
    seen: set[str] = set()
    for key, disp in items:
        if key in cache:
            result[key] = cache[key]
        elif key not in seen:
            seen.add(key)
            todo.append((key, disp))
    size = int(cfg.get("batch_size", 50))
    batches = [todo[i:i + size] for i in range(0, len(todo), size)]
    cap = int(cfg.get("max_calls_per_run", 300))
    skipped = batches[cap:]
    batches = batches[:cap]
    failed = 0
    if batches:
        with ThreadPoolExecutor(max_workers=max(1, int(cfg.get("workers", 4)))) as pool:
            for batch, got in zip(batches, pool.map(lambda b: _ask(b, cfg), batches)):
                if not got:
                    failed += 1
                for key, verdict in got.items():
                    cache[key] = verdict
                    result[key] = verdict
    stats = {"titles": len(seen), "cached": len(seen) - len(todo), "calls": len(batches), "failed_calls": failed,
             "uncapped_titles": sum(len(b) for b in skipped)}
    return result, stats
