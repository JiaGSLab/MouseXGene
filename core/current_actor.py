"""Per-request current user for stamping created_by / updated_by on saves."""

from __future__ import annotations

import threading
from typing import Any

_local = threading.local()


def set_current_actor(user: Any | None) -> None:
    _local.user = user


def get_current_actor() -> Any | None:
    return getattr(_local, "user", None)


def clear_current_actor() -> None:
    if hasattr(_local, "user"):
        delattr(_local, "user")
