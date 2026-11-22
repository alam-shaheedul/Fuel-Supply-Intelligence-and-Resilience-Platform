from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class DemandObservation(Base):
    __tablename__ = "demand_observations"
    __table_args__ = (
        UniqueConstraint("run_id", "source_id", name="uq_demand_run_source"),
        Index("ix_demand_station_fuel_tick", "run_id", "station_id", "fuel_type", "tick"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(String(100))
    source_id: Mapped[int]
    station_id: Mapped[str] = mapped_column(String(100))
    fuel_type: Mapped[str] = mapped_column(String(20))
    tick: Mapped[int]
    sim_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    demand_liters: Mapped[float]
    served_liters: Mapped[float]
    unmet_liters: Mapped[float]


class Prediction(Base):
    __tablename__ = "predictions"
    __table_args__ = (
        CheckConstraint("target_tick >= source_tick", name="ck_prediction_tick_order"),
        Index("ix_prediction_lookup", "run_id", "station_id", "fuel_type", "target_tick"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(String(100))
    station_id: Mapped[str] = mapped_column(String(100))
    fuel_type: Mapped[str] = mapped_column(String(20))
    source_tick: Mapped[int]
    target_tick: Mapped[int]
    kind: Mapped[str] = mapped_column(String(50))
    model_version: Mapped[str] = mapped_column(String(100))
    result: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DecisionLog(Base):
    __tablename__ = "decision_logs"
    __table_args__ = (
        Index("ix_decision_run_tick", "run_id", "tick", "id"),
        Index("ix_decision_run_key", "run_id", "decision_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(String(100))
    decision_key: Mapped[str] = mapped_column(String(150))
    tick: Mapped[int]
    status: Mapped[str] = mapped_column(String(50))
    explanation: Mapped[str]
    allocation_id: Mapped[int | None]
    details: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
