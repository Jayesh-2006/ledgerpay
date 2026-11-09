from typing import Annotated

from fastapi import Depends, Header

from app.services.idempotency import RequestKey

DEMO_TOKEN_PATTERN = r"^[A-Za-z0-9._:-]+$"


def read_request_key(
    demo_client: Annotated[
        str,
        Header(
            alias="X-Demo-Client",
            min_length=1,
            max_length=64,
            pattern=DEMO_TOKEN_PATTERN,
            description="Caller-chosen identity for the local demo",
        ),
    ],
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=1,
            max_length=128,
            pattern=DEMO_TOKEN_PATTERN,
            description="Use the same key when retrying this operation",
        ),
    ],
) -> RequestKey:
    return RequestKey(client_id=demo_client, key=idempotency_key)


PaymentRequestKey = Annotated[RequestKey, Depends(read_request_key)]
