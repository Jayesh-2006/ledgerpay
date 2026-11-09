from sqlalchemy import select
from sqlalchemy.orm import Session

from app.constants import MAX_PAISE
from app.errors import ApiError, wallet_not_found
from app.models import Transfer, Wallet, WalletTransaction
from app.schemas import FundingResponse, TransferRequest, TransferResponse
from app.services.idempotency import RequestKey, reserve_request


def lock_wallets(session: Session, wallet_ids: list[int]) -> dict[int, Wallet]:
    # All money writes take wallet locks in ascending ID order.
    statement = (
        select(Wallet).where(Wallet.id.in_(wallet_ids)).order_by(Wallet.id).with_for_update()
    )
    wallets = {wallet.id: wallet for wallet in session.scalars(statement)}
    for wallet_id in wallet_ids:
        if wallet_id not in wallets:
            raise wallet_not_found(wallet_id)
    return wallets


def credit_wallet(wallet: Wallet, amount_paise: int) -> None:
    new_balance = wallet.balance_paise + amount_paise
    if new_balance > MAX_PAISE:
        raise ApiError(409, "balance_limit", "Credit would exceed the wallet balance limit.")
    wallet.balance_paise = new_balance


def record_transaction(
    session: Session,
    wallet: Wallet,
    kind: str,
    amount_paise: int,
    *,
    transfer_id: int | None = None,
) -> WalletTransaction:
    entry = WalletTransaction(
        wallet_id=wallet.id,
        kind=kind,
        transfer_id=transfer_id,
        amount_paise=amount_paise,
        balance_after_paise=wallet.balance_paise,
    )
    session.add(entry)
    return entry


def fund_wallet(
    session: Session, wallet_id: int, amount_paise: int, request_key: RequestKey
) -> FundingResponse:
    with session.begin():
        record = reserve_request(
            session, "fund", request_key, {"wallet_id": wallet_id, "amount_paise": amount_paise}
        )
        if record.response_body is not None:
            return FundingResponse.model_validate(record.response_body)

        wallet = lock_wallets(session, [wallet_id])[wallet_id]
        credit_wallet(wallet, amount_paise)
        entry = record_transaction(session, wallet, "fund", amount_paise)
        session.flush()
        response = FundingResponse(
            wallet_id=entry.wallet_id,
            transaction_id=entry.id,
            amount_paise=entry.amount_paise,
            balance_paise=entry.balance_after_paise,
            created_at=entry.created_at,
        )
        record.response_body = response.model_dump(mode="json")
        return response


def transfer_funds(
    session: Session, request: TransferRequest, request_key: RequestKey
) -> TransferResponse:
    with session.begin():
        record = reserve_request(session, "transfer", request_key, request.model_dump())
        if record.response_body is not None:
            return TransferResponse.model_validate(record.response_body)

        wallets = lock_wallets(session, [request.sender_wallet_id, request.receiver_wallet_id])
        sender = wallets[request.sender_wallet_id]
        receiver = wallets[request.receiver_wallet_id]
        amount = request.amount_paise
        if sender.balance_paise < amount:
            raise ApiError(409, "insufficient_funds", "Sender has insufficient funds.")
        credit_wallet(receiver, amount)
        sender.balance_paise -= amount

        transfer = Transfer(
            sender_wallet_id=sender.id, receiver_wallet_id=receiver.id, amount_paise=amount
        )
        session.add(transfer)
        session.flush()  # Assign the transfer ID before writing both history rows.
        record_transaction(session, sender, "transfer_out", amount, transfer_id=transfer.id)
        record_transaction(session, receiver, "transfer_in", amount, transfer_id=transfer.id)
        response = TransferResponse.model_validate(transfer)
        record.response_body = response.model_dump(mode="json")
        return response
