import pytest

import store


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Point the job store at a throwaway SQLite file for one test."""
    path = tmp_path / "verticlip-test.db"
    monkeypatch.setattr(store, "DB_PATH", path)
    return path
