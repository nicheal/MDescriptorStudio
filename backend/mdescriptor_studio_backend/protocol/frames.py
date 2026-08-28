"""NDJSON protocol frames (docs/plan/02-IPC_PROTOCOL.md)."""

from __future__ import annotations

import json

from ..config import PROTOCOL_VERSION
from ..errors import INVALID_PARAMS, AppError

MAX_LINE_BYTES = 8 * 1024 * 1024


def encode(frame: dict) -> str:
    return json.dumps(frame, ensure_ascii=False, separators=(",", ":"), default=str)


def parse_request(line: str) -> tuple[int | None, str, dict]:
    if len(line.encode("utf-8", "replace")) > MAX_LINE_BYTES:
        raise AppError(INVALID_PARAMS, "frame exceeds 8 MB limit")
    try:
        obj = json.loads(line)
    except json.JSONDecodeError as exc:
        raise AppError(INVALID_PARAMS, f"malformed JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise AppError(INVALID_PARAMS, "frame must be a JSON object")
    version = obj.get("protocol_version")
    if version != PROTOCOL_VERSION:
        raise AppError(
            "PROTOCOL_VERSION_MISMATCH",
            f"protocol_version {version!r} unsupported (want {PROTOCOL_VERSION})",
        )
    vid = obj.get("id")
    method = obj.get("method")
    params = obj.get("params", {})
    if not isinstance(method, str) or not method:
        raise AppError(INVALID_PARAMS, "missing string field 'method'")
    if not isinstance(params, dict):
        raise AppError(INVALID_PARAMS, "'params' must be an object")
    return vid, method, params


def response_ok(request_id: int | None, result) -> dict:
    return {"protocol_version": PROTOCOL_VERSION, "id": request_id, "result": result}


def response_err(request_id: int | None, err: AppError) -> dict:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "id": request_id,
        "error": {"code": err.code, "message": err.message, "details": err.details},
    }


def event_frame(name: str, data: dict) -> dict:
    return {"protocol_version": PROTOCOL_VERSION, "event": name, "data": data}
