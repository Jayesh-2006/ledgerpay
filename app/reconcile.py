import sys

from sqlalchemy import case, func, select
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError

from app.database import configured_url, make_engine
from app.models import Wallet, WalletTransaction


def report_balance_mismatches(connection: Connection) -> int:
    signed_amount = case(
        (WalletTransaction.kind == "transfer_out", -WalletTransaction.amount_paise),
        else_=WalletTransaction.amount_paise,
    )
    history_balance = func.coalesce(func.sum(signed_amount), 0)
    # Read both values in one query so they reflect the same committed data.
    statement = (
        select(Wallet.id, Wallet.balance_paise, history_balance)
        .outerjoin(WalletTransaction, WalletTransaction.wallet_id == Wallet.id)
        .group_by(Wallet.id, Wallet.balance_paise)
        .order_by(Wallet.id)
    )

    checked = 0
    mismatches = 0
    for checked, (wallet_id, stored, expected) in enumerate(connection.execute(statement), start=1):
        if stored != expected:
            mismatches += 1
            print(
                f"Wallet {wallet_id} has {stored} paise stored and {expected} paise in history "
                f"(difference {stored - expected:+} paise)."
            )

    print(f"Checked {checked} wallets and found {mismatches} balance mismatches.")
    return mismatches


def main() -> int:
    try:
        engine = make_engine(configured_url("DATABASE_URL"))
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2

    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            return 1 if report_balance_mismatches(connection) else 0
    except SQLAlchemyError:
        print(
            "Could not check balances. Check PostgreSQL, DATABASE_URL, and table setup.",
            file=sys.stderr,
        )
        return 2
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
