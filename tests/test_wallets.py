import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import URL

from app.constants import MAX_ID, MAX_PAISE
from app.main import create_app
from tests.helpers import balance, create_wallet, fund, send_funding


def test_health_and_documentation() -> None:
    url = URL.create("postgresql+psycopg", database="unused")
    with TestClient(create_app(url)) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert "Swagger UI" in client.get("/docs").text
        assert "/api/v1/wallets" in client.get("/openapi.json").json()["paths"]


def test_create_retrieve_and_list_wallets(client: TestClient) -> None:
    wallet_id = create_wallet(client, "  Asha  ")
    wallet = client.get(f"/api/v1/wallets/{wallet_id}").json()
    assert wallet["owner_name"] == "Asha"
    assert wallet["balance_paise"] == 0
    assert wallet["currency"] == "INR"
    assert wallet["created_at"]
    assert client.get("/api/v1/wallets").json() == [wallet]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"owner_name": " "},
        {"owner_name": 1},
        {"owner_name": "a" * 101},
        {"owner_name": "a\x00b"},
        {"owner_name": "Asha", "balance_paise": 1},
    ],
)
def test_reject_invalid_wallet_data(client: TestClient, body: dict[str, object]) -> None:
    response = client.post("/api/v1/wallets", json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.parametrize("suffix", ["", "/balance", "/fund"])
def test_missing_wallet(client: TestClient, suffix: str) -> None:
    response = (
        send_funding(client, MAX_ID, 10)
        if suffix == "/fund"
        else client.get(f"/api/v1/wallets/{MAX_ID}{suffix}")
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "wallet_not_found"


def test_simulated_funding(client: TestClient) -> None:
    wallet_id = create_wallet(client)
    response = send_funding(client, wallet_id, 2550)
    assert response.status_code == 201
    assert response.json()["simulated"] is True
    assert response.json()["transaction_id"] > 0
    assert response.json()["balance_paise"] == balance(client, wallet_id) == 2550


@pytest.mark.parametrize("amount", [0, -1, 1.0, 2.5, "100", True, MAX_PAISE + 1])
def test_reject_invalid_funding_amount(client: TestClient, amount: object) -> None:
    wallet_id = create_wallet(client)
    response = send_funding(client, wallet_id, amount)
    assert response.status_code == 422
    assert balance(client, wallet_id) == 0


def test_funding_balance_limit(client: TestClient) -> None:
    wallet_id = create_wallet(client)
    fund(client, wallet_id, MAX_PAISE)
    response = send_funding(client, wallet_id, 1)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "balance_limit"
    assert balance(client, wallet_id) == MAX_PAISE


def test_persistence_after_api_restart(db_url: URL) -> None:
    with TestClient(create_app(db_url)) as client:
        wallet_id = create_wallet(client)
        fund(client, wallet_id, 3000)

    with TestClient(create_app(db_url)) as restarted_client:
        assert balance(restarted_client, wallet_id) == 3000
