import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from app.constants import MAX_ID, MAX_PAISE
from app.models import IdempotencyRecord, Transfer
from tests.helpers import balance, create_wallet, fund, transfer, wait_for_two_requests


def test_transfer_and_conservation(client: TestClient) -> None:
    sender = create_wallet(client)
    receiver = create_wallet(client, "Ravi")
    fund(client, sender, 100_000)
    fund(client, receiver, 5_000)
    before = balance(client, sender) + balance(client, receiver)

    response = transfer(client, sender, receiver, 50_000)
    assert response.status_code == 201
    saved = response.json()
    assert saved["status"] == "completed"
    assert saved["amount_paise"] == 50_000
    assert client.get(f"/api/v1/transfers/{saved['id']}").json() == saved
    assert balance(client, sender) == 50_000
    assert balance(client, receiver) == 55_000
    assert balance(client, sender) + balance(client, receiver) == before


def test_insufficient_funds(client: TestClient) -> None:
    sender, receiver = create_wallet(client), create_wallet(client)
    fund(client, sender, 200)
    response = transfer(client, sender, receiver, 201)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "insufficient_funds"
    assert balance(client, sender) == 200
    assert balance(client, receiver) == 0


def test_receiver_balance_limit(client: TestClient) -> None:
    sender, receiver = create_wallet(client), create_wallet(client)
    fund(client, sender, 1)
    fund(client, receiver, MAX_PAISE)
    response = transfer(client, sender, receiver, 1)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "balance_limit"
    assert balance(client, sender) == 1
    assert balance(client, receiver) == MAX_PAISE


def test_same_wallet(client: TestClient) -> None:
    wallet = create_wallet(client)
    assert transfer(client, wallet, wallet, 1).status_code == 422


@pytest.mark.parametrize("missing_sender", [True, False])
def test_missing_transfer_wallet(client: TestClient, missing_sender: bool) -> None:
    wallet = create_wallet(client)
    sender, receiver = (MAX_ID, wallet) if missing_sender else (wallet, MAX_ID)
    response = transfer(client, sender, receiver, 1)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "wallet_not_found"


@pytest.mark.parametrize("amount", [0, -1, 1.5, "100", True, MAX_PAISE + 1])
def test_invalid_transfer_amount(client: TestClient, amount: object) -> None:
    sender, receiver = create_wallet(client), create_wallet(client)
    assert transfer(client, sender, receiver, amount).status_code == 422


def test_missing_transfer_and_history(client: TestClient) -> None:
    assert client.get(f"/api/v1/transfers/{MAX_ID}").status_code == 404
    assert client.get(f"/api/v1/wallets/{MAX_ID}/transactions").status_code == 404


def test_history_and_pagination(client: TestClient) -> None:
    sender, receiver = create_wallet(client), create_wallet(client)
    sender_url = f"/api/v1/wallets/{sender}/transactions"
    assert client.get(sender_url).json() == []
    fund(client, sender, 3000)
    transfer_id = transfer(client, sender, receiver, 1000).json()["id"]
    outgoing, funding = client.get(sender_url).json()
    (incoming,) = client.get(f"/api/v1/wallets/{receiver}/transactions").json()
    assert outgoing["kind"] == "transfer_out"
    assert outgoing["balance_after_paise"] == 2000
    assert incoming["kind"] == "transfer_in"
    assert incoming["balance_after_paise"] == incoming["amount_paise"] == 1000
    assert incoming["transfer_id"] == outgoing["transfer_id"] == transfer_id
    assert funding["kind"] == "fund"
    assert funding["transfer_id"] is None
    assert client.get(sender_url, params={"limit": 1, "offset": 1}).json() == [funding]
    assert client.get(sender_url, params={"limit": 1, "offset": 2}).json() == []
    assert client.get(sender_url, params={"limit": 101}).status_code == 422
    assert client.get(sender_url, params={"offset": -1}).status_code == 422


def test_concurrent_transfers_cannot_overspend(client: TestClient, db: Engine) -> None:
    sender = create_wallet(client)
    receivers = (create_wallet(client), create_wallet(client))
    fund(client, sender, 100_000)
    responses = wait_for_two_requests(
        db,
        sender,
        lambda: transfer(client, sender, receivers[0], 80_000),
        lambda: transfer(client, sender, receivers[1], 80_000),
    )
    assert sorted(response.status_code for response in responses) == [201, 409]
    rejected = next(response for response in responses if response.status_code == 409)
    assert rejected.json()["error"]["code"] == "insufficient_funds"
    assert balance(client, sender) == 20_000
    assert sorted(balance(client, wallet) for wallet in receivers) == [0, 80_000]
    with db.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(Transfer)) == 1


def test_rolls_back_if_history_insert_fails(client: TestClient, db: Engine) -> None:
    sender, receiver = create_wallet(client), create_wallet(client)
    fund(client, sender, 2000)
    with db.begin() as connection:
        connection.exec_driver_sql(
            "ALTER TABLE wallet_transactions "
            "ADD CONSTRAINT test_reject_transfer_history CHECK (kind = 'fund')"
        )
    response = transfer(client, sender, receiver, 1000, key="injected-failure")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "database_error"
    assert "test_reject_transfer_history" not in response.text
    assert balance(client, sender) == 2000
    assert balance(client, receiver) == 0
    assert len(client.get(f"/api/v1/wallets/{sender}/transactions").json()) == 1
    assert client.get(f"/api/v1/wallets/{receiver}/transactions").json() == []
    with db.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(Transfer)) == 0
        assert (
            connection.scalar(
                select(IdempotencyRecord.key).where(IdempotencyRecord.operation == "transfer")
            )
            is None
        )
    with db.begin() as connection:
        connection.exec_driver_sql(
            "ALTER TABLE wallet_transactions DROP CONSTRAINT test_reject_transfer_history"
        )
    assert transfer(client, sender, receiver, 1000, key="injected-failure").status_code == 201
    assert balance(client, sender) == balance(client, receiver) == 1000
