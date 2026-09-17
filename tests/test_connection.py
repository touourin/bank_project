import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from neo4j.exceptions import DriverError, ServiceUnavailable

from bank_project.adapters.graph_store.connection import Neo4jConnection

pytestmark = pytest.mark.anyio


@pytest.fixture
def driver(monkeypatch):
    driver = MagicMock()
    driver.close = AsyncMock()
    monkeypatch.setattr(
        "bank_project.adapters.graph_store.connection.AsyncGraphDatabase.driver",
        lambda *args, **kwargs: driver,
    )
    return driver


async def test_unresponsive_database_is_cancelled_and_connection_can_recover(driver):
    cancelled = asyncio.Event()

    async def unresponsive(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    session = driver.session.return_value.__aenter__.return_value
    session.run = AsyncMock(side_effect=unresponsive)
    connection = Neo4jConnection("bolt://test", "test", "test", "neo4j", timeout=0.02)
    try:
        assert await asyncio.wait_for(connection.ready(), timeout=1) is False
        assert cancelled.is_set()
        driver.session.return_value.__aexit__.assert_awaited_once()

        session.run.side_effect = None
        session.run.return_value.consume = AsyncMock()
        assert await connection.ready() is True
    finally:
        await connection.close()
    driver.close.assert_awaited_once()


@pytest.mark.parametrize("error", [ServiceUnavailable("offline"), DriverError("closed")])
async def test_driver_failures_mark_dependency_unready(driver, error):
    session = driver.session.return_value.__aenter__.return_value
    session.run = AsyncMock(side_effect=error)
    connection = Neo4jConnection("bolt://test", "test", "test", "neo4j")
    try:
        assert await connection.ready() is False
    finally:
        await connection.close()
