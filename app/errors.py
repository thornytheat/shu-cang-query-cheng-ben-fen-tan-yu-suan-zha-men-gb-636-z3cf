"""领域异常 → HTTP 映射；业务代码只抛这些，不直接构造 HTTPException。"""
from __future__ import annotations


class CostsplitError(Exception):
    status_code = 400
    code = "E_UNKNOWN"

    def __init__(self, message: str, *, code: str | None = None,
                 detail: dict | None = None):
        super().__init__(message)
        self.message = message
        self.detail = detail or {}
        if code:
            self.code = code

    def payload(self) -> dict:
        return {"error": self.code, "message": self.message, "detail": self.detail}


class BadRequest(CostsplitError):
    status_code = 400
    code = "E_BAD_REQUEST"


class NotFound(CostsplitError):
    status_code = 404
    code = "E_NOT_FOUND"


class Conflict(CostsplitError):
    status_code = 409
    code = "E_CONFLICT"


class StaleVersion(Conflict):
    code = "Q_STALE_VERSION"


class Unprocessable(CostsplitError):
    status_code = 422
    code = "E_UNPROCESSABLE"


def install_handlers(app) -> None:
    from fastapi import Request
    from fastapi.responses import JSONResponse

    @app.exception_handler(CostsplitError)
    async def _costsplit(request: Request, exc: CostsplitError):  # noqa: ANN001
        return JSONResponse(status_code=exc.status_code, content=exc.payload())
