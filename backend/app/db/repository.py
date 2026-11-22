from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .models import DecisionLog, DemandObservation, Prediction


def store_demand_history(session: Session, rows: list[dict]) -> int:
    if not rows:
        return 0
    statement = insert(DemandObservation).values(rows).on_conflict_do_nothing(
        constraint="uq_demand_run_source"
    ).returning(DemandObservation.id)
    return len(session.scalars(statement).all())


def store_prediction(session: Session, prediction: Prediction) -> int:
    session.add(prediction)
    session.flush()
    return prediction.id


def append_decision(session: Session, decision: DecisionLog) -> int:
    session.add(decision)
    session.flush()
    return decision.id
