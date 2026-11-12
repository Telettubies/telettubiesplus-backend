"""Configuración por entorno (ADR-016: mismo código en local y en la VM).

Todos los secretos llegan por variables de entorno y nunca se versionan (S-06).
Los nombres de modelos son configurables por entorno (P-19).
"""

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Aplicación ---
    app_env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # --- PostgreSQL (C-05) ---
    database_url: SecretStr

    # --- RabbitMQ (C-04) ---
    rabbitmq_url: SecretStr

    # --- AWS S3 (C-07) ---
    aws_region: str
    s3_bucket: str
    s3_presign_ttl_seconds: int = Field(default=300, gt=0)

    # --- Supabase Auth (C-08) ---
    supabase_url: str
    supabase_jwt_audience: str = "authenticated"
    supabase_service_role_key: SecretStr | None = None

    # --- Límites (S-08) ---
    max_pdf_mb: int = Field(gt=0)

    # --- Anthropic (C-10) ---
    anthropic_api_key: SecretStr | None = None
    anthropic_model_extraccion: str | None = None
    anthropic_model_verificacion: str | None = None
    anthropic_model_redaccion: str | None = None

    # --- NVIDIA (C-09) ---
    nvidia_api_key: SecretStr | None = None
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_model_filtro: str | None = None
    nvidia_model_embeddings: str | None = None
    nvidia_model_chat: str | None = None
    nvidia_model_rerank: str | None = None

    # --- Pipeline ---
    pipeline_checkpointer_enabled: bool = True

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [origen.strip() for origen in value.split(",") if origen.strip()]
        return value

    @field_validator(
        "anthropic_model_extraccion",
        "anthropic_model_verificacion",
        "anthropic_model_redaccion",
        "nvidia_model_filtro",
        "nvidia_model_embeddings",
        "nvidia_model_chat",
        "nvidia_model_rerank",
        "supabase_service_role_key",
        "anthropic_api_key",
        "nvidia_api_key",
        mode="before",
    )
    @classmethod
    def _empty_as_none(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("supabase_url")
    @classmethod
    def _strip_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @property
    def supabase_issuer(self) -> str:
        return f"{self.supabase_url}/auth/v1"

    @property
    def supabase_jwks_url(self) -> str:
        return f"{self.supabase_issuer}/.well-known/jwks.json"

    @property
    def max_pdf_bytes(self) -> int:
        return self.max_pdf_mb * 1024 * 1024

    @property
    def psycopg_database_url(self) -> str:
        """URL para psycopg (checkpointer de LangGraph, P-08), sin el sufijo del driver."""
        return self.database_url.get_secret_value().replace(
            "postgresql+asyncpg://", "postgresql://"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()  # los campos obligatorios vienen del entorno
