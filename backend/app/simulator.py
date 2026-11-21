import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx
from anyio import CancelScope
from pydantic import BaseModel, StrictStr, ValidationError

logger = logging.getLogger(__name__)


class SimulatorError(Exception):
    def __init__(
        self, code: str, message: str, status_code: int = 503, details: Any = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.details = details


class SimulatorHealth(BaseModel):
    status: StrictStr
    database: StrictStr


class SimulatorClient:
    def __init__(self, client: httpx.AsyncClient, stream_read_timeout: float = 30.0) -> None:
        self.client = client
        self.stream_timeout = httpx.Timeout(client.timeout)
        self.stream_timeout.read = stream_read_timeout

    async def _send(self, request: httpx.Request, *, stream: bool = False) -> httpx.Response:
        try:
            return await self.client.send(request, stream=stream)
        except httpx.TimeoutException as exc:
            raise SimulatorError("SIMULATOR_TIMEOUT", "The simulator request timed out.", 504) from exc
        except httpx.RequestError as exc:
            raise SimulatorError("SIMULATOR_UNREACHABLE", "The simulator could not be reached.") from exc

    def _check_status(self, response: httpx.Response) -> None:
        if response.is_success:
            return

        status = response.status_code
        if status not in (404, 409, 422, 503):
            raise SimulatorError("SIMULATOR_HTTP_ERROR", "The simulator returned an unexpected HTTP status.", 502)

        try:
            payload = response.json()
        except ValueError:
            payload = {}
        detail = payload.get("detail", payload.get("error")) if isinstance(payload, dict) else None
        if isinstance(detail, dict) and isinstance(detail.get("code"), str):
            message = detail.get("message")
            raise SimulatorError(
                detail["code"],
                message if isinstance(message, str) else "The simulator rejected the request.",
                status,
            )
        if status == 422 and isinstance(detail, list):
            raise SimulatorError("SIMULATOR_VALIDATION_ERROR", "The simulator rejected the request.", status, detail)
        raise SimulatorError("SIMULATOR_HTTP_ERROR", "The simulator rejected the request.", status)

    async def request_json(
        self, method: str, path: str, *, params: dict | None = None, body: dict | None = None
    ) -> tuple[Any, httpx.Response]:
        request = self.client.build_request(method, path, params=params, json=body)
        response = await self._send(request)
        self._check_status(response)
        try:
            return response.json(), response
        except ValueError as exc:
            raise SimulatorError("SIMULATOR_INVALID_RESPONSE", "The simulator returned invalid JSON.", 502) from exc

    async def open_stream(self) -> httpx.Response:
        request = self.client.build_request(
            "GET", "v1/stream", headers={"Accept": "text/event-stream"}, timeout=self.stream_timeout
        )
        response = await self._send(request, stream=True)
        try:
            if not response.is_success:
                await response.aread()
                self._check_status(response)
            if response.headers.get("content-type", "").split(";")[0].strip().lower() != "text/event-stream":
                raise SimulatorError("SIMULATOR_INVALID_RESPONSE", "The simulator returned an invalid event stream.", 502)
        except BaseException as exc:
            with CancelScope(shield=True):
                await response.aclose()
            if isinstance(exc, httpx.TimeoutException):
                raise SimulatorError("SIMULATOR_TIMEOUT", "The simulator stream request timed out.", 504) from exc
            if isinstance(exc, httpx.RequestError):
                raise SimulatorError("SIMULATOR_UNREACHABLE", "The simulator stream could not be read.") from exc
            raise
        return response

    async def stream_bytes(self, response: httpx.Response) -> AsyncIterator[bytes]:
        try:
            async for chunk in response.aiter_bytes():
                yield chunk
        except httpx.RequestError as exc:
            logger.warning("Simulator event stream disconnected: %s", exc)
        finally:
            with CancelScope(shield=True):
                await response.aclose()

    async def check_health(self) -> None:
        try:
            response = await self.client.get("v1/health")
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise SimulatorError("SIMULATOR_TIMEOUT", "The simulator health request timed out.") from exc
        except httpx.RequestError as exc:
            raise SimulatorError("SIMULATOR_UNREACHABLE", "The simulator could not be reached.") from exc
        except httpx.HTTPStatusError as exc:
            raise SimulatorError("SIMULATOR_HTTP_ERROR", "The simulator returned an unsuccessful HTTP status.") from exc

        try:
            health = SimulatorHealth.model_validate_json(response.content)
        except ValidationError as exc:
            raise SimulatorError("SIMULATOR_INVALID_RESPONSE", "The simulator returned an invalid health response.") from exc

        if health.status != "ok" or health.database != "ok":
            raise SimulatorError("SIMULATOR_UNHEALTHY", "The simulator reported an unhealthy service or database.")
