from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.constants import MAX_PAISE


class Base(DeclarativeBase):
    pass


class Wallet(Base):
    __tablename__ = "wallets"
    __table_args__ = (
        CheckConstraint("char_length(btrim(owner_name)) > 0", name="wallet_name_nonempty"),
        CheckConstraint(f"balance_paise BETWEEN 0 AND {MAX_PAISE}", name="wallet_balance_range"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    owner_name: Mapped[str] = mapped_column(String(100))
    balance_paise: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )


class Transfer(Base):
    __tablename__ = "transfers"
    __table_args__ = (
        CheckConstraint("sender_wallet_id <> receiver_wallet_id", name="transfer_distinct_wallets"),
        CheckConstraint(f"amount_paise BETWEEN 1 AND {MAX_PAISE}", name="transfer_amount_range"),
        CheckConstraint("status = 'completed'", name="transfer_completed"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    sender_wallet_id: Mapped[int] = mapped_column(ForeignKey("wallets.id"))
    receiver_wallet_id: Mapped[int] = mapped_column(ForeignKey("wallets.id"))
    amount_paise: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(9), server_default=text("'completed'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )


class WalletTransaction(Base):
    __tablename__ = "wallet_transactions"
    __table_args__ = (
        CheckConstraint(f"amount_paise BETWEEN 1 AND {MAX_PAISE}", name="history_amount_range"),
        CheckConstraint(
            f"balance_after_paise BETWEEN 0 AND {MAX_PAISE}", name="history_balance_range"
        ),
        CheckConstraint(
            "(kind = 'fund' AND transfer_id IS NULL) OR "
            "(kind IN ('transfer_in', 'transfer_out') AND transfer_id IS NOT NULL)",
            name="history_kind_matches_transfer",
        ),
        Index("ix_wallet_transactions_wallet_id_id", "wallet_id", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallets.id"))
    kind: Mapped[str] = mapped_column(String(12))
    transfer_id: Mapped[int | None] = mapped_column(ForeignKey("transfers.id"))
    amount_paise: Mapped[int] = mapped_column(BigInteger)
    balance_after_paise: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        CheckConstraint("operation IN ('fund', 'transfer')", name="idempotency_operation"),
    )

    client_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    operation: Mapped[str] = mapped_column(String(8), primary_key=True)
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    response_body: Mapped[dict[str, object] | None] = mapped_column(JSONB(none_as_null=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )
