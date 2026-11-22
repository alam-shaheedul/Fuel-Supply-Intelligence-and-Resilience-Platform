import asyncio
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Path, Request
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from ..db.models import DecisionLog, Prediction
from ..db.repository import append_decision
from ..intelligence.routes import SimulatorClock, failure
from ..routes import Simulator
from ..simulator import SimulatorError
from .models import ForecastInput, Plan, Snapshot
from .optimizer import OptimizationError, build_plan

router = APIRouter(prefix="/v1/recommendations", tags=["Allocation recommendations"])


class RecommendationRequest(BaseModel):
    prediction_ids: list[Annotated[int, Field(gt=0, le=2_147_483_647)]] = Field(min_length=1, max_length=24)


class RecommendationResponse(BaseModel):
    id: int
    run_id: str
    tick: int
    plan: Plan


async def read_snapshot(simulator: Simulator) -> Snapshot:
    async def read(resource: str):
        payload, response = await simulator.request_json("GET", f"v1/{resource}")
        if response.headers.get("X-Simulator-Stale", "").strip().lower() == "true":
            raise SimulatorError("SIMULATOR_STALE_DATA", f"The simulator marked {resource} data as stale.")
        return payload

    try:
        before = SimulatorClock.model_validate(await read("instance"))
        depots, stations, routes, allocations = await asyncio.gather(
            read("depots"), read("stations"), read("routes"), read("allocations")
        )
        after = SimulatorClock.model_validate(await read("instance"))
        snapshot = Snapshot(
            tick=after.tick, depots=depots, stations=stations, routes=routes, allocations=allocations
        )
    except ValidationError as exc:
        raise SimulatorError("SIMULATOR_INVALID_RESPONSE", "The simulator logistics snapshot is invalid.", 502) from exc
    if before.tick != after.tick:
        raise failure(409, "SNAPSHOT_MOVED", "The simulator advanced during the snapshot. Retry the request.")
    return snapshot


def read_forecasts(session: Session, prediction_ids: list[int], snapshot: Snapshot) -> tuple[str, list[ForecastInput]]:
    predictions = session.scalars(
        select(Prediction).where(
            Prediction.id.in_(prediction_ids), Prediction.kind == "station_fuel_intelligence"
        ).order_by(Prediction.id)
    ).all()
    if len(predictions) != len(prediction_ids):
        raise failure(404, "PREDICTION_NOT_FOUND", "At least one prediction does not exist.")
    run_id = predictions[0].run_id
    if any(item.run_id != run_id or item.source_tick != snapshot.tick - 1 for item in predictions):
        raise failure(409, "PREDICTIONS_OUT_OF_SYNC", "Use current predictions from a single simulator run.")
    pairs = {(item.station_id, item.fuel_type) for item in predictions}
    if len(pairs) != len(predictions):
        raise failure(422, "DUPLICATE_STATION_FUEL", "Select one prediction per station and fuel.")
    stations = {item.id: item for item in snapshot.stations}
    forecasts = []
    for item in predictions:
        station = stations.get(item.station_id)
        if station is None or item.fuel_type not in station.inventory or item.fuel_type not in station.capacity:
            raise failure(409, "PREDICTION_STATION_MISMATCH", "The forecast station or fuel is missing from current inventory.")
        try:
            forecast = ForecastInput(
                id=item.id, station_id=item.station_id, fuel_type=item.fuel_type,
                points=item.result.get("demand_forecast"),
            )
        except ValidationError as exc:
            raise failure(409, "INVALID_FORECAST", "A saved demand forecast is invalid.") from exc
        if [point.tick for point in forecast.points] != list(range(snapshot.tick, snapshot.tick + len(forecast.points))):
            raise failure(409, "INVALID_FORECAST", "Forecast ticks must be consecutive and start at the current tick.")
        forecasts.append(forecast)
    return run_id, forecasts


def create_recommendation(engine: Engine, prediction_ids: list[int], snapshot: Snapshot) -> RecommendationResponse:
    try:
        with Session(engine) as session:
            run_id, forecasts = read_forecasts(session, prediction_ids, snapshot)
        plan = build_plan(snapshot, forecasts)
        decision = DecisionLog(
            run_id=run_id,
            decision_key=f"recommendation-{uuid4()}",
            tick=snapshot.tick,
            status="recommendation_generated",
            explanation=f"Proposed {len(plan.shipments)} shipment(s) based on forecast demand and current capacity.",
            details={"plan": plan.model_dump(mode="json")},
        )
        with Session(engine) as session, session.begin():
            decision_id = append_decision(session, decision)
    except SQLAlchemyError as exc:
        raise failure(503, "STORAGE_UNAVAILABLE", "Recommendation storage is unavailable.") from exc
    except OptimizationError as exc:
        raise failure(503, "OPTIMIZATION_UNAVAILABLE", str(exc)) from exc
    return RecommendationResponse(id=decision_id, run_id=run_id, tick=snapshot.tick, plan=plan)


@router.post("", status_code=201, response_model=RecommendationResponse)
async def recommend(query: RecommendationRequest, request: Request, simulator: Simulator) -> RecommendationResponse:
    if len(set(query.prediction_ids)) != len(query.prediction_ids):
        raise failure(422, "DUPLICATE_PREDICTION_ID", "Each prediction ID may appear only once.")
    snapshot = await read_snapshot(simulator)
    return await run_in_threadpool(create_recommendation, request.app.state.database, query.prediction_ids, snapshot)


@router.get("/{recommendation_id}", response_model=RecommendationResponse)
def recommendation_by_id(
    recommendation_id: Annotated[int, Path(gt=0, le=2_147_483_647)], request: Request
) -> RecommendationResponse:
    try:
        with Session(request.app.state.database) as session:
            decision = session.get(DecisionLog, recommendation_id)
            if decision is None or decision.status != "recommendation_generated":
                raise failure(404, "RECOMMENDATION_NOT_FOUND", "The recommendation does not exist.")
            return RecommendationResponse(
                id=decision.id, run_id=decision.run_id, tick=decision.tick,
                plan=Plan.model_validate(decision.details["plan"]),
            )
    except SQLAlchemyError as exc:
        raise failure(503, "STORAGE_UNAVAILABLE", "Recommendation storage is unavailable.") from exc
