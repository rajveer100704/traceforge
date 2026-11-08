"""System capability report shared by `traceforge doctor` and the capabilities endpoint."""

from __future__ import annotations

import platform as py_platform
import sys
from typing import TYPE_CHECKING, Any

from traceforge import __version__
from traceforge.config import Settings
from traceforge.modules.training.capabilities import detect

if TYPE_CHECKING:
    from traceforge.application.container import Platform


def docker_status(settings: Settings) -> dict[str, Any]:
    from traceforge.infrastructure.sandbox.docker_provider import DockerSandboxProvider

    provider = DockerSandboxProvider(
        image=settings.sandbox_image, base_dir=settings.data_dir / "sandboxes"
    )
    ok, why = provider.available()
    return {"available": ok, "reason": why, "image": settings.sandbox_image}


def capabilities(settings: Settings, platform: Platform | None = None) -> dict[str, Any]:
    training = detect(str(settings.data_dir.resolve().anchor or "."))
    out: dict[str, Any] = {
        "traceforge_version": __version__,
        "python": sys.version.split()[0],
        "os": f"{py_platform.system()} {py_platform.release()}",
        "env": settings.env,
        "storage_backend": settings.storage_backend,
        "queue_backend": settings.queue_backend,
        "database": settings.resolved_database_url.split(":", 1)[0],
        "auth": "api_key" if settings.api_key else "disabled (local only)",
        "sandboxes": {
            "local": {
                "available": True,
                "isolation": "process (not a security boundary)",
                "trusted_code_only": not settings.allow_untrusted_local_execution,
            },
            "docker": docker_status(settings),
        },
        "providers": {
            "demo": {"available": True},
            "openai_compatible": {
                "available": True,
                "note": "requires base_url (+ API key env var)",
            },
        },
        "training": training.as_dict(),
    }
    if platform is not None:
        out["database_migrations_current"] = platform.db.is_up_to_date()
        out["registered_tasks"] = len(platform.registry)
        out["active_sandbox"] = platform.sandbox.name
    return out
