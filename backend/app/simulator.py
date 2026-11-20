import httpx
from pydantic import BaseModel, StrictStr, ValidationError


class SimulatorError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SimulatorHealth(BaseModel):
    status: StrictStr
    database: StrictStr


class SimulatorClient:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

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
