from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from time import monotonic, sleep
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import select, text
from sqlalchemy.engine import Engine

from app.models import Wallet


def payment_headers(key: str | None = None, demo_client: str = "pytest") -> dict[str, str]:
    return {
        "Idempotency-Key": key if key is not None else uuid4().hex,
        "X-Demo-Client": demo_client,
    }


def create_wallet(client: TestClient, owner: str = "Asha") -> int:
    response = client.post("/api/v1/wallets", json={"owner_name": owner})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def send_funding(
    client: TestClient, wallet_id: int, amount_paise: object, *, key: str | None = None
) -> Response:
    return client.post(
        f"/api/v1/wallets/{wallet_id}/fund",
        json={"amount_paise": amount_paise},
        headers=payment_headers(key),
    )


def fund(client: TestClient, wallet_id: int, amount_paise: int) -> None:
    response = send_funding(client, wallet_id, amount_paise)
    assert response.status_code == 201, response.text


def balance(client: TestClient, wallet_id: int) -> int:
    response = client.get(f"/api/v1/wallets/{wallet_id}/balance")
    assert response.status_code == 200, response.text
    return response.json()["balance_paise"]


def transfer(
    client: TestClient,
    sender: int,
    receiver: int,
    amount: object,
    *,
    key: str | None = None,
    demo_client: str = "pytest",
) -> Response:
    return client.post(
        "/api/v1/transfers",
        headers=payment_headers(key, demo_client),
        json={"sender_wallet_id": sender, "receiver_wallet_id": receiver, "amount_paise": amount},
    )


def wait_for_two_requests(
    engine: Engine, wallet_id: int, first: Callable[[], Response], second: Callable[[], Response]
) -> tuple[Response, Response]:
    with ThreadPoolExecutor(max_workers=2) as workers:
        with engine.begin() as blocker, engine.connect() as monitor:
            blocker.execute(select(Wallet.id).where(Wallet.id == wallet_id).with_for_update())
            futures = (workers.submit(first), workers.submit(second))
            # A fresh stats snapshot per poll confirms genuine overlap in PostgreSQL.
            monitor.execution_options(isolation_level="AUTOCOMMIT")
            deadline = monotonic() + 10
            while monotonic() < deadline:
                waiting = monitor.scalar(
                    text(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE application_name = :name AND wait_event_type = 'Lock'"
                    ),
                    {"name": engine.url.query["application_name"]},
                )
                if waiting == 2:
                    break
                assert not any(future.done() for future in futures), "A request bypassed its lock"
                sleep(0.02)
            else:
                pytest.fail("Both requests did not reach PostgreSQL lock waits.")
        return futures[0].result(timeout=10), futures[1].result(timeout=10)
