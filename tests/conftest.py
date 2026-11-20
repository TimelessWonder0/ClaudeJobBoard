"""Every test gets its own state folder, so nothing a test writes (reviews.json, ids.json) reaches the real state/."""
import pytest


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path_factory, monkeypatch):
    from board import config
    d = tmp_path_factory.mktemp("state")
    monkeypatch.setattr(config, "state_dir", lambda: d)
