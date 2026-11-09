from datetime import datetime
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationInfo,
    field_validator,
)

from app.constants import MAX_ID, MAX_PAISE

AmountPaise = Annotated[
    int, Field(strict=True, ge=1, le=MAX_PAISE, description="Amount in integer INR paise")
]
WalletIdField = Annotated[int, Field(strict=True, ge=1, le=MAX_ID)]


class WalletCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner_name: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True, min_length=1, max_length=100, pattern=r"^[^\x00]+$"
        ),
    ]


class WalletResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    owner_name: str
    balance_paise: int
    currency: Literal["INR"] = "INR"
    created_at: datetime


class FundRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount_paise: AmountPaise


class BalanceResponse(BaseModel):
    wallet_id: int
    balance_paise: int
    currency: Literal["INR"] = "INR"


class FundingResponse(BalanceResponse):
    transaction_id: int
    amount_paise: int
    simulated: Literal[True] = True
    created_at: datetime


class TransferRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sender_wallet_id: WalletIdField
    receiver_wallet_id: WalletIdField
    amount_paise: AmountPaise

    @field_validator("receiver_wallet_id")
    @classmethod
    def different_wallet(cls, value: int, info: ValidationInfo) -> int:
        if value == info.data.get("sender_wallet_id"):
            raise ValueError("Sender and receiver must be different wallets.")
        return value


class TransferResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sender_wallet_id: int
    receiver_wallet_id: int
    amount_paise: int
    currency: Literal["INR"] = "INR"
    status: Literal["completed"]
    created_at: datetime


class TransactionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    wallet_id: int
    kind: Literal["fund", "transfer_in", "transfer_out"]
    transfer_id: int | None
    amount_paise: int
    balance_after_paise: int
    currency: Literal["INR"] = "INR"
    created_at: datetime


class ErrorDetail(BaseModel):
    location: list[str | int]
    message: str


class ErrorInfo(BaseModel):
    code: str
    message: str
    details: list[ErrorDetail] | None = None


class ErrorResponse(BaseModel):
    error: ErrorInfo
