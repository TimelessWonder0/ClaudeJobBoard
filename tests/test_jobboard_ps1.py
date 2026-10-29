import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(sys.platform != "win32" or not shutil.which("powershell.exe"), reason="needs Windows PowerShell")
def test_jobboard_ps1_helpers():
    proc = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "RemoteSigned", "-File", str(ROOT / "tests" / "jobboard.tests.ps1")],
        capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0 and "ALL PASSED" in proc.stdout, proc.stdout + proc.stderr


def test_jobboard_ps1_is_plain_ascii():
    """Windows PowerShell 5.1 reads BOM-less scripts as ANSI; non-ASCII characters would corrupt them."""
    for name in ("jobboard.ps1", "jobboard.cmd", "tests/jobboard.tests.ps1"):
        data = (ROOT / name).read_bytes()
        assert all(b < 128 for b in data), f"{name} contains non-ASCII bytes"
