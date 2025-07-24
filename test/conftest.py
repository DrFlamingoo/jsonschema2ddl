import json
import tempfile
import os

import pytest
from sqlalchemy import create_engine
from testcontainers.postgres import PostgresContainer
import duckdb


@pytest.fixture
def db(scope="session"):
    with PostgresContainer("postgres:11.4") as postgres:
        engine = create_engine(postgres.get_connection_url())
        connection = engine.raw_connection()
        yield {
            "connection": connection,
            "engine": engine,
        }
        connection.close()


@pytest.fixture
def schema():
    with open("test/schema.json") as f:
        schema = json.load(f)
    return schema


@pytest.fixture
def schema_flat():
    with open("test/schema_flat.json") as f:
        schema = json.load(f)
    return schema


@pytest.fixture
def schema_long_names():
    with open("test/schema_long_names.json") as f:
        schema = json.load(f)
    return schema


@pytest.fixture
def schema_refs():
    with open("test/schema_refs.json") as f:
        schema = json.load(f)
    return schema


@pytest.fixture
def schema_time():
    with open("test/schema_time.json") as f:
        schema = json.load(f)
    return schema


@pytest.fixture
def duckdb_db():
    """Create an in-memory DuckDB database for testing"""
    conn = duckdb.connect(":memory:")
    yield {
        "connection": conn,
        "engine": None,  # DuckDB doesn't use SQLAlchemy engine in our tests
    }
    conn.close()
