from typing import Annotated, Literal

import numpy as np
from fastapi import APIRouter, HTTPException, Path, Request
from pydantic import BaseModel, Field, FiniteFloat, ValidationError
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from ..db.models import DemandObservation, Prediction
from ..db.repository import store_prediction
from ..routes import Simulator, entity_segment
from ..simulator import SimulatorError
from .forecast import MODEL_VERSION, describe_demand, evaluate_forecast, forecast_demand
from .risk import detect_latest_anomaly, project_shortage

router = APIRouter(prefix="/v1/predictions", tags=["Intelligence"])
Fuel = Literal["DIESEL", "PETROL", "OCTANE"]
MAX_HISTORY = 512


class PredictionRequest(BaseModel):
    run_id: str = Field(min_length=1, max_length=100, pattern=r"^\S(?:.*\S)?$")
    station_id: str = Field(min_length=1, max_length=100)
    fuel_type: Fuel
    horizon_ticks: int = Field(default=8, ge=1, le=24)


class InventorySnapshot(BaseModel):
    inventory: dict[str, FiniteFloat]


class SimulatorClock(BaseModel):
    tick: int = Field(ge=0)


class PredictionResponse(BaseModel):
    id: int
    run_id: str
    station_id: str
    fuel_type: Fuel
    source_tick: int
    target_tick: int
    model_version: str
    result: dict


def failure(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, detail={"code": code, "message": message})


async def read_inventory(simulator: Simulator, station_id: str, fuel: Fuel) -> tuple[int, float]:
    snapshots = []
    for path in ("v1/instance", f"v1/stations/{entity_segment(station_id)}", "v1/instance"):
        payload, response = await simulator.request_json("GET", path)
        if response.headers.get("X-Simulator-Stale", "").strip().lower() == "true":
            raise SimulatorError("SIMULATOR_STALE_DATA", "The simulator marked the inventory snapshot as stale.")
        snapshots.append(payload)

    try:
        before = SimulatorClock.model_validate(snapshots[0])
        station = InventorySnapshot.model_validate(snapshots[1])
        after = SimulatorClock.model_validate(snapshots[2])
        inventory = station.inventory[fuel]
    except (ValidationError, KeyError) as exc:
        raise SimulatorError("SIMULATOR_INVALID_RESPONSE", "The simulator inventory snapshot is invalid.", 502) from exc
    if inventory < 0:
        raise SimulatorError("SIMULATOR_INVALID_RESPONSE", "The simulator inventory is negative.", 502)
    if before.tick != after.tick:
        raise failure(409, "SNAPSHOT_MOVED", "The simulator advanced during the snapshot. Retry the request.")
    return after.tick, inventory


def build_prediction(engine: Engine, query: PredictionRequest, tick: int, inventory: float) -> PredictionResponse:
    try:
        with Session(engine) as session:
            rows = session.execute(
                select(DemandObservation.tick, DemandObservation.demand_liters)
                .where(
                    DemandObservation.run_id == query.run_id,
                    DemandObservation.station_id == query.station_id,
                    DemandObservation.fuel_type == query.fuel_type,
                )
                .order_by(DemandObservation.tick.desc(), DemandObservation.source_id.desc())
                .limit(MAX_HISTORY)
            ).all()
    except SQLAlchemyError as exc:
        raise failure(503, "STORAGE_UNAVAILABLE", "Prediction storage is unavailable.") from exc

    if not rows:
        raise failure(409, "NO_DEMAND_HISTORY", "Import demand history for this run, station, and fuel.")
    if rows[0].tick != tick - 1:
        raise failure(409, "HISTORY_OUT_OF_SYNC", "Import current demand history from the same simulator run.")

    contiguous = []
    for observation_tick, liters in rows:
        if observation_tick != tick - 1 - len(contiguous):
            break
        contiguous.append(liters)
    values = np.asarray(contiguous[::-1], dtype=float)
    if not np.isfinite(values).all() or np.any(values < 0):
        raise failure(409, "INVALID_DEMAND_HISTORY", "Stored demand must be finite and nonnegative.")

    forecast = forecast_demand(values, query.horizon_ticks)
    result = {
        "forecast_method": forecast.method,
        "demand_summary": describe_demand(values),
        "demand_forecast": [
            {"tick": forecast_tick, "liters": liters}
            for forecast_tick, liters in enumerate(forecast.liters, start=tick)
        ],
        "shortage": project_shortage(inventory, forecast.liters, tick),
        "anomaly": {"tick": tick - 1, **detect_latest_anomaly(values)},
        "evaluation": evaluate_forecast(values, query.horizon_ticks),
    }
    prediction = Prediction(
        run_id=query.run_id, station_id=query.station_id, fuel_type=query.fuel_type,
        kind="station_fuel_intelligence", source_tick=tick - 1,
        target_tick=tick + query.horizon_ticks - 1, model_version=MODEL_VERSION, result=result,
    )
    try:
        with Session(engine) as session, session.begin():
            store_prediction(session, prediction)
            response = PredictionResponse.model_validate(prediction, from_attributes=True)
    except SQLAlchemyError as exc:
        raise failure(503, "STORAGE_UNAVAILABLE", "Prediction storage is unavailable.") from exc
    return response


@router.post("", status_code=201, response_model=PredictionResponse)
async def predict(query: PredictionRequest, request: Request, simulator: Simulator) -> PredictionResponse:
    tick, inventory = await read_inventory(simulator, query.station_id, query.fuel_type)
    return await run_in_threadpool(build_prediction, request.app.state.database, query, tick, inventory)


@router.get("/{prediction_id}", response_model=PredictionResponse)
def prediction_by_id(prediction_id: Annotated[int, Path(gt=0)], request: Request) -> PredictionResponse:
    try:
        with Session(request.app.state.database) as session:
            prediction = session.get(Prediction, prediction_id)
            if prediction is None or prediction.kind != "station_fuel_intelligence":
                raise failure(404, "PREDICTION_NOT_FOUND", "The prediction does not exist.")
            return PredictionResponse.model_validate(prediction, from_attributes=True)
    except SQLAlchemyError as exc:
        raise failure(503, "STORAGE_UNAVAILABLE", "Prediction storage is unavailable.") from exc
