from typing import Annotated

from fastapi import APIRouter, Path

from app.api.payment_headers import PaymentRequestKey
from app.constants import MAX_ID
from app.database import DatabaseSession
from app.errors import ApiError
from app.models import Transfer
from app.schemas import ErrorResponse, TransferRequest, TransferResponse
from app.services import payments

router = APIRouter(prefix="/transfers", tags=["transfers"])
TransferId = Annotated[int, Path(ge=1, le=MAX_ID)]


@router.post(
    "",
    status_code=201,
    responses={
        404: {"model": ErrorResponse, "description": "Wallet does not exist"},
        409: {
            "model": ErrorResponse,
            "description": "Insufficient funds, balance limit, or idempotency conflict",
        },
    },
)
def create_transfer(
    body: TransferRequest, session: DatabaseSession, request_key: PaymentRequestKey
) -> TransferResponse:
    return payments.transfer_funds(session, body, request_key)


@router.get(
    "/{transfer_id}", responses={404: {"model": ErrorResponse, "description": "Missing transfer"}}
)
def get_transfer(transfer_id: TransferId, session: DatabaseSession) -> TransferResponse:
    transfer = session.get(Transfer, transfer_id)
    if transfer is None:
        raise ApiError(404, "transfer_not_found", f"Transfer {transfer_id} does not exist.")
    return TransferResponse.model_validate(transfer)
