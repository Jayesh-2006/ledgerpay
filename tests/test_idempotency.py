import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.engine import URL, Engine

from app.main import create_app
from app.models import IdempotencyRecord, Transfer, Wallet
from tests.helpers import (
    balance,
    create_wallet,
    fund,
    payment_headers,
    send_funding,
    transfer,
    wait_for_two_requests,
)


def test_funding_replays_original_balance_after_restart(db_url: URL) -> None:
    with TestClient(create_app(db_url)) as client:
        wallet = create_wallet(client)
        original = send_funding(client, wallet, 100, key="first-funding")
        assert original.status_code == 201
        fund(client, wallet, 200)

    with TestClient(create_app(db_url)) as client:
        replay = send_funding(client, wallet, 100, key="first-funding")
        assert replay.status_code == 201
        assert replay.json() == original.json()
        assert replay.json()["balance_paise"] == 100
        assert balance(client, wallet) == 300
        assert len(client.get(f"/api/v1/wallets/{wallet}/transactions").json()) == 2


def test_funding_key_conflicts_with_changed_wallet_or_amount(client: TestClient) -> None:
    first_wallet, second_wallet = create_wallet(client), create_wallet(client)
    assert send_funding(client, first_wallet, 100, key="fund-once").status_code == 201
    changed_amount = send_funding(client, first_wallet, 101, key="fund-once")
    changed_wallet = send_funding(client, second_wallet, 100, key="fund-once")
    for response in (changed_amount, changed_wallet):
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "idempotency_conflict"
    assert balance(client, first_wallet) == 100
    assert balance(client, second_wallet) == 0


def test_transfer_replay_uses_canonical_payload(client: TestClient) -> None:
    sender, receiver = create_wallet(client), create_wallet(client)
    fund(client, sender, 1000)
    original = transfer(client, sender, receiver, 100, key="transfer-once")
    replay = client.post(
        "/api/v1/transfers",
        headers=payment_headers("transfer-once") | {"Content-Type": "application/json"},
        content=f'{{ "amount_paise": 100, "receiver_wallet_id": {receiver}, '
        f'"sender_wallet_id": {sender} }}',
    )
    conflict = transfer(client, sender, receiver, 200, key="transfer-once")
    assert original.status_code == replay.status_code == 201
    assert original.json() == replay.json()
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"
    assert balance(client, sender) == 900
    assert balance(client, receiver) == 100


def test_same_key_is_scoped_by_operation_and_demo_client(client: TestClient) -> None:
    sender, receiver = create_wallet(client), create_wallet(client)
    assert send_funding(client, sender, 1000, key="shared").status_code == 201
    first = transfer(client, sender, receiver, 100, key="shared")
    second = transfer(client, sender, receiver, 100, key="shared", demo_client="second-client")
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] != second.json()["id"]
    assert balance(client, receiver) == 200


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-Demo-Client": "pytest"},
        {"Idempotency-Key": "test"},
        {"X-Demo-Client": "pytest", "Idempotency-Key": "bad key"},
        {"X-Demo-Client": "x" * 65, "Idempotency-Key": "test"},
    ],
)
def test_payment_headers_required_and_validated(
    client: TestClient, headers: dict[str, str]
) -> None:
    sender, receiver = create_wallet(client), create_wallet(client)
    funding = client.post(
        f"/api/v1/wallets/{sender}/fund", headers=headers, json={"amount_paise": 100}
    )
    movement = client.post(
        "/api/v1/transfers",
        headers=headers,
        json={"sender_wallet_id": sender, "receiver_wallet_id": receiver, "amount_paise": 100},
    )
    assert funding.status_code == movement.status_code == 422
    assert balance(client, sender) == 0


def test_validation_and_insufficient_funds_do_not_consume_key(client: TestClient) -> None:
    sender, receiver = create_wallet(client), create_wallet(client)
    assert transfer(client, sender, receiver, -1, key="retry").status_code == 422
    assert transfer(client, sender, receiver, 200, key="retry").status_code == 409
    fund(client, sender, 200)
    assert transfer(client, sender, receiver, 200, key="retry").status_code == 201
    assert balance(client, receiver) == 200


@pytest.mark.parametrize("second_amount", [100, 200])
def test_parallel_transfer_key_executes_once(
    client: TestClient, db: Engine, second_amount: int
) -> None:
    sender, receiver = create_wallet(client), create_wallet(client)
    fund(client, sender, 1000)
    first, second = wait_for_two_requests(
        db,
        sender,
        lambda: transfer(client, sender, receiver, 100, key="parallel"),
        lambda: transfer(client, sender, receiver, second_amount, key="parallel"),
    )
    if second_amount == 100:
        assert first.status_code == second.status_code == 201
        assert first.json() == second.json()
    else:
        assert sorted([first.status_code, second.status_code]) == [201, 409]
        conflict = first if first.status_code == 409 else second
        assert conflict.json()["error"]["code"] == "idempotency_conflict"
    completed = first if first.status_code == 201 else second
    assert balance(client, receiver) == completed.json()["amount_paise"]
    assert balance(client, sender) + balance(client, receiver) == 1000
    with db.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(Transfer)) == 1


def test_parallel_funding_key_credits_once(client: TestClient, db: Engine) -> None:
    wallet = create_wallet(client)
    first, second = wait_for_two_requests(
        db,
        wallet,
        lambda: send_funding(client, wallet, 100, key="parallel-fund"),
        lambda: send_funding(client, wallet, 100, key="parallel-fund"),
    )
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert balance(client, wallet) == 100
    assert len(client.get(f"/api/v1/wallets/{wallet}/transactions").json()) == 1


def test_retry_after_transient_lock_timeout(client: TestClient, db: Engine, db_url: URL) -> None:
    wallet = create_wallet(client)
    timed_url = db_url.update_query_dict(
        {"options": f"{db_url.query['options']} -clock_timeout=250ms"}
    )
    with db.begin() as blocker:
        blocker.execute(select(Wallet.id).where(Wallet.id == wallet).with_for_update())
        with TestClient(create_app(timed_url)) as timed_client:
            result = send_funding(timed_client, wallet, 100, key="timeout")
            assert result.status_code == 503
            assert result.json()["error"]["code"] == "database_unavailable"
    with db.connect() as connection:
        assert connection.scalar(select(IdempotencyRecord.key)) is None
    assert balance(client, wallet) == 0
    assert send_funding(client, wallet, 100, key="timeout").status_code == 201
