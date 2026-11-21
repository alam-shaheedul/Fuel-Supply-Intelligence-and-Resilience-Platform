from contextlib import asynccontextmanager
from typing import Literal

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .config import Settings
from .routes import router
from .simulator import SimulatorClient, SimulatorError


class HealthResponse(BaseModel):
    status: Literal["ok"]
    simulator: Literal["ok"]


class ErrorDetail(BaseModel):
    code: str
    message: str


class DegradedHealthResponse(BaseModel):
    status: Literal["degraded"]
    simulator: Literal["unavailable"]
    error: ErrorDetail


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings()
    async with httpx.AsyncClient(
        base_url=str(settings.simulator_base_url).rstrip("/") + "/",
        timeout=settings.simulator_timeout_seconds,
        trust_env=False,
    ) as http_client:
        app.state.simulator = SimulatorClient(http_client, settings.simulator_stream_read_timeout_seconds)
        yield


app = FastAPI(title="Fuel Supply Intelligence and Resilience Platform", lifespan=lifespan)
app.include_router(router)


@app.exception_handler(SimulatorError)
async def handle_simulator_error(request: Request, exc: SimulatorError) -> JSONResponse:
    if request.url.path.rstrip("/") != "/health":
        error = ErrorDetail(code=exc.code, message=str(exc)).model_dump()
        if exc.details is not None:
            error["details"] = exc.details
        return JSONResponse(status_code=exc.status_code, content={"error": error})

    body = DegradedHealthResponse(
        status="degraded",
        simulator="unavailable",
        error=ErrorDetail(code=exc.code, message=str(exc)),
    )
    return JSONResponse(status_code=503, content=body.model_dump())


@app.get("/health", responses={503: {"model": DegradedHealthResponse}})
async def health(request: Request) -> HealthResponse:
    simulator: SimulatorClient = request.app.state.simulator
    await simulator.check_health()
    return HealthResponse(status="ok", simulator="ok")
