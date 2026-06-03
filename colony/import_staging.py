"""Session-backed staging for import overwrite confirmation."""

from __future__ import annotations

import base64
from io import BytesIO
from typing import Any

SESSION_KEY_CAGE = "mxg_pending_cage_import"
SESSION_KEY_MOUSE = "mxg_pending_mouse_import"
MAX_STAGED_BYTES = 5 * 1024 * 1024


class ImportStagingError(Exception):
    pass


def _encode_file(content: bytes) -> str:
    if len(content) > MAX_STAGED_BYTES:
        raise ImportStagingError(
            f"File is too large to stage for confirmation ({len(content)} bytes). "
            "Try splitting the import or contact an admin."
        )
    return base64.b64encode(content).decode("ascii")


def decode_staged_file(content_b64: str) -> bytes:
    return base64.b64decode(content_b64.encode("ascii"))


def file_bytes_to_upload(content: bytes, filename: str):
    handle = BytesIO(content)
    handle.name = filename or "upload.csv"
    return handle


def stage_cage_import(
    request,
    *,
    filename: str,
    content: bytes,
    id_prefix: str | None,
    update_existing: bool,
) -> None:
    request.session[SESSION_KEY_CAGE] = {
        "filename": filename,
        "content_b64": _encode_file(content),
        "id_prefix": id_prefix,
        "update_existing": update_existing,
    }
    request.session.modified = True


def pop_staged_cage_import(request) -> dict[str, Any] | None:
    data = request.session.pop(SESSION_KEY_CAGE, None)
    request.session.modified = True
    return data


def clear_staged_cage_import(request) -> None:
    if SESSION_KEY_CAGE in request.session:
        request.session.pop(SESSION_KEY_CAGE, None)
        request.session.modified = True


def stage_mouse_import(
    request,
    *,
    filename: str,
    content: bytes,
    id_prefix: str | None,
    update_existing: bool,
    import_options: dict[str, bool],
) -> None:
    request.session[SESSION_KEY_MOUSE] = {
        "filename": filename,
        "content_b64": _encode_file(content),
        "id_prefix": id_prefix,
        "update_existing": update_existing,
        "import_options": import_options,
    }
    request.session.modified = True


def pop_staged_mouse_import(request) -> dict[str, Any] | None:
    data = request.session.pop(SESSION_KEY_MOUSE, None)
    request.session.modified = True
    return data


def clear_staged_mouse_import(request) -> None:
    if SESSION_KEY_MOUSE in request.session:
        request.session.pop(SESSION_KEY_MOUSE, None)
        request.session.modified = True
