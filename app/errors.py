import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError, OperationalError, SQLAlchemyError, TimeoutError
from starlette.exceptions import HTTPException

from app.schemas import ErrorDetail, ErrorInfo, ErrorResponse

logger = logging.getLogger(__name__)


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def wallet_not_found(wallet_id: int) -> ApiError:
    return ApiError(404, "wallet_not_found", f"Wallet {wallet_id} does not exist.")


def error_response(
    status: int, code: str, message: str, *, details: list[ErrorDetail] | None = None
) -> JSONResponse:
    body = ErrorResponse(error=ErrorInfo(code=code, message=message, details=details))
    return JSONResponse(body.model_dump(exclude_none=True), status_code=status)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc.status, exc.code, str(exc))

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            ErrorDetail(location=list(error["loc"]), message=error["msg"]) for error in exc.errors()
        ]
        return error_response(
            422, "validation_error", "Request validation failed.", details=details
        )

    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, exc: HTTPException) -> JSONResponse:
        response = error_response(exc.status_code, "http_error", str(exc.detail))
        if exc.headers:
            response.headers.update(exc.headers)
        return response

    @app.exception_handler(SQLAlchemyError)
    async def database_error(_request: Request, exc: SQLAlchemyError) -> JSONResponse:
        # Exception messages can contain connection or statement details.
        logger.error("Database operation failed (%s)", type(exc).__name__)
        if isinstance(exc, (OperationalError, TimeoutError)) or (
            isinstance(exc, DBAPIError) and exc.connection_invalidated
        ):
            return error_response(503, "database_unavailable", "Database operation unavailable.")
        return error_response(500, "database_error", "Database operation failed.")

    @app.exception_handler(Exception)
    async def unexpected_error(_request: Request, _exc: Exception) -> JSONResponse:
        return error_response(500, "internal_error", "Unexpected server error.")
