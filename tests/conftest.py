import os
from collections.abc import Iterator
from uuid import uuid4

import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import URL, Engine
from sqlalchemy.schema import CreateSchema, DropSchema

from app.database import configured_url, make_engine
from app.main import create_app
from app.models import Base


@pytest.fixture(scope="session")
def test_database_url() -> URL:
    load_dotenv()
    if not os.getenv("TEST_DATABASE_URL"):
        pytest.skip("Set TEST_DATABASE_URL to run PostgreSQL integration tests.")

    url = configured_url("TEST_DATABASE_URL")
    if url.database != "ledgerpay_test":
        pytest.fail("TEST_DATABASE_URL must point to the dedicated ledgerpay_test database.")
    if os.getenv("DATABASE_URL") and configured_url("DATABASE_URL").database == url.database:
        pytest.fail("The development URL must not use the ledgerpay_test database.")
    return url


@pytest.fixture
def db_url(test_database_url: URL) -> Iterator[URL]:
    schema = f"test_{uuid4().hex}"
    scoped_url = test_database_url.update_query_dict(
        {"options": f"-csearch_path={schema}", "application_name": schema}
    )
    engine = make_engine(scoped_url)
    try:
        with engine.begin() as connection:
            if connection.scalar(text("SELECT current_database()")) != "ledgerpay_test":
                pytest.fail("Refusing to create a test schema outside ledgerpay_test.")
            connection.execute(CreateSchema(schema))
        try:
            Base.metadata.create_all(engine)
            yield scoped_url
        finally:
            with engine.begin() as connection:
                connection.execute(DropSchema(schema, cascade=True))
    finally:
        engine.dispose()


@pytest.fixture
def client(db_url: URL) -> Iterator[TestClient]:
    with TestClient(create_app(db_url)) as test_client:
        yield test_client


@pytest.fixture
def db(db_url: URL) -> Iterator[Engine]:
    engine = make_engine(db_url)
    try:
        yield engine
    finally:
        engine.dispose()
