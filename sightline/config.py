"""Environment configuration. See docs/SIGHTLINE_BUILD_SPEC_V2.md §2."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


class MissingConfig(Exception):
    pass


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise MissingConfig(f"{name} is not set")
    return value


@dataclass(frozen=True)
class Settings:
    supabase_url: str
    supabase_service_key: str
    theirstack_api_key: str
    theirstack_webhook_secret: str | None
    theirstack_webhook_url: str | None
    anthropic_api_key: str
    dashboard_username: str
    dashboard_password: str


@lru_cache
def get_settings() -> Settings:
    return Settings(
        supabase_url=_required("SUPABASE_URL").rstrip("/"),
        supabase_service_key=_required("SUPABASE_SERVICE_KEY"),
        theirstack_api_key=_required("THEIRSTACK_API_KEY"),
        theirstack_webhook_secret=os.environ.get("THEIRSTACK_WEBHOOK_SECRET"),
        theirstack_webhook_url=os.environ.get("THEIRSTACK_WEBHOOK_URL"),
        anthropic_api_key=_required("ANTHROPIC_API_KEY"),
        dashboard_username=os.environ.get("DASHBOARD_USERNAME", "james"),
        dashboard_password=_required("DASHBOARD_PASSWORD"),
    )
