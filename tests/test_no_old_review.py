"""The old /review machinery is gone and nothing in the default path can import it."""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_the_old_review_module_and_command_are_gone():
    assert not (ROOT / "board" / "review.py").exists()
    assert not (ROOT / ".claude" / "commands" / "review.md").exists()
    assert not (ROOT / ".claude" / "commands" / "scan.md").exists()


def test_no_board_module_imports_a_review_module():
    for path in (ROOT / "board").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"^\s*from \. import .*\breview\b|^\s*from \.review import|^\s*import board\.review|^\s*from board import .*\breview\b",
                             text, re.M), path.name


def test_the_default_run_path_imports_without_loading_a_review_module():
    code = ("import sys; import board.cli, board.pipeline, board.fetch, board.markdown, board.excel, board.applications; "
            "print([m for m in sys.modules if m == 'board.review'])")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and out.stdout.strip() == "[]", out.stderr


def test_the_cli_has_no_review_command():
    out = subprocess.run([sys.executable, "-m", "board", "review", "prepare"], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert out.returncode != 0 and "invalid choice" in out.stderr
