from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from .simulator import SimulatorClient

router = APIRouter(prefix="/v1", tags=["Simulator"])


def get_simulator(request: Request) -> SimulatorClient:
    return request.app.state.simulator


Simulator = Annotated[SimulatorClient, Depends(get_simulator)]


class AllocationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=1, max_length=150)
    source_depot_id: str = Field(min_length=1)
    destination_station_id: str = Field(min_length=1)
    route_id: str = Field(min_length=1)
    fuel_type: Literal["DIESEL", "PETROL", "OCTANE"]
    quantity: float = Field(gt=0, allow_inf_nan=False)


def entity_segment(entity_id: str) -> str:
    if entity_id in (".", ".."):
        return entity_id.replace(".", "%2E")
    return quote(entity_id, safe="")


async def read(simulator: SimulatorClient, response: Response, path: str, params: dict | None = None):
    payload, upstream = await simulator.request_json("GET", path, params=params)
    stale = upstream.headers.get("X-Simulator-Stale")
    if stale is not None:
        response.headers["X-Simulator-Stale"] = stale
    return payload


@router.get("/instance")
async def instance(simulator: Simulator, response: Response):
    return await read(simulator, response, "v1/instance")


@router.get("/regions")
async def regions(simulator: Simulator, response: Response):
    return await read(simulator, response, "v1/regions")


@router.get("/depots")
async def depots(simulator: Simulator, response: Response):
    return await read(simulator, response, "v1/depots")


@router.get("/depots/{entity_id}")
async def depot(entity_id: str, simulator: Simulator, response: Response):
    return await read(simulator, response, f"v1/depots/{entity_segment(entity_id)}")


@router.get("/stations")
async def stations(simulator: Simulator, response: Response):
    return await read(simulator, response, "v1/stations")


@router.get("/stations/{entity_id}")
async def station(entity_id: str, simulator: Simulator, response: Response):
    return await read(simulator, response, f"v1/stations/{entity_segment(entity_id)}")


@router.get("/routes")
async def routes(simulator: Simulator, response: Response):
    return await read(simulator, response, "v1/routes")


@router.get("/supply-arrivals")
async def supply_arrivals(simulator: Simulator, response: Response):
    return await read(simulator, response, "v1/supply-arrivals")


@router.get("/events")
async def events(simulator: Simulator, response: Response):
    return await read(simulator, response, "v1/events")


@router.get("/demand-history")
async def demand_history(
    simulator: Simulator,
    response: Response,
    station_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=2000)] = 200,
):
    params: dict[str, str | int] = {"limit": limit}
    if station_id is not None:
        params["station_id"] = station_id
    return await read(simulator, response, "v1/demand-history", params)


@router.get("/metrics")
async def metrics(simulator: Simulator, response: Response):
    return await read(simulator, response, "v1/metrics")


@router.get("/allocations")
async def allocations(simulator: Simulator, response: Response):
    return await read(simulator, response, "v1/allocations")


@router.post("/allocations", status_code=201, responses={200: {"description": "Idempotent replay"}})
async def submit_allocation(allocation: AllocationRequest, simulator: Simulator, response: Response):
    payload, upstream = await simulator.request_json(
        "POST", "v1/allocations", body=allocation.model_dump()
    )
    response.status_code = upstream.status_code
    return payload


@router.post("/allocations/{allocation_id}/cancel")
async def cancel_allocation(allocation_id: Annotated[int, Path(gt=0)], simulator: Simulator):
    payload, _ = await simulator.request_json("POST", f"v1/allocations/{allocation_id}/cancel")
    return payload


@router.get(
    "/stream",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def stream(simulator: Simulator):
    upstream = await simulator.open_stream()
    return StreamingResponse(
        simulator.stream_bytes(upstream),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
