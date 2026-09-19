"""NDJSON protocol frames (docs/plan/02-IPC_PROTOCOL.md)."""

from __future__ import annotations

import json

from ..config import PROTOCOL_VERSION
from ..errors import INVALID_PARAMS, AppError

MAX_LINE_BYTES = 8 * 1024 * 1024
MAX_REQUEST_ID = (1 << 53) - 1


def encode(frame: dict) -> str:
    # allow_nan=False is a protocol requirement, not a style preference: the
    # default emits bare NaN/Infinity tokens, which the Rust bridge forwards
    # verbatim but JSON.parse in the renderer rejects. The frame is dropped
    # there, so the awaiting request would never be answered and never fail.
    return json.dumps(
        frame, ensure_ascii=False, separators=(",", ":"), default=str, allow_nan=False
    )


def parse_request(line: str) -> tuple[int | None, str, dict]:
    if len(line.encode("utf-8", "replace")) > MAX_LINE_BYTES:
        raise AppError(INVALID_PARAMS, "frame exceeds 8 MB limit")
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, RecursionError, UnicodeError) as exc:
        raise AppError(INVALID_PARAMS, f"malformed JSON: {exc}", public_message="Malformed request.") from exc
    if not isinstance(obj, dict):
        raise AppError(INVALID_PARAMS, "frame must be a JSON object")
    version = obj.get("protocol_version")
    if version != PROTOCOL_VERSION:
        raise AppError(
            "PROTOCOL_VERSION_MISMATCH",
            f"protocol_version {version!r} unsupported (want {PROTOCOL_VERSION})",
        )
    vid = obj.get("id")
    if vid is not None and (
        isinstance(vid, bool)
        or not isinstance(vid, int)
        or vid < 0
        or vid > MAX_REQUEST_ID
    ):
        raise AppError(INVALID_PARAMS, "request id is outside the supported range")
    method = obj.get("method")
    params = obj.get("params", {})
    if not isinstance(method, str) or not method:
        raise AppError(INVALID_PARAMS, "missing string field 'method'")
    if len(method) > 256 or any(ord(ch) < 0x20 for ch in method):
        raise AppError(INVALID_PARAMS, "method name is invalid")
    if not isinstance(params, dict):
        raise AppError(INVALID_PARAMS, "'params' must be an object")
    return vid, method, params


def response_ok(request_id: int | None, result) -> dict:
    return {"protocol_version": PROTOCOL_VERSION, "id": request_id, "result": result}


def response_err(request_id: int | None, err: AppError) -> dict:
    # Deliberately not err.details: the developer diagnosis can quote a path,
    # and the renderer is not a trusted recipient. Server._handle logs it
    # against err.error_id, which is the reference the user is shown.
    return {
        "protocol_version": PROTOCOL_VERSION,
        "id": request_id,
        "error": {"code": err.code, "message": err.public_message, "error_id": err.error_id},
    }


def event_frame(name: str, data: dict) -> dict:
    return {"protocol_version": PROTOCOL_VERSION, "event": name, "data": data}
