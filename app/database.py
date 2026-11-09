import os
from collections.abc import Iterator
from typing import Annotated

from dotenv import load_dotenv
from fastapi import Depends, Request
from sqlalchemy import create_engine
from sqlalchemy.engine import URL, Engine, make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import Base


def configured_url(variable: str) -> URL:
    load_dotenv()
    raw_url = os.getenv(variable)
    try:
        url = make_url(raw_url) if raw_url else None
    except (ArgumentError, ValueError):
        url = None
    if url is None or url.drivername != "postgresql+psycopg" or not url.database:
        raise ValueError(f"Set {variable} to a postgresql+psycopg URL with a database name.")
    return url


def make_engine(url: URL) -> Engine:
    return create_engine(
        url,
        isolation_level="READ COMMITTED",
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args={"connect_timeout": 5},
    )


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as session:
        yield session


DatabaseSession = Annotated[Session, Depends(get_session)]


def main() -> None:
    try:
        engine = make_engine(configured_url("DATABASE_URL"))
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    try:
        Base.metadata.create_all(engine)
    except SQLAlchemyError:
        raise SystemExit(
            "Could not initialize tables. Check PostgreSQL and DATABASE_URL."
        ) from None
    finally:
        engine.dispose()
    print("LedgerPay tables are ready.")


if __name__ == "__main__":
    main()
