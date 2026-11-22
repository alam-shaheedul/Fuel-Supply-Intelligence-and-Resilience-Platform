from alembic import context

from backend.app.config import Settings
from backend.app.db.connection import database_engine
from backend.app.db.models import Base


def run_offline() -> None:
    context.configure(
        url=str(Settings().database_url),
        target_metadata=Base.metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_online() -> None:
    engine = database_engine(Settings())
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=Base.metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_offline()
else:
    run_online()
