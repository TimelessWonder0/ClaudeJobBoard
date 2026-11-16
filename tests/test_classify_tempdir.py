"""Regression: the 2026-11-10 run crashed in board/classify.py because cleaning up the temp cwd raised
PermissionError (WinError 32) while the claude process still held the folder."""
import shutil
import subprocess

from board import classify


def _fake_claude(monkeypatch, run):
    monkeypatch.setattr(shutil, "which", lambda name: "claude")
    monkeypatch.setattr(subprocess, "run", run)


def test_cleanup_lock_does_not_crash(monkeypatch):
    def run(args, cwd, **kw):
        return subprocess.CompletedProcess(args, 0, stdout='{"1": "keep"}', stderr="")

    def locked_rmtree(path, ignore_errors=False, **kw):
        if not ignore_errors:
            raise PermissionError(32, "The process cannot access the file because it is being used by another process")

    _fake_claude(monkeypatch, run)
    monkeypatch.setattr(classify.shutil, "rmtree", locked_rmtree)
    assert classify._ask([("k", "Analyst - Acme")], {}) == {"k": "keep"}


def test_temp_dir_removed_after_success_and_timeout(monkeypatch):
    seen = []

    def ok(args, cwd, **kw):
        seen.append(cwd)
        return subprocess.CompletedProcess(args, 0, stdout='{"1": "drop"}', stderr="")

    _fake_claude(monkeypatch, ok)
    assert classify._ask([("k", "Cook - Acme")], {}) == {"k": "drop"}

    def timeout(args, cwd, **kw):
        seen.append(cwd)
        raise subprocess.TimeoutExpired(args, 1)

    _fake_claude(monkeypatch, timeout)
    assert classify._ask([("k", "Cook - Acme")], {}) == {}
    from pathlib import Path
    assert seen and not any(Path(p).exists() for p in seen)
