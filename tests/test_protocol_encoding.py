"""Protocol frame boundaries: what may leave the backend, and what may come in.

Three rules that each failed silently or falsely before: a non-finite value has
to be *refused* rather than stringified (numpy float32 is not a Python float, so
``allow_nan=False`` alone never saw it); an unencodable or oversized result must
answer with an error frame for the same request id instead of silence; and the
frame size cap must agree between the reader and the parser, or a legal 8 MiB
request is rejected by one input loop and accepted by the other.
"""

from __future__ import annotations

import concurrent.futures
import io
import json
from pathlib import Path

import numpy as np
import pytest

from mdescriptor_studio_backend.protocol import frames
import mdescriptor_studio_backend.protocol.server as server_module
from mdescriptor_studio_backend.protocol.server import Server


def test_numpy_values_cannot_sneak_out_as_strings():
    # default=str used to turn these into "inf", "[1. 2.]", "{1, 2}": past the
    # NaN guard, and a string where the renderer expects a number.
    for value in (np.float32("inf"), np.float32("nan"), np.array([1.0, 2.0]), {1, 2}, Path("x")):
        with pytest.raises((ValueError, TypeError)):
            frames.encode(frames.response_ok(1, {"v": value}))
    with pytest.raises(ValueError):
        frames.encode(frames.response_ok(1, {"v": float("nan")}))
    # np.float64 is a Python float subclass, so the real guard still applies to it.
    encoded = json.loads(frames.encode(frames.response_ok(1, {"v": np.float64(1.5)})))
    assert encoded["result"]["v"] == 1.5


class _InlineExecutor:
    """Runs submitted work immediately.

    serve_forever/_serve_blocking end in close(), which shuts the pools down
    with cancel_futures=True — correct for a real EOF, but it would also drop
    the very frames these tests are asserting on.
    """

    def submit(self, fn, *args, **kwargs):
        future: concurrent.futures.Future = concurrent.futures.Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except BaseException as error:  # noqa: BLE001 - mirrored onto the future
            future.set_exception(error)
        return future

    def shutdown(self, *_args, **_kwargs):
        return None


def _server(handler) -> tuple[Server, list[dict]]:
    server = Server(methods={"echo": handler})
    written: list[dict] = []

    def capture(frame: dict) -> None:
        # Run the real encoder and record what the renderer would actually
        # receive: patching _write itself would skip the guard under test.
        written.append(json.loads(server._encode(frame)))

    server._write = capture  # type: ignore[assignment]
    server._pool = _InlineExecutor()  # type: ignore[assignment]
    server._control_pool = _InlineExecutor()  # type: ignore[assignment]
    return server, written


def _stream(*lines: bytes) -> io.BytesIO:
    return io.BytesIO(b"".join(lines))


def test_unencodable_result_answers_with_an_error_frame_for_the_same_id():
    server, written = _server(lambda params: {"v": np.float32("inf")})
    server._serve_blocking(_stream(_line(id=41, method="echo")))

    assert len(written) == 1
    reply = written[0]
    assert reply["id"] == 41
    assert reply["error"]["code"] == "INTERNAL_ERROR"


def test_oversized_result_answers_instead_of_vanishing():
    server, written = _server(lambda params: {"blob": "x" * (frames.MAX_LINE_BYTES + 10)})
    server._serve_blocking(_stream(_line(id=42, method="echo")))

    assert [frame["id"] for frame in written] == [42]
    assert written[0]["error"]["code"] == "INTERNAL_ERROR"


def _line(id: int, method: str, pad: int = 0) -> bytes:
    return (
        json.dumps(
            {
                "protocol_version": frames.PROTOCOL_VERSION,
                "id": id,
                "method": method,
                "params": {"pad": "p" * pad},
            }
        ).encode("utf-8")
        + b"\n"
    )


def _request_of_exact_length(size: int, id: int = 7) -> bytes:
    body = _line(id=id, method="echo")[:-1]
    padded = len(body)
    assert size > padded
    return _line(id=id, method="echo", pad=size - padded)


def test_frame_at_the_size_limit_is_accepted_and_the_stream_stays_aligned():
    seen: list[int] = []

    def handler(params):
        seen.append(len(params.get("pad", "")))
        return {"ok": True}

    server, written = _server(handler)
    at_limit = _request_of_exact_length(frames.MAX_LINE_BYTES, id=7)
    over_limit = _request_of_exact_length(frames.MAX_LINE_BYTES + 1, id=8)
    assert len(at_limit) == frames.MAX_LINE_BYTES + 1  # content at the cap + newline
    assert len(over_limit) == frames.MAX_LINE_BYTES + 2

    server._serve_blocking(_stream(at_limit, over_limit, _line(id=9, method="echo")))

    # The exactly-8 MiB frame is processed (readline reserves room for its
    # newline), the 8 MiB + 1 frame is refused with its own error, and the
    # stream stays frame-aligned so the frame after it still answers.
    assert [frame.get("id") for frame in written] == [7, None, 9]
    assert written[0]["result"] == {"ok": True}
    assert written[1]["error"]["code"] == "INVALID_PARAMS"
    assert written[2]["result"] == {"ok": True}
    assert len(seen) == 2


def test_both_input_loops_share_one_size_rule():
    # The polled loop (Windows pipe stdin) bounds a line by the accumulated
    # buffer, the blocking loop by the readline result. _consume_frame is the
    # rule they must agree on: content longer than the cap is refused, content
    # exactly at the cap is dispatched.
    server, written = _server(lambda params: {"ok": True})
    at_limit = _request_of_exact_length(frames.MAX_LINE_BYTES, id=7)
    over_limit = _request_of_exact_length(frames.MAX_LINE_BYTES + 1, id=8)
    assert server._consume_frame(at_limit) is True
    assert server._consume_frame(over_limit) is True
    assert [frame.get("id") for frame in written] == [7, None]
    assert written[0]["result"] == {"ok": True}
    assert written[1]["error"]["code"] == "INVALID_PARAMS"


def test_polled_loop_discards_the_rest_of_an_oversized_line(monkeypatch):
    server, written = _server(lambda params: {"ok": True})
    chunks = iter(
        [
            b"x" * (frames.MAX_LINE_BYTES + 1),
            b"tail-that-is-still-the-same-frame\n" + _line(id=9, method="echo"),
            b"",
        ]
    )
    monkeypatch.setattr(server_module.os, "read", lambda _fd, _size: next(chunks))

    assert server._serve_polled(lambda: True) == "eof"
    assert [frame.get("id") for frame in written] == [None, 9]
    assert written[0]["error"]["code"] == "INVALID_PARAMS"
    assert written[1]["result"] == {"ok": True}
