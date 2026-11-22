from datetime import UTC, datetime
from typing import Annotated, Literal

from anyio import from_thread
from fastapi import APIRouter, Path, Request
from pydantic import BaseModel, Field, StringConstraints, ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..db.models import DecisionLog
from ..intelligence.routes import failure
from ..routes import AllocationRequest, Simulator
from ..simulator import SimulatorClient, SimulatorError
from .execution import capacity_issue
from .models import Allocation, Plan, Shipment
from .routes import LogisticsClock, read_snapshot

router = APIRouter(prefix="/v1/recommendations", tags=["Operator workflow"])
RecommendationId = Annotated[int, Path(gt=0, le=2_147_483_647)]
EXECUTION_LOCK = 819230815


class Approval(BaseModel):
    operator: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class ExecutionRecord(BaseModel):
    approved_by: str | None = None
    approved_at: datetime | None = None
    allocation_ids: dict[str, int] = Field(default_factory=dict)
    last_error: str | None = None


class WorkflowShipment(BaseModel):
    idempotency_key: str
    allocation_id: int | None
    proposal: Shipment


class WorkflowResponse(BaseModel):
    recommendation_id: int
    status: Literal["proposed", "approved", "partial", "submitted", "no_shipments"]
    approved_by: str | None
    approved_at: datetime | None
    last_error: str | None
    shipments: list[WorkflowShipment]


def load_plan(
    session: Session, recommendation_id: int, *, lock: bool = False
) -> tuple[DecisionLog, Plan, ExecutionRecord]:
    query = select(DecisionLog).where(
        DecisionLog.id == recommendation_id, DecisionLog.status == "recommendation_generated"
    )
    if lock:
        query = query.with_for_update()
    decision = session.scalar(query)
    if decision is None:
        raise failure(404, "RECOMMENDATION_NOT_FOUND", "The recommendation does not exist.")
    return (
        decision,
        Plan.model_validate(decision.details["plan"]),
        ExecutionRecord.model_validate(decision.details.get("execution", {})),
    )


def allocation_key(decision: DecisionLog, index: int) -> str:
    return f"platform-{decision.decision_key}-{index + 1}"


def workflow_view(decision: DecisionLog, plan: Plan, record: ExecutionRecord) -> WorkflowResponse:
    shipments = [
        WorkflowShipment(
            idempotency_key=key, allocation_id=record.allocation_ids.get(key), proposal=shipment,
        )
        for index, shipment in enumerate(plan.shipments)
        for key in [allocation_key(decision, index)]
    ]
    if not shipments:
        status = "no_shipments"
    elif all(item.allocation_id is not None for item in shipments):
        status = "submitted"
    elif record.allocation_ids:
        status = "partial"
    elif record.approved_at is not None:
        status = "approved"
    else:
        status = "proposed"
    return WorkflowResponse(
        recommendation_id=decision.id, status=status, approved_by=record.approved_by,
        approved_at=record.approved_at, last_error=record.last_error, shipments=shipments,
    )


def save(decision: DecisionLog, record: ExecutionRecord) -> None:
    decision.details = {**decision.details, "execution": record.model_dump(mode="json")}


async def current_clock(simulator: SimulatorClient) -> LogisticsClock:
    payload, response = await simulator.request_json("GET", "v1/instance")
    if response.headers.get("X-Simulator-Stale", "").strip().lower() == "true":
        raise SimulatorError("SIMULATOR_STALE_DATA", "The simulator marked the current clock as stale.")
    try:
        return LogisticsClock.model_validate(payload)
    except ValidationError as exc:
        raise SimulatorError("SIMULATOR_INVALID_RESPONSE", "The simulator clock is invalid.", 502) from exc


def allocation_matches(allocation: Allocation, item: WorkflowShipment) -> bool:
    proposal = item.proposal
    return allocation.id is not None and allocation.idempotency_key == item.idempotency_key and (
        allocation.source_depot_id, allocation.destination_station_id, allocation.route_id,
        allocation.fuel_type, allocation.quantity,
    ) == (
        proposal.source_depot_id, proposal.destination_station_id, proposal.route_id,
        proposal.fuel_type, proposal.quantity,
    )


