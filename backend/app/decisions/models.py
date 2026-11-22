from typing import Annotated, Literal

from pydantic import BaseModel, Field, FiniteFloat

from ..intelligence.routes import Fuel

Liters = Annotated[FiniteFloat, Field(ge=0, le=1_000_000_000)]


class Depot(BaseModel):
    id: str
    status: str
    inventory: dict[str, Liters]
    dispatch_capacity_per_tick: Liters


class Station(BaseModel):
    id: str
    status: str
    inventory: dict[str, Liters]
    capacity: dict[str, Liters]


class Route(BaseModel):
    id: str
    source_depot_id: str
    destination_station_id: str
    status: str
    transit_ticks: int = Field(ge=0)
    max_shipment: Liters


class Allocation(BaseModel):
    source_depot_id: str
    destination_station_id: str
    route_id: str
    fuel_type: str
    quantity: Liters
    status: str
    created_tick: int = Field(ge=0)
    departure_tick: int | None = Field(default=None, ge=0)
    expected_arrival_tick: int | None = Field(default=None, ge=0)


class Snapshot(BaseModel):
    tick: int = Field(ge=0)
    depots: list[Depot]
    stations: list[Station]
    routes: list[Route]
    allocations: list[Allocation]


class ForecastPoint(BaseModel):
    tick: int = Field(ge=0)
    liters: Liters


class ForecastInput(BaseModel):
    id: int
    station_id: str
    fuel_type: Fuel
    points: list[ForecastPoint] = Field(min_length=1, max_length=24)


class Shipment(BaseModel):
    prediction_id: int
    source_depot_id: str
    destination_station_id: str
    route_id: str
    fuel_type: Fuel
    quantity: int
    estimated_arrival_tick: int
    priority_score: int
    explanation: str


class StationAssessment(BaseModel):
    prediction_id: int
    station_id: str
    fuel_type: Fuel
    first_shortage_tick: int | None
    projected_unmet_liters: float
    inbound_liters: float
    available_tank_space_liters: int
    priority_score: int
    recommended_liters: int = 0
    explanation: str


class Plan(BaseModel):
    solver_status: Literal["OPTIMAL", "FEASIBLE", "NO_CANDIDATES"]
    shipments: list[Shipment]
    assessments: list[StationAssessment]
