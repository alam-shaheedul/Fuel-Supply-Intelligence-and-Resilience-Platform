from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from ..config import Settings


def database_engine(settings: Settings) -> Engine:
    return create_engine(
        str(settings.database_url),
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5},
    )