async def submit(simulator: SimulatorClient, item: WorkflowShipment, tick: int) -> int:
    clock = await current_clock(simulator)
    if clock.tick != tick or clock.status != "PAUSED":
        raise SimulatorError("PLAN_OUT_OF_SYNC", "The simulator must be paused at the plan tick.", 409)
    body = AllocationRequest(
        idempotency_key=item.idempotency_key,
        **item.proposal.model_dump(include={
            "source_depot_id", "destination_station_id", "route_id", "fuel_type", "quantity",
        }),
    )
    payload, _ = await simulator.request_json("POST", "v1/allocations", body=body.model_dump())
    try:
        allocation = Allocation.model_validate(payload)
    except ValidationError as exc:
        raise SimulatorError("UNCONFIRMED_ALLOCATION", "The allocation response could not be verified.", 502) from exc
    if not allocation_matches(allocation, item):
        raise SimulatorError("UNCONFIRMED_ALLOCATION", "The allocation response could not be verified.", 502)
    return allocation.id


def execute(
    simulator: SimulatorClient, decision: DecisionLog, plan: Plan, record: ExecutionRecord,
    *, reconcile_only: bool = False,
) -> None:
    items = workflow_view(decision, plan, record).shipments
    pending = [item for item in items if item.allocation_id is None]
    if not pending:
        return
    snapshot = from_thread.run(read_snapshot, simulator)
    upstream = {item.idempotency_key: item for item in snapshot.allocations}
    outstanding = []
    for item in pending:
        existing = upstream.get(item.idempotency_key)
        if existing is None:
            outstanding.append(item)
        elif allocation_matches(existing, item):
            record.allocation_ids[item.idempotency_key] = existing.id
        else:
            record.last_error = "An upstream allocation uses this plan's key with different shipment details."
            return
    if reconcile_only and outstanding:
        return
    record.last_error = None
    if not outstanding:
        return
    if snapshot.tick != decision.tick or snapshot.status != "PAUSED":
        record.last_error = "The simulator must be paused at the original plan tick to submit remaining shipments."
        return
    record.last_error = capacity_issue(snapshot, [item.proposal for item in outstanding])
    if record.last_error:
        return

    for item in outstanding:
        try:
            record.allocation_ids[item.idempotency_key] = from_thread.run(submit, simulator, item, decision.tick)
        except SimulatorError as exc:
            record.last_error = f"{exc.code}: {exc}"
            break


@router.get("/{recommendation_id}/workflow", response_model=WorkflowResponse)
def get_workflow(recommendation_id: RecommendationId, request: Request) -> WorkflowResponse:
    try:
        with Session(request.app.state.database) as session:
            return workflow_view(*load_plan(session, recommendation_id))
    except SQLAlchemyError as exc:
        raise failure(503, "STORAGE_UNAVAILABLE", "Workflow storage is unavailable.") from exc


@router.post("/{recommendation_id}/approve", response_model=WorkflowResponse)
def approve(
    recommendation_id: RecommendationId, approval: Approval, request: Request, simulator: Simulator
) -> WorkflowResponse:
    try:
        with Session(request.app.state.database) as session, session.begin():
            decision, plan, record = load_plan(session, recommendation_id, lock=True)
            if not plan.shipments:
                raise failure(409, "NO_SHIPMENTS", "This plan has no shipments to approve.")
            if record.approved_at is None:
                clock = from_thread.run(current_clock, simulator)
                if clock.tick != decision.tick or clock.status != "PAUSED":
                    raise failure(409, "PLAN_OUT_OF_SYNC", "The simulator must be paused at the plan tick.")
                record.approved_by = approval.operator
                record.approved_at = datetime.now(UTC)
                save(decision, record)
            elif record.approved_by != approval.operator:
                raise failure(409, "ALREADY_APPROVED", "This plan already has a recorded operator approval.")
            return workflow_view(decision, plan, record)
    except SQLAlchemyError as exc:
        raise failure(503, "STORAGE_UNAVAILABLE", "Workflow storage is unavailable.") from exc


@router.post("/{recommendation_id}/execute", response_model=WorkflowResponse)
def execute_approved(
    recommendation_id: RecommendationId, request: Request, simulator: Simulator,
    reconcile_only: bool = False,
) -> WorkflowResponse:
    try:
        with Session(request.app.state.database) as session, session.begin():
            acquired = session.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": EXECUTION_LOCK})
            if not acquired:
                raise failure(409, "EXECUTION_IN_PROGRESS", "Another plan is being submitted. Retry shortly.")
            decision, plan, record = load_plan(session, recommendation_id, lock=True)
            if record.approved_at is None:
                raise failure(409, "APPROVAL_REQUIRED", "Approve this plan before submitting shipments.")
            execute(simulator, decision, plan, record, reconcile_only=reconcile_only)
            save(decision, record)
            return workflow_view(decision, plan, record)
    except SQLAlchemyError as exc:
        raise failure(503, "STORAGE_UNAVAILABLE", "Workflow storage is unavailable. Retrying can recover confirmed allocations.") from exc
