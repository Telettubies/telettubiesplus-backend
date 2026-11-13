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

    # --- Supabase: Auth (C-08) y Storage (C-07, sustituye a S3) ---
    supabase_url: str
    supabase_jwt_audience: str = "authenticated"
    # Solo backend (S-06): invita usuarios y accede al bucket privado.
    supabase_service_role_key: SecretStr | None = None
    supabase_storage_bucket: str
    # Vigencia de las URL firmadas del PDF (P-14, S-10: "pocos minutos").
    storage_url_ttl_seconds: int = Field(default=300, gt=0)

    # --- Límites de uso (S-08, HU-B26) ---
    # Los valores por defecto son provisionales: P-23 no fija cifras (ver docs/PLAN.md).
    max_pdf_mb: int = Field(gt=0)
    max_pdf_paginas: int = Field(default=600, gt=0)
    # Tamaño máximo del archivo JSON de la base de conocimiento.
    max_conocimiento_mb: int = Field(default=5, gt=0)
    # Cuotas por organización en 24 horas móviles.
    cuota_analisis_diaria: int = Field(default=20, gt=0)
    cuota_chat_diaria: int = Field(default=200, gt=0)
    # Borradores de observación con Claude por organización y 24 horas (HU-B25, S-08).
    cuota_observaciones_diaria: int = Field(default=30, gt=0)
    chat_max_caracteres: int = Field(default=2000, gt=0)
    # Fragmentos del pliego que recibe el chat como contexto (HU-B24).
    chat_fragmentos: int = Field(default=6, gt=0, le=30)
    # Ítems del resultado que recibe el chat como contexto, brechas y revisiones primero.
    chat_max_items_contexto: int = Field(default=40, gt=0, le=200)
    # Longitud máxima de cada fragmento del pliego indexado para el chat.
    pliego_fragmento_caracteres: int = Field(default=1500, ge=300, le=8000)

    # --- Anthropic (C-10) ---
    anthropic_api_key: SecretStr | None = None
    anthropic_model_extraccion: str | None = None
    anthropic_model_verificacion: str | None = None
    anthropic_model_redaccion: str | None = None
    # Modelo con visión que transcribe las páginas que el OCR no logra leer (P-09).
    anthropic_model_ocr: str | None = None

    # --- NVIDIA (C-09) ---
    nvidia_api_key: SecretStr | None = None
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_model_filtro: str | None = None
    nvidia_model_embeddings: str | None = None
    nvidia_model_chat: str | None = None
    nvidia_model_rerank: str | None = None
    # El reranker no usa la API compatible con OpenAI: tiene su propia URL (P-11).
    nvidia_rerank_url: str | None = None
    # Textos por llamada al modelo de embeddings (HU-B06).
    embeddings_lote: int = Field(default=32, gt=0)
    # Límite de velocidad y reintentos (R-02: ~40 peticiones/min según fuentes no oficiales, Q-13).
    nvidia_peticiones_por_minuto: int = Field(default=30, gt=0)
    nvidia_max_reintentos: int = Field(default=5, ge=0)
    nvidia_espera_base_segundos: float = Field(default=2.0, gt=0)
    nvidia_espera_maxima_segundos: float = Field(default=60.0, gt=0)

    # --- Lectura y OCR (HU-B10, P-09) ---
    ocr_idioma: str = "spa"
    ocr_dpi: int = Field(default=200, ge=72, le=400)
    # Una página con menos caracteres extraíbles se trata como escaneada.
    ocr_min_caracteres: int = Field(default=20, ge=0)
    # Confianza media (0-100) de Tesseract por debajo de la cual el texto no se da por legible.
    ocr_confianza_minima: float = Field(default=50, ge=0, le=100)
    # Páginas por pliego que como máximo se envían al modelo con visión (RC-02).
    ocr_vision_max_paginas: int = Field(default=30, ge=0)

    # Una sección más larga se parte en tramos de este tamaño para la extracción (HU-B11).
    seccion_max_caracteres: int = Field(default=12000, ge=1000)

    # Secciones por llamada al filtro de NVIDIA y tamaño máximo de un lote (HU-B12).
    filtro_lote: int = Field(default=8, gt=0)
    filtro_max_caracteres_lote: int = Field(default=24000, ge=1000)

    # Secciones que se envían a Claude a la vez en la extracción (HU-B13).
    extraccion_concurrencia: int = Field(default=3, gt=0)
    # Veces que la comprobación de cobertura reintenta las secciones sin procesar (HU-B14).
    cobertura_max_reintentos: int = Field(default=2, ge=0)

    # --- Recuperación de evidencia (HU-B16) ---
    # Fragmentos que se piden a la búsqueda vectorial y cuántos se conservan por ítem.
    evidencia_candidatos: int = Field(default=20, gt=0)
    evidencia_max: int = Field(default=5, gt=0)
    # Distancia coseno (0 a 2) a partir de la cual un fragmento ya no se considera relevante.
    # Valor provisional (Q-14).
    evidencia_distancia_maxima: float = Field(default=1.0, gt=0, le=2)
    # Reranker de NVIDIA (P-11, no aprobada): apagado salvo que se active aquí.
    rerank_activo: bool = False

    # --- Verificación (HU-B17) ---
    # Valor del SMMLV en pesos para convertir lo que el pliego exige en SMMLV (Q-12). Sin
    # valor, los ítems que lo necesitan pasan a revisión.
    smmlv_cop: int | None = Field(default=None, gt=0)
    # Ítems que se envían a Claude a la vez en la verificación.
    verificacion_concurrencia: int = Field(default=3, gt=0)

    # --- Validación de citas y revisión (HU-B18, HU-B20) ---
    # Similitud mínima (0 a 100) entre una cita y su fuente para darla por verificada.
    # Valor provisional (Q-14).
    citas_umbral_similitud: float = Field(default=90, ge=0, le=100)
    # Confianza mínima de una evaluación automática (Q-14, valor de la demo).
    revision_umbral_confianza: float = Field(default=0.70, ge=0, le=1)

    # --- Pipeline ---
    pipeline_checkpointer_enabled: bool = True
    # Tiempo máximo de un trabajo antes de darlo por fallido (S-09). Valor por confirmar.
    pipeline_timeout_segundos: int = Field(default=3600, gt=0)
    # Entregas del mismo mensaje que se toleran antes de abandonar el trabajo (HU-B09).
    pipeline_max_intentos: int = Field(default=3, gt=0)

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
        "anthropic_model_ocr",
        "nvidia_model_filtro",
        "nvidia_model_embeddings",
        "nvidia_model_chat",
        "nvidia_model_rerank",
        "nvidia_rerank_url",
        "smmlv_cop",
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
