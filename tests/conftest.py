import pytest
import asyncio
import os
from rss_sidecar import models as m


@pytest.fixture
async def db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(m, "DB_PATH", db_path)
    await m.init_db()
    yield db_path
