"""Release identity of a running TenderMate service build.

Amvera passes neither build args nor build-time env vars, so each service's release workflow
writes ``src/build_info.json`` into the tree it syncs to the Amvera mirror. Locally and in tests
the file is absent and the version reads as ``dev``. Each service calls :func:`load_build_info`
once with its own name and file path (``core/build_info.py``).
"""

from __future__ import annotations

import json
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path

from pydantic import BaseModel


class BuildInfo(BaseModel):
    service: str
    version: str = "dev"
    git_sha: str = "unknown"
    built_at: str | None = None
    # Installed tendermate-shared release — shows which contract version this build runs on.
    shared_version: str = "unknown"

    @property
    def release(self) -> str:
        """Sentry release id, ``<service>@<version>``."""
        return f"{self.service}@{self.version}"


def shared_version() -> str:
    try:
        return package_version("tendermate-shared")
    except PackageNotFoundError:
        return "unknown"


def load_build_info(service: str, path: Path) -> BuildInfo:
    """Read *path* (``build_info.json``) if it exists; the service name is never taken from it."""
    data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    return BuildInfo.model_validate(
        {**data, "service": service, "shared_version": shared_version()}
    )
