"""FastAPI application: routes, CORS for the frontend, and the ProblemDetail error boundary.

Domain rejections (BookingError) are rendered here as structured problem-details so every route
handler can stay thin and no stack trace or internal ever leaks to the client.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.dbos_runtime import launch_dbos, shutdown_dbos
from app.repositories.booking_repository import BookingError
from app.routes import booking
from app.schemas import ErrorCode, ProblemDetail


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    launch_dbos()
    yield
    shutdown_dbos()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Travel Agent API", version="0.1.0", lifespan=_lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(booking.router)

    @app.exception_handler(BookingError)
    async def _render_booking_error(request: Request, error: BookingError) -> JSONResponse:
        problem = ProblemDetail(code=error.code, detail=error.detail)
        return JSONResponse(status_code=error.status_code, content=problem.model_dump(mode="json"))

    @app.exception_handler(RequestValidationError)
    async def _render_validation_error(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        field_errors = "; ".join(
            f"{'.'.join(str(part) for part in violation['loc'])}: {violation['msg']}"
            for violation in error.errors()
        )
        problem = ProblemDetail(code=ErrorCode.VALIDATION_ERROR, detail=field_errors)
        return JSONResponse(status_code=422, content=problem.model_dump(mode="json"))

    return app


app = create_app()
