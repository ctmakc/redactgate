"""Central configuration. All runtime behaviour is driven from environment / .env.

This module is part of the *contract spine*: other modules import ``settings`` and
``get_settings()`` and must not redefine configuration locally.
"""

from __future__ import annotations

import base64
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ProviderName = Literal[
    "anthropic", "openai", "gemini", "azure", "bedrock", "do-genai", "ollama"
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # ── Core ──────────────────────────────────────────────────────────────
    app_name: str = "RedactGate"
    environment: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"

    # ── Datastores ────────────────────────────────────────────────────────
    database_url: str = Field(
        default="postgresql+asyncpg://redactgate:redactgate@localhost:5432/redactgate",
        description="SQLAlchemy async DSN (asyncpg driver).",
    )
    redis_url: str = "redis://localhost:6379/0"

    # ── AI gateway ────────────────────────────────────────────────────────
    ai_provider: ProviderName = "ollama"
    # Per-provider credentials / endpoints. Empty string = unconfigured.
    anthropic_api_key: str = ""
    anthropic_base_url: str = "https://api.anthropic.com"
    anthropic_default_model: str = "claude-sonnet-4-6"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_default_model: str = "gpt-4o-mini"
    gemini_api_key: str = ""
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    gemini_default_model: str = "gemini-2.5-flash"
    azure_openai_api_key: str = ""
    azure_openai_endpoint: str = ""
    azure_openai_deployment: str = ""
    azure_openai_api_version: str = "2024-06-01"
    bedrock_region: str = "us-east-1"
    bedrock_default_model: str = "anthropic.claude-3-5-sonnet-20240620-v1:0"
    do_genai_api_key: str = ""
    do_genai_base_url: str = "https://inference.do-ai.run/v1"
    do_genai_default_model: str = "llama3.3-70b-instruct"
    ollama_base_url: str = "http://localhost:11434"
    ollama_default_model: str = "llama3.1"

    upstream_timeout_seconds: float = 120.0

    # ── Detection ─────────────────────────────────────────────────────────
    enable_presidio: bool = False  # heavy; opt-in via the `ner` extra
    enable_llm_ner: bool = False  # uses the configured provider for an NER pass
    presidio_languages: str = "en"  # comma list e.g. "en,uk"
    default_pack_codes: str = "GENERIC,CA,US,EU,UA"  # comma list of jurisdiction packs

    # ── Vault / crypto ────────────────────────────────────────────────────
    # 32-byte master key, base64. A per-session DEK is derived and AES-GCM-wraps values.
    vault_master_key: str = Field(
        default="",
        description="base64-encoded 32-byte master key. Generated if empty in dev/test.",
    )
    # HMAC key for value fingerprints (referential consistency) and audit hash chain.
    fingerprint_hmac_key: str = Field(default="", description="base64 32-byte HMAC key.")
    audit_hmac_key: str = Field(default="", description="base64 32-byte HMAC key.")
    session_ttl_hours: int = 24

    # ── Auth ──────────────────────────────────────────────────────────────
    admin_token: str = ""  # gates the admin API/UI when set
    require_api_key: bool = True  # if False, a default dev team/policy is used

    # ── Hardening ─────────────────────────────────────────────────────────
    max_body_bytes: int = 2_000_000  # reject request bodies larger than this (413)
    cors_origins: str = "http://localhost:3088,http://localhost:3000"  # comma list; "*" allowed

    # ── Derived helpers ───────────────────────────────────────────────────
    @field_validator("vault_master_key", "fingerprint_hmac_key", "audit_hmac_key")
    @classmethod
    def _validate_b64_key(cls, v: str) -> str:
        if not v:
            return v
        try:
            raw = base64.b64decode(v)
        except Exception as exc:  # noqa: BLE001
            raise ValueError("key must be valid base64") from exc
        if len(raw) != 32:
            raise ValueError("key must decode to exactly 32 bytes")
        return v

    def key_bytes(self, field: str) -> bytes:
        """Return the 32 raw bytes for a configured base64 key field.

        In dev/test, if a key is unset we derive a *deterministic* placeholder from the
        field name so the app boots — NEVER acceptable in prod (validated at startup).
        """
        value: str = getattr(self, field)
        if value:
            return base64.b64decode(value)
        if self.environment == "prod":
            raise RuntimeError(f"{field} must be set in production")
        # deterministic dev fallback — 32 bytes, clearly non-secret
        seed = (field + "-redactgate-dev").encode()
        return (seed * 32)[:32]

    def runtime_problems(self) -> list[str]:
        """Fatal misconfigurations for the current environment (fail-closed in prod).

        Guards against shipping the deterministic dev-fallback keys (which would make the
        whole vault decryptable by anyone) or leaving the admin API / proxy unauthenticated.
        """
        problems: list[str] = []
        if self.environment == "prod":
            for f in ("vault_master_key", "fingerprint_hmac_key", "audit_hmac_key"):
                if not getattr(self, f):
                    problems.append(
                        f"{f} must be set in production (refusing the insecure dev fallback)"
                    )
            if not self.admin_token:
                problems.append(
                    "admin_token must be set in production (the admin API is otherwise unguarded)"
                )
            if not self.require_api_key:
                problems.append("require_api_key must be true in production")
        return problems

    @property
    def pack_codes(self) -> list[str]:
        return [c.strip().upper() for c in self.default_pack_codes.split(",") if c.strip()]

    @property
    def presidio_language_list(self) -> list[str]:
        return [c.strip() for c in self.presidio_languages.split(",") if c.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [c.strip() for c in self.cors_origins.split(",") if c.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
