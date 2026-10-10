import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    access_token: str
    openai_api_key: str
    summary_model: str = "gpt-4.1-mini"
    max_audio_bytes: int = 24_000_000
    max_reference_bytes: int = 1_000_000
    max_request_bytes: int = 28_100_000
    max_duration_seconds: int = 3600
    max_summary_request_bytes: int = 2_000_000
    normalization_timeout_seconds: float = 180.0
    provider_timeout_seconds: float = 600.0

    @classmethod
    def from_environment(cls):
        # Only variable names are included in failures: never print credential values.
        access_token = os.environ.get("MAJLIS_ACCESS_TOKEN", "").strip()
        provider_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not access_token:
            raise RuntimeError("MAJLIS_ACCESS_TOKEN must be configured before server startup")
        if len(access_token) < 24:
            raise RuntimeError("MAJLIS_ACCESS_TOKEN must contain at least 24 characters")
        if not provider_key:
            raise RuntimeError("OPENAI_API_KEY must be configured before server startup")
        model = os.environ.get("MAJLIS_SUMMARY_MODEL", "gpt-4.1-mini").strip()
        if not model:
            raise RuntimeError("MAJLIS_SUMMARY_MODEL cannot be empty")
        return cls(access_token=access_token, openai_api_key=provider_key, summary_model=model)
