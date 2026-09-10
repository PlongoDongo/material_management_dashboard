"""
Neo4j: driver lifecycle.

The one rule you need to know:

    DRIVER  = long-lived, thread-safe, owns the connection pool
              -> EXACTLY ONE per process, created at application start
    SESSION = short-lived, NOT thread-safe
              -> EXACTLY ONE per unit of work (= per request), closed afterwards

A common mistake is building a driver per request: that throws the connection
pool away and turns every request into a fresh TCP plus TLS handshake. The other
common mistake is a process-wide session: it is not thread-safe and produces
sporadic failures under load.

We use the ASYNCHRONOUS driver because FastAPI endpoints are `async def`. With
the synchronous driver the endpoints would have to be plain `def` -- FastAPI
then moves them to a thread pool. Both work, but they must not be mixed: a
blocking driver call inside `async def` blocks the whole event loop and with it
every other request.
"""
from __future__ import annotations

import logging

from neo4j import AsyncDriver, AsyncGraphDatabase

from core.errors import ConfigurationError

log = logging.getLogger(__name__)

Auth = tuple[str, str] | str | None


def _coerce_auth(auth: Auth) -> tuple[str, str] | None:
    """Turns 'user/password' or 'user:password' into a (user, password) tuple.

    Same convention as the dashboard uses -- the same .env fits both.

    A string without a separator is rejected instead of being mapped to None:
    otherwise a typo (`neo4jpassword` instead of `neo4j/password`) would make
    the driver connect *without* authentication, and the resulting AuthError
    would give no hint about the cause.
    """
    if isinstance(auth, tuple):
        return auth
    if isinstance(auth, str) and auth:
        for separator in ("/", ":"):
            if separator in auth:
                user, _, password = auth.partition(separator)
                return (user, password)
        raise ConfigurationError(
            "Neo4j auth as a string must be 'user/password' or 'user:password'."
        )
    return None


async def create_driver(
    uri: str | None,
    auth: Auth,
    *,
    max_connection_pool_size: int = 50,
    connection_acquisition_timeout: float = 2.0,
) -> AsyncDriver | None:
    """Creates the one driver. Without a URI: None.

    In that case every data product that needs the graph reports a configuration
    error and /readyz answers 503.

    The two pool settings are passed in rather than left at the driver's
    defaults so they are visible in core/config.py -- the acquisition timeout in
    particular decides whether a saturated pool fails fast or piles requests up
    until the client gives up.

    `verify_connectivity()` runs at startup on purpose: better for the container
    to fall over immediately than to report "healthy" and fail every request.
    """
    if not uri:
        log.warning("Neo4j host is not configured -- Neo4j inactive.")
        return None

    driver = AsyncGraphDatabase.driver(
        uri,
        auth=_coerce_auth(auth),
        max_connection_pool_size=max_connection_pool_size,
        connection_acquisition_timeout=connection_acquisition_timeout,
    )
    await driver.verify_connectivity()
    log.info("Connected to Neo4j: %s", uri)
    return driver


async def close_driver(driver: AsyncDriver | None) -> None:
    if driver is not None:
        await driver.close()
        log.info("Neo4j driver closed.")
