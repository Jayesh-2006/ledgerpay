from typing import Annotated

from fastapi import APIRouter, Path, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.payment_headers import PaymentRequestKey
from app.constants import MAX_ID
from app.database import DatabaseSession
from app.errors import wallet_not_found
from app.models import Wallet, WalletTransaction
from app.schemas import (
    BalanceResponse,
    ErrorResponse,
    FundingResponse,
    FundRequest,
    TransactionResponse,
    WalletCreate,
    WalletResponse,
)
from app.services import payments

router = APIRouter(prefix="/wallets", tags=["wallets"])
WalletId = Annotated[int, Path(ge=1, le=MAX_ID)]
WALLET_NOT_FOUND = {"model": ErrorResponse, "description": "Wallet does not exist"}


def require_wallet(session: Session, wallet_id: int) -> Wallet:
    wallet = session.get(Wallet, wallet_id)
    if wallet is None:
        raise wallet_not_found(wallet_id)
    return wallet


@router.post("", status_code=201)
def create_wallet(body: WalletCreate, session: DatabaseSession) -> WalletResponse:
    with session.begin():
        wallet = Wallet(owner_name=body.owner_name)
        session.add(wallet)
        session.flush()
        return WalletResponse.model_validate(wallet)


@router.get("")
def list_wallets(session: DatabaseSession) -> list[WalletResponse]:
    return [
        WalletResponse.model_validate(wallet)
        for wallet in session.scalars(select(Wallet).order_by(Wallet.id))
    ]


@router.get("/{wallet_id}", responses={404: WALLET_NOT_FOUND})
def get_wallet(wallet_id: WalletId, session: DatabaseSession) -> WalletResponse:
    return WalletResponse.model_validate(require_wallet(session, wallet_id))


@router.get("/{wallet_id}/balance", responses={404: WALLET_NOT_FOUND})
def get_balance(wallet_id: WalletId, session: DatabaseSession) -> BalanceResponse:
    wallet = require_wallet(session, wallet_id)
    return BalanceResponse(wallet_id=wallet.id, balance_paise=wallet.balance_paise)


@router.post(
    "/{wallet_id}/fund",
    status_code=201,
    responses={
        404: WALLET_NOT_FOUND,
        409: {"model": ErrorResponse, "description": "Balance limit or idempotency conflict"},
    },
)
def fund_wallet(
    wallet_id: WalletId, body: FundRequest, session: DatabaseSession, request_key: PaymentRequestKey
) -> FundingResponse:
    """Add simulated funds to a wallet."""
    return payments.fund_wallet(session, wallet_id, body.amount_paise, request_key)


@router.get("/{wallet_id}/transactions", responses={404: WALLET_NOT_FOUND})
def list_transactions(
    wallet_id: WalletId,
    session: DatabaseSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=MAX_ID)] = 0,
) -> list[TransactionResponse]:
    require_wallet(session, wallet_id)
    statement = (
        select(WalletTransaction)
        .where(WalletTransaction.wallet_id == wallet_id)
        .order_by(WalletTransaction.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return [TransactionResponse.model_validate(entry) for entry in session.scalars(statement)]
