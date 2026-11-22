from pydantic import AnyHttpUrl, Field, PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    simulator_base_url: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:8000")
    simulator_timeout_seconds: float = Field(default=3.0, gt=0, allow_inf_nan=False)
    simulator_stream_read_timeout_seconds: float = Field(default=30.0, gt=15, allow_inf_nan=False)
    database_url: PostgresDsn = PostgresDsn(
        "postgresql+psycopg://fuel_platform:fuel_platform_local@127.0.0.1:5433/fuel_platform"
    )
