from __future__ import annotations

import fakeredis
import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.common.config import Settings
from app.common.run_store import RunStore

VACUUM_FLASK = "Stainless steel vacuum flask with a moulded plastic outer body."


@pytest.fixture
def redis_server():
    """Set ``redis_server.connected = False`` to simulate a Redis outage."""
    return fakeredis.FakeServer()


@pytest.fixture
def redis_client(redis_server):
    return fakeredis.FakeRedis(server=redis_server, decode_responses=True)


@pytest.fixture
def settings() -> Settings:
    return Settings(redis_url="redis://unused", rate_limit_per_minute=5, run_wall_clock_timeout_s=5,
                    llm_backoff_base_s=0, llm_backoff_max_s=0, tool_timeout_s=1)


@pytest.fixture
def store(redis_client, settings) -> RunStore:
    return RunStore(redis_client, settings.run_ttl_seconds)


@pytest.fixture
def client(settings, redis_client) -> TestClient:
    return TestClient(create_app(settings, redis_client))
