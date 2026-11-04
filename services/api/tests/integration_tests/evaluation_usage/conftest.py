"""Use a psycopg-compatible Windows loop for these explicit isolated PG tests."""
import asyncio
import os


def pytest_asyncio_loop_factories(config, item):
    return {"selector": asyncio.SelectorEventLoop} if os.name == "nt" else None
