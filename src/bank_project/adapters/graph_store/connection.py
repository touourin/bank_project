"""Connection lifecycle only. No graph schema, graph reads or writes."""

import asyncio

from neo4j import READ_ACCESS, AsyncGraphDatabase, Query
from neo4j.exceptions import DriverError, Neo4jError


class Neo4jConnection:
    def __init__(
        self, uri: str, user: str, password: str, database: str, *, timeout: float = 2.0
    ) -> None:
        self._driver = AsyncGraphDatabase.driver(
            uri,
            auth=(user, password),
            connection_timeout=timeout,
            connection_acquisition_timeout=timeout,
            max_transaction_retry_time=0,
        )
        self._database = database
        self._timeout = timeout

    async def ready(self) -> bool:
        try:
            # TCP/pool timeouts do not bound waiting for an established connection's response.
            async with asyncio.timeout(self._timeout):
                async with self._driver.session(
                    database=self._database, default_access_mode=READ_ACCESS
                ) as session:
                    result = await session.run(Query("RETURN 1", timeout=self._timeout))
                    await result.consume()
            return True
        except (Neo4jError, DriverError, TimeoutError):
            return False

    async def close(self) -> None:
        await self._driver.close()
