import argparse
import asyncio
from datetime import UTC, datetime

import httpx
from pydantic import BaseModel, TypeAdapter, ValidationError, field_validator
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..config import Settings
from ..simulator import SimulatorClient, SimulatorError
from .connection import database_engine
from .repository import store_demand_history


class ObservationPayload(BaseModel):
    id: int
    station_id: str
    fuel_type: str
    tick: int
    sim_time: datetime
    demand_liters: float
    served_liters: float
    unmet_liters: float

    @field_validator("sim_time")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def fetch_history(settings: Settings, limit: int, station_id: str | None) -> list[ObservationPayload]:
    params: dict[str, int | str] = {"limit": limit}
    if station_id is not None:
        params["station_id"] = station_id
    async with httpx.AsyncClient(
        base_url=str(settings.simulator_base_url).rstrip("/") + "/",
        timeout=settings.simulator_timeout_seconds,
        trust_env=False,
    ) as client:
        payload, response = await SimulatorClient(client).request_json(
            "GET", "v1/demand-history", params=params
        )
    if response.headers.get("X-Simulator-Stale", "").strip().lower() == "true":
        raise SimulatorError("SIMULATOR_STALE_DATA", "The simulator marked its demand history as stale.")
    return TypeAdapter(list[ObservationPayload]).validate_python(payload)


def main() -> None:
    parser = argparse.ArgumentParser(description="Import simulator demand history into PostgreSQL.")
    parser.add_argument("--run-id", required=True, help="Stable identifier for this simulator run.")
    parser.add_argument("--limit", type=int, default=2000, choices=range(1, 2001), metavar="1-2000")
    parser.add_argument("--station-id")
    args = parser.parse_args()
    run_id = args.run_id.strip()
    if not 1 <= len(run_id) <= 100:
        parser.error("--run-id must contain 1 to 100 characters.")

    try:
        settings = Settings()
        observations = asyncio.run(fetch_history(settings, args.limit, args.station_id))
        rows = [
            dict(run_id=run_id, source_id=item.id, **item.model_dump(exclude={"id"}))
            for item in observations
        ]
        engine = database_engine(settings)
        try:
            with Session(engine) as session, session.begin():
                inserted = store_demand_history(session, rows)
        finally:
            engine.dispose()
    except SimulatorError as exc:
        parser.exit(1, f"Import failed ({exc.code}): {exc}\n")
    except ValidationError:
        parser.exit(1, "Import failed: invalid settings or simulator demand history.\n")
    except SQLAlchemyError:
        parser.exit(1, "Import failed: check PostgreSQL connectivity and apply the database migration.\n")
    print(f"Fetched {len(rows)} observation(s), inserted {inserted}, skipped {len(rows) - inserted} duplicate(s).")


if __name__ == "__main__":
    main()
