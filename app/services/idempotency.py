import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.errors import ApiError
from app.models import IdempotencyRecord


@dataclass(frozen=True)
class RequestKey:
    client_id: str
    key: str


def reserve_request(
    session: Session,
    operation: Literal["fund", "transfer"],
    request_key: RequestKey,
    payload: dict[str, int],
) -> IdempotencyRecord:
    canonical_payload = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    request_hash = hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()
    scope = {"client_id": request_key.client_id, "operation": operation, "key": request_key.key}
    statement = (
        insert(IdempotencyRecord)
        .values(**scope, request_hash=request_hash)
        .on_conflict_do_nothing(index_elements=list(scope))
        .returning(IdempotencyRecord)
    )
    reserved = session.scalar(statement)
    if reserved is not None:
        return reserved

    # PostgreSQL waits for a competing insert to finish. At READ COMMITTED,
    # this separate SELECT sees the completed row if the competing insert committed.
    previous = session.get(IdempotencyRecord, scope)
    if previous is None or previous.response_body is None:
        raise ApiError(500, "idempotency_error", "The original result could not be retrieved.")
    if previous.request_hash != request_hash:
        raise ApiError(409, "idempotency_conflict", "This key already has a different request.")
    return previous
